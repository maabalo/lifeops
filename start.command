#!/bin/sh
# LifeOps for macOS (and Linux): double-click this file (Linux: `sh start.command`) to start LifeOps.
# The first run sets everything up (about a minute); after that it starts in seconds.
# Close this window (or press Ctrl+C) to stop LifeOps. Your data stays in the "data" folder.
cd "$(dirname "$0")" || exit 1

if [ ! -x .venv/bin/python ]; then
  if ! python3 -c 'import sys; sys.exit(sys.version_info < (3, 12))' 2>/dev/null; then
    echo "LifeOps needs Python 3.12 or newer: https://www.python.org/downloads/ - then run this again."
    exit 1
  fi
  echo "Setting up LifeOps for the first time (about a minute)..."
  if ! { python3 -m venv .venv && .venv/bin/python -m pip install --disable-pip-version-check --quiet -r requirements.lock; }; then
    echo "Setup didn't finish. Check the internet connection, then run this again."
    rm -rf .venv
    exit 1
  fi
fi

echo
echo "LifeOps is starting at http://127.0.0.1:8780"
echo "Close this window (or press Ctrl+C) to stop it."
echo
# Open the browser a few seconds from now, once the server is up
( sleep 3; if command -v open >/dev/null 2>&1; then open http://127.0.0.1:8780; else xdg-open http://127.0.0.1:8780; fi ) >/dev/null 2>&1 &
exec .venv/bin/python -m uvicorn --factory app.main:create_app --host 127.0.0.1 --port 8780
