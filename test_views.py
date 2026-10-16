from src.coverage_view import annotated_source, parse_ranges
from src.side_by_side import function_sources, scenario_rows

CODE = '''import pytest

def test_a():
    assert 1 == 1

class TestGroup:
    def test_b(self):
        assert 2 == 2

def helper():
    pass
'''


def test_function_sources_returns_test_functions_only():
    sources = function_sources(CODE)
    assert set(sources) == {"test_a", "test_b"}
    assert sources["test_a"].startswith("def test_a():") and "assert 1 == 1" in sources["test_a"]
    assert function_sources("def broken(:") == {}


def test_scenario_rows_join_spec_test_and_outcome():
    item = {
        "code": CODE,
        "plan": {"scenarios": [
            {"id": "SC-01", "name": "test_a", "category": "Happy Path", "inputs": "i", "expected": "e", "evidence": "ev"},
            {"id": "SC-02", "name": "test_b", "category": "Negative"},
            {"id": "SC-03", "name": "test_missing"}]},
        "tests": [{"name": "test_a", "status": "PASSED", "reason": ""},
                  {"name": "TestGroup::test_b", "status": "FAILED", "reason": "assert 3 == 2"}],
    }
    rows = scenario_rows(item)

    assert [(r["id"], r["status"]) for r in rows] == [("SC-01", "PASSED"), ("SC-02", "FAILED"), ("SC-03", "NOT RUN")]
    assert rows[0]["expected"] == "e" and "assert 1 == 1" in rows[0]["code"]
    assert rows[1]["reason"] == "assert 3 == 2"
    assert rows[2]["code"] == ""


def test_scenario_rows_read_the_script_when_code_was_not_stored(tmp_path):
    script = tmp_path / "x_test.py"
    script.write_text(CODE, encoding="utf-8")
    rows = scenario_rows({"script": str(script), "plan": {"scenarios": [{"name": "test_a"}]}})
    assert "assert 1 == 1" in rows[0]["code"]
    assert scenario_rows({}) == []


def test_parse_ranges_handles_single_lines_and_ranges():
    assert parse_ranges("366-367, 370, 372-373") == [366, 367, 370, 372, 373]
    assert parse_ranges("-") == [] and parse_ranges(None) == []


def test_annotated_source_marks_uncovered_lines_and_gaps():
    lines = [f"line {number}" for number in range(1, 21)]
    text = annotated_source(lines, [3, 15], context=1)

    rows = text.splitlines()
    assert rows[0] == "      2  line 2"
    assert "!     3  line 3" in rows
    assert "      ..." in rows
    assert "!    15  line 15" in rows
    assert annotated_source(lines, [999]) == ""
    assert annotated_source(lines, range(1, 21), context=0, limit=5).endswith("... 15 more line(s)")
