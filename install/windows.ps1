# windows.ps1 - installs the program on Windows. Started by Install-Windows.bat.
#
# What it does, in order, saying each step as it goes:
#   1. Finds Python 3.10 or newer. If there is none, offers to install it with
#      winget, Windows' own installer, for this user only (no administrator).
#   2. The same for Node.js (the LTS version).
#   3. Makes a private Python folder for the program (.venv inside this folder)
#      and installs its libraries there, so nothing else on the computer changes.
#   4. Installs the office's Node libraries from the lockfile (npm ci).
#   5. Makes "Start Glasswork.bat" in this folder, a desktop shortcut and a Start
#      menu entry, then offers to open the program.
#
# Run it again at any time: it only does what is still missing.
#   -CheckOnly   say what is missing and change nothing

param([switch]$CheckOnly)
$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location $Root
$Name = "Glasswork"
try { $Name = (Get-Content (Join-Path $Root "config\product.json") -Raw | ConvertFrom-Json).name } catch { }

function Say($text) { Write-Host "  $text" }
function Step($text) { Write-Host ""; Write-Host "== $text" -ForegroundColor Cyan }
function Fail($text) { Write-Host ""; Write-Host "  $text" -ForegroundColor Red; exit 1 }

function Refresh-Path {
    $m = [Environment]::GetEnvironmentVariable("Path", "Machine")
    $u = [Environment]::GetEnvironmentVariable("Path", "User")
    $env:Path = "$m;$u"
}

function Find-Python {
    foreach ($cmd in @("py -3", "python", "python3")) {
        $parts = $cmd.Split(" ")
        $exe = Get-Command $parts[0] -ErrorAction SilentlyContinue
        if (-not $exe) { continue }
        try {
            # not $args: that name is PowerShell's own
            $extra = @()
            if ($parts.Length -gt 1) { $extra += $parts[1] }
            $v = & $exe.Source @extra -c "import sys; print('%d.%d' % sys.version_info[:2])" 2>$null
            if ($v -and [version]$v -ge [version]"3.10") { return @{ exe = $exe.Source; extra = $extra; version = $v } }
        } catch { }
    }
    return $null
}

function Find-Node {
    $exe = Get-Command node -ErrorAction SilentlyContinue
    if (-not $exe) { return $null }
    try {
        $v = (& $exe.Source --version).TrimStart("v")
        if ([version]($v.Split(".")[0] + ".0") -ge [version]"18.0") { return @{ exe = $exe.Source; version = $v } }
    } catch { }
    return $null
}

function Winget-Install($id, $what) {
    if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
        Fail "$what is not installed, and this Windows has no winget to install it. Install $what yourself (python.org or nodejs.org), then run this again."
    }
    $yes = Read-Host "  $what is not installed. Install it now with winget? (Y/n)"
    if ($yes -and $yes.Trim().ToLower().StartsWith("n")) { Fail "Stopped. Install $what, then run this again." }
    winget install -e --id $id --scope user --accept-package-agreements --accept-source-agreements
    Refresh-Path
}

Write-Host ""
Write-Host "Installing $Name in $Root" -ForegroundColor Cyan

Step "Python"
$py = Find-Python
if (-not $py) {
    if ($CheckOnly) { Say "Python 3.10 or newer: MISSING" } else { Winget-Install "Python.Python.3.12" "Python"; $py = Find-Python }
    if (-not $py -and -not $CheckOnly) { Fail "Python still cannot be found. Close this window, open a new one and run Install again." }
}
if ($py) { Say "Python $($py.version) found." }

Step "Node.js"
$node = Find-Node
if (-not $node) {
    if ($CheckOnly) { Say "Node.js 18 or newer: MISSING" } else { Winget-Install "OpenJS.NodeJS.LTS" "Node.js"; $node = Find-Node }
    if (-not $node -and -not $CheckOnly) { Fail "Node.js still cannot be found. Close this window, open a new one and run Install again." }
}
if ($node) { Say "Node.js $($node.version) found." }

if ($CheckOnly) { Write-Host ""; Say "Check only: nothing was changed."; exit 0 }

Step "The program's own Python folder"
$venvPy = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path $venvPy)) {
    $extra = $py.extra
    & $py.exe @extra -m venv (Join-Path $Root ".venv")
    if (-not (Test-Path $venvPy)) { Fail "Could not make the .venv folder." }
    Say "Made .venv."
} else { Say ".venv is already there." }
& $venvPy -m pip install --upgrade pip --disable-pip-version-check -q
& $venvPy -m pip install -r (Join-Path $Root "requirements.txt") --disable-pip-version-check
if ($LASTEXITCODE -ne 0) { Fail "Installing the Python libraries failed. The lines above say why." }
Say "Python libraries installed."

Step "The office's Node libraries"
npm ci --no-audit --no-fund
if ($LASTEXITCODE -ne 0) {
    Say "npm ci could not use the lockfile; trying npm install."
    npm install --no-audit --no-fund
    if ($LASTEXITCODE -ne 0) { Fail "Installing the Node libraries failed. The lines above say why." }
}
Say "Node libraries installed."

Step "Shortcuts"
$start = Join-Path $Root "Start $Name.bat"
Set-Content -Path $start -Encoding ASCII -Value "@echo off`r`ncd /d `"%~dp0`"`r`n`".venv\Scripts\python.exe`" run.py %*`r`nif errorlevel 1 pause`r`n"
Say "Made `"Start $Name.bat`" in this folder."
$shell = New-Object -ComObject WScript.Shell
foreach ($dir in @([Environment]::GetFolderPath("Desktop"), (Join-Path ([Environment]::GetFolderPath("Programs")) $Name))) {
    New-Item -ItemType Directory -Force -Path $dir | Out-Null
    $lnk = $shell.CreateShortcut((Join-Path $dir "$Name.lnk"))
    $lnk.TargetPath = $start
    $lnk.WorkingDirectory = $Root
    $lnk.Description = "Open $Name"
    $lnk.Save()
}
Say "Added $Name to the desktop and the Start menu."

Write-Host ""
Write-Host "  $Name is installed." -ForegroundColor Green
$open = Read-Host "  Open it now? (Y/n)"
if (-not ($open -and $open.Trim().ToLower().StartsWith("n"))) { Start-Process -FilePath $start -WorkingDirectory $Root }
