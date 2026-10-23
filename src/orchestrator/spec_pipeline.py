"""Source-grounded planning and specification-to-script traceability (Ollama and VIO)."""

import ast
import json
import keyword
import logging
import os
import re
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

logger = logging.getLogger(__name__)

MAX_CONTEXT_TOKENS = 16384
MIN_CONTEXT_TOKENS = 8192
PLAN_MAX_OUTPUT_TOKENS = 4096
PLAN_ATTEMPTS = 2
REPAIR_ROUNDS = 2
RERUN_REPAIR_ROUNDS = 3
RERUN_SOURCE_CHARS = 12000
COMPACT_SOURCE_CHARS = 8000
COMPACT_PLAN_TOKENS = 2048
TRANSIENT_MARKERS = ("HTTP 429", "HTTP 502", "HTTP 503", "HTTP 504", "Timeout", "Connection error")
DIAGNOSIS_CAUSES = ("wrong_expectation", "wrong_input", "setup_or_api_misuse", "environment", "source_bug_suspected")
NO_AUTOFIX_CAUSES = ("environment", "source_bug_suspected")
SCRIPT_SCENARIOS_PER_BATCH = 3
MIN_MEANINGFUL_WEIGHT = 20
COVERAGE_TARGET = 60.0
COVERAGE_ROUNDS = 4
COVERAGE_MAX_BATCH = 15
COVERAGE_FOCUS_CHARS = 3500

SPEC_MARKER_PREFIXES = ("<!-- ollama-spec-plan", "<!-- spec-plan")
SCRIPT_MARKER_PREFIXES = ("# Ollama specification-driven test", "# Specification-driven test")
CATEGORY_ORDER = ("Happy Path", "Negative", "Boundary", "Validation", "Error Handling", "Security", "Integration")
_CATEGORY_KEYS = (
    ("happy", "Happy Path"), ("negative", "Negative"), ("invalid", "Negative"),
    ("boundar", "Boundary"), ("edge", "Boundary"), ("valid", "Validation"),
    ("error", "Error Handling"), ("exception", "Error Handling"),
    ("secur", "Security"), ("integrat", "Integration"),
)
_SPEC_LOCK = threading.Lock()


