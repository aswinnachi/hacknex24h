import os
import subprocess
import json
import cv2
import numpy as np
from typing import Optional, Dict, Any, List
from backend.app.core.logging import logger
from backend.app.db.database import execute_query

CLIPS_DIR = "data/clips"
os.makedirs(CLIPS_DIR, exist_ok=True)

def compute_dense_clip_scene(clip_path: str, clip_start: float = 0.0, clip_end: float = 10.0) -> List[Dict[str, Any]]:
    """
    Retrieves per-frame ground-truth multi-object trajectories across the video clip.
    Instantly slices from pre-computed source camera scenes when available,
    avoiding live neural network inference during HTTP requests.
    """
    if not os.path.exists(clip_path):
        return []

    cache_path = f"{clip_path}.scene.json"
    if os.path.exists(cache_path) and os.path.getsize(cache_path) > 20:
        try:
            with open(cache_path, "r") as f:
                return json.load(f)
        except Exception:
            pass

    # Instant slicing from camera source scene cache for generated clips
    import glob
    if "/clips/" in clip_path:
        parts = clip_path.split("/clips/")
        if len(parts) == 2:
            cam_sub = parts[1].split("/")[0]
            cam_scenes = glob.glob(f"data/videos/{cam_sub}/*.scene.json")
            if cam_scenes:
                try:
                    with open(cam_scenes[0], "r") as f:
                        full_scene = json.load(f)
                    fps = 24.0
                    start_f = int(clip_start * fps)
                    end_f = int(clip_end * fps)
                    
                    clip_objs = []
                    for obj in full_scene:
                        boxes = obj.get("bounding_boxes", [])
                        if not boxes:
                            continue
                        sliced = boxes[start_f:end_f]
                        if not any(b and len(b) >= 4 and (b[0] > 0 or b[2] > 0) for b in sliced):
                            continue
                        
                        obj_copy = dict(obj)
                        obj_copy["bounding_boxes"] = sliced
                        clip_objs.append(obj_copy)

                    if clip_objs:
                        try:
                            with open(cache_path, "w") as f:
                                json.dump(clip_objs, f)
                        except Exception:
                            pass
                        return clip_objs
                except Exception as e:
                    logger.warning(f"Error slicing scene from {cam_scenes[0]}: {e}")

    cap = cv2.VideoCapture(clip_path)
    if not cap.isOpened():
        return []

    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    fps = cap.get(cv2.CAP_PROP_FPS) or 24.0
    frames = []
    while True:
        ret, f = cap.read()
        if not ret:
            break
        frames.append(f)
        if len(frames) >= 400:
            break
    cap.release()

    if len(frames) < 2:
        return []

    total_frames = len(frames)

    try:
        from backend.app.core.detector import get_detector
        from backend.app.core.attributes import analyze_crop_color
        detector = get_detector()
        detector.set_classes(["car", "suv", "van", "truck", "bus", "motorcycle", "scooter", "bicycle", "person", "vehicle"])
        model = detector.model
    except Exception as e:
        logger.error(f"Error initializing detector for clip scene: {e}")
        return []

    step = 3
    tracks = []
    next_id = 1

    def iou(b1, b2):
        x1 = max(b1[0], b2[0]); y1 = max(b1[1], b2[1])
        x2 = min(b1[2], b2[2]); y2 = min(b1[3], b2[3])
        inter = max(0, x2 - x1) * max(0, y2 - y1)
        a1 = max(1.0, (b1[2] - b1[0]) * (b1[3] - b1[1]))
        a2 = max(1.0, (b2[2] - b2[0]) * (b2[3] - b2[1]))
        return inter / (a1 + a2 - inter)

    def are_labels_compatible(l1, l2):
        if l1 == l2: return True
        v_types = {"car", "suv", "van", "truck", "bus", "vehicle"}
        two_types = {"motorcycle", "scooter", "bicycle"}
        if l1 in v_types and l2 in v_types: return True
        if l1 in two_types and l2 in two_types: return True
        return False

    active_tracks = []
    for f_idx in range(0, total_frames, step):
        res = model.predict(frames[f_idx], conf=0.14, verbose=False)[0]
        cur_dets = []
        for b in res.boxes:
            cls_id = int(b.cls[0])
            label = res.names[cls_id]
            box = [round(float(v), 1) for v in b.xyxy[0].tolist()]
            crop = frames[f_idx][int(box[1]):int(box[3]), int(box[0]):int(box[2])]
            col, col_conf = analyze_crop_color(crop, concept=label)
            cur_dets.append({
                "bbox": box,
                "label": label,
                "conf": float(b.conf[0]),
                "color": col,
                "color_conf": col_conf
            })

        matched_tracks = set()
        matched_dets = set()
        for d_i, det in enumerate(cur_dets):
            best_score = 0.12
            best_t = None
            for t in active_tracks:
                if t["track_id"] in matched_tracks:
                    continue
                last_box = t["points"][max(t["points"].keys())]
                score = iou(last_box, det["bbox"])
                if score > best_score and are_labels_compatible(det["label"], t["label"]):
                    best_score = score
                    best_t = t
            if best_t:
                best_t["points"][f_idx] = det["bbox"]
                best_t["colors"].append(det["color"])
                best_t["labels"].append(det["label"])
                best_t["confs"].append(det["conf"])
                matched_tracks.add(best_t["track_id"])
                matched_dets.add(d_i)

        for d_i, det in enumerate(cur_dets):
            if d_i not in matched_dets:
                t_obj = {
                    "track_id": next_id,
                    "label": det["label"],
                    "labels": [det["label"]],
                    "colors": [det["color"]],
                    "confs": [det["conf"]],
                    "points": {f_idx: det["bbox"]}
                }
                next_id += 1
                tracks.append(t_obj)
                active_tracks.append(t_obj)

        active_tracks = [t for t in active_tracks if max(t["points"].keys()) >= f_idx - 3 * step]

    # Convert tracks to full-length per-frame dense trajectories
    scene_objects = []
    valid_tracks = [t for t in tracks if len(t["points"]) >= 2]
    for t in valid_tracks:
        dom_color = max(set(t["colors"]), key=t["colors"].count)
        dom_raw_label = max(set(t.get("labels", [t["label"]])), key=t.get("labels", [t["label"]]).count)
        
        # Canonical label
        if dom_raw_label in ("suv", "van", "vehicle"):
            c_label = "car"
        elif dom_raw_label in ("scooter", "motorbike"):
            c_label = "motorcycle"
        else:
            c_label = dom_raw_label
            
        avg_conf = round(float(np.mean(t["confs"])), 2)
        f_min = min(t["points"].keys())
        f_max = max(t["points"].keys())

        # Build dense per-frame bounding box array
        dense_boxes = []
        known_frames = sorted(t["points"].keys())
        for fi in range(total_frames):
            if fi < f_min or fi > f_max:
                dense_boxes.append([0.0, 0.0, 0.0, 0.0])
            elif fi in t["points"]:
                dense_boxes.append(t["points"][fi])
            else:
                prev_fi = max(f for f in known_frames if f < fi)
                next_fi = min(f for f in known_frames if f > fi)
                alpha = (fi - prev_fi) / float(next_fi - prev_fi)
                b0 = t["points"][prev_fi]
                b1 = t["points"][next_fi]
                inter_b = [
                    round(b0[k] + alpha * (b1[k] - b0[k]), 1) for k in range(4)
                ]
                dense_boxes.append(inter_b)

        scene_objects.append({
            "event_id": f"clip_track_{t['track_id']}",
            "tracking_id": str(t["track_id"]),
            "concept": c_label,
            "color": dom_color,
            "clothing": "",
            "has_bag": False,
            "is_moving": True,
            "confidence": avg_conf,
            "start_frame": f_min,
            "end_frame": f_max,
            "bounding_boxes": dense_boxes,
            "is_target": False
        })

    try:
        with open(cache_path, "w") as f:
            json.dump(scene_objects, f)
    except Exception as e:
        logger.warning(f"Could not save scene cache {cache_path}: {e}")

    return scene_objects

