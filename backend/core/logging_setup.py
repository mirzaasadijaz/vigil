"""
Persistent run logs, and a record of what the run was configured with.

WHY THIS EXISTS
---------------
Everything this application reports goes through `print(..., flush=True)` with a [TAG]
prefix, straight to stdout. That is fine while somebody is watching a terminal and
useless afterwards. Every fault diagnosed in this codebase so far - a swallowed
`NameError` in the fire_smoke loop, an attendance punch failing on a missing import, a
mapper that took every other mapper down with it - announced itself in a print that had
already scrolled away by the time anyone looked.

The analytics `DiagnosticLog` machinery (services/analytics/diagnostics.py) does write
files, and writes them well, but each one is inert unless its own `*_DEBUG` variable is
set. In a default deployment every one of them is off, which is why the `logs/`
directory they target never even gets created. This module is the always-on layer
underneath them; it does not replace them and does not touch their files.

It provides two things:

  init_run_log(name)
      Tees stdout and stderr into logs/<name>.log. Every existing print is persisted
      and timestamped with no call-site changes, and the console still shows exactly
      what it showed before.

  log_parameters(name)
      Writes a PARAMETERS block recording every tunable this process resolved. A log
      you cannot interpret is not evidence: a miss caused by a bad threshold looks
      identical to a miss caused by the environment variable that set it, and only one
      of those is a code problem. Parameters are DISCOVERED by introspection, not
      listed here, so a tunable added next year is recorded without anyone remembering
      to update this file.

SECRETS ARE NEVER WRITTEN
    Any parameter whose NAME looks like a credential (KEY, SECRET, TOKEN, PASSWORD,
    PWD, CRED) is written as *** , and any value containing a URL has its
    user:password@ stripped. That covers ATTENDANCE_KEY, JWT_SECRET and DATABASE_URL.
    The redaction is by name and by shape, not by a list of known secrets, so a
    credential added later is redacted by default rather than leaked by omission.

ONE FILE PER PROCESS
    RotatingFileHandler is not safe across processes - two of them rotating the same
    file interleave and truncate. This application is deliberately two processes (see
    CLAUDE.md), so the main process and the perception child each get their own file:
    logs/backend.log and logs/perception.log. Do not point them at one path.

OFF SWITCH
    RUN_LOG=0 disables the tee entirely (the console is untouched either way).
    RUN_LOG_MAX_MB sets the per-file cap, default 20, keeping 3 rotations.
"""
from __future__ import annotations

import logging
import os
import re
import sys
import threading
from logging.handlers import RotatingFileHandler

_TRUTHY_OFF = ("0", "false", "no", "off")

# Substrings that make a parameter NAME a credential. Matched case-insensitively
# against the whole name, so ATTENDANCE_KEY, JWT_SECRET and API_TOKEN are all caught.
_SECRET_HINTS = ("key", "secret", "token", "password", "passwd", "pwd", "cred")

_lock = threading.Lock()
_installed: dict[str, "_Tee"] = {}


def logs_dir() -> str:
    """<repo root>/logs - the same directory the analytics diagnostics already use."""
    base = None
    try:
        from backend.core.config import settings
        if settings.BASE_DIR and os.path.isdir(settings.BASE_DIR):
            base = settings.BASE_DIR
    except Exception:
        base = None
    if not base:
        base = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    return os.path.join(base, "logs")


def redact(name: str, value) -> str:
    """Render a parameter value, never revealing a credential.

    Redacts on the NAME first (a secret is a secret whatever it looks like), then on
    the SHAPE of the value (a connection string carries its password inline, and its
    name - DATABASE_URL - contains none of the hint words)."""
    lowered = name.lower()
    if isinstance(value, bool) or isinstance(value, (int, float)):
        # A number is a threshold or a duration, not a credential - ACCESS_TOKEN_-
        # EXPIRE_MINUTES contains "token" and is worth seeing. Only redact a numeric
        # value when the name ENDS with a hint, which a real key would.
        if lowered.rstrip("_").endswith(_SECRET_HINTS):
            return "***"
        return str(value)
    if any(h in lowered for h in _SECRET_HINTS):
        return "***"
    text = str(value)
    if "://" in text:
        text = re.sub(r"://([^:/@\s]+):[^@\s]*@", r"://\1:***@", text)
    return text


