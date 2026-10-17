# Debug Dojo 🥋

> **An adversarial, security-hardened backend for pedagogical programming education.**  
> Built for the hackathon with FastAPI, Docker container sandboxing, deterministic mistake taxonomy, exponential moving average mastery tracking, and guardrailed Gemini AI coaching.

---

## Architecture Overview

```
                                      +-----------------------------------------+
                                      |            Learner / Frontend           |
                                      +-----------------------------------------+
                                                           |
                                                HTTPS / JSON API REST
                                                           v
+-------------------------------------------------------------------------------------------------------+
|  FASTAPI APPLICATION (Host Process)                                                                   |
|                                                                                                       |
|  +------------------------+  +-------------------------+  +---------------------------------------+   |
|  | Authentication (Lite)  |  |  Submissions & History  |  | Socratic Coach & Interactive Hunter   |   |
|  +------------------------+  +-------------------------+  +---------------------------------------+   |
|               |                           |                                   |                       |
|               v                           v                                   v                       |
|  +------------------------+  +-------------------------+  +---------------------------------------+   |
|  | SQLite Database (WAL)  |  | Diagnostic Engine       |  | Guardrailed Gemini AI Layer           |   |
|  | - Learners & Sessions  |  | - Mistake Taxonomy (AST)|  | - System Instruction Guardrails       |   |
|  | - Submissions & Mastery|  | - Mastery Tracker (EMA) |  | - AST & Secrecy Output Validator      |   |
|  | - Achievements & XP    |  | - Recommender Engine    |  | - Deterministic Safe Fallbacks        |   |
|  +------------------------+  +-------------------------+  +---------------------------------------+   |
|                                           |                                                           |
+-------------------------------------------|-----------------------------------------------------------+
                                            | JSON Payload (Code + Inputs only; NO Expected Values)
                                            v
+-------------------------------------------------------------------------------------------------------+
|  EPHEMERAL DOCKER SANDBOX (`debugdojo-runner`)                                                        |
|                                                                                                       |
|  - Non-root user (UID 10001, /usr/sbin/nologin)       - Memory limit: 192MB                           |
|  - Read-only root filesystem                          - CPU quota: 0.5 cores                          |
|  - Network disabled (--network=none)                  - Process limit: 64 PIDs                        |
|  - All Linux capabilities dropped (--cap-drop=ALL)    - 16MB tmpfs (/tmp)                             |
|  - No-new-privileges flag enabled                     - Hard execution timeouts                       |
+-------------------------------------------------------------------------------------------------------+
```

---

## Quick Start (One Command)

Start the entire environment with a single command:

### Windows
```cmd
scripts\start.bat
```

### Linux / macOS
```bash
chmod +x scripts/start.sh
./scripts/start.sh
```

### What the Start Script Does Automatically:
1. **Checks Docker Daemon**: Verifies Docker is active and accessible.
2. **Builds Sandbox Image**: Inspects and builds `debugdojo-runner` from `docker/runner/Dockerfile` if missing.
3. **Creates Python Virtual Environment**: Sets up `.venv` and installs dependencies from `backend/requirements.txt` if not present.
4. **Environment Initialization**: Safely copies `backend/.env.example` to `backend/.env` without overwriting existing secrets, and reminds you to insert your `GEMINI_API_KEY`.
5. **Starts Backend API**: Boots FastAPI under Uvicorn bound to `0.0.0.0:8000`.
6. **Frontend Auto-Detection**: If a `frontend/` directory with `package.json` exists, installs node modules and launches the Vite dev server in a companion window.

---

## Configuration & Environment Variables

All settings are configured via `backend/.env` (git-ignored) or environment variables:

| Variable | Default | Purpose |
|---|---|---|
| `GEMINI_API_KEY` | `""` | Google Gemini API key. If omitted or empty, all AI endpoints automatically fall back to deterministic authored hints and explanations. |
| `GEMINI_MODEL` | `gemini-2.5-flash` | The Gemini model identifier used for explanations and coaching. |
| `DATABASE_PATH` | `backend/debugdojo.db` | SQLite database file location (WAL mode enabled). |
| `SANDBOX_MODE` | `docker` | Sandbox execution mode (`docker` or `disabled`). Learner code **never** runs on the host. |
| `MAX_PARALLEL_RUNS` | `4` | Maximum concurrent Docker runner containers permitted simultaneously. |
| `CONTAINER_TIMEOUT` | `20.0` | Maximum wall-clock seconds for an entire submission evaluation run. |
| `PER_TEST_TIMEOUT` | `2.0` | Per-test execution time limit in seconds. |
| `HOST` | `0.0.0.0` | Network binding interface. |
| `PORT` | `8000` | HTTP port. |
| `CORS_ORIGINS` | `["*"]` | Allowed CORS origins list. |

