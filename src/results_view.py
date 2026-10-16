"""Turns per-component run results into table rows with an honest quality status."""
from src.orchestrator.spec_pipeline import describe_failure

STATUSES = ("Failed", "No spec", "Needs review", "Repaired", "Passing")

CAUSE_LABELS = {
    "wrong_expectation": "Test asserts something the source does not do",
    "wrong_input": "Input does not reach the intended path",
    "setup_or_api_misuse": "Wrong setup, imports or call signature",
    "environment": "Missing dependency, file or service",
    "source_bug_suspected": "Possible bug in the source",
    "unknown": "Cause not determined",
}


def component_status(item):
    """Passing means every test passed; flagged or repaired passes are reported separately."""
    if not item.get("spec"):
        return "No spec"
    if item.get("status") != "COMPLETED":
        return "Failed"
    if item.get("review_flags"):
        return "Needs review"
    if item.get("repair_applied"):
        return "Repaired"
    return "Passing"


def _note(item, status):
    diagnosis = item.get("diagnosis") or {}
    if status in ("Failed", "No spec"):
        return diagnosis.get("fix") or describe_failure(item)
    if status == "Needs review":
        return str(item["review_flags"][0])
    if status == "Repaired":
        return "Failed on first run; tests were rewritten to match the source"
    return ""


def _tests(item):
    total = sum(item.get(key, 0) for key in ("passed", "failed", "errors"))
    return f"{item.get('passed', 0)}/{total}" if total else "-"


def component_rows(components, coverage=None, plan_failures=()):
    """One row per component, worst status first; coverage is matched by component name."""
    percent = {row["component"]: row["percent"] for row in (coverage or {}).get("components", [])}
    rows = []
    for item in [*components, *({**failure, "spec": None} for failure in plan_failures)]:
        status = component_status(item)
        cause = (item.get("diagnosis") or {}).get("cause")
        rows.append({
            "Component": item["component"],
            "Status": status,
            "Tests": _tests(item),
            "Coverage %": percent.get(item["component"]),
            "Cause": CAUSE_LABELS.get(cause, "") if cause else "",
            "Note": _note(item, status),
        })
    rows.sort(key=lambda row: (STATUSES.index(row["Status"]), row["Component"]))
    return rows


def status_counts(rows):
    return {status: sum(row["Status"] == status for row in rows) for status in STATUSES}
