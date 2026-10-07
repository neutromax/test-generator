# Security Test Generation Prompt (v2 - 100% COVERAGE)

You are an expert security engineer. Your task is to generate COMPREHENSIVE security tests that achieve 100% vulnerability coverage.

## Repository Information
- **Repository Path**: `{repository_path}`
- **Repository URL**: `{repository_url}`
- **Analysis Date**: `{analysis_date}`

## 🎯 YOUR CRITICAL OBJECTIVE
Generate security tests for **EVERY** vulnerable input point and security control. Tests must cover:
- ✅ ALL input validation points (injection, malformed data)
- ✅ ALL authentication mechanisms (bypasses, edge cases)
- ✅ ALL authorization checks (privilege escalation, access control)
- ✅ ALL data security (exposure, leakage, encryption)
- ✅ ALL error handling (info disclosure prevention)
- ✅ ALL injection vectors (SQL, XSS, command, path, template)
- ✅ ALL crypto/secrets (hardcoded, weak, exposure)
- ✅ ALL rate limiting and DoS prevention
- ✅ ALL CORS and origin validation
- ✅ ALL session and authentication edge cases

## PHASE 1: COMPREHENSIVE SECURITY DISCOVERY 🔍
**You MUST:**
1. Find **EVERY** input point (API, UI, files, environment)
2. Find **EVERY** authentication mechanism (login, tokens, API keys)
3. Find **EVERY** authorization control (roles, permissions, ownership)
4. Find **EVERY** data storage point (database, files, cache)
5. Find **EVERY** error handling point (logs, responses, messages)
6. Find **EVERY** external call (APIs, databases, files)
7. Find **EVERY** cryptographic operation
8. Find **EVERY** secret/credential reference

**Output a discovery checklist:**
```
INPUT POINTS FOUND:
  ✓ POST /login (email, password)
  ✓ POST /users (name, email, phone)
  ✓ GET /search?q=query (search term)
  ✓ File upload endpoint
  ✓ Configuration files
  
AUTHENTICATION FOUND:
  ✓ Username/password login
  ✓ JWT token validation
  ✓ API key validation
  ✓ Session management
  
AUTHORIZATION FOUND:
  ✓ Role-based (admin, user, guest)
  ✓ Ownership checks (user owns post)
  ✓ Resource-level (can delete comment)
  
DATA SECURITY:
  ✓ Passwords stored (hashed?)
  ✓ API keys stored (encrypted?)
  ✓ Sensitive logs (PII exposure?)
  
ERROR HANDLING:
  ✓ 404 messages (info leak?)
  ✓ 500 messages (stack trace?)
  ✓ Auth errors (success/fail difference?)
  
VULNERABILITIES TO TEST:
  ✓ SQL injection points
  ✓ XSS injection points
  ✓ Command injection points
  ✓ Path traversal points
  ✓ Authentication bypass points
  ✓ Authorization bypass points
```

## PHASE 2: COMPREHENSIVE SECURITY TEST PLANNING 📋
**For EVERY vulnerability type, design tests covering:**

### 1. SQL INJECTION (All injection vectors)
```python
test_sql_injection_string_termination()    # '; DROP TABLE
test_sql_injection_union_select()          # UNION SELECT
test_sql_injection_time_based()            # SLEEP, WAITFOR
test_sql_injection_blind()                 # Boolean-based
test_sql_injection_comment_bypass()        # --, #, /* */
test_sql_injection_encoding()              # URL encoding, Unicode
test_sql_injection_with_parametrized()     # Verify parameters work
```

### 2. XSS PREVENTION (All XSS vectors)
```python
test_xss_script_injection()                # <script>alert()</script>
test_xss_event_handler()                   # onclick="alert()"
test_xss_attribute_injection()             # " onload="
test_xss_svg_injection()                   # <svg onload=>
test_xss_data_url()                        # data:text/html
test_xss_encoding_bypass()                 # &#60;script&#62;
test_xss_case_sensitivity()                # <ScRiPt>
test_xss_html_entity_encoding()            # Verify encoding
```

### 3. AUTHENTICATION BYPASS
```python
test_auth_missing_token()                  # No token provided
test_auth_invalid_token()                  # Malformed token
test_auth_expired_token()                  # Expired JWT
test_auth_wrong_secret()                   # Token from wrong source
test_auth_sql_injection()                  # SQL injection in login
test_auth_password_none()                  # null password
test_auth_username_none()                  # null username
test_auth_weak_session()                   # Predictable session ID
test_auth_session_fixation()               # Attacker sets session
test_auth_timing_attack()                  # Response time reveals data
```

### 4. AUTHORIZATION/PRIVILEGE ESCALATION
```python
test_user_cannot_access_others_data()      # Cross-user access
test_user_cannot_modify_others_data()      # Cross-user modify
test_user_cannot_delete_others_data()      # Cross-user delete
test_user_cannot_elevate_privilege()       # Role escalation
test_user_cannot_impersonate_admin()       # Admin impersonation
test_admin_can_access_all()                # Admin access verified
test_guest_limited_access()                # Guest restrictions
test_permission_boundary_checks()          # All permission edges
```

