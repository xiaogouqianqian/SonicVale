@echo off
REM Batch script to start backend and frontend

REM Start backend in new window
start "Backend" cmd /k "cd /d %~dp0\SonicVale && uvicorn app.main:app --reload --port 8200"

REM Start frontend in new window
start "Frontend" cmd /k "cd /d %~dp0\sonicvale-front && npm run start"

echo Development servers launched. Close this window if you don't need it.
pause