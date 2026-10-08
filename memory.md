# VIGIL — System Reference

## Stack

| Layer | Technology |
|---|---|
| Backend | FastAPI + Uvicorn, Python 3.11, Conda env `ppe` |
| Database | PostgreSQL (`ppe_new` DB, user `postgres`/`admin`, port 5432) |
| ORM | SQLAlchemy async (asyncpg driver) + Alembic migrations |
| Auth | JWT — HttpOnly cookies (`access_token` + `refresh_token`) |
| AI | Ultralytics YOLO (`ppe.pt` + `yolo11n.pt`), DeepFace ArcFace + RetinaFace |
| Frontend | React 19, TypeScript, Vite, Tailwind CSS |
| Streaming | WebSocket per camera (`/api/stream/ws/{cam_id}`) |

---

## How It All Connects

```
Browser
  └─ React SPA (served from /static/ by FastAPI)
       ├─ REST calls  → /api/...          (JSON over HTTP, cookies carry JWT)
       ├─ WebSocket   → /api/stream/ws/{id}  (binary JPEG frames, one per camera)
       └─ Media files → /media/...        (snapshots, employee photos)

FastAPI (port 8000)
  ├─ Async routers  → PostgreSQL (asyncpg)
  ├─ CameraManager  → per-camera: capture thread + AI thread
  │     └─ AI thread pushes JPEG → ConnectionManager → WebSocket clients
  ├─ FaceWorker     → background daemon thread, drains face_queue
  │     └─ Writes AlertLog / Attendance rows via sync SQLAlchemy session
  └─ ModelSwapper   → holds YOLO model handles, switches PPE ↔ Face mode
```

**Startup sequence** (`main.py` lifespan):
1. `create_tables()` — ensures all ORM tables exist
2. `load_ppe_models()` — loads `ppe.pt` + `yolo11n.pt` into memory
3. `start_face_worker()` — starts background face recognition daemon
4. `connection_manager.set_loop(...)` — gives the sync AI thread access to the async event loop for WebSocket broadcasting

---

## Database Structure

### `users`
| Column | Type | Notes |
|---|---|---|
| id | int PK | |
| username | varchar(100) | unique |
| email | varchar(255) | unique, nullable |
| hashed_password | varchar(255) | bcrypt |
| role | enum | `admin`, `supervisor`, `operator`, `viewer` |
| is_active | bool | default true |
| created_at | timestamptz | server default now() |

### `employees`
| Column | Type | Notes |
|---|---|---|
| id | int PK | |
| name | varchar(100) | |
| employee_id | varchar(50) | unique — e.g. `EMP-001` |
| photo | varchar(500) | relative path under `media/` — e.g. `employees/EMP-001_face.jpg` |

### `camera_devices`
| Column | Type | Notes |
|---|---|---|
| id | int PK | |
| name | varchar(100) | |
| ip_address | varchar(100) | nullable (NVR channels have no direct IP) |
| username | varchar(100) | nullable |
| password | varchar(100) | nullable |
| channel | int | default 102 |
| process_frame_rate | int | AI processes every Nth frame |
| is_active | bool | |
| nvr_id | int FK | → `nvr_devices.id` ON DELETE CASCADE, nullable |

### `nvr_devices`
| Column | Type | Notes |
|---|---|---|
| id | int PK | |
| name | varchar(100) | |
| ip_address | varchar(100) | |
| port | int | default 554 |
| username | varchar(100) | nullable |
| password | varchar(100) | nullable |
| rtsp_template_id | int FK | → `rtsp_string_templates.id` ON DELETE SET NULL |

### `rtsp_string_templates`
| Column | Type | Notes |
|---|---|---|
| id | int PK | |
| name | varchar(100) | unique |
| template_string | text | e.g. `rtsp://{username}:{password}@{ip}:{port}/ch{channel}` |

RTSP URL is built in `CameraDevice.get_rtsp_url()`: replaces `{ip}`, `{port}`, `{username}`, `{password}`, `{channel}` with NVR values.

### `alert_logs`
| Column | Type | Notes |
|---|---|---|
| id | int PK | |
| employee_id | int FK | → `employees.id` ON DELETE SET NULL, nullable |
| unknown_person | bool | true when face not matched |
| violation_type | varchar(255) | e.g. `Missing Hardhat`, `Face Identification Scan` |
| distance_score | float | ArcFace cosine distance (lower = better match) |
| likely_candidate | varchar(100) | best-guess name when unknown, nullable |
| snapshot | varchar(500) | relative path e.g. `snapshots/a_abc12345.jpg` |
| timestamp | timestamptz | server default now() |

