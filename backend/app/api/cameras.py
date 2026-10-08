import os
import shutil
import json
import time
import uuid
import glob
import cv2
from datetime import datetime
from fastapi import APIRouter, HTTPException, UploadFile, File, Form, Query as FastQuery
from pydantic import BaseModel
from typing import Optional, List, Dict, Any
from backend.app.db.database import execute_query
from backend.app.core.video import start_video_processing, get_job_status, _jobs
from backend.app.core.vector_search import get_vector_search
from backend.app.core.logging import logger

router = APIRouter()

class CameraCreate(BaseModel):
    id: str
    name: str
    description: Optional[str] = None
    video_path: str
    source_type: Optional[str] = "uploaded"
    dataset_id: Optional[str] = None
    is_demo: Optional[int] = 0

class CameraResponse(BaseModel):
    id: str
    name: str
    description: Optional[str] = None
    video_path: Optional[str] = None
    fps: Optional[float] = None
    duration: Optional[float] = None
    resolution: Optional[str] = None
    width: Optional[int] = None
    height: Optional[int] = None
    status: Optional[str] = "READY"
    analysis_summary: Optional[Dict[str, Any]] = None
    source_type: Optional[str] = "uploaded"
    dataset_id: Optional[str] = None
    is_demo: Optional[int] = 0
    created_at: Optional[float] = None

class ResetRequest(BaseModel):
    confirm: bool

@router.get("/cameras", response_model=List[CameraResponse])
def get_cameras(include_demo: bool = False):
    if include_demo:
        rows = execute_query("SELECT * FROM cameras ORDER BY created_at DESC, id ASC", fetch=True)
    else:
        rows = execute_query("SELECT * FROM cameras WHERE is_demo = 0 ORDER BY created_at DESC, id ASC", fetch=True)
        
    results = []
    for row in rows:
        item = dict(row)
        if item.get("analysis_summary"):
            try: item["analysis_summary"] = json.loads(item["analysis_summary"])
            except: pass
        results.append(item)
    return results

@router.get("/cameras/{camera_id}", response_model=CameraResponse)
def get_camera(camera_id: str):
    row = execute_query("SELECT * FROM cameras WHERE id = ?", (camera_id,), fetch=True, fetch_one=True)
    if not row:
        raise HTTPException(status_code=404, detail="Camera not found")
    item = dict(row)
    if item.get("analysis_summary"):
        try: item["analysis_summary"] = json.loads(item["analysis_summary"])
        except: pass
    return item

@router.get("/cameras/{camera_id}/summary")
def get_camera_summary(camera_id: str):
    row = execute_query("SELECT id, name, status, duration, fps, resolution, analysis_summary FROM cameras WHERE id = ?", (camera_id,), fetch=True, fetch_one=True)
    if not row:
        raise HTTPException(status_code=404, detail="Camera not found")
        
    if row["analysis_summary"]:
        try:
            return json.loads(row["analysis_summary"])
        except Exception:
            return {"summary": row["analysis_summary"]}
            
    # If summary is not yet generated because analysis is in progress
    row_dict = dict(row)
    status = row_dict.get("status") or "ANALYZING"
    return {
        "camera_id": row_dict["id"],
        "name": row_dict["name"],
        "status": status,
        "duration_seconds": row_dict.get("duration"),
        "fps": row_dict.get("fps"),
        "resolution": row_dict.get("resolution"),
        "detected_objects": {},
        "message": f"Analysis is currently {status}"
    }

@router.get("/jobs/{job_id}")
def get_processing_job_status(job_id: str):
    status = get_job_status(job_id)
    if not status:
        raise HTTPException(status_code=404, detail="Job not found")
    return status

