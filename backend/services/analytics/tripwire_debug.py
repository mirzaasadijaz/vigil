"""
Virtual trip-wire diagnostic logging (Kohat POC #1).

Enable — ONE variable:
    TRIPWIRE_DEBUG=trace     # every crossing considered, including ones filtered out
    TRIPWIRE_DEBUG=debug     # decisions + direction filtering
    TRIPWIRE_DEBUG=1         # decisions only (INFO)
    unset / =0               # off, the default

Optional overrides:
    TRIPWIRE_DEBUG_LOG=/path/to/file   # default <repo>/logs/tripwire_debug.log
    TRIPWIRE_METRICS_EVERY=60          # seconds between METRICS snapshots

WHERE THE DETAIL LIVES — read this before diagnosing a miss.

A trip wire is `HeadCount` with the counting removed and the alerting turned up: it
inherits the whole Phase 1 crossing machine (foot-point reference, hysteresis band,
movement validation, direction consistency, id hand-off, debounce). That means a
crossing has already been accepted or rejected by head-count logic BEFORE this module
sees it.

So the two logs answer different questions:

    logs/headcount_debug.log   why a crossing was or was not DETECTED
                               (trajectories, band entry/exit, validation rejections)
    logs/tripwire_debug.log    what this module DID with a detected crossing
                               (direction filter, cooldown, event emitted)

**A trip wire that misses somebody who definitely walked past is almost always a
detection problem, not a trip-wire problem — capture HEADCOUNT_DEBUG=trace for that.**
Set both when you do not yet know which half is at fault.

Runs inside the perception CHILD process (no DB access, file I/O only).
"""

from __future__ import annotations

from backend.services.analytics.diagnostics import TRACE_LEVEL, DiagnosticLog

__all__ = ["TRACE_LEVEL", "LOG", "reset", "announce", "debug_enabled", "configured_level",
           "tracing", "verbose", "log_path", "log", "info", "debug", "trace",
           "session", "Metrics", "METRIC_FIELDS"]

LOG = DiagnosticLog("tripwire", env_prefix="TRIPWIRE_DEBUG",
                    filename="tripwire_debug.log")

METRIC_FIELDS = (
    "crossings_detected",        # handed up by the inherited head-count machine
    "crossings_alerted",         # survived the direction filter and the cooldown
    "filtered_wrong_direction",  # crossed, but the wrong way for this wire's config
    "filtered_cooldown",         # correct direction, too soon after the previous alert
    "rejected",                  # anything else declined here
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


def session(camera, config: dict, extra: dict | None = None,
            force: bool = False) -> None:
    LOG.session(camera, config, extra, force)


def Metrics(cam_id, report_interval: float | None = None):
    return LOG.metrics(cam_id, METRIC_FIELDS, reject_field="rejected",
                       report_interval=report_interval)
