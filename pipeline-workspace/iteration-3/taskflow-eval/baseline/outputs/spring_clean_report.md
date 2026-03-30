# Codebase Audit Report

## Summary
TaskFlow is a FastAPI-based task management API with background job processing via Redis queues, SQLAlchemy ORM models, JWT authentication, and a notification webhook integration. The codebase has serious security vulnerabilities throughout -- hardcoded production credentials, SQL injection, arbitrary SQL execution, and sensitive data exposure in logs and API responses. The most urgent finding is the combination of hardcoded database credentials and an admin endpoint that permits arbitrary SQL execution, which together could enable complete data exfiltration or destruction.

## Findings

### [Critical] SQL Injection in Task Search
- **Category**: Security
- **Location**: `services/task_service.py`, line 43
- **Problem**: User-supplied search input is directly interpolated into a raw SQL query string. An attacker can inject arbitrary SQL to read, modify, or delete any data in the database.
- **Evidence**:
  ```python
  sql = text(f"SELECT * FROM tasks WHERE owner_id = {user_id} AND (title LIKE '%{query}%' OR description LIKE '%{query}%')")
  ```
  The `query` parameter originates from the `/search/` API route (`api/routes/tasks.py`, line 60) where user input is passed directly without sanitization.
- **Suggested fix**: Use parameterized queries with SQLAlchemy's `text()` bind parameters, e.g., `text("SELECT * FROM tasks WHERE owner_id = :uid AND (title LIKE :q OR description LIKE :q)")` with bound parameters.
- **Effort**: Small (< 1 hour)

### [Critical] Hardcoded Production Database Credentials
- **Category**: Security
- **Location**: `models/database.py`, line 7
- **Problem**: The production database URL including username (`admin`) and password (`s3cretPassw0rd`) is hardcoded in source code. Anyone with repository access has full database credentials. This also makes it impossible to rotate credentials without a code change and deployment.
- **Evidence**:
  ```python
  DATABASE_URL = "postgresql://admin:s3cretPassw0rd@prod-db.internal.company.com:5432/taskflow"
  ```
- **Suggested fix**: Read `DATABASE_URL` from an environment variable using `os.environ.get("DATABASE_URL")` and fail fast if it is not set.
- **Effort**: Small (< 1 hour)

### [Critical] Arbitrary SQL Execution Endpoint
- **Category**: Security
- **Location**: `api/routes/admin.py`, lines 44-52
- **Problem**: The `/api/admin/run-query` endpoint accepts and executes any SQL query. Even though it requires admin authentication, the admin check trusts an unverified JWT claim (see separate finding), and even legitimate admins should not have raw SQL access through an API endpoint. This enables DROP TABLE, data exfiltration, and privilege escalation.
- **Evidence**:
  ```python
  @router.post("/run-query")
  def run_query(query: str, db: Session = Depends(get_db), admin=Depends(require_admin)):
      result = db.execute(text(query))
  ```
- **Suggested fix**: Remove this endpoint entirely. If admin query capability is needed, implement a restricted set of predefined queries with parameterized inputs.
- **Effort**: Small (< 1 hour)

### [Critical] Hardcoded JWT Secret Key
- **Category**: Security
- **Location**: `utils/auth.py`, line 9
- **Problem**: The JWT signing secret is hardcoded in source code. Anyone with access to the repository can forge arbitrary JWT tokens, impersonate any user, and grant themselves admin privileges.
- **Evidence**:
  ```python
  SECRET_KEY = "my-super-secret-jwt-key-do-not-share"
  ```
- **Suggested fix**: Load the secret from an environment variable. Use a cryptographically random value of at least 256 bits.
- **Effort**: Small (< 1 hour)

### [Critical] Unsafe Deserialization with pickle.loads on Redis Data
- **Category**: Security
- **Location**: `utils/cache.py`, line 17
- **Problem**: `pickle.loads()` is used to deserialize data retrieved from Redis. If an attacker gains write access to the Redis instance (which has no authentication -- see separate finding), they can inject a malicious pickle payload to achieve arbitrary code execution on the application server.
- **Evidence**:
  ```python
  return pickle.loads(data)
  ```
- **Suggested fix**: Replace pickle serialization with JSON (`json.dumps`/`json.loads`). If arbitrary Python objects must be cached, use a safe serialization format or a restricted unpickler.
- **Effort**: Medium (hours) -- requires updating all cached value types to be JSON-serializable.

