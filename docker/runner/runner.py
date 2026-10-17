"""Isolated runner script for untrusted Python learner code.

Executes each test case in a sandboxed child process, enforces timeouts,
memory and output limits, detects argument mutation, and outputs structured JSON.
Standard library only. NEVER receives or outputs expected values.
"""

from argparse import ArgumentParser
import copy
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading
from typing import Any, Optional

MAX_INPUT_BYTES = 512 * 1024       # 512 KB maximum runner input payload
MAX_STDOUT_BYTES = 32 * 1024       # 32 KB stdout capture limit per test
MEMORY_LIMIT_BYTES = 128 * 1024 * 1024  # 128 MB memory limit


# ==============================================================================
# Windows Job Object Helper (Cross-Platform Fallback)
# ==============================================================================

def _setup_windows_memory_limit(proc_handle: int) -> Optional[int]:
    """Assign Windows process to a Job Object with 128MB memory cap."""
    if sys.platform != "win32":
        return None
    try:
        import ctypes
        from ctypes import wintypes

        class IO_COUNTERS(ctypes.Structure):
            _fields_ = [
                ("ReadOperationCount", ctypes.c_uint64),
                ("WriteOperationCount", ctypes.c_uint64),
                ("OtherOperationCount", ctypes.c_uint64),
                ("ReadTransferCount", ctypes.c_uint64),
                ("WriteTransferCount", ctypes.c_uint64),
                ("OtherTransferCount", ctypes.c_uint64),
            ]

        class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", wintypes.LARGE_INTEGER),
                ("PerJobUserTimeLimit", wintypes.LARGE_INTEGER),
                ("LimitFlags", wintypes.DWORD),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.c_size_t),
                ("PriorityClass", wintypes.DWORD),
                ("SchedulingClass", wintypes.DWORD),
            ]

        class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
            _fields_ = [
                ("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION),
                ("IoInfo", IO_COUNTERS),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t),
            ]

        JOB_OBJECT_LIMIT_PROCESS_MEMORY = 0x00000100
        JobObjectExtendedLimitInformation = 9

        kernel32 = ctypes.windll.kernel32
        job = kernel32.CreateJobObjectW(None, None)
        if not job:
            return None

        info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
        info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_PROCESS_MEMORY
        info.ProcessMemoryLimit = MEMORY_LIMIT_BYTES

        ret = kernel32.SetInformationJobObject(
            job,
            JobObjectExtendedLimitInformation,
            ctypes.byref(info),
            ctypes.sizeof(info),
        )
        if not ret:
            kernel32.CloseHandle(job)
            return None

        kernel32.AssignProcessToJobObject(job, proc_handle)
        return job
    except Exception:
        return None


# ==============================================================================
# Child Process Worker Mode
# ==============================================================================

def _apply_resource_limits() -> None:
    """Apply POSIX resource limits where available as defense in depth."""
    try:
        import resource  # Available on Linux/Unix inside the container
    except ImportError:
        return

    # Address space / virtual memory: 128 MB
    if hasattr(resource, "RLIMIT_AS"):
        try:
            resource.setrlimit(resource.RLIMIT_AS, (MEMORY_LIMIT_BYTES, MEMORY_LIMIT_BYTES))
        except (ValueError, OSError):
            pass

    # CPU time: 5 seconds hard cap per test
    if hasattr(resource, "RLIMIT_CPU"):
        try:
            resource.setrlimit(resource.RLIMIT_CPU, (5, 5))
        except (ValueError, OSError):
            pass

    # File write size: 1 MB cap
    if hasattr(resource, "RLIMIT_FSIZE"):
        try:
            resource.setrlimit(resource.RLIMIT_FSIZE, (1024 * 1024, 1024 * 1024))
        except (ValueError, OSError):
            pass

    # Number of processes / threads
    if hasattr(resource, "RLIMIT_NPROC"):
        try:
            resource.setrlimit(resource.RLIMIT_NPROC, (64, 64))
        except (ValueError, OSError):
            pass


def _write_result(result_dict: dict[str, Any], result_fd: Optional[int], result_file: Optional[str]) -> None:
    """Send structured result back to parent process."""
    data = json.dumps(result_dict)
    if result_fd is not None:
        with os.fdopen(result_fd, "w", encoding="utf-8") as f:
            f.write(data)
    elif result_file is not None:
        Path(result_file).write_text(data, encoding="utf-8")
    else:
        try:
            with os.fdopen(3, "w", encoding="utf-8") as f:
                f.write(data)
        except OSError:
            pass


