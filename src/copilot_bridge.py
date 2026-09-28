"""
Bridge module to automate interaction with GitHub Copilot Chat via VS Code.

This module provides functions to:
- Copy generated test prompts to system clipboard
- Open files in VS Code automatically
- Manage test folder structures
- Collect test results from generated files
- Package test results as ZIP archives

The functions handle error cases gracefully and return success/failure indicators
to allow Streamlit UI to provide appropriate feedback to the user.
"""

import os
import shutil
import subprocess
import zipfile
from pathlib import Path
from typing import Any


def copy_to_clipboard(prompt_text: str) -> bool:
    """
    Copy the generated test prompt to the system clipboard.

    This function uses the pyperclip library to copy text to the clipboard,
    enabling the user to quickly paste the prompt into Copilot Chat.

    Args:
        prompt_text (str): The test prompt text to copy to clipboard

    Returns:
        bool: True if copy was successful, False otherwise

    Raises:
        No exceptions raised - errors are caught and False is returned
    """
    try:
        # Import pyperclip only when needed to handle missing dependency gracefully
        import pyperclip

        # Attempt to copy the text to system clipboard
        pyperclip.copy(prompt_text)
        print(f"✓ Prompt copied to clipboard ({len(prompt_text)} characters)")
        return True

    except ImportError:
        # pyperclip not installed - clipboard access unavailable
        print("✗ pyperclip not installed - clipboard access unavailable")
        return False
    except Exception as e:
        # Generic error (e.g., no clipboard system available)
        print(f"✗ Failed to copy to clipboard: {type(e).__name__}: {e}")
        return False


def open_in_vscode(filepath: str) -> bool:
    """
    Open a file in VS Code using the 'code' command.

    This function launches VS Code with the specified file, allowing the user
    to view the generated Test_Prompt.md in their editor environment.

    Args:
        filepath (str): The absolute or relative path to the file to open

    Returns:
        bool: True if file was opened successfully, False otherwise

    Raises:
        No exceptions raised - errors are caught and False is returned
    """
    try:
        # Convert to absolute path if needed
        abs_path = str(Path(filepath).resolve())

        # Launch VS Code with the file (subprocess detaches and returns immediately)
        subprocess.Popen(["code", abs_path])
        print(f"✓ Opened file in VS Code: {abs_path}")
        return True

    except FileNotFoundError:
        # 'code' command not found - VS Code CLI not available
        print(f"✗ VS Code command not found - is VS Code installed and in PATH?")
        return False
    except Exception as e:
        # Generic error (e.g., permission denied, etc.)
        print(f"✗ Failed to open in VS Code: {type(e).__name__}: {e}")
        return False


def create_test_folders(base_path: str) -> bool:
    """
    Create the standard test folder structure under the given base path.

    Creates the following directory structure if it doesn't exist:
    - tests/
    - tests/specs/
    - tests/test_scripts/
    - tests/coverage/

    This folder structure is required by the test generation prompt and is where
    Copilot Chat will save generated test artifacts.

    Args:
        base_path (str): The base repository path where tests/ folder should be created

    Returns:
        bool: True if folders were created or already exist, False on error

    Raises:
        No exceptions raised - errors are caught and False is returned
    """
    try:
        # Define the required test folder structure
        base_path_obj = Path(base_path)
        test_folders = [
            base_path_obj / "tests",
            base_path_obj / "tests" / "specs",
            base_path_obj / "tests" / "test_scripts",
            base_path_obj / "tests" / "coverage",
        ]

        # Create each folder with exist_ok=True (no error if already exists)
        for folder_path in test_folders:
            folder_path.mkdir(parents=True, exist_ok=True)
            print(f"✓ Ensured folder exists: {folder_path}")

        return True

    except PermissionError as e:
        # No permission to create directories
        print(f"✗ Permission denied creating test folders: {e}")
        return False
    except Exception as e:
        # Generic error (e.g., invalid path, disk full, etc.)
        print(f"✗ Failed to create test folders: {type(e).__name__}: {e}")
        return False


