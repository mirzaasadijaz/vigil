"""
Per-rule diagnostic logging for the false-detection layer.

Implements section 4A of `false-detection-plan.md`. Self-contained by design: this
branch has none of the Phase 1/2 diagnostic machinery, and the layer must not acquire a
dependency on work that lives on another branch.

The governing rule, and the reason this exists at all:

    EVERY LINE CARRIES THE PARAMETER VALUES THAT PRODUCED THE DECISION.

A log that records "suppressed" without recording the threshold in force cannot be
interpreted later, or on another machine, or by anyone who was not watching at the time.
That exact omission let a confidence-threshold bug survive two field captures in the
phone module: "working correctly" and "silently misconfigured" produced identical logs.

Enable with ONE variable:

    FD_DEBUG=1        decisions only  - SUPPRESS/DEMOTE, config changes, metrics
    FD_DEBUG=debug    + every ACCEPT with the list of rules that examined it
    FD_DEBUG=trace    + per-rule intermediate values
    unset / =0        off, the default

Optional:
    FD_LOG_DIR=logs/false_detection/   where the files go
    FD_LOG_MAX_MB=50                   rotation size per file
    FD_LOG_BACKUPS=5                   rotated files kept
    FD_LOG_SPLIT=false                 one combined file instead of ten
"""

from __future__ import annotations

import logging
import os
import threading
from logging.handlers import RotatingFileHandler

TRACE_LEVEL = 5
logging.addLevelName(TRACE_LEVEL, "TRACE")

_TRUTHY = ("1", "TRUE", "ON", "YES")
_FALSEY = ("0", "", "FALSE", "NO", "OFF")

# One file per rule. `decisions` is the master ordered trail; the rest isolate a single
# rule so "is dedup too aggressive?" is one file rather than a grep through a mixed
# stream. Splitting loses nothing because every judgement also lands in `decisions`.
RULE_FILES = (
    "decisions",
    "severity",
    "dedup",
    "ratelimit",
    "evidence",
    "zones",
    "warmup",
    "repeat",
    "metrics",
    "config",
)


def repo_root() -> str:
    """backend/services/false_detection/ -> repo root."""
    here = os.path.abspath(__file__)
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(here))))


def fmt(value) -> str:
    """Render one field value.

    Floats are fixed-width so columns stay scannable; sequences render without spaces so
    a line always splits cleanly on whitespace. `None` becomes `-` rather than vanishing,
    because an absent value is itself information."""
    if value is None:
        return "-"
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, float):
        return f"{value:.4f}"
    if isinstance(value, dict):
        return ",".join(f"{k}:{fmt(v)}" for k, v in sorted(value.items())) or "-"
    if isinstance(value, (tuple, list)):
        return "(" + ",".join(fmt(v) for v in value) + ")"
    text = str(value)
    return text.replace(" ", "_") if " " in text else text


