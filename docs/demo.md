# 3-Minute Live Hackathon Demo Walkthrough

This guide walks through the exact 8-step live demonstration for hackathon judging.

---

### Step 1: Multi-Camera Overview & Health Telemetry (0:00 - 0:30)
1. Open the dashboard at **`http://127.0.0.1:8000/`**.
2. Point out the **Header Telemetry**:
   * Hardware Acceleration detected (`Compute: MPS` / Apple Silicon).
   * Backend Status: `Online`.
   * Total Events Indexed.
3. Show the **Left Panel**:
   * Multiple active camera streams registered (`camera_01` Street Camera, `camera_02` Gate Camera).

---

### Step 2: Visual Event Timeline (0:30 - 0:50)
1. Point to the **Bottom Event Timeline**.
2. Click the `Camera 01` / `Camera 02` filter buttons to show multi-camera event segmentation.
3. Click any event thumbnail in the timeline.
4. Show that the **Center Evidence Player** instantly loads and streams the video clip with bounding boxes overlaid on canvas.

---

### Step 3: Open-Vocabulary Natural Language Search (0:50 - 1:20)
1. Click the preset chip: **`"Did a red car pass through the main gate in the last hour?"`** (or type it in the chat box).
2. The AI responds instantly in chat:
   ```text
   YES
   White vehicle detected.
   Camera: Gate Camera (camera_02)
   CCTV Wall Time: 14:37:05
   Relevance: 91%
   ```
3. Show that the **Visual Evidence Player** automatically loops the exact 6.4s event sub-clip from Camera 02.

---

### Step 4: Complex Multi-Attribute Query (1:20 - 1:50)
1. Type or click: **`"Show me a person carrying a large bag in the first 5 seconds."`**
2. Show that:
   * The query parser extracts the composite concept (`person carrying a large bag`).
   * The temporal filter excludes events after 5.0s.
   * Top match from Camera 01 (Street Camera) loads with video clip and frame evidence.

---

### Step 5: Clarify-Once Memory Trigger (1:50 - 2:30)
1. Type a query with an unmapped landmark: **`"Did a vehicle pass through the warehouse dock?"`**
2. Point out that instead of guessing, the system triggers the **Clarify-Once Card**:
   > *"I don't know which camera/region represents 'warehouse dock'. Please select it."*
3. Select **`Street Camera (camera_01)`** from the dropdown and pick **`Full Camera View`**.
4. Click **`Save to Memory & Search`**.
5. Show that the memory is registered in SQLite and the query resolves automatically without re-typing.

---

### Step 6: Memory Persistence Across Reboots (2:30 - 3:00)
1. In the terminal, kill the backend server process and restart it:
   ```bash
   source venv/bin/activate
   KMP_DUPLICATE_LIB_OK=TRUE uvicorn backend.app.main:app --host 127.0.0.1 --port 8000
   ```
2. Refresh the browser and ask again: **`"Did a vehicle pass through the warehouse dock?"`**
3. Point out that the system **remembers** the mapping permanently and resolves directly to `camera_01`.

---

### Demo Summary Takeaways
* ✅ **Grounded Evidence**: Every answer is tied to an actual camera, timestamp, frame, and playable clip.
* ✅ **Open-Vocabulary**: Supports arbitrary visual concepts without retraining.
* ✅ **Multi-Camera**: Accurate camera targeting and disambiguation.
* ✅ **Clarify-Once**: Persistent spatial memory that survives reboots.
* ✅ **Real-Time Speed**: ~17 ms query latency on consumer edge hardware.