@router.get("/debug/state")
def get_debug_state():
    cameras_rows = execute_query("SELECT * FROM cameras", fetch=True)
    events_count = execute_query("SELECT COUNT(*) as cnt FROM events", fetch=True, fetch_one=True)["cnt"]
    events_by_cam_rows = execute_query("SELECT camera_id, COUNT(*) as cnt FROM events GROUP BY camera_id", fetch=True)
    
    events_by_cam = {r["camera_id"]: r["cnt"] for r in events_by_cam_rows}
    cams_list = [dict(r) for r in cameras_rows]
    active_cams = [c["id"] for c in cams_list if c.get("status") == "READY"]
    
    return {
        "cameras_count": len(cams_list),
        "active_cameras": active_cams,
        "cameras": cams_list,
        "events_count": events_count,
        "events_by_camera": events_by_cam,
        "jobs": _jobs
    }

@router.delete("/cameras/{camera_id}")
def delete_camera(camera_id: str):
    logger.info(f"Request to delete camera {camera_id}")
    cam = execute_query("SELECT * FROM cameras WHERE id = ?", (camera_id,), fetch=True, fetch_one=True)
    if not cam:
        raise HTTPException(status_code=404, detail="Camera not found")
    
    events = execute_query("SELECT id, representative_frame, clip_path FROM events WHERE camera_id = ?", (camera_id,), fetch=True)
    deleted_events_count = len(events)
    
    for ev in events:
        ev_dict = dict(ev)
        frame_path = ev_dict.get("representative_frame")
        clip_path = ev_dict.get("clip_path")
        ev_id = ev_dict.get("id")
        
        if frame_path and os.path.exists(frame_path):
            try: os.remove(frame_path)
            except Exception as e: logger.warning(f"Error removing frame {frame_path}: {e}")
            
        if clip_path and os.path.exists(clip_path):
            try: os.remove(clip_path)
            except Exception as e: logger.warning(f"Error removing clip {clip_path}: {e}")
            
        for cfile in glob.glob(f"data/clips/{ev_id}*.mp4"):
            try: os.remove(cfile)
            except Exception: pass

    cam_video_dir = f"data/videos/{camera_id}"
    if os.path.exists(cam_video_dir):
        try:
            shutil.rmtree(cam_video_dir)
            logger.info(f"Removed camera video directory: {cam_video_dir}")
        except Exception as e:
            logger.warning(f"Error removing video directory {cam_video_dir}: {e}")
            
    execute_query("DELETE FROM events WHERE camera_id = ?", (camera_id,))
    execute_query("DELETE FROM memory WHERE camera_id = ?", (camera_id,))
    execute_query("DELETE FROM cameras WHERE id = ?", (camera_id,))
    
    try:
        get_vector_search().rebuild_from_db()
    except Exception as e:
        logger.warning(f"Failed to rebuild vector index after camera deletion: {e}")
        
    return {
        "success": True,
        "camera_id": camera_id,
        "deleted_events": deleted_events_count,
        "message": f"Camera {camera_id} and all {deleted_events_count} associated events removed completely."
    }

@router.post("/videos/register", response_model=CameraResponse)
def register_video(camera: CameraCreate):
    created_at = time.time()
    dataset_id = camera.dataset_id or str(uuid.uuid4())
    execute_query(
        "INSERT OR REPLACE INTO cameras (id, name, description, video_path, source_type, dataset_id, is_demo, status, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (camera.id, camera.name, camera.description, camera.video_path, camera.source_type or "registered", dataset_id, camera.is_demo or 0, "READY", created_at)
    )
    row = execute_query("SELECT * FROM cameras WHERE id = ?", (camera.id,), fetch=True, fetch_one=True)
    item = dict(row)
    if item.get("analysis_summary"):
        try: item["analysis_summary"] = json.loads(item["analysis_summary"])
        except: pass
    return item