class FDLogger:
    """Owns the whole `logs/false_detection/` directory. Cheap to construct; no I/O
    until `announce()` or the first write."""

    def __init__(self):
        self._lock = threading.Lock()
        self._loggers: dict[str, logging.Logger] = {}
        self._enabled: bool | None = None
        self._level: int | None = None
        self._split: bool | None = None
        self._announced = False

    # ── state ────────────────────────────────────────────────────────────────
    def reset(self) -> None:
        """Drop cached state and detach OUR handlers. Handlers belonging to anything
        else on the same logger are left alone."""
        for lg in self._loggers.values():
            for h in [h for h in lg.handlers if getattr(h, "_vigil_fd", False)]:
                try:
                    h.close()
                except Exception:
                    pass
                lg.removeHandler(h)
        self._loggers.clear()
        self._enabled = None
        self._level = None
        self._split = None
        self._announced = False

    def enabled(self) -> bool:
        if self._enabled is None:
            raw = os.environ.get("FD_DEBUG", "0").strip().upper()
            self._enabled = raw not in _FALSEY
        return self._enabled

    def level(self) -> int:
        if self._level is None:
            raw = os.environ.get("FD_DEBUG", "").strip().upper()
            if raw in _TRUTHY or not raw:
                raw = "INFO"
            self._level = (TRACE_LEVEL if raw == "TRACE"
                           else getattr(logging, raw, logging.INFO))
        return self._level

    def split(self) -> bool:
        if self._split is None:
            raw = os.environ.get("FD_LOG_SPLIT", "1").strip().upper()
            self._split = raw not in _FALSEY
        return self._split

    def verbose(self) -> bool:
        """True at DEBUG or TRACE — ACCEPT lines are wanted."""
        return self.enabled() and self.level() <= logging.DEBUG

    def tracing(self) -> bool:
        """Cheap cached check so callers skip building trace payloads in the hot path."""
        return self.enabled() and self.level() <= TRACE_LEVEL

    # ── destination ──────────────────────────────────────────────────────────
    def directory(self) -> str:
        override = os.environ.get("FD_LOG_DIR")
        if override:
            return override
        # settings.BASE_DIR comes from .env and is frequently left at the .env.example
        # placeholder on dev checkouts. Only trust it if it points somewhere real.
        base = None
        try:
            from backend.core.config import settings
            if settings.BASE_DIR and os.path.isdir(settings.BASE_DIR):
                base = settings.BASE_DIR
        except Exception:
            base = None
        return os.path.join(base or repo_root(), "logs", "false_detection")

    def path(self, rule: str) -> str:
        name = "fd_all.log" if not self.split() else f"fd_{rule}.log"
        return os.path.join(self.directory(), name)

    def _get(self, rule: str) -> logging.Logger | None:
        if not self.enabled():
            return None
        key = "all" if not self.split() else rule
        lg = self._loggers.get(key)
        if lg is not None:
            return lg
        with self._lock:
            if key in self._loggers:
                return self._loggers[key]
            lg = logging.getLogger(f"vigil.fd.{key}")
            lg.propagate = False              # never leak into the application log
            lg.setLevel(self.level())
            # Check for OUR handler specifically, not "any handler at all". Something
            # else may legitimately attach one to this logger — pytest's capture plugin
            # does, and so would a monitoring agent — and a naive `if not lg.handlers`
            # would then silently skip creating the file, losing the whole capture while
            # appearing to work.
            mine = [h for h in lg.handlers if getattr(h, "_vigil_fd", False)]
            if not mine:
                target = self.path(rule)
                try:
                    os.makedirs(os.path.dirname(target), exist_ok=True)
                    max_mb = float(os.environ.get("FD_LOG_MAX_MB", "50"))
                    backups = int(os.environ.get("FD_LOG_BACKUPS", "5"))
                    h = RotatingFileHandler(target, maxBytes=int(max_mb * 1024 * 1024),
                                            backupCount=backups, encoding="utf-8")
                    h.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
                    h._vigil_fd = True
                    lg.addHandler(h)
                except Exception as e:        # logging must never break the writer
                    print(f"[FALSE-DETECTION] cannot open {target}: {e}", flush=True)
                    null = logging.NullHandler()
                    null._vigil_fd = True
                    lg.addHandler(null)
            self._loggers[key] = lg
            return lg

    # ── emitting ─────────────────────────────────────────────────────────────
    def write(self, rule: str, level: int, /, **fields) -> None:
        """One key=value line into `rule`'s file. Silent when disabled."""
        lg = self._get(rule)
        if lg is None or not lg.isEnabledFor(level):
            return
        try:
            line = " ".join(f"{k}={fmt(v)}" for k, v in fields.items())
            # With split off everything shares a file, so the rule has to be on the line.
            if not self.split():
                line = f"log={rule} " + line
            lg.log(level, line)
        except Exception:
            pass

    # `rule` is positional-only on all four. Diagnostic payloads are arbitrary key=value
    # pairs, and a field legitimately named `rule` (every suppression carries one) must
    # not collide with the signature.
    def info(self, rule: str, /, **fields) -> None:
        self.write(rule, logging.INFO, **fields)

    def debug(self, rule: str, /, **fields) -> None:
        self.write(rule, logging.DEBUG, **fields)

    def trace(self, rule: str, /, **fields) -> None:
        self.write(rule, TRACE_LEVEL, **fields)

    # ── start-up ─────────────────────────────────────────────────────────────
    def announce(self, config_fields: dict, /,
                 component: str = "false_detection") -> bool:
        """Report the switch state, create every file, and write the SESSION header.

        Called at start-up so the logs exist from the moment the flag is set rather than
        appearing lazily on the first suppression. Without this, "the layer suppressed
        nothing" and "the flag never arrived" are the same empty directory."""
        if not self.enabled():
            print("[FALSE-DETECTION] diagnostics OFF (FD_DEBUG unset or 0)", flush=True)
            return False
        level_name = logging.getLevelName(self.level())
        print(f"[FALSE-DETECTION] diagnostics -> {self.directory()} "
              f"(level={level_name}, split={self.split()})", flush=True)
        header = dict(event="SESSION", component=component, pid=os.getpid(),
                      level=level_name, **config_fields)
        for rule in RULE_FILES:
            lg = self._get(rule)
            if lg is not None:
                lg.info("=" * 78)
                self.info(rule, **header)
                lg.info("=" * 78)
            if not self.split():
                break                       # one file: one header
        self._announced = True
        return True


LOG = FDLogger()
