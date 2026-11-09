# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller specification for the ca-pa desktop app.

Build (on the matching platform; CI builds macOS and Windows)::

    pyinstaller --clean ca-pa.spec

A folder bundle on every platform: ``dist/ca-pa.app`` on macOS (a real
``.app`` bundle with the ca-pa icon), ``dist/ca-pa/ca-pa.exe`` on Windows,
``dist/ca-pa/ca-pa`` elsewhere; the resources (icons) are bundled either
way.  Folder bundles (not single files) on purpose: a one-file PySide6 app
re-extracts itself to a temp directory on every start, and the pre-Qt
splash child (which re-runs the same executable) must not pay that cost.
"""

import os
import sys

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

HERE = os.path.dirname(os.path.abspath(SPEC))  # noqa: F821 (provided by PyInstaller)
SRC = os.path.join(HERE, "src")
RESOURCES = os.path.join(SRC, "ca_pa", "resources")


def _icon_path():
    """The platform-appropriate application icon (PyInstaller is picky)."""
    if sys.platform == "darwin":
        for name in ("ca-pa_icon.icns", "ca-pa_icon.png"):
            path = os.path.join(RESOURCES, name)
            if os.path.exists(path):
                return path
    elif sys.platform == "win32":
        for name in ("ca-pa_icon.ico",):
            path = os.path.join(RESOURCES, name)
            if os.path.exists(path):
                return path
    path = os.path.join(RESOURCES, "ca-pa_icon.png")
    return path if os.path.exists(path) else None


def _version():
    """The app version, read from the package (single source of truth)."""
    try:
        with open(os.path.join(SRC, "ca_pa", "__init__.py"), encoding="utf-8") as fh:
            for line in fh:
                if line.startswith("__version__"):
                    return line.split("=")[1].strip().strip('"')
    except OSError:
        pass
    return "0.0.0"

# psd-tools ships data files and a plugin-ish import graph; pull it all in.
psd_data = collect_data_files("psd_tools")
psd_modules = collect_submodules("psd_tools")

hiddenimports = [
    "ca_pa",
    "ca_pa.bootstrap",
    "ca_pa.gui",
    "ca_pa.parser",
    "ca_pa.psd_parser",
    "ca_pa.tiff_parser",
    "ca_pa.model",
    "ca_pa.labels",
    "ca_pa.errors",
    "ca_pa.run_app",
    "PIL",
    "PIL.Image",
] + psd_modules

block_cipher = None

a = Analysis(
    [os.path.join(SRC, "ca_pa", "run_app.py")],
    pathex=[SRC],
    binaries=[],
    datas=[(RESOURCES, os.path.join("ca_pa", "resources"))] + psd_data,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

# Folder bundle on every platform (a one-file PySide6 app would re-extract
# itself to a temp dir on every start — and the pre-Qt splash child, which
# re-runs the same executable, must not pay that cost either).
exe = EXE(
    pyz,
    a.scripts,
    [],
    [],
    name="ca-pa",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    runtime_tmpdir=None,
    console=False,  # windowed app: no terminal window
    disable_windowed_traceback=False,
    exclude_binaries=True,
    icon=_icon_path(),
)

if sys.platform == "darwin":
    # A real .app bundle: Contents/MacOS/ca-pa + Frameworks + Resources.
    app = BUNDLE(
        exe,
        a.binaries,
        a.zipfiles,
        a.datas,
        name="ca-pa.app",
        icon=_icon_path(),
        bundle_identifier="ai.cuarentaysiete.ca-pa",
        info_plist={
            "CFBundleName": "ca-pa",
            "CFBundleDisplayName": "ca-pa",
            "CFBundleShortVersionString": _version(),
            "CFBundleVersion": _version(),
            "NSHighResolutionCapable": True,
        },
    )
else:
    # dist/ca-pa/ca-pa.exe (Windows) or dist/ca-pa/ca-pa (Linux) + files.
    coll = COLLECT(
        exe,
        a.binaries,
        a.datas,
        strip=False,
        upx=False,
        name="ca-pa",
    )
