#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

if pgrep -f "python.*run.py" >/dev/null 2>&1; then
  echo "EmbyPulse is already running."
  echo "Open: http://127.0.0.1:10307/"
  exit 0
fi

echo "Starting EmbyPulse..."
echo "Open: http://127.0.0.1:10307/"

echo "Building Tailwind CSS..."
npm run build:css

python3 run.py