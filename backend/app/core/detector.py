import json
import uuid
import cv2
import os
from backend.app.core.hardware import detect_hardware
from backend.app.core.logging import logger
from backend.app.db.database import execute_query

class OpenVocabularyDetector:
    def __init__(self, model_name="yolov8s-world.pt"):
        self.hw_info = detect_hardware()
        self.device = self.hw_info.get("primary_device", "cpu")
        
        logger.info(f"Loading YOLO-World ({model_name}) on {self.device}...")
        try:
            from ultralytics import YOLOWorld
            self.model = YOLOWorld(model_name)
            if self.device != "cpu":
                self.model.to(self.device)
            self.current_classes = []
            self.available = True
        except ImportError:
            logger.error("Ultralytics not installed. Open-vocabulary detection is unavailable.")
            self.available = False
            self.model = None

    def set_classes(self, classes):
        if not self.available: return
        if set(self.current_classes) != set(classes):
            self.model.set_classes(classes)
            self.current_classes = classes

    def track_frame(self, frame, classes, conf_threshold=0.15):
        if not self.available: return []
        
        self.set_classes(classes)
        # Use persist=True and optimized CCTV ByteTrack configuration
        tracker_cfg = os.path.join(os.path.dirname(__file__), "cctv_tracker.yaml")
        if not os.path.exists(tracker_cfg):
            tracker_cfg = "bytetrack.yaml"

        results = self.model.track(frame, conf=conf_threshold, persist=True, tracker=tracker_cfg, verbose=False)
        
        detections = []
        if results and len(results) > 0:
            result = results[0]
            boxes = result.boxes
            if boxes:
                for box in boxes:
                    conf = float(box.conf[0])
                    cls_id = int(box.cls[0])
                    if hasattr(result, "names") and isinstance(result.names, dict) and cls_id in result.names:
                        label = result.names[cls_id]
                    elif cls_id < len(self.current_classes):
                        label = self.current_classes[cls_id]
                    else:
                        label = str(cls_id)
                    x1, y1, x2, y2 = box.xyxy[0].tolist()
                    
                    track_id = int(box.id[0]) if box.id is not None else None
                    
                    detections.append({
                        "label": label,
                        "confidence": conf,
                        "bbox": [x1, y1, x2, y2],
                        "track_id": track_id
                    })
        return detections

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


# Create a global detector instance so we don't reload weights on every request
_detector_instance = None

def get_detector():
    global _detector_instance
    if _detector_instance is None:
        _detector_instance = OpenVocabularyDetector()
    return _detector_instance
