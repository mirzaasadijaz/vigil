# VIGIL — perception sharding

Written 2026-08-28. Companion to `unoptimized-architecture.md` (Option A there).

**Status: implemented, off by default.** `PERCEPTION_SHARDS=1` is the shipped default and
is byte-for-byte the previous behaviour. Turning it on is one environment variable;
turning it off again is the same variable and a restart.

---

## 1. What this changes, and what it does not

Cameras are distributed across N perception child processes instead of one:

```
BEFORE                                AFTER (N=3)
                                      ┌──────────────┐
cam 47 ─┐                     cam 47 ─┤ Perception 0 │
cam 48 ─┤    ┌─────────────┐  cam 49 ─┘              │
cam 49 ─┼───►│ ONE process │          └──────────────┘
  ...   ─┤   │             │          ┌──────────────┐
cam 61 ─┘    └─────────────┘  cam 50 ─┤ Perception 1 │
                              cam 52 ─┘              │
                                      └──────────────┘
                                      ┌──────────────┐
                              cam 48 ─┤ Perception 2 │
                              cam 53 ─┤              │
                              cam 59 ─┤              │
                              cam 60 ─┤              │
                              cam 61 ─┘              │
                                      └──────────────┘
```

**Unchanged:** the YOLO model, every threshold, tracking behaviour within a camera,
every analytics rule, event semantics, the writer, the false-detection layer, the
severity map, snapshots, overlays.

**Why that claim is safe, concretely.** Every state container in the child is keyed by
`cam_id` — verified, all thirteen of them (`perception.py`, top of `_child_main`):

| | |
|---|---|
| `consumers[(cam_id, module)]` | the analytics rule instances |
| `person._models[cam_key]` | one YOLO + ByteTrack per camera already |
| `track_identity` · `track_votes` · `face_confirmed` | identity fusion and voting |
| `body_gallery` · `body_day` | Re-ID appearance galleries — **per camera, not global** |
| `reid_frame` · `fire_frame` · `vehicle_frame` | throttle counters |
| `smoke_last` · `att_crossed` · `att_prev_side` | cooldowns and ROI gating |
| `headcount_seed` · `tripwire_seed` · `seen_modules` | restored totals, reporting |

There is **no cross-camera state**. A camera's entire processing context moves with it.

### The one exception: the face gallery

`embedding_cache._gallery` is global per process, so each shard holds its own copy. Two
consequences, both handled:

1. **Gallery commands broadcast, never route.** `add`, `remove`, `add_extra`,
   `reset_autos`, `reload_gallery`, `seed_vecs` go to every shard. The call sites use
   `broadcast()` explicitly rather than relying on `enqueue()` to notice, so adding a
   command later cannot quietly get it wrong.
2. **Auto-enrolled vectors fan out.** With one child, a vector learnt from a live face
   was instantly usable by every camera. With N children it would only help that shard
   until the next full reload — a slow, invisible recognition regression rather than a
   failure. The writer now pushes each new vector to the other shards as `add_vecs`,
   which routes through `add_runtime_vector` so `ENROLL_MAX_AUTO` is enforced identically
   on every shard.

If galleries ever do drift, `POST /api/system/reload` rebuilds all of them from one DB
snapshot. That is the repair path.

---

## 2. What it buys — and what it does not

**It does not reduce CPU.** Total work is identical; it is spread. The measured cost
decomposes as:

```
person  12.4 ms/frame × 41.0 fps  +  face 15.7 ms/frame × 19.1 fps  =  80.8% of a core
measured                                                               78%
```

That 81% does not shrink. It becomes N × ~27%, **plus** one extra CUDA context and one
extra face-model copy per additional child.

- Complaint "one core is pegged at 99%" → this fixes it.
- Complaint "total machine CPU is high" → this makes it marginally worse.

What it buys is **ceiling**: ~53 fps → ~N × 53. Current demand is 41. So this is headroom
for the Kohat expansion, not an improvement to today's alerts. Reducing actual work is
Appendix D of `unoptimized-architecture.md`, and the two are complementary — do the
free `BROADCAST_INTERVAL` change as well, not instead.

---

## 3. Assignment — why not `cam_id % N`

Cameras cost wildly different amounts: a trip-wire camera runs at 10 fps, a slow-lane
camera at 2, and a `mobile` camera pays for the face backbone as well. Modulo ignores all
of that, and on this deployment it puts **both** fast-lane cameras on the same shard
(`47 % 3 = 50 % 3 = 2`):

| Strategy | shard 0 | shard 1 | shard 2 | peak |
|---|---|---|---|---:|
| `cam_id % 3` | 48, 60 → 15.5% | 49, 52, 61 → 29.0% | **47, 50, 53, 59 → 36.4%** | 36.4% |
| cost-balanced | 47, 49 → 26.4% | 50, 52 → 26.4% | 48, 53, 59, 60, 61 → 27.1% | **27.1%** |

Ideal is 26.7%. The implemented split lands **1.6% above ideal**; modulo lands 36% above.

Assignment is greedy least-loaded on an estimated cost, and is **persisted** to
`logs/shard_assignment.json`. Persistence is not an optimisation: moving a running camera
to another shard resets its ByteTrack ids. (Head-count and trip-wire totals re-seed from
`alert_logs`, so those recover; the tracks do not.)

Greedy is not optimal, but it is *incremental* — adding a tenth camera does not reshuffle
the nine already running, which matters more here than the last percent of balance.

### The cost model

