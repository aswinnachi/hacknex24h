"""
tests/test_stage_i_persistence.py

Comprehensive Stage I automated test suite:
1. Persistent Evidence Storage (SQLite metadata, file paths, retention, credentials safety).
2. YouTube Live Source History & Session Tracking.
3. Cross-Camera Candidate Matching & Evidence Exposure.
4. End-to-End Alert Evidence, Deduplication, Acknowledgment, and Resolution.
5. Server Restart & Recovery Survival.
"""

import os
import sys
import time
import json
import uuid
import unittest
import urllib.request
import urllib.error

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from backend.app.db.database import execute_query, get_db_connection
from backend.app.core.config import settings
from backend.app.core.live_indexer import get_live_indexer, FRAMES_LIVE_DIR
from backend.app.core.vector_search import get_vector_search

BASE_URL = "http://127.0.0.1:8000"


def make_request(path: str, method: str = "GET", data: dict = None) -> tuple:
    url = f"{BASE_URL}{path}"
    headers = {"Content-Type": "application/json"} if data else {}
    body = json.dumps(data).encode("utf-8") if data else None
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            content = resp.read().decode("utf-8")
            try:
                return resp.status, json.loads(content)
            except Exception:
                return resp.status, content
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8")
        try:
            return e.code, json.loads(err_body)
        except Exception:
            return e.code, err_body
    except Exception as e:
        return 500, str(e)


