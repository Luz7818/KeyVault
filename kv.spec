# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec — 把 kv GUI 打成单个 kv.exe。

构建：pyinstaller kv.spec --clean
产物：dist/kv.exe
"""

a = Analysis(
    ['kv_gui.py'],
    pathex=[],
    binaries=[],
    datas=[],
    hiddenimports=[
        'kv.gui.pages.secrets',
        'kv.gui.pages.detect',
        'kv.gui.pages.capture',
        'kv.gui.pages.ingest',
        'kv.gui.pages.security',
        'kv.gui.pages.settings',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='kv',
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
)
