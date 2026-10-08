"""
Global per-capability on/off switches.

WHY THIS EXISTS ALONGSIDE THE PER-CAMERA ASSIGNMENTS
----------------------------------------------------
Modules are already assignable per camera through `camera_devices.module_config`. That is
the right granularity for configuration, and it is the wrong granularity for an
emergency.

If a newly-deployed capability starts producing false alerts across a site, an operator
needs ONE switch that stops it everywhere — not an edit to every camera in turn, which
is slow, error-prone, and destroys the per-camera configuration they will want back when
the problem is fixed. That is what these toggles are: a global kill switch layered ON TOP
of the per-camera assignments, leaving those assignments untouched.

SEMANTICS — deliberately conservative
    Absent from the map      -> ENABLED. A module nobody has ever toggled behaves exactly
                                as it did before this file existed. Adding a capability
                                must never silently disable it.
    Explicitly false         -> disabled everywhere, per-camera config ignored but kept.
    Explicitly true          -> enabled, subject to the per-camera assignment.

So a toggle can only ever SUBTRACT from what the cameras asked for. It cannot switch a
module on for a camera that has not been given it.

WHERE IT IS ENFORCED
    `camera_manager.CameraInstance._process_frame` filters the module list before it
    reaches the perception queue, and `_check_tamper` consults it directly. Filtering at
    that single point means the perception child never learns about a globally-disabled
    module, so no detector backbone is loaded for it and no VRAM is spent.

    It is NOT enforced in the child, which has no DB access — the architectural rule
    since day one.

CACHING
    Read from `system_settings.module_toggles` once and held in memory, because
    `_process_frame` runs at frame rate and must not touch the database. `refresh()` is
    called at start-up and whenever the admin panel saves, which is the same live-reload
    contract the false-detection layer uses.
"""
from __future__ import annotations

import json
import threading

_lock = threading.Lock()
_toggles: dict[str, bool] = {}
_loaded = False


def set_toggles(mapping: dict | None) -> dict[str, bool]:
    """Replace the in-memory map. Returns what was actually stored.

    Unknown module ids are dropped rather than kept: a stale name in the database would
    otherwise linger forever and show up in the admin panel as a phantom capability."""
    from backend.services.modules.registry import MODULES

    global _loaded
    clean: dict[str, bool] = {}
    for k, v in (mapping or {}).items():
        if k in MODULES:
            clean[str(k)] = bool(v)
    with _lock:
        _toggles.clear()
        _toggles.update(clean)
        _loaded = True
    return clean


def all_toggles() -> dict[str, bool]:
    """The stored map only — absent keys are NOT filled in, so a caller can tell the
    difference between "explicitly enabled" and "never configured"."""
    with _lock:
        return dict(_toggles)


def effective() -> dict[str, bool]:
    """Every registered module with its resolved state. What the admin panel renders."""
    from backend.services.modules.registry import MODULES

    stored = all_toggles()
    return {mid: stored.get(mid, True) for mid in MODULES}


def enabled(module_id: str) -> bool:
    """Is this capability globally on? Absent means yes — see the module docstring."""
    with _lock:
        return _toggles.get(module_id, True)


def filter_modules(modules: list | None) -> list:
    """Drop globally-disabled entries from a per-camera module list.

    Hot path: called once per frame per camera, so it does no I/O and returns the
    original list object untouched when nothing is disabled."""
    if not modules:
        return modules or []
    with _lock:
        if not _toggles:
            return modules          # nothing has ever been toggled; skip the work
        off = {k for k, v in _toggles.items() if not v}
    if not off:
        return modules
    return [m for m in modules if m.get("module") not in off]


def refresh() -> dict[str, bool]:
    """Load from the database. Called at start-up and after an admin-panel save.

    Failure is not fatal and deliberately leaves the previous map in place: if the
    database is briefly unreachable, continuing with the last known configuration is
    safer than silently re-enabling something an operator switched off."""
    from sqlalchemy import create_engine, select
    from sqlalchemy.orm import sessionmaker
    from backend.core.config import settings
    from backend.models.settings import SystemSettings

    try:
        engine = create_engine(
            settings.DATABASE_URL.replace("+asyncpg", "+psycopg2"), pool_pre_ping=True)
        Session = sessionmaker(bind=engine)
        with Session() as db:
            row = db.execute(
                select(SystemSettings).where(SystemSettings.id == 1)).scalar_one_or_none()
            raw = getattr(row, "module_toggles", None) if row is not None else None
        stored = json.loads(raw) if raw else {}
        clean = set_toggles(stored if isinstance(stored, dict) else {})
        off = sorted(k for k, v in clean.items() if not v)
        if off:
            print(f"[MODULES] globally disabled: {', '.join(off)}", flush=True)
        else:
            print("[MODULES] all capabilities globally enabled", flush=True)
        return clean
    except Exception as e:
        print(f"[MODULES] toggle load skipped ({e}); "
              f"keeping {'previous' if _loaded else 'default all-enabled'} state.",
              flush=True)
        return all_toggles()


def reset() -> None:
    """Drop cached state (tests)."""
    global _loaded
    with _lock:
        _toggles.clear()
        _loaded = False
