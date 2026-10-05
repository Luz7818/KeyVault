@echo off
REM 构建 kv.exe — 需要 Python 3.12+ 和 pip
REM 产物在 dist\kv.exe

echo [1/3] 安装 PyInstaller…
python -m pip install pyinstaller -q
if errorlevel 1 (
    echo 安装失败，请确认 python 和 pip 在 PATH 里
    pause
    exit /b 1
)

echo [2/3] 打包…
python -m PyInstaller kv.spec --clean -y
if errorlevel 1 (
    echo 打包失败
    pause
    exit /b 1
)

echo [3/3] 完成
echo 产物：dist\kv.exe
echo 双击即可运行，不需要安装 Python。
pause
