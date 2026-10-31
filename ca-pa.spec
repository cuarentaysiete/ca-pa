# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller specification for the ca-pa desktop app.

Build (on macOS)::

    pyinstaller --clean ca-pa.spec

Produces ``dist/ca-pa.app``.  The resources (icon) are bundled; on macOS the
result is a real ``.app`` bundle with the ca-pa icon.
"""

import os
import sys

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

HERE = os.path.dirname(os.path.abspath(SPEC))  # noqa: F821 (provided by PyInstaller)
SRC = os.path.join(HERE, "src")
RESOURCES = os.path.join(SRC, "ca_pa", "resources")

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

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="ca-pa",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    runtime_tmpdir=None,
    console=False,  # windowed app: no terminal window
    disable_windowed_traceback=False,
    icon=os.path.join(RESOURCES, "ca-pa_icon.icns")
    if os.path.exists(os.path.join(RESOURCES, "ca-pa_icon.icns"))
    else os.path.join(RESOURCES, "ca-pa_icon.png"),
)

# On macOS wrap the executable in a .app bundle.
if sys.platform == "darwin":
    app = BUNDLE(
        exe,
        name="ca-pa.app",
        icon=os.path.join(RESOURCES, "ca-pa_icon.icns")
        if os.path.exists(os.path.join(RESOURCES, "ca-pa_icon.icns"))
        else os.path.join(RESOURCES, "ca-pa_icon.png"),
        bundle_identifier="ai.cuarentaysiete.ca-pa",
        info_plist={
            "CFBundleName": "ca-pa",
            "CFBundleDisplayName": "ca-pa",
            "CFBundleShortVersionString": "0.1.0",
            "NSHighResolutionCapable": True,
        },
    )
