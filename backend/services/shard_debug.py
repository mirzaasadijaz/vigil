"""
Perception sharding diagnostic log.

Enable — ONE variable:
    SHARD_DEBUG=trace     # every routed task, per shard (very noisy — short captures)
    SHARD_DEBUG=debug     # routing decisions, broadcasts, enrol fan-out, queue depth
    SHARD_DEBUG=1         # lifecycle + assignment + METRICS only (INFO)
    unset / =0            # off, the default

Optional overrides:
    SHARD_DEBUG_LOG=/path/to/file   # default <repo>/logs/shard_debug.log
    SHARD_METRICS_EVERY=60          # seconds between METRICS snapshots

WHAT THIS LOG IS FOR
--------------------
Sharding is the one change in this pipeline that can go wrong SILENTLY. Every failure
mode looks like something else:

    a task routed to the wrong shard          -> "that camera stopped detecting"
    a gallery command routed instead of
      broadcast                               -> "face recognition broke on some cameras"
    an auto-enrolled vector not fanned out    -> "recognition got slightly worse"
    an unbalanced split                       -> "CPU is still pegged"
    a dead shard                              -> "three cameras went quiet"

None of those raise an exception, and none of them are visible in any other log. So this
one records the routing table itself, not just its consequences:

    SESSION      shard count, cost model, resolved lane rates, map file
    ASSIGN       camera -> shard, its weight, and the resulting load on every shard
    BALANCE      the full table: which cameras on which shard, peak vs ideal
    SPAWN/DEAD   child lifecycle, with pid
    BROADCAST    a gallery command and how many shards accepted it
    FANOUT       an auto-enrolled vector pushed to the other shards
    ROUTE        (trace) every task, with its camera, lane and destination shard
    DROP         a task that could not be queued, and on which shard
    METRICS      per-shard queue depth, tasks routed, drops

READ THIS FIRST WHEN A CAMERA GOES QUIET AFTER ENABLING SHARDING
----------------------------------------------------------------
Grep ASSIGN for the camera id. If it is absent, no frame for that camera ever reached
the router — the problem is upstream in camera_manager, not here. If it is present,
check DROP for that shard: a single hot shard fills its own queue while the others idle,
which is the signature of a bad split rather than a bad camera.

Runs in the MAIN process (the router lives there; children never route).
"""

from __future__ import annotations

from backend.services.analytics.diagnostics import TRACE_LEVEL, DiagnosticLog

__all__ = ["TRACE_LEVEL", "LOG", "reset", "announce", "debug_enabled", "configured_level",
           "tracing", "verbose", "log_path", "log", "info", "debug", "trace",
           "session", "Metrics", "METRIC_FIELDS"]

LOG = DiagnosticLog("shard", env_prefix="SHARD_DEBUG", filename="shard_debug.log")

METRIC_FIELDS = (
    "routed",            # frame tasks handed to a shard
    "broadcast",         # gallery commands sent to every shard
    "fanout",            # auto-enrolled vectors pushed to other shards
    "assigned",          # cameras placed on a shard for the first time
    "dropped",           # tasks that could not be queued (shard queue full)
    "rejected",          # anything else declined here
)


def announce(where: str = "") -> bool:
    return LOG.announce(where)


def reset() -> None:
    LOG.reset()


def debug_enabled() -> bool:
    return LOG.enabled()


def configured_level() -> int:
    return LOG.level()


def tracing() -> bool:
    return LOG.tracing()


def verbose() -> bool:
    return LOG.verbose()


def log_path() -> str:
    return LOG.path()


def log(level: int, /, **fields) -> None:
    LOG.log(level, **fields)


def info(**fields) -> None:
    LOG.info(**fields)


def debug(**fields) -> None:
    LOG.debug(**fields)


def trace(**fields) -> None:
    LOG.trace(**fields)


def session(key, config: dict, extra: dict | None = None, force: bool = False) -> None:
    LOG.session(key, config, extra, force)


def Metrics(key="router", report_interval: float | None = None):
    return LOG.metrics(key, METRIC_FIELDS, reject_field="rejected",
                       report_interval=report_interval)


def log_balance(snapshot: dict, ideal: float | None = None) -> None:
    """Emit the whole routing table.

    Called after every new assignment, because the interesting question is never "where
    did this one camera go" but "what does the split look like now". A peak far above
    the ideal is the signal that the cost model has drifted from reality — which is
    exactly what a naive `cam_id % N` split would show, and why this is not that."""
    if not LOG.enabled():
        return
    loads = snapshot.get("loads") or [0.0]
    total = sum(loads)
    n = max(1, snapshot.get("shards", 1))
    ideal = ideal if ideal is not None else total / n
    for idx, cams in sorted((snapshot.get("by_shard") or {}).items()):
        LOG.info(event="BALANCE", shard=idx, cameras=tuple(cams), count=len(cams),
                 load_ms=round(loads[idx] if idx < len(loads) else 0.0, 1),
                 pct_core=round((loads[idx] if idx < len(loads) else 0.0) / 10.0, 1))
    LOG.info(event="BALANCE_SUMMARY", shards=n,
             total_ms=round(total, 1), ideal_ms=round(ideal, 1),
             peak_ms=round(max(loads), 1),
             peak_over_ideal=round((max(loads) / ideal) if ideal else 1.0, 3))
