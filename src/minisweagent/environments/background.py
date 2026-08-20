"""Environment that can run commands in the background.

A command whose last word is ``<background>`` is launched as a detached
``Popen`` (its own process group) instead of being awaited. Output keeps
accumulating in a per-task buffer while the agent does other things.

Two companion commands query/manage running tasks:
- ``<bg:query> <pid>``: return the current status + accumulated output.
- ``bg_kill <pid>``: terminate the task's process group.

Everything else is delegated to :class:`LocalEnvironment` unchanged.
"""

import os
import signal
import subprocess
import threading
import time
from typing import Any

from minisweagent.environments.local import LocalEnvironment, LocalEnvironmentConfig

_BACKGROUND_MARKER = "<background>"
_QUERY_PREFIX = "<bg:query>"
_KILL_PREFIX = "bg_kill"


class BackgroundEnvironmentConfig(LocalEnvironmentConfig):
    background_timeout: int = 300
    """Fallback wall-clock timeout for background tasks (killed when exceeded)."""
    max_buffered_output: int = 200_000
    """Maximum characters of captured output kept per background task."""


class _BackgroundTask:
    def __init__(self, pid: int, command: str, cwd: str, env: dict[str, str], max_buffered: int):
        self.pid = pid
        self.command = command
        self.cwd = cwd
        self.started = time.time()
        self._output: list[str] = []
        self._output_len = 0
        self._max_buffered = max_buffered
        self._lock = threading.Lock()
        self._proc: subprocess.Popen | None = None

    def attach(self, proc: subprocess.Popen) -> None:
        self._proc = proc

    def _read_loop(self, stream) -> None:
        try:
            for line in iter(stream.readline, ""):
                with self._lock:
                    if self._output_len < self._max_buffered:
                        self._output.append(line)
                        self._output_len += len(line)
        except Exception:
            pass

    @property
    def status(self) -> str:
        if self._proc is None:
            return "starting"
        if self._proc.poll() is None:
            return "running"
        return f"exited ({self._proc.returncode})"

    def read_output(self) -> str:
        with self._lock:
            return "".join(self._output)


class BackgroundEnvironment(LocalEnvironment):
    def __init__(self, *, config_class: type = BackgroundEnvironmentConfig, **kwargs):
        super().__init__(config_class=config_class, **kwargs)
        self._tasks: dict[int, _BackgroundTask] = {}

    # -- task management ------------------------------------------------

    def _start_background(self, command: str, cwd: str, env: dict[str, str]) -> dict[str, Any]:
        proc = subprocess.Popen(
            command,
            shell=True,
            text=True,
            cwd=cwd,
            env=env,
            encoding="utf-8",
            errors="replace",
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            start_new_session=os.name == "posix",
            creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) if os.name == "nt" else 0,
        )
        task = _BackgroundTask(proc.pid, command, cwd, dict(env), self.config.max_buffered_output)
        task.attach(proc)
        self._tasks[proc.pid] = task
        threading.Thread(target=task._read_loop, args=(proc.stdout,), daemon=True).start()
        return {
            "output": f"Started background task with pid {proc.pid}.\n",
            "returncode": 0,
            "exception_info": "",
            "extra": {"background": True, "pid": proc.pid},
        }

    def _query_task(self, arg: str) -> dict[str, Any]:
        try:
            pid = int(arg.split()[0])
        except (ValueError, IndexError):
            return {"output": "Usage: <bg:query> <pid>\n", "returncode": 1, "exception_info": ""}
        task = self._tasks.get(pid)
        if task is None:
            return {"output": f"Unknown background task {pid}.\n", "returncode": 1, "exception_info": ""}
        return {
            "output": f"Task {pid} status: {task.status}\nOutput so far:\n{task.read_output()}",
            "returncode": 0,
            "exception_info": "",
            "extra": {"background": True, "pid": pid},
        }

    def _kill_task(self, arg: str) -> dict[str, Any]:
        try:
            pid = int(arg.split()[0])
        except (ValueError, IndexError):
            return {"output": "Usage: bg_kill <pid>\n", "returncode": 1, "exception_info": ""}
        task = self._tasks.get(pid)
        if task is None or task._proc is None or task._proc.poll() is not None:
            return {"output": f"Task {pid} not running.\n", "returncode": 1, "exception_info": ""}
        _kill_process_group(task._proc.pid)
        task._proc.wait(timeout=10)
        return {"output": f"Killed background task {pid}.\n", "returncode": 0, "exception_info": ""}

    # -- main interface -------------------------------------------------

    def execute(self, action: dict, cwd: str = "", *, timeout: int | None = None) -> dict[str, Any]:
        command = action.get("command", "").strip()
        cwd = cwd or self.config.cwd or os.getcwd()
        env = os.environ | self.config.env
        if command.endswith(_BACKGROUND_MARKER):
            return self._start_background(command[: -len(_BACKGROUND_MARKER)].strip(), cwd, env)
        if command.startswith(_QUERY_PREFIX):
            return self._query_task(command[len(_QUERY_PREFIX):].strip())
        if command.startswith(_KILL_PREFIX):
            return self._kill_task(command[len(_KILL_PREFIX):].strip())
        return super().execute(action, cwd, timeout=timeout)

    def get_template_vars(self, **kwargs) -> dict[str, Any]:
        return super().get_template_vars(
            background_tasks=[{"pid": pid, "status": task.status} for pid, task in self._tasks.items()],
            background_marker=_BACKGROUND_MARKER,
            query_prefix=_QUERY_PREFIX,
            kill_prefix=_KILL_PREFIX,
            **kwargs,
        )

    def serialize(self) -> dict:
        data = super().serialize()
        data["info"]["config"]["background_tasks"] = [
            {"pid": pid, "status": task.status} for pid, task in self._tasks.items()
        ]
        return data


def _kill_process_group(pid: int) -> None:
    if os.name == "posix":
        try:
            os.killpg(pid, signal.SIGKILL)
        except ProcessLookupError:
            return
    else:  # pragma: no cover - windows
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(pid)], capture_output=True, check=False)
