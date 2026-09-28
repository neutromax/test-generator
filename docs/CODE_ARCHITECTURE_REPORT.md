# Test Generator - Code Architecture & Data Flow Analysis

**Generated:** 2026-09-23  
**Focus:** Code connections, data flow, and component interactions

---

## 🔗 Code Architecture Overview

### File Structure & Relationships

```
test generator/
├── app.py                      ◄─── ENTRY POINT (Streamlit UI)
│   └─ imports from ──────────────► generate_test_prompt.py
├── generate_test_prompt.py    ◄─── BACKEND ENGINE
└── repositories/              ◄─── OUTPUT DIRECTORY
    └─ [cloned repos]
```

**Key Insight:** The UI (`app.py`) and backend (`generate_test_prompt.py`) are **tightly coupled**. The UI imports 4 core functions from the backend and orchestrates them.

---

## 📊 Data Flow Diagram

```
┌─────────────────────────────────────────────────────────────────────┐
│ USER INTERFACE (app.py)                                             │
│                                                                      │
│  User enters URL in text input                                      │
│         ↓                                                            │
│  "Generate Test Prompt" button clicked                              │
│         ↓                                                            │
│  generate_repository_prompt(repository_url)                         │
│         ↓                                                            │
├─────────────────────────────────────────────────────────────────────┤
│ BACKEND ENGINE (generate_test_prompt.py)                            │
│                                                                      │
│  1️⃣  validate_repository_url(url) ─────────────────────┐           │
│      ↓ [returns: bool, error_message]                  │           │
│      URL format check (https, github.com, owner/repo)  │           │
│                                                         │           │
│  2️⃣  repository_name(url) ◄────────────────────────────┘           │
│      ↓ [returns: string]                                           │
│      Extract & clean repo name from URL path                       │
│      stores in: name = "tiny-music-player"                         │
│      calculates: target = REPOSITORIES_DIR / name                  │
│                                                                     │
│  3️⃣  is_git_repository(target) [Check 1]                          │
│      ↓ [returns: bool]                                             │
│      Git subprocess: git -C {path} rev-parse --is-inside-work-tree │
│      ─┬─ YES: Use existing cached repo                             │
│        └─ NO: Proceed to clone                                     │
│                                                                     │
│  4️⃣  ensure_repository(url, REPOSITORIES_DIR)                     │
│      ↓ [returns: Path]                                             │
│      ─┬─ IF repo exists & is git repo:                             │
│        │  return target (cached)                                   │
│        │                                                            │
│        └─ IF repo doesn't exist:                                   │
│           Git subprocess: git clone {url} {target}                 │
│           returns: target path                                     │
│      stores in: repository_path = {cloned path}                    │
│                                                                     │
│  5️⃣  build_prompt(repository_url, repository_path)                │
│      ↓ [returns: string]                                           │
│      Creates hardcoded template string with:                       │
│      • Repository URL                                              │
│      • Local path                                                  │
│      • 9-phase workflow instructions                               │
│      • Output structure requirements                               │
│      stores in: prompt = {multi-line string}                       │
│                                                                     │
└─────────────────────────────────────────────────────────────────────┘
                            ↓
┌─────────────────────────────────────────────────────────────────────┐
│ OUTPUT GENERATION (Still in app.py)                                 │
│                                                                      │
│  6️⃣  SAVE TO DISK                                                   │
│      prompt_path = repository_path / "Test_Prompt.md"              │
│      prompt_path.write_text(prompt, encoding="utf-8")              │
│      File created at: {repo_path}/Test_Prompt.md                   │
│                                                                     │
│  7️⃣  STORE IN SESSION STATE                                         │
│      st.session_state["repository_name"] = name                    │
│      st.session_state["repository_path"] = str(repository_path)    │
│      st.session_state["clone_status"] = "Cloned/Already cloned"    │
│      st.session_state["generated_prompt"] = prompt                 │
│      st.session_state["prompt_path"] = str(prompt_path)            │
│      st.session_state["prompt_status"] = "Generated successfully"  │
│                                                                     │
│  8️⃣  DISPLAY IN UI                                                  │
│      • Show status badges (Name, Clone Status, Prompt Status, Path)│
│      • Display editable text area with prompt content              │
│      • Show "Download Test_Prompt.md" button                       │
│                                                                     │
└─────────────────────────────────────────────────────────────────────┘
                            ↓
┌─────────────────────────────────────────────────────────────────────┐
│ USER ACTION (Manual - Not Automated)                                │
│                                                                      │
│  9️⃣  COPY PROMPT                                                    │
│      User copies generated Test_Prompt.md from text area            │
│      OR downloads the file                                          │
│                                                                     │
│  🔟  PASTE INTO CLAUDE                                              │
│      User opens Claude Chat                                         │
│      Pastes Test_Prompt.md content                                  │
│      ⚠️  THIS IS MANUAL - NOT AUTOMATED                             │
│                                                                     │
│  1️⃣1️⃣  CLAUDE PROCESSES PROMPT                                      │
│      Claude reads the 9-phase workflow                             │
│      Claude analyzes {repository_path}                             │
│      Claude generates specifications: tests/specs/001_.md, etc.    │
│      ⚠️  STOPS at approval gate (Phase 5)                           │
│                                                                     │
│  1️⃣2️⃣  USER APPROVES                                                │
│      User reviews specs                                             │
│      User sends: "Approve" / "Continue" / "Generate Tests"         │
│      ⚠️  THIS IS MANUAL - NOT AUTOMATED                             │
│                                                                     │
│  1️⃣3️⃣  CLAUDE GENERATES TESTS                                       │
│      Claude creates: tests/test_scripts/001_test.py, etc.          │
│      Claude creates: tests/coverage/coverage_summary.md            │
│      Claude creates: tests/Results.md, tests/Result.md             │
│      Claude executes tests (if possible)                           │
│                                                                     │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 🔄 Function Call Chain

### When "Generate Test Prompt" is clicked:

```
app.py :: main()
  └─ st.button("Generate Test Prompt") is True
      └─ generate_repository_prompt(repository_url)
          │
          ├─ [1] validate_repository_url(repository_url)
          │   └─ Returns: (bool, str)
          │   └─ Uses: urlparse() from urllib.parse
          │   └─ Checks: scheme, hostname, path format
          │
          ├─ [2] repository_name(repository_url)
          │   └─ Returns: str (e.g., "tiny-music-player")
          │   └─ Uses: urlparse(), Path().name
          │   └─ Handles: .git suffix removal
          │
          ├─ [3] REPOSITORIES_DIR / name
          │   └─ Creates: Path object (d:\test generator\repositories\tiny-music-player)
          │   └─ Checks: target.exists()
          │
          ├─ [4] is_git_repository(target)
          │   └─ Returns: bool
          │   └─ Runs: subprocess.run(["git", "-C", str(path), "rev-parse", "--is-inside-work-tree"])
          │   └─ Decision point:
          │       ├─ True: Use cached repo
          │       └─ False: Proceed to clone
          │
          ├─ [5] ensure_repository(repository_url, REPOSITORIES_DIR)
          │   └─ Returns: Path (repository location)
          │   └─ If exists & is git:
          │   │   └─ print("Using existing repository")
          │   │   └─ return target
          │   └─ If not git repo:
          │       └─ raise RuntimeError
          │   └─ If doesn't exist:
          │       └─ CLONE: subprocess.run(["git", "clone", repository_url, str(target)])
          │       └─ return target
          │
          ├─ [6] build_prompt(repository_url, repository_path)
          │   └─ Returns: str (hardcoded multi-line prompt template)
          │   └─ Uses: f-string with variables injected
          │   └─ Content: 9-phase workflow instructions
          │
          ├─ [7] prompt_path.write_text(prompt)
          │   └─ File created: {repository_path}/Test_Prompt.md
          │   └─ Encoding: UTF-8
          │
          └─ [8] st.session_state updates (6 keys updated)
              └─ Display UI with results
