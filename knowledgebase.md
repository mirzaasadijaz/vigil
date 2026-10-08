# VIGIL — System Understanding Reference

**Repository:** `PPE_NEW` (DB `ppe_new`), product name **VIGIL** — "Workplace Intelligence Platform"
**Branch this document describes:** `stagging` (HEAD `eee6d86`, "multi images+employe card")
**Default PR branch:** `fastapi-migration` · **SaaS work-in-progress branch:** `sass-under-testing`
**Document generated:** 2026-08-25
**Purpose:** a complete, self-contained briefing for an AI assistant doing deep research
on this codebase — models, architecture, stack, use cases, current approaches, gaps.

> This file describes *the code as it exists*, not a design proposal. Where the code
> contradicts a docstring or an older document, both are stated and the code wins.

---

## 1. What the system is

VIGIL is a **real-time AI video-surveillance and workplace-analytics platform** for a
single-site deployment. It ingests RTSP streams from IP cameras / NVRs (or a local
webcam), runs a **module-driven** computer-vision pipeline on an NVIDIA GPU, writes every
detection into one unified event table, and serves a React SPA showing live annotated
video, alerts, attendance records and analytics rollups.

The distinguishing design idea is the **module system**. A camera is not "an attendance
camera" or "a counting camera" — each camera is assigned a JSON list of *modules*
(attendance, headcount, occupancy, idle, intrusion, smoking, mobile, fire/smoke, re-id).
Each module declares which detector **backbone** it needs. The perception worker loads
only the backbones actually required across the assigned modules, runs each backbone
**once per frame per camera**, and fans the resulting primitives out to cheap stateful
rule consumers. Adding an analytic is therefore a registry entry plus a rule class, not a
new model pipeline.

### Primary use cases

| Use case | Module | What it produces |
|---|---|---|
| Staff check-in / check-out by face | `attendance` | `attendance` rows + `checkin` / `checkout` / `unknown` events |
| Directional people counting at a door | `headcount` | `entry` / `exit` events, running IN/OUT totals |
| Live zone occupancy vs capacity | `occupancy` | `over_capacity` events, live count overlay |
| Unmanned workstation / motionless worker / loitering | `idle` | escalating `idle` events (50 % / 75 % / 100 % of dwell) |
| Restricted-zone intrusion, optionally time-scheduled | `intrusion` | `intrusion` events |
| Smoking detection | `smoking` | `smoking` events |
| Phone usage on the floor | `mobile` | `phone_use` events, name-tagged when face recognition is on |
| Fire and smoke detection | `fire_smoke` | `fire` / `smoke` events |
| Keep an identity bound when the face is turned away | `reid` ("Vectoring") | no events — it *improves* the other modules |

Deployment target: **one machine, one NVIDIA GPU (CUDA 12.1)**, conda env named `ppe`.

---

## 2. Technology stack

### Backend (Python 3.11)

| Layer | Choice | Version | Notes |
|---|---|---|---|
| Web framework | FastAPI | 0.137.2 | app title is still `Core Sentry`, v2.0.0 |
| ASGI server | uvicorn[standard] | 0.49.0 | started via `start.sh` / `start.bat` |
| ORM | SQLAlchemy 2.x (async + sync) | 2.0.51 | typed `Mapped[]` / `mapped_column` style |
| DB drivers | asyncpg 0.31.0 (main), psycopg2-binary 2.9.12 (perception writer) | | two drivers on purpose — see §4 |
| Migrations | Alembic | 1.18.4 | `backend/alembic`, **two heads** |
| Validation | Pydantic + pydantic-settings | 2.13.4 / 2.14.2 | `backend/core/config.py` reads `.env` |
| Auth | python-jose (JWT HS256) + bcrypt 5.0.0 | | HttpOnly cookie, access + refresh |
| Uploads | python-multipart | 0.0.32 | employee photos |
| CV runtime | OpenCV-python | 4.13.0.92 | capture, JPEG, overlay drawing |
| DL runtime | PyTorch 2.5.1 + torchvision 0.20.1 (cu121) | | **not** in `requirements.txt` — installed first, hardware-specific |
| ONNX runtime | onnxruntime-gpu 1.26.0 | | needs **cuDNN 9.3**; the pip wheel's 9.1 fails with `No DNN support for stream` |
| Detection framework | ultralytics | 8.4.48 | YOLO + ByteTrack |
| Face | insightface | 0.7.3 | SCRFD + ArcFace, `buffalo_l` pack |
| Numerics | numpy 2.4.4, pillow 12.1.1, scikit-image 0.26.0 | | |
| Misc | ping3 5.1.5 | | camera reachability checks |

### Frontend

| Layer | Choice | Version |
|---|---|---|
| Framework | React | 19.2.5 |
| Language | TypeScript | ~6.0.2 |
| Bundler | Vite | 8.0.10 |
| Styling | Tailwind CSS v4 (`@tailwindcss/vite`) + CSS custom properties | 4.2.4 |
| Charts | Recharts | 3.8.1 |
| Icons | lucide-react | 0.469.0 |
| Lint | eslint 10 + typescript-eslint 8 | |

### Storage / packaging

- **PostgreSQL** — sole datastore. Snapshots and employee photos live on disk under
  `media/` (git-ignored), referenced by relative path from DB rows.
- **`frontend/dist/` is committed** and served by FastAPI. A frontend change is not live
  until `npm run build` runs *and* `dist/` is committed.
- **PyInstaller** bundle (`VIGIL.spec`, entry `launcher.py`, built by `build_exe.bat`)
  for a Windows `.exe`. Note the spec **excludes** `torch`, `tensorflow`, `deepface`,
  `cv2` and `ultralytics`, and references a `yolo11n.pt` the runtime no longer uses — the
  bundle is a launcher shell, not a self-contained AI deployment.
- **No test suite on this branch.** `tests/` contains only `__pycache__`. Root-level
  `test_rtsp.py`, `phone_check.py`, `export_gpu.py` and `ppe_detection.py` are one-off
  scripts. (The `sass-under-testing` branch does have a real pytest suite — see §14.)

---

## 3. Repository map

