# Kohat POC — 8 Capability Plan

Initial plan. Scope assessment and sequencing only; no code written yet.

Companion to `false-detection-plan.md`. That document describes the layer every
capability below writes through; this one describes the capabilities.

---

## 0. Read this first

**Your list numbers 1, 2, 4, 5, 6, 7, 8, 9 — there is no 3. Confirmed absent, not
missing.** The POC is **eight** capabilities, not nine. Every count in this document is
out of eight. If a ninth is added later it slots in without disturbing the plan.

**Seven of the eight need no new model — five to build, two already working.** The
single biggest finding of this survey:
`models/yolo11s.pt` is a COCO model. Today the pipeline asks it for class 0 (person) and
class 67 (cell phone). The same weights already know:

| COCO class | Name | Serves |
|---:|---|---|
| 2, 3, 5, 7 | car, motorcycle, bus, truck | #6 vehicle stop |
| 24, 26, 28 | backpack, handbag, suitcase | #5 — LABEL only; the mechanism is MOG2, see §5 |

That is a second inference pass on weights already resident in VRAM — not a new model, no
new training, no new download. `backend/services/detectors/phone.py` is the working
template for exactly this (`PHONE_CLASSES=67`, its own cadence, graceful no-op when
weights are absent).

> **How firmly this is established.** `models/` is git-ignored and the weights are not on
> the machine I surveyed from, so I could not dump the class list directly. The claim
> rests on `phone.py` documenting `yolo11s.pt` as a stock COCO model addressed by COCO
> class ids, and on class 67 working in production. Stock COCO carries all seven classes
> above. **Confirm on the deployment box before committing to it** — one line:
> `python -c "from ultralytics import YOLO; print(YOLO('models/yolo11s.pt').names)"`.
> If those weights turn out to be a person-only fine-tune rather than stock COCO, #5 and
> #6 need a stock `yolo11s.pt` alongside — still a download, not a training run.

**Only one capability genuinely needs a model that does not exist: #9, weapons.**
That is the POC's real risk. Candidate models for it are in §4A; the risk itself is
discussed in §1 under capability 9.

**Current scope, as agreed: the five in §1A plus the two that already work.** #9 is
deferred until a model decision is made.

---

## 1. Coverage: what exists, what adapts, what is new

| # | Capability | Status | What it costs |
|---|---|---|---|
| 2 | Restricted zone intrusion | ✅ **Exists and ships today** | Configure and test |
| 7 | Mobile phone usage | ✅ **Done** (Phase 2) | Already tuned |
| 1 | Virtual trip wire | 🟡 **Adapt** — headcount's line machinery | Small |
| 8 | Unusual gathering / crowd | 🟡 **Adapt** — occupancy's zone counting | Medium |
| 6 | Vehicle stop in no-stop area | 🟠 **New, no new model** | Medium |
| 4 | Camera tampering / turned to wall | 🟠 **New, no model at all** | Small |
| 5 | Object dropped and left | 🔴 **New, hard** — MOG2, no new model | Large |
| 9 | Weapon brandishing | 🔴 **New, needs a model that does not exist** | Large + unknown |

### 2 — Restricted zone intrusion ✅ SHIPS TODAY

`backend/services/analytics/intrusion.py`, registry id `intrusion`, geometry `zones`,
config `["zones", "schedule"]`, event `intrusion`, severity already mapped **high**.

Nothing to build. Draw zones in the camera editor and it runs. This is your fastest
demonstrable win and it should be the first thing shown at Kohat.

### 7 — Mobile phone usage ✅ DONE

Phase 2. Combined evidence gate, association scoring, temporal state machine, crop-mode
detection at ~7× effective resolution, dedicated `phone_debug.log`. Already handles the
awkward cases you reported: calling, texting held still, secret photography.

### 1 — Virtual trip wire 🟡 ADAPT

A trip wire is `headcount` with the counting removed and the alerting turned up. Same
geometry (`line`), same crossing detection, different semantics: headcount asks *which
direction and how many*, a trip wire asks *did anyone cross at all*.

