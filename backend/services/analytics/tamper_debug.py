"""
Camera-tampering diagnostic logging (Kohat POC #4).

Enable — ONE variable:
    TAMPER_DEBUG=trace     # the three signal values on every sampled frame
    TAMPER_DEBUG=debug     # frames whose signals are past a threshold but not yet sustained
    TAMPER_DEBUG=1         # decisions only (INFO)
    unset / =0      # off, the default

Optional overrides:
    TAMPER_DEBUG_LOG=/path/to/file   # default <repo>/logs/tamper_debug.log
    TAMPER_METRICS_EVERY=60              # seconds between METRICS snapshots

WHAT TO LOOK FOR.

Three independent signals, each with its own failure character:

    blur      variance-of-Laplacian collapse  -> lens sprayed, defocused, or the stream
                                                 degraded to a very low bitrate
    moved     histogram divergence from the   -> camera physically turned; ALSO fires on
              rolling reference                  a genuine scene change (lights off,
                                                 shutter down), which is worth knowing
    covered   sustained low luminance variance-> lens blocked, bag over the camera

Every one requires a SUSTAIN window. A single dark or blurred frame is a compression
artefact; ten seconds of them is an intervention. If you see alerts firing on single
frames, `sustain_seconds` is not being applied — that is a bug, not a tuning problem.

The reference histogram is rebuilt after an alert clears, so a legitimately repositioned
camera settles instead of alerting forever.

Runs in the MAIN process inside camera_manager, NOT in the perception child.
That placement is deliberate and it matters for diagnosis: tampering must still
be detected when the GPU child has died, because a dead child looks a lot like a
blinded camera. If this log is being written while headcount_debug.log is silent,
the child is down — which is itself the finding.
"""

from __future__ import annotations

from backend.services.analytics.diagnostics import TRACE_LEVEL, DiagnosticLog

__all__ = ["TRACE_LEVEL", "LOG", "reset", "announce", "debug_enabled", "configured_level",
           "tracing", "verbose", "log_path", "log", "info", "debug", "trace",
           "session", "Metrics", "METRIC_FIELDS"]

LOG = DiagnosticLog("tamper", env_prefix="TAMPER_DEBUG", filename="tamper_debug.log")

METRIC_FIELDS = (
    "frames_sampled",
    "blur_over_threshold",
    "scene_over_threshold",
    "coverage_over_threshold",
    "blur_alerted",
    "moved_alerted",
    "covered_alerted",
    "filtered_below_sustain",
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
