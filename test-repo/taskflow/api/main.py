"""FastAPI application entry point."""
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from .routes import tasks, users, admin

app = FastAPI(title="TaskFlow API", version="0.1.0")

# BUG [SECURITY]: Wildcard CORS -- allows any origin
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(tasks.router, prefix="/api/tasks", tags=["tasks"])
app.include_router(users.router, prefix="/api/users", tags=["users"])
app.include_router(admin.router, prefix="/api/admin", tags=["admin"])


@app.middleware("http")
async def log_requests(request: Request, call_next):
    """Log all incoming requests."""
    from ..utils.logging import log_request
    body = await request.body()
    # BUG [PERFORMANCE]: Reads and decodes request body for every request including file uploads
    try:
        body_json = body.decode("utf-8") if body else None
    except Exception:
        body_json = None
    response = await call_next(request)
    log_request(request.method, str(request.url), getattr(request.state, "user_id", 0), body_json)
    return response


@app.get("/health")
async def health_check():
    return {"status": "ok"}