def run_child_worker(result_fd: Optional[int], result_file: Optional[str]) -> None:
    """Execute learner code for one test case inside a sandboxed child process."""
    _apply_resource_limits()

    # Read test payload from child stdin
    try:
        raw_input = sys.stdin.read(MAX_INPUT_BYTES)
        payload = json.loads(raw_input)
    except Exception as exc:
        _write_result(
            {
                "status": "error",
                "value": None,
                "exc_type": type(exc).__name__,
                "limit": None,
                "mutated": False,
                "error_note": "Failed to read test payload in child",
            },
            result_fd,
            result_file,
        )
        return

    code = payload["code"]
    function_name = payload["function_name"]
    args = payload["args"]

    # Deep-copy original args to detect argument mutation
    try:
        original_args = copy.deepcopy(args)
    except Exception:
        original_args = None

    # Execute learner code in a clean namespace
    namespace: dict[str, Any] = {
        "__name__": "__main__",
        "__doc__": None,
        "__builtins__": __builtins__,
    }

    try:
        compiled_code = compile(code, "<learner_code>", "exec")
        exec(compiled_code, namespace)
    except MemoryError:
        _write_result(
            {
                "status": "error",
                "value": None,
                "exc_type": "MemoryError",
                "limit": "memory",
                "mutated": False,
            },
            result_fd,
            result_file,
        )
        return
    except BaseException as exc:
        _write_result(
            {
                "status": "error",
                "value": None,
                "exc_type": type(exc).__name__,
                "limit": None,
                "mutated": False,
            },
            result_fd,
            result_file,
        )
        return

    if function_name not in namespace or not callable(namespace[function_name]):
        _write_result(
            {
                "status": "error",
                "value": None,
                "exc_type": "NameError",
                "limit": None,
                "mutated": False,
                "error_note": f"Function '{function_name}' was not defined",
            },
            result_fd,
            result_file,
        )
        return

    # Call the target function
    target_func = namespace[function_name]
    try:
        return_value = target_func(*args)
    except MemoryError:
        _write_result(
            {
                "status": "error",
                "value": None,
                "exc_type": "MemoryError",
                "limit": "memory",
                "mutated": False,
            },
            result_fd,
            result_file,
        )
        return
    except BaseException as exc:
        _write_result(
            {
                "status": "error",
                "value": None,
                "exc_type": type(exc).__name__,
                "limit": None,
                "mutated": False,
            },
            result_fd,
            result_file,
        )
        return

    # Check argument mutation
    mutated = False
    if original_args is not None:
        try:
            mutated = (args != original_args)
        except Exception:
            mutated = False

    # Check return value JSON serializability
    try:
        json.dumps(return_value)
    except (TypeError, ValueError):
        _write_result(
            {
                "status": "error",
                "value": None,
                "exc_type": "TypeError",
                "limit": None,
                "mutated": mutated,
                "error_note": "return value not serialisable",
            },
            result_fd,
            result_file,
        )
        return

    _write_result(
        {
            "status": "ok",
            "value": return_value,
            "exc_type": None,
            "limit": None,
            "mutated": mutated,
        },
        result_fd,
        result_file,
    )


# ==============================================================================
# Parent Process Manager Mode
# ==============================================================================

