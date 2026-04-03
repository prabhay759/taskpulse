"""
Tests for taskpulse.
Run with: pytest tests/ -v
"""

import time
import threading
import tempfile
import os
import pytest

from taskpulse import TaskPulse, Task, TaskStatus


@pytest.fixture
def pulse(tmp_path):
    p = TaskPulse(db_path=str(tmp_path / "test.db"), show_progress=False)
    yield p
    p.shutdown()


# ── Basic task execution ──────────────────────────────────────────────────────

class TestTaskExecution:
    def test_runs_and_returns_result(self, pulse):
        def fn(task): return 42
        t = pulse.run(fn)
        pulse.wait(t.id)
        assert t.status == TaskStatus.DONE
        assert t.result == 42

    def test_captures_error(self, pulse):
        def bad(task): raise ValueError("oops")
        t = pulse.run(bad)
        pulse.wait(t.id)
        assert t.status == TaskStatus.FAILED
        assert "ValueError" in t.error

    def test_passes_args(self, pulse):
        def fn(task, x, y): return x + y
        t = pulse.run(fn, 3, 4)
        pulse.wait(t.id)
        assert t.result == 7

    def test_passes_kwargs(self, pulse):
        def fn(task, name="world"): return f"hello {name}"
        t = pulse.run(fn, name="Alice")
        pulse.wait(t.id)
        assert t.result == "hello Alice"

    def test_task_has_id(self, pulse):
        def fn(task): return 1
        t = pulse.run(fn)
        assert t.id.startswith("task-")

    def test_custom_name(self, pulse):
        def fn(task): return 1
        t = pulse.run(fn, name="My Custom Task")
        assert t.name == "My Custom Task"

    def test_started_and_finished_at_set(self, pulse):
        def fn(task): return 1
        t = pulse.run(fn)
        pulse.wait(t.id)
        assert t.started_at is not None
        assert t.finished_at is not None

    def test_elapsed_increases(self, pulse):
        def fn(task):
            time.sleep(0.1)
            return 1
        t = pulse.run(fn)
        pulse.wait(t.id)
        assert t.elapsed >= 0.05


# ── Progress tracking ─────────────────────────────────────────────────────────

class TestProgress:
    def test_progress_updates(self, pulse):
        def fn(task):
            for i in range(10):
                task.update(i + 1, total=10)
            return "done"
        t = pulse.run(fn)
        pulse.wait(t.id)
        assert t.progress == 10
        assert t.total == 10

    def test_percent_calculated(self, pulse):
        def fn(task):
            task.update(5, total=10)
            time.sleep(0.05)
            return 1
        t = pulse.run(fn)
        time.sleep(0.1)
        # At some point percent was 50%
        assert t.percent is not None or t.status == TaskStatus.DONE

    def test_eta_none_without_total(self, pulse):
        def fn(task): return 1
        t = pulse.run(fn)
        assert t.eta is None

    def test_no_total_percent_is_none(self, pulse):
        def fn(task): return 1
        t = pulse.run(fn)
        pulse.wait(t.id)
        assert t.percent is None


# ── Hooks ─────────────────────────────────────────────────────────────────────

class TestHooks:
    def test_on_start_called(self, pulse):
        called = []
        def fn(task): return 1
        pulse.run(fn, on_start=lambda t: called.append("start"))
        time.sleep(0.2)
        assert "start" in called

    def test_on_done_called(self, pulse):
        called = []
        def fn(task): return 1
        t = pulse.run(fn, on_done=lambda t: called.append("done"))
        pulse.wait(t.id)
        assert "done" in called

    def test_on_error_called(self, pulse):
        called = []
        def bad(task): raise RuntimeError("x")
        t = pulse.run(bad, on_error=lambda t: called.append("error"))
        pulse.wait(t.id)
        assert "error" in called

    def test_on_progress_called(self, pulse):
        called = []
        def fn(task):
            task.update(1, total=3)
            task.update(2, total=3)
            task.update(3, total=3)
            return 1
        t = pulse.run(fn, on_progress=lambda t: called.append(t.progress))
        pulse.wait(t.id)
        assert 1 in called
        assert 3 in called

    def test_decorator_hooks(self, pulse):
        called = []

        @pulse.task(name="Hooked", on_done=lambda t: called.append("done"))
        def fn(task): return 1

        t = pulse.run(fn)
        pulse.wait(t.id)
        assert "done" in called


# ── Concurrent tasks ──────────────────────────────────────────────────────────

class TestConcurrent:
    def test_multiple_tasks_run_concurrently(self, pulse):
        results = []
        def fn(task, n):
            time.sleep(0.1)
            results.append(n)
            return n

        tasks = [pulse.run(fn, i) for i in range(4)]
        pulse.wait(*[t.id for t in tasks], timeout=2)
        assert len(results) == 4

    def test_all_tasks_tracked(self, pulse):
        def fn(task): return 1
        for _ in range(5):
            pulse.run(fn)
        pulse.wait()
        assert len(pulse.all_tasks()) == 5

    def test_get_task_by_id(self, pulse):
        def fn(task): return 1
        t = pulse.run(fn)
        found = pulse.get(t.id)
        assert found is t


# ── SQLite history ────────────────────────────────────────────────────────────

class TestHistory:
    def test_history_persisted(self, pulse):
        def fn(task): return "saved"
        t = pulse.run(fn)
        pulse.wait(t.id)
        hist = pulse.history()
        assert any(h["id"] == t.id for h in hist)

    def test_history_includes_status(self, pulse):
        def fn(task): return 1
        t = pulse.run(fn)
        pulse.wait(t.id)
        hist = {h["id"]: h for h in pulse.history()}
        assert hist[t.id]["status"] == "done"

    def test_failed_task_in_history(self, pulse):
        def bad(task): raise RuntimeError("fail")
        t = pulse.run(bad)
        pulse.wait(t.id)
        hist = {h["id"]: h for h in pulse.history()}
        assert hist[t.id]["status"] == "failed"

    def test_history_limit(self, pulse):
        def fn(task): return 1
        for _ in range(10):
            t = pulse.run(fn)
        pulse.wait()
        hist = pulse.history(limit=5)
        assert len(hist) <= 5


# ── Task decorator ────────────────────────────────────────────────────────────

class TestDecorator:
    def test_task_decorator(self, pulse):
        @pulse.task(name="Decorated", total=5)
        def fn(task):
            for i in range(5):
                task.update(i + 1)
            return "done"

        t = pulse.run(fn)
        pulse.wait(t.id)
        assert t.result == "done"
        assert t.name == "Decorated"

    def test_task_decorator_preserves_function(self, pulse):
        @pulse.task()
        def my_func(task): return 1
        assert callable(my_func)


# ── Wait with timeout ─────────────────────────────────────────────────────────

class TestWait:
    def test_wait_returns_tasks(self, pulse):
        def fn(task): return 1
        t = pulse.run(fn)
        tasks = pulse.wait(t.id)
        assert len(tasks) == 1
        assert tasks[0].id == t.id

    def test_wait_timeout(self, pulse):
        def slow(task):
            time.sleep(5)
            return 1
        t = pulse.run(slow)
        tasks = pulse.wait(t.id, timeout=0.2)
        assert tasks[0].status in (TaskStatus.RUNNING, TaskStatus.DONE)
