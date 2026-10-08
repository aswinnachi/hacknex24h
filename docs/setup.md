# Setup & Installation Guide

## Prerequisites
* Python 3.9+
* FFmpeg (e.g. `brew install ffmpeg` on macOS or `apt-get install ffmpeg` on Ubuntu)
* Hardware Acceleration: Auto-detected (Apple Silicon MPS, NVIDIA CUDA, or CPU fallback)

---

## 1. Environment Setup

Clone and initialize virtual environment:
```bash
cd multi-stream-video-intelligence
python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```

---

## 2. Model Setup

The system automatically downloads the required lightweight weights on first run or loads them from local cache:
1. **YOLO-World Detector**: `yolov8s-world.pt` (automatically cached by `ultralytics`)
2. **CLIP Embedding Model**: `openai/clip-vit-base-patch16` (cached via `transformers`)

---

## 3. Database Initialization

Initialize the SQLite schema:
```bash
source venv/bin/activate
python -m backend.app.db.init_db
```

---

## 4. Running the Application

Launch the unified FastAPI + static frontend server:
```bash
source venv/bin/activate
KMP_DUPLICATE_LIB_OK=TRUE uvicorn backend.app.main:app --host 127.0.0.1 --port 8000
```
Open your browser at **http://127.0.0.1:8000/** to view the interactive dashboard.

---

## 5. Adding & Indexing CCTV Videos

### Register Cameras:
```bash
# Register Camera 01 (Street Camera)
curl -X POST http://127.0.0.1:8000/api/videos/register \
  -H "Content-Type: application/json" \
  -d '{"id": "camera_01", "name": "Street Camera", "description": "Street camera feed", "video_path": "data/videos/camera_01/real_video.mp4"}'

# Register Camera 02 (Gate Camera)
curl -X POST http://127.0.0.1:8000/api/videos/register \
  -H "Content-Type: application/json" \
  -d '{"id": "camera_02", "name": "Gate Camera", "description": "Main gate entrance", "video_path": "data/videos/camera_02/gate_video.mp4"}'
```

### Process Video Streams:
```bash
# Process Camera 01
curl -X POST http://127.0.0.1:8000/api/process \
  -H "Content-Type: application/json" \
  -d '{"camera_id": "camera_01", "frame_interval": 15}'

# Process Camera 02
curl -X POST http://127.0.0.1:8000/api/process \
  -H "Content-Type: application/json" \
  -d '{"camera_id": "camera_02", "frame_interval": 15}'
```

### Generate Vector Index:
```bash
python scripts/index_events.py
```

---

## 6. Running Evaluation Suite

To run the automated 9-query benchmark suite:
```bash
python scripts/evaluate.py
```
