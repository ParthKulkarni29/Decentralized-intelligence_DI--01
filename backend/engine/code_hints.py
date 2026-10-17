"""Code-aware progressive hints for learner-typed solutions.

WHY THIS EXISTS: the authored hint ladders in hints.json are keyed by the mistake tags we
recognise for each challenge. A learner who types their own, different attempt (unfinished code,
a syntax slip, a missing return, ...) matched no tag and only ever saw one generic ladder.
This module inspects the learner's actual code and produces a three-layer ladder about THEIR
attempt: Nudge (a guiding question), Pointer (where to look), Approach (the strategy in words).

SECURITY / PEDAGOGY GUARANTEES:
- Learner code is only parsed with ``ast``; it is NEVER executed here.
- Only the learner's own code, the public challenge description and edge-case CATEGORY words
  are used. Hidden test inputs, expected outputs and exception text are never read.
- Hints are plain English. They never contain code or a replacement solution.
"""

import ast
import builtins
import difflib
from dataclasses import dataclass, field
from typing import Any, Optional

from backend.engine.challenges import FullChallenge

_MUTATING_METHODS = {"append", "extend", "insert", "pop", "remove", "sort", "reverse", "clear", "update", "add", "discard"}


@dataclass
class HintFinding:
    """One observation about the learner's code, with a 3-layer hint ladder."""

    kind: str
    ladder: list[str] = field(default_factory=list)


def _ladder(nudge: str, pointer: str, approach: str) -> list[str]:
    return [nudge, pointer, approach]


def _walk_no_nested_defs(func: ast.FunctionDef):
    """Walk a function body without descending into nested function/class definitions."""
    stack = list(func.body)
    while stack:
        node = stack.pop()
        yield node
        for child in ast.iter_child_nodes(node):
            if not isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
                stack.append(child)


def _is_placeholder_body(func: ast.FunctionDef) -> bool:
    """True when the body does nothing real: only pass, ..., docstrings or bare constants."""
    for stmt in func.body:
        if isinstance(stmt, ast.Pass):
            continue
        if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Constant):
            continue
        return False
    return True


def _find_function(tree: ast.AST, name: str) -> Optional[ast.FunctionDef]:
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    return None


def _names_used(func: ast.FunctionDef) -> set[str]:
    return {n.id for n in _walk_no_nested_defs(func) if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)}


def _param_names(func: ast.FunctionDef) -> list[str]:
    a = func.args
    return [p.arg for p in (a.posonlyargs + a.args + a.kwonlyargs)]


_SHADOW_PRONE = {
    "sum", "max", "min", "len", "list", "dict", "set", "str", "int", "float", "sorted", "input", "print", "type", "id",
    "all", "any", "map", "filter", "range", "abs", "round", "zip", "next", "iter", "format", "hash", "object", "open",
    "bool", "tuple", "reversed", "enumerate",
}
_EXTRA_MODULE_NAMES = {"__file__", "__doc__", "__spec__", "__loader__", "__package__", "__builtins__"}


def _bound_names(tree: ast.AST) -> tuple[set[str], bool]:
    """Every name the file creates anywhere (flat, scope-agnostic on purpose: avoids false alarms).

    Returns (names, has_star_import). With a star import we cannot know what exists, so callers skip checks.
    """
    names: set[str] = set()
    star = False
    for n in ast.walk(tree):
        if isinstance(n, ast.Name) and isinstance(n.ctx, (ast.Store, ast.Del)):
            names.add(n.id)
        elif isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(n.name)
        elif isinstance(n, ast.arg):
            names.add(n.arg)
        elif isinstance(n, (ast.Import, ast.ImportFrom)):
            for a in n.names:
                if a.name == "*":
                    star = True
                else:
                    names.add((a.asname or a.name).split(".")[0])
        elif isinstance(n, ast.ExceptHandler) and n.name:
            names.add(n.name)
        elif isinstance(n, (ast.Global, ast.Nonlocal)):
            names.update(n.names)
        elif n.__class__.__name__ in ("MatchAs", "MatchStar") and getattr(n, "name", None):
            names.add(n.name)
        elif n.__class__.__name__ == "MatchMapping" and getattr(n, "rest", None):
            names.add(n.rest)
    return names, star


