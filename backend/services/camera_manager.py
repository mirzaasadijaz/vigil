import os
import cv2
import numpy as np
import threading
import time
from backend.services import perf_debug as perf
from backend.services.connection_manager import connection_manager

# Downscale width for frames sent to the perception worker. Higher = smaller/farther
# subjects survive detection, at the cost of slower inference.
# NOTE: ultralytics re-letterboxes to imgsz=640 internally, so this mainly benefits the
# face backbone (det_size 640x640 over a wider frame) and the Re-ID crops.
DET_WIDTH = 1280

# Width for FAST-lane frames specifically, and it is a large saving for free.
#
# The fast lane carries only person-backbone modules (head-count, trip wire). ultralytics
# letterboxes to imgsz=640 regardless of what it is handed, so a 1280x720 frame is scaled
# to 640x360 inside the detector anyway - a person 20px wide at 1280 is 10px wide by the
# time YOLO sees them, exactly as they would be if 640x360 had been sent in the first
# place. The extra pixels are discarded before any inference happens.
#
# What they are NOT free of is transport. Every fast-lane frame is pickled, copied
# through a multiprocessing queue and unpickled:
#
#     1280x720x3 = 2.76 MB per frame  x 10 fps  =  27.6 MB/s PER CAMERA
#      640x360x3 = 0.69 MB per frame  x 10 fps  =   6.9 MB/s PER CAMERA
#
# On a machine reporting 14,198 dropped frames on one camera, a 4x reduction in queue
# traffic on precisely the cameras that are starving is the cheapest capacity available.
#
# The SLOW lane keeps DET_WIDTH: face recognition (det_size 640x640 over a wider frame)
# and Re-ID crops genuinely do benefit, and the phone detector takes crops from it. Only
# the fast lane is safe to shrink, because only the fast lane is person-only.
DET_WIDTH_FAST = int(os.environ.get("DET_WIDTH_FAST", "640"))

# ── Live-view encode (optimisation plan item 3) ──────────────────────────────────
# The browser shows cameras in a grid at a few hundred pixels wide; encoding the live
# view at full sensor resolution is work nobody can see. Measured on this codebase:
#
#     JPEG encode 1920x1080   18.21 ms
#     resize + encode 960x540  6.06 ms      -> 3x cheaper, and far less network
#
# This path is HUMAN VIEWING ONLY. Detection runs on the separate detection frame, so
# nothing here can change what any model sees or decides. Set 0 to disable the downscale.
BROADCAST_WIDTH = int(os.environ.get("BROADCAST_WIDTH", "960"))
BROADCAST_QUALITY = int(os.environ.get("BROADCAST_QUALITY", "75"))

# ── Decode gating (optimisation plan item 1) ─────────────────────────────────────
# OFF BY DEFAULT, deliberately.
#
# cap.read() is grab() + retrieve(). Splitting them lets the loop pull packets without
# paying for the colour conversion and copy on frames nothing will use. Measured here:
#
#     read()  every frame            9.15 ms/frame
#     grab() all + retrieve() 1-in-3 3.70 ms/frame     -> 60% cheaper
#
# It is off by default because it changes WHICH frames reach the detectors. The rate is
# unchanged and the frames are marginally fresher, but they are not the byte-identical
# frames today's code would have picked - so detections could differ in detail. On a
# system whose alerts are known-good that is not a change to make silently.
#
# Enable with DECODE_GATING=1, then compare the module diagnostic logs before and after.
DECODE_GATING = os.environ.get("DECODE_GATING", "0").strip().lower() in ("1", "true", "yes", "on")

# ── Recent-frame ring (optimisation plan items 2 and 5) ──────────────────────────
# Depth of the per-camera raw frame history. Snapshots are encoded from this ON DEMAND
# when an event fires, instead of speculatively encoding a full-resolution JPEG on every
# slow-lane frame - 2 per second per camera, of which essentially all were discarded.
#
# It also removes the per-iteration `.copy()`: the capture thread appends new frames
# rather than overwriting one buffer, so a reference taken from the ring stays valid.
FRAME_RING = int(os.environ.get("FRAME_RING", "8"))

# ── Concurrent stream ceiling (optimisation plan item 6) ─────────────────────────
# Every open browser tab wakes a camera that would otherwise idle-stop, and each woken
# camera costs a decode thread. Opening six tabs starts six of them, which is the
# reported desktop hang.
#
# 0 = no limit (the historical behaviour). Set it to what the machine can actually
# sustain and the ceiling becomes a polite refusal rather than a hung desktop.
MAX_LIVE_STREAMS = int(os.environ.get("MAX_LIVE_STREAMS", "0"))

# ── Back-off when the perception queue is full ───────────────────────────────────
# Found by the perf log, and it is the reason the CPU stayed pinned:
#
#     cam 48: 2196 drops/sec   but the stream only delivers 15 fps
#
# You cannot drop 146x more frames than arrive. What was happening is a busy-spin. When
# the queue is full, _process_frame counts a drop and returns WITHOUT stamping the lane
# timer - so _work_due() is still true on the very next iteration, the AI loop does not
# sleep, and it spins as fast as the CPU allows. Every spin still ran the overlay draw
# and the per-frame bookkeeping, which is real work done thousands of times a second for
# nothing.
#
# The fix is to remember that the queue was full and not re-attempt for a short while.
# One interval of the fastest lane is the natural period: retrying sooner cannot succeed
# any better, because the child has not had time to consume anything.
QUEUE_BACKOFF = float(os.environ.get("QUEUE_BACKOFF", "0.05"))

