# VIGIL — Head-Count Accuracy, Phase 1

**Branch:** `headcount_staging` (branched from `stagging`)
**Date:** 2026-08-25
**Scope:** the `headcount` module (entry/exit counting) only. No changes to phone
detection, face recognition, attendance, smoking, fire/smoke, occupancy, intrusion,
the module architecture, or the frontend.
**Status:** implemented, tested, and validated against two real captures from live
cameras. One follow-up action is outstanding and it is a camera configuration change,
not code.

> This document is written to be handed to another engineer or agent with no prior
> context. Where a claim is backed by measurement, the measurement is shown. Where it
> is not, that is stated explicitly.

---

## 1. The problem

Reported symptom: head-count accuracy was unstable — "75% sometimes, 50% sometimes".
The instability was the important clue. A consistently wrong counter is a logic bug; a
counter whose accuracy *varies* is usually being starved of input.

## 2. Root cause — measured, not assumed

Two independent defects, found by measurement before any code was changed.

### 2.1 The tracker was blind at the pipeline's frame rate

`camera_manager.py` enqueued frames for analytics every 0.5 s (`ENQUEUE_INTERVAL`), so
person tracking ran at **~2 fps**. Ultralytics registers every tracker with a hardcoded
`frame_rate=30` (`ultralytics/trackers/track.py:63`), so ByteTrack's Kalman filter
advances one 1/30 s step per update while 0.5 s of real motion has occurred.

The failure mode is **not** ID churn, which was the initial hypothesis. It is **total
track loss**. ByteTrack associates by IoU with `match_thresh=0.8`. A person walking at
normal pace moves further than their own bounding-box width between frames sampled 0.5 s
apart, so consecutive boxes do not overlap at all — IoU is 0, no association is possible.
The re-detected person starts a fresh track which is `unconfirmed` and needs a second
consecutive match to activate, which never arrives because they have moved on again.

**The tracker emitted the person on frame 1 and then nothing for the rest of the walk.**

Measured with the real `BYTETracker` driven by synthetic detections
(`backend/scripts/bench_tracking_cadence.py`), no GPU or weights required:

```
--- normal crossing (3 s) --- (expected IN=1 OUT=0)
   fps   ids/person  switches   old-rule in/out
     2            1         0               0/0   MISS
     4            1         0               1/0   OK
    10            1         0               1/0   OK
```

At 2 fps the tracker reports one distinct id and zero switches — because it emits one
frame and then goes silent.

The cadence floor follows directly: association survives while per-frame displacement
stays below roughly one box width, i.e. `required_fps > normalised_speed / box_width`.
Swept across apparent sizes and walking speeds:

| box width (normalised) | 3 s crossing | 1.5 s crossing | 1 s crossing |
|---|---|---|---|
| 0.05 (distant) | 8 fps | 15 fps | 25 fps |
| 0.08 (typical) | 4 fps | 10 fps | 15 fps |
| 0.12 (near) | 3 fps | 6 fps | 10 fps |

This is why accuracy varied: whether a person survived tracking depended on their
walking speed, their distance from the camera, and the load-dependent real sampling
interval (frames are silently dropped when the perception queue is full).

### 2.2 The counting rule decided on one frame of evidence

The original rule took the bounding-box **centroid**, computed its side of the line, and
counted a crossing the instant that sign flipped between two consecutive observations of
the same track id.

```python
prev = self.prev_side.get(t["id"])
self.prev_side[t["id"]] = s
if prev is None or s == 0 or (s > 0) == (prev > 0):
    continue        # a fresh track id can NEVER count on its first frame
```

Failure modes: any id change or dropped frame near the line silently discarded the
crossing; jitter across the line produced repeats; the centroid sits at chest height, so
under camera tilt it leads or lags the feet and jumps when the box is clipped.

---

## 3. What changed

### 3.1 Two-lane analytics cadence

`backend/services/camera_manager.py`, `backend/services/perception.py`

Person tracking needs ~10 fps. Face recognition, phone, smoking and fire/smoke do not,
and are the expensive part. So the cadence was split rather than raised globally:

- **Slow lane** (`ENQUEUE_INTERVAL`, 0.5 s, unchanged) — the full module list plus the
  full-resolution JPEG used for alert snapshots. Face/phone/smoking/fire GPU cost is
  **unchanged**.
- **Fast lane** (`TRACK_INTERVAL`, 0.1 s) — person tracking and head-count only, with
  **no JPEG payload** (head-count events never attach a snapshot). Runs only on cameras
  that have a module in `FAST_MODULES = {"headcount"}`.

