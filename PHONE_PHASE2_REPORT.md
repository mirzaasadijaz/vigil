# VIGIL — Phase 2: Mobile Phone Usage Detection

**Branch:** `headcount_staging` (continues from Phase 1)
**Date:** 2026-08-25
**Scope:** the `mobile` module only. No changes to head-count logic, face recognition,
attendance, smoking, fire/smoke, intrusion, occupancy, the module architecture, the
database schema, or the frontend.

> Written to be handed to another engineer or agent with no prior context. Where a claim
> is backed by measurement, the measurement is shown. Where it is not, that is stated.

---

## A. Root cause

Four failure modes were separable, and they are **not** equally important.

### A.1 Camera resolution — the dominant failure (detector)

Measured from the real Phase 1 capture, which records every person box on every frame
(`python -m backend.scripts.bench_phone_scale --log logs/headcount_debug.log`):

| camera | det frame | median person box | implied phone | **after imgsz=640 letterbox** |
|---|---|---|---|---|
| 50 | 1280×720 | 61 × 176 px | 11.0 px | **5.5 px** |
| 47 | 960×1080 | 137 × 596 px | 24.7 px | **14.6 px** |

A hand-held phone is roughly 0.18× a person's shoulder width. YOLO's finest head is
stride 8, so detection collapses below roughly 16 px.

**On camera 50 the phone was 5.5 px in the image the detector actually saw.** That is
not a threshold problem and not a model problem — the information is not in the pixels
that reach the network. Camera 47 sat right at the floor. This alone explains most of
the "sometimes detects, sometimes doesn't" behaviour: whether a phone was detectable
depended on how close the person happened to be.

Note the frame is 1280 wide but ultralytics letterboxes to `imgsz=640` regardless, so
the det-frame width was buying nothing for this module.

### A.2 Association — a real but secondary failure

The old rule was: phone box centre inside person box → that person is holding it. A
standing person's box is ~0.4 × 1.8 m of image. A phone on the desk in front of them,
on a shelf behind them, or in a passer-by's hand all land "inside" it.

### A.3 Temporal — a real failure

One detection produced an event. Small-object detection on CCTV flickers constantly, so
single-frame noise became alerts. There was no notion of an episode; the `holding_phone`
flag was carried forward 1.5 s and that was the entire memory of the system.

### A.4 Cadence — a contributing failure

`PHONE_EVERY = 3` on top of the ~2 fps slow lane meant phone inference ran roughly
**0.67 times per second**. A phone visible for two seconds got about one chance to be
seen — and with no temporal confirmation, that one chance was also the whole decision.

---

## B. Model decision: **KEEP the COCO detector, change what it is shown**

No dedicated phone model has been added.

The evidence says the detector was never given a fair chance: at 5.5 px, a purpose-built
phone model would fail too. The lever that actually exists is **magnification**, so
inference now runs on the **upper-body crop of each person track**, letterboxed *up*:

| camera | phone, whole frame | phone, crop mode | gain |
|---|---|---|---|
| 50 — p10 (distant) | 3.0 px `BELOW FLOOR` | 31.4 px `marginal` | **5.2×** |
| 50 — median | 5.5 px `BELOW FLOOR` | **40.2 px `comfortable`** | **3.6×** |
| 50 — p90 (near) | 17.2 px `marginal` | 54.0 px `comfortable` | 1.6× |
| 47 — p10 | 10.4 px `BELOW FLOOR` | 26.4 px `marginal` | 1.5× |
| 47 — median | 14.6 px `BELOW FLOOR` | 26.5 px `marginal` | 1.1× |
| 47 — p90 | 19.9 px `marginal` | 34.2 px `comfortable` | 1.0× |

Cost scales with the number of **people**, not frame size, and crops are batched into a
single inference call.

Swapping in a dedicated model remains a one-line change if recall is still short after a
real capture — `PHONE_WEIGHTS` and `PHONE_CLASSES` are wired, the person backbone stays
shared, and the missing-weights contract is preserved. That decision should be made from
a capture, not from an assumption.

