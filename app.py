"""
Streamlit UI for the repository-specific Copilot test prompt generator.

This application provides a web-based interface for:
- Cloning GitHub repositories
- Generating test prompts from repository structure
- Automating clipboard copy and VS Code integration
- Managing test result collection and packaging
- Displaying generated test artifacts (specs, scripts, coverage)
- ORCHESTRATOR: Master-Worker intelligent test generation with Ollama models
"""

from __future__ import annotations

import io
import os
import re
import subprocess
import time
import zipfile
import logging
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.parse import urlparse
from datetime import datetime
from typing import List

import streamlit as st

# Import the copilot_bridge module for automation functions
from src import copilot_bridge

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

# VIO (Aumovio online agents) + environment loading
from src.vio_client import VIOClient, AGENTS, AGENT_MODELS, TYPE_TO_AGENT

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
        "mode": None,  # None (gate) | "vio" | "ollama"
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


def run_test_orchestration(selected_types: List[str]) -> None:
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
    task_router = st.session_state["task_router"]
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
                    batch_results[task_id] = {"status": "SKIPPED", "path": cached_path}
                    continue
                
                # Mark in progress
                cache_manager.mark_in_progress(repo_hash, task_type_str)
                
                # Select model
                model = model_selector.select_model(task_type_str)
                if not model:
                    st.error(f"  ❌ {task_id}: No available model")
                    task_router.mark_task_failed(task_id, "No available model")
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
                        batch_results[task_id] = {"status": "COMPLETED", "path": result_path}
                    
                    elif validation_result.needs_correction:
                        # Apply correction
                        st.write(f"  ⚠️ {task_id}: Needs correction, retrying...")
                        
                        corrected_output, _ = ollama_client.generate(
                            model, validation_result.correction_prompt
                        )
                        
                        result_path = save_orchestration_result(
                            repo_path, task_id, corrected_output
                        )
                        
                        cache_manager.mark_complete(
                            repo_hash, task_type_str, result_path, file_hashes
                        )
                        
                        st.write(f"  ✅ {task_id}: Corrected and saved")
                        task_router.mark_task_complete(task_id, result_path)
                        batch_results[task_id] = {"status": "COMPLETED", "path": result_path}
                    
                    else:
                        # Failed validation
                        error_msg = validation_result.error_message or "Output validation failed"
                        st.error(f"  ❌ {task_id}: {error_msg}")
                        cache_manager.mark_failed(repo_hash, task_type_str, error_msg)
                        task_router.mark_task_failed(task_id, error_msg)
                        batch_results[task_id] = {"status": "FAILED", "error": error_msg}
                
                except Exception as e:
                    error_msg = f"Execution error: {str(e)}"
                    st.error(f"  ❌ {task_id}: {error_msg}")
                    cache_manager.mark_failed(repo_hash, task_type_str, error_msg)
                    task_router.mark_task_failed(task_id, error_msg)
                    batch_results[task_id] = {"status": "FAILED", "error": error_msg}
            
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


def create_task_prompt(task_type: str, repo_path: str) -> str:
    """Create an Ollama prompt for a specific task type, grounded in real code.

    Combines three inputs:
    1. High-level guidance distilled from the repo's Test_Prompt.md (if present).
    2. The actual repository source code (ground truth).
    3. An explicit list of importable symbols parsed from that source.
    """
    repo_path_obj = Path(repo_path)

    source_context, package = gather_source_context(str(repo_path_obj))

    # 1. High-level guidance from the folder prompt (kept short so it doesn't
    #    derail a small model into writing prose instead of code).
    guidance = ""
    prompt_file = repo_path_obj / "Test_Prompt.md"
    if prompt_file.exists():
        try:
            full = prompt_file.read_text(encoding="utf-8")
            # Take the opening objective paragraph(s) only.
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
            f"- Use the AAA pattern and name tests test_*.\n"
            f"- Output ONLY a single ```python fenced code block, and you MUST end with a closing ```.\n\n"
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
        f"use the AAA pattern, and wrap all code in ```python fenced blocks."
    )






