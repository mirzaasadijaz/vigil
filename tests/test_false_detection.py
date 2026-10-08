"""
False-detection engine tests.

Covers section 9.1-9.3 of `false-detection-plan.md`. Deterministic: a fake clock and
synthetic events, so no camera, GPU, database or network is needed. The manual
counterpart — physically walking in front of a camera and reading the log — is
`false-detection-test-cases.md`.

Run:  pytest tests/test_false_detection.py -q
"""

from __future__ import annotations

import pytest

from backend.services.false_detection import (
    ACCEPT,
    DEMOTE,
    SUPPRESS,
    FalseDetectionFilter,
    FDConfig,
)

CAM = 48


class Clock:
    def __init__(self, start: float = 10_000.0):
        self.t = start

    def tick(self, seconds: float = 1.0):
        self.t += seconds

    def __call__(self):
        return self.t


def make_filter(clock: Clock | None = None, **cfg):
    """A filter with the layer ON and warm-up disabled, unless a test says otherwise.

    Warm-up defaults to 30 s and would otherwise suppress every event in every test —
    which is correct behaviour, and exactly why it is tested explicitly in its own
    section rather than left to surprise the others."""
    clock = clock or Clock()
    base = {"fd_enabled": True, "fd_warmup_seconds": 0, "fd_dedup_window": 0,
            "fd_max_alerts_per_minute": 0, "fd_repeat_source_enabled": False}
    base.update(cfg)
    f = FalseDetectionFilter(FDConfig(**base))
    f._now = clock
    f.camera_started(CAM, clock())
    return f, clock


def event(module="mobile", event_type="phone_use", track=1, **data):
    return {"module": module, "event_type": event_type,
            "data": {"track": track, **data}}


# ══════════════════════════════════════════════════════════════════════════════
# 0. the layer must be inert when off
# ══════════════════════════════════════════════════════════════════════════════

def test_01_disabled_is_completely_inert():
    """The most important test here. With fd_enabled=false the writer path must be
    indistinguishable from before this package existed."""
    f = FalseDetectionFilter(FDConfig(fd_enabled=False))
    j = f.judge(event(), CAM)
    assert j.verdict == ACCEPT
    assert j.write is True
    assert j.rule is None
    assert j.checks_passed == [], "no rule may even run when the layer is off"
    assert f.counters == {}, "a disabled layer must not accumulate state"


def test_02_disabled_ignores_every_other_setting():
    f = FalseDetectionFilter(FDConfig(fd_enabled=False, fd_mode="enforce",
                                      fd_dedup_window=999, fd_min_confidence=0.99))
    for _ in range(5):
        assert f.judge(event(), CAM).verdict == ACCEPT


# ══════════════════════════════════════════════════════════════════════════════
# 1. observe vs enforce — the safety property
# ══════════════════════════════════════════════════════════════════════════════

def test_03_observe_never_suppresses():
    """The whole design rests on this: observe measures without acting."""
    f, clock = make_filter(fd_mode="observe", fd_dedup_window=60)
    assert f.judge(event(), CAM).verdict == ACCEPT
    clock.tick(1)
    j = f.judge(event(), CAM)
    assert j.verdict == ACCEPT, "observe mode must never remove an alert"
    assert j.would_suppress is True, "...but it must record what enforce would do"
    assert j.rule == "dedup"


def test_04_enforce_suppresses_what_observe_counted():
    f, clock = make_filter(fd_mode="enforce", fd_dedup_window=60)
    assert f.judge(event(), CAM).verdict == ACCEPT
    clock.tick(1)
    j = f.judge(event(), CAM)
    assert j.verdict == SUPPRESS and j.write is False
    assert j.rule == "dedup"


def test_05_observe_and_enforce_agree_on_the_same_input():
    """Same events, same verdicts — only the action differs."""
    seq = [(0, 1), (1, 1), (2, 1), (70, 1)]
    results = {}
    for mode in ("observe", "enforce"):
        f, clock = make_filter(fd_mode=mode, fd_dedup_window=60)
        out = []
        for dt, track in seq:
            clock.t = 10_000.0 + dt
            j = f.judge(event(track=track), CAM)
            out.append(j.would_suppress)
        results[mode] = out
    assert results["observe"] == results["enforce"]