def generation_options(prompt, max_output_tokens):
    estimated_prompt_tokens = (len(prompt) + 2) // 3
    required_context = estimated_prompt_tokens + max_output_tokens + 1024
    context_tokens = min(
        MAX_CONTEXT_TOKENS,
        max(MIN_CONTEXT_TOKENS, ((required_context + 1023) // 1024) * 1024),
    )
    return {"num_predict": max_output_tokens, "num_ctx": context_tokens}


def log_generation_metrics(metrics, phase):
    logger.info(
        "LLM %s: %s output tokens in %.2fs "
        "(load %.2fs, prompt %.2fs, generation %.2fs)",
        phase,
        metrics.response_tokens,
        metrics.total_time,
        metrics.load_duration / 1_000_000_000,
        metrics.prompt_eval_duration / 1_000_000_000,
        metrics.eval_duration / 1_000_000_000,
    )


def _absolute_import(node, source, package_parts):
    if isinstance(node, ast.ImportFrom) and node.level:
        base = package_parts[:len(package_parts) - (node.level - 1)]
        module = ".".join(base + ([node.module] if node.module else []))
        names = ", ".join(
            alias.name + (f" as {alias.asname}" if alias.asname else "")
            for alias in node.names
        )
        return f"from {module} import {names}"
    return ast.get_source_segment(source, node) or ""


_DEF_TYPES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
_NON_LIBRARY_DIRS = {
    "tests", "test", "test_scripts", "docs", "doc", "examples", "example", "demo", "demos",
    "sample", "samples", "bench", "benchmark", "benchmarks", "fuzz", "scripts", "ci",
    "build", "dist", "node_modules", "site-packages", "venv", "__pycache__",
}
_NON_LIBRARY_FILES = {"setup.py", "conftest.py", "noxfile.py", "fabfile.py", "manage.py", "tasks.py", "__main__.py",
                      "test.py", "tests.py"}

# Character budgets keep every component prompt within MAX_SOURCE_CHARS.
MAX_SOURCE_CHARS = 24000
_IMPORT_BUDGET, _TARGET_BUDGET, _LOCAL_BUDGET, _IMPORTED_BUDGET = 2500, 11000, 5500, 5000


def _absolute_module(node, package_parts):
    base = package_parts[:len(package_parts) - (node.level - 1)] if node.level else []
    return ".".join(base + ([node.module] if node.module else []))


def _absolute_import(node, source, package_parts):
    if isinstance(node, ast.ImportFrom) and node.level:
        names = ", ".join(
            alias.name + (f" as {alias.asname}" if alias.asname else "")
            for alias in node.names
        )
        return f"from {_absolute_module(node, package_parts)} import {names}"
    return ast.get_source_segment(source, node) or ""


def _segment_start(node):
    return min([node.lineno] + [item.lineno for item in getattr(node, "decorator_list", [])])


def _segment(lines, node):
    return "\n".join(lines[_segment_start(node) - 1:node.end_lineno])


def _truncate(text, limit):
    if len(text) <= limit:
        return text
    return text[:limit].rsplit("\n", 1)[0] + "\n# ... (truncated)"


def _stub(lines, function, with_doc=True):
    """Signature (and first docstring line) of a function, body elided."""
    first = function.body[0]
    if first.lineno <= function.lineno:
        return _segment(lines, function)
    signature = "\n".join(lines[_segment_start(function) - 1:first.lineno - 1])
    indent = " " * (function.col_offset + 4)
    doc = (ast.get_docstring(function) or "").strip().splitlines()
    if with_doc and doc:
        return f'{signature}\n{indent}"""{doc[0][:200]}"""'
    return f"{signature}\n{indent}..."


def _condense(lines, node, limit):
    """Source of a definition that fits `limit`, degrading method bodies before dropping them."""
    text = _segment(lines, node)
    if len(text) <= limit or not isinstance(node, ast.ClassDef):
        return _truncate(text, limit)
    start = min(_segment_start(child) for child in node.body)
    header = "\n".join(lines[_segment_start(node) - 1:start - 1]) or lines[node.lineno - 1]
    members = []
    for child in node.body:
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
            members.append((child, _segment(lines, child), _stub(lines, child)))
        else:
            piece = _truncate(_segment(lines, child), 600)
            members.append((None, piece, piece))

    def render(rows):
        return header + "\n" + "\n\n".join(row[2] for row in rows)

    rows = [list(item) for item in members]
    if len(render(rows)) > limit:
        for row in rows:
            if row[0] is not None:
                row[2] = _stub(lines, row[0], with_doc=False)
        return _truncate(render(rows), limit)
    size = len(render(rows))
    order = sorted(
        (i for i, row in enumerate(rows) if row[0] is not None),
        key=lambda i: (rows[i][0].name.startswith("_") and rows[i][0].name != "__init__", i),
    )
    for index in order:
        extra = len(rows[index][1]) - len(rows[index][2])
        if size + extra <= limit:
            rows[index][2] = rows[index][1]
            size += extra
    return render(rows)


def _is_trivial(node):
    statements = [
        item for item in node.body
        if not (isinstance(item, ast.Expr) and isinstance(item.value, ast.Constant))
    ]
    return all(isinstance(item, ast.Pass) for item in statements)


def _loaded_names(node):
    return [
        child.id for child in ast.walk(node)
        if isinstance(child, ast.Name) and isinstance(child.ctx, ast.Load)
    ]


def _bound_names(node):
    return {
        (alias.asname or alias.name).split(".")[0]
        for alias in node.names
    }


def _declared_exports(tree):
    names = set()
    for node in tree.body:
        targets = node.targets if isinstance(node, ast.Assign) else [getattr(node, "target", None)]
        if any(isinstance(target, ast.Name) and target.id == "__all__" for target in targets) \
                and isinstance(getattr(node, "value", None), (ast.List, ast.Tuple)):
            names.update(
                item.value for item in node.value.elts
                if isinstance(item, ast.Constant) and isinstance(item.value, str)
            )
    return names


class _RepoIndex:
    """Resolve names imported from other modules of the same repository."""

    def __init__(self, root):
        self.root = root
        self._parsed = {}

    def parse(self, path):
        if path not in self._parsed:
            try:
                source = path.read_text(encoding="utf-8")
                self._parsed[path] = (source, ast.parse(source))
            except (OSError, UnicodeError, SyntaxError, ValueError):
                self._parsed[path] = None
        return self._parsed[path]

    def find_file(self, module):
        base = self.root.joinpath(*module.split("."))
        for candidate in (base.with_name(base.name + ".py"), base / "__init__.py"):
            if candidate.is_file():
                return candidate
        return None

    def resolve(self, module, name, depth=0):
        """Return (module, definition node, source lines) for `name` defined in or re-exported by `module`."""
        path = self.find_file(module) if module else None
        parsed = self.parse(path) if path else None
        if parsed is None or depth > 3:
            return None
        source, tree = parsed
        lines = source.splitlines()
        for node in tree.body:
            if isinstance(node, _DEF_TYPES) and node.name == name:
                return module, node, lines
        parts = module.split(".")
        package_parts = parts if path.name == "__init__.py" else parts[:-1]
        for node in tree.body:
            if isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    if (alias.asname or alias.name) == name:
                        return self.resolve(_absolute_module(node, package_parts), alias.name, depth + 1)
        return None


def _discover_in_file(path, root, repo, index, exports):
    parsed = index.parse(path)
    if parsed is None:
        return []
    source, tree = parsed
    lines = source.splitlines()
    parts = list(path.relative_to(root).with_suffix("").parts)
    is_package = parts[-1] == "__init__"
    if is_package:
        parts.pop()
    module = ".".join(parts)
    package_parts = parts if is_package else parts[:-1]
    if is_package:
        exports.update(_declared_exports(tree))
        for node in tree.body:
            if isinstance(node, ast.ImportFrom):
                exports.update(alias.asname or alias.name for alias in node.names)
    import_nodes = [node for node in tree.body if isinstance(node, (ast.Import, ast.ImportFrom))]
    imported = {}
    for node in import_nodes:
        if isinstance(node, ast.ImportFrom):
            origin = _absolute_module(node, package_parts)
            for alias in node.names:
                imported[alias.asname or alias.name] = (origin, alias.name)
    definitions = {item.name: item for item in tree.body if isinstance(item, _DEF_TYPES)}
    for item in tree.body:
        if isinstance(item, ast.Assign) and len(item.targets) == 1 and isinstance(item.targets[0], ast.Name):
            definitions.setdefault(item.targets[0].id, item)
    public_names = _declared_exports(tree)
    found = []
    for node in tree.body:
        if not isinstance(node, _DEF_TYPES) or node.name.startswith("_"):
            continue
        if any(_dotted_name(item).split(".")[-1] == "overload" for item in node.decorator_list):
            continue
        if public_names and node.name not in public_names:
            continue
        loaded = set(_loaded_names(node))
        related, visited, pending, used = [], {node.name}, list(loaded), 0
        while pending:
            name = pending.pop()
            definition = definitions.get(name)
            if definition is None or name in visited:
                continue
            if _LOCAL_BUDGET - used < 200:
                break
            visited.add(name)
            piece = _condense(lines, definition, min(2500, _LOCAL_BUDGET - used)) \
                if isinstance(definition, _DEF_TYPES) else _truncate(_segment(lines, definition), 600)
            related.append(piece)
            used += len(piece)
            loaded.update(_loaded_names(definition))
            pending.extend(_loaded_names(definition))
        borrowed, used = [], 0
        for name in sorted(loaded & set(imported) - visited):
            origin, original = imported[name]
            resolved = index.resolve(origin, original)
            if resolved is None or _IMPORTED_BUDGET - used < 200:
                continue
            origin_module, definition, origin_lines = resolved
            piece = f"# --- {origin_module}.{definition.name} ---\n" + _condense(
                origin_lines, definition, min(2500, _IMPORTED_BUDGET - used))
            borrowed.append(piece)
            used += len(piece)
        import_text = "\n".join(_absolute_import(item, source, package_parts) for item in import_nodes)
        if len(import_text) > _IMPORT_BUDGET:
            import_text = "\n".join(
                _absolute_import(item, source, package_parts) for item in import_nodes
                if _bound_names(item) & (loaded | visited)
            )[:_IMPORT_BUDGET]
        text = import_text + "\n\n" + _condense(lines, node, _TARGET_BUDGET)
        if related:
            text += "\n\n# Related local definitions:\n" + "\n\n".join(related)
        if borrowed:
            text += "\n\n# Related definitions imported from other repository modules:\n" + "\n\n".join(borrowed)
        found.append({
            "module": module,
            "symbol": node.name,
            "source_path": path.relative_to(repo).as_posix(),
            "import_root": str(root),
            "source": text,
            "fixture": any(
                _dotted_name(item.func if isinstance(item, ast.Call) else item).split(".")[-1] == "fixture"
                for item in node.decorator_list),
            "_weight": sum(1 for _ in ast.walk(node)),
            "_trivial": _is_trivial(node),
        })
    return found


def _is_library_file(path, root, flat, allow_loose=True):
    parts = path.relative_to(root).parts
    directories, filename = parts[:-1], parts[-1]
    if not directories and not allow_loose and filename != "__init__.py":
        return False
    if any(part in _NON_LIBRARY_DIRS or part.startswith(".") or part.endswith(".egg-info") for part in directories):
        return False
    if filename in _NON_LIBRARY_FILES or filename.startswith("test_") or filename.endswith("_test.py"):
        return False
    stem_parts = list(directories) + ([] if filename == "__init__.py" else [path.stem])
    if not all(part.isidentifier() and not keyword.iskeyword(part) for part in stem_parts):
        return False
    # In a flat layout, directories without __init__.py are scripts, not importable packages.
    return not (flat and directories and not (root / directories[0] / "__init__.py").is_file())


def discover_components(repo_path):
    """Importable public classes/functions of the repository, most valuable first.

    Scripts, benchmarks, tests and non-identifier module names are skipped; components that
    the package re-exports and that contain real logic rank before trivial markers.
    """
    repo = Path(repo_path)
    root = repo / "src" if (repo / "src").is_dir() else repo
    flat = root == repo
    index = _RepoIndex(root)
    exports = set()
    components = []
    # Loose top-level modules are scripts (bench.py, release.py) unless the repo has no package.
    has_package = flat and any(
        child.is_dir() and child.name not in _NON_LIBRARY_DIRS and (child / "__init__.py").is_file()
        for child in root.iterdir()
    )
    for path in sorted(root.rglob("*.py")):
        if _is_library_file(path, root, flat, allow_loose=not has_package):
            components.extend(_discover_in_file(path, root, repo, index, exports))
    def rank(item):
        return (item["_trivial"], -(2000 * (item["symbol"] in exports) + min(item["_weight"], 1500)))
    components.sort(key=rank)
    for item in components:
        item["weight"], item["trivial"] = item.pop("_weight"), item.pop("_trivial")
        item["exported"] = item["symbol"] in exports
    return components


def classify_components(components):
    """Split components into those worth a specification (best first) and the excluded, with reasons."""
    meaningful, excluded = [], []
    for item in components:
        exported, weight = item.get("exported", False), item.get("weight")
        reason = ""
        if item.get("trivial"):
            reason = "marker with no logic of its own"
        elif not exported and any(part.startswith("_") for part in item["module"].split(".")):
            reason = "internal module, not part of the public API"
        elif not exported and weight is not None and weight < MIN_MEANINGFUL_WEIGHT:
            reason = "too little logic to specify on its own"
        if reason:
            excluded.append({"component": item["module"] + "." + item["symbol"], "reason": reason})
        else:
            meaningful.append(item)
    meaningful.sort(key=lambda item: -(2000 * item.get("exported", False) + min(item.get("weight") or 0, 1500)))
    return meaningful, excluded


def spec_slugs(repo_path):
    """Specification names (module_symbol) already written under tests/specs."""
    specs = Path(repo_path) / "tests" / "specs"
    return {path.stem.split("_", 1)[1] for path in specs.glob("*.md") if "_" in path.stem}


def _typed_spec_slugs(repo_path, required_types):
    """Components whose saved scenarios already cover all required test types."""
    required = {str(t) for t in (required_types or []) if t}
    if not required:
        return spec_slugs(repo_path)

    state_path = Path(repo_path) / "tests" / "vio_run_state.json"
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return spec_slugs(repo_path)

    covered_by_component = {}
    fallback_types = {str(t) for t in (state.get("types") or []) if t}
    for item in state.get("results") or []:
        component = str(item.get("component") or "")
        if not component:
            continue
        plan = item.get("plan") or {}
        scenarios = plan.get("scenarios") or []
        found = {
            str(scenario.get("test_type"))
            for scenario in scenarios
            if isinstance(scenario, dict) and scenario.get("test_type")
        }
        if not found and fallback_types:
            found = set(fallback_types)
        if found:
            covered_by_component.setdefault(component, set()).update(found)

    return {
        component.replace(".", "_")
        for component, covered in covered_by_component.items()
        if required.issubset(covered)
    }


def select_components(repo_path, components, resume=False, required_types=None):
    """Queue of meaningful components still to specify; `resume` skips already covered ones."""
    meaningful, excluded = classify_components(components)
    done = _typed_spec_slugs(repo_path, required_types) if resume else set()
    pending = [item for item in meaningful
               if (item["module"] + "." + item["symbol"]).replace(".", "_") not in done]
    return {"pending": pending, "meaningful": len(meaningful),
            "already_specified": len(meaningful) - len(pending), "excluded": excluded}


_VIO_GUIDANCE_START = "<!-- VIO_GUIDANCE_START -->"
_VIO_GUIDANCE_END = "<!-- VIO_GUIDANCE_END -->"
_VIO_GUIDANCE_TYPES = {
    "unit_test": "unit_test",
    "integration_test": "integration_test",
    "e2e_test": "e2e_test",
    "api_test": "e2e_test",
    "security": "security_test",
    "vulnerability": "vulnerability_test",
    "linting": "unit_test",
}


def task_guidance(test_types):
    """Load only compact specialist guidance, excluding incompatible standalone prompt formats."""
    prompt_dir = Path(__file__).resolve().parent.parent / "prompts"
    sections = []
    for test_type in dict.fromkeys(test_types):
        prompt_name = _VIO_GUIDANCE_TYPES.get(test_type)
        if prompt_name is None:
            continue
        path = prompt_dir / f"{prompt_name}.md"
        try:
            content = path.read_text(encoding="utf-8")
        except OSError:
            logger.warning("VIO guidance prompt is unavailable: %s", path)
            continue
        if _VIO_GUIDANCE_START not in content or _VIO_GUIDANCE_END not in content:
            logger.warning("VIO guidance markers are missing from %s", path)
            continue
        guidance = content.split(_VIO_GUIDANCE_START, 1)[1].split(_VIO_GUIDANCE_END, 1)[0].strip()
        if guidance:
            sections.append(f"## {test_type}\n{guidance}")
    return "\n\n".join(sections)


PLAN_SCHEMA = (
    '{"objective":"...","overview":"what the component does",'
    '"business_rules":["rule the source enforces"],"preconditions":["..."],'
    '"assumptions":["..."],"requirements":[{"id":"REQ-1","text":"testable requirement"}],'
    '"test_data":["data needed"],"risk":{"level":"High|Medium|Low","rationale":"..."},'
    '"automation":"feasibility and approach","scenarios":[{"name":"test_descriptive_name",'
    '"category":"Happy Path|Negative|Boundary|Validation|Error Handling|Security|Integration",'
    '"test_type":"one requested test type","requirement_ids":["REQ-1"],'
    '"inputs":"...","expected":"...","evidence":"source statement(s) copied verbatim that decide the expected result",'
    '"reason":"..."}],'
    '"limitations":["missing dependencies or context"]}'
)


FIXTURE_NOTE = (
    "The target is a pytest fixture: calling it directly raises an error. Obtain the plain function with "
    "`fn = getattr(target, '__wrapped__', None) or target._get_wrapped_function()` and call `fn(...)` with "
    "stand-in arguments (for example a MagicMock request). "
)


def _target_notes(component):
    return FIXTURE_NOTE if component.get("fixture") else ""


def plan_prompt(component, test_types, compact=False, include_task_guidance=False):
    guidance = task_guidance(test_types) if include_task_guidance else ""
    return (
        "Act as a senior QA engineer writing a formal test specification for this actual "
        "public component. Base every statement on the source shown; do not invent behavior. "
        "Consider happy paths, invalid input, boundaries, validation, exceptions, state "
        "changes, and security/integration only where applicable. "
        "Plan only inputs the source accepts; for objects the component merely reads "
        "(such as obj.headers or obj.environ) plan simple stand-in objects, and never "
        "assume APIs of classes whose source is not shown; definitions shown under 'Related "
        "definitions' are real and may be relied on. Long classes show some method bodies "
        "reduced to signatures: plan only behavior visible in the shown bodies and docstrings. "
        "If the component is a marker or constant-like class, plan only identity, inheritance and "
        "representation checks that the shown source guarantees. "
        "The number of scenarios must follow the component's complexity, not a fixed quota. "
        "Every expected result must be DERIVED by tracing the shown source for exactly the stated "
        "inputs, never from RFCs, standards, documentation, names or general domain knowledge. "
        "Choose inputs whose outcome you can compute by hand from the shown source; if you cannot "
        "trace the outcome (for example a byte sequence whose table lookups are not shown), do not "
        "plan that scenario. State the expected result as a concrete value, exception type or message "
        "that the source produces. When the source mutates a container (append, appendleft, pop, "
        "insert, clear), trace which end each operation touches before stating order or positions. "
        "Every scenario needs an 'evidence' field: one or two statements copied verbatim from the "
        "shown source that decide the expected result; scenarios without verbatim evidence are discarded. "
        "Write 3-6 testable requirements; every scenario must cite at least one requirement id "
        "and every requirement must be covered by at least one scenario. "
        "Each scenario's test_type must be exactly one of the requested test types. "
        "Keep each text field to one short sentence. "
        + ("COVERAGE TOP-UP: other tests already run most of this component. Plan only scenarios whose "
           "inputs make the listed unexecuted source lines run, with evidence copied from those lines or "
           "their branch conditions; skip lines that need real network, processes or external services. "
           if component.get("coverage_gap") else "") + _target_notes(component) +
        f"Return JSON only (no markdown, no commentary) in this shape: {PLAN_SCHEMA} "
        + ("Keep the plan compact: at most 6 scenarios and 3 requirements, one short sentence per field. "
           if compact else "") +
        "Use unique valid test_ names. Each scenario needs a source-supported expected result.\n"
        f"Requested test types: {', '.join(test_types)}\n"
        + (f"VIO SPECIALIST GUIDANCE:\n{guidance}\n" if guidance else "")
        + f"Public component: {component['module']}.{component['symbol']}\n"
        f"Source file: {component['source_path']}\n{component['source']}"
    )


def _load_json(text):
    try:
        return json.loads(text)
    except (TypeError, ValueError):
        pass
    fenced = re.search(r"```(?:json)?\s*(.*?)```", text or "", re.DOTALL)
    candidate = fenced.group(1) if fenced else (text or "")
    start, end = candidate.find("{"), candidate.rfind("}")
    if start == -1 or end <= start:
        raise ValueError("Planner did not return a JSON object")
    return json.loads(candidate[start:end + 1])


def parse_plan(text):
    plan = _load_json(text)
    if not isinstance(plan, dict):
        raise ValueError("Planner did not return a JSON object")
    scenarios = plan.get("scenarios")
    if not isinstance(scenarios, list) or not scenarios:
        raise ValueError("Planner returned no scenarios")
    names = []
    for scenario in scenarios:
        if not isinstance(scenario, dict):
            raise ValueError("Scenario is not an object")
        name = scenario.get("name", "")
        if not isinstance(name, str) or not name.isidentifier() or not name.startswith("test_"):
            raise ValueError("Invalid scenario test name")
        if not scenario.get("expected") or not scenario.get("inputs"):
            raise ValueError("Scenario lacks inputs or expected behavior")
        names.append(name)
    if len(set(names)) != len(names):
        raise ValueError("Duplicate scenario test names")
    return plan


def _as_list(value):
    if isinstance(value, str):
        return [value] if value.strip() else []
    if isinstance(value, list):
        return [str(item) for item in value if str(item).strip()]
    return []


def _squash(text):
    return re.sub(r"[\s`'\"]+", "", str(text or ""))


def ground_plan(plan, source):
    """Discard scenarios whose evidence is not quoted from the shown source; fail if none remain."""
    haystack = _squash(source)
    kept, dropped = [], []
    for scenario in plan["scenarios"]:
        fragments = [_squash(part) for part in re.split(r"\n|\.\.\.|\u2026", str(scenario.get("evidence") or ""))]
        (kept if any(len(part) >= 6 and part in haystack for part in fragments) else dropped).append(scenario)
    if not kept:
        raise ValueError("No scenario cites evidence copied verbatim from the shown source")
    if not dropped:
        return plan
    cited = {rid for scenario in kept for rid in _as_list(scenario.get("requirement_ids"))}
    requirements = plan.get("requirements") or []
    if all(isinstance(item, dict) and item.get("id") for item in requirements):
        requirements = [item for item in requirements if item["id"] in cited]
    note = ("Scenarios omitted because their expected result was not backed by a statement quoted from "
            "the shown source: " + ", ".join(str(item.get("name")) for item in dropped))
    return {**plan, "scenarios": kept, "requirements": requirements,
            "limitations": [*_as_list(plan.get("limitations")), note]}


def _category(value):
    text = str(value or "").strip()
    lowered = text.lower()
    for key, label in _CATEGORY_KEYS:
        if key in lowered:
            return label
    return text.title() or "Happy Path"


def normalize_plan(plan, test_types):
    """Fill optional spec fields so every spec renders the same complete structure."""
    default_type = test_types[0] if test_types else "unit_test"
    scenarios = []
    for index, raw in enumerate(plan["scenarios"], 1):
        scenario = dict(raw)
        scenario["id"] = f"SC-{index:02d}"
        scenario["category"] = _category(raw.get("category"))
        scenario["test_type"] = raw.get("test_type") if raw.get("test_type") in test_types else default_type
        scenario["requirement_ids"] = _as_list(raw.get("requirement_ids"))
        scenarios.append(scenario)

    requirements = []
    for position, item in enumerate(plan.get("requirements") or [], 1):
        if isinstance(item, dict):
            text, requirement_id = item.get("text") or item.get("requirement"), item.get("id")
        else:
            text, requirement_id = item, None
        if text:
            requirements.append({"id": str(requirement_id or f"REQ-{position}"), "text": str(text)})
    if requirements:
        known = {item["id"] for item in requirements}
        for scenario in scenarios:
            scenario["requirement_ids"] = [rid for rid in scenario["requirement_ids"] if rid in known]
    else:
        requirements = [{"id": f"REQ-{i}", "text": s["expected"]} for i, s in enumerate(scenarios, 1)]
        for index, scenario in enumerate(scenarios, 1):
            scenario["requirement_ids"] = [f"REQ-{index}"]

    risk = plan.get("risk")
    if not isinstance(risk, dict):
        risk = {"level": str(risk or ""), "rationale": ""}
    level = next((name for name in ("High", "Medium", "Low") if name.lower() in str(risk.get("level", "")).lower()), "Medium")

    return {
        "objective": str(plan.get("objective") or ""),
        "overview": str(plan.get("overview") or ""),
        "business_rules": _as_list(plan.get("business_rules")),
        "preconditions": _as_list(plan.get("preconditions")),
        "assumptions": _as_list(plan.get("assumptions")),
        "requirements": requirements,
        "test_data": _as_list(plan.get("test_data")),
        "risk": {"level": level, "rationale": str(risk.get("rationale") or "")},
        "automation": str(plan.get("automation") or ""),
        "scenarios": scenarios,
        "limitations": _as_list(plan.get("limitations")),
    }


def _cell(value):
    return str(value if value not in (None, "") else "-").replace("|", "\\|").replace("\n", " ")


def _bullets(items, empty):
    return [f"- {item}" for item in items] if items else [f"- {empty}"]


def render_spec(component, plan, engine, writer, script_name, number):
    identity = component["module"] + "." + component["symbol"]
    scenarios = plan["scenarios"]
    lines = [
        f"<!-- spec-plan engine={engine} -->",
        f"# Test Specification {number}: {identity}",
        "",
        "| Field | Value |",
        "| --- | --- |",
        f"| Specification ID | SPEC-{number} |",
        f"| Component | `{identity}` |",
        f"| Source file | `{component['source_path']}` |",
        f"| Risk level | {plan['risk']['level']} |",
        f"| Scenarios | {len(scenarios)} |",
        f"| Generated by | {engine}{' / ' + writer if writer else ''} |",
        f"| Test script | `{script_name}` |",
        "",
        "## 1. Objective",
        plan["objective"] or f"Verify the behavior of `{identity}`.",
        "",
        "## 2. Functional Overview",
        plan["overview"] or "Not stated by the planner; see the source component.",
        "",
        "## 3. Business Logic",
        *_bullets(plan["business_rules"], "No explicit business rules were identified in the source."),
        "",
        "## 4. Source Components",
        f"- `{component['source_path']}` - `{identity}`",
        "",
        "## 5. Preconditions",
        *_bullets(plan["preconditions"], "The target package is importable and its dependencies are installed."),
        "",
        "## 6. Assumptions",
        *_bullets(plan["assumptions"], "Behavior is taken only from the shown source."),
        "",
        "## 7. Functional Requirements",
        "",
        "| ID | Requirement |",
        "| --- | --- |",
        *[f"| {_cell(item['id'])} | {_cell(item['text'])} |" for item in plan["requirements"]],
        "",
        "## 8. Test Data Requirements",
        *_bullets(plan["test_data"], "Inline literals and minimal stand-in objects defined in the test."),
        "",
        "## 9. Test Scenarios",
    ]
    categories = [name for name in CATEGORY_ORDER if any(s["category"] == name for s in scenarios)]
    categories += sorted({s["category"] for s in scenarios} - set(CATEGORY_ORDER))
    for number_index, category in enumerate(categories, 1):
        lines.extend([
            "", f"### 9.{number_index} {category}", "",
            "| ID | Test function | Test type | Inputs / preconditions | Expected result | Source evidence | Requirements | Rationale |",
            "| --- | --- | --- | --- | --- | --- | --- | --- |",
        ])
        for scenario in (s for s in scenarios if s["category"] == category):
            evidence = str(scenario.get("evidence") or "").replace("`", "'")
            lines.append(
                f"| {scenario['id']} | `{scenario['name']}` | {_cell(scenario['test_type'])} | "
                f"{_cell(scenario['inputs'])} | {_cell(scenario['expected'])} | "
                f"{_cell('`' + evidence + '`' if evidence else '')} | "
                f"{_cell(', '.join(scenario['requirement_ids']))} | {_cell(scenario.get('reason'))} |"
            )
    lines.extend([
        "", "## 10. Expected Results",
        f"- All {len(scenarios)} scenarios pass with no skipped or xfailed tests.",
        "- Every requirement in section 7 is linked to at least one scenario (section 13).",
        "", "## 11. Risk Assessment",
        f"- **{plan['risk']['level']}**: {plan['risk']['rationale'] or 'No rationale provided.'}",
        "", "## 12. Automation Feasibility",
        plan["automation"] or "Automatable with pytest using the shown source and minimal stand-ins.",
        "", "## 13. Traceability Matrix", "",
        "| Requirement | Description | Scenarios | Test functions |",
        "| --- | --- | --- | --- |",
    ])
    for requirement in plan["requirements"]:
        linked = [s for s in scenarios if requirement["id"] in s["requirement_ids"]]
        lines.append(
            f"| {_cell(requirement['id'])} | {_cell(requirement['text'])} | "
            f"{', '.join(s['id'] for s in linked) or 'NOT COVERED'} | "
            f"{', '.join('`' + s['name'] + '`' for s in linked) or '-'} |"
        )
    unlinked = [s for s in scenarios if not s["requirement_ids"]]
    if unlinked:
        lines.append(f"| (none) | Scenarios without a requirement | {', '.join(s['id'] for s in unlinked)} | "
                     f"{', '.join('`' + s['name'] + '`' for s in unlinked)} |")
    lines.extend(["", "## 14. Limitations", *_bullets(plan["limitations"], "None identified.")])
    return "\n".join(lines) + "\n"


def write_spec(repo_path, component, plan, engine="ollama", writer=""):
    """Write the numbered Markdown specification; the number is reserved under a lock."""
    directory = Path(repo_path) / "tests" / "specs"
    directory.mkdir(parents=True, exist_ok=True)
    identity = component["module"] + "." + component["symbol"]
    slug = identity.replace(".", "_")
    with _SPEC_LOCK:
        files = sorted(directory.glob("*.md"))
        existing = next((path for path in files if path.name.endswith("_" + slug + ".md")), None)
        numbers = [int(path.stem.split("_", 1)[0]) for path in files if path.stem.split("_", 1)[0].isdigit()]
        path = existing or directory / f"{max(numbers, default=0) + 1:03d}_{slug}.md"
        if path.exists() and not path.read_text(encoding="utf-8").startswith(SPEC_MARKER_PREFIXES):
            raise ValueError(f"Preserving existing user specification: {path.name}")
        number = path.stem.split("_", 1)[0]
        path.write_text(
            render_spec(component, plan, engine, writer, f"{path.stem}_test.py", number),
            encoding="utf-8",
        )
    return path


def _failure_reason(output, name):
    match = re.search(rf"_{{2,}} (?:\S+\.)?{re.escape(name)} _{{2,}}\n(.*?)(?=\n_{{3,}} |\n={{3,}} |\Z)", output, re.DOTALL)
    lines = match.group(1).splitlines() if match else []
    return next((line[1:].strip()[:200] for line in lines if line.startswith("E ")), "")


def annotate_spec_corrections(spec_path, names, pytest_output, diagnosis=""):
    """Disclose in the spec which expectations were revised after execution contradicted them."""
    heading = "## 15. Post-execution Corrections"
    rows = [f"| `{name}` | {_cell(_failure_reason(pytest_output, name))} |" for name in sorted(names)]
    path = Path(spec_path)
    text = path.read_text(encoding="utf-8").split("\n" + heading)[0].rstrip("\n")
    cause = f"VIO diagnosis: {diagnosis}\n\n" if diagnosis else ""
    path.write_text(
        text + f"\n\n{heading}\n\nThese tests failed on first execution and were rewritten to match the behavior "
        "the source actually implements. Their original expected results in section 9 may be outdated; "
        "review them before relying on this specification.\n\n" + cause +
        "| Test function | Failure observed on first run |\n| --- | --- |\n" + "\n".join(rows) + "\n",
        encoding="utf-8",
    )


def script_prompt(component, plan, spec_path, include_task_guidance=False):
    scenarios = [
        {key: scenario.get(key) for key in ("id", "name", "category", "inputs", "expected", "evidence", "requirement_ids")}
        for scenario in plan["scenarios"]
    ]
    test_types = list(dict.fromkeys(
        scenario.get("test_type") for scenario in plan["scenarios"] if scenario.get("test_type")
    ))
    guidance = task_guidance(test_types) if include_task_guidance else ""
    return (
        "Implement this specified test plan as runnable pytest Python. "
        "One test function per scenario, with exactly the scenario's name. "
        "Start each test with a one-line docstring '<scenario id>: <expected result>'. "
        "Import the exact target from the specified module and exercise that imported object. "
        "If the target is a class used as a marker or constant, exercise it by reference "
        "(identity, isinstance/issubclass, repr, equality) instead of instantiating it. "
        "Base every expected value on the shown source, including the 'Related definitions' "
        "sections; when an expectation depends on runtime behavior the source does not "
        "reveal (such as type(x) of a metaclass instance), assert only what is derivable. "
        "Never reimplement, copy, subclass, or shadow the target in the test file or reuse "
        "its imported name for a local variable. "
        "Include all imports and required fixtures; use the actual data structures expected "
        "by the source. Initialize every field the source reads or writes on each object "
        "before invoking the target; if it accesses obj.headers, every relevant stub must "
        "provide a dict-like headers attribute, including negative and cache-path tests. "
        "Every test must assert behavior or use pytest.raises. "
        "For objects the component merely reads or writes (such as obj.headers or "
        "obj.environ), use minimal stand-ins only when the source treats them structurally. "
        "If the source checks isinstance, calls a related type's methods, or compares its "
        "properties, use that real type from SOURCE instead of SimpleNamespace. "
        "Never pass an unhashable stand-in to a set-membership operation. "
        "Every stand-in object (SimpleNamespace, Mock or small class) must define each attribute the source "
        "reads from it, including nested ones such as options.some_flag: trace every `obj.attr` access on the "
        "executed path; when many attributes are read, use unittest.mock.MagicMock and set the ones whose "
        "value matters. Call functions and constructors only with the parameters shown in their signatures. "
        + _target_notes(component) +
        "Only use another class's API if its definition is shown in SOURCE. "
        "Keep every test independent and self-contained; do not create module constants, "
        "fixtures, helper functions, or helper classes. Include only imports and the "
        "specified test functions at module scope. "
        "Establish every specified precondition inside each test that depends on it: set module-level state, "
        "registries and caches in the test itself with monkeypatch.setitem, monkeypatch.setattr or "
        "unittest.mock.patch.dict (restored automatically) and never rely on a plugin or another test having "
        "initialised them. Check every expected exception with pytest.raises. "
        "Do not skip, xfail, or leave placeholder/pass tests. Do not assert True, and never write a "
        "conditional or `or True` assertion: every assertion must be able to fail. "
        "monkeypatch.setattr takes a module or object; use monkeypatch.setitem for a dict such as __globals__. "
        "If a package re-exports an object named like its own submodule, import pkg.mod returns that object: "
        "read the module from sys.modules['pkg.mod'] instead. "
        "Do not assert absence of side effects unless the scenario names an observable check; "
        "for a no-callback path, use a spy on the callback factory or inspect the returned "
        "object, not hasattr on a class that may inherit class attributes. "
        "The imported target is a class or function, not its module: call it directly and "
        "never write target.target or alias it as a module. "
        "Do not invent behavior that is not supported by the source or plan. "
        "Return one complete closed python code fence.\n"
        f"Specification: {spec_path.name}\n"
        f"Public component: {component['module']}.{component['symbol']}\n"
        f"Objective: {plan['objective']}\n"
        f"Preconditions: {'; '.join(plan['preconditions']) or 'none'}\n"
        + (f"State to set up inside EVERY test: {', '.join(_precondition_state(plan, component))}\n"
           if _precondition_state(plan, component) else "")
        + f"SCENARIOS:\n{json.dumps(scenarios)}\nSOURCE:\n{component['source']}\n"
        + (f"VIO SPECIALIST GUIDANCE:\n{guidance}\n" if guidance else "")
    )


def _scenario_batches(plan, batch_size=SCRIPT_SCENARIOS_PER_BATCH):
    scenarios = plan["scenarios"]
    return [scenarios[index:index + batch_size] for index in range(0, len(scenarios), batch_size)]


def _subplan(plan, scenarios):
    linked_ids = {requirement_id for scenario in scenarios for requirement_id in scenario["requirement_ids"]}
    return {
        **plan,
        "requirements": [item for item in plan["requirements"] if item["id"] in linked_ids],
        "scenarios": scenarios,
    }


def merge_test_chunks(chunks, replace_names=()):
    imports = {}
    helpers = {}
    tests = {}
    replace_names = set(replace_names)
    for chunk in chunks:
        tree = ast.parse(chunk)
        for node in tree.body:
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                imports.setdefault(ast.dump(node, include_attributes=False), ast.unparse(node))
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test_"):
                if node.name in tests and node.name not in replace_names:
                    raise ValueError(f"Duplicate test function across generated chunks: {node.name}")
                tests[node.name] = ast.unparse(node)
            elif isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                key = (type(node).__name__, node.name)
                rendered = ast.unparse(node)
                previous = helpers.get(key)
                if previous is not None and ast.dump(previous[0], include_attributes=False) != ast.dump(node, include_attributes=False):
                    raise ValueError(f"Conflicting generated helper definition across chunks: {node.name}")
                helpers.setdefault(key, (node, rendered))
            elif isinstance(node, (ast.Assign, ast.AnnAssign)):
                names = [target.id for target in ast.walk(node) if isinstance(target, ast.Name)
                         and isinstance(target.ctx, ast.Store)]
                if not names:
                    raise ValueError("Generated module assignment has no simple name")
                for name in names:
                    key = ("assignment", name)
                    rendered = ast.unparse(node)
                    previous = helpers.get(key)
                    if previous is not None and ast.dump(previous[0], include_attributes=False) != ast.dump(node, include_attributes=False):
                        raise ValueError(f"Conflicting generated module value across chunks: {name}")
                    helpers.setdefault(key, (node, rendered))
            elif isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
                continue
            elif isinstance(node, ast.If):
                # Ignore common model-added runner guards; they are not needed for pytest modules.
                test = node.test
                is_main_guard = (
                    isinstance(test, ast.Compare)
                    and isinstance(test.left, ast.Name)
                    and test.left.id == "__name__"
                    and len(test.ops) == 1
                    and isinstance(test.ops[0], ast.Eq)
                    and len(test.comparators) == 1
                    and isinstance(test.comparators[0], ast.Constant)
                    and test.comparators[0].value == "__main__"
                )
                if is_main_guard:
                    continue
                raise ValueError(
                    f"Generated chunk has unsupported module-level code: {type(node).__name__}"
                )
            else:
                raise ValueError(
                    f"Generated chunk has unsupported module-level code: {type(node).__name__}"
                )
    code = "\n\n".join([
        *imports.values(),
        *(value[1] for value in helpers.values()),
        *tests.values(),
    ]) + "\n"
    tree = ast.parse(code)
    loaded_names = {
        node.id for node in ast.walk(tree)
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load)
    }
    cleaned = []
    for node in tree.body:
        if not isinstance(node, ast.ImportFrom) or node.module == "__future__" or any(alias.name == "*" for alias in node.names):
            cleaned.append(node)
            continue
        aliases = [alias for alias in node.names if (alias.asname or alias.name) in loaded_names]
        if aliases:
            node.names = aliases
            cleaned.append(node)
    return "\n\n".join(ast.unparse(node) for node in cleaned) + "\n"


def remove_unplanned_tests(code, plan):
    tree = ast.parse(code)
    expected = {scenario["name"] for scenario in plan["scenarios"]}
    actual = {
        node.name for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test_")
    }
    if expected <= actual and actual - expected:
        tree.body = [
            node for node in tree.body
            if not (isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                    and node.name.startswith("test_") and node.name not in expected)
        ]
        return ast.unparse(tree) + "\n"
    return code


def generate_chunked_script(client, model, component, plan, spec_path, extract_code,
                            attempts=None, repair_context="", include_task_guidance=False):
    chunks = []
    prompts = []
    for scenarios in _scenario_batches(plan):
        batch_plan = _subplan(plan, scenarios)
        prompt = script_prompt(component, batch_plan, spec_path, include_task_guidance)
        if repair_context:
            prompt += (
                "\nThese planned scenarios failed under pytest. Keep the same test names. The pytest "
                "evidence shows what the code actually did: trace the shown source for the exact input. "
                "If the scenario's expected result contradicts what the source does, the expectation "
                "was wrong, so assert the behavior the source actually implements for that input; if "
                "the input cannot be traced, replace it with one whose outcome you can compute from "
                "the source. If the failure is a setup or API-usage error, fix the setup. Never weaken an "
                "assertion to isinstance, len, or truthiness checks and never skip or xfail.\n"
                f"PYTEST FAILURE EVIDENCE:\n{repair_context[-7000:]}"
            )
        prompts.append(prompt)
        output_limit = min(3072, max(1536, len(scenarios) * 768))
        chunks.append(generate_aligned_code(
            client, model, prompt, extract_code,
            generation_options(prompt, output_limit), batch_plan, component, attempts,
        ))
    code = merge_test_chunks(chunks)
    check_alignment(code, plan, component)
    return code, len(chunks), prompts


def _record_generation_attempt(attempts, model, prompt, response):
    if attempts is not None:
        attempts.append({"model": model, "prompt": prompt, "response": response})


def _fenced_text(text):
    longest = max((len(match.group(0)) for match in re.finditer(r"`+", text)), default=2)
    fence = "`" * max(3, longest + 1)
    return f"{fence}text\n{text}\n{fence}"


def _save_generation_failure(repo, component, result, stage, error, attempts):
    directory = Path(repo) / "tests" / "orchestration_results"
    directory.mkdir(parents=True, exist_ok=True)
    identity = component["module"] + "." + component["symbol"]
    stem = Path(result["spec"]).stem if result.get("spec") else identity.replace(".", "_")
    path = directory / f"{stem}_generation_failure.md"
    lines = [
        f"# Generation failure: {identity}", "",
        f"- Stage: **{stage}**", f"- Error: `{error}`",
        f"- Specification: `{Path(result['spec']).name if result.get('spec') else 'not created'}`",
        f"- Script: `{Path(result['script']).name if result.get('script') else 'not created'}`",
        f"- Attempts captured: {len(attempts)}", "",
    ]
    for index, attempt in enumerate(attempts, 1):
        lines.extend([
            f"## Attempt {index} ({attempt['model']})", "", "### Prompt", "",
            _fenced_text(attempt["prompt"]), "", "### Raw model response", "",
            _fenced_text(attempt["response"]), "",
        ])
    path.write_text("\n".join(lines), encoding="utf-8")
    return str(path)


def _dotted_name(node):
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        parent = _dotted_name(node.value)
        return f"{parent}.{node.attr}" if parent else node.attr
    return ""


def _is_weak_assertion(expression):
    """True for checks that hold for almost any result: a bare variable or `len(x) > 0`."""
    if isinstance(expression, ast.Name):
        return True
    if isinstance(expression, ast.Compare) and len(expression.ops) == 1:
        left, operator, right = expression.left, expression.ops[0], expression.comparators[0]
        return (isinstance(left, ast.Call) and _dotted_name(left.func) == "len"
                and isinstance(right, ast.Constant)
                and ((isinstance(operator, (ast.Gt, ast.NotEq)) and right.value == 0)
                     or (isinstance(operator, ast.GtE) and right.value == 1)))
    return False


def _only_asserts_not_none(function):
    assertions = [node.test for node in ast.walk(function) if isinstance(node, ast.Assert)]
    return bool(assertions) and all(
        isinstance(test, ast.Compare) and len(test.ops) == 1 and isinstance(test.ops[0], ast.IsNot)
        and isinstance(test.comparators[0], ast.Constant) and test.comparators[0].value is None
        for test in assertions
    )


def _target_signature(component):
    """(arguments, leading parameters to skip) when the target's plain signature is visible in its source."""
    try:
        tree = ast.parse(component.get("source", ""))
    except (SyntaxError, ValueError):
        return None
    for node in tree.body:
        if getattr(node, "name", None) != component["symbol"]:
            continue
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return None if node.decorator_list else (node.args, 0)
        if isinstance(node, ast.ClassDef) and not node.decorator_list and not node.keywords:
            init = next((item for item in node.body
                         if isinstance(item, ast.FunctionDef) and item.name == "__init__"), None)
            return (init.args, 1) if init is not None and not init.decorator_list else None
        return None
    return None


def _signature_problem(function, signature, is_target, symbol):
    """Describe the first call of the target inside `function` that cannot match its signature."""
    args, skip = signature
    positional = [item.arg for item in (*args.posonlyargs, *args.args)][skip:]
    required = positional[:len(positional) - min(len(args.defaults), len(positional))]
    keywords = {item.arg for item in args.kwonlyargs} | (set(positional) - {item.arg for item in args.posonlyargs})
    required_keyword_only = {item.arg for item, default in zip(args.kwonlyargs, args.kw_defaults) if default is None}
    for call in (node for node in ast.walk(function) if isinstance(node, ast.Call) and is_target(node.func)):
        if any(isinstance(item, ast.Starred) for item in call.args) or any(k.arg is None for k in call.keywords):
            continue
        given = {keyword.arg for keyword in call.keywords}
        if len(call.args) > len(positional) and not args.vararg:
            return f"it passes {len(call.args)} positional arguments but {symbol} accepts {len(positional)}"
        unknown = given - keywords
        if unknown and not args.kwarg:
            return f"it passes keyword argument(s) {sorted(unknown)} that {symbol} does not accept"
        supplied = set(positional[:len(call.args)]) | given
        missing = [name for name in required if name not in supplied] + sorted(required_keyword_only - given)
        if missing:
            return f"it omits required argument(s) {missing} of {symbol}"
    return ""


def _mentions(function):
    """Names, attributes and string literals used inside a function."""
    names, strings = set(), []
    for node in ast.walk(function):
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            strings.append(node.value)
    return names, strings


_UNPOPULATED = re.compile(r"\b(?:empty|cleared|reset|unset|absent|missing)\b", re.I)


def _precondition_state(plan, component):
    """Private module-level state (`_name`) that preconditions say must be populated and the source reads."""
    source = component.get("source", "")
    found = {}
    for sentence in plan.get("preconditions") or []:
        if _UNPOPULATED.search(str(sentence)):
            continue
        for name in re.findall(r"(?<![A-Za-z0-9_])_[A-Za-z][A-Za-z0-9_]*", str(sentence)):
            if name in source and not re.search(rf"(?<!\w){name}\s*\(", source):
                found[name] = None
    return list(found)


_EXCEPTION_NAME = re.compile(r"\b([A-Z][A-Za-z0-9]*(?:Error|Exception))\b")
_NEGATED = re.compile(r"(?:\b(?:no|not|never|without|instead of|rather than)\b|n't\b)[^;.,]{0,24}$", re.I)


def _expected_exceptions(expected):
    expected = str(expected or "")
    found = []
    for match in _EXCEPTION_NAME.finditer(expected):
        if match.group(1) not in found and not _NEGATED.search(expected[max(0, match.start() - 40):match.start()]):
            found.append(match.group(1))
    return found


def semantic_gaps(code, plan, component, advisory=False):
    """'<test>: <finding>' for tests that skip a specified precondition or never check the expected exception.

    With `advisory`, tests whose only assertions are `is not None` are listed as well.
    """
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return []
    scenarios = {scenario["name"]: scenario for scenario in plan["scenarios"]}
    state = _precondition_state(plan, component)
    findings = []
    for function in (node for node in ast.walk(tree)
                     if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in scenarios):
        names, strings = _mentions(function)
        findings += [f"{function.name}: does not set up the precondition state {name}" for name in state
                     if name not in names and not any(name in text for text in strings)]
        findings += [f"{function.name}: never checks the expected {error}"
                     for error in _expected_exceptions(scenarios[function.name].get("expected"))
                     if error not in names and not any(error in text for text in strings)]
        if advisory and _only_asserts_not_none(function):
            findings.append(f"{function.name}: only asserts the result is not None, not its expected value")
    return findings


def _declared_tests(tree):
    """Test functions pytest collects: module-level ones and class members, not helpers nested in a test."""
    found, pending = [], list(tree.body)
    for node in pending:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name.startswith("test_"):
                found.append(node)
        elif isinstance(node, ast.ClassDef):
            pending.extend(node.body)
    return found


def _is_vacuous_assertion(expression):
    """True for `x if cond else True` and `x or True`, which can pass without checking anything."""
    def always_true(node):
        return isinstance(node, ast.Constant) and bool(node.value)
    if isinstance(expression, ast.IfExp):
        return always_true(expression.body) or always_true(expression.orelse)
    if isinstance(expression, ast.BoolOp) and isinstance(expression.op, ast.Or):
        return any(always_true(value) for value in expression.values)
    return False


def _patches_a_mapping(call):
    """True for setattr on a dict such as `f.__globals__`, `obj.__dict__` or `globals()`; it needs setitem."""
    if _dotted_name(call.func).split(".")[-1] != "setattr" or not call.args:
        return False
    target = call.args[0]
    if isinstance(target, ast.Attribute):
        return target.attr in {"__globals__", "__dict__"}
    return isinstance(target, ast.Call) and _dotted_name(target.func) in {"globals", "vars"}


def _module_shadowed(component):
    """True when the parent package re-exports a name equal to the module's own name."""
    parts = str(component.get("module", "")).split(".")
    root = component.get("import_root")
    if len(parts) < 2 or not root:
        return False
    try:
        tree = ast.parse(Path(root).joinpath(*parts[:-1], "__init__.py").read_text(encoding="utf-8"))
    except (OSError, UnicodeError, SyntaxError, ValueError):
        return False
    return any(isinstance(node, ast.ImportFrom) and any((item.asname or item.name) == parts[-1] for item in node.names)
               for node in tree.body)


def _shadowed_module_use(tree, module):
    """Whether the test reaches the shadowed module through `pkg.mod`, `import pkg.mod as m` or `from pkg import mod`."""
    parent, _, name = module.rpartition(".")
    aliases = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            aliases.update(item.asname for item in node.names if item.name == module and item.asname)
        elif isinstance(node, ast.ImportFrom) and node.module == parent:
            aliases.update(item.asname or item.name for item in node.names if item.name == name)
    return any(
        (isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load) and node.id in aliases)
        or (isinstance(node, ast.Attribute) and _dotted_name(node) == module)
        for node in ast.walk(tree)
    )


