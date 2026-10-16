from types import SimpleNamespace

import pytest

from src.orchestrator.spec_pipeline import (
    check_alignment,
    classify_components,
    discover_components,
    generate_aligned_code,
    generate_chunked_script,
    generate_suite,
    measure_coverage,
    merge_test_chunks,
    plan_prompt,
    script_prompt,
    select_components,
    task_guidance,
    remove_unplanned_tests,
)


COMPONENT = {"module": "example.component", "symbol": "Widget"}
PLAN = {"scenarios": [{"name": "test_widget_behavior"}]}
VALID_SCRIPT = '''from example.component import Widget

def test_widget_behavior():
    widget = Widget()
    assert widget is not None
'''


@pytest.mark.parametrize(
    ("code", "error"),
    [
        (
            '''from example.component import Widget
import pytest

@pytest.mark.skip(reason="unknown")
def test_widget_behavior():
    assert Widget() is not None
''',
            "Skipped or xfailed",
        ),
        (
            '''from example.component import Widget

class Widget:
    pass

def test_widget_behavior():
    assert Widget() is not None
''',
            "redefine target component",
        ),
        (
            '''from example.component import Widget

def test_widget_behavior():
    Widget = Widget()
    assert Widget is not None
''',
            "shadow imported target",
        ),
        (
            '''from example.component import Widget

def test_widget_behavior():
    Widget()
''',
            "no behavioral assertion",
        ),
        (
            '''def test_widget_behavior():
    assert True
''',
            "Placeholder assertion",
        ),
    ],
)
def test_check_alignment_rejects_non_behavioral_suites(code, error):
    with pytest.raises(ValueError, match=error):
        check_alignment(code, PLAN, COMPONENT)


def test_check_alignment_rejects_imported_target_used_as_module():
    code = '''from example.component import Widget as widget_module

def test_widget_behavior():
    assert widget_module.Widget() is not None
'''
    with pytest.raises(ValueError, match="not a module"):
        check_alignment(code, PLAN, COMPONENT)


def test_check_alignment_rejects_assertion_contradicting_local_fixture():
    code = '''from example.component import Widget

class Structured:
    _on_update = None

def test_widget_behavior():
    assert not hasattr(Structured, "_on_update")
    assert Widget() is not None
'''
    with pytest.raises(ValueError, match="contradicts fixture"):
        check_alignment(code, PLAN, COMPONENT)


def test_remove_unplanned_tests_keeps_only_specified_functions():
    plan = {"scenarios": [{"name": "test_widget_behavior"}]}
    code = '''from example.component import Widget

def test_widget_behavior():
    assert Widget() is not None

def test_unplanned_extra():
    assert Widget() is not None
'''
    cleaned = remove_unplanned_tests(code, plan)
    assert "def test_widget_behavior" in cleaned
    assert "test_unplanned_extra" not in cleaned
    check_alignment(cleaned, plan, COMPONENT)


def test_generate_aligned_code_retries_invalid_suite():
    invalid_script = '''from example.component import Widget

def test_widget_behavior():
    Widget = Widget()
    assert Widget is not None
'''
    metrics = SimpleNamespace(
        response_tokens=1,
        total_time=0.01,
        load_duration=0,
        prompt_eval_duration=0,
        eval_duration=0,
    )

    class FakeClient:
        def __init__(self):
            self.responses = iter([invalid_script, VALID_SCRIPT])
            self.prompts = []

        def generate(self, model, prompt, options=None):
            self.prompts.append(prompt)
            return next(self.responses), metrics

    client = FakeClient()
    generated = generate_aligned_code(
        client, "fake-model", "write a test", str, {}, PLAN, COMPONENT
    )

    assert generated == VALID_SCRIPT
    assert len(client.prompts) == 2
    assert "Validation error:" in client.prompts[1]


def test_generate_aligned_code_requests_only_missing_scenarios():
    component = {**COMPONENT, "source": "class Widget:\n    pass\n"}
    plan = normalize_plan(parse_plan(json.dumps(RAW_PLAN)), ["unit_test"])
    partial = "from example.component import Widget\n\ndef test_size():\n    assert Widget() is not None\n"
    missing = "from example.component import Widget\n\ndef test_leak():\n    assert Widget() is not None\n"
    metrics = SimpleNamespace(response_tokens=1, total_time=0.01, load_duration=0,
                              prompt_eval_duration=0, eval_duration=0)

    class MissingScenarioClient:
        def __init__(self):
            self.responses = iter([partial, missing])
            self.prompts = []

        def generate(self, model, prompt, options=None):
            self.prompts.append(prompt)
            return next(self.responses), metrics

    client = MissingScenarioClient()
    code = generate_aligned_code(client, "fake", "write tests", str, {}, plan, component)

    assert "def test_size" in code and "def test_leak" in code
    assert len(client.prompts) == 2
    assert '"test_leak"' in client.prompts[1]
    assert '"test_size"' in client.prompts[1]
    assert "ALREADY IMPLEMENTED" in client.prompts[1]


def test_generate_aligned_code_rejects_second_invalid_suite():
    invalid_script = '''from example.component import Widget

@pytest.mark.skip(reason="unknown")
def test_widget_behavior():
    assert Widget() is not None
'''
    metrics = SimpleNamespace(
        response_tokens=1,
        total_time=0.01,
        load_duration=0,
        prompt_eval_duration=0,
        eval_duration=0,
    )

    class FakeClient:
        def generate(self, model, prompt, options=None):
            return invalid_script, metrics

    with pytest.raises(ValueError, match="Skipped or xfailed"):
        generate_aligned_code(
            FakeClient(), "fake-model", "write a test", str, {}, PLAN, COMPONENT
        )

def test_discover_components_resolves_relative_imports(tmp_path):
    package = tmp_path / "src" / "pkg" / "sub"
    package.mkdir(parents=True)
    (package / "helpers.py").write_text("class Helper:\n    pass\n", encoding="utf-8")
    (package / "widget.py").write_text(
        "from .helpers import Helper\nfrom .. import top\n\n"
        "class Widget:\n    pass\n",
        encoding="utf-8",
    )

    components = list(discover_components(tmp_path))
    widget = next(item for item in components if item["symbol"] == "Widget")

    assert "from pkg.sub.helpers import Helper" in widget["source"]
    assert "from pkg import top" in widget["source"]
    assert "from .helpers" not in widget["source"]


def test_discover_components_includes_referenced_local_types(tmp_path):
    package = tmp_path / "src" / "pkg"
    package.mkdir(parents=True)
    (package / "etag.py").write_text(
        "class ETag:\n    def __init__(self, value):\n        self.value = value\n\n"
        "class ETagSet:\n    def contains(self, etag):\n"
        "        if isinstance(etag, ETag):\n            return etag.value in {'x'}\n"
        "        return etag in {'x'}\n",
        encoding="utf-8",
    )

    components = list(discover_components(tmp_path))
    etag_set = next(item for item in components if item["symbol"] == "ETagSet")

    assert "class ETag:" in etag_set["source"]
    assert "isinstance(etag, ETag)" in etag_set["source"]


def test_generate_suite_reports_progress_before_each_component(tmp_path):
    package = tmp_path / "src" / "pkg"
    package.mkdir(parents=True)
    (package / "widget.py").write_text("class Widget:\n    pass\n", encoding="utf-8")

    class FailingClient:
        def generate(self, *args, **kwargs):
            raise RuntimeError("offline")

    seen = []
    results = list(generate_suite(
        tmp_path, ["unit_test"], FailingClient(), "fake", str, None, progress=seen.append,
    ))

    assert seen == ["pkg.widget.Widget"]
    assert results[0]["status"] == "FAILED"


def test_generate_chunked_script_merges_all_scenarios_without_truncation():
    raw_plan = {
        "objective": "Exercise Widget.",
        "requirements": [{"id": "REQ-1", "text": "Widget is constructible"}],
        "scenarios": [
            {"name": f"test_widget_{index}", "category": "Happy Path", "test_type": "unit_test",
             "requirement_ids": ["REQ-1"], "inputs": "Widget()", "expected": "instance exists"}
            for index in range(7)
        ],
    }
    plan = normalize_plan(parse_plan(json.dumps(raw_plan)), ["unit_test"])

    class BatchClient:
        def __init__(self):
            self.batch_sizes = []

        def generate(self, model, prompt, options=None):
            batch = json.loads(prompt.split("SCENARIOS:\n", 1)[1].split("\nSOURCE:", 1)[0])
            self.batch_sizes.append(len(batch))
            functions = [
                f'''def {scenario['name']}():\n    """{scenario['id']}: instance exists."""\n    assert Widget() is not None'''
                for scenario in batch
            ]
            code = "```python\nfrom example.component import Widget\n\n" + "\n\n".join(functions) + "\n```"
            metrics = SimpleNamespace(response_tokens=1, total_time=0.0, load_duration=0,
                                      prompt_eval_duration=0, eval_duration=0)
            return code, metrics

    client = BatchClient()
    component = {**COMPONENT, "source": "class Widget:\n    pass\n"}
    code, chunks, _ = generate_chunked_script(
        client, "fake", component, plan, Path("001_widget.md"),
        lambda response: response.split("```python\n", 1)[1].rsplit("\n```", 1)[0],
    )

    assert client.batch_sizes == [3, 3, 1]
    assert chunks == 3
    check_alignment(code, plan, component)