class _Tee:
    """Write to the original stream AND to a rotating log file, line by line.

    Lines are buffered until a newline so the file gets one timestamped record per
    logical line rather than per write() call - `print` alone issues two.
    """

    def __init__(self, stream, logger: logging.Logger, level: int):
        self._stream = stream
        self._logger = logger
        self._level = level
        self._buf = ""
        self._buf_lock = threading.Lock()

    def write(self, text):
        # The console must see byte-for-byte what it saw before this module existed.
        try:
            self._stream.write(text)
        except Exception:
            pass
        try:
            with self._buf_lock:
                self._buf += text
                while "\n" in self._buf:
                    line, self._buf = self._buf.split("\n", 1)
                    if line.strip():
                        line = line.rstrip()
                        self._logger.log(self._level, line)
                        _route_tagged_line(line)
        except Exception:
            # Logging must never be the reason the application stops working.
            pass
        return len(text)

    def flush(self):
        try:
            self._stream.flush()
        except Exception:
            pass

    def __getattr__(self, item):
        # isatty / fileno / encoding and friends: defer to the real stream, or uvicorn
        # and anything else probing the terminal gets the wrong answer.
        return getattr(self._stream, item)


def init_run_log(name: str) -> str | None:
    """Tee this process's stdout/stderr into logs/<name>.log. Returns the path.

    Idempotent per name: calling it twice does not stack two tees on one stream.
    """
    if os.environ.get("RUN_LOG", "1").lower() in _TRUTHY_OFF:
        return None
    with _lock:
        if name in _installed:
            return os.path.join(logs_dir(), f"{name}.log")
        directory = logs_dir()
        path = os.path.join(directory, f"{name}.log")
        try:
            os.makedirs(directory, exist_ok=True)
            logger = logging.getLogger(f"vigil.run.{name}")
            logger.propagate = False          # never loop back into the tee
            logger.setLevel(logging.INFO)
            # Check for OUR handler specifically - the same trap diagnostics.py
            # documents: anything else attaching a handler would otherwise silence us.
            if not any(isinstance(h, RotatingFileHandler) for h in logger.handlers):
                max_mb = float(os.environ.get("RUN_LOG_MAX_MB", "20"))
                handler = RotatingFileHandler(
                    path, maxBytes=int(max_mb * 1024 * 1024), backupCount=3,
                    encoding="utf-8")
                handler.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
                logger.addHandler(handler)
            out = _Tee(sys.stdout, logger, logging.INFO)
            err = _Tee(sys.stderr, logger, logging.ERROR)
            sys.stdout, sys.stderr = out, err
            _installed[name] = out
            print(f"[LOG] run log -> {path} (RUN_LOG=0 disables, "
                  f"RUN_LOG_MAX_MB={max_mb:g})", flush=True)
            return path
        except Exception as e:
            # A log that cannot be opened is a nuisance; a boot that fails because of
            # it is an outage. Say so on the console and carry on unlogged.
            print(f"[LOG] could not open run log {path}: {e}", flush=True)
            return None


# ── per-module logs ──────────────────────────────────────────────────────────────
#
# One file per module, named exactly for the module id in registry.py:
# logs/truck_count.log, logs/attendance.log, logs/headcount.log, and so on. When a
# single module misbehaves you read that module's file instead of grepping a combined
# stream for a tag - which is the whole point, because the interesting modules are the
# quiet ones and a quiet module is invisible in a busy log.
#
# WHY APPEND-ONLY AND NOT RotatingFileHandler
#     Both processes write here: the perception child produces the events and the
#     errors, the main process persists them, and a module's story is incomplete
#     without both halves. Rotation is a rename-then-reopen, and two processes doing
#     that to one path interleave and lose records - the failure the run logs avoid by
#     having a file each, which is not an option when the file IS the module.
#     Appends of whole lines are safe concurrently (O_APPEND); renames are not. So
#     these handlers never rotate mid-run, and the size cap is applied ONCE at boot by
#     roll_module_logs(), from the main process, before the child exists.
#
# These files name cameras and recognised employees. logs/ is gitignored for exactly
# that reason - see the note on the rule in .gitignore.
#
# MODULE_LOGS=0 disables them. MODULE_LOG_MAX_MB caps each file at boot (default 20).

_module_loggers: dict[str, logging.Logger] = {}
_proc_tag = "main"