**A bug the benchmark caught in my own first attempt:** with `CROP_SIZE=320`, camera 47's
crop gain was **0.8× — cropping made phones smaller than whole-frame**, because its
people are 596 px tall and a 417 px crop was being letterboxed *down* into 320. The crop
size is now adaptive and never downscales (`_crop_imgsz`: at least the largest crop's
long side, rounded to the stride-32 grid, floored at 448 and capped at 640).

### Detector specification

| | |
|---|---|
| Architecture | YOLOv11s (the shared person backbone weights) |
| Weights | `models/yolo11s.pt`; override with `PHONE_WEIGHTS` |
| Classes | COCO 67 `cell phone`; override with `PHONE_CLASSES` (ids, or `all`) |
| Input | crop mode: adaptive 448–640, batched over people · frame mode: 640 |
| Confidence | **0.35** (`PHONE_CONF`) — raised from 0.20 after the first field capture; see §H2 |
| Degradation | Missing weights log once and return nothing; the perception child never crashes |
| GPU | One extra batched pass per medium-lane frame, on crops rather than the full frame |

---

## C. Architecture

Phase 1's two lanes become three. **The Phase 1 fast lane is unchanged.**

```
FAST     TRACK_INTERVAL   0.1 s   ~10 fps   person tracking + headcount
MEDIUM   PHONE_INTERVAL   0.2 s   ~5 fps    + phone perception
SLOW     ENQUEUE_INTERVAL 0.5 s   ~2 fps    everything (face, smoking, fire, ...)
```

```
Camera → camera_manager (picks the most inclusive due lane)
           ↓  mp.Queue, task carries lane + module subset
       perception child (GPU, no DB)
           ↓
       YOLOv11s + ByteTrack  →  person tracks
           ↓
       phone detector (upper-body crops, batched)
           ↓
       MobileUsage.process_phones()
           ├─ association scoring   (hand region, size sanity)
           └─ temporal state machine (per track)
           ↓
       phone_use event  →  result queue  →  PerceptionWriter  →  alert_logs
```

Key properties:

- **Lane isolation is enforced.** `_lane_enabled()` checks only that lane's modules, so a
  phone-only camera never enters the head-count fast lane and never pays for 10 fps
  person tracking. Covered by `test_phone_only_camera_does_not_enter_the_headcount_fast_lane`.
- **The slow lane is a genuine superset.** It stamps the medium timer when it fires, so
  it also *runs* phone inference — otherwise it would starve the phone cadence rather
  than contribute to it. (This was a bug in the first draft, caught by a cadence test.)
- **Only the fast lane skips the JPEG payload.** `phone_use` events attach snapshots, so
  medium-lane frames still carry one.
- **Sub-slow lanes send `overlays=None`**, so they never clobber the live overlay set that
  the slow lane owns — the Phase 1 rule, unchanged.
- No new process, no DB access in the child, no duplicated person detection, no new table.

### Association

A phone belongs to a person only if it falls in the part of their box a hand-held phone
can occupy, scored rather than boolean:

```
score = vertical × horizontal × size
  vertical    1.0 inside the hand band (head → hip), decaying over `hand_falloff`
  horizontal  1.0 inside the box, decaying across `hand_pad` (hands extend past the torso)
  size        1.0 while the phone is small relative to the person, decaying beyond
```

Multiplicative, so any single hard failure zeroes it, and **every component is logged**,
so no decision is opaque. Each detection is assigned to at most one person — the argmax —
and the runner-up is recorded so close calls are auditable.

### Temporal state machine (per track)

```
NO_PHONE ──hit──▶ CANDIDATE ──confirm_hits of last confirm_window──▶ CONFIRMED
                      │                                                  │
                      └──release_misses consecutive misses──▶ NO_PHONE   │
                                                                          │
                            release_misses consecutive misses ──▶ RELEASED
```

- The event fires **once**, on entry to CONFIRMED, with a per-track cooldown.
- Confirm and release thresholds are separate, so a one- or two-frame dropout does not
  end an episode.
- State is per **track**. There is deliberately no camera-level flag; several people can
  be confirmed simultaneously.
