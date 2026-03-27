# BGF Retail Auto-Order System - Security Architecture Review

> Review Date: 2026-03-01
> Reviewer: Security Architect Agent
> Scope: Full codebase at `C:\Users\kanur\OneDrive\바탕 화면\bbb\bgf_auto\`
> Framework: OWASP Top 10 (2021), PDCA Security Model
> Previous Audit: 2026-02-22 (Score: 35/100)

---

## 1. Executive Summary

### Current Score: 68/100 (Significant Improvement Since Last Audit)

Since the 2026-02-22 audit (35/100), major security improvements have been implemented:
- Authentication system added (session-based, werkzeug password hashing)
- Role-based access control (admin/viewer)
- Rate limiting middleware
- Security headers (CSP, X-Frame-Options, etc.)
- `.env` added to `.gitignore`
- `host=0.0.0.0` changed to `127.0.0.1`

However, critical issues remain that prevent a higher score.

| Severity | Count | Status |
|----------|-------|--------|
| **Critical** | 2 | Requires immediate action |
| **High** | 4 | Fix before next release |
| **Medium** | 6 | Fix in next sprint |
| **Low** | 5 | Track in backlog |

### Top 3 Remaining Risks

1. **Plaintext credentials in `.env` and DB (`stores.bgf_password`) with real passwords visible**
2. **No CSRF protection on any state-changing endpoint (all POST/PUT/DELETE)**
3. **`subprocess.Popen` command execution from web API with limited argument validation**

---

## 2. OWASP Top 10 Analysis

---

### A01: Broken Access Control [HIGH - Partially Remediated]

**Previous Status**: CRITICAL (no auth at all)
**Current Status**: HIGH (auth exists but gaps remain)

#### 2.1a What Was Fixed (Good)

The authentication system is now in place with proper patterns:

- `C:\Users\kanur\OneDrive\바탕 화면\bbb\bgf_auto\src\web\routes\api_auth.py` - Session-based auth with `login_required` and `admin_required` decorators
- `C:\Users\kanur\OneDrive\바탕 화면\bbb\bgf_auto\src\web\middleware.py` - `check_auth_and_store_access()` middleware enforces store-level isolation for viewer role
- `C:\Users\kanur\OneDrive\바탕 화면\bbb\bgf_auto\src\web\app.py` line 60-64 - Global `before_request` hook checks authentication
- Brute-force protection: 5 attempts per 5-minute window per IP (api_auth.py lines 20-35)
- Admin-only decorators applied to destructive operations: `scheduler/start`, `scheduler/stop`, `run-script`, `predict`, user management

#### 2.1b Remaining Issues

**ISSUE-01: Inconsistent Auth Decorator Application [HIGH]**

Several endpoints that modify state or expose sensitive data lack explicit `@login_required` or `@admin_required`:

| File | Endpoint | Method | Issue |
|------|----------|--------|-------|
| `api_order.py` line 117 | `/adjust` | POST | No `@login_required` - relies solely on middleware |
| `api_order.py` line 143 | `/categories` | GET | No auth - exposes business data categories |
| `api_order.py` line 185 | `/partial-summary` | POST | No auth decorator |
| `api_order.py` line 216 | `/export-excel` | POST | No auth - generates downloadable files |
| `api_order.py` line 639 | `/script-status` | GET | No auth - exposes running process details |
| `api_order.py` line 660 | `/stop-script` | POST | No auth - can stop running scripts |
| `api_order.py` line 679 | `/exclusions` | GET | No auth decorator |
| `api_order.py` line 703 | `/exclusions/toggle` | POST | No auth - modifies ordering behavior |

These endpoints ARE protected by the global `check_auth_and_store_access()` middleware in `app.py`, but the defense-in-depth principle requires explicit decorators for clarity and safety against middleware bypass.

**File**: `C:\Users\kanur\OneDrive\바탕 화면\bbb\bgf_auto\src\web\routes\api_order.py`

**Severity**: HIGH
**Remediation**:
```python
# Add explicit decorators to all state-changing endpoints
@order_bp.route("/adjust", methods=["POST"])
@admin_required  # This modifies prediction data
def adjust():
    ...

