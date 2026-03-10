@echo off
REM Batch script to start backend and frontend

set "ROOT=%~dp0"
set "VENV_PY=%ROOT%.venv\Scripts\python.exe"

if not exist "%VENV_PY%" (
	echo [ERROR] Virtual environment not found: %VENV_PY%
	echo Please create it first with: python -m venv .venv
	pause
	exit /b 1
)

REM Start backend in new window
start "Backend" cmd /k "cd /d %ROOT%SonicVale && call ^"%VENV_PY%^" -m uvicorn app.main:app --reload --port 8200"

REM Start frontend in new window
start "Frontend" cmd /k "cd /d %ROOT%sonicvale-front && npm run start"

echo Development servers launched. Close this window if you don't need it.
pause