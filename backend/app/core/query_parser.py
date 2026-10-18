import re
import time
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, List, Tuple
from backend.app.core.logging import logger
from backend.app.db.database import execute_query

COLORS = [
    "red", "blue", "green", "white", "black", "silver", "gray", "grey",
    "yellow", "orange", "brown", "purple", "pink", "dark", "light"
]

OBJECT_ALIASES = {
    "person": ["person", "people", "man", "woman", "human", "humans", "pedestrian", "someone", "individual", "guy", "lady"],
    "car": ["car", "cars", "automobile", "automobiles", "sedan", "suv"],
    "auto": ["auto", "autos", "auto rickshaw", "auto-rickshaw", "autorickshaw", "rickshaw", "rickshaws", "tuk-tuk", "tuktuk", "three wheeler", "three-wheeler", "3 wheeler"],
    "bus": ["bus", "buses"],
    "truck": ["truck", "trucks", "pickup", "lorry"],
    "motorcycle": ["motorcycle", "motorcycles", "motorbike", "motorbikes", "moped", "scooter", "scooters", "scotter", "scooty", "twowheeler", "two wheeler", "two-wheeler"],
    "bicycle": ["bicycle", "bicycles", "bike", "bikes", "cycle", "cycles"],
    "vehicle": ["vehicle", "vehicles", "transport"],
    "backpack": ["backpack", "backpacks", "pack"],
    "handbag": ["handbag", "handbags", "purse", "clutch"],
    "suitcase": ["suitcase", "suitcases", "luggage"],
    "bag": ["bag", "bags", "sack"],
    "umbrella": ["umbrella", "umbrellas", "parasol"],
    "phone": ["phone", "phones", "cellphone", "mobile", "smartphone"],
    "bottle": ["bottle", "bottles", "cup", "tumbler"],
    "knife": ["knife", "knives", "blade", "dagger", "machete", "weapon", "weapons"],
    "helmet": ["helmet", "helmets"],
    "hat": ["hat", "hats", "cap"],
    "glasses": ["glasses", "sunglasses", "spectacles"]
}

STOP_WORDS = [
    "find", "show", "me", "search", "for", "look", "can", "you", "did", "a", "an", "the",
    "any", "all", "is", "there", "are", "see", "pass", "through", "passing", "moving",
    "detected", "appear", "appeared", "in", "at", "on", "from", "to", "please", "tell",
    "what", "where", "which", "how", "many", "count", "were", "happened", "occurred",
    "was", "same", "between", "both", "across"
]

