# VIGIL (PPE_NEW) — Setup Guide

Real-time AI surveillance dashboard: a **FastAPI** backend ingests RTSP/webcam
video, runs **YOLOv11n + ByteTrack** (person detection/tracking) and
**InsightFace SCRFD + ArcFace** (face recognition) through a module-driven
pipeline, and serves a built **React/TypeScript** SPA. PostgreSQL is the database.

This guide takes a fresh machine from zero to a running app.

---

## 1. Prerequisites

Install these first (system package manager):

| Tool | Version | Notes |
|------|---------|-------|
| **Miniconda / Anaconda** | latest | Python env manager |
| **PostgreSQL** | 14+ | the database server |
| **Node.js + npm** | 18+ | only needed to rebuild the frontend |
| **Git** | any | |
| A C compiler (`build-essential` / `gcc`) | | required to build `insightface` |
| **NVIDIA GPU + driver** | driver ≥ 525 (supports CUDA 12.1) | this deployment runs on GPU |

This deployment is **GPU-based**. You need an NVIDIA GPU and a recent NVIDIA
driver — confirm with `nvidia-smi` (the "CUDA Version" it reports must be ≥ 12.1).
You do **not** need a system-wide CUDA toolkit: the PyTorch cu121 wheel bundles
its own CUDA runtime, and the cuDNN that `onnxruntime-gpu` needs is supplied from
the conda env via `start.sh`. (A CPU-only fallback is noted in step 3 for non-GPU
machines, but is not the target here.)

---

## 2. Get the code

```bash
git clone git@github.com:ahmadjaved24/PPE_NEW.git
cd PPE_NEW
```

---

## 3. Python environment

```bash
conda create -n ppe_test python=3.11 -y
conda activate ppe_test
```

Install **GPU PyTorch + onnxruntime-gpu** (do this BEFORE `requirements.txt`) —
this is the build/test config and the intended deployment:

```bash
pip install torch==2.5.1 torchvision==0.20.1 --index-url https://download.pytorch.org/whl/cu121
pip install onnxruntime-gpu==1.26.0
```

**Install the correct cuDNN (required).** `onnxruntime-gpu` needs cuDNN **9.3**.
The pip cuDNN wheel that gets pulled in is 9.1 and fails on Ampere with *"No DNN
support for stream"*. Install cuDNN 9.3 from conda **into the env** — `start.sh`
puts the env `lib` first on the library path, so this build wins over the pip one:
```bash
conda install -n ppe_test -c nvidia cudnn=9.3.0.75 -y
```

Then the rest:
```bash
pip install -r requirements.txt
pip install -r requirements-agent.txt   # chatbot / agent / RAG (needed: the backend imports it)
```

Chatbot: set `GEMINI_API_KEY` in `.env`, drop SOPs / policies into `data/rag/documents/<sop|policies|manuals>/`,
then run `python -m scripts.reindex_rag` (see `docs/AGENT_RAG.md`).

Verify the GPU is actually visible to PyTorch:
```bash
python -c "import torch; print('CUDA available:', torch.cuda.is_available(), '|', torch.cuda.get_device_name(0))"
```
It should print `CUDA available: True` and your GPU name. If it prints `False`,
the NVIDIA driver is too old or missing — fix that before continuing.

> **cuDNN — important.** With the conda cuDNN 9.3 installed above, `start.sh`
> prepends the env's `lib` (the working cuDNN 9.3) and the pip NVIDIA CUDA libs to
> `LD_LIBRARY_PATH`, so the good cuDNN wins over the pip 9.1 wheel. **Always launch
> via `bash start.sh`, not a bare `uvicorn` command** — otherwise face recognition
> fails with "No DNN support for stream". The InsightFace + YOLO providers are
> requested as `["CUDAExecutionProvider", "CPUExecutionProvider"]`, so they use the
> GPU when available and fall back to CPU only if it isn't.

**CPU-only fallback** (only for a machine without an NVIDIA GPU — not the target):
```bash
pip install torch==2.5.1 torchvision==0.20.1 --index-url https://download.pytorch.org/whl/cpu
pip install onnxruntime==1.26.0
pip install -r requirements.txt
pip install -r requirements-agent.txt
```

---

## 4. Database

Create the database and user (defaults match `.env.example`):

```bash
sudo -u postgres psql <<'SQL'
CREATE DATABASE ppe_new;
ALTER USER postgres WITH PASSWORD 'admin';
SQL
```

(Use your own user/password — just keep `DATABASE_URL` in `.env` in sync.)

Apply the schema migrations from the repo root:
```bash
alembic upgrade head
```

Create the first admin account (interactive; defaults to `admin` / `admin123`):
```bash
python -m backend.scripts.create_admin
```

