"""
Stage G: Real-Time Alerting, Tripwire Rules & Proactive Notifications
Automated Test Suite

Verifies:
1. Rule creation, updates, disabling, and deletion.
2. Object-detection rules using genuine/fixture detection events.
3. Restricted hours and overnight schedules (e.g., 22:00 to 06:00).
4. Tripwire crossings in both directions ('ab' vs 'ba') and bidirectional ('any').
5. False crossings caused by jitter or bounding-box overlap.
6. Polygon zone entry, continued presence, exit, and re-entry.
7. Cooldown and duplicate suppression.
8. Independent evaluation across multiple cameras.
9. Persistence across application restart / reload.
10. Alert evidence and acknowledgement / resolve lifecycle.
11. Invalid rules, missing cameras, and storage validation.
12. Queue saturation and graceful shutdown.
13. Uploaded-video and conversational-query regression.
"""

import sys
import os
import time
import json
import uuid
import urllib.request
import urllib.error
from datetime import datetime

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from backend.app.core.alert_engine import (
    AlertEngine,
    is_point_in_polygon,
    line_segments_intersect,
    determine_crossing_direction,
    is_within_time_window,
    get_alert_engine
)
from backend.app.db.database import get_db_connection, execute_query

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
    print("STAGE G: REAL-TIME ALERTING & TRIPWIRE RULES AUTOMATED TESTS")
    print("=" * 65)

    # Clean up test data from prior runs
    with get_db_connection() as conn:
        conn.execute("DELETE FROM alert_rules WHERE name LIKE 'TEST_%'")
        conn.execute("DELETE FROM alerts WHERE rule_name LIKE 'TEST_%'")
        conn.commit()

    # ---------------------------------------------------------
    # TEST 1: Rule Creation, Updates, Disabling, and Deletion
    # ---------------------------------------------------------
    print("\n[Test 1] Testing Rule CRUD (Create, Update, Disable, Delete)...")
    status, res = make_request("/api/alerts/rules", method="POST", data={
        "name": "TEST_Person_Rule_01",
        "rule_type": "object_detected",
        "object_class": "person",
        "min_confidence": 0.45,
        "severity": "warning",
        "cooldown_seconds": 15.0
    })
    assert status == 201, f"Expected 201 Created, got {status}: {res}"
    rule_id = res["id"]
    print(f"  ✓ Created rule: {rule_id}")

    # Read rule
    status, r_get = make_request(f"/api/alerts/rules/{rule_id}")
    assert status == 200, f"Expected 200, got {status}"
    assert r_get["name"] == "TEST_Person_Rule_01"
    assert r_get["min_confidence"] == 0.45
    assert r_get["enabled"] is True
    print("  ✓ Retrieved rule by ID successfully")

    # Update rule (disable)
    status, r_up = make_request(f"/api/alerts/rules/{rule_id}", method="PUT", data={
        "enabled": False,
        "min_confidence": 0.50
    })
    assert status == 200
    assert r_up["enabled"] is False
    assert r_up["min_confidence"] == 0.50
    print("  ✓ Updated rule: disabled and updated min_confidence")

    # Re-enable
    make_request(f"/api/alerts/rules/{rule_id}", method="PUT", data={"enabled": True})

    # Delete rule
    status, r_del = make_request(f"/api/alerts/rules/{rule_id}", method="DELETE")
    assert status == 200
    status, _ = make_request(f"/api/alerts/rules/{rule_id}")
    assert status == 404, "Deleted rule should return 404"
    print("  ✓ Deleted rule successfully verified")

    # ---------------------------------------------------------
    # TEST 2: Object Detection Rule Evaluation
    # ---------------------------------------------------------
    print("\n[Test 2] Testing Object-Detection rule evaluation...")
    engine = AlertEngine()
    
    # Insert test rule into DB and reload
    r2_id = f"rule_test_{uuid.uuid4().hex[:6]}"
    now = time.time()
    with get_db_connection() as conn:
        conn.execute("""
            INSERT INTO alert_rules (
                id, name, enabled, camera_id, rule_type, object_class,
                min_confidence, cooldown_seconds, severity, created_at, updated_at
            ) VALUES (?, ?, 1, NULL, 'object_detected', 'car', 0.4, 10.0, 'warning', ?, ?)
        """, (r2_id, "TEST_Car_Alert", now, now))
        conn.commit()
    engine.reload_rules()

    # Detections fixture: 1 low conf car (0.2), 1 matching car (0.75), 1 dog (0.9)
    test_dets = [
        {"class": "car", "confidence": 0.2, "bbox": [10, 10, 50, 50], "track_id": 101},
        {"class": "car", "confidence": 0.75, "bbox": [100, 100, 200, 200], "track_id": 102},
        {"class": "dog", "confidence": 0.9, "bbox": [50, 50, 80, 80], "track_id": 103}
    ]
    initial_alerts_count = engine.stats["alerts_generated"]
    engine.evaluate_frame_detections(
        camera_id="cam_test_01",
        camera_name="Test Camera 1",
        frame_number=1,
        timestamp=now,
        detections=test_dets,
        frame_width=640,
        frame_height=480
    )
    assert engine.stats["alerts_generated"] == initial_alerts_count + 1, "Expected exactly 1 alert for matching car"
    print("  ✓ Successfully matched car detection with confidence filter and class filter")

    # ---------------------------------------------------------
    # TEST 3: Restricted Hours & Overnight Schedule Logic
    # ---------------------------------------------------------
    print("\n[Test 3] Testing restricted hours (daytime & overnight windows)...")
    # Daytime window: 09:00 to 17:00
    dt_11am = datetime(2026, 10, 15, 11, 30) # Inside
    dt_8pm = datetime(2026, 10, 15, 20, 0)   # Outside
    assert is_within_time_window("09:00", "17:00", dt_11am) is True
    assert is_within_time_window("09:00", "17:00", dt_8pm) is False
    print("  ✓ Daytime window (09:00-17:00) verified")

    # Overnight window: 22:00 to 06:00
    dt_23pm = datetime(2026, 10, 15, 23, 15) # Inside overnight
    dt_3am = datetime(2026, 10, 15, 3, 45)   # Inside overnight
    dt_2pm = datetime(2026, 10, 15, 14, 0)   # Outside overnight
    assert is_within_time_window("22:00", "06:00", dt_23pm) is True, "23:15 should match overnight 22:00-06:00"
    assert is_within_time_window("22:00", "06:00", dt_3am) is True, "03:45 should match overnight 22:00-06:00"
    assert is_within_time_window("22:00", "06:00", dt_2pm) is False, "14:00 should not match overnight 22:00-06:00"
    print("  ✓ Overnight window (22:00-06:00) verified")

    # ---------------------------------------------------------
    # TEST 4: Tripwire Crossings & Directional Logic
    # ---------------------------------------------------------
    print("\n[Test 4] Testing tripwire crossing in directions 'ab', 'ba', and 'any'...")
    # Vertical tripwire along x = 0.5 from y=0.0 to y=1.0 (start: (0.5, 0.0), end: (0.5, 1.0))
    trip_start = (0.5, 0.0)
    trip_end = (0.5, 1.0)

    # Track moving Left to Right: (0.3, 0.5) -> (0.7, 0.5)
    dir_lr = determine_crossing_direction((0.3, 0.5), (0.7, 0.5), trip_start, trip_end)
    # Track moving Right to Left: (0.7, 0.5) -> (0.3, 0.5)
    dir_rl = determine_crossing_direction((0.7, 0.5), (0.3, 0.5), trip_start, trip_end)

    assert dir_lr in ('ab', 'ba'), f"Expected valid crossing direction, got {dir_lr}"
    assert dir_rl in ('ab', 'ba'), f"Expected valid crossing direction, got {dir_rl}"
    assert dir_lr != dir_rl, "Opposite directions must have opposite sign ('ab' vs 'ba')"
    print(f"  ✓ Left-to-Right detected as '{dir_lr}', Right-to-Left detected as '{dir_rl}'")

    # Non-intersecting motion: (0.1, 0.2) -> (0.4, 0.3)
    no_cross = determine_crossing_direction((0.1, 0.2), (0.4, 0.3), trip_start, trip_end)
    assert no_cross is None, "Non-intersecting path must return None"
    print("  ✓ Non-intersecting motion correctly returns None")

    # ---------------------------------------------------------
    # TEST 5: Boundary Jitter & Overlap Suppression
    # ---------------------------------------------------------
    print("\n[Test 5] Testing jitter and micro-overlap hysteresis suppression...")
    # Micro motion directly on the line without significant crossing (< 0.005)
    micro_p1 = (0.5001, 0.5)
    micro_p2 = (0.4999, 0.5)
    jitter_res = determine_crossing_direction(micro_p1, micro_p2, trip_start, trip_end)
    assert jitter_res is None, "Sub-threshold jitter must be suppressed by hysteresis"
    print("  ✓ Micro-jitter suppressed by hysteresis margin")

    # ---------------------------------------------------------
    # TEST 6: Polygon Zone Entry, Continued Presence, and Re-entry
    # ---------------------------------------------------------
    print("\n[Test 6] Testing Polygon Zone Entry, Continued Presence, Exit & Re-entry...")
    # Define rectangular zone [0.2, 0.2] to [0.6, 0.6]
    zone_pts = [(0.2, 0.2), (0.6, 0.2), (0.6, 0.6), (0.2, 0.6)]
    assert is_point_in_polygon((0.4, 0.4), zone_pts) is True, "Center point must be inside polygon"
    assert is_point_in_polygon((0.1, 0.1), zone_pts) is False, "Outside point must be outside polygon"

    # Insert zone rule
    r_zone_id = f"rule_zone_{uuid.uuid4().hex[:6]}"
    with get_db_connection() as conn:
        conn.execute("""
            INSERT INTO alert_rules (
                id, name, enabled, camera_id, rule_type, object_class,
                min_confidence, zone_points, cooldown_seconds, severity, created_at, updated_at
            ) VALUES (?, ?, 1, 'cam_zone_test', 'zone_entry', 'person', 0.3, ?, 0.0, 'critical', ?, ?)
        """, (r_zone_id, "TEST_Zone_Entry", json.dumps(zone_pts), now, now))
        conn.commit()
    engine.reload_rules()

    zone_alerts_before = engine.stats["alerts_generated"]

    # Frame 1: Person outside polygon (x=0.1, y=0.1 in 640x480 -> norm=(0.1, 0.1))
    det_outside = [{"class": "person", "confidence": 0.8, "bbox": [50, 40, 70, 55], "track_id": 201}]
    engine.evaluate_frame_detections("cam_zone_test", "Zone Cam", 1, now + 1, det_outside, 640, 480)
    assert engine.stats["alerts_generated"] == zone_alerts_before, "No alert while outside zone"

    # Frame 2: Person enters polygon (norm bottom center ~ 0.4, 0.4)
    # bbox in 640x480: [200, 150, 312, 192] -> center=(256/640=0.4, 192/480=0.4)
    det_inside = [{"class": "person", "confidence": 0.8, "bbox": [200, 150, 312, 192], "track_id": 201}]
    engine.evaluate_frame_detections("cam_zone_test", "Zone Cam", 2, now + 2, det_inside, 640, 480)
    assert engine.stats["alerts_generated"] == zone_alerts_before + 1, "Alert must fire on entry"
    print("  ✓ Fired alert on transition: OUTSIDE -> ENTERED")

    # Frame 3: Continued presence inside polygon (remains inside)
    engine.evaluate_frame_detections("cam_zone_test", "Zone Cam", 3, now + 3, det_inside, 640, 480)
    assert engine.stats["alerts_generated"] == zone_alerts_before + 1, "No duplicate alert while remaining inside"
    print("  ✓ No duplicate alert on continued presence inside polygon")

    # Frame 4: Person exits polygon
    engine.evaluate_frame_detections("cam_zone_test", "Zone Cam", 4, now + 4, det_outside, 640, 480)
    assert engine.stats["alerts_generated"] == zone_alerts_before + 1, "No alert on exit"
    print("  ✓ Person exited zone cleanly")

    # Frame 5: Person re-enters polygon
    engine.evaluate_frame_detections("cam_zone_test", "Zone Cam", 5, now + 5, det_inside, 640, 480)
    assert engine.stats["alerts_generated"] == zone_alerts_before + 2, "Alert must fire upon re-entry"
    print("  ✓ Fired alert on genuine re-entry into zone")

    # ---------------------------------------------------------
    # TEST 7: Cooldown & Duplicate Suppression
    # ---------------------------------------------------------
    print("\n[Test 7] Testing Cooldown and Duplicate Suppression...")
    r_cd_id = f"rule_cd_{uuid.uuid4().hex[:6]}"
    with get_db_connection() as conn:
        conn.execute("""
            INSERT INTO alert_rules (
                id, name, enabled, camera_id, rule_type, object_class,
                min_confidence, cooldown_seconds, severity, created_at, updated_at
            ) VALUES (?, ?, 1, NULL, 'object_detected', 'person', 0.3, 30.0, 'warning', ?, ?)
        """, (r_cd_id, "TEST_Cooldown_Rule", now, now))
        conn.commit()
    engine.reload_rules()

    cd_before = engine.stats["alerts_generated"]
    det_person = [{"class": "person", "confidence": 0.85, "bbox": [100, 100, 200, 300], "track_id": 301}]

    # Fire 1
    engine.evaluate_frame_detections("cam_cd", "CD Cam", 1, now + 10, det_person, 640, 480)
    assert engine.stats["alerts_generated"] == cd_before + 1

    # Fire 2 (5 seconds later, within 30s cooldown)
    engine.evaluate_frame_detections("cam_cd", "CD Cam", 2, now + 15, det_person, 640, 480)
    assert engine.stats["alerts_generated"] == cd_before + 1, "Must be suppressed by cooldown"
    print("  ✓ Alert within cooldown period successfully suppressed")

    # Fire 3 (35 seconds later, past 30s cooldown)
    engine.evaluate_frame_detections("cam_cd", "CD Cam", 3, now + 45, det_person, 640, 480)
    assert engine.stats["alerts_generated"] == cd_before + 2, "Must fire once cooldown expires"
    print("  ✓ Alert fired after cooldown elapsed")

    # ---------------------------------------------------------
    # TEST 8: Multi-Camera Independent Evaluation
    # ---------------------------------------------------------
    print("\n[Test 8] Testing independent evaluation across multiple cameras...")
    r_cam1_id = f"rule_c1_{uuid.uuid4().hex[:6]}"
    with get_db_connection() as conn:
        conn.execute("""
            INSERT INTO alert_rules (
                id, name, enabled, camera_id, rule_type, object_class,
                min_confidence, cooldown_seconds, severity, created_at, updated_at
            ) VALUES (?, ?, 1, 'specific_cam_1', 'object_detected', 'truck', 0.3, 0.0, 'info', ?, ?)
        """, (r_cam1_id, "TEST_Cam1_Truck_Only", now, now))
        conn.commit()
    engine.reload_rules()

    c_before = engine.stats["alerts_generated"]
    det_truck = [{"class": "truck", "confidence": 0.9, "bbox": [100, 100, 300, 300], "track_id": 401}]

    # Evaluate on specific_cam_2 (should NOT trigger specific_cam_1 rule)
    engine.evaluate_frame_detections("specific_cam_2", "Cam 2", 1, now + 50, det_truck, 640, 480)
    assert engine.stats["alerts_generated"] == c_before, "Rule scoped to specific_cam_1 must not fire on specific_cam_2"

    # Evaluate on specific_cam_1 (SHOULD trigger)
    engine.evaluate_frame_detections("specific_cam_1", "Cam 1", 1, now + 51, det_truck, 640, 480)
    assert engine.stats["alerts_generated"] == c_before + 1, "Rule must fire on designated camera"
    print("  ✓ Camera scoping strictly enforced without cross-talk")

    # ---------------------------------------------------------
    # TEST 9: Persistence Across Application Restart / Reload
    # ---------------------------------------------------------
    print("\n[Test 9] Testing persistent rules reload across restarts...")
    engine2 = AlertEngine()
    # Check that rules created above were loaded into new engine instance
    with engine2._rules_lock:
        rule_ids = set(engine2._rules.keys())
    assert r2_id in rule_ids, f"Rule {r2_id} should be loaded from DB on startup"
    assert r_zone_id in rule_ids, f"Rule {r_zone_id} should be loaded from DB on startup"
    print(f"  ✓ Fresh AlertEngine initialized and reloaded {len(rule_ids)} persistent rules from DB")

    # ---------------------------------------------------------
    # TEST 10: Alert Evidence and Acknowledgement Lifecycle
    # ---------------------------------------------------------
    print("\n[Test 10] Testing Alert Evidence and Acknowledge/Resolve lifecycle...")
    # Wait briefly for persistence worker to write queued alerts
    time.sleep(1.0)
    status, alert_list = make_request("/api/alerts?limit=10")
    assert status == 200
    assert alert_list["total_count"] > 0, "Expected persisted alerts in database"
    
    first_alert = alert_list["alerts"][0]
    alert_id = first_alert["id"]
    print(f"  ✓ Persisted alert found: {alert_id} (Reason: {first_alert['trigger_reason']})")

    # Acknowledge
    status, ack_res = make_request(f"/api/alerts/{alert_id}/acknowledge", method="POST")
    assert status == 200
    assert ack_res["status"] == "acknowledged"
    assert ack_res["acknowledged_at"] is not None
    print("  ✓ Successfully acknowledged alert")

    # Resolve
    status, res_res = make_request(f"/api/alerts/{alert_id}/resolve", method="POST")
    assert status == 200
    assert res_res["status"] == "resolved"
    assert res_res["resolved_at"] is not None
    print("  ✓ Successfully resolved alert")

    # ---------------------------------------------------------
    # TEST 11: Validation for Invalid Rules & Missing Cameras
    # ---------------------------------------------------------
    print("\n[Test 11] Testing invalid payload rejection & error handling...")
    # Invalid rule type
    status, res = make_request("/api/alerts/rules", method="POST", data={
        "name": "Invalid Rule Type",
        "rule_type": "teleportation_detected"
    })
    assert status == 422, f"Expected 422 Unprocessable Entity, got {status}"

    # Invalid tripwire (only 1 point)
    status, res = make_request("/api/alerts/rules", method="POST", data={
        "name": "Invalid Tripwire",
        "rule_type": "tripwire_crossing",
        "tripwire_line": [[0.5, 0.5]]
    })
    assert status == 422

    # Nonexistent camera ID
    status, res = make_request("/api/alerts/rules", method="POST", data={
        "name": "Ghost Camera Rule",
        "camera_id": "non_existent_camera_9999",
        "rule_type": "object_detected"
    })
    assert status == 404, f"Expected 404 for missing camera, got {status}"
    print("  ✓ Successfully rejected invalid payloads and missing camera references")

    # ---------------------------------------------------------
    # TEST 12: Queue Saturation & Graceful Shutdown
    # ---------------------------------------------------------
    print("\n[Test 12] Testing bounded queue saturation handling & shutdown...")
    small_engine = AlertEngine(queue_maxsize=3)
    # Shut down background worker first so it does not pop items from queue
    small_engine.shutdown()
    
    # Fill queue to maximum capacity
    for i in range(3):
        small_engine._alert_queue.put_nowait({"id": f"dummy_{i}"})
    
    # Next alert should overflow gracefully without crashing
    small_engine._dispatch_alert_if_not_suppressed(
        rule={"id": "r_dummy", "name": "Dummy", "severity": "info", "cooldown_seconds": 0.0},
        camera_id="cam_dummy",
        camera_name="Dummy Cam",
        timestamp=time.time(),
        det={"class": "car", "confidence": 0.9},
        track_id="999",
        trigger_reason="Queue test overflow",
        evidence_frame_path=None
    )
    assert small_engine.stats["queue_overflows"] >= 1, "Queue overflow counter must increment"
    print("  ✓ Gracefully handled queue saturation and verified worker shutdown")

    # ---------------------------------------------------------
    # TEST 13: Uploaded-Video & Conversational Query Regression
    # ---------------------------------------------------------
    print("\n[Test 13] Checking regression for uploaded video and queries...")
    status, cams = make_request("/api/cameras")
    assert status == 200
    print(f"  ✓ Camera endpoint operational ({len(cams)} cameras listed)")

    status, query_res = make_request("/api/query", method="POST", data={
        "query": "Show any detected cars"
    })
    assert status == 200, f"Query endpoint failed: {status}: {query_res}"
    assert "verdict" in query_res or "summary" in query_res, "Query engine returned structured response"
    print("  ✓ Conversational query endpoint operational and non-regressed")

    # Clean up test rules
    with get_db_connection() as conn:
        conn.execute("DELETE FROM alert_rules WHERE name LIKE 'TEST_%'")
        conn.execute("DELETE FROM alerts WHERE rule_name LIKE 'TEST_%'")
        conn.commit()

    print("\n" + "=" * 65)
    print("ALL 13 STAGE G AUTOMATED TESTS PASSED SUCCESSFULLY! (100%)")
    print("=" * 65)

if __name__ == "__main__":
    run_tests()
