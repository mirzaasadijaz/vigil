"""
Perception worker — shared multi-module AI pipeline (PyTorch / ONNX, isolated process).

Generalizes the old single-purpose face_worker. Per camera it runs only the detector
*backbones* its assigned modules need (person via YOLOv11n+ByteTrack, face via SCRFD+
InsightFace), once per frame, then fans the detections out to each module's rule
consumer. The child emits plain Events + overlay primitives; the main process persists
Events/Attendance and publishes overlays. No DB or torch in the main process path that
the GPU child touches — same isolation that kept the stack stable.
"""
import os
import json
import time
import uuid
import threading
import multiprocessing as mp
import requests
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from backend.core.config import settings
from backend.services import shard_debug as shard_log
from backend.services import shards

MAX_QUEUE = 50
TZ = ZoneInfo("Asia/Karachi")
EMP_COOLDOWN = 60
UNKNOWN_COOLDOWN = int(os.environ.get("UNKNOWN_COOLDOWN", "60"))
MIN_CHECKOUT_GAP = 60
OVERLAY_TTL = 2.0
MESS_OPEN_COOLDOWN = int(os.environ.get("MESS_OPEN_COOLDOWN", "60"))
HR_PAYROLL_BASE_URL = settings.HR_PAYROLL_BASE_URL
ATTENDANCE_KEY = settings.ATTENDANCE_KEY

# ── Unknown-alert quality gate (#14: stop "too many unknown" spam) ────────────────
# A face that is too small or has a low detector score is an unreliable detection —
# on bad/distant feeds it produces a stream of bogus "Unknown" alerts. Require a
# minimum SCRFD score and face size before an unmatched face may raise an alert.
# The per-camera Attendance module `confidence_threshold` overrides the score floor,
# so operators can tune noisy cameras from the UI; env sets the global defaults.
UNKNOWN_MIN_SCORE = float(os.environ.get("UNKNOWN_MIN_SCORE", "0.62"))
UNKNOWN_MIN_PX = int(os.environ.get("UNKNOWN_MIN_PX", "44"))

# ── Heavy-detector throttles (#16 lag) ────────────────────────────────────────────
# The shared worker processes every camera sequentially; mobile (a 2nd YOLO pass) and
# fire/smoke (a separate model) roughly doubled per-frame cost after they were enabled.
# Run them every Nth processed frame instead of every frame — phone flags are carried
# forward between runs so overlays don't flicker, and fire persists for seconds anyway.
FIRE_EVERY = int(os.environ.get("FIRE_EVERY", "2"))
# Vehicles that are STOPPED are the thing being detected, so a low cadence costs nothing
# - a car does not stop and un-stop between evaluations. This is the cheapest possible
# setting for a second YOLO pass.
VEHICLE_EVERY = int(os.environ.get("VEHICLE_EVERY", "3"))
# PHONE_EVERY is gone: phone inference is no longer an Nth-frame throttle inside the
# slow lane, it has its own cadence (PHONE_INTERVAL) on the medium lane. See below.

# ── Fast lane (Phase 1) ───────────────────────────────────────────────────────────
# camera_manager runs two cadences. A `fast` task carries ONLY these modules and no
# JPEG payload: it exists so person tracking gets the ~10 fps ByteTrack needs to hold a
# walking person, without dragging face/phone/fire/smoking inference along with it.
# Everything else still arrives on the original ~2 fps lane, so their cost is unchanged.
# `tripwire` is here for the same reason headcount is, and it is not optional: it
# INHERITS head-count's crossing machine, which needs ~10 fps to hold a walking person
# across the line. On the 2 fps slow lane a person can vanish between frames entirely
# (measured in Phase 1: zero IoU overlap, total track loss) and the crossing is simply
# never seen. A trip wire that misses people is worse than no trip wire.
# Derived from the registry, not duplicated. See camera_manager for the reasoning:
# a module's lane is a property of the module and belongs beside its backbone and
# config, not in two hand-synchronised sets in two files.
from backend.services.modules.registry import (          # noqa: E402
    carried_by_lane as _carried_by_lane, lane_modules as _lane_modules,
    modules_for_lane as _modules_for_lane)

FAST_MODULES = _modules_for_lane("fast")
MEDIUM_MODULES = _modules_for_lane("medium")
LANE_MODULES = _lane_modules()
# How long a recognized identity stays bound to a person track after the track is
# last seen (covers brief occlusions / ByteTrack gaps). While the track is present
# the identity is refreshed every frame, so it survives the face leaving view.
IDENTITY_GRACE = 5.0
# Min seconds between smoking events per camera (overlays still draw every frame).
SMOKE_COOLDOWN = 15

# ── Temporal identity voting (continuity / noise reduction) ──────────────────────
# Per track we accumulate similarity-weighted votes for each candidate identity and
# bind the leader. This keeps recall HIGH on bad streams (a single solid frame already
# commits — VOTE_MIN == the lenient match threshold) while preventing per-frame flicker
# between two look-alikes (the leader must clearly beat the runner-up to switch).
VOTE_DECAY = 0.85          # fade stale votes each processed frame
VOTE_MARGIN = 1.25         # leader must beat runner-up by this ratio to (re)bind

# ── Labeling ambiguity guard (misID reduction; does NOT touch the absolute threshold) ──
# A matched face is accepted as an identity only when its best similarity clearly beats
# the 2nd-best EMPLOYEE by this margin. Margin is *separation*, not absolute similarity,
# so a clear-but-low-quality match still passes (bad-camera recall preserved) — only
# near-tied frames (two people almost equally close) are demoted to "uncertain", which
# is exactly when we used to label one person as the other. Tunable via LABEL_MARGIN env;
# raise it if misID persists, lower it if legitimate people get missed.
LABEL_MARGIN = float(os.environ.get("LABEL_MARGIN", "0.08"))

# Temporary diagnostic: set FACE_DEBUG=1 to log every detected face's best match
# (key/sim/margin/score/size) — used to tune thresholds against a real scene.
FACE_DEBUG = os.environ.get("FACE_DEBUG", "0") != "0"

# ── Auto-enrollment gate (STRICT — protects the gallery from poisoning) ──────────
# Only very confident, well-separated, good-quality, non-duplicate faces grow a
# gallery. This is independent of (and stricter than) the runtime labeling threshold.
ENROLL_ENABLE = os.environ.get("AUTOENROLL", "1") != "0"
ENROLL_SIM = 0.70          # min cosine similarity to enroll — only very confident matches get enrolled
                           # so only confident matches get auto-enrolled.
ENROLL_MARGIN = 0.12       # min lead over the 2nd-best employee — the real poisoning guard
ENROLL_SCORE = 0.55        # min SCRFD detector score (sharp, real face)
ENROLL_MIN_PX = 48         # min face box side in pixels (not a distant blob)
ENROLL_DUP_SIM = 0.92      # reject near-duplicates of existing gallery vectors (keep angle diversity)
ENROLL_MAX_AUTO = 8        # cap on stored auto vectors per employee (more angles = better recall)

# ── Body Re-ID (OSNet) — cross-track re-bind + daily appearance gallery ──────────
REID_EVERY = 5             # run Re-ID every Nth processed frame (throttle GPU)
BODY_MAX = 3               # appearance crops kept per employee (daily, then expire)
BODY_MATCH = 0.62          # lenient appearance cosine to re-bind an unidentified track
REID_ENABLE = os.environ.get("REID", "1") != "0"   # kill switch: REID=0 disables body Re-ID

# ── Sharding ─────────────────────────────────────────────────────────────────────
# One task queue and one child PER SHARD; ONE result queue shared by all of them.
#
# The asymmetry is deliberate. Task queues must be per-shard because tasks have to be
# ROUTED — with a shared task queue any child could pick up any camera's frame, and a
# camera's ByteTrack state lives in exactly one process. The result queue can be shared
# because mp.Queue is multi-producer safe and the writer is already the sole consumer;
# `submit_events` has been writing to it from the MAIN process alongside the child since
# the tamper module landed, so multi-producer is not a new property here.
#
# PERCEPTION_SHARDS=1 (the default) collapses all of this back to exactly the previous
# behaviour: one queue, one child, no routing.
_task_queues: "list[mp.Queue]" = []
_result_queue: "mp.Queue | None" = None
_procs: "list[mp.Process]" = []
_writer: "threading.Thread | None" = None
_shard_map: "shards.ShardMap | None" = None
_shard_metrics = None

_overlay_lock = threading.Lock()
_overlays: dict[str, tuple[float, float, list]] = {}


# ── Overlay store (read by camera_manager to draw on the live feed) ───────────────
def set_overlay(cam_id, scale: float, boxes: list) -> None:
    if cam_id is None:
        return
    with _overlay_lock:
        _overlays[str(cam_id)] = (time.time(), scale, boxes)


def get_overlay(cam_id, max_age: float = OVERLAY_TTL):
    with _overlay_lock:
        v = _overlays.get(str(cam_id))
    if not v:
        return None
    ts, scale, boxes = v
    if time.time() - ts > max_age:
        return None
    return scale, boxes


def annotate_snapshot(frame_buf: bytes, overlays: list) -> bytes:
    """Draw the frame's overlay primitives (boxes/zones/lines) onto the raw snapshot
    JPEG so saved alert snapshots show the detection — not a bare frame. Runs in the
    main process (cv2 is safe here); recognition is unaffected since face crops are
    taken earlier in the GPU child from the un-annotated frame."""
    if not frame_buf or not overlays:
        return frame_buf
    try:
        import cv2
        import numpy as np
        from backend.services.camera_manager import draw_overlays_on_frame
        img = cv2.imdecode(np.frombuffer(frame_buf, dtype=np.uint8), cv2.IMREAD_COLOR)
        if img is None:
            return frame_buf
        draw_overlays_on_frame(img, overlays)
        ok, out = cv2.imencode(".jpg", img)
        return out.tobytes() if ok else frame_buf
    except Exception:
        return frame_buf


