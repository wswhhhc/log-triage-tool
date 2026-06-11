@echo off
cd /d "%~dp0.."
if exist anomaly_triage.db del anomaly_triage.db
start /B uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
echo http://localhost:8000
pause >nul
taskkill //F //IM uvicorn.exe >nul 2>&1
