from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
import datetime
import json
from backend.core.database import get_db
from backend.core.dependencies import get_current_user, require_role
from backend.models.user import User, UserRole
from backend.models.settings import SystemSettings
from backend.models.alert import AlertLog

router = APIRouter(prefix="/settings", tags=["settings"])


def _parse_list(raw) -> list[str]:
    """Accept a JSON array string or comma-separated string; return clean list."""
    if not raw:
        return []
    raw = str(raw).strip()
    try:
        val = json.loads(raw)
        if isinstance(val, list):
            return [str(x).strip() for x in val if str(x).strip()]
    except (ValueError, TypeError):
        pass
    return [p.strip() for p in raw.split(",") if p.strip()]


async def _get_or_create_settings(db: AsyncSession) -> SystemSettings | None:
    """system_settings is left alone on the remote DB (may not exist yet) — return None
    so callers fall back to defaults instead of erroring out."""
    try:
        result = await db.execute(select(SystemSettings).where(SystemSettings.id == 1))
        s = result.scalar_one_or_none()
        if not s:
            s = SystemSettings(id=1)
            db.add(s)
            await db.commit()
            await db.refresh(s)
        return s
    except Exception:
        await db.rollback()
        return None


@router.get("/")
async def get_settings(db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)):
    s = await _get_or_create_settings(db)
    if s is None:
        return {
            "office_open_time": "09:00",
            "office_close_time": "18:00",
            "locations": [],
            "departments": [],
        }
    return {
        "office_open_time": s.office_open_time.strftime("%H:%M"),
        "office_close_time": s.office_close_time.strftime("%H:%M"),
        "locations": _parse_list(s.locations),
        "departments": _parse_list(s.departments),
    }


@router.put("/")
async def update_settings(
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.admin)),
):
    form = await request.form()
    open_time = form.get("office_open_time")
    close_time = form.get("office_close_time")
    locations = form.get("locations")
    departments = form.get("departments")
    s = await _get_or_create_settings(db)
    if open_time:
        s.office_open_time = datetime.time.fromisoformat(open_time)
    if close_time:
        s.office_close_time = datetime.time.fromisoformat(close_time)
    if locations is not None:
        s.locations = json.dumps(_parse_list(locations))
    if departments is not None:
        s.departments = json.dumps(_parse_list(departments))
    await db.commit()
    return {"status": "success"}


# ── False-detection layer ────────────────────────────────────────────────────────
# Two endpoints beside the existing ones; nothing above is changed. Both are guarded by
# require_role like every other mutating endpoint in the project.

@router.get("/false-detection")
async def get_false_detection(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Current configuration plus the last 24 h of counters.

    The counters are the point of this screen: they are what lets an operator see the
    layer's effect in `observe` mode BEFORE trusting it to remove anything."""
    from sqlalchemy import func
    from backend.services.false_detection import FDConfig

    s = await _get_or_create_settings(db)
    stored = {}
    if getattr(s, "false_detection", None):
        try:
            stored = json.loads(s.false_detection)
        except (ValueError, TypeError):
            stored = {}
    cfg = FDConfig.from_env().merged(stored)

    since = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=24)
    rows = (await db.execute(
        select(AlertLog.severity, func.count())
        .where(AlertLog.timestamp >= since)
        .group_by(AlertLog.severity)
    )).all()
    by_severity = {(sev or "unclassified"): n for sev, n in rows}

    return {
        "config": cfg.to_dict(),
        "stored": stored,
        "defaults": FDConfig().to_dict(),
        "counters": {
            "window_hours": 24,
            "alerts_written": sum(by_severity.values()),
            "by_severity": by_severity,
        },
    }