### 5. SENSITIVE DATA EXPOSURE
```python
test_passwords_hashed_not_plaintext()      # Password storage
test_api_keys_not_hardcoded()              # No hardcoded keys
test_secrets_not_in_logs()                 # No secrets in logs
test_pii_not_in_error_messages()           # No PII disclosure
test_response_data_sanitized()             # No sensitive data
test_cache_not_storing_sensitive()         # Cache security
test_database_connection_secure()          # Encrypted connection
test_file_permissions_restrictive()        # File access control
```

### 6. ERROR MESSAGE INFORMATION DISCLOSURE
```python
test_404_doesnt_reveal_structure()         # No path structure leak
test_500_no_stack_trace()                  # No stack trace exposed
test_auth_error_generic()                  # Generic auth error
test_validation_error_safe()               # Safe error message
test_database_error_hidden()                # DB error not exposed
test_file_error_hidden()                   # File error not exposed
test_exception_doesnt_leak_data()          # Exception safe
```

### 7. INJECTION ATTACKS (All types)
```python
test_command_injection_prevention()        # os.system, subprocess
test_path_traversal_prevention()           # ../, ..\\, etc.
test_template_injection_prevention()       # {{ code }}
test_xml_injection_prevention()            # XXE, XML bombs
test_eval_injection_prevention()           # eval, exec prevention
test_format_string_prevention()            # %x, %n format strings
test_ldap_injection_prevention()           # LDAP filter injection
```

### 8. RATE LIMITING & DOS
```python
test_brute_force_rate_limited()            # Too many login attempts
test_api_rate_limiting()                   # API call limits
test_resource_exhaustion_prevented()       # Memory/CPU limits
test_large_payload_rejected()              # Max payload size
test_slow_client_timeout()                 # Slow client timeout
test_infinite_loop_prevented()             # Timeout on loops
```

### 9. CRYPTO/SECRETS
```python
test_weak_random_not_used()                # Not using random
test_secure_random_used()                  # Using secrets
test_hardcoded_secrets_not_present()       # No hardcoded values
test_api_key_not_logged()                  # Keys not in logs
test_passwords_not_plaintext()             # Passwords encrypted
test_crypto_algorithm_strong()             # Strong algorithms
test_key_derivation_proper()               # Proper KDF used
```

### 10. SESSION & TOKEN SECURITY
```python
test_session_id_unpredictable()            # Strong session IDs
test_session_expires()                     # Session timeout
test_session_invalidation()                # Logout clears session
test_csrf_token_required()                 # CSRF protection
test_same_site_cookie_set()                # SameSite cookie flag
test_secure_cookie_flag()                  # Secure flag set
test_httponly_cookie_flag()                # HttpOnly flag set
test_token_binding()                       # Token to user binding
```

## PHASE 3: COMPREHENSIVE TEST GENERATION 🧪

**You MUST generate tests covering 100% of:**
- Every injection vector (SQL, XSS, command, path, template)
- Every authentication mechanism and bypass
- Every authorization check and escalation vector
- Every error handling point
- Every data storage/exposure point
- Every crypto/secret usage
- Every rate limit and DoS prevention
- Every session/token handling

**MINIMUM test count:**
- Single input point: 8+ tests (validation + all injection types)
- Authentication system: 12+ tests (valid + bypasses + edge cases)
- Authorization system: 15+ tests (roles, permissions, escalation)
- Data security: 10+ tests (storage, exposure, handling)
- Total target: 100+ security tests

## Output Structure
Create numbered test files under `tests/security/`:
```
tests/security/
├── 001_input_validation_injection_test.py
├── 002_authentication_bypass_test.py
├── 003_authorization_escalation_test.py
├── 004_data_security_test.py
├── 005_error_disclosure_test.py
└── ...
```

## Test Format Requirements
- Use pytest framework
- Test both positive (should pass) and negative (should fail)
- Use realistic attack payloads
- Follow AAA pattern (Arrange, Act, Assert)
- Include docstrings for security requirement
- Name tests: `test_{vulnerability}_{what}_{condition}`
  - Example: `test_sql_injection_string_termination_blocked`
  - Example: `test_xss_script_tag_escaped`
  - Example: `test_auth_expired_token_rejected`

## Quality Standards
- **MINIMUM 95% vulnerability coverage** per category
- **EVERY injection type must be tested**
- **EVERY authentication bypass must be tested**
- **EVERY authorization edge case must be tested**
- **EVERY data exposure point must be tested**
- **EVERY error handling must be tested**
- All tests should PASS (security is working)
- Tests should use realistic attack payloads
- Document security assumptions in docstrings

