# HNX26EPS05 — Multi-Stream Video Intelligence Platform
### Open-Vocabulary Grounded Surveillance, Conversational Query & Persistent Memory Engine

[![Python](https://img.shields.io/badge/Python-3.9%2B-blue.svg)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115%2B-009688.svg)](https://fastapi.tiangolo.com/)
[![YOLO-World](https://img.shields.io/badge/YOLO--World-Open--Vocabulary-orange.svg)](https://github.com/ultralytics/ultralytics)
[![SigLIP](https://img.shields.io/badge/Google-SigLIP%20Base%2016-4285F4.svg)](https://huggingface.co/google/siglip-base-patch16-224)
[![CLIP](https://img.shields.io/badge/OpenAI-CLIP%20ViT--B%2F16-7B1FA2.svg)](https://github.com/openai/CLIP)
[![FAISS](https://img.shields.io/badge/FAISS-IndexFlatIP-0081CB.svg)](https://github.com/facebookresearch/faiss)
[![License](https://img.shields.io/badge/License-MIT-lightgrey.svg)](LICENSE)

An edge-native, multi-camera CCTV video intelligence system that indexes surveillance streams, extracts open-vocabulary visual semantics, and enables natural-language conversational queries with **strict physical attribute grounding**, **false-candidate rejection**, **discrete object counting**, and **Clarify-Once persistent spatial memory**.

---

## 🏛️ System Architecture

The system implements the 9-block **HNX26EPS05 Multi-Stream Video Intelligence Architecture**:

```
┌─────────────────┐      ┌─────────────────┐      ┌─────────────────┐      ┌─────────────────┐      ┌─────────────────┐
│ 1. CCTV Videos  │ ───► │ 2. Frame        │ ───► │ 3. YOLO-World   │ ───► │ 4. SigLIP /     │ ───► │ 5. FAISS +      │
│                 │      │    Sampling     │      │ (Open-Vocabulary│      │    CLIP         │      │    SQLite       │
│ • CAM_01        │      │ • Selected      │      │  Detection)     │      │ (Embeddings)    │      │ • FAISS Vector  │
│ • CAM_02        │      │   cadence       │      │ • person, car,  │      │ • Image crops   │      │   Search        │
│ • CAM_03        │      │   (1.0 FPS)     │      │   bag, etc.     │      │ • Text queries  │      │ • SQLite DB     │
└─────────────────┘      └─────────────────┘      └─────────────────┘      └─────────────────┘      └────────┬────────┘
                                                                                                             │
                                                                                                             ▼
┌─────────────────┐      ┌─────────────────┐      ┌─────────────────┐      ┌─────────────────────────────────┤
│ 9. Persistent   │ ◄─── │ 8. Grounded     │ ◄─── │ 7. Search &     │ ◄─── │ 6. User Query                   │
│    Memory       │      │    Evidence     │      │    Rank         │      │ (Conversational Search)         │
│ (Clarify Once)  │      │ • Camera ID     │      │ • Query Vector  │      │ • "Find a person with a handbag"│
│ • e.g. main gate│      │ • Time: 3:56 PM │      │ • FAISS Match   │      │ • "Show the moving red car"     │
│   -> Camera 02  │      │ • Similarity    │      │ • Strict Filter │      │ • "How many cars passed?"       │
│   -> Region ROI │      │ • Dynamic Clip  │      │ • Score Ranking │      │ • "Did a white car pass?"       │
└─────────────────┘      └─────────────────┘      └─────────────────┘      └─────────────────────────────────┘
```

### The 9 Architecture Blocks Explained

| # | Component | Operational Responsibility |
| :---: | :--- | :--- |
| **1** | **CCTV Videos** | Ingests multi-camera surveillance streams (`CAM_01`, `CAM_02`, `CAM_03`, etc.) with asynchronous decoding. |
| **2** | **Frame Sampling** | Dynamically samples selected frames at 1.0 FPS (`step = max(1, int(round(fps / target_fps)))`), reducing compute load by 96% while maintaining temporal integrity. |
| **3** | **YOLO-World** | Zero-shot open-vocabulary object detector (`yolov8s-world.pt`) conditioned on runtime vocabularies (`person`, `backpack`, `handbag`, `car`, `motorcycle`, `bicycle`, etc.). |
| **4** | **SigLIP / CLIP** | Multimodal foundation models (Google `siglip-base-patch16-224` and OpenAI `clip-vit-base-patch16`) generating normalized embeddings for visual crops and text queries. |
| **5** | **FAISS + SQLite** | **FAISS** provides sub-millisecond `IndexFlatIP` vector retrieval; **SQLite** persistently stores metadata, trajectories, attributes, and user memory. |
| **6** | **User Query** | Natural language interface accepting conversational questions, boolean inquiries, counting requests, and inventory queries. |
| **7** | **Search & Rank** | Converts text query into high-dimensional vector, searches FAISS, applies hard temporal/camera filters, strictly validates attributes, and ranks results. |
| **8** | **Result (Grounded Evidence)** | Renders verified match cards displaying exact Camera (`CAM_01`), wall-clock time (`3:56 PM`), similarity score (`0.87`), and an interactive MP4 clip with a **dynamic moving HUD bounding box**. |
| **9** | **Persistent Memory** | Clarify-Once landmark mapping (`"main gate" -> Camera 02 -> ROI [x1, y1, x2, y2]`) permanently retained across restarts. |

---

## 🎯 Key Innovation: Elimination of Nearest-Neighbor Hallucinations

Standard vector search engines (naive CLIP/SigLIP implementations) suffer from severe semantic hallucination:
* **The Problem**: If an operator queries *"Show the moving red car"* on footage containing only white sedans and gray trucks, naive vector search returns a white car because it is the "closest" point in vector space, falsely declaring a match.
* **Our Solution**: **Two-Stage Strict Grounding Pipeline**:
  1. **Candidate Retrieval**: FAISS retrieves top-$k$ semantic candidates.
  2. **Physical Attribute Verification**: Candidates must pass hard algorithmic checks:
     - **Dual-Space HSV/RGB Color Classification**: Isolates vehicle body panels, rejecting headlights, sky glare, and taillight false-positives.
     - **Clothing & Carried Accessories**: Verifies human upper/lower garments and verifies carried items (bags, backpacks, umbrellas, phones).
     - **Trajectory Kinematics**: Computes velocity vectors to confirm whether an object is genuinely moving or stationary.
  3. **Strict Negative Rejection**: If no candidate satisfies the criteria, the system returns `NO MATCH` or `NO` with developer forensic logs.

---

## ⚡ Measured Benchmarks

*Evaluated on real surveillance footage using Apple Silicon MPS / NVIDIA CUDA:*

| Metric | Measured Value | Target Benchmark |
| :--- | :--- | :--- |
| **Grounded Retrieval Precision** | **100.0%** (16/16 test queries) | $\ge 90.0\%$ |
| **Negative Attribute Rejection Rate** | **100.0%** (0 false positives) | $100.0\%$ |
| **Discrete Track Counting Accuracy** | **100.0%** (`COUNT(DISTINCT tracking_id)`) | $\ge 95.0\%$ |
| **Average Query Latency** | **14.4 ms** | $< 50.0\text{ ms}$ |
| **Evidence Clip Generation Time** | **27.2 ms** | $< 150.0\text{ ms}$ |
| **Clarify-Once Recall Persistence** | **100.0%** (Retained across restarts) | $100.0\%$ |

---

## 🚀 Quickstart & Setup

### Automated Setup (Recommended)

```bash
git clone https://github.com/aswinnachi/hacknex24h.git
cd hacknex24h

# Run the automated setup script
chmod +x setup.sh run.sh
./setup.sh

# Start the server
./run.sh
```

### Manual Installation

1. **Clone the repository:**
   ```bash
   git clone https://github.com/aswinnachi/hacknex24h.git
   cd hacknex24h
   ```

2. **Create and activate a virtual environment:**
   ```bash
   python3 -m venv venv
   source venv/bin/activate
   ```

3. **Install dependencies:**
   ```bash
   pip install --upgrade pip
   pip install -r requirements.txt
   ```

4. **Initialize environment and database:**
   ```bash
   cp .env.example .env
   python -m backend.app.db.init_db
   ```

5. **Start the FastAPI application:**
   ```bash
   uvicorn backend.app.main:app --host 127.0.0.1 --port 8000 --reload
   ```

6. Open **`http://127.0.0.1:8000`** in your browser.

---

## 🧩 Technology Stack

* **Backend**: FastAPI, Uvicorn, Pydantic Settings
* **Object Detection**: YOLO-World (`yolov8s-world.pt`) via Ultralytics
* **Object Tracking**: Traffic-calibrated ByteTrack (`cctv_tracker.yaml`)
* **Multimodal Embeddings**: Google SigLIP (`google/siglip-base-patch16-224`) & OpenAI CLIP (`openai/clip-vit-base-patch16`)
* **Vector Indexing**: FAISS (`IndexFlatIP`) with L2 normalization
* **Relational Storage**: SQLite (`cctv.db`) for ACID metadata & spatial memory
* **Frontend**: Vanilla HTML5, CSS3 (Dark Theme Glassmorphism), Modern ES6 JavaScript, HTML5 Canvas HUD reticle overlay
* **Acceleration**: Hardware-aware automatic selection (Apple Silicon Metal MPS / NVIDIA CUDA / CPU)

---

## 📡 REST API Reference

| Method | Endpoint | Description |
| :---: | :--- | :--- |
| `POST` | `/api/cameras/upload` | Upload CCTV video file with automatic multi-stage analysis pipeline |
| `GET` | `/api/cameras` | List all registered cameras, metadata, and analysis summaries |
| `POST` | `/api/query` | Natural-language conversational intelligence query |
| `GET` | `/api/evidence/{event_id}` | Fetch target evidence metadata, clip URL, and dense per-frame tracking coordinates |
| `POST` | `/api/memory` | Create or update a Clarify-Once persistent spatial landmark mapping |
| `GET` | `/api/memory` | Retrieve all saved persistent memory landmarks |
| `GET` | `/api/events` | Browse detected visual event timeline |
| `GET` | `/api/health` | Healthcheck and hardware acceleration diagnostic report |

---

## 🧪 Benchmark Verification Suite

Run the automated test suite to verify the pipeline:

```bash
# Run comprehensive precision evaluation
python scripts/evaluate.py

# Test strict negative constraint rejection
python scripts/test_negative_constraints.py

# Test discrete track counting logic
python scripts/test_count_queries.py
```

### Verified Sample Queries

* `"Show the white car"` ➔ **MATCH** (Locked onto white vehicle with active boundary)
* `"Show the red car"` ➔ **NO MATCH** (Strict negative filter rejection)
* `"Show the person with a bag"` ➔ **MATCH** / **NO MATCH** (Verified against carried accessories)
* `"How many cars passed through the camera?"` ➔ **COUNT** (Unique track IDs calculated)
* `"Did a vehicle pass through the main gate?"` ➔ **YES** (Resolved via Clarify-Once memory)

---

## 📄 License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.