### `attendance`
| Column | Type | Notes |
|---|---|---|
| id | int PK | |
| employee_id | int FK | → `employees.id` ON DELETE CASCADE |
| date | date | |
| check_in | timestamptz | nullable |
| check_out | timestamptz | nullable |
| status | varchar(100) | `On Time`, `Late`, `Early Departure`, `Completed`, `Late & Early Departure` |

Unique constraint: `(employee_id, date)` — one row per person per day.

### `system_settings`
| Column | Type | Notes |
|---|---|---|
| id | int PK | always 1 (singleton) |
| office_open_time | time | default 09:00 — used to mark attendance as Late |
| office_close_time | time | default 18:00 — used to mark Early Departure |

---

## RBAC

| Role | Permissions |
|---|---|
| `admin` | Everything |
| `supervisor` | Read all + manage employees + clear alerts + switch AI mode |
| `operator` | Read all + view camera streams |
| `viewer` | Read-only (no streams) |

---

## API Endpoints

All routes prefixed `/api/`. Auth via HttpOnly cookie `access_token`.

### Auth — `/api/auth/`

| Method | Path | Auth | Body | Returns |
|---|---|---|---|---|
| POST | `/auth/login` | None | form: `username`, `password` | Sets cookies, returns `{role, username}` |
| POST | `/auth/logout` | Any | — | Clears cookies |
| POST | `/auth/refresh` | refresh cookie | — | Issues new access token |
| GET | `/auth/me` | Any | — | `{id, username, email, role, is_active}` |

### Users — `/api/users/` (Admin only)

| Method | Path | Body |
|---|---|---|
| GET | `/users/` | — |
| POST | `/users/` | `{username, email, password, role}` |
| PUT | `/users/{id}` | `{username?, email?, password?, role?, is_active?}` |
| DELETE | `/users/{id}` | — |

### Employees — `/api/employees/`

| Method | Path | Auth | Body |
|---|---|---|---|
| GET | `/employees/` | Any | — |
| POST | `/employees/` | Admin, Supervisor | multipart: `name`, `employee_id`, `photo` (file) |
| PUT | `/employees/{id}` | Admin, Supervisor | multipart: `name`, `employee_id`, `photo?` |
| DELETE | `/employees/{id}` | Admin, Supervisor | — |

### Cameras — `/api/cameras/`

| Method | Path | Auth | Body / Query |
|---|---|---|---|
| GET | `/cameras/?ip_only=bool` | Any | `ip_only=true` filters NVR-linked cameras out |
| POST | `/cameras/` | Admin | `{name, ip_address, username?, password?, channel, process_frame_rate, is_active}` |
| PUT | `/cameras/{id}` | Admin | same fields, all optional |
| DELETE | `/cameras/{id}` | Admin | — |

`is_active` in the response is live-checked: tries the running `CameraInstance`, falls back to TCP port 554 probe, then ping.

### NVRs — `/api/nvrs/`

| Method | Path | Auth | Body |
|---|---|---|---|
| GET | `/nvrs/` | Any | — (includes channels array) |
| POST | `/nvrs/` | Admin | `{name, ip_address, port, username?, password?, rtsp_template?}` |
| PUT | `/nvrs/{id}` | Admin | same fields |
| DELETE | `/nvrs/{id}` | Admin | — (cascades to channels) |
| POST | `/nvrs/{id}/channels/` | Admin | query: `channel`, `process_frame_rate` |

### RTSP Templates — `/api/rtsp-templates/`

| Method | Path | Auth |
|---|---|---|
| GET | `/rtsp-templates/` | Any |
| POST | `/rtsp-templates/` | Admin |
| PUT | `/rtsp-templates/{id}` | Admin |
| DELETE | `/rtsp-templates/{id}` | Admin |

Body: `{name, template_string}` — template uses `{ip}`, `{port}`, `{username}`, `{password}`, `{channel}` placeholders.

### Alerts — `/api/alerts/`

| Method | Path | Auth | Notes |
|---|---|---|---|
| GET | `/alerts/?limit=100` | Any | Returns latest N alerts, timestamps in Asia/Karachi |
| DELETE | `/alerts/` | Admin, Supervisor | Deletes all alert rows + snapshot files, resets cooldown timers |

### Attendance — `/api/attendance/`

| Method | Path | Auth | Query |
|---|---|---|---|
| GET | `/attendance/?date=YYYY-MM-DD` | Any | `date` filters to a single day; omit for all records |

