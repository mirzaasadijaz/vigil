# VIGIL (PPE_NEW)

Real-time AI surveillance dashboard — FastAPI backend (YOLOv11n + ByteTrack
person tracking, InsightFace SCRFD + ArcFace recognition, module-driven
analytics) serving a React/TypeScript SPA, backed by PostgreSQL.

## Features

- **Module-driven AI** per camera: Smart Attendance (face check-in/out),
  Head-counting / Entry-Exit, Occupancy, Idle-time monitoring, Intrusion
  (Phase 2/3: Mobile-usage, Fire/Smoke — pending weights).
- **Live feeds** over WebSocket (auto-reconnect), with a filterable grid and a
  portal-wide full-screen mode.
- **Cameras**: direct-IP cameras *and* NVR/DVR channels (bulk-add channels by
  range, resolved RTSP URLs, RTSP templates per brand).
- **Bulk module assignment**: apply one or more AI modules to many cameras /
  channels at once; geometry modules seed a full-frame zone you refine per camera.
- **Alerts** unified across modules, with zoomable snapshots + live toasts.
- **Attendance & HR**: employee registration, productive/idle time,
  multi-day CSV export.
- **RBAC** (admin / supervisor / operator / viewer) over JWT cookie auth;
  6 selectable dark themes; GPU (CUDA 12.1) deployment.

## Setup

See **[SETUP.md](SETUP.md)** for a complete, from-scratch installation guide
(environment, database, model weights, configuration, and running the app).

Quick start once prerequisites are installed:

```bash
conda create -n ppe_test python=3.11 -y && conda activate ppe_test
# install torch + onnxruntime for your hardware (see SETUP.md), then:
pip install -r requirements.txt
cp .env.example .env        # then edit paths/secrets
alembic upgrade head
python -m backend.scripts.create_admin
bash start.sh               # http://localhost:8000/
```
<!-- uvicorn backend.main:app --host 127.0.0.1 --port 8000    -->

## Other docs
- [SETUP.md](SETUP.md) — installation & first-run guide
- [RECOGNITION_TUNING.md](RECOGNITION_TUNING.md) — face-recognition tuning
- [RECOMMENDATIONS.md](RECOMMENDATIONS.md) — deployment recommendations
