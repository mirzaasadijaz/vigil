# VIGIL — CPU optimisation plan

Written 2026-08-28, against the symptom: one `python` process at **98% CPU** on an
RTX 3060 box, GPU idling at 50–65% with 3.3 GB of 12 GB VRAM used, desktop hanging when
several live-stream tabs are open.

**Nothing in this plan changes what any AI model sees, how often it runs, or what it
decides.** Every item removes work that is discarded, duplicated, or done speculatively.
Where a trade-off exists it is stated explicitly and is opt-in.

---

## 0. The diagnosis, from your own numbers

| Evidence | Reading |
|---|---|
| GPU 50–65%, oscillating, never pegged | The GPU is **starved**, not saturated |
| VRAM 3.3 GB / 12 GB | Model loading is **not** the problem |
| One process at 98% CPU | A **single-threaded CPU path** is the bottleneck |
| Hangs with several stream tabs | Each tab **wakes another camera**, adding a decode thread |

Your developer's suspicion about miniconda is understandable but almost certainly wrong.
Miniconda is a packaging layout; it does not make code slower. The GPU stack is clearly
working — CUDA is running, VRAM is allocated, inference is happening. The problem is that
the CPU cannot feed it.

### Where the CPU actually goes

Per camera, per second, as currently configured:

| Work | Rate | Cost |
|---|---|---|
| **H.264 decode of every delivered frame** | **25–30 fps** | **dominant** |
| Resize to the detection frame | ≤10 fps | moderate |
| Full-resolution JPEG for snapshots | 2 fps | moderate, mostly wasted |
| Full-resolution JPEG for the live view | 10 fps per camera with a viewer | moderate |
| Full-frame `.copy()` | ≤10 fps | small but avoidable |

All of it runs **single-threaded**, because `backend/main.py` sets `cv2.setNumThreads(0)`
and pins `OMP/MKL/OPENBLAS` to 1 thread.

With seven cameras that is roughly **200 full-frame decodes per second on one core**. At
even 5 ms each that is a saturated core; at 15 ms it is three. That is your 98%.

**The core waste: the pipeline decodes 25–30 fps and uses at most 10.** Two thirds of the
most expensive operation in the system is thrown away.

---

## 1. Decode only the frames we use  ⭐ biggest win

**`backend/services/camera_manager.py`, `_capture_loop`**

```python
success, frame = self.cap.read()      # ALWAYS decodes
if success:
    self.frame = frame
```

`cap.read()` is `grab()` + `retrieve()`: pull the packet **and** decode it. Every frame is
decoded whether or not anything wants it.

Split them:

```python
if not self.cap.grab():               # cheap: pulls the packet, NO decode
    ...handle failure...
if self._decode_due(now):             # a lane is due, or a viewer is watching
    ok, frame = self.cap.retrieve()   # decode only now
    if ok:
        self.frame = frame
```

**Saving:** 30 fps → 10 fps decoded is a **~65% reduction in the single largest CPU
cost**. With no viewer and only slow-lane modules it drops to 2 fps — a **93%** reduction.

**AI impact: none.** The pipeline already throttles to `TRACK_INTERVAL` / `ENQUEUE_INTERVAL`
and discards the rest. This decodes the same frames it would have used and skips the ones
it was going to throw away.

**Risk:** low. `grab`/`retrieve` is the standard OpenCV idiom for exactly this.
`CAP_PROP_BUFFERSIZE` is already 1, so grabbing keeps the stream current rather than
building a backlog.

---

## 2. Stop speculatively encoding snapshot JPEGs

**`_process_frame`**

```python
buf = (None if lane == "fast" else cv2.imencode(".jpg", frame)[1].tobytes())
```

A **full-resolution JPEG on every slow-lane frame** — 2 per second per camera, forever —
to serve an alert that happens a few times an hour. Across seven cameras that is 14
full-res encodes per second, of which essentially all are discarded.

The naive fix (drop it and let the writer call `latest_jpeg()`) is **wrong for the slow
lane**: that returns the *current* frame, which is hundreds of milliseconds newer. For a
face-recognition or phone alert the snapshot could show a different person than the one
the event is about. That is an AI-functionality regression and this plan will not make it.

**Correct fix — a small raw-frame ring buffer:**

- Keep the last N raw frames per camera (N≈8; at 1080p that is ~25 MB per camera, trivial
  against 64 GB)
- Tag each perception task with a frame token
- The writer asks for that exact frame by token and encodes **only when an event fires**

**Saving:** 14 full-res encodes/sec → a handful per hour, with the snapshot still being
the exact frame the decision was made on. Strictly better than today on both counts.

---

## 3. Encode the live view smaller

**`_ai_loop`** encodes the annotated frame at **full resolution, 10 fps, per camera with a
viewer**. A browser showing a grid of cameras displays each at a few hundred pixels wide.

Downscale before encoding — `BROADCAST_WIDTH`, default ~960:

**Saving:** JPEG cost scales with pixel count; 1080p → 960-wide is roughly **2.5× cheaper**
per encode, plus a matching drop in network traffic (your screenshot shows 273 MB/s TX).

**AI impact: none.** This is the human-viewing path only. Detection runs on the separate
detection frame.

**Trade-off:** the live view is softer if someone full-screens one camera. Make it
configurable and default conservative.

---