```
backend/
  main.py                     FastAPI app, lifespan, static/SPA mount, thread pinning
  core/
    config.py                 pydantic-settings; DATABASE_URL, JWT_*, MEDIA_ROOT, BASE_DIR
    database.py               async engine, Base, create_tables(), get_db()
    security.py               bcrypt hashing, JWT access/refresh encode + decode
    dependencies.py           get_current_user (cookie), require_role(...) factories
  models/                     SQLAlchemy ORM  (see §8)
  schemas/                    Pydantic request/response models
  routers/                    13 routers, all mounted under /api  (see §9)
  services/
    perception.py             * 974 lines — the whole AI pipeline + DB writer
    camera_manager.py         capture + AI threads per camera, overlay drawing, backoff
    connection_manager.py     per-camera WebSocket fan-out, thread -> loop bridge
    embedding_cache.py        per-employee face gallery, cosine matching, auto-enrol
    modules/registry.py       * single source of truth for the module catalogue
    analytics/                rule consumers: headcount, occupancy, idle, intrusion,
                              mobile, fire_smoke  (+ base.py geometry helpers)
    detectors/                thin model wrappers: person, face, reid, smoking,
                              fire_smoke, phone
    face_worker.py            DEAD (DeepFace era, superseded by perception.py)
    model_swapper.py          DEAD (PPE/face mode switching, superseded by modules)
    global_appearance_cache.py  UNUSED (cross-camera re-id scaffolding)
  alembic/versions/           5 migrations, two heads
frontend/src/
  App.tsx                     no router — switches on an `active` string
  views/                      13 top-level views
  components/ui/index.tsx     shared primitives (Card, Button, Badge, Table, Modal, ...)
  components/domain/          CameraGrid, StreamTile, ZoneEditor, CameraModulesEditor, ...
  lib/modules.ts              * hand-maintained MIRROR of registry.py — edit both
  lib/api.ts                  fetch wrapper, credentials:"include", 401 -> reload
  hooks/useCameraStream.ts    WebSocket -> canvas with exponential-backoff reconnect
reid.onnx                     the ONLY committed model weight (~0.9 MB OSNet)
models/                       git-ignored, NOT PRESENT in this working copy
media/                        git-ignored, empty in this working copy
```

---

## 4. Runtime architecture — the load-bearing decision

```
+- MAIN PROCESS -------------------------------------------------------------+
| FastAPI + asyncio + OpenCV/FFmpeg + asyncpg           ***NO GPU, NO TORCH***|
|                                                                            |
|  CameraInstance (one per source, camera_manager.py)                        |
|    +- capture thread  — cv2.VideoCapture(rtsp), BUFFERSIZE=1               |
|    +- AI thread       — throttles frames onto the perception task queue,   |
|                         draws the latest overlays back on, JPEG-encodes    |
|                         only if a WebSocket client is attached             |
|                                                                            |
|  PerceptionWriter (daemon thread, perception._result_writer)               |
|    +- SYNC psycopg2 session — the ONLY writer of AlertLog / Attendance /   |
|       FaceEmbedding coming out of the pipeline                             |
|                                                                            |
|  ConnectionManager — broadcast_sync() hops sync thread -> event loop -> WS  |
+---------------+---------------------------------------^--------------------+
                | mp.Queue task_queue (max 50)           | mp.Queue result_queue (max 200)
                v                                        |
+- PERCEPTION CHILD PROCESS (spawn, daemon) --------------+------------------+
| ALL torch / ONNX / GPU work                            ***NO DB ACCESS***  |
|  perception._child_main:                                                   |
|    - loads only the backbones the assigned modules need                    |
|    - per camera: person track -> face detect+embed -> identity fusion ->    |
|      re-id -> phone -> fire/smoke -> per-module rule consumers              |
|    - emits events + normalised overlay primitives + enrol vectors          |
+----------------------------------------------------------------------------+
```

**Why the split exists (do not undo it):** torch/ONNX sharing an address space with
libpq and FFmpeg corrupted the native heap on this deployment. Hence the hard rule:
**no DB in the child, no GPU in the main process.** `backend/main.py` additionally sets
`cv2.setNumThreads(0)` and pins `OMP_NUM_THREADS` / `OPENBLAS_NUM_THREADS` /
`MKL_NUM_THREADS` to 1 **before any heavy import** — OpenCV's OpenMP `parallel_for`
collided with inference in worker threads (`free(): invalid pointer` / SIGABRT).
Preserve that import ordering.

### IPC contract

**Task queue (main → child)** carries either a frame job or a command:

```python
# frame job
{"frame": ndarray_BGR_downscaled, "frame_buf": jpeg_bytes_full_res,
 "cam_id": int, "scale": float, "modules": [ {module, enabled, ...config}, ... ]}

# commands
{"cmd": "add",            "path": abs, "rel": "employees/x.jpg"}   # new anchor photo
{"cmd": "add_extra",      "path": abs, "rel": anchor_rel}          # extra manual angle
{"cmd": "remove",         "rel": photo_rel}
{"cmd": "reset_autos",    "key": basename}
{"cmd": "reload_gallery", "photos": [rel, ...]}                    # authoritative rebuild
{"cmd": "seed_vecs",      "vecs": [{key, vec(bytes), source, quality}, ...]}
{"cmd": "seed_counts",    "counts": {cam_id: {"in": n, "out": n}}}
```

**Result queue (child → main):**

```python
{"cam_id", "scale",
 "faces":    [{photo_base, ambiguous, weak, sim, margin, score, role, box,
               inferred?, crossed_line}],
 "events":   [{module, event_type, value, data, label?, snapshot?, photo_base?}],
 "overlays": [{kind: box|line|zone|label, ...normalised 0-1 coords..., color,
               label, ident?}],
 "snapshot": jpeg_bytes,
 "enroll":   [{key, vec(bytes), quality}]}
```

FIFO ordering on the single task queue is load-bearing: `reload_gallery` is guaranteed to
run before the `seed_vecs` that follows it.

---

## 5. Model inventory — what AI actually runs

