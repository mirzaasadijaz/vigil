"""
Shared diagnostic-logging machinery for analytics modules.

Phase 1 built this for head-count; Phase 2 needs the same thing for phone use. Rather
than duplicate ~300 lines, the mechanism lives here and each module owns a thin instance
that fixes its own name, environment prefix and log file:

    from backend.services.analytics.diagnostics import DiagnosticLog
    LOG = DiagnosticLog("phone", env_prefix="PHONE_DEBUG", filename="phone_debug.log")

The design goals are unchanged from Phase 1, and they are what make a captured log
useful to somebody who was not there when it was recorded:

  * completely inert unless explicitly switched on, and off by default
  * its own file and its own logger with propagate=False, so it never pollutes the
    application log
  * ONE environment variable carries both the on/off state and the level
  * a SESSION header recording the resolved configuration — without it a captured log
    cannot be interpreted, because a miss caused by bad configuration looks identical
    to a miss caused by a bad threshold
  * a TRACE level that records enough per-observation detail for the capture to be
    REPLAYED offline, turning a real-world failure into a reproducible test case
"""

from __future__ import annotations

import logging
import os
import threading
import time
from logging.handlers import RotatingFileHandler

TRACE_LEVEL = 5
logging.addLevelName(TRACE_LEVEL, "TRACE")

_TRUTHY = ("1", "TRUE", "ON", "YES")
_FALSEY = ("0", "", "FALSE", "NO", "OFF")


def repo_root() -> str:
    """backend/services/analytics/ -> repo root."""
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__)))))


def fmt(value) -> str:
    """Render one field value. Floats are fixed-width so logs stay column-scannable,
    and sequences render without spaces so a line splits cleanly on whitespace."""
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, float):
        return f"{value:.4f}"
    if isinstance(value, (tuple, list)):
        return "(" + ",".join(fmt(v) for v in value) + ")"
    return str(value)


class Metrics:
    """Diagnostic counters for one (camera, module).

    Not persisted and not exposed through the API — these exist so a tuning run can be
    evaluated from the log. `maybe_report` emits a snapshot at a slow interval so a long
    capture produces a readable summary rather than only a final number.
    """

    def __init__(self, log: "DiagnosticLog", cam_id, fields: tuple[str, ...],
                 reject_field: str = "rejected", report_interval: float | None = None):
        self._log = log
        self.cam_id = cam_id
        self.FIELDS = fields
        self._reject_field = reject_field
        self.report_interval = (
            report_interval if report_interval is not None
            else float(os.environ.get(f"{log.env_prefix}_METRICS_EVERY", "60")))
        self._last_report = time.time()
        self.reject_reasons: dict[str, int] = {}
        for f in fields:
            setattr(self, f, 0)

    def bump(self, field: str, n: int = 1) -> None:
        setattr(self, field, getattr(self, field, 0) + n)

    def note(self, field: str, reason: str) -> None:
        """Count a rejection against a SPECIFIC counter, and record why.

        `reject()` uses the module's default counter; some rejections belong to their
        own (a static object is not an association failure), but the reason must still
        reach the breakdown either way."""
        self.bump(field)
        self.reject_reasons[reason] = self.reject_reasons.get(reason, 0) + 1

    def reject(self, reason: str) -> None:
        """Record a rejection and why. The counter name differs per module, so it is
        fixed at construction rather than passed at every call site."""
        if hasattr(self, self._reject_field):
            self.bump(self._reject_field)
        self.reject_reasons[reason] = self.reject_reasons.get(reason, 0) + 1

    def snapshot(self) -> dict:
        d = {f: getattr(self, f) for f in self.FIELDS}
        if self.reject_reasons:
            d["reject_reasons"] = ",".join(
                f"{k}:{v}" for k, v in sorted(self.reject_reasons.items()))
        return d

    def maybe_report(self, now: float | None = None) -> None:
        if not self._log.enabled():
            return
        now = now if now is not None else time.time()
        if now - self._last_report < self.report_interval:
            return
        self._last_report = now
        self._log.info(event="METRICS", camera=self.cam_id, **self.snapshot())

    def report_now(self, note: str = "") -> None:
        """Force a snapshot — the final summary at the end of a capture."""
        if not self._log.enabled():
            return
        self._log.info(event="METRICS", camera=self.cam_id, note=note or "final",
                       **self.snapshot())


