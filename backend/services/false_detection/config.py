"""
False-detection configuration — every `fd_*` parameter, in one place.

Implements section 4 of `false-detection-plan.md`. Three layers, later beating earlier:

    1. the defaults below
    2. environment variables  (FD_ENABLED, FD_DEDUP_WINDOW, ...)
    3. stored configuration   (system_settings.false_detection, JSON)
    4. per-camera overrides   (camera_devices.module_config -> "_fd")

Nothing here overlaps an existing VIGIL parameter. The whole namespace is `fd_*` /
`FD_*`, verified against the Phase 1 `HC_*`, Phase 2 `PHONE_*`, the cadence intervals
and the nine registry `config` fields.
"""

from __future__ import annotations

import os
from dataclasses import asdict, dataclass, field, fields

# Ordered weakest -> strongest, so comparisons are index comparisons.
SEVERITY_ORDER = ("info", "low", "medium", "high", "critical")

# Starting map from the plan. Every entry is overridable, globally or per camera.
DEFAULT_SEVERITY_MAP = {
    # Kohat POC. Tampering is critical: someone disabling a camera is the most serious
    # thing on the capability list, and it is the one event that also invalidates every
    # other module on that camera.
    "tamper_blur": "critical",
    "tamper_moved": "critical",
    "tamper_covered": "critical",
    # A perimeter breach is not a footfall count, which is why `crossing` sits well above
    # head-count's own entry/exit.
    "crossing": "high",
    "gathering": "high",
    "no_stopping": "medium",
    "dispersed": "info",        # the situation resolving itself is context, not an alarm
    "fire": "critical",
    "smoke": "critical",
    "intrusion": "high",
    # An object left in a watched zone is a security concern, not housekeeping - the
    # whole point of the module is that somebody put it there and walked away.
    "unattended_object": "high",
    "smoking": "high",
    "unknown": "high",
    "phone_use": "medium",
    "over_capacity": "medium",
    "idle": "medium",
    # Counting, not alarming - same level as a person crossing.
    "vehicle_entry": "low",
    "vehicle_exit": "low",
    "entry": "low",
    "exit": "low",
    "checkin": "low",
    "checkout": "low",
    "recognized": "info",
    "occupancy": "info",
}

# What an unmapped event type gets. Deliberately not "info": a new event type that
# nobody has classified yet should still reach an operator, not be quietly demoted.
FALLBACK_SEVERITY = "medium"

MODES = ("observe", "enforce")
DEDUP_SCOPES = ("track", "camera", "global")
STORM_ACTIONS = ("demote", "suppress", "none")


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().upper() not in ("0", "", "FALSE", "NO", "OFF")


def _env_num(name: str, default: float) -> float:
    try:
        return float(os.environ[name])
    except (KeyError, TypeError, ValueError):
        return default


def _env_int(name: str, default: int) -> int:
    return int(_env_num(name, default))


def _env_str(name: str, default: str) -> str:
    return os.environ.get(name, default).strip()


