"""What the packaged app runs. (``eaal_platform.launcher`` does the real work.)"""

from eaal_platform.launcher import main

if __name__ == "__main__":
    raise SystemExit(main())
