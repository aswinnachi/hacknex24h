# ==============================================================================
# AI Vision Security — Persistent Production Backend Dockerfile
# ==============================================================================
FROM python:3.10-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    DEBIAN_FRONTEND=noninteractive

WORKDIR /app

# Install system dependencies including FFmpeg for video transcoding and OpenCV requirements
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    git \
    ffmpeg \
    libsm6 \
    libxext6 \
    libgl1 \
    libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements and install Python dependencies
COPY requirements.txt /app/
RUN pip install --upgrade pip setuptools wheel && \
    pip install -r requirements.txt

# Copy project files
COPY . /app/

# Create persistent storage directories
RUN mkdir -p /app/data/storage /app/data/frames /app/data/evidence /app/data/clips /app/database

# Initialize database schema and pre-warm model weights into container image
RUN python -c "from backend.app.db.init_db import init_db; init_db()" && \
    python -c "from ultralytics import YOLOWorld; YOLOWorld('yolov8s-world.pt')"

# Expose default port (Render/Railway sets PORT dynamically)
ENV PORT=8000
EXPOSE 8000

# Start Uvicorn bound to dynamic host port
CMD ["sh", "-c", "uvicorn backend.app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
