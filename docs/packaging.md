# Building the macOS app

ca-pa is packaged with **PyInstaller** into a standalone `ca-pa.app` bundle.
Because PyInstaller builds are platform-specific, the build must be done on
**macOS** (this repo's CI/container is Linux and can produce a Linux binary,
but not a `.app`).

## Prerequisites

```bash
# a virtualenv with the app and PyInstaller
python3 -m venv .venv
.venv/bin/pip install -e .
.venv/bin/pip install pyinstaller
```

## Icon

The repo ships a small 32 px `ca-pa_icon.icns`, so the bundle already has a
working icon.  For a crisp Retina icon (16–1024 px), regenerate it from the
high-resolution PNG first:

```bash
scripts/make_icon.sh      # needs sips + iconutil (stock on macOS)
```

Skip this if the 32 px icon is good enough.

## Build

```bash
.venv/bin/pyinstaller --clean --noconfirm ca-pa.spec
```

This produces:

```
dist/ca-pa.app        # the standalone app
build/                # intermediates (safe to delete)
```

Double-click `dist/ca-pa.app` to run it, or drag layered TIFF/PSD/PSB files
onto the window.

## CI builds (both architectures)

`.github/workflows/build.yml` mirrors the local build in GitHub Actions:
the test suite runs once on `macos-latest`, and the `build` job runs as a
matrix (currently a single leg):

| runner | architecture | artifact / release asset |
|---|---|---|
| `macos-latest` | Apple Silicon (arm64, native) | `ca-pa-macos-arm64.zip` |
| `macos-latest` | Intel (x86_64, via Rosetta 2) | `ca-pa-macos-intel.zip` |

The Intel leg runs under Rosetta 2.  It does **not** rely on
`actions/setup-python` for the interpreter — the current runner images no
longer resolve an `x86_64` Python through that action — and instead
downloads a pinned python-build-standalone CPython 3.11 x86_64 tarball,
extracts it, and builds the venv from it.  PyInstaller then bundles
exactly that x86_64 interpreter, yielding a genuine Intel `.app` that also
runs on Apple Silicon via Rosetta.  Keeping both legs on the one
`macos-latest` runner also keeps the workflow independent of the scarce
`macos-13` Intel runners.

A final `release` job collects both zips and, on a `v*` tag push, publishes
them together in one GitHub Release (manual runs keep them as run
artifacts instead).  PyInstaller cannot produce a universal2 `.app` on its
own, so two native builds are the reliable way to cover both families.

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
