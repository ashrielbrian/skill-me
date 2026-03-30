# TaskFlow Codebase Audit Report

**Date:** 2026-03-30
**Scope:** Full codebase at `/Users/brian.tang/skill_me/test-repo/taskflow`
**Summary:** 28 issues identified across security, correctness, performance, and error handling categories.

---

## Issues Ranked by Severity

### 1. [CRITICAL / SECURITY] SQL Injection in Task Search

- **File:** `services/task_service.py`, line 43
- **Description:** The `search_tasks` function concatenates user input directly into a raw SQL query string. An attacker can inject arbitrary SQL to read, modify, or delete any data in the database.
- **Code:** `text(f"SELECT * FROM tasks WHERE owner_id = {user_id} AND (title LIKE '%{query}%' OR description LIKE '%{query}%')")`
- **Impact:** Complete database compromise. An attacker can exfiltrate all data, modify records, or drop tables.
- **Proposed Solution:** Use parameterized queries with SQLAlchemy's `text()` bind parameters, or use the ORM's `ilike` filter. Example: `db.query(Task).filter(Task.owner_id == user_id, or_(Task.title.ilike(f"%{query}%"), Task.description.ilike(f"%{query}%")))`.
- **Complexity:** Low. Straightforward replacement of string interpolation with parameterized queries.

---

### 2. [CRITICAL / SECURITY] SQL Injection in Admin Delete User

- **File:** `api/routes/admin.py`, line 39
- **Description:** The `delete_user` endpoint concatenates `user_id` directly into raw SQL. Although `user_id` is typed as `int` by FastAPI (mitigating most injection), this pattern is dangerous and inconsistent with safe coding practices.
- **Code:** `db.execute(text(f"DELETE FROM users WHERE id = {user_id}"))`
- **Impact:** Lower risk than the search injection due to integer typing, but still a bad pattern that could be copy-pasted elsewhere without type safety.
- **Proposed Solution:** Use ORM: `db.query(User).filter(User.id == user_id).delete()`, or use parameterized queries: `db.execute(text("DELETE FROM users WHERE id = :id"), {"id": user_id})`.
- **Complexity:** Low.

---

### 3. [CRITICAL / SECURITY] Arbitrary SQL Execution Endpoint

