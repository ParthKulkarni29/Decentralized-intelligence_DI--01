"""Unit tests for docker/runner/runner.py execution engine."""

import json
from pathlib import Path
import subprocess
import sys
import pytest

RUNNER_SCRIPT = Path(__file__).resolve().parent.parent.parent / "docker" / "runner" / "runner.py"


def _run_runner(payload: dict) -> dict:
    """Helper to execute runner.py with a JSON payload via stdin."""
    proc = subprocess.run(
        [sys.executable, "-u", str(RUNNER_SCRIPT)],
        input=json.dumps(payload),
        text=True,
        capture_output=True,
    )
    assert proc.returncode == 0, f"Runner failed: {proc.stderr}"
    return json.loads(proc.stdout)


def test_runner_executes_normal_function():
    """Verify runner executes a valid function and captures stdout when requested."""
    payload = {
        "code": "def add(a, b):\n    print('calculating')\n    return a + b\n",
        "function_name": "add",
        "tests": [
            {"id": 1, "args": [10, 25], "capture_stdout": True},
            {"id": 2, "args": [0, -5], "capture_stdout": False},
        ],
        "per_test_timeout": 2.0,
    }
    result = _run_runner(payload)
    results = result["results"]
    assert len(results) == 2

    assert results[0]["id"] == 1
    assert results[0]["status"] == "ok"
    assert results[0]["value"] == 35
    assert "calculating" in results[0]["stdout"]
    assert results[0]["mutated"] is False

    assert results[1]["id"] == 2
    assert results[1]["status"] == "ok"
    assert results[1]["value"] == -5
    assert "stdout" not in results[1]  # Discarded when capture_stdout is False


def test_runner_handles_exceptions_securely():
    """SECURITY TEST: Verify exception returns type name only, no message or traceback."""
    payload = {
        "code": "def divide(a, b):\n    return a / b\n",
        "function_name": "divide",
        "tests": [
            {"id": 1, "args": [10, 0], "capture_stdout": False},
        ],
        "per_test_timeout": 2.0,
    }
    result = _run_runner(payload)
    item = result["results"][0]
    assert item["status"] == "error"
    assert item["exc_type"] == "ZeroDivisionError"
    assert item["value"] is None
    # Ensure no raw tracebacks or exception messages leak
    assert "division by zero" not in json.dumps(result)


def test_runner_detects_timeouts():
    """Verify infinite loop or slow function triggers timeout outcome."""
    payload = {
        "code": "import time\ndef spin():\n    time.sleep(2)\n",
        "function_name": "spin",
        "tests": [
            {"id": 1, "args": [], "capture_stdout": False},
        ],
        "per_test_timeout": 0.3,
    }
    result = _run_runner(payload)
    item = result["results"][0]
    assert item["status"] == "timeout"
    assert item["limit"] == "time"


def test_runner_detects_argument_mutation():
    """Verify runner correctly flags in-place argument mutation."""
    payload = {
        "code": "def pop_item(lst):\n    lst.pop()\n    return len(lst)\n",
        "function_name": "pop_item",
        "tests": [
            {"id": 1, "args": [[1, 2, 3]], "capture_stdout": False},
        ],
        "per_test_timeout": 2.0,
    }
    result = _run_runner(payload)
    item = result["results"][0]
    assert item["status"] == "ok"
    assert item["value"] == 2
    assert item["mutated"] is True


def test_runner_detects_non_serializable_return():
    """Verify non-JSON-serializable returns (like set) are reported as error with note."""
    payload = {
        "code": "def make_set():\n    return {1, 2, 3}\n",
        "function_name": "make_set",
        "tests": [
            {"id": 1, "args": [], "capture_stdout": False},
        ],
        "per_test_timeout": 2.0,
    }
    result = _run_runner(payload)
    item = result["results"][0]
    assert item["status"] == "error"
    assert item["exc_type"] == "TypeError"


def test_runner_enforces_output_limit():
    """Verify exceeding stdout byte cap kills child and reports output limit."""
    payload = {
        "code": "def loud():\n    print('A' * 65000)\n    return 1\n",
        "function_name": "loud",
        "tests": [
            {"id": 1, "args": [], "capture_stdout": True},
        ],
        "per_test_timeout": 2.0,
    }
    result = _run_runner(payload)
    item = result["results"][0]
    assert item["status"] == "error"
    assert item["limit"] == "output"
