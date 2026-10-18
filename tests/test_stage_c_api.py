import os
import sys
import json
import urllib.request
import urllib.error
import time

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from backend.app.api.cameras import (
    connect_camera_stream,
    get_camera_status,
    get_camera_live_stream,
    disconnect_camera_stream,
    CameraConnectRequest
)
from backend.app.core.stream_manager import get_stream_manager
from backend.app.db.database import execute_query
from fastapi import HTTPException

def test_direct_stage_c_endpoints():
    print("Testing Stage C endpoints directly...")
    test_video_path = os.path.abspath("test_cctv.mp4")
    assert os.path.exists(test_video_path), f"test_cctv.mp4 not found at {test_video_path}"

    test_cam_id = "test_live_api_cam"

    # Ensure cleanup
    disconnect_camera_stream(test_cam_id)
    execute_query("DELETE FROM cameras WHERE id = ?", (test_cam_id,))

    # 1. Test connect_camera_stream
    req = CameraConnectRequest(
        camera_id=test_cam_id,
        name="Live Stage C Test Camera",
        stream_url=test_video_path,
        timeout=6.0
    )
    res = connect_camera_stream(req)
    assert res["success"] is True
    assert res["status"] == "connected"
    assert res["camera_id"] == test_cam_id
    assert res["source_type"] == "live"
    assert res["fps"] > 0
    assert res["width"] > 0
    assert res["height"] > 0

    # Verify DB state
    row = execute_query("SELECT * FROM cameras WHERE id = ?", (test_cam_id,), fetch=True, fetch_one=True)
    assert row is not None
    assert row["source_type"] == "live"
    assert row["status"] == "CONNECTED"

    # 2. Test get_camera_status
    st = get_camera_status(test_cam_id)
    assert st["camera_id"] == test_cam_id
    assert st["is_connected"] is True
    assert st["status"] == "connected"
    assert st["frames_read"] > 0

    # 3. Test get_camera_live_stream
    live_resp = get_camera_live_stream(test_cam_id)
    assert live_resp.media_type == "multipart/x-mixed-replace; boundary=frame"
    stream = get_stream_manager().get_stream(test_cam_id)
    assert stream is not None
    chunk = next(stream.generate_mjpeg_stream(fps_limit=10.0))
    assert b"--frame" in chunk
    assert b"Content-Type: image/jpeg" in chunk

    # 4. Test duplicate connection handling
    dup_res = connect_camera_stream(req)
    assert dup_res["success"] is True
    assert dup_res["status"] == "connected"
    assert "already connected" in dup_res.get("message", "").lower()

    # 5. Test invalid source handling
    bad_req = CameraConnectRequest(
        camera_id="invalid_source_cam",
        name="Invalid RTSP Camera",
        stream_url="rtsp://admin:superSecretPass@127.0.0.1:9999/bad",
        timeout=2.0
    )
    caught_err = False
    try:
        connect_camera_stream(bad_req)
    except HTTPException as e:
        caught_err = True
        assert e.status_code == 400
        # Password must NEVER appear in error detail
        assert "superSecretPass" not in str(e.detail)
    assert caught_err is True, "Expected HTTPException for unreachable camera stream"

    # 6. Test disconnect_camera_stream
    disc_res = disconnect_camera_stream(test_cam_id)
    assert disc_res["success"] is True
    assert disc_res["status"] == "disconnected"

    st_after = get_camera_status(test_cam_id)
    assert st_after["is_connected"] is False
    assert st_after["status"] == "disconnected"

    # Cleanup
    execute_query("DELETE FROM cameras WHERE id = ?", (test_cam_id,))
    print("✓ Direct endpoint tests passed!")

def test_http_stage_c_endpoints_if_server_up():
    base_url = "http://127.0.0.1:8000"
    try:
        req = urllib.request.Request(f"{base_url}/api/health")
        with urllib.request.urlopen(req, timeout=1.0) as resp:
            if resp.status != 200:
                print("Server not responding with 200 on /api/health; skipping live HTTP test.")
                return
    except Exception as e:
        print(f"Dev server not running on port 8000 ({e}); skipping live HTTP test.")
        return

    print("Dev server is running! Testing Stage C via HTTP requests...")
    test_video_path = os.path.abspath("test_cctv.mp4")
    cam_id = "http_test_live_cam"

    # Disconnect any old
    try:
        req = urllib.request.Request(f"{base_url}/api/cameras/{cam_id}/disconnect", data=b"{}", headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=2.0)
    except Exception:
        pass

    # 1. Connect
    body = json.dumps({
        "camera_id": cam_id,
        "name": "HTTP Live Camera",
        "stream_url": test_video_path,
        "timeout": 5.0
    }).encode("utf-8")
    req = urllib.request.Request(f"{base_url}/api/cameras/connect", data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=8.0) as resp:
        assert resp.status == 200
        res_data = json.loads(resp.read().decode("utf-8"))
        assert res_data["status"] == "connected"
        assert res_data["camera_id"] == cam_id

    # 2. Status
    req = urllib.request.Request(f"{base_url}/api/cameras/{cam_id}/status")
    with urllib.request.urlopen(req, timeout=2.0) as resp:
        assert resp.status == 200
        st_data = json.loads(resp.read().decode("utf-8"))
        assert st_data["is_connected"] is True

    # 3. Live stream chunk
    req = urllib.request.Request(f"{base_url}/api/cameras/{cam_id}/live")
    resp = urllib.request.urlopen(req, timeout=4.0)
    assert resp.status == 200
    content_type = resp.headers.get("Content-Type", "")
    assert "multipart/x-mixed-replace" in content_type
    initial_chunk = resp.read(512)
    assert b"--frame" in initial_chunk
    resp.close()

    time.sleep(0.5)

    # 4. Disconnect
    req = urllib.request.Request(f"{base_url}/api/cameras/{cam_id}/disconnect", data=b"{}", headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=6.0) as resp:
        assert resp.status == 200

    print("✓ Live HTTP tests passed!")

if __name__ == "__main__":
    test_direct_stage_c_endpoints()
    test_http_stage_c_endpoints_if_server_up()
    print("\nALL STAGE C BACKEND TESTS PASSED!")
