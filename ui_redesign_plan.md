# AI Vision Security — Complete UI Redesign Plan

## 1. Executive Summary & Design Vision

**Product Identity:**  
- **Product Name:** `AI Vision Security`  
- **Subtitle:** `Smarter Surveillance. Safer Tomorrow.`  
- **Design Paradigm:** High-density, professional commercial AI video surveillance command center. Deep navy, near-black obsidian base, fine specular borders, subtle radial elevation, information-dense multi-column hierarchy, and category-color bounding boxes.
- **Goal:** Completely redesign the frontend into a multi-page/multi-view command platform matching the reference specifications without replacing real working backend endpoints with mockups or deleting existing implementations.

---

## 2. Design Tokens & Color System

All components, surfaces, overlays, and charts will use the exact specified design tokens:

| Token | Hex Value | Semantic Usage |
|---|---|---|
| `--bg-main` | `#050B18` | Main application backdrop |
| `--bg-sidebar` | `#071226` | Fixed left navigation sidebar |
| `--bg-top-nav` | `#08152B` | Compact global header bar |
| `--bg-card` | `#09172D` | Primary card container background |
| `--bg-elevated` | `#0D1D36` | Elevated panels, popovers, dropdowns |
| `--bg-hover` | `#112746` | Interactive hover states |
| `--border-color` | `#193454` | Fine specular borders & dividers |
| `--text-main` | `#EAF3FF` | Primary crisp heading & metric text |
| `--text-secondary` | `#8DA6C8` | Secondary labels & metadata |
| `--text-muted` | `#617B9F` | Timestamps, micro-badges, hints |
| `--cyan-primary` | `#13D9F5` | Live stream badges, interactive highlights, focus rings |
| `--blue-bright` | `#258BFF` | Active navigation, primary action buttons |
| `--violet-ai` | `#9B5CFF` | AI search, people-tracking overlays, conversational elements |
| `--emerald-status`| `#00D5A0` | Healthy system status, connected cameras, verified verdicts |
| `--amber-warning` | `#FFB52E` | Cautionary warnings, reconnection states, unverified states |
| `--red-critical`  | `#FF416C` | Critical alerts, tripwire violations, disconnect/danger |
| `--orange-secondary`| `#FF9638` | Industrial equipment, vehicles secondary highlight |

---

## 3. Existing Project Architecture & API Mapping

The current stack consists of a FastAPI backend mounted with SQLite + FAISS + PyTorch/MPS YOLO-World & ByteTrack, serving static frontend assets (`index.html`, `styles.css`, `app.js`).

### Comprehensive Feature-to-API Mapping

