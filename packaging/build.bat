@echo off
chcp 65001 >nul
REM 构建 kv.exe — 需要 Python 3.12+ 和 pip
REM 产物固定在仓库根 dist\kv.exe（README/目录说明引用的约定路径）

REM 无论从哪里双击，都先回到仓库根，产物与缓存路径才不漂移
cd /d "%~dp0.."

echo [1/3] 检查 PyInstaller…
python -m pip show pyinstaller >nul 2>&1
if errorlevel 1 (
    echo 未安装，正在安装 PyInstaller…
    python -m pip install pyinstaller -q
    if errorlevel 1 (
        echo 安装失败，请确认 python 和 pip 在 PATH 里
        pause
        exit /b 1
    )
)

echo [2/3] 打包…
python -m PyInstaller packaging\kv.spec --clean -y --distpath dist --workpath build
if errorlevel 1 (
    echo 打包失败——最常见原因是 dist\kv.exe 正在运行，请先关闭它再重试
    pause
    exit /b 1
)

echo [3/3] 完成
echo 产物：dist\kv.exe
echo 双击即可运行，不需要安装 Python。
pause
