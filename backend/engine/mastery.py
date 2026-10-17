"""Mastery tracking, mistake frequency analysis, and Bug DNA.

Implements transparent concept mastery updates using exponential moving average (EMA),
computes recurring mistake patterns across challenges, and tallies learner Bug DNA.
"""

from datetime import datetime, timezone
import sqlite3
from typing import Any
from backend.engine.challenges import FullChallenge

ALL_CONCEPTS = ["loops", "lists", "indexing", "conditions", "edge_cases", "functions"]
INITIAL_MASTERY = 0.5

# Beginner-friendly plain-language descriptions for every mistake taxonomy tag
MISTAKE_DESCRIPTIONS: dict[str, str] = {
    "print_not_return": "Using print() inside the function instead of returning a value back to the caller.",
    "mutates_input": "Modifying input lists or arguments in-place, which unexpectedly changes the original data.",
    "unhandled_short_input": "Your logic works on standard inputs, but crashes or misbehaves on empty, single-element, or zero inputs.",
    "boundary_comparison": "Using a strict inequality (< or >) instead of an inclusive one (<= or >=) on a threshold cutoff value.",
    "off_by_one": "The result differs from expected by exactly one element or unit, often due to loop bounds or slicing indices.",
    "wrong_initialization": "Starting an accumulator or result variable with an incorrect initial value instead of a neutral baseline.",
    "incorrect_condition": "Checking the wrong condition or inverting boolean logic in an if statement.",
    "incorrect_accumulation": "Updating a running total incorrectly, such as incrementing a step count instead of adding element values.",
    "wrong_return_type": "Returning a data type that does not match the expected return type (e.g. list vs int).",
}


def _get_utc_now() -> str:
    """Return ISO 8601 UTC timestamp."""
    return datetime.now(timezone.utc).isoformat()


def get_learner_mastery(conn: sqlite3.Connection, learner_id: str) -> dict[str, float]:
    """Retrieve current mastery scores for all 6 concepts, defaulting to 0.5."""
    cur = conn.cursor()
    cur.execute(
        "SELECT concept, score FROM mastery WHERE learner_id = ?;",
        (learner_id,),
    )
    rows = {r["concept"]: float(r["score"]) for r in cur.fetchall()}
    return {concept: round(rows.get(concept, INITIAL_MASTERY), 4) for concept in ALL_CONCEPTS}


def update_learner_mastery(
    conn: sqlite3.Connection,
    learner_id: str,
    challenge: FullChallenge,
    all_passed: bool,
    hints_used: int,
    mistake_tags: list[str],
) -> dict[str, float]:
    """Update concept mastery scores using exponential moving average (EMA).

    Formula:
        new = old + weight * (target - old)

    Weights & Targets:
    - Pass with zero hints: target 1.0, weight 0.30
    - Pass after hints: target 1.0, weight 0.30 * max(0.25, 1 - 0.25 * hints_used)
    - Fail: target 0.0, weight 0.10 (capped at 3 failures per challenge per learner)
    - Additional edge_cases update: triggers if unhandled_short_input or
      boundary_comparison occurred on a challenge not already listing edge_cases.
    """
    cur = conn.cursor()
    now = _get_utc_now()
    current_mastery = get_learner_mastery(conn, learner_id)

    # Determine which concepts receive updates
    concepts_to_update = set(challenge.concepts)
    if not all_passed:
        if "unhandled_short_input" in mistake_tags or "boundary_comparison" in mistake_tags:
            concepts_to_update.add("edge_cases")

    # Calculate weight and target
    if all_passed:
        target = 1.0
        if hints_used == 0:
            weight = 0.30
        else:
            hint_multiplier = max(0.25, 1.0 - 0.25 * hints_used)
            weight = 0.30 * hint_multiplier
    else:
        target = 0.0
        # Count prior failures on this challenge for this learner (up to 3)
        cur.execute(
            """
            SELECT COUNT(*) FROM submissions
            WHERE learner_id = ? AND challenge_id = ? AND all_passed = 0;
            """,
            (learner_id, challenge.id),
        )
        fail_count = cur.fetchone()[0]
        # At most 3 failures per challenge per learner count toward mastery penalty
        if fail_count <= 3:
            weight = 0.10
        else:
            weight = 0.0

    # Apply EMA update
    updated_scores: dict[str, float] = dict(current_mastery)
    for concept in concepts_to_update:
        if concept in ALL_CONCEPTS:
            old = current_mastery[concept]
            new = old + weight * (target - old)
            new = max(0.0, min(1.0, new))
            updated_scores[concept] = round(new, 4)

            cur.execute(
                """
                INSERT INTO mastery (learner_id, concept, score, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(learner_id, concept) DO UPDATE SET
                    score = excluded.score,
                    updated_at = excluded.updated_at;
                """,
                (learner_id, concept, updated_scores[concept], now),
            )

    conn.commit()
    return updated_scores


def get_recurring_mistakes(conn: sqlite3.Connection, learner_id: str) -> list[dict[str, Any]]:
    """Identify mistake tags appearing in at least 2 DIFFERENT challenges.

    Repeated occurrences on the same challenge do not count.
    Returns:
        List of dicts: tag, challenges, count (distinct challenges), description.
    """
    cur = conn.cursor()
    cur.execute(
        """
        SELECT tag, challenge_id
        FROM mistakes
        WHERE learner_id = ?
        ORDER BY created_at ASC;
        """,
        (learner_id,),
    )
    rows = cur.fetchall()

    challenges_by_tag: dict[str, set[str]] = {}
    for r in rows:
        tag = r["tag"]
        ch_id = r["challenge_id"]
        if tag not in challenges_by_tag:
            challenges_by_tag[tag] = set()
        challenges_by_tag[tag].add(ch_id)

    recurring: list[dict[str, Any]] = []
    for tag, ch_set in challenges_by_tag.items():
        if len(ch_set) >= 2:
            recurring.append({
                "tag": tag,
                "challenges": sorted(list(ch_set)),
                "count": len(ch_set),
                "description": MISTAKE_DESCRIPTIONS.get(
                    tag, f"Recurring pattern of {tag.replace('_', ' ')} across challenges."
                ),
            })

    # Sort by count descending, then tag name
    recurring.sort(key=lambda x: (-x["count"], x["tag"]))
    return recurring


