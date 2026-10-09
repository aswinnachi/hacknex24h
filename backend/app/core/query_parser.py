import re
import time
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
    "helmet": ["helmet", "helmets"],
    "hat": ["hat", "hats", "cap"],
    "glasses": ["glasses", "sunglasses", "spectacles"]
}

STOP_WORDS = [
    "find", "show", "me", "search", "for", "look", "can", "you", "did", "a", "an", "the",
    "any", "all", "is", "there", "are", "see", "pass", "through", "passing", "moving",
    "detected", "appear", "appeared", "in", "at", "on", "from", "to", "please", "tell",
    "what", "where", "which", "how", "many", "count", "were", "happened", "occurred"
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

        # 5. Extract Carried Objects & Negative Attributes
        sec_obj, relation, requires_bag, forbid_bag, req_accessories, forbid_accessories = self._extract_accessories_and_relations(cleaned_query)

        # 6. Resolve Memory References / Clarify-Once
        mem_mgr = get_memory_manager()
        matched_mem, unknown_location = mem_mgr.resolve_location(cleaned_query)
        resolved_memory = None
        needs_clarification = False
        clarification_prompt = None
        missing_reference = None
        camera_id = None
        camera_label = None

        if matched_mem:
            resolved_memory = {
                "id": matched_mem["id"],
                "name": matched_mem["key_name"],
                "camera_id": matched_mem["camera_id"],
                "region": matched_mem.get("region")
            }
            camera_id = matched_mem["camera_id"]
            camera_label = f"Memory: {matched_mem['key_name']} ({matched_mem['camera_id']})"
            cleaned_query = cleaned_query.replace(matched_mem["key_name"], " ")
        else:
            camera_id, camera_label, cleaned_query = self._extract_camera(cleaned_query)
            if not camera_id and unknown_location:
                if intent != "TEMPORAL_SEARCH" and intent != "INVENTORY":
                    needs_clarification = True
                    missing_reference = unknown_location
                    clarification_prompt = f"I don't know which camera/region represents '{unknown_location}'. Please select it."

        # 7. Extract Dominant Color if not already extracted in clothing
        color = upper_color or self._extract_color(cleaned_query)

        # 8. Extract Canonical Object Class
        obj = self._extract_object(cleaned_query)

        # 9. Structured Constraints (Positive & Negative)
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

        # 10. Extract Visual Concept for Embedding
        visual_concept = self._extract_visual_concept(cleaned_query, color, obj, sec_obj if not forbid_bag else None, original_query)

        return {
            "original_query": original_query,
            "intent": intent,
            "camera": camera_id,
            "camera_label": camera_label,
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
            "required_accessories": req_accessories,
            "forbidden_accessories": forbid_accessories,
            "required_objects": required_objects,
            "forbidden_objects": forbidden_objects,
            "required_attributes": required_attributes,
            "forbidden_attributes": forbidden_attributes,
            "visual_concept": visual_concept
        }

    def _classify_intent(self, text: str) -> str:
        if re.search(r"\b(?:how many|count\b|number of|total number|total count|total)\b", text):
            return "COUNT"
        if re.search(r"\b(?:what objects|what was detected|list objects|inventory|what is in the video)\b", text):
            return "INVENTORY"
        if re.search(r"\b(?:what happened|what occurred|show what happened)\b", text):
            return "TEMPORAL_SEARCH"
        if re.search(r"\b(?:did a|did the|is there a|was there a|can you see a|has a|any)\b", text) and text.endswith("?"):
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
        if re.search(r"\b(?:moving|in motion|passed|passing|driving|entering|entered|exiting|exited)\b", text):
            return "moving"
        return None

    def _extract_clothing_attributes(self, text: str) -> Tuple[Optional[str], Optional[str], Optional[str], Optional[str]]:
        upper_color = None
        upper_type = None
        lower_color = None
        lower_type = None

        # Upper clothing patterns: e.g. "white shirt", "wearing a white shirt", "wearing red jacket", "black t-shirt"
        for col in COLORS:
            if re.search(rf"\b(?:wearing\s+(?:a\s+)?)?{col}\s+(?:shirt|t-shirt|jacket|hoodie|coat|top|tshirt|sweater)\b", text):
                upper_color = col
                upper_type = "shirt"
                break
            elif re.search(rf"\b(?:wearing|in)\s+{col}\b", text):
                upper_color = col
                upper_type = "shirt"
                break

        # Lower clothing patterns: e.g. "black pants", "wearing blue jeans", "wearing dark trousers"
        for col in COLORS:
            if re.search(rf"\b{col}\s+(?:pants|jeans|trousers|shorts|skirt)\b", text):
                lower_color = col
                lower_type = "pants"
                break

        return upper_color, upper_type, lower_color, lower_type

    def _extract_accessories_and_relations(self, text: str) -> Tuple[Optional[str], Optional[str], bool, bool, List[str], List[str]]:
        sec_obj = None
        relation = None
        requires_bag = False
        forbid_bag = False
        req_accessories = []
        forbid_accessories = []

        # Negative bag relations
        if re.search(r"\b(?:without a bag|without bag|without any bag|without backpack|without a backpack|without backpacks|no bag|no backpack|not carrying a bag|not carrying bag|not carrying any bag|not holding a bag|without luggage|without suitcase)\b", text):
            return "bag", "without", False, True, [], ["bag", "backpack", "suitcase", "handbag"]

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
        if re.search(r"\b(?:with a bag|with bag|with any bag|carrying a bag|carrying bag|carrying any bag|holding a bag|has a bag)\b", text):
            sec_obj = "bag"
            relation = "carrying"
            requires_bag = True
            if not req_accessories:
                req_accessories.append("bag")

        return sec_obj, relation, requires_bag, forbid_bag, req_accessories, forbid_accessories

        # Negative bag relation (person without bag)
        if re.search(r"\b(?:without a bag|without bag|without any bag|without backpack|without a backpack|without backpacks|no bag|no backpack|not carrying a bag|not carrying bag|not carrying any bag|not holding a bag|without luggage|without suitcase)\b", text):
            return "bag", "without", False, True

        return None, None, False, False

    def _extract_camera(self, text: str) -> Tuple[Optional[str], Optional[str], str]:
        # Check for explicit 'all cameras' or 'across all cameras'
        all_cam_match = re.search(r"\b(?:across\s+all\s+cameras|in\s+all\s+cameras|all\s+cameras|every\s+camera)\b", text)
        if all_cam_match:
            remaining = text[:all_cam_match.start()] + " " + text[all_cam_match.end():]
            return "all", "All Cameras", " ".join(remaining.split())

        patterns = [
            (r"\b(?:in|on|at|from)?\s*camera\s*0*([0-9]+)\b", "camera"),
            (r"\b(?:in|on|at|from)?\s*cam\s*0*([0-9]+)\b", "cam"),
            (r"\b(?:in|on|at|from)?\s*camera_0*([0-9]+)\b", "camera_"),
        ]
        
        for pat, prefix in patterns:
            match = re.search(pat, text)
            if match:
                cam_num = int(match.group(1))
                camera_id = f"camera_{cam_num:02d}"
                camera_label = f"Camera {cam_num}"
                remaining = text[:match.start()] + " " + text[match.end():]
                return camera_id, camera_label, " ".join(remaining.split())

        try:
            cameras = execute_query("SELECT id, name FROM cameras", fetch=True)
            for cam in cameras:
                cam_name = cam["name"].lower()
                if cam_name in text:
                    idx = text.find(cam_name)
                    remaining = text[:idx] + " " + text[idx + len(cam_name):]
                    return cam["id"], cam["name"], " ".join(remaining.split())
        except Exception as e:
            logger.warning(f"Error checking camera names: {e}")

        return None, None, text

    def _extract_time(self, text: str) -> Tuple[Optional[Dict[str, Any]], str]:
        ref_time = self._get_reference_time()
        
        relative_patterns = [
            (r"\b(?:in|during|over|for)?\s*(?:the\s*)?(?:last|past)\s*minute\b", 60, "last minute"),
            (r"\b(?:in|during|over|for)?\s*(?:the\s*)?(?:last|past)\s*1\s*minute\b", 60, "last minute"),
            (r"\b(?:in|during|over|for)?\s*(?:the\s*)?(?:last|past)\s*5\s*minutes\b", 300, "last 5 minutes"),
            (r"\b(?:in|during|over|for)?\s*(?:the\s*)?(?:last|past)\s*10\s*minutes\b", 600, "last 10 minutes"),
            (r"\b(?:in|during|over|for)?\s*(?:the\s*)?(?:last|past)\s*30\s*minutes\b", 1800, "last 30 minutes"),
            (r"\b(?:in|during|over|for)?\s*(?:the\s*)?(?:last|past)\s*(?:an|1)?\s*hour\b", 3600, "last hour"),
            (r"\b(?:in|during|over|for)?\s*(?:the\s*)?(?:last|past)\s*([0-9]+)\s*seconds\b", None, "seconds_var"),
            (r"\b(?:in|during|over|for)?\s*(?:the\s*)?(?:last|past)\s*([0-9]+)\s*minutes\b", None, "minutes_var")
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
                    
                min_t = max(0.0, ref_time - delta)
                max_t = ref_time
                remaining = text[:match.start()] + " " + text[match.end():]
                return {
                    "type": "relative",
                    "label": label,
                    "min_timestamp": min_t,
                    "max_timestamp": max_t,
                    "delta_seconds": delta
                }, " ".join(remaining.split())

        if re.search(r"\b(?:today)\b", text):
            match = re.search(r"\b(?:today)\b", text)
            remaining = text[:match.start()] + " " + text[match.end():]
            return {
                "type": "calendar",
                "label": "today",
                "min_timestamp": 0.0,
                "max_timestamp": ref_time
            }, " ".join(remaining.split())

        if re.search(r"\b(?:yesterday)\b", text):
            match = re.search(r"\b(?:yesterday)\b", text)
            remaining = text[:match.start()] + " " + text[match.end():]
            return {
                "type": "calendar",
                "label": "yesterday",
                "min_timestamp": 0.0,
                "max_timestamp": 0.0
            }, " ".join(remaining.split())

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
            "shirt", "pants", "tshirt", "t-shirt", "clothes", "clothing", "jacket", "dress", "jeans"
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
                return float(row["max_t"])
        except Exception as e:
            logger.warning(f"Error querying max event timestamp: {e}")
        return 60.0

_parser_instance = None
def get_query_parser() -> QueryParser:
    global _parser_instance
    if _parser_instance is None:
        _parser_instance = QueryParser()
    return _parser_instance