Critically, this inherits the **Phase 1 hardening** — foot-point reference, hysteresis
band, movement validation, ID hand-off, debounce. That work took a headcount from 0.62 to
1.00 on synthetic ground truth, and a trip wire built on it starts at that quality rather
than at the naive `centroid crossed a line` that a fresh implementation would give you.

New module `tripwire`, subclass of the hardened crossing logic, `direction` config
(`any` | `in` | `out`). Severity **high** — a perimeter breach is not a footfall count.

### 8 — Unusual gathering / crowd 🟡 ADAPT

`occupancy` already counts people per zone against a capacity and emits `over_capacity`.
That is *most* of a crowd alert but not all of it, and the gap matters:

- **Occupancy answers "how many are in this zone."** Crowd needs "how many are close to
  *each other*" — five people spread across a yard is not a gathering, five people in one
  huddle is. That is spatial clustering, not a count.
- **A gathering has a time dimension.** People walking past each other trip a count
  instantly; a gathering persists. Needs a sustain window before it fires.
- **Dispersal is an event too**, and often the more useful one operationally.

Build `crowd` as its own module rather than bending `occupancy`: cluster track centroids
by pairwise distance (normalised, so it survives resolution changes), require
`min_people` within `radius` sustained for `sustain_seconds`. Config
`["zones", "min_people", "radius", "sustain_seconds"]`. Events `gathering`, `dispersed`.

### 6 — Vehicle stop in no-stop area 🟠 NEW, NO NEW MODEL

Two pieces:

1. **`detectors/vehicle.py`** — COCO classes 2/3/5/7 off the shared `yolo11s.pt`, its own
   cadence, `VEHICLE_CLASSES` env override. Copy the shape of `phone.py`, including its
   graceful no-op when weights are missing.
2. **`analytics/no_stopping.py`** — a vehicle stationary inside a zone beyond
   `dwell_seconds`. This is `idle`'s logic applied to vehicle boxes instead of person
   tracks, so read `analytics/idle.py` before writing it.

The real engineering is **stationarity**, not detection. A parked car and a car waiting at
a barrier look identical for the first ten seconds. Needs box-centre displacement
smoothed over a window, with a tolerance for detector jitter — a stationary box still
wobbles a few pixels per frame.

Config `["zones", "dwell_seconds", "vehicle_types"]`. Event `no_stopping`, severity
**medium**.

### 4 — Camera tampering 🟠 NEW, NO MODEL AT ALL

The cheapest real win on the list, and the only one that needs no neural network.
Three classical signals on the decoded frame:

| Signal | Catches | Method |
|---|---|---|
| Blur | Lens sprayed, defocused | Variance of Laplacian collapses |
| Scene change | Camera turned to the wall | Histogram divergence vs a rolling reference |
| Coverage | Lens blocked, bag over camera | Sustained very low luminance variance |

Runs in **`camera_manager`, in the main process**, not the perception child — it needs
only the frame the capture thread already decoded, and it must keep working when the child
is dead. That placement is deliberate: a tampering detector that dies with the GPU process
cannot report a camera being disabled.

All three need a **sustain window**. A single dark frame is a compression artefact; ten
seconds of dark frames is a bag over the lens. Without that this is a false-positive
generator.

Config `["blur_threshold", "scene_threshold", "sustain_seconds"]`. Events `tamper_blur`,
`tamper_moved`, `tamper_covered`, all severity **critical** — someone disabling a camera
is the most serious thing on this list.

### 5 — Object dropped and left 🔴 NEW, HARD — reframed

**Scope, as asked: anything a person puts down and walks away from.** A bag, a box, a
carton, a sack, a bin bag, a gas cylinder, a piece of furniture, a parked vehicle. Not
"is there a bag" — *"did somebody leave that there"*.

That reframing is not cosmetic. It changes the technique, and it makes the hard part
easier rather than harder.

#### Why "bag detection" is the wrong mechanism for this