- **Identity is optional.** The consumer works from person tracks alone; the perception
  worker attaches `photo_base` afterwards *if* face recognition happens to have bound
  one. Somebody facing away or beyond recognition range is still eligible.

### Event confidence

Not detector confidence alone:

```
confidence = phone_confidence × association_confidence
```

with `phone_confidence`, `association_confidence`, `evidence` (hits/window) and
`duration` (detection latency) all reported separately in `data`.

---

## D. Configuration

Env default, per-camera override via that camera's `mobile` entry in `module_config`.

| Parameter | Default | Env | Purpose |
|---|---|---|---|
| — | 0.2 s | `PHONE_INTERVAL` | Medium-lane cadence (~5 fps) |
| `phone_mode` | `crop` | `PHONE_MODE` | `crop` \| `frame` |
| `phone_conf` | 0.20 | `PHONE_CONF` | Detector confidence floor |
| `phone_crop_size` | 448 | `PHONE_CROP_SIZE` | Minimum crop inference size |
| — | 640 | `PHONE_CROP_MAX` | Cap on the adaptive crop size |
| — | 640 | `PHONE_FRAME_IMGSZ` | Frame-mode inference size |
| — | 0.00 / 0.70 / 0.20 | `PHONE_CROP_TOP` / `_BOTTOM` / `_PAD` | Crop band and sideways pad |
| — | 24 px / 12 | `PHONE_MIN_CROP_PX` / `PHONE_MAX_CROPS` | Skip tiny crops; bound the batch |
| — | `models/yolo11s.pt` | `PHONE_WEIGHTS` | Detector weights |
| — | `67` | `PHONE_CLASSES` | Class ids, or `all` |
| `hand_top` / `hand_bottom` | 0.00 / 0.70 | `PHONE_HAND_TOP` / `_BOTTOM` | Association band |
| `hand_pad` | 0.20 | `PHONE_HAND_PAD` | Sideways tolerance |
| `hand_falloff` | 0.12 | `PHONE_HAND_FALLOFF` | Decay outside the band |
| `association_min` | 0.35 | `PHONE_ASSOC_MIN` | Minimum score to associate |
| `max_size_ratio` | 0.06 | `PHONE_MAX_SIZE_RATIO` | Phone/person area sanity |
| `confirm_hits` | 3 | `PHONE_CONFIRM_HITS` | Hits needed to confirm |
| `confirm_window` | 5 | `PHONE_CONFIRM_WINDOW` | Sliding window length |
| `release_misses` | 4 | `PHONE_RELEASE_MISSES` | Misses that end an episode |
| `cooldown` | 30 s | `PHONE_COOLDOWN` | Min seconds between events per track |
| `state_ttl` | 5 s | `PHONE_STATE_TTL` | State lifetime after a track vanishes |

`PHONE_EVERY` is **removed** — phone inference is no longer an Nth-frame throttle inside
the slow lane. Any existing setting is simply inert.

---

## E. Files changed

```
NEW  backend/services/analytics/diagnostics.py    shared logger + log parser (extracted)
NEW  backend/services/analytics/phone_debug.py    phone diagnostic instance
NEW  backend/scripts/phone_report.py              phone log analysis + replay
NEW  backend/scripts/bench_phone_scale.py         resolution/scale measurement
NEW  tests/test_phone.py                          33 tests
NEW  tests/test_phone_detector.py                 16 tests
NEW  tests/test_phone_logging.py                  11 tests

     backend/services/analytics/mobile.py         rewritten: association + state machine
     backend/services/detectors/phone.py          rewritten: crop mode, adaptive size,
                                                  graceful degradation (was dead code)
     backend/services/camera_manager.py           three-lane cadence
     backend/services/perception.py               medium lane, phone helper, inline pass
                                                  removed
     backend/services/analytics/headcount_debug.py  now a thin instance of diagnostics
     backend/scripts/headcount_report.py          uses the shared parser
     tests/test_headcount_cadence.py              +7 lane-isolation tests
     tests/test_headcount_logging.py              resets via the public reset()
```

The Phase 1 logger was **generalised, not duplicated** — `DiagnosticLog` is now shared,
and the log format's parser lives beside its writer so the two cannot drift.

