# VIGIL — False-Detection Control Layer

**Status:** PLAN ONLY — no code written yet.
**Target branch:** `unusal-activity`
**Source brief:** `false-detection-prompt.txt`
**Date:** 2026-08-26
**Intended outcome:** production deployment

> ### WARNING - branch prerequisite, read before step A
>
> `unusal-activity` branches from `eee6d86`, the state **before** the Phase 1
> (head-count) and Phase 2 (phone) work. Verified on checkout: this branch has **no**
> `analytics/diagnostics.py`, no `headcount_debug.py`, no `phone_debug.py`, no `tests/`
> and no report scripts, and it carries the ORIGINAL `headcount.py`, `mobile.py` and
> `detectors/phone.py`.
>
> Two consequences:
>
> 1. **The logging machinery must be built fresh here.** This plan therefore specifies a
>    self-contained logger (section 4A) with no dependency on Phase 1/2 files, so it
>    works on this branch as it stands.
> 2. **Convergence is an open decision.** Phase 1/2 changed `perception.py`,
>    `camera_manager.py`, `headcount.py`, `mobile.py` and `detectors/phone.py`. This work
>    touches `perception.py` (the writer) and the frontend. Shipping both to production
>    from different branches WILL conflict in `perception.py`.
>
> **Decide before writing code:** merge `headcount_staging` into `unusal-activity` first,
> or keep them separate and accept a manual merge later. Recommended: merge first. The
> Phase 1/2 work has 163 passing tests and fixes measured accuracy defects, and rebasing
> a diagnostics layer onto it is far cheaper than reconciling two writers later.

---

## 0. What this plan is, and what it deliberately is not

`false-detection-prompt.txt` is a **capability checklist for a security-intelligence
product**. Most of the ✓ items are not false-detection settings at all — they are new
analytics that do not exist yet (vehicles, crowds, tampering, abandoned objects, clips).

Those are two different jobs, and mixing them is how the phone module ended up swinging
between over- and under-alerting three times. So this plan separates them:

| | |
|---|---|
| **In scope here** | A **false-detection control layer**: one new, self-contained stage that every module's events pass through before they become alerts, with its own parameters, its own admin UI, its own diagnostics. |
| **Deferred (Phase 3+)** | The new *capabilities* on the checklist — vehicle, crowd, tampering, abandoned object, clips. Section 8 scopes them, but this plan does not build them. |

**Hard constraint honoured throughout:** no existing parameter is renamed, re-defaulted
or repurposed. Everything new lives in a separate namespace (`FD_*` / `fd_*`) in new
files. If the layer is switched off, behaviour is byte-for-byte what it is today.

---

## 1. The brief, mapped against what already exists

Legend — ✅ exists · 🟡 partial · ❌ absent

| Brief item | State | Where it lives today |
|---|---|---|
| **OBJECTS** | | |
| Person | ✅ | `detectors/person.py` — YOLOv11s + ByteTrack |
| Vehicle | ❌ | No vehicle class is requested from the model |
| **SPATIAL** | | |
| Restricted zones | ✅ | `analytics/intrusion.py` |
| Virtual tripwires | ✅ | `analytics/headcount.py` (Phase 1 rewrite) |
| Direction | ✅ | head-count entry/exit, sign of `side_of_line` |
| Zone transitions | 🟡 | zones exist (occupancy/idle/intrusion); A→B transitions do not |
| **TEMPORAL** | | |
| Dwell time | ✅ | `analytics/idle.py` (`dwell_seconds`) |
| Loitering | ✅ | `idle_mode: "loiter"` |
| After-hours presence | 🟡 | `base.within_schedule()` exists; no after-hours *event* |
| Persistence | 🟡 | per-module state TTLs; no cross-event notion |
| Repeated movement | ❌ | — |
| **GROUP** | | |
| Crowd detection | 🟡 | occupancy counts people; no crowd threshold or escalation |
| Unusual gathering | ❌ | — |
| Density escalation | ❌ | — |
| **MOVEMENT** | | |
| Wrong direction | 🟡 | direction is computed; not expressible as a violation |
| Rapid movement | ❌ | speed is tracked internally (head-count `st.speed`) but unused |
| Abnormal movement patterns | ❌ | — |
| **SECURITY** | | |
| Abandoned object | ❌ | — (the phone *static-object map* is the nearest relative) |
| Camera tampering | 🟡 | RTSP open-failure backoff only; no blur/cover/move detection |
| Vehicle intrusion | ❌ | needs Vehicle |
| Composite security events | ❌ | — |
| **ALERTING** | | |
| Severity | ❌ | `alert_logs` has no severity field |
| Cooldowns | 🟡 | each module invents its own; no shared policy |
| Deduplication | 🟡 | per-track cooldowns only; no cross-module dedup |
| Snapshots | ✅ | `perception._result_writer.save_snapshot` |
| Clips | ❌ | no video recording anywhere |
| Audit trail | 🟡 | `alert_logs` is the trail; no actor/decision record |
| **False-positive metrics** | 🟡 | Phase 1/2 diagnostics — per module, not system-wide |

