# Debug Dojo: Frontend Build Prompt

Paste everything below the line into Antigravity (or any coding agent). It is self-contained. The backend is being built separately, so build against the mock layer first, then flip one env flag to go live.

---

## ROLE AND GOAL

Build the frontend for **Debug Dojo**, an AI debugging coach for beginner Python learners. It does not give answers. It runs the learner's code, shows per-test results, explains failures in plain language, gives progressively stronger hints, tracks recurring mistakes ("Bug DNA"), and recommends the next challenge.

Core idea to express in the UI: Debug Dojo does not count how many problems you solved. It measures which bugs you have learned to defeat.

## STACK (fixed)

- React + Vite (JavaScript or TypeScript, your choice, be consistent)
- Tailwind CSS
- Monaco Editor (`@monaco-editor/react`), Python language mode
- Recharts for charts
- React Router
- No other UI frameworks. No backend code in this repo.

## ENV AND RUN

- `VITE_API_BASE` (default `http://localhost:8000`)
- `VITE_USE_MOCK` (`true` or `false`, default `true`)
- `npm install && npm run dev` must work with mocks and no backend running.
- All network calls live in `src/services/api.js`. It checks `VITE_USE_MOCK` and either calls `fetch` or returns mock data from `src/services/mock.js`. Components never call `fetch` directly.

## HARD RULES (security and judging; do not break)

1. **Hidden tests stay hidden.** The API returns only `{test, outcome}` for hidden tests. Never render, log, store, or request hidden inputs, expected outputs, actual outputs, or exception messages. Do not add `console.log` of full API responses. Do not add fields to the UI that assume such data exists.
2. **Outcome labels are exactly four**, sent as snake_case strings: `passed`, `wrong_output`, `runtime_error`, `timeout`. Display them as: Passed, Wrong output, Runtime error, Timeout. Never invent other learner-facing labels. A memory/output/disk limit arrives as `runtime_error` with a `limit` field (see contract); show it as a small note under the label, e.g. "Runtime error: Memory limit exceeded".
3. **Never show or build a "solution" or "show answer" button.** Hints come only from `POST /api/hint`, one at a time, in order.
4. Do not bundle any challenge test data in the frontend beyond what the API returns for visible tests.
5. Pseudonymous learners only. Ask for a display name on first launch (no password), store `learner_id` (lowercase slug of the name) in `localStorage`. This is the only use of localStorage.

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

## SCREENS

### 1. Name screen (first launch only)
Ask "What should we call you?", create `learner_id`, go to Dashboard.

### 2. Dashboard (`/`)
- Greeting by time of day; "Ready to defeat another bug?"
- Three stat cards: Bugs defeated, Independent streak, Mastery (average of the mastery values, as a percent).
- **Your Bug DNA**: horizontal bars from `bug_dna`, with a one-line plain description of the top pattern (use the matching `recurring_mistakes[].description` if present).
- **Next Challenge** card: title, the `reason` text, and a Start Challenge button.
- **Recurring mistakes**: a badge for each, with the challenges it appeared in.
- **Bug Map** (Recharts radar or a simple vertical path of concept nodes): one node per concept in `mastery`, colour by value (below 0.5 red, 0.5 to 0.75 amber, above 0.75 green). A single sentence naming the weakest concept: "Your biggest current weakness is {concept}."
- Achievements row of badges.
- Challenge list below, grouped by difficulty, with status chips.

### 3. Challenge screen (`/challenge/:id`)
Two-pane layout. Left: title, difficulty, concept chips, description, the visible tests list (`call` and `expected`). Right: Monaco editor preloaded with `starter_code`, a Run button, a Hint button.

Flow:
1. Clicking **Run** first opens the **Predict Before Run** modal: "What do you predict?" with four options (Everything passes / Some tests fail / Runtime error / Timeout) and a Skip link. Send the choice as `prediction` in the submit call.
2. While running, show the progress state.
3. After results, show **Prediction card** if `prediction_result` exists: "Your prediction: X. Actual: Y." with a gentle tone either way.
4. **Results panel**:
   - Visible tests: each row shows test number, `call`, expected, got, and an outcome pill.
   - Hidden tests: a row of numbered pills with only the outcome. Heading "Hidden tests". Do not add any tooltip or detail beyond the outcome.
   - **Bug Arena strip**: "Bugs defeated: 4 / 7" with chips from `arena.defeated` (ticked) and `arena.remaining` (bug icon). Hide it if `arena` is absent.
