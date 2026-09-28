# Test Generator - Complete File & Function Reference

**Generated:** 2026-09-23  
**Purpose:** Detailed breakdown of every file and function in the project

---

## 📁 File Overview

The project has **2 main Python files** that work together:

```
test generator/
├── generate_test_prompt.py    ◄─── BACKEND ENGINE (Library)
├── app.py                     ◄─── FRONTEND (Streamlit UI)
└── requirements.txt           ◄─── Dependencies
```

| File | Type | Purpose | Lines | Functions |
|------|------|---------|-------|-----------|
| `generate_test_prompt.py` | Backend Library | Repository analysis & prompt generation | ~300 | 6 functions |
| `app.py` | Frontend UI | Web interface for users | ~250 | 6 functions |
| `requirements.txt` | Config | Python dependencies | 1 | N/A |

---

## 🔧 FILE 1: `generate_test_prompt.py`

**Purpose:** Backend engine that handles Git operations, repository analysis, and prompt generation  
**Type:** Library/CLI module  
**Language:** Python 3.9+  
**Key Responsibility:** Clone repositories, validate URLs, build comprehensive test prompts

### Module-Level Constants

```python
TOOL_DIR = Path(__file__).resolve().parent
```
- **What it is:** Global constant that points to the directory containing this file
- **Value:** `d:\test generator\`
- **Used by:** All functions that need relative paths
- **Example:** `TOOL_DIR / "repositories"` → `d:\test generator\repositories`

```python
PROMPT_FILE = TOOL_DIR / "Test_Prompt.md"
```
- **What it is:** Default output path for generated prompts
- **Value:** `d:\test generator\Test_Prompt.md`
- **Used by:** `parse_args()` as default, rarely used in current flow
- **Note:** Prompt is usually saved inside the repository folder, not here

---

### Function 1: `repository_name()`

**Signature:**
```python
def repository_name(repository_url: str) -> str:
```

**Purpose:** Extract a safe directory name from a GitHub URL

**Parameters:**
| Parameter | Type | Example | Description |
|-----------|------|---------|-------------|
| `repository_url` | str | `https://github.com/owner/tiny-music-player` | Full GitHub repository URL |

**Returns:**
| Type | Example | Description |
|------|---------|-------------|
| str | `tiny-music-player` | Clean repository name, safe for filesystem |

**What it does:**
1. Parses the URL using `urlparse()` from urllib.parse
2. Extracts the last part of the URL path (e.g., `/owner/tiny-music-player` → `tiny-music-player`)
3. Removes `.git` suffix if present (e.g., `tiny-music-player.git` → `tiny-music-player`)
4. Validates the result (must not be empty or `.` or `..`)
5. Returns the clean name

**Code:**
```python
def repository_name(repository_url: str) -> str:
    """Return a safe local directory name from a GitHub-style URL."""
    parsed = urlparse(repository_url)  # Parse URL
    name = Path(parsed.path.rstrip("/")).name  # Get last path component
    if name.endswith(".git"):  # Remove .git suffix
        name = name[:-4]
    if not name or name in {".", ".."}:  # Validate
        raise ValueError("The repository URL does not contain a valid repository name.")
    return name
```

**Examples:**
```python
repository_name("https://github.com/martinmimigames/tiny-music-player")
# Returns: "tiny-music-player"

repository_name("https://github.com/owner/my-repo.git")
# Returns: "my-repo"

repository_name("https://github.com/owner/invalid")
# Returns: "invalid"

repository_name("https://github.com/owner/.")
# Raises ValueError: "The repository URL does not contain a valid repository name."
```

**Used by:**
- `ensure_repository()` - to calculate target directory
- `app.py` - imported and used in validation

**Error Handling:**
- Raises `ValueError` if name is empty or invalid

---

### Function 2: `is_git_repository()`

**Signature:**
```python
def is_git_repository(path: Path) -> bool:
```

**Purpose:** Check if a given directory is a valid Git repository

**Parameters:**
| Parameter | Type | Example | Description |
|-----------|------|---------|-------------|
| `path` | Path | `Path("d:\\repos\\my-project")` | Absolute path to check |

**Returns:**
| Type | Example | Description |
|------|---------|-------------|
| bool | `True` or `False` | True if valid Git repo, False otherwise |