def check_alignment(code, plan, component):
    tree = ast.parse(code)
    if any(isinstance(node, ast.Assert) and isinstance(node.test, ast.Constant)
           for node in ast.walk(tree)):
        raise ValueError("Placeholder assertion detected; assert actual repository behavior")

    symbol = component["symbol"]
    module = component["module"]
    # The defining module or any parent package that re-exports the target is a valid import path.
    module_parts = module.split(".")
    import_paths = {".".join(module_parts[:end]) for end in range(1, len(module_parts) + 1)}
    imported_names = {
        alias.asname or alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module in import_paths
        for alias in node.names
        if alias.name == symbol
    }
    module_aliases = {
        alias.asname or alias.name
        for node in ast.walk(tree) if isinstance(node, ast.Import)
        for alias in node.names if alias.name in import_paths
    }
    module_attribute_uses = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Attribute) and node.attr == symbol
        and _dotted_name(node.value) in module_aliases
    ]
    if not imported_names and not module_attribute_uses:
        raise ValueError(f"Test script must import {symbol} from {module}")

    for node in ast.walk(tree):
        if (isinstance(node, ast.Attribute) and node.attr == symbol
                and isinstance(node.value, ast.Name) and node.value.id in imported_names):
            raise ValueError(f"Imported target {symbol} is a class/function, not a module; call it directly")

    if _module_shadowed(component) and _shadowed_module_use(tree, module):
        raise ValueError(
            f"{module} is shadowed by an object of the same name that its package re-exports, so "
            f"`import {module} as m` or `{module}.attr` returns that object, not the module. "
            f"Get the module with sys.modules['{module}'] after importing the target, or importlib.import_module"
        )

    if any(
        isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == symbol
        for node in ast.walk(tree)
    ):
        raise ValueError(f"Test script must not redefine target component {symbol}")

    if any(
        isinstance(node, ast.Name)
        and node.id in imported_names
        and isinstance(node.ctx, (ast.Store, ast.Del))
        for node in ast.walk(tree)
    ):
        raise ValueError(f"Test script must not shadow imported target {symbol}")

    # Sentinels, constants-like classes and exceptions are exercised by reference, not only by calls.
    if not module_attribute_uses and not any(
        isinstance(node, ast.Name) and node.id in imported_names and isinstance(node.ctx, ast.Load)
        for node in ast.walk(tree)
    ):
        raise ValueError(f"Test script imports {symbol} but never uses it")

    if component.get("fixture") and any(
        isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in imported_names
        for node in ast.walk(tree)
    ):
        raise ValueError(f"{symbol} is a pytest fixture and must not be called directly. {FIXTURE_NOTE}")

    forbidden_markers = (".skip", ".skipif", ".xfail")
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and any(
            _dotted_name(node.func).endswith(marker)
            for marker in forbidden_markers
        ):
            raise ValueError("Skipped or xfailed tests are not accepted as generated coverage")

    actual = [node.name for node in _declared_tests(tree)]
    expected = {scenario["name"] for scenario in plan["scenarios"]}
    if len(actual) != len(set(actual)) or set(actual) != expected:
        raise ValueError(f"Spec/script mismatch: missing={sorted(expected - set(actual))}, extra={sorted(set(actual) - expected)}")

    signature = _target_signature(component)

    for test_function in _declared_tests(tree):
        has_assertion = any(isinstance(node, ast.Assert) for node in ast.walk(test_function))
        has_exception_assertion = any(
            isinstance(node, ast.Call)
            and _dotted_name(node.func) in {"pytest.raises", "self.assertRaises"}
            for node in ast.walk(test_function)
        )
        if not has_assertion and not has_exception_assertion:
            raise ValueError(f"Test {test_function.name} has no behavioral assertion")
        asserted = [node.test for node in ast.walk(test_function) if isinstance(node, ast.Assert)]
        if asserted and not has_exception_assertion and all(_is_weak_assertion(test) for test in asserted):
            raise ValueError(
                f"Test {test_function.name} only asserts truthiness or a non-empty length; "
                "assert the concrete expected value from the specification"
            )
        if any(_is_vacuous_assertion(test) for test in asserted):
            raise ValueError(
                f"Test {test_function.name} has an assertion that can pass without checking behavior "
                "(`... if ... else True` or `... or True`); assert the expected value unconditionally"
            )
        if any(isinstance(node, ast.Call) and _patches_a_mapping(node) for node in ast.walk(test_function)):
            raise ValueError(
                f"Test {test_function.name} passes a dict (__globals__, __dict__, globals()) to setattr; "
                "use monkeypatch.setitem(mapping, key, value) or unittest.mock.patch.dict"
            )
        names, strings = _mentions(test_function)
        if not (names & imported_names or any(
                isinstance(node, ast.Attribute) and node.attr == symbol and _dotted_name(node.value) in module_aliases
                for node in ast.walk(test_function)) or any(symbol in text for text in strings)):
            raise ValueError(f"Test {test_function.name} never exercises {symbol}; call or inspect the imported target")
        if signature and "TypeError" not in names:
            problem = _signature_problem(
                test_function, signature,
                lambda func: (isinstance(func, ast.Name) and func.id in imported_names)
                or (isinstance(func, ast.Attribute) and func.attr == symbol
                    and _dotted_name(func.value) in module_aliases),
                symbol)
            if problem:
                raise ValueError(f"Test {test_function.name} calls {symbol} with a call its signature rejects: {problem}")
        for assertion in (node for node in ast.walk(test_function) if isinstance(node, ast.Assert)):
            expression = assertion.test
            if not (isinstance(expression, ast.UnaryOp) and isinstance(expression.op, ast.Not)
                    and isinstance(expression.operand, ast.Call)
                    and isinstance(expression.operand.func, ast.Name)
                    and expression.operand.func.id == "hasattr"
                    and len(expression.operand.args) == 2
                    and isinstance(expression.operand.args[0], ast.Name)
                    and isinstance(expression.operand.args[1], ast.Constant)
                    and isinstance(expression.operand.args[1].value, str)):
                continue
            fixture_name = expression.operand.args[0].id
            attribute = expression.operand.args[1].value
            for fixture in (node for node in ast.walk(tree)
                            if isinstance(node, ast.ClassDef) and node.name == fixture_name):
                if any(isinstance(child, (ast.Assign, ast.AnnAssign))
                       and any(isinstance(target, ast.Name) and target.id == attribute
                               for target in ast.walk(child))
                       for child in fixture.body):
                    raise ValueError(
                        f"Test {test_function.name} contradicts fixture {fixture_name}: "
                        f"it defines {attribute!r} then asserts hasattr is false"
                    )