# ---- Spec format, VIO adapter and component pipeline -------------------------------

import ast
import json
import threading
from pathlib import Path

from src.orchestrator.spec_pipeline import (
    MAX_SOURCE_CHARS,
    describe_failure,
    ground_plan,
    import_roots,
    normalize_plan,
    parse_plan,
    plan_prompt,
    preflight_components,
    process_component,
    rerun_component,
    render_spec,
    write_spec,
)
from src.vio_client import VIOGenerationAdapter

SECTIONS = [
    "## 1. Objective", "## 2. Functional Overview", "## 3. Business Logic",
    "## 4. Source Components", "## 5. Preconditions", "## 6. Assumptions",
    "## 7. Functional Requirements", "## 8. Test Data Requirements", "## 9. Test Scenarios",
    "## 10. Expected Results", "## 11. Risk Assessment", "## 12. Automation Feasibility",
    "## 13. Traceability Matrix", "## 14. Limitations",
]
WIDGET = {"module": "pkg.widget", "symbol": "Widget", "source_path": "src/pkg/widget.py",
          "source": "class Widget:\n    def size(self):\n        return 3\n"}
RAW_PLAN = {
    "objective": "Verify Widget.",
    "requirements": [{"id": "REQ-1", "text": "size is 3"}, {"id": "REQ-2", "text": "unused"}],
    "scenarios": [
        {"name": "test_size", "category": "happy path", "test_type": "unit_test",
         "requirement_ids": ["REQ-1"], "inputs": "Widget()", "expected": "3"},
        {"name": "test_leak", "category": "security", "test_type": "bogus",
         "requirement_ids": ["REQ-9"], "inputs": "x", "expected": "safe"},
    ],
}


def test_parse_plan_accepts_fenced_json_with_commentary():
    text = "Here is the plan:\n```json\n" + json.dumps(RAW_PLAN) + "\n```\nDone."
    assert [s["name"] for s in parse_plan(text)["scenarios"]] == ["test_size", "test_leak"]


def test_normalize_plan_repairs_types_and_requirement_links():
    plan = normalize_plan(parse_plan(json.dumps(RAW_PLAN)), ["unit_test", "security"])
    first, second = plan["scenarios"]
    assert (first["id"], first["category"], first["test_type"]) == ("SC-01", "Happy Path", "unit_test")
    assert second["test_type"] == "unit_test"
    assert second["requirement_ids"] == []
    assert plan["risk"]["level"] == "Medium"


def test_render_spec_contains_every_section_and_flags_gaps():
    plan = normalize_plan(parse_plan(json.dumps(RAW_PLAN)), ["unit_test"])
    text = render_spec(WIDGET, plan, "vio", "test_unit_generator", "001_pkg_widget_Widget_test.py", "001")
    for heading in SECTIONS:
        assert heading in text
    assert "| REQ-2 | unused | NOT COVERED | - |" in text
    assert "`test_size`" in text and "Scenarios without a requirement" in text


def test_write_spec_reserves_unique_numbers_across_threads(tmp_path):
    plan = normalize_plan(parse_plan(json.dumps(RAW_PLAN)), ["unit_test"])
    components = [{**WIDGET, "symbol": f"Widget{i}"} for i in range(12)]
    paths = []
    threads = [threading.Thread(target=lambda c=c: paths.append(write_spec(tmp_path, c, plan)))
               for c in components]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert sorted(p.name.split("_")[0] for p in paths) == [f"{n:03d}" for n in range(1, 13)]


def test_write_spec_preserves_user_authored_specification(tmp_path):
    specs = tmp_path / "tests" / "specs"
    specs.mkdir(parents=True)
    (specs / "001_pkg_widget_Widget.md").write_text("# mine\n", encoding="utf-8")
    plan = normalize_plan(parse_plan(json.dumps(RAW_PLAN)), ["unit_test"])
    with pytest.raises(ValueError, match="Preserving existing user specification"):
        write_spec(tmp_path, WIDGET, plan)


class ScriptedClient:
    def __init__(self, responses):
        self.responses, self.calls = iter(responses), []
        self.prompts = []

    def generate(self, model, prompt, format="", options=None):
        self.calls.append((model, format))
        self.prompts.append(prompt)
        metrics = SimpleNamespace(response_tokens=1, total_time=0.0, load_duration=0,
                                  prompt_eval_duration=0, eval_duration=0)
        return next(self.responses), metrics


def test_process_component_aligns_spec_script_and_result_with_separate_writer(tmp_path):
    plan = {"objective": "o", "scenarios": [
        {"name": "test_size", "category": "Happy Path", "test_type": "unit_test",
         "inputs": "Widget()", "expected": "3", "evidence": "return 3"}]}
    script = ("```python\nfrom pkg.widget import Widget\n\n"
              "def test_size():\n    assert Widget().size() == 3\n```")
    planner, writer = ScriptedClient([json.dumps(plan)]), ScriptedClient([script])

    result = process_component(
        tmp_path, WIDGET, ["unit_test"], planner, "master", lambda text: text.strip("`python\n"),
        lambda repo, name: {"output": "1 passed", "passed": 1, "failed": 0, "errors": 0, "all_passed": True},
        choose_writer=lambda plan: (writer, "unit-agent", "unit-agent"), engine="vio",
    )

    assert result["status"] == "COMPLETED" and result["writer"] == "unit-agent"
    assert Path(result["spec"]).stem + "_test" == Path(result["script"]).stem
    assert planner.calls == [("master", "json")] and writer.calls == [("unit-agent", "")]
    spec_text = Path(result["spec"]).read_text(encoding="utf-8")
    assert "unit-agent" in spec_text and Path(result["script"]).name in spec_text
    assert Path(result["script"]).read_text(encoding="utf-8").startswith("# Specification-driven test")


def test_process_component_reports_planner_failure_without_script(tmp_path):
    planner = ScriptedClient(["not json at all", "still not json"])
    result = process_component(
        tmp_path, WIDGET, ["unit_test"], planner, "master", str, lambda *a: {}, engine="vio",
    )
    assert result["status"] == "FAILED" and "script" not in result and result["error"]
    assert result["failure_stage"] == "plan validation"
    assert result["attempts_captured"] == 2
    assert "complete valid plan" in planner.prompts[1]
    failure = Path(result["failure_artifact"]).read_text(encoding="utf-8")
    assert "not json at all" in failure
    assert "### Raw model response" in failure


def test_process_component_repairs_only_failing_scenarios(tmp_path):
    plan = {"objective": "Verify Widget size.", "scenarios": [
        {"name": "test_widget_valid", "category": "Happy Path", "inputs": "Widget()", "expected": "size is 3",
         "evidence": "return 3"},
        {"name": "test_widget_edge", "category": "Boundary", "inputs": "Widget()", "expected": "size is 3",
         "evidence": "return 3"},
    ]}
    initial = ("```python\nfrom pkg.widget import Widget\n\n"
               "def test_widget_valid():\n    assert Widget().size() == 3\n\n"
               "def test_widget_edge():\n    assert Widget().size() == 4\n```")
    repaired = ("```python\nfrom pkg.widget import Widget\n\n"
                "def test_widget_edge():\n    assert Widget().size() == 3\n```")
    planner, writer = ScriptedClient([json.dumps(plan)]), ScriptedClient([initial, repaired])
    executions = iter([
        {"output": "pkg/test_widget.py::test_widget_valid PASSED\npkg/test_widget.py::test_widget_edge FAILED", "passed": 1,
         "failed": 1, "errors": 0, "all_passed": False},
        {"output": "2 passed", "passed": 2, "failed": 0, "errors": 0, "all_passed": True},
    ])

    result = process_component(
        tmp_path, WIDGET, ["unit_test"], planner, "master",
        lambda response: response.split("```python\n", 1)[1].rsplit("\n```", 1)[0],
        lambda repo, name: next(executions),
        choose_writer=lambda plan: (writer, "unit-agent", "unit-agent"), engine="vio",
    )

    assert result["status"] == "COMPLETED" and result["repair_applied"]
    assert len(writer.calls) == 2
    assert '"test_widget_edge"' in writer.prompts[1]
    assert '"test_widget_valid"' not in writer.prompts[1]
    final_script = Path(result["script"]).read_text(encoding="utf-8")
    assert "def test_widget_valid" in final_script
    assert "def test_widget_edge" in final_script
    assert "Source evidence" in Path(result["spec"]).read_text(encoding="utf-8")


