import os
import re
import time
import threading
import logging
from typing import Optional, Dict, Any, Generator, Tuple, List
from urllib.parse import urlsplit, urlunsplit
import cv2
import numpy as np

from backend.app.core.logging import logger
from backend.app.core.youtube_resolver import is_youtube_url, resolve_youtube_live_stream
from backend.app.core.stream_recorder import StreamRecorder

def sanitize_stream_url(url: str) -> str:
    """
    Masks credentials in RTSP, HTTP, or other URLs so passwords never leak
    in logs, responses, or UI.
    Example: rtsp://admin:secret123@192.168.1.100:554/stream -> rtsp://admin:***@192.168.1.100:554/stream
    """
    if not url:
        return ""
    # Check if numeric webcam index
    if str(url).strip().isdigit():
        return f"webcam_{url.strip()}"
    
    # Regex masking for URLs with user:pass@
    return re.sub(r'://([^:]+):([^@]+)@', r'://\1:***@', str(url))

def parse_source_target(source: str) -> Any:
    """
    Converts a camera source string into cv2.VideoCapture compatible target:
    - Numeric string '0', '1' -> integer index 0, 1 (local USB webcam)
    - File path or URL -> string
    """
    s = str(source).strip()
    if s.isdigit():
        return int(s)
    return s