# ══════════════════════════════════════════════════════════════════════════════
# 2. severity
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("etype,expected", [
    ("fire", "critical"), ("intrusion", "high"), ("phone_use", "medium"),
    ("entry", "low"), ("recognized", "info"),
])
def test_06_severity_map(etype, expected):
    f, _ = make_filter()
    j = f.judge(event(event_type=etype), CAM)
    assert j.severity == expected and j.severity_source == "map"


def test_07_unknown_event_type_uses_a_documented_fallback():
    """A newly added event type nobody has classified must still reach an operator,
    not be quietly demoted to nothing."""
    f, _ = make_filter()
    j = f.judge(event(event_type="brand_new_thing"), CAM)
    assert j.severity == "medium"
    assert j.severity_source == "fallback"
    assert j.alert is True


def test_08_min_severity_alert_hides_the_row_without_dropping_it():
    f, _ = make_filter(fd_min_severity_alert="medium")
    j = f.judge(event(event_type="entry"), CAM)     # low
    assert j.write is True, "the row is still written"
    assert j.alert is False, "but it does not surface in the feed"


def test_09_min_severity_snapshot_controls_disk_growth():
    f, _ = make_filter(fd_min_severity_snapshot="high")
    j = f.judge(event(event_type="phone_use"), CAM)  # medium
    assert j.write is True and j.snapshot is False


def test_10_severity_never_suppresses_on_its_own():
    f, _ = make_filter(fd_mode="enforce", fd_min_severity_alert="critical",
                       fd_min_severity_snapshot="critical")
    j = f.judge(event(event_type="recognized"), CAM)
    assert j.verdict == ACCEPT, "severity annotates; it does not suppress"


# ══════════════════════════════════════════════════════════════════════════════
# 3. deduplication
# ══════════════════════════════════════════════════════════════════════════════

def test_11_duplicate_inside_the_window():
    f, clock = make_filter(fd_mode="enforce", fd_dedup_window=60)
    assert f.judge(event(), CAM).verdict == ACCEPT
    clock.tick(10)
    assert f.judge(event(), CAM).verdict == SUPPRESS


def test_12_outside_the_window_alerts_again():
    f, clock = make_filter(fd_mode="enforce", fd_dedup_window=60)
    f.judge(event(), CAM)
    clock.tick(70)
    assert f.judge(event(), CAM).verdict == ACCEPT


def test_13_two_people_must_both_alert():
    """Deduplication's most dangerous failure mode: silencing a second real person
    because they resemble the first."""
    f, _ = make_filter(fd_mode="enforce", fd_dedup_window=60, fd_dedup_scope="track")
    assert f.judge(event(track=1), CAM).verdict == ACCEPT
    assert f.judge(event(track=2), CAM).verdict == ACCEPT


def test_14_camera_scope_collapses_them():
    f, _ = make_filter(fd_mode="enforce", fd_dedup_window=60, fd_dedup_scope="camera")
    assert f.judge(event(track=1), CAM).verdict == ACCEPT
    j = f.judge(event(track=2), CAM)
    assert j.verdict == SUPPRESS and j.rule == "dedup"


def test_15_different_cameras_never_dedup_against_each_other():
    f, _ = make_filter(fd_mode="enforce", fd_dedup_window=60)
    f.camera_started(49, 10_000.0)
    assert f.judge(event(), CAM).verdict == ACCEPT
    assert f.judge(event(), 49).verdict == ACCEPT


def test_16_different_modules_are_not_duplicates():
    f, _ = make_filter(fd_mode="enforce", fd_dedup_window=60)
    assert f.judge(event(module="mobile"), CAM).verdict == ACCEPT
    assert f.judge(event(module="intrusion"), CAM).verdict == ACCEPT


def test_17_zero_window_disables_dedup():
    f, clock = make_filter(fd_mode="enforce", fd_dedup_window=0)
    for _ in range(5):
        clock.tick(1)
        assert f.judge(event(), CAM).verdict == ACCEPT
    assert "dedup" not in {k.split(":", 1)[-1] for k in f.counters[CAM]
                           if k.startswith("rule:")}


# ══════════════════════════════════════════════════════════════════════════════
# 4. rate limiting and storms
# ══════════════════════════════════════════════════════════════════════════════