## 4. Let OpenCV use more than one thread in the MAIN process

`backend/main.py` sets `cv2.setNumThreads(0)` and pins BLAS/OMP to 1.

That exists for a real reason recorded in `CLAUDE.md`: torch + ONNX + libpq + FFmpeg in
one address space corrupted the native heap. **But that constraint belongs to the
perception child, which owns torch. The main process has no torch at all** — the
three-tier design guarantees it.

Allowing the main process 2–4 OpenCV threads would speed decode and encode close to
linearly.

**This is the one item with real risk**, so:

- gate it behind `MAIN_CV_THREADS` (default `0` = today's behaviour, no change)
- change it **only after** items 1–3 are in and measured
- if anything becomes unstable, set it back to 0 — one variable, instant rollback

Do not do this one first. It is the least certain and the others are free.

---

## 5. Avoid the per-iteration full-frame copy

`_ai_loop` does `raw_frame = self.frame.copy()` on every AI iteration — a full-frame
memcpy up to 10× a second per camera, to guard against the capture thread replacing the
buffer mid-use.

With the ring buffer from item 2 the copy becomes unnecessary: the capture thread writes
new frames into new slots, so a reference taken from the ring is already stable.

**Saving:** small individually, but it is pure memory bandwidth on the hot path.

---

## 6. Cap what a browser tab can start

Each open tab wakes a camera that would otherwise idle-stop after 30 s. Opening six tabs
starts six capture threads — which is exactly the reported hang.

After items 1–3 this is far less severe (an idle-but-watched camera costs a tenth of what
it does today), but two guards are still worth having:

- a configurable ceiling on concurrently streaming cameras, refusing politely beyond it
- pause the broadcast encode for tabs that are not visible (`document.hidden`), so a
  background tab stops costing a JPEG every 100 ms

---

## 7. Already known, still outstanding: perception queue saturation

Reported by the counters added earlier:

```
camera 47: perception queue FULL, 14,198 frame(s) dropped
camera 50: perception queue FULL,  2,357 frame(s) dropped
```

One task queue, one sequential child. Items 1–5 give the CPU room, which will help, but
the arithmetic still holds: every fast-lane camera demands 10 inferences/sec of a single
worker.

Do not add fast-lane cameras until the drop counts are near zero.

---

## Order of work

| # | Item | Effort | Risk | Expected CPU saving |
|---|---|---|---|---|
| 1 | grab/retrieve decode gating | small | low | **~65%** of the dominant cost |
| 2 | Frame ring + on-demand snapshot encode | medium | low | 14 full-res encodes/sec |
| 3 | Smaller broadcast encode | small | low | ~2.5× on the view path |
| 5 | Drop the full-frame copy | small | low | memory bandwidth |
| 6 | Stream caps + hidden-tab pause | small | low | scales with tabs open |
| 4 | OpenCV threads in main process | small | **medium** | close to linear, do last |

Items 1 and 3 alone should take that process well below 98%. I would do 1, measure, then
3, measure, and only then decide whether 2, 5 and 6 are still needed.

---

## How we will know it worked

Take these **before** any change, on the same camera set:

```bash
# per-process CPU, 60s
top -b -d 5 -n 12 -p $(pgrep -f 'uvicorn backend.main') | grep python

# frames dropped for want of queue capacity
grep 'queue FULL' <server stdout>

# GPU utilisation - should RISE as the CPU stops starving it
nvidia-smi --query-gpu=utilization.gpu,memory.used --format=csv -l 5
```

Success looks like: **CPU well under 100%, GPU utilisation higher than it is now, drop
counts near zero, and every module's diagnostic log showing the same detections it shows
today.** That last one is the acceptance test — the point is a faster pipeline that
detects exactly what it detected before.

---

## What this plan deliberately does not do

- **Not** change any model, threshold, cadence or detection parameter
- **Not** reduce what the AI sees — every frame the pipeline uses today, it still uses
- **Not** touch the perception child's torch/ONNX configuration
- **Not** blame miniconda, which is not the problem

## Status — implemented 2026-08-28

**Live by default (cannot change detections):**
- [x] **3. Smaller live-view encode** — `BROADCAST_WIDTH=960`, `BROADCAST_QUALITY=75`
- [x] **2. Frame ring + on-demand snapshot** — `FRAME_RING=8`; no lane pre-encodes a
      JPEG, and the snapshot is still the exact frame the decision was made on
- [x] **5. Per-iteration full-frame copy removed** — the ring makes it unnecessary
- [x] **6a. Hidden browser tabs stop streaming** — a background tab was costing a JPEG
      every 100 ms for pictures nobody could see
- [x] **Instrumentation** — `logs/perf_debug.log`, per-camera per-stage timings and a
      `cpu_pct` figure

**Implemented but OFF by default (could alter detections or stability):**
- [x] **1. Decode gating** — `DECODE_GATING=1`. Measured 60% cheaper per frame, but it
      changes WHICH frames reach the detectors
- [x] **4. OpenCV threads in the main process** — `MAIN_CV_THREADS=2..4`
- [x] **6b. Concurrent stream ceiling** — `MAX_LIVE_STREAMS=N`

**Outstanding:**
- [ ] Before/after measurement on the deployment box
- [ ] Decide whether item 1 is needed once items 2/3/5/6a are measured
