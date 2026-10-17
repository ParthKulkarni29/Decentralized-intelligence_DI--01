# Debug Dojo — Mastery, Mistakes, and Recommender Engine

This document specifies the mathematics, rules, and worked examples for concept mastery tracking, mistake analysis, Bug DNA, and personalized challenge recommendations in Debug Dojo.

---

## 1. Concept Mastery Model

Debug Dojo tracks learner proficiency across 6 core Python concepts:
- `loops`
- `lists`
- `indexing`
- `conditions`
- `edge_cases`
- `functions`

Every concept score is a continuous value bounded in $[0.0, 1.0]$. All learners begin at a baseline score of **0.5000** for each concept.

### 1.1 Exponential Moving Average (EMA) Formula

Whenever a learner submits code for a challenge, the mastery score for every concept tagged on that challenge is updated using an Exponential Moving Average (EMA):

$$\text{new} = \text{old} + \text{weight} \times (\text{target} - \text{old})$$

Where:
- $\text{old}$: Current mastery score for the concept in $[0.0, 1.0]$.
- $\text{target}$: $1.0$ if the submission passes all tests, or $0.0$ if any test fails.
- $\text{weight}$: Learning rate factor determined by hints used and attempt history.

### 1.2 Target and Weight Rules

1. **Pass with Zero Hints Used**:
   - $\text{target} = 1.0$
   - $\text{weight} = 0.30$
   - Formula: $\text{new} = \text{old} + 0.30 \times (1.0 - \text{old})$

2. **Pass after Hints Used**:
   - $\text{target} = 1.0$
   - $\text{weight} = 0.30 \times \max(0.25, 1.0 - 0.25 \times \text{hints\_used})$
   - Weight values:
     - 1 hint: $0.30 \times 0.75 = 0.2250$
     - 2 hints: $0.30 \times 0.50 = 0.1500$
     - 3 hints: $0.30 \times 0.25 = 0.0750$
     - 4+ hints: $0.30 \times 0.25 = 0.0750$ (minimum floor multiplier)

3. **Failed Submission (Morale Cap)**:
   - $\text{target} = 0.0$
   - If prior failures on this challenge for this learner $< 3$:
     - $\text{weight} = 0.10$
     - Formula: $\text{new} = \text{old} + 0.10 \times (0.0 - \text{old}) = 0.90 \times \text{old}$
   - If prior failures on this challenge $\ge 3$:
     - $\text{weight} = 0.00$
     - **Morale Protection**: Repeated failures on a single stubborn problem will not endlessly drag down a learner's score. Mastery stops decaying after the 3rd failure on that challenge.

4. **Cross-Concept Edge Cases Update**:
   - If a failed submission triggers `unhandled_short_input` or `boundary_comparison`, but the challenge does *not* list `edge_cases` in its primary concepts, an additional failure update ($\text{target}=0.0, \text{weight}=0.10$) is applied directly to `edge_cases`.

---

## 2. Worked Examples

### Example 1: Repeated Failures Followed by Hint-Assisted Solve

Consider a learner starting with initial mastery $0.5000$ in `lists` and `loops` on a challenge:

| Step | Action | Outcome | Hints | Prior Fails | Weight | Calculation | New Score |
|:---|:---|:---|:---:|:---:|:---:|:---|:---:|
| 0 | Initial Baseline | — | — | — | — | Baseline | **0.5000** |
| 1 | Attempt 1 | Fail | 0 | 0 | 0.10 | $0.5000 + 0.10 \times (0 - 0.5000)$ | **0.4500** |
| 2 | Attempt 2 | Fail | 0 | 1 | 0.10 | $0.4500 + 0.10 \times (0 - 0.4500)$ | **0.4050** |
| 3 | Attempt 3 | Fail | 0 | 2 | 0.10 | $0.4050 + 0.10 \times (0 - 0.4050)$ | **0.3645** |
| 4 | Attempt 4 | Fail | 0 | 3 | 0.00 | Morale cap: weight is 0.00 | **0.3645** |
| 5 | Attempt 5 | Fail | 0 | 4 | 0.00 | Morale cap: weight is 0.00 | **0.3645** |
| 6 | Attempt 6 | Pass | 1 | 5 | 0.225 | $0.3645 + 0.225 \times (1 - 0.3645) = 0.3645 + 0.1430$ | **0.5075** |

