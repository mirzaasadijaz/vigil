"""
Unusual Gathering / Crowd Alert (Kohat POC #8).

WHY THIS IS NOT `occupancy` WITH A LOWER CAPACITY
-------------------------------------------------
`occupancy` answers "how many people are inside this zone". That is a different question
from "are people gathering", and using one for the other produces both false positives
and false negatives on the same camera:

  * five people spread evenly across a yard is not a gathering, but it is five people in
    the zone — occupancy alerts, and it is wrong
  * five people in one tight huddle at the edge of the zone IS a gathering, and if the
    zone boundary clips two of them occupancy counts three and stays quiet

A gathering is about proximity to EACH OTHER, not membership of a region. So this module
clusters people by mutual distance and ignores the zone except as a mask for where to
look at all.

The second half of the definition is time. People walking past one another are
momentarily close and constantly form transient clusters; a gathering persists. Without
a sustain window this module would fire on every corridor. `sustain_seconds` is
therefore not a noise filter bolted on afterwards — it is part of what "gathering"
means.

CLUSTERING
    Single-linkage over the mutual-distance graph: two people are linked if their foot
    points are within `radius`, and a cluster is a connected component. Chosen over
    k-means (needs k, which is the answer) and over DBSCAN (an extra dependency for
    identical behaviour at this scale — n is a handful of tracks per frame, so an
    O(n^2) union-find is free).

MEASURED IN PEOPLE, NOT IN PIXELS
    A fixed distance cannot work, and the reason is perspective. Two people standing
    shoulder to shoulder at the far end of a corridor are a handful of pixels apart; the
    same two standing together in the foreground are hundreds. Any single frame-fraction
    is therefore too big at one end of the view and too small at the other, and a group
    that spreads out a little slips out of it entirely.

    So the default metric is the APPARENT SIZE OF THE PEOPLE THEMSELVES. Two people are
    linked when the gap between their feet is less than `radius_persons` multiplied by
    their average box height. "Closer together than one and a half body-heights" is true
    at both ends of the corridor and stays true when the camera is re-aimed, which
    removes most of the per-camera tuning that `radius` needed.

    `radius_mode="frame"` restores the old fixed-fraction behaviour for a camera where
    box heights are unreliable (very oblique angles, heavy occlusion).

DISTANCE IS ISOTROPIC
    Both axes are normalised by frame WIDTH. Normalising x by width and y by height - as
    this module originally did - makes a horizontal gap of 0.2 compare equal to a
    vertical gap of 0.2, when on a 16:9 frame the second is physically a little over half
    the first. Distances between people are a physical quantity and must not depend on
    which way round the frame is.

FOOT POINTS, NOT CENTROIDS
    Same reason as Phase 1 head-count: two people at different distances from the camera
    have wildly different box heights, so their centroids are separated vertically even
    when they are standing together. Foot points sit on the ground plane and cluster the
    way a human would judge it.

CONFIG
    zones             optional mask; empty = whole frame
    min_people        cluster size that constitutes a gathering (default 4)
    radius_mode       "person" (default) | "frame"
    radius_persons    linkage distance in body-heights (default 1.5)
    radius            linkage distance as a fraction of frame width, "frame" mode only
    sustain_seconds   how long it must hold before alerting (default 8)
    cooldown          seconds between alerts per camera (default 60)

EVENTS
    gathering   a cluster of >= min_people held for sustain_seconds
    dispersed   a previously-alerted gathering broke up (often the more useful one
                operationally — it tells an operator the situation resolved itself)
"""
import os
import time

from backend.services.analytics import crowd_debug as dbg
from backend.services.analytics.base import Analytic, in_any_zone



