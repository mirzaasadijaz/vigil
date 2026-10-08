"""
Module registry — single source of truth for the platform's AI analytics.

Each module declares the detector *backbone* it consumes (so the perception worker
loads only what's needed), the per-camera config it expects (geometry + params),
and the event types it emits. Adding a new analytic = one entry here + one rule
consumer in backend/services/analytics/. The frontend mirrors this in
frontend/src/lib/modules.ts.

Backbones (shared perception): one detector can feed many modules.
  - person   : YOLOv11n + ByteTrack  -> headcount, occupancy, idle, intrusion
  - vehicle  : YOLOv11s (truck class) -> truck_count
  - face     : SCRFD + InsightFace    -> attendance
  - smoking  : dedicated YOLO model    -> smoking       (Phase 2)
  - phone    : YOLOv11s (custom)      -> mobile        (Phase 2)
  - fire_smoke: dedicated model        -> fire_smoke    (Phase 3)
  - vehicle  : shared yolo11s, COCO 2/3/5/7 -> no_stopping (Kohat POC)
  - none     : no detector at all      -> tamper        (Kohat POC)

`backbone: None` means the module needs NO perception backbone. `tamper` is the only
one: it runs classical CV in the MAIN process (camera_manager), because a dead
perception child looks exactly like a blinded camera and must still be reported.
"""

