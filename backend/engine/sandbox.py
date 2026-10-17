"""Docker sandbox runner for executing untrusted learner code.

Spawns debugdojo-runner inside a hardened Docker container, limits concurrency
with a global semaphore, enforces timeouts, and guarantees container cleanup.
"""

from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
import shutil
import subprocess
import threading
import uuid
from typing import Any, Optional
from backend.config import get_settings

logger = logging.getLogger("debugdojo.sandbox")

CONTAINER_PREFIX = "debugdojo-run-"

# Global concurrency control: limit simultaneous running containers
settings = get_settings()
_CONCURRENCY_SEMAPHORE = threading.Semaphore(settings.MAX_PARALLEL_RUNS)
QUEUE_TIMEOUT_SECONDS = float(os.getenv("SANDBOX_QUEUE_TIMEOUT", "5.0"))
MAX_STDOUT_CAPTURE_BYTES = 2 * 1024 * 1024  # 2 MB safety cap on docker stdout


class SandboxBusyError(Exception):
    """Raised when every sandbox slot is busy and the queue timeout passes."""
    pass


def cleanup_stale_containers() -> int:
    """Remove any stale containers on server startup whose names start with the sandbox prefix.

    WHY:
    - Guarantees no orphaned containers from previous crashes or interrupted runs linger.
    - Uses docker rm -f on matching container names and logs how many were removed.
    """
    docker_bin = shutil.which("docker")
    if not docker_bin:
        logger.info("Docker binary not found on host; skipping startup stale container cleanup.")
        return 0

    try:
        proc = subprocess.run(
            ["docker", "ps", "-a", "--filter", f"name={CONTAINER_PREFIX}", "--format", "{{.Names}}"],
            capture_output=True,
            text=True,
            timeout=10.0,
            check=False,
        )
        if proc.returncode != 0:
            logger.warning(
                "docker ps failed with code %d during startup cleanup: %s",
                proc.returncode,
                proc.stderr.strip(),
            )
            return 0

        names = [
            line.strip().lstrip("/")
            for line in proc.stdout.splitlines()
            if line.strip() and line.strip().lstrip("/").startswith(CONTAINER_PREFIX)
        ]

        if not names:
            logger.info("Startup container cleanup: zero stale sandbox containers found.")
            return 0

        logger.info("Startup container cleanup: found %d stale container(s) to remove: %s", len(names), names)
        rm_proc = subprocess.run(
            ["docker", "rm", "-f"] + names,
            capture_output=True,
            text=True,
            timeout=20.0,
            check=False,
        )
        if rm_proc.returncode != 0:
            logger.warning(
                "docker rm -f returned code %d during cleanup: %s",
                rm_proc.returncode,
                rm_proc.stderr.strip(),
            )

        logger.info("Startup container cleanup: successfully removed %d stale container(s).", len(names))
        return len(names)
    except Exception as exc:
        logger.error("Exception during startup stale container cleanup: %s", type(exc).__name__)
        return 0


def cleanup_old_containers(max_age_seconds: float = 60.0) -> int:
    """Safety net: remove sandbox containers older than max_age_seconds (default 60s).

    WHY:
    - Individual tests time out after 2-20s. Any container exceeding 60s is leaked.
    - Background task queries creation timestamps and force-removes zombies.
    """
    docker_bin = shutil.which("docker")
    if not docker_bin:
        return 0

    try:
        proc = subprocess.run(
            ["docker", "ps", "-a", "--filter", f"name={CONTAINER_PREFIX}", "--format", "{{.Names}}"],
            capture_output=True,
            text=True,
            timeout=10.0,
            check=False,
        )
        if proc.returncode != 0:
            return 0

        names = [
            line.strip().lstrip("/")
            for line in proc.stdout.splitlines()
            if line.strip() and line.strip().lstrip("/").startswith(CONTAINER_PREFIX)
        ]
        if not names:
            return 0

        insp_proc = subprocess.run(
            ["docker", "inspect", "-f", "{{.Name}}\t{{.Created}}"] + names,
            capture_output=True,
            text=True,
            timeout=15.0,
            check=False,
        )
        if insp_proc.returncode != 0:
            return 0

        now = datetime.now(timezone.utc)
        to_remove: list[str] = []

        for line in insp_proc.stdout.splitlines():
            line = line.strip()
            if not line or "\t" not in line:
                continue
            name, created_str = line.split("\t", 1)
            name = name.strip().lstrip("/")
            created_str = created_str.strip()

            try:
                # Normalize nanoseconds for datetime.fromisoformat
                clean_ts = created_str
                if "." in clean_ts:
                    dot_idx = clean_ts.index(".")
                    z_idx = clean_ts.find("Z", dot_idx)
                    plus_idx = clean_ts.find("+", dot_idx)
                    end_idx = z_idx if z_idx != -1 else (plus_idx if plus_idx != -1 else len(clean_ts))
                    frac = clean_ts[dot_idx + 1:end_idx][:6]
                    suffix = clean_ts[end_idx:]
                    clean_ts = clean_ts[:dot_idx + 1] + frac + suffix
                if clean_ts.endswith("Z"):
                    clean_ts = clean_ts[:-1] + "+00:00"
                created_dt = datetime.fromisoformat(clean_ts)
                if (now - created_dt).total_seconds() > max_age_seconds:
                    to_remove.append(name)
            except Exception:
                continue

        if not to_remove:
            return 0

        subprocess.run(
            ["docker", "rm", "-f"] + to_remove,
            capture_output=True,
            text=True,
            timeout=20.0,
            check=False,
        )
        logger.info(
            "Background cleanup removed %d container(s) older than %ss: %s",
            len(to_remove),
            max_age_seconds,
            to_remove,
        )
        return len(to_remove)
    except Exception as exc:
        logger.warning("Error during background container cleanup: %s", type(exc).__name__)
        return 0


