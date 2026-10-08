import os
from dotenv import load_dotenv
load_dotenv()
os.environ.setdefault("TF_USE_LEGACY_KERAS", "1")
os.environ.setdefault("OPENCV_FFMPEG_CAPTURE_OPTIONS", "rtsp_transport;tcp")
os.environ.setdefault("OMP_NUM_THREADS", "1")
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
os.environ.setdefault("MKL_NUM_THREADS", "1")

# Persist this process's output to logs/backend.log before anything else prints, so the
# boot sequence - which is where most of the interesting failures announce themselves -
# is in the file too and not just on a terminal nobody is watching. RUN_LOG=0 disables.
from backend.core.logging_setup import (init_run_log, log_parameters,
                                        roll_module_logs, prepare_module_logs)
init_run_log("backend")
# Apply the per-module log size cap now, from the main process, BEFORE the perception
# child exists - both processes append to those files and only a lone process may
# safely rename them. See the note in logging_setup. Then make sure every module in the
# registry has a file, so a silent module reads as silent rather than as missing.
roll_module_logs()
prepare_module_logs()

# Disable OpenCV's internal threadpool. Its OpenMP-based parallel_for collides
# with TensorFlow (DeepFace/ArcFace) inference running in the face-worker thread,
# corrupting the native heap (free(): invalid pointer / SIGABRT) once multiple
# camera threads are active. Must run before any heavy cv2 op.
import cv2
# OpenCV threading in the MAIN process (optimisation plan item 4).
#
# This was 0 - fully single-threaded - and that is why it stays 0 by default. The reason
# is recorded in CLAUDE.md: torch + ONNX + libpq + FFmpeg in one address space corrupted
# the native heap, and pinning every threading knob was part of making the stack stable.
#
# BUT that constraint belongs to the perception CHILD, which owns torch. This process has
# no torch at all - the three-tier design guarantees it - so the contention that motivated
# the pin does not exist here. Decode and JPEG encode, which are the main process's whole
# CPU cost, are both threadable.
#
# It remains OFF by default because "probably safe" is not good enough for a change that
# previously corrupted memory. Raise MAIN_CV_THREADS to 2-4, measure with PERF_DEBUG=1,
# and set it back to 0 at the first sign of instability - one variable, instant rollback.
_cv_threads = int(os.environ.get("MAIN_CV_THREADS", "0"))
cv2.setNumThreads(_cv_threads)
if _cv_threads:
    print(f"[INIT] OpenCV threads in the main process: {_cv_threads} "
          f"(MAIN_CV_THREADS; set 0 to restore single-threaded behaviour)", flush=True)

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from backend.core.database import create_tables
from backend.core.config import settings
from backend.services.perception import start_perception, stop_perception
from backend.services.connection_manager import connection_manager
from backend.services import hr_sync

from backend.routers import (
    auth,
    users,
    employees,
    cameras,
    nvrs,
    rtsp_templates,
    alerts,
    attendance,
    settings as settings_router,
    mode,
    stream,
    analytics,
    system,
    mess,
    chat,
)

DIST_DIR = Path(__file__).resolve().parent.parent / "frontend" / "dist"
MEDIA_DIR = Path(settings.MEDIA_ROOT)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Dump every resolved tunable once, here rather than at import time, so the block
    # reflects the fully-imported process (perception's thresholds included) and lands
    # at the top of each run's log. Credentials are written as ***.
    log_parameters("backend")
    await create_tables()
    connection_manager.set_loop(asyncio.get_running_loop())
    # Employees now come from the HR API. Sync once BEFORE the perception process
    # starts so its initial gallery already contains the fresh avatars; a background
    # task keeps it in sync afterwards. Best-effort — a failing HR API never blocks boot.
    sync_task: asyncio.Task | None = None
    try:
        await asyncio.to_thread(hr_sync.sync_employees)
    except Exception as e:
        print(f"[HR-SYNC] startup sync failed: {e}", flush=True)

    # All AI detectors (YOLOv11n+ByteTrack person, SCRFD+InsightFace face) run in a
    # SEPARATE spawned process (see perception). The main process owns DB + streaming;
    # the child owns the GPU. stop_perception() releases VRAM on shutdown.
    start_perception()

    async def _periodic_hr_sync():
        interval = max(1, settings.HR_SYNC_INTERVAL_MINUTES) * 60
        while True:
            await asyncio.sleep(interval)
            try:
                await asyncio.to_thread(hr_sync.sync_employees)
            except Exception as e:
                print(f"[HR-SYNC] periodic sync failed: {e}", flush=True)

    sync_task = asyncio.create_task(_periodic_hr_sync())
    yield
    if sync_task is not None:
        sync_task.cancel()
    stop_perception()


app = FastAPI(title="Core Sentry", version="2.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# API routers
for router_module in [
    auth, users, employees, cameras, nvrs, rtsp_templates,
    alerts, attendance, settings_router, mode, stream, analytics, system, mess,
    chat
]:
    app.include_router(router_module.router, prefix="/api")

# Media files
MEDIA_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/media", StaticFiles(directory=str(MEDIA_DIR)), name="media")

# Static frontend assets — only if dist exists
if DIST_DIR.exists():
    _assets = DIST_DIR / "assets"
    if _assets.exists():
        app.mount("/static/assets", StaticFiles(directory=str(_assets)), name="assets")
    app.mount("/static", StaticFiles(directory=str(DIST_DIR)), name="static_root")

    @app.get("/", include_in_schema=False)
    @app.get("/{full_path:path}", include_in_schema=False)
    async def spa_fallback(full_path: str = ""):
        if full_path.startswith("api/") or full_path.startswith("media/") or full_path.startswith("static/"):
            from fastapi import HTTPException
            raise HTTPException(status_code=404)
        file = DIST_DIR / full_path
        if full_path and file.exists() and file.is_file():
            return FileResponse(str(file))
        index = DIST_DIR / "index.html"
        if index.exists():
            return FileResponse(str(index))
        return {"detail": "Frontend not built yet. Run: cd frontend && npm run build"}
