# AI Vision Security — Live Detections Panel Fix Report

**Project:** AI Vision Security — Multi-Stream Video Intelligence with Conversational Query  
**Issue:** Live Detections panel stuck displaying "Loading live detections…" indefinitely  
**Date:** October 18, 2026  
**Status:** **RESOLVED & VALIDATED (8/8 Automated Tests Passing)**

---

## 1. Executive Summary & Root Cause Analysis

### Symptoms
When opening the dashboard (Overview or Live Monitoring view), the Live Detections panel displayed:
- Category filter buttons: **All / Person / Vehicle / Other**
- **Real-Time** badge
- Text message **"Loading live detections…"** indefinitely, never resolving to real events even while backend multi-camera inference and ByteTrack tracking were actively running.

### Root Cause Identification
A complete data flow trace across frontend (`frontend/app.js`, `frontend/index.html`) and backend (`backend/app/api/cameras.py`, `backend/app/core/stream_manager.py`) revealed two critical client-side parsing bottlenecks:

1. **API Schema Contract Mismatch:**
   - The backend endpoint `GET /api/cameras/detections/recent` returns an object:
     ```json
     {
       "count": 50,
       "limit": 50,
       "camera_id": null,
       "detections": [...]
     }
     ```
   - In `frontend/app.js` (`loadRecentDetectionsFeed()` line 4176), the frontend treated the response as a direct array:
     ```javascript
     let detections = await res.json();
     if (liveDetectionsFilter !== "all") {
         detections = detections.filter(d => ...); // THREW TypeError: detections.filter is not a function!
     }
     ```
   - Calling `.filter()` directly on the wrapper object threw an unhandled `TypeError: detections.filter is not a function` inside the `try...catch` block. The exception aborted DOM rendering before `#overview-detections-list` or `#monitoring-detections-list` could be populated.
   - Consequently, the static HTML placeholder `<div class="empty-state">Loading live detections...</div>` remained permanently stuck on screen.

2. **Metric Calculation Cascading Failure in `loadOverviewData()`:**
   - In `loadOverviewData()` (lines 3970–3980), a separate call to `/api/cameras/detections/recent` was made:
     ```javascript
     const recTracks = await resTracks.json();
     const uniqueTrackIds = new Set(recTracks.map(t => t.track_id).filter(Boolean)); // THREW TypeError: recTracks.map is not a function!
     ```
   - This failure prevented the periodic overview refresh from reaching `await loadRecentDetectionsFeed()`, leaving the UI permanently unrefreshed.

3. **Absence of State Machine & Fallback UI:**
   - The original code had no distinct state management for `loading`, `populated`, `empty`, `error`, or `updating`.
   - If a request was pending, timed out, or returned 0 detections, no user feedback or retry action was available.
   - Filter clicks only modified tabs within one panel rather than syncing across both Overview and Live Monitoring panels.

---

## 2. Modified Files

| File | Changes Made |
| :--- | :--- |
| `frontend/app.js` | 1. Implemented full Live Detections state machine (`loading`, `populated`, `empty`, `error`, `updating`).<br>2. Added safe payload unwrapping for both `{ detections: [...] }` and direct array formats.<br>3. Fixed `loadOverviewData()` tracked objects calculation.<br>4. Implemented non-overlapping polling with 5000ms `AbortController` timeout.<br>5. Implemented deduplication by `camera_id + track_id + timestamp + class` and timestamp-descending sort.<br>6. Connected friendly camera names, track IDs, confidence percentage with dynamic badges, and thumbnails.<br>7. Added synchronized filter tabs across Overview and Live Monitoring.<br>8. Added error state with interactive `window.retryLiveDetections()` action. |
| `frontend/index.html` | 1. Updated `#overview-detections-list` placeholder to include animated inline spinner.<br>2. Updated `#monitoring-detections-list` placeholder to include animated inline spinner.<br>3. Ensured matching structure and tab attributes across views. |
| `frontend/styles.css` | Added `@keyframes spin` and `.spinner-inline` utility styles. |
| `tests/test_live_detections_panel.js` | Comprehensive automated test suite validating all 8 requirements. |