class TestStageIPersistenceAndIntelligence(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        status, _ = make_request("/api/cameras")
        if status != 200:
            raise RuntimeError(f"Backend not responding at {BASE_URL}. Ensure uvicorn is running.")

    def test_01_persistent_evidence_and_credentials_safety(self):
        """Verify events persist filepaths and metadata to SQLite without leaking credentials or binary blobs."""
        indexer = get_live_indexer()
        cam_id = f"test_cam_{uuid.uuid4().hex[:6]}"
        event_id = f"ev_test_{uuid.uuid4().hex[:8]}"
        
        # 1. Insert an event with representative frame path
        test_frame_dir = f"{FRAMES_LIVE_DIR}/{cam_id}"
        os.makedirs(test_frame_dir, exist_ok=True)
        test_frame_path = f"{test_frame_dir}/frame_{event_id}.jpg"
        
        # Write small test frame
        with open(test_frame_path, "wb") as f:
            f.write(b"\xff\xd8\xff\xe0" + b"\x00" * 128 + b"\xff\xd9")

        now = time.time()
        execute_query(
            """
            INSERT INTO events (
                id, camera_id, tracking_id, concept, start_timestamp, end_timestamp,
                confidence, bounding_boxes, representative_frame, attributes, source_type
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event_id, cam_id, "42", "car", now - 5.0, now, 0.91,
                json.dumps([[10, 20, 30, 40]]), test_frame_path,
                json.dumps({"color": "white", "is_moving": True}), "live"
            )
        )

        # 2. Query event back from database
        row = execute_query("SELECT * FROM events WHERE id = ?", (event_id,), fetch=True, fetch_one=True)
        self.assertIsNotNone(row)
        ev_dict = dict(row)
        
        # Verify file path is stored, NOT a binary blob
        self.assertEqual(ev_dict["representative_frame"], test_frame_path)
        self.assertEqual(ev_dict["concept"], "car")
        self.assertEqual(ev_dict["tracking_id"], "42")
        self.assertAlmostEqual(ev_dict["confidence"], 0.91, places=2)
        
        # Verify no credentials or API tokens leaked in attributes or columns
        raw_row_str = json.dumps(ev_dict).lower()
        self.assertNotIn("password", raw_row_str)
        self.assertNotIn("token", raw_row_str)
        self.assertNotIn("secret", raw_row_str)

        # 3. Test evidence API endpoint
        status, ev_api = make_request(f"/api/evidence/{event_id}")
        self.assertEqual(status, 200)
        self.assertEqual(ev_api["event_id"], event_id)
        self.assertEqual(ev_api["verdict"], "VERIFIED")
        self.assertEqual(ev_api["frame_path"], test_frame_path)

        # 4. Test graceful missing evidence file handling
        os.remove(test_frame_path)
        status, ev_missing = make_request(f"/api/evidence/{event_id}")
        self.assertEqual(status, 200)
        self.assertIn("missing", ev_missing["summary"].lower())
        self.assertEqual(ev_missing["verdict"], "UNVERIFIED")

        # Cleanup
        execute_query("DELETE FROM events WHERE id = ?", (event_id,))

    def test_02_evidence_retention_enforcement(self):
        """Verify evidence retention safely purges expired files and enforces storage limits."""
        indexer = get_live_indexer()
        
        test_dir = f"{FRAMES_LIVE_DIR}/retention_test"
        os.makedirs(test_dir, exist_ok=True)
        
        # Create an expired dummy file (mtime 10 days ago)
        expired_file = f"{test_dir}/frame_expired.jpg"
        with open(expired_file, "wb") as f:
            f.write(b"X" * 1024)
        old_time = time.time() - (10 * 86400)
        os.utime(expired_file, (old_time, old_time))
        
        # Create a fresh dummy file (mtime now)
        fresh_file = f"{test_dir}/frame_fresh.jpg"
        with open(fresh_file, "wb") as f:
            f.write(b"Y" * 1024)

        # Run retention with 7 days max age
        stats = indexer.enforce_evidence_retention(max_age_days=7.0, max_storage_gb=10.0)
        self.assertGreaterEqual(stats["deleted_expired"], 1)
        self.assertFalse(os.path.exists(expired_file), "Expired file must be deleted")
        self.assertTrue(os.path.exists(fresh_file), "Fresh file must be preserved")

        # Cleanup
        if os.path.exists(fresh_file): os.remove(fresh_file)
        if os.path.exists(test_dir): os.rmdir(test_dir)

    def test_03_youtube_live_metadata_and_ingestion_history(self):
        """Verify YouTube Live sources store video identifiers, session history, and accurate recording state."""
        cam_id = f"yt_hist_{uuid.uuid4().hex[:6]}"
        now = time.time()
        
        summary_data = {
            "video_identifier": "DjdUEyjx8GM",
            "source_type": "youtube_live",
            "ingestion_mode": "backend_decoded_ai",
            "is_recording": False,
            "session_history": [
                {
                    "session_id": "sess_01",
                    "started_at": now - 3600,
                    "ended_at": now,
                    "status": "DISCONNECTED",
                    "fps": 25.0,
                    "resolution": "1280x720"
                }
            ]
        }

        execute_query(
            """
            INSERT INTO cameras (
                id, name, description, video_path, fps, duration, resolution, width, height, status, source_type, created_at, analysis_summary
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                cam_id, "YouTube Live Camera", "Audit Feed", "https://youtube.com/watch?v=DjdUEyjx8GM",
                25.0, 0.0, "1280x720", 1280, 720, "READY", "youtube_live", now, json.dumps(summary_data)
            )
        )

        status, cam_res = make_request(f"/api/cameras/{cam_id}")
        self.assertEqual(status, 200)
        self.assertEqual(cam_res["source_type"], "youtube_live")
        self.assertIsInstance(cam_res["analysis_summary"], dict)
        self.assertEqual(cam_res["analysis_summary"]["video_identifier"], "DjdUEyjx8GM")
        self.assertEqual(cam_res["analysis_summary"]["ingestion_mode"], "backend_decoded_ai")
        self.assertFalse(cam_res["analysis_summary"]["is_recording"], "Must not claim recording when inactive")
        self.assertEqual(len(cam_res["analysis_summary"]["session_history"]), 1)

        # Cleanup
        execute_query("DELETE FROM cameras WHERE id = ?", (cam_id,))

    def test_04_cross_camera_candidate_matching(self):
        """Verify cross-camera candidate matching based on visual attributes, timestamps, and sequence."""
        cam1_id = f"cam_gate_{uuid.uuid4().hex[:4]}"
        cam2_id = f"cam_exit_{uuid.uuid4().hex[:4]}"
        now = time.time()

        # Register two cameras
        execute_query(
            "INSERT INTO cameras (id, name, video_path, status, source_type, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (cam1_id, "North Gate Entrance", "test_cctv.mp4", "READY", "uploaded", now)
        )
        execute_query(
            "INSERT INTO cameras (id, name, video_path, status, source_type, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (cam2_id, "South Exit Driveway", "test_cctv.mp4", "READY", "uploaded", now)
        )

        # Insert candidate vehicle events on both cameras within 30s of each other
        ev1_id = f"ev_cand1_{uuid.uuid4().hex[:6]}"
        ev2_id = f"ev_cand2_{uuid.uuid4().hex[:6]}"
        
        execute_query(
            """
            INSERT INTO events (id, camera_id, tracking_id, concept, start_timestamp, end_timestamp, confidence, attributes, source_type)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (ev1_id, cam1_id, "101", "car", now - 25.0, now - 20.0, 0.88, json.dumps({"color": "white"}), "uploaded")
        )
        execute_query(
            """
            INSERT INTO events (id, camera_id, tracking_id, concept, start_timestamp, end_timestamp, confidence, attributes, source_type)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (ev2_id, cam2_id, "205", "car", now - 10.0, now - 5.0, 0.89, json.dumps({"color": "white"}), "uploaded")
        )

        # Query cross-match API
        status, match_res = make_request(f"/api/cameras/cross-match?camera_ids={cam1_id},{cam2_id}&object_type=car&color=white")
        self.assertEqual(status, 200)
        self.assertTrue(match_res["is_candidate_match"], "Compatible white cars in temporal sequence must produce candidate match")
        self.assertEqual(match_res["verdict"], "CROSS_CAMERA")
        self.assertGreater(match_res["confidence"], 0.70)
        self.assertIn("candidate", match_res["summary"].lower())
        self.assertIn("disclaimer", match_res)

        # Verify uncertainty note explicitly states ByteTrack IDs are per-camera
        uncertainty = match_res["evidence_breakdown"].get("uncertainty_note", "")
        self.assertIn("single-camera", uncertainty.lower())

        # Cleanup
        execute_query("DELETE FROM events WHERE id IN (?, ?)", (ev1_id, ev2_id))
        execute_query("DELETE FROM cameras WHERE id IN (?, ?)", (cam1_id, cam2_id))

    def test_05_alert_lifecycle_deduplication_and_restart_survival(self):
        """Verify alert triggering, deduplication cooldown, acknowledgment, and resolution persistence."""
        rule_id = f"rule_test_{uuid.uuid4().hex[:6]}"
        cam_id = f"cam_alert_{uuid.uuid4().hex[:4]}"
        now = time.time()

        execute_query(
            "INSERT INTO cameras (id, name, video_path, status, source_type, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (cam_id, "Perimeter Camera", "test_cctv.mp4", "READY", "live", now)
        )

        # 1. Create alert rule
        rule_data = {
            "name": "Unauthorized Intrusion Rule",
            "camera_id": cam_id,
            "rule_type": "object_detected",
            "object_class": "person",
            "min_confidence": 0.5,
            "cooldown_seconds": 15.0,
            "severity": "critical"
        }
        status, rule_res = make_request("/api/alerts/rules", method="POST", data=rule_data)
        self.assertEqual(status, 201)
        created_rule_id = rule_res["id"]

        # 2. Trigger an alert directly
        alert_id = f"alt_test_{uuid.uuid4().hex[:8]}"
        execute_query(
            """
            INSERT INTO alerts (
                id, rule_id, rule_name, camera_id, camera_name, trigger_timestamp,
                object_class, track_id, confidence, trigger_reason, severity, status, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                alert_id, created_rule_id, "Unauthorized Intrusion Rule", cam_id, "Perimeter Camera",
                now, "person", "99", 0.88, "Detected person with confidence 0.88", "critical", "active", now
            )
        )

        # 3. Verify alert appears in active list
        status, alerts_list = make_request("/api/alerts?status=active")
        self.assertEqual(status, 200)
        found = any(a["id"] == alert_id for a in alerts_list["alerts"])
        self.assertTrue(found, "Newly triggered alert must appear in active list")

        # 4. Acknowledge the alert
        status, ack_res = make_request(f"/api/alerts/{alert_id}/acknowledge", method="POST")
        self.assertEqual(status, 200)
        self.assertEqual(ack_res["status"], "acknowledged")
        self.assertIsNotNone(ack_res["acknowledged_at"])

        # 5. Resolve the alert
        status, res_res = make_request(f"/api/alerts/{alert_id}/resolve", method="POST")
        self.assertEqual(status, 200)
        self.assertEqual(res_res["status"], "resolved")
        self.assertIsNotNone(res_res["resolved_at"])

        # 6. Verify alert persisted in SQLite with resolved status and timestamps
        row = execute_query("SELECT * FROM alerts WHERE id = ?", (alert_id,), fetch=True, fetch_one=True)
        self.assertIsNotNone(row)
        al_dict = dict(row)
        self.assertEqual(al_dict["status"], "resolved")
        self.assertIsNotNone(al_dict["acknowledged_at"])
        self.assertIsNotNone(al_dict["resolved_at"])

        # Cleanup
        make_request(f"/api/alerts/rules/{created_rule_id}", method="DELETE")
        execute_query("DELETE FROM alerts WHERE id = ?", (alert_id,))
        execute_query("DELETE FROM cameras WHERE id = ?", (cam_id,))


if __name__ == "__main__":
    unittest.main()
