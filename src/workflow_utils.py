"""Consolidated workflow helpers used by the Streamlit app.

This module merges chat parsing, result table helpers, coverage rendering,
run progress/history bookkeeping, side-by-side scenario mapping, and Copilot
bridge filesystem utilities into one file to simplify project structure.
"""

from __future__ import annotations

import ast
import json
import os
import re
import shutil
import subprocess
import time
import zipfile
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from src.orchestrator.spec_pipeline import describe_failure

# -------------------- Chat request understanding --------------------

TEST_TYPES = ("unit_test", "integration_test", "e2e_test", "security")
MODES = ("recommended", "continue", "all")
CUSTOM_MODE = "custom"
ENGINES = ("vio",)
INTENTS = ("generate", "rerun", "status", "count", "other")

_TYPE_PATTERNS = {
    "unit_test": r"\bunit\b",
    "integration_test": r"\bintegration\b",
    "e2e_test": r"\be2e\b|end[- ]to[- ]end",
    "security": r"\bsecurity\b|\bvulnerab\w*",
}
_URL_RE = re.compile(r"https?://[^\s/<>\"')]*github[^\s/<>\"')]*/[\w.-]+/[\w.-]+", re.IGNORECASE)
_TARGET_RES = (
    re.compile(r"\b(?:class|function|method|component|module)\s+`?([A-Za-z_][\w.]*)`?", re.IGNORECASE),
    re.compile(r"`?\b([A-Za-z_][\w.]*)`?\s+(?:class|function|method|component|module)\b", re.IGNORECASE),
)
_CAPITAL_TARGET_RE = re.compile(r"\b(?:for|of|on|around|covering)\s+(?:the\s+)?`?([A-Z][A-Za-z0-9_.]*)`?")
_STOP_TARGETS = {"the", "a", "an", "this", "that", "each", "every", "all", "public", "main", "my", "its", "of"}

HELP_TEXT = (
    "I generate pytest specs and runnable test scripts from the repository source code itself (no pre-available "
    "answers). Share the repository link and "
    "the test type you need (unit, integration, e2e or security). You can also ask me to re-run failed specs "
    "or report progress."
)


def new_request() -> Dict:
    return {"repo_url": "", "repo_name": "", "test_types": [], "target": "", "max_components": 0,
            "mode": "", "engine": "", "confirmed": False}


def interpret_locally(text: str, repo_names: List[str]) -> Dict:
    """Rule-based extraction; works offline and is the fallback when the model is unavailable."""
    lowered = text.lower()
    found: Dict = {"intent": "other"}

    match = _URL_RE.search(text)
    if match:
        url = match.group(0).rstrip(".,;:!?")
        found["repo_url"] = url[:-4] if url.endswith(".git") else url
    for name in sorted(repo_names, key=len, reverse=True):
        if re.search(rf"(?<![\w-]){re.escape(name.lower())}(?![\w-])", lowered):
            found["repo_name"] = name
            break

    types = [kind for kind, pattern in _TYPE_PATTERNS.items() if re.search(pattern, lowered)]
    if types:
        found["test_types"] = types
    for engine in ENGINES:
        if re.search(rf"\b{engine}\b", lowered):
            found["engine"] = engine

    count = re.search(r"\b(?:first|top|next)?\s*(\d{1,3})\s+(?:components?|specs?|classes|functions?|items?)\b", lowered)
    if count:
        found["max_components"] = int(count.group(1))

    if re.search(r"\ball (?:the )?(?:meaningful )?(?:components|classes|functions)\b|\beverything\b", lowered):
        found["mode"] = "all"
    elif re.search(r"\bcontinue\b|\bremaining\b|\buncovered\b|\bthe rest\b|\bmore components\b", lowered):
        found["mode"] = "continue"
    elif re.search(r"\bfresh\b|from scratch|start over", lowered):
        found["mode"] = "recommended"

    for pattern in _TARGET_RES:
        for hit in pattern.finditer(text):
            if hit.group(1).lower() not in _STOP_TARGETS:
                found["target"] = hit.group(1)
                break
        if found.get("target"):
            break
    if not found.get("target"):
        hit = _CAPITAL_TARGET_RE.search(text)
        if hit and hit.group(1).lower() not in _STOP_TARGETS:
            found["target"] = hit.group(1)
    repo_words = {name.lower() for name in repo_names}
    if found.get("target", "").lower() in repo_words or (
            found.get("repo_url") and found.get("target", "").lower() == found["repo_url"].rsplit("/", 1)[-1].lower()):
        del found["target"]

    if re.search(r"\bhow many\b|\bnumber of (?:components|classes|functions|specs)\b", lowered):
        found["intent"] = "count"
    elif re.search(r"\bre-?run\b|\bretry\b|\btry again\b|\bfailed (?:specs?|tests?)\b", lowered):
        found["intent"] = "rerun"
    elif re.search(r"\bstatus\b|\bprogress\b|how(?:'s| is)? it going|\bdone yet\b|\bresults?\b", lowered):
        found["intent"] = "status"
    elif re.search(r"\b(?:generate|create|write|build|make|cover|test|tests|specs?|add)\b", lowered) or types or match:
        found["intent"] = "generate"
    return found


