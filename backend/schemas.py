"""Pydantic schemas matching docs/API_CONTRACT.md.

Enforces exact field names, data types, and literal outcome types.
"""

from typing import Any, Literal, Optional
from typing import Literal
from pydantic import BaseModel, Field, model_validator

# Outcome labels are strictly the 4 specified in SECURITY RULES and API CONTRACT
OutcomeType = Literal["passed", "wrong_output", "runtime_error", "timeout"]
DifficultyType = Literal["easy", "medium", "hard"]
ChallengeStatusType = Literal["new", "attempted", "solved"]
PredictionType = Literal["all_pass", "some_fail", "runtime_error", "timeout"]
RatingType = Literal["good", "partial", "missing"]


# --- Health & Error Schemas ---

class HealthResponse(BaseModel):
    """Health check response schema."""

    status: str = "ok"


class ErrorResponse(BaseModel):
    """Generic error response schema."""

    error: str


# --- Challenge Schemas ---

class ChallengeSummaryResponse(BaseModel):
    """GET /api/challenges summary item."""

    id: str
    title: str
    difficulty: DifficultyType
    concepts: list[str]
    status: ChallengeStatusType = "new"


class VisibleTestCase(BaseModel):
    """Visible test case metadata in challenge detail."""

    test: int
    call: str
    expected: str


class ChallengeDetailResponse(BaseModel):
    """GET /api/challenges/{id} full specification."""

    id: str
    title: str
    difficulty: DifficultyType
    concepts: list[str]
    description: str
    starter_code: str
    visible_tests: list[VisibleTestCase]
    hidden_test_count: int
    arg_names: list[str]


# --- Submission Schemas ---

class SubmitRequest(BaseModel):
    """POST /api/submit request body."""

    learner_id: str
    challenge_id: str
    code: str
    prediction: Optional[PredictionType] = None


class VisibleTestResult(BaseModel):
    """Visible test execution result."""

    test: int
    call: str
    expected: str
    got: Optional[str] = None
    outcome: OutcomeType
    limit: Optional[str] = None
    error: Optional[str] = None


class HiddenTestResult(BaseModel):
    """Hidden test execution result.

    SECURITY RULE: Hidden test inputs, expected outputs, got outputs,
    and tracebacks MUST NEVER be defined or returned here.
    """

    test: int
    outcome: OutcomeType
    limit: Optional[str] = None


class PredictionResult(BaseModel):
    """Prediction outcome evaluation."""

    predicted: PredictionType
    actual: PredictionType
    correct: bool


class ArenaResult(BaseModel):
    """Bug Arena test case progress breakdown."""

    defeated: list[str]
    remaining: list[str]


class SubmitResponse(BaseModel):
    """POST /api/submit response body."""

    submission_id: str
    learner_id: str
    challenge_id: str
    visible: list[VisibleTestResult]
    hidden: list[HiddenTestResult]
    all_passed: bool
    mistake_tags: list[str] = Field(default_factory=list)
    explanation: str
    hints_available: int
    prediction_result: Optional[PredictionResult] = None
    arena: Optional[ArenaResult] = None


# --- Run Schemas (LeetCode-Style Run Button) ---

class RunRequest(BaseModel):
    """POST /api/run request body."""

    learner_id: str
    challenge_id: str
    code: str


class RunResponse(BaseModel):
    """POST /api/run response body."""

    visible: list[VisibleTestResult]


# --- Hint Schemas ---

class HintRequest(BaseModel):
    """POST /api/hint request body."""

    # Either a submission_id (hint about that graded attempt) OR challenge_id + code
    # (hint about the code currently in the editor, before any submit).
    submission_id: Optional[str] = None
    learner_id: Optional[str] = None
    challenge_id: Optional[str] = None
    code: Optional[str] = None
    hint_level: Optional[int] = Field(None, ge=1, le=3)
    # Live mode (editor hints only): return the whole 3-layer ladder at once. Static analysis only:
    # fast, no AI call, no sandbox run. `visible_outcomes` are the learner-visible results of their last Run.
    all_levels: bool = False
    visible_outcomes: Optional[list[Literal["passed", "wrong_output", "runtime_error", "timeout"]]] = Field(None, max_length=50)

    @model_validator(mode="after")
    def _needs_target(self) -> "HintRequest":
        if not self.submission_id and not (self.challenge_id and self.code is not None):
            raise ValueError("Provide submission_id, or challenge_id together with code")
        return self


class HintResponse(BaseModel):
    """POST /api/hint response body."""

    hint_number: int
    hint: str
    hints_total: int
    exhausted: bool
    text: Optional[str] = None
    hints_remaining: Optional[int] = None
    # Live mode only: every layer at once (Nudge, Pointer, Approach), and what the hint is about
    ladder: Optional[list[str]] = None
    notes: Optional[list[str]] = None
    focus: Optional[str] = None


# --- Learner Profile Schemas ---

