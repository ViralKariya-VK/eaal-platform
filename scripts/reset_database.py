"""Clear a CAVY database for a fresh start: labs, students, professors, sessions, everything.

    python scripts/reset_database.py --db "<data folder>/server.db"        # shows what would go
    python scripts/reset_database.py --db "<data folder>/server.db" --yes  # backs up, then clears

What stays: the administrators, the mail settings and the app settings (lab-mode switch...). A
backup of the whole file is made first, next to it in a ``backups`` folder. Stop the server before
running this and start it again afterwards; the six programmes are set up again so the next import
can go straight in.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from sqlalchemy import text

from eaal_platform.db import roster
from eaal_platform.db.engine import create_db_engine, create_session_factory, init_db
from eaal_platform.db.maintenance import KEEP_TABLES, backup_database, wipe_data
from eaal_platform.db.models import Base
from eaal_platform.server.host import server_db_path


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument(
        "--db", type=Path, default=None, help="database file (default: the server's)"
    )
    parser.add_argument(
        "--yes", action="store_true", help="really do it (otherwise only show what would go)"
    )
    args = parser.parse_args()

    db_path = args.db or server_db_path()
    if not db_path.exists():
        print(f"No database at {db_path}")
        return 1
    engine = create_db_engine(db_path)
    init_db(engine)
    print(f"Database: {db_path}\n")
    with engine.connect() as connection:
        counts = {
            t.name: connection.execute(text(f"SELECT COUNT(*) FROM {t.name}")).scalar_one()  # nosec B608
            for t in Base.metadata.sorted_tables
        }
    for name, count in counts.items():
        if count:
            fate = "(kept)" if name in KEEP_TABLES else "to be removed"
            print(f"  {name:<24} {count:>6} rows  {fate}")
    if not args.yes:
        print("\nNothing was changed. Add --yes to back up and clear.")
        return 0

    backup = backup_database(db_path)
    print(f"\nBackup written to {backup}")
    removed = wipe_data(engine)
    print(f"Removed rows from {len(removed)} tables.")
    added = roster.load_programmes(create_session_factory(engine))
    print(f"Set up {added} programmes. Done.")
    engine.dispose()
    return 0


if __name__ == "__main__":
    sys.exit(main())