COCO gives you `24 backpack`, `26 handbag`, `28 suitcase` free, off weights already in
VRAM (see `detectors/vehicle.py` for the identical pattern). But:

- **Most abandoned objects at a real site are not bags.** A box, a sack, a crate, a
  cylinder, a bundle of cable. None are COCO classes. A bag detector is blind to them.
- **A bag detector cannot tell a dropped bag from a permanent one.** Your own camera 52
  has a backpack on the table and a handbag on the desk in *every single frame*. A
  detector-driven rule alerts on both, forever, from the first minute.

#### The mechanism that fits: a NEW STATIC REGION

Background subtraction (OpenCV **MOG2**) finds pixels that differ from the learned
background. An object somebody put down is, by definition, **a region that was not there
before and has now stopped changing**. That is object-agnostic by construction — it does
not care whether the thing is a bag, a carton or a chair.

And it dissolves the furniture problem for free: **the bin that has always been there IS
the background.** It was never dropped. Only regions that newly appear can ever qualify,
so a permanently-parked object can never alert, no matter how abandoned it looks.

That is the whole argument for this approach over the COCO one. The classification is
then a *bonus*: when the new region happens to overlap a COCO detection, label the alert
"backpack" instead of "object". Label, not mechanism.

#### The trap that sinks naive implementations

**A single MOG2 model absorbs the abandoned object into its own background within
seconds**, and the alert window closes before the dwell timer expires. This is the
standard failure and it has a standard fix: **two background models at different learning
rates.** A fast one tracks the live scene; a slow one remembers what the scene looked like
before. A static region present in the fast model but absent from the slow one is a
candidate. Anyone implementing this without the dual model will conclude the technique
does not work.

#### Where it runs, and why that is cheap

The MAIN process, alongside `tamper.py`. Classical CV, no GPU, and the main process
already decodes every frame and sits at ~35% of a core with headroom - while the
perception child, the constrained one at ~78%, is untouched. Events reach the database
through `submit_events()`, the same path tamper uses, so they inherit the false-detection
layer, severity mapping and snapshots for free.

Person association is an ENRICHMENT, not a requirement. "A new object appeared and
stayed" is already the alert; "and person 47 was standing there 8 seconds earlier" makes
the alert better, and comes from the overlay person boxes the child already publishes.
Build the first without the second.

#### What will actually go wrong

Stated up front, because these decide whether it ships:

1. **A moved chair reads as two events** - a new region where it now is, and a hole where
   it was. Real, and common in an office.
2. **Lighting changes** create large new regions. Mitigated by requiring a compact,
   bounded, persistent region rather than any change.
3. **Camera shake** ghosts edges across the whole frame. Gate on the tamper module's
   `moved` signal, which already measures exactly this.
4. **Shadows** - MOG2 has shadow detection built in; it must be enabled.
5. **A person standing still** becomes a static region. This is the one that needs person
   boxes: suppress candidates that overlap a live person track.

#### Config and phasing

Config `["zones", "abandon_seconds", "min_area", "object_types", "owner_radius"]`.
Event `unattended_object`, severity **high**.

- **Phase A — bags only, COCO route.** `detectors/bag.py` cloned from `vehicle.py`, plus
  an analytic cloned from `no_stopping.py`, which already solves stationarity, IoU slot
  matching and occlusion grace for a motionless object. A day's work, narrow coverage,
  and it will alert on the permanent handbag until Phase B lands.
- **Phase B — the real one, MOG2 dual-background.** Object-agnostic, furniture-immune,
  and what the requirement actually asks for.
- **Phase C — person causality.** Bind the object to whoever was near it, and report them.

**Phase B is the deliverable.** Phase A is worth doing only because it is nearly free and
proves the event path end to end. If time is short, skip A.

Realistically this still needs a tuning round against real site footage, the way phone
detection did. **Do not promise it for the first demo.**

### 9 — Weapon brandishing 🔴 NEW, NEEDS A MODEL THAT DOES NOT EXIST

Weapons are **not COCO classes**. Nothing in the current inventory detects them. This is
the one capability that cannot be built from what you have, and it carries three risks
that are not engineering risks:

1. **No weights.** You need a public pretrained weapon model, or a dataset plus a training
   run. Public gun-detection models are typically trained on handguns in clean, well-lit,
   close-range images. CCTV at distance and at a wide angle is a different problem.
2. **Licensing and provenance.** A model of unknown origin in a security deployment is a
   liability. Whatever is chosen needs its licence and training data documented.
3. **The base rate is brutal.** Weapons are extremely rare. At 15 fps across several
   cameras even a 99.9 % specific detector produces a steady stream of false alarms —
   and false "gun" alerts are far worse than false phone alerts, because someone acts on
   them. This capability needs the strictest possible temporal confirmation and probably
   a human-verification step before it alerts.

**"Waving/showing openly" is also a behaviour, not an object.** Object detection tells you
a gun-shaped thing is present; it does not tell you it is being brandished. Genuine
brandishing detection is pose plus motion over time. For a POC, scope this to *object
present, temporally confirmed*, and say plainly that is what it is.

**Recommendation: sequence this last, and treat feasibility as a separate investigation
with its own go/no-go.** Do not let it block the other eight.

---

## 1A. Immediate scope — everything that runs on today's models

This is the working set. **Seven of the eight need no new model**; only #9 does.

### The 5 to build, no new model required

| # | Capability | Module to add | Detector source | Geometry |
|---|---|---|---|---|
| **1** | Virtual trip wire | `tripwire` | `yolo11s.pt` class **0** (person) — already loaded | `line` |
| **4** | Camera tampering / turned to wall | `tamper` | **No model at all** — classical CV on the decoded frame | none |
| **5** | Object dropped and left | `unattended` | **OpenCV MOG2** (no model) in the main process; `yolo11s.pt` classes 24/26/28 only to LABEL a bag when it is one | `zones` |
| **6** | Vehicle stop in no-stop area | `no_stopping` | `yolo11s.pt` classes **2, 3, 5, 7** (car, motorcycle, bus, truck) | `zones` |
| **8** | Unusual gathering / crowd | `crowd` | `yolo11s.pt` class **0** (person) — already loaded | `zones` |

Not one of these adds a weights file. #1 and #8 use the person detector that is already
running on every frame — literally zero extra inference. #5 and #6 add a second pass over
weights already resident in VRAM, the same trick `phone.py` uses for class 67. #4 touches
no network whatsoever.

### The 2 that already work

| # | Capability | State |
|---|---|---|
| **2** | Restricted zone intrusion | ✅ `intrusion` module ships today — zones + schedule, severity `high`. Configure and demo. |
| **7** | Mobile phone usage | ✅ Done and tuned in Phase 2. |

### The 1 that is deferred

| # | Capability | Why |
|---|---|---|
| **9** | Weapon brandishing | Needs a model that does not exist in the inventory. Candidates in §4A. |

### Cheapest-first build order within the five

1. **#4 tampering** — no model, no geometry, self-contained, `critical` severity. Highest
   value per unit of work on the entire list.
2. **#1 trip wire** — inherits the Phase 1 crossing hardening; mostly assembly.
3. **#6 vehicle no-stopping** — one new detector file on free classes, one dwell rule.
4. **#8 crowd** — clustering plus a sustain window; no new detector at all.
5. **#5 object dropped and left** — no model at all (MOG2), but the causality and occlusion logic is
   the real work. Last of the five, and it needs real footage.

---

## 2. Your grouping question

> which parameters are belonging to unusual gathering or we need to add these to security

Short answer: **only #8 is "unusual gathering". Everything else is security.**

"Unusual gathering" describes one specific behaviour — people converging. The other seven
are perimeter, access and asset protection. Grouping them under a gathering heading would
misdescribe them in the UI and make them hard to find.

The registry has **no category field today** — modules are a flat list. For nine more
capabilities that list becomes unusable, so I propose adding one:

| Category | Modules |
|---|---|
| **Perimeter & Access** | `tripwire` (1), `intrusion` (2) |
| **Asset & Scene** | `tamper` (4), `unattended` (5), `no_stopping` (6) |
| **Crowd & Behaviour** | `crowd` (8), `occupancy`, `idle` |
| **Conduct** | `mobile` (7) ✅, `smoking` |
| **Threat** | `weapon` (9), `fire_smoke` |
| **Identity** | `attendance`, `reid`, `headcount` |

This is one optional `"category"` key per registry entry plus the mirrored field in
`frontend/src/lib/modules.ts`. Purely additive — an entry without a category still works.

---

## 3. How this links to the false-detection plan

This is the part that makes nine capabilities tractable rather than nine separate
false-positive problems, so it is worth being explicit.

**The layer is module-agnostic and sits at the writer.** Every event from every module
already passes through `fd_judge` in `perception._result_writer`. That means each new
capability inherits, with no per-module work:

| Rule | What a new module gets free |
|---|---|
| `dedup` | One parked car does not alert every frame for an hour |
| `ratelimit` / storm | A camera facing a busy road cannot bury the feed |
| `zones` (`fd_ignore_zones`) | Mask the road outside the fence out of the trip wire |
| `warmup` | No alert storm in the seconds after a camera reconnects |
| `repeat` (`fd_repeat_*`) | **The bin that looks abandoned forever gets learned as furniture** |
| `severity` | Weapon `critical` routes differently from vehicle `medium` |
| `observe` mode | Measure a new module's false-positive rate *without* it alerting anyone |

That last row is the important one for a POC. A brand-new module can be enabled in
`observe`, run for a day against real Kohat footage, and you get a measured suppression
rate before it ever alerts an operator.

**What each new module must do to plug in — three small obligations:**

1. **Add its event types to `DEFAULT_SEVERITY_MAP`** in
   `backend/services/false_detection/config.py`. An unmapped type falls back to
   `FALLBACK_SEVERITY = "medium"` — deliberately not `info`, so nothing is silently
   demoted, but every new type should be classified explicitly.
2. **Emit `data.track`** where a track exists. The spatial rules and track-scoped dedup
   derive their point from the overlay matching that track; without it `fd_ignore_zones`
   records that it could not run rather than guessing.
3. **Add a diagnostic log** under `logs/`, following the `HEADCOUNT_DEBUG` /
   `PHONE_DEBUG` pattern — one env var carrying the level, `announce()` at start-up so the
   file exists immediately, a replayable line format. This is how you send me a capture
   and I tell you what went wrong, which is how phone detection got fixed.

**One gap to close:** `fd_repeat_*` was written with a repeated *source* in mind and is
off by default. #5 needs it least now: a background model cannot alert on furniture,
because permanent furniture IS the background. Validate it against #8 instead.

---

## 4. Model and detector inventory impact

| Component | Change |
|---|---|
| `models/yolo11s.pt` | **Unchanged.** Serves person, phone, vehicle, bag |
| `buffalo_l` (SCRFD + ArcFace) | Unchanged |
| `reid.onnx` (OSNet) | Unchanged |
| `models/best.pt` (smoking) | Unchanged |
| `models/fire_smoke.pt` | Unchanged |
| **New: weapon weights** | ⚠️ Does not exist. Sourcing decision required — §6 |
| `detectors/vehicle.py` | New file, existing weights |
| `detectors/objects.py` | New file, existing weights |
| Camera tampering | **No model.** Classical CV in `camera_manager` |

Eight of nine capabilities ship on the current model inventory. Preserve the existing
convention: a detector that finds no weights **logs once and no-ops** rather than
crashing the pipeline.

---

## 4A. Models for the capabilities not covered by today's inventory

Only **#9 weapons** actually requires a model you do not have. The other entries here are
*optional upgrades* — reach for them only if the free route measurably fails, which is a
decision to make against Kohat footage, not in advance.

> ⚠️ **Treat every name below as a candidate to evaluate, not a recommendation to adopt.**
> I have not benchmarked any of them on your footage, and for a security deployment the
> licence and training provenance of a third-party model are as important as its mAP.
> Check both before anything ships.