**Reading this table:** the alerting row is where the false-detection work actually
lives. Severity, cooldowns, deduplication and false-positive metrics are all currently
per-module and ad hoc. That is the gap this plan closes.

---

## 2. Why a separate layer rather than more per-module knobs

Every module already carries its own suppression logic, and they disagree with each
other:

```
headcount   crossing_cooldown 2 s   per track
mobile      cooldown         30 s   per track
occupancy   30 s                    per camera, via Analytic._cooldown
intrusion   10 s                    per camera
smoking     SMOKE_COOLDOWN   15 s   per camera
fire_smoke  30 s                    per class
attendance  EMP_COOLDOWN     15 s   per (employee, camera)
```

Six different cooldown notions, none visible to an operator, none consistent, and no
way to answer "why did I get twelve alerts in a minute?" without reading six files.

A single stage that every event passes through gives one place to express policy, one
place to log why an alert was suppressed, and one screen to tune it — **without
touching any of the six**. Each module keeps its own logic exactly as it is; the new
layer only ever *removes or annotates* what they emit.

---

## 3. Where it sits

```
perception child (GPU)
   │  module consumers emit events   ← UNCHANGED
   ▼
mp.Queue
   ▼
PerceptionWriter  (main process, the single DB writer)
   │
   ├─▶ ★ FalseDetectionFilter.judge(event, context)   ← NEW, the only new stage
   │        ├─ verdict ACCEPT   → written to alert_logs as today, + severity
   │        ├─ verdict SUPPRESS → not written; counted and logged
   │        └─ verdict DEMOTE   → written with severity="info", no snapshot
   ▼
alert_logs
```

**Why in the writer, not in the child:**

- It is the single choke point every module's events already pass through.
- It has DB access, which cross-event deduplication needs (recent alert history).
- The child stays GPU-only with no DB — the architectural rule from day one.
- Modules stay ignorant of it, so nothing existing changes.

**Cost:** the writer is a single synchronous thread. Section 7 covers keeping the
judgement O(1) so it does not become a throughput bound.

---

## 4. New parameters — `fd_*`, none overlapping

Checked against the full existing inventory (Phase 1 `HC_*` + Phase 2 `PHONE_*` +
`ENQUEUE_INTERVAL` / `TRACK_INTERVAL` / `PHONE_INTERVAL` / `BROADCAST_INTERVAL` and the
nine registry `config` fields). **No name collides.**

### 4.1 Master switch

| Parameter | Default | Purpose |
|---|---|---|
| `fd_enabled` | `false` | Off by default. Switched off, the writer path is identical to today. |
| `fd_mode` | `"observe"` | `observe` = judge and log but never suppress · `enforce` = actually suppress. Ship in `observe` so its effect is measured before it changes any alert. |

`observe` mode is the point of the design: we can measure exactly what this layer would
have suppressed against real traffic **before** trusting it. That is the step missing
from every previous round of tuning.

### 4.2 Severity

| Parameter | Default | Purpose |
|---|---|---|
| `fd_severity_map` | see below | event type → `critical` \| `high` \| `medium` \| `low` \| `info` |
| `fd_min_severity_alert` | `"low"` | below this, the row is written but does not surface in the alert feed |
| `fd_min_severity_snapshot` | `"medium"` | below this, no snapshot is saved — directly cuts disk growth |

Proposed starting map (all overridable per camera):

```
critical  fire, smoke, intrusion (after-hours)
high      intrusion, smoking, unknown (attendance)
medium    phone_use, over_capacity, idle
low       entry, exit, checkin, checkout
info      recognized, occupancy
```

### 4.3 Deduplication

| Parameter | Default | Purpose |
|---|---|---|
| `fd_dedup_window` | `60` s | two events matching the key inside this window are one alert |
| `fd_dedup_key` | `"camera,module,event_type,track"` | which fields make an alert "the same" |
| `fd_dedup_scope` | `"track"` | `track` \| `camera` \| `global` |

Deliberately **additive** to existing per-module cooldowns — those keep working, and
this catches what they cannot see (the same person alerting from two modules at once,
or the same event re-firing after a track-id change).

### 4.4 Rate limiting and storm control

| Parameter | Default | Purpose |
|---|---|---|
| `fd_max_alerts_per_minute` | `20` | per camera; excess is counted and dropped |
| `fd_storm_window` | `60` s | window over which a storm is judged |
| `fd_storm_threshold` | `40` | alerts in the window that mark a camera as storming |
| `fd_storm_action` | `"demote"` | `demote` \| `suppress` \| `none` |

A camera producing 40 alerts a minute is broken, not busy. Today that floods the feed
and buries genuine alerts — the exact complaint the occupancy fix addressed in Phase 1,
generalised.

