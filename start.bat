@echo off
rem Keitaro Wrapper launcher: kill stale server, start fresh one, open browser
cd /d "%~dp0"
rem if an old server still holds port 8000, kill it (otherwise the new one can't start)
for /f "tokens=5" %%P in ('netstat -ano ^| findstr ":8000" ^| findstr "LISTENING"') do taskkill /F /PID %%P >nul 2>&1
timeout /t 1 /nobreak >nul
start "Keitaro Wrapper (server)" cmd /k ".venv\Scripts\python.exe -m uvicorn app.main:app --reload --port 8000"
rem wait until the server responds (up to ~15 s)
powershell -NoProfile -Command "for ($i = 0; $i -lt 30; $i++) { try { (Invoke-WebRequest -UseBasicParsing -TimeoutSec 1 http://127.0.0.1:8000) | Out-Null; exit 0 } catch { Start-Sleep -Milliseconds 500 } }"
start "" http://127.0.0.1:8000
echo Server: http://127.0.0.1:8000