def _general_findings(tree: ast.AST) -> list[HintFinding]:
    """Problems in ANY Python code, whatever the challenge: names used but never created, hidden built-ins."""
    out: list[HintFinding] = []
    bound, star = _bound_names(tree)
    known = bound | set(dir(builtins)) | _EXTRA_MODULE_NAMES

    if not star:
        seen: dict[str, int] = {}
        for n in ast.walk(tree):
            if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load) and n.id not in known:
                if n.id not in seen or n.lineno < seen[n.id]:
                    seen[n.id] = n.lineno
        if seen:
            name, line = min(seen.items(), key=lambda kv: kv[1])
            same_letters = [k for k in known if k.lower() == name.lower() and k != name and not k.startswith("__")]
            close = difflib.get_close_matches(name, [k for k in bound if not k.startswith("__")], n=1, cutoff=0.75)
            if same_letters:
                out.append(HintFinding("undefined_name_case", _ladder(
                    "Python treats uppercase and lowercase letters as different. Do all your names use exactly the same capitals everywhere?",
                    f"Line {line} uses `{name}`, but nothing with that exact spelling exists in your code. "
                    f"You do have a name that is spelled the same except for capital letters.",
                    "Pick one spelling for each thing and use it exactly the same way every time you create it and every time you read it.",
                )))
            else:
                hint = f" You do have a similarly spelled name (`{close[0]}`)." if close else ""
                out.append(HintFinding("undefined_name", _ladder(
                    "Python stops with an error when it reaches a name it has never seen. Which names did you create, and which did you only use?",
                    f"Line {line} uses `{name}`, but your code never creates anything with that name.{hint}",
                    "Create the value before the line that needs it, or fix the spelling so it matches the name you created.",
                )))

    shadow_lines: dict[str, int] = {}
    for n in ast.walk(tree):
        if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store) and n.id in _SHADOW_PRONE:
            if n.id not in shadow_lines or n.lineno < shadow_lines[n.id]:
                shadow_lines[n.id] = n.lineno
    if shadow_lines:
        name, line = min(shadow_lines.items(), key=lambda kv: kv[1])
        out.append(HintFinding("shadowed_builtin", _ladder(
            "Python already gives you some ready-made tools with simple names. Could one of your variable names collide with one of them?",
            f"On line {line} you create a variable called `{name}`, which hides Python's own `{name}` from then on.",
            "Give that variable a more descriptive name that says what it holds, so the built-in tool stays available.",
        )))
    return out


