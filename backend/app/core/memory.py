import json
import time
import uuid
from typing import Optional, List, Dict, Any, Tuple
from backend.app.core.logging import logger
from backend.app.db.database import execute_query

def normalize_key(name: str) -> str:
    return name.strip().lower()

class MemoryManager:
    def __init__(self):
        pass

    def add_memory(
        self,
        name: str,
        camera_id: str,
        region: Optional[List[float]] = None,
        metadata: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Permanently stores a user-defined semantic location / reference in SQLite.
        """
        key_name = normalize_key(name)
        mem_id = str(uuid.uuid4())
        now = time.time()
        
        region_str = json.dumps(region) if region is not None else None
        meta_str = json.dumps(metadata) if metadata is not None else None
        
        # Check if already exists; if so, update
        existing = execute_query("SELECT id FROM memory WHERE key_name = ?", (key_name,), fetch=True, fetch_one=True)
        if existing:
            mem_id = existing["id"]
            execute_query(
                "UPDATE memory SET camera_id = ?, region = ?, metadata = ?, updated_at = ? WHERE id = ?",
                (camera_id, region_str, meta_str, now, mem_id)
            )
            logger.info(f"Updated memory mapping: '{key_name}' -> camera '{camera_id}'")
        else:
            execute_query(
                "INSERT INTO memory (id, key_name, camera_id, region, metadata, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (mem_id, key_name, camera_id, region_str, meta_str, now, now)
            )
            logger.info(f"Created persistent memory mapping: '{key_name}' -> camera '{camera_id}'")

        return self.get_memory_by_id(mem_id)

    def get_all_memories(self) -> List[Dict[str, Any]]:
        rows = execute_query("SELECT * FROM memory ORDER BY created_at DESC", fetch=True)
        results = []
        for r in rows:
            item = dict(r)
            if item.get("region"):
                try: item["region"] = json.loads(item["region"])
                except: pass
            if item.get("metadata"):
                try: item["metadata"] = json.loads(item["metadata"])
                except: pass
            results.append(item)
        return results

    def get_memory_by_id(self, mem_id: str) -> Optional[Dict[str, Any]]:
        row = execute_query("SELECT * FROM memory WHERE id = ?", (mem_id,), fetch=True, fetch_one=True)
        if not row: return None
        item = dict(row)
        if item.get("region"):
            try: item["region"] = json.loads(item["region"])
            except: pass
        if item.get("metadata"):
            try: item["metadata"] = json.loads(item["metadata"])
            except: pass
        return item

    def get_memory_by_name(self, name: str) -> Optional[Dict[str, Any]]:
        key_name = normalize_key(name)
        row = execute_query("SELECT * FROM memory WHERE key_name = ?", (key_name,), fetch=True, fetch_one=True)
        if not row: return None
        item = dict(row)
        if item.get("region"):
            try: item["region"] = json.loads(item["region"])
            except: pass
        if item.get("metadata"):
            try: item["metadata"] = json.loads(item["metadata"])
            except: pass
        return item

    def delete_memory(self, mem_id: str) -> bool:
        execute_query("DELETE FROM memory WHERE id = ?", (mem_id,))
        return True

    def resolve_location(self, text: str) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
        """
        Checks if text contains a known memory location or an unknown location phrase.
        Returns: (matched_memory_dict, unknown_location_phrase)
        """
        normalized_text = text.lower()
        memories = self.get_all_memories()
        
        # 1. Check known memories first
        for mem in memories:
            key = mem["key_name"]
            if key in normalized_text:
                return mem, None
                
        # 2. Check for unknown location phrases (e.g., 'main gate', 'back entrance', 'parking lot', 'front gate', etc.)
        import re
        location_patterns = [
            r"\b(?:at|near|by|through|in|around)\s+(?:the\s+)?([a-zA-Z0-9\s-]+?)(?:\s+in\s+the|\s+today|\s+yesterday|\s*\?|\s*$)",
            r"\b(?:the\s+)([a-zA-Z0-9\s-]+?(?:gate|entrance|exit|door|parking|lot|lobby|deck|hallway|porch|courtyard|perimeter|yard))\b"
        ]
        
        # Known non-location phrases to ignore
        ignore_phrases = ["last hour", "last minute", "first 10 seconds", "last 5 minutes", "today", "yesterday", "video", "camera", "image", "car", "person", "vehicle"]
        
        for pat in location_patterns:
            matches = re.finditer(pat, normalized_text)
            for m in matches:
                phrase = m.group(1).strip() if m.groups() else m.group(0).strip()
                phrase = phrase.replace("the ", "").strip()
                if phrase and len(phrase) > 2 and phrase not in ignore_phrases:
                    # Check if phrase is just an object or camera
                    if not phrase.startswith("cam") and not phrase.startswith("camera"):
                        return None, phrase

        return None, None

def check_bbox_region_overlap(bboxes: List[List[float]], region: List[float]) -> bool:
    """
    Checks if any bounding box [bx1, by1, bx2, by2] intersects with region [rx1, ry1, rx2, ry2].
    """
    if not region or len(region) < 4:
        return True
    rx1, ry1, rx2, ry2 = region[:4]
    
    for bbox in bboxes:
        if len(bbox) < 4: continue
        bx1, by1, bx2, by2 = bbox[:4]
        # Overlap condition
        if max(rx1, bx1) < min(rx2, bx2) and max(ry1, by1) < min(ry2, by2):
            return True
    return False

_memory_manager = None
def get_memory_manager() -> MemoryManager:
    global _memory_manager
    if _memory_manager is None:
        _memory_manager = MemoryManager()
    return _memory_manager
