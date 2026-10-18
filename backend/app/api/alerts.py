import os
import time
import json
import uuid
from typing import Optional, List, Dict, Any
from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, Field, validator

from backend.app.db.database import get_db_connection, execute_query
from backend.app.core.alert_engine import get_alert_engine
from backend.app.core.logging import logger

router = APIRouter()

SUPPORTED_RULE_TYPES = {
    "object_detected",
    "restricted_hours",
    "zone_entry",
    "tripwire_crossing",
    "vehicle_in_region",
    "indexed_event_violation"
}

SUPPORTED_SEVERITIES = {"info", "warning", "critical"}
SUPPORTED_DIRECTIONS = {"any", "ab", "ba"}

class AlertRuleCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    camera_id: Optional[str] = None
    rule_type: str
    object_class: Optional[str] = None
    min_confidence: float = Field(0.3, ge=0.0, le=1.0)
    time_start: Optional[str] = None # "HH:MM"
    time_end: Optional[str] = None   # "HH:MM"
    zone_points: Optional[List[List[float]]] = None # [[x, y], ...]
    tripwire_line: Optional[List[List[float]]] = None # [[x1, y1], [x2, y2]]
    direction: Optional[str] = "any"
    cooldown_seconds: float = Field(30.0, ge=0.0)
    severity: str = "warning"
    enabled: bool = True
    metadata: Optional[Dict[str, Any]] = None

    @validator("rule_type")
    def validate_rule_type(cls, v):
        if v not in SUPPORTED_RULE_TYPES:
            raise ValueError(f"Invalid rule_type '{v}'. Must be one of {sorted(SUPPORTED_RULE_TYPES)}")
        return v

    @validator("severity")
    def validate_severity(cls, v):
        if v.lower() not in SUPPORTED_SEVERITIES:
            raise ValueError(f"Invalid severity '{v}'. Must be one of {sorted(SUPPORTED_SEVERITIES)}")
        return v.lower()

    @validator("direction")
    def validate_direction(cls, v):
        if v and v.lower() not in SUPPORTED_DIRECTIONS:
            raise ValueError(f"Invalid direction '{v}'. Must be one of {sorted(SUPPORTED_DIRECTIONS)}")
        return (v.lower() if v else "any")

    @validator("tripwire_line")
    def validate_tripwire(cls, v, values):
        if values.get("rule_type") == "tripwire_crossing":
            if not v or len(v) != 2:
                raise ValueError("tripwire_line must contain exactly 2 points [[x1, y1], [x2, y2]]")
            for pt in v:
                if len(pt) != 2:
                    raise ValueError("Each point in tripwire_line must be [x, y]")
                if not (0.0 <= pt[0] <= 1.0 and 0.0 <= pt[1] <= 1.0):
                    raise ValueError("Tripwire coordinates must be normalized within [0.0, 1.0]")
        return v

    @validator("zone_points")
    def validate_zone(cls, v, values):
        if values.get("rule_type") in ("zone_entry", "vehicle_in_region"):
            if not v or len(v) < 3:
                raise ValueError("zone_points must contain at least 3 points forming a polygon")
            for pt in v:
                if len(pt) != 2:
                    raise ValueError("Each point in zone_points must be [x, y]")
                if not (0.0 <= pt[0] <= 1.0 and 0.0 <= pt[1] <= 1.0):
                    raise ValueError("Zone coordinates must be normalized within [0.0, 1.0]")
        return v

class AlertRuleUpdate(BaseModel):
    name: Optional[str] = None
    camera_id: Optional[str] = None
    rule_type: Optional[str] = None
    object_class: Optional[str] = None
    min_confidence: Optional[float] = Field(None, ge=0.0, le=1.0)
    time_start: Optional[str] = None
    time_end: Optional[str] = None
    zone_points: Optional[List[List[float]]] = None
    tripwire_line: Optional[List[List[float]]] = None
    direction: Optional[str] = None
    cooldown_seconds: Optional[float] = Field(None, ge=0.0)
    severity: Optional[str] = None
    enabled: Optional[bool] = None
    metadata: Optional[Dict[str, Any]] = None

def _format_rule_row(row: Dict[str, Any]) -> Dict[str, Any]:
    r = dict(row)
    r["enabled"] = bool(r.get("enabled", 1))
    if r.get("zone_points") and isinstance(r["zone_points"], str):
        try:
            r["zone_points"] = json.loads(r["zone_points"])
        except Exception:
            r["zone_points"] = None
    if r.get("tripwire_line") and isinstance(r["tripwire_line"], str):
        try:
            r["tripwire_line"] = json.loads(r["tripwire_line"])
        except Exception:
            r["tripwire_line"] = None
    if r.get("metadata") and isinstance(r["metadata"], str):
        try:
            r["metadata"] = json.loads(r["metadata"])
        except Exception:
            r["metadata"] = {}
    return r

