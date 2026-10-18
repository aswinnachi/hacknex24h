import os
import cv2
import time
import json
import csv
import uuid
import subprocess
import threading
from typing import Dict, Any, List, Optional, Tuple

from backend.app.core.logging import logger
from backend.app.core.config import settings
from backend.app.core.storage import get_storage
from backend.app.core.detector import get_detector, DEFAULT_CLASSES
from backend.app.core.attributes import analyze_crop_color, analyze_track_movement
from backend.app.db.database import execute_query

# Active cancellation tokens for ongoing jobs
_cancellation_flags: Dict[str, bool] = {}
_jobs_lock = threading.Lock()

def request_job_cancellation(job_id: str) -> bool:
    with _jobs_lock:
        _cancellation_flags[job_id] = True
    logger.info(f"[VideoLab] Cancellation requested for job_id={job_id}")
    return True

def is_job_cancelled(job_id: str) -> bool:
    with _jobs_lock:
        return _cancellation_flags.get(job_id, False)

def clear_job_cancellation(job_id: str):
    with _jobs_lock:
        _cancellation_flags.pop(job_id, None)

def validate_video_file(file_path: str, original_filename: str) -> Dict[str, Any]:
    """
    Validates file extension, magic bytes, and OpenCV decodability.
    Rejects corrupted or non-video files.
    """
    ext = os.path.splitext(original_filename)[1].lower()
    allowed_exts = [".mp4", ".mov", ".mkv", ".avi", ".webm"]
    if ext not in allowed_exts:
        raise ValueError(f"Unsupported file format '{ext}'. Supported formats: {', '.join(allowed_exts)}")

    if not os.path.exists(file_path):
        raise FileNotFoundError(f"File not found on server: {file_path}")

    file_size = os.path.getsize(file_path)
    if file_size < 1024:
        raise ValueError("File is too small to be a valid video (< 1 KB)")

    max_size_mb = float(os.getenv("MAX_UPLOAD_SIZE_MB", "250.0"))
    if file_size > (max_size_mb * 1024 * 1024):
        raise ValueError(f"File size ({file_size / (1024*1024):.1f} MB) exceeds maximum allowed limit ({max_size_mb} MB)")

    # Inspect magic bytes
    with open(file_path, "rb") as f:
        header = f.read(64)

    is_valid_header = False
    # MP4 / MOV ISO Base Media File Format: contains 'ftyp' at bytes 4..8 or 'moov' / 'mdat' / 'wide' / 'free'
    if b"ftyp" in header[:32] or b"moov" in header[:32] or b"mdat" in header[:32] or b"wide" in header[:32]:
        is_valid_header = True
    # AVI format: 'RIFF....AVI '
    elif header.startswith(b"RIFF") and b"AVI " in header[:16]:
        is_valid_header = True
    # Matroska / WebM EBML header: \x1a\x45\xdf\xa3
    elif header.startswith(b"\x1a\x45\xdf\xa3"):
        is_valid_header = True

    if not is_valid_header:
        # Fallback check: check if OpenCV can open it anyway
        test_cap = cv2.VideoCapture(file_path)
        can_open = test_cap.isOpened()
        test_cap.release()
        if not can_open:
            raise ValueError(f"File '{original_filename}' does not have a valid video signature or codec")

    # Deep verification with OpenCV
    cap = cv2.VideoCapture(file_path)
    if not cap.isOpened():
        raise ValueError(f"Failed to open video container for '{original_filename}'. Codec may be corrupted.")

    fps = cap.get(cv2.CAP_PROP_FPS) or 15.0
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    duration = (total_frames / fps) if fps > 0 else 0.0

    ret, first_frame = cap.read()
    cap.release()

    if not ret or first_frame is None:
        raise ValueError(f"Unable to decode video frames from '{original_filename}'. Stream is empty or corrupted.")

    if width < 32 or height < 32:
        raise ValueError(f"Video resolution ({width}x{height}) is too small to process.")

    return {
        "filename": original_filename,
        "file_size": file_size,
        "fps": round(fps, 2),
        "total_frames": total_frames,
        "width": width,
        "height": height,
        "resolution": f"{width}x{height}",
        "duration": round(duration, 2)
    }


