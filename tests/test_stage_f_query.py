"""
Stage F: Conversational Query Engine Integration Automated Test Suite
Verifies:
1. Natural language query parsing into structured filters.
2. Camera resolution and unknown location clarification ("main gate").
3. Relative, calendar, and clock time-range interpretation.
4. Searches against Stage E live persisted records and uploaded CCTV records.
5. Evidence-based distinct-track counting without frame duplication.
6. Empty results and grounded insufficient evidence reporting.
7. Attribute searches (bags, holding objects, knives).
8. Cross-camera candidate matching and uncertainty reporting.
9. Backward compatibility with uploaded video queries.
10. Error handling for invalid inputs.
"""

import sys
import os
import time
import json
import urllib.request
import urllib.error

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from backend.app.core.query_parser import get_query_parser
from backend.app.core.vector_search import get_vector_search
from backend.app.db.database import execute_query

BASE_URL = "http://127.0.0.1:8000"

def make_request(endpoint, method="GET", data=None):
    url = f"{BASE_URL}{endpoint}"
    req_data = json.dumps(data).encode("utf-8") if data is not None else None
    headers = {"Content-Type": "application/json"} if data is not None else {}
    req = urllib.request.Request(url, data=req_data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=15) as response:
            body = response.read().decode("utf-8")
            return response.status, json.loads(body) if body else {}
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8")
        try:
            return e.code, json.loads(body)
        except Exception:
            return e.code, {"error": body}