class QueryParser:
    def __init__(self):
        pass

    def parse(self, query: str) -> Dict[str, Any]:
        from backend.app.core.memory import get_memory_manager
        
        original_query = query.strip()
        cleaned_query = original_query.lower()

        # 1. Classify Intent
        intent = self._classify_intent(cleaned_query)

        # 2. Extract Time Filter FIRST
        time_info, cleaned_query = self._extract_time(cleaned_query)

        # 3. Extract Action / Movement
        action = self._extract_action(cleaned_query)

        # 4. Extract Clothing Attributes (Upper and Lower)
        upper_color, upper_type, lower_color, lower_type = self._extract_clothing_attributes(cleaned_query)

        # 5. Extract Carried Objects, Weapons & Negative Attributes
        sec_obj, relation, requires_bag, forbid_bag, req_accessories, forbid_accessories, requires_holding = self._extract_accessories_and_relations(cleaned_query)

        # 6. Extract Cameras (Single & Multi-Camera / Cross-Camera)
        camera_id, camera_label, detected_cameras, cleaned_query = self._extract_cameras(cleaned_query)

        # If multiple cameras detected or cross-camera keywords, set CROSS_CAMERA intent
        if len(detected_cameras) >= 2 or (len(detected_cameras) == 1 and intent == "CROSS_CAMERA"):
            intent = "CROSS_CAMERA"

        # 7. Resolve Memory References / Clarify-Once
        mem_mgr = get_memory_manager()
        matched_mem, unknown_location = mem_mgr.resolve_location(cleaned_query)
        resolved_memory = None
        needs_clarification = False
        clarification_prompt = None
        missing_reference = None

        if matched_mem:
            resolved_memory = {
                "id": matched_mem["id"],
                "name": matched_mem["key_name"],
                "camera_id": matched_mem["camera_id"],
                "region": matched_mem.get("region")
            }
            if not camera_id:
                camera_id = matched_mem["camera_id"]
                camera_label = f"Memory: {matched_mem['key_name']} ({matched_mem['camera_id']})"
            cleaned_query = cleaned_query.replace(matched_mem["key_name"], " ")
        else:
            if not camera_id and unknown_location:
                if intent not in ("TEMPORAL_SEARCH", "INVENTORY"):
                    needs_clarification = True
                    missing_reference = unknown_location
                    clarification_prompt = f"I don't know which camera/region represents '{unknown_location}'. Please select it."

        # 8. Extract Dominant Color if not already extracted in clothing
        color = upper_color or self._extract_color(cleaned_query)

        # 9. Extract Canonical Object Class
        obj = self._extract_object(cleaned_query)

        # If knife was requested as accessory and no main obj, set knife as object or person holding knife
        if "knife" in req_accessories and not obj:
            obj = "person"

        # 10. Structured Constraints (Positive & Negative)
        required_objects = [obj] if obj else []
        forbidden_objects = []
        if forbid_bag:
            forbidden_objects.extend(["bag", "backpack", "suitcase", "luggage", "handbag"])
        if re.search(r"\b(?:no car|without car|no vehicle|without vehicle)\b", cleaned_query):
            forbidden_objects.extend(["car", "vehicle"])
        if re.search(r"\b(?:no person|without person|no people)\b", cleaned_query):
            forbidden_objects.extend(["person"])

        required_attributes = []
        forbidden_attributes = []
        if color:
            required_attributes.append(color)
        if upper_color:
            required_attributes.append(f"upper:{upper_color}")
        if lower_color:
            required_attributes.append(f"lower:{lower_color}")
        if action:
            required_attributes.append(action)
        if req_accessories:
            required_attributes.extend(req_accessories)
        elif requires_bag:
            required_attributes.append("bag")
        if forbid_bag:
            forbidden_attributes.append("bag")
        if forbid_accessories:
            forbidden_attributes.extend(forbid_accessories)

        # 11. Extract Visual Concept for Embedding
        visual_concept = self._extract_visual_concept(cleaned_query, color, obj, sec_obj if not forbid_bag else None, original_query)

        return {
            "original_query": original_query,
            "intent": intent,
            "camera": camera_id,
            "camera_label": camera_label,
            "cross_camera_list": detected_cameras if len(detected_cameras) >= 2 else None,
            "resolved_memory": resolved_memory,
            "needs_clarification": needs_clarification,
            "clarification_prompt": clarification_prompt,
            "missing_reference": missing_reference,
            "time_filter": time_info,
            "color": color,
            "upper_color": upper_color,
            "upper_type": upper_type,
            "lower_color": lower_color,
            "lower_type": lower_type,
            "object": obj,
            "action": action,
            "secondary_object": sec_obj,
            "relation": relation,
            "requires_bag": requires_bag,
            "forbid_bag": forbid_bag,
            "requires_holding_object": requires_holding,
            "required_accessories": req_accessories,
            "forbidden_accessories": forbid_accessories,
            "required_objects": required_objects,
            "forbidden_objects": forbidden_objects,
            "required_attributes": required_attributes,
            "forbidden_attributes": forbidden_attributes,
            "visual_concept": visual_concept
        }

    def _classify_intent(self, text: str) -> str:
        # Cross-camera intent pattern
        if re.search(r"\b(?:same\s+(?:\w+\s+)?(?:seen|appear|detected)\s+(?:on|in|across)|between\s+camera|on\s+camera\s*\d+\s+and\s+camera\s*\d+|across\s+(?:both|different|multiple)?\s*cameras?)\b", text):
            return "CROSS_CAMERA"
        if re.search(r"\b(?:how many|count\b|number of|total number|total count|total)\b", text):
            return "COUNT"
        if re.search(r"\b(?:what objects|what was detected|list objects|inventory|what is in the video)\b", text):
            return "INVENTORY"
        if re.search(r"\b(?:what happened|what occurred|show what happened)\b", text):
            return "TEMPORAL_SEARCH"
        if re.search(r"\b(?:did a|did the|is there a|was there a|can you see a|has a|any|was anyone)\b", text) and text.endswith("?"):
            return "YES_NO"
        if re.search(r"^(?:did\b|is\b|was\b|has\b|are\b|can\b)", text):
            return "YES_NO"
        return "SEARCH"

    def _extract_action(self, text: str) -> Optional[str]:
        if re.search(r"\b(?:walking|walks|walked)\b", text):
            return "walking"
        if re.search(r"\b(?:running|runs|ran|jogging)\b", text):
            return "running"
        if re.search(r"\b(?:riding|rides|cycling)\b", text):
            return "riding"
        if re.search(r"\b(?:sitting|sits|seated)\b", text):
            return "sitting"
        if re.search(r"\b(?:stopped|parked|stationary|standing|stands)\b", text):
            return "stationary"
        if re.search(r"\b(?:moving|in motion|driving|entering|entered|exiting|exited)\b", text):
            return "moving"
        return None

    def _extract_clothing_attributes(self, text: str) -> Tuple[Optional[str], Optional[str], Optional[str], Optional[str]]:
        upper_color = None
        upper_type = None
        lower_color = None
        lower_type = None

        # Upper clothing patterns
        for col in COLORS:
            if re.search(rf"\b(?:wearing\s+(?:a\s+)?)?{col}\s+(?:shirt|t-shirt|jacket|hoodie|coat|top|tshirt|sweater)\b", text):
                upper_color = col
                upper_type = "shirt"
                break
            elif re.search(rf"\b(?:wearing|in)\s+{col}\b", text):
                upper_color = col
                upper_type = "shirt"
                break

        # Lower clothing patterns
        for col in COLORS:
            if re.search(rf"\b{col}\s+(?:pants|jeans|trousers|shorts|skirt)\b", text):
                lower_color = col
                lower_type = "pants"
                break

        return upper_color, upper_type, lower_color, lower_type

    def _extract_accessories_and_relations(self, text: str) -> Tuple[Optional[str], Optional[str], bool, bool, List[str], List[str], bool]:
        sec_obj = None
        relation = None
        requires_bag = False
        forbid_bag = False
        requires_holding = False
        req_accessories = []
        forbid_accessories = []

        # Check holding an object in hand
        if re.search(r"\b(?:holding (?:an? )?(?:object|item|something)|carrying (?:an? )?(?:object|item|something)|holding (?:anything|something) in (?:their|his|her)?\s*hand)\b", text):
            requires_holding = True

        # Negative bag relations
        if re.search(r"\b(?:without a bag|without bag|without any bag|without backpack|without a backpack|without backpacks|no bag|no backpack|not carrying a bag|not carrying bag|not carrying any bag|not holding a bag|without luggage|without suitcase)\b", text):
            return "bag", "without", False, True, [], ["bag", "backpack", "suitcase", "handbag"], requires_holding

        # Weapons / Knife
        if re.search(r"\b(?:knife|knives|blade|dagger|machete|weapon|weapons)\b", text):
            sec_obj = "knife"
            relation = "carrying"
            req_accessories.append("knife")

        # Positive specific accessories
        if re.search(r"\b(?:backpack|backpacks|pack)\b", text):
            sec_obj = "backpack"
            relation = "carrying"
            requires_bag = True
            req_accessories.append("backpack")
        if re.search(r"\b(?:handbag|handbags|purse|clutch)\b", text):
            sec_obj = "handbag"
            relation = "carrying"
            requires_bag = True
            req_accessories.append("handbag")
        if re.search(r"\b(?:suitcase|suitcases|luggage)\b", text):
            sec_obj = "suitcase"
            relation = "carrying"
            requires_bag = True
            req_accessories.append("suitcase")
        if re.search(r"\b(?:umbrella|umbrellas)\b", text):
            sec_obj = "umbrella"
            relation = "with"
            req_accessories.append("umbrella")
        if re.search(r"\b(?:phone|phones|cellphone|mobile)\b", text):
            sec_obj = "phone"
            relation = "holding"
            req_accessories.append("phone")
        if re.search(r"\b(?:bottle|bottles|cup)\b", text):
            sec_obj = "bottle"
            relation = "holding"
            req_accessories.append("bottle")
        if re.search(r"\b(?:with a bag|with bag|with any bag|carrying a bag|carrying bag|carrying any bag|carrying bags|holding a bag|has a bag)\b", text):
            sec_obj = "bag"
            relation = "carrying"
            requires_bag = True
            if not req_accessories:
                req_accessories.append("bag")

        return sec_obj, relation, requires_bag, forbid_bag, req_accessories, forbid_accessories, requires_holding

    def _extract_cameras(self, text: str) -> Tuple[Optional[str], Optional[str], List[str], str]:
        detected_cams = []
        remaining = text

        # Check for explicit 'all cameras' or 'across all cameras'
        all_cam_match = re.search(r"\b(?:across\s+all\s+cameras|in\s+all\s+cameras|all\s+cameras|every\s+camera)\b", text)
        if all_cam_match:
            rem = text[:all_cam_match.start()] + " " + text[all_cam_match.end():]
            return "all", "All Cameras", ["all"], " ".join(rem.split())

        # Patterns for camera IDs or numbers: e.g. "camera 1", "camera 3", "camera_01", "cam 2"
        cam_patterns = [
            r"\b(?:camera|cam)[_\s]*0*([0-9]+)\b"
        ]

        found_matches = []
        for pat in cam_patterns:
            for m in re.finditer(pat, remaining):
                cam_num = int(m.group(1))
                cam_id = f"camera_{cam_num:02d}"
                found_matches.append((m.start(), m.end(), cam_id, f"Camera {cam_num}"))

        # Check existing cameras in DB to resolve exact IDs or friendly names
        try:
            db_cams = execute_query("SELECT id, name FROM cameras", fetch=True) or []
            for cam in db_cams:
                c_id = cam["id"].lower()
                c_name = cam["name"].lower()
                # If specific ID appears in text
                if c_id in remaining and not any(c_id == fm[2] for fm in found_matches):
                    idx = remaining.find(c_id)
                    found_matches.append((idx, idx + len(c_id), cam["id"], cam["name"]))
                # If camera friendly name appears
                elif c_name in remaining and not any(c_name in fm[3].lower() for fm in found_matches):
                    idx = remaining.find(c_name)
                    found_matches.append((idx, idx + len(c_name), cam["id"], cam["name"]))
        except Exception as e:
            logger.warning(f"Error checking camera names in query_parser: {e}")

        # Sort matches by start index
        found_matches.sort(key=lambda x: x[0])
        
        # Deduplicate cameras
        seen_ids = set()
        unique_matches = []
        for m in found_matches:
            if m[2] not in seen_ids:
                seen_ids.add(m[2])
                unique_matches.append(m)

        if unique_matches:
            # Mask out found camera tokens from remaining text
            # Replace from right to left to preserve offsets
            for start, end, _, _ in reversed(unique_matches):
                remaining = remaining[:start] + " " + remaining[end:]
            
            detected_cams = [m[2] for m in unique_matches]
            if len(unique_matches) == 1:
                return unique_matches[0][2], unique_matches[0][3], detected_cams, " ".join(remaining.split())
            else:
                combined_label = " and ".join(m[3] for m in unique_matches)
                return None, combined_label, detected_cams, " ".join(remaining.split())

        return None, None, [], text

    def _extract_time(self, text: str) -> Tuple[Optional[Dict[str, Any]], str]:
        ref_time = self._get_reference_time()
        is_epoch = (ref_time > 1000000.0)
        now_dt = datetime.fromtimestamp(ref_time) if is_epoch else datetime.now()
        start_of_today = datetime(now_dt.year, now_dt.month, now_dt.day, 0, 0, 0).timestamp() if is_epoch else 0.0

        # 1. Clock time patterns: "after 5 PM", "after 5pm", "after 17:00", "before 3 PM", "between 2 PM and 5 PM"
        after_match = re.search(r"\b(?:after|past|from)\s+([0-9]{1,2})(?::([0-9]{2}))?\s*(am|pm)?\b", text)
        if after_match:
            hr = int(after_match.group(1))
            mn = int(after_match.group(2) or 0)
            mer = (after_match.group(3) or "").lower()
            if mer == "pm" and hr < 12: hr += 12
            elif mer == "am" and hr == 12: hr = 0

            if is_epoch:
                min_t = start_of_today + hr * 3600 + mn * 60
                max_t = start_of_today + 86400
            else:
                min_t = hr * 3600 + mn * 60
                max_t = ref_time

            remaining = text[:after_match.start()] + " " + text[after_match.end():]
            label = f"after {hr:02d}:{mn:02d}"
            return {
                "type": "explicit",
                "label": label,
                "min_timestamp": min_t,
                "max_timestamp": max_t
            }, " ".join(remaining.split())

        before_match = re.search(r"\b(?:before|prior\s+to|until)\s+([0-9]{1,2})(?::([0-9]{2}))?\s*(am|pm)?\b", text)
        if before_match:
            hr = int(before_match.group(1))
            mn = int(before_match.group(2) or 0)
            mer = (before_match.group(3) or "").lower()
            if mer == "pm" and hr < 12: hr += 12
            elif mer == "am" and hr == 12: hr = 0

            if is_epoch:
                min_t = start_of_today
                max_t = start_of_today + hr * 3600 + mn * 60
            else:
                min_t = 0.0
                max_t = hr * 3600 + mn * 60

            remaining = text[:before_match.start()] + " " + text[before_match.end():]
            label = f"before {hr:02d}:{mn:02d}"
            return {
                "type": "explicit",
                "label": label,
                "min_timestamp": min_t,
                "max_timestamp": max_t
            }, " ".join(remaining.split())

        # 2. Relative time patterns
        relative_patterns = [
            (r"\b(?:in|during|over|for)?\s*(?:the\s*)?(?:last|past)\s*minute\b", 60, "last minute"),
            (r"\b(?:in|during|over|for)?\s*(?:the\s*)?(?:last|past)\s*1\s*minute\b", 60, "last minute"),
            (r"\b(?:in|during|over|for)?\s*(?:the\s*)?(?:last|past)\s*5\s*minutes\b", 300, "last 5 minutes"),
            (r"\b(?:in|during|over|for)?\s*(?:the\s*)?(?:last|past)\s*10\s*minutes\b", 600, "last 10 minutes"),
            (r"\b(?:in|during|over|for)?\s*(?:the\s*)?(?:last|past)\s*30\s*minutes\b", 1800, "last 30 minutes"),
            (r"\b(?:in|during|over|for)?\s*(?:the\s*)?(?:last|past)\s*(?:an|1)?\s*hour\b", 3600, "last hour"),
            (r"\b(?:in|during|over|for)?\s*(?:the\s*)?(?:last|past)\s*([0-9]+)\s*seconds\b", None, "seconds_var"),
            (r"\b(?:in|during|over|for)?\s*(?:the\s*)?(?:last|past)\s*([0-9]+)\s*minutes\b", None, "minutes_var"),
            (r"\b(?:in|during|over|for)?\s*(?:the\s*)?(?:last|past)\s*([0-9]+)\s*hours\b", None, "hours_var")
        ]

        for pat, delta, label in relative_patterns:
            match = re.search(pat, text)
            if match:
                if label == "seconds_var":
                    delta = int(match.group(1))
                    label = f"last {delta} seconds"
                elif label == "minutes_var":
                    mins = int(match.group(1))
                    delta = mins * 60
                    label = f"last {mins} minutes"
                elif label == "hours_var":
                    hrs = int(match.group(1))
                    delta = hrs * 3600
                    label = f"last {hrs} hours"
                    
                min_t = max(0.0, ref_time - delta)
                max_t = ref_time + (60.0 if is_epoch else 0.0)
                remaining = text[:match.start()] + " " + text[match.end():]
                return {
                    "type": "relative",
                    "label": label,
                    "min_timestamp": min_t,
                    "max_timestamp": max_t,
                    "delta_seconds": delta
                }, " ".join(remaining.split())

        # 3. Calendar patterns: today, yesterday
        if re.search(r"\b(?:today)\b", text):
            match = re.search(r"\b(?:today)\b", text)
            remaining = text[:match.start()] + " " + text[match.end():]
            min_t = start_of_today if is_epoch else 0.0
            max_t = ref_time + 60.0 if is_epoch else ref_time
            return {
                "type": "calendar",
                "label": "today",
                "min_timestamp": min_t,
                "max_timestamp": max_t
            }, " ".join(remaining.split())

        if re.search(r"\b(?:yesterday)\b", text):
            match = re.search(r"\b(?:yesterday)\b", text)
            remaining = text[:match.start()] + " " + text[match.end():]
            if is_epoch:
                min_t = start_of_today - 86400.0
                max_t = start_of_today
            else:
                min_t = 0.0
                max_t = 0.0
            return {
                "type": "calendar",
                "label": "yesterday",
                "min_timestamp": min_t,
                "max_timestamp": max_t
            }, " ".join(remaining.split())

        # 4. Explicit second ranges for uploaded video: "first 10 seconds", "between 5s and 15s"
        first_match = re.search(r"\b(?:in\s+)?(?:the\s+)?first\s+([0-9]+(?:\.[0-9]+)?)\s*(?:s|sec|seconds)?\b", text)
        if first_match:
            max_t = float(first_match.group(1))
            remaining = text[:first_match.start()] + " " + text[first_match.end():]
            return {
                "type": "explicit",
                "label": f"first {max_t}s",
                "min_timestamp": 0.0,
                "max_timestamp": max_t
            }, " ".join(remaining.split())

        between_match = re.search(r"\bbetween\s*([0-9]+(?:\.[0-9]+)?)\s*(?:s|sec|seconds)?\s*and\s*([0-9]+(?:\.[0-9]+)?)\s*(?:s|sec|seconds)?\b", text)
        if between_match:
            t1 = float(between_match.group(1))
            t2 = float(between_match.group(2))
            min_t, max_t = min(t1, t2), max(t1, t2)
            remaining = text[:between_match.start()] + " " + text[between_match.end():]
            return {
                "type": "explicit",
                "label": f"between {min_t}s and {max_t}s",
                "min_timestamp": min_t,
                "max_timestamp": max_t
            }, " ".join(remaining.split())

        return None, text

    def _extract_color(self, text: str) -> Optional[str]:
        words = text.split()
        for w in words:
            clean_w = re.sub(r"[^\w]", "", w)
            if clean_w in COLORS:
                return clean_w
        return None

    def _extract_object(self, text: str) -> Optional[str]:
        for standard_name, aliases in OBJECT_ALIASES.items():
            for alias in aliases:
                if re.search(rf"\b{re.escape(alias)}\b", text):
                    return standard_name
                    
        # Check if query references an open-vocabulary unknown entity (e.g. elephant, submarine, dog, cat)
        ignore_words = set(STOP_WORDS) | set(COLORS) | {
            "walking", "walks", "walked", "running", "runs", "ran", "jogging",
            "riding", "rides", "cycling", "sitting", "sits", "seated",
            "stopped", "parked", "stationary", "standing", "stands",
            "moving", "motion", "passed", "passing", "driving", "entering", "entered", "exiting", "exited",
            "with", "without", "carrying", "having", "wearing", "in", "at", "on", "from", "to", "by", "of",
            "shirt", "pants", "tshirt", "t-shirt", "clothes", "clothing", "jacket", "dress", "jeans", "hand", "handbag"
        }
        for w in text.split():
            clean_w = re.sub(r"[^\w]", "", w.lower())
            if clean_w and clean_w not in ignore_words and len(clean_w) > 2:
                return clean_w
        return None

    def _extract_visual_concept(self, text: str, color: Optional[str], obj: Optional[str], sec_obj: Optional[str], original: str) -> str:
        clean = re.sub(r"[^\w\s-]", " ", text)
        words = clean.split()
        
        concept_words = [w for w in words if w not in STOP_WORDS]
        concept_str = " ".join(concept_words).strip()
        
        if not concept_str:
            parts = []
            if color: parts.append(color)
            if obj: parts.append(obj)
            if sec_obj: parts.append(f"with {sec_obj}")
            concept_str = " ".join(parts) if parts else original.strip()
                
        return concept_str

    def _get_reference_time(self) -> float:
        try:
            row = execute_query("SELECT MAX(end_timestamp) as max_t FROM events", fetch=True, fetch_one=True)
            if row and row["max_t"] is not None and row["max_t"] > 0:
                max_t = float(row["max_t"])
                # If there are live events (epoch time), use current system time
                if max_t > 1000000.0:
                    return time.time()
                return max_t
        except Exception as e:
            logger.warning(f"Error querying max event timestamp: {e}")
        return 60.0

_parser_instance = None
def get_query_parser() -> QueryParser:
    global _parser_instance
    if _parser_instance is None:
        _parser_instance = QueryParser()
    return _parser_instance
