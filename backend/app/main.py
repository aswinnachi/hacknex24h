import os
import signal

os.environ["KMP_DUPLICATE_LIB_OK"] = "TRUE"
os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = "reconnect;1|reconnect_streamed;1|reconnect_delay_max;5"

try:
    signal.signal(signal.SIGPIPE, signal.SIG_IGN)
except Exception:
    pass

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from backend.app.core.config import settings
from backend.app.core.logging import logger
from backend.app.core.hardware import detect_hardware

from backend.app.api.cameras import router as cameras_router
from backend.app.api.process import router as process_router
from backend.app.api.events import router as events_router
from backend.app.api.search import router as search_router
from backend.app.api.query import router as query_router
from backend.app.api.evidence import router as evidence_router
from backend.app.api.memory import router as memory_router
from backend.app.api.alerts import router as alerts_router
from backend.app.api.video_lab import router as video_lab_router

app = FastAPI(
    title="HNX26EPS05 Multi-Stream Video Intelligence",
    description="Backend for Multi-Stream Video Intelligence with Conversational Query",
    version="0.1.0",
)

@app.on_event("startup")
def startup_warmup():
    try:
        from backend.app.core.embeddings import get_embedder
        get_embedder()
    except Exception as e:
        logger.warning(f"Embedding warmup warning: {e}")

    try:
        from backend.app.core.video_lab import get_job_queue
        q = get_job_queue()
        q.recover_orphaned_jobs()
    except Exception as e:
        logger.warning(f"VideoLab queue recovery warning: {e}")

    def _auto_connect_live_streams():
        import time
        import threading
        time.sleep(2.0)
        try:
            from backend.app.db.database import execute_query
            from backend.app.core.stream_manager import get_stream_manager
            mgr = get_stream_manager()
            rows = execute_query(
                "SELECT id, name, video_path, source_type FROM cameras WHERE source_type IN ('live', 'youtube_live') ORDER BY created_at DESC",
                fetch=True
            )
            # Concurrently initialize the top 4 visible cameras for instant matrix tracking
            def _connect_worker(row_dict):
                try:
                    c_id = row_dict["id"]
                    existing = mgr.get_stream(c_id)
                    if not (existing and existing.is_connected):
                        mgr.connect_camera(
                            camera_id=c_id,
                            name=row_dict.get("name") or c_id,
                            stream_url=row_dict["video_path"],
                            source_type=row_dict.get("source_type", "youtube_live"),
                            timeout=25.0,
                            record_stream=False
                        )
                except Exception as stream_err:
                    logger.warning(f"[AutoConnect] Error starting stream {row_dict.get('id')}: {stream_err}")

            threads = []
            for r in (rows or [])[:4]:
                t = threading.Thread(target=_connect_worker, args=(dict(r),), daemon=True)
                t.start()
                threads.append(t)
        except Exception as startup_err:
            logger.warning(f"[AutoConnect] Startup stream initialization error: {startup_err}")

    import threading
    threading.Thread(target=_auto_connect_live_streams, daemon=True, name="AutoConnectStartup").start()



app.include_router(cameras_router, prefix="/api", tags=["Cameras"])
app.include_router(process_router, prefix="/api", tags=["Process"])
app.include_router(events_router, prefix="/api", tags=["Events"])
app.include_router(search_router, prefix="/api", tags=["Search"])
app.include_router(query_router, prefix="/api", tags=["Query"])
app.include_router(evidence_router, prefix="/api", tags=["Evidence"])
app.include_router(memory_router, prefix="/api", tags=["Memory"])
app.include_router(alerts_router, prefix="/api", tags=["Alerts"])
app.include_router(video_lab_router, prefix="/api", tags=["Video Lab"])



# Mount data static files for frames and clips
data_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "data"))
if os.path.exists(data_dir):
    app.mount("/data", StaticFiles(directory=data_dir), name="data")


# Dynamic CORS configuration
cors_origins_env = os.getenv("CORS_ORIGINS", "*")
if cors_origins_env.strip() == "*":
    origins = ["*"]
    origin_regex = None
else:
    origins = [o.strip() for o in cors_origins_env.split(",") if o.strip()]
    origin_regex = r"https://.*\.vercel\.app"

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_origin_regex=origin_regex,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/api/health")
async def health_check():
    hw_info = detect_hardware()
    logger.info("Health check requested")
    return {
        "status": "ok",
        "service": "HNX26EPS05 Multi-Stream Video Intelligence API",
        "hardware": hw_info
    }

# Mount frontend static files
frontend_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "frontend"))
if os.path.exists(frontend_dir):
    app.mount("/", StaticFiles(directory=frontend_dir, html=True), name="frontend")
else:
    logger.warning(f"Frontend directory not found at {frontend_dir}")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("backend.app.main:app", host=settings.api_host, port=settings.api_port, reload=settings.app_debug)