```

---

## 💾 Data Structures & Variables

### Session State Storage (In Memory - Persists During Session)
```python
st.session_state = {
    "repository_name": "tiny-music-player",           # Type: str
    "repository_path": "D:\\test generator\\repositories\\tiny-music-player",  # Type: str
    "clone_status": "Cloned successfully",            # Type: str
    "prompt_status": "Generated successfully",        # Type: str
    "generated_prompt": "# Enterprise Test Generation...",  # Type: str (multi-line)
    "prompt_path": "D:\\...\\tiny-music-player\\Test_Prompt.md",  # Type: str
}
```

### File System Output
```
D:\test generator\
└── repositories\
    └── tiny-music-player\           ◄─ Cloned by ensure_repository()
        ├── [all repo files/folders]
        ├── Test_Prompt.md           ◄─ Created by build_prompt() + write_text()
        ├── tests\
        │   ├── specs\               ◄─ (Will be created by Claude)
        │   ├── test_scripts\        ◄─ (Will be created by Claude)
        │   └── coverage\            ◄─ (Will be created by Claude)
        └── [other repo files]
```

---

## 🔌 Import Dependencies

### app.py imports:
```python
from pathlib import Path          # Python stdlib - path handling
from urllib.parse import urlparse # Python stdlib - URL parsing
import streamlit as st            # External - UI framework
import re                         # Python stdlib - regex validation