### [Critical] Admin Authorization Trusts Unverified JWT Claim
- **Category**: Security
- **Location**: `api/routes/admin.py`, lines 13-19
- **Problem**: The `require_admin` function checks `payload.get("is_admin")` from the JWT, but the `is_admin` claim is never set during token creation (`api/routes/users.py`, line 51 only sets `user_id` and `username`). This means the admin check currently blocks everyone -- but more critically, the design trusts a client-provided JWT claim rather than verifying admin status against the database. Combined with the hardcoded JWT secret, any attacker can forge a token with `is_admin: true`.
- **Evidence**:
  ```python
  def require_admin(token: str):
      payload = decode_token(token)
      if not payload.get("is_admin"):
          raise HTTPException(status_code=403, detail="Admin access required")
  ```
- **Suggested fix**: Look up the user in the database and check the `is_admin` column rather than trusting the JWT claim.
- **Effort**: Small (< 1 hour)

### [High] Sensitive Data Logged in Request Bodies (Passwords)
- **Category**: Security
- **Location**: `utils/logging.py`, lines 21-30; `api/main.py`, lines 22-34
- **Problem**: The HTTP middleware reads the full request body and passes it to `log_request()`, which logs it as JSON. This means passwords submitted to `/api/users/register` and `/api/users/login` are written to application logs in plaintext. The `log_error()` function (line 34-40) also logs arbitrary context dictionaries that may contain sensitive data.
- **Evidence**:
  ```python
  # api/main.py
  body_json = body.decode("utf-8") if body else None
  log_request(request.method, str(request.url), getattr(request.state, "user_id", 0), body_json)

  # utils/logging.py
  logger.info(json.dumps({...  "body": body,  }))
  ```
- **Suggested fix**: Exclude sensitive endpoints (login, register) from body logging, or implement a sanitizer that redacts fields named `password`, `token`, `secret`, etc.
- **Effort**: Medium (hours)

### [High] User Registration Exposes Hashed Password in Response
- **Category**: Security
- **Location**: `api/routes/users.py`, lines 40-41
- **Problem**: The register endpoint returns the full SQLAlchemy `User` object, which includes the `hashed_password` field. While the password is hashed, exposing the hash enables offline brute-force attacks.
- **Evidence**:
  ```python
  return db_user  # Returns entire User object including hashed_password
  ```
- **Suggested fix**: Create a Pydantic response model that excludes `hashed_password` and `is_admin`, and use it as the `response_model` for the endpoint.
- **Effort**: Small (< 1 hour)

### [High] User Profile Endpoint Exposes All Fields Including Password Hash
- **Category**: Security
- **Location**: `api/routes/users.py`, lines 55-61
- **Problem**: The `GET /api/users/{user_id}` endpoint returns the full user object with no authentication required, exposing hashed passwords and admin status for any user to any unauthenticated caller.
- **Evidence**:
  ```python
  @router.get("/{user_id}")
  def get_user(user_id: int, db: Session = Depends(get_db)):
      # No authentication
      return user  # All fields exposed
  ```
- **Suggested fix**: Require authentication, add authorization checks, and use a response model that excludes sensitive fields.
- **Effort**: Small (< 1 hour)

### [High] JWT Token Passed in Query String
- **Category**: Security
- **Location**: `api/routes/tasks.py`, lines 33-37
- **Problem**: The `get_current_user` dependency extracts the JWT from a query parameter. Query strings are logged in web server access logs, stored in browser history, and may be cached by proxies. This exposes authentication tokens broadly.
- **Evidence**:
  ```python
  def get_current_user(token: str = Query(...)):
      payload = decode_token(token)
      return payload
  ```
- **Suggested fix**: Use the `Authorization: Bearer <token>` header instead. FastAPI has built-in support via `OAuth2PasswordBearer`.
- **Effort**: Small (< 1 hour)

### [High] Token Decoding Has No Exception Handling
- **Category**: Security / Error Handling
- **Location**: `utils/auth.py`, lines 36-39
- **Problem**: `decode_token` does not catch exceptions from `jwt.decode()`. An invalid, expired, or malformed token will raise an unhandled `jwt.exceptions.DecodeError` or `jwt.exceptions.ExpiredSignatureError`, resulting in a 500 Internal Server Error instead of a proper 401 response.
- **Evidence**:
  ```python
  def decode_token(token: str) -> dict:
      return jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
  ```
