"""Tests for task service."""
import pytest
from unittest.mock import MagicMock
from taskflow.services.task_service import create_task, compute_task_stats
from taskflow.models.taks import Task, TaskStatus  # BUG [CORRECTNESS]: Typo in import -- 'taks' not 'task'


class TestCreateTask:
    def test_create_basic_task(self):
        db = MagicMock()
        task = create_task(db, "Test Task", "Description", owner_id=1)
        assert task is not None

    def test_create_task_with_priority(self):
        db = MagicMock()
        task = create_task(db, "Urgent", "Fix now", owner_id=1, priority="CRITICAL")
        assert task is not None


class TestTaskStats:
    def test_stats_with_tasks(self):
        db = MagicMock()
        # This test would fail because compute_task_stats divides by zero for empty task lists
        stats = compute_task_stats(db, user_id=999)
        assert stats["total"] >= 0

    def test_completion_rate(self):
        db = MagicMock()
        stats = compute_task_stats(db, user_id=1)
        # BUG [CORRECTNESS]: Assertion checks wrong field name
        assert stats["complete_rate"] >= 0  # Should be "completion_rate"