**What it does:**
1. Runs Git command: `git -C {path} rev-parse --is-inside-work-tree`
2. Captures the output (doesn't display it)
3. Checks if command succeeded (return code = 0) AND output is "true"
4. Returns the result

**Code:**
```python
def is_git_repository(path: Path) -> bool:
    result = subprocess.run(
        ["git", "-C", str(path), "rev-parse", "--is-inside-work-tree"],
        capture_output=True,
        text=True,
        check=False,
    )
    return result.returncode == 0 and result.stdout.strip() == "true"
```

**How it works:**
- `-C {path}` tells Git to operate in that directory without changing it
- `rev-parse --is-inside-work-tree` asks "Is this inside a Git repository?"
- Git returns "true" or error code if not in a Git repo
- We check both: success (returncode == 0) AND "true" output

**Examples:**
```python
# If d:\repos\valid-repo is a Git repository:
is_git_repository(Path("d:\\repos\\valid-repo"))
# Returns: True

# If d:\repos\random-folder is NOT a Git repository:
is_git_repository(Path("d:\\repos\\random-folder"))
# Returns: False

# If path doesn't exist:
is_git_repository(Path("d:\\repos\\nonexistent"))
# Returns: False (Git command fails)
```

**Used by:**
- `ensure_repository()` - to check if cached repo is valid
- `app.py` - imported and used for validation

**Error Handling:**
- No exceptions raised, returns False on any error

---

### Function 3: `ensure_repository()`

**Signature:**
```python
def ensure_repository(repository_url: str, clone_root: Path) -> Path:
```

**Purpose:** Ensure a repository exists locally - clone it if needed, or use cached version

**Parameters:**
| Parameter | Type | Example | Description |
|-----------|------|---------|-------------|
| `repository_url` | str | `https://github.com/owner/repo` | Full GitHub URL to clone |
| `clone_root` | Path | `Path("d:\\repos")` | Parent directory for cloning |

**Returns:**
| Type | Example | Description |
|------|---------|-------------|
| Path | `Path("d:\\repos\\repo-name")` | Full path to repository |

**What it does:**
1. Creates the `clone_root` directory if it doesn't exist
2. Calculates target path: `clone_root / repository_name(url)`
3. Checks if target exists:
   - **If exists:** Verifies it's a valid Git repo
     - If valid: Return it (use cached)
     - If not valid: Raise error
   - **If doesn't exist:** Clone the repository
4. Returns the path to the repository

**Code:**
```python
def ensure_repository(repository_url: str, clone_root: Path) -> Path:
    clone_root.mkdir(parents=True, exist_ok=True)  # Create parent dir
    target = clone_root / repository_name(repository_url)  # Calculate path

    if target.exists():  # If target already exists
        if is_git_repository(target):  # Is it a valid Git repo?
            print(f"Using existing repository: {target}")
            return target  # Yes, use cached
        raise RuntimeError(  # No, error
            f"The target folder already exists but is not a Git repository: {target}"
        )

    # Target doesn't exist, clone it
    print(f"Cloning repository into: {target}")
    try:
        subprocess.run(
            ["git", "clone", repository_url, str(target)],
            check=True,  # Raise error if git fails
        )
    except FileNotFoundError as error:  # Git not installed
        raise RuntimeError("Git is not installed or is not available on PATH.") from error
    except subprocess.CalledProcessError as error:  # Git clone failed
        raise RuntimeError(f"Git clone failed with exit code {error.returncode}.") from error
    return target  # Return cloned repo path
```

**Process Flow:**
```
ensure_repository(url, root)
    ↓
Create directories if needed
    ↓
Calculate target = root / repo_name
    ↓
Does target exist?
    ├─ YES → Is it a Git repo?
    │   ├─ YES → print "Using existing..." and return target
    │   └─ NO → raise RuntimeError
    │
    └─ NO → Clone repo
        ├─ git clone successful?
        │   ├─ YES → return target
        │   └─ NO → raise RuntimeError
        └─ Git not installed?
            └─ YES → raise RuntimeError
```

**Examples:**
```python
# First run (no cache):
path = ensure_repository(
    "https://github.com/martinmimigames/tiny-music-player",
    Path("d:\\repos")
)
# Output: "Cloning repository into: d:\repos\tiny-music-player"
# Git runs: git clone https://github.com/martinmimigames/tiny-music-player d:\repos\tiny-music-player
# Returns: Path("d:\\repos\\tiny-music-player")

# Second run (uses cache):
path = ensure_repository(
    "https://github.com/martinmimigames/tiny-music-player",
    Path("d:\\repos")
)
# Output: "Using existing repository: d:\repos\tiny-music-player"
# Returns: Path("d:\\repos\\tiny-music-player") (no clone)

# Error case (folder exists but not Git repo):
ensure_repository(url, root)  # If d:\repos\tiny-music-player exists but isn't Git
# Raises: RuntimeError("The target folder already exists but is not a Git repository...")
```

**Used by:**
- `main()` - called after URL validation
- `app.py` - imported in `generate_repository_prompt()`

**Error Handling:**
- `FileNotFoundError` → Converts to RuntimeError about Git not installed
- `CalledProcessError` → Converts to RuntimeError about clone failure
- Custom `RuntimeError` → Non-Git folder already exists

---

### Function 4: `build_prompt()`

**Signature:**
```python
def build_prompt(repository_url: str, repository_path: Path) -> str:
```

**Purpose:** Generate the comprehensive test prompt for Claude

**Parameters:**
| Parameter | Type | Example | Description |
|-----------|------|---------|-------------|
| `repository_url` | str | `https://github.com/owner/repo` | Original GitHub URL |
| `repository_path` | Path | `Path("d:\\repos\\repo")` | Local path to cloned repo |

**Returns:**
| Type | Example | Description |
|------|---------|-------------|
| str | `# Enterprise Test Generation...\n...` | Full prompt as multi-line string (~5000 chars) |

**What it does:**
1. Takes both the URL and local path
2. Creates a hardcoded multi-line prompt string using f-string
3. Injects the URL and path into the prompt
4. Returns the complete prompt

**Code:**
```python
def build_prompt(repository_url: str, repository_path: Path) -> str:
    return f"""# Enterprise Test Generation Prompt

Analyze and test the repository below.

- Repository URL: `{repository_url}`
- Local repository path: `{repository_path}`

You are an Enterprise Test Generation Agent acting as a software architect, QA architect,
test automation engineer, business analyst, and repository analysis agent. Work from the
actual repository code; never generate tests blindly.

## Required workflow

Execute these phases strictly in order:

1. **Repository Discovery**: inspect README files, design and ADR documents, package and
   build configuration, CI/CD configuration, environment configuration, source code,
   APIs, controllers, services, models, utilities, UI components, and data-access layers.
2. **Code Understanding**: document the application purpose, architecture, business rules,
   ...
[CONTINUES WITH 9 PHASES AND DETAILED INSTRUCTIONS]
"""
```

**What the prompt contains:**
1. Repository metadata (URL + local path)
2. Claude's role definition (architect, QA engineer, etc.)
3. 9-phase workflow:
   - Repository Discovery
   - Code Understanding
   - Feature Identification
   - Specification Generation
   - **Human Approval Gate** ⚠️ (stops here)
   - Test Script Generation
   - Coverage Analysis
   - Test Execution
   - Results Reporting
4. Required output structure (tests/ directory layout)
5. Specification format requirements
6. Test script requirements
7. Coverage and results requirements
8. Approval review summary

**Example:**
```python
prompt = build_prompt(
    "https://github.com/martinmimigames/tiny-music-player",
    Path("d:\\test generator\\repositories\\tiny-music-player")
)
print(prompt[:200])  # First 200 characters
# Output: # Enterprise Test Generation Prompt
#
# Analyze and test the repository below.
#
# - Repository URL: `https://github.com/martinmimigames/tiny-music-player`
# - Local repository path: `d:\test generator\repositories\tiny-music-player`
```

**Used by:**
- `main()` - to save prompt to file
- `app.py` - imported in `generate_repository_prompt()`

**Error Handling:**
- None (always succeeds)

---

### Function 5: `parse_args()`

**Signature:**
```python
def parse_args() -> argparse.Namespace:
```

**Purpose:** Parse command-line arguments for CLI mode

**Parameters:** None

**Returns:**
| Type | Example | Description |
|------|---------|-------------|
| argparse.Namespace | `Namespace(repository_url='https://...', clone_root=Path(...), output=Path(...))` | Object with attributes |

**What it does:**
1. Creates an argument parser
2. Defines 3 arguments:
   - `repository_url` (optional positional)
   - `--clone-root` (optional flag)
   - `--output` (optional flag)
3. Parses command line arguments
4. Returns Namespace object with all values

**Code:**
```python
def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Clone a repository if needed and generate Test_Prompt.md for Copilot."
    )
    parser.add_argument(
        "repository_url",
        nargs="?",  # Optional (0 or 1 argument)
        help="Git repository URL"
    )
    parser.add_argument(
        "--clone-root",
        type=Path,
        default=TOOL_DIR / "repositories",  # Default: d:\test generator\repositories
        help="Folder containing cloned repositories (default: ./repositories)"
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=PROMPT_FILE,  # Default: d:\test generator\Test_Prompt.md
        help="Prompt output path (default: ./Test_Prompt.md)"
    )
    return parser.parse_args()