_SYSTEM_PROMPT = (
    "You turn a user's chat message about generating Python tests into JSON. Reply with ONLY a JSON object with "
    'keys: "intent" (generate|rerun|status|count|other), "repo_url" (GitHub URL or ""), "repo_name" (one of the known '
    'repositories or ""), "test_types" (subset of unit_test, integration_test, e2e_test, security), "target" '
    '(class/function/module name the user wants tested or ""), "max_components" (integer, 0 if unspecified), '
    '"mode" (recommended|continue|all or ""), "engine" ("vio" or ""), '
    '"reply" (one short friendly sentence, only when intent is '
    "other: greet or ask which repository and component to test, and make no claims about limits or counts). "
    "Use empty values for anything the user did not say. Never invent a repository."
)


def interpret_with_model(
    chat: Callable[..., dict], text: str, history: List[Dict], repo_names: List[str], current: Dict,
) -> Optional[Dict]:
    """Ask the model for structured intent; returns None when the call or the JSON is unusable."""
    recent = "\n".join(f"{m['role']}: {m['content'][:300]}" for m in history[-6:])
    prompt = (
        f"Known cloned repositories: {', '.join(repo_names) or 'none'}\n"
        f"Request so far: {json.dumps(current)}\n"
        f"Recent conversation:\n{recent}\n\nNew user message: {text}"
    )
    try:
        result = chat(prompt, system=_SYSTEM_PROMPT)
    except Exception:
        return None
    if not result.get("ok"):
        return None
    block = re.search(r"\{.*\}", result.get("content", ""), re.DOTALL)
    if not block:
        return None
    try:
        data = json.loads(block.group(0))
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def _clean(data: Dict, repo_names: List[str], text: str) -> Dict:
    """Keep only well-formed fields from model output; scope fields come from explicit wording only."""
    cleaned: Dict = {}
    if data.get("intent") in INTENTS:
        cleaned["intent"] = data["intent"]
    url = str(data.get("repo_url") or "")
    match = _URL_RE.search(url)
    if match:
        cleaned["repo_url"] = match.group(0)
    name = str(data.get("repo_name") or "")
    if name in repo_names:
        cleaned["repo_name"] = name
    target = str(data.get("target") or "").strip()
    if re.fullmatch(r"[A-Za-z_][\w.]*", target) and target.lower() in text.lower():
        cleaned["target"] = target
    if isinstance(data.get("reply"), str) and data["reply"].strip():
        cleaned["reply"] = data["reply"].strip()
    return cleaned


