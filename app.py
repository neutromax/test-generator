"""
Streamlit UI for the repository-specific Copilot test prompt generator.

This application provides a web-based interface for:
- Cloning GitHub repositories
- Generating test prompts from repository structure
- Automating clipboard copy and VS Code integration
- Managing test result collection and packaging
- Displaying generated test artifacts (specs, scripts, coverage)
"""

from __future__ import annotations

import io
import os
import re
import subprocess
import time
import zipfile
from pathlib import Path
from urllib.parse import urlparse

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
        "clone_status": "Not started",
        "prompt_status": "Not generated",
        "generated_prompt": "",
        "prompt_path": "",
        "approval_status": "Pending",  # Pending, Approved, Rejected
        "proceed_clicked": False,  # Track if Proceed button was clicked
        "test_results": None,  # Stores collected test results
        "auto_refresh_active": False,  # Track auto-refresh loop status
        "last_results_count": 0,  # Track changes for auto-refresh
    }
    for key, value in defaults.items():
        st.session_state.setdefault(key, value)


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
            "--tb=short",
            "-v",
            "--cov",
            f"--cov-report=html:{coverage_html_dir}",
            "--cov-report=term",
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
        
        # Extract test counts from output
        if "passed" in output:
            import re
            # Look for pattern like "35 passed in 2.34s"
            passed_match = re.search(r"(\d+) passed", output)
            if passed_match:
                result["tests_passed"] = int(passed_match.group(1))
            
            failed_match = re.search(r"(\d+) failed", output)
            if failed_match:
                result["tests_failed"] = int(failed_match.group(1))
            
            result["tests_run"] = result["tests_passed"] + result["tests_failed"]
            result["success"] = True
        
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
        
        # Display results loading and viewing section
        display_results_section()


if __name__ == "__main__":
    main()