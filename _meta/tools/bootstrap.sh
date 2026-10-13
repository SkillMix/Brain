#!/bin/sh
# Bootstrap (macOS, Linux): find a Python 3.10 or newer, or install one without admin rights,
# then run start.py with every argument given. Needs only a shell and curl or wget.
# uv comes from its official release, pinned and checked against its SHA-256 before use; it
# installs a Python under your home folder.
#
#   sh _meta/tools/bootstrap.sh [start.py arguments ...]
set -eu
HERE=$(cd "$(dirname "$0")" && pwd)
START=${BOOTSTRAP_START:-$HERE/start.py}
TOOLS="$HOME/.spaces/tools"

ok_python() {
  "$1" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' >/dev/null 2>&1
}

for py in python3 python; do
  if command -v "$py" >/dev/null 2>&1 && ok_python "$py"; then
    exec "$py" "$START" "$@"
  fi
done

case "$(uname -s)-$(uname -m)" in
  Linux-x86_64)
    URL=https://github.com/astral-sh/uv/releases/download/0.12.18/uv-x86_64-unknown-linux-gnu.tar.gz
    SHA=89eadd7c76fc063887959510d5ba0ab1264dfd5f1143b925ddb73021a40acf16 ;;
  Linux-aarch64|Linux-arm64)
    URL=https://github.com/astral-sh/uv/releases/download/0.12.18/uv-aarch64-unknown-linux-gnu.tar.gz
    SHA=afb6291f3f0a6b4521fc67b947822506c41dde5b60d2189dd8f3695b2ac8c9e7 ;;
  Darwin-x86_64)
    URL=https://github.com/astral-sh/uv/releases/download/0.12.18/uv-x86_64-apple-darwin.tar.gz
    SHA=2e4108f5395397c8bc5d43bf83d3bdbb2d0e92b90d0efa607756be704905fa33 ;;
  Darwin-arm64)
    URL=https://github.com/astral-sh/uv/releases/download/0.12.18/uv-aarch64-apple-darwin.tar.gz
    SHA=cf40e0c6a202190ccd9e0406dcfdd5b2d6668a9a5c779b17948963df32aafe5b ;;
  *)
    echo "bootstrap: no pinned uv for $(uname -s) $(uname -m): install Python 3.10 or newer, then run start.py" >&2
    exit 1 ;;
esac

UV="$TOOLS/uv/uv"
if [ ! -x "$UV" ]; then
  tmp=$(mktemp -d)
  if command -v curl >/dev/null 2>&1; then
    curl -fsSL "$URL" -o "$tmp/uv.tar.gz"
  elif command -v wget >/dev/null 2>&1; then
    wget -q "$URL" -O "$tmp/uv.tar.gz"
  else
    echo "bootstrap: neither curl nor wget is here: install one (sudo apt install curl), then run this again" >&2
    exit 1
  fi
  if command -v sha256sum >/dev/null 2>&1; then
    got=$(sha256sum "$tmp/uv.tar.gz" | cut -d' ' -f1)
  else
    got=$(shasum -a 256 "$tmp/uv.tar.gz" | cut -d' ' -f1)
  fi
  if [ "$got" != "$SHA" ]; then
    rm -rf "$tmp"
    echo "bootstrap: the uv download did not match its published checksum; nothing was installed" >&2
    exit 1
  fi
  tar -xzf "$tmp/uv.tar.gz" -C "$tmp"
  mkdir -p "$TOOLS/uv"
  cp "$tmp"/uv-*/uv "$tmp"/uv-*/uvx "$TOOLS/uv/"
  chmod 755 "$TOOLS/uv/uv" "$TOOLS/uv/uvx"
  rm -rf "$tmp"
fi
"$UV" python install 3.12 >&2
PY=$("$UV" python find 3.12)
exec "$PY" "$START" "$@"