def generate_valid_code(client, model, prompt, extract_code, options, attempts=None):
    """Generate Python and retry up to two times if the model returns invalid syntax."""
    current_prompt = prompt
    attempts_left = 2
    phase = "test generation"
    while True:
        output, metrics = client.generate(model, current_prompt, options=options)
        _record_generation_attempt(attempts, model, current_prompt, output)
        log_generation_metrics(metrics, phase)
        code = extract_code(output)
        try:
            ast.parse(code)
            return code
        except SyntaxError as syntax_error:
            if attempts_left == 0:
                raise ValueError(
                    "Model returned invalid Python after syntax-repair attempts: "
                    f"{syntax_error.msg} at line {syntax_error.lineno}"
                ) from syntax_error
            current_prompt = (
                prompt
                + "\nThe previous response was not valid Python. Return a complete corrected "
                "pytest file. Do not assign to a function call; use a variable assignment "
                "or an equality assertion as appropriate.\n"
                f"Parser error: {syntax_error.msg} at line {syntax_error.lineno}.\n"
                f"INVALID PYTHON:\n{code}"
            )
            attempts_left -= 1
            phase = "syntax repair"


def generate_aligned_code(client, model, prompt, extract_code, options, plan, component, attempts=None):
    """Aligned code, regenerated once more when tests omit specified preconditions or expected-exception checks."""
    code = _aligned_code(client, model, prompt, extract_code, options, plan, component, attempts)
    gaps = semantic_gaps(code, plan, component)
    if not gaps:
        return code
    retry_prompt = (
        prompt
        + "\nThe previous test file leaves specified preconditions or checks out of some tests. Fix every "
        "finding, keep every test name and keep the rest unchanged. Establish module-level state inside the test "
        "itself (monkeypatch.setitem, monkeypatch.setattr or unittest.mock.patch.dict, which restore it "
        "afterwards) and assert each expected exception with pytest.raises.\n"
        "FINDINGS:\n- " + "\n- ".join(gaps[:12]) + f"\nPREVIOUS TEST FILE:\n{code}"
    )
    try:
        improved = remove_unplanned_tests(
            generate_valid_code(client, model, retry_prompt, extract_code, options, attempts), plan)
        check_alignment(improved, plan, component)
    except (ValueError, RuntimeError):
        return code
    return improved if len(semantic_gaps(improved, plan, component)) < len(gaps) else code