@router.post("/videos/upload")
async def upload_video(
    file: UploadFile = File(...),
    camera_name: Optional[str] = Form(None),
    camera_id: Optional[str] = Form(None),
    frame_interval: int = Form(15)
):
    filename = file.filename
    ext = os.path.splitext(filename)[1].lower()
    supported = [".mp4", ".mov", ".avi", ".mkv", ".jpg", ".jpeg", ".png", ".webp"]
    
    if ext not in supported:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported format '{ext}'. Supported CCTV formats are: {', '.join(supported)}"
        )
        
    # Auto-generate camera ID if omitted (find lowest unused camera_XX)
    if not camera_id:
        existing_rows = execute_query("SELECT id FROM cameras", fetch=True)
        existing_ids = set(r["id"] for r in existing_rows)
        idx = 1
        while f"camera_{idx:02d}" in existing_ids:
            idx += 1
        camera_id = f"camera_{idx:02d}"
        
    dataset_id = str(uuid.uuid4())
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
    is_img = ext in (".jpg", ".jpeg", ".png", ".webp")
    source_type = "image" if is_img else "uploaded"

    if not camera_name or not camera_name.strip():
        camera_name = f"Uploaded {'Snapshot' if is_img else 'CCTV'} — {now_str}"
    else:
        camera_name = camera_name.strip()
        
    upload_dir = f"data/videos/{camera_id}"
    os.makedirs(upload_dir, exist_ok=True)
    save_path = os.path.join(upload_dir, filename)
    
    with open(save_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)
        
    # Synchronously extract video / image dimensions from saved file
    try:
        if is_img:
            img = cv2.imread(save_path)
            if img is not None:
                height, width = img.shape[:2]
                fps, duration, resolution = 1.0, 1.0, f"{width}x{height}"
            else:
                fps, duration, resolution, width, height = 1.0, 1.0, "768x432", 768, 432
        else:
            cap = cv2.VideoCapture(save_path)
            fps = cap.get(cv2.CAP_PROP_FPS) or 12.0
            total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
            width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
            height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
            duration = total_frames / fps if fps > 0 else 0.0
            resolution = f"{width}x{height}" if width > 0 else "768x432"
            cap.release()
    except Exception as e:
        logger.warning(f"Error inspecting media properties: {e}")
        fps, duration, resolution, width, height = 12.0, 0.0, "768x432", 768, 432
        
    created_at = time.time()
    logger.info(f"[UPLOAD] filename={filename} camera_id={camera_id} dataset_id={dataset_id} video_path={save_path} duration={duration:.1f}s resolution={resolution} fps={fps:.1f}")
    
    # Register in DB with extracted metadata and status = 'ANALYZING'
    execute_query(
        """
        INSERT OR REPLACE INTO cameras 
        (id, name, description, video_path, fps, duration, resolution, width, height, status, source_type, dataset_id, is_demo, created_at) 
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (camera_id, camera_name, f"Uploaded CCTV footage: {filename}", save_path, fps, duration, resolution, width, height, "ANALYZING", "uploaded", dataset_id, 0, created_at)
    )
    
    # Trigger background video processing
    job_id = start_video_processing(camera_id, save_path, frame_interval)
    
    return {
        "status": "ANALYZING",
        "camera_id": camera_id,
        "camera_name": camera_name,
        "dataset_id": dataset_id,
        "file_name": filename,
        "video_path": save_path,
        "video": {
            "fps": fps,
            "duration": duration,
            "resolution": resolution,
            "width": width,
            "height": height
        },
        "job_id": job_id
    }

@router.post("/data/reset")
def reset_all_data(req: Optional[ResetRequest] = None):
    if req is not None and not req.confirm:
        raise HTTPException(status_code=400, detail="Must confirm reset with confirm=true")
        
    logger.warning("RESETTING ALL APPLICATION DATA...")
    
    execute_query("DELETE FROM events")
    execute_query("DELETE FROM cameras")
    execute_query("DELETE FROM memory")
    execute_query("DELETE FROM queries")
    
    dirs_to_clean = ["data/frames", "data/clips", "data/videos"]
    for d in dirs_to_clean:
        if os.path.exists(d):
            try:
                shutil.rmtree(d)
                os.makedirs(d, exist_ok=True)
            except Exception as e:
                logger.warning(f"Error cleaning {d}: {e}")
                
    get_vector_search().reset()
    
    logger.info("All data reset complete. 0 cameras, 0 events, 0 vectors.")
    return {
        "success": True,
        "cameras_count": 0,
        "events_count": 0,
        "message": "All uploaded videos, cameras, events, frames, clips, and vector indexes have been completely reset."
    }
