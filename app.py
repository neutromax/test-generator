"""
Streamlit UI for the repository test generator.

This application provides a web-based interface for:
- Cloning GitHub repositories
- Generating test prompts from repository structure
- Automating clipboard copy
- Managing test result collection and packaging
- Displaying generated test artifacts (specs, scripts, coverage)
- ORCHESTRATOR: Master-Worker intelligent test generation with Ollama models
"""

from __future__ import annotations

import io
import json
import os
import re
import ast
import json
import subprocess
import sys
import time
import zipfile
import logging
import threading
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.parse import urlparse
from datetime import datetime
from typing import List, Dict, Any

import streamlit as st
import pandas as pd

# Import the copilot_bridge module for automation functions
from src import workflow_utils as copilot_bridge
from src import workflow_utils as chat_assistant
from src import workflow_utils as coverage_view
from src import workflow_utils as results_view
from src import workflow_utils as run_history
from src import workflow_utils as run_progress
from src import workflow_utils as side_by_side

from src.generate_test_prompt import (
    TOOL_DIR,
    build_prompt,
    ensure_repository,
    is_git_repository,
    repository_name,
)

# Import orchestrator components
from src.ollama_client import OllamaClient
from src.orchestrator.cache_manager import CacheManager
from src.orchestrator.model_selector import ModelSelector
from src.orchestrator.master_supervisor import MasterSupervisor
from src.orchestrator.task_router import TaskRouter, TaskType, TaskStatus
from src.orchestrator.spec_pipeline import (
    COVERAGE_TARGET,
    SPEC_MARKER_PREFIXES,
    coverage_env,
    coverage_options,
    discover_components,
    describe_failure,
    generate_suite,
    measure_coverage,
    preflight_components,
    process_component,
    raise_coverage,
    render_coverage_markdown,
    rerun_component,
    select_components,
    spec_slugs,
    summarize_coverage,
    top_up_component,
    write_test_support,
)

# VIO (Aumovio online agents) + environment loading
from src.vio_client import VIOClient, VIOGenerationAdapter, AGENTS, AGENT_MODELS, TYPE_TO_AGENT
from src.vio_client import VIOClient, AGENTS, AGENT_MODELS, TYPE_TO_AGENT
from src.test_runner import run_pytest, save_test_script, parse_worker_output, save_agent_output, save_test_result, validate_python_code, extract_test_cases_from_code, extract_test_spec_from_code

try:
    from streamlit_agraph import agraph, Node, Edge, Config
    _AGRAPH = True
except Exception:
    _AGRAPH = False

try:
    from dotenv import load_dotenv
    load_dotenv()  # load VIO_API_KEY / VIO_API_BASE / OLLAMA_HOST from .env
except Exception:
    pass

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


REPOSITORIES_DIR = TOOL_DIR / "repositories"
VIEW_CHAT, VIEW_RESULTS = "💬 Chat", "📊 Results"


def validate_repository_url(repository_url: str) -> tuple[bool, str]:
    """Validate a public or enterprise GitHub repository URL."""
    parsed = urlparse(repository_url.strip())
    host = (parsed.hostname or "").lower()
    path_parts = [part for part in parsed.path.strip("/").split("/") if part]

    if parsed.scheme not in {"http", "https"} or not host:
        return False, "Enter a complete GitHub URL beginning with https://."
    if host != "github.com" and "github" not in host:
        return False, "The URL must point to GitHub or a GitHub Enterprise server."
    if len(path_parts) != 2 or any(not re.fullmatch(r"[A-Za-z0-9_.-]+", part) for part in path_parts):
        return False, "Use the format https://github.com/owner/repository."
    return True, ""


def initialize_state() -> None:
    """Initialize all Streamlit session state variables with default values."""
    defaults = {
        "repository_name": "",
        "repository_path": "",
        "repository_url": "",
        "clone_status": "Not started",
        "prompt_status": "Not generated",
        "generated_prompt": "",
        "prompt_path": "",
        "approval_status": "Pending",  # Pending, Approved, Rejected
        "proceed_clicked": False,  # Track if Proceed button was clicked
        "test_results": None,  # Stores collected test results
        "auto_refresh_active": False,  # Track auto-refresh loop status
        "last_results_count": 0,  # Track changes for auto-refresh
        # Orchestrator state
        "selected_test_types": [],  # Selected test types for orchestrator
        "orchestrator_running": False,  # Is orchestrator currently running
        "task_router": None,  # TaskRouter instance
        "model_selector": None,  # ModelSelector instance
        "master_supervisor": None,  # MasterSupervisor instance
        "cache_manager": None,  # CacheManager instance
        "ollama_client": None,  # OllamaClient instance
        "ollama_health": False,  # Is Ollama server running
        "queue_status": None,  # Current queue status
        "execution_results": {},  # Results from each model execution
        "orchestration_exec_result": None,  # pytest run result for generated tests
        "dep_install_result": None,  # pip install result for repo dependencies
        "self_correct_result": None,  # self-correction loop summary
        # --- Dual-mode (VIO / Ollama) UI state ---
        "mode": None,  # None (landing page) | "app"
        "theme": "dark",  # "dark" | "light"
        "vio_endpoint": os.getenv("VIO_API_BASE", "https://vio.automotive-wan.com:446"),
        "vio_client": None,  # VIOClient instance
        "vio_connected": False,  # ping result
        "vio_failures": 0,  # consecutive API failures (for fallback suggestion)
        "selected_model": None,  # agent/model selected in the branch graph
        "models": {},  # shared per-model state (status/code/results/tokens/...)
        "vio_report": None,  # test_generation_master final report
    }
    for key, value in defaults.items():
        st.session_state.setdefault(key, value)


def initialize_orchestrator() -> bool:
    """
    Initialize orchestrator components (Ollama client, cache, model selector, etc.).
    
    Returns:
        True if all components initialized successfully, False otherwise
    """
    try:
        # Initialize Ollama client and check health
        if st.session_state["ollama_client"] is None:
            try:
                ollama = OllamaClient()
                logger.info("Testing Ollama connection...")
                
                if not ollama.health_check():
                    st.error("❌ Ollama server not running. Please ensure Ollama is running on localhost:11434")
                    logger.warning("Ollama health check failed")
                    return False
                
                # Try to list models to verify connectivity
                try:
                    models = ollama.list_models()
                    logger.info(f"✓ Connected to Ollama. Found {len(models)} models")
                    st.session_state["ollama_client"] = ollama
                    st.session_state["ollama_health"] = True
                except Exception as e:
                    logger.error(f"Failed to list models: {e}")
                    st.error(f"❌ Cannot list Ollama models: {str(e)}")
                    return False
                    
            except Exception as e:
                logger.error(f"Failed to initialize Ollama client: {e}")
                st.error(f"❌ Ollama initialization error: {str(e)}")
                return False
        
        # Initialize other orchestrator components
        if st.session_state["cache_manager"] is None:
            st.session_state["cache_manager"] = CacheManager()
        
        if st.session_state["model_selector"] is None:
            st.session_state["model_selector"] = ModelSelector(st.session_state["ollama_client"])
        
        if st.session_state["master_supervisor"] is None:
            st.session_state["master_supervisor"] = MasterSupervisor()
        
        if st.session_state["task_router"] is None:
            st.session_state["task_router"] = TaskRouter()
        
        return True
    
    except Exception as e:
        logger.error(f"Failed to initialize orchestrator: {e}")
        st.error(f"❌ Orchestrator initialization failed: {e}")
        return False


def display_orchestrator_section() -> None:
    """
    Display the intelligent test orchestration section.
    
    Features:
    - Test type selection (checkboxes)
    - Ollama server health check
    - Queue execution with progress
    - Master-Worker visualization
    - Results display from each model
    """
    if not st.session_state["repository_path"]:
        return
    
    st.markdown("---")
    st.markdown("### 🤖 Intelligent Test Orchestration")
    st.markdown("Generate tests using local Ollama models with Master-Worker supervision.")
    
    # Initialize orchestrator
    if not initialize_orchestrator():
        return
    
    # Show Ollama server status
    col1, col2 = st.columns([3, 1])
    
    with col1:
        st.markdown("#### Available Models & Test Types")
    
    with col2:
        try:
            ollama_client = st.session_state["ollama_client"]
            available_models = ollama_client.list_models()
            # Don't filter by 'loaded' - Ollama API doesn't provide that field
            # Just show all available models
            model_names = [m.name for m in available_models]
            
            if model_names:
                st.success(f"✅ Ollama: {len(model_names)} models available")
                # Log which models are available for debugging
                logger.info(f"Available models: {model_names}")
            else:
                st.warning("⚠️ No Ollama models available")
        except Exception as e:
            logger.error(f"Error checking Ollama models: {e}")
            st.error(f"❌ Error loading models: {str(e)}")
    
    # Test type selection with checkboxes
    st.markdown("##### Select Test Types to Generate:")
    
    col1, col2, col3 = st.columns(3)
    
    test_type_options = {
        "unit_test": "🧪 Unit Tests",
        "integration_test": "🔗 Integration Tests",
        "e2e_test": "🌐 E2E Tests",
        "api_test": "📡 API Tests",
        "security": "🔐 Security Tests",
        "vulnerability": "⚠️ Vulnerability Scan",
        "linting": "📋 Linting & Code Quality",
    }
    
    selected_types = []
    
    for idx, (task_key, display_name) in enumerate(test_type_options.items()):
        col = [col1, col2, col3][idx % 3]
        with col:
            if st.checkbox(display_name, key=f"test_type_{task_key}"):
                selected_types.append(task_key)
    
    st.session_state["selected_test_types"] = selected_types
    
    # Show model assignments
    if selected_types:
        st.markdown("---")
        st.markdown("##### Model Assignments:")
        
        model_selector = st.session_state["model_selector"]
        assignments = {}
        
        for test_type in selected_types:
            model = model_selector.select_model(test_type)
            if model:
                assignments[test_type] = model
        
        if assignments:
            assign_col1, assign_col2 = st.columns(2)
            for idx, (task_type, model) in enumerate(assignments.items()):
                col = assign_col1 if idx % 2 == 0 else assign_col2
                with col:
                    emoji_map = {
                        "unit_test": "🧪",
                        "integration_test": "🔗",
                        "e2e_test": "🌐",
                        "api_test": "📡",
                        "security": "🔐",
                        "vulnerability": "⚠️",
                        "linting": "📋",
                    }
                    emoji = emoji_map.get(task_type, "•")
                    st.info(f"{emoji} {task_type}\n→ **{model}**")
    
    # Run orchestrator button
    st.markdown("---")
    
    col1, col2, col3 = st.columns([2, 1, 1])
    
    with col1:
        if st.button("▶️ Run Test Orchestration", type="primary", use_container_width=True):
            if not selected_types:
                st.warning("⚠️ Please select at least one test type")
            else:
                run_test_orchestration(selected_types)
    
    with col2:
        if st.session_state["task_router"] and st.session_state["task_router"].tasks:
            status = st.session_state["task_router"].get_queue_status()
            st.metric("Tasks", f"{status['completed']}/{status['total_tasks']}")
    
    with col3:
        if st.button("📊 Queue Status", use_container_width=True):
            if st.session_state["task_router"] and st.session_state["task_router"].tasks:
                st.session_state["show_queue_status"] = True
    
    # Display queue status if requested
    if st.session_state.get("show_queue_status", False):
        display_queue_status()
    
    # Display execution results
    if st.session_state["execution_results"]:
        display_orchestration_results()


def run_test_orchestration(selected_types: List[str], components=None, inline_extras: bool = True) -> None:
    """Generate and execute one matching script for each component specification."""
    repo_path = st.session_state["repository_path"]
    client = st.session_state["ollama_client"]
    selector = st.session_state["model_selector"]
    model = selector.select_model(selected_types[0])
    if not model:
        st.error("No suitable installed Ollama model is available.")
        return
    scripts = Path(repo_path) / "tests" / "test_scripts"
    scripts.mkdir(parents=True, exist_ok=True)
    write_test_support(repo_path)
    results = {}
    st.session_state["execution_results"] = results
    st.session_state["orchestration_exec_result"] = None
    with st.status("Planning and testing repository components", expanded=True) as progress:
        st.write("Checking that repository modules import...")
        components, import_failures = preflight_components(
            repo_path, discover_components(repo_path) if components is None else components)
        for failure in import_failures:
            results[failure["component"]] = failure
            st.write(f"{failure['component']}: FAILED")
            st.warning(failure["error"])
        for result in generate_suite(
            repo_path, selected_types, client, model, extract_python_code, run_pytest_single,
            progress=lambda name: st.write(f"Working on {name}..."), components=components,
        ):
            task_id = Path(result["spec"]).stem if result.get("spec") else result["component"]
            results[task_id] = result
            st.write(f"{result['component']}: {result['status']}")
            if result.get("error"):
                st.warning(result["error"])
        failed = sum(result["status"] == "FAILED" for result in results.values())
        progress.update(label=f"{len(results)} components processed; {failed} coverage gaps", state="error" if failed else "complete")
    components = discover_components(repo_path)
    with st.spinner(f"Measuring source coverage and raising it to {COVERAGE_TARGET:g}%..."):
        def top_up(component, row):
            previous = next((r for r in results.values() if r["component"] == row["component"]), None)
            updated = top_up_component(
                repo_path, component, row, previous, selected_types, client, model,
                extract_python_code, run_pytest_single, engine="ollama")
            if updated and updated.get("component"):
                for key in [k for k, r in results.items() if r["component"] == updated["component"]]:
                    del results[key]
                results[Path(updated["spec"]).stem if updated.get("spec") else updated["component"]] = updated
            return updated

        coverage = raise_coverage(
            repo_path, components,
            measure_coverage(repo_path, components, results=list(results.values())), top_up,
            measure=lambda path, found: measure_coverage(path, found, results=list(results.values())),
            exclude={r["component"] for r in results.values() if r.get("script") and r.get("status") != "COMPLETED"})
    st.session_state["last_coverage"] = coverage
    report = Path(repo_path) / "tests" / "spec_traceability.json"
    report.write_text(json.dumps(list(results.values()), indent=2, default=str), encoding="utf-8")
    if inline_extras:
        st.download_button("Download specification traceability", data=report.read_text(encoding="utf-8"),
                           file_name=report.name, mime="application/json")
    if inline_extras:
        render_coverage_panel(coverage)


def render_coverage_panel(coverage: dict) -> None:
    """Show overall and per-component source coverage so the next batch of specs can target gaps."""
    if not coverage:
        return
    if coverage.get("error"):
        st.warning(f"Source coverage unavailable: {coverage['error']}")
        return
    st.markdown("#### Source Coverage")
    st.metric("Line + branch coverage", f"{coverage['percent']}%")
    if "target" in coverage:
        met = coverage["target_met"]
        (st.success if met else st.warning)(
            f"Target {coverage['target']:g}% {'met' if met else 'not met'} after {coverage.get('rounds', 0)} top-up round(s).")
    st.caption("Lowest coverage first. Components without a specification are the candidates for the next batch.")
    st.dataframe(
        [{"Component": row["component"], "Specified": "Yes" if row["specified"] else "No",
          "Coverage %": row["percent"], "Uncovered lines": row["uncovered_lines"]}
         for row in coverage["components"]],
        use_container_width=True, hide_index=True,
    )


def _legacy_run_test_orchestration(selected_types: List[str]) -> None:
    """
    Execute the test orchestration workflow.
    
    Steps:
    1. Create tasks for selected types
    2. Build execution queue (topological sort)
    3. Get next batch of parallelizable tasks
    4. For each task: select model, generate, validate
    5. Cache results
    6. Display progress and results
    """
    # Each run gets a fresh queue so prior tasks cannot be reprocessed or shown
    # as current results after the user changes the selected test types.
    task_router = TaskRouter()
    st.session_state["task_router"] = task_router
    st.session_state["execution_results"] = {}
    st.session_state["show_queue_status"] = False
    model_selector = st.session_state["model_selector"]
    master_supervisor = st.session_state["master_supervisor"]
    cache_manager = st.session_state["cache_manager"]
    ollama_client = st.session_state["ollama_client"]
    repo_path = st.session_state["repository_path"]

    # Clear stale generated artifacts from previous runs so old/broken test files
    # (e.g. a truncated e2e test) don't get collected by pytest this run.
    for sub in ("test_scripts", "orchestration_results"):
        stale_dir = Path(repo_path) / "tests" / sub
        if stale_dir.exists():
            for f in stale_dir.glob("*"):
                try:
                    if f.is_file():
                        f.unlink()
                except Exception:
                    pass

    # Create tasks
    task_types = [TaskType(t) for t in selected_types]
    task_router.create_tasks(task_types)
    
    # Build execution queue
    queue = task_router.build_execution_queue()
    
    with st.status("🚀 Running Test Orchestration", expanded=True) as status_container:
        execution_results = {}
        
        # Process batches
        batch_num = 0
        while True:
            batch = task_router.get_next_batch()
            
            if not batch:
                break
            
            batch_num += 1
            st.write(f"**Batch {batch_num}:** {', '.join(batch)}")
            
            # Process each task in the batch
            batch_results = {}
            
            for task_id in batch:
                task = task_router.get_task(task_id)
                task_type_str = task.task_type.value
                
                # Check cache
                repo_hash = cache_manager.get_repo_hash(repo_path)
                file_hashes = cache_manager.get_file_hashes([repo_path])
                
                if cache_manager.is_cached(repo_hash, task_type_str, file_hashes):
                    cached_path = cache_manager.get_cached_result(repo_hash, task_type_str)
                    st.write(f"  ✅ {task_id}: Using cached result")
                    task_router.mark_task_skipped(task_id, "Cached")
                    batch_results[task_id] = {
                        "status": "SKIPPED",
                        "path": cached_path,
                        "model": "cache",
                        "validation_level": "cached",
                    }
                    continue
                
                # Mark in progress
                cache_manager.mark_in_progress(repo_hash, task_type_str)
                
                # Select model
                model = model_selector.select_model(task_type_str)
                if not model:
                    st.error(f"  ❌ {task_id}: No available model")
                    error_msg = (
                        f"No installed Ollama model matches the routing for {task_type_str}. "
                        "Install one of the configured fallback models and retry."
                    )
                    task_router.mark_task_failed(task_id, error_msg)
                    batch_results[task_id] = {
                        "status": "FAILED",
                        "error": error_msg,
                        "model": "none",
                    }
                    continue
                
                # Generate test
                try:
                    st.write(f"  🔄 {task_id} ({model})...")
                    
                    # Create prompt for this task
                    prompt = create_task_prompt(task_type_str, repo_path)
                    
                    # Generate with Ollama
                    start_time = time.time()
                    output, metrics = ollama_client.generate(model, prompt)
                    duration_ms = (time.time() - start_time) * 1000
                    
                    # Validate output
                    validation_result = master_supervisor.validate_worker_output(
                        task_type_str, output, model
                    )
                    
                    if validation_result.is_approved:
                        # Save result
                        result_path = save_orchestration_result(
                            repo_path, task_id, output
                        )
                        
                        # Cache result
                        cache_manager.mark_complete(
                            repo_hash, task_type_str, result_path, file_hashes
                        )
                        
                        # Record model performance
                        model_selector.record_execution(
                            model, task_type_str, duration_ms, True
                        )
                        
                        st.write(f"  ✅ {task_id}: Generated ({duration_ms:.0f}ms)")
                        task_router.mark_task_complete(task_id, result_path)
                        batch_results[task_id] = {
                            "status": "COMPLETED",
                            "path": result_path,
                            "model": model,
                            "duration_ms": duration_ms,
                            "validation_level": validation_result.level.value,
                            "prompt": prompt,
                        }
                    
                    elif validation_result.needs_correction:
                        # Apply correction
                        st.write(f"  ⚠️ {task_id}: Needs correction, retrying...")
                        
                        corrected_output, _ = ollama_client.generate(
                            model, validation_result.correction_prompt
                        )

                        corrected_validation = master_supervisor.validate_worker_output(
                            task_type_str, corrected_output, model
                        )
                        if not corrected_validation.is_approved:
                            error_msg = (
                                f"Correction remained {corrected_validation.level.value}: "
                                f"{corrected_validation.error_message or 'quality checks still failed'}"
                            )
                            st.error(f"  ❌ {task_id}: {error_msg}")
                            cache_manager.mark_failed(repo_hash, task_type_str, error_msg)
                            task_router.mark_task_failed(task_id, error_msg)
                            batch_results[task_id] = {
                                "status": "FAILED",
                                "error": error_msg,
                                "model": model,
                                "validation_level": corrected_validation.level.value,
                                "prompt": validation_result.correction_prompt,
                            }
                            continue
                        
                        result_path = save_orchestration_result(
                            repo_path, task_id, corrected_output
                        )
                        
                        cache_manager.mark_complete(
                            repo_hash, task_type_str, result_path, file_hashes
                        )
                        
                        st.write(f"  ✅ {task_id}: Corrected and saved")
                        task_router.mark_task_complete(task_id, result_path)
                        batch_results[task_id] = {
                            "status": "COMPLETED",
                            "path": result_path,
                            "model": model,
                            "duration_ms": duration_ms,
                            "validation_level": corrected_validation.level.value,
                            "prompt": validation_result.correction_prompt,
                        }
                    
                    else:
                        # Failed validation
                        error_msg = validation_result.error_message or "Output validation failed"
                        st.error(f"  ❌ {task_id}: {error_msg}")
                        cache_manager.mark_failed(repo_hash, task_type_str, error_msg)
                        task_router.mark_task_failed(task_id, error_msg)
                        batch_results[task_id] = {
                            "status": "FAILED",
                            "error": error_msg,
                            "model": model,
                            "validation_level": validation_result.level.value,
                            "prompt": prompt,
                        }
                
                except Exception as e:
                    error_msg = f"Execution error: {str(e)}"
                    st.error(f"  ❌ {task_id}: {error_msg}")
                    cache_manager.mark_failed(repo_hash, task_type_str, error_msg)
                    task_router.mark_task_failed(task_id, error_msg)
                    batch_results[task_id] = {
                        "status": "FAILED",
                        "error": error_msg,
                        "model": model,
                        "prompt": prompt,
                    }
            
            execution_results.update(batch_results)
        
        # Final status
        final_status = task_router.get_queue_status()
        status_msg = (
            f"✅ Orchestration Complete!\n\n"
            f"Completed: {final_status['completed']}, "
            f"Failed: {final_status['failed']}, "
            f"Skipped: {final_status['skipped']}"
        )
        status_container.update(label="✅ Orchestration Complete", state="complete")
        st.write(status_msg)
    
    st.session_state["execution_results"] = execution_results
    st.success("✅ Test generation complete!")


def discover_package_import_root(repo_path: Path) -> tuple[Path, str]:
    """Find the import root and top-level package name for a repo.

    Returns (import_root, package_name). import_root is the directory to add to
    sys.path; package_name is the importable top-level package (may be "").
    """
    # Prefer a src/ layout: repo/src/<package>/__init__.py
    src_dir = repo_path / "src"
    candidates = []
    if src_dir.is_dir():
        for child in src_dir.iterdir():
            if child.is_dir() and (child / "__init__.py").exists():
                candidates.append((src_dir, child.name))
    # Fall back to a top-level package: repo/<package>/__init__.py
    if not candidates:
        for child in repo_path.iterdir():
            if child.is_dir() and (child / "__init__.py").exists() and child.name != "tests":
                candidates.append((repo_path, child.name))
    if candidates:
        return candidates[0]
    return (repo_path, "")


def gather_source_context(repo_path: str, max_chars: int = 3000) -> tuple[str, str]:
    """Collect real source code from the repo to ground test generation.

    Picks small, function-rich modules from the package and returns
    (context_text, package_name). context_text includes the module import path
    and its source, bounded by max_chars so it fits the model context window.
    """
    repo = Path(repo_path)
    import_root, package = discover_package_import_root(repo)

    search_dir = (import_root / package) if package else import_root
    # Include __init__.py — some packages (e.g. `inflection`) put their entire
    # public API there. Only skip test files and very large modules.
    py_files = [
        p for p in search_dir.rglob("*.py")
        if "test" not in p.name.lower()
        and p.stat().st_size < 12000  # skip huge modules
    ]

    def score(p: Path) -> int:
        try:
            text = p.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            return -1
        # Prefer modules with public functions/classes, penalize very large ones.
        return text.count("\ndef ") + 2 * text.count("\nclass ") - len(text) // 4000

    py_files.sort(key=score, reverse=True)

    chunks: list[str] = []
    total = 0
    for p in py_files:
        try:
            text = p.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        # Build a dotted module path relative to the import root. For __init__.py,
        # use the package directory name (e.g. inflection/__init__.py -> "inflection").
        rel = p.relative_to(import_root)
        if rel.name == "__init__.py":
            parts = rel.parts[:-1]  # drop "__init__.py"
        else:
            parts = rel.with_suffix("").parts
        module = ".".join(parts) if parts else (package or rel.stem)
        snippet = f"# ===== Module: {module} =====\n{text.strip()}\n"
        if total + len(snippet) > max_chars and chunks:
            break
        chunks.append(snippet)
        total += len(snippet)
        if total >= max_chars:
            break

    return ("\n".join(chunks), package)


def build_symbol_index(source_context: str) -> str:
    """Build an explicit list of importable symbols per module from gathered source.

    Parses the '# ===== Module: X =====' headers and the top-level `class`/`def`
    names under each, so the prompt can tell the model exactly what may be imported.
    """
    lines = source_context.splitlines()
    current_module = None
    index: dict[str, list[str]] = {}
    for line in lines:
        header = re.match(r"#\s*=+\s*Module:\s*(\S+)\s*=+", line)
        if header:
            current_module = header.group(1)
            index.setdefault(current_module, [])
            continue
        if current_module is None:
            continue
        # Top-level (no indentation) class/def definitions are the public API.
        m = re.match(r"(?:class|def)\s+([A-Za-z_][A-Za-z0-9_]*)", line)
        if m and not m.group(1).startswith("_"):
            index[current_module].append(m.group(1))

    parts = []
    for module, names in index.items():
        if names:
            unique = ", ".join(dict.fromkeys(names))
            parts.append(f"- from {module} import {unique}")
    return "\n".join(parts)


def create_task_prompt(task_type: str, repo_path: str, use_local_templates: bool = True) -> str:
    """Create a task prompt grounded in repository code.

    ENHANCED v2 (100% Coverage Mode):
    1. Optionally reads enhanced prompt from /src/prompts/{task_type}_test.md
    2. Appends the actual repository source code (ground truth)
    3. Adds explicit list of importable symbols
    4. Falls back to dynamic prompt if file doesn't exist

    Args:
        task_type: test category
        repo_path: repository root path
        use_local_templates: True for Ollama flow, False for VIO flow
    """
    repo_path_obj = Path(repo_path)
    source_context, package = gather_source_context(str(repo_path_obj))

    if not use_local_templates:
        # VIO agents already have cloud-side system prompts; send compact, grounded task context only.
        if source_context:
            symbol_index = build_symbol_index(source_context)
            allowed_block = (
                f"IMPORTABLE SYMBOLS:\n{symbol_index}\n\n" if symbol_index else ""
            )
            return (
                f"TASK TYPE: {task_type}\n"
                "Use your configured cloud agent instructions to generate tests from the repository source.\n\n"
                f"{allowed_block}"
                f"REPOSITORY SOURCE CODE (ground truth)\n```python\n{source_context}\n```\n"
            )
        return (
            f"TASK TYPE: {task_type}\n"
            "Use your configured cloud agent instructions and generate tests for this repository."
        )

    # STEP 1 (Ollama): Try to read enhanced prompt from /src/prompts/
    # Map task_type to prompt filename
    prompt_file_map = {
        "unit_test": "unit_test.md",
        "integration_test": "integration_test.md",
        "e2e_test": "e2e_test.md",
        "api_test": "e2e_test.md",
        "security": "security_test.md",
        "vulnerability": "security_test.md",
    }
    
    prompt_filename = prompt_file_map.get(task_type, f"{task_type}_test.md")
    enhanced_prompt_path = Path(__file__).parent / "src" / "prompts" / prompt_filename
    
    if enhanced_prompt_path.exists():
        try:
            enhanced_prompt = enhanced_prompt_path.read_text(encoding="utf-8")
            logger.info(f"✓ Using enhanced v2 prompt from {enhanced_prompt_path.name}")
            
            # Append source code to enhanced prompt
            if source_context:
                symbol_index = build_symbol_index(source_context)
                allowed_block = (
                    f"\n## IMPORTABLE SYMBOLS\nimport ONLY names from this exact list:\n{symbol_index}\n\n"
                    if symbol_index else ""
                )
                source_block = (
                    f"\n## REPOSITORY SOURCE CODE (ground truth)\n```python\n{source_context}\n```\n"
                )
                return enhanced_prompt + allowed_block + source_block
            else:
                return enhanced_prompt
        except Exception as e:
            logger.warning(f"⚠️ Failed to read enhanced prompt from {enhanced_prompt_path}: {e}")
            # Fall through to dynamic prompt generation below

    # FALLBACK: Dynamic prompt generation (old behavior)
    logger.info(f"ℹ️ Ollama template prompt not found, using dynamic prompt generation for {task_type}")
    
    # High-level guidance from the repo's Test_Prompt.md (if present)
    guidance = ""
    repo_test_prompt = repo_path_obj / "Test_Prompt.md"
    if repo_test_prompt.exists():
        try:
            full = repo_test_prompt.read_text(encoding="utf-8")
            guidance = full.strip()[:600]
        except Exception:
            guidance = ""

    if source_context:
        symbol_index = build_symbol_index(source_context)
        allowed_block = (
            f"IMPORTABLE SYMBOLS — import ONLY names from this exact list:\n{symbol_index}\n\n"
            if symbol_index else ""
        )
        guidance_block = (
            f"PROJECT TESTING GOALS (high-level context):\n{guidance}\n\n"
            if guidance else ""
        )
        return (
            f"You are an expert Python test engineer. Write **{task_type}** tests using pytest "
            f"for the ACTUAL source code shown below.\n\n"
            f"{guidance_block}"
            f"STRICT RULES:\n"
            f"- Import ONLY the names listed under 'IMPORTABLE SYMBOLS'. These are the only "
            f"names that exist. Do NOT import or reference any other name.\n"
            f"- Include EVERY import your tests need at the top (standard library and "
            f"third-party), e.g. `from datetime import datetime`, `from uuid import UUID`, "
            f"`from markupsafe import Markup`. Never use a name you did not import.\n"
            f"- If you are unsure a name exists, do not use it.\n"
            f"- Only test functions/classes that actually appear in the code below.\n"
            f"- Write EXACTLY 3 short tests so your answer is COMPLETE and not cut off. "
            f"Keep the whole answer concise (under ~30 lines).\n"
            f"- Use the AAA pattern and name tests test_*.\n\n"
            f"OUTPUT FORMAT (choose one):\n"
            f"OPTION A (preferred): Wrap in ```json with this structure:\n"
            f"{{\n"
            f'  "cases": [{{"name": "test_example", "input": "sample", "expected": "result", "category": "unit"}}],\n'
            f'  "spec": {{"type": "{task_type}", "file": "module.py", "func": "func", "imports_used": [], "assumptions": "", "fixtures": "", "edge_cases_covered": []}},\n'
            f'  "script": "import pytest\\n\\ndef test_example():\\n    pass"\n'
            f"}}\n\n"
            f"OPTION B (fallback): Just output a single ```python code block:\n"
            f"```python\n"
            f"import pytest\n\n"
            f"def test_example():\n"
            f"    pass\n"
            f"```\n\n"
            f"{allowed_block}"
            f"===== REPOSITORY SOURCE (ground truth) =====\n"
            f"{source_context}\n"
            f"===== END SOURCE =====\n"
        )

    # Fallback when no source could be gathered.
    return (
        f"You are an expert test engineer. Generate comprehensive {task_type} "
        f"tests as valid, runnable pytest Python code for the repository located "
        f"at {repo_path_obj}. Include multiple test functions named test_*, "
        f"use the AAA pattern, and wrap all code in ```json code blocks with the structure: "
        f'{{"cases": [...], "spec": {{...}}, "script": "..."}}'
    )






