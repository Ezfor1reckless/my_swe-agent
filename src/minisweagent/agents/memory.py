"""Agent with persistent long-term memory across runs.

Memory is stored as markdown in a single file (default: the global config dir).
At the start of each run the file's content is exposed to prompt templates as
``{{ memory }}``, and at the end of each run a summary of the trajectory is
appended to the file.

The memory file is the only extra store: the trajectory stays linear, so the agent
loop is unchanged (this is a thin subclass of ``DefaultAgent``).
"""

import logging
from pathlib import Path

from minisweagent import Environment, Model, global_config_dir
from minisweagent.agents.default import AgentConfig, DefaultAgent

logger = logging.getLogger("memory_agent")

DEFAULT_MEMORY_FILE = global_config_dir / "memory.md"

_SUMMARY_TEMPLATE = (
    "You have just finished a coding task. Please write a short markdown summary "
    "of the session for future runs. Only report information that would still be "
    "useful in a future session, and omit anything that is obvious from the code. "
    "Use the following format:\n\n"
    "## Task\n<one sentence>\n\n"
    "## Decisions & gotchas\n<bullet list>\n\n"
    "## Files touched\n<bullet list>\n\n"
    "Conversation so far:\n{% for msg in messages %}\n"
    "### {{ msg.role }}\n{{ msg.content }}\n{% endfor %}"
)


class MemoryConfig(AgentConfig):
    memory_file: Path = DEFAULT_MEMORY_FILE
    """Where the persistent markdown memory is stored."""
    memory_read_limit: int = 500
    """Maximum characters of stored memory injected into the next run (0 = no limit)."""
    summary_template: str = _SUMMARY_TEMPLATE
    """Template used to ask the model for a session summary (rendered with jinja2)."""


class MemoryAgent(DefaultAgent):
    def __init__(self, model: Model, env: Environment, *, config_class: type = MemoryConfig, **kwargs):
        super().__init__(model, env, config_class=config_class, **kwargs)
        self._start_memory = ""

    def _load_memory(self) -> str:
        if not self.config.memory_file.exists():
            return ""
        content = self.config.memory_file.read_text(encoding="utf-8")
        if 0 < self.config.memory_read_limit < len(content):
            return content[: self.config.memory_read_limit]
        return content

    def _write_memory(self, content: str) -> None:
        self.config.memory_file.parent.mkdir(parents=True, exist_ok=True)
        self.config.memory_file.write_text(content, encoding="utf-8")

    def _summary_model(self) -> Model:
        """Model used to summarize the trajectory (defaults to the main model)."""
        return self.model

    def _summarize(self) -> str:
        """Summarize the trajectory into markdown."""
        from jinja2 import StrictUndefined, Template

        prompt = Template(self.config.summary_template, undefined=StrictUndefined).render(
            messages=self.messages,
            **self.get_template_vars(),
        )
        try:
            response = self._summary_model().query(
                [
                    {
                        "role": "user",
                        "content": prompt,
                    }
                ]
            )
        except Exception as e:
            logger.warning("Could not summarize trajectory for memory: %s", e)
            return ""
        content = response.get("content") or ""
        if isinstance(content, list):
            content = "".join(item.get("text", "") for item in content if isinstance(item, dict))
        return content.strip()

    def _update_memory_file(self) -> None:
        summary = self._summarize()
        if not summary:
            return
        old = self._start_memory
        new = f"{old}\n\n## Session\n\n{summary}\n" if old else f"## Session\n\n{summary}\n"
        self._write_memory(new)

    def run(self, task: str = "", **kwargs) -> dict:
        self._start_memory = self._load_memory()
        self.extra_template_vars |= {"memory": self._start_memory}
        try:
            result = super().run(task, **kwargs)
        finally:
            self._update_memory_file()
        return result

    def serialize(self, *extra_dicts) -> dict:
        return super().serialize(
            {
                "info": {
                    "config": {
                        "memory_file": str(self.config.memory_file),
                    }
                }
            },
            *extra_dicts,
        )