class DiagnosticLog:
    """One module's diagnostic log. Cheap to construct; does no I/O until first use."""

    def __init__(self, name: str, env_prefix: str, filename: str):
        self.name = name
        self.env_prefix = env_prefix          # e.g. "PHONE_DEBUG"
        self.filename = filename              # e.g. "phone_debug.log"
        self._logger_name = f"vigil.{name}"
        self._lock = threading.Lock()
        self._logger: logging.Logger | None = None
        self._enabled: bool | None = None
        self._level: int | None = None
        self._sessions: set = set()

    # ── state ────────────────────────────────────────────────────────────────
    def reset(self) -> None:
        """Drop cached state and detach handlers. For tests and for re-reading env."""
        lg = logging.getLogger(self._logger_name)
        for h in list(lg.handlers):
            try:
                h.close()
            except Exception:
                pass
            lg.removeHandler(h)
        self._logger = None
        self._enabled = None
        self._level = None
        self._sessions = set()

    def enabled(self) -> bool:
        if self._enabled is None:
            raw = os.environ.get(self.env_prefix, "0").strip().upper()
            self._enabled = raw not in _FALSEY
        return self._enabled

    def level(self) -> int:
        """Resolved level, cached.

        The single variable carries the level: `PHONE_DEBUG=trace` is the whole switch.
        `=1` means INFO. `<PREFIX>_LEVEL` still overrides, for existing scripts."""
        if self._level is None:
            raw = (os.environ.get(f"{self.env_prefix}_LEVEL")
                   or os.environ.get(self.env_prefix, "")).strip().upper()
            if raw in _TRUTHY or not raw:
                raw = "INFO"
            self._level = (TRACE_LEVEL if raw == "TRACE"
                           else getattr(logging, raw, logging.INFO))
        return self._level

    def tracing(self) -> bool:
        """Cheap cached check callers use to skip building trace payloads in the hot
        loop. `log()` would discard the record anyway, but the caller still pays to
        construct the keyword dict — and per-observation tracing runs for every
        detection on every frame."""
        return self.enabled() and self.level() <= TRACE_LEVEL

    def verbose(self) -> bool:
        """True at DEBUG or TRACE — per-observation logging is wanted at all."""
        return self.enabled() and self.level() <= logging.DEBUG

    # ── destination ──────────────────────────────────────────────────────────
    def path(self) -> str:
        override = os.environ.get(f"{self.env_prefix}_LOG")
        if override:
            return override
        # settings.BASE_DIR comes from .env and is frequently left at the .env.example
        # placeholder on dev checkouts. Only trust it if it points somewhere real,
        # otherwise the log lands in a directory invented from a placeholder path.
        base = None
        try:
            from backend.core.config import settings
            if settings.BASE_DIR and os.path.isdir(settings.BASE_DIR):
                base = settings.BASE_DIR
        except Exception:
            base = None
        return os.path.join(base or repo_root(), "logs", self.filename)

    def _get_logger(self) -> logging.Logger | None:
        if not self.enabled():
            return None
        if self._logger is not None:
            return self._logger
        with self._lock:
            if self._logger is not None:
                return self._logger
            lg = logging.getLogger(self._logger_name)
            lg.propagate = False                 # never leak into the app log
            level = self.level()
            lg.setLevel(level)
            # Check for OUR handler, not for "any handler at all".
            #
            # `if not lg.handlers` looked equivalent and is not: anything else that
            # attaches a handler to this logger silently suppresses the file. pytest's
            # logging plugin does exactly that, which is why four tests in this repo
            # could only pass in isolation - run together, the capture handler arrived
            # first, this branch was skipped, and the log file was never created. The
            # same trap applies in production to any library that decides to instrument
            # our loggers, and the failure mode is a diagnostic file that quietly stops
            # being written just when somebody needs it.
            if not any(isinstance(h, RotatingFileHandler) for h in lg.handlers):
                path = self.path()
                try:
                    os.makedirs(os.path.dirname(path), exist_ok=True)
                    # TRACE fills a 5 MB file in minutes and the interesting part of a
                    # capture is usually the START, so the cap follows the level rather
                    # than making the caller remember to raise it.
                    default_mb = "50" if level <= TRACE_LEVEL else "5"
                    max_mb = float(os.environ.get(
                        f"{self.env_prefix}_MAX_MB", default_mb))
                    h = RotatingFileHandler(path, maxBytes=int(max_mb * 1024 * 1024),
                                            backupCount=3, encoding="utf-8")
                    h.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
                    lg.addHandler(h)
                    print(f"[{self.name.upper()}] diagnostic log -> {path} "
                          f"(level={logging.getLevelName(level)})", flush=True)
                except Exception as e:      # never let logging break the pipeline
                    print(f"[{self.name.upper()}] could not open diagnostic log "
                          f"{path}: {e}", flush=True)
                    lg.addHandler(logging.NullHandler())
            self._logger = lg
            return self._logger

    # ── emitting ─────────────────────────────────────────────────────────────
    def announce(self, where: str = "") -> bool:
        """Report the switch state, and OPEN the file if it is on.

        Called at start-up so the log exists from the moment the flag is set, instead of
        appearing lazily on the first write. Without this, a capture that produced no
        events looked identical to a flag that never arrived — no file, no message, no
        way to tell which. Returns True when logging is on."""
        tag = f"[{self.name.upper()}]"
        if not self.enabled():
            print(f"{tag} diagnostics OFF ({self.env_prefix} unset or 0)"
                  f"{' ' + where if where else ''}", flush=True)
            return False
        # Forces the handler open, which creates the file and prints its path.
        self._get_logger()
        self.info(event="STARTUP", component=where or "perception",
                  pid=os.getpid(), path=self.path())
        return True

    def log(self, level: int, /, **fields) -> None:
        """Emit one key=value line. Silent when disabled.

        `level` is positional-only: payloads are arbitrary key=value pairs and a field
        legitimately named `level` must not collide with the signature."""
        lg = self._get_logger()
        if lg is None or not lg.isEnabledFor(level):
            return
        try:
            lg.log(level, " ".join(f"{k}={fmt(v)}" for k, v in fields.items()))
        except Exception:
            pass

    def info(self, **fields) -> None:
        self.log(logging.INFO, **fields)

    def debug(self, **fields) -> None:
        self.log(logging.DEBUG, **fields)

    def trace(self, **fields) -> None:
        """Full per-observation record — TRACE only. Guard calls with tracing()."""
        self.log(TRACE_LEVEL, **fields)

    def session(self, key, config: dict, extra: dict | None = None,
                force: bool = False) -> None:
        """Write the SESSION header describing exactly how the module is configured.

        Emitted once per (key, CONFIG), so a mid-capture retune records a fresh header
        rather than leaving the rest of the log ambiguous. `extra` carries informational
        values that legitimately change all the time (running totals, for instance):
        they are logged but excluded from the session identity, otherwise the header
        would repeat constantly."""
        if not self.enabled():
            return
        fingerprint = (key, tuple(sorted((k, str(v)) for k, v in config.items())))
        if not force and fingerprint in self._sessions:
            return
        self._sessions.add(fingerprint)
        lg = self._get_logger()
        if lg is None:
            return
        lg.info("=" * 78)
        self.info(event="SESSION", camera=key, pid=os.getpid(),
                  level=logging.getLevelName(self.level()),
                  **config, **(extra or {}))
        lg.info("=" * 78)

    def metrics(self, cam_id, fields: tuple[str, ...], **kw) -> Metrics:
        return Metrics(self, cam_id, fields, **kw)