# ── Analytics cadence ─────────────────────────────────────────────────────────────
# Two lanes, because the modules have very different frame-rate requirements.
#
# SLOW lane (ENQUEUE_INTERVAL): the historical cadence. Carries the full module list
# and the full-resolution JPEG used for alert snapshots. Face recognition, smoking,
# fire/smoke and phone all run off this lane, so their GPU cost is unchanged.
#
# FAST lane (TRACK_INTERVAL): person tracking only, for modules that need temporal
# continuity. ByteTrack associates by IoU (match_thresh 0.8); at 2 fps a walking person
# moves further than their own box width between frames, the boxes stop overlapping,
# association fails and the tracker emits NOTHING for the rest of the walk. Measured in
# backend/scripts/bench_tracking_cadence.py: association survives while per-frame
# displacement stays under roughly one box width, i.e. fps > speed / box_width. 10 fps
# covers a normal-to-fast walk at typical apparent sizes. Fast-lane frames carry no
# JPEG payload (head-count events never attach a snapshot), so the extra IPC is small.
#
# MEDIUM lane (PHONE_INTERVAL, Phase 2): phone perception. Small-object detection needs
# more frequent evaluation than the slow lane to accumulate temporal evidence, but far
# less than person tracking - and it costs a second inference pass, so it gets its own
# cadence rather than riding the fast lane.
ENQUEUE_INTERVAL = float(os.environ.get("ENQUEUE_INTERVAL", "0.5"))
TRACK_INTERVAL = float(os.environ.get("TRACK_INTERVAL", "0.1"))
PHONE_INTERVAL = float(os.environ.get("PHONE_INTERVAL", "0.2"))
# Cap the live-view encode rate so the fast lane cannot multiply JPEG cost. 0.1 s keeps
# the stream at the ~10 fps it already ran at.
BROADCAST_INTERVAL = float(os.environ.get("BROADCAST_INTERVAL", "0.1"))
# Which lane each module needs is declared on the MODULE, in
# backend/services/modules/registry.py, and derived here. It used to be two literal sets
# duplicated here and in perception.py, kept in agreement by a comment and a test; the
# registry is already the single source of truth for a module's backbone, geometry,
# config and events, and its cadence belongs with them. Importing it is safe from the
# capture thread - it is pure data with no torch.
from backend.services.modules.registry import (          # noqa: E402
    lane_modules as _lane_modules, modules_for_lane as _modules_for_lane,
    needs_lane as _needs_lane)

FAST_MODULES = _modules_for_lane("fast")
MEDIUM_MODULES = _modules_for_lane("medium")
LANE_MODULES = _lane_modules()
# Modules whose whole purpose depends on the camera being watched CONTINUOUSLY, so the
# capture loop must not idle out when the last viewer disconnects. See _capture_loop.
# `unattended` is here for a second reason too: it holds two background models per camera
# and an idle-out throws them away, which means a fresh warm-up before anything can fire.
ALWAYS_ON_MODULES = {"tamper", "unattended"}
LANE_INTERVALS = {"fast": lambda: TRACK_INTERVAL, "medium": lambda: PHONE_INTERVAL}
LANE_KEYS = {"fast": "enqueue_fast", "medium": "enqueue_medium"}

# Open-failure backoff. When a source fails to open (e.g. RTSP 401 / unreachable),
# don't re-attempt for a growing cooldown instead of hammering it every frame. This
# is critical for NVRs/IP-cams that LOCK the account after repeated failed auth: a
# tight reconnect loop would keep the lockout alive forever. Exponential per streak,
# capped, reset on the first good frame.
OPEN_FAIL_BASE = 15.0     # seconds for the first failure
OPEN_FAIL_CAP = 300.0     # max backoff (5 min)

# Overlay color tokens -> BGR (mirror the frontend theme accents).
_OVERLAY_BGR = {
    "accent":      (242, 101, 88),    # blurple #5865f2
    "accent-soft": (218, 137, 114),   # #7289da
    "online":      (90, 165, 35),     # #23a55a
    "idle":        (50, 178, 240),    # #f0b232
    "dnd":         (67, 63, 242),     # #f23f43
}


def create_no_signal_frame(text="NO SIGNAL") -> bytes:
    frame = np.zeros((480, 640, 3), dtype=np.uint8)
    cv2.putText(frame, text, (140, 250), cv2.FONT_HERSHEY_SIMPLEX, 1.2, (80, 80, 80), 2)
    _, buf = cv2.imencode(".jpg", frame)
    return buf.tobytes()


NO_SIGNAL_BYTES = create_no_signal_frame()
CONNECTING_BYTES = create_no_signal_frame("CONNECTING...")
DISABLED_BYTES = create_no_signal_frame("CAMERA DISABLED")