### 4.5 Confidence and evidence floors

| Parameter | Default | Purpose |
|---|---|---|
| `fd_min_confidence` | `0.0` | reject events whose `data.confidence` is below this |
| `fd_min_evidence_frames` | `0` | reject events whose `data.evidence` shows fewer hits |
| `fd_require_snapshot` | `false` | reject events that produced no snapshot (a proxy for "unverifiable") |

Zero defaults = inert. These exist so a site can raise a floor **globally** without
editing each module's own thresholds.

### 4.6 Spatial and temporal suppression

| Parameter | Default | Purpose |
|---|---|---|
| `fd_ignore_zones` | `[]` | normalised polygons where events are never raised (a TV, a poster, a reflective door) |
| `fd_ignore_hours` | `[]` | `[{start,end}]` windows where events are demoted (cleaning shift, known deliveries) |
| `fd_warmup_seconds` | `30` | suppress everything for this long after a camera starts — the tracker is unstable at start-up |

`fd_ignore_zones` is the single most requested false-positive control in CCTV analytics
and currently has no equivalent anywhere in VIGIL.

### 4.7 Static / repeated-source suppression (generalised)

| Parameter | Default | Purpose |
|---|---|---|
| `fd_repeat_source_enabled` | `true` | generalises the phone module's static-object map to all modules |
| `fd_repeat_cell` | `48` px | grid resolution |
| `fd_repeat_hits` | `25` | hits before an address is treated as a fixture |
| `fd_repeat_window` | `120` s | ...spanning at least this long |
| `fd_repeat_min_tracks` | `3` | ...credited to at least this many different tracks |
| `fd_repeat_ttl` | `900` s | cell expiry |

**Note:** the phone module's own `PHONE_STATIC_*` parameters stay exactly as they are.
This is a second, independent map at the alert level. If the two prove redundant after
measurement, retiring one is a later decision — not a change made blind now.

### 4.8 Diagnostics

| Parameter | Default | Purpose |
|---|---|---|
| `FD_DEBUG` | unset | `off` \| `1` \| `debug` \| `trace` — master switch and level |
| `FD_LOG_DIR` | `logs/false_detection/` | directory for the per-rule log files |
| `FD_LOG_MAX_MB` | `50` | rotation size per file |
| `FD_LOG_BACKUPS` | `5` | rotated files kept per rule |
| `FD_METRICS_EVERY` | `60` s | periodic counter snapshot |
| `FD_LOG_SPLIT` | `true` | one file per rule; `false` = single combined file |

Full specification in section 4A.

---

## 4A. Logging specification — one log per rule

The core requirement: **a log must be sufficient on its own to diagnose what each
parameter did.** A log handed over from the server has to answer both "why did this
fire" and "why did this NOT fire" without access to the running system.

### 4A.1 Directory layout

```
logs/
  false_detection/
    fd_decisions.log     every judgement, the master ordered trail
    fd_severity.log      severity assignment
    fd_dedup.log         fd_dedup_window / _key / _scope
    fd_ratelimit.log     fd_max_alerts_per_minute / fd_storm_*
    fd_evidence.log      fd_min_confidence / _evidence_frames / _require_snapshot
    fd_zones.log         fd_ignore_zones / fd_ignore_hours
    fd_warmup.log        fd_warmup_seconds
    fd_repeat.log        fd_repeat_* (repeated-source map)
    fd_metrics.log       periodic counters, per camera
    fd_config.log        audit: every config change, who and when
```

**Why one file per rule.** With everything interleaved, isolating one parameter's effect
means grepping a mixed stream and inferring which rule was responsible. Split by rule,
"is dedup too aggressive?" is one file, and a single rule's log can be shipped rather
than the whole system. `fd_decisions.log` keeps the full ordered trail, so the split
loses nothing.

`FD_LOG_SPLIT=false` produces a single combined file when that is easier to send.

### 4A.2 Every line is self-describing

The rule that makes these logs usable:

> **Every line carries the parameter values that produced the decision.**

Without it a log cannot be interpreted later or on another machine. This is exactly what
let the Phase 2 confidence-threshold bug survive two captures: the log recorded the
decision but not which floor was in force, so the two were indistinguishable.

Format: one line, `key=value`, whitespace-separated, values never containing spaces.

### 4A.3 Line schemas

Every file opens with a SESSION header carrying the complete resolved configuration:

```
event=SESSION component=false_detection pid=612 mode=observe fd_enabled=1
  fd_dedup_window=60 fd_dedup_scope=track fd_max_alerts_per_minute=20
  fd_storm_threshold=40 fd_storm_action=demote fd_min_confidence=0.00
  fd_warmup_seconds=30 fd_repeat_hits=25 fd_repeat_window=120
```

`fd_decisions.log` — one line per event judged:

```
event=JUDGE camera=48 module=mobile event_type=phone_use track=27
  verdict=SUPPRESS rule=dedup reason=duplicate_within_window
  severity=medium confidence=0.62 age=12.4
  fd_dedup_window=60 fd_dedup_scope=track mode=enforce
```