---

## F. Tests

```
python -m pytest tests/ -q     →  133 passed
pyflakes                        →  clean on every file touched
```

| File | Tests | Covers |
|---|---|---|
| `test_phone.py` | 33 | all 18 required scenarios: phone in hand, on a table, outside the upper body, wrong nearby person, one-frame noise, intermittent detection, long use, put away, two people at once, no phone, two people one phone, no face recognition, track loss, disappear/reappear, multiple phones, distant/small, partial occlusion, camera-specific geometry |
| `test_phone_detector.py` | 16 | crop geometry, clipping, adaptive inference size (incl. the never-downscale regression), graceful degradation, class config, cross-crop dedupe |
| `test_phone_logging.py` | 11 | silent by default, session header, TRACE vs INFO, rejection reasons, report, **replay fidelity**, independence from the head-count log |
| `test_headcount_cadence.py` | 15 (7 new) | phone-only camera stays out of the fast lane, both cadences together, per-lane module sets, Phase 1 unchanged, allow-lists agree across processes |
| Phase 1 suites | 70 | unchanged and passing |

Phase 1 regression additionally verified end-to-end: replaying the real capture still
gives `camera 47: 9 in / 5 out` and `camera 50: 13 in / 9 out`, **fidelity MATCH** on
both.

---

## G. Accuracy

**No ground truth exists for phone use, so no precision/recall/F1 is claimed.** No
capture has been taken with the new pipeline, and nobody has recorded what really
happened. Reporting a number here would be fabrication.

What *is* measured:

- **Effective phone size**, from 4 658 real person observations across two cameras
  (§B). Camera 50's median phone goes from 5.5 px (below the detection floor) to 40.2 px
  (comfortable) — a **7.3× increase in effective resolution**.
- **Inference cadence**: ~0.67 fps → ~5–6 fps, a **7.5–9× increase in evaluation rate**,
  without changing any other module's cadence.
- **Phase 1 non-regression**: 133 tests, plus replay fidelity on the real capture.

To obtain real accuracy numbers:

```bash
PHONE_DEBUG=trace bash start.sh
# open the live views; note what actually happens:
#   camera 47: __ people used a phone, __ did not
#   camera 50: __ people used a phone, __ did not
python -m backend.scripts.phone_report --scale
python -m backend.scripts.phone_report --replay
```

The report separates the failure modes by itself: rejection reasons distinguish a
**detector** failure (nothing detected) from an **association** failure (detected, not
attributable) from a **temporal** failure (attributed, never persistent enough).

---

## H. Diagnostic tooling

```
log path         logs/phone_debug.log          (independent of the head-count log)
enable           PHONE_DEBUG=trace             # one variable; also =debug, =1, unset
analyse          python -m backend.scripts.phone_report
                 python -m backend.scripts.phone_report --tracks --scale
replay           python -m backend.scripts.phone_report --replay
retune           python -m backend.scripts.phone_report --replay --camera 3 \
                     --set association_min=0.25 --set confirm_hits=2
resolution       python -m backend.scripts.bench_phone_scale --log logs/headcount_debug.log
```

Levels: `INFO` decisions only · `DEBUG` + one `EVAL` line per track per evaluation ·
`TRACE` + a `DET` line for every raw detection with its full association breakdown.

Sample lines:

```
event=DET     camera=3 frame=42 phone=(612.0,318.1,635.0,341.9) phone_conf=0.6100
              source=crop best_track=42 association=0.8300 runner_up=0.1100
              v=1.0 h=0.83 s=1.0 vy=0.452 area_ratio=0.0031
event=STATE   camera=3 frame=44 track=42 state=CANDIDATE->CONFIRMED decision=PHONE_USE
              phone_conf=0.610 association=0.830 temporal=3/5 latency=0.60
event=REJECT  camera=3 frame=51 track=21 phone_conf=0.7200 association=0.0000
              decision=REJECT reason=phone_outside_upper_body v=0.0 h=1.0 s=1.0 vy=0.981
event=STATE   camera=3 frame=88 track=42 state=CONFIRMED->RELEASED decision=RELEASE
              duration=8.80 misses=4
event=METRICS camera=3 frames_evaluated=54 raw_detections=22 detections_associated=8
              detections_unassociated=14 phone_candidates=1 phone_confirmed=1
              phone_use_events=1 phone_released=1 association_rejections=14
              temporal_rejections=0 reject_reasons=phone_outside_upper_body:14
```