```

**Arguments:**

| Argument | Type | Default | Example | Description |
|----------|------|---------|---------|-------------|
| `repository_url` | str | None | `https://github.com/owner/repo` | URL to clone (optional - prompts if missing) |
| `--clone-root` | Path | `./repositories` | `--clone-root d:\my-repos` | Where to store cloned repos |
| `--output` | Path | `./Test_Prompt.md` | `--output ~/prompt.md` | Where to save generated prompt |

**CLI Usage Examples:**
```bash
# Interactive (asks for URL):
python generate_test_prompt.py

# Direct with URL:
python generate_test_prompt.py https://github.com/owner/repo

# Custom clone location:
python generate_test_prompt.py https://github.com/owner/repo --clone-root d:\my-repos

# Custom output location:
python generate_test_prompt.py https://github.com/owner/repo --output C:\prompts\my-prompt.md

# Both custom:
python generate_test_prompt.py https://github.com/owner/repo --clone-root d:\repos --output d:\output\prompt.md
```

**Used by:**
- `main()` - called at start to get command-line args

**Error Handling:**
- None (argparse handles invalid args automatically)

---

### Function 6: `main()`

**Signature:**
```python
def main() -> int:
```

**Purpose:** Main entry point for CLI mode (when run as a script)

**Parameters:** None

