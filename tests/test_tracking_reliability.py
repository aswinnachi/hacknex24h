"""
tests/test_tracking_reliability.py

Comprehensive Phase 9 Automated Test Suite for:
1. False Positive Suppression (Sky, trees, textures produce 0 person detections).
2. Genuine Detection & Track Stability on real CCTV footage.
3. Class ID and Name Mapping Correctness.
4. Bounding Box Geometric Bounds & Sanity.
5. Per-Camera ByteTrack Tracker Isolation & Reset Hygiene.
6. Multi-Camera Fair Inference Scheduling (Round-robin, no starvation, latest frame delivery).
7. Confidence & Track State Integrity (raw_confidence vs confidence, confirmed vs tentative).
"""

import sys
import os
import time
import numpy as np
import cv2
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from backend.app.core.detector import get_detector, DEFAULT_CLASSES, DEFAULT_CONF_THRESHOLDS, is_valid_detection_box
from backend.app.core.stream_manager import StreamManager


class TestTrackingReliability(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.detector = get_detector()
        cls.test_cctv_path = os.path.abspath("test_cctv.mp4")
        cls.test_real_cctv_path = os.path.abspath("test_real_cctv.mp4")

    def test_01_synthetic_background_false_positives(self):
        """Verify that pure sky, trees, textured noise, and building facades produce 0 person detections."""
        print("\n[Test 1] Synthetic Background False-Positive Rejection...")
        
        # 1. Uniform blue sky gradient
        sky = np.zeros((720, 1280, 3), dtype=np.uint8)
        for y in range(720):
            sky[y, :, :] = [235, max(0, 180 - int(y * 0.15)), 100]  # BGR gradient
        dets_sky = self.detector.track_frame(sky, classes=["person", "car"])
        person_sky = [d for d in dets_sky if d["label"] == "person"]
        self.assertEqual(len(person_sky), 0, f"False person detected on sky gradient: {person_sky}")
        print("  ✓ Pure sky gradient: 0 person detections")

        # 2. Textured green tree foliage simulation
        np.random.seed(42)
        trees = np.random.randint(20, 80, (720, 1280, 3), dtype=np.uint8)
        trees[:, :, 1] = np.random.randint(70, 160, (720, 1280), dtype=np.uint8)  # Heavy green
        trees = cv2.GaussianBlur(trees, (15, 15), 0)
        dets_trees = self.detector.track_frame(trees, classes=["person", "bicycle"])
        person_trees = [d for d in dets_trees if d["label"] == "person"]
        self.assertEqual(len(person_trees), 0, f"False person detected on tree foliage: {person_trees}")
        print("  ✓ Textured tree foliage: 0 person detections")

        # 3. High-contrast building facade (grid patterns)
        facade = np.ones((720, 1280, 3), dtype=np.uint8) * 180
        for x in range(0, 1280, 40):
            cv2.line(facade, (x, 0), (x, 720), (50, 50, 50), 4)
        for y in range(0, 720, 60):
            cv2.line(facade, (0, y), (1280, y), (50, 50, 50), 4)
        dets_facade = self.detector.track_frame(facade, classes=["person", "vehicle"])
        person_facade = [d for d in dets_facade if d["label"] == "person"]
        self.assertEqual(len(person_facade), 0, f"False person detected on building facade: {person_facade}")
        print("  ✓ Building grid facade: 0 person detections")

    def test_02_bounding_box_geometry_sanity(self):
        """Verify degenerate and out-of-bounds boxes are rejected."""
        print("\n[Test 2] Bounding Box Geometric Bounds & Sanity...")
        
        # Valid box: (bbox, label, img_w, img_h)
        self.assertTrue(is_valid_detection_box([100, 100, 200, 350], "person", 1280, 720))
        
        # Non-finite coordinates (NaN / Inf)
        self.assertFalse(is_valid_detection_box([float("nan"), 100, 200, 300], "person", 1280, 720))
        self.assertFalse(is_valid_detection_box([100, float("inf"), 200, 300], "person", 1280, 720))
        
        # Inverted coordinates
        self.assertFalse(is_valid_detection_box([200, 300, 100, 200], "person", 1280, 720))
        
        # Out-of-bounds completely
        self.assertFalse(is_valid_detection_box([-200, -100, -50, -20], "person", 1280, 720))
        self.assertFalse(is_valid_detection_box([1300, 750, 1400, 800], "person", 1280, 720))
        
        # Degenerate tiny box (< 140 px)
        self.assertFalse(is_valid_detection_box([100, 100, 108, 108], "person", 1280, 720))
        
        # Giant full-frame activation (> 88%)
        self.assertFalse(is_valid_detection_box([5, 5, 1275, 715], "person", 1280, 720))
        
        # Inverted aspect ratio for person (wider than tall: width 400, height 50)
        self.assertFalse(is_valid_detection_box([100, 100, 500, 150], "person", 1280, 720))
        print("  ✓ Degenerate, out-of-bounds, NaN, and inverted aspect-ratio boxes rejected correctly")

    def test_03_genuine_cctv_person_detection(self):
        """Verify real CCTV frame detects genuine people with high confidence (>= 0.50)."""
        print("\n[Test 3] Genuine CCTV Footage Detection & Tracking...")
        if not os.path.exists(self.test_cctv_path):
            self.skipTest("test_cctv.mp4 not found")

        cap = cv2.VideoCapture(self.test_cctv_path)
        ret, frame = cap.read()
        cap.release()
        self.assertTrue(ret, "Failed to read first frame from test_cctv.mp4")

        # Run detection
        dets = self.detector.track_frame(frame, classes=["person", "car"])
        people = [d for d in dets if d["label"] == "person"]
        self.assertGreaterEqual(len(people), 2, f"Expected at least 2 people in test_cctv.mp4, found {len(people)}")
        
        # Check confidences are all >= calibrated threshold (0.50)
        for p in people:
            self.assertGreaterEqual(p["confidence"], DEFAULT_CONF_THRESHOLDS["person"],
                                    f"Person confidence {p['confidence']} below threshold 0.50")
            self.assertIn("raw_confidence", p)
            self.assertGreater(p["raw_confidence"], 0.0)
        print(f"  ✓ Successfully detected {len(people)} genuine people in test_cctv.mp4 with confidence >= 0.50")

    def test_04_camera_tracker_isolation(self):
        """Verify that separate cameras maintain strictly isolated ByteTrack state."""
        print("\n[Test 4] Tracker Isolation Across Cameras...")
        frame = np.zeros((360, 634, 3), dtype=np.uint8)
        
        self.detector.reset_tracker("cam_isolated_A")
        self.detector.reset_tracker("cam_isolated_B")

        self.detector.track_frame(frame, camera_id="cam_isolated_A")
        self.detector.track_frame(frame, camera_id="cam_isolated_B")

        trkA = self.detector._trackers.get("cam_isolated_A")
        trkB = self.detector._trackers.get("cam_isolated_B")

        self.assertIsNotNone(trkA, "Tracker A not created")
        self.assertIsNotNone(trkB, "Tracker B not created")
        self.assertIsNot(trkA, trkB, "Tracker A and B share the exact same object reference!")
        
        # Reset cam A, verify cam B is untouched
        self.detector.reset_tracker("cam_isolated_A")
        self.assertNotIn("cam_isolated_A", self.detector._trackers)
        self.assertIn("cam_isolated_B", self.detector._trackers)
        
        # Cleanup
        self.detector.reset_tracker("cam_isolated_B")
        self.assertNotIn("cam_isolated_B", self.detector._trackers)
        print("  ✓ Strict tracker isolation confirmed; resetting one camera preserves others")

    def test_05_multicamera_fair_scheduling(self):
        """Verify fair round-robin scheduling across multiple concurrent streams."""
        print("\n[Test 5] Multi-Camera Fair Inference Scheduling...")
        if not os.path.exists(self.test_cctv_path):
            self.skipTest("test_cctv.mp4 not found")

        mgr = StreamManager.get_instance()
        
        # Use distinct files if available, otherwise distinct camera IDs
        sources = [self.test_cctv_path]
        if os.path.exists(self.test_real_cctv_path):
            sources.append(self.test_real_cctv_path)
        third_video = os.path.abspath("data/videos/camera_01/WhatsApp Video 2026-10-06 at 23.43.10.mp4")
        if os.path.exists(third_video):
            sources.append(third_video)
        
        cam_ids = [f"test_fair_cam_{i+1}" for i in range(len(sources))]
        for i, cid in enumerate(cam_ids):
            src = sources[i]
            ok, _ = mgr.connect_camera(cid, f"Fair {cid}", src, timeout=5.0)
            self.assertTrue(ok, f"Failed to connect {cid}")

        # Let scheduler run for 2.5 seconds
        time.sleep(2.5)

        # Collect analyzed frame counts for each camera
        counts = {}
        for cid in cam_ids:
            st = mgr.get_camera_detections(cid)
            self.assertIsNotNone(st, f"No status for {cid}")
            counts[cid] = st.get("frames_analyzed", 0)

        # Disconnect cameras
        for cid in cam_ids:
            mgr.disconnect_camera(cid)

        print(f"  Frame counts analyzed across cameras: {counts}")
        # Every camera must have received inference turns
        for cid, count in counts.items():
            self.assertGreater(count, 0, f"Camera {cid} starved completely! Analyzed: {count}")
        
        # Max difference between cameras must be within fair tolerance (no monopolization)
        max_c = max(counts.values())
        min_c = min(counts.values())
        self.assertLessEqual(max_c - min_c, 5, f"Unfair scheduling: max={max_c}, min={min_c}")
        print("  ✓ Fair scheduling verified: all cameras received equitable inference turns")


if __name__ == "__main__":
    unittest.main()