```
event=JUDGE camera=48 module=headcount event_type=entry track=91
  verdict=ACCEPT rule=- reason=- severity=low confidence=1.00
  checks_passed=severity,dedup,ratelimit,evidence,zones,warmup,repeat mode=enforce
```

`checks_passed` is deliberate: an ACCEPT records WHICH rules examined it, so a rule that
is silently not running is visible in the log rather than invisible.

`fd_dedup.log`:

```
event=DEDUP camera=48 module=mobile event_type=phone_use track=27
  key=48|mobile|phone_use|27 first_seen=10:14:10.0 age=12.4
  verdict=SUPPRESS fd_dedup_window=60 fd_dedup_scope=track
  suppressed_count=3 mode=enforce
```

`fd_ratelimit.log`:

```
event=RATE camera=48 window_alerts=21 verdict=SUPPRESS
  fd_max_alerts_per_minute=20 storming=0 fd_storm_threshold=40 mode=enforce
event=STORM camera=48 window_alerts=44 verdict=DEMOTE
  fd_storm_threshold=40 fd_storm_action=demote fd_storm_window=60
```

`fd_zones.log`:

```
event=ZONE camera=48 module=intrusion event_type=intrusion track=12
  point=(0.4120,0.6630) zone=2 verdict=SUPPRESS reason=inside_ignore_zone
  fd_ignore_zones=3
event=HOURS camera=48 event_type=intrusion now=03:14 window=22:00-06:00
  verdict=DEMOTE reason=inside_ignore_hours
```

`fd_evidence.log`:

```
event=EVIDENCE camera=48 module=mobile event_type=phone_use track=27
  confidence=0.18 fd_min_confidence=0.25 evidence=2/5 fd_min_evidence_frames=3
  snapshot=1 fd_require_snapshot=0 verdict=SUPPRESS reason=below_min_confidence
```

`fd_repeat.log`:

```
event=REPEAT camera=48 module=mobile event_type=phone_use
  cell=(14,9) hits=27 span=141.2 tracks=4 verdict=SUPPRESS reason=repeated_source
  fd_repeat_hits=25 fd_repeat_window=120 fd_repeat_min_tracks=3
```

`fd_warmup.log`:

```
event=WARMUP camera=48 since_start=11.4 fd_warmup_seconds=30
  verdict=SUPPRESS reason=camera_warming_up events_suppressed=6
```

`fd_severity.log`:

```
event=SEVERITY camera=48 module=mobile event_type=phone_use assigned=medium
  source=default fd_min_severity_alert=low fd_min_severity_snapshot=medium
  alert=1 snapshot=1
```

`fd_metrics.log` — every `FD_METRICS_EVERY` seconds, per camera:

```
event=METRICS camera=48 window=60 events_judged=142 accepted=118 demoted=6
  suppressed=18 by_rule=dedup:9,ratelimit:4,evidence:3,zones:2
  severity=critical:0,high:2,medium:41,low:75 mode=enforce
```

`fd_config.log` — the audit trail the brief asks for:

```
event=CONFIG_CHANGE actor=admin actor_id=1 at=2026-08-26T10:02:11
  scope=global changed=fd_dedup_window before=60 after=90
event=CONFIG_CHANGE actor=supervisor actor_id=4 scope=camera:48
  changed=fd_ignore_zones before=2 after=3
```

### 4A.4 Levels

| Level | Contents | Volume |
|---|---|---|
| `1` (INFO) | SUPPRESS and DEMOTE verdicts, config changes, metrics | a few lines per suppression |
| `debug` | + every ACCEPT with `checks_passed` | one line per event |
| `trace` | + per-rule intermediate values (dedup keys, rate-window contents, per-zone point tests) | verbose; minutes, not shifts |

**Level `1` is the production default when `FD_DEBUG` is set.** It answers "what was
suppressed and why" at a volume safe to leave running indefinitely.

### 4A.5 Disk budget

`FD_LOG_MAX_MB=50` x `FD_LOG_BACKUPS=5` x 10 files = **2.5 GB worst case**, all rotating.
At level `1` on a 20-camera site, expect tens of MB per day.

- [ ] Confirm free space on the production box before enabling `trace`
- [ ] `logs/` is already git-ignored (verified) — captures cannot be committed by accident

### 4A.6 What I do with a log you send

1. `false_detection_report.py <dir>` — totals, per-rule suppression counts, per-camera breakdown
2. Cross-check the SESSION header against what you *intended* to configure. This catches the "parameter never actually applied" class of bug, which is what went wrong twice in Phase 2
3. For each suppression, confirm the recorded inputs justify the verdict under the recorded parameters
4. Replay the event stream at alternative settings (`--sweep`) to show the trade-off curve
5. Compare against the recorded results in `false-detection-test-cases.md`

---