**Returns:**
| Type | Example | Description |
|------|---------|-------------|
| int | `0` (success) or `1` (error) or `2` (missing args) | Exit code for the process |

**What it does:**
1. Parses command-line arguments
2. Gets repository URL (from args or prompts user)
3. Validates URL is not empty
4. Calls `ensure_repository()` to clone/cache
5. Calls `build_prompt()` to generate prompt
6. Saves prompt to file
7. Prints success message
8. Returns exit code

**Code:**
```python
def main() -> int:
    args = parse_args()  # Get command-line args
    
    # Get repository URL
    repository_url = (
        input("Repository URL: ").strip()  # Prompt user if not provided
        if args.repository_url is None
        else args.repository_url.strip()
    )
    
    # Validate URL is not empty
    if not repository_url:
        print("A repository URL is required.", file=sys.stderr)
        return 2  # Exit code 2 = invalid arguments

    try:
        # Clone/cache repository
        repository_path = ensure_repository(repository_url, args.clone_root.resolve())
        
        # Prepare output directory
        output_path = args.output.resolve()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        
        # Generate and save prompt
        output_path.write_text(
            build_prompt(repository_url, repository_path),
            encoding="utf-8",
        )
    except (OSError, RuntimeError, ValueError) as error:
        # Handle errors
        print(f"Error: {error}", file=sys.stderr)
        return 1  # Exit code 1 = error

    # Success
    print(f"Generated Copilot prompt: {output_path}")
    print("Paste the contents of Test_Prompt.md into Copilot to begin repository analysis.")
    return 0  # Exit code 0 = success
```

**Exit Codes:**
| Code | Meaning | Example |
|------|---------|---------|
| 0 | Success | Prompt generated successfully |
| 1 | Error | Git clone failed, permission denied, etc. |
| 2 | Missing arguments | User didn't provide URL |

**Flow:**
```
main()
    ↓
parse_args()
    ↓
Get repository_url (from args or input())
    ↓
Is repository_url empty?
    ├─ YES → print error, return 2
    └─ NO → Continue
    ↓
Try:
    ├─ ensure_repository() [may clone]
    ├─ build_prompt()
    ├─ write to file
    └─ print success message
    ↓
Catch (OSError, RuntimeError, ValueError):
    ├─ print error
    └─ return 1
    ↓
return 0
```

**Example Usage:**
```
$ python generate_test_prompt.py https://github.com/martinmimigames/tiny-music-player
Cloning repository into: d:\test generator\repositories\tiny-music-player
Generated Copilot prompt: d:\test generator\Test_Prompt.md
Paste the contents of Test_Prompt.md into Copilot to begin repository analysis.
Exit code: 0
```

**Used by:**
- Module entry point: `if __name__ == "__main__": raise SystemExit(main())`

**Error Handling:**
- Catches and reports `OSError`, `RuntimeError`, `ValueError`
- Returns appropriate exit code
- Prints to stderr for errors

---

## 🎨 FILE 2: `app.py`