from generate_test_prompt import (
    TOOL_DIR,                     # Constant: tool directory path
    build_prompt,                 # Function: generates prompt string
    ensure_repository,            # Function: clones or returns cached repo
    is_git_repository,            # Function: checks if path is git repo
    repository_name,              # Function: extracts repo name from URL
)
```

### generate_test_prompt.py imports:
```python
from __future__ import annotations  # Python 3.9+ - type hints
import argparse                     # Python stdlib - CLI argument parsing
import subprocess                   # Python stdlib - run git commands
import sys                          # Python stdlib - system functions
from pathlib import Path            # Python stdlib - path handling
from urllib.parse import urlparse   # Python stdlib - URL parsing
```

### Global Constants:
```python
TOOL_DIR = Path(__file__).resolve().parent  
# Resolves to: d:\test generator (absolute path)

REPOSITORIES_DIR = TOOL_DIR / "repositories"
# Resolves to: d:\test generator\repositories

PROMPT_FILE = TOOL_DIR / "Test_Prompt.md"
# Resolves to: d:\test generator\Test_Prompt.md (not used in current flow)
```

---

## 🎯 Input Entry Points

### 1. Web UI Entry Point (Streamlit)
**File:** `app.py`  
**Function:** `main()`  
**Input Method:**
```python
repository_url = st.text_input(
    "GitHub Repository URL",
    placeholder="https://github.com/owner/repository",
    help="Public GitHub or GitHub Enterprise repository URL."
)
# User types: https://github.com/martinmimigames/tiny-music-player
```

**Validation:**
```python
def validate_repository_url(repository_url: str) -> tuple[bool, str]:
    # Check 1: URL scheme must be http or https
    # Check 2: Hostname must exist
    # Check 3: Hostname must be github.com or contain "github"
    # Check 4: Path must have exactly 2 parts (owner/repo)
    # Check 5: Each part must match pattern [A-Za-z0-9_.-]+
```

### 2. CLI Entry Point (Command Line)
**File:** `generate_test_prompt.py`  
**Function:** Can be called directly (if added to main)
```python
# Usage: python generate_test_prompt.py <repo-url> [--clone-root <folder>] [--output <file>]
# Note: argparse setup is in the code but main entry point is not implemented
```

---

## 🚀 Processing Flow - Step by Step

### Step 1: User Input → Validation
```
Input: "https://github.com/martinmimigames/tiny-music-player"
        ↓
validate_repository_url()
├─ urlparse() → ParseResult(scheme='https', hostname='github.com', path='/martinmimigames/tiny-music-player')
├─ Check scheme in {'http', 'https'} ✓
├─ Check hostname == 'github.com' ✓
├─ Split path by '/' → ['martinmimigames', 'tiny-music-player'] ✓
├─ Check each part matches [A-Za-z0-9_.-]+ ✓
        ↓
Output: (True, "")  # Valid!
```

### Step 2: Extract Repository Name
```
Input: "https://github.com/martinmimigames/tiny-music-player"
        ↓
repository_name()
├─ urlparse() → ParseResult(path='/martinmimigames/tiny-music-player')
├─ Path(parsed.path.rstrip("/")).name → "tiny-music-player"
├─ Check if ends with ".git" → No
├─ Check if empty or {".", ".."} → No
        ↓
Output: "tiny-music-player"
```

### Step 3: Calculate Target Path
```
repository_name = "tiny-music-player"
REPOSITORIES_DIR = Path("d:\\test generator\\repositories")
        ↓
target = REPOSITORIES_DIR / repository_name
        ↓
Output: Path("d:\\test generator\\repositories\\tiny-music-player")
```

### Step 4: Check Cache
```
target = Path("d:\\test generator\\repositories\\tiny-music-player")
        ↓
