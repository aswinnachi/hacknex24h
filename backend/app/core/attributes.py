import cv2
import numpy as np
from typing import List, Dict, Any, Tuple, Optional

# Supported Color Categories
COLOR_CATEGORIES = [
    "white", "black", "red", "blue", "green", "yellow", "gray", "grey",
    "brown", "orange", "pink", "purple", "dark", "light"
]

def analyze_crop_color(crop: np.ndarray, concept: Optional[str] = None) -> Tuple[str, float]:
    """
    Analyzes an image crop to determine the dominant color using dual-space HSV and RGB statistics.
    Isolates central body panels for vehicles to discard roof/windshield reflections and tires/asphalt.
    Prevents small taillights from falsely triggering red on white/gray vehicles.
    """
    if crop is None or crop.size == 0 or crop.shape[0] < 5 or crop.shape[1] < 5:
        return "unknown", 0.0

    c_lower = (concept or "").lower()
    is_vehicle = c_lower in ("car", "truck", "bus", "motorcycle", "bicycle", "vehicle", "van")
    h_orig, w_orig = crop.shape[:2]

    # For vehicles, isolate inner body panel (discard roof glare/sky at top, tires/asphalt at bottom)
    if is_vehicle and h_orig >= 10 and w_orig >= 10:
        body = crop[int(h_orig * 0.15):int(h_orig * 0.82), int(w_orig * 0.12):int(w_orig * 0.88)]
        if body.size == 0:
            body = crop
    else:
        body = crop

    resized = cv2.resize(body, (48, 48))
    hsv = cv2.cvtColor(resized, cv2.COLOR_BGR2HSV)
    h = hsv[:, :, 0]
    s = hsv[:, :, 1]
    v = hsv[:, :, 2]
    b = resized[:, :, 0]
    g = resized[:, :, 1]
    r = resized[:, :, 2]
    total_pixels = 48 * 48

    # 1. Red body mask (Covers crimson, scarlet, ruby, maroon, burgundy)
    red_rgb = (r > g * 1.15) & (r > b * 1.05) & (r > 38) & ((r.astype(int) - g.astype(int)) >= 10)
    red_hsv = ((h <= 14) | (h >= 155)) & (s >= 25) & (v >= 35)
    red_mask = red_rgb | red_hsv
    red_ratio = np.sum(red_mask) / total_pixels

    # 2. Blue body mask (Requires genuine blue dominance to prevent sky glare on white/silver cars)
    blue_rgb = (b > r * 1.30) & (b > g * 1.15) & ((b.astype(int) - r.astype(int)) >= 25)
    blue_hsv = (h >= 95) & (h <= 135) & (s >= 45) & (v >= 45)
    blue_mask = blue_rgb & blue_hsv
    blue_ratio = np.sum(blue_mask) / total_pixels

    # 3. Green body mask
    green_rgb = (g > r * 1.18) & (g > b * 1.12) & (g > 40)
    green_hsv = (h >= 36) & (h <= 88) & (s >= 40) & (v >= 40)
    green_mask = green_rgb & green_hsv
    green_ratio = np.sum(green_mask) / total_pixels

    # 4. Yellow body mask
    yellow_rgb = (r > 120) & (g > 120) & (b < 90)
    yellow_hsv = (h >= 16) & (h <= 34) & (s >= 45) & (v >= 60)
    yellow_mask = yellow_rgb | yellow_hsv
    yellow_ratio = np.sum(yellow_mask) / total_pixels

    # 5. Orange body mask
    orange_hsv = (h >= 11) & (h <= 18) & (s >= 55) & (v >= 55)
    orange_ratio = np.sum(orange_hsv) / total_pixels

    # 6. Purple / Violet body mask
    purple_mask = (h >= 135) & (h <= 154) & (s >= 40) & (v >= 45)
    purple_ratio = np.sum(purple_mask) / total_pixels

    # 7. Brown check (low/moderate brightness, warm hue)
    brown_mask = (h >= 8) & (h <= 25) & (s >= 30) & (s <= 140) & (v >= 30) & (v <= 110)
    brown_ratio = np.sum(brown_mask) / total_pixels

    # Require threshold to prevent small accessories or taillight dots from triggering
    chroma_thresh = 0.28 if is_vehicle else 0.20

    if red_ratio >= chroma_thresh:
        return "red", float(min(1.0, red_ratio * 1.5))
    if blue_ratio >= chroma_thresh:
        return "blue", float(min(1.0, blue_ratio * 1.5))
    if green_ratio >= chroma_thresh:
        return "green", float(min(1.0, green_ratio * 1.5))
    if yellow_ratio >= chroma_thresh:
        return "yellow", float(min(1.0, yellow_ratio * 1.5))
    if orange_ratio >= chroma_thresh:
        return "orange", float(min(1.0, orange_ratio * 1.5))
    if purple_ratio >= chroma_thresh:
        return "purple", float(min(1.0, purple_ratio * 1.5))
    if brown_ratio >= 0.25:
        return "brown", float(min(1.0, brown_ratio * 2.0))

    # Achromatic checks: White / Light, Gray / Silver, Black / Dark
    # Check body panel (lower-middle section without sky reflections and tires)
    h_b, w_b = body.shape[:2]
    if h_b >= 6 and w_b >= 6:
        panel = body[int(h_b * 0.25):int(h_b * 0.88), int(w_b * 0.10):int(w_b * 0.90)]
        if panel.size > 0:
            p_hsv = cv2.cvtColor(panel, cv2.COLOR_BGR2HSV)
            p_v = p_hsv[:, :, 2]
            p_s = p_hsv[:, :, 1]
            dark_ratio = np.mean(p_v < 85)
            bright_ratio = np.mean((p_v > 125) & (p_s < 45))
            med_v = np.median(p_v)
            
            # Robust black detection: dark lower body panels (even with reflections)
            if dark_ratio >= 0.40 or (med_v < 90 and bright_ratio < 0.35):
                return "black", float(min(1.0, 0.70 + dark_ratio * 0.3))
            
            # Robust white detection: bright body panels with low saturation
            if bright_ratio >= 0.42 and med_v >= 135:
                return "white", float(min(1.0, 0.70 + bright_ratio * 0.3))

    mean_v = np.mean(v)
    mean_s = np.mean(s)
    med_v = np.median(v)

    if mean_s < 38 and (mean_v >= 115 or med_v >= 130):
        return "white", float(min(1.0, mean_v / 255.0))
    elif mean_v < 75 or med_v < 75 or np.mean(v < 80) >= 0.40:
        return "black", float(min(1.0, 0.85))
    else:
        return "gray", 0.85

