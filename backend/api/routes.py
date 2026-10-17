"""API route handlers for Debug Dojo.

Implements /api/submit, /api/hint, /api/learner/{learner_id}, and challenge listing.
Strictly adheres to docs/API_CONTRACT.md and AGENTS.md anti-leak rules.
"""

from datetime import datetime, timedelta, timezone
import json
import re
from typing import Any, Optional
from fastapi import APIRouter, HTTPException, Header, status
from fastapi.responses import JSONResponse

from backend.database import get_db_connection
from backend.auth import (
    PIN_REGEX,
    check_brute_force,
    create_session,
    generate_learner_id,
    hash_pin,
    record_login_attempt,
    verify_learner_token,
    verify_pin,
)
from backend.engine.challenges import (
    get_full_challenge,
    get_public_challenge,
    get_visible_challenge,
    list_challenges,
)
from backend.engine.grader import grade_run, grade_submission, strip_for_api
import backend.engine.grader as grader_module
from backend.engine.mistakes import detect_mistakes
from backend.engine.code_hints import analyze_code, build_code_aware_ladder, extra_notes
from backend.engine.mastery import (
    ALL_CONCEPTS,
    award_achievement,
    check_and_award_all_achievements,
    check_and_award_bug_habit_broken,
    get_bug_dna,
    get_learner_mastery,
    get_recurring_mistakes,
    update_learner_mastery,
)
from backend.engine.reference import execute_reference
from backend.engine.recommender import recommend_next_challenge
from backend.ai.coach import call_model
from backend.ai.guardrails import verify_coach_reply
from backend.ai.prompts import (
    SYSTEM_INSTRUCTION,
    build_explanation_prompt,
    build_hint_prompt,
    build_socratic_reply_prompt,
)
from backend.schemas import (
    ArenaResult,
    ChallengeDetailResponse,
    ChallengeSummaryResponse,
    CoachRequest,
    CoachResponse,
    CounterexampleRequest,
    CounterexampleResponse,
    DraftRequest,
    DraftResponse,
    ExplainRequest,
    ExplainResponse,
    HintRequest,
    HintResponse,
    HistoryResponse,
    HistorySubmissionItem,
    LearnerProfileResponse,
    LearnerStats,
    LoginRequest,
    LoginResponse,
    MutationAnswerRequest,
    MutationAnswerResponse,
    MutationChallengeResponse,
    MutationOption,
    NextChallengeRecommendation,
    PredictionResult,
    RegisterRequest,
    RegisterResponse,
    ResetResponse,
    RunRequest,
    RunResponse,
    SubmissionDetailResponse,
    SubmitRequest,
    SubmitResponse,
)

router = APIRouter(prefix="/api")

# Validation regex: short slug (letters, digits, - and _ only, max 40 chars)
LEARNER_ID_REGEX = re.compile(r"^[a-zA-Z0-9_-]{1,40}$")
MAX_CODE_LENGTH = 20000

ALL_CONCEPTS = ["loops", "lists", "indexing", "conditions", "edge_cases", "functions"]


def _format_count_word(n: int) -> str:
    """Format small integers as English words."""
    words = {
        0: "zero", 1: "one", 2: "two", 3: "three", 4: "four",
        5: "five", 6: "six", 7: "seven", 8: "eight", 9: "nine", 10: "ten",
    }
    return words.get(n, str(n))


def _format_category_description(category: str) -> str:
    """Translate internal category tags into natural English descriptions."""
    category_map = {
        "k_larger_than_list": "where k is larger than the number of marks",
        "empty_list": "where the list is empty",
        "single_element": "with a single element",
        "k_zero": "where k is zero",
        "k_equal_to_length": "where k is equal to the length of the list",
        "boundary_value": "at boundary threshold values",
        "length_one": "where the list has length one",
        "length_two": "where the list has length two",
        "fewer_than_three": "with fewer than three items",
        "n_zero": "where n is zero",
        "n_one": "where n is one",
    }
    if category in category_map:
        return category_map[category]
    return f"involving {category.replace('_', ' ')}"


def _format_arena_category(category: Optional[str]) -> str:
    """Format an internal category key into a friendly Bug Arena title."""
    if not category:
        return "Standard cases"
    arena_map = {
        "k_larger_than_list": "Large K input",
        "empty_list": "Empty list",
        "single_element": "Single element",
        "k_zero": "Zero K input",
        "k_equal_to_length": "K equals list length",
        "boundary_value": "Boundary values",
        "length_one": "Single element",
        "length_two": "Two elements",
        "fewer_than_three": "Fewer than three items",
        "n_zero": "Zero input",
        "n_one": "Single element",
        "all_pass": "All passing marks",
        "all_fail": "All failing marks",
        "alternating": "Alternating values",
        "even_numbers": "Even numbers",
        "odd_numbers": "Odd numbers",
        "mixed_numbers": "Mixed numbers",
        "no_duplicates": "No duplicates",
        "all_duplicates": "All duplicates",
        "consecutive_duplicates": "Consecutive duplicates",
        "mutation_check": "Input preservation",
        "k_negative": "Negative shift",
        "k_greater_than_length": "Large rotation",
    }
    if category in arena_map:
        return arena_map[category]
    return category.replace("_", " ").capitalize()


def build_arena_status(
    visible_results: list[dict[str, Any]],
    hidden_results: list[dict[str, Any]],
) -> dict[str, list[str]]:
    """Build Bug Arena defeated and remaining named categories."""
    cats_status: dict[str, bool] = {}

    # Example inputs category
    vis_all_passed = bool(visible_results) and all(v["outcome"] == "passed" for v in visible_results)
    cats_status["Example inputs"] = vis_all_passed

    for h in hidden_results:
        cat_name = _format_arena_category(h.get("category"))
        passed = (h["outcome"] == "passed")
        if cat_name not in cats_status:
            cats_status[cat_name] = passed
        else:
            cats_status[cat_name] = cats_status[cat_name] and passed

    defeated = [cat for cat, passed in cats_status.items() if passed]
    remaining = [cat for cat, passed in cats_status.items() if not passed]
    return {"defeated": defeated, "remaining": remaining}


