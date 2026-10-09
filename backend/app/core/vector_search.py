import os
os.environ["KMP_DUPLICATE_LIB_OK"] = "TRUE"
import json
import numpy as np
from typing import List, Dict, Any, Optional, Tuple
from backend.app.core.logging import logger
from backend.app.db.database import execute_query

try:
    import faiss
    HAS_FAISS = True
except ImportError:
    HAS_FAISS = False
    logger.warning("FAISS not found, falling back to NumPy cosine similarity.")

INDEX_PATH = "data/vector_index"
os.makedirs(INDEX_PATH, exist_ok=True)

COLOR_FAMILIES = {
    "white": {"white", "silver", "light gray", "light grey", "cream", "pearl", "light"},
    "silver": {"silver", "white", "gray", "grey", "light gray", "light grey"},
    "gray": {"gray", "grey", "silver", "slate", "charcoal", "dark gray"},
    "grey": {"gray", "grey", "silver", "slate", "charcoal", "dark gray"},
    "black": {"black", "dark", "charcoal", "navy", "dark gray"},
    "dark": {"black", "dark", "charcoal", "navy", "dark gray", "brown"},
    "yellow": {"yellow", "gold", "golden", "amber", "mustard", "orange", "beige"},
    "orange": {"orange", "yellow", "amber", "gold"},
    "red": {"red", "maroon", "burgundy", "crimson", "ruby"},
    "blue": {"blue", "navy", "teal", "cyan", "sky blue", "indigo"},
    "green": {"green", "olive", "emerald", "lime"},
    "brown": {"brown", "tan", "bronze", "beige"},
}

def are_colors_compatible(req_color: Optional[str], cand_color: Optional[str]) -> bool:
    if not req_color or not cand_color or cand_color == "unknown":
        return False
    r = req_color.lower().strip()
    c = cand_color.lower().strip()
    if r == c or r in c or c in r:
        return True
    family = COLOR_FAMILIES.get(r, {r})
    if c in family:
        return True
    for item in family:
        if item in c or c in item:
            return True
    return False

def validate_candidate_event(event_dict: Dict[str, Any], camera_id: Optional[str] = None) -> Tuple[bool, str]:
    """
    Hard verification of data lifecycle, camera consistency, dataset ID, and video timestamp boundaries.
    """
    cam_id = event_dict.get("camera_id")
    if not cam_id:
        return False, "Missing camera_id"
    if camera_id and cam_id != camera_id:
        return False, f"Camera mismatch: {cam_id} != {camera_id}"

    cam = execute_query("SELECT id, dataset_id, duration, status FROM cameras WHERE id = ?", (cam_id,), fetch=True, fetch_one=True)
    if not cam:
        return False, f"Camera {cam_id} does not exist in database"

    cam_dict = dict(cam)
    # Check dataset_id matches camera's active dataset_id
    if cam_dict.get("dataset_id") and event_dict.get("dataset_id"):
        if event_dict["dataset_id"] != cam_dict["dataset_id"]:
            return False, f"Stale event dataset {event_dict['dataset_id']} != camera dataset {cam_dict['dataset_id']}"

    # Check timestamps within video duration
    duration = cam_dict.get("duration")
    if duration and duration > 0:
        start_t = float(event_dict.get("start_timestamp", 0.0))
        end_t = float(event_dict.get("end_timestamp", start_t))
        if start_t < 0.0 or start_t > (duration + 0.5):
            return False, f"Event start timestamp {start_t:.2f}s is beyond video duration ({duration:.2f}s)"
        if end_t < 0.0:
            return False, f"Event end timestamp {end_t:.2f}s is negative"

    return True, "Valid"

