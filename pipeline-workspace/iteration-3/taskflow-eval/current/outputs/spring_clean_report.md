# Codebase Audit Report

## Summary

TaskFlow is a FastAPI-based task management API with background job processing via Redis queues, supporting user authentication, task CRUD, and async job execution. The codebase has serious security vulnerabilities throughout -- hardcoded production credentials, SQL injection, unsafe deserialization, and sensitive data exposure in logs and API responses -- that collectively make it unsuitable for production deployment. The most critical finding is a SQL injection vulnerability in the task search endpoint that allows any authenticated user to execute arbitrary SQL against the database.

## Findings

### [Critical] SQL injection in task search endpoint
- **Category**: Security
- **Location**: `services/task_service.py`, line 43
- **Problem**: User-supplied search input is concatenated directly into a raw SQL query string without any sanitization or parameterization. An authenticated user can inject arbitrary SQL to read, modify, or delete any data in the database, including other users' credentials.
- **Evidence**:
  ```python
  sql = text(f"SELECT * FROM tasks WHERE owner_id = {user_id} AND (title LIKE '%{query}%' OR description LIKE '%{query}%')")
  ```
  A search query like `'; DROP TABLE users; --` would be executed as SQL.
- **Suggested fix**: Use SQLAlchemy parameterized queries or ORM filters instead of string interpolation. Replace with `db.query(Task).filter(Task.owner_id == user_id, or_(Task.title.ilike(f"%{query}%"), Task.description.ilike(f"%{query}%")))`.
- **Effort**: Small (< 1 hour)

### [Critical] Hardcoded production database credentials in source code
- **Category**: Security
- **Location**: `models/database.py`, line 7
- **Problem**: The database connection string contains a plaintext username and password for a production database. Anyone with access to the repository can connect directly to the production database. If the repository is or ever becomes public, the credentials are immediately compromised.
- **Evidence**:
  ```python
  DATABASE_URL = "postgresql://admin:s3cretPassw0rd@prod-db.internal.company.com:5432/taskflow"
  ```
- **Suggested fix**: Read `DATABASE_URL` from an environment variable using `os.environ["DATABASE_URL"]` or a secrets manager. Remove the hardcoded string and rotate the exposed credentials immediately.
- **Effort**: Small (< 1 hour)

### [Critical] Hardcoded JWT secret key
- **Category**: Security
- **Location**: `utils/auth.py`, line 9
- **Problem**: The JWT signing secret is hardcoded in source code. Anyone with repository access can forge valid JWT tokens for any user, including admin accounts, gaining full access to the system.
- **Evidence**:
  ```python
  SECRET_KEY = "my-super-secret-jwt-key-do-not-share"
  ```
- **Suggested fix**: Load the secret key from an environment variable or secrets manager. Rotate the key immediately since it is committed to source control.
- **Effort**: Small (< 1 hour)

### [Critical] Unsafe pickle deserialization from Redis cache
- **Category**: Security
- **Location**: `utils/cache.py`, lines 16-17
- **Problem**: `pickle.loads()` is used to deserialize data retrieved from Redis. If an attacker gains access to the Redis instance (which has no authentication -- see separate finding), they can inject a malicious pickle payload that executes arbitrary code on the application server when deserialized.
- **Evidence**:
  ```python
  return pickle.loads(data)
  ```
- **Suggested fix**: Replace pickle serialization with JSON (`json.dumps`/`json.loads`) for cache values. If complex object serialization is needed, use a safe serialization format and validate the data schema on load.
- **Effort**: Medium (hours)

### [Critical] Arbitrary SQL execution endpoint
- **Category**: Security
- **Location**: `api/routes/admin.py`, lines 44-53
- **Problem**: The `/api/admin/run-query` endpoint allows executing arbitrary SQL queries against the database. Even though it requires admin authentication, the admin check relies solely on a JWT claim (see separate finding about JWT trust), meaning any user who can forge a token can run arbitrary SQL. Even for legitimate admins, this is a direct path to data exfiltration, modification, or destruction.
- **Evidence**:
  ```python
  @router.post("/run-query")
  def run_query(query: str, db: Session = Depends(get_db), admin=Depends(require_admin)):
      result = db.execute(text(query))
  ```
- **Suggested fix**: Remove this endpoint entirely. If admin SQL access is needed, it should be provided through a separate, audited tool with query allowlisting, not through the application API.
- **Effort**: Small (< 1 hour)