def build_deterministic_base_explanation(
    visible_results: list[dict[str, Any]],
    hidden_results: list[dict[str, Any]],
    all_passed: bool,
) -> str:
    """Construct deterministic base explanation from test counts and categories.

    Style:
    'All 3 visible tests pass, but 3 of the 6 hidden tests fail: one crashes and two give the wrong total.
    All three are cases where k is larger than the number of marks.'
    """
    if all_passed:
        return "All visible and hidden tests passed! Excellent work."

    vis_total = len(visible_results)
    vis_passed = sum(1 for v in visible_results if v["outcome"] == "passed")

    hid_total = len(hidden_results)
    failed_hidden = [h for h in hidden_results if h["outcome"] != "passed"]
    hid_failed_count = len(failed_hidden)

    # 1. Visible clause
    if vis_passed == vis_total:
        vis_clause = f"All {vis_total} visible tests pass"
    elif vis_passed == 0:
        vis_clause = f"None of the {vis_total} visible tests pass"
    else:
        vis_clause = f"{vis_passed} of the {vis_total} visible tests pass"

    # 2. Hidden clause & failure breakdown
    if hid_failed_count == 0:
        return f"{vis_clause}, and all {hid_total} hidden tests pass."

    crashes = sum(1 for h in failed_hidden if h["outcome"] == "runtime_error")
    wrong_outputs = sum(1 for h in failed_hidden if h["outcome"] == "wrong_output")
    timeouts = sum(1 for h in failed_hidden if h["outcome"] == "timeout")

    breakdown_parts = []
    if crashes > 0:
        word = _format_count_word(crashes)
        breakdown_parts.append(f"{word} crash{'es' if crashes == 1 else ''}")
    if wrong_outputs > 0:
        word = _format_count_word(wrong_outputs)
        breakdown_parts.append(f"{word} give{'s' if wrong_outputs == 1 else ''} the wrong total")
    if timeouts > 0:
        word = _format_count_word(timeouts)
        breakdown_parts.append(f"{word} time{'s' if timeouts == 1 else ''} out")

    if breakdown_parts:
        breakdown_str = " and ".join(breakdown_parts)
        hid_clause = f"but {hid_failed_count} of the {hid_total} hidden tests fail: {breakdown_str}."
    else:
        hid_clause = f"but {hid_failed_count} of the {hid_total} hidden tests fail."

    # 3. Category description
    failed_cats = [h.get("category", "") for h in failed_hidden if h.get("category")]
    unique_cats = list(dict.fromkeys(failed_cats))

    if unique_cats:
        if len(unique_cats) == 1:
            cat_desc = _format_category_description(unique_cats[0])
            if hid_failed_count == 1:
                cat_sentence = f"This case is one {cat_desc}."
            else:
                word_total = _format_count_word(hid_failed_count)
                cat_sentence = f"All {word_total} are cases {cat_desc}."
        else:
            cat_descs = [_format_category_description(c) for c in unique_cats]
            cat_sentence = f"These cases involve: {', '.join(cat_descs)}."
        return f"{vis_clause}, {hid_clause} {cat_sentence}"

    return f"{vis_clause}, {hid_clause}"


def _get_utc_now() -> str:
    """Return ISO 8601 UTC timestamp."""
    return datetime.now(timezone.utc).isoformat()


def _ensure_learner_exists(conn: Any, learner_id: str) -> None:
    """Auto-create learner record if not present.

    WHY: Anti-authentication requirement. Judges and initial users invoke endpoints
    without authentication or pre-registration.
    """
    now = _get_utc_now()
    conn.execute(
        "INSERT OR IGNORE INTO learners (id, display_name, pin_hash, created_at) VALUES (?, ?, NULL, ?);",
        (learner_id, learner_id, now),
    )


def _ensure_challenge_exists(conn: Any, challenge: Any) -> None:
    """Ensure challenge record exists in database table to satisfy foreign keys."""
    conn.execute(
        """
        INSERT OR IGNORE INTO challenges (
            id, title, difficulty, concepts_json, targets_mistakes_json, sort_order
        ) VALUES (?, ?, ?, ?, ?, ?);
        """,
        (
            challenge.id,
            challenge.title,
            challenge.difficulty,
            json.dumps(challenge.concepts),
            json.dumps(challenge.targets_mistakes),
            challenge.sort_order,
        ),
    )


# ==============================================================================
# Authentication Endpoints (Login-Lite)
# ==============================================================================

@router.post("/auth/register", response_model=RegisterResponse, status_code=status.HTTP_201_CREATED)
def register_learner(request: RegisterRequest) -> RegisterResponse:
    """Register a pseudonymous learner with an optional 4-8 digit numeric PIN.
    
    SECURITY AND PRIVACY NOTICE:
    No real names, emails, or personal data are collected or stored by this service.
    Account names are converted into clean pseudonymous slugs.
    """
    pin_hash = None
    if request.pin is not None:
        if not PIN_REGEX.match(request.pin):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="PIN must be 4 to 8 digits.",
            )
        pin_hash = hash_pin(request.pin)

    conn = get_db_connection()
    try:
        learner_id = generate_learner_id(conn, request.display_name)
        now_iso = _get_utc_now()
        with conn:
            conn.execute(
                """
                INSERT INTO learners (id, display_name, pin_hash, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (learner_id, request.display_name.strip(), pin_hash, now_iso),
            )
            token = create_session(conn, learner_id)
        return RegisterResponse(learner_id=learner_id, token=token)
    finally:
        conn.close()


@router.post("/auth/login", response_model=LoginResponse)
def login_learner(request: LoginRequest) -> LoginResponse:
    """Authenticate a learner using their pseudonymous learner_id and numeric PIN."""
    check_brute_force(request.learner_id)

    if not PIN_REGEX.match(request.pin):
        record_login_attempt(request.learner_id, success=False)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid learner ID or PIN.",
        )

    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT pin_hash FROM learners WHERE id = ?", (request.learner_id,))
        row = cursor.fetchone()
        if not row or not row["pin_hash"]:
            record_login_attempt(request.learner_id, success=False)
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid learner ID or PIN.",
            )

        if not verify_pin(request.pin, row["pin_hash"]):
            record_login_attempt(request.learner_id, success=False)
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid learner ID or PIN.",
            )

        # Successful login
        record_login_attempt(request.learner_id, success=True)
        with conn:
            token = create_session(conn, request.learner_id)
        return LoginResponse(token=token)
    finally:
        conn.close()


# ==============================================================================
# Challenge Endpoints
# ==============================================================================

@router.get("/challenges", response_model=list[ChallengeSummaryResponse])
def get_challenges() -> list[dict[str, Any]]:
    """List available challenges with difficulty, concepts, and status."""
    return list_challenges()


@router.get("/challenges/{challenge_id}", response_model=ChallengeDetailResponse)
def get_challenge_by_id(challenge_id: str) -> dict[str, Any]:
    """Retrieve public challenge details.

    SECURITY RULE: Hidden tests are never exposed; only hidden_test_count is returned.
    """
    try:
        return get_public_challenge(challenge_id)
    except KeyError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Challenge not found",
        )


@router.get("/challenges/{challenge_id}/mutation", response_model=MutationChallengeResponse)
def get_mutation_challenge(challenge_id: str) -> dict[str, Any]:
    """Retrieve an optional mutation challenge with code snippet and prediction choices."""
    challenge_id = challenge_id.strip()
    try:
        get_public_challenge(challenge_id)
    except KeyError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Challenge not found")

    from pathlib import Path
    PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
    mut_file = PROJECT_ROOT / "backend" / "challenges" / challenge_id / "mutations.json"
    if not mut_file.exists():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No mutation challenge available for challenge '{challenge_id}'",
        )

    data = json.loads(mut_file.read_text(encoding="utf-8"))
    return {
        "challenge_id": challenge_id,
        "mutated_code": data["mutated_code"],
        "question": data["question"],
        "options": data["options"],
    }


@router.post("/challenges/{challenge_id}/mutation/answer", response_model=MutationAnswerResponse)
def answer_mutation_challenge(challenge_id: str, request: MutationAnswerRequest) -> dict[str, Any]:
    """Verify the learner's answer to a mutation prediction challenge."""
    challenge_id = challenge_id.strip()
    try:
        get_public_challenge(challenge_id)
    except KeyError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Challenge not found")

    from pathlib import Path
    PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
    mut_file = PROJECT_ROOT / "backend" / "challenges" / challenge_id / "mutations.json"
    if not mut_file.exists():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No mutation challenge available for challenge '{challenge_id}'",
        )

    data = json.loads(mut_file.read_text(encoding="utf-8"))
    correct_id = data.get("correct_id", "A")
    is_correct = (request.answer_id.strip().upper() == correct_id.upper())

    xp_awarded = 0
    if is_correct and request.learner_id:
        conn = get_db_connection()
        try:
            _ensure_learner_exists(conn, request.learner_id)
            now_iso = _get_utc_now()
            with conn:
                cur = conn.cursor()
                cur.execute(
                    "SELECT id FROM xp_events WHERE learner_id = ? AND reason = ?;",
                    (request.learner_id, f"mutation_{challenge_id}"),
                )
                if not cur.fetchone():
                    xp_awarded = 10
                    cur.execute(
                        "INSERT INTO xp_events (learner_id, amount, reason, created_at) VALUES (?, 10, ?, ?);",
                        (request.learner_id, f"mutation_{challenge_id}", now_iso),
                    )
        finally:
            conn.close()

    return {
        "correct": is_correct,
        "correct_id": correct_id,
        "explanation": data.get("explanation", ""),
        "xp_awarded": xp_awarded,
    }


