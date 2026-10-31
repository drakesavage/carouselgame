@echo off
rem Rebuilds data\reference.db from the Carousel source packages (CAROUSEL_SOURCE_DIR, default E:\Carousel).
rem Safe to re-run after a map or data fix: it refuses, and keeps the old data, if a saved campaign would lose a place or resident.
setlocal
cd /d "%~dp0backend"
"..\.venv\Scripts\python.exe" -m app.carousel.importer
exit /b %ERRORLEVEL%