# ── Tunable defaults ─────────────────────────────────────────────────────────────
# These have env fallbacks because the camera editor has no renderer for them yet, so
# without this they are reachable by no means at all - not from the UI, not from a
# config file, not from the environment. A parameter nobody can change is a parameter
# that is always wrong somewhere.
#
# Precedence is the same as everywhere else in this codebase: per-camera config wins,
# then the environment, then the constant.
DEF_MIN_PEOPLE = int(os.environ.get("CROWD_MIN_PEOPLE", "4"))
# NORMALISED to frame width, so it survives a resolution change but NOT a change of
# camera angle - a wide overview needs a smaller radius than a close shot. This is the
# knob most likely to need tuning per camera; the TRACE log records the cluster sizes
# you need to pick it.
# 0.12 was a guess and it was wrong. Measured on a real camera, people standing together
# in a group of three to four had a CLOSEST-PAIR distance of 0.133 to 0.243 (median
# 0.180) - so 0.12 linked NONE of them and the module could not form a cluster at all.
# 0.20 links 83% of those observations, 0.25 links all of them.
#
# 0.20 is the default rather than 0.25 because this is still one camera's geometry, and
# erring slightly tight keeps unrelated passers-by from being grouped. Expect to set it
# per camera; `min_pair` in the TRACE log is exactly the measurement needed.
DEF_RADIUS = float(os.environ.get("CROWD_RADIUS", "0.20"))
# Linkage distance expressed in body-heights - see the module docstring. 1.5 means "the
# gap between two people is less than one and a half times their own height", which is
# roughly conversational distance and holds at any depth in the frame.
# 1.2 body-heights. For a 1.7 m person that is about 2 m - the outer edge of what reads
# as a group. People standing in conversation sit at 0.5-2.0 m, i.e. 0.3-1.2 body-heights,
# so this admits a loose huddle while excluding two people who merely share a corridor.
# 1.5 was tried first and is too generous: 2.5 m apart is "same room", not "together".
DEF_RADIUS_PERSONS = float(os.environ.get("CROWD_RADIUS_PERSONS", "1.2"))
DEF_RADIUS_MODE = os.environ.get("CROWD_RADIUS_MODE", "person").strip().lower()
DEF_SUSTAIN = float(os.environ.get("CROWD_SUSTAIN_SECONDS", "8"))
DEF_COOLDOWN = float(os.environ.get("CROWD_COOLDOWN", "60"))
# How long a formed cluster may VANISH without resetting the sustain timer.
#
# This module runs on the slow lane, so it sees a couple of frames a second at best - and
# fewer when the perception queue is saturated. A real capture found 481 of 523
# evaluations detecting nobody at all on a camera where people were demonstrably present.
# Against that, a strict state machine can never accumulate a sustain window: the cluster
# is seen, the next frame is dropped or the detector misses one person, the timer resets,
# and a genuine ten-second gathering is reported as a series of half-second ones.
#
# A gathering does not cease to exist because a frame was lost. Bridging short gaps is
# what makes the sustain window measure the GATHERING rather than the detector's luck.
DEF_GRACE = float(os.environ.get("CROWD_GRACE_SECONDS", "3"))