### #9 Weapon detection — required

Two routes, and for a POC they are complementary rather than alternatives.

**Route A — open-vocabulary, no training run (start here).**

| Model | Why it fits | Cost |
|---|---|---|
| **YOLO-World** | Text-prompted YOLO (`"handgun"`, `"rifle"`). Fastest of the three; closest to the YOLO runtime already in the stack | Moderate |
| **OWLv2** (OWL-ViT v2) | Strong zero-shot localisation, well-documented | Heavy |
| **Grounding DINO** | Best zero-shot quality of the three | Heaviest |

These need **no dataset and no training** — you write the prompt and get boxes. That is
exactly what a feasibility question needs: you learn within days whether weapons are
detectable at Kohat's camera angles and distances before committing to a training effort.

They are too slow to run on every frame of every camera. The practical pattern, which the
codebase already supports: **gate on the person detector, run on person crops only, at a
low cadence.** That is the same crop-mode trick that gave phone detection ~7× effective
resolution — and it applies here for the same reason, since a weapon occupies very few
pixels in a full frame.

**Route B — fine-tuned detector (if Route A proves it out).**

Fine-tune **YOLOv11** on a curated weapon dataset, so the runtime, the ByteTrack
integration and the detector contract all stay identical to what is already in the
pipeline. Candidate public sources worth reviewing: the **Granada / Olmos handgun
dataset**, the **Sohas weapon dataset**, and weapon datasets on **Roboflow Universe**.
Every one needs a licence check.

Be aware of the domain gap: these datasets are largely handguns, close range, well lit.
Kohat CCTV is distant, wide-angle, and variably lit. Expect to need footage from the
actual site in the training mix.

**Brandishing is a behaviour, not an object.**

Neither route tells you a weapon is being *waved* — only that one is present. If that
distinction matters, add **YOLOv11-pose** (same family, same runtime, already an
ultralytics model) and gate on arm-raise geometry. Carrying and brandishing differ in
posture, not in the object.

### #8 Crowd — optional upgrade only

Person detection plus clustering is the right call for moderate density and needs no new
model. It fails when heads occlude each other in genuinely dense crowds, because the
detector stops resolving individuals.

If and only if that happens, switch to density estimation, which counts without detecting:
**CSRNet**, **DM-Count**, or **MCNN**. These regress a density map and handle dense scenes
that detection cannot.

Do not start here. Start with clustering, measure, and escalate only against evidence.

### #5 Object dropped and left — background subtraction is the MECHANISM, not a complement

Superseded by §5 above, and the change of mind is worth recording. This section used to
call background subtraction an "optional complement" to the COCO bag classes. That was
backwards.

Once the requirement is stated as **"a person put something down and left"** rather than
**"there is an unattended bag"**, MOG2 becomes the mechanism and COCO becomes the label:

- COCO 24/26/28 cover bags and nothing else. A box, a carton, a sack, a crate, a cylinder
  - the objects actually abandoned at a real site - are not COCO classes at all.
- More decisively, a detector cannot tell a dropped object from a permanent one. Only a
  background model can, because a permanent object IS the background.

**ABODA** remains the public benchmark worth sanity-checking an implementation against.
No new model, no training, no extra VRAM - MOG2 is OpenCV, and it runs in the main
process next to `tamper.py` rather than in the constrained perception child.

### #4 Camera tampering — no model, deliberately

Learned tampering classifiers exist. Do not use one. Blur, histogram divergence and
luminance variance are cheap, interpretable, tunable per camera, and run in the main
process where they keep working even if the GPU child has died. A model here would be
slower, less explainable and no more accurate.

### #6 Vehicle — COCO is sufficient

Classes 2/3/5/7 cover the capability as written. **Number plates are a different problem**
and are not on your list — they would need a plate detector plus OCR (**PaddleOCR** or
**EasyOCR**). Out of scope unless you say otherwise.

### Summary