---

## 5. Admin panel

### 5.1 Where

A new **"False Detection"** card in `frontend/src/views/AdminView.tsx`, alongside the
existing Locations / Departments / AI-parameter cards. It reuses `components/ui`
primitives (`Card`, `Switch`, `Input`, `Select`, `Badge`, `Toast`) — no new design
system, consistent with the existing admin surface.

### 5.2 Layout

```
┌─ False Detection ────────────────────────────────────────────────┐
│  Enabled            [ ○ off ]        Mode  ( Observe | Enforce )  │
│  ─────────────────────────────────────────────────────────────── │
│  Last 24 h:  1 284 events → 1 106 alerts, 178 suppressed (13.9%)  │
│     dedup 92 · rate-limit 40 · ignore-zone 31 · warm-up 15        │
│  ─────────────────────────────────────────────────────────────── │
│  Deduplication      window [ 60 ] s     scope ( track ▾ )         │
│  Rate limit         max [ 20 ] /min     storm [ 40 ] → (demote ▾) │
│  Evidence floors    confidence [ 0.00 ] frames [ 0 ]              │
│  Warm-up            [ 30 ] s after camera start                   │
│  Repeated source    [ ✓ ]  hits [ 25 ] window [ 120 ]s tracks [3] │
│  ─────────────────────────────────────────────────────────────── │
│  Severity by event type            [ table: event → severity ▾ ]  │
│  Alert feed shows      ( low ▾ and above )                        │
│  Save snapshots for    ( medium ▾ and above )                     │
│  ─────────────────────────────────────────────────────────────── │
│  Per-camera overrides                            [ Manage… ]      │
└──────────────────────────────────────────────────────────────────┘
```

The **live suppression counter is the important element**, not the inputs. It is what
lets an operator see the layer's effect in `observe` mode before enabling `enforce`.

### 5.3 Ignore zones

Drawn with the **existing `ZoneEditor`** component (already used by occupancy, idle and
intrusion) on a camera snapshot, stored normalised 0–1 like all other geometry. No new
drawing code.

Per-camera overrides go in the existing `CameraModulesEditor` as a new collapsible
section, so camera settings stay in one place.

### 5.4 Storage — decision required

| Option | Cost | Recommendation |
|---|---|---|
| **A.** New `false_detection` TEXT column on `system_settings`, JSON | one small Alembic migration; matches how `locations`/`departments` are already stored | **Recommended** — consistent with existing practice |
| **B.** New `false_detection_settings` table | proper columns, but contradicts the "modules do not add tables" rule | Not recommended |
| **C.** Env vars only, no UI | no migration | Rejected — the brief asks for an admin panel |

Per-camera overrides ride in the existing `camera_devices.module_config` JSON under an
`"_fd"` key, so no camera-table migration is needed.

⚠️ The migration tree has **two heads** (`9488212d6fbb`, `d4e5f6a0b1c2`). Any new
revision must be created against a chosen head and applied with `alembic upgrade heads`.

### 5.5 API

Two new endpoints beside the existing settings router — no existing endpoint changes:

```
GET  /api/settings/false-detection      → current config + last-24h counters
PUT  /api/settings/false-detection      → update (admin/supervisor only)
```

Both go through `require_role`, matching every other mutating endpoint.

---

## 6. What changes at each stage

| Stage | Change | Risk |
|---|---|---|
| **Model inventory** | **None.** No new model, no new weights, no change to YOLOv11s, SCRFD, ArcFace, OSNet, smoking or fire/smoke. | — |
| **Detectors** | **None.** The layer never sees pixels. | — |
| **Perception child** | **None.** No new state, no DB access, GPU work unchanged. | — |
| **Analytics consumers** | **None.** All nine modules untouched. Their cooldowns and thresholds keep their current values. | — |
| **camera_manager** | **None.** The three-lane cadence is untouched. | — |
| **PerceptionWriter** | One new call per result batch. Must stay O(1) per event. | Medium — it is the single DB writer |
| **DB schema** | One nullable TEXT column on `system_settings`; optionally one nullable `severity` column on `alert_logs` | Low, but see §6.1 |
| **API** | Two new endpoints | Low |
| **Frontend** | One new admin card, one new section in the camera editor. `dist/` must be rebuilt and committed. | Low |
| **Diagnostics** | Third `DiagnosticLog` instance + `false_detection_report.py` | Low |

### 6.1 Severity storage — decision required

| Option | Trade-off |
|---|---|
| **A.** `data.severity` in the existing JSON string | No migration. **Not filterable or indexable** — `data` is TEXT, not JSONB |
| **B.** New nullable `severity` column on `alert_logs` | One migration. Filterable, indexable, sortable in the alert feed |

**Recommended: B.** A security product must be able to answer "show me today's critical
alerts", and option A cannot do that without a full table scan and JSON parsing in
Python. It is a column, not a table, so the "modules do not add tables" rule is intact.

---

