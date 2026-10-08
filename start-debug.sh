#!/bin/bash
# Start VIGIL with the Kohat POC diagnostic logs switched on.
#
#   bash start-debug.sh
#
# This is a thin wrapper around start.sh - it sets the diagnostic switches and then
# hands over. start.sh itself is left alone deliberately, so production never picks up
# TRACE logging by accident just because somebody forgot to unset a variable.
#
# EVERY VALUE BELOW CAN BE OVERRIDDEN on the command line, because they use ${VAR:-default}:
#
#   TAMPER_COVER_SUSTAIN=1 bash start-debug.sh         # even faster cover alert
#   PERSON_IOU=0.85 bash start-debug.sh                # keep overlapping people
#   CROWD_DEBUG=0 bash start-debug.sh                  # silence one module
#
# Logs are written to logs/ in the repo root. Confirm on start-up that each enabled
# module prints a `diagnostic log -> ...` line; a missing line means the flag did not
# reach the process, and a path that looks wrong means BASE_DIR in .env is still the
# .env.example placeholder.
set -e
cd "$(dirname "$0")"

# ── Per-module diagnostic logs ───────────────────────────────────────────────────
# trace = full, replayable capture. debug = decisions + near-misses. 1 = decisions only.
export TRIPWIRE_DEBUG="${TRIPWIRE_DEBUG:-trace}"
export TAMPER_DEBUG="${TAMPER_DEBUG:-trace}"
export CROWD_DEBUG="${CROWD_DEBUG:-trace}"
export VEHICLE_DEBUG="${VEHICLE_DEBUG:-trace}"
export INTRUSION_DEBUG="${INTRUSION_DEBUG:-trace}"
# Object dropped and left (#5). `trace` also records the background-mask statistics
# (long_fg / short_fg / static_frac), which is the only way to tell "nothing was
# dropped" apart from "the dual background never produced a candidate".
export UNATTENDED_DEBUG="${UNATTENDED_DEBUG:-trace}"

# ── Pipeline performance ─────────────────────────────────────────────────────────
# Where the CPU actually goes: per-camera decode / resize / encode timings and a
# cpu_pct figure per camera. `1` gives SUMMARY lines only, which is what you want for
# monitoring; `trace` adds a sampled line per operation and is for digging.
export PERF_DEBUG="${PERF_DEBUG:-1}"

# The trip wire INHERITS head-count's crossing machine, so head-count's log is where the
# reason for a MISSED crossing lives - tripwire_debug.log can only report what it was
# handed. Keep both on while the trip wire is being evaluated.
export HEADCOUNT_DEBUG="${HEADCOUNT_DEBUG:-trace}"

# Phone detection is already tuned; off by default here to keep captures readable.
# Phone detection is under investigation, so this is ON. logs/phone_debug.log records
# association scoring and the temporal window - which is what tells a missed phone
# (detector never saw it) apart from a rejected one (seen, but not associated with a
# person, or not held long enough).
#
# It was 0 here, which is why logs/phone_debug.log contained nothing but STARTUP lines
# and the problem could not be diagnosed at all. Set PHONE_DEBUG=0 to silence it again
# once phone detection is behaving.
export PHONE_DEBUG="${PHONE_DEBUG:-trace}"

# ── Tamper: testing-friendly timings ─────────────────────────────────────────────
# The SUSTAIN windows are now per-signal and their defaults are already right, so this
# script no longer overrides them:
#
#     covered  2s   an object on the lens is unambiguous - alert fast
#     blur     4s
#     moved   10s   fires on any big scene change, incl. someone walking past
#
# Only the COOLDOWN is shortened, because five minutes between repeat tests of the same
# signal makes iterating painful. Raise it again for production.
export TAMPER_COOLDOWN="${TAMPER_COOLDOWN:-30}"