# ==============================================================================
# Run Endpoint (LeetCode-Style Run Button)
# ==============================================================================

@router.post("/run", response_model=RunResponse)
def run_solution(
    request: RunRequest,
    authorization: Optional[str] = Header(None),
) -> dict[str, Any]:
    """Run learner code against VISIBLE tests only in Docker sandbox.

    Guarantees:
    - Runs in the same Docker sandbox as /api/submit.
    - Tests only the challenge's visible test cases.
    - Hidden tests are never loaded, referenced, or evaluated.
    - Nothing is written to the database (no submissions, hints, mastery, XP, or streaks).
    - Unprotected learners (no PIN) do not require a token.
    - Protected learners require a valid Bearer token.
    - Code length is strictly checked against MAX_CODE_LENGTH.
    """
    # 1. Validate inputs
    learner_id = request.learner_id.strip()
    if not LEARNER_ID_REGEX.match(learner_id):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid learner_id: must be 1-40 alphanumeric characters, hyphens, or underscores",
        )

    code = request.code
    if len(code) > MAX_CODE_LENGTH:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Code exceeds maximum length of {MAX_CODE_LENGTH} characters",
        )

    # 2. Token verification parity with /api/submit (if Authorization header provided, verify it)
    if authorization:
        conn = get_db_connection()
        try:
            verify_learner_token(conn, learner_id, authorization)
        finally:
            conn.close()

    # 3. Load visible-only challenge configuration (never touches hidden_tests.json)
    try:
        challenge = get_visible_challenge(request.challenge_id)
    except KeyError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Challenge not found",
        )

    # 4. Grade code against visible tests only inside the Docker sandbox
    visible_results = grade_run(
        function_name=challenge.function_name,
        visible_tests=challenge.visible_tests,
        code=code,
    )

    # 5. Return clean visible results (0 DB writes)
    return {"visible": visible_results}


# ==============================================================================
# Submission Endpoint
# ==============================================================================

@router.post("/submit", response_model=SubmitResponse)
def submit_solution(request: SubmitRequest) -> dict[str, Any]:
    """Submit learner code for automated grading inside Docker sandbox.

    Validates inputs, auto-creates learner if needed, evaluates in sandbox,
    stores results with hidden test secrecy, and returns clean results.
    """
    # 1. Validate inputs
    learner_id = request.learner_id.strip()
    if not LEARNER_ID_REGEX.match(learner_id):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid learner_id: must be 1-40 alphanumeric characters, hyphens, or underscores",
        )

    code = request.code
    if len(code) > MAX_CODE_LENGTH:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Code exceeds maximum length of {MAX_CODE_LENGTH} characters",
        )

    # 2. Verify challenge exists
    try:
        challenge = get_full_challenge(request.challenge_id)
    except KeyError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Challenge not found",
        )

    # 3. Grade code on the host using the container sandbox
    graded = grade_submission(challenge, code)

    # 4. Detect mistake tags
    mistake_tags = detect_mistakes(challenge, code, graded)

    # 5. Generate deterministic base explanation and attempt AI rewrite
    base_explanation = build_deterministic_base_explanation(
        visible_results=graded["visible"],
        hidden_results=graded["hidden"],
        all_passed=graded["all_passed"],
    )

    explanation = base_explanation
    if not graded["all_passed"]:
        failed_hidden = [h for h in graded["hidden"] if h["outcome"] != "passed"]
        failed_categories = sorted(set(h["category"] for h in failed_hidden if "category" in h))
        vis_summaries = [f"Test #{v['test']}: {v['outcome']}" for v in graded["visible"]]

        ai_prompt = build_explanation_prompt(
            challenge_title=challenge.title,
            challenge_description=challenge.description,
            learner_code=code,
            passed_count=sum(1 for t in (graded["visible"] + graded["hidden"]) if t["outcome"] == "passed"),
            total_count=len(graded["visible"]) + len(graded["hidden"]),
            failed_categories=failed_categories,
            mistake_tags=mistake_tags,
            visible_summaries=vis_summaries,
        )
        ai_explanation = call_model(
            purpose="explanation_rewrite",
            system_instruction=SYSTEM_INSTRUCTION,
            user_content=ai_prompt,
            hidden_tests=challenge.hidden_tests,
            is_hint=False,
        )
        if ai_explanation is not None:
            explanation = ai_explanation

    # 6. Evaluate prediction if provided
    prediction_result = None
    all_tests = graded["visible"] + graded["hidden"]
    if request.prediction:
        if any(t["outcome"] == "timeout" for t in all_tests):
            actual_pred = "timeout"
        elif any(t["outcome"] == "runtime_error" for t in all_tests):
            actual_pred = "runtime_error"
        elif graded["all_passed"]:
            actual_pred = "all_pass"
        else:
            actual_pred = "some_fail"

        is_correct = (request.prediction == actual_pred)
        prediction_result = {
            "predicted": request.prediction,
            "actual": actual_pred,
            "correct": is_correct,
        }

    # 7. Format clean visible results and Bug Arena status
    clean_graded = strip_for_api(graded)
    arena_status = build_arena_status(clean_graded["visible"], graded["hidden"])

    # 8. Database persistence
    conn = get_db_connection()
    try:
        _ensure_learner_exists(conn, learner_id)
        _ensure_challenge_exists(conn, challenge)

        # Compute next sequence number and unique submission_id (e.g. s_0001)
        cur = conn.cursor()
        cur.execute("SELECT COALESCE(MAX(seq), 0) + 1 FROM submissions;")
        seq = cur.fetchone()[0]
        submission_id = f"s_{seq:04d}"

        now = _get_utc_now()
        cur.execute(
            """
            INSERT INTO submissions (
                id, seq, learner_id, challenge_id, code, all_passed, hints_used,
                prediction, mistake_tags_json, explanation, summary_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, 0, ?, ?, ?, ?, ?);
            """,
            (
                submission_id,
                seq,
                learner_id,
                challenge.id,
                code,
                1 if graded["all_passed"] else 0,
                request.prediction,
                json.dumps(mistake_tags),
                explanation,
                json.dumps({
                    "visible": clean_graded["visible"],
                    "arena": arena_status,
                    "visible_passed": sum(1 for v in graded["visible"] if v["outcome"] == "passed"),
                    "visible_total": len(graded["visible"]),
                    "hidden_passed": sum(1 for h in graded["hidden"] if h["outcome"] == "passed"),
                    "hidden_total": len(graded["hidden"]),
                }),
                now,
            ),
        )

        # Store mistake tags in mistakes table
        for tag in mistake_tags:
            cur.execute(
                """
                INSERT INTO mistakes (learner_id, challenge_id, submission_id, tag, created_at)
                VALUES (?, ?, ?, ?, ?);
                """,
                (learner_id, challenge.id, submission_id, tag, now),
            )

        # Store test_results
        for v in graded["visible"]:
            cur.execute(
                """
                INSERT INTO test_results (
                    submission_id, kind, test_no, category, outcome, limit_hit
                ) VALUES (?, 'visible', ?, NULL, ?, ?);
                """,
                (submission_id, v["test"], v["outcome"], v.get("limit")),
            )

        # Store hidden test_results (SECURITY: strictly stores outcome and category, never inputs or outputs)
        for h in graded["hidden"]:
            cur.execute(
                """
                INSERT INTO test_results (
                    submission_id, kind, test_no, category, outcome, limit_hit
                ) VALUES (?, 'hidden', ?, ?, ?, ?);
                """,
                (submission_id, h["test"], h.get("category"), h["outcome"], h.get("limit")),
            )

        # Store prediction if present
        if prediction_result:
            cur.execute(
                """
                INSERT INTO predictions (
                    learner_id, submission_id, predicted, actual, correct, created_at
                ) VALUES (?, ?, ?, ?, ?, ?);
                """,
                (
                    learner_id,
                    submission_id,
                    prediction_result["predicted"],
                    prediction_result["actual"],
                    1 if prediction_result["correct"] else 0,
                    now,
                ),
            )

        # Check prior hints used for this challenge
        cur.execute(
            """
            SELECT COALESCE(MAX(hints_used), 0)
            FROM submissions
            WHERE learner_id = ? AND challenge_id = ?;
            """,
            (learner_id, challenge.id),
        )
        prior_hints = cur.fetchone()[0]

        # Update concept mastery scores
        update_learner_mastery(
            conn=conn,
            learner_id=learner_id,
            challenge=challenge,
            all_passed=graded["all_passed"],
            hints_used=prior_hints if graded["all_passed"] else 0,
            mistake_tags=mistake_tags,
        )

        # Check and award achievements (first_bug, three_bugs, no_hint_streak, boundary_breaker, bug_habit_broken)
        check_and_award_all_achievements(
            conn=conn,
            learner_id=learner_id,
            challenge=challenge,
            all_passed=graded["all_passed"],
            hints_used=prior_hints if graded["all_passed"] else 0,
            mistake_tags=mistake_tags,
        )

        # Award XP for solving challenge and prediction accuracy
        if graded["all_passed"]:
            cur.execute(
                "INSERT INTO xp_events (learner_id, amount, reason, created_at) VALUES (?, 50, 'challenge_solved', ?);",
                (learner_id, now),
            )
            if prior_hints == 0:
                cur.execute(
                    "INSERT INTO xp_events (learner_id, amount, reason, created_at) VALUES (?, 20, 'no_hint_solve_bonus', ?);",
                    (learner_id, now),
                )
        if prediction_result and prediction_result["correct"]:
            cur.execute(
                "INSERT INTO xp_events (learner_id, amount, reason, created_at) VALUES (?, 10, 'prediction_correct', ?);",
                (learner_id, now),
            )

        conn.commit()
    finally:
        conn.close()

    return {
        "submission_id": submission_id,
        "learner_id": learner_id,
        "challenge_id": challenge.id,
        "visible": clean_graded["visible"],
        "hidden": clean_graded["hidden"],
        "all_passed": clean_graded["all_passed"],
        "mistake_tags": mistake_tags,
        "explanation": explanation,
        "hints_available": len(challenge.hints.get("generic", [])),
        "prediction_result": prediction_result,
        "arena": arena_status,
    }