def interpret(
    text: str, history: List[Dict], repo_names: List[str], current: Dict,
    chat: Optional[Callable[..., dict]] = None,
) -> Dict:
    """Combine rule-based and model extraction; rule-based fields fill any gap the model leaves."""
    parsed = interpret_locally(text, repo_names)
    if chat is not None:
        modeled = interpret_with_model(chat, text, history, repo_names, current)
        if modeled:
            parsed = {**parsed, **_clean(modeled, repo_names, text)}
    return parsed


def merge_request(current: Dict, parsed: Dict) -> Dict:
    """Later messages refine the request; a new repository starts a fresh target and scope."""
    merged = {**current}
    new_repo = (parsed.get("repo_url") and parsed["repo_url"] != current["repo_url"]) or (
        parsed.get("repo_name") and parsed["repo_name"] != current["repo_name"])
    if new_repo:
        merged.update(repo_url="", repo_name="", target="", max_components=0, mode="")
    for key in ("repo_url", "repo_name", "test_types", "target", "max_components", "mode", "engine"):
        if parsed.get(key):
            merged[key] = parsed[key]
    if parsed.get("repo_url"):
        merged["repo_name"] = ""
    elif parsed.get("repo_name"):
        merged["repo_url"] = ""
    return merged


def missing_question(request: Dict, repo_names: List[str]) -> str:
    """The one question needed before generation can start, or '' when the request is complete."""
    if request["repo_url"] or request["repo_name"]:
        return ""
    known = f" Already cloned: {', '.join(repo_names)}." if repo_names else ""
    return "Please share the GitHub repository link." + known


def type_label(test_types: List[str]) -> str:
    return ", ".join(t.replace("_test", "").replace("_", " ") for t in (test_types or ["unit_test"]))


def describe_request(request: Dict, repo_label: str) -> str:
    scope = f" for `{request['target']}`" if request["target"] else ""
    return f"Generating {type_label(request['test_types'])} tests{scope} in {repo_label}."


# -------------------- Results view helpers --------------------

STATUSES = ("Generation failed", "Runtime failed", "No spec", "Needs review", "Repaired", "Code-consistent")

CAUSE_LABELS = {
    "wrong_expectation": "Test asserts something the source does not do",
    "wrong_input": "Input does not reach the intended path",
    "setup_or_api_misuse": "Wrong setup, imports or call signature",
    "environment": "Missing dependency, file or service",
    "source_bug_suspected": "Possible bug in the source",
    "unknown": "Cause not determined",
}


def _failed_status(item):
    """Split non-completed items into generation-time vs runtime failures."""
    stage = str(item.get("failure_stage") or "").lower()
    error = str(item.get("error") or "").lower()
    generation_markers = ("generation", "plan", "syntax", "spec")
    if any(marker in stage for marker in generation_markers):
        return "Generation failed"
    if "invalid python" in error or "syntax" in error:
        return "Generation failed"
    return "Runtime failed"


def component_status(item):
    """Code-consistent means generated tests match current implementation behavior."""
    if not item.get("spec"):
        return "No spec"
    if item.get("status") != "COMPLETED":
        return _failed_status(item)
    if item.get("review_flags"):
        return "Needs review"
    if item.get("repair_applied"):
        return "Repaired"
    return "Code-consistent"


def _note(item, status):
    diagnosis = item.get("diagnosis") or {}
    if status in ("Generation failed", "Runtime failed", "No spec"):
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


# -------------------- Coverage view helpers --------------------

def parse_ranges(text):
    """Line numbers from a coverage range string such as '366-367, 369-381'."""
    numbers = set()
    for start, end in re.findall(r"(\d+)(?:-(\d+))?", str(text or "")):
        numbers.update(range(int(start), int(end or start) + 1))
    return sorted(numbers)


