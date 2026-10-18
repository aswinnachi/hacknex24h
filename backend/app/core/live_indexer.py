"""
backend/app/core/live_indexer.py

Stage E: Persistent Live Event Indexing & Track Episode Aggregator
- Aggregates frame-by-frame detections into cohesive object-track episodes.
- Avoids flooding the database with duplicate writes for every video frame.
- Extracts visual attributes (dominant color, clothing, movement displacement).
- Asynchronously saves representative frame snapshots to data/frames/live/{camera_id}/.
- Bounded async persistence queue with worker thread and explicit overflow protection.
- Automatically finalizes tracks on expiration/timeout, camera disconnect, or shutdown.
- Seamlessly integrates with SQLite database and VectorSearch multimodal indexing.
"""

import os
import time
import json
import uuid
import queue
import threading
from typing import Dict, Any, List, Optional, Tuple
from datetime import datetime
import cv2
import numpy as np

from backend.app.core.logging import logger
from backend.app.db.database import execute_query
from backend.app.core.attributes import (
    analyze_crop_color,
    analyze_person_clothing_and_accessories,
    analyze_track_movement
)

from backend.app.core.config import settings

FRAMES_LIVE_DIR = getattr(settings, "frames_storage_dir", "data/frames/live")
os.makedirs(FRAMES_LIVE_DIR, exist_ok=True)


