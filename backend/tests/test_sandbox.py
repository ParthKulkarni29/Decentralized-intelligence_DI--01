"""Unit tests for sandbox isolation, semaphore limits, and error handling."""

import subprocess
import pytest
from backend.config import Settings
from backend.engine.sandbox import run_in_sandbox


def test_sandbox_disabled_mode_refuses_runs(monkeypatch):
    """Verify SANDBOX_MODE=disabled refuses execution and maps all tests to error."""
    mock_settings = Settings(
        GEMINI_API_KEY="",
        GEMINI_MODEL="",
        DATABASE_PATH="backend/debugdojo.db",
        SANDBOX_MODE="disabled",
        MAX_PARALLEL_RUNS=3,
        CORS_ORIGINS=["http://localhost:5173"],
    )
    monkeypatch.setattr("backend.engine.sandbox.get_settings", lambda: mock_settings)

    tests = [{"id": 1, "args": [1, 2], "capture_stdout": False}]
    results = run_in_sandbox("def f(): pass", "f", tests)

    assert len(results) == 1
    assert results[0]["status"] == "error"
    assert results[0]["exc_type"] == "RuntimeError"
    assert "disabled" in results[0]["error_note"].lower()


def test_docker_unavailable_returns_clear_internal_error(monkeypatch):
    """Verify missing Docker executable returns clear error and never falls back to local execution."""
    mock_settings = Settings(
        GEMINI_API_KEY="",
        GEMINI_MODEL="",
        DATABASE_PATH="backend/debugdojo.db",
        SANDBOX_MODE="docker",
        MAX_PARALLEL_RUNS=3,
        CORS_ORIGINS=["http://localhost:5173"],
    )
    monkeypatch.setattr("backend.engine.sandbox.get_settings", lambda: mock_settings)
    monkeypatch.setattr("shutil.which", lambda prog: None)

    tests = [
        {"id": 1, "args": [1], "capture_stdout": False},
        {"id": 2, "args": [2], "capture_stdout": False},
    ]
    results = run_in_sandbox("def f(): return 1", "f", tests)

    assert len(results) == 2
    for r in results:
        assert r["status"] == "error"
        assert r["exc_type"] == "RuntimeError"
        assert "unavailable" in r["error_note"].lower()


def test_malformed_docker_output_never_passes(monkeypatch):
    """Verify invalid stdout from docker maps to runtime_error, never passed."""
    mock_settings = Settings(
        GEMINI_API_KEY="",
        GEMINI_MODEL="",
        DATABASE_PATH="backend/debugdojo.db",
        SANDBOX_MODE="docker",
        MAX_PARALLEL_RUNS=3,
        CORS_ORIGINS=["http://localhost:5173"],
    )
    monkeypatch.setattr("backend.engine.sandbox.get_settings", lambda: mock_settings)
    monkeypatch.setattr("shutil.which", lambda prog: "/usr/bin/docker")

    # Mock Popen to return malformed output and verify cleanup was called
    cleanup_called = False

    class MockPopen:
        def __init__(self, *args, **kwargs):
            pass

        def communicate(self, *args, **kwargs):
            return b"Garbage Non-JSON Container Output", b""

    def mock_run_cmd(cmd, *args, **kwargs):
        nonlocal cleanup_called
        if "rm" in cmd and "-f" in cmd:
            cleanup_called = True
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr("subprocess.Popen", MockPopen)
    monkeypatch.setattr("subprocess.run", mock_run_cmd)

    tests = [{"id": 1, "args": [], "capture_stdout": False}]
    results = run_in_sandbox("def f(): pass", "f", tests)

    assert len(results) == 1
    assert results[0]["status"] == "error"
    assert results[0]["exc_type"] == "RuntimeError"
    assert cleanup_called is True, "Docker force-cleanup was not invoked!"


def test_container_timeout_maps_to_timeout(monkeypatch):
    """Verify total timeout maps all tests to status timeout."""
    mock_settings = Settings(
        GEMINI_API_KEY="",
        GEMINI_MODEL="",
        DATABASE_PATH="backend/debugdojo.db",
        SANDBOX_MODE="docker",
        MAX_PARALLEL_RUNS=3,
        CORS_ORIGINS=["http://localhost:5173"],
    )
    monkeypatch.setattr("backend.engine.sandbox.get_settings", lambda: mock_settings)
    monkeypatch.setattr("shutil.which", lambda prog: "/usr/bin/docker")

    class TimeoutPopen:
        def __init__(self, *args, **kwargs):
            pass

        def communicate(self, *args, **kwargs):
            raise subprocess.TimeoutExpired(cmd="docker", timeout=2.0)

        def kill(self):
            pass

    monkeypatch.setattr("subprocess.Popen", TimeoutPopen)
    monkeypatch.setattr("subprocess.run", lambda *args, **kwargs: subprocess.CompletedProcess([], 0))

    tests = [{"id": 1, "args": [], "capture_stdout": False}]
    results = run_in_sandbox("while True: pass", "f", tests, total_timeout=0.1)

    assert len(results) == 1
    assert results[0]["status"] == "timeout"
    assert results[0]["limit"] == "time"