def annotated_source(lines, uncovered, context=2, limit=150):
    """Numbered source windows around uncovered lines; '!' marks the lines no passing test runs."""
    missing = {number for number in uncovered if 0 < number <= len(lines)}
    keep = sorted({n for number in missing
                   for n in range(max(1, number - context), min(len(lines), number + context) + 1)})
    out, previous = [], None
    for number in keep[:limit]:
        if previous is not None and number != previous + 1:
            out.append("      ...")
        out.append(f"{'!' if number in missing else ' '} {number:>5}  {lines[number - 1].rstrip()}")
        previous = number
    if len(keep) > limit:
        out.append(f"      ... {len(keep) - limit} more line(s)")
    return "\n".join(out)


# -------------------- Run progress helpers --------------------

PHASES = ("Discover", "Specify and test", "Raise coverage", "Report")
_PHASE_KEYS = ("discover", "components", "coverage", "report")
STAGES = ("plan", "script", "pytest", "repair")
STAGE_LABELS = {"plan": "Planning", "script": "Writing script", "pytest": "Running pytest", "repair": "Repairing"}


def begin_components(run_state, total, done=0, now=None):
    run_state.update(total=total, done=done, done0=done, current=[], stages={}, phase="components",
                     components_started=time.time() if now is None else now)


def set_phase(run_state, phase):
    run_state["phase"] = phase


def phase_index(run_state):
    """Index into PHASES of the active phase; len(PHASES) once the run is over."""
    if run_state.get("finished"):
        return len(PHASES)
    phase = run_state.get("phase")
    return _PHASE_KEYS.index(phase) if phase in _PHASE_KEYS else 0


def stage_counts(run_state):
    """Components currently in each stage, keyed by stage name."""
    counts = Counter(run_state.get("stages", {}).values())
    return {stage: counts[stage] for stage in STAGES if counts[stage]}


def record_stage(run_state, identity, stage):
    run_state.setdefault("stages", {})[identity] = stage


def clear_stage(run_state, identity):
    run_state.get("stages", {}).pop(identity, None)


def eta_seconds(run_state, now=None):
    """Seconds left in the component phase, from the pace so far; None until two components have finished."""
    if run_state.get("phase") != "components" or run_state.get("finished"):
        return None
    finished = run_state.get("done", 0) - run_state.get("done0", 0)
    remaining = run_state.get("total", 0) - run_state.get("done", 0)
    started = run_state.get("components_started")
    if finished < 2 or remaining <= 0 or started is None:
        return None
    elapsed = (time.time() if now is None else now) - started - run_state.get("paused_seconds", 0)
    return max(0.0, elapsed / finished * remaining)


def format_duration(seconds):
    seconds = int(round(seconds))
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return f"{hours}h {minutes}m"
    return f"{minutes}m {secs:02d}s" if minutes else f"{secs}s"


def request_cancel(run_state):
    run_state["cancel"] = True
    run_state["paused"] = False


def toggle_pause(run_state, now=None):
    """Pause or resume; time spent paused is excluded from the ETA."""
    now = time.time() if now is None else now
    if run_state.get("paused"):
        run_state["paused_seconds"] = run_state.get("paused_seconds", 0) + now - run_state.pop("paused_at", now)
        run_state["paused"] = False
    else:
        run_state.update(paused=True, paused_at=now)


def checkpoint(run_state, sleep=time.sleep):
    """Block while the run is paused; True when it was cancelled and no new work should start."""
    while run_state.get("paused") and not run_state.get("cancel"):
        sleep(0.5)
    return bool(run_state.get("cancel"))


# -------------------- Run history helpers --------------------

MAX_RUNS = 30
_RANK = {"Generation failed": 0, "Runtime failed": 0, "No spec": 0, "Needs review": 1,
         "Repaired": 2, "Code-consistent": 3}
_MODE_LABELS = {"rerun": "Re-run of failed specs", "run": "New run", "continue": "Continue run",
                "topup": "Coverage top-up"}
