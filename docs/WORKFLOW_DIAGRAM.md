# Test Generator Automation - Workflow Diagram

## Complete User Flow

```
┌─────────────────────────────────────────────────────────────────┐
│                        START                                     │
│                   Test Generator UI                              │
└────────────────────────────┬────────────────────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│  STEP 1: Input Repository                                        │
│  - User enters GitHub repository URL                             │
│  - Click "Generate Test Prompt"                                  │
└────────────────────────────┬────────────────────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│  STEP 2: Clone & Generate                                        │
│  - Repository cloned to repositories/                            │
│  - Test_Prompt.md generated from repo structure                  │
│  - Prompt stored in session state                                │
└────────────────────────────┬────────────────────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│  STEP 3: Approval Gate                                           │
│  - User reviews generated prompt                                 │
│  - Can download Test_Prompt.md for reference                     │
│  - Two options: ✅ Proceed OR ❌ Reject                          │
└────────────┬────────────────────────────┬──────────────────────┘
             │                            │
      [✅ PROCEED]                    [❌ REJECT]
             │                            │
             ▼                            ▼
    ┌──────────────────┐      ┌──────────────────────┐
    │ Clipboard Copy   │      │ Clear Prompt         │
    │ (pyperclip)      │      │ Return to Input      │
    │                  │      │ Modify & Retry       │
    └────────┬─────────┘      └──────────────────────┘
             │
             ▼
    ┌──────────────────┐
    │ Open VS Code     │
    │ Test_Prompt.md   │
    │ (subprocess)     │
    └────────┬─────────┘
             │
             ▼
    ┌──────────────────┐
    │ Create Folders   │
    │ tests/           │
    │ tests/specs/     │
    │ tests/scripts/   │
    │ tests/coverage/  │
    └────────┬─────────┘
             │
             ▼
    ┌─────────────────────────────────────┐
    │  STEP 4: Success & Next Steps       │
    │  ✅ Prompt copied to clipboard      │
    │  ✅ VS Code file opened             │
    │  ✅ Test folders created            │
    │                                     │
    │  Show user:                         │
    │  1. Open Copilot Chat (Ctrl+Shift+I)│
    │  2. Paste prompt (Ctrl+V)           │
    │  3. Generate tests with Copilot     │
    │  4. Save files to tests/ folder     │
    │  5. Return and click Load Results   │
    └────────┬────────────────────────────┘
             │
             ▼
    ┌─────────────────────────────────────┐
    │ USER: Copilot Chat Interaction      │
    │                                     │
    │ - Paste prompt into Copilot         │
    │ - Copilot generates test specs      │
    │ - Copilot generates test scripts    │
    │ - Copilot generates coverage       │
    │ - Save files to tests/ folder       │
    │   - tests/specs/001_*.md            │
    │   - tests/test_scripts/001_*_test.* │
    │   - tests/coverage/summary.md       │
    └────────┬────────────────────────────┘
             │
             ▼
    ┌──────────────────────────────────────┐
    │  STEP 5: Load Results                │
    │  Click "🔄 Load Results" Button      │
    │                                      │
    │  - Scan tests/ folder               │
    │  - Read all .md and .py files       │
    │  - Organize by category:            │
    │    • specs/                         │
    │    • test_scripts/                  │
    │    • coverage/                      │
    │  - Store in session state           │
    └────────┬───────────────────────────┘
             │
             ▼
    ┌──────────────────────────────────────┐
    │  STEP 6: Display Results             │
    │                                      │
    │  Three Expandable Sections:          │
    │  📋 Test Specifications              │
    │     - Each spec with full content    │
    │  🧪 Test Scripts                     │
    │     - Each script with code syntax   │
    │  📊 Coverage Summary                 │
    │     - Coverage report markdown       │
    └────────┬───────────────────────────┘
             │
             ▼
    ┌──────────────────────────────────────┐
    │  STEP 7: Download Options            │
    │                                      │
    │  - "Create ZIP Archive" button       │
    │  - ZIP contains entire tests/ dir    │
    │  - "Download tests.zip" button       │
    │  - Also download individual files    │
    │                                      │
    │  Optional: Auto-Refresh              │
    │  - Toggle "🔁 Auto-Refresh (5s)"    │
    │  - Watches folder every 5 seconds    │
    │  - Auto-rerun on new files           │
    └────────┬───────────────────────────┘
             │
             ▼
┌─────────────────────────────────────────┐
│            WORKFLOW COMPLETE             │
│  All tests downloaded and ready          │
└─────────────────────────────────────────┘
```

