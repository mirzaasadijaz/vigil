"""
Perception sharding — which camera is handled by which perception child process.

WHY THIS EXISTS
---------------
All inference for all cameras used to funnel through ONE child process. That process is
single-threaded by design (`cv2.setNumThreads(0)` plus OMP/MKL pinned to 1, because
torch/ONNX sharing an address space with libpq/FFmpeg corrupted the native heap), so its
ceiling is one CPU core — measured at roughly 53 frames/sec, against a measured demand of
41. Sharding cameras across N children does not reduce the total work; it spreads it,
raising the ceiling to roughly N x 53.

WHAT MAKES THIS SAFE
--------------------
Every piece of state inside the child is keyed by `cam_id` — consumers, ByteTrack models,
identity votes, body galleries, cooldowns, throttle counters, seeds. There is no
cross-camera state. So moving a camera to a different process moves its entire state with
it, and changes nothing about how that camera is processed.

The ONE exception is the face gallery (`embedding_cache`), which is global per process.
Two rules follow, and both are enforced in `perception.py`:

  1. Gallery commands (add / remove / add_extra / reset_autos / reload_gallery /
     seed_vecs) must BROADCAST to every shard. Routing one to a single shard would
     silently apply an employee edit to only 1/N of the cameras.
  2. Auto-enrolled vectors must be FANNED OUT. A vector learned on shard 0 is not visible
     to shard 1 until it is pushed there, whereas with one child it was instantly visible
     everywhere. Without the fan-out this is a real (if small) recognition regression.

DEFAULT IS ONE SHARD
--------------------
`PERCEPTION_SHARDS` defaults to 1, which is exactly the previous behaviour: one child,
one queue, no assignment file, no routing decisions. Sharding is opt-in, so backing the
change out is `PERCEPTION_SHARDS=1` and a restart — no code revert required.

ASSIGNMENT
----------
NOT `cam_id % N`. On the reference deployment that puts both fast-lane cameras (47 and
50) on the same shard: the peak shard lands at 36.4% of a core against 28.1% for a
cost-balanced split, on an ideal of 26.9%. Cameras cost wildly different amounts — a
trip-wire camera runs at 10 fps and a slow-lane camera at 2 — so assignment is greedy
least-loaded on an estimated cost, and the result is PERSISTED. Stability matters: moving
a camera between shards resets its ByteTrack ids mid-run.
"""

from __future__ import annotations

import json
import os
import threading

# ── Cost model ───────────────────────────────────────────────────────────────────
# Per-frame backbone cost, in milliseconds of CPU. Derived by decomposing the measured
# 78% of a core against the measured per-camera frame rates (Appendix D of
# unoptimized-architecture.md); the model reproduces that measurement to within 3%.
# These only need to be RELATIVELY right — they rank cameras, they do not predict.
PERSON_MS = float(os.environ.get("SHARD_COST_PERSON_MS", "12.4"))
FACE_MS = float(os.environ.get("SHARD_COST_FACE_MS", "15.7"))

# Fallbacks only; the live values are read from camera_manager (see _lane_rates).
_FALLBACK_FPS = {"fast": 10.0, "medium": 5.0, "slow": 2.0}

# Modules that pull the face backbone in. `attendance` needs it by definition; `mobile`
# drags it in for name-tagging (perception.py: `if "mobile" in ids: backbones.add("face")`).
_FACE_MODULES = frozenset({"attendance", "mobile"})


def shard_count() -> int:
    """How many perception children to run. 1 == previous behaviour exactly."""
    try:
        n = int(os.environ.get("PERCEPTION_SHARDS", "1"))
    except ValueError:
        n = 1
    return max(1, min(n, 8))    # 8 is a sanity cap, not a tested limit


def _lane_rates() -> dict:
    """Lane frame rates from camera_manager, so the cost model tracks any retuning of
    TRACK_INTERVAL / PHONE_INTERVAL / ENQUEUE_INTERVAL instead of going stale.

    Imported lazily: camera_manager imports perception, which imports this module."""
    try:
        from backend.services import camera_manager as cm
        return {"fast": 1.0 / cm.TRACK_INTERVAL,
                "medium": 1.0 / cm.PHONE_INTERVAL,
                "slow": 1.0 / cm.ENQUEUE_INTERVAL}
    except Exception:
        return dict(_FALLBACK_FPS)


def _lane_sets() -> tuple[frozenset, frozenset]:
    try:
        from backend.services import camera_manager as cm
        return frozenset(cm.FAST_MODULES), frozenset(cm.MEDIUM_MODULES)
    except Exception:
        return frozenset({"headcount", "tripwire"}), frozenset({"mobile"})


def weight_for(modules) -> float:
    """Estimated CPU cost of one camera, in milliseconds per second.

    `modules` is the camera's module list (dicts with "module"/"enabled", as stored in
    `camera_devices.module_config`) or a bare iterable of module ids."""
    ids = set()
    for m in (modules or []):
        if isinstance(m, dict):
            if m.get("enabled", True) and m.get("module"):
                ids.add(m["module"])
        elif m:
            ids.add(str(m))
    if not ids:
        return 0.0
    fast, medium = _lane_sets()
    rates = _lane_rates()
    # A camera enqueues at its FASTEST due lane; the slower lanes are subsets of it, not
    # additional frames on top (a due slow frame is also a medium and a fast frame).
    if ids & fast:
        fps = rates["fast"]
    elif ids & medium:
        fps = rates["medium"]
    else:
        fps = rates["slow"]
    per_frame = PERSON_MS + (FACE_MS if ids & _FACE_MODULES else 0.0)
    return fps * per_frame


