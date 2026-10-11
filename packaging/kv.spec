# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec — 打出两个 exe：kv.exe（GUI）+ kv-cli.exe（命令行）。

构建：packaging/build.bat（或仓库根执行 pyinstaller packaging/kv.spec --clean）
产物：dist/kv.exe、dist/kv-cli.exe（--distpath/--workpath 由 build.bat 写死）

kv-cli 是 copy 派出的擦除 helper 的宿主（frozen 下 spawn_helper 直接调
sys.executable），也补上「init 提示的 kv export」没有命令行入口的缺口。
"""

a = Analysis(
    ['../kv_gui.py'],
    pathex=[],
    binaries=[],
    datas=[('icon.png', 'packaging')],
    hiddenimports=[
        'kv.cli',
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
    icon='icon.ico',
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

# CLI 版：入口是 kv.py shim（main() 读 sys.argv），console=True。
# 必须独立 Analysis —— 复用 GUI 的 a.scripts 会让 kv-cli.exe 启动 GUI。
cli_a = Analysis(
    ['../kv.py'],
    pathex=[],
    binaries=[],
    datas=[],
    hiddenimports=['kv.cli'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
cli_pyz = PYZ(cli_a.pure)

cli_exe = EXE(
    cli_pyz,
    cli_a.scripts,
    cli_a.binaries,
    cli_a.datas,
    [],
    name='kv-cli',
    icon='icon.ico',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
