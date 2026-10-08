"""
Benchmark: where does the person backbone's per-frame cost actually go, and would
batching across cameras recover any of it?

WHY THIS EXISTS
---------------
The perception child is CPU-bound, and the person backbone is the single largest item in
it — measured at ~51% of a core, ~12.4 ms per frame across 41 fps (see
unoptimized-architecture.md §2.1). The proposed fix is to decouple ByteTrack from the
YOLO model object so that one shared model can run BATCHED inference across cameras,
instead of one `model.track()` call per camera per frame.

That refactor sits directly underneath head-count and the trip wire, which have already
been validated against real site footage. It should not be attempted on a hypothesis.
This script produces the number that decides it.

THE QUESTION IT ANSWERS
-----------------------
Batching only pays if a meaningful share of the per-frame cost is FIXED PER CALL rather
than PER IMAGE. Three of the four stages are per image and batching cannot remove them:

    preprocess    letterbox + BGR->RGB + HWC->CHW + to(device)   per image
    inference     the forward pass                               per image (batched on GPU)
    postprocess   NMS — ultralytics loops per image               per image
    tracker       ByteTrack association                           per CAMERA, never batchable

What batching removes is the per-call Python/ultralytics setup and the CUDA launch and
sync overhead. So the honest question is: how big is that fixed part? Test C measures it
directly by comparing N separate calls against one call with a batch of N.

    If batching saves <15% of the person budget, the refactor is not worth its blast
    radius — sharding already raises the ceiling with no detection risk at all.

WHAT ELSE IT CHECKS
-------------------
Test B: does input width matter? `detectors/person.py` never passes `imgsz`, so it
silently inherits ultralytics' default of 640. (`smoking.py` is the same, but it is not
assigned on this deployment; phone, vehicle and fire all set it explicitly.) On the slow lane the
main process resizes to DET_WIDTH=1280 and ships a 1280x720 array across the process
boundary, and ultralytics then letterboxes it straight back down to 640x384 for this
pass. The 1280 width exists for the FACE backbone (det_size 640x640 over a wider frame
resolves distant faces), so it cannot simply be lowered — but if the person pass is
paying materially for pixels it discards, that is worth knowing before anything else.

Test D: what does one-model-instance-per-camera actually cost? The answer is VRAM, not
CPU — the instance is reused, so nine instances do not make one frame slower. This
measures the VRAM so the trade-off is a number rather than an assumption.

RUN IT ON THE DEPLOYMENT
------------------------
It needs the real weights and the real GPU; the numbers mean nothing anywhere else.

    python -m backend.scripts.bench_person                          # synthetic frames
    python -m backend.scripts.bench_person --source path/to/clip.mp4    # far better
    python -m backend.scripts.bench_person --source frame.jpg --cameras 3

Prefer --source with real footage. Postprocess cost scales with how many boxes survive
NMS, so synthetic noise (which detects nothing) understates it — the script says so in
its output rather than letting you forget.

Reads nothing from the running pipeline and writes nothing. Safe to run alongside it,
though it will compete for the GPU, so prefer a quiet moment.
"""

from __future__ import annotations

import argparse
import os
import statistics
import sys
import time

DEFAULT_WEIGHTS = "models/yolo11s.pt"
WARMUP = 5
ITERS = 40

# Mirrors detectors/person.py, so the benchmark measures what the pipeline actually runs.
PERSON_CONF = float(os.environ.get("PERSON_CONF", "0.3"))
PERSON_IOU = float(os.environ.get("PERSON_IOU", "0.7"))

# The threshold this script exists to test against. Below this, the refactor costs more
# in risk than it returns in throughput.
WORTH_IT_PCT = 15.0


def _fmt(ms: float) -> str:
    return f"{ms:7.2f}"


