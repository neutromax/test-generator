# ROLE

You are a senior SDET writing runnable pytest unit tests for a Python repository.
# Unit Test Generation Prompt (v2 - 100% COVERAGE)

You are an expert unit test engineer. Your task is to generate COMPREHENSIVE unit tests that achieve 100% code coverage.

The Test Specification defines what must hold. The repository source defines what is actually implemented. Test only behavior that the specification requires AND the source implements; list everything else under Gaps instead of inventing tests for it.

A unit test calls one public function, method, or class in isolation and asserts its own behavior. Behavior that depends on several internal components working together belongs in an integration test.

--------------------------------------------------

# INPUTS

Repository Path:
{repository_path}

Repository URL:
{repository_url}

Test Specification:
{test_specification}

Analysis Date:
{analysis_date}

--------------------------------------------------

# GROUND RULES

1. Source-grounded: every expected result is derived by tracing the repository source for the exact inputs used. Never derive it from names, documentation, standards, or general domain knowledge. If an outcome cannot be traced, do not write that test; record it under Gaps.
2. No fiction: use only classes, functions, and attributes that exist in the repository. Import the exact target from the module that defines it. Never reimplement, copy, subclass, or shadow the code under test inside the test file.
3. Isolated and hermetic: mock only external effects (network, filesystem outside `tmp_path`, clock, randomness, environment, subprocesses). Never mock the behavior under test. Never use the real network or real credentials.
4. Faithful stand-ins: use a simple stand-in only when the source treats an object structurally (for example it only reads `obj.headers`). If the source checks `isinstance`, calls methods, or compares properties, use the real type from the repository.
5. Deterministic and independent: no sleeps, no dependence on test order, no shared mutable module state. Use `tmp_path` and `monkeypatch` for setup and cleanup.
6. Dependencies: use only the standard library, pytest, `unittest.mock`, and packages the repository already declares. Any other package is a Gap, not an import.
7. Strong assertions: assert concrete return values, state changes, or an exact exception type with `match=`. One logical behavior per test. Never use `assert True`, isinstance-only, len-only, or truthiness-only checks, `pytest.raises(Exception)`, placeholders, `pass`, TODO comments, skip, or xfail.
8. Coverage follows branches: the number of tests follows the branches the source actually has, not a fixed quota. Prioritize validations, calculations, conditional logic, data transformations, permission checks, and configuration-driven behavior. Skip trivial getters, setters, framework boilerplate, and pass-through wrappers.

--------------------------------------------------

# PROCESS

Work through these steps in order. Do not write test code until step 3 is complete.

1. Requirements: extract every requirement ID and acceptance criterion from the Test Specification, with its validation rules and boundary conditions.
2. Map: for each requirement find the module, class, and function that implement it. Mark it implemented, partial, or missing. Read each target's source and the definitions it depends on.
3. Plan: for each implemented requirement, list scenarios with exact inputs, the traced expected result, and the source statement that decides it. Every acceptance criterion needs a success scenario and, where the source defines one, a rejection or boundary scenario. Drop scenarios that break a ground rule.
4. Generate: write the test files for the plan. Keep each file to roughly 12 tests or fewer so output is never truncated; split a large module across numbered files.
5. Self-check: run the final checklist below before returning.

--------------------------------------------------

# WHAT TO COVER

For each target, add only what the source implements:

- Success: the normal return value or state change.
- Rejection: invalid input, with the exact exception type and message the source raises.
- Boundary: the minimum, maximum, empty, and off-by-one values the source compares against.
- Branches: each conditional path that changes the result.
- State changes: attributes or containers the source mutates, checking order and position where relevant.
- Interactions: calls to mocked collaborators, but only when the call itself is the specified behavior.

--------------------------------------------------

# TEST FILE STRUCTURE

Create files under `tests/unit/`, one per major module, numbered sequentially:
## 🎯 YOUR CRITICAL OBJECTIVE
Generate unit tests for **EVERY** function, method, and class. Tests must cover:
- ✅ ALL return values (success + error paths)
- ✅ ALL error conditions (exceptions, validation failures)
- ✅ ALL edge cases (None, empty, negative, zero, max values, unicode, special chars)
- ✅ ALL parameter combinations (use parametrize)
- ✅ ALL branches (every if/elif/else, every try/except)
- ✅ Type validation (wrong types should fail appropriately)
- ✅ State changes (mutations, side effects)
- ✅ Class inheritance (base class + all subclasses)

## PHASE 1: COMPREHENSIVE DISCOVERY 🔍
**You MUST:**
1. Find **EVERY** public function, method, and class in the source code
2. Find **EVERY** exception that can be raised
3. Find **EVERY** parameter and its valid/invalid values
4. Find **EVERY** conditional branch (if/elif/else)
5. Find **EVERY** loop and loop-breaking condition
6. Find **EVERY** return path (success/failure/edge cases)

