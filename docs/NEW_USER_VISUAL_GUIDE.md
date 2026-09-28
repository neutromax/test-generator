# 🎯 New User Workflow - Visual Guide

**How a new user sets up and uses the Test Generator**

---

## Complete Setup Flow

```
┌─────────────────────────────────────────────────────────────────┐
│                    NEW USER WORKFLOW                           │
└─────────────────────────────────────────────────────────────────┘

┌─ SETUP PHASE (5 minutes) ─────────────────────────────────────┐
│                                                                 │
│  1. Clone Repository                                           │
│     $ git clone <url>                                          │
│     $ cd test-generator                                        │
│                  │                                             │
│                  ↓                                             │
│  2. Install Python Packages                                    │
│     $ python -m venv venv                                      │
│     $ venv\Scripts\Activate.ps1  (Windows)                     │
│     $ source venv/bin/activate   (macOS/Linux)                │
│     $ pip install -r requirements.txt                          │
│     $ pip install anthropic                                    │
│                  │                                             │
│                  ↓                                             │
│  3. Get API Key from Anthropic                                 │
│     Visit: https://console.anthropic.com/                      │
│     Create new key: sk-ant-xxxxx                               │
│                  │                                             │
│                  ↓                                             │
│  4. Add API Key to Streamlit Secrets                           │
│     File: ~/.streamlit/secrets.toml                            │
│     Content: ANTHROPIC_API_KEY = "sk-ant-xxxxx"               │
│                                                                 │
└─ RUN PHASE (Click & Go) ──────────────────────────────────────┘
│
│  $ python -m streamlit run app.py
│
│  Browser opens: http://localhost:8501
│
└─────────────────────────────────────────────────────────────────┘
```

---

## Usage Flow (After Setup)

```
┌─────────────────────────────────────────────────────────────────┐
│              USING THE TEST GENERATOR APP                      │
└─────────────────────────────────────────────────────────────────┘

User Opens Browser
       │
       ↓
┌──────────────────────────────────┐
│  Streamlit Web Interface         │
│  http://localhost:8501           │
└──────────────────────────────────┘
       │
       ↓
┌──────────────────────────────────┐
│  Enter GitHub URL                │
│  Example:                        │
│  https://github.com/python/cpython
└──────────────────────────────────┘
       │
       ↓
Click "Generate Test Prompt"
       │
       ↓
┌──────────────────────────────────┐
│  Backend Processing:             │
│  1. Clone repository (if needed) │
│  2. Analyze codebase             │
│  3. Generate 9-phase prompt      │
│  4. Display in UI                │
└──────────────────────────────────┘
       │
       ↓
┌──────────────────────────────────┐
│  Review Generated Prompt         │
│  (User reads & approves)         │
└──────────────────────────────────┘
       │
       ├─ NOT SATISFIED
       │  └─ Click "Reject"
       │     └─ Try different repo
       │
       └─ SATISFIED
          └─ Click "Proceed"
             │
             ↓
          ┌────────────────────────┐
          │ Automated Generation:  │
          │ 1. API Call #1:        │
          │    Generate test specs │
          │ 2. API Call #2:        │
          │    Generate test code  │
          │ 3. API Call #3:        │
          │    Coverage analysis   │
          │ 4. Save files to disk  │
          └────────────────────────┘
             │
             ↓
          ┌────────────────────────┐
          │ Results Display:       │
          │ - Specs preview       │
          │ - Code preview        │
          │ - Coverage summary    │
          │ - Download options    │
          └────────────────────────┘
             │
             ├─ Download individual files
             ├─ Download ZIP archive
             └─ Copy to clipboard
```

---

## Security Flow

```
┌─────────────────────────────────────────────────────────────────┐
│              HOW API KEYS ARE PROTECTED                         │
└─────────────────────────────────────────────────────────────────┘

1. User Gets Key
   ├─ Visits: https://console.anthropic.com/
   ├─ Creates new API key
   └─ Copies key: sk-ant-xxxxx

2. User Adds Key to Secrets File
   ├─ File location: ~/.streamlit/secrets.toml
   ├─ Only on their computer
   └─ Protected by OS permissions

3. Streamlit Reads Key
   ├─ Loads secrets.toml on startup
   ├─ Never logs the key
   ├─ Never exposes the key
   └─ Keeps in memory during session

4. App Uses Key
   ├─ Claude API calls made securely
   ├─ Key sent over HTTPS
   ├─ Anthropic servers validate
   └─ Response returned to app

5. Key Never Leaves Computer
   ├─ NOT stored in code
   ├─ NOT committed to git
   ├─ NOT shared on internet
   ├─ NOT visible in browser
   └─ Protected by .gitignore

Security Result:
   ✅ Your API key is safe
   ✅ Project can be shared freely
   ✅ Anyone can clone without security risk
```

---

## API Key Configuration Options

