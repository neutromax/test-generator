# Test Generator

This tool prepares a repository-specific prompt for GitHub Copilot's enterprise test-generation workflow.

## 🚀 Quick Start for New Users

**New to this project?** Follow our complete setup guide:

👉 **[NEW_USER_SETUP.md](docs/NEW_USER_SETUP.md)** — Step-by-step guide to set up and run the app (5 minutes)

This covers:
- Installing Python and dependencies
- Getting an Anthropic API key
- Configuring secure API storage
- Running the web interface

---

## Requirements

- Python 3.9 or newer
- Git installed and available on `PATH`

## Usage

From this folder, run:

```powershell
python .\generate_test_prompt.py
```

The tool will prompt:

```text
Repository URL:
```

Enter the GitHub repository link when prompted. The URL can also be supplied as a command-line argument for automation:

```powershell
python .\generate_test_prompt.py <repo-url>
```

The tool will:

1. Create `repositories\` when needed.
2. Reuse `repositories\repository` if it is already a Git repository.
3. Clone the repository when it is not present.
4. Generate `Test_Prompt.md` in this tool folder.

Paste `Test_Prompt.md` into Copilot. Copilot must stop after generating specifications and wait for explicit human approval before creating test scripts.

## Options

```powershell
python .\generate_test_prompt.py <repo-url> --clone-root <folder> --output <prompt-file>
```

If the derived repository folder exists but is not a Git repository, the tool stops instead of overwriting it.

## Streamlit Web Interface (Recommended) ✨

The Streamlit web interface provides an automated workflow with:
- 🖥️ **Web UI** for easy interaction
- ⚡ **Automated workflow** - no manual copy-paste needed
- 🔑 **Secure API integration** with Anthropic Claude
- 📊 **Result visualization** with downloadable files
- ✅ **Approval gates** for quality control

### Setup

Install the UI dependency and start the dashboard from this folder:

```powershell
python -m pip install -r .\requirements.txt
pip install anthropic
python -m streamlit run .\app.py
```

### Configure Your API Key

**First time setup?** Before running the app, add your Anthropic API key:

**Windows:**
```powershell
notepad "$env:USERPROFILE\.streamlit\secrets.toml"
# Add this line:
# ANTHROPIC_API_KEY = "sk-ant-your-key-here"
```

**macOS/Linux:**
```bash
nano ~/.streamlit/secrets.toml
# Add this line:
# ANTHROPIC_API_KEY = "sk-ant-your-key-here"
```

Get your free API key here: https://console.anthropic.com/

The dashboard opens in your browser at `http://localhost:8501`. Enter a GitHub repository URL, review the generated prompt, then click **Proceed** to automatically generate test specifications, scripts, and coverage analysis.

---

## Command-Line Interface (Original)