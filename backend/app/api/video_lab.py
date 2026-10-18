import os
import uuid
import time
import json
import shutil
from typing import Optional, List, Dict, Any
from fastapi import APIRouter, UploadFile, File, Form, HTTPException, Query, Response, status
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

from backend.app.core.logging import logger
from backend.app.core.storage import get_storage
from backend.app.core.video_lab import (
    validate_video_file,
    start_lab_job_in_background,
    request_job_cancellation
)
from backend.app.db.database import execute_query

router = APIRouter()

class StartAnalysisRequest(BaseModel):
    frame_interval: int = 15

@router.post("/lab/upload")
async def upload_video(
    file: UploadFile = File(...),
    session_id: Optional[str] = Form(None)
):
    """
    Accepts video upload, validates magic bytes and format, saves to persistent storage,
    and initializes an analysis job in 'queued' state.
    """
    filename = file.filename or "video.mp4"
    job_id = str(uuid.uuid4())
    temp_dir = os.path.abspath(os.path.join("data", "temp_uploads"))
    os.makedirs(temp_dir, exist_ok=True)
    temp_path = os.path.join(temp_dir, f"{job_id}_{filename}")

    try:
        # Save to temp location for inspection
        with open(temp_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)

        # Validate video integrity, magic bytes, and decodability
        meta = validate_video_file(temp_path, filename)

        # Store permanently via storage abstraction
        storage = get_storage()
        storage_key = f"uploads/{job_id}/{filename}"
        storage.save_file(temp_path, storage_key, content_type="video/mp4")

        now = time.time()
        execute_query(
            """
            INSERT INTO analysis_jobs 
            (id, owner_session, filename, file_size, duration, fps, resolution, total_frames, status, stage, progress_pct, original_video_key, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                job_id,
                session_id or "default",
                filename,
                meta["file_size"],
                meta["duration"],
                meta["fps"],
                meta["resolution"],
                meta["total_frames"],
                "queued",
                "Ready for AI Analysis",
                0.0,
                storage_key,
                now
            )
        )

        logger.info(f"[VideoLab] Video uploaded and job created: job_id={job_id} filename={filename} size={meta['file_size']} duration={meta['duration']}s")

        return {
            "job_id": job_id,
            "filename": filename,
            "file_size": meta["file_size"],
            "duration": meta["duration"],
            "resolution": meta["resolution"],
            "fps": meta["fps"],
            "total_frames": meta["total_frames"],
            "status": "queued",
            "message": "Video verified and stored successfully. Ready for analysis."
        }

    except Exception as e:
        logger.error(f"[VideoLab] Upload failed: {e}")
        raise HTTPException(status_code=400, detail=str(e))
    finally:
        if os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except Exception:
                pass


@router.post("/lab/jobs/{job_id}/start")
def start_analysis_job(job_id: str, req: Optional[StartAnalysisRequest] = None):
    """
    Submits a queued video analysis job to the background worker.
    """
    row = execute_query("SELECT * FROM analysis_jobs WHERE id = ?", (job_id,), fetch=True, fetch_one=True)
    if not row:
        raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found")

    job = dict(row)
    if job.get("status") == "processing":
        return {"job_id": job_id, "status": "processing", "message": "Job is already running"}

    interval = req.frame_interval if req else 15
    start_lab_job_in_background(job_id, frame_interval=interval)

    return {
        "job_id": job_id,
        "status": "processing",
        "message": "AI analysis job submitted to background worker"
    }


@router.post("/lab/upload-and-analyze")
async def upload_and_analyze(
    file: UploadFile = File(...),
    frame_interval: int = Form(15),
    session_id: Optional[str] = Form(None)
):
    """
    Seamless combined endpoint: uploads, validates, and immediately launches analysis.
    """
    upload_res = await upload_video(file=file, session_id=session_id)
    job_id = upload_res["job_id"]
    start_lab_job_in_background(job_id, frame_interval=frame_interval)
    upload_res["status"] = "processing"
    upload_res["message"] = "Analysis started"
    return upload_res


@router.get("/lab/jobs/{job_id}/status")
def get_job_status(job_id: str):
    """
    Returns real-time processing progress and status.
    """
    row = execute_query("SELECT * FROM analysis_jobs WHERE id = ?", (job_id,), fetch=True, fetch_one=True)
    if not row:
        raise HTTPException(status_code=404, detail="Job not found")

    job = dict(row)
    return {
        "job_id": job["id"],
        "filename": job["filename"],
        "status": job["status"],
        "stage": job.get("stage") or "Processing",
        "progress_pct": job.get("progress_pct", 0.0),
        "current_frame": job.get("current_frame", 0),
        "total_frames": job.get("total_frames", 0),
        "frames_analyzed": job.get("frames_analyzed", 0),
        "unique_tracks_count": job.get("unique_tracks_count", 0),
        "total_detections_count": job.get("total_detections_count", 0),
        "error_message": job.get("error_message"),
        "created_at": job.get("created_at"),
        "started_at": job.get("started_at"),
        "completed_at": job.get("completed_at")
    }


@router.get("/lab/jobs/{job_id}/results")
def get_job_results(job_id: str):
    """
    Returns comprehensive tracking results: original & annotated video URLs,
    unique tracks, detection counts, per-class summaries, and interactive event timeline.
    """
    row = execute_query("SELECT * FROM analysis_jobs WHERE id = ?", (job_id,), fetch=True, fetch_one=True)
    if not row:
        raise HTTPException(status_code=404, detail="Job not found")

    job = dict(row)
    storage = get_storage()

    orig_url = storage.get_url(job["original_video_key"]) if job.get("original_video_key") else None
    annotated_url = storage.get_url(job["annotated_video_key"]) if job.get("annotated_video_key") else None

    # Parse stored summary if available
    summary = {}
    if job.get("summary_json"):
        try:
            summary = json.loads(job["summary_json"])
        except Exception:
            pass

    # Fetch detection timeline
    det_rows = execute_query(
        """
        SELECT id, tracking_id, track_state, label, confidence, raw_confidence,
               timestamp_offset, frame_index, bbox_x1, bbox_y1, bbox_x2, bbox_y2,
               attributes, evidence_frame_key
        FROM lab_detections 
        WHERE job_id = ? 
        ORDER BY timestamp_offset ASC, tracking_id ASC
        """,
        (job_id,),
        fetch=True
    )

    detections = []
    for r in (det_rows or []):
        d = dict(r)
        d["bbox"] = [d["bbox_x1"], d["bbox_y1"], d["bbox_x2"], d["bbox_y2"]]
        d["evidence_url"] = storage.get_url(d["evidence_frame_key"]) if d.get("evidence_frame_key") else None
        detections.append(d)

    return {
        "job_id": job["id"],
        "filename": job["filename"],
        "file_size": job.get("file_size"),
        "duration": job.get("duration"),
        "fps": job.get("fps"),
        "resolution": job.get("resolution"),
        "status": job["status"],
        "progress_pct": job.get("progress_pct", 100.0),
        "created_at": job.get("created_at"),
        "completed_at": job.get("completed_at"),
        "original_video_url": orig_url,
        "annotated_video_url": annotated_url,
        "json_results_url": f"/api/lab/jobs/{job_id}/download/json",
        "csv_results_url": f"/api/lab/jobs/{job_id}/download/csv",
        "annotated_video_download_url": f"/api/lab/jobs/{job_id}/download/video",
        "metrics": {
            "unique_tracks_count": job.get("unique_tracks_count", 0),
            "total_detections_count": job.get("total_detections_count", 0),
            "frames_analyzed": job.get("frames_analyzed", 0),
            "total_frames": job.get("total_frames", 0),
            "class_counts": summary.get("class_counts", {})
        },
        "summary": summary,
        "detections": detections,
        "disclaimer": "ByteTrack IDs are trajectory sequence tokens local to this video file and do not establish biometric real-world identity."
    }


@router.get("/lab/jobs/{job_id}/download/{file_type}")
def download_job_file(job_id: str, file_type: str):
    """
    Downloads annotated video, structured JSON, or CSV summary.
    """
    row = execute_query("SELECT * FROM analysis_jobs WHERE id = ?", (job_id,), fetch=True, fetch_one=True)
    if not row:
        raise HTTPException(status_code=404, detail="Job not found")

    job = dict(row)
    storage = get_storage()

    if file_type == "video":
        key = job.get("annotated_video_key")
        filename = f"annotated_{job['filename']}"
        media_type = "video/mp4"
    elif file_type == "json":
        key = job.get("json_results_key")
        filename = f"tracking_results_{job_id}.json"
        media_type = "application/json"
    elif file_type == "csv":
        key = job.get("csv_results_key")
        filename = f"tracking_results_{job_id}.csv"
        media_type = "text/csv"
    else:
        raise HTTPException(status_code=400, detail="Invalid file_type. Must be 'video', 'json', or 'csv'")

    if not key:
        raise HTTPException(status_code=404, detail=f"Requested {file_type} file is not available for this job")

    local_path = storage.get_local_path(key)
    if not os.path.exists(local_path):
        raise HTTPException(status_code=404, detail=f"File not found on storage: {key}")

    return FileResponse(
        path=local_path,
        media_type=media_type,
        filename=filename
    )


@router.get("/lab/jobs")
def list_jobs(limit: int = 50):
    """
    Lists job history with status and summary metrics.
    """
    rows = execute_query(
        """
        SELECT id, filename, file_size, duration, resolution, status, stage, 
               progress_pct, unique_tracks_count, total_detections_count, 
               created_at, completed_at
        FROM analysis_jobs 
        ORDER BY created_at DESC 
        LIMIT ?
        """,
        (limit,),
        fetch=True
    )
    return [dict(r) for r in (rows or [])]


@router.post("/lab/jobs/{job_id}/cancel")
def cancel_job(job_id: str):
    """
    Safely requests worker cancellation for an ongoing or queued job.
    """
    row = execute_query("SELECT status FROM analysis_jobs WHERE id = ?", (job_id,), fetch=True, fetch_one=True)
    if not row:
        raise HTTPException(status_code=404, detail="Job not found")

    status_val = row["status"]
    if status_val in ("completed", "failed", "cancelled"):
        return {"job_id": job_id, "status": status_val, "message": f"Job is already {status_val}"}

    request_job_cancellation(job_id)
    execute_query("UPDATE analysis_jobs SET status = 'cancelled', stage = 'Analysis Cancelled by User' WHERE id = ?", (job_id,))

    return {"job_id": job_id, "status": "cancelled", "message": "Cancellation request submitted"}


@router.delete("/lab/jobs/{job_id}")
def delete_job(job_id: str):
    """
    Deletes the analysis job, associated detections, and cleans up all stored files.
    """
    row = execute_query("SELECT * FROM analysis_jobs WHERE id = ?", (job_id,), fetch=True, fetch_one=True)
    if not row:
        raise HTTPException(status_code=404, detail="Job not found")

    job = dict(row)
    storage = get_storage()

    # Cancel if running
    request_job_cancellation(job_id)

    # Delete files from storage
    keys_to_delete = [
        job.get("original_video_key"),
        job.get("annotated_video_key"),
        job.get("json_results_key"),
        job.get("csv_results_key")
    ]

    # Also delete evidence files
    ev_rows = execute_query("SELECT evidence_frame_key FROM lab_detections WHERE job_id = ?", (job_id,), fetch=True)
    for er in (ev_rows or []):
        if er["evidence_frame_key"]:
            keys_to_delete.append(er["evidence_frame_key"])

    for k in keys_to_delete:
        if k:
            try:
                storage.delete_file(k)
            except Exception as e:
                logger.warning(f"[VideoLab] Error deleting key {k}: {e}")

    # Remove work directory if it exists
    work_dir = os.path.abspath(os.path.join("data", "lab_processing", job_id))
    if os.path.exists(work_dir):
        try:
            shutil.rmtree(work_dir)
        except Exception:
            pass

    # Remove from DB
    execute_query("DELETE FROM lab_detections WHERE job_id = ?", (job_id,))
    execute_query("DELETE FROM analysis_jobs WHERE id = ?", (job_id,))

    logger.info(f"[VideoLab] Deleted job {job_id} and cleaned all associated storage.")
    return {"job_id": job_id, "status": "deleted", "message": "Job and associated data successfully deleted"}


@router.api_route("/lab/files/{file_path:path}", methods=["GET", "HEAD"])
def serve_storage_file(file_path: str, response: Response):
    """
    Serves files from local storage with proper content headers.
    """
    storage = get_storage()
    clean_path = storage.get_local_path(file_path)

    if not os.path.exists(clean_path):
        raise HTTPException(status_code=404, detail=f"File not found: {file_path}")

    ext = os.path.splitext(clean_path)[1].lower()
    media_types = {
        ".mp4": "video/mp4",
        ".mov": "video/quicktime",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".webp": "image/webp",
        ".json": "application/json",
        ".csv": "text/csv"
    }
    media_type = media_types.get(ext, "application/octet-stream")

    return FileResponse(clean_path, media_type=media_type)
