from fastapi import APIRouter, HTTPException, Query
from typing import List, Optional, Dict, Any
from pydantic import BaseModel
from backend.app.core.embeddings import get_embedder
from backend.app.core.vector_search import get_vector_search
import time

router = APIRouter()

class SearchResponse(BaseModel):
    event: str
    camera: str
    timestamp: float
    similarity: float
    concept: str
    frame: str
    
class SearchResult(BaseModel):
    results: List[SearchResponse]
    latency_ms: float

@router.get("/search", response_model=SearchResult)
def search_by_text(query: str, top_k: int = 5):
    t0 = time.time()
    
    embedder = get_embedder()
    vector_search = get_vector_search()
    
    if not embedder.available:
        raise HTTPException(status_code=500, detail="Embedding model not available")
        
    query_vector = embedder.embed_text(query)
    if query_vector is None:
        raise HTTPException(status_code=500, detail="Failed to embed text query")
        
    results = vector_search.search_by_text(query_vector, top_k=top_k)
    
    t1 = time.time()
    latency_ms = (t1 - t0) * 1000
    
    return {
        "results": results,
        "latency_ms": latency_ms
    }

@router.get("/search/cross-camera")
def search_cross_camera_api(
    camera_ids: Optional[str] = Query(None, description="Comma-separated camera IDs"),
    object_type: Optional[str] = Query(None, description="Object type (e.g. car, truck, person)"),
    color: Optional[str] = Query(None, description="Color (e.g. red, white, black)"),
    min_timestamp: Optional[float] = Query(None),
    max_timestamp: Optional[float] = Query(None)
):
    from backend.app.db.database import execute_query
    vsearch = get_vector_search()
    target_cams = [c.strip() for c in camera_ids.split(",") if c.strip()] if camera_ids else []
    if len(target_cams) < 2:
        rows = execute_query("SELECT id FROM cameras ORDER BY created_at ASC LIMIT 4", fetch=True) or []
        target_cams = [r["id"] for r in rows][:2]
        
    if len(target_cams) < 2:
        return {
            "verdict": "NO_MATCH",
            "is_candidate_match": False,
            "message": "At least two cameras are required for cross-camera correlation.",
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

