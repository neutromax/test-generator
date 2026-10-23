# 🚀 How to Run the Test Generator

**Quick Reference Guide for Running the Application**

---

## ⚡ Quick Start (5 Minutes)

### 1. Navigate to Project Directory
```bash
cd d:\test generator
```

### 2. Install Dependencies (First Time Only)
```bash
python -m pip install -q -r requirements.txt
```

### 3. Run the Application
```bash
python -m streamlit run app.py
```

**Browser opens automatically:** `http://localhost:8501`

---

## 📋 Detailed Instructions by OS

### Windows (PowerShell)

#### Step 1: Open PowerShell
```bash
# Navigate to project
cd "d:\test generator"
```

#### Step 2: Create Virtual Environment (Recommended)
```bash
# Create virtual environment
python -m venv venv

# Activate it
.\venv\Scripts\Activate.ps1
```

#### Step 3: Install Dependencies
```bash
# Install from requirements.txt
pip install -r requirements.txt

# OR install individually
pip install streamlit>=1.32,<2
pip install anthropic  # For API integration
```

#### Step 4: Run the App
```bash
# Default port 8501
python -m streamlit run app.py

# OR custom port
python -m streamlit run app.py --server.port 8502
```

#### Step 5: Stop the App
```bash
# Press Ctrl+C in terminal
```

---

### macOS / Linux (Bash)

#### Step 1: Open Terminal
```bash
# Navigate to project
cd /path/to/test\ generator
```

#### Step 2: Create Virtual Environment (Recommended)
```bash
# Create virtual environment
python3 -m venv venv

# Activate it
source venv/bin/activate
```

#### Step 3: Install Dependencies
```bash
# Install from requirements.txt
pip install -r requirements.txt

# OR install individually
pip install streamlit>=1.32,<2
pip install anthropic  # For API integration
```

#### Step 4: Run the App
```bash
# Default port 8501
python -m streamlit run app.py

# OR custom port
python -m streamlit run app.py --server.port 8502
```

#### Step 5: Stop the App
```bash
# Press Ctrl+C in terminal
```

---

## 🔑 Running with API Key

### Option 1: Environment Variable (Recommended)

**Windows (PowerShell):**
```bash
$env:ANTHROPIC_API_KEY = "sk-ant-your-key-here"
python -m streamlit run app.py
```

**macOS / Linux (Bash):**
```bash
export ANTHROPIC_API_KEY="sk-ant-your-key-here"
python -m streamlit run app.py
```

### Option 2: Streamlit Secrets File

**Create file:** `~/.streamlit/secrets.toml`
```toml
ANTHROPIC_API_KEY = "sk-ant-your-key-here"
OPENAI_API_KEY = "sk-..."  # Optional
```

**Then run:**
```bash
python -m streamlit run app.py
```

### Option 3: .env File

**Create file:** `d:\test generator\.env`
```
ANTHROPIC_API_KEY=sk-ant-your-key-here
OPENAI_API_KEY=sk-...
```

**Then run:**
```bash
python -m streamlit run app.py
```

---

## 🎯 Common Terminal Commands

### Check Python Version
```bash
python --version
```
**Required:** Python 3.9+

### Check pip Version
```bash
python -m pip --version
```

### Upgrade pip
```bash
# Windows
python -m pip install --upgrade pip

# macOS / Linux
python3 -m pip install --upgrade pip
```

### List Installed Packages
```bash
pip list
```

### Check if Streamlit is Installed
```bash
pip show streamlit
```

### Uninstall Package
```bash
pip uninstall streamlit -y
```

### Reinstall Requirements
```bash
pip install -r requirements.txt --force-reinstall
```

---

## 🔧 Advanced Configuration

### Custom Port (If 8501 is Busy)
```bash
python -m streamlit run app.py --server.port 8502
```

### Disable Browser Auto-Open
```bash
python -m streamlit run app.py --logger.level=debug -- --client.showErrorDetails=false
```

### Run in Headless Mode (No Browser)
```bash
python -m streamlit run app.py --server.headless true
```

### Full Configuration
```bash
python -m streamlit run app.py \
  --server.port 8502 \
  --server.headless true \
  --logger.level=info
```

---

## 📊 Running as Backend Service

### Windows Service (Using NSSM)

**Install NSSM:**
```bash
# Download from: https://nssm.cc/download
# Extract and add to PATH
```

