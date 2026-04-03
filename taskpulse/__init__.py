"""
taskpulse — Lightweight background task monitor with live progress,
ETA, concurrent tasks, hooks, and SQLite history.
"""

from .core import TaskPulse, Task, TaskStatus
from .monitor import Monitor

__all__ = ["TaskPulse", "Task", "TaskStatus", "Monitor"]
__version__ = "1.0.0"
__author__ = "prabhay759"