# ── Main-process API ──────────────────────────────────────────────────────────
def start_perception():
    global _task_queues, _result_queue, _procs, _writer, _shard_map, _shard_metrics
    if _procs and any(p.is_alive() for p in _procs):
        return
    n = shards.shard_count()
    ctx = mp.get_context("spawn")
    _shard_map = shards.ShardMap(n)
    _shard_map.load()
    _shard_metrics = shard_log.Metrics("router")
    # One result queue for ALL shards. See the comment on _task_queues: this is safe
    # because mp.Queue is multi-producer and the writer is the only consumer.
    _result_queue = ctx.Queue(maxsize=MAX_QUEUE * 4 * n)
    _task_queues = []
    _procs = []
    for i in range(n):
        q = ctx.Queue(maxsize=MAX_QUEUE)
        p = ctx.Process(target=_child_main, args=(q, _result_queue),
                        name=f"PerceptionProc-{i}" if n > 1 else "PerceptionProc",
                        daemon=True)
        p.start()
        _task_queues.append(q)
        _procs.append(p)
    _writer = threading.Thread(target=_result_writer, args=(_result_queue,), name="PerceptionWriter", daemon=True)
    _writer.start()
    pids = ", ".join(str(p.pid) for p in _procs)
    print(f"[INIT] Perception process{'es' if n > 1 else ''} started "
          f"({n} shard{'s' if n > 1 else ''}, pid={pids}); result-writer running.", flush=True)
    if n > 1:
        # Say this loudly and unconditionally, not only under SHARD_DEBUG. Sharding
        # changes which process handles a camera, and an operator debugging "camera 47
        # went quiet" needs to know that is even possible without first discovering a
        # flag they have never heard of.
        print(f"[SHARD] Sharding ENABLED: {n} perception children. "
              f"Camera assignment persists in {_shard_map.path}. "
              f"Set PERCEPTION_SHARDS=1 to disable.", flush=True)
    shard_log.session("router", {
        "shards": n,
        "person_ms": shards.PERSON_MS,
        "face_ms": shards.FACE_MS,
        "max_queue_per_shard": MAX_QUEUE,
        "map_path": _shard_map.path,
    }, extra={"pids": tuple(p.pid for p in _procs)})
    for i, p in enumerate(_procs):
        shard_log.info(event="SPAWN", shard=i, pid=p.pid)
    if _shard_map.known_any():
        shard_log.log_balance(_shard_map.snapshot())
    # State the false-detection switch explicitly. Silence here is ambiguous: a flag
    # that never arrived looks exactly like a flag that arrived with nothing to log.
    try:
        fd_load_config()
    except Exception as e:
        print(f"[FALSE-DETECTION] start-up skipped: {e}", flush=True)
    # Global capability toggles. Stated explicitly for the same reason as the line
    # above: an operator needs to see on start-up that something is switched off,
    # rather than wondering why a module they configured is producing nothing.
    try:
        from backend.services import module_toggles
        module_toggles.refresh()
    except Exception as e:
        print(f"[MODULES] toggle load skipped: {e}", flush=True)
    # State the diagnostic switches explicitly at start-up. Silence here used to be
    # ambiguous: a flag that never arrived looked exactly like a flag that arrived but
    # had nothing to log yet.
    try:
        from backend.services.analytics import headcount_debug as _hcd, phone_debug as _pd
        _hcd.announce("main")
        _pd.announce("main")
        for _mod in ("tripwire_debug", "crowd_debug", "vehicle_debug",
                     "tamper_debug", "intrusion_debug", "unattended_debug"):
            __import__("backend.services.analytics." + _mod,
                       fromlist=["announce"]).announce("main")
        # Pipeline performance diagnostics live one level up, in services/ rather than
        # analytics/, because they measure the capture and streaming path rather than any
        # single module.
        from backend.services import perf_debug as _perf
        _perf.announce("main")
        # Sharding lives at the same level - it routes work between processes rather
        # than deciding anything inside one module.
        shard_log.announce("main")
    except Exception as e:
        print(f"[INIT] diagnostic announce skipped: {e}", flush=True)


def stop_perception():
    global _procs
    stopped = 0
    for p in _procs:
        if p is not None and p.is_alive():
            p.terminate()
            p.join(timeout=5)
            if p.is_alive():
                p.kill()
            stopped += 1
    if stopped:
        print(f"[SHUTDOWN] Perception process{'es' if stopped > 1 else ''} stopped "
              f"({stopped}).", flush=True)


# ── Routing ──────────────────────────────────────────────────────────────────────
def shard_count() -> int:
    return len(_task_queues) or 1


def shard_of(cam_id) -> "int | None":
    """Which shard owns this camera, or None if it has not been placed yet.

    Never assigns. Use this everywhere except the frame-routing path — see
    ShardMap.peek() for what goes wrong otherwise."""
    if _shard_map is None or _shard_map.n <= 1:
        return 0
    return _shard_map.peek(cam_id)


def shard_for(cam_id, modules=None) -> int:
    """Which shard owns this camera, assigning it on first sight.

    Assignment is cost-balanced rather than `cam_id % N` — see shards.py for why that
    distinction is worth 6 points of peak-shard CPU on the reference deployment.

    `modules` is required to place a camera: without it the camera weighs nothing and
    every camera would collapse onto one shard. Called with no modules for a camera that
    has not been placed, this returns 0 provisionally and records nothing."""
    if _shard_map is None or _shard_map.n <= 1:
        return 0
    idx, is_new = _shard_map.assign(cam_id, modules)
    if is_new:
        snap = _shard_map.snapshot()
        loads = snap["loads"]
        if _shard_metrics is not None:
            _shard_metrics.bump("assigned")
        # Unconditional, like the start-up banner: which process owns a camera is the
        # first thing anybody debugging a quiet camera needs, and it happens once.
        print(f"[SHARD] camera {cam_id} -> shard {idx} "
              f"(weight {shards.weight_for(modules):.0f} ms/s; "
              f"loads {[round(v / 10.0, 1) for v in loads]} % of a core)", flush=True)
        shard_log.info(event="ASSIGN", camera=cam_id, shard=idx,
                       weight_ms=round(shards.weight_for(modules), 1),
                       modules=tuple(sorted(
                           m.get("module") for m in (modules or [])
                           if isinstance(m, dict) and m.get("module"))),
                       loads_pct=tuple(round(v / 10.0, 1) for v in loads))
        shard_log.log_balance(snap)
    return idx


def forget_camera(cam_id) -> None:
    """Drop a deleted camera from the routing table so its weight stops distorting
    future assignments. Safe to call for a camera that was never assigned."""
    if _shard_map is not None:
        _shard_map.forget(cam_id)
        shard_log.info(event="FORGET", camera=cam_id)


def _put(idx: int, task: dict) -> bool:
    try:
        _task_queues[idx].put_nowait(task)
        return True
    except Exception:
        return False


def enqueue(task: dict) -> bool:
    """Hand one task to the shard that owns its camera.

    A task carrying `cmd` and no `cam_id` is a GALLERY command and is broadcast to every
    shard instead — see the module docstring in shards.py. Routing one of those to a
    single shard would apply an employee edit to only 1/N of the cameras, and nothing
    anywhere would report it."""
    if not _task_queues:
        return False
    if len(_task_queues) == 1:
        return _put(0, task)                      # unsharded: no routing at all
    if task.get("cmd") and task.get("cam_id") is None:
        return broadcast(task)
    idx = shard_for(task.get("cam_id"), task.get("modules"))
    ok = _put(idx, task)
    if _shard_metrics is not None:
        _shard_metrics.bump("routed" if ok else "dropped")
        _shard_metrics.maybe_report()
    if not ok:
        shard_log.debug(event="DROP", camera=task.get("cam_id"), shard=idx,
                        lane=task.get("lane") or "slow", qsize=queue_size(task.get("cam_id")))
    elif shard_log.tracing():
        shard_log.trace(event="ROUTE", camera=task.get("cam_id"), shard=idx,
                        lane=task.get("lane") or "slow", cmd=task.get("cmd") or "-",
                        qsize=queue_size(task.get("cam_id")))
    return ok


def broadcast(task: dict) -> bool:
    """Send one command to EVERY shard.

    Used for anything that touches the face gallery, which is the only per-process state
    in the child that is not keyed by camera. Returns True if at least one shard took
    it — a partial delivery is still logged, because a gallery that is consistent on two
    shards out of three is a bug that would otherwise present as "recognition is worse
    on some cameras"."""
    if not _task_queues:
        return False
    ok = [i for i in range(len(_task_queues)) if _put(i, task)]
    if _shard_metrics is not None:
        _shard_metrics.bump("broadcast")
    if len(ok) != len(_task_queues):
        # Loud, not debug-only: a dropped gallery command leaves shards disagreeing
        # about who people are, and that is not something to discover from a log flag.
        print(f"[SHARD] WARNING: command {task.get('cmd')!r} reached only "
              f"{len(ok)}/{len(_task_queues)} shards - face galleries may now differ "
              f"between them. Run POST /api/system/reload to resynchronise.", flush=True)
        shard_log.info(event="BROADCAST_PARTIAL", cmd=task.get("cmd"),
                       delivered=tuple(ok), shards=len(_task_queues))
    else:
        shard_log.debug(event="BROADCAST", cmd=task.get("cmd"), shards=len(ok))
    return bool(ok)


def queue_size(cam_id=None) -> int:
    """Depth of the queue a camera's frames actually go to.

    Per-shard rather than global on purpose: `camera_manager` uses this for backpressure,
    and a global figure would let one busy shard throttle cameras belonging to the idle
    ones. With no cam_id, reports the deepest queue — the honest answer to "is the
    pipeline behind", which is what the diagnostics want."""
    if not _task_queues:
        return 0
    try:
        if cam_id is None:
            return max(q.qsize() for q in _task_queues)
        if len(_task_queues) == 1:
            return _task_queues[0].qsize()
        idx = shard_of(cam_id)
        if idx is None:
            # Not placed yet — this is the queue check that runs just BEFORE the
            # camera's first frame task. Report the shallowest queue, because that is
            # where the camera is about to be placed. Reporting the deepest would be
            # a deadlock: a busy shard would block the enqueue that does the placing,
            # so the camera would never be placed and never enqueue again.
            return min(q.qsize() for q in _task_queues)
        return _task_queues[idx].qsize()
    except Exception:
        return 0


def queue_sizes() -> list:
    """Every shard's depth, for diagnostics."""
    try:
        return [q.qsize() for q in _task_queues]
    except Exception:
        return []


def shard_health() -> list:
    """(index, pid, alive) per shard.

    A dead shard takes its cameras down silently — they simply stop being processed,
    with no exception anywhere, because the router keeps queueing to a queue nobody
    reads. This is the only thing that reports it."""
    return [{"shard": i, "pid": p.pid, "alive": bool(p.is_alive())}
            for i, p in enumerate(_procs)]


# ── Gallery commands — ALWAYS broadcast, never routed ────────────────────────────
# The face gallery is the one piece of child state not keyed by camera, so every shard
# holds its own copy and all of them must be told. `broadcast` rather than `enqueue`
# here even though enqueue would route these correctly on its own: the guarantee should
# be visible at the call site, so that adding a command later cannot quietly get it
# wrong. Unsharded, broadcast is exactly enqueue.
def notify_photo_added(full_path: str, photo_rel: str):
    broadcast({"cmd": "add", "path": full_path, "rel": photo_rel})


def notify_photo_removed(photo_rel: str):
    broadcast({"cmd": "remove", "rel": photo_rel})


def notify_extra_photo_added(full_path: str, anchor_rel: str):
    broadcast({"cmd": "add_extra", "path": full_path, "rel": anchor_rel})


