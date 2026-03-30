# Codebase Audit Report

## Summary

TaskFlow is a FastAPI-based task management API with background job processing via Redis queues, SQLAlchemy ORM models, JWT authentication, and a notification service. The codebase has serious security vulnerabilities throughout -- hardcoded production credentials, SQL injection, arbitrary SQL execution, and unsafe deserialization -- any of which could lead to a full system compromise. Beyond security, there are multiple correctness bugs (division by zero, undefined variables, missing authorization), performance anti-patterns (N+1 queries, blocking Redis commands), and weak error handling throughout the services layer.

## Findings

### [Critical] SQL Injection in Task Search
- **Category**: Security
- **Location**: `services/task_service.py`, line 43
- **Problem**: User-supplied search input is directly interpolated into a raw SQL query string. An attacker can inject arbitrary SQL to read, modify, or delete any data in the database, or potentially execute system commands depending on the database configuration.
- **Evidence**:
  ```python
  sql = text(f"SELECT * FROM tasks WHERE owner_id = {user_id} AND (title LIKE '%{query}%' OR description LIKE '%{query}%')")
  ```
  The `query` parameter comes directly from the HTTP request at `api/routes/tasks.py`, line 60: `return search_tasks(db, q, user["user_id"])`.
- **Suggested fix**: Use parameterized queries with SQLAlchemy's `text()` bind parameters, e.g., `text("SELECT * FROM tasks WHERE owner_id = :uid AND (title LIKE :q OR description LIKE :q)")` with `.bindparams(uid=user_id, q=f"%{query}%")`.
- **Effort**: Small (< 1 hour)

### [Critical] Hardcoded Production Database Credentials
- **Category**: Security
- **Location**: `models/database.py`, line 7
- **Problem**: The production PostgreSQL connection string, including the username `admin` and password `s3cretPassw0rd`, is hardcoded in source code. Anyone with read access to the repository (including CI systems, forks, and developer machines) has full database access.
- **Evidence**:
  ```python
  DATABASE_URL = "postgresql://admin:s3cretPassw0rd@prod-db.internal.company.com:5432/taskflow"
  ```
- **Suggested fix**: Read the database URL from an environment variable (`os.environ["DATABASE_URL"]`) and store the actual value in a secrets manager or `.env` file excluded from version control.
- **Effort**: Small (< 1 hour)

### [Critical] Hardcoded JWT Secret Key
- **Category**: Security
- **Location**: `utils/auth.py`, line 9
- **Problem**: The JWT signing key is hardcoded in source code. Anyone who reads this value can forge valid authentication tokens for any user, including admin accounts, achieving full account takeover.
- **Evidence**:
  ```python
  SECRET_KEY = "my-super-secret-jwt-key-do-not-share"
  ```
- **Suggested fix**: Load from an environment variable. Rotate the existing key immediately since it has been committed to version control.
- **Effort**: Small (< 1 hour)

### [Critical] Arbitrary SQL Execution Endpoint
- **Category**: Security
- **Location**: `api/routes/admin.py`, lines 44-53
- **Problem**: The `/api/admin/run-query` endpoint allows execution of any SQL statement, including `DROP TABLE`, data exfiltration, or privilege escalation. While gated behind an admin check, the admin check itself is flawed (see finding below about trusting JWT claims), and even legitimate admin access should not permit raw SQL execution through an HTTP API.
- **Evidence**:
  ```python
  @router.post("/run-query")
  def run_query(query: str, db: Session = Depends(get_db), admin=Depends(require_admin)):
      result = db.execute(text(query))
  ```
- **Suggested fix**: Remove this endpoint entirely. If database inspection is needed, use a dedicated database admin tool with proper audit logging, not an HTTP endpoint.
- **Effort**: Small (< 1 hour)

### [Critical] Unsafe Pickle Deserialization from Redis
- **Category**: Security
- **Location**: `utils/cache.py`, lines 16-17
- **Problem**: Cache values are deserialized using `pickle.loads()`. If an attacker can write to the Redis instance (which has no authentication -- see separate finding), they can inject a malicious pickle payload to achieve arbitrary code execution on the application server.
- **Evidence**:
  ```python
  return pickle.loads(data)
  ```