def test_repair_prompt_lets_source_override_a_wrong_expectation_and_spec_discloses_it(tmp_path):
    plan = {"objective": "o", "scenarios": [
        {"name": "test_widget_edge", "category": "Boundary", "inputs": "Widget()", "expected": "size is 4",
         "evidence": "return 3"}]}
    initial = "```python\nfrom pkg.widget import Widget\n\ndef test_widget_edge():\n    assert Widget().size() == 4\n```"
    repaired = "```python\nfrom pkg.widget import Widget\n\ndef test_widget_edge():\n    assert Widget().size() == 3\n```"
    writer = ScriptedClient([initial, repaired])
    failing = ("______ test_widget_edge ______\nt.py:5: in test_widget_edge\n    assert Widget().size() == 4\n"
               "E   assert 3 == 4\n=== 1 failed ===")
    executions = iter([
        {"output": failing, "passed": 0, "failed": 1, "errors": 0, "all_passed": False},
        {"output": "1 passed", "passed": 1, "failed": 0, "errors": 0, "all_passed": True},
    ])

    result = process_component(
        tmp_path, WIDGET, ["unit_test"], ScriptedClient([json.dumps(plan)]), "master",
        lambda response: response.split("```python\n", 1)[1].rsplit("\n```", 1)[0],
        lambda repo, name: next(executions),
        choose_writer=lambda plan: (writer, "unit-agent", "unit-agent"), engine="vio",
    )

    assert "Keep the same test names and expected behavior" not in writer.prompts[1]
    assert "expectation was wrong" in writer.prompts[1]
    spec_text = Path(result["spec"]).read_text(encoding="utf-8")
    assert "## 15. Post-execution Corrections" in spec_text
    assert "`test_widget_edge` | assert 3 == 4" in spec_text


def test_ground_plan_drops_scenarios_without_verbatim_source_evidence():
    plan = {"scenarios": [
        {"name": "test_a", "evidence": "return  3", "requirement_ids": ["REQ-1"]},
        {"name": "test_b", "evidence": "per RFC 7541 the table must be empty", "requirement_ids": ["REQ-2"]},
        {"name": "test_c", "requirement_ids": ["REQ-2"]},
    ], "requirements": [{"id": "REQ-1", "text": "x"}, {"id": "REQ-2", "text": "y"}]}

    grounded = ground_plan(plan, WIDGET["source"])

    assert [s["name"] for s in grounded["scenarios"]] == ["test_a"]
    assert [r["id"] for r in grounded["requirements"]] == ["REQ-1"]
    assert "test_b, test_c" in grounded["limitations"][-1]


def test_ground_plan_rejects_a_plan_with_no_grounded_scenario():
    with pytest.raises(ValueError, match="verbatim from the shown source"):
        ground_plan({"scenarios": [{"name": "test_a", "evidence": "invented rule here"}]}, WIDGET["source"])


def test_plan_prompt_requires_traceable_expectations_and_evidence():
    prompt = plan_prompt(WIDGET, ["unit_test"])
    assert "DERIVED by tracing the shown source" in prompt
    assert "do not plan that scenario" in prompt and "'evidence' field" in prompt
    assert '"evidence"' in prompt


@pytest.mark.parametrize(
    ("test_type", "expected_guidance"),
    [
        ("unit_test", "isolated unit behavior"),
        ("linting", "isolated unit behavior"),
        ("integration_test", "real internal components"),
        ("e2e_test", "public entry point"),
        ("api_test", "public entry point"),
        ("security", "security controls"),
        ("vulnerability", "exact version and a specific advisory"),
    ],
)
def test_plan_prompt_includes_selected_vio_guidance(test_type, expected_guidance):
    prompt = plan_prompt(WIDGET, [test_type], include_task_guidance=True)

    assert "VIO SPECIALIST GUIDANCE:" in prompt
    assert expected_guidance in prompt


def test_script_prompt_includes_only_guidance_for_planned_scenarios():
    plan = {
        "objective": "Verify a workflow",
        "preconditions": [],
        "scenarios": [{
            "id": "SC-01", "name": "test_workflow", "category": "Happy Path",
            "test_type": "e2e_test", "inputs": "valid input", "expected": "completed",
            "evidence": "return result", "requirement_ids": ["REQ-1"],
        }],
    }

    prompt = script_prompt(
        {**WIDGET, "source": "return result"}, plan, Path("001_workflow.md"), include_task_guidance=True,
    )

    assert "public entry point" in prompt
    assert "## unit_test" not in prompt
    assert task_guidance(["e2e_test"]) in prompt


def test_non_vio_prompts_do_not_include_task_template_guidance():
    prompt = plan_prompt(WIDGET, ["e2e_test"])

    assert "VIO SPECIALIST GUIDANCE:" not in prompt
    assert "public entry point" not in prompt


def test_import_roots_are_absolute_for_relative_repo_paths(tmp_path, monkeypatch):
    (tmp_path / "repo" / "src").mkdir(parents=True)
    monkeypatch.chdir(tmp_path)
    assert all(Path(root).is_absolute() for root in import_roots("repo"))


def test_merge_test_chunks_replaces_named_function_and_rejects_other_duplicates():
    original = "from example.component import Widget\n\ndef test_value():\n    assert Widget() == 1\n"
    replacement = "from example.component import Widget\n\ndef test_value():\n    assert Widget() == 2\n"
    merged = merge_test_chunks([original, replacement], replace_names={"test_value"})
    assert "Widget() == 2" in merged and "Widget() == 1" not in merged
    with pytest.raises(ValueError, match="Duplicate test function"):
        merge_test_chunks([original, replacement])


def test_merge_test_chunks_keeps_helpers_and_removes_unused_imports():
    first = (
        "from example.component import Widget, NotARealSymbol\n\n"
        "class Owner:\n    headers = {}\n\n"
        "def test_widget_exists():\n    assert Widget() is not None\n"
    )
    second = (
        "from example.component import Widget\n\n"
        "class Response:\n    headers = {}\n\n"
        "def test_widget_again():\n    assert Widget() is not None\n"
    )

    merged = merge_test_chunks([first, second])
    tree = ast.parse(merged)
    top_level_classes = {node.name for node in tree.body if isinstance(node, ast.ClassDef)}
    imports = {alias.name for node in tree.body if isinstance(node, ast.ImportFrom) for alias in node.names}

    assert top_level_classes == {"Owner", "Response"}
    assert imports == {"Widget"}
    assert "NotARealSymbol" not in merged


class FakeVIO:
    def __init__(self, replies):
        self.replies, self.timeouts = list(replies), []

    def chat(self, agent, prompt, max_tokens, temperature, system=None, timeout=None):
        self.timeouts.append((system, timeout))
        return self.replies.pop(0)


def test_vio_adapter_retries_then_returns_content_and_metrics():
    client = FakeVIO([
        {"ok": False, "content": "", "error": "Timeout after 30s", "tokens": 0, "time_s": 1.0},
        {"ok": True, "content": "{}", "error": "", "tokens": 7, "time_s": 2.0},
    ])
    adapter = VIOGenerationAdapter(client, "agent", timeout=99, backoff=0)
    text, metrics = adapter.generate("agent", "p", format="json", options={"num_predict": 10})
    assert text == "{}" and metrics.response_tokens == 7
    assert [timeout for _, timeout in client.timeouts] == [99, 99]
    assert "JSON" in client.timeouts[0][0]


def test_vio_adapter_raises_after_exhausting_attempts():
    failure = {"ok": False, "content": "", "error": "HTTP 500", "tokens": 0, "time_s": 0.0}
    adapter = VIOGenerationAdapter(FakeVIO([failure, failure, failure]), "agent")
    with pytest.raises(RuntimeError, match="HTTP 500"):
        adapter.generate("agent", "p")


