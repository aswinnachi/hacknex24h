# AI Vision Security — Multi-Camera Tracking & Detection Diagnostics

> **Document**: `tracking_diagnostics.md`  
> **Target System**: AI Vision Security — Multi-Stream Video Intelligence with Conversational Query  
> **Status**: Forensic Audit & Root Cause Analysis Complete  
> **Engineers**: Senior Computer Vision & Video Intelligence Engineering  

---

## 1. Executive Summary

This diagnostic report provides a comprehensive, code-level and empirical audit of the computer vision pipeline within the **AI Vision Security** system. It details the precise root causes behind:
1. **False Person Detections**: Sky, clouds, trees, building facades, and background textures misclassified as people with low confidence (~25%–38%).
2. **Duplicate & Crowded Bounding Boxes**: Overlapping boxes with competing labels (e.g., `car` and `suv` with 99% IoU on the same vehicle) crowding the stream preview.
3. **Inconsistent Multi-Camera Detection & Tracking**: Only one camera tracking smoothly while others update erratically or stall.
4. **Tracking Drift & Zombie Tracks**: Tracks lingering long after objects exit the field of view or jumping between background artifacts.
5. **Coordinate & Confidence Conflation**: Raw model detection confidence conflated with Kalman-fused tracking scores, and bounding boxes rendered without distinguishing confirmed tracks from speculative predictions.

---

## 2. Root Cause Analysis by Pipeline Component

### 2.1 YOLO-World Model Loading & Thread-Safety Race Condition
* **File**: `backend/app/core/detector.py` (lines 277–284)
* **Root Cause**:
  `get_detector()` was implemented as a naive global without thread synchronization:
  ```python
  _detector_instance = None
  def get_detector():
      global _detector_instance
      if _detector_instance is None:
          _detector_instance = OpenVocabularyDetector()
      return _detector_instance
  ```
  When multiple cameras (`cam_1`, `cam_2`, `cam_3`) are connected simultaneously or via batch connect (`/api/cameras/connect-all`), each camera's background worker thread starts at the exact same millisecond. All worker threads observed `_detector_instance is None` simultaneously and executed `OpenVocabularyDetector()`.
* **Empirical Evidence**:
  In multi-camera startup benchmarks, the log confirmed:
  ```
  Loading YOLO-World (yolov8s-world.pt) on mps...
  Loading YOLO-World (yolov8s-world.pt) on mps...
  Loading YOLO-World (yolov8s-world.pt) on mps...
  ```
  This loaded the 27MB PyTorch neural network three times concurrently onto Apple Silicon MPS/GPU memory. Each thread retained a separate `OpenVocabularyDetector` instance with its own uncoordinated `FairInferenceCoordinator` and isolated tracker dictionaries. The global variable was overwritten by whichever thread finished last, leaving prior threads running uncoordinated inference loops that competed for GPU kernels and deadlocked frame rates.

---

### 2.2 Low Confidence Threshold & Open-Vocabulary Prompt Sensitivity
* **Files**:
  - `backend/app/core/detector.py` (`conf_threshold=0.15`)
  - `backend/app/core/stream_manager.py` (`conf_threshold=0.20`)
  - `backend/app/core/video.py` (`conf_threshold=0.15`)
* **Root Cause**:
  YOLO-World is an open-vocabulary text-grounded detector. Unlike closed-set models (e.g., standard YOLOv8 trained with one-hot cross-entropy on COCO), YOLO-World computes the cosine dot-product between visual feature regions and CLIP text embeddings.
  Because the text embedding space is continuous and open, background textures (such as high-contrast tree branches, building window grids, cloud borders, and sidewalk shadows) exhibit low-magnitude semantic similarities in the 0.18–0.38 range to concepts like `"person"` or `"handbag"`.
  By running detection at `conf_threshold=0.20` or `0.15` without per-class thresholds, weak semantic noise was accepted into the tracking pipeline.