---

## Key Automation Points

### 🔄 Clipboard Integration
```
User clicks "Proceed"
    ↓
copilot_bridge.copy_to_clipboard(prompt_text)
    ↓
Uses pyperclip library
    ↓
Prompt automatically in system clipboard
    ↓
User pastes with Ctrl+V in Copilot Chat
```

### 📂 VS Code Integration
```
User clicks "Proceed"
    ↓
copilot_bridge.open_in_vscode(filepath)
    ↓
subprocess.Popen(["code", filepath])
    ↓
VS Code launches with file open
```

### 📁 Folder Structure
```
copilot_bridge.create_test_folders(repo_path)
    ↓
Creates:
├── tests/
├── tests/specs/
├── tests/test_scripts/
└── tests/coverage/
```

### 📂 Result Collection
```
User clicks "Load Results"
    ↓
copilot_bridge.get_test_results(test_folder_path)
    ↓
Scans three folders recursively
    ↓
Reads all .md and .py files
    ↓
Returns organized dictionary:
{
  "specs": [{"name": "...", "content": "..."}],
  "test_scripts": [...],
  "coverage": [...]
}
```

### 📦 ZIP Creation
```
User clicks "Create ZIP Archive"
    ↓
copilot_bridge.zip_test_results(test_folder, output_path)
    ↓
zipfile.ZipFile creates archive
    ↓
Preserves directory structure
    ↓
Returns path to created .zip
```

---

## Error Handling Paths

### ❌ Clipboard Unavailable
```
copy_to_clipboard() returns False
    ↓
Show warning: "Couldn't copy to clipboard"
    ↓
Display prompt in st.code() block
    ↓
User can select & copy manually
```

### ❌ VS Code Not Found
```
open_in_vscode() returns False
    ↓
Show warning: "Couldn't open VS Code"
    ↓
Display file path
    ↓
User can open file manually
```

### ❌ Folder Creation Failed
```
create_test_folders() returns False
    ↓
Show error: "Failed to create test folders"
    ↓
Stop workflow (don't proceed)
    ↓
User must check permissions & retry
```

### ❌ No Results Found
```
get_test_results() returns empty structure
    ↓
Show info: "No files found yet"
    ↓
Prompt: "Save your Copilot output and try again"
    ↓
User returns after saving files
```

### ❌ ZIP Creation Failed
```
zip_test_results() returns None
    ↓
Show error with details
    ↓
User can try again or download individual files
```

---

## Session State Management

```
Session State Variables:
├── repository_name (str)
├── repository_path (str)
├── clone_status (str)
├── prompt_status (str)
├── generated_prompt (str)
├── prompt_path (str)
├── approval_status (str) - "Pending" → "Approved" → "Rejected"
├── proceed_clicked (bool) - Controls display_results_section()
├── test_results (dict) - Stores loaded results
├── auto_refresh_active (bool)
└── last_results_count (int)
```

---

## Data Flow

```
GitHub Repo
    ↓
ensure_repository() - Clone/verify
    ↓
build_prompt() - Generate prompt from structure
    ↓
Test_Prompt.md written to disk
    ↓
Session state: generated_prompt
    ↓
User clicks Proceed
    ↓
Clipboard: copied prompt
    ↓
VS Code: opens Test_Prompt.md
    ↓
Folders: created tests/ structure
    ↓
Session state: proceed_clicked = True
    ↓
User: Copilot Chat interaction
    ↓
User: saves files to tests/
    ↓
User clicks: Load Results
    ↓
get_test_results() scans tests/
    ↓
Files read and organized
    ↓
Session state: test_results
    ↓
Display: 3 expanders with all artifacts
    ↓
Download: Individual files or ZIP
```
