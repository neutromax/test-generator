"""
Test Runner - Execute pytest on generated test files and parse results.

Handles:
- Saving test scripts to disk
- Running pytest with coverage
- Parsing pytest output (passed/failed/errors/duration/coverage)
- Structuring results for UI display
"""

from __future__ import annotations

import re
import subprocess
import json
import logging
import ast  # For syntax validation
from pathlib import Path
from typing import Optional, Dict, List, Any

logger = logging.getLogger(__name__)


def run_pytest(test_file: Path, repo_path: Path, timeout: int = 60, venv_python: str = None) -> Dict[str, Any]:
    """
    Run pytest on a single test file and parse results.
    
    Args:
        test_file: Path to the .py test file
        repo_path: Repository root (for conftest discovery)
        timeout: Max seconds to wait for pytest
        venv_python: Path to Python executable in repo's venv (optional). If None, uses "python"
    
    Returns:
        {
            "status": "passed" | "failed",
            "passed": int,
            "failed": int,
            "errors": int,
            "skipped": int,
            "duration": float,
            "coverage": float (0-100, or None),
            "summary": str,
            "failures": [{"test": str, "error": str}, ...],
            "logs": str,
        }
    """
    result = {
        "status": "failed",
        "passed": 0,
        "failed": 0,
        "errors": 0,
        "skipped": 0,
        "duration": 0.0,
        "coverage": None,
        "summary": "",
        "failures": [],
        "logs": "",
    }

    if not test_file.exists():
        result["logs"] = f"Test file not found: {test_file}"
        return result

    # Use the repo's venv Python if provided, otherwise use system Python
    python_exe = venv_python or "python"
    logger.info(f"Using Python executable: {python_exe}")
    logger.info(f"Test file: {test_file}")
    logger.info(f"Repo path: {repo_path}")

    try:
        # Run pytest with verbose output using the appropriate Python executable
        cmd = [
            python_exe, "-m", "pytest",
            str(test_file),
            "-v",
            "--tb=short",
            f"--rootdir={repo_path}",
            f"--confcutdir={repo_path}",
        ]
        
        logger.debug(f"Running command: {' '.join(cmd)}")
        
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=str(repo_path),
        )
        
        logger.info(f"Pytest return code: {proc.returncode}")
        output = proc.stdout + proc.stderr
        result["logs"] = output
        
        # Log output for debugging (log more than 500 chars to see full results)
        if output:
            log_len = min(2000, len(output))  # Show up to 2000 chars
            logger.info(f"Pytest output ({log_len} chars of {len(output)}): {output[:log_len]}")
            if proc.returncode == 0:
                # For successful runs, extract and log the summary
                summary_match = re.search(r"=+\s+(.+)\s+=+$", output, re.MULTILINE)
                if summary_match:
                    logger.info(f"✅ Pytest summary: {summary_match.group(1)}")
        else:
            logger.warning(f"⚠️ No pytest output captured for {test_file}")
        
        # Check for pytest config errors (return code 4, 1, or 5 with error messages)
        if "unknown config option" in output.lower():
            result["logs"] = (
                f"❌ Pytest config error: Repository has a pytest option that requires a missing plugin.\n\n"
                f"Common cause: Project uses pytest plugins (like pytest-timeout, pytest-asyncio) "
                f"that weren't installed.\n\n"
                f"Output:\n{output}"
            )
            result["status"] = "failed"
            logger.error(f"Pytest config error detected in {test_file}")
            return result
        
        # Check for collection errors (syntax, import, etc. in the test file itself)
        if "ERROR collecting" in output or "SyntaxError" in output or "ImportError" in output or "ModuleNotFoundError" in output:
            result["logs"] = (
                f"❌ Error collecting/parsing test file: {test_file}\n\n"
                f"This usually means the generated test code has:\n"
                f"  - Syntax errors (invalid Python)\n"
                f"  - Import errors (missing modules)\n"
                f"  - Invalid decorators or fixtures\n\n"
                f"Output:\n{output}"
            )
            result["status"] = "failed"
            logger.error(f"Collection error in test file {test_file}: {output[:200]}")
            return result
        
        if "no tests ran" in output.lower() and proc.returncode != 0:
            result["logs"] = (
                f"⚠️ No tests ran (pytest exit code {proc.returncode}).\n\n"
                f"Possible causes:\n"
                f"  - No test functions found (must start with 'test_')\n"
                f"  - Pytest collection error (syntax or import issues)\n"
                f"  - Pytest config error\n\n"
                f"Output:\n{output}"
            )
            result["status"] = "failed"
            logger.warning(f"No tests collected from {test_file}")
            return result
        
        # Parse summary line: handle both "2 passed, 1 failed" and "1 failed, 2 passed"
        summary_match = re.search(
            r"(\d+)\s+(?:passed|failed).*?(?:in\s+([\d.]+)s)?",
            output
        )
        
        # Try to extract counts with flexible order
        passed_pattern = re.search(r"(\d+)\s+passed", output)
        failed_pattern = re.search(r"(\d+)\s+failed", output)
        error_pattern = re.search(r"(\d+)\s+error", output)
        skipped_pattern = re.search(r"(\d+)\s+skipped", output)
        duration_pattern = re.search(r"in\s+([\d.]+)s", output)
        
        if passed_pattern or failed_pattern or error_pattern or skipped_pattern:
            result["passed"] = int(passed_pattern.group(1)) if passed_pattern else 0
            result["failed"] = int(failed_pattern.group(1)) if failed_pattern else 0
            result["errors"] = int(error_pattern.group(1)) if error_pattern else 0
            result["skipped"] = int(skipped_pattern.group(1)) if skipped_pattern else 0
            result["duration"] = float(duration_pattern.group(1)) if duration_pattern else 0.0
        else:
            # Check for "no tests collected" message
            if "no tests collected" in output.lower():
                result["logs"] = f"⚠️ No tests found in {test_file}\n\n" + output
                result["status"] = "failed"
                return result
        
        if summary_match:
            result["summary"] = summary_match.group(0)
        
        # Extract failed test details with expected vs actual
        for match in re.finditer(
            r"FAILED\s+(.*?)\s+-\s+(.*?)(?=FAILED|PASSED|=====|$)",
            output,
            re.DOTALL
        ):
            test_name = match.group(1).strip()
            error_text = match.group(2).strip()
            
            # Try to extract function name from test name (test_func_xyz -> func)
            # Pattern: test_<function>_<case>
            func_match = re.match(r".*::test_([a-z_]+?)(?:_[a-z_]*)?(?:\[.+\])?$", test_name)
            function_name = func_match.group(1) if func_match else "unknown"
            
            # Try to extract assertion error details
            # Look for "AssertionError: assert ... == ..." or similar
            assertion_match = re.search(
                r"AssertionError:.*?assert\s+(.+?)\s*==\s*(.+?)(?:\n|$)",
                error_text,
                re.DOTALL
            )
            
            if assertion_match:
                actual_str = assertion_match.group(1).strip()[:50]
                expected_str = assertion_match.group(2).strip()[:50]
                message = f"Expected {expected_str} but got {actual_str}"
            else:
                # Fallback: just use error text
                expected_str = "See error"
                actual_str = "See error"
                message = error_text[:200]
            
            result["failures"].append({
                "test": test_name,
                "function": function_name,
                "input": "See error",  # Hard to extract from pytest
                "expected": expected_str,
                "actual": actual_str,
                "message": message,
            })
        
        # Determine overall status
        if result["failed"] == 0 and result["errors"] == 0 and result["passed"] > 0:
            result["status"] = "passed"
        else:
            result["status"] = "failed"
        
    except subprocess.TimeoutExpired:
        result["logs"] = f"Test execution timed out after {timeout}s"
        logger.warning(f"Pytest timeout for {test_file}")
    except FileNotFoundError as e:
        result["logs"] = f"❌ Python executable not found: {python_exe}\n\nPlease ensure the virtual environment is properly set up.\n\nError: {e}"
        logger.error(f"Python executable not found: {python_exe}")
    except Exception as e:
        result["logs"] = f"Error running pytest: {type(e).__name__}: {e}"
        logger.exception(f"Exception running pytest for {test_file}")
    
    return result