@router.put("/false-detection")
async def update_false_detection(
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.admin, UserRole.supervisor)),
):
    """Replace the stored configuration.

    Validated before it is saved, so a bad value can never reach the writer thread - and
    every change is written to the audit log with who made it."""
    from backend.services.false_detection import FDConfig
    from backend.services.false_detection.engine import FILTER

    try:
        incoming = await request.json()
    except Exception:
        from fastapi import HTTPException
        raise HTTPException(status_code=400, detail="expected a JSON object")
    if not isinstance(incoming, dict):
        from fastapi import HTTPException
        raise HTTPException(status_code=400, detail="expected a JSON object")

    s = await _get_or_create_settings(db)
    before = {}
    if getattr(s, "false_detection", None):
        try:
            before = json.loads(s.false_detection)
        except (ValueError, TypeError):
            before = {}

    # Validate by round-tripping through the config object. Anything out of range is
    # clamped rather than rejected, and anything unknown is dropped - so a payload from
    # a newer build cannot half-apply.
    merged = FDConfig.from_env().merged(before).merged(incoming)
    stored = {k: v for k, v in merged.to_dict().items()}
    s.false_detection = json.dumps(stored)
    await db.commit()

    # Audit every field that actually moved.
    for key, after in stored.items():
        if before.get(key) != after and key in incoming:
            FILTER.log_config_change(
                getattr(current_user, "username", "?"),
                getattr(current_user, "id", None),
                "global", key, before.get(key), after)

    # Apply immediately: the layer is meant to be switchable without a restart, which is
    # also the fastest rollback there is.
    try:
        FILTER.set_config(merged, FILTER._overrides)
    except Exception as e:
        print(f"[FALSE-DETECTION] live reload failed: {e}", flush=True)

    return {"status": "success", "config": stored}


# ── Global capability toggles (Kohat POC) ────────────────────────────────────────
# A kill switch per capability, layered on top of the per-camera assignments in
# camera_devices.module_config. See backend/services/module_toggles.py for why both
# granularities exist: per-camera is for configuration, this is for an emergency.

@router.get("/modules")
async def get_module_toggles(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Every registered capability with its resolved global state, plus enough registry
    metadata for the admin panel to render the list without hard-coding it."""
    from backend.services import module_toggles
    from backend.services.modules.registry import MODULES

    s = await _get_or_create_settings(db)
    stored = {}
    if getattr(s, "module_toggles", None):
        try:
            stored = json.loads(s.module_toggles)
        except (ValueError, TypeError):
            stored = {}
    if not isinstance(stored, dict):
        stored = {}

    # Resolve against the registry rather than trusting what is stored: a module removed
    # from the code must disappear from the panel, not linger as a phantom row.
    modules = [
        {
            "id": mid,
            "name": meta.get("name", mid),
            "phase": meta.get("phase"),
            "backbone": meta.get("backbone"),
            "geometry": meta.get("geometry"),
            "event_types": meta.get("event_types", []),
            "description": meta.get("description", ""),
            # Absent from the stored map means ENABLED - never silently off.
            "enabled": bool(stored.get(mid, True)),
            "configured": mid in stored,
        }
        for mid, meta in MODULES.items()
    ]
    return {
        "modules": modules,
        "stored": stored,
        "live": module_toggles.effective(),
    }


@router.put("/modules")
async def update_module_toggles(
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.admin, UserRole.supervisor)),
):
    """Replace the stored toggle map and apply it immediately.

    Applied live for the same reason the false-detection settings are: this is the switch
    someone reaches for when a capability is misbehaving in production, and it is useless
    if it needs a restart."""
    from fastapi import HTTPException
    from backend.services import module_toggles

    try:
        incoming = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="expected a JSON object")
    if not isinstance(incoming, dict):
        raise HTTPException(status_code=400, detail="expected a JSON object")

    s = await _get_or_create_settings(db)
    before = {}
    if getattr(s, "module_toggles", None):
        try:
            before = json.loads(s.module_toggles)
        except (ValueError, TypeError):
            before = {}
    if not isinstance(before, dict):
        before = {}

    # Merge, then validate through the service - which drops unknown module ids, so a
    # stale name from an older build cannot persist.
    merged = {**before, **{k: bool(v) for k, v in incoming.items()}}
    clean = module_toggles.set_toggles(merged)
    s.module_toggles = json.dumps(clean)
    await db.commit()

    # Audit through the false-detection log: it is the existing per-parameter audit
    # trail, and "who switched a whole capability off" belongs in it at least as much as
    # a threshold change does.
    try:
        from backend.services.false_detection.engine import FILTER
        for key, after in clean.items():
            if before.get(key) != after and key in incoming:
                FILTER.log_config_change(
                    getattr(current_user, "username", "?"),
                    getattr(current_user, "id", None),
                    "module_toggle", key, before.get(key), after)
    except Exception as e:
        print(f"[MODULES] toggle audit skipped: {e}", flush=True)

    off = sorted(k for k, v in clean.items() if not v)
    print(f"[MODULES] toggles updated by "
          f"{getattr(current_user, 'username', '?')}; "
          f"disabled: {', '.join(off) or 'none'}", flush=True)
    return {"status": "success", "toggles": clean, "live": module_toggles.effective()}