class CameraInstance:
    def __init__(self, source, frame_rate: int = 10, cam_id=None, modules=None):
        self.source = source
        self.cam_id = cam_id
        self.frame_rate = frame_rate
        self.modules = modules or []          # assigned AI modules (from module_config)
        # Camera tampering (Kohat POC #4) is checked HERE, in the main process, not in
        # the perception child - see backend/services/tamper.py for why that placement
        # is the whole point. Created lazily so a camera without the module pays nothing.
        self._tamper = None
        self._unattended = None
        self.cap = None
        self.frame = None
        # Recent RAW frames, newest last. Snapshots are encoded from here on demand, so
        # the alert carries the exact frame its decision was made on without the
        # pipeline pre-encoding one for every frame just in case.
        self._ring: list = []
        self._ring_seq = 0
        self.annotated_frame = None
        self.stopped = True
        self.connecting = False
        self.last_log_time = {}
        self.ai_busy = False
        self.frame_count = 0
        self.lock = threading.Lock()
        self.error_count = 0
        self.last_activity = time.time()
        self.fail_until = 0.0        # don't re-attempt opening before this time
        self.fail_streak = 0         # consecutive open/read failures (drives backoff)
        self._sched: dict[str, float] = {}   # monotonic timestamps for the cadence lanes
        self._queue_full_until = 0.0         # see QUEUE_BACKOFF

    # ── cadence helpers ──────────────────────────────────────────────────────
    def _lane_enabled(self, lane: str) -> bool:
        """Does this camera run a module that needs `lane`?

        Only the modules of THAT lane count, not the cumulative set — otherwise a
        phone-only camera would be dragged into the head-count fast lane and pay for
        10 fps person tracking it does not need.

        The lane is a PER-CAMERA setting (`lane` in this module's config entry) with the
        registry value as its default, so an operator assigning modules decides the
        cadence for THIS camera rather than inheriting one global choice."""
        return any(m.get("enabled", True) and _needs_lane(m, lane)
                   for m in (self.modules or []))

    def _push_frame(self, frame) -> None:
        """Publish a newly decoded frame.

        Appends to the ring rather than overwriting one buffer, which is what makes the
        per-iteration `.copy()` in the AI loop unnecessary: a reference handed out here
        stays valid until it falls off the end of the ring."""
        self._ring_seq += 1
        self._ring.append((self._ring_seq, frame))
        if len(self._ring) > FRAME_RING:
            del self._ring[0]
        self.frame = frame

    def frame_by_seq(self, seq):
        """The exact frame a perception task was built from, or None if it has aged out.

        This is what lets a snapshot be the frame the DECISION was made on rather than
        whatever the camera happens to be showing when the alert reaches the database."""
        for s_, f in self._ring:
            if s_ == seq:
                return f
        return None

    def _frame_wanted(self) -> bool:
        """Would anything actually use a frame decoded right now?

        Only consulted when DECODE_GATING is on. Deliberately generous: it says yes if a
        viewer is connected OR any lane is due, so gating can never starve the pipeline -
        at worst it decodes a frame nothing ended up using, which is what happens on
        every frame today."""
        if connection_manager.has_clients(str(self.cam_id)):
            return True
        if self._tamper is not None:
            return True                      # tampering samples the raw frame itself
        return self._work_due(time.monotonic())

    def _always_on(self) -> bool:
        """Should this camera keep capturing when nobody is watching it?

        Yes if it has ANY enabled analytic. Previously only `tamper` qualified, which
        left a subtler version of the same bug: an auto-started trip-wire camera would
        open, run for thirty seconds, decide nobody was watching and stop - so detection
        would silently cease shortly after boot.

        A camera assigned an analytic is doing work whether or not a human is looking at
        it; that is the entire point of assigning it. Cameras with NO modules are pure
        viewing sources with nothing to detect, and still idle-stop as before."""
        return any(m.get("module") and m.get("enabled", True)
                   for m in (self.modules or []))

    def _fast_enabled(self) -> bool:
        """Kept as the Phase 1 name: does the fast person-tracking lane apply here?"""
        return self._lane_enabled("fast")

    def _sub_slow_lanes(self) -> list:
        """Lanes faster than the slow one that this camera actually needs."""
        return [lane for lane in ("fast", "medium") if self._lane_enabled(lane)]

    def _ready(self, now: float, key: str, interval: float) -> bool:
        """Is `key` due? Pure check — call _stamp() when the work actually happens."""
        last = self._sched.get(key)
        return last is None or (now - last) >= interval

    def _stamp(self, key: str, now: float) -> None:
        self._sched[key] = now

    def _work_due(self, now: float) -> bool:
        """Anything to do this iteration? Checked BEFORE copying the frame, so the
        fast lane's tighter loop does not turn into a continuous full-frame memcpy.

        Returns False while the perception queue is known to be full. Without that the
        loop spins: the lane stays due because nothing was enqueued, so it retries
        immediately, forever. See QUEUE_BACKOFF."""
        if now < self._queue_full_until:
            return False
        if self._ready(now, "enqueue", ENQUEUE_INTERVAL):
            return True
        for lane in self._sub_slow_lanes():
            if self._ready(now, LANE_KEYS[lane], LANE_INTERVALS[lane]()):
                return True
        return (connection_manager.has_clients(str(self.cam_id))
                and self._ready(now, "broadcast", BROADCAST_INTERVAL))

    def _register_failure(self):
        """Grow the reconnect backoff after a failed open / dropped stream so we stop
        hammering a locked or unreachable source."""
        self.fail_streak += 1
        backoff = min(OPEN_FAIL_BASE * (2 ** (self.fail_streak - 1)), OPEN_FAIL_CAP)
        self.fail_until = time.time() + backoff
        key = self.cam_id if self.cam_id is not None else self.source
        print(f"[CAM {key}] open/stream failed (streak={self.fail_streak}); "
              f"backing off {backoff:.0f}s before retry.", flush=True)

    def start(self):
        with self.lock:
            self.last_activity = time.time()
            if not self.stopped or self.connecting:
                return
            if time.time() < self.fail_until:
                return   # in backoff after a failed open — don't hammer the source
            self.stopped = False
            self.connecting = True
            threading.Thread(target=self._capture_loop, name=f"Cap-{self.source}", daemon=True).start()
            threading.Thread(target=self._ai_loop, name=f"AI-{self.source}", daemon=True).start()

    def stop(self):
        with self.lock:
            self.stopped = True
            self.connecting = False
            if self.cap:
                try:
                    self.cap.release()
                except Exception:
                    pass
                self.cap = None

    def _capture_loop(self):
        actual_source = self.source
        if isinstance(self.source, str) and self.source.isdigit():
            actual_source = int(self.source)
        try:
            if isinstance(actual_source, int):
                found = False
                for backend in [cv2.CAP_DSHOW, cv2.CAP_MSMF, cv2.CAP_ANY]:
                    if found:
                        break
                    for idx in [0, 1, 2]:
                        try:
                            tmp = cv2.VideoCapture(idx, backend)
                            if tmp and tmp.isOpened():
                                self.cap = tmp
                                found = True
                                break
                            elif tmp:
                                tmp.release()
                        except Exception:
                            continue
            else:
                self.cap = cv2.VideoCapture(actual_source)

            if not self.cap or not self.cap.isOpened():
                self._register_failure()
                self.stopped = True
                self.connecting = False
                return

            self.cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
            self.connecting = False
            # Tell the false-detection layer this camera has just started, so its
            # warm-up window begins now. The tracker is unstable for the first seconds
            # of a stream and that noise should not become alerts.
            try:
                from backend.services.false_detection import FILTER
                FILTER.camera_started(self.cam_id)
            except Exception:
                pass

            while not self.stopped:
                # Self-terminate after 30s of no activity — but an open WebSocket
                # viewer counts as activity, so live streams don't freeze mid-watch.
                #
                # A camera running an ALWAYS-ON module is exempt. Camera tampering is the
                # case that forced this: it detects a lens being sprayed, blocked or
                # turned away, and those things happen precisely when nobody is watching.
                # A tamper detector that stops the moment the last viewer closes the tab
                # reports only the tampering somebody was already looking at, which is
                # the tampering that needed reporting least.
                #
                # The cost is real and worth stating: such a camera decodes continuously
                # whether or not anyone is watching. That is the point, but it is not
                # free, so only modules that are useless without it belong in
                # ALWAYS_ON_MODULES.
                if (time.time() - self.last_activity > 30
                        and not connection_manager.has_clients(str(self.cam_id))
                        and not self._always_on()):
                    break
                try:
                    if DECODE_GATING:
                        # Pull the packet cheaply; only pay for the colour conversion and
                        # copy when something is actually going to look at the frame.
                        # See DECODE_GATING - off by default because it changes WHICH
                        # frames reach the detectors.
                        if not self.cap.grab():
                            success, frame = False, None
                        elif self._frame_wanted():
                            with perf.stage(self.cam_id, "decode"):
                                success, frame = self.cap.retrieve()
                        else:
                            continue          # grabbed and discarded, nothing decoded
                    else:
                        with perf.stage(self.cam_id, "decode"):
                            success, frame = self.cap.read()

                    if success and frame is not None:
                        self._push_frame(frame)
                        self.error_count = 0
                        self.fail_streak = 0     # healthy stream — clear backoff
                        self.fail_until = 0.0
                        perf.summarise(self.cam_id)
                    else:
                        self.error_count += 1
                        if self.error_count > 50:
                            self._register_failure()
                            break
                        time.sleep(0.01)
                except Exception:
                    self._register_failure()
                    break
        except Exception:
            self._register_failure()
        self.stop()

    def _ai_loop(self):
        while not self.stopped:
            if self.frame is None or self.ai_busy:
                time.sleep(0.1)
                continue
            fast = bool(self._sub_slow_lanes())   # any lane faster than the slow one
            self.frame_count += 1
            # Cameras WITHOUT a fast module keep the historical every-Nth-iteration
            # gate exactly as before. Cameras WITH one are gated on wall-clock instead,
            # so the fast lane can reach its interval — `process_frame_rate` cannot
            # express 10 fps reliably because it counts loop iterations, not frames.
            if not fast and self.frame_count % self.frame_rate != 0:
                time.sleep(0.01)
                continue
            now = time.monotonic()
            if fast and not self._work_due(now):
                # Sleep longer while the queue is backed off: there is nothing this
                # camera can usefully do until the child catches up, and spinning at
                # 200 Hz to discover that is exactly the bug this replaced.
                time.sleep(0.02 if now < self._queue_full_until else 0.005)
                continue
            self.ai_busy = True
            try:
                # No .copy() here any more. The capture thread appends to a ring rather
                # than overwriting one buffer, so this reference stays valid for as long
                # as it takes to use it - see _push_frame. A full-frame memcpy was ~2 ms
                # at 1080p, paid up to ten times a second per camera.
                raw_frame = self.frame
                self.annotated_frame = self._process_frame(raw_frame)
                if self.cam_id is not None and self.annotated_frame is not None:
                    cam_key = str(self.cam_id)
                    # Throttle the live-view encode on fast cameras so the tighter loop
                    # does not multiply JPEG cost; unchanged for everyone else.
                    if connection_manager.has_clients(cam_key) and (
                            not fast or self._ready(now, "broadcast", BROADCAST_INTERVAL)):
                        self._stamp("broadcast", now)
                        self._broadcast(cam_key, self.annotated_frame)
            except Exception:
                pass
            finally:
                self.ai_busy = False

    def _broadcast(self, cam_key: str, frame) -> None:
        """Encode and publish one frame for the live view.

        Downscaled first: a browser grid shows each camera a few hundred pixels wide, so
        encoding at full sensor resolution is work nobody can see. Measured at 1080p this
        is roughly 3x cheaper end to end, and cuts the outbound bandwidth by about as
        much.

        HUMAN VIEWING PATH ONLY. Detection runs on the separate detection frame built in
        _process_frame, so nothing here can alter what any model sees."""
        with perf.stage(self.cam_id, "broadcast"):
            out = frame
            if BROADCAST_WIDTH:
                h, w = frame.shape[:2]
                if w > BROADCAST_WIDTH:
                    out = cv2.resize(frame,
                                     (BROADCAST_WIDTH, int(h * BROADCAST_WIDTH / w)),
                                     interpolation=cv2.INTER_AREA)
            ok, buf = cv2.imencode(".jpg", out,
                                   [cv2.IMWRITE_JPEG_QUALITY, BROADCAST_QUALITY])
            if ok:
                connection_manager.broadcast_sync(cam_key, buf.tobytes())

    def _process_frame(self, frame):
        """Module-driven: the camera thread does NO inference. It throttles frames
        (with the camera's assigned modules) onto the perception queue; the isolated
        worker runs the needed detectors + rules. Overlays are drawn back here."""
        from backend.services import perception

        from backend.services import module_toggles
        # Global kill switches are applied FIRST, before anything looks at the list. A
        # globally-disabled module must never reach the perception queue, so the child
        # never loads a backbone for it and spends no VRAM on it.
        enabled = module_toggles.filter_modules(
            [m for m in self.modules if m.get("enabled", True)])

        # Tampering first, and deliberately BEFORE the `not enabled` bail-out below is
        # reached via its own module check: it is the one analytic that must keep working
        # when the perception child is dead, so it must not sit behind anything that
        # depends on the child being alive.
        self._check_tamper(frame, enabled)
        self._check_unattended(frame, enabled)

        if not enabled:
            return frame   # no analytics assigned - just stream the raw feed

        now = time.monotonic()
        slow_due = self._ready(now, "enqueue", ENQUEUE_INTERVAL)
        due = [lane for lane in self._sub_slow_lanes()
               if self._ready(now, LANE_KEYS[lane], LANE_INTERVALS[lane]())]
        # Backpressure is measured on THIS camera's shard, not globally. With sharding
        # enabled a global figure would let one busy shard throttle cameras that belong
        # to an idle one - the exact starvation the split exists to remove.
        if (slow_due or due) and perception.queue_size(self.cam_id) >= perception.MAX_QUEUE:
            # Queue full: this frame is lost. Count it and say so, periodically.
            key = str(self.cam_id)
            _drops[key] = _drops.get(key, 0) + 1
            perf.note_drop(self.cam_id)
            # Do not come straight back: the child cannot have drained anything yet, and
            # retrying immediately is what turned a full queue into a busy-spin.
            self._queue_full_until = now + QUEUE_BACKOFF
            if now - _drop_last_log.get(key, 0.0) >= DROP_LOG_INTERVAL:
                _drop_last_log[key] = now
                print(f"[CAM-MGR] camera {self.cam_id}: perception queue FULL, "
                      f"{_drops[key]} frame(s) dropped so far - the machine is at "
                      f"capacity, so this camera's modules will miss events",
                      flush=True)

        if (slow_due or due) and perception.queue_size(self.cam_id) < perception.MAX_QUEUE:
            # A due slow frame always wins: it is a superset of every other lane.
            if slow_due:
                lane, mods = "slow", enabled
                self._stamp("enqueue", now)
                for _k in LANE_KEYS.values():
                    self._stamp(_k, now)
            else:
                lane = "medium" if "medium" in due else "fast"
                wanted = set()
                for _l in due:
                    wanted |= LANE_MODULES[_l]
                    self._stamp(LANE_KEYS[_l], now)
                mods = [m for m in enabled if m.get("module") in wanted]
            # Person-only lanes do not need the full width - see DET_WIDTH_FAST.
            target = DET_WIDTH_FAST if lane == "fast" else DET_WIDTH
            with perf.stage(self.cam_id, "resize"):
                h, w = frame.shape[:2]
                if w > target:
                    det = cv2.resize(frame, (target, int(h * target / w)))
                    scale = w / target
                else:
                    det = frame.copy()
                    scale = 1.0
            # Only the pure-tracking lane skips the full-resolution JPEG: head-count
            # events never attach a snapshot, but phone_use events do, so the medium
            # lane still has to carry one.
            #
            # `tripwire` also rides the fast lane AND does attach a snapshot. Rather than
            # encode a JPEG 10x a second on every camera to serve a handful of events,
            # the writer falls back to latest_jpeg() above when a fast-lane event asks
            # for a picture. See that function for the trade-off.
            # No speculative JPEG. This used to encode a FULL-RESOLUTION snapshot on
            # every slow-lane frame - two per second per camera, forever - to serve an
            # alert that fires a few times an hour. Measured at 1080p that is 18 ms of
            # pure waste per encode.
            #
            # Instead the task carries the ring sequence number of the frame it was built
            # from, and the writer encodes THAT EXACT FRAME on demand when an event
            # actually fires. Strictly better than before: no speculative work, and the
            # snapshot is still the frame the decision was made on rather than whatever
            # the camera is showing by the time the alert reaches the database.
            with perf.stage(self.cam_id, "enqueue"):
                perception.enqueue({
                    "frame": det, "frame_buf": None,
                    "frame_seq": self._ring_seq,
                    "lane": lane, "fast": lane == "fast",
                    "cam_id": self.cam_id, "scale": scale, "modules": mods,
                })

        with perf.stage(self.cam_id, "overlay"):
            self._draw_overlays(frame, perception)
        return frame

    def _check_tamper(self, frame, enabled_modules) -> None:
        """Run the tamper monitor if this camera has the module assigned.

        Events are pushed onto the perception result queue via `submit_events`, so they
        travel the SAME writer path as every other module and inherit the
        false-detection layer, severity mapping and snapshot handling for free.

        Wrapped in a blanket except because this runs inside the capture loop: losing
        the video feed to protect against tampering would be an absurd trade."""
        cfg = next((m for m in (enabled_modules or [])
                    if m.get("module") == "tamper"), None)
        if cfg is None:
            self._tamper = None      # module switched off; drop the baseline
            return
        try:
            from backend.services import perception
            from backend.services.tamper import TamperMonitor
            if self._tamper is None:
                self._tamper = TamperMonitor(self.cam_id, cfg)
            else:
                self._tamper.config = cfg     # pick up edited config
            events = self._tamper.check(frame)
            if events:
                # A tamper snapshot is worth the JPEG encode: "the camera was blinded"
                # is not a claim anyone should have to take on trust.
                try:
                    buf = cv2.imencode(".jpg", frame)[1].tobytes()
                except Exception:
                    buf = None
                perception.submit_events(self.cam_id, events, buf)
        except Exception as e:
            print(f"[TAMPER] camera {self.cam_id} check skipped: {e}", flush=True)

    def _check_unattended(self, frame, enabled_modules) -> None:
        """Object dropped and left (#5). Classical CV in the MAIN process, like tamper.

        Same reasoning: no GPU work, needs every frame rather than a sampled one, and the
        main process has headroom while the perception child is the constrained one. The
        module keeps two background models per camera, so its state must live across
        frames - hence the instance held here rather than a call into a stateless helper.

        Blanket except for the same reason tamper has one: losing the video feed to
        protect against a dropped bag would be an absurd trade."""
        cfg = next((m for m in (enabled_modules or [])
                    if m.get("module") == "unattended"), None)
        if cfg is None:
            self._unattended = None      # module switched off; drop the background models
            return
        try:
            from backend.services import perception
            from backend.services.unattended import UnattendedMonitor
            if getattr(self, "_unattended", None) is None:
                self._unattended = UnattendedMonitor(self.cam_id, cfg)
            else:
                self._unattended.config = cfg     # pick up edited config
            events = self._unattended.check(frame)
            if events:
                try:
                    buf = cv2.imencode(".jpg", frame)[1].tobytes()
                except Exception:
                    buf = None
                # Carry each event's box through to the saved picture. "An object was
                # left somewhere" is not an alert anybody can act on; the operator has
                # to see WHERE, and on a busy scene they will never find it unaided.
                ovs = [o for e in events for o in (e.get("overlays") or [])]
                perception.submit_events(self.cam_id, events, buf, snapshot_overlays=ovs)
        except Exception as e:
            print(f"[UNATTENDED] camera {self.cam_id} check skipped: {e}", flush=True)

    def _draw_overlays(self, frame, perception):
        """Draw the latest analytics overlays (normalized 0-1 coords) for this camera:
        boxes, counting lines, zones, and labels. Lags the feed slightly (async)."""
        ov = perception.get_overlay(self.cam_id)
        if not ov:
            return
        _scale, items = ov
        draw_overlays_on_frame(frame, items)


