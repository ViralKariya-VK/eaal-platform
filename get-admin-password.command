#!/usr/bin/env bash
# Double-click me on the SERVER computer (Mac). I print the admin panel's login: a fresh
# password every time (the old one can't be read back, only replaced).
cd "$(dirname "$0")"
echo
if [ -f src/eaal_platform/server/__main__.py ]; then
  [ -f /opt/miniconda3/etc/profile.d/conda.sh ] && source /opt/miniconda3/etc/profile.d/conda.sh && conda activate eaal-platform
  PYTHONPATH="$PWD/src" python -m eaal_platform.server --reset-admin-password
elif [ -x /Applications/CAVY.app/Contents/MacOS/CAVY ]; then
  /Applications/CAVY.app/Contents/MacOS/CAVY --cavy-serve --reset-admin-password
else
  echo "Could not find Python or the CAVY app here."
fi
echo "Open the admin panel at  http://127.0.0.1:8000/admin"
echo "Sign in with the email and password shown above."
echo
read -n 1 -s -r -p "Press any key to close."
