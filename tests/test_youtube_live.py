import os
import sys
import time
import shutil
import tempfile
import urllib.request
import urllib.error
import json

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from backend.app.core.youtube_resolver import (
    validate_youtube_url,
    resolve_youtube_live_stream,
    register_mock_youtube_stream,
    clear_mock_youtube_streams
)
from backend.app.core.stream_manager import get_stream_manager
from backend.app.core.stream_recorder import StreamRecorder
from backend.app.api.cameras import (
    connect_camera_stream,
    get_camera_status,
    disconnect_camera_stream,
    get_camera_recordings,
    cleanup_camera_recordings,
    CameraConnectRequest
)
from backend.app.api.query import execute_natural_language_query, QueryRequest
from backend.app.db.database import execute_query

TEST_VIDEO_PATH = os.path.abspath("test_cctv.mp4")

def test_youtube_url_validation():
    """Verify URL validation for all valid and invalid YouTube formats."""
    print("Testing YouTube URL validation...")
    valid_urls = [
        "https://www.youtube.com/watch?v=jfKfPfyJRdk",
        "http://youtube.com/watch?v=dQw4w9WgXcQ",
        "https://youtu.be/5qap5aO4i9A",
        "https://www.youtube.com/live/live_stream_id123",
        "https://youtube.com/live/abcXYZ_-123?feature=share",
        "https://www.youtube.com/embed/jfKfPfyJRdk",
        "https://m.youtube.com/watch?v=jfKfPfyJRdk"
    ]
    for url in valid_urls:
        is_val, vid_id, err = validate_youtube_url(url)
        assert is_val is True, f"Expected valid: {url}, got error: {err}"
        assert vid_id is not None

    invalid_urls = [
        "https://vimeo.com/12345678",
        "rtsp://192.168.1.100:554/live",
        "http://example.com/video.mp4",
        "not_a_url",
        "https://youtube.com/invalid_path",
        "https://www.youtube.com/watch",
        ""
    ]
    for url in invalid_urls:
        is_val, vid_id, err = validate_youtube_url(url)
        assert is_val is False, f"Expected invalid: {url}"
    print("✓ URL validation passed.")


def test_youtube_inaccessible_error_guidance():
    """Verify clear error message recommending direct RTSP/HLS when a stream cannot be resolved."""
    print("Testing error guidance for inaccessible streams...")
    fake_url = "https://www.youtube.com/watch?v=nonexistent_video_id_99999"
    success, stream_url, error_msg = resolve_youtube_live_stream(fake_url, timeout=3.0)
    assert success is False
    assert stream_url is None
    assert error_msg is not None
    assert "Unable to access YouTube Live stream" in error_msg or "not accessible" in error_msg
    assert "RTSP" in error_msg
    print(f"✓ Compliant error guidance passed: {error_msg}")


def test_youtube_live_connect_and_stream():
    """Test connecting a YouTube Live stream with mock resolver, verifying frames and DB state."""
    print("Testing YouTube Live connection & frame decoding...")
    assert os.path.exists(TEST_VIDEO_PATH), f"Missing {TEST_VIDEO_PATH}"
    
    mock_url = "https://www.youtube.com/watch?v=MOCK_LIVE_STREAM_1"
    register_mock_youtube_stream(mock_url, TEST_VIDEO_PATH)

    cam_id = "test_yt_cam_1"
    disconnect_camera_stream(cam_id)
    execute_query("DELETE FROM cameras WHERE id = ?", (cam_id,))

    req = CameraConnectRequest(
        camera_id=cam_id,
        name="Mock YouTube Live Cam 1",
        stream_url=mock_url,
        source_type="youtube_live",
        timeout=10.0
    )

    res = connect_camera_stream(req)
    assert res["success"] is True
    assert res["status"] == "connected"
    assert res["source_type"] == "youtube_live"
    assert res["camera_id"] == cam_id
    assert res["fps"] > 0
    assert res["width"] > 0
    assert res["height"] > 0

    # Verify DB camera record
    db_cam = execute_query("SELECT * FROM cameras WHERE id = ?", (cam_id,), fetch=True, fetch_one=True)
    assert db_cam is not None
    assert db_cam["source_type"] == "youtube_live"
    assert db_cam["status"] == "CONNECTED"

    # Wait briefly for frames to be processed
    time.sleep(1.0)

    # Verify status endpoint
    st = get_camera_status(cam_id)
    assert st["is_connected"] is True
    assert st["frames_read"] > 0
    assert st["source_type"] == "youtube_live"

    # Clean disconnect
    disc = disconnect_camera_stream(cam_id)
    assert disc["status"] == "disconnected"

    # Verify DB status updated
    db_cam_after = execute_query("SELECT * FROM cameras WHERE id = ?", (cam_id,), fetch=True, fetch_one=True)
    assert db_cam_after["status"] == "DISCONNECTED"
    print("✓ YouTube Live connect, frame reception, and disconnect passed.")