| # | Role | Architecture | Weights | Runtime | Input | Output | Where |
|---|---|---|---|---|---|---|---|
| 1 | Person detection + tracking | **YOLOv11s** + **ByteTrack** | `models/yolo11s.pt` (git-ignored) | PyTorch / ultralytics, CUDA | frame downscaled to 1280 px wide | tracked boxes, class 0 only, conf ≥ 0.3 | `detectors/person.py` |
| 2 | Face detection | **SCRFD** (`det_10g`) | InsightFace `buffalo_l` pack, auto-downloads to `~/.insightface/models/` | ONNX Runtime, CUDA → CPU fallback | `det_size=(640, 640)` | boxes + `det_score` | `detectors/face.py` |
| 3 | Face recognition | **ArcFace** (`w600k_r50`, ResNet-50 backbone) | same `buffalo_l` pack | ONNX Runtime | aligned 112×112 crop (handled internally by `FaceAnalysis.get()`) | **512-d L2-normalised** embedding | `detectors/face.py` |
| 4 | Body appearance Re-ID | **OSNet x0_25** | `reid.onnx` — **the only committed weight** (~0.9 MB) | ONNX Runtime, CUDA → CPU | NCHW 1×3×**256×128**, RGB, ImageNet mean/std | **512-d L2-normalised** appearance vector | `detectors/reid.py` |
| 5 | Phone detection | **YOLOv11s**, COCO **class 67** (`cell phone`) | reuses the *same per-camera* `yolo11s.pt` instance | PyTorch | 640, conf 0.25 | boxes matched to person tracks | inline in `perception.py` |
| 6 | Smoking detection | fine-tuned **YOLO** (custom) | `models/best.pt`, override `SMOKING_WEIGHTS` | PyTorch | default conf 0.75, per-camera override | boxes + confidence | `detectors/smoking.py` |
| 7 | Fire / smoke detection | fine-tuned **YOLO** (custom, 2 classes: `0=fire`, `1=smoke`) | `models/fire_smoke.pt` | PyTorch | imgsz 640, conf 0.75 | class + conf + box | `detectors/fire_smoke.py` |

### Model notes, discrepancies and gotchas

- **Naming drift.** `registry.py`, `perception.py` and `main.py` docstrings all say
  *YOLOv11n*; the code loads **`models/yolo11s.pt`** (the `s` variant). `VIGIL.spec` still
  bundles a `yolo11n.pt`. Treat `yolo11s` as ground truth.
- **`detectors/phone.py` is never imported.** Phone detection is done inline in
  `perception.py` by calling
  `person_det.get_model(cam_key).predict(classes=[67], conf=0.25, imgsz=640)` — it reuses
  the camera's existing YOLO instance rather than the wrapper. The wrapper file exists
  with a different default (`conf=0.02`) and is dead code.
- **One YOLO instance per camera** (`_models: dict[cam_id] -> YOLO`), deliberately, so
  each camera's ByteTrack state (`persist=True`) stays isolated. Cheap at the camera
  counts in scope; would need pooling for a large fleet.
- **Graceful degradation is a contract.** `smoking.py` and `reid.py` attempt to load
  once, log a single line if the weights are missing, then no-op. Any new detector must
  preserve this — a missing weight file must never crash the pipeline. (`fire_smoke.py`
  does *not* implement this guard and uses a bare relative path `"models/fire_smoke.pt"`
  resolved against the process CWD, unlike the others which join `settings.BASE_DIR`.)
- **No weights are committed except `reid.onnx`.** `.gitignore` excludes `models`, `*.pt`
  and `*.onnx`, with an explicit `!reid.onnx`. In this working copy `models/` does not
  exist at all — person / phone / smoking / fire detection would be inert until weights
  are supplied; only `buffalo_l` would auto-download.
- **TensorRT export exists but is not wired in.** `export_gpu.py` exports `yolo11s.pt`
  (and a legacy `ppe.pt`) to `format='engine', half=True`; nothing in the runtime loads a
  `.engine`.
- **Dead model machinery.** `services/face_worker.py` is the pre-migration DeepFace
  implementation (still imported under an alias: `employees.py` does
  `from backend.services import perception as face_worker`). `services/model_swapper.py`
  plus `/api/mode` are a vestigial PPE↔face model-switching mechanism the module system
  replaced. `download_models.py` still imports DeepFace, which is no longer a dependency.

---

## 6. The module system

`backend/services/modules/registry.py` is the single source of truth.
`frontend/src/lib/modules.ts` is a **hand-maintained mirror — the two must be edited
together.**

Each entry declares: `backbone`, `geometry` (`None` | `"line"` | `"zones"` — what the
camera editor must capture), `config` field list, `event_types`, `icon`, `color`, `phase`.

| Module | Name in UI | Phase | Backbone | Geometry | Config fields | Events |
|---|---|---|---|---|---|---|
| `attendance` | Attendance | 1 | face | line | `line`, `role`, `confidence_threshold` | recognized, unknown, checkin, checkout |
| `reid` | Vectoring | 2 | person (+ OSNet) | — | — | — |
| `headcount` | Head Counting / Entry-Exit | 1 | person | line | `line` | entry, exit |
| `occupancy` | Occupancy | 1 | person | zones | `zones`, `capacity` | occupancy, over_capacity |
| `idle` | Idle Time Monitoring | 1 | person | zones | `zones`, `dwell_seconds`, `idle_mode` (+ `schedule` in the FE mirror) | idle |
| `intrusion` | Intrusion Detection | 2 | person | zones | `zones`, `schedule` | intrusion |
| `smoking` | Smoking Detection | 2 | smoking | — | `confidence_threshold` | smoking |
| `mobile` | Mobile Usage Monitoring | 2 | phone | — | — | phone_use |
| `fire_smoke` | Fire & Smoke Detection | 3 | fire_smoke | — | — | fire, smoke |

> Mirror drift already exists: the backend `idle` entry omits `schedule` from its
> `config` list although `IdleTime` reads `self.config.get("schedule")`; the frontend
> mirror includes it. The frontend also types `reid`'s backbone as `person`.

### Backbone activation logic (`perception._child_main`)

```
backbones = backbones_for(assigned_module_ids)
if "mobile" in ids:      backbones |= {"face"}    # so phone alerts carry a name
if "face" in backbones:  backbones |= {"person"}  # identity must bind to a body track
tracks = person.detect_and_track(...) if ("person" in backbones or "phone" in backbones)
reid_on = "reid" in ids    # opt-in; OSNet is not even loaded into VRAM otherwise
```

### Rule consumers (`backend/services/analytics/`)

A consumer is `Analytic(cam_id, config)` with
`process(tracks, faces, shape) -> (events, overlays)`, instantiated lazily per
`(cam_id, module_id)` and cached in the child. On each frame its `.config` is replaced
with the current assignment dict, so **edited config takes effect without discarding
state**. Registration is `analytics/__init__.py::_CONSUMERS`.

**Coordinate convention (easy to get wrong):**

- Config geometry (`line`, `zones`) is stored **normalised 0–1** so it survives
  resolution changes.
