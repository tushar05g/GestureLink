# GestureLink Hub Launcher with Port Cleanup
# This script kills any process using port 8000, then starts the Hub

Write-Host "[*] Checking for processes using port 8000..." -ForegroundColor Cyan

# Find process using port 8000
$processes = Get-NetTCPConnection -LocalPort 8000 -ErrorAction SilentlyContinue | ForEach-Object { $_.OwningProcess }

if ($processes) {
    Write-Host "[!] Found processes using port 8000: $processes" -ForegroundColor Yellow
    
    foreach ($process_id in $processes) {
        if ($process_id -lt 10) { continue }
        try {
            $process = Get-Process -Id $process_id -ErrorAction SilentlyContinue
            if ($process) {
                Write-Host "[X] Killing process: $($process.Name) (PID: $process_id)" -ForegroundColor Red
                Stop-Process -Id $process_id -Force -ErrorAction SilentlyContinue
            }
        } catch {
            Write-Host "[E] Could not kill PID $process_id`:" $_ -ForegroundColor Red
        }
    }
    
    Write-Host "[*] Waiting 2 seconds for port to free up..." -ForegroundColor Cyan
    Start-Sleep -Seconds 2
} else {
    Write-Host "[+] Port 8000 is free" -ForegroundColor Green
}

# Kill cloudflared tunnel process specifically
Write-Host "[*] Checking for cloudflared processes..." -ForegroundColor Cyan
$cloudflared = Get-Process -Name cloudflared -ErrorAction SilentlyContinue
if ($cloudflared) {
    Write-Host "[X] Killing cloudflared tunnel..." -ForegroundColor Red
    Stop-Process -Name cloudflared -Force -ErrorAction SilentlyContinue
    Start-Sleep -Seconds 2
}

# Also kill any existing Python Hub processes
Write-Host "[*] Checking for existing Hub processes..." -ForegroundColor Cyan
$hubProcesses = Get-Process -Name python -ErrorAction SilentlyContinue | Where-Object { $_.CommandLine -match "gesturelink\.py|hub" }

if ($hubProcesses) {
    Write-Host "[!] Found existing Hub processes" -ForegroundColor Yellow
    foreach ($proc in $hubProcesses) {
        Write-Host "[X] Killing: $($proc.Name) (PID: $($proc.Id))" -ForegroundColor Red
        Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue
    }
    Start-Sleep -Seconds 1
}

# Start the Hub
Write-Host "`n[+] Starting GestureLink Hub..." -ForegroundColor Green
Write-Host "====================================================" -ForegroundColor Cyan

& .\.venv\Scripts\python.exe gesturelink.py hub
