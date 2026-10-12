"""Load a college's student lists (name, SAP ID, roll number) into a CAVY database.

    python scripts/import_roster.py data/Students.xlsx --db /path/to/server.db --dry-run
    python scripts/import_roster.py data/Students.xlsx --db /path/to/server.db \
        --credentials passwords.csv

With an official list (SAP ID, email, first name, last name) that is the truth for one programme:

    python scripts/import_roster.py data/Students.xlsx --db /path/to/server.db \
        --directory data/Msc_DS.xlsx --directory-programme "MSc Data Science" \
        --teachers "Viral Kariya,Carol Maria" --derive-emails

The division sheets then only say where each student sits (division, roll number); names, emails and
SAP IDs come from the official list. People named in ``--teachers`` are not loaded as students: they
are added to the approved emails as teachers instead.

Without ``--db`` it uses the classroom server's database in the CAVY data folder. Run with
``--dry-run`` first: it shows what would be created and lists every row that cannot be loaded, and
changes nothing. A real run writes the temporary passwords to ``--credentials`` (a file outside the
repository, handed out once); keep it somewhere private and delete it afterwards.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from eaal_platform.db import approvals, roster
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
    parser.add_argument("--programme", default=None, help="for a sheet whose name doesn't say")
    parser.add_argument("--division", default=None, help="for a sheet whose name doesn't say")
    parser.add_argument(
        "--semester", type=int, default=None, help="for a sheet whose name doesn't say"
    )
    parser.add_argument("--directory", type=Path, default=None, help="the official list of people")
    parser.add_argument(
        "--directory-programme", default=None, help="the programme the official list covers"
    )
    parser.add_argument(
        "--teachers",
        default="",
        help="comma-separated names to approve as teachers instead of loading as students",
    )
    parser.add_argument(
        "--derive-emails",
        action="store_true",
        help="for students with no email, use first.last + last 3 digits of the SAP ID @nmims.in",
    )
    args = parser.parse_args()

    db_path = args.db or server_db_path()
    engine = create_db_engine(db_path)
    init_db(engine)
    factory = create_session_factory(engine)
    if not args.dry_run and roster.load_programmes(factory):
        print("Set up the programmes.")

    groups = roster.read_groups(args.file)
    leftover_problems: list[dict[str, str | int]] = []
    teachers: list[roster.Person] = []
    if args.directory:
        if not args.directory_programme:
            parser.error("--directory needs --directory-programme")
        people = roster.read_directory(args.directory)
        names = [n.strip() for n in args.teachers.split(",") if n.strip()]
        people, teachers = roster.split_out(people, names)
        # The sheets still list them; take them out of the sheets as well.
        taken = {p.sap_id for p in teachers}
        unplaced, merge_problems = roster.merge_directory(
            groups, people + teachers, programme=args.directory_programme
        )
        for group in groups:
            group.rows = [r for r in group.rows if r.sap_id not in taken]
        leftover_problems.extend(merge_problems)
        unplaced = [p for p in unplaced if p.sap_id not in taken]
        reference = next(
            (g for g in groups if g.programme == args.directory_programme and g.semester), None
        )
        if unplaced and reference is not None:
            groups.append(
                roster.Group(
                    "Official list (no division)",
                    reference.programme,
                    None,
                    reference.semester,
                    None,
                    [roster.Row(0, p.sap_id, p.name, None, p.email) for p in unplaced],
                    allow_unplaced=True,
                )
            )
    plan = roster.build_plan(
        factory,
        groups,
        programme=args.programme,
        division=args.division,
        semester=args.semester,
        derive_emails=args.derive_emails,
    )
    plan.problems.extend(leftover_problems)

    print(f"\nDatabase: {db_path}\n")
    for info in plan.groups:
        place = (
            f"{info['programme'] or '?'} · Div {info['division'] or '-'} · "
            f"Sem {info['semester'] or '?'}"
        )
        batch = f"batch of {info['batch_of']}" if info.get("batch_of") else "not loaded"
        print(
            f"  {info['sheet']:<30} {place:<50} {info['students']:>3} students "
            f"(Batch 1: {info['batch_1']}, Batch 2: {info['batch_2']})  {batch}"
        )
    print(f"\n{len(plan.students)} students ready, {len(plan.problems)} row(s) left out.")
    for problem in plan.problems:
        print(
            f"  ! {problem['sheet']} line {problem['line']}: {problem['name']} "
            f"({problem['sap_id']}): {problem['error']}"
        )
    for note in plan.notes:
        print(f"  note: {note}")
    if teachers:
        print(
            f"\nTeachers to approve (not loaded as students): {', '.join(p.name for p in teachers)}"
        )
    if args.dry_run:
        print("\nDry run: nothing was written.")
        return 0

    result = roster.apply_plan(factory, plan)
    print(f"\nCreated {result.created} accounts, updated {result.updated}.")
    for person in teachers:
        if person.email:
            try:
                approvals.add_entry(factory, "professor", name=person.name, email=person.email)
                print(f"Approved teacher email: {person.name} <{person.email}>")
            except approvals.ApprovalError as exc:
                print(f"Could not approve {person.email}: {exc}")
    if result.credentials:
        target = args.credentials or Path("cavy-temporary-passwords.csv")
        target.write_text(roster.credentials_csv(result.credentials), encoding="utf-8")
        print(f"Temporary passwords written to {target.resolve()}")
        print("Hand them out, then delete that file.")
    engine.dispose()
    return 0


if __name__ == "__main__":
    sys.exit(main())
