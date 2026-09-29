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
        "-p", "no:cacheprovider", "--tb=short", "-q",
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
    
    # Initialize session state and UI
    initialize_state()
    render_sidebar()

    # Apply custom CSS styling
    st.markdown(
        """
        <style>
        @import url('https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500;600;700&family=Space+Grotesk:wght@500;600;700&display=swap');
        :root { 
            --primary: #087f8c;
            --primary-dark: #056b7a;
            --bg-dark: #0e1117;
            --bg-secondary: #161b22;
            --text-primary: #c9d1d9;
            --text-secondary: #8b949e;
            --border: #30363d;
        }
        h1, h2, h3 { font-family: 'Space Grotesk', sans-serif; color: var(--text-primary); }
        p, label, div { font-family: 'DM Sans', sans-serif; }
        .hero { max-width: 900px; margin: 1.5rem auto 1rem; text-align: center; }
        .eyebrow { color: var(--primary); font-size: .75rem; font-weight: 700; letter-spacing: .12em; text-transform: uppercase; }
        .hero h1 { font-size: clamp(2rem, 5vw, 3.7rem); margin: .35rem 0 .4rem; letter-spacing: 0; }
        .hero p { color: var(--text-secondary); font-size: 1.08rem; margin: 0; }
        .input-shell { max-width: 840px; margin: 2rem auto 0; padding: 1.35rem 1.5rem .8rem; background: var(--bg-secondary); border: 1px solid var(--border); border-radius: 14px; box-shadow: 0 14px 38px rgba(0, 0, 0, .3); }
        .status-card { min-height: 88px; display: flex; align-items: center; gap: .8rem; padding: 1rem; background: var(--bg-secondary); border: 1px solid var(--border); border-radius: 12px; }
        .status-icon { font-size: 1.35rem; }
        .status-label { color: var(--text-secondary); font-size: .76rem; text-transform: uppercase; letter-spacing: .08em; }
        .status-value { color: var(--text-primary); font-size: .96rem; font-weight: 600; margin-top: .25rem; word-break: break-word; }
        .workflow-step { display: flex; align-items: center; gap: .7rem; min-height: 42px; padding: .35rem .15rem; color: var(--text-primary); font-size: .9rem; }
        .workflow-number { display: inline-flex; align-items: center; justify-content: center; width: 28px; height: 28px; flex: 0 0 28px; border: 1px solid var(--primary); border-radius: 50%; color: var(--primary); font-size: .72rem; font-weight: 700; }
        .prompt-header { display: flex; justify-content: space-between; align-items: end; margin: 1.8rem 0 .55rem; }
        .prompt-header h2 { margin: 0; }
        [data-testid='stSidebar'] { background: #0d1b1f !important; }
        [data-testid='stSidebar'] * { color: var(--text-primary) !important; }
        [data-testid='stSidebar'] .stCaption { color: var(--text-secondary) !important; }
        .stButton > button { background: var(--primary); color: white; border: 0; border-radius: 8px; font-weight: 700; }
        .stButton > button:hover { background: var(--primary-dark); color: white; }
        .stMarkdown code { background: var(--bg-secondary); color: var(--text-primary); padding: 2px 6px; border-radius: 4px; border: 1px solid var(--border); }
        </style>
        """,
        unsafe_allow_html=True,
    )

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