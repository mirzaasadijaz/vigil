"""
Unusual-gathering / crowd diagnostic logging (Kohat POC #8).

Enable — ONE variable:
    CROWD_DEBUG=trace     # every cluster evaluated on every processed frame
    CROWD_DEBUG=debug     # clusters that reached min_people but not the sustain window
    CROWD_DEBUG=1         # decisions only (INFO)
    unset / =0      # off, the default

Optional overrides:
    CROWD_DEBUG_LOG=/path/to/file   # default <repo>/logs/crowd_debug.log
    CROWD_METRICS_EVERY=60              # seconds between METRICS snapshots

WHAT TO LOOK FOR.

A gathering is not a head count. The two failure modes are opposite and the log
distinguishes them:

    largest_cluster never reaches min_people   -> the RADIUS is too small for this
                                                  camera's perspective, or people are
                                                  being counted who are not clustered
    clusters form and dissolve below sustain   -> people are walking past each other,
                                                  not gathering. Raise sustain_seconds
                                                  rather than min_people.

`radius` is in NORMALISED units (fraction of frame width), so it survives a resolution
change but NOT a change of camera angle — a wide shot needs a smaller radius than a
close one. Expect to tune it per camera.

Runs inside the perception CHILD process (no DB access, file I/O only).
"""

from __future__ import annotations

from backend.services.analytics.diagnostics import TRACE_LEVEL, DiagnosticLog

__all__ = ["TRACE_LEVEL", "LOG", "reset", "announce", "debug_enabled", "configured_level",
           "tracing", "verbose", "log_path", "log", "info", "debug", "trace",
           "session", "Metrics", "METRIC_FIELDS"]

LOG = DiagnosticLog("crowd", env_prefix="CROWD_DEBUG", filename="crowd_debug.log")

METRIC_FIELDS = (
    "frames",
    "people_in_zone",
    "clusters_formed",          # reached min_people this frame
    "gatherings_alerted",       # survived the sustain window
    "dispersals_alerted",
    "filtered_below_sustain",   # cluster existed but broke up too soon
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