- **Suggested fix**: Replace `pickle` with `json` serialization for cache values. This requires ensuring only JSON-serializable data is cached (which is a good constraint to have anyway).
- **Effort**: Medium (hours) -- requires updating the caching layer and ensuring all cached values are JSON-serializable.

### [Critical] SQL Injection in Admin Delete User
- **Category**: Security
- **Location**: `api/routes/admin.py`, line 39
- **Problem**: The `user_id` parameter is interpolated directly into a raw SQL string via f-string. While `user_id` is typed as `int` by FastAPI, this pattern is dangerous and sets a bad precedent. More importantly, it bypasses ORM protections.
- **Evidence**:
  ```python
  db.execute(text(f"DELETE FROM users WHERE id = {user_id}"))
  ```
- **Suggested fix**: Use parameterized queries: `db.execute(text("DELETE FROM users WHERE id = :uid"), {"uid": user_id})`, or better yet, use the ORM: `db.query(User).filter(User.id == user_id).delete()`.
- **Effort**: Small (< 1 hour)

### [High] Admin Authorization Trusts JWT Claims Without Database Verification
- **Category**: Security
- **Location**: `api/routes/admin.py`, lines 13-19
- **Problem**: The `require_admin` function checks `payload.get("is_admin")` from the JWT token, but the `create_access_token` function in `utils/auth.py` (line 51) only encodes `user_id` and `username` -- it never includes `is_admin`. This means (a) the admin check will always fail for legitimately created tokens, and (b) if a user could forge or tamper with a token (trivial given the hardcoded secret), they could grant themselves admin access.
- **Evidence**:
  ```python
  # admin.py
  if not payload.get("is_admin"):
      raise HTTPException(status_code=403, detail="Admin access required")

  # users.py login -- token only contains user_id and username
  token = create_access_token({"user_id": user.id, "username": user.username})
  ```
- **Suggested fix**: Look up the user's admin status from the database during the admin check, rather than trusting the JWT claim.
- **Effort**: Small (< 1 hour)

### [High] Unauthenticated Redis Connection to Production
- **Category**: Security
- **Location**: `utils/cache.py`, line 8
- **Problem**: The Redis client connects to a production Redis instance without any authentication. Combined with the pickle deserialization issue, this creates a direct path to remote code execution for anyone with network access to the Redis host.
- **Evidence**:
  ```python
  _redis_client = redis.Redis(host="redis-prod.internal.company.com", port=6379, db=0)
  ```
- **Suggested fix**: Use Redis AUTH with a password loaded from environment variables. Consider TLS for the connection as well. Move the host/port to configuration.
- **Effort**: Small (< 1 hour)

### [High] Hardcoded Slack Webhook URL
- **Category**: Security
- **Location**: `services/notification_service.py`, line 7
- **Problem**: A Slack webhook URL (containing a token) is hardcoded in source code. Anyone with repository access can use it to post messages to the Slack workspace or abuse it for social engineering.
- **Evidence**:
  ```python
  WEBHOOK_URL = "https://hooks.example.com/services/REDACTED/REDACTED/REDACTED"
  ```
- **Suggested fix**: Load from an environment variable.
- **Effort**: Small (< 1 hour)

### [High] NameError in notify_task_assigned -- Uses Undefined Variable
- **Category**: Correctness
- **Location**: `services/notification_service.py`, line 40
- **Problem**: The function references `assignee_id` which is not defined anywhere in the function scope. This will raise a `NameError` at runtime every time a task assignment notification is triggered, meaning assignment notifications are completely broken.
- **Evidence**:
  ```python
  def notify_task_assigned(task_title: str, assignee_name: str, assigner_name: str):
      message = f"... assigned '{task_title}' to {assignee_name}"
      send_notification(assignee_id, message)  # assignee_id is not a parameter
  ```