Returns array of `{id, employee: {name, employee_id, photo}, date, check_in, check_out, status}`.

### Settings — `/api/settings/`

| Method | Path | Auth | Body |
|---|---|---|---|
| GET | `/settings/` | Any | — |
| PUT | `/settings/` | Admin | form: `office_open_time` (HH:MM), `office_close_time` (HH:MM) |

### Mode — `/api/mode/`

| Method | Path | Auth | Notes |
|---|---|---|---|
| GET | `/mode/` | Any | Returns `{mode: "ppe" \| "face"}` |
| POST | `/mode/?mode=ppe\|face` | Admin, Supervisor | Switches AI model; runs in thread executor (non-blocking) |

### Stream — `/api/stream/`

| Method | Path | Auth | Notes |
|---|---|---|---|
| GET | `/stream/snapshot?cam_id=N` | Operator+ | Returns single JPEG (fallback for non-WS clients) |
| GET | `/stream/video?cam_id=N` | Operator+ | MJPEG `multipart/x-mixed-replace` stream (legacy) |
| WS | `/stream/ws/{cam_id}` | Any authenticated | Binary WebSocket — server pushes JPEG frames as they are produced by the AI loop |

---

## AI Pipeline

### PPE Mode (`ppe.pt` + `yolo11n.pt`)
1. Every `process_frame_rate`-th frame is processed
2. `ppe.pt` (conf 0.5) detects violations — any label containing `no-` or `not`
3. `yolo11n.pt` (COCO, conf 0.2) detects persons; each person crop re-checked for phone (class 67, conf 0.15)
4. Violating persons are face-cropped (top 20% + margins) and queued to `face_queue`
5. `FaceWorker` runs ArcFace recognition → writes `AlertLog`

### Face Mode (`yolo11n.pt` only)
1. COCO detects persons (conf 0.25)
2. Head region cropped (top 45% of person box + 15% margins)
3. Queued to `face_queue` with `v_type = "Face Identification Scan"`
4. `FaceWorker` runs ArcFace recognition
5. On match (cosine distance < 0.65):
   - Camera 19 = check-in cam → writes/updates `Attendance` row
   - Camera 20 = checkout cam → sets `check_out` time
   - Other cams → writes `AlertLog` only

### Recognition thresholds
- ArcFace cosine distance **< 0.65** = recognized
- Face image upscaled if height < 400px before recognition
- 3-second cooldown per grid cell to avoid duplicate logs

### Mode switching
- Runs in `asyncio.run_in_executor` (non-blocking for the event loop)
- `SYSTEM_MODE` only updated on successful model load
- Failed load sets `MODELS_LOADED = False` so retry is not skipped

---

## WebSocket Streaming Architecture

```
CameraInstance._ai_loop (thread)
  └─ produces annotated JPEG
       └─ connection_manager.broadcast_sync(cam_id, jpeg_bytes)
            └─ asyncio.run_coroutine_threadsafe(broadcast(), event_loop)
                 └─ sends bytes to all connected WebSocket clients for that cam_id

Browser
  └─ useCameraStream(camId) hook
       ├─ opens ws://{host}/api/stream/ws/{cam_id}
       ├─ receives ArrayBuffer → Blob → draws on <canvas>
       └─ auto-reconnects with exponential backoff (1s → 2s → 4s → max 10s)
```

JPEG encoding only happens when at least one client is connected (`connection_manager.has_clients(cam_id)`).

---

## File Paths

```
media/
  employees/   — employee photos ({employee_id}_{filename})
  snapshots/   — alert snapshots (a_{uuid8}.jpg)
  debug_face.jpg — last face crop sent to DeepFace (debug)

ppe.pt         — PPE violation YOLO model (git-ignored, must be present)
yolo11n.pt     — COCO person/phone YOLO model (git-ignored, must be present)

frontend/dist/ — built React SPA (committed to git, served by FastAPI)
```

---

## Running

```bash
# Backend
conda activate ppe
uvicorn backend.main:app --host 0.0.0.0 --port 8000 --reload

# Frontend (dev only — auto-reloads without rebuilding)
cd frontend && npm run dev

# Frontend (production build — required for FastAPI to serve updated UI)
cd frontend && npm run build

# Migrations
alembic upgrade head

# Create default admin user
python backend/scripts/create_admin.py

# Linux binary launcher
./dist/VIGIL
```

Default admin credentials: `admin` / `admin123`
