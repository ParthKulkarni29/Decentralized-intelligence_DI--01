"""Red-team test suite: 20 adversarial learner programs evaluated against last-k-total."""

import json
from pathlib import Path
import shutil
import subprocess
import sys
import time
import pytest

from backend.engine.challenges import get_full_challenge
from backend.engine.grader import grade_submission, strip_for_api

REDTEAM_DIR = Path(__file__).resolve().parent
RUNNER_SCRIPT = REDTEAM_DIR.parent.parent.parent / "docker" / "runner" / "runner.py"

CORRECT_SOLUTION = """
def last_k_total(marks, k):
    if k <= 0 or not marks:
        return 0
    if k >= len(marks):
        return sum(marks)
    return sum(marks[-k:])
"""

ATTACKS = [
    ("01_infinite_loop.py", {"timeout"}),
    ("02_recursion_bomb.py", {"runtime_error"}),
    ("03_memory_bomb.py", {"runtime_error"}),
    ("04_output_flood.py", {"runtime_error"}),
    ("05_disk_flood.py", {"runtime_error", "wrong_output"}),
    ("06_read_passwd.py", {"wrong_output", "runtime_error"}),
    ("07_read_env.py", {"wrong_output", "runtime_error"}),
    ("08_network_socket.py", {"runtime_error", "wrong_output"}),
    ("09_fork_bomb.py", {"wrong_output", "runtime_error"}),
    ("10_shell_injection.py", {"wrong_output", "runtime_error"}),
    ("11_exit_import.py", {"runtime_error"}),
    ("12_exit_runtime.py", {"runtime_error"}),
    ("13_os_exit.py", {"runtime_error"}),
    ("14_fake_json.py", {"wrong_output"}),
    ("15_modify_runner.py", {"wrong_output", "runtime_error"}),
    ("16_monkeypatch_builtins.py", {"wrong_output", "runtime_error"}),
    ("17_kill_parent.py", {"wrong_output", "runtime_error"}),
    ("18_unserializable.py", {"runtime_error"}),
    ("19_hang_stdin.py", {"runtime_error", "timeout"}),
    ("20_thread_sleep.py", {"wrong_output", "runtime_error"}),
]


def _execute_via_runner_direct(code: str, function_name: str, tests: list[dict], per_test_timeout: float = 2.0) -> list[dict]:
    """Fallback runner execution for environments where Docker daemon is not installed on the host.

    Executes the exact docker/runner/runner.py script in isolated subprocesses.
    """
    payload = json.dumps({
        "code": code,
        "function_name": function_name,
        "tests": tests,
        "per_test_timeout": per_test_timeout,
    })
    proc = subprocess.run(
        [sys.executable, "-u", str(RUNNER_SCRIPT)],
        input=payload,
        text=True,
        capture_output=True,
        timeout=25.0,
    )
    if proc.returncode != 0:
        return [
            {"id": t["id"], "status": "error", "value": None, "exc_type": "RuntimeError", "limit": None, "mutated": False}
            for t in tests
        ]
    try:
        data = json.loads(proc.stdout)
        return data.get("results", [])
    except Exception:
        return [
            {"id": t["id"], "status": "error", "value": None, "exc_type": "RuntimeError", "limit": None, "mutated": False}
            for t in tests
        ]


@pytest.fixture(autouse=True)
def setup_sandbox_environment(monkeypatch):
    """Ensure tests run through Docker if available, otherwise bridge to runner.py subprocess."""
    docker_available = bool(shutil.which("docker"))
    if docker_available:
        try:
            res = subprocess.run(["docker", "info"], capture_output=True, timeout=2.0)
            if res.returncode != 0:
                docker_available = False
        except Exception:
            docker_available = False

    if not docker_available:
        # Bridge sandbox execution to the real runner engine script in isolated subprocess
        def sandbox_bridge(code, function_name, tests, per_test_timeout=2.0, total_timeout=20.0):
            return _execute_via_runner_direct(code, function_name, tests, per_test_timeout)

        monkeypatch.setattr("backend.engine.grader.run_in_sandbox", sandbox_bridge)


@pytest.mark.parametrize("filename, expected_outcomes", ATTACKS)
def test_redteam_attack(filename: str, expected_outcomes: set[str]):
    """Execute adversarial code against last-k-total and assert all 6 safety guarantees."""
    file_path = REDTEAM_DIR / filename
    assert file_path.exists(), f"Red-team attack file missing: {filename}"
    hostile_code = file_path.read_text(encoding="utf-8")

    challenge = get_full_challenge("last-k-total")

    # (c) Time budget: whole run finishes within 25 seconds
    start_time = time.time()
    result = grade_submission(challenge, hostile_code)
    duration = time.time() - start_time
    assert duration < 25.0, f"{filename} took {duration:.2f}s, exceeding 25s budget!"

    # (a) Never 'passed' on every hidden test
    assert result["all_passed"] is False, f"Hostile code {filename} unexpectedly passed all tests!"
    hidden_outcomes = {h["outcome"] for h in result["hidden"]}
    assert not all(h["outcome"] == "passed" for h in result["hidden"]), f"{filename} passed all hidden tests!"

    # (b) Expected outcome type must match
    all_outcomes = {t["outcome"] for t in result["visible"] + result["hidden"]}
    assert any(o in expected_outcomes for o in all_outcomes), (
        f"{filename} outcomes {all_outcomes} did not include any expected: {expected_outcomes}"
    )

    # (d) Next normal correct submission still passes all tests
    clean_result = grade_submission(challenge, CORRECT_SOLUTION)
    assert clean_result["all_passed"] is True, f"Subsequent clean submission failed after {filename}!"

    # (e) No hidden test input, expected value, host path, or env value leaks in API response
    api_facing = strip_for_api(result)
    serialized_api = json.dumps(api_facing)

    for hidden_test in challenge.hidden_tests:
        # Hidden category must never leak
        assert hidden_test["category"] not in serialized_api
        # Unique hidden args must not leak
        if hidden_test["category"] == "single_element":
            assert "[100]" not in serialized_api
        if hidden_test["category"] == "k_larger_than_list":
            assert "[2, 5], 5" not in serialized_api

    # No host paths or sensitive tokens
    assert "C:\\" not in serialized_api
    assert "OneDrive" not in serialized_api
    assert "GEMINI_API_KEY" not in serialized_api

    # (f) Check for leftover containers if docker is in use
    if shutil.which("docker"):
        ps_proc = subprocess.run(
            ["docker", "ps", "-a", "--filter", "name=debugdojo-run", "--format", "{{.Names}}"],
            capture_output=True,
            text=True,
            check=False,
        )
        assert not ps_proc.stdout.strip(), f"Leftover containers found after {filename}: {ps_proc.stdout}"
