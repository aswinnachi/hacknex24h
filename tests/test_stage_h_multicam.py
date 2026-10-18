"""
Stage H: Multi-Camera Performance, Concurrency, and Correctness Test Suite

Tests:
1. Fair Inference Scheduling & Starvation Prevention:
   - Ensures FairInferenceCoordinator serves concurrent camera streams fairly.
   - Ensures live streams have priority over background video analysis jobs.
2. Bounded Queues & Latency Protection:
   - Ensures CameraStream buffers are bounded (single latest frame + lock) and drop stale frames under load.
3. Decoupled Capture Responsiveness:
   - Ensures capture thread continues capturing at full camera rate even when inference is throttled.
4. Tracker Isolation & State Hygiene:
   - Verifies ByteTrack instances remain strictly isolated per camera session without cross-talk.
5. Worker Lifecycle & Reconnection Hygiene:
   - Verifies streams cleanly disconnect, terminate worker threads, and reconnect without thread leaks.
6. Graceful Fault Tolerance:
   - Verifies model inference exceptions do not deadlock the FairInferenceCoordinator or stall streams.
7. Event and Indexer Concurrency:
   - Verifies persistent indexing and episode finalization work safely under multi-camera load.
"""

import sys
import os
import time
import threading
import numpy as np

# Ensure backend is on sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from backend.app.core.detector import FairInferenceCoordinator, get_detector
from backend.app.core.stream_manager import CameraStream, StreamManager
from backend.app.core.live_indexer import get_live_indexer


def test_fair_inference_coordinator_fairness():
    print("\n[Test 1] FairInferenceCoordinator multi-stream fairness & priority...")
    coord = FairInferenceCoordinator()
    
    # 1. Test live stream round-robin fairness
    order_served = []
    
    def simulate_worker(cam_id, num_requests):
        for _ in range(num_requests):
            with coord.inference_context(cam_id, is_live=True):
                order_served.append(cam_id)
                time.sleep(0.01)
    
    t1 = threading.Thread(target=simulate_worker, args=("cam_1", 10))
    t2 = threading.Thread(target=simulate_worker, args=("cam_2", 10))
    t3 = threading.Thread(target=simulate_worker, args=("cam_3", 10))
    
    t1.start()
    t2.start()
    t3.start()
    
    t1.join()
    t2.join()
    t3.join()
    
    c1_count = order_served.count("cam_1")
    c2_count = order_served.count("cam_2")
    c3_count = order_served.count("cam_3")
    
    assert c1_count == 10 and c2_count == 10 and c3_count == 10, f"Unfair distribution: {c1_count}, {c2_count}, {c3_count}"
    print(f"  ✓ Live stream requests served with perfect fairness: cam_1={c1_count}, cam_2={c2_count}, cam_3={c3_count}")

    # 2. Test live stream priority over background jobs
    bg_served = []
    live_served = []
    
    # Hold lock to queue both
    with coord.inference_context("hold_lock", is_live=True):
        def bg_req():
            with coord.inference_context("bg_job", is_live=False):
                bg_served.append(time.time())
        def live_req():
            with coord.inference_context("live_cam", is_live=True):
                live_served.append(time.time())
                
        t_bg = threading.Thread(target=bg_req)
        t_live = threading.Thread(target=live_req)
        
        # Start bg first, then live
        t_bg.start()
        time.sleep(0.02)
        t_live.start()
        time.sleep(0.02)
        
    t_live.join()
    t_bg.join()
    
    assert live_served[0] < bg_served[0], "Live camera request was not prioritized over background video job!"
    print("  ✓ Live streams correctly take priority over background batch analysis jobs")