- **Suggested fix**: Wrap in try/except, catch `jwt.PyJWTError`, and raise an appropriate HTTP 401 exception.
- **Effort**: Small (< 1 hour)

### [High] Unauthenticated Redis Connection to Production
- **Category**: Security
- **Location**: `utils/cache.py`, line 8
- **Problem**: The Redis client connects to a production server with no authentication and the hostname is hardcoded. Anyone on the network can connect to this Redis instance.
- **Evidence**:
  ```python
  _redis_client = redis.Redis(host="redis-prod.internal.company.com", port=6379, db=0)
  ```
- **Suggested fix**: Configure Redis authentication with a password from an environment variable, and use TLS for the connection. Make the host configurable.
- **Effort**: Small (< 1 hour)

### [High] Hardcoded Webhook URL with Embedded Token
- **Category**: Security
- **Location**: `services/notification_service.py`, line 6
- **Problem**: A webhook URL containing what appears to be service tokens is hardcoded in source code.
- **Evidence**:
  ```python
  WEBHOOK_URL = "https://hooks.example.com/services/REDACTED/REDACTED/REDACTED"
  ```
- **Suggested fix**: Move the webhook URL to an environment variable.
- **Effort**: Small (< 1 hour)

### [High] Wildcard CORS with Credentials Enabled
- **Category**: Security
- **Location**: `api/main.py`, lines 9-15
- **Problem**: CORS is configured to allow all origins (`*`) while also allowing credentials. This enables any website to make authenticated cross-origin requests to the API, potentially leading to CSRF-like attacks.
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
- **Suggested fix**: Restrict `allow_origins` to the specific domains that need to access the API. When `allow_credentials=True`, the wildcard origin is not spec-compliant and some browsers may reject it.
- **Effort**: Small (< 1 hour)

### [High] Missing Authorization on Task Read
- **Category**: Correctness
- **Location**: `api/routes/tasks.py`, lines 48-54
- **Problem**: The `GET /api/tasks/{task_id}` endpoint has no authentication or authorization. Any unauthenticated user can read any task by ID.
- **Evidence**:
  ```python
  @router.get("/{task_id}")
  def read_task(task_id: int, db: Session = Depends(get_db)):
      task = get_task(db, task_id)
  ```
- **Suggested fix**: Add the `get_current_user` dependency and verify the requesting user owns or is assigned to the task.
- **Effort**: Small (< 1 hour)

### [High] Missing Authorization on Task Status Update
- **Category**: Correctness
- **Location**: `services/task_service.py`, lines 57-68
- **Problem**: `update_task_status` accepts a `user_id` parameter but never uses it for authorization. Any authenticated user can change the status of any task.
- **Evidence**:
  ```python
  def update_task_status(db: Session, task_id: int, new_status: str, user_id: int) -> Optional[Task]:
      task = db.query(Task).filter(Task.id == task_id).first()
      if not task:
          return None
      # No check that user_id matches task.owner_id or task.assignee_id
      task.status = TaskStatus[new_status]
  ```
- **Suggested fix**: Add a check that `user_id == task.owner_id or user_id == task.assignee_id` before allowing the update.
- **Effort**: Small (< 1 hour)

### [High] User Deletion Leaves Orphaned Tasks (SQL Injection Too)
- **Category**: Correctness / Security
- **Location**: `api/routes/admin.py`, lines 35-41
- **Problem**: The delete user endpoint does not delete or reassign the user's tasks, leaving orphaned records with broken foreign keys. Additionally, the `user_id` parameter is interpolated directly into a SQL string, creating another SQL injection vector (though this endpoint requires admin access).
- **Evidence**:
  ```python
  db.execute(text(f"DELETE FROM users WHERE id = {user_id}"))
  ```
- **Suggested fix**: Use parameterized queries. Delete or reassign associated tasks before deleting the user, or set up CASCADE deletion on the foreign key.
- **Effort**: Medium (hours)

### [High] Division by Zero in Task Statistics
- **Category**: Correctness
- **Location**: `services/task_service.py`, lines 99-101
- **Problem**: When a user has no tasks, `total` is 0 and the code divides by zero on line 101, raising a `ZeroDivisionError` which will crash the request.
- **Evidence**:
  ```python
  total = len(tasks)
  completed = len([t for t in tasks if t.status == TaskStatus.COMPLETED])
  completion_rate = completed / total  # ZeroDivisionError when total == 0
  ```