# Last successfully-encoded frame per camera, so a momentary reconnect does not cost an
# alert its evidence. Bounded in age deliberately - see latest_jpeg().
_last_jpeg: dict[str, tuple[float, bytes]] = {}
_last_jpeg_lock = threading.Lock()
SNAPSHOT_FALLBACK_MAX_AGE = float(os.environ.get("SNAPSHOT_FALLBACK_MAX_AGE", "20"))

# ── Perception queue saturation ──────────────────────────────────────────────────
# There is ONE task queue and ONE child consuming it sequentially. Every camera running
# a fast-lane module pushes ~10 frames a second into it, so N cameras means 10N
# inferences per second through a single worker. Past that worker's throughput the queue
# fills and `_process_frame` simply skips - which it did SILENTLY, with no counter and no
# log line.
#
# That silence is the problem. A trip wire on four cameras where the machine can only
# keep up with two does not fail loudly: it quietly drops frames, and whichever cameras
# lose the race appear to "not work" while their logs show nothing wrong at all. The
# frames never reached the detector, so no module can report their absence.
#
# These counters make the ceiling visible. If you see this line, the machine is at
# capacity: reduce TRACK_INTERVAL, take a module off a camera, or add hardware. It is
# not a bug in the module that appears broken.
_drops: dict = {}
_drop_last_log: dict = {}
DROP_LOG_INTERVAL = float(os.environ.get("PERCEPTION_DROP_LOG_INTERVAL", "30"))