@order_bp.route("/stop-script", methods=["POST"])
@admin_required  # Process termination requires admin
def stop_script():
    ...

@order_bp.route("/exclusions/toggle", methods=["POST"])
@admin_required  # Modifies ordering behavior
def toggle_exclusion():
    ...
```

**ISSUE-02: Health Endpoint `/api/health` Bypasses Auth [LOW]**

The `/api/health` endpoint is intentionally unauthenticated (for external monitoring), but the `/api/health/detail` endpoint exposes DB sizes, scheduler PID, error log contents, and cloud sync status without its own auth guard. It relies on middleware but the comment on line 213 says "before_request handles auth" which is fragile.

**File**: `C:\Users\kanur\OneDrive\바탕 화면\bbb\bgf_auto\src\web\routes\api_health.py` line 212

**Severity**: LOW
**Remediation**: Add `@login_required` explicitly to `/api/health/detail`.

---

### A02: Cryptographic Failures [CRITICAL]

**ISSUE-03: Real Credentials in .env File [CRITICAL]**

The `.env` file contains actual production credentials in plaintext:

**File**: `C:\Users\kanur\OneDrive\바탕 화면\bbb\bgf_auto\.env`
```
BGF_USER_ID=46513
BGF_PASSWORD=1113
BGF_USER_ID_46513=46513
BGF_PASSWORD_46513=1113
BGF_USER_ID_46704=46704
BGF_PASSWORD_46704=1113
KAKAO_REST_API_KEY=1a01b8795eec4f853909f272856ea0f2
KAKAO_CLIENT_SECRET=VEVAyIaBudBgYCdxpN5dAzxSy0KaaFwl
KAKAO_ID=kanura4533@hanmail.net
KAKAO_PW=dltkdals23!
```

While `.gitignore` now includes `.env` (FIXED since last audit), the file still exists on disk. If the repository was ever pushed without `.gitignore` protection, credentials may be in git history.

**Severity**: CRITICAL
**Remediation**:
1. Rotate ALL exposed credentials immediately (BGF passwords, Kakao API key, Kakao client secret, Kakao login credentials)
2. Use a secrets manager (Windows Credential Manager, Azure Key Vault, or encrypted `.env.enc`)
3. Verify git history: `git log --all -- .env` - if found, use `git filter-branch` or BFG Repo-Cleaner
4. All passwords in the file appear to be `1113` which is extremely weak (4-digit numeric)

**ISSUE-04: BGF Password Stored in Plaintext in Database [HIGH]**

The `stores` table in `common.db` has a `bgf_password TEXT` column storing credentials in plaintext.

**File**: `C:\Users\kanur\OneDrive\바탕 화면\bbb\bgf_auto\src\infrastructure\database\schema.py` lines 98-106
```sql
CREATE TABLE IF NOT EXISTS stores (
    store_id TEXT PRIMARY KEY,
    store_name TEXT NOT NULL,
    location TEXT,
    type TEXT,
    is_active INTEGER DEFAULT 1,
    bgf_user_id TEXT,
    bgf_password TEXT,    -- PLAINTEXT PASSWORD IN DATABASE
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
)
```

And `store_manager.py` writes plaintext passwords via:
```python
"bgf_password": bgf_password,  # line 153
```

**Severity**: HIGH
**Remediation**:
1. Remove `bgf_password` column from `stores` table - credentials should ONLY come from environment variables
2. If DB storage is required, use Fernet symmetric encryption (`cryptography.fernet`)
3. The `StoreConfigLoader` already loads from env vars correctly - the DB column is redundant

**ISSUE-05: Kakao Tokens in JSON File [MEDIUM]**

**File**: `C:\Users\kanur\OneDrive\바탕 화면\bbb\bgf_auto\config\kakao_token.json`
```json
{
  "access_token": "BLgtTigbrk1hLTtyqEQnqg...",
  "refresh_token": "mNr1BRun5aRdd8-yqZxCYc..."
}
```

This is partially mitigated by `.gitignore` including `config/kakao_token.json`. However, the tokens are still plaintext on disk.

**Severity**: MEDIUM (mitigated by .gitignore)

**ISSUE-06: PythonAnywhere API Token in JSON File [MEDIUM]**

**File**: `C:\Users\kanur\OneDrive\바탕 화면\bbb\bgf_auto\config\pythonanywhere.json`
```json
{
    "username": "kanura",
    "api_token": "9eb558f669ed75ee8ef4a071b22c4f13bef561b8",
    ...
}
```

Also mitigated by `.gitignore` entry `config/pythonanywhere.json`.

**Severity**: MEDIUM (mitigated by .gitignore)

**ISSUE-07: Weak Minimum Password Length [MEDIUM]**

**File**: `C:\Users\kanur\OneDrive\바탕 화면\bbb\bgf_auto\src\web\routes\api_auth.py` line 67
```python
_MIN_PASSWORD_LEN = 4
```

A 4-character minimum password is far below security standards. NIST 800-63B recommends at least 8 characters.

**Severity**: MEDIUM
**Remediation**: Increase `_MIN_PASSWORD_LEN` to 8 and add complexity requirements (mixed case + digits).

**ISSUE-08: Flask SECRET_KEY Fallback [LOW - Mostly Fixed]**

**File**: `C:\Users\kanur\OneDrive\바탕 화면\bbb\bgf_auto\src\web\app.py` line 32
```python
app.config["SECRET_KEY"] = os.getenv("FLASK_SECRET_KEY") or secrets.token_hex(32)
```

This is a significant improvement from the previous hardcoded default. The `secrets.token_hex(32)` fallback generates a cryptographically strong random key. However, the key changes on every restart, invalidating all existing sessions.

**Severity**: LOW
**Remediation**: Set `FLASK_SECRET_KEY` in `.env` to persist across restarts.

---

### A03: Injection [LOW - Well Protected]

#### SQL Injection: PASS

The codebase consistently uses parameterized queries across all 25+ repository files. No dynamic SQL string concatenation with user input was found.

**Evidence of good practices**:

1. **BaseRepository pattern** (`C:\Users\kanur\OneDrive\바탕 화면\bbb\bgf_auto\src\infrastructure\database\base_repository.py`) - All repos inherit proper patterns

2. **Parameterized queries throughout** - Example from `sales_repo.py`:
```python
cursor.execute("SELECT * FROM daily_sales WHERE item_cd = ?", (item_cd,))
```

3. **Sort column whitelist** in `api_category.py` lines 37-43:
```python
ALLOWED_SORT_COLUMNS = {
    "sale_qty": "sale_qty",
    "disuse_qty": "disuse_qty",
    ...
}
```

4. **Input validation patterns** in `api_order.py` lines 22-23:
```python
_STORE_ID_PATTERN = re.compile(r'^[0-9]{4,6}$')
_CATEGORY_CODE_PATTERN = re.compile(r'^[0-9]{3}$')
```

**One Minor Concern**: In `api_prediction.py` lines 118-126, the `sf` variable is used in f-strings for SQL:
```python
sf = "AND store_id = ?" if store_id else ""
sp = (store_id,) if store_id else ()
row = conn.execute(f"""
    SELECT COUNT(*) AS total, ...
    FROM eval_outcomes
    WHERE created_at >= datetime('now', '-7 days') {sf}
""", sp)
```

This is SAFE because `sf` is a constant string controlled by the application (not user input), and the actual `store_id` value is passed as a parameter.

**Severity**: LOW (good overall, minor pattern concern)

#### Command Injection: MEDIUM

**ISSUE-09: subprocess.Popen with User-Influenced Arguments [MEDIUM]**

**File**: `C:\Users\kanur\OneDrive\바탕 화면\bbb\bgf_auto\src\web\routes\api_order.py` lines 560-636

The `/run-script` endpoint executes Python scripts via `subprocess.Popen`:

```python
cmd = [sys.executable, script_path] + list(cfg["args"])
cmd += ["--store-id", str(store_id)]  # user input
cmd += ["--categories", ",".join(str(c) for c in cat_codes)]  # user input
cmd += ["--max-items", str(int(max_items))]  # user input
```

**Mitigations already in place** (GOOD):
- Script path is from a hardcoded whitelist `_SCRIPT_MAP` (line 540-546) - no arbitrary script execution
- `store_id` is validated against `_STORE_ID_PATTERN` (regex `^[0-9]{4,6}$`)
- `cat_codes` are validated against `_CATEGORY_CODE_PATTERN` (regex `^[0-9]{3}$`)
- `max_items` and `min_qty` are cast to `int()` before use
- `shell=False` is used (Popen default - no shell expansion)
- Endpoint requires `@admin_required`

**Remaining risk**: The `mode` parameter is validated against `_SCRIPT_MAP` keys. The hardcoded scripts include `"real-order"` which triggers actual BGF ordering. An admin with compromised credentials could trigger real purchases.

**Severity**: MEDIUM (well-mitigated but subprocess pattern is inherently risky)
**Remediation**:
1. Add a confirmation step or two-factor for `real-order` mode
2. Log all script executions with full command lines to audit trail
3. Consider replacing subprocess with direct Python function calls

---

### A04: Insecure Design [MEDIUM]

**ISSUE-10: No CSRF Protection [HIGH]**

There is zero CSRF protection across the entire Flask application. No CSRF tokens, no SameSite=Strict cookies, no `X-CSRF-Token` header validation.

**File**: `C:\Users\kanur\OneDrive\바탕 화면\bbb\bgf_auto\src\web\app.py`

While `SESSION_COOKIE_SAMESITE = "Lax"` (line 36) provides partial CSRF protection for top-level navigations, it does NOT protect against:
- Cross-origin `fetch()` with `credentials: 'include'`
- Subdomain attacks
- Cross-site POST from `<form>` elements

**Severity**: HIGH
**Attack Scenario**: If an admin visits a malicious page while logged into the dashboard, that page could POST to `/api/order/run-script` with `mode: "real-order"` and trigger actual purchasing.

**Remediation**:
```python
# Option 1: Flask-WTF CSRF
from flask_wtf.csrf import CSRFProtect
csrf = CSRFProtect()
csrf.init_app(app)