class Crowd(Analytic):
    module_id = "crowd"

    def __init__(self, cam_id, config):
        super().__init__(cam_id, config)
        self.metrics = dbg.Metrics(cam_id)
        # When the current largest qualifying cluster first appeared. None = no cluster.
        self._cluster_since: float | None = None
        self._alerted = False
        self._last_alert = 0.0
        self._peak = 0
        self._missing_since: float | None = None   # cluster gone, within grace
        # `held` is wall-clock since the cluster formed and INCLUDES grace gaps, which
        # makes a log line like "held=4.58 sustain=2.00 verdict=BELOW_SUSTAIN" read as a
        # contradiction. It is not: the group existed for 0.5 s and the other 4 s were
        # grace. These two track the time the cluster was actually SEEN, so the log can
        # say which is which. Reporting only; the alert gate is unchanged.
        self._observed = 0.0                       # seconds the cluster truly held
        self._last_obs: float | None = None        # previous qualifying observation
        self._observed_n = 0                       # qualifying observations counted
        self._cluster_ids: set = set()             # who was in it when it last qualified

    # ── config ───────────────────────────────────────────────────────────────
    def _num(self, key: str, default: float) -> float:
        try:
            v = self.config.get(key)
            return default if v is None or v == "" else float(v)
        except (TypeError, ValueError):
            return default

    def _int(self, key: str, default: int) -> int:
        return int(self._num(key, default))

    @staticmethod
    def _foot(box) -> tuple[float, float]:
        x1, y1, x2, y2 = box
        return (x1 + x2) / 2.0, y2

    # ── clustering ───────────────────────────────────────────────────────────
    @staticmethod
    def _clusters(points, radius, sizes=None, persons=None) -> list[list[int]]:
        """Connected components of the "within radius" graph, via union-find.

        n is a handful of tracks, so the O(n^2) pass is cheaper than importing a
        spatial index and far cheaper than the inference that produced the tracks."""
        n = len(points)
        parent = list(range(n))

        def find(a):
            while parent[a] != a:
                parent[a] = parent[parent[a]]
                a = parent[a]
            return a

        def union(a, b):
            ra, rb = find(a), find(b)
            if ra != rb:
                parent[rb] = ra

        for i in range(n):
            xi, yi = points[i]
            for j in range(i + 1, n):
                xj, yj = points[j]
                dx, dy = xi - xj, yi - yj
                d2 = dx * dx + dy * dy
                if sizes is not None and persons is not None:
                    # Threshold scales with how big these two people APPEAR, so the same
                    # physical separation links at any depth in the frame.
                    thr = persons * (sizes[i] + sizes[j]) / 2.0
                else:
                    thr = radius
                if d2 <= thr * thr:
                    union(i, j)

        groups: dict[int, list[int]] = {}
        for i in range(n):
            groups.setdefault(find(i), []).append(i)
        return list(groups.values())

    # ── main ─────────────────────────────────────────────────────────────────
    def process(self, tracks, faces, shape):
        h, w = shape
        zones = self.config.get("zones") or []
        # Floor of 1, not 2. A "gathering" of one is not a gathering, but it is the
        # only way to prove the whole chain end to end with one person in front of the
        # camera - and an operator who sets 1 and is silently given 2 has no way to tell
        # the setting was ignored. Both this clamp and the UI's `min: 2` did exactly
        # that, so a reported "I set 1 and nothing happened" was unanswerable.
        min_people = max(1, self._int("min_people", DEF_MIN_PEOPLE))
        radius = self._num("radius", DEF_RADIUS)
        radius_persons = self._num("radius_persons", DEF_RADIUS_PERSONS)
        mode = str(self.config.get("radius_mode") or DEF_RADIUS_MODE).strip().lower()
        if mode not in ("person", "frame"):
            mode = "person"
        sustain = self._num("sustain_seconds", DEF_SUSTAIN)
        cooldown = self._num("cooldown", DEF_COOLDOWN)
        grace = self._num("grace_seconds", DEF_GRACE)
        now = time.time()

        # NO _announced guard here on purpose. DiagnosticLog.session() already
        # fingerprints on the config and re-emits only when it CHANGES, which is what
        # makes a mid-run retune visible. Guarding it with a one-shot flag threw that
        # away: an operator could edit a value in the admin panel and the log would keep
        # reporting the value from start-up, with no way to tell whether the edit had
        # reached the pipeline at all.
        dbg.session(self.cam_id, {
            "min_people": min_people, "radius": radius,
            "radius_mode": mode, "radius_persons": radius_persons,
            "sustain_seconds": sustain, "cooldown": cooldown,
            "grace_seconds": grace, "zones": len(zones),
        })

        self.metrics.bump("frames")

        # Normalised foot points of everyone inside the mask.
        # Both axes normalised by WIDTH so the metric is isotropic - see the docstring.
        # Zone membership still uses the conventional (x/w, y/h) space, because that is
        # how zone polygons are stored.
        pts, ids, sizes = [], [], []
        for t in tracks:
            fx, fy = self._foot(t["box"])
            if zones and not in_any_zone(fx / w, fy / h, zones):
                continue
            pts.append((fx / w, fy / w))
            x1, y1, x2, y2 = t["box"]
            sizes.append(max(1e-6, (y2 - y1) / w))     # apparent height, same units
            ids.append(t["id"])
        self.metrics.bump("people_in_zone", len(pts))

        # Closest pair actually observed. Without this the log says only whether a
        # cluster formed, which tells you that `radius` is wrong but not what to change
        # it to - a real capture showed 175 frames with people present and exactly one
        # cluster, with no way to know whether they were 0.13 apart or 0.40.
        min_pair = None          # closest pair, fraction of frame width
        min_pair_persons = None  # ...and the same gap in body-heights
        for i in range(len(pts)):
            for j in range(i + 1, len(pts)):
                d = ((pts[i][0] - pts[j][0]) ** 2 + (pts[i][1] - pts[j][1]) ** 2) ** 0.5
                if min_pair is None or d < min_pair:
                    min_pair = d
                    avg = (sizes[i] + sizes[j]) / 2.0
                    min_pair_persons = d / avg if avg > 0 else None

        groups = (self._clusters(pts, radius,
                                 sizes if mode == "person" else None,
                                 radius_persons if mode == "person" else None)
                  if pts else [])
        biggest = max(groups, key=len) if groups else []
        size = len(biggest)

        # An empty frame has nothing to say. 78% of a real capture was `people=0` lines,
        # which buries the handful that matter and makes a log nobody wants to read.
        # Still logged while a cluster is pending, because the timer running down against
        # an empty view IS information.
        if dbg.tracing() and (pts or self._cluster_since is not None):
            dbg.trace(event="CLUSTER", camera=self.cam_id, people=len(pts),
                      clusters=len(groups), largest=size, min_people=min_people,
                      radius=radius, mode=mode, min_pair=min_pair,
                      min_pair_persons=min_pair_persons,
                      tracks=tuple(ids[i] for i in biggest) if biggest else "-")

        events = []
        overlays = [{"kind": "zone", "polygon": z, "color": "accent-soft"}
                    for z in zones]

        qualifies = size >= min_people
        if qualifies:
            self._missing_since = None
            self.metrics.bump("clusters_formed")
            self._peak = max(self._peak, size)
            if self._cluster_since is None:
                self._cluster_since = now
                dbg.debug(event="FORMING", camera=self.cam_id, size=size,
                          min_people=min_people, verdict="TIMER_STARTED")
            # Only count the interval if the PREVIOUS observation also qualified; an
            # interval spanning a grace gap is time the cluster did not exist.
            if self._last_obs is not None:
                self._observed += now - self._last_obs
            self._last_obs = now
            self._observed_n += 1
            self._cluster_ids = {ids[i] for i in biggest}
            held = now - self._cluster_since
            if not self._alerted and held >= sustain:
                if cooldown > 0 and (now - self._last_alert) < cooldown:
                    self.metrics.bump("filtered_cooldown")
                    dbg.debug(event="FILTER", camera=self.cam_id, size=size,
                              age=now - self._last_alert, cooldown=cooldown,
                              verdict="COOLDOWN")
                else:
                    self._alerted = True
                    self._last_alert = now
                    self.metrics.bump("gatherings_alerted")
                    dbg.info(event="GATHERING", camera=self.cam_id, size=size,
                             held=held, observed=self._observed,
                             observed_n=self._observed_n,
                             sustain=sustain, people=len(pts),
                             clusters=len(groups), verdict="ALERT")
                    events.append({
                        "event_type": "gathering",
                        "label": f"Unusual Gathering ({size} people)",
                        "value": size,
                        "data": {"size": size, "people_in_zone": len(pts),
                                 "clusters": len(groups),
                                 "held_seconds": round(held, 1),
                                 "tracks": [ids[i] for i in biggest]},
                        "snapshot": True,
                    })
            elif not self._alerted:
                dbg.debug(event="PENDING", camera=self.cam_id, size=size,
                          held=held, observed=self._observed,
                          observed_n=self._observed_n,
                          sustain=sustain, verdict="BELOW_SUSTAIN")
        elif (self._cluster_since is not None
              and grace > 0
              and (now - (self._missing_since or now)) < grace):
            # Cluster not visible THIS frame, but it was moments ago. Hold the timer
            # rather than resetting it - see DEF_GRACE.
            if self._missing_since is None:
                self._missing_since = now
            self._last_obs = None      # do not count the gap as time held together
            # WHICH person went missing. A capture on 2026-08-28 showed track 941 seen
            # continuously while 862 vanished and returned repeatedly - stable ids, so
            # neither tracking churn nor the clustering rule, but the person detector
            # dropping one of a shoulder-to-shoulder pair (NMS; see person_iou). Naming
            # the id here is what turns "the gathering did not fire" into a diagnosis.
            dbg.debug(event="GAP", camera=self.cam_id, people=len(pts),
                      missing_for=now - self._missing_since, grace=grace,
                      was=tuple(sorted(self._cluster_ids)),
                      still_here=tuple(sorted(set(ids) & self._cluster_ids)),
                      lost=tuple(sorted(self._cluster_ids - set(ids))),
                      verdict="TIMER_HELD")
        else:
            self._missing_since = None
            if self._cluster_since is not None and not self._alerted:
                # Formed and broke up before the sustain window — this is the
                # "people walking past each other" case, and counting it separately is
                # how you tell that apart from a radius that is simply too small.
                self.metrics.bump("filtered_below_sustain")
                dbg.debug(event="DISSOLVED", camera=self.cam_id,
                          held=now - self._cluster_since, observed=self._observed,
                          observed_n=self._observed_n,
                          sustain=sustain,
                          verdict="BELOW_SUSTAIN")
            if self._alerted:
                self.metrics.bump("dispersals_alerted")
                dbg.info(event="DISPERSED", camera=self.cam_id, peak=self._peak,
                         verdict="ALERT")
                events.append({
                    "event_type": "dispersed",
                    "label": "Gathering Dispersed",
                    "value": self._peak,
                    "data": {"peak_size": self._peak},
                    # A dispersal shows the area AFTER the group left, which is the
                    # evidence that the situation resolved - and an alert row with no
                    # thumbnail reads as broken next to one that has it.
                    "snapshot": True,
                })
            self._cluster_since = None
            self._observed = 0.0
            self._last_obs = None
            self._observed_n = 0
            self._cluster_ids = set()
            self._alerted = False
            self._peak = 0

        # Draw every qualifying cluster, not just the biggest — an operator wants to see
        # all of them, and a second cluster forming is worth seeing before it alerts.
        for g in groups:
            if len(g) < min_people:
                continue
            xs = [pts[i][0] for i in g]
            ys = [pts[i][1] for i in g]
            pad = (radius_persons * sum(sizes[i] for i in g) / len(g) / 2.0
                   if mode == "person" else radius / 2.0)
            # Clustering works in width-normalised space (isotropic); OVERLAYS are
            # published in the conventional (x/w, y/h) space every other module uses, so
            # the vertical component has to be converted back or the box renders
            # squashed against the top of the frame.
            ay = w / float(h)
            overlays.append({
                "kind": "box",
                "box": [max(0.0, min(xs) - pad), max(0.0, (min(ys) - pad) * ay),
                        min(1.0, max(xs) + pad), min(1.0, (max(ys) + pad) * ay)],
                "color": "dnd" if self._alerted else "idle",
                "label": f"GATHERING {len(g)}",
            })

        if not overlays:
            overlays = [{"kind": "label", "text": f"CROWD {size}/{min_people}",
                         "color": "accent-soft"}]
        self.metrics.maybe_report()
        return events, overlays