def analyze_track_movement(bboxes: List[List[float]], fps: float = 12.0, concept: str = "person") -> Tuple[bool, str, float]:
    """
    Analyzes track trajectory to determine if the object is moving and infer specific action.
    Returns: (is_moving: bool, action: str, displacement_px: float)
    """
    if not bboxes or len(bboxes) < 2:
        return False, "stationary", 0.0

    centroids = []
    for b in bboxes:
        if len(b) >= 4:
            cx = (b[0] + b[2]) / 2.0
            cy = (b[1] + b[3]) / 2.0
            centroids.append((cx, cy))

    if len(centroids) < 2:
        return False, "stationary", 0.0

    start_c = centroids[0]
    end_c = centroids[-1]
    net_displacement = np.sqrt((end_c[0] - start_c[0])**2 + (end_c[1] - start_c[1])**2)

    path_distance = 0.0
    for i in range(len(centroids) - 1):
        p1 = centroids[i]
        p2 = centroids[i+1]
        path_distance += np.sqrt((p2[0] - p1[0])**2 + (p2[1] - p1[1])**2)

    # Thresholds: at least 15 pixels of displacement
    is_moving = bool(net_displacement > 15.0 or path_distance > 25.0)
    
    num_frames = len(bboxes)
    duration_sec = num_frames / max(fps, 1.0)
    velocity_px_s = path_distance / max(duration_sec, 0.1)

    c_lower = (concept or "").lower()
    if c_lower in ("car", "bus", "truck", "motorcycle", "bicycle", "vehicle"):
        action = "moving" if is_moving else "stationary"
    elif c_lower == "person":
        if not is_moving:
            action = "standing"
        elif velocity_px_s > 60.0:
            action = "running"
        else:
            action = "walking"
    else:
        action = "moving" if is_moving else "stationary"

    return is_moving, action, float(net_displacement)