| # | Free route (use this) | Upgrade, only if the free route fails |
|---|---|---|
| 1 | `yolo11s` person | — |
| 2 | `yolo11s` person ✅ shipping | — |
| 4 | Classical CV, no model | — (do not use a model) |
| 5 | **MOG2 background subtraction, no model** | + `yolo11s` 24/26/28 to label a bag |
| 6 | `yolo11s` classes 2/3/5/7 | + plate detector & OCR (out of scope) |
| 7 | `yolo11s` class 67 ✅ done | — |
| 8 | `yolo11s` person + clustering | CSRNet / DM-Count / MCNN |
| 9 | **none — model required** | YOLO-World or OWLv2 → fine-tuned YOLOv11 (+ YOLOv11-pose) |

---

## 5. Suggested POC sequence

Ordered by demonstrable-value-per-unit-risk, not by your list order.

**Stage A — show something working immediately**
- #2 intrusion: configure zones, verify, demo. Zero build.
- #7 mobile: already done, already tuned.

**Stage B — cheap, high-value, low-risk**
- #4 camera tampering (no model, self-contained, critical severity)
- #1 trip wire (inherits Phase 1 hardening)

**Stage C — moderate build on free classes**
- #6 vehicle no-stopping
- #8 crowd / unusual gathering

**Stage D — needs real footage and a tuning loop**
- #5 object dropped and left

**Stage E — separate feasibility investigation, own go/no-go**
- #9 weapon

Run every new module in `observe` for a day before enforcing. That is what the layer is
for, and it converts "we think it works" into a number.

---

## 6. Open decisions — I need your answers