- **Suggested fix**: `completion_rate = completed / total if total > 0 else 0.0`
- **Effort**: Small (< 1 hour)

### [High] MD5 Used for API Key Generation
- **Category**: Security
- **Location**: `utils/auth.py`, lines 42-46
- **Problem**: API keys are generated using MD5, which is cryptographically broken. The input includes the secret key and a timestamp, making keys predictable if the secret is known (which it is, since it is hardcoded).
- **Evidence**:
  ```python
  raw = f"{user_id}:{SECRET_KEY}:{datetime.utcnow().isoformat()}"
  return hashlib.md5(raw.encode()).hexdigest()
  ```
- **Suggested fix**: Use `secrets.token_urlsafe(32)` for generating API keys, and store a hash of the key in the database rather than the key itself.
- **Effort**: Small (< 1 hour)

### [High] Excessive JWT Token Expiry (30 Days)
- **Category**: Security
- **Location**: `utils/auth.py`, line 11
- **Problem**: Access tokens expire after 43200 minutes (30 days). If a token is leaked, it remains valid for a month. There is no refresh token mechanism or token revocation.
- **Evidence**:
  ```python
  ACCESS_TOKEN_EXPIRE_MINUTES = 43200  # 30 days
  ```
- **Suggested fix**: Reduce to 15-60 minutes and implement a refresh token flow with shorter-lived access tokens.
- **Effort**: Medium (hours)

### [Medium] Race Conditions in Job Processor
- **Category**: Correctness
- **Location**: `services/job_processor.py`, lines 11-13, 52
- **Problem**: The global `_processing_count` is incremented without any synchronization from multiple worker threads, leading to inaccurate counts. The `_job_handlers` dict is also shared mutable state, though in practice it is likely only written at startup.
- **Evidence**:
  ```python
  _processing_count = 0
  ...
  _processing_count += 1  # Race condition -- not atomic
  ```
- **Suggested fix**: Use `threading.Lock` or `threading.atomic` (or `itertools.count`) for the counter. Consider using a `threading.Lock` to protect handler registration if it can happen at runtime.
- **Effort**: Small (< 1 hour)

### [Medium] Worker Threads Not Daemonized
- **Category**: Correctness
- **Location**: `services/job_processor.py`, lines 74-77
- **Problem**: Worker threads are started as non-daemon threads with infinite loops. The application cannot shut down cleanly -- it will hang until forcibly killed because these threads never exit.
- **Evidence**:
  ```python
  t = threading.Thread(target=worker_loop)
  t.start()  # Not daemon, no shutdown mechanism
  ```
- **Suggested fix**: Set `t.daemon = True` or implement a shutdown signal (e.g., an `Event`) that the worker loop checks.
- **Effort**: Small (< 1 hour)

### [Medium] Error Information Lost in Dead Letter Queue
- **Category**: Error Handling
- **Location**: `services/job_processor.py`, lines 56-60
- **Problem**: When a job fails three times and is moved to the dead letter queue, the exception information is discarded. This makes it impossible to diagnose why a job failed.
- **Evidence**:
  ```python
  except Exception as e:
      job["attempts"] += 1
      if job["attempts"] >= 3:
          _redis_client.lpush(DEAD_LETTER_QUEUE, json.dumps(job))
          # 'e' is never recorded in the job
  ```
- **Suggested fix**: Add `job["last_error"] = str(e)` before pushing to the dead letter queue.
- **Effort**: Small (< 1 hour)

### [Medium] N+1 Query Pattern in get_user_tasks
- **Category**: Performance
- **Location**: `services/task_service.py`, lines 48-54
- **Problem**: The function loads all tasks for a user into memory and then slices in Python. For users with many tasks, this wastes memory and database bandwidth.
- **Evidence**:
  ```python
  all_tasks = db.query(Task).filter(Task.owner_id == user_id).all()
  start = (page - 1) * per_page
  end = start + per_page
  return all_tasks[start:end]
  ```
- **Suggested fix**: Use `.offset()` and `.limit()` on the query: `db.query(Task).filter(...).offset(start).limit(per_page).all()`
- **Effort**: Small (< 1 hour)

### [Medium] No Upper Bound on per_page Parameter
- **Category**: Performance
- **Location**: `api/routes/tasks.py`, line 64
- **Problem**: The `per_page` query parameter has no maximum. A client can request `per_page=999999999`, forcing the server to load an enormous result set. Combined with the load-all-then-slice bug above, this is especially dangerous.
- **Evidence**:
  ```python
  def list_tasks(page: int = 1, per_page: int = 100, ...):
  ```