def analyze_person_clothing_and_accessories(
    frame: np.ndarray,
    person_bbox: List[float],
    other_detections_in_frame: List[Dict[str, Any]]
) -> Dict[str, Any]:
    """
    Analyzes a person's upper/lower clothing colors, types, and spatial associations
    with accessories (backpack, bag, handbag, suitcase, umbrella, phone, bottle).
    """
    if frame is None or len(person_bbox) < 4:
        return {
            "upper_color": "unknown", "upper_color_conf": 0.0, "upper_type": "shirt",
            "lower_color": "unknown", "lower_color_conf": 0.0, "lower_type": "pants",
            "clothing_summary": "neutral clothing",
            "has_bag": False, "has_backpack": False, "has_handbag": False,
            "has_suitcase": False, "has_umbrella": False, "has_phone": False,
            "has_bottle": False, "accessories": []
        }

    h, w = frame.shape[:2]
    x1, y1, x2, y2 = [int(max(0, v)) for v in person_bbox[:4]]
    x2, y2 = min(w, x2), min(h, y2)

    if x2 <= x1 or y2 <= y1:
        return {
            "upper_color": "unknown", "upper_color_conf": 0.0, "upper_type": "shirt",
            "lower_color": "unknown", "lower_color_conf": 0.0, "lower_type": "pants",
            "clothing_summary": "neutral clothing",
            "has_bag": False, "has_backpack": False, "has_handbag": False,
            "has_suitcase": False, "has_umbrella": False, "has_phone": False,
            "has_bottle": False, "accessories": []
        }

    person_crop = frame[y1:y2, x1:x2]
    ph, pw = person_crop.shape[:2]

    # 1. Upper Body Torso Crop (top 15% to 50%)
    torso_y1 = int(ph * 0.15)
    torso_y2 = int(ph * 0.50)
    torso_crop = person_crop[torso_y1:torso_y2, :] if torso_y2 > torso_y1 else person_crop
    upper_color, upper_conf = analyze_crop_color(torso_crop)
    upper_type = "shirt"

    # 2. Lower Body Legs Crop (55% to 95%)
    lower_y1 = int(ph * 0.55)
    lower_y2 = int(ph * 0.95)
    lower_crop = person_crop[lower_y1:lower_y2, :] if lower_y2 > lower_y1 else person_crop
    lower_color, lower_conf = analyze_crop_color(lower_crop)
    lower_type = "pants"

    # 3. Formulate readable clothing summary
    upper_desc = f"{upper_color} {upper_type}" if upper_color != "unknown" else "shirt"
    lower_desc = f"{lower_color} {lower_type}" if lower_color != "unknown" else ""
    parts = [upper_desc]
    if lower_desc: parts.append(lower_desc)
    clothing_summary = " with ".join(parts)

    # 4. Check Spatial Associations with Other Detections (Accessories & Carried Objects)
    has_bag = False
    has_backpack = False
    has_handbag = False
    has_suitcase = False
    has_umbrella = False
    has_phone = False
    has_bottle = False
    accessories_found = []

    # Expansion margin for bounding box proximity (15% person width)
    margin_x = int((x2 - x1) * 0.20)
    margin_y = int((y2 - y1) * 0.15)
    p_box_exp = [x1 - margin_x, y1 - margin_y, x2 + margin_x, y2 + margin_y]

    for det in other_detections_in_frame:
        label = det.get("label", "").lower()
        bx1, by1, bx2, by2 = det["bbox"][:4]

        # Check spatial intersection with expanded person box
        ox1 = max(p_box_exp[0], bx1)
        oy1 = max(p_box_exp[1], by1)
        ox2 = min(p_box_exp[2], bx2)
        oy2 = min(p_box_exp[3], by2)

        if ox2 > ox1 and oy2 > oy1:
            if label in ("backpack", "pack"):
                has_backpack = True
                has_bag = True
                accessories_found.append("backpack")
            elif label in ("handbag", "purse"):
                has_handbag = True
                has_bag = True
                accessories_found.append("handbag")
            elif label in ("suitcase", "luggage"):
                has_suitcase = True
                has_bag = True
                accessories_found.append("suitcase")
            elif label in ("bag", "sack"):
                has_bag = True
                accessories_found.append("bag")
            elif label == "umbrella":
                has_umbrella = True
                accessories_found.append("umbrella")
            elif label in ("phone", "cellphone", "mobile"):
                has_phone = True
                accessories_found.append("phone")
            elif label in ("bottle", "cup"):
                has_bottle = True
                accessories_found.append("bottle")

    return {
        "upper_color": upper_color,
        "upper_color_conf": round(upper_conf, 2),
        "upper_type": upper_type,
        "lower_color": lower_color,
        "lower_color_conf": round(lower_conf, 2),
        "lower_type": lower_type,
        "clothing_summary": clothing_summary,
        "has_bag": has_bag,
        "has_backpack": has_backpack,
        "has_handbag": has_handbag,
        "has_suitcase": has_suitcase,
        "has_umbrella": has_umbrella,
        "has_phone": has_phone,
        "has_bottle": has_bottle,
        "accessories": list(set(accessories_found))
    }