def test_vio_adapter_backs_off_on_gateway_timeout_but_not_on_other_errors(monkeypatch):
    sleeps = []
    monkeypatch.setattr("src.vio_client.time.sleep", sleeps.append)
    gateway = {"ok": False, "content": "", "error": "HTTP 504: <html>", "tokens": 0, "time_s": 0.0}
    ok = {"ok": True, "content": "{}", "error": "", "tokens": 1, "time_s": 0.1}
    VIOGenerationAdapter(FakeVIO([gateway, gateway, ok]), "agent").generate("agent", "p")
    assert len(sleeps) == 2 and sleeps[1] > sleeps[0]

    sleeps.clear()
    bad_request = {"ok": False, "content": "", "error": "HTTP 400: bad", "tokens": 0, "time_s": 0.0}
    with pytest.raises(RuntimeError):
        VIOGenerationAdapter(FakeVIO([bad_request] * 3), "agent").generate("agent", "p")
    assert sleeps == []


# ---- Generic-repository discovery and alignment --------------------------------------

def _write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_discover_components_skips_scripts_and_non_importable_modules(tmp_path):
    _write(tmp_path / "pkg" / "__init__.py", "from ._core import Engine\n")
    _write(tmp_path / "pkg" / "_core.py", "class Engine:\n    def run(self):\n        return 1\n")
    _write(tmp_path / "bench" / "benchmarks.py", "def time_it():\n    return 1\n")
    _write(tmp_path / "fuzz" / "afl-server.py", "def process_all():\n    return 1\n")
    _write(tmp_path / "scripts" / "release.py", "def publish():\n    return 1\n")
    _write(tmp_path / "setup.py", "def configure():\n    return 1\n")
    _write(tmp_path / "bench.py", "def loose():\n    return 1\n")
    _write(tmp_path / "pkg" / "tests" / "test_core.py", "def helper():\n    return 1\n")

    assert [(c["module"], c["symbol"]) for c in discover_components(tmp_path)] == [("pkg._core", "Engine")]


def test_discover_components_ranks_exported_logic_before_trivial_markers(tmp_path):
    _write(tmp_path / "pkg" / "__init__.py", "from ._core import Marker, Engine\n")
    _write(tmp_path / "pkg" / "_core.py",
           "class Marker:\n    pass\n\nclass Engine:\n    def run(self, x):\n        if x:\n            return 1\n        return 2\n")
    _write(tmp_path / "pkg" / "a.py", "def helper():\n    return 1\n")

    symbols = [c["symbol"] for c in discover_components(tmp_path)]
    assert symbols[0] == "Engine" and symbols[-1] == "Marker"


def test_discover_components_drops_typing_overloads(tmp_path):
    _write(tmp_path / "pkg" / "__init__.py", "")
    _write(tmp_path / "pkg" / "m.py",
           "from typing import overload\n\n@overload\ndef f(x: int) -> int: ...\n"
           "@overload\ndef f(x: str) -> str: ...\ndef f(x):\n    return x\n")

    assert [c["symbol"] for c in discover_components(tmp_path)] == ["f"]


def test_discover_components_condenses_large_classes_within_budget(tmp_path):
    methods = "\n".join(
        f"    def method_{i}(self):\n        \"\"\"Doc {i}.\"\"\"\n        return {'x' * 700!r}\n" for i in range(40)
    )
    _write(tmp_path / "pkg" / "__init__.py", "")
    _write(tmp_path / "pkg" / "big.py", f"class Big:\n    def __init__(self):\n        self.v = 1\n\n{methods}")

    (component,) = discover_components(tmp_path)

    assert len(component["source"]) <= MAX_SOURCE_CHARS
    assert "def __init__(self):\n        self.v = 1" in component["source"]
    assert "def method_39(self):" in component["source"] and "Doc 39." in component["source"]


def test_discover_components_includes_definitions_imported_from_repo_modules(tmp_path):
    _write(tmp_path / "pkg" / "__init__.py", "")
    _write(tmp_path / "pkg" / "util.py", "class Base(type):\n    def __repr__(self):\n        return 'base'\n")
    _write(tmp_path / "pkg" / "state.py", "from .util import Base\n\nclass Idle(Base):\n    def go(self):\n        return 1\n")

    idle = next(c for c in discover_components(tmp_path) if c["symbol"] == "Idle")

    assert "# --- pkg.util.Base ---" in idle["source"] and "return 'base'" in idle["source"]


def test_check_alignment_accepts_reexporting_package_and_reference_only_use():
    component = {"module": "pkg._util", "symbol": "NEED_DATA"}
    plan = {"scenarios": [{"name": "test_marker"}]}
    code = "from pkg import NEED_DATA\n\ndef test_marker():\n    assert NEED_DATA is NEED_DATA\n"
    check_alignment(code, plan, component)
    module_style = "import pkg._util as util\n\ndef test_marker():\n    assert util.NEED_DATA is util.NEED_DATA\n"
    check_alignment(module_style, plan, component)
    with pytest.raises(ValueError, match="must import NEED_DATA"):
        check_alignment("from other import NEED_DATA\n\ndef test_marker():\n    assert NEED_DATA\n", plan, component)


def test_preflight_reports_unimportable_modules_without_install(tmp_path):
    _write(tmp_path / "pkg" / "__init__.py", "")
    _write(tmp_path / "pkg" / "good.py", "class Good:\n    pass\n")
    _write(tmp_path / "pkg" / "bad.py", "import definitely_missing_dependency_xyz\n\nclass Bad:\n    pass\n")

    ready, failed = preflight_components(tmp_path, discover_components(tmp_path), install=None)

    assert [c["symbol"] for c in ready] == ["Good"]
    assert failed[0]["component"] == "pkg.bad.Bad" and "definitely_missing_dependency_xyz" in failed[0]["error"]


def test_measure_coverage_reports_per_component_and_marks_unspecified(tmp_path):
    _write(tmp_path / "pkg" / "__init__.py", "")
    _write(tmp_path / "pkg" / "calc.py",
           "def covered(x):\n    return x + 1\n\n\ndef untouched(x):\n    if x:\n        return 1\n    return 2\n")
    _write(tmp_path / "tests" / "specs" / "001_pkg_calc_covered.md", "<!-- spec-plan engine=vio -->\n")
    _write(tmp_path / "tests" / "test_scripts" / "001_pkg_calc_covered_test.py",
           "from pkg.calc import covered\n\n\ndef test_covered():\n    assert covered(1) == 2\n")

    coverage = measure_coverage(tmp_path, discover_components(tmp_path))

    rows = {row["component"]: row for row in coverage["components"]}
    assert rows["pkg.calc.covered"]["percent"] == 100.0 and rows["pkg.calc.covered"]["specified"]
    assert rows["pkg.calc.untouched"]["percent"] == 16.7 and not rows["pkg.calc.untouched"]["specified"]
    assert rows["pkg.calc.untouched"]["uncovered_lines"] == "6-8"
    assert coverage["components"][0]["component"] == "pkg.calc.untouched"
    assert (tmp_path / "tests" / "coverage" / "coverage_summary.md").read_text(encoding="utf-8").count("`pkg.calc.") == 2


# ---- Re-running failed specs ----------------------------------------------------------

def _extract(response):
    return response.split("```python\n", 1)[1].rsplit("\n```", 1)[0]


FAILING_OUTPUT = ("pkg/test_widget.py::test_widget_edge FAILED\n"
                  "______ test_widget_edge ______\nt.py:5: in test_widget_edge\n    assert Widget().size() == 4\n"
                  "E   assert 3 == 4\n=== 1 failed ===")


def _failed(passed=0):
    return {"output": FAILING_OUTPUT, "passed": passed, "failed": 1, "errors": 0, "all_passed": False}


def _passed():
    return {"output": "1 passed", "passed": 1, "failed": 0, "errors": 0, "all_passed": True}


def _previous_failure(tmp_path, monkeypatch):
    monkeypatch.setattr("src.orchestrator.spec_pipeline.REPAIR_ROUNDS", 0)
    plan = {"objective": "o", "scenarios": [
        {"name": "test_widget_edge", "category": "Boundary", "inputs": "Widget()", "expected": "size is 4",
         "evidence": "return 3"}]}
    script = "```python\nfrom pkg.widget import Widget\n\ndef test_widget_edge():\n    assert Widget().size() == 4\n```"
    return process_component(
        tmp_path, WIDGET, ["unit_test"], ScriptedClient([json.dumps(plan)]), "master", _extract,
        lambda repo, name: _failed(), choose_writer=lambda p: (ScriptedClient([script]), "unit-agent", "unit-agent"),
        engine="vio",
    )


