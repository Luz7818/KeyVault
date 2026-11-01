# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec — 打出三个 exe：kv.exe（GUI）+ kv-cli.exe（命令行）+ kv-host.exe（浏览器扩展宿主）。

构建：packaging/build.bat（或仓库根执行 pyinstaller packaging/kv.spec --clean）
产物：dist/kv.exe、dist/kv-cli.exe、dist/kv-host.exe（--distpath/--workpath 由 build.bat 写死）

kv-cli 是 copy 派出的擦除 helper 的宿主（frozen 下 spawn_helper 直接调
sys.executable），也补上「init 提示的 kv export」没有命令行入口的缺口。
kv-host 是浏览器扩展的 native messaging 宿主（注册用 `kv host install`），
冻结态兼职 `_wipe` helper 与 `--version`（kv_host.py shim 分发）。
"""

a = Analysis(
    ['../kv_gui.py'],
    pathex=[],
    binaries=[],
    datas=[
        ('icon.png', 'packaging'),
        # 冻结态运行时会按 __file__ 相对路径找这两个：DB 的 schema 与 golden 语料。
        # 漏掉的失败模式是静默的 —— 代码能跑、单测全绿，exe 一 init / selftest
        # 就「文件不存在：_MEIxxxx/kv/core/schema.sql」。tests/test_arch.py 有断言守着。
        ('../kv/core/schema.sql', 'kv/core'),
        ('../tests/corpus', 'tests/corpus'),
    ],
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
    datas=[
        ('../kv/core/schema.sql', 'kv/core'),
        ('../tests/corpus', 'tests/corpus'),
    ],
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

# 原生消息宿主：入口是 kv_host.py shim（stdio 是 native messaging 协议通道），
# console=True——宿主必须继承标准句柄，console=False 的冻结进程 stdout 是 None。
# 必须独立 Analysis（同 CLI 的理由）；datas 带 schema/corpus 是 test_arch 对
# 每个 datas 块的统一断言（宿主日常不需要它们，_wipe 分发路径与将来兜底需要）。
host_a = Analysis(
    ['../kv_host.py'],
    pathex=[],
    binaries=[],
    datas=[
        ('../kv/core/schema.sql', 'kv/core'),
        ('../tests/corpus', 'tests/corpus'),
    ],
    hiddenimports=['kv.cli', 'kv.host.app'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
host_pyz = PYZ(host_a.pure)

host_exe = EXE(
    host_pyz,
    host_a.scripts,
    host_a.binaries,
    host_a.datas,
    [],
    name='kv-host',
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
