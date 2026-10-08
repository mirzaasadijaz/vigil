# VIGIL — False Detection: Manual Test Cases

**Companion to:** `false-detection-plan.md`
**Purpose:** verify **every** false-detection parameter by hand, on the real system,
before production sign-off.
**Date:** 2026-08-26

---

## How to use this document

Each test gives: what to configure, what to physically do, **the exact log line it must
produce**, and a pass criterion. Tick the box only when the log line was actually seen —
not when the behaviour merely looked right.

> **The log line is the evidence, not the screen.** A parameter that "seems to work" but
> writes nothing cannot be diagnosed later, and cannot be checked from a log you send me.
> Twice in Phase 2 a setting appeared to work while silently not being applied; both times
> the log was what eventually revealed it.

### Session setup

```bash
cd /home/optimumtech/headcount_stagging/PPE_NEW
FD_DEBUG=trace bash start.sh
```

Confirm before starting:

```
[FALSE-DETECTION] diagnostics -> /.../logs/false_detection/ (level=TRACE, mode=...)
```

Then in a second terminal:

```bash
watch -n1 'ls -la logs/false_detection/'
tail -F logs/false_detection/fd_decisions.log
```

**Open the camera's live view and keep it open.** Camera threads stop after 30 s idle,
so a test with no viewer produces no events and looks like a failure.

### Recording results

For every test record: **time started · what you did · what the log showed · pass/fail**.
Send `logs/false_detection/` plus this filled-in document. The pairing of the two is what
lets a log be interpreted rather than guessed at.

---

## Part 0 — Preconditions

| # | Check | How | Pass | ✅ |
|---|---|---|---|---|
| 0.1 | Layer is inert when off | `fd_enabled=false`, generate any event | Row appears in `alert_logs` exactly as before; `fd_decisions.log` has only the SESSION header | ☐ |
| 0.2 | Log directory is created | Start with `FD_DEBUG=1` | All 10 files exist immediately, each with a SESSION header | ☐ |
| 0.3 | Session header is complete | `head -2 fd_decisions.log` | Every `fd_*` parameter appears with its value | ☐ |
| 0.4 | Diagnostics off writes nothing | unset `FD_DEBUG`, restart, generate events | No new lines in any file | ☐ |
| 0.5 | Existing modules unaffected | Walk the head-count line; use a phone | Counts and phone alerts behave exactly as before this layer | ☐ |

**0.5 is the most important test in this document.** If it fails, stop — the layer is
changing existing behaviour, which it must never do.

---

## Part 1 — Master switch

### 1.1 `fd_enabled`

| | |
|---|---|
| **Set** | `fd_enabled=false` |
| **Do** | Trigger any alert (walk past a camera, use a phone) |
| **Expect** | `fd_decisions.log` gains no JUDGE line. The alert appears normally |
| **Pass** | Zero JUDGE lines, alert present | ☐ |

Then set `fd_enabled=true`, repeat:

```
event=JUDGE camera=48 module=... verdict=ACCEPT rule=- checks_passed=... mode=observe
```

| **Pass** | A JUDGE line appears for every event, all ACCEPT | ☐ |

### 1.2 `fd_mode` — observe vs enforce

| | |
|---|---|
| **Set** | `fd_enabled=true`, `fd_mode=observe`, `fd_dedup_window=60` |
| **Do** | Trigger the SAME event twice within 10 s |
| **Expect** | Second event: `verdict=SUPPRESS rule=dedup ... mode=observe` — **but the alert still appears** |
| **Pass** | Log says SUPPRESS, alert IS present (observe never suppresses) | ☐ |

Switch to `fd_mode=enforce`, repeat:

| **Pass** | Log says SUPPRESS, alert is **absent** | ☐ |

> This pair is the safety property of the whole design: `observe` measures without
> acting. If `observe` ever removes an alert, the layer is unsafe to deploy.

---

## Part 2 — Severity

### 2.1 `fd_severity_map`

| | |
|---|---|
| **Set** | default map |
| **Do** | Trigger one event of each type available to you (entry, phone_use, intrusion) |
| **Expect** | `fd_severity.log`: `assigned=low` for entry, `medium` for phone_use, `high` for intrusion |
| **Pass** | Each type gets its mapped severity | ☐ |

### 2.2 Unknown event type

| **Do** | Trigger an event type absent from the map (e.g. `occupancy`) |
| **Expect** | `assigned=<documented default> source=fallback` |
| **Pass** | No crash, fallback recorded explicitly | ☐ |

### 2.3 `fd_min_severity_alert`

