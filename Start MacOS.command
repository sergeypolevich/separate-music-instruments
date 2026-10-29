#!/bin/sh
# macOS: double-click in Finder. Starts at once while the app's environment matches requirements-macos.lock.
APP="$(cd "$(dirname "$0")/app" && pwd)"
if [ -x "$APP/.venv/bin/python" ] && cmp -s "$APP/requirements-macos.lock" "$APP/.venv/requirements.installed"; then
  cd "$APP" && "$APP/.venv/bin/python" -m audio_stems.desktop
  code=$?
  # Exit code 3 means a dependency is missing: run the full setup, which reinstalls it.
  [ "$code" -eq 3 ] || exit "$code"
  rm -f "$APP/.venv/requirements.installed"
fi
exec sh "$APP/Start-Audio-Stems.sh"