def notify_reset_autos(photo_rel: str):
    broadcast({"cmd": "reset_autos", "key": os.path.basename(photo_rel)})


def reload_gallery(photos: "list[str] | None" = None, seed_vecs: list[dict] | None = None) -> bool:
    """Rebuild every child's face gallery from EXACTLY `photos` (employee-referenced rel
    paths), then re-seed stored auto vectors. FIFO order within each shard's queue
    guarantees the reload runs before the seed on that shard. `photos` comes from the
    writer/endpoint (which own DB access), so a stray file in media/employees/ is never
    loaded.

    This is also the repair path for shards whose galleries have drifted apart: it
    rebuilds all of them from the same DB snapshot."""
    ok = broadcast({"cmd": "reload_gallery", "photos": photos or []})
    if seed_vecs:
        broadcast({"cmd": "seed_vecs", "vecs": seed_vecs})
    return ok


def fanout_enrollments(origin_cam_id, enrolls: list) -> None:
    """Push auto-enrolled vectors to the OTHER shards.

    This closes the only behavioural difference sharding introduces. With one child, a
    vector learned from a live face on any camera was instantly usable by every other
    camera, because they shared one gallery. With N children it would only help cameras
    on the shard that learned it, until the next full reload — a slow, invisible
    degradation of recognition rather than a failure.

    `add_vecs` (not `seed_vecs`) on purpose: it calls `add_runtime_vector`, which
    enforces ENROLL_MAX_AUTO exactly as the originating shard did, so every shard ends
    up with the same gallery rather than one that grows past the cap."""
    if len(_task_queues) <= 1 or not enrolls:
        return
    # shard_of, not shard_for: an unplaced camera must not be placed by a fan-out.
    # None means "origin unknown", and -1 excludes nothing — every shard gets it, which
    # is the safe direction to be wrong in.
    origin = (shard_of(origin_cam_id) if origin_cam_id is not None else None)
    origin = -1 if origin is None else origin
    payload = {"cmd": "add_vecs",
               "vecs": [{"key": e.get("key"), "vec": e.get("vec"),
                         "quality": e.get("quality")} for e in enrolls if e.get("key")]}
    if not payload["vecs"]:
        return
    sent = [i for i in range(len(_task_queues)) if i != origin and _put(i, payload)]
    if _shard_metrics is not None:
        _shard_metrics.bump("fanout", len(payload["vecs"]))
    shard_log.debug(event="FANOUT", origin_shard=origin, camera=origin_cam_id,
                    vectors=len(payload["vecs"]), to_shards=tuple(sent),
                    keys=tuple(sorted({e["key"] for e in payload["vecs"]})))


def submit_events(cam_id, events: list, frame_buf: bytes | None = None,
                  scale: float = 1.0, snapshot_overlays: list | None = None) -> bool:
    """Push MAIN-PROCESS-originated events onto the result queue.

    Camera tampering (Kohat POC #4) is detected in `camera_manager`, not in the GPU
    child, because a dead child looks exactly like a blinded camera. Its events still
    have to reach `alert_logs`, and they must go through the SAME writer path as every
    other module so they inherit the false-detection layer, the severity map and the
    snapshot handling rather than re-implementing all three.

    The result queue is created in this process and read by the writer thread in this
    process, so writing to it from here is not a cross-process hop at all - the child
    merely happens to be the usual producer.

    `overlays=None` deliberately: the perception child owns the live overlay set, and a
    tamper event must not blank it."""
    if _result_queue is None or not events:
        return False
    try:
        from backend.core.logging_setup import log_events
        log_events(events, cam_id)
        _result_queue.put_nowait({
            "cam_id": cam_id, "scale": scale, "faces": [], "events": events,
            # overlays=None: the perception child owns the LIVE overlay set and a
            # main-process event must not blank it. But the SAVED PICTURE is a different
            # thing - without this the box marking what was detected was built, attached
            # to the event, and then silently dropped here, so every unattended-object
            # alert arrived as a bare frame with nothing to show what it meant.
            "overlays": None, "snapshot_overlays": snapshot_overlays or [],
            "snapshot": frame_buf, "enroll": [],
        })
        return True
    except Exception:
        # A full queue means the writer is behind. Dropping a tamper event is bad, but
        # blocking the capture loop to deliver it is worse.
        return False


# ── False-detection layer (main process only) ────────────────────────────────────
def fd_judge(event: dict, cam_id, overlays):
    """Run one event past the false-detection filter.

    Lives here rather than in the child because the layer needs alert history and the
    child has no DB access - the architectural rule since day one. Any failure inside
    the layer must never lose an event, so it degrades to ACCEPT."""
    from backend.services.false_detection import FILTER

    try:
        point = _event_point(event, overlays)
        return FILTER.judge(event, cam_id, point=point,
                            has_snapshot=bool(event.get("snapshot")))
    except Exception as e:
        print(f"[FALSE-DETECTION] judge error, event allowed through: {e}", flush=True)
        from backend.services.false_detection import Judgement
        return Judgement()


def _event_point(event: dict, overlays) -> "tuple | None":
    """Normalised (x, y) for the spatial rules, from the overlay belonging to this
    event's track. Returns None when the module cannot supply one - the zone rule then
    records that it could not run rather than guessing."""
    track = (event.get("data") or {}).get("track")
    if track is None or not overlays:
        return None
    for ov in overlays:
        box = ov.get("box")
        if not box or len(box) != 4:
            continue
        if ov.get("track") == track:
            return ((box[0] + box[2]) / 2.0, (box[1] + box[3]) / 2.0)
    return None


def fd_load_config():
    """Read the stored configuration into the filter. Called at start-up and whenever
    the admin panel saves."""
    from sqlalchemy import create_engine, select as sa_select
    from sqlalchemy.orm import sessionmaker
    from backend.core.config import settings as app_settings
    from backend.models.settings import SystemSettings
    from backend.models.camera import CameraDevice
    from backend.services.false_detection import FDConfig, FILTER
    from backend.services.false_detection.logging import LOG as FD_LOG

    stored, overrides = {}, {}
    try:
        engine = create_engine(
            app_settings.DATABASE_URL.replace("+asyncpg", "+psycopg2"),
            pool_pre_ping=True)
        Session = sessionmaker(bind=engine)
        with Session() as db:
            row = db.execute(sa_select(SystemSettings)
                             .where(SystemSettings.id == 1)).scalar_one_or_none()
            if row is not None and getattr(row, "false_detection", None):
                stored = json.loads(row.false_detection)
            for cam in db.execute(sa_select(CameraDevice)).scalars().all():
                fd = (cam.get_modules() and next(
                    (m.get("_fd") for m in cam.get_modules() if m.get("_fd")), None))
                if fd:
                    overrides[cam.id] = fd
    except Exception as e:
        print(f"[FALSE-DETECTION] config load skipped ({e}); using env defaults.",
              flush=True)

    cfg = FDConfig.from_env().merged(stored)
    FILTER.set_config(cfg, overrides)
    FD_LOG.announce(cfg.log_fields())
    if not cfg.fd_enabled:
        print("[FALSE-DETECTION] fd_enabled=0 - layer inert, writer path unchanged",
              flush=True)
    else:
        print(f"[FALSE-DETECTION] active, mode={cfg.fd_mode}, "
              f"{len(overrides)} camera override(s)", flush=True)
    return cfg