def generate_dense_clip_trajectories(clip_path: str, objects_to_track: List[Dict[str, Any]]) -> Dict[str, list]:
    """
    Backwards-compatible helper returning event_id -> bounding_boxes mapping.
    """
    scene = compute_dense_clip_scene(clip_path)
    res = {}
    for obj in scene:
        res[obj["event_id"]] = obj["bounding_boxes"]
    return res

def format_timestamp(seconds: float, base_hour: int = 14, base_minute: int = 37, base_second: int = 0) -> str:
    """
    Formats seconds into both video offset HH:MM:SS and simulated CCTV timestamp.
    """
    total_sec = int(seconds)
    millis = int((seconds - total_sec) * 100)
    hrs = total_sec // 3600
    mins = (total_sec % 3600) // 60
    secs = total_sec % 60
    
    # CCTV simulated wall-clock time
    sim_sec = (base_second + total_sec) % 60
    carry_min = (base_second + total_sec) // 60
    sim_min = (base_minute + mins + carry_min) % 60
    carry_hr = (base_minute + mins + carry_min) // 60
    sim_hr = (base_hour + hrs + carry_hr) % 24
    
    return f"{sim_hr:02d}:{sim_min:02d}:{sim_sec:02d}"

def format_time_12h(seconds: float, base_hour: int = 15, base_minute: int = 56, base_second: int = 0) -> str:
    """
    Formats seconds into standard CCTV wall-clock 12-hour timestamp like '3:56 PM'.
    """
    total_sec = int(seconds)
    hrs = total_sec // 3600
    mins = (total_sec % 3600) // 60
    
    sim_sec = (base_second + total_sec) % 60
    carry_min = (base_second + total_sec) // 60
    sim_min = (base_minute + mins + carry_min) % 60
    carry_hr = (base_minute + mins + carry_min) // 60
    sim_hr_24 = (base_hour + hrs + carry_hr) % 24
    
    period = "PM" if sim_hr_24 >= 12 else "AM"
    sim_hr_12 = sim_hr_24 % 12
    if sim_hr_12 == 0:
        sim_hr_12 = 12
    return f"{sim_hr_12}:{sim_min:02d} {period}"