### [Critical] SQL injection in admin delete user endpoint
- **Category**: Security
- **Location**: `api/routes/admin.py`, line 39
- **Problem**: The user deletion endpoint constructs a SQL query by directly interpolating the `user_id` parameter into a raw SQL string. While `user_id` is typed as `int` by FastAPI (which provides some protection), this pattern is dangerous and inconsistent with safe coding practices. If the type constraint were ever loosened or bypassed, it would be a direct SQL injection vector.
- **Evidence**:
  ```python
  db.execute(text(f"DELETE FROM users WHERE id = {user_id}"))
  ```
- **Suggested fix**: Use parameterized queries: `db.execute(text("DELETE FROM users WHERE id = :id"), {"id": user_id})`, or use the ORM: `db.query(User).filter(User.id == user_id).delete()`.
- **Effort**: Small (< 1 hour)

### [High] Request body logging exposes passwords and sensitive data
- **Category**: Security
- **Location**: `utils/logging.py`, lines 24-31; `api/main.py`, lines 23-34
- **Problem**: The HTTP middleware reads and logs the full request body for every request, including POST requests to `/api/users/register` and `/api/users/login`. This means plaintext passwords are written to application logs. The `log_error` function (line 40) also dumps arbitrary context dictionaries to logs without filtering.
- **Evidence**:
  ```python
  # In api/main.py:
  log_request(request.method, str(request.url), getattr(request.state, "user_id", 0), body_json)

  # In utils/logging.py:
  logger.info(json.dumps({... "body": body, ...}))
  ```
- **Suggested fix**: Implement a body sanitizer that redacts fields like `password`, `token`, `secret`, and `hashed_password` before logging. Alternatively, do not log request bodies for authentication endpoints. Also add filtering to `log_error` context output.
- **Effort**: Medium (hours)

### [High] User API endpoints expose hashed passwords and admin status
- **Category**: Security
- **Location**: `api/routes/users.py`, lines 40-41 and lines 57-61
- **Problem**: The registration endpoint returns the full User ORM object (including `hashed_password` and `is_admin` fields) in the response. The `GET /api/users/{user_id}` endpoint does the same and requires no authentication at all -- anyone can query any user's data including their hashed password.
- **Evidence**:
  ```python
  # Line 41 (register):
  return db_user  # Returns full ORM object including hashed_password

  # Line 61 (get_user):
  return user  # Exposes all fields with no auth check
  ```
- **Suggested fix**: Create a Pydantic response model (e.g., `UserResponse`) that includes only `id`, `email`, `username`, and `created_at`. Use it as the `response_model` for both endpoints. Add authentication to the `GET` endpoint.
- **Effort**: Small (< 1 hour)

### [High] JWT token passed as query parameter
- **Category**: Security
- **Location**: `api/routes/tasks.py`, lines 33-37
- **Problem**: Authentication tokens are passed as URL query parameters rather than in the `Authorization` header. Query parameters are logged in web server access logs, browser history, referrer headers, and proxy logs -- all of which become credential leaks. Combined with the 30-day token expiry, this creates a wide attack window.
- **Evidence**:
  ```python
  def get_current_user(token: str = Query(...)):
      payload = decode_token(token)
      return payload
  ```
- **Suggested fix**: Accept the token via the `Authorization: Bearer <token>` header instead. Use FastAPI's `Depends` with a custom security scheme or `HTTPBearer`.
- **Effort**: Small (< 1 hour)

### [High] Admin authorization trusts JWT claims without database verification
- **Category**: Security
- **Location**: `api/routes/admin.py`, lines 13-19
- **Problem**: The `require_admin` function checks the `is_admin` claim from the JWT token but never verifies it against the database. If a user's admin status is revoked in the database, their existing token still grants admin access. Combined with the hardcoded JWT secret and 30-day expiry, this is particularly dangerous.
- **Evidence**:
  ```python
  def require_admin(token: str):
      payload = decode_token(token)
      if not payload.get("is_admin"):
          raise HTTPException(status_code=403, detail="Admin access required")
      return payload
  ```
- **Suggested fix**: Query the database to verify the user's current `is_admin` status. Cache this check briefly if performance is a concern, but always verify against the authoritative source.
- **Effort**: Small (< 1 hour)

