import os
os.environ["KMP_DUPLICATE_LIB_OK"] = "TRUE"

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

app = FastAPI(
    title="HNX26EPS05 Multi-Stream Video Intelligence",
    description="Backend for Multi-Stream Video Intelligence with Conversational Query",
    version="0.1.0",
)

app.include_router(cameras_router, prefix="/api", tags=["Cameras"])
app.include_router(process_router, prefix="/api", tags=["Process"])
app.include_router(events_router, prefix="/api", tags=["Events"])
app.include_router(search_router, prefix="/api", tags=["Search"])
app.include_router(query_router, prefix="/api", tags=["Query"])
app.include_router(evidence_router, prefix="/api", tags=["Evidence"])
app.include_router(memory_router, prefix="/api", tags=["Memory"])



# Mount data static files for frames and clips
data_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "data"))
if os.path.exists(data_dir):
    app.mount("/data", StaticFiles(directory=data_dir), name="data")


# Allow CORS for frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
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
