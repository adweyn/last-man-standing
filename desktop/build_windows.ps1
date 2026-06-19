$ErrorActionPreference = "Stop"

$DesktopDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$VenvDir = Join-Path $DesktopDir "venv"
$Python = Join-Path $VenvDir "Scripts\python.exe"

if (!(Test-Path $Python)) {
    Write-Host "Creating desktop virtual environment..."
    python -m venv $VenvDir
}

Write-Host "Installing desktop build dependencies..."
& $Python -m pip install --disable-pip-version-check -r (Join-Path $DesktopDir "requirements.txt")

Write-Host "Building LastManStanding.exe from the web/Telegram version..."
$MainScript = Join-Path $DesktopDir "main.py"
$Settings = Join-Path $DesktopDir "app_settings.json"
& $Python -m PyInstaller `
    --noconfirm `
    --clean `
    --onefile `
    --windowed `
    --name LastManStanding `
    --distpath (Join-Path $DesktopDir "dist") `
    --workpath (Join-Path $DesktopDir "build") `
    --specpath $DesktopDir `
    --add-data "$Settings;." `
    "$MainScript"

$ReleaseDir = Join-Path $DesktopDir "release"
New-Item -ItemType Directory -Path $ReleaseDir -Force | Out-Null
Copy-Item $Settings (Join-Path $DesktopDir "dist\app_settings.json") -Force
Compress-Archive `
    -Path (Join-Path $DesktopDir "dist\LastManStanding.exe"), (Join-Path $DesktopDir "dist\app_settings.json") `
    -DestinationPath (Join-Path $ReleaseDir "LastManStanding-PC-Web.zip") `
    -Force

Write-Host ""
Write-Host "Build complete:"
Write-Host (Join-Path $ReleaseDir "LastManStanding-PC-Web.zip")