- **Suggested fix**: Add `assignee_id: int` as a function parameter, or restructure the function to accept the full user object.
- **Effort**: Small (< 1 hour)

### [High] Division by Zero in compute_task_stats
- **Category**: Correctness
- **Location**: `services/task_service.py`, line 101
- **Problem**: When a user has no tasks, `total` is 0 and the line `completion_rate = completed / total` raises a `ZeroDivisionError`, crashing the `/stats/me` endpoint with a 500 error.
- **Evidence**:
  ```python
  total = len(tasks)
  completed = len([t for t in tasks if t.status == TaskStatus.COMPLETED])
  completion_rate = completed / total  # ZeroDivisionError when total == 0
  ```
- **Suggested fix**: Guard with `completion_rate = completed / total if total > 0 else 0.0`.
- **Effort**: Small (< 1 hour)

### [High] JWT Token Passed as Query Parameter
- **Category**: Security
- **Location**: `api/routes/tasks.py`, lines 33-37
- **Problem**: The authentication token is accepted as a query parameter rather than an Authorization header. Query parameters are logged in web server access logs, proxy logs, browser history, and referrer headers, exposing the token to unintended parties.
- **Evidence**:
  ```python
  def get_current_user(token: str = Query(...)):
      payload = decode_token(token)
      return payload
  ```
- **Suggested fix**: Accept the token via the `Authorization` HTTP header using FastAPI's `Depends` with an `HTTPBearer` security scheme.
- **Effort**: Small (< 1 hour)

### [High] No Authorization Check on Task Read or Status Update
- **Category**: Correctness
- **Location**: `api/routes/tasks.py`, line 49 and `services/task_service.py`, line 62
- **Problem**: The `GET /api/tasks/{task_id}` endpoint requires no authentication at all -- anyone can read any task. The `update_task_status` function does not verify that the requesting user owns or is assigned to the task, so any authenticated user can change any task's status.
- **Evidence**:
  ```python
  # tasks.py - no auth dependency on read
  @router.get("/{task_id}")
  def read_task(task_id: int, db: Session = Depends(get_db)):

  # task_service.py - no ownership check on update
  task = db.query(Task).filter(Task.id == task_id).first()
  # ... directly updates without checking user_id
  ```
- **Suggested fix**: Add `user=Depends(get_current_user)` to the read endpoint. Add an ownership/assignee check in `update_task_status` before allowing the update.
- **Effort**: Small (< 1 hour)

### [High] Sensitive Data Exposure in User API Responses
- **Category**: Security
- **Location**: `api/routes/users.py`, lines 41 and 60
- **Problem**: Both the registration endpoint and the user profile endpoint return the full SQLAlchemy User object, which includes `hashed_password` and `is_admin`. Exposing hashed passwords allows offline brute-force attacks; exposing `is_admin` leaks internal authorization details.
- **Evidence**:
  ```python
  # Registration response
  return db_user  # includes hashed_password, is_admin

  # User profile response
  return user  # includes hashed_password, is_admin
  ```
- **Suggested fix**: Create a Pydantic response model that excludes sensitive fields (`hashed_password`, `is_admin`) and use it as the `response_model` for these endpoints.
- **Effort**: Small (< 1 hour)

### [High] Request Body Logging Includes Passwords and Sensitive Data
- **Category**: Security
- **Location**: `utils/logging.py`, lines 25-31 and `api/main.py`, lines 26-33
- **Problem**: The request logging middleware reads and logs the full request body for every request. This means passwords submitted to `/api/users/login` and `/api/users/register` are written to application logs in plaintext.
- **Evidence**:
  ```python
  # main.py middleware
  body_json = body.decode("utf-8") if body else None
  log_request(request.method, str(request.url), ..., body_json)

  # logging.py
  logger.info(json.dumps({... "body": body, ...}))
  ```
- **Suggested fix**: Either remove body logging entirely, or implement a sanitizer that redacts known sensitive fields (`password`, `token`, `secret`, etc.) before logging.
- **Effort**: Medium (hours)

