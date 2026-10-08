# System Architecture — HNX26EPS05

## Overview
**HNX26EPS05** is an edge-native, multi-stream video intelligence engine that ingests recorded CCTV footage, extracts open-vocabulary visual tracks, indexes temporal events into a vector search space, and answers conversational natural-language queries grounded directly in video and frame evidence.

```
+---------------------------------------------------------------------------------------+
|                                    USER INTERFACE                                     |
|           Dark-Mode Dashboard: Video Streamer • Canvas HUD • Conversational Chat     |
+---------------------------------------------------------------------------------------+
                                        ▲      |
                   Evidence Clip & Meta |      | Natural-Language Query
                                        |      ▼
+---------------------------------------------------------------------------------------+
|                                 FASTAPI API BACKEND                                   |
|   /api/health • /api/cameras • /api/process • /api/events • /api/query • /api/memory |
+---------------------------------------------------------------------------------------+
        |                     |                     |                     |
        ▼                     ▼                     ▼                     ▼
+---------------+     +---------------+     +---------------+     +---------------+
| QUERY ENGINE  |     | EMBEDDINGS    |     | VECTOR INDEX  |     | EVIDENCE GEN  |
| Deterministic |     | OpenAI CLIP   |     | FAISS FlatIP  |     | FFmpeg Lazy   |
| NLP & Memory  |     | ViT-B/16      |     | (512-dim)     |     | FastStart MP4 |
| Resolution    |     | (MPS / CPU)   |     | Top-K Search  |     | H.264 / AAC   |
+---------------+     +---------------+     +---------------+     +---------------+
        ▲                     ▲                     ▲                     ▲
        |                     |                     |                     |
+---------------------------------------------------------------------------------------+
|                                  SQLITE DATABASE LAYER                                |
|          Tables: cameras • events • memory (Clarify-Once) • queries                   |
+---------------------------------------------------------------------------------------+
        ▲
        | Frame Tracks & Metadata
+---------------------------------------------------------------------------------------+
|                               INGESTION & DETECTION PIPELINE                          |
|         OpenCV Video Streamer ➔ YOLO-World Detector ➔ ByteTrack Object Tracker        |
+---------------------------------------------------------------------------------------+
```

---

## Component Deep Dive

### 1. Ingestion, Open-Vocabulary Detection & Tracking
* **Video Ingestion** (`backend/app/core/video.py`):
  * Reads multi-camera MP4 video streams (`camera_01`, `camera_02`, etc.) using OpenCV.
  * Dynamically extracts video metadata (FPS, frame count, resolution, duration).
  * Implements configurable frame sampling to balance tracking fidelity and compute.
* **Open-Vocabulary Detection** (`backend/app/core/detector.py`):
  * Employs pretrained **YOLO-World** (`yolov8s-world.pt`) executing on Apple Silicon MPS or CPU.
  * Detects arbitrary open-vocabulary prompts (e.g., `person`, `red car`, `motorcycle`, `white vehicle`, `person carrying a large bag`, `backpack`).
* **Temporal Tracking**:
  * Integrates **ByteTrack** to maintain track IDs across frames and temporal gaps.
  * Groups consecutive frame detections into continuous event tracks (`start_timestamp` to `end_timestamp`).

### 2. Embeddings & Vector Search Engine
* **Visual & Text Embeddings** (`backend/app/core/embeddings.py`):
  * Uses **OpenAI CLIP ViT-B/16** (`openai/clip-vit-base-patch16`).
  * Normalizes 512-dimensional feature representations for cross-modal similarity.
* **Vector Search Engine** (`backend/app/core/vector_search.py`):
  * Built on **FAISS** `IndexFlatIP` (inner product over normalized embeddings = cosine similarity).
  * Automatically handles post-filtering across Camera IDs, Temporal Windows (`min_timestamp`, `max_timestamp`), and Spatial Region-of-Interest (ROI) bounds.

### 3. Natural-Language Query Engine & Clarify-Once Memory
* **Deterministic NLP Parser** (`backend/app/core/query_parser.py`):
  * Dissects free-form natural language queries into:
    * `visual_concept` (stripped of conversational filler words).
    * `color` (`red`, `white`, `black`, etc.).
    * `object` (`car`, `vehicle`, `person`, `motorcycle`, `bag`).
    * `camera` (`camera 1`, `camera 02`, `Gate Camera`).
    * `time_range` (`last minute`, `last 5 minutes`, `last hour`, `first 10 seconds`, `today`, `yesterday`).
* **Clarify-Once Memory System** (`backend/app/core/memory.py`):
  * Detects unknown location references (e.g. `main gate`, `warehouse dock`).
  * Triggers an interactive clarification payload prompting the user to assign a camera and ROI.
  * Stores permanent mappings in SQLite (`memory` table) that persist across restarts.
  * Intersects event bounding boxes with user-defined ROI polygons during subsequent searches.

### 4. Grounded Evidence & Media Streaming
* **Lazy Clip Generation** (`backend/app/core/evidence.py`):
  * On-demand sub-clip extraction around event windows using FFmpeg (`-c:v libx264 -preset ultrafast -movflags +faststart`).
  * Cached after the first request (~16 ms retrieval latency).
* **Grounded Verdict Response**:
  * Returns formatted verdicts (`YES`/`NO`), detection summaries, camera names, CCTV wall-clock timestamps, video offsets, and confidence percentages.
