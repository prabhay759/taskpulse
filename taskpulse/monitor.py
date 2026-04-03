"""
taskpulse.monitor
-----------------
Live terminal monitor — refreshes task status table in place.
"""

import threading
import time
from typing import Optional

from .core import TaskPulse, TaskStatus


class Monitor:
    """
    Live terminal dashboard for TaskPulse.

    Refreshes a status table in-place while tasks are running.

    Example
    -------
    >>> pulse = TaskPulse(show_progress=False)
    >>> monitor = Monitor(pulse, refresh=0.5)
    >>> monitor.start()
    >>> pulse.run(my_task)
    >>> pulse.wait()
    >>> monitor.stop()
    """

    def __init__(self, pulse: TaskPulse, refresh: float = 0.5):
        self.pulse = pulse
        self.refresh = refresh
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._last_lines = 0

    def start(self) -> "Monitor":
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        return self

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2)
        self._clear()

    def _loop(self):
        while not self._stop.is_set():
            self._render()
            self._stop.wait(self.refresh)
        self._render(final=True)

    def _clear(self):
        if self._last_lines > 0:
            print(f"\033[{self._last_lines}A\033[J", end="", flush=True)
            self._last_lines = 0

    def _render(self, final: bool = False):
        tasks = self.pulse.all_tasks()
        lines = []
        lines.append(f"  ── TaskPulse Monitor ─────────────────────────────────")

        for t in tasks:
            pct = f"{t.percent:.1f}%" if t.percent is not None else "   —  "
            eta = f"ETA {t.eta:.1f}s" if t.eta else "      "
            elapsed = f"{t.elapsed:.1f}s"
            icon = {"pending": "⏳", "running": "⚡", "done": "✅",
                    "failed": "❌", "cancelled": "🚫"}.get(t.status.value, "•")

            if t.percent is not None and t.status == TaskStatus.RUNNING:
                bar_len = 20
                filled = int(bar_len * t.percent / 100)
                bar = "█" * filled + "░" * (bar_len - filled)
                bar_str = f"[{bar}] {pct}"
            else:
                bar_str = f"{'[done]' if t.status == TaskStatus.DONE else t.status.value:<26}"

            lines.append(
                f"  {icon} {t.name:<22} {bar_str}  {elapsed:>6}  {eta}"
            )

            if t.status == TaskStatus.FAILED and t.error:
                err_line = t.error.strip().splitlines()[-1][:60]
                lines.append(f"       ❗ {err_line}")

        lines.append("")

        self._clear()
        output = "\n".join(lines)
        print(output, flush=True)
        self._last_lines = len(lines)