class CameraStream:
    """
    Manages continuous, non-blocking frame capture from an RTSP stream,
    USB webcam, or local/remote video feed with automatic reconnection,
    bounded frame buffering, and thread-safe MJPEG generation.
    """
    def __init__(
        self,
        camera_id: str,
        name: str,
        stream_url: str,
        reconnect_interval: float = 3.0,
        max_reconnect_attempts: int = 10,
        loop_file_stream: bool = True,
        source_type: str = "live",
        record_stream: bool = False,
        max_storage_mb: float = 5000.0,
        retention_days: float = 7.0
    ):
        self.camera_id = camera_id
        self.name = name
        self.raw_stream_url = stream_url.strip()
        self.sanitized_url = sanitize_stream_url(self.raw_stream_url)
        self.reconnect_interval = reconnect_interval
        self.max_reconnect_attempts = max_reconnect_attempts
        self.loop_file_stream = loop_file_stream
        
        # Source identification
        if is_youtube_url(self.raw_stream_url) or source_type == "youtube_live":
            self.source_type = "youtube_live"
        else:
            self.source_type = source_type or "live"

        # Optional stream recording
        self.record_stream = record_stream
        self.recorder = StreamRecorder(
            camera_id=camera_id,
            max_storage_mb=max_storage_mb,
            retention_days=retention_days
        )

        # Connection and capture state
        self.status = "disconnected"  # "disconnected", "connecting", "connected", "error"
        self.error_message: Optional[str] = None
        self.is_connected = False
        
        # Camera specs
        self.fps: float = 0.0
        self.width: int = 0
        self.height: int = 0
        self.resolution: str = "unknown"
        self.frames_read: int = 0
        self.last_frame_time: float = 0.0
        self.connection_start_time: float = 0.0

        # Threading controls
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._frame_lock = threading.RLock()
        self._latest_frame: Optional[np.ndarray] = None
        self._latest_jpeg: Optional[bytes] = None
        self._cap: Optional[cv2.VideoCapture] = None

        # Live analysis and tracking state
        self.inference_interval: float = 0.45  # ~2.2 FPS frame sampling per camera for optimal multi-stream balance
        self.enable_analysis: bool = True
        self.use_external_scheduler: bool = False
        self._analysis_thread: Optional[threading.Thread] = None
        self._detection_lock = threading.RLock()
        self._latest_detections: List[Dict[str, Any]] = []
        self._recent_detections: List[Dict[str, Any]] = []
        self._frames_analyzed: int = 0
        self._last_analyzed_frame_time: float = 0.0
        self._last_analyzed_time: float = 0.0
        self._last_latency_ms: float = 0.0
        self._avg_latency_ms: float = 0.0
        self._total_detections_count: int = 0
        self._last_error: Optional[str] = None
        self._capture_ticks: List[float] = []
        self._analyzed_ticks: List[float] = []
        self._analyzed_w: int = 0
        self._analyzed_h: int = 0

    def connect(self, timeout: float = 10.0) -> bool:
        """
        Starts the background capture thread and waits up to timeout seconds
        for confirmation of successful stream opening.
        """
        if self._thread is not None and self._thread.is_alive():
            logger.warning(f"[{self.camera_id}] Capture thread already running.")
            return self.is_connected

        self._stop_event.clear()
        self.status = "connecting"
        self.error_message = None
        self.frames_read = 0

        self._thread = threading.Thread(
            target=self._capture_worker,
            name=f"CaptureWorker-{self.camera_id}",
            daemon=True
        )
        self._thread.start()

        # Wait for confirmation or timeout
        start = time.time()
        while time.time() - start < timeout:
            if self.is_connected:
                logger.info(f"[{self.camera_id}] Stream connected successfully ({self.resolution} @ {self.fps:.1f} fps).")
                if self.enable_analysis:
                    self._start_analysis()
                return True
            if self.status == "error":
                logger.warning(f"[{self.camera_id}] Stream connection failed: {self.error_message}")
                return False
            time.sleep(0.1)

        if not self.is_connected:
            self.status = "error"
            if not self.error_message:
                self.error_message = f"Connection timed out after {timeout}s"
            logger.warning(f"[{self.camera_id}] Stream connection timed out: {self.error_message}")
            return False

        return self.is_connected

    def disconnect(self):
        """
        Stops capture worker, releases VideoCapture resources, and marks disconnected.
        """
        logger.info(f"[{self.camera_id}] Disconnecting stream...")
        self._stop_event.set()
        
        self._stop_analysis()
        try:
            self.recorder.stop()
        except Exception as e:
            logger.warning(f"[{self.camera_id}] Error stopping stream recorder: {e}")

        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=3.0)
            self._thread = None

        self._cleanup_cap()
        with self._frame_lock:
            self._latest_frame = None
            self._latest_jpeg = None

        try:
            from backend.app.core.detector import get_detector
            get_detector().reset_tracker(self.camera_id)
        except Exception as e:
            logger.warning(f"[{self.camera_id}] Error resetting tracker on disconnect: {e}")

        try:
            from backend.app.core.live_indexer import get_live_indexer
            get_live_indexer().finalize_camera_stream(self.camera_id)
        except Exception as e:
            logger.warning(f"[{self.camera_id}] Error finalizing live indexer on disconnect: {e}")

        with self._detection_lock:
            self._latest_detections.clear()

        self.status = "disconnected"
        self.is_connected = False
        logger.info(f"[{self.camera_id}] Stream disconnected and resources released.")

    def get_fresh_frame_for_analysis(self) -> Optional[Tuple[np.ndarray, int, float]]:
        """
        Thread-safe, non-blocking fetch of the latest available frame for inference.
        Returns None if no new frame has arrived or if inference interval hasn't elapsed.
        """
        with self._frame_lock:
            if self._latest_frame is None or not self.is_connected:
                return None
            if self.last_frame_time <= self._last_analyzed_frame_time:
                return None
            now = time.time()
            if now - self._last_analyzed_time < self.inference_interval:
                return None
            self._last_analyzed_frame_time = self.last_frame_time
            return (self._latest_frame.copy(), self.frames_read, self.last_frame_time)

    def record_analysis_results(
        self,
        formatted_dets: List[Dict[str, Any]],
        latency_ms: float,
        analyzed_w: int,
        analyzed_h: int,
        timestamp: float
    ):
        """Thread-safe recording of inference completions and tracking state."""
        with self._detection_lock:
            self._latest_detections = formatted_dets
            self._frames_analyzed += 1
            self._last_analyzed_time = timestamp
            self._last_latency_ms = latency_ms
            self._analyzed_w = analyzed_w
            self._analyzed_h = analyzed_h
            self._last_error = None
            if self._avg_latency_ms == 0.0:
                self._avg_latency_ms = latency_ms
            else:
                self._avg_latency_ms = 0.85 * self._avg_latency_ms + 0.15 * latency_ms
            self._total_detections_count += len(formatted_dets)
            for d in formatted_dets:
                self._recent_detections.append(d)
            if len(self._recent_detections) > 100:
                self._recent_detections = self._recent_detections[-100:]
            self._analyzed_ticks.append(timestamp)
            if len(self._analyzed_ticks) > 30:
                self._analyzed_ticks = self._analyzed_ticks[-30:]

    def record_analysis_error(self, error_msg: str):
        with self._detection_lock:
            self._last_error = error_msg

    def _compute_capture_fps(self) -> float:
        now = time.time()
        with self._frame_lock:
            recent = [t for t in self._capture_ticks if now - t <= 3.0]
            if len(recent) >= 2:
                duration = recent[-1] - recent[0]
                if duration > 0.1:
                    return round((len(recent) - 1) / duration, 1)
        return round(self.fps, 1) if self.fps > 0 else 0.0

    def _compute_inference_fps(self) -> float:
        now = time.time()
        with self._detection_lock:
            recent = [t for t in self._analyzed_ticks if now - t <= 3.0]
            if len(recent) >= 2:
                duration = recent[-1] - recent[0]
                if duration > 0.1:
                    return round((len(recent) - 1) / duration, 1)
            elif self._avg_latency_ms > 0:
                return round(1000.0 / self._avg_latency_ms, 1)
        return 0.0

    def _start_analysis(self):
        if self.use_external_scheduler:
            return
        if self._analysis_thread is not None and self._analysis_thread.is_alive():
            return
        self._analysis_thread = threading.Thread(
            target=self._analysis_worker,
            name=f"AnalysisWorker-{self.camera_id}",
            daemon=True
        )
        self._analysis_thread.start()
        logger.info(f"[{self.camera_id}] Live detection & tracking worker started (sampling interval={self.inference_interval}s).")

    def _stop_analysis(self):
        if self._analysis_thread is not None and self._analysis_thread.is_alive():
            self._analysis_thread.join(timeout=2.0)
            self._analysis_thread = None
            logger.info(f"[{self.camera_id}] Live detection worker stopped.")

    def _analysis_worker(self):
        """
        Continuous inference loop: fetches latest frame, runs YOLO-World detection +
        ByteTrack tracking with per-camera isolation, and updates live structured detection state.
        """
        from backend.app.core.detector import get_detector, DEFAULT_CLASSES
        detector = get_detector()

        while not self._stop_event.is_set():
            loop_start = time.time()
            if not self.is_connected:
                time.sleep(0.1)
                continue

            frame_data = self.get_fresh_frame_for_analysis()
            if frame_data is None:
                time.sleep(0.04)
                continue

            frame, frame_number, frame_timestamp = frame_data

            try:
                t0 = time.time()
                fh, fw = frame.shape[:2]
                raw_detections = detector.track_frame(
                    frame=frame,
                    classes=DEFAULT_CLASSES,
                    camera_id=self.camera_id,
                    is_live=True
                )
                latency_ms = (time.time() - t0) * 1000.0
                now = time.time()

                formatted_dets = []
                for det in raw_detections:
                    formatted_dets.append({
                        "camera_id": self.camera_id,
                        "timestamp": round(now, 3),
                        "frame_number": frame_number,
                        "class": det["label"],
                        "label": det["label"],
                        "confidence": round(float(det["confidence"]), 3),
                        "raw_confidence": round(float(det.get("raw_confidence", det["confidence"])), 3),
                        "bbox": [round(float(v), 1) for v in det["bbox"]],
                        "track_id": det.get("track_id"),
                        "track_state": det.get("track_state", "confirmed" if det.get("track_id") is not None else "tentative"),
                        "is_predicted": det.get("is_predicted", False),
                        "source_type": self.source_type
                    })

                self.record_analysis_results(formatted_dets, latency_ms, fw, fh, now)

                # Pass detections to persistent live event indexer
                try:
                    from backend.app.core.live_indexer import get_live_indexer
                    get_live_indexer().process_frame_detections(
                        camera_id=self.camera_id,
                        frame=frame,
                        detections=raw_detections,
                        frame_number=frame_number,
                        timestamp=now
                    )
                except Exception as indexer_err:
                    logger.warning(f"[{self.camera_id}] Live event indexer error: {indexer_err}")

                # Pass detections to AlertEngine for real-time rule evaluation
                try:
                    from backend.app.core.alert_engine import get_alert_engine
                    get_alert_engine().evaluate_frame_detections(
                        camera_id=self.camera_id,
                        camera_name=self.name,
                        frame_number=frame_number,
                        timestamp=now,
                        detections=formatted_dets,
                        frame_width=fw,
                        frame_height=fh
                    )
                except Exception as alert_err:
                    logger.warning(f"[{self.camera_id}] Alert evaluation error: {alert_err}")

            except Exception as e:
                self.record_analysis_error(str(e))
                logger.warning(f"[{self.camera_id}] Live inference error: {e}")

            elapsed = time.time() - loop_start
            sleep_time = max(0.01, self.inference_interval - elapsed)
            time.sleep(sleep_time)

    def _cleanup_cap(self):
        if self._cap is not None:
            try:
                self._cap.release()
            except Exception as e:
                logger.warning(f"[{self.camera_id}] Error releasing capture: {e}")
            self._cap = None

    def _capture_worker(self):
        """
        Continuous frame capture loop with reconnection support.
        """
        reconnect_attempts = 0

        while not self._stop_event.is_set():
            try:
                self.status = "connecting"
                # Check if another active stream already has this exact URL to prevent duplicate TLS sockets
                from backend.app.core.stream_manager import get_stream_manager
                mgr = get_stream_manager()
                shared_parent = None
                for sid, s in mgr._streams.items():
                    if sid != self.camera_id and s.raw_stream_url == self.raw_stream_url and s.is_connected:
                        shared_parent = s
                        break

                if shared_parent:
                    logger.info(f"[{self.camera_id}] Reusing stream frame feed from [{shared_parent.camera_id}] (avoids duplicate TLS session).")
                    self.is_connected = True
                    self.status = "connected"
                    self.error_message = None
                    self.fps = shared_parent.fps
                    self.width = shared_parent.width
                    self.height = shared_parent.height
                    self.resolution = shared_parent.resolution
                    self.connection_start_time = time.time()

                    last_parent_frame_time = 0.0
                    while not self._stop_event.is_set():
                        if not shared_parent.is_connected:
                            logger.info(f"[{self.camera_id}] Shared feed parent [{shared_parent.camera_id}] disconnected. Halting.")
                            break
                        p_frame = shared_parent.get_latest_frame()
                        p_time = shared_parent.last_frame_time
                        if p_frame is not None and p_time != last_parent_frame_time:
                            last_parent_frame_time = p_time
                            with self._frame_lock:
                                self._latest_frame = p_frame
                                self.last_frame_time = p_time
                                self.frames_read += 1
                                self._latest_jpeg = None
                        time.sleep(0.04)
                    break

                # Resolve YouTube Live HLS stream if applicable
                if self.source_type == "youtube_live" or is_youtube_url(self.raw_stream_url):
                    ok, live_target, err = resolve_youtube_live_stream(self.raw_stream_url, timeout=12.0)
                    if not ok or not live_target:
                        self.status = "error"
                        self.error_message = err or f"Could not resolve live stream from {self.sanitized_url}"
                        logger.warning(f"[{self.camera_id}] YouTube Live resolution failed: {self.error_message}")
                        break
                    target = live_target
                else:
                    target = parse_source_target(self.raw_stream_url)
                
                # Configure OpenCV VideoCapture
                cap = cv2.VideoCapture(target)
                if isinstance(target, str) and (target.startswith("rtsp://") or ".m3u8" in target):
                    # Low latency buffer size for network/HLS streams
                    cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)

                if not cap.isOpened():
                    raise ConnectionError(f"Failed to open video source: {self.sanitized_url}")

                # Read test frame to verify actual readability
                ret, test_frame = cap.read()
                if not ret or test_frame is None:
                    cap.release()
                    raise ConnectionError(f"Unable to read initial frame from {self.sanitized_url}")

                self._cap = cap
                self.is_connected = True
                self.status = "connected"
                self.error_message = None
                reconnect_attempts = 0
                self.connection_start_time = time.time()

                raw_fps = cap.get(cv2.CAP_PROP_FPS)
                self.fps = float(raw_fps) if (raw_fps and raw_fps > 0 and raw_fps < 120) else 25.0
                h, w = test_frame.shape[:2]
                self.width, self.height = w, h
                self.resolution = f"{w}x{h}"

                with self._frame_lock:
                    self._latest_frame = test_frame.copy()
                    _, buf = cv2.imencode(".jpg", test_frame, [cv2.IMWRITE_JPEG_QUALITY, 80])
                    self._latest_jpeg = buf.tobytes()
                    self.last_frame_time = time.time()
                    self.frames_read += 1

                if self.record_stream:
                    self.recorder.start(width=w, height=h)

                fps_delay = 1.0 / self.fps if self.fps > 0 else 0.04
                is_file_target = isinstance(target, str) and os.path.exists(target)

                # Continuous read loop
                while not self._stop_event.is_set():
                    loop_start = time.time()
                    ret, frame = cap.read()
                    
                    if not ret or frame is None:
                        if is_file_target and self.loop_file_stream and not self._stop_event.is_set():
                            # Loop test video to simulate endless CCTV feed
                            cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                            continue
                        logger.warning(f"[{self.camera_id}] Frame read failed or stream ended.")
                        break

                    # Downscale frame if resolution is larger than 1280px to save RAM and GPU memory
                    fh, fw = frame.shape[:2]
                    if fw > 1280:
                        s = 1280.0 / fw
                        frame = cv2.resize(frame, (1280, int(fh * s)))

                    now = time.time()
                    with self._frame_lock:
                        self._latest_frame = frame
                        self.last_frame_time = now
                        self.frames_read += 1
                        self._capture_ticks.append(now)
                        if len(self._capture_ticks) > 50:
                            self._capture_ticks = self._capture_ticks[-50:]
                        # Lazily clear old jpeg; generate on demand or on sample
                        self._latest_jpeg = None

                    if self.record_stream:
                        self.recorder.write_frame(frame, now)

                    # Throttle capture loop to smooth ~12-15 FPS for network streams, or natural FPS for file streams
                    elapsed = time.time() - loop_start
                    target_delay = fps_delay if is_file_target else 0.08
                    sleep_time = max(0.005, target_delay - elapsed)
                    time.sleep(sleep_time)

            except Exception as e:
                self.status = "error"
                self.error_message = str(e)
                self.is_connected = False
                logger.warning(f"[{self.camera_id}] Capture exception: {e}")

            self._cleanup_cap()
            self.is_connected = False

            if self._stop_event.is_set():
                break

            # Attempt reconnection if not stopped
            reconnect_attempts += 1
            if self.max_reconnect_attempts > 0 and reconnect_attempts > self.max_reconnect_attempts:
                self.status = "error"
                self.error_message = f"Exceeded maximum reconnection attempts ({self.max_reconnect_attempts})"
                logger.error(f"[{self.camera_id}] Max reconnection attempts reached. Halting.")
                break

            self.status = "connecting"
            logger.info(f"[{self.camera_id}] Reconnecting in {self.reconnect_interval}s (attempt {reconnect_attempts})...")
            # Sleep in small increments to respond quickly to stop_event
            sleep_end = time.time() + self.reconnect_interval
            while time.time() < sleep_end and not self._stop_event.is_set():
                time.sleep(0.2)

        self.status = "disconnected"
        self.is_connected = False

    def get_latest_frame(self) -> Optional[np.ndarray]:
        """
        Returns a thread-safe copy of the latest captured frame, or None if unavailable.
        """
        with self._frame_lock:
            if self._latest_frame is not None:
                return self._latest_frame.copy()
            return None

    def get_latest_jpeg(self, quality: int = 75) -> Optional[bytes]:
        """
        Returns JPEG encoded bytes of the latest frame for MJPEG streaming.
        """
        with self._frame_lock:
            if self._latest_frame is None:
                return None
            if self._latest_jpeg is not None:
                return self._latest_jpeg
            ret, buf = cv2.imencode(".jpg", self._latest_frame, [cv2.IMWRITE_JPEG_QUALITY, quality])
            if ret:
                self._latest_jpeg = buf.tobytes()
                return self._latest_jpeg
            return None

    def generate_mjpeg_stream(self, fps_limit: float = 15.0) -> Generator[bytes, None, None]:
        """
        Yields multipart/x-mixed-replace MJPEG bytes for HTTP streaming.
        """
        frame_interval = 1.0 / max(1.0, min(fps_limit, 30.0))
        boundary = b"--frame\r\nContent-Type: image/jpeg\r\n\r\n"

        while not self._stop_event.is_set():
            start_t = time.time()
            jpeg_bytes = self.get_latest_jpeg()
            
            if jpeg_bytes is not None:
                yield boundary + jpeg_bytes + b"\r\n"
            else:
                # If frame not ready yet, send a blank placeholder
                blank = np.zeros((360, 640, 3), dtype=np.uint8)
                cv2.putText(blank, f"{self.name}: {self.status.upper()}", (30, 180),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.7, (200, 200, 200), 2)
                _, buf = cv2.imencode(".jpg", blank)
                yield boundary + buf.tobytes() + b"\r\n"

            elapsed = time.time() - start_t
            sleep_t = max(0.01, frame_interval - elapsed)
            time.sleep(sleep_t)

    def get_status(self) -> Dict[str, Any]:
        """
        Returns camera status dictionary without leaking raw credentials,
        with clearly decoupled preview vs inference metrics and pipeline health breakdown.
        """
        now = time.time()
        uptime = round(now - self.connection_start_time, 1) if (self.is_connected and self.connection_start_time > 0) else 0.0
        c_fps = self._compute_capture_fps()
        i_fps = self._compute_inference_fps()
        with self._detection_lock:
            det_count = len(self._latest_detections)
            frames_analyzed = self._frames_analyzed
            lat = self._last_latency_ms
            avg_lat = self._avg_latency_ms
            active_tracks = len([d for d in self._latest_detections if d.get("track_id") is not None and d.get("track_state") == "confirmed"])
            last_err = self._last_error
            analyzed_w = self._analyzed_w if getattr(self, "_analyzed_w", 0) > 0 else (self.width or 1280)
            analyzed_h = self._analyzed_h if getattr(self, "_analyzed_h", 0) > 0 else (self.height or 720)

        # Disaggregated pipeline health
        preview_working = bool(self.is_connected and (now - self.last_frame_time < 3.0 or self.frames_read > 0))
        frames_reaching_detector = bool(self.is_connected and (now - self._last_analyzed_time < 4.0 or frames_analyzed > 0))
        inference_working = bool(self.is_connected and (last_err is None or now - self._last_analyzed_time < 4.0))
        tracking_working = bool(self.is_connected and frames_analyzed > 0)

        return {
            "camera_id": self.camera_id,
            "name": self.name,
            "status": self.status,
            "is_connected": self.is_connected,
            "error_message": self.error_message,
            "stream_url": self.sanitized_url,
            "fps": c_fps,
            "capture_fps": c_fps,
            "inference_fps": i_fps,
            "processing_fps": i_fps,
            "width": analyzed_w,
            "height": analyzed_h,
            "resolution": f"{analyzed_w}x{analyzed_h}",
            "frames_read": self.frames_read,
            "frames_analyzed": frames_analyzed,
            "dropped_frames": max(0, self.frames_read - frames_analyzed),
            "uptime_seconds": uptime,
            "source_type": self.source_type,
            "is_recording": self.recorder.is_recording,
            "recording_stats": self.recorder.get_storage_stats() if self.record_stream else None,
            "is_analyzing": self.is_connected and self.enable_analysis,
            "current_detections_count": det_count,
            "active_tracks_count": active_tracks,
            "last_latency_ms": round(lat, 1),
            "avg_latency_ms": round(avg_lat, 1),
            "pipeline_health": {
                "preview_working": preview_working,
                "frames_reaching_detector": frames_reaching_detector,
                "inference_working": inference_working,
                "tracking_working": tracking_working
            }
        }

    def get_latest_detections(self) -> Dict[str, Any]:
        """
        Returns structured detection and tracking results for the latest analyzed frame.
        """
        now = time.time()
        c_fps = self._compute_capture_fps()
        with self._detection_lock:
            i_fps = self._compute_inference_fps()
            active_tracks = len([d for d in self._latest_detections if d.get("track_id") is not None and d.get("track_state") == "confirmed"])
            analyzed_w = self._analyzed_w if getattr(self, "_analyzed_w", 0) > 0 else (self.width or 1280)
            analyzed_h = self._analyzed_h if getattr(self, "_analyzed_h", 0) > 0 else (self.height or 720)
            frames_analyzed = self._frames_analyzed
            last_err = self._last_error

        preview_working = bool(self.is_connected and (now - self.last_frame_time < 3.0 or self.frames_read > 0))
        frames_reaching_detector = bool(self.is_connected and (now - self._last_analyzed_time < 4.0 or frames_analyzed > 0))
        inference_working = bool(self.is_connected and (last_err is None or now - self._last_analyzed_time < 4.0))
        tracking_working = bool(self.is_connected and frames_analyzed > 0)

        with self._detection_lock:
            return {
                "camera_id": self.camera_id,
                "status": "active" if (self.is_connected and self.enable_analysis) else "idle",
                "timestamp": self._last_analyzed_time,
                "frame_number": self.frames_read,
                "latency_ms": round(self._last_latency_ms, 1),
                "avg_latency_ms": round(self._avg_latency_ms, 1),
                "fps": i_fps,
                "capture_fps": c_fps,
                "inference_fps": i_fps,
                "frames_analyzed": self._frames_analyzed,
                "count": len(self._latest_detections),
                "active_tracks_count": active_tracks,
                "frame_width": analyzed_w,
                "frame_height": analyzed_h,
                "pipeline_health": {
                    "preview_working": preview_working,
                    "frames_reaching_detector": frames_reaching_detector,
                    "inference_working": inference_working,
                    "tracking_working": tracking_working
                },
                "detections": list(self._latest_detections)
            }

    def get_detection_status(self) -> Dict[str, Any]:
        """
        Returns live analysis performance and tracking stats.
        """
        with self._detection_lock:
            i_fps = self._compute_inference_fps()
            active_tracks = len([d for d in self._latest_detections if d.get("track_id") is not None and d.get("track_state") == "confirmed"])
            return {
                "camera_id": self.camera_id,
                "is_analyzing": self.is_connected and self.enable_analysis,
                "frames_analyzed": self._frames_analyzed,
                "last_latency_ms": round(self._last_latency_ms, 1),
                "avg_latency_ms": round(self._avg_latency_ms, 1),
                "fps": i_fps,
                "total_detections": self._total_detections_count,
                "last_analyzed_time": self._last_analyzed_time,
                "active_tracks_count": active_tracks,
                "last_error": self._last_error
            }

    def get_recent_detections(self, limit: int = 50) -> List[Dict[str, Any]]:
        with self._detection_lock:
            return list(self._recent_detections[-limit:])



