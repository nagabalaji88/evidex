@echo off
rem Evidex: start the app on Windows. Run install.bat once first.
rem   run.bat        the API, which also serves the built web page at http://localhost:8000
rem   run.bat dev    the API with auto-reload, plus the Vite dev server (http://localhost:5173)
rem                  in a second window, for working on the code
rem Stop with Ctrl+C (and close the second window in dev mode).
setlocal
cd /d "%~dp0"

if not exist "backend\.venv\Scripts\python.exe" (
    echo backend\.venv was not found. Run install.bat first.
    exit /b 1
)

where ollama >nul 2>nul
if not errorlevel 1 (
    ollama list >nul 2>nul
    if errorlevel 1 echo Note: Ollama is installed but not running. Start the Ollama app, or answers will fail.
)

if /i "%~1"=="dev" goto :dev

if not exist "frontend\dist\index.html" (
    echo Note: frontend\dist is missing, so only the API runs. Run install.bat with Node.js installed to build the web page.
)
echo Starting Evidex at http://localhost:8000  (health check: http://localhost:8000/api/health)
cd backend
.venv\Scripts\python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
endlocal
exit /b %errorlevel%

:dev
where npm >nul 2>nul
if errorlevel 1 (
    echo Node.js is needed for dev mode. Install it from https://nodejs.org or use run.bat without "dev".
    exit /b 1
)
if not exist "frontend\node_modules" (
    pushd frontend
    call npm install || (popd & exit /b 1)
    popd
)
start "Evidex front end (Vite)" cmd /k "cd /d ""%~dp0frontend"" && npm run dev"
echo Starting the API with auto-reload. Open http://localhost:5173
cd backend
.venv\Scripts\python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
endlocal
exit /b %errorlevel%