- Detection primitives handed to `process()` are in **det-frame pixels**.
- Returned overlays must be **normalised 0–1** again.

**Algorithms in brief:**

- **HeadCount** — signed side of the line (`side_of_line`) per track centroid; a sign flip
  is a crossing. `prev < 0 <= s` counts as *entry*, otherwise *exit*. Keeps in-memory
  `entries` / `exits`, which the writer re-seeds from history on startup.
- **Occupancy** — ray-cast point-in-polygon per centroid; falls back to a whole-frame
  count if no zone is drawn. **Only emits an event when `count > capacity`** (30 s
  cooldown); the within-limit count is overlay-only. This was a deliberate fix — emitting
  on every count change flooded the alert feed.
- **IdleTime** — three modes:
  - `away` (default) — the zone is a workstation; idle = **empty** for longer than
    `dwell_seconds`. Flags once, re-arms after someone returns and leaves.
  - `still` — a person in the zone whose centroid stays within `MOVE_EPS = 0.004`
    (normalised L1) of the **anchor position where the timer started** — not the previous
    frame, so slow continuous movement is not flagged.
  - `loiter` — present in the zone continuously, moving or not.

  `still` / `loiter` escalate at 50 % / 75 % / 100 % of dwell, carrying `data.level` and
  `data.pct`. An optional `schedule` `{start, end}` window (overnight-aware) arms it.
- **Intrusion** — any track centroid inside any zone while armed; 10 s cooldown.
- **MobileUsage** — reads the `holding_phone` flag `perception.py` attaches to tracks;
  5 s cooldown per track id.
- **FireSmoke** — the odd one out: `process()` returns nothing, and a separate
  `process_detections(detections, shape)` is called with raw detector output; 30 s
  cooldown per class plus a 10 s banner overlay.
- **Attendance is NOT a consumer.** Face matching happens in the child; the DB writes
  happen in `perception._result_writer::write_attendance`.

---

## 7. Face identity pipeline — the most intricate part

### 7.1 Gallery model (`embedding_cache.py`)

Each employee has a **gallery** of 512-d L2-normalised ArcFace vectors, keyed by
**the basename of their anchor photo** (e.g. `john_doe.jpg`). That basename —
`photo_base` — is the identity token flowing through the entire pipeline. The writer
resolves it back to a row with `Employee.photo ILIKE %photo_base%`.

Gallery entry sources:

- `manual` — one permanent anchor, encoded from the enrolment photo. Never pruned.
- `manual_extra` — additional enrolment photos (files containing `_extra`, or added via
  the `add_extra` command). Permanent.
- `auto` — up to `ENROLL_MAX_AUTO = 8` vectors captured at runtime from high-confidence
  live matches, persisted to `face_embeddings` and re-seeded on startup.

**The writer, not the child, decides what a real identity is.** On startup the writer
queries `Employee.photo`, sends `reload_gallery` with exactly those paths, then
`seed_vecs` with the stored auto vectors. The child never scans `media/employees/`. This
was the fix for a mis-identification bug where a stray file in the media directory became
a phantom identity.

### 7.2 Matching

`find_match(query)` computes, per employee, `max(gallery_matrix @ query)` (cosine, since
everything is L2-normalised), sorts, and returns `(key, best_sim, margin)` where
`margin = best − second_best_employee`. A match requires `best ≥ MATCH_SIM = 0.45`
(deliberately lenient, to keep recall on poor streams). The **margin** is what the
stricter gates use.

### 7.3 Layered defences — every threshold, what it guards, where to tune it

| Constant | Default | Env var | Purpose |
|---|---|---|---|
| `MATCH_SIM` | 0.45 | — (`detectors/face.py`) | absolute cosine floor for any match; kept lenient on purpose |
| `LABEL_MARGIN` | 0.08 | `LABEL_MARGIN` | **ambiguity guard** — a match whose lead over the 2nd-best *employee* is smaller is demoted to `ambiguous` (`photo_base=None`): no check-in, no alert, no vote. Guards *separation*, not absolute similarity, so a clear-but-low-quality match still passes |
| `UNKNOWN_MIN_SCORE` | 0.62 | `UNKNOWN_MIN_SCORE` | min SCRFD score before an *unmatched* face may raise an "Unknown" alert. Overridable per camera via the attendance module's `confidence_threshold` |
| `UNKNOWN_MIN_PX` | 44 | `UNKNOWN_MIN_PX` | min face-box side (px) for the same gate. Together these stopped "too many unknown" spam from distant blobs |
| `UNKNOWN_COOLDOWN` | 60 s | `UNKNOWN_COOLDOWN` | per-camera cooldown on unknown alerts |
| `EMP_COOLDOWN` | 15 s | — | per-(employee, camera) attendance cooldown |
| `MIN_CHECKOUT_GAP` | 60 s | — | minimum gap after check-in before a check-out is recorded |
| `VOTE_DECAY` | 0.85 | — | per-processed-frame fade of accumulated identity votes |
| `VOTE_MARGIN` | 1.25 | — | the leading identity must beat the runner-up by this ratio to (re)bind |
| `IDENTITY_GRACE` | 5.0 s | — | how long an identity stays bound after its track was last seen |
| `ENROLL_ENABLE` | on | `AUTOENROLL=0` disables | master switch for auto-enrolment |
| `ENROLL_SIM` | 0.70 | — | min similarity to auto-enrol (far stricter than `MATCH_SIM`) |
| `ENROLL_MARGIN` | 0.12 | — | min lead over 2nd-best employee — **the real gallery-poisoning guard** |
| `ENROLL_SCORE` | 0.55 | — | min SCRFD detector score (sharp, real face) |
| `ENROLL_MIN_PX` | 48 | — | min face-box side |
| `ENROLL_DUP_SIM` | 0.92 | — | reject near-duplicates, keeping angle diversity |
| `ENROLL_MAX_AUTO` | 8 | — | cap on stored auto vectors per employee (FIFO eviction, anchor untouched) |
| `REID_ENABLE` | on | `REID=0` | kill switch for body Re-ID |
| `REID_EVERY` | 5 | — | run OSNet every Nth processed frame |
| `BODY_MAX` | 3 | — | appearance crops kept per employee (daily, then expired) |
| `BODY_MATCH` | 0.62 | — | appearance cosine needed to re-bind a faceless track |
| `PHONE_EVERY` | 3 | `PHONE_EVERY` | phone YOLO pass cadence |
| `FIRE_EVERY` | 2 | `FIRE_EVERY` | fire/smoke model cadence |
| `SMOKE_COOLDOWN` | 15 s | — | per-camera smoking-event cooldown |
| `OVERLAY_TTL` | 2.0 s | — | overlays older than this are not drawn |
| `MAX_QUEUE` | 50 | — | task queue depth (result queue is 4×) |
| `FACE_DEBUG` | off | `FACE_DEBUG=1` | per-face logging of key / sim / margin / score / px |