class MultiCameraInferenceScheduler:
    """
    Coordinates multi-camera inference scheduling so that:
    - Active cameras receive fair round-robin inference turns.
    - One camera cannot monopolize the GPU/MPS detector.
    - Only the freshest available frame is analyzed, eliminating latency backlogs.
    - Stalled or slow cameras immediately yield their turn without blocking healthy streams.
    - Decouples capture FPS from inference FPS.
    """
    def __init__(self, target_fps_per_cam: float = 2.5):
        self._streams: Dict[str, CameraStream] = {}
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._last_analyzed: Dict[str, float] = {}
        self.target_fps_per_cam = target_fps_per_cam
        self.min_interval = 1.0 / max(0.5, target_fps_per_cam)

    def start(self):
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run,
            name="MultiCameraInferenceScheduler",
            daemon=True
        )
        self._thread.start()
        logger.info("[Scheduler] MultiCameraInferenceScheduler background coordinator started.")

    def stop(self):
        self._stop_event.set()
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=2.0)
            self._thread = None
        with self._lock:
            self._streams.clear()
            self._last_analyzed.clear()
        logger.info("[Scheduler] MultiCameraInferenceScheduler stopped.")

    def register_stream(self, stream: CameraStream):
        with self._lock:
            stream.use_external_scheduler = True
            self._streams[stream.camera_id] = stream
            self._last_analyzed[stream.camera_id] = 0.0
        logger.info(f"[Scheduler] Registered camera '{stream.camera_id}' with inference scheduler.")

    def unregister_stream(self, camera_id: str):
        with self._lock:
            self._streams.pop(camera_id, None)
            self._last_analyzed.pop(camera_id, None)
        logger.info(f"[Scheduler] Unregistered camera '{camera_id}' from inference scheduler.")

    def _run(self):
        from backend.app.core.detector import get_detector, DEFAULT_CLASSES
        detector = get_detector()

        while not self._stop_event.is_set():
            with self._lock:
                cids = list(self._streams.keys())

            if not cids:
                time.sleep(0.04)
                continue

            processed_any = False
            for cid in cids:
                if self._stop_event.is_set():
                    break
                with self._lock:
                    stream = self._streams.get(cid)

                if not stream or not stream.is_connected or not stream.enable_analysis:
                    continue

                now = time.time()
                last_time = self._last_analyzed.get(cid, 0.0)
                if now - last_time < self.min_interval:
                    continue

                frame_data = stream.get_fresh_frame_for_analysis()
                if frame_data is None:
                    continue

                processed_any = True
                self._last_analyzed[cid] = now
                frame, frame_number, frame_timestamp = frame_data

                try:
                    t0 = time.time()
                    fh, fw = frame.shape[:2]
                    raw_detections = detector.track_frame(
                        frame=frame,
                        classes=DEFAULT_CLASSES,
                        camera_id=cid,
                        is_live=True
                    )
                    latency_ms = (time.time() - t0) * 1000.0
                    now_det = time.time()

                    formatted_dets = []
                    for det in raw_detections:
                        formatted_dets.append({
                            "camera_id": cid,
                            "timestamp": round(now_det, 3),
                            "frame_number": frame_number,
                            "class": det["label"],
                            "label": det["label"],
                            "confidence": round(float(det["confidence"]), 3),
                            "raw_confidence": round(float(det.get("raw_confidence", det["confidence"])), 3),
                            "bbox": [round(float(v), 1) for v in det["bbox"]],
                            "track_id": det.get("track_id"),
                            "track_state": det.get("track_state", "confirmed" if det.get("track_id") is not None else "tentative"),
                            "is_predicted": det.get("is_predicted", False),
                            "source_type": stream.source_type
                        })

                    stream.record_analysis_results(formatted_dets, latency_ms, fw, fh, now_det)

                    # Pass detections to persistent live event indexer
                    try:
                        from backend.app.core.live_indexer import get_live_indexer
                        get_live_indexer().process_frame_detections(
                            camera_id=cid,
                            frame=frame,
                            detections=raw_detections,
                            frame_number=frame_number,
                            timestamp=now_det
                        )
                    except Exception as indexer_err:
                        logger.warning(f"[{cid}] Live event indexer error in scheduler: {indexer_err}")

                    # Pass detections to AlertEngine for real-time rule evaluation
                    try:
                        from backend.app.core.alert_engine import get_alert_engine
                        get_alert_engine().evaluate_frame_detections(
                            camera_id=cid,
                            camera_name=stream.name,
                            frame_number=frame_number,
                            timestamp=now_det,
                            detections=formatted_dets,
                            frame_width=fw,
                            frame_height=fh
                        )
                    except Exception as alert_err:
                        logger.warning(f"[{cid}] Alert evaluation error in scheduler: {alert_err}")

                except Exception as e:
                    stream.record_analysis_error(str(e))
                    logger.warning(f"[{cid}] Inference scheduler error: {e}")

            if not processed_any:
                time.sleep(0.01)


