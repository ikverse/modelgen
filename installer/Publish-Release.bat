@echo off
rem Publishes the version in modelgen\__init__.py as a GitHub release of ikverse/modelgen:
rem builds the setup, pushes the code, and uploads ModelGen-Setup.exe and its SHA-256 fingerprint.
rem Every installed ModelGen sees the new release within a few hours (or at once with "Check now").
rem Before running it: raise __version__ in modelgen\__init__.py and commit your changes.
setlocal
cd /d "%~dp0\.."
for /f "delims=" %%v in ('.venv\Scripts\python.exe -c "import modelgen; print(modelgen.__version__)"') do set "VER=%%v"

git diff --quiet
if errorlevel 1 ( echo You have changes that are not committed. Commit them first. & goto failed )
git diff --cached --quiet
if errorlevel 1 ( echo You have changes that are not committed. Commit them first. & goto failed )
gh release view v%VER% >nul 2>nul
if not errorlevel 1 ( echo v%VER% is already released. Raise __version__ in modelgen\__init__.py first. & goto failed )

call installer\Build-Installer.bat /nopause
if errorlevel 1 goto failed

echo Pushing the code ...
git push
if errorlevel 1 goto failed

echo Publishing v%VER% ...
gh release create v%VER% dist\ModelGen-Setup.exe dist\ModelGen-Setup.exe.sha256 --title "ModelGen %VER%" --notes "Download ModelGen-Setup.exe and run it. ModelGen needs ClaudeIn on the same PC. Installed copies update themselves."
if errorlevel 1 goto failed

echo.
echo Released ModelGen %VER%.
pause
exit /b 0

:failed
echo.
echo Not released. Read the message above.
pause
exit /b 1