def is_candidate_grounded(
    concept: str,
    attributes: Dict[str, Any],
    required_object: Optional[str] = None,
    required_color: Optional[str] = None,
    required_upper_color: Optional[str] = None,
    required_lower_color: Optional[str] = None,
    required_action: Optional[str] = None,
    requires_bag: Optional[bool] = None,
    forbid_bag: Optional[bool] = None,
    required_accessories: Optional[List[str]] = None,
    forbidden_accessories: Optional[List[str]] = None,
    forbidden_objects: Optional[List[str]] = None
) -> Tuple[bool, str]:
    """
    Rigorously checks if a candidate event satisfies hard positive and negative query constraints.
    Returns (is_valid: bool, reason: str).
    """
    c_lower = concept.lower()
    attr_color = (attributes.get("color") or "unknown").lower()
    upper_color = (attributes.get("upper_color") or attr_color or "unknown").lower()
    lower_color = (attributes.get("lower_color") or "unknown").lower()
    is_moving = bool(attributes.get("is_moving", False))
    cand_action = (attributes.get("action") or ("moving" if is_moving else "stationary")).lower()
    has_bag = bool(attributes.get("has_bag", False))
    cand_accessories = [a.lower() for a in attributes.get("accessories", [])]

    # 1. Forbidden Objects & Negative Constraints Check
    if forbidden_objects:
        for f_obj in forbidden_objects:
            f_lower = f_obj.lower()
            if f_lower in ("bag", "backpack", "suitcase", "luggage", "handbag"):
                if c_lower in ("bag", "backpack", "suitcase", "handbag") or has_bag:
                    return False, "Candidate has a bag/backpack associated, violating negative constraint."
            elif f_lower in ("car", "automobile", "sedan"):
                if c_lower in ("car", "automobile", "sedan"):
                    return False, f"Candidate is a forbidden car."
            elif f_lower in ("vehicle", "transport"):
                if c_lower in ("car", "vehicle", "white vehicle", "truck", "bus", "motorcycle", "bicycle"):
                    return False, f"Candidate is a forbidden vehicle ({c_lower})."
            elif f_lower == "person":
                if c_lower == "person":
                    return False, "Candidate is a forbidden person."

    if forbidden_accessories:
        for f_acc in forbidden_accessories:
            if f_acc.lower() in cand_accessories or (f_acc.lower() in ("bag", "backpack") and has_bag):
                return False, f"Candidate has forbidden accessory: {f_acc}."

    # 2. Strict Secondary Object (Bag / Backpack / Handbag) Constraints
    if requires_bag and not has_bag:
        return False, "Candidate person does not have a verified bag/backpack/handbag."
    if forbid_bag:
        if has_bag:
            return False, "Candidate person has a bag/backpack detected (query requires without bag)."
        if c_lower in ("bag", "backpack", "suitcase", "handbag"):
            return False, "Candidate is a bag, but query requires without bag."

    cand_is_auto = (c_lower in ("auto", "auto rickshaw", "autorickshaw", "three wheeler") or attributes.get("is_auto") is True or attributes.get("vehicle_type") == "auto")

    # 3. Exact Canonical Object Class Check (Strict Separation for Auto, Flexible Vehicle Grounding)
    if required_object:
        req_obj = required_object.lower()
        if req_obj in ("auto", "auto rickshaw", "autorickshaw", "auto-rickshaw", "tuk tuk", "three wheeler", "3 wheeler"):
            if not cand_is_auto:
                return False, f"Candidate is a {c_lower}, not an auto-rickshaw."
        elif req_obj in ("car", "automobile", "sedan", "suv", "van"):
            if cand_is_auto:
                return False, "Candidate is an auto-rickshaw, not a car."
            # In surveillance feeds, passenger cars, delivery vans, and light commercial vehicles (tempos/trucks) are all 4-wheel vehicles
            if c_lower not in ("car", "automobile", "sedan", "suv", "van", "vehicle", "truck"):
                return False, f"Candidate is a {c_lower}, not a car."
        elif req_obj in ("bus", "buses"):
            if cand_is_auto:
                return False, "Candidate is an auto-rickshaw, not a bus."
            if c_lower not in ("bus", "buses"):
                return False, f"Candidate is a {c_lower}, not a bus."
        elif req_obj in ("truck", "trucks", "lorry", "pickup"):
            if cand_is_auto:
                return False, "Candidate is an auto-rickshaw, not a truck."
            if c_lower not in ("truck", "trucks", "lorry", "pickup", "van"):
                return False, f"Candidate is a {c_lower}, not a truck."
        elif req_obj in ("motorcycle", "motorbike", "moped", "scooter", "scooters", "scotter", "scooty", "twowheeler", "two wheeler", "two-wheeler"):
            if cand_is_auto:
                return False, "Candidate is an auto-rickshaw, not a motorcycle."
            if c_lower not in ("motorcycle", "motorbike", "moped", "scooter"):
                return False, f"Candidate is a {c_lower}, not a motorcycle."
        elif req_obj in ("bicycle", "bike", "cycle", "bicycles", "bikes"):
            if cand_is_auto:
                return False, "Candidate is an auto-rickshaw, not a bicycle."
            if c_lower not in ("bicycle", "bike", "cycle", "bicycles", "bikes"):
                return False, f"Candidate is a {c_lower}, not a bicycle."
        elif req_obj in ("person", "people", "man", "woman", "human", "pedestrian"):
            if c_lower != "person":
                return False, f"Candidate is a {c_lower}, not a person."
        elif req_obj in ("vehicle", "vehicles", "transport"):
            if not cand_is_auto and c_lower not in ("car", "bus", "truck", "motorcycle", "bicycle", "vehicle", "white vehicle"):
                return False, f"Candidate is a {c_lower}, not a vehicle."
        elif req_obj in ("backpack", "pack"):
            if c_lower not in ("backpack", "pack") and not attributes.get("has_backpack"):
                return False, f"Candidate is a {c_lower}, not a backpack."
        elif req_obj in ("handbag", "purse"):
            if c_lower not in ("handbag", "purse") and not attributes.get("has_handbag"):
                return False, f"Candidate is a {c_lower}, not a handbag."
        elif req_obj in ("suitcase", "luggage"):
            if c_lower not in ("suitcase", "luggage") and not attributes.get("has_suitcase"):
                return False, f"Candidate is a {c_lower}, not a suitcase."
        elif req_obj in ("bag", "bags"):
            if c_lower not in ("bag", "backpack", "handbag", "suitcase") and not has_bag:
                return False, f"Candidate is a {c_lower}, not a bag."
        elif req_obj == "umbrella":
            if c_lower != "umbrella" and not attributes.get("has_umbrella"):
                return False, f"Candidate is a {c_lower}, not an umbrella."
        elif req_obj in ("phone", "cellphone", "mobile"):
            if c_lower not in ("phone", "cellphone", "mobile") and not attributes.get("has_phone"):
                return False, f"Candidate is a {c_lower}, not a phone."
        elif req_obj in ("bottle", "cup"):
            if c_lower not in ("bottle", "cup") and not attributes.get("has_bottle"):
                return False, f"Candidate is a {c_lower}, not a bottle."
        elif c_lower != req_obj:
            return False, f"Candidate is a {c_lower}, not a {req_obj}."

    # 4. Strict Color Verification (Upper clothing for persons, overall body color for vehicles & objects)
    req_col = (required_upper_color or required_color or "").lower()
    if req_col:
        matched_color = False
        if cand_is_auto:
            # Auto-rickshaws frequently have two-tone yellow top and green/black body
            if req_col in ("green", "yellow", "black") and (req_col in attr_color or "green and yellow" in attr_color):
                matched_color = True
            elif are_colors_compatible(req_col, attr_color) or are_colors_compatible(req_col, attributes.get("vehicle_color", "")):
                matched_color = True
        elif c_lower == "person":
            if are_colors_compatible(req_col, upper_color) or are_colors_compatible(req_col, attr_color):
                matched_color = True
        else:
            if are_colors_compatible(req_col, attr_color) or are_colors_compatible(req_col, attributes.get("vehicle_color", "")):
                matched_color = True
        
        if not matched_color:
            verified_c = attr_color if c_lower != "person" else (upper_color or attr_color)
            return False, f"Color verified as '{verified_c}', not requested '{req_col}'."

    # 5. Strict Lower Clothing Color Verification
    if required_lower_color:
        req_lcol = required_lower_color.lower()
        if lower_color != "unknown" and not are_colors_compatible(req_lcol, lower_color):
            return False, f"Lower clothing color verified as '{lower_color}', not requested '{req_lcol}'."

    # 6. Specific Accessory Verification (e.g. backpack, handbag, umbrella, phone, bottle)
    if required_accessories:
        for req_acc in required_accessories:
            acc_name = req_acc.lower()
            if acc_name in ("backpack", "pack"):
                if not attributes.get("has_backpack") and "backpack" not in cand_accessories:
                    return False, "Candidate does not have a verified backpack."
            elif acc_name in ("handbag", "purse"):
                if not attributes.get("has_handbag") and "handbag" not in cand_accessories:
                    return False, "Candidate does not have a verified handbag."
            elif acc_name in ("suitcase", "luggage"):
                if not attributes.get("has_suitcase") and "suitcase" not in cand_accessories:
                    return False, "Candidate does not have a verified suitcase."
            elif acc_name == "umbrella":
                if not attributes.get("has_umbrella") and "umbrella" not in cand_accessories:
                    return False, "Candidate does not have a verified umbrella."
            elif acc_name in ("phone", "cellphone", "mobile"):
                if not attributes.get("has_phone") and "phone" not in cand_accessories:
                    return False, "Candidate does not have a verified phone in hand."
            elif acc_name in ("bottle", "cup"):
                if not attributes.get("has_bottle") and "bottle" not in cand_accessories:
                    return False, "Candidate does not have a verified bottle in hand."
            elif acc_name in ("bag", "bags"):
                if not has_bag:
                    return False, "Candidate does not have a verified bag."

    # 7. Movement & Action Verification
    if required_action:
        req_act = required_action.lower()
        if req_act in ("moving", "in motion", "passed", "passing", "entering", "exiting"):
            if not is_moving:
                return False, "Candidate object is stationary, not moving."
        elif req_act in ("stationary", "stopped", "parked", "standing"):
            if is_moving:
                return False, "Candidate object is in motion, not stationary."
        elif req_act == "walking":
            if cand_action != "walking" and not is_moving:
                return False, "Candidate person is not walking."
        elif req_act == "running":
            if cand_action != "running":
                return False, "Candidate person is not running."
        elif req_act == "riding":
            if cand_action != "riding" and c_lower not in ("bicycle", "motorcycle"):
                return False, "Candidate is not riding."

    return True, "Verified"

