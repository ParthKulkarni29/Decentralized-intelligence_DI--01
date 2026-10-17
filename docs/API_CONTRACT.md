## API CONTRACT (the backend implements exactly this)

All requests and responses are JSON. Base path `/api`.

### GET /api/challenges
```json
[
  {"id": "last-k-total", "title": "Last K Total", "difficulty": "easy",
   "concepts": ["lists", "indexing", "edge_cases"], "status": "new"}
]
```
`difficulty`: `easy | medium | hard`. `status`: `new | attempted | solved`.

### GET /api/challenges/{id}
```json
{
  "id": "last-k-total",
  "title": "Last K Total",
  "difficulty": "easy",
  "concepts": ["lists", "indexing", "edge_cases"],
  "description": "Return the total of the last k marks. If k is larger than the list, use all marks.",
  "starter_code": "def last_k_total(marks, k):\n    pass\n",
  "visible_tests": [
    {"test": 1, "call": "last_k_total([10, 20, 30, 40], 2)", "expected": "70"}
  ],
  "hidden_test_count": 6,
  "arg_names": ["marks", "k"]
}
```

### POST /api/submit
Request:
```json
{"learner_id": "rohan", "challenge_id": "last-k-total", "code": "...",
 "prediction": "some_fail"}
```
`prediction` is optional: `all_pass | some_fail | runtime_error | timeout`.

Response:
```json
{
  "submission_id": "s_0142",
  "learner_id": "rohan",
  "challenge_id": "last-k-total",
  "visible": [
    {"test": 1, "call": "last_k_total([10, 20, 30, 40], 2)", "expected": "70",
     "got": "70", "outcome": "passed"}
  ],
  "hidden": [
    {"test": 1, "outcome": "passed"},
    {"test": 3, "outcome": "runtime_error", "limit": null},
    {"test": 4, "outcome": "wrong_output"}
  ],
  "all_passed": false,
  "mistake_tags": ["unhandled_short_input"],
  "explanation": "All 3 visible tests pass, but 3 of the 6 hidden tests fail...",
  "hints_available": 4,
  "prediction_result": {"predicted": "some_fail", "actual": "runtime_error", "correct": false},
  "arena": {"defeated": ["Normal input", "Single element"], "remaining": ["Large K", "Empty input"]}
}
```
`prediction_result` and `arena` may be absent; handle that. Visible tests may also contain `"limit"` and an `"error"` string (visible only).

### POST /api/hint
Request: `{"submission_id": "s_0142"}`
Response: `{"hint_number": 2, "hint": "Work out last_k_total([2, 5], 3) by hand...", "hints_total": 4, "exhausted": false}`
Calling again returns the next hint. When all are used, `exhausted: true` and the last hint repeats.

### GET /api/learner/{learner_id}
```json
{
  "learner_id": "rohan",
  "stats": {"bugs_defeated": 12, "independent_streak": 3, "longest_streak": 7,
            "hint_assisted_solves": 4, "prediction_accuracy": 0.71, "xp": 120},
  "recurring_mistakes": [
    {"tag": "unhandled_short_input", "challenges": ["last-k-total", "middle-three"],
     "count": 2, "description": "Your code works on normal inputs but breaks on short or empty ones."}
  ],
  "bug_dna": [{"tag": "unhandled_short_input", "count": 3}, {"tag": "off_by_one", "count": 2}],
  "mastery": {"loops": 0.81, "lists": 0.64, "indexing": 0.58, "conditions": 0.73,
              "edge_cases": 0.40, "functions": 0.70},
  "next_challenge": {"id": "safe-first-element", "title": "Safe First Element",
                     "reason": "You made the same mistake (unhandled_short_input) in two different challenges."},
  "achievements": ["First Bug", "No-Hint Streak"]
}
```

### POST /api/counterexample (Counterexample Hunter)
Request: `{"submission_id": "s_0142", "args": [[2, 5], 3]}` (args in the order of `arg_names`, JSON values)
Response: `{"exposes_bug": true, "call": "last_k_total([2, 5], 3)", "expected": "7", "got": "10", "xp_awarded": 10, "message": "..."}`
This is the learner's own input, so showing expected and got is allowed.

