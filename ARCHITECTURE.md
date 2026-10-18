# AI Vision Security — System Architecture

This document describes the architectural layout, data flow, computer-vision pipeline, storage strategy, and deployment topology of the **AI Vision Security** system.

---

## 1. System Topology Diagram

```mermaid
graph TB
    subgraph ClientLayer ["1. Client Layer (Public Edge)"]
        Browser["End User Browser / Mobile Device"]
        VercelCDN["Vercel Edge Network (Global CDN)"]
        Browser -->|HTTPS Static Assets| VercelCDN
    end

    subgraph FrontendApp ["2. Frontend Application (SPA)"]
        Dashboard["AI Vision Security Dashboard"]
        VideoLab["Video Intelligence Lab"]
        PlayerView["Dual Video Player (Annotated vs Original)"]
        TimelineView["Interactive Detection Timeline"]
        HistoryView["Job History & File Management"]
        VercelCDN --> Dashboard
        Dashboard --> VideoLab
        VideoLab --> PlayerView
        VideoLab --> TimelineView
        VideoLab --> HistoryView
    end

    subgraph Gateway ["3. Network & Security Gateway"]
        ReverseProxy["Vercel Proxy Rewrites / CORS Filter"]
        Browser -->|HTTPS API Requests / Video Uploads| ReverseProxy
    end

    subgraph BackendService ["4. Persistent Backend Service (FastAPI)"]
        FastAPIApp["FastAPI Application Server"]
        AuthValidation["Validation & Magic-Bytes Engine"]
        JobManager["Analysis Job Manager"]
        FileServer["Streaming File Server (HTTP 206)"]
        ReverseProxy --> FastAPIApp
        FastAPIApp --> AuthValidation
        FastAPIApp --> JobManager
        FastAPIApp --> FileServer
    end

    subgraph Workers ["5. Video Intelligence Processing Worker"]
        WorkerPool["Background Worker Thread Pool"]
        Decoder["OpenCV Video Demuxer & Frame Sampler"]
        YOLOWorld["YOLO-World Open-Vocabulary Detector"]
        ByteTrack["ByteTrack Multi-Object Tracker"]
        Annotator["Frame Overlay & H.264 Encoder (FFmpeg)"]
        JobManager --> WorkerPool
        WorkerPool --> Decoder
        Decoder --> YOLOWorld
        YOLOWorld --> ByteTrack
        ByteTrack --> Annotator
    end

    subgraph StorageLayer ["6. Storage & Database Tier"]
        Database[("Persistent Database (SQLite / Postgres)
- analysis_jobs
- lab_detections
- cameras & events")]
        StorageAbstraction["Unified Storage Layer (Local / S3 / R2)"]
        ObjectStore[("Cloud Object Storage (S3 / R2)
- Uploaded CCTV Videos
- Annotated MP4 Videos
- Keyframe Evidence Crops
- JSON & CSV Reports")]
        FastAPIApp --> Database
        WorkerPool --> Database
        FastAPIApp --> StorageAbstraction
        WorkerPool --> StorageAbstraction
        StorageAbstraction --> ObjectStore
    end
```

---

## 2. Ingestion to Tracking Pipeline Flow

```mermaid
sequenceDiagram
    autonumber
    actor User as User Browser
    participant FE as Frontend (Vercel)
    participant API as FastAPI Backend
    participant Storage as Object Storage (S3/Local)
    participant Worker as Background Worker
    participant DB as Persistent Database

    User->>FE: Select / Drag-and-drop CCTV video file
    FE->>FE: Client-side validation (Size <= 250MB, Container format)
    FE->>User: Display preview video & duration/resolution metadata
    User->>FE: Click "Analyze Video with YOLO-World & ByteTrack"
    FE->>API: POST /api/lab/upload-and-analyze (Multipart file + sampling)
    API->>API: Server-side validation (Magic bytes + OpenCV container probe)
    API->>Storage: Persist original video file (uploads/{job_id}/video.mp4)
    API->>DB: INSERT INTO analysis_jobs (status='queued', duration, resolution)
    API->>Worker: Spawn asynchronous worker thread
    API-->>FE: Return {job_id, status: 'processing'}

    loop Real-time Telemetry (Every 700ms)
        FE->>API: GET /api/lab/jobs/{job_id}/status
        API->>DB: Query current progress & frames analyzed
        API-->>FE: Return {progress_pct, stage, frames_analyzed, unique_tracks, total_detections}
        FE->>User: Update progress bar & live statistics HUD
    end

    Worker->>Worker: Demux frames via OpenCV VideoCapture
    Worker->>Worker: Run YOLO-World open-vocabulary object detection
    Worker->>Worker: Associate bounding boxes via ByteTrack Kalman filter
    Worker->>Worker: Draw bounding boxes, IDs, and HUD telemetry on frames
    Worker->>Worker: Transcode annotated video to H.264 MP4 via FFmpeg
    Worker->>Worker: Generate detection_results.json & detection_results.csv
    Worker->>Storage: Store annotated MP4, JSON, CSV, and keyframe snapshots
    Worker->>DB: INSERT detections into lab_detections & UPDATE job status='completed'

    FE->>API: GET /api/lab/jobs/{job_id}/results
    API-->>FE: Return full tracking results, video URLs, timeline, and disclaimer
    FE->>User: Display Dual Video Player, Interactive Timeline, and Evidence Gallery
```

---

## 3. Storage Strategy & Persistence

The project employs a tiered storage abstraction (`backend/app/core/storage.py`):

1. **Development & Self-Hosted Fallback (`LocalStorage`)**:
   - Stores files in `data/storage/` with clean subdirectories:
     - `uploads/{job_id}/`: Original source videos.
     - `annotated/{job_id}/`: H.264 browser-compatible annotated videos.
     - `evidence/{job_id}/`: Extracted keyframe object crops.
     - `reports/{job_id}/`: Structured JSON and CSV exports.
   - Files are served directly via `/api/lab/files/{path}` with `Accept-Ranges: bytes` support for smooth HTML5 video seeking.

2. **Cloud Object Storage (`S3Storage`)**:
   - Compatible with Cloudflare R2, AWS S3, MinIO, or Wasabi via `boto3`.
   - Activated automatically when `S3_BUCKET_NAME` is configured.
   - Generates presigned URLs for secure browser streaming and direct downloads.

---

## 4. Computer Vision Model Decoupling

- **Open-Vocabulary Object Detection**:
  - Model: `yolov8s-world.pt` (YOLO-World v2).
  - Open vocabulary classes: Person, Car, SUV, Van, Truck, Bus, Motorcycle, Scooter, Bicycle, Bag, Backpack, Handbag, Suitcase, etc.
  - Per-class calibrated confidence thresholds to suppress background noise.
- **Sequence Multi-Object Tracking**:
  - Algorithm: **ByteTrack** (high-performance association utilizing both high and low confidence detection boxes via Kalman filtering and Hungarian matching).
  - Tracking tokens are sequence-local: each track maintains a continuous trajectory ID (`Track #1`, `Track #2`) across frames.
- **Identity Disclaimer**:
  - Prominently labeled in the UI and exported in JSON reports:
  > *"ByteTrack tracking IDs are sequence-local trajectory tokens within this video footage. They track object motion across consecutive frames but do not establish a person's biometric identity or claim cross-camera re-identification."*