is_git_repository(target)?
├─ target.exists()? 
│   ├─ NO → Skip to Clone
│   └─ YES → Check if it's a git repo
├─ subprocess.run(["git", "-C", str(target), "rev-parse", "--is-inside-work-tree"])
│   └─ Return code == 0 AND stdout.strip() == "true"?
│       ├─ YES → Use cached (return target)
│       └─ NO → Error (non-git folder exists)
        ↓
Output: bool (True = cached, False = need to clone)
```

### Step 5: Clone or Return Cached
```
ensure_repository(url, REPOSITORIES_DIR)
        ↓
if target.exists():
    ├─ is_git_repository(target)?
    │   ├─ YES → print("Using existing repository: {target}")
    │   │         return target
    │   └─ NO → raise RuntimeError("...not a Git repository")
else:
    └─ print("Cloning repository into: {target}")
       subprocess.run(["git", "clone", url, str(target)])
       return target
        ↓
Output: Path("d:\\test generator\\repositories\\tiny-music-player")
repository_path = target
```

### Step 6: Build Prompt String
```
build_prompt(repository_url, repository_path)
        ↓
return f"""# Enterprise Test Generation Prompt

Analyze and test the repository below.

- Repository URL: `{repository_url}`
- Local repository path: `{repository_path}`

You are an Enterprise Test Generation Agent...
[9 phases of workflow]
[Output structure requirements]
[Specification format]
[Test requirements after approval]
[Coverage and results requirements]
[Approval review summary]
"""
        ↓
Output: str (multi-line prompt with variables injected)
```

### Step 7: Save to Disk
```
prompt = "# Enterprise Test Generation Prompt\n..."
repository_path = Path("d:\\test generator\\repositories\\tiny-music-player")
        ↓
prompt_path = repository_path / "Test_Prompt.md"
prompt_path.write_text(prompt, encoding="utf-8")
        ↓
Output: File created at d:\test generator\repositories\tiny-music-player\Test_Prompt.md
```

### Step 8: Store in Session State
```
st.session_state["repository_name"] = "tiny-music-player"
st.session_state["repository_path"] = "d:\\test generator\\repositories\\tiny-music-player"
st.session_state["clone_status"] = "Cloned successfully"
st.session_state["generated_prompt"] = "# Enterprise Test Generation Prompt\n..."
st.session_state["prompt_path"] = "d:\\test generator\\repositories\\tiny-music-player\\Test_Prompt.md"
st.session_state["prompt_status"] = "Generated successfully"
        ↓
Output: Session persisted (visible in UI)
```

### Step 9: Display in UI
```
Status badges show:
├─ Repository Name: "tiny-music-player"
├─ Clone Status: "Cloned successfully"
├─ Prompt Status: "Generated successfully"
└─ Local Path: "d:\\test generator\\repositories\\tiny-music-player"

Text area shows:
└─ Full editable prompt content (620px height)

Buttons available:
├─ "Download Test_Prompt.md" (downloads the file)
└─ Copy button (browser native)
```

---

## 🔴 Current Manual Handoff Points

The system currently requires **manual human intervention** at these points:

1. **After Step 9** → User must manually:
   ```
   - Copy the prompt text
   - Open Claude Chat
   - Paste the prompt
   ```

2. **After Claude generates specs** → User must manually:
   ```
   - Read the specifications
   - Review the approval gate summary
   - Send approval message to Claude
   ```

3. **After Claude generates tests** → User must manually:
   ```
   - Run tests
   - Review results
   - Copy results back
   ```

---

## 🔧 Where Each Component Does Its Work

| Component | Location | Responsibility | Input | Output |
|-----------|----------|-----------------|-------|--------|
| **URL Input** | app.py, line 125-129 | User provides repository URL | String (URL) | Validated string |
| **validate_repository_url()** | app.py, line 24-33 + generate_test_prompt.py | URL validation | String (URL) | (bool, str) |
| **repository_name()** | generate_test_prompt.py, line 17-26 | Extract repo name from URL | String (URL) | String (name) |
| **is_git_repository()** | generate_test_prompt.py, line 29-37 | Check if path is git repo | Path object | bool |
| **ensure_repository()** | generate_test_prompt.py, line 40-60 | Clone or return cached | String (URL), Path | Path (repo location) |
| **build_prompt()** | generate_test_prompt.py, line 63-156 | Create test prompt | String (URL), Path | String (prompt) |
| **write_text()** | app.py, line 102 | Save prompt to disk | String (prompt) | File created |
| **Session State** | app.py, line 103-109 | Store in memory | Multiple variables | Streamlit session dict |
| **UI Display** | app.py, line 139-178 | Show results to user | Session state | Rendered HTML/UI |

---

## 🎯 Current Bottlenecks (Manual Steps)

```
┌─────────────────────────────┐
│  Generate Test Prompt.md    │  ✅ AUTOMATED (app.py + generate_test_prompt.py)
│  (Repo → Context)           │
└────────────┬────────────────┘
             ↓
