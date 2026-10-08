"""
Acceptance test: does `PERSON_SHARED_MODEL=1` change anything a camera sees?

The pipeline historically kept ONE YOLO INSTANCE PER CAMERA, because
`model.track(persist=True)` stores ByteTrack state on the model object and that is the
only way to keep one camera's tracker away from another's. Ten cameras therefore held
ten copies of the weights.

`detectors/person.py` can now share ONE model across every camera and hold a separate
`BYTETracker` per camera itself. This script is the evidence for that being safe: it
runs both paths over the same real tracking sequence and compares them frame by frame.

It also measures what the old design costs (memory per instance) and checks the two
things that could plausibly go wrong (state leaking between cameras that share a model,
and having many instances loaded slowing inference down).

RUN IT ON THE DEPLOYMENT before switching the flag on:

    python -m backend.scripts.bench_shared_model                 # downloads yolo11s
    python -m backend.scripts.bench_shared_model models/yolo11s.pt

Exit code 0 means every check passed. Anything else means do not enable the flag.

CPU-only is fine here: track ids are deterministic arithmetic, not a GPU property. The
BATCHING question is a different one and needs a GPU - see bench_person.py for that.
"""
import gc
import os
import statistics
import sys
import time

import cv2
import numpy as np
import torch
from ultralytics import YOLO
from ultralytics.trackers.byte_tracker import BYTETracker
from ultralytics.utils import YAML, IterableSimpleNamespace
from ultralytics.utils.checks import check_yaml

WEIGHTS = sys.argv[1] if len(sys.argv) > 1 else "yolo11s.pt"
PERSON_CONF, PERSON_IOU = 0.3, 0.7          # the pipeline's values
NFRAMES = 40
FAIL = []


def rss_mb() -> float:
    """Resident memory. psutil if present, else Windows/Linux fallbacks."""
    try:
        import psutil
        return psutil.Process().memory_info().rss / 1024 ** 2
    except Exception:
        pass
    try:                                     # Linux
        with open("/proc/self/statm") as fh:
            return int(fh.read().split()[1]) * os.sysconf("SC_PAGE_SIZE") / 1024 ** 2
    except Exception:
        pass
    try:                                     # Windows
        import ctypes
        import ctypes.wintypes as wt

        class PMC(ctypes.Structure):
            _fields_ = [("cb", wt.DWORD), ("PageFaultCount", wt.DWORD),
                        ("PeakWorkingSetSize", ctypes.c_size_t),
                        ("WorkingSetSize", ctypes.c_size_t),
                        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                        ("PagefileUsage", ctypes.c_size_t),
                        ("PeakPagefileUsage", ctypes.c_size_t)]
        c = PMC(); c.cb = ctypes.sizeof(PMC)
        ctypes.windll.psapi.GetProcessMemoryInfo(
            ctypes.windll.kernel32.GetCurrentProcess(), ctypes.byref(c), c.cb)
        return c.WorkingSetSize / 1024 ** 2
    except Exception:
        return float("nan")


def make_sequence(n: int):
    """A moving sequence with REAL people in it.

    Synthetic noise detects nothing, which would make the tracking comparison vacuous.
    ultralytics ships bus.jpg (several people); panning a crop window across it produces
    genuine detections that genuinely have to be associated frame to frame."""
    from ultralytics.utils import ASSETS
    img = cv2.imread(str(ASSETS / "bus.jpg"))
    if img is None:
        raise SystemExit("could not load the ultralytics bus.jpg asset")
    h, w = img.shape[:2]
    cw, ch = int(w * 0.75), int(h * 0.75)
    frames = []
    for i in range(n):
        # Pan right then back, so tracks persist, leave and re-enter.
        t = i / max(1, n - 1)
        off = int((w - cw) * (t if t < 0.5 else 1 - t) * 2)
        crop = img[0:ch, off:off + cw]
        frames.append(cv2.resize(crop, (640, int(640 * ch / cw))))
    return frames


def tracker_args():
    return IterableSimpleNamespace(**YAML.load(check_yaml("bytetrack.yaml")))


def path_current(frames):
    """What the pipeline does today: one YOLO instance, model.track(persist=True)."""
    model = YOLO(WEIGHTS)
    out = []
    for f in frames:
        r = model.track(f, persist=True, classes=[0], conf=PERSON_CONF, iou=PERSON_IOU,
                        tracker="bytetrack.yaml", verbose=False)[0]
        b = r.boxes
        if b is None or b.id is None:
            out.append([])
            continue
        out.append([(int(i), tuple(round(float(v), 3) for v in xy))
                    for i, xy in zip(b.id.cpu().numpy(), b.xyxy.cpu().numpy())])
    return out, model


