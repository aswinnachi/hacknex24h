from fastapi import APIRouter, HTTPException, Query
from typing import Optional, List, Dict, Any
from pydantic import BaseModel
from backend.app.db.database import execute_query
import json

router = APIRouter()

class EventResponse(BaseModel):
    id: str
    camera_id: str
    camera_name: Optional[str] = None
    dataset_id: Optional[str] = None
    tracking_id: Optional[str] = None
    concept: str
    start_timestamp: float
    end_timestamp: float
    confidence: float
    bounding_boxes: List[List[float]] = []
    representative_frame: Optional[str] = None
    clip_path: Optional[str] = None
    embedding_ref: Optional[str] = None
    attributes: Optional[Dict[str, Any]] = None
    source_type: Optional[str] = "uploaded"
    session_id: Optional[str] = None
    frame_number: Optional[int] = None

class EventSearchResponse(BaseModel):
    total_count: int
    limit: int
    offset: int
    events: List[EventResponse]

def _format_event_row(row_dict: Dict[str, Any]) -> Dict[str, Any]:
    event = dict(row_dict)
    if event.get("bounding_boxes"):
        if isinstance(event["bounding_boxes"], str):
            try:
                event["bounding_boxes"] = json.loads(event["bounding_boxes"])
            except Exception:
                event["bounding_boxes"] = []
    else:
        event["bounding_boxes"] = []

    if event.get("attributes"):
        if isinstance(event["attributes"], str):
            try:
                event["attributes"] = json.loads(event["attributes"])
            except Exception:
                event["attributes"] = {}
    else:
        event["attributes"] = {}

    if not event.get("source_type"):
        event["source_type"] = "uploaded"

    return event

@router.get("/events/search", response_model=EventSearchResponse)
def search_events(
    camera_id: Optional[str] = None,
    concept: Optional[str] = None,
    class_name: Optional[str] = None,
    tracking_id: Optional[str] = None,
    track_id: Optional[str] = None,
    start_time: Optional[float] = None,
    end_time: Optional[float] = None,
    source_type: Optional[str] = None,
    min_confidence: Optional[float] = None,
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0)
):
    base_where = """
        FROM events
        JOIN cameras ON cameras.id = events.camera_id
        WHERE (cameras.status IN ('READY', 'CONNECTED') OR events.source_type IN ('live', 'youtube_live'))
          AND (cameras.dataset_id IS NULL OR cameras.dataset_id = events.dataset_id)
    """
    params = []

    if camera_id:
        base_where += " AND events.camera_id = ?"
        params.append(camera_id)
    c_filter = concept or class_name
    if c_filter:
        base_where += " AND LOWER(events.concept) = ?"
        params.append(c_filter.lower().strip())
    t_filter = tracking_id or track_id
    if t_filter:
        base_where += " AND events.tracking_id = ?"
        params.append(str(t_filter).strip())
    if source_type:
        base_where += " AND events.source_type = ?"
        params.append(source_type.strip().lower())
    if start_time is not None:
        base_where += " AND events.end_timestamp >= ?"
        params.append(start_time)
    if end_time is not None:
        base_where += " AND events.start_timestamp <= ?"
        params.append(end_time)
    if min_confidence is not None:
        base_where += " AND events.confidence >= ?"
        params.append(min_confidence)

    # Count total matching
    count_row = execute_query(f"SELECT COUNT(*) as cnt {base_where}", tuple(params), fetch=True, fetch_one=True)
    total_count = count_row["cnt"] if count_row else 0

    # Fetch page
    select_query = f"SELECT events.*, cameras.name as camera_name {base_where} ORDER BY events.start_timestamp DESC LIMIT ? OFFSET ?"
    rows = execute_query(select_query, tuple(params + [limit, offset]), fetch=True) or []

    events = [_format_event_row(r) for r in rows]
    return {
        "total_count": total_count,
        "limit": limit,
        "offset": offset,
        "events": events
    }

@router.get("/events", response_model=List[EventResponse])
def get_events(
    camera_id: Optional[str] = None,
    concept: Optional[str] = None,
    class_name: Optional[str] = None,
    tracking_id: Optional[str] = None,
    track_id: Optional[str] = None,
    start_time: Optional[float] = None,
    end_time: Optional[float] = None,
    source_type: Optional[str] = None,
    min_confidence: Optional[float] = None,
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0)
):
    query = """
        SELECT events.*, cameras.name as camera_name FROM events
        JOIN cameras ON cameras.id = events.camera_id
        WHERE (cameras.status IN ('READY', 'CONNECTED') OR events.source_type IN ('live', 'youtube_live'))
          AND (cameras.dataset_id IS NULL OR cameras.dataset_id = events.dataset_id)
    """
    params = []
    
    if camera_id:
        query += " AND events.camera_id = ?"
        params.append(camera_id)
    c_filter = concept or class_name
    if c_filter:
        query += " AND LOWER(events.concept) = ?"
        params.append(c_filter.lower().strip())
    t_filter = tracking_id or track_id
    if t_filter:
        query += " AND events.tracking_id = ?"
        params.append(str(t_filter).strip())
    if source_type:
        query += " AND events.source_type = ?"
        params.append(source_type.strip().lower())
    if start_time is not None:
        query += " AND events.end_timestamp >= ?"
        params.append(start_time)
    if end_time is not None:
        query += " AND events.start_timestamp <= ?"
        params.append(end_time)
    if min_confidence is not None:
        query += " AND events.confidence >= ?"
        params.append(min_confidence)
        
    query += " ORDER BY events.start_timestamp DESC LIMIT ? OFFSET ?"
    params.extend([limit, offset])
    
    rows = execute_query(query, tuple(params), fetch=True) or []
    return [_format_event_row(r) for r in rows]

@router.get("/events/{event_id}", response_model=EventResponse)
def get_event(event_id: str):
    row = execute_query(
        """
        SELECT events.*, cameras.name as camera_name FROM events
        JOIN cameras ON cameras.id = events.camera_id
        WHERE events.id = ?
        """,
        (event_id,),
        fetch=True,
        fetch_one=True
    )
    if not row:
        row = execute_query("SELECT * FROM events WHERE id = ?", (event_id,), fetch=True, fetch_one=True)
        if not row:
            raise HTTPException(status_code=404, detail="Event not found")
        
    return _format_event_row(row)

