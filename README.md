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

You need Linux (CI runs Ubuntu; the script was tested on Ubuntu 24.04). Other
systems are untested, and the script stops with a message on anything but Linux.

Install the prerequisites once (Debian/Ubuntu commands; skip this if `git`,
`python3` and the build tools are already installed; it asks for your Linux
password; on other distributions install the equivalent packages):

```sh
sudo apt-get update && sudo apt-get install --yes git python3 build-essential pkg-config libffi-dev libreadline-dev
```

Then build and start the simulator:

```sh
git clone https://github.com/cryptoadvance/specter-diy-web-simulator.git
cd specter-diy-web-simulator && ./run-local.sh
```

When the script prints `Serving on http://localhost:8765/`, open that address in
a browser (Chromium is the engine covered by CI). Stop it with `Ctrl+C`; run
`./run-local.sh` again to restart.

`run-local.sh` downloads over 300 MB (the pinned Emscripten 3.1.74 plus the
firmware with its submodules), all into `.browser-work/` (ignored by Git), builds
the WebAssembly runtime, verifies it, and serves `web/`. Later runs reuse the
downloads and only rebuild. Options are environment variables:

| Variable | Effect |
| --- | --- |
| `PORT=9000` | serve on another port |
| `BUILD_ONLY=1` | build and verify, do not start the server |
| `SPECTER_REF=<tag, branch or sha>` | simulate that firmware revision instead of the default branch |

Do not open `web/index.html` directly from disk: the page fetches the build
files over HTTP.

<details>
<summary>Manual steps without the script</summary>

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

</details>

The build records which firmware commit and which simulator commit produced it
in `web/builds/<owner>/<repo>/<firmware-sha>/build-info.json`. The simulator
repository and commit are read from this checkout; the firmware repository and
commit come from `SPECTER_SRC` (`.browser-work/specter-diy` when using `run-local.sh`). See
[Build locally](docs/browser-simulator.md#build-locally) for the environment
variables that override this and for running the automated browser tests.

**The build modifies the firmware checkout.** `build-browser.sh` patches C
sources and makefiles under `f469-disco/` (display, SDL, crypto and MicroPython
unix-port files), updates submodules, and writes `browser.manifest.py` inside
`SPECTER_SRC`. Use a dedicated clone of `specter-diy`, not a working copy with
uncommitted changes. `run-local.sh` uses its own clone in `.browser-work/`;
delete that folder to start from scratch.

**Troubleshooting:** see [Troubleshooting](docs/browser-simulator.md#troubleshooting).

The official browser build and pull-request previews are published by the
firmware repository at <https://cryptoadvance.github.io/specter-diy/>. To
connect a running simulator to wallet software, use the
[`cryptoadvance/specter-virtual-host`](https://github.com/cryptoadvance/specter-virtual-host)
local bridge and its [latest release](https://github.com/cryptoadvance/specter-virtual-host/releases/latest).
