"""Deterministic mistake taxonomy and rule-based diagnostic engine.

Analyzes learner submissions using:
1. Graded test outcomes and internal test categories.
2. The sandbox runner's argument mutation detection flag.
3. Learner stdout captured on visible tests.
4. Python's abstract syntax tree (ast module) for static inspection.

SECURITY GUARANTEES:
- Learner code is NEVER executed on the host. AST parsing is purely static.
- Only internal host analysis uses categories; public API responses strip them.
- Maximum 2 tags returned in priority order. One-off slips receive an empty list.
"""

import ast
from typing import Any, Optional
from backend.engine.challenges import FullChallenge

# Priority order of mistake tags
MISTAKE_PRIORITY = [
    "print_not_return",
    "mutates_input",
    "unhandled_short_input",
    "boundary_comparison",
    "off_by_one",
    "wrong_initialization",
    "incorrect_condition",
    "incorrect_accumulation",
    "wrong_return_type",
]


def _safe_parse_ast(code: str) -> Optional[ast.AST]:
    """Parse learner code into an AST safely without throwing syntax errors."""
    try:
        return ast.parse(code)
    except (SyntaxError, ValueError, TypeError):
        return None


def _find_target_func_ast(parsed_ast: Optional[ast.AST], function_name: str) -> Optional[ast.FunctionDef]:
    """Locate the target function definition in the AST."""
    if not parsed_ast:
        return None
    for node in ast.walk(parsed_ast):
        if isinstance(node, ast.FunctionDef) and node.name == function_name:
            return node
    for node in ast.walk(parsed_ast):
        if isinstance(node, ast.FunctionDef):
            return node
    return None


def _is_boundary_category(category: Optional[str]) -> bool:
    """Determine whether a test category belongs to the threshold/boundary group."""
    if not category:
        return False
    cat_lower = category.lower().strip()
    return "boundar" in cat_lower or "below" in cat_lower or "above" in cat_lower


def _is_short_input_category(category: Optional[str], challenge: FullChallenge) -> bool:
    """Determine whether a test category belongs to the short-input boundary group."""
    if not category:
        return False
    # If category explicitly tests a boundary threshold, it is boundary, not short-input
    if _is_boundary_category(category):
        return False

    cat_lower = category.lower().strip()

    # Explicit challenge configuration
    if challenge.short_input_categories:
        if any(cat_lower == s.lower().strip() for s in challenge.short_input_categories):
            return True

    # Common short input naming patterns
    short_keywords = [
        "empty", "single", "zero", "fewer", "short",
        "length_one", "length_two", "n_zero", "n_one",
        "k_larger", "k_zero",
    ]
    return any(kw in cat_lower for kw in short_keywords)


# ==============================================================================
# Rule 1: print_not_return
# ==============================================================================

def check_print_not_return(
    challenge: FullChallenge,
    code: str,
    parsed_ast: Optional[ast.AST],
    graded_result: dict[str, Any],
) -> bool:
    """Detect functions that print output rather than returning it.

    RULE:
    The function returns None on one or more tests where a non-None value was expected
    AND (the code printed something on visible tests OR the AST contains print( in the
    function body with no return statement carrying a value).
    """
    all_tests = graded_result.get("visible", []) + graded_result.get("hidden", [])

    # Check if function returned None when non-None was expected
    none_returned_on_non_none = False
    for t in all_tests:
        exp = t.get("expected_value")
        raw = t.get("raw_value")
        if exp is not None and raw is None and t.get("outcome") != "passed":
            none_returned_on_non_none = True
            break

    if not none_returned_on_non_none:
        return False

    # Check 1: Printed output on visible tests
    visible_tests = graded_result.get("visible", [])
    has_printed_output = any(
        bool(v.get("stdout") and str(v["stdout"]).strip())
        for v in visible_tests
    )
    if has_printed_output:
        return True

    # Check 2: AST contains print() with no meaningful return value
    func_node = _find_target_func_ast(parsed_ast, challenge.function_name)
    if func_node:
        calls = [
            node for node in ast.walk(func_node)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "print"
        ]
        if calls:
            returns = [node for node in ast.walk(func_node) if isinstance(node, ast.Return)]
            has_val_return = any(
                r.value is not None and not (isinstance(r.value, ast.Constant) and r.value.value is None)
                for r in returns
            )
            if not has_val_return:
                return True

    return False


# ==============================================================================
# Rule 2: mutates_input
# ==============================================================================

