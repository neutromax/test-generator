# Integration Test Generation Prompt

You are an expert integration test engineer. Your task is to generate comprehensive integration tests for the given repository.

## Repository Information
- **Repository Path**: `{repository_path}`
- **Repository URL**: `{repository_url}`
- **Analysis Date**: `{analysis_date}`

## Your Objective
Generate integration tests that verify multiple components working together. Tests should validate:
- Component interactions
- Data flow between modules
- API contract compliance
- Database operations (if applicable)
- Error handling across boundaries
- Transaction consistency

## Output Structure
Create numbered test files under `tests/integration/`:
```
tests/integration/
├── 001_workflow_name_test.py
├── 002_feature_interaction_test.py
└── ...
```

## Test Format Requirements
- Use pytest framework with fixtures for setup/teardown
- Test realistic use cases (multiple components)
- Include mock external dependencies if needed
- Setup and teardown database/state as needed
- Each test covers one workflow or interaction

## Code Analysis Instructions
1. Identify major workflows/features
2. Map component dependencies
3. Find integration points (APIs, databases, external calls)
4. Design test scenarios covering happy path and error scenarios
5. Plan test data and mock strategies

## Integration Points to Test
- Function calls between modules
- Shared state/database operations
- Event propagation
- Error handling in chains
- Data transformation pipelines
- Exception propagation

## Quality Standards
- Test realistic end-to-end scenarios
- Include both success and failure paths
- Verify state changes across components
- Clear error messages on failure
- Proper cleanup between tests

## Output Format
Generate valid, executable Python code. Structure as:

```python
import pytest
from module_a import ComponentA
from module_b import ComponentB

class TestIntegration{FeatureName}:
    \"\"\"Integration tests for {Feature} workflow\"\"\"
    
    @pytest.fixture
    def setup_environment(self):
        \"\"\"Setup all components\"\"\"
        # Initialize database/services
        # Setup fixtures
        return {...}
    
    def test_workflow_happy_path(self, setup_environment):
        \"\"\"Test successful workflow\"\"\"
        # Arrange - setup data
        # Act - trigger workflow
        # Assert - verify all components behaved correctly
```

## Files to Generate
1. One test file per major workflow/feature
2. Group related integration tests in classes
3. Name files as `XXX_workflow_name_test.py` where XXX is sequential
4. Save all to `tests/integration/` directory

## Test Data Requirements
- Include realistic test data
- Setup/teardown for databases
- Mock external services properly
- Clean state between tests

Begin integration test generation now.