**Read the comment blocks at the top of `perception.py` before changing any of these —
each records the incident it was introduced to fix.**

### 7.4 Identity binding — the full per-frame sequence

1. **Detect and embed** every face; compute `(key, sim, margin)`; mark `ambiguous` if
   `margin < LABEL_MARGIN`; mark `weak` if score or size falls under the unknown gate.
2. **Auto-enrol** if all six strict conditions hold — added to the in-memory gallery
   immediately (instant benefit) and emitted for the writer to persist.
3. **Temporal voting.** For every track present this frame, decay existing votes by
   `VOTE_DECAY`; add `max(sim, 0)` to the vote for the identity of any recognised face
   whose centre falls inside the track box. Commit the leader when
   `lead ≥ MATCH_SIM and lead ≥ runner_up × VOTE_MARGIN`, and mark that track
   **face-confirmed**. Present-but-undecided tracks keep their existing binding alive.
   Expire after `IDENTITY_GRACE`.
4. **Body Re-ID** (only if the camera enabled `reid`, `REID` is on, the face backbone is
   active, `reid.onnx` loaded, and `frame % REID_EVERY == 0`):
   - Build a **daily** appearance gallery from **face-confirmed tracks only** — never
     from appearance-only bindings, which would be self-reinforcing drift. Cleared when
     the local date changes (people change clothes).
   - Re-bind only tracks that (a) have no identity **and** (b) have **no visible face this
     frame**. A person facing the camera whose face is unknown stays unknown rather than
     inheriting an identity from similar clothing. Threshold `BODY_MATCH = 0.62`.
     Re-bound tracks are **not** marked face-confirmed, so they never feed the gallery.
5. **Identity uniqueness.** At most one track may carry a given identity at a time. Where
   several claim it, keep the strongest by
   `(is_face_confirmed, live_vote_for_that_identity, most_recent_binding)` and release the
   rest. This was the fix for "two or three people shown with the same name".
6. **Unknown suppression.** An unmatched face inside an identified track's box is almost
   certainly that same person momentarily blurred or turned. It is attributed to the bound
   identity and flagged `inferred` — the writer shows the name but raises **no** unknown
   alert and creates **no** attendance row. Guard: this applies only when the track hosts
   **exactly one** face, otherwise a bystander standing close would inherit the name.
7. **Attendance ROI line gating.** If the attendance module has a `line`, a track must
   *cross* it (sign flip of `side_of_line` on the centroid) before its faces count. Faces
   inside a crossed track are marked `crossed_line`; the writer skips the rest. With no
   line configured, every face is treated as crossed (full-frame attendance).

### 7.5 Attendance write rules (`_result_writer::write_attendance`)

- Skip `inferred` faces and faces that have not crossed the ROI line.
- No `photo_base`:
  - `ambiguous` → return silently (never log a wrong ID *or* a false unknown)
  - `weak` → return silently (quality gate)
  - otherwise → one `unknown` `AlertLog` per `UNKNOWN_COOLDOWN` per camera, with snapshot.
- With `photo_base`: resolve the employee, apply `EMP_COOLDOWN`, then
  - no row for today and `role ∈ {in, both}` → create `Attendance` with status
    `Late` / `On Time` compared against `SystemSettings.office_open_time`, plus a
    `checkin` alert;
  - existing row, `role ∈ {out, both}`, and ≥ `MIN_CHECKOUT_GAP` since check-in → set
    `check_out` and log a `checkout` alert.

### 7.6 Live-pipeline invalidation

Dashboard edits must be pushed to the child explicitly. The paths that do so:

| Action | Mechanism |
|---|---|
| `POST /api/system/reload` | rebuilds the gallery from employee-referenced photos, re-seeds auto vectors, refreshes every running camera instance's modules |
| `PUT /api/cameras/{id}` | `camera_manager.refresh_modules_by_cam_id` |
| Employee create / update / delete | `perception.notify_photo_added` / `_removed` / `notify_extra_photo_added` / `notify_reset_autos` |

**If a change appears not to take effect, check whether its code path pushes to the child.**

---

## 8. Data model

| Table | Purpose | Key columns |
|---|---|---|
| `alert_logs` | **the unified event store** — every module writes here | `module`, `camera_id`, `event_type`, `value` (numeric payload), `data` (JSON *string*), `violation_type`, `employee_id`, `unknown_person`, `distance_score`, `likely_candidate`, `snapshot`, `timestamp` |
| `attendance` | one row per employee per day | `employee_id`, `date`, `check_in`, `check_out`, `status` |
| `employees` | roster + enrolment photos | `name`, `employee_id` (unique), `photo`, `extra_photos` (PG `ARRAY`), designation, department, email, phone, gender, status, join_date |
| `face_embeddings` | persisted gallery vectors | `employee_id`, `vec` (`LargeBinary`, 512×float32), `source` (`auto` / `manual_extra`), `quality`, `created_at` |
| `camera_devices` | one row per camera / NVR channel | `name`, `ip_address`, credentials, `channel` (default 102), `process_frame_rate`, `is_active`, `location`, `department`, **`module_config`** (JSON text), `nvr_id`; methods `get_modules()` and `get_rtsp_url()` |
| `nvr_devices` | NVR/DVR head units | ip, port (554), credentials, `rtsp_template_id` |
| `rtsp_string_templates` | reusable RTSP path templates containing `{channel}` | `name`, `template_string` |
| `users` | dashboard accounts | `username`, `email`, `hashed_password`, `role` (`admin` / `supervisor` / `operator` / `viewer`), `is_active` |
| `system_settings` | singleton row `id=1` | `office_open_time`, `office_close_time`, `locations` / `departments` (JSON text) |
| `global_person_appearance` | cross-camera re-id store — **table and model exist, nothing reads or writes them** | `person_id` (PK, string), `face_embedding`, `body_embedding`, first/last seen, `confidence_score`, `camera_src`, `sighting_count`, `metadata` |
| `tenants`, `tenant_lifecycle_events`, `tenant_sites`, `nvr_probe_runs`, `roles`, `permissions` (+ association tables) | **multi-tenant SaaS models present on this branch but not wired to any router or service here** — see §14 | |

