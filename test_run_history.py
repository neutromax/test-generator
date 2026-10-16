from datetime import datetime

from src import run_history


def _result(name, status="COMPLETED", passed=3, failed=0, **extra):
    return {"component": name, "spec": "s.md", "status": status,
            "execution": {"passed": passed, "failed": failed, "errors": 0}, **extra}


def test_entry_summarises_counts_statuses_and_coverage():
    results = [_result("a"), _result("b", status="FAILED", passed=0, failed=4), _result("c", repair_applied=True)]
    entry = run_history.build_entry({"mode": "rerun"}, results, {"percent": 46.1}, now=datetime(2026, 1, 2, 3, 4, 5))

    assert entry["time"] == "2026-01-02T03:04:05"
    assert (entry["passed"], entry["failed"], entry["coverage"]) == (6, 4, 46.1)
    assert entry["statuses"] == {"a": "Passing", "b": "Failed", "c": "Repaired"}
    assert entry["counts"]["Failed"] == 1
    assert run_history.build_entry({}, results, {"error": "x"})["coverage"] is None


def test_record_keeps_only_the_latest_runs_and_load_survives_bad_files(tmp_path):
    (tmp_path / "tests").mkdir()
    assert run_history.load(tmp_path) == []
    for number in range(run_history.MAX_RUNS + 3):
        run_history.record(tmp_path, {"n": number})
    runs = run_history.load(tmp_path)
    assert len(runs) == run_history.MAX_RUNS and runs[-1]["n"] == run_history.MAX_RUNS + 2
    run_history.history_path(tmp_path).write_text("not json", encoding="utf-8")
    assert run_history.load(tmp_path) == []


def test_compare_reports_deltas_and_quality_moves():
    before = {"coverage": 40.0, "passed": 10, "failed": 5,
              "statuses": {"a": "Failed", "b": "Passing", "c": "Passing"}}
    after = {"coverage": 46.1, "passed": 14, "failed": 3,
             "statuses": {"a": "Repaired", "b": "Needs review", "c": "Passing", "d": "Passing"}}

    diff = run_history.compare(before, after)

    assert (diff["coverage_delta"], diff["passed_delta"], diff["failed_delta"]) == (6.1, 4, -2)
    assert diff["improved"] == [("a", "Failed", "Repaired")]
    assert diff["regressed"] == [("b", "Passing", "Needs review")]
    assert diff["new"] == ["d"]
    assert run_history.compare({"statuses": {}}, {"coverage": 5, "statuses": {}})["coverage_delta"] is None


def test_rows_are_newest_first_and_label_the_run_type():
    runs = [run_history.build_entry({"mode": "run"}, [_result("a")], {"percent": 10.0}, now=datetime(2026, 1, 1)),
            run_history.build_entry({"mode": "rerun", "cancelled": True}, [_result("a")], None, now=datetime(2026, 1, 2))]
    rows = run_history.rows(runs)
    assert [row["Run"] for row in rows] == ["Re-run of failed specs (cancelled)", "New run"]
    assert rows[1]["When"] == "2026-01-01 00:00:00" and rows[1]["Coverage %"] == 10.0