def get_bug_dna(conn: sqlite3.Connection, learner_id: str) -> list[dict[str, Any]]:
    """Return all mistake tags with total occurrence counts for learner, sorted by count."""
    cur = conn.cursor()
    cur.execute(
        """
        SELECT tag, COUNT(*) as count
        FROM mistakes
        WHERE learner_id = ?
        GROUP BY tag
        ORDER BY count DESC, tag ASC;
        """,
        (learner_id,),
    )
    rows = cur.fetchall()
    return [{"tag": r["tag"], "count": r["count"]} for r in rows]


def check_and_award_bug_habit_broken(
    conn: sqlite3.Connection,
    learner_id: str,
    challenge: FullChallenge,
    all_passed: bool,
    hints_used: int,
    mistake_tags: list[str],
) -> list[str]:
    """Check if a previously recurring mistake habit was broken on this challenge.

    RULE:
    When a previously recurring tag does not appear on a later submission of a
    challenge that targets it, and that submission passes without hints, record
    the achievement in the database.
    """
    if not all_passed or hints_used > 0:
        return []

    recurring = get_recurring_mistakes(conn, learner_id)
    if not recurring:
        return []

    cur = conn.cursor()
    awarded_achievements: list[str] = []
    now = _get_utc_now()

    for item in recurring:
        tag = item["tag"]
        # Challenge must target this mistake tag
        if tag in challenge.targets_mistakes:
            # Tag did not appear on this successful no-hint submission
            if tag not in mistake_tags:
                achievement_key = f"Bug Habit Broken: {tag.replace('_', ' ').title()}"

                # Insert into achievements table if not already awarded
                cur.execute(
                    """
                    INSERT OR IGNORE INTO achievements (learner_id, key, awarded_at)
                    VALUES (?, ?, ?);
                    """,
                    (learner_id, achievement_key, now),
                )
                if cur.rowcount > 0:
                    awarded_achievements.append(achievement_key)

    conn.commit()
    return awarded_achievements


def award_achievement(conn: sqlite3.Connection, learner_id: str, key: str) -> bool:
    """Award an achievement key to a learner if not already unlocked."""
    cur = conn.cursor()
    now = _get_utc_now()
    cur.execute(
        "INSERT OR IGNORE INTO achievements (learner_id, key, awarded_at) VALUES (?, ?, ?);",
        (learner_id, key, now),
    )
    awarded = (cur.rowcount > 0)
    if awarded:
        cur.execute(
            "INSERT INTO xp_events (learner_id, amount, reason, created_at) VALUES (?, 25, ?, ?);",
            (learner_id, f"achievement_{key}", now),
        )
    return awarded


def check_and_award_all_achievements(
    conn: sqlite3.Connection,
    learner_id: str,
    challenge: FullChallenge,
    all_passed: bool,
    hints_used: int,
    mistake_tags: list[str],
) -> list[str]:
    """Evaluate and award all applicable achievements upon submission.

    Keys:
    - first_bug: first solved challenge
    - three_bugs: at least 3 distinct solved challenges
    - no_hint_streak: 3 consecutive solves with 0 hints
    - boundary_breaker: solved after a previous boundary_comparison failure on this challenge
    - bug_habit_broken: broken recurring mistake habit
    """
    awarded: list[str] = []
    if not all_passed:
        return awarded

    cur = conn.cursor()

    # 1. Solved challenges count
    cur.execute(
        "SELECT COUNT(DISTINCT challenge_id) FROM submissions WHERE learner_id = ? AND all_passed = 1;",
        (learner_id,),
    )
    solved_count = cur.fetchone()[0] or 0

    if solved_count >= 1:
        if award_achievement(conn, learner_id, "first_bug"):
            awarded.append("first_bug")
    if solved_count >= 3:
        if award_achievement(conn, learner_id, "three_bugs"):
            awarded.append("three_bugs")

    # 2. No-hint streak (3 solves in a row with 0 hints)
    cur.execute(
        "SELECT all_passed, hints_used FROM submissions WHERE learner_id = ? ORDER BY seq DESC LIMIT 3;",
        (learner_id,),
    )
    last_three = cur.fetchall()
    if len(last_three) == 3 and all(r["all_passed"] == 1 and r["hints_used"] == 0 for r in last_three):
        if award_achievement(conn, learner_id, "no_hint_streak"):
            awarded.append("no_hint_streak")

    # 3. Boundary breaker: previous boundary_comparison failure on this challenge
    cur.execute(
        "SELECT id FROM mistakes WHERE learner_id = ? AND challenge_id = ? AND tag = 'boundary_comparison';",
        (learner_id, challenge.id),
    )
    if cur.fetchone():
        if award_achievement(conn, learner_id, "boundary_breaker"):
            awarded.append("boundary_breaker")

    # 4. Bug habit broken
    habit_awards = check_and_award_bug_habit_broken(
        conn, learner_id, challenge, all_passed, hints_used, mistake_tags
    )
    if habit_awards:
        if award_achievement(conn, learner_id, "bug_habit_broken"):
            awarded.append("bug_habit_broken")
        awarded.extend(habit_awards)

    return awarded