class Timer:
    """Wall and CPU time for one stage.

    Both, because they answer different questions: wall includes waiting for the GPU,
    CPU (thread_time) does not. A gap between them is GPU wait; a small gap on a busy
    box means the work really is CPU-bound, which is the finding this whole exercise
    rests on."""

    def __init__(self):
        self.wall: list[float] = []
        self.cpu: list[float] = []

    def __enter__(self):
        self._w = time.perf_counter()
        self._c = time.thread_time()
        return self

    def __exit__(self, *a):
        self.wall.append((time.perf_counter() - self._w) * 1000.0)
        self.cpu.append((time.thread_time() - self._c) * 1000.0)

    @property
    def wall_ms(self) -> float:
        return statistics.median(self.wall) if self.wall else 0.0

    @property
    def cpu_ms(self) -> float:
        return statistics.median(self.cpu) if self.cpu else 0.0


def load_frames(source: str | None, n: int, width: int):
    """n frames at the given width. Real footage if given, synthetic otherwise."""
    import cv2
    import numpy as np

    frames = []
    if source and os.path.isfile(source):
        if source.lower().endswith((".mp4", ".avi", ".mkv", ".mov")):
            cap = cv2.VideoCapture(source)
            while len(frames) < n:
                ok, f = cap.read()
                if not ok:
                    break
                frames.append(f)
            cap.release()
        else:
            img = cv2.imread(source)
            if img is not None:
                frames = [img.copy() for _ in range(n)]
    if not frames:
        # Structured noise, not flat grey: flat frames make NMS trivially cheap and would
        # flatter the postprocess numbers.
        rng = np.random.default_rng(0)
        frames = [rng.integers(0, 255, (720, 1280, 3), dtype=np.uint8) for _ in range(n)]

    out = []
    for f in frames:
        h, w = f.shape[:2]
        if w != width:
            f = cv2.resize(f, (width, int(h * width / w)))
        out.append(f)
    return out