def extract_python_code(content: str) -> str:
    """Extract Python source from an LLM markdown response.

    Handles three cases:
    1. One or more complete ```python ... ``` fenced blocks (preferred).
    2. Generic ``` blocks (if no python/py tags) - but skip JSON blocks.
    3. No fences: return empty string (don't return raw JSON/content).
    """
    if not content:
        return ""
    
    # Case 1: Look FIRST for explicit ```python or ```py tagged blocks.
    blocks = re.findall(r"```(?:python|py)\s*\n(.*?)```", content, re.DOTALL)
    if blocks:
        combined = "\n\n".join(b.strip("\n") for b in blocks if b.strip())
        if combined:
            logger.debug(f"extract_python_code: Found {len(blocks)} ```python blocks, {len(combined)} chars extracted")
            return combined

    # Case 2: Generic ``` blocks, but NOT ```json or other non-python blocks.
    generic_blocks = re.findall(r"```\s*\n(.*?)```", content, re.DOTALL)
    logger.debug(f"extract_python_code: Found {len(generic_blocks)} generic code blocks")
    
    python_blocks = []
    
    for i, block in enumerate(generic_blocks):
        block_stripped = block.strip()
        if not block_stripped:
            continue
        
        # Skip JSON/list blocks (they start with JSON indicators)
        if block_stripped.startswith(("{", "[")):
            logger.debug(f"  Block {i}: Skipped (JSON/list)")
            continue
        
        # Heuristics to identify Python code (not config, not yaml, not sql)
        # Look for common Python patterns
        is_python = any([
            "def " in block_stripped,
            "class " in block_stripped,
            "import " in block_stripped,
            "from " in block_stripped,
            "@" in block_stripped and "def " in block_stripped,  # decorators
            "assert " in block_stripped,
            "if __name__" in block_stripped,
        ])
        
        if is_python:
            logger.debug(f"  Block {i}: Identified as Python ({len(block_stripped)} chars)")
            python_blocks.append(block_stripped)
        else:
            logger.debug(f"  Block {i}: Not Python-like ({len(block_stripped)} chars, starts with: {block_stripped[:50]})")
    
    if python_blocks:
        result = "\n\n".join(python_blocks)
        logger.debug(f"extract_python_code: Extracted {len(python_blocks)} Python blocks, total {len(result)} chars")
        return result
    
    # Case 3: No valid Python code blocks found. Return empty string rather than raw content.
    logger.debug(f"extract_python_code: No Python code blocks found in {len(content)} char response")
    return ""



def save_orchestration_result(repo_path: str, task_id: str, content: str) -> str:
    """Save orchestration result to file.

    Saves the full model response as Markdown (for review) and, when the response
    contains Python code, also writes a runnable ``{task_id}_test.py`` into
    ``tests/test_scripts/`` so it can be executed with pytest.
    """
    repo_path_obj = Path(repo_path)
    results_dir = repo_path_obj / "tests" / "orchestration_results"
    results_dir.mkdir(parents=True, exist_ok=True)
    
    filename = f"{task_id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.md"
    result_path = results_dir / filename
    
    result_path.write_text(content, encoding="utf-8")

    # Also emit a runnable .py test file when code is present.
    code = extract_python_code(content)
    if code and ("def test" in code or "import" in code):
        scripts_dir = repo_path_obj / "tests" / "test_scripts"
        scripts_dir.mkdir(parents=True, exist_ok=True)
        script_path = scripts_dir / f"{task_id}_test.py"
        header = (
            f"# Auto-generated by orchestrator for task '{task_id}'\n"
            f"# Source: {result_path.name}\n\n"
        )
        script_path.write_text(header + code + "\n", encoding="utf-8")

    return str(result_path)



def display_queue_status() -> None:
    """Display current queue status and metrics."""
    task_router = st.session_state["task_router"]
    
    if not task_router or not task_router.tasks:
        st.info("No tasks in queue")
        return
    
    st.markdown("---")
    st.markdown("### 📊 Queue Status")
    
    status = task_router.get_queue_status()
    
    # Progress bar
    completion_pct = float(status["completion_percent"].rstrip("%"))
    st.progress(completion_pct / 100, text=f"{status['completion_percent']} Complete")
    
    # Metrics
    col1, col2, col3, col4 = st.columns(4)
    
    with col1:
        st.metric("Total Tasks", status["total_tasks"])
    
    with col2:
        st.metric("Completed", f"{status['completed']} ✅")
    
    with col3:
        st.metric("Failed", f"{status['failed']} ❌")
    
    with col4:
        st.metric("Skipped", f"{status['skipped']} ⊘")
    
    # Queue order
    st.markdown("##### Execution Queue Order:")
    queue_text = " → ".join(status["queue_order"])
    st.code(queue_text, language="text")
    
    # Current batch
    if status["current_batch"]:
        st.markdown("##### Currently Executing:")
        for task_id in status["current_batch"]:
            st.info(f"⏳ {task_id}")
    
    # Estimated remaining time
    st.markdown(f"**Estimated Remaining:** {status['estimated_remaining_sec']:.0f}s")


def display_orchestration_results() -> None:
    """Display results from orchestration execution."""
    if not st.session_state["execution_results"]:
        return
    
    st.markdown("---")
    st.markdown("### 📊 Orchestration Results")
    
    results = st.session_state["execution_results"]
    aligned = [
        result for result in results.values()
        if result.get("spec") and result.get("script")
    ]
    if aligned:
        st.markdown("#### 📚 Aligned Specs, Scripts & Pytest Results")
        st.caption(
            "Every specification has exactly one matching test script and pytest result "
            "sharing the same numeric prefix and feature name."
        )
        for result in aligned:
            spec_path = Path(result["spec"])
            script_path = Path(result["script"])
            if not spec_path.exists():
                continue
            stem = spec_path.stem
            with st.expander(f"📦 {stem}", expanded=False):
                spec_content = spec_path.read_text(encoding="utf-8")
                st.markdown("**Specification**")
                st.markdown(spec_content)
                st.download_button(
                    "⬇️ Download specification",
                    data=spec_content,
                    file_name=spec_path.name,
                    mime="text/markdown",
                    key=f"dl_spec_{stem}",
                )
                if script_path.exists():
                    script_content = script_path.read_text(encoding="utf-8")
                    st.markdown(f"**Test script** — `{script_path.name}`")
                    st.code(script_content, language="python")
                    st.download_button(
                        "⬇️ Download test script",
                        data=script_content,
                        file_name=script_path.name,
                        mime="text/x-python",
                        key=f"dl_script_{stem}",
                    )
                pytest_output = (result.get("execution") or {}).get("output", "")
                if pytest_output:
                    st.markdown("**Pytest result**")
                    st.code(pytest_output, language="text")
                    st.download_button(
                        "⬇️ Download pytest output",
                        data=pytest_output,
                        file_name=f"{stem}_pytest.txt",
                        mime="text/plain",
                        key=f"dl_pytest_{stem}",
                    )

        # Aligned bundle: specs + matching scripts + pytest results in one ZIP
        bundle = io.BytesIO()
        with zipfile.ZipFile(bundle, "w", zipfile.ZIP_DEFLATED) as archive:
            for result in aligned:
                spec_path = Path(result["spec"])
                if spec_path.exists():
                    archive.write(spec_path, f"specs/{spec_path.name}")
                if result.get("script") and Path(result["script"]).exists():
                    archive.write(result["script"], f"test_scripts/{Path(result['script']).name}")
                pytest_output = (result.get("execution") or {}).get("output")
                if pytest_output:
                    archive.writestr(f"pytest_results/{spec_path.stem}_pytest.txt", pytest_output)
                if result.get("path") and Path(result["path"]).exists():
                    archive.write(result["path"], f"orchestration_results/{Path(result['path']).name}")
        st.download_button(
            "📦 Download aligned bundle (specs + scripts + pytest results)",
            data=bundle.getvalue(),
            file_name=f"{st.session_state.get('repository_name') or 'repository'}_aligned_tests.zip",
            mime="application/zip",
            key="dl_aligned_bundle",
        )

    # Fallback viewer for specifications that have no matching script yet
    unmatched_specs = [
        Path(result["spec"]) for result in results.values()
        if result.get("spec") and not result.get("script")
    ]
    if unmatched_specs:
        selected_spec = st.selectbox("Test specification (no matching script yet)", unmatched_specs,
                                     format_func=lambda path: path.name)
        spec_content = selected_spec.read_text(encoding="utf-8")
        with st.expander("Specification details", expanded=False):
            st.markdown(spec_content)
        st.download_button("Download test specification", data=spec_content,
                           file_name=selected_spec.name, mime="text/markdown")
    
    # Summary stats
    col1, col2, col3 = st.columns(3)
    
    completed_count = sum(1 for r in results.values() if r["status"] == "COMPLETED")
    failed_count = sum(1 for r in results.values() if r["status"] == "FAILED")
    skipped_count = sum(1 for r in results.values() if r["status"] == "SKIPPED")
    
    with col1:
        st.metric("Generated", completed_count)
    
    with col2:
        st.metric("Failed", failed_count)
    
    with col3:
        st.metric("Cached", skipped_count)
    
    # Detailed results
    st.markdown("---")
    st.markdown("##### Task Results:")
    
    for task_id, result in results.items():
        if result["status"] == "COMPLETED":
            path = result.get("path", "")
            st.success(f"✅ {task_id}: Generated successfully")
            meta = []
            if result.get("model"):
                meta.append(f"Model: `{result['model']}`")
            if result.get("duration_ms") is not None:
                meta.append(f"Duration: {result['duration_ms'] / 1000:.1f}s")
            if result.get("validation_level"):
                meta.append(f"Validation: **{result['validation_level']}**")
            if meta:
                st.caption(" · ".join(meta))
            content = ""
            if path and Path(path).exists():
                try:
                    content = Path(path).read_text(encoding="utf-8")
                except Exception as exc:  # pragma: no cover - display only
                    st.warning(f"Could not read result file: {exc}")
            if content:
                with st.expander(f"📄 View generated output — {task_id}", expanded=False):
                    st.caption(f"Saved to: {path}")
                    st.markdown(content)
                    st.download_button(
                        label="⬇️ Download",
                        data=content,
                        file_name=Path(path).name,
                        mime="text/markdown",
                        key=f"download_{task_id}",
                    )
                    if result.get("prompt"):
                        with st.expander(f"🧾 View Ollama prompt — {task_id}", expanded=False):
                            st.code(result["prompt"], language="text")
        elif result["status"] == "FAILED":
            st.error(f"❌ {task_id}: {result.get('error', 'Unknown error')}")
            failure_meta = []
            if result.get("model"):
                failure_meta.append(f"Model: `{result['model']}`")
            if result.get("validation_level"):
                failure_meta.append(f"Validation: **{result['validation_level']}**")
            if failure_meta:
                st.caption(" · ".join(failure_meta))
            if result.get("prompt"):
                with st.expander(f"🧾 View failed Ollama prompt — {task_id}", expanded=False):
                    st.code(result["prompt"], language="text")
        elif result["status"] == "SKIPPED":
            path = result.get("path", "")
            st.info(f"⊘ {task_id}: Using cached result")
            if path and Path(path).exists():
                try:
                    content = Path(path).read_text(encoding="utf-8")
                    with st.expander(f"📄 View cached output — {task_id}", expanded=False):
                        st.caption(f"Cached at: {path}")
                        st.markdown(content)
                except Exception:
                    pass

    # --- Execution: run the generated tests with pytest ---
    st.markdown("---")
    st.markdown("##### ▶️ Run Generated Tests")
    repo_path = st.session_state.get("repository_path", "")
    scripts_dir = Path(repo_path) / "tests" / "test_scripts" if repo_path else None

    # Backfill: ensure each result's .md has a matching runnable .py in test_scripts/.
    if scripts_dir is not None:
        for task_id, result in results.items():
            md_path = result.get("path", "")
            if not md_path or not Path(md_path).exists():
                continue
            script_path = scripts_dir / f"{task_id}_test.py"
            if script_path.exists():
                continue
            try:
                md_content = Path(md_path).read_text(encoding="utf-8")
                code = extract_python_code(md_content)
                if code and ("def test" in code or "import" in code):
                    scripts_dir.mkdir(parents=True, exist_ok=True)
                    header = (
                        f"# Auto-generated by orchestrator for task '{task_id}'\n"
                        f"# Source: {Path(md_path).name}\n\n"
                    )
                    script_path.write_text(header + code + "\n", encoding="utf-8")
            except Exception:
                pass

    spec_driven = any(result.get("component") for result in results.values())
    if spec_driven:
        script_files = [Path(result["script"]) for result in results.values()
                        if result.get("script") and Path(result["script"]).is_file()]
    else:
        script_files = sorted(scripts_dir.glob("*_test.py")) if scripts_dir and scripts_dir.exists() else []

    # Write a conftest.py so pytest can import the repo's package from source.
    if scripts_dir is not None and script_files:
        try:
            write_test_support(repo_path)
        except Exception:
            pass

    if not script_files:
        st.info("No runnable test scripts were extracted from the generated output yet.")
    else:
        st.caption(f"{len(script_files)} generated test file(s) in tests/test_scripts/")

        # Dependency install: the repo's own deps (e.g. werkzeug for Flask) must be
        # importable for tests to pass. This installs them via pip on demand.
        col_a, col_b = st.columns(2)
        with col_a:
            if st.button("📦 Install Repo Dependencies", key="install_repo_deps"):
                with st.spinner("Installing repository dependencies (pip)..."):
                    st.session_state["dep_install_result"] = install_repo_dependencies(repo_path)
        with col_b:
            if st.button("▶️ Run Generated Tests (pytest)", key="run_generated_tests"):
                with st.spinner("Running pytest on generated tests..."):
                    if spec_driven:
                        executions = [run_pytest_single(repo_path, path.name) for path in script_files]
                        exec_result = {
                            "tests_passed": sum(item["passed"] for item in executions),
                            "tests_failed": sum(item["failed"] for item in executions),
                            "tests_errors": sum(item["errors"] for item in executions),
                            "output": "\n".join(item["output"] for item in executions),
                        }
                        exec_result["tests_run"] = sum(exec_result[key] for key in ("tests_passed", "tests_failed", "tests_errors"))
                    else:
                        exec_result = run_tests_and_coverage(repo_path)
                st.session_state["orchestration_exec_result"] = exec_result

        dep_result = st.session_state.get("dep_install_result")
        if dep_result:
            if dep_result.get("success"):
                st.success(dep_result.get("message", "Dependencies installed"))
            else:
                st.warning(dep_result.get("message", "Dependency install had issues"))
            if dep_result.get("output"):
                with st.expander("📦 pip output", expanded=False):
                    st.code(dep_result["output"][-4000:], language="text")


    exec_result = st.session_state.get("orchestration_exec_result")
    if exec_result:
        if exec_result.get("error"):
            st.error(f"❌ {exec_result['error']}")
        c1, c2, c3, c4 = st.columns(4)
        with c1:
            st.metric("Tests Run", exec_result.get("tests_run", 0))
        with c2:
            st.metric("Passed", exec_result.get("tests_passed", 0))
        with c3:
            st.metric("Failed", exec_result.get("tests_failed", 0))
        with c4:
            st.metric("Errors", exec_result.get("tests_errors", 0))

        # Human-readable per-test breakdown.
        parsed = parse_pytest_output(exec_result.get("output", ""))
        if parsed:
            run = exec_result.get("tests_run", 0) or 1
            pass_pct = round(100 * exec_result.get("tests_passed", 0) / run)
            st.progress(pass_pct / 100, text=f"{pass_pct}% of tests passed")

            st.markdown("**Test-by-test results:**")
            for t in parsed:
                icon = {"PASSED": "✅", "FAILED": "❌", "ERROR": "🟠"}.get(t["status"], "•")
                if t["status"] == "PASSED":
                    st.markdown(f"{icon} **{t['name']}** — passed")
                else:
                    st.markdown(f"{icon} **{t['name']}** — {t['status'].lower()}")
                    if t.get("reason"):
                        st.caption(f"↳ {t['reason']}")

        if exec_result.get("output"):
            with st.expander("🔧 Raw pytest output (for debugging)", expanded=False):
                st.code(exec_result["output"], language="text")

        # --- Self-correction: feed failures back to the model to fix them ---
        failed_or_error = (
            exec_result.get("tests_failed", 0) + exec_result.get("tests_errors", 0)
        )
        if failed_or_error > 0:
            st.markdown("---")
            st.caption(
                f"{failed_or_error} test(s) failing. Auto-fix runs pytest, feeds the "
                f"failures + real source back to the model, and retries (up to 2 rounds). "
                f"This is slower but improves accuracy."
            )
            if st.button("🔧 Auto-Fix Failing Tests (self-correction)", key="self_correct"):
                with st.spinner("Self-correcting: run → analyze failures → regenerate → re-run..."):
                    st.session_state["self_correct_result"] = self_correct_tests(repo_path)
                # Refresh the main pytest result after correction.
                st.session_state["orchestration_exec_result"] = run_tests_and_coverage(repo_path)
                st.rerun()

        sc = st.session_state.get("self_correct_result")
        if sc:
            st.success(
                f"Self-correction complete: {sc['fixed']} file(s) improved across "
                f"{sc['iterations']} round(s)."
            )


def run_pytest_single(repo_path: str, test_filename: str) -> dict:
    """Run pytest on a single generated test file and return pass/fail counts."""
    scripts_dir = Path(repo_path).resolve() / "tests" / "test_scripts"
    write_test_support(repo_path)
    venv_python, venv_ok = get_venv_python(repo_path)
    python_cmd = venv_python if venv_ok and venv_python else sys.executable
    install_info = _install_script_requirements(repo_path, scripts_dir / test_filename, python_cmd)
    if not install_info.get("success"):
        out = (
            "Failed to install generated test requirements before pytest.\n"
            f"Script: {test_filename}\n"
            f"Requirements: {', '.join(install_info.get('requirements') or [])}\n\n"
            f"{install_info.get('output', '')}"
        )
        return {
            "output": out,
            "passed": 0,
            "failed": 0,
            "errors": 1,
            "skipped": 0,
            "all_passed": False,
        }
    cmd = [
        python_cmd, "-m", "pytest", str(scripts_dir / test_filename),
        "-c", str(scripts_dir / "pytest.ini"), "--rootdir", str(scripts_dir),
        f"--confcutdir={scripts_dir}", "-p", "no:cacheprovider", "--tb=short", "-v",
    ]
    out = ""
    try:
        # pytest imports an installed copy of the target package before conftest.py can redirect it.
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=300, cwd=str(scripts_dir),
                              env=coverage_env(repo_path))
        out = (proc.stdout or "") + (proc.stderr or "")
    except Exception as exc:  # pragma: no cover - defensive
        out = f"pytest run error: {exc}"

    def _count(pat: str) -> int:
        m = re.search(pat, out)
        return int(m.group(1)) if m else 0

    passed, failed, errors = _count(r"(\d+) passed"), _count(r"(\d+) failed"), _count(r"(\d+) error")
    return {
        "output": out,
        "passed": passed,
        "failed": failed,
        "errors": errors,
        "skipped": _count(r"(\d+) skipped"),
        "all_passed": failed == 0 and errors == 0 and passed > 0,
    }


def build_fix_prompt(test_code: str, pytest_output: str, repo_path: str) -> str:
    """Build a prompt asking the model to fix a failing test against real source."""
    source_context, _ = gather_source_context(repo_path)
    symbol_index = build_symbol_index(source_context)
    return (
        "You are an expert Python test engineer. The pytest test below FAILED. "
        "Fix ONLY the test so it PASSES against the real source. Correct wrong assertions, "
        "constructor arguments, and expected values based on what the actual code does.\n\n"
        "STRICT RULES:\n"
        "- Import ONLY names in 'IMPORTABLE SYMBOLS'.\n"
        "- Instantiate objects correctly (never pass None where a real object is required).\n"
        "- Output ONLY one ```python fenced block, and end with a closing ```.\n\n"
        f"IMPORTABLE SYMBOLS:\n{symbol_index}\n\n"
        f"CURRENT FAILING TEST:\n```python\n{test_code}\n```\n\n"
        f"PYTEST FAILURE OUTPUT (fix these):\n{pytest_output[:1500]}\n\n"
        f"===== REAL SOURCE (ground truth) =====\n{source_context}\n===== END SOURCE =====\n"
    )


def self_correct_tests(repo_path: str, max_iters: int = 2) -> dict:
    """Iteratively fix failing generated tests by feeding failures back to the model."""
    ollama = st.session_state.get("ollama_client")
    selector = st.session_state.get("model_selector")
    scripts_dir = Path(repo_path) / "tests" / "test_scripts"
    fixed_count = 0
    total_iters = 0

    if ollama is None or not scripts_dir.exists():
        return {"fixed": 0, "iterations": 0}

    for tf in sorted(scripts_dir.glob("*_test.py")):
        res = run_pytest_single(repo_path, tf.name)
        improved = False
        iters = 0
        while not res["all_passed"] and iters < max_iters:
            code = tf.read_text(encoding="utf-8")
            model = (selector.select_model("unit_test") if selector else None) or "qwen2.5-coder:7b"
            prompt = build_fix_prompt(code, res["output"], repo_path)
            try:
                fixed_out, _ = ollama.generate(model, prompt)
            except Exception:
                break
            fixed_code = extract_python_code(fixed_out)
            if fixed_code and "def test" in fixed_code:
                header = f"# Auto-fixed (self-correction round {iters + 1})\n\n"
                tf.write_text(header + fixed_code + "\n", encoding="utf-8")
                new_res = run_pytest_single(repo_path, tf.name)
                # Keep the fix only if it did not make things worse.
                if new_res["passed"] >= res["passed"]:
                    res = new_res
                    improved = True
                else:
                    tf.write_text(code, encoding="utf-8")  # revert
                    break
            iters += 1
            total_iters += 1
        if improved:
            fixed_count += 1

    return {"fixed": fixed_count, "iterations": total_iters}



def parse_pytest_output(output: str) -> list[dict]:
    """Parse pytest -v output into a readable list of {name, status, reason}.

    - Extracts each test's short name and PASSED/FAILED/ERROR status.
    - For failures/errors, attaches the concise reason (the `E   ...` line).
    """
    if not output:
        return []

    results: list[dict] = []
    # Per-test status lines, e.g. "...::TestClass::test_name PASSED [ 10%]".
    for m in re.finditer(r"::([\w<>\.]+(?:::[\w<>\.]+)*)\s+(PASSED|FAILED|ERROR)", output):
        full = m.group(1)
        short = full.split("::")[-1] if "::" in full else full
        # Include the class for context when present.
        name = "::".join(full.split("::")[-2:]) if "::" in full else short
        results.append({"name": name, "status": m.group(2), "reason": ""})

    # Attach concise failure reasons from the FAILURES/ERRORS section.
    # Each failure block header looks like "____ TestClass.test_name ____".
    blocks = re.split(r"_{5,}\s+([\w\.]+)\s+_{5,}", output)
    # blocks: [pre, name1, body1, name2, body2, ...]
    reason_by_name: dict[str, str] = {}
    for i in range(1, len(blocks) - 1, 2):
        # Normalize "TestClass.test_name" -> "TestClass::test_name" to match above.
        full_key = blocks[i].replace(".", "::")
        body = blocks[i + 1]
        err_lines = [ln.strip() for ln in body.splitlines() if ln.strip().startswith("E ")]
        if err_lines:
            # Last "E ..." line is usually the actual exception.
            reason_by_name[full_key] = err_lines[-1].lstrip("E").strip()

    for r in results:
        if r["status"] in ("FAILED", "ERROR"):
            # Match on the full "Class::test" name; fall back to endswith.
            reason = reason_by_name.get(r["name"])
            if reason is None:
                for k, v in reason_by_name.items():
                    if k.endswith(r["name"]) or r["name"].endswith(k):
                        reason = v
                        break
            r["reason"] = reason or ""

    return results






def _is_local_module(repo: Path, module: str) -> bool:
    """True when module looks like part of the cloned repository source tree."""
    return any([
        (repo / f"{module}.py").exists(),
        (repo / module / "__init__.py").exists(),
        (repo / "src" / f"{module}.py").exists(),
        (repo / "src" / module / "__init__.py").exists(),
    ])


def _stdlib_names() -> set[str]:
    names = getattr(sys, "stdlib_module_names", None)
    return set(names) if names else set()


def _extract_script_requirements(script_path: Path, repo_path: str) -> list[str]:
    """Infer third-party requirements from imports in a generated test script."""
    try:
        tree = ast.parse(script_path.read_text(encoding="utf-8"), filename=str(script_path))
    except Exception:
        return []

    stdlib = _stdlib_names()
    repo = Path(repo_path)
    modules: set[str] = set()

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                modules.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            modules.add(node.module.split(".")[0])

    skip = {"pytest", "conftest"}
    requirements = [
        module for module in sorted(modules)
        if module and module not in stdlib and module not in skip and not _is_local_module(repo, module)
    ]
    return requirements


def _install_script_requirements(repo_path: str, script_path: Path, venv_python: str) -> dict:
    """Install inferred script dependencies into repo .venv and persist requirement lists."""
    requirements = _extract_script_requirements(script_path, repo_path)
    scripts_dir = script_path.parent

    req_file = scripts_dir / f"{script_path.stem}_requirements.txt"
    req_file.write_text("\n".join(requirements) + ("\n" if requirements else ""), encoding="utf-8")

    aggregate = scripts_dir / "generated_requirements.txt"
    previous = []
    if aggregate.exists():
        previous = [line.strip() for line in aggregate.read_text(encoding="utf-8").splitlines() if line.strip()]
    merged = sorted(set(previous) | set(requirements))
    aggregate.write_text("\n".join(merged) + ("\n" if merged else ""), encoding="utf-8")

    if not requirements:
        return {"success": True, "requirements": [], "output": ""}

    cmd = [venv_python, "-m", "pip", "install", "-q", *requirements]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    except Exception as exc:
        return {"success": False, "requirements": requirements, "output": str(exc)}

    output = (proc.stdout or "") + (proc.stderr or "")
    return {"success": proc.returncode == 0, "requirements": requirements, "output": output}


def get_venv_python(repo_path: str) -> tuple[str, bool]:
    """Get the Python executable path for a repo's virtual environment.
    
    Creates the venv if it doesn't exist.
    Returns (python_path, success) where success=False means venv creation failed.
    """
    repo = Path(repo_path)
    venv_path = repo / ".venv"
    
    # Python executable path inside venv
    if os.name == "nt":  # Windows
        python_exe = venv_path / "Scripts" / "python.exe"
    else:  # Unix/Linux/macOS
        python_exe = venv_path / "bin" / "python"
    
    # If venv already exists, return the path
    if python_exe.exists():
        return str(python_exe), True
    
    # Create venv if it doesn't exist
    logger.info(f"Creating virtual environment at {venv_path}")
    try:
        subprocess.run(
            ["python", "-m", "venv", str(venv_path)],
            capture_output=True,
            text=True,
            timeout=120,
            check=True,
        )
        logger.info(f"✅ Virtual environment created at {venv_path}")
        return str(python_exe), True
    except Exception as e:
        logger.error(f"Failed to create venv: {e}")
        return "", False


