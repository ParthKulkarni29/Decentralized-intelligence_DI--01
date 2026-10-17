"""Output safety guardrails for AI-generated hints and explanations.

Enforces strict pedagogical rules:
- No complete or partial code solutions.
- No markdown code fences or raw code lines (def, return, for, if, import, assignments).
- No compound code expressions inline (e.g. `range(max(0, len(x)-k), len(x))`, `marks[-k:]`).
- No prohibited giveaway phrases ("here is the corrected", "the solution is").
- No word floods (max 120 words for hints).
- No leakage of hidden test inputs or expected values.

When any check fails, the guardrail rejects the text and the caller reverts
to deterministic authored fallbacks.
"""

import ast
import json
import re
from typing import Any, Optional

MAX_HINT_WORDS = 120

PROHIBITED_PHRASES = [
    "here is the corrected",
    "here's the corrected",
    "the solution is",
    "the answer is",
    "here is the fix",
    "here's the fix",
    "here is the code",
    "here's the code",
    "try this code",
    "use this code",
    "copy and paste",
    "replace your code with",
    "corrected code",
    "fixed version",
    "solution code",
]

# Patterns for lines that represent raw code
CODE_LINE_PATTERNS = [
    (re.compile(r"^\s*def\s+\w+\s*\(.*"), "function definition ('def')"),
    (re.compile(r"^\s*(?:import\s+\w+|from\s+\w+\s+import\s+.*)"), "import statement"),
    (re.compile(r"^\s*for\s+\w+.*in.*:\s*$"), "for-loop statement"),
    (re.compile(r"^\s*while\s+.*:\s*$"), "while-loop statement"),
    (re.compile(r"^\s*if\s+.*:\s*$"), "if-statement"),
    (re.compile(r"^\s*elif\s+.*:\s*$"), "elif-statement"),
    (re.compile(r"^\s*else\s*:\s*$"), "else-statement"),
    (re.compile(r"^\s*return(?:\s+.*)?$"), "return statement"),
    (re.compile(r"^\s*[a-zA-Z_]\w*\s*(?:=|\+=|-=|\*=|//=|/)\s*[^=].*"), "assignment statement"),
]


def _is_compound_code_expression(expr_str: str) -> bool:
    """Determine if a backticked fragment is an executable compound code solution.

    Allowed:
    - Single identifier: `marks`, `k`, `items`, `n`
    - Simple function reference: `len`, `sum`, `range`, `len()`, `print()`
    - Plain words or types: `int`, `list`, `empty_list`

    Disallowed:
    - Slicing: `marks[-k:]`, `items[0]`
    - Arithmetic / logic: `n * n`, `x + 1`, `mark >= 40`
    - Comprehensions: `[i * i for i in range(...)]`
    - Complex nested calls: `range(max(0, len(x)-k), len(x))`
    - Return statements: `return 0`, `return sum(marks)`
    """
    cleaned = expr_str.strip()
    if not cleaned:
        return False

    # Allowed bare references
    if re.fullmatch(r"[a-zA-Z_]\w*", cleaned):
        return False
    if re.fullmatch(r"[a-zA-Z_]\w*\(\)", cleaned):
        return False

    # Attempt to parse as Python code
    try:
        parsed = ast.parse(cleaned)
    except SyntaxError:
        # Not valid Python syntax (e.g. prose with backticks like `fewer than 3`)
        return False

    # If it contains any statement constructs
    if not parsed.body:
        return False

    first_node = parsed.body[0]
    if isinstance(first_node, (ast.Return, ast.Assign, ast.AugAssign, ast.For, ast.While, ast.If, ast.FunctionDef, ast.Import, ast.ImportFrom)):
        return True

    if isinstance(first_node, ast.Expr):
        val = first_node.value
        # Check for comprehensions
        if isinstance(val, (ast.ListComp, ast.DictComp, ast.SetComp, ast.GeneratorExp)):
            return True
        # Check for slicing / indexing
        if isinstance(val, ast.Subscript):
            return True
        # Check for binary operators or comparisons
        if isinstance(val, (ast.BinOp, ast.Compare, ast.UnaryOp, ast.BoolOp)):
            return True
        # Check for complex calls (calls with multiple args or nested calls)
        if isinstance(val, ast.Call):
            if len(val.args) > 1:
                return True
            for subnode in ast.walk(val):
                if subnode is not val and isinstance(subnode, (ast.Call, ast.Subscript, ast.BinOp, ast.Compare, ast.ListComp)):
                    return True

    return False