**Purpose:** Streamlit web UI for the Test Generator  
**Type:** Web Application  
**Language:** Python 3.9+  
**Key Responsibility:** Provide user-friendly web interface

### Module-Level Constants

```python
REPOSITORIES_DIR = TOOL_DIR / "repositories"
```
- **What it is:** Directory where cloned repositories are stored
- **Value:** `d:\test generator\repositories`
- **Used by:** `validate_repository_url()`, sidebar display

### Function 1: `validate_repository_url()`

**Signature:**
```python
def validate_repository_url(repository_url: str) -> tuple[bool, str]:
```

**Purpose:** Validate GitHub URL format (different from backend, this is UI-specific)

**Parameters:**
| Parameter | Type | Example | Description |
|-----------|------|---------|-------------|
| `repository_url` | str | `https://github.com/owner/repo` | URL to validate |

**Returns:**
| Type | Example | Description |
|------|---------|-------------|
| tuple[bool, str] | `(True, "")` or `(False, "error message")` | Validity and error message |

**What it does:**
1. Parses URL using `urlparse()`
2. Validates multiple conditions:
   - Scheme must be http or https
   - Hostname must exist
   - Hostname must be github.com or contain "github"
   - Path must have exactly 2 parts (owner/repo)
   - Each part must match pattern [A-Za-z0-9_.-]+
3. Returns (True, "") if valid
4. Returns (False, error_message) if invalid

**Code:**
```python
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
```

**Validation Rules:**
```
Check 1: Scheme
├─ Must be: http or https
└─ Error: "Enter a complete GitHub URL beginning with https://."

Check 2: Hostname
├─ Must be: github.com or contain "github" (for enterprise)
└─ Error: "The URL must point to GitHub or a GitHub Enterprise server."

Check 3: Path parts
├─ Must have: exactly 2 parts (owner, repo)
├─ Each part must match: [A-Za-z0-9_.-]+
└─ Error: "Use the format https://github.com/owner/repository."
```

**Examples:**
```python
# Valid
validate_repository_url("https://github.com/owner/repo")
# Returns: (True, "")

# Valid (enterprise)
validate_repository_url("https://github.company.com/owner/repo")
# Returns: (True, "")

# Invalid (http instead of https)
validate_repository_url("http://github.com/owner/repo")
# Returns: (True, "")  # Actually accepts http too!

# Invalid (not github)
validate_repository_url("https://gitlab.com/owner/repo")
# Returns: (False, "The URL must point to GitHub...")

# Invalid (wrong path format)
validate_repository_url("https://github.com/only-one-part")
# Returns: (False, "Use the format...")

# Invalid (special characters in name)
validate_repository_url("https://github.com/owner/repo@name")
# Returns: (False, "Use the format...")
```

**Used by:**
- `generate_repository_prompt()` - first thing called

**Error Handling:**
- Returns tuple (False, message) instead of raising exception

---

### Function 2: `initialize_state()`

**Signature:**
```python
def initialize_state() -> None:
```

**Purpose:** Initialize Streamlit session state with default values

**Parameters:** None

**Returns:** None

**What it does:**
1. Creates a dictionary of default values
2. Sets each key in `st.session_state` if not already set
3. Prevents overwriting existing session data on page reloads

**Code:**
```python
def initialize_state() -> None:
    defaults = {
        "repository_name": "",
        "repository_path": "",
        "clone_status": "Not started",
        "prompt_status": "Not generated",
        "generated_prompt": "",
        "prompt_path": "",
    }
    for key, value in defaults.items():
        st.session_state.setdefault(key, value)
```

**Session State Keys:**
| Key | Type | Initial Value | Purpose |
|-----|------|---------------|---------|
| `repository_name` | str | `""` | Stores repo name (e.g., "tiny-music-player") |
| `repository_path` | str | `""` | Stores repo path (e.g., "d:\repos\...") |
| `clone_status` | str | `"Not started"` | Stores clone result status |
| `prompt_status` | str | `"Not generated"` | Stores prompt generation status |
| `generated_prompt` | str | `""` | Stores full prompt text |
| `prompt_path` | str | `""` | Stores path to saved prompt file |

**Why this matters:**
- `st.session_state` persists during a browser session
- Without `setdefault()`, reloads would reset everything
- With `setdefault()`, values survive page reloads

**Used by:**
- `main()` - called at the very start

---

### Function 3: `status_badge()`

**Signature:**
```python
def status_badge(label: str, value: str, icon: str) -> None:
```

**Purpose:** Display a styled status card in the UI