_LEGACY_STATUS_MAP = {
    "Passing": "Code-consistent",
    "Failed": "Runtime failed",
}


def _normalize_status_label(status):
    """Map legacy labels from older run history files to current status labels."""
    return _LEGACY_STATUS_MAP.get(status, status)


def _normalize_statuses(statuses):
    return {name: _normalize_status_label(status) for name, status in (statuses or {}).items()}


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
    before = _normalize_statuses(previous.get("statuses", {}))
    after = _normalize_statuses(current.get("statuses", {}))
    moved = [(name, before[name], status) for name, status in sorted(after.items())
             if name in before and _RANK.get(status, 0) != _RANK.get(before[name], 0)]
    coverage = (None if previous.get("coverage") is None or current.get("coverage") is None
                else round(current["coverage"] - previous["coverage"], 1))
    return {
        "coverage_delta": coverage,
        "passed_delta": current.get("passed", 0) - previous.get("passed", 0),
        "failed_delta": current.get("failed", 0) - previous.get("failed", 0),
        "improved": [item for item in moved if _RANK.get(item[2], 0) > _RANK.get(item[1], 0)],
        "regressed": [item for item in moved if _RANK.get(item[2], 0) < _RANK.get(item[1], 0)],
        "new": sorted(set(after) - set(before)),
    }


def rows(runs):
    """Table rows, newest first."""
    def failing_components(run):
        counts = run.get("counts", {})
        return (counts.get("Generation failed", 0)
                + counts.get("Runtime failed", 0)
                + counts.get("Failed", 0)
                + counts.get("No spec", 0))

    return [{
        "When": run["time"].replace("T", " "),
        "Run": _MODE_LABELS.get(run["mode"], "Run") + (" (cancelled)" if run.get("cancelled") else ""),
        "Components": run["components"],
        "Passed": run["passed"],
        "Failed": run["failed"],
        "Coverage %": run["coverage"],
        "Failing components": failing_components(run),
    } for run in reversed(runs)]


# -------------------- Side-by-side scenario mapping --------------------

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
    normalized_outcomes = {
        re.sub(r"[^a-z0-9]", "", str(name).lower()): outcome
        for name, outcome in outcomes.items()
    }
    sources = function_sources(_script_code(item))
    normalized_sources = {
        re.sub(r"[^a-z0-9]", "", str(name).lower()): code
        for name, code in sources.items()
    }
    out = []
    for scenario in (item.get("plan") or {}).get("scenarios", []):
        name = scenario.get("name", "")
        normalized_name = re.sub(r"[^a-z0-9]", "", str(name).lower())
        outcome = outcomes.get(name) or normalized_outcomes.get(normalized_name)
        code = sources.get(name) or normalized_sources.get(normalized_name, "")
        fallback_failure = item.get("error") or item.get("repair_error") or ""
        execution = item.get("execution") or {}
        status = outcome["status"] if outcome else (
            "PASSED" if item.get("status") == "COMPLETED" and execution.get("all_passed") else
            "FAILED" if item.get("status") == "FAILED" and fallback_failure else
            "NOT RUN"
        )
        reason = (outcome or {}).get("reason", "") or (
            "Mapped from script-level pass result" if status == "PASSED" and not outcome else
            fallback_failure if status == "FAILED" else ""
        )
        out.append({
            "id": scenario.get("id", ""),
            "name": name,
            "category": scenario.get("category", ""),
            "inputs": scenario.get("inputs", ""),
            "expected": scenario.get("expected", ""),
            "evidence": scenario.get("evidence", ""),
            "code": code,
            "status": status,
            "reason": reason,
        })
    return out


# -------------------- Copilot bridge filesystem helpers --------------------

def copy_to_clipboard(prompt_text: str) -> bool:
    """Copy prompt text to the system clipboard."""
    try:
        import pyperclip
        pyperclip.copy(prompt_text)
        return True
    except Exception:
        return False


