# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller specification for the ca-pa desktop app.

Build (on the matching platform; CI builds macOS and Windows)::

    pyinstaller --clean ca-pa.spec

On macOS this produces ``dist/ca-pa.app`` (a real ``.app`` bundle with the
ca-pa icon); on Windows, ``dist/ca-pa/ca-pa.exe`` (a folder bundle with the
ca-pa icon); the resources (icons) are bundled either way.
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

# One single executable on macOS (wrapped in a .app) and Linux; a folder
# bundle on Windows, where a one-file PySide6 app would re-extract itself to
# %TEMP% on every start.
ONEFILE = sys.platform != "win32"

exe = EXE(
    pyz,
    a.scripts,
    a.binaries if ONEFILE else [],
    a.zipfiles if ONEFILE else [],
    a.datas if ONEFILE else [],
    [],
    name="ca-pa",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    runtime_tmpdir=None,
    console=False,  # windowed app: no terminal window
    disable_windowed_traceback=False,
    exclude_binaries=not ONEFILE,
    icon=_icon_path(),
)

if sys.platform == "darwin":
    # Wrap the executable in a real .app bundle.
    app = BUNDLE(
        exe,
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
elif sys.platform == "win32":
    # dist/ca-pa/ca-pa.exe + the PySide6/Qt files in the same folder.
    coll = COLLECT(
        exe,
        a.binaries,
        a.datas,
        strip=False,
        upx=False,
        name="ca-pa",
    )
