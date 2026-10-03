#!/usr/bin/env bash
# Starts the CAVY server (and its admin panel). Run: scripts/run-server.sh
set -e
cd "$(dirname "$0")/.."  # the project root
source /opt/miniconda3/etc/profile.d/conda.sh
conda activate eaal-platform
python -m eaal_platform.server "$@"
