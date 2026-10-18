# AI Vision Security — Complete Production Deployment Guide

This guide details the complete architecture, configuration, deployment, and verification procedure for deploying the **AI Vision Security — Multi-Camera CCTV Intelligence & Video Intelligence Lab** to persistent public cloud hosts (Render / Railway / Docker Linux VM) and Vercel for the frontend.

---

## 1. Cloud Architecture Overview

The deployed system decouples the static frontend interface from the persistent computer vision backend, preventing request timeouts during heavy deep-learning inference.

```
                           +-----------------------------------+
                           |         PUBLIC INTERNET           |
                           +-----------------------------------+
                                             |
                    +------------------------+------------------------+
                    |                                                 |
                    v                                                 v
       +--------------------------+                      +--------------------------+
       |    Frontend (Vercel)     |                      |  FastAPI Backend/Worker  |
       |  - Static / SPA Host     |                      |  - Render / Railway / VM |
       |  - Glassmorphic Web App  |                      |  - YOLO-World + ByteTrack|
       |  - Video Intelligence Lab|                      |  - Dynamic $PORT Binding |
       |  - Dual Video Player     |                      |  - VideoLabJobQueue      |
       +--------------------------+                      +--------------------------+
                    |                                                 |
                    | (REST APIs, Video Streaming, SSE Telemetry)     |
                    +-------------------------------------------------+
                                             |
                    +------------------------+------------------------+
                    |                                                 |
                    v                                                 v
       +--------------------------+                      +--------------------------+
       |   Object Storage / Disk  |                      |   Persistent Database    |
       |  - Cloudflare R2 / AWS S3|                      |  - SQLite (Persistent)   |
       |  - Original Videos       |                      |    or Managed PostgreSQL |
       |  - Annotated H.264 Clips |                      |  - Analysis Jobs         |
       |  - Evidence Keyframes    |                      |  - Detections & Tracking |
       +--------------------------+                      +--------------------------+
```

---

## 2. Environment Variables Specification

Configure these environment variables in your cloud hosting provider dashboard:

### A. Backend Variables (Render / Railway / Linux Host)

| Variable | Default / Example | Required | Purpose / Explanation |
| :--- | :--- | :--- | :--- |
| `PORT` | `8000` | **Yes** | Cloud provider assigns this dynamically (Render/Railway). Backend automatically binds to `0.0.0.0:$PORT`. |
| `APP_ENV` | `production` | **Yes** | Controls logging level and disables hot-reloading overhead. |
| `APP_DEBUG` | `false` | **Yes** | Disables debug introspection in production. |
| `DATABASE_URL` | `sqlite:////data/app.db` | **Yes** | Path to persistent database. On Render, `/data` is the mounted persistent disk volume. |
| `STORAGE_LOCAL_DIR` | `/data/storage` | **Yes** | Local persistent directory for uploads and annotated video artifacts when using disk storage. |
| `CORS_ORIGINS` | `https://*.vercel.app,http://localhost:3000` | **Yes** | Comma-separated list of allowed frontend origins. Regex supports all preview and production Vercel domains. |
| `MAX_UPLOAD_SIZE_MB` | `250.0` | No | Maximum file size allowed for video uploads (defaults to 250 MB). |
| `VIDEO_LAB_MAX_CONCURRENT_JOBS` | `1` | No | Limits active AI analysis jobs to prevent OOM on shared CPU/RAM tiers. Excess jobs queue safely. |
| `S3_BUCKET_NAME` | `cctv-vision-storage` | Optional | Set if using Cloudflare R2 or AWS S3 for object storage instead of persistent disk. |
| `S3_ENDPOINT_URL` | `https://<account-id>.r2.cloudflarestorage.com` | Optional | S3 endpoint URL (Required for Cloudflare R2 / MinIO). |
| `AWS_ACCESS_KEY_ID` | `your_r2_or_aws_access_key` | Optional | Object storage access key ID. |
| `AWS_SECRET_ACCESS_KEY` | `your_r2_or_aws_secret_key` | Optional | Object storage secret key. |
| `AWS_REGION` | `auto` | Optional | Object storage region (`auto` for R2, `us-east-1` for S3). |

