"""
Person detector backbone: YOLOv11n + ByteTrack.

Shared by Head Counting, Occupancy, Idle Time, and Intrusion — it runs once per
frame per camera and emits tracked person boxes; the analytics are cheap rules
over these tracks. Runs inside the perception child process (PyTorch / GPU).

TWO MODES, and the difference is where the tracker lives
--------------------------------------------------------
`model.track(persist=True)` keeps ByteTrack state ON THE MODEL OBJECT, so isolating
one camera's tracker from another's means one YOLO instance per camera. That is the
historical mode, and it costs a full copy of the weights per camera — measured at
~58 MB each (`backend/scripts/bench_shared_model.py`), so ten cameras carry ten copies.

`PERSON_SHARED_MODEL=1` switches to ONE model shared by every camera, with a separate
`BYTETracker` per camera held here instead. Measured identical: same track ids, same
boxes, on every frame of a real tracking sequence — the tracker is fed exactly the
detections ultralytics would have fed it, by the same five lines ultralytics uses
(`ultralytics/trackers/track.py::on_predict_postprocess_end`).

**Per-camera instances are a MEMORY cost, not a per-frame CPU cost.** Six loaded
instances were measured within 1.8% of one on a single inference — the instance is
reused, so loading ten does not make any one frame slower. Sharing the model saves
memory and is the prerequisite for batching across cameras later; it does not by itself
make the pipeline faster.

Run `python -m backend.scripts.bench_shared_model` on the deployment to reproduce all of
that against the real weights before trusting it.
"""
import os
import threading

_models: dict = {}          # cam_id -> YOLO         (per-camera mode)
_shared = None              # the single YOLO         (shared mode)
_trackers: dict = {}        # cam_id -> BYTETracker   (shared mode)
_tracker_args = None
_no_weights_logged = False
_lock = threading.Lock()
_WEIGHTS = None

# One model for every camera, with the tracker kept separately. Off by default: the
# behaviour is measured identical, but this sits directly under head-count and the trip
# wire, so it is opt-in until it has been run against site footage.
SHARED_MODEL = os.environ.get("PERSON_SHARED_MODEL", "0") not in ("0", "", "false", "False")


def _weights_path() -> str:
    global _WEIGHTS
    if _WEIGHTS is None:
        _WEIGHTS = os.environ.get("PERSON_WEIGHTS", "models/yolo11s.pt")
    return _WEIGHTS


def _weights_ok() -> bool:
    """Do the weights exist on disk?

    Checked BEFORE constructing YOLO, because ultralytics treats an unknown path as an
    asset name and tries to download it from GitHub. In production that turned a missing
    file into a per-frame network call, a per-frame 403, and a per-frame exception that
    aborted the whole frame - see the note in fire_smoke.py, where it actually happened.
    Person weights being absent is fatal to everything rather than to one module, so it
    must fail loudly ONCE and then stay quiet, not hammer an API at frame rate."""
    global _no_weights_logged
    if os.path.exists(_weights_path()):
        return True
    if not _no_weights_logged:
        _no_weights_logged = True
        print(f"[PERSON] no weights at {_weights_path()} - person tracking disabled, "
              f"so EVERY person-based module (head-count, trip wire, intrusion, crowd, "
              f"idle, occupancy) will produce nothing. Install the file or set "
              f"PERSON_WEIGHTS.", flush=True)
    return False


def _get_model(cam_key: str):
    """The model this camera should use.

    In shared mode every camera gets the same instance. That instance must NEVER have
    had `.track()` called on it: `register_tracker` attaches callbacks to the MODEL and
    never removes them, so a model that has once tracked keeps tracking on plain
    `.predict()` calls forever after, with its own hidden tracker state. Feeding that
    into our per-camera tracker double-tracks and silently changes both boxes and ids.
    This was caught by the equivalence test, not by reading the code."""
    global _shared
    if not _weights_ok():
        return None
    if SHARED_MODEL:
        if _shared is None:
            with _lock:
                if _shared is None:
                    from ultralytics import YOLO
                    _shared = YOLO(_weights_path())
                    print("[PERSON] shared model mode: one YOLO for all cameras "
                          "(PERSON_SHARED_MODEL=1).", flush=True)
        return _shared
    if cam_key not in _models:
        from ultralytics import YOLO
        _models[cam_key] = YOLO(_weights_path())
    return _models[cam_key]


def get_model(cam_key: str):
    """Public access to the YOLO model backing this camera."""
    return _get_model(cam_key)


def _get_tracker(cam_key: str):
    """This camera's ByteTrack state (shared mode only).

    Constructed from the same `bytetrack.yaml` and `frame_rate=30` that
    `ultralytics/trackers/track.py::on_predict_start` uses, so the tracker is configured
    exactly as it would have been inside the model object."""
    global _tracker_args
    if _tracker_args is None:
        from ultralytics.utils import YAML, IterableSimpleNamespace
        from ultralytics.utils.checks import check_yaml
        _tracker_args = IterableSimpleNamespace(**YAML.load(check_yaml("bytetrack.yaml")))
    tr = _trackers.get(cam_key)
    if tr is None:
        from ultralytics.trackers.byte_tracker import BYTETracker
        tr = _trackers[cam_key] = BYTETracker(args=_tracker_args, frame_rate=30)
    return tr


