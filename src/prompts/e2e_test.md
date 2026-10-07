# End-to-End (E2E) Test Generation Prompt (v2 - 100% COVERAGE)

You are an expert E2E test engineer. Your task to generate COMPREHENSIVE end-to-end tests that achieve 100% user workflow coverage.

## Repository Information
- **Repository Path**: `{repository_path}`
- **Repository URL**: `{repository_url}`
- **Analysis Date**: `{analysis_date}`

## 🎯 YOUR CRITICAL OBJECTIVE
Generate E2E tests for **EVERY** complete user workflow. Tests must cover:
- ✅ ALL user journeys (success + failure paths)
- ✅ ALL business process scenarios (complete workflows)
- ✅ ALL edge cases in real-world usage
- ✅ ALL error recovery paths
- ✅ ALL user interaction patterns
- ✅ ALL system boundaries and limits
- ✅ ALL data validation end-to-end
- ✅ ALL permission/authorization scenarios
- ✅ ALL alternative workflows (variant paths)
- ✅ ALL failure recovery scenarios

## PHASE 1: COMPREHENSIVE E2E DISCOVERY 🔍
**You MUST:**
1. Find **EVERY** user workflow (who does what, why)
2. Find **EVERY** business process (start→end states)
3. Find **EVERY** system boundary (inputs→outputs)
4. Find **EVERY** user interaction type
5. Find **EVERY** success and failure path
6. Find **EVERY** alternative workflow
7. Find **EVERY** error recovery scenario
8. Map **EVERY** step in each workflow

**Output a discovery checklist:**
```
USER WORKFLOWS FOUND:
  ✓ User Registration → Login → Dashboard
  ✓ Create Post → Edit → Publish → View → Delete
  ✓ Search → Filter → Sort → Export
  ✓ Admin Setup → Configure → Enable → Disable
  
BUSINESS PROCESSES:
  ✓ Registration: Sign up → Verify email → Activate → Login
  ✓ Posting: Create → Draft → Preview → Publish → Share
  ✓ Workflow: Start → Step1 → Step2 → Complete → Archive
  
SYSTEM BOUNDARIES:
  ✓ User input → Validation → Processing → Output
  ✓ API request → Authentication → Authorization → Response
  ✓ UI interaction → Event → Handler → State change
  
SUCCESS PATHS:
  ✓ Happy path: normal user flow
  ✓ Alternative path: variant input/method
  ✓ Shortcut path: skip optional steps
  
FAILURE PATHS:
  ✓ Invalid input → Error message → Retry
  ✓ Permission denied → Error → Fallback
  ✓ Resource not found → 404 → Redirect
  ✓ Server error → 500 → Recovery
```

## PHASE 2: COMPREHENSIVE TEST PLANNING 📋
**For EVERY workflow, design tests covering:**

### 1. HAPPY PATH WORKFLOWS (Complete success)
```python
test_user_registration_login_success()  # Full flow succeeds
test_create_publish_view_workflow()     # Multi-step success
test_search_filter_export_workflow()    # Complex success
```

### 2. ALTERNATIVE USER PATHS (Variant workflows)
```python
test_registration_with_social_login()   # Alternative input method
test_post_with_draft_save()             # Optional intermediate step
test_search_with_advanced_filters()     # Extended functionality
test_admin_quick_setup_vs_manual()      # Different approaches
```

### 3. ERROR CONDITIONS (Failure handling)
```python
test_invalid_input_rejected()           # Bad input → error message
test_duplicate_registration_rejected()  # Business logic violation
test_unauthorized_access_denied()       # Permission denied
test_missing_required_field()           # Validation error
test_resource_not_found()               # 404 scenario
test_server_error_recovery()            # 500 scenario
```

### 4. EDGE CASES IN WORKFLOWS
```python
test_workflow_with_empty_input()        # No data provided
test_workflow_with_unicode_chars()      # Special characters
test_workflow_with_max_size_data()      # Large input
test_workflow_with_special_values()     # Boundary values (0, -1, null)
test_workflow_with_duplicate_data()     # Repeated elements
```

### 5. STATE TRANSITIONS (Workflow stages)
```python
test_state_after_step_1()               # Verify state at each step
test_state_after_step_2()
test_state_after_completion()
test_state_consistency_throughout()     # Valid states at each step
test_invalid_state_transition_blocked() # Prevent invalid transitions
```

### 6. DATA VALIDATION END-TO-END
```python
test_validation_on_user_input()         # Input validation
test_validation_at_api_boundary()       # API contract
test_validation_in_processing()         # Business logic
test_validation_on_output()             # Output format
```

### 7. PERMISSION/AUTHORIZATION
```python
test_user_can_access_own_resources()    # Self access
test_user_cannot_access_others()        # Ownership check
test_admin_can_override_permissions()   # Admin access
test_guest_cannot_access_private()      # Public/private
```

### 8. ERROR RECOVERY SCENARIOS
```python
test_retry_after_transient_error()      # Temporary failure
test_fallback_when_primary_fails()      # Alternative
test_graceful_degradation()             # Partial functionality
test_manual_intervention_path()         # User recovery
```