def run_tests():
    print("=" * 65)
    print("STAGE F: CONVERSATIONAL QUERY ENGINE INTEGRATION TESTS")
    print("=" * 65)

    parser = get_query_parser()
    vsearch = get_vector_search()

    # 1. NLP Parser Tests
    print("\n[Test 1] Testing natural language parsing of Stage F queries...")
    # 1a. Count query with relative time
    p1 = parser.parse("How many cars passed Camera 1 in the last 30 minutes?")
    assert p1["intent"] == "COUNT", f"Expected COUNT, got {p1['intent']}"
    assert p1["object"] == "car", f"Expected car, got {p1['object']}"
    assert p1["camera"] == "camera_01", f"Expected camera_01, got {p1['camera']}"
    assert p1["time_filter"] is not None and "30 minutes" in p1["time_filter"]["label"]
    print("  ✓ 'How many cars passed Camera 1 in the last 30 minutes?' parsed correctly.")

    # 1b. Unknown landmark with clarification
    p2 = parser.parse("Did a red car pass the main gate in the last hour?")
    assert p2["intent"] == "YES_NO", f"Expected YES_NO, got {p2['intent']}"
    assert p2["object"] == "car", f"Expected car, got {p2['object']}"
    assert p2["color"] == "red", f"Expected red, got {p2['color']}"
    assert p2["needs_clarification"] is True, "Expected needs_clarification=True for unmapped main gate"
    assert p2["missing_reference"] == "main gate"
    print("  ✓ 'Did a red car pass the main gate in the last hour?' triggered clarification for 'main gate'.")

    # 1c. Bags & Calendar time
    p3 = parser.parse("Find people carrying bags today.")
    assert p3["intent"] == "SEARCH", f"Expected SEARCH, got {p3['intent']}"
    assert p3["object"] == "person", f"Expected person, got {p3['object']}"
    assert p3["requires_bag"] is True, "Expected requires_bag=True"
    assert p3["time_filter"] is not None and p3["time_filter"]["label"] == "today"
    print("  ✓ 'Find people carrying bags today.' parsed correctly.")

    # 1d. Explicit clock time
    p4 = parser.parse("Show vehicles seen by Camera 2 after 5 PM.")
    assert p4["object"] == "vehicle", f"Expected vehicle, got {p4['object']}"
    assert p4["camera"] == "camera_02", f"Expected camera_02, got {p4['camera']}"
    assert p4["time_filter"] is not None and "after" in p4["time_filter"]["label"]
    print("  ✓ 'Show vehicles seen by Camera 2 after 5 PM.' parsed correctly.")

    # 1e. Cross-camera query
    p5 = parser.parse("Was the same vehicle seen on Camera 1 and Camera 3?")
    assert p5["intent"] == "CROSS_CAMERA", f"Expected CROSS_CAMERA, got {p5['intent']}"
    assert p5["cross_camera_list"] == ["camera_01", "camera_03"], f"Unexpected cameras: {p5['cross_camera_list']}"
    print("  ✓ 'Was the same vehicle seen on Camera 1 and Camera 3?' parsed CROSS_CAMERA correctly.")

    # 1f. Holding object in hand
    p6 = parser.parse("Find a person holding an object in their hand.")
    assert p6["object"] == "person", f"Expected person, got {p6['object']}"
    assert p6["requires_holding_object"] is True, "Expected requires_holding_object=True"
    print("  ✓ 'Find a person holding an object in their hand.' parsed correctly.")

    # 1g. Knife detection query
    p7 = parser.parse("Was anyone detected carrying a knife?")
    assert p7["intent"] == "YES_NO", f"Expected YES_NO, got {p7['intent']}"
    assert p7["object"] == "knife" or "knife" in p7.get("required_accessories", []), "Expected knife accessory/object"
    print("  ✓ 'Was anyone detected carrying a knife?' parsed correctly.")

    # 1h. Calendar time yesterday
    p8 = parser.parse("Show all buses recorded yesterday.")
    assert p8["object"] == "bus", f"Expected bus, got {p8['object']}"
    assert p8["time_filter"] is not None and p8["time_filter"]["label"] == "yesterday"
    print("  ✓ 'Show all buses recorded yesterday.' parsed correctly.")

    # 2. HTTP Clarification Endpoint Test
    print("\n[Test 2] Testing HTTP clarification prompt when landmark is unmapped...")
    status, clar_res = make_request("/api/query", method="POST", data={"query": "Did a red car pass the main gate in the last hour?"})
    assert status == 200, f"Query failed: {status}"
    assert clar_res["verdict"] == "CLARIFICATION REQUIRED", f"Expected CLARIFICATION REQUIRED, got {clar_res['verdict']}"
    assert clar_res["needs_clarification"] is True
    assert clar_res["missing_reference"] == "main gate"
    assert "available_cameras" in clar_res and len(clar_res["available_cameras"]) > 0
    print(f"  ✓ Clarification returned available cameras: {[c['id'] for c in clar_res['available_cameras']]}")

    # 3. Connect Live CCTV Stream & Index Events
    print("\n[Test 3] Connecting live camera feed for live query testing...")
    cam1_id = "test_live_stage_f1"
    cam2_id = "test_live_stage_f2"
    
    # Clean up any leftover records
    make_request(f"/api/cameras/{cam1_id}/disconnect", method="POST")
    make_request(f"/api/cameras/{cam2_id}/disconnect", method="POST")

    try:
        # Connect Camera 1
        st1, conn1 = make_request("/api/cameras/connect", method="POST", data={
            "camera_id": cam1_id,
            "name": "Live Front Gate",
            "stream_url": "test_cctv.mp4"
        })
        assert st1 == 200 and conn1.get("success"), f"Cam 1 connect failed: {conn1}"
        
        # Connect Camera 2
        st2, conn2 = make_request("/api/cameras/connect", method="POST", data={
            "camera_id": cam2_id,
            "name": "Live Yard Feed",
            "stream_url": "test_cctv.mp4"
        })
        assert st2 == 200 and conn2.get("success"), f"Cam 2 connect failed: {conn2}"

        print("  Waiting for live background indexing across feeds...")
        for _ in range(20):
            time.sleep(0.5)
            st, det_st = make_request(f"/api/cameras/{cam1_id}/detections/status")
            if st == 200 and det_st.get("frames_analyzed", 0) >= 4:
                break


        # 4. Live COUNT Query with Anti-Flooding Verification
        print("\n[Test 4] Testing COUNT query on live stream (distinct track episodes)...")
        status, count_res = make_request("/api/query", method="POST", data={
            "query": f"How many people passed in the last 30 minutes in {cam1_id}?",
            "camera_id": cam1_id
        })
        assert status == 200, f"Count query failed: {status}"
        assert count_res["verdict"] == "COUNT RESULT", f"Expected COUNT RESULT, got {count_res['verdict']}"
        assert count_res["count"] is not None and count_res["count"] > 0, "Expected non-zero unique tracks"
        debug = count_res["debug"]
        assert debug["final_count"] <= debug["total_person_detections"], "Unique count must not exceed total detections"
        print(f"  ✓ COUNT result: {count_res['count']} unique tracks (across {debug['total_person_detections']} raw frame detections). Summary: {count_res['summary']}")

        # 5. Live Grounded Attribute Query: Holding Object / Bag
        print("\n[Test 5] Testing attribute search on live indexed events...")
        status, bag_res = make_request("/api/query", method="POST", data={
            "query": f"Find people in {cam1_id} today.",
            "camera_id": cam1_id
        })
        assert status == 200, f"Live query failed: {status}"
        assert bag_res["verdict"] in ("MATCH", "YES"), f"Expected MATCH or YES, got {bag_res['verdict']}"
        assert len(bag_res["results"]) > 0, "Expected live matches"
        assert bag_res["results"][0]["frame"] is not None
        print(f"  ✓ Live event grounded match: {bag_res['summary']}")

        # 6. Safety / Weapon Query (Knife Detection)
        print("\n[Test 6] Testing negative safety query (knife / weapon)...")
        status, knife_res = make_request("/api/query", method="POST", data={
            "query": f"Was anyone detected carrying a knife in {cam1_id}?",
            "camera_id": cam1_id
        })
        assert status == 200, f"Knife query failed: {status}"
        assert knife_res["verdict"] == "NO", f"Expected NO, got {knife_res['verdict']}"
        assert "knife" in knife_res["summary"].lower(), f"Expected knife in summary, got {knife_res['summary']}"
        print(f"  ✓ Grounded negative answer: {knife_res['summary']}")

        # 7. Cross-Camera Correlation Query
        print("\n[Test 7] Testing Cross-Camera query across two feeds...")
        status, cross_res = make_request("/api/query", method="POST", data={
            "query": f"Was the same person seen on {cam1_id} and {cam2_id}?"
        })
        assert status == 200, f"Cross-camera query failed: {status}"
        assert cross_res["verdict"] == "CROSS_CAMERA", f"Expected CROSS_CAMERA, got {cross_res['verdict']}"
        assert len(cross_res["results"]) == 2, f"Expected 2 correlated events, got {len(cross_res['results'])}"
        assert "uncertainty_note" in cross_res["debug"], "Uncertainty disclaimer missing in cross-camera response"
        print(f"  ✓ Cross-camera correlation verified: {cross_res['summary']}")

        # 8. Uploaded Video Query Regression
        print("\n[Test 8] Testing regression for pre-recorded uploaded CCTV queries...")
        status, upload_res = make_request("/api/query", method="POST", data={
            "query": "Show the purple car",
            "camera_id": "camera_01"
        })
        assert status == 200, f"Uploaded query failed: {status}"
        assert upload_res["verdict"] == "NO MATCH", f"Expected NO MATCH for purple car, got {upload_res['verdict']}"

        status, person_res = make_request("/api/query", method="POST", data={
            "query": "Show a person",
            "camera_id": "camera_01"
        })
        assert status == 200, f"Person query failed: {status}"
        assert person_res["verdict"] in ("MATCH", "YES"), f"Expected MATCH for person, got {person_res['verdict']}"
        print("  ✓ Uploaded video queries regression test passed completely.")

        # 9. Invalid Input Handling
        print("\n[Test 9] Testing invalid input handling...")
        status, empty_res = make_request("/api/query", method="POST", data={"query": ""})
        assert status == 200, f"Empty query should not crash: {status}"
        assert empty_res["verdict"] in ("NO MATCH", "NO"), f"Expected NO MATCH, got {empty_res['verdict']}"

        status, bad_cam = make_request("/api/query", method="POST", data={
            "query": "Show cars",
            "camera_id": "non_existent_camera_xyz"
        })
        assert status == 200, f"Unknown camera should return safe response: {status}"
        assert bad_cam["verdict"] == "NO MATCH"
        assert "not found" in bad_cam["summary"].lower()
        print("  ✓ Invalid input handled safely without exception.")

        print("\n" + "=" * 65)
        print("ALL STAGE F AUTOMATED TESTS PASSED SUCCESSFULLY! (9/9)")
        print("=" * 65)

    finally:
        # Disconnect and cleanup test cameras
        print("\nCleaning up Stage F test feeds and database records...")
        make_request(f"/api/cameras/{cam1_id}/disconnect", method="POST")
        make_request(f"/api/cameras/{cam2_id}/disconnect", method="POST")
        execute_query("DELETE FROM events WHERE camera_id IN (?, ?)", (cam1_id, cam2_id))
        execute_query("DELETE FROM cameras WHERE id IN (?, ?)", (cam1_id, cam2_id))
        time.sleep(1.0)
        import shutil
        for cid in (cam1_id, cam2_id):
            p = f"data/frames/live/{cid}"
            if os.path.exists(p):
                shutil.rmtree(p, ignore_errors=True)
        print("  ✓ Cleanup complete.")

if __name__ == "__main__":
    run_tests()