# ==============================================================================
# Hint Endpoint
# ==============================================================================

def _editor_code_hint(request: HintRequest, authorization: Optional[str]) -> dict[str, Any]:
    """Hint about editor code before submitting (see get_hint). Writes nothing to the database."""
    code = request.code or ""
    if len(code) > MAX_CODE_LENGTH:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Code exceeds maximum length of {MAX_CODE_LENGTH} characters",
        )
    if request.learner_id:
        learner_id = request.learner_id.strip()
        if not LEARNER_ID_REGEX.match(learner_id):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid learner_id")
        if authorization:
            conn = get_db_connection()
            try:
                verify_learner_token(conn, learner_id, authorization)
            finally:
                conn.close()

    try:
        challenge = get_full_challenge(request.challenge_id)
    except KeyError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Challenge not found")

    level = request.hint_level or 1
    findings = analyze_code(challenge, code)
    visible_outcomes: list[str] = []
    if request.all_levels:
        # Live mode: instant, deterministic, never runs the sandbox or calls the AI.
        aware = build_code_aware_ladder(challenge, code, list(request.visible_outcomes or []))
        if not aware:
            return {
                "hint_number": 0, "hint": "", "text": "", "hints_total": 0, "hints_remaining": 0,
                "exhausted": False, "ladder": [], "focus": "clean",
            }
        ladder, kind = aware
        return {
            "hint_number": 1, "hint": ladder[0], "text": ladder[0], "hints_total": len(ladder),
            "hints_remaining": len(ladder) - 1, "exhausted": False, "ladder": ladder, "focus": kind,
            "notes": extra_notes(challenge, code),
        }
    if not findings:
        # Code looks structurally fine: use the learner-visible test outcomes (never hidden tests).
        try:
            visible_outcomes = [
                v["outcome"]
                for v in grade_run(
                    function_name=challenge.function_name,
                    visible_tests=challenge.visible_tests,
                    code=code,
                )
            ]
        except Exception:
            visible_outcomes = []
    aware = build_code_aware_ladder(challenge, code, visible_outcomes)
    ladder = aware[0] if aware else challenge.hints.get("generic", [])
    if not ladder:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No hints available for this challenge")
    total = len(ladder)
    level = min(level, total)
    base_hint_text = ladder[level - 1]

    ai_hint = call_model(
        purpose="hint_personalization",
        system_instruction=SYSTEM_INSTRUCTION,
        user_content=build_hint_prompt(
            challenge_description=challenge.description,
            learner_code=code,
            authored_hint=base_hint_text,
            hint_level=level,
            mistake_tags=[],
        ),
        hidden_tests=challenge.hidden_tests,
        is_hint=True,
    )
    text = ai_hint if ai_hint is not None else base_hint_text
    return {
        "hint_number": level,
        "hint": text,
        "text": text,
        "hints_total": total,
        "hints_remaining": max(0, total - level),
        "exhausted": level >= total,
    }


