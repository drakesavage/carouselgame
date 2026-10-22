@echo off
cd /d "%~dp0backend"
"..\.venv\Scripts\python.exe" -m pytest -q %*