---

## How the Docker Sandbox Works

Learner code is untrusted and potentially malicious. Debug Dojo enforces isolation at the container, OS, and process levels:

- **Network-Isolated**: The container is started with `--network=none`. It cannot reach the internet, local networks, or host loopback.
- **Read-Only Root Filesystem**: The root filesystem is mounted read-only (`--read-only`). Writes are restricted to an in-memory `tmpfs` mounted at `/tmp` with a strict `size=16m` ceiling.
- **Non-Root Execution**: Runs as user `runner` (UID `10001`, GID `10001`) with `/usr/sbin/nologin`.
- **Capability Dropping**: `--cap-drop=ALL` removes all Linux capabilities (cannot mount disks, change network routes, or manipulate devices).
- **Privilege Escalation Prevention**: `--security-opt=no-new-privileges` prevents `setuid` binaries from elevating privileges.
- **Strict Resource Constraints**: Memory capped at `--memory=192m` (with `--memory-swap=192m`), CPU quota capped at `--cpus=0.5`, and process table restricted to `--pids-limit=64` to prevent fork bombs.
- **Host Communication**: Host passes JSON over `stdin` and reads `stdout` with a 256KB cap.
- **Cleanup Guarantee**: Every container run is wrapped in a Python `try...finally` block that invokes `docker rm -f <container_name>` immediately upon completion or timeout.

---

## How Hidden-Test Secrecy Works

To prevent learners or hostile scripts from extracting test benchmarks or tailoring trivial hardcoded lookups:

1. **Expected Values Never Enter Sandbox**:
   - The runner container receives **only** the function name, the code, and test input arguments.
   - Expected values and assertions are retained exclusively on the backend host.
   - The runner returns the raw return value or exception. The host compares values deterministically.
2. **Hidden Test Outputs Discarded**:
   - Standard output (`stdout`) generated during hidden test execution is completely discarded.
   - Hidden test exceptions are sanitized; raw exception tracebacks and argument representations are never included in API responses.
3. **API Contract Masking**:
   - Responses for hidden tests contain **only** `{"test": n, "outcome": "...", "limit": ...}`.
   - Fields such as `call`, `expected`, `got`, or exception text are stripped via `strip_for_api()`.
4. **Database Sanitization**:
   - SQLite `test_results` stores only `kind`, `test_no`, `category`, `outcome`, and `limit_hit`. Inputs and expected answers are never persisted.
5. **AI Prompt Isolation**:
   - AI coaches and explanation generators receive only aggregate counts and category names (e.g., *"3 of 6 hidden tests fail, involving cases where k is larger than list"*). Raw hidden test inputs and expected values are never placed into LLM prompts.

---

## How AI Guardrails Work

Debug Dojo uses Gemini to rewrite diagnostic explanations and personalize hints without ever spoiling solutions:

- **System Instruction Constraints**: The model is instructed to act as an encouraging coach, never provide runnable code, never provide markdown code blocks, and keep answers concise and conceptual.
- **Pre-Call Verification**: Input prompts are screened to ensure no hidden test inputs or solution code snippets are passed.
- **Post-Call Output Guardrails (`backend/ai/guardrails.py`)**:
  - **Code Block Blocker**: Rejects responses containing markdown code fences (```` ``` ````).
  - **Inline Code Threshold**: Rejects responses containing more than 2 inline backtick snippets or snippets longer than 30 characters.
  - **Solution Keyword / AST Filter**: Rejects responses containing Python function definitions (`def ...`), assignments, or full expressions.
  - **Hidden Data Leak Blocker**: Scans candidate responses for any hidden test argument or output fragment.
- **Deterministic Fallback**: If the model is blocked by a guardrail, returns malformed text, or fails due to network outage, the endpoint seamlessly returns pre-authored deterministic hints or structured category explanations.
- **Audit Logging**: Every AI request is logged to the `ai_logs` SQLite table and `logs/ai_calls.jsonl` with prompt text, response text, model version, and block reasons.

---

## Self-Test & Verification Suites

### 1. Judge Comprehensive Self-Test
Runs the 7 strict evaluation checks (Outcome Labels, Hidden Secrecy, Hint Non-Leakage, Red-Team Hostile Programs, Learner Model Recurrence, API Contract Shape, Concurrent Load):
```powershell
python scripts/judge_selftest.py
```

### 2. Red-Team Hostile Attack Suite
Runs 20 adversarial learner programs (fork bombs, memory bombs, infinite loops, AST mutators, inspect probe, signal handlers, subprocessing attacks) through the real grader:
```powershell
pytest backend/tests/redteam/test_redteam.py -v
```

### 3. Scripted Learner Simulation (Phase 5)
Simulates 11 sequential submissions planting 2 recurring mistakes and 1 one-off slip, asserting that recurring tags are detected, the one-off is excluded, and mastery is updated:
```powershell
python scripts/scripted_learner.py
```

### 4. Full Pytest Test Suite
Runs all 164 unit and integration tests across database, engine, grader, AI layer, auth, and API routes:
```powershell
pytest backend/tests -v
```

---

## API Summary

All endpoints conform strictly to [`docs/API_CONTRACT.md`](file:///c:/Users/Admin/OneDrive/Desktop/debug%20dojo/docs/API_CONTRACT.md) and [`docs/API_ADDITIONS.md`](file:///c:/Users/Admin/OneDrive/Desktop/debug%20dojo/docs/API_ADDITIONS.md):

### Core Evaluation & Learning
- `GET  /api/health` — Health check endpoint.
- `GET  /api/challenges` — List all active debugging challenges with concepts and metadata.
- `GET  /api/challenges/{id}` — Challenge specification, starter code, and visible test cases.
- `POST /api/submit` — Submit learner code for sandboxed grading, mistake diagnosis, and mastery update.
- `POST /api/hint` — Request next hint from the progressive 4-step hint ladder.
- `GET  /api/learner/{id}` — Learner profile with recurring mistakes, Bug DNA, mastery radar, and recommendations.

### Auth & Persistence
- `POST /api/auth/register` — Pseudonymous registration with optional PIN.
- `POST /api/auth/login` — Session token generation for PIN-protected learners.
- `GET  /api/learner/{id}/history` — Submission history list.
- `GET  /api/submissions/{id}` — Full submission inspection.
- `GET  /api/drafts/{id}/{challenge_id}` — Retrieve in-progress code draft.
- `PUT  /api/drafts/{id}/{challenge_id}` — Autosave code draft.

### Interactive Pedagogical Modes
- `POST /api/arena/fight` — Bug Arena: submit buggy code against reference tests for instant feedback.
- `POST /api/counterexample` — Counterexample Hunter: learner submits test inputs to find edge-case failures.
- `POST /api/explain` — Explain-It-Back: learner explains code; evaluated for clarity, mechanism, and edge-cases.
- `POST /api/coach` — Socratic Coach: interactive chat with conversational memory and strict solution guardrails.
- `POST /api/mutate` — Mutation Challenge: run learner's tests against buggy mutants to measure test coverage.

### Gamification & Data Management
- `GET  /api/learner/{id}/achievements` — Learner badges and XP totals.
- `POST /api/learner/{id}/reset` — Reset learner history and mastery to initial state.
- `GET  /api/learner/{id}/export` — Complete GDPR-compliant JSON data export.

---

## Honest Limitations

1. **Docker Shared Kernel**:
   - The sandbox relies on Docker Linux containers. While capabilities are dropped and namespaces isolated, containers share the host Linux kernel (unlike hardware-virtualized microVMs such as Firecracker or gVisor). In high-security multi-tenant production, running on gVisor or Firecracker is recommended.
2. **Heuristic Mistake Taxonomy**:
   - The deterministic mistake diagnosis engine utilizes static AST inspection and test category mapping. Unconventional implementations (e.g. implementing addition via bitwise operations or recursion with atypical variable names) may not trigger specific secondary tags and will cleanly receive an empty tag list `[]` instead of false positives.
3. **AI Non-Determinism**:
   - While Gemini provides human-like feedback, LLMs can produce varied phrasing. Debug Dojo mitigates this by restricting AI to tone rewriting and hint personalization; deterministic code **always** decides test outcomes, mastery scores, and diagnostic tags.
