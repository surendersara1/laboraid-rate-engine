"""Extractor-Python codegen validator (D.4).

Given a candidate Python source string for an `extract_<local>(union_dir)`
function, verify:

1. `py_compile` accepts the source (no syntax errors).
2. The AST contains exactly one top-level function definition named
   `extract_<local>` taking a single positional arg `union_dir`.
3. The function appears to return a 2-tuple (rows, gaps) — either via a
   literal `return rows, gaps` / `return (rows, gaps)` or a `Return` whose
   value is a 2-element tuple. (We do not type-check; that's deferred to a
   container-level `mypy --strict` pass — see BUILD_LOG.)

Pure Python, no LLM. ``mypy --strict`` clean.

Return shape:
    {
      "ok": bool,
      "function_name": str | None,
      "syntax_pass": bool,
      "signature_pass": bool,
      "returns_tuple_pass": bool,
      "errors": list[str],
      "warnings": list[str],
    }
"""

from __future__ import annotations

import ast
import py_compile
import tempfile
from pathlib import Path
from typing import Any


def codegen_check(
    candidate_source: str,
    expected_local: str | None = None,
) -> dict[str, Any]:
    """Validate the candidate extractor source. See module docstring for shape.

    Args:
        candidate_source: the Python source as a string.
        expected_local: if provided (e.g., ``"120"``), the function name must be
            exactly ``extract_<expected_local>``. If ``None``, any name matching
            ``extract_<digits>`` is accepted.
    """
    result: dict[str, Any] = {
        "ok": False,
        "function_name": None,
        "syntax_pass": False,
        "signature_pass": False,
        "returns_tuple_pass": False,
        "errors": [],
        "warnings": [],
    }
    errors: list[str] = result["errors"]
    warnings: list[str] = result["warnings"]

    # --- 1) syntax via py_compile -------------------------------------------
    syntax_ok, compile_err = _run_py_compile(candidate_source)
    result["syntax_pass"] = syntax_ok
    if not syntax_ok:
        errors.append(f"py_compile failed: {compile_err}")
        # No point going further — AST parse would also fail.
        return result

    # --- 2) AST parse to inspect signature + return shape -------------------
    try:
        tree = ast.parse(candidate_source)
    except SyntaxError as exc:
        errors.append(f"ast.parse failed despite py_compile pass: {exc}")
        return result

    fn_defs: list[ast.FunctionDef] = [
        node for node in tree.body if isinstance(node, ast.FunctionDef)
    ]
    extract_fns = [fn for fn in fn_defs if fn.name.startswith("extract_")]
    if not extract_fns:
        errors.append(
            "no top-level function whose name starts with 'extract_' was found"
        )
        return result

    if len(extract_fns) > 1:
        warnings.append(
            f"multiple extract_* functions defined: "
            f"{[fn.name for fn in extract_fns]}; using the first"
        )

    fn = extract_fns[0]
    result["function_name"] = fn.name

    # Name matches expected_local?
    if expected_local is not None:
        wanted = f"extract_{expected_local}"
        if fn.name != wanted:
            errors.append(f"function name must be {wanted!r}, got {fn.name!r}")

    # Signature: single positional arg named `union_dir`. Allow defaults but
    # not `**kwargs`. Reject `*args`.
    sig_errors = _check_signature(fn)
    if sig_errors:
        errors.extend(sig_errors)
    else:
        result["signature_pass"] = True

    # --- 3) returns a 2-tuple? ----------------------------------------------
    returns_tuple = _function_returns_2tuple(fn)
    result["returns_tuple_pass"] = returns_tuple
    if not returns_tuple:
        errors.append(
            f"function {fn.name!r} does not appear to return a 2-tuple "
            "(rows, gaps); every return statement must be a 2-element tuple"
        )

    result["ok"] = (
        result["syntax_pass"]
        and result["signature_pass"]
        and result["returns_tuple_pass"]
        and not errors
    )
    return result


def _run_py_compile(source: str) -> tuple[bool, str]:
    """Compile the source via py_compile; return (ok, error_message)."""
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".py", delete=False, encoding="utf-8"
    ) as tmp:
        tmp.write(source)
        tmp_path = Path(tmp.name)
    try:
        py_compile.compile(str(tmp_path), doraise=True)
        return True, ""
    except py_compile.PyCompileError as exc:
        return False, str(exc.msg if hasattr(exc, "msg") else exc)
    except SyntaxError as exc:
        return False, f"{exc.msg} (line {exc.lineno})"
    finally:
        try:
            tmp_path.unlink()
        except OSError:
            pass


def _check_signature(fn: ast.FunctionDef) -> list[str]:
    """Verify the function takes a single positional arg named ``union_dir``."""
    errors: list[str] = []
    args = fn.args

    if args.vararg is not None:
        errors.append(f"{fn.name}: must not use *args")
    if args.kwarg is not None:
        errors.append(f"{fn.name}: must not use **kwargs")

    positional = list(args.posonlyargs) + list(args.args)
    if len(positional) != 1:
        errors.append(
            f"{fn.name}: must take exactly 1 positional arg (union_dir), "
            f"got {len(positional)}: {[a.arg for a in positional]}"
        )
        return errors

    arg_name = positional[0].arg
    if arg_name != "union_dir":
        errors.append(
            f"{fn.name}: the sole positional arg must be named 'union_dir', "
            f"got {arg_name!r}"
        )
    return errors


def _function_returns_2tuple(fn: ast.FunctionDef) -> bool:
    """True if every `return` in the function returns a 2-element tuple.

    Accepts both ``return rows, gaps`` (parsed as a Tuple) and
    ``return (rows, gaps)``. A function with no explicit return is rejected.
    Returns inside nested functions/lambdas are ignored.
    """
    saw_any_return = False

    class _ReturnVisitor(ast.NodeVisitor):
        def __init__(self) -> None:
            self.depth = 0
            self.all_two_tuple = True
            self.any_return = False

        def visit_FunctionDef(self, node: ast.FunctionDef) -> None:  # noqa: N802
            # Don't descend into nested functions.
            if self.depth == 0:
                self.depth += 1
                self.generic_visit(node)
                self.depth -= 1

        def visit_AsyncFunctionDef(
            self, node: ast.AsyncFunctionDef
        ) -> None:  # noqa: N802
            if self.depth == 0:
                self.depth += 1
                self.generic_visit(node)
                self.depth -= 1

        def visit_Lambda(self, node: ast.Lambda) -> None:  # noqa: N802
            # Lambdas have implicit returns; skip them.
            return

        def visit_Return(self, node: ast.Return) -> None:  # noqa: N802
            self.any_return = True
            value = node.value
            if value is None:
                # bare `return` — not a 2-tuple
                self.all_two_tuple = False
                return
            if isinstance(value, ast.Tuple) and len(value.elts) == 2:
                return
            # Any other shape (single value, 3-tuple, dict, etc.) fails.
            self.all_two_tuple = False

    visitor = _ReturnVisitor()
    visitor.depth = 1  # we're already inside `fn`
    for node in fn.body:
        visitor.visit(node)
    saw_any_return = visitor.any_return
    return saw_any_return and visitor.all_two_tuple