| **Set** | `fd_min_severity_alert=medium` |
| **Do** | Trigger a head-count entry (severity `low`) |
| **Expect** | `fd_severity.log`: `alert=0`. Row written to `alert_logs`, but absent from the alert feed |
| **Pass** | Row exists in DB, not shown in the feed | ☐ |

### 2.4 `fd_min_severity_snapshot`

| **Set** | `fd_min_severity_snapshot=high` |
| **Do** | Trigger a `phone_use` (severity `medium`) |
| **Expect** | `snapshot=0`; no new file in `media/snapshots/` |
| **Pass** | Row written, no snapshot file created | ☐ |
| **Why it matters** | This is the direct control on snapshot disk growth | |

---

## Part 3 — Deduplication

### 3.1 `fd_dedup_window`

| **Set** | `fd_dedup_window=60`, `fd_mode=enforce` |
| **Do** | Same person triggers the same event twice, ~10 s apart |
| **Expect** | `fd_dedup.log`: `verdict=SUPPRESS age=10.x fd_dedup_window=60` |
| **Pass** | Exactly one alert | ☐ |

### 3.2 Outside the window

| **Do** | Repeat, but wait **70 s** between the two |
| **Expect** | `age=70.x` and **no** SUPPRESS |
| **Pass** | Two alerts | ☐ |

### 3.3 `fd_dedup_scope=track` — two people must both alert

| **Set** | `fd_dedup_scope=track` |
| **Do** | **Two people** trigger the same event at the same moment |
| **Expect** | Two JUDGE lines, different `track=`, both ACCEPT |
| **Pass** | **Two alerts.** One alert here is a FAILURE | ☐ |

> This is the single most dangerous failure mode of deduplication: suppressing a second
> real person because they resemble the first. Test it deliberately.

### 3.4 `fd_dedup_scope=camera`

| **Set** | `fd_dedup_scope=camera` |
| **Do** | Repeat 3.3 |
| **Expect** | Second person: `verdict=SUPPRESS ... fd_dedup_scope=camera` |
| **Pass** | One alert, and the log explains why | ☐ |
| **Note** | Confirms the scope is real, and shows why `camera` scope is dangerous for people-events | |

### 3.5 Different cameras never dedup against each other

| **Do** | Trigger the same event type on two cameras simultaneously |
| **Expect** | Both ACCEPT |
| **Pass** | Two alerts | ☐ |

---

## Part 4 — Rate limiting and storms

### 4.1 `fd_max_alerts_per_minute`

| **Set** | `fd_max_alerts_per_minute=5`, `fd_mode=enforce` |
| **Do** | Trigger 8+ events on one camera inside a minute (walk back and forth over the line) |
| **Expect** | First 5 ACCEPT; the rest `fd_ratelimit.log`: `verdict=SUPPRESS window_alerts=6 fd_max_alerts_per_minute=5` |
| **Pass** | Exactly 5 alerts; the surplus counted in the log | ☐ |

### 4.2 The window rolls

| **Do** | Wait 60 s, trigger again |
| **Expect** | ACCEPT — the window has moved on |
| **Pass** | Alert appears | ☐ |

### 4.3 `fd_storm_threshold` / `fd_storm_action=demote`

| **Set** | `fd_storm_threshold=10`, `fd_storm_action=demote` |
| **Do** | Generate 12+ events in the storm window |
| **Expect** | `fd_ratelimit.log`: `event=STORM verdict=DEMOTE window_alerts=12` |
| **Pass** | Later rows written at `severity=info`, not dropped | ☐ |

### 4.4 Storm isolation between cameras

| **Do** | Storm camera A; trigger one normal event on camera B |
| **Expect** | Camera B: ACCEPT |
| **Pass** | **Camera B is unaffected.** Cross-camera suppression is a critical failure | ☐ |

---

## Part 5 — Evidence floors

### 5.1 `fd_min_confidence`

| **Set** | `fd_min_confidence=0.50` |
| **Do** | Trigger a `phone_use` (typical confidence 0.2–0.6) |
| **Expect** | `fd_evidence.log`: `confidence=0.3x fd_min_confidence=0.50 verdict=SUPPRESS` |
| **Pass** | Low-confidence events suppressed, the value recorded | ☐ |

### 5.2 Zero is inert

| **Set** | `fd_min_confidence=0.0` |
| **Expect** | No EVIDENCE suppression at all |
| **Pass** | Confirms the default cannot silently filter | ☐ |

### 5.3 `fd_min_evidence_frames`

