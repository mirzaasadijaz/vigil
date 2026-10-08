"""
Analyse a captured false-detection log directory.

Answers, from the log alone, the two questions that matter:

    what did each parameter actually do?
    and would a different setting have done better?

Implements section 4A.6 of `false-detection-plan.md`. The most important output is the
**per-rule cost table** — it says which filter to move when something is being wrongly
suppressed, instead of guessing one parameter at a time.

Usage (the directory defaults to wherever the logger writes, so it can be omitted):
    python -m backend.scripts.false_detection_report
    python -m backend.scripts.false_detection_report --tracks
    python -m backend.scripts.false_detection_report --config-check
    python -m backend.scripts.false_detection_report --replay
    python -m backend.scripts.false_detection_report --sweep fd_dedup_window

Capture the input with:
    FD_DEBUG=trace bash start.sh
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime

TS_RE = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3})\s+(.*)$")


# ── parsing ───────────────────────────────────────────────────────────────────
def parse_value(raw: str):
    """Inverse of logging.fmt for one value."""
    if raw == "-":
        return None
    if raw.startswith("(") and raw.endswith(")"):
        inner, depth, buf = [], 0, ""
        for ch in raw[1:-1]:
            if ch == "," and depth == 0:
                inner.append(buf)
                buf = ""
                continue
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
            buf += ch
        if buf:
            inner.append(buf)
        return [parse_value(v) for v in inner]
    try:
        return float(raw) if ("." in raw or "e" in raw.lower()) else int(raw)
    except ValueError:
        return raw


def parse_file(path):
    records = []
    try:
        fh = open(path, encoding="utf-8", errors="replace")
    except OSError:
        return records
    with fh:
        for line in fh:
            m = TS_RE.match(line.rstrip("\n"))
            if not m:
                continue
            ts_raw, body = m.groups()
            if "=" not in body:
                continue
            fields = {}
            for token in body.split(" "):
                if "=" not in token:
                    continue
                k, _, v = token.partition("=")
                fields[k] = parse_value(v)
            if "event" not in fields:
                continue
            records.append((datetime.strptime(ts_raw, "%Y-%m-%d %H:%M:%S,%f")
                            .timestamp(), fields))
    return records


def load(directory):
    """Every rule file in the directory, keyed by rule name."""
    from backend.services.false_detection.logging import RULE_FILES

    out = {}
    combined = os.path.join(directory, "fd_all.log")
    if os.path.exists(combined):
        # FD_LOG_SPLIT=false: one file, each line tagged with its rule.
        for ts, f in parse_file(combined):
            out.setdefault(f.pop("log", "decisions"), []).append((ts, f))
        return out
    for rule in RULE_FILES:
        recs = parse_file(os.path.join(directory, f"fd_{rule}.log"))
        if recs:
            out[rule] = recs
    return out


# ── report ────────────────────────────────────────────────────────────────────
def report(data, show_tracks: bool, config_check: bool):
    decisions = [f for _, f in data.get("decisions", [])]
    judged = [f for f in decisions if f.get("event") == "JUDGE"]
    sessions = [f for f in decisions if f.get("event") == "SESSION"]
    metrics = [f for _, f in data.get("metrics", []) if f.get("event") == "METRICS"]
    changes = [f for _, f in data.get("config", []) if f.get("event") == "CONFIG_CHANGE"]

    print("=" * 78)
    print("FALSE-DETECTION REPORT")
    print("=" * 78)

    if not sessions:
        print("\n!! No SESSION header. The capture is truncated or came from an older")
        print("   build — the settings below cannot be verified against what ran.")
    for s in sessions[-1:]:
        print("\n--- configuration in force ---")
        for k, v in s.items():
            if k not in ("event", "component"):
                print(f"    {k:<28} {v}")

    all_recs = [ts for recs in data.values() for ts, _ in recs]
    if all_recs:
        span = max(all_recs) - min(all_recs)
        print("\n--- capture ---")
        print(f"    duration                     {span:.1f} s")
        print(f"    rule files present           {len(data)} "
              f"({', '.join(sorted(data))})")
        print(f"    judgements recorded          {len(judged)}")

    if not judged:
        print("\n!! No JUDGE records. Either the layer was disabled (fd_enabled=0), or")
        print("   no events reached the writer, or FD_DEBUG was not set when it ran.")
        print("   The SESSION header above distinguishes the three.")
        return

    verdicts = Counter(f.get("verdict") for f in judged)
    would = sum(1 for f in judged if f.get("would_suppress") in (1, True))
    mode = (sessions[-1].get("mode") if sessions else judged[-1].get("mode")) or "?"
    print("\n--- outcome ---")
    print(f"    mode                         {mode}")
    for v in ("ACCEPT", "DEMOTE", "SUPPRESS"):
        n = verdicts.get(v, 0)
        print(f"    {v.lower():<28} {n:>6}  ({100 * n / len(judged):5.1f}%)")
    if mode == "observe":
        print(f"    WOULD suppress under enforce {would:>6}  "
              f"({100 * would / len(judged):5.1f}%)")
        print("    ^ this is the number to review before enabling enforcement")

    # ── the tuning table ─────────────────────────────────────────────────────
    by_rule = Counter(f.get("rule") for f in judged
                      if f.get("would_suppress") in (1, True) and f.get("rule"))
    if by_rule:
        print("\n--- what each rule cost ---")
        print(f"    {'rule':<14}{'events':>8}   which parameter to move")
        hints = {
            "dedup": "fd_dedup_window / fd_dedup_scope",
            "ratelimit": "fd_max_alerts_per_minute / fd_storm_threshold",
            "evidence": "fd_min_confidence / fd_min_evidence_frames",
            "zones": "fd_ignore_zones (a zone may be too large)",
            "hours": "fd_ignore_hours",
            "warmup": "fd_warmup_seconds",
            "repeat": "fd_repeat_hits / _window / _min_tracks",
        }
        for rule, n in by_rule.most_common():
            print(f"    {rule:<14}{n:>8}   {hints.get(rule, '')}")
        top, top_n = by_rule.most_common(1)[0]
        if top_n > 0.8 * sum(by_rule.values()):
            print(f"\n    WARNING: '{top}' accounts for "
                  f"{100 * top_n / sum(by_rule.values()):.0f}% of all suppression.")
            print("    One rule doing everything usually means its threshold is wrong,")
            print("    not that the other rules are unnecessary.")

    reasons = Counter(f.get("reason") for f in judged if f.get("reason"))
    if reasons:
        print("\n--- reasons ---")
        for reason, n in reasons.most_common():
            print(f"    {reason:<34}{n:>6}")

    sev = Counter(f.get("severity") for f in judged)
    print("\n--- severity assigned ---")
    for level in ("critical", "high", "medium", "low", "info"):
        if sev.get(level):
            print(f"    {level:<28}{sev[level]:>6}")

    by_cam = defaultdict(Counter)
    for f in judged:
        by_cam[f.get("camera")][f.get("verdict")] += 1
    if len(by_cam) > 1:
        print("\n--- per camera ---")
        print(f"    {'camera':>8}{'judged':>9}{'accepted':>10}{'suppressed':>12}"
              f"{'rate':>8}")
        for cam, c in sorted(by_cam.items(), key=lambda kv: str(kv[0])):
            total = sum(c.values())
            supp = c.get("SUPPRESS", 0)
            print(f"    {str(cam):>8}{total:>9}{c.get('ACCEPT', 0):>10}{supp:>12}"
                  f"{100 * supp / max(1, total):>7.1f}%")

    if metrics:
        print("\n--- last metrics snapshot ---")
        for k, v in metrics[-1].items():
            if k != "event":
                print(f"    {k:<28} {v}")

    if changes:
        print("\n--- configuration changes during the capture ---")
        for c in changes:
            print(f"    {c.get('actor')}: {c.get('changed')} "
                  f"{c.get('before')} -> {c.get('after')} (scope {c.get('scope')})")

    if config_check and sessions:
        _config_check(sessions[-1])

    if show_tracks:
        print("\n--- every suppression ---")
        for f in judged:
            if f.get("verdict") != "ACCEPT":
                print(f"    camera={f.get('camera')} module={f.get('module')} "
                      f"track={f.get('track')} {f.get('verdict')} "
                      f"rule={f.get('rule')} reason={f.get('reason')}")
    print()


def _config_check(session: dict):
    """Compare what ran against what the current environment would produce.

    This catches the "the parameter never actually applied" class of bug — which is what
    let a threshold defect survive two field captures in the phone module."""
    from backend.services.false_detection import FDConfig

    print("\n--- configuration check ---")
    live = FDConfig.from_env().log_fields()
    # Collections cannot be expressed as environment variables, so comparing their
    # counts against an env-derived config always reports a difference that means
    # nothing. They are only settable through the stored configuration.
    ENV_UNSETTABLE = {"fd_ignore_zones", "fd_ignore_hours"}
    drift = []
    for k, expected in live.items():
        if k in ENV_UNSETTABLE:
            continue
        actual = session.get(k)
        if actual is None:
            continue
        if isinstance(expected, bool):
            expected = 1 if expected else 0
        if isinstance(expected, float) and isinstance(actual, (int, float)):
            same = abs(float(expected) - float(actual)) < 1e-6
        else:
            same = str(expected) == str(actual)
        if not same:
            drift.append((k, actual, expected))
    if not drift:
        print("    the capture ran with the same settings this environment produces")
        print("    (zones and hours are excluded - they are not env-settable)")
    else:
        print("    THESE DIFFER between the capture and this environment:")
        for k, was, now in drift:
            print(f"      {k:<30} capture={was}   here={now}")
        print("    If you expected them to match, a setting is not being applied.")


# ── replay and sweep ──────────────────────────────────────────────────────────
def _rebuild(data):
    """Reconstruct the event stream from the JUDGE records, ordered."""
    out = []
    for ts, f in data.get("decisions", []):
        if f.get("event") != "JUDGE":
            continue
        out.append((ts, {
            "module": f.get("module"), "event_type": f.get("event_type"),
            "data": {"track": f.get("track"), "confidence": f.get("confidence")},
        }, f.get("camera")))
    return out


def replay(data, overrides: dict, quiet: bool = False) -> int:
    """Re-run the captured stream through the real engine at different settings.

    Note the limit, and it is a real one: the log records the point-free fields of each
    event, so spatial rules cannot be replayed faithfully. Dedup, rate limiting,
    evidence, warm-up and severity all can."""
    from backend.services.false_detection import FDConfig
    from backend.services.false_detection.engine import FalseDetectionFilter

    stream = _rebuild(data)
    if not stream:
        if not quiet:
            print("Cannot replay: no JUDGE records.")
        return -1

    sessions = [f for _, f in data.get("decisions", []) if f.get("event") == "SESSION"]
    base = {}
    if sessions:
        for k, v in sessions[-1].items():
            if k.startswith("fd_") and not isinstance(v, str):
                base[k] = v
    base["fd_enabled"] = True
    base["fd_mode"] = "enforce"
    base.pop("fd_ignore_zones", None)      # counts, not geometry - cannot be replayed
    base.pop("fd_ignore_hours", None)
    cfg = FDConfig().merged(base).merged(overrides)

    clock = {"t": stream[0][0]}
    f = FalseDetectionFilter(cfg)
    f._now = lambda: clock["t"]
    started = set()
    suppressed = 0
    for ts, event, cam in stream:
        clock["t"] = ts
        if cam not in started:
            # Warm-up cannot be replayed either: the log records the judgement, not the
            # camera's start time. Treat every camera as already warm.
            f.camera_started(cam, ts - 10_000)
            started.add(cam)
        if f.judge(event, cam).verdict != "ACCEPT":
            suppressed += 1
    if not quiet:
        live = sum(1 for _, fld in data.get("decisions", [])
                   if fld.get("event") == "JUDGE"
                   and fld.get("would_suppress") in (1, True))
        print("\n--- replay ---")
        print(f"    events replayed              {len(stream)}")
        print(f"    overrides                    {overrides or 'none'}")
        print(f"    suppressed when captured     {live}")
        print(f"    suppressed on replay         {suppressed}")
        print("    (spatial and warm-up rules are not replayable - see the docstring)")
    return suppressed


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("directory", nargs="?", default=None,
                    help="log directory (default: where the logger writes)")
    ap.add_argument("--tracks", action="store_true", help="list every suppression")
    ap.add_argument("--config-check", action="store_true",
                    help="compare the capture's settings against this environment")
    ap.add_argument("--replay", action="store_true", help="re-run at other settings")
    ap.add_argument("--set", action="append", default=[], metavar="KEY=VALUE")
    ap.add_argument("--sweep", metavar="KEY",
                    help="replay repeatedly, varying KEY")
    ap.add_argument("--sweep-values", default=None,
                    help="comma-separated values for --sweep")
    args = ap.parse_args(argv)

    from backend.services.false_detection.logging import LOG

    directory = args.directory or LOG.directory()
    if not args.directory:
        print(f"reading {directory}")
    if not os.path.isdir(directory):
        print(f"no such directory: {directory}")
        print("hint: run with no argument to read the default location, or capture "
              "first with FD_DEBUG=trace")
        return 2

    data = load(directory)
    if not data:
        print(f"{directory}: no records found (was FD_DEBUG set when it ran?)")
        return 2

    report(data, args.tracks, args.config_check)

    overrides = {}
    for item in args.set:
        k, _, v = item.partition("=")
        overrides[k.strip()] = parse_value(v.strip())

    if args.sweep:
        defaults = {"fd_dedup_window": "0,15,30,60,120",
                    "fd_min_confidence": "0.0,0.2,0.3,0.5",
                    "fd_max_alerts_per_minute": "0,5,10,20,40"}
        raw = args.sweep_values or defaults.get(args.sweep)
        if not raw:
            print(f"--sweep {args.sweep} needs --sweep-values")
            return 2
        print(f"\n--- sweeping {args.sweep} ---")
        print(f"    {args.sweep:>28}{'suppressed':>12}")
        for v in [parse_value(x.strip()) for x in raw.split(",")]:
            n = replay(data, {**overrides, args.sweep: v}, quiet=True)
            print(f"    {str(v):>28}{n:>12}")
        print("    Choose from the shape of this curve, not one value at a time.")
        return 0

    if args.replay:
        replay(data, overrides)
    return 0


if __name__ == "__main__":
    sys.exit(main())