### POST /api/explain (Explain-It-Back, after a full pass)
Request: `{"submission_id": "s_0150", "explanation": "free text"}`
Response: `{"code_correctness": "good", "concept_understanding": "good", "edge_case_understanding": "partial", "feedback": "..."}`
Rating values: `good | partial | missing`.

### POST /api/coach (Socratic coach, optional chat)
Request: `{"submission_id": "s_0142", "message": "maybe my loop is wrong"}`
Response: `{"reply": "Good hypothesis. Which part of the loop decides which elements are visited?"}`

### Errors
Any failure: HTTP 4xx/5xx with `{"error": "short message"}`. Show a friendly banner, never a raw stack trace. A submit can take several seconds (sandbox startup). Show a progress state ("Running your code in the sandbox...") and disable the Run button while waiting. Time out the request at 30 s with a retry option.

### POST /api/auth/register (Pseudonymous Register)
Request: `{"display_name": "Rohan", "pin": "1234"}` (`pin` optional, 4 to 8 digits)
Response: `{"learner_id": "rohan", "token": "..."}`

### POST /api/auth/login (PIN Login)
Request: `{"learner_id": "rohan", "pin": "1234"}`
Response: `{"token": "..."}`

### GET /api/learner/{id}/history
Query: `?limit=50&challenge_id=last-k-total`
Response:
```json
[
  {
    "submission_id": "s_0142",
    "challenge_id": "last-k-total",
    "created_at": "...",
    "all_passed": false,
    "counts": {"passed": 3, "failed": 2, "visible_passed": 3, "visible_failed": 0, "hidden_passed": 0, "hidden_failed": 2},
    "mistake_tags": ["unhandled_short_input"],
    "hints_used": 1,
    "prediction": "some_fail",
    "prediction_accuracy": 0.71
  }
]
```

### GET /api/submissions/{submission_id}
Response:
```json
{
  "submission_id": "s_0142",
  "learner_id": "rohan",
  "challenge_id": "last-k-total",
  "code": "def last_k_total(marks, k):\n    return sum(marks[-k:])\n",
  "created_at": "...",
  "visible": [{"test": 1, "call": "last_k_total([10, 20, 30, 40], 2)", "expected": "70", "got": "70", "outcome": "passed", "limit": null, "error": null}],
  "hidden": [{"test": 1, "outcome": "passed", "limit": null}, {"test": 2, "outcome": "runtime_error", "limit": null}],
  "all_passed": false,
  "mistake_tags": ["unhandled_short_input"],
  "explanation": "...",
  "hints_used": 1,
  "prediction": "some_fail",
  "prediction_result": {"predicted": "some_fail", "actual": "runtime_error", "correct": false},
  "arena": {"defeated": ["Example inputs"], "remaining": ["Large K input", "Empty list"]}
}
```

### GET /api/drafts/{learner_id}/{challenge_id}
Response: `{"learner_id": "rohan", "challenge_id": "last-k-total", "code": "...", "updated_at": "..."}`
*(Returns challenge starter_code if no draft has been saved yet).*

### PUT /api/drafts/{learner_id}/{challenge_id}
Request: `{"code": "def last_k_total(marks, k):\n    pass\n"}`
Response: `{"learner_id": "rohan", "challenge_id": "last-k-total", "code": "...", "updated_at": "..."}`

### POST /api/learner/{id}/reset
Header: `Authorization: Bearer <token>` (if pinned)
Response: `{"status": "ok", "message": "Progress for rohan has been reset."}`

### GET /api/learner/{id}/export
Header: `Authorization: Bearer <token>` (if pinned)
Response:
```json
{
  "learner": {"id": "rohan", "display_name": "Rohan", "created_at": "..."},
  "submissions": [...],
  "mastery": [...],
  "drafts": [...],
  "achievements": [...],
  "xp_events": [...]
}
```

### GET /api/challenges/{id}/mutation
Response:
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

### POST /api/challenges/{id}/mutation/answer
Request: `{"answer_id": "B", "learner_id": "rohan"}`
Response:
```json
{
  "correct": true,
  "correct_id": "B",
  "explanation": "...",
  "xp_awarded": 10
}
```

