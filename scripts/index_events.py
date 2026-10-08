import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.app.db.database import execute_query
from backend.app.core.embeddings import get_embedder
from backend.app.core.vector_search import get_vector_search
import time

def index_existing_events():
    print("Loading embedding model...")
    embedder = get_embedder()
    if not embedder.available:
        print("Embedder not available. Exiting.")
        return
        
    print("Initializing vector search...")
    vector_search = get_vector_search()
    
    rows = execute_query("SELECT id, representative_frame, concept FROM events WHERE embedding_ref IS NULL", fetch=True)
    if not rows:
        print("No unindexed events found.")
        return
        
    print(f"Found {len(rows)} events to index.")
    
    for row in rows:
        event_id = row["id"]
        frame_path = row["representative_frame"]
        concept = row["concept"]
        
        if not os.path.exists(frame_path):
            print(f"Warning: Frame {frame_path} not found for event {event_id}. Skipping.")
            continue
            
        print(f"Embedding event {event_id} ({concept})...")
        t0 = time.time()
        vector = embedder.embed_image(frame_path)
        t1 = time.time()
        
        if vector is not None:
            vector_search.add_vector(event_id, vector)
            print(f"  Indexed in {t1 - t0:.3f}s")
        else:
            print(f"  Failed to embed.")
            
    vector_search.save()
    print("Indexing complete. Saved vector index.")

if __name__ == "__main__":
    index_existing_events()
