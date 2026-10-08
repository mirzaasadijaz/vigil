"""
Fire/Smoke detector backbone: fine-tuned YOLO (custom).
Runs inside the perception child process (PyTorch / GPU).
class 0 = Fire, class 1 = Smoke
"""
import os

_model = None
_loaded = False             # have we ATTEMPTED to load yet (load-once, even on failure)
_WEIGHTS = os.environ.get("FIRE_SMOKE_WEIGHTS", "models/fire_smoke.pt")
_CONF_DEFAULT = 0.75
_IMGSZ = 640

CLASS_MAP = {0: "fire", 1: "smoke"}


def _weights_path() -> str:
    return _WEIGHTS


def _get_model():
    """Load once, even on failure.

    THE EXISTENCE CHECK IS LOAD-BEARING, and its absence took the pipeline down in
    production. `YOLO("models/fire_smoke.pt")` on a missing file does not simply fail:
    ultralytics treats an unknown path as an asset name and tries to DOWNLOAD it from
    GitHub. On a server that meant a hit to api.github.com per frame, a 403 rate-limit
    warning per frame, and then a FileNotFoundError raised from OUTSIDE the try block in
    detect_fire_smoke() - which escaped into the perception loop and aborted the whole
    frame. Every frame. The queue filled and 228,657 frames were dropped on one camera
    while every other module on the machine went blind.

    Checking the path first means no network call is ever attempted, and `_loaded`
    means the diagnosis is printed once rather than at frame rate. This is the same
    shape as phone.py, smoking.py, vehicle.py and reid.py; this module was the only
    one that never got it. CLAUDE.md states the contract: a detector that finds no
    weights logs once and no-ops rather than crashing the pipeline."""
    global _model, _loaded
    if _loaded:
        return _model
    _loaded = True
    path = _weights_path()
    if not os.path.exists(path):
        print(f"[FIRE_SMOKE] no weights at {path} - fire/smoke detection disabled "
              f"(drop a trained fire_smoke.pt there or set FIRE_SMOKE_WEIGHTS). "
              f"Every other module is unaffected.", flush=True)
        return None
    try:
        from ultralytics import YOLO
        _model = YOLO(path)
        print(f"[FIRE_SMOKE] loaded weights: {path}", flush=True)
    except Exception as e:
        print(f"[FIRE_SMOKE] failed to load {path}: {e}", flush=True)
        _model = None
    return _model


def available() -> bool:
    return _get_model() is not None


def reset_for_tests() -> None:
    """Drop the cached model so a test can re-exercise the load path."""
    global _model, _loaded
    _model, _loaded = None, False


def detect_fire_smoke(frame, key: str = "global", conf: float = None, imgsz: int = None) -> list[dict]:
    """Return fire/smoke detections: [{class_id, class_name, conf, box:(x1,y1,x2,y2)}] in pixel coords."""
    model = _get_model()
    if model is None:
        return []                 # no weights: no-op, never raise into the frame loop
    threshold = conf if conf is not None else _CONF_DEFAULT
    size = imgsz or _IMGSZ
    try:
        results = model.predict(frame, conf=threshold, verbose=False, imgsz=size)
    except Exception as e:
        print(f"[FIRE_SMOKE] detect error: {e}", flush=True)
        return []
    out: list[dict] = []
    for r in results:
        for b in getattr(r, "boxes", []) or []:
            try:
                cls_id = int(b.cls[0])
                x1, y1, x2, y2 = map(float, b.xyxy[0])
                confv = float(b.conf[0])
                out.append({
                    "class_id": cls_id,
                    "class_name": CLASS_MAP.get(cls_id, "unknown"),
                    "conf": confv,
                    "box": (x1, y1, x2, y2),
                })
            except Exception:
                continue
    return out
