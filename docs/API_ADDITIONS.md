# Debug Dojo - API Additions (Frontend Integration Guide)

This document details all Phase 7 API extensions added to the Debug Dojo backend. All requests and responses use JSON and the `/api` prefix.

---

## 1. Authentication (Login-Lite)

Debug Dojo provides optional, pseudonymous authentication.
- **Privacy Notice**: No real names, email addresses, or personal data are collected or stored.
- **Judge Compatibility**: Unprotected learners (no PIN) do **not** require any token or login. All endpoints (`/api/submit`, `/api/hint`, `/api/learner/{id}`) function normally with just a `learner_id`.
- **PIN Protection**: If a learner registers with a 4–8 digit PIN, subsequent access to protected profile endpoints, history, and drafts requires an `Authorization: Bearer <token>` header.

### POST /api/auth/register
Create a pseudonymous learner account with an optional 4–8 digit PIN.

**Request Body:**
```json
{
  "display_name": "Code Samurai",
  "pin": "1234"
}
```
*Note: `pin` is optional. If omitted, the profile remains unprotected (no login required).*

**Response (201 Created):**
```json
{
  "learner_id": "code_samurai",
  "token": "dGVzdF90b2tlbl9zdHJpbmdfZXhhbXBsZQ"
}
```

---

### POST /api/auth/login
Log in to an existing PIN-protected learner account to obtain a session token.

**Request Body:**
```json
{
  "learner_id": "code_samurai",
  "pin": "1234"
}
```

**Response (200 OK):**
```json
{
  "token": "dGVzdF90b2tlbl9zdHJpbmdfZXhhbXBsZQ"
}
```

**Error Responses:**
- `401 Unauthorized`: Invalid learner ID or PIN.
- `429 Too Many Requests`: Account temporarily locked due to 5 consecutive failed attempts (60-second cooldown).

---

## 2. History & Submission Detail

### GET /api/learner/{id}/history
Retrieve historical submissions for a learner, optionally filtered by `challenge_id` and bounded by `limit`.

**Query Parameters:**
- `limit` (integer, optional, default: 50)
- `challenge_id` (string, optional)

**Headers:**
- `Authorization: Bearer <token>` (only required if learner is PIN-protected)

**Response (200 OK):**
```json
[
  {
    "submission_id": "s_0042",
    "challenge_id": "last-k-total",
    "created_at": "2026-10-16T18:00:00Z",
    "all_passed": false,
    "counts": {
      "passed": 3,
      "failed": 2,
      "visible_passed": 3,
      "visible_failed": 0,
      "hidden_passed": 0,
      "hidden_failed": 2
    },
    "mistake_tags": ["unhandled_short_input"],
    "hints_used": 1,
    "prediction": "some_fail",
    "prediction_accuracy": 0.75
  }
]
```

---

### GET /api/submissions/{submission_id}
Retrieve full details of a specific submission, including the learner's submitted source code and API-safe test execution results.

**Headers:**
- `Authorization: Bearer <token>` (only required if submission belongs to a PIN-protected learner)

**Response (200 OK):**
```json
{
  "submission_id": "s_0042",
  "learner_id": "code_samurai",
  "challenge_id": "last-k-total",
  "code": "def last_k_total(marks, k):\n    return sum(marks[-k:])\n",
  "created_at": "2026-10-16T18:00:00Z",
  "visible": [
    {
      "test": 1,
      "call": "last_k_total([10, 20, 30, 40], 2)",
      "expected": "70",
      "got": "70",
      "outcome": "passed",
      "limit": null,
      "error": null
    }
  ],
  "hidden": [
    {"test": 1, "outcome": "passed", "limit": null},
    {"test": 2, "outcome": "runtime_error", "limit": null}
  ],
  "all_passed": false,
  "mistake_tags": ["unhandled_short_input"],
  "explanation": "All 3 visible tests pass, but 2 of the 7 hidden tests fail...",
  "hints_used": 1,
  "prediction": "some_fail",
  "prediction_result": {
    "predicted": "some_fail",
    "actual": "runtime_error",
    "correct": false
  },
  "arena": {
    "defeated": ["Example inputs", "Standard cases"],
    "remaining": ["Large K input", "Empty list"]
  }
}
```