def get_test_results(test_folder_path: str) -> dict[str, list[dict[str, str]]]:
    """
    Scan the test folder structure and collect all generated test files.

    Reads files from the standard test structure (specs/, test_scripts/, coverage/)
    and returns their contents organized by category. Supports .md and .py files.

    Args:
        test_folder_path (str): Path to the tests/ folder containing specs/, test_scripts/, coverage/

    Returns:
        dict: Dictionary with three keys:
            - "specs": List of dicts with "name" and "content" from tests/specs/*.md
            - "test_scripts": List of dicts with "name" and "content" from tests/test_scripts/*
            - "coverage": List of dicts with "name" and "content" from tests/coverage/*.md

        Returns empty lists if folders don't exist or no files found.

    Raises:
        No exceptions raised - errors are caught and empty structure is returned
    """
    # Initialize result structure with empty lists for each category
    results = {
        "specs": [],
        "test_scripts": [],
        "coverage": [],
    }

    try:
        test_path = Path(test_folder_path)

        # Process specs folder (markdown specification files)
        specs_path = test_path / "specs"
        if specs_path.exists():
            # Read all .md files from specs folder, sorted by filename
            for md_file in sorted(specs_path.glob("*.md")):
                try:
                    content = md_file.read_text(encoding="utf-8")
                    results["specs"].append({
                        "name": md_file.name,
                        "content": content,
                    })
                    print(f"✓ Loaded spec: {md_file.name}")
                except Exception as e:
                    print(f"✗ Failed to read spec {md_file.name}: {e}")

        # Process test_scripts folder (python, javascript, etc. test files)
        scripts_path = test_path / "test_scripts"
        if scripts_path.exists():
            # Read all files (.py, .js, .ts, .java, etc.) from test_scripts folder
            for script_file in sorted(scripts_path.glob("*")):
                if script_file.is_file():  # Only files, not directories
                    try:
                        content = script_file.read_text(encoding="utf-8")
                        results["test_scripts"].append({
                            "name": script_file.name,
                            "content": content,
                        })
                        print(f"✓ Loaded test script: {script_file.name}")
                    except Exception as e:
                        print(f"✗ Failed to read script {script_file.name}: {e}")

        # Process coverage folder (coverage analysis markdown files)
        coverage_path = test_path / "coverage"
        if coverage_path.exists():
            # Read all .md files from coverage folder
            for coverage_file in sorted(coverage_path.glob("*.md")):
                try:
                    content = coverage_file.read_text(encoding="utf-8")
                    results["coverage"].append({
                        "name": coverage_file.name,
                        "content": content,
                    })
                    print(f"✓ Loaded coverage report: {coverage_file.name}")
                except Exception as e:
                    print(f"✗ Failed to read coverage {coverage_file.name}: {e}")

        # Print summary
        total_files = (
            len(results["specs"])
            + len(results["test_scripts"])
            + len(results["coverage"])
        )
        print(
            f"✓ Scan complete: {len(results['specs'])} specs, "
            f"{len(results['test_scripts'])} scripts, "
            f"{len(results['coverage'])} coverage files"
        )

        return results

    except Exception as e:
        # Generic error (invalid path, permission denied, etc.)
        print(f"✗ Failed to scan test results: {type(e).__name__}: {e}")
        return results  # Return empty structure


def zip_test_results(test_folder_path: str, output_path: str) -> str | None:
    """
    Create a ZIP archive of the entire tests/ directory.

    Compresses all test artifacts (specs, scripts, coverage) into a single .zip file
    for easy download and sharing. Uses standard ZIP compression.

    Args:
        test_folder_path (str): Path to the tests/ folder to archive
        output_path (str): Path where the output .zip file should be created
                          (e.g., "/path/to/tests.zip")

    Returns:
        str: Path to the created ZIP file if successful, None if failed

    Raises:
        No exceptions raised - errors are caught and None is returned
    """
    try:
        test_path = Path(test_folder_path)
        output_file = Path(output_path)

        # Verify the tests folder exists
        if not test_path.exists():
            print(f"✗ Tests folder does not exist: {test_path}")
            return None

        # Create parent directory for output zip if it doesn't exist
        output_file.parent.mkdir(parents=True, exist_ok=True)

        # Create the zip archive
        with zipfile.ZipFile(output_file, "w", zipfile.ZIP_DEFLATED) as zipf:
            # Walk through all files in the tests directory
            for root, dirs, files in os.walk(test_path):
                for file in files:
                    # Get the full file path
                    file_path = Path(root) / file
                    # Calculate the archive name (relative path from test folder)
                    arcname = file_path.relative_to(test_path.parent)
                    # Add file to archive
                    zipf.write(file_path, arcname=arcname)
                    print(f"✓ Added to archive: {arcname}")

        # Verify the zip file was created
        if output_file.exists():
            size_mb = output_file.stat().st_size / (1024 * 1024)
            print(f"✓ ZIP created successfully: {output_file} ({size_mb:.2f} MB)")
            return str(output_file)
        else:
            print("✗ ZIP file was not created")
            return None

    except PermissionError as e:
        # No permission to write zip file
        print(f"✗ Permission denied creating ZIP: {e}")
        return None
    except zipfile.BadZipFile as e:
        # ZIP creation failed
        print(f"✗ Bad ZIP file error: {e}")
        return None
    except Exception as e:
        # Generic error (disk full, path invalid, etc.)
        print(f"✗ Failed to create ZIP: {type(e).__name__}: {e}")
        return None