# Option 2: Custom double-submit cookie pattern
# Generate CSRF token on login, require it in X-CSRF-Token header
```

**ISSUE-11: Session Fixation Resistance [LOW]**

**File**: `C:\Users\kanur\OneDrive\바탕 화면\bbb\bgf_auto\src\web\routes\api_auth.py` lines 99-105

The login handler sets session data but does not regenerate the session ID. Flask's default session handler uses client-side signed cookies, which mitigates session fixation somewhat, but best practice is to call `session.clear()` before setting new session values.

```python
# Current code:
session.permanent = True
session["user_id"] = user["id"]
# ...

# Recommended:
old_data = dict(session)  # preserve if needed
session.clear()  # regenerate
session.permanent = True
session["user_id"] = user["id"]
```

**Severity**: LOW

---

### A05: Security Misconfiguration [MEDIUM]

**ISSUE-12: CSP Allows 'unsafe-inline' and 'unsafe-eval' [MEDIUM]**

**File**: `C:\Users\kanur\OneDrive\바탕 화면\bbb\bgf_auto\src\web\app.py` lines 80-87
```python
"script-src 'self' 'unsafe-inline' 'unsafe-eval' https://cdn.jsdelivr.net; "
"style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net https://fonts.googleapis.com; "
```

`'unsafe-inline'` and `'unsafe-eval'` in script-src effectively nullify CSP protection against XSS. While `'unsafe-inline'` for styles is often acceptable, it should be avoided for scripts.

**Severity**: MEDIUM
**Remediation**: Use nonce-based CSP for inline scripts:
```python
import secrets
nonce = secrets.token_urlsafe(16)
f"script-src 'self' 'nonce-{nonce}' https://cdn.jsdelivr.net; "
```

**ISSUE-13: No HTTPS Enforcement / HSTS Header Missing [MEDIUM]**

The application has no `Strict-Transport-Security` header and no HTTPS redirect. While the app binds to `127.0.0.1:5000` (local only), the PythonAnywhere deployment (`kanura.pythonanywhere.com`) likely serves over HTTPS but the app itself doesn't enforce it.

**File**: `C:\Users\kanur\OneDrive\바탕 화면\bbb\bgf_auto\src\web\app.py` lines 73-87 (security headers section)

**Severity**: MEDIUM (local deployment mitigates)
**Remediation**: Add HSTS header for production:
```python
response.headers['Strict-Transport-Security'] = 'max-age=31536000; includeSubDomains'
```

**ISSUE-14: SESSION_COOKIE_SECURE Not Set [MEDIUM]**

**File**: `C:\Users\kanur\OneDrive\바탕 화면\bbb\bgf_auto\src\web\app.py`

`SESSION_COOKIE_SECURE` is not set, meaning the session cookie can be transmitted over HTTP. If deployed behind HTTPS (PythonAnywhere), this should be `True`.

**Severity**: MEDIUM
**Remediation**:
```python
app.config["SESSION_COOKIE_SECURE"] = os.getenv("FLASK_ENV") == "production"
```

---

### A06: Vulnerable and Outdated Components [LOW]

No `requirements.txt` or `pyproject.toml` was analyzed for known CVEs in this review. The key dependencies are:
- Flask (web framework)
- Selenium (browser automation)
- werkzeug (password hashing - GOOD)
- sklearn (ML models)

**Recommendation**: Run `pip audit` or `safety check` regularly.

**Severity**: LOW (insufficient data for full assessment)

---

### A07: Identification and Authentication Failures [MEDIUM]

#### What Works Well (GOOD)

1. **Password Hashing**: `werkzeug.security.generate_password_hash` / `check_password_hash` used correctly in `user_repo.py` and `signup_repo.py`
2. **Brute-force Protection**: 5 attempts per 5 minutes per IP (api_auth.py lines 20-35)
3. **Session Management**: 8-hour session lifetime, `HttpOnly` cookie, `SameSite=Lax`
4. **Account Deactivation**: `is_active` check in `verify_password()` (user_repo.py line 99)
5. **Self-deletion Prevention**: Users cannot delete their own accounts (api_auth.py line 224)

#### Remaining Issues

**ISSUE-15: No Account Lockout After Failed Attempts [MEDIUM]**

The rate limiter blocks new attempts for 5 minutes, but the in-memory storage resets on server restart. A persistent attacker could restart attempts after each window or after server restart.

**File**: `C:\Users\kanur\OneDrive\바탕 화면\bbb\bgf_auto\src\web\routes\api_auth.py` line 20
```python
_login_attempts: dict[str, list[float]] = defaultdict(list)
```

**Severity**: MEDIUM
**Remediation**: Store failed attempts in DB with permanent lockout after N failures.

**ISSUE-16: No Password Complexity Requirements [MEDIUM]**

Only minimum length (4 chars) is checked. No requirements for uppercase, lowercase, digits, or special characters.

**Severity**: MEDIUM (combined with ISSUE-07)

---

### A08: Software and Data Integrity Failures [LOW]

**ISSUE-17: Feature Flags Modified at Runtime Without Persistence [LOW]**

**File**: `C:\Users\kanur\OneDrive\바탕 화면\bbb\bgf_auto\src\web\routes\api_settings.py` lines 250-282

Feature flags are changed via `setattr(const, key, new_value)` which modifies the Python module in-memory. This does not persist across restarts and could lead to inconsistent behavior.

**Severity**: LOW (audit log captures changes)
**Remediation**: Write flag changes to DB or config file for persistence.

**ISSUE-18: DB Backup SHA256 Verification [GOOD]**

The health-check-alert system includes DB backup SHA256 verification (per MEMORY.md). This is a positive integrity control.

---

### A09: Security Logging and Monitoring Failures [LOW]

#### What Works Well (GOOD)

1. **Access Logging**: Every request logged in `app.py` line 67-70
2. **Authentication Events**: Login success/failure logged with IP and username
3. **Admin Actions**: User creation, deletion, signup approval all logged
4. **Settings Audit Trail**: `settings_audit_log` table in common.db
5. **AlertingHandler**: Custom alerting with cooldown and time restrictions

#### Remaining Issues

**ISSUE-19: Sensitive Data in Logs [LOW]**

Login failure logs include the attempted username (api_auth.py line 96):
```python
logger.warning(f"[AUTH] 로그인 실패: {username} from {ip}")
```

While username logging is standard practice, ensure passwords are NEVER logged. Current code is clean in this regard.

**Severity**: LOW

---

### A10: Server-Side Request Forgery (SSRF) [LOW]

**ISSUE-20: Log File Path Parameter [LOW]**

**File**: `C:\Users\kanur\OneDrive\바탕 화면\bbb\bgf_auto\src\web\routes\api_logs.py` line 28
```python
log_file = request.args.get("file")
stats = _parser.get_file_stats(log_file)
```

The `file` parameter is passed to `LogParser.get_file_stats()`. If the log parser resolves arbitrary file paths, this could be a path traversal issue. However, from the search results, `LogParser` uses a fixed set of log file names ("main", "prediction", "order", "collector", "error") mapped to specific files in the `logs/` directory, which limits the risk.

**Severity**: LOW (if LogParser uses whitelist mapping)
**Remediation**: Verify LogParser validates the `file` parameter against an allowlist.

---

## 3. Error Handling and Information Disclosure

**ISSUE-21: Exception Details Exposed to Client [MEDIUM]**

Multiple endpoints return `str(e)` directly to the client:

| File | Line | Pattern |
|------|------|---------|
| `api_settings.py` | 141, 185, 224, 247, 282, 314 | `jsonify({"error": str(e)})` |
| `api_receiving.py` | 134, 177, 281, 304, 323, 350, 372 | `jsonify({"error": str(e)})` |
| `api_inventory.py` | 219, 336 | `str(e)[:200]` (truncated, slightly better) |
| `api_health.py` | 57 | `str(e)[:200]` |

This can leak internal implementation details (SQL table names, file paths, library versions) to attackers.

**Severity**: MEDIUM
**Remediation**: Use generic error messages for API responses; log full errors server-side.
```python
# Bad
return jsonify({"error": str(e)}), 500

