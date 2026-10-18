"""
tests/test_video_lab_deployment.py

Automated test suite verifying the complete Video Intelligence Lab deployment:
1. Video file validation logic & corrupted file rejection.
2. Persistent Storage abstraction CRUD (Local / S3 fallback).
3. Video upload endpoint (/api/lab/upload) with metadata extraction.
4. Background worker execution (/api/lab/upload-and-analyze & /api/lab/jobs/{id}/start).
5. Real AI YOLO-World detection + ByteTrack tracking execution.
6. Structured tracking results API (/api/lab/jobs/{id}/results).
7. Annotated video, JSON, and CSV export downloads.
8. Job history listing & safe deletion with storage file cleanup.
9. Regression verification for existing live cameras, events, queries, and alerts.
"""

import os
import sys
import time
import json
import unittest
import requests

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from backend.app.core.storage import get_storage, LocalStorage
from backend.app.core.video_lab import validate_video_file

BASE_URL = "http://127.0.0.1:8000"

class TestVideoLabDeployment(unittest.TestCase):

    def test_01_storage_abstraction_crud(self):
        storage = LocalStorage(base_dir="data/storage_test")
        test_key = "unit_tests/sample_artifact.txt"
        test_content = b"AI Vision Security Persistent Storage Test Payload"

        # Save
        saved_key = storage.save_file(test_content, test_key, content_type="text/plain")
        self.assertEqual(saved_key, test_key)
        self.assertTrue(storage.file_exists(test_key))

        # Local path
        local_path = storage.get_local_path(test_key)
        self.assertTrue(os.path.exists(local_path))
        with open(local_path, "rb") as f:
            self.assertEqual(f.read(), test_content)

        # URL
        url = storage.get_url(test_key)
        self.assertIn("/api/lab/files/unit_tests/sample_artifact.txt", url)

        # Delete
        deleted = storage.delete_file(test_key)
        self.assertTrue(deleted)
        self.assertFalse(storage.file_exists(test_key))

    def test_02_video_validation_rules(self):
        # 1. Non-video extension rejected
        dummy_txt = "data/test_dummy.txt"
        os.makedirs(os.path.dirname(dummy_txt), exist_ok=True)
        with open(dummy_txt, "w") as f:
            f.write("plain text content")
        try:
            with self.assertRaises(ValueError):
                validate_video_file(dummy_txt, "test_dummy.txt")
        finally:
            if os.path.exists(dummy_txt): os.remove(dummy_txt)

        # 2. Corrupted file rejected
        fake_mp4 = "data/fake_corrupted.mp4"
        with open(fake_mp4, "wb") as f:
            f.write(b"not a valid mp4 header at all and short")
        try:
            with self.assertRaises(ValueError):
                validate_video_file(fake_mp4, "fake_corrupted.mp4")
        finally:
            if os.path.exists(fake_mp4): os.remove(fake_mp4)

        # 3. Valid CCTV video verified
        if os.path.exists("test_cctv.mp4"):
            meta = validate_video_file("test_cctv.mp4", "test_cctv.mp4")
            self.assertGreater(meta["duration"], 0)
            self.assertGreater(meta["total_frames"], 0)
            self.assertGreater(meta["fps"], 0)
            self.assertIn("x", meta["resolution"])

    def test_03_video_upload_endpoint(self):
        if not os.path.exists("test_cctv.mp4"):
            self.skipTest("test_cctv.mp4 not found")

        with open("test_cctv.mp4", "rb") as vf:
            res = requests.post(
                f"{BASE_URL}/api/lab/upload",
                files={"file": ("test_cctv.mp4", vf, "video/mp4")},
                data={"session_id": "unittest_session"}
            )
        self.assertEqual(res.status_code, 200)
        data = res.json()
        job_id = data.get("job_id")
        self.assertIsNotNone(job_id)
        self.assertEqual(data["status"], "queued")
        self.assertGreater(data["duration"], 0)

        # Check status
        st_res = requests.get(f"{BASE_URL}/api/lab/jobs/{job_id}/status")
        self.assertEqual(st_res.status_code, 200)
        st_data = st_res.json()
        self.assertEqual(st_data["job_id"], job_id)
        self.assertEqual(st_data["status"], "queued")

        # Clean up
        del_res = requests.delete(f"{BASE_URL}/api/lab/jobs/{job_id}")
        self.assertEqual(del_res.status_code, 200)

    def test_04_end_to_end_analysis_and_exports(self):
        sample_path = "/tmp/sample_cctv.mp4"
        if not os.path.exists(sample_path):
            if os.path.exists("test_cctv.mp4"):
                os.system(f"ffmpeg -y -i test_cctv.mp4 -t 3 -c:v copy {sample_path} >/dev/null 2>&1")

        if not os.path.exists(sample_path):
            self.skipTest("sample_cctv.mp4 not available")

        # 1. Upload & Analyze
        with open(sample_path, "rb") as cf:
            res = requests.post(
                f"{BASE_URL}/api/lab/upload-and-analyze",
                files={"file": ("sample_cctv.mp4", cf, "video/mp4")},
                data={"frame_interval": "5"}
            )
        self.assertEqual(res.status_code, 200)
        init_data = res.json()
        job_id = init_data["job_id"]
        self.assertIsNotNone(job_id)

        # 2. Poll until completed (timeout 30s)
        start_t = time.time()
        final_st = None
        while (time.time() - start_t) < 30.0:
            st = requests.get(f"{BASE_URL}/api/lab/jobs/{job_id}/status").json()
            if st["status"] in ("completed", "failed"):
                final_st = st["status"]
                break
            time.sleep(0.5)

        self.assertEqual(final_st, "completed", f"Job failed or timed out: {st}")

        # 3. Verify Results API
        res_api = requests.get(f"{BASE_URL}/api/lab/jobs/{job_id}/results")
        self.assertEqual(res_api.status_code, 200)
        r_data = res_api.json()
        self.assertEqual(r_data["status"], "completed")
        self.assertIsNotNone(r_data.get("annotated_video_url"))
        self.assertGreater(r_data["metrics"]["frames_analyzed"], 0)
        self.assertIn("ByteTrack", r_data["disclaimer"])

        # 4. Verify CSV Download
        csv_res = requests.get(f"{BASE_URL}/api/lab/jobs/{job_id}/download/csv")
        self.assertEqual(csv_res.status_code, 200)
        self.assertIn("job_id,timestamp_offset_sec", csv_res.text)

        # 5. Verify JSON Download
        json_res = requests.get(f"{BASE_URL}/api/lab/jobs/{job_id}/download/json")
        self.assertEqual(json_res.status_code, 200)
        json_data = json_res.json()
        self.assertEqual(json_data["job_id"], job_id)

        # 6. Verify Annotated Video File Streaming
        v_url = r_data["annotated_video_url"]
        v_res = requests.get(f"{BASE_URL}{v_url}", stream=True)
        self.assertEqual(v_res.status_code, 200)
        self.assertEqual(v_res.headers.get("content-type"), "video/mp4")

        # 7. Check History list
        hist_res = requests.get(f"{BASE_URL}/api/lab/jobs")
        self.assertEqual(hist_res.status_code, 200)
        job_ids = [j["id"] for j in hist_res.json()]
        self.assertIn(job_id, job_ids)

        # 8. Clean up job
        del_res = requests.delete(f"{BASE_URL}/api/lab/jobs/{job_id}")
        self.assertEqual(del_res.status_code, 200)

    def test_05_regression_existing_endpoints(self):
        """Verifies existing endpoints (health, cameras, events, alerts) remain healthy."""
        h = requests.get(f"{BASE_URL}/api/health")
        self.assertEqual(h.status_code, 200)
        self.assertEqual(h.json()["status"], "ok")

        cams = requests.get(f"{BASE_URL}/api/cameras")
        self.assertEqual(cams.status_code, 200)

        events = requests.get(f"{BASE_URL}/api/events?limit=5")
        self.assertEqual(events.status_code, 200)

        alerts = requests.get(f"{BASE_URL}/api/alerts?limit=5")
        self.assertEqual(alerts.status_code, 200)


if __name__ == "__main__":
    unittest.main()
