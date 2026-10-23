# Quick Start Guide - Test Generator Automation

## 🚀 Installation (First Time)

```bash
# Navigate to test generator directory
cd d:\test generator

# Install dependencies
pip install -r requirements.txt

# Note: pyperclip requires xclip/xsel on Linux, works natively on Windows/macOS
```

## ▶️ Running the Application

```bash
# Start the Streamlit web UI
streamlit run app.py

# App opens at http://localhost:8501
```

## 📋 Step-by-Step User Workflow

### 1️⃣ **Enter Repository URL**
- Paste GitHub repository URL: `https://github.com/owner/repo`
- Click "Generate Test Prompt"
- Wait for cloning and prompt generation (progress shown in UI)

### 2️⃣ **Review Generated Prompt**
- Prompt displays in expandable section
- Can download Test_Prompt.md if desired
- Review for accuracy

### 3️⃣ **Click Proceed Button**
- ✅ Prompt automatically copied to clipboard
- ✅ Test_Prompt.md opens in VS Code
- ✅ Test folder structure created automatically
- Next steps displayed with instructions

### 4️⃣ **Use Copilot Chat in VS Code**
```
1. Open Copilot Chat: Ctrl+Shift+I
2. Paste prompt: Ctrl+V (already in clipboard!)
3. Interact with Copilot to generate tests
4. Ask Copilot to save files to tests/ folder:
   - tests/specs/001_*.md
   - tests/test_scripts/001_*_test.py
   - tests/coverage/coverage_summary.md
```

### 5️⃣ **Return to App and Load Results**
- Click "🔄 Load Results" button
- App scans tests/ folder
- All generated files displayed

### 6️⃣ **View Results**
- **📋 Test Specifications** - Expandable, full markdown content
- **🧪 Test Scripts** - Syntax-highlighted code
- **📊 Coverage Summary** - Coverage report

### 7️⃣ **Download Files**
- Option 1: "📦 Create ZIP Archive" → "💾 Download tests.zip"
- Option 2: Download individual files from each section

## 🔧 Troubleshooting

| Problem | Solution |
|---------|----------|
| **Clipboard copy fails** | Show warning → User can copy manually from code block |
| **VS Code doesn't open** | Show warning with file path → User opens manually |
| **Folders not created** | Check file permissions, try again |
| **No results found** | Save files to correct folders and reload |
| **pyperclip not installed** | Install: `pip install pyperclip` |
| **Streamlit not installed** | Install: `pip install streamlit` |

## 📁 File Structure After Running

```
d:\test generator\
├── repositories/
│   └── {repo-name}/
│       ├── ... (cloned repo)
│       ├── Test_Prompt.md
│       └── tests/
│           ├── specs/
│           │   ├── 001_feature1.md
│           │   ├── 002_feature2.md
│           │   └── ...
│           ├── test_scripts/
│           │   ├── 001_feature1_test.py
│           │   ├── 002_feature2_test.py
│           │   └── ...
│           └── coverage/
│               └── coverage_summary.md
├── app.py
├── generate_test_prompt.py
├── copilot_bridge.py (NEW)
└── requirements.txt (UPDATED)
```

## 🎛️ Advanced Features

### Auto-Refresh (Watch for New Files)
```
1. After clicking "Load Results"
2. Check "🔁 Auto-Refresh (5s)" checkbox
3. App watches tests/ folder every 5 seconds
4. Auto-reruns UI when new files detected
5. Great for long-running Copilot prompts
```

### Reject & Modify
```
1. After reviewing prompt, click "❌ Reject"
2. Modify repository URL or inputs
3. Click "Generate Test Prompt" again
4. New prompt generated for new/same repo
```

## 🔍 What Each Function Does

### `copilot_bridge.copy_to_clipboard(prompt_text)`
- Takes: Test prompt string
- Does: Copies to system clipboard
- Returns: `True` if success, `False` if failed
- Usage: Automated when user clicks "Proceed"

### `copilot_bridge.open_in_vscode(filepath)`
- Takes: Path to Test_Prompt.md
- Does: Launches VS Code with file
- Returns: `True` if success, `False` if failed
- Usage: Automated when user clicks "Proceed"

### `copilot_bridge.create_test_folders(base_path)`
- Takes: Repository base path
- Does: Creates tests/ folder structure
- Returns: `True` if created/exists, `False` if failed
- Usage: Automated when user clicks "Proceed"

### `copilot_bridge.get_test_results(test_folder_path)`
- Takes: Path to tests/ folder
- Does: Scans and reads all test files
- Returns: Dictionary with specs, scripts, coverage
- Usage: Called when user clicks "Load Results"

### `copilot_bridge.zip_test_results(test_folder_path, output_path)`
- Takes: tests/ folder path and output ZIP path
- Does: Creates ZIP archive of all test files
- Returns: Path to ZIP file if success, `None` if failed
- Usage: Called when user clicks "Create ZIP Archive"

## 🛠️ Development

### Adding New Features
1. **New automation functions**: Add to `copilot_bridge.py`
2. **New UI sections**: Add functions in `app.py` (e.g., `display_*()`)
3. **New state tracking**: Add to `initialize_state()` defaults

### Code Organization
```python
# copilot_bridge.py
- Pure functions, no Streamlit dependency
- Error handling with try/except
- Print statements for debugging
- Docstrings explaining each function

# app.py
- Streamlit UI components
- Session state management
- Calls copilot_bridge functions
- Displays results and errors
```

### Testing Locally
```bash
# Syntax check
python -m py_compile copilot_bridge.py app.py

# Run in debug mode
streamlit run app.py --logger.level=debug
```

## 📚 Key Libraries Used

| Library | Purpose | Why |
|---------|---------|-----|
| `streamlit` | Web UI framework | Fast, interactive UI without JS |
| `pathlib` | Path operations | Cross-platform path handling |
| `subprocess` | External processes | Launch VS Code |
| `pyperclip` | Clipboard access | Auto-copy prompt |
| `zipfile` | Archive creation | Package test results |
| `zipfile` | Archive reading | Organize by type |

## ⚙️ Configuration

### No Configuration Needed!
- Works out of the box
- Auto-detects VS Code installation
- Auto-detects system clipboard
- No API keys required
- No external services required

### Optional: Change Clipboard Library
If pyperclip doesn't work on your system:
```python
# In copilot_bridge.py, modify copy_to_clipboard():
# Use alternative: pyperclip3, clipboard, xclip-wrapper, etc.
```

## 📞 Common Questions

**Q: Do I need an API key?**
A: No! All automation uses system tools (clipboard, subprocess, file I/O).

**Q: Can I use this with GitHub Enterprise?**
A: Yes! Works with any GitHub or GitHub Enterprise URL.

**Q: What if I close the Streamlit app mid-workflow?**
A: Your cloned repositories are saved. Just run the app again and continue.

**Q: Can I modify Test_Prompt.md and regenerate?**
A: Yes! The original is in the tests/specs folder. Click Reject and try again.

**Q: Does it work on Windows/Mac/Linux?**
A: Yes! Cross-platform. pyperclip and subprocess work on all OS.

**Q: Can I run this headless (no UI)?**
A: Yes! The copilot_bridge functions work standalone. Import directly in Python:
```python
import copilot_bridge
copilot_bridge.copy_to_clipboard(text)
copilot_bridge.create_test_folders(path)
```

