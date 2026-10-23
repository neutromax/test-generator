# Test Repositories for Orchestration System

Perfect repositories to test your Master-Worker intelligent test generation system. Each is carefully selected for optimal testing experience.

---

## 🎯 Recommended Test Repositories

### **1. SMALL & QUICK (5-10 min) - Start Here**

#### **Repository: pallets/flask**
```
URL: https://github.com/pallets/flask
Size: Small Python web framework
Best For: Quick validation of orchestration
```

**Why This Repo?**
- Small, well-structured codebase
- Clear module boundaries (app, routing, testing)
- Existing tests to compare against
- Good for all test types (unit, integration, linting)

**Expected Outcomes:**
```
📊 Statistics:
├─ Files to analyze: ~50-100
├─ Estimated execution time: 8-12 minutes
├─ Expected GOLD rate: 80-90%
├─ Cache size: 2-5 MB

Generated Tests:
├─ Unit Tests: 15-20 test cases
├─ Integration Tests: 8-10 test cases
├─ Linting Report: 5-10 style issues
├─ Security Scan: 2-3 potential issues
└─ Total Generated: ~30-50 test cases
```

**Test This:**
```bash
# Start Ollama
ollama serve

# In another terminal
streamlit run app.py

# Enter URL: https://github.com/pallets/flask
# Select: ☑ Unit Tests, ☑ Linting, ☑ Security
# Click: Run Test Orchestration
```

**Expected Quality:**
- Flask is well-written → GOLD rate should be high (85%+)
- Simple patterns → Qwen handles well
- Good learning example

---

#### **Repository: psf/requests**
```
URL: https://github.com/psf/requests
Size: HTTP library for Python
Best For: Testing integration & API tests
```

**Why This Repo?**
- Popular, production-grade code
- Good for API test generation
- Clear responsibilities per module
- Medium complexity (not too hard)

**Expected Outcomes:**
```
📊 Statistics:
├─ Files to analyze: ~100-150
├─ Estimated execution time: 10-15 minutes
├─ Expected GOLD rate: 75-85%
├─ Cache size: 3-7 MB

Generated Tests:
├─ Unit Tests: 20-25 test cases
├─ API Tests: 15-20 test cases
├─ Integration: 10-15 test cases
├─ Security: 5-8 vulnerabilities
└─ Total Generated: ~50-70 test cases
```

**What to Expect:**
- Good for HTTP mocking examples
- Qwen generates clean code
- Llama catches security issues well

---

### **2. MEDIUM (15-25 min) - Best Learning**

#### **Repository: django/django**
```
URL: https://github.com/django/django
Size: Full web framework (medium-large)
Best For: Complex dependencies, full workflow
```

**Why This Repo?**
- Enterprise-grade code quality
- Tests everything: views, models, forms, auth
- Real-world complexity
- Great for testing cascade invalidation

**Expected Outcomes:**
```
📊 Statistics:
├─ Files to analyze: 500-1000
├─ Estimated execution time: 20-30 minutes
├─ Expected GOLD rate: 70-80%
├─ Cache size: 10-20 MB

Generated Tests:
├─ Unit Tests: 50-100 test cases
├─ Integration Tests: 30-50 test cases
├─ E2E Tests: 15-25 test cases
├─ Security Tests: 20-30 vulnerabilities
├─ Linting Issues: 50-100 issues
└─ Total Generated: 150-300 test cases
```

**Advanced Testing:**
```
This repo will test:
✓ Complex dependency chains
✓ Performance of topological sort
✓ Batch parallelization (multiple workers)
✓ Cascade invalidation (if auth tests fail)
✓ Master validation on complex code
✓ Cache persistence across sessions
```

**Real-World Scenario:**
- First run: 25-30 minutes (all fresh)
- Second run (same code): 2-3 minutes (70% cache hits)
- Third run (code changed): 15-20 minutes (selective regeneration)

---

#### **Repository: numpy/numpy**
```
URL: https://github.com/numpy/numpy
Size: Large numerical computing library
Best For: Testing performance at scale
```

**Why This Repo?**
- Large codebase (stress test)
- Mix of Python and C (complex analysis)
- Scientific computing patterns
- Good for security scanning