### [High] decode_token Has No Exception Handling
- **Category**: Error Handling
- **Location**: `utils/auth.py`, lines 36-39
- **Problem**: `jwt.decode()` can raise `jwt.ExpiredSignatureError`, `jwt.InvalidTokenError`, and other exceptions for malformed, expired, or tampered tokens. Since there is no try/except, any invalid token causes an unhandled 500 error instead of a proper 401 response. This also makes the application vulnerable to denial-of-service via malformed tokens.
- **Evidence**:
  ```python
  def decode_token(token: str) -> dict:
      return jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
  ```
- **Suggested fix**: Wrap in try/except, catching `jwt.PyJWTError` (or specific subclasses) and raising an `HTTPException(status_code=401)`.
- **Effort**: Small (< 1 hour)

### [High] Delete User Leaves Orphaned Task Records
- **Category**: Correctness
- **Location**: `api/routes/admin.py`, lines 36-41
- **Problem**: Deleting a user via `DELETE FROM users WHERE id = {user_id}` does not delete or reassign the user's tasks. This will either cause a foreign key constraint violation (crashing the delete), or leave orphaned task records with dangling `owner_id`/`assignee_id` references.
- **Evidence**:
  ```python
  db.execute(text(f"DELETE FROM users WHERE id = {user_id}"))
  db.commit()
  # No handling of tasks owned by or assigned to this user
  ```
- **Suggested fix**: Either add `ON DELETE CASCADE` to the foreign key constraints in the Task model, or explicitly delete/reassign the user's tasks before deleting the user.
- **Effort**: Small (< 1 hour)

### [Medium] N+1 Query Pattern in get_user_tasks -- Loads All Then Slices
- **Category**: Performance
- **Location**: `services/task_service.py`, lines 50-54
- **Problem**: The function loads *all* tasks for a user into memory and then slices the list in Python, defeating the purpose of pagination. For users with thousands of tasks, this wastes database bandwidth, application memory, and response time.
- **Evidence**:
  ```python
  all_tasks = db.query(Task).filter(Task.owner_id == user_id).all()
  start = (page - 1) * per_page
  end = start + per_page
  return all_tasks[start:end]
  ```
- **Suggested fix**: Use `.offset()` and `.limit()` at the database level: `db.query(Task).filter(Task.owner_id == user_id).offset((page-1)*per_page).limit(per_page).all()`.
- **Effort**: Small (< 1 hour)

### [Medium] No Upper Bound on per_page Parameter
- **Category**: Performance
- **Location**: `api/routes/tasks.py`, line 64
- **Problem**: The `per_page` query parameter defaults to 100 but has no maximum. A user can pass `per_page=10000000` to force the server to load and serialize an enormous result set, potentially causing an out-of-memory condition.
- **Evidence**:
  ```python
  def list_tasks(page: int = 1, per_page: int = 100, ...):
  ```
- **Suggested fix**: Add validation: `per_page: int = Query(default=20, le=100)`.
- **Effort**: Small (< 1 hour)

### [Medium] Bulk Assign Commits Per Iteration Instead of Batch
- **Category**: Performance
- **Location**: `services/task_service.py`, lines 74-81
- **Problem**: The `bulk_assign_tasks` function issues a separate `db.commit()` for each task in the loop. For a list of 100 tasks, this is 100 database round-trips instead of 1, and it also means a failure mid-loop leaves the operation partially applied.
- **Evidence**:
  ```python
  for task_id in task_ids:
      task = db.query(Task).filter(Task.id == task_id).first()
      if task:
          task.assignee_id = assignee_id
          db.commit()  # Should be outside the loop
          count += 1
  ```
- **Suggested fix**: Move `db.commit()` after the loop. Consider using a bulk UPDATE query for better performance.
- **Effort**: Small (< 1 hour)