def save_agent_output(
    agent_output: Dict[str, Any],
    repo_path: Path,
    task_type: str,
    task_index: int = 0,
) -> Path:
    """
    Save the full agent output (with test_data and test_display).
    
    Saves to: orchestration_results/{task_type}_{task_index}_output.json
    
    Args:
        agent_output: Full parsed agent response (from parse_worker_output)
        repo_path: Repository root
        task_type: "unit_test", "integration_test", etc.
        task_index: Test index (default 0)
        
    Returns:
        Path to saved output file
    """
    results_dir = Path(repo_path) / "tests" / "orchestration_results"
    results_dir.mkdir(parents=True, exist_ok=True)
    
    output_path = results_dir / f"{task_type}_{task_index}_output.json"
    output_path.write_text(json.dumps(agent_output, indent=2), encoding="utf-8")
    
    return output_path


def save_test_result(
    pytest_result: Dict[str, Any],
    repo_path: Path,
    task_type: str,
    task_index: int = 0,
) -> Path:
    """
    Save the pytest execution result.
    
    Saves to: orchestration_results/{task_type}_{task_index}_result.json
    
    Args:
        pytest_result: Result dict from run_pytest
        repo_path: Repository root
        task_type: "unit_test", "integration_test", etc.
        task_index: Test index (default 0)
        
    Returns:
        Path to saved result file
    """
    results_dir = Path(repo_path) / "tests" / "orchestration_results"
    results_dir.mkdir(parents=True, exist_ok=True)
    
    result_path = results_dir / f"{task_type}_{task_index}_result.json"
    result_path.write_text(json.dumps(pytest_result, indent=2), encoding="utf-8")
    
    return result_path