def _format_alert_row(row: Dict[str, Any]) -> Dict[str, Any]:
    a = dict(row)
    if a.get("attributes") and isinstance(a["attributes"], str):
        try:
            a["attributes"] = json.loads(a["attributes"])
        except Exception:
            a["attributes"] = {}
    return a

# --- Rule Management Endpoints ---

@router.post("/alerts/rules", status_code=status.HTTP_201_CREATED)
def create_rule(rule: AlertRuleCreate):
    rule_id = f"rule_{uuid.uuid4().hex[:10]}"
    now = time.time()
    
    # If camera_id specified, check that camera exists
    if rule.camera_id:
        with get_db_connection() as conn:
            c = conn.execute("SELECT id FROM cameras WHERE id = ?", (rule.camera_id,)).fetchone()
            if not c:
                raise HTTPException(status_code=404, detail=f"Camera '{rule.camera_id}' not found.")
                
    zone_str = json.dumps(rule.zone_points) if rule.zone_points else None
    line_str = json.dumps(rule.tripwire_line) if rule.tripwire_line else None
    meta_str = json.dumps(rule.metadata or {})
    
    try:
        with get_db_connection() as conn:
            conn.execute("""
                INSERT INTO alert_rules (
                    id, name, enabled, camera_id, rule_type, object_class,
                    min_confidence, time_start, time_end, zone_points,
                    tripwire_line, direction, cooldown_seconds, severity,
                    metadata, created_at, updated_at
                ) VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                )
            """, (
                rule_id, rule.name, int(rule.enabled), rule.camera_id, rule.rule_type,
                rule.object_class.lower() if rule.object_class else None,
                rule.min_confidence, rule.time_start, rule.time_end,
                zone_str, line_str, rule.direction, rule.cooldown_seconds,
                rule.severity, meta_str, now, now
            ))
            conn.commit()
            
        get_alert_engine().reload_rules()
        logger.info(f"Created alert rule: {rule_id} ('{rule.name}')")
        return {"id": rule_id, "message": "Rule created successfully"}
    except Exception as e:
        logger.error(f"Error creating rule: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/alerts/rules")
def list_rules(camera_id: Optional[str] = None, enabled: Optional[bool] = None):
    query = "SELECT * FROM alert_rules WHERE 1=1"
    params = []
    if camera_id:
        query += " AND (camera_id = ? OR camera_id IS NULL)"
        params.append(camera_id)
    if enabled is not None:
        query += " AND enabled = ?"
        params.append(1 if enabled else 0)
    query += " ORDER BY created_at DESC"
    
    rows = execute_query(query, tuple(params), fetch=True) or []
    return [_format_rule_row(r) for r in rows]

@router.get("/alerts/rules/{rule_id}")
def get_rule(rule_id: str):
    rows = execute_query("SELECT * FROM alert_rules WHERE id = ?", (rule_id,), fetch=True)
    if not rows:
        raise HTTPException(status_code=404, detail=f"Rule '{rule_id}' not found.")
    return _format_rule_row(rows[0])

@router.put("/alerts/rules/{rule_id}")
def update_rule(rule_id: str, updates: AlertRuleUpdate):
    rows = execute_query("SELECT * FROM alert_rules WHERE id = ?", (rule_id,), fetch=True)
    if not rows:
        raise HTTPException(status_code=404, detail=f"Rule '{rule_id}' not found.")
    
    current = dict(rows[0])
    now = time.time()
    
    update_data = updates.dict(exclude_unset=True)
    if not update_data:
        return _format_rule_row(current)
        
    set_clauses = ["updated_at = ?"]
    params = [now]
    
    for key, val in update_data.items():
        if key == "zone_points":
            set_clauses.append("zone_points = ?")
            params.append(json.dumps(val) if val is not None else None)
        elif key == "tripwire_line":
            set_clauses.append("tripwire_line = ?")
            params.append(json.dumps(val) if val is not None else None)
        elif key == "metadata":
            set_clauses.append("metadata = ?")
            params.append(json.dumps(val or {}))
        elif key == "enabled":
            set_clauses.append("enabled = ?")
            params.append(1 if val else 0)
        elif key == "object_class" and val:
            set_clauses.append("object_class = ?")
            params.append(val.lower())
        else:
            set_clauses.append(f"{key} = ?")
            params.append(val)
            
    params.append(rule_id)
    query = f"UPDATE alert_rules SET {', '.join(set_clauses)} WHERE id = ?"
    
    with get_db_connection() as conn:
        conn.execute(query, tuple(params))
        conn.commit()
        
    get_alert_engine().reload_rules()
    updated_rows = execute_query("SELECT * FROM alert_rules WHERE id = ?", (rule_id,), fetch=True)
    return _format_rule_row(updated_rows[0])

