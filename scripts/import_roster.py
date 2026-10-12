"""Load a college's student lists (name, SAP ID, roll number) into a CAVY database.

    python scripts/import_roster.py data/Students.xlsx --db /path/to/server.db --dry-run
    python scripts/import_roster.py data/Students.xlsx --db /path/to/server.db \
        --credentials passwords.csv

Without ``--db`` it uses the classroom server's database in the CAVY data folder. Run with
``--dry-run`` first: it shows what would be created and lists every row that cannot be loaded, and
changes nothing. A real run writes the temporary passwords to ``--credentials`` (a file outside the
repository, handed out once); keep it somewhere private and delete it afterwards.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from eaal_platform.db import roster
from eaal_platform.db.engine import create_db_engine, create_session_factory, init_db
from eaal_platform.server.host import server_db_path


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument("file", type=Path, help="the .xlsx with one sheet per division")
    parser.add_argument(
        "--db", type=Path, default=None, help="database file (default: the server's)"
    )
    parser.add_argument("--dry-run", action="store_true", help="show the plan, change nothing")
    parser.add_argument(
        "--credentials", type=Path, default=None, help="where to write the temporary passwords"
    )
    parser.add_argument(
        "--programme",
        default=None,
        help="for a sheet whose name doesn't say, e.g. 'MSc Data Science'",
    )
    parser.add_argument(
        "--division", default=None, help="for a sheet whose name doesn't say, e.g. A"
    )
    parser.add_argument(
        "--semester", type=int, default=None, help="for a sheet whose name doesn't say, e.g. 3"
    )
    args = parser.parse_args()

    db_path = args.db or server_db_path()
    engine = create_db_engine(db_path)
    init_db(engine)
    factory = create_session_factory(engine)
    added = 0 if args.dry_run else roster.load_programmes(factory)
    if added:
        print(f"Set up {added} programme(s).")
    groups = roster.read_groups(args.file)
    plan = roster.build_plan(
        factory, groups, programme=args.programme, division=args.division, semester=args.semester
    )

    print(f"\nDatabase: {db_path}\n")
    for info in plan.groups:
        place = (
            f"{info['programme'] or '?'} · Div {info['division'] or '?'} · "
            f"Sem {info['semester'] or '?'}"
        )
        batch = f"batch of {info['batch_of']}" if info.get("batch_of") else "not loaded"
        print(
            f"  {info['sheet']:<28} {place:<52} {info['students']:>3} students "
            f"(Batch 1: {info['batch_1']}, Batch 2: {info['batch_2']})  {batch}"
        )
    print(f"\n{len(plan.students)} students ready, {len(plan.problems)} row(s) left out.")
    for problem in plan.problems:
        print(
            f"  ! {problem['sheet']} line {problem['line']}: {problem['name']} "
            f"({problem['sap_id']}): {problem['error']}"
        )
    if args.dry_run:
        print("\nDry run: nothing was written.")
        return 0

    result = roster.apply_plan(factory, plan)
    print(f"\nCreated {result.created} accounts, updated {result.updated}.")
    if result.credentials:
        target = args.credentials or Path("cavy-temporary-passwords.csv")
        target.write_text(roster.credentials_csv(result.credentials), encoding="utf-8")
        print(f"Temporary passwords written to {target.resolve()}")
        print("Hand them out, then delete that file.")
    engine.dispose()
    return 0


if __name__ == "__main__":
    sys.exit(main())
