# VIGIL / PPE_NEW — FastAPI + AI backend (CPU build, portable).
#
# Build-time toggle: ARG GPU=true -> GPU wheels (needs NVIDIA GPU + nvidia-container-toolkit
# on the host; use docker-compose.gpu.yml). GPU=false (default) -> portable CPU build.
# The cu121 / onnxruntime-gpu pip wheels bundle their own CUDA/cuDNN libs, so the base
# image stays the same. See requirements.txt for the wheel matrix.
FROM python:3.11-slim
ARG GPU=false

# System libs: cv2 (libGL/libglib/libsm/libxext), onnxruntime/torch (libgomp),
# insightface build (gcc/python3-dev), ffmpeg for OpenCV RTSP.
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgl1 libglib2.0-0 libsm6 libxext6 libgomp1 \
    ffmpeg curl git \
    build-essential python3-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# ── Python deps ──────────────────────────────────────────────────────────────
# torch CPU + onnxruntime CPU first (per requirements.txt), then the rest.
COPY requirements.txt .
# Cython + numpy first: insightface needs them to compile (they come later in requirements.txt)
RUN pip install --no-cache-dir cython==3.2.5 numpy==2.4.4 \
    && if [ "$GPU" = "true" ]; then \
           pip install --no-cache-dir \
               torch==2.5.1 torchvision==0.20.1 \
               --index-url https://download.pytorch.org/whl/cu121 \
           && pip install --no-cache-dir onnxruntime-gpu==1.26.0; \
       else \
           pip install --no-cache-dir \
               torch==2.5.1 torchvision==0.20.1 \
               --index-url https://download.pytorch.org/whl/cpu \
           && pip install --no-cache-dir onnxruntime==1.26.0; \
       fi \
    && pip install --no-cache-dir -r requirements.txt

# Chatbot / agent / RAG (LangChain + Gemini + Chroma). chat.py is imported at startup,
# so these are required for the backend to boot.
COPY requirements-agent.txt .
RUN pip install --no-cache-dir -r requirements-agent.txt

# ── App ──────────────────────────────────────────────────────────────────────
COPY backend/       /app/backend/
COPY scripts/       /app/scripts/
COPY data/          /app/data/
COPY models/        /app/models/
COPY frontend/dist  /app/frontend/dist/

# Pre-download insightface buffalo_l models so the image is self-contained.
# Falls back to a runtime download if the build machine has no network.
RUN python -c "from insightface.app import FaceAnalysis; import time; t=time.time(); FaceAnalysis(name='buffalo_l', providers=['CPUExecutionProvider']); print('buffalo_l ready', time.time()-t)" || echo "insightface download deferred to first run"

ENV BASE_DIR=/app \
    MEDIA_ROOT=/app/media \
    PORT=8001 \
    PYTHONUNBUFFERED=1

RUN mkdir -p /app/media

EXPOSE 8001

HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8001/', timeout=4)"

CMD ["python", "-m", "uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8001"]