@dataclass
class FDConfig:
    """The resolved configuration for one camera at one moment."""

    # ── 4.1 master switch ────────────────────────────────────────────────────
    fd_enabled: bool = False
    # observe = judge and log but never suppress. This is the safety property of the
    # whole design: the layer's effect is measured against real traffic before it is
    # allowed to remove anything.
    fd_mode: str = "observe"

    # ── 4.2 severity ─────────────────────────────────────────────────────────
    fd_severity_map: dict = field(default_factory=lambda: dict(DEFAULT_SEVERITY_MAP))
    fd_min_severity_alert: str = "low"
    fd_min_severity_snapshot: str = "medium"

    # ── 4.3 deduplication ────────────────────────────────────────────────────
    fd_dedup_window: float = 60.0
    fd_dedup_scope: str = "track"

    # ── 4.4 rate limiting and storm control ──────────────────────────────────
    fd_max_alerts_per_minute: int = 20
    fd_storm_window: float = 60.0
    fd_storm_threshold: int = 40
    fd_storm_action: str = "demote"

    # ── 4.5 evidence floors (zero = inert) ───────────────────────────────────
    fd_min_confidence: float = 0.0
    fd_min_evidence_frames: int = 0
    fd_require_snapshot: bool = False

    # ── 4.6 spatial and temporal ─────────────────────────────────────────────
    fd_ignore_zones: list = field(default_factory=list)
    fd_ignore_hours: list = field(default_factory=list)
    fd_warmup_seconds: float = 30.0

    # ── 4.7 repeated-source map ──────────────────────────────────────────────
    fd_repeat_source_enabled: bool = True
    fd_repeat_cell: float = 48.0
    fd_repeat_hits: int = 25
    fd_repeat_window: float = 120.0
    fd_repeat_min_tracks: int = 3
    fd_repeat_ttl: float = 900.0

    # ── derived helpers ──────────────────────────────────────────────────────
    @property
    def enforcing(self) -> bool:
        return self.fd_enabled and self.fd_mode == "enforce"

    def severity_for(self, event_type: str) -> tuple[str, str]:
        """(severity, source) — `source` records whether the map or the fallback
        decided, so the log can distinguish a classified event from an unclassified one."""
        mapped = (self.fd_severity_map or {}).get(event_type)
        if mapped in SEVERITY_ORDER:
            return mapped, "map"
        return FALLBACK_SEVERITY, "fallback"

    @staticmethod
    def at_least(severity: str, floor: str) -> bool:
        try:
            return SEVERITY_ORDER.index(severity) >= SEVERITY_ORDER.index(floor)
        except ValueError:
            return True          # unknown names never silently hide an event

    # ── construction ─────────────────────────────────────────────────────────
    @classmethod
    def from_env(cls) -> "FDConfig":
        d = cls()
        d.fd_enabled = _env_bool("FD_ENABLED", d.fd_enabled)
        d.fd_mode = _env_str("FD_MODE", d.fd_mode).lower()
        d.fd_min_severity_alert = _env_str("FD_MIN_SEVERITY_ALERT",
                                           d.fd_min_severity_alert).lower()
        d.fd_min_severity_snapshot = _env_str("FD_MIN_SEVERITY_SNAPSHOT",
                                              d.fd_min_severity_snapshot).lower()
        d.fd_dedup_window = _env_num("FD_DEDUP_WINDOW", d.fd_dedup_window)
        d.fd_dedup_scope = _env_str("FD_DEDUP_SCOPE", d.fd_dedup_scope).lower()
        d.fd_max_alerts_per_minute = _env_int("FD_MAX_ALERTS_PER_MINUTE",
                                              d.fd_max_alerts_per_minute)
        d.fd_storm_window = _env_num("FD_STORM_WINDOW", d.fd_storm_window)
        d.fd_storm_threshold = _env_int("FD_STORM_THRESHOLD", d.fd_storm_threshold)
        d.fd_storm_action = _env_str("FD_STORM_ACTION", d.fd_storm_action).lower()
        d.fd_min_confidence = _env_num("FD_MIN_CONFIDENCE", d.fd_min_confidence)
        d.fd_min_evidence_frames = _env_int("FD_MIN_EVIDENCE_FRAMES",
                                            d.fd_min_evidence_frames)
        d.fd_require_snapshot = _env_bool("FD_REQUIRE_SNAPSHOT", d.fd_require_snapshot)
        d.fd_warmup_seconds = _env_num("FD_WARMUP_SECONDS", d.fd_warmup_seconds)
        d.fd_repeat_source_enabled = _env_bool("FD_REPEAT_SOURCE_ENABLED",
                                               d.fd_repeat_source_enabled)
        d.fd_repeat_cell = _env_num("FD_REPEAT_CELL", d.fd_repeat_cell)
        d.fd_repeat_hits = _env_int("FD_REPEAT_HITS", d.fd_repeat_hits)
        d.fd_repeat_window = _env_num("FD_REPEAT_WINDOW", d.fd_repeat_window)
        d.fd_repeat_min_tracks = _env_int("FD_REPEAT_MIN_TRACKS", d.fd_repeat_min_tracks)
        d.fd_repeat_ttl = _env_num("FD_REPEAT_TTL", d.fd_repeat_ttl)
        return d.validated()

    def merged(self, overrides: dict | None) -> "FDConfig":
        """A copy with `overrides` applied. Unknown keys are ignored rather than raising,
        so a stored config written by a newer build cannot break an older one."""
        if not overrides:
            return self
        known = {f.name for f in fields(self)}
        data = asdict(self)
        for k, v in overrides.items():
            key = k if k.startswith("fd_") else f"fd_{k}"
            if key not in known or v is None:
                continue
            if key == "fd_severity_map" and isinstance(v, dict):
                # MERGE, do not replace. A stored config is a snapshot of the map as it
                # was on the day somebody pressed Save. Replacing wholesale means every
                # event type added afterwards is missing from it and silently takes
                # FALLBACK_SEVERITY instead of the default chosen for it.
                #
                # That is not hypothetical: it was caught by an end-to-end test writing
                # a real `tamper_covered` row, which came out `medium` on a database
                # holding a config saved before the tamper module existed - when the
                # whole point of that event type is that it is CRITICAL. Under-
                # classifying a camera being blinded is exactly the failure the severity
                # map exists to prevent.
                #
                # Merging keeps operator intent (their entries win) while letting new
                # event types arrive with their intended default.
                merged_map = dict(data[key])
                merged_map.update(v)
                data[key] = merged_map
            else:
                data[key] = v
        return FDConfig(**data).validated()

    def validated(self) -> "FDConfig":
        """Clamp to sane ranges. A bad stored value must degrade to a safe default, not
        take the writer down — this runs inside the single DB writer thread."""
        if self.fd_mode not in MODES:
            self.fd_mode = "observe"
        if self.fd_dedup_scope not in DEDUP_SCOPES:
            self.fd_dedup_scope = "track"
        if self.fd_storm_action not in STORM_ACTIONS:
            self.fd_storm_action = "demote"
        if self.fd_min_severity_alert not in SEVERITY_ORDER:
            self.fd_min_severity_alert = "low"
        if self.fd_min_severity_snapshot not in SEVERITY_ORDER:
            self.fd_min_severity_snapshot = "medium"
        self.fd_dedup_window = max(0.0, float(self.fd_dedup_window))
        self.fd_storm_window = max(1.0, float(self.fd_storm_window))
        self.fd_max_alerts_per_minute = max(0, int(self.fd_max_alerts_per_minute))
        self.fd_storm_threshold = max(1, int(self.fd_storm_threshold))
        self.fd_min_confidence = min(1.0, max(0.0, float(self.fd_min_confidence)))
        self.fd_min_evidence_frames = max(0, int(self.fd_min_evidence_frames))
        self.fd_warmup_seconds = max(0.0, float(self.fd_warmup_seconds))
        self.fd_repeat_cell = max(1.0, float(self.fd_repeat_cell))
        self.fd_repeat_hits = max(1, int(self.fd_repeat_hits))
        self.fd_repeat_window = max(0.0, float(self.fd_repeat_window))
        self.fd_repeat_min_tracks = max(1, int(self.fd_repeat_min_tracks))
        self.fd_repeat_ttl = max(1.0, float(self.fd_repeat_ttl))
        if not isinstance(self.fd_ignore_zones, list):
            self.fd_ignore_zones = []
        if not isinstance(self.fd_ignore_hours, list):
            self.fd_ignore_hours = []
        if not isinstance(self.fd_severity_map, dict):
            self.fd_severity_map = dict(DEFAULT_SEVERITY_MAP)
        return self

    def to_dict(self) -> dict:
        return asdict(self)

    def log_fields(self) -> dict:
        """The subset written into every SESSION header. Collections are summarised by
        count — the values themselves are large and are already in the stored config."""
        return {
            "fd_enabled": self.fd_enabled,
            "mode": self.fd_mode,
            "fd_min_severity_alert": self.fd_min_severity_alert,
            "fd_min_severity_snapshot": self.fd_min_severity_snapshot,
            "fd_dedup_window": self.fd_dedup_window,
            "fd_dedup_scope": self.fd_dedup_scope,
            "fd_max_alerts_per_minute": self.fd_max_alerts_per_minute,
            "fd_storm_window": self.fd_storm_window,
            "fd_storm_threshold": self.fd_storm_threshold,
            "fd_storm_action": self.fd_storm_action,
            "fd_min_confidence": self.fd_min_confidence,
            "fd_min_evidence_frames": self.fd_min_evidence_frames,
            "fd_require_snapshot": self.fd_require_snapshot,
            "fd_ignore_zones": len(self.fd_ignore_zones),
            "fd_ignore_hours": len(self.fd_ignore_hours),
            "fd_warmup_seconds": self.fd_warmup_seconds,
            "fd_repeat_source_enabled": self.fd_repeat_source_enabled,
            "fd_repeat_cell": self.fd_repeat_cell,
            "fd_repeat_hits": self.fd_repeat_hits,
            "fd_repeat_window": self.fd_repeat_window,
            "fd_repeat_min_tracks": self.fd_repeat_min_tracks,
            "fd_repeat_ttl": self.fd_repeat_ttl,
        }