@router.post("/hint", response_model=HintResponse)
def get_hint(request: HintRequest, authorization: Optional[str] = Header(None)) -> dict[str, Any]:
    """Retrieve the next progressive hint.

    Two modes:
    - ``submission_id``: the next hint for a graded attempt (tracked in the hints table).
    - ``challenge_id`` + ``code``: a hint about the code currently in the editor, available
      before any submit. Stateless: the client sends ``hint_level`` (1-3) and nothing is stored.
    """
    if not request.submission_id:
        return _editor_code_hint(request, authorization)
    submission_id = request.submission_id.strip()

    conn = get_db_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT id, challenge_id, code, all_passed, hints_used, mistake_tags_json FROM submissions WHERE id = ?;",
            (submission_id,),
        )
        row = cur.fetchone()
        if not row:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Submission not found",
            )

        challenge_id = row["challenge_id"]
        all_passed = bool(row["all_passed"])
        learner_code = row["code"]
        raw_mistake_tags = row["mistake_tags_json"]
        mistake_tags = json.loads(raw_mistake_tags) if raw_mistake_tags else []

        try:
            challenge = get_full_challenge(challenge_id)
        except KeyError:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Associated challenge not found",
            )

        # If submission already passed all tests, no hint needed
        if all_passed:
            msg = "This submission passed all tests, no hint needed."
            return {
                "hint_number": 0,
                "hint": msg,
                "text": msg,
                "hints_total": 0,
                "hints_remaining": 0,
                "exhausted": True,
            }

        # Check existing issued hints count for this submission
        cur.execute(
            "SELECT COUNT(*) FROM hints WHERE submission_id = ?;",
            (submission_id,),
        )
        issued_count = cur.fetchone()[0]

        # Authored ladder: keyed by first mistake tag, or generic fallback
        ladder = []
        if mistake_tags:
            first_tag = mistake_tags[0]
            ladder = challenge.hints.get(first_tag, [])
        if not ladder:
            # No recognised mistake pattern: build the ladder from what the learner actually wrote,
            # and only fall back to the challenge's generic ladder if nothing specific stands out.
            aware = build_code_aware_ladder(challenge, learner_code)
            ladder = aware[0] if aware else challenge.hints.get("generic", [])
        total_hints = len(ladder)

        next_hint_no = issued_count + 1
        now = _get_utc_now()

        if next_hint_no <= total_hints:
            base_hint_text = ladder[next_hint_no - 1]
            exhausted = (next_hint_no == total_hints)
            remaining = max(0, total_hints - next_hint_no)

            # Personalize hint via AI model
            hint_prompt = build_hint_prompt(
                challenge_description=challenge.description,
                learner_code=learner_code,
                authored_hint=base_hint_text,
                hint_level=next_hint_no,
                mistake_tags=mistake_tags,
            )
            ai_hint = call_model(
                purpose="hint_personalization",
                system_instruction=SYSTEM_INSTRUCTION,
                user_content=hint_prompt,
                hidden_tests=challenge.hidden_tests,
                is_hint=True,
            )

            if ai_hint is not None:
                final_hint_text = ai_hint
                source = "ai"
            else:
                final_hint_text = base_hint_text
                source = "authored"

            cur.execute(
                """
                INSERT INTO hints (submission_id, hint_number, text, source, created_at)
                VALUES (?, ?, ?, ?, ?);
                """,
                (submission_id, next_hint_no, final_hint_text, source, now),
            )
            cur.execute(
                "UPDATE submissions SET hints_used = ? WHERE id = ?;",
                (next_hint_no, submission_id),
            )
            conn.commit()
            return {
                "hint_number": next_hint_no,
                "hint": final_hint_text,
                "text": final_hint_text,
                "hints_total": total_hints,
                "hints_remaining": remaining,
                "exhausted": exhausted,
            }
        else:
            # Exhausted: repeat the last hint per API CONTRACT, and report exhaustion
            last_hint = ladder[-1] if ladder else "No more hints available for this attempt."
            return {
                "hint_number": total_hints,
                "hint": last_hint,
                "text": "No more hints available for this attempt.",
                "hints_total": total_hints,
                "hints_remaining": 0,
                "exhausted": True,
            }
    finally:
        conn.close()


# ==============================================================================
# Learner Profile Endpoint
# ==============================================================================

@router.get("/learner/{learner_id}", response_model=LearnerProfileResponse)
def get_learner_profile(
    learner_id: str,
    authorization: Optional[str] = Header(None),
) -> dict[str, Any]:
    """Retrieve learner debugging statistics, Bug DNA, mastery, and recommended challenge.

    If the learner has set a PIN, a valid Bearer token is required.
    Unprotected learners require no authentication.
    """
    learner_id = learner_id.strip()
    if not LEARNER_ID_REGEX.match(learner_id):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid learner_id",
        )

    conn = get_db_connection()
    try:
        verify_learner_token(conn, learner_id, authorization)
        _ensure_learner_exists(conn, learner_id)
        cur = conn.cursor()

        # 1. Solved challenges and hint assists
        cur.execute(
            """
            SELECT challenge_id, MAX(all_passed) as solved, MIN(hints_used) as min_hints
            FROM submissions
            WHERE learner_id = ?
            GROUP BY challenge_id;
            """,
            (learner_id,),
        )
        challenge_rows = cur.fetchall()
        solved_ids = {r["challenge_id"] for r in challenge_rows if r["solved"] == 1}
        hint_assisted = sum(1 for r in challenge_rows if r["solved"] == 1 and r["min_hints"] > 0)

        # 2. Prediction accuracy
        cur.execute(
            "SELECT COUNT(*), SUM(correct) FROM predictions WHERE learner_id = ?;",
            (learner_id,),
        )
        pred_row = cur.fetchone()
        total_preds = pred_row[0] or 0
        correct_preds = pred_row[1] or 0
        pred_accuracy = round(correct_preds / total_preds, 2) if total_preds > 0 else 0.0

        # 3. Streaks: count consecutive solves without hints
        cur.execute(
            """
            SELECT all_passed, hints_used
            FROM submissions
            WHERE learner_id = ?
            ORDER BY seq ASC;
            """,
            (learner_id,),
        )
        subs = cur.fetchall()

        current_streak = 0
        max_streak = 0
        for s in subs:
            if s["all_passed"] == 1 and s["hints_used"] == 0:
                current_streak += 1
                if current_streak > max_streak:
                    max_streak = current_streak
            elif s["all_passed"] == 0:
                current_streak = 0

        # 4. XP total
        cur.execute(
            "SELECT COALESCE(SUM(amount), 0) FROM xp_events WHERE learner_id = ?;",
            (learner_id,),
        )
        xp_total = cur.fetchone()[0]

        # 5. Mastery scores
        mastery = get_learner_mastery(conn, learner_id)

        # 6. Recurring mistakes and Bug DNA
        recurring_mistakes = get_recurring_mistakes(conn, learner_id)
        bug_dna = get_bug_dna(conn, learner_id)

        # 7. Next challenge recommendation
        next_challenge = recommend_next_challenge(conn, learner_id)

        # 8. Achievements
        cur.execute(
            "SELECT key FROM achievements WHERE learner_id = ? ORDER BY awarded_at ASC;",
            (learner_id,),
        )
        achievements = [r["key"] for r in cur.fetchall()]

        # Calculate daily streak based on unique submission dates
        cur.execute(
            """
            SELECT DISTINCT substr(created_at, 1, 10) as sub_date
            FROM submissions
            WHERE learner_id = ?
            ORDER BY sub_date DESC;
            """,
            (learner_id,),
        )
        date_rows = cur.fetchall()
        daily_streak = 0
        if date_rows:
            today = datetime.now(timezone.utc).date()
            yesterday = today - timedelta(days=1)
            dates = [datetime.strptime(r["sub_date"], "%Y-%m-%d").date() for r in date_rows]
            if dates[0] in (today, yesterday):
                expected = dates[0]
                for d in dates:
                    if d == expected:
                        daily_streak += 1
                        expected = expected - timedelta(days=1)
                    else:
                        break

        stats = {
            "bugs_defeated": len(solved_ids),
            "independent_streak": current_streak,
            "longest_streak": max_streak,
            "daily_streak": daily_streak,
            "hint_assisted_solves": hint_assisted,
            "prediction_accuracy": pred_accuracy,
            "xp": xp_total,
        }

        return {
            "learner_id": learner_id,
            "stats": stats,
            "recurring_mistakes": recurring_mistakes,
            "bug_dna": bug_dna,
            "mastery": mastery,
            "next_challenge": next_challenge,
            "achievements": achievements,
        }
    finally:
        conn.close()


# ==============================================================================
# History & Submission Inspection Endpoints (Phase 7 Feature B & D)
# ==============================================================================