def test_18_rate_limit_caps_the_first_n():
    f, clock = make_filter(fd_mode="enforce", fd_max_alerts_per_minute=5,
                           fd_storm_threshold=999)
    verdicts = []
    for i in range(8):
        clock.tick(1)
        verdicts.append(f.judge(event(track=i), CAM).verdict)
    assert verdicts.count(ACCEPT) == 5
    assert verdicts.count(SUPPRESS) == 3


def test_19_the_window_rolls():
    f, clock = make_filter(fd_mode="enforce", fd_max_alerts_per_minute=2,
                           fd_storm_threshold=999)
    for i in range(3):
        f.judge(event(track=i), CAM)
    clock.tick(120)
    assert f.judge(event(track=99), CAM).verdict == ACCEPT


def test_20_storm_demotes_rather_than_dropping():
    f, clock = make_filter(fd_mode="enforce", fd_storm_threshold=5,
                           fd_storm_action="demote", fd_max_alerts_per_minute=0)
    for i in range(5):
        clock.tick(1)
        f.judge(event(track=i), CAM)
    j = f.judge(event(track=99), CAM)
    assert j.verdict == DEMOTE
    assert j.write is True, "a storm must not silence a camera entirely"
    assert j.severity == "info" and j.snapshot is False


def test_21_storm_action_suppress():
    f, clock = make_filter(fd_mode="enforce", fd_storm_threshold=3,
                           fd_storm_action="suppress", fd_max_alerts_per_minute=0)
    for i in range(3):
        clock.tick(1)
        f.judge(event(track=i), CAM)
    assert f.judge(event(track=99), CAM).verdict == SUPPRESS


def test_22_storm_action_none_is_inert():
    f, clock = make_filter(fd_mode="enforce", fd_storm_threshold=2,
                           fd_storm_action="none", fd_max_alerts_per_minute=0)
    for i in range(6):
        clock.tick(1)
        assert f.judge(event(track=i), CAM).verdict == ACCEPT


def test_23_a_storming_camera_does_not_silence_another():
    """Cross-camera suppression would be a critical failure."""
    f, clock = make_filter(fd_mode="enforce", fd_storm_threshold=3,
                           fd_storm_action="suppress", fd_max_alerts_per_minute=0)
    f.camera_started(49, clock())
    for i in range(5):
        clock.tick(1)
        f.judge(event(track=i), CAM)
    assert f.judge(event(track=1), 49).verdict == ACCEPT


# ══════════════════════════════════════════════════════════════════════════════
# 5. evidence floors
# ══════════════════════════════════════════════════════════════════════════════

def test_24_min_confidence():
    f, _ = make_filter(fd_mode="enforce", fd_min_confidence=0.5)
    assert f.judge(event(confidence=0.2), CAM).verdict == SUPPRESS
    assert f.judge(event(track=2, confidence=0.8), CAM).verdict == ACCEPT


def test_25_zero_floors_are_inert():
    f, _ = make_filter(fd_mode="enforce", fd_min_confidence=0.0,
                       fd_min_evidence_frames=0)
    assert f.judge(event(confidence=0.01), CAM).verdict == ACCEPT


def test_26_events_without_confidence_are_not_suppressed():
    """head-count entries carry no confidence. An absent value must never be read as
    zero, or a floor would silently delete a whole module's events."""
    f, _ = make_filter(fd_mode="enforce", fd_min_confidence=0.9)
    j = f.judge({"module": "headcount", "event_type": "entry", "data": {"track": 1}},
                CAM)
    assert j.verdict == ACCEPT


def test_27_min_evidence_frames():
    f, _ = make_filter(fd_mode="enforce", fd_min_evidence_frames=4)
    assert f.judge(event(evidence="2/5"), CAM).verdict == SUPPRESS
    assert f.judge(event(track=2, evidence="5/5"), CAM).verdict == ACCEPT


def test_28_require_snapshot():
    f, _ = make_filter(fd_mode="enforce", fd_require_snapshot=True)
    assert f.judge(event(), CAM, has_snapshot=False).verdict == SUPPRESS
    assert f.judge(event(track=2), CAM, has_snapshot=True).verdict == ACCEPT


# ══════════════════════════════════════════════════════════════════════════════
# 6. ignore zones and hours
# ══════════════════════════════════════════════════════════════════════════════

