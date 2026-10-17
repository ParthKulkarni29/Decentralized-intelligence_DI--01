"""Development server runner script for Debug Dojo.

Starts uvicorn on port 8000 with auto-reload.
"""

from pathlib import Path
import sys
import uvicorn

# Ensure repository root is in sys.path so 'backend' package is importable
ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

if __name__ == "__main__":
    uvicorn.run(
        "backend.main:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
    )