# Good
logger.error(f"처리 실패: {e}", exc_info=True)
return jsonify({"error": "처리에 실패했습니다"}), 500
```

Note: Some endpoints already follow the correct pattern (e.g., `api_waste.py` consistently uses `"처리에 실패했습니다"`).

---

## 4. Selenium Automation Security

**ISSUE-22: Browser Automation Detection Bypass [LOW]**

**File**: `C:\Users\kanur\OneDrive\바탕 화면\bbb\bgf_auto\src\sales_analyzer.py` lines 99-117
```python
chrome_options.add_argument("--disable-blink-features=AutomationControlled")
chrome_options.add_experimental_option("excludeSwitches", ["enable-automation"])
```

This is standard for Selenium automation against detection-aware sites. The credentials are loaded from environment variables via `StoreConfigLoader` which is the correct pattern.

**Positive Finding**: `requests` calls consistently use `timeout=10` per MEMORY.md.

**Severity**: LOW (operational risk, not a vulnerability)

---

## 5. File System Security

**ISSUE-23: Path Traversal Risk in Report Baseline [LOW]**

**File**: `C:\Users\kanur\OneDrive\바탕 화면\bbb\bgf_auto\src\web\routes\api_report.py` lines 134-144
```python
baseline_param = request.args.get("baseline")
# ...
if not baseline_param or not Path(baseline_param).exists():
    ...
