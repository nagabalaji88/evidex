@echo off
rem Evidex on Windows: double-click (or run evidex.bat). Every run does the same steps:
rem   1. Backend:   backend\.venv (Python 3.12) with backend\requirements.txt
rem                 (installed the first time, reinstalled when requirements.txt changes)
rem   2. Settings:  .env created from .env.example if missing (the Gemini key is set in the app)
rem   3. Front end: npm packages (when package-lock.json changes) and the build in frontend\dist
rem                 (rebuilt whenever the front-end code changes, e.g. after a git pull)
rem   4. Launch:    starts Evidex at http://localhost:8000 and opens it in your browser
rem No local AI models are installed or started: answers come from the Gemini API, chosen in
rem Settings. (The optional local reranker is in backend\requirements-local.txt; not used here.)
rem Stop Evidex with Ctrl+C or by closing this window.
setlocal
cd /d "%~dp0"
set "PY=backend\.venv\Scripts\python.exe"
set "URL=http://localhost:8000"
rem So "python -m app.launcher" works from the repo root.
set "PYTHONPATH=%~dp0backend"

rem ---- 1. Backend ----
echo [1/4] Backend
if exist "%PY%" goto :venv_ready
set "PYEXE="
where py >nul 2>nul && py -3.12 -c "import sys" >nul 2>nul && set "PYEXE=py -3.12"
if not defined PYEXE (
    where python >nul 2>nul && python -c "import sys; sys.exit(0 if sys.version_info[:2] == (3, 12) else 1)" >nul 2>nul && set "PYEXE=python"
)
if not defined PYEXE (
    echo Python 3.12 was not found. Install it from https://www.python.org/downloads/ and run evidex.bat again.
    goto :fail
)
echo       Creating backend\.venv with %PYEXE% ...
%PYEXE% -m venv backend\.venv || goto :fail
:venv_ready
set "STAMP=backend\.venv\requirements.installed"
if exist "%STAMP%" fc /b "%STAMP%" "backend\requirements.txt" >nul 2>nul && goto :backend_ready
echo       Installing Python packages ...
"%PY%" -m pip install --upgrade pip || goto :fail
"%PY%" -m pip install -r backend\requirements.txt || goto :fail
copy /y "backend\requirements.txt" "%STAMP%" >nul
:backend_ready
"%PY%" -c "import fastapi, uvicorn, chromadb, httpx, pdfplumber" >nul 2>nul
if errorlevel 1 (
    echo       Some Python packages are missing; reinstalling ...
    "%PY%" -m pip install -r backend\requirements.txt || goto :fail
)
echo       ready

rem ---- 2. Settings file ----
echo [2/4] Settings
if not exist ".env" (
    copy /y ".env.example" ".env" >nul || goto :fail
    echo       Created .env. Add your Gemini API key in the app: Settings, AI models.
) else (
    echo       .env found
)

rem ---- 3. Front end ----
echo [3/4] Front end
where npm >nul 2>nul
if errorlevel 1 (
    echo Node.js was not found. The web page needs it. Install the LTS version from https://nodejs.org
    echo and run evidex.bat again.
    goto :fail
)
set "NPMSTAMP=frontend\node_modules\.package-lock.installed"
if exist "%NPMSTAMP%" fc /b "%NPMSTAMP%" "frontend\package-lock.json" >nul 2>nul && goto :npm_ready
echo       Installing front-end packages ...
pushd frontend
call npm install || (popd & goto :fail)
popd
copy /y "frontend\package-lock.json" "%NPMSTAMP%" >nul
:npm_ready
set "FEHASH="
for /f "usebackq delims=" %%H in (`"%PY%" -m app.launcher frontend-hash`) do set "FEHASH=%%H"
set "FESTAMP=frontend\dist\.build-hash"
set "BUILT="
if exist "frontend\dist\index.html" if exist "%FESTAMP%" set /p BUILT=<"%FESTAMP%"
if defined FEHASH if "%BUILT%"=="%FEHASH%" goto :frontend_ready
echo       Building the web page ...
pushd frontend
call npm run build || (popd & goto :fail)
popd
if defined FEHASH (>"%FESTAMP%" echo %FEHASH%)
:frontend_ready
echo       ready

rem ---- 4. Launch ----
echo [4/4] Starting Evidex at %URL%  (Ctrl+C or close this window to stop)
start "" /b "%PY%" -m app.launcher open %URL%
cd backend
.venv\Scripts\python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
endlocal
exit /b %errorlevel%

:fail
echo.
echo Stopped because of the error above.
pause
endlocal
exit /b 1