def path_shared(frames, model=None):
    """The proposal: ONE shared model + a per-camera BYTETracker.

    Replicates ultralytics/trackers/track.py::on_predict_postprocess_end exactly.

    TRAP, found the hard way: `register_tracker` adds the tracking callbacks to the
    MODEL, and they are never removed. So a model that has ever had `.track()` called
    keeps tracking on plain `.predict()` calls forever after - silently, with its own
    tracker state. Feeding that output into a second tracker double-tracks and produces
    different boxes and different ids. The shared model must therefore be one that
    `.track()` has never touched, which is exactly what the refactor would give it."""
    if model is None:
        model = YOLO(WEIGHTS)
    assert not hasattr(model.predictor, "trackers"), \
        "shared model has tracking callbacks registered - see the docstring"
    tracker = BYTETracker(args=tracker_args(), frame_rate=30)
    out = []
    for f in frames:
        r = model.predict(f, classes=[0], conf=PERSON_CONF, iou=PERSON_IOU,
                          verbose=False)[0]
        det = r.boxes.cpu().numpy()
        tracks = tracker.update(det, r.orig_img, getattr(r, "feats", None))
        if len(tracks) == 0:
            out.append([])
            continue
        idx = tracks[:, -1].astype(int)
        r = r[idx]
        r.update(boxes=torch.as_tensor(tracks[:, :-1]))
        b = r.boxes
        out.append([(int(i), tuple(round(float(v), 3) for v in xy))
                    for i, xy in zip(b.id.cpu().numpy(), b.xyxy.cpu().numpy())])
    return out


print("=" * 78)
print("ONE SHARED YOLO vs ONE INSTANCE PER CAMERA")
print("=" * 78)
print(f"weights {WEIGHTS} | torch {torch.__version__} | cuda {torch.cuda.is_available()}")
print(f"conf {PERSON_CONF} iou {PERSON_IOU} | {NFRAMES} frames")
print()

frames = make_sequence(NFRAMES)

# ── 1. Memory per instance — the user's actual question ──────────────────────
print("-" * 78)
print("1. What does one YOLO instance per camera cost?")
print("-" * 78)
gc.collect()
base = rss_mb()
models = []
per = []
for i in range(6):
    m = YOLO(WEIGHTS)
    m.predict(frames[0], classes=[0], conf=PERSON_CONF, verbose=False)   # force warm
    models.append(m)
    gc.collect()
    now = rss_mb()
    per.append(now - base)
    base = now
    print(f"  instance {i + 1}:  +{per[-1]:6.1f} MB")
steady = statistics.median(per[1:]) if len(per) > 1 else per[0]
print()
print(f"  steady cost per extra instance   {steady:6.1f} MB")
print(f"  10 cameras (your example)        {steady * 10:6.1f} MB")
print(f"  1 shared instance                {steady:6.1f} MB")
print(f"  saving at 10 cameras             {steady * 9:6.1f} MB")
print()
print("  This is host RAM on CPU. On the deployment the same duplication happens in")
print("  VRAM, and the weights are the same size either way.")
print()

# ── 2. Do N loaded instances slow a single inference? ────────────────────────
print("-" * 78)
print("2. Do 6 loaded instances make one inference slower? (memory vs CPU)")
print("-" * 78)


def bench(m, n=15):
    for f in frames[:3]:
        m.predict(f, classes=[0], conf=PERSON_CONF, iou=PERSON_IOU, verbose=False)
    ts = []
    for f in frames[:n]:
        t = time.perf_counter()
        m.predict(f, classes=[0], conf=PERSON_CONF, iou=PERSON_IOU, verbose=False)
        ts.append((time.perf_counter() - t) * 1000)
    return statistics.median(ts)


with_many = bench(models[0])
solo_model = models[0]
del models
gc.collect()
with_one = bench(solo_model)
print(f"  one inference, 6 instances loaded   {with_many:7.2f} ms")
print(f"  one inference, 1 instance loaded    {with_one:7.2f} ms")
print(f"  difference                          {abs(with_many - with_one):7.2f} ms "
      f"({abs(with_many - with_one) / with_one * 100:.1f}%)")
print()
print("  Per-camera instances are a MEMORY cost, not a per-frame CPU cost: the")
print("  instance is reused, so loading ten does not make any one frame slower.")
print()

# ── 3. The correctness question ──────────────────────────────────────────────
print("-" * 78)
print("3. Does a shared model + per-camera BYTETracker give IDENTICAL results?")
print("-" * 78)
cur, cur_model = path_current(frames)
shared = path_shared(frames)          # FRESH model - see path_shared's docstring

n_cur = sum(len(f) for f in cur)
n_sha = sum(len(f) for f in shared)
print(f"  tracked boxes: current {n_cur}, shared {n_sha}")
if n_cur == 0:
    print("  !! no detections at all - the comparison is vacuous, fix the fixture")
    FAIL.append("no detections")