---

## 3. Editor Drafts

Auto-save learner editor state so browser refreshes do not lose in-progress code.

### GET /api/drafts/{learner_id}/{challenge_id}
Retrieve saved draft code. If no draft has been saved yet, returns the challenge's default `starter_code`.

**Headers:**
- `Authorization: Bearer <token>` (only required if learner is PIN-protected)

**Response (200 OK):**
```json
{
  "learner_id": "code_samurai",
  "challenge_id": "last-k-total",
  "code": "def last_k_total(marks, k):\n    # my draft in progress\n    return 0\n",
  "updated_at": "2026-10-16T18:05:00Z"
}
```

---

### PUT /api/drafts/{learner_id}/{challenge_id}
Save editor draft code (maximum 20,000 characters).

**Headers:**
- `Authorization: Bearer <token>` (only required if learner is PIN-protected)

**Request Body:**
```json
{
  "code": "def last_k_total(marks, k):\n    # my updated draft\n    return sum(marks[-k:])\n"
}
```

**Response (200 OK):**
```json
{
  "learner_id": "code_samurai",
  "challenge_id": "last-k-total",
  "code": "def last_k_total(marks, k):\n    # my updated draft\n    return sum(marks[-k:])\n",
  "updated_at": "2026-10-16T18:05:30Z"
}
```

---

## 4. Bug Arena (Included in POST /api/submit)

The `POST /api/submit` response now includes the `arena` object:
```json
"arena": {
  "defeated": ["Example inputs", "Standard cases"],
  "remaining": ["Large K input", "Empty list"]
}
```
- `defeated`: Human-readable names of test categories where **all** test cases passed.
- `remaining`: Human-readable names of test categories with **at least one** failing test case.
- Never discloses hidden test arguments, values, or counts.

---

## 5. Counterexample Hunter

Allows learners to construct their own test inputs to test their code and discover bugs.

### POST /api/counterexample
Test candidate arguments against the learner's submitted code and the trusted reference solution.

**Request Body:**
```json
{
  "submission_id": "s_0042",
  "args": [[2, 5], 3]
}
```

**Response (200 OK):**
```json
{
  "exposes_bug": true,
  "call": "last_k_total([2, 5], 3)",
  "expected": "7",
  "got": "10",
  "xp_awarded": 10,
  "message": "Counterexample found! +10 Debugging XP"
}
```

---

## 6. Explain-It-Back (Post-Solve Reflection)

Allows learners to explain why their previous buggy code failed and how they fixed it once all tests have passed.

### POST /api/explain
Submit an explanation of the bug. Only allowed when `all_passed` is `true`.

**Request Body:**
```json
{
  "submission_id": "s_0043",
  "explanation": "My original slice marks[-k:] computed a negative index when k was greater than the length of marks, so it looped around and summed everything instead of capping at the list length."
}
```

**Response (200 OK):**
```json
{
  "code_correctness": "good",
  "concept_understanding": "good",
  "edge_case_understanding": "good",
  "feedback": "Outstanding reflection! You precisely recognized that Python negative slicing behaves differently when the absolute offset exceeds sequence length."
}
```
*Ratings: `good` | `partial` | `missing`.*

---

## 7. Socratic Coach

Interactive dialogue with the pedagogical coach. Never reveals answers or provides solution code.

### POST /api/coach
Send a message or question to the Socratic coach for a specific submission.

**Request Body:**
```json
{
  "submission_id": "s_0042",
  "message": "Why does my slice fail when k is 5 and list has 2 items?"
}
```

**Response (200 OK):**
```json
{
  "reply": "Good observation! In Python, what starting index does marks[-k:] compute when k = 5 on a list of length 2?"
}
```

---