```
┌─────────────────────────────────────────────────────────────────┐
│        WHERE TO STORE YOUR API KEY (Choose 1)                  │
└─────────────────────────────────────────────────────────────────┘

Option 1: STREAMLIT SECRETS (Recommended ⭐)
  ├─ Location: ~/.streamlit/secrets.toml
  ├─ Persistence: Permanent
  ├─ Security: 🔒🔒🔒 High
  ├─ Setup: Create file & add key
  └─ Best for: Production & everyday use

Option 2: ENVIRONMENT VARIABLE
  ├─ Location: $env:ANTHROPIC_API_KEY (Windows)
  ├─ Persistence: Current session only
  ├─ Security: 🔒🔒🔒 High
  ├─ Setup: Set variable before running app
  └─ Best for: Development & temporary use

Option 3: .env FILE (Local)
  ├─ Location: d:\test-generator\.env
  ├─ Persistence: Permanent (local)
  ├─ Security: 🔒🔒 Medium
  ├─ Setup: Create file in project root
  ├─ Important: Must be in .gitignore
  └─ Best for: Local development only
```

---

## Complete Setup Command Reference

### Windows - Copy & Paste

```powershell
# 1. Clone
git clone <repo-url>
cd test-generator

# 2. Setup environment
python -m venv venv
.\venv\Scripts\Activate.ps1

# 3. Install packages
pip install -r requirements.txt
pip install anthropic

# 4. Create secrets folder
mkdir -p $env:USERPROFILE\.streamlit

# 5. Add API key (replace YOUR-KEY-HERE)
@"
ANTHROPIC_API_KEY = "sk-ant-YOUR-KEY-HERE"
"@ | Out-File -FilePath "$env:USERPROFILE\.streamlit\secrets.toml" -Encoding UTF8

# 6. Verify
Get-Content "$env:USERPROFILE\.streamlit\secrets.toml"

# 7. Run
python -m streamlit run app.py
```

### macOS/Linux - Copy & Paste

```bash
# 1. Clone
git clone <repo-url>
cd test-generator

# 2. Setup environment
python3 -m venv venv
source venv/bin/activate

# 3. Install packages
pip install -r requirements.txt
pip install anthropic

# 4. Create secrets folder & file
mkdir -p ~/.streamlit
cat > ~/.streamlit/secrets.toml << EOF
ANTHROPIC_API_KEY = "sk-ant-YOUR-KEY-HERE"
EOF

# 5. Verify
cat ~/.streamlit/secrets.toml

# 6. Run
python -m streamlit run app.py
```

---

## Troubleshooting Quick Reference

| Problem | Solution |
|---------|----------|
| **"API key not configured"** | Add key to `~/.streamlit/secrets.toml` |
| **ModuleNotFoundError: anthropic** | Run `pip install anthropic` |
| **PowerShell execution policy error** | Run `Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser` |
| **Port already in use** | Run `python -m streamlit run app.py --server.port 8502` |
| **Git not found** | Install Git from https://git-scm.com/ |
| **Python not found** | Install Python from https://www.python.org/downloads/ |

---

## What Happens Inside the App

```
┌──────────────────────────────────────────────────────┐
│      WHEN YOU CLICK "PROCEED"                       │
└──────────────────────────────────────────────────────┘

Your API Key (from secrets.toml)
       │
       ↓ Loaded securely by Streamlit
┌──────────────────────────────────────────────────────┐
│              app.py                                 │
│  (Streamlit web interface)                          │
└──────────────────────────────────────────────────────┘
       │
       ↓ Calls
┌──────────────────────────────────────────────────────┐
│         claude_integration.py                       │
│  (API integration layer)                            │
│                                                     │
│  1. generate_specifications()                       │
│     ├─ Uses API key                                 │
│     └─ Creates Claude API call #1                  │
│                                                     │
│  2. generate_test_scripts()                         │
│     ├─ Uses API key                                 │
│     └─ Creates Claude API call #2                  │
│                                                     │
│  3. generate_coverage_analysis()                    │
│     ├─ Uses API key                                 │
│     └─ Creates Claude API call #3                  │
│                                                     │
│  4. Saves all results to disk                       │
└──────────────────────────────────────────────────────┘
       │
       ├─ API Call #1
       │  └─ HTTPS to https://api.anthropic.com/
       │     └─ Returns test specifications
       │
       ├─ API Call #2
       │  └─ HTTPS to https://api.anthropic.com/
       │     └─ Returns test code
       │
       └─ API Call #3
          └─ HTTPS to https://api.anthropic.com/
             └─ Returns coverage analysis
       
All results saved locally to:
├─ tests/specs/
├─ tests/test_scripts/
└─ tests/coverage/

Results displayed in browser for download
```

---

## Key Points for New Users

✅ **Setup takes 5 minutes**
- Clone
- Create venv
- Install packages
- Add API key
- Run app

✅ **API key is safe**
- Only stored on your computer
- Never in code
- Never in git
- Never logged

✅ **No manual copy-paste**
- Enter GitHub URL
- Click "Proceed"
- Automated generation
- Download results

✅ **Free to use**
- Anthropic offers free tier
- Create account at console.anthropic.com
- No credit card required (initially)

❌ **Never do this**
- Don't hardcode your key
- Don't commit .env to git
- Don't share your secrets.toml
- Don't push secrets to GitHub

---

**Ready to get started?** 👉 [NEW_USER_SETUP.md](NEW_USER_SETUP.md)