def jpeg_for_seq(cam_id, seq) -> bytes | None:
    """Encode the EXACT frame a perception task was built from.

    The pipeline no longer pre-encodes a snapshot for every frame on the chance that an
    event might fire (see _process_frame). Instead each task carries the ring sequence
    number of its source frame, and this encodes that frame - and only that frame - when
    an alert actually needs evidence.

    Two properties matter, and they used to be in tension:

      * no speculative work: two full-resolution JPEGs per second per camera were being
        produced and discarded
      * the snapshot is still the frame the DECISION was made on, not whatever the camera
        happens to be showing a few hundred milliseconds later when the row is written

    Returns None if the frame has aged out of the ring, which the caller must treat as
    "fall back to the current frame", not as an error."""
    if seq is None:
        return None
    try:
        for inst in list(camera_manager.instances.values()):
            if str(getattr(inst, "cam_id", "")) != str(cam_id):
                continue
            frame = inst.frame_by_seq(seq)
            if frame is None:
                continue
            with perf.stage(cam_id, "snapshot"):
                ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 80])
            if ok:
                return buf.tobytes()
    except Exception as e:
        print(f"[CAM-MGR] jpeg_for_seq({cam_id},{seq}) failed: {e}", flush=True)
    return None


def latest_jpeg(cam_id) -> bytes | None:
    """Encode this camera's most recent frame as a JPEG, right now.

    Exists because the FAST lane deliberately carries no JPEG payload. That was correct
    when head-count was the only module on it - head-count events never attach a
    snapshot - but `tripwire` DOES attach one, and rides the fast lane because it needs
    the same ~10 fps to see a crossing at all. The result was alerts arriving with no
    picture: `save_snapshot` returns None when handed an empty buffer.

    Encoding every fast-lane frame would fix it and cost a full-resolution JPEG encode
    at 10 fps per camera, forever, to serve an event that happens a few times an hour.
    Encoding here instead costs one encode PER ALERT.

    The trade-off, stated plainly: this frame is a few hundred milliseconds newer than
    the one the crossing was detected in. For a person walking through a doorway they
    are still in shot, so the picture is still evidence of the event - but it is not the
    exact frame the decision was made on, and a very fast mover could be near the edge
    of it.

    WHY THIS SCANS EVERY INSTANCE INSTEAD OF TAKING THE FIRST MATCH
        `instances` is keyed by SOURCE STRING and entries are never removed, so one
        camera id can legitimately map to several CameraInstance objects - an old one
        left behind when its RTSP URL was edited, plus the live one. The first version
        of this function returned None the moment it found a matching instance whose
        `frame` was None, so a single stale entry was enough to lose the picture. That
        is exactly what produced "some alerts have images and some do not".

        So: scan all of them and take the first that actually holds a frame.

    LAST-KNOWN-GOOD FALLBACK
        Even the live instance has `frame is None` for a moment while it reconnects, and
        an alert with no evidence is a poor alert. Each successful encode is cached per
        camera and reused if the live frame is unavailable and the cache is younger than
        `SNAPSHOT_FALLBACK_MAX_AGE`. Beyond that age it is refused rather than attached:
        a picture from two minutes ago is not evidence of this event, and silently
        pretending otherwise would be worse than no picture at all.

    Returns None only when there is no usable frame anywhere, which the caller must
    treat as "no snapshot" rather than as an error."""
    now = time.time()
    try:
        candidates = [inst for inst in list(camera_manager.instances.values())
                      if str(getattr(inst, "cam_id", "")) == str(cam_id)]
        for inst in candidates:
            frame = inst.frame
            if frame is None:
                continue
            ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 80])
            if ok:
                data = buf.tobytes()
                with _last_jpeg_lock:
                    _last_jpeg[str(cam_id)] = (now, data)
                return data
        # Nothing live. Fall back to the most recent good frame, if it is recent enough.
        with _last_jpeg_lock:
            cached = _last_jpeg.get(str(cam_id))
        if cached and (now - cached[0]) <= SNAPSHOT_FALLBACK_MAX_AGE:
            print(f"[CAM-MGR] latest_jpeg({cam_id}): no live frame "
                  f"({len(candidates)} instance(s)); using cached frame "
                  f"{now - cached[0]:.1f}s old", flush=True)
            return cached[1]
        print(f"[CAM-MGR] latest_jpeg({cam_id}): no frame available "
              f"({len(candidates)} instance(s), cache "
              f"{'stale' if cached else 'empty'}) - alert will have no snapshot",
              flush=True)
    except Exception as e:
        print(f"[CAM-MGR] latest_jpeg({cam_id}) failed: {e}", flush=True)
    return None