def check_text(
    text: str,
    hidden_tests: Optional[list[dict[str, Any]]] = None,
    is_hint: bool = True,
) -> tuple[bool, Optional[str]]:
    """Verify that generated text conforms to all safety and anti-leak guardrails.

    Returns:
        (True, None) if text is safe.
        (False, reason_str) if text violates any safety rule.
    """
    if not text or not text.strip():
        return False, "Generated text is empty"

    # 1. Word count check (max 120 words for hints)
    words = text.split()
    if is_hint and len(words) > MAX_HINT_WORDS:
        return False, f"Exceeds maximum hint word limit ({len(words)} > {MAX_HINT_WORDS} words)"

    # 2. Markdown code fences
    if "```" in text:
        return False, "Contains markdown code fence"

    # 3. Prohibited giveaway phrases
    lower_text = text.lower()
    for phrase in PROHIBITED_PHRASES:
        if phrase in lower_text:
            return False, f"Contains prohibited giveaway phrase: '{phrase}'"

    # 4. Line-by-line checks for code statements or indented code blocks
    lines = text.splitlines()
    for line_idx, line in enumerate(lines, 1):
        stripped = line.strip()
        if not stripped:
            continue

        # Check for indented code blocks (4+ spaces or a tab at line start followed by code-like syntax)
        leading_spaces = len(line) - len(line.lstrip(" "))
        has_tab = line.startswith("\t")
        if (leading_spaces >= 4 or has_tab) and any(op in stripped for op in ["=", "(", "[", ":", "+", "-"]):
            # Check if this indented line parses as Python statement
            try:
                parsed_line = ast.parse(stripped)
                if parsed_line.body and isinstance(parsed_line.body[0], (ast.Assign, ast.AugAssign, ast.Return, ast.Expr, ast.If, ast.For)):
                    # Distinguish from indented bullet list item
                    if not re.match(r"^[-*•\d.]\s+", stripped):
                        return False, f"Line {line_idx} contains indented code"
            except SyntaxError:
                pass

        # Check explicit code line patterns
        for pattern, desc in CODE_LINE_PATTERNS:
            if pattern.match(stripped):
                return False, f"Line {line_idx} contains code: {desc}"

        # Check for inline 'return' followed by a code expression within prose
        # E.g. "You should write return sum(marks)" or "try return 0"
        return_expr_match = re.search(r"\breturn\s+(\[[^\]]*\]|\{[^\}]*\}|\d+|[a-zA-Z_]\w*\s*[\(\[\+\-\*\/])", stripped)
        if return_expr_match:
            return False, f"Line {line_idx} contains return expression: '{return_expr_match.group(0)}'"

    # 5. Check inline code expressions inside backticks
    backticked_snippets = re.findall(r"`([^`]+)`", text)
    for snippet in backticked_snippets:
        if _is_compound_code_expression(snippet):
            return False, f"Contains inline code solution expression: `{snippet}`"

    # 6. Check for leaked hidden test data
    if hidden_tests:
        for t in hidden_tests:
            # Check expected output leakage
            exp = t.get("expected")
            if exp is not None:
                if isinstance(exp, (list, dict)):
                    json_exp = json.dumps(exp)
                    compact_json = json.dumps(exp, separators=(",", ":"))
                    repr_exp = repr(exp)
                    if json_exp in text or compact_json in text or repr_exp in text:
                        return False, "Leaked hidden test expected output"
                elif isinstance(exp, int) and exp > 9:
                    # Multi-digit integers (e.g. 70, 100)
                    if str(exp) in text:
                        # Ensure it's not a generic word count or percentage
                        pattern = rf"\b{exp}\b"
                        if re.search(pattern, text):
                            # Check if preceded by words like expected/output/returns/result
                            context_match = re.search(rf"(?:expected|output|returns|result|equals?)\s*(?:is|to|of)?\s*{exp}\b", lower_text)
                            if context_match:
                                return False, f"Leaked hidden test expected value: {exp}"

            # Check input args leakage
            args = t.get("args")
            if args:
                for arg in args:
                    if isinstance(arg, (list, dict)) and len(arg) > 0:
                        json_arg = json.dumps(arg)
                        compact_arg = json.dumps(arg, separators=(",", ":"))
                        repr_arg = repr(arg)
                        if json_arg in text or compact_arg in text or repr_arg in text:
                            return False, "Leaked hidden test input data"

    return True, None


def verify_coach_reply(
    reply: str,
    hidden_tests: Optional[list[dict[str, Any]]] = None,
) -> tuple[bool, Optional[str]]:
    """Verify coach chat response contains no code fences, solution statements, or hidden test leaks."""
    return check_text(reply, hidden_tests=hidden_tests, is_hint=False)

