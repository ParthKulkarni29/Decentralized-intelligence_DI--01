@echo off
setlocal enabledelayedexpansion

REM ============================================================================
REM Debug Dojo Unified Startup Script (Windows)
REM Starts Backend API (FastAPI/Uvicorn) and Frontend Dev Server (if present)
REM ============================================================================

REM Ensure working directory is project root
cd /d "%~dp0\.."
echo [Debug Dojo] Starting system from: %CD%

REM 1. Check Docker status
echo [1/5] Checking Docker daemon status...
where docker >nul 2>&1
if !ERRORLEVEL! neq 0 (
    echo [WARNING] Docker CLI not found in PATH.
    echo Sandboxed learner code execution requires Docker to run containers.
    echo Please ensure Docker Desktop is installed and added to PATH.
) else (
    docker info >nul 2>&1
    if !ERRORLEVEL! neq 0 (
        echo [WARNING] Docker daemon is not running.
        echo Please launch Docker Desktop to enable sandboxed learner code execution.
    ) else (
        echo Docker is running.
        REM 2. Check and build debugdojo-runner Docker image if missing
        echo [2/5] Checking debugdojo-runner image...
        docker image inspect debugdojo-runner >nul 2>&1
        if !ERRORLEVEL! neq 0 (
            echo Building debugdojo-runner Docker image...
            docker build -t debugdojo-runner docker/runner
            if !ERRORLEVEL! neq 0 (
                echo [ERROR] Failed to build debugdojo-runner image.
                exit /b 1
            )
            echo Image debugdojo-runner built successfully.
        ) else (
            echo Image debugdojo-runner is ready.
        )
    )
)

REM 3. Virtual environment and requirements
echo [3/5] Checking Python virtual environment...
if not exist ".venv" (
    echo Creating virtual environment in .venv...
    python -m venv .venv
    if !ERRORLEVEL! neq 0 (
        echo [ERROR] Failed to create virtual environment with 'python -m venv .venv'.
        exit /b 1
    )
    call .venv\Scripts\activate.bat
    echo Upgrading pip and installing backend dependencies...
    python -m pip install --quiet --upgrade pip
    pip install --quiet -r backend\requirements.txt
    if !ERRORLEVEL! neq 0 (
        echo [ERROR] Failed to install backend requirements.
        exit /b 1
    )
) else (
    call .venv\Scripts\activate.bat
)

REM 4. Environment configuration (.env)
echo [4/5] Checking environment configuration...
if not exist "backend\.env" (
    if exist "backend\.env.example" (
        echo Copying backend\.env.example to backend\.env...
        copy "backend\.env.example" "backend\.env" >nul
        echo.
        echo ********************************************************************************
        echo ATTENTION: backend\.env has been generated from backend\.env.example.
        echo To enable Gemini AI hints and explanations, set GEMINI_API_KEY in backend\.env:
        echo   GEMINI_API_KEY=your_gemini_api_key_here
        echo If omitted, Debug Dojo will safely use deterministic authored fallbacks.
        echo ********************************************************************************
        echo.
    ) else (
        echo [WARNING] backend\.env.example not found.
    )
) else (
    echo Configuration backend\.env exists.
)

REM 5. Frontend Dev Server (if present)
echo [5/5] Checking for frontend folder...
if exist "frontend\package.json" (
    echo Frontend detected at frontend\package.json.
    echo Launching Vite development server in a separate window...
    start "Debug Dojo Frontend" cmd /c "cd /d \"%CD%\frontend\" && if not exist node_modules npm install && npm run dev"
) else (
    echo No frontend/package.json found. Running backend API only.
)

REM 6. Start Uvicorn Server
echo.
echo ================================================================================
echo Debug Dojo Backend API starting on 0.0.0.0:8000
echo Local URL:     http://127.0.0.1:8000
echo Health URL:    http://127.0.0.1:8000/api/health
echo Documentation: http://127.0.0.1:8000/docs
echo Press CTRL+C to stop the server.
echo ================================================================================
echo.

python -m uvicorn backend.main:app --host 0.0.0.0 --port 8000
