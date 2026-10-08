"""
Head-count diagnostic logging (Phase 1).

The mechanism now lives in `diagnostics.DiagnosticLog`, shared with the phone module
added in Phase 2 — this file is the head-count instance plus the module-level API that
the head-count consumer and its tests use. Behaviour is unchanged.

Enable — ONE variable is all it takes:
    HEADCOUNT_DEBUG=trace     # full capture: decisions + every trajectory (replayable)
    HEADCOUNT_DEBUG=debug     # decisions + observations near the line
    HEADCOUNT_DEBUG=1         # decisions only (INFO)
    unset / =0                # off, the default

Optional overrides (rarely needed):
    HEADCOUNT_DEBUG_LOG=/path/to/file   # default <repo>/logs/headcount_debug.log
    HEADCOUNT_DEBUG_MAX_MB=50           # rotation size; defaults to 50 at TRACE, else 5
    HEADCOUNT_DEBUG_LEVEL=TRACE         # legacy split form, overrides the above
    HEADCOUNT_METRICS_EVERY=60          # seconds between METRICS snapshots

Levels:
    INFO   decisions only: state transitions, commits, genuine rejections, id hand-offs,
           per-track summaries, periodic METRICS. A couple of lines per crossing.
    DEBUG  adds per-observation OBS lines for tracks near the line, plus PENDING lines.
           Use this to tune thresholds against a live scene.
    TRACE  adds an OBS line for EVERY track on EVERY processed frame, near the line or
           not. This is the full trajectory record that makes a captured log REPLAYABLE
           (see backend/scripts/headcount_report.py --replay), so a real-world miss can
           be turned into a reproducible test case without needing the video.

Runs inside the perception CHILD process (no DB access, file I/O only).
"""

from __future__ import annotations

from backend.services.analytics.diagnostics import TRACE_LEVEL, DiagnosticLog

__all__ = ["TRACE_LEVEL", "LOG", "reset", "announce", "debug_enabled", "configured_level",
           "tracing", "verbose", "log_path", "log", "info", "debug", "trace",
           "session", "Metrics", "METRIC_FIELDS"]

LOG = DiagnosticLog("headcount", env_prefix="HEADCOUNT_DEBUG",
                    filename="headcount_debug.log")

METRIC_FIELDS = (
    "tracks_seen",
    "tracks_near_line",
    "crossing_candidates",
    "crossings_committed",
    "entries",
    "exits",
    "rejected_crossings",
    "duplicate_crossings_prevented",
    "temporary_track_loss",
    "track_id_switches_near_line",
)


def announce(where: str = "") -> bool:
    """Report the switch state and open the log file if enabled."""
    return LOG.announce(where)


def reset() -> None:
    """Drop cached state and detach handlers (tests, or to re-read the environment)."""
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
    """Where the diagnostic log is (or would be) written. The analysis tooling uses
    this to default its input, so nobody has to retype a platform-specific path."""
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
    """Head-count counters. Keeps the Phase 1 call signature."""
    return LOG.metrics(cam_id, METRIC_FIELDS,
                       reject_field="rejected_crossings",
                       report_interval=report_interval)