**Expected Outcomes:**
```
📊 Statistics:
├─ Files to analyze: 1000+
├─ Estimated execution time: 30-45 minutes
├─ Expected GOLD rate: 60-70% (complex code)
├─ Cache size: 20-50 MB

Generated Tests:
├─ Unit Tests: 100-200
├─ Integration: 50-100
├─ Performance: 20-30
├─ Security: 30-50
└─ Total Generated: 200-400+ test cases
```

**Performance Testing:**
- Stress test the orchestrator
- Test memory usage (multiple models loaded)
- Validate cache performance at scale
- Check batch execution optimization

---

### **3. SPECIALTY (For Specific Features)**

#### **Repository: redis/redis-py**
```
URL: https://github.com/redis/redis-py
Size: Redis Python client
Best For: Testing cache interactions
```

**Why This Repo?**
- Perfect for cache-related tests
- Good API design (easy to understand)
- Clear test patterns already exist
- Fast analysis (medium size)

**What to Test:**
- Cache hits on repeated runs
- Performance tracking (same code = cache reuse)
- Validate cache invalidation (code changes)

---

#### **Repository: kubernetes/kubernetes**
```
URL: https://github.com/kubernetes/kubernetes
Size: VERY LARGE (skip unless you want stress test)
Best For: Advanced stress testing
```

**Why This Repo?**
- Massive codebase (extreme test)
- Real security concerns (penetration testing)
- Complex architecture (great for analysis)
- Go code (test multilingual prompt generation)

**Warning:** This will take 45-90 minutes first run
**Benefit:** Tests system limits and performance

---

### **4. QUICK WINS (2-5 min) - Validation Only**

#### **Repository: minimal/cli-tool**
```
URL: https://github.com/cli/cli
Size: GitHub CLI
Best For: Fast validation
```

**Why This Repo?**
- Small to medium
- Well-structured
- Quick analysis
- Fast results to see if everything works

---

## 🧪 Testing Strategy

### **Phase 1: Validation (30 min)**
Start with Flask or Requests
```
Goal: Verify orchestration works end-to-end
✓ Ollama connected
✓ Models loaded
✓ Task queue builds
✓ Batches execute
✓ Cache stores
✓ Results display
```

### **Phase 2: Quality Assessment (45 min)**
Use Django or Flask
```
Goal: Check test quality metrics
✓ GOLD rate > 70%?
✓ SILVER corrections working?
✓ BRONZE fallbacks functional?
✓ Master validation effective?
✓ Model performance tracking accurate?
```

### **Phase 3: Performance Testing (1-2 hours)**
Use numpy or Kubernetes
```
Goal: Stress test the system
✓ Parallel execution efficient?
✓ Topological sort correct?
✓ Cache at scale working?
✓ Memory usage acceptable?
✓ Cascade invalidation proper?
```

### **Phase 4: Cache Validation (15 min)**
Run same repo twice
```
Goal: Verify caching system
First Run:  All fresh (generate everything)
Second Run: 70-80% cache hits (very fast)
Third Run:  With changes (selective regeneration)
```

---

## 📊 Testing Matrix

| Repo | Size | Time | GOLD% | Best For | Difficulty |
|------|------|------|-------|----------|-----------|
| Flask | XS | 8-12m | 85%+ | Start here | ⭐ |
| Requests | S | 10-15m | 80%+ | API tests | ⭐⭐ |
| CLI | S | 5-8m | 85%+ | Quick test | ⭐ |
| Django | M | 20-30m | 75%+ | Complex deps | ⭐⭐⭐ |
| Numpy | L | 30-45m | 65%+ | Stress test | ⭐⭐⭐⭐ |
| Kubernetes | XL | 60-90m | 50%+ | Extreme test | ⭐⭐⭐⭐⭐ |

---

## 🚀 Quick Start Commands

### **Setup (One Time)**

```bash
# Terminal 1: Start Ollama
ollama serve

# Terminal 2: Load models
ollama pull llama3.1:8b
ollama pull qwen2.5-coder:7b
ollama pull gemma3

# Terminal 3: Run app
cd "d:\test generator"
streamlit run app.py
```

### **Test Run 1: Flask (Quickest)**

```
Browser: http://localhost:8501
Input URL: https://github.com/pallets/flask

Select Test Types:
  ☑ Unit Tests
  ☑ Integration Tests
  ☑ Linting
  ☑ Security

Click: Run Test Orchestration

Expected Result:
  ✅ Complete in ~10-15 minutes
  ✅ GOLD rate ~85-90%
  ✅ ~30-50 test cases generated
```

