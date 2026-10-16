from src.results_view import component_rows, component_status, status_counts


def _item(component, **fields):
    return {"component": component, "spec": "s.md", "status": "COMPLETED", "passed": 3, "failed": 0, "errors": 0, **fields}


def test_status_separates_passing_from_flagged_repaired_and_failed():
    assert component_status(_item("a")) == "Passing"
    assert component_status(_item("a", review_flags=["x: only asserts not None"])) == "Needs review"
    assert component_status(_item("a", repair_applied=True)) == "Repaired"
    assert component_status(_item("a", status="FAILED")) == "Failed"
    assert component_status(_item("a", spec=None)) == "No spec"


def test_rows_sort_worst_first_and_carry_cause_fix_and_coverage():
    failed = _item("pkg.bad", status="FAILED", passed=0, failed=4,
                   diagnosis={"cause": "setup_or_api_misuse", "fix": "Patch the module, not the class."})
    flagged = _item("pkg.weak", review_flags=["test_a: only asserts the result is not None"])
    plan_failure = {"component": "pkg.unplanned", "error": "HTTP 503 from gateway", "failure_stage": "plan"}
    coverage = {"components": [{"component": "pkg.bad", "percent": 12.5}]}

    rows = component_rows([_item("pkg.ok"), flagged, failed], coverage, [plan_failure])

    assert [row["Component"] for row in rows] == ["pkg.bad", "pkg.unplanned", "pkg.weak", "pkg.ok"]
    bad = rows[0]
    assert (bad["Tests"], bad["Coverage %"]) == ("0/4", 12.5)
    assert bad["Note"] == "Patch the module, not the class."
    assert bad["Cause"] == "Wrong setup, imports or call signature"
    assert "retryable" in rows[1]["Note"]
    assert rows[2]["Note"].startswith("test_a: only asserts")
    assert status_counts(rows) == {"Failed": 1, "No spec": 1, "Needs review": 1, "Repaired": 0, "Passing": 1}
