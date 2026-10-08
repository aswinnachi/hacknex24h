import time
import uuid
import json
import re
from typing import Optional, List, Dict, Any
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from backend.app.core.logging import logger
from backend.app.core.embeddings import get_embedder
from backend.app.core.vector_search import get_vector_search
from backend.app.core.query_parser import get_query_parser
from backend.app.db.database import execute_query

router = APIRouter()

class QueryRequest(BaseModel):
    query: str
    top_k: int = 5
    camera_id: Optional[str] = None

class TimeFilterInfo(BaseModel):
    type: str
    label: str
    min_timestamp: Optional[float] = None
    max_timestamp: Optional[float] = None
    delta_seconds: Optional[float] = None

class MemoryReferenceInfo(BaseModel):
    id: str
    name: str
    camera_id: str
    region: Optional[List[float]] = None

class CameraOption(BaseModel):
    id: str
    name: str

class ParsedQuery(BaseModel):
    original_query: str
    intent: str
    visual_concept: str
    color: Optional[str] = None
    object: Optional[str] = None
    action: Optional[str] = None
    secondary_object: Optional[str] = None
    relation: Optional[str] = None
    camera: Optional[str] = None
    camera_label: Optional[str] = None
    resolved_memory: Optional[MemoryReferenceInfo] = None
    needs_clarification: bool = False
    clarification_prompt: Optional[str] = None
    missing_reference: Optional[str] = None
    time_filter: Optional[TimeFilterInfo] = None

class QueryMatch(BaseModel):
    event: str
    camera: str
    tracking_id: Optional[str] = None
    timestamp: float
    end_timestamp: Optional[float] = None
    similarity: float = 0.0
    concept: str
    attributes: Optional[Dict[str, Any]] = None
    frame: str

class QueryResponse(BaseModel):
    query: str
    verdict: str # MATCH, NO MATCH, YES, NO, COUNT RESULT, INVENTORY
    summary: str
    count: Optional[int] = None
    debug: Optional[Dict[str, Any]] = None
    needs_clarification: bool = False
    clarification_prompt: Optional[str] = None
    missing_reference: Optional[str] = None
    available_cameras: Optional[List[CameraOption]] = None
    parsed: ParsedQuery
    results: List[QueryMatch]
    total_matches: int
    latency_ms: float

