import unittest
import os
import sys
import time
import uuid
import json

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from backend.app.core.vector_search import (
    is_candidate_grounded,
    validate_candidate_event,
    get_vector_search
)
from backend.app.core.evidence import (
    are_labels_compatible,
    generate_evidence_clip
)
from backend.app.api.query import execute_natural_language_query, QueryRequest
from backend.app.db.database import execute_query


class TestCorrectnessSuite(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.vector_search = get_vector_search()

    # =========================================================================
    # 1. Cars versus Buses Test
    # =========================================================================
    def test_cars_versus_buses(self):
        """
        Verify that exact object-class filtering prevents car queries from matching buses
        and vice-versa, even under semantic vector similarity.
        """
        bus_attrs = {"color": "red", "is_moving": True, "action": "moving"}
        car_attrs = {"color": "red", "is_moving": True, "action": "moving"}
        truck_attrs = {"color": "white", "is_moving": True, "action": "moving"}

        # 1. Car query against bus candidate
        is_valid, reason = is_candidate_grounded(
            concept="bus",
            attributes=bus_attrs,
            required_object="car"
        )
        self.assertFalse(is_valid, "Car query must NOT match a bus candidate")
        self.assertIn("not a car", reason.lower())

        # 2. Car query against truck candidate
        is_valid_truck, reason_truck = is_candidate_grounded(
            concept="truck",
            attributes=truck_attrs,
            required_object="car"
        )
        self.assertFalse(is_valid_truck, "Car query must NOT match a truck candidate")
        self.assertIn("not a car", reason_truck.lower())

        # 3. Bus query against car candidate
        is_valid_bus, reason_bus = is_candidate_grounded(
            concept="car",
            attributes=car_attrs,
            required_object="bus"
        )
        self.assertFalse(is_valid_bus, "Bus query must NOT match a car candidate")
        self.assertIn("not a bus", reason_bus.lower())

        # 4. Bus query against bus candidate (positive control)
        is_valid_pos, _ = is_candidate_grounded(
            concept="bus",
            attributes=bus_attrs,
            required_object="bus"
        )
        self.assertTrue(is_valid_pos, "Bus query must match a bus candidate")

        # 5. Label compatibility check in evidence engine
        self.assertFalse(are_labels_compatible("car", "bus"), "Evidence engine must not treat car and bus as compatible")
        self.assertFalse(are_labels_compatible("car", "truck"), "Evidence engine must not treat car and truck as compatible")

    # =========================================================================
    # 2. Bicycles versus Cars Test
    # =========================================================================
    def test_bicycles_versus_cars(self):
        """
        Verify that bicycle queries do not match cars or motorcycles, and car
        queries do not match bicycles.
        """
        bicycle_attrs = {"color": "black", "is_moving": True, "action": "riding"}
        car_attrs = {"color": "black", "is_moving": True, "action": "moving"}
        moto_attrs = {"color": "black", "is_moving": True, "action": "riding"}

        # 1. Bicycle query against car candidate
        is_valid, reason = is_candidate_grounded(
            concept="car",
            attributes=car_attrs,
            required_object="bicycle"
        )
        self.assertFalse(is_valid, "Bicycle query must NOT match a car candidate")
        self.assertIn("not a bicycle", reason.lower())

        # 2. Bicycle query against motorcycle candidate
        is_valid_m, reason_m = is_candidate_grounded(
            concept="motorcycle",
            attributes=moto_attrs,
            required_object="bicycle"
        )
        self.assertFalse(is_valid_m, "Bicycle query must NOT match a motorcycle candidate")
        self.assertIn("not a bicycle", reason_m.lower())

        # 3. Car query against bicycle candidate
        is_valid_c, reason_c = is_candidate_grounded(
            concept="bicycle",
            attributes=bicycle_attrs,
            required_object="car"
        )
        self.assertFalse(is_valid_c, "Car query must NOT match a bicycle candidate")
        self.assertIn("not a car", reason_c.lower())

        # 4. Bicycle query against bicycle candidate (positive control)
        is_valid_pos, _ = is_candidate_grounded(
            concept="bicycle",
            attributes=bicycle_attrs,
            required_object="bicycle"
        )
        self.assertTrue(is_valid_pos, "Bicycle query must match a bicycle candidate")

        # 5. Label compatibility check in evidence engine
        self.assertFalse(are_labels_compatible("bicycle", "car"), "Evidence engine must not equate bicycle and car")
        self.assertFalse(are_labels_compatible("bicycle", "motorcycle"), "Evidence engine must not equate bicycle and motorcycle")

    # =========================================================================
    # 3. Unique-Track Counting Test
    # =========================================================================
    def test_unique_track_counting(self):
        """
        Verify that counting filters by canonical object class, camera, and dataset,
        and accurately counts distinct tracking IDs rather than multi-frame occurrences.
        """
        test_cam_id = f"test_cam_{uuid.uuid4().hex[:6]}"
        test_dataset_id = f"ds_{uuid.uuid4().hex[:8]}"

        # Insert temporary camera
        execute_query(
            """
            INSERT INTO cameras (id, name, video_path, fps, duration, status, dataset_id, created_at)
            VALUES (?, ?, ?, ?, ?, 'READY', ?, ?)
            """,
            (test_cam_id, "Counting Test Camera", "test_cctv.mp4", 12.0, 15.0, test_dataset_id, time.time())
        )

        test_event_ids = []
        try:
            # Insert 3 events for Track 101 (car) across different timestamps
            for t in [1.0, 2.0, 3.0]:
                eid = str(uuid.uuid4())
                test_event_ids.append(eid)
                execute_query(
                    """
                    INSERT INTO events (id, camera_id, dataset_id, tracking_id, concept, start_timestamp, end_timestamp, confidence, bounding_boxes, representative_frame, attributes)
                    VALUES (?, ?, ?, '101', 'car', ?, ?, 0.9, '[[10, 10, 50, 50]]', 'data/frames/test.jpg', '{"color": "white", "is_moving": true}')
                    """,
                    (eid, test_cam_id, test_dataset_id, t, t + 0.5)
                )

            # Insert 2 events for Track 102 (car)
            for t in [4.0, 5.0]:
                eid = str(uuid.uuid4())
                test_event_ids.append(eid)
                execute_query(
                    """
                    INSERT INTO events (id, camera_id, dataset_id, tracking_id, concept, start_timestamp, end_timestamp, confidence, bounding_boxes, representative_frame, attributes)
                    VALUES (?, ?, ?, '102', 'car', ?, ?, 0.88, '[[60, 60, 100, 100]]', 'data/frames/test.jpg', '{"color": "white", "is_moving": true}')
                    """,
                    (eid, test_cam_id, test_dataset_id, t, t + 0.5)
                )

            # Insert 2 events for Track 201 (bus) - must be excluded from car counts
            for t in [2.0, 3.0]:
                eid = str(uuid.uuid4())
                test_event_ids.append(eid)
                execute_query(
                    """
                    INSERT INTO events (id, camera_id, dataset_id, tracking_id, concept, start_timestamp, end_timestamp, confidence, bounding_boxes, representative_frame, attributes)
                    VALUES (?, ?, ?, '201', 'bus', ?, ?, 0.92, '[[150, 50, 250, 150]]', 'data/frames/test.jpg', '{"color": "yellow", "is_moving": true}')
                    """,
                    (eid, test_cam_id, test_dataset_id, t, t + 1.0)
                )

            # Execute query_count for cars
            car_count_res = self.vector_search.query_count(
                required_object="car",
                camera_id=test_cam_id
            )
            # Must return 2 distinct car tracks (101, 102), not 5 detections, and must exclude bus
            self.assertEqual(car_count_res["count"], 2, f"Expected 2 unique car tracks, got {car_count_res['count']}")
            self.assertEqual(set(car_count_res["unique_tracking_ids"]), {"101", "102"})

            # Execute query_count for buses
            bus_count_res = self.vector_search.query_count(
                required_object="bus",
                camera_id=test_cam_id
            )
            self.assertEqual(bus_count_res["count"], 1, f"Expected 1 unique bus track, got {bus_count_res['count']}")
            self.assertEqual(bus_count_res["unique_tracking_ids"], ["201"])

            # Execute query_count for bicycles (zero ground-truth)
            bike_count_res = self.vector_search.query_count(
                required_object="bicycle",
                camera_id=test_cam_id
            )
            self.assertEqual(bike_count_res["count"], 0, f"Expected 0 bicycles, got {bike_count_res['count']}")

        finally:
            # Clean up test camera and test events
            execute_query("DELETE FROM events WHERE camera_id = ?", (test_cam_id,))
            execute_query("DELETE FROM cameras WHERE id = ?", (test_cam_id,))

    # =========================================================================
    # 4. Dataset Isolation Test
    # =========================================================================
    def test_dataset_isolation(self):
        """
        Verify that events from old uploads or other sessions (different dataset_id)
        are strictly isolated and never appear in current results.
        """
        test_cam_id = f"test_cam_{uuid.uuid4().hex[:6]}"
        active_dataset_id = f"active_ds_{uuid.uuid4().hex[:8]}"
        stale_dataset_id = f"stale_ds_{uuid.uuid4().hex[:8]}"

        # Camera is configured with active_dataset_id
        execute_query(
            """
            INSERT INTO cameras (id, name, video_path, fps, duration, status, dataset_id, created_at)
            VALUES (?, ?, ?, 12.0, 10.0, 'READY', ?, ?)
            """,
            (test_cam_id, "Isolation Test Camera", "test_cctv.mp4", active_dataset_id, time.time())
        )

        stale_event_id = str(uuid.uuid4())
        active_event_id = str(uuid.uuid4())
        try:
            # Insert event with old/stale dataset_id
            execute_query(
                """
                INSERT INTO events (id, camera_id, dataset_id, tracking_id, concept, start_timestamp, end_timestamp, confidence, bounding_boxes, representative_frame, attributes)
                VALUES (?, ?, ?, 'stale_99', 'person', 1.0, 2.0, 0.9, '[[10, 10, 50, 50]]', 'test.jpg', '{"color": "red"}')
                """,
                (stale_event_id, test_cam_id, stale_dataset_id)
            )

            # Insert event with active dataset_id
            execute_query(
                """
                INSERT INTO events (id, camera_id, dataset_id, tracking_id, concept, start_timestamp, end_timestamp, confidence, bounding_boxes, representative_frame, attributes)
                VALUES (?, ?, ?, 'active_01', 'person', 1.0, 2.0, 0.9, '[[10, 10, 50, 50]]', 'test.jpg', '{"color": "red"}')
                """,
                (active_event_id, test_cam_id, active_dataset_id)
            )

            # 1. Test validate_candidate_event directly
            stale_dict = {"id": stale_event_id, "camera_id": test_cam_id, "dataset_id": stale_dataset_id, "start_timestamp": 1.0, "end_timestamp": 2.0}
            active_dict = {"id": active_event_id, "camera_id": test_cam_id, "dataset_id": active_dataset_id, "start_timestamp": 1.0, "end_timestamp": 2.0}

            is_valid_stale, err_stale = validate_candidate_event(stale_dict, camera_id=test_cam_id)
            self.assertFalse(is_valid_stale, "Stale dataset event must fail validation")
            self.assertIn("stale", err_stale.lower())

            is_valid_active, _ = validate_candidate_event(active_dict, camera_id=test_cam_id)
            self.assertTrue(is_valid_active, "Active dataset event must pass validation")

            # 2. Test query_count excludes stale dataset events
            count_res = self.vector_search.query_count(
                required_object="person",
                camera_id=test_cam_id
            )
            self.assertEqual(count_res["count"], 1, "Only active dataset event should be counted")
            self.assertEqual(count_res["unique_tracking_ids"], ["active_01"])

        finally:
            execute_query("DELETE FROM events WHERE camera_id = ?", (test_cam_id,))
            execute_query("DELETE FROM cameras WHERE id = ?", (test_cam_id,))

    # =========================================================================
    # 5. Timestamp and Evidence Consistency Test
    # =========================================================================
    def test_timestamp_and_evidence_consistency(self):
        """
        Verify that evidence payload fields (camera, timestamp, event, clip boundaries,
        and target tracking ID) strictly refer to the same event.
        """
        test_cam_id = f"test_cam_{uuid.uuid4().hex[:6]}"
        test_dataset_id = f"ds_{uuid.uuid4().hex[:8]}"
        test_event_id = str(uuid.uuid4())

        execute_query(
            """
            INSERT INTO cameras (id, name, video_path, fps, duration, status, dataset_id, width, height, created_at)
            VALUES (?, ?, 'test_cctv.mp4', 12.0, 15.0, 'READY', ?, 768, 432, ?)
            """,
            (test_cam_id, "Evidence Consistency Cam", test_dataset_id, time.time())
        )

        try:
            execute_query(
                """
                INSERT INTO events (id, camera_id, dataset_id, tracking_id, concept, start_timestamp, end_timestamp, confidence, bounding_boxes, representative_frame, attributes)
                VALUES (?, ?, ?, '42', 'car', 4.5, 6.0, 0.95, '[[100, 200, 300, 400]]', 'data/frames/test.jpg', '{"color": "white", "is_moving": true}')
                """,
                (test_event_id, test_cam_id, test_dataset_id)
            )

            cam_clips_dir = os.path.join("data/clips", test_cam_id)
            os.makedirs(cam_clips_dir, exist_ok=True)
            clip_filename = f"{cam_clips_dir}/clip_{test_event_id}.mp4"
            cache_path = f"{clip_filename}.scene.json"
            dummy_scene = [{
                "event_id": "clip_track_42",
                "tracking_id": "42",
                "concept": "car",
                "color": "white",
                "clothing": "",
                "has_bag": False,
                "is_moving": True,
                "confidence": 0.95,
                "start_frame": 18,
                "end_frame": 36,
                "bounding_boxes": [[100.0, 200.0, 300.0, 400.0]],
                "is_target": False
            }]
            with open(cache_path, "w") as f:
                json.dump(dummy_scene, f)

            evidence = generate_evidence_clip(test_event_id)
            self.assertIsNotNone(evidence, "Evidence generation must return a payload")

            # Consistency assertions:
            self.assertEqual(evidence["event_id"], test_event_id, "Evidence event_id must match requested event")
            self.assertEqual(evidence["camera_id"], test_cam_id, "Evidence camera_id must match event camera")
            self.assertEqual(evidence["tracking_id"], "42", "Evidence tracking_id must match event tracking_id")
            self.assertEqual(evidence["concept"], "car", "Evidence concept must match event concept")
            self.assertAlmostEqual(evidence["timestamp"], 4.5, delta=0.01, msg="Timestamp must match start_timestamp")

            # Clip boundaries must bracket the event timestamp
            self.assertLessEqual(evidence["clip_start"], evidence["timestamp"], "clip_start must be <= event timestamp")
            self.assertGreaterEqual(evidence["clip_end"], 6.0, "clip_end must be >= event end_timestamp")

            # Scene objects target consistency
            scene_objects = evidence.get("scene_objects", [])
            target_objects = [o for o in scene_objects if o.get("is_target")]
            self.assertEqual(len(target_objects), 1, "Exactly one object must be designated as the target")
            target = target_objects[0]
            self.assertEqual(str(target.get("tracking_id")), "42", "Target object tracking_id must match event tracking_id")
            self.assertEqual(target.get("concept"), "car", "Target object concept must match event concept")

        finally:
            execute_query("DELETE FROM events WHERE camera_id = ?", (test_cam_id,))
            execute_query("DELETE FROM cameras WHERE id = ?", (test_cam_id,))
            import shutil
            cam_clips_dir = os.path.join("data/clips", test_cam_id)
            if os.path.exists(cam_clips_dir):
                shutil.rmtree(cam_clips_dir, ignore_errors=True)

    # =========================================================================
    # 6. Queries That Should Return No Match Test
    # =========================================================================
    def test_queries_returning_no_match(self):
        """
        Verify that queries for non-existent objects, missing attributes, or absent
        secondary objects return NO MATCH (or NO) with zero matches.
        """
        test_cam_id = f"test_cam_{uuid.uuid4().hex[:6]}"
        test_dataset_id = f"ds_{uuid.uuid4().hex[:8]}"

        execute_query(
            """
            INSERT INTO cameras (id, name, video_path, fps, duration, status, dataset_id, created_at)
            VALUES (?, ?, ?, ?, ?, 'READY', ?, ?)
            """,
            (test_cam_id, "Negative Query Test Camera", "test_cctv.mp4", 12.0, 15.0, test_dataset_id, time.time())
        )

        # Insert candidate white car and person in blue shirt
        execute_query(
            """
            INSERT INTO events (id, camera_id, dataset_id, tracking_id, concept, start_timestamp, end_timestamp, confidence, representative_frame, attributes)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(uuid.uuid4()), test_cam_id, test_dataset_id, "101", "car",
                2.0, 5.0, 0.92, "data/frames/test_car.jpg",
                json.dumps({"color": "white", "is_moving": True, "object_type": "car"})
            )
        )
        execute_query(
            """
            INSERT INTO events (id, camera_id, dataset_id, tracking_id, concept, start_timestamp, end_timestamp, confidence, representative_frame, attributes)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(uuid.uuid4()), test_cam_id, test_dataset_id, "102", "person",
                3.0, 6.0, 0.90, "data/frames/test_person.jpg",
                json.dumps({"color": "blue", "upper_color": "blue", "lower_color": "black", "is_moving": True, "object_type": "person"})
            )
        )

        try:
            negative_cases = [
                # 1. Non-existent open-vocabulary object
                ("Show the submarine", "NO MATCH"),
                ("Show the airplane", "NO MATCH"),

                # 2. Non-existent color for candidate object
                ("Show the purple car", "NO MATCH"),
                ("Show the pink bicycle", "NO MATCH"),

                # 3. Missing accessory
                ("Show a person with an umbrella", "NO MATCH"),

                # 4. Yes/No negative query
                ("Did a purple car pass?", "NO"),
                ("Is there a submarine?", "NO"),
            ]

            for query_text, expected_verdict in negative_cases:
                req = QueryRequest(query=query_text, camera_id=test_cam_id)
                res = execute_natural_language_query(req)
                self.assertEqual(
                    res["verdict"], expected_verdict,
                    f"Query '{query_text}' expected verdict '{expected_verdict}', got '{res['verdict']}'"
                )
                self.assertEqual(
                    res["total_matches"], 0,
                    f"Query '{query_text}' must have 0 total matches, got {res['total_matches']}"
                )
                self.assertEqual(len(res["results"]), 0)
        finally:
            execute_query("DELETE FROM events WHERE camera_id = ?", (test_cam_id,))
            execute_query("DELETE FROM cameras WHERE id = ?", (test_cam_id,))

    # =========================================================================
    # 7. Honest Uncertainty Test
    # =========================================================================
    def test_honest_uncertainty_when_evidence_missing(self):
        """
        Verify that candidates with missing visual evidence files on disk are NOT labeled
        verified, returning UNVERIFIED or being filtered out.
        """
        test_cam_id = f"test_cam_{uuid.uuid4().hex[:6]}"
        test_dataset_id = f"ds_{uuid.uuid4().hex[:8]}"
        test_event_id = str(uuid.uuid4())

        # Camera with non-existent video path
        execute_query(
            """
            INSERT INTO cameras (id, name, video_path, fps, duration, status, dataset_id, created_at)
            VALUES (?, ?, 'non_existent_video_path.mp4', 12.0, 10.0, 'READY', ?, ?)
            """,
            (test_cam_id, "Missing Evidence Cam", test_dataset_id, time.time())
        )

        try:
            # Event with non-existent frame path
            execute_query(
                """
                INSERT INTO events (id, camera_id, dataset_id, tracking_id, concept, start_timestamp, end_timestamp, confidence, bounding_boxes, representative_frame, attributes)
                VALUES (?, ?, ?, '55', 'car', 2.0, 3.0, 0.9, '[[10, 10, 50, 50]]', 'non_existent_frame.jpg', '{"color": "white"}')
                """,
                (test_event_id, test_cam_id, test_dataset_id)
            )

            # 1. Lifecycle validator must reject when visual evidence files are missing
            event_dict = {
                "id": test_event_id,
                "camera_id": test_cam_id,
                "dataset_id": test_dataset_id,
                "representative_frame": "non_existent_frame.jpg",
                "start_timestamp": 2.0,
                "end_timestamp": 3.0
            }
            is_valid, reason = validate_candidate_event(event_dict, camera_id=test_cam_id)
            self.assertFalse(is_valid, "Candidate with missing evidence files must be rejected")
            self.assertIn("missing", reason.lower())

            # 2. Evidence endpoint must return UNVERIFIED instead of YES
            evidence = generate_evidence_clip(test_event_id)
            self.assertIsNotNone(evidence)
            self.assertEqual(
                evidence["verdict"], "UNVERIFIED",
                f"Expected verdict 'UNVERIFIED' when evidence is missing, got '{evidence['verdict']}'"
            )

        finally:
            execute_query("DELETE FROM events WHERE camera_id = ?", (test_cam_id,))
            execute_query("DELETE FROM cameras WHERE id = ?", (test_cam_id,))


if __name__ == "__main__":
    unittest.main()
