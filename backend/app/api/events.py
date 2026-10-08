from fastapi import APIRouter, HTTPException, Query
from typing import Optional, List
from pydantic import BaseModel
from backend.app.db.database import execute_query
import json

router = APIRouter()

class EventResponse(BaseModel):
    id: str
    camera_id: str
    tracking_id: Optional[str]
    concept: str
    start_timestamp: float
    end_timestamp: float
    confidence: float
    bounding_boxes: List[List[float]]
    representative_frame: str
    clip_path: Optional[str] = None
    embedding_ref: Optional[str] = None

@router.get("/events", response_model=List[EventResponse])
def get_events(
    camera_id: Optional[str] = None,
    concept: Optional[str] = None,
    min_confidence: Optional[float] = None
):
    query = "SELECT * FROM events WHERE 1=1"
    params = []
    
    if camera_id:
        query += " AND camera_id = ?"
        params.append(camera_id)
    if concept:
        query += " AND concept = ?"
        params.append(concept)
    if min_confidence:
        query += " AND confidence >= ?"
        params.append(min_confidence)
        
    query += " ORDER BY start_timestamp DESC"
    
    rows = execute_query(query, tuple(params), fetch=True)
    events = []
    for row in rows:
        event = dict(row)
        if event.get("bounding_boxes"):
            try:
                event["bounding_boxes"] = json.loads(event["bounding_boxes"])
            except:
                event["bounding_boxes"] = []
        events.append(event)
    return events

@router.get("/events/{event_id}", response_model=EventResponse)
def get_event(event_id: str):
    row = execute_query("SELECT * FROM events WHERE id = ?", (event_id,), fetch=True, fetch_one=True)
    if not row:
        raise HTTPException(status_code=404, detail="Event not found")
        
    event = dict(row)
    if event.get("bounding_boxes"):
        try:
            event["bounding_boxes"] = json.loads(event["bounding_boxes"])
        except:
            event["bounding_boxes"] = []
    return event
