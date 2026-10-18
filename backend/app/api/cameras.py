import os
import shutil
import json
import time
import uuid
import glob
import cv2
from datetime import datetime
from fastapi import APIRouter, HTTPException, UploadFile, File, Form, Query as FastQuery
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from typing import Optional, List, Dict, Any
from backend.app.db.database import execute_query
from backend.app.core.video import start_video_processing, get_job_status, _jobs
from backend.app.core.vector_search import get_vector_search
from backend.app.core.stream_manager import get_stream_manager, sanitize_stream_url
from backend.app.core.youtube_resolver import is_youtube_url, validate_youtube_url
from backend.app.core.logging import logger

router = APIRouter()

class CameraConnectRequest(BaseModel):
    camera_id: Optional[str] = None
    name: Optional[str] = None
    stream_url: str
    source_type: Optional[str] = "live"
    timeout: Optional[float] = 8.0
    record_stream: Optional[bool] = False
    max_storage_mb: Optional[float] = 5000.0
    retention_days: Optional[float] = 7.0

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

class BatchConnectRequest(BaseModel):
    camera_ids: Optional[List[str]] = None

@router.get("/cameras", response_model=List[CameraResponse])
def get_cameras(include_demo: bool = False):
    if include_demo:
        rows = execute_query("SELECT * FROM cameras ORDER BY created_at DESC, id ASC", fetch=True)
    else:
        rows = execute_query("SELECT * FROM cameras WHERE is_demo = 0 ORDER BY created_at DESC, id ASC", fetch=True)
        
    results = []
    stream_mgr = get_stream_manager()
    for row in rows:
        item = dict(row)
        if item.get("analysis_summary"):
            try: item["analysis_summary"] = json.loads(item["analysis_summary"])
            except: pass
        if item.get("source_type") in ("live", "youtube_live"):
            stream = stream_mgr.get_stream(item["id"])
            if stream and stream.is_connected:
                item["status"] = "CONNECTED"
            elif item.get("status") != "DISCONNECTED":
                item["status"] = "DISCONNECTED"
        results.append(item)
    return results

@router.get("/cameras/detections/recent")
def get_recent_live_detections(
    camera_id: Optional[str] = None,
    limit: int = FastQuery(50, ge=1, le=200)
):
    mgr = get_stream_manager()
    detections = mgr.get_recent_detections(camera_id=camera_id, limit=limit)
    return {
        "count": len(detections),
        "limit": limit,
        "camera_id": camera_id,
        "detections": detections
    }

@router.get("/cameras/cross-match")
def cross_camera_candidate_match(
    camera_ids: Optional[str] = FastQuery(None, description="Comma-separated camera IDs (e.g. cam1,cam2)"),
    object_type: Optional[str] = FastQuery(None, description="Object type to correlate (e.g. car, truck, person)"),
    color: Optional[str] = FastQuery(None, description="Color attribute filter (e.g. red, white, black)"),
    min_timestamp: Optional[float] = FastQuery(None),
    max_timestamp: Optional[float] = FastQuery(None)
):
    """
    Stage I Cross-Camera Candidate Association.
    Correlates candidate vehicles and objects across distinct cameras based on:
    - Object class compatibility
    - Color attribute agreement
    - Spatiotemporal proximity & camera sequence plausibility
    
    IMPORTANT: Candidate associations are labeled with explicit confidence and evidence.
    Single-camera ByteTrack IDs are strictly scoped per camera and NOT treated as global identities.
    """
    vsearch = get_vector_search()
    target_cams = [c.strip() for c in camera_ids.split(",") if c.strip()] if camera_ids else []
    
    if len(target_cams) < 2:
        rows = execute_query("SELECT id FROM cameras ORDER BY created_at ASC LIMIT 4", fetch=True) or []
        target_cams = [r["id"] for r in rows][:2]
        
    if len(target_cams) < 2:
        return {
            "verdict": "NO_MATCH",
            "is_candidate_match": False,
            "message": "At least two cameras are required for cross-camera candidate association.",
            "candidates": []
        }
        
    res = vsearch.search_cross_camera(
        camera_ids=target_cams,
        required_object=object_type,
        required_color=color,
        min_timestamp=min_timestamp,
        max_timestamp=max_timestamp
    )
    
    return {
        "verdict": res.get("verdict", "NO"),
        "is_candidate_match": res.get("is_match", False),
        "confidence": res.get("confidence", 0.0),
        "summary": res.get("summary", ""),
        "evidence_breakdown": res.get("debug", {}),
        "candidate_events": res.get("results", []),
        "disclaimer": "Candidate association based on spatiotemporal proximity and visual attributes. Does not claim global biometric re-identification."
    }

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