def _draw_tracking_overlay(
    frame,
    detections: List[Dict[str, Any]],
    frame_idx: int,
    timestamp: float,
    fps: float
):
    """
    Renders high-contrast, premium CCTV bounding boxes with ByteTrack IDs and confidence badges.
    """
    annotated = frame.copy()
    h, w = annotated.shape[:2]

    # Palette
    CLASS_COLORS = {
        "person": (255, 180, 0),       # Cyan / Sky
        "car": (0, 220, 100),          # Bright Emerald
        "suv": (0, 220, 100),
        "van": (0, 220, 100),
        "truck": (0, 160, 255),        # Amber Orange
        "bus": (0, 160, 255),
        "vehicle": (0, 220, 100),
        "motorcycle": (255, 100, 220), # Violet / Magenta
        "bicycle": (255, 100, 220),
        "backpack": (0, 140, 255),     # Orange
        "bag": (0, 140, 255),
        "handbag": (0, 140, 255),
        "suitcase": (0, 140, 255),
        "dog": (50, 255, 180),
        "cat": (50, 255, 180)
    }
    DEFAULT_COLOR = (200, 200, 200)

    for det in detections:
        box = det["bbox"]
        label = det["label"].lower()
        conf = det.get("confidence", 0.0)
        track_id = det.get("track_id")
        color = CLASS_COLORS.get(label, DEFAULT_COLOR)

        x1, y1, x2, y2 = [int(v) for v in box[:4]]
        x1, y1 = max(0, x1), max(0, y1)
        x2, y2 = min(w, x2), min(h, y2)

        # Draw bounding box (thick with corner accents)
        cv2.rectangle(annotated, (x1, y1), (x2, y2), color, 2, cv2.LINE_AA)

        # Corner brackets for tech aesthetic
        corner_len = min(16, max(6, int((x2 - x1) * 0.15)))
        cv2.line(annotated, (x1, y1), (x1 + corner_len, y1), color, 3)
        cv2.line(annotated, (x1, y1), (x1, y1 + corner_len), color, 3)
        cv2.line(annotated, (x2, y1), (x2 - corner_len, y1), color, 3)
        cv2.line(annotated, (x2, y1), (x2, y1 + corner_len), color, 3)
        cv2.line(annotated, (x1, y2), (x1 + corner_len, y2), color, 3)
        cv2.line(annotated, (x1, y2), (x1, y2 - corner_len), color, 3)
        cv2.line(annotated, (x2, y2), (x2 - corner_len, y2), color, 3)
        cv2.line(annotated, (x2, y2), (x2, y2 - corner_len), color, 3)

        # Badge text: [ID: X] label conf%
        id_str = f"ID:{track_id} " if track_id is not None else ""
        badge_text = f"{id_str}{label.upper()} {int(conf * 100)}%"

        font = cv2.FONT_HERSHEY_SIMPLEX
        font_scale = 0.45
        font_thick = 1
        (tw, th), baseline = cv2.getTextSize(badge_text, font, font_scale, font_thick)

        # Badge background
        bx1 = x1
        by1 = max(0, y1 - th - 8)
        bx2 = x1 + tw + 10
        by2 = y1

        cv2.rectangle(annotated, (bx1, by1), (bx2, by2), (20, 24, 32), -1)
        cv2.rectangle(annotated, (bx1, by1), (bx2, by2), color, 1)
        cv2.putText(annotated, badge_text, (bx1 + 5, by2 - 4), font, font_scale, (255, 255, 255), font_thick, cv2.LINE_AA)

    # Top-left CCTV telemetry HUD overlay
    minutes = int(timestamp // 60)
    seconds = int(timestamp % 60)
    millis = int((timestamp - int(timestamp)) * 100)
    time_str = f"T+{minutes:02d}:{seconds:02d}.{millis:02d} | F:{frame_idx}"

    hud_text = f"AI LAB TRACKING | {time_str} | ACTIVE TRACKS: {len([d for d in detections if d.get('track_id')])}"
    cv2.rectangle(annotated, (10, 10), (min(w - 10, 480), 38), (15, 20, 28), -1)
    cv2.rectangle(annotated, (10, 10), (min(w - 10, 480), 38), (60, 80, 110), 1)
    cv2.putText(annotated, hud_text, (18, 29), cv2.FONT_HERSHEY_SIMPLEX, 0.42, (0, 230, 255), 1, cv2.LINE_AA)

    return annotated


def run_video_lab_analysis(job_id: str, frame_interval: int = 15):
    """
    Background worker function that runs real YOLO-World + ByteTrack processing,
    generates real annotated MP4 video, exports JSON & CSV, extracts evidence keyframes,
    and updates job status in the database.
    """
    storage = get_storage()
    clear_job_cancellation(job_id)

    # Fetch job info
    row = execute_query("SELECT * FROM analysis_jobs WHERE id = ?", (job_id,), fetch=True, fetch_one=True)
    if not row:
        logger.error(f"[VideoLab] Job not found: {job_id}")
        return

    job = dict(row)
    original_key = job.get("original_video_key")
    if not original_key:
        logger.error(f"[VideoLab] No original video key for job {job_id}")
        execute_query("UPDATE analysis_jobs SET status = 'failed', error_message = 'Missing video file reference' WHERE id = ?", (job_id,))
        return

    # Update status to processing
    execute_query(
        "UPDATE analysis_jobs SET status = 'processing', stage = 'Initializing detector & reading video', started_at = ? WHERE id = ?",
        (time.time(), job_id)
    )

    work_dir = os.path.abspath(os.path.join("data", "lab_processing", job_id))
    os.makedirs(work_dir, exist_ok=True)
    evidence_dir = os.path.join(work_dir, "evidence")
    os.makedirs(evidence_dir, exist_ok=True)

    try:
        video_local_path = storage.get_local_path(original_key)
        if not os.path.exists(video_local_path):
            raise FileNotFoundError(f"Local video file not found at {video_local_path}")

        cap = cv2.VideoCapture(video_local_path)
        if not cap.isOpened():
            raise ValueError(f"Could not open video file: {video_local_path}")

        fps = cap.get(cv2.CAP_PROP_FPS) or 15.0
        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        duration = total_frames / fps if fps > 0 else 0.0

        execute_query(
            "UPDATE analysis_jobs SET total_frames = ?, fps = ?, duration = ?, resolution = ? WHERE id = ?",
            (total_frames, round(fps, 2), round(duration, 2), f"{width}x{height}", job_id)
        )

        detector = get_detector()
        if not (detector and detector.available):
            logger.warning("[VideoLab] YOLO detector not immediately ready, initializing...")

        # Setup output annotated video
        raw_output_path = os.path.join(work_dir, "annotated_raw.mp4")
        # Use avc1 / mp4v fourcc
        fourcc = cv2.VideoWriter_fourcc(*'mp4v')
        out_writer = cv2.VideoWriter(raw_output_path, fourcc, fps, (width, height))

        current_frame = 0
        frames_analyzed = 0
        all_detections_records: List[Dict[str, Any]] = []
        unique_tracks: Dict[int, Dict[str, Any]] = {}
        per_class_counts: Dict[str, int] = {}
        last_frame_detections: List[Dict[str, Any]] = []

        # Process interval: default is frame_interval or dynamic based on video length
        step_interval = max(1, frame_interval)

        execute_query("UPDATE analysis_jobs SET stage = 'Running YOLO-World object detection & ByteTrack tracking' WHERE id = ?", (job_id,))
        logger.info(f"[VideoLab START] job_id={job_id} total_frames={total_frames} fps={fps:.1f} step={step_interval}")

        last_db_update_time = time.time()

        while cap.isOpened():
            if is_job_cancelled(job_id):
                logger.info(f"[VideoLab CANCELLED] job_id={job_id} at frame {current_frame}")
                cap.release()
                out_writer.release()
                execute_query("UPDATE analysis_jobs SET status = 'cancelled', stage = 'Analysis cancelled by user' WHERE id = ?", (job_id,))
                return

            ret, frame = cap.read()
            if not ret or frame is None:
                break

            timestamp = (current_frame / fps) if fps > 0 else 0.0
            is_keyframe = (current_frame % step_interval == 0) or (current_frame == 0)

            if is_keyframe:
                frames_analyzed += 1
                detections = detector.track_frame(
                    frame=frame,
                    classes=DEFAULT_CLASSES,
                    conf_threshold=0.30,
                    camera_id=f"lab_{job_id}",
                    is_live=False
                )
                last_frame_detections = detections

                for det in detections:
                    tid = det.get("track_id")
                    label = det.get("label", "unknown").lower()
                    conf = det.get("confidence", 0.0)
                    raw_conf = det.get("raw_confidence", conf)
                    bbox = det.get("bbox", [0, 0, 0, 0])
                    track_state = det.get("track_state", "confirmed" if tid is not None else "tentative")

                    # Track stats
                    if tid is not None:
                        if tid not in unique_tracks:
                            evidence_file = os.path.join(evidence_dir, f"track_{tid}.jpg")
                            # Save snapshot crop
                            x1, y1, x2, y2 = [int(v) for v in bbox]
                            x1, y1 = max(0, x1), max(0, y1)
                            x2, y2 = min(width, x2), min(height, y2)
                            crop = frame[y1:y2, x1:x2].copy() if (x2 > x1 and y2 > y1) else frame
                            cv2.imwrite(evidence_file, crop)

                            evidence_key = f"evidence/{job_id}/track_{tid}.jpg"
                            storage.save_file(evidence_file, evidence_key, content_type="image/jpeg")

                            unique_tracks[tid] = {
                                "track_id": tid,
                                "label": label,
                                "first_seen": round(timestamp, 2),
                                "last_seen": round(timestamp, 2),
                                "max_conf": conf,
                                "evidence_key": evidence_key,
                                "bboxes": [bbox]
                            }
                            per_class_counts[label] = per_class_counts.get(label, 0) + 1
                        else:
                            unique_tracks[tid]["last_seen"] = round(timestamp, 2)
                            unique_tracks[tid]["bboxes"].append(bbox)
                            if conf > unique_tracks[tid]["max_conf"]:
                                unique_tracks[tid]["max_conf"] = conf

                    record_id = str(uuid.uuid4())
                    all_detections_records.append({
                        "id": record_id,
                        "job_id": job_id,
                        "tracking_id": tid,
                        "track_state": track_state,
                        "label": label,
                        "confidence": round(conf, 3),
                        "raw_confidence": round(raw_conf, 3),
                        "timestamp_offset": round(timestamp, 2),
                        "frame_index": current_frame,
                        "bbox_x1": bbox[0],
                        "bbox_y1": bbox[1],
                        "bbox_x2": bbox[2],
                        "bbox_y2": bbox[3],
                        "attributes": json.dumps({"label": label, "conf": conf}),
                        "evidence_frame_key": unique_tracks[tid]["evidence_key"] if (tid and tid in unique_tracks) else None,
                        "created_at": time.time()
                    })

            # Render overlay for all frames (using latest tracked positions)
            annotated_frame = _draw_tracking_overlay(frame, last_frame_detections, current_frame, timestamp, fps)
            out_writer.write(annotated_frame)

            current_frame += 1

            # Update DB progress throttled to every 0.8s
            now = time.time()
            if (now - last_db_update_time) > 0.8 or current_frame >= total_frames:
                last_db_update_time = now
                pct = round(min(98.0, (current_frame / max(1, total_frames)) * 100.0), 1)
                execute_query(
                    """
                    UPDATE analysis_jobs 
                    SET current_frame = ?, progress_pct = ?, frames_analyzed = ?, 
                        unique_tracks_count = ?, total_detections_count = ?
                    WHERE id = ?
                    """,
                    (current_frame, pct, frames_analyzed, len(unique_tracks), len(all_detections_records), job_id)
                )

        cap.release()
        out_writer.release()

        # Step 2: Batch insert detections into lab_detections table
        execute_query("UPDATE analysis_jobs SET stage = 'Saving detection timeline and attributes' WHERE id = ?", (job_id,))
        for det_item in all_detections_records:
            execute_query(
                """
                INSERT INTO lab_detections 
                (id, job_id, tracking_id, track_state, label, confidence, raw_confidence, timestamp_offset, frame_index, bbox_x1, bbox_y1, bbox_x2, bbox_y2, attributes, evidence_frame_key, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    det_item["id"],
                    det_item["job_id"],
                    det_item["tracking_id"],
                    det_item["track_state"],
                    det_item["label"],
                    det_item["confidence"],
                    det_item["raw_confidence"],
                    det_item["timestamp_offset"],
                    det_item["frame_index"],
                    det_item["bbox_x1"],
                    det_item["bbox_y1"],
                    det_item["bbox_x2"],
                    det_item["bbox_y2"],
                    det_item["attributes"],
                    det_item["evidence_frame_key"],
                    det_item["created_at"]
                )
            )

        # Step 3: Re-encode video with ffmpeg to standard H.264 if available for browser compatibility
        execute_query("UPDATE analysis_jobs SET stage = 'Encoding browser-compatible H.264 video' WHERE id = ?", (job_id,))
        final_video_path = os.path.join(work_dir, "annotated_browser.mp4")
        has_h264 = False

        ffmpeg_bin = shutil_which("ffmpeg")
        if ffmpeg_bin:
            try:
                cmd = [
                    ffmpeg_bin, "-y",
                    "-i", raw_output_path,
                    "-vcodec", "libx264",
                    "-pix_fmt", "yuv420p",
                    "-preset", "fast",
                    "-crf", "23",
                    "-movflags", "+faststart",
                    final_video_path
                ]
                proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=120)
                if proc.returncode == 0 and os.path.exists(final_video_path) and os.path.getsize(final_video_path) > 0:
                    has_h264 = True
                    logger.info(f"[VideoLab] Successfully encoded H.264 video with ffmpeg for {job_id}")
            except Exception as ffmpeg_err:
                logger.warning(f"[VideoLab] FFmpeg re-encode failed: {ffmpeg_err}")

        video_to_upload = final_video_path if has_h264 else raw_output_path
        annotated_key = f"annotated/{job_id}/annotated_tracking.mp4"
        storage.save_file(video_to_upload, annotated_key, content_type="video/mp4")

        # Step 4: Export JSON Results
        json_results_path = os.path.join(work_dir, "results.json")
        summary_payload = {
            "job_id": job_id,
            "filename": job.get("filename"),
            "duration_seconds": round(duration, 2),
            "total_frames": total_frames,
            "frames_analyzed": frames_analyzed,
            "fps": round(fps, 2),
            "resolution": f"{width}x{height}",
            "unique_tracked_objects_count": len(unique_tracks),
            "total_detections_count": len(all_detections_records),
            "class_counts": per_class_counts,
            "tracks": [
                {
                    "tracking_id": t["track_id"],
                    "label": t["label"],
                    "first_seen": t["first_seen"],
                    "last_seen": t["last_seen"],
                    "duration_visible": round(t["last_seen"] - t["first_seen"], 2),
                    "max_confidence": round(t["max_conf"], 3),
                    "evidence_url": storage.get_url(t["evidence_key"]) if t.get("evidence_key") else None
                }
                for t in unique_tracks.values()
            ],
            "note": "ByteTrack IDs are trajectory sequence tokens local to this video file and do not establish biometric real-world identity."
        }
        with open(json_results_path, "w", encoding="utf-8") as jf:
            json.dump(summary_payload, jf, indent=2)

        json_key = f"reports/{job_id}/detection_results.json"
        storage.save_file(json_results_path, json_key, content_type="application/json")

        # Step 5: Export CSV Results
        csv_results_path = os.path.join(work_dir, "results.csv")
        with open(csv_results_path, "w", newline="", encoding="utf-8") as cf:
            writer = csv.writer(cf)
            writer.writerow([
                "job_id", "timestamp_offset_sec", "frame_index", "tracking_id",
                "label", "confidence", "raw_confidence", "track_state",
                "bbox_x1", "bbox_y1", "bbox_x2", "bbox_y2"
            ])
            for d in all_detections_records:
                writer.writerow([
                    d["job_id"], d["timestamp_offset"], d["frame_index"],
                    d["tracking_id"], d["label"], d["confidence"], d["raw_confidence"],
                    d["track_state"], d["bbox_x1"], d["bbox_y1"], d["bbox_x2"], d["bbox_y2"]
                ])

        csv_key = f"reports/{job_id}/detection_results.csv"
        storage.save_file(csv_results_path, csv_key, content_type="text/csv")

        # Step 6: Mark job completed
        execute_query(
            """
            UPDATE analysis_jobs 
            SET status = 'completed', stage = 'Analysis Complete', progress_pct = 100.0,
                annotated_video_key = ?, json_results_key = ?, csv_results_key = ?,
                summary_json = ?, completed_at = ?
            WHERE id = ?
            """,
            (
                annotated_key,
                json_key,
                csv_key,
                json.dumps(summary_payload),
                time.time(),
                job_id
            )
        )
        logger.info(f"[VideoLab COMPLETE] job_id={job_id} tracks={len(unique_tracks)} detections={len(all_detections_records)}")

    except Exception as e:
        logger.error(f"[VideoLab FAILED] job_id={job_id} error={e}", exc_info=True)
        execute_query(
            "UPDATE analysis_jobs SET status = 'failed', stage = 'Analysis Failed', error_message = ? WHERE id = ?",
            (str(e), job_id)
        )
    finally:
        clear_job_cancellation(job_id)
        # Notify queue that slot is released
        get_job_queue().notify_job_finished(job_id)

def shutil_which(cmd: str) -> Optional[str]:
    import shutil
    return shutil.which(cmd)

class VideoLabJobQueue:
    """
    Durable, concurrency-bounded background worker queue.
    Prevents CPU/RAM exhaustion by limiting simultaneous video inference jobs.
    Recovers orphaned jobs gracefully across server restarts.
    """
    def __init__(self, max_concurrency: Optional[int] = None):
        self.max_concurrency = max_concurrency or int(os.getenv("MAX_CONCURRENT_ANALYSIS_JOBS", "1"))
        self.active_jobs: Dict[str, threading.Thread] = {}
        self.lock = threading.Lock()
        self._interval_map: Dict[str, int] = {}
        self._worker_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()

    def start(self):
        with self.lock:
            if self._worker_thread is None or not self._worker_thread.is_alive():
                self._stop_event.clear()
                self._worker_thread = threading.Thread(
                    target=self._queue_dispatcher_loop,
                    daemon=True,
                    name="VideoLabQueueDispatcher"
                )
                self._worker_thread.start()
                logger.info(f"[VideoLabQueue] Started dispatcher with max_concurrency={self.max_concurrency}")

    def recover_orphaned_jobs(self):
        """
        Marks any lingering 'processing' jobs as failed/interrupted on startup.
        """
        try:
            rows = execute_query(
                "SELECT id, filename FROM analysis_jobs WHERE status = 'processing'",
                fetch=True
            )
            for r in (rows or []):
                j_id = r["id"]
                logger.warning(f"[VideoLabQueue] Recovering orphaned job {j_id} ({r.get('filename')}) from prior crash/restart")
                execute_query(
                    """
                    UPDATE analysis_jobs 
                    SET status = 'failed', stage = 'Interrupted by server restart',
                        error_message = 'Job was interrupted by a container restart. Please click re-submit or re-upload.'
                    WHERE id = ?
                    """,
                    (j_id,)
                )
        except Exception as e:
            logger.error(f"[VideoLabQueue] Error recovering orphaned jobs: {e}")

    def enqueue(self, job_id: str, frame_interval: int = 15):
        with self.lock:
            self._interval_map[job_id] = frame_interval
            if len(self.active_jobs) >= self.max_concurrency:
                execute_query(
                    "UPDATE analysis_jobs SET status = 'queued', stage = 'Queued: Waiting for previous video to complete' WHERE id = ?",
                    (job_id,)
                )
                logger.info(f"[VideoLabQueue] Job {job_id} queued (active={len(self.active_jobs)}/{self.max_concurrency})")
            self._trigger_dispatch_locked()

    def notify_job_finished(self, job_id: str):
        with self.lock:
            self.active_jobs.pop(job_id, None)
            self._interval_map.pop(job_id, None)
            logger.info(f"[VideoLabQueue] Job {job_id} slot released. Active remaining: {len(self.active_jobs)}")
            self._trigger_dispatch_locked()

    def _trigger_dispatch_locked(self):
        while len(self.active_jobs) < self.max_concurrency:
            row = execute_query(
                "SELECT id FROM analysis_jobs WHERE status = 'queued' ORDER BY created_at ASC LIMIT 1",
                fetch=True,
                fetch_one=True
            )
            if not row:
                break
            next_id = row["id"]
            if next_id in self.active_jobs:
                break
            interval = self._interval_map.get(next_id, 15)
            t = threading.Thread(
                target=run_video_lab_analysis,
                args=(next_id, interval),
                daemon=True,
                name=f"VideoLabWorker-{next_id}"
            )
            self.active_jobs[next_id] = t
            t.start()
            logger.info(f"[VideoLabQueue] Dispatched job {next_id} to worker thread. Active: {len(self.active_jobs)}/{self.max_concurrency}")

    def _queue_dispatcher_loop(self):
        while not self._stop_event.is_set():
            try:
                with self.lock:
                    if len(self.active_jobs) < self.max_concurrency:
                        self._trigger_dispatch_locked()
            except Exception as e:
                logger.warning(f"[VideoLabQueue] Dispatcher check error: {e}")
            time.sleep(2.0)


_queue_instance: Optional[VideoLabJobQueue] = None
_queue_lock = threading.Lock()

def get_job_queue() -> VideoLabJobQueue:
    global _queue_instance
    if _queue_instance is None:
        with _queue_lock:
            if _queue_instance is None:
                _queue_instance = VideoLabJobQueue()
                _queue_instance.start()
    return _queue_instance

def start_lab_job_in_background(job_id: str, frame_interval: int = 15):
    """
    Submits job to the durable concurrency-bounded queue.
    """
    queue = get_job_queue()
    queue.enqueue(job_id, frame_interval)