# Existing code already prefixes its diagnostics with a [TAG]. Routing those tags to the
# matching module file means a module's free-text story - "[ATTENDANCE] Skipped inactive
# employee", "[MESS] bill opened" - lands in its own file without touching the hundreds
# of print sites that produce it, and keeps doing so for prints added later.
#
# [VEHICLE] is deliberately ABSENT. vehicle.py now serves both vehicle_count and
# truck_count, so its lines cannot be attributed to one of them; filing them under a
# guess would be worse than leaving them in perception.log, where they still are.
# [PERCEPTION] is absent for the same reason - it is the lane, not a module - and its
# per-module errors are routed explicitly at the handler instead.
_TAG_TO_MODULE = {
    "ATTENDANCE": "attendance",
    "MESS": "mess",
    "HEADCOUNT": "headcount",
    "TRIPWIRE": "tripwire",
    "CROWD": "crowd",
    "INTRUSION": "intrusion",
    "TAMPER": "tamper",
    "UNATTENDED": "unattended",
    "PHONE": "mobile",             # the module id is `mobile`; the detector is phone.py
    "FIRE": "fire_smoke",
    "SMOKING": "smoking",
}
_TAG_RE = re.compile(r"^\s*\[([A-Z][A-Z0-9_-]*)\]\s*(.*)$")


def set_process_tag(tag: str) -> None:
    """Label this process's lines so a module file reads as one story across both."""
    global _proc_tag
    _proc_tag = tag


def module_logs_enabled() -> bool:
    return os.environ.get("MODULE_LOGS", "1").lower() not in _TRUTHY_OFF


def module_log_path(module_id: str) -> str:
    return os.path.join(logs_dir(), f"{module_id}.log")


def roll_module_logs(max_mb: float | None = None) -> None:
    """Apply the size cap once, at boot, from the main process only.

    Called before the perception child is spawned, so no other process can be holding
    one of these files open mid-rename.
    """
    if not module_logs_enabled():
        return
    cap = (max_mb if max_mb is not None
           else float(os.environ.get("MODULE_LOG_MAX_MB", "20"))) * 1024 * 1024
    directory = logs_dir()
    try:
        os.makedirs(directory, exist_ok=True)
        for fname in os.listdir(directory):
            if not fname.endswith(".log"):
                continue
            path = os.path.join(directory, fname)
            try:
                if os.path.getsize(path) <= cap:
                    continue
                for i in (3, 2, 1):
                    older, newer = f"{path}.{i}", (f"{path}.{i-1}" if i > 1 else path)
                    if os.path.exists(newer):
                        if os.path.exists(older):
                            os.remove(older)
                        os.replace(newer, older)
            except Exception:
                continue
    except Exception:
        pass


def prepare_module_logs() -> list[str]:
    """Create an empty log for EVERY module in the registry, at boot.

    Without this a module that never fires has no file at all, and "there is no
    truck_count.log" reads as "logging is broken" when it actually means "this module
    produced nothing" - which is the single most useful thing a troubleshooter can
    learn, and the one they currently have to infer from an absence.
    """
    if not module_logs_enabled():
        return []
    created: list[str] = []
    try:
        from backend.services.modules.registry import MODULES   # pure data, no imports
        os.makedirs(logs_dir(), exist_ok=True)
        for module_id in sorted(MODULES):
            path = module_log_path(module_id)
            if not os.path.exists(path):
                try:
                    with open(path, "a", encoding="utf-8"):
                        pass
                    created.append(module_id)
                except Exception:
                    continue
    except Exception as e:
        print(f"[LOG] could not pre-create module logs: {e}", flush=True)
        return created
    print(f"[LOG] module logs -> {logs_dir()} "
          f"({len(created)} created, one file per module; MODULE_LOGS=0 disables)",
          flush=True)
    return created


def _module_logger(module_id: str) -> logging.Logger | None:
    if not module_logs_enabled():
        return None
    lg = _module_loggers.get(module_id)
    if lg is not None:
        return lg
    with _lock:
        lg = _module_loggers.get(module_id)
        if lg is not None:
            return lg
        try:
            os.makedirs(logs_dir(), exist_ok=True)
            lg = logging.getLogger(f"vigil.module.{module_id}")
            lg.propagate = False           # never echo into the run log or the console
            lg.setLevel(logging.INFO)
            if not any(isinstance(h, logging.FileHandler) for h in lg.handlers):
                h = logging.FileHandler(module_log_path(module_id), mode="a",
                                        encoding="utf-8", delay=True)
                h.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
                lg.addHandler(h)
            _module_loggers[module_id] = lg
            return lg
        except Exception:
            _module_loggers[module_id] = logging.getLogger(f"vigil.module.{module_id}")
            _module_loggers[module_id].addHandler(logging.NullHandler())
            return None


def log_module(module_id: str, message: str, cam_id=None) -> None:
    """One line in this module's own file. Never raises."""
    try:
        lg = _module_logger(module_id)
        if lg is None:
            return
        where = f"cam={cam_id} " if cam_id is not None else ""
        lg.info(f"[{_proc_tag}] {where}{message}")
    except Exception:
        pass