def _default_path() -> str:
    override = os.environ.get("SHARD_MAP_PATH")
    if override:
        return override
    try:
        from backend.services.analytics.diagnostics import repo_root
        base = repo_root()
    except Exception:
        base = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    return os.path.join(base, "logs", "shard_assignment.json")


class ShardMap:
    """camera -> shard index, cost-balanced and persisted.

    Lives in the MAIN process only. Children never route anything; they are handed work
    and have no idea other shards exist."""

    def __init__(self, n: int, path: str | None = None):
        self.n = max(1, n)
        self.path = path or _default_path()
        self._lock = threading.RLock()
        self._assign: dict[str, int] = {}     # str(cam_id) -> shard index
        self._weight: dict[str, float] = {}   # str(cam_id) -> last known weight
        self._loaded = False

    # ── persistence ──────────────────────────────────────────────────────────
    def load(self) -> None:
        """Restore a previous assignment.

        A camera that changes shard mid-deployment loses its ByteTrack ids and its
        consumers' in-memory state (head-count and trip-wire totals re-seed from
        alert_logs, so those recover). That is why the map is written to disk rather than
        recomputed from a camera list that changes whenever somebody adds a camera."""
        with self._lock:
            self._loaded = True
            if self.n <= 1:
                return
            try:
                with open(self.path, encoding="utf-8") as fh:
                    data = json.load(fh)
            except Exception:
                return
            # A different shard count invalidates the whole map — keeping it would leave
            # cameras pointing at shards that no longer exist.
            if int(data.get("shards", 0)) != self.n:
                return
            self._assign = {str(k): int(v) for k, v in (data.get("assign") or {}).items()
                            if 0 <= int(v) < self.n}
            self._weight = {str(k): float(v) for k, v in (data.get("weight") or {}).items()}

    def save(self) -> None:
        if self.n <= 1:
            return
        with self._lock:
            payload = {"shards": self.n, "assign": dict(self._assign),
                       "weight": dict(self._weight)}
        try:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, indent=2, sort_keys=True)
            os.replace(tmp, self.path)          # atomic; never a half-written map
        except Exception:
            pass                                # never break the pipeline over a cache

    # ── routing ──────────────────────────────────────────────────────────────
    def loads(self) -> list[float]:
        with self._lock:
            out = [0.0] * self.n
            for cam, s in self._assign.items():
                if 0 <= s < self.n:
                    out[s] += self._weight.get(cam, 0.0)
            return out

    def peek(self, cam_id) -> "int | None":
        """The camera's shard if it already has one, else None — never assigns.

        Everything that is not the frame-routing path must use this. `assign()` needs
        the camera's module list to weigh it, and the callers that lack one (queue-depth
        checks, enrol fan-out) would otherwise place the camera at weight zero. That is
        not a cosmetic error: camera_manager checks the queue depth BEFORE it enqueues
        the first frame, so every camera would be assigned weight-zero, every camera
        would land on the least-loaded-so-far shard 0, and sharding would silently do
        nothing at all — with the wrong map persisted to disk for next time."""
        with self._lock:
            if not self._loaded:
                self.load()
            return self._assign.get(str(cam_id))

    def known(self, cam_id) -> bool:
        return str(cam_id) in self._assign

    def known_any(self) -> bool:
        """True once at least one camera has been placed — i.e. there is a table worth
        logging. Distinguishes "restored a previous assignment" from "cold start"."""
        return bool(self._assign)

    def assign(self, cam_id, modules=None) -> tuple[int, bool]:
        """Return (shard, newly_assigned) for a camera, assigning it if needed.

        Greedy least-loaded on estimated cost. Greedy is not optimal, but on the
        reference deployment it lands within 1.2 points of the ideal split, and it has
        the property that actually matters here: it is incremental, so adding a tenth
        camera does not reshuffle the nine already running."""
        if self.n <= 1:
            return 0, False
        key = str(cam_id)
        with self._lock:
            if not self._loaded:
                self.load()
            w = weight_for(modules)
            existing = self._assign.get(key)
            if existing is None and w <= 0:
                # No module list, so no idea what this camera costs. Refusing to place
                # it is the whole point of peek() — see the note there. Report shard 0
                # provisionally without recording anything; the first real frame task
                # carries modules and does the placement properly.
                return 0, False
            if existing is not None:
                # Keep the placement; refresh the weight so later assignments see the
                # true load. Re-placing a running camera is never worth the reset.
                if w > 0 and abs(w - self._weight.get(key, 0.0)) > 0.01:
                    self._weight[key] = w
                    self.save()
                return existing, False
            loads = [0.0] * self.n
            for cam, s in self._assign.items():
                if 0 <= s < self.n:
                    loads[s] += self._weight.get(cam, 0.0)
            shard = min(range(self.n), key=lambda i: (loads[i], i))
            self._assign[key] = shard
            self._weight[key] = w
            self.save()
            return shard, True

    def forget(self, cam_id) -> None:
        """Drop a camera from the map, so a deleted camera's weight stops distorting
        future assignments."""
        if self.n <= 1:
            return
        with self._lock:
            if self._assign.pop(str(cam_id), None) is not None:
                self._weight.pop(str(cam_id), None)
                self.save()

    def snapshot(self) -> dict:
        with self._lock:
            by_shard: dict[int, list] = {i: [] for i in range(self.n)}
            for cam, s in sorted(self._assign.items(), key=lambda kv: int(kv[0])):
                by_shard.setdefault(s, []).append(int(cam))
            return {"shards": self.n, "by_shard": by_shard, "loads": self.loads()}