def check_mutates_input(
    challenge: FullChallenge,
    code: str,
    parsed_ast: Optional[ast.AST],
    graded_result: dict[str, Any],
) -> bool:
    """Detect functions that mutate caller arguments in-place.

    RULE:
    The runner reports the args were mutated on any test (visible or hidden) where
    the function otherwise completed without timing out or crashing beforehand.
    """
    all_tests = graded_result.get("visible", []) + graded_result.get("hidden", [])

    # Check if any completed test had mutated == True
    for t in all_tests:
        if t.get("mutated") and t.get("outcome") in ("passed", "wrong_output"):
            return True

    if graded_result.get("any_mutated"):
        # Ensure at least one test ran without crashing
        if any(t.get("outcome") in ("passed", "wrong_output") for t in all_tests):
            return True

    return False


# ==============================================================================
# Rule 3: unhandled_short_input
# ==============================================================================

def check_unhandled_short_input(
    challenge: FullChallenge,
    code: str,
    parsed_ast: Optional[ast.AST],
    graded_result: dict[str, Any],
) -> bool:
    """Detect functions that work on normal inputs but fail on empty or short inputs.

    RULE:
    Failing hidden or visible tests whose category is in the short-input group
    (empty_list, single_element, k_larger_than_list, fewer_than_three, n_zero, etc.),
    while normal-category tests pass. Runtime errors (e.g. IndexError) on short-input
    categories also count.
    """
    all_tests = graded_result.get("visible", []) + graded_result.get("hidden", [])

    short_tests = []
    normal_tests = []

    for t in all_tests:
        cat = t.get("category") or ""
        if _is_short_input_category(cat, challenge):
            short_tests.append(t)
        elif not _is_boundary_category(cat):
            normal_tests.append(t)

    if not short_tests:
        return False

    # At least one short-input test failed
    short_failed = any(t["outcome"] in ("wrong_output", "runtime_error", "timeout") for t in short_tests)
    if not short_failed:
        return False

    # Normal tests must exist and pass (if normal tests also fail, it's a general logic error)
    if not normal_tests:
        return False

    normal_all_passed = all(t["outcome"] == "passed" for t in normal_tests)
    return normal_all_passed


# ==============================================================================
# Rule 4: boundary_comparison
# ==============================================================================

def _contains_boundary_value(args: Any, boundary_values: list[Any]) -> bool:
    """Check recursively if args contain any of the boundary values."""
    if not boundary_values or args is None:
        return False
    if isinstance(args, (list, tuple)):
        return any(_contains_boundary_value(item, boundary_values) for item in args)
    if isinstance(args, dict):
        return any(_contains_boundary_value(v, boundary_values) for v in args.values())
    if isinstance(args, (int, float)) and not isinstance(args, bool):
        return args in boundary_values
    return False


def check_boundary_comparison(
    challenge: FullChallenge,
    code: str,
    parsed_ast: Optional[ast.AST],
    graded_result: dict[str, Any],
) -> bool:
    """Detect strict inequality mistakes on threshold or boundary values.

    RULE:
    A test whose category is boundary_value fails while normal tests pass,
    AND the AST contains a strict comparison (Lt or Gt) against one of the
    challenge's boundary_values (or a boundary-adjacent literal such as 39 or 41
    when the boundary is 40).
    """
    if not challenge.boundary_values:
        return False

    all_tests = graded_result.get("visible", []) + graded_result.get("hidden", [])
    boundary_tests = [
        t for t in all_tests
        if _is_boundary_category(t.get("category"))
        or _contains_boundary_value(t.get("args"), challenge.boundary_values)
    ]
    normal_tests = [
        t for t in all_tests
        if not _is_boundary_category(t.get("category"))
        and not _contains_boundary_value(t.get("args"), challenge.boundary_values)
        and not _is_short_input_category(t.get("category"), challenge)
    ]

    if not boundary_tests:
        return False

    boundary_failed = any(t["outcome"] != "passed" for t in boundary_tests)
    if not boundary_failed:
        return False

    normal_passed = len(normal_tests) > 0 and all(t["outcome"] == "passed" for t in normal_tests)
    if not normal_passed:
        return False

    # AST check: look for strict comparison against boundary or boundary-adjacent literal
    if not parsed_ast:
        return False

    target_boundaries: set[float | int] = set()
    for bv in challenge.boundary_values:
        if isinstance(bv, (int, float)):
            target_boundaries.add(bv)
            target_boundaries.add(bv - 1)
            target_boundaries.add(bv + 1)

    func_node = _find_target_func_ast(parsed_ast, challenge.function_name)
    search_root = func_node if func_node else parsed_ast

    for node in ast.walk(search_root):
        if isinstance(node, ast.Compare):
            # Strict comparisons: Lt or Gt
            has_strict_op = any(isinstance(op, (ast.Lt, ast.Gt)) for op in node.ops)
            # Boundary literals in left or comparators
            operands = [node.left] + node.comparators
            has_boundary_literal = False
            for opnd in operands:
                if isinstance(opnd, ast.Constant) and isinstance(opnd.value, (int, float)):
                    if opnd.value in target_boundaries:
                        has_boundary_literal = True
                        break

            if has_strict_op and has_boundary_literal:
                return True

    return False


