"""
taskpulse.core
--------------
Task, TaskStatus, and TaskPulse — the main task runner.
"""

import sqlite3
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Callable, Dict, List, Optional


class TaskStatus(str, Enum):
    PENDING  = "pending"
    RUNNING  = "running"
    DONE     = "done"
    FAILED   = "failed"
    CANCELLED = "cancelled"


@dataclass
class Task:
    """Represents a single tracked task."""
    id: str
    name: str
    func: Callable
    args: tuple = field(default_factory=tuple)
    kwargs: dict = field(default_factory=dict)
    total: Optional[int] = None        # total steps (for progress %)

    status: TaskStatus = TaskStatus.PENDING
    progress: int = 0                  # current step
    result: Any = None
    error: Optional[str] = None
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    created_at: datetime = field(default_factory=datetime.now)

    # Hooks
    on_start:    Optional[Callable[["Task"], None]] = None
    on_progress: Optional[Callable[["Task"], None]] = None
    on_done:     Optional[Callable[["Task"], None]] = None
    on_error:    Optional[Callable[["Task"], None]] = None

    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    @property
    def elapsed(self) -> float:
        if self.started_at is None:
            return 0.0
        end = self.finished_at or datetime.now()
        return (end - self.started_at).total_seconds()

    @property
    def eta(self) -> Optional[float]:
        """Estimated seconds remaining."""
        if self.total is None or self.progress == 0:
            return None
        rate = self.progress / max(self.elapsed, 0.001)
        remaining = self.total - self.progress
        return remaining / rate if rate > 0 else None

    @property
    def percent(self) -> Optional[float]:
        if self.total is None or self.total == 0:
            return None
        return min(100.0, self.progress / self.total * 100)

    def update(self, progress: int, total: Optional[int] = None):
        """Update progress. Call this from inside your task function."""
        with self._lock:
            self.progress = progress
            if total is not None:
                self.total = total
        if self.on_progress:
            try:
                self.on_progress(self)
            except Exception:
                pass

    def summary(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "status": self.status.value,
            "progress": self.progress,
            "total": self.total,
            "percent": self.percent,
            "elapsed": round(self.elapsed, 2),
            "eta": round(self.eta, 2) if self.eta is not None else None,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
            "error": self.error,
        }