- **File:** `api/routes/admin.py`, lines 44-53
- **Description:** The `/admin/run-query` endpoint accepts and executes arbitrary SQL from the request body. Even with admin authentication, this is extremely dangerous -- a compromised admin token allows full database control, and the admin check itself is flawed (see issue #6).
- **Impact:** Full database compromise via any path that obtains an admin-flagged JWT.
- **Proposed Solution:** Remove this endpoint entirely. If needed for debugging, restrict to specific read-only queries via an allowlist, and only enable in non-production environments.
- **Complexity:** Low (deletion) to Medium (if implementing an allowlist).

---

### 4. [CRITICAL / SECURITY] Hardcoded Database Credentials

- **File:** `models/database.py`, line 7
- **Description:** Production database URL with username and password is hardcoded in source code: `postgresql://admin:s3cretPassw0rd@prod-db.internal.company.com:5432/taskflow`.
- **Impact:** Anyone with source code access has full database credentials. Credentials are stored in version control history permanently.
- **Proposed Solution:** Read from environment variables: `DATABASE_URL = os.environ["DATABASE_URL"]`. Use a `.env` file for local development (excluded from git). Rotate the exposed credentials immediately.
- **Complexity:** Low.

---

### 5. [CRITICAL / SECURITY] Hardcoded JWT Secret Key

- **File:** `utils/auth.py`, line 9
- **Description:** The JWT signing secret is hardcoded as `"my-super-secret-jwt-key-do-not-share"`. Anyone with source access can forge valid JWTs for any user, including admin users.
- **Impact:** Complete authentication bypass. Attacker can impersonate any user or grant themselves admin access.
- **Proposed Solution:** Load from environment variable. Use a cryptographically random secret of at least 256 bits. Rotate the compromised key and invalidate all existing tokens.
- **Complexity:** Low.

---

### 6. [CRITICAL / SECURITY] Admin Authorization Trusts JWT Claims Without Database Verification

- **File:** `api/routes/admin.py`, lines 13-19
- **Description:** The `require_admin` function checks `payload.get("is_admin")` from the JWT without verifying against the database. Combined with the hardcoded JWT secret (issue #5), any user can forge an admin token. Even with a proper secret, if `is_admin` is ever added to the token payload, a user who was later demoted would retain admin access until token expiry.
- **Impact:** Privilege escalation to admin access.
- **Proposed Solution:** Look up the user in the database and verify `user.is_admin` from the database record, not the JWT claim. Note: the current login endpoint does not even include `is_admin` in the token, so this check always fails -- no one can access admin routes.
- **Complexity:** Low.

---

### 7. [CRITICAL / SECURITY] Arbitrary Code Execution via pickle.loads on Redis Data

- **File:** `utils/cache.py`, line 17
- **Description:** `pickle.loads()` is used to deserialize data from Redis. If an attacker gains write access to Redis (which has no authentication -- see issue #8), they can inject a malicious pickle payload that executes arbitrary code on the server.
- **Impact:** Remote code execution on the application server.
- **Proposed Solution:** Replace `pickle` with `json` serialization. For complex objects, use a safe serialization library or define explicit serialization schemas.
- **Complexity:** Medium. Requires ensuring all cached values are JSON-serializable, which may require adding serialization methods to ORM models.

---

### 8. [HIGH / SECURITY] Redis Connection Without Authentication

- **File:** `utils/cache.py`, line 8
- **Description:** The Redis client connects to a production server without any password or TLS. The hostname is also hardcoded.
- **Impact:** Any network-adjacent attacker can read/write to the cache, enabling cache poisoning and (combined with pickle deserialization) remote code execution.
- **Proposed Solution:** Configure Redis with authentication (`password` parameter), use TLS, and read connection details from environment variables.
- **Complexity:** Low.

---

### 9. [HIGH / SECURITY] Hardcoded Slack Webhook URL

- **File:** `services/notification_service.py`, line 6
- **Description:** A Slack webhook URL with embedded token is hardcoded in source code.
- **Impact:** Anyone with source access can send messages to the Slack channel, potentially for phishing or spam. The token should be rotated.
- **Proposed Solution:** Load from environment variable. Rotate the compromised webhook URL.
- **Complexity:** Low.

---

### 10. [HIGH / SECURITY] JWT Token Passed as Query Parameter

- **File:** `api/routes/tasks.py`, lines 33-37
- **Description:** The `get_current_user` function extracts the JWT token from a query parameter (`token: str = Query(...)`). Query parameters are logged in server access logs, browser history, proxy logs, and referrer headers.
- **Impact:** Token leakage via logs and browser history enables session hijacking.
- **Proposed Solution:** Use the `Authorization: Bearer <token>` header instead. FastAPI's `Depends` with a custom `HTTPBearer` security scheme is idiomatic.
- **Complexity:** Low.

---

### 11. [HIGH / SECURITY] User Registration Returns Hashed Password

- **File:** `api/routes/users.py`, line 41
- **Description:** The registration endpoint returns the full `User` ORM object, which includes `hashed_password` and `is_admin` fields.
- **Impact:** Hashed passwords are exposed to clients. While bcrypt hashes are resistant to reversal, exposing them is unnecessary and violates the principle of least privilege.
- **Proposed Solution:** Return a Pydantic response model that excludes sensitive fields: `class UserResponse(BaseModel): id: int; email: str; username: str; is_active: bool`.
- **Complexity:** Low.

---

### 12. [HIGH / SECURITY] User Profile Endpoint Exposes All Fields

- **File:** `api/routes/users.py`, lines 56-61
- **Description:** The `GET /users/{user_id}` endpoint returns the full User object including `hashed_password` and `is_admin`, and requires no authentication.
- **Impact:** Any unauthenticated user can enumerate user data and retrieve hashed passwords.
- **Proposed Solution:** Add authentication, add authorization (users can only view their own profile or admins can view all), and use a response model that excludes sensitive fields.
- **Complexity:** Low.

---

### 13. [HIGH / SECURITY] Excessive Token Expiry (30 Days)

- **File:** `utils/auth.py`, line 11
- **Description:** `ACCESS_TOKEN_EXPIRE_MINUTES = 43200` results in tokens valid for 30 days. Combined with no token revocation mechanism, a stolen token is valid for an entire month.
- **Impact:** Extended window of exposure for stolen credentials.
- **Proposed Solution:** Reduce to 15-60 minutes for access tokens. Implement refresh tokens with a longer expiry for session continuity. Add a token revocation/blocklist mechanism.
- **Complexity:** Medium. Requires implementing refresh token flow.

---

### 14. [HIGH / SECURITY] Weak API Key Generation Using MD5

- **File:** `utils/auth.py`, lines 42-46
- **Description:** `generate_api_key` uses MD5 to hash a predictable string (`user_id:SECRET_KEY:timestamp`). MD5 is cryptographically broken, and the inputs are guessable.
- **Impact:** API keys can be predicted or brute-forced by an attacker who knows the secret key and approximate creation time.
- **Proposed Solution:** Use `secrets.token_urlsafe(32)` for API key generation. Store a hash of the key in the database rather than generating deterministically.
- **Complexity:** Low.

---

### 15. [HIGH / SECURITY] No Exception Handling in Token Decoding

- **File:** `utils/auth.py`, lines 37-39
- **Description:** `decode_token` calls `jwt.decode()` without any try/except. Invalid, expired, or malformed tokens will raise unhandled exceptions that propagate as 500 Internal Server Errors.
- **Impact:** Application crashes on invalid tokens. Error responses may leak stack traces with sensitive information.
- **Proposed Solution:** Wrap in try/except for `jwt.ExpiredSignatureError`, `jwt.InvalidTokenError`, and return appropriate HTTP 401 responses.
- **Complexity:** Low.

---

### 16. [HIGH / SECURITY] Wildcard CORS Configuration

- **File:** `api/main.py`, lines 9-15
- **Description:** CORS is configured with `allow_origins=["*"]` and `allow_credentials=True`. This combination allows any website to make authenticated cross-origin requests.
- **Impact:** Enables cross-site request forgery and data exfiltration from any malicious website.
- **Proposed Solution:** Restrict `allow_origins` to specific trusted domains. When `allow_credentials=True`, a wildcard origin is already rejected by browsers, but the server should still be explicit.
- **Complexity:** Low.

---

### 17. [HIGH / SECURITY] Request Body Logged Including Passwords

- **File:** `utils/logging.py`, lines 21-31
- **Description:** `log_request` logs the full request body as JSON. This includes login requests containing plaintext passwords and registration requests.
- **Impact:** Passwords stored in plaintext in log files.
- **Proposed Solution:** Sanitize the body before logging -- redact fields named `password`, `token`, `secret`, etc. Or only log non-sensitive metadata.
- **Complexity:** Low.

---

### 18. [HIGH / CORRECTNESS] SQL Injection in Admin Delete (Orphaned Records)

- **File:** `api/routes/admin.py`, lines 36-41
- **Description:** `delete_user` deletes only from the `users` table without deleting or reassigning associated tasks. This leaves orphaned task records with invalid `owner_id` foreign keys.
- **Impact:** Data integrity violation. Orphaned tasks may cause errors when queried.
- **Proposed Solution:** Use cascade delete in the ORM relationship, or explicitly delete associated tasks before deleting the user, or use `ON DELETE CASCADE` in the database schema.
- **Complexity:** Low.

---

### 19. [HIGH / CORRECTNESS] Undefined Variable `assignee_id` in Notification

- **File:** `services/notification_service.py`, lines 38-40
- **Description:** `notify_task_assigned` references `assignee_id` which is not defined in the function scope. This will raise a `NameError` at runtime every time.
- **Impact:** Task assignment notifications always fail with an unhandled exception.
- **Proposed Solution:** Add `assignee_id` as a function parameter: `def notify_task_assigned(task_title, assignee_name, assigner_name, assignee_id)`.
- **Complexity:** Low.

---

### 20. [MEDIUM / CORRECTNESS] Missing Authorization on Task Status Update

- **File:** `services/task_service.py`, lines 57-68
- **Description:** `update_task_status` accepts a `user_id` parameter but never checks whether that user owns or is assigned to the task. Any authenticated user can change the status of any task.
- **Impact:** Broken access control -- users can manipulate other users' tasks.
- **Proposed Solution:** Add a check: `if task.owner_id != user_id and task.assignee_id != user_id: raise PermissionError(...)`.
- **Complexity:** Low.

---

### 21. [MEDIUM / CORRECTNESS] Unauthenticated Task Read Endpoint

- **File:** `api/routes/tasks.py`, lines 48-54
- **Description:** `GET /api/tasks/{task_id}` does not require authentication. Any user (or unauthenticated request) can read any task by ID.
- **Impact:** Information disclosure of potentially sensitive task data.
- **Proposed Solution:** Add `user=Depends(get_current_user)` and verify the user has access to the task.
- **Complexity:** Low.

---

### 22. [MEDIUM / CORRECTNESS] Division by Zero in `compute_task_stats`

- **File:** `services/task_service.py`, line 101
- **Description:** `completion_rate = completed / total` will raise `ZeroDivisionError` when a user has no tasks (`total == 0`).
- **Impact:** The `/stats/me` endpoint crashes for new users with no tasks.
- **Proposed Solution:** `completion_rate = completed / total if total > 0 else 0.0`.
- **Complexity:** Low.

---

### 23. [MEDIUM / CORRECTNESS] Off-by-One Error in Cache Warming

- **File:** `utils/cache.py`, lines 47-51
- **Description:** `warm_cache` iterates `range(1, len(items))`, which skips the first item at index 0.
- **Impact:** The first item in any cache warming batch is never cached, leading to a cache miss on first access.
- **Proposed Solution:** Change to `range(0, len(items))` or simply `for item in items:`.
- **Complexity:** Low.

---

### 24. [MEDIUM / CORRECTNESS] Test File Has Typo in Import Path

- **File:** `tests/test_tasks.py`, line 5
- **Description:** `from taskflow.models.taks import Task, TaskStatus` -- the module name is misspelled as `taks` instead of `task`. This test file will fail with `ModuleNotFoundError`.
- **Impact:** Test suite cannot run; all tests fail at import time.
- **Proposed Solution:** Fix the import: `from taskflow.models.task import Task, TaskStatus`.
- **Complexity:** Low.

---

### 25. [MEDIUM / CORRECTNESS] Test Asserts Wrong Field Name

- **File:** `tests/test_tasks.py`, line 31
- **Description:** `assert stats["complete_rate"] >= 0` references `"complete_rate"` but the actual key is `"completion_rate"`. This test would always fail with a `KeyError`.
- **Impact:** Test suite is broken; gives false negatives.
- **Proposed Solution:** Fix to `assert stats["completion_rate"] >= 0`.
- **Complexity:** Low.

---

### 26. [MEDIUM / CORRECTNESS] Race Conditions in Job Processor

- **File:** `services/job_processor.py`, lines 12-13, 52
- **Description:** `_job_handlers` and `_processing_count` are global mutable state shared across threads with no synchronization. `_processing_count += 1` is not atomic in Python (despite the GIL, it involves read-modify-write at the bytecode level). `_job_handlers` could be modified during iteration.
- **Impact:** Inaccurate processing counts; potential crashes if handlers are registered while jobs are being processed.
- **Proposed Solution:** Use `threading.Lock` to protect shared state, or use `threading.atomic` / `itertools.count` for the counter. For the handler dict, register all handlers before starting workers.
- **Complexity:** Low to Medium.

---

### 27. [MEDIUM / CORRECTNESS] Worker Threads Not Daemon Threads

- **File:** `services/job_processor.py`, lines 74-77
- **Description:** Worker threads are started without `daemon=True`. The main process cannot exit cleanly because these threads run infinite loops.
- **Impact:** Application hangs on shutdown; requires `kill -9` to terminate.
- **Proposed Solution:** Set `t.daemon = True` before `t.start()`, and/or implement a graceful shutdown mechanism using a `threading.Event` stop flag.
- **Complexity:** Low.

---

### 28. [MEDIUM / PERFORMANCE] N+1 Query / In-Memory Pagination in `get_user_tasks`

- **File:** `services/task_service.py`, lines 50-54
- **Description:** `get_user_tasks` loads ALL tasks for a user into memory with `.all()`, then slices the list in Python. For users with thousands of tasks, this wastes memory and database bandwidth.
- **Impact:** Slow response times and high memory usage for users with many tasks. Compounded by the lack of an upper bound on `per_page` (issue in `tasks.py` line 64).
- **Proposed Solution:** Use SQL-level pagination: `db.query(Task).filter(Task.owner_id == user_id).offset(start).limit(per_page).all()`. Also add a maximum cap on `per_page` (e.g., 100).
- **Complexity:** Low.

---

### 29. [MEDIUM / PERFORMANCE] Commits Inside Loop in `bulk_assign_tasks`

- **File:** `services/task_service.py`, lines 74-80
- **Description:** Each task assignment triggers a separate `db.commit()`. For bulk operations with many tasks, this is extremely slow due to per-commit overhead and round-trips.
- **Impact:** Bulk operations are O(n) in database round-trips instead of O(1).
- **Proposed Solution:** Move `db.commit()` outside the loop, after all tasks are updated. Or use a single `UPDATE ... WHERE id IN (...)` query.
- **Complexity:** Low.

---

### 30. [MEDIUM / PERFORMANCE] Redis KEYS Command Blocks Server

- **File:** `utils/cache.py`, lines 27-31
- **Description:** `invalidate_pattern` uses `_redis_client.keys(pattern)` which scans the entire keyspace in a single blocking operation. Redis is single-threaded, so this blocks ALL other Redis operations.
- **Impact:** On large datasets, this can cause multi-second latency spikes for all Redis consumers, effectively causing a denial of service.
- **Proposed Solution:** Use `SCAN` with an iterator: `for key in _redis_client.scan_iter(match=pattern): _redis_client.delete(key)`. Or restructure caching to avoid pattern-based invalidation.
- **Complexity:** Low.

---

### 31. [MEDIUM / PERFORMANCE] Sequential HTTP Calls for Bulk Notifications

- **File:** `services/notification_service.py`, lines 26-33
- **Description:** `send_bulk_notifications` sends HTTP requests one at a time in a synchronous loop. Each call could take hundreds of milliseconds.
- **Impact:** Sending notifications to N users takes N * latency time. For 100 users at 200ms per call, that is 20 seconds.
- **Proposed Solution:** Use `asyncio.gather()` with `httpx.AsyncClient`, or use a thread pool (`concurrent.futures.ThreadPoolExecutor`), or enqueue notifications as background jobs.
- **Complexity:** Medium.

---

### 32. [MEDIUM / PERFORMANCE] Request Body Read on Every Request

- **File:** `api/main.py`, lines 26-28
- **Description:** The logging middleware reads and decodes the full request body for every HTTP request, including file uploads. This doubles memory usage for large payloads and may interfere with streaming.
- **Impact:** High memory usage and potential timeouts for large file uploads.
- **Proposed Solution:** Only read the body for non-file requests, or log only metadata (method, path, content-length). Consider moving request logging to a background task.
- **Complexity:** Low.

---

### 33. [LOW / PERFORMANCE] Multiple Iterations in `compute_task_stats`

- **File:** `services/task_service.py`, lines 96-115
- **Description:** The function iterates the full task list four separate times (once per status) using list comprehensions after already loading all tasks into memory.
- **Impact:** Minor inefficiency; four passes over the list instead of one.
- **Proposed Solution:** Use a single loop with a `Counter` or `defaultdict` to count statuses in one pass. Better yet, use a SQL `GROUP BY` query: `SELECT status, COUNT(*) FROM tasks WHERE owner_id = :id GROUP BY status`.
- **Complexity:** Low.

---

### 34. [LOW / ERROR HANDLING] No Timeout on Notification HTTP Requests

- **File:** `services/notification_service.py`, line 19
- **Description:** `httpx.post(WEBHOOK_URL, json=payload)` has no timeout parameter. If the Slack webhook is slow or unresponsive, the calling thread hangs indefinitely.
- **Impact:** Thread starvation; potential cascading failures.
- **Proposed Solution:** Add a timeout: `httpx.post(WEBHOOK_URL, json=payload, timeout=5.0)`.
- **Complexity:** Low.

---

### 35. [LOW / ERROR HANDLING] Silent Exception Swallowing in Notifications

- **File:** `services/notification_service.py`, lines 21-23
- **Description:** The `except Exception: return False` clause catches and silently discards all errors with no logging.
- **Impact:** Notification failures are invisible; debugging delivery issues is impossible.
- **Proposed Solution:** Log the exception before returning False: `logger.warning(f"Notification failed: {e}")`.
- **Complexity:** Low.

---

### 36. [LOW / ERROR HANDLING] Error Info Lost in Dead Letter Queue

- **File:** `services/job_processor.py`, lines 58-60
- **Description:** When a job exhausts its retries and moves to the dead letter queue, the exception that caused the failure is not recorded in the job data.
- **Impact:** Operators cannot diagnose why jobs failed by inspecting the dead letter queue.
- **Proposed Solution:** Add error info to the job: `job["last_error"] = str(e)` before pushing to the dead letter queue.
- **Complexity:** Low.

---

### 37. [LOW / CORRECTNESS] No Email Validation on Registration

- **File:** `api/routes/users.py`, line 27
- **Description:** The `UserCreate` model accepts `email: str` with no validation. Any string (including empty strings or SQL fragments) is accepted as an email.
- **Impact:** Invalid data in the database; potential downstream errors when sending emails.
- **Proposed Solution:** Use Pydantic's `EmailStr` type: `from pydantic import EmailStr; email: EmailStr`. Add the `email-validator` package to dependencies.
- **Complexity:** Low.

---

### 38. [LOW / CORRECTNESS] `datetime.utcnow()` Used as Column Default

- **File:** `models/user.py`, line 17; `models/task.py`, lines 33-34
- **Description:** `default=datetime.utcnow` is called at class definition time for each instance, but `datetime.utcnow()` is deprecated in Python 3.12+ in favor of timezone-aware `datetime.now(timezone.utc)`.
- **Impact:** Minor correctness concern; `utcnow` returns naive datetimes without timezone info, which can cause confusion in timezone-aware applications.
- **Proposed Solution:** Use `default=lambda: datetime.now(timezone.utc)` or use `server_default` with a SQL function like `func.now()`.
- **Complexity:** Low.

---

### 39. [LOW / CORRECTNESS] Caching SQLAlchemy ORM Objects

- **File:** `services/task_service.py`, lines 31-36
- **Description:** `get_task` caches SQLAlchemy ORM `Task` objects in Redis via pickle. Detached ORM objects lose their session binding and may cause `DetachedInstanceError` when accessing lazy-loaded relationships after deserialization.
- **Impact:** Potential runtime errors when accessing relationships on cached task objects.
- **Proposed Solution:** Serialize tasks to dicts or Pydantic models before caching. Deserialize back to a data transfer object, not an ORM instance.
- **Complexity:** Medium.

---

### 40. [LOW / CORRECTNESS] Job ID Not Stored in Job Data

- **File:** `services/job_processor.py`, lines 23-31
- **Description:** `enqueue_job` generates a `job_id` but does not include it in the job data pushed to Redis. The job ID is returned to the caller but cannot be used to track or cancel the job.
- **Impact:** Job tracking is broken; callers receive an ID that has no correlation to the queued job.
- **Proposed Solution:** Add `job["id"] = job_id` before pushing to Redis.
- **Complexity:** Low.

---

## Summary Table

| # | Severity | Category | File | Brief Description |
|---|----------|----------|------|-------------------|
| 1 | CRITICAL | Security | `services/task_service.py` | SQL injection in search_tasks |
| 2 | CRITICAL | Security | `api/routes/admin.py` | SQL injection in delete_user |
| 3 | CRITICAL | Security | `api/routes/admin.py` | Arbitrary SQL execution endpoint |
| 4 | CRITICAL | Security | `models/database.py` | Hardcoded database credentials |
| 5 | CRITICAL | Security | `utils/auth.py` | Hardcoded JWT secret key |
| 6 | CRITICAL | Security | `api/routes/admin.py` | Admin auth trusts JWT claims only |
| 7 | CRITICAL | Security | `utils/cache.py` | Arbitrary code exec via pickle |
| 8 | HIGH | Security | `utils/cache.py` | Redis without authentication |
| 9 | HIGH | Security | `services/notification_service.py` | Hardcoded Slack webhook URL |
| 10 | HIGH | Security | `api/routes/tasks.py` | JWT token in query parameter |
| 11 | HIGH | Security | `api/routes/users.py` | Registration returns hashed password |
| 12 | HIGH | Security | `api/routes/users.py` | User profile exposes all fields |
| 13 | HIGH | Security | `utils/auth.py` | 30-day token expiry, no revocation |
| 14 | HIGH | Security | `utils/auth.py` | MD5-based API key generation |
| 15 | HIGH | Security | `utils/auth.py` | No exception handling in decode_token |
| 16 | HIGH | Security | `api/main.py` | Wildcard CORS with credentials |
| 17 | HIGH | Security | `utils/logging.py` | Passwords logged in request body |
| 18 | HIGH | Correctness | `api/routes/admin.py` | Orphaned records on user delete |
| 19 | HIGH | Correctness | `services/notification_service.py` | Undefined variable `assignee_id` |
| 20 | MEDIUM | Correctness | `services/task_service.py` | No authorization on status update |
| 21 | MEDIUM | Correctness | `api/routes/tasks.py` | Unauthenticated task read |
| 22 | MEDIUM | Correctness | `services/task_service.py` | Division by zero in task stats |
| 23 | MEDIUM | Correctness | `utils/cache.py` | Off-by-one in cache warming |
| 24 | MEDIUM | Correctness | `tests/test_tasks.py` | Typo in import path |
| 25 | MEDIUM | Correctness | `tests/test_tasks.py` | Wrong field name in assertion |
| 26 | MEDIUM | Correctness | `services/job_processor.py` | Race conditions on shared state |
| 27 | MEDIUM | Correctness | `services/job_processor.py` | Non-daemon threads block shutdown |
| 28 | MEDIUM | Performance | `services/task_service.py` | In-memory pagination (N+1 query) |
| 29 | MEDIUM | Performance | `services/task_service.py` | Commits inside loop |
| 30 | MEDIUM | Performance | `utils/cache.py` | Redis KEYS blocks server |
| 31 | MEDIUM | Performance | `services/notification_service.py` | Sequential HTTP calls |
| 32 | MEDIUM | Performance | `api/main.py` | Request body read on every request |
| 33 | LOW | Performance | `services/task_service.py` | Multiple list iterations in stats |
| 34 | LOW | Error Handling | `services/notification_service.py` | No HTTP timeout |
| 35 | LOW | Error Handling | `services/notification_service.py` | Silent exception swallowing |
| 36 | LOW | Error Handling | `services/job_processor.py` | Error info lost in dead letter queue |
| 37 | LOW | Correctness | `api/routes/users.py` | No email validation |
| 38 | LOW | Correctness | `models/user.py`, `models/task.py` | Deprecated `datetime.utcnow()` |
| 39 | LOW | Correctness | `services/task_service.py` | Caching detached ORM objects |
| 40 | LOW | Correctness | `services/job_processor.py` | Job ID not stored in job data |

---

## Recommended Prioritization

**Immediate (deploy blockers):** Issues 1-7 -- these represent active security vulnerabilities that could lead to data breach or remote code execution. Hardcoded credentials (4, 5, 8, 9) should be rotated immediately.

**Short-term (next sprint):** Issues 8-19 -- high-severity security and correctness issues that require straightforward fixes.

**Medium-term (next 2-4 weeks):** Issues 20-32 -- correctness bugs and performance problems that affect reliability and user experience.

**Backlog:** Issues 33-40 -- low-severity items that improve code quality and maintainability.