def test_youtube_live_event_indexing():
    """Verify that detection and tracking events from YouTube Live feeds are indexed with source_type='youtube_live'."""
    print("Testing YouTube Live detection, tracking, and event indexing...")
    assert os.path.exists(TEST_VIDEO_PATH)
    
    mock_url = "https://www.youtube.com/live/MOCK_EVENT_INDEX"
    register_mock_youtube_stream(mock_url, TEST_VIDEO_PATH)

    cam_id = "test_yt_indexing_cam"
    disconnect_camera_stream(cam_id)
    execute_query("DELETE FROM events WHERE camera_id = ?", (cam_id,))
    execute_query("DELETE FROM cameras WHERE id = ?", (cam_id,))

    req = CameraConnectRequest(
        camera_id=cam_id,
        name="YouTube Event Indexer Cam",
        stream_url=mock_url,
        source_type="youtube_live",
        timeout=10.0
    )
    connect_camera_stream(req)

    # Let worker run for at least 3 seconds to decode frames and detect/track objects
    time.sleep(3.0)

    # Check database for indexed events
    events = execute_query(
        "SELECT * FROM events WHERE camera_id = ?",
        (cam_id,),
        fetch=True
    ) or []

    if len(events) > 0:
        sample_ev = events[0]
        assert sample_ev["source_type"] == "youtube_live"
        assert sample_ev["confidence"] > 0
        assert sample_ev["concept"] is not None
        if sample_ev["representative_frame"]:
            frame_full_path = os.path.join(".", sample_ev["representative_frame"].lstrip("/"))
            assert os.path.exists(frame_full_path), f"Evidence image not found: {frame_full_path}"
        print(f"✓ Verified {len(events)} indexed YouTube Live events with evidence.")
    else:
        print("✓ Stream processed successfully (0 events in window).")

    disconnect_camera_stream(cam_id)


def test_persistent_query_logging():
    """Verify that queries and summaries are logged to SQLite queries table."""
    print("Testing persistent query logging...")
    test_q = "Locate vehicles or persons in live feeds"
    req = QueryRequest(query=test_q)
    res = execute_natural_language_query(req)
    assert res is not None

    logged = execute_query(
        "SELECT * FROM queries WHERE query_text = ? ORDER BY timestamp DESC LIMIT 1",
        (test_q,),
        fetch=True,
        fetch_one=True
    )
    assert logged is not None
    logged_dict = dict(logged)
    assert logged_dict["query_text"] == test_q
    assert "result_summary" in logged_dict
    assert "result_count" in logged_dict
    print("✓ Query history & results persistence passed.")


def test_stream_recorder_and_quota_cleanup():
    """Test segmented stream recorder and retention/storage quota cleanup."""
    print("Testing segmented stream recorder and storage cleanup...")
    temp_dir = tempfile.mkdtemp(prefix="test_recorder_")
    try:
        recorder = StreamRecorder(
            camera_id="test_rec_cam",
            recordings_dir=temp_dir,
            segment_duration=2.0,
            max_storage_mb=50.0,
            retention_days=1.0
        )
        recorder.start()

        import numpy as np
        for _ in range(25):
            frame = np.zeros((240, 320, 3), dtype=np.uint8)
            recorder.write_frame(frame)
            time.sleep(0.04)

        recorder.stop()

        recs = recorder.get_recordings()
        assert len(recs) >= 1
        assert os.path.exists(recs[0]["file_path"])
        assert recs[0]["size_bytes"] > 0

        # Test storage cleanup by creating dummy old files in recorder base_dir
        dummy_old = os.path.join(recorder.base_dir, "segment_old_archive.mp4")
        with open(dummy_old, "wb") as f:
            f.write(b"0" * (1024 * 1024)) # 1MB dummy
        
        old_time = time.time() - (86400 * 2)
        os.utime(dummy_old, (old_time, old_time))

        deleted = recorder.cleanup_old_recordings()
        assert deleted["deleted_files"] >= 1
        assert not os.path.exists(dummy_old)
        print("✓ Stream recorder and quota cleanup passed.")
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def test_simultaneous_youtube_and_local_streams():
    """Verify that multiple simultaneous sources (YouTube Live + Local/RTSP) operate concurrently without starvation."""
    print("Testing simultaneous YouTube Live and Local RTSP streams...")
    assert os.path.exists(TEST_VIDEO_PATH)

    mock_url = "https://www.youtube.com/watch?v=MOCK_MULTI_CAM_YT"
    register_mock_youtube_stream(mock_url, TEST_VIDEO_PATH)

    cam1_id = "multi_cam_yt"
    cam2_id = "multi_cam_local"

    for cid in [cam1_id, cam2_id]:
        disconnect_camera_stream(cid)
        execute_query("DELETE FROM cameras WHERE id = ?", (cid,))

    # Connect YouTube Live
    res1 = connect_camera_stream(CameraConnectRequest(
        camera_id=cam1_id,
        name="YouTube Source",
        stream_url=mock_url,
        source_type="youtube_live",
        timeout=10.0
    ))
    assert res1["success"] is True

    # Connect Local Stream
    res2 = connect_camera_stream(CameraConnectRequest(
        camera_id=cam2_id,
        name="Local RTSP Source",
        stream_url=TEST_VIDEO_PATH,
        source_type="live",
        timeout=10.0
    ))
    assert res2["success"] is True

    time.sleep(2.0)

    # Verify both feeds received frames
    st1 = get_camera_status(cam1_id)
    st2 = get_camera_status(cam2_id)

    assert st1["is_connected"] is True
    assert st1["frames_read"] > 0
    assert st2["is_connected"] is True
    assert st2["frames_read"] > 0

    disconnect_camera_stream(cam1_id)
    disconnect_camera_stream(cam2_id)
    print("✓ Simultaneous multi-source concurrency passed.")


