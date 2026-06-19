$ErrorActionPreference = "Stop"

$ClientDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$Python = Join-Path $ClientDir "venv\Scripts\python.exe"

if (!(Test-Path $Python)) {
    Write-Host "Creating client virtual environment..."
    python -m venv (Join-Path $ClientDir "venv")
}

Write-Host "Installing build dependencies..."
& $Python -m pip install --disable-pip-version-check -r (Join-Path $ClientDir "requirements-build.txt")

Write-Host "Building LastManStanding.exe..."
$MainScript = Join-Path $ClientDir "main.py"
$SettingsExample = Join-Path $ClientDir "client_settings.example.json"
Push-Location $ClientDir
try {
    & $Python -m PyInstaller `
        --noconfirm `
        --clean `
        --onefile `
        --windowed `
        --name LastManStanding `
        --distpath (Join-Path $ClientDir "dist") `
        --workpath (Join-Path $ClientDir "build") `
        --specpath $ClientDir `
        --add-data "$SettingsExample;." `
        "$MainScript"
}
finally {
    Pop-Location
}

$DistDir = Join-Path $ClientDir "dist"
$SettingsTarget = Join-Path $DistDir "client_settings.json"
if (!(Test-Path $SettingsTarget)) {
    Copy-Item (Join-Path $ClientDir "client_settings.example.json") $SettingsTarget
}

Write-Host ""
Write-Host "Build complete:"
Write-Host (Join-Path $DistDir "LastManStanding.exe")
Write-Host ""
Write-Host "Before publishing, edit dist\client_settings.json with your Render URL."