# ==============================================================================
# Rule 5: off_by_one
# ==============================================================================

def check_off_by_one(
    challenge: FullChallenge,
    code: str,
    parsed_ast: Optional[ast.AST],
    graded_result: dict[str, Any],
    matched_so_far: list[str],
) -> bool:
    """Detect off-by-one errors in iteration counts, range bounds, or element totals.

    RULE:
    Wrong_output where the result differs from expected by exactly one element
    (list length differs by 1, or numeric sum/count differs by one unit),
    OR the AST contains range(...) bounds that include a literal +1 or -1 adjustment,
    and the failure is NOT already explained by rules 1 to 4. Be conservative.
    """
    # Guard: Do not fire if already explained by rules 1 through 4
    higher_priority_tags = {"print_not_return", "mutates_input", "unhandled_short_input", "boundary_comparison"}
    if any(t in higher_priority_tags for t in matched_so_far):
        return False

    all_tests = graded_result.get("visible", []) + graded_result.get("hidden", [])
    wrong_output_tests = [t for t in all_tests if t.get("outcome") == "wrong_output"]

    # Condition A: Result differs from expected by exactly 1 unit or 1 element
    if wrong_output_tests:
        deltas_are_one = []
        for t in wrong_output_tests:
            raw = t.get("raw_value")
            exp = t.get("expected_value")

            if isinstance(raw, (int, float)) and isinstance(exp, (int, float)) and not isinstance(raw, bool) and not isinstance(exp, bool):
                deltas_are_one.append(abs(raw - exp) == 1)
            elif isinstance(raw, list) and isinstance(exp, list):
                deltas_are_one.append(abs(len(raw) - len(exp)) == 1)
            else:
                deltas_are_one.append(False)

        if deltas_are_one and all(deltas_are_one):
            return True

    # Condition B: AST contains range(...) with literal +1 or -1 adjustment
    if parsed_ast and (wrong_output_tests or any(t.get("outcome") == "runtime_error" for t in all_tests)):
        func_node = _find_target_func_ast(parsed_ast, challenge.function_name)
        search_root = func_node if func_node else parsed_ast

        for node in ast.walk(search_root):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "range":
                for arg in node.args:
                    if isinstance(arg, ast.BinOp) and isinstance(arg.op, (ast.Add, ast.Sub)):
                        if isinstance(arg.right, ast.Constant) and arg.right.value == 1:
                            return True

    return False


# ==============================================================================
# Additional Rules (Lower Priority)
# ==============================================================================

def check_wrong_initialization(
    challenge: FullChallenge,
    code: str,
    parsed_ast: Optional[ast.AST],
    graded_result: dict[str, Any],
) -> bool:
    """Detect accumulator or result initialized to an incorrect starting value.

    RULE:
    Accumulator initialised to a non-neutral literal, for example total = 1 for a sum,
    OR failing test results differ from expected by a non-zero constant offset.
    """
    if not parsed_ast:
        return False

    func_node = _find_target_func_ast(parsed_ast, challenge.function_name)
    search_root = func_node if func_node else parsed_ast

    # Check for suspicious non-neutral literal assignments to accumulator names
    acc_names = {"total", "count", "acc", "sum_val", "result", "res"}
    for node in ast.walk(search_root):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id.lower() in acc_names:
                    # e.g. total = 10, total = 1 (when neutral would be 0)
                    if isinstance(node.value, ast.Constant) and isinstance(node.value.value, (int, float)):
                        if node.value.value not in (0, 0.0):
                            return True

    # Execution check: all numeric wrong_outputs differ by the same constant != 1
    wrong_tests = [t for t in graded_result.get("visible", []) + graded_result.get("hidden", []) if t.get("outcome") == "wrong_output"]
    if len(wrong_tests) >= 2:
        diffs = []
        for t in wrong_tests:
            raw = t.get("raw_value")
            exp = t.get("expected_value")
            if isinstance(raw, (int, float)) and isinstance(exp, (int, float)) and not isinstance(raw, bool):
                diffs.append(raw - exp)
        if len(diffs) >= 2 and len(set(diffs)) == 1 and diffs[0] != 0 and abs(diffs[0]) > 1:
            return True

    return False


