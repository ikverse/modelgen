@echo off
rem Starts the ModelGen server from its own environment and opens the page.
rem Any ModelGen server already running is stopped first, so every start is a fresh one.
rem The server stops by itself a little after its last page closes (--auto-stop).
rem /noopen: do not open the page. /hidden: ModelGen.vbs runs this with no window, so the server's
rem output goes to data\server.log and an error does not wait at a pause nobody can see.
title ModelGen
cd /d "%~dp0"
set "OPEN=--open"
set "HIDDEN="
for %%a in (%*) do (
    if /i "%%~a"=="/noopen" set "OPEN="
    if /i "%%~a"=="/hidden" set "HIDDEN=1"
)
if not exist "data" mkdir data
if not exist ".venv\Scripts\python.exe" (
    if defined HIDDEN (
        echo ModelGen is not set up on this PC yet. Run Setup-ModelGen.bat first.> data\server.log
        exit /b 1
    )
    echo ModelGen is not set up on this PC yet. Run Setup-ModelGen.bat first.
    pause
    exit /b 1
)
rem Keep in step with server.port in config.yaml.
set PORT=8770
powershell -NoProfile -Command "$old = @(Get-CimInstance Win32_Process | Where-Object { $_.Name -eq 'python.exe' -and $_.CommandLine -like '*-m modelgen.server*' }); $ids = $old.ProcessId; foreach ($p in $old) { if ($ids -notcontains $p.ParentProcessId) { taskkill /T /F /PID $p.ProcessId *> $null; 'Stopped the old ModelGen (PID ' + $p.ProcessId + ')' } }; for ($i = 0; $i -lt 50 -and (Get-NetTCPConnection -LocalPort %PORT% -State Listen -ErrorAction SilentlyContinue); $i++) { Start-Sleep -Milliseconds 100 }"
rem /noopen: the setup starts ModelGen again after an update; the page that is already open reconnects.
if defined HIDDEN (
    ".venv\Scripts\python.exe" -m modelgen.server %OPEN% --auto-stop > data\server.log 2>&1
    exit /b
)
".venv\Scripts\python.exe" -m modelgen.server %OPEN% --auto-stop
if errorlevel 1 pause
