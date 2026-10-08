"""
Start every camera that has analytics assigned, at application boot.

WHY THIS EXISTS — the gap it closes
------------------------------------
Until this file, a camera was only ever started by somebody LOOKING at it. The two call
sites were the WebSocket stream handler and `get_frame_bytes`, both on the viewing path.
Nothing in the application's lifespan opened a camera.

The consequence, reported from the deployment and confirmed in the code: after a restart,
**no detection ran and no alerts were produced until an operator opened the live feed**.
The perception child was up, the writer was up, the modules were configured — and nothing
was being watched, so nothing was being analysed.

A surveillance system that only detects while a human is looking at the screen is not
doing surveillance. It is a video player with a detector attached.

WHAT THIS CHANGES
    At start-up, every ACTIVE camera with at least one enabled module is opened and kept
    running, exactly as though a viewer had opened it. Cameras with no analytics assigned
    are left alone — they are pure viewing sources and there is nothing for them to
    detect, so they still start on demand and still idle-stop.

COST, STATED PLAINLY
    Every analytics camera now decodes continuously instead of only while watched. That
    is the point, and it is not free. It is affordable because the busy-spin fix took the
    main process from 945% of a core to 35%; it would not have been before.

    If the machine cannot sustain it, the answer is fewer cameras or fewer fast-lane
    modules — not silently detecting nothing. `AUTOSTART_CAMERAS=0` restores the old
    viewer-driven behaviour for anyone who wants it.

WHERE IT RUNS
    Main process, from the FastAPI lifespan, after `start_perception()` so the queues and
    the writer thread exist before any frame is produced.
"""
from __future__ import annotations

import os
import threading

AUTOSTART = os.environ.get("AUTOSTART_CAMERAS", "1").strip().lower() not in (
    "0", "false", "no", "off")

# Opening RTSP sources is slow and can block for seconds each when a camera is
# unreachable. Doing that inline would delay the application becoming ready, so the sweep
# runs on its own thread and the app serves requests immediately.
START_DELAY = float(os.environ.get("AUTOSTART_DELAY", "2.0"))


def _cameras_with_modules():
    """Active cameras that have at least one enabled analytic.

    Uses a synchronous session for the same reason `fd_load_config` does: this runs
    during start-up, outside the request event loop, and a sync query here keeps it
    simple and self-contained."""
    from sqlalchemy import create_engine, select
    from sqlalchemy.orm import sessionmaker
    from backend.core.config import settings
    from backend.models.camera import CameraDevice

    engine = create_engine(
        settings.DATABASE_URL.replace("+asyncpg", "+psycopg2"), pool_pre_ping=True)
    Session = sessionmaker(bind=engine)
    out = []
    with Session() as db:
        for cam in db.execute(select(CameraDevice)).scalars().all():
            if not getattr(cam, "is_active", True):
                continue
            try:
                mods = [m for m in (cam.get_modules() or [])
                        if m.get("enabled", True) and m.get("module")]
            except Exception:
                mods = []
            if not mods:
                continue                      # nothing to detect; leave it on demand
            try:
                url = cam.get_rtsp_url()
            except Exception:
                url = None
            if not url:
                continue
            out.append((cam.id, url, int(getattr(cam, "process_frame_rate", 10) or 10),
                        mods))
    return out


def _sweep():
    import time
    time.sleep(START_DELAY)
    try:
        from backend.services import module_toggles
        from backend.services.camera_manager import camera_manager
    except Exception as e:
        print(f"[AUTOSTART] skipped: {e}", flush=True)
        return

    try:
        cams = _cameras_with_modules()
    except Exception as e:
        print(f"[AUTOSTART] could not read cameras ({e}); "
              f"detection will start only when a live feed is opened.", flush=True)
        return

    if not cams:
        print("[AUTOSTART] no camera has analytics assigned — nothing to start. "
              "Detection will not run until a module is assigned to a camera.",
              flush=True)
        return

    started = 0
    for cam_id, url, fps, mods in cams:
        # A module switched off globally should not cause a camera to be opened for it.
        live = module_toggles.filter_modules(mods)
        if not live:
            print(f"[AUTOSTART] camera {cam_id}: every assigned module is globally "
                  f"disabled, not starting", flush=True)
            continue
        try:
            inst = camera_manager.get_instance(url, fps, cam_id, mods)
            inst.start()
            started += 1
            names = ", ".join(sorted(m["module"] for m in live))
            print(f"[AUTOSTART] camera {cam_id} started: {names}", flush=True)
        except Exception as e:
            # One unreachable camera must not stop the others. The instance's own
            # exponential backoff handles retrying it.
            print(f"[AUTOSTART] camera {cam_id} failed to start: {e}", flush=True)

    print(f"[AUTOSTART] {started}/{len(cams)} analytics camera(s) running. "
          f"Detection is now active without anyone watching a live feed.", flush=True)


def start_all() -> None:
    """Kick off the start-up sweep. Returns immediately."""
    if not AUTOSTART:
        print("[AUTOSTART] disabled (AUTOSTART_CAMERAS=0) — detection will only run "
              "while a live feed is open.", flush=True)
        return
    threading.Thread(target=_sweep, name="CameraAutostart", daemon=True).start()