# ── Main-process DB writer (no torch/GPU here) ───────────────────────────────────
def _result_writer(result_queue):
    from sqlalchemy import create_engine, select, func
    from sqlalchemy.orm import sessionmaker
    from backend.core.config import settings
    import backend.models.user, backend.models.camera     # noqa: F401
    import backend.models.alert, backend.models.attendance, backend.models.settings  # noqa: F401
    import backend.models.embedding  # noqa: F401
    import backend.models.mess  # noqa: F401
    from backend.models.alert import AlertLog
    from backend.models.attendance import AttendanceRecord
    from backend.models.embedding import FaceEmbedding
    from backend.models.mess import MessSession
    from backend.services import hr_index

    engine = create_engine(settings.DATABASE_URL.replace("+asyncpg", "+psycopg2"), pool_pre_ping=True)
    Session = sessionmaker(bind=engine)
    last_seen: dict[str, datetime] = {}
    name_cache: dict[str, str | None] = {}   # photo_base -> employee name (overlay labels)

    def resolve_uid(photo_base):
        """Remote users.id for an anchor basename, only if HR still knows them."""
        return hr_index.user_id_of(photo_base)

    # Drive the child's INITIAL gallery load. The child has no DB, so the writer is the
    # single source of truth for which photos are real identities: load anchors for
    # EMPLOYEE-REFERENCED photos only (never a stray file in media/employees/), then
    # re-seed every stored auto vector (keyed by the employee's CURRENT anchor basename).
    try:
        photos = hr_index.avatar_rels()
        with Session() as db:
            rows = db.execute(
                select(FaceEmbedding.vec, FaceEmbedding.source, FaceEmbedding.quality,
                       FaceEmbedding.employee_id)
                .where(FaceEmbedding.source.in_(("auto", "manual_extra")))
            ).all()
        seed = []
        for (vec, src, q, uid) in rows:
            info = hr_index.by_user_id(int(uid)) if uid is not None else None
            if info:
                seed.append({"key": f"{info['employee_id']}_avatar.jpg",
                             "vec": bytes(vec), "source": src, "quality": q})
        reload_gallery(photos, seed)
        print(f"[PERCEPTION-WRITER] Loaded {len(photos)} employee anchors + seeded {len(seed)} auto vectors.", flush=True)
    except Exception as e:
        print(f"[PERCEPTION-WRITER] vector-seed skipped: {e}", flush=True)

    # Seed persistent counting totals (#8/#11): HeadCount / TruckCount keep their
    # entry/exit counters in memory, so they reset to 0 on every API restart. Rebuild
    # the running totals from the AlertLog crossing history (one row per crossing) and
    # push them to the child, which applies them when it (re)creates each camera's
    # consumer — so the live totals survive a restart. Truck Count resets daily, so it
    # is seeded from today's rows only.
    try:
        today_start = datetime.now(TZ).replace(hour=0, minute=0, second=0, microsecond=0)
        today_start_utc = today_start.astimezone(timezone.utc).replace(tzinfo=None)
        with Session() as db:
            hc_rows = db.execute(
                select(AlertLog.camera_id, AlertLog.event_type, func.count())
                .where(AlertLog.module == "headcount",
                       AlertLog.event_type.in_(("entry", "exit")))
                .group_by(AlertLog.camera_id, AlertLog.event_type)
            ).all()
            tc_rows = db.execute(
                select(AlertLog.camera_id, AlertLog.event_type, func.count())
                .where(AlertLog.module == "truck_count",
                       AlertLog.event_type.in_(("entry", "exit")),
                       AlertLog.timestamp >= today_start_utc)
                .group_by(AlertLog.camera_id, AlertLog.event_type)
            ).all()
        counts: dict = {}
        for module, rows in (("headcount", hc_rows), ("truck_count", tc_rows)):
            for cam, et, n in rows:
                if cam is None:
                    continue
                d = counts.setdefault(module, {}).setdefault(int(cam), {"in": 0, "out": 0})
                d["in" if et == "entry" else "out"] = int(n)
        if counts:
            # Broadcast, not routed: the payload is keyed by camera and each shard pops
            # only its own entries in run_consumers, so every shard can safely receive
            # the whole map. It also has to be a broadcast - at seed time, on a cold
            # start, no camera has been assigned to a shard yet.
            broadcast({"cmd": "seed_counts", "counts": counts})
            print(f"[PERCEPTION-WRITER] Seeded head-count totals for {len(counts)} camera(s).", flush=True)
    except Exception as e:
        print(f"[PERCEPTION-WRITER] count-seed skipped: {e}", flush=True)

    # Trip-wire totals survive a restart the same way head-count's do: the count lives
    # in memory in the consumer, so without this it silently resets to zero every time
    # the service restarts - and "how many people crossed" is the question this module
    # exists to answer. `alert_logs` already holds one row per crossing, so the total is
    # rebuilt from that rather than stored separately. Unified event store, no new table.
    try:
        with Session() as db:
            tw_rows = db.execute(
                select(AlertLog.camera_id, func.count())
                .where(AlertLog.module == "tripwire",
                       AlertLog.event_type == "crossing")
                .group_by(AlertLog.camera_id)
            ).all()
        tw_counts = {int(cam): int(n) for cam, n in tw_rows if cam is not None}
        if tw_counts:
            broadcast({"cmd": "seed_crossings", "counts": tw_counts})   # see seed_counts
            print(f"[PERCEPTION-WRITER] Seeded trip-wire totals for "
                  f"{len(tw_counts)} camera(s).", flush=True)
    except Exception as e:
        print(f"[PERCEPTION-WRITER] trip-wire seed skipped: {e}", flush=True)

    def persist_enrollments(db, enrolls):
        """Persist auto-enrolled vectors, capping each employee at ENROLL_MAX_AUTO by
        dropping their oldest auto row (the manual anchor is never stored here)."""
        for en in enrolls:
            uid = resolve_uid(en.get("key"))
            if uid is None:
                continue
            db.add(FaceEmbedding(employee_id=uid, vec=en["vec"], source="auto",
                                 quality=en.get("quality")))
            db.flush()
            autos = db.execute(
                select(FaceEmbedding).where(
                    FaceEmbedding.employee_id == uid, FaceEmbedding.source == "auto")
                .order_by(FaceEmbedding.created_at.asc(), FaceEmbedding.id.asc())
            ).scalars().all()
            for extra in autos[:-ENROLL_MAX_AUTO]:
                db.delete(extra)
        db.commit()

    def now_local():
        return datetime.now(TZ)

    def cooldown_ok(key, seconds):
        now = now_local()
        last = last_seen.get(key)
        if last is None or (now - last).total_seconds() >= seconds:
            last_seen[key] = now
            return True
        return False

    def save_snapshot(frame_buf, snapshot_overlays=None):
        if not frame_buf:
            return None
        buf = frame_buf
        if snapshot_overlays:
            try:
                import cv2, numpy as np
                from backend.services.camera_manager import _OVERLAY_BGR
                img = cv2.imdecode(np.frombuffer(buf, np.uint8), cv2.IMREAD_COLOR)
                h2, w2 = img.shape[:2]
                _label_row = 0
                for it in snapshot_overlays:
                    color = _OVERLAY_BGR.get(it.get("color", "accent"), _OVERLAY_BGR["accent"])
                    kind = it.get("kind")
                    if kind == "box":
                        x1, y1, x2, y2 = it["box"]
                        p1 = (int(x1 * w2), int(y1 * h2))
                        p2 = (int(x2 * w2), int(y2 * h2))
                        cv2.rectangle(img, p1, p2, color, 2)
                        if it.get("label"):
                            ty = p1[1] - 8 if p1[1] - 8 > 12 else p2[1] + 18
                            cv2.putText(img, it["label"], (p1[0], ty), cv2.FONT_HERSHEY_SIMPLEX, 0.55, color, 2)
                    elif kind == "label" and it.get("text"):
                        # Stacked, for the same reason as the live overlay: several
                        # modules on one camera would otherwise overprint each other.
                        cv2.putText(img, it["text"], (12, 28 + 26 * _label_row),
                                    cv2.FONT_HERSHEY_SIMPLEX, 0.7, color, 2)
                        _label_row += 1
                _, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 80])
                buf = buf.tobytes()
            except Exception as e:
                print(f"[SNAPSHOT] draw error: {e}", flush=True)
        name = f"a_{uuid.uuid4().hex[:8]}.jpg"
        rel = os.path.join("snapshots", name)
        path = os.path.join(settings.MEDIA_ROOT, "snapshots", name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as f:
            f.write(buf)
        return rel

    def write_attendance(db, face, cam_id, get_buf):
        photo_base = face.get("photo_base")
        sim = face.get("sim")
        role = face.get("role") or "both"
        if not photo_base:
            if face.get("ambiguous"):
                return   # matched someone but not clearly separated from a look-alike —
                         # defer rather than log a wrong ID or a false "unknown"
            if face.get("weak"):
                return   # #14: face too small / low detector score — unreliable, don't
                         # spam an "Unknown" alert off a distant blob or a bad frame
            if cooldown_ok(f"unknown_{cam_id}", UNKNOWN_COOLDOWN):
                db.add(AlertLog(module="attendance", camera_id=cam_id, event_type="unknown",
                                unknown_person=True, violation_type="Unknown Face",
                                distance_score=sim, snapshot=save_snapshot(get_buf())))
                db.commit()
            return
        uid = hr_index.user_id_of(photo_base)
        if uid is None:
            return
        info = hr_index.info_of(photo_base)
        emp_name = (info or {}).get("name") or uid
        if (info or {}).get("status") == "inactive":
            # inactive employees don't log attendance (check-in or check-out)
            print(f"[ATTENDANCE] Skipped inactive employee: {emp_name}", flush=True)
            return
        if not cooldown_ok(f"emp_{uid}_{cam_id}", EMP_COOLDOWN):
            return

        # Validate camera_id exists in camera_devices
        from backend.models.camera import CameraDevice
        cam = db.execute(select(CameraDevice).where(CameraDevice.id == cam_id)).scalar_one_or_none()
        if cam is None:
            print(f"[ATTENDANCE] Invalid camera_id {cam_id} - not found in camera_devices", flush=True)
            return

        today, now_t = now_local().date(), now_local().time()
        att = db.execute(select(AttendanceRecord).where(
            AttendanceRecord.employee_id == uid, AttendanceRecord.date == today)).scalar_one_or_none()
        punch_payload = {"user_id": uid, "punched_at": f"{today} {now_t}", "camera_id": cam_id, "source": "ai"}
        api_ok = False
        body = {}
        try:
            resp = requests.post(
                f"{HR_PAYROLL_BASE_URL}/api/attendance/punch",
                headers={"X-Attendance-Key": ATTENDANCE_KEY, "Content-Type": "application/json"},
                json=punch_payload,
                timeout=10,
            )
            print(f"[ATTENDANCE] API response ({emp_name}): {resp.status_code} {resp.text}", flush=True)
            if resp.status_code == 200:
                body = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
                status = body.get("status")
                api_ok = status in ("applied", "held", "duplicate")
                print(f"[ATTENDANCE] HR punch status={status!r}, body={body}", flush=True)
            elif 500 <= resp.status_code < 600:
                # HTTP 5xx - will be retried on next detection (cooldown still applies)
                print(f"[ATTENDANCE] HTTP {resp.status_code} error - will retry on next detection", flush=True)
                return
        except Exception as e:
            print(f"[ATTENDANCE] API error ({emp_name}): {e}", flush=True)
        if not api_ok:
            # `ignored`/`rejected` are final HR decisions and must not create a
            # local record that falsely suggests the punch exists in HR Payroll.
            status_msg = body.get("status") or "network_error"
            print(f"[ATTENDANCE] Skipped ({emp_name}): API status={status_msg}", flush=True)
            return

        # HR Payroll accepts the punch first; mirror the accepted result locally
        # because the Attendance view reads attendance_records.
        if role in ("in", "both") and att is None:
            db.add(AttendanceRecord(
                employee_id=uid,
                date=today,
                clock_in=now_t,
                status="present",
                is_absent=False,
            ))
            db.commit()
        elif role in ("out", "both") and att is not None and att.clock_in and (
                datetime.combine(today, now_t) - datetime.combine(today, att.clock_in)
        ).total_seconds() >= MIN_CHECKOUT_GAP:
            att.clock_out = now_t
            db.commit()

        if att is None and role in ("in", "both"):
            db.add(AlertLog(module="attendance", camera_id=cam_id, event_type="checkin",
                            employee_id=uid, violation_type="Attendance Check-In",
                            distance_score=sim, snapshot=save_snapshot(get_buf())))
            db.commit()
            print(f"[ATTENDANCE] Checked-in: {emp_name}", flush=True)
        elif att is not None and role in ("out", "both") and att.clock_in and (
                datetime.combine(today, now_t) - datetime.combine(today, att.clock_in)).total_seconds() >= MIN_CHECKOUT_GAP:
            db.add(AlertLog(module="attendance", camera_id=cam_id, event_type="checkout",
                            employee_id=uid, violation_type="Attendance Check-Out",
                            distance_score=sim, snapshot=save_snapshot(get_buf())))
            db.commit()
            print(f"[ATTENDANCE] Last seen: {emp_name}", flush=True)

    def write_mess(db, face, cam_id, mess_on, get_buf):
        """Mess (canteen) billing hook: when the `mess` module is active on this camera
        and a recognized employee arrives, open an `open` mess bill for them (one at a
        time) and log the arrival. Items are added/closed from the Mess UI."""
        if not mess_on:
            return
        photo_base = face.get("photo_base")
        if not photo_base:
            return
        uid = hr_index.user_id_of(photo_base)
        if uid is None:
            return
        info = hr_index.info_of(photo_base)
        emp_name = (info or {}).get("name") or uid
        if not cooldown_ok(f"mess_{uid}_{cam_id}", MESS_OPEN_COOLDOWN):
            return
        open_bill = db.execute(select(MessSession).where(
            MessSession.employee_id == uid, MessSession.status == "open")).scalar_one_or_none()
        if open_bill is None:
            db.add(MessSession(employee_id=uid, camera_id=cam_id, status="open", total=0.0))
        db.add(AlertLog(module="mess", camera_id=cam_id, event_type="person_in",
                        employee_id=uid, violation_type="Mess Entry",
                        distance_score=face.get("sim"), snapshot=save_snapshot(get_buf())))
        db.commit()
        print(f"[MESS] Opened bill for: {emp_name}", flush=True)

    print("[INIT] Perception result-writer ready.", flush=True)
    while True:
        try:
            res = result_queue.get()
            if res is None:
                continue
            cam_id = res.get("cam_id")
            scale = res.get("scale", 1.0)
            frame_buf = res.get("snapshot")
            overlays = res.get("overlays")

            # Fast-lane (tracking-cadence) results carry overlays=None: the slow lane
            # owns the live overlay set, so leave it alone rather than replacing it with
            # a head-count-only set and making the live view flicker. Results with
            # nothing to persist skip the DB session entirely — at 10 fps per camera the
            # fast lane would otherwise open a session per frame for no reason.
            keep_overlays = overlays is not None
            if overlays is None:
                overlays = []
            if not keep_overlays and not (res.get("faces") or res.get("events")
                                          or res.get("enroll")):
                continue

            # Lazily annotate the snapshot with detection boxes — computed once, only
            # if some row in this result actually needs a snapshot saved.
            frame_seq = res.get("frame_seq")
            _snap_cache: dict = {}
            def get_buf():
                if "buf" not in _snap_cache:
                    raw = frame_buf
                    if not raw and frame_seq is not None:
                        # Preferred: the EXACT frame this decision was made on, encoded
                        # now rather than speculatively for every frame.
                        try:
                            from backend.services.camera_manager import jpeg_for_seq
                            raw = jpeg_for_seq(cam_id, frame_seq)
                        except Exception as e:
                            print(f"[PERCEPTION-WRITER] exact-frame snapshot failed: {e}",
                                  flush=True)
                    if not raw:
                        # Fast-lane results carry no JPEG (see camera_manager). Ask the
                        # capture thread for the current frame instead of dropping the
                        # picture - an alert with no evidence is a poor alert.
                        try:
                            from backend.services.camera_manager import latest_jpeg
                            raw = latest_jpeg(cam_id)
                        except Exception as e:
                            print(f"[PERCEPTION-WRITER] snapshot fallback failed: {e}",
                                  flush=True)
                            raw = None
                    # Sub-slow lanes send overlays=None (they must not touch the live
                    # view) but still carry what they detected in `snapshot_overlays`,
                    # so the saved picture shows the phone/box the alert is about.
                    snap_ovs = res.get("snapshot_overlays") or overlays
                    _snap_cache["buf"] = annotate_snapshot(raw, snap_ovs) if raw else None
                return _snap_cache["buf"]

            with Session() as db:
                # Replace identity tags (photo_base) with the employee's NAME so the live
                # overlay + saved snapshot show who was recognized, not a generic "ID".
                for ov in overlays:
                    pb = ov.get("ident")
                    if not pb:
                        continue
                    if pb not in name_cache:
                        name_cache[pb] = hr_index.name_of(pb)
                    if name_cache[pb]:
                        suffix = " - PHONE" if ov.get("phone") else ""
                        ov["label"] = f"{name_cache[pb]}{suffix}"
                if keep_overlays:
                    set_overlay(cam_id, scale, overlays)

                for face in res.get("faces", []):
                    if face.get("inferred"):
                        continue  # identity carried from the track — shown via overlay; not a
                                  # fresh face match, so no unknown alert and no new attendance row
                    if not face.get("crossed_line", True):
                        continue  # ROI line active and this face hasn't crossed it yet
                    write_attendance(db, face, cam_id, get_buf)
                    write_mess(db, face, cam_id, res.get("mess_on"), get_buf)
                for ev in res.get("events", []):
                    module = ev.get("module")

                    # ── False-detection layer ────────────────────────────────
                    # The single stage every module's events pass through. With
                    # fd_enabled=false it returns ACCEPT without running a rule or
                    # touching any state, so this path is what it was before.
                    verdict = fd_judge(ev, cam_id, overlays)
                    if not verdict.write:
                        continue

                    want_snap = ev.get("snapshot") and verdict.snapshot
                    # A failed snapshot must cost the PICTURE, not the ALERT. Unprotected,
                    # a write error here (full disk, unwritable MEDIA_ROOT, a bad path
                    # from an unedited .env) propagates to the loop's blanket handler and
                    # discards the whole result batch - every alert in it, silently, under
                    # a generic error line. The row matters more than the thumbnail.
                    snap = None
                    if want_snap:
                        try:
                            snap = save_snapshot(get_buf())
                            if snap is None:
                                print(f"[PERCEPTION-WRITER] no snapshot for "
                                      f"{module}:{ev.get('event_type')} on camera "
                                      f"{cam_id} - no frame was available", flush=True)
                        except Exception as se:
                            print(f"[PERCEPTION-WRITER] snapshot FAILED for "
                                  f"{module}:{ev.get('event_type')} on camera {cam_id}: "
                                  f"{se} - the alert is still being written", flush=True)
                            snap = None
                    # attribute track-based events to an employee when fusion identified them
                    emp_uid = None
                    pb = ev.get("photo_base")
                    if pb:
                        emp_uid = hr_index.user_id_of(pb)
                    db.add(AlertLog(
                        module=module, camera_id=cam_id, event_type=ev.get("event_type"),
                        violation_type=ev.get("label") or f"{module}:{ev.get('event_type')}",
                        value=ev.get("value"), employee_id=emp_uid,
                        data=json.dumps(ev.get("data")) if ev.get("data") is not None else None,
                        snapshot=snap,
                        severity=verdict.severity if verdict.severity_recorded else None,
                    ))
                db.commit()

                enrolls = res.get("enroll")
                if enrolls:
                    try:
                        persist_enrollments(db, enrolls)
                    except Exception as e:
                        db.rollback()
                        print(f"[PERCEPTION-WRITER] enroll persist error: {e}", flush=True)
                    # Share the new vector with the other shards. Deliberately OUTSIDE
                    # the try above: the in-memory fan-out is what keeps recognition
                    # consistent across shards, and it should still happen even if the
                    # database write failed. No-op when unsharded.
                    try:
                        fanout_enrollments(res.get("cam_id"), enrolls)
                    except Exception as e:
                        print(f"[PERCEPTION-WRITER] enroll fan-out error: {e}", flush=True)
        except Exception as e:
            print(f"[PERCEPTION-WRITER] error: {e}", flush=True)


# ── Child process: GPU detectors + rules (no DB) ─────────────────────────────────
# Rate-limited failure reporting for the child. A fault in the hot loop repeats at
# frame rate, so an unthrottled print is not a diagnostic - it is a denial of service on
# your own log.
_ERR_SEEN: dict = {}
ERR_REPEAT_SECONDS = float(os.environ.get("PERCEPTION_ERR_REPEAT", "60"))


def _report_once(key: str, message: str, exc: Exception | None = None) -> None:
    now = time.time()
    last = _ERR_SEEN.get(key, 0.0)
    if now - last < ERR_REPEAT_SECONDS:
        return
    first = key not in _ERR_SEEN
    _ERR_SEEN[key] = now
    print(message, flush=True)
    if first and exc is not None:
        import traceback
        traceback.print_exception(type(exc), exc, exc.__traceback__)


def _detector_failed(name: str, cam_id, exc: Exception) -> None:
    _report_once(f"det:{name}:{type(exc).__name__}",
                 f"[PERCEPTION] {name} detector failed on camera {cam_id}: {exc} "
                 f"- that detector is skipped for this frame, everything else on the "
                 f"frame still runs.", exc)


def _loop_error(cam_id, exc: Exception) -> None:
    _report_once(f"loop:{type(exc).__name__}:{exc}",
                 f"[PERCEPTION] loop error on camera {cam_id}: {exc} "
                 f"- THIS FRAME WAS DISCARDED ENTIRELY (all modules on it). "
                 f"Repeats are suppressed for {ERR_REPEAT_SECONDS:.0f}s.", exc)


def _child_main(task_queue, result_queue):
    os.environ.setdefault("OMP_NUM_THREADS", "1")
    # Its OWN file, not the backend's: this is a separate spawned process, and two
    # RotatingFileHandlers rotating one path interleave and truncate each other. This
    # is also the process whose failures are hardest to see - its traceback goes to a
    # stdout the parent does not capture - so it is the one that most needs a file.
    from backend.core.logging_setup import (init_run_log, log_parameters,
                                             set_process_tag, log_module,
                                             log_module_config, log_events)
    set_process_tag("perception")
    init_run_log("perception")
    log_parameters("perception")
    import numpy as np
    from backend.services import embedding_cache
    from backend.services.detectors import person as person_det, face as face_det, fire_smoke as fire_smoke_det
    from backend.services.detectors import smoking as smoking_det
    from backend.services.detectors import phone as phone_det
    from backend.services.detectors import vehicle as vehicle_det
    from backend.services.analytics import make_consumer, is_person_analytic
    from backend.services.analytics.base import side_of_line
    from backend.services.modules.registry import MODULES, backbones_for

    print("[PERCEPTION] Starting — loading detectors…", flush=True)
    # NOTE: anchors are NOT dir-scanned here. The writer (which has DB access) drives the
    # gallery load via a `reload_gallery` command carrying ONLY employee-referenced photos,
    # so a stray/orphan file in media/employees/ can never become a phantom identity.
    from backend.services.analytics import headcount_debug as hcdbg_child
    from backend.services.analytics import phone_debug as pdbg_child
    hcdbg_child.announce("perception-child")
    pdbg_child.announce("perception-child")
    # Kohat POC modules. Each announces so its file exists from start-up: silence in a
    # log you were told to capture is ambiguous between "switched off" and "nothing to
    # say yet", and that ambiguity has cost real debugging time on this project.
    for _mod in ("tripwire_debug", "crowd_debug", "vehicle_debug",
                 "intrusion_debug"):
        try:
            _m = __import__("backend.services.analytics." + _mod,
                            fromlist=["announce"])
            _m.announce("perception-child")
        except Exception as _e:
            print(f"[INIT] {_mod} announce skipped: {_e}", flush=True)
    print("[PERCEPTION] Ready. Awaiting gallery load from writer.", flush=True)

    consumers: dict[tuple, object] = {}   # (cam_id, module) -> consumer
    track_identity: dict = {}             # cam_id -> {track_id: (photo_base, ts)} (face↔track fusion)
    track_votes: dict = {}                # cam_id -> {track_id: {photo_base: score}} (temporal voting)
    face_confirmed: dict = {}             # cam_id -> set(track_id) identified via a FACE (gallery-safe)
    smoke_last: dict = {}                 # cam_id -> last smoking-event ts (per-cam cooldown)
    count_seed: dict = {}                # module -> {cam_id: {"in": n, "out": n}} restored totals (applied once)
    tripwire_seed: dict = {}             # cam_id -> restored crossing total (applied once)
    fire_frame: dict = {}              # cam_id -> processed-frame counter (fire/smoke throttle)
    vehicle_frame: dict = {}           # cam_id -> processed-frame counter (vehicle throttle)
    att_crossed: dict = {}             # cam_id -> set(track_id) that crossed the attendance ROI line
    att_prev_side: dict = {}           # cam_id -> {track_id: side} previous frame side of line

    seen_modules: dict = {}          # cam_id -> last module set reported

    def report_modules(cam_id_, mods_):
        """Print each camera's module list the first time it is seen, and whenever it
        changes.

        This exists because of a real diagnosis that took far too long: a trip wire was
        assigned to two cameras and only one produced alerts, and NOTHING in any log said
        which modules the second camera had actually been given. The per-module logs only
        speak once their consumer runs, so a module that never arrives is invisible -
        indistinguishable from one that arrives and fails.

        One line per camera per change is cheap and answers the question immediately."""
        ids = tuple(sorted(m.get("module") for m in (mods_ or []) if m.get("module")))
        if seen_modules.get(cam_id_) == ids:
            return
        seen_modules[cam_id_] = ids
        print(f"[PERCEPTION] camera {cam_id_} modules: "
              f"{', '.join(ids) if ids else '(none)'}", flush=True)

    def person_knobs(mods_):
        """Per-camera person-detector recall settings, or (None, None) for the defaults.

        CAMERA-WIDE by nature: there is one person pass per frame, shared by every
        person-based module on that camera. So the value is read from whichever module
        carries it rather than being scoped to one - the UI puts it on the modules that
        actually care about recall (crowd, head-count).

        Bad values fall back to the default rather than raising: this runs in the frame
        loop, and a typo in a camera's config must not blind the whole pipeline."""
        conf = iou = None
        for m_ in (mods_ or []):
            for key, cur in (("person_conf", conf), ("person_iou", iou)):
                if cur is not None:
                    continue
                raw = m_.get(key)
                if raw in (None, ""):
                    continue
                try:
                    v = float(raw)
                except (TypeError, ValueError):
                    continue
                if not 0.0 < v <= 1.0:
                    continue
                if key == "person_conf":
                    conf = v
                else:
                    iou = v
        return conf, iou


    def run_vehicle_count(mods_, frame_, shape_, cam_id_, cam_key_):
        """Vehicle counting, on whichever lane is running.

        It needs its OWN tracked detection pass — the no-stopping path deliberately
        returns untracked boxes, and a crossing cannot be decided without identity. And
        it has to run on the fast lane like every other crossing rule: a vehicle moves
        faster than a person, so if ByteTrack association needs 10 fps for a walker it
        certainly needs it here."""
        cfg = next((m for m in (mods_ or []) if m.get("module") == "vehicle_count"), None)
        if cfg is None:
            return [], []
        try:
            vt = vehicle_det.detect_and_track_vehicles(frame_, cam_key_)
        except Exception as e:
            _detector_failed("vehicle_count", cam_id_, e)
            return [], []
        key = (cam_id_, "vehicle_count")
        c = consumers.get(key)
        if c is None:
            c = make_consumer("vehicle_count", cam_id_, cfg)
            consumers[key] = c
            log_module_config("vehicle_count", cam_id_, cfg)
        else:
            c.config = cfg
        try:
            evs, ovs = c.process(vt, [], shape_)
        except Exception as e:
            print(f"[PERCEPTION] vehicle_count error: {e}", flush=True)
            log_module("vehicle_count", f"ERROR {e}", cam_id_)
            return [], []
        for e in evs:
            e["module"] = "vehicle_count"
        return evs, ovs or []


    def run_consumers(mods_, tracks_, faces_, shape_, cam_id_, truck_tracks_=None):
        """Instantiate/refresh the person-track rule consumers for a camera and feed
        them one frame. Shared by the slow lane and the fast (tracking-cadence) lane so
        both paths create consumers, restore seeded totals and attribute events to a
        recognised employee in exactly the same way."""
        evs_all, ovs_all = [], []
        for m_ in mods_:
            mid = m_["module"]
            if not is_person_analytic(mid):
                continue
            # ...and it must actually WANT person tracks. `_CONSUMERS` membership only
            # says "this module is a track-fed rule", not "these are the right tracks".
            # vehicle_count is such a rule, and feeding it people would have it counting
            # pedestrians as cars - silently, because the shapes are identical.
            # truck_count is the one vehicle-backbone rule that IS a track-fed
            # line-crossing rule (headcount's logic, truck tracks). It is fed
            # truck_tracks_ below. vehicle_count stays out - it has its own lane.
            if ((MODULES.get(mid) or {}).get("backbone") not in (None, "person")
                    and mid != "truck_count"):
                continue
            key = (cam_id_, mid)
            c = consumers.get(key)
            if c is None:
                c = make_consumer(mid, cam_id_, m_)
                consumers[key] = c
                log_module_config(mid, cam_id_, m_)
                if mid in ("headcount", "truck_count"):  # restore persisted IN/OUT totals (#8/#11)
                    sd = (count_seed.get(mid) or {}).pop(cam_id_, None)
                    if sd and hasattr(c, "entries"):
                        c.entries = int(sd.get("in", 0))
                        c.exits = int(sd.get("out", 0))
                elif mid == "tripwire":           # restore the persisted crossing total
                    n = tripwire_seed.pop(cam_id_, None)
                    if n is not None and hasattr(c, "crossings"):
                        c.crossings = int(n)
            else:
                c.config = m_  # pick up edited config
            try:
                feed = (truck_tracks_ or []) if mid == "truck_count" else tracks_
                evs, ovs = c.process(feed, faces_, shape_)
            except Exception as e:
                print(f"[PERCEPTION] {mid} error: {e}", flush=True)
                log_module(mid, f"ERROR {e}", cam_id_)
                evs, ovs = [], []
            for e in evs:
                e["module"] = mid
                tid = (e.get("data") or {}).get("track")
                if tid is not None:
                    ent = track_identity.get(cam_id_, {}).get(tid)
                    if ent:
                        e["photo_base"] = ent[0]
            evs_all += evs
            ovs_all += ovs
        return evs_all, ovs_all

    def run_phone(mods_, tracks_, frame_, shape_, cam_id_):
        """Run phone perception for one evaluation and feed the mobile consumer.

        Detection happens here (GPU); association and the temporal state machine live in
        the consumer, where they are pure geometry and can be tested without a model."""
        m_ = next((m for m in mods_ if m.get("module") == "mobile"), None)
        if m_ is None:
            return [], []
        key = (cam_id_, "mobile")
        c = consumers.get(key)
        if c is None:
            c = make_consumer("mobile", cam_id_, m_)
            consumers[key] = c
        else:
            c.config = m_
        if c is None:
            return [], []
        try:
            dets = phone_det.detect_phones(
                frame_, tracks_, shape_,
                conf=(float(m_.get("phone_conf")) if m_.get("phone_conf") else None),
                mode=m_.get("phone_mode"),
                crop_size=m_.get("phone_crop_size"))
            evs, ovs = c.process_phones(tracks_, dets, shape_)
        except Exception as e:
            print(f"[PERCEPTION] mobile error: {e}", flush=True)
            log_module("mobile", f"ERROR {e}", cam_id_)
            return [], []
        for e in evs:
            e["module"] = "mobile"
            tid = (e.get("data") or {}).get("track")
            if tid is not None:
                ent = track_identity.get(cam_id_, {}).get(tid)
                if ent:
                    # Identity is OPTIONAL: it enriches the event when face recognition
                    # happens to have bound one, and its absence never blocks detection.
                    e["photo_base"] = ent[0]
        return evs, ovs

    while True:
        try:
            task = task_queue.get()
            if task is None:
                continue
            cmd = task.get("cmd")
            if cmd == "add":
                embedding_cache.add_photo(task["path"], task["rel"]); continue
            if cmd == "add_extra":
                embedding_cache.add_manual_photos(task["rel"], [task["path"]]); continue
            if cmd == "remove":
                embedding_cache.remove_photo(task["rel"]); continue
            if cmd == "seed_vecs":
                embedding_cache.seed_vectors(task.get("vecs") or []); continue
            if cmd == "add_vecs":
                # A vector another shard auto-enrolled. Goes through add_runtime_vector,
                # not seed_vectors, so the ENROLL_MAX_AUTO cap is applied here exactly as
                # it was on the shard that learned it — otherwise galleries drift apart
                # in size as well as content. See fanout_enrollments().
                for _v in (task.get("vecs") or []):
                    try:
                        _k, _b = _v.get("key"), _v.get("vec")
                        if not _k or not _b:
                            continue
                        _arr = np.frombuffer(_b, dtype=np.float32).copy()
                        if _arr.size != 512:
                            continue
                        embedding_cache.add_runtime_vector(_k, _arr, "auto", _v.get("quality"))
                    except Exception:
                        pass          # a bad vector must never stop the frame loop
                continue
            if cmd == "reset_autos":
                embedding_cache.reset_auto(task["key"]); continue
            if cmd == "reload_gallery":
                embedding_cache.load_photos(task.get("photos") or []); continue
            if cmd == "seed_counts":
                count_seed.update(task.get("counts") or {}); continue
            if cmd == "seed_crossings":
                tripwire_seed.update(task.get("counts") or {}); continue

            frame = task.get("frame")
            frame_buf = task.get("frame_buf")
            # Ring sequence of the frame this task was built from. The main process
            # encodes that exact frame if - and only if - an event needs a snapshot.
            frame_seq = task.get("frame_seq")
            cam_id = task.get("cam_id")
            scale = task.get("scale", 1.0)
            mods = [m for m in (task.get("modules") or []) if m.get("enabled", True)]
            lane = task.get("lane") or ("fast" if task.get("fast") else "slow")
            if lane != "slow":
                # By EFFECTIVE lane, so a per-camera override is honoured here too. If
                # this filter used the global sets while camera_manager scheduled by the
                # per-camera value, a lane would fire and its modules would be filtered
                # straight back out - no error, just a module that silently never runs.
                mods = [m for m in mods if _carried_by_lane(m, lane)]
            if frame is None or getattr(frame, "size", 0) == 0 or not mods:
                continue
            if lane == "slow":
                # Only the slow lane carries the FULL module list; the faster lanes are
                # filtered subsets and would report a misleading picture.
                report_modules(cam_id, mods)

            shape = frame.shape[:2]

            if lane != "slow":
                # Sub-slow cadence frame: person backbone plus this lane's consumers.
                # It deliberately touches none of the identity / re-id / fire state, so
                # those stay driven purely by the slow lane and keep their tuning.
                # overlays=None tells the writer to leave the current overlay set alone
                # (the slow lane owns the live view).
                cam_key = str(cam_id)
                _pc, _pi = person_knobs(mods)
                tracks = person_det.detect_and_track(frame, cam_key, conf=_pc, iou=_pi)
                evs, snap_ovs = [], []
                if lane == "medium":
                    # Keep the phone overlays. `overlays` below stays None on purpose —
                    # the slow lane owns the LIVE view and must not be clobbered at this
                    # cadence — but that is a different thing from the SAVED PICTURE.
                    # Discarding these was why a phone_use alert's screenshot showed the
                    # person with no box on the phone: the only overlay that marks what
                    # was detected was thrown away one line after it was produced.
                    ph_evs, ph_ovs = run_phone(mods, tracks, frame, shape, cam_id)
                    evs += ph_evs
                    snap_ovs += ph_ovs or []
                rest = [m for m in mods if m.get("module") != "mobile"]
                c_evs, c_ovs = run_consumers(rest, tracks, [], shape, cam_id)
                evs += c_evs
                snap_ovs += c_ovs or []
                v_evs, v_ovs = run_vehicle_count(mods, frame, shape, cam_id, cam_key)
                evs += v_evs
                snap_ovs += v_ovs
                if evs:
                    log_events(evs, cam_id)
                    result_queue.put({"cam_id": cam_id, "scale": scale, "faces": [],
                                      "events": evs, "overlays": None,
                                      "snapshot_overlays": snap_ovs,
                                      "snapshot": frame_buf, "frame_seq": frame_seq,
                                      "enroll": []})
                continue

            ids = [m["module"] for m in mods]
            backbones = backbones_for(ids)
            # Person tracking runs whenever face recognition is active, so a recognized
            # identity can bind to the body track and PERSIST after the face leaves view
            # (turns around / occlusion) — even if no person-analytics module is enabled.
            if "face" in backbones:
                backbones = backbones | {"person"}
            cam_key = str(cam_id)

            _pc, _pi = person_knobs(mods)
            tracks = (person_det.detect_and_track(frame, cam_key, conf=_pc, iou=_pi)
                      if ("person" in backbones or "phone" in backbones) else [])

            # Truck tracking for the Truck Count module (own backbone/instance).
            # NOT `vehicle_tracks`: the vehicle_count lane below binds that name to CAR
            # tracks in this same scope, and used to overwrite the trucks before
            # run_consumers ever saw them.
            truck_tracks = []
            if "vehicle" in backbones:
                tc_cfg = next((m for m in mods if m["module"] == "truck_count"), {})
                tc_conf = float(tc_cfg.get("confidence_threshold") or 0) or 0.8
                truck_tracks = vehicle_det.detect_and_track_trucks(frame, cam_key, conf=tc_conf)

            faces_out = []
            enroll_out = []
            if "face" in backbones:
                att_cfg = next((m for m in mods if m["module"] == "attendance"), {})
                role = att_cfg.get("role", "both")
                # #14: per-camera min face score for Unknown alerts (UI override), else env floor
                unknown_min_score = float(att_cfg.get("confidence_threshold") or 0) or UNKNOWN_MIN_SCORE
                for f in face_det.detect_faces(frame):
                    key, sim, margin = embedding_cache.find_match(f["embedding"])
                    # Ambiguity guard: a match too close to the 2nd-best employee is NOT a
                    # confident identity — demote it (photo_base=None) so it can't check in
                    # the wrong person, raise a wrong alert, or vote as them. Voting and the
                    # unknown-suppression re-attribution below already treat None faces right
                    # (defer to the track's committed identity when there is one). `key` is
                    # left untouched for the STRICT auto-enroll gate (larger margin anyway).
                    ambiguous = bool(key) and margin < LABEL_MARGIN
                    x1, y1, x2, y2 = f["box"]
                    facepx = min(x2 - x1, y2 - y1)
                    # #14: an unmatched face too small or too low-score is an unreliable
                    # detection — flag it so the writer won't raise an "Unknown" alert.
                    weak = f.get("score", 0.0) < unknown_min_score or facepx < UNKNOWN_MIN_PX
                    if FACE_DEBUG:
                        print(f"[FACE] best={key or '—'} sim={sim:.3f} margin={margin:.3f} "
                              f"score={f.get('score', 0.0):.2f} px={facepx:.0f} "
                              f"-> {'AMBIG(demoted)' if ambiguous else ('MATCH' if key else ('weak-unknown' if weak else 'unknown'))}", flush=True)
                    faces_out.append({
                        "photo_base": None if ambiguous else key, "ambiguous": ambiguous,
                        "weak": weak, "sim": sim, "margin": margin,
                        "score": f.get("score", 0.0), "role": role,
                        "box": [x1 / shape[1], y1 / shape[0], x2 / shape[1], y2 / shape[0]],
                    })
                    # Auto-enroll (STRICT gate, independent of the lenient runtime
                    # threshold): a very confident, well-separated, sharp, sizeable,
                    # non-duplicate live face grows this employee's gallery with a fresh
                    # angle. Added to the in-memory gallery now (instant use) and emitted
                    # for the writer to persist (capped at ENROLL_MAX_AUTO).
                    if (ENROLL_ENABLE and key and sim >= ENROLL_SIM and margin >= ENROLL_MARGIN
                            and f.get("score", 0.0) >= ENROLL_SCORE
                            and min(x2 - x1, y2 - y1) >= ENROLL_MIN_PX
                            and embedding_cache.auto_count(key) < ENROLL_MAX_AUTO
                            and embedding_cache.is_diverse(key, f["embedding"], ENROLL_DUP_SIM)):
                        emb = np.asarray(f["embedding"], dtype=np.float32)
                        embedding_cache.add_runtime_vector(key, emb, "auto", round(float(sim), 4))
                        enroll_out.append({"key": key, "vec": emb.tobytes(), "quality": round(float(sim), 4)})
                        print(f"[ENROLL] +auto {key} sim={sim:.3f} margin={margin:.3f} "
                              f"autos={embedding_cache.auto_count(key)}", flush=True)

            # Fuse recognized faces with overlapping person tracks AND keep that identity
            # bound to the ByteTrack track id for the track's lifetime — so a recognized
            # person stays identified after their face leaves view (turns around / brief
            # occlusion). Identity is committed by TEMPORAL VOTING: per-frame face matches
            # accumulate similarity-weighted votes per track and the leader binds, which
            # keeps recall high on bad streams (one solid frame already commits) while
            # damping flicker between look-alikes. Identity drops only once the track has
            # been absent IDENTITY_GRACE seconds. Cross-track re-binding (new ByteTrack id
            # / re-entry) is handled by the body Re-ID pass below.
            if tracks or track_identity.get(cam_id):
                tmap = track_identity.setdefault(cam_id, {})
                votes = track_votes.setdefault(cam_id, {})
                fconf = face_confirmed.setdefault(cam_id, set())
                nowf = time.time()
                track_ids = {t["id"] for t in tracks}
                # fade stale votes for tracks present this frame
                for tid in track_ids:
                    tv = votes.get(tid)
                    if tv:
                        for pb in list(tv):
                            tv[pb] *= VOTE_DECAY
                            if tv[pb] < 0.05:
                                del tv[pb]
                # (a) accumulate votes from faces recognized this frame (face inside track box)
                for fo in faces_out:
                    if not fo["photo_base"]:
                        continue
                    fx = (fo["box"][0] + fo["box"][2]) / 2 * shape[1]
                    fy = (fo["box"][1] + fo["box"][3]) / 2 * shape[0]
                    for t in tracks:
                        x1, y1, x2, y2 = t["box"]
                        if x1 <= fx <= x2 and y1 <= fy <= y2:
                            tv = votes.setdefault(t["id"], {})
                            tv[fo["photo_base"]] = tv.get(fo["photo_base"], 0.0) + max(fo["sim"], 0.0)
                            break
                # (b) commit the vote leader (must clearly beat runner-up) / refresh present tracks
                for tid in track_ids:
                    tv = votes.get(tid)
                    if tv:
                        leader = max(tv, key=tv.get)
                        lead = tv[leader]
                        runner = max((s for pb, s in tv.items() if pb != leader), default=0.0)
                        if lead >= face_det.MATCH_SIM and lead >= runner * VOTE_MARGIN:
                            tmap[tid] = (leader, nowf)
                            fconf.add(tid)            # this binding is FACE-confirmed (gallery-safe)
                            continue
                    if tid in tmap:                   # present but no fresh decision — keep alive
                        tmap[tid] = (tmap[tid][0], nowf)
                # (c) expire identities + votes for tracks gone past the grace window
                for tid in list(tmap):
                    if nowf - tmap[tid][1] > IDENTITY_GRACE:
                        tmap.pop(tid, None)
                        votes.pop(tid, None)
                        fconf.discard(tid)
                for tid in list(votes):
                    if tid not in track_ids and tid not in tmap:
                        votes.pop(tid, None)

            # ── Identity uniqueness (#16): at most ONE track carries an identity at a time ──
            # Root cause of "two/three people shown with the same name": temporal voting or
            # body Re-ID bound the same employee to multiple tracks at once. Keep only the
            # strongest claimant per identity (face-confirmed > highest live vote > most-recent
            # binding) and release the rest, so a second/nearby person can't share the name.
            tmap = track_identity.get(cam_id)
            if tmap and len(tmap) > 1:
                votes = track_votes.get(cam_id, {})
                fconf = face_confirmed.get(cam_id, set())
                by_ident: dict = {}
                for tid, (pb, ts) in tmap.items():
                    by_ident.setdefault(pb, []).append(tid)
                for pb, tids in by_ident.items():
                    if len(tids) < 2:
                        continue
                    keep = max(tids, key=lambda t: (t in fconf, votes.get(t, {}).get(pb, 0.0), tmap[t][1]))
                    for tid in tids:
                        if tid != keep:
                            tmap.pop(tid, None)
                            votes.pop(tid, None)
                            fconf.discard(tid)

            # Suppress false "Unknown" for an already-recognized person: an unmatched face
            # inside an identified track's box is that same person (face momentarily blurred /
            # turned / low-quality frame). Attribute it to the bound identity and flag it
            # `inferred` so the writer shows their name and never raises an "unknown" alert
            # (and doesn't double-log attendance off a non-fresh match).
            tmap_now = track_identity.get(cam_id, {})
            if tmap_now and tracks:
                centers = [(((fo["box"][0] + fo["box"][2]) / 2 * shape[1]),
                            ((fo["box"][1] + fo["box"][3]) / 2 * shape[0]), fo)
                           for fo in faces_out]
                for t in tracks:
                    ent = tmap_now.get(t["id"])
                    if not ent:
                        continue
                    x1, y1, x2, y2 = t["box"]
                    inside = [fo for (cx, cy, fo) in centers if x1 <= cx <= x2 and y1 <= cy <= y2]
                    # Adopt a face into this identity ONLY when the track hosts EXACTLY ONE
                    # face. If a second person stands close, their (unknown) face also falls
                    # inside this person's box — attributing it would label BOTH as this
                    # person. With a single face present and unmatched, it's this person's
                    # own momentarily-unrecognized face, so attributing it is safe.
                    if len(inside) == 1 and not inside[0]["photo_base"]:
                        inside[0]["photo_base"] = ent[0]
                        inside[0]["inferred"] = True

            # ── Attendance ROI line gating: only process faces of people who crossed the line ──
            att_mod = next((m for m in mods if m["module"] == "attendance"), None)
            att_line = (att_mod or {}).get("line")
            if att_line and len(att_line) >= 2 and tracks:
                (ax, ay), (bx, by) = att_line[0], att_line[1]
                crossed = att_crossed.setdefault(cam_id, set())
                prev_sides = att_prev_side.setdefault(cam_id, {})
                track_ids = {t["id"] for t in tracks}
                for t in tracks:
                    cx, cy = t["centroid"]
                    nx, ny = cx / shape[1], cy / shape[0]
                    s = side_of_line(nx, ny, ax, ay, bx, by)
                    if t["id"] not in crossed:
                        prev = prev_sides.get(t["id"])
                        if prev is not None and s != 0 and (s > 0) != (prev > 0):
                            crossed.add(t["id"])
                    prev_sides[t["id"]] = s
                for tid in list(prev_sides):
                    if tid not in track_ids:
                        prev_sides.pop(tid, None)
                        crossed.discard(tid)
                hw, hh = shape[1], shape[0]
                for fo in faces_out:
                    if fo.get("crossed_line"):
                        continue
                    fcx = (fo["box"][0] + fo["box"][2]) / 2 * hw
                    fcy = (fo["box"][1] + fo["box"][3]) / 2 * hh
                    fo["crossed_line"] = any(
                        t["id"] in crossed
                        and t["box"][0] <= fcx <= t["box"][2]
                        and t["box"][1] <= fcy <= t["box"][3]
                        for t in tracks
                    )
            else:
                # No line → mark all faces as crossed so attendance works full-frame
                for fo in faces_out:
                    fo["crossed_line"] = True

            # Phone perception runs here too. The slow lane is a SUPERSET of every other
            # lane and it stamps the medium timer when it fires, so if it skipped phone
            # inference it would starve the phone cadence rather than contribute to it.
            # Its own cadence still lives on the medium lane (PHONE_INTERVAL); this is
            # one more evaluation, not a second pipeline.
            phone_events, phone_overlays = [], []
            if "mobile" in ids:
                phone_events, phone_overlays = run_phone(mods, tracks, frame, shape, cam_id)
                c_mob = consumers.get((cam_id, "mobile"))
                if c_mob is not None and tracks:
                    # Mirror the confirmed set onto the tracks: the identity overlay
                    # below keys off `holding_phone`.
                    confirmed = c_mob.confirmed_tracks()
                    for t in tracks:
                        if t["id"] in confirmed:
                            t["holding_phone"] = True

            # Fire/Smoke detection: dedicated model. #16 lag fix: throttle to every
            # FIRE_EVERY processed frames — fire/smoke persists for seconds, so a slightly
            # lower detection cadence doesn't miss events but halves this model's cost.
            fire_smoke_results = []
            if "fire_smoke" in backbones:
                ff = fire_frame.get(cam_id, 0)
                fire_frame[cam_id] = ff + 1
                if ff % FIRE_EVERY == 0:
                    # Contained on purpose. An optional detector failing must cost ITS
                    # OWN result, not the frame: the outer handler below abandons
                    # everything - person tracks, faces, every consumer event - and a
                    # missing fire_smoke.pt once did exactly that on every frame, until
                    # the queue filled and 228,657 frames were dropped on one camera.
                    try:
                        fire_smoke_results = fire_smoke_det.detect_fire_smoke(frame)
                    except Exception as e:
                        _detector_failed("fire_smoke", cam_id, e)

            # Vehicle detection for the no-stopping rule. Same shape as fire/smoke: a
            # throttled second pass whose results are handed to a consumer through
            # process_detections rather than the person-track process().
            vehicle_results = []
            if "vehicle" in backbones:
                vf = vehicle_frame.get(cam_id, 0)
                vehicle_frame[cam_id] = vf + 1
                if vf % VEHICLE_EVERY == 0:
                    try:
                        vehicle_results = vehicle_det.detect_vehicles(frame)
                    except Exception as e:
                        _detector_failed("vehicle", cam_id, e)

            # NOTE: vehicle COUNTING runs its own tracked detection pass inside
            # run_vehicle_count() (called below on every lane). A second
            # detect_and_track_vehicles() used to run here and its result was never
            # read - the only reader was a mis-merged line in the fire_smoke loop.
            # Removed: it was a duplicate GPU pass per slow-lane frame.

            events, overlays = [], []
            # face overlays (green matched / red unknown) — when attendance or mess is on
            if "attendance" in ids or "mess" in ids:
                for fo in faces_out:
                    if not fo.get("crossed_line", True):
                        continue
                    matched = bool(fo["photo_base"])
                    ov = {"kind": "box", "box": fo["box"],
                          "color": "online" if matched else "dnd",
                          "label": "ID" if matched else "Unknown"}
                    if matched:
                        ov["ident"] = fo["photo_base"]
                    overlays.append(ov)

            # ── smoking (detector-driven, like attendance — not a track rule) ──
            if "smoking" in backbones:
                sm_cfg = next((m for m in mods if m["module"] == "smoking"), {})
                conf = float(sm_cfg.get("confidence_threshold") or 0.6)
                smoke_dets = smoking_det.detect_smoking(frame, conf)
                for d in smoke_dets:
                    x1, y1, x2, y2 = d["box"]
                    overlays.append({"kind": "box",
                                     "box": [x1 / shape[1], y1 / shape[0], x2 / shape[1], y2 / shape[0]],
                                     "color": "dnd", "label": "SMOKING"})
                if smoke_dets:
                    nowf = time.time()
                    if nowf - smoke_last.get(cam_id, 0) >= SMOKE_COOLDOWN:
                        smoke_last[cam_id] = nowf
                        top = max(d["confidence"] for d in smoke_dets)
                        events.append({"module": "smoking", "event_type": "smoking",
                                       "label": "Smoking Detected", "value": round(top, 3),
                                       "data": {"count": len(smoke_dets), "confidence": round(top, 3)},
                                       "snapshot": True})

            evs, ovs = run_consumers(mods, tracks, faces_out, shape, cam_id, truck_tracks)
            events += evs
            overlays += ovs
            # Phone results were produced above; the mobile consumer's process() is inert
            # because detections arrive through process_phones instead.
            events += phone_events
            overlays += phone_overlays

            # Fire/Smoke analytics consumer
            for m in mods:
                if m["module"] != "fire_smoke":
                    continue
                key = (cam_id, "fire_smoke")
                c = consumers.get(key)
                if c is None:
                    c = make_consumer("fire_smoke", cam_id, m)
                    consumers[key] = c
                else:
                    c.config = m
                try:
                    evs, ovs = c.process_detections(fire_smoke_results, shape)
                except Exception as e:
                    print(f"[PERCEPTION] fire_smoke error: {e}", flush=True)
                    log_module("fire_smoke", f"ERROR {e}", cam_id)
                    evs, ovs = [], []
                for e in evs:
                    e["module"] = "fire_smoke"
                events += evs
                overlays += ovs

            # No-stopping consumer (vehicles). Mirrors the fire_smoke block: the
            # consumer process() is inert and detections arrive here instead.
            for m in mods:
                if m["module"] != "no_stopping":
                    continue
                key = (cam_id, "no_stopping")
                c = consumers.get(key)
                if c is None:
                    c = make_consumer("no_stopping", cam_id, m)
                    consumers[key] = c
                else:
                    c.config = m
                try:
                    evs, ovs = c.process_detections(vehicle_results, shape)
                except Exception as e:
                    print(f"[PERCEPTION] no_stopping error: {e}", flush=True)
                    log_module("no_stopping", f"ERROR {e}", cam_id)
                    evs, ovs = [], []
                for e in evs:
                    e["module"] = "no_stopping"
                events += evs
                overlays += ovs

            # ── vehicle counting ────────────────────────────────────────────
            v_evs, v_ovs = run_vehicle_count(mods, frame, shape, cam_id, cam_key)
            events += v_evs
            overlays += v_ovs

            # Persisted-identity overlay: tag tracked persons with bound identity
            tmap = track_identity.get(cam_id, {})
            if tmap:
                crossed = att_crossed.get(cam_id, set())
                for t in tracks:
                    ent = tmap.get(t["id"])
                    if not ent:
                        continue
                    is_phone = t.get("holding_phone", False)
                    # Attendance/Mess mode: show identity box for recognized persons
                    if ("attendance" in ids or "mess" in ids) and (not att_line or t["id"] in crossed):
                        x1, y1, x2, y2 = t["box"]
                        overlays.append({"kind": "box",
                                         "box": [x1 / shape[1], y1 / shape[0], x2 / shape[1], y2 / shape[0]],
                                         "color": "online", "label": "ID", "ident": ent[0],
                                         "phone": is_phone})

            # Remove duplicate mobile.py PHONE overlays for known persons
            phone_id_boxes = {tuple(ov["box"]) for ov in overlays if ov.get("phone")}
            if phone_id_boxes:
                overlays = [ov for ov in overlays if not (
                    ov.get("label") == "PHONE" and tuple(ov["box"]) in phone_id_boxes and not ov.get("phone")
                )]

            # Attendance ROI line overlay
            if att_line and len(att_line) >= 2:
                overlays.append({"kind": "line", "points": att_line,
                                 "color": "online", "label": "GATE"})

            # Don't send faces for attendance when module is off (just needed for identity fusion)
            if "attendance" not in ids and "mess" not in ids:
                faces_out = []

            log_events(events, cam_id)
            result_queue.put({"cam_id": cam_id, "scale": scale, "faces": faces_out,
                              "events": events, "overlays": overlays,
                              "snapshot": frame_buf, "frame_seq": frame_seq,
                              "enroll": enroll_out})
        except Exception as e:
            # Say WHICH camera, and print the stack ONCE per distinct failure. The old
            # message was "[PERCEPTION] loop error: [Errno 2] No such file or directory:
            # 'models/fire_smoke.pt'" repeated at frame rate - no camera, no module, no
            # traceback, and no clue that it was silently discarding every frame on the
            # machine. Rate-limiting matters as much as the detail: at 40 fps an
            # unthrottled message buries every other line in the log.
            _loop_error(locals().get("cam_id"), e)
