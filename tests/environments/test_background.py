import sys
import time

from minisweagent.environments.background import BackgroundEnvironment


def _python_echo_delay(seconds: float) -> str:
    """A python one-liner that prints before and after a delay (unbuffered)."""
    return f"{sys.executable} -u -c \"import time; print('started'); time.sleep({seconds}); print('finished')\""


def test_background_task_is_nonblocking_and_accumulates_output():
    """A command ending in <background> returns immediately and its output accumulates."""
    env = BackgroundEnvironment()
    start = time.time()
    result = env.execute({"command": f"{_python_echo_delay(1.5)} <background>"})
    elapsed = time.time() - start
    assert elapsed < 1.0, "background launch must not block"
    assert result["returncode"] == 0
    pid = result["extra"]["pid"]
    assert pid > 0

    # Give it a moment, then query
    time.sleep(0.5)
    query = env.execute({"command": f"<bg:query> {pid}"})
    assert query["returncode"] == 0
    assert "started" in query["output"]
    assert "finished" not in query["output"]  # still running

    # Wait for it to finish
    time.sleep(1.5)
    query2 = env.execute({"command": f"<bg:query> {pid}"})
    assert "finished" in query2["output"]
    assert "exited (0)" in query2["output"]


def test_background_task_kill():
    """bg_kill terminates a running background task."""
    env = BackgroundEnvironment()
    result = env.execute({"command": f"{_python_echo_delay(60)} <background>"})
    pid = result["extra"]["pid"]
    assert env.execute({"command": f"<bg:query> {pid}"})["returncode"] == 0

    kill = env.execute({"command": f"bg_kill {pid}"})
    assert kill["returncode"] == 0
    # After the kill, querying reports the task is no longer running
    time.sleep(0.3)
    query = env.execute({"command": f"<bg:query> {pid}"})
    assert "running" not in query["output"]


def test_unknown_background_task_errors():
    """Querying/killing an unknown pid returns a non-zero returncode."""
    env = BackgroundEnvironment()
    assert env.execute({"command": "<bg:query> 99999999"})["returncode"] == 1
    assert env.execute({"command": "bg_kill 99999999"})["returncode"] == 1


def test_plain_commands_still_work():
    """Non-background commands are delegated to the base environment."""
    env = BackgroundEnvironment()
    result = env.execute({"command": "echo plain"})
    assert result["returncode"] == 0
    assert "plain" in result["output"]


def test_background_tasks_in_template_vars_and_serialization():
    """background_tasks appears in template vars and serialization."""
    env = BackgroundEnvironment()
    result = env.execute({"command": f"{_python_echo_delay(5)} <background>"})
    pid = result["extra"]["pid"]
    vars = env.get_template_vars()
    assert any(t["pid"] == pid for t in vars["background_tasks"])
    serialized = env.serialize()
    assert any(t["pid"] == pid for t in serialized["info"]["config"]["background_tasks"])
