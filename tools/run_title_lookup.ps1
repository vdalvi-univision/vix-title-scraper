# Start the ViX title layout compare UI (uses project .venv when present).
# Opens http://127.0.0.1:8765/ in your browser.
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

$VenvPy = Join-Path $Root ".venv\Scripts\python.exe"
if (Test-Path $VenvPy) {
    $Python = $VenvPy
} else {
    $Python = "python"
    Write-Host "Warning: .venv not found; using system python on PATH." -ForegroundColor Yellow
}

# Free port 8765 if an old lookup server is still listening
$pids = @()
try {
    $pids = @(Get-NetTCPConnection -LocalPort 8765 -State Listen -ErrorAction Stop |
        Select-Object -ExpandProperty OwningProcess -Unique)
} catch {
    foreach ($line in (netstat -ano | Select-String ":8765\s+.*LISTENING")) {
        $parts = ($line.ToString() -split '\s+') | Where-Object { $_ }
        if ($parts.Count -ge 5) { $pids += [int]$parts[-1] }
    }
    $pids = $pids | Select-Object -Unique
}
foreach ($procId in $pids) {
    try {
        Stop-Process -Id $procId -Force -ErrorAction SilentlyContinue
        Write-Host "Stopped old process on port 8765 (PID $procId)"
    } catch {}
}

Write-Host "Starting ViX title layout compare with: $Python"
Write-Host "Open UI: http://127.0.0.1:8765/  (do not open HTML as a file)"
Write-Host "Paste tokens in the form to Scrape / Refresh; then Compare / Lookup title."
& $Python (Join-Path $Root "tools\title_lookup.py") --port 8765