@router.delete("/alerts/rules/{rule_id}")
def delete_rule(rule_id: str):
    rows = execute_query("SELECT * FROM alert_rules WHERE id = ?", (rule_id,), fetch=True)
    if not rows:
        raise HTTPException(status_code=404, detail=f"Rule '{rule_id}' not found.")
        
    with get_db_connection() as conn:
        conn.execute("DELETE FROM alert_rules WHERE id = ?", (rule_id,))
        conn.commit()
        
    get_alert_engine().reload_rules()
    logger.info(f"Deleted alert rule: {rule_id}")
    return {"id": rule_id, "message": "Rule deleted successfully"}

# --- Alert Retrieval & Resolution Endpoints ---

@router.get("/alerts")
def list_alerts(
    camera_id: Optional[str] = None,
    rule_id: Optional[str] = None,
    severity: Optional[str] = None,
    status: Optional[str] = None,
    start_time: Optional[float] = None,
    end_time: Optional[float] = None,
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0)
):
    query = "SELECT * FROM alerts WHERE 1=1"
    params = []
    
    if camera_id:
        query += " AND camera_id = ?"
        params.append(camera_id)
    if rule_id:
        query += " AND rule_id = ?"
        params.append(rule_id)
    if severity:
        query += " AND severity = ?"
        params.append(severity.lower())
    if status:
        query += " AND status = ?"
        params.append(status.lower())
    if start_time is not None:
        query += " AND trigger_timestamp >= ?"
        params.append(start_time)
    if end_time is not None:
        query += " AND trigger_timestamp <= ?"
        params.append(end_time)
        
    count_query = f"SELECT COUNT(*) as count FROM ({query})"
    count_res = execute_query(count_query, tuple(params), fetch=True)
    total_count = count_res[0]["count"] if count_res else 0
    
    query += " ORDER BY trigger_timestamp DESC LIMIT ? OFFSET ?"
    params.extend([limit, offset])
    
    rows = execute_query(query, tuple(params), fetch=True) or []
    return {
        "total_count": total_count,
        "limit": limit,
        "offset": offset,
        "alerts": [_format_alert_row(r) for r in rows]
    }

@router.get("/alerts/recent")
def get_recent_alerts(limit: int = Query(10, ge=1, le=50)):
    rows = execute_query(
        "SELECT * FROM alerts ORDER BY trigger_timestamp DESC LIMIT ?",
        (limit,),
        fetch=True
    ) or []
    return [_format_alert_row(r) for r in rows]

@router.get("/alerts/status")
def get_alerting_status():
    engine = get_alert_engine()
    return {
        "status": "online",
        "active_rules_count": engine.stats["active_rules_count"],
        "evaluated_frames": engine.stats["evaluated_frames"],
        "alerts_generated": engine.stats["alerts_generated"],
        "queue_overflows": engine.stats["queue_overflows"],
        "last_evaluation_time": engine.stats["last_evaluation_time"]
    }

@router.get("/alerts/{alert_id}")
def get_alert(alert_id: str):
    rows = execute_query("SELECT * FROM alerts WHERE id = ?", (alert_id,), fetch=True)
    if not rows:
        raise HTTPException(status_code=404, detail=f"Alert '{alert_id}' not found.")
    return _format_alert_row(rows[0])

@router.post("/alerts/{alert_id}/acknowledge")
def acknowledge_alert(alert_id: str):
    rows = execute_query("SELECT * FROM alerts WHERE id = ?", (alert_id,), fetch=True)
    if not rows:
        raise HTTPException(status_code=404, detail=f"Alert '{alert_id}' not found.")
    
    now = time.time()
    with get_db_connection() as conn:
        conn.execute("""
            UPDATE alerts SET status = 'acknowledged', acknowledged_at = ?
            WHERE id = ?
        """, (now, alert_id))
        conn.commit()
        
    return {"id": alert_id, "status": "acknowledged", "acknowledged_at": now}

@router.post("/alerts/{alert_id}/resolve")
def resolve_alert(alert_id: str):
    rows = execute_query("SELECT * FROM alerts WHERE id = ?", (alert_id,), fetch=True)
    if not rows:
        raise HTTPException(status_code=404, detail=f"Alert '{alert_id}' not found.")
    
    now = time.time()
    with get_db_connection() as conn:
        conn.execute("""
            UPDATE alerts SET status = 'resolved', resolved_at = ?
            WHERE id = ?
        """, (now, alert_id))
        conn.commit()
        
    return {"id": alert_id, "status": "resolved", "resolved_at": now}