class LiveTrackEpisode:
    """
    Encapsulates the state of an ongoing object track within a live camera stream session.
    """
    def __init__(
        self,
        event_id: str,
        camera_id: str,
        dataset_id: Optional[str],
        session_id: str,
        track_id: int,
        concept: str,
        initial_confidence: float,
        initial_bbox: List[float],
        start_timestamp: float,
        frame_number: int,
        representative_frame_img: Optional[np.ndarray] = None,
        source_type: str = "live"
    ):
        self.event_id = event_id
        self.camera_id = camera_id
        self.dataset_id = dataset_id
        self.session_id = session_id
        self.track_id = track_id
        self.concept = concept
        self.source_type = source_type or "live"
        self.max_confidence = initial_confidence
        self.start_timestamp = start_timestamp
        self.end_timestamp = start_timestamp
        self.start_frame = frame_number
        self.end_frame = frame_number
        self.last_seen_time = start_timestamp
        self.frame_count = 1
        
        # Bounded bounding box trajectory history (max 40 points)
        self.bboxes: List[List[float]] = [initial_bbox]
        self.last_bbox_sample_time = start_timestamp
        
        # Representative frame file path and memory buffer
        cam_dir = f"{FRAMES_LIVE_DIR}/{camera_id}"
        self.representative_frame_path = f"{cam_dir}/frame_{event_id}.jpg"
        self.representative_frame_img = None
        
        # Attribute observation history (bounded)
        self.crops: List[np.ndarray] = []
        self.person_observations: List[Dict[str, Any]] = []
        self.color = "unknown"
        self.color_conf = 0.5
        self.attributes: Dict[str, Any] = {}
        
        # Database synchronization state
        self.is_persisted = False
        self.last_db_sync_time = 0.0

    def update(
        self,
        confidence: float,
        bbox: List[float],
        timestamp: float,
        frame_number: int,
        crop: Optional[np.ndarray] = None,
        person_attr: Optional[Dict[str, Any]] = None
    ):
        self.end_timestamp = timestamp
        self.end_frame = frame_number
        self.last_seen_time = timestamp
        self.frame_count += 1
        if confidence > self.max_confidence:
            self.max_confidence = confidence

        # Sample bounding boxes at ~0.3s intervals (bounded memory, max 40 points)
        if (timestamp - self.last_bbox_sample_time >= 0.3) and len(self.bboxes) < 40:
            self.bboxes.append(bbox)
            self.last_bbox_sample_time = timestamp

        # Sample crops (up to 3) for color extraction - store small patches to avoid RAM bloat
        if crop is not None and len(self.crops) < 3:
            try:
                if crop.shape[0] > 64 or crop.shape[1] > 64:
                    crop = cv2.resize(crop, (64, 64))
                self.crops.append(crop)
            except Exception:
                pass

        # Sample person observations (up to 4)
        if person_attr is not None and len(self.person_observations) < 4:
            self.person_observations.append(person_attr)

    def finalize_attributes(self):
        """
        Synthesizes observed attributes across the lifetime of the episode.
        """
        # 1. Color extraction
        if self.crops and self.color == "unknown":
            color_candidates = []
            for c in self.crops:
                col, conf = analyze_crop_color(c, self.concept)
                if col != "unknown":
                    color_candidates.append((col, conf))
            if color_candidates:
                cols = [c[0] for c in color_candidates]
                self.color = max(set(cols), key=cols.count)
                self.color_conf = max(c[1] for c in color_candidates if c[0] == self.color)

        # 2. Movement & action analysis
        is_moving, action, displacement = analyze_track_movement(
            self.bboxes,
            fps=12.0,
            concept=self.concept
        )

        # 3. Person clothing & accessories synthesis
        upper_color = "unknown"
        lower_color = "unknown"
        clothing_summary = f"{self.color} clothing" if self.color != "unknown" else "clothing"
        has_bag = False
        has_backpack = False
        has_handbag = False
        has_suitcase = False
        accessories = []

        if self.concept.lower() == "person" and self.person_observations:
            u_cols = [o["upper_color"] for o in self.person_observations if o.get("upper_color") != "unknown"]
            if u_cols:
                upper_color = max(set(u_cols), key=u_cols.count)
            l_cols = [o["lower_color"] for o in self.person_observations if o.get("lower_color") != "unknown"]
            if l_cols:
                lower_color = max(set(l_cols), key=l_cols.count)

            has_backpack = any(o.get("has_backpack") for o in self.person_observations)
            has_handbag = any(o.get("has_handbag") for o in self.person_observations)
            has_suitcase = any(o.get("has_suitcase") for o in self.person_observations)
            has_bag = bool(has_backpack or has_handbag or has_suitcase or any(o.get("has_bag") for o in self.person_observations))
            
            if has_backpack: accessories.append("backpack")
            if has_handbag: accessories.append("handbag")
            if has_suitcase: accessories.append("suitcase")
            if has_bag and not accessories: accessories.append("bag")

            parts = []
            if upper_color != "unknown": parts.append(f"{upper_color} shirt")
            if lower_color != "unknown": parts.append(f"{lower_color} pants")
            clothing_summary = " with ".join(parts) if parts else (f"{self.color} clothing" if self.color != "unknown" else "clothing")

        self.attributes = {
            "object_type": self.concept,
            "color": self.color,
            "color_confidence": round(self.color_conf, 2),
            "upper_color": upper_color,
            "lower_color": lower_color,
            "clothing": clothing_summary,
            "is_moving": is_moving,
            "action": action,
            "displacement_px": round(displacement, 1),
            "has_bag": has_bag,
            "has_backpack": has_backpack,
            "has_handbag": has_handbag,
            "has_suitcase": has_suitcase,
            "accessories": accessories,
            "frames_observed": self.frame_count,
            "duration": round(max(0.1, self.end_timestamp - self.start_timestamp), 2),
            "session_id": self.session_id
        }

    def to_record_dict(self) -> Dict[str, Any]:
        return {
            "id": self.event_id,
            "camera_id": self.camera_id,
            "dataset_id": self.dataset_id,
            "tracking_id": str(self.track_id),
            "concept": self.concept,
            "start_timestamp": round(self.start_timestamp, 3),
            "end_timestamp": round(self.end_timestamp, 3),
            "confidence": round(float(self.max_confidence), 3),
            "bounding_boxes": json.dumps(self.bboxes),
            "representative_frame": self.representative_frame_path,
            "attributes": json.dumps(self.attributes),
            "source_type": self.source_type or "live",
            "session_id": self.session_id,
            "frame_number": self.start_frame
        }