### [High] Unauthenticated Redis connection to production
- **Category**: Security
- **Location**: `utils/cache.py`, line 8
- **Problem**: The Redis client connects to a production Redis instance with no authentication (no password) and no TLS. The hostname is also hardcoded. Anyone on the internal network can access the Redis instance, and since pickle is used for serialization, this is a direct path to remote code execution.
- **Evidence**:
  ```python
  _redis_client = redis.Redis(host="redis-prod.internal.company.com", port=6379, db=0)
  ```
- **Suggested fix**: Configure Redis with authentication (`password` parameter), use TLS (`ssl=True`), and load the connection details from environment variables.
- **Effort**: Small (< 1 hour)

### [High] NameError in notify_task_assigned -- references undefined variable
- **Category**: Correctness
- **Location**: `services/notification_service.py`, lines 38-40
- **Problem**: The function `notify_task_assigned` references the variable `assignee_id` which is never defined. This function will raise a `NameError` every time it is called, meaning task assignment notifications are completely broken.
- **Evidence**:
  ```python
  def notify_task_assigned(task_title: str, assignee_name: str, assigner_name: str):
      message = f"... assigned '{task_title}' to {assignee_name}"
      send_notification(assignee_id, message)  # assignee_id is not a parameter or local variable
  ```
- **Suggested fix**: Add `assignee_id: int` as a parameter to the function signature, and update all call sites to pass it.
- **Effort**: Small (< 1 hour)

### [High] Division by zero in compute_task_stats
- **Category**: Correctness
- **Location**: `services/task_service.py`, line 101
- **Problem**: When a user has no tasks, `total` is 0 and the expression `completed / total` raises a `ZeroDivisionError`. This crashes the `GET /api/tasks/stats/me` endpoint for any user who has not created any tasks.
- **Evidence**:
  ```python
  total = len(tasks)
  completed = len([t for t in tasks if t.status == TaskStatus.COMPLETED])
  completion_rate = completed / total  # ZeroDivisionError when total == 0
  ```
- **Suggested fix**: Add a guard: `completion_rate = (completed / total) if total > 0 else 0.0`.
- **Effort**: Small (< 1 hour)

### [High] No authorization check on task status updates
- **Category**: Correctness
- **Location**: `services/task_service.py`, lines 57-68
- **Problem**: The `update_task_status` function accepts a `user_id` parameter but never uses it to verify that the requesting user owns or is assigned to the task. Any authenticated user can change the status of any task in the system.
- **Evidence**:
  ```python
  def update_task_status(db: Session, task_id: int, new_status: str, user_id: int) -> Optional[Task]:
      task = db.query(Task).filter(Task.id == task_id).first()
      if not task:
          return None
      # No check that user_id matches task.owner_id or task.assignee_id
      task.status = TaskStatus[new_status]
  ```
- **Suggested fix**: Add an authorization check: `if task.owner_id != user_id and task.assignee_id != user_id: raise HTTPException(403, "Not authorized")`.
- **Effort**: Small (< 1 hour)

### [High] Unauthenticated task read endpoint
- **Category**: Correctness
- **Location**: `api/routes/tasks.py`, lines 48-54
- **Problem**: The `GET /api/tasks/{task_id}` endpoint requires no authentication. Any anonymous user can read any task by guessing or iterating task IDs, potentially exposing sensitive task data.
- **Evidence**:
  ```python
  @router.get("/{task_id}")
  def read_task(task_id: int, db: Session = Depends(get_db)):
      # No user=Depends(get_current_user)
      task = get_task(db, task_id)
  ```
- **Suggested fix**: Add `user=Depends(get_current_user)` and verify the user has permission to view the task.
- **Effort**: Small (< 1 hour)

### [High] Wildcard CORS with credentials enabled
- **Category**: Security
- **Location**: `api/main.py`, lines 9-15
- **Problem**: The CORS middleware allows all origins (`*`) while also allowing credentials. This configuration allows any website to make authenticated cross-origin requests to the API, enabling cross-site request forgery and credential theft. Note: most browsers block `allow_credentials=True` with `allow_origins=["*"]`, but the intent is clearly misconfigured and some clients may not enforce this.
- **Evidence**:
  ```python
  app.add_middleware(
      CORSMiddleware,
      allow_origins=["*"],
      allow_credentials=True,
  )
  ```
- **Suggested fix**: Restrict `allow_origins` to the specific domains that should access the API (e.g., `["https://app.taskflow.com"]`).
- **Effort**: Small (< 1 hour)