| **Set** | `fd_min_evidence_frames=4` |
| **Do** | Brief phone use (2–3 detections) |
| **Expect** | `evidence=2/5 fd_min_evidence_frames=4 verdict=SUPPRESS` |
| **Pass** | Suppressed, both numbers logged | ☐ |

### 5.4 Events without a confidence field

| **Do** | Trigger a head-count entry (no `data.confidence`) |
| **Expect** | Not suppressed; log records the field was absent |
| **Pass** | **No crash, no accidental suppression** | ☐ |

---

## Part 6 — Ignore zones and hours

### 6.1 `fd_ignore_zones` — inside

| **Set** | Draw a zone over a known false-positive source (a TV, a poster, a reflective door) |
| **Do** | Cause an event whose point falls inside it |
| **Expect** | `fd_zones.log`: `point=(x,y) zone=0 verdict=SUPPRESS reason=inside_ignore_zone` |
| **Pass** | Suppressed, with the point logged so the geometry is checkable | ☐ |

### 6.2 Outside the zone

| **Do** | Same event type, just outside the zone |
| **Expect** | ACCEPT |
| **Pass** | Alert appears — proves the zone is bounded, not global | ☐ |

### 6.3 Zone survives a resolution change

| **Do** | Change the camera's `process_frame_rate` or stream resolution; repeat 6.1 |
| **Expect** | Still suppressed — geometry is normalised 0–1 |
| **Pass** | Behaviour unchanged | ☐ |

### 6.4 `fd_ignore_hours`

| **Set** | A window covering **now** (e.g. now−5 min to now+30 min) |
| **Do** | Trigger any event |
| **Expect** | `event=HOURS ... verdict=DEMOTE reason=inside_ignore_hours` |
| **Pass** | **Demoted, not dropped** — a security event in a quiet window still exists | ☐ |

### 6.5 Overnight window

| **Set** | `22:00–06:00` and test at 23:00 and at 12:00 |
| **Expect** | Demoted at 23:00, normal at 12:00 |
| **Pass** | Wrap-around handled | ☐ |

---

## Part 7 — Warm-up

### 7.1 `fd_warmup_seconds`

| **Set** | `fd_warmup_seconds=30` |
| **Do** | Restart, then immediately walk in front of the camera |
| **Expect** | `fd_warmup.log`: `since_start=8.2 fd_warmup_seconds=30 verdict=SUPPRESS` |
| **Pass** | Suppressed within the window | ☐ |

### 7.2 After warm-up

| **Do** | Wait 35 s, repeat |
| **Expect** | ACCEPT |
| **Pass** | Alert appears | ☐ |

### 7.3 Per camera, not global

| **Do** | Start camera A, wait 60 s, then start camera B and trigger on B |
| **Expect** | B warms up independently |
| **Pass** | B suppressed, A unaffected | ☐ |

---

## Part 8 — Repeated-source map

### 8.1 A fixture becomes suppressed

| **Set** | `fd_repeat_hits=10`, `fd_repeat_window=30`, `fd_repeat_min_tracks=2` (lowered for testing) |
| **Do** | Let a static false-positive source fire repeatedly for ~2 minutes |
| **Expect** | `fd_repeat.log`: `cell=(x,y) hits=11 span=34.2 tracks=2 verdict=SUPPRESS` |
| **Pass** | Suppressed after the thresholds are met, with all three numbers logged | ☐ |

### 8.2 One person is never furniture

| **Do** | ONE person triggers repeatedly from roughly the same spot for 2 minutes |
| **Expect** | `tracks=1` → **not** suppressed |
| **Pass** | Alerts continue. `fd_repeat_min_tracks` is what protects a genuine user | ☐ |

### 8.3 Cells expire

| **Do** | After 8.1, stop the source for longer than `fd_repeat_ttl`, then trigger there again |
| **Expect** | ACCEPT — the cell expired |
| **Pass** | A location is never permanently blacklisted | ☐ |

---

## Part 9 — Admin panel

| # | Check | Pass | ✅ |
|---|---|---|---|
| 9.1 | Every parameter in §4 is editable | All present, none missing | ☐ |
| 9.2 | Save persists | Reload the page — values retained | ☐ |
| 9.3 | Survives a restart | Restart the server — values retained | ☐ |
| 9.4 | Live counters update | Suppression counts move as events occur | ☐ |
| 9.5 | Per-camera override wins | Set global 60, camera-48 override 120 → `fd_dedup_window=120` in camera 48's log lines | ☐ |
| 9.6 | Invalid input rejected | Enter `-5` → clear error, nothing half-applied | ☐ |
| 9.7 | Non-admin refused | Log in as viewer → 403 | ☐ |
| 9.8 | Change is audited | `fd_config.log`: `event=CONFIG_CHANGE actor=... before=60 after=90` | ☐ |
| 9.9 | Ignore-zone drawing | Draw with `ZoneEditor`, save, verify it appears in the log's `fd_ignore_zones` count | ☐ |
| 9.10 | Zones persist per camera | Camera A's zones do not appear on camera B | ☐ |