def extract_python_code(content: str) -> str:
    """Extract Python source from an LLM markdown response.

    Handles three cases:
    1. One or more complete ```python ... ``` fenced blocks.
    2. An UNCLOSED fence (model output truncated before the closing ```): take
       everything after the first fence line, up to a closing fence if present.
    3. No fences at all: return the raw content.
    """
    # Case 1: complete language-tagged blocks.
    blocks = re.findall(r"```(?:python|py)\s*\n(.*?)```", content, re.DOTALL)
    if not blocks:
        blocks = re.findall(r"```\s*\n(.*?)```", content, re.DOTALL)
    if blocks:
        return "\n\n".join(b.strip("\n") for b in blocks if b.strip())

    # Case 2: an opening fence with no matching close (truncated output).
    m = re.search(r"```[a-zA-Z0-9_+-]*[ \t]*\n", content)
    if m:
        rest = content[m.end():]
        # Cut at a closing fence if one exists further down.
        rest = re.split(r"\n```", rest)[0]
        return rest.strip()

    # Case 3: bare code.
    return content.strip()



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
        elif result["status"] == "FAILED":
            st.error(f"❌ {task_id}: {result.get('error', 'Unknown error')}")
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

    script_files = sorted(scripts_dir.glob("*_test.py")) if scripts_dir and scripts_dir.exists() else []

    # Write a conftest.py so pytest can import the repo's package from source.
    if scripts_dir is not None and script_files:
        try:
            import_root, package = discover_package_import_root(Path(repo_path))
            conftest = scripts_dir / "conftest.py"
            conftest.write_text(
                "import sys\n"
                "from pathlib import Path\n"
                f"# Add the repository import root so `{package or 'the package'}` is importable.\n"
                f"sys.path.insert(0, r\"{import_root}\")\n"
                f"sys.path.insert(0, r\"{Path(repo_path)}\")\n",
                encoding="utf-8",
            )
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
    scripts_dir = Path(repo_path) / "tests" / "test_scripts"
    cmd = [
        "python", "-m", "pytest", str(scripts_dir / test_filename),
        f"--confcutdir={scripts_dir}", "--rootdir", str(scripts_dir),
        "-p", "no:cacheprovider", "--tb=short", "-v",
    ]
    out = ""
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
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






