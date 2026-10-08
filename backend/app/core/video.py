import cv2
import os
import uuid
import json
import threading
import numpy as np
from typing import Dict, Any, List, Optional
from backend.app.core.logging import logger

from backend.app.db.database import execute_query
from backend.app.core.detector import get_detector, save_track_event
from backend.app.core.attributes import (
    analyze_crop_color,
    analyze_track_movement,
    analyze_person_clothing_and_accessories
)

# In-memory job tracking
_jobs = {}

def get_job_status(job_id: str):
    return _jobs.get(job_id)

def process_video_job(job_id: str, camera_id: str, video_path: str, frame_interval: int):
    _jobs[job_id] = {
        "status": "processing",
        "stage": "Reading video metadata",
        "progress": 0,
        "total_frames": 0,
        "frames_saved": 0,
        "frames_analyzed": 0,
        "camera_id": camera_id
    }
    try:
        if not os.path.exists(video_path):
            raise FileNotFoundError(f"Video file not found: {video_path}")
            
        cam_row = execute_query("SELECT dataset_id FROM cameras WHERE id = ?", (camera_id,), fetch=True, fetch_one=True)
        dataset_id = cam_row["dataset_id"] if (cam_row and "dataset_id" in cam_row.keys() and cam_row["dataset_id"]) else "unknown"
        _jobs[job_id]["dataset_id"] = dataset_id

        is_image = os.path.splitext(video_path)[1].lower() in (".jpg", ".jpeg", ".png", ".webp")
        if is_image:
            img = cv2.imread(video_path)
            if img is None:
                raise ValueError(f"Could not read image file: {video_path}")
            height, width = img.shape[:2]
            fps = 1.0
            total_frames = 1
            duration = 1.0
            resolution = f"{width}x{height}"
            cap = None
        else:
            cap = cv2.VideoCapture(video_path)
            if not cap.isOpened():
                raise ValueError(f"Could not open video file: {video_path}")
            fps = cap.get(cv2.CAP_PROP_FPS) or 12.0
            total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
            width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
            height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
            duration = total_frames / fps if fps > 0 else 0.0
            resolution = f"{width}x{height}"
        
        execute_query(
            "UPDATE cameras SET fps = ?, duration = ?, resolution = ?, width = ?, height = ?, status = 'ANALYZING' WHERE id = ?",
            (fps, duration, resolution, width, height, camera_id)
        )
        
        logger.info(f"[ANALYSIS START] camera_id={camera_id} dataset_id={dataset_id} is_image={is_image} total_frames={total_frames} duration={duration:.1f}s resolution={resolution} fps={fps:.1f}")
        
        _jobs[job_id]["total_frames"] = total_frames
        _jobs[job_id]["stage"] = "Analyzing media and detecting visual tracks"
        
        frames_dir = f"data/frames/{camera_id}"
        os.makedirs(frames_dir, exist_ok=True)
        
        frames_saved = 0
        current_frame = 0
        frames_analyzed = 0
        
        detector = get_detector()
        detection_classes = [
            "person", "car", "suv", "van", "motorcycle", "scooter", "bicycle", "truck", "bus", "vehicle", "traffic light",
            "bag", "backpack", "handbag", "suitcase", "umbrella", "phone", "bottle"
        ]
        
        active_tracks = {}
        MAX_LOST_FRAMES = 25 # Tolerance for lost tracking
        
        # We also keep a history of finalized tracks for the video analysis report
        finalized_tracks = []
        
        # Pre-cleanup previous events for this camera to ensure fresh analysis
        execute_query("DELETE FROM events WHERE camera_id = ?", (camera_id,))
        
        while (cap is not None and cap.isOpened()) or (is_image and current_frame == 0):
            if is_image:
                ret, frame = True, img
            else:
                ret, frame = cap.read()
                if not ret:
                    break
                
            process_interval = max(1, min(frame_interval, 2)) if frame_interval > 0 else 1
            
            if is_image or (current_frame % process_interval == 0):
                frames_analyzed += 1
                timestamp = current_frame / fps if fps > 0 else 0.0
                
                if detector and detector.available:
                    detections = detector.track_frame(frame, detection_classes, conf_threshold=0.15)
                    seen_track_ids = set()
                    
                    # Separate non-person accessory detections in this frame for spatial association
                    other_dets = [d for d in detections if d["label"] != "person"]
                    
                    if detections:
                        for det_idx, det in enumerate(detections):
                            tid = det.get("track_id")
                            if tid is None:
                                tid = det_idx + 1
                                
                            raw_label = det["label"]
                            if raw_label in ("suv", "van", "vehicle"):
                                label = "car"
                            elif raw_label in ("scooter", "motorbike"):
                                label = "motorcycle"
                            else:
                                label = raw_label
                            bbox = det["bbox"]
                            conf = det["confidence"]
                            
                            seen_track_ids.add(tid)
                            
                            # Extract crop for visual attribute analysis
                            x1, y1, x2, y2 = [int(max(0, v)) for v in bbox[:4]]
                            x2, y2 = min(width, x2), min(height, y2)
                            crop = frame[y1:y2, x1:x2].copy() if (x2 > x1 and y2 > y1) else None
                            
                            if tid not in active_tracks:
                                frame_filename = f"{frames_dir}/track_{tid}_start.jpg"
                                cv2.imwrite(frame_filename, frame)
                                frames_saved += 1
                                
                                active_tracks[tid] = {
                                    "label": label,
                                    "start_time": timestamp,
                                    "end_time": timestamp,
                                    "max_conf": conf,
                                    "bboxes": [bbox],
                                    "crops": [crop] if crop is not None else [],
                                    "representative_frame": frame_filename,
                                    "lost_count": 0,
                                    "person_attrs_observed": []
                                }
                            else:
                                track = active_tracks[tid]
                                track["end_time"] = timestamp
                                track["lost_count"] = 0
                                track["bboxes"].append(bbox)
                                if crop is not None and len(track["crops"]) < 5:
                                    track["crops"].append(crop)
                                if conf > track["max_conf"]:
                                    track["max_conf"] = conf
                                    
                            # If this is a person, analyze clothing and accessories
                            if label == "person" and tid in active_tracks:
                                p_attr = analyze_person_clothing_and_accessories(frame, bbox, other_dets)
                                active_tracks[tid]["person_attrs_observed"].append(p_attr)
                    
                    # Cleanup lost tracks
                    for tid in list(active_tracks.keys()):
                        if tid not in seen_track_ids:
                            active_tracks[tid]["lost_count"] += 1
                            if active_tracks[tid]["lost_count"] > MAX_LOST_FRAMES:
                                finalized_item = _finalize_and_save_track(camera_id, tid, active_tracks[tid])
                                if finalized_item:
                                    finalized_tracks.append(finalized_item)
                                del active_tracks[tid]
                
                _jobs[job_id]["progress"] = current_frame
                _jobs[job_id]["frames_saved"] = frames_saved
                _jobs[job_id]["frames_analyzed"] = frames_analyzed
                
            current_frame += 1
            
        if cap is not None:
            cap.release()
        
        # Save remaining active tracks
        for tid, track in active_tracks.items():
            finalized_item = _finalize_and_save_track(camera_id, tid, track)
            if finalized_item:
                finalized_tracks.append(finalized_item)
                
        # Generate and save comprehensive Video Analysis Summary
        _jobs[job_id]["stage"] = "Generating Video Analysis Summary"
        summary = generate_video_analysis_summary(camera_id, video_path, duration, resolution, fps, total_frames, frames_analyzed, finalized_tracks)
        
        execute_query(
            "UPDATE cameras SET status = 'READY', analysis_summary = ? WHERE id = ?",
            (json.dumps(summary), camera_id)
        )
        
        # Automatically update FAISS vector index with newly indexed events
        try:
            from backend.app.core.vector_search import get_vector_search
            get_vector_search().rebuild_from_db()
        except Exception as e:
            logger.warning(f"Error updating vector search index after processing: {e}")
            
        _jobs[job_id]["status"] = "completed"
        _jobs[job_id]["stage"] = "Analysis Complete"
        _jobs[job_id]["progress"] = total_frames
        _jobs[job_id]["events_count"] = len(finalized_tracks)
        logger.info(f"[ANALYSIS COMPLETE] camera_id={camera_id} dataset_id={dataset_id} events={len(finalized_tracks)} duration={duration:.1f}s fps={fps:.1f}")
        
    except Exception as e:
        logger.error(f"[ANALYSIS FAILED] camera_id={camera_id} error={e}", exc_info=True)
        execute_query("UPDATE cameras SET status = 'FAILED' WHERE id = ?", (camera_id,))
        _jobs[job_id]["status"] = "failed"
        _jobs[job_id]["error"] = str(e)