class TaskPulse:
    """
    Run and monitor background tasks with live progress, ETA, hooks, and SQLite history.

    Parameters
    ----------
    db_path : str
        Path to SQLite file for task history. Default: "taskpulse.db".
    max_workers : int
        Max concurrent tasks. Default: 4.
    show_progress : bool
        Print live progress bars to stdout. Default: True.

    Example
    -------
    >>> pulse = TaskPulse()

    >>> @pulse.task(name="Process files", total=100)
    ... def process(task):
    ...     for i in range(100):
    ...         do_work()
    ...         task.update(i + 1)
    ...     return "done"

    >>> task = pulse.run(process)
    >>> pulse.wait(task.id)
    >>> print(task.result)
    """

    def __init__(
        self,
        db_path: str = "taskpulse.db",
        max_workers: int = 4,
        show_progress: bool = True,
    ):
        self._db_path = db_path
        self._max_workers = max_workers
        self._show_progress = show_progress
        self._tasks: Dict[str, Task] = {}
        self._lock = threading.Lock()
        self._executor = ThreadPoolExecutor(max_workers=max_workers)
        self._id_counter = 0
        self._db = self._init_db()

    # ── DB ────────────────────────────────────────────────────────────────────

    def _init_db(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path, check_same_thread=False)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS task_history (
                id           TEXT PRIMARY KEY,
                name         TEXT,
                status       TEXT,
                progress     INTEGER,
                total        INTEGER,
                elapsed      REAL,
                result       TEXT,
                error        TEXT,
                started_at   TEXT,
                finished_at  TEXT,
                created_at   TEXT
            )
        """)
        conn.commit()
        return conn

    def _save_task(self, task: Task):
        self._db.execute("""
            INSERT OR REPLACE INTO task_history
            (id, name, status, progress, total, elapsed, result, error,
             started_at, finished_at, created_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?)
        """, (
            task.id, task.name, task.status.value,
            task.progress, task.total, round(task.elapsed, 3),
            str(task.result) if task.result is not None else None,
            task.error,
            task.started_at.isoformat() if task.started_at else None,
            task.finished_at.isoformat() if task.finished_at else None,
            task.created_at.isoformat(),
        ))
        self._db.commit()

    # ── Task registration ─────────────────────────────────────────────────────

    def task(
        self,
        name: Optional[str] = None,
        total: Optional[int] = None,
        on_start: Optional[Callable] = None,
        on_progress: Optional[Callable] = None,
        on_done: Optional[Callable] = None,
        on_error: Optional[Callable] = None,
    ):
        """
        Decorator to mark a function as a taskpulse task.

        The decorated function receives a Task object as its first argument.

        Example
        -------
        >>> @pulse.task(name="My Task", total=50)
        ... def my_task(task):
        ...     for i in range(50):
        ...         work()
        ...         task.update(i + 1)
        """
        def decorator(func: Callable) -> Callable:
            func._taskpulse_config = {
                "name": name or func.__name__,
                "total": total,
                "on_start": on_start,
                "on_progress": on_progress,
                "on_done": on_done,
                "on_error": on_error,
            }
            return func
        return decorator

    def _next_id(self) -> str:
        with self._lock:
            self._id_counter += 1
            return f"task-{self._id_counter:04d}"

    def run(
        self,
        func: Callable,
        *args,
        name: Optional[str] = None,
        total: Optional[int] = None,
        on_start: Optional[Callable] = None,
        on_progress: Optional[Callable] = None,
        on_done: Optional[Callable] = None,
        on_error: Optional[Callable] = None,
        **kwargs,
    ) -> Task:
        """
        Submit a task for background execution.

        Returns the Task object immediately (non-blocking).
        """
        config = getattr(func, "_taskpulse_config", {})
        task = Task(
            id=self._next_id(),
            name=name or config.get("name") or func.__name__,
            func=func,
            args=args,
            kwargs=kwargs,
            total=total or config.get("total"),
            on_start=on_start or config.get("on_start"),
            on_progress=on_progress or config.get("on_progress"),
            on_done=on_done or config.get("on_done"),
            on_error=on_error or config.get("on_error"),
        )

        with self._lock:
            self._tasks[task.id] = task

        self._save_task(task)
        self._executor.submit(self._execute, task)
        return task

    def _execute(self, task: Task):
        task.status = TaskStatus.RUNNING
        task.started_at = datetime.now()
        self._save_task(task)

        if task.on_start:
            try: task.on_start(task)
            except Exception: pass

        if self._show_progress:
            self._start_progress(task)

        try:
            # Pass task as first argument so the function can call task.update()
            result = task.func(task, *task.args, **task.kwargs)
            task.result = result
            task.status = TaskStatus.DONE
            task.finished_at = datetime.now()

            if task.on_done:
                try: task.on_done(task)
                except Exception: pass

        except Exception as e:
            task.error = traceback.format_exc()
            task.status = TaskStatus.FAILED
            task.finished_at = datetime.now()

            if task.on_error:
                try: task.on_error(task)
                except Exception: pass

        finally:
            self._save_task(task)

    # ── Progress display ──────────────────────────────────────────────────────

    def _start_progress(self, task: Task):
        def _watch():
            while task.status == TaskStatus.RUNNING:
                self._render_progress(task)
                time.sleep(0.2)
            self._render_progress(task, final=True)
            print()  # newline after final render

        t = threading.Thread(target=_watch, daemon=True)
        t.start()

    def _render_progress(self, task: Task, final: bool = False):
        elapsed = task.elapsed
        eta = task.eta

        if task.percent is not None:
            pct = task.percent
            bar_len = 30
            filled = int(bar_len * pct / 100)
            bar = "█" * filled + "░" * (bar_len - filled)
            progress_str = f"[{bar}] {pct:5.1f}%"
        else:
            spinner = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
            spin_char = spinner[int(elapsed * 5) % len(spinner)]
            progress_str = f"[{spin_char}] running"

        eta_str = f"  ETA {eta:.1f}s" if eta is not None else ""
        status_icon = {"running": "⚡", "done": "✅", "failed": "❌"}.get(
            task.status.value, "•"
        )

        line = (
            f"\r  {status_icon} {task.name:<25} "
            f"{progress_str}  "
            f"elapsed {elapsed:.1f}s{eta_str}    "
        )
        print(line, end="", flush=True)

    # ── Control ───────────────────────────────────────────────────────────────

    def wait(self, *task_ids: str, timeout: Optional[float] = None) -> List[Task]:
        """Block until specified tasks (or all if none given) are complete."""
        ids = list(task_ids) if task_ids else list(self._tasks.keys())
        start = time.monotonic()

        while True:
            tasks = [self._tasks[tid] for tid in ids if tid in self._tasks]
            done = all(t.status in (TaskStatus.DONE, TaskStatus.FAILED,
                                    TaskStatus.CANCELLED) for t in tasks)
            if done:
                return tasks
            if timeout and (time.monotonic() - start) > timeout:
                return tasks
            time.sleep(0.1)

    def cancel(self, task_id: str):
        """Mark a task as cancelled (best-effort, cooperative)."""
        task = self._tasks.get(task_id)
        if task and task.status == TaskStatus.PENDING:
            task.status = TaskStatus.CANCELLED
            self._save_task(task)

    def get(self, task_id: str) -> Optional[Task]:
        """Get a task by ID."""
        return self._tasks.get(task_id)

    def all_tasks(self) -> List[Task]:
        with self._lock:
            return list(self._tasks.values())

    def status(self) -> List[dict]:
        """Return summary dicts for all tasks."""
        return [t.summary() for t in self.all_tasks()]

    def history(self, limit: int = 50) -> List[dict]:
        """Return task history from SQLite."""
        rows = self._db.execute(
            "SELECT * FROM task_history ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()
        cols = [d[0] for d in self._db.execute(
            "SELECT * FROM task_history LIMIT 0"
        ).description]
        return [dict(zip(cols, r)) for r in rows]

    def print_status(self):
        """Print a formatted status table."""
        tasks = self.all_tasks()
        if not tasks:
            print("  No tasks.")
            return
        print(f"\n{'Name':<25} {'Status':<12} {'Progress':<12} {'Elapsed':>8}  {'ETA':>8}")
        print("─" * 75)
        for t in tasks:
            pct = f"{t.percent:.1f}%" if t.percent is not None else "—"
            eta = f"{t.eta:.1f}s" if t.eta else "—"
            icon = {"pending": "⏳", "running": "⚡", "done": "✅",
                    "failed": "❌", "cancelled": "🚫"}.get(t.status.value, "•")
            print(f"  {icon} {t.name:<23} {t.status.value:<12} {pct:<12} "
                  f"{t.elapsed:>7.1f}s  {eta:>8}")
        print()

    def shutdown(self):
        """Shutdown the executor."""
        self._executor.shutdown(wait=False)
        self._db.close()