def install_repo_dependencies(repo_path: str) -> dict:
    """Install the target repository's dependencies in its isolated virtual environment.

    Tries an editable install (pip install -e .) when a pyproject.toml or setup.py
    exists, otherwise installs from requirements*.txt. This makes the repo's
    package (and its dependencies) importable so generated tests can run.
    
    Uses the repo's .venv/ virtual environment to keep dependencies isolated.
    """
    repo = Path(repo_path)
    result = {"success": False, "message": "", "output": "", "venv_python": ""}

    # Get or create the repo's virtual environment
    venv_python, venv_ok = get_venv_python(repo_path)
    if not venv_ok or not venv_python:
        result["message"] = "❌ Failed to create virtual environment for this repository."
        return result
    
    result["venv_python"] = venv_python
    
    # Upgrade pip in the venv first
    logger.info("Upgrading pip in virtual environment...")
    try:
        subprocess.run(
            [venv_python, "-m", "pip", "install", "--upgrade", "pip", "-q"],
            capture_output=True,
            text=True,
            timeout=300,
        )
    except Exception as e:
        logger.warning(f"Could not upgrade pip: {e}")

    # Determine what to install
    if (repo / "pyproject.toml").exists() or (repo / "setup.py").exists():
        cmd = [sys.executable, "-m", "pip", "install", "-e", ".", "-q"]
        cmd = [venv_python, "-m", "pip", "install", "-e", ".", "-q"]
        install_type = "editable install (pyproject.toml/setup.py)"
    else:
        req = next(
            (repo / name for name in ("requirements.txt", "requirements-dev.txt")
             if (repo / name).exists()),
            None,
        )
        if req is None:
            result["message"] = "⚠️ No pyproject.toml, setup.py, or requirements.txt found. Tests may fail due to missing imports."
            return result
        cmd = [sys.executable, "-m", "pip", "install", "-r", str(req), "-q"]
        cmd = [venv_python, "-m", "pip", "install", "-r", str(req), "-q"]
        install_type = f"requirements install ({req.name})"

    try:
        logger.info(f"Installing dependencies in venv: {install_type}")
        proc = subprocess.run(
            cmd, cwd=str(repo), capture_output=True, text=True, timeout=900
        )
        result["output"] = (proc.stdout or "") + (proc.stderr or "")
        result["success"] = proc.returncode == 0
        result["message"] = (
            f"✅ Dependencies installed in {repo.name}/.venv — ready for testing."
            if result["success"]
            else f"⚠️ Dependency install had errors ({install_type}). Tests may fail on missing imports."
        )
        logger.info(f"Install result: {result['message']}")
    except subprocess.TimeoutExpired:
        result["message"] = "❌ Dependency install timed out after 15 minutes."
    except Exception as exc:
        result["message"] = f"❌ Install failed: {exc}"

    # CRITICAL: Always ensure pytest and common plugins are installed in the venv
    # (They may not be included in repo's main dependencies)
    if result["success"]:
        logger.info("Ensuring pytest and common plugins are installed in venv...")
        try:
            # Install pytest + commonly used plugins that projects often configure
            # pytest-timeout: handles timeout config option in pytest.ini/pyproject.toml
            # pytest-asyncio: handles async test functions
            # pytest-xdist: parallel test execution
            plugins = ["pytest", "pytest-cov", "pytest-timeout", "pytest-asyncio"]
            pytest_install = subprocess.run(
                [venv_python, "-m", "pip", "install"] + plugins + ["-q"],
                capture_output=True,
                text=True,
                timeout=300,
            )
            if pytest_install.returncode == 0:
                logger.info(f"✅ pytest and plugins installed: {', '.join(plugins)}")
            else:
                logger.warning(f"⚠️ pytest plugin install had issues: {pytest_install.stderr}")
                result["message"] += "\n⚠️ pytest plugin install had warnings (tests may still run)"
        except Exception as e:
            logger.warning(f"Could not install pytest plugins in venv: {e}")
            result["message"] += f"\n⚠️ pytest plugin install failed ({e}), tests may not run"

    return result


def status_badge(label: str, value: str, icon: str) -> None:
    st.markdown(
        f'<div class="status-card"><span class="status-icon">{icon}</span>'
        f'<div><div class="status-label">{label}</div>'
        f'<div class="status-value">{value}</div></div></div>',
        unsafe_allow_html=True,
    )


def render_sidebar() -> None:
    with st.sidebar:
        st.markdown("## Enterprise Test Generator")
        st.caption("Generate aligned specs, pytest scripts, and reports for any repository.")
        
        st.divider()
        st.markdown("### Workflow")
        workflow_steps = (
            ("01", "Enter Repository URL"),
            ("02", "Clone Repository"),
            ("03", "Generate Test Prompt"),
            ("04", "Generate Specs & Scripts"),
            ("05", "Run Pytest"),
            ("06", "Download Results"),
        )
        st.markdown(
            "".join(
                f'<div class="workflow-step"><span class="workflow-number">{number}</span>'
                f'<span>{label}</span></div>'
                for number, label in workflow_steps
            ),
            unsafe_allow_html=True,
        )
        st.divider()
        st.markdown("### Local storage")
        st.caption(str(REPOSITORIES_DIR))


def generate_repository_prompt(repository_url: str) -> None:
    """Run the backend workflow and persist its result in session state."""
    repository_url = repository_url.strip()
    valid, error_message = validate_repository_url(repository_url)
    if not valid:
        st.error(error_message, icon="⚠️")
        return

    try:
        with st.status("Preparing repository", expanded=True) as status:
            st.write("Validating repository URL")
            name = repository_name(repository_url)
            target = REPOSITORIES_DIR / name
            was_cloned = not target.exists()

            st.write("Checking local repository cache")
            if target.exists() and not is_git_repository(target):
                raise RuntimeError(
                    f"A non-Git folder already exists at {target}. "
                    "Rename or remove it before continuing."
                )

            st.write("Cloning repository" if was_cloned else "Using existing clone")
            repository_path = ensure_repository(repository_url, REPOSITORIES_DIR)
            st.session_state["repository_name"] = name
            st.session_state["repository_path"] = str(repository_path)
            st.session_state["repository_url"] = repository_url
            st.session_state["clone_status"] = "Cloned successfully" if was_cloned else "Already cloned"

            st.write("Creating isolated .venv and installing repository dependencies")
            st.session_state["dep_install_result"] = install_repo_dependencies(str(repository_path))

            st.write("Generating Test_Prompt.md")
            prompt = build_prompt(repository_url, repository_path)
            prompt_path = repository_path / "Test_Prompt.md"
            prompt_path.write_text(prompt, encoding="utf-8")
            st.session_state["generated_prompt"] = prompt
            st.session_state["prompt_path"] = str(prompt_path)
            st.session_state["prompt_status"] = "Generated successfully"
            status.update(label="Prompt ready", state="complete", expanded=False)
    except ValueError as error:
        st.error(f"Repository validation failed: {error}", icon="⚠️")
    except PermissionError as error:
        st.error(f"Permission denied while accessing the repository folder: {error}", icon="🔒")
    except OSError as error:
        st.error(f"Unable to write the generated prompt: {error}", icon="⚠️")
    except RuntimeError as error:
        st.error(f"Repository operation failed: {error}", icon="⚠️")


def clone_repository_only(repository_url: str) -> None:
    """Clone a repository and update session state without generating a prompt."""
    repository_url = repository_url.strip()
    valid, error_message = validate_repository_url(repository_url)
    if not valid:
        st.error(error_message, icon="⚠️")
        return

    try:
        name = repository_name(repository_url)
        target = REPOSITORIES_DIR / name
        was_cloned = not target.exists()
        if target.exists() and not is_git_repository(target):
            raise RuntimeError(
                f"A non-Git folder already exists at {target}. "
                "Rename or remove it before continuing."
            )

        with st.status("Preparing repository", expanded=True) as status:
            st.write("Cloning repository" if was_cloned else "Using existing clone")
            repository_path = ensure_repository(repository_url, REPOSITORIES_DIR)
            st.session_state["repository_name"] = name
            st.session_state["repository_path"] = str(repository_path)
            st.session_state["repository_url"] = repository_url
            st.session_state["clone_status"] = "Cloned successfully" if was_cloned else "Already cloned"

            st.write("Creating isolated .venv and installing repository dependencies")
            st.session_state["dep_install_result"] = install_repo_dependencies(str(repository_path))

            status.update(label="Repository ready", state="complete", expanded=False)
    except ValueError as error:
        st.error(f"Repository validation failed: {error}", icon="⚠️")
    except PermissionError as error:
        st.error(f"Permission denied while accessing the repository folder: {error}", icon="🔒")
    except OSError as error:
        st.error(f"Unable to access the repository folder: {error}", icon="⚠️")
    except RuntimeError as error:
        st.error(f"Repository operation failed: {error}", icon="⚠️")




def run_tests_and_coverage(repo_path: str) -> dict:
    """
    Run pytest with coverage on test scripts and generate coverage report.
    
    Features:
    - Auto-installs pytest and pytest-cov if not available
    - Runs pytest on tests/test_scripts/ with coverage
    - Generates HTML coverage report
    - Saves terminal output to tests/coverage/coverage_summary.md
    - Returns dict with execution status and output
    """
    repo_path_obj = Path(repo_path)
    test_scripts_dir = repo_path_obj / "tests" / "test_scripts"
    coverage_dir = repo_path_obj / "tests" / "coverage"
    coverage_html_dir = coverage_dir / "html"
    coverage_summary = coverage_dir / "coverage_summary.md"
    
    result = {
        "success": False,
        "output": "",
        "error": "",
        "tests_run": 0,
        "tests_passed": 0,
        "tests_failed": 0,
        "tests_errors": 0,
    }
    
    # Ensure coverage directory exists
    coverage_dir.mkdir(parents=True, exist_ok=True)

    # Always execute tests from the cloned repo's isolated virtual environment.
    venv_python, venv_ok = get_venv_python(repo_path)
    if not venv_ok or not venv_python:
        result["error"] = "Failed to create repository virtual environment (.venv)."
        return result

    dep_result = install_repo_dependencies(repo_path)
    st.session_state["dep_install_result"] = dep_result
    if not dep_result.get("success"):
        result["error"] = dep_result.get("message", "Repository dependency install failed.")
        result["output"] = dep_result.get("output", "")
        return result
    
    # Step 2: Run pytest with coverage
    try:
        # Make sure test_scripts directory exists and has tests
        if not test_scripts_dir.exists():
            result["error"] = f"Test scripts directory not found: {test_scripts_dir}"
            return result
        
        test_files = list(test_scripts_dir.glob("*_test.py"))
        if not test_files:
            result["error"] = "No test files found in tests/test_scripts/ (expected *_test.py files)"
            return result

        for script in test_files:
            install_info = _install_script_requirements(repo_path, script, venv_python)
            if not install_info.get("success"):
                result["error"] = (
                    "Failed to install generated test requirements before pytest. "
                    f"Script: {script.name}; requirements: {', '.join(install_info.get('requirements') or [])}"
                )
                result["output"] = install_info.get("output", "")
                return result
        
        # Run pytest command
        pytest_cmd = [
            venv_python,
            "-m",
            "pytest",
            str(test_scripts_dir),
            # Only load conftest.py from test_scripts/ down — prevents the target
            # repo's own tests/conftest.py (which may import the uninstalled package)
            # from aborting our run.
            f"--confcutdir={test_scripts_dir}",
            "--rootdir",
            str(test_scripts_dir),
            "-p",
            "no:cacheprovider",
            "--tb=short",
            "-v",
        ]
        components = discover_components(repo_path)
        json_path = coverage_dir.resolve() / "coverage.json"
        json_path.unlink(missing_ok=True)
        pytest_cmd += coverage_options(components, json_path)
        
        proc = subprocess.run(
            pytest_cmd,
            capture_output=True,
            text=True,
            timeout=300,  # 5 minute timeout
            env=coverage_env(repo_path),
        )
        
        # Parse output
        output = proc.stdout + proc.stderr
        result["output"] = output
        
        # Extract test counts from output (passed / failed / errors)
        import re
        passed_match = re.search(r"(\d+) passed", output)
        failed_match = re.search(r"(\d+) failed", output)
        error_match = re.search(r"(\d+) error", output)
        result["tests_passed"] = int(passed_match.group(1)) if passed_match else 0
        result["tests_failed"] = int(failed_match.group(1)) if failed_match else 0
        result["tests_errors"] = int(error_match.group(1)) if error_match else 0
        result["tests_run"] = (
            result["tests_passed"] + result["tests_failed"] + result["tests_errors"]
        )
        # "success" here means pytest ran and produced a summary, not that all passed.
        result["success"] = bool(passed_match or failed_match or error_match)

        
        coverage_markdown = "Coverage was not collected (is pytest-cov installed?)."
        if json_path.is_file():
            try:
                coverage_markdown = render_coverage_markdown(
                    summarize_coverage(repo_path, components, json_path, Path.cwd()))
            except (OSError, ValueError, KeyError) as exc:
                coverage_markdown = f"Coverage data could not be read: {exc}"

        # Write output to coverage_summary.md
        summary_content = f"""# Test Execution Summary

## Command
```bash
{coverage_markdown}

## Test Results
- **Tests Run**: {result['tests_run']}
- **Passed**: {result['tests_passed']}
- **Failed**: {result['tests_failed']}

## Coverage Report
HTML coverage report generated at: `tests/coverage/html/index.html`

## Full Output
```
{output}
```
"""
        coverage_summary.write_text(summary_content, encoding="utf-8")
        
        return result
    
    except subprocess.TimeoutExpired:
        result["error"] = "Test execution timed out after 5 minutes"
        return result
    except Exception as e:
        result["error"] = f"Unexpected error during test execution: {str(e)}"
        return result


def display_approval_gate() -> None:
    """
    Display the approval gate UI with Proceed/Reject buttons.
    
    When user clicks Proceed:
    - Copies prompt to system clipboard
    - Creates test folder structure for results collection
    - Shows next steps guidance
    
    When user clicks Reject:
    - Clears the generated prompt
    - Allows user to modify inputs and try again
    """
    if not st.session_state["generated_prompt"]:
        return
    
    st.markdown("---")
    st.markdown("### ✅ Approval Gate")
    st.markdown(
        "Review the generated prompt above. Click **Proceed** to copy to clipboard and start "
        "the test generation workflow, or **Reject** to modify your inputs."
    )
    
    # Display approval buttons side by side
    col1, col2, col3 = st.columns([1, 1, 2])
    
    with col1:
        if st.button("✅ Proceed", type="primary", use_container_width=True):
            # Proceed workflow: copy to clipboard and create folders
            prompt_text = st.session_state["generated_prompt"]
            repo_path = st.session_state["repository_path"]
            
            # Step 1: Copy prompt to clipboard
            clipboard_success = copilot_bridge.copy_to_clipboard(prompt_text)
            if not clipboard_success:
                st.warning(
                    "⚠️ Couldn't copy to clipboard. Please copy manually from the prompt above."
                )
            
            # Step 2: Create test folder structure
            folders_success = copilot_bridge.create_test_folders(repo_path)
            if not folders_success:
                st.error("❌ Failed to create test folders. Check permissions and try again.")
                return
            
            # Mark proceed as clicked and show next steps
            st.session_state["proceed_clicked"] = True
            st.rerun()
    
    with col2:
        if st.button("❌ Reject", use_container_width=True):
            # Reject workflow: clear state and allow modification
            st.session_state["approval_status"] = "Rejected"
            st.session_state["generated_prompt"] = ""
            st.session_state["proceed_clicked"] = False
            st.warning("Prompt rejected. Modify your inputs and try again.")
            st.rerun()


def display_results_section() -> None:
    """
    Display the results loading and viewing section.
    
    Features:
    - Load Results button to scan test/ folder
    - Three expandable sections: Specs, Scripts, Coverage
    - Auto-refresh capability to watch for new files
    - Download individual files and ZIP archive
    """
    if not st.session_state["proceed_clicked"]:
        return
    
    repo_path = Path(st.session_state["repository_path"])
    test_folder_path = str(repo_path / "tests")
    
    st.markdown("---")
    st.markdown("### 🔄 Load Test Results")
    st.markdown(
        "After test generation has saved files to the tests/ folder, "
        "click below to load and display the results."
    )
    
    col1, col2, col3 = st.columns([2, 1, 1])
    
    with col1:
        if st.button("🔄 Load Results", use_container_width=True):
            # Load all test results from the tests/ folder structure
            results = copilot_bridge.get_test_results(test_folder_path)
            st.session_state["test_results"] = results
            
            # Count total files found
            total_files = (
                len(results["specs"])
                + len(results["test_scripts"])
                + len(results["coverage"])
            )
            
            if total_files > 0:
                st.success(f"✅ Loaded {total_files} files from tests/ folder")
            else:
                st.info(
                    "ℹ️ No files found yet. Save generated output in the tests/ folder "
                    "and click Load Results again."
                )
    
    with col2:
        if st.button("▶️ Run Tests & Coverage", use_container_width=True):
            with st.spinner("Running tests and generating coverage..."):
                result = run_tests_and_coverage(repo_path)
            
            if result["success"]:
                st.success(f"✅ Tests completed! Passed: {result['tests_passed']}, Failed: {result['tests_failed']}")
                
                # Display test results in an expander
                with st.expander("📊 Test Output", expanded=False):
                    st.code(result["output"], language="bash")
                
                # Reload results after tests complete
                st.session_state["test_results"] = copilot_bridge.get_test_results(test_folder_path)
                st.info("✅ Coverage report saved to tests/coverage/coverage_summary.md")
                st.info("📊 HTML coverage report available at tests/coverage/html/index.html")
                
                # Display coverage summary immediately
                coverage_path = repo_path / "tests" / "coverage" / "coverage_summary.md"
                if coverage_path.exists():
                    st.markdown("---")
                    st.subheader("📊 Coverage Summary (Generated)")
                    with st.expander("View Coverage Details", expanded=True):
                        try:
                            coverage_content = coverage_path.read_text(encoding="utf-8")
                            st.markdown(coverage_content)
                        except Exception as e:
                            st.error(f"Error loading coverage: {e}")
            else:
                st.error(f"❌ Test execution failed: {result['error']}")
                if result["output"]:
                    with st.expander("📋 Error Details", expanded=True):
                        st.code(result["output"], language="bash")
    
    with col3:
        # Auto-refresh checkbox to watch for changes
        auto_refresh = st.checkbox("🔁 Auto-Refresh (5s)")
    
    # Display loaded results if available
    if st.session_state["test_results"]:
        results = st.session_state["test_results"]
        
        # Create three expandable sections for different result types
        st.markdown("### 📊 Test Artifacts")
        
        # Section 1: Test Specifications
        if results["specs"]:
            with st.expander("📋 Test Specifications", expanded=True):
                for spec in results["specs"]:
                    st.subheader(spec["name"])
                    st.markdown(spec["content"])
                    st.divider()
        else:
            st.info("📋 No test specifications found in tests/specs/")
        
        # Section 2: Test Scripts
        if results["test_scripts"]:
            with st.expander("🧪 Test Scripts", expanded=True):
                for script in results["test_scripts"]:
                    st.subheader(script["name"])
                    # Detect language from file extension
                    language = "python" if script["name"].endswith(".py") else "javascript"
                    st.code(script["content"], language=language)
                    st.divider()
        else:
            st.info("🧪 No test scripts found in tests/test_scripts/")
        
        # Section 3: Coverage Summary
        if results["coverage"]:
            with st.expander("📊 Coverage Summary", expanded=True):
                for coverage in results["coverage"]:
                    st.write(f"**File:** `{coverage['name']}`")
                    try:
                        # Display markdown content directly
                        st.markdown(coverage["content"], unsafe_allow_html=False)
                    except Exception as e:
                        st.error(f"Error displaying {coverage['name']}: {e}")
                        # Fallback: show as code if markdown fails
                        st.code(coverage["content"], language="markdown")
                    st.divider()
        else:
            st.info("📊 No coverage reports found in tests/coverage/")
            # Alternative: Try loading directly from file system
            coverage_path = repo_path / "tests" / "coverage" / "coverage_summary.md"
            if coverage_path.exists():
                st.info("💡 Found coverage_summary.md in file system. Click to expand:")
                try:
                    coverage_content = coverage_path.read_text(encoding="utf-8")
                    with st.expander("📊 Coverage Summary (from file)", expanded=True):
                        st.markdown(coverage_content)
                except Exception as e:
                    st.error(f"Error loading coverage file: {e}")
        
        # Download all as ZIP
        st.markdown("---")
        st.markdown("### 📥 Download Results")
        
        col1, col2 = st.columns(2)
        
        with col1:
            if st.button("📦 Create ZIP Archive", use_container_width=True):
                # Create a ZIP file with all test results
                zip_path = str(repo_path / f"{st.session_state['repository_name']}_tests.zip")
                zip_result = copilot_bridge.zip_test_results(test_folder_path, zip_path)
                
                if zip_result:
                    try:
                        with open(zip_result, "rb") as f:
                            st.session_state["zip_data"] = f.read()
                        st.success("✅ ZIP created successfully!")
                    except Exception as e:
                        st.error(f"❌ Failed to read ZIP file: {e}")
                else:
                    st.error("❌ Failed to create ZIP archive")
        
        with col2:
            if "zip_data" in st.session_state:
                st.download_button(
                    "💾 Download tests.zip",
                    data=st.session_state["zip_data"],
                    file_name=f"{st.session_state['repository_name']}_tests.zip",
                    mime="application/zip",
                    use_container_width=True,
                )
    
    # Auto-refresh loop (watches folder for changes every 5 seconds)
    if auto_refresh:
        st.info("🔁 Auto-refresh is active. Checking for new files every 5 seconds...")
        
        # Create placeholder for refresh status
        refresh_placeholder = st.empty()
        
        # Get initial count of files
        initial_results = copilot_bridge.get_test_results(test_folder_path)
        initial_count = (
            len(initial_results["specs"])
            + len(initial_results["test_scripts"])
            + len(initial_results["coverage"])
        )
        
        try:
            # Watch loop: check every 5 seconds for new files
            while auto_refresh:
                time.sleep(5)
                
                # Re-scan folder for new files
                current_results = copilot_bridge.get_test_results(test_folder_path)
                current_count = (
                    len(current_results["specs"])
                    + len(current_results["test_scripts"])
                    + len(current_results["coverage"])
                )
                
                # Update display if files changed
                if current_count > initial_count:
                    refresh_placeholder.success(
                        f"✅ Found {current_count - initial_count} new files! "
                        f"Total: {current_count}"
                    )
                    st.session_state["test_results"] = current_results
                    st.rerun()
                else:
                    refresh_placeholder.info(f"⏳ Watching... ({current_count} files found)")
        
        except KeyboardInterrupt:
            st.info("⏹️ Auto-refresh stopped")





def aumovio_css(theme: str = "dark") -> str:
    """Return the Aumovio-branded CSS (orange + deep purple) for the given theme."""
    if theme == "light":
        bg, bg2, card = "#FFFFFF", "#F7F4FA", "#FFFFFF"
        text, text2, border = "#1A0B2E", "#5B5570", "#E5DFEC"
        code_text = "#A63A00"
    else:  # dark (default)
        bg, bg2, card = "#1A0B2E", "#2E0A4F", "#3A1A5C"
        text, text2, border = "#F5F3F7", "#C9BFD6", "#4A2A6C"
        code_text = "#FF8A3D"
    orange, orange_hover, purple = "#B54708", "#9A3412", "#5B2A86"
    return f"""
    <style>
    @import url('https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500;600;700&family=Space+Grotesk:wght@500;600;700&display=swap');
    :root {{
        --orange:{orange}; --orange-hover:{orange_hover}; --code-text:{code_text}; --purple:{purple};
        --bg:{bg}; --bg2:{bg2}; --card:{card};
        --text:{text}; --text2:{text2}; --border:{border};
    }}
    .stApp {{ background:{bg}; color:{text}; }}
    h1,h2,h3,h4 {{ font-family:'Space Grotesk',sans-serif; color:{text} !important; }}
    p,label,span,li {{ font-family:'DM Sans',sans-serif; }}
    /* Force readable body text on the themed background */
    .stApp p, .stApp label, .stApp li,
    [data-testid="stMarkdownContainer"] p,
    [data-testid="stMarkdownContainer"] li,
    [data-testid="stWidgetLabel"] p {{ color:{text} !important; }}
    .av-muted, .stCaption, [data-testid="stCaptionContainer"] {{ color:{text2} !important; }}
    /* Gradient top bar */
    .av-topbar {{
        display:flex; align-items:center; justify-content:space-between;
        background:linear-gradient(90deg,{orange} 0%,{purple} 100%);
        color:#fff; padding:.7rem 1.1rem; border-radius:14px; margin-bottom:1rem;
        box-shadow:0 10px 30px rgba(0,0,0,.25);
    }}
    .av-topbar .brand {{ font-family:'Space Grotesk',sans-serif; font-weight:700; font-size:1.15rem; letter-spacing:.02em; }}
    .av-topbar .brand small {{ opacity:1; font-weight:500; }}
    /* Cards */
    .av-card {{ background:{card}; border:1px solid {border}; border-radius:14px; padding:1rem 1.25rem; margin:.4rem 0; }}
    .av-card h4 {{ margin:0 0 .3rem; }}
    .av-muted {{ color:{text2}; font-size:.85rem; }}
    /* Mode gate cards */
    .av-gate {{ background:{card}; border:1px solid {border}; border-radius:18px; padding:1.6rem; text-align:center; }}
    .av-gate .ico {{ font-size:2.4rem; }}
    .av-gate h3 {{ margin:.4rem 0 .2rem; }}
    .stApp [data-testid="stTabs"] [data-testid="stTab"] {{ color:{text2} !important; }}
    .stApp [data-testid="stTabs"] [data-testid="stTab"] * {{ color:inherit !important; }}
    .stApp [data-testid="stTabs"] [data-testid="stTab"][aria-selected="true"] {{ color:{text} !important; }}
    .stApp [data-testid="stTabs"] [data-testid="stTab"][aria-selected="true"] * {{ color:inherit !important; }}
    .stApp [data-testid="stTabs"] [data-baseweb="tab-highlight"] {{ background-color:{orange} !important; }}
    .stApp [data-testid="stMarkdownContainer"] table {{ background:{card} !important; border-color:{border} !important; }}
    .stApp [data-testid="stMarkdownContainer"] th,
    .stApp [data-testid="stMarkdownContainer"] td {{
        color:{text} !important; background:{card} !important; border-color:{border} !important;
    }}
    .stApp [data-testid="stExpander"] details {{ background:{card} !important; border:1px solid {border} !important; }}
    .stApp [data-testid="stExpander"] summary,
    .stApp [data-testid="stExpander"] summary * {{
        color:{text} !important; background:{card} !important;
    }}
    .stApp [data-testid="stDownloadButton"] button {{
        color:{text} !important; background:{card} !important;
        border:1px solid {border} !important; border-radius:8px;
    }}
    .stApp [data-testid="stDownloadButton"] button:hover {{ background:{bg2} !important; }}
    /* Status dot */
    .av-dot {{ height:11px; width:11px; border-radius:50%; display:inline-block; margin-right:7px; vertical-align:middle; }}
    .av-badge {{ display:inline-block; padding:.15rem .6rem; border-radius:999px; font-size:.72rem; font-weight:700; letter-spacing:.04em; }}
    /* Buttons -> Aumovio orange */
    .stButton > button {{ background:{orange}; color:#fff; border:0; border-radius:10px; font-weight:700; }}
    .stButton > button:hover {{ background:{orange_hover}; color:#fff; }}
    .stApp .st-key-vio_clone_only [data-testid="stButton"] > button,
    .stApp .st-key-vio_preview_queue [data-testid="stButton"] > button {{
        background:{orange} !important; color:#fff !important; border:0 !important;
    }}
    .stApp .st-key-vio_clone_only [data-testid="stButton"] > button:hover,
    .stApp .st-key-vio_preview_queue [data-testid="stButton"] > button:hover {{
        background:{orange_hover} !important; color:#fff !important;
    }}
    .stApp .st-key-vio_clone_only [data-testid="stButton"] > button:disabled,
    .stApp .st-key-vio_preview_queue [data-testid="stButton"] > button:disabled {{
        background:{orange} !important; color:#fff !important; opacity:.55;
    }}
    [data-testid='stSidebar'] {{ background:{bg2} !important; }}
    [data-testid='stSidebar'] * {{ color:{text} !important; }}
    .stMarkdown code {{ background:{bg2}; color:{code_text}; padding:2px 6px; border-radius:4px; border:1px solid {border}; }}
    /* Chat: keep typed text and bubbles readable on the themed background */
    [data-testid="stBottom"], [data-testid="stBottom"] > div {{ background:{bg} !important; }}
    [data-testid="stChatInput"], [data-testid="stChatInput"] > div, [data-testid="stChatInput"] div[data-baseweb] {{
        background:{card} !important; border-color:{border} !important;
    }}
    [data-testid="stChatInput"] textarea {{
        color:{text} !important; -webkit-text-fill-color:{text} !important; caret-color:{text} !important;
        background:transparent !important;
    }}
    [data-testid="stChatInput"] textarea::placeholder {{ color:{text2} !important; -webkit-text-fill-color:{text2} !important; opacity:1; }}
    [data-testid="stChatInput"] button {{ background:{orange} !important; color:#fff !important; }}
    [data-testid="stChatMessage"] {{ background:{card}; border:1px solid {border}; border-radius:14px; }}
    [data-testid="stChatMessage"] * {{ color:{text}; }}
    [data-testid="stChatMessage"] code {{ color:{code_text}; }}
    </style>
    """


