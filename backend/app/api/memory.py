from fastapi import APIRouter, HTTPException
from typing import Optional, List, Dict, Any
from pydantic import BaseModel
from backend.app.core.memory import get_memory_manager

router = APIRouter()

class MemoryCreate(BaseModel):
    name: str
    camera_id: str
    region: Optional[List[float]] = None
    metadata: Optional[Dict[str, Any]] = None

class MemoryResponse(BaseModel):
    id: str
    key_name: str
    camera_id: Optional[str] = None
    region: Optional[List[float]] = None
    metadata: Optional[Dict[str, Any]] = None
    created_at: float
    updated_at: float

@router.post("/memory", response_model=MemoryResponse)
def create_or_update_memory(memory_in: MemoryCreate):
    mgr = get_memory_manager()
    record = mgr.add_memory(
        name=memory_in.name,
        camera_id=memory_in.camera_id,
        region=memory_in.region,
        metadata=memory_in.metadata
    )
    if not record:
        raise HTTPException(status_code=500, detail="Failed to save memory record")
    return record

@router.get("/memory", response_model=List[MemoryResponse])
def list_memories():
    mgr = get_memory_manager()
    return mgr.get_all_memories()

@router.get("/memory/{name_or_id}", response_model=MemoryResponse)
def get_memory(name_or_id: str):
    mgr = get_memory_manager()
    record = mgr.get_memory_by_name(name_or_id)
    if not record:
        record = mgr.get_memory_by_id(name_or_id)
    if not record:
        raise HTTPException(status_code=404, detail=f"Memory reference '{name_or_id}' not found")
    return record

@router.delete("/memory/{memory_id}")
def delete_memory(memory_id: str):
    mgr = get_memory_manager()
    mgr.delete_memory(memory_id)
    return {"status": "deleted", "id": memory_id}