# ── Crowd: testing-friendly ──────────────────────────────────────────────────────
# min_people=4 needs four people to stand still together; 3 is easier to arrange and
# still means something. RADIUS is the knob most likely to be wrong for your camera - it
# is normalised to frame width, so a wide overview needs a SMALLER value than a close
# shot. Watch `largest=` in the TRACE log to pick it.
export CROWD_MIN_PEOPLE="${CROWD_MIN_PEOPLE:-3}"
export CROWD_SUSTAIN_SECONDS="${CROWD_SUSTAIN_SECONDS:-5}"
export CROWD_COOLDOWN="${CROWD_COOLDOWN:-20}"

# ── False-detection layer ────────────────────────────────────────────────────────
export FD_DEBUG="${FD_DEBUG:-1}"

# -- Perception sharding ---------------------------------------------------------
# How many perception CHILD processes to run. 1 (the default, and the default here)
# is exactly the historical behaviour: one child, one queue, no routing.
#
# The child is single-threaded by design, so its ceiling is one CPU core - about 53
# frames/sec against a measured demand of 41. Sharding does not reduce the total
# work; it spreads it across N cores, raising the ceiling to roughly N x 53.
#
#     PERCEPTION_SHARDS=2 bash start-debug.sh     # try this FIRST, and watch VRAM
#     PERCEPTION_SHARDS=3 bash start-debug.sh     # only after 2 looks healthy
#
# Each extra child costs its own CUDA context and its own copy of the face model, so
# VRAM does NOT stay flat. Check `nvidia-smi` after starting before going higher.
#
# TO BACK IT OUT: unset the variable (or set it to 1) and restart. No code change.
export PERCEPTION_SHARDS="${PERCEPTION_SHARDS:-1}"

# Routing diagnostics: which camera went to which shard, the resulting balance,
# gallery broadcasts and auto-enrol fan-out. `1` is enough for monitoring; `debug`
# adds the broadcasts; `trace` logs every routed frame and fills the file fast.
export SHARD_DEBUG="${SHARD_DEBUG:-1}"

# -- One YOLO for all cameras (instead of one per camera) ------------------------
# model.track(persist=True) keeps ByteTrack state on the MODEL object, so isolating
# each camera used to mean a whole YOLO instance per camera - measured at ~58 MB
# each, so ten cameras carried ten copies of the weights.
#
# =1 shares ONE model and keeps a BYTETracker per camera instead. Measured
# frame-for-frame identical (same track ids, same boxes) by:
#
#     python -m backend.scripts.bench_shared_model
#
# RUN THAT FIRST. Exit code 0 means safe to enable. It saves memory, not CPU: the
# instance is reused, so ten instances never made any one frame slower.
export PERSON_SHARED_MODEL="${PERSON_SHARED_MODEL:-0}"

echo "[VIGIL-DEBUG] diagnostics on:"
echo "               tripwire=$TRIPWIRE_DEBUG  headcount=$HEADCOUNT_DEBUG"
echo "               tamper=$TAMPER_DEBUG (covered=2s blur=4s moved=10s, cooldown=${TAMPER_COOLDOWN}s)"
echo "               crowd=$CROWD_DEBUG (min_people=$CROWD_MIN_PEOPLE sustain=${CROWD_SUSTAIN_SECONDS}s radius=${CROWD_RADIUS:-0.12})"
echo "               vehicle=$VEHICLE_DEBUG  intrusion=$INTRUSION_DEBUG  phone=$PHONE_DEBUG"
echo "               unattended=$UNATTENDED_DEBUG  (logs/unattended_debug.log)"
echo "               perf=$PERF_DEBUG  (logs/perf_debug.log — cpu_pct per camera)"
echo "               shards=$PERCEPTION_SHARDS  shard_log=$SHARD_DEBUG  (logs/shard_debug.log)"
echo "               shared_yolo=$PERSON_SHARED_MODEL  (0=one model per camera)"
echo "[VIGIL-DEBUG] logs -> $(pwd)/logs/"
echo "[VIGIL-DEBUG] NOTE: tamper cooldown is shortened for testing - raise it for production."
echo

exec bash ./start.sh