class StreamManager:
    """
    Central registry and lifecycle manager for all live CCTV camera streams.
    Ensures single capture thread per camera ID, clean disconnection,
    and safe multi-camera coordination.
    """
    _instance = None
    _lock = threading.Lock()

    def __init__(self):
        self._streams: Dict[str, CameraStream] = {}
        self._manager_lock = threading.Lock()
        self._scheduler = MultiCameraInferenceScheduler()
        self._scheduler.start()

    @classmethod
    def get_instance(cls) -> "StreamManager":
        with cls._lock:
            if cls._instance is None:
                cls._instance = StreamManager()
            return cls._instance

    def connect_camera(
        self,
        camera_id: str,
        name: str,
        stream_url: str,
        timeout: float = 8.0,
        loop_file_stream: bool = True,
        source_type: str = "live",
        record_stream: bool = False,
        max_storage_mb: float = 5000.0,
        retention_days: float = 7.0
    ) -> Tuple[bool, Dict[str, Any]]:
        """
        Connects a live camera or replaces an existing disconnected stream.
        Prevents duplicate concurrent connections for the same camera_id.
        """
        with self._manager_lock:
            existing = self._streams.get(camera_id)
            if existing is not None:
                if existing.is_connected:
                    logger.info(f"[StreamManager] Camera {camera_id} already connected.")
                    return True, existing.get_status()
                else:
                    # Disconnect stale/stopped instance
                    self._scheduler.unregister_stream(camera_id)
                    existing.disconnect()

            stream = CameraStream(
                camera_id=camera_id,
                name=name,
                stream_url=stream_url,
                loop_file_stream=loop_file_stream,
                source_type=source_type,
                record_stream=record_stream,
                max_storage_mb=max_storage_mb,
                retention_days=retention_days
            )
            stream.use_external_scheduler = True
            self._streams[camera_id] = stream

        # Connect outside the manager lock so other calls are not blocked
        connected = stream.connect(timeout=timeout)
        if connected:
            self._scheduler.register_stream(stream)
        return connected, stream.get_status()

    def disconnect_camera(self, camera_id: str) -> bool:
        """
        Safely disconnects and releases a camera stream.
        """
        with self._manager_lock:
            stream = self._streams.get(camera_id)
            if not stream:
                return False
            del self._streams[camera_id]

        self._scheduler.unregister_stream(camera_id)
        stream.disconnect()
        try:
            from backend.app.core.detector import get_detector
            get_detector().reset_tracker(camera_id)
        except Exception:
            pass
        return True

    def get_stream(self, camera_id: str) -> Optional[CameraStream]:
        with self._manager_lock:
            return self._streams.get(camera_id)

    def list_streams(self) -> Dict[str, Dict[str, Any]]:
        with self._manager_lock:
            return {cid: stream.get_status() for cid, stream in self._streams.items()}

    def get_all_active_detections(self) -> Dict[str, Any]:
        """
        Returns the latest detections and tracking states across all connected streams.
        """
        with self._manager_lock:
            items = list(self._streams.items())
        result = {}
        for cid, stream in items:
            if stream.is_connected:
                result[cid] = stream.get_latest_detections()
        return result

    def disconnect_all(self):
        """
        Disconnects all active cameras on shutdown.
        """
        with self._manager_lock:
            all_streams = list(self._streams.values())
            self._streams.clear()

        self._scheduler.stop()
        for stream in all_streams:
            stream.disconnect()
            try:
                from backend.app.core.detector import get_detector
                get_detector().reset_tracker(stream.camera_id)
            except Exception:
                pass

    def get_camera_detections(self, camera_id: str) -> Optional[Dict[str, Any]]:
        stream = self.get_stream(camera_id)
        if stream:
            return stream.get_latest_detections()
        return None

    def get_camera_detection_status(self, camera_id: str) -> Optional[Dict[str, Any]]:
        stream = self.get_stream(camera_id)
        if stream:
            return stream.get_detection_status()
        return None

    def get_recent_detections(self, camera_id: Optional[str] = None, limit: int = 50) -> List[Dict[str, Any]]:
        with self._manager_lock:
            if camera_id:
                streams = [self._streams[camera_id]] if camera_id in self._streams else []
            else:
                streams = list(self._streams.values())

        all_dets = []
        for s in streams:
            all_dets.extend(s.get_recent_detections(limit=limit))
        all_dets.sort(key=lambda d: d.get("timestamp", 0), reverse=True)
        return all_dets[:limit]



def get_stream_manager() -> StreamManager:
    return StreamManager.get_instance()