### **Test Run 2: Requests (Good Balance)**

```
Input URL: https://github.com/psf/requests

Select Test Types:
  ☑ Unit Tests
  ☑ Integration Tests
  ☑ API Tests
  ☑ Security

Expected Result:
  ✅ Complete in ~15-20 minutes
  ✅ GOLD rate ~80-85%
  ✅ ~50-70 test cases generated
```

### **Test Run 3: Django (Advanced)**

```
Input URL: https://github.com/django/django

Select Test Types:
  ☑ Unit Tests
  ☑ Integration Tests
  ☑ E2E Tests
  ☑ Security
  ☑ Linting

Expected Result:
  ✅ Complete in ~25-35 minutes
  ✅ GOLD rate ~70-75%
  ✅ SILVER corrections: 10-20%
  ✅ 150-300 test cases generated
```

---

## 📈 What to Monitor During Testing

### **Metrics to Track:**

```
1. EXECUTION TIME
   ├─ Overall: Should decrease 50% on 2nd run (cache)
   ├─ Per batch: Should be ~3-4s for parallel execution
   └─ Individual task: 2-3s per task

2. QUALITY (GOLD/SILVER/BRONZE)
   ├─ GOLD rate: Should be 70-90%
   ├─ SILVER rate: 5-20% (corrections working?)
   └─ BRONZE rate: <5% (actual failures)

3. MODEL PERFORMANCE
   ├─ Qwen avg time: 2000-2500ms
   ├─ Llama avg time: 3000-4000ms
   ├─ Gemma avg time: 2500-3500ms
   └─ Success rate: Should be >95% for all

4. CACHE HIT RATE
   ├─ First run: 0% (all fresh)
   ├─ Second run: 70-80% (most cached)
   └─ Third run (code changed): 10-30% (selective)

5. RESOURCE USAGE
   ├─ Memory: 15-20GB (3 models loaded)
   ├─ GPU VRAM: Full utilization of GPU memory
   └─ Disk: Cache database growth <100MB
```

### **Flags to Look For:**

```
❌ GOLD rate < 50%
   → Prompt might need improvement
   → Model selection might be wrong
   → Try different model

❌ SILVER rate > 30%
   → Many minor fixes needed
   → Prompts are unclear
   → Validation thresholds too strict

❌ Any BRONZE errors
   → Something went wrong
   → Check error messages
   → May need prompt engineering

❌ Slow execution (>60s per task)
   → Model might be overloaded
   → Check GPU memory
   → Try different model

❌ Cache hit rate = 0% on 2nd run
   → Cache not working (bug)
   → Check SQLite database
   → Verify repo hash logic
```

---

## 💡 Pro Tips for Testing

### **Tip 1: Start Small**
```
Don't jump to Kubernetes first!
Flask → Requests → Django → Numpy → Kubernetes
Each level validates different capabilities
```

### **Tip 2: Check Each Phase**
```
Phase 1: Can I run it end-to-end?
Phase 2: Is quality acceptable (>70% GOLD)?
Phase 3: Is it fast (parallel working)?
Phase 4: Is caching persistent (2nd run faster)?
```

### **Tip 3: Monitor Logs**
```
Streamlit terminal will show:
- Model selection decisions
- Cache hits/misses
- Validation levels
- Error messages
- Performance metrics

Watch for patterns in what works/fails
```

### **Tip 4: Test Cache System Specifically**
```
Run 1: Flask (generates cache)
  → Check: /project/.cache/test_results.db exists

Run 2: Flask again (same code)
  → Should show "Using cached result"
  → Should be 10x faster
  → Check cache hit rate in report

Modify code in /repo/src/file.py
Run 3: Flask again (code changed)
  → Some cache hits, some misses
  → Selective regeneration
  → Smart invalidation working!
```

### **Tip 5: Validate Master Validation**
```
Look for SILVER level outputs:
  "⚠️  {task_id}: Needs correction..."
  
Check if:
  ✓ Correction prompt generated
  ✓ Retry with same model
  ✓ Fixed output now GOLD
  ✓ Result cached
  
This validates Master supervision working!
```

---

## 📋 Test Checklist

