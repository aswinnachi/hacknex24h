import os
import time
import json
import uuid
import math
import threading
from datetime import datetime
from queue import Queue, Empty, Full
from typing import Dict, List, Optional, Tuple, Any

from backend.app.db.database import get_db_connection
from backend.app.core.logging import logger

def is_point_in_polygon(point: Tuple[float, float], polygon: List[Tuple[float, float]]) -> bool:
    """
    Ray-casting algorithm to test if point (x, y) is inside a polygon.
    Polygon is a list of (x, y) tuples in normalized coordinates [0.0, 1.0].
    """
    if len(polygon) < 3:
        return False
    x, y = point
    inside = False
    n = len(polygon)
    p1x, p1y = polygon[0]
    for i in range(1, n + 1):
        p2x, p2y = polygon[i % n]
        if y > min(p1y, p2y):
            if y <= max(p1y, p2y):
                if x <= max(p1x, p2x):
                    if p1y != p2y:
                        xinters = (y - p1y) * (p2x - p1x) / (p2y - p1y) + p1x
                    if p1x == p2x or x <= xinters:
                        inside = not inside
        p1x, p1y = p2x, p2y
    return inside

def ccw(A: Tuple[float, float], B: Tuple[float, float], C: Tuple[float, float]) -> bool:
    """Test whether three points are listed in counterclockwise order."""
    return (C[1] - A[1]) * (B[0] - A[0]) > (B[1] - A[1]) * (C[0] - A[0])

def line_segments_intersect(
    p1: Tuple[float, float], p2: Tuple[float, float],
    p3: Tuple[float, float], p4: Tuple[float, float]
) -> bool:
    """Return True if line segment p1-p2 intersects line segment p3-p4."""
    return (ccw(p1, p3, p4) != ccw(p2, p3, p4)) and (ccw(p1, p2, p3) != ccw(p1, p2, p4))

def cross_product_2d(origin: Tuple[float, float], A: Tuple[float, float], B: Tuple[float, float]) -> float:
    """Cross product of vectors (A - origin) and (B - origin)."""
    return (A[0] - origin[0]) * (B[1] - origin[1]) - (A[1] - origin[1]) * (B[0] - origin[0])

def determine_crossing_direction(
    prev_pt: Tuple[float, float], curr_pt: Tuple[float, float],
    line_start: Tuple[float, float], line_end: Tuple[float, float]
) -> Optional[str]:
    """
    Determine if track moving from prev_pt to curr_pt crossed line segment line_start->line_end.
    Returns:
      'ab': crossed from side A (left) to side B (right)
      'ba': crossed from side B (right) to side A (left)
      None: did not intersect or ambiguous
    """
    if not line_segments_intersect(prev_pt, curr_pt, line_start, line_end):
        return None
    
    cp_prev = cross_product_2d(line_start, line_end, prev_pt)
    cp_curr = cross_product_2d(line_start, line_end, curr_pt)
    
    # Require hysteresis: endpoints must be sufficiently distant from the line to avoid jitter
    min_dist = 0.005 # 0.5% normalized image distance hysteresis
    if abs(cp_prev) < min_dist and abs(cp_curr) < min_dist:
        return None
        
    if cp_prev > 0 and cp_curr <= 0:
        return 'ab'
    elif cp_prev < 0 and cp_curr >= 0:
        return 'ba'
    return None

def is_within_time_window(start_str: Optional[str], end_str: Optional[str], target_dt: Optional[datetime] = None) -> bool:
    """
    Check if target_dt (defaults to now) falls within [start_str, end_str] (HH:MM format).
    Supports overnight schedules where start_str > end_str (e.g., 22:00 to 06:00).
    If start_str or end_str is not provided, returns True (no time restriction).
    """
    if not start_str or not end_str:
        return True
    
    if target_dt is None:
        target_dt = datetime.now()
        
    try:
        sh, sm = map(int, start_str.strip().split(":"))
        eh, em = map(int, end_str.strip().split(":"))
        start_min = sh * 60 + sm
        end_min = eh * 60 + em
        curr_min = target_dt.hour * 60 + target_dt.minute
        
        if start_min <= end_min:
            # Daytime window (e.g. 09:00 to 17:00)
            return start_min <= curr_min <= end_min
        else:
            # Overnight window (e.g. 22:00 to 06:00)
            return curr_min >= start_min or curr_min <= end_min
    except Exception as e:
        logger.warning(f"Error parsing time window ({start_str}-{end_str}): {e}")
        return True