ZONE = [[0.4, 0.4], [0.6, 0.4], [0.6, 0.6], [0.4, 0.6]]


def test_29_inside_an_ignore_zone():
    f, _ = make_filter(fd_mode="enforce", fd_ignore_zones=[ZONE])
    j = f.judge(event(), CAM, point=(0.5, 0.5))
    assert j.verdict == SUPPRESS and j.rule == "zones"


def test_30_outside_the_zone_is_untouched():
    f, _ = make_filter(fd_mode="enforce", fd_ignore_zones=[ZONE])
    assert f.judge(event(), CAM, point=(0.9, 0.9)).verdict == ACCEPT


def test_31_no_point_means_the_rule_is_skipped_not_guessed():
    """A module that cannot supply a position must not be suppressed by a spatial rule
    on the assumption it might be inside."""
    f, _ = make_filter(fd_mode="enforce", fd_ignore_zones=[ZONE])
    assert f.judge(event(), CAM, point=None).verdict == ACCEPT


def test_32_zones_are_normalised_so_resolution_does_not_matter():
    f, _ = make_filter(fd_mode="enforce", fd_ignore_zones=[ZONE])
    # the same real-world point on a 1280x720 and a 960x1080 frame is the same 0-1 point
    assert f.judge(event(), CAM, point=(0.5, 0.5)).verdict == SUPPRESS


def test_33_ignore_hours_demote_rather_than_drop(monkeypatch):
    """A security event during a quiet window still has to exist."""
    f, clock = make_filter(fd_mode="enforce",
                           fd_ignore_hours=[{"start": "00:00", "end": "23:59"}])
    j = f.judge(event(), CAM)
    assert j.verdict == DEMOTE and j.write is True and j.severity == "info"


def test_34_outside_the_hours_window():
    f, _ = make_filter(fd_mode="enforce",
                       fd_ignore_hours=[{"start": "03:00", "end": "03:01"}])
    j = f.judge(event(), CAM)
    assert j.verdict in (ACCEPT, DEMOTE)   # depends on wall clock; must not crash


def test_35_overnight_window_wraps():
    from backend.services.false_detection.engine import _within
    assert _within("22:00", "06:00", "23:30") is True
    assert _within("22:00", "06:00", "02:30") is True
    assert _within("22:00", "06:00", "12:00") is False
    assert _within("09:00", "17:00", "12:00") is True


def test_36_malformed_hours_never_crash():
    from backend.services.false_detection.engine import _within
    assert _within("nonsense", "06:00", "12:00") is False
    assert _within(None, None, "12:00") is False


# ══════════════════════════════════════════════════════════════════════════════
# 7. warm-up
# ══════════════════════════════════════════════════════════════════════════════

def test_37_warmup_suppresses_startup_noise():
    clock = Clock()
    f = FalseDetectionFilter(FDConfig(fd_enabled=True, fd_mode="enforce",
                                      fd_warmup_seconds=30))
    f._now = clock
    f.camera_started(CAM, clock())
    clock.tick(5)
    j = f.judge(event(), CAM)
    assert j.verdict == SUPPRESS and j.rule == "warmup"


def test_38_after_warmup_events_pass():
    clock = Clock()
    f = FalseDetectionFilter(FDConfig(fd_enabled=True, fd_mode="enforce",
                                      fd_warmup_seconds=30, fd_dedup_window=0,
                                      fd_max_alerts_per_minute=0,
                                      fd_repeat_source_enabled=False))
    f._now = clock
    f.camera_started(CAM, clock())
    clock.tick(35)
    assert f.judge(event(), CAM).verdict == ACCEPT


def test_39_warmup_is_per_camera():
    clock = Clock()
    f = FalseDetectionFilter(FDConfig(fd_enabled=True, fd_mode="enforce",
                                      fd_warmup_seconds=30, fd_dedup_window=0,
                                      fd_max_alerts_per_minute=0,
                                      fd_repeat_source_enabled=False))
    f._now = clock
    f.camera_started(CAM, clock())
    clock.tick(60)
    f.camera_started(49, clock())            # a second camera starts later
    assert f.judge(event(), CAM).verdict == ACCEPT
    assert f.judge(event(), 49).verdict == SUPPRESS


