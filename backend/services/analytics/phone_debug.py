"""
Phone-use diagnostic logging (Phase 2).

Same mechanism as the head-count logger — see `diagnostics.DiagnosticLog` — with its own
file and its own switch, so the two can be captured independently.

Enable — ONE variable:
    PHONE_DEBUG=trace     # full capture: every detection, association and state change
    PHONE_DEBUG=debug     # decisions + per-evaluation detail for associated tracks
    PHONE_DEBUG=1         # decisions only (INFO)
    unset / =0            # off, the default

Optional overrides:
    PHONE_DEBUG_LOG=/path/to/file   # default <repo>/logs/phone_debug.log
    PHONE_DEBUG_MAX_MB=50           # rotation size; defaults to 50 at TRACE, else 5
    PHONE_DEBUG_LEVEL=TRACE         # legacy split form, overrides the above
    PHONE_METRICS_EVERY=60          # seconds between METRICS snapshots

Levels:
    INFO   decisions only: state transitions, confirmed phone use, releases, rejected
           associations, per-track summaries, periodic METRICS.
    DEBUG  adds one EVAL line per track per phone evaluation, showing the temporal
           evidence window and why the state did or did not change.
    TRACE  adds a DET line for EVERY raw phone detection (before association) and an
           ASSOC line for every candidate person considered. This is the full record
           that makes a capture REPLAYABLE
           (backend/scripts/phone_report.py --replay), so a real-world false positive
           or miss becomes a reproducible test case without needing the video.

Runs inside the perception CHILD process (no DB access, file I/O only).
"""

from __future__ import annotations

from backend.services.analytics.diagnostics import TRACE_LEVEL, DiagnosticLog

__all__ = ["TRACE_LEVEL", "LOG", "reset", "announce", "debug_enabled", "configured_level",
           "tracing", "verbose", "log_path", "log", "info", "debug", "trace",
           "session", "Metrics", "METRIC_FIELDS"]

LOG = DiagnosticLog("phone", env_prefix="PHONE_DEBUG", filename="phone_debug.log")

METRIC_FIELDS = (
    "frames_evaluated",
    "raw_detections",
    "below_confidence",
    "detections_associated",
    "detections_unassociated",
    "phone_candidates",
    "phone_confirmed",
    "phone_use_events",
    "phone_released",
    "association_rejections",
    "temporal_rejections",
    "static_rejections",
    "motion_rejections",
    "duplicate_events_prevented",
    "tracks_seen",
)


def announce(where: str = "") -> bool:
    """Report the switch state and open the log file if enabled."""
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
    return LOG.metrics(cam_id, METRIC_FIELDS,
                       reject_field="association_rejections",
                       report_interval=report_interval)
