from src import workflow_utils as ca

REPOS = ["click", "flask", "itsdangerous"]


def test_full_request_in_one_message():
    parsed = ca.interpret_locally("Generate edge-case unit tests for the Signer in itsdangerous", REPOS)
    assert parsed["intent"] == "generate"
    assert parsed["repo_name"] == "itsdangerous"
    assert parsed["test_types"] == ["unit_test"]
    assert parsed["target"] == "Signer"


def test_repo_name_is_not_a_target():
    parsed = ca.interpret_locally("write tests for Flask", REPOS)
    assert "target" not in parsed
    assert parsed["repo_name"] == "flask"


def test_url_trimmed_to_owner_repo():
    parsed = ca.interpret_locally("cover https://github.com/pallets/click/tree/main with integration tests.", [])
    assert parsed["repo_url"] == "https://github.com/pallets/click"
    assert parsed["test_types"] == ["integration_test"]


def test_intents_and_counts():
    assert ca.interpret_locally("re-run the failed specs", REPOS)["intent"] == "rerun"
    assert ca.interpret_locally("how is it going?", REPOS)["intent"] == "status"
    parsed = ca.interpret_locally("do 5 components of click, then continue", REPOS)
    assert parsed["max_components"] == 5 and parsed["mode"] == "continue"


def test_missing_repo_asks_one_question_and_followup_completes():
    request = ca.merge_request(ca.new_request(), ca.interpret_locally("generate security tests", REPOS))
    assert "repository link" in ca.missing_question(request, REPOS)
    request = ca.merge_request(request, ca.interpret_locally("itsdangerous", REPOS))
    assert ca.missing_question(request, REPOS) == ""
    assert request["test_types"] == ["security"]


def test_how_many_is_a_count_question_not_a_run():
    parsed = ca.interpret_locally("if its run all components how many do we have?", REPOS)
    assert parsed["intent"] == "count"


def test_engine_is_detected_and_kept_across_messages():
    parsed = ca.interpret_locally("use Ollama please", REPOS)
    assert parsed["engine"] == "ollama"
    request = ca.merge_request(ca.merge_request(ca.new_request(), parsed), {"repo_name": "click"})
    assert request["engine"] == "ollama"


def test_new_repo_resets_target_and_scope():
    request = ca.merge_request(ca.new_request(), {"repo_name": "click", "target": "Command", "max_components": 3})
    request = ca.merge_request(request, {"repo_name": "flask"})
    assert request["target"] == "" and request["max_components"] == 0


def test_model_output_is_sanitised_and_failure_falls_back():
    parsed = ca.interpret("unit tests for flask", [], REPOS, ca.new_request(),
                          chat=lambda prompt, system=None: {"ok": True, "content": (
                              '{"intent":"generate","repo_name":"evil","test_types":["unit_test","bogus"],'
                              '"max_components":9999,"mode":"all","target":"x; rm"}')})
    assert parsed["repo_name"] == "flask" and parsed["test_types"] == ["unit_test"]
    assert "target" not in parsed and "max_components" not in parsed and "mode" not in parsed
    failing = ca.interpret("tests for flask", [], REPOS, ca.new_request(), chat=lambda p, system=None: {"ok": False})
    assert failing["repo_name"] == "flask"
    hallucinated = ca.interpret("tests for flask", [], REPOS, ca.new_request(), chat=lambda p, system=None: {
        "ok": True, "content": '{"intent":"generate","test_types":["unit_test"]}'})
    assert "test_types" not in hallucinated
