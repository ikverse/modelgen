@echo off
rem One-time setup: builds ModelGen's own Python environment in .venv and installs the viewer.
rem Run it again whenever requirements.txt changes. Pass /nopause to skip the final pause.
title ModelGen setup
cd /d "%~dp0"
set "PY=C:\Python314\python.exe"
if not exist "%PY%" set "PY=py -3.14"

if not exist ".venv\Scripts\python.exe" (
    echo Creating the Python environment in .venv ...
    %PY% -m venv .venv
    if errorlevel 1 goto failed
)

echo Installing packages ...
".venv\Scripts\python.exe" -m pip install --upgrade pip --quiet
rem ModelGen asks Claude through ClaudeIn; its small client comes from ClaudeIn's folder next to this one.
if not exist "..\ClaudeIn\client\pyproject.toml" (
    echo ClaudeIn was not found next to this folder ^(..\ClaudeIn^). ModelGen needs it to reach Claude.
    goto failed
)
".venv\Scripts\python.exe" -m pip install -e ..\ClaudeIn\client
if errorlevel 1 goto failed
".venv\Scripts\python.exe" -m pip install -r requirements-dev.txt
if errorlevel 1 goto failed

echo Installing the 3D viewer library (three.js 0.160.0) into modelgen\web\vendor ...
where npm >nul 2>nul
if errorlevel 1 (
    echo npm was not found. Install Node.js, then run this again. The page works without the viewer.
) else (
    call npm install three@0.160.0 --prefix modelgen\web\vendor --no-audit --no-fund --loglevel=error
    if errorlevel 1 goto failed
)

echo.
echo Checking for Blender ...
".venv\Scripts\python.exe" -c "from modelgen import config; p = config.blender_path(); print('Blender found: ' + p if p else 'Blender is not installed yet. Install it with:  winget install --id BlenderFoundation.Blender -e')"

echo.
echo Done. Start ModelGen with ModelGen.bat.
if /i not "%~1"=="/nopause" pause
exit /b 0

:failed
echo.
echo Setup stopped with an error. Read the message above.
if /i not "%~1"=="/nopause" pause
exit /b 1