1. ~~What is item #3?~~ **Answered: there is no #3.** The POC is eight capabilities.
2. **Weapon model (#9)** — deferred by agreement, so this is no longer blocking. When you
   pick it up, §4A recommends starting with an open-vocabulary model (no training run) to
   answer feasibility before committing to a dataset.
3. **Kohat footage** — can I get recorded clips containing the actual events? Without
   them every accuracy number is synthetic. This is the single thing that most determines
   whether #5 and #8 work.
4. **Demo date and audience** — decides how much of Stage D/E is realistic.
5. **Vehicle scope** — detect stopping only, or also number plates? Plates are a
   different problem (OCR) and not on your list; I have assumed not.
6. **Does Kohat have a GPU box provisioned?** Six modules at 15 fps is materially more
   inference than the current load.

---

## 7. Branch: `kohat_poc` ✅ decided

**Verified, not assumed** — `git log staging_v2 ^unusal-activity` returns empty, and
`git branch --contains staging_v2` lists `unusal-activity`. Your merge `af2dfe5` brought
`staging_v2` in completely.

So `unusal-activity` is currently the **only branch that has everything**:

- Phase 1 headcount hardening — which #1 trip wire is built on
- Phase 2 phone detection — capability #7, already done
- The false-detection layer — which all nine write through
- Everything from `staging_v2`

Building on `staging_v2` would mean working without the false-detection layer and without
the Phase 1 crossing logic, then merging them back later — reintroducing exactly the
divergence you just resolved.

**Decided: `kohat_poc`, branched from `unusal-activity`.** Verified — it is missing
zero commits from both `staging_v2` and `unusal-activity`, so it carries the Phase 1
crossing hardening, Phase 2 phone detection and the false-detection layer in full. POC
experiments cannot destabilise the layer on the parent branch.

One caveat I flagged before and will flag once more, because it will bite at deployment
rather than now: the **alembic lineage is still divergent**. Your live `vigil` database
sits on `a75c31865cd3`, whose migration file does not exist on this branch, while the
false-detection migration hangs off `d4e5f6a0b1c2`. That is a merge-time chore, not a
blocker for POC development — but it must be resolved before this reaches a production
database, and it gets harder with every migration added.

Merge `kohat_poc` back into `unusal-activity` once a capability is measured and accepted,
rather than at the end — nine months of POC divergence is how the alembic problem below
happened in the first place.

---

## 8. Honest assessment

- **Two of eight already work.** #2 and #7 are demonstrable today with zero build.
- **Five more need no new model.** #1, #4, #6, #8 are genuinely tractable; #5 is
  tractable but needs a tuning loop. That is the agreed current scope.
- **One is a research question.** #9 has no weights, an unfavourable base rate, and a
  behaviour-vs-object gap. It should not carry the same confidence as the other eight.
- **No accuracy claim in this document is measured.** There is no Kohat ground truth yet.
  Every number will be a capability measurement until real annotated footage exists —
  the same discipline applied in Phases 1 and 2, and the reason those improvements held.
- **Nine capabilities is a large POC.** Stages A and B are comfortably achievable. Stage
  D depends on footage. Stage E may not land at all, and planning around that now is
  cheaper than explaining it later.

---

## 9. Status

**Done**
- [x] Survey the existing registry, consumers and detectors
- [x] Map all 9 (8 listed) capabilities to exists / adapt / new
- [x] Identify that 5 need no new model (COCO classes already in `yolo11s.pt`)
- [x] Answer the grouping question (§2)
- [x] Link each capability to the false-detection layer (§3)
- [x] Assess model-inventory impact (§4)
- [x] Verify branch topology and recommend (§7)
- [x] Write this document

**Decided since the first draft**
- [x] Item #3 — confirmed absent; the POC is eight capabilities
- [x] Branch — `kohat_poc`, verified complete (§7)
- [x] Scope — the five in §1A plus the two already working; #9 deferred
- [x] Models for everything not covered by today's inventory (§4A)

**Pending — still needed from you**
- [ ] Obtain Kohat footage — the one item that most determines whether #5 and #8 work
- [ ] Confirm demo date, audience, and target hardware
- [ ] Confirm `yolo11s.pt` is stock COCO on the deployment box (one-line check in §0)

**Built — #1, #4, #6, #8 (376 automated tests green, pyflakes clean)**
- [x] **#1 trip wire** — `analytics/tripwire.py`, subclasses `HeadCount` so it inherits
      the whole Phase 1 crossing machine. Added to `FAST_MODULES` in **both**
      `perception.py` and `camera_manager.py` (they are duplicated by design and
      `tests/test_headcount_cadence.py` enforces agreement — it caught the mismatch).
- [x] **#4 camera tampering** — `services/tamper.py`, three classical signals, no model.
      Runs in `camera_manager` (main process) and reaches the writer through the new
      `perception.submit_events`, so it inherits the false-detection layer.
- [x] **#6 vehicle no-stopping** — `detectors/vehicle.py` (COCO 2/3/5/7, shared weights)
      + `analytics/no_stopping.py` (IoU slot matching, EMA-smoothed stationarity).
- [x] **#8 crowd** — `analytics/crowd.py`, union-find clustering on foot points plus a
      sustain window.
- [x] Registry entries + mirrored `modules.ts` (parity asserted by a test)
- [x] `DEFAULT_SEVERITY_MAP` entries for all 7 new event types
- [x] Per-capability diagnostic logs: `tripwire_debug`, `crowd_debug`, `vehicle_debug`,
      `tamper_debug` — all created at start-up, verified live
- [x] Global capability toggles: `services/module_toggles.py`, migration
      `a1b2c3d4e5f7`, `GET/PUT /api/settings/modules`, `ModuleTogglePanel.tsx`
- [x] `tests/test_kohat_poc.py` — 63 cases
- [x] `frontend/dist/` rebuilt

**Pending — build, in sequence order**
- [ ] Stage A: configure and verify #2 intrusion (needs a camera)
- [ ] Stage D: #5 object dropped and left (MOG2)
- [ ] ~~Stage E: #9 weapon~~ — **deferred**; see §4A when it is picked up
- [ ] Registry `category` field + mirrored `modules.ts` entries
- [ ] `DEFAULT_SEVERITY_MAP` entries for every new event type
- [ ] Per-module diagnostic logs under `logs/`
- [ ] `plan_kohat_poc.md` test-case document, one procedure per capability
