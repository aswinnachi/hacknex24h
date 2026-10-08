# Multi-Stream Video Intelligence — Project File Index & Component Map

This document maps all files, directories, and modules in this repository to their responsibilities, dependencies, inputs, and outputs.

---

## 📂 Root Directory

| File / Directory | Purpose | Key Details |
| :--- | :--- | :--- |
| [`requirements.txt`](file:///Users/aswinnachi/Desktop/hacknex_ps5/multi-stream-video-intelligence/requirements.txt) | Python dependencies | Version-constrained packages for FastAPI, PyTorch, YOLO-World, OpenCV, FAISS, Transformers. |
| [`requirements-lock.txt`](file:///Users/aswinnachi/Desktop/hacknex_ps5/multi-stream-video-intelligence/requirements-lock.txt) | Pinned dependency snapshot | Complete pip freeze of tested, working environment. |
| [`environment.yml`](file:///Users/aswinnachi/Desktop/hacknex_ps5/multi-stream-video-intelligence/environment.yml) | Conda / Mamba environment file | Sets up Python 3.9 environment with PyTorch and packages. |
| [`setup.sh`](file:///Users/aswinnachi/Desktop/hacknex_ps5/multi-stream-video-intelligence/setup.sh) | Automated batch setup script | Creates venv, installs dependencies, verifies GPU/MPS, downloads weights, checks DB. |
| [`run.sh`](file:///Users/aswinnachi/Desktop/hacknex_ps5/multi-stream-video-intelligence/run.sh) | Server launch script | Starts FastAPI application via Uvicorn on port 8000. |
| [`AI_PROJECT_CONTEXT.md`](file:///Users/aswinnachi/Desktop/hacknex_ps5/multi-stream-video-intelligence/AI_PROJECT_CONTEXT.md) | AI Developer Dossier | Comprehensive technical guide for LLMs and autonomous agents detailing pipelines, algorithms, and models. |
| [`PROJECT_MAP.md`](file:///Users/aswinnachi/Desktop/hacknex_ps5/multi-stream-video-intelligence/PROJECT_MAP.md) | File directory map (this file) | Explains the role and contents of every file in the project. |
| [`README.md`](file:///Users/aswinnachi/Desktop/hacknex_ps5/multi-stream-video-intelligence/README.md) | User-facing documentation | Quickstart instructions, feature overview, and project presentation. |
| [`yolov8s-world.pt`](file:///Users/aswinnachi/Desktop/hacknex_ps5/multi-stream-video-intelligence/yolov8s-world.pt) | YOLO-World model weights | 27MB base weights for open-vocabulary zero-shot object detection. |

---

## 📂 `backend/app/` — Application Backend

### Core Engine (`backend/app/core/`)
| File | Responsibility |
| :--- | :--- |
| [`attributes.py`](file:///Users/aswinnachi/Desktop/hacknex_ps5/multi-stream-video-intelligence/backend/app/core/attributes.py) | **Visual Attribute Engine**: Implements dual-space HSV/RGB vehicle body color classification (anti-taillight rules, sky glare rejection), clothing upper/lower torso/leg analysis, accessory detection (bags, backpacks, umbrellas, phones), and trajectory movement calculation. |
| [`detector.py`](file:///Users/aswinnachi/Desktop/hacknex_ps5/multi-stream-video-intelligence/backend/app/core/detector.py) | **YOLO-World Detector Wrapper**: Wraps Ultralytics `YOLOWorld`, manages hardware device (`mps`/`cuda`/`cpu`), dynamic vocabulary configuration, and track persistence. |
| [`cctv_tracker.yaml`](file:///Users/aswinnachi/Desktop/hacknex_ps5/multi-stream-video-intelligence/backend/app/core/cctv_tracker.yaml) | **ByteTrack Configuration**: Tuned thresholds (`track_high_thresh: 0.35`, `match_thresh: 0.70`, `track_buffer: 90`) to prevent track fragmentation on highway traffic. |
| [`evidence.py`](file:///Users/aswinnachi/Desktop/hacknex_ps5/multi-stream-video-intelligence/backend/app/core/evidence.py) | **Evidence Clip & Dense Tracking**: Generates lazy 4–6s MP4 clips via FFmpeg, executes `compute_dense_clip_scene` to produce ground-truth per-frame bounding boxes for all visible objects, and formats evidence payloads. |
| [`query_parser.py`](file:///Users/aswinnachi/Desktop/hacknex_ps5/multi-stream-video-intelligence/backend/app/core/query_parser.py) | **Deterministic Intent Parser**: Regex/rule-based NLP parser extracting query intents (`SEARCH`, `COUNT`, `YES_NO`, `INVENTORY`), object concepts, colors, clothing, actions, accessories, and memory landmarks. |
| [`vector_search.py`](file:///Users/aswinnachi/Desktop/hacknex_ps5/multi-stream-video-intelligence/backend/app/core/vector_search.py) | **Vector Search & Strict Grounding**: FAISS IndexFlatIP cosine similarity retrieval supplemented with `is_candidate_grounded` to reject semantic hallucinations and enforce attribute constraints. |
| [`embeddings.py`](file:///Users/aswinnachi/Desktop/hacknex_ps5/multi-stream-video-intelligence/backend/app/core/embeddings.py) | **Multimodal Embedder**: Loads Google SigLIP (`google/siglip-base-patch16-224`, 768-dim) or OpenAI CLIP (`openai/clip-vit-base-patch16`, 512-dim) to produce normalized text and image embeddings. |
| [`video.py`](file:///Users/aswinnachi/Desktop/hacknex_ps5/multi-stream-video-intelligence/backend/app/core/video.py) | **Video Processing Pipeline**: Handles asynchronous video ingestion, frame decoding, cadence sampling @ 1 FPS, ByteTrack tracking, multi-frame attribute consensus, and database event insertion. |
| [`hardware.py`](file:///Users/aswinnachi/Desktop/hacknex_ps5/multi-stream-video-intelligence/backend/app/core/hardware.py) | **Hardware Device Detection**: Detects Apple Silicon MPS, NVIDIA CUDA, or CPU fallbacks and system memory. |
| [`config.py`](file:///Users/aswinnachi/Desktop/hacknex_ps5/multi-stream-video-intelligence/backend/app/core/config.py) | **Configuration Settings**: Pydantic BaseSettings loading environment variables. |
| [`logging.py`](file:///Users/aswinnachi/Desktop/hacknex_ps5/multi-stream-video-intelligence/backend/app/core/logging.py) | **Logging Utility**: Centralized colorized logging handler. |

### API Endpoints (`backend/app/api/`)
| File | Responsibility |
| :--- | :--- |
| [`query.py`](file:///Users/aswinnachi/Desktop/hacknex_ps5/multi-stream-video-intelligence/backend/app/api/query.py) | `POST /api/query`: Primary natural language intelligence endpoint. Orchestrates parsing, memory resolution, counting, vector search, and verdict summaries. |
| [`evidence.py`](file:///Users/aswinnachi/Desktop/hacknex_ps5/multi-stream-video-intelligence/backend/app/api/evidence.py) | `GET /api/evidence/{event_id}`: Serves target visual evidence metadata, video clip URL, and dense multi-frame tracking arrays. |
| [`cameras.py`](file:///Users/aswinnachi/Desktop/hacknex_ps5/multi-stream-video-intelligence/backend/app/api/cameras.py) | `POST /api/cameras/upload`: Video upload and analysis trigger. `GET /api/cameras`: Camera status and progress monitoring. |
| [`search.py`](file:///Users/aswinnachi/Desktop/hacknex_ps5/multi-stream-video-intelligence/backend/app/api/search.py) | `GET /api/search`: Raw multimodal text-to-image semantic vector search. |
| [`events.py`](file:///Users/aswinnachi/Desktop/hacknex_ps5/multi-stream-video-intelligence/backend/app/api/events.py) | `GET /api/events`: Event log retrieval and timeline browsing. |
| [`memory.py`](file:///Users/aswinnachi/Desktop/hacknex_ps5/multi-stream-video-intelligence/backend/app/api/memory.py) | `POST /api/memory`: Clarify-Once persistent spatial landmark memory configuration (`"main gate"` $\rightarrow$ Camera 02 $\rightarrow$ ROI coordinates). |
| [`system.py`](file:///Users/aswinnachi/Desktop/hacknex_ps5/multi-stream-video-intelligence/backend/app/api/system.py) | Hardware status and application state wipe/reset endpoint (`POST /api/system/reset`). |

### Database Layer (`backend/app/db/`)
| File | Responsibility |
| :--- | :--- |
| [`database.py`](file:///Users/aswinnachi/Desktop/hacknex_ps5/multi-stream-video-intelligence/backend/app/db/database.py) | SQLite connection pool, schema initialization, and transactional query execution (`cctv.db`). |

---

## 📂 `frontend/` — Web Application Interface

| File | Responsibility |
| :--- | :--- |
| [`index.html`](file:///Users/aswinnachi/Desktop/hacknex_ps5/multi-stream-video-intelligence/frontend/index.html) | Semantic HTML5 structure: camera streams sidebar, conversational query chat, visual evidence player viewport, event timeline, and memory modal. |
| [`app.js`](file:///Users/aswinnachi/Desktop/hacknex_ps5/multi-stream-video-intelligence/frontend/app.js) | Complete client logic: API communications, chat UI rendering, canvas HUD overlay with glowing neon reticles, per-frame coordinate interpolation, and Query Matches Only filtering. |
| [`styles.css`](file:///Users/aswinnachi/Desktop/hacknex_ps5/multi-stream-video-intelligence/frontend/styles.css) | Dark-mode design system: glassmorphism, HUD styling, custom typography, status badges, and responsive layouts. |

---

## 📂 `data/` & `database/` — Data Storage

| Directory | Purpose |
| :--- | :--- |
| `data/videos/` | Stored source CCTV MP4 video streams organized by camera ID. |
| `data/clips/` | Dynamically generated 4–6s MP4 visual evidence clips and cached `.scene.json` trajectories. |
| `data/frames/` | Representative JPEG frames for detected events and tracks. |
| `database/cctv.db` | SQLite database storing cameras, events, queries, and memories. |
| `database/indexes/` | FAISS index binary (`faiss.index`) and ID mapping (`mapping.json`). |

---

## 📂 `scripts/` — Auditing, Indexing & Diagnostics

| File | Purpose |
| :--- | :--- |
| `test_master_suite.py` | Full integration benchmark testing search, count, negative rejection, and visual attributes. |
| `test_negative_constraints.py` | Strict rejection test suite validating zero false-positive matches for absent objects. |
| `diagnose_models_and_pipeline.py` | Forensic diagnostic tool verifying detector, embedder, and vector search health. |
| `index_events.py` | Batch indexing utility for video datasets. |
| `create_test_video.py` | Synthetic CCTV test video generator with defined visual events. |