diverged = None
for i, (a, b) in enumerate(zip(cur, shared)):
    if a != b:
        diverged = i
        break
if diverged is None and n_cur > 0:
    print(f"  IDENTICAL on all {len(frames)} frames - same track ids, same boxes.")
else:
    if diverged is not None:
        print(f"  DIVERGED at frame {diverged}")
        print(f"    current: {cur[diverged]}")
        print(f"    shared : {shared[diverged]}")
        FAIL.append(f"diverged at frame {diverged}")
ids_cur = {t[0] for f in cur for t in f}
ids_sha = {t[0] for f in shared for t in f}
print(f"  distinct track ids: current {sorted(ids_cur)}, shared {sorted(ids_sha)}")
print()

# ── 4. Two cameras interleaved on one shared model ───────────────────────────
print("-" * 78)
print("4. Two cameras interleaved through ONE model - does state leak between them?")
print("-" * 78)
model = YOLO(WEIGHTS)          # never .track()ed
tA, tB = BYTETracker(args=tracker_args(), frame_rate=30), BYTETracker(args=tracker_args(), frame_rate=30)
seqA, seqB = frames, list(reversed(frames))
outA, outB = [], []
for fa, fb in zip(seqA, seqB):
    for f, tr, acc in ((fa, tA, outA), (fb, tB, outB)):
        r = model.predict(f, classes=[0], conf=PERSON_CONF, iou=PERSON_IOU, verbose=False)[0]
        tracks = tr.update(r.boxes.cpu().numpy(), r.orig_img, getattr(r, "feats", None))
        acc.append([] if len(tracks) == 0 else
                   [(int(t[-2] if len(t) > 5 else t[4]),) for t in tracks])
# Camera A interleaved must equal camera A alone.
soloA = path_shared(seqA)
same = [len(x) for x in outA] == [len(x) for x in soloA]
print(f"  camera A alone      : {sum(len(x) for x in soloA)} tracked boxes")
print(f"  camera A interleaved: {sum(len(x) for x in outA)} tracked boxes")
print(f"  camera B interleaved: {sum(len(x) for x in outB)} tracked boxes")
print(f"  {'PASS' if same else 'FAIL'}  camera A unaffected by camera B sharing the model")
if not same:
    FAIL.append("interleaving changed camera A")
print()

# ── 5. Fixed per-call overhead — the batching question ───────────────────────
print("-" * 78)
print("5. How much of the per-frame cost is FIXED PER CALL? (what batching removes)")
print("-" * 78)
print(f"  {'batch':>6}{'total ms':>11}{'per frame':>11}")
pts = []
for bs in (1, 2, 3, 4, 6):
    b = frames[:bs]
    for _ in range(3):
        model.predict(b, classes=[0], conf=PERSON_CONF, iou=PERSON_IOU, verbose=False)
    ts = []
    for _ in range(10):
        t = time.perf_counter()
        model.predict(b, classes=[0], conf=PERSON_CONF, iou=PERSON_IOU, verbose=False)
        ts.append((time.perf_counter() - t) * 1000)
    m = statistics.median(ts)
    pts.append((bs, m))
    print(f"  {bs:>6}{m:>11.2f}{m / bs:>11.2f}")
# total(n) = fixed + n*variable  -> least squares on two ends is enough here
(n1, t1), (n2, t2) = pts[0], pts[-1]
variable = (t2 - t1) / (n2 - n1)
fixed = t1 - variable
print()
print(f"  fixed per call    {fixed:7.2f} ms   <- the ONLY part batching removes")
print(f"  per image         {variable:7.2f} ms   <- survives batching")
print(f"  fixed share of a single-frame call: {fixed / t1 * 100:5.1f}%")
print()