@router.get("/learner/{learner_id}/history", response_model=list[HistorySubmissionItem])
def get_learner_history(
    learner_id: str,
    limit: Optional[int] = 50,
    challenge_id: Optional[str] = None,
    authorization: Optional[str] = Header(None),
) -> list[dict[str, Any]]:
    """Retrieve past submissions for a learner, with test counts, mistake tags, and prediction accuracy."""
    learner_id = learner_id.strip()
    if not LEARNER_ID_REGEX.match(learner_id):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid learner_id")

    conn = get_db_connection()
    try:
        verify_learner_token(conn, learner_id, authorization)
        cur = conn.cursor()

        # Cumulative prediction accuracy for learner
        cur.execute("SELECT COUNT(*), SUM(correct) FROM predictions WHERE learner_id = ?", (learner_id,))
        pred_row = cur.fetchone()
        tot_preds, cor_preds = pred_row[0] or 0, pred_row[1] or 0
        overall_pred_acc = round(cor_preds / tot_preds, 2) if tot_preds > 0 else None

        query = """
            SELECT id, challenge_id, created_at, all_passed, hints_used, prediction, mistake_tags_json
            FROM submissions
            WHERE learner_id = ?
        """
        params: list[Any] = [learner_id]
        if challenge_id:
            query += " AND challenge_id = ?"
            params.append(challenge_id)
        query += " ORDER BY seq DESC"
        if limit and limit > 0:
            query += " LIMIT ?"
            params.append(limit)

        cur.execute(query, tuple(params))
        sub_rows = cur.fetchall()

        history_items = []
        for row in sub_rows:
            sub_id = row["id"]
            cur.execute(
                """
                SELECT kind, outcome, COUNT(*) as cnt
                FROM test_results
                WHERE submission_id = ?
                GROUP BY kind, outcome
                """,
                (sub_id,),
            )
            res_rows = cur.fetchall()
            counts = {
                "passed": sum(r["cnt"] for r in res_rows if r["outcome"] == "passed"),
                "failed": sum(r["cnt"] for r in res_rows if r["outcome"] != "passed"),
                "visible_passed": sum(r["cnt"] for r in res_rows if r["kind"] == "visible" and r["outcome"] == "passed"),
                "visible_failed": sum(r["cnt"] for r in res_rows if r["kind"] == "visible" and r["outcome"] != "passed"),
                "hidden_passed": sum(r["cnt"] for r in res_rows if r["kind"] == "hidden" and r["outcome"] == "passed"),
                "hidden_failed": sum(r["cnt"] for r in res_rows if r["kind"] == "hidden" and r["outcome"] != "passed"),
            }
            tags = json.loads(row["mistake_tags_json"]) if row["mistake_tags_json"] else []
            history_items.append({
                "submission_id": sub_id,
                "challenge_id": row["challenge_id"],
                "created_at": row["created_at"],
                "all_passed": bool(row["all_passed"]),
                "counts": counts,
                "mistake_tags": tags,
                "hints_used": row["hints_used"] or 0,
                "prediction": row["prediction"],
                "prediction_accuracy": overall_pred_acc,
            })
        return history_items
    finally:
        conn.close()