def stage_breakdown(model, frames, imgsz=None):
    """Per-frame cost split by ultralytics' own instrumentation.

    `r.speed` reports preprocess / inference / postprocess in ms. That split is the whole
    argument: preprocess and postprocess are per-image and survive batching; only the
    per-call overhead does not."""
    kw = dict(classes=[0], conf=PERSON_CONF, iou=PERSON_IOU, verbose=False)
    if imgsz:
        kw["imgsz"] = imgsz
    pre, inf, post, dets = [], [], [], []
    for f in frames:
        r = model.predict(f, **kw)[0]
        s = getattr(r, "speed", {}) or {}
        pre.append(s.get("preprocess", 0.0))
        inf.append(s.get("inference", 0.0))
        post.append(s.get("postprocess", 0.0))
        dets.append(0 if r.boxes is None else len(r.boxes))
    med = statistics.median
    return {"preprocess": med(pre), "inference": med(inf), "postprocess": med(post),
            "detections": med(dets)}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--weights", default=DEFAULT_WEIGHTS)
    ap.add_argument("--source", default=None,
                    help="image or video of the real site - strongly preferred")
    ap.add_argument("--cameras", type=int, default=3,
                    help="batch size to test, i.e. cameras per shard (default 3)")
    ap.add_argument("--iters", type=int, default=ITERS)
    ap.add_argument("--widths", default="640,1280",
                    help="input widths to compare (default 640,1280 = fast/slow lane)")
    args = ap.parse_args()

    try:
        import torch
        from ultralytics import YOLO
    except ImportError as e:
        print(f"needs torch + ultralytics in this environment: {e}")
        return 2
    if not os.path.isfile(args.weights):
        print(f"weights not found: {args.weights}")
        print("Run this on the deployment box — the numbers mean nothing without the "
              "real weights and GPU.")
        return 2

    cuda = torch.cuda.is_available()
    synthetic = not (args.source and os.path.isfile(args.source))
    print("=" * 78)
    print("PERSON BACKBONE BENCHMARK")
    print("=" * 78)
    print(f"weights   {args.weights}")
    print(f"device    {'cuda:0 ' + torch.cuda.get_device_name(0) if cuda else 'CPU ONLY'}")
    print(f"source    {args.source if not synthetic else 'SYNTHETIC NOISE'}")
    print(f"conf/iou  {PERSON_CONF} / {PERSON_IOU}   (from the same env as the pipeline)")
    if synthetic:
        print()
        print("  !! Synthetic frames detect almost nothing, so NMS and the tracker are")
        print("     doing far less work here than in deployment. Postprocess and tracker")
        print("     figures below are LOWER BOUNDS. Re-run with --source <site clip>.")
    if not cuda:
        print()
        print("  !! No CUDA. Every number below is a CPU-only forward pass and does not")
        print("     represent the deployment at all.")
    print()

    widths = [int(w) for w in args.widths.split(",") if w.strip()]
    model = YOLO(args.weights)

    # ── A. Where the per-frame cost goes ─────────────────────────────────────
    print("-" * 78)
    print("A. Per-frame cost breakdown, and how much of it batching could ever remove")
    print("-" * 78)
    base_w = widths[0]
    frames = load_frames(args.source, max(args.iters, WARMUP), base_w)
    for f in frames[:WARMUP]:
        model.predict(f, classes=[0], conf=PERSON_CONF, iou=PERSON_IOU, verbose=False)

    br = stage_breakdown(model, frames[:args.iters])
    total = br["preprocess"] + br["inference"] + br["postprocess"]
    print(f"  input width {base_w}, median over {args.iters} frames, "
          f"{br['detections']:.0f} detections/frame")
    print()
    print(f"  {'stage':<14}{'ms':>8}   {'share':>7}   batchable?")
    for name, key in (("preprocess", "preprocess"), ("inference", "inference"),
                      ("postprocess", "postprocess")):
        ms = br[key]
        note = {"preprocess": "no  — per image (letterbox, HWC->CHW)",
                "inference": "YES — one forward pass for the batch",
                "postprocess": "no  — ultralytics loops NMS per image"}[key]
        print(f"  {name:<14}{_fmt(ms)}   {ms / total * 100 if total else 0:6.1f}%   {note}")
    print(f"  {'TOTAL':<14}{_fmt(total)}")
    print()

    # ── B. Does input width matter? ──────────────────────────────────────────
    if len(widths) > 1:
        print("-" * 78)
        print("B. Input width — person.py never passes imgsz, so it inherits 640")
        print("-" * 78)
        print(f"  {'width':>6}{'pre':>9}{'inf':>9}{'post':>9}{'total':>9}   note")
        for w in widths:
            fr = load_frames(args.source, args.iters, w)
            for f in fr[:WARMUP]:
                model.predict(f, classes=[0], conf=PERSON_CONF, iou=PERSON_IOU,
                              verbose=False)
            b = stage_breakdown(model, fr[:args.iters])
            t = b["preprocess"] + b["inference"] + b["postprocess"]
            note = "fast lane (DET_WIDTH_FAST)" if w == 640 else \
                   ("slow lane (DET_WIDTH) — letterboxed back to 640 for this pass"
                    if w == 1280 else "")
            print(f"  {w:>6}{_fmt(b['preprocess'])}{_fmt(b['inference'])}"
                  f"{_fmt(b['postprocess'])}{_fmt(t):>9}   {note}")
        print()
        print("  The slow lane ships a wider frame across the process boundary for the")
        print("  FACE backbone's benefit. If the person figures above differ materially")
        print("  by width, that transport is costing this pass too — but do NOT simply")
        print("  lower DET_WIDTH: face recognition is what needs those pixels.")
        print()

    # ── C. The decision: does batching actually help? ────────────────────────
    print("-" * 78)
    print(f"C. THE DECISION — {args.cameras} separate calls vs one batch of {args.cameras}")
    print("-" * 78)
    n = args.cameras
    batch = load_frames(args.source, n, base_w)
    kw = dict(classes=[0], conf=PERSON_CONF, iou=PERSON_IOU, verbose=False)
    for _ in range(WARMUP):
        model.predict(batch, **kw)
        for f in batch:
            model.predict(f, **kw)

    seq, bat = Timer(), Timer()
    for _ in range(args.iters):
        with seq:
            for f in batch:
                model.predict(f, **kw)
        if cuda:
            torch.cuda.synchronize()
        with bat:
            model.predict(batch, **kw)
        if cuda:
            torch.cuda.synchronize()

    seq_pf, bat_pf = seq.wall_ms / n, bat.wall_ms / n
    seq_cpu, bat_cpu = seq.cpu_ms / n, bat.cpu_ms / n
    saving = (seq_pf - bat_pf) / seq_pf * 100 if seq_pf else 0.0
    cpu_saving = (seq_cpu - bat_cpu) / seq_cpu * 100 if seq_cpu else 0.0
    print(f"  {'':<26}{'wall/frame':>12}{'cpu/frame':>12}")
    print(f"  {f'{n} separate calls':<26}{_fmt(seq_pf)}{'ms':<3}{_fmt(seq_cpu)}ms")
    print(f"  {f'one batch of {n}':<26}{_fmt(bat_pf)}{'ms':<3}{_fmt(bat_cpu)}ms")
    print(f"  {'saving':<26}{saving:9.1f}%   {cpu_saving:9.1f}%")
    print()

    # ── D. What per-camera instances actually cost ───────────────────────────
    if cuda:
        print("-" * 78)
        print("D. VRAM cost of one model instance per camera (the current design)")
        print("-" * 78)
        torch.cuda.synchronize()
        before = torch.cuda.memory_allocated() / 1024 ** 2
        extra = [YOLO(args.weights) for _ in range(3)]
        for m in extra:
            m.predict(batch[0], **kw)
        torch.cuda.synchronize()
        after = torch.cuda.memory_allocated() / 1024 ** 2
        per = (after - before) / 3
        print(f"  per extra instance   {per:6.1f} MB")
        print(f"  9 cameras            {per * 9:6.1f} MB   (current)")
        print(f"  1 shared instance    {per:6.1f} MB   (after the refactor)")
        print(f"  saving               {per * 8:6.1f} MB of "
              f"{torch.cuda.get_device_properties(0).total_memory / 1024 ** 3:.1f} GB")
        del extra
        torch.cuda.empty_cache()
        print()
        print("  Note this is VRAM, not CPU. Nine instances do not make one frame")
        print("  slower — the instance is reused. Per-camera instances are a memory")
        print("  cost and a batching blocker, not a per-frame cost.")
        print()

    # ── Verdict ──────────────────────────────────────────────────────────────
    print("=" * 78)
    print("VERDICT")
    print("=" * 78)
    unbatchable = (br["preprocess"] + br["postprocess"]) / total * 100 if total else 0
    print(f"  {unbatchable:.0f}% of the per-frame cost is per-image work that batching")
    print(f"  cannot remove, before ByteTrack association is even counted.")
    print()
    print(f"  Measured batching saving: {saving:.1f}% wall, {cpu_saving:.1f}% CPU.")
    print()
    if saving < WORTH_IT_PCT:
        print(f"  -> BELOW the {WORTH_IT_PCT:.0f}% bar. Decoupling ByteTrack from the model")
        print("     object is NOT worth its blast radius: it sits directly under")
        print("     head-count and the trip wire, which are already validated against")
        print("     site footage. Shard instead — same ceiling gain, no detection risk.")
    else:
        print(f"  -> ABOVE the {WORTH_IT_PCT:.0f}% bar. The refactor is justified. It is")
        print("     smaller than it looks: BYTETracker already runs standalone in this")
        print("     repo (backend/scripts/bench_tracking_cadence.py instantiates it")
        print("     directly), so the change is a shared model + a per-camera tracker")
        print("     dict, not a rewrite. Validate against the same footage before and")
        print("     after — identical track ids, identical crossings.")
    print()
    print("  Either way, the tracker is per-camera and can never be batched, and this")
    print("  measurement excludes it — so the real-world saving is lower than the")
    print("  figure above, not higher.")
    if synthetic:
        print()
        print("  !! Synthetic frames. Re-run with --source before deciding anything.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
