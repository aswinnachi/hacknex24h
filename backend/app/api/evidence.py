from fastapi import APIRouter, HTTPException
from typing import Optional, List
from pydantic import BaseModel
from backend.app.core.evidence import generate_evidence_clip

router = APIRouter()

class SceneObject(BaseModel):
    event_id: str
    tracking_id: Optional[str] = None
    concept: str
    color: Optional[str] = "unknown"
    clothing: Optional[str] = ""
    has_bag: Optional[bool] = False
    is_moving: Optional[bool] = False
    confidence: Optional[float] = 0.8
    bounding_boxes: List[List[float]] = []
    is_target: bool = False

class EvidenceResponse(BaseModel):
    event_id: str
    verdict: str
    summary: str
    concept: str
    camera_id: str
    camera_name: str
    tracking_id: Optional[str] = None
    timestamp: float
    time_formatted: str
    time_hms: Optional[str] = None
    video_offset: str
    confidence: float
    relevance_percent: float
    frame_path: str
    frame_url: str
    clip_path: Optional[str] = None
    clip_url: Optional[str] = None
    clip_start: float
    clip_end: float
    clip_duration: float
    bounding_boxes: List[List[float]] = []
    video_width: Optional[int] = 854
    video_height: Optional[int] = 480
    attributes: Optional[dict] = {}
    color: Optional[str] = None
    scene_objects: Optional[List[SceneObject]] = []

@router.get("/evidence/{event_id}", response_model=EvidenceResponse)
def get_event_evidence(event_id: str):
    evidence = generate_evidence_clip(event_id)
    if not evidence:
        raise HTTPException(status_code=404, detail="Event not found or evidence could not be retrieved")
    return evidence
