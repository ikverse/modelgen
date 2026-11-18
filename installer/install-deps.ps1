# ModelGen's setup runs this after copying the program files. It checks the PC and installs only
# what is missing:
#   1. ClaudeIn   - ModelGen reaches Claude only through it. When it is missing, the setup of the
#                   latest release of -ClaudeInRepo (GitHub) is downloaded and run with its window.
#   2. Python 3.14 - used if found; otherwise python.org's installer is downloaded, its digital
#                   signature checked, and installed quietly for this user only.
#   3. Packages   - ModelGen's own environment in .venv, with the packages from requirements.txt
#                   and the ClaudeIn client the setup carries.
#   4. Blender    - an installed Blender 5.2 or newer is used; otherwise (none, or an older one)
#                   Blender 5.2 (blender.org) is downloaded, checked against blender.org's SHA-256
#                   and unpacked into ModelGen's blender\ folder. The person's own Blender is left alone.
# Then it writes installed.json, which marks this folder as an installed copy (for auto-update).
# Windows PowerShell 5.1. Exit code 0 = ready, anything else = something is missing (see install.log).

param(
    [Parameter(Mandatory = $true)][string]$AppDir,
    [string]$Version = "",
    [string]$ClaudeInRepo = "ikverse/ClaudeIn"
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"   # Invoke-WebRequest is many times slower with its progress bar
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

$PythonVersion = "3.14.6"
$PythonUrl = "https://www.python.org/ftp/python/$PythonVersion/python-$PythonVersion-amd64.exe"
$BlenderUrl = "https://download.blender.org/release/Blender5.2/blender-5.2.3-windows-x64.zip"
$BlenderSha256 = "723f08a4401502561eb763cdeacf8745b31e5f3f154d287d3830d97cf75dc2fb"
$BlenderMinimum = [version]"5.2"   # ModelGen is built and tested on Blender 5.2
$ClaudeInWaitS = 120               # how long to wait for a just-installed ClaudeIn to start
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
    Say "ClaudeIn is not installed. Looking for it on GitHub ($ClaudeInRepo) ..."
    try {
        $release = Invoke-RestMethod -Uri "https://api.github.com/repos/$ClaudeInRepo/releases/latest" `
                                     -UserAgent "ModelGen-setup" -Headers @{ Accept = "application/vnd.github+json" }
    } catch {
        Fail "ClaudeIn isn't installed, and no ClaudeIn download was found on GitHub (none published yet, or no internet). Install ClaudeIn, then run ModelGen's setup again."
    }
    # only ClaudeIn's own installer, only from this repository's release downloads
    $asset = $release.assets | Where-Object { $_.name -match '^ClaudeIn-Setup-[0-9][0-9A-Za-z.\-]*\.exe$' } | Select-Object -First 1
    $prefix = "https://github.com/$ClaudeInRepo/releases/download/"
    if (-not $asset -or -not $asset.browser_download_url.StartsWith($prefix)) {
        Fail "ClaudeIn's latest release ($($release.tag_name)) has no installer. Install ClaudeIn, then run ModelGen's setup again."
    }
    $setup = Join-Path $Temp $asset.name
    Download $asset.browser_download_url $setup
    Say "Running ClaudeIn's setup ($($asset.name)). Choose its options and sign in to Claude there."
    $p = Start-Process -FilePath $setup -Wait -PassThru
    if ($p.ExitCode -ne 0) { Fail "ClaudeIn's setup did not finish (code $($p.ExitCode)). Run ModelGen's setup again to retry." }
    Say "Waiting for ClaudeIn to start ..."
    for ($i = 0; $i -lt $ClaudeInWaitS -and -not (Test-Path $claudeinJson); $i++) { Start-Sleep -Seconds 1 }
}
if (Test-Path $claudeinJson) {
    try {
        $port = (Get-Content $claudeinJson -Raw | ConvertFrom-Json).port
        $listening = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue
        if ($listening) { Say "ClaudeIn: found and running." }
        else { Say "ClaudeIn: found, but not running now. Start it before using ModelGen." }
    } catch {
        Say "ClaudeIn: found (could not tell whether it is running)."
    }
} else {
    Say "ClaudeIn: installed, but it has not started yet. Start ClaudeIn (from the Start menu) before using ModelGen."
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
function Blender-Version([string]$exe) {
    # "Blender 5.2.3 LTS" -> 5.2.3; $null when it cannot be told
    try {
        $line = & $exe --version 2>$null | Select-Object -First 1
        if ("$line" -match 'Blender\s+(\d+\.\d+(\.\d+)?)') { return [version]$Matches[1] }
    } catch { }
    return $null
}

$found = & $venvPython -c "import sys; sys.path.insert(0, sys.argv[1]); from modelgen import config; print(config.blender_path())" $AppDir 2>$null
$found = "$found".Trim()
$have = $null
if ($LASTEXITCODE -eq 0 -and $found -and (Test-Path $found)) { $have = Blender-Version $found }
if ($have -and $have -ge $BlenderMinimum) {
    Say "Blender: found $have ($found)."
} else {
    if ($have) { Say "Blender $have is installed, but ModelGen needs $BlenderMinimum or newer. Your Blender is left as it is." }
    elseif ($found) { Say "Blender was found ($found) but its version could not be read." }
    else { Say "Blender is not installed." }
    $zip = Join-Path $Temp "blender-5.2.3-windows-x64.zip"
    if (-not (Test-Path $zip) -or (Get-FileHash $zip -Algorithm SHA256).Hash.ToLower() -ne $BlenderSha256) {
        Say "Downloading Blender 5.2 from blender.org for ModelGen (about 400 MB) ..."
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
