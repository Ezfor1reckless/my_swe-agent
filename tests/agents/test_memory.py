from pathlib import Path

from minisweagent.agents.memory import MemoryAgent
from minisweagent.environments.local import LocalEnvironment
from minisweagent.models.test_models import DeterministicModel, make_output

CONFIG = {
    "system_template": "You are a bot.",
    "instance_template": "Memory: {{ memory }}\nTask: {{task}}",
}


def _memory_agent(tmp_path: Path, main_outputs: list[dict], memory_file: Path, summary_outputs: list[dict]) -> MemoryAgent:
    model = DeterministicModel(outputs=main_outputs)
    agent = MemoryAgent(
        model,
        LocalEnvironment(),
        memory_file=memory_file,
        **CONFIG,
    )
    # The summary query runs on its own model so the main-loop output stream is not consumed.
    agent._summary_model = lambda: DeterministicModel(outputs=summary_outputs)  # type: ignore[method-assign]
    return agent


def test_no_memory_file_yields_empty_memory(tmp_path):
    """A missing memory file results in an empty {{ memory }} template var."""
    memory_file = tmp_path / "memory.md"
    outputs = [
        make_output("Run", [{"command": "echo hi"}]),
        make_output(
            "Done",
            [{"command": "echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT\necho done"}],
        ),
    ]
    agent = _memory_agent(tmp_path, outputs, memory_file, [make_output("Summary", [])])
    agent.run("Task")
    # First user message (index 1) contains the instance template with empty memory
    assert "Task: Task" in agent.messages[1]["content"]


def test_memory_is_injected_and_persisted(tmp_path):
    """Memory from a previous run is injected into the next run's templates."""
    memory_file = tmp_path / "memory.md"
    outputs1 = [
        make_output("Run", [{"command": "echo hi"}]),
        make_output(
            "Done",
            [{"command": "echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT\necho done"}],
        ),
    ]
    summary1 = [make_output("Session summary: fixed the bug in parser.py", [])]
    agent1 = _memory_agent(tmp_path, outputs1, memory_file, summary1)
    agent1.run("First task")
    assert memory_file.exists()
    assert "fixed the bug in parser.py" in memory_file.read_text()

    outputs2 = [
        make_output("Run2", [{"command": "echo hi"}]),
        make_output(
            "Done2",
            [{"command": "echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT\necho done2"}],
        ),
    ]
    summary2 = [make_output("Second summary", [])]
    agent2 = _memory_agent(tmp_path, outputs2, memory_file, summary2)
    agent2.run("Second task")
    # The second run's instance template should include the persisted memory
    assert "fixed the bug in parser.py" in agent2.messages[1]["content"]
    # And the memory file should accumulate
    assert "Second summary" in memory_file.read_text()


def test_memory_read_limit_truncates(tmp_path):
    """memory_read_limit caps how much stored memory is injected."""
    memory_file = tmp_path / "memory.md"
    memory_file.write_text("a" * 1000)
    outputs = [
        make_output("Run", [{"command": "echo hi"}]),
        make_output(
            "Done",
            [{"command": "echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT\necho done"}],
        ),
    ]
    agent = _memory_agent(tmp_path, outputs, memory_file, [make_output("Summary", [])])
    agent.config.memory_read_limit = 10
    agent.run("Task")
    assert agent.extra_template_vars["memory"] == "a" * 10


def test_summary_failure_does_not_crash(tmp_path):
    """A failing summary query leaves the memory file unchanged."""
    memory_file = tmp_path / "memory.md"
    memory_file.write_text("original")
    outputs = [
        make_output("Run", [{"command": "echo hi"}]),
        make_output(
            "Done",
            [{"command": "echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT\necho done"}],
        ),
    ]
    agent = _memory_agent(tmp_path, outputs, memory_file, [])  # no summary outputs -> IndexError
    info = agent.run("Task")
    assert info["exit_status"] == "Submitted"
    assert memory_file.read_text() == "original"


def test_serialize_includes_memory_file(tmp_path):
    """serialize() records the memory file path."""
    memory_file = tmp_path / "memory.md"
    outputs = [
        make_output("Run", [{"command": "echo hi"}]),
        make_output(
            "Done",
            [{"command": "echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT\necho done"}],
        ),
    ]
    agent = _memory_agent(tmp_path, outputs, memory_file, [make_output("Summary", [])])
    data = agent.serialize()
    assert str(memory_file) == data["info"]["config"]["memory_file"]