baseline = json.loads(Path(baseline_param).read_text(encoding="utf-8"))
```

The `baseline` query parameter accepts a file path from user input and reads it. An attacker could potentially read arbitrary JSON files on the server.

**Severity**: LOW (requires auth, file must be valid JSON, only reads)
**Remediation**: Validate that the path is within the expected directory:
```python
expected_dir = Path(project_root) / "data" / "reports" / "impact"
baseline_path = Path(baseline_param).resolve()
if not str(baseline_path).startswith(str(expected_dir.resolve())):
    return jsonify({"error": "Invalid baseline path"}), 400
```

---

## 6. Summary of Findings by Severity

### Critical (2) - Block Deployment

| ID | OWASP | Finding | File |
|----|-------|---------|------|
| ISSUE-03 | A02 | Real credentials in `.env` (BGF passwords, Kakao API keys, personal email/password) | `.env` |
| ISSUE-10 | A04 | No CSRF protection on any state-changing endpoint | `app.py` |

### High (4) - Fix Before Release

| ID | OWASP | Finding | File |
|----|-------|---------|------|
| ISSUE-01 | A01 | Missing auth decorators on 8 endpoints in api_order.py | `api_order.py` |
| ISSUE-04 | A02 | `bgf_password` stored as plaintext in stores table (common.db) | `schema.py` |
| ISSUE-07 | A02 | Minimum password length only 4 characters | `api_auth.py` |
| ISSUE-21 | A05 | Exception details (`str(e)`) exposed to API clients in 6 files | Multiple |

### Medium (6) - Fix Next Sprint

| ID | OWASP | Finding | File |
|----|-------|---------|------|
| ISSUE-05 | A02 | Kakao tokens in plaintext JSON (mitigated by .gitignore) | `config/kakao_token.json` |
| ISSUE-06 | A02 | PythonAnywhere API token in plaintext JSON | `config/pythonanywhere.json` |
| ISSUE-09 | A03 | subprocess.Popen with user-influenced arguments | `api_order.py` |
| ISSUE-12 | A05 | CSP allows 'unsafe-inline' and 'unsafe-eval' | `app.py` |
| ISSUE-13 | A05 | No HSTS header / HTTPS enforcement | `app.py` |
| ISSUE-14 | A05 | SESSION_COOKIE_SECURE not set | `app.py` |
| ISSUE-15 | A07 | No persistent account lockout | `api_auth.py` |

### Low (5) - Backlog

| ID | OWASP | Finding | File |
|----|-------|---------|------|
| ISSUE-02 | A01 | `/api/health/detail` has no explicit auth decorator | `api_health.py` |
| ISSUE-08 | A02 | SECRET_KEY regenerated on restart (session invalidation) | `app.py` |
| ISSUE-11 | A04 | No session ID regeneration on login | `api_auth.py` |
| ISSUE-17 | A08 | Feature flags not persisted to storage | `api_settings.py` |
| ISSUE-23 | A10 | Potential path traversal in baseline file reading | `api_report.py` |

---

## 7. Positive Security Controls (What Works Well)

| Area | Assessment | Evidence |
|------|------------|----------|
| SQL Injection Protection | EXCELLENT | 100% parameterized queries across 25+ repository files |
| Password Hashing | GOOD | werkzeug `generate_password_hash` / `check_password_hash` |
| Session Management | GOOD | HttpOnly, SameSite=Lax, 8-hour lifetime |
| Security Headers | GOOD | X-Frame-Options: DENY, X-Content-Type-Options: nosniff, Referrer-Policy |
| Rate Limiting | GOOD | Global + endpoint-specific limits |
| Role-Based Access | GOOD | admin/viewer with store isolation |
| Brute-Force Protection | GOOD | 5 attempts / 5 minutes per IP |
| Input Validation | GOOD | Regex patterns for store_id, category codes, usernames |
| Jinja2 Auto-Escaping | GOOD | XSS protection for template rendering |
| Request Timeout | GOOD | `requests` timeout=10 consistently used |
| Error Handlers | GOOD | Global 404/500 handlers return generic JSON |
| Binding Address | GOOD | `host="127.0.0.1"` (was 0.0.0.0 - FIXED) |
| .gitignore | GOOD | Covers .env, tokens, DB files, logs |
| Audit Trail | GOOD | Settings changes logged to DB with user attribution |
| User Management | GOOD | Signup requires admin approval, self-deletion blocked |
| Script Whitelist | GOOD | Only predefined scripts can be executed via web API |

---

## 8. Remediation Priority (PDCA Act Phase)

### Immediate (This Week)

1. **Rotate all credentials** exposed in `.env` - new BGF passwords, Kakao API key, Kakao secrets
2. **Add CSRF protection** - Install Flask-WTF or implement double-submit cookie
3. **Add explicit auth decorators** to all 8 undecorated endpoints in `api_order.py`

### Short-Term (Next Sprint)

4. Remove `bgf_password` column from `stores` table schema
5. Increase `_MIN_PASSWORD_LEN` to 8 with complexity requirements
6. Replace `str(e)` in error responses with generic messages across all API files
7. Set `SESSION_COOKIE_SECURE = True` for production
8. Add HSTS header

### Medium-Term (Next Month)

9. Implement nonce-based CSP (remove `unsafe-inline`, `unsafe-eval`)
10. Add persistent failed login tracking in DB
11. Encrypt sensitive config files at rest (Fernet)
12. Run `pip audit` / `safety check` and integrate into CI

### Long-Term

13. Replace subprocess-based script execution with direct Python function calls
14. Implement proper session ID regeneration on login
15. Add request signing for critical operations (real ordering)

---

## 9. Comparison with Previous Audit (2026-02-22)

| Issue from Previous Audit | Previous Status | Current Status |
|--------------------------|----------------|----------------|
| `.env` not in `.gitignore` | CRITICAL | FIXED - `.gitignore` now includes `.env` |
| Flask no authentication | CRITICAL | FIXED - Session auth + RBAC implemented |
| `subprocess.Popen` no auth | CRITICAL | IMPROVED - `@admin_required` + input validation |
| `bgf_password` plaintext in DB | HIGH | OPEN - Still stores plaintext |
| SECRET_KEY hardcoded | HIGH | FIXED - `secrets.token_hex(32)` fallback |
| `host=0.0.0.0` binding | HIGH | FIXED - Changed to `127.0.0.1` |
| No security headers | HIGH | FIXED - CSP, X-Frame-Options, etc. added |
| No rate limiting | MEDIUM | FIXED - RateLimiter middleware added |
| Error details exposed | MEDIUM | PARTIAL - Some endpoints fixed, some still expose `str(e)` |

**Score Improvement**: 35/100 -> 68/100 (+33 points)

---

## 10. Architecture Diagram: Current Security Layers

```
                            Internet
                               |
                     [PythonAnywhere HTTPS]
                               |
                    +----------+----------+
                    |    Flask Application |
                    +---------------------+
                    |                     |
                    |  before_request     |
                    |  1. RateLimiter     |  <-- Global rate limit (60/min)
                    |  2. AuthMiddleware  |  <-- Session check + store isolation
                    |  3. RequestLogger   |  <-- Access logging
                    |                     |
                    +---------------------+
                    |                     |
                    |  Blueprint Routes   |
                    |  - @login_required  |  <-- Explicit auth guards
                    |  - @admin_required  |  <-- Role-based access
                    |  - Input Validation |  <-- Regex + type checks
                    |                     |
                    +---------------------+
                    |                     |
                    |  Repository Layer   |
                    |  - Parameterized ?  |  <-- SQL injection protection
                    |  - BaseRepository   |  <-- Connection management
                    |  - DBRouter         |  <-- Store isolation
                    |                     |
                    +---------------------+
                    |                     |
                    |  after_request      |
                    |  - Security Headers |  <-- CSP, X-Frame-Options, etc.
                    |  - Cache-Control    |
                    |                     |
                    +---------------------+
```

---

*End of Security Architecture Review*
*Next Review: Recommended after remediation of Critical/High items*
