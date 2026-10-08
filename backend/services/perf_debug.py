"""
Pipeline performance diagnostics — where the CPU actually goes.

Enable — ONE variable:
    PERF_DEBUG=trace     # every sampled operation, per camera
    PERF_DEBUG=debug     # per-camera SUMMARY lines plus notable events
    PERF_DEBUG=1         # SUMMARY lines only (INFO)
    unset / =0           # off, the default — and genuinely free when off

Optional overrides:
    PERF_DEBUG_LOG=/path/to/file    # default <repo>/logs/perf_debug.log
    PERF_SUMMARY_EVERY=10           # seconds between SUMMARY lines
    PERF_SAMPLE_EVERY=20            # at trace level, log 1 operation in N

WHY THIS EXISTS
    A process pinned at 98% CPU tells you nothing about WHICH work is responsible.
    Diagnosing it from the outside means guessing; every guess costs a round trip and a
    restart on a live system.

    This records the cost of each stage separately, per camera, so the answer is read off
    a log rather than inferred:

        decode      pulling a frame off the stream and converting it            (capture)
                    NOTE: cap.read() BLOCKS waiting for the next frame, so its WALL time
                    is ~100% of any window on a live camera and means nothing by itself.
                    That is why every stage records CPU time separately.
        resize      building the detection frame handed to the perception child (AI lane)
        enqueue     handing that frame across the process boundary              (IPC)
        snapshot    full-resolution JPEG kept as alert evidence                 (writer)
        broadcast   JPEG for the live view in a browser                         (viewers)
        overlay     drawing boxes and zones onto the streamed frame             (viewers)

READING A SUMMARY LINE
    Each line reports, for one camera over the interval:

        fps_in      frames actually taken off the stream
        fps_ai      frames handed to the perception pipeline
        fps_out     frames encoded for viewers
        <stage>_ms  WALL milliseconds - includes time blocked on the network, so a
                    large decode_ms is normal and is not evidence of a problem
        <stage>_cpu CPU milliseconds actually burned (time.thread_time), which excludes
                    blocking. THIS is what costs you a core
        cpu_pct     share of ONE core, computed from the CPU figures not the wall ones
        drops_per_sec
                    iterations that found the perception queue full. Far ABOVE the
                    camera's frame rate means the loop is spinning rather than waiting -
                    a bug, not a busy machine. That is exactly how the busy-spin in
                    camera_manager was found: 2196 drops/sec on a 15 fps camera.

    `cpu_pct` is the number to watch. Summed across cameras it should be comfortably
    below 100 x (cores available). If one stage dominates, that is where to look - and
    the ordering above is roughly the ordering of expected cost.

COST OF THE INSTRUMENTATION ITSELF
    Timing calls are `time.perf_counter()` pairs, a few hundred nanoseconds each, and are
    skipped entirely when the log is off (`enabled()` is a cached boolean). At TRACE the
    per-operation lines are sampled 1-in-N rather than written every time, because a log
    that costs more than the work it measures is worse than no log.

Runs in the MAIN process, alongside camera_manager.
"""

from __future__ import annotations

import os
import time

from backend.services.analytics.diagnostics import TRACE_LEVEL, DiagnosticLog

__all__ = ["TRACE_LEVEL", "LOG", "reset", "announce", "enabled", "tracing", "log_path",
           "info", "debug", "trace", "session", "stage", "note_drop", "summarise",
           "STAGES"]

LOG = DiagnosticLog("perf", env_prefix="PERF_DEBUG", filename="perf_debug.log")

# Ordered roughly by expected cost, which is also the order worth investigating.
STAGES = ("decode", "resize", "enqueue", "snapshot", "broadcast", "overlay")

SUMMARY_EVERY = float(os.environ.get("PERF_SUMMARY_EVERY", "10"))
SAMPLE_EVERY = int(os.environ.get("PERF_SAMPLE_EVERY", "20"))


def announce(where: str = "") -> bool:
    return LOG.announce(where)


def reset() -> None:
    LOG.reset()
    _state.clear()


def enabled() -> bool:
    return LOG.enabled()


def tracing() -> bool:
    return LOG.tracing()


def log_path() -> str:
    return LOG.path()


def info(**fields) -> None:
    LOG.info(**fields)


