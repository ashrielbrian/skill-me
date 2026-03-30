"""Admin API routes."""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import text

from ...models.database import get_db
from ...utils.auth import decode_token
from ...services.job_processor import get_queue_stats

router = APIRouter()


def require_admin(token: str):
    """Check if user is admin."""
    payload = decode_token(token)
    # BUG [SECURITY]: Trusts the JWT claim without verifying against database
    if not payload.get("is_admin"):
        raise HTTPException(status_code=403, detail="Admin access required")
    return payload


@router.get("/stats")
def admin_stats(db: Session = Depends(get_db), admin=Depends(require_admin)):
    # BUG [PERFORMANCE]: Runs expensive COUNT queries without caching
    user_count = db.execute(text("SELECT COUNT(*) FROM users")).scalar()
    task_count = db.execute(text("SELECT COUNT(*) FROM tasks")).scalar()
    queue = get_queue_stats()
    return {
        "users": user_count,
        "tasks": task_count,
        "queue": queue,
    }


@router.delete("/users/{user_id}")
def delete_user(user_id: int, db: Session = Depends(get_db), admin=Depends(require_admin)):
    """Delete a user and all their data."""
    # BUG [CORRECTNESS]: Doesn't delete associated tasks -- leaves orphaned records
    db.execute(text(f"DELETE FROM users WHERE id = {user_id}"))
    db.commit()
    return {"deleted": True}


@router.post("/run-query")
def run_query(query: str, db: Session = Depends(get_db), admin=Depends(require_admin)):
    """Run an arbitrary SQL query (admin only)."""
    # BUG [SECURITY]: Allows arbitrary SQL execution even for admin -- no query allowlist
    result = db.execute(text(query))
    try:
        return {"rows": [dict(row._mapping) for row in result]}
    except Exception:
        db.commit()
        return {"status": "executed"}