def run_in_sandbox(
    code: str,
    function_name: str,
    tests: list[dict[str, Any]],
    per_test_timeout: float = 2.0,
    total_timeout: float = 20.0,
) -> list[dict[str, Any]]:
    """Execute learner code across tests inside an isolated debugdojo-runner container.

    Args:
        code: Learner source code.
        function_name: Target function name to execute.
        tests: List of dicts with 'id', 'args', and 'capture_stdout'.
        per_test_timeout: Seconds per individual test case.
        total_timeout: Overall wall-clock timeout for the container.

    Returns:
        List of test result dicts. On any container crash, timeout, or error,
        tests map to runtime_error or timeout—NEVER passed.
    """
    current_settings = get_settings()

    # 1. Check sandbox mode
    if current_settings.SANDBOX_MODE == "disabled":
        logger.warning("Sandbox execution is explicitly disabled (SANDBOX_MODE=disabled)")
        return [
            {
                "id": t["id"],
                "status": "error",
                "value": None,
                "exc_type": "RuntimeError",
                "limit": None,
                "mutated": False,
                "error_note": "Sandbox execution is disabled",
            }
            for t in tests
        ]

    if current_settings.SANDBOX_MODE != "docker":
        logger.error("Unsupported sandbox mode: %s. Only 'docker' is supported.", current_settings.SANDBOX_MODE)
        return [
            {
                "id": t["id"],
                "status": "error",
                "value": None,
                "exc_type": "RuntimeError",
                "limit": None,
                "mutated": False,
                "error_note": f"Unsupported sandbox mode: {current_settings.SANDBOX_MODE}",
            }
            for t in tests
        ]

    # 2. Check Docker availability on host
    docker_bin = shutil.which("docker")
    if not docker_bin:
        logger.error("Docker executable not found on host. Refusing to run untrusted code unsandboxed.")
        return [
            {
                "id": t["id"],
                "status": "error",
                "value": None,
                "exc_type": "RuntimeError",
                "limit": None,
                "mutated": False,
                "error_note": "Docker daemon or CLI is unavailable on host",
            }
            for t in tests
        ]

    # 3. Acquire concurrency semaphore with queue timeout
    acquired = _CONCURRENCY_SEMAPHORE.acquire(timeout=QUEUE_TIMEOUT_SECONDS)
    if not acquired:
        logger.error(
            "Concurrency limit reached (%d runs). Request queued out after %ss.",
            current_settings.MAX_PARALLEL_RUNS,
            QUEUE_TIMEOUT_SECONDS,
        )
        raise SandboxBusyError("All sandbox execution slots are busy")

    container_name = f"{CONTAINER_PREFIX}{uuid.uuid4().hex[:12]}"
    payload = json.dumps({
        "code": code,
        "function_name": function_name,
        "tests": tests,
        "per_test_timeout": per_test_timeout,
    }).encode("utf-8")

    # Hardened Docker execution flags
    docker_cmd = [
        "docker",
        "run",
        "-i",
        "--name",
        container_name,
        "--network",
        "none",
        "--memory",
        "128m",
        "--cpus",
        "0.5",
        "--pids-limit",
        "64",
        "--read-only",
        "--tmpfs",
        "/tmp:rw,size=8m",
        "--user",
        "10001",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges",
        "debugdojo-runner",
    ]

    timed_out = False
    stdout_output: bytes = b""

    try:
        # Start container process (list of args, never shell=True)
        proc = subprocess.Popen(
            docker_cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            close_fds=True,
        )

        try:
            stdout_output, stderr_output = proc.communicate(
                input=payload,
                timeout=total_timeout,
            )
        except subprocess.TimeoutExpired:
            timed_out = True
            logger.warning("Sandbox run exceeded total timeout of %ss. Killing container %s.", total_timeout, container_name)
            try:
                proc.kill()
            except Exception:
                pass
            try:
                proc.communicate(timeout=2.0)
            except Exception:
                pass

    except Exception as exc:
        logger.error("Failed to execute docker run: %s", type(exc).__name__)
        return [
            {
                "id": t["id"],
                "status": "error",
                "value": None,
                "exc_type": type(exc).__name__,
                "limit": None,
                "mutated": False,
                "error_note": "Failed to launch container",
            }
            for t in tests
        ]
    finally:
        # ALWAYS force-remove the container to prevent orphan leaks
        try:
            subprocess.run(
                ["docker", "rm", "-f", container_name],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
        except Exception as rm_exc:
            logger.warning("Cleanup docker rm -f %s encountered: %s", container_name, type(rm_exc).__name__)

        # Release concurrency semaphore
        _CONCURRENCY_SEMAPHORE.release()

    if timed_out:
        return [
            {
                "id": t["id"],
                "status": "timeout",
                "value": None,
                "exc_type": None,
                "limit": "time",
                "mutated": False,
            }
            for t in tests
        ]

    # Parse container output
    try:
        raw_text = stdout_output[:MAX_STDOUT_CAPTURE_BYTES].decode("utf-8", errors="replace").strip()
        data = json.loads(raw_text)
        results = data.get("results")
        if isinstance(results, list) and len(results) == len(tests):
            return results
    except Exception as parse_exc:
        logger.warning("Failed to parse sandbox output JSON: %s", type(parse_exc).__name__)

    # Any malformed output or premature container death maps to runtime_error, NEVER passed
    return [
        {
            "id": t["id"],
            "status": "error",
            "value": None,
            "exc_type": "RuntimeError",
            "limit": None,
            "mutated": False,
            "error_note": "Malformed sandbox response or container crashed",
        }
        for t in tests
    ]
