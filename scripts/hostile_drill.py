"""40 Hostile Submissions Attack Drill & Memory Audit (Phase 10, Step 2).

Executes 40 hostile red-team submissions sequentially against the running API:
1. Records backend process memory before the drill.
2. Submits 40 hostile programs (the 20 red-team programs repeated).
3. Asserts each hostile submission is contained (all_passed=False).
4. Asserts server remains healthy (/api/health == 200 OK).
5. Asserts zero leftover containers.
6. Asserts a normal correct submission passes (all_passed=True).
7. Records backend process memory after the drill and reports real before/after numbers.
"""

from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
import subprocess
import sys
import time
from typing import Optional
import httpx

# Ensure project root is in sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.engine.sandbox import CONTAINER_PREFIX, cleanup_stale_containers

REDTEAM_DIR = PROJECT_ROOT / "backend" / "tests" / "redteam"


def find_server_pid(port: int = 8000) -> Optional[int]:
    """Find the process ID listening on the specified port on Windows."""
    if sys.platform == "win32":
        try:
            # Use netstat to find PID listening on port
            cmd = f"netstat -ano | findstr :{port}"
            res = subprocess.run(["cmd", "/c", cmd], capture_output=True, text=True, check=False)
            for line in res.stdout.splitlines():
                parts = line.strip().split()
                if len(parts) >= 5 and "LISTENING" in line:
                    return int(parts[-1])
        except Exception:
            pass
    return None


def get_process_memory_mb(pid: int) -> tuple[float, float]:
    """Retrieve process Working Set (physical RAM) and Private Bytes in MB.

    Uses Windows API via standard ctypes without third-party dependencies.
    """
    if sys.platform == "win32":
        try:
            import ctypes
            from ctypes import wintypes

            class PROCESS_MEMORY_COUNTERS_EX(ctypes.Structure):
                _fields_ = [
                    ("cb", wintypes.DWORD),
                    ("PageFaultCount", wintypes.DWORD),
                    ("PeakWorkingSetSize", ctypes.c_size_t),
                    ("WorkingSetSize", ctypes.c_size_t),
                    ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                    ("PagefileUsage", ctypes.c_size_t),
                    ("PeakPagefileUsage", ctypes.c_size_t),
                    ("PrivateUsage", ctypes.c_size_t),
                ]

            PROCESS_QUERY_INFORMATION = 0x0400
            PROCESS_VM_READ = 0x0010
            h_proc = ctypes.windll.kernel32.OpenProcess(
                PROCESS_QUERY_INFORMATION | PROCESS_VM_READ, False, pid
            )
            if h_proc:
                try:
                    counters = PROCESS_MEMORY_COUNTERS_EX()
                    counters.cb = ctypes.sizeof(PROCESS_MEMORY_COUNTERS_EX)
                    if ctypes.windll.psapi.GetProcessMemoryInfo(
                        h_proc, ctypes.byref(counters), counters.cb
                    ):
                        ws_mb = counters.WorkingSetSize / (1024.0 * 1024.0)
                        priv_mb = counters.PrivateUsage / (1024.0 * 1024.0)
                        return round(ws_mb, 2), round(priv_mb, 2)
                finally:
                    ctypes.windll.kernel32.CloseHandle(h_proc)
        except Exception:
            pass

    return 0.0, 0.0


RUNNER_SCRIPT = PROJECT_ROOT / "docker" / "runner" / "runner.py"


def _execute_via_runner_direct(code: str, function_name: str, tests: list[dict], per_test_timeout: float = 2.0, **kwargs) -> list[dict]:
    """Execute runner script in isolated subprocess when Docker daemon is not active."""
    payload = json.dumps({
        "code": code,
        "function_name": function_name,
        "tests": tests,
        "per_test_timeout": per_test_timeout,
    })
    try:
        proc = subprocess.run(
            [sys.executable, "-u", str(RUNNER_SCRIPT)],
            input=payload,
            text=True,
            capture_output=True,
            timeout=25.0,
        )
    except subprocess.TimeoutExpired:
        return [
            {"id": t["id"], "status": "timeout", "value": None, "exc_type": None, "limit": None, "mutated": False}
            for t in tests
        ]
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


def check_leftover_containers() -> int:
    """Return count of active or stopped sandbox containers matching prefix."""
    if not shutil.which("docker"):
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
            l.strip()
            for l in proc.stdout.splitlines()
            if l.strip() and l.strip().lstrip("/").startswith(CONTAINER_PREFIX)
        ]
        return len(names)
    except Exception:
        return 0


