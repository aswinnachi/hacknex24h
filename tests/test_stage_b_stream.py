import os
import sys
import time

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from backend.app.core.stream_manager import (
    CameraStream,
    StreamManager,
    get_stream_manager,
    sanitize_stream_url
)

def test_credential_sanitization():
    # Passwords must be completely masked
    rtsp_url = "rtsp://admin:superSecret123@192.168.1.50:554/live/ch0"
    sanitized = sanitize_stream_url(rtsp_url)
    assert "superSecret123" not in sanitized
    assert "admin:***@192.168.1.50:554/live/ch0" in sanitized

    # Webcam numeric string
    webcam_url = "0"
    assert sanitize_stream_url(webcam_url) == "webcam_0"

    # Plain URL without creds
    plain = "http://example.com/stream.m3u8"
    assert sanitize_stream_url(plain) == plain

def test_stream_manager_accessible_stream():
    """
    Tests connecting an accessible test video stream (test_cctv.mp4).
    """
    test_video_path = os.path.abspath("test_cctv.mp4")
    assert os.path.exists(test_video_path), f"Test video missing: {test_video_path}"

    mgr = get_stream_manager()
    cam_id = "test_cam_01"
    name = "Test CCTV Feed"

    # 1. Connect camera
    connected, status = mgr.connect_camera(
        camera_id=cam_id,
        name=name,
        stream_url=test_video_path,
        timeout=5.0,
        loop_file_stream=True
    )
    assert connected is True, f"Failed to connect camera stream: {status.get('error_message')}"
    assert status["is_connected"] is True
    assert status["status"] == "connected"
    assert status["camera_id"] == cam_id
    assert status["name"] == name
    assert status["width"] > 0
    assert status["height"] > 0

    stream = mgr.get_stream(cam_id)
    assert stream is not None

    # 2. Wait and verify frame updates
    time.sleep(1.0)
    frame = stream.get_latest_frame()
    assert frame is not None
    assert frame.shape[0] == status["height"]
    assert frame.shape[1] == status["width"]
    assert stream.frames_read > 5

    # 3. Test MJPEG frame generation
    jpeg_bytes = stream.get_latest_jpeg()
    assert jpeg_bytes is not None
    assert len(jpeg_bytes) > 500  # valid JPEG payload

    mjpeg_gen = stream.generate_mjpeg_stream(fps_limit=10.0)
    chunk = next(mjpeg_gen)
    assert b"--frame" in chunk
    assert b"Content-Type: image/jpeg" in chunk

    # 4. Test duplicate connect protection
    connected2, status2 = mgr.connect_camera(
        camera_id=cam_id,
        name=name,
        stream_url=test_video_path,
        timeout=2.0
    )
    assert connected2 is True
    assert status2["is_connected"] is True

    # 5. Test disconnection
    disconnected = mgr.disconnect_camera(cam_id)
    assert disconnected is True
    assert mgr.get_stream(cam_id) is None
    assert stream.is_connected is False
    assert stream.status == "disconnected"

def test_stream_manager_invalid_stream_handling():
    """
    Tests that invalid/unreachable stream URLs do not crash the application,
    and report status = 'error'.
    """
    mgr = get_stream_manager()
    cam_id = "invalid_cam_test"
    invalid_url = "rtsp://invalid_user:invalid_pass@127.0.0.1:9999/nonexistent"

    connected, status = mgr.connect_camera(
        camera_id=cam_id,
        name="Invalid Stream",
        stream_url=invalid_url,
        timeout=2.0
    )
    # Must report failure truthfully
    assert connected is False
    assert status["is_connected"] is False
    assert status["status"] == "error"
    # Credentials must be masked in status
    assert "invalid_pass" not in str(status)

    mgr.disconnect_camera(cam_id)

if __name__ == "__main__":
    test_credential_sanitization()
    print("✓ test_credential_sanitization passed")
    test_stream_manager_accessible_stream()
    print("✓ test_stream_manager_accessible_stream passed")
    test_stream_manager_invalid_stream_handling()
    print("✓ test_stream_manager_invalid_stream_handling passed")
    print("\nALL STAGE B TESTS PASSED SUCCESSFULLY!")
