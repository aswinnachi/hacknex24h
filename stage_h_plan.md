# Stage H Architecture Inspection & Optimization Plan

## 1. Current Architecture Review

### Capture & Transport
- **`CameraStream`** (`backend/app/core/stream_manager.py`):
  - Threaded capture worker per camera (`_capture_worker`) reading frames via OpenCV `VideoCapture`.
  - Bounded latest-frame buffer (`self._latest_frame`) guarded by `self._frame_lock`.
  - MJPEG generator (`generate_mjpeg_stream`) streaming JPEG frames to browser preview.

### Inference & Tracking
- **`OpenVocabularyDetector`** (`backend/app/core/detector.py`):
  - Shared global singleton instance `_detector_instance` running YOLO-World (`yolov8s-world.pt`) on Apple Silicon Metal Performance Shaders (`mps`).
  - Thread lock `self._lock` acquired around the entire `track_frame()` call.
  - Per-camera `BYTETracker` instances stored in `self._trackers[camera_id]`.
- **`CameraStream._analysis_worker`**:
  - Independent inference worker thread per connected camera stream.
  - Default inference interval = 0.25s (~4 FPS nominal per camera).

### Event Persistence & Alerting
- **`LiveEventIndexer`** (`backend/app/core/live_indexer.py`):
  - Aggregates frame detections into track episodes.
  - Asynchronous bounded queue (capacity = 1000) with dedicated `LivePersistenceWorker` thread.
- **`AlertEngine`** (`backend/app/core/alert_engine.py`):
  - Real-time rule evaluation (tripwires, polygon zones, restricted hours, object presence).
  - Evaluates in-memory and pushes alert records to bounded async persistence worker queue.

---

## 2. Suspected Bottlenecks & Critical Findings

1. **Inference Lock Contention (Shared `_lock`)**:
   - Each camera stream spawns an independent thread that independently calls `detector.track_frame()`.
   - All camera threads compete for `self._lock` in `detector.py`.
   - Under multi-camera load (2-3+ cameras), lock contention causes unpredictable scheduling delays, tail latency spikes (p95/max), and potential camera starvation where one camera thread re-acquires the lock while another waits.

2. **YOLO-World Class Vocabulary Thrashing**:
   - In `stream_manager.py`, live cameras query `DEFAULT_CLASSES` (15 classes).
   - In `video.py`, uploaded video processing queries `detection_classes` (18 classes).
   - Whenever classes differ, `detector.set_classes()` is called, forcing YOLO-World to recompute text clip embeddings on the GPU/CPU for every frame alternated between live streams and uploaded jobs.
   - Synchronizing class sets eliminates prompt re-embedding overhead.

3. **Missing `camera_id` in Uploaded Video Processing**:
   - `video.py` calls `detector.track_frame(frame, detection_classes)` without passing `camera_id`, which risks tracker state pollution against live camera feeds.

4. **Inference Resolution & PyTorch MPS Execution**:
   - `self.model.track(frame, ...)` currently relies on default image size handling.
   - Explicit `imgsz=640` or `imgsz=480` can provide deterministic tensor shapes and eliminate shape reallocation overhead on MPS.

5. **JPEG Encoding Overhead for Preview**:
   - In `_capture_worker`, `self._latest_jpeg = None` clears the cached JPEG on every raw frame.
   - If MJPEG stream requests a frame at 15 FPS while capture runs at 25-30 FPS, encoding happens repeatedly unless cached efficiently.

---

## 3. Baseline Metrics to Collect

The benchmark test harness (`tests/benchmark_multicam.py`) will measure steady-state performance across:
- **Scenario 1**: 1 Live Camera
- **Scenario 2**: 2 Simultaneous Live Cameras
- **Scenario 3**: 3 Simultaneous Live Cameras
- **Scenario 4**: Live Cameras + Concurrent Uploaded Video Processing

For each scenario, the following metrics will be collected:
1. **Camera Capture FPS**: Real capture frame rate per camera.
2. **Inference FPS**: Actual inference frames processed per second per camera.
3. **Aggregate Inference FPS**: Total system detection throughput across all streams.
4. **Inference Latency**: p50 (median), p95, and maximum latency in milliseconds.
5. **Frame Age**: Elapsed time from frame capture to detection result completion.
6. **Persistence & Alert Latency**: Delay between detection output and database write.
7. **Resource Utilization**: CPU usage %, RAM consumption (MB), and MPS device status.
8. **Frame Accounting**: Total frames captured, frames analyzed, frames skipped, and errors.
9. **Tracking Stability**: Unique track IDs observed and track continuity.
10. **MJPEG Preview Responsiveness**: Preview frame delivery rate.

---

## 4. Minimal Optimization Plan (Phase 3)

1. **Centralized / Coordinated Fair Inference Dispatcher**:
   - Replace uncoordinated competing camera worker threads with a coordinated round-robin or priority inference scheduler, OR a fair scheduling queue that prevents lock starvation and ensures equal GPU time slices across all active cameras.
2. **Harmonized Vocabulary Caching**:
   - Align `detection_classes` across live and uploaded pipelines so YOLO-World text prompt embeddings remain cached in VRAM/MPS memory without repeated re-computation.
3. **Deterministic Input Resolution on MPS**:
   - Pass explicit `imgsz=640` (or benchmark `480` / `640`) to `model.track()` to avoid internal tensor resizing variability.
4. **Camera ID Isolation in Uploaded Jobs**:
   - Pass `camera_id` in `video.py` to maintain strict ByteTrack tracker isolation between uploaded jobs and live cameras.
5. **JPEG Encoding Cache Optimization**:
   - Reuse recent encoded JPEG buffers within frame intervals to reduce unnecessary image compression cycles.