def analyze_code(challenge: FullChallenge, code: str) -> list[HintFinding]:
    """Inspect learner code and return findings, most important first. [] when nothing stands out."""
    fname = challenge.function_name
    expected_params = list(challenge.arg_names)
    findings: list[HintFinding] = []

    if not (code or "").strip():
        return [HintFinding("empty_editor", _ladder(
            "What does the problem ask your function to hand back, and what information does it receive?",
            "The editor is empty, so there is nothing to check yet. A function needs a name, its inputs and a result.",
            f"Start by writing the function `{fname}` with the inputs from the problem, say in words how you would get the "
            "answer by hand, then turn each of those steps into a line.",
        ))]

    try:
        tree = ast.parse(code)
    except SyntaxError as exc:
        where = f"around line {exc.lineno}" if exc.lineno else "near the end"
        return [HintFinding("syntax_error", _ladder(
            "Python reads your file from top to bottom. Where could it get confused about what you meant?",
            f"Python cannot even read your code: it stopped {where} ({(exc.msg or 'invalid syntax').lower()}). "
            "The real slip is often on that line or the one just before it.",
            "Check that every opening bracket or quote is closed, every `if`/`for`/`def` line ends with a colon, "
            "and the indentation is consistent. Fix the first problem Python reports, then try again.",
        ))]
    except (ValueError, TypeError, RecursionError):
        return []

    general = _general_findings(tree)
    func = _find_function(tree, fname)
    if func is None:
        others = [n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]
        pointer = (
            f"Nothing in your code is named `{fname}`"
            + (f" (you defined: {', '.join('`'+o+'`' for o in others[:3])})." if others else ".")
            + " The checker looks for exactly that name."
        )
        missing = HintFinding("missing_function", _ladder(
            "How does the checker know which of your functions to call?",
            pointer,
            f"Define a function named `{fname}` that takes "
            + (", ".join(f"`{p}`" for p in expected_params) if expected_params else "the inputs from the problem")
            + " and keep your logic inside it.",
        ))
        # Real mistakes in the code come first; "wrong function name" is only the headline when nothing else is wrong.
        return general + [missing]

    findings.extend(general)
    params = _param_names(func)
    if expected_params and len(params) != len(expected_params):
        findings.append(HintFinding("wrong_signature", _ladder(
            "How many pieces of information does the problem say the function receives?",
            f"`{fname}` takes {len(params)} input(s) in your code, but the problem gives it {len(expected_params)}.",
            "Make the function's input list match the problem statement, in the same order.",
        )))

    if _is_placeholder_body(func):
        findings.append(HintFinding("placeholder_body", _ladder(
            "If you ran the function right now, what would it hand back?",
            f"`{fname}` does not compute anything yet: its body is only a placeholder. Nothing is being returned.",
            "Say in plain words how you would get the answer by hand for one example, then write one line per step "
            "and finish by giving the result back to the caller.",
        )))
        return findings

    body_nodes = list(_walk_no_nested_defs(func))
    has_return_value = any(isinstance(n, ast.Return) and n.value is not None for n in body_nodes)
    has_print = any(
        isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "print" for n in body_nodes
    )

    if not has_return_value:
        if has_print:
            findings.append(HintFinding("print_without_return", _ladder(
                "How does the code that calls your function get the answer back from it?",
                "Your function shows a result on the screen, but nothing is handed back to the caller, "
                "so the caller receives nothing.",
                "Keep the calculation, but give the finished answer back to the caller instead of only displaying it.",
            )))
        else:
            findings.append(HintFinding("no_return", _ladder(
                "When your function reaches its last line, what does the caller get?",
                f"`{fname}` never hands a value back, so the caller always receives nothing.",
                "Work out the final answer in a variable, then end the function by giving that value back.",
            )))

    used = _names_used(func)
    unused = [p for p in params if p not in used and not p.startswith("_")]
    if unused:
        listed = ", ".join(f"`{p}`" for p in unused)
        findings.append(HintFinding("unused_input", _ladder(
            "Does the answer depend on every piece of information the function is given?",
            f"The input(s) {listed} are never used in your logic, so the result cannot depend on them.",
            "Decide how each input should influence the answer, then use it in the calculation.",
        )))

    mutated = _mutated_params(func, params)
    if mutated:
        findings.append(HintFinding("mutates_input", _ladder(
            "Who else might be holding on to the data you were given, and what happens if you change it?",
            f"Your code changes the caller's own data ({', '.join('`'+m+'`' for m in mutated)}) instead of working on a copy.",
            "Leave the input untouched: build a new value for your answer, or work on a copy of the input.",
        )))

    neg_slice = _unguarded_negative_slice(func, params)
    if neg_slice:
        findings.append(HintFinding("negative_slice_unguarded", _ladder(
            "What does counting backwards from the end do when the count is 0, or larger than the list?",
            f"You slice from the end using `{neg_slice}` with no check on its value. Zero and oversized "
            "counts behave surprisingly with negative positions.",
            "Handle the zero case and the 'count is bigger than the list' case on their own before slicing, "
            "or compute a start position that can never go below the first item.",
        )))

    if _next_index_in_len_loop(func):
        findings.append(HintFinding("index_past_end", _ladder(
            "On the very last pass of your loop, which item does your code look at?",
            "Inside a loop over every position you look one step ahead, so on the final pass you reach past the end.",
            "Either stop the loop one position earlier, or only look ahead when a next item actually exists.",
        )))

    if _while_true_without_exit(func):
        findings.append(HintFinding("endless_loop", _ladder(
            "What has to happen inside your loop for it to ever stop?",
            "Your loop has no way out, so it keeps running forever and the test times out.",
            "Give the loop an exit: change something each pass until a stopping condition becomes true, "
            "or leave it with an explicit `break` or `return` when you are done.",
        )))

    return findings


def _mutated_params(func: ast.FunctionDef, params: list[str]) -> list[str]:
    hit: list[str] = []
    for n in _walk_no_nested_defs(func):
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr in _MUTATING_METHODS:
            base = n.func.value
            if isinstance(base, ast.Name) and base.id in params and base.id not in hit:
                hit.append(base.id)
        if isinstance(n, (ast.Assign, ast.AugAssign)):
            targets = n.targets if isinstance(n, ast.Assign) else [n.target]
            for t in targets:
                if isinstance(t, ast.Subscript) and isinstance(t.value, ast.Name) and t.value.id in params:
                    if t.value.id not in hit:
                        hit.append(t.value.id)
    return hit