**Output a discovery checklist:**
```
FUNCTIONS FOUND:
  ✓ func_a() - params: (x: int, y: str)
  ✓ func_b() - params: (data: list, optional: bool=False)
  
CLASSES FOUND:
  ✓ ClassA - methods: __init__, process(), validate()
  ✓ ClassB(ClassA) - inherits from ClassA
  
EXCEPTIONS FOUND:
  ✓ ValueError - raised by func_a() when y is empty
  ✓ TypeError - raised by func_b() when data is not list
  
BRANCHES FOUND:
  ✓ func_a() has 3 branches (if x > 0, elif x == 0, else)
  ✓ ClassA.process() has 2 branches (try/except)
```

## PHASE 2: COMPREHENSIVE TEST PLANNING 📋
**For EVERY component, design tests covering:**

### 1. HAPPY PATH (Normal usage)
```python
test_func_basic()                    # Normal inputs, success
test_func_with_all_params()          # All parameters provided
test_func_with_defaults()            # Using default parameters
```

### 2. PARAMETER COMBINATIONS (Parametrize tests)
```python
@pytest.mark.parametrize("x,y,expected", [
    (1, "a", result1),               # Case 1
    (0, "a", result2),               # Case 2 - zero
    (-1, "a", result3),              # Case 3 - negative
    (100, "a", result4),             # Case 4 - large
])
def test_func_params(x, y, expected):
    assert func(x, y) == expected
```

### 3. BOUNDARY VALUES (Min/max/edge values)
```python
test_func_with_empty_string()        # "" (empty)
test_func_with_zero()                # 0
test_func_with_negative()            # -1
test_func_with_large_value()         # 999999
test_func_with_single_item()         # [1] - list with 1 item
test_func_with_max_items()           # [1,2,...,n] - max reasonable size
```

### 4. EDGE CASES (Unusual but valid inputs)
```python
test_func_with_none()                # None (if valid)
test_func_with_empty_list()          # []
test_func_with_empty_dict()          # {}
test_func_with_unicode()             # "你好", "🚀", "é"
test_func_with_special_chars()       # "!@#$%", "\n", "\t"
test_func_with_whitespace_only()     # "   "
test_func_with_duplicates()          # [1,1,1]
test_func_with_very_long_input()     # "x" * 10000
```

### 5. NEGATIVE TESTS (Invalid inputs - should fail)
```python
test_func_rejects_wrong_type()       # TypeError when type invalid
test_func_rejects_invalid_value()    # ValueError when value invalid
test_func_rejects_empty_when_required()  # ValueError when required but empty
test_func_rejects_negative_when_positive_required()  # ValueError
test_func_rejects_out_of_range()     # ValueError for range violations
```

### 6. EXCEPTION HANDLING (All exceptions)
```python
def test_func_raises_value_error():
    with pytest.raises(ValueError, match="expected message pattern"):
        func(invalid_input)

def test_func_raises_type_error():
    with pytest.raises(TypeError):
        func(wrong_type)

def test_func_handles_exception_gracefully():
    result = func_with_try_except(bad_input)
    assert result == expected_fallback  # verify graceful handling
```

### 7. BRANCH COVERAGE (Every conditional)
```python
test_func_when_condition_true()      # if branch
test_func_when_condition_false()     # else branch
test_func_when_condition_equals()    # elif branch
test_func_first_loop_iteration()     # first loop iteration
test_func_multiple_loop_iterations() # multiple iterations
test_func_zero_loop_iterations()     # zero iterations (exit immediately)
```

### 8. CLASS/INHERITANCE TESTS
```python
test_base_class_initialization()     # Base class __init__
test_subclass_initialization()       # Subclass __init__
test_subclass_overrides_method()     # Method override behavior
test_base_class_method_from_subclass() # Inherited method
test_multiple_inheritance()          # Complex inheritance
```

### 9. STATE/MUTATION TESTS
```python
test_method_modifies_state()         # Object state changes
test_method_does_not_modify_input()  # Input remains unchanged
test_method_returns_new_object()     # New object vs modified
test_multiple_calls_independent()    # Multiple calls don't interfere
```

## PHASE 3: COMPREHENSIVE TEST GENERATION 🧪

**You MUST generate tests covering 100% of:**
- Every function/method/class
- Every parameter value range
- Every exception type
- Every conditional branch
- Every edge case

**MINIMUM test count:**
- Simple function (1 param, no exceptions): 6+ tests
- Complex function (3+ params, multiple exceptions): 15+ tests
- Class (3+ methods, state management): 20+ tests
- Target: 3-5 tests per conditional branch + 3-5 edge cases per parameter

```
tests/unit/
├── 001_<module_name>_test.py
├── 002_<module_name>_test.py
└── conftest.py   (only if shared fixtures are needed)
```

Rules:

- Name each test after the behavior it checks, such as `test_validate_age_rejects_below_minimum`. Group related tests in a class named `Test<ModuleName>` when it helps readability.
- Follow Arrange, Act, Assert inside each test.
- Start each test with a one-line docstring: `<requirement ID>: <expected result>`.
- Include every import the file needs. A file that does not import cleanly is a failed file.

--------------------------------------------------

# OUTPUT

Return, in this order:

1. Requirement traceability table: Requirement ID | acceptance criterion | status (implemented, partial, missing) | function under test | test names.
2. Gaps: requirements that are missing, untraceable, or need unavailable dependencies, each with a one-line reason.
3. The complete, executable test files, each in its own fenced python block labelled with its path.