**Design rule: new modules must not add tables. They emit events into `alert_logs`.**
`/api/analytics/summary` and the dashboard roll up over that one table, and head-count
running totals are literally rebuilt by counting `entry` / `exit` rows on startup.

### Migrations

`backend/alembic/versions/` — 5 revisions, and the tree has **two heads**
(`9488212d6fbb` and `d4e5f6a0b1c2`, both branching from `51d2c9f065e1`). Use
**`alembic upgrade heads`** (plural) or merge them with `alembic merge`. Additionally
`create_tables()` runs `Base.metadata.create_all` at startup *alongside* Alembic, so a
fresh database can end up structurally ahead of `alembic_version`.

**All timestamps are rendered in `Asia/Karachi`.** `TZ = ZoneInfo("Asia/Karachi")` is
repeated verbatim in `perception.py`, `analytics/base.py`, `routers/alerts.py`,
`routers/analytics.py` and `routers/attendance.py`.

---

## 9. API surface (all under `/api`)

| Router | Endpoints |
|---|---|
| `auth` | `POST /auth/login`, `POST /auth/logout`, `POST /auth/refresh`, `GET /auth/me` |
| `users` | `GET/POST /users/`, `PUT/DELETE /users/{id}` |
| `employees` | `GET/POST /employees/`, `PUT/DELETE /employees/{id}`, `DELETE /employees/{id}/extra-photos`, `DELETE /employees/{id}/face-vectors` |
| `cameras` | `GET/POST /cameras/`, `PUT/DELETE /cameras/{id}`, `POST /cameras/bulk-modules` |
| `nvrs` | `GET/POST /nvrs/`, `PUT/DELETE /nvrs/{id}`, `POST /nvrs/{id}/channels/`, `POST /nvrs/{id}/channels/bulk`, `DELETE /nvrs/{id}/channels/{cam_id}` |
| `rtsp_templates` | `GET/POST /rtsp-templates/`, `PUT/DELETE /{id}` |
| `alerts` | `GET /alerts/` (filters: `limit`, `module`, `camera_id`, `employee_id`), `DELETE /alerts/` (also deletes snapshot files) |
| `attendance` | `GET /attendance/` (`date`, or `start` / `end` range) |
| `analytics` | `GET /analytics/summary?hours&module&camera_id` → totals by module / camera / event type |
| `settings` | `GET /settings/`, `PUT /settings/` |
| `system` | `POST /system/reload` |
| `stream` | `GET /stream/video` (MJPEG), `GET /stream/snapshot` (JPEG), **`WS /stream/ws/{cam_id}`** |
| `mode` | `GET/POST /mode/` — **vestigial** PPE/face switch |

**Auth:** HttpOnly cookie carrying an HS256 JWT (`access_token`, 60 min; refresh 7 days).
`get_current_user` reads the cookie off the `HTTPConnection`, which is why it also works
for the WebSocket route. `require_role(*roles)` gates by the
`admin > supervisor > operator > viewer` enum. Streaming endpoints require
operator-or-above; the WebSocket only requires authentication.

**Static serving:** `/media` → `media/`, `/static` → `frontend/dist`, plus a catch-all SPA
fallback returning `index.html` for anything that is not `api/`, `media/` or `static/`.

---

## 10. Streaming and camera lifecycle

- **Capture** — `cv2.VideoCapture` with `OPENCV_FFMPEG_CAPTURE_OPTIONS=rtsp_transport;tcp`
  and `CAP_PROP_BUFFERSIZE=1`. Webcam sources (numeric strings) probe
  `CAP_DSHOW → CAP_MSMF → CAP_ANY` across indices 0–2.
- **Frame budget** — the AI thread acts only on every `process_frame_rate`-th frame
  (default 10), enqueues to perception at most every `ENQUEUE_INTERVAL = 0.5 s`, and only
  if `perception.queue_size() < MAX_QUEUE`. The *effective analytics rate is therefore
  ≈ 2 fps per camera*, independent of stream fps.
- **Downscale** — frames wider than `DET_WIDTH = 1280` are resized for detection; the
  original full-resolution JPEG travels alongside as `frame_buf` for snapshots.
- **Encoding is conditional** — JPEG encoding and broadcast are skipped entirely unless
  `connection_manager.has_clients(cam_id)`. No viewers, no encode cost.
- **Idle self-termination** — a camera thread stops after 30 s without activity, but an
  open WebSocket counts as activity, so a watched stream never freezes.
- **Failure backoff** — a failed open (or more than 50 consecutive bad reads) grows an
  exponential cooldown `15 s → 30 → 60 → … → 300 s cap`, reset on the first good frame.
  This is **deliberate and important**: NVRs lock accounts after repeated failed auth, and
  a tight reconnect loop would keep the lockout alive forever.
- **Disabled cameras are never dialled at all** — `is_active=False` returns a static
  "CAMERA DISABLED" JPEG from every stream path.
- **Transport** — `WS /api/stream/ws/{cam_id}` pushes **raw JPEG bytes**; the client
  (`useCameraStream`) blobs them into an `Image` and draws to a `<canvas>`, resizing the
  pixel buffer only when dimensions change. Reconnect backoff 1 s → 10 s.
- **Overlays lag the video slightly** by design: the camera thread draws the *latest*
  overlay set (TTL 2 s) from `perception.get_overlay()` while inference runs
  asynchronously.
- Overlay colour tokens (`accent`, `accent-soft`, `online`, `idle`, `dnd`) are mapped to
  **BGR** in `camera_manager._OVERLAY_BGR` and mirror the frontend theme accents.

---

## 11. Frontend architecture

- **No router.** `App.tsx` holds an `active: string` and a `useMemo` switch over 13 views;
  the sidebar and mobile tabs set it. Deep links and browser back are not supported.
- **Views:** Overview, Operations (live grid), Intelligence (Recharts analytics), Assets,
  Devices (IP cameras), NVR/DVR, RTSP strings, Employees, Attendance, Alerts, Users,
  Admin, Login.
- **Auth** is an HttpOnly cookie, so every call goes through `lib/api.ts` with
  `credentials: "include"`; a 401 triggers `window.location.reload()` to re-run the auth
  guard in `AuthContext`.