def generate_evidence_clip(
    event_id: str,
    buffer_before: float = 3.0,
    buffer_after: float = 3.0
) -> Optional[Dict[str, Any]]:
    """
    Lazily generates a video clip around the event if not already generated.
    Returns metadata including clip path and frame details.
    """
    row = execute_query("SELECT * FROM events WHERE id = ?", (event_id,), fetch=True, fetch_one=True)
    if not row:
        logger.warning(f"Event {event_id} not found in database.")
        return None
        
    event = dict(row)
    camera_id = event["camera_id"]
    start_t = float(event["start_timestamp"])
    end_t = float(event["end_timestamp"])
    
    # Get camera details
    cam_row = execute_query("SELECT * FROM cameras WHERE id = ?", (camera_id,), fetch=True, fetch_one=True)
    cam_dict = dict(cam_row) if cam_row else {}
    camera_name = cam_dict.get("name", camera_id)
    video_path = cam_dict.get("video_path")
    duration = float(cam_dict.get("duration") or 60.0)
    
    cam_clips_dir = os.path.join(CLIPS_DIR, camera_id)
    os.makedirs(cam_clips_dir, exist_ok=True)
    
    clip_filename = f"{cam_clips_dir}/clip_{event_id}.mp4"
    
    clip_start = max(0.0, start_t - buffer_before)
    clip_end = min(duration, end_t + buffer_after)
    if clip_end - clip_start < 2.5:
        clip_end = min(duration, clip_start + 3.0)
    clip_duration = clip_end - clip_start
    
    # Lazy generation: check if file already exists
    if not os.path.exists(clip_filename) or os.path.getsize(clip_filename) == 0:
        if video_path and os.path.exists(video_path):
            try:
                cmd = [
                    "ffmpeg", "-y",
                    "-ss", f"{clip_start:.3f}",
                    "-to", f"{clip_end:.3f}",
                    "-i", video_path,
                    "-c:v", "libx264",
                    "-preset", "ultrafast",
                    "-crf", "23",
                    "-c:a", "aac",
                    "-movflags", "+faststart",
                    clip_filename
                ]
                res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=10)
                if res.returncode == 0:
                    logger.info(f"Generated lazy evidence clip: {clip_filename} ({clip_duration:.2f}s)")
                    execute_query("UPDATE events SET clip_path = ? WHERE id = ?", (clip_filename, event_id))
                else:
                    logger.error(f"FFmpeg failed to generate clip: {res.stderr.decode('utf-8', errors='ignore')}")
            except Exception as e:
                logger.error(f"Exception during clip generation for event {event_id}: {e}")
        else:
            logger.warning(f"Video file not found at {video_path}")
            
    # Parse bounding boxes
    bboxes = []
    if event.get("bounding_boxes"):
        try:
            bboxes = json.loads(event["bounding_boxes"])
        except Exception:
            bboxes = []
            
    confidence = float(event.get("confidence") or 0.85)
    relevance_pct = round(confidence * 100, 1)
    
    attr_dict = {}
    if event.get("attributes"):
        try:
            attr_dict = json.loads(event["attributes"])
        except Exception:
            pass
    frame_path = event.get("representative_frame", "")

    # Fetch all co-occurring objects detected in this camera around this timestamp
    co_rows = execute_query(
        """
        SELECT id, tracking_id, concept, start_timestamp, end_timestamp, confidence, bounding_boxes, attributes
        FROM events
        WHERE camera_id = ? AND start_timestamp <= ? AND end_timestamp >= ?
        ORDER BY confidence DESC
        LIMIT 40
        """,
        (camera_id, start_t + 1.2, max(0.0, start_t - 1.2)),
        fetch=True
    )

    # Compute dense, ground-truth per-frame trajectories for all objects in the clip
    scene_objects = []
    target_matched = False
    target_color = (attr_dict.get("color") or "unknown").lower()
    target_concept = event["concept"].lower()

    if os.path.exists(clip_filename):
        clip_scene = compute_dense_clip_scene(clip_filename, clip_start=clip_start, clip_end=clip_end)
        if clip_scene:
            scene_objects = clip_scene
            
            # Find the best matching track for the target event in the clip
            best_cand = None
            best_score = -1.0
            clip_offset = max(0.0, start_t - clip_start)

            for cand in scene_objects:
                c_score = 0.0
                cand_conc = (cand.get("concept") or "").lower()
                cand_col = (cand.get("color") or "unknown").lower()
                
                if cand_conc == target_concept:
                    c_score += 3.0
                if target_color != "unknown" and cand_col == target_color:
                    c_score += 5.0
                    
                # Time alignment with target appearance in clip
                cand_start_sec = cand.get("start_frame", 0) / 25.0
                time_diff = abs(cand_start_sec - clip_offset)
                c_score += max(0.0, 4.0 - time_diff)

                if c_score > best_score:
                    best_score = c_score
                    best_cand = cand

            if best_cand and best_score >= 3.0:
                best_cand["is_target"] = True
                best_cand["event_id"] = event_id
                best_cand["tracking_id"] = str(event.get("tracking_id") or best_cand["tracking_id"])
                bboxes = best_cand["bounding_boxes"]
                target_matched = True
                if not attr_dict.get("color") or attr_dict.get("color") == "unknown":
                    attr_dict["color"] = best_cand.get("color", "unknown")

    # Fallback if no clip scene or target track was not matched
    if not target_matched and bboxes:
        scene_objects.insert(0, {
            "event_id": event_id,
            "tracking_id": str(event.get("tracking_id")) if event.get("tracking_id") is not None else None,
            "concept": event["concept"],
            "color": attr_dict.get("color") or "unknown",
            "clothing": attr_dict.get("clothing") or "",
            "has_bag": bool(attr_dict.get("has_bag", False)),
            "is_moving": bool(attr_dict.get("is_moving", False)),
            "confidence": confidence,
            "bounding_boxes": bboxes,
            "start_timestamp": start_t,
            "end_timestamp": end_t,
            "is_target": True
        })

    return {
        "event_id": event_id,
        "verdict": "YES",
        "summary": f"{event['concept'].capitalize()} detected.",
        "concept": event["concept"],
        "camera_id": camera_id,
        "camera_name": camera_name,
        "tracking_id": event.get("tracking_id"),
        "timestamp": start_t,
        "time_formatted": format_time_12h(start_t),
        "time_hms": format_timestamp(start_t),
        "video_offset": f"{start_t:.2f}s",
        "confidence": confidence,
        "relevance_percent": relevance_pct,
        "frame_path": frame_path,
        "frame_url": f"/{frame_path}" if not frame_path.startswith("/") else frame_path,
        "clip_path": clip_filename if os.path.exists(clip_filename) else None,
        "clip_url": f"/{clip_filename}" if os.path.exists(clip_filename) else None,
        "clip_start": clip_start,
        "clip_end": clip_end,
        "clip_duration": clip_duration,
        "bounding_boxes": bboxes,
        "video_width": int(cam_dict.get("width") or 854),
        "video_height": int(cam_dict.get("height") or 480),
        "attributes": attr_dict,
        "color": attr_dict.get("color") or "unknown",
        "scene_objects": scene_objects
    }
