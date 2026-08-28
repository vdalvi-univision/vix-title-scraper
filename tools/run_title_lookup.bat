@echo off
setlocal
cd /d "%~dp0.."
set "PY=.venv\Scripts\python.exe"
if not exist "%PY%" set "PY=python"

REM Kill anything listening on 8765 (best-effort)
for /f "tokens=5" %%p in ('netstat -ano ^| findstr ":8765" ^| findstr "LISTENING"') do (
  echo Stopping old process on port 8765 ^(PID %%p^)
  taskkill /F /PID %%p >nul 2>&1
)

echo Starting ViX title layout compare with: %PY%
echo Open UI: http://127.0.0.1:8765/  ^(do not open HTML as a file^)
echo Paste tokens in the form to Scrape / Refresh; then Compare / Lookup title.
"%PY%" tools\title_lookup.py --port 8765
endlocal
