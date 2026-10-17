# Debug Dojo Mistake Taxonomy (One-Page Reference)

Deterministic diagnostic engine implemented in `backend/engine/mistakes.py`. Evaluates on the host **only when tests fail**. Returns at most **two tags** in strict priority order. If unclassified, returns `[]`.

---

## Priority Order & Diagnostic Detection Rules

| # | Mistake Tag | Type | Beginner Meaning | Deterministic Detection Rule |
|---|---|---|---|---|
| **1** | `print_not_return` | Mandatory | Printed result instead of returning it | Returns `None` where non-`None` expected **AND** (`stdout` captured on visible tests **OR** AST confirms `print()` without value-return). |
| **2** | `mutates_input` | Mandatory | Modified input arguments in-place | Docker runner reports `mutated == True` via deep comparison on any completed test. |
| **3** | `unhandled_short_input` | Mandatory | Fails on empty/short/zero inputs | Fails short-input tests (`empty_list`, `single_element`, `k_larger_than_list`, `n_zero`) **AND** passes all normal/standard tests. |
| **4** | `boundary_comparison` | Mandatory | Strict inequality error on threshold | Fails boundary test cases **AND** passes non-boundary tests **AND** AST confirms strict comparison (`<`, `>`) against boundary literal. |
| **5** | `off_by_one` | Mandatory | Result or length is off by exactly 1 | Not explained by #1–#4 **AND** (`\|got - expected\| == 1` or `\|len(got) - len(expected)\| == 1` or AST `range()` boundary `+ 1`/`- 1`). |
| **6** | `wrong_initialization` | Secondary | Accumulator starts at wrong value | AST identifies non-zero/non-neutral accumulator initialization (`total = 1`) or uniform constant offset on all failures. |
| **7** | `incorrect_condition` | Secondary | Conditional check inverted | AST identifies inverted condition operator (`% 2 == 1` for evens) on non-boundary logic. |
| **8** | `incorrect_accumulation` | Secondary | Loop accumulator applies wrong step | AST detects constant increment (`total += 1` instead of `total += item`) or overwriting accumulator (`acc = item`). |
| **9** | `wrong_return_type` | Secondary | Returned data type is incorrect | Function returns wrong type on all failing tests (`type(got) != type(expected)`). |

---

## Architectural & Anti-Leak Invariants

1. **Host-Side Static Analysis**: Code is **never** executed on the host. Analysis uses Python's static `ast.parse()`.
2. **Category Isolation**: Internal categories (`empty_list`, `boundary_40`) guide host diagnosis but are stripped via `strip_for_api()`.
3. **No Solution Disclosure**: Mistake tags never leak solution code; hints provide conceptual steps, not code snippets.