### [High] JWT decode has no error handling -- malformed tokens crash the app
- **Category**: Error Handling
- **Location**: `utils/auth.py`, lines 36-39
- **Problem**: The `decode_token` function does not catch exceptions from `jwt.decode()`. An expired token raises `jwt.ExpiredSignatureError`, a malformed token raises `jwt.DecodeError`, and other invalid tokens raise `jwt.InvalidTokenError`. Since this function is called for every authenticated request, any of these unhandled exceptions result in a 500 Internal Server Error instead of a proper 401 response.
- **Evidence**:
  ```python
  def decode_token(token: str) -> dict:
      return jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
  ```
- **Suggested fix**: Wrap in a try/except that catches `jwt.PyJWTError` and raises `HTTPException(status_code=401, detail="Invalid or expired token")`.
- **Effort**: Small (< 1 hour)

### [High] 30-day JWT token expiry
- **Category**: Security
- **Location**: `utils/auth.py`, line 11
- **Problem**: Access tokens expire after 43200 minutes (30 days). If a token is leaked (which is likely given the query-parameter auth and request body logging issues), it remains valid for an entire month with no mechanism for revocation.
- **Evidence**:
  ```python
  ACCESS_TOKEN_EXPIRE_MINUTES = 43200  # 30 days
  ```
- **Suggested fix**: Reduce to 15-60 minutes and implement a refresh token mechanism for longer sessions. Add a token revocation list for compromised tokens.
- **Effort**: Medium (hours)

### [High] MD5-based API key generation is weak and predictable
- **Category**: Security
- **Location**: `utils/auth.py`, lines 42-46
- **Problem**: API keys are generated using MD5 of a concatenation of `user_id`, the JWT secret key, and the current timestamp. MD5 is cryptographically broken. Additionally, because the secret key is hardcoded and known, and the timestamp can be guessed, an attacker can predict or brute-force API keys.
- **Evidence**:
  ```python
  def generate_api_key(user_id: int) -> str:
      raw = f"{user_id}:{SECRET_KEY}:{datetime.utcnow().isoformat()}"
      return hashlib.md5(raw.encode()).hexdigest()
  ```
- **Suggested fix**: Use `secrets.token_urlsafe(32)` to generate cryptographically random API keys, and store a hash of each key in the database for verification.
- **Effort**: Small (< 1 hour)

### [Medium] Admin user deletion leaves orphaned task records
- **Category**: Correctness
- **Location**: `api/routes/admin.py`, lines 36-41
- **Problem**: Deleting a user via `DELETE /api/admin/users/{user_id}` removes only the user row but does not delete or reassign their tasks. This leaves orphaned records with `owner_id` and `assignee_id` pointing to non-existent users, which will cause issues when those tasks are queried or displayed.
- **Evidence**:
  ```python
  db.execute(text(f"DELETE FROM users WHERE id = {user_id}"))
  db.commit()
  return {"deleted": True}
  # No deletion of tasks where owner_id = user_id or assignee_id = user_id
  ```
- **Suggested fix**: Delete or reassign associated tasks before deleting the user, or add a `CASCADE` foreign key constraint at the database level.
- **Effort**: Small (< 1 hour)

### [Medium] N+1 query pattern in get_user_tasks -- loads all tasks then paginates in Python
- **Category**: Performance
- **Location**: `services/task_service.py`, lines 48-54
- **Problem**: The function fetches all of a user's tasks from the database and then slices the list in Python to implement pagination. For a user with thousands of tasks, this loads all rows into memory on every page request. The database performs the full scan regardless of which page is requested.
- **Evidence**:
  ```python
  all_tasks = db.query(Task).filter(Task.owner_id == user_id).all()
  start = (page - 1) * per_page
  end = start + per_page
  return all_tasks[start:end]
  ```
- **Suggested fix**: Use SQL-level pagination: `db.query(Task).filter(Task.owner_id == user_id).offset((page - 1) * per_page).limit(per_page).all()`.
- **Effort**: Small (< 1 hour)

### [Medium] No upper bound on per_page parameter
- **Category**: Performance
- **Location**: `api/routes/tasks.py`, line 64
- **Problem**: The `per_page` parameter for task listing defaults to 100 but has no maximum constraint. A client can pass `per_page=1000000` to force the server to load an arbitrary number of rows, causing memory exhaustion or slow responses. Combined with the Python-side pagination issue, this loads all rows regardless.
- **Evidence**:
  ```python
  def list_tasks(page: int = 1, per_page: int = 100, ...):
      return get_user_tasks(db, user["user_id"], page, per_page)
  ```