**Parameters:**
| Parameter | Type | Example | Description |
|-----------|------|---------|-------------|
| `label` | str | `"Clone Status"` | Label text (uppercase) |
| `value` | str | `"Cloned successfully"` | Value to display |
| `icon` | str | `"↻"` | Unicode icon |

**Returns:** None (displays HTML directly)

**What it does:**
1. Takes label, value, and icon
2. Builds custom HTML with inline CSS styling
3. Displays it using `st.markdown(unsafe_allow_html=True)`

**Code:**
```python
def status_badge(label: str, value: str, icon: str) -> None:
    st.markdown(
        f'<div class="status-card"><span class="status-icon">{icon}</span>'
        f'<div><div class="status-label">{label}</div>'
        f'<div class="status-value">{value}</div></div></div>',
        unsafe_allow_html=True,
    )
```

**Example Usage:**
```python
status_badge("Clone Status", "Cloned successfully", "↻")
# Displays a styled card with icon ↻, label "CLONE STATUS", value "Cloned successfully"
```

**Used by:**
- `main()` - displays 4 status badges

**Output:**
```
┌─────────────────────────────┐
│ ↻  CLONE STATUS             │
│    Cloned successfully      │
└─────────────────────────────┘
```

---

### Function 4: `render_sidebar()`

**Signature:**
```python
def render_sidebar() -> None:
```

**Purpose:** Build and display the sidebar menu

**Parameters:** None

**Returns:** None

**What it does:**
1. Opens sidebar context (`with st.sidebar:`)
2. Displays title and description
3. Displays workflow steps (01-06)
4. Displays directory path

**Code:**
```python
def render_sidebar() -> None:
    with st.sidebar:
        st.markdown("## Enterprise Test Generator")
        st.caption("Prepare a repository-specific testing prompt for GitHub Copilot.")
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
```

**Sidebar Display:**
```
═════════════════════════════════════
  Enterprise Test Generator
  Prepare a repository-specific testing
  prompt for GitHub Copilot.
  
  Workflow
  
  01  Enter Repository URL
  02  Clone Repository
  03  Generate Test Prompt
  04  Copy Prompt
  05  Paste into Copilot
  06  Generate Tests
  
  ─────────────────────────────────
  
  Local storage
  d:\test generator\repositories
═════════════════════════════════════
```

**Used by:**
- `main()` - called at start

---

### Function 5: `generate_repository_prompt()`

**Signature:**
```python
def generate_repository_prompt(repository_url: str) -> None:
```

**Purpose:** Main workflow orchestrator (core logic)

**Parameters:**
| Parameter | Type | Example | Description |
|-----------|------|---------|-------------|
| `repository_url` | str | `https://github.com/owner/repo` | URL to process |

**Returns:** None (stores results in session state)

**What it does:**
1. Validates URL using `validate_repository_url()`
2. Shows status progress bar
3. Extracts repo name using `repository_name()`
4. Checks cache using `is_git_repository()`
5. Clones/retrieves using `ensure_repository()`
6. Generates prompt using `build_prompt()`
7. Saves to disk using `write_text()`
8. Updates session state with results
9. Displays results in UI

**Code Flow:**
```python
def generate_repository_prompt(repository_url: str) -> None:
    """Run the backend workflow and persist its result in session state."""
    
    # Step 1: Validate URL
    valid, error_message = validate_repository_url(repository_url)
    if not valid:
        st.error(error_message, icon="⚠️")
        return

    try:
        with st.status("Preparing repository", expanded=True) as status:
            # Step 2: Extract repository name
            st.write("Validating repository URL")
            name = repository_name(repository_url)
            target = REPOSITORIES_DIR / name
            was_cloned = not target.exists()

            # Step 3: Check cache
            st.write("Checking local repository cache")
            if target.exists() and not is_git_repository(target):
                raise RuntimeError(
                    f"A non-Git folder already exists at {target}. "
                    "Rename or remove it before continuing."
                )

            # Step 4: Clone or use cached
            st.write("Cloning repository" if was_cloned else "Using existing clone")
            repository_path = ensure_repository(repository_url, REPOSITORIES_DIR)
            
            # Step 5: Update session state (clone info)
            st.session_state["repository_name"] = name
            st.session_state["repository_path"] = str(repository_path)
            st.session_state["clone_status"] = "Cloned successfully" if was_cloned else "Already cloned"

            # Step 6: Generate prompt
            st.write("Generating Test_Prompt.md")
            prompt = build_prompt(repository_url, repository_path)
            prompt_path = repository_path / "Test_Prompt.md"
            
            # Step 7: Save to disk
            prompt_path.write_text(prompt, encoding="utf-8")
            
            # Step 8: Update session state (prompt info)
            st.session_state["generated_prompt"] = prompt
            st.session_state["prompt_path"] = str(prompt_path)
            st.session_state["prompt_status"] = "Generated successfully"
            
            # Step 9: Show success
            status.update(label="Prompt ready", state="complete", expanded=False)
    
    except ValueError as error:  # URL validation failed
        st.error(f"Repository validation failed: {error}", icon="⚠️")
    except PermissionError as error:  # Can't write file
        st.error(f"Permission denied while accessing the repository folder: {error}", icon="🔒")
    except OSError as error:  # File system error
        st.error(f"Unable to write the generated prompt: {error}", icon="⚠️")
    except RuntimeError as error:  # ensure_repository failed
        st.error(f"Repository operation failed: {error}", icon="⚠️")
```