**Register Service:**
```bash
nssm install TestGenerator "python -m streamlit run app.py --server.port 8501"
nssm start TestGenerator
```

**Check Status:**
```bash
nssm status TestGenerator
```

**Stop Service:**
```bash
nssm stop TestGenerator
```

### Docker (Optional)

**Build Docker Image:**
```bash
docker build -t test-generator .
```

**Run Container:**
```bash
docker run -p 8501:8501 test-generator
```

---

## 🐛 Troubleshooting

### Port Already in Use
```bash
# Find what's using port 8501
netstat -ano | findstr :8501  # Windows
lsof -i :8501                 # macOS/Linux

# Use different port
python -m streamlit run app.py --server.port 8502
```

### Module Not Found Errors
```bash
# Reinstall dependencies
pip install --force-reinstall -r requirements.txt
```

### Streamlit Cache Issues
```bash
# Clear Streamlit cache
streamlit cache clear

# Run with cache disabled
python -m streamlit run app.py --logger.level=debug
```

### API Key Not Working
```bash
# Check if key is set
echo $env:ANTHROPIC_API_KEY  # Windows
echo $ANTHROPIC_API_KEY      # macOS/Linux

# If not set, see "Running with API Key" section above
```

---

## 🔄 Workflow: Terminal Commands Sequence

### Complete First-Run Setup
```bash
# 1. Navigate to folder
cd d:\test generator

# 2. Create virtual environment
python -m venv venv

# 3. Activate virtual environment
.\venv\Scripts\Activate.ps1  # Windows
# OR
source venv/bin/activate     # macOS/Linux

# 4. Upgrade pip
python -m pip install --upgrade pip

# 5. Install dependencies
pip install -r requirements.txt

# 6. Set API key (optional)
$env:ANTHROPIC_API_KEY = "sk-ant-your-key"

# 7. Run application
python -m streamlit run app.py

# 8. Open browser
# Automatically opens http://localhost:8501
```

### Subsequent Runs (After Setup)
```bash
# 1. Navigate to folder
cd d:\test generator

# 2. Activate virtual environment (if using one)
.\venv\Scripts\Activate.ps1

# 3. Set API key (if using one)
$env:ANTHROPIC_API_KEY = "sk-ant-your-key"

# 4. Run application
python -m streamlit run app.py
```

---

## 📱 Accessing the Application

### Local Access
- **URL:** `http://localhost:8501`
- **Device:** Your computer

### Network Access
- **URL:** `http://<your-ip>:8501`
- **From other machines on same network**
- Run: `ipconfig` (Windows) or `ifconfig` (macOS/Linux) to find your IP

### Example Network Access
```bash
# Find your IP
ipconfig  # Windows

# Access from another machine
http://192.168.1.100:8501
```

---

## ✅ Running Checklist

- [ ] Python 3.9+ installed (`python --version`)
- [ ] Project folder accessible (`cd d:\test generator`)
- [ ] Dependencies installed (`pip list` shows streamlit)
- [ ] Virtual environment activated (if using one)
- [ ] API key set (if using automation)
- [ ] Port 8501 available (or use different port)
- [ ] Ready to run: `python -m streamlit run app.py`

---

## 🆘 Need Help?

| Issue | Solution |
|-------|----------|
| **Port in use** | Use `--server.port 8502` |
| **Module not found** | Run `pip install -r requirements.txt` |
| **API key error** | Set via env var or secrets file |
| **Browser not opening** | Manually go to `http://localhost:8501` |
| **App crashes** | Check terminal for error, see troubleshooting |

---

## 🔗 Related Documentation

- **Full Setup Guide:** [SETUP_AUTOMATION.md](SETUP_AUTOMATION.md)
- **Quick Start:** [QUICKSTART.md](QUICKSTART.md)
- **Deployment:** [DEPLOYMENT_GUIDE.md](DEPLOYMENT_GUIDE.md)
- **Project Analysis:** [PROJECT_ANALYSIS_REPORT.md](PROJECT_ANALYSIS_REPORT.md)

---

## 💾 Save These Commands

**Copy this quick reference:**
```bash
# Quick setup
cd "d:\test generator"
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt

# Set API key (optional)
$env:ANTHROPIC_API_KEY = "your-key-here"

# Run app
python -m streamlit run app.py
```

---

**Ready to run? Copy the commands above and paste into your terminal! 🚀**
