"""
Model lifecycle — DeepFace-only build.

PPE/YOLO is archived for now (the dev GPU only has enough VRAM for one model at
a time). This module no longer imports torch/ultralytics so the process never
holds a PyTorch CUDA context alongside TensorFlow — that coexistence was the
source of the native heap corruption (free(): invalid pointer / SIGABRT).

The only model is DeepFace (ArcFace + RetinaFace), which loads lazily inside the
face worker thread. Here we just own the system mode and TF/GPU configuration.
"""

import gc
import threading

SYSTEM_MODE = "face"        # DeepFace-only; PPE archived
MODELS_LOADED = False
model_lock = threading.Lock()

# Legacy YOLO handles. Kept as None so any remaining PPE code paths short-circuit
# cleanly without importing torch/ultralytics.
yolo_ppe = None
yolo_coco = None

_tf_configured = False


def get_system_mode() -> str:
    return SYSTEM_MODE


def configure_tf_gpu():
    """Enable GPU memory growth so TF doesn't grab all VRAM up front.
    Must run before the first GPU op. Safe to call multiple times."""
    global _tf_configured
    if _tf_configured:
        return
    try:
        import tensorflow as tf
        gpus = tf.config.list_physical_devices("GPU")
        for gpu in gpus:
            try:
                tf.config.experimental.set_memory_growth(gpu, True)
            except Exception:
                pass
        if gpus:
            print(f"[SYSTEM] TensorFlow using GPU: {gpus}")
        else:
            print("[SYSTEM] TensorFlow running on CPU (no GPU visible).")
        _tf_configured = True
    except Exception as e:
        print(f"[SYSTEM] TF GPU config error: {e}")


def load_face_models():
    """Initialize face (DeepFace) mode. ArcFace/RetinaFace load lazily on first
    use inside the worker thread; here we just configure TF/GPU."""
    global MODELS_LOADED
    print("[SYSTEM] Initializing Face (DeepFace) mode...")
    configure_tf_gpu()
    MODELS_LOADED = True
    print("[SYSTEM] Face mode ready.")


def load_ppe_models():
    """PPE is archived in this build."""
    print("[SYSTEM] PPE detection is archived in this build — ignoring.")


def clear_gpu_memory(keep_coco: bool = False):
    gc.collect()
    try:
        import tensorflow as tf
        tf.keras.backend.clear_session()
    except Exception:
        pass


def switch_mode(new_mode: str):
    """Only face mode is supported in this build."""
    global SYSTEM_MODE
    if new_mode != "face":
        print(f"[SYSTEM] Mode '{new_mode}' is archived — staying on face mode.")
    SYSTEM_MODE = "face"
