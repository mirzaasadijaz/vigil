import {
  PackageX,
  ScanFace, ArrowLeftRight, Users, Timer, ShieldAlert, Smartphone, Flame, Wind,
  Fence, UsersRound, Car, CameraOff, Truck, UtensilsCrossed,
  type LucideIcon,
} from "lucide-react";

export type Geometry = "line" | "zones" | null;
export type ModuleConfigField = "role" | "line" | "zones" | "capacity" | "dwell_seconds" | "schedule" | "idle_mode" | "confidence_threshold"
  // Kohat POC
  | "direction" | "cooldown" | "cooldown_scope" | "segment_margin" | "min_people" | "radius" | "sustain_seconds"
  | "move_tolerance" | "vehicle_types" | "grace_seconds" | "radius_mode" | "radius_persons"
  | "blur_threshold" | "scene_threshold" | "cover_threshold"
  | "blur_drop_ratio" | "cover_drop_ratio" | "cover_area_ratio"
  | "invert_direction" | "swap_labels" | "snapshot" | "abandon_seconds" | "settle_seconds" | "min_area" | "lane" | "person_conf" | "person_iou" | "min_px" | "body_drift_min" | "body_drift_obs" | "max_size_ratio" | "max_aspect" | "max_inv_aspect" | "confirm_hits";

export interface ModuleDef {
  id: string;
  name: string;
  phase: 1 | 2 | 3;
  // null = needs NO perception backbone. `tamper` is the only one: classical CV
  // in the main process, so it still reports when the GPU child is down.
  backbone: "face" | "person" | "phone" | "fire_smoke" | "smoking" | "vehicle" | null;
  geometry: Geometry;
  config: ModuleConfigField[];
  eventTypes: string[];
  icon: LucideIcon;
  tone: "blue" | "success" | "info" | "warning" | "danger";
  description: string;
}

