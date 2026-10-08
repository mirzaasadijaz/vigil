"""
Face detector + recognizer backbone: SCRFD + InsightFace (ArcFace), ONNX Runtime.

Replaces the old DeepFace/TensorFlow pipeline so the whole stack is torch/ONNX
(no TF-vs-torch native heap corruption). `FaceAnalysis.get()` does detect + align
+ embed in one call; embeddings are L2-normalized so matching is a cosine dot product.
"""
import numpy as np

MODEL_PACK = "buffalo_l"          # SCRFD det_10g + ArcFace w600k_r50
MATCH_SIM = 0.45                  # cosine similarity threshold for a positive match

_app = None


def _get_app():
    global _app
    if _app is None:
        from insightface.app import FaceAnalysis
        _app = FaceAnalysis(
            name=MODEL_PACK,
            providers=["CUDAExecutionProvider", "CPUExecutionProvider"],
        )
        _app.prepare(ctx_id=0, det_size=(640, 640))
        print("[FACE] InsightFace ready (SCRFD + ArcFace).", flush=True)
    return _app


def detect_faces(frame) -> list[dict]:
    """Detect faces -> [{box:(x1,y1,x2,y2), embedding:np.ndarray(512), score:float}]."""
    try:
        faces = _get_app().get(frame)
    except Exception as e:
        print(f"[FACE] detect error: {e}", flush=True)
        return []
    out = []
    for f in faces:
        x1, y1, x2, y2 = [float(v) for v in f.bbox]
        out.append({
            "box": (x1, y1, x2, y2),
            "embedding": np.asarray(f.normed_embedding, dtype=np.float32),
            "score": float(getattr(f, "det_score", 0.0)),
        })
    return out


def embed_image(bgr) -> "np.ndarray | None":
    """Embed the most prominent face in an image (for enrollment). None if no face."""
    faces = detect_faces(bgr)
    if not faces:
        return None
    best = max(faces, key=lambda f: f["score"])
    return best["embedding"]


def cosine_match(query: "np.ndarray", cache: dict) -> tuple[str | None, float]:
    """cache: {key -> normed embedding}. Returns (best_key, similarity) or (None, sim)
    if below MATCH_SIM. Embeddings are pre-normalized so similarity = dot product."""
    if not cache or query is None:
        return None, 0.0
    keys = list(cache.keys())
    mat = np.stack([cache[k] for k in keys])        # (N, 512), already L2-normed
    sims = mat @ query                              # cosine similarity
    idx = int(np.argmax(sims))
    sim = float(sims[idx])
    if sim >= MATCH_SIM:
        return keys[idx], sim
    return None, sim