- **Shared primitives** live in `components/ui/index.tsx` (Card, Button, Badge, Table,
  Modal, MetricCard, Toast, Spinner, `cx`) — reuse rather than styling ad hoc.
- **Theming** is CSS custom properties under `:root[data-theme=...]` in `index.css` with
  Tailwind v4. Use semantic token classes (`bg-base`, `text-tx`, `bg-content`), not raw
  colours.
- **Domain components of note:** `ZoneEditor` (draw normalised polygons and lines over a
  snapshot), `CameraModulesEditor` (assign modules and capture geometry), `StreamTile`,
  `CameraGrid`, `ActivityToaster` (live alert toasts), `Lightbox`, `ModuleFilter`.
- `frontend/src/data/mockData.ts` still backs parts of `AssetsView` — not everything on
  screen is live data.

---

## 12. Configuration

`.env` at the repo root (git-ignored; `.env.example` is the template), read by
`pydantic-settings`:

```
DATABASE_URL=postgresql+asyncpg://.../ppe_new     # writer rewrites +asyncpg -> +psycopg2
JWT_SECRET, JWT_ALGORITHM=HS256
ACCESS_TOKEN_EXPIRE_MINUTES=60, REFRESH_TOKEN_EXPIRE_DAYS=7
MEDIA_ROOT=<abs>/media          # must be absolute, per machine
BASE_DIR=<abs>                  # repo root
```

Tuning knobs are **environment variables read at import time in `perception.py`** (see the
table in §7.3), plus `SMOKING_WEIGHTS` and `REID_WEIGHTS` for weight overrides. Per-camera
config lives in `camera_devices.module_config` and is edited from the UI.

### Commands

```bash
bash start.sh                  # Linux — REQUIRED: prepends the conda env's cuDNN 9.3 to
                               # LD_LIBRARY_PATH; a bare uvicorn breaks ONNX face
                               # recognition with "No DNN support for stream"
start.bat                      # Windows, 127.0.0.1:8000
alembic upgrade heads          # plural — two heads
python -m backend.scripts.create_admin
cd frontend && npm run dev     # Vite :5173, proxies /api + /media to :8000
cd frontend && npm run build   # tsc -b && vite build -> frontend/dist (COMMIT IT)
build_exe.bat                  # PyInstaller bundle
```

---

## 13. Performance profile

Known cost structure, and how the code responds to it:

- The perception child processes **every camera sequentially on one GPU**. There is no
  batching across cameras and no worker pool — throughput is the sum of per-camera
  per-frame cost.
- Per processed frame, worst case with everything enabled: one YOLO person+track pass,
  one SCRFD detect plus N ArcFace embeds, one extra YOLO pass for phones, one smoking
  model pass, one fire/smoke model pass, and (every 5th frame) one batched OSNet pass.
- Throttles that exist specifically because enabling `mobile` and `fire_smoke` roughly
  doubled per-frame cost: `PHONE_EVERY = 3` (with the "holding phone" flag carried forward
  on the track id for ≤ 1.5 s so overlays do not flicker) and `FIRE_EVERY = 2` (fire and
  smoke persist for seconds, so a lower cadence loses nothing).
- `REID_EVERY = 5`, and OSNet is not loaded into VRAM at all unless some camera enables
  the `reid` module — the `reid_on` check is deliberately evaluated *before*
  `reid_det.available()`.
- Ingest side: roughly 2 analytics fps per camera (`ENQUEUE_INTERVAL = 0.5 s`), det frames
  capped at 1280 px wide, JPEG encode skipped with no viewers, queue depth 50 with
  drop-on-full (`put_nowait` returns `False`).
- Snapshot annotation is lazy — the overlay is drawn onto the JPEG only if some row in
  that result actually needs a saved snapshot.

---

## 14. Multi-tenant / SaaS state

This branch (`stagging`) **contains the multi-tenant ORM models but none of the code that
uses them**: `models/tenant.py` (Tenant, TenantLifecycleEvent, status / attach-mode /
billing enums, `camera_quota`, `licensed_modules`, renewal and grace period),
`models/site.py` (TenantSite with connectivity mode / WAN host / allowlist, NVRProbeRun),
and `models/permission.py` (Role, Permission, tenant-scoped RBAC).

The routers and services that consume them — `routers/admin.py`, `core/permissions.py`,
`services/lifecycle.py`, `services/quota.py`, `services/discovery.py`,
`services/rtsp_probe.py` — exist only on **`sass-under-testing`**, together with a
separate admin SPA entry point (`frontend/src/admin/`, its own `admin.html` bundle) and a
**real pytest suite** (~2 900 lines: tenancy, permissions, quota, discovery, lifecycle,
devices, admin API, schema contract). Stale `.pyc` files for those modules are still
present in `backend/**/__pycache__` on this branch, which is why grep can appear to find
them.

Practical read: runtime RBAC on `stagging` is the flat `UserRole` enum on `users`, not the
`roles` / `permissions` tables.

> Note: an untracked `platform-admin-credentials.txt` sits in the repo root. It should not
> be committed; treat whatever it holds as a live secret to be rotated and stored in a
> secret manager rather than a file.

---

## 15. Known gaps, dead code and documentation drift

**Dead or unwired code**

- `services/face_worker.py` — pre-migration DeepFace implementation. Superseded, but
  `routers/employees.py` still imports `perception` *aliased* as `face_worker`.
- `services/model_swapper.py` + `routers/mode.py` (`/api/mode`) — vestigial PPE/face model
  switching.
- `services/global_appearance_cache.py` + `models/person_appearance.py` +
  `global_person_appearance` table — cross-camera re-id scaffolding; nothing calls it.
- `services/detectors/phone.py` — superseded by the inline phone pass in `perception.py`.
- `download_models.py` — still imports DeepFace, no longer a dependency.
- `test.jsx` (51 KB) and `ppe_detection.py` at the repo root — legacy artefacts.

**Correctness / consistency issues worth investigating**

- `yolo11n` vs `yolo11s` naming drift across docstrings, `VIGIL.spec` and code.
- `detectors/fire_smoke.py` uses a bare relative weights path and has no missing-weights
  guard, unlike `smoking.py` / `reid.py`.
- `registry.py`'s `idle` config list omits `schedule` even though the consumer reads it;
  the frontend mirror includes it. Registry ↔ `modules.ts` drift is a standing hazard —
  they are synchronised **by hand**.
