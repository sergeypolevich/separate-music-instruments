#!/bin/sh
# macOS setup: find Python 3.12 or install it for this user without admin rights (uv, pinned and
# checksum-verified), then run.py creates .venv, installs requirements.txt and starts the app.
set -eu
APP="$(cd "$(dirname "$0")" && pwd)"
UV_VERSION=0.12.22
UV_DIR="$HOME/.local/share/audio-stems/uv-$UV_VERSION"

is_python312() {
  "$1" -c 'import sys; raise SystemExit(sys.version_info[:2] != (3, 12))' 2>/dev/null
}

find_python() {
  for candidate in python3.12 /opt/homebrew/bin/python3.12 /usr/local/bin/python3.12 \
      /Library/Frameworks/Python.framework/Versions/3.12/bin/python3.12; do
    found="$(command -v "$candidate" 2>/dev/null || true)"
    if [ -n "$found" ] && is_python312 "$found"; then echo "$found"; return 0; fi
  done
  if [ -x "$UV_DIR/uv" ]; then "$UV_DIR/uv" python find 3.12 2>/dev/null && return 0; fi
  return 1
}

PYTHON="$(find_python || true)"
if [ -z "$PYTHON" ]; then
  echo "Python 3.12 is missing. Installing it for this user with uv $UV_VERSION..."
  case "$(uname -m)" in
    arm64) ARCH=aarch64; SUM=5d714de09501a59393ceca78f4bc232a50478729640d251907160299b2a93ddd ;;
    x86_64) ARCH=x86_64; SUM=1b8a5b316883df2daf20fb9a446e5b230e01d947d57aba2694977c5ac5a7e98c ;;
    *) echo "This Mac ($(uname -m)) is not supported."; exit 1 ;;
  esac
  TMP="$(mktemp -d)"
  trap 'rm -rf "$TMP"' EXIT
  curl -fsSL -o "$TMP/uv.tar.gz" "https://github.com/astral-sh/uv/releases/download/$UV_VERSION/uv-$ARCH-apple-darwin.tar.gz"
  echo "$SUM  $TMP/uv.tar.gz" | shasum -a 256 -c - >/dev/null || { echo "The uv download does not match its checksum."; exit 1; }
  tar -xzf "$TMP/uv.tar.gz" -C "$TMP"
  mkdir -p "$UV_DIR"
  mv "$TMP/uv-$ARCH-apple-darwin/uv" "$UV_DIR/uv"
  "$UV_DIR/uv" python install 3.12
  PYTHON="$("$UV_DIR/uv" python find 3.12)"
fi

echo "Starting Audio Stems. The first start downloads the application dependencies and models."
cd "$APP"
exec "$PYTHON" run.py --app