class LiveEventIndexer:
    """
    Central persistence engine for live CCTV event episodes.
    Maintains active track episodes, handles deduplication, and streams updates
    through an asynchronous persistence worker with bounded memory and overflow safety.
    """
    _instance = None
    _lock = threading.Lock()

    def __init__(self, track_timeout: float = 2.5, queue_capacity: int = 100):
        self.track_timeout = track_timeout
        self._queue: queue.Queue = queue.Queue(maxsize=queue_capacity)
        self._stop_event = threading.Event()
        self._worker_thread: Optional[threading.Thread] = None

        # (camera_id, track_id) -> LiveTrackEpisode
        self._active_episodes: Dict[str, Dict[int, LiveTrackEpisode]] = {}
        self._camera_sessions: Dict[str, str] = {}
        self._camera_datasets: Dict[str, Optional[str]] = {}
        self._camera_source_types: Dict[str, str] = {}
        self._indexer_lock = threading.Lock()

        # Operational metrics
        self.stats = {
            "total_episodes_created": 0,
            "total_persisted_events": 0,
            "dropped_writes": 0,
            "db_errors": 0,
            "queue_overflows": 0
        }

        self._start_worker()

    @classmethod
    def get_instance(cls) -> "LiveEventIndexer":
        with cls._lock:
            if cls._instance is None:
                cls._instance = LiveEventIndexer()
            return cls._instance

    def _start_worker(self):
        if self._worker_thread is not None and self._worker_thread.is_alive():
            return
        self._stop_event.clear()
        self._worker_thread = threading.Thread(
            target=self._worker_loop,
            name="LivePersistenceWorker",
            daemon=True
        )
        self._worker_thread.start()
        logger.info("[LiveIndexer] Asynchronous persistence worker started.")

    def _worker_loop(self):
        while not self._stop_event.is_set():
            try:
                task = self._queue.get(timeout=0.25)
            except queue.Empty:
                continue

            try:
                action, record, frame_bytes, frame_path = task

                # 1. Asynchronously save JPEG frame snapshot
                if frame_bytes is not None and frame_path:
                    try:
                        dir_path = os.path.dirname(frame_path)
                        os.makedirs(dir_path, exist_ok=True)
                        if not os.path.exists(frame_path):
                            with open(frame_path, "wb") as f:
                                f.write(frame_bytes)
                    except Exception as e:
                        logger.warning(f"[LiveIndexer] Error saving frame snapshot {frame_path}: {e}")

                # 2. Persist or update SQLite events table
                if action == "upsert":
                    execute_query(
                        """
                        INSERT INTO events (
                            id, camera_id, dataset_id, tracking_id, concept,
                            start_timestamp, end_timestamp, confidence,
                            bounding_boxes, representative_frame, attributes,
                            source_type, session_id, frame_number
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(id) DO UPDATE SET
                            end_timestamp = excluded.end_timestamp,
                            confidence = excluded.confidence,
                            bounding_boxes = excluded.bounding_boxes,
                            attributes = excluded.attributes,
                            representative_frame = COALESCE(excluded.representative_frame, events.representative_frame)
                        """,
                        (
                            record["id"],
                            record["camera_id"],
                            record["dataset_id"],
                            record["tracking_id"],
                            record["concept"],
                            record["start_timestamp"],
                            record["end_timestamp"],
                            record["confidence"],
                            record["bounding_boxes"],
                            record["representative_frame"],
                            record["attributes"],
                            record["source_type"],
                            record["session_id"],
                            record["frame_number"]
                        )
                    )
                    self.stats["total_persisted_events"] += 1

                    # 3. Incremental indexing into VectorSearch with memory-cached vectors
                    try:
                        concept_key = record.get("concept", "object")
                        if not hasattr(self, "_concept_vector_cache"):
                            self._concept_vector_cache = {}
                        
                        vec = self._concept_vector_cache.get(concept_key)
                        if vec is None:
                            from backend.app.core.embeddings import get_embedder
                            embedder = get_embedder()
                            if embedder.available:
                                raw_vec = embedder.embed_text(concept_key)
                                if raw_vec is not None:
                                    vec = np.array(raw_vec, dtype=np.float32)
                                    self._concept_vector_cache[concept_key] = vec
                        
                        if vec is not None:
                            from backend.app.core.vector_search import get_vector_search
                            vsearch = get_vector_search()
                            vsearch.add_vector(record["id"], vec)
                    except Exception as e:
                        logger.debug(f"[LiveIndexer] Incremental vector indexing skipped: {e}")

            except Exception as e:
                self.stats["db_errors"] += 1
                logger.error(f"[LiveIndexer] Database persistence error: {e}")
            finally:
                self._queue.task_done()

    def _enqueue_write(self, action: str, record: Dict[str, Any], frame_img: Optional[np.ndarray], frame_path: Optional[str]):
        frame_bytes = None
        if frame_img is not None and frame_path and not os.path.exists(frame_path):
            try:
                h, w = frame_img.shape[:2]
                scale = min(1.0, 640.0 / max(w, 1))
                small = cv2.resize(frame_img, (int(w * scale), int(h * scale))) if scale < 1.0 else frame_img
                ret, buf = cv2.imencode(".jpg", small, [cv2.IMWRITE_JPEG_QUALITY, 80])
                if ret:
                    frame_bytes = buf.tobytes()
            except Exception:
                pass

        try:
            self._queue.put_nowait((action, record, frame_bytes, frame_path))
        except queue.Full:
            self.stats["queue_overflows"] += 1
            self.stats["dropped_writes"] += 1
            try:
                _ = self._queue.get_nowait()
                self._queue.put_nowait((action, record, frame_bytes, frame_path))
            except Exception:
                pass

    def process_frame_detections(
        self,
        camera_id: str,
        frame: np.ndarray,
        detections: List[Dict[str, Any]],
        frame_number: int,
        timestamp: float
    ):
        """
        Receives real-time detections for a video frame, manages episode lifecycles,
        extracts attributes, and schedules async persistence.
        """
        with self._indexer_lock:
            # Ensure camera session
            session_id = self._camera_sessions.get(camera_id)
            if not session_id:
                session_id = str(uuid.uuid4())
                self._camera_sessions[camera_id] = session_id
                cam_row = execute_query("SELECT dataset_id, source_type FROM cameras WHERE id = ?", (camera_id,), fetch=True, fetch_one=True)
                self._camera_datasets[camera_id] = cam_row["dataset_id"] if cam_row else None
                self._camera_source_types[camera_id] = cam_row["source_type"] if (cam_row and cam_row["source_type"]) else "live"

            dataset_id = self._camera_datasets.get(camera_id)
            if camera_id not in self._active_episodes:
                self._active_episodes[camera_id] = {}

            camera_tracks = self._active_episodes[camera_id]
            h, w = frame.shape[:2]

            for det in detections:
                tid = det.get("track_id")
                if tid is None:
                    continue  # Only track objects with confirmed tracker IDs

                conf = float(det.get("confidence", 0.0))
                bbox = det.get("bbox", [0, 0, 0, 0])
                label = det.get("label") or det.get("class") or "object"

                # Extract crop safely
                x1, y1, x2, y2 = [int(max(0, v)) for v in bbox[:4]]
                x2, y2 = min(w, x2), min(h, y2)
                crop = frame[y1:y2, x1:x2].copy() if (x2 > x1 + 5 and y2 > y1 + 5) else None

                person_attr = None
                if label == "person" and crop is not None:
                    try:
                        person_attr = analyze_person_clothing_and_accessories(frame, bbox, detections)
                    except Exception:
                        pass

                if tid not in camera_tracks:
                    # New track episode started
                    event_id = str(uuid.uuid4())
                    ep = LiveTrackEpisode(
                        event_id=event_id,
                        camera_id=camera_id,
                        dataset_id=dataset_id,
                        session_id=session_id,
                        track_id=tid,
                        concept=label,
                        initial_confidence=conf,
                        initial_bbox=bbox,
                        start_timestamp=timestamp,
                        frame_number=frame_number,
                        representative_frame_img=None,
                        source_type=self._camera_source_types.get(camera_id, "live")
                    )
                    if crop is not None:
                        ep.crops.append(crop)
                        col, c_conf = analyze_crop_color(crop, label)
                        ep.color = col
                        ep.color_conf = c_conf
                    if person_attr is not None:
                        ep.person_observations.append(person_attr)

                    ep.finalize_attributes()
                    camera_tracks[tid] = ep
                    self.stats["total_episodes_created"] += 1

                    # Immediately enqueue initial record so it appears in live search
                    rec = ep.to_record_dict()
                    self._enqueue_write("upsert", rec, frame, ep.representative_frame_path)
                    ep.is_persisted = True
                    ep.last_db_sync_time = timestamp
                else:
                    # Ongoing track continues
                    ep = camera_tracks[tid]
                    ep.update(
                        confidence=conf,
                        bbox=bbox,
                        timestamp=timestamp,
                        frame_number=frame_number,
                        crop=crop,
                        person_attr=person_attr
                    )

                    # Periodic upsert every 2.0s for ongoing tracks
                    if timestamp - ep.last_db_sync_time >= 2.0:
                        ep.finalize_attributes()
                        rec = ep.to_record_dict()
                        self._enqueue_write("upsert", rec, None, None)
                        ep.last_db_sync_time = timestamp

            # Check for expired/disappeared tracks
            lost_tids = []
            for tid, ep in camera_tracks.items():
                if timestamp - ep.last_seen_time > self.track_timeout:
                    lost_tids.append(tid)

            for tid in lost_tids:
                ep = camera_tracks.pop(tid)
                ep.finalize_attributes()
                rec = ep.to_record_dict()
                self._enqueue_write("upsert", rec, None, None)

    def finalize_camera_stream(self, camera_id: str):
        """
        Called when a camera stream disconnects. Flushes all remaining active
        episodes into the persistence queue and resets the tracking session.
        """
        with self._indexer_lock:
            if camera_id in self._active_episodes:
                for ep in self._active_episodes[camera_id].values():
                    ep.finalize_attributes()
                    rec = ep.to_record_dict()
                    self._enqueue_write("upsert", rec, None, None)
                self._active_episodes[camera_id].clear()

            if camera_id in self._camera_sessions:
                del self._camera_sessions[camera_id]

            if camera_id in self._camera_source_types:
                del self._camera_source_types[camera_id]

        logger.info(f"[LiveIndexer] Finalized all active episodes for camera {camera_id}.")

    def reset_all(self):
        """
        Clears all in-memory tracking states and resets queues.
        """
        with self._indexer_lock:
            self._active_episodes.clear()
            self._camera_sessions.clear()
            self._camera_datasets.clear()
            self._camera_source_types.clear()

    def enforce_evidence_retention(
        self,
        max_age_days: Optional[float] = None,
        max_storage_gb: Optional[float] = None
    ) -> Dict[str, Any]:
        """
        Safely enforces evidence storage limits and retention policies.
        Deletes files older than max_age_days or purges oldest files when total size exceeds max_storage_gb.
        Handles disk errors, missing files, and permissions gracefully.
        """
        age_days = max_age_days if max_age_days is not None else getattr(settings, "evidence_retention_days", 7.0)
        storage_gb = max_storage_gb if max_storage_gb is not None else getattr(settings, "max_evidence_storage_gb", 10.0)
        
        cutoff_time = time.time() - (age_days * 86400.0)
        max_bytes = storage_gb * 1024 * 1024 * 1024
        
        scanned_files = []
        total_size = 0
        deleted_expired = 0
        deleted_size = 0
        freed_bytes = 0
        
        target_dir = FRAMES_LIVE_DIR
        if os.path.exists(target_dir):
            for root, _, files in os.walk(target_dir):
                for fname in files:
                    fpath = os.path.join(root, fname)
                    try:
                        st = os.stat(fpath)
                        total_size += st.st_size
                        scanned_files.append((fpath, st.st_mtime, st.st_size))
                    except (OSError, FileNotFoundError):
                        continue

        # 1. Purge expired files older than retention days
        remaining_files = []
        for fpath, mtime, fsize in scanned_files:
            if mtime < cutoff_time:
                try:
                    os.remove(fpath)
                    freed_bytes += fsize
                    total_size -= fsize
                    deleted_expired += 1
                except OSError as e:
                    logger.warning(f"[LiveIndexer] Disk error removing expired evidence {fpath}: {e}")
            else:
                remaining_files.append((fpath, mtime, fsize))

        # 2. Purge oldest files if total size exceeds max_storage_gb
        if total_size > max_bytes:
            remaining_files.sort(key=lambda x: x[1])  # Oldest first
            for fpath, mtime, fsize in remaining_files:
                if total_size <= max_bytes:
                    break
                try:
                    os.remove(fpath)
                    freed_bytes += fsize
                    total_size -= fsize
                    deleted_size += 1
                except OSError as e:
                    logger.warning(f"[LiveIndexer] Disk error purging evidence under capacity limit {fpath}: {e}")

        return {
            "scanned_files": len(scanned_files),
            "deleted_expired": deleted_expired,
            "deleted_size_limit": deleted_size,
            "freed_bytes": freed_bytes,
            "freed_mb": round(freed_bytes / (1024 * 1024), 2),
            "remaining_storage_mb": round(total_size / (1024 * 1024), 2),
            "max_storage_gb": storage_gb,
            "retention_days": age_days
        }

    def get_stats(self) -> Dict[str, Any]:
        with self._indexer_lock:
            active_count = sum(len(episodes) for episodes in self._active_episodes.values())
            return {
                "active_episodes_count": active_count,
                "queue_pending_writes": self._queue.qsize(),
                **self.stats
            }


def get_live_indexer() -> LiveEventIndexer:
    return LiveEventIndexer.get_instance()
