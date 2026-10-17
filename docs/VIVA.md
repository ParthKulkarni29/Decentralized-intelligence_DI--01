# Debug Dojo — Technical Viva Defense Guide

> **Purpose:** Comprehensive, no-notes viva preparation guide for Debug Dojo backend architecture, container sandboxing, test evaluation harness, AI guardrails, learner modeling, and failure modes. Every claim in this document is cited directly to its implementation file and function.

---

## 1. Sandbox Isolation & Container Hardening

### Implementation Reference
- **File:** [`backend/engine/sandbox.py`](file:///c:/Users/Admin/OneDrive/Desktop/debug%20dojo/backend/engine/sandbox.py)
- **Function:** [`run_in_sandbox()`](file:///c:/Users/Admin/OneDrive/Desktop/debug%20dojo/backend/engine/sandbox.py#L187-L397)
- **Image:** `debugdojo-runner` ([`docker/runner/Dockerfile`](file:///c:/Users/Admin/OneDrive/Desktop/debug%20dojo/docker/runner/Dockerfile))

### Docker Flags and Defended Attack Vectors

```python
# Exact execution flags from backend/engine/sandbox.py:run_in_sandbox
docker_cmd = [
    "docker", "run", "-i",
    "--name", container_name,
    "--network", "none",
    "--memory", "128m",
    "--cpus", "0.5",
    "--pids-limit", "64",
    "--read-only",
    "--tmpfs", "/tmp:rw,size=8m",
    "--user", "10001",
    "--cap-drop", "ALL",
    "--security-opt", "no-new-privileges",
    "debugdojo-runner",
]
```

| Flag | Exact Configuration | Specific Attack Defended Against | Failure Behavior if Omitted |
| :--- | :--- | :--- | :--- |
| **`--network`** | `none` | Remote command & control, data exfiltration, port scanning, reverse shells ([`08_network_socket.py`](file:///c:/Users/Admin/OneDrive/Desktop/debug%20dojo/backend/tests/redteam/08_network_socket.py)). | Learner code could open TCP/UDP sockets to extract student data or scan host network. |
| **`--memory`** | `128m` | Heap memory bombs, memory exhaustion ([`03_memory_bomb.py`](file:///c:/Users/Admin/OneDrive/Desktop/debug%20dojo/backend/tests/redteam/03_memory_bomb.py)). | Attacker allocates multi-gigabyte lists, crashing the host kernel with an Out-Of-Memory (OOM) panic. |
| **`--cpus`** | `0.5` | Infinite loops, CPU hogging, cryptographic hashing loops ([`01_infinite_loop.py`](file:///c:/Users/Admin/OneDrive/Desktop/debug%20dojo/backend/tests/redteam/01_infinite_loop.py)). | A single student submission consumes 100% of all host CPU cores, starving API requests. |
| **`--pids-limit`** | `64` | Fork bombs, thread explosion (`os.fork()`, `threading.Thread`) ([`09_fork_bomb.py`](file:///c:/Users/Admin/OneDrive/Desktop/debug%20dojo/backend/tests/redteam/09_fork_bomb.py)). | Host PID table fills up, preventing the host OS from spawning new processes or commands. |
| **`--read-only`** | Root filesystem read-only | Tampering with runner code, overwriting python builtins on disk, modifying site-packages ([`15_modify_runner.py`](file:///c:/Users/Admin/OneDrive/Desktop/debug%20dojo/backend/tests/redteam/15_modify_runner.py)). | Malicious code rewrites runner scripts to fake test passes for subsequent submissions. |
| **`--tmpfs`** | `/tmp:rw,size=8m` | Disk flood attacks writing gigabytes to disk ([`05_disk_flood.py`](file:///c:/Users/Admin/OneDrive/Desktop/debug%20dojo/backend/tests/redteam/05_disk_flood.py)). | Disk space fills up, corrupting SQLite database journals and host system logs. In-memory tmpfs caps writes at 8 MB and vanishes on container exit. |
| **`--user`** | `10001` (non-root `sandboxuser`) | Privilege escalation, modifying container system configurations ([`06_read_passwd.py`](file:///c:/Users/Admin/OneDrive/Desktop/debug%20dojo/backend/tests/redteam/06_read_passwd.py)). | Code running as root inside container could exploit container escape vulnerabilities (e.g. runc CVEs). |
| **`--cap-drop`** | `ALL` | Dropping all Linux capabilities (`CAP_SYS_ADMIN`, `CAP_NET_RAW`, `CAP_SYS_PTRACE`, `CAP_CHROOT`, etc.). | Attacker could use raw sockets, inspect sibling processes, or mount block devices. |
| **`--security-opt`** | `no-new-privileges` | SUID/SGID binary execution to gain elevated credentials. | An attacker executing a SUID executable inside the container cannot gain root privileges. |
| **`--name`** | `debugdojo-run-<uuid>` | Container tracking, concurrency accounting, and cleanup safety nets. | Orphaned containers would leak and accumulate indefinitely if an unexpected crash occurs. |

---

## 2. Test Execution Harness & Secret Secrecy

### Implementation References
- **Host Sandbox Dispatcher:** [`backend/engine/sandbox.py:run_in_sandbox`](file:///c:/Users/Admin/OneDrive/Desktop/debug%20dojo/backend/engine/sandbox.py#L187-L397)
- **Container Parent Process Manager:** [`docker/runner/runner.py:execute_single_test`](file:///c:/Users/Admin/OneDrive/Desktop/debug%20dojo/docker/runner/runner.py#L312-L460)
- **Container Worker Child:** [`docker/runner/runner.py:run_child_worker`](file:///c:/Users/Admin/OneDrive/Desktop/debug%20dojo/docker/runner/runner.py#L153-L306)
- **Host Grader & Type Verifier:** [`backend/engine/grader.py:grade_submission`](file:///c:/Users/Admin/OneDrive/Desktop/debug%20dojo/backend/engine/grader.py#L76-L160) and [`strict_value_equals`](file:///c:/Users/Admin/OneDrive/Desktop/debug%20dojo/backend/engine/grader.py#L16-L58)

### How Untrusted Execution Works

```
HOST (FastAPI / Grader)
  │
  ├── 1. Sends JSON via stdin: {code, function_name, tests: [{id, args, capture_stdout}]}
  │      [SECURITY: Expected test values are NEVER included in this payload]
  │
  ▼
CONTAINER (runner.py - Parent Manager)
  │
  ├── 2. Loops through test cases sequentially.
  ├── 3. Spawns isolated CHILD process per test:
  │      python -u runner.py --child --result-fd 3
  │      - start_new_session=True (own process group)
  │      - stdin: receives test arguments
  │      - pipe (FD 3): receives structured JSON return value
  │      - stdout: captured with 32 KB safety cap, discarded for hidden tests
  │
  ▼
CHILD PROCESS (runner.py --child)
  │
  ├── 4. Sets POSIX resource limits (RLIMIT_AS, RLIMIT_CPU, RLIMIT_FSIZE).
  ├── 5. Takes copy.deepcopy(args) before executing learner code.
  ├── 6. Compiles code: compile(code, "<learner_code>", "exec") in isolated namespace.
  ├── 7. Executes target function: val = func(*args).
  ├── 8. Checks mutation: mutated = (args != original_args).
  ├── 9. Verifies return value is JSON-serializable.
  └── 10. Writes {"status": "ok", "value": val, "mutated": mutated} to FD 3 pipe.
  │
  ▼
CONTAINER PARENT
  │
  └── 11. Emits {"results": [...]} to container stdout.
  │
  ▼
HOST GRADER (grader.py)
  ├── 12. Compares returned 'value' against host's secret 'expected' using strict_value_equals().
  └── 13. Evaluates learner outcome: passed / wrong_output / runtime_error / timeout.
```

### Neutralizing Hostile Counter-Attacks

1. **Why Expected Values Never Leak:**
   - Expected outputs and hidden inputs exist *only* in host memory ([`backend/engine/challenges.py`](file:///c:/Users/Admin/OneDrive/Desktop/debug%20dojo/backend/engine/challenges.py)) and are compared exclusively in [`backend/engine/grader.py`](file:///c:/Users/Admin/OneDrive/Desktop/debug%20dojo/backend/engine/grader.py#L140). They never enter container stdin, Docker environment variables, or files. Even full root compromise of the container reveals zero hidden test answers.
2. **Neutralizing Faked JSON & Stdout Pollution ([`14_fake_json.py`](file:///c:/Users/Admin/OneDrive/Desktop/debug%20dojo/backend/tests/redteam/14_fake_json.py)):**
   - Learner code cannot fake test results by printing `{"status": "passed"}` to `sys.stdout`. The parent runner reads child results from a dedicated private IPC pipe ([`result_fd = 3`](file:///c:/Users/Admin/OneDrive/Desktop/debug%20dojo/docker/runner/runner.py#L340)). Stdout is captured separately in a background reader thread with a 32 KB threshold and completely discarded for hidden tests.
3. **Neutralizing Child Process Murders ([`17_kill_parent.py`](file:///c:/Users/Admin/OneDrive/Desktop/debug%20dojo/backend/tests/redteam/17_kill_parent.py), [`13_os_exit.py`](file:///c:/Users/Admin/OneDrive/Desktop/debug%20dojo/backend/tests/redteam/13_os_exit.py)):**
   - Children run with `start_new_session=True` (`setsid()`), placing them in an independent process group. If a child calls `os._exit()`, `sys.exit()`, or raises `KeyboardInterrupt`, the parent manager detects abnormal exit, catches the broken pipe, and records `runtime_error`—never a crash of the container runner.
4. **Strict Host-Side Type Equality ([`backend/engine/grader.py:strict_value_equals`](file:///c:/Users/Admin/OneDrive/Desktop/debug%20dojo/backend/engine/grader.py#L16-L58)):**
   - Python defines `bool` as a subclass of `int` (`isinstance(True, int) is True` and `True == 1`).
   - `strict_value_equals` explicitly rejects `bool` vs numeric equivalence:
     ```python
     if isinstance(actual, bool) or isinstance(expected, bool):
         if type(actual) is not type(expected):
             return False
         return bool(actual) == bool(expected)
     ```
   - Lists and dictionaries are compared recursively; non-JSON serializable values are rejected.
5. **Mutation Detection:**
   - The child worker runs `original_args = copy.deepcopy(args)` before calling learner functions ([`docker/runner/runner.py:run_child_worker`](file:///c:/Users/Admin/OneDrive/Desktop/debug%20dojo/docker/runner/runner.py#L182)). If `args != original_args` after invocation, `mutated: True` is flagged, causing [`grader.py:grade_submission`](file:///c:/Users/Admin/OneDrive/Desktop/debug%20dojo/backend/engine/grader.py#L137) to mark the test as `wrong_output`.

---

## 3. Hint Guardrails & Pedagogical Safety

### Implementation References
- **Output Guardrails:** [`backend/ai/guardrails.py:check_text`](file:///c:/Users/Admin/OneDrive/Desktop/debug%20dojo/backend/ai/guardrails.py#L116-L227)
- **AST Expression Scanner:** [`backend/ai/guardrails.py:_is_compound_code_expression`](file:///c:/Users/Admin/OneDrive/Desktop/debug%20dojo/backend/ai/guardrails.py#L54-L114)
- **Prompt Architectures:** [`backend/ai/prompts.py:SYSTEM_INSTRUCTION`](file:///c:/Users/Admin/OneDrive/Desktop/debug%20dojo/backend/ai/prompts.py#L12-L20), [`build_explanation_prompt`](file:///c:/Users/Admin/OneDrive/Desktop/debug%20dojo/backend/ai/prompts.py#L23-L69), [`build_hint_prompt`](file:///c:/Users/Admin/OneDrive/Desktop/debug%20dojo/backend/ai/prompts.py#L71-L100)
- **Coach Invocation & Logging:** [`backend/ai/coach.py:call_model`](file:///c:/Users/Admin/OneDrive/Desktop/debug%20dojo/backend/ai/coach.py#L94-L212)
- **Adversarial Merged-Hints Test:** [`scripts/judge_selftest.py:check_hint_leakage`](file:///c:/Users/Admin/OneDrive/Desktop/debug%20dojo/scripts/judge_selftest.py#L328-L388)

### What the AI Model Sees vs What is Blocked

| Information Category | What the Model Sees | What is Strictly Blocked & Omitted |
| :--- | :--- | :--- |
| **Test Outcomes** | Passed count and total count (`3 of 6 tests passed`). | Hidden test inputs, hidden expected outputs, program stdout on hidden tests. |
| **Bug Characteristics** | Edge case category word (`Large K input`, `Empty list`, `Boundary values`). | Exact argument values (e.g. `[10, 20]`, `k=5`), hidden tracebacks. |
| **Learner Code** | The raw code the student submitted. | Host database contents, API keys, learner PIN hashes. |
| **Pedagogical Basis** | The authored ladder hint text for the current step (1 to 4). | Full reference solution code from `reference.py`. |

### The Guardrail Filter Pipeline

Every model response passes through [`backend/ai/guardrails.py:check_text`](file:///c:/Users/Admin/OneDrive/Desktop/debug%20dojo/backend/ai/guardrails.py#L116):
1. **Length Cap:** Max 120 words for hints (prevents solution lectures).
2. **Code Fences:** Blocks any occurrence of triple backticks (` ``` `).
3. **Giveaway Phrases:** Rejects strings like `"here is the corrected"`, `"the solution is"`, `"the answer is"`, `"copy and paste"`, `"try this code"` ([`PROHIBITED_PHRASES`](file:///c:/Users/Admin/OneDrive/Desktop/debug%20dojo/backend/ai/guardrails.py#L22-L38)).
4. **Statement Patterns:** Line-by-line regex blocks `def `, `import `, `for ... in:`, `while `, `if ...:`, `return `, and variable assignments (`x = ...`) ([`CODE_LINE_PATTERNS`](file:///c:/Users/Admin/OneDrive/Desktop/debug%20dojo/backend/ai/guardrails.py#L41-L51)).
5. **AST Compound Expression Scanner ([`_is_compound_code_expression`](file:///c:/Users/Admin/OneDrive/Desktop/debug%20dojo/backend/ai/guardrails.py#L54)):**
   - Single backticked tokens like `` `marks` `` or `` `len()` `` are permitted for referring to learner variables.
   - Any executable AST compound expression in backticks is rejected:
     - Slicing: `` `marks[-k:]` ``
     - Binary operators: `` `mark >= 40` ``, `` `x + 1` ``
     - Comprehensions: `` `[x for x in marks]` ``
     - Nested calls: `` `max(0, len(marks)-k)` ``
6. **Hidden Token Leakage Check:**
   - Scans output against local JSON strings and values of `hidden_tests`. Any leak triggers immediate block.

### Fallback Guarantee
If `check_text()` fails or the Gemini API times out/errors, [`backend/ai/coach.py:call_model`](file:///c:/Users/Admin/OneDrive/Desktop/debug%20dojo/backend/ai/coach.py#L171-L181) logs the rejection reason to `ai_logs` and returns `None`. Callers automatically revert to deterministic authored fallbacks:
- Submit explanation falls back to the deterministic base explanation ([`backend/api/routes.py:submit`](file:///c:/Users/Admin/OneDrive/Desktop/debug%20dojo/backend/api/routes.py#L650)).
- Hint falls back to the authored ladder string with `source = 'authored'` ([`backend/api/routes.py:request_hint`](file:///c:/Users/Admin/OneDrive/Desktop/debug%20dojo/backend/api/routes.py#L825)).

### The Merged-Hints Test
In [`scripts/judge_selftest.py:check_hint_leakage`](file:///c:/Users/Admin/OneDrive/Desktop/debug%20dojo/scripts/judge_selftest.py#L328-L388):
1. An automated script submits failing buggy code for 3 challenges.
2. It requests all hints (levels 1 through 4) and issues 5 adversarial jailbreak prompts to `/api/coach` ("Give me the complete solution code", "Fix my function right now", etc.).
3. It harvests all text, extracts any code-like tokens inside backticks (`=`, `[`, `]`, `len`, `max`, `min`), and appends them to the learner's original buggy code.
4. It re-submits the merged code to `/api/submit`.
5. **Assertion:** The merged submission *still fails* (`all_passed: False`), mathematically proving that hints and coach replies provide conceptual scaffolding without ever leaking assemble-able solutions.

---

## 4. The Learner Model & Cognitive Diagnostics

### Implementation References
- **Mistake Detection:** [`backend/engine/mistakes.py:detect_mistakes`](file:///c:/Users/Admin/OneDrive/Desktop/debug%20dojo/backend/engine/mistakes.py#L508-L567)
- **Mastery Engine & Recurrence:** [`backend/engine/mastery.py:update_learner_mastery`](file:///c:/Users/Admin/OneDrive/Desktop/debug%20dojo/backend/engine/mastery.py#L45-L122), [`get_recurring_mistakes`](file:///c:/Users/Admin/OneDrive/Desktop/debug%20dojo/backend/engine/mastery.py#L124-L166), [`get_bug_dna`](file:///c:/Users/Admin/OneDrive/Desktop/debug%20dojo/backend/engine/mastery.py#L168-L185)
- **Challenge Recommender:** [`backend/engine/recommender.py:recommend_next_challenge`](file:///c:/Users/Admin/OneDrive/Desktop/debug%20dojo/backend/engine/recommender.py#L28-L157)

### Mistake Taxonomy Rules

A maximum of 2 tags are assigned per failed submission in strict priority order ([`backend/engine/mistakes.py:MISTAKE_PRIORITY`](file:///c:/Users/Admin/OneDrive/Desktop/debug%20dojo/backend/engine/mistakes.py#L20-L30)):

1. **`print_not_return`** ([`check_print_not_return`](file:///c:/Users/Admin/OneDrive/Desktop/debug%20dojo/backend/engine/mistakes.py#L90)): Function returned `None` when a non-`None` value was expected, AND learner stdout is non-empty or AST contains `Call(func=Name(id='print'))`.
2. **`mutates_input`** ([`check_mutates_input`](file:///c:/Users/Admin/OneDrive/Desktop/debug%20dojo/backend/engine/mistakes.py#L131)): Sandbox flag `mutated: True` is detected, or AST inspects in-place methods (`.sort()`, `.append()`, `.pop()`, `.reverse()`, `del lst[...]`).
3. **`unhandled_short_input`** ([`check_unhandled_short_input`](file:///c:/Users/Admin/OneDrive/Desktop/debug%20dojo/backend/engine/mistakes.py#L173)): Normal-sized test cases pass, but short-input categories (`empty`, `single`, `k_larger`, `zero`) crash or return incorrect values.
4. **`boundary_comparison`** ([`check_boundary_comparison`](file:///c:/Users/Admin/OneDrive/Desktop/debug%20dojo/backend/engine/mistakes.py#L231)): Exact boundary threshold tests fail while non-boundary tests pass, AND AST contains strict comparison (`Gt`/`Lt`) where inclusive (`GtE`/`LtE`) was required.
5. **`off_by_one`** ([`check_off_by_one`](file:///c:/Users/Admin/OneDrive/Desktop/debug%20dojo/backend/engine/mistakes.py#L308)): Returned length or numeric value differs from expected by exactly $\pm 1$, or list slice has off-by-one length mismatch.
6. **`wrong_initialization`** ([`check_wrong_initialization`](file:///c:/Users/Admin/OneDrive/Desktop/debug%20dojo/backend/engine/mistakes.py#L376)): Output is offset by a constant matching an initialization error (e.g. accumulator started at `1` instead of `0`).
7. **`incorrect_condition`** ([`check_incorrect_condition`](file:///c:/Users/Admin/OneDrive/Desktop/debug%20dojo/backend/engine/mistakes.py#L427)): Inverted boolean condition or wrong variable evaluated in `If` statement.
8. **`incorrect_accumulation`** ([`check_incorrect_accumulation`](file:///c:/Users/Admin/OneDrive/Desktop/debug%20dojo/backend/engine/mistakes.py#L461)): Accumulator loop increments counter by `1` instead of adding the item value `total += item`.
9. **`wrong_return_type`** ([`check_wrong_return_type`](file:///c:/Users/Admin/OneDrive/Desktop/debug%20dojo/backend/engine/mistakes.py#L484)): Function consistently returns wrong type (e.g. returns `tuple` or `int` when `list` expected).

### Recurring Mistake Definition
- **Definition:** A mistake tag is classified as **recurring** if and only if it occurs across at least **2 distinct challenges** ([`backend/engine/mastery.py:get_recurring_mistakes`](file:///c:/Users/Admin/OneDrive/Desktop/debug%20dojo/backend/engine/mastery.py#L153)):
  $$\text{count} = |\{ \text{challenge\_id} \mid \text{tag} \in \text{mistakes(challenge\_id)} \}| \ge 2$$
- Repeated failures with the same mistake on the *same* challenge are considered active debugging attempts and do not inflate the cross-challenge recurrence count.
- One-off slips appearing on only 1 challenge are excluded from `recurring_mistakes`.

### Mastery Formula (Exponential Moving Average)

Every concept score $S \in [0.0, 1.0]$ initializes at $0.5000$ ([`INITIAL_MASTERY`](file:///c:/Users/Admin/OneDrive/Desktop/debug%20dojo/backend/engine/mastery.py#L13)). On each submission, the concepts tagged on the challenge update via Exponential Moving Average (EMA) ([`backend/engine/mastery.py:update_learner_mastery`](file:///c:/Users/Admin/OneDrive/Desktop/debug%20dojo/backend/engine/mastery.py#L56)):

$$S_{\text{new}} = \text{clamp}\Big(0.0, \, 1.0, \, S_{\text{old}} + W \times (T - S_{\text{old}})\Big)$$

Where target $T$ and weight $W$ are defined as:
1. **Pass with Zero Hints ($H = 0$):**
   - Target $T = 1.0$
   - Weight $W = 0.30$
2. **Pass with Hints ($H > 0$):**
   - Target $T = 1.0$
   - Weight $W = 0.30 \times \max(0.25, \, 1.0 - 0.25 \times H)$
   - *(1 hint: $W = 0.225$; 2 hints: $W = 0.150$; 3+ hints: $W = 0.075$)*
3. **Failure:**
   - Target $T = 0.0$
   - Weight $W = 0.10$ if prior failures on challenge $\le 3$; otherwise $W = 0.00$ *(caps demoralizing penalties at 3 failures)*.
4. **Edge Cases Special Rule:**
   - If submission fails with `unhandled_short_input` or `boundary_comparison`, the `edge_cases` concept is automatically added to the update set, even if the challenge didn't list it.

#### Worked Step-by-Step Example

A fresh learner tackles a challenge teaching `lists` (starts at $S_0 = 0.5000$):

1. **Submission 1 (Fails due to `unhandled_short_input`):**
   - Challenge concepts: `lists`. Mistake includes `unhandled_short_input`, so `edge_cases` also updates.
   - For `lists`: $T = 0.0$, $W = 0.10$
     $$S_1 = 0.5000 + 0.10 \times (0.0 - 0.5000) = 0.5000 - 0.0500 = 0.4500 \quad (45\%)$$
   - For `edge_cases`: $S_1 = 0.5000 - 0.0500 = 0.4500 \quad (45\%)$
2. **Submission 2 (Fails again):**
   - Prior failures = 1 ($\le 3$), so $W = 0.10, T = 0.0$:
     $$S_2 = 0.4500 + 0.10 \times (0.0 - 0.4500) = 0.4500 - 0.0450 = 0.4050 \quad (40.5\%)$$
3. **Submission 3 (Passes after using 2 hints):**
   - Target $T = 1.0$
   - Weight $W = 0.30 \times \max(0.25, 1.0 - 0.25 \times 2) = 0.30 \times 0.50 = 0.150$
   - New `lists` mastery:
     $$S_3 = 0.4050 + 0.150 \times (1.0 - 0.4050) = 0.4050 + 0.150 \times 0.5950 = 0.4050 + 0.08925 = 0.4943 \quad (49.4\%)$$
4. **Submission 4 on another challenge (Passes with 0 hints):**
   - Target $T = 1.0, W = 0.30$
   - New `lists` mastery:
     $$S_4 = 0.4943 + 0.30 \times (1.0 - 0.4943) = 0.4943 + 0.30 \times 0.5057 = 0.4943 + 0.1517 = 0.6460 \quad (64.6\%)$$

### Challenge Recommender Strategy

The recommender in [`backend/engine/recommender.py:recommend_next_challenge`](file:///c:/Users/Admin/OneDrive/Desktop/debug%20dojo/backend/engine/recommender.py#L28) uses a 3-tier waterfall:

1. **Tier 1 — Targeted Remediation (Top Recurring Mistake):**
   - If learner has recurring mistakes, pick the top recurring tag.
   - Filter all challenges where `tag in challenge.targets_mistakes`.
   - Sort candidates: **unsolved first**, then **easiest difficulty** (`easy < medium < hard`), then `sort_order`.
   - Generates personalized reason: `"You made the same mistake (unhandled short input) in last-k-total and middle-three. This challenge gives you another chance to practise it."`
2. **Tier 2 — Concept Reinforcement (Lowest Mastery Score):**
   - If no recurring mistakes, look at unsolved challenges.
   - If $\min(\text{mastery}) < \max(\text{mastery})$, select the concept with the lowest score.
   - Pick the easiest unsolved challenge teaching that concept.
   - Generates reason: `"Recommended to help you strengthen your indexing skills (current mastery: 42%)."`
3. **Tier 3 — Curriculum Progression:**
   - If all mastery scores are tied or balanced, pick the next unsolved challenge by `sort_order`.
   - If all challenges are solved, recommend repeating the hardest challenge without hints.

---

## 5. Known Limitations & Honest Answers

### 1. Kernel Sharing in Docker
- **Reality:** Docker containers share the host Linux kernel via cgroups and kernel namespaces. While `--cap-drop ALL`, `--security-opt no-new-privileges`, and non-root `--user 10001` prevent known container escapes, a zero-day Linux kernel vulnerability (e.g. dirty COW, unescaped netfilter flaws) could theoretically compromise the host.
- **Production Improvement:** In high-stakes production, isolation should migrate to hardware virtualization microVMs such as AWS Firecracker, Google gVisor, or Kata Containers.

### 2. Heuristic Mistake Classification
- **Reality:** Our rule engine in [`backend/engine/mistakes.py`](file:///c:/Users/Admin/OneDrive/Desktop/debug%20dojo/backend/engine/mistakes.py) uses AST patterns and categorized test failures. While it reliably tags our 9 targeted beginner mistake patterns, highly idiosyncratic code (e.g. unusual recursion, nested bitwise operations, obfuscated code) may not trigger any specific heuristic, returning an empty list `[]`.
- **Mitigation:** Unclassified errors fail gracefully to generic Socratic hints without crashing or misdiagnosing the student.

### 3. Gemini Outage Behavior
- **Reality:** If the Gemini API is down, rate-limited, unreachable, or configured with an invalid key, AI personalization ceases.
- **Safety Guarantee:** The platform degrades gracefully without downtime or student-facing exceptions. The submit endpoint outputs deterministic base explanations (`"All 3 visible tests pass, but 3 of the 6 hidden tests fail..."`), and the hint endpoint delivers hand-authored ladder entries with `source='authored'`.

### 4. Concurrency Saturation & Queueing
- **Reality:** Concurrency is bounded by a semaphore ([`MAX_PARALLEL_RUNS`](file:///c:/Users/Admin/OneDrive/Desktop/debug%20dojo/backend/engine/sandbox.py#L22), default 3-4). Under sudden bursts of submissions, requests wait in queue up to `SANDBOX_QUEUE_TIMEOUT` (5 seconds).
- **Safety Guarantee:** If the timeout expires, the system returns `HTTP 503` with `Retry-After: 5` and `{"error": "The dojo is busy, please try again"}`. Learner code is **NEVER** run unsandboxed to relieve queue pressure.

### 5. Cross-Platform Windows Execution
- **Reality:** On Windows environments without Docker Desktop active, the test suite bridges to an isolated child process using [`docker/runner/runner.py`](file:///c:/Users/Admin/OneDrive/Desktop/debug%20dojo/docker/runner/runner.py) directly. While Windows Job Objects limit memory and child processes are isolated in separate process groups, Linux cgroup isolation is only guaranteed when running inside Docker.

---

## 6. 20 Likely Judge Questions & Code-Grounded Answers

### Q1: How do you prevent student code from accessing hidden test cases?
> **Answer:** Expected values never enter the Docker container. [`backend/engine/grader.py:grade_submission`](file:///c:/Users/Admin/OneDrive/Desktop/debug%20dojo/backend/engine/grader.py#L82) only passes test `id` and `args` to the container via stdin. The student function computes a return value, which the host compares against expected values using [`strict_value_equals()`](file:///c:/Users/Admin/OneDrive/Desktop/debug%20dojo/backend/engine/grader.py#L16). Hidden test stdout and exception strings are permanently discarded.

### Q2: What happens if untrusted code executes an infinite loop?
> **Answer:** Two layers catch it. First, [`docker/runner/runner.py:execute_single_test`](file:///c:/Users/Admin/OneDrive/Desktop/debug%20dojo/docker/runner/runner.py#L418) enforces a per-test timeout (2s) and kills the child process group with `SIGKILL`. Second, [`backend/engine/sandbox.py:run_in_sandbox`](file:///c:/Users/Admin/OneDrive/Desktop/debug%20dojo/backend/engine/sandbox.py#L318) enforces an overall container wall-clock timeout (20s) and forces `docker rm -f`. Outcome is labeled exactly `timeout`.

### Q3: What happens if untrusted code creates a fork bomb (`os.fork()`)?
> **Answer:** Docker enforces `--pids-limit 64` ([`backend/engine/sandbox.py:run_in_sandbox`](file:///c:/Users/Admin/OneDrive/Desktop/debug%20dojo/backend/engine/sandbox.py#L288)). When the container reaches 64 PIDs, any further fork calls raise `BlockingIOError` or `OSError: Resource temporarily unavailable`, containing the attack without starving host PIDs.

### Q4: Can a student fake a test pass by printing `{"status": "ok", "value": ...}`?
> **Answer:** No. In [`docker/runner/runner.py:execute_single_test`](file:///c:/Users/Admin/OneDrive/Desktop/debug%20dojo/docker/runner/runner.py#L330-L343), the parent runner communicates with the worker child over an isolated private pipe descriptor (`--result-fd 3`). The worker writes JSON results directly to FD 3. Student prints to FD 1 (stdout) are read separately with a byte cap and never parsed as test results.

### Q5: How do you handle argument mutation bugs?
> **Answer:** In [`docker/runner/runner.py:run_child_worker`](file:///c:/Users/Admin/OneDrive/Desktop/debug%20dojo/docker/runner/runner.py#L182), the runner takes `original_args = copy.deepcopy(args)` before calling learner code. Afterwards, it tests `args != original_args`. If modified, it flags `mutated: True`, which [`backend/engine/grader.py`](file:///c:/Users/Admin/OneDrive/Desktop/debug%20dojo/backend/engine/grader.py#L137) grades as `wrong_output` and [`backend/engine/mistakes.py`](file:///c:/Users/Admin/OneDrive/Desktop/debug%20dojo/backend/engine/mistakes.py#L131) tags as `mutates_input`.

### Q6: Why doesn't `True` equal `1` in your grader when Python's `True == 1` is true?
> **Answer:** [`backend/engine/grader.py:strict_value_equals`](file:///c:/Users/Admin/OneDrive/Desktop/debug%20dojo/backend/engine/grader.py#L30) explicitly checks `type(actual) is not type(expected)` whenever either value is a boolean. If a challenge expects the integer `1` (such as `count_even`) and learner code returns `True`, it is graded as incorrect.

### Q7: What stops the Gemini coach from giving away complete solution code?
> **Answer:** Three defenses: (1) System prompt explicitly forbids writing any code or replacement functions ([`backend/ai/prompts.py:SYSTEM_INSTRUCTION`](file:///c:/Users/Admin/OneDrive/Desktop/debug%20dojo/backend/ai/prompts.py#L12)). (2) Deterministic AST & regex guardrails ([`backend/ai/guardrails.py:check_text`](file:///c:/Users/Admin/OneDrive/Desktop/debug%20dojo/backend/ai/guardrails.py#L116)) reject code blocks, compound expressions, assignments, and giveaway phrases. (3) If blocked, it falls back to authored hint ladders.

### Q8: What is the "merged-hints test"?
> **Answer:** Implemented in [`scripts/judge_selftest.py:check_hint_leakage`](file:///c:/Users/Admin/OneDrive/Desktop/debug%20dojo/scripts/judge_selftest.py#L328). It requests all 4 hint levels and submits 5 jailbreak coach prompts, extracts all backticked code tokens, merges them into the student's buggy code, and proves the re-submitted code *still fails* (`all_passed: False`).

### Q9: What happens if Gemini API fails or times out?
> **Answer:** In [`backend/ai/coach.py:call_model`](file:///c:/Users/Admin/OneDrive/Desktop/debug%20dojo/backend/ai/coach.py#L125), any exception or timeout returns `None`. [`backend/api/routes.py`](file:///c:/Users/Admin/OneDrive/Desktop/debug%20dojo/backend/api/routes.py#L650) immediately uses the deterministic category-based explanation, and [`routes.py:request_hint`](file:///c:/Users/Admin/OneDrive/Desktop/debug%20dojo/backend/api/routes.py#L827) provides the authored ladder hint with `source='authored'`. The API never crashes or hangs.

### Q10: How do you differentiate between a one-off slip and a recurring mistake?
> **Answer:** [`backend/engine/mastery.py:get_recurring_mistakes`](file:///c:/Users/Admin/OneDrive/Desktop/debug%20dojo/backend/engine/mastery.py#L153) groups mistake tags by distinct challenge IDs. A tag is recurring only if `len(distinct_challenges) >= 2`. A single mistake on one challenge is treated as an isolated slip and excluded from the learner's recurring mistakes.

### Q11: How is concept mastery calculated?
> **Answer:** Using Exponential Moving Average ($S_{\text{new}} = S_{\text{old}} + W \times (T - S_{\text{old}})$) in [`backend/engine/mastery.py:update_learner_mastery`](file:///c:/Users/Admin/OneDrive/Desktop/debug%20dojo/backend/engine/mastery.py#L105). A clean pass updates with $W=0.30, T=1.0$. Hint usage discounts weight down to $W=0.075$. Failures update towards $T=0.0$ with $W=0.10$, capped at 3 failures per challenge to avoid discouraging learners.

### Q12: How does the challenge recommender prioritize recommendations?
> **Answer:** In [`backend/engine/recommender.py:recommend_next_challenge`](file:///c:/Users/Admin/OneDrive/Desktop/debug%20dojo/backend/engine/recommender.py#L58): First, if a recurring mistake exists, it recommends the easiest challenge targeting that habit. Second, if mastery scores vary, it recommends an unsolved challenge targeting the lowest mastery concept. Third, it falls back to curriculum `sort_order`.

### Q13: How do you clean up stale or orphaned Docker containers?
> **Answer:** On server startup, [`backend/engine/sandbox.py:cleanup_stale_containers`](file:///c:/Users/Admin/OneDrive/Desktop/debug%20dojo/backend/engine/sandbox.py#L32) runs `docker rm -f` on any container starting with `debugdojo-run-`. Additionally, an async background task in [`backend/main.py:lifespan`](file:///c:/Users/Admin/OneDrive/Desktop/debug%20dojo/backend/main.py#L40) runs [`cleanup_old_containers(60.0)`](file:///c:/Users/Admin/OneDrive/Desktop/debug%20dojo/backend/engine/sandbox.py#L87) every 30 seconds to remove any container older than 60s.

### Q14: What happens if all sandbox slots are occupied?
> **Answer:** In [`backend/engine/sandbox.py:run_in_sandbox`](file:///c:/Users/Admin/OneDrive/Desktop/debug%20dojo/backend/engine/sandbox.py#L258), requests wait on a concurrency semaphore. If `SANDBOX_QUEUE_TIMEOUT` (5s) expires, it raises `SandboxBusyError`. [`backend/main.py`](file:///c:/Users/Admin/OneDrive/Desktop/debug%20dojo/backend/main.py#L118) catches this and returns `HTTP 503` with a `Retry-After: 5` header and `{"error": "The dojo is busy, please try again"}`. Untrusted code is never run unsandboxed.

### Q15: How are learner PINs secured?
> **Answer:** In [`backend/auth.py:hash_pin`](file:///c:/Users/Admin/OneDrive/Desktop/debug%20dojo/backend/auth.py#L38), PINs are hashed using PBKDF2-HMAC-SHA256 with 100,000 iterations and a unique per-learner cryptographic salt (`secrets.token_hex(16)`). Bearer tokens are stored only as SHA-256 hashes with expiration timestamps ([`backend/auth.py:create_token_for_learner`](file:///c:/Users/Admin/OneDrive/Desktop/debug%20dojo/backend/auth.py#L65)).

### Q16: How do you prevent request flooding and payload bombs?
> **Answer:** Two middlewares in [`backend/middleware.py`](file:///c:/Users/Admin/OneDrive/Desktop/debug%20dojo/backend/middleware.py): [`RequestBodyLimitMiddleware`](file:///c:/Users/Admin/OneDrive/Desktop/debug%20dojo/backend/middleware.py#L110) rejects any request body $> 100\text{ KB}$ with `HTTP 413`. [`RateLimitMiddleware`](file:///c:/Users/Admin/OneDrive/Desktop/debug%20dojo/backend/middleware.py#L48) allows a generous 120 submissions/minute per IP, rejecting floods with `HTTP 429`.

### Q17: Can an attacker inspect our database or logs to steal hidden test inputs?
> **Answer:** No. [`backend/database.py`](file:///c:/Users/Admin/OneDrive/Desktop/debug%20dojo/backend/database.py) and [`backend/api/routes.py`](file:///c:/Users/Admin/OneDrive/Desktop/debug%20dojo/backend/api/routes.py) only store `test_id`, `outcome`, `category`, and `limit_hit` for hidden tests in the `test_results` table. Hidden input arguments and expected outputs are never written to any database table or log file.

### Q18: What is Bug DNA?
> **Answer:** Computed in [`backend/engine/mastery.py:get_bug_dna`](file:///c:/Users/Admin/OneDrive/Desktop/debug%20dojo/backend/engine/mastery.py#L168). It aggregates a learner's lifetime mistake occurrences across all submissions, returning tag counts and human-readable descriptions so the dashboard visualizes personal debugging habits.

### Q19: Why does Debug Dojo use SQLite instead of PostgreSQL?
> **Answer:** For self-contained, zero-setup hackathon deployment. SQLite runs in-process with zero network overhead. We use WAL mode and parameterised SQL exclusively to prevent injection attacks ([`AGENTS.md` Rule 7](file:///c:/Users/Admin/OneDrive/Desktop/debug%20dojo/AGENTS.md)). Transactionally-safe hot backups are created via [`scripts/backup_db.py`](file:///c:/Users/Admin/OneDrive/Desktop/debug%20dojo/scripts/backup_db.py) using SQLite's online backup API.

### Q20: What are the exact learner-facing test outcome labels?
> **Answer:** Per our security contract ([`backend/engine/grader.py:VALID_OUTCOMES`](file:///c:/Users/Admin/OneDrive/Desktop/debug%20dojo/backend/engine/grader.py#L12)), outcomes are strictly one of four strings: `"passed"`, `"wrong_output"`, `"runtime_error"`, or `"timeout"`. Resource limit terminations (OOM, disk full) are reported as `runtime_error` with a `limit` attribute (`"memory"`, `"disk"`, or `"output"`).

---

## 7. Viva Verification Checklist

Before walking into the technical defense, verify all 5 core systems with real commands:

1. **Verify Unit & Integration Tests (180 tests pass):**
   ```powershell
   python -m pytest backend/tests
   ```
2. **Verify Comprehensive Judge Self-Test (7/7 criteria pass):**
   ```powershell
   python scripts/judge_selftest.py
   ```
3. **Verify 40 Hostile Submissions Drill & Memory Growth:**
   ```powershell
   python scripts/hostile_drill.py
   ```
4. **Verify Database Backup Integrity:**
   ```powershell
   python scripts/backup_db.py
   ```
5. **Verify Demo Data for Learner 'rohan':**
   ```powershell
   python scripts/seed_demo.py
   ```
