@echo off
rem Runs the Carousel server without opening a browser (Windows startup uses this; start.cmd is the interactive one).
rem Does nothing when something is already listening on the port, so a second launch is harmless.
setlocal
cd /d "%~dp0"
set "PORT=8797"
netstat -ano -p tcp | findstr /r /c:"127\.0\.0\.1:%PORT% .*LISTENING" >nul && exit /b 0
title Carousel server (port %PORT%) - close to stop the game
if not exist data mkdir data
echo [%date% %time%] serve.cmd starting the server on port %PORT% >> data\server.log
".venv\Scripts\python.exe" -m uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port %PORT% >> data\server.log 2>&1
echo [%date% %time%] server process ended with exit code %errorlevel% >> data\server.log
