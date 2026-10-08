import os
import sys
import time
import json
import numpy as np
import cv2

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

def test_model_yolo_world():
    print("\n--- [TEST 1] YOLO-World Open-Vocabulary Detector ---")
    try:
        from backend.app.core.detector import OpenVocabularyDetector
        t0 = time.time()
        detector = OpenVocabularyDetector()
        print(f"YOLO-World initialized in {time.time() - t0:.2f}s, device={detector.device}, available={detector.available}")
        if not detector.available:
            return False, "YOLO-World marked as not available"

        # Create a synthetic image or use an existing frame
        test_frame = np.full((360, 640, 3), 128, dtype=np.uint8)
        # Draw a white rectangle simulating a vehicle
        cv2.rectangle(test_frame, (100, 100), (300, 250), (255, 255, 255), -1)

        classes = ["car", "person", "bus", "truck"]
        dets = detector.track_frame(test_frame, classes, conf_threshold=0.1)
        print(f"Inference succeeded on synthetic frame. Detections returned: {len(dets)}")
        return True, "YOLO-World functional"
    except Exception as e:
        return False, f"YOLO-World error: {e}"

def test_model_bytetrack():
    print("\n--- [TEST 2] ByteTrack Multi-Object Tracker ---")
    try:
        from backend.app.core.detector import OpenVocabularyDetector
        detector = OpenVocabularyDetector()
        test_frame = np.full((360, 640, 3), 128, dtype=np.uint8)
        cv2.rectangle(test_frame, (100, 100), (300, 250), (255, 255, 255), -1)

        # Track across 3 sequential frames
        classes = ["car", "bus"]
        track_ids = []
        for i in range(3):
            # shift box slightly
            f = test_frame.copy()
            cv2.rectangle(f, (100 + i * 5, 100), (300 + i * 5, 250), (255, 255, 255), -1)
            dets = detector.track_frame(f, classes, conf_threshold=0.1)
            if dets and dets[0].get("track_id") is not None:
                track_ids.append(dets[0]["track_id"])

        print(f"ByteTrack sequence executed. Track IDs tracked: {track_ids}")
        return True, "ByteTrack functional"
    except Exception as e:
        return False, f"ByteTrack error: {e}"

def test_model_clip_embedder():
    print("\n--- [TEST 3] CLIP / Embedder Model ---")
    try:
        from backend.app.core.embeddings import get_embedder
        t0 = time.time()
        embedder = get_embedder()
        print(f"Embedder initialized in {time.time() - t0:.2f}s, device={embedder.device}, available={embedder.available}")
        if not embedder.available:
            return False, "Embedder marked as unavailable"

        text_vec = embedder.embed_text("a red car")
        if text_vec is None or len(text_vec) != 512:
            return False, f"Text embedding invalid shape: {getattr(text_vec, 'shape', None)}"

        # Test image embedder with dummy image
        dummy_img_path = "data/test_clip_dummy.jpg"
        os.makedirs("data", exist_ok=True)
        dummy_img = np.full((224, 224, 3), 200, dtype=np.uint8)
        cv2.imwrite(dummy_img_path, dummy_img)

        img_vec = embedder.embed_image(dummy_img_path)
        if os.path.exists(dummy_img_path):
            os.remove(dummy_img_path)

        if img_vec is None or len(img_vec) != 512:
            return False, f"Image embedding invalid shape: {getattr(img_vec, 'shape', None)}"

        sim = float(np.dot(text_vec, img_vec))
        print(f"Embeddings generated successfully. Cosine similarity = {sim:.4f}")
        return True, "CLIP Embedder functional"
    except Exception as e:
        return False, f"CLIP Embedder error: {e}"

def test_vector_search_faiss():
    print("\n--- [TEST 4] FAISS Vector Search Index ---")
    try:
        from backend.app.core.vector_search import get_vector_search
        vs = get_vector_search()
        ntotal = vs.index.ntotal if hasattr(vs.index, "ntotal") else len(vs.vectors)
        mapping_count = len(vs.id_to_event)
        print(f"Vector search loaded. Total indexed vectors: {ntotal}, Mapped events: {mapping_count}")
        return True, f"Vector Search functional (ntotal={ntotal}, mapped={mapping_count})"
    except Exception as e:
        return False, f"Vector Search error: {e}"

def test_database():
    print("\n--- [TEST 5] SQLite Database ---")
    try:
        from backend.app.db.database import execute_query
        cams = execute_query("SELECT id, name, status, dataset_id FROM cameras", fetch=True)
        events_cnt = execute_query("SELECT COUNT(*) as c FROM events", fetch=True, fetch_one=True)
        print(f"Cameras registered: {len(cams) if cams else 0}")
        print(f"Total events in DB: {events_cnt['c'] if events_cnt else 0}")
        return True, "Database functional"
    except Exception as e:
        return False, f"Database error: {e}"

def test_evidence_generation():
    print("\n--- [TEST 6] Evidence Video Generator ---")
    try:
        from backend.app.core.evidence import generate_evidence_clip
        from backend.app.db.database import execute_query
        
        # Check an existing event
        event = execute_query("SELECT id FROM events LIMIT 1", fetch=True, fetch_one=True)
        if not event:
            return False, "No events found to test evidence clip"

        clip_data = generate_evidence_clip(event["id"])
        if clip_data and clip_data.get("clip_path") and os.path.exists(clip_data["clip_path"]):
            size = os.path.getsize(clip_data["clip_path"])
            print(f"Generated/loaded clip successfully: {clip_data['clip_path']} ({size} bytes)")
            return True, f"Evidence clip generator functional (clip size={size} bytes)"
        else:
            return False, "Failed to generate clip file"
    except Exception as e:
        return False, f"Evidence clip error: {e}"

def main():
    print("=" * 70)
    print("RUNNING COMPLETE MODEL & PIPELINE DIAGNOSTIC AUDIT")
    print("=" * 70)

    tests = [
        ("YOLO-World Detector", test_model_yolo_world),
        ("ByteTrack Tracker", test_model_bytetrack),
        ("CLIP / SigLIP Embedder", test_model_clip_embedder),
        ("FAISS / Vector Search", test_vector_search_faiss),
        ("Database / SQLite", test_database),
        ("Evidence Video Clip Generation", test_evidence_generation),
    ]

    summary = []
    for name, fn in tests:
        ok, msg = fn()
        status_str = "PASS" if ok else "FAIL"
        summary.append((name, status_str, msg))

    print("\n" + "=" * 70)
    print("DIAGNOSTIC SUMMARY:")
    print("=" * 70)
    for name, status_str, msg in summary:
        print(f"[{status_str}] {name}: {msg}")

if __name__ == "__main__":
    main()