# geometry: None | "line" | "zones"   (what the camera config editor must capture)
MODULES: dict[str, dict] = {
    "attendance": {
        "id": "attendance", "name": "Attendance", "phase": 1,
        "backbone": "face", "geometry": "line",
        "config": ["line", "role", "confidence_threshold"],   # line = ROI gate; role: in|out|both; conf = min face score for Unknown alerts
        "event_types": ["recognized", "unknown", "checkin", "checkout"],
        "icon": "ScanFace", "color": "accent",
        "description": "Face recognition check-in / check-out with optional ROI line.",
    },
    "mess": {
        "id": "mess", "name": "Mess Management", "phase": 1,
        "backbone": "face", "geometry": None,
        "config": ["confidence_threshold"],
        "event_types": ["person_in", "bill"],
        "icon": "Utensils", "color": "accent",
        "description": "Face-recognition billing: a recognized employee auto-opens a mess bill at the counter, "
                       "items are added against their monthly budget, and a receipt prints on close.",
    },
    "headcount": {
        "id": "headcount", "name": "Head Counting / Entry-Exit", "phase": 1,
        "lane": "fast",
        "backbone": "person", "geometry": "line",
        "config": ["lane", "person_conf", "person_iou", "line", "invert_direction", "swap_labels", "snapshot"],                       # directional counting line
        "event_types": ["entry", "exit"],
        "icon": "ArrowLeftRight", "color": "online",
        "description": "Directional people counting across a line.",
    },
    "truck_count": {
        "id": "truck_count", "name": "Truck Count", "phase": 1,
        "backbone": "vehicle", "geometry": "line",
        "config": ["line", "confidence_threshold"],   # line = gate ROI; conf = min truck score (cut rickshaw false-positives)
        "event_types": ["entry", "exit"],
        "icon": "Truck", "color": "online",
        "description": "Directional truck counting across a gate line (IN / OUT).",
    },
    "occupancy": {
        "id": "occupancy", "name": "Occupancy", "phase": 1,
        "backbone": "person", "geometry": "zones",
        "config": ["zones", "capacity"],
        "event_types": ["occupancy", "over_capacity"],
        "icon": "Users", "color": "accent-soft",
        "description": "Live count of people inside a zone vs capacity.",
    },
    "idle": {
        "id": "idle", "name": "Idle Time Monitoring", "phase": 1,
        "backbone": "person", "geometry": "zones",
        "config": ["zones", "dwell_seconds", "idle_mode"],
        "event_types": ["idle"],
        "icon": "Timer", "color": "idle",
        "description": "Flags an unmanned work station (default) or a person motionless in a zone.",
    },
    "intrusion": {
        "id": "intrusion", "name": "Intrusion Detection", "phase": 2,
        "backbone": "person", "geometry": "zones",
        "config": ["zones", "schedule"],
        "event_types": ["intrusion"],
        "icon": "ShieldAlert", "color": "dnd",
        "description": "Alerts on any person entering a restricted zone (optionally by schedule).",
    },
    "smoking": {
        "id": "smoking", "name": "Smoking Detection", "phase": 2,
        "backbone": "smoking", "geometry": None,
        "config": ["confidence_threshold"],       # tunable knob to cut false positives
        "event_types": ["smoking"],
        "icon": "Wind", "color": "dnd",
        "description": "Detects smoking; raise the confidence threshold to cut false positives.",
    },
    "mobile": {
        "id": "mobile", "name": "Mobile Usage Monitoring", "phase": 2,
        "lane": "medium",
        "backbone": "phone", "geometry": None,
        # The plausibility gates, tunable per camera rather than only by env var. These
        # are the knobs that decide whether a finger or a cigarette is reported as a
        # phone, and the right value depends on how large people appear in THIS camera -
        # which is exactly what a per-camera setting is for.
        "config": ["lane", "min_px", "body_drift_min", "body_drift_obs",
                   "max_size_ratio", "max_aspect", "max_inv_aspect",
                   "confirm_hits", "cooldown"],
        "event_types": ["phone_use"],
        "icon": "Smartphone", "color": "idle",
        "description": "Detects phone usage in monitored areas.",
    },
    "fire_smoke": {
        "id": "fire_smoke", "name": "Fire & Smoke Detection", "phase": 3,
        "backbone": "fire_smoke", "geometry": None,
        "config": [],
        "event_types": ["fire", "smoke"],
        "icon": "Flame", "color": "dnd",
        "description": "Dedicated fire and smoke detection.",
    },
    # ── Kohat POC ────────────────────────────────────────────────────────────
    "tripwire": {
        "id": "tripwire", "name": "Virtual Trip Wire", "phase": 3,
        "lane": "fast",
        "backbone": "person", "geometry": "line",
        "config": ["lane", "line", "invert_direction", "direction", "cooldown", "cooldown_scope",
                   "segment_margin"],
        "event_types": ["crossing"],
        "icon": "Fence", "color": "dnd",
        "description": "Alerts whenever anyone crosses a drawn line. Inherits the "
                       "hardened head-count crossing logic, so it does not fire on "
                       "jitter or on people passing the end of the line.",
    },
    "crowd": {
        "id": "crowd", "name": "Unusual Gathering", "phase": 3,
        "lane": "medium",
        "backbone": "person", "geometry": "zones",
        "config": ["lane", "person_conf", "person_iou", "zones", "min_people", "radius_mode", "radius_persons",
                   "radius", "sustain_seconds", "grace_seconds", "cooldown"],
        "event_types": ["gathering", "dispersed"],
        "icon": "UsersRound", "color": "idle",
        "description": "Detects people clustering together, not merely present. "
                       "Clusters by mutual distance and requires the group to hold "
                       "before alerting, so passers-by do not trigger it.",
    },
    "vehicle_count": {
        "id": "vehicle_count", "name": "Vehicle Counting (by type)", "phase": 3,
        "lane": "fast",
        # Same lane as head-count and the trip wire, and for the same reason: a crossing
        # needs the track to survive frame to frame, and ByteTrack association fails when
        # per-frame displacement exceeds a box width. A vehicle moves FASTER than a
        # person, so if anything it needs this more.
        "backbone": "vehicle", "geometry": "line",
        "config": ["line", "vehicle_types", "invert_direction", "swap_labels", "snapshot"],
        "event_types": ["vehicle_entry", "vehicle_exit"],
        "icon": "Car", "color": "accent",
        "description": "Counts vehicles crossing a line, with a separate IN/OUT tally "
                       "per type (car, motorcycle, bus, truck). NOTE: COCO has no "
                       "rickshaw class - a rickshaw is counted as a vehicle but not as "
                       "a rickshaw.",
    },
    "no_stopping": {
        "id": "no_stopping", "name": "No-Stopping Zone (Vehicles)", "phase": 3,
        "backbone": "vehicle", "geometry": "zones",
        "config": ["zones", "dwell_seconds", "move_tolerance", "vehicle_types",
                   "schedule", "cooldown"],
        "event_types": ["no_stopping"],
        "icon": "Car", "color": "idle",
        "description": "Flags a vehicle that stops inside a prohibited zone. Uses the "
                       "shared YOLO weights (no extra model); the tuning knob that "
                       "matters is move_tolerance, not confidence.",
    },
    "unattended": {
        "id": "unattended", "name": "Object Dropped / Left Behind", "phase": 3,
        # No backbone at all, like tamper: the mechanism is background subtraction in
        # the MAIN process, not a detector. A COCO bag class would only supply a LABEL,
        # and would be blind to the boxes, sacks and cartons actually left at a site -
        # and could not tell a dropped object from a permanent one anyway, because a
        # detector has no idea what the scene looked like before.
        "backbone": None, "geometry": "zones",
        "config": ["zones", "abandon_seconds", "settle_seconds", "min_area", "cooldown"],
        "event_types": ["unattended_object"],
        "icon": "PackageX", "color": "dnd",
        "description": "Alerts when something is put down inside a zone and left there. "
                       "Object-agnostic - a bag, a box, a carton, a sack. Permanent "
                       "furniture can never trigger it, because furniture is the "
                       "learned background.",
    },
    "tamper": {
        "id": "tamper", "name": "Camera Tampering", "phase": 3,
        "backbone": None, "geometry": None,
        # The absolute *_threshold floors still exist but default to 0 = disabled; the
        # ratios are what actually decide, so those are what the editor should offer.
        "config": ["blur_drop_ratio", "cover_drop_ratio", "cover_area_ratio",
                   "scene_threshold", "sustain_seconds", "cooldown"],
        "event_types": ["tamper_blur", "tamper_moved", "tamper_covered"],
        "icon": "CameraOff", "color": "dnd",
        "description": "Detects a defocused, covered or repositioned camera. Needs no "
                       "AI model and runs in the main process, so it still reports "
                       "when the perception process is down.",
    },
}