Replay re-runs association and the state machine over a captured scene — **it does not
re-run the detector**, so a phone the model never saw cannot be recovered by replay.
That distinction is deliberate: it is what separates a tuning problem from a resolution
problem, and `--scale` reports on the latter.

Verified: replay of a captured scene reproduces the recorded decisions exactly
(`fidelity MATCH`).

---

## H2. Field findings — first real capture (2026-08-25, 12 min, 5 cameras)

The first production capture produced **55 confirmed phone-use alerts in 12 minutes**,
in an office where nobody was using a phone. The diagnostic log identified the cause
without needing the video.

### What the log showed

```
raw detections                1302
detector confidence           median 0.32, only 5.5% above 0.70
association score             median 1.00  (almost everything passed)
vy (position down the box)    median 0.45  (mid-band)
```

**65% of ALL detections came from FIFTEEN fixed image locations**, each hit 8–157 times
over minutes, and attributed to **36 different track ids between them**:

```
  hits  span(s)  tracks  cam     x     y  med conf
   157      215       3   49   768    96      0.27
   120       25       1   52   720   312      0.35
    94      109       2   52   672   480      0.34
    81      148       2   52   672   504      0.29
```

One object cannot be in 36 people's hands. Those are keyboards, mice, wallets and dark
rectangles lying on desks.

### Three compounding causes

1. **The confidence floor was far too low.** `PHONE_CONF=0.20` let through detections
   whose median score was 0.32. Raised to **0.35** — but a floor alone is not the fix:
   even at 0.50, **42% of surviving detections were still furniture**.

2. **The hand-band geometry assumes a side-on view of a standing person.** These are
   overhead cameras looking down at seated people, so the person's box includes the desk
   in front of them and "45% down the box" is the desk surface, not a hand. Geometry
   alone cannot separate "phone on the desk in front of a seated person" from "phone in
   that person's hand" on an overhead camera.

3. **Nothing required the phone to move.** A phone being *used* travels with the hand
   holding it. That is the discriminator the data points to, and it was missing.

### What was added

| Filter | Timescale | Catches |
|---|---|---|
| **Episode motion** | seconds | The first alert, before the map has learned. The detections forming a confirming window must span at least `max(min_motion_px, person_width × min_motion_frac)`. The episode stays CANDIDATE rather than being discarded, so it confirms the moment the phone does move. |
| **Static-object map** | minutes | Persistent furniture. A grid cell hit `static_hits` times over `static_window` seconds is suppressed outright. |

The motion requirement **scales with apparent size** — a person 61 px wide across the
room moves their hand through far fewer pixels than one 190 px wide near the camera, and
a fixed pixel threshold would have silently excluded everybody distant.

The map **only learns from detections that are not part of a confirmed episode**.
Without that, somebody on a long call holding reasonably still would accumulate in their
own cell and eventually blacklist themselves — turning the false-positive fix into a
false-negative one. Since the motion gate keeps furniture out of CONFIRMED, furniture
keeps accumulating and gets suppressed, while a real phone stops feeding its cell.

### Measured effect, replayed against the same capture

| camera | as recorded | conf 0.35 only | motion only | static only | **all fixes** |
|---|---|---|---|---|---|
| 47 | 2 | 0 | 1 | 2 | **0** |
| 48 | 0 | 0 | 0 | 0 | **0** |
| 49 | 11 | 4 | 5 | 6 | **1** |
| 50 | 4 | 4 | 4 | 4 | **4** |
| 52 | 38 | 14 | 9 | 12 | **4** |
| **total** | **55** | 22 | 19 | 24 | **9** |

**55 → 9 alerts, an 84% reduction**, and no single filter achieves it alone.

