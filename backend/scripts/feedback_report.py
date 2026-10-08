"""
Turn operator thumbs-down into something a person or an agent can act on.

Every alert carries the numbers the decision was made from, in its `data` payload -
`phone_confidence`, `association_confidence`, `body_drift`, `dwell_seconds`, `held`, and
so on. An operator marking an alert WRONG therefore does not just say "that was wrong";
it labels one specific point in that parameter space as a false positive.

This script collects those labelled points and answers the question that actually moves
a threshold:

    for each module, what do the WRONG alerts look like,
    and how do they differ from the RIGHT ones?

That difference is the whole output. A threshold can only be set where the two
populations separate, and guessing at it without this comparison is how the phone size
floor was got wrong twice before it was measured.

USAGE
    python -m backend.scripts.feedback_report                    # summary, all modules
    python -m backend.scripts.feedback_report --module mobile    # one module
    python -m backend.scripts.feedback_report --json out.json    # for an agent
    python -m backend.scripts.feedback_report --days 7

WHAT AN AGENT SHOULD BE GIVEN
    `--json` writes one record per judged alert: module, event type, camera, timestamp,
    the operator's verdict, their note, the snapshot path and the full `data` payload.
    That is a labelled dataset - the note especially, because "that is the printed phone
    on his shirt" is a diagnosis and `conf=0.55` is not.

A CAUTION WORTH READING BEFORE TRUSTING ANY NUMBER HERE
    Feedback is not a random sample. Operators mark what annoys them, so thumbs-down
    arrives far more readily than thumbs-up, and an "accuracy" computed from it is
    biased low. It is useful for FINDING the separation between right and wrong; it is
    not a measurement of how well the system performs. Rows nobody judged are excluded
    entirely rather than counted as correct.
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
from collections import defaultdict


def _rows(days: int | None, module: str | None):
    """Read judged alerts. Sync psycopg2 via the writer's engine, like the other
    report scripts - this is a command-line tool, not part of the async app."""
    from sqlalchemy import create_engine, select
    from sqlalchemy.orm import Session

    from backend.core.config import settings
    # Import the models PACKAGE, not just AlertLog. SQLAlchemy resolves the
    # `relationship("Employee")` string lazily against the registry, so importing the one
    # class leaves the mapper unable to find Employee and every query fails with a
    # configuration error rather than anything that points at the cause.
    # Import EVERY model, not just AlertLog. SQLAlchemy resolves relationship() strings
    # lazily against the registry, so a partial import leaves the mapper unable to find
    # a related class and every query fails with a configuration error that points at
    # the relationship rather than at the missing import.
    # Import the models AlertLog's mapper needs, and only those. SQLAlchemy resolves
    # relationship() strings lazily against the registry, so importing AlertLog alone
    # leaves it unable to find Employee. Importing EVERYTHING is worse: this package
    # also holds models that are not wired into the app (permission, tenant, site) and
    # whose mappers do not configure, so a blanket import fails for an unrelated reason.
    from backend.models import (alert, attendance, camera,  # noqa: F401
                                embedding, employee, user)
    from backend.models.alert import AlertLog

    url = settings.DATABASE_URL.replace("+asyncpg", "").replace("postgresql+psycopg2",
                                                               "postgresql")
    engine = create_engine(url)
    with Session(engine) as db:
        stmt = select(AlertLog).where(AlertLog.feedback.isnot(None))
        if module:
            stmt = stmt.where(AlertLog.module == module)
        stmt = stmt.order_by(AlertLog.feedback_at.desc())
        out = []
        for a in db.execute(stmt).scalars().all():
            try:
                payload = json.loads(a.data) if a.data else {}
            except Exception:
                payload = {}
            out.append({
                "id": a.id,
                "module": a.module,
                "event_type": a.event_type,
                "camera_id": a.camera_id,
                "timestamp": a.timestamp.isoformat() if a.timestamp else None,
                "severity": a.severity,
                "value": a.value,
                "verdict": a.feedback,
                "note": a.feedback_note,
                "snapshot": a.snapshot,
                "data": payload,
            })
    if days:
        from datetime import datetime, timedelta, timezone
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        out = [r for r in out
               if r["timestamp"] and r["timestamp"] >= cutoff.isoformat()]
    return out


def _numeric_fields(rows) -> list:
    """Payload keys that are numeric on enough rows to be worth comparing."""
    seen = defaultdict(int)
    for r in rows:
        for k, v in (r["data"] or {}).items():
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                seen[k] += 1
    return [k for k, n in sorted(seen.items(), key=lambda kv: -kv[1])
            if n >= max(2, len(rows) * 0.3)]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--module", default=None)
    ap.add_argument("--days", type=int, default=None)
    ap.add_argument("--json", dest="json_out", default=None,
                    help="write the labelled records here, for an agent to consume")
    ap.add_argument("--notes", action="store_true",
                    help="print every thumbs-down note in full")
    args = ap.parse_args()

    try:
        rows = _rows(args.days, args.module)
    except Exception as e:
        print(f"could not read the database: {e}")
        print("Run from the repo root with the app's environment configured.")
        return 2

    if not rows:
        print("No judged alerts yet.")
        print("Feedback is collected from the thumbs up / thumbs down control on the")
        print("Alerts screen. Nothing here means nobody has used it, which is different")
        print("from everything being correct.")
        return 0

    up = [r for r in rows if r["verdict"] == "up"]
    down = [r for r in rows if r["verdict"] == "down"]
    print("=" * 78)
    print("OPERATOR FEEDBACK")
    print("=" * 78)
    print(f"  judged {len(rows)}   correct {len(up)}   WRONG {len(down)}")
    print()
    print("  Not a random sample: operators mark what annoys them, so thumbs-down")
    print("  arrives more readily than thumbs-up. Use this to find WHERE right and")
    print("  wrong separate, not as a measurement of accuracy.")
    print()

    # ── per event type ───────────────────────────────────────────────────────
    per = defaultdict(lambda: {"up": 0, "down": 0})
    for r in rows:
        per[(r["module"] or "?", r["event_type"] or "-")][r["verdict"]] += 1
    print("-" * 78)
    print(f"  {'module':<14}{'event':<18}{'right':>7}{'WRONG':>7}{'judged':>8}")
    print("-" * 78)
    for (mod, ev), c in sorted(per.items(), key=lambda kv: -kv[1]["down"]):
        n = c["up"] + c["down"]
        print(f"  {mod:<14}{ev:<18}{c['up']:>7}{c['down']:>7}{n:>8}")
    print()

    # ── the comparison that sets a threshold ─────────────────────────────────
    for mod in sorted({r["module"] for r in rows if r["module"]}):
        m_up = [r for r in up if r["module"] == mod]
        m_down = [r for r in down if r["module"] == mod]
        if not m_down:
            continue
        fields = _numeric_fields([r for r in rows if r["module"] == mod])
        if not fields:
            continue
        print("-" * 78)
        print(f"  {mod}: what separates WRONG from RIGHT")
        print("-" * 78)
        if not m_up:
            print("  No thumbs-UP yet for this module, so there is nothing to compare")
            print("  against. A threshold set from the wrong population alone will cut")
            print("  genuine alerts too. Mark a few correct ones.")
            print()
            continue
        print(f"  {'field':<24}{'WRONG median':>14}{'right median':>14}   separation")
        for f in fields:
            dv = [r["data"][f] for r in m_down if isinstance(r["data"].get(f), (int, float))]
            uv = [r["data"][f] for r in m_up if isinstance(r["data"].get(f), (int, float))]
            if len(dv) < 2 or len(uv) < 2:
                continue
            md, mu = statistics.median(dv), statistics.median(uv)
            # Do the ranges actually part company, or do they sit on top of each other?
            gap = ("WRONG lower" if max(dv) < min(uv) else
                   "WRONG higher" if min(dv) > max(uv) else "overlap - no clean cut")
            print(f"  {f:<24}{md:>14.4f}{mu:>14.4f}   {gap}")
        print()

    # ── the notes, which are the highest-value field ─────────────────────────
    noted = [r for r in down if r["note"]]
    if noted:
        print("-" * 78)
        print(f"  Why operators said it was wrong ({len(noted)} of {len(down)} explained)")
        print("-" * 78)
        for r in (noted if args.notes else noted[:10]):
            print(f"  [{r['module']}/{r['event_type']}] {r['note']}")
        if not args.notes and len(noted) > 10:
            print(f"  ... {len(noted) - 10} more; pass --notes for all")
        print()

    if args.json_out:
        with open(args.json_out, "w", encoding="utf-8") as fh:
            json.dump({"judged": len(rows), "records": rows}, fh, indent=2)
        print(f"  labelled records -> {args.json_out}")
        print("  One record per judged alert, with the full diagnostic payload the")
        print("  decision was made from. That is what an agent should be given.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