def module_ids() -> list[str]:
    return list(MODULES.keys())


def get_module(module_id: str) -> dict | None:
    return MODULES.get(module_id)


def backbones_for(assigned: list[str]) -> set[str]:
    """Union of detector backbones required by a set of assigned module ids."""
    out: set[str] = set()
    for m in assigned:
        meta = MODULES.get(m)
        # `tamper` declares backbone None - it needs no perception backbone at all, and
        # adding None to this set would make the child try to load a detector called
        # "None".
        if meta and meta.get("backbone"):
            out.add(meta["backbone"])
    return out


# ── Cadence lanes ────────────────────────────────────────────────────────────────
# WHICH LANE a module needs is a property OF THE MODULE, so it is declared above with
# everything else about that module rather than in two hardcoded sets in two files.
#
# It used to live as literal sets in BOTH camera_manager.py (which decides what to
# schedule) and perception.py (which decides what a lane's task may carry), with a
# comment warning that the copies must agree by hand. They now both call these, so the
# question "which lane is this module on" has exactly one answer, and adding a module to
# a lane is a one-word edit to its entry above.
#
#   "fast"    ~10 fps. Crossing detection: ByteTrack association fails at lower rates.
#   "medium"   ~5 fps. Needs frequent sampling but not tracking continuity - small-object
#              temporal evidence (mobile), or seeing several people SIMULTANEOUSLY (crowd).
#   None       the historical slow lane, ~2 fps. Everything else.
#
# Every module promoted out of the slow lane raises per-camera GPU cost, so promote on
# measurement rather than intuition.
def modules_for_lane(lane: str) -> set[str]:
    """Module ids that need `lane`."""
    return {mid for mid, m in MODULES.items() if m.get("lane") == lane}


def lane_modules() -> dict:
    """{lane: modules carried by a task on that lane}.

    A lane carries the modules of every lane at or below it, so a camera running
    head-count AND phone gets both without duplicating the person pass."""
    fast = modules_for_lane("fast")
    return {"fast": fast, "medium": fast | modules_for_lane("medium")}


def lane_for(module_id: str) -> "str | None":
    m = MODULES.get(module_id) or {}
    return m.get("lane")


LANES = ("fast", "medium", "slow")
LANE_ORDER = {"fast": 0, "medium": 1, "slow": 2}


def effective_lane(module_cfg: dict) -> str:
    """The lane THIS camera runs this module on.

    The registry value is a DEFAULT, not a rule. An operator assigning modules to a
    camera can override it per camera, because the right cadence depends on the camera:
    the same `crowd` module on a doorway where people are large and close needs far less
    sampling than one watching a wide yard. Hard-coding one lane for every camera made
    that the developer's decision instead of the operator's.

    `module_cfg` is an entry from `camera_devices.module_config`. An unrecognised or
    missing value falls back to the registry default, then to the slow lane - a typo in
    the config must never promote a camera to 10 fps by accident.
    """
    override = str((module_cfg or {}).get("lane") or "").strip().lower()
    if override in LANES:
        return override
    mid = (module_cfg or {}).get("module")
    return (MODULES.get(mid) or {}).get("lane") or "slow"


def carried_by_lane(module_cfg: dict, lane: str) -> bool:
    """Does a task on `lane` carry this module?

    A lane carries the modules of every lane at or below it, so a camera running
    head-count AND phone gets both without duplicating the person pass."""
    return LANE_ORDER.get(effective_lane(module_cfg), 2) <= LANE_ORDER.get(lane, 2)


def needs_lane(module_cfg: dict, lane: str) -> bool:
    """Does this module specifically REQUIRE `lane` to be scheduled?

    Only the modules of THAT lane count, not the cumulative set - otherwise a phone-only
    camera would be dragged into the head-count fast lane and pay for 10 fps person
    tracking it does not need."""
    return effective_lane(module_cfg) == lane