## 8. Learner Management & Export

### POST /api/learner/{id}/reset
Reset all progress for a learner (deletes submissions, mastery, drafts, achievements, and XP events).

**Headers:**
- `Authorization: Bearer <token>` (only required if learner is PIN-protected)

**Response (200 OK):**
```json
{
  "status": "ok",
  "message": "Progress for code_samurai has been reset."
}
```

---

### GET /api/learner/{id}/export
Export complete JSON archive of all learner activity.

**Headers:**
- `Authorization: Bearer <token>` (only required if learner is PIN-protected)

**Response (200 OK):**
```json
{
  "learner": {"id": "code_samurai", "display_name": "Code Samurai", "created_at": "..."},
  "submissions": [...],
  "mastery": [...],
  "drafts": [...],
  "achievements": [...],
  "xp_events": [...]
}
```

---

## 9. Mutation Challenge (Lightweight)

Predefined code mutation puzzles where learners predict how a subtle code mutation behaves.

### GET /api/challenges/{id}/mutation
Retrieve the mutated code snippet, question, and multiple choice options.

**Response (200 OK):**
```json
{
  "challenge_id": "last-k-total",
  "mutated_code": "def last_k_total(marks, k):\n    return sum(marks[-k:])\n",
  "question": "What will happen if we call last_k_total([10, 20], 0)?",
  "options": [
    {"id": "A", "text": "It returns 0 as expected"},
    {"id": "B", "text": "It returns 30 (the sum of the entire list)"},
    {"id": "C", "text": "It raises an IndexError"},
    {"id": "D", "text": "It returns None"}
  ]
}
```

---

### POST /api/challenges/{id}/mutation/answer
Submit an answer choice to a mutation challenge.

**Request Body:**
```json
{
  "answer_id": "B",
  "learner_id": "code_samurai"
}
```

**Response (200 OK):**
```json
{
  "correct": true,
  "correct_id": "B",
  "explanation": "In Python, -0 evaluates to 0, so marks[-0:] becomes marks[0:]. This slices from the beginning of the list to the end, resulting in the sum of all elements (30) instead of 0.",
  "xp_awarded": 10
}
```

---




## 10. LeetCode-Style Run (Visible Tests Only)

Execute learner code against visible example tests without creating a formal submission, affecting mastery, modifying streaks, or writing to the database.

### POST /api/run
Run learner code against VISIBLE tests only in the Docker sandbox.

**Headers:**
- `Content-Type: application/json`
- `Authorization: Bearer <token>` (only required if learner is PIN-protected)

**Request Body:**
```json
{
  "learner_id": "code_samurai",
  "challenge_id": "count-even",
  "code": "def count_even(numbers):\n    return sum(1 for x in numbers if x % 2 == 0)\n"
}
```

**Response (200 OK):**
```json
{
  "visible": [
    {
      "test": 1,
      "call": "count_even([1, 2, 3, 4])",
      "expected": "2",
      "got": "2",
      "outcome": "passed",
      "limit": null,
      "error": null
    },
    {
      "test": 2,
      "call": "count_even([1, 3, 5])",
      "expected": "0",
      "got": "0",
      "outcome": "passed",
      "limit": null,
      "error": null
    },
    {
      "test": 3,
      "call": "count_even([])",
      "expected": "0",
      "got": "0",
      "outcome": "passed",
      "limit": null,
      "error": null
    }
  ]
}
```

**Notes for Frontend:**
- **Zero Database Writes**: Run attempts do not count as submissions. No records are written to `submissions`, `hints`, `mastery`, `xp_events`, or `streak_events`.
- **Hidden Tests Isolated**: Hidden tests are never loaded or executed for this endpoint.
- **Shared Rate Limit**: Shares the 120 req/min rate limit bucket with `/api/submit`.
- **Sandbox Busy (503)**: If all sandbox slots are busy and the queue timeout passes, the server returns HTTP 503 with `Retry-After: 5` and `{"error": "The dojo is busy, please try again"}`.
