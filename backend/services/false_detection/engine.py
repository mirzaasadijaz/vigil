"""
The false-detection judgement engine.

One stage, in the perception WRITER, that every module's events pass through before they
become alerts. It never sees pixels and it never changes what a module computes — it can
only annotate an event with a severity, or decline to write it.

    module consumers -> mp.Queue -> PerceptionWriter -> judge() -> alert_logs

Design constraints, all load-bearing:

  * **O(1) per event.** The writer is a single synchronous thread and already a suspected
    throughput bound. Every rule is a dict or deque lookup; no rule may query the DB.
  * **Bounded memory.** Every map is keyed per camera and expires by TTL.
  * **Observe mode never suppresses.** It computes the same verdict and logs it, then
    returns ACCEPT. This is what lets the layer's effect be measured against real traffic
    before it is trusted to remove anything.
  * **Every decision is logged with the parameters that produced it.**

Rule order matters and is deliberate:

    severity  -> always computed; other rules and the log need it
    warmup    -> cheapest, and start-up noise should not reach the later maps
    zones     -> spatial, before anything stateful
    hours     -> demote-only
    evidence  -> per-event, stateless
    repeat    -> stateful map
    dedup     -> stateful
    ratelimit -> last, because it counts what actually got through
"""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field

from backend.services.false_detection.config import FDConfig
from backend.services.false_detection.logging import LOG

ACCEPT = "ACCEPT"
SUPPRESS = "SUPPRESS"
DEMOTE = "DEMOTE"

# The order they run in, and the order reported in `checks_passed`.
RULES = ("severity", "warmup", "zones", "hours", "evidence", "repeat",
         "dedup", "ratelimit")


@dataclass
class Judgement:
    """What the writer should do with one event."""

    verdict: str = ACCEPT
    rule: str | None = None
    reason: str | None = None
    severity: str = "medium"
    severity_source: str = "fallback"
    alert: bool = True          # surfaces in the alert feed
    snapshot: bool = True       # a snapshot may be saved
    checks_passed: list = field(default_factory=list)
    would_suppress: bool = False   # observe mode: what enforce WOULD have done
    # False while the layer is disabled, so the writer stores NULL and rows stay
    # byte-identical to what they were before this package existed.
    severity_recorded: bool = False

    @property
    def write(self) -> bool:
        return self.verdict != SUPPRESS


def _point_in_poly(px: float, py: float, poly) -> bool:
    """Ray casting. poly = [[x, y], ...] normalised 0-1, matching every other piece of
    geometry in VIGIL."""
    if not poly or len(poly) < 3:
        return False
    inside = False
    n = len(poly)
    j = n - 1
    for i in range(n):
        xi, yi = poly[i][0], poly[i][1]
        xj, yj = poly[j][0], poly[j][1]
        if ((yi > py) != (yj > py)) and \
                (px < (xj - xi) * (py - yi) / ((yj - yi) or 1e-9) + xi):
            inside = not inside
        j = i
    return inside


def _within(start: str, end: str, hhmm: str) -> bool:
    """Is `hhmm` inside [start, end]? Handles windows that wrap midnight."""
    try:
        s, e, n = (int(v.split(":")[0]) * 60 + int(v.split(":")[1])
                   for v in (start, end, hhmm))
    except (ValueError, IndexError, AttributeError):
        return False
    return (s <= n <= e) if s <= e else (n >= s or n <= e)


class _CameraState:
    """Per-camera memory. Everything here is bounded and expires."""

    __slots__ = ("started_at", "dedup", "recent", "repeat")

    def __init__(self, now: float):
        self.started_at = now
        self.dedup: dict = {}         # key -> [first_seen, last_seen, suppressed]
        self.recent: deque = deque()  # timestamps of alerts that got through
        self.repeat: dict = {}        # cell -> [hits, first, last, {tracks}]


