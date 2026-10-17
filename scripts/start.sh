#!/usr/bin/env bash
set -e

# ============================================================================
# Debug Dojo Unified Startup Script (Linux / macOS)
# Starts Backend API (FastAPI/Uvicorn) and Frontend Dev Server (if present)
# ============================================================================

# Ensure working directory is project root
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$DIR"
echo "[Debug Dojo] Starting system from: $PWD"

# 1. Check Docker status
echo "[1/5] Checking Docker daemon status..."
if ! command -v docker >/dev/null 2>&1; then
    echo "[WARNING] Docker CLI not found in PATH."
    echo "Sandboxed learner code execution requires Docker to run containers."
    echo "Please install Docker and ensure it is in PATH."
else
    if ! docker info >/dev/null 2>&1; then
        echo "[WARNING] Docker daemon is not running."
        echo "Please start the Docker daemon to enable sandboxed learner code execution."
    else
        echo "Docker is running."
        # 2. Check and build debugdojo-runner image if missing
        echo "[2/5] Checking debugdojo-runner image..."
        if ! docker image inspect debugdojo-runner >/dev/null 2>&1; then
            echo "Building debugdojo-runner Docker image..."
            docker build -t debugdojo-runner docker/runner
            echo "Image debugdojo-runner built successfully."
        else
            echo "Image debugdojo-runner is ready."
        fi
    fi
fi

# 3. Virtual environment and requirements
echo "[3/5] Checking Python virtual environment..."
if [ ! -d ".venv" ]; then
    echo "Creating virtual environment in .venv..."
    python3 -m venv .venv
    source .venv/bin/activate
    echo "Upgrading pip and installing backend dependencies..."
    pip install --quiet --upgrade pip
    pip install --quiet -r backend/requirements.txt
else
    source .venv/bin/activate
fi

# 4. Environment configuration (.env)
echo "[4/5] Checking environment configuration..."
if [ ! -f "backend/.env" ]; then
    if [ -f "backend/.env.example" ]; then
        echo "Copying backend/.env.example to backend/.env..."
        cp backend/.env.example backend/.env
        echo ""
        echo "********************************************************************************"
        echo "ATTENTION: backend/.env has been generated from backend/.env.example."
        echo "To enable Gemini AI hints and explanations, set GEMINI_API_KEY in backend/.env:"
        echo "  GEMINI_API_KEY=your_gemini_api_key_here"
        echo "If omitted, Debug Dojo will safely use deterministic authored fallbacks."
        echo "********************************************************************************"
        echo ""
    else
        echo "[WARNING] backend/.env.example not found."
    fi
else
    echo "Configuration backend/.env exists."
fi

# 5. Frontend Dev Server (if present)
echo "[5/5] Checking for frontend folder..."
if [ -f "frontend/package.json" ]; then
    echo "Frontend detected at frontend/package.json."
    echo "Launching Vite development server..."
    if command -v gnome-terminal >/dev/null 2>&1; then
        gnome-terminal --title="Debug Dojo Frontend" -- bash -c "cd '$PWD/frontend' && [ ! -d node_modules ] && npm install; npm run dev; exec bash"
    elif command -v xterm >/dev/null 2>&1; then
        xterm -title "Debug Dojo Frontend" -e "cd '$PWD/frontend' && [ ! -d node_modules ] && npm install && npm run dev" &
    else
        (cd "$PWD/frontend" && [ ! -d node_modules ] && npm install && npm run dev) &
    fi
else
    echo "No frontend/package.json found. Running backend API only."
fi

# 6. Start Uvicorn Server
echo ""
echo "================================================================================"
echo "Debug Dojo Backend API starting on 0.0.0.0:8000"
echo "Local URL:     http://127.0.0.1:8000"
echo "Health URL:    http://127.0.0.1:8000/api/health"
echo "Documentation: http://127.0.0.1:8000/docs"
echo "Press CTRL+C to stop the server."
echo "================================================================================"
echo ""

exec python -m uvicorn backend.main:app --host 0.0.0.0 --port 8000