def live_stream_count() -> int:
    """Cameras currently capturing because somebody is watching them."""
    n = 0
    for inst in list(camera_manager.instances.values()):
        if inst.stopped:
            continue
        if connection_manager.has_clients(str(getattr(inst, "cam_id", ""))):
            n += 1
    return n


def stream_slot_available(cam_id) -> bool:
    """Is there room to start streaming another camera?

    A camera already streaming always says yes - the ceiling limits how many are STARTED,
    it does not interrupt one somebody is already watching."""
    if MAX_LIVE_STREAMS <= 0:
        return True
    if connection_manager.has_clients(str(cam_id)):
        return True
    return live_stream_count() < MAX_LIVE_STREAMS


def dropped_frames() -> dict:
    """Per-camera count of frames lost to a full perception queue.

    Zero everywhere means the machine is keeping up. Anything non-zero means at least
    one camera's modules are working from an incomplete view of what happened."""
    return dict(_drops)


def draw_overlays_on_frame(frame, items):
    """Draw analytics overlay primitives (normalized 0-1 coords) onto a BGR frame
    in place. Shared by the live feed (camera thread) and saved snapshots (perception
    writer). Returns the same frame for convenience."""
    if not items:
        return frame
    label_row = 0          # see the "label" branch below
    h, w = frame.shape[:2]
    font = cv2.FONT_HERSHEY_SIMPLEX
    placed: list = []      # label rects already drawn, for collision avoidance

    def put_label(text, x, y, colour, scale=0.55):
        """Draw a label, nudging it clear of ones already placed.

        Three trip wires drawn from nearly the same origin printed three labels on top
        of each other - a screenshot showed "TRIP WIRE 33" three times as one illegible
        smear. The stacking that the "label" kind does for corner text was never applied
        to line, zone or box labels, which are positioned by their geometry and so
        collide whenever two pieces of geometry start in the same place."""
        (tw, th), _ = cv2.getTextSize(text, font, scale, 2)
        for _ in range(12):                      # bounded: never loop on a full frame
            rect = (x, y - th - 2, x + tw, y + 4)
            if not any(rect[0] < q[2] and q[0] < rect[2]
                       and rect[1] < q[3] and q[1] < rect[3] for q in placed):
                break
            y += th + 6                          # try the next row down
            if y > h - 4:                        # ran out of frame: draw it anyway
                break
        placed.append((x, y - th - 2, x + tw, y + 4))
        cv2.putText(frame, text, (x, y), font, scale, colour, 2)
    for it in items:
        color = _OVERLAY_BGR.get(it.get("color", "accent"), _OVERLAY_BGR["accent"])
        kind = it.get("kind")
        if kind == "box":
            x1, y1, x2, y2 = it["box"]
            p1 = (int(x1 * w), int(y1 * h)); p2 = (int(x2 * w), int(y2 * h))
            cv2.rectangle(frame, p1, p2, color, 2)
            if it.get("label"):
                ty = p1[1] - 8 if p1[1] - 8 > 12 else p2[1] + 18
                put_label(it["label"], p1[0], ty, color)
        elif kind == "line":
            pts = it.get("points") or []
            if len(pts) >= 2:
                a = (int(pts[0][0] * w), int(pts[0][1] * h))
                b = (int(pts[1][0] * w), int(pts[1][1] * h))
                cv2.line(frame, a, b, color, 3)
                if it.get("label"):
                    put_label(it["label"], a[0], max(16, a[1] - 8), color, 0.6)
        elif kind == "zone":
            poly = it.get("polygon") or []
            if len(poly) >= 3:
                arr = np.array([[int(p[0] * w), int(p[1] * h)] for p in poly], dtype=np.int32)
                cv2.polylines(frame, [arr], True, color, 2)
                if it.get("label"):
                    put_label(it["label"], int(arr[0][0]), max(16, int(arr[0][1] - 6)), color)
        elif kind == "label" and it.get("text"):
            # STACK them. Every label used to be drawn at the same fixed point, so on a
            # camera running several modules they landed on top of one another and only
            # the last was legible - which looked exactly like the other modules not
            # running at all. Crowd's "CROWD n/min" readout was being painted over by
            # head-count's IN/OUT tally.
            cv2.putText(frame, it["text"], (12, 28 + 26 * label_row), font, 0.7,
                        color, 2)
            label_row += 1
    return frame


