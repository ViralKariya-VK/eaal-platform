"""Which version of the code this copy of CAVY was built from.

The installer build writes ``_build_info.py`` (not stored in Git) with the
commit and time; running from source has none, so it reports "development".
``CAVY --cavy-version`` prints this, which is how to tell which build is installed.
"""

from __future__ import annotations

VERSION = "0.1.0"

try:
    from eaal_platform._build_info import BUILT_AT, COMMIT
except ImportError:  # running from source
    COMMIT, BUILT_AT = "development", ""


def describe() -> str:
    suffix = f" (commit {COMMIT}" + (f", built {BUILT_AT}" if BUILT_AT else "") + ")"
    return f"CAVY {VERSION}{suffix}"