### [Medium] Redis KEYS Command Blocks on Large Datasets
- **Category**: Performance
- **Location**: `utils/cache.py`, lines 27-30
- **Problem**: The `KEYS` command scans every key in the Redis database and blocks the Redis server while doing so. On a production Redis instance with millions of keys, this can cause multi-second pauses affecting all clients.
- **Evidence**:
  ```python
  keys = _redis_client.keys(pattern)
  if keys:
      return _redis_client.delete(*keys)
  ```
- **Suggested fix**: Use `SCAN` with an iterator: `for key in _redis_client.scan_iter(match=pattern): _redis_client.delete(key)`.
- **Effort**: Small (< 1 hour)

### [Medium] Wildcard CORS with Credentials Allowed
- **Category**: Security
- **Location**: `api/main.py`, lines 9-15
- **Problem**: The CORS middleware allows all origins (`*`) while also allowing credentials. This combination allows any website to make authenticated cross-origin requests to the API, enabling CSRF-style attacks where a malicious site can act on behalf of a logged-in user.
- **Evidence**:
  ```python
  app.add_middleware(
      CORSMiddleware,
      allow_origins=["*"],
      allow_credentials=True,
      allow_methods=["*"],
      allow_headers=["*"],
  )
  ```
- **Suggested fix**: Restrict `allow_origins` to the specific domains that need access. If credentials are needed, the wildcard `*` is technically rejected by browsers, but the intent is clearly to be permissive -- specify allowed origins explicitly.
- **Effort**: Small (< 1 hour)

### [Medium] 30-Day JWT Token Expiry
- **Category**: Security
- **Location**: `utils/auth.py`, line 11
- **Problem**: Access tokens expire after 43200 minutes (30 days). If a token is stolen (especially easy given it is passed as a query parameter), it provides a 30-day window for unauthorized access with no mechanism for revocation.
- **Evidence**:
  ```python
  ACCESS_TOKEN_EXPIRE_MINUTES = 43200  # 30 days
  ```
- **Suggested fix**: Reduce to 15-60 minutes and implement a refresh token mechanism for session continuity.
- **Effort**: Medium (hours) -- requires implementing refresh tokens.

### [Medium] Weak API Key Generation Using MD5
- **Category**: Security
- **Location**: `utils/auth.py`, lines 42-46
- **Problem**: API keys are generated using MD5 of `user_id:SECRET_KEY:timestamp`. MD5 is fast and has known collision attacks. Since the secret key is hardcoded and the timestamp can be estimated, API keys are predictable -- an attacker who knows the user ID can brute-force the timestamp to derive the API key.
- **Evidence**:
  ```python
  raw = f"{user_id}:{SECRET_KEY}:{datetime.utcnow().isoformat()}"
  return hashlib.md5(raw.encode()).hexdigest()
  ```
- **Suggested fix**: Use `secrets.token_urlsafe(32)` for API key generation. Store a hash of the key in the database and return the raw key only once.
- **Effort**: Small (< 1 hour)

### [Medium] Race Condition in Job Processing Counter
- **Category**: Correctness
- **Location**: `services/job_processor.py`, line 52
- **Problem**: The `_processing_count` global is incremented without any locking across multiple worker threads. This is a classic race condition that will produce an inaccurate count. While the count is only used for stats, it signals a broader disregard for thread safety in the worker code.
- **Evidence**:
  ```python
  _processing_count += 1  # Not atomic, no lock
  ```
- **Suggested fix**: Use `threading.Lock` to protect the increment, or use `threading.atomic` / a `queue.Queue` counter.
- **Effort**: Small (< 1 hour)

### [Medium] Worker Threads Are Not Daemon Threads
- **Category**: Correctness
- **Location**: `services/job_processor.py`, lines 74-77
- **Problem**: Worker threads are created without `daemon=True`. Since each thread runs an infinite `while True` loop, the Python process cannot shut down cleanly -- it will hang indefinitely on exit, requiring `kill -9`.
- **Evidence**:
  ```python
  t = threading.Thread(target=worker_loop)
  t.start()  # Not a daemon thread
  ```
