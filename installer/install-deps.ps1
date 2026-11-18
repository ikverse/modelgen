# ModelGen's setup runs this after copying the program files. It checks the PC and installs only
# what is missing:
#   1. ClaudeIn   - must be set up on this PC (ModelGen reaches Claude only through it). When
#                   -ClaudeInUrl is given and ClaudeIn is missing, its setup is downloaded and run.
#   2. Python 3.14 - used if found; otherwise python.org's installer is downloaded, its digital
#                   signature checked, and installed quietly for this user only.
#   3. Packages   - ModelGen's own environment in .venv, with the packages from requirements.txt
#                   and the ClaudeIn client the setup carries.
#   4. Blender    - used if installed; otherwise Blender 5.2 (blender.org) is downloaded, checked
#                   against blender.org's SHA-256 and unpacked into ModelGen's blender\ folder.
# Then it writes installed.json, which marks this folder as an installed copy (for auto-update).
# Windows PowerShell 5.1. Exit code 0 = ready, anything else = something is missing (see install.log).

param(
    [Parameter(Mandatory = $true)][string]$AppDir,
    [string]$Version = "",
    [string]$ClaudeInUrl = ""
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"   # Invoke-WebRequest is many times slower with its progress bar
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

$PythonVersion = "3.14.6"
$PythonUrl = "https://www.python.org/ftp/python/$PythonVersion/python-$PythonVersion-amd64.exe"
$BlenderUrl = "https://download.blender.org/release/Blender5.2/blender-5.2.3-windows-x64.zip"
$BlenderSha256 = "723f08a4401502561eb763cdeacf8745b31e5f3f154d287d3830d97cf75dc2fb"
$Log = Join-Path $AppDir "install.log"
$Temp = Join-Path $env:TEMP "modelgen-setup"
New-Item -ItemType Directory -Force $Temp | Out-Null

function Say([string]$text) {
    Write-Host $text
    Add-Content -Path $Log -Value ("{0}  {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $text) -Encoding UTF8
}

function Fail([string]$text) {
    Say "STOPPED: $text"
    exit 1
}

function Download([string]$url, [string]$dest) {
    Say "Downloading $url ..."
    $part = "$dest.part"
    Invoke-WebRequest -Uri $url -OutFile $part -UseBasicParsing -UserAgent "ModelGen-setup"
    Move-Item -Force $part $dest
}

Say "ModelGen $Version setup: checking this PC"

# --- 1. ClaudeIn ----------------------------------------------------------------------------------
$claudeinJson = Join-Path $env:USERPROFILE ".claudein\local.json"
if (-not (Test-Path $claudeinJson)) {
    if ($ClaudeInUrl) {
        $setup = Join-Path $Temp "ClaudeIn-Setup.exe"
        Download $ClaudeInUrl $setup
        Say "Running ClaudeIn's setup ..."
        $p = Start-Process -FilePath $setup -Wait -PassThru
        if ($p.ExitCode -ne 0 -or -not (Test-Path $claudeinJson)) {
            Fail "ClaudeIn's setup did not finish. Install ClaudeIn, then run ModelGen's setup again."
        }
    } else {
        Fail "ClaudeIn is not installed on this PC. ModelGen reaches Claude only through ClaudeIn: install it, then run ModelGen's setup again."
    }
}
try {
    $port = (Get-Content $claudeinJson -Raw | ConvertFrom-Json).port
    $listening = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue
    if ($listening) { Say "ClaudeIn: found and running." }
    else { Say "ClaudeIn: found, but not running now. Start it before using ModelGen." }
} catch {
    Say "ClaudeIn: found (could not tell whether it is running)."
}

# --- 2. Python 3.14 -------------------------------------------------------------------------------
function Find-Python {
    $candidates = @(
        (Join-Path $env:LOCALAPPDATA "Programs\Python\Python314\python.exe"),
        "C:\Python314\python.exe",
        (Join-Path $env:ProgramFiles "Python314\python.exe")
    )
    foreach ($c in $candidates) { if (Test-Path $c) { return $c } }
    $py = Get-Command py -ErrorAction SilentlyContinue
    if ($py) {
        try {
            $exe = & $py.Source -3.14 -c "import sys; print(sys.executable)" 2>$null
            if ($LASTEXITCODE -eq 0 -and $exe -and (Test-Path $exe.Trim())) { return $exe.Trim() }
        } catch { }
    }
    return $null
}

$python = Find-Python
if ($python) {
    Say "Python 3.14: found ($python)."
} else {
    $setup = Join-Path $Temp "python-$PythonVersion-amd64.exe"
    Download $PythonUrl $setup
    $sig = Get-AuthenticodeSignature $setup
    if ($sig.Status -ne "Valid" -or $sig.SignerCertificate.Subject -notmatch "Python Software Foundation") {
        Remove-Item -Force $setup
        Fail "The Python installer's digital signature did not check out, so it was deleted and not run."
    }
    Say "Installing Python $PythonVersion for this user (a minute or two) ..."
    $pyArgs = "/quiet InstallAllUsers=0 PrependPath=0 Include_launcher=1 InstallLauncherAllUsers=0 Include_test=0 Include_doc=0 Shortcuts=0"
    $p = Start-Process -FilePath $setup -ArgumentList $pyArgs -Wait -PassThru
    if ($p.ExitCode -ne 0) { Fail "Python's installer stopped with code $($p.ExitCode)." }
    $python = Find-Python
    if (-not $python) { Fail "Python was installed but could not be found afterwards." }
    Say "Python 3.14: installed ($python)."
}

# --- 3. ModelGen's environment and packages ------------------------------------------------------
$venvPython = Join-Path $AppDir ".venv\Scripts\python.exe"
if (-not (Test-Path $venvPython)) {
    Say "Making ModelGen's Python environment ..."
    & $python -m venv (Join-Path $AppDir ".venv")
    if ($LASTEXITCODE -ne 0) { Fail "Could not make the Python environment." }
}
$wheel = Get-ChildItem (Join-Path $AppDir "installer\wheels") -Filter "claudein_client-*.whl" | Select-Object -First 1
if (-not $wheel) { Fail "The ClaudeIn client is missing from this setup." }
Say "Installing packages (needs the internet the first time) ..."
& $venvPython -m pip install --disable-pip-version-check --quiet $wheel.FullName
if ($LASTEXITCODE -ne 0) { Fail "Installing the ClaudeIn client failed." }
& $venvPython -m pip install --disable-pip-version-check --quiet -r (Join-Path $AppDir "requirements.txt")
if ($LASTEXITCODE -ne 0) { Fail "Installing the packages failed. Check the internet connection and run the setup again." }
Say "Packages: ready."

# --- 4. Blender -----------------------------------------------------------------------------------
$found = & $venvPython -c "import sys; sys.path.insert(0, sys.argv[1]); from modelgen import config; print(config.blender_path())" $AppDir 2>$null
if ($LASTEXITCODE -eq 0 -and $found -and (Test-Path $found.Trim())) {
    Say "Blender: found ($($found.Trim()))."
} else {
    $zip = Join-Path $Temp "blender-5.2.3-windows-x64.zip"
    if (-not (Test-Path $zip) -or (Get-FileHash $zip -Algorithm SHA256).Hash.ToLower() -ne $BlenderSha256) {
        Say "Blender is not installed. Downloading Blender 5.2 from blender.org (about 400 MB) ..."
        Download $BlenderUrl $zip
    }
    if ((Get-FileHash $zip -Algorithm SHA256).Hash.ToLower() -ne $BlenderSha256) {
        Remove-Item -Force $zip
        Fail "The Blender download did not match blender.org's SHA-256, so it was deleted."
    }
    Say "Unpacking Blender (a few minutes) ..."
    $target = Join-Path $AppDir "blender"
    $unpack = Join-Path $target "unpack"
    if (Test-Path $unpack) { Remove-Item -Recurse -Force $unpack }
    New-Item -ItemType Directory -Force $unpack | Out-Null
    & tar.exe -xf $zip -C $unpack
    if ($LASTEXITCODE -ne 0) { Fail "Unpacking Blender failed." }
    $inner = Get-ChildItem $unpack -Directory | Select-Object -First 1
    $final = Join-Path $target "Blender 5.2"
    if (Test-Path $final) { Remove-Item -Recurse -Force $final }
    Move-Item $inner.FullName $final
    Remove-Item -Recurse -Force $unpack
    Remove-Item -Force $zip
    if (-not (Test-Path (Join-Path $final "blender.exe"))) { Fail "Blender was unpacked but blender.exe is missing." }
    Say "Blender: installed ($final)."
}

# --- done -----------------------------------------------------------------------------------------
$marker = @{ version = $Version; installed_at = (Get-Date -Format "s") } | ConvertTo-Json
Set-Content -Path (Join-Path $AppDir "installed.json") -Value $marker -Encoding UTF8
Say "ModelGen is ready."
exit 0
