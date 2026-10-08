"""
Object-dropped-and-left diagnostic logging (Kohat POC #5).

Enable — ONE variable:
    UNATTENDED_DEBUG=trace     # every evaluation, including the mask statistics
    UNATTENDED_DEBUG=debug     # region lifecycle: appeared, pending, gone, suppressed
    UNATTENDED_DEBUG=1         # alerts, warm-up and METRICS only (INFO)
    unset / =0                 # off, the default

Optional overrides:
    UNATTENDED_DEBUG_LOG=/path/to/file   # default <repo>/logs/unattended_debug.log
    UNATTENDED_METRICS_EVERY=60          # seconds between METRICS snapshots

WHAT TO READ, AND IN WHAT ORDER
-------------------------------
This module has three places a detection can die, and they need different fixes. The log
is arranged so one capture tells you which:

    1. THE MASK          `SAMPLE` (trace) - long_fg, short_fg, static_frac
       Is the dual background producing anything at all? `static_frac` near zero every
       sample means no region is ever both new-and-stopped. Either nothing was dropped,
       or SHORT_ALPHA is too slow to absorb a stationary object (so it stays foreground
       in both models and the conjunction never fires).

    2. THE FILTERS       `METRICS` reject_reasons
       Regions exist but none survive. The breakdown names which gate:
         too_large        a lighting change or camera nudge, not an object
         ragged           blob does not fill its own box - not a compact object
         outside_zone     it happened where you are not watching
         is_a_person      somebody standing still (see below)
         cooldown         same object, too soon after the last alert

    3. THE TIMER         `APPEARED` -> `PENDING` -> `UNATTENDED` / `GONE`
       Regions survive but never mature. `PENDING dwell=... abandon=...` says how far it
       got; `GONE dwell=... alerted=0` says it vanished before the threshold. Repeated
       APPEARED/GONE pairs for the same place mean the region keeps breaking - raise
       MISS_GRACE, or the object is being occluded by passers-by.

THE WARM-UP MATTERS
    Nothing may fire until the LONG background model has learned the scene. Before that
    every object already present looks newly-arrived. `START warmup_seconds=...` records
    it and `SAMPLE warming=1` marks the samples that were ignored. An alert storm the
    moment a camera comes up means the warm-up was too short.

THE ONE THAT SURPRISES PEOPLE
    A person standing still IS a static region, and would be reported as an abandoned
    object. Those are suppressed against the live person boxes and counted as
    `is_a_person`. If that counter is large and you are missing real alerts, the object
    is probably being detected as part of the person who is standing beside it.

Runs in the MAIN process (classical CV, no GPU), alongside tamper.
"""

from __future__ import annotations

from backend.services.analytics.diagnostics import TRACE_LEVEL, DiagnosticLog

__all__ = ["TRACE_LEVEL", "LOG", "reset", "announce", "debug_enabled", "configured_level",
           "tracing", "verbose", "log_path", "log", "info", "debug", "trace",
           "session", "Metrics", "METRIC_FIELDS"]

LOG = DiagnosticLog("unattended", env_prefix="UNATTENDED_DEBUG",
                    filename="unattended_debug.log")

METRIC_FIELDS = (
    "samples",       # evaluations performed
    "candidates",    # regions that passed every filter
    "alerted",       # objects that reached the dwell threshold
    "removed",       # slots dropped after MISS_GRACE consecutive misses
    "rejected",      # filtered out; see reject_reasons for the breakdown
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