def _aligned_code(client, model, prompt, extract_code, options, plan, component, attempts=None):
    """Regenerate once when output is syntactically valid but not a real test suite."""
    code = generate_valid_code(client, model, prompt, extract_code, options, attempts)
    code = remove_unplanned_tests(code, plan)
    try:
        check_alignment(code, plan, component)
    except ValueError as validation_error:
        actual_names = [node.name for node in _declared_tests(ast.parse(code))]
        expected_names = {scenario["name"] for scenario in plan["scenarios"]}
        missing = [scenario for scenario in plan["scenarios"] if scenario["name"] not in actual_names]
        if missing and not (set(actual_names) - expected_names) and len(actual_names) == len(set(actual_names)):
            missing_plan = _subplan(plan, missing)
            missing_prompt = (
                prompt
                + "\nThe previous output omitted planned scenarios. Generate ONLY these missing "
                "test functions, with the exact names shown. Do not repeat any already implemented test. "
                "Include required imports and keep each function self-contained.\n"
                f"ALREADY IMPLEMENTED: {json.dumps(actual_names)}\n"
                f"MISSING SCENARIOS: {json.dumps(missing)}\n"
                f"INITIAL OUTPUT:\n{code}"
            )
            missing_code = generate_valid_code(
                client, model, missing_prompt, extract_code, options, attempts,
            )
            check_alignment(missing_code, missing_plan, component)
            code = merge_test_chunks([code, missing_code])
            check_alignment(code, plan, component)
            return code
        repair_prompt = (
            prompt
            + "\nThe previous test file failed required quality checks. Correct it while "
            "preserving every planned test name. Import and exercise the real target; "
            "do not skip, xfail, redefine, or copy it. Every test needs an assertion or "
            "pytest.raises.\nValidation error: "
            f"{validation_error}\nINVALID TEST FILE:\n{code}"
        )
        code = generate_valid_code(client, model, repair_prompt, extract_code, options, attempts)
        code = remove_unplanned_tests(code, plan)
        try:
            check_alignment(code, plan, component)
        except ValueError as second_error:
            targeted_hint = ""
            text = str(second_error)
            if "no behavioral assertion" in text:
                targeted_hint = (
                    "For every test, add at least one behavioral assertion about return value, raised exception, "
                    "or observable state mutation. Avoid placeholder assertions like assert True.\n"
                )
            elif "never exercises" in text:
                targeted_hint = (
                    "Each test must call or directly inspect the imported target symbol under test; do not only "
                    "set up fixtures without exercising the target.\n"
                )
            second_repair_prompt = (
                prompt
                + "\nThe previous correction still failed quality checks. Produce a final corrected test file "
                "that keeps all planned test names and satisfies strict alignment checks.\n"
                + targeted_hint
                + f"Validation error: {second_error}\nPREVIOUS TEST FILE:\n{code}"
            )
            code = generate_valid_code(client, model, second_repair_prompt, extract_code, options, attempts)
            code = remove_unplanned_tests(code, plan)
            check_alignment(code, plan, component)
    return code


def _test_sources(code, names):
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return ""
    return "\n\n".join(
        ast.unparse(node) for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in names
    )


def import_roots(repo_path):
    """Directories that must be on sys.path so the repository's own packages import from source."""
    repo = Path(repo_path).resolve()
    src = repo / "src"
    return [str(src), str(repo)] if src.is_dir() else [str(repo)]


def write_test_support(repo_path):
    """Write conftest.py (import paths) and pytest.ini (isolation from the repo's own pytest config)."""
    scripts = Path(repo_path) / "tests" / "test_scripts"
    scripts.mkdir(parents=True, exist_ok=True)
    (scripts / "conftest.py").write_text(
        "import sys\n"
        f"for _root in reversed({import_roots(repo_path)!r}):\n"
        "    if _root not in sys.path:\n"
        "        sys.path.insert(0, _root)\n",
        encoding="utf-8",
    )
    # The target repo's own addopts/filterwarnings/plugins must not alter generated-test runs.
    (scripts / "pytest.ini").write_text(
        "[pytest]\naddopts =\nfilterwarnings =\n    ignore::DeprecationWarning\n"
        "    ignore::PendingDeprecationWarning\n",
        encoding="utf-8",
    )
    return scripts


