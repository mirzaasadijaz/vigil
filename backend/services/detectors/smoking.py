"""
Smoking detector backbone: a dedicated YOLO model for cigarette / smoking detection.

Runs inside the perception child process (PyTorch / GPU). Weights are NOT bundled —
drop a trained model at <repo>/smoking.pt (git-ignored, like the other *.pt weights)
or point SMOKING_WEIGHTS at one. If no weights are present the detector logs once and
returns nothing, so the module degrades gracefully instead of crashing the pipeline.

The `conf` threshold is the user-tunable knob (per-camera `confidence_threshold`) that
suppresses the low-confidence false positives reported against ad-hoc smoking setups.
"""

import os

_model = None
_loaded = False  # have we attempted to load yet (load-once, even on failure)?


def _weights_path() -> str:
    env = os.environ.get("SMOKING_WEIGHTS")
    if env:
        return env
    from backend.core.config import settings

    return os.path.join(settings.BASE_DIR, "models/best.pt")


def _get_model():
    global _model, _loaded
    if _loaded:
        return _model
    _loaded = True
    path = _weights_path()
    if not os.path.exists(path):
        print(
            f"[SMOKING] no weights at {path} — smoking detection disabled "
            f"(drop a trained smoking.pt or set SMOKING_WEIGHTS).",
            flush=True,
        )
        return None
    try:
        from ultralytics import YOLO

        _model = YOLO(path)
        print(f"[SMOKING] loaded weights: {path}", flush=True)
    except Exception as e:
        print(f"[SMOKING] failed to load {path}: {e}", flush=True)
        _model = None
    return _model


def detect_smoking(frame, conf: float = 0.75) -> list[dict]:
    """Return smoking detections: [{box:(x1,y1,x2,y2), confidence}]. Empty if no
    weights are installed. `conf` filters low-confidence boxes."""
    model = _get_model()
    if model is None:
        return []
    try:
        res = model.predict(frame, conf=conf, verbose=False)
    except Exception as e:
        print(f"[SMOKING] inference error: {e}", flush=True)
        return []
    if not res:
        return []
    r = res[0]
    boxes = getattr(r, "boxes", None)
    if boxes is None:
        return []
    xyxy = boxes.xyxy.cpu().numpy()
    confs = boxes.conf.cpu().numpy() if boxes.conf is not None else [0.0] * len(xyxy)
    out: list[dict] = []
    for box, c in zip(xyxy, confs):
        x1, y1, x2, y2 = [float(v) for v in box]
        out.append({"box": (x1, y1, x2, y2), "confidence": float(c)})
    return out