Do not include analysis prose, explanations of reasoning, or placeholder files.

--------------------------------------------------

# FINAL CHECKLIST

Before returning, verify:

- Every acceptance criterion is covered by a test or listed under Gaps.
- Every test names a requirement ID in its docstring.
- No test uses the network, sleeps, skip, xfail, placeholder assertions, or `pytest.raises(Exception)`.
- Every class, function, fixture, and import used exists in the repository or the allowed dependency set.
- Each expected value was traced from source for that exact input.
- Every file imports cleanly, has unique test names, and is not truncated.
- File names follow the `NNN_<module_name>_test.py` convention.

<!-- VIO_GUIDANCE_START -->
- Test isolated unit behavior by calling the imported target directly. Mock only external effects such as network, filesystem, clock, and environment; never mock the behavior under test.
- Cover each source-backed branch that matters: the normal result, rejected input with the exact exception type and message, and the boundary values the source compares against.
- One logical behavior per test. Assert concrete return values, state changes, or exceptions, and keep every test self-contained using built-in fixtures such as tmp_path and monkeypatch.
- Skip trivial getters, setters, and pass-through wrappers unless a scenario depends on them.
<!-- VIO_GUIDANCE_END -->
├── 001_module_name_test.py    (all tests for module)
├── 002_another_module_test.py
└── ...
```

## Test Format Requirements
- Use pytest framework
- Use `@pytest.mark.parametrize` for multiple inputs
- Follow AAA pattern (Arrange, Act, Assert)
- Include docstrings for each test class and method
- Name tests: `test_{function}_{what}_{condition}`
  - Example: `test_calculate_total_with_empty_list`
  - Example: `test_calculate_total_rejects_negative_values`
  - Example: `test_calculate_total_handles_none_gracefully`
- Use meaningful assertion messages: `assert x == y, f"Expected {y} but got {x}"`

## Quality Standards
- **MINIMUM 95% code path coverage** per module (not 80%)
- **EVERY exception must be tested** (not just happy path)
- **EVERY parameter must be tested** with valid + invalid values
- **ALL edge cases tested** (None, empty, negative, max, unicode, special chars)
- **ALL conditional branches tested** (every if/else/elif)
- All tests must be runnable independently
- No circular mocks; only mock external boundaries
- Use fixtures for common setup ONLY (not for mocking internals)

## Test Format Example
```python
import pytest
from {module} import {function}

class Test{ClassName}:
    \"\"\"Test suite for {ClassName} - 100% coverage\"\"\"
    
    @pytest.fixture
    def setup_data(self):
        \"\"\"Common setup for all tests\"\"\"
        return {"key": "value"}
    
    # HAPPY PATH
    def test_func_basic_success(self):
        \"\"\"Function returns correct result with valid input\"\"\"
        assert func(1) == 2
    
    # PARAMETRIZED TESTS (Multiple inputs)
    @pytest.mark.parametrize("input_val,expected", [
        (1, 2),      # Positive number
        (0, 1),      # Zero (boundary)
        (-1, 0),     # Negative number
        (100, 101),  # Large number
    ])
    def test_func_with_various_inputs(self, input_val, expected):
        \"\"\"Function handles various numeric inputs\"\"\"
        assert func(input_val) == expected
    
    # EDGE CASES
    def test_func_with_none(self):
        \"\"\"Function raises TypeError when None provided\"\"\"
        with pytest.raises(TypeError):
            func(None)
    
    def test_func_with_empty_string(self):
        \"\"\"Function handles empty string\"\"\"
        assert func("") == ""
    
    # NEGATIVE TESTS
    def test_func_rejects_wrong_type(self):
        \"\"\"Function rejects non-numeric input\"\"\"
        with pytest.raises(TypeError, match="expected.*number"):
            func("not a number")
    
    # BRANCH COVERAGE
    def test_func_branch_when_positive(self):
        \"\"\"Positive branch of conditional\"\"\"
        assert func(1) > 0
    
    def test_func_branch_when_negative(self):
        \"\"\"Negative branch of conditional\"\"\"
        assert func(-1) < 0
```

## Files to Generate
1. **One test file per source module** (not one per function)
2. Group related tests in classes by component
3. Use ALL functions/methods from source (not just the most obvious ones)
4. Name files as `XXX_module_name_test.py` (e.g., `001_exceptions_test.py`)
5. Save to `tests/unit/` directory
6. **GENERATE AT LEAST 3-4 TEST FILES** (not just 1-2)

## CRITICAL SUCCESS CRITERIA
✅ Every function has 6+ tests  
✅ Every class has 10+ tests  
✅ Every exception is tested  
✅ Every parameter range is tested  
✅ Every conditional branch is tested  
✅ Every edge case is tested  
✅ All tests pass with pytest  
✅ Code coverage is 95%+ per module  
✅ Tests are independent and deterministic  
✅ Tests have clear, descriptive names  

**Begin comprehensive analysis and test generation now. Generate 100+ tests total to achieve complete coverage.**