---

## 5. Configuration (`.env`)

`.env` is git-ignored, so each machine needs its own:

```bash
cp .env.example .env
```

Edit `.env` and set, at minimum:
- `DATABASE_URL` — your Postgres credentials/host.
- `JWT_SECRET` — generate one: `python -c "import secrets; print(secrets.token_urlsafe(48))"`
- `BASE_DIR` — absolute path to this repo on this machine.
- `MEDIA_ROOT` — absolute path to `<repo>/media`.

Create the media directory (also git-ignored):
```bash
mkdir -p media/employees
```

---

## 6. AI model weights — they download automatically

You do **not** need to copy any model files manually. On first run:

| Model | Used for | How it's obtained |
|-------|----------|-------------------|
| `yolo11n.pt` | person detection + ByteTrack | Ultralytics downloads it into `BASE_DIR` on first use |
| `buffalo_l` (SCRFD + ArcFace `w600k_r50`) | face detect + recognition | InsightFace downloads it to `~/.insightface/models/buffalo_l/` on first use |

The first startup is therefore slower (it pulls ~a few hundred MB) and needs
internet access. To pre-seed on an air-gapped machine, copy `yolo11n.pt` into the
repo root and copy the `~/.insightface/models/buffalo_l/` folder from a machine
that already has it.

> Phase 2/3 modules (Mobile-usage, Fire/Smoke) require custom-trained weights
> that are not part of this repo yet; the Phase 1 modules above need nothing extra.

---

## 7. Frontend

The built SPA in `frontend/dist/` is committed and served directly by FastAPI,
so for a plain deployment you can **skip this section**.

Rebuild it only if you change frontend source:
```bash
cd frontend
npm install
npm run build      # outputs to frontend/dist (committed; served by FastAPI)
```
During active frontend work use `npm run dev` (proxies /api, /media, /stream to :8000).

---

## 8. Run

From the repo root, with the `ppe_test` env active:
```bash
bash start.sh
```

This launches `uvicorn backend.main:app` on `0.0.0.0:8000` after setting the
GPU library path. Watch for `[PERCEPTION] Ready.` — that means models are loaded.

Open **http://localhost:8000/** and log in with the admin account from step 4.

---

## 9. First-use checklist in the app

1. **RTSP Strings** — add an RTSP URL template for your NVR/DVR brand
   (e.g. `Streaming/Channels/{channel}`).
2. **NVR / DVR** — add your recorder, then click **Channels** to bulk-add
   channels (e.g. `1-16`) and set a frame rate. Each channel shows its resolved
   RTSP URL (copy into VLC to test). Assign AI modules to channels right here —
   per channel (with zone drawing) or in bulk across selected channels.
3. **IP Cameras** — for *direct-IP* cameras only (NVR channels live under NVR/DVR).
   Add cameras and bulk-assign AI modules; geometry modules get a full-frame zone
   you can refine per camera.
4. **Employees** — register people with a clear face photo (drives recognition
   + attendance). Photos are stored under `media/employees/`.
5. **Admin → Settings** — office open/close times, locations, departments, and
   the check-in/check-out camera roles.

---

## Troubleshooting

- **`uvicorn`/import errors, or face/onnx fails on GPU** — you launched without
  `start.sh`. Use `bash start.sh` so `LD_LIBRARY_PATH` is set.
- **"No DNN support for stream" / onnxruntime can't init CUDA** — wrong cuDNN.
  Confirm cuDNN 9.3 is in the env: `ls $(conda info --base)/envs/ppe_test/lib | grep libcudnn.so.9.3`.
  If missing, run `conda install -n ppe_test -c nvidia cudnn=9.3.0.75 -y` and
  always start with `bash start.sh`.
- **`torch.cuda.is_available()` is False** — NVIDIA driver too old/missing
  (need ≥ 525 for CUDA 12.1). Check with `nvidia-smi`.
- **DB connection refused** — Postgres isn't running, or `DATABASE_URL` is wrong.
- **`alembic` "target database is not up to date"** — run `alembic upgrade head`.
- **No video / camera shows inactive** — verify the RTSP URL works in VLC; the
  backend forces TCP transport. Check the camera's reachability (port 554).
- **First start hangs for a while** — it's downloading model weights (section 6).
- **Recognition quality** — see `RECOGNITION_TUNING.md`.

## Reference docs in this repo
- `CLAUDE.md` — architecture deep-dive (note: some stack details predate the
  move from DeepFace/TensorFlow to InsightFace/ONNX; this guide is authoritative
  for setup).
- `RECOGNITION_TUNING.md` — face-recognition threshold tuning.
- `RECOMMENDATIONS.md` — deployment recommendations.
