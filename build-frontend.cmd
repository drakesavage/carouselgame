@echo off
setlocal
cd /d "%~dp0frontend"
rem Uses CAROUSEL_NODE, else the Node bundled with the Codex app, else node on PATH (same lookup as Barcode Battles).
set "RT=%USERPROFILE%\.cache\codex-runtimes\codex-primary-runtime\dependencies\node"
set "NODE_EXE=node"
if exist "%RT%\bin\node.exe" set "NODE_EXE=%RT%\bin\node.exe"
if defined CAROUSEL_NODE set "NODE_EXE=%CAROUSEL_NODE%"

if not exist "node_modules\vite" (
  if exist "%RT%\node_modules\pnpm\bin\pnpm.cjs" (
    "%NODE_EXE%" "%RT%\node_modules\pnpm\bin\pnpm.cjs" install --config.node-linker=hoisted
  ) else (
    call npm install
  )
  if errorlevel 1 exit /b 1
)
"%NODE_EXE%" node_modules\typescript\bin\tsc -b
if errorlevel 1 exit /b 1
"%NODE_EXE%" node_modules\vite\bin\vite.js build
exit /b %ERRORLEVEL%
