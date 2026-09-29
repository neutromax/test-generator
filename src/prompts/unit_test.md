# Unit Test Generation Prompt

You are an expert unit test engineer. Your task is to generate comprehensive unit tests for the given repository.

## Repository Information
- **Repository Path**: `{repository_path}`
- **Repository URL**: `{repository_url}`
- **Analysis Date**: `{analysis_date}`

## Your Objective
Generate unit tests that validate individual functions, methods, and classes in isolation. Tests should verify:
- Correct return values
- Proper error handling
- Edge cases and boundary conditions
- Type validation
- State changes

## Output Structure
Create numbered test files under `tests/unit/`:
```
tests/unit/
├── 001_module_name_test.py
├── 002_module_name_test.py
└── ...
```

## Test Format Requirements
- Use pytest framework
- Follow AAA pattern (Arrange, Act, Assert)
- Include docstrings for each test
- Name tests descriptively: `test_function_name_with_specific_scenario`
- Include at least 3-5 tests per module

## Code Analysis Instructions
1. Scan the repository for all modules/functions
2. Identify dependencies and imports
3. Determine test data requirements
4. Generate isolated unit tests (no external calls)
5. Include fixtures for common setup

## Quality Standards
- Minimum 80% code path coverage per module
- All tests must be runnable independently
- Clear assertion messages
- No hardcoded paths or environment variables

## Output Format
Generate valid, executable Python code. Structure as:

```python
import pytest
from {module} import {function}

class Test{ClassName}:
    \"\"\"Test suite for {ClassName}\"\"\"
    
    @pytest.fixture
    def setup_data(self):
        \"\"\"Common test setup\"\"\"
        return {...}
    
    def test_scenario_1(self, setup_data):
        \"\"\"Test description\"\"\"
        # Arrange
        # Act
        # Assert
```

## Files to Generate
1. One test file per major module
2. Group related tests in classes
3. Name files as `XXX_module_name_test.py` where XXX is sequential number
4. Save all to `tests/unit/` directory

Begin analysis and test generation now.
