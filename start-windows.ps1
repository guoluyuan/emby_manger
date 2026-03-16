$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

$existing = Get-CimInstance Win32_Process | Where-Object { $_.Name -eq 'python.exe' -and $_.CommandLine -match 'run.py' }
if ($existing) {
    $pids = ($existing | Select-Object -ExpandProperty ProcessId) -join ', '
    Write-Host "EmbyPulse is already running (PID: $pids)."
    Write-Host "Open: http://127.0.0.1:10307/"
    exit 0
}

Write-Host "Starting EmbyPulse..."
Write-Host "Open: http://127.0.0.1:10307/"

Write-Host "Building Tailwind CSS..."
npm run build:css

python run.py
