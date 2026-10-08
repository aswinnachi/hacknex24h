#!/usr/bin/env bash
# ==============================================================================
# Multi-Stream Video Intelligence — Start Server
# ==============================================================================
set -e

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJECT_DIR"

if [ -f "venv/bin/activate" ]; then
    source venv/bin/activate
fi

HOST="${HOST:-127.0.0.1}"
PORT="${PORT:-8000}"

echo "=========================================================="
echo "🚀 Starting Multi-Stream Video Intelligence API..."
echo "📡 Listening on: http://${HOST}:${PORT}"
echo "💻 Frontend UI: http://${HOST}:${PORT}/"
echo "📚 Swagger API Docs: http://${HOST}:${PORT}/docs"
echo "=========================================================="

exec uvicorn backend.app.main:app --host "$HOST" --port "$PORT" --reload