`shards.PERSON_MS = 12.4`, `FACE_MS = 15.7`, from decomposing the measurement above. Lane
rates are read live from `camera_manager`, so retuning `TRACK_INTERVAL` or
`PHONE_INTERVAL` updates the model rather than leaving it stale. Both are overridable
(`SHARD_COST_PERSON_MS`, `SHARD_COST_FACE_MS`). They only need to be *relatively* right —
they rank cameras, they do not predict runtime.

---

## 4. Rolling it out

```bash
PERCEPTION_SHARDS=2 SHARD_DEBUG=1 bash start-debug.sh
```

**Start at 2, not 3.** VRAM is the one thing that cannot be predicted from here.

Then, in order:

1. **Confirm the children exist.** Start-up prints, unconditionally:
   ```
   [INIT] Perception processes started (2 shards, pid=..., ...); result-writer running.
   [SHARD] Sharding ENABLED: 2 perception children. ...
   [SHARD] camera 47 -> shard 0 (weight 124 ms/s; loads [12.4, 0.0] % of a core)
   ```
2. **Confirm the gallery reached every shard.** `[EMBED] Loaded N/N employee anchors`
   must appear **once per shard**. One line where you expected two means a broadcast was
   dropped and recognition will differ between shards.
3. **Check VRAM** — `nvidia-smi`. Baseline is 3.07 of 12 GB for one child. Each extra
   child adds its own CUDA context and face model. If two children fit comfortably, try
   three; if they do not, stop at two.
4. **Check the balance** — `GET /api/system/shards`, or the `BALANCE_SUMMARY` lines in
   `logs/shard_debug.log`. `peak_over_ideal` should be near 1.0.
5. **Check the CPU** — each child should sit near total ÷ N, not one at 78% and the rest
   idle. If one is hot, the cost model is wrong for your cameras; compare against
   `perf_debug.log` and adjust `SHARD_COST_*`.
6. **Verify the alerts still behave** — the trip wire, tamper and crowd captures you
   already field-tested. Nothing about them should change. If anything has, back out.

### Backing it out

```bash
PERCEPTION_SHARDS=1 bash start-debug.sh     # or just unset it
```

No code revert, no migration, no database change. The assignment file is ignored when
unsharded and is safe to delete. If you want the code gone as well, the change is
confined to `backend/services/shards.py`, `backend/services/shard_debug.py`, and the
routing block in `perception.py`.

---

## 5. The trace log

`logs/shard_debug.log`, controlled by one variable:

```
SHARD_DEBUG=1        lifecycle, assignment, BALANCE tables, METRICS   (start here)
SHARD_DEBUG=debug    + gallery broadcasts, enrol fan-out, drops
SHARD_DEBUG=trace    + every routed frame — fills fast, short captures only
```

It exists because **sharding is the one change here that can fail silently**, and every
failure mode presents as something else:

| What actually happened | What it looks like | What the log shows |
|---|---|---|
| task routed to the wrong shard | "that camera stopped detecting" | `ROUTE` / `ASSIGN` |
| gallery command routed, not broadcast | "recognition broke on some cameras" | `BROADCAST_PARTIAL` |
| enrolled vector not fanned out | "recognition got slightly worse" | absence of `FANOUT` |
| unbalanced split | "CPU is still pegged" | `BALANCE_SUMMARY peak_over_ideal` |
| a shard died | "three cameras went quiet" | `SPAWN` + `GET /api/system/shards` |

None of those raise an exception. None appear in any other log.

**Start here when a camera goes quiet:** grep `ASSIGN` for its id. Absent means no frame
for that camera ever reached the router — the problem is upstream in `camera_manager`,
not here. Present means check `DROP` for that shard: one hot shard filling its own queue
while the others idle is the signature of a bad split, not a bad camera.

A partial broadcast additionally prints a warning to the console regardless of the flag,
because a gallery that agrees on two shards out of three is not something to discover
from a diagnostic setting nobody switched on.

---

## 6. Known limits

- **No automatic restart of a dead shard.** A dead child takes its cameras down silently
  and the router keeps queueing to a queue nobody reads. This is not a regression — a
  dead child was already a total outage with no restart — and the blast radius drops from
  9 cameras to 3. But it is now *detectable* (`GET /api/system/shards`, `all_alive`)
  where before it was not. Supervision is the obvious next step and is deliberately not
  in this change.
- **VRAM is estimated, not measured.** Structurally favourable, because
  `person._models` is already one YOLO instance per camera, so YOLO memory tracks camera
  count and sharding does not multiply it. What does duplicate: the face model, the ORT
  CUDA arena and the CUDA context. Estimated 5–7 GB for three children against 12
  available. **Measure it; do not trust this number.**
- **GPU contention is unmodelled.** Total GPU work is unchanged, so the 59% utilisation
  should not move, but N processes interleaving CUDA contexts is not free. If throughput
  does not improve as expected at N=3, this is the first thing to suspect.
- **The cost model is a model.** It reproduces the current measurement to within 3%, and
  it is used only to rank cameras. It has not been validated against a different camera
  mix.

---

## 7. Files

| File | Role |
|---|---|
| `backend/services/shards.py` | cost model, `ShardMap`, persistence |
| `backend/services/shard_debug.py` | the diagnostic log |
| `backend/services/perception.py` | N queues, `shard_for`/`shard_of`, `broadcast`, `fanout_enrollments`, `add_vecs` |
| `backend/services/camera_manager.py` | per-shard backpressure |
| `backend/routers/system.py` | `GET /api/system/shards` |
| `backend/routers/cameras.py` | `forget_camera` on delete |
| `start-debug.sh` | `PERCEPTION_SHARDS`, `SHARD_DEBUG` |
| `logs/shard_assignment.json` | persisted camera → shard map |

No migration. No schema change. No new table.