┌─────────────────────────────┐
│  Copy → Paste to Claude     │  ❌ MANUAL (User copy-pastes)
│  (UI → Claude Chat)         │
└────────────┬────────────────┘
             ↓
┌─────────────────────────────┐
│  Claude generates Specs     │  ❓ CLAUDE SIDE (not in this codebase)
│  (Analyze Repo)             │
└────────────┬────────────────┘
             ↓
┌─────────────────────────────┐
│  User approves Specs        │  ❌ MANUAL (User types "Approve")
│  (Review → Approve)         │
└────────────┬────────────────┘
             ↓
┌─────────────────────────────┐
│  Claude generates Tests     │  ❓ CLAUDE SIDE (not in this codebase)
│  + Coverage + Results       │
└─────────────────────────────┘
```

---

## 💡 What to Automate Next

To make this fully automated (without manual copy-paste):

### Option A: Direct Anthropic API Integration
```python
# Add to app.py:
import anthropic

client = anthropic.Anthropic(api_key=st.secrets["ANTHROPIC_API_KEY"])

# After building prompt:
response = client.messages.create(
    model="claude-3-5-sonnet-20241022",
    max_tokens=4096,
    messages=[{
        "role": "user",
        "content": prompt  # The hardcoded Test_Prompt template
    }]
)

specs_output = response.content[0].text
# Save to tests/specs/001_.md, 002_.md, etc.
# Ask for approval
# Generate tests automatically
```

### Option B: Keep Manual but Improve UX
```python
# Add to app.py:
# - Approval input field (instead of manual Claude message)
# - API call to Claude when user clicks "Approve & Generate Tests"
# - Display test results in UI
```

**Current gaps:**
- No Anthropic API integration
- No approval workflow in app
- No test execution framework
- No results parsing

---

## 📋 Summary: Code Connection Map

```
generate_test_prompt.py (Backend Library)
    ├── repository_name(url) → str
    ├── is_git_repository(path) → bool
    ├── ensure_repository(url, root) → Path [RUNS: git clone]
    └── build_prompt(url, path) → str

app.py (Streamlit UI - Main Application)
    ├── main()
    │   ├── render_sidebar()
    │   ├── st.text_input() ◄─── USER INPUT (URL)
    │   └── st.button() "Generate Test Prompt"
    │
    └── generate_repository_prompt(url)  [ORCHESTRATOR]
        ├─ validate_repository_url() [from app.py]
        ├─ repository_name() [from generate_test_prompt]
        ├─ is_git_repository() [from generate_test_prompt]
        ├─ ensure_repository() [from generate_test_prompt] ◄─── GIT CLONE HAPPENS HERE
        ├─ build_prompt() [from generate_test_prompt]
        ├─ prompt_path.write_text() ◄─── FILE SAVED TO DISK
        └─ st.session_state[] ◄─── DATA STORED IN MEMORY
            └─ st.text_area() / st.download_button() ◄─── UI DISPLAY

Workflow:
URL → validate → extract name → check cache → clone/reuse → build prompt → save file → store in session → display UI
```

---

## 🎓 Key Takeaways

1. **Entry Point:** User types URL in Streamlit text input
2. **Processing:** Backend functions validate, extract, clone, and build
3. **Output:** Test_Prompt.md file + session state
4. **Current Limitation:** Manual copy-paste to Claude (no API integration)
5. **To Automate:** Add Anthropic API client to send prompt and receive specs
6. **Data Flow:** URL → Functions → Path → String → File → UI
7. **Git Integration:** Happens via `subprocess.run(["git", "clone", ...])` in `ensure_repository()`

---

**Report Generated:** 2026-09-23  
**Next Step:** Implement Anthropic API integration to automate Claude interaction