def execute_single_test(
    code: str,
    function_name: str,
    test: dict[str, Any],
    per_test_timeout: float,
) -> dict[str, Any]:
    """Spawn an isolated child process for one test and collect the result."""
    test_id = test["id"]
    args = test.get("args", [])
    capture_stdout = bool(test.get("capture_stdout", False))

    child_payload = json.dumps({
        "code": code,
        "function_name": function_name,
        "args": args,
    }).encode("utf-8")

    use_pass_fds = (sys.platform != "win32")
    r_fd, w_fd = None, None
    temp_result_file = None

    if use_pass_fds:
        r_fd, w_fd = os.pipe()
        child_cmd = [
            sys.executable,
            "-u",
            __file__,
            "--child",
            "--result-fd",
            str(w_fd),
        ]
        pass_fds = (w_fd,)
    else:
        import tempfile
        tfile = tempfile.NamedTemporaryFile(delete=False, suffix=".json")
        temp_result_file = tfile.name
        tfile.close()
        child_cmd = [
            sys.executable,
            "-u",
            __file__,
            "--child",
            "--result-file",
            temp_result_file,
        ]
        pass_fds = ()

    start_new_session = (sys.platform != "win32")
    win_job_handle = None
    try:
        proc = subprocess.Popen(
            child_cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            close_fds=True,
            pass_fds=pass_fds,
            start_new_session=start_new_session,
        )
        if sys.platform == "win32" and hasattr(proc, "_handle"):
            win_job_handle = _setup_windows_memory_limit(int(proc._handle))
    finally:
        if w_fd is not None:
            os.close(w_fd)

    # Deliver test input to child and close its stdin
    try:
        proc.stdin.write(child_payload)
        proc.stdin.close()
    except (BrokenPipeError, OSError):
        pass

    # Read child stdout with hard byte cap in background thread
    stdout_chunks: list[bytes] = []
    stdout_bytes_read = 0
    output_limit_hit = False

    def capture_worker_stdout() -> None:
        nonlocal stdout_bytes_read, output_limit_hit
        try:
            while True:
                chunk = proc.stdout.read(4096)
                if not chunk:
                    break
                stdout_bytes_read += len(chunk)
                if stdout_bytes_read > MAX_STDOUT_BYTES:
                    output_limit_hit = True
                    try:
                        if sys.platform != "win32":
                            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
                        else:
                            proc.kill()
                    except Exception:
                        pass
                    break
                if capture_stdout:
                    stdout_chunks.append(chunk)
        except Exception:
            pass

    reader_thread = threading.Thread(target=capture_worker_stdout, daemon=True)
    reader_thread.start()

    # Wait for process with per-test timeout
    timed_out = False
    try:
        proc.wait(timeout=per_test_timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        try:
            if sys.platform != "win32":
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            else:
                proc.kill()
        except Exception:
            pass
        proc.wait()

    reader_thread.join(timeout=0.5)

    if win_job_handle:
        try:
            import ctypes
            ctypes.windll.kernel32.CloseHandle(win_job_handle)
        except Exception:
            pass

    # Read result from dedicated channel
    child_result: dict[str, Any] = {}
    if r_fd is not None:
        try:
            with os.fdopen(r_fd, "r", encoding="utf-8") as f:
                raw_res = f.read()
                if raw_res.strip():
                    child_result = json.loads(raw_res)
        except Exception:
            pass
    elif temp_result_file is not None:
        p = Path(temp_result_file)
        if p.exists():
            try:
                raw_res = p.read_text(encoding="utf-8")
                if raw_res.strip():
                    child_result = json.loads(raw_res)
            except Exception:
                pass
            try:
                p.unlink(missing_ok=True)
            except Exception:
                pass

    captured_stdout_str = b"".join(stdout_chunks).decode("utf-8", errors="replace") if capture_stdout else None

    if timed_out:
        res = {
            "id": test_id,
            "status": "timeout",
            "value": None,
            "exc_type": None,
            "limit": "time",
            "mutated": False,
        }
    elif output_limit_hit:
        res = {
            "id": test_id,
            "status": "error",
            "value": None,
            "exc_type": "RuntimeError",
            "limit": "output",
            "mutated": False,
        }
    elif proc.returncode != 0 and not child_result:
        limit = "memory" if proc.returncode in (-signal.SIGKILL if hasattr(signal, "SIGKILL") else -9, -11) else None
        res = {
            "id": test_id,
            "status": "error",
            "value": None,
            "exc_type": "RuntimeError",
            "limit": limit,
            "mutated": False,
        }
    elif child_result:
        res = {
            "id": test_id,
            "status": child_result.get("status", "error"),
            "value": child_result.get("value"),
            "exc_type": child_result.get("exc_type"),
            "limit": child_result.get("limit"),
            "mutated": bool(child_result.get("mutated", False)),
        }
    else:
        res = {
            "id": test_id,
            "status": "error",
            "value": None,
            "exc_type": "RuntimeError",
            "limit": None,
            "mutated": False,
        }

    if capture_stdout:
        res["stdout"] = captured_stdout_str or ""

    return res


def run_parent() -> None:
    """Read full batch payload from stdin, execute all tests, and write JSON output."""
    try:
        raw_data = sys.stdin.read(MAX_INPUT_BYTES)
        payload = json.loads(raw_data)
    except Exception as exc:
        err_output = {
            "results": [],
            "error": f"Invalid runner input: {type(exc).__name__}",
        }
        sys.stdout.write(json.dumps(err_output) + "\n")
        return

    code = payload.get("code", "")
    function_name = payload.get("function_name", "")
    tests = payload.get("tests", [])
    per_test_timeout = float(payload.get("per_test_timeout", 2.0))

    results = []
    for test in tests:
        test_result = execute_single_test(code, function_name, test, per_test_timeout)
        results.append(test_result)

    final_doc = {"results": results}
    sys.stdout.write(json.dumps(final_doc) + "\n")
    sys.stdout.flush()


def main() -> None:
    parser = ArgumentParser(description="Debug Dojo isolated test runner")
    parser.add_argument("--child", action="store_true", help="Run in child worker mode")
    parser.add_argument("--result-fd", type=int, default=None, help="File descriptor for child result pipe")
    parser.add_argument("--result-file", type=str, default=None, help="File path for child result fallback")
    args = parser.parse_args()

    if args.child:
        run_child_worker(args.result_fd, args.result_file)
    else:
        run_parent()


if __name__ == "__main__":
    main()