---

## 3. API Contract Specification

### Endpoint: `GET /api/cameras/detections/recent`

#### Query Parameters
- `camera_id` (*optional*, `string`): Filter recent detections to a specific camera. If omitted, returns recent detections across all connected cameras.
- `limit` (*optional*, `integer`, default: 50, min: 1, max: 200): Maximum records returned.

#### Response Payload (`200 OK`)
```json
{
  "count": 50,
  "limit": 50,
  "camera_id": null,
  "detections": [
    {
      "camera_id": "LIVE_CAM_01_PEDESTRIAN",
      "timestamp": 1792319214.766,
      "frame_number": 14324,
      "class": "person",
      "label": "person",
      "confidence": 0.839,
      "raw_confidence": 0.839,
      "bbox": [452.9, 100.8, 520.4, 211.9],
      "track_id": 2183,
      "track_state": "confirmed",
      "is_predicted": false,
      "source_type": "live",
      "representative_frame": "data/frames/live/LIVE_CAM_01_PEDESTRIAN/frame_xxx.jpg"
    }
  ]
}
```

#### Safe Frontend Client Parser Contract
```javascript
const data = await res.json();
const rawList = Array.isArray(data) ? data : (data?.detections || []);
```

---

## 4. UI State Specifications

1. **Loading State:**
   - Active when cache is empty and first network request is in flight.
   - Shows: `<span class="spinner-inline"></span> Loading live detections...`

2. **Populated State:**
   - Active when detection records exist.
   - Displays up to 25 items sorted by timestamp (newest first).
   - Each card displays:
     - Class Icon / Thumbnail (Person 👤, Vehicle 🚗, Other 📦).
     - Object Class name (capitalized).
     - Camera Name (resolved via `allCameras` map).
     - Track ID (`Track #<id>`).
     - Formatted Time Badge (`HH:MM:SS` or `Just now`).
     - Confidence Percentage Badge (green for $\ge 75\%$, cyan for $50-74\%$, amber for $< 50\%$).
     - Click action to navigate to AI Search forensic investigation.

3. **Empty State:**
   - Active when request succeeds with 0 events: displays `"No detections yet."`
   - Active when category filter excludes all events: displays `"No detections in this category."`

4. **Error State:**
   - Active when request fails (HTTP error, connection reset, timeout) and cache is empty.
   - Displays: `"⚠️ Unable to load detections"` with an interactive `Retry` button that invokes `window.retryLiveDetections()`.

5. **Updating State:**
   - Active during background polling refreshes while previous detections are already displayed.
   - Preserves previously rendered items so the UI never flickers, blanks out, or jumps back to "Loading".

---

## 5. Automated Verification Results

Test suite: `tests/test_live_detections_panel.js` executed via Node.js v26.9.0.

```
============================================================
TESTING LIVE DETECTIONS PANEL LOGIC & STATE MACHINE
============================================================
[Test 1 & 2] Verifying Loading -> Populated state with real API data...
  ✓ Initial loading completed, populated real events rendered accurately.
[Test 3] Verifying successful empty response -> 'No detections yet.'...
  ✓ Empty response shows 'No detections yet.'
[Test 4] Verifying failed request -> Error state with Retry action...
  ✓ Failed request renders error and retry action.
[Test 5] Verifying category filters (All, Person, Vehicle, Other)...
  ✓ Filters work accurately without mutating underlying data.
[Test 6] Verifying deduplication on repeated refreshes...
  ✓ Repeated refreshes properly deduplicate events.
[Test 7] Verifying Updating state preserves content without wiping...
  ✓ Updating state preserves visible items without flashing blank or error.
[Test 8] Verifying missing optional fields do not crash rendering...
  ✓ Missing optional fields handled completely safely.
============================================================
ALL 8 FRONTEND UNIT & INTEGRATION TESTS PASSED (8/8)!
============================================================
```

### Backend Integration Tests
`tests/test_stage_d_detection.py`:
- 8/8 tests passed successfully including live detection endpoints, track stability, multi-camera tracker isolation, and recent detections serialization.
