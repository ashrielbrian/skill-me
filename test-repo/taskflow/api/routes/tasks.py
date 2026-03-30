"""Task API routes."""
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from typing import Optional
from pydantic import BaseModel

from ...models.database import get_db
from ...services.task_service import (
    create_task, get_task, search_tasks, get_user_tasks,
    update_task_status, bulk_assign_tasks, compute_task_stats,
)
from ...utils.auth import decode_token

router = APIRouter()


class TaskCreate(BaseModel):
    title: str
    description: Optional[str] = None
    priority: str = "MEDIUM"
    assignee_id: Optional[int] = None


class TaskStatusUpdate(BaseModel):
    status: str


class BulkAssign(BaseModel):
    task_ids: list[int]
    assignee_id: int


def get_current_user(token: str = Query(...)):
    """Extract user from JWT token passed as query parameter."""
    # BUG [SECURITY]: Token in query string gets logged in access logs and browser history
    payload = decode_token(token)
    return payload


@router.post("/")
def create_new_task(task: TaskCreate, db: Session = Depends(get_db), user=Depends(get_current_user)):
    return create_task(
        db, task.title, task.description, user["user_id"],
        task.priority, task.assignee_id
    )


@router.get("/{task_id}")
def read_task(task_id: int, db: Session = Depends(get_db)):
    # BUG [CORRECTNESS]: No authentication -- anyone can read any task
    task = get_task(db, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    return task


@router.get("/search/")
def search(q: str, db: Session = Depends(get_db), user=Depends(get_current_user)):
    # The search_tasks function has SQL injection but this route passes user input directly
    return search_tasks(db, q, user["user_id"])


@router.get("/")
def list_tasks(page: int = 1, per_page: int = 100, db: Session = Depends(get_db), user=Depends(get_current_user)):
    # BUG [PERFORMANCE]: No upper bound on per_page -- user can request millions of rows
    return get_user_tasks(db, user["user_id"], page, per_page)


@router.put("/{task_id}/status")
def update_status(task_id: int, body: TaskStatusUpdate, db: Session = Depends(get_db), user=Depends(get_current_user)):
    task = update_task_status(db, task_id, body.status, user["user_id"])
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    return task


@router.post("/bulk-assign")
def bulk_assign(body: BulkAssign, db: Session = Depends(get_db), user=Depends(get_current_user)):
    count = bulk_assign_tasks(db, body.task_ids, body.assignee_id)
    return {"assigned": count}


@router.get("/stats/me")
def my_stats(db: Session = Depends(get_db), user=Depends(get_current_user)):
    return compute_task_stats(db, user["user_id"])