| UI Page / Module | Feature | Existing API Endpoint | Method | Status |
|---|---|---|---|---|
| **Global Header** | System Health & Device | `/api/health` | `GET` | Preserved |
| **Global Header** | Real-Time Alert Badge | `/api/alerts/status` | `GET` | Preserved |
| **Left Sidebar** | Unacknowledged Alerts Count | `/api/alerts/status` | `GET` | Preserved |
| **Overview** | Active/Offline Metrics | `/api/cameras` | `GET` | Preserved |
| **Overview** | Recent Detections Grid | `/api/cameras/detections/recent` | `GET` | Preserved |
| **Overview** | Recent Alerts Feed | `/api/alerts/recent` | `GET` | Preserved |
| **Live Monitoring**| Live Stream Feed (MJPEG) | `/api/cameras/{id}/live` | `GET` | Preserved |
| **Live Monitoring**| Live Detections / Bounding Boxes | `/api/cameras/{id}/detections/latest` | `GET` | Preserved |
| **Live Monitoring**| Camera Stream Telemetry | `/api/cameras/{id}/status` | `GET` | Preserved |
| **Live Monitoring**| Disconnect / Reconnect | `/api/cameras/{id}/disconnect`, `/api/cameras/connect` | `POST` | Preserved |
| **Cameras & Sources**| Connect Live Source (USB/RTSP/YT) | `/api/cameras/connect` | `POST` | Preserved |
| **Cameras & Sources**| Upload CCTV Footage | `/api/videos/upload` | `POST` | Preserved |
| **Cameras & Sources**| Upload Job Status Polling | `/api/jobs/{job_id}` | `GET` | Preserved |
| **Cameras & Sources**| Delete Camera Stream | `/api/cameras/{camera_id}` | `DELETE` | Preserved |
| **Cameras & Sources**| Reset All System Data | `/api/data/reset` | `POST` | Preserved |
| **AI Search** | Conversational Natural Query | `/api/query` | `POST` | Preserved |
| **AI Search** | Query History & Latencies | `/api/queries/history` | `GET` | Preserved |
| **AI Search** | Clarify-Once Landmark ROI | `/api/memory` | `GET`, `POST`, `DELETE` | Preserved |
| **Detection Events**| Filtered Event Search | `/api/events/search` | `GET` | Preserved |
| **Detection Events**| Detailed Event Record | `/api/events/{event_id}` | `GET` | Preserved |
| **Alerts & Tripwires**| Rule List & Management | `/api/alerts/rules` | `GET`, `POST`, `PUT`, `DELETE` | Preserved |
| **Alerts & Tripwires**| Alerts Log with Filter | `/api/alerts` | `GET` | Preserved |
| **Alerts & Tripwires**| Acknowledge Alert | `/api/alerts/{id}/acknowledge` | `POST` | Preserved |
| **Alerts & Tripwires**| Resolve Alert | `/api/alerts/{id}/resolve` | `POST` | Preserved |
| **Analytics** | Scene Intelligence Report | `/api/cameras/{id}/scene` | `GET` | Preserved |
| **Analytics** | Detection Volume & Classes | `/api/cameras/{id}/summary` | `GET` | Preserved |

---

## 4. UI Page Architecture & Layout Specifications

### A. Fixed Left Sidebar
- **Header:** Shield icon with glowing cyan radar animation, title `AI Vision Security`, subtitle `Smarter Surveillance. Safer Tomorrow.`.
- **Navigation Items (8 dedicated views):**
  1. `Overview` (Icon: Layout Dashboard)
  2. `Live Monitoring` (Icon: Video) — with active stream indicator
  3. `Cameras & Sources` (Icon: Sliders / Server)
  4. `AI Search` (Icon: Sparkles / Brain)
  5. `Detection Events` (Icon: History / Clock)
  6. `Alerts & Tripwires` (Icon: Shield Alert) — with real active alert counter pill
  7. `Analytics` (Icon: BarChart3)
  8. `Settings` (Icon: Settings)
- **Footer:** Real backend status indicator (`● Backend: Online (MPS)`), version (`v1.4.0`), and current active cameras.

### B. Top Navigation Bar
- Current page title & breadcrumbs (e.g. `Live Monitoring // Matrix Grid 2x2`).
- Fast Global Search input for cameras or natural language questions.
- Browser local clock (`15:42:08 UTC+5:30`) updated every second.
- Backend hardware badge (`Compute: Apple MPS`).
- Notifications button linked to alerts drawer / page.

### C. The 8 Dedicated View Pages

#### 1. Overview Dashboard
- **Top 5 Metric Cards:** Active Cameras, Offline Cameras, Tracked Objects, Total Events, Critical Alerts.
- **Main View Grid:**
  - Hero active camera live preview card with quick grid switcher.
  - Right: Live Detections Feed (real-time stream of detected objects across cameras).
  - Lower Grid: Camera Status matrix, Detection Activity chart, and Recent Alerts log with direct acknowledge buttons.

#### 2. Live Monitoring (Flagship Workspace)
- **Grid Layout Switcher:** 1-Camera Focus, 2-Camera Dual, 4-Camera (2x2 Matrix), 6-Camera Grid.
- **Camera Cards:** Real live MJPEG stream `/api/cameras/{id}/live` or video element, canvas detection overlays with consistent class colors (Person = violet, Vehicles = cyan/blue, Other = green, Alert = red), stream resolution/FPS badge, expand/fullscreen action, snapshot action, disconnect/reconnect controls.
- **Right Panel: Live Detections Feed:** Filter tabs (`All`, `Person`, `Vehicle`, `Other`) with live incoming detections, confidence scores, track IDs, and click to view full evidence.

