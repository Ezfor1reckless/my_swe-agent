"""Run mini-SWE-agent on a task read from a file.

This is a thin convenience wrapper around the default agent that reads the task
description from a text/markdown file instead of the command line. Reading from
a file avoids encoding issues when the task contains non-ASCII text (e.g. Chinese)
on Windows terminals, where command-line arguments can be mangled by the console
codepage.

Example:
    mini-task task.md --model openrouter/google/gemini-2.5-flash
"""

import os
import sys
from pathlib import Path
from typing import Any

import typer
from rich.console import Console

from minisweagent.agents import get_agent
from minisweagent.config import get_config_from_spec
from minisweagent.environments import get_environment
from minisweagent.models import get_model
from minisweagent.utils.serialize import UNSET, recursive_merge

# Force UTF-8 so Chinese (and other non-ASCII) task text survives both the
# template rendering and the subprocesses the agent spawns.
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
os.environ.setdefault("PYTHONUTF8", "1")
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

console = Console(highlight=False)
app = typer.Typer(rich_markup_mode="rich")


@app.command(help="Run the agent on a task read from a file.")
def main(
    task_file: Path = typer.Argument(..., help="Path to a text/markdown file containing the task", exists=True),
    model_name: str = typer.Option(
        os.getenv("MSWEA_MODEL_NAME", "openrouter/google/gemini-2.5-flash"),
        "-m",
        "--model",
        help="Model name (default: MSWEA_MODEL_NAME env var, else openrouter/google/gemini-2.5-flash)",
    ),
    config_spec: list[str] = typer.Option(
        ["mini.yaml"],
        "-c",
        "--config",
        help="Config files (see --help of `mini`). Defaults to mini.yaml.",
    ),
    output: Path | None = typer.Option(None, "-o", "--output", help="Output trajectory file"),
    exit_immediately: bool = typer.Option(
        False,
        "--exit-immediately",
        help="Exit immediately when the agent wants to finish instead of prompting.",
        rich_help_panel="Advanced",
    ),
) -> Any:
    task = task_file.read_text(encoding="utf-8")

    configs = [get_config_from_spec(spec) for spec in config_spec]
    configs.append(
        {
            "run": {"task": task},
            "agent": {
                "agent_class": UNSET,
                "mode": "yolo",
                "confirm_exit": False if exit_immediately else UNSET,
                "output_path": output or UNSET,
            },
            "model": {
                "model_name": model_name or UNSET,
            },
        }
    )
    config = recursive_merge(*configs)

    # Be lenient about cost tracking by default: models not in litellm's local
    # price table (e.g. openrouter/google/gemini-2.5-flash) raise on cost calc,
    # which would abort the whole run. Users can opt back into strict tracking
    # with `-c model.cost_tracking=default`.
    config.setdefault("model", {}).setdefault("cost_tracking", "ignore_errors")

    model = get_model(config=config.get("model", {}))
    # On Windows the local env shells out to cmd.exe, which can't run the bash
    # syntax the agent is taught. Default to the bash env (Git Bash) there.
    default_env = "bash" if os.name == "nt" else "local"
    env = get_environment(config.get("environment", {}), default_type=default_env)
    agent = get_agent(model, env, config.get("agent", {}), default_type="default")
    agent.run(task)
    if output:
        console.print(f"Saved trajectory to [bold green]'{output}'[/bold green]")
    return agent


if __name__ == "__main__":
    app()
