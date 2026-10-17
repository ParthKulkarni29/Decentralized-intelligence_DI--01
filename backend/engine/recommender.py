"""Personalized challenge recommendation engine.

Selects the next challenge for a learner using a three-tiered deterministic strategy:
1. Targeted remediation for recurring mistake patterns.
2. Concept reinforcement for the learner's lowest mastery score.
3. Curriculum progression by sort_order or harder challenge repetition.
"""

import sqlite3
from typing import Optional
from backend.engine.challenges import FullChallenge, list_challenges, load_all_challenges
from backend.engine.mastery import ALL_CONCEPTS, get_learner_mastery, get_recurring_mistakes

DIFFICULTY_RANK = {"easy": 1, "medium": 2, "hard": 3}


def _format_challenge_list(challenge_ids: list[str]) -> str:
    """Format a list of challenge ids into natural English ('A and B' or 'A, B and C')."""
    if not challenge_ids:
        return ""
    if len(challenge_ids) == 1:
        return challenge_ids[0]
    if len(challenge_ids) == 2:
        return f"{challenge_ids[0]} and {challenge_ids[1]}"
    return f"{', '.join(challenge_ids[:-1])} and {challenge_ids[-1]}"


def recommend_next_challenge(
    conn: sqlite3.Connection,
    learner_id: str,
    all_challenges: Optional[list[FullChallenge]] = None,
) -> dict[str, str]:
    """Select the optimal next challenge for a learner.

    Returns:
        Dict with "id", "title", and personalized "reason".
    """
    if all_challenges is None:
        all_challenges_dict = load_all_challenges()
        all_challenges = sorted(all_challenges_dict.values(), key=lambda c: c.sort_order)

    cur = conn.cursor()
    # 1. Identify challenges already solved by learner
    cur.execute(
        """
        SELECT DISTINCT challenge_id
        FROM submissions
        WHERE learner_id = ? AND all_passed = 1;
        """,
        (learner_id,),
    )
    solved_ids = {r[0] for r in cur.fetchall()}
    unsolved = [ch for ch in all_challenges if ch.id not in solved_ids]

    # ==========================================================================
    # Strategy 1: Targeted Recurring Mistake Remediation
    # ==========================================================================
    recurring = get_recurring_mistakes(conn, learner_id)
    if recurring:
        # Most frequent recurring mistake
        top_mistake = recurring[0]
        target_tag = top_mistake["tag"]
        plain_tag = target_tag.replace("_", " ")
        mistake_challenges = top_mistake["challenges"]
        ch_str = _format_challenge_list(mistake_challenges)

        # Find challenges targeting this mistake, easiest first, preferring unsolved
        candidates = [
            ch for ch in all_challenges
            if target_tag in ch.targets_mistakes
        ]
        if candidates:
            # Sort: unsolved first (0 < 1), easiest first (1 < 2 < 3), then sort_order
            candidates.sort(
                key=lambda c: (
                    0 if c.id not in solved_ids else 1,
                    DIFFICULTY_RANK.get(c.difficulty, 2),
                    c.sort_order,
                )
            )
            picked = candidates[0]
            reason = (
                f"You made the same mistake ({plain_tag}) in {ch_str}. "
                f"This challenge gives you another chance to practise it."
            )
            return {
                "id": picked.id,
                "title": picked.title,
                "reason": reason,
            }

    # ==========================================================================
    # Strategy 2: Concept Reinforcement (Lowest Mastery Score)
    # ==========================================================================
    if unsolved:
        mastery = get_learner_mastery(conn, learner_id)

        # Concepts covered by remaining unsolved challenges
        available_concepts = [c for c in ALL_CONCEPTS if any(c in ch.concepts for ch in unsolved)]

        if available_concepts:
            scores = [mastery.get(c, 0.5) for c in available_concepts]
            min_score = min(scores)
            max_score = max(scores)

            # Trigger concept reinforcement only if there is a concept with lower mastery than others
            if min_score < max_score:
                lowest_concept = min(available_concepts, key=lambda c: mastery.get(c, 0.5))
                lowest_score = mastery.get(lowest_concept, 0.5)

                # Pick unsolved challenge teaching lowest_concept, easiest first
                concept_candidates = [ch for ch in unsolved if lowest_concept in ch.concepts]
                if concept_candidates:
                    concept_candidates.sort(
                        key=lambda c: (DIFFICULTY_RANK.get(c.difficulty, 2), c.sort_order)
                    )
                    picked = concept_candidates[0]
                    reason = (
                        f"Recommended to help you strengthen your {lowest_concept} skills "
                        f"(current mastery: {int(lowest_score * 100)}%)."
                    )
                    return {
                        "id": picked.id,
                        "title": picked.title,
                        "reason": reason,
                    }

    # ==========================================================================
    # Strategy 3: Next Unsolved by sort_order or Harder Repeat if All Solved
    # ==========================================================================
    if unsolved:
        unsolved.sort(key=lambda c: c.sort_order)
        picked = unsolved[0]
        return {
            "id": picked.id,
            "title": picked.title,
            "reason": "Next challenge to practice your debugging skills.",
        }

    # All challenges are solved: suggest repeating a hard challenge
    if all_challenges:
        hardest = max(
            all_challenges,
            key=lambda c: (DIFFICULTY_RANK.get(c.difficulty, 1), c.sort_order),
        )
        return {
            "id": hardest.id,
            "title": hardest.title,
            "reason": f"You've solved all available challenges! Try repeating {hardest.title} without any hints to master it.",
        }

    return {
        "id": "last-k-total",
        "title": "Last K Total",
        "reason": "Start your debugging journey with this challenge.",
    }