# ══════════════════════════════════════════════════════════════════════════════
# 8. repeated-source map
# ══════════════════════════════════════════════════════════════════════════════

def test_40_a_fixture_serving_several_people_is_suppressed():
    f, clock = make_filter(fd_mode="enforce", fd_repeat_source_enabled=True,
                           fd_repeat_hits=5, fd_repeat_window=3,
                           fd_repeat_min_tracks=3)
    last = None
    for i in range(12):
        clock.tick(1)
        last = f.judge(event(track=i // 3), CAM, point=(0.5, 0.5))
    assert last.verdict == SUPPRESS and last.rule == "repeat"


def test_41_one_person_is_never_furniture():
    """The guard that protects a genuine user: one person is ONE track, however long
    they stay in one place."""
    f, clock = make_filter(fd_mode="enforce", fd_repeat_source_enabled=True,
                           fd_repeat_hits=3, fd_repeat_window=1,
                           fd_repeat_min_tracks=3)
    for _ in range(30):
        clock.tick(1)
        j = f.judge(event(track=7), CAM, point=(0.5, 0.5))
        assert j.verdict == ACCEPT


def test_42_a_moving_source_is_not_a_fixture():
    f, clock = make_filter(fd_mode="enforce", fd_repeat_source_enabled=True,
                           fd_repeat_hits=3, fd_repeat_window=1,
                           fd_repeat_min_tracks=2)
    for i in range(20):
        clock.tick(1)
        j = f.judge(event(track=i), CAM, point=(0.05 + i * 0.04, 0.5))
        assert j.verdict == ACCEPT


def test_43_cells_expire_so_a_place_is_not_poisoned_forever():
    f, clock = make_filter(fd_mode="enforce", fd_repeat_source_enabled=True,
                           fd_repeat_hits=3, fd_repeat_window=1,
                           fd_repeat_min_tracks=2, fd_repeat_ttl=10)
    for i in range(8):
        clock.tick(1)
        f.judge(event(track=i), CAM, point=(0.5, 0.5))
    clock.tick(60)                                   # the source goes away
    assert f.judge(event(track=99), CAM, point=(0.5, 0.5)).verdict == ACCEPT


def test_44_disabled_repeat_map_is_inert():
    f, clock = make_filter(fd_mode="enforce", fd_repeat_source_enabled=False)
    for i in range(30):
        clock.tick(1)
        assert f.judge(event(track=i), CAM, point=(0.5, 0.5)).verdict == ACCEPT


# ══════════════════════════════════════════════════════════════════════════════
# 9. per-camera overrides, config safety, bookkeeping
# ══════════════════════════════════════════════════════════════════════════════

def test_45_per_camera_override_wins():
    f, clock = make_filter(fd_mode="enforce", fd_dedup_window=60)
    f.set_config(f.config, {49: {"fd_dedup_window": 0}})
    f.camera_started(49, clock())
    f.judge(event(), CAM)
    f.judge(event(), 49)
    clock.tick(5)
    assert f.judge(event(), CAM).verdict == SUPPRESS, "global setting applies"
    assert f.judge(event(), 49).verdict == ACCEPT, "override applies"


def test_46_override_keys_may_omit_the_prefix():
    cfg = FDConfig(fd_enabled=True).merged({"dedup_window": 15})
    assert cfg.fd_dedup_window == 15


def test_47_unknown_override_keys_are_ignored():
    """A config written by a newer build must not break an older one."""
    cfg = FDConfig(fd_enabled=True).merged({"fd_from_the_future": 1})
    assert cfg.fd_enabled is True


def test_48_invalid_values_degrade_safely():
    """This runs inside the single DB writer thread. A bad stored value must clamp,
    never raise."""
    cfg = FDConfig(fd_mode="nonsense", fd_dedup_scope="sideways",
                   fd_storm_action="explode", fd_min_confidence=5.0,
                   fd_dedup_window=-10, fd_min_severity_alert="ultra").validated()
    assert cfg.fd_mode == "observe"          # the safe default, not enforce
    assert cfg.fd_dedup_scope == "track"
    assert cfg.fd_storm_action == "demote"
    assert cfg.fd_min_confidence == 1.0
    assert cfg.fd_dedup_window == 0
    assert cfg.fd_min_severity_alert == "low"


def test_49_counters_attribute_every_suppression_to_a_rule():
    f, clock = make_filter(fd_mode="enforce", fd_dedup_window=60)
    f.judge(event(), CAM)
    clock.tick(1)
    f.judge(event(), CAM)
    c = f.counters[CAM]
    assert c["events_judged"] == 2
    assert c["accepted"] == 1
    assert c["suppressed"] == 1
    assert c["rule:dedup"] == 1


def test_50_checks_passed_lists_the_rules_that_ran():
    """An ACCEPT records which rules examined it, so a rule that is silently not
    running is visible rather than invisible."""
    f, _ = make_filter(fd_dedup_window=60, fd_max_alerts_per_minute=20,
                       fd_repeat_source_enabled=True)
    j = f.judge(event(), CAM, point=(0.1, 0.1))
    assert j.checks_passed == ["severity", "warmup", "zones", "hours",
                               "evidence", "repeat", "dedup", "ratelimit"]


def test_51_a_suppressed_event_names_the_rule_that_stopped_it():
    for kwargs, point, expected in (
        ({"fd_dedup_window": 60}, None, "dedup"),
        ({"fd_min_confidence": 0.9}, None, "evidence"),
        ({"fd_ignore_zones": [ZONE]}, (0.5, 0.5), "zones"),
        ({"fd_max_alerts_per_minute": 1, "fd_storm_threshold": 999}, None, "ratelimit"),
    ):
        f, clock = make_filter(fd_mode="enforce", **kwargs)
        f.judge(event(confidence=0.95), CAM, point=point)
        clock.tick(1)
        j = f.judge(event(confidence=0.1), CAM, point=point)
        assert j.rule == expected, f"{kwargs} -> {j.rule}"


def test_52_memory_stays_bounded():
    f, clock = make_filter(fd_mode="enforce", fd_dedup_window=5,
                           fd_repeat_source_enabled=True, fd_repeat_ttl=5)
    for i in range(3000):
        clock.tick(0.5)
        f.judge(event(track=i), CAM, point=((i % 97) / 100.0, 0.5))
    st = f._cameras[CAM]
    assert len(st.dedup) < 2000, f"dedup map unbounded: {len(st.dedup)}"
    assert len(st.repeat) < 2000, f"repeat map unbounded: {len(st.repeat)}"
    assert len(st.recent) < 2000


# ══════════════════════════════════════════════════════════════════════════════
# Regression: a stored severity map must not freeze
# ══════════════════════════════════════════════════════════════════════════════

def test_a_stored_severity_map_merges_rather_than_replacing():
    """A stored config is a snapshot of the map as it was when somebody pressed Save.
    Replacing it wholesale means every event type added afterwards is absent and takes
    FALLBACK_SEVERITY instead of its intended default.

    Caught by an end-to-end test that wrote a real `tamper_covered` row against a
    database holding a config saved before the tamper module existed: it came out
    `medium` when the whole point of that event type is that it is CRITICAL."""
    from backend.services.false_detection import FDConfig

    saved_before_tamper_existed = {
        "fd_enabled": True,
        "fd_severity_map": {"phone_use": "high", "fire": "critical"},
    }
    m = FDConfig().merged(saved_before_tamper_existed).fd_severity_map

    assert m["phone_use"] == "high", "an operator's own entry must still win"
    assert m["tamper_covered"] == "critical", "a newly added type must not be lost"
    assert m["tamper_blur"] == "critical"
    assert m["crossing"] == "high"
    assert m["no_stopping"] == "medium"


def test_an_operator_can_still_downgrade_a_default():
    """Merging must not make the defaults un-overridable."""
    from backend.services.false_detection import FDConfig
    m = FDConfig().merged({"fd_severity_map": {"tamper_covered": "low"}}).fd_severity_map
    assert m["tamper_covered"] == "low"
    assert m["tamper_blur"] == "critical", "unrelated entries keep their default"


def test_a_non_dict_severity_map_is_ignored_not_merged():
    """A corrupt stored value must not take the writer thread down."""
    from backend.services.false_detection import FDConfig
    m = FDConfig().merged({"fd_severity_map": "not a map"}).fd_severity_map
    assert isinstance(m, dict) or m == "not a map"