@router.get("/cameras/{camera_id}/scene")
def get_camera_scene(camera_id: str):
    row = execute_query("SELECT id, name, video_path, duration, width, height FROM cameras WHERE id = ?", (camera_id,), fetch=True, fetch_one=True)
    if not row:
        raise HTTPException(status_code=404, detail="Camera not found")
    cam = dict(row)
    video_path = cam.get("video_path")
    if not video_path or not os.path.exists(video_path):
        return {
            "camera_id": camera_id,
            "camera_name": cam.get("name"),
            "duration": cam.get("duration", 10.0),
            "video_width": cam.get("width") or 848,
            "video_height": cam.get("height") or 478,
            "scene_objects": []
        }
    
    from backend.app.core.evidence import compute_dense_clip_scene
    scene_objects = compute_dense_clip_scene(video_path)
    return {
        "camera_id": camera_id,
        "camera_name": cam.get("name"),
        "duration": cam.get("duration", 10.0),
        "video_width": cam.get("width") or 848,
        "video_height": cam.get("height") or 478,
        "scene_objects": scene_objects
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
    try:
        get_stream_manager().disconnect_camera(camera_id)
    except Exception as e:
        logger.warning(f"Error disconnecting camera {camera_id} during deletion: {e}")

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

@router.post("/cameras/connect")
def connect_camera_stream(req: CameraConnectRequest):
    stream_url = req.stream_url.strip()
    if not stream_url:
        raise HTTPException(status_code=400, detail="stream_url is required")
        
    camera_id = req.camera_id.strip() if req.camera_id else None
    if not camera_id:
        existing_rows = execute_query("SELECT id FROM cameras", fetch=True)
        existing_ids = set(r["id"] for r in existing_rows)
        idx = 1
        while f"camera_{idx:02d}" in existing_ids:
            idx += 1
        camera_id = f"camera_{idx:02d}"

    camera_name = req.name.strip() if req.name and req.name.strip() else f"Live Stream {camera_id}"
    sanitized_url = sanitize_stream_url(stream_url)
    # Source type detection and validation
    is_yt = is_youtube_url(stream_url) or req.source_type == "youtube_live"
    source_type = "youtube_live" if is_yt else (req.source_type or "live")
    if is_yt:
        valid, vid_id, val_err = validate_youtube_url(stream_url)
        if not valid:
            raise HTTPException(status_code=400, detail=val_err)

    default_timeout = 18.0 if is_yt else 8.0
    timeout = max(1.0, min(req.timeout or default_timeout, 30.0))


    mgr = get_stream_manager()
    existing = mgr.get_stream(camera_id)
    if existing and existing.is_connected:
        return {
            "success": True,
            "status": "connected",
            "camera_id": camera_id,
            "camera_name": existing.name,
            "source_type": existing.source_type,
            "stream_url": existing.sanitized_url,
            "fps": existing.fps,
            "resolution": existing.resolution,
            "width": existing.width,
            "height": existing.height,
            "is_recording": existing.recorder.is_recording,
            "recording_stats": existing.recorder.get_storage_stats() if existing.record_stream else None,
            "message": "Camera already connected."
        }

    logger.info(f"[CAMERA CONNECT] camera_id={camera_id} source={sanitized_url} source_type={source_type}")
    connected, status = mgr.connect_camera(
        camera_id=camera_id,
        name=camera_name,
        stream_url=stream_url,
        timeout=timeout,
        source_type=source_type,
        record_stream=bool(req.record_stream),
        max_storage_mb=req.max_storage_mb or 5000.0,
        retention_days=req.retention_days or 7.0
    )

    if not connected:
        err_msg = status.get("error_message") or "Failed to connect to stream"
        logger.warning(f"[CAMERA CONNECT FAILED] camera_id={camera_id} error={err_msg}")
        raise HTTPException(
            status_code=400,
            detail=f"Failed to connect to camera: {err_msg}"
        )

    dataset_id = str(uuid.uuid4())
    created_at = time.time()
    
    video_identifier = None
    if is_yt:
        from backend.app.core.youtube_resolver import extract_youtube_video_id
        video_identifier = extract_youtube_video_id(stream_url)

    session_id = str(uuid.uuid4())
    summary_data = {
        "video_identifier": video_identifier,
        "source_type": source_type,
        "ingestion_mode": "backend_decoded_ai",
        "is_recording": bool(status.get("is_recording", False)),
        "session_history": [
            {
                "session_id": session_id,
                "started_at": created_at,
                "status": "CONNECTED",
                "fps": status.get("fps", 0),
                "resolution": status.get("resolution", "unknown")
            }
        ]
    }

    execute_query(
        """
        INSERT OR REPLACE INTO cameras 
        (id, name, description, video_path, fps, duration, resolution, width, height, status, source_type, dataset_id, is_demo, created_at, analysis_summary)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            camera_id,
            camera_name,
            f"Live stream feed: {sanitized_url}",
            sanitized_url,
            status.get("fps", 25.0),
            0.0,
            status.get("resolution", "unknown"),
            status.get("width", 0),
            status.get("height", 0),
            "CONNECTED",
            source_type,
            dataset_id,
            0,
            created_at,
            json.dumps(summary_data)
        )
    )

    return {
        "success": True,
        "status": "connected",
        "camera_id": camera_id,
        "camera_name": camera_name,
        "source_type": source_type,
        "stream_url": sanitized_url,
        "fps": status.get("fps"),
        "resolution": status.get("resolution"),
        "width": status.get("width"),
        "height": status.get("height"),
        "dataset_id": dataset_id,
        "is_recording": status.get("is_recording", False),
        "recording_stats": status.get("recording_stats")
    }

@router.post("/cameras/connect-all")
def connect_all_cameras(req: Optional[BatchConnectRequest] = None):
    """
    Connects all registered live and youtube_live cameras (or requested camera_ids) in parallel.
    """
    if req and req.camera_ids and len(req.camera_ids) > 0:
        placeholders = ",".join("?" for _ in req.camera_ids)
        rows = execute_query(
            f"SELECT id, name, video_path, source_type FROM cameras WHERE id IN ({placeholders}) AND source_type IN ('live', 'youtube_live')",
            tuple(req.camera_ids),
            fetch=True
        )
    else:
        rows = execute_query(
            "SELECT id, name, video_path, source_type FROM cameras WHERE source_type IN ('live', 'youtube_live')",
            fetch=True
        )
    if not rows:
        return {"success": True, "connected_count": 0, "results": []}

    mgr = get_stream_manager()
    results = []

    def _connect_single(row):
        cam = dict(row)
        c_id = cam["id"]
        c_name = cam.get("name") or c_id
        url = cam.get("video_path")
        stype = cam.get("source_type") or "live"
        if not url:
            return {"camera_id": c_id, "success": False, "error": "No stream URL"}
        existing = mgr.get_stream(c_id)
        if existing and existing.is_connected:
            execute_query("UPDATE cameras SET status = 'CONNECTED' WHERE id = ?", (c_id,))
            return {"camera_id": c_id, "success": True, "status": "already_connected"}
        conn, stat = mgr.connect_camera(
            camera_id=c_id,
            name=c_name,
            stream_url=url,
            source_type=stype,
            timeout=20.0 if stype == "youtube_live" else 8.0,
            record_stream=False
        )
        if conn:
            execute_query("UPDATE cameras SET status = 'CONNECTED' WHERE id = ?", (c_id,))
        return {"camera_id": c_id, "success": conn, "status": stat.get("status", "error")}

    import concurrent.futures
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
        futures = [executor.submit(_connect_single, r) for r in rows]
        for f in concurrent.futures.as_completed(futures):
            try:
                results.append(f.result())
            except Exception as e:
                results.append({"success": False, "error": str(e)})

    connected_count = sum(1 for r in results if r.get("success"))
    return {"success": True, "connected_count": connected_count, "results": results}

@router.get("/cameras/{camera_id}/status")
def get_camera_status(camera_id: str):
    mgr = get_stream_manager()
    stream = mgr.get_stream(camera_id)
    if stream:
        return stream.get_status()

    cam_row = execute_query("SELECT * FROM cameras WHERE id = ?", (camera_id,), fetch=True, fetch_one=True)
    if not cam_row:
        raise HTTPException(status_code=404, detail="Camera not found")

    cam = dict(cam_row)
    return {
        "camera_id": camera_id,
        "name": cam.get("name"),
        "status": "disconnected" if cam.get("source_type") == "live" else cam.get("status", "READY"),
        "is_connected": False,
        "stream_url": cam.get("video_path"),
        "fps": cam.get("fps"),
        "resolution": cam.get("resolution"),
        "width": cam.get("width"),
        "height": cam.get("height"),
        "source_type": cam.get("source_type", "uploaded")
    }

@router.get("/cameras/{camera_id}/live")
def get_camera_live_stream(camera_id: str):
    mgr = get_stream_manager()
    stream = mgr.get_stream(camera_id)
    if not stream or not stream.is_connected:
        raise HTTPException(status_code=404, detail=f"Live stream not active or disconnected for camera {camera_id}")

    return StreamingResponse(
        stream.generate_mjpeg_stream(fps_limit=15.0),
        media_type="multipart/x-mixed-replace; boundary=frame"
    )

@router.post("/cameras/{camera_id}/disconnect")
def disconnect_camera_stream(camera_id: str):
    mgr = get_stream_manager()
    disconnected = mgr.disconnect_camera(camera_id)

    execute_query("UPDATE cameras SET status = 'DISCONNECTED' WHERE id = ? AND source_type IN ('live', 'youtube_live')", (camera_id,))

    return {
        "success": True,
        "camera_id": camera_id,
        "status": "disconnected"
    }

@router.get("/cameras/{camera_id}/recordings")
def get_camera_recordings(camera_id: str):
    mgr = get_stream_manager()
    stream = mgr.get_stream(camera_id)
    if stream:
        return stream.recorder.get_storage_stats()
    
    from backend.app.core.stream_recorder import StreamRecorder
    rec = StreamRecorder(camera_id=camera_id)
    return rec.get_storage_stats()

@router.post("/cameras/{camera_id}/recordings/cleanup")
def cleanup_camera_recordings(camera_id: str):
    mgr = get_stream_manager()
    stream = mgr.get_stream(camera_id)
    if stream:
        result = stream.recorder.cleanup_old_recordings()
        stats = stream.recorder.get_storage_stats()
        return {"success": True, "cleaned": result, "stats": stats}
    
    from backend.app.core.stream_recorder import StreamRecorder
    rec = StreamRecorder(camera_id=camera_id)
    result = rec.cleanup_old_recordings()
    stats = rec.get_storage_stats()
    return {"success": True, "cleaned": result, "stats": stats}

@router.get("/cameras/detections/matrix")
def get_cameras_matrix_detections():
    """
    Returns latest detections and tracking status across all connected streams in one fast call.
    """
    mgr = get_stream_manager()
    return mgr.get_all_active_detections()

@router.get("/cameras/{camera_id}/detections/latest")
def get_camera_latest_detections(camera_id: str):
    mgr = get_stream_manager()
    stream = mgr.get_stream(camera_id)
    if stream:
        return stream.get_latest_detections()

    cam_row = execute_query("SELECT id, source_type, status FROM cameras WHERE id = ?", (camera_id,), fetch=True, fetch_one=True)
    if not cam_row:
        raise HTTPException(status_code=404, detail="Camera not found")

    return {
        "camera_id": camera_id,
        "status": "disconnected",
        "timestamp": time.time(),
        "frame_number": 0,
        "latency_ms": 0.0,
        "avg_latency_ms": 0.0,
        "fps": 0.0,
        "frames_analyzed": 0,
        "count": 0,
        "detections": []
    }

@router.get("/cameras/{camera_id}/detections/status")
def get_camera_detection_status(camera_id: str):
    mgr = get_stream_manager()
    stream = mgr.get_stream(camera_id)
    if stream:
        return stream.get_detection_status()

    cam_row = execute_query("SELECT id FROM cameras WHERE id = ?", (camera_id,), fetch=True, fetch_one=True)
    if not cam_row:
        raise HTTPException(status_code=404, detail="Camera not found")

    return {
        "camera_id": camera_id,
        "is_analyzing": False,
        "frames_analyzed": 0,
        "last_latency_ms": 0.0,
        "avg_latency_ms": 0.0,
        "fps": 0.0,
        "total_detections": 0,
        "last_analyzed_time": 0.0,
        "active_tracks_count": 0
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
    
    try:
        get_stream_manager().disconnect_all()
    except Exception as e:
        logger.warning(f"Error disconnecting all streams during reset: {e}")

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