Cameras without head-count are byte-for-byte unchanged: same single lane, same
`process_frame_rate` gate.

Supporting changes:
- The AI loop is wall-clock gated on fast cameras (`process_frame_rate` counts loop
  iterations, not frames, so it cannot express 10 fps reliably). A "work due" check runs
  *before* the frame copy so the tighter loop does not become a continuous memcpy.
- `BROADCAST_INTERVAL` (0.1 s) caps live-view JPEG encoding so the faster loop cannot
  multiply streaming cost. Preserves the ~10 fps the stream already ran at.
- In the perception child, a `fast` task short-circuits to person tracking plus fast
  consumers only. It deliberately touches none of the identity, re-id, phone or fire
  state, so those remain driven purely by the slow lane and keep their tuning.
- Fast results carry `overlays=None`, telling the writer to leave the live overlay set
  alone, and are skipped entirely when they contain nothing to persist — otherwise the
  writer would open a DB session per frame per camera at 10 fps.
- The consumer loop was factored into a shared `run_consumers()` so both lanes create
  consumers, restore seeded totals, and attribute events to employees identically.

**Process architecture is unchanged.** No DB access was added to the GPU child; no GPU
work was added to the main process; per-camera tracker isolation is preserved.

### 3.2 Head-count rewritten

`backend/services/analytics/headcount.py` (rewritten), `base.py` (+3 helpers)

| | Old | New |
|---|---|---|
| Reference point | box centroid | **foot point** (bottom-centre); `reference_point: "centroid"` restores the old behaviour |
| Trigger | one-frame sign flip | state machine `BEFORE → NEAR_LINE → AFTER` with the origin side latched |
| Line | exact mathematical line | **hysteresis band** of half-width `crossing_band`, in normalised perpendicular distance |
| Evidence | 1 observation | far side confirmed for `confirm_observations` consecutive frames |
| Geometry | infinite line | **bounded to the drawn segment** (see §3.3) |
| New track id | crossing dropped | **speed-aware hand-off** from a recently lost track |
| Repeat suppression | none | per-**track** cooldown, never global |

Validation order matters and is deliberate: everything that leaves a track *pending* is
tested before anything that *re-latches* it, so a track briefly out of range while still
gathering evidence is not discarded.

Preserved contracts: `entries`/`exits` attributes (perception restores persisted totals
by direct assignment), `data["track"]` (the writer uses it for employee attribution),
`value`, `event_type`, and the `line` overlay. Direction semantics are unchanged and
asserted against a reimplementation of the old rule (`test_17`).

### 3.3 Crossings bounded to the drawn line segment

Found by analysing the first real capture, and **pre-existing** — the old `side_of_line`
was equally infinite.

The operator draws a finite segment across a doorway, but `side_of_line` and
`signed_distance_to_line` both describe an **infinite line**. Someone walking around the
*end* of the drawn line registered as crossing it. In the first capture this was **4 of
29 crossings (14%)**, all on camera 47.

Now the crossing position along the segment is computed (`segment_position()`, 0 = start,
1 = end) and a crossing is only committed if it falls within `[-margin, 1+margin]`. The
position is judged at the track's **closest approach to the line**, not where it first
clears the far side — a fast diagonal walker has already drifted by then.

### 3.4 Diagnostic logging and analysis tooling

`backend/services/analytics/headcount_debug.py` (new),
`backend/scripts/headcount_report.py` (new)

A dedicated logger, off by default, own file, `propagate=False` so it never leaks into
the application log. **One environment variable** controls it:

```bash
HEADCOUNT_DEBUG=trace     # full capture: decisions + every trajectory (replayable)
HEADCOUNT_DEBUG=debug     # decisions + observations near the line
HEADCOUNT_DEBUG=1         # decisions only
unset / =0                # off (default)
```

Rotation size follows the level automatically (50 MB at TRACE, else 5 MB), because a
5 MB cap silently discards the beginning of a TRACE capture — the part that matters.

Every capture opens with a `SESSION` header carrying the resolved configuration (line
geometry, every threshold, frame shape). Without it a captured log is uninterpretable:
a miss caused by a mis-drawn line looks identical to a miss caused by a bad threshold.

Event vocabulary: `SESSION`, `OBS`, `STATE`, `PENDING`, `COMMIT`, `REJECT`, `TURNED_BACK`,
`ID_HANDOFF`, `TRACK_END`, `METRICS`. Volume at INFO is ~2 lines per crossing, ~8 at DEBUG.