## 7. Performance

The writer is a **single synchronous psycopg2 thread** and is already a suspected
throughput bound. Rules for this layer:

- Judgement must be **O(1) per event** — dictionary and deque lookups only, no queries.
- Dedup and rate-limit state live in **memory**, seeded once at start-up from the last
  `fd_dedup_window` of `alert_logs`, never queried per event.
- Bounded memory: every map is keyed per camera with TTL expiry, as the phone
  static-map already is.
- The layer must be measured against writer throughput **before** `enforce` is enabled
  anywhere.

---

## 8. Explicitly deferred (Phase 3+)

Scoped here so it is clear they are *not* in this plan, with the honest cost of each:

| Capability | What it needs | Notes |
|---|---|---|
| Vehicle | COCO classes 2/3/5/7 from the **existing** yolo11s — no new model | Smallest new capability; the `registry`+`modules.ts` mirror must be edited together |
| Vehicle intrusion | Vehicle + existing zone logic | Follows directly from Vehicle |
| Crowd / density escalation | Existing person tracks + a new consumer | No model needed |
| Zone transitions | New consumer over existing zones | No model needed |
| After-hours presence | `within_schedule` already exists | Small |
| Rapid / abnormal movement | Per-track speed — head-count already computes it | Needs a shared track-speed primitive |
| Abandoned object | Background modelling, new detector | **Largest** item; genuinely hard on a moving-exposure CCTV feed |
| Camera tampering | Frame statistics (blur, luminance, histogram shift) — no model | Cheap, high value, no GPU |
| Composite events | A rule engine over `alert_logs` | Only sensible **after** severity and dedup exist |
| Clips | Rolling frame buffer + encoder in `camera_manager` | Significant memory and disk cost; needs its own plan |
| Audit trail | `alert_logs` + an actor/decision record | Overlaps the SaaS branch's tenant work |

**Recommended order after this plan:** Camera tampering → Vehicle → Crowd → Zone
transitions → the rest.

---

## 9. Test plan

> Automated coverage is below. **Manual per-parameter verification is specified
> separately in `false-detection-test-cases.md`** — every parameter has a physical
> procedure, the exact log line it should produce, and a pass criterion. Both must be
> complete before production sign-off.

### 9.1 Regression — must not break (highest priority)

- [ ] All 163 existing tests pass unchanged
- [ ] With `fd_enabled=false`, the writer produces **byte-identical** `alert_logs` rows
- [ ] Head-count fast lane, replay and event contract unaffected
- [ ] Phone medium lane, evidence gate and event contract unaffected
- [ ] No existing parameter's default value changes (asserted by a test that pins them)
- [ ] Perception child still has no DB access

### 9.2 Judgement logic

- [ ] `observe` mode never suppresses, but counts what it would have
- [ ] `enforce` mode suppresses exactly what `observe` counted, on the same input
- [ ] Duplicate inside the window → one alert; outside → two
- [ ] Dedup scope `track` vs `camera` vs `global` behave differently on the same input
- [ ] Rate limit drops the excess and counts it; the first N still pass
- [ ] Storm action `demote` writes the row at `info`; `suppress` writes nothing
- [ ] Event inside `fd_ignore_zones` is suppressed; the same event outside is not
- [ ] Event inside `fd_ignore_hours` is demoted, not silently dropped
- [ ] Warm-up suppresses only within `fd_warmup_seconds` of camera start
- [ ] Repeated-source map suppresses a fixed address after the thresholds are met
- [ ] Repeated-source map does **not** suppress an address credited to one track
- [ ] Severity map assigns correctly; unknown event types get a documented default
- [ ] `fd_min_severity_snapshot` prevents the snapshot write but not the row

### 9.3 Multi-camera and isolation

- [ ] Camera A storming does not suppress camera B
- [ ] Per-camera override beats the global setting
- [ ] Two people alerting simultaneously on one camera both survive dedup
- [ ] State is purged per camera; memory bounded over a long run

### 9.4 Persistence and API

- [ ] Config round-trips through `GET`/`PUT` unchanged
- [ ] Invalid config is rejected with a clear error, never half-applied
- [ ] Non-admin roles are refused
- [ ] Migration applies cleanly with `alembic upgrade heads` (two heads!)
- [ ] Existing rows survive the `severity` column addition (nullable)

### 9.5 Performance

- [ ] Judgement is O(1): 10 000 events measured, no per-event DB query
- [ ] Writer throughput before vs after, measured not assumed
- [ ] Memory bounded across a simulated 24 h run

### 9.6 Diagnostics

- [ ] `FD_DEBUG` off by default; nothing written
- [ ] Every suppression records the rule that caused it
- [ ] `false_detection_report.py` summarises a capture and reports what each rule cost
- [ ] Replay: re-run a captured event stream at different settings

---

## 10. Rollout

Each step ends in a measurable state. Nothing moves to the next until the previous is
verified against real traffic.