def install_repo_dependencies(repo_path: str) -> dict:
    """Install the target repository's dependencies with pip.

    Tries an editable install (pip install -e .) when a pyproject.toml or setup.py
    exists, otherwise installs from requirements*.txt. This makes the repo's
    package (and its dependencies) importable so generated tests can run.
    """
    repo = Path(repo_path)
    result = {"success": False, "message": "", "output": ""}

    if (repo / "pyproject.toml").exists() or (repo / "setup.py").exists():
        cmd = ["python", "-m", "pip", "install", "-e", ".", "-q"]
    else:
        req = next(
            (repo / name for name in ("requirements.txt", "requirements-dev.txt")
             if (repo / name).exists()),
            None,
        )
        if req is None:
            result["message"] = "No pyproject.toml, setup.py, or requirements.txt found."
            return result
        cmd = ["python", "-m", "pip", "install", "-r", str(req), "-q"]

    try:
        proc = subprocess.run(
            cmd, cwd=str(repo), capture_output=True, text=True, timeout=900
        )
        result["output"] = (proc.stdout or "") + (proc.stderr or "")
        result["success"] = proc.returncode == 0
        result["message"] = (
            "✅ Dependencies installed — the repo package is now importable."
            if result["success"]
            else "⚠️ pip finished with errors (see output). Some imports may still fail."
        )
    except subprocess.TimeoutExpired:
        result["message"] = "Dependency install timed out after 15 minutes."
    except Exception as exc:  # pragma: no cover - defensive
        result["message"] = f"Install failed: {exc}"

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
        st.caption("Prepare a repository-specific testing prompt for GitHub Copilot.")
        
        st.divider()
        st.markdown("### Workflow")
        workflow_steps = (
            ("01", "Enter Repository URL"),
            ("02", "Clone Repository"),
            ("03", "Generate Test Prompt"),
            ("04", "Copy Prompt"),
            ("05", "Paste into Copilot"),
            ("06", "Generate Tests"),
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
    
    # Step 1: Check and install pytest/pytest-cov if needed
    try:
        import pytest
    except ImportError:
        try:
            with st.spinner("Installing pytest and pytest-cov..."):
                subprocess.run(
                    ["pip", "install", "pytest", "pytest-cov", "-q"],
                    check=True,
                    capture_output=True,
                    text=True,
                )
        except subprocess.CalledProcessError as e:
            result["error"] = f"Failed to install pytest: {e.stderr}"
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
        
        # Run pytest command
        pytest_cmd = [
            "python",
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
        
        proc = subprocess.run(
            pytest_cmd,
            capture_output=True,
            text=True,
            timeout=300,  # 5 minute timeout
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

        
        # Write output to coverage_summary.md
        summary_content = f"""# Test Execution Summary

## Command
```bash
{' '.join(pytest_cmd)}
```

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
    - Copies prompt to system clipboard using copilot_bridge
    - Opens Test_Prompt.md in VS Code automatically
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
        "the Copilot Chat workflow, or **Reject** to modify your inputs."
    )
    
    # Display approval buttons side by side
    col1, col2, col3 = st.columns([1, 1, 2])
    
    with col1:
        if st.button("✅ Proceed", type="primary", use_container_width=True):
            # Proceed workflow: copy to clipboard, open file, create folders
            prompt_text = st.session_state["generated_prompt"]
            repo_path = st.session_state["repository_path"]
            prompt_path = st.session_state["prompt_path"]
            
            # Step 1: Copy prompt to clipboard
            clipboard_success = copilot_bridge.copy_to_clipboard(prompt_text)
            if not clipboard_success:
                st.warning(
                    "⚠️ Couldn't copy to clipboard. Please copy manually from the prompt above."
                )
            
            # Step 2: Open Test_Prompt.md in VS Code
            vscode_success = copilot_bridge.open_in_vscode(prompt_path)
            if not vscode_success:
                st.warning(
                    f"⚠️ Couldn't open VS Code automatically. "
                    f"Please open this file manually: {prompt_path}"
                )
            
            # Step 3: Create test folder structure
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
        "After you've completed the Copilot Chat workflow and saved files to the tests/ folder, "
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
                    "ℹ️ No files found yet. Save your Copilot output in the tests/ folder "
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
    else:  # dark (default)
        bg, bg2, card = "#1A0B2E", "#2E0A4F", "#3A1A5C"
        text, text2, border = "#F5F3F7", "#C9BFD6", "#4A2A6C"
    orange, orange_l, purple = "#FF6A13", "#FF8A3D", "#5B2A86"
    return f"""
    <style>
    @import url('https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500;600;700&family=Space+Grotesk:wght@500;600;700&display=swap');
    :root {{
        --orange:{orange}; --orange-l:{orange_l}; --purple:{purple};
        --bg:{bg}; --bg2:{bg2}; --card:{card};
        --text:{text}; --text2:{text2}; --border:{border};
    }}
    .stApp {{ background:{bg}; }}
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
    .av-topbar .brand small {{ opacity:.85; font-weight:500; }}
    /* Cards */
    .av-card {{ background:{card}; border:1px solid {border}; border-radius:14px; padding:1rem 1.25rem; margin:.4rem 0; }}
    .av-card h4 {{ margin:0 0 .3rem; }}
    .av-muted {{ color:{text2}; font-size:.85rem; }}
    /* Mode gate cards */
    .av-gate {{ background:{card}; border:1px solid {border}; border-radius:18px; padding:1.6rem; text-align:center; }}
    .av-gate .ico {{ font-size:2.4rem; }}
    .av-gate h3 {{ margin:.4rem 0 .2rem; }}
    /* Status dot */
    .av-dot {{ height:11px; width:11px; border-radius:50%; display:inline-block; margin-right:7px; vertical-align:middle; }}
    .av-badge {{ display:inline-block; padding:.15rem .6rem; border-radius:999px; font-size:.72rem; font-weight:700; letter-spacing:.04em; }}
    /* Buttons -> Aumovio orange */
    .stButton > button {{ background:{orange}; color:#fff; border:0; border-radius:10px; font-weight:700; }}
    .stButton > button:hover {{ background:{orange_l}; color:#fff; }}
    [data-testid='stSidebar'] {{ background:{bg2} !important; }}
    [data-testid='stSidebar'] * {{ color:{text} !important; }}
    .stMarkdown code {{ background:{bg2}; color:{orange_l}; padding:2px 6px; border-radius:4px; border:1px solid {border}; }}
    </style>
    """


def render_top_bar() -> None:
    """Persistent gradient top bar: brand, mode badge, theme toggle, change mode."""
    mode = st.session_state.get("mode")
    mode_label = "🌐 VIO Agents (Online)" if mode == "vio" else "🖥️ Ollama (Offline)"
    st.markdown(
        f"""
        <div class="av-topbar">
            <div class="brand">AUMOVIO &nbsp;·&nbsp; Enterprise Test Generator
                <small>&nbsp;| {mode_label}</small></div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    c1, c2, _ = st.columns([1, 1, 6])
    with c1:
        if st.button("⟵ Change mode", use_container_width=True):
            st.session_state["mode"] = None
            st.rerun()
    with c2:
        cur = st.session_state.get("theme", "dark")
        label = "☀️ Light" if cur == "dark" else "🌙 Dark"
        if st.button(label, use_container_width=True):
            st.session_state["theme"] = "light" if cur == "dark" else "dark"
            st.rerun()


def render_mode_gate() -> None:
    """Landing gate: choose VIO (online) or Ollama (offline)."""
    st.markdown(
        """
        <div style="text-align:center; margin:2rem 0 1rem;">
            <div style="font-family:'Space Grotesk',sans-serif; font-weight:700;
                        font-size:2rem; background:linear-gradient(90deg,#FF6A13,#5B2A86);
                        -webkit-background-clip:text; -webkit-text-fill-color:transparent;">
                AUMOVIO Enterprise Test Generator
            </div>
            <div class="av-muted">Choose how you want to generate tests</div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    col1, col2 = st.columns(2)
    with col1:
        st.markdown(
            '<div class="av-gate"><div class="ico">🌐</div>'
            '<h3>VIO Agents (Online)</h3>'
            '<div class="av-muted">5 cloud agents · fast (5–15s/test) · needs network</div></div>',
            unsafe_allow_html=True,
        )
        if st.button("Use VIO Agents", key="gate_vio", use_container_width=True):
            st.session_state["mode"] = "vio"
            st.rerun()
    with col2:
        st.markdown(
            '<div class="av-gate"><div class="ico">🖥️</div>'
            '<h3>Ollama (Offline)</h3>'
            '<div class="av-muted">3 local models · private · slower (CPU) · offline backup</div></div>',
            unsafe_allow_html=True,
        )
        if st.button("Use Ollama (Local)", key="gate_ollama", use_container_width=True):
            st.session_state["mode"] = "ollama"
            st.rerun()


STATUS_COLORS = {
    "idle": "#9CA3AF",
    "queued": "#5B2A86",
    "generating": "#FF8A3D",
    "validating": "#8B5CF6",
    "testing": "#FF6A13",
    "fixing": "#14B8A6",
    "done": "#22C55E",
    "error": "#EF4444",
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

    lines = "".join(
        f'<line x1="380" y1="86" x2="{cx}" y2="212" stroke="#FF8A3D" stroke-width="2.5" />'
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


def render_branch_graph() -> None:
    """Static branch view: diagram (from session models) + selection buttons."""
    st.markdown(
        _branch_diagram_html(st.session_state.get("models", {})),
        unsafe_allow_html=True,
    )
    render_branch_controls()


def render_model_detail() -> None:
    """Clean, README-style master-detail panel for the selected agent."""
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
        st.caption("No run data yet. Run a generation to populate this agent.")
        return

    # ── Master (supervisor) view ──
    if is_master:
        st.markdown("##### 🧠 What the master did")
        st.markdown(
            "- **Validated** every worker's generated tests (quality gate: GOLD / SILVER / BRONZE)\n"
            "- **Diagnosed** failures and guided auto-fixes\n"
            "- **Wrote** the consolidated final report"
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
    task = data.get("task_type", "—")
    passed = data.get("passed", 0)
    failed = data.get("failed", 0)
    errors = data.get("errors", 0)

    st.markdown("##### 📋 What we did")
    st.write(
        f"Generated **{task.replace('_',' ')}** for this repository using "
        f"**{AGENT_MODELS.get(agent, agent)}**, then validated and executed them."
    )

    st.markdown("##### ⚙️ How we did it")
    st.markdown(
        "1. Read the **real repository source** (grounding — no guessing)\n"
        "2. Extracted the **actual importable symbols** so imports are valid\n"
        "3. **Generated** pytest tests with this agent\n"
        "4. **Validated** them with `test_generation_master`\n"
        "5. **Executed** with pytest; auto-fixed failures when needed"
    )

    v = data.get("validation", {})
    if v:
        emoji = {"GOLD": "🥇", "SILVER": "🥈", "BRONZE": "🥉"}.get(v.get("level"), "•")
        st.markdown(f"##### {emoji} Quality verdict: **{v.get('level','?')}**")
        if v.get("reasons"):
            st.caption(v["reasons"])

    st.markdown("##### 🎯 Result")
    m1, m2, m3 = st.columns(3)
    m1.metric("Passed", passed)
    m2.metric("Failed", failed)
    m3.metric("Errors", errors)

    results = data.get("results", [])
    if results:
        for t in results:
            icon = {"PASSED": "✅", "FAILED": "❌", "ERROR": "🟠"}.get(t["status"], "•")
            st.markdown(f"{icon} `{t['name']}`")
            if t.get("reason"):
                st.caption(f"↳ {t['reason']}")
    else:
        st.caption("No per-test breakdown available.")

    code = data.get("code", "")
    if code:
        with st.expander("📄 View the generated test code", expanded=False):
            st.code(code, language="python")
            st.download_button(
                "⬇️ Download",
                data=code,
                file_name=f"{task}_test.py",
                mime="text/x-python",
                key=f"dl_{agent}",
            )



def _write_vio_conftest(repo_path: str) -> None:
    """Write a conftest so pytest can import the repo package from source."""
    scripts_dir = Path(repo_path) / "tests" / "test_scripts"
    if not scripts_dir.exists():
        return
    try:
        import_root, package = discover_package_import_root(Path(repo_path))
        (scripts_dir / "conftest.py").write_text(
            "import sys\n"
            f"sys.path.insert(0, r\"{import_root}\")\n"
            f"sys.path.insert(0, r\"{Path(repo_path)}\")\n",
            encoding="utf-8",
        )
    except Exception:
        pass


def _vio_process_task(client, repo_path, source_context, task_type, status_cb=None) -> tuple:
    """Thread-safe worker: generate → validate → pytest → fix for one task.

    Contains NO Streamlit calls so it can run inside a thread pool.
    status_cb(str) is an optional callback to report live step status.
    Returns (agent, result_dict).
    """
    def _report(s):
        if status_cb:
            try:
                status_cb(s)
            except Exception:
                pass

    agent = TYPE_TO_AGENT.get(task_type, AGENTS["unit"])
    task_id = f"{task_type}_0"
    result = {"status": "generating", "task_type": task_type, "tokens": 0, "time_s": 0.0}

    _report("generating")
    prompt = create_task_prompt(task_type, repo_path)
    res = client.generate_test(agent, prompt)
    if not res["ok"]:
        result.update(status="error", validation={"level": "-", "reasons": res["error"]})
        _report("error")
        return agent, result

    code = extract_python_code(res["content"])
    elapsed = res["time_s"]
    save_orchestration_result(repo_path, task_id, res["content"])

    _report("validating")
    verdict = client.validate(code, source_context)

    _report("testing")
    pyres = run_pytest_single(repo_path, f"{task_id}_test.py")

    # One fix round if needed
    if pyres["failed"] + pyres["errors"] > 0:
        _report("fixing")
        fix = client.fix_test(code, pyres["output"], source_context)
        if fix["ok"]:
            fixed = extract_python_code(fix["content"])
            if fixed and "def test" in fixed:
                scripts_dir = Path(repo_path) / "tests" / "test_scripts"
                (scripts_dir / f"{task_id}_test.py").write_text(
                    "# VIO auto-fixed\n" + fixed + "\n", encoding="utf-8")
                code = fixed
                pyres = run_pytest_single(repo_path, f"{task_id}_test.py")
                elapsed += fix["time_s"]

    results = parse_pytest_output(pyres["output"])
    all_pass = pyres["passed"] > 0 and pyres["failed"] + pyres["errors"] == 0
    final_status = "done" if all_pass else ("error" if pyres["passed"] == 0 else "done")
    result.update(
        status=final_status,
        code=code, validation=verdict, results=results,
        tokens=client.token_usage.get(agent, 0), time_s=elapsed,
        passed=pyres["passed"], failed=pyres["failed"], errors=pyres["errors"],
    )
    _report(final_status)
    return agent, result


def _vio_background_run(client, repo_path, source_context, selected_types, parallel) -> None:
    """Run the VIO pipeline in a background thread, updating VIO_LIVE live."""
    def work(tt):
        agent = TYPE_TO_AGENT.get(tt, AGENTS["unit"])
        try:
            a, result = _vio_process_task(
                client, repo_path, source_context, tt,
                status_cb=lambda s: VIO_LIVE["progress"].__setitem__(agent, s),
            )
            VIO_LIVE["results"][a] = result
            VIO_LIVE["progress"][a] = result.get("status", "done")
        except Exception as exc:  # pragma: no cover - defensive
            VIO_LIVE["progress"][agent] = "error"
            VIO_LIVE["results"][agent] = {
                "status": "error", "task_type": tt,
                "validation": {"level": "-", "reasons": str(exc)},
            }

    try:
        if parallel and len(selected_types) > 1:
            with ThreadPoolExecutor(max_workers=min(4, len(selected_types))) as ex:
                list(ex.map(work, selected_types))
        else:
            for tt in selected_types:
                work(tt)
    finally:
        VIO_LIVE["done"] = True



def run_vio_orchestration(selected_types: list, parallel: bool = True, graph_ph=None) -> None:
    """Generate + validate + run + fix tests using the VIO agents.

    Redraws the branch diagram into `graph_ph` (an st.empty) after every status
    change so the graph updates LIVE on the main thread (no background threads).
    Sequential mode shows every step (generate→validate→test→fix); parallel mode
    lights up each agent as it completes.
    """
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
                redraw()
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
                redraw()

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
            client = VIOClient(base_url=endpoint)
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

    # Interactive agent branch graph (master → workers). The diagram lives in a
    # placeholder so it can be redrawn live during a run; buttons stay separate.
    st.markdown("#### Agent Branches")
    st.caption("Click any agent node to see its task, generated tests, and results.")
    graph_ph = st.empty()
    graph_ph.markdown(
        _branch_diagram_html(st.session_state.get("models", {})),
        unsafe_allow_html=True,
    )
    render_branch_controls()

    # Master-detail panel for the selected agent
    render_model_detail()

    # ── Generation controls ──
    st.markdown("---")
    st.markdown("#### Generate Tests")
    repo_url = st.text_input(
        "GitHub Repository URL",
        key="vio_repo_url",
        placeholder="https://github.com/owner/repository",
    )

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
        help="Run all selected test types concurrently (much faster for VIO).",
    )

    run_col, dep_col = st.columns([1, 1])
    with run_col:
        run = st.button("▶️ Generate with VIO Agents", use_container_width=True,
                        disabled=not connected)
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

    if run:
        if not selected_types:
            st.warning("Select at least one test type.")
        elif not repo_url:
            st.warning("Enter a GitHub repository URL.")
        else:
            generate_repository_prompt(repo_url)  # clone + set repository_path
            if st.session_state.get("repository_path"):
                run_vio_orchestration(selected_types, parallel=parallel, graph_ph=graph_ph)
                st.rerun()  # refresh scorecard/detail with final state

    # ── Aggregate scorecard ──
    models = st.session_state.get("models", {})
    ran = [m for m in models.values() if m.get("results") is not None or "passed" in m]
    if ran:
        total_p = sum(m.get("passed", 0) for m in ran)
        total_f = sum(m.get("failed", 0) for m in ran)
        total_e = sum(m.get("errors", 0) for m in ran)
        total_tok = sum(m.get("tokens", 0) for m in ran)
        st.markdown("#### Results")
        s1, s2, s3, s4 = st.columns(4)
        s1.metric("Passed", total_p)
        s2.metric("Failed", total_f)
        s3.metric("Errors", total_e)
        s4.metric("Tokens", total_tok)

        report = st.session_state.get("vio_report")
        if report:
            with st.expander("📝 Final report (by test_generation_master)", expanded=False):
                st.markdown(report)


def main() -> None:
    """
    Main application entry point.
    
    Manages the complete workflow:
    1. Repository input and cloning
    2. Test prompt generation
    3. Approval gate with clipboard/VS Code automation
    4. Results collection and display
    """
    # Configure Streamlit page settings
    st.set_page_config(
        page_title="Enterprise Test Generator",
        page_icon="🧪",
        layout="wide",
        initial_sidebar_state="expanded",
    )
    
    # Initialize session state
    initialize_state()

    # Apply Aumovio theme (orange + deep purple; dark default, light toggle)
    st.markdown(aumovio_css(st.session_state.get("theme", "dark")), unsafe_allow_html=True)

    # Mode gate — pick VIO or Ollama before showing any workspace
    if st.session_state.get("mode") is None:
        render_mode_gate()
        return

    # Persistent top bar (brand, mode badge, theme toggle, change mode)
    render_top_bar()

    # VIO workspace returns early; Ollama continues with the existing flow below
    if st.session_state.get("mode") == "vio":
        render_vio_workspace()
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

    # Workflow section: Show generated prompt and approval gate
    if st.session_state["generated_prompt"]:
        if st.session_state["approval_status"] == "Pending":
            # Show generated prompt for review
            st.markdown("---")
            st.markdown("### 📝 Generated Test_Prompt.md")
            st.caption(
                "Review the generated prompt below. You can download it or proceed with "
                "the Copilot Chat workflow."
            )
            
            with st.expander("📋 View Full Prompt", expanded=True):
                st.markdown(st.session_state["generated_prompt"])
            
            # Download button for prompt
            st.download_button(
                "📥 Download Test_Prompt.md",
                data=st.session_state["generated_prompt"],
                file_name="Test_Prompt.md",
                mime="text/markdown",
                use_container_width=False,
            )
            
            # Display approval gate with Proceed/Reject buttons
            display_approval_gate()
    
    # Workflow section: Show results loading when proceed is clicked
    if st.session_state["proceed_clicked"]:
        # Show success message with next steps
        st.markdown("---")
        st.success(
            "✅ Prompt copied to clipboard!\n\n"
            "**Next steps:**\n"
            "1. Open Copilot Chat in VS Code (Ctrl+Shift+I)\n"
            "2. Paste the prompt (Ctrl+V) and press Enter\n"
            "3. Save the generated files in the tests/ folder\n"
            "4. Return here and click 'Load Results'"
        )
        
        # Display orchestration section (intelligent test generation)
        display_orchestrator_section()
        
        # Display results loading and viewing section
        display_results_section()


if __name__ == "__main__":
    main()