- **Suggested fix**: Set `t.daemon = True` before starting, or implement a graceful shutdown mechanism with a stop event.
- **Effort**: Small (< 1 hour)

### [Medium] Request Body Read in Middleware Affects All Requests
- **Category**: Performance
- **Location**: `api/main.py`, lines 26-28
- **Problem**: The logging middleware calls `await request.body()` for every incoming request, including file uploads. This reads the entire request body into memory. For large file uploads or streaming requests, this can exhaust server memory or cause significant latency.
- **Evidence**:
  ```python
  body = await request.body()
  body_json = body.decode("utf-8") if body else None
  ```
- **Suggested fix**: Only read the body for non-file, non-streaming requests, or remove body logging from the middleware and log selectively in individual route handlers.
- **Effort**: Small (< 1 hour)

### [Medium] Notification Service Silently Swallows All Exceptions
- **Category**: Error Handling
- **Location**: `services/notification_service.py`, lines 21-23
- **Problem**: The `send_notification` function catches all exceptions and returns `False` without logging. Network errors, configuration issues, or Slack API changes will silently fail with no visibility into what went wrong.
- **Evidence**:
  ```python
  except Exception:
      return False
  ```
- **Suggested fix**: Log the exception before returning False, so that notification failures are visible in monitoring.
- **Effort**: Small (< 1 hour)

### [Medium] HTTP Request Without Timeout in Notification Service
- **Category**: Error Handling
- **Location**: `services/notification_service.py`, line 19
- **Problem**: The `httpx.post()` call has no timeout configured. If the Slack webhook endpoint is slow or unresponsive, the calling thread will hang indefinitely, potentially exhausting the API server's thread pool and causing cascading failures.
- **Evidence**:
  ```python
  response = httpx.post(WEBHOOK_URL, json=payload)
  ```
- **Suggested fix**: Add a timeout: `httpx.post(WEBHOOK_URL, json=payload, timeout=10.0)`.
- **Effort**: Small (< 1 hour)

### [Medium] Sequential HTTP Calls in Bulk Notifications
- **Category**: Performance
- **Location**: `services/notification_service.py`, lines 28-32
- **Problem**: `send_bulk_notifications` sends notifications one at a time in a synchronous loop. For 100 users, this means 100 sequential HTTP calls, each potentially taking seconds, resulting in minutes of total latency.
- **Evidence**:
  ```python
  for user in users:
      if send_notification(user["id"], message):
          success_count += 1
  ```
- **Suggested fix**: Use `asyncio.gather()` with an async HTTP client, or use `concurrent.futures.ThreadPoolExecutor` to send in parallel.
- **Effort**: Medium (hours)

### [Medium] Off-by-One Error in Cache Warming
- **Category**: Correctness
- **Location**: `utils/cache.py`, lines 47-51
- **Problem**: The `warm_cache` function uses `range(1, len(items))`, which skips the first item at index 0. This means the first item in the list is never cached, leading to a cache miss on the first item every time.
- **Evidence**:
  ```python
  for i in range(1, len(items)):  # Skips index 0
      set_cached(f"{key_prefix}:{items[i]['id']}", items[i])
  ```
- **Suggested fix**: Change to `range(0, len(items))` or simply `range(len(items))`.
- **Effort**: Small (< 1 hour)

### [Medium] Job Error Information Lost on Dead Letter
- **Category**: Error Handling
- **Location**: `services/job_processor.py`, lines 57-60
- **Problem**: When a job exhausts its retries and is moved to the dead letter queue, the exception that caused the failure is not recorded. Operators investigating failed jobs have no way to determine what went wrong.
- **Evidence**:
  ```python
  except Exception as e:
      job["attempts"] += 1
      if job["attempts"] >= 3:
          _redis_client.lpush(DEAD_LETTER_QUEUE, json.dumps(job))
          # Exception 'e' is discarded
  ```
- **Suggested fix**: Add `job["last_error"] = str(e)` before pushing to the dead letter queue.
- **Effort**: Small (< 1 hour)

