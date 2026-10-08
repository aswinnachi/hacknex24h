from fastapi import APIRouter, HTTPException, Query
from typing import List
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
