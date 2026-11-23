@echo off
rem Evidex: one-time installation on Windows. Run from anywhere: double-click or "install.bat".
rem   1. backend\.venv with the Python packages (Python 3.12)
rem   2. .env copied from .env.example if it does not exist yet
rem   3. the Ollama models named in config.yaml / .env (skipped if Ollama is not installed)
rem   4. the front end built into frontend\dist (skipped if Node.js is not installed)
rem Safe to run again: it only adds what is missing and updates packages.
setlocal
cd /d "%~dp0"

echo.
echo === 1/4  Python environment (backend\.venv) ===
if exist "backend\.venv\Scripts\python.exe" goto :venv_ready
set "PYEXE="
where py >nul 2>nul && py -3.12 -c "import sys" >nul 2>nul && set "PYEXE=py -3.12"
if not defined PYEXE (
    where python >nul 2>nul && python -c "import sys; sys.exit(0 if sys.version_info[:2] == (3, 12) else 1)" >nul 2>nul && set "PYEXE=python"
)
if not defined PYEXE (
    echo Python 3.12 was not found. Install it from https://www.python.org/downloads/ and run install.bat again.
    goto :fail
)
echo Creating backend\.venv with %PYEXE% ...
%PYEXE% -m venv backend\.venv || goto :fail
:venv_ready
backend\.venv\Scripts\python -m pip install --upgrade pip || goto :fail
rem CPU-only PyTorch first (for the reranker), so pip does not download the much larger CUDA build.
backend\.venv\Scripts\python -m pip install torch --index-url https://download.pytorch.org/whl/cpu || goto :fail
backend\.venv\Scripts\python -m pip install -r backend\requirements.txt || goto :fail

echo.
echo === 2/4  Settings file (.env) ===
if exist ".env" (
    echo .env already exists; left unchanged.
) else (
    copy /y ".env.example" ".env" >nul || goto :fail
    echo Created .env from .env.example. Edit it only to use another model server or API keys.
)

echo.
echo === 3/4  Ollama models ===
where ollama >nul 2>nul
if errorlevel 1 (
    echo Ollama is not installed; skipped. Install it from https://ollama.com and run install.bat again,
    echo or point .env at another model server.
    goto :frontend
)
pushd backend
for /f "usebackq delims=" %%M in (`.venv\Scripts\python -m app.ollama_models`) do (
    echo Pulling %%M ...
    ollama pull %%M || (popd & goto :fail)
)
popd

:frontend
echo.
echo === 4/4  Front end (frontend\dist) ===
where npm >nul 2>nul
if errorlevel 1 (
    echo Node.js is not installed; skipped. Install the LTS version from https://nodejs.org and run install.bat again.
    goto :done
)
pushd frontend
call npm install || (popd & goto :fail)
call npm run build || (popd & goto :fail)
popd

:done
echo.
echo Installation finished. Start Evidex with run.bat
endlocal
exit /b 0

:fail
echo.
echo Installation stopped because of the error above.
endlocal
exit /b 1