def coverage_options(components, json_path):
    """pytest-cov arguments that measure the repository packages behind `components`."""
    packages = sorted({item["module"].split(".")[0] for item in components})
    return [*(f"--cov={name}" for name in packages), "--cov-branch", f"--cov-report=json:{json_path}"]


def coverage_env(repo_path):
    """Environment that resolves --cov package names to the repository source, not an installed copy."""
    env = dict(os.environ)
    roots = import_roots(repo_path)
    env["PYTHONPATH"] = os.pathsep.join([*roots, *filter(None, [env.get("PYTHONPATH")])])
    return env


def repo_python(repo_path):
    """Interpreter for repository-bound test operations; prefer repo .venv, else current Python."""
    repo = Path(repo_path)
    candidates = (
        repo / ".venv" / "Scripts" / "python.exe",  # Windows
        repo / ".venv" / "bin" / "python",          # POSIX
    )
    for candidate in candidates:
        if candidate.exists():
            return str(candidate)
    return sys.executable


def _symbol_span(path, symbol):
    try:
        tree = ast.parse(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, SyntaxError, ValueError):
        return None
    for node in tree.body:
        if isinstance(node, _DEF_TYPES) and node.name == symbol:
            return _segment_start(node), node.end_lineno
    return None


def _line_ranges(lines):
    ranges, start, previous = [], None, None
    for line in sorted(lines):
        if start is None:
            start = previous = line
        elif line == previous + 1:
            previous = line
        else:
            ranges.append((start, previous))
            start = previous = line
    if start is not None:
        ranges.append((start, previous))
    return ", ".join(str(a) if a == b else f"{a}-{b}" for a, b in ranges)


def summarize_coverage(repo_path, components, json_path, base):
    """Per-component line+branch coverage from a coverage JSON report (paths relative to `base`)."""
    data = json.loads(Path(json_path).read_text(encoding="utf-8"))
    repo = Path(repo_path).resolve()
    measured = {(Path(base) / name).resolve(): entry for name, entry in data["files"].items()}
    specified = spec_slugs(repo)
    rows = []
    for item in components:
        identity = item["module"] + "." + item["symbol"]
        source = (repo / item["source_path"]).resolve()
        entry, span = measured.get(source), _symbol_span(source, item["symbol"])
        row = {"component": identity, "specified": identity.replace(".", "_") in specified,
               "percent": None, "uncovered_lines": "", "missing": 0, "missing_lines": []}
        if entry is not None and span is not None:
            first, last = span
            executed = sum(first <= line <= last for line in entry["executed_lines"])
            missing = [line for line in entry["missing_lines"] if first <= line <= last]
            branches_run = sum(first <= edge[0] <= last for edge in entry.get("executed_branches", []))
            branches_missed = sum(first <= edge[0] <= last for edge in entry.get("missing_branches", []))
            total = executed + len(missing) + branches_run + branches_missed
            row["percent"] = round(100 * (executed + branches_run) / total, 1) if total else 100.0
            row["uncovered_lines"] = _line_ranges(missing)
            row["missing"], row["missing_lines"] = len(missing) + branches_missed, sorted(missing)
        rows.append(row)
    rows.sort(key=lambda row: (row["percent"] is None, row["percent"] or 0.0, row["component"]))
    totals = data["totals"]
    return {
        "percent": round(totals["percent_covered"], 1), "components": rows,
        "units": totals.get("num_statements", 0) + totals.get("num_branches", 0),
        "covered_units": totals.get("covered_lines", 0) + totals.get("covered_branches", 0),
    }


def render_coverage_markdown(coverage):
    lines = [
        "## Source Coverage", "",
        f"Overall line + branch coverage of the measured packages: **{coverage['percent']}%**", "",
        *(["Counts only tests that passed; failing tests are excluded.", ""] if coverage.get("passing_only") else []),
        *([f"Target {coverage['target']:g}%: {'met' if coverage['target_met'] else 'NOT met'} "
           f"after {coverage.get('rounds', 0)} top-up round(s).", ""] if "target" in coverage else []),
        "Lowest coverage first. Components marked 'No' have no specification yet.", "",
        "| Component | Specified | Coverage | Uncovered lines |",
        "| --- | --- | ---: | --- |",
    ]
    for row in coverage["components"]:
        percent = "n/a" if row["percent"] is None else f"{row['percent']}%"
        lines.append(
            f"| `{row['component']}` | {'Yes' if row['specified'] else 'No'} | {percent} | "
            f"{row['uncovered_lines'][:120] or '-'} |"
        )
    return "\n".join(lines) + "\n"


def passing_selection(results, scripts):
    """Script files with passing tests, plus the failing tests inside them, as pytest --deselect ids."""
    files, deselect = [], []
    for item in results:
        execution, script = item.get("execution") or {}, item.get("script")
        if not script or not execution.get("passed"):
            continue
        name = Path(script).name
        files.append(str(Path(scripts) / name))
        deselect += [f"{name}::{test}" for test in sorted(_failed_test_names(execution.get("output", "")))]
    return files, deselect


def measure_coverage(repo_path, components, timeout=900, results=None):
    """Run generated scripts under pytest-cov; returns the coverage summary or {"error": ...}.

    With `results` (per-component outcomes) only passing tests count: failing tests also execute source lines.
    """
    scripts = write_test_support(repo_path).resolve()
    directory = scripts.parent / "coverage"
    directory.mkdir(parents=True, exist_ok=True)
    json_path = directory / "coverage.json"
    json_path.unlink(missing_ok=True)
    all_scripts = sorted(str(path) for path in scripts.glob("*_test.py"))
    targets, deselect = (all_scripts or [str(scripts)]), []
    passing_only = results is not None
    if results is not None:
        targets, deselect = passing_selection(results, scripts)
        if not targets:
            # Some runs don't persist per-test pass counts; keep coverage useful by
            # measuring all generated scripts instead of failing early.
            targets, deselect = (all_scripts or [str(scripts)]), []
            passing_only = False
    command = [
        repo_python(repo_path), "-m", "pytest", *targets, "-c", str(scripts / "pytest.ini"),
        "--rootdir", str(scripts), f"--confcutdir={scripts}", "-p", "no:cacheprovider", "-q",
        *(f"--deselect={test}" for test in deselect),
        *coverage_options(components, json_path),
    ]
    try:
        proc = subprocess.run(command, capture_output=True, text=True, timeout=timeout, cwd=str(scripts),
                              env=coverage_env(repo_path))
    except (subprocess.TimeoutExpired, OSError) as exc:
        return {"error": f"coverage run did not complete: {exc}"}
    output = (proc.stdout or "") + (proc.stderr or "")
    if not json_path.is_file() and results is not None and targets != (all_scripts or [str(scripts)]):
        # If selection of only passing scripts produced no report, retry on all generated scripts.
        fallback = [
            repo_python(repo_path), "-m", "pytest", *(all_scripts or [str(scripts)]), "-c", str(scripts / "pytest.ini"),
            "--rootdir", str(scripts), f"--confcutdir={scripts}", "-p", "no:cacheprovider", "-q",
            *coverage_options(components, json_path),
        ]
        try:
            proc2 = subprocess.run(fallback, capture_output=True, text=True, timeout=timeout, cwd=str(scripts),
                                   env=coverage_env(repo_path))
            output += "\n\n--- coverage fallback run ---\n" + (proc2.stdout or "") + (proc2.stderr or "")
        except (subprocess.TimeoutExpired, OSError):
            pass
    if not json_path.is_file():
        # Salvage mode: run each target script separately with coverage append so one
        # crashing script cannot block coverage for every other passing script.
        packages = sorted({item["module"].split(".")[0] for item in components})
        cov_data = scripts / ".coverage"
        cov_data.unlink(missing_ok=True)
        ran = 0
        salvage_log = []
        for index, target in enumerate(targets):
            per_script = [
                repo_python(repo_path), "-m", "pytest", target, "-c", str(scripts / "pytest.ini"),
                "--rootdir", str(scripts), f"--confcutdir={scripts}", "-p", "no:cacheprovider", "-q",
                *(f"--deselect={test}" for test in deselect if Path(test.split("::", 1)[0]).name == Path(target).name),
                *(f"--cov={name}" for name in packages), "--cov-branch", "--cov-report=",
            ]
            if index > 0:
                per_script.append("--cov-append")
            try:
                per = subprocess.run(
                    per_script,
                    capture_output=True,
                    text=True,
                    timeout=timeout,
                    cwd=str(scripts),
                    env=coverage_env(repo_path),
                )
            except (subprocess.TimeoutExpired, OSError) as exc:
                salvage_log.append(f"{Path(target).name}: {exc}")
                continue
            ran += 1
            per_out = ((per.stdout or "") + (per.stderr or "")).strip()
            if "INTERNALERROR" in per_out:
                salvage_log.append(f"{Path(target).name}: INTERNALERROR")

        if cov_data.is_file() and ran:
            try:
                to_json = subprocess.run(
                    [repo_python(repo_path), "-m", "coverage", "json", "-o", str(json_path)],
                    capture_output=True,
                    text=True,
                    timeout=timeout,
                    cwd=str(scripts),
                    env=coverage_env(repo_path),
                )
                if to_json.returncode != 0:
                    salvage_log.append(((to_json.stdout or "") + (to_json.stderr or "")).strip())
            except (subprocess.TimeoutExpired, OSError) as exc:
                salvage_log.append(f"coverage json export failed: {exc}")

        if not json_path.is_file():
            reason = "pytest-cov is not installed (pip install pytest-cov)" \
                if "unrecognized arguments" in output else "pytest produced no coverage data"
            extra = "\n".join([line for line in salvage_log if line])
            return {"error": reason, "output": (output + ("\n\n--- salvage ---\n" + extra if extra else ""))[-2000:]}
    try:
        coverage = summarize_coverage(repo_path, components, json_path, scripts)
    except (OSError, ValueError, KeyError) as exc:
        return {"error": f"could not read coverage data: {exc}"}
    coverage["passing_only"] = passing_only
    (directory / "coverage_summary.md").write_text(render_coverage_markdown(coverage), encoding="utf-8")
    return coverage


_IMPORT_PROBE = (
    "import importlib, json, sys\n"
    "for root in reversed(json.loads(sys.argv[1])):\n"
    "    sys.path.insert(0, root)\n"
    "failures = {}\n"
    "for name in json.loads(sys.argv[2]):\n"
    "    try:\n"
    "        importlib.import_module(name)\n"
    "    except BaseException as exc:\n"
    "        failures[name] = f'{type(exc).__name__}: {exc}'[:300]\n"
    "print('\\x00RESULT' + json.dumps(failures))\n"
)


def check_imports(repo_path, modules, timeout=300):
    """Import modules in a fresh interpreter; returns {module: error} for the ones that fail."""
    try:
        proc = subprocess.run(
            [repo_python(repo_path), "-c", _IMPORT_PROBE,
             json.dumps(import_roots(repo_path)), json.dumps(sorted(modules))],
            capture_output=True, text=True, timeout=timeout, cwd=str(repo_path),
        )
    except (subprocess.TimeoutExpired, OSError):
        logger.warning("Import preflight could not complete; skipping it")
        return {}
    marker = next((line for line in reversed(proc.stdout.splitlines()) if line.startswith("\x00RESULT")), None)
    return json.loads(marker[len("\x00RESULT"):]) if marker else {}


def declared_dependencies(repo_path):
    """Runtime dependencies declared by pyproject.toml, else requirements.txt (as pip arguments)."""
    repo = Path(repo_path)
    pyproject = repo / "pyproject.toml"
    if pyproject.is_file():
        try:
            import tomllib
            deps = tomllib.loads(pyproject.read_text(encoding="utf-8")).get("project", {}).get("dependencies", [])
            if deps:
                return list(deps)
        except Exception:
            logger.warning("Could not read dependencies from %s", pyproject)
    requirements = repo / "requirements.txt"
    return ["-r", str(requirements)] if requirements.is_file() else []


def install_declared_dependencies(repo_path):
    """pip-install the repository's declared dependencies (never the repository itself)."""
    args = declared_dependencies(repo_path)
    if not args:
        return False
    try:
        proc = subprocess.run(
            [repo_python(repo_path), "-m", "pip", "install", "-q", *args],
            capture_output=True, text=True, timeout=900, cwd=str(repo_path),
        )
    except (subprocess.TimeoutExpired, OSError):
        return False
    return proc.returncode == 0


def preflight_components(repo_path, components, install=install_declared_dependencies):
    """Split components into importable ones and failures reported without spending LLM calls."""
    components = list(components)
    errors = check_imports(repo_path, {item["module"] for item in components})
    own = {item["module"].split(".")[0] for item in components}
    missing_third_party = any(
        "No module named" in error and error.split("No module named", 1)[1].strip(" '\"").split(".")[0] not in own
        for error in errors.values()
    )
    if errors and missing_third_party and install is not None and install(repo_path):
        errors = check_imports(repo_path, {item["module"] for item in components})
    ready = [item for item in components if item["module"] not in errors]
    failed = [
        {
            "component": item["module"] + "." + item["symbol"], "status": "FAILED",
            "failure_stage": "import preflight",
            "error": f"Module {item['module']} cannot be imported: {errors[item['module']]}",
        }
        for item in components if item["module"] in errors
    ]
    return ready, failed


def is_transient(message):
    return any(marker in str(message) for marker in TRANSIENT_MARKERS)