---

## Part 10 — Logging itself

| # | Check | Pass | ✅ |
|---|---|---|---|
| 10.1 | All 10 files created at start-up | Present with SESSION headers | ☐ |
| 10.2 | Every line is parseable | `false_detection_report.py` reads the whole capture with no errors | ☐ |
| 10.3 | Every suppression names its rule | No SUPPRESS line without `rule=` and `reason=` | ☐ |
| 10.4 | Every line carries its parameters | Pick 10 random lines — each states the values that decided it | ☐ |
| 10.5 | `checks_passed` on ACCEPT | Every rule listed; a disabled rule is visibly absent | ☐ |
| 10.6 | Level `1` excludes ACCEPT | Restart at `FD_DEBUG=1` — only SUPPRESS/DEMOTE/METRICS | ☐ |
| 10.7 | Rotation works | Force >`FD_LOG_MAX_MB` — `.1` backup appears, disk stable | ☐ |
| 10.8 | `FD_LOG_SPLIT=false` | Single combined file, same content | ☐ |
| 10.9 | Metrics are periodic | `fd_metrics.log` gains a line every `FD_METRICS_EVERY` | ☐ |
| 10.10 | Report matches reality | Report's suppression totals equal the counts you observed by hand | ☐ |

**10.10 is the integration test for this whole document.** If the report disagrees with
what you saw, the logs cannot be trusted as evidence.

---

## Part 11 — Regression (must pass before sign-off)

| # | Check | Pass | ✅ |
|---|---|---|---|
| 11.1 | Head-count accuracy unchanged | Walk the line 10 times → same count as before the layer | ☐ |
| 11.2 | Phone detection unchanged | Use a phone → alerts as before | ☐ |
| 11.3 | Attendance unchanged | Check in → attendance row created | ☐ |
| 11.4 | Face recognition unchanged | Recognised name still shown on the live view | ☐ |
| 11.5 | Live view unchanged | Overlays, lines, bands all render as before | ☐ |
| 11.6 | No new DB tables | `\\dt` → same tables plus nothing | ☐ |
| 11.7 | Perception child has no DB | `grep -r "psycopg2\\|asyncpg" backend/services/false_detection.py` → nothing | ☐ |
| 11.8 | Writer keeps up | Alert timestamps stay current under load, no growing lag | ☐ |
| 11.9 | Automated tests | `python -m pytest tests/ -q` green | ☐ |

---

## Part 12 — Sign-off

| Requirement | ✅ |
|---|---|
| Parts 0–11 complete, every box ticked with a log line seen | ☐ |
| 24 h `observe` capture reviewed and accepted | ☐ |
| Rollback rehearsed on the production box | ☐ |
| Logs sent for independent review | ☐ |
| Zero missed genuine events during staged enablement | ☐ |

**Tested by:** ______________  **Date:** ____________  **Build/commit:** ____________

---

## Appendix A — Fast triage from a log

| Symptom | Look at | Likely cause |
|---|---|---|
| Alerts stopped entirely | `fd_decisions.log` verdicts | Over-suppression — check which `rule=` dominates |
| Nothing in any log | Start-up banner | `FD_DEBUG` never reached the process |
| Files exist but stay empty | `fd_enabled` in SESSION | Layer off, or no events reaching the writer |
| One rule suppresses everything | `fd_metrics.log` `by_rule=` | That rule's threshold is wrong |
| A real event was missed | `fd_decisions.log` at that timestamp | The recorded `rule=`/`reason=` names the culprit |
| Suppression rate ~0% | SESSION header | Parameters not applied — compare against what you set |
| Counts differ from the screen | `fd_severity.log` `alert=` | `fd_min_severity_alert` is hiding rows that exist in the DB |

## Appendix B — Recording template

```
TEST ID:        3.3  (dedup scope = track, two people)
TIME:           2026-08-26 14:32:10
CAMERA:         48
CONFIG:         fd_dedup_window=60 fd_dedup_scope=track fd_mode=enforce
ACTION:         Two people crossed the line together
EXPECTED:       Two JUDGE lines, different track=, both ACCEPT, two alerts
OBSERVED:       <paste the log lines>
ALERTS SEEN:    <count from the dashboard>
RESULT:         PASS / FAIL
NOTES:
```
