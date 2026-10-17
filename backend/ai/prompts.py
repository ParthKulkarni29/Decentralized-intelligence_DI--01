"""Prompt builders and system instructions for the Debug Dojo AI coach.

Enforces pedagogical principles:
- The AI never writes code or reveals hidden test inputs/outputs.
- Prompts only contain challenge descriptions, learner code, mistake tags,
  test counts, and edge case CATEGORY words.
- All prompts reinforce short, beginner-friendly Socratic guidance.
"""

from typing import Any, Optional

SYSTEM_INSTRUCTION = (
    "You are Debug Dojo, a programming learning coach. Help the learner discover the bug without giving the solution. "
    "NEVER provide complete corrected code, replacement functions, copy-paste solutions, exact hidden test data, "
    "hidden expected output, hidden program output, or hidden exception messages. You MAY explain the concept, "
    "identify the relevant region of the learner's code, ask guiding questions, give progressively stronger hints, "
    "describe the CATEGORY of an edge case, and help the learner reason. The learner must perform the final correction. "
    "Do not write any code at all, not even one line or a snippet. Plain English only, short, beginner friendly. "
    "If asked for the answer, politely refuse and offer a guiding question instead."
)


def build_explanation_prompt(
    challenge_title: str,
    challenge_description: str,
    learner_code: str,
    passed_count: int,
    total_count: int,
    failed_categories: list[str],
    mistake_tags: list[str],
    visible_summaries: Optional[list[str]] = None,
) -> str:
    """Build prompt explaining what went wrong without spoiling the fix.

    SECURITY GUARANTEE:
    Only test counts and high-level category words are passed. No hidden test inputs,
    expected outputs, or hidden execution outputs are ever included.
    """
    cat_str = ", ".join(failed_categories) if failed_categories else "general edge cases"
    tags_str = ", ".join(t.replace("_", " ") for t in mistake_tags) if mistake_tags else "logic oversight"

    prompt_lines = [
        f"Challenge: {challenge_title}",
        f"Description: {challenge_description}",
        "",
        "Learner's Submitted Code:",
        learner_code.strip(),
        "",
        f"Test Results: {passed_count} of {total_count} tests passed.",
        f"Failing Test Categories: {cat_str}",
        f"Identified Pattern: {tags_str}",
    ]

    if visible_summaries:
        prompt_lines.append("")
        prompt_lines.append("Visible Test Outcomes:")
        for v in visible_summaries:
            prompt_lines.append(f"- {v}")

    prompt_lines.extend([
        "",
        "Task: Explain clearly and encouragingly why the code failed without providing any code.",
        "Highlight the category of input where the bug occurs, point out which part of the learner's logic to examine,",
        "and end with a thought-provoking question to help them find the solution on their own.",
        "Keep your response strictly under 100 words in plain English.",
    ])

    return "\n".join(prompt_lines)


HINT_LAYER_NAMES = {
    1: "Nudge",
    2: "Pointer",
    3: "Approach",
}

HINT_LAYER_RULES = {
    1: (
        "Layer 1 ('Nudge'): Ask a thought-provoking guiding question pointing at the core concept. "
        "Do NOT mention or analyze specific lines, variable names, or functions from the learner's code."
    ),
    2: (
        "Layer 2 ('Pointer'): Explain where the logic goes wrong, or describe what kind of input breaks it, in words. "
        "Point to the underlying issue or problematic input case without giving the strategy."
    ),
    3: (
        "Layer 3 ('Approach'): Outline the conceptual strategy or algorithmic approach in plain language. "
        "NO code, not even partial code. ABSOLUTELY NO code blocks, markdown fences, def or return lines."
    ),
}


def build_hint_prompt(
    challenge_description: str,
    learner_code: str,
    authored_hint: str,
    hint_level: int,
    mistake_tags: list[str],
) -> str:
    """Build prompt to personalize an authored progressive hint for the learner's code.

    Enforces exactly 3 pedagogical hint layers:
    - Layer 1 (Nudge): A question that points at the concept, nothing about the learner's code.
    - Layer 2 (Pointer): Where the logic goes wrong, or what kind of input breaks it, in words.
    - Layer 3 (Approach): The strategy in plain language. NO code, not even partial code.
    """
    tags_str = ", ".join(t.replace("_", " ") for t in mistake_tags) if mistake_tags else "general logic"
    layer_name = HINT_LAYER_NAMES.get(hint_level, f"Layer {hint_level}")
    layer_rule = HINT_LAYER_RULES.get(
        hint_level,
        "Provide conceptual guidance in plain English without any code."
    )

    code_context = ""
    if hint_level > 1:
        code_context = f"Learner's Current Code:\n{learner_code.strip()}\n\n"

    prompt = f"""Challenge Goal: {challenge_description}

{code_context}Hint Level: {hint_level} of 3 ({layer_name})
Base Pedagogical Hint: "{authored_hint}"
Detected Bug Habit: {tags_str}

Layer {hint_level} ({layer_name}) Rule:
{layer_rule}

Task: Personalize or rephrase the base hint following the Layer {hint_level} ({layer_name}) rule above.
Rules:
- Strictly adhere to the Layer {hint_level} rule: {'no references to the learner code, only a guiding question' if hint_level == 1 else 'no code or syntax formulas, plain English only'}.
- Do NOT write any code, replacement lines, def/return statements, or code blocks.
- Maximum 60 words in friendly plain English.
"""
    return prompt.strip()


def build_socratic_reply_prompt(
    challenge_title: str,
    challenge_description: str,
    learner_code: str,
    learner_message: str,
    previous_hints: Optional[list[str]] = None,
) -> str:
    """Build prompt for responding to a learner's Socratic dialogue question."""
    hist_str = ""
    if previous_hints:
        hist_str = "\nPrevious Hints Given:\n" + "\n".join(f"- {h}" for h in previous_hints)

    prompt = f"""Challenge: {challenge_title}
Goal: {challenge_description}

Learner's Code:
{learner_code.strip()}
{hist_str}

Learner's Question: "{learner_message}"

Task: Respond to the learner's question with Socratic coaching.
Rules:
- Validate their thinking and clarify any conceptual misunderstanding.
- If they ask for the answer, politely decline and redirect with a guiding question.
- Do NOT provide code or syntax solutions.
- Keep the response short (under 80 words).
"""
    return prompt.strip()


def build_explain_it_back_evaluation_prompt(
    challenge_description: str,
    learner_explanation: str,
    actual_mistake_tags: list[str],
    failing_categories: list[str],
) -> str:
    """Build prompt to evaluate a learner's 'Explain It Back' submission.

    Assesses whether the learner truly understands the root cause of the bug.
    """
    tags_str = ", ".join(t.replace("_", " ") for t in actual_mistake_tags) if actual_mistake_tags else "unhandled edge case"
    cat_str = ", ".join(failing_categories) if failing_categories else "general edge case"

    prompt = f"""Challenge: {challenge_description}
Actual Root Cause: {tags_str}
Input Categories Affected: {cat_str}

Learner's Explanation of Why Their Code Was Buggy:
"{learner_explanation.strip()}"

Task: Evaluate whether the learner correctly understood the root cause of the bug.
Provide:
1. An encouraging assessment of whether their explanation captured the real issue.
2. A brief 1-2 sentence reinforcement of the key concept to remember for the future.
Rules:
- Do not provide code solutions.
- Keep the tone celebratory and educational (under 80 words).
"""
    return prompt.strip()