@router.get("/submissions/{submission_id}", response_model=SubmissionDetailResponse)
def get_submission_detail(
    submission_id: str,
    authorization: Optional[str] = Header(None),
) -> dict[str, Any]:
    """Retrieve details for a specific submission, including learner code and API-safe test results.
    
    SECURITY GUARANTEE:
    Hidden test inputs, expected values, and got values are NEVER exposed; hidden tests contain outcome only.
    """
    submission_id = submission_id.strip()
    conn = get_db_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT id, seq, learner_id, challenge_id, code, all_passed, hints_used,
                   prediction, mistake_tags_json, explanation, summary_json, created_at
            FROM submissions
            WHERE id = ?
            """,
            (submission_id,),
        )
        row = cur.fetchone()
        if not row:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Submission not found")

        learner_id = row["learner_id"]
        verify_learner_token(conn, learner_id, authorization)

        summary = json.loads(row["summary_json"]) if row["summary_json"] else {}
        visible_results = summary.get("visible", [])
        arena = summary.get("arena")

        # Hidden results strictly fetched from test_results table (outcome & limit only)
        cur.execute(
            """
            SELECT test_no, outcome, limit_hit, category
            FROM test_results
            WHERE submission_id = ? AND kind = 'hidden'
            ORDER BY test_no ASC
            """,
            (submission_id,),
        )
        hidden_rows = cur.fetchall()
        hidden_results = [
            {"test": r["test_no"], "outcome": r["outcome"], "limit": r["limit_hit"]}
            for r in hidden_rows
        ]

        if not arena:
            arena = build_arena_status(visible_results, [dict(r) for r in hidden_rows])

        prediction_result = None
        if row["prediction"]:
            cur.execute(
                "SELECT actual, correct FROM predictions WHERE submission_id = ?",
                (submission_id,),
            )
            p_row = cur.fetchone()
            if p_row:
                prediction_result = {
                    "predicted": row["prediction"],
                    "actual": p_row["actual"],
                    "correct": bool(p_row["correct"]),
                }

        return {
            "submission_id": row["id"],
            "learner_id": learner_id,
            "challenge_id": row["challenge_id"],
            "code": row["code"],
            "created_at": row["created_at"],
            "visible": visible_results,
            "hidden": hidden_results,
            "all_passed": bool(row["all_passed"]),
            "mistake_tags": json.loads(row["mistake_tags_json"]) if row["mistake_tags_json"] else [],
            "explanation": row["explanation"],
            "hints_used": row["hints_used"] or 0,
            "prediction": row["prediction"],
            "prediction_result": prediction_result,
            "arena": arena,
        }
    finally:
        conn.close()


# ==============================================================================
# Editor Drafts Endpoints (Phase 7 Feature C)
# ==============================================================================

@router.get("/drafts/{learner_id}/{challenge_id}", response_model=DraftResponse)
def get_draft(
    learner_id: str,
    challenge_id: str,
    authorization: Optional[str] = Header(None),
) -> dict[str, Any]:
    """Retrieve saved editor draft code for a learner and challenge.
    
    If no draft exists, returns the challenge starter_code.
    """
    learner_id = learner_id.strip()
    if not LEARNER_ID_REGEX.match(learner_id):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid learner_id")

    try:
        challenge = get_public_challenge(challenge_id)
    except KeyError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Challenge not found")

    conn = get_db_connection()
    try:
        verify_learner_token(conn, learner_id, authorization)
        cur = conn.cursor()
        cur.execute(
            "SELECT code, updated_at FROM drafts WHERE learner_id = ? AND challenge_id = ?",
            (learner_id, challenge_id),
        )
        row = cur.fetchone()
        if row:
            return {
                "learner_id": learner_id,
                "challenge_id": challenge_id,
                "code": row["code"],
                "updated_at": row["updated_at"],
            }
        return {
            "learner_id": learner_id,
            "challenge_id": challenge_id,
            "code": challenge["starter_code"],
            "updated_at": _get_utc_now(),
        }
    finally:
        conn.close()


@router.put("/drafts/{learner_id}/{challenge_id}", response_model=DraftResponse)
def save_draft(
    learner_id: str,
    challenge_id: str,
    request: DraftRequest,
    authorization: Optional[str] = Header(None),
) -> dict[str, Any]:
    """Save editor draft code so a browser refresh does not lose work (max 20,000 chars)."""
    learner_id = learner_id.strip()
    if not LEARNER_ID_REGEX.match(learner_id):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid learner_id")

    try:
        get_public_challenge(challenge_id)
    except KeyError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Challenge not found")

    if len(request.code) > MAX_CODE_LENGTH:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Draft code exceeds maximum length of 20,000 characters",
        )

    conn = get_db_connection()
    try:
        verify_learner_token(conn, learner_id, authorization)
        _ensure_learner_exists(conn, learner_id)
        now_iso = _get_utc_now()
        with conn:
            conn.execute(
                """
                INSERT INTO drafts (learner_id, challenge_id, code, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(learner_id, challenge_id) DO UPDATE SET
                    code = excluded.code,
                    updated_at = excluded.updated_at
                """,
                (learner_id, challenge_id, request.code, now_iso),
            )
        return {
            "learner_id": learner_id,
            "challenge_id": challenge_id,
            "code": request.code,
            "updated_at": now_iso,
        }
    finally:
        conn.close()


# ==============================================================================
# Counterexample Hunter (Phase 7 Feature E)
# ==============================================================================

@router.post("/counterexample", response_model=CounterexampleResponse)
def test_counterexample(request: CounterexampleRequest) -> dict[str, Any]:
    """Test a learner-crafted counterexample input against their submission and reference solution.

    WHY:
    - The reference solution is author-written trusted code and contains no learner input,
      so executing it in-process on the host to compute the expected output is safe,
      deterministic, and avoids container startup overhead.
    - Learner code is arbitrary untrusted code, run strictly inside the Docker sandbox.
    - Never leaks hidden test inputs: evaluates user-provided arguments neutrally.
    - Awards 10 XP the first time an exposing input is found for a submission, and the 'edge_case_hunter' achievement.
    """
    sub_id = request.submission_id.strip()
    conn = get_db_connection()
    try:
        cur = conn.cursor()
        cur.execute("SELECT id, learner_id, challenge_id, code FROM submissions WHERE id = ?", (sub_id,))
        sub_row = cur.fetchone()
        if not sub_row:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Submission not found")

        learner_id = sub_row["learner_id"]
        challenge_id = sub_row["challenge_id"]
        learner_code = sub_row["code"]
        challenge = get_full_challenge(challenge_id)

        # 1. Validate argument shape and size limits
        if len(request.args) != len(challenge.arg_names):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Expected {len(challenge.arg_names)} arguments ({', '.join(challenge.arg_names)}), got {len(request.args)}",
            )
        args_json = json.dumps(request.args)
        if len(args_json) > 5000:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Arguments exceed maximum allowed payload size",
            )

        # 2. Format call representation
        args_repr = ", ".join(repr(a) for a in request.args)
        call_str = f"{challenge.function_name}({args_repr})"

        # 3. Compute expected result on host using trusted reference solution
        try:
            expected_val = execute_reference(challenge_id, challenge.function_name, request.args)
            expected_str = json.dumps(expected_val)
        except Exception as e:
            expected_str = f"Error: {type(e).__name__}"
            expected_val = None

        # 4. Compute got result inside Docker sandbox using learner code
        test_spec = [{"id": 1, "args": request.args, "capture_stdout": False}]
        sandbox_res = grader_module.run_in_sandbox(
            code=learner_code,
            function_name=challenge.function_name,
            tests=test_spec,
            per_test_timeout=3.0,
            total_timeout=10.0,
        )

        res = sandbox_res[0] if sandbox_res else {"status": "error", "value": None}
        if res.get("status") == "ok":
            got_val = res.get("value")
            got_str = json.dumps(got_val)
            exposes_bug = (got_val != expected_val)
        elif res.get("status") == "timeout":
            got_str = "timeout"
            exposes_bug = True
        else:
            got_str = f"Error: {res.get('exc_type', 'RuntimeError')}"
            exposes_bug = True

        # 5. Award 10 XP and edge_case_hunter achievement if bug exposed
        now_iso = _get_utc_now()
        xp_awarded = 0
        if exposes_bug:
            cur.execute(
                "SELECT id FROM counterexamples WHERE submission_id = ? AND exposes_bug = 1",
                (sub_id,),
            )
            already_found = cur.fetchone()
            if not already_found:
                xp_awarded = 10
                cur.execute(
                    "INSERT INTO xp_events (learner_id, amount, reason, created_at) VALUES (?, 10, 'counterexample_found', ?)",
                    (learner_id, now_iso),
                )
                cur.execute(
                    "INSERT OR IGNORE INTO achievements (learner_id, key, awarded_at) VALUES (?, 'edge_case_hunter', ?)",
                    (learner_id, now_iso),
                )

        cur.execute(
            """
            INSERT INTO counterexamples (learner_id, submission_id, args_json, exposes_bug, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (learner_id, sub_id, args_json, 1 if exposes_bug else 0, now_iso),
        )
        conn.commit()

        message = (
            "Counterexample found! +10 Debugging XP"
            if exposes_bug
            else "Not yet. Think about the smallest or most unusual valid input."
        )
        return {
            "exposes_bug": exposes_bug,
            "call": call_str,
            "expected": expected_str,
            "got": got_str,
            "xp_awarded": xp_awarded,
            "message": message,
        }
    finally:
        conn.close()


# ==============================================================================
# Explain-It-Back (Phase 7 Feature F)
# ==============================================================================

@router.post("/explain", response_model=ExplainResponse)
def evaluate_explanation(request: ExplainRequest) -> dict[str, Any]:
    """Evaluate a learner's 'Explain-It-Back' reflection after all tests pass."""
    sub_id = request.submission_id.strip()
    explanation_text = request.explanation.strip()
    if len(explanation_text) < 5:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Please provide a more detailed explanation.",
        )

    conn = get_db_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT id, learner_id, challenge_id, all_passed, mistake_tags_json FROM submissions WHERE id = ?",
            (sub_id,),
        )
        sub_row = cur.fetchone()
        if not sub_row:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Submission not found")
        if not sub_row["all_passed"]:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Explain-It-Back is only available after all tests have passed.",
            )

        learner_id = sub_row["learner_id"]
        challenge = get_full_challenge(sub_row["challenge_id"])

        # Model evaluation with fallback
        prompt = (
            f"Challenge: {challenge.title}\n"
            f"Concepts: {', '.join(challenge.concepts)}\n"
            f"Learner Explanation: {explanation_text}\n\n"
            "Evaluate learner understanding. Return JSON ONLY with keys:\n"
            '{"concept_understanding": "good"|"partial"|"missing", '
            '"edge_case_understanding": "good"|"partial"|"missing", '
            '"feedback": "2-3 encouraging sentences without code"}'
        )

        model_res = call_model(
            purpose="explain_it_back",
            system_instruction=SYSTEM_INSTRUCTION,
            user_content=prompt,
            hidden_tests=challenge.hidden_tests,
            is_hint=False,
        )

        concept_rating = "partial"
        edge_rating = "missing"
        feedback = "Good reflection! You clearly articulated how your algorithm executes. Be sure to consider edge cases like empty or boundary inputs."

        if model_res:
            try:
                cleaned_json = model_res.strip()
                if cleaned_json.startswith("```"):
                    cleaned_json = re.sub(r"^```(?:json)?\s*", "", cleaned_json)
                    cleaned_json = re.sub(r"\s*```$", "", cleaned_json)
                parsed = json.loads(cleaned_json)
                if parsed.get("concept_understanding") in ("good", "partial", "missing"):
                    concept_rating = parsed["concept_understanding"]
                if parsed.get("edge_case_understanding") in ("good", "partial", "missing"):
                    edge_rating = parsed["edge_case_understanding"]
                if parsed.get("feedback") and "```" not in parsed["feedback"]:
                    feedback = parsed["feedback"]
            except Exception:
                pass

        # If model was not configured or failed, run deterministic keyword rubric
        if not model_res:
            lower_exp = explanation_text.lower()
            concept_keywords = {"loop", "iterate", "list", "index", "slice", "sum", "count", "even", "odd", "sort", "reverse", "condition"}
            edge_keywords = {"empty", "zero", "single", "boundary", "length", "larger", "fewer", "negative", "equal", "cutoff", "duplicate"}

            concept_hits = sum(1 for kw in concept_keywords if kw in lower_exp)
            edge_hits = sum(1 for kw in edge_keywords if kw in lower_exp)

            concept_rating = "good" if concept_hits >= 1 else "partial"
            if edge_hits >= 2:
                edge_rating = "good"
            elif edge_hits == 1:
                edge_rating = "partial"
            else:
                edge_rating = "missing"

        now_iso = _get_utc_now()
        ratings = {
            "code_correctness": "good",
            "concept_understanding": concept_rating,
            "edge_case_understanding": edge_rating,
            "feedback": feedback,
        }

        # Store explanation
        cur.execute(
            """
            INSERT INTO explanations (learner_id, submission_id, text, ratings_json, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (learner_id, sub_id, explanation_text, json.dumps(ratings), now_iso),
        )

        # Award 15 XP for completing reflection (first time per submission)
        cur.execute(
            "SELECT id FROM xp_events WHERE learner_id = ? AND reason = ?;",
            (learner_id, f"explain_{sub_id}"),
        )
        if not cur.fetchone():
            cur.execute(
                "INSERT INTO xp_events (learner_id, amount, reason, created_at) VALUES (?, 15, ?, ?);",
                (learner_id, f"explain_{sub_id}", now_iso),
            )

        conn.commit()
        return ratings
    finally:
        conn.close()


# ==============================================================================
# Socratic Coach (Phase 7 Feature G)
# ==============================================================================

@router.post("/coach", response_model=CoachResponse)
def coach_chat(request: CoachRequest) -> dict[str, Any]:
    """Socratic Coach dialogue endpoint.

    SECURITY GUARANTEES:
    - Never provides code solutions, complete functions, or copy-paste fixes.
    - If asked for code, refuses politely and asks a guiding question.
    - All replies pass strict guardrails (no markdown code fences or leaks).
    """
    sub_id = request.submission_id.strip()
    msg = request.message.strip()
    if not msg:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Message cannot be empty")

    conn = get_db_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT id, learner_id, challenge_id, code FROM submissions WHERE id = ?",
            (sub_id,),
        )
        sub_row = cur.fetchone()
        if not sub_row:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Submission not found")

        challenge = get_full_challenge(sub_row["challenge_id"])

        # Fetch last 6 messages
        cur.execute(
            """
            SELECT role, content FROM coach_messages
            WHERE submission_id = ?
            ORDER BY id DESC
            LIMIT 6
            """,
            (sub_id,),
        )
        history = list(reversed(cur.fetchall()))
        history_prompts = [f"{h['role'].capitalize()}: {h['content']}" for h in history]

        prompt = build_socratic_reply_prompt(
            challenge_title=challenge.title,
            challenge_description=challenge.description,
            learner_code=sub_row["code"],
            learner_message=msg,
            previous_hints=history_prompts,
        )

        model_reply = call_model(
            purpose="coach_chat",
            system_instruction=SYSTEM_INSTRUCTION,
            user_content=prompt,
            hidden_tests=challenge.hidden_tests,
            is_hint=False,
        )

        fallback_reply = (
            "I'm here to help guide your debugging process rather than write the code for you! "
            "Which part of your current logic or boundary condition feels most uncertain?"
        )

        final_reply = fallback_reply
        if model_reply:
            is_safe, _ = verify_coach_reply(model_reply, hidden_tests=challenge.hidden_tests)
            if is_safe:
                final_reply = model_reply

        now_iso = _get_utc_now()
        with conn:
            conn.execute(
                "INSERT INTO coach_messages (submission_id, role, content, created_at) VALUES (?, 'user', ?, ?)",
                (sub_id, msg, now_iso),
            )
            conn.execute(
                "INSERT INTO coach_messages (submission_id, role, content, created_at) VALUES (?, 'assistant', ?, ?)",
                (sub_id, final_reply, now_iso),
            )

        return {"reply": final_reply}
    finally:
        conn.close()


# ==============================================================================
# Learner Management (Phase 7 Feature J)
# ==============================================================================

@router.post("/learner/{learner_id}/reset", response_model=ResetResponse)
def reset_learner_progress(
    learner_id: str,
    authorization: Optional[str] = Header(None),
) -> dict[str, Any]:
    """Reset all progress for a learner (deletes submissions, mastery, drafts, achievements).

    Protected profiles (with PIN set) require Bearer authentication.
    """
    learner_id = learner_id.strip()
    if not LEARNER_ID_REGEX.match(learner_id):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid learner_id")

    conn = get_db_connection()
    try:
        verify_learner_token(conn, learner_id, authorization)
        with conn:
            conn.execute("DELETE FROM submissions WHERE learner_id = ?", (learner_id,))
            conn.execute("DELETE FROM mastery WHERE learner_id = ?", (learner_id,))
            conn.execute("DELETE FROM drafts WHERE learner_id = ?", (learner_id,))
            conn.execute("DELETE FROM achievements WHERE learner_id = ?", (learner_id,))
            conn.execute("DELETE FROM xp_events WHERE learner_id = ?", (learner_id,))
            conn.execute("DELETE FROM predictions WHERE learner_id = ?", (learner_id,))
            conn.execute("DELETE FROM mistakes WHERE learner_id = ?", (learner_id,))
            conn.execute("DELETE FROM counterexamples WHERE learner_id = ?", (learner_id,))
            conn.execute("DELETE FROM explanations WHERE learner_id = ?", (learner_id,))
        return {"status": "ok", "message": f"Progress for {learner_id} has been reset."}
    finally:
        conn.close()


@router.get("/learner/{learner_id}/export")
def export_learner_data(
    learner_id: str,
    authorization: Optional[str] = Header(None),
) -> dict[str, Any]:
    """Export complete JSON archive of all learner activity and profile data."""
    learner_id = learner_id.strip()
    if not LEARNER_ID_REGEX.match(learner_id):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid learner_id")

    conn = get_db_connection()
    try:
        verify_learner_token(conn, learner_id, authorization)
        cur = conn.cursor()
        cur.execute("SELECT id, display_name, created_at FROM learners WHERE id = ?", (learner_id,))
        learner_row = cur.fetchone()
        if not learner_row:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Learner not found")

        cur.execute("SELECT * FROM submissions WHERE learner_id = ? ORDER BY seq ASC", (learner_id,))
        submissions = [dict(r) for r in cur.fetchall()]

        cur.execute("SELECT concept, score, updated_at FROM mastery WHERE learner_id = ?", (learner_id,))
        mastery = [dict(r) for r in cur.fetchall()]

        cur.execute("SELECT challenge_id, code, updated_at FROM drafts WHERE learner_id = ?", (learner_id,))
        drafts = [dict(r) for r in cur.fetchall()]

        cur.execute("SELECT key, awarded_at FROM achievements WHERE learner_id = ?", (learner_id,))
        achievements = [dict(r) for r in cur.fetchall()]

        cur.execute("SELECT amount, reason, created_at FROM xp_events WHERE learner_id = ?", (learner_id,))
        xp_events = [dict(r) for r in cur.fetchall()]

        return {
            "learner": dict(learner_row),
            "submissions": submissions,
            "mastery": mastery,
            "drafts": drafts,
            "achievements": achievements,
            "xp_events": xp_events,
        }
    finally:
        conn.close()


