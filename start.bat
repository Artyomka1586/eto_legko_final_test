@echo off
rem Запуск обёртки Keitaro: сервер + браузер
cd /d "%~dp0"
start "Keitaro Wrapper (сервер)" cmd /k ".venv\Scripts\python.exe -m uvicorn app.main:app --port 8000"
powershell -NoProfile -Command "for ($i = 0; $i -lt 30; $i++) { try { (Invoke-WebRequest -UseBasicParsing -TimeoutSec 1 http://127.0.0.1:8000) | Out-Null; exit 0 } catch { Start-Sleep -Milliseconds 500 } }"
start "" http://127.0.0.1:8000
echo Обёртка запущена: http://127.0.0.1:8000