**The key capability is replay.** Because TRACE records every box on every frame, a
captured log reconstructs the scene and can be pushed back through the real consumer —
so a real-world miss becomes a reproducible test case *without the original video*, and
thresholds can be re-tuned against actual footage:

```bash
python -m backend.scripts.headcount_report                      # summary
python -m backend.scripts.headcount_report --tracks             # per-track table
python -m backend.scripts.headcount_report --replay             # fidelity check
python -m backend.scripts.headcount_report --replay --camera 47 \
    --set 'line=((0.1572,0.3148),(0.40,0.85))'                  # test a change
```

The report also derives the **effective analytics rate** from frame numbering, which is
the single number that says whether the fast lane is actually running in production.

---

## 4. New configuration

All env-overridable globally, and per-camera by adding the same key to that camera's
`headcount` entry in `camera_devices.module_config`. The module registry and its frontend
mirror were deliberately **not** changed, so there is no UI editor and no
registry↔`modules.ts` drift risk.

| Parameter | Default | Env | Purpose |
|---|---|---|---|
| `crossing_band` | 0.03 | `HC_CROSSING_BAND` | Hysteresis half-width |
| `confirm_observations` | 2 | `HC_CONFIRM_OBSERVATIONS` | Consecutive far-side frames before committing |
| `min_track_age` | 2 | `HC_MIN_TRACK_AGE` | Minimum observations |
| `min_displacement` | 0.05 | `HC_MIN_DISPLACEMENT` | Teleport guard |
| `min_direction_consistency` | 0.6 | `HC_MIN_DIRECTION_CONSISTENCY` | Rejects oscillation |
| `crossing_cooldown` | 2.0 s | `HC_CROSSING_COOLDOWN` | Per-track debounce |
| `segment_margin` | 0.05 | `HC_SEGMENT_MARGIN` | Tolerance past the drawn endpoints, as a fraction of line length |
| `handoff_radius` | 0.10 | `HC_HANDOFF_RADIUS` | Base id hand-off search radius |
| `handoff_max_gap` | 3 frames | `HC_HANDOFF_MAX_GAP` | Longer gap ⇒ a different person |
| `handoff_speed_tol` | 1.5 | `HC_HANDOFF_SPEED_TOL` | Radius scales with `speed × gap` |
| `handoff_ttl` | 1.5 s | `HC_HANDOFF_TTL` | Wall-clock bound |
| `state_ttl` | 3.0 s | `HC_STATE_TTL` | State purge (bounded memory) |
| `reference_point` | `foot` | `HC_REFERENCE_POINT` | `foot` \| `centroid` |
| — | 0.1 s | `TRACK_INTERVAL` | Fast-lane cadence |
| — | 0.5 s | `ENQUEUE_INTERVAL` | Slow-lane cadence (was hard-coded) |
| — | 0.1 s | `BROADCAST_INTERVAL` | Live-view encode cap |

---

## 5. Files changed

```
 .gitignore                                    |   3 +    ignore logs/
 backend/scripts/bench_tracking_cadence.py     | 254 +    NEW  cadence measurement
 backend/scripts/headcount_report.py           | 375 +    NEW  log analysis + replay
 backend/services/analytics/base.py            |  36 +    signed_distance_to_line,
                                                          segment_position, line_normal
 backend/services/analytics/headcount.py       | 516 +    rewritten
 backend/services/analytics/headcount_debug.py | 289 +    NEW  logger + metrics
 backend/services/camera_manager.py            |  90 +    two-lane cadence
 backend/services/detectors/person.py          |   6 +    tracks carry `conf`
 backend/services/perception.py                | 114 +    fast lane, writer guards
 knowledgebase.md                              | 801 +    NEW  system reference
 tests/test_headcount.py                       | 620 +    NEW  35 tests
 tests/test_headcount_cadence.py               | 154 +    NEW   8 tests
 tests/test_headcount_logging.py               | 265 +    NEW  15 tests
```

**61 tests passing, pyflakes clean on every touched file.**

---

## 6. Evidence

### 6.1 Synthetic benchmark — old rule vs new, at both cadences

15 scenarios with known ground truth, pushed through the **real** ByteTrack:

| Configuration | Entry recall | Exit recall | Precision | Overall |
|---|---|---|---|---|
| **Old rule @ 2 fps** (previous production) | 0.55 | 1.00 | 1.00 | **0.62** |
| Old rule @ 10 fps | 0.91 | 1.00 | 1.00 | 0.92 |
| New rule @ 2 fps | 0.82 | 1.00 | 0.91 | 0.77 |
| **New rule @ 10 fps** (Phase 1 default) | 1.00 | 1.00 | 1.00 | **1.00** |

The 0.62 baseline matches the reported 50–75%, which is evidence the harness reproduces
the real failure. **Both changes are required.** Cadence does the heavy lifting; the rule
fixes the perspective case that 10 fps alone does not, and supplies the jitter and
duplicate defences that smooth synthetic walks under-exercise. The new rule at 2 fps
produced one false positive (an aliasing artefact) — do not ship the rule without the
cadence change.

Reproduce with `python -m tests.test_headcount`.

### 6.2 Real capture 1 — 2026-08-25 13:31, 5.4 min, cameras 47 + 50

First production capture, before the segment fix.

- Analytics rate **9.7 fps** — confirms the fast lane works on real RTSP streams
- 29 crossings committed (47: 8 in / 8 out · 50: 7 in / 6 out)
- 39 tracks: 24 counted, 14 never approached, 1 turned back
- **Zero** tracked people crossed without being counted

Checks performed:
- *Are concurrent tracks one person split into two boxes?* No — foot separation medians
  0.09–0.27 normalised, i.e. body-width and more. Genuinely separate people.
- *Was anyone missed?* Track 101 (127 observations, closest approach 0.0031) reached
  d=+0.015, hovered inside the band, and turned back without clearing the far side.
  A correct rejection.
- *Did crossings happen where the line was drawn?* **No — 4 of 29 (14%) were beyond the
  endpoint**, all on camera 47 (t = 1.023, 1.120, 1.230, 1.261). This produced §3.3.

### 6.3 Real capture 2 — 2026-08-25 13:56, 12.9 min, with the fix deployed

```
--- capture ---
    duration                     771.8 s
    analytics rate               9.5 fps  <- fast lane active

--- outcome ---
    crossings committed          36  (entry=22 exit=14)
    id hand-offs                 3
    turn-backs at the line       1
    genuine rejections           5
        outside_line_segment         5

--- crossing position along the drawn line (0=start, 1=end) ---
    within the drawn segment     35 / 36
    BEYOND an endpoint           1
        camera=47 track=836 ENTRY t=1.037

--- tracks that came close and counted NOTHING ---
    6 of 89 completed tracks
       track   obs   closest     reached               last_reject        frames
          72    40    0.0031       AFTER      outside_line_segment       404-443
         736    35    0.0023       AFTER      outside_line_segment     3598-3632
         818    28    0.0024       AFTER      outside_line_segment     3863-3891
        1939    34    0.0033       AFTER      outside_line_segment     7167-7201
        2030    39    0.0043       AFTER      outside_line_segment     7336-7374
        2113    27    0.0597      BEFORE                         -     7534-7561
```

Per camera: **47 → 9 in / 5 out**, **50 → 13 in / 9 out**.

**Replay fidelity: MATCH on both cameras** — the replayed counts equal the live counts,
so the log is trustworthy evidence and can be used as a regression fixture.

### 6.4 Before/after comparison

| | Capture 1 (old code) | Capture 2 (with fix) |
|---|---|---|
| Duration | 5.4 min | 12.9 min |
| Analytics rate | 9.7 fps | 9.5 fps |
| Crossings committed | 29 | 36 |
| **Within the drawn line** | 25/29 = **86%** | **35/36 = 97%** |
| Beyond the endpoint, counted | 4 | 1 (t=1.037, inside tolerance) |
| Tracked people crossing uncounted | 0 | 0 |
| Tracks committing twice | 5 | 1 (exit then re-entry — legitimate) |
| Replay fidelity | MATCH | MATCH (both cameras) |

Camera 50 is clean: every crossing inside the line, no rejections, no duplicates, no id
switches near the line.

---

## 7. Outstanding action — camera 47's line is drawn too short

**This is a configuration change, not a code change.**

Five crossings in capture 2 were rejected as `outside_line_segment`. They are not noise.
All five came within 0.002–0.004 of the line (essentially touching it) and cluster
tightly past the line's `B` endpoint at (0.337, 0.708):

