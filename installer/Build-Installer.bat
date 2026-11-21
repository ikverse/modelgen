@echo off
rem Builds dist\ModelGen-Setup.exe and its SHA-256 fingerprint (dist\ModelGen-Setup.exe.sha256).
rem What ships: the code, the agents, the studio page, the free model packs (assets_cc0), the icon,
rem config.yaml, ModelGen.bat and ModelGen.vbs, the README and credits, the 3D viewer files the page uses, and the
rem ClaudeIn client. Never shipped: your projects, library, data, car_examples, the before/after
rem pictures, the tests and .venv.
rem Needs: ModelGen's .venv, ..\ClaudeIn\client, Node's three.js in modelgen\web\vendor (Setup-ModelGen.bat)
rem and Inno Setup 6 (winget install JRSoftware.InnoSetup). Pass /nopause to skip the final pause.
setlocal
title ModelGen installer build
cd /d "%~dp0\.."
set "ROOT=%CD%"
set "STAGE=%ROOT%\dist\stage"
set "PY=%ROOT%\.venv\Scripts\python.exe"
set "THREE=modelgen\web\vendor\node_modules\three"

if not exist "%PY%" ( echo ModelGen's .venv is missing. Run Setup-ModelGen.bat first. & goto failed )
if not exist "..\ClaudeIn\client\pyproject.toml" ( echo ..\ClaudeIn\client was not found. & goto failed )
if not exist "%THREE%\build\three.module.js" ( echo The 3D viewer library is missing. Run Setup-ModelGen.bat first. & goto failed )

set "ISCC="
for %%p in ("%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe" "%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe" "%ProgramFiles%\Inno Setup 6\ISCC.exe") do if not defined ISCC if exist %%p set "ISCC=%%~p"
if not defined ISCC ( echo Inno Setup 6 was not found. Install it with: winget install JRSoftware.InnoSetup & goto failed )

rem a relative path here: cmd's for /f cannot run a quoted path that has spaces
for /f "delims=" %%v in ('.venv\Scripts\python.exe -c "import modelgen; print(modelgen.__version__)"') do set "VER=%%v"
if not defined VER ( echo Could not read the version from modelgen\__init__.py. & goto failed )
echo Building ModelGen %VER% ...

if exist "%STAGE%" rmdir /s /q "%STAGE%"
mkdir "%STAGE%" || goto failed

rem robocopy: exit codes below 8 mean success
robocopy modelgen "%STAGE%\modelgen" /E /XD __pycache__ vendor /XF *.pyc /NFL /NDL /NJH /NJS /NP >nul
if errorlevel 8 goto failed
robocopy map "%STAGE%\map" /E /XD __pycache__ /NFL /NDL /NJH /NJS /NP >nul
if errorlevel 8 goto failed
robocopy assets "%STAGE%\assets" /E /NFL /NDL /NJH /NJS /NP >nul
if errorlevel 8 goto failed
robocopy assets_cc0 "%STAGE%\assets_cc0" /E /XD __pycache__ /NFL /NDL /NJH /NJS /NP >nul
if errorlevel 8 goto failed

rem only the viewer files the page imports (instead of the whole 33 MB three.js package)
set "S3=%STAGE%\%THREE%"
for %%d in (build examples\jsm\controls examples\jsm\loaders examples\jsm\exporters examples\jsm\utils) do mkdir "%S3%\%%d"
for %%f in (build\three.module.js examples\jsm\controls\OrbitControls.js examples\jsm\loaders\GLTFLoader.js examples\jsm\exporters\GLTFExporter.js examples\jsm\utils\BufferGeometryUtils.js examples\jsm\utils\TextureUtils.js LICENSE) do (
    copy /y "%THREE%\%%f" "%S3%\%%f" >nul || goto failed
)

for %%f in (config.yaml requirements.txt ModelGen.bat ModelGen.vbs README.md CREDITS.md) do copy /y "%%f" "%STAGE%\%%f" >nul || goto failed
mkdir "%STAGE%\installer\wheels"
copy /y installer\install-deps.ps1 "%STAGE%\installer\install-deps.ps1" >nul || goto failed

echo Packing the ClaudeIn client ...
"%PY%" -m pip wheel --quiet --no-deps --disable-pip-version-check -w "%STAGE%\installer\wheels" ..\ClaudeIn\client
if errorlevel 1 goto failed

echo Compiling the setup ...
"%ISCC%" /Q /DAppVersion=%VER% "/DStageDir=%STAGE%" installer\ModelGen.iss
if errorlevel 1 goto failed

powershell -NoProfile -Command "$h = (Get-FileHash 'dist\ModelGen-Setup.exe' -Algorithm SHA256).Hash.ToLower(); Set-Content -Path 'dist\ModelGen-Setup.exe.sha256' -Value ($h + '  ModelGen-Setup.exe') -Encoding ascii; $mb = [math]::Round((Get-Item 'dist\ModelGen-Setup.exe').Length / 1MB, 1); Write-Host ('dist\ModelGen-Setup.exe: ' + $mb + ' MB, SHA-256 ' + $h)"
if errorlevel 1 goto failed

echo.
echo Done: dist\ModelGen-Setup.exe (ModelGen %VER%)
if /i not "%~1"=="/nopause" pause
exit /b 0

:failed
echo.
echo The build stopped with an error. Read the message above.
if /i not "%~1"=="/nopause" pause
exit /b 1