class LearnerStats(BaseModel):
    """High-level learner debugging statistics."""

    bugs_defeated: int = 0
    independent_streak: int = 0
    longest_streak: int = 0
    daily_streak: int = 0
    hint_assisted_solves: int = 0
    prediction_accuracy: float = 0.0
    xp: int = 0


class RecurringMistake(BaseModel):
    """Recurring mistake pattern."""

    tag: str
    challenges: list[str]
    count: int
    description: str


class BugDnaItem(BaseModel):
    """Item in Bug DNA frequency chart."""

    tag: str
    count: int


class NextChallengeRecommendation(BaseModel):
    """Recommended challenge with personalized reason."""

    id: str
    title: str
    reason: str


class LearnerProfileResponse(BaseModel):
    """GET /api/learner/{learner_id} response body."""

    learner_id: str
    stats: LearnerStats
    recurring_mistakes: list[RecurringMistake] = Field(default_factory=list)
    bug_dna: list[BugDnaItem] = Field(default_factory=list)
    mastery: dict[str, float] = Field(default_factory=dict)
    next_challenge: Optional[NextChallengeRecommendation] = None
    achievements: list[str] = Field(default_factory=list)


# --- Interactive Phase Schemas ---

class CounterexampleRequest(BaseModel):
    """POST /api/counterexample request body."""

    submission_id: str
    args: list[Any]


class CounterexampleResponse(BaseModel):
    """POST /api/counterexample response body."""

    exposes_bug: bool
    call: str
    expected: str
    got: str
    xp_awarded: int
    message: str


class ExplainRequest(BaseModel):
    """POST /api/explain request body."""

    submission_id: str
    explanation: str


class ExplainResponse(BaseModel):
    """POST /api/explain response body."""

    code_correctness: RatingType
    concept_understanding: RatingType
    edge_case_understanding: RatingType
    feedback: str


class CoachRequest(BaseModel):
    """POST /api/coach request body."""

    submission_id: str
    message: str


class CoachResponse(BaseModel):
    """POST /api/coach response body."""

    reply: str


# --- Phase 7 Additions ---

class RegisterRequest(BaseModel):
    """POST /api/auth/register request body."""

    display_name: str = Field(..., min_length=1, max_length=50)
    pin: Optional[str] = Field(None, min_length=4, max_length=8)


class RegisterResponse(BaseModel):
    """POST /api/auth/register response body."""

    learner_id: str
    token: Optional[str] = None


class LoginRequest(BaseModel):
    """POST /api/auth/login request body."""

    learner_id: str
    pin: str = Field(..., min_length=4, max_length=8)


class LoginResponse(BaseModel):
    """POST /api/auth/login response body."""

    token: str


class HistorySubmissionItem(BaseModel):
    """Single submission entry in GET /api/learner/{id}/history."""

    submission_id: str
    challenge_id: str
    created_at: str
    all_passed: bool
    counts: dict[str, int]
    mistake_tags: list[str] = Field(default_factory=list)
    hints_used: int = 0
    prediction: Optional[str] = None
    prediction_accuracy: Optional[float] = None


class HistoryResponse(BaseModel):
    """GET /api/learner/{id}/history response body."""

    submissions: list[HistorySubmissionItem]


class SubmissionDetailResponse(BaseModel):
    """GET /api/submissions/{submission_id} response body."""

    submission_id: str
    learner_id: str
    challenge_id: str
    code: str
    created_at: str
    visible: list[VisibleTestResult]
    hidden: list[HiddenTestResult]
    all_passed: bool
    mistake_tags: list[str] = Field(default_factory=list)
    explanation: Optional[str] = None
    hints_used: int = 0
    prediction: Optional[str] = None
    prediction_result: Optional[PredictionResult] = None
    arena: Optional[dict[str, list[str]]] = None


class DraftRequest(BaseModel):
    """PUT /api/drafts/{learner_id}/{challenge_id} request body."""

    code: str = Field(..., max_length=20000)


class DraftResponse(BaseModel):
    """GET/PUT /api/drafts/{learner_id}/{challenge_id} response body."""

    learner_id: str
    challenge_id: str
    code: str
    updated_at: str


class ResetResponse(BaseModel):
    """POST /api/learner/{id}/reset response body."""

    status: str
    message: str


class MutationOption(BaseModel):
    """Option for mutation prediction challenge."""

    id: str
    text: str


class MutationChallengeResponse(BaseModel):
    """GET /api/challenges/{id}/mutation response body."""

    challenge_id: str
    mutated_code: str
    question: str
    options: list[MutationOption]


class MutationAnswerRequest(BaseModel):
    """POST /api/challenges/{id}/mutation/answer request body."""

    answer_id: str
    learner_id: Optional[str] = None


class MutationAnswerResponse(BaseModel):
    """POST /api/challenges/{id}/mutation/answer response body."""

    correct: bool
    correct_id: str
    explanation: str
    xp_awarded: int = 0


