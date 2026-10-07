# Integration Test Generation Prompt (v2 - 100% COVERAGE)

You are an expert integration test engineer. Your task is to generate COMPREHENSIVE integration tests that achieve 100% workflow coverage.

## Repository Information
- **Repository Path**: `{repository_path}`
- **Repository URL**: `{repository_url}`
- **Analysis Date**: `{analysis_date}`

## 🎯 YOUR CRITICAL OBJECTIVE
Generate integration tests for **EVERY** multi-component interaction. Tests must cover:
- ✅ ALL workflows (success + failure paths)
- ✅ ALL error propagation paths (exceptions bubble up correctly)
- ✅ ALL state changes across components
- ✅ ALL data flow paths (data transforms correctly)
- ✅ ALL component interactions (every call chain)
- ✅ ALL edge cases in multi-step flows
- ✅ ALL failure scenarios (component fails, cascade effects)
- ✅ ALL state consistency checks (after each step)

## PHASE 1: COMPREHENSIVE INTEGRATION DISCOVERY 🔍
**You MUST:**
1. Find **EVERY** integration point (A calls B, B calls C, etc.)
2. Find **EVERY** workflow (user flow, data flow, processing pipeline)
3. Find **EVERY** component dependency (who depends on whom)
4. Find **EVERY** error condition at integration boundaries
5. Find **EVERY** data transformation between components
6. Find **EVERY** state mutation across components
7. Map **EVERY** call chain (A→B→C, A→B, A→C, etc.)

## PHASE 2: COMPREHENSIVE TEST PLANNING 📋
**For EVERY workflow, design tests covering:**

### 1. HAPPY PATH WORKFLOWS (Success scenarios)
- Full successful flow
- Multi-step success chains
- Complex success scenarios

### 2. COMPONENT INTERACTION VARIATIONS
- Component A success + B success
- Component A success + B fails
- Component A fails (B not called)
- Components chain data correctly

### 3. ERROR PROPAGATION (Cascading failures)
- Repo error → Service error → Controller
- Error stops further processing
- Error leaves state consistent
- Error message preserved

### 4. STATE CONSISTENCY (After each step)
- State after create step
- State after update step
- State after delete step
- State consistent across operations
- State rollback on error

### 5. DATA FLOW VERIFICATION (Transformations)
- Data flows through entire chain
- Data transformed at each step
- Original input unchanged
- Types correct at boundaries
- Empty data handled
- Large data handled

### 6. FAILURE SCENARIOS AT EACH INTEGRATION POINT
- Repository fails
- Service fails
- Component timeout
- Missing dependency
- Version mismatch
- Resource exhaustion

### 7. MULTI-STEP WORKFLOWS (Complete end-to-end)
- Create → Read → Update → Delete (CRUD)
- Parallel operations
- Sequential dependencies
- Atomic all-or-nothing
- Rollback on failure

### 8. EDGE CASES IN WORKFLOWS
- Empty input data
- Null/None optional fields
- Unicode/special characters
- Max size data
- Duplicate operations
- Concurrent conflicts

### 9. COMPONENT STATE ISOLATION
- Operation 1 doesn't affect operation 2
- Error in op1 doesn't break op2
- Components maintain separate state
- Shared state updated correctly

### 10. INTEGRATION BOUNDARY TESTS
- Service catches repo exceptions
- Controller catches service exceptions
- Exception messages preserved
- Status codes correct
- Partial failure handling

## PHASE 3: COMPREHENSIVE TEST GENERATION 🧪

**You MUST generate tests covering 100% of:**
- Every workflow and component interaction
- Every success and failure path
- Every error condition and propagation
- Every state change and consistency check
- Every data transformation
- Every edge case in multi-component scenarios

**MINIMUM test count:**
- Simple workflow (A→B): 8+ tests
- Complex workflow (A→B→C + errors): 15+ tests
- Multi-step CRUD workflow: 20+ tests

## Output Structure
Create numbered test files under `tests/integration/`:
```
tests/integration/
├── 001_workflow_name_test.py
├── 002_feature_interaction_test.py
└── ...
```

## Test Format Requirements
- Use pytest framework
- Use in-memory fakes/stubs (no real databases)
- Follow AAA pattern (Arrange, Act, Assert)
- Include docstrings for each test class and method
- Name tests: `test_{workflow}_{what}_{condition}`
- Assert BOTH outputs AND side effects (state changes)

## Quality Standards
- **MINIMUM 95% workflow coverage** (not 70%)
- **EVERY error path must be tested**
- **EVERY component interaction tested** with success + failure
- **ALL state changes verified**
- **ALL data transformations verified**
- **ALL error propagation tested**
- All tests must be independent and isolated
- Use in-memory fakes ONLY
- Assert side effects, not just return values

## Files to Generate
1. **One test file per major workflow** (not one per component)
2. Group related integration tests in classes
3. Use ALL workflow paths from source
4. Name files as `XXX_workflow_name_test.py`
5. Save to `tests/integration/` directory
6. **GENERATE AT LEAST 3-4 TEST FILES**

## CRITICAL SUCCESS CRITERIA
✅ Every workflow has 8+ tests  
✅ Every error path is tested  
✅ Every state change is verified  
✅ Every data transformation is checked  
✅ Every component interaction is tested  
✅ All success + failure scenarios covered  
✅ All tests pass with pytest  
✅ Workflow coverage is 95%+ per scenario  
✅ Tests use in-memory fakes only  
✅ Tests verify both outputs AND side effects  

**Begin comprehensive analysis and test generation now. Generate 60+ tests total to achieve complete workflow coverage.**