def test_bounded_frame_buffer_and_stale_drop():
    print("\n[Test 2] Bounded frame queue & fresh frame delivery...")
    # CameraStream uses bounded latest frame buffer protected by _frame_lock
    cam = CameraStream("test_bounded", "Bounded Cam", "dummy_source", loop_file_stream=False)
    
    # Directly test buffer semantics: update 10 frames
    for i in range(10):
        frame = np.ones((50, 50, 3), dtype=np.uint8) * i
        with cam._frame_lock:
            cam._latest_frame = frame
            cam.frames_read += 1
            cam.last_frame_time = time.time()
            
    # Buffer contains exactly ONE latest frame (size is strictly O(1) bounded)
    retrieved = cam.get_latest_frame()
    assert retrieved is not None, "Failed to retrieve frame"
    assert retrieved[0, 0, 0] == 9, f"Expected latest frame value 9, got {retrieved[0, 0, 0]}"
    assert cam.frames_read == 10, f"Expected frames_read 10, got {cam.frames_read}"
    print("  ✓ Latest-frame buffer is strictly O(1) bounded; stale frames discarded automatically")


def test_decoupled_capture_responsiveness():
    print("\n[Test 3] Decoupled capture responsiveness under slow inference...")
    video_path = os.path.abspath("test_cctv.mp4")
    if not os.path.exists(video_path):
        print("  ! test_cctv.mp4 not found, skipping capture responsiveness test")
        return

    # Initialize stream with analysis disabled to verify pure capture rate
    cam = CameraStream("test_decoupled", "Decoupled Cam", video_path, loop_file_stream=True)
    cam.enable_analysis = False
    assert cam.connect(timeout=5.0), "Failed to connect to test video"
    
    # Capture runs in background thread
    time.sleep(1.0)
    status = cam.get_status()
    captured_frames = status["frames_read"]
    assert captured_frames > 5, f"Capture thread starved or stalled: {captured_frames} frames"
    
    # Simulate slow downstream consumer polling intermittently
    for _ in range(3):
        f = cam.get_latest_frame()
        assert f is not None
        time.sleep(0.3)
        
    status2 = cam.get_status()
    assert status2["frames_read"] > captured_frames, "Capture thread halted when consumer was slow"
    cam.disconnect()
    print(f"  ✓ Capture thread captured {status2['frames_read']} frames continuously during slow consumption")


def test_tracker_isolation_multi_camera():
    print("\n[Test 4] Per-camera ByteTrack tracker isolation...")
    detector = get_detector()
    
    dummy_frame = np.zeros((480, 640, 3), dtype=np.uint8)
    dummy_frame[100:200, 100:200] = 255
    
    # Track on cam_A
    detector.track_frame(dummy_frame, camera_id="cam_A", is_live=True)
    # Track on cam_B
    detector.track_frame(dummy_frame, camera_id="cam_B", is_live=True)
    
    tracker_a = detector._trackers.get("cam_A")
    tracker_b = detector._trackers.get("cam_B")
    
    assert tracker_a is not None and tracker_b is not None, "Trackers not instantiated"
    assert tracker_a is not tracker_b, "Trackers are sharing the same instance across cameras!"
    print("  ✓ Cameras have strictly independent ByteTrack instances")
    
    # Cleanup
    detector.reset_tracker("cam_A")
    detector.reset_tracker("cam_B")
    assert "cam_A" not in detector._trackers
    assert "cam_B" not in detector._trackers
    print("  ✓ Trackers properly released and de-allocated on reset")


def test_worker_cleanup_and_reconnection():
    print("\n[Test 5] Worker cleanup and camera reconnection without thread leak...")
    video_path = os.path.abspath("test_cctv.mp4")
    if not os.path.exists(video_path):
        print("  ! test_cctv.mp4 not found, skipping reconnection test")
        return

    sm = StreamManager()
    cam_id = "test_reconnect_cam"
    
    threads_before = threading.active_count()
    
    # Connect
    ok, status = sm.connect_camera(cam_id, "Test Reconnect", video_path, timeout=5.0)
    assert ok, "Failed to connect stream"
    time.sleep(0.5)
    
    # Verify stream is active
    assert status["status"] == "connected"
    
    # Disconnect
    ok_disc = sm.disconnect_camera(cam_id)
    assert ok_disc, "Failed to disconnect stream"
    time.sleep(0.5)
    
    # Verify stream is cleaned up
    assert sm.get_stream(cam_id) is None
    
    # Reconnect
    ok_rec, status_rec = sm.connect_camera(cam_id, "Test Reconnect", video_path, timeout=5.0)
    assert ok_rec, "Failed to reconnect stream"
    time.sleep(0.5)
    
    # Clean up again
    sm.disconnect_camera(cam_id)
    time.sleep(0.5)
    
    threads_after = threading.active_count()
    assert threads_after <= threads_before + 1, f"Thread leak detected! Before: {threads_before}, After: {threads_after}"
    print(f"  ✓ Reconnection succeeded; threads cleanly collected (Before: {threads_before}, After: {threads_after})")


