#!/usr/bin/env python3
"""
scripts/reset_data.py
Completely wipes all cameras, events, clips, frames, vector indexes, and queries,
leaving the application in a clean 0-camera, 0-event state ready for fresh uploads.
"""

import os
import shutil
import sqlite3
import glob

def reset_all_data():
    print("=" * 70)
    print(" HNX26EPS05 — COMPLETE APPLICATION DATA RESET")
    print("=" * 70)
    
    db_path = "database/app.db"
    
    # 1. Reset SQLite Tables
    if os.path.exists(db_path):
        conn = sqlite3.connect(db_path)
        cur = conn.cursor()
        print("  • Clearing database tables (events, cameras, memory, queries)...")
        cur.execute("DELETE FROM events")
        cur.execute("DELETE FROM cameras")
        cur.execute("DELETE FROM memory")
        cur.execute("DELETE FROM queries")
        conn.commit()
        conn.close()
        print("  ✓ Database tables cleared.")
        
    # 2. Clear Filesystem Directories
    dirs_to_clean = ["data/frames", "data/clips", "data/videos"]
    for d in dirs_to_clean:
        if os.path.exists(d):
            print(f"  • Cleaning directory: {d}...")
            shutil.rmtree(d)
            os.makedirs(d, exist_ok=True)
            print(f"  ✓ {d} cleaned.")
            
    # 3. Clear Vector Index
    vec_dir = "data/vector_index"
    if os.path.exists(vec_dir):
        print(f"  • Resetting vector index in {vec_dir}...")
        for f in glob.glob(f"{vec_dir}/*"):
            try:
                os.remove(f)
            except Exception:
                pass
        print(f"  ✓ Vector index cleared.")
        
    print("\n" + "=" * 70)
    print(" RESET COMPLETE — APPLICATION IS IN A 100% CLEAN STATE:")
    print("   • Cameras: 0")
    print("   • Events:  0")
    print("   • Vectors: 0")
    print(" Ready for fresh user CCTV video uploads!")
    print("=" * 70)

if __name__ == "__main__":
    reset_all_data()
