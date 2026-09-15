"""Say, on the Events already analysed, that they were analysed.

BR-E-20 gave `processing_status` its first writer at CP30, and every analysis from then on records
what it did. This is the past: an organization running since CP24 has Events it analysed under code
that never wrote the field, so they all read `received` — which the review surface renders,
accurately and wrongly, as "nothing has read this".

    python ops/dev/backfill_processing_status.py            # report, change nothing
    python ops/dev/backfill_processing_status.py --apply    # write

**Why this is a script and not a migration, which is the interesting part.**

It was written as migration 0016 first. It ran, reported success, and changed nothing — because
`workos_owner` owns every table, every table has FORCE ROW LEVEL SECURITY, and `workos_owner` is
deliberately NOSUPERUSER and NOBYPASSRLS (Checkpoint 3.5). A migration therefore runs with
`app_current_org()` returning NULL and sees no tenant row anywhere. It cannot even list the
organizations to loop over them: `organization` carries the same policy, keyed on its own id.

So **no Alembic migration in this repository can backfill tenant data**, and one has already been
written as though it could — migration 0007's BR-W-19 backfill has exactly this shape. Its
statement is correct and `test_the_migration_backfill_narrows_rows_that_predate_the_rule` proves it,
by executing that statement inside a session that *has* an organization set. What no test covers is
whether Alembic's own session can see a row, and it cannot. Nothing was ever wrong, because 0007
shipped before any deployment held data — but the guarantee it reads as making is not one it can
deliver.

The precedent for what to do instead is `ops/dev/seed.py`, and its reasoning transfers exactly: a
question asked across every tenant is asked from outside the boundary, by the cluster superuser, in
a script somebody runs deliberately — not by the role that owns the schema, whose inability to do
this is the property that makes it safe.

**Backfilled only where it can be proved.** `ai_interaction.input_refs` carries `event_ids`: the
Events a run was handed. An Event named there was read, whatever the run concluded, so `extracted`
is a fact about it rather than an inference. Every other Event keeps `received`, which leaves some
understated — analysed early enough that nothing recorded which Events the run saw. Understating is
the right direction to be wrong in: a false `received` costs somebody a second analysis, while a
false `extracted` says a message was read when nobody knows that it was, which is the failure the
field exists to prevent.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from sqlalchemy import create_engine, text

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))

#: Events some AI run was handed. `?` tests for the key, so an `input_refs` of another shape is
#: skipped rather than raising — the column is deliberately open, and its shape has changed once.
ANALYSED = """
    SELECT DISTINCT (jsonb_array_elements_text(input_refs -> 'event_ids'))::uuid AS event_id
    FROM ai_interaction
    WHERE input_refs ? 'event_ids'
"""

COUNT = f"""
    SELECT count(*) FROM event
    WHERE processing_status = 'received' AND id IN ({ANALYSED})
"""

APPLY = f"""
    UPDATE event SET processing_status = 'extracted'
    WHERE processing_status = 'received' AND id IN ({ANALYSED})
"""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply", action="store_true", help="write the change; otherwise only report"
    )
    parser.add_argument(
        "--database-url",
        default=os.environ.get(
            "WORKOS_SEED_DATABASE_URL",
            "postgresql+psycopg://workos:workos@127.0.0.1:5432/workos",
        ),
    )
    args = parser.parse_args()

    engine = create_engine(args.database_url, future=True)
    with engine.begin() as connection:
        eligible = connection.execute(text(COUNT)).scalar_one()
        # Reported alongside, because "38 of 68" is the number that tells an operator whether to
        # believe the screen afterwards — the remainder stay `received` and some of them were read.
        total = connection.execute(text("SELECT count(*) FROM event")).scalar_one()
        changed = connection.execute(text(APPLY)).rowcount if args.apply else 0

    print(
        json.dumps(
            {
                "events": total,
                "provably_analysed_still_saying_received": eligible,
                "updated": changed,
                "applied": args.apply,
            },
            indent=2,
        )
    )
    if not args.apply:
        print("\nNothing was written. Re-run with --apply.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