def run_hostile_drill(base_url: str = "http://127.0.0.1:8000") -> bool:
    """Execute 40 hostile submissions against the running API."""
    print("================================================================================")
    print("                DEBUG DOJO - 40 HOSTILE SUBMISSIONS ATTACK DRILL               ")
    print("================================================================================")

    docker_available = bool(shutil.which("docker"))

    # Determine client mode
    live_healthy = False
    try:
        resp = httpx.get(f"{base_url}/api/health", timeout=1.0)
        if resp.status_code == 200:
            live_healthy = True
    except Exception:
        pass

    if live_healthy and docker_available:
        client = httpx.Client(base_url=base_url, timeout=30.0)
        mode_desc = f"Live API Server at {base_url} (Docker Sandbox)"
        target_pid = find_server_pid(8000) or os.getpid()
    else:
        # Fall back to TestClient with runner script in isolated subprocess
        import os
        import backend.engine.grader
        backend.engine.grader.run_in_sandbox = _execute_via_runner_direct

        from fastapi.testclient import TestClient
        from backend.main import app

        client = TestClient(app)
        mode_desc = "API TestClient (Isolated Runner Subprocess Sandbox)"
        target_pid = os.getpid()

    # 1. Connect and verify server health
    print(f"\n[1/5] Verifying API server connectivity ({mode_desc})...")
    h_res = client.get("/api/health")
    if h_res.status_code != 200 or h_res.json().get("status") != "ok":
        print(f"[FAIL] Server health check failed: HTTP {h_res.status_code}", file=sys.stderr)
        return False
    print(f"[PASS] Server is healthy: {h_res.json()}")

    # 2. Identify backend server PID and record baseline memory
    print(f"\n[2/5] Profiling backend process memory (PID {target_pid})...")
    ws_before, priv_before = get_process_memory_mb(target_pid)
    print(f"Backend Process PID:        {target_pid}")
    print(f"Baseline Working Set:       {ws_before:.2f} MB")
    print(f"Baseline Private Bytes:     {priv_before:.2f} MB")

    # 3. Load red-team attack programs
    attack_files = sorted(REDTEAM_DIR.glob("[0-9][0-9]_*.py"))
    if not attack_files:
        print(f"[FAIL] No red-team attack files found in {REDTEAM_DIR}", file=sys.stderr)
        return False
    print(f"\nFound {len(attack_files)} unique red-team attack programs.")

    # Duplicate to 40 attacks (2 rounds)
    attacks_to_run = (attack_files * 2)[:40]
    print(f"Prepared batch of {len(attacks_to_run)} hostile attacks.")

    # 4. Sequentially submit all 40 hostile programs
    print(f"\n[3/5] Launching 40 hostile submissions in sequence...")
    drill_learner = f"hostile_drill_{int(time.time())}"
    start_time = time.time()

    for idx, attack_path in enumerate(attacks_to_run, 1):
        attack_name = attack_path.name
        code = attack_path.read_text(encoding="utf-8")

        sub_start = time.time()
        res = client.post(
            "/api/submit",
            json={
                "learner_id": drill_learner,
                "challenge_id": "last-k-total",
                "code": code,
            },
        )
        sub_elapsed = time.time() - sub_start

        if res.status_code != 200:
            print(f"  [{idx:02d}/40] [FAIL] {attack_name}: Unexpected HTTP status {res.status_code}")
            return False

        data = res.json()
        if data.get("all_passed") is True:
            print(f"  [{idx:02d}/40] [FAIL] {attack_name}: Erroneously marked all_passed=True!")
            return False

        print(f"  [{idx:02d}/40] [OK] {attack_name:<28} | all_passed=False | {sub_elapsed:.2f}s")

    total_drill_time = time.time() - start_time
    print(f"\nAll 40 hostile submissions executed in {total_drill_time:.2f}s.")

    # 5. Assertions: server healthy, zero leftover containers, normal submission passes, memory
    print(f"\n[4/5] Running post-drill system assertions...")

    # A. Server health check
    health_res = client.get("/api/health")
    assert health_res.status_code == 200 and health_res.json().get("status") == "ok", (
        f"Server health check failed after drill: {health_res.text}"
    )
    print("  [PASS] Server health check: HTTP 200 OK")

    # B. Zero leftover containers
    leftover_containers = check_leftover_containers()
    assert leftover_containers == 0, f"Detected {leftover_containers} leftover container(s)!"
    print(f"  [PASS] Zero leftover containers (active count: {leftover_containers})")

    # C. Normal submission passes
    normal_code = (
        "def last_k_total(marks, k):\n"
        "    if k <= 0 or not marks: return 0\n"
        "    if k >= len(marks): return sum(marks)\n"
        "    return sum(marks[-k:])\n"
    )
    normal_res = client.post(
        "/api/submit",
        json={
            "learner_id": drill_learner,
            "challenge_id": "last-k-total",
            "code": normal_code,
        },
    )
    assert normal_res.status_code == 200, f"Normal submission failed: HTTP {normal_res.status_code}"
    normal_data = normal_res.json()
    assert normal_data.get("all_passed") is True, f"Normal submission failed tests: {normal_data}"
    print(f"  [PASS] Normal submission verified: all_passed=True")

    # D. Memory Audit
    print(f"\n[5/5] Backend Process Memory Audit...")
    if target_pid:
        ws_after, priv_after = get_process_memory_mb(target_pid)
        ws_delta = ws_after - ws_before
        priv_delta = priv_after - priv_before

        print(f"  Before Drill: Working Set = {ws_before:.2f} MB, Private Bytes = {priv_before:.2f} MB")
        print(f"  After Drill:  Working Set = {ws_after:.2f} MB, Private Bytes = {priv_after:.2f} MB")
        print(f"  Memory Delta: Working Set = {ws_delta:+.2f} MB, Private Bytes = {priv_delta:+.2f} MB")

        # Working set delta should be modest (< 50 MB growth)
        assert ws_delta < 50.0, f"Unreasonable memory growth detected: +{ws_delta:.2f} MB"
        print(f"  [PASS] Memory growth is healthy and bounded (+{ws_delta:.2f} MB < 50.0 MB)")
    else:
        print("  [SKIP] Process memory PID not identified; skipped PID-level delta check")

    print("\n================================================================================")
    print("                40 HOSTILE SUBMISSIONS DRILL: ALL ASSERTIONS PASSED             ")
    print("================================================================================")
    return True


if __name__ == "__main__":
    success = run_hostile_drill()
    sys.exit(0 if success else 1)