## Test Format Example
```python
import pytest
from app import create_app
from app.models import User

class TestSQLInjectionPrevention:
    \"\"\"Security tests for SQL injection - 100% coverage\"\"\"
    
    @pytest.fixture
    def app_client(self):
        app = create_app(config='test')
        return app.test_client()
    
    # POSITIVE TEST - Should pass
    def test_valid_query_works(self, app_client):
        \"\"\"Normal query works correctly\"\"\"
        r = app_client.get('/search?q=test')
        assert r.status_code == 200
    
    # NEGATIVE TESTS - All should be blocked
    def test_sql_injection_string_termination_blocked(self, app_client):
        \"\"\"SQL injection via string termination prevented\"\"\"
        r = app_client.get(\"/search?q='; DROP TABLE users; --\")
        # Should NOT execute SQL - either 400 error or safe result
        assert r.status_code != 200 or 'users' not in r.data
    
    def test_sql_injection_union_select_blocked(self, app_client):
        \"\"\"SQL injection via UNION SELECT prevented\"\"\"
        r = app_client.get(\"/search?q=x' UNION SELECT password FROM users; --\")
        assert 'password' not in r.data or r.status_code in [400, 403]
    
    def test_sql_injection_parameter_safe(self, app_client):
        \"\"\"Parameterized queries safe against injection\"\"\"
        r = app_client.get(\"/api/user/1'; DROP TABLE; --\")
        # Should NOT interpret as SQL - treat as string
        assert r.status_code in [200, 404]  # Not DB error

class TestAuthenticationBypass:
    \"\"\"Security tests for authentication - 100% coverage\"\"\"
    
    @pytest.fixture
    def app_client(self):
        app = create_app(config='test')
        return app.test_client()
    
    def test_missing_token_rejected(self, app_client):
        \"\"\"Request without token rejected\"\"\"
        r = app_client.get('/api/profile')
        assert r.status_code == 401
    
    def test_invalid_token_rejected(self, app_client):
        \"\"\"Invalid token rejected\"\"\"
        r = app_client.get('/api/profile', headers={'Authorization': 'Bearer invalid'})
        assert r.status_code == 401
    
    def test_expired_token_rejected(self, app_client):
        \"\"\"Expired token rejected\"\"\"
        token = create_expired_token()
        r = app_client.get('/api/profile', headers={'Authorization': f'Bearer {token}'})
        assert r.status_code == 401

class TestAuthorizationBypass:
    \"\"\"Security tests for authorization - 100% coverage\"\"\"
    
    def test_user_cannot_access_others_profile(self, app_client):
        \"\"\"User A cannot view User B's private profile\"\"\"
        login_as('user_a')
        r = app_client.get('/api/user/user_b/profile')
        assert r.status_code == 403  # Forbidden
    
    def test_user_cannot_modify_others_profile(self, app_client):
        \"\"\"User A cannot edit User B's profile\"\"\"
        login_as('user_a')
        r = app_client.put('/api/user/user_b/profile', json={'name': 'Hacked'})
        assert r.status_code == 403
    
    def test_user_cannot_elevate_to_admin(self, app_client):
        \"\"\"Regular user cannot make themselves admin\"\"\"
        login_as('regular_user')
        r = app_client.put('/api/profile', json={'role': 'admin'})
        assert r.status_code == 403  # Or just ignored
```

## Files to Generate
1. **One test file per security category** (input validation, auth, authz, etc.)
2. Group related security tests in classes
3. Name files as `XXX_security_category_test.py` (e.g., `001_input_validation_injection_test.py`)
4. Save to `tests/security/` directory
5. **GENERATE AT LEAST 5-6 TEST FILES**

## CRITICAL SUCCESS CRITERIA
✅ Every input point has 8+ tests  
✅ Every injection type tested  
✅ Every auth bypass tested  
✅ Every authz escalation tested  
✅ Every data exposure tested  
✅ Every error message safe  
✅ All secrets protected  
✅ All rate limiting tested  
✅ All crypto proper  
✅ All tests pass (security working)  

**Begin comprehensive analysis and test generation now. Generate 100+ security tests total to achieve complete vulnerability coverage.**
        assert_table_exists('users')
    
    def test_authentication_required(self):
        \"\"\"Verify endpoint requires authentication\"\"\"
        # Attempt access without token
        response = unauthenticated_request()
        assert response.status_code == 401
    
    def test_invalid_input_rejected(self, app):
        \"\"\"Verify invalid inputs are rejected\"\"\"
        # Send invalid data
        result = app.process(invalid_data='<script>alert()</script>')
        # Verify no XSS vulnerability
        assert '<script>' not in result
```

## Files to Generate
1. One test file per security category
2. Group related security tests in classes
3. Name files as `XXX_security_category_test.py` where XXX is sequential
4. Save all to `tests/security/` directory

## Security Test Categories
- Authentication and authorization
- Input validation and encoding
- Sensitive data protection
- Error handling and logging
- API security
- Session management

Begin security test generation now.
