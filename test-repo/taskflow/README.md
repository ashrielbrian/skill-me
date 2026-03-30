# TaskFlow

A task management API with background job processing. Supports user authentication, task CRUD, and async job execution via Redis queues.

## Quick Start

```bash
pip install -e .
uvicorn taskflow.api.main:app --reload
```

## Architecture

- `api/` - FastAPI routes and middleware
- `models/` - SQLAlchemy ORM models
- `services/` - Business logic layer
- `utils/` - Shared utilities (auth, caching, logging)
