"""
Tests for the phone detector wrapper.

No model weights are needed: what matters here is the crop geometry, the adaptive
inference size, and the graceful-degradation contract. Detector recall itself can only
be measured on real footage — that is what bench_phone_scale and the diagnostic log are
for.
"""

from __future__ import annotations

import numpy as np
import pytest

from backend.services.detectors import phone as pd


@pytest.fixture(autouse=True)
def _clean_model_cache():
    pd.reset_for_tests()
    yield
    pd.reset_for_tests()


# ── crop geometry ─────────────────────────────────────────────────────────────
def test_upper_body_box_covers_head_to_hip_and_pads_sideways():
    shape = (720, 1280)
    box = (500.0, 100.0, 600.0, 460.0)          # 100 x 360 person
    x1, y1, x2, y2 = pd.upper_body_box(box, shape)

    assert y1 == 100, "the band starts at the top of the head (phone at the ear)"
    assert y2 == pytest.approx(100 + 360 * pd.CROP_BOTTOM, abs=1)
    # hands extend past the torso, so the crop is padded sideways
    assert x1 == pytest.approx(500 - 100 * pd.CROP_PAD, abs=1)
    assert x2 == pytest.approx(600 + 100 * pd.CROP_PAD, abs=1)


def test_upper_body_box_is_clipped_to_the_frame():
    shape = (720, 1280)
    x1, y1, x2, y2 = pd.upper_body_box((-40.0, -30.0, 60.0, 300.0), shape)
    assert x1 >= 0 and y1 >= 0
    x1, y1, x2, y2 = pd.upper_body_box((1240.0, 500.0, 1400.0, 900.0), shape)
    assert x2 <= 1280 and y2 <= 720


def test_upper_body_box_rejects_a_crop_too_small_to_be_worth_inference():
    assert pd.upper_body_box((10.0, 10.0, 20.0, 25.0), (720, 1280)) is None


def test_upper_body_band_is_overridable():
    shape = (720, 1280)
    _, y1, _, y2 = pd.upper_body_box((500.0, 100.0, 600.0, 460.0), shape,
                                     top=0.10, bottom=0.50)
    assert y1 == pytest.approx(100 + 36, abs=1)
    assert y2 == pytest.approx(100 + 180, abs=1)


# ── adaptive inference size ───────────────────────────────────────────────────
def _crop(h, w):
    return np.zeros((h, w, 3), dtype=np.uint8)


def test_small_crops_are_magnified_to_the_floor():
    """Camera 50's people are ~61x176 px; the crop must be scaled UP."""
    assert pd._crop_imgsz([_crop(123, 85)], pd.CROP_SIZE) == pd.CROP_SIZE


def test_large_crops_are_never_downscaled():
    """The camera-47 bug: letterboxing a 417 px crop into 320 shrinks the phone, which
    is worse than not cropping at all."""
    imgsz = pd._crop_imgsz([_crop(417, 192)], 320)
    assert imgsz >= 417, "a crop must never be scaled below its own size"
    assert imgsz % 32 == 0, "inference size must sit on the stride-32 grid"


def test_adaptive_size_is_capped():
    assert pd._crop_imgsz([_crop(2000, 2000)], pd.CROP_SIZE) == pd.CROP_MAX


def test_batch_size_follows_the_largest_crop():
    imgsz = pd._crop_imgsz([_crop(80, 60), _crop(500, 200), _crop(120, 90)], 448)
    assert imgsz >= 500


# ── graceful degradation ──────────────────────────────────────────────────────
def test_missing_weights_disable_detection_without_crashing(monkeypatch, capsys):
    """Same contract as detectors/smoking.py and detectors/reid.py: a missing model
    must never take the perception child down."""
    monkeypatch.setenv("PHONE_WEIGHTS", "/definitely/not/a/model.pt")
    pd.reset_for_tests()

    assert pd.available() is False
    assert pd.detect_phones(_crop(720, 1280), [], (720, 1280)) == []
    assert "phone detection disabled" in capsys.readouterr().out


def test_missing_weights_are_reported_once(monkeypatch, capsys):
    monkeypatch.setenv("PHONE_WEIGHTS", "/definitely/not/a/model.pt")
    pd.reset_for_tests()
    for _ in range(5):
        pd.detect_phones(_crop(720, 1280), [], (720, 1280))
    assert capsys.readouterr().out.count("phone detection disabled") == 1


def test_crop_mode_with_no_people_detects_nothing(monkeypatch):
    """A phone with nobody near it is not phone USE, and crop mode has nothing to crop.
    Must not silently fall back to a whole-frame pass."""
    calls = []
    monkeypatch.setattr(pd, "_get_model", lambda: calls.append(1) or object())
    assert pd.detect_phones(_crop(720, 1280), [], (720, 1280), mode="crop") == []


# ── class configuration ───────────────────────────────────────────────────────
def test_default_class_is_coco_cell_phone(monkeypatch):
    monkeypatch.delenv("PHONE_CLASSES", raising=False)
    assert pd._classes() == [67]


def test_classes_can_be_set_for_a_dedicated_model(monkeypatch):
    monkeypatch.setenv("PHONE_CLASSES", "0,2")
    assert pd._classes() == [0, 2]
    monkeypatch.setenv("PHONE_CLASSES", "all")
    assert pd._classes() is None


def test_bad_class_config_falls_back_to_the_default(monkeypatch):
    monkeypatch.setenv("PHONE_CLASSES", "not,a,number")
    assert pd._classes() == [67]


# ── overlapping crops ─────────────────────────────────────────────────────────
def test_the_same_phone_seen_in_two_crops_is_deduped():
    """Neighbouring people produce overlapping crops, each reporting the same phone."""
    a = {"box": (100.0, 100.0, 120.0, 140.0), "conf": 0.4, "source": "crop"}
    b = {"box": (101.0, 101.0, 121.0, 141.0), "conf": 0.7, "source": "crop"}
    out = pd._dedupe([a, b])
    assert len(out) == 1
    assert out[0]["conf"] == 0.7, "the more confident detection survives"


def test_two_distinct_phones_are_both_kept():
    a = {"box": (100.0, 100.0, 120.0, 140.0), "conf": 0.6, "source": "crop"}
    b = {"box": (400.0, 300.0, 420.0, 340.0), "conf": 0.5, "source": "crop"}
    assert len(pd._dedupe([a, b])) == 2