5. **"What happened?" card** with `explanation`, plus the `mistake_tags` shown as friendly labels (map `unhandled_short_input` to "Short or empty input not handled", and so on; include a fallback that humanises any unknown tag).
6. **Hint ladder**: a "Need a hint?" button calls `/api/hint`. Hints stack one under another (Hint 1, Hint 2, ...), each revealed only after the previous, with a "hint X of N" indicator. When `exhausted` is true, disable the button and say "That is every hint. Try the counterexample hunter or ask the coach."
7. **Counterexample Hunter** (shown after a failing submit): "Your code works on the examples. Now try to break it." One input per name in `arg_names` (accept JSON, e.g. `[2, 5]` and `3`), a Test button, and then either "Not yet. Think about the smallest or most unusual valid input." or "Counterexample found! +10 Debugging XP" with the call, expected and got.
8. **Socratic coach**: a small chat panel under the results. Send each message to `/api/coach`. Keep it compact.
9. If `all_passed` is true: celebration state, then the **Explain-It-Back** panel (textarea and a "Check my understanding" button). Show the three ratings as Correctness, Concept, Edge cases with good (tick), partial (warning), missing (cross), plus `feedback`. Then a Next challenge button using fresh learner data.

### 4. Progress page (`/progress`)
Full Bug DNA chart, Bug Map, mastery bars, streak stats (independent streak, longest streak, hint-assisted solves), prediction accuracy, achievements.

## VISUAL DESIGN

- Dojo theme without clutter: dark charcoal background, off-white text, a red accent (dojo seal) and a calm green for success. Rounded cards, generous spacing, 🥋 branding in the header.
- Must work on a laptop projector: minimum 16 px body text, strong contrast, large pills. Do not rely on colour alone; always pair an icon or text with colour.
- Beginner friendly: short sentences, no jargon on screen.
- Light animation only (progress, a small confetti or pulse on success). No heavy libraries.

## MOCK DATA (`src/services/mock.js`)

Implement mocks for every endpoint above with these behaviours so the whole UI can be demoed offline:
- `GET challenges`: the 10 challenges `count-passes, first-n-squares, last-k-total, middle-three, safe-first-element, count-even, remove-duplicates, running-total, rotate-list, group-passing-marks` with difficulty easy (first five), medium (count-even, remove-duplicates, running-total), hard (rotate-list, group-passing-marks).
- `submit`: if the code contains `len(marks) - k` return a failing result (3 visible passed; hidden 6 with outcomes passed, passed, runtime_error, wrong_output, wrong_output, passed; tag `unhandled_short_input`). If the code contains `while True` return all hidden `timeout`. If it contains `max(` return all passed. Otherwise return a mixed wrong_output result.
- `hint`: the 4 hints for last-k-total from the example (cycle in order, then `exhausted: true`). For other challenges return 3 generic hints.
- `learner`: the example JSON above.
- `counterexample`: return `exposes_bug: true` when the second argument is larger than the first list's length.
- `explain`: return `good, good, partial` with a sample feedback.
- Add an artificial 800 ms delay to every mock call so the loading states can be seen.

## PROJECT STRUCTURE

```
frontend/
  src/
    components/   (StatCard, BugDnaBars, BugMap, OutcomePill, TestRow, HintLadder,
                   PredictModal, CounterexampleHunter, ExplainItBack, CoachChat,
                   ArenaStrip, ErrorBanner, Loader)
    pages/        (Name, Dashboard, Challenge, Progress)
    hooks/        (useLearner, useChallenge)
    services/     (api.js, mock.js)
    utils/        (labels.js for outcome and tag display names)
    App.jsx
  .env.example
  README.md      (how to run, how to switch mock off)
```

## ORDER OF WORK

1. Scaffold, routing, Tailwind, the api layer with mocks.
2. Challenge screen: editor, run, results with the four outcome labels, hidden tests as pills only.
3. Explanation, hint ladder, tag labels.
4. Dashboard: stats, Bug DNA, next challenge with reason.
5. Predict modal, counterexample hunter, explain-it-back, coach chat, Bug Arena strip.
6. Progress page, Bug Map, achievements.
7. Polish, empty and error states, responsive layout.
8. Turn `VITE_USE_MOCK=false` and test against the real backend.

## ACCEPTANCE CHECKLIST

- [ ] Runs with mocks only, no backend needed.
- [ ] Only the four outcome labels appear anywhere in the UI.
- [ ] Hidden tests show outcome only. Searching the codebase for "hidden" fields beyond `test` and `outcome` finds nothing.
- [ ] No show-answer button anywhere. Hints appear one by one.
- [ ] Run button disabled while a submission is in flight; errors show a friendly banner.
- [ ] Dashboard shows recurring mistakes and the next challenge with its reason.
- [ ] No `console.log` of API responses in production code.
- [ ] Changing API base or mock flag needs only `.env` edits.

If any part of the contract looks wrong or missing, do not change it silently: write the proposed change in `API_CHANGES.md` at the repo root so the backend owner can review it.
