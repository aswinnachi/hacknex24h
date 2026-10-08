# Evaluation & Benchmark Report — HNX26EPS05

This document details the quantitative evaluation metrics measured across the grounded precision benchmark suite (`scripts/evaluate.py`).

---

## 1. Measured Performance Metrics

* **Evaluation Date:** October 8, 2026
* **Hardware Environment:** Apple M-Series (Apple Silicon) with MPS Acceleration
* **Models Evaluated:**
  * Detector: **YOLO-World-S** (`yolov8s-world.pt`)
  * Embedder: **OpenAI CLIP ViT-B/16** (`openai/clip-vit-base-patch16`, 512-d normalized vectors)
  * Vector Index: **FAISS FlatIP** (L2-normalized cosine similarity)
  * Attribute Engine: **HSV/RGB Histogram + Centroid Trajectory Analyzer**

| Metric | Target | Measured Result | Status |
| :--- | :--- | :--- | :--- |
| **Grounded Retrieval Accuracy** | ≥ 90.0% | **100.0%** (16/16 queries passed) | ✅ EXCEEDED |
| **Negative Attribute Rejection Rate** | 100.0% | **100.0%** (Zero false-positive CLIP leakage) | ✅ MET |
| **Discrete Track Counting Accuracy** | 100.0% | **100.0%** (`COUNT(DISTINCT tracking_id)`) | ✅ MET |
| **Average Query Latency** | < 50 ms | **14.41 ms** | ✅ EXCEEDED |
| **Average Evidence Gen Latency** | < 150 ms | **27.22 ms** | ✅ EXCEEDED |
| **Clarification Trigger Accuracy** | 100.0% | **100.0%** (Prompted on unmapped landmarks) | ✅ MET |

---

## 2. Detailed Query Benchmark Results

| ID | Query | Expected Verdict | Actual Verdict | Status | Latency | Grounding Verification Details |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **Q01** | *"Show the white car"* | `MATCH` | `MATCH` | **PASS** | 37.0 ms | `camera_02` \| car \| Track #61 |
| **Q02** | *"Show the moving white car"* | `MATCH` | `MATCH` | **PASS** | 25.3 ms | `camera_02` \| car \| Track #61 |
| **Q03** | *"Show the red car"* | `NO MATCH` | `NO MATCH` | **PASS** | 20.6 ms | Strictly Rejected (0 False Positives) |
| **Q04** | *"Show the moving red car"* | `NO MATCH` | `NO MATCH` | **PASS** | 12.5 ms | Strictly Rejected (0 False Positives) |
| **Q05** | *"Show the person"* | `MATCH` | `MATCH` | **PASS** | 12.2 ms | `camera_01` \| person \| Track #2 |
| **Q06** | *"Show the moving person"* | `MATCH` | `MATCH` | **PASS** | 16.3 ms | `camera_01` \| person \| Track #2 |
| **Q07** | *"Show the person with a bag"* | `NO MATCH` | `NO MATCH` | **PASS** | 15.6 ms | Strictly Rejected (No bag present) |
| **Q08** | *"Show the motorcycle"* | `NO MATCH` | `NO MATCH` | **PASS** | 15.8 ms | Strictly Rejected (No motorcycle present) |
| **Q09** | *"How many people passed through the camera?"* | `COUNT RESULT` | `COUNT RESULT` | **PASS** | 0.5 ms | Counted: 2 unique person tracks |
| **Q10** | *"How many cars passed through the camera?"* | `COUNT RESULT` | `COUNT RESULT` | **PASS** | 0.5 ms | Counted: 4 unique vehicle tracks |
| **Q11** | *"Did a red car pass?"* | `NO` | `NO` | **PASS** | 17.6 ms | Strictly Rejected (0 False Positives) |
| **Q12** | *"Did the white car pass?"* | `YES` | `YES` | **PASS** | 18.0 ms | `camera_02` \| car \| Track #61 |
| **Q13** | *"What objects were detected?"* | `INVENTORY` | `INVENTORY` | **PASS** | 0.5 ms | Summary: 3 bicycle(s), 4 car(s), 2 person(s) |
| **Q14** | *"What happened in the first 5 seconds?"* | `MATCH` | `MATCH` | **PASS** | 19.8 ms | `camera_02` \| car \| Track #61 (0.8s-5.0s) |
| **Q15** | *"Did a vehicle pass through the main gate?"* | `YES` | `YES` | **PASS** | 18.0 ms | `camera_02` \| Clarify-Once memory resolved |
| **Q16** | *"Did a vehicle pass the unknown perimeter fence?"* | `CLARIFICATION REQUIRED` | `CLARIFICATION REQUIRED` | **PASS** | 0.5 ms | Clarify-Once modal triggered |

---

## 3. Error Handling & Edge Case Validation

| Test Case | Method / Payload | Expected HTTP | Observed HTTP | Result |
| :--- | :--- | :--- | :--- | :--- |
| **Invalid Evidence UUID** | `GET /api/evidence/non-existent-uuid` | 404 Not Found | 404 | **PASS** |
| **Invalid Memory Lookup** | `GET /api/memory/non-existent-landmark`| 404 Not Found | 404 | **PASS** |
| **Empty Query String** | `POST /api/query` with `{"query": ""}` | 200 (Graceful 0 results)| 200 | **PASS** |

---

## 4. Scope & System Boundaries (No Overclaiming)

1. **Multi-Camera Tracking**: Tracking and ByteTrack IDs operate deterministically within each camera. Cross-camera Re-ID (linking identity across different physical cameras) is a future extension.
2. **Footage Mode**: Ingests recorded CCTV video files (MP4, MOV, AVI, MKV).
3. **Memory Layer**: Clarify-Once memory stores persistent landmarks and regions in SQLite, decoupled from transient chat messages.
