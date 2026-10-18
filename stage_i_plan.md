# Stage I Implementation Plan — Persistent Evidence, Cross-Camera Intelligence & End-to-End Validation

**System:** AI Vision Security — Multi-Stream Video Intelligence with Conversational Query  
**Stage:** Stage I  
**Target Hardware:** Apple Silicon MacBook Air M4 (MPS acceleration, Unified Memory)  
**Date:** October 18, 2026  

---

## 1. Baseline Inspection & Component Inventory

A thorough audit of the existing Stage B–H codebase reveals the following foundation:

1. **Database Schema (`backend/app/db/init_db.py`):**
   - Tables: `cameras`, `events`, `alert_rules`, `alerts`, `memory`, `queries`.
   - `events` table schema stores `id`, `camera_id`, `dataset_id`, `tracking_id`, `concept`, `start_timestamp`, `end_timestamp`, `confidence`, `bounding_boxes` (JSON string), `representative_frame` (filepath), `clip_path` (filepath), `embedding_ref`, `attributes` (JSON string with colors, clothing, movement displacement), `source_type`, `session_id`, `frame_number`.
   - Filepaths are stored rather than binary blobs in SQLite, adhering to optimal WAL-mode storage practices.

2. **Persistent Live Event Indexer (`backend/app/core/live_indexer.py`):**
   - Implements `LiveTrackEpisode` and `LiveEventIndexer`.
   - Bounded async write queue with worker thread and overflow protection.
   - Representative snapshots saved asynchronously to `data/frames/live/{camera_id}/frame_{event_id}.jpg`.
   - Tracks flushed to SQLite on expiration (timeout), camera disconnect, or graceful shutdown.

3. **Alert Engine (`backend/app/core/alert_engine.py`):**
   - Rules stored in `alert_rules` (`rule_type`, `object_class`, `zone_points`, `tripwire_line`, `cooldown_seconds`, `severity`).
   - Triggered alerts persisted in `alerts` table (`status` = `active`, `acknowledged`, `resolved`, `acknowledged_at`, `resolved_at`, `evidence_frame`).
   - Alert cooldown logic prevents spamming alerts for the same track within cooldown window.

4. **Multi-Camera Inference & Tracker Pipeline (`backend/app/core/stream_manager.py`):**
   - `MultiCameraInferenceScheduler` fair round-robin scheduling across active streams.
   - Independent ByteTrack instances per camera (`_trackers[camera_id]`).
   - Decoupled preview FPS vs inference FPS.

5. **Cross-Camera Forensics Foundation:**
   - Existing vector search (`backend/app/core/vector_search.py`) and search API (`backend/app/api/search.py`).
   - Need dedicated candidate association endpoint (`/api/cameras/cross-match`) that evaluates multi-camera candidate matches using temporal sequence, color family, concept, and movement heuristics, explicitly labeled as candidate matches without claiming global biometric re-ID.

---

## 2. Implementation Workstreams

### Workstream 1: Persistent Evidence & Retention Management
- Verify snapshot directory configuration (`data/frames/live/`, `data/evidence/`).
- Ensure no credentials/tokens leak into `events` or `cameras` records (sanitized URLs only).
- Implement retention limit enforcement (`clean_expired_evidence(max_age_hours, max_gb)`).
- Handle missing files safely: if a snapshot file is deleted from disk, API returns HTTP 200 with placeholder or clear `evidence_missing: true` flag rather than crashing with 500 error.
- Deduplicate events on insertion: handle duplicate event IDs gracefully.

### Workstream 2: YouTube Live Source & Ingestion History
- Differentiate:
  1. **Direct Embedded Playback (`youtube_live` direct):** Client-side iframe rendering via YouTube IFrame API. No backend decoding, no recording, no AI tracking claim.
  2. **Backend-Decoded AI Ingestion (`youtube_live` AI):** Stream decoded via `streamlink` / `yt-dlp` + OpenCV in backend, feeding YOLO-World detector and ByteTrack.
- Track ingestion sessions: `session_id`, `session_start`, `session_end`, `frames_decoded`, `connection_status`.
- Persist in `cameras` metadata table.

### Workstream 3: Cross-Camera Candidate Matching Engine
- Create `backend/app/core/cross_camera.py` and API route `GET /api/cameras/cross-match`.
- Retrieve candidate objects (vehicles and pedestrians) across distinct cameras.
- Heuristics:
  - Temporal plausibility window: $\Delta t = t_2 - t_1 \in [1s, 180s]$.
  - Object class match (`car` $\leftrightarrow$ `car`, `truck` $\leftrightarrow$ `vehicle`).
  - Color similarity matching based on HSV color histograms / dominant color tag.
  - Directional camera transition plausibility (e.g., Gate Entrance $\rightarrow$ Main Driveway).
- Output structure clearly flags:
  - `is_candidate_match: true`
  - `match_confidence: float`
  - `match_evidence: { time_delta_sec, class_match, color_match, sequence_plausible }`
  - Disclaimers: *"Candidate association based on spatiotemporal and attribute similarity. Does not represent unique global biometric re-identification."*

### Workstream 4: End-to-End Alert Lifecycle & Deduplication
- Create test alert rule (e.g., zone intrusion or tripwire for `person` on test camera).
- Trigger alert, capture evidence frame crop, verify persistence in `alerts` table.
- Test acknowledgment (`POST /api/alerts/{id}/acknowledge`) and resolution (`POST /api/alerts/{id}/resolve`).
- Verify deduplication: repeated frame detections of the same track do not generate duplicate alert entries.

### Workstream 5: Restart & Recovery Testing
- Check data survival across clean and simulated restart.
- Test recovery behavior:
  - SQLite WAL persists `cameras`, `events`, `alerts`.
  - Evidence files on disk remain linked.
  - If a video source is unavailable on restart, mark as `DISCONNECTED` gracefully without blocking server startup.

### Workstream 6: MacBook Air M4 Performance Benchmarking
- Benchmark 1, 2, and 3 concurrent video feeds (using test media).
- Measure:
  - Inference FPS per camera
  - Preview / Capture FPS
  - Inference latency: p50 and p95 (ms)
  - Process memory (RSS in MB via `psutil`)
  - MPS (Metal Performance Shaders) / CPU utilization

### Workstream 7: Regression & Verification Testing
- Run existing test suites (`test_correctness.py`, `test_stage_h_multicam.py`, `test_tracking_reliability.py`).
- Add `tests/test_stage_i_persistence.py` covering persistence, restart recovery, missing evidence handling, cross-camera matching, and alert lifecycle.
- Produce `stage_i_validation_report.md`.
