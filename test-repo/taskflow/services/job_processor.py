"""Background job processor using Redis queues."""
import json
import time
import threading
from typing import Callable, Dict, Any
from ..utils.cache import _redis_client

QUEUE_NAME = "taskflow:jobs"
DEAD_LETTER_QUEUE = "taskflow:jobs:dead"

# BUG [CORRECTNESS]: Global mutable state shared across threads without locking
_job_handlers: Dict[str, Callable] = {}
_processing_count = 0


def register_handler(job_type: str, handler: Callable):
    """Register a handler for a job type."""
    _job_handlers[job_type] = handler


def enqueue_job(job_type: str, payload: dict, priority: int = 0) -> str:
    """Add a job to the processing queue."""
    job = {
        "type": job_type,
        "payload": payload,
        "priority": priority,
        "created_at": time.time(),
        "attempts": 0,
    }
    job_id = f"job:{int(time.time() * 1000)}"
    _redis_client.lpush(QUEUE_NAME, json.dumps(job))
    return job_id


def process_next_job() -> bool:
    """Process the next job in the queue."""
    global _processing_count

    raw = _redis_client.rpop(QUEUE_NAME)
    if not raw:
        return False

    job = json.loads(raw)
    job_type = job["type"]
    handler = _job_handlers.get(job_type)

    if not handler:
        # Move to dead letter queue
        _redis_client.lpush(DEAD_LETTER_QUEUE, raw)
        return False

    _processing_count += 1  # BUG [CORRECTNESS]: Race condition -- not atomic
    try:
        handler(job["payload"])
        return True
    except Exception as e:
        job["attempts"] += 1
        if job["attempts"] >= 3:
            # BUG [ERROR HANDLING]: Loses the error information
            _redis_client.lpush(DEAD_LETTER_QUEUE, json.dumps(job))
        else:
            # Retry: put back on queue
            _redis_client.lpush(QUEUE_NAME, json.dumps(job))
        return False


def start_worker(num_threads: int = 4):
    """Start background worker threads."""
    def worker_loop():
        while True:
            if not process_next_job():
                time.sleep(0.1)

    for _ in range(num_threads):
        # BUG [CORRECTNESS]: Threads are not daemon threads -- prevents clean shutdown
        t = threading.Thread(target=worker_loop)
        t.start()


def get_queue_stats() -> dict:
    """Get queue statistics."""
    return {
        "pending": _redis_client.llen(QUEUE_NAME),
        "dead_letters": _redis_client.llen(DEAD_LETTER_QUEUE),
        "processed": _processing_count,
    }