# ── reading a captured log back ───────────────────────────────────────────────
# The counterpart of `fmt` above. Kept here, next to the writer, so the two cannot
# drift; both the head-count and phone analysis tools use it.
import re as _re                                                        # noqa: E402
from datetime import datetime as _dt                                    # noqa: E402

_TS_RE = _re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3})\s+(.*)$")


def parse_value(raw: str):
    """Inverse of fmt() for one value: scalars, or nested (a,b) sequences."""
    if raw.startswith("(") and raw.endswith(")"):
        inner, depth, buf = [], 0, ""
        for ch in raw[1:-1]:
            if ch == "," and depth == 0:
                inner.append(buf)
                buf = ""
                continue
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
            buf += ch
        if buf:
            inner.append(buf)
        return [parse_value(v) for v in inner]
    try:
        return float(raw) if ("." in raw or "e" in raw.lower()) else int(raw)
    except ValueError:
        return raw


def parse_log(path):
    """Read a captured diagnostic log into [(timestamp, {field: value}), ...]."""
    records = []
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            m = _TS_RE.match(line.rstrip("\n"))
            if not m:
                continue                       # separator banners etc.
            ts_raw, body = m.groups()
            if "=" not in body:
                continue
            fields = {}
            for token in body.split(" "):
                if "=" not in token:
                    continue
                k, _, v = token.partition("=")
                fields[k] = parse_value(v)
            if "event" not in fields:
                continue
            records.append((_dt.strptime(ts_raw, "%Y-%m-%d %H:%M:%S,%f").timestamp(),
                            fields))
    return records
