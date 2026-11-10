#!/usr/bin/env bash
# Build the Specter DIY browser simulator from source and serve it locally.
#
#   ./run-local.sh                 build (first run: slow) and serve on :8765
#   PORT=9000 ./run-local.sh       serve on another port
#   BUILD_ONLY=1 ./run-local.sh    build and verify, do not start the server
#   SPECTER_REF=<tag|branch|sha> ./run-local.sh   simulate another firmware revision
#
# Everything it downloads goes into ./.browser-work (ignored by git): a
# recursive clone of cryptoadvance/specter-diy and Emscripten 3.1.74. The
# firmware clone there is patched by the build; delete .browser-work to reset.
# Needs Linux with git, python3 and the native build tools below.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORK="$HERE/.browser-work"
FIRMWARE="$WORK/specter-diy"
EMSDK_DIR="$WORK/emsdk"
PORT="${PORT:-8765}"
EMSCRIPTEN_VERSION=3.1.74
APT_PACKAGES="git python3 build-essential pkg-config libffi-dev libreadline-dev"

step() { printf '\n==> %s\n' "$*"; }
die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

step "Checking prerequisites"
case "$(uname -s)" in
  Linux) ;;
  *) die "Linux is required (found $(uname -s))." ;;
esac
missing=()
for tool in git python3 gcc make pkg-config; do
  command -v "$tool" >/dev/null 2>&1 || missing+=("$tool")
done
pkg-config --exists libffi 2>/dev/null || missing+=("libffi-dev")
[[ -f /usr/include/readline/readline.h ]] || missing+=("libreadline-dev")
if ((${#missing[@]})); then
  echo "Missing: ${missing[*]}" >&2
  die "Install the prerequisites, then run this script again:
  sudo apt-get update && sudo apt-get install --yes $APT_PACKAGES"
fi

# The build records which simulator checkout produced it. Check this now so a
# problem shows up in seconds, not after the downloads.
if ! git -C "$HERE" rev-parse HEAD >/dev/null 2>&1; then
  die "$HERE is not a git clone (a ZIP download has no history). Use:
  git clone https://github.com/cryptoadvance/specter-diy-web-simulator.git"
fi
if [[ -z "${SIMULATOR_REPOSITORY:-}" ]]; then
  origin="$(git -C "$HERE" remote get-url origin 2>/dev/null || true)"
  if [[ ! "$origin" =~ github\.com[:/][A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+ ]]; then
    die "Cannot tell the GitHub owner/repo of this clone (origin: '${origin:-none}'). Run:
  SIMULATOR_REPOSITORY=owner/repo ./run-local.sh"
  fi
fi

mkdir -p "$WORK"

step "Firmware source (cryptoadvance/specter-diy)"
if [[ ! -d "$FIRMWARE/.git" ]]; then
  git clone --recurse-submodules https://github.com/cryptoadvance/specter-diy.git "$FIRMWARE"
else
  echo "Using existing $FIRMWARE"
fi
if [[ -n "${SPECTER_REF:-}" ]]; then
  git -C "$FIRMWARE" fetch --tags origin
  git -C "$FIRMWARE" checkout --force "$SPECTER_REF"
  git -C "$FIRMWARE" submodule update --init --recursive
fi

step "Emscripten $EMSCRIPTEN_VERSION"
if [[ ! -f "$EMSDK_DIR/emsdk_env.sh" ]]; then
  git clone https://github.com/emscripten-core/emsdk.git "$EMSDK_DIR"
fi
"$EMSDK_DIR/emsdk" install "$EMSCRIPTEN_VERSION"   # no-op when already installed
"$EMSDK_DIR/emsdk" activate "$EMSCRIPTEN_VERSION"
# shellcheck source=/dev/null
source "$EMSDK_DIR/emsdk_env.sh" >/dev/null

step "Building the WebAssembly simulator (the first build takes a while)"
SPECTER_SRC="$FIRMWARE" bash "$HERE/web/browser/build-browser.sh"

step "Verifying the build"
python3 "$HERE/web/browser/verify_build.py"

[[ "${BUILD_ONLY:-0}" = 1 ]] && { echo "Build finished."; exit 0; }

step "Serving on http://localhost:$PORT/  (Ctrl+C to stop)"
exec python3 -m http.server "$PORT" --directory "$HERE/web"
