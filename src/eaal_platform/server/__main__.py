"""Run the CAVY server: ``python -m eaal_platform.server``."""

from __future__ import annotations

import argparse
import os
from pathlib import Path

import uvicorn

from eaal_platform.server.app import create_app
from eaal_platform.server.host import build_state, lan_addresses, server_db_path


def main() -> int:
    parser = argparse.ArgumentParser(description="CAVY central server")
    parser.add_argument("--host", default="0.0.0.0", help="address to listen on (default: all)")  # nosec B104 - LAN server
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument(
        "--db",
        type=Path,
        default=None,
        help="database file (default: server.db in the CAVY data folder)",
    )
    args = parser.parse_args()
    env_db = os.environ.get("EAAL_DB_PATH")
    db_path = args.db or (Path(env_db) if env_db else server_db_path())
    shown = lan_addresses() if args.host in ("0.0.0.0", "::") else [args.host]  # nosec B104
    print("\nCAVY server starting.", flush=True)
    for ip in shown or ["this-computer"]:
        print(f"  Admin panel (open in a browser):  http://{ip}:{args.port}/admin", flush=True)
        print(f"  Type this into CAVY on other PCs: {ip}:{args.port}", flush=True)
    print("  Stop with Ctrl+C.\n", flush=True)
    uvicorn.run(create_app(build_state(db_path)), host=args.host, port=args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