- [x] **A. Skeleton** — `backend/services/false_detection/` (config, logging, engine), `fd_enabled=false`, writer hook in `perception._result_writer`. Zero behaviour change proven by `tests/test_false_detection_writer.py::test_01/test_02`.
- [x] **B. Diagnostics** — `FD_DEBUG` logger writing ten per-rule files + `backend/scripts/false_detection_report.py` with `--config-check`, `--replay`, `--sweep`. Covered by `tests/test_false_detection_logging.py` and `tests/test_false_detection_report.py`.
- [x] **C. Severity** — `DEFAULT_SEVERITY_MAP` + `alert_logs.severity` column, migration `f1a2b3c4d5e6`.
- [x] **D. Dedup + rate limit** — implemented with `observe` as the shipped default.
- [ ] **E. Measure** — ⏳ **needs the deployment box.** Cannot be done here: this laptop has no camera and no PostgreSQL. **Decision point: is this layer worth enabling?**
- [x] **F. Ignore zones + warm-up** — both implemented; `camera_manager` calls `FILTER.camera_started` so warm-up is anchored to the real stream start.
- [x] **G. Admin panel** — `GET`/`PUT /api/settings/false-detection`, `FalseDetectionPanel.tsx` wired into `AdminView`, `npm run build` run and `dist/` rebuilt. ⏳ Per-camera override UI in `CameraModulesEditor` still to do; per-camera overrides are supported by the engine and settable through the API today.
- [ ] **H. Enforce** — ⏳ **needs the deployment box**, and must not be attempted before E.
- [x] **I. Repeated-source map** — implemented (`fd_repeat_*`), off by default.

---

## 10A. Production deployment runbook

This plan targets production, so the deployment path is part of it rather than an
afterthought.

### 10A.1 Pre-flight (do not skip)

- [ ] Branch decision made (see the warning at the top) and merge completed if chosen
- [ ] `python -m pytest tests/ -q` green on the deployment branch
- [ ] `alembic heads` shows the expected heads; migration written against one of them
- [ ] Database backed up — the migration adds columns and is reversible, but back up anyway
- [ ] `frontend/` built AND `dist/` committed — a frontend change is not live otherwise
- [ ] Free disk confirmed against the log budget (section 4A.5)
- [ ] `fd_enabled=false` in the shipped configuration

### 10A.2 Deploy

```bash
cd /home/optimumtech/headcount_stagging/PPE_NEW
git fetch origin && git checkout unusal-activity && git pull

alembic upgrade heads          # NOTE: heads, plural - the tree has two

# frontend, only if dist/ was not committed with the change
cd frontend && npm ci && npm run build && cd ..

FD_DEBUG=1 bash start.sh
```

Expected on stdout within seconds of start:

```
[INIT] Perception process started (pid=...)
[FALSE-DETECTION] diagnostics -> /.../logs/false_detection/ (level=INFO, mode=observe)
[FALSE-DETECTION] fd_enabled=0 - layer inert, writer path unchanged
[PERCEPTION] Ready.
```

If the second line is absent, the environment variable did not reach the process. Stop
and fix that before continuing — a silent diagnostics failure is what made the Phase 2
threshold bug invisible.

### 10A.3 Staged enablement

Never enable enforcement globally in one step.

| Stage | Setting | Duration | Exit criterion |
|---|---|---|---|
| 1 | `fd_enabled=false` | 1 h | No change in alert volume vs the previous day |
| 2 | `fd_enabled=true`, `fd_mode=observe` | 24 h | `fd_metrics.log` shows what WOULD be suppressed; you review and accept it |
| 3 | `fd_mode=enforce` on ONE camera | 24 h | No genuine event missed on that camera |
| 4 | `fd_mode=enforce` on all cameras | ongoing | Suppression rate stable, no missed events reported |

**Stage 2 is the decision point.** If the layer would have suppressed something you
needed, it is tuned or abandoned there — before it can hide a real security event.

### 10A.4 Rollback

Three levels, fastest first:

```bash
# 1. instant, no restart - via the admin panel or API
PUT /api/settings/false-detection  {"fd_enabled": false}

# 2. environment, needs a restart
unset FD_DEBUG && bash start.sh          # with fd_enabled=false persisted

# 3. full revert
git checkout <previous-commit> && alembic downgrade -1 && bash start.sh
```

The added DB columns are nullable, so level 3 does not require a downgrade to keep the
system running — old code ignores columns it does not know about.

### 10A.5 Monitoring after deployment

Watch daily for the first week:

| Signal | Where | Healthy | Investigate if |
|---|---|---|---|
| Suppression rate | `fd_metrics.log` | 5-20% | >40% (over-suppressing) or ~0% (rules not firing) |
| Rule balance | `by_rule=` | spread | one rule accounts for >80% |
| Storming cameras | `fd_ratelimit.log` | rare | the same camera daily = a broken camera, not a busy one |
| Writer throughput | alert timestamps | steady | growing lag = the judgement is not O(1) |
| Log growth | `du -sh logs/` | tens of MB/day | GB/day = drop from `trace` to `1` |
| Missed events | operator reports | none | ANY - this is the failure that matters |