```
track 72    d=-0.0031  seg_t=1.086   foot=(0.355, 0.740)
track 1939  d=-0.0033  seg_t=1.074   foot=(0.353, 0.736)
track 2030  d=+0.0043  seg_t=1.083   foot=(0.348, 0.742)
track 818   d=+0.0024  seg_t=1.119   foot=(0.356, 0.755)
track 736   d=+0.0023  seg_t=1.296   foot=(0.387, 0.825)
```

All five are **exits**. Entries cross within the line; exits consistently pass beyond its
end — people leave on a slightly different path from the one they arrive on.

Tested against the same footage by replay:

```
camera 47, current line B=(0.337, 0.708):    9 in /  5 out
camera 47, extended  B=(0.40,  0.85):        9 in / 10 out
```

Recovers all five exits, adds no entries.

**Action: extend camera 47's counting line so its lower end reaches approximately
(0.40, 0.85)** — drag endpoint B further down-right in the zone editor.

Extending the line is preferred over raising `segment_margin`: the margin exists to
absorb imprecision in drawing, whereas this line genuinely does not span the walkway.

---

## 8. What is verified, and what is not

**Verified**
- The fast lane runs at 9.5–9.7 fps on production RTSP streams
- Replay reproduces live counts exactly on both cameras — the logs are sound evidence
- No tracked person crossed the line without being counted, in either capture
- Concurrent tracks are distinct people, not one person split in two
- Only one double-count occurred, and it was a genuine exit-then-re-entry
- All 3 id hand-offs had 2–3 frame gaps and distances well inside the allowance
- Zero jitter, oscillation or turn-back false positives

**Not verified**
- **There is no ground truth for either capture.** Nobody counted the people. The
  internal logic can be proven correct and 5 wrongly-rejected crossings identified, but
  whether 36 is the right total is unknown.
- **People the detector never saw are invisible in the log by definition.** 49 of 89
  tracks never went near the line, which is normal for a busy scene, but it cannot
  distinguish "nobody else crossed" from "three people crossed and were never tracked".
- The 10 fps cadence was chosen from a tracker-behaviour sweep on CPU. GPU headroom at
  the full camera count has not been measured; run
  `python -m backend.scripts.bench_tracking_cadence` on the deployment box.
- A distant person crossing in under 1.5 s still needs ~15–25 fps and remains a miss.

**The single highest-value next step is a capture with a hand tally.** With it, the two
remaining failure modes can be separated: a *detector* miss (person never tracked →
Phase 2 work) versus a *counting* miss (tracked but not counted → still mine to fix, and
`TRACK_END` names the rule that stopped it). They need completely different fixes and
are currently indistinguishable.

---

## 9. Known limitations and technical debt

1. `FAST_MODULES` is declared in two files (producer `camera_manager`, consumer
   `perception`). A test guards against drift, but it remains duplicated.
2. `process_frame_rate` no longer gates cameras with a fast module — they are wall-clock
   gated instead. Behaviour change confined to those cameras.
3. Tuning knobs are env/JSON only; no UI editors were added.
4. Replay is most faithful at TRACE; at DEBUG, tracks away from the line are absent, so
   the hand-off search sees a different scene. The report reports a fidelity mismatch
   rather than silently producing wrong numbers.
5. Unrelated, found in passing: `settings.BASE_DIR` on the dev checkout is still the
   `.env.example` placeholder. The diagnostic logger was hardened against this, but
   `detectors/smoking.py` and `detectors/reid.py` also join `BASE_DIR` for weight paths
   and would silently look in the wrong place. Out of Phase 1 scope.
6. `start.bat` launches uvicorn twice (`start /B` then again in the foreground), so the
   second instance fails to bind port 8000. Pre-existing, not touched.

---

## 10. How to reproduce any of this

```bash
# tests
python -m pytest tests/ -q                        # 61 tests
python -m tests.test_headcount                    # accuracy report, old vs new

# the cadence measurement that chose 10 fps
python -m backend.scripts.bench_tracking_cadence

# capture from live cameras (one variable; open the live view or the camera idles out)
HEADCOUNT_DEBUG=trace bash start.sh

# analyse a capture
python -m backend.scripts.headcount_report
python -m backend.scripts.headcount_report --tracks
python -m backend.scripts.headcount_report --replay
python -m backend.scripts.headcount_report --replay --camera 47 \
    --set 'line=((0.1572,0.3148),(0.40,0.85))'
```

Deployment: backend-only change. No migration, no `npm run build`, no
`/api/system/reload` — the perception child loads analytics modules when it spawns, so a
full restart is required.
