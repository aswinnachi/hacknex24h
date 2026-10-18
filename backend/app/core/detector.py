import json
import uuid
import cv2
import os
import threading
import torch
from typing import Optional, Dict, Any, List, Tuple
from backend.app.core.hardware import detect_hardware
from backend.app.core.logging import logger
from backend.app.db.database import execute_query

import math

# Unified comprehensive CCTV detection vocabulary
DEFAULT_CLASSES = [
    "person", "car", "suv", "van", "truck", "bus", "motorcycle", "scooter", "bicycle", "vehicle",
    "backpack", "bag", "handbag", "suitcase", "dog", "cat", "traffic light", "umbrella", "phone", "bottle"
]

# Configurable per-class confidence thresholds calibrated for YOLO-World open-vocabulary
DEFAULT_CONF_THRESHOLDS: Dict[str, float] = {
    "person": 0.50,
    "car": 0.45,
    "suv": 0.45,
    "van": 0.45,
    "truck": 0.45,
    "bus": 0.45,
    "vehicle": 0.45,
    "motorcycle": 0.40,
    "scooter": 0.40,
    "bicycle": 0.40,
    "backpack": 0.40,
    "bag": 0.40,
    "handbag": 0.40,
    "suitcase": 0.40,
    "dog": 0.45,
    "cat": 0.45,
    "traffic light": 0.40,
    "umbrella": 0.40,
    "phone": 0.35,
    "bottle": 0.35,
    "default": 0.40
}

def compute_iou(box1: List[float], box2: List[float]) -> float:
    """Computes Intersection-over-Union between two [x1, y1, x2, y2] boxes."""
    x1 = max(box1[0], box2[0])
    y1 = max(box1[1], box2[1])
    x2 = min(box1[2], box2[2])
    y2 = min(box1[3], box2[3])
    inter = max(0.0, x2 - x1) * max(0.0, y2 - y1)
    area1 = max(0.0, box1[2] - box1[0]) * max(0.0, box1[3] - box1[1])
    area2 = max(0.0, box2[2] - box2[0]) * max(0.0, box2[3] - box2[1])
    union = area1 + area2 - inter
    return inter / union if union > 0 else 0.0

def is_valid_detection_box(bbox: List[float], label: str, img_w: int, img_h: int) -> bool:
    """
    Rejects degenerate bounding boxes, invalid/non-finite coordinates,
    implausible aspect ratios, and full-frame global activations.
    """
    if not bbox or len(bbox) < 4:
        return False
    x1, y1, x2, y2 = bbox[:4]
    if any(math.isnan(v) or math.isinf(v) for v in (x1, y1, x2, y2)):
        return False
    
    w = x2 - x1
    h = y2 - y1
    if w < 10 or h < 14:
        return False
    area = w * h
    if area < 140:
        return False
    
    frame_area = max(1, img_w * img_h)
    if (area / frame_area) > 0.88:
        return False

    lbl = label.lower()
    if lbl == "person":
        ar = h / max(1.0, w)
        # People in CCTV are vertically oriented; reject wide horizontal strips
        if ar < 0.70 or ar > 5.5:
            return False
    elif lbl in ("car", "suv", "van", "truck", "bus", "vehicle"):
        ar = w / max(1.0, h)
        if ar < 0.35 or ar > 4.5:
            return False

    return True