def log_module_config(module_id: str, cam_id, config) -> None:
    """The resolved per-camera config, recorded when the consumer is built.

    This is the line that makes the rest of the file interpretable: a module that never
    fires because its line is in the wrong place looks exactly like one that never
    fires because of a threshold, and only this tells them apart.
    """
    try:
        if not isinstance(config, dict):
            log_module(module_id, f"CONFIG {config}", cam_id)
            return
        rendered = " ".join(f"{k}={redact(k, v)}" for k, v in sorted(config.items())
                            if k != "module")
        log_module(module_id, f"CONFIG {rendered}", cam_id)
    except Exception:
        pass


def _route_tagged_line(line: str) -> None:
    """Copy a `[TAG] ...` console line into the matching module's file.

    Safe against recursion: module loggers have propagate=False and only a FileHandler,
    so nothing written here comes back through the tee.
    """
    try:
        if not module_logs_enabled():
            return
        m = _TAG_RE.match(line)
        if not m:
            return
        module_id = _TAG_TO_MODULE.get(m.group(1))
        if module_id:
            log_module(module_id, m.group(2))
    except Exception:
        pass


def log_events(events, cam_id=None) -> None:
    """Fan a batch of events out to one file per `module`.

    Called at the points where events leave the producer, so every module's events are
    captured by three hooks rather than one per module path.
    """
    try:
        if not events or not module_logs_enabled():
            return
        for e in events:
            if not isinstance(e, dict):
                continue
            module_id = e.get("module")
            if not module_id:
                continue
            parts = [f"EVENT type={e.get('event_type')}"]
            if e.get("value") is not None:
                parts.append(f"value={e['value']}")
            if e.get("label"):
                parts.append(f"label={e['label']}")
            if e.get("photo_base"):
                parts.append(f"identity={e['photo_base']}")
            if e.get("data") is not None:
                parts.append(f"data={e['data']}")
            log_module(module_id, " ".join(parts), e.get("camera_id", cam_id))
    except Exception:
        pass


def _collect(obj, names=None) -> list[tuple[str, str]]:
    """UPPER_CASE scalars off a module, or the declared fields off a settings object."""
    rows: list[tuple[str, str]] = []
    for attr in (names if names is not None else dir(obj)):
        if attr.startswith("_") or not attr.isupper():
            continue
        try:
            value = getattr(obj, attr)
        except Exception:
            continue
        if callable(value) or isinstance(value, type):
            continue
        if not isinstance(value, (str, int, float, bool, type(None), tuple, list, set, frozenset)):
            # ZoneInfo, Path and friends render usefully; modules and clients do not.
            if type(value).__module__ == "builtins":
                continue
            value = str(value)
        rows.append((attr, redact(attr, value)))
    return sorted(rows)


def log_parameters(name: str, extra: dict | None = None) -> None:
    """Record every tunable this process resolved, as one PARAMETERS block.

    Discovered, not enumerated - whatever UPPER_CASE settings exist at call time are
    reported, so this keeps telling the truth as the codebase changes.
    """
    try:
        import platform
        sections: list[tuple[str, list[tuple[str, str]]]] = []

        runtime = [
            ("process", name),
            ("pid", str(os.getpid())),
            ("python", platform.python_version()),
            ("platform", platform.platform()),
            ("cwd", os.getcwd()),
        ]
        sections.append(("runtime", runtime))

        try:
            from backend.core.config import settings as _s
            fields = list(getattr(_s, "model_fields", {}) or {})
            sections.append(("config", _collect(_s, names=[f for f in fields if f.isupper()])))
        except Exception as e:
            sections.append(("config", [("<unavailable>", str(e))]))

        # perception holds the detection thresholds. Only report it if it is ALREADY
        # imported - importing it here would drag torch into a process that must not
        # have it (see the three-tier note in CLAUDE.md).
        mod = sys.modules.get("backend.services.perception")
        if mod is not None:
            sections.append(("perception", _collect(mod)))

        if extra:
            sections.append(("extra", sorted((k, redact(k, v)) for k, v in extra.items())))

        width = max((len(k) for _, rows in sections for k, _ in rows), default=0)
        print(f"[PARAMS] ===== resolved parameters for '{name}' =====", flush=True)
        for title, rows in sections:
            if not rows:
                continue
            print(f"[PARAMS] -- {title} --", flush=True)
            for key, value in rows:
                print(f"[PARAMS]   {key.ljust(width)} = {value}", flush=True)
        print(f"[PARAMS] ===== end parameters ({sum(len(r) for _, r in sections)} "
              f"values, credentials shown as ***) =====", flush=True)
    except Exception as e:
        print(f"[PARAMS] parameter dump failed: {e}", flush=True)
