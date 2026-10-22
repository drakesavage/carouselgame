@echo off
setlocal
cd /d "%~dp0"
set "PORT=8777"

if not exist ".venv\Scripts\python.exe" (
  echo [Crawler Console] Creating Python environment...
  py -3 -m venv .venv 2>nul || python -m venv .venv
  if errorlevel 1 ( echo Python 3.10+ is required. & pause & exit /b 1 )
  ".venv\Scripts\python.exe" -m pip install --disable-pip-version-check -q -r backend\requirements.txt
  if errorlevel 1 ( echo Dependency install failed. & pause & exit /b 1 )
)
if not exist "frontend\dist\index.html" (
  echo [Crawler Console] Building the interface...
  call "%~dp0build-frontend.cmd"
  if errorlevel 1 ( echo Frontend build failed. & pause & exit /b 1 )
)

echo [Crawler Console] http://127.0.0.1:%PORT%   (Ctrl+C to stop)
start "" /b cmd /c "timeout /t 3 >nul & start "" http://127.0.0.1:%PORT%"
".venv\Scripts\python.exe" -m uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port %PORT%