def debug(**fields) -> None:
    LOG.debug(**fields)


def trace(**fields) -> None:
    LOG.trace(**fields)


def session(camera, config: dict, extra: dict | None = None) -> None:
    LOG.session(camera, config, extra)


# ── per-camera accumulators ──────────────────────────────────────────────────────
# Plain dicts rather than a class: this is touched on the hot path and a dict lookup is
# cheaper than attribute resolution through a wrapper.
_state: dict = {}


def _cam(cam_id):
    key = str(cam_id)
    st = _state.get(key)
    if st is None:
        st = _state[key] = {
            "since": time.time(),
            "counts": {s: 0 for s in STAGES},
            "ms": {s: 0.0 for s in STAGES},
            "cpu": {s: 0.0 for s in STAGES},
            "drops": 0,
            "sample": 0,
        }
    return st


class stage:
    """Context manager timing one pipeline stage for one camera.

        with perf.stage(cam_id, "decode"):
            ok, frame = cap.retrieve()

    Inert when the log is off: `enabled()` is a cached boolean, so the overhead is one
    attribute lookup and a branch."""

    __slots__ = ("cam", "name", "t0", "c0", "on")

    def __init__(self, cam_id, name: str):
        self.cam = cam_id
        self.name = name
        self.on = LOG.enabled()
        self.t0 = 0.0
        self.c0 = 0.0

    def __enter__(self):
        if self.on:
            self.t0 = time.perf_counter()
            # thread_time EXCLUDES time spent blocked. A blocking cap.read() is ~100% of
            # wall clock and ~0% of CPU; conflating the two sends you optimising the
            # wrong stage, which is precisely what happened on the first pass.
            self.c0 = time.thread_time()
        return self

    def __exit__(self, *exc):
        if not self.on:
            return False
        dt = (time.perf_counter() - self.t0) * 1000.0
        dc = (time.thread_time() - self.c0) * 1000.0
        st = _cam(self.cam)
        st["counts"][self.name] += 1
        st["ms"][self.name] += dt
        st["cpu"][self.name] += dc
        if LOG.tracing():
            st["sample"] += 1
            if st["sample"] % SAMPLE_EVERY == 0:
                LOG.trace(event="OP", camera=self.cam, stage=self.name,
                          ms=dt, cpu=dc)
        return False


def note_drop(cam_id, n: int = 1) -> None:
    """A frame the pipeline wanted but could not take (queue full)."""
    if not LOG.enabled():
        return
    _cam(cam_id)["drops"] += n


def summarise(cam_id, extra: dict | None = None) -> None:
    """Emit a SUMMARY line if the interval has elapsed, then reset the window."""
    if not LOG.enabled():
        return
    st = _cam(cam_id)
    now = time.time()
    window = now - st["since"]
    if window < SUMMARY_EVERY:
        return

    counts, ms, cpu = st["counts"], st["ms"], st["cpu"]
    total_cpu = sum(cpu.values())
    fields = {
        "window": round(window, 1),
        "fps_in": round(counts["decode"] / window, 1),
        "fps_ai": round(counts["resize"] / window, 1),
        "fps_out": round(counts["broadcast"] / window, 1),
        "drops": st["drops"],
    }
    for s in STAGES:
        if counts[s]:
            fields[f"{s}_ms"] = round(ms[s], 1)
            fields[f"{s}_cpu"] = round(cpu[s], 1)
    # Share of ONE core, from CPU time. Wall time would count blocking and read ~100%
    # on every camera, which is what made the first capture unreadable.
    fields["cpu_pct"] = round(100.0 * total_cpu / 1000.0 / window, 1)
    fields["drops_per_sec"] = round(st["drops"] / window, 1)

    LOG.info(event="SUMMARY", camera=cam_id, **fields, **(extra or {}))

    st["since"] = now
    st["drops"] = 0
    for s in STAGES:
        counts[s] = 0
        ms[s] = 0.0
        cpu[s] = 0.0


def totals() -> dict:
    """Current accumulators, for tests and for a report script."""
    return {k: {"counts": dict(v["counts"]), "ms": dict(v["ms"]),
                "cpu": dict(v["cpu"]), "drops": v["drops"]}
            for k, v in _state.items()}
