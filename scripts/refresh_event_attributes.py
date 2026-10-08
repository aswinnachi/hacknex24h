import os
import sys
import json
import sqlite3
import cv2

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from backend.app.core.attributes import (
    analyze_crop_color,
    analyze_track_movement,
    analyze_person_clothing_and_accessories,
)

def refresh_attributes():
    db_path = "database/app.db"
    if not os.path.exists(db_path):
        print("Database not found.")
        return

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    cursor.execute("SELECT id, camera_id, concept, bounding_boxes, representative_frame, attributes FROM events")
    rows = cursor.fetchall()
    print(f"Refreshing attributes for {len(rows)} events...")

    updated_count = 0
    for row in rows:
        event_id = row["id"]
        concept = row["concept"]
        rep_frame_path = row["representative_frame"]
        raw_bboxes = json.loads(row["bounding_boxes"]) if row["bounding_boxes"] else []
        old_attr = json.loads(row["attributes"]) if row["attributes"] else {}

        # 1. Movement analysis
        is_moving, action, disp = analyze_track_movement(raw_bboxes, fps=12.0, concept=concept)

        # 2. Color & clothing / vehicle attributes
        frame = cv2.imread(rep_frame_path) if rep_frame_path and os.path.exists(rep_frame_path) else None

        if concept == "person":
            if frame is not None and raw_bboxes:
                h, w = frame.shape[:2]
                b = raw_bboxes[0]
                person_bbox = [
                    max(0, min(w - 1, b[0])),
                    max(0, min(h - 1, b[1])),
                    max(0, min(w, b[2])),
                    max(0, min(h, b[3]))
                ]
                clothing_info = analyze_person_clothing_and_accessories(frame, person_bbox, [])
            else:
                clothing_info = {
                    "upper_color": "unknown", "upper_color_conf": 0.0, "upper_type": "shirt",
                    "lower_color": "unknown", "lower_color_conf": 0.0, "lower_type": "pants",
                    "clothing_summary": "neutral clothing",
                    "has_bag": False, "has_backpack": False, "has_handbag": False,
                    "has_suitcase": False, "has_umbrella": False, "has_phone": False,
                    "has_bottle": False, "accessories": []
                }

            attr = {
                "object_type": "person",
                "upper_color": clothing_info.get("upper_color", "unknown"),
                "upper_color_conf": clothing_info.get("upper_color_conf", 0.0),
                "upper_type": clothing_info.get("upper_type", "shirt"),
                "lower_color": clothing_info.get("lower_color", "unknown"),
                "lower_color_conf": clothing_info.get("lower_color_conf", 0.0),
                "lower_type": clothing_info.get("lower_type", "pants"),
                "clothing": clothing_info.get("clothing_summary", "shirt"),
                "has_bag": clothing_info.get("has_bag", False),
                "has_backpack": clothing_info.get("has_backpack", False),
                "has_handbag": clothing_info.get("has_handbag", False),
                "has_suitcase": clothing_info.get("has_suitcase", False),
                "has_umbrella": clothing_info.get("has_umbrella", False),
                "has_phone": clothing_info.get("has_phone", False),
                "has_bottle": clothing_info.get("has_bottle", False),
                "accessories": clothing_info.get("accessories", []),
                "is_moving": is_moving,
                "action": action,
                "displacement_px": round(disp, 1),
                "color": clothing_info.get("upper_color", "unknown"),
                "color_confidence": clothing_info.get("upper_color_conf", 0.0),
            }
        elif concept in ("car", "bus", "truck", "motorcycle", "bicycle", "vehicle"):
            if frame is not None and raw_bboxes:
                h, w = frame.shape[:2]
                b = raw_bboxes[0]
                x1 = int(max(0, min(w - 1, b[0])))
                y1 = int(max(0, min(h - 1, b[1])))
                x2 = int(max(0, min(w, b[2])))
                y2 = int(max(0, min(h, b[3])))
                if x2 > x1 and y2 > y1:
                    crop = frame[y1:y2, x1:x2]
                    color, conf = analyze_crop_color(crop)
                else:
                    color, conf = "unknown", 0.0
            else:
                color, conf = "unknown", 0.0

            attr = {
                "object_type": concept,
                "vehicle_class": concept,
                "color": color,
                "color_confidence": round(conf, 2),
                "vehicle_color": color,
                "is_moving": is_moving,
                "movement": "moving" if is_moving else "stationary",
                "action": "moving" if is_moving else "stationary",
                "displacement_px": round(disp, 1),
                "clothing": None,
                "has_bag": False,
            }
        else:
            if frame is not None and raw_bboxes:
                h, w = frame.shape[:2]
                b = raw_bboxes[0]
                x1 = int(max(0, min(w - 1, b[0])))
                y1 = int(max(0, min(h - 1, b[1])))
                x2 = int(max(0, min(w, b[2])))
                y2 = int(max(0, min(h, b[3])))
                if x2 > x1 and y2 > y1:
                    crop = frame[y1:y2, x1:x2]
                    color, conf = analyze_crop_color(crop)
                else:
                    color, conf = "unknown", 0.0
            else:
                color, conf = "unknown", 0.0
            attr = {
                "object_type": concept,
                "color": color,
                "color_confidence": round(conf, 2),
                "is_moving": is_moving,
                "action": action,
                "displacement_px": round(disp, 1),
            }

        cursor.execute(
            "UPDATE events SET attributes = ? WHERE id = ?",
            (json.dumps(attr), event_id)
        )
        updated_count += 1

    conn.commit()
    conn.close()
    print(f"Successfully refreshed {updated_count} events with cropped structured attributes.")

if __name__ == "__main__":
    refresh_attributes()