def _finalize_and_save_track(camera_id: str, tracking_id: int, track: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """
    Computes rigorous grounded attributes (color, movement, clothing, bag, accessories) and saves track event.
    """
    # Reject noise tracks that only appeared for 1 frame if confidence is low
    if len(track["bboxes"]) < 2 and track["max_conf"] < 0.25:
        return None
        
    label = track["label"]
    bboxes = track["bboxes"]
    crops = track["crops"]
    
    # 1. Movement & Action Analysis
    is_moving, action, displacement_px = analyze_track_movement(bboxes, concept=label)
    
    # 2. Dominant Color Analysis across crops (weighted consensus across observed frames)
    color = "unknown"
    color_conf = 0.0
    if crops:
        color_candidates = [analyze_crop_color(c, concept=label) for c in crops if c is not None]
        if color_candidates:
            color_scores = {}
            for col, conf in color_candidates:
                color_scores[col] = color_scores.get(col, 0.0) + conf
            best_color = max(color_scores.keys(), key=lambda c: color_scores[c])
            best_conf = min(1.0, color_scores[best_color] / len(color_candidates))
            color = best_color
            color_conf = best_conf

            
    # 3. Person specific attributes (Clothing & Accessories)
    upper_color = color
    upper_color_conf = color_conf
    upper_type = "shirt"
    lower_color = "unknown"
    lower_color_conf = 0.0
    lower_type = "pants"
    clothing_summary = f"{color} clothing"
    has_bag = False
    has_backpack = False
    has_handbag = False
    has_suitcase = False
    has_umbrella = False
    has_phone = False
    has_bottle = False
    accessories = []

    if label == "person":
        p_observations = track.get("person_attrs_observed", [])
        if p_observations:
            # Aggregate most frequent upper and lower colors
            upper_colors = [o["upper_color"] for o in p_observations if o["upper_color"] != "unknown"]
            if upper_colors:
                upper_color = max(set(upper_colors), key=upper_colors.count)
                upper_color_conf = max([o["upper_color_conf"] for o in p_observations if o["upper_color"] == upper_color] or [0.75])
            
            lower_colors = [o["lower_color"] for o in p_observations if o["lower_color"] != "unknown"]
            if lower_colors:
                lower_color = max(set(lower_colors), key=lower_colors.count)
                lower_color_conf = max([o["lower_color_conf"] for o in p_observations if o["lower_color"] == lower_color] or [0.75])

            # Associated accessory votes across frames
            has_backpack = sum(1 for o in p_observations if o.get("has_backpack")) >= 2
            has_handbag = sum(1 for o in p_observations if o.get("has_handbag")) >= 2
            has_suitcase = sum(1 for o in p_observations if o.get("has_suitcase")) >= 2
            has_umbrella = sum(1 for o in p_observations if o.get("has_umbrella")) >= 2
            has_phone = sum(1 for o in p_observations if o.get("has_phone")) >= 2
            has_bottle = sum(1 for o in p_observations if o.get("has_bottle")) >= 2
            has_bag = bool(has_backpack or has_handbag or has_suitcase or sum(1 for o in p_observations if o.get("has_bag")) >= 2)

            all_acc = []
            if has_backpack: all_acc.append("backpack")
            if has_handbag: all_acc.append("handbag")
            if has_suitcase: all_acc.append("suitcase")
            if has_bag and not all_acc: all_acc.append("bag")
            if has_umbrella: all_acc.append("umbrella")
            if has_phone: all_acc.append("phone")
            if has_bottle: all_acc.append("bottle")
            accessories = all_acc

            parts = [f"{upper_color} {upper_type}" if upper_color != "unknown" else "shirt"]
            if lower_color != "unknown":
                parts.append(f"{lower_color} {lower_type}")
            clothing_summary = " with ".join(parts)
        else:
            clothing_summary = f"{color} shirt" if color in ("black", "white", "dark", "blue", "red") else f"{color} clothing"
        
    attributes = {
        "object_type": label,
        "color": color,
        "color_confidence": round(color_conf, 2),
        "upper_color": upper_color,
        "upper_color_conf": round(upper_color_conf, 2),
        "upper_type": upper_type,
        "lower_color": lower_color,
        "lower_color_conf": round(lower_color_conf, 2),
        "lower_type": lower_type,
        "clothing": clothing_summary,
        "is_moving": is_moving,
        "action": action,
        "displacement_px": round(displacement_px, 1),
        "has_bag": has_bag,
        "has_backpack": has_backpack,
        "has_handbag": has_handbag,
        "has_suitcase": has_suitcase,
        "has_umbrella": has_umbrella,
        "has_phone": has_phone,
        "has_bottle": has_bottle,
        "accessories": accessories
    }
    
    track["attributes"] = attributes
    event_id = save_track_event(camera_id, tracking_id, track)
    
    return {
        "event_id": event_id,
        "track_id": tracking_id,
        "label": label,
        "start_time": track["start_time"],
        "end_time": track["end_time"],
        "duration": track["end_time"] - track["start_time"],
        "max_conf": track["max_conf"],
        "attributes": attributes,
        "representative_frame": track["representative_frame"]
    }

def generate_video_analysis_summary(
    camera_id: str,
    video_path: str,
    duration: float,
    resolution: str,
    fps: float,
    total_frames: int,
    frames_analyzed: int,
    tracks: List[Dict[str, Any]]
) -> Dict[str, Any]:
    """
    Generates a structured, evidence-grounded summary of the actual video content.
    """
    file_name = os.path.basename(video_path)
    
    # Categorize objects
    person_tracks = [t for t in tracks if t["label"] == "person"]
    car_tracks = [t for t in tracks if t["label"] in ("car", "truck", "bus", "white vehicle")]
    motorcycle_tracks = [t for t in tracks if t["label"] in ("motorcycle", "bicycle")]
    bag_tracks = [t for t in tracks if t["label"] in ("bag", "backpack", "suitcase") or t["attributes"].get("has_bag")]
    
    persons_summary = []
    for p in person_tracks:
        attr = p["attributes"]
        persons_summary.append({
            "track_id": p["track_id"],
            "first_seen": f"{p['start_time']:.2f}s",
            "last_seen": f"{p['end_time']:.2f}s",
            "movement": "YES" if attr["is_moving"] else "NO",
            "approximate_clothing": attr["clothing"] or "dark/neutral clothing",
            "has_bag": "YES" if attr["has_bag"] else "NO"
        })
        
    cars_summary = []
    for c in car_tracks:
        attr = c["attributes"]
        cars_summary.append({
            "track_id": c["track_id"],
            "first_seen": f"{c['start_time']:.2f}s",
            "last_seen": f"{c['end_time']:.2f}s",
            "movement": "YES" if attr["is_moving"] else "NO",
            "approximate_color": attr["color"]
        })
        
    red_cars = [c for c in car_tracks if c["attributes"]["color"] == "red"]
    
    return {
        "camera_id": camera_id,
        "file_name": file_name,
        "duration_seconds": round(duration, 2),
        "resolution": resolution,
        "fps": round(fps, 1),
        "total_frames": total_frames,
        "frames_analyzed": frames_analyzed,
        "total_events_detected": len(tracks),
        "detected_objects": {
            "PERSON": {
                "detected": len(person_tracks) > 0,
                "count": len(person_tracks),
                "details": persons_summary if person_tracks else "No reliable detection"
            },
            "CAR / VEHICLE": {
                "detected": len(car_tracks) > 0,
                "count": len(car_tracks),
                "details": cars_summary if car_tracks else "No reliable detection"
            },
            "BAG / BACKPACK": {
                "detected": len(bag_tracks) > 0,
                "count": len(bag_tracks),
                "details": "Detected" if bag_tracks else "No reliable detection"
            },
            "MOTORCYCLE": {
                "detected": len(motorcycle_tracks) > 0,
                "count": len(motorcycle_tracks),
                "details": "Detected" if motorcycle_tracks else "No reliable detection"
            },
            "RED CAR": {
                "detected": len(red_cars) > 0,
                "count": len(red_cars),
                "details": "Detected" if red_cars else "No reliable detection"
            }
        }
    }

def start_video_processing(camera_id: str, video_path: str, frame_interval: int = 15) -> str:
    job_id = str(uuid.uuid4())
    thread = threading.Thread(
        target=process_video_job,
        args=(job_id, camera_id, video_path, frame_interval)
    )
    thread.daemon = True
    thread.start()
    return job_id
