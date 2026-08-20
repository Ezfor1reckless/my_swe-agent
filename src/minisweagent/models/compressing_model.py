"""Model wrapper that compresses a long history before it exceeds the context window.

When the token count of the conversation reaches ``compression_threshold_tokens``,
the oldest messages (after the system message) are replaced by a single summary
message produced by a (possibly different, cheaper) model. The most recent
``keep_recent_messages`` messages are always preserved verbatim.

Delegates all other :class:`Model` protocol methods to the wrapped model, so this
can wrap any model (litellm, openrouter, ...) and composes with other variants.
"""

import json
import logging
from collections.abc import Callable
from typing import Any

from pydantic import BaseModel

from minisweagent.models import get_model
from minisweagent.models.utils.content_string import get_content_string

logger = logging.getLogger("compressing_model")

_DEFAULT_SUMMARY_TEMPLATE = (
    "Please summarize the following conversation so that all relevant information "
    "(decisions, findings, file changes, commands run, errors and their fixes) is "
    "retained for continuing the task. Do not invent anything. Return only the summary.\n\n"
    "{% for msg in messages %}\n### {{ msg.role }}\n{{ msg.content }}\n{% endfor %}"
)


class CompressingModelConfig(BaseModel):
    model_name: str
    model_class: str = "litellm"
    model_kwargs: dict[str, Any] = {}
    compression_threshold_tokens: int = 60_000
    """Compress when the estimated token count exceeds this value (0 = never)."""
    keep_recent_messages: int = 8
    """Number of most recent messages to keep verbatim when compressing."""
    summary_model_name: str | None = None
    """Model used to produce the summary (defaults to the wrapped model)."""
    summary_model_class: str | None = None
    summary_prompt_template: str = _DEFAULT_SUMMARY_TEMPLATE


class CompressingModel:
    """Wraps another model and transparently compresses an over-long history."""

    def __init__(self, *, config_class: Callable = CompressingModelConfig, **kwargs):
        self.config = config_class(**kwargs)
        self.model = get_model(
            config={"model_name": self.config.model_name, "model_class": self.config.model_class}
            | self.config.model_kwargs
        )
        self.n_compressions = 0
        self._tokens_before = 0
        self._tokens_after = 0

    def _summary_model(self) -> Any:
        if self.config.summary_model_name:
            return get_model(
                config={
                    "model_name": self.config.summary_model_name,
                    "model_class": self.config.summary_model_class or "litellm",
                }
            )
        return self.model

    @staticmethod
    def _flat(messages: list[dict]) -> list[dict]:
        """Flatten arbitrary messages into {"role", "content"} pairs for counting/summaries."""
        return [
            {"role": (m.get("role") or m.get("type", "unknown")).replace(" ", "_"), "content": get_content_string(m)}
            for m in messages
        ]

    @staticmethod
    def _count_tokens(messages: list[dict]) -> int:
        try:
            import litellm

            return litellm.token_counter(model="gpt-4o", messages=CompressingModel._flat(messages))
        except Exception as e:  # pragma: no cover - defensive
            logger.warning("Could not estimate token count: %s", e)
            return 0

    def _query_summary(self, messages: list[dict]) -> dict:
        from jinja2 import StrictUndefined, Template

        prompt = Template(self.config.summary_prompt_template, undefined=StrictUndefined).render(
            messages=self._flat(messages)
        )
        response = self._summary_model().query([{"role": "user", "content": prompt}])
        content = response.get("content") or ""
        if isinstance(content, list):
            content = "".join(item.get("text", "") for item in content if isinstance(item, dict))
        return {
            "role": "user",
            "content": f"Summary of earlier conversation:\n{content.strip()}",
            "extra": {"compressed": True},
        }

    def _split(self, messages: list[dict], keep: int) -> tuple[int, list[dict]]:
        """Find the split index so ``messages[:i]`` is compressed and ``messages[i:]`` kept.

        The kept segment is guaranteed to start with an ``assistant`` message, so an
        assistant tool-call is never separated from its ``tool`` results.
        """
        if keep <= 0:
            return 0, messages
        recent: list[dict] = []
        for msg in reversed(messages):
            recent.append(msg)
            if len(recent) >= keep and msg.get("role") == "assistant":
                break
        recent.reverse()
        return len(messages) - len(recent), recent

    def _compress(self, messages: list[dict]) -> list[dict]:
        """Replace old messages with a summary; keep the newest verbatim."""
        system = [m for m in messages if m.get("role") == "system"]
        rest = [m for m in messages if m.get("role") != "system"]
        i, recent = self._split(rest, self.config.keep_recent_messages)
        old = rest[:i]
        if len(old) < 2:
            return messages

        summary = self._query_summary(old)
        if not summary:
            return messages
        self.n_compressions += 1
        self._tokens_after = self._count_tokens(system + [summary] + recent)
        return system + [summary] + recent

    # -- Model protocol (delegate to wrapped model) ---------------------

    def query(self, messages: list[dict[str, str]], **kwargs) -> dict:
        self._tokens_before = self._count_tokens(messages)
        if 0 < self.config.compression_threshold_tokens < self._tokens_before:
            messages = self._compress(messages)
        return self.model.query(messages, **kwargs)

    def format_message(self, **kwargs) -> dict:
        return self.model.format_message(**kwargs)

    def format_observation_messages(
        self, message: dict, outputs: list[dict], template_vars: dict | None = None
    ) -> list[dict]:
        return self.model.format_observation_messages(message, outputs, template_vars)

    def get_template_vars(self, **kwargs) -> dict[str, Any]:
        return self.model.get_template_vars(**kwargs) | {
            "n_compressions": self.n_compressions,
            "tokens_before": self._tokens_before,
            "tokens_after": self._tokens_after,
        }

    def serialize(self) -> dict:
        return {
            "info": {
                "config": {
                    "model": json.loads(self.config.model_dump_json()),
                    "model_type": f"{self.__class__.__module__}.{self.__class__.__name__}",
                    "compression_stats": {
                        "n_compressions": self.n_compressions,
                        "tokens_before": self._tokens_before,
                        "tokens_after": self._tokens_after,
                    },
                },
            }
        }
