# Building the app

ca-pa is packaged with **PyInstaller** as a **folder bundle on every
platform**: a real `ca-pa.app` on macOS, a `ca-pa/` folder
(`ca-pa.exe` + `_internal/`) on Windows, the same shape on Linux.  The
folder form is deliberate: a one-file PySide6 app would re-extract itself
to a temp directory on every start, and the pre-Qt splash child — which
re-runs the same executable — must not pay that cost.  Because PyInstaller
builds are platform-specific, each bundle must be built on its own
platform (CI does this; a Linux box can only produce the Linux binary).

## Prerequisites

```bash
# a virtualenv with the app and PyInstaller
python3 -m venv .venv
.venv/bin/pip install -e .
.venv/bin/pip install pyinstaller
```

## Icon

The repo ships the icon in three forms: the source PNG, a small 32 px
`.icns` (macOS) and a multi-size `.ico` (Windows); the spec picks the
right one per platform, so the bundles have working icons out of the box.
(The `.ico` is generated from the PNG with Pillow; a crisp Retina `.icns`
can be built the same way, or with `sips`/`iconutil` on a Mac, and
committed in its place.)

## Build

```bash
.venv/bin/pyinstaller --clean --noconfirm ca-pa.spec
```

This produces:

```
dist/ca-pa.app        # on macOS: the standalone .app bundle
dist/ca-pa/           # on Windows/Linux: the folder bundle (run ca-pa[.exe])
build/                # intermediates (safe to delete)
```

Double-click `dist/ca-pa.app` (or `ca-pa\ca-pa.exe`) to run it, or drag
layered TIFF/PSD/PSB files onto the window.

## CI builds (all platforms)

`.github/workflows/build.yml` mirrors the local build in GitHub Actions:
the test suite runs on `macos-latest` **and** `windows-latest` (headless —
`tests/conftest.py` sets the offscreen Qt platform), and the `build` job
runs as a three-leg matrix:

| runner | architecture | artifact / release asset |
|---|---|---|
| `macos-latest` | Apple Silicon (arm64, native) | `ca-pa-macos-arm64.zip` (`ca-pa.app`) |
| `macos-latest` | Intel (x86_64, via Rosetta 2) | `ca-pa-macos-intel.zip` (`ca-pa.app`) |
| `windows-latest` | x64 | `ca-pa-windows-x64.zip` (`ca-pa/` folder) |

The Intel leg runs under Rosetta 2.  It does **not** rely on
`actions/setup-python` for the interpreter — the current runner images no
longer resolve an `x86_64` Python through that action — and instead
downloads a pinned python-build-standalone CPython 3.11 x86_64 tarball,
extracts it, and builds from it.  PyInstaller then bundles exactly that
x86_64 interpreter, yielding a genuine Intel `.app` that also runs on
Apple Silicon via Rosetta.  Keeping both macOS legs on the one
`macos-latest` runner keeps that half of the workflow independent of the
scarce `macos-13` Intel runners.

The Windows leg is plain `actions/setup-python` + `pip` + PyInstaller.
It produces a **folder** bundle (`ca-pa/ca-pa.exe` plus the PySide6/Qt
files) rather than a single executable: a one-file PySide6 app would
re-extract ~200 MB to `%TEMP%` on every start, so the folder form is the
one users should unzip.  The icon is `src/ca_pa/resources/ca-pa_icon.ico`
(generate it from the PNG with Pillow: `img.save("ca-pa_icon.ico",
sizes=[(16,16),(24,24),(32,32),(48,48),(64,64),(128,128),(256,256)])`).

A final `release` job collects the three zips and, on a `v*` tag push,
publishes them together in one GitHub Release (manual runs keep them as
run artifacts instead).  PyInstaller cannot produce a universal2 `.app` on
its own, so two native macOS builds are the reliable way to cover both
chip families.

## Code signing (optional)

An unsigned app triggers Gatekeeper's warning on first launch.  To sign it:

```bash
codesign --force --deep --sign - dist/ca-pa.app     # ad-hoc signature
```

For distribution outside your machine, sign with a registered Apple
certificate and notarize.

## What the bundle contains

The spec bundles:

* the `ca_pa` package and its `resources/` (the icon),
* the `psd-tools` data files and submodules,
* the Pillow and PySide6 binaries PyInstaller discovers automatically.

The window icon is resolved at runtime via `ca_pa.gui._resource()`, which
looks in the package directory and, when frozen, in PyInstaller's `_MEIPASS`
temp directory -- so it works both from source and from the bundle.
