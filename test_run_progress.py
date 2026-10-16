from src import run_progress as progress


def test_eta_needs_two_finished_components_and_scales_with_remaining_work():
    state = {}
    progress.begin_components(state, total=10, done=2, now=1000.0)
    assert progress.eta_seconds(state, now=1100.0) is None
    state["done"] = 4
    assert progress.eta_seconds(state, now=1100.0) == 300.0
    state["done"] = 10
    assert progress.eta_seconds(state, now=1100.0) is None


def test_eta_excludes_time_spent_paused():
    state = {}
    progress.begin_components(state, total=10, done=0, now=0.0)
    progress.toggle_pause(state, now=50.0)
    progress.toggle_pause(state, now=150.0)
    state["done"] = 2
    assert state["paused_seconds"] == 100.0
    assert progress.eta_seconds(state, now=200.0) == 400.0


def test_phase_index_and_stage_counts():
    state = {"phase": "components"}
    assert progress.phase_index(state) == 1
    progress.record_stage(state, "a", "pytest")
    progress.record_stage(state, "b", "pytest")
    progress.record_stage(state, "c", "plan")
    assert progress.stage_counts(state) == {"plan": 1, "pytest": 2}
    progress.clear_stage(state, "a")
    assert progress.stage_counts(state)["pytest"] == 1
    state["finished"] = True
    assert progress.phase_index(state) == len(progress.PHASES)


def test_checkpoint_blocks_while_paused_and_reports_cancel():
    state = {"paused": True}
    waits = []

    def sleeper(_seconds):
        waits.append(1)
        if len(waits) == 3:
            progress.request_cancel(state)

    assert progress.checkpoint(state, sleep=sleeper) is True
    assert len(waits) == 3 and state["paused"] is False
    assert progress.checkpoint({}, sleep=sleeper) is False


def test_format_duration():
    assert [progress.format_duration(s) for s in (5, 65, 3700)] == ["5s", "1m 05s", "1h 1m"]
