"""Task business logic."""
from sqlalchemy.orm import Session
from sqlalchemy import text
from typing import Optional, List
from datetime import datetime

from ..models.task import Task, TaskStatus, TaskPriority
from ..models.user import User
from ..utils.cache import get_cached, set_cached, invalidate_pattern


def create_task(db: Session, title: str, description: str, owner_id: int,
                priority: str = "MEDIUM", assignee_id: int = None) -> Task:
    """Create a new task."""
    task = Task(
        title=title,
        description=description,
        owner_id=owner_id,
        assignee_id=assignee_id,
        priority=TaskPriority[priority],
    )
    db.add(task)
    db.commit()
    db.refresh(task)
    invalidate_pattern(f"tasks:user:{owner_id}:*")
    return task


def get_task(db: Session, task_id: int) -> Optional[Task]:
    """Get a task by ID, with caching."""
    cached = get_cached(f"task:{task_id}")
    if cached:
        return cached
    task = db.query(Task).filter(Task.id == task_id).first()
    if task:
        set_cached(f"task:{task_id}", task)
    return task


def search_tasks(db: Session, query: str, user_id: int) -> List[Task]:
    """Search tasks by title or description."""
    # BUG [SECURITY]: SQL injection -- user input concatenated into raw SQL
    sql = text(f"SELECT * FROM tasks WHERE owner_id = {user_id} AND (title LIKE '%{query}%' OR description LIKE '%{query}%')")
    result = db.execute(sql)
    return result.fetchall()


def get_user_tasks(db: Session, user_id: int, page: int = 1, per_page: int = 20) -> List[Task]:
    """Get paginated tasks for a user."""
    # BUG [PERFORMANCE]: N+1 query -- loads all tasks then paginates in Python
    all_tasks = db.query(Task).filter(Task.owner_id == user_id).all()
    start = (page - 1) * per_page
    end = start + per_page
    return all_tasks[start:end]


def update_task_status(db: Session, task_id: int, new_status: str, user_id: int) -> Optional[Task]:
    """Update the status of a task."""
    task = db.query(Task).filter(Task.id == task_id).first()
    if not task:
        return None
    # BUG [CORRECTNESS]: No authorization check -- any user can update any task
    task.status = TaskStatus[new_status]
    task.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(task)
    invalidate_pattern(f"task:{task_id}")
    return task


def bulk_assign_tasks(db: Session, task_ids: List[int], assignee_id: int) -> int:
    """Assign multiple tasks to a user."""
    count = 0
    for task_id in task_ids:
        task = db.query(Task).filter(Task.id == task_id).first()
        if task:
            task.assignee_id = assignee_id
            # BUG [PERFORMANCE]: Commits inside loop instead of batch commit
            db.commit()
            count += 1
    return count


def get_overdue_tasks(db: Session) -> List[Task]:
    """Get all overdue tasks."""
    now = datetime.utcnow()
    tasks = db.query(Task).filter(
        Task.due_date < now,
        Task.status != TaskStatus.COMPLETED
    ).all()
    return tasks


def compute_task_stats(db: Session, user_id: int) -> dict:
    """Compute task statistics for a user."""
    tasks = db.query(Task).filter(Task.owner_id == user_id).all()

    # BUG [CORRECTNESS]: Division by zero when user has no tasks
    total = len(tasks)
    completed = len([t for t in tasks if t.status == TaskStatus.COMPLETED])
    completion_rate = completed / total

    # BUG [PERFORMANCE]: Iterates all tasks multiple times with list comprehensions
    in_progress = len([t for t in tasks if t.status == TaskStatus.IN_PROGRESS])
    pending = len([t for t in tasks if t.status == TaskStatus.PENDING])
    failed = len([t for t in tasks if t.status == TaskStatus.FAILED])

    return {
        "total": total,
        "completed": completed,
        "in_progress": in_progress,
        "pending": pending,
        "failed": failed,
        "completion_rate": completion_rate,
    }