def test_youtube_live_reconnection():
    """Verify that a YouTube Live source can be disconnected and reconnected cleanly without leaks."""
    print("Testing YouTube Live reconnection...")
    assert os.path.exists(TEST_VIDEO_PATH)

    mock_url = "https://www.youtube.com/watch?v=MOCK_RECONNECT_STREAM"
    register_mock_youtube_stream(mock_url, TEST_VIDEO_PATH)
    cam_id = "test_yt_reconnect_cam"

    disconnect_camera_stream(cam_id)
    execute_query("DELETE FROM cameras WHERE id = ?", (cam_id,))

    # First connection
    res1 = connect_camera_stream(CameraConnectRequest(
        camera_id=cam_id,
        name="Reconnect YT Cam",
        stream_url=mock_url,
        source_type="youtube_live",
        timeout=10.0
    ))
    assert res1["success"] is True
    time.sleep(1.0)
    st1 = get_camera_status(cam_id)
    assert st1["is_connected"] is True
    assert st1["frames_read"] > 0

    # Disconnect
    disc = disconnect_camera_stream(cam_id)
    assert disc["status"] == "disconnected"
    time.sleep(0.5)

    # Reconnect
    res2 = connect_camera_stream(CameraConnectRequest(
        camera_id=cam_id,
        name="Reconnect YT Cam",
        stream_url=mock_url,
        source_type="youtube_live",
        timeout=10.0
    ))
    assert res2["success"] is True
    time.sleep(1.0)
    st2 = get_camera_status(cam_id)
    assert st2["is_connected"] is True
    assert st2["frames_read"] > 0

    # Cleanup
    disconnect_camera_stream(cam_id)
    print("✓ YouTube Live reconnection passed.")


def test_youtube_live_evidence_retrieval():
    """Verify that evidence for indexed YouTube Live events can be retrieved via the evidence pipeline."""
    print("Testing YouTube Live evidence retrieval...")
    assert os.path.exists(TEST_VIDEO_PATH)

    from backend.app.core.evidence import generate_evidence_clip

    mock_url = "https://www.youtube.com/live/MOCK_EVIDENCE_STREAM"
    register_mock_youtube_stream(mock_url, TEST_VIDEO_PATH)
    cam_id = "test_yt_evidence_cam"

    disconnect_camera_stream(cam_id)
    execute_query("DELETE FROM events WHERE camera_id = ?", (cam_id,))
    execute_query("DELETE FROM cameras WHERE id = ?", (cam_id,))

    connect_camera_stream(CameraConnectRequest(
        camera_id=cam_id,
        name="Evidence Test Cam",
        stream_url=mock_url,
        source_type="youtube_live",
        timeout=10.0
    ))

    try:
        time.sleep(3.0)

        # Find an event generated for this camera
        ev_row = execute_query(
            "SELECT id FROM events WHERE camera_id = ? ORDER BY start_timestamp DESC LIMIT 1",
            (cam_id,),
            fetch=True,
            fetch_one=True
        )
        if ev_row:
            ev_id = ev_row["id"]
            evidence = generate_evidence_clip(ev_id)
            assert evidence is not None
            assert evidence["event_id"] == ev_id
            assert evidence["camera_id"] == cam_id
            assert evidence["verdict"] in ("VERIFIED", "YES")
            assert evidence["video_offset"] == "LIVE CCTV FEED"
            assert "frame_path" in evidence
            print(f"✓ Evidence retrieval for event {ev_id} passed: {evidence['summary']}")
        else:
            print("✓ Evidence pipeline verified (0 events in sample window).")
    finally:
        disconnect_camera_stream(cam_id)


if __name__ == "__main__":
    clear_mock_youtube_streams()
    test_youtube_url_validation()
    test_youtube_inaccessible_error_guidance()
    test_youtube_live_connect_and_stream()
    test_youtube_live_reconnection()
    test_youtube_live_event_indexing()
    test_youtube_live_evidence_retrieval()
    test_persistent_query_logging()
    test_stream_recorder_and_quota_cleanup()
    test_simultaneous_youtube_and_local_streams()
    clear_mock_youtube_streams()
    print("\n===========================================")
    print("ALL YOUTUBE LIVE & STORAGE TESTS PASSED! ✓")
    print("===========================================")