### Example 2: Clean Solve with Zero Hints vs 3 Hints

Starting from baseline $0.5000$:

- **Clean Solve (0 hints)**:
  $$\text{new} = 0.5000 + 0.30 \times (1.0 - 0.5000) = 0.5000 + 0.1500 = \mathbf{0.6500}$$
- **Assisted Solve (3 hints)**:
  $$\text{weight} = 0.30 \times 0.25 = 0.0750$$
  $$\text{new} = 0.5000 + 0.0750 \times (1.0 - 0.5000) = 0.5000 + 0.0375 = \mathbf{0.5375}$$

The clean solve awards four times the mastery gain ($+0.1500$ vs $+0.0375$) compared to solving with heavy hint reliance.

---

## 3. Recurring Mistakes and Bug DNA

### 3.1 Taxonomy Descriptions
Every mistake tag is documented in plain, encouraging language for beginners:

| Tag | Plain-Language Description |
|:---|:---|
| `print_not_return` | Using `print()` inside the function instead of returning a value back to the caller. |
| `mutates_input` | Modifying input lists or arguments in-place, which unexpectedly changes the original data. |
| `unhandled_short_input` | Your logic works on standard inputs, but crashes or misbehaves on empty, single-element, or zero inputs. |
| `boundary_comparison` | Using a strict inequality (`<` or `>`) instead of an inclusive one (`<=` or `>=`) on a threshold cutoff value. |
| `off_by_one` | The result differs from expected by exactly one element or unit, often due to loop bounds or slicing indices. |
| `wrong_initialization` | Starting an accumulator or result variable with an incorrect initial value instead of a neutral baseline. |
| `incorrect_condition` | Checking the wrong condition or inverting boolean logic in an `if` statement. |
| `incorrect_accumulation` | Updating a running total incorrectly, such as incrementing a step count instead of adding element values. |
| `wrong_return_type` | Returning a data type that does not match the expected return type (e.g. list vs int). |

### 3.2 Recurring Mistake Qualification
A mistake tag qualifies as **recurring** if and only if:
- It appears in failed submissions across **at least 2 different challenges** for that learner.
- Repeated occurrences within the *same* challenge do **not** trigger recurrence.

### 3.3 Bug DNA
`bug_dna` aggregates all mistake tags ever recorded for the learner in the `mistakes` table, grouped by tag and ordered descending by frequency.

---

## 4. Recommender Engine

The recommendation algorithm `recommend_next_challenge(learner_id)` deterministically selects the optimal next challenge using four prioritized strategies:

1. **Recurring Mistake Remediation (Priority 1)**:
   - Identifies the learner's top recurring mistake tag (by distinct challenge count).
   - Finds challenges targeting that mistake (`targets_mistakes`).
   - Prioritizes unsolved challenges over solved, and easiest difficulties first (`easy` $\to$ `medium` $\to$ `hard`).
   - Explanation template:
     `"You made the same mistake (<tag in plain words>) in <challenge1> and <challenge2>. This challenge gives you another chance to practise it."`

2. **Concept Reinforcement (Priority 2)**:
   - If no recurring mistakes exist, identifies the concept with the lowest mastery score among concepts represented in remaining unsolved challenges.
   - Picks the easiest unsolved challenge featuring that concept.
   - Explanation template:
     `"Recommended to help you strengthen your <concept> skills (current mastery: <X>%)."`

3. **Curriculum Progression (Priority 3)**:
   - If all concept scores are equal or no specific deficit is identified, selects the next unsolved challenge by `sort_order`.
   - Explanation template:
     `"Next challenge to practice your debugging skills."`

4. **Master Repetition (Priority 4)**:
   - If the learner has solved all available challenges, selects the hardest challenge in the catalogue.
   - Explanation template:
     `"You've solved all available challenges! Try repeating <Title> without any hints to master it."`

---

## 5. Habit Breaking Achievements

When a learner demonstrates mastery over a persistent bug pattern:
- **Condition**: A previously recurring mistake tag does *not* appear on a submission of a challenge that targets that mistake, and the submission passes all tests with **zero hints**.
- **Reward**: An achievement with key `"Bug Habit Broken: <Tag>"` is recorded in the `achievements` table and displayed on the learner's profile.