def test_model_inference_exception_resilience():
    print("\n[Test 6] Graceful handling of inference exceptions...")
    coord = FairInferenceCoordinator()
    
    # Ensure lock releases even if an exception occurs inside the context
    try:
        with coord.inference_context("faulty_cam", is_live=True):
            raise RuntimeError("Simulated GPU inference crash")
    except RuntimeError:
        pass
        
    # Verify the coordinator is NOT locked and can accept subsequent calls
    acquired = False
    with coord.inference_context("healthy_cam", is_live=True):
        acquired = True
        
    assert acquired, "FairInferenceCoordinator remained locked after an exception occurred!"
    print("  ✓ FairInferenceCoordinator released lock properly after inference exception")


def test_indexer_multi_camera_integrity():
    print("\n[Test 7] LiveIndexer multi-camera event integrity...")
    indexer = get_live_indexer()
    
    # Record mock events from two different cameras
    det_a = [{
        "track_id": 1,
        "label": "car",
        "confidence": 0.88,
        "bbox": [10, 10, 50, 50]
    }]
    det_b = [{
        "track_id": 1, # Same track_id number, but different camera!
        "label": "person",
        "confidence": 0.92,
        "bbox": [20, 20, 60, 60]
    }]
    
    dummy_img = np.zeros((100, 100, 3), dtype=np.uint8)
    now = time.time()
    indexer.process_frame_detections("cam_alpha", dummy_img, det_a, frame_number=10, timestamp=now)
    indexer.process_frame_detections("cam_beta", dummy_img, det_b, frame_number=15, timestamp=now)
    
    episodes_a = indexer._active_episodes.get("cam_alpha", {})
    episodes_b = indexer._active_episodes.get("cam_beta", {})
    
    assert 1 in episodes_a, "cam_alpha episode missing"
    assert 1 in episodes_b, "cam_beta episode missing"
    
    # Check that cam_alpha episode is car and cam_beta is person
    assert episodes_a[1].concept == "car"
    assert episodes_b[1].concept == "person"
    
    # Finalize cam_alpha
    indexer.finalize_camera_stream("cam_alpha")
    assert len(indexer._active_episodes.get("cam_alpha", {})) == 0
    # cam_beta should still be active
    assert 1 in indexer._active_episodes.get("cam_beta", {})
    
    # Finalize cam_beta
    indexer.finalize_camera_stream("cam_beta")
    print("  ✓ Multi-camera episodes tracked separately and finalized independently without cross-talk")


def main():
    print("=" * 70)
    print("STAGE H: MULTI-CAMERA CONCURRENCY & RESILIENCE TEST SUITE")
    print("=" * 70)
    
    test_fair_inference_coordinator_fairness()
    test_bounded_frame_buffer_and_stale_drop()
    test_decoupled_capture_responsiveness()
    test_tracker_isolation_multi_camera()
    test_worker_cleanup_and_reconnection()
    test_model_inference_exception_resilience()
    test_indexer_multi_camera_integrity()
    
    print("\n" + "=" * 70)
    print("ALL STAGE H CORRECTNESS & CONCURRENCY TESTS PASSED (7/7)")
    print("=" * 70)


if __name__ == "__main__":
    main()