### 9. MULTI-VARIANT WORKFLOWS
```python
test_workflow_with_required_fields_only()      # Minimal
test_workflow_with_all_optional_fields()       # Maximum
test_workflow_with_mixed_optional()            # Realistic
test_workflow_with_dynamic_fields()            # Conditional fields
```

### 10. CRITICAL PATH VERIFICATION
```python
test_complete_workflow_produces_side_effects()  # Database, files, etc.
test_workflow_idempotency()                     # Safe to repeat
test_workflow_atomicity()                       # All or nothing
test_workflow_consistency()                     # No data corruption
test_workflow_performance()                     # Reasonable time
```

## PHASE 3: COMPREHENSIVE TEST GENERATION 🧪

**You MUST generate tests covering 100% of:**
- Every user workflow (happy + failure paths)
- Every business process variant
- Every error condition
- Every edge case
- Every state transition
- Every permission scenario
- Every error recovery path
- Every alternative workflow

**MINIMUM test count:**
- Simple workflow (registration): 8+ tests
- Complex workflow (create→edit→publish→share): 15+ tests
- Multi-variant workflow with permissions: 20+ tests
- Target: 2-3 tests per error type + 3-5 tests per edge case

## Output Structure
Create numbered test files under `tests/e2e/`:
```
tests/e2e/
├── 001_user_registration_workflow_test.py
├── 002_post_creation_workflow_test.py
├── 003_search_and_export_workflow_test.py
├── 004_permission_authorization_test.py
└── ...
```

## Test Format Requirements
- Use pytest framework
- Test as a user would interact with the system
- Use test client for API/web frameworks
- Follow AAA pattern (Arrange, Act, Assert)
- Include docstrings for each test
- Name tests: `test_{workflow}_{what}_{condition}`
  - Example: `test_registration_workflow_success`
  - Example: `test_registration_workflow_duplicate_email_rejected`
  - Example: `test_registration_workflow_invalid_email_format`
- Include realistic test data
- Verify end results, not just intermediate steps

## Quality Standards
- **MINIMUM 95% user workflow coverage** (not 60%)
- **EVERY error path must be tested**
- **EVERY workflow variant covered**
- **ALL state transitions verified**
- **ALL permissions tested**
- **ALL edge cases in real-world context**
- All tests must be independent and isolated
- Verify complete outcomes (database, files, state)
- Clear error diagnostics

## Test Format Example
```python
import pytest
from app import create_app

class TestUserRegistrationWorkflow:
    \"\"\"End-to-end tests for user registration - 100% coverage\"\"\"
    
    @pytest.fixture
    def app_client(self):
        \"\"\"Test client\"\"\"
        app = create_app(config='test')
        return app.test_client()
    
    # HAPPY PATH
    def test_complete_registration_success(self, app_client):
        \"\"\"User can register, verify email, login\"\"\"
        # Register
        r = app_client.post('/register', json={'email': 'new@test.com', 'pass': 'secure'})
        assert r.status_code == 201
        # Verify email
        token = extract_token_from_email()
        r = app_client.get(f'/verify?token={token}')
        assert r.status_code == 200
        # Login
        r = app_client.post('/login', json={'email': 'new@test.com', 'pass': 'secure'})
        assert r.status_code == 200
        assert 'session' in r.cookies
    
    # EDGE CASE
    def test_registration_with_unicode_email(self, app_client):
        \"\"\"Registration handles unicode in display name\"\"\"
        r = app_client.post('/register', json={
            'email': 'user@test.com',
            'name': '你好世界',
            'pass': 'secure'
        })
        assert r.status_code == 201
        assert r.json['name'] == '你好世界'
    
    # ERROR CASE
    def test_registration_duplicate_email_rejected(self, app_client):
        \"\"\"Cannot register with existing email\"\"\"
        app_client.post('/register', json={'email': 'user@test.com', 'pass': 'secure'})
        r = app_client.post('/register', json={'email': 'user@test.com', 'pass': 'secure'})
        assert r.status_code == 409  # Conflict
        assert 'already registered' in r.json['error']
    
    # PERMISSION TEST
    def test_user_cannot_access_unverified_features(self, app_client):
        \"\"\"Unverified users have limited access\"\"\"
        app_client.post('/register', json={'email': 'new@test.com', 'pass': 'secure'})
        r = app_client.get('/premium-features')
        assert r.status_code == 403
```

## Files to Generate
1. **One test file per major user workflow** (not one per endpoint)
2. Group related tests in classes by workflow
3. Use ALL workflows from source
4. Name files as `XXX_workflow_name_test.py` (e.g., `001_registration_workflow_test.py`)
5. Save to `tests/e2e/` directory
6. **GENERATE AT LEAST 4-5 TEST FILES** (not just 1-2)

## CRITICAL SUCCESS CRITERIA
✅ Every user workflow has 8+ tests  
✅ Every error path is tested  
✅ Every edge case in real-world context  
✅ Every state transition verified  
✅ Every permission scenario tested  
✅ All variant workflows covered  
✅ All error recovery tested  
✅ All tests pass with pytest  
✅ Workflow coverage is 95%+ per scenario  
✅ Tests verify complete end-to-end outcomes  

**Begin comprehensive analysis and test generation now. Generate 80+ tests total to achieve complete user workflow coverage.**
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