def describe_failure(result):
    """One factual line on why a component has no passing tests; transient errors are marked retryable."""
    error, stage = str(result.get("error") or ""), result.get("failure_stage")
    if stage == "import preflight":
        return "module cannot be imported (missing dependency or environment); the model cannot fix this"
    if is_transient(error):
        code = re.search(r"HTTP (\d{3})", error)
        return (f"VIO gateway error{' ' + code.group(1) if code else ''} during {stage or 'generation'}; "
                "transient and retryable, not a testability problem")
    execution = result.get("execution")
    if execution:
        return (f"{execution.get('failed', 0)} test(s) failed and {execution.get('errors', 0)} errored "
                "after repair attempts")
    return (error.strip().splitlines() or [""])[0][:160] or "unknown failure"


def _passed_cleanly(execution):
    execution["all_passed"] = bool(execution.get("all_passed") and " skipped" not in execution.get("output", ""))
    return execution["all_passed"]


def _failed_test_names(output):
    return set(re.findall(r"::(test_[A-Za-z0-9_]+)\s+(?:FAILED|ERROR)", output))


def failure_hints(output, code):
    """Concrete fixes for recurring generated-test mistakes recognised in the pytest output."""
    try:
        local_classes = {node.name for node in ast.walk(ast.parse(code)) if isinstance(node, ast.ClassDef)}
    except SyntaxError:
        local_classes = set()
    stand_ins = local_classes | {"SimpleNamespace", "Mock", "NonCallableMock"}
    hints = []
    if re.search(r'Fixture "[^"]+" called directly', output):
        hints.append(FIXTURE_NOTE.strip())
    if any(name in stand_ins for name in re.findall(r"AttributeError: '(\w+)' object has no attribute", output)):
        hints.append("A stand-in object lacks an attribute the source reads. Add every attribute read on the "
                     "executed path (trace each `obj.attr`, including nested options) or use MagicMock.")
    if re.search(r"unexpected keyword argument|positional arguments? but|missing \d+ required", output):
        hints.append("A call does not match the real signature. Use only the parameters shown in the source "
                     "signature of that function or constructor.")
    if "DID NOT RAISE" in output:
        hints.append("The source does not raise for that input. Trace the shown source: assert the value it "
                     "returns, or choose an input that reaches the raise statement.")
    if "KeyError" in output:
        hints.append("If the KeyError comes from module-level state the source reads (a registry, cache or mapping "
                     "that the library's own setup normally fills), populate it inside each failing test with "
                     "monkeypatch.setitem, monkeypatch.setattr or unittest.mock.patch.dict on the module that owns "
                     "it, using the real values shown in the source.")
    if re.search(r"AttributeError: \{'__name__'", output):
        hints.append("setattr was given a dict (such as __globals__). Use monkeypatch.setitem(mapping, key, value) "
                     "or unittest.mock.patch.dict, or patch the module object that owns the name.")
    if re.search(r"(?:type object '\w+'|<class '[\w.]+'>) has no attribute", output):
        hints.append("If a package re-exports an object named like its own submodule, `import pkg.mod as m` returns "
                     "that object, not the module. Get the module with sys.modules['pkg.mod'] or "
                     "importlib.import_module('pkg.mod') and read the attributes from it.")
    return "\n".join(f"HINT: {hint}" for hint in hints)


def _repair_rounds(repo, component, plan, spec, script, header, code, execution, writer, extract_code,
                   run_tests, attempts, result, rounds, diagnosis="", include_task_guidance=False):
    """Rewrite only the failing tests (guided by `diagnosis`) until pytest passes or no progress is made."""
    writer_client, writer_model = writer
    for _round in range(rounds):
        if execution.get("all_passed"):
            break
        result["repair_attempted"] = True
        try:
            output = execution.get("output", "")
            before = _failed_test_names(output)
            failed_names = set(before)
            repair_scenarios = [
                scenario for scenario in plan["scenarios"]
                if not failed_names or scenario["name"] in failed_names
            ]
            repair_plan = _subplan(plan, repair_scenarios)
            failed_names = failed_names or {scenario["name"] for scenario in repair_scenarios}
            context = (f"VIO DIAGNOSIS: {diagnosis}\n" if diagnosis else "") + output[-3000:]
            hints = failure_hints(output, code)
            if hints:
                context += "\n" + hints
            unmet = [gap for gap in semantic_gaps(code, plan, component) if gap.split(":", 1)[0] in failed_names]
            if unmet:
                context += "\nHINT: the failing tests omit specified setup or checks: " + "; ".join(unmet[:8])
            failing_code = _test_sources(code, failed_names)
            if failing_code:
                context += "\nCURRENT CODE OF THE FAILING TESTS:\n" + failing_code[:3000]
            repaired_tests, _, repair_prompts = generate_chunked_script(
                writer_client, writer_model, component, repair_plan, spec,
                extract_code, attempts, context, include_task_guidance,
            )
            fixed_code = merge_test_chunks([code, repaired_tests], replace_names=failed_names)
            check_alignment(fixed_code, plan, component)
            script.write_text(header + fixed_code + "\n", encoding="utf-8")
            result["prompt"] = result.get("prompt", "") + \
                "\n\n--- pytest repair prompts ---\n\n" + "\n\n".join(repair_prompts)
            fixed_execution = run_tests(str(repo), script.name)
            _passed_cleanly(fixed_execution)
            still_failing = _failed_test_names(fixed_execution.get("output", ""))
            regressed = bool(before) and not still_failing <= before
            fully_passes = fixed_execution["all_passed"]
            if fully_passes or (fixed_execution.get("passed", 0) > execution.get("passed", 0) and not regressed):
                execution, code = fixed_execution, fixed_code
                # A partly fixed script stays on disk as a candidate but is never reported as a repair.
                result["repair_applied" if fully_passes else "repair_partial"] = True
                if fully_passes:
                    result.pop("repair_partial", None)
                resolved = failed_names - still_failing
                if resolved:
                    annotate_spec_corrections(spec, resolved, output, diagnosis)
            else:
                script.write_text(header + code + "\n", encoding="utf-8")
                break
        except Exception as repair_error:
            script.write_text(header + code + "\n", encoding="utf-8")
            result["repair_error"] = str(repair_error)
            break
    unresolved = set() if execution.get("all_passed") else _failed_test_names(execution.get("output", ""))
    if unresolved:
        result["repair_unresolved"] = sorted(unresolved)
    else:
        result.pop("repair_unresolved", None)
    return execution, code


def _record_outcome(result, directory, spec, script, plan, execution, code, identity, component=None):
    passed = bool(execution.get("all_passed"))
    result.update(script=str(script), execution=execution, code=code, status="COMPLETED" if passed else "FAILED")
    result["error"] = "" if passed else \
        "Tests did not all pass; inspect per-test results and environment requirements."
    if component is not None:
        result["review_flags"] = semantic_gaps(code, plan, component, advisory=True)
    result_path = directory / f"{spec.stem}_result.md"
    result_path.write_text(
        f"# {identity}\n\nSpecification: {spec.name}\n\n"
        f"Script: {script.name}\n\nPlanned scenarios: {len(plan['scenarios'])}\n\n"
        f"Status: {result['status']}\n\n```text\n{execution['output']}\n```\n",
        encoding="utf-8",
    )
    result["path"] = str(result_path)


def diagnose_failure(client, model, component, failing_code, pytest_output):
    """Ask the planner why generated tests failed; returns {cause, explanation, fix} or None."""
    prompt = (
        "Act as a senior QA engineer. Generated pytest tests failed against the real source. Decide the root "
        "cause by tracing the shown source for the failing inputs; do not guess. Respond with JSON only: "
        '{"cause":"wrong_expectation|wrong_input|setup_or_api_misuse|environment|source_bug_suspected",'
        '"explanation":"one or two sentences naming the failing tests and why","fix":"what the tests must '
        'assert or set up instead, derived from the shown source"}. Causes: wrong_expectation = the test '
        "asserts something the source does not do; wrong_input = the input does not trigger the intended "
        "path; setup_or_api_misuse = wrong constructor arguments, fixtures or imports; environment = a missing "
        "dependency, file or service; source_bug_suspected = ONLY if the shown source contradicts its own "
        "docstring, comments or a clearly stated contract, otherwise the test is wrong.\n"
        f"Component: {component['module']}.{component['symbol']}\n"
        f"FAILING TEST CODE:\n{failing_code[:3000]}\nPYTEST OUTPUT:\n{pytest_output[-3000:]}\n"
        f"SOURCE:\n{_truncate(component['source'], RERUN_SOURCE_CHARS)}"
    )
    try:
        text, metrics = client.generate(model, prompt, format="json", options=generation_options(prompt, 700))
        log_generation_metrics(metrics, "failure diagnosis")
        data = _load_json(text)
    except Exception:
        logger.exception("Failure diagnosis unavailable for %s", component["symbol"])
        return None
    if not isinstance(data, dict):
        return None
    cause = str(data.get("cause", "")).strip().lower()
    return {
        "cause": cause if cause in DIAGNOSIS_CAUSES else "unknown",
        "explanation": str(data.get("explanation") or "")[:600],
        "fix": str(data.get("fix") or "")[:600],
    }


def rerun_component(repo, component, previous, test_types, plan_client, plan_model, extract_code, run_tests,
                    choose_writer=None, engine="ollama", on_stage=None):
    """Retry one failed component: re-plan when it has no spec, else diagnose and repair only its failing tests."""
    repo = Path(repo)
    spec, script = previous.get("spec"), previous.get("script")
    plan = previous.get("plan")
    if not (plan and spec and script and Path(script).is_file()):
        smaller = {**component, "source": _truncate(component["source"], RERUN_SOURCE_CHARS)}
        result = process_component(repo, smaller, test_types, plan_client, plan_model, extract_code,
                                   run_tests, choose_writer=choose_writer, engine=engine, on_stage=on_stage)
        return {**result, "rerun": "replanned"}

    spec, script = Path(spec), Path(script)
    result = {key: value for key, value in previous.items() if key not in ("repair_error", "diagnosis")}
    result["rerun"] = "repaired"
    parts = script.read_text(encoding="utf-8").split("\n", 2)
    if not parts[0].startswith(SCRIPT_MARKER_PREFIXES):
        return {**result, "rerun": "skipped", "error": f"Preserving user-edited script: {script.name}"}
    header, code = "\n".join(parts[:2]) + "\n", (parts[2] if len(parts) == 3 else "").rstrip("\n")
    identity = component["module"] + "." + component["symbol"]
    try:
        if on_stage:
            on_stage("pytest")
        execution = run_tests(str(repo), script.name)
        if not _passed_cleanly(execution):
            if on_stage:
                on_stage("repair")
            output = execution.get("output", "")
            diagnosis = diagnose_failure(plan_client, plan_model, component,
                                         _test_sources(code, _failed_test_names(output)), output)
            if diagnosis:
                result["diagnosis"] = diagnosis
            if diagnosis and diagnosis["cause"] in NO_AUTOFIX_CAUSES:
                result["repair_error"] = f"Needs review ({diagnosis['cause']}): {diagnosis['explanation']}"
            else:
                writer_client, writer_model, writer_label = (
                    choose_writer(plan) if choose_writer else (plan_client, plan_model, ""))
                result.update(model=writer_model, writer=writer_label or result.get("writer", ""))
                guidance = ""
                if diagnosis:
                    guidance = f"[{diagnosis['cause']}] {diagnosis['explanation']} FIX: {diagnosis['fix']}"
                execution, code = _repair_rounds(
                    repo, component, plan, spec, script, header, code, execution, (writer_client, writer_model),
                    extract_code, run_tests, [], result, RERUN_REPAIR_ROUNDS, guidance,
                    include_task_guidance=engine == "ollama",
                )
        _record_outcome(result, repo / "tests" / "orchestration_results", spec, script, plan, execution, code,
                        identity, component)
        if result.get("repair_error", "").startswith("Needs review"):
            result["error"] = result["repair_error"]
    except Exception as exc:
        result.update(status="FAILED", error=f"Re-run failed: {exc}")
    return result


