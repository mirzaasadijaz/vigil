"""
Vehicle detection — COCO vehicle classes off the SHARED YOLO weights.

NO NEW MODEL. `models/yolo11s.pt` is a COCO model; the pipeline already asks it for
class 0 (person) and class 67 (cell phone). Vehicles are classes it already knows:

    2  car        3  motorcycle        5  bus        7  truck

So this is a second inference pass over weights already resident in VRAM — not a new
download, not a training run. Structure and contract follow
`backend/services/detectors/phone.py` deliberately, including the one that matters most:

    MISSING WEIGHTS LOG ONCE AND RETURN NOTHING. The module degrades to "detects no
    vehicles" rather than crashing the perception child. Preserve that when editing.

WEIGHTS / TUNING
    VEHICLE_WEIGHTS   path override; defaults to the shared models/yolo11s.pt
    VEHICLE_CLASSES   comma-separated class ids, or `all`. Default "2,3,5,7"
    VEHICLE_CONF      confidence floor (default 0.35)
    VEHICLE_IMGSZ     inference size (default 640)

WHY DETECTION IS NOT TRACKED HERE
    `person.py` uses `model.track(persist=True)` because ByteTrack identity is what
    head-count and Re-ID need. A stopped vehicle does not need identity across frames —
    the no-stopping rule matches by BOX OVERLAP over time, which is more robust for an
    object that by definition is not moving. ByteTrack is tuned for motion and gives a
    stationary object no advantage.
"""
import os

_model = None
_loaded = False

CONF = float(os.environ.get("VEHICLE_CONF", "0.35"))
IMGSZ = int(os.environ.get("VEHICLE_IMGSZ", "640"))

# COCO ids -> readable names, used for the event payload and the overlay label.
CLASS_NAMES = {1: "bicycle", 2: "car", 3: "motorcycle", 5: "bus", 7: "truck"}

# Truck Count's own class set. Deliberately NOT derived from VEHICLE_CLASSES: that is
# the no-stopping / vehicle_count filter, and the two must be able to differ.
TRUCK_CLASSES = [7]           # COCO: truck

# NOTE ON RICKSHAWS, because it is the first question anyone here asks.
# COCO has no rickshaw / auto-rickshaw / tuk-tuk class, and no three-wheeler class of
# any kind. A COCO model shown one will emit *something* - usually car, motorcycle or
# truck, and not consistently the same one. So a rickshaw CAN be counted as a vehicle,
# but it cannot be counted AS a rickshaw without a model trained to know what one is.
# Counting it under a wrong label is worse than not counting it, so nothing here
# pretends otherwise.


def _weights_path() -> str:
    env = os.environ.get("VEHICLE_WEIGHTS")
    if env:
        return env
    from backend.core.config import settings
    return os.path.join(settings.BASE_DIR, "models", "yolo11s.pt")


def _classes():
    """Class ids to keep. Default COCO 2/3/5/7; `all` disables filtering."""
    raw = os.environ.get("VEHICLE_CLASSES", "2,3,5,7").strip().lower()
    if raw in ("", "all", "none", "*"):
        return None
    try:
        return [int(v) for v in raw.split(",") if v.strip() != ""]
    except ValueError:
        return [2, 3, 5, 7]


def _get_model():
    global _model, _loaded
    if _loaded:
        return _model
    _loaded = True
    path = _weights_path()
    if not os.path.exists(path):
        print(f"[VEHICLE] no weights at {path} — vehicle detection disabled "
              f"(set VEHICLE_WEIGHTS or install models/yolo11s.pt).", flush=True)
        return None
    try:
        from ultralytics import YOLO
        _model = YOLO(path)
        print(f"[VEHICLE] loaded {path} (classes={_classes()}, conf={CONF}).",
              flush=True)
    except Exception as e:
        print(f"[VEHICLE] failed to load {path}: {e}", flush=True)
        _model = None
    return _model


def available() -> bool:
    return _get_model() is not None


def reset() -> None:
    """Drop the cached model so the next call re-reads the environment (tests)."""
    global _model, _loaded
    _model, _loaded = None, False


_trackers: dict = {}          # cam_key -> BYTETracker  (counting path only)
_tracker_args = None


def _get_tracker(cam_key: str):
    """This camera's ByteTrack state.

    One shared model with a tracker per camera, rather than a model per camera: the two
    were measured identical on the person path (backend/scripts/bench_shared_model.py)
    and this way costs one copy of the weights instead of N."""
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