def render_top_bar() -> None:
    """Persistent gradient top bar: brand, home and theme toggle."""
    st.markdown(
        """
        <div class="av-topbar">
            <div class="brand">AUMOVIO &nbsp;·&nbsp; TestGenie
                <small>&nbsp;| your wish is my test suite</small></div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    c1, c2, c3 = st.columns([1, 1, 6])
    with c1:
        if st.button("⟵ Home", use_container_width=True):
            st.session_state["mode"] = None
            st.rerun()
    with c2:
        cur = st.session_state.get("theme", "dark")
        label = "☀️ Light" if cur == "dark" else "🌙 Dark"
        if st.button(label, use_container_width=True):
            st.session_state["theme"] = "light" if cur == "dark" else "dark"
            st.rerun()
    with c3:
        if st.session_state.get("mode") == "vio":
            st.caption("🟢 VIO connected" if st.session_state.get("vio_connected")
                       else "⚪ VIO not connected yet; it connects when you start a run")


LANDING_HTML = (
    "<style>"
    "@keyframes tt-bob{0%,100%{transform:translateY(0) rotate(-4deg)}50%{transform:translateY(-16px) rotate(4deg)}}"
    "@keyframes tt-twinkle{0%,100%{transform:scale(.4);opacity:0}50%{transform:scale(1.15);opacity:1}}"
    "@keyframes tt-shimmer{to{background-position:200% center}}"
    "@keyframes tt-pulse{0%,100%{box-shadow:0 0 0 0 rgba(181,71,8,.55)}50%{box-shadow:0 0 0 14px rgba(181,71,8,0)}}"
    ".tt-hero{text-align:center;margin:3rem 0 1.2rem;position:relative}"
    ".tt-bot{font-size:4.6rem;display:inline-block;animation:tt-bob 3s ease-in-out infinite}"
    ".tt-tick{position:absolute;font-size:1.3rem;animation:tt-twinkle 2.4s ease-in-out infinite}"
    ".tt-name{font-family:'Space Grotesk',sans-serif;font-weight:700;font-size:3.4rem;letter-spacing:.02em;"
    "background:linear-gradient(90deg,#B54708,#7C3AED,#B54708);background-size:200% auto;"
    "-webkit-background-clip:text;-webkit-text-fill-color:transparent;animation:tt-shimmer 4s linear infinite}"
    ".tt-tag{font-size:1.1rem;color:var(--text2);margin-top:.3rem}"
    ".st-key-enter_site button{animation:tt-pulse 2s infinite;font-size:1.05rem;padding:.6rem 1rem}"
    "@media (prefers-reduced-motion:reduce){.tt-bot,.tt-tick,.tt-name,.st-key-enter_site button{animation:none}}"
    "</style>"
    '<div class="tt-hero">'
    '<span class="tt-tick" style="left:36%;top:.6rem;animation-delay:0s">✨</span>'
    '<span class="tt-tick" style="left:60%;top:1.4rem;animation-delay:.8s">✨</span>'
    '<span class="tt-tick" style="left:66%;top:3rem;animation-delay:1.6s">⭐</span>'
    '<div class="tt-bot">🧞</div>'
    '<div class="tt-name">TestGenie</div>'
    '<div class="tt-tag">Your wish is my test suite. Drop in a repo link and make a wish.</div>'
    "</div>"
)


def render_mode_gate() -> None:
    """Landing page: product name, tagline and a single entry button."""
    st.markdown(LANDING_HTML, unsafe_allow_html=True)
    _, middle, _ = st.columns([2, 1, 2])
    with middle:
        if st.button("Make a wish", key="enter_site", use_container_width=True):
            st.session_state["mode"] = "app"
            st.rerun()


STATUS_COLORS = {
    "idle": "#5B6472",
    "queued": "#5B2A86",
    "generating": "#B54708",
    "validating": "#6D28D9",
    "testing": "#C2410C",
    "fixing": "#0F766E",
    "done": "#15803D",
    "error": "#B91C1C",
}

# Cross-thread progress store for live branch-graph updates. Written by the
# background VIO worker thread, read by the auto-refreshing UI fragment.
VIO_LIVE = {"progress": {}, "results": {}, "done": True, "running": False}


def _branch_diagram_html(models_state: dict) -> str:
    """Build the fixed branch-diagram (+legend) HTML from a models-state dict."""
    def _status(agent: str) -> str:
        return models_state.get(agent, {}).get("status", "idle")

    def _color(agent: str) -> str:
        return STATUS_COLORS.get(_status(agent), "#9CA3AF")

    def _substat(agent: str) -> str:
        d = models_state.get(agent, {})
        stt = d.get("status", "idle").upper()
        if "passed" in d:
            return f"{stt} · {d.get('passed',0)}✓ {d.get('failed',0)}✗ {d.get('errors',0)}⚠"
        return stt

    master = AGENTS["master"]
    workers = [AGENTS[k] for k in ("unit", "integration", "e2e", "security")]
    worker_labels = ["unit", "integration", "e2e", "security"]
    worker_centers = [95, 285, 475, 665]
    connector_color = "#A63A00" if st.session_state.get("theme") == "light" else "#FF8A3D"

    lines = "".join(
        f'<line x1="380" y1="86" x2="{cx}" y2="212" stroke="{connector_color}" stroke-width="2.5" />'
        for cx in worker_centers
    )
    master_box = (
        f'<div style="position:absolute; left:280px; top:20px; width:200px; '
        f'text-align:center; padding:.6rem .4rem; border-radius:12px; '
        f'background:{_color(master)}; color:#fff; font-weight:700; '
        f'box-shadow:0 6px 18px rgba(0,0,0,.35);">🧠 test_generation_master'
        f'<div style="font-size:.72rem; font-weight:500; opacity:.9;">'
        f'{AGENT_MODELS.get(master,"")}</div>'
        f'<div style="font-size:.66rem; font-weight:700; margin-top:.15rem; '
        f'letter-spacing:.03em;">{_substat(master)}</div></div>'
    )
    worker_boxes = ""
    for label, w, cx in zip(worker_labels, workers, worker_centers):
        worker_boxes += (
            f'<div style="position:absolute; left:{cx-75}px; top:212px; width:150px; '
            f'text-align:center; padding:.5rem .3rem; border-radius:12px; '
            f'background:{_color(w)}; color:#fff; font-weight:700; font-size:.85rem; '
            f'box-shadow:0 6px 16px rgba(0,0,0,.3);">{label}'
            f'<div style="font-size:.68rem; font-weight:500; opacity:.9;">'
            f'{AGENT_MODELS.get(w,"")}</div>'
            f'<div style="font-size:.64rem; font-weight:700; margin-top:.15rem;">'
            f'{_substat(w)}</div></div>'
        )
    legend = "".join(
        f'<span style="margin-right:.9rem; white-space:nowrap;">'
        f'<span class="av-dot" style="background:{c}"></span>{name}</span>'
        for name, c in [
            ("idle", STATUS_COLORS["idle"]), ("generating", STATUS_COLORS["generating"]),
            ("validating", STATUS_COLORS["validating"]), ("testing", STATUS_COLORS["testing"]),
            ("fixing", STATUS_COLORS["fixing"]), ("done", STATUS_COLORS["done"]),
            ("error", STATUS_COLORS["error"]),
        ]
    )
    return (
        f'<div style="position:relative; width:760px; height:300px; margin:0 auto;">'
        f'<svg width="760" height="300" style="position:absolute; top:0; left:0;">{lines}</svg>'
        f'{master_box}{worker_boxes}</div>'
        f'<div class="av-muted" style="text-align:center;">{legend}</div>'
    )


def render_branch_controls() -> None:
    """Render the row of selection buttons under the branch diagram."""
    master = AGENTS["master"]
    workers = [AGENTS[k] for k in ("unit", "integration", "e2e", "security")]
    worker_labels = ["unit", "integration", "e2e", "security"]
    st.caption("Select an agent to view its details:")
    bcols = st.columns(5)
    for col, (name, agent) in zip(
        bcols, [("🧠 master", master)] + list(zip(worker_labels, workers))
    ):
        with col:
            if st.button(name, key=f"sel_{agent}", use_container_width=True):
                st.session_state["selected_model"] = agent


def _build_test_readme(
    agent: str,
    model_name: str,
    task_type: str,
    cases: List[Dict[str, Any]],
    spec: Dict[str, Any],
    script: str,
    pytest_result: Dict[str, Any],
    validation_level: str,
) -> str:
    """Build markdown README for a single test artifact."""
    md = f"""# 🧪 {task_type.replace("_", " ").title()}

**Agent**: {agent}  
**Model**: {model_name}  
**Quality**: {validation_level}  
**Generated**: {pd.Timestamp.now().strftime("%Y-%m-%d %H:%M:%S")}

## 📋 Test Summary

| Metric | Value |
|--------|-------|
| Status | {'✅ Passed' if pytest_result.get('status') == 'passed' else '❌ Failed'} |
| Passed | {pytest_result.get('passed', 0)} |
| Failed | {pytest_result.get('failed', 0)} |
| Duration | {pytest_result.get('duration', 0):.2f}s |

## 📝 Test Cases

| # | Name | Input | Expected | Category |
|---|------|-------|----------|----------|
"""
    
    for i, case in enumerate(cases, 1):
        md += (
            f"| {i} | `{case.get('name', '?')}` | "
            f"`{case.get('input', '?')}` | "
            f"`{case.get('expected', '?')}` | "
            f"{case.get('category', '?')} |\n"
        )
    
    md += "\n## 📄 Test Specification\n\n"
    for key, value in spec.items():
        if isinstance(value, list):
            value = ", ".join(str(v) for v in value)
        md += f"- **{key}**: {value}\n"
    
    md += f"\n## 💻 Test Script\n\n```python\n{script}\n```\n"
    md += f"\n## 📊 Execution Result\n\n```\n{pytest_result.get('logs', '')}\n```\n"
    
    if pytest_result.get("failures"):
        md += "\n## ❌ Failures\n\n"
        for failure in pytest_result["failures"]:
            md += f"- `{failure['test']}`: {failure['error']}\n"
    
    return md


def render_branch_graph() -> None:
    """Static branch view: diagram (from session models) + selection buttons."""
    st.markdown(
        _branch_diagram_html(st.session_state.get("models", {})),
        unsafe_allow_html=True,
    )
    render_branch_controls()


def _render_vio_spec_detail(agent: str, data: dict) -> None:
    """Spec-driven worker view: agent README plus each spec, its script, pytest result and prompt."""
    components = data.get("components", [])
    tabs = st.tabs(["README & Result", "Specification", "Script", "Pytest result", "Prompt"])
    with tabs[0]:
        readme = data.get("readme", "")
        if readme:
            st.markdown(readme)
            st.download_button("⬇️ Download README", data=readme, file_name=f"{agent}_README.md",
                               mime="text/markdown", key=f"dl_readme_{agent}")
        else:
            m1, m2, m3 = st.columns(3)
            m1.metric("Passed", data.get("passed", 0))
            m2.metric("Failed", data.get("failed", 0))
            m3.metric("Errors", data.get("errors", 0))
            st.info(f"{len(components)} specification(s) processed so far. The README appears when the run finishes.")

    items = [item for item in components if item.get("spec")]
    if not items:
        for tab in tabs[1:]:
            with tab:
                st.info("No specification has been written for this agent yet.")
        return
    by_stem = {Path(item["spec"]).stem: item for item in items}
    picked = st.selectbox(
        "Specification", list(by_stem), key=f"vio_pick_{agent}",
        format_func=lambda stem: f"{stem}  ·  {by_stem[stem].get('status', '')}",
    )
    chosen = by_stem[picked]
    spec_path, script_path = Path(chosen["spec"]), Path(chosen["script"]) if chosen.get("script") else None
    with tabs[1]:
        if spec_path.exists():
            spec_text = spec_path.read_text(encoding="utf-8")
            st.markdown(spec_text)
            st.download_button("⬇️ Download specification", data=spec_text, file_name=spec_path.name,
                               mime="text/markdown", key=f"dl_spec_{agent}_{spec_path.stem}")
    with tabs[2]:
        if script_path and script_path.exists():
            script_text = script_path.read_text(encoding="utf-8")
            st.code(script_text, language="python")
            st.download_button("⬇️ Download script", data=script_text, file_name=script_path.name,
                               mime="text/x-python", key=f"dl_script_{agent}_{spec_path.stem}")
        else:
            st.info(chosen.get("error") or "No script was produced for this specification.")
    with tabs[3]:
        execution = chosen.get("execution") or {}
        c1, c2, c3 = st.columns(3)
        c1.metric("Passed", execution.get("passed", 0))
        c2.metric("Failed", execution.get("failed", 0))
        c3.metric("Errors", execution.get("errors", 0))
        for test in chosen.get("tests", []):
            icon = {"PASSED": "✅", "FAILED": "❌", "ERROR": "🟠"}.get(test["status"], "•")
            st.markdown(f"{icon} **{test['name']}** - {test['status'].lower()}")
            if test.get("reason"):
                st.caption(f"↳ {test['reason']}")
        if execution.get("output"):
            with st.expander("Full pytest output", expanded=False):
                st.code(execution["output"], language="text")
    with tabs[4]:
        st.code(chosen.get("prompt", ""), language="text")


def render_model_detail() -> None:
    """Clean, 4-tab artifact panel for the selected agent."""
    agent = st.session_state.get("selected_model")
    if not agent:
        st.caption("👆 Click an agent node above to see its details.")
        return

    data = st.session_state.get("models", {}).get(agent, {})
    status = data.get("status", "idle")
    color = STATUS_COLORS.get(status, "#9CA3AF")
    is_master = agent == AGENTS["master"]

    # Header card
    st.markdown(
        f'<div class="av-card"><h4>{"🧠 " if is_master else ""}{agent} '
        f'<span class="av-badge" style="background:{color};color:#fff">{status.upper()}</span></h4>'
        f'<div class="av-muted">{AGENT_MODELS.get(agent, "")} · '
        f'{data.get("tokens", 0)} tokens</div></div>',
        unsafe_allow_html=True,
    )

    if not data:
        st.caption("👆 No run data yet. Select test types and generate to populate this agent.")
        return

    # ── Master (supervisor) view ──
    if is_master:
        st.markdown("##### 🧠 What the master did")
        st.markdown(
            "- **Planned** every numbered test specification from the real source code\n"
            "- **Assigned** each specification to the specialist agent that owns most of its scenarios\n"
            "- **Graded** every generated script (GOLD / SILVER / BRONZE)\n"
            "- **Wrote** the consolidated final report"
        )
        for failure in data.get("plan_failures", []):
            st.warning(
                f"No specification for `{failure['component']}`: {failure['error']} "
                f"Diagnostic: `{failure.get('failure_artifact', 'not saved')}`"
            )
        vals = data.get("validations", [])
        if vals:
            st.markdown("##### ✅ Validation verdicts")
            for v in vals:
                emoji = {"GOLD": "🥇", "SILVER": "🥈", "BRONZE": "🥉"}.get(v["level"], "•")
                st.markdown(f"{emoji} **{v['agent']}** — {v['level']}")
                if v.get("reasons"):
                    st.caption(f"↳ {v['reasons']}")
        if data.get("report"):
            st.markdown("##### 📝 Final report")
            st.markdown(data["report"])
        return

    # ── Worker (README-style) view ──
    if "components" in data:
        _render_vio_spec_detail(agent, data)
        return
    # ── Worker (4-tab) view ──
    task = data.get("task_type", "—")
    cases = data.get("cases", [])
    spec = data.get("spec", {})
    script = data.get("script", "")
    pytest_result = data.get("pytest_result", {})
    validation_level = data.get("validation", {}).get("level", "UNKNOWN")

    tab_cases, tab_spec, tab_script, tab_result = st.tabs(
        ["📋 Test Cases", "📄 Test Spec", "💻 Script", "📊 Result"]
    )

    # ── TAB 1: Test Cases ──
    with tab_cases:
        if cases:
            st.markdown("#### Test Cases")
            case_data = []
            for case in cases:
                case_data.append({
                    "Name": f"`{case.get('name', '?')}`",
                    "Input": f"`{case.get('input', '?')}`",
                    "Expected": f"`{case.get('expected', '?')}`",
                    "Category": case.get("category", "?"),
                    "Status": "✅" if pytest_result.get("status") == "passed" else "❌",
                })
            st.dataframe(case_data, use_container_width=True)
            
            col1, col2 = st.columns(2)
            with col1:
                st.download_button(
                    "📥 Download Cases (JSON)",
                    data=json.dumps(cases, indent=2),
                    file_name=f"{task}_cases.json",
                    mime="application/json",
                    key=f"dl_cases_{agent}",
                )
            with col2:
                st.code(json.dumps(cases, indent=2), language="json")
        else:
            st.info("ℹ️ No test cases available yet.")

    # ── TAB 2: Test Spec ──
    with tab_spec:
        if spec:
            st.markdown("#### Test Specification")
            spec_data = []
            for key, value in spec.items():
                if isinstance(value, list):
                    value = ", ".join(str(v) for v in value)
                spec_data.append({"Field": key, "Value": value})
            st.dataframe(spec_data, use_container_width=True)
            
            col1, col2 = st.columns(2)
            with col1:
                st.download_button(
                    "📥 Download Spec (JSON)",
                    data=json.dumps(spec, indent=2),
                    file_name=f"{task}_spec.json",
                    mime="application/json",
                    key=f"dl_spec_{agent}",
                )
            with col2:
                st.code(json.dumps(spec, indent=2), language="json")
        else:
            st.info("ℹ️ No spec available yet.")

    # ── TAB 3: Script ──
    with tab_script:
        if script:
            st.markdown("#### Python Test Script")
            st.code(script, language="python")
            
            col1, col2, col3 = st.columns(3)
            with col1:
                st.download_button(
                    "💾 Download (.py)",
                    data=script,
                    file_name=f"{task}_test.py",
                    mime="text/x-python",
                    key=f"dl_script_{agent}",
                )
            with col2:
                st.button("▶️ Run Test", key=f"run_{agent}")
            with col3:
                st.button("📋 Copy Code", key=f"copy_{agent}")
        else:
            st.info("ℹ️ No script generated yet.")

    # ── TAB 4: Result ──
    with tab_result:
        # Show error banner and retry button if status is ERROR
        if status == "error":
            error_msg = data.get("generation_error") or data.get("error") or "Unknown error occurred"
            st.error(f"❌ **Test Execution Failed**\n\n{error_msg}")
            
            # Retry button
            if st.button("🔄 Retry: Let Master Fix & Re-run", key=f"retry_{agent}", use_container_width=True):
                repo_path = st.session_state.get("repository_path", "")
                source_context = st.session_state.get("source_context", "")
                client = st.session_state.get("vio_client")
                
                # If source_context not in session, compute it now
                if not source_context and repo_path:
                    source_context, _ = gather_source_context(repo_path, max_chars=8000)
                
                if not client or not repo_path:
                    st.error("❌ Cannot retry: missing client or repo path.")
                else:
                    try:
                        with st.spinner(f"🧠 Master is analyzing and fixing {agent}... This may take a minute."):
                            # Get the task type
                            task_type = data.get("task_type", "unit_test")
                            task_id = f"{task_type}_0"
                            
                            # Call master to fix the code with timeout protection
                            try:
                                fix_result = client.fix_test(script, error_msg, source_context)
                            except TimeoutError as te:
                                st.error(f"❌ Fix request timed out: {str(te)}")
                                st.info("💡 Try again or check VIO connectivity.")
                                return
                            except Exception as fix_exc:
                                st.error(f"❌ Error calling master: {type(fix_exc).__name__}: {str(fix_exc)}")
                                return
                            
                            if not fix_result:
                                st.error("❌ Master returned empty response.")
                                return
                                
                            if not fix_result.get("ok"):
                                st.warning(f"⚠️ Master couldn't fix the test: {fix_result.get('error', 'Unknown error')}")
                                return
                            
                            # Extract the fixed code
                            fixed_code = extract_python_code(fix_result["content"])
                            if not fixed_code or "def test" not in fixed_code:
                                st.error("❌ Master's fix didn't produce valid Python code. Please review the prompt or try another approach.")
                                return
                            
                            # Save the fixed code
                            test_file = Path(repo_path) / "tests" / "test_scripts" / f"{task_id}_test.py"
                            test_file.parent.mkdir(parents=True, exist_ok=True)
                            test_file.write_text(fixed_code, encoding="utf-8")
                            
                            # Re-run pytest using repo's venv
                            venv_python, _ = get_venv_python(repo_path)
                            pytest_result = run_pytest(test_file, Path(repo_path), venv_python=venv_python)
                            
                            # Update session state with results
                            data["script"] = fixed_code
                            data["code"] = fixed_code
                            data["pytest_result"] = pytest_result
                            data["passed"] = pytest_result.get("passed", 0)
                            data["failed"] = pytest_result.get("failed", 0)
                            data["errors"] = pytest_result.get("errors", 0)
                            data["status"] = "done" if pytest_result.get("passed", 0) > 0 and (pytest_result.get("failed", 0) + pytest_result.get("errors", 0)) == 0 else "error"
                            data["fix_applied"] = True
                            
                            st.session_state["models"][agent] = data
                            st.success(f"✅ Master fixed the test! Status: {data['status'].upper()}")
                            st.rerun()
                    
                    except Exception as exc:
                        logger.exception("Unexpected error in retry handler")
                        st.error(f"❌ Unexpected error during retry: {type(exc).__name__}: {str(exc)}")
            
            st.markdown("---")
        
        if pytest_result and pytest_result.get("logs"):
            st.markdown("#### Test Execution Result")
            
            # Summary metrics
            col1, col2, col3, col4 = st.columns(4)
            col1.metric("Status", "✅ Passed" if pytest_result.get("status") == "passed" else "❌ Failed")
            col2.metric("Passed", pytest_result.get("passed", 0))
            col3.metric("Failed", pytest_result.get("failed", 0))
            col4.metric("Duration", f"{pytest_result.get('duration', 0):.2f}s")
            
            st.markdown("---")
            
            # Show if fix was applied
            if data.get("fix_applied"):
                st.info("ℹ️ This test was auto-fixed by the master agent after an initial failure.")
            
            # Pytest output
            st.markdown("**Pytest Summary**")
            st.code(pytest_result.get("summary", "No summary available"), language="text")
            
            # Failures
            if pytest_result.get("failures"):
                with st.expander("❌ View Failures", expanded=False):
                    for failure in pytest_result["failures"]:
                        st.error(f"**{failure['test']}**\n{failure['error']}")
            
            # Full logs
            with st.expander("📋 Full Pytest Output", expanded=False):
                st.code(pytest_result.get("logs", ""), language="text")
            
            # Download result as README
            st.download_button(
                "📥 Download Result (README.md)",
                data=_build_test_readme(
                    agent, AGENT_MODELS.get(agent, agent), task, 
                    cases, spec, script, pytest_result, validation_level
                ),
                file_name=f"{task}_result_README.md",
                mime="text/markdown",
                key=f"dl_result_{agent}",
            )
        else:
            if status != "error":
                st.info("ℹ️ Test execution result will appear here after pytest runs.")



def _write_vio_conftest(repo_path: str) -> None:
    """Write a conftest so pytest can import the repo package from source."""
    scripts_dir = Path(repo_path) / "tests" / "test_scripts"
    if not scripts_dir.exists():
        return
    try:
        write_test_support(repo_path)
    except Exception:
        pass


def _failure_analysis(test_result: dict) -> tuple[str, str]:
    """Explain a pytest failure from its observed reason and recommend a fix."""
    reason = str(test_result.get("reason", "")).strip()
    lowered = reason.lower()

    if "not writable" in lowered or "read-only" in lowered:
        return (
            "The test tried to assign a value to a read-only attribute. The pytest traceback below identifies the object and assignment.",
            "Do not mutate the read-only attribute. Assert its documented value, or use a small test double that exposes the required writable property.",
        )
    if "assertionerror" in lowered or reason.startswith(("+", "-", "?")):
        return (
            "An assertion's expected value did not match the observed output. The pytest diff/traceback below is the evidence for the mismatch.",
            "Derive the expected value from the repository implementation or documented behavior, then assert the relevant result without assuming unrelated stream or CLI state.",
        )
    if "modulenotfounderror" in lowered or "importerror" in lowered:
        return (
            "❌ Missing import: Python could not import a module required by the test. This often happens when the target repository's dependencies are not installed.",
            "**First step:** Click the '📦 Install Repo Dependencies' button to install the target repo's package and dependencies. Then regenerate tests.",
        )
    if "attributeerror" in lowered:
        return (
            "The test accessed an attribute that the runtime object does not expose in this environment.",
            "Check the object's real type and public API, then update the test to use a supported attribute or a suitable test double.",
        )
    if "typeerror" in lowered:
        return (
            "The call received an argument or value incompatible with the callable's runtime signature.",
            "Compare the test call with the source signature and correct the argument names, types, or setup.",
        )
    if reason:
        return (
            f"Pytest reported: `{reason}`. This error may indicate missing dependencies or an issue with the environment.",
            "**Try first:** Install repo dependencies (📦 button), then regenerate. If the issue persists, review the traceback below for specific details.",
        )
    return (
        "Pytest marked this test as failed. This often indicates missing imports or environment issues.",
        "**Try first:** Install repo dependencies (📦 button), then regenerate tests. If the issue persists, see the full pytest output below.",
    )


def _write_agent_readme(repo_path: str, task_id: str, result: dict) -> None:
    """Persist a detailed Markdown README for one agent's test run."""
    tests = result.get("results", [])
    failures = [test for test in tests if test.get("status") in {"FAILED", "ERROR"}]
    validation = result.get("validation", {})
    task_name = str(result.get("task_type", task_id)).replace("_", " ").title()
    readme = [
        f"# {task_name} Results",
        "",
        "## Summary",
        "",
        "| Status | Passed | Failed | Errors | Quality |",
        "| --- | ---: | ---: | ---: | --- |",
        f"| {result.get('status', 'unknown').upper()} | {result.get('passed', 0)} | "
        f"{result.get('failed', 0)} | {result.get('errors', 0)} | {validation.get('level', 'N/A')} |",
        "",
        f"- Agent: `{result.get('agent', 'unknown')}`",
        f"- Repository: `{Path(repo_path).name}`",
        f"- Tokens: {result.get('tokens', 0)}",
    ]

    if validation.get("reasons"):
        readme.extend(["", "## Validation Notes", "", str(validation["reasons"])])

    readme.extend([
        "",
        "## Test Results",
        "",
        "| Test | Status | Failure reason |",
        "| --- | --- | --- |",
    ])
    if tests:
        for test in tests:
            status = test.get("status", "UNKNOWN")
            name = str(test.get("name", "Unnamed test")).replace("|", "\\|")
            reason = str(test.get("reason", "")).replace("|", "\\|").replace("\n", " ")
            readme.append(f"| `{name}` | **{status}** | {reason or '—'} |")
            if status in {"FAILED", "ERROR"}:
                cause, solution = _failure_analysis(test)
                readme.extend([
                    "",
                    f"### Failure analysis: `{name}`",
                    "",
                    "**Why it happened (based on pytest evidence):**",
                    cause,
                    "",
                    "**Recommended solution:**",
                    solution,
                    "",
                ])
    else:
        readme.append("No per-test results were produced.")

    if not failures and tests:
        readme.extend(["", f"All **{len(tests)}** generated tests passed."])

    if result.get("fix_attempted") or result.get("fix_error"):
        readme.extend([
            "",
            "## Automated Correction",
            "",
            f"- Attempted: **{'Yes' if result.get('fix_attempted') else 'No'}**",
            f"- Applied: **{'Yes' if result.get('fix_applied') else 'No'}**",
        ])
        if result.get("fix_error"):
            readme.append(f"- Correction error: {result['fix_error']}")

    if result.get("generation_error"):
        readme.extend([
            "",
            "## Generation Error",
            "",
            f"The agent request did not produce a test script: `{result['generation_error']}`",
            "",
            "**Recommended solution:** verify VIO connectivity and agent access, then retry. If the request succeeds but still returns no code, review the agent response and prompt in the detail panel.",
            "",
        ])
    elif result.get("error"):
        readme.extend([
            "",
            "## Run Error",
            "",
            str(result["error"]),
            "",
            "**Recommended solution:** inspect the captured pytest output below, correct the first failing import/setup/assertion, and rerun this agent.",
            "",
        ])

    summary_content = "\n".join(readme)
    attempts = result.get("test_attempts", [])
    evidence = []
    if attempts:
        for attempt in attempts:
            evidence.extend([
                f"### {attempt.get('label', 'Pytest attempt')}",
                "",
                "```text",
                str(attempt.get("output") or "No pytest output was captured."),
                "```",
                "",
            ])
    elif failures and not result.get("test_output"):
        evidence.append("Full pytest output was not captured for this earlier run. The per-test reasons above are the available evidence; rerun to capture the traceback.")
    elif result.get("test_output"):
        evidence.extend(["```text", str(result["test_output"]), "```"])

    readme.extend([
        "",
        "## Generated Artifacts",
        "",
        f"- Prompt: `{result.get('prompt_path', 'not captured for this run')}`",
        f"- Test script: `{result.get('script_path', 'not generated')}`",
        "",
    ])
    summary_content = "\n".join(readme)
    content = summary_content
    if evidence:
        content += "\n\n<!-- FULL_PYTEST_EVIDENCE -->\n\n## Full Pytest Evidence\n\n" + "\n".join(evidence)
    readme_path = Path(repo_path) / "tests" / "orchestration_results" / f"{task_id}_README.md"
    readme_path.parent.mkdir(parents=True, exist_ok=True)
    readme_path.write_text(content, encoding="utf-8")
    result["readme"] = content
    result["readme_summary"] = summary_content
    result["pytest_evidence"] = "\n".join(evidence)
    result["readme_path"] = str(readme_path)


_VIO_SPECIALISATION = {"security": 2, "vulnerability": 2, "integration_test": 1, "e2e_test": 1, "api_test": 1}

VIO_MODE_RECOMMENDED, VIO_MODE_CONTINUE, VIO_MODE_ALL = "recommended", "continue", "all"
VIO_MODE_LABELS = {
    VIO_MODE_RECOMMENDED: "Recommended batch (fresh start)",
    VIO_MODE_CONTINUE: "Continue with uncovered",
    VIO_MODE_ALL: "All meaningful components",
}


def _vio_clear_artifacts(repo_path: str) -> None:
    """Remove stale generated files so specs and scripts from one run stay one-to-one."""
    tests_dir = Path(repo_path) / "tests"
    for sub in ("test_scripts", "orchestration_results"):
        directory = tests_dir / sub
        if directory.exists():
            for path in directory.glob("*"):
                try:
                    if path.is_file():
                        path.unlink()
                except OSError:
                    logger.warning("Could not remove stale VIO artifact: %s", path)
    specs = tests_dir / "specs"
    if specs.exists():
        for path in specs.glob("*.md"):
            try:
                if path.read_text(encoding="utf-8").startswith(SPEC_MARKER_PREFIXES):
                    path.unlink()
            except OSError:
                logger.warning("Could not remove stale generated spec: %s", path)


def _vio_choose_writer(client, selected_types):
    """Pick the specialist agent that owns most of a spec's scenarios."""
    def choose(plan):
        counts = Counter(scenario["test_type"] for scenario in plan["scenarios"])
        best = max(selected_types, key=lambda t: (
            counts.get(t, 0), _VIO_SPECIALISATION.get(t, 0), -selected_types.index(t)))
        agent = TYPE_TO_AGENT.get(best, AGENTS["unit"])
        return VIOGenerationAdapter(client, agent), agent, agent
    return choose


def _vio_aggregate_agent(agent, task_types, components, client, status):
    """Combine per-component results into the agent-level state the UI reads."""
    passed = failed = errors = 0
    tests = []
    levels = Counter()
    ordered = sorted(components, key=lambda item: item.get("spec", item["component"]))
    for item in ordered:
        execution = item.get("execution")
        item["tests"] = parse_pytest_output(execution.get("output", "")) if execution else []
        if execution:
            passed += execution.get("passed", 0)
            failed += execution.get("failed", 0)
            errors += execution.get("errors", 0)
            tests.extend({**test, "component": item["component"]} for test in item["tests"])
        else:
            errors += 1
        level = (item.get("validation") or {}).get("level")
        if level and level != "N/A":
            levels[level] += 1
    level = levels.most_common(1)[0][0] if levels else "N/A"
    return {
        "status": status,
        "agent": agent,
        "task_type": ", ".join(task_types),
        "components": ordered,
        "results": tests,
        "passed": passed,
        "failed": failed,
        "errors": errors,
        "skipped": sum((item.get("execution") or {}).get("skipped", 0) for item in ordered),
        "runnable": sum(1 for item in ordered if item.get("execution")),
        "fully_passing": sum(1 for item in ordered if item.get("status") == "COMPLETED"),
        "unresolved": sum(1 for item in ordered if item.get("repair_unresolved")),
        "needs_review": sum(1 for item in ordered if item.get("review_flags")),
        "tokens": client.token_usage.get(agent, 0),
        "time_s": 0.0,
        "validation": {
            "level": level,
            "reasons": ", ".join(f"{name}: {count}" for name, count in levels.most_common()) or "No quality verdicts",
        },
    }


def _write_vio_agent_readme(repo_path: str, agent: str, data: dict) -> None:
    """Write the per-agent report: aligned spec/script table, failures and fixes."""
    components = data.get("components", [])
    aligned = [c for c in components if c.get("spec") and c.get("script")]
    readme = [
        f"# {agent} - Specification-Driven Results",
        "",
        "## Summary",
        "",
        "| Specs | Runnable scripts | Fully passing scripts | Tests passed | Tests failed | Errors | Skipped | Flagged for review |",
        "| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
        f"| {len([c for c in components if c.get('spec')])} | {len(aligned)} | {data.get('fully_passing', 0)} | "
        f"{data.get('passed', 0)} | {data.get('failed', 0)} | {data.get('errors', 0)} | {data.get('skipped', 0)} | "
        f"{data.get('needs_review', 0)} |",
        "",
        f"- Reviewer opinion (advisory only, not a pass/fail signal): {data.get('validation', {}).get('level', 'N/A')}",
        f"- Agent: `{agent}` ({AGENT_MODELS.get(agent, '')})",
        f"- Repository: `{Path(repo_path).name}`",
        f"- Test types handled: {data.get('task_type') or '-'}",
        f"- Tokens used: {data.get('tokens', 0)}",
        "",
        "## Spec / Script / Pytest Alignment",
        "",
        "Each row is one numbered specification, the single script that implements it, and its pytest result.",
        "",
        "| Specification | Test script | Component | Scenarios | Passed | Failed | Reviewer opinion (advisory) | Outcome |",
        "| --- | --- | --- | ---: | ---: | ---: | --- | --- |",
    ]
    for item in components:
        execution = item.get("execution") or {}
        spec = Path(item["spec"]).name if item.get("spec") else "-"
        script = Path(item["script"]).name if item.get("script") else "-"
        quality = (item.get("validation") or {}).get("level", "N/A")
        readme.append(
            f"| {spec} | {script} | `{item['component']}` | {item.get('scenarios', 0)} | "
            f"{execution.get('passed', 0)} | {execution.get('failed', 0) + execution.get('errors', 0)} | "
            f"{quality} | {item.get('status', 'FAILED')} |"
        )

    failing = [(item, test) for item in components for test in item.get("tests", [])
               if test.get("status") in {"FAILED", "ERROR"}]
    if failing:
        readme.extend(["", "## Failure Analysis"])
    for item, test in failing:
        cause, solution = _failure_analysis(test)
        readme.extend([
            "", f"### `{test['name']}` in `{Path(item['script']).name}`", "",
            f"**Reason reported by pytest:** {test.get('reason') or 'not parsed'}", "",
            f"**Why it happened:** {cause}", "", f"**Recommended solution:** {solution}",
        ])

    unaligned = [item for item in components if not item.get("execution")]
    if unaligned:
        readme.extend(["", "## Components Without a Runnable Script", ""])
        for item in unaligned:
            readme.append(
                f"- `{item['component']}`: {item.get('error') or 'no script was produced'} "
                f"Diagnostic artifact: `{item.get('failure_artifact', 'not saved')}`"
            )

    repaired = [item for item in components if item.get("repair_applied")]
    if repaired:
        readme.extend(["", "## Automated Repair", "",
                       f"{len(repaired)} script(s) were repaired after a failing pytest run and now pass completely."])
    unresolved = [item for item in components if item.get("repair_unresolved")]
    if unresolved:
        readme.extend(["", "## Failing Tests Left After Repair", ""])
        for item in unresolved:
            partly = " (partly repaired)" if item.get("repair_partial") else ""
            readme.append(f"- `{item['component']}`{partly}: {', '.join(f'`{name}`' for name in item['repair_unresolved'])}")
    flagged = [item for item in components if item.get("review_flags")]
    if flagged:
        readme.extend(["", "## Flagged For Human Review", "",
                       "Static checks could not confirm these tests set up their preconditions or check what the "
                       "specification expects.", ""])
        for item in flagged:
            readme.extend(f"- `{item['component']}`: {finding}" for finding in item["review_flags"][:6])

    content = "\n".join(readme) + "\n"
    path = Path(repo_path) / "tests" / "orchestration_results" / f"{agent}_README.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    data["readme"] = content
    data["readme_path"] = str(path)


def _write_vio_traceability(repo_path: str, results: list) -> None:
    rows = [
        {key: item.get(key) for key in (
            "component", "spec", "script", "writer", "scenarios", "status", "error",
            "failure_stage", "failure_artifact", "attempts_captured", "repair_attempted", "repair_applied",
            "repair_partial", "repair_unresolved", "review_flags",
        )}
        | {"passed": (item.get("execution") or {}).get("passed", 0),
           "failed": (item.get("execution") or {}).get("failed", 0),
           "errors": (item.get("execution") or {}).get("errors", 0)}
        for item in results
    ]
    (Path(repo_path) / "tests" / "spec_traceability.json").write_text(
        json.dumps(rows, indent=2), encoding="utf-8")


def _build_aligned_bundle(repo_path: str) -> bytes:
    """ZIP of specs, matching scripts, pytest results and READMEs for download."""
    tests_dir = Path(repo_path) / "tests"
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for folder, pattern in (("specs", "*.md"), ("test_scripts", "*_test.py"),
                                ("orchestration_results", "*.md"), ("coverage", "*.md")):
            for path in sorted((tests_dir / folder).glob(pattern)):
                archive.write(path, f"{folder}/{path.name}")
        trace = tests_dir / "spec_traceability.json"
        if trace.exists():
            archive.write(trace, trace.name)
    return buffer.getvalue()
    agent = TYPE_TO_AGENT.get(task_type, AGENTS["unit"])
    task_id = f"{task_type}_0"
    result = {"status": "generating", "task_type": task_type, "tokens": 0, "time_s": 0.0}

    try:
        _report("generating")
        prompt = create_task_prompt(task_type, repo_path, use_local_templates=False)
        result["prompt"] = prompt
        prompt_path = Path(repo_path) / "tests" / "orchestration_results" / f"{task_id}_prompt.md"
        prompt_path.parent.mkdir(parents=True, exist_ok=True)
        prompt_path.write_text(prompt, encoding="utf-8")
        result["prompt_path"] = str(prompt_path)
        
        res = client.generate_test(agent, prompt)
        if not res["ok"]:
            result.update(
                status="error",
                agent=agent,
                validation={"level": "-", "reasons": res["error"]},
                generation_error=res["error"],
                results=[],
                passed=0,
                failed=0,
                errors=1,
            )
            _write_agent_readme(repo_path, task_id, result)
            _report("error")
            return agent, result

        code = extract_python_code(res["content"])
        elapsed = res["time_s"]
        save_orchestration_result(repo_path, task_id, res["content"])
        script_path = str(Path(repo_path) / "tests" / "test_scripts" / f"{task_id}_test.py")
        
        # Parse worker output to extract cases, spec, script (NEW: also test_data and test_display)
        worker_data = parse_worker_output(res["content"])
        cases = worker_data.get("cases", [])
        spec = worker_data.get("spec", {})
        
        # If cases/spec weren't in structured format, extract them from generated code
        if not cases and code:
            cases = extract_test_cases_from_code(code)
            logger.debug(f"Extracted {len(cases)} test cases from generated code")
        
        if not spec and code:
            spec = extract_test_spec_from_code(code, task_type)
            logger.debug(f"Generated test spec with {len(spec.get('imports_used', []))} imports")
        
        if not code and worker_data.get("script"):
            code = worker_data.get("script")

        # Log what we got from the agent for debugging
        logger.debug(f"Agent response length: {len(res['content'])} chars")
        logger.debug(f"Code extracted: {len(code) if code else 0} chars")
        logger.debug(f"Worker data keys: {list(worker_data.keys())}")
        logger.debug(f"Has test_data: {'test_data' in worker_data}")
        logger.debug(f"Has test_display: {'test_display' in worker_data}")
        
        # Validate we have code before proceeding
        if not code or not code.strip():
            logger.error(
                f"❌ {agent} failed to produce code. Response length: {len(res['content'])}, "
                f"Code: {len(code) if code else 0} chars, "
                f"Worker script: {len(worker_data.get('script', '')) if worker_data.get('script') else 0} chars"
            )
            logger.error(f"Agent response (first 1000 chars):\n{res['content'][:1000]}")
            result.update(
                status="error",
                agent=agent,
                validation={"level": "-", "reasons": "No valid Python code extracted from model response"},
                generation_error="Failed to extract Python code from model output",
                results=[],
                passed=0,
                failed=0,
                errors=1,
                cases=cases,
                spec=spec,
            )
            _write_agent_readme(repo_path, task_id, result)
            _report("error")
            return agent, result

        # Validate Python syntax before running tests
        is_valid, syntax_error = validate_python_code(code)
        if not is_valid:
            logger.error(f"❌ Generated code has syntax errors: {syntax_error}")
            result.update(
                status="error",
                agent=agent,
                validation={"level": "-", "reasons": f"Generated code has syntax errors: {syntax_error}"},
                generation_error=f"Python syntax error in generated code:\n{syntax_error}",
                results=[],
                passed=0,
                failed=0,
                errors=1,
                cases=cases,
                spec=spec,
            )
            _write_agent_readme(repo_path, task_id, result)
            _report("error")
            return agent, result

        _report("validating")
        verdict = client.validate(code, source_context)

        _report("testing")
        # Save the test script to disk
        test_file = save_test_script(code, Path(repo_path), task_type)
        # Run pytest using the repo's virtual environment
        venv_python, venv_ok = get_venv_python(repo_path)
        
        # Validate venv Python exists and is executable
        if not venv_python or not Path(venv_python).exists():
            logger.error(f"⚠️ venv Python not found at: {venv_python}")
            result.update(
                status="error",
                agent=agent,
                validation={"level": "-", "reasons": f"Virtual environment Python not found at {venv_python}. Ensure dependencies are installed."},
                generation_error="Virtual environment setup failed",
                results=[],
                passed=0,
                failed=0,
                errors=1,
            )
            _write_agent_readme(repo_path, task_id, result)
            _report("error")
            return agent, result
        
        logger.info(f"Using venv Python: {venv_python}")
        pytest_result = run_pytest(test_file, Path(repo_path), venv_python=venv_python)
        
        # Log the actual test results
        logger.info(
            f"Test Results: {pytest_result['passed']} passed, "
            f"{pytest_result['failed']} failed, "
            f"{pytest_result['errors']} errors | Status: {pytest_result['status']}"
        )
        
        # Save both agent output and pytest result to separate JSON files
        logger.debug(f"Saving agent output and pytest result...")
        try:
            save_agent_output(worker_data, Path(repo_path), task_type, task_index=0)
            save_test_result(pytest_result, Path(repo_path), task_type, task_index=0)
            logger.debug(f"✅ Saved {task_type}_0_output.json and {task_type}_0_result.json")
        except Exception as e:
            logger.warning(f"⚠️ Failed to save JSON results: {e}")
        
        test_attempts = [{"label": "Initial pytest run", "output": pytest_result["logs"]}]
        fix_attempted = False
        fix_error = ""

        # One fix round if needed
        if pytest_result["failed"] + pytest_result["errors"] > 0:
            fix_attempted = True
            _report("fixing")
            fix = client.fix_test(code, pytest_result["logs"], source_context)
            if fix["ok"]:
                fixed = extract_python_code(fix["content"])
                if fixed and "def test" in fixed:
                    code = fixed
                    # Save and re-run fixed version with repo's venv
                    test_file.write_text(code, encoding="utf-8")
                    pytest_result = run_pytest(test_file, Path(repo_path), venv_python=venv_python)
                    # Log retried test results
                    logger.info(
                        f"After auto-fix - Test Results: {pytest_result['passed']} passed, "
                        f"{pytest_result['failed']} failed, "
                        f"{pytest_result['errors']} errors | Status: {pytest_result['status']}"
                    )
                    test_attempts.append({"label": "Pytest after auto-fix", "output": pytest_result["logs"]})
                    elapsed += fix["time_s"]
            else:
                fix_error = fix.get("error", "VIO did not return a corrected test.")
        
        all_pass = pytest_result["passed"] > 0 and pytest_result["failed"] + pytest_result["errors"] == 0
        final_status = "done" if all_pass or pytest_result["passed"] > 0 else "error"
        result.update(
            status=final_status,
            agent=agent,
            code=code,
            script=code,
            cases=cases,
            spec=spec,
            # NEW: Store test_data and test_display for frontend
            test_data=worker_data.get("test_data", {"cases": cases, "spec": spec, "script": code}),
            test_display=worker_data.get("test_display", {}),
            prompt=prompt,
            prompt_path=str(prompt_path),
            script_path=str(test_file),
            validation=verdict,
            pytest_result=pytest_result,
            test_attempts=test_attempts,
            fix_attempted=fix_attempted,
            fix_applied=bool(fix_attempted and len(test_attempts) > 1),
            fix_error=fix_error,
            tokens=client.token_usage.get(agent, 0),
            time_s=elapsed,
            passed=pytest_result["passed"],
            failed=pytest_result["failed"],
            errors=pytest_result["errors"],
        )
        _write_agent_readme(repo_path, task_id, result)
        _report(final_status)
        return agent, result
        
    except Exception as exc:
        # Catch any unhandled exception and report it as an error
        logger.exception("Unhandled exception in _vio_process_task for %s", task_type)
        error_msg = f"{type(exc).__name__}: {str(exc)}"
        result.update(
            status="error",
            agent=agent,
            validation={"level": "-", "reasons": error_msg},
            generation_error=error_msg,
            results=[],
            passed=0,
            failed=0,
            errors=1,
        )
        try:
            _write_agent_readme(repo_path, task_id, result)
        except Exception:
            pass
        _report("error")
        return agent, result


def _vio_background_run(
    client,
    repo_path,
    selected_types,
    parallel,
    models_state,
    run_state,
    max_components=0,
    mode=VIO_MODE_RECOMMENDED,
    target="",
) -> None:
    """Spec-driven VIO run: master plans each spec, a specialist writes its script, pytest verifies it."""
    master = AGENTS["master"]
    lock = threading.Lock()
    per_agent: dict = {}
    plan_failures: list = []
    try:
        resume = mode != VIO_MODE_RECOMMENDED
        run_state.setdefault("mode", "continue" if resume else "run")
        run_progress.set_phase(run_state, "discover")
        if not resume:
            _vio_clear_artifacts(repo_path)
        (Path(repo_path) / "tests" / "test_scripts").mkdir(parents=True, exist_ok=True)
        _write_vio_conftest(repo_path)

        queue = select_components(
            repo_path,
            discover_components(repo_path),
            resume=resume,
            required_types=selected_types,
        )
        run_state["excluded"] = queue["excluded"]
        pending = queue["pending"]
        if target:
            pending = [c for c in pending if target.lower() in (c["module"] + "." + c["symbol"]).lower()]
            if not pending:
                raise ValueError(f"No component matching '{target}' is waiting for a specification.")
        components, import_failures = preflight_components(repo_path, pending)
        plan_failures.extend(import_failures)
        if max_components:
            components = components[:max_components]
        if not components:
            if not import_failures and queue["already_specified"]:
                raise ValueError(
                    "Every meaningful component already has a specification. "
                    "Use 'Re-run failed specs' to retry the ones that failed.")
            detail = "; ".join(item["error"] for item in import_failures[:3])
            raise ValueError("No importable public Python components were found to specify. " + detail)
        carried = 0
        if resume:
            batch = {c["module"] + "." + c["symbol"] for c in components} | {f["component"] for f in import_failures}
            for item in (_vio_load_state(repo_path) or {}).get("results", []):
                if item["component"] not in batch:
                    (per_agent.setdefault(item["writer"], []) if item.get("writer") else plan_failures).append(item)
                    carried += 1
        run_progress.begin_components(run_state, len(components) + len(import_failures), len(import_failures))
        models_state[master] = {
            "status": "generating", "task_type": "supervision", "tokens": 0,
            "validations": [], "report": "", "repo": str(repo_path),
        }
        planner = VIOGenerationAdapter(client, master)
        choose_writer = _vio_choose_writer(client, selected_types)

        # Auto-install repository dependencies before test generation.
        # This ensures generated tests can import the target package.
        logger.info("Installing repository dependencies for test execution...")
        try:
            dep_result = install_repo_dependencies(repo_path)
            if dep_result["success"]:
                logger.info("✅ Dependencies installed: " + dep_result["message"])
            else:
                logger.warning("⚠️ Dependency installation issue: " + dep_result["message"])
                if dep_result.get("output"):
                    logger.debug("Installation output: " + dep_result["output"][:500])
        except Exception as exc:
            logger.warning(f"Dependency installation failed (tests may still run): {exc}")

        def work(component):
            identity = component["module"] + "." + component["symbol"]
            if run_progress.checkpoint(run_state):
                run_state["not_started"] = run_state.get("not_started", 0) + 1
                return
            with lock:
                run_state["current"] = run_state["current"] + [identity]
            try:
                result = process_component(
                    repo_path, component, selected_types, planner, master,
                    extract_python_code, run_pytest_single,
                    choose_writer=choose_writer, engine="vio",
                    on_stage=lambda stage: run_progress.record_stage(run_state, identity, stage),
                )
                if result.get("code"):
                    verdict = client.validate(result["code"], component["source"])
                    if not verdict.get("ok"):
                        verdict["level"] = "N/A"
                    result["validation"] = verdict
            except Exception as exc:  # pragma: no cover - defensive
                logger.exception("VIO component failed: %s", identity)
                result = {"component": identity, "status": "FAILED", "error": str(exc)}
            with lock:
                agent = result.get("writer")
                if agent:
                    per_agent.setdefault(agent, []).append(result)
                    types = [t for t in selected_types if TYPE_TO_AGENT.get(t, AGENTS["unit"]) == agent]
                    models_state[agent] = _vio_aggregate_agent(agent, types, per_agent[agent], client, "testing")
                else:
                    plan_failures.append(result)
                run_state["done"] += 1
                run_state["current"] = [name for name in run_state["current"] if name != identity]
                run_progress.clear_stage(run_state, identity)
                models_state[master] = {**models_state[master], "tokens": client.token_usage.get(master, 0)}

        for task_type in selected_types:
            agent = TYPE_TO_AGENT.get(task_type, AGENTS["unit"])
            models_state[agent] = {**models_state.get(agent, {}), "status": "generating", "task_type": task_type}

        if parallel and len(components) > 1:
            with ThreadPoolExecutor(max_workers=4) as executor:
                list(executor.map(work, components))
        else:
            for component in components:
                work(component)

        _vio_finalize(client, repo_path, selected_types, models_state, run_state,
                      per_agent, plan_failures, len(components) + carried)

        summary_lines = [
            f"- {agent} ({result.get('task_type', '')}): "
            f"status={result.get('status', '?')}, "
            f"{result.get('passed', 0)} passed, {result.get('failed', 0)} failed, "
            f"{result.get('errors', 0)} errors; "
            f"validation={result.get('validation', {}).get('level', '?')}"
            for agent, result in models_state.items()
            if agent != AGENTS["master"] and "passed" in result
        ]
        report_text = ""
        if summary_lines:
            report = client.report(
                "Repository: " + str(repo_path) + "\n" + "\n".join(summary_lines)
            )
            if report.get("ok"):
                report_text = report["content"]

        validations = [
            {
                "agent": agent,
                "level": result.get("validation", {}).get("level", "?"),
                "reasons": result.get("validation", {}).get("reasons", ""),
            }
            for agent, result in models_state.items()
            if agent != AGENTS["master"] and result.get("validation")
        ]
        models_state[AGENTS["master"]] = {
            "status": "done",
            "task_type": "supervision",
            "tokens": client.token_usage.get(AGENTS["master"], 0),
            "validations": validations,
            "report": report_text,
            "repo": str(repo_path),
        }
        run_state["report"] = report_text
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception("VIO orchestration failed")
        run_state["error"] = str(exc)
    finally:
        run_state["active"] = False
        run_state["finished"] = True


def _vio_raise_coverage(client, repo_path, selected_types, run_state, per_agent, plan_failures) -> dict:
    """Measure source coverage and top up the weakest components until it reaches COVERAGE_TARGET."""
    master = AGENTS["master"]
    planner = VIOGenerationAdapter(client, master)
    choose_writer = _vio_choose_writer(client, selected_types)
    lock = threading.Lock()
    components = discover_components(repo_path)

    def all_results():
        return [item for items in (*per_agent.values(), plan_failures) for item in items]

    def measure(path, found):
        return measure_coverage(path, found, results=all_results())

    def top_up(component, row):
        identity = row["component"]
        if run_progress.checkpoint(run_state):
            return None
        with lock:
            previous = next((item for items in (*per_agent.values(), plan_failures)
                             for item in items if item["component"] == identity), None)
            run_state["current"] = run_state.get("current", []) + [f"{identity} (raising coverage)"]
        try:
            result = top_up_component(
                repo_path, component, row, previous, selected_types, planner, master,
                extract_python_code, run_pytest_single, choose_writer=choose_writer, engine="vio")
        finally:
            with lock:
                run_state["current"] = [n for n in run_state.get("current", []) if not n.startswith(identity + " (")]
        if result and result.get("component") and result is not previous:
            with lock:
                for items in (*per_agent.values(), plan_failures):
                    items[:] = [item for item in items if item["component"] != identity]
                (per_agent.setdefault(result["writer"], []) if result.get("writer") else plan_failures).append(result)
        return result

    return raise_coverage(
        repo_path, components, measure(repo_path, components), top_up, measure=measure,
        **({"rounds": 0} if run_state.get("measure_only") else {}),
        exclude={item["component"] for item in all_results()
                 if item.get("failure_stage") == "import preflight"
                 or (item.get("script") and item.get("status") != "COMPLETED")})


def _vio_finalize(client, repo_path, selected_types, models_state, run_state, per_agent, plan_failures, total):
    """Write per-agent reports, traceability, coverage, bundle and the final report; persist run state."""
    master = AGENTS["master"]
    cancelled = bool(run_state.get("cancel"))
    run_state["cancelled"] = cancelled
    run_progress.set_phase(run_state, "coverage")
    try:
        if cancelled:
            coverage = {"error": "the run was cancelled before coverage was measured"}
        else:
            coverage = _vio_raise_coverage(client, repo_path, selected_types, run_state, per_agent, plan_failures)
    except Exception as exc:  # coverage is auxiliary: never lose the generated specs, scripts and state
        logger.exception("Coverage step failed")
        coverage = {"error": f"coverage step failed: {exc}"}
    run_state["coverage"] = coverage
    all_results = [item for items in per_agent.values() for item in items] + plan_failures
    for task_type in selected_types:
        agent = TYPE_TO_AGENT.get(task_type, AGENTS["unit"])
        if agent in per_agent:
            continue
        models_state[agent] = _vio_aggregate_agent(agent, [task_type], [], client, "idle")
    for agent, items in per_agent.items():
        types = [t for t in selected_types if TYPE_TO_AGENT.get(t, AGENTS["unit"]) == agent]
        data = _vio_aggregate_agent(agent, types, items, client, "done")
        if data["passed"] == 0:
            data["status"] = "error"
        _write_vio_agent_readme(repo_path, agent, data)
        models_state[agent] = data
    for agent in {TYPE_TO_AGENT.get(t, AGENTS["unit"]) for t in selected_types} - set(per_agent):
        _write_vio_agent_readme(repo_path, agent, models_state[agent])

    _write_vio_traceability(repo_path, all_results)
    _vio_save_state(repo_path, selected_types, all_results)
    run_history.record(repo_path, run_history.build_entry(run_state, all_results, coverage))

    summary_lines = [
        f"- {agent}: {data.get('runnable', 0)} of {len(data.get('components', []))} specs have a runnable script, "
        f"{data.get('fully_passing', 0)} script(s) pass completely; {data.get('passed', 0)} tests passed, "
        f"{data.get('failed', 0)} failed, {data.get('errors', 0)} errors, {data.get('skipped', 0)} skipped; "
        f"advisory reviewer opinion (not a pass/fail signal)={data.get('validation', {}).get('level', '?')}"
        for agent, data in models_state.items() if agent != master and "passed" in data
    ]
    summary_lines.append(
        f"- Specifications written: {sum(1 for item in all_results if item.get('spec'))} of "
        f"{total}; components without a runnable script: "
        f"{sum(1 for item in all_results if not item.get('execution'))}"
    )
    if coverage.get("error"):
        summary_lines.append(f"- Source coverage unavailable: {coverage['error']}")
    else:
        summary_lines.append(
            f"- Source coverage (line + branch, passing tests only): {coverage['percent']}% (target "
            f"{coverage['target']:g}%, {'met' if coverage['target_met'] else 'NOT met'} after "
            f"{coverage['rounds']} top-up round(s))")
        weakest = [row for row in coverage["components"] if row["percent"] is not None and row["percent"] < 100][:5]
        summary_lines.extend(
            f"- Lowest coverage: {row['component']} {row['percent']}% ({'specified' if row['specified'] else 'no spec yet'})"
            for row in weakest
        )
    totals = [sum(data.get(key, 0) for agent, data in models_state.items() if agent != master and "passed" in data)
              for key in ("passed", "failed", "errors")]
    summary_lines.append(f"- Test totals: {totals[0]} passed, {totals[1]} failed, {totals[2]} errors")
    unresolved = [item for item in all_results if item.get("repair_unresolved")]
    if unresolved:
        summary_lines.append(
            f"- Failing tests left after automated repair: {sum(len(item['repair_unresolved']) for item in unresolved)} "
            f"in {len(unresolved)} component(s)")
    flagged = [item for item in all_results if item.get("review_flags")]
    if flagged:
        summary_lines.append(
            f"- Scripts flagged for human review (missing precondition setup or unchecked expected results): "
            f"{len(flagged)}")
    stage_seconds: dict = {}
    for item in all_results:
        for stage_name, seconds in (item.get("timings") or {}).items():
            stage_seconds[stage_name] = stage_seconds.get(stage_name, 0) + seconds
    if stage_seconds:
        summary_lines.append("- Time spent per stage (summed over components, seconds): " + ", ".join(
            f"{name} {value:.0f}" for name, value in sorted(stage_seconds.items(), key=lambda pair: -pair[1])))
    broken = [item for item in all_results if item.get("status") != "COMPLETED"]
    summary_lines.append(
        f"- Components not passing completely: {len(broken)} of {total}"
        + ("" if broken else " (every component passes)"))
    summary_lines.extend(f"- FAILED {item['component']}: {describe_failure(item)}" for item in broken[:12])
    run_progress.set_phase(run_state, "report")
    if cancelled:
        report_text = ("The run was cancelled. Results for the components that finished are saved; "
                       "choose 'Uncovered components' to continue with the rest.")
    else:
        report = client.report("Repository: " + str(repo_path) + "\n" + "\n".join(summary_lines))
        report_text = report["content"] if report.get("ok") else ""

    models_state[master] = {
        "status": "done", "task_type": "supervision",
        "tokens": client.token_usage.get(master, 0),
        "validations": [
            {"agent": agent, "level": data["validation"]["level"], "reasons": data["validation"]["reasons"]}
            for agent, data in models_state.items() if agent != master and data.get("validation")
        ],
        "plan_failures": [
            {"component": item["component"], "error": item.get("error", "")} for item in plan_failures
        ],
        "report": report_text, "repo": str(repo_path),
    }
    run_state["report"] = report_text
    (Path(repo_path) / "tests" / "final_report.md").write_text(report_text, encoding="utf-8")
    run_state["bundle"] = _build_aligned_bundle(repo_path)


def _vio_state_path(repo_path) -> Path:
    return Path(repo_path) / "tests" / "vio_run_state.json"


def _vio_save_state(repo_path, selected_types, results) -> None:
    """Persist full per-component results so failed specs can be re-run later, even after a restart."""
    _vio_state_path(repo_path).write_text(
        json.dumps({"types": list(selected_types), "results": results}, default=str), encoding="utf-8")


def _vio_load_state(repo_path):
    try:
        state = json.loads(_vio_state_path(repo_path).read_text(encoding="utf-8"))
        return state if state.get("results") else None
    except (OSError, ValueError):
        return None


def _vio_rerunnable(results) -> list:
    """Failed components that a new attempt can change; import failures need dependencies, not the model."""
    return [item for item in results
            if item.get("status") == "FAILED" and item.get("failure_stage") != "import preflight"]


def _vio_rerunnable_count(repo_path) -> int:
    state = _vio_load_state(repo_path) if repo_path else None
    return len(_vio_rerunnable(state["results"])) if state else 0


def _vio_rerun_background(client, repo_path, parallel, models_state, run_state) -> None:
    """Retry only the failed specs: re-plan the ones with no spec, diagnose and repair the ones whose tests fail."""
    master = AGENTS["master"]
    lock = threading.Lock()
    try:
        state = _vio_load_state(repo_path)
        if not state:
            raise ValueError("No earlier VIO run was found for this repository.")
        selected_types = state["types"]
        results = {item["component"]: item for item in state["results"]}
        targets = _vio_rerunnable(results.values())
        if not targets:
            raise ValueError("There are no failed specifications to re-run.")
        discovered = {c["module"] + "." + c["symbol"]: c for c in discover_components(repo_path)}
        run_progress.begin_components(run_state, len(targets))
        models_state[master] = {
            "status": "generating", "task_type": "supervision", "tokens": client.token_usage.get(master, 0),
            "validations": [], "report": "", "repo": str(repo_path),
        }
        for task_type in selected_types:
            agent = TYPE_TO_AGENT.get(task_type, AGENTS["unit"])
            models_state[agent] = {**models_state.get(agent, {}), "status": "generating", "task_type": task_type}
        planner = VIOGenerationAdapter(client, master)
        choose_writer = _vio_choose_writer(client, selected_types)

        def work(previous):
            identity = previous["component"]
            if run_progress.checkpoint(run_state):
                run_state["not_started"] = run_state.get("not_started", 0) + 1
                return
            with lock:
                run_state["current"] = run_state["current"] + [identity]
            component = discovered.get(identity)
            try:
                if component is None:
                    result = {**previous, "error": "Component no longer exists in the repository."}
                else:
                    result = rerun_component(
                        repo_path, component, previous, selected_types, planner, master,
                        extract_python_code, run_pytest_single, choose_writer=choose_writer, engine="vio",
                        on_stage=lambda stage: run_progress.record_stage(run_state, identity, stage),
                    )
                    if result.get("code"):
                        verdict = client.validate(result["code"], component["source"])
                        if not verdict.get("ok"):
                            verdict["level"] = "N/A"
                        result["validation"] = verdict
            except Exception as exc:  # pragma: no cover - defensive
                logger.exception("VIO re-run failed: %s", identity)
                result = {**previous, "status": "FAILED", "error": str(exc)}
            with lock:
                results[identity] = result
                run_state["done"] += 1
                run_state["current"] = [name for name in run_state["current"] if name != identity]
                run_progress.clear_stage(run_state, identity)

        # Two workers: a gateway that already timed out should not be hit by four large prompts at once.
        if parallel and len(targets) > 1:
            with ThreadPoolExecutor(max_workers=2) as executor:
                list(executor.map(work, targets))
        else:
            for previous in targets:
                work(previous)

        per_agent: dict = {}
        plan_failures: list = []
        for item in results.values():
            (per_agent.setdefault(item["writer"], []) if item.get("writer") else plan_failures).append(item)
        run_state["rerun_summary"] = {
            "attempted": len(targets),
            "fixed": sum(1 for item in targets if results[item["component"]].get("status") == "COMPLETED"),
            "needs_review": [
                {"component": item["component"], "reason": results[item["component"]].get("error", "")}
                for item in targets if results[item["component"]].get("status") != "COMPLETED"
            ],
        }
        _vio_finalize(client, repo_path, selected_types, models_state, run_state,
                      per_agent, plan_failures, len(results))
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception("VIO re-run failed")
        run_state["error"] = str(exc)
    finally:
        run_state["active"] = False
        run_state["finished"] = True


def _legacy_run_vio_orchestration(selected_types: list, parallel: bool = True, graph_ph=None) -> None:
    """Previous synchronous implementation, retained temporarily for reference."""
    client = st.session_state.get("vio_client")
    repo_path = st.session_state.get("repository_path")
    if not client:
        st.error("Connect to VIO first.")
        return
    if not repo_path:
        st.error("Clone a repository first (enter a URL and generate).")
        return

    # Clear stale artifacts from previous runs.
    for sub in ("test_scripts", "orchestration_results"):
        d = Path(repo_path) / "tests" / sub
        if d.exists():
            for f in d.glob("*"):
                try:
                    if f.is_file():
                        f.unlink()
                except Exception:
                    pass

    source_context, _ = gather_source_context(repo_path, max_chars=8000)
    scripts_dir = Path(repo_path) / "tests" / "test_scripts"
    scripts_dir.mkdir(parents=True, exist_ok=True)
    _write_vio_conftest(repo_path)

    # Live model state used only for redrawing the graph during the run.
    live = {
        TYPE_TO_AGENT.get(tt, AGENTS["unit"]): {"status": "queued", "task_type": tt}
        for tt in selected_types
    }

    def redraw():
        st.session_state["models"] = {
            agent: dict(agent_data) for agent, agent_data in live.items()
        }
        if graph_ph is not None:
            graph_ph.markdown(_branch_diagram_html(live), unsafe_allow_html=True)

    redraw()

    if parallel and len(selected_types) > 1:
        # Workers report sub-step status into a shared dict; the main thread
        # polls it and redraws so all agents visibly progress concurrently.
        shared = {}

        def _make_cb(agent):
            return lambda s: shared.__setitem__(agent, s)

        with ThreadPoolExecutor(max_workers=min(4, len(selected_types))) as ex:
            futures = {}
            for tt in selected_types:
                agent = TYPE_TO_AGENT.get(tt, AGENTS["unit"])
                shared[agent] = "generating"
                live[agent]["status"] = "generating"
                futures[ex.submit(
                    _vio_process_task, client, repo_path, source_context, tt, _make_cb(agent)
                )] = agent
            redraw()

            # Poll while all workers run concurrently.
            while not all(f.done() for f in futures):
                for agent, s in list(shared.items()):
                    if agent in live and "passed" not in live[agent]:
                        live[agent]["status"] = s
                time.sleep(0.4)

            # Collect final results.
            for fut, agent in futures.items():
                try:
                    a, result = fut.result()
                    live[a] = result
                except Exception as exc:  # pragma: no cover - defensive
                    logger.error(f"VIO task failed: {exc}")
            redraw()
    else:
        for tt in selected_types:
            agent = TYPE_TO_AGENT.get(tt, AGENTS["unit"])

            def _cb(s, a=agent):
                live[a]["status"] = s

            a, result = _vio_process_task(client, repo_path, source_context, tt, status_cb=_cb)
            live[a] = result
            redraw()

    # Final report from test_generation_master
    report_text = ""
    try:
        summary_lines = []
        for agent, d in live.items():
            if "passed" in d:
                summary_lines.append(
                    f"- {agent} ({d.get('task_type','')}): "
                    f"{d.get('passed',0)} passed, {d.get('failed',0)} failed, "
                    f"{d.get('errors',0)} errors; validation={d.get('validation',{}).get('level','?')}"
                )
        if summary_lines:
            rep = client.report("Repository: " + str(repo_path) + "\n" + "\n".join(summary_lines))
            if rep.get("ok"):
                report_text = rep["content"]
                st.session_state["vio_report"] = report_text
    except Exception:
        pass

    # Populate the master's own detail panel (its supervisory work).
    master_agent = AGENTS["master"]
    validations = [
        {
            "agent": a,
            "level": d.get("validation", {}).get("level", "?"),
            "reasons": d.get("validation", {}).get("reasons", ""),
        }
        for a, d in live.items() if d.get("validation")
    ]
    live[master_agent] = {
        "status": "done",
        "task_type": "supervision",
        "tokens": client.token_usage.get(master_agent, 0),
        "validations": validations,
        "report": report_text,
        "repo": str(repo_path),
    }

    st.session_state["models"] = live
    st.success("VIO generation complete! Click a branch to inspect each agent.")


def run_vio_orchestration(selected_types: list, parallel: bool = True, max_components: int = 0,
                          mode: str = VIO_MODE_RECOMMENDED, target: str = "") -> str:
    """Initialize shared per-agent state and launch VIO work in a background thread.

    Returns an error message when startup is blocked; empty string on success.
    """
    client = st.session_state.get("vio_client")
    repo_path = st.session_state.get("repository_path")
    if not client:
        msg = "Connect to VIO first."
        st.error(msg)
        return msg
    if not repo_path:
        msg = "Clone a repository first (enter a URL and generate)."
        st.error(msg)
        return msg

    # Fast-fail on deterministic DNS issues to avoid retry storms in background workers.
    try:
        health = client.ping_detail() if hasattr(client, "ping_detail") else {"ok": client.ping(), "error": ""}
    except Exception as exc:
        health = {"ok": False, "error": str(exc)}
    if not health.get("ok"):
        err = str(health.get("error", ""))
        if "DNS error" in err or "Failed to resolve" in err or "NameResolutionError" in err or "getaddrinfo failed" in err:
            msg = "VIO host is not resolvable from this machine right now. Use Ollama, or fix DNS/VPN and retry."
            st.error(msg)
            return msg

    current_run = st.session_state.get("vio_run_state") or {}
    if current_run.get("active"):
        msg = "VIO generation is already running."
        st.warning(msg)
        return msg

    models_state = {
        TYPE_TO_AGENT.get(task_type, AGENTS["unit"]): {
            "status": "queued",
            "task_type": task_type,
            "tokens": 0,
        }
        for task_type in selected_types
    }
    models_state.setdefault(
        AGENTS["master"], {"status": "idle", "task_type": "supervision", "tokens": 0}
    )
    run_state = {"active": True, "finished": False, "report": "", "error": ""}
    st.session_state["models"] = models_state
    st.session_state["vio_run_state"] = run_state
    if not st.session_state.get("selected_model"):
        st.session_state["selected_model"] = TYPE_TO_AGENT.get(
            selected_types[0], AGENTS["unit"]
        )

    worker = threading.Thread(
        target=_vio_background_run,
        args=(client, repo_path, selected_types.copy(), parallel, models_state, run_state, max_components, mode,
              target),
        name="vio-test-generation",
        daemon=True,
    )
    worker.start()
    return ""


def run_vio_rerun_failed(repo_path: str, parallel: bool = True) -> str:
    """Launch the background job that retries only the failed specs of the last VIO run.

    Returns an error message when startup is blocked; empty string on success.
    """
    client = st.session_state.get("vio_client")
    if not client:
        msg = "Connect to VIO first."
        st.error(msg)
        return msg
    try:
        health = client.ping_detail() if hasattr(client, "ping_detail") else {"ok": client.ping(), "error": ""}
    except Exception as exc:
        health = {"ok": False, "error": str(exc)}
    if not health.get("ok"):
        err = str(health.get("error", ""))
        if "DNS error" in err or "Failed to resolve" in err or "NameResolutionError" in err or "getaddrinfo failed" in err:
            msg = "VIO host is not resolvable from this machine right now. Use Ollama, or fix DNS/VPN and retry."
            st.error(msg)
            return msg
    if (st.session_state.get("vio_run_state") or {}).get("active"):
        msg = "VIO generation is already running."
        st.warning(msg)
        return msg
    state = _vio_load_state(repo_path)
    if not state:
        msg = "No earlier VIO run was found for this repository."
        st.warning(msg)
        return msg

    models_state = {
        TYPE_TO_AGENT.get(task_type, AGENTS["unit"]): {"status": "queued", "task_type": task_type, "tokens": 0}
        for task_type in state["types"]
    }
    models_state.setdefault(AGENTS["master"], {"status": "idle", "task_type": "supervision", "tokens": 0})
    run_state = {"active": True, "finished": False, "report": "", "error": "", "mode": "rerun"}
    st.session_state["models"] = models_state
    st.session_state["vio_run_state"] = run_state
    threading.Thread(
        target=_vio_rerun_background,
        args=(client, repo_path, parallel, models_state, run_state),
        name="vio-test-rerun",
        daemon=True,
    ).start()
    return ""


def _vio_topup_background(client, repo_path, identity, row, models_state, run_state) -> None:
    """Add tests for one specified component's uncovered lines, then rebuild reports without further top-ups."""
    master = AGENTS["master"]
    try:
        state = _vio_load_state(repo_path)
        if not state:
            raise ValueError("No earlier VIO run was found for this repository.")
        selected_types = state["types"]
        component = next((c for c in discover_components(repo_path) if c["module"] + "." + c["symbol"] == identity), None)
        if component is None:
            raise ValueError(f"{identity} no longer exists in the repository.")
        results = {item["component"]: item for item in state["results"]}
        run_progress.begin_components(run_state, 1)
        run_state["current"] = [f"{identity} (raising coverage)"]
        results[identity] = top_up_component(
            repo_path, component, row, results.get(identity), selected_types,
            VIOGenerationAdapter(client, master), master, extract_python_code, run_pytest_single,
            choose_writer=_vio_choose_writer(client, selected_types), engine="vio")
        run_state.update(done=1, current=[], topup_summary={
            "component": identity, "result": results[identity].get("topup", "")})
        per_agent: dict = {}
        plan_failures: list = []
        for item in results.values():
            (per_agent.setdefault(item["writer"], []) if item.get("writer") else plan_failures).append(item)
        _vio_finalize(client, repo_path, selected_types, models_state, run_state,
                      per_agent, plan_failures, len(results))
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception("VIO coverage top-up failed")
        run_state["error"] = str(exc)
    finally:
        run_state["active"] = False
        run_state["finished"] = True


def run_vio_topup(repo_path: str, row: dict) -> str:
    """Launch a background top-up for one component; returns an error message, or '' when started."""
    if (st.session_state.get("vio_run_state") or {}).get("active"):
        return "A run is already in progress."
    client = _chat_vio_client()
    if client is None:
        return "I can't reach VIO. Check VIO_API_KEY in your .env."
    try:
        health = client.ping_detail() if hasattr(client, "ping_detail") else {"ok": client.ping(), "error": ""}
    except Exception as exc:
        health = {"ok": False, "error": str(exc)}
    if not health.get("ok"):
        err = str(health.get("error", ""))
        if "DNS error" in err or "Failed to resolve" in err or "NameResolutionError" in err or "getaddrinfo failed" in err:
            return "VIO host is not resolvable from this machine right now. Use Ollama, or fix DNS/VPN and retry."
    state = _vio_load_state(repo_path)
    if not state:
        return "No earlier VIO run was found for this repository."
    models_state = {
        TYPE_TO_AGENT.get(task_type, AGENTS["unit"]): {"status": "queued", "task_type": task_type, "tokens": 0}
        for task_type in state["types"]
    }
    models_state.setdefault(AGENTS["master"], {"status": "idle", "task_type": "supervision", "tokens": 0})
    run_state = {"active": True, "finished": False, "report": "", "error": "", "mode": "topup", "measure_only": True}
    st.session_state["models"] = models_state
    st.session_state["vio_run_state"] = run_state
    threading.Thread(
        target=_vio_topup_background,
        args=(client, repo_path, row["component"], row, models_state, run_state),
        name="vio-coverage-topup",
        daemon=True,
    ).start()
    return ""


def _start_vio_run(client, repo_path: str, selected_types: list, parallel: bool) -> None:
    """Deprecated: superseded by the synchronous placeholder-redraw approach."""
    return


def render_vio_workspace() -> None:
    """VIO mode shell: endpoint input, connect/ping, connection status, agents."""
    st.markdown("### 🌐 VIO Agents")

    col_ep, col_btn = st.columns([4, 1])
    with col_ep:
        endpoint = st.text_input(
            "VIO Endpoint",
            value=st.session_state.get("vio_endpoint", ""),
            help="Base URL. Requests go to {base}/chat/completions",
        )
        st.session_state["vio_endpoint"] = endpoint
    with col_btn:
        st.write("")
        st.write("")
        connect = st.button("🔌 Connect", use_container_width=True)

    if connect:
        with st.spinner("Pinging VIO..."):
            # Use 120s timeout for complex repos; master agent may take time to validate/fix
            client = VIOClient(base_url=endpoint, timeout=120)
            if not client.is_configured:
                st.session_state["vio_connected"] = False
                st.error("VIO_API_KEY missing. Add it to your .env file, then reconnect.")
            else:
                ok = client.ping()
                st.session_state["vio_client"] = client
                st.session_state["vio_connected"] = ok
                st.session_state["vio_failures"] = 0 if ok else st.session_state.get("vio_failures", 0) + 1

    # Connection status dot
    connected = st.session_state.get("vio_connected", False)
    dot = "#22C55E" if connected else "#EF4444"
    label = "Connected" if connected else "Not connected"
    st.markdown(
        f'<span class="av-dot" style="background:{dot}"></span>'
        f'<b>{label}</b> &nbsp;<span class="av-muted">{endpoint}</span>',
        unsafe_allow_html=True,
    )

    if not connected and st.session_state.get("vio_failures", 0) >= 3:
        st.warning("VIO failed 3+ times. Consider switching to Ollama (offline) mode.")

    render_vio_results_panel()

    # ── Generation controls ──
    st.markdown("---")
    st.markdown("#### Generate Tests")
    repo_url = st.text_input(
        "GitHub Repository URL",
        key="vio_repo_url",
        placeholder="https://github.com/owner/repository",
    )
    if st.button(
        "Clone repository only",
        key="vio_clone_only",
        help="Clones or reuses the repository without generating a prompt or starting VIO.",
    ):
        clone_repository_only(repo_url)

    type_map = {
        "🧪 Unit": "unit_test",
        "🔗 Integration": "integration_test",
        "🌐 E2E": "e2e_test",
        "🔐 Security": "security",
    }
    st.caption("Select test types (each routes to its specialized agent):")
    tcols = st.columns(len(type_map))
    selected_types = []
    for col, (label, ttype) in zip(tcols, type_map.items()):
        with col:
            if st.checkbox(label, key=f"vio_type_{ttype}"):
                selected_types.append(ttype)

    parallel = st.checkbox(
        "⚡ Run agents in parallel", value=True, key="vio_parallel",
        help="Process up to four components concurrently (much faster for VIO).",
    )
    mode = st.radio(
        "Which components to specify", list(VIO_MODE_LABELS), format_func=VIO_MODE_LABELS.get,
        key="vio_select_mode", horizontal=True,
        help="Recommended: the most important components, clearing earlier generated files. Continue: the next "
             "most important components that have no spec yet, keeping everything already generated. All: every "
             "meaningful component without a spec. Trivial markers and internal helpers are always skipped.",
    )
    max_components = st.number_input(
        "Components per batch", min_value=1, max_value=500, value=20, step=5,
        key="vio_max_components", disabled=mode == VIO_MODE_ALL,
        help="Each component gets one numbered spec and one matching script, most important first. "
             "Every component costs several VIO requests, so start small.",
    )
    confirm_all = True
    if mode == VIO_MODE_ALL:
        confirm_all = st.checkbox(
            "I understand this runs every meaningful component without a spec and can take a long time",
            key="vio_confirm_all",
        )

    preview_path = st.session_state.get("repository_path")
    if not preview_path and repo_url.strip():
        guess = REPOSITORIES_DIR / repository_name(repo_url.strip())
        preview_path = str(guess) if guess.exists() else None
    if st.button("Preview queue", key="vio_preview_queue", disabled=not preview_path,
                 help="Shows what would be specified, without calling VIO."):
        with st.spinner("Scanning components..."):
            st.session_state["vio_queue_preview"] = {
                "path": str(preview_path),
                "queue": select_components(
                    preview_path,
                    discover_components(preview_path),
                    resume=mode != VIO_MODE_RECOMMENDED,
                    required_types=selected_types,
                ),
            }
    preview = st.session_state.get("vio_queue_preview")
    if preview and preview["path"] == str(preview_path):
        queue = preview["queue"]
        batch = len(queue["pending"]) if mode == VIO_MODE_ALL else min(int(max_components), len(queue["pending"]))
        st.caption(
            f"{queue['meaningful']} meaningful components, {queue['already_specified']} already specified, "
            f"{len(queue['pending'])} pending, {len(queue['excluded'])} skipped as not worth a spec. "
            f"This run would specify {batch}.")
        if queue["excluded"]:
            with st.expander(f"Skipped components ({len(queue['excluded'])})"):
                st.dataframe(queue["excluded"], width="stretch")

    run_col, rerun_col, dep_col = st.columns([1, 1, 1])
    run_state = st.session_state.get("vio_run_state") or {}
    with run_col:
        run = st.button("▶️ Generate with VIO Agents", use_container_width=True,
                        disabled=not connected or run_state.get("active", False) or not confirm_all)
    with rerun_col:
        known_path = st.session_state.get("repository_path")
        if not known_path and repo_url.strip():
            guess = REPOSITORIES_DIR / repository_name(repo_url.strip())
            known_path = str(guess) if guess.exists() else None
        failed_count = _vio_rerunnable_count(known_path)
        rerun = st.button(
            f"🔁 Re-run failed specs ({failed_count})", use_container_width=True, key="vio_rerun_failed",
            disabled=not connected or run_state.get("active", False) or not failed_count,
            help="Retries only the failed specs. VIO first diagnoses each failure, then re-plans specs that "
                 "were never written or rewrites only the failing tests. Passing specs are not touched.",
        )
    with dep_col:
        if st.button("📦 Install Repo Dependencies", use_container_width=True,
                     key="vio_install_deps"):
            if st.session_state.get("repository_path"):
                with st.spinner("Installing repo dependencies..."):
                    st.session_state["dep_install_result"] = install_repo_dependencies(
                        st.session_state["repository_path"])
            else:
                st.warning("Clone a repo first (enter URL and click Generate).")

    dep_result = st.session_state.get("dep_install_result")
    if dep_result:
        (st.success if dep_result.get("success") else st.warning)(
            dep_result.get("message", ""))

    if rerun and known_path:
        error = run_vio_rerun_failed(known_path, parallel=parallel)
        if error:
            st.warning(error)

    if run:
        if not selected_types:
            st.warning("Select at least one test type.")
        elif not repo_url:
            st.warning("Enter a GitHub repository URL.")
        else:
            generate_repository_prompt(repo_url)  # clone + set repository_path
            if st.session_state.get("repository_path"):
                error = run_vio_orchestration(
                    selected_types,
                    parallel=parallel,
                    max_components=0 if mode == VIO_MODE_ALL else int(max_components),
                    mode=mode,
                )
                if error:
                    st.warning(error)


CHAT_GREETING = ""
CHAT_CUSTOM = chat_assistant.CUSTOM_MODE
CHAT_SPEC_LABELS = {
    VIO_MODE_ALL: "Run all components",
    CHAT_CUSTOM: "Customised",
    VIO_MODE_CONTINUE: "Uncovered components",
    VIO_MODE_RECOMMENDED: "Fresh start",
}
CHAT_SPEC_PROMPT = (
    "How do you want your specs? Pick one:\n\n"
    "- **Run all components**: every meaningful component without a spec (can take a long time)\n"
    "- **Customised**: you choose a class or function, or how many components\n"
    "- **Uncovered components**: the next most important components without a spec; existing files are kept\n"
    "- **Fresh start**: the most important components, replacing earlier generated files"
)


def _chat_repo_names() -> list:
    if not REPOSITORIES_DIR.is_dir():
        return []
    return sorted(p.name for p in REPOSITORIES_DIR.iterdir() if p.is_dir() and not p.name.startswith("."))


def _chat_vio_client(require_live: bool = False):
    """VIO client for chat; resilient to transient ping failures.

    - Returns None only when VIO is unconfigured.
    - With require_live=True, returns None when ping fails.
    - With require_live=False (default), returns a configured client even if ping fails,
      so execution can still attempt API calls with retry logic.
    """
    existing = st.session_state.get("vio_client")
    if existing and getattr(existing, "is_configured", False):
        if not require_live:
            return existing
        ok = existing.ping()
        st.session_state["vio_connected"] = ok
        return existing if ok else None

    client = VIOClient(base_url=st.session_state.get("vio_endpoint", ""))
    if not client.is_configured:
        st.session_state["vio_connected"] = False
        return None

    ok = client.ping()
    st.session_state["vio_client"] = client
    st.session_state["vio_connected"] = ok
    if require_live and not ok:
        return None
    return client


def _chat_prepare_repo(request: dict) -> tuple:
    """Clone or locate the requested repository; returns (path, error)."""
    try:
        if request["repo_url"]:
            valid, error = validate_repository_url(request["repo_url"])
            if not valid:
                return "", error
            path = ensure_repository(request["repo_url"], REPOSITORIES_DIR)
            url = request["repo_url"]
        else:
            path = REPOSITORIES_DIR / request["repo_name"]
            url = ""
            if not path.is_dir():
                return "", f"`{request['repo_name']}` is not cloned yet. Send me its GitHub URL."
    except (ValueError, RuntimeError, OSError) as error:
        return "", f"I couldn't prepare the repository: {error}"
    st.session_state.update(repository_name=Path(path).name, repository_path=str(path),
                            repository_url=url, clone_status="Ready")
    return str(path), ""


def _chat_run_summary(run_state: dict) -> str:
    if run_state.get("error"):
        return f"The run stopped: {run_state['error']}"
    master = AGENTS["master"]
    models = st.session_state.get("models", {})
    passed, failed, errors = (
        sum(data.get(key, 0) for agent, data in models.items() if agent != master and "passed" in data)
        for key in ("passed", "failed", "errors")
    )
    text = f"{'Cancelled early' if run_state.get('cancelled') else 'Finished'}: {passed} tests passed, {failed} failed, {errors} errors."
    runnable, fully, review = _chat_script_totals(models)
    text += f" {fully} of {runnable} runnable scripts pass completely" + (f"; {review} flagged for review." if review else ".")
    coverage = run_state.get("coverage") or {}
    if coverage and not coverage.get("error"):
        text += f" Source coverage from passing tests is {coverage['percent']}% (target {coverage['target']:g}%)."
    return text + " I can re-run failed specs or continue with more components. Your downloads are below."


def _chat_script_totals(models: dict) -> tuple:
    """(runnable scripts, fully passing scripts, scripts flagged for review) across all writer agents."""
    workers = [data for agent, data in models.items() if agent != AGENTS["master"] and "passed" in data]
    return tuple(sum(data.get(key, 0) for data in workers) for key in ("runnable", "fully_passing", "needs_review"))


def _chat_status() -> str:
    run_state = st.session_state.get("vio_run_state") or {}
    if run_state.get("active"):
        total = run_state.get("total")
        return (f"Working: {run_state.get('done', 0)} of {total} specifications done." if total
                else "Still preparing the component queue.")
    if run_state.get("finished"):
        return _chat_run_summary(run_state)
    return "Nothing has run yet. Tell me what to test and I'll start."


def _chat_rerun(request: dict) -> str:
    if _chat_vio_client(require_live=True) is None:
        if not os.getenv("VIO_API_KEY"):
            return "VIO is not configured. Add VIO_API_KEY in your .env, then try again."
        return "I can't reach VIO right now, so I can't re-run specs. Please try again in a moment."
    path = ""
    if request["repo_url"] or request["repo_name"]:
        path, error = _chat_prepare_repo(request)
        if error:
            return error
    path = path or st.session_state.get("repository_path", "")
    count = _vio_rerunnable_count(path)
    if not count:
        return "There are no failed specs to re-run."
    if (st.session_state.get("vio_run_state") or {}).get("active"):
        return "A run is already in progress; I'll report when it finishes."
    start_error = run_vio_rerun_failed(path, parallel=True)
    if start_error:
        st.session_state.update(chat_run_pending=False)
        return start_error
    st.session_state.update(chat_run_pending=True, chat_stage="running")
    return f"Re-running {count} failed specs. Progress is below."


def _chat_run_ollama(path: str, types: list, mode: str, pending: list, limit: int) -> str:
    """Run the local Ollama pipeline synchronously and return a summary."""
    state = st.session_state
    if not initialize_orchestrator():
        state.update(chat_stage="engine")
        state["chat_request"]["engine"] = ""
        return "I couldn't reach Ollama. Start it, or choose VIO instead."
    if mode == VIO_MODE_RECOMMENDED:
        _vio_clear_artifacts(path)
    run_test_orchestration(types, components=pending[:limit] if limit else pending, inline_extras=False)
    results = state.get("execution_results") or {}
    failed = sum(result["status"] == "FAILED" for result in results.values())
    text = f"Finished: {len(results)} components processed, {failed} with failures."
    coverage = state.get("last_coverage") or {}
    if coverage and not coverage.get("error"):
        text += f" Source coverage is {coverage['percent']}%."
    state["chat_stage"] = "done"
    return text + " Your downloads are below."


def _chat_start(request: dict) -> str:
    """Resolve the repository and components, then launch the chosen engine."""
    state = st.session_state
    path, error = _chat_prepare_repo(request)
    if error:
        request.update(repo_url="", repo_name="")
        state["chat_stage"] = "intake"
        return error + " Please share the GitHub repository link again."
    if request["engine"] == "vio":
        if (state.get("vio_run_state") or {}).get("active"):
            return "A run is already in progress; I'll report when it finishes."
        if _chat_vio_client() is None:
            request["engine"] = ""
            state["chat_stage"] = "engine"
            if not os.getenv("VIO_API_KEY"):
                return "VIO is not configured. Add VIO_API_KEY in your .env, or choose Ollama."
            return "VIO is currently unavailable. Please try again, or choose Ollama."

    target, choice = request["target"], request["mode"]
    mode = VIO_MODE_CONTINUE if choice == CHAT_CUSTOM else choice
    components = discover_components(path)
    already_specified = bool(spec_slugs(path))
    if target:
        needle = target.lower()
        matching = [c for c in components if needle in (c["module"] + "." + c["symbol"]).lower()]
        if not matching:
            request.update(target="", mode="")
            state["chat_stage"] = "specs"
            return (f"I couldn't find a class or function matching `{target}` in {Path(path).name}. "
                    "Choose how to select components, or name another one.")
        components, mode = matching, VIO_MODE_CONTINUE
    pending = select_components(
        path,
        components,
        resume=mode != VIO_MODE_RECOMMENDED,
        required_types=request.get("test_types") or [],
    )["pending"]
    if not pending:
        request.update(target="", max_components=0, mode="", confirmed=False)
        state["chat_stage"] = "done"
        return "Every matching component already has a specification. I can re-run failed ones or pick others."

    if choice == VIO_MODE_ALL and not request.get("confirmed"):
        state["chat_stage"] = "confirm"
        default = (f"Run all will specify {len(pending)} components in `{Path(path).name}`. Each one needs several "
                   "model requests, so this can take a long time. Start?")
        return _chat_master_question("confirm_run_all", default, request)

    limit = 0 if target or mode == VIO_MODE_ALL else (request["max_components"] or 20)
    types = request["test_types"]
    label = f"`{Path(path).name}`"
    scope = CHAT_SPEC_LABELS.get(choice, "").lower()
    request.update(target="", max_components=0, mode="", confirmed=False)
    if request["engine"] == "ollama":
        return _chat_run_ollama(path, types, mode, pending, limit)

    start_error = run_vio_orchestration(types, parallel=True, max_components=limit, mode=mode, target=target)
    if start_error:
        state.update(chat_run_pending=False)
        state["chat_stage"] = "engine"
        if request["engine"] == "vio":
            request["engine"] = ""
        return start_error
    state.update(chat_run_pending=True, chat_stage="running")
    reply = chat_assistant.describe_request({**request, "test_types": types, "target": target}, label)
    reply = reply.replace("Generating", "Generating with VIO", 1)
    if scope:
        reply += f" Scope: {scope}."
    if mode == VIO_MODE_RECOMMENDED and already_specified:
        reply += " This replaces earlier generated files."
    return reply + " I'll report back when it's done."


def _chat_queue_text(request: dict) -> str:
    """Component counts for the requested repository, or the error that blocks them."""
    path, error = _chat_prepare_repo(request)
    if error:
        request.update(repo_url="", repo_name="")
        st.session_state["chat_stage"] = "intake"
        return error + " Please share the GitHub repository link again."
    queue = select_components(
        path,
        discover_components(path),
        resume=True,
        required_types=request.get("test_types") or [],
    )
    return (f"`{Path(path).name}` has {queue['meaningful']} meaningful components: {len(queue['pending'])} still "
            f"need a spec, {queue['already_specified']} already have one, and {len(queue['excluded'])} are "
            "skipped as trivial or internal.")


def _chat_advance() -> str:
    """Ask for whatever is still missing, or start the run; returns the assistant's message."""
    state = st.session_state
    request = state["chat_request"]
    if not (request["repo_url"] or request["repo_name"]):
        state["chat_stage"] = "intake"
        default = chat_assistant.missing_question(request, _chat_repo_names())
        return _chat_master_question("ask_repository", default, request)
    if not request["test_types"]:
        state["chat_stage"] = "intake"
        default = "Which test types do you need (one or more): unit, integration, e2e or security?"
        return _chat_master_question("ask_test_types", default, request)
    if not request["engine"]:
        state["chat_stage"] = "engine"
        default = "How would you like to generate the tests?"
        return _chat_master_question("ask_engine", default, request)
    if not request["mode"]:
        counts = _chat_queue_text(request)
        if not (request["repo_url"] or request["repo_name"]):
            return counts
        state["chat_stage"] = "specs"
        types = request["test_types"]
        summary = f"Test types: {chat_assistant.type_label(types)}."
        if len(types) > 1:
            summary += " They are combined in one spec and one script per component."
        default = f"{summary} {counts}\n\n{CHAT_SPEC_PROMPT}"
        return _chat_master_question("ask_scope_mode", default, request)
    if request["mode"] == CHAT_CUSTOM and not (request["target"] or request["max_components"]):
        state["chat_stage"] = "custom"
        default = "Type the class or function to cover, or how many components (for example `Parser` or `10`)."
        return _chat_master_question("ask_custom_target_or_count", default, request)
    return _chat_start(request)


def _chat_set(**changes) -> str:
    st.session_state["chat_request"].update(changes)
    return _chat_advance()


def _chat_new_repository() -> str:
    engine = st.session_state["chat_request"]["engine"]
    st.session_state["chat_request"] = {**chat_assistant.new_request(), "engine": engine}
    st.session_state["chat_stage"] = "intake"
    default = "Sure. Share the GitHub repository link and the type of tests you need."
    return _chat_master_question("ask_new_repository", default, st.session_state["chat_request"])


def _chat_master_fallback(user_text: str) -> str | None:
    """Route free-form chat commands to VIO master when available."""
    client = _chat_vio_client()
    if client is None:
        return None

    state = st.session_state

    request = state.get("chat_request", {})
    run_state = state.get("vio_run_state", {})
    context = (
        f"Repository URL: {request.get('repo_url') or '-'}\n"
        f"Repository name: {request.get('repo_name') or '-'}\n"
        f"Requested test types: {', '.join(request.get('test_types') or []) or '-'}\n"
        f"Engine: {request.get('engine') or '-'}\n"
        f"Mode: {request.get('mode') or '-'}\n"
        f"Run stage: {_chat_stage_label(run_state) if run_state else 'Idle'}\n"
    )
    prompt = (
        "You are TestGenie master assistant. The user may ask commands beyond preset prompts. "
        "Answer clearly and briefly using the context below. If the user asks to run/trigger actions, "
        "reply with the exact next step they should type in this chat.\n\n"
        f"CURRENT CONTEXT:\n{context}\n"
        f"USER MESSAGE:\n{user_text}\n"
    )
    try:
        result = client.chat(AGENTS["master"], prompt, max_tokens=450, temperature=0.2, timeout=90, retries=2)
    except Exception:
        logger.exception("Master chat fallback failed")
        return None
    if result.get("ok") and result.get("content"):
        return str(result["content"]).strip()
    return None


def _chat_master_question(goal: str, default: str, request: dict | None = None) -> str:
    """Have master phrase stage questions simply; fallback to default text."""
    client = _chat_vio_client()
    if client is None:
        return default
    payload = {
        "goal": goal,
        "request": request or st.session_state.get("chat_request", {}),
    }
    prompt = (
        "You are TestGenie assistant. Rephrase the question below in very simple plain English. "
        "Keep it short (1-2 lines), friendly, actionable, and text-only (no markdown list).\n"
        f"CONTEXT: {json.dumps(payload)}\n"
        f"DEFAULT QUESTION: {default}"
    )
    try:
        res = client.chat(AGENTS["master"], prompt, max_tokens=120, temperature=0.2, timeout=60, retries=1)
    except Exception:
        logger.exception("Master question phrasing failed")
        return default
    text = str(res.get("content") or "").strip() if res.get("ok") else ""
    return text or default


def _chat_master_interpret(user_text: str) -> dict | None:
    """Primary chat interpreter: ask master to return structured routing JSON."""
    client = _chat_vio_client()
    if client is None:
        return None

    state = st.session_state

    request = state.get("chat_request", {})
    run_state = state.get("vio_run_state", {})
    context = {
        "repo_url": request.get("repo_url", ""),
        "repo_name": request.get("repo_name", ""),
        "test_types": request.get("test_types", []),
        "engine": request.get("engine", ""),
        "mode": request.get("mode", ""),
        "run_stage": _chat_stage_label(run_state) if run_state else "Idle",
    }
    prompt = (
        "Return STRICT JSON only (no markdown) using this schema: "
        '{"intent":"generate|rerun|status|count|other","repo_url":"","repo_name":"",'
        '"test_types":[],"target":"","max_components":0,"mode":"","engine":"","reply":""}.\n'
        "Rules: infer intent and fields from the user message; use empty values when unknown; "
        "test_types can contain only unit_test,integration_test,e2e_test,security,vulnerability; "
        "engine can be vio or ollama; mode can be recommended,continue,all,custom. "
        "For intent=other, fill reply with a concise plain-text response.\n"
        f"CONTEXT: {json.dumps(context)}\n"
        f"USER: {user_text}"
    )
    try:
        result = client.chat(AGENTS["master"], prompt, max_tokens=350, temperature=0.0, timeout=90, retries=2)
    except Exception:
        logger.exception("Master interpret failed")
        return None
    if not (result.get("ok") and result.get("content")):
        return None
    try:
        data = json.loads(result["content"])
    except Exception:
        return None
    if not isinstance(data, dict):
        return None

    parsed = {
        "intent": str(data.get("intent") or "other").strip().lower(),
        "repo_url": str(data.get("repo_url") or "").strip(),
        "repo_name": str(data.get("repo_name") or "").strip(),
        "test_types": [t for t in (data.get("test_types") or []) if t in
                       {"unit_test", "integration_test", "e2e_test", "security", "vulnerability"}],
        "target": str(data.get("target") or "").strip(),
        "max_components": int(data.get("max_components") or 0),
        "mode": str(data.get("mode") or "").strip().lower(),
        "engine": str(data.get("engine") or "").strip().lower(),
        "reply": str(data.get("reply") or "").strip(),
    }
    if parsed["intent"] not in {"generate", "rerun", "status", "count", "other"}:
        parsed["intent"] = "other"
    return parsed


def _chat_reply(text: str) -> str:
    state = st.session_state
    request = state["chat_request"]
    names = _chat_repo_names()
    stage = state.get("chat_stage")
    word = text.strip().strip("`")

    # Deterministic stage shortcuts so simple replies do not depend on master parsing.
    if stage == "engine":
        low = word.lower()
        if low in {"vio", "ollama"}:
            return _chat_set(engine=low)
    if stage == "specs":
        low = word.lower()
        if word.isdigit():
            return _chat_set(mode=CHAT_CUSTOM, max_components=int(word))
        if low in {"all", "run all", "all components"}:
            return _chat_set(mode=VIO_MODE_ALL)
        if low in {"continue", "uncovered", "uncovered components", "next"}:
            return _chat_set(mode=VIO_MODE_CONTINUE)
        if low in {"fresh", "fresh start", "recommended", "default"}:
            return _chat_set(mode=VIO_MODE_RECOMMENDED)
        if low in {"custom", "customised", "customized"}:
            return _chat_set(mode=CHAT_CUSTOM)

    parsed = _chat_master_interpret(text)
    if parsed is None:
        # Backup parser path when master is unavailable.
        client = state.get("vio_client") if state.get("vio_connected") else None
        ask = (lambda prompt, system=None: client.chat(
            AGENTS["master"], prompt, system=system, max_tokens=450, temperature=0.0, timeout=90, retries=2)
        ) if client else None
        parsed = chat_assistant.interpret(text, state["chat_messages"][:-1], names, request, ask)

    if state.get("chat_stage") == "custom" and not (parsed.get("target") or parsed.get("max_components")):
        word = text.strip().strip("`")
        if word.isdigit():
            parsed["max_components"] = int(word)
        elif re.fullmatch(r"[A-Za-z_][\w.]*", word):
            parsed["target"] = word
    if state.get("chat_stage") == "confirm" and re.fullmatch(
            r"(yes|y|ok|okay|start|go|go ahead|proceed|sure)[.!]?", text.strip().lower()):
        return _chat_set(confirmed=True)
    if (parsed.get("target") or parsed.get("max_components")) and not parsed.get("mode"):
        parsed["mode"] = CHAT_CUSTOM

    # Master agent is the primary intent router. Do not auto-promote conversational
    # replies to the old staged generate flow based only on extracted fields.
    intent = parsed["intent"]
    if intent == "status":
        return _chat_status()
    if intent == "count":
        request = chat_assistant.merge_request(request, parsed)
        state["chat_request"] = request
        if not (request["repo_url"] or request["repo_name"]):
            return chat_assistant.missing_question(request, names)
        with st.spinner("Counting components…"):
            return _chat_queue_text(request)
    if intent == "other":
        # If we're in a staged intake step, recover with the deterministic local parser
        # so short answers like "vio" or "yes" still advance the flow.
        if state.get("chat_stage") in {"intake", "engine", "specs", "custom", "confirm"}:
            client = state.get("vio_client") if state.get("vio_connected") else None
            ask = (lambda prompt, system=None: client.chat(
                AGENTS["master"], prompt, system=system, max_tokens=450, temperature=0.0, timeout=90, retries=2)
            ) if client else None
            fallback = chat_assistant.interpret(text, state["chat_messages"][:-1], names, request, ask)
            has_fields = any([
                fallback.get("repo_url"),
                fallback.get("repo_name"),
                fallback.get("test_types"),
                fallback.get("engine"),
                fallback.get("mode"),
                fallback.get("target"),
                fallback.get("max_components"),
            ])
            if fallback.get("intent") != "other" or has_fields:
                parsed = fallback
                intent = parsed.get("intent", "generate")
            else:
                return parsed.get("reply") or _chat_master_fallback(text) or chat_assistant.HELP_TEXT
        else:
            return parsed.get("reply") or _chat_master_fallback(text) or chat_assistant.HELP_TEXT

    request = chat_assistant.merge_request(request, parsed)
    state["chat_request"] = request
    if intent == "rerun":
        return _chat_rerun(request)
    return _chat_advance()


def _zip_folder(folder: Path, pattern: str) -> bytes:
    files = sorted(folder.glob(pattern))
    if not files:
        return b""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in files:
            archive.write(path, path.name)
    return buffer.getvalue()


def _render_export_menu(repo_path: str) -> None:
    """One Export menu for everything the last run wrote under tests/."""
    tests = Path(repo_path) / "tests"
    name = Path(repo_path).name
    scripts = _zip_folder(tests / "test_scripts", "*_test.py")
    specs = _zip_folder(tests / "specs", "*.md")
    if not (scripts or specs):
        return
    files = [
        ("Everything (specs, scripts, results)", _build_aligned_bundle(repo_path), f"{name}_tests.zip", "application/zip"),
        ("Test scripts", scripts, f"{name}_scripts.zip", "application/zip"),
        ("Specifications", specs, f"{name}_specs.zip", "application/zip"),
    ]
    for label, file_name, mime in (("Traceability (JSON)", "spec_traceability.json", "application/json"),
                                   ("Run history (JSON)", "run_history.json", "application/json"),
                                   ("Final report", "final_report.md", "text/markdown")):
        path = tests / file_name
        if path.exists() and path.stat().st_size:
            files.append((label, path.read_bytes(), file_name, mime))
    with st.popover("⬇️ Export", use_container_width=False):
        for label, data, file_name, mime in files:
            st.download_button(label, data, file_name, mime, key=f"export_{file_name}", use_container_width=True)
        single = [f"test_scripts/{p.name}" for p in sorted((tests / "test_scripts").glob("*_test.py"))]
        single += [f"specs/{p.name}" for p in sorted((tests / "specs").glob("*.md"))]
        picked = st.selectbox("A single file", [""] + single, key="export_single_file",
                              format_func=lambda value: value or "Select a script or spec")
        if picked:
            st.download_button(f"Download {picked.split('/')[-1]}", (tests / picked).read_bytes(),
                               file_name=picked.split("/")[-1], key="export_dl_single")


def _render_chat_options(slot) -> None:
    """Buttons for the current stage; a click runs the matching action and refreshes the page."""
    state = st.session_state
    stage = state.get("chat_stage")
    request = state["chat_request"]
    options = []
    if stage == "engine":
        options = [("Test with VIO", lambda: _chat_set(engine="vio")),
                   ("Test with Ollama", lambda: _chat_set(engine="ollama"))]
    elif stage == "specs":
        options = [(label, (lambda key=key: _chat_set(mode=key))) for key, label in CHAT_SPEC_LABELS.items()]
    elif stage == "confirm":
        options = [("Start run", lambda: _chat_set(confirmed=True)),
                   ("Choose differently", lambda: _chat_set(mode="", confirmed=False))]
    elif stage == "done":
        options = [("Uncovered components", lambda: _chat_set(mode=VIO_MODE_CONTINUE))]
        if request["engine"] == "vio" and _vio_rerunnable_count(state.get("repository_path")):
            options.append(("Re-run failed specs", lambda: _chat_rerun(request)))
        options.append(("Test another repository", _chat_new_repository))
    clicked = None
    with slot.container():
        for column, (label, action) in zip(st.columns(len(options) or 1), options):
            if column.button(label, key=f"chat_opt_{stage}_{label}", use_container_width=True):
                clicked = (label, action)
    if not clicked:
        return
    slot.empty()
    label, action = clicked
    state["chat_messages"].append({"role": "user", "content": label})
    with st.chat_message("user"):
        st.markdown(label)
    with st.chat_message("assistant"):
        with st.spinner("Working…"):
            reply = action()
    state["chat_messages"].append({"role": "assistant", "content": reply})
    st.rerun()


def _chat_stage_label(run_state: dict) -> str:
    if run_state.get("error"):
        return "Stopped"
    if run_state.get("finished"):
        return "Cancelled" if run_state.get("cancelled") else "Finished"
    if run_state.get("cancel"):
        return "Cancelling"
    if run_state.get("paused"):
        return "Paused"
    current = run_state.get("current") or []
    if any("(raising coverage)" in name for name in current):
        return f"Raising coverage to {COVERAGE_TARGET:g}%"
    total = run_state.get("total")
    if total and run_state.get("done", 0) >= total:
        return "Measuring coverage and writing the report"
    return "Writing and running tests" if total else "Preparing the component queue"


def _chat_component_rows(models: dict, coverage: dict) -> list:
    master = AGENTS["master"]
    components = [item for agent, data in models.items() if agent != master for item in data.get("components", [])]
    return results_view.component_rows(components, coverage, models.get(master, {}).get("plan_failures", []))


def _render_component_results(models: dict, coverage: dict) -> None:
    """Per-component table: honest status, tests, coverage and why/how to fix, with a status filter."""
    rows = _chat_component_rows(models, coverage)
    if not rows:
        return
    counts = results_view.status_counts(rows)
    shown = [status for status in results_view.STATUSES if counts[status]]
    st.markdown("**Results by component**")
    st.caption(" - ".join(f"{counts[status]} {status.lower()}" for status in shown))
    picked = st.multiselect("Show", results_view.STATUSES, default=[s for s in shown if s != "Passing"] or shown,
                            key="results_filter", label_visibility="collapsed")
    st.dataframe([row for row in rows if row["Status"] in picked], hide_index=True, width="stretch")


def _render_run_summary(models: dict, run_state: dict) -> None:
    """Totals, coverage target and script-level pass counts shared by the chat and results views."""
    master = AGENTS["master"]
    workers = [data for agent, data in models.items() if agent != master and "passed" in data]
    coverage = run_state.get("coverage") or {}
    columns = st.columns(4)
    columns[0].metric("Specs", sum(len([c for c in d.get("components", []) if c.get("spec")]) for d in workers))
    columns[1].metric("Passed", sum(d.get("passed", 0) for d in workers))
    columns[2].metric("Failed", sum(d.get("failed", 0) + d.get("errors", 0) for d in workers))
    columns[3].metric("Coverage", f"{coverage['percent']}%" if coverage and not coverage.get("error") else "-")
    runnable, fully, review = _chat_script_totals(models)
    if runnable:
        st.caption(f"{fully} of {runnable} runnable scripts pass completely"
                   + (f" - {review} flagged for review" if review else "")
                   + (" - coverage counts passing tests only" if coverage and not coverage.get("error") else ""))
    if coverage and not coverage.get("error"):
        met = coverage["target_met"]
        (st.success if met else st.warning)(
            f"Coverage target {coverage['target']:g}% {'reached' if met else 'not reached'}.")
    elif coverage.get("error") and run_state.get("finished"):
        st.info(f"Coverage was not measured: {coverage['error']}")


def _render_run_tracker(run_state: dict) -> None:
    """Phase strip, progress with ETA, per-stage counts and pause/cancel controls."""
    active = run_state.get("active")
    position = run_progress.phase_index(run_state)
    steps = [f"{'✅' if index < position else '🔄' if index == position else '⚪'} {name}"
             for index, name in enumerate(run_progress.PHASES)]
    st.markdown("  →  ".join(f"**{step}**" if index == position else step for index, step in enumerate(steps)))
    total, done = run_state.get("total"), run_state.get("done", 0)
    if active and total:
        eta = run_progress.eta_seconds(run_state)
        st.progress(min(done / total, 1.0), text=f"{done} of {total} specifications done"
                    + (f" - about {run_progress.format_duration(eta)} left" if eta else ""))
    counts = run_progress.stage_counts(run_state)
    if active and counts:
        st.caption(" · ".join(f"{run_progress.STAGE_LABELS[stage]}: {count}" for stage, count in counts.items()))
    working = [f"`{name.split(' (')[0]}` ({run_progress.STAGE_LABELS.get(stage, stage).lower()})"
               for name, stage in list((run_state.get("stages") or {}).items())[:4]]
    if active and working:
        st.caption("Working on: " + ", ".join(working))
    if not active:
        return
    if run_state.get("cancel"):
        st.info("Stopping after the components in progress finish. Finished work is saved.")
        return
    if run_state.get("paused"):
        st.warning("Paused: running components finish, then no new ones start.")
    pause, cancel, _ = st.columns([1, 1, 4])
    if pause.button("▶ Resume" if run_state.get("paused") else "⏸ Pause", key="run_pause", use_container_width=True):
        run_progress.toggle_pause(run_state)
        st.rerun(scope="app")
    if cancel.button("⏹ Cancel", key="run_cancel", use_container_width=True):
        run_progress.request_cancel(run_state)
        st.rerun(scope="app")


def _render_chat_status() -> None:
    """Chat view status: stage tracker, totals and a pointer to the Results page."""
    state = st.session_state
    run_state = state.get("vio_run_state") or {}
    st.markdown(f"**Status:** {_chat_stage_label(run_state)}")
    _render_run_tracker(run_state)
    if run_state.get("error"):
        st.error(f"The run stopped: {run_state['error']}")
    _render_run_summary(state.get("models", {}), run_state)
    if run_state.get("finished"):
        st.caption("Open the Results view for the component table, side-by-side tests, coverage gaps and history.")


@st.fragment(run_every=1)
def _chat_status_live() -> None:
    _render_chat_status()
    if not (st.session_state.get("vio_run_state") or {}).get("active"):
        st.rerun(scope="app")


def render_chat_status_panel() -> None:
    if (st.session_state.get("vio_run_state") or {}).get("active"):
        _chat_status_live()
    else:
        _render_chat_status()


@st.cache_data(ttl=300, show_spinner=False)
def _component_sources(repo_path: str) -> dict:
    return {c["module"] + "." + c["symbol"]: c["source_path"] for c in discover_components(repo_path)}


def _saved_run_repositories() -> list:
    if not REPOSITORIES_DIR.is_dir():
        return []
    return sorted(p.name for p in REPOSITORIES_DIR.iterdir() if (p / "tests" / "vio_run_state.json").is_file())


def _restore_last_run(repo_path: str) -> bool:
    """Rebuild the results view from the last saved run of a repository, e.g. after restarting the app."""
    saved = _vio_load_state(repo_path)
    if not saved:
        return False

    class _NoUsage:
        token_usage: dict = {}

    per_agent: dict = {}
    plan_failures: list = []
    for item in saved["results"]:
        (per_agent.setdefault(item["writer"], []) if item.get("writer") else plan_failures).append(item)
    models = {
        agent: _vio_aggregate_agent(
            agent, [t for t in saved["types"] if TYPE_TO_AGENT.get(t, AGENTS["unit"]) == agent], items,
            _NoUsage(), "done")
        for agent, items in per_agent.items()
    }
    report_file = Path(repo_path) / "tests" / "final_report.md"
    report = report_file.read_text(encoding="utf-8") if report_file.is_file() else ""
    models[AGENTS["master"]] = {
        "status": "done", "task_type": "supervision", "tokens": 0, "validations": [], "report": report,
        "plan_failures": [{"component": item["component"], "error": item.get("error", "")} for item in plan_failures],
        "repo": str(repo_path),
    }
    coverage: dict = {}
    tests = Path(repo_path) / "tests"
    try:
        found = summarize_coverage(repo_path, discover_components(repo_path),
                                   tests / "coverage" / "coverage.json", tests / "test_scripts")
        coverage = {**found, "target": COVERAGE_TARGET, "target_met": found["percent"] >= COVERAGE_TARGET, "rounds": 0}
    except (OSError, ValueError, KeyError):
        coverage = {}
    st.session_state.update(
        models=models, repository_path=str(repo_path),
        vio_run_state={"active": False, "finished": True, "report": report, "error": "", "coverage": coverage,
                       "ui_refreshed": True, "restored": True})
    return True


def _render_results_empty() -> None:
    st.info("There are no results in this session yet. Start a run in the Chat view, or open a saved one.")
    saved = _saved_run_repositories()
    if not saved:
        return
    picked = st.selectbox("Saved results", saved, key="results_open")
    if st.button("Open saved results", key="results_open_btn"):
        if _restore_last_run(str(REPOSITORIES_DIR / picked)):
            st.rerun()
        st.error("The saved run for that repository could not be read.")


def _render_side_by_side(models: dict) -> None:
    """Each scenario of the specification next to the test that implements it and its pytest outcome."""
    master = AGENTS["master"]
    items = [item for agent, data in models.items() if agent != master
             for item in data.get("components", []) if item.get("plan")]
    if not items:
        st.info("No stored specification scenarios for this run. Run again to see them here.")
        return
    items.sort(key=lambda item: (results_view.STATUSES.index(results_view.component_status(item)), item["component"]))
    status = {item["component"]: results_view.component_status(item) for item in items}
    name = st.selectbox("Component", [item["component"] for item in items], key="sbs_component",
                        format_func=lambda value: f"{value} - {status[value].lower()}")
    item = next(item for item in items if item["component"] == name)
    only_failing = st.toggle("Only failing scenarios", key="sbs_failing")
    if item.get("spec"):
        st.caption(f"Specification: `{Path(item['spec']).name}`"
                   + (f" - script: `{Path(item['script']).name}`" if item.get("script") else ""))
    icons = {"PASSED": "✅", "FAILED": "❌", "ERROR": "❌"}
    rows = [row for row in side_by_side.scenario_rows(item)
            if not only_failing or row["status"] in ("FAILED", "ERROR", "NOT RUN")]
    if not rows:
        st.success("Every scenario of this component passed.")
    for row in rows:
        with st.container(border=True):
            spec_col, test_col, result_col = st.columns([3, 4, 3])
            spec_col.markdown(f"**{row['id']} · {row['category']}**  \n*Inputs:* {row['inputs']}  \n"
                              f"*Expected:* {row['expected']}")
            if row["evidence"]:
                spec_col.caption("Source evidence: " + row["evidence"][:240])
            test_col.code(row["code"] or "# test function not found in the script", language="python")
            result_col.markdown(f"{icons.get(row['status'], '⚪')} **{row['status'].title()}**")
            if row["reason"]:
                result_col.code(row["reason"], language="text")
    output = (item.get("execution") or {}).get("output")
    if output:
        with st.expander("Full pytest output"):
            st.code(output, language="text")


def _worker_agent_states(models: dict) -> list:
    return [(agent, data) for agent, data in models.items() if agent != AGENTS["master"] and "passed" in data]


def _agent_test_type(agent: str, item: dict, data: dict) -> str:
    writer = str(item.get("writer") or agent)
    if writer == AGENTS.get("unit"):
        return "unit"
    if writer == AGENTS.get("integration"):
        return "integration"
    if writer == AGENTS.get("e2e"):
        return "e2e"
    if writer == AGENTS.get("security"):
        return "security"
    task_type = str(data.get("task_type") or "").lower()
    if "integration" in task_type:
        return "integration"
    if "e2e" in task_type:
        return "e2e"
    if "security" in task_type:
        return "security"
    return "unit"


def _results_case_catalog(models: dict) -> list:
    """Flatten planned scenarios into numbered test-case rows for UI + AI chat references."""
    cases = []
    counter = 1
    for agent, data in _worker_agent_states(models):
        for item in data.get("components", []):
            case_type = _agent_test_type(agent, item, data)
            for scenario in side_by_side.scenario_rows(item):
                case_id = f"TC-{counter:04d}"
                counter += 1
                cases.append({
                    "case_id": case_id,
                    "component": item.get("component", ""),
                    "test_type": case_type,
                    "scenario_id": scenario.get("id", ""),
                    "test_name": scenario.get("name", ""),
                    "category": scenario.get("category", ""),
                    "inputs": scenario.get("inputs", ""),
                    "expected": scenario.get("expected", ""),
                    "evidence": scenario.get("evidence", ""),
                    "code": scenario.get("code", ""),
                    "status": scenario.get("status", "NOT RUN"),
                    "reason": scenario.get("reason", ""),
                    "script": item.get("script", ""),
                    "spec": item.get("spec", ""),
                })
    return cases


def _refresh_worker_rollups(models: dict) -> None:
    """Recompute worker totals after manual script reruns/edits."""
    for _agent, data in _worker_agent_states(models):
        passed = failed = errors = skipped = 0
        runnable = fully_passing = needs_review = unresolved = 0
        tests = []
        for item in data.get("components", []):
            execution = item.get("execution") or {}
            item["tests"] = parse_pytest_output(execution.get("output", "")) if execution else []
            tests.extend({**test, "component": item.get("component", "")} for test in item.get("tests", []))
            if execution:
                runnable += 1
                passed += execution.get("passed", 0)
                failed += execution.get("failed", 0)
                errors += execution.get("errors", 0)
                skipped += execution.get("skipped", 0)
            if item.get("status") == "COMPLETED":
                fully_passing += 1
            if item.get("review_flags"):
                needs_review += 1
            if item.get("repair_unresolved"):
                unresolved += 1
        data.update(
            results=tests,
            passed=passed,
            failed=failed,
            errors=errors,
            skipped=skipped,
            runnable=runnable,
            fully_passing=fully_passing,
            needs_review=needs_review,
            unresolved=unresolved,
        )


def _update_component_execution(models: dict, component: str, execution: dict) -> bool:
    for _agent, data in _worker_agent_states(models):
        for item in data.get("components", []):
            if item.get("component") != component:
                continue
            item["execution"] = execution
            item["tests"] = parse_pytest_output(execution.get("output", ""))
            item["status"] = "COMPLETED" if execution.get("all_passed") else "FAILED"
            item["error"] = "" if execution.get("all_passed") else \
                "Tests did not all pass; inspect per-test results and environment requirements."
            return True
    return False


def _replace_test_function(script_code: str, test_name: str, replacement_function: str) -> tuple[str, bool]:
    """Replace one test function by name; returns (updated_code, replaced)."""
    try:
        tree = ast.parse(script_code)
    except SyntaxError:
        return script_code + "\n\n" + replacement_function.strip() + "\n", False
    lines = script_code.splitlines()
    target = None
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == test_name:
            target = node
            break
    if target is None or getattr(target, "end_lineno", None) is None:
        return script_code + "\n\n" + replacement_function.strip() + "\n", False
    start = int(target.lineno) - 1
    end = int(target.end_lineno)
    merged = lines[:start] + replacement_function.strip().splitlines() + lines[end:]
    return "\n".join(merged).rstrip() + "\n", True


def _extract_target_function(candidate: str, desired_name: str) -> str:
    """Pull one function body from model output; fallback to full candidate text."""
    sources = side_by_side.function_sources(candidate)
    if desired_name in sources:
        return sources[desired_name]
    for name, body in sources.items():
        if name.startswith("test_"):
            return body
    return candidate


def _case_from_prompt(user_text: str, case_index: dict, active_case_id: str) -> dict | None:
    token = re.search(r"\bTC-\d{4}\b", user_text.upper())
    if token:
        return case_index.get(token.group(0))
    if active_case_id:
        return case_index.get(active_case_id)
    return None


def _target_test_name(user_text: str, default_name: str) -> str:
    """Pick an explicit test function name from user text when present."""
    hit = re.search(r"\b(test_[A-Za-z0-9_]+)\b", user_text)
    if hit:
        return hit.group(1)
    qualified = re.search(r"\b([A-Za-z0-9_]+::test_[A-Za-z0-9_]+)\b", user_text)
    if qualified:
        return qualified.group(1).split("::")[-1]
    return default_name


def _results_ai_action_kind(user_text: str) -> str:
    text = user_text.lower()
    wants_fix = bool(re.search(r"\b(fix|change|rewrite|modify|update)\b", text))
    wants_run = bool(re.search(r"\b(run|rerun|re-run|execute|retry)\b", text))
    if wants_fix:
        return "fix_rerun"
    if wants_run:
        return "rerun"
    return ""


def _is_affirmative(user_text: str) -> bool:
    return bool(re.fullmatch(r"\s*(yes|y|ok|okay|sure|go|proceed|run|do it|continue)\s*[.!]?\s*", user_text.lower()))


def _is_negative(user_text: str) -> bool:
    return bool(re.fullmatch(r"\s*(no|n|cancel|stop|don'?t|do not)\s*[.!]?\s*", user_text.lower()))


def _resolve_case_script_path(repo_path: str, script_ref: str) -> Path | None:
    """Resolve saved script references that may be absolute paths or bare filenames."""
    if not script_ref:
        return None
    candidate = Path(script_ref)
    if candidate.is_file():
        return candidate

    repo = Path(repo_path)
    scripts_dir = repo / "tests" / "test_scripts"

    # Common case: stored as bare file name.
    by_name = scripts_dir / candidate.name
    if by_name.is_file():
        return by_name

    # Sometimes stored as path relative to repo root.
    rel_repo = repo / script_ref
    if rel_repo.is_file():
        return rel_repo

    # Last-resort: any generated script with same name anywhere under test_scripts.
    matches = list(scripts_dir.rglob(candidate.name)) if scripts_dir.exists() else []
    return matches[0] if matches else None


def _results_ai_reply(user_text: str, case_index: dict, models: dict, repo_path: str, active_case_id: str = "") -> str:
    case = _case_from_prompt(user_text, case_index, active_case_id)
    wants_fix = bool(re.search(r"\b(fix|change|rewrite|modify|update)\b", user_text.lower()))
    wants_rerun = bool(re.search(r"\b(run|rerun|re-run|execute|retry)\b", user_text.lower()))

    if wants_fix and not case:
        return "Pick a case in the table first, or mention a case number like TC-0003 so I know which test to edit."
    if wants_fix and not case.get("script"):
        return f"{case['case_id']} has no saved script path yet, so I can't edit it."

    if wants_rerun and not wants_fix:
        if not case:
            return "Pick a case in the table first, or mention a case number like TC-0003 so I know what to run."
        script_path = _resolve_case_script_path(repo_path, str(case.get("script") or ""))
        if script_path is None:
            return (
                f"Script file not found for {case['case_id']}. This case likely failed before script generation "
                "or the script was cleaned up. Re-run this component/spec first."
            )
        execution = run_pytest_single(repo_path, script_path.name)
        _update_component_execution(models, case["component"], execution)
        _refresh_worker_rollups(models)
        verdict = "now passes" if execution.get("all_passed") else "still fails"
        return (
            f"Re-ran {case['case_id']} ({script_path.name}): {execution.get('passed', 0)} passed, "
            f"{execution.get('failed', 0)} failed, {execution.get('errors', 0)} errors ({verdict})."
        )

    client = _chat_vio_client()
    if wants_fix and client is None:
        return "VIO master is unavailable right now, so I can't generate a patch automatically."

    if wants_fix:
        script_path = _resolve_case_script_path(repo_path, str(case.get("script") or ""))
        if script_path is None:
            return (
                f"Script file not found for {case['case_id']}. This case likely failed before script generation "
                "or the script was cleaned up. Re-run this component/spec first, then ask me to fix it."
            )
        target_name = _target_test_name(user_text, case["test_name"])
        max_rounds = 3
        latest_execution = None
        replaced_any = False
        round_notes = []
        last_failure = case.get("reason") or ""

        for round_no in range(1, max_rounds + 1):
            current = script_path.read_text(encoding="utf-8")
            prompt = (
                "You are fixing one pytest function. Return ONLY one python function body for the target test, no fences.\n"
                f"Case: {case['case_id']}\n"
                f"Component: {case['component']}\n"
                f"Test type: {case['test_type']}\n"
                f"Scenario: {case['scenario_id']} {case['category']}\n"
                f"Inputs: {case['inputs']}\n"
                f"Expected: {case['expected']}\n"
                f"Failure: {last_failure}\n"
                f"Target test function name: {target_name}\n\n"
                f"Current test function code:\n{case['code'] or '# missing function source'}\n\n"
                f"Full script for context:\n{current[:9000]}\n\n"
                f"User request:\n{user_text}\n"
            )
            response = client.chat(
                AGENTS["master"], prompt, max_tokens=1400, temperature=0.1, timeout=120, retries=1)
            if not response.get("ok") or not response.get("content"):
                round_notes.append(f"round {round_no}: no patch returned")
                break

            candidate = extract_python_code(response["content"]).strip() or str(response["content"]).strip()
            replacement = _extract_target_function(candidate, target_name)
            if "def " not in replacement:
                round_notes.append(f"round {round_no}: invalid function patch")
                break

            updated, replaced = _replace_test_function(current, target_name, replacement)
            if updated == current:
                round_notes.append(f"round {round_no}: patch made no change")
                break
            replaced_any = replaced_any or replaced
            script_path.write_text(updated, encoding="utf-8")

            latest_execution = run_pytest_single(repo_path, script_path.name)
            _update_component_execution(models, case["component"], latest_execution)
            _refresh_worker_rollups(models)
            round_notes.append(
                f"round {round_no}: {latest_execution.get('passed', 0)} passed, "
                f"{latest_execution.get('failed', 0)} failed, {latest_execution.get('errors', 0)} errors"
            )
            if latest_execution.get("all_passed"):
                break
            last_failure = (latest_execution.get("output") or "")[-1800:] or last_failure

        if latest_execution is None:
            return "I couldn't generate a usable patch for that case right now."

        verdict = "now passes" if latest_execution.get("all_passed") else "still fails"
        return (
            f"Updated {case['case_id']} ({target_name}) in {script_path.name} and re-ran pytest: "
            f"{latest_execution.get('passed', 0)} passed, {latest_execution.get('failed', 0)} failed, "
            f"{latest_execution.get('errors', 0)} errors ({verdict}). "
            f"Attempts: {', '.join(round_notes)}."
            + (" Function replaced." if replaced_any else " Function was appended because it was not found in the script.")
        )

    if case:
        if client is None:
            if case.get("status") in {"FAILED", "ERROR"}:
                return f"{case['case_id']} failed because: {case.get('reason') or 'pytest reported a failure without a parsed reason.'}"
            return f"{case['case_id']} checks {case.get('expected') or 'the expected behavior from the spec'} using `{case.get('test_name')}`."
        prompt = (
            "Explain this test case and (if failed) why it failed. Keep it concise and practical.\n"
            f"Case: {case['case_id']}\n"
            f"Test type: {case['test_type']}\n"
            f"Component: {case['component']}\n"
            f"Scenario ID: {case['scenario_id']}\n"
            f"Scenario category: {case['category']}\n"
            f"Inputs: {case['inputs']}\n"
            f"Expected: {case['expected']}\n"
            f"Status: {case['status']}\n"
            f"Failure reason: {case['reason']}\n"
            f"Test code:\n{case['code'][:3000]}\n"
            f"User question:\n{user_text}\n"
        )
        response = client.chat(AGENTS["master"], prompt, max_tokens=500, temperature=0.2, timeout=90, retries=1)
        if response.get("ok") and response.get("content"):
            return str(response["content"]).strip()
    return "Pick a case from the table, then ask naturally (for example: 'why it failed?' or 'fix this to match actual behavior')."


def _render_results_ai_chat(cases: list, models: dict, repo_path: str) -> None:
    """Floating-like AI helper for case-level explain/fix actions in Results view."""
    st.markdown("""
<style>
.results-ai-chip {
  border: 1px solid rgba(59,130,246,.35);
  background: rgba(59,130,246,.10);
  border-radius: 999px;
  padding: .25rem .65rem;
  font-size: .8rem;
}
</style>
""", unsafe_allow_html=True)
    state = st.session_state
    state.setdefault("results_ai_messages", [
        {"role": "assistant", "content": "I'm the Results AI helper. Mention a case like TC-0003 and ask why it failed, or say 'fix TC-0003 ...'."}
    ])
    state.setdefault("results_ai_pending_prompt", "")
    state.setdefault("results_ai_pending_action", None)
    case_index = {case["case_id"]: case for case in cases}
    active_case_id = str(state.get("results_active_case_id") or "")

    def ask_and_store(prompt_text: str) -> None:
        state["results_ai_messages"].append({"role": "user", "content": prompt_text})
        pending = state.get("results_ai_pending_action")
        if pending:
            if _is_affirmative(prompt_text):
                run_prompt = str(pending.get("prompt") or "")
                reply = _results_ai_reply(run_prompt, case_index, models, repo_path, active_case_id=active_case_id)
                state["results_ai_pending_action"] = None
                state["results_ai_messages"].append({"role": "assistant", "content": reply})
                return
            if _is_negative(prompt_text):
                state["results_ai_pending_action"] = None
                state["results_ai_messages"].append({
                    "role": "assistant",
                    "content": "Cancelled. Tell me when you want to run or fix this case."
                })
                return
            state["results_ai_messages"].append({
                "role": "assistant",
                "content": "Please reply with yes or no for the pending run/fix action."
            })
            return

        action_kind = _results_ai_action_kind(prompt_text)
        if action_kind:
            case = _case_from_prompt(prompt_text, case_index, active_case_id)
            if not case:
                state["results_ai_messages"].append({
                    "role": "assistant",
                    "content": "Pick a case in the table first, or mention one like TC-0003."
                })
                return
            action_text = (
                "apply AI fix + rerun the script (up to 3 repair rounds)"
                if action_kind == "fix_rerun" else
                "rerun this script"
            )
            state["results_ai_pending_action"] = {
                "kind": action_kind,
                "case_id": case["case_id"],
                "prompt": prompt_text,
            }
            state["results_ai_messages"].append({
                "role": "assistant",
                "content": (
                    f"Do you want me to {action_text} for {case['case_id']} "
                    f"({case.get('test_type', 'test')} type) now? Reply yes or no."
                )
            })
            return

        reply = _results_ai_reply(prompt_text, case_index, models, repo_path, active_case_id=active_case_id)
        state["results_ai_messages"].append({"role": "assistant", "content": reply})

    with st.popover("💬 AI Test Assistant", use_container_width=False):
        if active_case_id:
            st.caption(f"Active case: {active_case_id}. You can ask directly, or mention another case ID.")
        else:
            st.caption("Pick a case in the table first, or mention one like TC-0002.")
        st.markdown("<span class='results-ai-chip'>Explain</span> <span class='results-ai-chip'>Why failed</span> <span class='results-ai-chip'>Fix and rerun</span>", unsafe_allow_html=True)

        pending_prompt = (state.get("results_ai_pending_prompt") or "").strip()
        if pending_prompt:
            with st.spinner("Thinking..."):
                ask_and_store(pending_prompt)
            state["results_ai_pending_prompt"] = ""
            st.rerun()

        for msg in state["results_ai_messages"][-8:]:
            with st.chat_message(msg["role"]):
                st.markdown(msg["content"])
        prompt = st.chat_input("Ask about a case number...", key="results_ai_input")
        if prompt:
            with st.spinner("Thinking..."):
                ask_and_store(prompt)
            st.rerun()


def _render_case_workbench(models: dict, repo_path: str) -> None:
    """Case-first side-by-side view with numbering, type labels and targeted rerun."""
    cases = _results_case_catalog(models)
    if not cases:
        st.info("No scenario-level cases found yet.")
        return

    table = [{
        "Case": case["case_id"],
        "Type": case["test_type"],
        "Status": case["status"],
        "Component": case["component"],
        "Scenario": case["scenario_id"],
        "Test": case["test_name"],
    } for case in cases]
    st.markdown("**Numbered test cases**")
    st.dataframe(table, hide_index=True, width="stretch")

    picked_id = st.selectbox("Select a case", [case["case_id"] for case in cases], key="results_case_pick")
    st.session_state["results_active_case_id"] = picked_id
    picked = next(case for case in cases if case["case_id"] == picked_id)
    description = (
        f"Type: {picked['test_type']}\n\n"
        f"Component: `{picked['component']}`\n\n"
        f"Scenario: {picked['scenario_id'] or '-'} {picked['category'] or ''}\n\n"
        f"Inputs: {picked['inputs'] or '-'}\n\n"
        f"Expected: {picked['expected'] or '-'}\n\n"
        f"Evidence: {picked['evidence'] or '-'}"
    )
    left, middle, right = st.columns([3, 4, 3])
    left.markdown("**Description**")
    left.markdown(description)
    middle.markdown("**Script**")
    middle.code(picked.get("code") or "# test function not found in script", language="python")
    right.markdown("**Result**")
    badge = "✅" if picked["status"] == "PASSED" else ("❌" if picked["status"] in {"FAILED", "ERROR"} else "⚪")
    right.markdown(f"{badge} **{picked['status']}**")
    if picked.get("reason"):
        right.code(picked["reason"], language="text")

    actions = st.columns([1, 3])
    if actions[0].button("Run this script", key=f"rerun_case_{picked_id}", use_container_width=True):
        if picked.get("script"):
            script_path = _resolve_case_script_path(repo_path, str(picked.get("script") or ""))
            if script_path is None:
                st.warning(
                    "No runnable script file found for this case yet. Re-run generation for this component first."
                )
            else:
                execution = run_pytest_single(repo_path, script_path.name)
                _update_component_execution(models, picked["component"], execution)
                _refresh_worker_rollups(models)
                st.success(
                    f"Re-ran {script_path.name}: {execution.get('passed', 0)} passed, "
                    f"{execution.get('failed', 0)} failed, {execution.get('errors', 0)} errors."
                )
                st.rerun()
        else:
            st.warning("No script path recorded for this case.")
    actions[1].caption(
        f"Use 💬 AI Test Assistant and ask: Explain {picked_id} or Fix {picked_id}."
    )

    _render_results_ai_chat(cases, models, repo_path)


def _render_coverage_gaps(coverage: dict, repo_path: str) -> None:
    """Uncovered source lines per component, with an action that writes tests for them."""
    if not coverage or coverage.get("error"):
        st.info("Coverage was not measured for this run"
                + (f": {coverage['error']}" if coverage else "."))
        return
    gaps = [row for row in coverage["components"] if row.get("percent") is not None and row["percent"] < 100]
    if not gaps:
        st.success("Every measured component is fully covered.")
        return
    by_name = {row["component"]: row for row in gaps}
    picked = st.selectbox("Component", list(by_name), key="gap_component",
                          format_func=lambda value: f"{value} - {by_name[value]['percent']}%")
    row = by_name[picked]
    st.caption(f"Target {coverage.get('target', COVERAGE_TARGET):g}% overall. Lines marked ! are not run by any passing test.")
    source = _component_sources(repo_path).get(picked)
    try:
        lines = (Path(repo_path) / source).read_text(encoding="utf-8").splitlines() if source else []
    except (OSError, UnicodeError):
        lines = []
    uncovered = row.get("missing_lines") or coverage_view.parse_ranges(row.get("uncovered_lines"))
    if lines and uncovered:
        st.code(coverage_view.annotated_source(lines, uncovered), language="text")
    elif row.get("uncovered_lines"):
        st.write(f"Uncovered lines: {row['uncovered_lines']}")
    if not row["specified"]:
        st.caption("This component has no specification yet. Use 'Uncovered components' in the Chat view to write one.")
    elif st.button("Add tests for these lines", key="gap_topup"):
        error = run_vio_topup(repo_path, row)
        if error:
            st.error(error)
        else:
            st.rerun()


def _render_history(repo_path: str) -> None:
    runs = run_history.load(repo_path)
    if not runs:
        st.info("No runs have been recorded for this repository yet.")
        return
    st.dataframe(run_history.rows(runs), hide_index=True, width="stretch")
    if len(runs) < 2:
        return
    diff = run_history.compare(runs[-2], runs[-1])
    st.markdown("**Latest run compared with the one before**")
    columns = st.columns(3)
    latest = runs[-1]
    columns[0].metric("Coverage", f"{latest['coverage']}%" if latest["coverage"] is not None else "-",
                      delta=diff["coverage_delta"])
    columns[1].metric("Tests passed", latest["passed"], delta=diff["passed_delta"])
    columns[2].metric("Tests failed", latest["failed"], delta=diff["failed_delta"], delta_color="inverse")
    for label, moves in (("Improved", diff["improved"]), ("Got worse", diff["regressed"])):
        if moves:
            st.markdown(f"**{label}**")
            for name, before, after in moves:
                st.markdown(f"- `{name}`: {before} → {after}")
    if diff["new"]:
        st.caption(f"{len(diff['new'])} component(s) are new in the latest run.")


def _results_content() -> None:
    state = st.session_state
    run_state = state.get("vio_run_state") or {}
    models = state.get("models", {})
    coverage = run_state.get("coverage") or {}
    repo_path = state.get("repository_path", "")
    st.markdown(f"**Status:** {_chat_stage_label(run_state)}")
    _render_run_tracker(run_state)
    if run_state.get("error"):
        st.error(f"The run stopped: {run_state['error']}")
    topup = run_state.get("topup_summary")
    if run_state.get("finished") and topup:
        st.info(f"Coverage top-up for `{topup['component']}`: {topup['result'] or 'no change'}.")
    if run_state.get("cancelled"):
        st.warning("This run was cancelled. Finished components are saved; continue from the Chat view.")
    _render_run_summary(models, run_state)
    _render_component_results(models, coverage)
    if run_state.get("active"):
        return
    _render_export_menu(repo_path)
    workbench, sbs, gaps, history, report = st.tabs(
        ["Case workbench", "Side by side", "Coverage gaps", "History", "Report"])
    with workbench:
        _render_case_workbench(models, repo_path)
    with sbs:
        _render_side_by_side(models)
    with gaps:
        _render_coverage_gaps(coverage, repo_path)
    with history:
        _render_history(repo_path)
    with report:
        summary = run_state.get("rerun_summary")
        if summary:
            st.write(f"Re-run: {summary['fixed']} of {summary['attempted']} failed specs now pass.")
        st.markdown(run_state.get("report") or "No final report was written for this run.")


@st.fragment(run_every=1)
def _results_live() -> None:
    _results_content()
    if not (st.session_state.get("vio_run_state") or {}).get("active"):
        st.rerun(scope="app")


def render_results_page() -> None:
    """Review page: live tracker while running, then table, side-by-side tests, coverage gaps and history."""
    state = st.session_state
    run_state = state.get("vio_run_state") or {}
    if run_state.get("active"):
        _results_live()
    elif any("passed" in data for data in state.get("models", {}).values()):
        _results_content()
    else:
        _render_results_empty()


def render_chat_workspace() -> None:
    """Free-text chat front end for the VIO pipeline."""
    state = st.session_state
    state.setdefault("chat_messages", [])
    if len(state["chat_messages"]) == 1 and "Share the GitHub repository link and the type of tests you need" in \
            str(state["chat_messages"][0].get("content", "")):
        state["chat_messages"] = []
    state.setdefault("chat_request", chat_assistant.new_request())
    state.setdefault("chat_stage", "intake")

    run_state = state.get("vio_run_state") or {}
    if state.get("chat_run_pending") and run_state.get("finished"):
        state.update(chat_run_pending=False, chat_stage="done")
        state["chat_messages"].append({"role": "assistant", "content": _chat_run_summary(run_state)})

    for message in state["chat_messages"]:
        with st.chat_message(message["role"]):
            st.markdown(message["content"])

    if state["chat_request"]["engine"] == "vio" and state.get("vio_run_state"):
        render_chat_status_panel()
    if state["chat_stage"] == "done":
        if state["chat_request"]["engine"] == "ollama":
            render_coverage_panel(state.get("last_coverage"))
        _render_export_menu(state.get("repository_path", ""))

    text = st.chat_input("Type your message…")
    if text:
        state["chat_messages"].append({"role": "user", "content": text})
        with st.chat_message("user"):
            st.markdown(text)
        with st.chat_message("assistant"):
            with st.spinner("Thinking…"):
                reply = _chat_reply(text)
        state["chat_messages"].append({"role": "assistant", "content": reply})
        st.rerun()

    if len(state["chat_messages"]) > 0 and st.button("New conversation", key="chat_clear"):
        state.update(chat_messages=[],
                     chat_request=chat_assistant.new_request(), chat_stage="intake")
        st.rerun()


def main() -> None:
    """
    Main application entry point.
    
    Manages the complete workflow:
    1. Repository input and cloning
    2. Test prompt generation
    3. Approval gate with clipboard automation
    4. Results collection and display
    """
    # Configure Streamlit page settings
    st.set_page_config(
        page_title="TestGenie",
        page_icon="🧞",
        layout="wide",
        initial_sidebar_state="expanded",
    )
    
    # Initialize session state
    initialize_state()

    # Apply Aumovio theme (orange + deep purple; dark default, light toggle)
    st.markdown(aumovio_css(st.session_state.get("theme", "dark")), unsafe_allow_html=True)

    # Landing page first; the chat then asks which engine to use
    if st.session_state.get("mode") is None:
        render_mode_gate()
        return

    render_top_bar()

    # Chat and results return early; Ollama workspace continues with the existing flow below
    view = st.radio("View", [VIEW_CHAT, VIEW_RESULTS], horizontal=True,
                    key="app_view", label_visibility="collapsed")
    if view == VIEW_CHAT:
        render_chat_workspace()
        return
    if view == VIEW_RESULTS:
        render_results_page()
        return

    # ── Ollama workspace (existing flow, unchanged) ──
    render_sidebar()

    # Header and introduction
    st.markdown("## 🧪 Enterprise Test Generator")
    st.markdown("Generate comprehensive test suites automatically from your GitHub repository")
    
    st.markdown("---")
    
    # Repository input section
    repository_url = st.text_input(
        "Enter GitHub Repository URL",
        placeholder="https://github.com/owner/repository",
        help="Public GitHub or GitHub Enterprise repository URL.",
    )
    
    # Generate prompt button
    if st.button("Generate Test Prompt", type="primary", use_container_width=True):
        generate_repository_prompt(repository_url)
    
    st.markdown("---")

    # Display repository information status
    st.markdown("### Repository information")
    details = st.columns(4)
    with details[0]:
        status_badge("Repository Name", st.session_state["repository_name"] or "—", "◈")
    with details[1]:
        status_badge("Clone Status", st.session_state["clone_status"], "↻")
    with details[2]:
        status_badge("Prompt Status", st.session_state["prompt_status"], "✓")
    with details[3]:
        status_badge("Local Path", st.session_state["repository_path"] or "—", "⌂")

    if st.session_state["generated_prompt"] and st.session_state["approval_status"] == "Pending":
        st.markdown("---")
        st.markdown("### 📝 Generated Test_Prompt.md")
        with st.expander("📋 View Full Prompt", expanded=False):
            st.markdown(st.session_state["generated_prompt"])
        st.download_button(
            "📥 Download Test_Prompt.md",
            data=st.session_state["generated_prompt"],
            file_name="Test_Prompt.md",
            mime="text/markdown",
        )
        display_approval_gate()

    if st.session_state["proceed_clicked"]:
        st.markdown("---")
        display_orchestrator_section()
        display_results_section()


@st.fragment(run_every=1)
def render_vio_results_panel() -> None:
    """Refresh agent status and details without rerunning the main script."""
    models = st.session_state.get("models", {})
    run_state = st.session_state.get("vio_run_state", {})

    # Buttons outside this fragment only re-evaluate `disabled` on a full rerun, so trigger one when a run ends.
    if run_state.get("finished") and not run_state.get("ui_refreshed"):
        run_state["ui_refreshed"] = True
        st.rerun(scope="app")

    st.markdown("#### Agent Branches")
    st.caption("The diagram shows agent status. Use the agent buttons below to view details.")
    st.markdown(_branch_diagram_html(models), unsafe_allow_html=True)
    render_branch_controls()
    render_model_detail()

    if run_state.get("active"):
        st.info("VIO generation is running in the background. Agent selections and results update live.")
        if run_state.get("total"):
            done, total = run_state.get("done", 0), run_state["total"]
            st.progress(min(done / total, 1.0), text=f"{done} of {total} specifications completed")
            if run_state.get("current"):
                st.caption("Working on: " + ", ".join(run_state["current"][:4]))
    elif run_state.get("error"):
        st.error(f"VIO generation stopped: {run_state['error']}")
    elif run_state.get("finished"):
        st.success("VIO generation complete.")
        if run_state.get("bundle"):
            st.download_button(
                "📦 Download aligned bundle (specs + scripts + pytest results)",
                data=run_state["bundle"], file_name="vio_aligned_tests.zip",
                mime="application/zip", key="dl_vio_bundle",
            )
        render_coverage_panel(run_state.get("coverage"))
        summary = run_state.get("rerun_summary")
        if summary:
            st.markdown("#### Re-run of failed specs")
            st.write(f"{summary['fixed']} of {summary['attempted']} failed specs now pass.")
            for item in summary["needs_review"]:
                st.warning(f"{item['component']}: {item['reason'] or 'still failing'}")

    ran = [model for model in models.values() if model.get("results") is not None or "passed" in model]
    if ran:
        total_passed = sum(model.get("passed", 0) for model in ran)
        total_failed = sum(model.get("failed", 0) for model in ran)
        total_errors = sum(model.get("errors", 0) for model in ran)
        total_tokens = sum(model.get("tokens", 0) for model in ran)
        total_specs = sum(len([c for c in model.get("components", []) if c.get("spec")]) for model in ran)
        st.markdown("#### Results")
        columns = st.columns(5)
        for column, label, value in zip(
            columns,
            ("Specs", "Passed", "Failed", "Errors", "Tokens"),
            (total_specs, total_passed, total_failed, total_errors, total_tokens),
        ):
            column.metric(label, value)

    report = run_state.get("report") or st.session_state.get("vio_report")
    if report:
        with st.expander("📝 Final report (by test_generation_master)", expanded=True):
            st.markdown(report)


main()