def test_rerun_diagnoses_then_repairs_only_the_failing_tests(tmp_path, monkeypatch):
    previous = _previous_failure(tmp_path, monkeypatch)
    assert previous["status"] == "FAILED" and previous["plan"]
    diagnosis = json.dumps({"cause": "wrong_expectation", "explanation": "size() returns 3, not 4.",
                            "fix": "assert size() == 3"})
    repaired = "```python\nfrom pkg.widget import Widget\n\ndef test_widget_edge():\n    assert Widget().size() == 3\n```"
    planner, writer = ScriptedClient([diagnosis]), ScriptedClient([repaired])
    runs = iter([_failed(), _passed()])

    result = rerun_component(
        tmp_path, WIDGET, previous, ["unit_test"], planner, "master", _extract, lambda repo, name: next(runs),
        choose_writer=lambda p: (writer, "unit-agent", "unit-agent"), engine="vio",
    )

    assert result["status"] == "COMPLETED" and result["rerun"] == "repaired"
    assert result["diagnosis"]["cause"] == "wrong_expectation"
    assert "VIO DIAGNOSIS: [wrong_expectation]" in writer.prompts[0]
    assert "Widget().size() == 3" in Path(result["script"]).read_text(encoding="utf-8")
    assert "VIO diagnosis:" in Path(result["spec"]).read_text(encoding="utf-8")


def test_rerun_does_not_rewrite_tests_when_a_source_bug_is_suspected(tmp_path, monkeypatch):
    previous = _previous_failure(tmp_path, monkeypatch)
    diagnosis = json.dumps({"cause": "source_bug_suspected", "explanation": "docstring says 4.", "fix": "n/a"})
    writer = ScriptedClient([])
    before = Path(previous["script"]).read_text(encoding="utf-8")

    result = rerun_component(
        tmp_path, WIDGET, previous, ["unit_test"], ScriptedClient([diagnosis]), "master", _extract,
        lambda repo, name: _failed(), choose_writer=lambda p: (writer, "unit-agent", "unit-agent"), engine="vio",
    )

    assert result["status"] == "FAILED" and result["error"].startswith("Needs review (source_bug_suspected)")
    assert writer.calls == [] and Path(previous["script"]).read_text(encoding="utf-8") == before


def test_rerun_is_a_no_op_for_llm_calls_when_tests_now_pass(tmp_path, monkeypatch):
    previous = _previous_failure(tmp_path, monkeypatch)
    planner, writer = ScriptedClient([]), ScriptedClient([])

    result = rerun_component(
        tmp_path, WIDGET, previous, ["unit_test"], planner, "master", _extract, lambda repo, name: _passed(),
        choose_writer=lambda p: (writer, "unit-agent", "unit-agent"), engine="vio",
    )

    assert result["status"] == "COMPLETED" and planner.calls == [] and writer.calls == []


def test_rerun_replans_a_planning_failure_with_a_smaller_prompt(tmp_path):
    big = {**WIDGET, "source": WIDGET["source"] + "# padding line\n" * 3000}
    plan = {"objective": "o", "scenarios": [
        {"name": "test_size", "category": "Happy Path", "inputs": "Widget()", "expected": "3", "evidence": "return 3"}]}
    script = "```python\nfrom pkg.widget import Widget\n\ndef test_size():\n    assert Widget().size() == 3\n```"
    planner, writer = ScriptedClient([json.dumps(plan)]), ScriptedClient([script])
    previous = {"component": "pkg.widget.Widget", "status": "FAILED", "failure_stage": "specification planning"}

    result = rerun_component(
        tmp_path, big, previous, ["unit_test"], planner, "master", _extract, lambda repo, name: _passed(),
        choose_writer=lambda p: (writer, "unit-agent", "unit-agent"), engine="vio",
    )

    assert result["status"] == "COMPLETED" and result["rerun"] == "replanned"
    assert len(planner.prompts[0]) < 16000 and "# ... (truncated)" in planner.prompts[0]


class GatewayErrorThenClient(ScriptedClient):
    def __init__(self, error, responses):
        super().__init__(responses)
        self.error = error

    def generate(self, model, prompt, format="", options=None):
        if not self.calls:
            self.calls.append((model, format))
            self.prompts.append(prompt)
            raise RuntimeError(self.error)
        return super().generate(model, prompt, format, options)


_PLAN_JSON = json.dumps({"objective": "o", "scenarios": [
    {"name": "test_size", "category": "Happy Path", "inputs": "Widget()", "expected": "3", "evidence": "return 3"}]})
_SIZE_SCRIPT = "```python\nfrom pkg.widget import Widget\n\ndef test_size():\n    assert Widget().size() == 3\n```"


def test_gateway_timeout_on_planning_falls_back_to_a_compact_request(tmp_path):
    big = {**WIDGET, "source": WIDGET["source"] + "# padding line\n" * 3000}
    planner = GatewayErrorThenClient("VIO agent master failed: HTTP 504: <html>", [_PLAN_JSON])
    writer = ScriptedClient([_SIZE_SCRIPT])

    result = process_component(
        tmp_path, big, ["unit_test"], planner, "master", _extract, lambda repo, name: _passed(),
        choose_writer=lambda p: (writer, "unit-agent", "unit-agent"), engine="vio",
    )

    assert result["status"] == "COMPLETED"
    assert len(planner.prompts[0]) > 20000 and len(planner.prompts[1]) < len(planner.prompts[0]) / 2
    assert "Keep the plan compact" in planner.prompts[1] and "Keep the plan compact" not in planner.prompts[0]


def test_non_transient_planning_errors_are_not_retried_with_a_compact_request(tmp_path):
    planner = GatewayErrorThenClient("VIO agent master failed: HTTP 400: bad request", [_PLAN_JSON])
    result = process_component(tmp_path, WIDGET, ["unit_test"], planner, "master", _extract,
                               lambda *a: {}, engine="vio")
    assert result["status"] == "FAILED" and result["failure_stage"] == "specification planning"
    assert len(planner.prompts) == 1


def test_describe_failure_marks_gateway_errors_retryable_and_never_untestable():
    gateway = describe_failure({"error": "VIO agent master failed: HTTP 504: <html>\n<head>",
                                "failure_stage": "specification planning"})
    assert "504" in gateway and "retryable" in gateway and "<html>" not in gateway
    assert "cannot be imported" in describe_failure({"failure_stage": "import preflight", "error": "x"})
    assert describe_failure({"execution": {"failed": 2, "errors": 1}}) == \
        "2 test(s) failed and 1 errored after repair attempts"


# ---- Meaningful-component queue ---------------------------------------------------------

def _queue_repo(tmp_path):
    _write(tmp_path / "pkg" / "__init__.py", "from .core import compute\n")
    _write(tmp_path / "pkg" / "core.py",
           "def compute(values, limit):\n"
           "    total = 0\n"
           "    for value in values:\n"
           "        if value > limit:\n"
           "            raise ValueError('too large')\n"
           "        total += value * 2\n"
           "    return total\n\n\n"
           "class Marker:\n    pass\n\n\n"
           "def tiny():\n    return 1\n")
    _write(tmp_path / "pkg" / "_hidden.py",
           "def internal(values):\n"
           "    out = []\n"
           "    for value in values:\n"
           "        if value:\n"
           "            out.append(value * 3)\n"
           "    return out\n")
    return tmp_path


def test_classify_components_keeps_meaningful_and_explains_exclusions(tmp_path):
    meaningful, excluded = classify_components(discover_components(_queue_repo(tmp_path)))

    assert [c["symbol"] for c in meaningful] == ["compute"]
    reasons = {row["component"]: row["reason"] for row in excluded}
    assert "no logic" in reasons["pkg.core.Marker"]
    assert "too little logic" in reasons["pkg.core.tiny"]
    assert "internal module" in reasons["pkg._hidden.internal"]


def test_select_components_resume_skips_components_that_already_have_a_spec(tmp_path):
    repo = _queue_repo(tmp_path)
    components = discover_components(repo)

    fresh = select_components(repo, components, resume=False)
    _write(repo / "tests" / "specs" / "001_pkg_core_compute.md", "<!-- spec-plan engine=vio -->\n")
    resumed = select_components(repo, components, resume=True)
    ignored = select_components(repo, components, resume=False)

    assert [c["symbol"] for c in fresh["pending"]] == ["compute"]
    assert resumed["pending"] == [] and resumed["already_specified"] == 1
    assert [c["symbol"] for c in ignored["pending"]] == ["compute"]


def test_discovered_components_keep_ranking_metadata(tmp_path):
    compute = next(c for c in discover_components(_queue_repo(tmp_path)) if c["symbol"] == "compute")

    assert compute["exported"] is True and compute["trivial"] is False and compute["weight"] > 20


# ---- Coverage target --------------------------------------------------------------------

import re
import subprocess
import sys

from src.orchestrator.spec_pipeline import (
    COVERAGE_TARGET, coverage_env, pick_gap_rows, raise_coverage, top_up_component, write_test_support,
)