- `_result_writer` seeds `("auto", "manual_extra")` vectors at startup, but
  `POST /api/system/reload` re-seeds only `source == "auto"` — a reload silently drops
  persisted `manual_extra` vectors from the gallery until the next restart.
- `Analytic._cooldown` uses a **mutable default argument** `_last={}` as shared state,
  keyed by `(id(self), key)`. `id()` is reused after garbage collection, so a recreated
  consumer can inherit another's cooldown.
- Identity keying by photo *basename* with `ILIKE %photo_base%` is fragile: two employees
  whose photo filenames are substrings of one another would collide.
- `alert_logs.data` is a JSON **string** in a `Text` column, not `JSONB` — not queryable.
- `DELETE /api/alerts/` loads every row into memory and deletes them one at a time.
- `create_tables()` running alongside Alembic; two migration heads.
- The sequential, pool-free GPU loop is the throughput ceiling (§13).

**Stale documents in the repo** (kept as history — `SETUP.md` is the authoritative setup
guide): `memory.md`, `RECOGNITION_TUNING.md` (references a Django `dashboard/views.py`),
`RECOMMENDATIONS.md`, `GLOBAL_REID_SETUP.md` (describes a
`backend/models/person_appearance.py` API that does not match the current file),
`frontend/design.md` (cyan/violet scheme superseded by the token system). `start.sh`'s own
header still says "DeepFace-only build" and talks about TensorFlow 2.21 — the reason the
cuDNN 9.3 ordering matters now is **onnxruntime-gpu**, not TF.

---

## 16. Suggested deep-research directions

Grouped by what a research pass could most usefully produce.

**A. Recognition accuracy**

1. Is `MATCH_SIM = 0.45` on ArcFace `w600k_r50` defensible, given that `LABEL_MARGIN`, the
   voting margin and the unknown quality gate all sit downstream of it? What is the actual
   FAR/FRR at this gallery size?
2. Does the margin-based ambiguity guard degrade as the roster grows (more employees means
   a smaller expected gap to the 2nd-best)? Should `LABEL_MARGIN` scale with N?
3. Auto-enrolment: does an 8-vector `max(dot)` gallery with a 0.92 duplicate cut actually
   improve recall over a single anchor, or does it mostly accumulate near-duplicates?
   Would averaging or clustering vectors beat max-similarity?
4. Would quality-aware fusion (SCRFD score × face size × pose) outperform the current hard
   gates?

**B. Tracking and re-identification**

5. ByteTrack with `persist=True` per camera: measure the id-switch rate at ~2 fps input.
   The tracker was tuned for much higher frame rates, and the whole voting / grace / re-id
   edifice exists to paper over id switches. Would raising the *tracking* rate while
   keeping recognition throttled be cheaper than the compensation machinery?
6. OSNet x0_25 is the smallest OSNet. Is `BODY_MATCH = 0.62` right for it? What does a
   larger variant (x0_5 / x1_0) cost at `REID_EVERY = 5`?
7. Cross-camera re-id: the table and cache exist but are unused. What would it take to
   make `global_person_appearance` real, and is a daily-expiring clothing gallery the right
   abstraction for it?

**C. Model upgrades**

8. TensorRT / FP16: `export_gpu.py` exists but nothing loads a `.engine`. Quantify the win
   for YOLOv11s and whether the ultralytics engine path is stable enough here.
9. Batching frames across cameras in a single YOLO call is the largest available throughput
   win, but it conflicts with per-camera ByteTrack state. Is there a tracker supporting
   batched inference with per-stream state?
10. Are the custom smoking (`best.pt`) and fire/smoke (`fire_smoke.pt`) models characterised
    at all? Neither has a documented training set, a class mapping beyond
    `{0: fire, 1: smoke}`, or a validation number. The default confidences (0.75, and 0.6
    from the UI) look like empirical false-positive suppression rather than calibration.
11. Phone detection uses COCO class 67 on a general YOLO at conf 0.25 — cheap, but "person
    holding a phone" is inferred purely from box-centre containment. A dedicated
    phone-use or pose-based classifier would likely be far more precise.

**D. Architecture and scale**

12. One GPU child, one sequential loop. What is the actual camera ceiling at ~2 analytics
    fps with modules enabled, and where does it break first — VRAM, queue drops, or the
    writer?
13. The writer is a single synchronous psycopg2 thread doing per-result commits plus
    `ILIKE` employee lookups. Profile it as a throughput bound; batching and a cached
    `photo_base → employee_id` map already exist partially (`emp_id_cache`, `name_cache`).
14. Head-count totals are rebuilt by counting rows at startup — an O(history) query that
    grows without bound. Is a materialised counter warranted?
15. `alert_logs` has no retention policy on this branch (`Tenant.retention_days` exists on
    the SaaS branch but is unused). Snapshots accumulate in `media/snapshots/` indefinitely.

**E. Operations and correctness**

16. Merge the two Alembic heads and decide whether `create_tables()` should stay.
17. Generate `lib/modules.ts` from `registry.py` at build time instead of maintaining a
    hand-written mirror.
18. Add a regression harness. There is no test suite on `stagging`, and the identity
    pipeline has at least six interacting heuristics, each added to fix a specific
    production bug. Recorded-clip fixtures with expected event streams would be the
    highest-value addition. The `sass-under-testing` branch's `tests/` — including
    `fake_recorder.py` — is a template worth porting.

---

## 17. Glossary

| Term | Meaning |
|---|---|
| **backbone** | a detector model shared by several modules (`person`, `face`, `phone`, `smoking`, `fire_smoke`) |
| **module** | a per-camera analytic, declared in `registry.py`, assigned via `camera_devices.module_config` |
| **consumer** | the stateful rule object implementing a module (`analytics/*.py`) |
| **`photo_base`** | basename of an employee's anchor photo — the identity token used throughout the pipeline |
| **anchor** | the permanent `manual` gallery vector encoded from the enrolment photo |
| **auto vector** | a runtime-captured gallery vector, capped at 8 per employee |
| **face-confirmed** | a track whose identity was bound by an actual face match (not re-id) — the only kind allowed to feed the appearance gallery |
| **inferred face** | an unmatched face attributed to the identity already bound to its track; displayed, never alerted or logged |
| **det frame** | the ≤ 1280 px-wide frame the detectors see; overlay coordinates are normalised back to 0–1 |
| **overlay primitive** | `{kind: box \| line \| zone \| label, ...}` in normalised coordinates, drawn by the main process |
