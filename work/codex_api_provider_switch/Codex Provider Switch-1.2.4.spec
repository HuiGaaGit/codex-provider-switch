# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ["codex_api_provider_switch.py"],
    pathex=[],
    binaries=[],
    datas=[("assets", "assets")],
    hiddenimports=[
        "provider_switch.qt_ui",
        "provider_switch.ui",
        "PySide6.QtCore",
        "PySide6.QtGui",
        "PySide6.QtNetwork",
        "PySide6.QtWidgets",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
# A Poppler runtime on PATH exposes ICU 78 under generic DLL names. Qt on
# Windows targets the system ICU forwarder; bundling those foreign DLLs makes
# QtCore fail with an entry-point error on startup.
a.binaries = [
    item for item in a.binaries
    if item[0].casefold() not in {"icuuc.dll", "icudt78.dll"}
]
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="Codex Provider Switch-1.2.4",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=["assets\\codex_api_provider_switch_icon.ico"],
    version="assets\\version_info-1.2.4.txt",
)