def process_component(repo, component, test_types, plan_client, plan_model, extract_code, run_tests,
                      choose_writer=None, engine="ollama", on_stage=None):
    """Plan, specify, implement and run tests for one component; thread-safe per component.

    `on_stage` is called with plan, script, pytest or repair as each stage starts.
    """
    repo = Path(repo)
    directory = repo / "tests" / "orchestration_results"
    directory.mkdir(parents=True, exist_ok=True)
    identity = component["module"] + "." + component["symbol"]
    result = {"component": identity, "model": plan_model, "status": "FAILED", "engine": engine}
    timings, last = {}, [time.perf_counter()]
    result["timings"] = timings

    def mark(name):
        now = time.perf_counter()
        timings[name] = round(now - last[0], 1)
        last[0] = now

    def entering(name):
        if on_stage:
            on_stage(name)

    attempts = []
    stage = "specification planning"
    entering("plan")
    writer_client = plan_client
    try:
        if len(component["source"]) > MAX_SOURCE_CHARS:
            component = {**component, "source": _truncate(component["source"], MAX_SOURCE_CHARS)}
        base_prompt = prompt = plan_prompt(component, test_types, include_task_guidance=engine == "ollama")
        plan_component, plan_tokens, compact = component, PLAN_MAX_OUTPUT_TOKENS, False
        rounds, attempt = PLAN_ATTEMPTS, 0
        while True:
            attempt += 1
            stage = "specification planning"
            try:
                text, metrics = plan_client.generate(
                    plan_model, prompt, format="json",
                    options=generation_options(prompt, plan_tokens),
                )
            except RuntimeError as transport_error:
                if compact or not is_transient(str(transport_error)):
                    raise
                _record_generation_attempt(attempts, plan_model, prompt, f"[transport error] {transport_error}")
                # A gateway that times out on a large plan request often accepts a compact one.
                compact, rounds, plan_tokens = True, rounds + 1, COMPACT_PLAN_TOKENS
                plan_component = {**component, "source": _truncate(component["source"], COMPACT_SOURCE_CHARS)}
                base_prompt = prompt = plan_prompt(
                    plan_component, test_types, compact=True, include_task_guidance=engine == "ollama",
                )
                continue
            _record_generation_attempt(attempts, plan_model, prompt, text)
            log_generation_metrics(metrics, "test planning")
            stage = "plan validation"
            try:
                plan = normalize_plan(ground_plan(parse_plan(text), plan_component["source"]), test_types)
                break
            except (ValueError, TypeError, KeyError) as plan_error:
                if attempt == rounds:
                    raise
                prompt = (
                    base_prompt + "\nThe previous response was not a complete valid plan "
                    f"({plan_error}). Return a shorter, complete JSON object: at most 5 scenarios, "
                    "one short sentence per field, and for every scenario an 'evidence' value copied "
                    "verbatim from the shown source.\n"
                    f"PREVIOUS RESPONSE (truncated):\n{text[:1500]}"
                )
        writer_client, writer_model, writer_label = (
            choose_writer(plan) if choose_writer else (plan_client, plan_model, "")
        )
        mark("plan")
        result.update(model=writer_model, writer=writer_label, planner=plan_model,
                      scenarios=len(plan["scenarios"]), plan=plan)
        spec = write_spec(repo, component, plan, engine=engine, writer=writer_label)
        result["spec"] = str(spec)
        script = repo / "tests" / "test_scripts" / f"{spec.stem}_test.py"
        script.parent.mkdir(parents=True, exist_ok=True)
        if script.exists() and not script.read_text(encoding="utf-8").startswith(SCRIPT_MARKER_PREFIXES):
            raise ValueError(f"Preserving existing user script: {script.name}")
        header = f"{SCRIPT_MARKER_PREFIXES[1]}\n# Specification: {spec.name}\n"
        stage = "test script generation"
        entering("script")
        code, chunk_count, prompts = generate_chunked_script(
            writer_client, writer_model, component, plan, spec, extract_code, attempts,
            include_task_guidance=engine == "ollama",
        )
        result["chunk_count"] = chunk_count
        result["prompt"] = "\n\n--- scenario batch ---\n\n".join(prompts)
        script.write_text(header + code + "\n", encoding="utf-8")
        mark("script")
        stage = "pytest execution"
        entering("pytest")
        execution = run_tests(str(repo), script.name)
        mark("pytest")
        execution["all_passed"] = bool(
            execution.get("all_passed") and " skipped" not in execution.get("output", "")
        )
        result["repair_attempted"] = False
        stage = "pytest repair"
        if not execution.get("all_passed"):
            entering("repair")
        execution, code = _repair_rounds(
            repo, component, plan, spec, script, header, code, execution, (writer_client, writer_model),
            extract_code, run_tests, attempts, result, REPAIR_ROUNDS,
            include_task_guidance=engine == "ollama",
        )
        mark("repair")
        _record_outcome(result, directory, spec, script, plan, execution, code, identity, component)
    except Exception as exc:
        result["failure_stage"] = stage
        result["error"] = str(exc)
        result["attempts_captured"] = len(attempts)
        try:
            result["failure_artifact"] = _save_generation_failure(
                repo, component, result, stage, str(exc), attempts,
            )
        except OSError:
            logger.exception("Could not save generation failure artifact for %s", identity)
    return result


def generate_suite(repo_path, test_types, client, model, extract_code, run_tests, progress=None, components=None):
    """Yield per-component outcomes without claiming unexecuted tests pass."""
    repo = Path(repo_path)
    for component in (discover_components(repo) if components is None else components):
        if progress:
            progress(component["module"] + "." + component["symbol"])
        yield process_component(repo, component, test_types, client, model, extract_code, run_tests)


def coverage_gap(coverage, target=COVERAGE_TARGET):
    """Line+branch units still to execute for the overall coverage to reach `target` percent."""
    needed = -(-int(target * coverage.get("units", 0)) // 100)
    return max(0, needed - coverage.get("covered_units", 0))


def pick_gap_rows(coverage, target=COVERAGE_TARGET, attempted=()):
    """Components with the most unexecuted code, enough of them to plausibly close the gap."""
    gap = coverage_gap(coverage, target)
    rows = sorted((row for row in coverage["components"]
                   if row.get("missing") and row["component"] not in attempted),
                  key=lambda row: (-row["missing"], row["component"]))
    chosen, total = [], 0
    for row in rows:
        if len(chosen) >= COVERAGE_MAX_BATCH or (chosen and total >= 2 * gap):
            break
        chosen.append(row)
        total += row["missing"]
    return chosen


def coverage_focus(repo, component, row):
    """Numbered source lines of the component that no test has executed yet."""
    try:
        lines = (Path(repo) / component["source_path"]).read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError):
        return ""
    shown = [f"{number}: {lines[number - 1].rstrip()}" for number in row.get("missing_lines", [])
             if 0 < number <= len(lines) and lines[number - 1].strip()]
    return _truncate("\n".join(shown), COVERAGE_FOCUS_CHARS)


def _drop_tests(code, names):
    tree = ast.parse(code)
    tree.body = [node for node in tree.body
                 if not (isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in names)]
    return merge_test_chunks([ast.unparse(tree)])


def _respec(spec, repo, component, plan, engine, writer):
    """Re-render a specification from `plan` while keeping its post-execution corrections section."""
    heading = "\n## 15. Post-execution Corrections"
    old = Path(spec).read_text(encoding="utf-8")
    notes = heading + old.split(heading, 1)[1] if heading in old else ""
    path = Path(write_spec(repo, component, plan, engine, writer))
    if notes:
        path.write_text(path.read_text(encoding="utf-8").rstrip("\n") + "\n" + notes, encoding="utf-8")


def _supplement(plan, extra):
    """`extra` scenarios and requirements renumbered so they can join `plan` without id or name clashes."""
    taken = {scenario["name"] for scenario in plan["scenarios"]}
    numbers = [int(item["id"][4:]) for item in plan["requirements"]
               if item["id"].startswith("REQ-") and item["id"][4:].isdigit()]
    offset = max(numbers, default=len(plan["requirements"]))
    remap = {item["id"]: f"REQ-{offset + index}" for index, item in enumerate(extra["requirements"], 1)}
    scenarios = []
    for index, scenario in enumerate(extra["scenarios"], len(plan["scenarios"]) + 1):
        name = scenario["name"]
        while name in taken:
            name += "_cov"
        taken.add(name)
        scenarios.append({**scenario, "name": name, "id": f"SC-{index:02d}",
                          "requirement_ids": [remap[rid] for rid in scenario["requirement_ids"] if rid in remap]})
    requirements = [{**item, "id": remap[item["id"]]} for item in extra["requirements"]]
    return {**extra, "requirements": requirements, "scenarios": scenarios}


def top_up_component(repo, component, row, previous, test_types, plan_client, plan_model, extract_code,
                     run_tests, choose_writer=None, engine="ollama"):
    """Add tests for source lines no test runs yet; returns the updated result and never loses passing tests."""
    repo = Path(repo)
    previous = previous or {}
    if previous.get("failure_stage") == "import preflight" or (previous.get("script") and previous.get("status") != "COMPLETED"):
        return {**previous, "topup": "skipped: component has no passing tests to extend"}
    focus = coverage_focus(repo, component, row)
    if not focus:
        return previous
    focused = {
        **component, "coverage_gap": True,
        "source": _truncate(component["source"], MAX_SOURCE_CHARS - COVERAGE_FOCUS_CHARS - 200)
        + "\n\n# Source lines no test has executed yet (line number: code):\n" + focus,
    }
    if not previous.get("script"):
        result = process_component(repo, focused, test_types, plan_client, plan_model, extract_code,
                                   run_tests, choose_writer=choose_writer, engine=engine)
        return {**result, "topup": "specified to raise coverage"}

    spec, script, plan = Path(previous["spec"]), Path(previous["script"]), previous.get("plan")
    old_spec = spec.read_text(encoding="utf-8") if spec.is_file() else ""
    old_script = script.read_text(encoding="utf-8") if script.is_file() else ""
    parts = old_script.split("\n", 2)
    if not (plan and old_spec and parts[0].startswith(SCRIPT_MARKER_PREFIXES)):
        return {**previous, "topup": "skipped: script or specification is user-maintained"}
    header, code = "\n".join(parts[:2]) + "\n", (parts[2] if len(parts) == 3 else "").rstrip("\n")
    identity = component["module"] + "." + component["symbol"]
    result = {key: value for key, value in previous.items() if key not in ("repair_error", "diagnosis")}

    def restore(reason):
        spec.write_text(old_spec, encoding="utf-8")
        script.write_text(old_script, encoding="utf-8")
        return {**previous, "topup": reason}

    try:
        prompt = plan_prompt(focused, test_types, include_task_guidance=engine == "ollama")
        prompt += "\nExisting test names, do not reuse: " + json.dumps([s["name"] for s in plan["scenarios"]]) + "\n"
        text, _metrics = plan_client.generate(plan_model, prompt, format="json",
                                              options=generation_options(prompt, PLAN_MAX_OUTPUT_TOKENS))
        extra = _supplement(plan, normalize_plan(ground_plan(parse_plan(text), focused["source"]), test_types))
        merged = {**plan, "requirements": plan["requirements"] + extra["requirements"],
                  "scenarios": plan["scenarios"] + extra["scenarios"],
                  "limitations": [*plan["limitations"], f"Coverage top-up added {len(extra['scenarios'])} "
                                  "scenario(s) targeting source lines no earlier test executed."]}
        writer_client, writer_model, _label = choose_writer(extra) if choose_writer else (plan_client, plan_model, "")
        new_code, _chunks, _prompts = generate_chunked_script(
            writer_client, writer_model, focused, extra, spec, extract_code,
            include_task_guidance=engine == "ollama")
        code = merge_test_chunks([code, new_code])
        check_alignment(code, merged, component)
        _respec(spec, repo, component, merged, previous.get("engine", engine), previous.get("writer", ""))
        script.write_text(header + code + "\n", encoding="utf-8")
        execution = run_tests(str(repo), script.name)
        _passed_cleanly(execution)
        execution, code = _repair_rounds(
            repo, focused, merged, spec, script, header, code, execution, (writer_client, writer_model),
            extract_code, run_tests, [], result, REPAIR_ROUNDS, include_task_guidance=engine == "ollama")
        new_names = {scenario["name"] for scenario in extra["scenarios"]}
        failed = _failed_test_names(execution.get("output", ""))
        if not execution.get("all_passed") and failed and failed <= new_names and failed != new_names:
            code = _drop_tests(code, failed)
            kept = [s for s in merged["scenarios"] if s["name"] not in failed]
            linked = {rid for s in kept for rid in s["requirement_ids"]}
            merged = {**merged, "scenarios": kept,
                      "requirements": [r for r in merged["requirements"] if r["id"] in linked]}
            _respec(spec, repo, component, merged, previous.get("engine", engine), previous.get("writer", ""))
            script.write_text(header + code + "\n", encoding="utf-8")
            execution = run_tests(str(repo), script.name)
            _passed_cleanly(execution)
        if not execution.get("all_passed"):
            return restore("no passing tests could be added")
    except Exception as exc:
        logger.exception("Coverage top-up failed for %s", identity)
        return restore(f"failed: {exc}")
    result.update(plan=merged, scenarios=len(merged["scenarios"]),
                  topup=f"added {len(merged['scenarios']) - len(plan['scenarios'])} test(s)")
    _record_outcome(result, repo / "tests" / "orchestration_results", spec, script, merged, execution, code,
                    identity, component)
    return result


def raise_coverage(repo_path, components, coverage, top_up, target=COVERAGE_TARGET,
                   rounds=COVERAGE_ROUNDS, workers=2, measure=None, exclude=()):
    """Top up the least-covered components, re-measuring each round, until `target` percent is reached.

    `exclude` names components whose own tests fail: they need repair before more tests can be added.
    """
    measure = measure or measure_coverage
    by_name = {item["module"] + "." + item["symbol"]: item for item in components}
    attempted, ran = set(exclude), 0
    for _round in range(rounds):
        if coverage.get("error") or coverage["percent"] >= target:
            break
        jobs = [(by_name[row["component"]], row) for row in pick_gap_rows(coverage, target, attempted)
                if row["component"] in by_name]
        if not jobs:
            break
        attempted.update(job[1]["component"] for job in jobs)

        def run(job):
            try:
                return top_up(*job)
            except Exception:
                logger.exception("Coverage top-up crashed for %s", job[1]["component"])
                return None

        with ThreadPoolExecutor(max_workers=workers) as executor:
            list(executor.map(run, jobs))
        ran += 1
        refreshed = measure(repo_path, components)
        if refreshed.get("error"):
            break
        coverage = refreshed
    if coverage.get("error"):
        return coverage
    return {**coverage, "target": target, "target_met": coverage["percent"] >= target, "rounds": ran}