VEHICLE_CLASSES = {"car", "truck", "bus", "motorcycle", "bicycle", "vehicle"}

class AlertEngine:
    """
    Real-Time Alert Rule Evaluation Engine.
    - Evaluates detection and tracking output asynchronously and non-blockingly.
    - Manages persistent rules, geometric tracking states, cooldowns, and evidence generation.
    - Maintains a bounded async alert persistence queue.
    """
    def __init__(self, queue_maxsize: int = 1000):
        self._rules_lock = threading.RLock()
        self._rules: Dict[str, Dict[str, Any]] = {}
        
        # Track history per camera: camera_id -> track_id -> [(timestamp, (norm_x, norm_y))]
        self._track_history: Dict[str, Dict[str, List[Tuple[float, Tuple[float, float]]]]] = {}
        # Zone presence state per camera: camera_id -> rule_id -> track_id -> bool (is_inside)
        self._track_zone_state: Dict[str, Dict[str, Dict[str, bool]]] = {}
        
        # Cooldown map: (rule_id, camera_id, track_id_or_none) -> last_triggered_timestamp
        self._cooldowns: Dict[Tuple[str, str, Optional[str]], float] = {}
        self._cooldown_lock = threading.Lock()
        
        # Status / Telemetry
        self.stats = {
            "evaluated_frames": 0,
            "alerts_generated": 0,
            "active_rules_count": 0,
            "last_evaluation_time": 0.0,
            "queue_overflows": 0
        }
        
        # Async persistence queue
        self._alert_queue: Queue = Queue(maxsize=queue_maxsize)
        self._running = True
        self._worker_thread = threading.Thread(target=self._persistence_worker, daemon=True)
        self._worker_thread.start()
        
        # Load rules from DB
        self.reload_rules()

    def reload_rules(self):
        """Reload all active rules from database into in-memory cache."""
        try:
            with get_db_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT id, name, enabled, camera_id, rule_type, object_class,
                           min_confidence, time_start, time_end, zone_points,
                           tripwire_line, direction, cooldown_seconds, severity,
                           metadata, created_at, updated_at
                    FROM alert_rules
                """)
                rows = cursor.fetchall()
                
            new_rules = {}
            for r in rows:
                rule_id = r["id"]
                zone_pts = None
                if r["zone_points"]:
                    try:
                        zone_pts = json.loads(r["zone_points"])
                    except Exception:
                        zone_pts = None
                
                trip_line = None
                if r["tripwire_line"]:
                    try:
                        trip_line = json.loads(r["tripwire_line"])
                    except Exception:
                        trip_line = None
                        
                meta = {}
                if r["metadata"]:
                    try:
                        meta = json.loads(r["metadata"])
                    except Exception:
                        meta = {}

                new_rules[rule_id] = {
                    "id": rule_id,
                    "name": r["name"],
                    "enabled": bool(r["enabled"]),
                    "camera_id": r["camera_id"],
                    "rule_type": r["rule_type"],
                    "object_class": (r["object_class"].lower() if r["object_class"] else None),
                    "min_confidence": float(r["min_confidence"] if r["min_confidence"] is not None else 0.3),
                    "time_start": r["time_start"],
                    "time_end": r["time_end"],
                    "zone_points": zone_pts,
                    "tripwire_line": trip_line,
                    "direction": r["direction"] or "any",
                    "cooldown_seconds": float(r["cooldown_seconds"] if r["cooldown_seconds"] is not None else 30.0),
                    "severity": r["severity"] or "warning",
                    "metadata": meta,
                    "created_at": r["created_at"],
                    "updated_at": r["updated_at"]
                }
                
            with self._rules_lock:
                self._rules = new_rules
                self.stats["active_rules_count"] = sum(1 for rule in self._rules.values() if rule["enabled"])
            logger.info(f"AlertEngine: Loaded {len(new_rules)} rules ({self.stats['active_rules_count']} enabled).")
        except Exception as e:
            logger.error(f"AlertEngine: Error loading rules: {e}")

    def evaluate_frame_detections(
        self,
        camera_id: str,
        camera_name: str,
        frame_number: int,
        timestamp: float,
        detections: List[Dict[str, Any]],
        frame_width: int,
        frame_height: int,
        evidence_frame_path: Optional[str] = None
    ):
        """
        Evaluate frame detections against all applicable enabled rules.
        Called directly from CameraStream._analysis_worker() synchronously or via bounded execution.
        Must execute rapidly without blocking stream capture.
        """
        self.stats["evaluated_frames"] += 1
        self.stats["last_evaluation_time"] = timestamp
        
        with self._rules_lock:
            # Filter active rules applicable to this camera
            applicable_rules = [
                r for r in self._rules.values()
                if r["enabled"] and (not r["camera_id"] or r["camera_id"] == camera_id)
            ]
            
        if not applicable_rules:
            # Clean up old track histories periodically
            self._update_tracks_cleanup(camera_id, timestamp)
            return

        curr_dt = datetime.fromtimestamp(timestamp)

        # Update track positions
        tracks_present = set()
        for det in detections:
            track_id = det.get("track_id")
            bbox = det.get("bbox") # [x1, y1, x2, y2]
            if not bbox or len(bbox) != 4:
                continue
            
            # Bottom center point in normalized coords [0.0, 1.0]
            bw = max(1, frame_width)
            bh = max(1, frame_height)
            bottom_center = (
                float((bbox[0] + bbox[2]) / 2.0) / bw,
                float(bbox[3]) / bh
            )
            # Clamp to [0, 1]
            bottom_center = (
                max(0.0, min(1.0, bottom_center[0])),
                max(0.0, min(1.0, bottom_center[1]))
            )
            det["norm_bottom_center"] = bottom_center

            if track_id is not None:
                tid_str = str(track_id)
                tracks_present.add(tid_str)
                if camera_id not in self._track_history:
                    self._track_history[camera_id] = {}
                if tid_str not in self._track_history[camera_id]:
                    self._track_history[camera_id][tid_str] = []
                hist = self._track_history[camera_id][tid_str]
                hist.append((timestamp, bottom_center))
                # Keep last 10 points
                if len(hist) > 10:
                    hist.pop(0)

        # Evaluate rules
        for rule in applicable_rules:
            self._evaluate_rule(
                rule=rule,
                camera_id=camera_id,
                camera_name=camera_name,
                timestamp=timestamp,
                curr_dt=curr_dt,
                detections=detections,
                evidence_frame_path=evidence_frame_path
            )

        # Cleanup disappeared tracks
        self._update_tracks_cleanup(camera_id, timestamp, tracks_present)

    def _evaluate_rule(
        self,
        rule: Dict[str, Any],
        camera_id: str,
        camera_name: str,
        timestamp: float,
        curr_dt: datetime,
        detections: List[Dict[str, Any]],
        evidence_frame_path: Optional[str]
    ):
        rule_type = rule["rule_type"]
        min_conf = rule["min_confidence"]
        target_cls = rule["object_class"]
        
        # 1. Check time window
        if not is_within_time_window(rule["time_start"], rule["time_end"], curr_dt):
            return

        for det in detections:
            det_cls = (det.get("class") or det.get("label") or "").lower()
            conf = float(det.get("confidence") or 0.0)
            track_id = str(det.get("track_id")) if det.get("track_id") is not None else None
            norm_pt = det.get("norm_bottom_center")
            
            if conf < min_conf:
                continue

            # Class check
            if target_cls and target_cls != "any":
                if rule_type == "vehicle_in_region":
                    # Matches target class or general vehicle classes
                    if det_cls != target_cls and det_cls not in VEHICLE_CLASSES:
                        continue
                elif det_cls != target_cls:
                    continue

            # Rule specific checks
            trigger_reason = None

            if rule_type == "object_detected":
                trigger_reason = f"Detected {det_cls} with confidence {conf:.2f}"

            elif rule_type == "restricted_hours":
                if det_cls == "person" or (target_cls and det_cls == target_cls) or not target_cls:
                    trigger_reason = (
                        f"Detected {det_cls} during restricted hours "
                        f"({rule['time_start']} - {rule['time_end']})"
                    )

            elif rule_type in ("zone_entry", "vehicle_in_region"):
                if not rule.get("zone_points") or not norm_pt:
                    continue
                zone_pts = [(p[0], p[1]) for p in rule["zone_points"]]
                is_inside = is_point_in_polygon(norm_pt, zone_pts)
                
                # Check transition state
                rule_id = rule["id"]
                if camera_id not in self._track_zone_state:
                    self._track_zone_state[camera_id] = {}
                if rule_id not in self._track_zone_state[camera_id]:
                    self._track_zone_state[camera_id][rule_id] = {}
                
                state_map = self._track_zone_state[camera_id][rule_id]
                prev_inside = state_map.get(track_id, False) if track_id else False
                
                if track_id:
                    state_map[track_id] = is_inside

                # Alert only on transition from False -> True (Entry)
                if is_inside and not prev_inside:
                    trigger_reason = f"Object {det_cls} entered defined zone '{rule['name']}'"

            elif rule_type == "tripwire_crossing":
                if not rule.get("tripwire_line") or len(rule["tripwire_line"]) != 2 or not track_id:
                    continue
                
                line = rule["tripwire_line"]
                p_start = (float(line[0][0]), float(line[0][1]))
                p_end = (float(line[1][0]), float(line[1][1]))
                
                # Need at least two points in track history
                hist = self._track_history.get(camera_id, {}).get(track_id, [])
                if len(hist) < 2:
                    continue
                
                prev_pt = hist[-2][1]
                curr_pt = hist[-1][1]
                
                crossing_dir = determine_crossing_direction(prev_pt, curr_pt, p_start, p_end)
                if crossing_dir:
                    configured_dir = rule.get("direction", "any").lower()
                    if configured_dir == "any" or configured_dir == crossing_dir:
                        trigger_reason = (
                            f"Tracked {det_cls} (ID {track_id}) crossed virtual tripwire "
                            f"in direction '{crossing_dir}'"
                        )

            if trigger_reason:
                self._dispatch_alert_if_not_suppressed(
                    rule=rule,
                    camera_id=camera_id,
                    camera_name=camera_name,
                    timestamp=timestamp,
                    det=det,
                    track_id=track_id,
                    trigger_reason=trigger_reason,
                    evidence_frame_path=evidence_frame_path
                )

    def _dispatch_alert_if_not_suppressed(
        self,
        rule: Dict[str, Any],
        camera_id: str,
        camera_name: str,
        timestamp: float,
        det: Dict[str, Any],
        track_id: Optional[str],
        trigger_reason: str,
        evidence_frame_path: Optional[str]
    ):
        rule_id = rule["id"]
        cooldown = rule["cooldown_seconds"]
        
        # Check cooldown
        # Per track if available, else per rule+camera
        cooldown_key = (rule_id, camera_id, track_id)
        with self._cooldown_lock:
            last_time = self._cooldowns.get(cooldown_key, 0.0)
            if timestamp - last_time < cooldown:
                # Suppressed by cooldown
                return
            self._cooldowns[cooldown_key] = timestamp

        alert_id = f"alt_{uuid.uuid4().hex[:12]}"
        alert_payload = {
            "id": alert_id,
            "rule_id": rule_id,
            "rule_name": rule["name"],
            "camera_id": camera_id,
            "camera_name": camera_name,
            "trigger_timestamp": timestamp,
            "object_class": det.get("class") or det.get("label"),
            "track_id": track_id,
            "confidence": float(det.get("confidence") or 0.0),
            "attributes": json.dumps(det.get("attributes") or {}),
            "trigger_reason": trigger_reason,
            "evidence_frame": evidence_frame_path or det.get("crop_path"),
            "event_id": det.get("event_id"),
            "severity": rule["severity"],
            "status": "active",
            "acknowledged_at": None,
            "resolved_at": None,
            "created_at": time.time()
        }

        # Enqueue for asynchronous DB persistence
        try:
            self._alert_queue.put_nowait(alert_payload)
            self.stats["alerts_generated"] += 1
            logger.info(
                f"Alert Triggered [{rule['severity'].upper()}]: '{rule['name']}' on {camera_name} - {trigger_reason}"
            )
        except Full:
            self.stats["queue_overflows"] += 1
            logger.warning(f"AlertEngine: Alert queue full ({self._alert_queue.maxsize}), dropping alert {alert_id}")

    def _update_tracks_cleanup(self, camera_id: str, current_time: float, active_tracks: Optional[set] = None):
        """Clean up stale track histories and zone states."""
        if camera_id in self._track_history:
            stale_tids = []
            for tid, hist in self._track_history[camera_id].items():
                if hist and (current_time - hist[-1][0] > 10.0): # 10s inactivity
                    stale_tids.append(tid)
            for tid in stale_tids:
                del self._track_history[camera_id][tid]
                
        if camera_id in self._track_zone_state and active_tracks is not None:
            for rule_id, state_map in self._track_zone_state[camera_id].items():
                disappeared = [tid for tid in state_map if tid not in active_tracks]
                for tid in disappeared:
                    # Reset state to outside so re-entry fires if track returns
                    del state_map[tid]

    def _persistence_worker(self):
        """Background worker consuming alert payloads from queue and saving to DB."""
        while self._running:
            try:
                alert = self._alert_queue.get(timeout=0.5)
            except Empty:
                continue

            try:
                with get_db_connection() as conn:
                    conn.execute("""
                        INSERT INTO alerts (
                            id, rule_id, rule_name, camera_id, camera_name,
                            trigger_timestamp, object_class, track_id, confidence,
                            attributes, trigger_reason, evidence_frame, event_id,
                            severity, status, acknowledged_at, resolved_at, created_at
                        ) VALUES (
                            ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                        )
                    """, (
                        alert["id"],
                        alert["rule_id"],
                        alert["rule_name"],
                        alert["camera_id"],
                        alert["camera_name"],
                        alert["trigger_timestamp"],
                        alert["object_class"],
                        alert["track_id"],
                        alert["confidence"],
                        alert["attributes"],
                        alert["trigger_reason"],
                        alert["evidence_frame"],
                        alert["event_id"],
                        alert["severity"],
                        alert["status"],
                        alert["acknowledged_at"],
                        alert["resolved_at"],
                        alert["created_at"]
                    ))
                    conn.commit()
            except Exception as e:
                logger.error(f"AlertEngine: Failed to persist alert {alert.get('id')}: {e}")
            finally:
                self._alert_queue.task_done()

    def shutdown(self):
        """Clean shutdown of background persistence worker."""
        self._running = False
        if self._worker_thread.is_alive():
            self._worker_thread.join(timeout=2.0)
        logger.info("AlertEngine: Shutdown complete.")

# Global singleton
_alert_engine_instance: Optional[AlertEngine] = None
_instance_lock = threading.Lock()

def get_alert_engine() -> AlertEngine:
    global _alert_engine_instance
    if _alert_engine_instance is None:
        with _instance_lock:
            if _alert_engine_instance is None:
                _alert_engine_instance = AlertEngine()
    return _alert_engine_instance
