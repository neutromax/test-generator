"""Progress bookkeeping shared by the background run threads and the UI: phases, stages, ETA, pause and cancel."""
import time
from collections import Counter

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