def save_test_script(
    script_code: str,
    repo_path: Path,
    task_type: str,
) -> Path:
    """
    Save a test script to the standard location.
    
    Saves to: tests/test_scripts/{task_type}_0_test.py
    
    Args:
        script_code: Python test code
        repo_path: Repository root
        task_type: "unit_test", "integration_test", etc.
    
    Returns:
        Path to the saved file
    """
    test_dir = Path(repo_path) / "tests" / "test_scripts"
    test_dir.mkdir(parents=True, exist_ok=True)
    
    file_path = test_dir / f"{task_type}_0_test.py"
    file_path.write_text(script_code, encoding="utf-8")
    
    return file_path


def validate_python_code(code: str) -> tuple[bool, str]:
    """
    Validate Python code for syntax errors.
    
    Args:
        code: Python source code to validate
        
    Returns:
        (is_valid, error_message)
        - is_valid: True if code is syntactically correct
        - error_message: Error details if invalid, empty string if valid
    """
    try:
        ast.parse(code)
        return True, ""
    except SyntaxError as e:
        return False, f"Syntax Error at line {e.lineno}: {e.msg}\n{e.text}"
    except Exception as e:
        return False, f"Parse Error: {type(e).__name__}: {e}"


def parse_worker_output(json_str: str) -> Dict[str, Any]:
    """
    Parse the worker agent's output.
    
    Supports TWO formats:
    
    NEW FORMAT (agents now return):
    {
      "test_data": {"cases": [...], "spec": {...}, "script": "..."},
      "test_display": {"title": "...", "summary": "...", "tests": [...], "notes": [...]}
    }
    
    OLD FORMAT (backward compatibility):
    {"cases": [...], "spec": {...}, "script": "..."}
    
    Also accepts JSON in ```json blocks or Python in ```python blocks.
    
    Returns:
        Normalized dict with keys: test_data, test_display, cases, spec, script
    """
    original_str = json_str.strip()
    logger.debug(f"parse_worker_output: Input length {len(original_str)} chars")
    
    # Try 1: Direct JSON parsing
    try:
        data = json.loads(original_str)
        if isinstance(data, dict):
            logger.debug(f"parse_worker_output: Success via direct JSON parse, keys: {list(data.keys())}")
            return _normalize_agent_output(data)
    except (json.JSONDecodeError, ValueError):
        logger.debug(f"parse_worker_output: Direct JSON parse failed")
        pass
    
    # Try 2: Extract JSON from ```json ... ``` blocks
    try:
        json_match = re.search(r"```json\s*\n?(.*?)\n?```", original_str, re.DOTALL)
        if json_match:
            json_text = json_match.group(1).strip()
            data = json.loads(json_text)
            if isinstance(data, dict):
                logger.debug(f"parse_worker_output: Success via ```json block, keys: {list(data.keys())}")
                return _normalize_agent_output(data)
        else:
            logger.debug(f"parse_worker_output: No ```json block found")
    except (json.JSONDecodeError, ValueError, AttributeError) as e:
        logger.debug(f"parse_worker_output: ```json block parse failed: {e}")
        pass
    
    # Try 3: Extract Python code from ```python ... ``` or ``` ... ``` blocks (fallback)
    python_patterns = [
        (r"```python\s*\n?(.*?)\n?```", "```python"),
        (r"```py\s*\n?(.*?)\n?```", "```py"),
        (r"```\s*\n?(.*?)\n?```", "```"),
    ]
    
    for pattern, name in python_patterns:
        try:
            match = re.search(pattern, original_str, re.DOTALL)
            if match:
                script = match.group(1).strip()
                if script and len(script) > 10:
                    logger.debug(f"parse_worker_output: Success via {name} block, {len(script)} chars")
                    return _normalize_agent_output({"script": script})
                else:
                    logger.debug(f"parse_worker_output: {name} block found but too short ({len(script)} chars)")
            else:
                logger.debug(f"parse_worker_output: No {name} block found")
        except (AttributeError, IndexError) as e:
            logger.debug(f"parse_worker_output: {name} pattern error: {e}")
            continue
    
    # Fallback: return empty response
    logger.warning(f"parse_worker_output: Failed to parse any format from {len(original_str)} char response")
    return _normalize_agent_output({})