def apply_cross_class_suppression(
    detections: List[Dict[str, Any]],
    iou_threshold: float = 0.60
) -> List[Dict[str, Any]]:
    """
    Suppresses overlapping duplicate detections across synonym and conflicting classes
    (e.g., 'car' vs 'vehicle' vs 'suv' on the same physical vehicle).
    """
    if len(detections) <= 1:
        return detections

    # Sort descending by confidence
    sorted_dets = sorted(detections, key=lambda d: d.get("confidence", 0.0), reverse=True)
    keep: List[Dict[str, Any]] = []
    VEHICLE_SYNONYMS = {"car", "suv", "van", "truck", "bus", "vehicle"}
    BAG_SYNONYMS = {"bag", "handbag", "backpack", "suitcase"}

    for det in sorted_dets:
        box = det["bbox"]
        lbl = det["label"].lower()
        suppress = False

        for kept in keep:
            k_box = kept["bbox"]
            k_lbl = kept["label"].lower()
            iou = compute_iou(box, k_box)

            if iou >= iou_threshold:
                # 1. Both are vehicle synonyms: keep higher confidence / more specific
                if lbl in VEHICLE_SYNONYMS and k_lbl in VEHICLE_SYNONYMS:
                    suppress = True
                    break
                # 2. Both are bag synonyms: keep higher confidence
                if lbl in BAG_SYNONYMS and k_lbl in BAG_SYNONYMS:
                    suppress = True
                    break
                # 3. Person vs vehicle high overlap: vehicle wins if vehicle conf >= person conf
                if (lbl == "person" and k_lbl in VEHICLE_SYNONYMS) or (lbl in VEHICLE_SYNONYMS and k_lbl == "person"):
                    if iou > 0.70:
                        suppress = True
                        break
                # 4. Same class or generic heavy overlap >= 0.75
                if iou >= 0.75:
                    suppress = True
                    break

        if not suppress:
            keep.append(det)

    return keep

class FairInferenceCoordinator:
    """
    Fair inference coordinator ensuring non-blocking execution across multiple cameras
    and strictly preventing live stream starvation by background batch video jobs.
    """
    def __init__(self):
        self._lock = threading.Lock()
        self._cond = threading.Condition(self._lock)
        self._active_thread: Optional[int] = None
        self._live_waiters: int = 0

    def acquire(self, is_live: bool = True):
        tid = threading.get_ident()
        with self._lock:
            if is_live:
                self._live_waiters += 1
                try:
                    while self._active_thread is not None:
                        self._cond.wait()
                finally:
                    self._live_waiters -= 1
            else:
                # Background job yields to any live streams
                while self._active_thread is not None or self._live_waiters > 0:
                    self._cond.wait()
            self._active_thread = tid

    def release(self):
        with self._lock:
            self._active_thread = None
            self._cond.notify_all()

    def inference_context(self, camera_id: str = "default", is_live: bool = True):
        class _Ctx:
            def __init__(self, coord, live):
                self.coord = coord
                self.live = live
            def __enter__(self):
                self.coord.acquire(is_live=self.live)
                return self
            def __exit__(self, exc_type, exc_val, exc_tb):
                self.coord.release()
        return _Ctx(self, is_live)

    def __enter__(self):
        self.acquire(is_live=True)
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.release()