def detect_and_track_vehicles(frame, cam_key: str, conf: float | None = None) -> list[dict]:
    """Vehicle boxes WITH a stable id, for counting across a line.

    Separate from `detect_vehicles` on purpose, and both are needed:

      no-stopping  cares that a vehicle has NOT moved, and matches by box overlap. A
                   stationary object has near-perfect frame-to-frame IoU, which is the
                   case a motion tracker handles worst - so it wants no tracker at all.

      counting     cares that a vehicle HAS moved, across a line. That needs identity:
                   without it there is no way to know the car at frame N is the car at
                   frame N+1, and a fast vehicle's frame-to-frame IoU is far too low to
                   substitute for one.

    Returns the same shape as `detect_vehicles` plus `id`, so a crossing rule can treat
    these exactly like person tracks."""
    return _track(frame, str(cam_key), _classes(),
                  CONF if conf is None else float(conf))


def detect_and_track_trucks(frame, cam_key: str, conf: float = 0.8) -> list[dict]:
    """Tracked TRUCKS only (COCO class 7), for the Truck Count gate rule.

    Why this is not just `detect_and_track_vehicles` with a class filter:

      * the class set is different and must stay different. VEHICLE_CLASSES is the
        no-stopping / vehicle_count filter (car, motorcycle, bus, truck) and narrowing
        it to trucks would silently break both of those modules.
      * the ByteTrack state must be SEPARATE. Both rules can be assigned to one camera,
        and they feed the tracker different detection sets each frame. Sharing one
        tracker would have each rule corrupting the other's track ids - and a crossing
        rule decides entry vs exit purely from a track's identity across frames, so
        corrupted ids do not degrade the count, they invent and lose crossings. Hence
        the `:truck` tracker namespace.

    The default confidence is deliberately high (0.8): the low-quality gate feed
    mislabels three-wheelers as trucks, and the per-camera `confidence_threshold` from
    the UI is the primary guard against that."""
    return _track(frame, f"{cam_key}:truck", TRUCK_CLASSES, float(conf))


def _track(frame, tracker_key: str, classes, conf: float) -> list[dict]:
    """Shared detect-and-track core. `tracker_key` namespaces the ByteTrack state, so
    two rules on one camera never share (and corrupt) each other's track ids."""
    model = _get_model()
    if model is None or frame is None or getattr(frame, "size", 0) == 0:
        return []
    try:
        res = model.predict(frame, classes=classes, conf=conf,
                            imgsz=IMGSZ, verbose=False)
        if not res:
            return []
        r = res[0]
        det = r.boxes.cpu().numpy()
        tracks = _get_tracker(tracker_key).update(det, r.orig_img,
                                                  getattr(r, "feats", None))
        if len(tracks) == 0:
            return []
    except Exception as e:
        print(f"[VEHICLE] track error: {e}", flush=True)
        return []

    # BYTETracker returns [x1, y1, x2, y2, id, score, cls, idx].
    out: list[dict] = []
    for t in tracks:
        try:
            x1, y1, x2, y2 = [float(v) for v in t[:4]]
            cls = int(t[6])
            out.append({
                "id": int(t[4]),
                "box": (x1, y1, x2, y2),
                "centroid": ((x1 + x2) / 2.0, (y1 + y2) / 2.0),
                "conf": float(t[5]),
                "cls": cls,
                "kind": CLASS_NAMES.get(cls, str(cls)),
            })
        except Exception:
            continue
    return out


def reset_camera(cam_key: str) -> None:
    """Drop a camera's tracker state. Safe for an unknown camera.

    Clears BOTH namespaces - a camera can carry vehicle_count and truck_count at once,
    and leaving one behind would resume counting against stale track ids."""
    _trackers.pop(str(cam_key), None)
    _trackers.pop(f"{cam_key}:truck", None)


def detect_vehicles(frame, conf: float | None = None) -> list[dict]:
    """Vehicle boxes in DET-FRAME PIXELS.

    Returns [{box: [x1,y1,x2,y2], confidence: float, cls: int, kind: str}].
    Empty list when weights are absent — never raises."""
    model = _get_model()
    if model is None or frame is None or getattr(frame, "size", 0) == 0:
        return []
    try:
        res = model.predict(frame, classes=_classes(),
                            conf=(CONF if conf is None else float(conf)),
                            imgsz=IMGSZ, verbose=False)
    except Exception as e:
        print(f"[VEHICLE] inference error: {e}", flush=True)
        return []

    out: list[dict] = []
    for r in (res or []):
        boxes = getattr(r, "boxes", None)
        if boxes is None:
            continue
        for b in boxes:
            try:
                x1, y1, x2, y2 = [float(v) for v in b.xyxy[0].tolist()]
                cls = int(b.cls[0].item())
                out.append({
                    "box": [x1, y1, x2, y2],
                    "confidence": float(b.conf[0].item()),
                    "cls": cls,
                    "kind": CLASS_NAMES.get(cls, str(cls)),
                })
            except Exception:
                continue
    return out
