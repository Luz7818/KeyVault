@echo off
REM Build kv.exe (GUI) + kv-cli.exe (CLI) - needs Python 3.12+ and pip.
REM Artifacts land in dist\ (paths referenced by README / docs).
REM KEEP THIS FILE ASCII-ONLY: Chinese text in a UTF-8 bat under chcp 65001
REM makes cmd swallow the head of a line at certain byte offsets (reproduced
REM with a 164-byte script). See packaging/README.md "Do not".

REM Always start from the repo root, wherever this script is launched from.
cd /d "%~dp0.."

chcp 65001 >nul

echo [1/4] Checking PyInstaller...
python -m pip show pyinstaller >nul 2>&1
if errorlevel 1 (
    echo Not found - installing PyInstaller...
    python -m pip install pyinstaller -q
    if errorlevel 1 (
        echo Install failed. Make sure python and pip are on PATH.
        pause
        exit /b 1
    )
)

echo [2/4] Building...
python -m PyInstaller packaging\kv.spec --clean -y --distpath dist --workpath build
if errorlevel 1 (
    echo Build failed - most common cause: dist\kv.exe is still running.
    pause
    exit /b 1
)

echo [3/4] Verifying artifacts (version / init / golden / _wipe / GUI)...
python packaging\verify.py
if errorlevel 1 (
    echo Verification FAILED - do not ship these artifacts.
    pause
    exit /b 1
)

echo [4/4] Done.
echo Artifacts: dist\kv.exe ^(GUI^) + dist\kv-cli.exe ^(CLI^). No Python needed to run.
pause