class OpenVocabularyDetector:
    def __init__(self, model_name="yolov8s-world.pt"):
        self.hw_info = detect_hardware()
        self.device = self.hw_info.get("primary_device", "cpu")
        self.coordinator = FairInferenceCoordinator()
        self._trackers: Dict[str, Any] = {}
        self._class_cache: Dict[Tuple[str, ...], Tuple[Any, Any]] = {}
        
        logger.info(f"Loading YOLO-World ({model_name}) on {self.device}...")
        try:
            from ultralytics import YOLOWorld
            self.model = YOLOWorld(model_name)
            if self.device != "cpu":
                self.model.to(self.device)
            self.current_classes = []
            self.available = True
            
            # Pre-warm default classes into cache
            self.set_classes(DEFAULT_CLASSES)
        except ImportError:
            logger.error("Ultralytics not installed. Open-vocabulary detection is unavailable.")
            self.available = False
            self.model = None

    def set_classes(self, classes: List[str]):
        """
        Sets model vocabulary classes for open-vocabulary detection.
        Avoids redundant updates if classes have not changed.
        """
        if not self.available: return
        key = tuple(classes)
        if tuple(self.current_classes) == key:
            return

        self.model.set_classes(classes)
        self.current_classes = list(classes)

    def reset_tracker(self, camera_id: Optional[str] = None):
        """
        Resets and safely releases tracking state for a specific camera or all cameras.
        """
        self.coordinator.acquire(is_live=True)
        try:
            if camera_id:
                if camera_id in self._trackers:
                    try:
                        self._trackers[camera_id].reset()
                    except Exception:
                        pass
                    self._trackers.pop(camera_id, None)
                if hasattr(self.model, "predictor") and self.model.predictor and hasattr(self.model.predictor, "trackers"):
                    try:
                        delattr(self.model.predictor, "trackers")
                    except Exception:
                        self.model.predictor.trackers = None
            else:
                for trk in self._trackers.values():
                    try:
                        trk.reset()
                    except Exception:
                        pass
                self._trackers.clear()
                if hasattr(self, "_default_tracker") and self._default_tracker is not None:
                    try:
                        self._default_tracker.reset()
                    except Exception:
                        pass
                    self._default_tracker = None
                if hasattr(self.model, "predictor") and self.model.predictor and hasattr(self.model.predictor, "trackers"):
                    try:
                        delattr(self.model.predictor, "trackers")
                    except Exception:
                        self.model.predictor.trackers = None
        finally:
            self.coordinator.release()

    def track_frame(
        self,
        frame,
        classes=None,
        conf_threshold: Optional[float] = None,
        per_class_thresholds: Optional[Dict[str, float]] = None,
        camera_id: Optional[str] = None,
        is_live: bool = True
    ) -> List[Dict[str, Any]]:
        if not self.available or frame is None:
            return []
        if classes is None:
            classes = DEFAULT_CLASSES
        
        # Merge per-class thresholds
        thresh_map = dict(DEFAULT_CONF_THRESHOLDS)
        if per_class_thresholds:
            thresh_map.update(per_class_thresholds)
        
        # Ultralytics forward pass minimum threshold:
        # Feed candidates at min(thresh_map) - 0.05 so ByteTrack receives low-score candidates for stage-2 matching
        min_thresh = min(thresh_map.values()) if thresh_map else 0.35
        if conf_threshold is not None:
            base_conf = min(float(conf_threshold), min_thresh)
        else:
            base_conf = max(0.18, min_thresh - 0.10)

        self.coordinator.acquire(is_live=is_live)
        try:
            with torch.no_grad():
                self.set_classes(classes)
                tracker_cfg = os.path.join(os.path.dirname(__file__), "cctv_tracker.yaml")
                if not os.path.exists(tracker_cfg):
                    tracker_cfg = "bytetrack.yaml"

                # Strict per-camera tracker isolation
                if camera_id is not None:
                    if camera_id in self._trackers:
                        if hasattr(self.model, "predictor") and self.model.predictor:
                            self.model.predictor.trackers = [self._trackers[camera_id]]
                    else:
                        if hasattr(self.model, "predictor") and self.model.predictor and hasattr(self.model.predictor, "trackers") and self.model.predictor.trackers:
                            from ultralytics.trackers.byte_tracker import BYTETracker
                            template_args = self.model.predictor.trackers[0].args
                            self._trackers[camera_id] = BYTETracker(template_args)
                            self.model.predictor.trackers = [self._trackers[camera_id]]
                else:
                    # Isolated default tracker for un-keyed calls (e.g. video processing jobs)
                    if not hasattr(self, "_default_tracker") or self._default_tracker is None:
                        if hasattr(self.model, "predictor") and self.model.predictor and hasattr(self.model.predictor, "trackers") and self.model.predictor.trackers:
                            from ultralytics.trackers.byte_tracker import BYTETracker
                            template_args = self.model.predictor.trackers[0].args
                            self._default_tracker = BYTETracker(template_args)
                    if hasattr(self, "_default_tracker") and self._default_tracker is not None:
                        if hasattr(self.model, "predictor") and self.model.predictor:
                            self.model.predictor.trackers = [self._default_tracker]

                # Forward pass with fixed 640 input resolution and aspect ratio preserving letterboxing
                results = self.model.track(
                    frame,
                    conf=base_conf,
                    persist=True,
                    tracker=tracker_cfg,
                    verbose=False,
                    imgsz=640
                )
                
                # Save updated tracker state back into isolation map
                if hasattr(self.model, "predictor") and self.model.predictor and hasattr(self.model.predictor, "trackers") and self.model.predictor.trackers:
                    active_trk = self.model.predictor.trackers[0]
                    if camera_id is not None:
                        self._trackers[camera_id] = active_trk
                    else:
                        self._default_tracker = active_trk

                # Periodically release cached MPS/GPU tensors and garbage collect
                self._infer_step = getattr(self, "_infer_step", 0) + 1
                if self._infer_step % 20 == 0:
                    import gc
                    gc.collect()
                    if hasattr(torch, "mps") and hasattr(torch.mps, "empty_cache"):
                        try:
                            torch.mps.empty_cache()
                        except Exception:
                            pass
            
            fh, fw = frame.shape[:2]
            candidate_detections: List[Dict[str, Any]] = []

            if results and len(results) > 0:
                result = results[0]
                boxes = result.boxes
                if boxes is not None and len(boxes) > 0:
                    for box in boxes:
                        raw_conf = float(box.conf[0])
                        cls_id = int(box.cls[0])
                        if hasattr(result, "names") and isinstance(result.names, dict) and cls_id in result.names:
                            label = str(result.names[cls_id])
                        elif cls_id < len(self.current_classes):
                            label = str(self.current_classes[cls_id])
                        else:
                            label = str(cls_id)

                        # Bounding box bounds clipping and validation
                        x1_r, y1_r, x2_r, y2_r = box.xyxy[0].tolist()
                        x1 = max(0.0, min(float(x1_r), float(fw)))
                        y1 = max(0.0, min(float(y1_r), float(fh)))
                        x2 = max(0.0, min(float(x2_r), float(fw)))
                        y2 = max(0.0, min(float(y2_r), float(fh)))
                        clipped_box = [round(x1, 1), round(y1, 1), round(x2, 1), round(y2, 1)]

                        # 1. Reject invalid or degenerate boxes
                        if not is_valid_detection_box(clipped_box, label, fw, fh):
                            continue

                        # 2. Check per-class confidence threshold
                        class_min_conf = thresh_map.get(label.lower(), thresh_map.get("default", 0.40))
                        if raw_conf < class_min_conf:
                            continue

                        # 3. Track state assignment
                        track_id = int(box.id[0]) if box.id is not None else None
                        track_state = "confirmed" if track_id is not None else "tentative"

                        candidate_detections.append({
                            "label": label,
                            "confidence": round(raw_conf, 3),
                            "raw_confidence": round(raw_conf, 3),
                            "bbox": clipped_box,
                            "track_id": track_id,
                            "track_state": track_state,
                            "is_predicted": False
                        })

            # 4. Apply cross-class suppression to eliminate synonym overlaps (e.g. car vs vehicle vs suv)
            final_detections = apply_cross_class_suppression(candidate_detections, iou_threshold=0.60)
            return final_detections

        finally:
            self.coordinator.release()

    def draw_detections(self, frame, detections, output_path):
        annotated = frame.copy()
        for det in detections:
            x1, y1, x2, y2 = [int(v) for v in det["bbox"]]
            tid_text = f' ID:{det["track_id"]}' if det.get("track_id") is not None else ''
            label = f'{det["label"]}{tid_text} {det["confidence"]:.2f}'
            cv2.rectangle(annotated, (x1, y1), (x2, y2), (0, 255, 0), 2)
            cv2.putText(annotated, label, (x1, max(20, y1 - 10)), 
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 2)
        cv2.imwrite(output_path, annotated)