### 10A.6 Production sign-off

- [ ] 24 h in `observe` reviewed and accepted
- [ ] Every parameter manually verified per `false-detection-test-cases.md`
- [ ] Rollback rehearsed at least once on the deployment box
- [ ] Suppression rate within band for 7 days
- [ ] Zero reported missed events during staged enablement
- [ ] Log rotation confirmed working (files rolling, disk stable)

---

## 11. Status

### Done
- [x] Read `false-detection-prompt.txt`
- [x] Verify the target branch state (`unusal-activity` = pre-Phase-1; no diagnostics, no tests) and record the convergence risk
- [x] Specify per-rule logging under `logs/false_detection/` (section 4A)
- [x] Write the production deployment runbook, staged enablement and rollback (section 10A)
- [x] Write `false-detection-test-cases.md` — a manual procedure and expected log line for every parameter
- [x] Inventory every existing parameter (Phase 1 `HC_*`, Phase 2 `PHONE_*`, cadence, registry config) and verify **no `fd_*` name collides**
- [x] Map all 30 brief items to exists / partial / absent
- [x] Choose the integration point (writer) and justify it against the architecture rules
- [x] Design the parameter set, admin panel, storage and API
- [x] Identify model-inventory / detector impact (**none**)
- [x] Write the test plan
- [x] Write this document

### Pending — decisions needed from you
- [x] **Severity storage**: built as a `severity` column on `alert_logs` (nullable, indexed). Rows written before the layer existed stay `NULL` and the API reports them as `unclassified` rather than dropping them.
- [x] **Settings storage**: built as a `false_detection` JSON text column on `system_settings`. No new table.
- [x] **Default mode**: ships `fd_enabled=false`, and `fd_mode=observe` when enabled. Asserted by `tests/test_false_detection_api.py::test_01`.
- [ ] **Branch convergence**: ⚠️ **still open, and still the largest risk.** `unusal-activity` does not contain the Phase 1/2 work on `headcount_staging`. Both branches modify `perception.py`. Merge `headcount_staging` in first (recommended), or reconcile by hand later.
- [ ] **Scope**: is this layer the right next step, or is a deferred capability (camera tampering, vehicle) more valuable to you first?

### Pending — implementation
- [x] Steps A, B, C, D, F, G, I in §10 — built, 153 automated tests green, pyflakes clean
- [x] `tests/` — 153 tests across five files (engine 52, writer 15, logging 22, API 13, report 29 + 22 parametrised cases)
- [x] Migration `f1a2b3c4d5e6` verified offline via `alembic upgrade d4e5f6a0b1c2:f1a2b3c4d5e6 --sql` (no PostgreSQL on this laptop)
- [x] `frontend/dist/` rebuilt with the panel included
- [ ] Per-camera override UI in `CameraModulesEditor` (ignore zones via the existing `ZoneEditor`)
- [ ] Steps E and H — require the deployment box; see §10A and `false-detection-test-cases.md`

**What has NOT been verified, stated plainly:** nothing here has been run against a
camera, a real database, or real traffic. The automated tests prove the rules behave as
specified against synthetic events, that the log format round-trips through the report
parser, and that the layer is inert when disabled. They prove nothing about whether the
default thresholds are right for your site — that is exactly what step E is for.

---

## 12. Risks

| Risk | Why it matters | Mitigation |
|---|---|---|
| The layer hides real events | A suppressed security event is a failure, not an annoyance | `observe` mode first; every suppression logged with its reason; `enforce` per camera |
| Writer becomes a bottleneck | It is already the single DB writer and a suspected bound | O(1) rule; measure before/after; no per-event queries |
| Overlap with per-module cooldowns | Two layers suppressing the same thing is confusing to tune | Existing cooldowns untouched; the report attributes each suppression to a specific rule |
| Another blind tuning cycle | Exactly what went wrong with the phone module | Nothing ships without a measured `observe` capture; §10E is a hard decision point |
| Migration on a two-head tree | `alembic upgrade head` fails | Use `heads`; note it in the runbook |
| Scope creep from the brief | The checklist is a product roadmap, not a filter spec | §8 lists deferred items explicitly, with an order |

---

## 13. Honest assessment

The false-detection layer is worth building, but **it is not what will most improve
detection quality right now.** The phone module's own evidence says the binding
constraint is *detector recall* — three of five cameras produced zero phone detections
in 137 seconds. No amount of alert filtering fixes that.

This layer makes the alerts you *do* get cleaner, more consistent and tunable from one
screen, and it gives severity and deduplication, which a security product needs. Those
are real gains. But if the choice is between this and a dedicated phone detector, the
detector is the one that changes what the system can see.

Worth deciding before step A.