def open_in_vscode(filepath: str) -> bool:
    """Open a file in VS Code using the `code` CLI."""
    try:
        abs_path = str(Path(filepath).resolve())
        subprocess.Popen(["code", abs_path])
        return True
    except Exception:
        return False


def create_test_folders(base_path: str) -> bool:
    """Create standard tests folder structure."""
    try:
        base_path_obj = Path(base_path)
        test_folders = [
            base_path_obj / "tests",
            base_path_obj / "tests" / "specs",
            base_path_obj / "tests" / "test_scripts",
            base_path_obj / "tests" / "coverage",
        ]
        for folder_path in test_folders:
            folder_path.mkdir(parents=True, exist_ok=True)
        return True
    except Exception:
        return False


def get_test_results(test_folder_path: str) -> dict[str, list[dict[str, str]]]:
    """Collect generated test artifacts from tests/specs, tests/test_scripts and tests/coverage."""
    results = {"specs": [], "test_scripts": [], "coverage": []}
    try:
        test_path = Path(test_folder_path)
        specs_path = test_path / "specs"
        if specs_path.exists():
            for md_file in sorted(specs_path.glob("*.md")):
                try:
                    results["specs"].append({"name": md_file.name, "content": md_file.read_text(encoding="utf-8")})
                except Exception:
                    pass

        scripts_path = test_path / "test_scripts"
        if scripts_path.exists():
            for script_file in sorted(scripts_path.glob("*")):
                if script_file.is_file():
                    try:
                        results["test_scripts"].append({
                            "name": script_file.name,
                            "content": script_file.read_text(encoding="utf-8"),
                        })
                    except Exception:
                        pass

        coverage_path = test_path / "coverage"
        if coverage_path.exists():
            for coverage_file in sorted(coverage_path.glob("*.md")):
                try:
                    results["coverage"].append({
                        "name": coverage_file.name,
                        "content": coverage_file.read_text(encoding="utf-8"),
                    })
                except Exception:
                    pass
        return results
    except Exception:
        return results


def zip_test_results(test_folder_path: str, output_path: str) -> str | None:
    """Create a ZIP archive of the tests folder."""
    try:
        test_path = Path(test_folder_path)
        output_file = Path(output_path)
        if not test_path.exists():
            return None
        output_file.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(output_file, "w", zipfile.ZIP_DEFLATED) as zipf:
            for root, _dirs, files in os.walk(test_path):
                for file in files:
                    file_path = Path(root) / file
                    arcname = file_path.relative_to(test_path.parent)
                    zipf.write(file_path, arcname=arcname)
        return str(output_file) if output_file.exists() else None
    except Exception:
        return None


def clear_test_results(test_folder_path: str) -> bool:
    """Delete all generated test artifacts in the target tests folder."""
    try:
        test_path = Path(test_folder_path)
        if not test_path.exists():
            return False
        for subfolder in ["specs", "test_scripts", "coverage"]:
            folder = test_path / subfolder
            if folder.exists() and folder.is_dir():
                for item in folder.iterdir():
                    if item.is_file():
                        item.unlink()
                    elif item.is_dir():
                        shutil.rmtree(item)
        return True
    except Exception:
        return False


def get_folder_size(folder_path: str) -> dict[str, Any]:
    """Get total size and file count for a folder tree."""
    try:
        folder = Path(folder_path)
        if not folder.exists():
            return {"exists": False, "size_bytes": 0, "size_mb": 0.0, "file_count": 0}

        total_size = 0
        file_count = 0
        for file_path in folder.rglob("*"):
            if file_path.is_file():
                total_size += file_path.stat().st_size
                file_count += 1

        return {
            "exists": True,
            "size_bytes": total_size,
            "size_mb": round(total_size / (1024 * 1024), 2),
            "file_count": file_count,
        }
    except Exception:
        return {"exists": False, "size_bytes": 0, "size_mb": 0.0, "file_count": 0}