- **Suggested fix**: Clamp `per_page` to a maximum (e.g., 100) and validate that both `page` and `per_page` are positive.
- **Effort**: Small (< 1 hour)

### [Medium] Individual Commits in Bulk Assign Loop
- **Category**: Performance
- **Location**: `services/task_service.py`, lines 71-81
- **Problem**: `bulk_assign_tasks` commits to the database inside the for loop for every task. For large batches this causes many unnecessary round-trips and leaves the operation partially committed if one iteration fails.
- **Evidence**:
  ```python
  for task_id in task_ids:
      task = db.query(Task).filter(Task.id == task_id).first()
      if task:
          task.assignee_id = assignee_id
          db.commit()  # Commits per iteration
  ```
- **Suggested fix**: Move `db.commit()` after the loop to batch all changes in a single transaction.
- **Effort**: Small (< 1 hour)

### [Medium] Redis KEYS Command Blocks on Large Datasets
- **Category**: Performance
- **Location**: `utils/cache.py`, lines 26-30
- **Problem**: The `KEYS` command scans the entire Redis keyspace and blocks the server while doing so. On a production Redis instance with many keys, this can cause latency spikes or outages.
- **Evidence**:
  ```python
  keys = _redis_client.keys(pattern)
  ```
- **Suggested fix**: Use `SCAN` with an iterator: `for key in _redis_client.scan_iter(match=pattern): _redis_client.delete(key)`
- **Effort**: Small (< 1 hour)

### [Medium] Off-by-One in Cache Warming
- **Category**: Correctness
- **Location**: `utils/cache.py`, lines 48-50
- **Problem**: The loop starts at index 1 instead of 0, so the first item in the list is never cached.
- **Evidence**:
  ```python
  for i in range(1, len(items)):  # Skips index 0
      set_cached(f"{key_prefix}:{items[i]['id']}", items[i])
  ```
- **Suggested fix**: Change to `range(0, len(items))` or `range(len(items))`.
- **Effort**: Small (< 1 hour)

### [Medium] Notification Service Hangs Without Timeout
- **Category**: Error Handling
- **Location**: `services/notification_service.py`, line 19
- **Problem**: The `httpx.post()` call has no timeout. If the webhook server is unresponsive, the calling thread will block indefinitely.
- **Evidence**:
  ```python
  response = httpx.post(WEBHOOK_URL, json=payload)
  ```
- **Suggested fix**: Add a timeout: `httpx.post(WEBHOOK_URL, json=payload, timeout=10.0)`
- **Effort**: Small (< 1 hour)

### [Medium] Notification Silently Swallows All Exceptions
- **Category**: Error Handling
- **Location**: `services/notification_service.py`, lines 21-23
- **Problem**: The bare `except Exception: return False` hides all errors including network issues, serialization bugs, and programming errors. Failures are invisible.
- **Evidence**:
  ```python
  except Exception:
      return False
  ```
- **Suggested fix**: Log the exception before returning False, and consider distinguishing between transient errors (retry-worthy) and permanent failures.
- **Effort**: Small (< 1 hour)

### [Medium] NameError in notify_task_assigned
- **Category**: Correctness
- **Location**: `services/notification_service.py`, lines 38-40
- **Problem**: The function references `assignee_id` which is not defined in the function scope -- it is neither a parameter nor a local variable. Calling this function will always raise a `NameError`.
- **Evidence**:
  ```python
  def notify_task_assigned(task_title: str, assignee_name: str, assigner_name: str):
      message = f"..."
      send_notification(assignee_id, message)  # assignee_id is undefined
  ```
- **Suggested fix**: Add `assignee_id` as a parameter to the function.
- **Effort**: Small (< 1 hour)

### [Medium] Sequential HTTP Calls in Bulk Notifications
- **Category**: Performance
- **Location**: `services/notification_service.py`, lines 26-32
- **Problem**: `send_bulk_notifications` sends notifications one at a time synchronously. For large user lists this is very slow, especially given the missing timeout on each request.
- **Evidence**:
  ```python
  for user in users:
      if send_notification(user["id"], message):
          success_count += 1
  ```