**Honest caveat:** there is still no ground truth. This shows the alerts that the
operator says were all wrong have been reduced by 84%; it does **not** establish how many
of the remaining 9 are real, nor whether any genuine phone use was among the 46 removed.
Camera 50 is unchanged at 4, and its detections were higher-confidence and moving — they
may well be real.

### New configuration

| Parameter | Default | Env | Purpose |
|---|---|---|---|
| `min_motion_frac` | 0.10 | `PHONE_MIN_MOTION_FRAC` | Motion needed, as a fraction of the person's box width |
| `min_motion_px` | 6 | `PHONE_MIN_MOTION_PX` | Absolute floor for small boxes; set both to 0 to disable |
| `static_cell` | 32 px | `PHONE_STATIC_CELL` | Grid resolution of the furniture map |
| `static_hits` | 8 | `PHONE_STATIC_HITS` | Hits before a cell counts as furniture |
| `static_window` | 45 s | `PHONE_STATIC_WINDOW` | ...spanning at least this long |
| `static_ttl` | 600 s | `PHONE_STATIC_TTL` | Cell expiry, so a place is never poisoned forever |
| `phone_conf` | **0.35** (was 0.20) | `PHONE_CONF` | Detector floor |

New rejection reasons in the log: `static_object`, `phone_did_not_move`.

### Live-view change

The detected phone now gets a **yellow boundary** matching the person marker, rather
than red.

---

## I. Remaining limitations

1. **No ground truth yet.** Everything in §G is a capability measurement, not an accuracy
   measurement. This is the single highest-value next step.
2. **Camera 47 is still marginal.** Even with crops, its median phone lands at ~26 px —
   above the floor but not comfortable. Its people are tall and thin in a 960×1080 frame;
   a higher-resolution stream or a tighter crop band would help more than tuning.
3. **A phone held out of the hand band is invisible by design.** Someone using a phone at
   knee height, or resting it on a desk while reading it, will not be confirmed. That is
   a deliberate trade against desk-phone false positives, and `hand_bottom` moves the
   line if the site needs it.
4. **Phone-in-pocket, phone-to-ear behind the head, and a phone held by someone standing
   directly behind another person** remain unresolvable from a single 2D box.
5. **No pose model.** Deliberately not added — §14 of the brief asks for evidence first,
   and no capture yet shows association is the binding constraint. If a real capture
   shows many `association_below_threshold` rejections on genuine phone use, wrist
   keypoints become the evidence-backed next step.
6. **GPU cost is estimated, not measured.** The extra pass is batched over person crops
   at 448–640, running at ~5 fps. It has not been profiled on the deployment GPU. Run
   `bench_tracking_cadence` and watch queue depth after enabling the module on a real
   camera; if the queue saturates, raise `PHONE_INTERVAL` rather than reverting.
7. **`MAX_CROPS = 12`** silently bounds a very crowded scene to the twelve largest people.
   Logged only implicitly; if crowds are routine this needs surfacing.
8. **The medium lane carries a full-resolution JPEG** at ~5 fps per camera so phone events
   can attach snapshots. That is real CPU cost; if snapshots turn out not to matter,
   dropping the payload is an easy saving.

---

## J. Acceptance criteria

| Criterion | Status |
|---|---|
| Current behaviour benchmarked | Done — §A.1, from 4 658 real observations |
| Root cause documented, failure modes separated | Done — §A |
| Phone inference independently schedulable | Done — medium lane, `PHONE_INTERVAL` |
| Association more robust than containment | Done — scored hand region + size sanity |
| Temporal confirmation required | Done — `confirm_hits` of `confirm_window` |
| Release debounced | Done — separate `release_misses` |
| Multiple simultaneous users | Done — per-track state, tested |
| Face recognition optional | Done — tested |
| Existing `mobile` / `phone_use` event architecture | Preserved; no new table |
| Dedicated diagnostic logging | Done — `logs/phone_debug.log` |
| Replay capability | Done, integrated with Phase 1 tooling |
| Phase 1 tests pass | Done — 133 total, replay fidelity MATCH |
| GPU/queue performance measured | **Not done** — see §I.6 |
| Accuracy reported honestly | Done — §G states no ground truth exists |
| No Phase 3+ work | Confirmed |