- **Suggested fix**: Clamp `per_page` to a reasonable maximum: `per_page = min(per_page, 100)`.
- **Effort**: Small (< 1 hour)

### [Medium] Bulk task assignment commits inside loop
- **Category**: Performance
- **Location**: `services/task_service.py`, lines 71-81
- **Problem**: The `bulk_assign_tasks` function issues a separate database query and commit for each task in the list. Assigning 100 tasks results in 100 queries and 100 commits instead of a single bulk update.
- **Evidence**:
  ```python
  for task_id in task_ids:
      task = db.query(Task).filter(Task.id == task_id).first()
      if task:
          task.assignee_id = assignee_id
          db.commit()  # Commits on every iteration
          count += 1
  ```
- **Suggested fix**: Use a single bulk update query: `db.query(Task).filter(Task.id.in_(task_ids)).update({"assignee_id": assignee_id}, synchronize_session="fetch")` followed by one `db.commit()`.
- **Effort**: Small (< 1 hour)

### [Medium] Redis KEYS command blocks on large datasets
- **Category**: Performance
- **Location**: `utils/cache.py`, lines 27-30
- **Problem**: The `invalidate_pattern` function uses the Redis `KEYS` command, which scans every key in the database and blocks the Redis server during execution. In production with millions of keys, this can cause latency spikes for all Redis clients.
- **Evidence**:
  ```python
  keys = _redis_client.keys(pattern)
  if keys:
      return _redis_client.delete(*keys)
  ```
- **Suggested fix**: Use `SCAN` with an iterator pattern instead of `KEYS`: `for key in _redis_client.scan_iter(match=pattern): _redis_client.delete(key)`.
- **Effort**: Small (< 1 hour)

### [Medium] Race condition in job processor -- shared mutable counter without locking
- **Category**: Correctness
- **Location**: `services/job_processor.py`, lines 13 and 52
- **Problem**: The `_processing_count` global variable is incremented from multiple worker threads without any locking or atomic operations. This causes a data race where concurrent increments can lose counts. The `_job_handlers` dictionary is also modified at registration time and read during processing without synchronization.
- **Evidence**:
  ```python
  _processing_count = 0
  ...
  _processing_count += 1  # Not atomic, no lock
  ```
- **Suggested fix**: Use `threading.Lock` for `_processing_count` updates, or use `itertools.count()` / `threading` atomics. Ensure handler registration completes before workers start.
- **Effort**: Small (< 1 hour)

### [Medium] Worker threads are not daemon threads -- prevent clean shutdown
- **Category**: Correctness
- **Location**: `services/job_processor.py`, lines 74-77
- **Problem**: Worker threads are started as non-daemon threads with infinite loops. When the main process attempts to exit, these threads keep running, preventing a clean shutdown. The process must be forcefully killed.
- **Evidence**:
  ```python
  t = threading.Thread(target=worker_loop)
  t.start()
  # worker_loop contains: while True: ...
  ```
- **Suggested fix**: Set `t.daemon = True` or implement a shutdown signal (e.g., a `threading.Event`) that the worker loop checks on each iteration.
- **Effort**: Small (< 1 hour)

### [Medium] Notification service swallows all HTTP exceptions silently
- **Category**: Error Handling
- **Location**: `services/notification_service.py`, lines 21-23
- **Problem**: The `send_notification` function catches all exceptions and returns `False` with no logging. If the webhook is misconfigured, the network is down, or any other error occurs, failures are completely invisible. The HTTP call also has no timeout, so it can hang indefinitely.
- **Evidence**:
  ```python
  try:
      response = httpx.post(WEBHOOK_URL, json=payload)
      return response.status_code == 200
  except Exception:
      return False
  ```
- **Suggested fix**: Add a timeout (`timeout=10`), log the exception before returning `False`, and consider adding retry logic for transient failures.
- **Effort**: Small (< 1 hour)

### [Medium] Sequential HTTP calls in bulk notifications
- **Category**: Performance
- **Location**: `services/notification_service.py`, lines 26-32
- **Problem**: `send_bulk_notifications` sends HTTP requests sequentially in a loop. Each request can take seconds (especially with no timeout). Notifying 100 users means 100 serial HTTP requests, potentially taking minutes.
- **Evidence**:
  ```python
  for user in users:
      if send_notification(user["id"], message):
          success_count += 1
  ```
