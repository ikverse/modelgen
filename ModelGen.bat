@echo off
rem Starts the ModelGen server from its own environment and opens the page.
rem Any ModelGen server already running is stopped first, so every start is a fresh one.
title ModelGen
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    echo ModelGen is not set up on this PC yet. Run Setup-ModelGen.bat first.
    pause
    exit /b 1
)
rem Keep in step with server.port in config.yaml.
set PORT=8770
powershell -NoProfile -Command "$old = @(Get-CimInstance Win32_Process | Where-Object { $_.Name -eq 'python.exe' -and $_.CommandLine -like '*-m modelgen.server*' }); $ids = $old.ProcessId; foreach ($p in $old) { if ($ids -notcontains $p.ParentProcessId) { taskkill /T /F /PID $p.ProcessId *> $null; 'Stopped the old ModelGen (PID ' + $p.ProcessId + ')' } }; for ($i = 0; $i -lt 50 -and (Get-NetTCPConnection -LocalPort %PORT% -State Listen -ErrorAction SilentlyContinue); $i++) { Start-Sleep -Milliseconds 100 }"
rem /noopen: the setup starts ModelGen again after an update; the page that is already open reconnects.
set "OPEN=--open"
if /i "%~1"=="/noopen" set "OPEN="
".venv\Scripts\python.exe" -m modelgen.server %OPEN%
if errorlevel 1 pause