@router.post("/query", response_model=QueryResponse)
def execute_natural_language_query(request: QueryRequest):
    start_time = time.time()
    
    # 1. Deterministic NLP Query Parsing & Intent Extraction
    parser = get_query_parser()
    parsed_info = parser.parse(request.query)
    
    # 2. Check if Clarify-Once is needed (unmapped landmark)
    if parsed_info.get("needs_clarification"):
        cam_rows = execute_query("SELECT id, name FROM cameras WHERE is_demo = 0", fetch=True)
        available_cams = [{"id": r["id"], "name": r["name"]} for r in cam_rows]
        latency_ms = (time.time() - start_time) * 1000
        return {
            "query": request.query,
            "verdict": "CLARIFICATION REQUIRED",
            "summary": parsed_info["clarification_prompt"],
            "count": None,
            "needs_clarification": True,
            "clarification_prompt": parsed_info["clarification_prompt"],
            "missing_reference": parsed_info["missing_reference"],
            "available_cameras": available_cams,
            "parsed": parsed_info,
            "results": [],
            "total_matches": 0,
            "latency_ms": latency_ms
        }
    
    intent = parsed_info["intent"]
    
    # Priority for Camera scoping:
    # 1. Explicit camera in query text
    # 2. Scoped camera from user's selected camera in frontend
    camera_id = parsed_info["camera"]
    if not camera_id and request.camera_id and request.camera_id != "all":
        camera_id = request.camera_id
    elif camera_id == "all" or request.camera_id == "all":
        camera_id = None
        
    logger.info(f"[QUERY] camera_id={camera_id or 'all'} query=\"{request.query}\" intent={intent}")
    
    # Verify overall camera presence in DB
    cam_rows = execute_query("SELECT id, name, status FROM cameras WHERE is_demo = 0", fetch=True)
    all_cams = [dict(r) for r in cam_rows] if cam_rows else []
    if not all_cams:
        latency_ms = (time.time() - start_time) * 1000
        logger.warning("[QUERY ERROR] No cameras registered in system.")
        return {
            "query": request.query,
            "verdict": "NO MATCH",
            "summary": "No active cameras. Upload CCTV video to begin.",
            "count": None,
            "needs_clarification": False,
            "clarification_prompt": None,
            "missing_reference": None,
            "available_cameras": [],
            "parsed": parsed_info,
            "results": [],
            "total_matches": 0,
            "latency_ms": latency_ms
        }
        
    if camera_id:
        target_cam = next((c for c in all_cams if c["id"] == camera_id), None)
        if not target_cam:
            latency_ms = (time.time() - start_time) * 1000
            logger.warning(f"[QUERY ERROR] No READY camera for camera_id={camera_id}")
            return {
                "query": request.query,
                "verdict": "NO MATCH",
                "summary": f"Camera '{camera_id}' not found.",
                "count": None,
                "needs_clarification": False,
                "clarification_prompt": None,
                "missing_reference": None,
                "available_cameras": None,
                "parsed": parsed_info,
                "results": [],
                "total_matches": 0,
                "latency_ms": latency_ms
            }
        if target_cam.get("status") == "ANALYZING":
            latency_ms = (time.time() - start_time) * 1000
            return {
                "query": request.query,
                "verdict": "NO MATCH",
                "summary": f"Video analysis is currently in progress for {target_cam['name']}. Please wait a moment for event indexing to complete.",
                "count": None,
                "needs_clarification": False,
                "clarification_prompt": None,
                "missing_reference": None,
                "available_cameras": None,
                "parsed": parsed_info,
                "results": [],
                "total_matches": 0,
                "latency_ms": latency_ms
            }

    time_filter = parsed_info["time_filter"]
    visual_concept = parsed_info["visual_concept"]
    req_obj = parsed_info["object"]
    req_color = parsed_info["color"]
    req_upper_color = parsed_info.get("upper_color")
    req_lower_color = parsed_info.get("lower_color")
    req_action = parsed_info["action"]
    sec_obj = parsed_info["secondary_object"]
    requires_bag = parsed_info.get("requires_bag", False)
    forbid_bag = parsed_info.get("forbid_bag", False)
    req_accessories = parsed_info.get("required_accessories", [])
    forbid_accessories = parsed_info.get("forbidden_accessories", [])
    forbidden_objects = parsed_info.get("forbidden_objects", [])
    
    resolved_mem = parsed_info.get("resolved_memory")
    region = resolved_mem.get("region") if resolved_mem else None
    
    min_timestamp = time_filter["min_timestamp"] if time_filter else None
    max_timestamp = time_filter["max_timestamp"] if time_filter else None
    
    vector_search = get_vector_search()
    
    # 3. Handle Special Intent: INVENTORY (List detected objects)
    if intent == "INVENTORY":
        if camera_id:
            cam_row = execute_query("SELECT dataset_id FROM cameras WHERE id = ?", (camera_id,), fetch=True, fetch_one=True)
            active_dataset_id = cam_row["dataset_id"] if cam_row else None
            if active_dataset_id:
                events_rows = execute_query("SELECT concept, COUNT(DISTINCT tracking_id) as cnt FROM events WHERE camera_id = ? AND dataset_id = ? GROUP BY concept", (camera_id, active_dataset_id), fetch=True)
            else:
                events_rows = execute_query("SELECT concept, COUNT(DISTINCT tracking_id) as cnt FROM events WHERE camera_id = ? GROUP BY concept", (camera_id,), fetch=True)
        else:
            events_rows = execute_query("SELECT concept, COUNT(DISTINCT tracking_id) as cnt FROM events GROUP BY concept", fetch=True)
            
        items_desc = [f"{r['cnt']} unique {r['concept']}(s)" for r in events_rows]
        cam_scope_str = f" in {camera_id}" if camera_id else " in video"
        summary_text = f"Detected{cam_scope_str}: " + (", ".join(items_desc) if items_desc else "No objects detected.")
        latency_ms = (time.time() - start_time) * 1000
        return {
            "query": request.query,
            "verdict": "INVENTORY",
            "summary": summary_text,
            "count": len(events_rows),
            "needs_clarification": False,
            "clarification_prompt": None,
            "missing_reference": None,
            "available_cameras": None,
            "parsed": parsed_info,
            "results": [],
            "total_matches": len(events_rows),
            "latency_ms": latency_ms
        }

    # 4. Handle Special Intent: COUNT Query
    if intent == "COUNT":
        count_res = vector_search.query_count(
            required_object=req_obj,
            required_color=req_color,
            required_upper_color=req_upper_color,
            required_lower_color=req_lower_color,
            required_action=req_action,
            requires_bag=requires_bag,
            forbid_bag=forbid_bag,
            required_accessories=req_accessories,
            forbidden_accessories=forbid_accessories,
            forbidden_objects=forbidden_objects,
            camera_id=camera_id,
            min_timestamp=min_timestamp,
            max_timestamp=max_timestamp,
            region=region
        )
        total_count = count_res["count"]
        total_detections = count_res.get("total_detections", total_count)
        unique_tids = count_res.get("unique_tracking_ids", [])
        
        plural_obj = f"{req_obj}s" if (req_obj and not req_obj.endswith("s")) else (req_obj or "objects")
        if req_obj == "person":
            plural_obj = "people"
        singular_obj = req_obj or "object"
        
        # Build specific descriptive label for count summary
        qualifiers = []
        if req_upper_color:
            qualifiers.append(f"wearing {req_upper_color} shirts")
        elif req_color:
            qualifiers.append(req_color)
        if req_action:
            qualifiers.append(req_action)
        if req_accessories:
            qualifiers.append(f"with {', '.join(req_accessories)}")
        elif requires_bag:
            qualifiers.append("carrying bags")
            
        qualifier_str = f" ({' '.join(qualifiers)})" if qualifiers else ""
        cam_label = parsed_info.get("camera_label") or ("all cameras" if not camera_id else camera_id)
        region_str = f" crossing {parsed_info['resolved_memory']['name']}" if parsed_info.get("resolved_memory") else ""
        
        if total_count == 0:
            summary_text = f"0 unique {plural_obj}{qualifier_str} detected in {cam_label}{region_str}."
        elif total_count == 1:
            summary_text = f"1 unique {singular_obj}{qualifier_str} detected in {cam_label}{region_str} (Track #{unique_tids[0] if unique_tids else '1'})."
        else:
            summary_text = f"{total_count} unique {plural_obj}{qualifier_str} detected in {cam_label}{region_str} (counted via unique tracking IDs)."
            
        debug_info = {
            "camera_id": camera_id or "all",
            "dataset_id": count_res.get("dataset_id"),
            "video_path": count_res.get("video_path"),
            "total_person_detections": total_detections,
            "unique_tracking_ids": unique_tids,
            "final_count": total_count
        }
        
        latency_ms = (time.time() - start_time) * 1000
        _record_query_log(request.query, latency_ms)
        
        return {
            "query": request.query,
            "verdict": "COUNT RESULT",
            "summary": summary_text,
            "count": total_count,
            "debug": debug_info,
            "needs_clarification": False,
            "clarification_prompt": None,
            "missing_reference": None,
            "available_cameras": None,
            "parsed": parsed_info,
            "results": count_res["events"][:request.top_k],
            "total_matches": total_count,
            "latency_ms": latency_ms
        }

    # 5. Handle SEARCH / YES_NO / TEMPORAL_SEARCH with Strict Semantic Retrieval & Attribute Grounding
    embedder = get_embedder()
    query_vector = embedder.embed_text(visual_concept) if embedder.available else None
    if query_vector is None:
        query_vector = [0.0] * 512
        
    matches, debug_trace = vector_search.search_by_text(
        text_vector=query_vector,
        top_k=request.top_k,
        required_object=req_obj,
        required_color=req_color,
        required_upper_color=req_upper_color,
        required_lower_color=req_lower_color,
        required_action=req_action,
        requires_bag=requires_bag,
        forbid_bag=forbid_bag,
        required_accessories=req_accessories,
        forbidden_accessories=forbid_accessories,
        forbidden_objects=forbidden_objects,
        camera_id=camera_id,
        min_timestamp=min_timestamp,
        max_timestamp=max_timestamp,
        region=region,
        return_debug=True
    )
    
    latency_ms = (time.time() - start_time) * 1000
    _record_query_log(request.query, latency_ms)
    
    total_matches = len(matches)
    
    # 6. Formulate Honest Grounded Verdict
    if total_matches > 0:
        top_match = matches[0]
        c_name = top_match["concept"]
        attr = top_match.get("attributes") or {}
        color_str = f"{attr.get('color')} " if attr.get("color") and attr.get("color") != "unknown" else ""
        if c_name in ("car", "bus", "truck", "motorcycle", "bicycle", "vehicle"):
            moving_str = "moving " if attr.get("is_moving") else ""
        else:
            act = attr.get('action') or ('moving' if attr.get('is_moving') else 'stationary')
            moving_str = f"{act} " if attr.get("is_moving") else ""
        
        # Construct exact description
        desc = f"{color_str}{moving_str}{c_name}".strip()
        if not desc:
            desc = c_name
        if forbid_bag:
            desc = "person (verified without bag/backpack)"
        elif req_accessories:
            desc = f"person carrying {', '.join(req_accessories)}"
        elif requires_bag:
            desc = "person carrying a bag/backpack"
        elif req_upper_color and req_lower_color and req_obj:
            desc = f"person wearing {req_upper_color} shirt and {req_lower_color} pants"
        elif req_upper_color and req_obj:
            desc = f"person wearing {req_upper_color} shirt"
        # Handle specific natural language questions
        q_lower = request.query.lower()
        if re.search(r"\b(?:what color|what colour|color of|colour of)\b", q_lower):
            verdict = "MATCH"
            car_color = attr.get('color') or 'unknown'
            summary = f"The detected {c_name} (Track #{top_match.get('tracking_id')}) is {car_color}."
        elif re.search(r"\b(?:what shirt|what is (?:the )?person wearing|what clothes|what clothing|what is he wearing|what is she wearing)\b", q_lower):
            verdict = "MATCH"
            clothing_info = attr.get('clothing') or (f"{attr.get('upper_color', 'unknown')} shirt")
            summary = f"The detected person (Track #{top_match.get('tracking_id')}) is wearing {clothing_info}."
        elif re.search(r"\b(?:having (?:a )?bag|has (?:a )?bag|carrying (?:a )?bag|does (?:the )?person have (?:a )?bag|with (?:a )?bag)\b", q_lower):
            verdict = "YES" if attr.get('has_bag') else "NO"
            if attr.get('has_bag'):
                acc_str = f" ({', '.join(attr.get('accessories', []))})" if attr.get('accessories') else ""
                summary = f"Yes, the detected person (Track #{top_match.get('tracking_id')}) is carrying a bag/backpack{acc_str}."
            else:
                summary = f"No, the detected person (Track #{top_match.get('tracking_id')}) is verified without a bag or backpack."
        elif intent == "YES_NO":
            verdict = "YES"
            summary = f"Yes, {desc} was detected in {top_match['camera']} at {top_match['timestamp']:.2f}s."
        else:
            verdict = "MATCH"
            summary = f"Verified {desc} detected in {top_match['camera']} (Track ID #{top_match.get('tracking_id')})."
    else:
        # NO MATCH / NO
        verdict = "NO" if intent == "YES_NO" else "NO MATCH"
        reasons = []
        if forbid_bag:
            reasons.append("No person without bag could be reliably verified.")
        elif req_accessories:
            reasons.append(f"No person carrying {', '.join(req_accessories)} was reliably detected in the uploaded video.")
        elif requires_bag:
            reasons.append("No person carrying a bag or backpack was reliably detected in the uploaded video.")
        elif req_upper_color and req_lower_color:
            reasons.append(f"No person wearing {req_upper_color} shirt and {req_lower_color} pants was detected in the uploaded video.")
        elif req_upper_color:
            reasons.append(f"No person wearing a {req_upper_color} shirt was detected in the uploaded video.")
        elif req_lower_color:
            reasons.append(f"No person wearing {req_lower_color} pants was detected in the uploaded video.")
        elif req_color and req_obj:
            reasons.append(f"No {req_color} {req_obj} satisfying the requested attributes was reliably detected in the uploaded video.")
        elif req_action and req_obj:
            reasons.append(f"No {req_action} {req_obj} satisfying the requested constraints was reliably detected in the uploaded video.")
        elif req_obj:
            reasons.append(f"No {req_obj} was detected matching the specified constraints.")
        else:
            reasons.append("No visual events matched the requested query.")
            
        summary = " ".join(reasons)

    # Attach developer debug information
    cam_row = execute_query("SELECT dataset_id, video_path FROM cameras WHERE id = ?", (camera_id or "camera_01",), fetch=True, fetch_one=True)
    debug_payload = {
        "camera_id": camera_id or "all",
        "dataset_id": cam_row["dataset_id"] if cam_row else None,
        "video_path": cam_row["video_path"] if cam_row else None,
        "candidates_checked": debug_trace.get("candidates_checked", 0),
        "rejected_candidates": debug_trace.get("rejected_candidates", []),
        "accepted_candidates": debug_trace.get("accepted_candidates", []),
        "parsed_constraints": {
            "required_object": req_obj,
            "required_color": req_color,
            "required_action": req_action,
            "requires_bag": requires_bag,
            "forbid_bag": forbid_bag,
            "forbidden_objects": forbidden_objects
        }
    }

    return {
        "query": request.query,
        "verdict": verdict,
        "summary": summary,
        "count": None,
        "debug": debug_payload,
        "needs_clarification": False,
        "clarification_prompt": None,
        "missing_reference": None,
        "available_cameras": None,
        "parsed": parsed_info,
        "results": matches,
        "total_matches": total_matches,
        "latency_ms": latency_ms
    }

def _record_query_log(query_text: str, latency_ms: float):
    try:
        query_id = str(uuid.uuid4())
        execute_query(
            "INSERT INTO queries (id, query_text, timestamp, latency) VALUES (?, ?, ?, ?)",
            (query_id, query_text, time.time(), latency_ms)
        )
    except Exception as e:
        logger.warning(f"Failed to record query in DB: {e}")
