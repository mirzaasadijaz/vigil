#!/bin/bash
# Start VIGIL FastAPI backend.
# Usage:
#   bash start.sh          — run locally with the conda env (current machine)
#   bash start.sh docker   — build + run inside Docker (docker compose)
set -e

cd "$(dirname "$0")"

if [ "${1:-}" = "docker" ]; then
    echo "[VIGIL] Building + starting via Docker (CPU build)..."
    docker compose up -d --build
    echo "[VIGIL] Container 'ppe-backend' started. API + UI: http://<server-ip>:8002"
    echo "[VIGIL] Logs: docker compose logs -f"
    exit 0
fi

if [ "${1:-}" = "docker-gpu" ]; then
    echo "[VIGIL] Building + starting via Docker (GPU build)..."
    docker compose -f docker-compose.gpu.yml up -d --build
    echo "[VIGIL] GPU container 'ppe-backend' started. API + UI: http://<server-ip>:8001"
    echo "[VIGIL] Logs: docker compose -f docker-compose.gpu.yml logs -f"
    exit 0
fi

# ── Local (conda env) mode ───────────────────────────────────────────────────
# Conda env that has the working AI + web stack.
ENV_NAME="ppe"
ENV_PREFIX="$(conda info --base)/envs/${ENV_NAME}"
PYBIN="${ENV_PREFIX}/bin/python"

# ── cuDNN fix ──────────────────────────────────────────────────────────────────
# TensorFlow 2.21 needs cuDNN 9.3. The pip wheel ships cuDNN 9.1.0.70 which fails
# with "No DNN support for stream" on this GPU; the conda env/lib has 9.3.0 which
# works. Put env/lib FIRST, then the nvidia pip CUDA libs.
NV_LIBS="$(find "${ENV_PREFIX}/lib/python3.11/site-packages/nvidia" -name lib -type d 2>/dev/null | tr '\n' ':')"
export LD_LIBRARY_PATH="${ENV_PREFIX}/lib:${NV_LIBS}${LD_LIBRARY_PATH}"

# Keep TF logs quiet.
export TF_CPP_MIN_LOG_LEVEL="${TF_CPP_MIN_LOG_LEVEL:-2}"

echo "[VIGIL] Starting FastAPI backend (env=${ENV_NAME}, GPU DeepFace)..."
exec "${PYBIN}" -m uvicorn backend.main:app --host 0.0.0.0 --port 8001
