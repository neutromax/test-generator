"""Joins each specified scenario with the test function that implements it and that test's pytest outcome."""
import ast
from pathlib import Path


def function_sources(code):
    """Source text of every test function in `code`, keyed by function name."""
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return {}
    return {node.name: ast.get_source_segment(code, node) or ""
            for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test_")}


def _script_code(item):
    if item.get("code"):
        return item["code"]
    try:
        return Path(item["script"]).read_text(encoding="utf-8")
    except (KeyError, OSError, TypeError, UnicodeError):
        return ""


def scenario_rows(item):
    """One row per planned scenario: spec fields, test source and pytest status (NOT RUN without a result)."""
    outcomes = {str(test["name"]).split("::")[-1]: test for test in item.get("tests", [])}
    sources = function_sources(_script_code(item))
    rows = []
    for scenario in (item.get("plan") or {}).get("scenarios", []):
        name = scenario.get("name", "")
        outcome = outcomes.get(name)
        rows.append({
            "id": scenario.get("id", ""),
            "name": name,
            "category": scenario.get("category", ""),
            "inputs": scenario.get("inputs", ""),
            "expected": scenario.get("expected", ""),
            "evidence": scenario.get("evidence", ""),
            "code": sources.get(name, ""),
            "status": outcome["status"] if outcome else "NOT RUN",
            "reason": (outcome or {}).get("reason", ""),
        })
    return rows