**Session State Updates:**
```python
st.session_state = {
    "repository_name": "tiny-music-player",
    "repository_path": "d:\\test generator\\repositories\\tiny-music-player",
    "clone_status": "Cloned successfully" or "Already cloned",
    "generated_prompt": "# Enterprise Test Generation Prompt\n...",
    "prompt_path": "d:\\test generator\\repositories\\tiny-music-player\\Test_Prompt.md",
    "prompt_status": "Generated successfully",
}
```

**Error Handling:**
- `ValueError` - URL validation failed
- `PermissionError` - Can't write to disk
- `OSError` - File system error
- `RuntimeError` - Clone failed or non-git folder exists
- Each shows different error icon and message

**Used by:**
- `main()` - called when user clicks button

---

### Function 6: `main()`

**Signature:**
```python
def main() -> None:
```

**Purpose:** Main Streamlit app entry point

**Parameters:** None

**Returns:** None

**What it does:**
1. Configures Streamlit page
2. Initializes session state
3. Renders sidebar
4. Applies CSS styling
5. Displays hero section
6. Shows URL input field
7. Displays status information
8. Shows generated prompt (if available)

**Code (Partial):**
```python
def main() -> None:
    # Configure page
    st.set_page_config(
        page_title="Enterprise Test Generator",
        page_icon="🧪",
        layout="wide",
        initial_sidebar_state="expanded",
    )
    
    # Initialize session state
    initialize_state()
    
    # Render sidebar
    render_sidebar()

    # Apply CSS styling (long style block)
    st.markdown("""
        <style>
        @import url('https://fonts.googleapis.com/...');
        :root { --ink: #17212b; --muted: #64727d; ... }
        .stApp { background: linear-gradient(...); ... }
        ...
        </style>
    """, unsafe_allow_html=True)

    # Display hero section
    st.markdown(
        '<div class="hero"><div class="eyebrow">Repository-aware QA workflow</div>'
        '<h1>Enterprise Test Generator</h1>'
        '<p>Generate repository-specific testing prompts for Copilot</p></div>',
        unsafe_allow_html=True,
    )

    # Input section
    with st.container():
        st.markdown('<div class="input-shell">', unsafe_allow_html=True)
        repository_url = st.text_input(
            "GitHub Repository URL",
            placeholder="https://github.com/owner/repository",
            help="Public GitHub or GitHub Enterprise repository URL.",
        )
        if st.button("Generate Test Prompt", type="primary", use_container_width=True):
            generate_repository_prompt(repository_url)  # Call orchestrator
        st.markdown("</div>", unsafe_allow_html=True)

    # Status badges
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

    # Prompt display & download
    if st.session_state["generated_prompt"]:
        st.markdown(
            '<div class="prompt-header"><h2>Generated Test_Prompt.md</h2></div>',
            unsafe_allow_html=True,
        )
        st.caption("Edit the prompt here, copy it into Copilot, or download the repository copy.")
        edited_prompt = st.text_area(
            "Generated prompt",
            value=st.session_state["generated_prompt"],
            height=620,
            label_visibility="collapsed",
        )
        st.session_state["generated_prompt"] = edited_prompt
        st.download_button(
            "Download Test_Prompt.md",
            data=edited_prompt,
            file_name="Test_Prompt.md",
            mime="text/markdown",
            use_container_width=False,
        )
```