### B. Frontend Variables (Vercel)

| Variable | Default / Example | Required | Purpose / Explanation |
| :--- | :--- | :--- | :--- |
| `VITE_API_URL` or `API_BASE_URL` | `https://ai-vision-backend.onrender.com` | No | The base URL of your deployed backend. Can also be set directly in the UI via the **Backend Cloud Settings** modal or via `vercel.json` rewrites. |

---

## 3. Step-by-Step Backend Deployment (Render)

The repository includes a ready-to-use [`render.yaml`](./render.yaml) blueprint that configures Docker, a persistent disk, health checks, and all required services.

### Steps for User Action:
1. Log into your [Render Dashboard](https://dashboard.render.com).
2. Click **New +** in the top navigation bar and select **Blueprint**.
3. Connect your GitHub account and select your repository: `hacknex24h` (or your repository fork).
4. Render detects [`render.yaml`](./render.yaml) and presents the blueprint preview:
   - **Service Name:** `ai-vision-security-backend`
   - **Runtime:** `Docker` (using [`Dockerfile`](./Dockerfile))
   - **Disk:** `ai-vision-data` (10 GB mounted at `/data`)
   - **Plan:** Free or Starter (Standard 2 GB+ RAM recommended for PyTorch + YOLO-World)
5. Click **Apply**.
6. Render builds the Docker container:
   - Installs Ubuntu runtime, FFmpeg, OpenCV libraries.
   - Installs Python dependencies from `requirements.txt`.
   - Pre-caches `yolov8s-world.pt` model weights inside the container image so startup is instant.
7. Once deployment finishes, Render displays your public backend URL:
   `https://ai-vision-security-backend.onrender.com`
8. Verify health status by opening:
   `https://ai-vision-security-backend.onrender.com/api/health`
   It should return:
   `{"status": "healthy", "service": "AI Vision Security Backend", ...}`

---

## 4. Step-by-Step Frontend Deployment (Vercel)

The frontend is located in the [`frontend/`](./frontend) directory and is configured with [`frontend/vercel.json`](./frontend/vercel.json) to handle routing, security headers, and API proxying.

### Steps for User Action:
1. Log into your [Vercel Dashboard](https://vercel.com).
2. Click **Add New...** -> **Project**.
3. Import your GitHub repository (`hacknex24h`).
4. In the **Configure Project** screen:
   - **Framework Preset**: Select `Other`.
   - **Root Directory**: Click `Edit` and select `frontend`.
   - **Build Command**: Leave empty (frontend is native Vanilla JS and CSS).
   - **Output Directory**: Leave empty (`.` or root of frontend folder).
5. In **Environment Variables**:
   - Add `API_BASE_URL` with your Render backend URL (e.g. `https://ai-vision-security-backend.onrender.com`).
6. Click **Deploy**.
7. Vercel builds and outputs your production URL:
   `https://hacknex24h.vercel.app` (or similar).

---

## 5. Persistent Storage Options

### Option 1: Persistent Disk Volume (Simplest — Included in Render Blueprint)
- Render automatically mounts a 10 GB persistent disk at `/data`.
- Uploaded videos, annotated MP4s, evidence keyframe crops, and `app.db` (SQLite) are stored at `/data/storage` and `/data/app.db`.
- Data survives container restarts, updates, and redeployments.

### Option 2: Cloudflare R2 / AWS S3 (Zero Egress Object Storage)
To use external S3-compatible cloud storage:
1. Create a bucket named `cctv-vision-storage` in Cloudflare R2 or AWS S3.
2. Generate an API Access Key and Secret Key with Read/Write permissions.
3. In your Render / Railway environment settings, set:
   - `S3_BUCKET_NAME=cctv-vision-storage`
   - `S3_ENDPOINT_URL=https://<account_id>.r2.cloudflarestorage.com`
   - `AWS_ACCESS_KEY_ID=<your_key_id>`
   - `AWS_SECRET_ACCESS_KEY=<your_secret_key>`
   - `AWS_REGION=auto`
4. The backend automatically switches to S3 mode:
   - Original uploads stream directly into the bucket.
   - Annotated videos and evidence images are uploaded with presigned URLs or public read URLs.
   - Deleting a job through the API or UI deletes all associated S3 objects automatically.

---

## 6. End-to-End User Verification Checklist

Once deployed, open your public Vercel website URL and execute this verification sequence:

| Step | User Action | Expected Cloud System Behavior |
| :--- | :--- | :--- |
| **1** | Open the public Vercel website | Dashboard loads with glowing glassmorphic interface and "Backend: Online". |
| **2** | Switch to "Video Intelligence Lab" tab | Dropzone, "Upload CCTV Footage" and "Load Sample CCTV" buttons are active. |
| **3** | Click "Load Sample CCTV" or drop an MP4 | Video pre-flight card calculates duration, dimensions (e.g. 634x360), FPS, and estimated analysis time. |
| **4** | Click "Analyze Video with YOLO-World & ByteTrack" | Progress bar updates dynamically with stage telemetry, frames processed, and detection rate. |
| **5** | Processing Completes | View transitions automatically to "Tracking Results & Telemetry" panel. |
| **6** | Inspect Video Players | Original video and Annotated video with persistent bounding boxes and Track IDs play smoothly. |
| **7** | Interactive Timeline Seek | Clicking any timestamp (e.g., `00:14`) seeks the video player directly to that frame. |
| **8** | Evidence Gallery | Displays high-resolution cropped keyframes for confirmed track detections. |
| **9** | Download Results | Clicking MP4, JSON, or CSV downloads verified export files to your device. |
| **10** | Refresh Webpage | Job history table retains completed analysis and permits viewing or deleting past jobs. |

---

## 7. Cloud Troubleshooting Guide

### Common Cloud Issues and Solutions:

#### 1. Backend Service Returns 502 / 503 on First Request (Cold Start)
- **Cause:** Render free tier services spin down after 15 minutes of inactivity.
- **Remedy:** Allow 40-50 seconds for the container to boot. PyTorch and model weights are pre-cached in Docker, so once booted it runs immediately.

#### 2. Video Fails to Play in Browser (Black screen or audio-only)
- **Cause:** Video was encoded with an incompatible codec or missing H.264 profile headers.
- **Remedy:** The backend includes a dedicated FFmpeg transcode step using `-c:v libx264 -pix_fmt yuv420p -movflags +faststart` to guarantee seamless playback across all browsers (Chrome, Safari, Firefox, Edge).

#### 3. Out of Memory (OOM) Killed During Inference
- **Cause:** Attempting to process high-resolution 4K streams with multiple concurrent jobs on a 512MB RAM tier.
- **Remedy:**
  - `VIDEO_LAB_MAX_CONCURRENT_JOBS` is set to `1` by default to serialize heavy jobs.
  - Video processing automatically downsamples oversized frames to 1280px max dimension.
  - Allocate at least 1 GB RAM (Render Starter plan or Railway Pro) for production workloads.

#### 4. CORS Error in Browser Console
- **Cause:** Request origin from custom Vercel domain is not recognized.
- **Remedy:** In Render dashboard, set `CORS_ORIGINS` to include your exact Vercel production domain, or use the dynamic regex pattern `https://.*\.vercel\.app` which is enabled by default in `backend/app/main.py`.

---

## 8. Summary of Hosting Costs & Limits

- **Frontend (Vercel):** Free tier includes unlimited deployments, 100 GB bandwidth/month, and free SSL.
- **Backend (Render):** Free tier includes 750 free instance hours/month and 512 MB RAM (spins down on idle). Persistent disk is $1/month for 10 GB on paid tier, or local container storage on free tier.
- **Object Storage (Cloudflare R2):** Free tier includes 10 GB storage/month, 10M Class B operations, and $0 egress fees.