// Mirror of backend/services/modules/registry.py (keep in sync).
export const MODULES: ModuleDef[] = [
  { id: "attendance", name: "Attendance", phase: 1, backbone: "face", geometry: "line",
    config: ["line", "role", "confidence_threshold"], eventTypes: ["recognized", "unknown", "checkin", "checkout"],
    icon: ScanFace, tone: "blue", description: "Face recognition check-in / check-out with optional ROI line." },
  { id: "mess", name: "Mess Management", phase: 1, backbone: "face", geometry: null,
    config: ["confidence_threshold"], eventTypes: ["person_in", "bill"],
    icon: UtensilsCrossed, tone: "info", description: "Canteen billing: auto-opens a mess bill when a recognized employee appears at the camera." },
  { id: "headcount", name: "Head Counting / Entry-Exit", phase: 1, backbone: "person", geometry: "line",
    config: ["lane", "person_conf", "person_iou", "line", "invert_direction", "swap_labels", "snapshot"], eventTypes: ["entry", "exit"],
    icon: ArrowLeftRight, tone: "success", description: "Directional people counting across a line." },
  { id: "truck_count", name: "Truck Count", phase: 1, backbone: "vehicle", geometry: "line",
    config: ["line", "confidence_threshold"], eventTypes: ["entry", "exit"],
    icon: Truck, tone: "success", description: "Directional truck counting across a gate line (IN / OUT)." },
  { id: "occupancy", name: "Occupancy", phase: 1, backbone: "person", geometry: "zones",
    config: ["zones", "capacity"], eventTypes: ["occupancy", "over_capacity"],
    icon: Users, tone: "info", description: "Live count of people inside a zone vs capacity." },
  { id: "idle", name: "Idle Time Monitoring", phase: 1, backbone: "person", geometry: "zones",
    config: ["zones", "dwell_seconds", "idle_mode", "schedule"], eventTypes: ["idle"],
    icon: Timer, tone: "warning", description: "Flags an unmanned station, a motionless person, or loitering." },
  { id: "intrusion", name: "Intrusion Detection", phase: 2, backbone: "person", geometry: "zones",
    config: ["zones", "schedule"], eventTypes: ["intrusion"],
    icon: ShieldAlert, tone: "danger", description: "Alerts on entry into a restricted zone." },
  { id: "smoking", name: "Smoking Detection", phase: 2, backbone: "smoking", geometry: null,
    config: ["confidence_threshold"], eventTypes: ["smoking"],
    icon: Wind, tone: "danger", description: "Detects smoking; raise the confidence threshold to cut false positives." },
  { id: "mobile", name: "Mobile Usage Monitoring", phase: 2, backbone: "phone", geometry: null,
    config: ["lane", "min_px", "body_drift_min", "body_drift_obs", "max_size_ratio", "max_aspect", "max_inv_aspect", "confirm_hits", "cooldown"],
    eventTypes: ["phone_use"],
    icon: Smartphone, tone: "warning", description: "Detects phone usage in monitored areas." },
  { id: "fire_smoke", name: "Fire & Smoke Detection", phase: 3, backbone: "fire_smoke", geometry: null,
    config: [], eventTypes: ["fire", "smoke"],
    icon: Flame, tone: "danger", description: "Dedicated fire and smoke detection." },
  // ── Kohat POC ──────────────────────────────────────────────────────────────
  { id: "tripwire", name: "Virtual Trip Wire", phase: 3, backbone: "person", geometry: "line",
    config: ["lane", "line", "invert_direction", "direction", "cooldown", "cooldown_scope", "segment_margin"], eventTypes: ["crossing"],
    icon: Fence, tone: "danger", description: "Alerts whenever anyone crosses a drawn line. Inherits the hardened head-count crossing logic, so it does not fire on jitter or on people passing the end of the line." },
  { id: "crowd", name: "Unusual Gathering", phase: 3, backbone: "person", geometry: "zones",
    config: ["lane", "person_conf", "person_iou", "zones", "min_people", "radius_mode", "radius_persons", "radius", "sustain_seconds", "grace_seconds", "cooldown"], eventTypes: ["gathering", "dispersed"],
    icon: UsersRound, tone: "warning", description: "Detects people clustering together, not merely present. Clusters by mutual distance and requires the group to hold before alerting, so passers-by do not trigger it." },
  { id: "vehicle_count", name: "Vehicle Counting (by type)", phase: 3, backbone: "vehicle", geometry: "line",
    config: ["line", "vehicle_types", "invert_direction", "swap_labels", "snapshot"], eventTypes: ["vehicle_entry", "vehicle_exit"],
    icon: Car, tone: "info", description: "Counts vehicles crossing a line, with a separate IN/OUT tally per type (car, motorcycle, bus, truck). NOTE: COCO has no rickshaw class - a rickshaw is counted as a vehicle but not as a rickshaw." },
  { id: "no_stopping", name: "No-Stopping Zone (Vehicles)", phase: 3, backbone: "vehicle", geometry: "zones",
    config: ["zones", "dwell_seconds", "move_tolerance", "vehicle_types", "schedule", "cooldown"], eventTypes: ["no_stopping"],
    icon: Car, tone: "warning", description: "Flags a vehicle that stops inside a prohibited zone. Uses the shared YOLO weights (no extra model); the tuning knob that matters is move_tolerance, not confidence." },
  { id: "unattended", name: "Object Dropped / Left Behind", phase: 3, backbone: null, geometry: "zones",
    config: ["zones", "abandon_seconds", "settle_seconds", "min_area", "cooldown"], eventTypes: ["unattended_object"],
    icon: PackageX, tone: "danger", description: "Alerts when something is put down inside a zone and left there. Object-agnostic - a bag, a box, a carton, a sack. Permanent furniture can never trigger it, because furniture is the learned background." },
  { id: "tamper", name: "Camera Tampering", phase: 3, backbone: null, geometry: null,
    config: ["blur_drop_ratio", "cover_drop_ratio", "cover_area_ratio", "scene_threshold", "sustain_seconds", "cooldown"],
    eventTypes: ["tamper_blur", "tamper_moved", "tamper_covered"],
    icon: CameraOff, tone: "danger", description: "Detects a defocused, covered or repositioned camera. Needs no AI model and runs in the main process, so it still reports when the perception process is down." },
];

export const MODULE_BY_ID: Record<string, ModuleDef> = Object.fromEntries(MODULES.map((m) => [m.id, m]));

export const moduleName = (id: string) => MODULE_BY_ID[id]?.name ?? id;

/**
 * How to render each per-camera parameter.
 *
 * The editor used to hand-code a block per field, which meant a parameter added to the
 * registry simply did not appear — reachable only by environment variable, and then only
 * with a restart. Anything with a spec here is rendered automatically.
 *
 * `placeholder` shows the default that applies when the box is left empty, so an operator
 * can see what they are overriding rather than guessing.
 */
export interface FieldSpec {
  label: string;
  kind: "number" | "select" | "text";
  min?: number;
  max?: number;
  step?: number;
  placeholder?: string;
  hint?: string;
  options?: { value: string; label: string }[];
}

// Fields with bespoke editors elsewhere in the component (special labels or widgets).
export const HANDCODED_FIELDS = new Set([
  "role", "capacity", "idle_mode", "confidence_threshold", "dwell_seconds",
  "schedule", "line", "zones",
]);