**UI Sections:**
```
┌─────────────────────────────────────────┐
│  Sidebar                 Main Area       │
│                                          │
│ Enterprise     Repository-aware QA      │
│ Test Generator workflow                 │
│                          Enterprise Test│
│ Workflow:              Generator         │
│ 01 Enter URL   Generate test prompts    │
│ 02 Clone                                 │
│ 03 Generate    ┌──────────────────────┐ │
│ 04 Copy        │ URL input box         │ │
│ 05 Paste       │ [Submit Button]       │ │
│ 06 Generate    └──────────────────────┘ │
│                                          │
│ Local storage  ┌──────────────────────┐ │
│ d:\...repos    │ Name | Clone | Prompt│ │
│                │ Path │ Status│ Status│ │
│                └──────────────────────┘ │
│                                          │
│                ┌──────────────────────┐ │
│                │ Generated Prompt Text│ │
│                │ [Text Area - 620px]  │ │
│                │ [Download Button]    │ │
│                └──────────────────────┘ │
└─────────────────────────────────────────┘
```

**Used by:**
- Entry point: `if __name__ == "__main__": main()`

---

## 📝 FILE 3: `requirements.txt`

**Purpose:** Python dependencies specification

**Content:**
```
streamlit>=1.32,<2
```

**What it means:**
- Install Streamlit version 1.32 or newer
- But NOT version 2.0 or newer
- Version range: 1.32.x (e.g., 1.32.0, 1.32.1, 1.40, 1.99, but not 2.0)

**Installation:**
```bash
pip install -r requirements.txt
```

---

## 🔗 Function Call Relationships

### Web UI Flow (app.py):
```
main()
├── initialize_state()
├── render_sidebar()
├── st.text_input()  ◄─── User enters URL
└── st.button() "Generate Test Prompt"
    └── generate_repository_prompt(url)
        ├── validate_repository_url()
        ├── repository_name() [from generate_test_prompt]
        ├── is_git_repository() [from generate_test_prompt]
        ├── ensure_repository() [from generate_test_prompt]
        │   └── subprocess.run(["git", "clone", ...])
        ├── build_prompt() [from generate_test_prompt]
        ├── write_text()  ◄─── File saved to disk
        └── st.session_state[] updates
            └── status_badge() x4  ◄─── Display results
            └── st.text_area()
            └── st.download_button()
```

### CLI Flow (generate_test_prompt.py):
```
if __name__ == "__main__":
└── main()
    ├── parse_args()
    ├── input()  ◄─── Ask user for URL (if not provided)
    ├── ensure_repository()
    │   └── is_git_repository()
    │   └── repository_name()
    │   └── subprocess.run(["git", "clone", ...])
    ├── build_prompt()
    └── write_text()  ◄─── Save to file
```

---

## 📊 Data Flow Summary

```
User Input (URL)
    ↓
validate_repository_url()
    ↓ (Valid: True, Invalid: Error)
repository_name()
    ↓ (Extract: "repo-name")
is_git_repository()
    ↓ (Yes: Use cached, No: Clone)
ensure_repository()
    ↓ (Returns: /path/to/repo)
build_prompt()
    ↓ (Returns: String ~5000 chars)
write_text()
    ↓ (Saves: Test_Prompt.md)
st.session_state[]
    ↓ (Stores: all results)
UI Display
    ↓ (Shows: status, prompt, download)
User Actions
    ├── Copy prompt
    ├── Download file
    └── Edit prompt
```

---

## ✅ Summary Table

| Component | Purpose | Takes | Returns | Called By |
|-----------|---------|-------|---------|-----------|
| `repository_name()` | Extract repo name from URL | URL string | Repo name | `ensure_repository()`, `app.py` |
| `is_git_repository()` | Check if path is git repo | Path object | bool | `ensure_repository()`, `app.py` |
| `ensure_repository()` | Clone or use cached repo | URL + Path | Path | `main()`, `app.py` |
| `build_prompt()` | Generate test prompt | URL + Path | Prompt string | `main()`, `app.py` |
| `parse_args()` | Parse CLI arguments | None | Namespace | `main()` |
| `main()` [CLI] | CLI entry point | None | exit code | Script execution |
| `validate_repository_url()` | Validate URL format | URL string | (bool, msg) | `generate_repository_prompt()` |
| `initialize_state()` | Setup session vars | None | None | `main()` [app] |
| `status_badge()` | Display status card | label, value, icon | None | `main()` [app] |
| `render_sidebar()` | Build sidebar | None | None | `main()` [app] |
| `generate_repository_prompt()` | Workflow orchestrator | URL string | None | Button click |
| `main()` [app] | App entry point | None | None | Streamlit execution |

---

**Report Generated:** 2026-09-23  
**Completeness:** Every file, function, and parameter documented