def _normalize_agent_output(data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Normalize agent output to standard format.
    
    Handles both NEW format (test_data + test_display) and OLD format (flat keys).
    
    Returns dict with:
    - test_data: {cases, spec, script}
    - test_display: {title, summary, tests, notes}
    - cases, spec, script: For backward compatibility
    """
    # NEW FORMAT: has test_data and/or test_display
    if "test_data" in data or "test_display" in data:
        test_data = data.get("test_data", {})
        test_display = data.get("test_display", {})
        
        logger.debug(f"_normalize_agent_output: NEW format detected")
        return {
            "test_data": {
                "cases": test_data.get("cases", []),
                "spec": test_data.get("spec", {}),
                "script": test_data.get("script", ""),
            },
            "test_display": {
                "title": test_display.get("title", ""),
                "summary": test_display.get("summary", ""),
                "tests": test_display.get("tests", []),
                "notes": test_display.get("notes", []),
            },
            # Backward compatibility: also expose at top level
            "cases": test_data.get("cases", []),
            "spec": test_data.get("spec", {}),
            "script": test_data.get("script", ""),
        }
    
    # OLD FORMAT: flat structure with cases/spec/script
    logger.debug(f"_normalize_agent_output: OLD format (backward compatibility)")
    return {
        "test_data": {
            "cases": data.get("cases", []),
            "spec": data.get("spec", {}),
            "script": data.get("script", ""),
        },
        "test_display": {
            "title": data.get("title", ""),
            "summary": data.get("summary", ""),
            "tests": data.get("tests", []),
            "notes": data.get("notes", []),
        },
        # Backward compatibility
        "cases": data.get("cases", []),
        "spec": data.get("spec", {}),
        "script": data.get("script", ""),
    }


def build_pytest_markdown(
    agent_name: str,
    model_name: str,
    test_type: str,
    pytest_result: Dict[str, Any],
    script_code: str = "",
    spec: Dict[str, Any] = None,
    cases: List[Dict[str, Any]] = None,
    validation_level: str = "GOLD",
) -> str:
    """
    Build a README-style markdown report for a single test.
    
    Returns markdown string for display + download.
    """
    spec = spec or {}
    cases = cases or []
    
    md = f"""# 🧪 Test Result: {test_type}

## 📋 Summary
| Metric | Value |
|--------|-------|
| Status | {'✅ Passed' if pytest_result['status'] == 'passed' else '❌ Failed'} |
| Passed | {pytest_result['passed']} |
| Failed | {pytest_result['failed']} |
| Errors | {pytest_result['errors']} |
| Duration | {pytest_result['duration']:.2f}s |
| Agent | {agent_name} |
| Model | {model_name} |
| Quality | {validation_level} |

## 📝 Test Cases
| # | Name | Input | Expected | Category | Status |
|---|------|-------|----------|----------|--------|
"""
    
    for i, case in enumerate(cases, 1):
        status_emoji = "✅" if pytest_result["status"] == "passed" else "❌"
        md += (
            f"| {i} | `{case.get('name', '?')}` | "
            f"`{case.get('input', '?')}` | "
            f"`{case.get('expected', '?')}` | "
            f"{case.get('category', '?')} | {status_emoji} |\n"
        )
    
    md += "\n## 📄 Test Spec\n"
    md += "| Field | Value |\n|-------|-------|\n"
    
    spec_fields = {
        "type": "Type",
        "file": "Target File",
        "func": "Target Function",
        "imports_used": "Imports",
        "assumptions": "Assumptions",
        "fixtures": "Fixtures",
        "edge_cases_covered": "Edge Cases",
    }
    
    for key, label in spec_fields.items():
        value = spec.get(key, "—")
        if isinstance(value, list):
            value = ", ".join(value)
        md += f"| {label} | {value} |\n"
    
    md += f"\n## 💻 Test Script\n```python\n{script_code}\n```\n"
    
    md += f"\n## 📊 Pytest Output\n```\n{pytest_result['logs']}\n```\n"
    
    if pytest_result["failures"]:
        md += "\n## ❌ Failures\n"
        for failure in pytest_result["failures"]:
            md += f"- **{failure['test']}**: {failure['error']}\n"
    
    return md


def extract_test_cases_from_code(python_code: str) -> List[Dict[str, str]]:
    """
    Extract test case information from generated Python test code.
    
    Parses test functions to create structured test case data.
    Each test function becomes a test case with name, input/expected info extracted from docstring.
    
    Args:
        python_code: The generated Python test script
    
    Returns:
        List of test case dicts with keys: name, input, expected, category
    """
    cases = []
    
    try:
        # Parse the Python code into an AST
        tree = ast.parse(python_code)
    except SyntaxError as e:
        logger.warning(f"Could not parse Python code to extract test cases: {e}")
        return []
    
    # Extract all test functions
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name.startswith("test_"):
            # Extract docstring if present
            docstring = ast.get_docstring(node) or ""
            
            # Try to parse test parameters from docstring or function name
            # Format: "test_<function>_<scenario>"
            parts = node.name[5:].split("_")  # Remove "test_" prefix
            func_name = parts[0] if parts else "unknown"
            scenario = "_".join(parts[1:]) if len(parts) > 1 else ""
            
            case = {
                "name": node.name,
                "input": f"Scenario: {scenario}" if scenario else "Standard case",
                "expected": docstring.split("\n")[0] if docstring else "Test passes",
                "category": "unit",  # Default category
            }
            cases.append(case)
    
    return cases


def extract_test_spec_from_code(python_code: str, task_type: str = "unit_test") -> Dict[str, Any]:
    """
    Extract test specification metadata from generated Python code.
    
    Analyzes imports, functions, and structure to build a test spec.
    
    Args:
        python_code: The generated Python test script
        task_type: Type of tests (unit_test, integration_test, e2e_test, security_test)
    
    Returns:
        Dict with spec fields: type, imports_used, functions_tested, assumptions, fixtures, edge_cases_covered
    """
    spec = {
        "type": task_type.replace("_test", "").title(),
        "imports_used": [],
        "functions_tested": [],
        "assumptions": [],
        "fixtures": [],
        "edge_cases_covered": [],
    }
    
    try:
        tree = ast.parse(python_code)
    except SyntaxError as e:
        logger.warning(f"Could not parse Python code to extract spec: {e}")
        return spec
    
    # Extract imports
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                spec["imports_used"].append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                spec["imports_used"].append(f"from {node.module} import ...")
    
    # Extract test functions (these are the functions being tested indirectly)
    test_functions = []
    fixtures_found = set()
    
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef):
            if node.name.startswith("test_"):
                test_functions.append(node.name)
                
                # Extract fixture dependencies from function parameters
                for arg in node.args.args:
                    if arg.arg not in ("self", "cls"):
                        fixtures_found.add(arg.arg)
    
    spec["functions_tested"] = test_functions
    spec["fixtures"] = list(fixtures_found) if fixtures_found else ["default"]
    
    # Infer assumptions and edge cases from docstrings and structure
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name.startswith("test_"):
            docstring = ast.get_docstring(node) or ""
            if docstring:
                # Look for edge case keywords in docstring
                if any(kw in docstring.lower() for kw in ["edge", "boundary", "corner", "none", "empty", "invalid"]):
                    spec["edge_cases_covered"].append(node.name)
    
    # Add default assumption
    if not spec["assumptions"]:
        spec["assumptions"] = ["Target code is importable", "Dependencies are installed"]
    
    # Deduplicate
    spec["edge_cases_covered"] = list(set(spec["edge_cases_covered"]))
    
    return spec