def check_incorrect_condition(
    challenge: FullChallenge,
    code: str,
    parsed_ast: Optional[ast.AST],
    graded_result: dict[str, Any],
) -> bool:
    """Detect inverted or wrong condition constants outside boundary thresholds.

    RULE:
    Condition constants differ from expected behaviour (e.g. x % 2 == 1 for even numbers)
    and does not match a boundary value comparison.
    """
    if not parsed_ast:
        return False

    func_node = _find_target_func_ast(parsed_ast, challenge.function_name)
    search_root = func_node if func_node else parsed_ast

    # Check for modulo condition inversions, e.g. % 2 == 1 when checking even
    for node in ast.walk(search_root):
        if isinstance(node, ast.Compare):
            if isinstance(node.left, ast.BinOp) and isinstance(node.left.op, ast.Mod):
                if isinstance(node.left.right, ast.Constant) and node.left.right.value == 2:
                    if len(node.ops) == 1 and isinstance(node.ops[0], ast.Eq):
                        if len(node.comparators) == 1 and isinstance(node.comparators[0], ast.Constant):
                            # Even checking with % 2 == 1
                            if "even" in challenge.id and node.comparators[0].value == 1:
                                return True

    return False


def check_incorrect_accumulation(
    challenge: FullChallenge,
    code: str,
    parsed_ast: Optional[ast.AST],
    graded_result: dict[str, Any],
) -> bool:
    """Detect incorrect accumulation logic such as counting instead of summing."""
    if not parsed_ast:
        return False

    func_node = _find_target_func_ast(parsed_ast, challenge.function_name)
    search_root = func_node if func_node else parsed_ast

    for node in ast.walk(search_root):
        if isinstance(node, ast.For):
            for body_item in ast.walk(node):
                if isinstance(body_item, ast.AugAssign) and isinstance(body_item.op, ast.Add):
                    # Adding constant 1 when summing list elements
                    if "total" in challenge.id or "sum" in challenge.id:
                        if isinstance(body_item.value, ast.Constant) and body_item.value.value == 1:
                            return True

    return False


def check_wrong_return_type(
    challenge: FullChallenge,
    code: str,
    parsed_ast: Optional[ast.AST],
    graded_result: dict[str, Any],
) -> bool:
    """Detect return values whose data type consistently differs from expected.

    RULE:
    Returned type differs from expected type on every failing test where the function returned a value.
    """
    all_tests = graded_result.get("visible", []) + graded_result.get("hidden", [])
    wrong_tests = [t for t in all_tests if t.get("outcome") == "wrong_output"]

    if not wrong_tests:
        return False

    type_mismatches = []
    for t in wrong_tests:
        raw = t.get("raw_value")
        exp = t.get("expected_value")
        if raw is not None and exp is not None:
            type_mismatches.append(type(raw) is not type(exp))

    return bool(type_mismatches and all(type_mismatches))


# ==============================================================================
# Main Orchestrator: detect_mistakes
# ==============================================================================

def detect_mistakes(
    challenge: FullChallenge,
    code: str,
    graded_result: dict[str, Any],
) -> list[str]:
    """Detect mistake taxonomy tags for an unpassed submission.

    Args:
        challenge: The FullChallenge specification.
        code: The raw learner source code string.
        graded_result: Result dict from grader.grade_submission (containing visible,
                       hidden, and internal analysis attributes).

    Returns:
        List of at most 2 mistake tags in strict priority order.
        Returns [] if all tests passed or if the bug is an unclassified one-off slip.
    """
    # Runs ONLY when submission is not fully passed
    if graded_result.get("all_passed"):
        return []

    parsed_ast = _safe_parse_ast(code)
    matched: list[str] = []

    # 1. print_not_return
    if check_print_not_return(challenge, code, parsed_ast, graded_result):
        matched.append("print_not_return")

    # 2. mutates_input
    if check_mutates_input(challenge, code, parsed_ast, graded_result):
        matched.append("mutates_input")

    # 3. unhandled_short_input
    if check_unhandled_short_input(challenge, code, parsed_ast, graded_result):
        matched.append("unhandled_short_input")

    # 4. boundary_comparison
    if check_boundary_comparison(challenge, code, parsed_ast, graded_result):
        matched.append("boundary_comparison")

    # 5. off_by_one
    if check_off_by_one(challenge, code, parsed_ast, graded_result, matched):
        matched.append("off_by_one")

    # Lower priority additional tags
    if len(matched) < 2 and check_wrong_initialization(challenge, code, parsed_ast, graded_result):
        matched.append("wrong_initialization")

    if len(matched) < 2 and check_incorrect_condition(challenge, code, parsed_ast, graded_result):
        matched.append("incorrect_condition")

    if len(matched) < 2 and check_incorrect_accumulation(challenge, code, parsed_ast, graded_result):
        matched.append("incorrect_accumulation")

    if len(matched) < 2 and check_wrong_return_type(challenge, code, parsed_ast, graded_result):
        matched.append("wrong_return_type")

    # Return at most 2 tags in priority order
    return matched[:2]
