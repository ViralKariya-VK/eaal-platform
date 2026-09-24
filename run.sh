#!/usr/bin/env bash
# Starts CAVY. Just run: ./run.sh
set -e

cd "$(dirname "$0")"
source /opt/miniconda3/etc/profile.d/conda.sh
conda activate eaal-platform

# On macOS, launch through the real app bundle instead of a bare `python`
# process — that's the only way Launch Services shows CAVY's actual name
# and icon in the Dock/menu bar instead of "python3.11" with a blank icon
# (see scripts/build_macos_app.py). The bundle's launcher just points back
# at this same interpreter and `-m eaal_platform.app`, so it always runs
# current code — it only needs rebuilding if the icon or interpreter path
# changes, which this script does automatically when the bundle is missing
# or older than the icon it's built from.
if [[ "$OSTYPE" == darwin* ]]; then
  icon="src/eaal_platform/assets/icon.png"
  if [ ! -d "CAVY.app" ] || [ "$icon" -nt "CAVY.app/Contents/Resources/icon.icns" ]; then
    python scripts/build_macos_app.py
  fi
  open CAVY.app
else
  python -m eaal_platform.app
fi
