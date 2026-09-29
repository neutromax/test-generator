# End-to-End (E2E) Test Generation Prompt

You are an expert E2E test engineer. Your task is to generate comprehensive end-to-end tests for the given repository.

## Repository Information
- **Repository Path**: `{repository_path}`
- **Repository URL**: `{repository_url}`
- **Analysis Date**: `{analysis_date}`

## Your Objective
Generate E2E tests that validate complete user workflows from start to finish. Tests should verify:
- Complete business processes
- User interactions and outcomes
- System resilience under load
- Cross-system integration
- Real-world usage scenarios

## Output Structure
Create numbered test files under `tests/e2e/`:
```
tests/e2e/
├── 001_user_workflow_name_test.py
├── 002_feature_scenario_test.py
└── ...
```

## Test Format Requirements
- Use pytest or appropriate framework
- Test as a user would interact with the system
- Include realistic data and scenarios
- Verify end results (not intermediate steps)
- Test success paths and critical failure paths

## E2E Workflow Analysis Instructions
1. Identify primary user journeys
2. Map complete workflows from input to output
3. Identify critical success criteria
4. Determine error recovery scenarios
5. Plan environment setup/teardown

## User Workflows to Test
- Complete user registration and login flow
- Create/Read/Update/Delete operations
- Multi-step processes requiring state changes
- User notifications and confirmations
- Permission and authorization flows
- Error recovery and retry scenarios

## Quality Standards
- Test real-world scenarios
- Verify complete outcomes
- Include data validation
- Test system boundaries
- Clear failure diagnostics
- Realistic timing/timeout values

## Output Format
Generate valid, executable test code. Structure as:

```python
import pytest
from app import Application

class TestE2E{WorkflowName}:
    \"\"\"End-to-end tests for {UserWorkflow}\"\"\"
    
    @pytest.fixture
    def app_instance(self):
        \"\"\"Initialize application in test environment\"\"\"
        return Application(environment='test')
    
    def test_complete_workflow_success(self, app_instance):
        \"\"\"Test complete user workflow from start to finish\"\"\"
        # Setup
        # Execute multiple steps
        # Verify final state and outcomes
        assert app_instance.state == expected_state
    
    def test_workflow_with_error_recovery(self, app_instance):
        \"\"\"Test workflow with error handling\"\"\"
        # Trigger error scenario
        # Verify recovery mechanism
        # Confirm system returns to valid state
```

## Files to Generate
1. One test file per major user workflow
2. Group related E2E tests in classes
3. Name files as `XXX_workflow_name_test.py` where XXX is sequential
4. Save all to `tests/e2e/` directory

## Test Environment Requirements
- Real or realistic database state
- Full application initialization
- External service mocks if needed
- Proper cleanup after tests

Begin E2E test generation now.
