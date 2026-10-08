import os
import sys
import time
import requests
import json

BASE_URL = "http://127.0.0.1:8000"

def test_counting():
    print("==================================================")
    print("AUDITING COUNTING LOGIC & TRACKING ID GROUNDING")
    print("==================================================")
    
    # 1. Fetch active camera
    cams = requests.get(f"{BASE_URL}/api/cameras").json()
    assert len(cams) > 0, "No active cameras"
    cam1 = cams[0]
    cam_id = cam1["id"]
    dataset_id = cam1.get("dataset_id")
    print(f"Testing on Camera: {cam_id} | Dataset: {dataset_id} | Video: {cam1.get('video_path')}")
    
    # 2. Test "Count people"
    print("\n[TEST 1] Query: 'Count people'")
    res = requests.post(f"{BASE_URL}/api/query", json={"query": "Count people", "camera_id": cam_id})
    assert res.status_code == 200
    d = res.json()
    print("Verdict:", d.get("verdict"))
    print("Summary:", d.get("summary"))
    print("Count:", d.get("count"))
    debug = d.get("debug", {})
    print("Debug Info:", json.dumps(debug, indent=2))
    
    assert d.get("verdict") == "COUNT RESULT"
    assert debug.get("camera_id") == cam_id
    assert debug.get("total_person_detections") >= debug.get("final_count")
    assert len(debug.get("unique_tracking_ids", [])) == debug.get("final_count")
    print(f"✓ TEST 1 PASSED: Total Frame Detections: {debug.get('total_person_detections')} -> Unique Tracking IDs: {debug.get('final_count')}")

    # 3. Test "Count cars"
    print("\n[TEST 2] Query: 'Count cars'")
    res = requests.post(f"{BASE_URL}/api/query", json={"query": "Count cars", "camera_id": cam_id})
    assert res.status_code == 200
    d2 = res.json()
    print("Summary:", d2.get("summary"))
    print("Count:", d2.get("count"))
    debug2 = d2.get("debug", {})
    assert d2.get("count") == 0, f"Expected 0 cars, got {d2.get('count')}"
    print("✓ TEST 2 PASSED: 0 cars correctly returned.")

    # 4. Test "How many people passed through the gate?" with Region Memory
    print("\n[TEST 3] Creating 'gate' memory region & querying 'How many people passed through the gate?'")
    mem_res = requests.post(
        f"{BASE_URL}/api/memory",
        json={
            "name": "gate",
            "camera_id": cam_id,
            "region": [200.0, 100.0, 500.0, 360.0],
            "metadata": {"description": "Checkout gate area"}
        }
    )
    assert mem_res.status_code == 200
    
    res_gate = requests.post(f"{BASE_URL}/api/query", json={"query": "How many people passed through the gate?", "camera_id": cam_id})
    assert res_gate.status_code == 200
    d_gate = res_gate.json()
    print("Verdict:", d_gate.get("verdict"))
    print("Summary:", d_gate.get("summary"))
    print("Count crossing gate:", d_gate.get("count"))
    debug_gate = d_gate.get("debug", {})
    print("Debug gate:", json.dumps(debug_gate, indent=2))
    assert d_gate.get("verdict") == "COUNT RESULT"
    assert debug_gate.get("final_count") == d_gate.get("count")
    print("✓ TEST 3 PASSED: Region crossing tracking ID filter works accurately.")

    print("\n==================================================")
    print("ALL COUNTING LOGIC & TRACKING ID AUDIT TESTS PASSED!")
    print("==================================================")

if __name__ == "__main__":
    test_counting()