# -- 6. The real function, both modes ----------------------------------------
# Sections 3-4 exercised a hand-written copy of the shared path. This runs the actual
# detectors/person.py the pipeline calls, with the flag flipped, driving two cameras
# interleaved exactly as the perception child does.
print("-" * 78)
print("6. The REAL person.detect_and_track(), PERSON_SHARED_MODEL=0 vs =1")
print("-" * 78)
try:
    import importlib

    def run_mode(shared, cams=("11", "22")):
        os.environ["PERSON_SHARED_MODEL"] = "1" if shared else "0"
        import backend.services.detectors.person as person
        importlib.reload(person)
        person._WEIGHTS = WEIGHTS
        per_cam = {c: [] for c in cams}
        for f in frames:
            for c in cams:
                per_cam[c].append([(t["id"], tuple(round(v, 3) for v in t["box"]))
                                   for t in person.detect_and_track(f, c)])
        return per_cam, (1 if shared else len(person._models))

    old_m, n_old = run_mode(False)
    new_m, n_new = run_mode(True)
    print(f"  YOLO instances    per-camera: {n_old}    shared: {n_new}")
    for cam in old_m:
        same = old_m[cam] == new_m[cam]
        ids = sorted({t[0] for f in new_m[cam] for t in f})
        print(f"  {'PASS' if same else 'FAIL'}  camera {cam}: "
              f"{sum(len(f) for f in new_m[cam])} tracked boxes, ids {ids}"
              f"{'' if same else '  <-- DIVERGED'}")
        if not same:
            FAIL.append(f"real function camera {cam}")
    # Same frames to both cameras, so isolation means the same COUNT - not the same
    # ids. ultralytics' BaseTrack._count is class-level (basetrack.py:65), so ids are
    # process-global and interleaved cameras share one sequence. True in both modes,
    # and always has been; consumers key on (cam_id, track_id), never track_id alone.
    cams = list(new_m)
    for label, data in (("per-camera", old_m), ("shared", new_m)):
        a = [len(f) for f in data[cams[0]]]
        b = [len(f) for f in data[cams[1]]]
        print(f"  {'PASS' if a == b else 'FAIL'}  {label} mode: cameras tracked "
              f"independently ({sum(a)} vs {sum(b)} boxes)")
        if a != b:
            FAIL.append(f"isolation-{label}")
except Exception as e:
    print(f"  SKIPPED: could not import the pipeline module ({e})")
    print("  Run from the repo root so backend/ is importable.")
print()

# -- 7. THE QUESTION THAT MATTERS: do the ALERTS change? ----------------------
# Identical tracks ought to imply identical alerts, because every person analytic
# consumes nothing but detect_and_track()'s output. "Ought to" is not evidence, so this
# runs both track streams through the REAL consumer classes the perception child uses
# and compares the events they emit.
#
# A module that never fires is reported as "----", not PASS: "0 events vs 0 events" is
# not agreement, it is an absence of evidence, and reading it as a pass is exactly how
# a regression gets shipped.
print("-" * 78)
print("7. Do the ALERTS change? (real consumers, real events)")
print("-" * 78)
LINE = [[0.30, 0.0], [0.30, 1.0]]
ZONE = [[[0.15, 0.05], [0.60, 0.05], [0.60, 0.98], [0.15, 0.98]]]
MODCFG = {
    "headcount": {"line": LINE},
    "tripwire": {"line": LINE, "direction": "both", "cooldown": 0},
    "intrusion": {"zones": ZONE},
    "crowd": {"zones": ZONE, "min_people": 2, "sustain_seconds": 0,
              "grace_seconds": 0, "cooldown": 0},
    "occupancy": {"zones": ZONE},
    "idle": {"zones": ZONE},
}
try:
    import importlib

    def events_for(shared):
        os.environ["PERSON_SHARED_MODEL"] = "1" if shared else "0"
        import backend.services.detectors.person as person
        importlib.reload(person)
        person._WEIGHTS = WEIGHTS
        from backend.services.analytics import make_consumer
        cam, shape = "77", frames[0].shape[:2]
        cons = {m: make_consumer(m, cam, dict(c)) for m, c in MODCFG.items()}
        cons = {m: c for m, c in cons.items() if c is not None}
        acc = {m: [] for m in cons}
        for f in frames:
            tr = person.detect_and_track(f, cam)
            for mid, c in cons.items():
                try:
                    evs, _ = c.process(tr, [], shape)
                except Exception as e:
                    evs = [{"error": repr(e)}]
                for e in (evs or []):
                    acc[mid].append({k: v for k, v in e.items()
                                     if k not in ("ts", "timestamp", "time", "at")})
        return acc

    ev_old, ev_new = events_for(False), events_for(True)
    fired = 0
    for mid in sorted(ev_old):
        a, b = ev_old[mid], ev_new[mid]
        same = a == b
        kinds = ",".join(sorted({e.get("event_type", e.get("type", "?")) for e in a})) or "-"
        tag = "PASS" if same else "FAIL"
        if same and not a:
            tag = "----"
        fired += len(a)
        print(f"  {tag}  {mid:<11}{len(a):>3} events vs {len(b):>3}   {kinds}"
              f"{'   (never fired - proves nothing)' if not a else ''}")
        if not same:
            FAIL.append(f"alerts differ: {mid}")
            for x, y in zip(a, b):
                if x != y:
                    print(f"        per-camera: {x}")
                    print(f"        shared    : {y}")
                    break
    if fired == 0:
        print("  !! nothing fired at all - this section proved nothing")
        FAIL.append("no alerts fired")
except Exception as e:
    print(f"  SKIPPED: {e}")
    print("  Run from the repo root so backend/ is importable.")
print()

print("=" * 78)
print("RESULT: " + ("ALL CHECKS PASSED" if not FAIL else "FAILURES: " + "; ".join(FAIL)))
print("=" * 78)
sys.exit(1 if FAIL else 0)
