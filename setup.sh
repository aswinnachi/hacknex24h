#!/usr/bin/env bash
# ==============================================================================
# Multi-Stream Video Intelligence — Automated Batch Setup Script
# ==============================================================================
set -e

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJECT_DIR"

echo "=========================================================="
echo "🎥 Multi-Stream Video Intelligence — Setup"
echo "=========================================================="

# 1. Check Python
PYTHON_CMD=""
if command -v python3.9 &> /dev/null; then
    PYTHON_CMD="python3.9"
elif command -v python3 &> /dev/null; then
    PYTHON_CMD="python3"
else
    echo "❌ Error: Python 3.9+ is required but was not found."
    exit 1
fi
echo "✓ Using Python: $($PYTHON_CMD --version)"

# 2. Virtual Environment
if [ ! -d "venv" ]; then
    echo "📦 Creating virtual environment in ./venv..."
    $PYTHON_CMD -m venv venv
else
    echo "✓ Existing virtual environment found in ./venv"
fi

source venv/bin/activate
pip install --upgrade pip setuptools wheel --quiet

# 3. Install Dependencies
echo "📦 Installing Python dependencies from requirements.txt..."
pip install -r requirements.txt --quiet
echo "✓ Dependencies installed."

# 4. Hardware Acceleration Check
echo "🔍 Checking hardware acceleration..."
python -c "
import torch
if torch.cuda.is_available():
    print(f'✓ GPU Acceleration: CUDA ({torch.cuda.get_device_name(0)})')
elif hasattr(torch.backends, 'mps') and torch.backends.mps.is_available():
    print('✓ Hardware Acceleration: Apple Silicon Metal Performance Shaders (MPS)')
else:
    print('ℹ Running on CPU (Optimized fallbacks active)')
"

# 5. Model Weights Check
if [ ! -f "yolov8s-world.pt" ]; then
    echo "⬇ Downloading YOLO-World base model (yolov8s-world.pt)..."
    python -c "from ultralytics import YOLOWorld; YOLOWorld('yolov8s-world.pt')"
else
    echo "✓ YOLO-World model found: yolov8s-world.pt"
fi

# 6. Database & Storage Initialization
mkdir -p data/videos data/frames data/clips database/indexes
echo "✓ Storage directories verified."

# 7. Pre-flight verification
echo "🧪 Running pre-flight self-check..."
python -c "
from backend.app.db.database import init_db
init_db()
from backend.app.core.detector import get_detector
det = get_detector()
assert det.available, 'Detector failed to initialize'
from backend.app.core.embeddings import get_embedder
emb = get_embedder()
assert emb.available, 'Embedder failed to initialize'
print(f'✓ Database, YOLO-World ({det.model_name}), and Embedder ({emb.model_name}) verified.')
"

echo ""
echo "=========================================================="
echo "🎉 Setup complete! You can now start the server:"
echo "   ./run.sh"
echo "   Or: uvicorn backend.app.main:app --host 0.0.0.0 --port 8000 --reload"
echo "=========================================================="