#### 3. Cameras & Sources
- Top actions: `+ Add Live Camera Stream` (Webcam, RTSP, YouTube Live, Test Video) and `Upload CCTV Video`.
- Stream source cards showing: Name, ID, Source Type badge (`YT LIVE`, `RTSP`, `LOCAL`), Resolution, FPS, Status, Live Preview thumbnail, Reconnect button, Disconnect button, Delete button.
- Clean RTSP credential masking and YouTube sample presets.
- Progress bar for video uploads and indexing.

#### 4. AI Conversational Search
- Large prominent search bar with natural language voice/text prompt style.
- Preset query discovery chips: (`Show moving white car`, `Find people carrying bags`, `Did a red car pass?`, `Count people`).
- Results feed displaying: Verified verdict pill (`MATCH`, `NO MATCH`, `UNVERIFIED`), confidence, target camera, wall-clock timestamp, video offset, and lazy visual evidence clip player.
- Ambiguous landmark clarification modal (`Clarify-Once` spatial memory).

#### 5. Detection Events Explorer
- Search and multi-filter toolbar: Camera filter, Object class filter, Date/Time range, Track ID search.
- Clean event card grid / table with snapshot thumbnails, bounding box coordinates, detected attributes, and click to inspect modal.

#### 6. Alerts & Tripwires
- Subnav tabs: `Active Alerts Log` and `Tripwire & Zone Rules`.
- Real-time Alert cards with severity pill (`CRITICAL`, `WARNING`, `INFO`), trigger reason, camera source, timestamp, evidence snapshot, and 1-click `Acknowledge` and `Resolve` buttons.
- Rule creator with CAD virtual geometry drawing tool (2-click tripwire line, 3+ click polygon zone), schedule window (restricted overnight hours), target object class, and cooldown interval.

#### 7. Analytics
- Summary cards: Total Track Episodes, Average Inference Latency, Alert Rate.
- Category distribution breakdown (Cars, Persons, Bicycles, Trucks, Accessories).
- Hourly detection activity breakdown chart.
- Camera utilization metrics.

#### 8. Settings
- Display preferences (bounding box labels, confidence scores, HUD watermarks).
- Stream & detection preferences (sampling intervals, hardware device info).
- Storage & retention status (segmented recordings, clips directory size, clean reset trigger).

---

## 5. Implementation Steps & Verification Plan

1. **Phase 1: Write `ui_redesign_plan.md`** (Completed).
2. **Phase 2: CSS Architecture (`frontend/styles.css`)**:
   - Implement complete design tokens (`#050B18`, `#071226`, `#08152B`, `#09172D`, `#0D1D36`, etc.).
   - Build sidebar, top header, page containers, metric cards, video matrix grids, detection feed cards, alert badges.
3. **Phase 3: HTML Structure (`frontend/index.html`)**:
   - Restructure into multi-view architecture with standard view IDs (`view-overview`, `view-live-monitoring`, `view-cameras`, `view-ai-search`, `view-events`, `view-alerts`, `view-analytics`, `view-settings`).
   - Preserve all existing modal IDs and form controls for 100% backwards compatibility with `app.js`.
4. **Phase 4: Client Logic Integration (`frontend/app.js`)**:
   - Implement view switcher router.
   - Wire Overview metrics, multi-camera live grid, live detections feed, and analytics charts.
   - Preserve all existing live stream, query, upload, and alerting handlers.
5. **Phase 5: Regression & Correctness Testing**:
   - Run `tests/test_correctness.py` and `tests/test_youtube_live.py`.
   - Verify HTTP endpoints and browser responsiveness.
   - Generate `ui_redesign_test_report.md`.
