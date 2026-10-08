"""
Vehicle no-stopping diagnostic logging (Kohat POC #6).

Enable — ONE variable:
    VEHICLE_DEBUG=trace     # every vehicle box on every evaluation, moving or not
    VEHICLE_DEBUG=debug     # vehicles inside a zone with their dwell timers
    VEHICLE_DEBUG=1         # decisions only (INFO)
    unset / =0      # off, the default

Optional overrides:
    VEHICLE_DEBUG_LOG=/path/to/file   # default <repo>/logs/vehicle_debug.log
    VEHICLE_METRICS_EVERY=60              # seconds between METRICS snapshots

WHAT TO LOOK FOR.

Detection is rarely the problem here; STATIONARITY is. A parked car and a car waiting
at a barrier are identical for the first few seconds, and a stationary box still wobbles
a few pixels per frame from detector jitter.

    dwell resets repeatedly            -> `move_tolerance` is too tight; jitter is being
                                          read as movement
    a moving vehicle triggers an alert -> `move_tolerance` is too loose, or the vehicle
                                          genuinely stopped in the zone and the alert is
                                          correct

TRACE records the smoothed displacement per evaluation, which is what you need to pick
a tolerance instead of guessing at one.

Runs inside the perception CHILD process (no DB access, file I/O only).
"""

from __future__ import annotations

from backend.services.analytics.diagnostics import TRACE_LEVEL, DiagnosticLog

__all__ = ["TRACE_LEVEL", "LOG", "reset", "announce", "debug_enabled", "configured_level",
           "tracing", "verbose", "log_path", "log", "info", "debug", "trace",
           "session", "Metrics", "METRIC_FIELDS"]

LOG = DiagnosticLog("vehicle", env_prefix="VEHICLE_DEBUG", filename="vehicle_debug.log")

METRIC_FIELDS = (
    "evaluations",
    "vehicles_detected",
    "vehicles_in_zone",
    "dwell_started",
    "dwell_reset_by_motion",
    "stops_alerted",
    "filtered_cooldown",
    "rejected",
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
