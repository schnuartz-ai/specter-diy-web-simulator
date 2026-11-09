# Specter DIY web simulator

Browser simulator tooling for Specter DIY. It provides the WebAssembly build,
simulated smartcard/SD-card/USB runtime, browser shell, provenance checks,
and tests used by
[`cryptoadvance/specter-diy`](https://github.com/cryptoadvance/specter-diy).

The firmware repository resolves simulator `main` once per build, checks out
that exact commit in its read-only build jobs, and runs the simulator scripts
from that checkout. The exact Specter source and simulator commits are recorded
in every browser build for provenance.

See [the simulator workflow and release notes](docs/browser-simulator.md) for
the repository contract and migration details.

## Run it locally

You do not need this to try the simulator: the official build is hosted at
<https://cryptoadvance.github.io/specter-diy/>. Build locally to test your own
Specter DIY changes or simulator tooling.

**Requirements**

- Linux or Windows with WSL2 (CI runs Ubuntu; other systems are untested)
- `git`, `python3` (CI uses 3.11)
- Native build tools, as installed by CI:
  `sudo apt-get install build-essential pkg-config libffi-dev libreadline-dev`
- [Emscripten](https://emscripten.org/) **exactly 3.1.74**, installed below
- Node.js 22 and npm, only if you want to run the browser tests
- The first build compiles MicroPython, LVGL and secp256k1 and takes a while

**Steps** (both repositories are cloned side by side):

```sh
# 1. Get the firmware (with submodules) and this repository
git clone --recurse-submodules https://github.com/cryptoadvance/specter-diy.git
git clone https://github.com/cryptoadvance/specter-diy-web-simulator.git

# 2. Install and activate the pinned Emscripten toolchain
git clone https://github.com/emscripten-core/emsdk.git
./emsdk/emsdk install 3.1.74
./emsdk/emsdk activate 3.1.74
source ./emsdk/emsdk_env.sh        # repeat in every new shell
emcc --version                     # must print 3.1.74

# 3. Build the WebAssembly runtime from the firmware checkout
cd specter-diy-web-simulator
SPECTER_SRC="$(realpath ../specter-diy)" bash web/browser/build-browser.sh

# 4. Check the build, then serve the web/ directory
python3 web/browser/verify_build.py
python3 -m http.server 8765 --directory web
```

Open <http://localhost:8765/> in a browser. Chromium is the engine covered by
CI. Serve the page over HTTP as shown: it fetches the build files, so opening
`web/index.html` directly from disk is not supported. Stop the server with
`Ctrl+C`. On WSL2 the Windows browser can normally reach the same `localhost`
address.

The build records which firmware commit and which simulator commit produced it
in `web/builds/<owner>/<repo>/<firmware-sha>/build-info.json`. The simulator
repository and commit are read from this checkout; the firmware repository and
commit come from `SPECTER_SRC`. See
[Build locally](docs/browser-simulator.md#build-locally) for the environment
variables that override this and for running the automated browser tests.

**The build modifies the firmware checkout.** `build-browser.sh` patches C
sources and makefiles under `f469-disco/` (display, SDL, crypto and MicroPython
unix-port files), updates submodules, and writes `browser.manifest.py` inside
`SPECTER_SRC`. Use a dedicated clone of `specter-diy`, not a working copy with
uncommitted changes. To get a pristine tree again, delete that clone and clone
it anew.

**Troubleshooting:** see [Troubleshooting](docs/browser-simulator.md#troubleshooting).

The official browser build and pull-request previews are published by the
firmware repository at <https://cryptoadvance.github.io/specter-diy/>. To
connect a running simulator to wallet software, use the
[`cryptoadvance/specter-virtual-host`](https://github.com/cryptoadvance/specter-virtual-host)
local bridge and its [latest release](https://github.com/cryptoadvance/specter-virtual-host/releases/latest).
