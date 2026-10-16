"""Per-repository run history: one summary per run, so changes in quality and coverage can be compared."""
import json
from collections import Counter
from datetime import datetime
from pathlib import Path

from src.results_view import STATUSES, component_status

MAX_RUNS = 30
_RANK = {"Failed": 0, "No spec": 0, "Needs review": 1, "Repaired": 2, "Passing": 3}
_MODE_LABELS = {"rerun": "Re-run of failed specs", "run": "New run", "continue": "Continue run",
                "topup": "Coverage top-up"}


def history_path(repo_path):
    return Path(repo_path) / "tests" / "run_history.json"


def build_entry(run_state, results, coverage=None, now=None):
    execution = [item.get("execution") or {} for item in results]
    counts = Counter(component_status(item) for item in results)
    percent = None if not coverage or coverage.get("error") else coverage.get("percent")
    return {
        "time": (now or datetime.now()).isoformat(timespec="seconds"),
        "mode": run_state.get("mode") or "run",
        "cancelled": bool(run_state.get("cancelled")),
        "components": len(results),
        "passed": sum(item.get("passed", 0) for item in execution),
        "failed": sum(item.get("failed", 0) + item.get("errors", 0) for item in execution),
        "coverage": percent,
        "counts": {status: counts[status] for status in STATUSES},
        "statuses": {item["component"]: component_status(item) for item in results},
    }


def load(repo_path):
    try:
        runs = json.loads(history_path(repo_path).read_text(encoding="utf-8"))
        return runs if isinstance(runs, list) else []
    except (OSError, ValueError):
        return []


def record(repo_path, entry):
    runs = [*load(repo_path), entry][-MAX_RUNS:]
    path = history_path(repo_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(runs, indent=1), encoding="utf-8")
    return runs


def compare(previous, current):
    """What changed between two runs: coverage and pass deltas, and components that moved up or down in quality."""
    before, after = previous.get("statuses", {}), current.get("statuses", {})
    moved = [(name, before[name], status) for name, status in sorted(after.items())
             if name in before and _RANK[status] != _RANK[before[name]]]
    coverage = (None if previous.get("coverage") is None or current.get("coverage") is None
                else round(current["coverage"] - previous["coverage"], 1))
    return {
        "coverage_delta": coverage,
        "passed_delta": current.get("passed", 0) - previous.get("passed", 0),
        "failed_delta": current.get("failed", 0) - previous.get("failed", 0),
        "improved": [item for item in moved if _RANK[item[2]] > _RANK[item[1]]],
        "regressed": [item for item in moved if _RANK[item[2]] < _RANK[item[1]]],
        "new": sorted(set(after) - set(before)),
    }


def rows(runs):
    """Table rows, newest first."""
    return [{
        "When": run["time"].replace("T", " "),
        "Run": _MODE_LABELS.get(run["mode"], "Run") + (" (cancelled)" if run.get("cancelled") else ""),
        "Components": run["components"],
        "Passed": run["passed"],
        "Failed": run["failed"],
        "Coverage %": run["coverage"],
        "Failing components": run["counts"].get("Failed", 0) + run["counts"].get("No spec", 0),
    } for run in reversed(runs)]