export const FIELD_SPECS: Partial<Record<ModuleConfigField, FieldSpec>> = {
  direction: {
    label: "Direction", kind: "select",
    hint: "Which way across the line raises an alert.",
    options: [
      { value: "any", label: "Any direction" },
      { value: "in", label: "Inbound only" },
      { value: "out", label: "Outbound only" },
    ],
  },
  cooldown: {
    label: "Cooldown (seconds)", kind: "number", min: 0, placeholder: "5",
    hint: "Minimum gap before the same subject can alert again.",
  },
  cooldown_scope: {
    label: "Cooldown applies to", kind: "select",
    hint: "Per person is safer: 'whole wire' collapses a group into one alert.",
    options: [
      { value: "track", label: "Each person separately" },
      { value: "wire", label: "The whole wire" },
    ],
  },
  segment_margin: {
    label: "Corner tolerance", kind: "number", min: 0, max: 1, step: 0.05,
    placeholder: "0.20",
    hint: "How far past each END of the line still counts, as a fraction of its length.",
  },
  lane: {
    label: "How often this runs", kind: "select",
    hint: "Sampling rate for THIS camera. Faster catches more but costs GPU on every camera you raise. Gathering needs to see two people in the SAME frame, so it suffers most at the slow rate; a measured capture saw one person in 194 of 218 slow-lane evaluations while every pair that WAS seen clustered.",
    options: [
      { value: "", label: "Module default" },
      { value: "slow", label: "Slow — ~2 fps (cheapest)" },
      { value: "medium", label: "Medium — ~5 fps" },
      { value: "fast", label: "Fast — ~10 fps (needed for line crossings)" },
    ],
  },
  person_iou: {
    label: "Keep overlapping people (NMS IoU)", kind: "number", min: 0.1, max: 0.99, step: 0.05,
    placeholder: "0.70",
    hint: "THE knob for gatherings. Two people standing shoulder to shoulder have heavily overlapping boxes, and non-maximum suppression discards the one behind - so the pair is never seen in the same frame and no gathering can accumulate. Raise toward 0.85 to keep them. Too high and one person yields two boxes, which over-counts a trip wire on the same camera. Camera-wide.",
  },
  person_conf: {
    label: "Person detection confidence", kind: "number", min: 0.05, max: 0.95, step: 0.05,
    placeholder: "0.30",
    hint: "A partly-occluded person scores lower than one in the open, so a high floor drops the person behind. Lower for better recall in crowds; the crossing machine's track-age and movement checks already reject single-frame noise. Camera-wide.",
  },
  min_px: {
    label: "Smallest plausible phone (px)", kind: "number", min: 0, step: 1,
    placeholder: "8",
    hint: "Shortest side of the phone box, in detection-frame pixels. Below this it is noise, a finger or a cigarette. 8 is measured from a real capture and drops only physically-impossible 4-7px detections. Raise it where people appear LARGE in frame (a camera whose real phones are 60px+ can safely use 20); keep it low on wide views where a genuine phone is only ~11px. 0 = off.",
  },
  body_drift_min: {
    label: "Reject if pinned to the body (drift)", kind: "number", min: 0, max: 1, step: 0.005,
    placeholder: "0 (off)",
    hint: "A phone PRINTED on a t-shirt passes every other check - it is phone-shaped, phone-sized and sits in the hand band. What gives it away is that it never moves INSIDE the person's box, while a held phone wanders. Reject below this drift (fraction of the person's box). 0 = off. Read body_drift off logs/phone_debug.log and set it between your print and your real phones. Costs about a second of extra latency when on.",
  },
  body_drift_obs: {
    label: "Observations before judging drift", kind: "number", min: 2, step: 1,
    placeholder: "6",
    hint: "How many detections to gather before deciding whether it moved. Higher is more reliable and slower to alert.",
  },
  max_size_ratio: {
    label: "Largest plausible phone", kind: "number", min: 0, step: 0.01,
    placeholder: "0.06",
    hint: "Above this it is a foreground object, not a phone in someone's hand.",
  },
  max_aspect: {
    label: "Max width / height", kind: "number", min: 0, step: 0.1,
    placeholder: "2.2",
    hint: "Rejects wide flat bars - spectacles, watch straps, a keyboard edge-on.",
  },
  max_inv_aspect: {
    label: "Max height / width", kind: "number", min: 0, step: 0.1,
    placeholder: "3.2",
    hint: "Rejects long thin uprights - a finger, a pen, a cigarette.",
  },
  confirm_hits: {
    label: "Detections needed to confirm", kind: "number", min: 1, step: 1,
    placeholder: "2",
    hint: "How many evaluations in the sliding window must see the phone. Higher = fewer false positives, slower alert.",
  },
  invert_direction: {
    label: "Swap IN and OUT", kind: "select",
    hint: "Which side of the line counts as IN depends on the order the two points were drawn - which you cannot see. Watch one person walk in: if the wrong counter goes up, turn this on. Also flips the trip wire's direction filter, which uses the same convention.",
    options: [
      { value: "false", label: "Normal" },
      { value: "true", label: "Swapped - count the other way" },
    ],
  },
  swap_labels: {
    label: "Swap the words IN / OUT on the live view", kind: "select",
    hint: "Display only - it relabels the counter drawn on the video and changes nothing else. Use this ONLY if you are relabelling rather than recounting. If you have turned on 'Swap IN and OUT' above, leave this off: that already moves the counters, the overlay and the analytics together, and enabling both cancels them out.",
    options: [
      { value: "false", label: "Normal" },
      { value: "true", label: "Swapped - display only" },
    ],
  },
  snapshot: {
    label: "Save a picture of every entry and exit", kind: "select",
    hint: "A crossing log without pictures cannot answer 'who was that'. The cost is one full-resolution image per crossing, both directions - a door doing 1000 crossings a day writes a few hundred MB a day. Turn it off on a busy door where the count matters more than the evidence.",
    options: [
      { value: "true", label: "Yes - picture on every crossing" },
      { value: "false", label: "No - count only" },
    ],
  },
  abandon_seconds: {
    label: "Seconds before it counts as abandoned", kind: "number", min: 5, step: 5,
    placeholder: "30",
    hint: "How long the object must sit in the same place before alerting. This is the main knob: 30s for a corridor or lobby, longer where people legitimately set things down for a while.",
  },
  settle_seconds: {
    label: "Seconds to settle first", kind: "number", min: 0, step: 1,
    placeholder: "2",
    hint: "Ignore a region until it has been still this long. Stops a person mid-stride from starting a timer.",
  },
  min_area: {
    label: "Smallest object (fraction of frame)", kind: "number", min: 0, max: 0.5, step: 0.001,
    placeholder: "0.002",
    hint: "Regions smaller than this are ignored. Raise it if small shadows or debris are being reported; lower it if a genuine bag is too far from the camera to register.",
  },
  min_people: {
    label: "People to count as a gathering", kind: "number", min: 1, step: 1,
    placeholder: "4",
    hint: "1 is a TEST setting - it alerts on a single person, which is how you prove the camera and zone are working. Use 2+ in production; small values on a busy corridor will be noisy.",
  },
  radius_mode: {
    label: "Measure distance in", kind: "select",
    hint: "Body-heights adapts automatically to how far people are from the camera. Use frame width only if box heights are unreliable.",
    options: [
      { value: "person", label: "Body-heights (recommended)" },
      { value: "frame", label: "Fraction of frame width" },
    ],
  },
  radius_persons: {
    label: "Grouping distance (body-heights)", kind: "number", min: 0.2, max: 5, step: 0.1,
    placeholder: "1.2",
    hint: "1.2 is about 2 m for an adult. Lower means people must stand closer to count as a group.",
  },
  radius: {
    label: "Grouping distance", kind: "number", min: 0.01, max: 1, step: 0.01,
    placeholder: "0.12",
    hint: "How close people must be to count as together, as a fraction of frame width. A wide view needs a SMALLER value.",
  },
  sustain_seconds: {
    label: "Must hold for (seconds)", kind: "number", min: 0, step: 1,
    placeholder: "8",
    hint: "Stops people merely passing each other from alerting.",
  },
  grace_seconds: {
    label: "Tolerate gaps of (seconds)", kind: "number", min: 0, step: 1,
    placeholder: "3",
    hint: "How long the group may vanish from the detector without restarting the timer. Guards against dropped frames.",
  },
  move_tolerance: {
    label: "Movement tolerance", kind: "number", min: 0, max: 1, step: 0.005,
    placeholder: "0.02",
    hint: "Displacement treated as detector jitter. Too tight and a parked vehicle never alerts.",
  },
  vehicle_types: {
    label: "Vehicle types", kind: "text", placeholder: "car, truck, bus, motorcycle",
    hint: "Comma separated. Empty means all.",
  },
  blur_drop_ratio: {
    label: "Defocus sensitivity", kind: "number", min: 0.05, max: 1, step: 0.05,
    placeholder: "0.35",
    hint: "Alert when sharpness falls below this fraction of the camera's own normal.",
  },
  cover_drop_ratio: {
    label: "Cover sensitivity", kind: "number", min: 0.05, max: 1, step: 0.05,
    placeholder: "0.60",
    hint: "Alert when detail falls below this fraction of normal.",
  },
  cover_area_ratio: {
    label: "Fraction of view blocked", kind: "number", min: 0.05, max: 1, step: 0.05,
    placeholder: "0.30",
    hint: "How much of the frame must go blank to count as an obstruction.",
  },
  scene_threshold: {
    label: "Scene-change sensitivity", kind: "number", min: 0.05, max: 3, step: 0.05,
    placeholder: "0.45",
    hint: "Lower fires more readily on the camera being moved — and on people passing close.",
  },
};