CALC = "def covered(x):\n    return x + 1\n\n\ndef untouched(x):\n    if x:\n        return 1\n    return 2\n"


def _calc_repo(tmp_path):
    _write(tmp_path / "pkg" / "__init__.py", "")
    _write(tmp_path / "pkg" / "calc.py", CALC)
    _write(tmp_path / "tests" / "specs" / "001_pkg_calc_covered.md", "<!-- spec-plan engine=vio -->\n")
    _write(tmp_path / "tests" / "test_scripts" / "001_pkg_calc_covered_test.py",
           "from pkg.calc import covered\n\n\ndef test_covered():\n    assert covered(1) == 2\n")
    return tmp_path


def _run_pytest(repo, name):
    scripts = Path(repo).resolve() / "tests" / "test_scripts"
    write_test_support(repo)
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", str(scripts / name), "-c", str(scripts / "pytest.ini"),
         "--rootdir", str(scripts), f"--confcutdir={scripts}", "-p", "no:cacheprovider", "-v", "--tb=short"],
        capture_output=True, text=True, cwd=str(scripts), env=coverage_env(repo))
    out = proc.stdout + proc.stderr
    count = lambda word: int((re.search(rf"(\d+) {word}", out) or [0, 0])[1])
    return {"output": out, "passed": count("passed"), "failed": count("failed"), "errors": count("error"),
            "all_passed": proc.returncode == 0}


def _scenario_plan(name, inputs, expected, evidence):
    return json.dumps({"objective": "o", "scenarios": [
        {"name": name, "category": "Happy Path", "inputs": inputs, "expected": expected, "evidence": evidence}]})


def _code(body):
    return f"```python\nfrom pkg.calc import untouched\n\n{body}\n```"


def test_pick_gap_rows_takes_largest_gaps_until_the_target_is_plausible():
    rows = [{"component": f"c{i}", "missing": size} for i, size in enumerate([2, 30, 10])]
    coverage = {"components": rows, "units": 100, "covered_units": 50}

    assert [r["component"] for r in pick_gap_rows(coverage, 60)] == ["c1"]
    assert [r["component"] for r in pick_gap_rows(coverage, 60, attempted={"c1"})] == ["c2", "c0"]
    assert pick_gap_rows({"components": rows, "units": 100, "covered_units": 100}, 60) == [rows[1]]


def test_raise_coverage_specifies_untested_components_until_target_is_met(tmp_path):
    repo = _calc_repo(tmp_path)
    components = discover_components(repo)
    before = measure_coverage(repo, components)
    client = ScriptedClient([
        _scenario_plan("test_untouched_true", "untouched(1)", "1", "return 1"),
        _code("def test_untouched_true():\n    assert untouched(1) == 1"),
    ])

    def top_up(component, row):
        return top_up_component(repo, component, row, None, ["unit_test"], client, "m", _extract, _run_pytest)

    after = raise_coverage(repo, components, before, top_up)

    assert before["percent"] < COVERAGE_TARGET <= after["percent"]
    assert after["target_met"] and after["rounds"] == 1


def test_top_up_extends_a_passing_script_and_spec_without_losing_tests(tmp_path):
    repo = _calc_repo(tmp_path)
    component = next(c for c in discover_components(repo) if c["symbol"] == "untouched")
    first = process_component(
        repo, component, ["unit_test"],
        ScriptedClient([_scenario_plan("test_untouched_true", "untouched(1)", "1", "return 1"),
                        _code("def test_untouched_true():\n    assert untouched(1) == 1")]),
        "m", _extract, _run_pytest)
    assert first["status"] == "COMPLETED"
    row = next(r for r in measure_coverage(repo, discover_components(repo))["components"]
               if r["component"] == "pkg.calc.untouched")
    assert row["missing_lines"] == [8]
    client = ScriptedClient([
        _scenario_plan("test_untouched_false", "untouched(0)", "2", "return 2"),
        _code("def test_untouched_false():\n    assert untouched(0) == 2"),
    ])

    result = top_up_component(repo, component, row, first, ["unit_test"], client, "m", _extract, _run_pytest)

    assert result["status"] == "COMPLETED" and result["topup"] == "added 1 test(s)"
    script = Path(result["script"]).read_text(encoding="utf-8")
    assert "def test_untouched_true" in script and "def test_untouched_false" in script
    spec = Path(result["spec"]).read_text(encoding="utf-8")
    assert "test_untouched_false" in spec and "SC-02" in spec
    assert "8:     return 2\n" in client.prompts[0] and "test_untouched_true" in client.prompts[0]


def test_top_up_restores_files_when_the_new_tests_cannot_pass(tmp_path):
    repo = _calc_repo(tmp_path)
    component = next(c for c in discover_components(repo) if c["symbol"] == "untouched")
    first = process_component(
        repo, component, ["unit_test"],
        ScriptedClient([_scenario_plan("test_untouched_true", "untouched(1)", "1", "return 1"),
                        _code("def test_untouched_true():\n    assert untouched(1) == 1")]),
        "m", _extract, _run_pytest)
    row = next(r for r in measure_coverage(repo, discover_components(repo))["components"]
               if r["component"] == "pkg.calc.untouched")
    wrong = _code("def test_untouched_false():\n    assert untouched(0) == 99")
    client = ScriptedClient([_scenario_plan("test_untouched_false", "untouched(0)", "99", "return 2"),
                             wrong, wrong, wrong, wrong])
    before = (Path(first["script"]).read_text(encoding="utf-8"), Path(first["spec"]).read_text(encoding="utf-8"))

    result = top_up_component(repo, component, row, first, ["unit_test"], client, "m", _extract, _run_pytest)

    assert result["status"] == "COMPLETED" and result["topup"] == "no passing tests could be added"
    assert (Path(first["script"]).read_text(encoding="utf-8"), Path(first["spec"]).read_text(encoding="utf-8")) == before


# ---- Recurring generated-test mistakes ----------------------------------------------------

from src.orchestrator.spec_pipeline import failure_hints


def test_discovery_flags_pytest_fixtures_and_prompts_warn_against_direct_calls(tmp_path):
    _write(tmp_path / "pkg" / "__init__.py", "")
    _write(tmp_path / "pkg" / "plug.py",
           "import pytest\n\n@pytest.fixture\ndef cov(request):\n    if request:\n        return 1\n    return 2\n\n"
           "def plain(x):\n    if x:\n        return 1\n    return 2\n")
    by_name = {c["symbol"]: c for c in discover_components(tmp_path)}

    assert by_name["cov"]["fixture"] and not by_name["plain"]["fixture"]
    assert "calling it directly raises an error" in plan_prompt(by_name["cov"], ["unit_test"])
    assert "calling it directly" not in plan_prompt(by_name["plain"], ["unit_test"])
    plan = {"objective": "o", "preconditions": [], "scenarios": [
        {"id": "SC-01", "name": "test_a", "inputs": "i", "expected": "e", "evidence": "x", "requirement_ids": []}]}
    assert "__wrapped__" in script_prompt(by_name["cov"], plan, Path("001_x.md"))
    assert "must define each attribute the source reads" in script_prompt(by_name["plain"], plan, Path("001_x.md"))


def test_check_alignment_rejects_direct_calls_of_a_fixture_target_but_allows_unwrapping():
    component = {"module": "pkg.plug", "symbol": "cov", "fixture": True}
    plan = {"scenarios": [{"name": "test_a"}]}
    direct = "from pkg.plug import cov\n\ndef test_a():\n    assert cov(None) == 2\n"
    unwrapped = ("from pkg.plug import cov\n\ndef test_a():\n"
                 "    fn = getattr(cov, '__wrapped__', None) or cov._get_wrapped_function()\n"
                 "    assert fn(None) == 2\n")

    with pytest.raises(ValueError, match="must not be called directly"):
        check_alignment(direct, plan, component)
    check_alignment(unwrapped, plan, component)
    check_alignment(direct, plan, {**component, "fixture": False})


def test_failure_hints_recognise_recurring_generated_test_mistakes():
    code = "class NS:\n    pass\n\ndef test_a():\n    pass\n"

    assert "__wrapped__" in failure_hints('Fixture "cov" called directly. Fixtures are not meant', code)
    assert "stand-in object lacks an attribute" in failure_hints("E   AttributeError: 'NS' object has no attribute 'x'", code)
    assert "stand-in" not in failure_hints("E   AttributeError: 'Real' object has no attribute 'x'", code)
    assert "real signature" in failure_hints("E   TypeError: f() got an unexpected keyword argument 'realm'", code)
    assert "does not raise" in failure_hints("E   Failed: DID NOT RAISE TypeError", code)
    assert failure_hints("1 passed", code) == ""