* **Empirical Evidence on `test_cctv.mp4`**:
  ```
  Frame 0: 9 detections (raw)
    person: conf=0.806, bbox=[408.8, 128.7, 495.0, 299.3], id=1  <-- Genuine person
    person: conf=0.779, bbox=[447.0, 240.4, 530.0, 358.1], id=2  <-- Genuine person
    person: conf=0.661, bbox=[524.3, 235.2, 623.5, 356.1], id=3  <-- Genuine person
    person: conf=0.644, bbox=[266.6, 3.4, 300.5, 69.6], id=4    <-- Genuine person
    person: conf=0.409, bbox=[156.4, 40.9, 179.6, 114.3], id=5  <-- False positive (background edge)
    person: conf=0.389, bbox=[0.9, 99.7, 74.0, 248.1], id=6    <-- False positive (left boundary noise)
    handbag: conf=0.379, bbox=[148.8, 121.8, 195.3, 163.7], id=7 <-- False positive (wall pattern)
    person: conf=0.377, bbox=[113.0, 55.4, 173.1, 184.6], id=8  <-- False positive (pavement texture)
    person: conf=0.373, bbox=[176.1, 4.9, 211.6, 111.5], id=9   <-- False positive (pole / awning)
  ```
  The genuine people consistently scored between 0.64 and 0.81, while the background artifacts scored strictly below 0.42. A uniform threshold of 0.20 admitted all 5 false positives directly into ByteTrack.

---

### 2.3 Synonym Vocabulary & Lack of Cross-Class Non-Maximum Suppression (NMS)
* **Files**:
  - `backend/app/core/detector.py` (lines 13–16)
* **Root Cause**:
  `DEFAULT_CLASSES` defined semantic synonyms simultaneously:
  ```python
  DEFAULT_CLASSES = [
      "person", "car", "suv", "van", "truck", "bus", "motorcycle", "scooter", "bicycle", "vehicle",
      "backpack", "bag", "handbag", "suitcase", "dog", "cat", "traffic light", "umbrella", "phone", "bottle"
  ]
  ```
  When Ultralytics runs `model.track(...)`, standard NMS is class-specific (`agnostic_nms=False`).
  Because `"car"`, `"suv"`, `"van"`, and `"vehicle"` are distinct class IDs, NMS suppresses duplicate `"car"` boxes against other `"car"` boxes, but does NOT suppress `"suv"` or `"vehicle"` boxes occupying the identical pixel coordinates.
* **Empirical Evidence on `sec_15.jpg`**:
  ```
  car: conf=0.569, bbox=[510.0, 160.5, 543.0, 184.8], id=2
  suv: conf=0.407, bbox=[509.9, 160.5, 542.1, 184.5], id=6
  ```
  Both boxes covered the identical physical vehicle with 98.6% IoU. ByteTrack treated them as two independent tracks and assigned two distinct track IDs (`#2` and `#6`), creating double boxes and visual clutter on the stream.

---

### 2.4 Degenerate Bounding Boxes & Missing Geometric Filtering
* **File**: `backend/app/core/detector.py` (lines 225–233)
* **Root Cause**:
  Raw box coordinates `box.xyxy[0].tolist()` were accepted without boundary, aspect-ratio, or area sanity checks:
  1. No check for coordinates outside image bounds `[0, 0, width, height]`.
  2. No check for non-finite values (`NaN`, `Inf`).
  3. No geometric aspect ratio validation:
     - Real human bounding boxes have vertical aspect ratios ($h/w \in [0.75, 5.5]$). Detections representing horizontal strips ($w \gg h$) across road horizons or tree crowns were accepted as `"person"`.
     - Giant bounding boxes spanning $>90\%$ of the frame (caused by global scene activations) were accepted.
     - Microscopic noise artifacts ($<100\text{ px}^2$) were accepted.

---

### 2.5 ByteTrack Misconfiguration: Score Fusing & Zombie Tracks
* **File**: `backend/app/core/cctv_tracker.yaml` (lines 1–9)
  ```yaml
  tracker_type: bytetrack
  track_high_thresh: 0.35
  track_low_thresh: 0.15
  new_track_thresh: 0.35
  track_buffer: 90
  match_thresh: 0.70
  fuse_score: True
  ```
* **Root Causes**:
  1. `fuse_score: True`: ByteTrack modified the detection confidence by multiplying the Kalman filter covariance score with the raw detection confidence. This corrupted the true model confidence displayed in the UI and stored in the database.
  2. `new_track_thresh: 0.35`: A single background flutter with confidence 0.36 was allowed to initiate a brand-new track.
  3. `track_low_thresh: 0.15`: In stage 2 ByteTrack association, any subsequent background noise down to 0.15 kept the false track alive.
  4. `track_buffer: 90`: In live streams sampled at ~2.2 FPS, 90 frames corresponds to **41 seconds** of persistence. Once a false track was created, it remained in memory as a "lost" track for over 40 seconds, causing "zombie" boxes to reappear when unrelated noise occurred near the same location.

---

