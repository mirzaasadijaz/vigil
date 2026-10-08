"""
Restricted-zone intrusion diagnostic logging.

Enable — ONE variable:
    INTRUSION_DEBUG=trace     # every track tested against the zone, every frame
    INTRUSION_DEBUG=debug     # decisions plus suppressed alerts
    INTRUSION_DEBUG=1         # decisions only (INFO)
    unset / =0                # off, the default

Optional overrides:
    INTRUSION_DEBUG_LOG=/path/to/file   # default <repo>/logs/intrusion_debug.log
    INTRUSION_METRICS_EVERY=60          # seconds between METRICS snapshots

This adds NO behaviour. The intrusion module is unchanged — same centroid membership
test, same fixed 10-second cooldown, same events and overlays. Every line logged
observes a decision that was already being made.

READING A CAPTURE

    people walk through the zone      Compare `point=` on the OBS lines against the
    without being reported            polygon you drew. That is the coordinate the module
                                      actually tests, and a zone drawn where people's
                                      feet go rather than where their chest goes is the
                                      usual explanation.

    alerts fire with nobody in it     A detection box overlapping the zone edge. There is
                                      no dwell window, so a single frame inside the
                                      polygon is enough.

    only one alert for several        Expected. One event carries `value` = the number of
    people                            intruders and `data.tracks` lists them; the cooldown
                                      throttles the zone as a whole, not each person.
                                      `verdict=COOLDOWN` shows what was suppressed.

    nothing in the log at all         Either the module is not assigned to that camera, or
                                      no zone is drawn. `event=CREATED` appears the moment
                                      the consumer is built and records the zone count,
                                      which separates those two cases.

Runs inside the perception CHILD process (no DB access, file I/O only).
"""

from __future__ import annotations

from backend.services.analytics.diagnostics import TRACE_LEVEL, DiagnosticLog

__all__ = ["TRACE_LEVEL", "LOG", "reset", "announce", "debug_enabled", "configured_level",
           "tracing", "verbose", "log_path", "log", "info", "debug", "trace",
           "session", "Metrics", "METRIC_FIELDS"]

LOG = DiagnosticLog("intrusion", env_prefix="INTRUSION_DEBUG",
                    filename="intrusion_debug.log")

METRIC_FIELDS = (
    "frames",
    "tracks_seen",
    "tracks_in_zone",
    "intrusions_alerted",
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