- **Suggested fix**: Use `asyncio.gather()` with `httpx.AsyncClient` or `concurrent.futures.ThreadPoolExecutor` to send notifications in parallel.
- **Effort**: Medium (hours)

### [Medium] Hardcoded webhook URL with token
- **Category**: Security
- **Location**: `services/notification_service.py`, line 7
- **Problem**: The webhook URL (which likely contains authentication tokens in the path segments marked as REDACTED) is hardcoded in source code. This credential would be exposed to anyone with repository access.
- **Evidence**:
  ```python
  WEBHOOK_URL = "https://hooks.example.com/services/REDACTED/REDACTED/REDACTED"
  ```
- **Suggested fix**: Load the webhook URL from an environment variable.
- **Effort**: Small (< 1 hour)

### [Medium] Request body decoded for every request including file uploads
- **Category**: Performance
- **Location**: `api/main.py`, lines 26-28
- **Problem**: The logging middleware reads and decodes the entire request body for every incoming request, including potentially large file uploads. This doubles memory usage for upload requests and can cause significant performance degradation.
- **Evidence**:
  ```python
  body = await request.body()
  try:
      body_json = body.decode("utf-8") if body else None
  ```
- **Suggested fix**: Skip body reading for non-JSON content types or for requests exceeding a size threshold. Check `Content-Type` before attempting to read the body.
- **Effort**: Small (< 1 hour)

### [Low] Off-by-one error in cache warming function
- **Category**: Correctness
- **Location**: `utils/cache.py`, lines 47-51
- **Problem**: The `warm_cache` function iterates starting from index 1 (`range(1, len(items))`), which skips the first item in the list. The first item is never cached.
- **Evidence**:
  ```python
  for i in range(1, len(items)):  # Skips items[0]
      set_cached(f"{key_prefix}:{items[i]['id']}", items[i])
  ```
- **Suggested fix**: Change to `range(0, len(items))` or simply `range(len(items))`.
- **Effort**: Small (< 1 hour)

### [Low] Test file has import typo -- tests cannot run
- **Category**: Correctness
- **Location**: `tests/test_tasks.py`, line 5
- **Problem**: The test file imports from `taskflow.models.taks` (missing the 's') instead of `taskflow.models.task`. This causes an `ImportError` that prevents any tests in the file from running.
- **Evidence**:
  ```python
  from taskflow.models.taks import Task, TaskStatus  # 'taks' instead of 'task'
  ```
- **Suggested fix**: Change to `from taskflow.models.task import Task, TaskStatus`.
- **Effort**: Small (< 1 hour)

### [Low] Test asserts wrong field name
- **Category**: Correctness
- **Location**: `tests/test_tasks.py`, line 31
- **Problem**: The test checks `stats["complete_rate"]` but the actual field returned by `compute_task_stats` is `"completion_rate"`. This test will always fail with a `KeyError` even if the underlying function works correctly.
- **Evidence**:
  ```python
  assert stats["complete_rate"] >= 0  # Should be "completion_rate"
  ```
- **Suggested fix**: Change to `assert stats["completion_rate"] >= 0`.
- **Effort**: Small (< 1 hour)

### [Low] Dead letter queue loses error information
- **Category**: Error Handling
- **Location**: `services/job_processor.py`, lines 57-60
- **Problem**: When a job fails after 3 attempts and is moved to the dead letter queue, the exception that caused the failure is not recorded. This makes debugging failed jobs difficult because there is no record of why they failed.
- **Evidence**:
  ```python
  except Exception as e:
      job["attempts"] += 1
      if job["attempts"] >= 3:
          _redis_client.lpush(DEAD_LETTER_QUEUE, json.dumps(job))
          # 'e' is captured but never stored in the job or logged
  ```
- **Suggested fix**: Add `job["last_error"] = str(e)` before pushing to the dead letter queue, and log the exception.
- **Effort**: Small (< 1 hour)

## Issues Not Found

- **Circular dependencies**: No circular import patterns were detected across the module structure. The dependency graph flows cleanly from routes to services to models/utils.
- **Insecure cryptographic practices for password hashing**: Password hashing correctly uses bcrypt with salt generation, which is a strong choice.
- **Dependency vulnerabilities**: The specified dependency versions in `pyproject.toml` are reasonable and recent. No known-bad versions were identified in the pinned ranges.
- **Dead code**: No significant unreachable code branches or entirely unused functions were found (aside from `generate_api_key` which may or may not be used externally).
