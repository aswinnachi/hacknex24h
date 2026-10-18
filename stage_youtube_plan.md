# Stage YouTube: YouTube Live Integration and Persistent Local Storage Plan

**Project**: Multi-Stream Video Intelligence with Conversational Query  
**Target Feature**: YouTube Live Source Integration, Robust Stream Ingestion, Configurable Storage Retention, and Dashboard Management  
**Status**: Planning & Architecture Review  

---

## 1. Existing System Architecture Analysis

The system currently processes video through a decoupled, multi-threaded pipeline:

```
[Video Source] (RTSP / USB Webcam / File)
       │
       ▼
CameraStream._capture_worker (Threaded OpenCV VideoCapture)
       │
       ├──> Bounded Frame Lock (_latest_frame, _latest_jpeg) ──> MJPEG Preview Generator
       │
       ▼
CameraStream._analysis_worker (Threaded ~4 FPS Sampler)
       │
       ▼
OpenVocabularyDetector.track_frame() [FairInferenceCoordinator, imgsz=640, _class_cache]
       │
       ├──> ByteTrack (Per-Camera Isolated Instance)
       │
       ├──> LiveEventIndexer.process_frame_detections() (Async SQLite Upsert & SigLIP Indexing)
       │
       └──> AlertEngine.evaluate_frame_detections() (Tripwires, Polygon Zones, Cooldowns)
```

### Key Components & Reusability:
1. **`stream_manager.py` (`CameraStream`)**:
   - Manages capture thread, reconnect logic, error state tracking, and bounded frame locks.
   - Currently uses `parse_source_target(source)` which only converts digit strings to integer webcam indices or passes file paths/RTSP URLs straight to `cv2.VideoCapture`.
   - Reusable: Can be extended with a resolver hook that detects `source_type == "youtube_live"` and resolves the public HLS stream URL before opening `cv2.VideoCapture`.
2. **`detector.py` (`OpenVocabularyDetector`, `FairInferenceCoordinator`)**:
   - Already provides prioritized, non-starving inference with O(1) text vocabulary caching.
   - Reusable: Zero changes needed to inference or tracking logic; YouTube Live streams use the exact same shared detector and per-camera ByteTrack tracker without duplicating weights.
3. **`live_indexer.py` (`LiveEventIndexer`)**:
   - Asynchronously creates `LiveTrackEpisode`s, samples bounding boxes, saves representative JPEG evidence frames to `data/frames/live/<camera_id>/`, and indexes embeddings in FAISS/SigLIP.
   - Reusable: Handles events for any `camera_id` with `source_type="youtube_live"`.
4. **`database.py` & `init_db.py`**:
   - Tables: `cameras`, `events`, `queries`, `alert_rules`, `alerts`, `memory`.
   - Migration needed: Ensure `source_type` can store `'youtube_live'`, add `recording_settings` / `is_recording` fields, and add query history result metadata.
5. **`evidence.py`**:
   - Generates lazy clips and visual scene bounding boxes for query results.
6. **Frontend (`index.html`, `app.js`)**:
   - Connect modal currently offers Webcam, RTSP, and Test Video. Needs a "YouTube Live Stream" option with dedicated URL input and validation styling.
   - Event search panel needs source type filter support.

---

## 2. YouTube Live Resolution & Compliance Strategy

### A. Authorization & Platform Compliance
- **Compliance Policy**: In strict accordance with platform policies, the integration must **not** attempt to bypass DRM, private video restrictions, age gates, CAPTCHAs, or authentication requirements.
- **Resolver**: We utilize `yt-dlp` in Python library mode to extract legitimate public HLS manifests (`.m3u8` video master playlists) provided by YouTube for public live streams.
- **Fallback & Error Guidance**:
  If a stream is not live, is geoblocked, requires authentication, or is unavailable:
  - Do NOT crash or endlessly retry.
  - Return clear, helpful guidance:
    `"Unable to obtain live feed from this YouTube URL: <reason>. If you own or manage this camera, please connect using its direct RTSP or HLS stream URL."`

### B. URL Validation Scheme
Support standard YouTube URL variations:
- `https://www.youtube.com/watch?v={VIDEO_ID}`
- `https://youtu.be/{VIDEO_ID}`
- `https://www.youtube.com/live/{VIDEO_ID}`
- `https://m.youtube.com/watch?v={VIDEO_ID}`
- `https://www.youtube.com/embed/{VIDEO_ID}`
- Direct authorized HLS live stream URLs ending in `.m3u8`.
- Mock URLs for automated testing: `mock://youtube.com/live/{ID}` or query param `?mock=1`.

---

## 3. Storage, Retention & Video Recording Architecture

The user specifies:
> Save the following in the existing database/storage system:
> - Source ID, source name, source type and connection status.
> - Event timestamp, object class, track ID, confidence and available attributes.
> - Camera/source association and cross-camera match candidates with uncertainty.
> - Query history and results.
> - Alert events, rule IDs and acknowledgment/resolution status.
> - Evidence image paths and metadata.
> Store evidence images as files and reference them from the database. Make complete video recording an optional feature with configurable storage limits, retention and cleanup.

### Implementation Design:
1. **Evidence Frames & Clips**:
   - Evidence frames saved to `data/frames/live/<camera_id>/frame_<event_id>.jpg`.
   - Stored in SQLite `events` table (`representative_frame`, `attributes`, `bounding_boxes`, `source_type`).
2. **Optional Full Video Recording**:
   - Module: `backend/app/core/stream_recorder.py`.
   - Optional toggle per stream (`record_stream: bool`).
   - Writes 5-minute segmented MP4 files (`data/recordings/<camera_id>/seg_<timestamp>.mp4`).
   - Configurable retention policy:
     - `max_retention_days` (default 7 days).
     - `max_storage_mb` (default 5000 MB).
   - Automated background cleanup job running periodically to purge expired/excess segments.
3. **Query History Storage**:
   - Extend `queries` table with `result_summary` and `result_count`.
   - Expose `GET /api/queries/history` to view past searches and their grounded evidence.

---

## 4. Dashboard Enhancements

1. **Connect Modal**:
   - Add "YouTube Live Stream" to source selector.
   - Dynamic form group displaying YouTube URL field with validation regex.
2. **Camera Card & Stream List**:
   - Visual badge for YouTube Live sources (`badge-youtube` with red/ruby accent `#ef4444`).
   - Connection status indicator (`CONNECTED`, `CONNECTING`, `ERROR`, `DISCONNECTED`).
3. **Interactive Preview**:
   - Decoded MJPEG stream preview for active YouTube Live camera.
   - Live bounding box overlay and active track counter.
4. **Historical Event & Evidence Browser**:
   - Source type filter (All, YouTube Live, RTSP, Webcam, Uploaded).
   - Date/time range selector.
   - Interactive evidence preview modal displaying bounding boxes and object attributes.

---

## 5. Implementation Stages

- **Stage 1**: YouTube Live URL validator & compliant resolver (`backend/app/core/youtube_resolver.py`).
- **Stage 2**: Stream manager integration (`CameraStream` support for YouTube Live resolution, reconnection, and error handling).
- **Stage 3**: Optional stream recording & retention engine (`backend/app/core/stream_recorder.py`).
- **Stage 4**: Database schema updates & query history enhancements (`backend/app/db/init_db.py`, `backend/app/api/cameras.py`, `backend/app/api/query.py`).
- **Stage 5**: Frontend dashboard UI integration (`index.html`, `app.js`).
- **Stage 6**: Automated testing suite (`tests/test_youtube_live.py`) and documentation (`stage_youtube_test_report.md`).