class VectorSearch:
    def __init__(self, dimension=None):
        if dimension is None:
            try:
                from backend.app.core.embeddings import get_embedder
                embedder = get_embedder()
                dimension = getattr(embedder, "dimension", 768)
            except Exception:
                dimension = 768
        self.dimension = dimension
        self.index_file = f"{INDEX_PATH}/faiss.index"
        self.mapping_file = f"{INDEX_PATH}/mapping.json"
        
        self.id_to_event = {}
        self.next_id = 0
        
        if HAS_FAISS:
            if os.path.exists(self.index_file):
                self.index = faiss.read_index(self.index_file)
                self.dimension = self.index.d
            else:
                self.index = faiss.IndexFlatIP(self.dimension)
        else:
            self.vectors = []
            if os.path.exists(f"{INDEX_PATH}/numpy_vectors.npy"):
                self.vectors = list(np.load(f"{INDEX_PATH}/numpy_vectors.npy"))
            
        if os.path.exists(self.mapping_file):
            with open(self.mapping_file, "r") as f:
                mapping = json.load(f)
                self.id_to_event = {int(k): v for k, v in mapping.items()}
                if self.id_to_event:
                    self.next_id = max(self.id_to_event.keys()) + 1

    def save(self):
        with open(self.mapping_file, "w") as f:
            json.dump(self.id_to_event, f)
            
        if HAS_FAISS:
            faiss.write_index(self.index, self.index_file)
        else:
            np.save(f"{INDEX_PATH}/numpy_vectors.npy", np.array(self.vectors))

    def reload(self):
        if HAS_FAISS:
            if os.path.exists(self.index_file):
                self.index = faiss.read_index(self.index_file)
        else:
            if os.path.exists(f"{INDEX_PATH}/numpy_vectors.npy"):
                self.vectors = list(np.load(f"{INDEX_PATH}/numpy_vectors.npy"))
        if os.path.exists(self.mapping_file):
            with open(self.mapping_file, "r") as f:
                mapping = json.load(f)
                self.id_to_event = {int(k): v for k, v in mapping.items()}
                if self.id_to_event:
                    self.next_id = max(self.id_to_event.keys()) + 1

    def reset(self):
        """Completely wipes in-memory and on-disk index."""
        if HAS_FAISS:
            self.index = faiss.IndexFlatIP(self.dimension)
        else:
            self.vectors = []
        self.id_to_event = {}
        self.next_id = 0
        
        if os.path.exists(self.index_file):
            try: os.remove(self.index_file)
            except Exception: pass
        if os.path.exists(self.mapping_file):
            try: os.remove(self.mapping_file)
            except Exception: pass
        if os.path.exists(f"{INDEX_PATH}/numpy_vectors.npy"):
            try: os.remove(f"{INDEX_PATH}/numpy_vectors.npy")
            except Exception: pass
        self.save()
        logger.info("Vector index reset to empty.")

    def rebuild_from_db(self):
        """Rebuilds vector index from remaining valid events in database."""
        from backend.app.core.embeddings import get_embedder
        
        logger.info("Rebuilding vector index from database events...")
        embedder = get_embedder()
        self.dimension = getattr(embedder, "dimension", self.dimension)
        
        if HAS_FAISS:
            new_index = faiss.IndexFlatIP(self.dimension)
        else:
            new_vectors = []
            
        new_mapping = {}
        new_id = 0
        
        rows = execute_query("SELECT id, representative_frame, concept FROM events", fetch=True)
        concept_cache = {}
        for r in rows:
            rd = dict(r)
            event_id = rd["id"]
            concept = rd.get("concept") or "object"
            frame_path = rd.get("representative_frame") or ""
            
            vec = None
            # Visual frame embedding (multimodal frame representation in FAISS)
            if frame_path and os.path.exists(frame_path) and embedder.available:
                try:
                    vec = embedder.embed_image(frame_path)
                except Exception:
                    vec = None
                    
            if vec is None:
                if concept not in concept_cache and embedder.available:
                    try:
                        concept_cache[concept] = embedder.embed_text(concept)
                    except Exception:
                        pass
                vec = concept_cache.get(concept)
                
            if vec is None:
                vec = [0.0] * self.dimension
                
            vec_arr = np.array([vec], dtype=np.float32)
            if HAS_FAISS:
                new_index.add(vec_arr)
            else:
                new_vectors.append(vec_arr[0])
                
            new_mapping[new_id] = event_id
            execute_query("UPDATE events SET embedding_ref = ? WHERE id = ?", (str(new_id), event_id))
            new_id += 1
            
        if HAS_FAISS:
            self.index = new_index
        else:
            self.vectors = new_vectors
        self.id_to_event = new_mapping
        self.next_id = new_id
        self.save()
        logger.info(f"Vector index rebuilt successfully with {new_id} events.")

    def add_vector(self, event_id: str, vector: np.ndarray):
        vector = np.array([vector], dtype=np.float32)
        idx = self.next_id
        
        if HAS_FAISS:
            self.index.add(vector)
        else:
            self.vectors.append(vector[0])
            
        self.id_to_event[idx] = event_id
        self.next_id += 1
        
        execute_query("UPDATE events SET embedding_ref = ? WHERE id = ?", (str(idx), event_id))

    def search_by_text(
        self,
        text_vector: np.ndarray,
        top_k: int = 5,
        required_object: Optional[str] = None,
        required_color: Optional[str] = None,
        required_upper_color: Optional[str] = None,
        required_lower_color: Optional[str] = None,
        required_action: Optional[str] = None,
        requires_bag: Optional[bool] = None,
        forbid_bag: Optional[bool] = None,
        required_accessories: Optional[List[str]] = None,
        forbidden_accessories: Optional[List[str]] = None,
        forbidden_objects: Optional[List[str]] = None,
        camera_id: Optional[str] = None,
        min_timestamp: Optional[float] = None,
        max_timestamp: Optional[float] = None,
        region: Optional[list] = None,
        return_debug: bool = False
    ):
        from backend.app.core.memory import check_bbox_region_overlap
        self.reload()
        text_vector = np.array([text_vector], dtype=np.float32)
        results = []
        
        search_pool = max(top_k * 50, len(self.id_to_event), 200)
        
        if HAS_FAISS:
            if self.index.ntotal == 0:
                return ([], {}) if return_debug else []
            if text_vector.shape[1] != self.index.d:
                logger.warning(f"Embedding dim mismatch: query vector ({text_vector.shape[1]}) != FAISS index ({self.index.d}). Auto-rebuilding index...")
                self.rebuild_from_db()
                if self.index.ntotal == 0:
                    return ([], {}) if return_debug else []
            k_search = min(self.index.ntotal, search_pool)
            distances, indices = self.index.search(text_vector, k_search)
            for dist, idx in zip(distances[0], indices[0]):
                if idx != -1 and idx in self.id_to_event:
                    results.append({"event_id": self.id_to_event[idx], "similarity": float(dist)})
        else:
            if not self.vectors:
                return ([], {}) if return_debug else []
            vecs = np.array(self.vectors)
            similarities = np.dot(vecs, text_vector[0])
            top_indices = np.argsort(similarities)[::-1][:search_pool]
            for idx in top_indices:
                if idx in self.id_to_event:
                    results.append({"event_id": self.id_to_event[idx], "similarity": float(similarities[idx])})
                    
        # Structured DB candidates supplement (Hybrid Vector + Relational Retrieval)
        seen_events = {r["event_id"] for r in results}
        if required_object or camera_id:
            db_sql = "SELECT id FROM events WHERE 1=1"
            db_params = []
            if camera_id:
                db_sql += " AND camera_id = ?"
                db_params.append(camera_id)
            if required_object:
                db_sql += " AND concept = ?"
                db_params.append(required_object.lower())
            db_sql += " ORDER BY start_timestamp ASC LIMIT 500"
            db_rows = execute_query(db_sql, tuple(db_params), fetch=True) or []
            for dbr in db_rows:
                eid = dbr["id"]
                if eid not in seen_events:
                    results.append({"event_id": eid, "similarity": 0.5})
                    seen_events.add(eid)
                    
        detailed_results = []
        candidates_checked = 0
        rejected_candidates = []
        accepted_candidates = []

        for r in results:
            event_id = r["event_id"]
            row = execute_query(
                "SELECT id, camera_id, dataset_id, tracking_id, start_timestamp, end_timestamp, concept, bounding_boxes, representative_frame, attributes FROM events WHERE id = ?",
                (event_id,),
                fetch=True,
                fetch_one=True
            )
            if not row:
                continue

            candidates_checked += 1
            row_dict = dict(row)
            
            # 1. HARD LIFECYCLE & BOUNDARY VALIDATION
            is_valid_lifecycle, lifecycle_err = validate_candidate_event(row_dict, camera_id)
            if not is_valid_lifecycle:
                if len(rejected_candidates) < 15:
                    rejected_candidates.append(f"Event {event_id[:8]} (Track #{row_dict.get('tracking_id')}): {lifecycle_err}")
                continue

            # Parse attributes
            attrs = {}
            if row_dict.get("attributes"):
                try: attrs = json.loads(row_dict["attributes"])
                except: pass

            # 2. Apply Time Filter
            if min_timestamp is not None and row_dict["end_timestamp"] < min_timestamp:
                if len(rejected_candidates) < 15:
                    rejected_candidates.append(f"Event {event_id[:8]}: timestamp {row_dict['end_timestamp']:.2f}s < {min_timestamp:.2f}s")
                continue
            if max_timestamp is not None and row_dict["start_timestamp"] > max_timestamp:
                if len(rejected_candidates) < 15:
                    rejected_candidates.append(f"Event {event_id[:8]}: timestamp {row_dict['start_timestamp']:.2f}s > {max_timestamp:.2f}s")
                continue

            # 3. Apply Spatial ROI (Memory Region) Filter
            if region:
                bboxes = []
                if row_dict.get("bounding_boxes"):
                    try: bboxes = json.loads(row_dict["bounding_boxes"])
                    except: bboxes = []
                if not check_bbox_region_overlap(bboxes, region):
                    if len(rejected_candidates) < 15:
                        rejected_candidates.append(f"Event {event_id[:8]}: trajectory does not intersect region")
                    continue

            # 4. STRICT GROUNDING & POSITIVE/NEGATIVE ATTRIBUTE VERIFICATION
            is_valid, reason = is_candidate_grounded(
                concept=row_dict["concept"],
                attributes=attrs,
                required_object=required_object,
                required_color=required_color,
                required_upper_color=required_upper_color,
                required_lower_color=required_lower_color,
                required_action=required_action,
                requires_bag=requires_bag,
                forbid_bag=forbid_bag,
                required_accessories=required_accessories,
                forbidden_accessories=forbidden_accessories,
                forbidden_objects=forbidden_objects
            )
            
            if not is_valid:
                if len(rejected_candidates) < 15:
                    rejected_candidates.append(f"Event {event_id[:8]} (Track #{row_dict.get('tracking_id')}, {row_dict['concept']}): {reason}")
                continue

            color_conf = float(attrs.get("color_confidence") or 0.0) if required_color else 0.0
            upper_conf = float(attrs.get("upper_color_conf") or 0.0) if required_upper_color else 0.0
            rank_score = (r["similarity"] * 0.35) + (max(color_conf, upper_conf) * 0.65)

            # Exact concept match boost (e.g. if user asks for car, true car ranks above truck)
            if required_object and row_dict["concept"].lower() == required_object.lower():
                rank_score += 1.0
            if required_action == "moving" and attrs.get("is_moving"):
                rank_score += 0.4

            from backend.app.core.evidence import format_time_12h
            cand_item = {
                "event": event_id,
                "camera": row_dict["camera_id"],
                "dataset_id": row_dict.get("dataset_id"),
                "tracking_id": row_dict.get("tracking_id"),
                "timestamp": row_dict["start_timestamp"],
                "time_formatted": format_time_12h(row_dict["start_timestamp"]),
                "end_timestamp": row_dict["end_timestamp"],
                "similarity": r["similarity"],
                "concept": row_dict["concept"],
                "attributes": attrs,
                "frame": row_dict["representative_frame"],
                "_rank_score": rank_score
            }
            detailed_results.append(cand_item)
            accepted_candidates.append(f"Track #{row_dict.get('tracking_id')} at {row_dict['start_timestamp']:.2f}s ({row_dict['concept']})")
            
            if len(detailed_results) >= max(top_k * 5, 50):
                break

        # Prioritize candidates with highest verified attribute confidence
        detailed_results.sort(key=lambda x: x.get("_rank_score", 0), reverse=True)

        # Deduplicate by unique (camera, tracking_id) so all distinct vehicle tracks appear in results
        unique_results = []
        seen_tracks = set()
        for item in detailed_results:
            trk_key = (item["camera"], str(item.get("tracking_id", "")))
            if trk_key not in seen_tracks or not item.get("tracking_id"):
                seen_tracks.add(trk_key)
                unique_results.append(item)

        if len(unique_results) < top_k:
            for item in detailed_results:
                if item not in unique_results:
                    unique_results.append(item)
                if len(unique_results) >= top_k:
                    break

        final_results = unique_results[:top_k]
        for item in final_results:
            item.pop("_rank_score", None)

        debug_trace = {
            "candidates_checked": candidates_checked,
            "rejected_candidates": rejected_candidates,
            "accepted_candidates": accepted_candidates
        }

        if return_debug:
            return final_results, debug_trace
        return final_results

    def query_count(
        self,
        required_object: Optional[str] = None,
        required_color: Optional[str] = None,
        required_upper_color: Optional[str] = None,
        required_lower_color: Optional[str] = None,
        required_action: Optional[str] = None,
        requires_bag: Optional[bool] = None,
        forbid_bag: Optional[bool] = None,
        required_accessories: Optional[List[str]] = None,
        forbidden_accessories: Optional[List[str]] = None,
        forbidden_objects: Optional[List[str]] = None,
        camera_id: Optional[str] = None,
        min_timestamp: Optional[float] = None,
        max_timestamp: Optional[float] = None,
        region: Optional[list] = None
    ) -> Dict[str, Any]:
        """
        Executes a real COUNT query returning distinct tracking IDs satisfying the constraints.
        """
        from backend.app.core.memory import check_bbox_region_overlap
        
        query_sql = "SELECT id, camera_id, dataset_id, tracking_id, start_timestamp, end_timestamp, concept, bounding_boxes, representative_frame, attributes FROM events WHERE 1=1"
        params = []
        if camera_id:
            query_sql += " AND camera_id = ?"
            params.append(camera_id)
            
        rows = execute_query(query_sql, tuple(params), fetch=True)
        
        total_frame_detections = 0
        matching_tracks = set()
        matching_events = []
        
        target_dataset_id = None
        target_video_path = None
        if camera_id:
            cam_row = execute_query("SELECT dataset_id, video_path FROM cameras WHERE id = ?", (camera_id,), fetch=True, fetch_one=True)
            if cam_row:
                target_dataset_id = cam_row["dataset_id"]
                target_video_path = cam_row["video_path"]

        for r in rows:
            row_dict = dict(r)
            is_valid_lifecycle, _ = validate_candidate_event(row_dict, camera_id)
            if not is_valid_lifecycle:
                continue

            attrs = {}
            if row_dict.get("attributes"):
                try: attrs = json.loads(row_dict["attributes"])
                except: pass
                
            # Time filter
            if min_timestamp is not None and row_dict["end_timestamp"] < min_timestamp:
                continue
            if max_timestamp is not None and row_dict["start_timestamp"] > max_timestamp:
                continue
                
            bboxes = []
            if row_dict.get("bounding_boxes"):
                try: bboxes = json.loads(row_dict["bounding_boxes"])
                except: bboxes = []

            # Spatial ROI (Region / Gate Crossing filter)
            if region:
                if not check_bbox_region_overlap(bboxes, region):
                    continue
                    
            # Grounding check
            is_valid, _ = is_candidate_grounded(
                concept=row_dict["concept"],
                attributes=attrs,
                required_object=required_object,
                required_color=required_color,
                required_upper_color=required_upper_color,
                required_lower_color=required_lower_color,
                required_action=required_action,
                requires_bag=requires_bag,
                forbid_bag=forbid_bag,
                required_accessories=required_accessories,
                forbidden_accessories=forbidden_accessories,
                forbidden_objects=forbidden_objects
            )
            
            if is_valid:
                tid = str(row_dict.get("tracking_id") or row_dict["id"])
                matching_tracks.add(tid)
                total_frame_detections += max(len(bboxes), 1)
                matching_events.append({
                    "event": row_dict["id"],
                    "camera": row_dict["camera_id"],
                    "dataset_id": row_dict.get("dataset_id"),
                    "tracking_id": tid,
                    "timestamp": row_dict["start_timestamp"],
                    "end_timestamp": row_dict["end_timestamp"],
                    "concept": row_dict["concept"],
                    "attributes": attrs,
                    "frame": row_dict["representative_frame"]
                })
                
        sorted_tids = sorted(list(matching_tracks), key=lambda x: int(x) if x.isdigit() else x)
        
        return {
            "count": len(sorted_tids),
            "total_detections": total_frame_detections,
            "unique_tracking_ids": sorted_tids,
            "dataset_id": target_dataset_id,
            "video_path": target_video_path,
            "events": matching_events
        }

_vector_search_instance = None
def get_vector_search():
    global _vector_search_instance
    if _vector_search_instance is None:
        _vector_search_instance = VectorSearch()
    return _vector_search_instance