def test_repair_prompt_carries_the_hint_for_a_directly_called_fixture(tmp_path):
    plan = {"objective": "o", "scenarios": [
        {"name": "test_widget_edge", "category": "Boundary", "inputs": "Widget()", "expected": "3", "evidence": "return 3"}]}
    initial = "```python\nfrom pkg.widget import Widget\n\ndef test_widget_edge():\n    assert Widget().size() == 3\n```"
    writer = ScriptedClient([initial, initial])
    failing = ('t.py::test_widget_edge FAILED\n______ test_widget_edge ______\nFixture "Widget" called directly. Fixtures are '
               "not meant to be called directly\n=== 1 failed ===")
    runs = iter([{"output": failing, "passed": 0, "failed": 1, "errors": 0, "all_passed": False}, _passed()])

    process_component(
        tmp_path, WIDGET, ["unit_test"], ScriptedClient([json.dumps(plan)]), "master", _extract,
        lambda repo, name: next(runs), choose_writer=lambda p: (writer, "unit-agent", "unit-agent"), engine="vio")

    assert "HINT: The target is a pytest fixture" in writer.prompts[1]


# ---- Semantic checks, setup preconditions, repair acceptance, passing-only coverage -------------

from src.orchestrator.spec_pipeline import render_coverage_markdown, semantic_gaps

SIZED = {"module": "pkg.sized", "symbol": "sized", "source": "def sized(items, limit=3):\n    return len(items[:limit])\n"}
STATE = {"module": "pkg.reg", "symbol": "lookup", "source_path": "pkg/reg.py",
         "source": "_REGISTRY = {}\n\ndef lookup(key):\n    return _REGISTRY[key]\n"}


def _plan_of(*names, expected="e", **extra):
    return {"scenarios": [{"name": name, "expected": expected} for name in names], **extra}


def test_check_alignment_rejects_tests_that_only_assert_truthiness_or_non_empty_length():
    for body in ("result = sized([1])\n    assert result", "assert len(sized([1])) > 0"):
        with pytest.raises(ValueError, match="truthiness"):
            check_alignment(f"from pkg.sized import sized\n\ndef test_a():\n    {body}\n", _plan_of("test_a"), SIZED)
    check_alignment("from pkg.sized import sized\n\ndef test_a():\n    assert sized([1, 2]) == 2\n",
                    _plan_of("test_a"), SIZED)


def test_check_alignment_rejects_calls_the_target_signature_cannot_accept():
    head = "from pkg.sized import sized\n\ndef test_a():\n"
    for call, problem in (("sized()", "omits required"), ("sized([1], 2, 3)", "positional"),
                          ("sized([1], size=2)", "does not accept")):
        with pytest.raises(ValueError, match=problem):
            check_alignment(f"{head}    assert {call} == 1\n", _plan_of("test_a"), SIZED)
    check_alignment(f"{head}    assert sized([1], limit=2) == 1\n", _plan_of("test_a"), SIZED)
    deliberate = "import pytest\nfrom pkg.sized import sized\n\ndef test_a():\n    with pytest.raises(TypeError):\n        sized()\n"
    check_alignment(deliberate, _plan_of("test_a"), SIZED)


def test_check_alignment_reads_the_constructor_of_a_class_target():
    box = {"module": "pkg.box", "symbol": "Box",
           "source": "class Box:\n    def __init__(self, width, *, depth):\n        self.width = width\n"}
    head = "from pkg.box import Box\n\ndef test_a():\n"
    with pytest.raises(ValueError, match="omits required"):
        check_alignment(f"{head}    assert Box(1).width == 1\n", _plan_of("test_a"), box)
    check_alignment(f"{head}    assert Box(1, depth=2).width == 1\n", _plan_of("test_a"), box)


def test_check_alignment_rejects_a_test_that_never_touches_the_target():
    code = "from pkg.sized import sized\n\ndef test_a():\n    assert sized([1]) == 1\n\ndef test_b():\n    assert 1 + 1 == 2\n"
    with pytest.raises(ValueError, match="test_b never exercises sized"):
        check_alignment(code, _plan_of("test_a", "test_b"), SIZED)


def test_semantic_gaps_flag_tests_that_skip_required_state_or_unchecked_exceptions():
    plan = _plan_of("test_hit", "test_miss", expected="raises KeyError",
                    preconditions=["_REGISTRY must contain the key 'a'"])
    code = ("import pytest\nfrom pkg.reg import lookup\n\n"
            "def test_hit(monkeypatch):\n    import pkg.reg as reg\n    monkeypatch.setitem(reg._REGISTRY, 'a', 1)\n"
            "    with pytest.raises(KeyError):\n        lookup('b')\n\n"
            "def test_miss():\n    assert lookup('a') == 1\n")

    assert semantic_gaps(code, plan, STATE) == [
        "test_miss: does not set up the precondition state _REGISTRY",
        "test_miss: never checks the expected KeyError",
    ]


def test_semantic_gaps_ignore_empty_state_helper_functions_and_negated_exceptions():
    component = {"module": "pkg.m", "symbol": "f", "source": "_CACHE = {}\n\ndef f(x):\n    return _helper(x)\n"}
    plan = _plan_of("test_a", expected="does not raise ValueError",
                    preconditions=["_CACHE is empty", "_helper is importable"])
    weak = "from pkg.m import f\n\ndef test_a():\n    assert f(1) is not None\n"

    assert semantic_gaps(weak, plan, component) == []
    assert "only asserts the result is not None" in semantic_gaps(weak, plan, component, advisory=True)[0]


def test_generate_aligned_code_regenerates_once_when_required_state_is_not_set_up():
    plan = _plan_of("test_a", preconditions=["_REGISTRY must contain the key 'a'"])
    bare = "from pkg.reg import lookup\n\ndef test_a():\n    assert lookup('a') == 1\n"
    fixed = ("from pkg.reg import lookup\nimport pkg.reg as reg\n\ndef test_a(monkeypatch):\n"
             "    monkeypatch.setitem(reg._REGISTRY, 'a', 1)\n    assert lookup('a') == 1\n")

    client = ScriptedClient([bare, fixed])
    assert generate_aligned_code(client, "m", "write", str, {}, plan, STATE) == fixed
    assert len(client.prompts) == 2 and "FINDINGS" in client.prompts[1] and "_REGISTRY" in client.prompts[1]

    unhelpful = ScriptedClient([bare, bare])
    assert generate_aligned_code(unhelpful, "m", "write", str, {}, plan, STATE) == bare
    assert len(unhelpful.prompts) == 2


def test_script_prompt_and_failure_hints_tell_the_writer_to_set_up_module_state():
    plan = {"objective": "o", "preconditions": ["_REGISTRY must contain the key 'a'"], "scenarios": [
        {"id": "SC-01", "name": "test_a", "inputs": "i", "expected": "e", "evidence": "x", "requirement_ids": []}]}

    prompt = script_prompt(STATE, plan, Path("001_x.md"))

    assert "State to set up inside EVERY test: _REGISTRY" in prompt and "monkeypatch.setitem" in prompt
    assert "monkeypatch.setitem" in failure_hints("E   KeyError: 'assert_called_with'", "")


_WIDGET_TESTS = ("test_widget_a", "test_widget_b", "test_widget_c")


def _widget_script(*bodies):
    tests = "\n\n".join(f"def {name}():\n    assert Widget().size() == {value}" for name, value in bodies)
    return f"```python\nfrom pkg.widget import Widget\n\n{tests}\n```"


def _widget_run(passed, failing):
    lines = "\n".join(f"t.py::{name} {'FAILED' if name in failing else 'PASSED'}" for name in _WIDGET_TESTS)
    return {"output": lines, "passed": passed, "failed": len(failing), "errors": 0, "all_passed": False}


def _repair_widget(tmp_path, scripts, runs):
    plan = {"objective": "o", "scenarios": [
        {"name": name, "category": "Happy Path", "inputs": "Widget()", "expected": "size is 3", "evidence": "return 3"}
        for name in _WIDGET_TESTS]}
    writer, executions = ScriptedClient(scripts), iter(runs)
    return process_component(
        tmp_path, WIDGET, ["unit_test"], ScriptedClient([json.dumps(plan)]), "master", _extract,
        lambda repo, name: next(executions), choose_writer=lambda p: (writer, "unit-agent", "unit-agent"), engine="vio")


