@echo off
rem Evidex on Windows: installs whatever is missing, then starts the app.
rem   evidex.bat           install if needed, then run at http://localhost:8000
rem   evidex.bat dev       same, but the API reloads on code changes and the Vite dev server
rem                        runs in a second window at http://localhost:5173
rem   evidex.bat install   install or update everything (packages, models, front-end build), then stop
rem Stop the app with Ctrl+C (in dev mode, also close the second window).
rem
rem What "install" covers (each step is skipped when already done):
rem   1. backend\.venv with Python 3.12, CPU PyTorch and backend\requirements.txt
rem      (reinstalled when requirements.txt changes)
rem   2. .env copied from .env.example if it does not exist yet
rem   3. the Ollama models named in config.yaml / .env (skipped if Ollama is not installed)
rem   4. the front end built into frontend\dist (skipped if Node.js is not installed)
setlocal
cd /d "%~dp0"
set "MODE=%~1"
if /i "%MODE%"=="install" (set "FORCE=1") else (set "FORCE=")
if not "%MODE%"=="" if /i not "%MODE%"=="dev" if /i not "%MODE%"=="install" (
    echo Unknown option "%MODE%". Use: evidex.bat, evidex.bat dev or evidex.bat install
    exit /b 1
)

rem ---- 1. Python environment ----
if exist "backend\.venv\Scripts\python.exe" goto :venv_ready
echo === Creating the Python environment (backend\.venv) ===
set "PYEXE="
where py >nul 2>nul && py -3.12 -c "import sys" >nul 2>nul && set "PYEXE=py -3.12"
if not defined PYEXE (
    where python >nul 2>nul && python -c "import sys; sys.exit(0 if sys.version_info[:2] == (3, 12) else 1)" >nul 2>nul && set "PYEXE=python"
)
if not defined PYEXE (
    echo Python 3.12 was not found. Install it from https://www.python.org/downloads/ and run evidex.bat again.
    goto :fail
)
%PYEXE% -m venv backend\.venv || goto :fail
:venv_ready
set "STAMP=backend\.venv\requirements.installed"
if not defined FORCE if exist "%STAMP%" fc /b "%STAMP%" "backend\requirements.txt" >nul 2>nul && goto :packages_ready
echo === Installing Python packages ===
backend\.venv\Scripts\python -m pip install --upgrade pip || goto :fail
rem CPU-only PyTorch first (for the reranker), so pip does not download the much larger CUDA build.
backend\.venv\Scripts\python -m pip install torch --index-url https://download.pytorch.org/whl/cpu || goto :fail
backend\.venv\Scripts\python -m pip install -r backend\requirements.txt || goto :fail
copy /y "backend\requirements.txt" "%STAMP%" >nul
:packages_ready

rem ---- 2. Settings file ----
if not exist ".env" (
    copy /y ".env.example" ".env" >nul || goto :fail
    echo Created .env from .env.example. Edit it only to use another model server or API keys.
)

rem ---- 3. Ollama models ----
where ollama >nul 2>nul
if errorlevel 1 (
    echo Note: Ollama is not installed. Install it from https://ollama.com, or point .env at another model server.
    goto :frontend
)
ollama list >nul 2>nul
if errorlevel 1 (
    echo Note: Ollama is installed but not running. Start the Ollama app, then run evidex.bat again.
    goto :frontend
)
pushd backend
.venv\Scripts\python -m app.ollama_models > "%TEMP%\evidex_models.txt"
if errorlevel 1 (
    popd
    echo Could not read the model names from config.yaml / .env; see the error above.
    goto :fail
)
for /f "usebackq delims=" %%M in ("%TEMP%\evidex_models.txt") do (
    ollama show %%M >nul 2>nul || (
        echo === Pulling Ollama model %%M ===
        ollama pull %%M || (popd & goto :fail)
    )
)
del "%TEMP%\evidex_models.txt" >nul 2>nul
popd

rem ---- 4. Front end ----
:frontend
where npm >nul 2>nul
if errorlevel 1 (
    echo Note: Node.js is not installed, so only the API runs. Install the LTS version from https://nodejs.org
    goto :installed
)
if not defined FORCE if exist "frontend\node_modules" goto :npm_ready
echo === Installing front-end packages ===
pushd frontend
call npm install || (popd & goto :fail)
popd
:npm_ready
if /i "%MODE%"=="dev" goto :installed
if not defined FORCE if exist "frontend\dist\index.html" goto :installed
echo === Building the front end ===
pushd frontend
call npm run build || (popd & goto :fail)
popd

:installed
if defined FORCE (
    echo.
    echo Installation finished. Start Evidex with evidex.bat
    endlocal
    exit /b 0
)

rem ---- Run ----
if /i "%MODE%"=="dev" goto :dev
echo.
echo Starting Evidex at http://localhost:8000  (health check: http://localhost:8000/api/health)
cd backend
.venv\Scripts\python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
endlocal
exit /b %errorlevel%

:dev
where npm >nul 2>nul
if errorlevel 1 (
    echo Node.js is needed for dev mode. Install it from https://nodejs.org or run evidex.bat without "dev".
    goto :fail
)
start "Evidex front end (Vite)" cmd /k "cd /d ""%~dp0frontend"" && npm run dev"
echo.
echo Starting the API with auto-reload. Open http://localhost:5173
cd backend
.venv\Scripts\python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
endlocal
exit /b %errorlevel%

:fail
echo.
echo Stopped because of the error above.
endlocal
exit /b 1
