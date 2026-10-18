"""
Optional Local Stream Video Recorder with Retention and Storage Limit Cleanup.

Features:
1. Records incoming camera frames into timestamped segmented MP4 video files.
2. Segmented rollover (e.g., every 5 minutes / 300 seconds).
3. Configurable retention policy (purges segments older than retention_days).
4. Configurable storage quota (purges oldest segments if total size exceeds max_storage_mb).
5. Non-blocking async writing to avoid slowing down real-time capture and inference.
"""

import os
import time
import glob
import queue
import threading
import cv2
import numpy as np
from typing import Optional, Dict, Any, List
from backend.app.core.logging import logger

DEFAULT_RECORDINGS_DIR = "data/recordings"


class StreamRecorder:
    """
    Manages optional segmented local video recording for a single camera feed
    with automated storage quota enforcement and retention cleanup.
    """
    def __init__(
        self,
        camera_id: str,
        recordings_dir: str = DEFAULT_RECORDINGS_DIR,
        segment_duration: float = 300.0,  # 5 minutes per segment
        max_storage_mb: float = 5000.0,    # 5 GB default cap
        retention_days: float = 7.0,       # 7 days default retention
        fps: float = 15.0
    ):
        self.camera_id = camera_id
        self.base_dir = os.path.join(recordings_dir, camera_id)
        os.makedirs(self.base_dir, exist_ok=True)

        self.segment_duration = segment_duration
        self.max_storage_mb = max_storage_mb
        self.retention_days = retention_days
        self.fps = fps if (fps and 0 < fps < 120) else 15.0

        self.is_recording = False
        self._queue: queue.Queue = queue.Queue(maxsize=120)
        self._stop_event = threading.Event()
        self._worker_thread: Optional[threading.Thread] = None

        self._current_writer: Optional[cv2.VideoWriter] = None
        self._current_segment_path: Optional[str] = None
        self._current_segment_start: float = 0.0
        self._frame_width: int = 0
        self._frame_height: int = 0
        self._total_frames_written: int = 0
        self._dropped_frames: int = 0

    def start(self, width: int = 640, height: int = 360):
        """Starts the asynchronous writer worker thread."""
        if self.is_recording:
            return
        self._frame_width = width
        self._frame_height = height
        self._stop_event.clear()
        self.is_recording = True
        self._worker_thread = threading.Thread(
            target=self._writer_loop,
            name=f"StreamRecorder-{self.camera_id}",
            daemon=True
        )
        self._worker_thread.start()
        logger.info(f"[{self.camera_id}] Stream recording started -> {self.base_dir}")

    def write_frame(self, frame: np.ndarray, timestamp: Optional[float] = None):
        """Queues a frame for recording without blocking the capture thread."""
        if not self.is_recording:
            return
        now = timestamp or time.time()
        try:
            self._queue.put_nowait((frame, now))
        except queue.Full:
            self._dropped_frames += 1

    def stop(self):
        """Flushes remaining frames, closes active video writer, and stops worker."""
        if not self.is_recording:
            return
        self.is_recording = False
        self._stop_event.set()
        if self._worker_thread and self._worker_thread.is_alive():
            self._worker_thread.join(timeout=3.0)
            self._worker_thread = None

        self._close_current_segment()
        self.cleanup_old_recordings()
        logger.info(f"[{self.camera_id}] Stream recording stopped.")

    def _open_new_segment(self, timestamp: float, width: int, height: int):
        self._close_current_segment()
        filename = f"rec_{self.camera_id}_{int(timestamp)}.mp4"
        self._current_segment_path = os.path.join(self.base_dir, filename)
        self._current_segment_start = timestamp

        fourcc = cv2.VideoWriter_fourcc(*"mp4v")
        self._current_writer = cv2.VideoWriter(
            self._current_segment_path,
            fourcc,
            self.fps,
            (width, height)
        )
        logger.debug(f"[{self.camera_id}] Opened new recording segment: {filename}")

    def _close_current_segment(self):
        if self._current_writer is not None:
            try:
                self._current_writer.release()
            except Exception as e:
                logger.warning(f"[{self.camera_id}] Error releasing writer: {e}")
            self._current_writer = None
            self._current_segment_path = None

    def _writer_loop(self):
        last_cleanup_time = time.time()
        while not self._stop_event.is_set():
            try:
                item = self._queue.get(timeout=0.25)
            except queue.Empty:
                continue

            frame, ts = item
            h, w = frame.shape[:2]
            if w != self._frame_width or h != self._frame_height:
                self._frame_width = w
                self._frame_height = h
                self._close_current_segment()

            # Check if current segment needs rollover
            if self._current_writer is None or (ts - self._current_segment_start >= self.segment_duration):
                self._open_new_segment(ts, w, h)

            if self._current_writer is not None and self._current_writer.isOpened():
                try:
                    self._current_writer.write(frame)
                    self._total_frames_written += 1
                except Exception as e:
                    logger.warning(f"[{self.camera_id}] Error writing frame: {e}")

            self._queue.task_done()

            # Run retention cleanup every 10 minutes
            if time.time() - last_cleanup_time > 600.0:
                self.cleanup_old_recordings()
                last_cleanup_time = time.time()

    def cleanup_old_recordings(self) -> Dict[str, Any]:
        """
        Enforces retention days and storage quota by removing expired or excess segment files.
        """
        if not os.path.exists(self.base_dir):
            return {"deleted_files": 0, "freed_mb": 0.0}

        files = glob.glob(os.path.join(self.base_dir, "*.mp4"))
        now = time.time()
        retention_cutoff = now - (self.retention_days * 86400.0)

        deleted_files = 0
        freed_bytes = 0

        file_stats: List[Dict[str, Any]] = []
        for fpath in files:
            # Don't delete the active segment currently being written
            if self._current_segment_path and os.path.abspath(fpath) == os.path.abspath(self._current_segment_path):
                continue
            try:
                st = os.stat(fpath)
                mtime = st.st_mtime
                size = st.st_size

                # Rule 1: Purge if older than retention_days
                if mtime < retention_cutoff:
                    os.remove(fpath)
                    deleted_files += 1
                    freed_bytes += size
                    logger.info(f"[{self.camera_id}] Purged expired recording: {os.path.basename(fpath)}")
                else:
                    file_stats.append({"path": fpath, "mtime": mtime, "size": size})
            except Exception as e:
                logger.warning(f"[{self.camera_id}] Error inspecting file {fpath}: {e}")

        # Rule 2: Enforce storage quota (purge oldest files first)
        max_bytes = self.max_storage_mb * 1024 * 1024
        total_size = sum(f["size"] for f in file_stats)
        if total_size > max_bytes:
            # Sort by mtime ascending (oldest first)
            file_stats.sort(key=lambda x: x["mtime"])
            for f in file_stats:
                if total_size <= max_bytes:
                    break
                try:
                    os.remove(f["path"])
                    deleted_files += 1
                    freed_bytes += f["size"]
                    total_size -= f["size"]
                    logger.info(f"[{self.camera_id}] Purged recording to satisfy storage quota: {os.path.basename(f['path'])}")
                except Exception as e:
                    logger.warning(f"[{self.camera_id}] Error removing excess file {f['path']}: {e}")

        freed_mb = round(freed_bytes / (1024 * 1024), 2)
        return {"deleted_files": deleted_files, "freed_mb": freed_mb}

    def list_recordings(self) -> List[Dict[str, Any]]:
        """Returns metadata for all recorded video segment files."""
        if not os.path.exists(self.base_dir):
            return []
        files = sorted(glob.glob(os.path.join(self.base_dir, "*.mp4")), key=os.path.getmtime, reverse=True)
        results = []
        for f in files:
            results.append({
                "filename": os.path.basename(f),
                "file_path": f,
                "size_bytes": os.path.getsize(f),
                "size_mb": round(os.path.getsize(f) / (1024 * 1024), 2),
                "mtime": os.path.getmtime(f)
            })
        return results

    get_recordings = list_recordings

    def get_storage_stats(self) -> Dict[str, Any]:
        """Returns storage metrics for the camera's local recordings."""
        if not os.path.exists(self.base_dir):
            return {
                "camera_id": self.camera_id,
                "is_recording": self.is_recording,
                "file_count": 0,
                "total_size_mb": 0.0,
                "max_storage_mb": self.max_storage_mb,
                "retention_days": self.retention_days
            }

        files = glob.glob(os.path.join(self.base_dir, "*.mp4"))
        total_bytes = sum(os.path.getsize(f) for f in files if os.path.exists(f))
        return {
            "camera_id": self.camera_id,
            "is_recording": self.is_recording,
            "file_count": len(files),
            "total_size_mb": round(total_bytes / (1024 * 1024), 2),
            "max_storage_mb": self.max_storage_mb,
            "retention_days": self.retention_days,
            "total_frames_written": self._total_frames_written,
            "dropped_frames": self._dropped_frames
        }