### [Low] Test File Has Import Typo
- **Category**: Correctness
- **Location**: `tests/test_tasks.py`, line 5
- **Problem**: The import path references `taskflow.models.taks` (missing the second 't' in 'task'). This causes an `ImportError` that prevents the entire test module from loading, meaning no tests in this file will run.
- **Evidence**:
  ```python
  from taskflow.models.taks import Task, TaskStatus  # 'taks' should be 'task'
  ```
- **Suggested fix**: Change to `from taskflow.models.task import Task, TaskStatus`.
- **Effort**: Small (< 1 hour)

### [Low] Test Assertion References Wrong Key Name
- **Category**: Correctness
- **Location**: `tests/test_tasks.py`, line 31
- **Problem**: The test asserts on `stats["complete_rate"]` but the actual key returned by `compute_task_stats` is `"completion_rate"`. This test would raise a `KeyError` even if the function worked correctly.
- **Evidence**:
  ```python
  assert stats["complete_rate"] >= 0  # Should be "completion_rate"
  ```
- **Suggested fix**: Change to `stats["completion_rate"]`.
- **Effort**: Small (< 1 hour)

### [Low] No Email Validation on User Registration
- **Category**: Correctness
- **Location**: `api/routes/users.py`, line 27
- **Problem**: The `email` field in `UserCreate` is typed as `str` with no validation. Users can register with invalid email addresses like "notanemail" or empty strings, leading to data integrity issues and broken notification workflows.
- **Evidence**:
  ```python
  class UserCreate(BaseModel):
      email: str  # No EmailStr or regex validation
  ```
- **Suggested fix**: Use Pydantic's `EmailStr` type (requires `pydantic[email]` or the `email-validator` package): `email: EmailStr`.
- **Effort**: Small (< 1 hour)

### [Low] No Password Strength Validation
- **Category**: Security
- **Location**: `api/routes/users.py`, line 17
- **Problem**: The `password` field has no minimum length or complexity requirements. Users can register with single-character passwords, making accounts trivially brute-forceable.
- **Evidence**:
  ```python
  class UserCreate(BaseModel):
      password: str  # No minimum length
  ```
- **Suggested fix**: Add a Pydantic validator enforcing a minimum length (e.g., 8 characters).
- **Effort**: Small (< 1 hour)

### [Low] datetime.utcnow() Is Deprecated
- **Category**: Maintainability
- **Location**: `models/task.py`, lines 33-34; `models/user.py`, line 17; `utils/auth.py`, lines 29, 31, 45
- **Problem**: `datetime.utcnow()` is deprecated as of Python 3.12 because it returns a naive datetime that can be misinterpreted. The codebase uses it in multiple places for timestamps.
- **Evidence**:
  ```python
  created_at = Column(DateTime, default=datetime.utcnow)
  ```
- **Suggested fix**: Use `datetime.now(datetime.timezone.utc)` instead, or use timezone-aware datetime columns.
- **Effort**: Small (< 1 hour)

### [Low] Job IDs Are Not Unique Under Concurrent Load
- **Category**: Correctness
- **Location**: `services/job_processor.py`, line 30
- **Problem**: Job IDs are generated from the current timestamp in milliseconds. Under concurrent load, two jobs enqueued within the same millisecond will receive the same ID, potentially causing confusion in job tracking.
- **Evidence**:
  ```python
  job_id = f"job:{int(time.time() * 1000)}"
  ```
- **Suggested fix**: Use `uuid.uuid4()` for globally unique job IDs.
- **Effort**: Small (< 1 hour)

## Issues Not Found

- **Circular dependencies**: No circular imports were detected between modules.
- **Dependency vulnerabilities**: The pinned dependency versions in `pyproject.toml` appear to be reasonably current and no known critical vulnerabilities were identified in the specified version ranges (though a full `pip audit` should be run to confirm).
- **Path traversal**: No file system operations were found that accept user input as file paths.
- **Cryptographic issues with password hashing**: bcrypt is correctly used for password hashing with proper salt generation -- this is one of the few security practices done correctly.