class FalseDetectionFilter:
    """Judges events. One instance for the process; state is partitioned per camera."""

    def __init__(self, config: FDConfig | None = None):
        self.config = config or FDConfig()
        self._cameras: dict = {}
        self._overrides: dict = {}     # camera_id -> per-camera override dict
        self.counters: dict = {}       # camera_id -> {metric: n}
        self._last_metrics = time.time()

    # ── clock, injectable so tests are deterministic ─────────────────────────
    def _now(self) -> float:
        return time.time()

    # ── configuration ────────────────────────────────────────────────────────
    def set_config(self, config: FDConfig, overrides: dict | None = None) -> None:
        self.config = config.validated()
        self._overrides = overrides or {}

    def config_for(self, camera_id) -> FDConfig:
        override = self._overrides.get(camera_id) or self._overrides.get(str(camera_id))
        return self.config.merged(override) if override else self.config

    def camera_started(self, camera_id, at: float | None = None) -> None:
        """Reset the warm-up clock. Called when a camera instance starts streaming."""
        self._cameras[camera_id] = _CameraState(at if at is not None else self._now())

    def _state(self, camera_id) -> _CameraState:
        st = self._cameras.get(camera_id)
        if st is None:
            st = _CameraState(self._now())
            self._cameras[camera_id] = st
        return st

    def _bump(self, camera_id, metric: str, n: int = 1) -> None:
        c = self.counters.setdefault(camera_id, {})
        c[metric] = c.get(metric, 0) + n

    # ── the judgement ────────────────────────────────────────────────────────
    def judge(self, event: dict, camera_id, point=None,
              has_snapshot: bool = True) -> Judgement:
        """Decide what to do with one event.

        `event` is the module's own dict: {module, event_type, value, data, ...}.
        `point` is an optional normalised (x, y) for the spatial rules — when a module
        cannot supply one, the zone rule is skipped and says so rather than guessing."""
        cfg = self.config_for(camera_id)
        j = Judgement()

        if not cfg.fd_enabled:
            # Completely inert: not even a log line. With the layer off the writer path
            # must be indistinguishable from before this code existed.
            return j

        now = self._now()
        st = self._state(camera_id)
        module = event.get("module") or "-"
        etype = event.get("event_type") or "-"
        data = event.get("data") or {}
        track = data.get("track")

        self._bump(camera_id, "events_judged")

        # ── severity: always computed, never suppresses ──────────────────────
        j.severity, j.severity_source = cfg.severity_for(etype)
        j.severity_recorded = True
        j.alert = FDConfig.at_least(j.severity, cfg.fd_min_severity_alert)
        j.snapshot = FDConfig.at_least(j.severity, cfg.fd_min_severity_snapshot)
        j.checks_passed.append("severity")
        LOG.debug("severity", event="SEVERITY", camera=camera_id, module=module,
                  event_type=etype, assigned=j.severity, source=j.severity_source,
                  fd_min_severity_alert=cfg.fd_min_severity_alert,
                  fd_min_severity_snapshot=cfg.fd_min_severity_snapshot,
                  alert=j.alert, snapshot=j.snapshot)

        verdict = self._run_rules(j, cfg, st, event, camera_id, module, etype,
                                  data, track, point, has_snapshot, now)

        # ── observe mode: record the verdict, then let the event through ─────
        if verdict is not None:
            j.would_suppress = True
            if cfg.enforcing:
                j.verdict = verdict
            else:
                j.verdict = ACCEPT      # observe NEVER removes an alert
        if j.verdict == DEMOTE:
            j.severity = "info"
            j.snapshot = False

        # Only what actually reaches the feed counts towards the rate window.
        if j.verdict != SUPPRESS:
            st.recent.append(now)
            self._bump(camera_id, "accepted" if j.verdict == ACCEPT else "demoted")
        else:
            self._bump(camera_id, "suppressed")
        if j.rule:
            self._bump(camera_id, f"rule:{j.rule}")

        level_fn = LOG.info if j.would_suppress else LOG.debug
        level_fn("decisions", event="JUDGE", camera=camera_id, module=module,
                 event_type=etype, track=track, verdict=j.verdict,
                 rule=j.rule, reason=j.reason, severity=j.severity,
                 confidence=data.get("confidence"),
                 checks_passed=",".join(j.checks_passed) or "-",
                 would_suppress=j.would_suppress, mode=cfg.fd_mode)

        self._maybe_metrics(now, cfg)
        return j

    def _run_rules(self, j, cfg, st, event, camera_id, module, etype,
                   data, track, point, has_snapshot, now):
        """Return the verdict a rule wants, or None if everything passed.

        Each rule sets `j.rule` / `j.reason` when it fires, so the log always names what
        made the decision — an unattributed suppression is not diagnosable."""

        # ── warm-up ──────────────────────────────────────────────────────────
        since_start = now - st.started_at
        if cfg.fd_warmup_seconds > 0 and since_start < cfg.fd_warmup_seconds:
            j.rule, j.reason = "warmup", "camera_warming_up"
            LOG.info("warmup", event="WARMUP", camera=camera_id, module=module,
                     event_type=etype, since_start=round(since_start, 1),
                     fd_warmup_seconds=cfg.fd_warmup_seconds,
                     verdict=SUPPRESS, reason=j.reason, mode=cfg.fd_mode)
            return SUPPRESS
        j.checks_passed.append("warmup")

        # ── ignore zones ─────────────────────────────────────────────────────
        if cfg.fd_ignore_zones:
            if point is None:
                # Recorded rather than silently skipped: a rule that cannot run is
                # something an operator needs to see in the log.
                LOG.trace("zones", event="ZONE", camera=camera_id, module=module,
                          event_type=etype, verdict="SKIP", reason="no_point_available",
                          fd_ignore_zones=len(cfg.fd_ignore_zones))
            else:
                for idx, poly in enumerate(cfg.fd_ignore_zones):
                    if _point_in_poly(point[0], point[1], poly):
                        j.rule, j.reason = "zones", "inside_ignore_zone"
                        LOG.info("zones", event="ZONE", camera=camera_id, module=module,
                                 event_type=etype, track=track,
                                 point=(round(point[0], 4), round(point[1], 4)),
                                 zone=idx, verdict=SUPPRESS, reason=j.reason,
                                 fd_ignore_zones=len(cfg.fd_ignore_zones),
                                 mode=cfg.fd_mode)
                        return SUPPRESS
        j.checks_passed.append("zones")

        # ── ignore hours: demote, never drop ─────────────────────────────────
        if cfg.fd_ignore_hours:
            hhmm = time.strftime("%H:%M", time.localtime(now))
            for window in cfg.fd_ignore_hours:
                start, end = (window or {}).get("start"), (window or {}).get("end")
                if start and end and _within(start, end, hhmm):
                    j.rule, j.reason = "hours", "inside_ignore_hours"
                    LOG.info("zones", event="HOURS", camera=camera_id, module=module,
                             event_type=etype, now=hhmm, window=f"{start}-{end}",
                             verdict=DEMOTE, reason=j.reason, mode=cfg.fd_mode)
                    return DEMOTE
        j.checks_passed.append("hours")

        # ── evidence floors ──────────────────────────────────────────────────
        conf = data.get("confidence")
        hits = _evidence_hits(data.get("evidence"))
        fail = None
        if cfg.fd_min_confidence > 0 and isinstance(conf, (int, float)) \
                and conf < cfg.fd_min_confidence:
            fail = "below_min_confidence"
        elif cfg.fd_min_evidence_frames > 0 and hits is not None \
                and hits < cfg.fd_min_evidence_frames:
            fail = "below_min_evidence_frames"
        elif cfg.fd_require_snapshot and not has_snapshot:
            fail = "no_snapshot"
        if fail:
            j.rule, j.reason = "evidence", fail
            LOG.info("evidence", event="EVIDENCE", camera=camera_id, module=module,
                     event_type=etype, track=track, confidence=conf,
                     fd_min_confidence=cfg.fd_min_confidence,
                     evidence=data.get("evidence"), hits=hits,
                     fd_min_evidence_frames=cfg.fd_min_evidence_frames,
                     snapshot=has_snapshot,
                     fd_require_snapshot=cfg.fd_require_snapshot,
                     verdict=SUPPRESS, reason=fail, mode=cfg.fd_mode)
            return SUPPRESS
        j.checks_passed.append("evidence")

        # ── repeated source ──────────────────────────────────────────────────
        if cfg.fd_repeat_source_enabled and point is not None:
            verdict = self._repeat_rule(j, cfg, st, camera_id, module, etype,
                                        track, point, now)
            if verdict:
                return verdict
        j.checks_passed.append("repeat")

        # ── deduplication ────────────────────────────────────────────────────
        if cfg.fd_dedup_window > 0:
            key = _dedup_key(cfg.fd_dedup_scope, camera_id, module, etype, track)
            ent = st.dedup.get(key)
            if ent is not None and (now - ent[1]) <= cfg.fd_dedup_window:
                ent[1] = now
                ent[2] += 1
                j.rule, j.reason = "dedup", "duplicate_within_window"
                LOG.info("dedup", event="DEDUP", camera=camera_id, module=module,
                         event_type=etype, track=track, key=key,
                         age=round(now - ent[0], 1),
                         fd_dedup_window=cfg.fd_dedup_window,
                         fd_dedup_scope=cfg.fd_dedup_scope,
                         suppressed_count=ent[2], verdict=SUPPRESS, mode=cfg.fd_mode)
                return SUPPRESS
            st.dedup[key] = [now, now, 0]
            _expire(st.dedup, now, cfg.fd_dedup_window * 4, index=1)
        j.checks_passed.append("dedup")

        # ── rate limit and storm ─────────────────────────────────────────────
        cutoff = now - cfg.fd_storm_window
        while st.recent and st.recent[0] < cutoff:
            st.recent.popleft()
        in_window = len(st.recent)

        if cfg.fd_storm_threshold and in_window >= cfg.fd_storm_threshold \
                and cfg.fd_storm_action != "none":
            j.rule, j.reason = "ratelimit", "camera_storming"
            action = SUPPRESS if cfg.fd_storm_action == "suppress" else DEMOTE
            LOG.info("ratelimit", event="STORM", camera=camera_id, module=module,
                     event_type=etype, window_alerts=in_window,
                     fd_storm_threshold=cfg.fd_storm_threshold,
                     fd_storm_action=cfg.fd_storm_action,
                     fd_storm_window=cfg.fd_storm_window,
                     verdict=action, mode=cfg.fd_mode)
            return action

        if cfg.fd_max_alerts_per_minute:
            minute_cut = now - 60.0
            per_minute = sum(1 for t in st.recent if t >= minute_cut)
            if per_minute >= cfg.fd_max_alerts_per_minute:
                j.rule, j.reason = "ratelimit", "rate_limit_exceeded"
                LOG.info("ratelimit", event="RATE", camera=camera_id, module=module,
                         event_type=etype, window_alerts=per_minute,
                         fd_max_alerts_per_minute=cfg.fd_max_alerts_per_minute,
                         storming=0, fd_storm_threshold=cfg.fd_storm_threshold,
                         verdict=SUPPRESS, mode=cfg.fd_mode)
                return SUPPRESS
        j.checks_passed.append("ratelimit")
        return None

    def _repeat_rule(self, j, cfg, st, camera_id, module, etype, track, point, now):
        """A fixed image address that outlives the people near it is furniture.

        `fd_repeat_min_tracks` is what protects a genuine user: one person alerting from
        one spot is ONE track however long they stay, while a fixture gets credited to
        everybody who passes it."""
        cell = (int(point[0] * 1000 // cfg.fd_repeat_cell),
                int(point[1] * 1000 // cfg.fd_repeat_cell))
        ent = st.repeat.get(cell)
        if ent is None or (now - ent[2]) > cfg.fd_repeat_ttl:
            st.repeat[cell] = [1, now, now, {track} if track is not None else set()]
            _expire(st.repeat, now, cfg.fd_repeat_ttl, index=2)
            return None

        ent[0] += 1
        ent[2] = now
        if track is not None:
            ent[3].add(track)
        hits, first, _last, tracks = ent
        span = now - first
        if hits >= cfg.fd_repeat_hits and span >= cfg.fd_repeat_window \
                and len(tracks) >= cfg.fd_repeat_min_tracks:
            j.rule, j.reason = "repeat", "repeated_source"
            LOG.info("repeat", event="REPEAT", camera=camera_id, module=module,
                     event_type=etype, cell=cell, hits=hits, span=round(span, 1),
                     tracks=len(tracks), fd_repeat_hits=cfg.fd_repeat_hits,
                     fd_repeat_window=cfg.fd_repeat_window,
                     fd_repeat_min_tracks=cfg.fd_repeat_min_tracks,
                     verdict=SUPPRESS, reason=j.reason, mode=cfg.fd_mode)
            return SUPPRESS
        LOG.trace("repeat", event="REPEAT", camera=camera_id, cell=cell, hits=hits,
                  span=round(span, 1), tracks=len(tracks), verdict="PASS")
        return None

    # ── metrics ──────────────────────────────────────────────────────────────
    def _maybe_metrics(self, now: float, cfg: FDConfig) -> None:
        import os
        every = float(os.environ.get("FD_METRICS_EVERY", "60"))
        if now - self._last_metrics < every:
            return
        self._last_metrics = now
        self.emit_metrics(cfg, window=every)

    def emit_metrics(self, cfg: FDConfig | None = None, window: float = 60.0) -> None:
        cfg = cfg or self.config
        for camera_id, c in self.counters.items():
            by_rule = {k.split(":", 1)[1]: v for k, v in c.items()
                       if k.startswith("rule:")}
            LOG.info("metrics", event="METRICS", camera=camera_id, window=window,
                     events_judged=c.get("events_judged", 0),
                     accepted=c.get("accepted", 0), demoted=c.get("demoted", 0),
                     suppressed=c.get("suppressed", 0), by_rule=by_rule or None,
                     mode=cfg.fd_mode)

    def log_config_change(self, actor: str, actor_id, scope: str,
                          changed: str, before, after) -> None:
        """The audit trail the brief asks for."""
        LOG.info("config", event="CONFIG_CHANGE", actor=actor, actor_id=actor_id,
                 scope=scope, changed=changed, before=before, after=after)


def _dedup_key(scope: str, camera_id, module, etype, track) -> str:
    if scope == "global":
        return f"*|{module}|{etype}"
    if scope == "camera":
        return f"{camera_id}|{module}|{etype}"
    return f"{camera_id}|{module}|{etype}|{track}"


def _evidence_hits(evidence) -> int | None:
    """`data.evidence` is "hits/window" (the phone module's format). None when absent —
    an event without evidence must not be treated as having zero."""
    if isinstance(evidence, str) and "/" in evidence:
        try:
            return int(evidence.split("/", 1)[0])
        except ValueError:
            return None
    if isinstance(evidence, (int, float)):
        return int(evidence)
    return None


def _expire(mapping: dict, now: float, ttl: float, index: int) -> None:
    """Bounded memory: drop entries whose timestamp at `index` is older than `ttl`."""
    if len(mapping) < 512:
        return
    for k in [k for k, v in mapping.items() if now - v[index] > ttl]:
        mapping.pop(k, None)


FILTER = FalseDetectionFilter()
