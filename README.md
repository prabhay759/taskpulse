# taskpulse

> Lightweight background task monitor — live progress bars, ETA, elapsed time, concurrent tasks, callback hooks, and SQLite history. No Celery, no Redis, pure Python.

[![PyPI version](https://img.shields.io/pypi/v/taskpulse.svg)](https://pypi.org/project/taskpulse/)
[![Python](https://img.shields.io/pypi/pyversions/taskpulse.svg)](https://pypi.org/project/taskpulse/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

---

## Installation

```bash
pip install taskpulse
```

No dependencies. Requires Python 3.8+.

---

## Quick Start

```python
from taskpulse import TaskPulse

pulse = TaskPulse()

@pulse.task(name="Process files", total=100)
def process(task):
    for i in range(100):
        do_work()
        task.update(i + 1)
    return "done"

t = pulse.run(process)
pulse.wait(t.id)
print(t.result)   # "done"
```

Terminal output:
```
  ⚡ Process files           [████████████░░░░░░░░░░░░░░░░░░]  41.0%  elapsed 2.1s  ETA 3.0s
  ✅ Process files           [done]                             elapsed 5.1s
```

---

## Usage

### Define Tasks

```python
# Decorator style
@pulse.task(name="My Task", total=50)
def my_task(task):
    for i in range(50):
        process_item(i)
        task.update(i + 1)   # report progress
    return "finished"

# Run it
t = pulse.run(my_task)
```

```python
# Inline (no decorator)
def simple(task, items):
    for i, item in enumerate(items):
        process(item)
        task.update(i + 1, total=len(items))
    return len(items)

t = pulse.run(simple, my_items, name="Process items")
```

### Progress Updates

```python
def fn(task):
    task.update(5)           # just update progress
    task.update(10, total=50)  # also update total
```

### Concurrent Tasks

```python
tasks = [pulse.run(process, batch) for batch in batches]
pulse.wait(*[t.id for t in tasks])   # wait for all
```

### Hooks

```python
def on_start(task):
    print(f"Starting: {task.name}")

def on_progress(task):
    if task.percent and task.percent > 50:
        notify_team("halfway done")

def on_done(task):
    send_email(f"Task complete: {task.result}")

def on_error(task):
    alert(f"Task failed: {task.error}")

@pulse.task(
    name="Important Job",
    on_start=on_start,
    on_progress=on_progress,
    on_done=on_done,
    on_error=on_error,
)
def important_job(task):
    ...
```

### Task Status

```python
task = pulse.get("task-0001")
print(task.status)    # TaskStatus.RUNNING
print(task.percent)   # 42.0
print(task.elapsed)   # 3.2  (seconds)
print(task.eta)       # 4.4  (seconds remaining)
print(task.result)    # None while running
print(task.error)     # None if no error
```

### Live Dashboard

```python
from taskpulse import TaskPulse
from taskpulse import Monitor

pulse = TaskPulse(show_progress=False)
monitor = Monitor(pulse, refresh=0.5)
monitor.start()

for batch in batches:
    pulse.run(process, batch)

pulse.wait()
monitor.stop()
```

### SQLite History

```python
# Full run history persisted across restarts
history = pulse.history(limit=20)
for run in history:
    print(run["name"], run["status"], run["elapsed"])
```

---

## API Reference

### `TaskPulse`

```python
TaskPulse(
    db_path="taskpulse.db",  # SQLite history file
    max_workers=4,            # Max concurrent tasks
    show_progress=True,       # Live progress bars
)
```

| Method | Description |
|---|---|
| `task(name, total, on_start, ...)` | Decorator to define a task |
| `run(func, *args, **kwargs)` | Submit task, returns Task immediately |
| `wait(*task_ids, timeout)` | Block until tasks complete |
| `get(task_id)` | Get Task by ID |
| `all_tasks()` | List all Task objects |
| `status()` | List of status dicts |
| `history(limit)` | Run history from SQLite |
| `print_status()` | Print formatted table |
| `cancel(task_id)` | Cancel a pending task |

### `Task`

| Attribute | Type | Description |
|---|---|---|
| `id` | `str` | Unique task ID |
| `name` | `str` | Task name |
| `status` | `TaskStatus` | pending/running/done/failed/cancelled |
| `progress` | `int` | Current step count |
| `total` | `int \| None` | Total steps |
| `percent` | `float \| None` | 0-100 |
| `elapsed` | `float` | Seconds since start |
| `eta` | `float \| None` | Estimated seconds remaining |
| `result` | `Any` | Return value (when done) |
| `error` | `str \| None` | Traceback (when failed) |

---

## Running Tests

```bash
pip install pytest
pytest tests/ -v
```

---

## License

MIT © prabhay759