def test_a_partly_repaired_script_is_kept_but_never_reported_as_repaired(tmp_path):
    a, b, c = _WIDGET_TESTS
    result = _repair_widget(
        tmp_path,
        [_widget_script((a, 3), (b, 4), (c, 5)), _widget_script((b, 3), (c, 6)), _widget_script((c, 7))],
        [_widget_run(1, (b, c)), _widget_run(2, (c,)), _widget_run(2, (c,))])

    assert result["status"] == "FAILED"
    assert result["repair_partial"] and not result.get("repair_applied")
    assert result["repair_unresolved"] == [c]
    final = Path(result["script"]).read_text(encoding="utf-8")
    assert "== 6" in final and "== 7" not in final
    assert result["review_flags"] == []


def test_a_repair_that_breaks_a_passing_test_is_rejected(tmp_path):
    a, b, c = _WIDGET_TESTS
    result = _repair_widget(
        tmp_path,
        [_widget_script((a, 3), (b, 4), (c, 5)), _widget_script((b, 3), (c, 3))],
        [_widget_run(1, (b, c)), _widget_run(2, (a,))])

    assert result["status"] == "FAILED"
    assert not result.get("repair_partial") and not result.get("repair_applied")
    assert result["repair_unresolved"] == [b, c]
    assert "== 4" in Path(result["script"]).read_text(encoding="utf-8")


def test_measure_coverage_counts_only_passing_tests_when_results_are_given(tmp_path):
    repo = _calc_repo(tmp_path)
    scripts = repo / "tests" / "test_scripts"
    _write(scripts / "002_pkg_calc_untouched_test.py",
           "from pkg.calc import untouched\n\n\ndef test_ok():\n    assert untouched(0) == 2\n\n\n"
           "def test_bad():\n    assert untouched(1) == 99\n")
    components = discover_components(repo)
    results = [
        {"script": str(scripts / "001_pkg_calc_covered_test.py"),
         "execution": {"passed": 1, "output": "x.py::test_covered PASSED"}},
        {"script": str(scripts / "002_pkg_calc_untouched_test.py"),
         "execution": {"passed": 1, "failed": 1, "output": "x.py::test_ok PASSED\nx.py::test_bad FAILED"}},
    ]

    everything = measure_coverage(repo, components)
    passing = measure_coverage(repo, components, results=results)

    percent = lambda coverage: next(r["percent"] for r in coverage["components"] if r["component"].endswith("untouched"))
    assert percent(everything) > percent(passing)
    assert passing["passing_only"] and "only tests that passed" in render_coverage_markdown(passing)
    assert "no generated test passes" in measure_coverage(repo, components, results=[])["error"]


def test_raise_coverage_skips_excluded_components_and_uses_the_given_measure():
    coverage = {"percent": 10.0, "units": 100, "covered_units": 10,
                "components": [{"component": "pkg.a", "missing": 50}, {"component": "pkg.b", "missing": 40}]}
    seen = []

    result = raise_coverage(
        ".", [{"module": "pkg", "symbol": "a"}, {"module": "pkg", "symbol": "b"}], coverage,
        lambda component, row: seen.append(row["component"]),
        measure=lambda path, components: {**coverage, "percent": 90.0}, exclude={"pkg.a"})

    assert seen == ["pkg.b"] and result["target_met"]


def test_check_alignment_ignores_test_helpers_nested_inside_a_test():
    code = ("from pkg.sized import sized\n\ndef test_a():\n"
            "    class Inner:\n        def test_go(self):\n            pass\n"
            "    assert sized([1]) == 1\n")

    check_alignment(code, _plan_of("test_a"), SIZED)


def test_discovery_skips_a_projects_own_test_module(tmp_path):
    _write(tmp_path / "pkg" / "__init__.py", "")
    _write(tmp_path / "pkg" / "core.py", "def real(x):\n    if x:\n        return 1\n    return 2\n")
    _write(tmp_path / "pkg" / "test.py", "class TestReal:\n    def test_it(self):\n        assert True\n")

    assert {c["module"] for c in discover_components(tmp_path)} == {"pkg.core"}


def test_failure_hints_explain_a_package_attribute_that_shadows_its_submodule():
    output = "E   AttributeError: type object 'parameterized' has no attribute 'param'"

    assert "importlib.import_module" in failure_hints(output, "")


def test_check_alignment_rejects_assertions_that_can_pass_without_checking():
    head = "from pkg.sized import sized\n\ndef test_a():\n"
    for assertion in ("sized([1]) == 1 or True", "(sized([1]) == 1) if hasattr(pytest, 'run') else True"):
        with pytest.raises(ValueError, match="can pass without checking"):
            check_alignment(f"{head}    assert {assertion}\n", _plan_of("test_a"), SIZED)
    check_alignment(f"{head}    assert sized([1]) == 1 or sized([1]) == 2\n", _plan_of("test_a"), SIZED)


def test_check_alignment_rejects_setattr_on_a_dict_but_allows_setitem():
    head = "from pkg.sized import sized\n\ndef test_a(monkeypatch):\n"
    for target in ("sized.__globals__", "sized.__dict__", "globals()"):
        with pytest.raises(ValueError, match="monkeypatch.setitem"):
            check_alignment(f"{head}    monkeypatch.setattr({target}, 'x', 1)\n    assert sized([1]) == 1\n",
                            _plan_of("test_a"), SIZED)
    check_alignment(f"{head}    monkeypatch.setitem(sized.__globals__, 'x', 1)\n    assert sized([1]) == 1\n",
                    _plan_of("test_a"), SIZED)


def test_check_alignment_rejects_a_module_alias_that_a_reexport_shadows(tmp_path):
    package = tmp_path / "pkg"
    package.mkdir()
    (package / "__init__.py").write_text("from .mod import mod\n", encoding="utf-8")
    (package / "mod.py").write_text("class mod:\n    pass\n\ndef run():\n    return 1\n", encoding="utf-8")
    component = {"module": "pkg.mod", "symbol": "run", "import_root": str(tmp_path)}
    plan = _plan_of("test_a")
    head = "from pkg.mod import run\n"

    for prefix in ("import pkg.mod as m\n", "from pkg import mod as m\n"):
        with pytest.raises(ValueError, match="sys.modules"):
            check_alignment(f"{head}{prefix}\ndef test_a():\n    assert m.run() == 1\n", plan, component)
    with pytest.raises(ValueError, match="sys.modules"):
        check_alignment(f"{head}import pkg.mod\n\ndef test_a():\n    assert pkg.mod.run() == 1\n", plan, component)
    check_alignment(f"{head}import sys\n\ndef test_a():\n    assert run() == sys.modules['pkg.mod'].run() == 1\n", plan, component)
    (package / "__init__.py").write_text("", encoding="utf-8")
    check_alignment(f"{head}import pkg.mod as m\n\ndef test_a():\n    assert m.run() == 1\n", plan, component)


def test_prompt_and_hints_cover_vacuous_assertions_and_dict_patching():
    assert "every assertion must be able to fail" in script_prompt(SIZED, {"objective": "o", "preconditions": [],
        "scenarios": [{"name": "test_a", "category": "Happy Path"}]}, Path("001_x.md"))
    assert "monkeypatch.setitem" in failure_hints("E   AttributeError: {'__name__': 'pkg.mod', '__doc__': None", "")


def test_process_and_rerun_report_each_stage_to_the_progress_callback(tmp_path, monkeypatch):
    stages = []
    monkeypatch.setattr("src.orchestrator.spec_pipeline.REPAIR_ROUNDS", 0)
    plan = {"objective": "o", "scenarios": [
        {"name": "test_widget_edge", "category": "Boundary", "inputs": "Widget()", "expected": "size is 3",
         "evidence": "return 3"}]}
    script = "```python\nfrom pkg.widget import Widget\n\ndef test_widget_edge():\n    assert Widget().size() == 3\n```"

    passing = process_component(
        tmp_path, WIDGET, ["unit_test"], ScriptedClient([json.dumps(plan)]), "master", _extract,
        lambda repo, name: _passed(), choose_writer=lambda p: (ScriptedClient([script]), "unit-agent", "unit-agent"),
        engine="vio", on_stage=stages.append)
    assert stages == ["plan", "script", "pytest"]

    stages.clear()
    failing = process_component(
        tmp_path, WIDGET, ["unit_test"], ScriptedClient([json.dumps(plan)]), "master", _extract,
        lambda repo, name: _failed(), choose_writer=lambda p: (ScriptedClient([script]), "unit-agent", "unit-agent"),
        engine="vio", on_stage=stages.append)
    assert stages == ["plan", "script", "pytest", "repair"]

    stages.clear()
    rerun_component(tmp_path, WIDGET, failing, ["unit_test"], ScriptedClient([]), "master", _extract,
                    lambda repo, name: _passed(), engine="vio", on_stage=stages.append)
    assert stages == ["pytest"] and passing["status"] == "COMPLETED"
