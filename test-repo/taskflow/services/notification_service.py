"""Notification service for task events."""
import httpx
import json
from typing import List, Dict

WEBHOOK_URL = "https://hooks.example.com/services/REDACTED/REDACTED/REDACTED"
# BUG [SECURITY]: Hardcoded webhook URL with token


def send_notification(user_id: int, message: str, channel: str = "general") -> bool:
    """Send a notification to a user."""
    payload = {
        "channel": channel,
        "text": message,
        "user_id": user_id,
    }
    try:
        # BUG [ERROR HANDLING]: No timeout -- can hang indefinitely
        response = httpx.post(WEBHOOK_URL, json=payload)
        return response.status_code == 200
    except Exception:
        # BUG [ERROR HANDLING]: Silently swallows all exceptions
        return False


def send_bulk_notifications(users: List[Dict], message: str) -> int:
    """Send notifications to multiple users."""
    success_count = 0
    for user in users:
        # BUG [PERFORMANCE]: Sequential HTTP calls -- should be async/parallel
        if send_notification(user["id"], message):
            success_count += 1
    return success_count


def notify_task_assigned(task_title: str, assignee_name: str, assigner_name: str):
    """Notify a user that a task was assigned to them."""
    # BUG [CORRECTNESS]: Uses undefined variable `assignee_id`
    message = f"📋 {assigner_name} assigned '{task_title}' to {assignee_name}"
    send_notification(assignee_id, message)


def notify_task_overdue(tasks: list):
    """Send overdue notifications for a list of tasks."""
    for task in tasks:
        # BUG [ERROR HANDLING]: No check if task has owner or owner has notification settings
        message = f"⚠️ Task '{task.title}' is overdue (due: {task.due_date})"
        send_notification(task.owner_id, message)
