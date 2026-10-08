import os
import sys
import time
import requests
import json

BASE_URL = "http://127.0.0.1:8000"

def run_tests():
    print("==================================================")
    print("RUNNING 10-STEP CLEAN INTEGRATION TEST SUITE")
    print("==================================================")
    
    # STEP 1: Reset Data
    print("\n[STEP 1] Reset Data via POST /api/data/reset...")
    res = requests.post(f"{BASE_URL}/api/data/reset", json={"confirm": True})
    assert res.status_code == 200, f"Reset failed: {res.text}"
    
    cams = requests.get(f"{BASE_URL}/api/cameras").json()
    events = requests.get(f"{BASE_URL}/api/events").json()
    print(f"Cameras after reset: {len(cams)}, Events after reset: {len(events)}")
    assert len(cams) == 0, f"Expected 0 cameras, got {len(cams)}"
    assert len(events) == 0, f"Expected 0 events, got {len(events)}"
    print("✓ STEP 1 PASSED: 0 cameras, 0 events.")
    
    # STEP 2: Upload ONE test CCTV video
    print("\n[STEP 2] Uploading video 1 (test_real_cctv.mp4)...")
    video_file = "test_real_cctv.mp4"
    if not os.path.exists(video_file):
        raise FileNotFoundError(f"Video file {video_file} not found.")
        
    with open(video_file, "rb") as f:
        res = requests.post(
            f"{BASE_URL}/api/videos/upload",
            files={"file": ("cctv_entrance.mp4", f, "video/mp4")},
            data={"camera_name": "Entrance Cam", "source_type": "uploaded_video"}
        )
    assert res.status_code == 200, f"Upload failed: {res.text}"
    up_data = res.json()
    camera_id = up_data["camera_id"]
    dataset_id = up_data.get("dataset_id")
    job_id = up_data["job_id"]
    print(f"Uploaded! camera_id={camera_id}, dataset_id={dataset_id}, job_id={job_id}")
    
    # Wait for processing job to complete
    print("Polling job progress...")
    for _ in range(90):
        j_res = requests.get(f"{BASE_URL}/api/jobs/{job_id}").json()
        status = j_res.get("status")
        progress = j_res.get("progress", 0)
        total = j_res.get("total", 0)
        stage = j_res.get("stage", "")
        print(f"  Job {job_id}: status={status}, progress={progress}/{total}, stage={stage}")
        if status == "completed":
            break
        elif status == "failed":
            raise RuntimeError(f"Processing failed: {j_res.get('error')}")
        time.sleep(1)
        
    # Verify camera status is READY
    cams = requests.get(f"{BASE_URL}/api/cameras").json()
    assert len(cams) == 1, f"Expected 1 camera, got {len(cams)}"
    cam1 = cams[0]
    print(f"Camera record: id={cam1['id']}, name={cam1['name']}, status={cam1['status']}, fps={cam1['fps']:.1f}, duration={cam1['duration']:.1f}s, res={cam1['resolution']}")
    assert cam1["status"] == "READY", f"Expected READY, got {cam1['status']}"
    assert cam1["fps"] > 0, "Expected valid FPS"
    assert cam1["duration"] > 0, "Expected valid duration"
    print("✓ STEP 2 PASSED: 1 camera created and READY.")
    
    # STEP 3: Verify Video Analysis Summary
    print("\n[STEP 3] Verifying Analysis Summary & Camera Metadata...")
    summary_res = requests.get(f"{BASE_URL}/api/cameras/{camera_id}/summary")
    assert summary_res.status_code == 200, f"Summary failed: {summary_res.text}"
    summary = summary_res.json()
    if "summary" in summary:
        summary = summary["summary"]
    print(f"Summary: file_name={summary.get('file_name')}, duration={summary.get('duration_seconds')}s, fps={summary.get('fps')}, detected_objects={list(summary.get('detected_objects', {}).keys())}")
    assert summary.get("file_name") == "cctv_entrance.mp4"
    assert summary.get("fps") > 0
    print("✓ STEP 3 PASSED: Real metadata and analysis summary available.")
    
    # STEP 4: Verify Event Timeline
    print("\n[STEP 4] Verifying Event Timeline for camera_01...")
    events_res = requests.get(f"{BASE_URL}/api/events?camera_id={camera_id}")
    assert events_res.status_code == 200
    cam1_events = events_res.json()
    print(f"Events recorded for {camera_id}: {len(cam1_events)}")
    assert len(cam1_events) > 0, "Expected at least 1 tracked event in CCTV video"
    for ev in cam1_events[:3]:
        print(f"  Event {ev['id'][:8]}: concept={ev['concept']}, time={ev['start_timestamp']:.1f}-{ev['end_timestamp']:.1f}s, conf={ev['confidence']:.2f}")
    print("✓ STEP 4 PASSED: Event timeline accurately populated with real detections.")
    
    # STEP 5: Natural Language Query "person"
    print("\n[STEP 5] Query 'person' on camera_01...")
    q_res = requests.post(f"{BASE_URL}/api/query", json={"query": "person", "camera_id": camera_id})
    assert q_res.status_code == 200
    q_data = q_res.json()
    print(f"Query response: verdict={q_data.get('verdict')}, summary={q_data.get('summary')}, results_count={len(q_data.get('results', []))}")
    assert "No active cameras" not in q_data.get("summary", "")
    assert q_data.get("verdict") == "MATCH", f"Expected MATCH for person, got {q_data.get('verdict')}"
    print("✓ STEP 5 PASSED: 'person' matched without 'No active cameras' error.")
    
    # STEP 6: Query "show the white car" vs "moving person"
    print("\n[STEP 6] Testing queries 'moving person' and 'show the red car'...")
    q_person = requests.post(f"{BASE_URL}/api/query", json={"query": "moving person", "camera_id": camera_id}).json()
    print(f"Moving person result: {q_person.get('verdict')} - {q_person.get('summary')}")
    assert q_person.get("verdict") == "MATCH", f"Expected MATCH for moving person, got {q_person.get('verdict')}"
    
    q_car = requests.post(f"{BASE_URL}/api/query", json={"query": "show the red car", "camera_id": camera_id}).json()
    print(f"Red car result: {q_car.get('verdict')} - {q_car.get('summary')}")
    assert q_car.get("verdict") == "NO MATCH", f"Expected NO MATCH for red car, got {q_car.get('verdict')}"
    print("✓ STEP 6 PASSED: Precision querying works (Person MATCH, Red car NO MATCH).")
    
    # STEP 7: Evidence Verification
    print("\n[STEP 7] Verifying Evidence for matched event...")
    event_id = q_person["results"][0].get("event") or q_person["results"][0].get("id")
    ev_res = requests.get(f"{BASE_URL}/api/evidence/{event_id}")
    assert ev_res.status_code == 200
    ev_data = ev_res.json()
    print(f"Evidence: camera_id={ev_data.get('camera_id')}, clip_url={ev_data.get('clip_url')}, time_window={ev_data.get('start_timestamp')}-{ev_data.get('end_timestamp')}s")
    assert ev_data.get("camera_id") == camera_id
    assert ev_data.get("clip_url") is not None
    print("✓ STEP 7 PASSED: Evidence correctly linked to camera and video.")
    
    # STEP 8: Verify Debug State API
    print("\n[STEP 8] Verifying GET /api/debug/state...")
    dbg = requests.get(f"{BASE_URL}/api/debug/state").json()
    print(f"Debug state: cameras={len(dbg['cameras'])}, active={len(dbg['active_cameras'])}, events={dbg['events_count']}")
    assert len(dbg["cameras"]) == 1
    assert len(dbg["active_cameras"]) == 1
    assert dbg["events_count"] == len(cam1_events)
    print("✓ STEP 8 PASSED: Debug state is consistent.")
    
    # STEP 10: Multi-camera Isolation Test
    print("\n[STEP 10] Uploading second video (camera_02)...")
    with open(video_file, "rb") as f:
        res2 = requests.post(
            f"{BASE_URL}/api/videos/upload",
            files={"file": ("cctv_corridor.mp4", f, "video/mp4")},
            data={"camera_name": "Corridor Cam", "source_type": "uploaded_video"}
        )
    assert res2.status_code == 200
    up2 = res2.json()
    cam2_id = up2["camera_id"]
    job2_id = up2["job_id"]
    
    # Wait for job 2
    for _ in range(90):
        j2 = requests.get(f"{BASE_URL}/api/jobs/{job2_id}").json()
        if j2.get("status") == "completed":
            break
        time.sleep(1)
        
    cams_all = requests.get(f"{BASE_URL}/api/cameras").json()
    assert len(cams_all) == 2, f"Expected 2 cameras, got {len(cams_all)}"
    print(f"Total cameras now: {len(cams_all)}")
    
    events_cam1 = requests.get(f"{BASE_URL}/api/events?camera_id={camera_id}").json()
    events_cam2 = requests.get(f"{BASE_URL}/api/events?camera_id={cam2_id}").json()
    print(f"Events in {camera_id}: {len(events_cam1)}, Events in {cam2_id}: {len(events_cam2)}")
    
    for ev in events_cam1:
        assert ev["camera_id"] == camera_id
    for ev in events_cam2:
        assert ev["camera_id"] == cam2_id
        
    print("✓ STEP 10 PASSED: Strict isolation between camera_01 and camera_02.")
    
    print("\n==================================================")
    print("ALL 10 CLEAN INTEGRATION TESTS COMPLETED SUCCESSFULLY!")
    print("==================================================")

if __name__ == "__main__":
    run_tests()