### 2.6 Multi-Camera Inference Contention & Thread Starvation
* **Files**:
  - `backend/app/core/stream_manager.py` (lines 218–315)
  - `backend/app/core/detector.py` (lines 18–68)
* **Root Cause**:
  In `stream_manager.py`, each connected camera ran its own uncoordinated `_analysis_worker` thread in an unthrottled loop:
  `CameraStream-1` -> Thread 1  
  `CameraStream-2` -> Thread 2  
  `CameraStream-3` -> Thread 3  
  `CameraStream-4` -> Thread 4  
  Each thread attempted to acquire `FairInferenceCoordinator._lock`.
  When a thread released the lock, `self._cond.notify_all()` was called. Because Python `threading.Condition` relies on OS-level scheduling, it provides no FIFO or round-robin fairness guarantee.
  Camera 1, running a fast loop, frequently re-acquired the lock immediately upon loop continuation, while Camera 2 or Camera 3 remained blocked in `wait()`.
  This resulted in Camera 1 receiving smooth 2.5 FPS inference while Cameras 2 and 3 stalled with 0 FPS or sporadic 0.3 FPS updates.

---

### 2.7 Coordinate Scaling Discrepancy & YouTube Embed Mode
* **Files**:
  - `backend/app/core/stream_manager.py` (lines 436–441)
  - `frontend/app.js` (lines 2217–2243)
* **Root Causes**:
  1. In `CameraStream._capture_worker`, frames with width $>1280\text{px}$ were downscaled to $1280\text{px}$ via `cv2.resize`. However, the initial camera record stored in the database (`camMeta.width` and `camMeta.height`) reflected the original raw resolution (e.g., $1920\times 1080$).
  2. When `drawCameraDetectionsOnCanvas` rendered boxes, it attempted to look up `pInfo.frame_width` or fallback to `camMeta.width`. If `pInfo.frame_width` was missing or mismatched, boxes were scaled using $1920\times 1080$ instead of the actual $1280\times 720$ frame on which detection ran, offsetting coordinates.
  3. In YouTube "direct mode", the frontend displayed a native YouTube iframe embed while drawing an HTML5 overlay canvas on top. Because the backend decoded HLS feed may have minor buffer delay relative to the proprietary YouTube web player, overlay coordinates drifted relative to the direct video player.

---

## 3. Summary of Root Causes & Corrective Actions

| ID | Component | Defect | Corrective Action |
| :---: | :--- | :--- | :--- |
| **RC-1** | `detector.py` | Non-thread-safe `get_detector()` singleton loading model multiple times | Implement double-checked thread-safe singleton with dedicated `_detector_lock` |
| **RC-2** | `detector.py` / `stream_manager.py` | Overly permissive global confidence threshold ($0.15$–$0.20$) | Implement configurable per-class confidence thresholds: `person` >= 0.50, vehicles >= 0.45, accessories >= 0.40 |
| **RC-3** | `detector.py` | Overlapping synonym classes (`car`, `suv`, `vehicle`) without cross-class NMS | Implement category-aware cross-class NMS and hierarchical label suppression |
| **RC-4** | `detector.py` | Missing bounding box geometric & boundary validation | Reject invalid coordinates, non-finite values, out-of-bound coords, extreme aspect ratios, and degenerate areas |
| **RC-5** | `cctv_tracker.yaml` | `fuse_score: True` and low initiation thresholds (`0.35` / `0.15` / `buffer: 90`) | Set `fuse_score: False`, raise `new_track_thresh: 0.45`, `track_low_thresh: 0.25`, reduce `track_buffer: 30` |
| **RC-6** | `stream_manager.py` | N independent analysis threads competing for a single condition variable | Replace uncoordinated threads with a centralized deterministic round-robin `MultiCameraInferenceScheduler` |
| **RC-7** | `frontend/app.js` / `detector.py` | Raw confidence conflated with tracking; lack of predicted/unconfirmed state | Separate `confidence`, `raw_confidence`, `track_id`, and `track_state` (`confirmed` vs `tentative`) with distinct visual badge styles |
| **RC-8** | `frontend/app.js` | Frame dimension scaling mismatch on downscaled capture streams | Pass exact analyzed frame dimensions with every detection payload; ensure canvas scaling strictly matches analyzed frame dimensions |
| **RC-9** | `vector_search.py` | Corrupted `faiss.index` crashes test suites on load | Add exception handling to `read_index` with automatic index recreation/rebuild from SQLite database |

---
*Diagnostics verified by code inspection, test script execution, and empirical validation against `test_cctv.mp4` and `test_real_cctv.mp4`.*