- **Suggested fix**: Use `asyncio.gather()` or `concurrent.futures.ThreadPoolExecutor` to send notifications in parallel.
- **Effort**: Medium (hours)

### [Medium] Request Body Read for Every Request Including File Uploads
- **Category**: Performance
- **Location**: `api/main.py`, lines 26-31
- **Problem**: The middleware reads and decodes the full request body for every incoming request, including potentially large file uploads. This doubles memory usage for large payloads and may interfere with streaming.
- **Evidence**:
  ```python
  body = await request.body()
  body_json = body.decode("utf-8") if body else None
  ```
- **Suggested fix**: Only read the body for relevant endpoints, or log only metadata (content-type, content-length) rather than the full body.
- **Effort**: Small (< 1 hour)

### [Low] Broken Test Import (Typo)
- **Category**: Correctness
- **Location**: `tests/test_tasks.py`, line 5
- **Problem**: The import references `taskflow.models.taks` (missing 'k'), which will cause an `ImportError` when tests are run. No tests in this file can execute.
- **Evidence**:
  ```python
  from taskflow.models.taks import Task, TaskStatus  # 'taks' not 'task'
  ```
- **Suggested fix**: Change to `from taskflow.models.task import Task, TaskStatus`
- **Effort**: Small (< 1 hour)

### [Low] Test Asserts Wrong Field Name
- **Category**: Correctness
- **Location**: `tests/test_tasks.py`, line 31
- **Problem**: The test checks `stats["complete_rate"]` but the actual key returned by `compute_task_stats` is `"completion_rate"`. This test will always raise a `KeyError`.
- **Evidence**:
  ```python
  assert stats["complete_rate"] >= 0  # Should be "completion_rate"
  ```
- **Suggested fix**: Change to `stats["completion_rate"]`.
- **Effort**: Small (< 1 hour)

### [Low] No Email Validation on User Registration
- **Category**: Correctness
- **Location**: `api/routes/users.py`, lines 26-28
- **Problem**: The `email` field in `UserCreate` is typed as `str` with no validation. Any string is accepted, including empty strings or non-email values.
- **Evidence**:
  ```python
  class UserCreate(BaseModel):
      email: str  # No EmailStr or regex validation
  ```
- **Suggested fix**: Use `pydantic.EmailStr` (requires the `email-validator` package) or add a regex validator.
- **Effort**: Small (< 1 hour)

### [Low] Multiple Iterations Over Tasks List in compute_task_stats
- **Category**: Performance
- **Location**: `services/task_service.py`, lines 103-106
- **Problem**: The function iterates over the tasks list four separate times (one per status) to count statuses. While functionally correct, this is inefficient for large task lists.
- **Evidence**:
  ```python
  in_progress = len([t for t in tasks if t.status == TaskStatus.IN_PROGRESS])
  pending = len([t for t in tasks if t.status == TaskStatus.PENDING])
  failed = len([t for t in tasks if t.status == TaskStatus.FAILED])
  ```
- **Suggested fix**: Use a single pass with `collections.Counter` or a dictionary to count all statuses at once.
- **Effort**: Small (< 1 hour)

### [Low] Caching SQLAlchemy ORM Objects with Pickle
- **Category**: Correctness
- **Location**: `services/task_service.py`, lines 31-36
- **Problem**: `get_task` caches the SQLAlchemy `Task` ORM object via pickle. Detached ORM objects retrieved from cache will raise `DetachedInstanceError` if any lazy-loaded relationships are accessed, since they are no longer bound to a session.
- **Evidence**:
  ```python
  task = db.query(Task).filter(Task.id == task_id).first()
  if task:
      set_cached(f"task:{task_id}", task)  # Pickles an ORM object
  ```
- **Suggested fix**: Cache a dictionary or Pydantic model representation instead of the raw ORM object.
- **Effort**: Medium (hours)

## Issues Not Found
- **Circular dependencies**: No circular imports were detected between modules. The dependency graph flows cleanly from routes to services to models/utils.
- **Dead code**: No significant unreachable code or unused functions were found (all defined service functions are imported and used by route handlers).
- **Dependency vulnerabilities**: The pinned versions in `pyproject.toml` are minimum versions (using `>=`) for well-maintained libraries. No known-vulnerable version pins were identified, though the lack of upper bounds could allow untested major version upgrades.
- **Type coercion bugs**: Pydantic models provide basic type validation at the API boundary, and Python's type system avoids the implicit coercion issues common in JavaScript.