def reset_camera(cam_key: str) -> None:
    """Drop a camera's tracker/model state. Safe to call for an unknown camera."""
    _trackers.pop(str(cam_key), None)
    _models.pop(str(cam_key), None)


# ── Recall knobs for CROWDED scenes ──────────────────────────────────────────────
# Both of these decide whether two people standing close together are seen as two
# people or one, which is the difference between a trip wire reporting the right
# number and under-reporting a breach.
#
#   PERSON_CONF  detection confidence floor. A partially-occluded person scores lower
#                than one in the open, so a high floor drops the person behind. Lower
#                = better recall, more spurious single-frame detections (which the
#                crossing machine's track-age and movement validation already reject).
#
#   PERSON_IOU   NMS IoU threshold, and the one that matters most here. Non-maximum
#                suppression discards a box overlapping a stronger one by more than
#                this. Two people in single file can genuinely overlap by 70-80%, so
#                the DEFAULT SUPPRESSES THE PERSON BEHIND before the tracker ever sees
#                them. Raising it keeps them.
#
#                The trade-off is real and runs both ways: raise it too far and one
#                person yields two boxes, two tracks, and an over-count. Under-counting
#                a perimeter breach is worse than over-counting, but neither is free -
#                tune against footage of the actual site rather than guessing, using
#                HEADCOUNT_DEBUG=trace to see how many tracks appear.
#
# Defaults preserve the previous behaviour exactly (0.3, and ultralytics' own 0.7).
PERSON_CONF = float(os.environ.get("PERSON_CONF", "0.3"))
PERSON_IOU = float(os.environ.get("PERSON_IOU", "0.7"))


def detect_and_track(frame, cam_key: str, conf: float | None = None,
                     iou: float | None = None) -> list[dict]:
    """Return tracked persons: [{id, box:(x1,y1,x2,y2), centroid:(cx,cy)}].

    `conf` and `iou` override the module-level defaults FOR THIS CAMERA. They are
    camera-wide rather than per-module because there is exactly one person pass per
    frame, shared by every person-based module on that camera.

    Both matter for crowd scenes. A field capture on 2026-08-28 (logs/crowd_debug.log,
    camera 47) showed two people standing 0.25 body-heights apart - shoulder to shoulder
    - with STABLE track ids 862 and 941, where 941 was detected continuously and 862
    kept vanishing and returning. Not tracking churn, and not the clustering rule: NMS
    was discarding the person behind, exactly as PERSON_IOU documents below. The
    gathering never accumulated its sustain window because the pair was only ever
    complete for a few tenths of a second at a time."""
    model = _get_model(cam_key)
    if model is None:
        return []                 # no weights: no-op, never raise into the frame loop
    conf_v = PERSON_CONF if conf is None else conf
    iou_v = PERSON_IOU if iou is None else iou
    try:
        if SHARED_MODEL:
            # Detect with the shared model, then associate with THIS camera's tracker.
            # These five lines are ultralytics' own on_predict_postprocess_end, lifted
            # verbatim — deliberately, so the tracker sees exactly what it would have
            # seen inside model.track(). Verified frame-for-frame identical by
            # backend/scripts/bench_shared_model.py.
            res = model.predict(frame, classes=[0], conf=conf_v, iou=iou_v,
                                verbose=False)
            if not res:
                return []
            r = res[0]
            tracks = _get_tracker(cam_key).update(
                r.boxes.cpu().numpy(), r.orig_img, getattr(r, "feats", None))
            if len(tracks) == 0:
                return []
            import torch
            r = r[tracks[:, -1].astype(int)]
            r.update(boxes=torch.as_tensor(tracks[:, :-1]))
        else:
            res = model.track(
                frame, persist=True, classes=[0],
                conf=conf_v,
                iou=iou_v,
                tracker="bytetrack.yaml", verbose=False,
            )
            if not res:
                return []
            r = res[0]
    except Exception as e:
        print(f"[PERSON] track error: {e}", flush=True)
        return []
    out: list[dict] = []
    boxes = getattr(r, "boxes", None)
    if boxes is None or boxes.id is None:
        return out
    xyxy = boxes.xyxy.cpu().numpy()
    ids = boxes.id.cpu().numpy()
    # Detection confidence is surfaced per track so rule consumers can use it as a
    # track-quality signal (head-count diagnostics report it per observation).
    confs = boxes.conf.cpu().numpy() if getattr(boxes, "conf", None) is not None         else [0.0] * len(xyxy)
    for box, tid, cf in zip(xyxy, ids, confs):
        x1, y1, x2, y2 = [float(v) for v in box]
        out.append({
            "id": int(tid),
            "box": (x1, y1, x2, y2),
            "centroid": ((x1 + x2) / 2.0, (y1 + y2) / 2.0),
            "conf": float(cf),
        })
    return out