class CameraManager:
    def __init__(self):
        self.instances: dict[str, CameraInstance] = {}
        self.lock = threading.Lock()

    def get_instance(self, source: str, frame_rate: int = 10, cam_id=None, modules=None) -> CameraInstance:
        with self.lock:
            if source not in self.instances:
                self.instances[source] = CameraInstance(source, frame_rate, cam_id, modules)
            else:
                inst = self.instances[source]
                if cam_id is not None:
                    inst.cam_id = cam_id
                if modules is not None:
                    inst.modules = modules   # refresh assignments each viewer connect
            return self.instances[source]

    def refresh_modules_by_cam_id(self, cam_id: int, modules: list) -> None:
        """Find the running instance by cam_id and update its modules (no WS reconnect needed).

        The printed list is the one the DATABASE gave us, before global toggles or lane
        filtering. Compare it with `[PERCEPTION] camera N modules:` from the child: if
        they differ, the module was dropped in between, and that is a bug rather than a
        configuration mistake."""
        with self.lock:
            for inst in self.instances.values():
                if inst.cam_id == cam_id:
                    inst.modules = modules
                    names = ", ".join(sorted(
                        m.get("module") for m in (modules or [])
                        if m.get("module") and m.get("enabled", True))) or "(none)"
                    print(f"[CAM-MGR] Refreshed modules for camera {cam_id}: {names}",
                          flush=True)
                    return
            print(f"[CAM-MGR] No running instance found for camera {cam_id}", flush=True)


camera_manager = CameraManager()


def get_frame_bytes(source: str, frame_rate: int = 10, cam_id=None, modules=None) -> bytes:
    instance = camera_manager.get_instance(source, frame_rate, cam_id, modules)
    instance.last_activity = time.time()
    instance.start()
    f = instance.annotated_frame if instance.annotated_frame is not None else instance.frame
    if f is not None:
        _, buf = cv2.imencode(".jpg", f)
        return buf.tobytes()
    return CONNECTING_BYTES if instance.connecting else NO_SIGNAL_BYTES


def generate_frames(source: str, frame_rate: int = 10, cam_id=None, modules=None):
    while True:
        data = get_frame_bytes(source, frame_rate, cam_id, modules)
        yield (
            b"--frame\r\n"
            b"Content-Type: image/jpeg\r\n"
            b"Content-Length: " + str(len(data)).encode() + b"\r\n\r\n"
            + data + b"\r\n"
        )
        time.sleep(0.05)