def save_track_event(camera_id, tracking_id, track_data):
    event_id = str(uuid.uuid4())
    attributes = track_data.get("attributes", {})
    cam_row = execute_query("SELECT dataset_id FROM cameras WHERE id = ?", (camera_id,), fetch=True, fetch_one=True)
    dataset_id = cam_row["dataset_id"] if (cam_row and "dataset_id" in cam_row.keys() and cam_row["dataset_id"]) else None
    
    execute_query(
        """
        INSERT INTO events (id, camera_id, dataset_id, tracking_id, concept, start_timestamp, end_timestamp, confidence, bounding_boxes, representative_frame, attributes)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            event_id, 
            camera_id,
            dataset_id,
            str(tracking_id), 
            track_data["label"], 
            track_data["start_time"], 
            track_data["end_time"], 
            track_data["max_conf"], 
            json.dumps(track_data["bboxes"]), 
            track_data["representative_frame"],
            json.dumps(attributes)
        )
    )
    return event_id

# Thread-safe global detector singleton
_detector_instance = None
_detector_lock = threading.Lock()

def get_detector() -> OpenVocabularyDetector:
    global _detector_instance
    if _detector_instance is None:
        with _detector_lock:
            if _detector_instance is None:
                _detector_instance = OpenVocabularyDetector()
    return _detector_instance