def _unguarded_negative_slice(func: ast.FunctionDef, params: list[str]) -> Optional[str]:
    """Return the parameter name used as ``seq[-param:]`` when nothing ever compares that parameter."""
    compared: set[str] = set()
    for n in _walk_no_nested_defs(func):
        if isinstance(n, ast.Compare):
            for part in [n.left, *n.comparators]:
                for sub in ast.walk(part):
                    if isinstance(sub, ast.Name):
                        compared.add(sub.id)
    for n in _walk_no_nested_defs(func):
        if isinstance(n, ast.Subscript) and isinstance(n.slice, ast.Slice):
            bounds = [b for b in (n.slice.lower, n.slice.upper) if b is not None]
            for b in bounds:
                if isinstance(b, ast.UnaryOp) and isinstance(b.op, ast.USub) and isinstance(b.operand, ast.Name):
                    if b.operand.id in params and b.operand.id not in compared:
                        return f"-{b.operand.id}"
    return None


def _next_index_in_len_loop(func: ast.FunctionDef) -> bool:
    for n in _walk_no_nested_defs(func):
        if not (isinstance(n, ast.For) and isinstance(n.target, ast.Name) and isinstance(n.iter, ast.Call)):
            continue
        it = n.iter
        if not (isinstance(it.func, ast.Name) and it.func.id == "range" and len(it.args) == 1):
            continue
        arg = it.args[0]
        if not (isinstance(arg, ast.Call) and isinstance(arg.func, ast.Name) and arg.func.id == "len"):
            continue
        var = n.target.id
        for sub in ast.walk(n):
            if isinstance(sub, ast.Subscript):
                idx = sub.slice
                if (
                    isinstance(idx, ast.BinOp)
                    and isinstance(idx.op, ast.Add)
                    and isinstance(idx.left, ast.Name)
                    and idx.left.id == var
                ):
                    return True
    return False


def _while_true_without_exit(func: ast.FunctionDef) -> bool:
    for n in _walk_no_nested_defs(func):
        if isinstance(n, ast.While) and isinstance(n.test, ast.Constant) and n.test.value is True:
            if not any(isinstance(s, (ast.Break, ast.Return)) for s in ast.walk(n)):
                return True
    return False


def ladder_for_visible_outcomes(challenge: FullChallenge, outcomes: list[str]) -> Optional[list[str]]:
    """Ladder built from learner-visible test outcomes (passed / wrong_output / runtime_error / timeout)."""
    if not outcomes:
        return None
    total = len(outcomes)
    bad = [o for o in outcomes if o != "passed"]
    if not bad:
        cats = [c.replace("_", " ") for c in (challenge.short_input_categories or [])[:4]]
        extra = f" Think about inputs like: {', '.join(cats)}." if cats else ""
        return _ladder(
            "Your code handles the examples you can see. Which kinds of input have you not tried yet?",
            "All visible tests pass, so any remaining bug hides in an input kind the examples do not show." + extra,
            "Write down your own tricky examples (empty, smallest, largest, boundary) and trace your code on each by hand.",
        )
    if "timeout" in bad:
        return _ladder(
            "How long should this take, and what in your code could keep it busy forever?",
            f"{len(bad)} of {total} visible tests ran out of time, which usually means a loop that never finishes.",
            "Trace the loop by hand on a small input and check that something changes each pass toward the stopping condition.",
        )
    if "runtime_error" in bad:
        return _ladder(
            "Which operation in your code could fail for some inputs?",
            f"{len(bad)} of {total} visible tests crash while running. Look for places that assume the data has a certain size or type.",
            "Run the failing visible example by hand, line by line, and find the first line that cannot work for that data.",
        )
    return _ladder(
        "Compare what a failing visible example expects with what your code produces. What differs, exactly?",
        f"{len(bad)} of {total} visible tests give a different answer than expected. Trace one failing example through your code by hand.",
        "Find the first line where your intermediate value stops matching what you would compute by hand, and fix the idea behind that line.",
    )


def build_code_aware_ladder(
    challenge: FullChallenge,
    code: str,
    visible_outcomes: Optional[list[str]] = None,
) -> Optional[tuple[list[str], str]]:
    """Return ``(three_layer_ladder, source_kind)`` for this attempt, or None if nothing specific was found."""
    findings = analyze_code(challenge, code)
    if findings:
        return findings[0].ladder, findings[0].kind
    outcome_ladder = ladder_for_visible_outcomes(challenge, visible_outcomes or [])
    if outcome_ladder:
        return outcome_ladder, "visible_outcomes"
    return None


def extra_notes(challenge: FullChallenge, code: str, limit: int = 3) -> list[str]:
    """One-line pointers for the other problems found after the main one ("Also noticed")."""
    findings = analyze_code(challenge, code)
    return [f.ladder[1] for f in findings[1 : 1 + limit]]
