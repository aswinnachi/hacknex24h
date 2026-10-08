from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from backend.app.core.video import start_video_processing, get_job_status
from backend.app.db.database import execute_query

router = APIRouter()

class ProcessRequest(BaseModel):
    camera_id: str
    frame_interval: int = 30

@router.post("/process")
def process_video(request: ProcessRequest):
    row = execute_query("SELECT video_path FROM cameras WHERE id = ?", (request.camera_id,), fetch=True, fetch_one=True)
    if not row:
        raise HTTPException(status_code=404, detail="Camera not found")
        
    video_path = row["video_path"]
    job_id = start_video_processing(request.camera_id, video_path, request.frame_interval)
    return {"job_id": job_id, "status": "started"}

@router.get("/process/status/{job_id}")
def process_status(job_id: str):
    status = get_job_status(job_id)
    if not status:
        raise HTTPException(status_code=404, detail="Job not found")
    return status