```
Before You Start:
  ☐ Ollama running (ollama serve)
  ☐ 3 models loaded (llama3.1, qwen, gemma3)
  ☐ Streamlit running (streamlit run app.py)
  ☐ Browser at localhost:8501

Test 1: Flask (Validation)
  ☐ Repo clones successfully
  ☐ Test types selectable
  ☐ Orchestration starts
  ☐ Batch 1 shows progress
  ☐ Models assigned correctly
  ☐ Results displayed
  ☐ Reports generated
  ☐ Cache file created

Test 2: Requests (Quality)
  ☐ GOLD rate > 70%
  ☐ SILVER corrections working
  ☐ No BRONZE failures
  ☐ Report shows metrics
  ☐ Tests are valid code

Test 3: Django (Performance)
  ☐ Multiple batches execute
  ☐ Parallel execution visible
  ☐ Total time < 30 min
  ☐ Cascade invalidation working
  ☐ Large test set generated

Test 4: Cache (Persistence)
  ☐ Second run faster (10x+)
  ☐ Cache hit rate > 70%
  ☐ Database file growing
  ☐ 7-day expiration working

Test 5: Error Handling
  ☐ If Ollama stops: error message
  ☐ If model unavailable: fallback works
  ☐ If validation fails: proper status
  ☐ If cache corrupted: regenerates
```

---

## 🎓 What You'll Learn

**From Flask Test:**
- Basic orchestration flow
- Model selection
- Parallel batch execution
- Cache initialization
- Report generation

**From Requests Test:**
- Quality validation (GOLD/SILVER/BRONZE)
- Error correction mechanism
- Model performance tracking
- API test generation patterns

**From Django Test:**
- Complex dependency chains
- Cascade invalidation
- Large-scale cache management
- Performance optimization
- Advanced topological sorting

**From Numpy Test:**
- System stress limits
- Memory management
- Performance at scale
- Batch size optimization
- GPU utilization patterns

---

## 📞 Troubleshooting

**Issue: "Ollama server not running"**
```
Solution:
1. Start Ollama: ollama serve
2. Wait 5 seconds for startup
3. Check: http://localhost:11434/api/health
4. Should return {"status":"ok"}
```

**Issue: "Model not found: qwen2.5-coder:7b"**
```
Solution:
1. ollama pull qwen2.5-coder:7b
2. ollama pull llama3.1:8b
3. ollama pull gemma3
4. Wait for downloads (5-10 min each)
```

**Issue: "No available model for task"**
```
Solution:
1. Check: ollama list (in Ollama terminal)
2. Should show 3 loaded models
3. If not, pull them again
4. Verify status column says "6.5 GB" (loaded)
```

**Issue: "Streamlit connection refused"**
```
Solution:
1. Check: Port 8501 not blocked
2. Restart: streamlit run app.py
3. Clear browser cache
4. Try incognito window
5. Check: http://localhost:8501 directly
```

**Issue: "Generated tests have errors"**
```
Solution:
1. Check Master validation (should catch these)
2. Look at GOLD rate (should be >70%)
3. If BRONZE rate high: try different model
4. If SILVER rate high: improve prompt
5. Check logs for validation details
```

---

## 🏁 Success Criteria

Your orchestration system is working perfectly when:

```
✅ Flask Test:
   • Completes in <15 minutes
   • GOLD rate 85%+
   • No syntax errors in generated tests
   • Cache file created

✅ Requests Test:
   • GOLD rate 80%+
   • SILVER corrections working
   • Reports detailed and accurate
   • Models performing as expected

✅ Django Test:
   • GOLD rate 70%+
   • Complex dependencies handled
   • Cascade invalidation working
   • Performance acceptable

✅ Cache Test:
   • 2nd run 10x faster
   • Cache hit rate 70%+
   • Database persisting correctly
   • Invalidation on code changes

✅ Error Handling:
   • Fallback models work
   • Graceful degradation
   • Clear error messages
   • Recovery mechanisms functional
```

---

## 🚀 Ready to Test!

You now have:
✅ 6 real repositories to test
✅ Expected outcomes for each
✅ Testing strategy (4 phases)
✅ Metrics to monitor
✅ Pro tips for success
✅ Complete checklist
✅ Troubleshooting guide

**Start with Flask, graduate to Django, stress-test with Numpy!**

Good luck! 🎯

