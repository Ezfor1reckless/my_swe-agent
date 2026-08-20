# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

mini-swe-agent is a deliberately minimal AI software engineering agent: it solves GitHub issues / programming tasks by having an LLM issue bash commands. The whole agent is ~100 lines; the project's core value is **simplicity and readability** over features. Read [src/minisweagent/agents/default.py](src/minisweagent/agents/default.py) first — everything else is pluggable parts around that loop.

For project conventions (commit message format, style guide, test style), see [AGENTS.md](AGENTS.md) and [.github/copilot-instructions.md](.github/copilot-instructions.md) — they are authoritative. The essentials are repeated below.

## Commands

```bash
# Install for development
pip install -e '.[dev]'          # core + test/lint/docs deps
pre-commit install               # install git hooks (ruff, typos, prettier)
pip install -e '.[full]'         # also installs extra/ deps (swe-rex, modal, contree, ...)
uv sync --group dev              # alternative with uv (see pyproject dependency-groups)

# Run the CLI (typer app)
mini                             # interactive agent (default: local env, interactive agent)
python -m minisweagent           # same as `mini`
mini-extra                       # config / inspector / benchmark subcommands

# Tests (pytest, asyncio_mode=auto; run in parallel)
pytest -n auto                   # full suite (~3min)
pytest tests/models/test_litellm_model.py          # single file
pytest tests/models/test_litellm_model.py::test_name   # single test
pytest -k "not slow"             # skip slow marker tests
pytest --run-fire                # tests that make real (paid) API calls — never by default
# Note: container tests auto-skip unless docker/podman is running; some tests
# need API keys set via `mini-extra config set KEY VALUE` (see CI: pytest.yaml).

# Lint / format (via pre-commit; also runnable directly)
pre-commit run --all-files
ruff check --fix .
ruff format .

# Docs (mkdocs material)
mkdocs serve                      # local docs server
mike deploy latest                # versioned docs deploy
```

## Architecture

The design is **polymorphism over four components**. Everything lives under `src/minisweagent/` (package name `minisweagent`, src-layout):

- **Protocols** — [src/minisweagent/__init__.py](src/minisweagent/__init__.py) defines `Model`, `Environment`, `Agent` protocols (duck-typed). Also defines `__version__`, `package_dir`, and the global config file path (`~/.config/mini-swe-agent/.env`, overridable via `MSWEA_GLOBAL_CONFIG_DIR`). Dotenv is loaded here on import.
- **agents/** — control flow. `DefaultAgent.run()` is a linear loop: render system+instance templates → `query()` the model → `execute_actions()` each parsed action → `format_observation_messages()` feed outputs back → repeat until a message has `role == "exit"`. History is **completely linear** (no stateful shell, no history processing). `InteractiveAgent` subclasses it to put the user in the loop (modes `human`/`confirm`/`yolo`, slash commands `/y` `/c` `/u` `/m`).
- **environments/** — execute actions. `LocalEnvironment` runs each action as an independent `subprocess.run` (per-action timeout, kills process group). Other impls: `docker`, `singularity`, `bash` (Git Bash on Windows), and `extra/` (bubblewrap, contree, swerex). A command whose first output line is `COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT` with returncode 0 triggers submission (raises `Submitted`). `get_template_vars()` merges platform info into prompt templates.
- **models/** — LM interfaces. `LitellmModel` is the default: calls `litellm.completion` with a single `bash` tool, parses tool calls into `extra.actions`, computes cost. Variants: `openrouter_*`, `portkey_*`, `requesty`, textbased (no tool-calling) and response (structured-output) variants. Model selection/cost tracking in `models/__init__.py` (`get_model`, `GLOBAL_MODEL_STATS`).
- **run/** — entry points / run scripts. Each use case starts with a run script that wires up one agent + one environment + one model. `mini.py` is the `mini` CLI; `hello_world.py` is the minimal python-bindings example; `benchmarks/` contains `swebench`, `swebench_single`, `programbench` runners; `utilities/` has `config`, `inspector`, `mini_extra`.

### Key flows and concepts

- **Every action is independent** — each command runs in a fresh subshell (`subprocess.run`), so env vars / cwd don't persist between steps. The agent is told to use inline `VAR=x cmd && ...` prefixes. This is the core design decision enabling sandboxing and scaling.
- **Control flow via exceptions** — [exceptions.py](src/minisweagent/exceptions.py): `InterruptAgentFlow` and subclasses (`Submitted`, `LimitsExceeded`, `TimeExceeded`, `UserInterruption`, `FormatError`) carry messages that get appended into the trajectory. `DefaultAgent.run()` catches them and turns them into `role: "exit"` messages.
- **Config** — plain YAML files in [src/minisweagent/config/](src/minisweagent/config/) (`mini.yaml`, `default.yaml`, `benchmarks/*.yaml`), plus CLI `key=value` specs and env vars (`MSWEA_MODEL_NAME`, `MSWEA_CONFIG_DIR`, ...). Multiple sources are combined with `recursive_merge` ([utils/serialize.py](src/minisweagent/utils/serialize.py)) — later dicts win, `UNSET` values are skipped, nested dicts merge recursively. Agent/env/model configs are pydantic models (`*Config`) with `config_class=` override. Most template strings in configs are **jinja2** rendered with `StrictUndefined` using `get_template_vars()` from agent+env+model.
- **Factories** — `get_model` / `get_environment` / `get_agent` in the respective `__init__.py` resolve a short name (e.g. `local`, `litellm`) or a full import path to a class and instantiate it from a config dict. Adding a new component = implement the protocol + register in the mapping + (optionally) a config file.
- **Trajectories** — `agent.save(output_path)` writes JSON with `messages`, `info` (model/env configs, stats), and `trajectory_format: "mini-swe-agent-1.1"`. Test fixtures in `tests/test_data/*.traj.json` are replayed against this format.
- **API keys** — stored via `mini-extra config set KEY VALUE` into the global `.env` file. `minisweagent/__init__.py` warns loudly on startup unless `MSWEA_SILENT_STARTUP` is set (tests set it).

## Extended capabilities

Optional variants extend the base loop, each registered in the relevant factory and opt-in via a config file or `model_class`/`agent_class`/`environment_class`. They compose freely (e.g. `memory` agent + `compressing` model + `background` env in one config).

- **Long-term memory** — [agents/memory.py](src/minisweagent/agents/memory.py): `MemoryAgent` (agent_class `memory`) loads `memory.md` from the global config dir into a `{{ memory }}` template var before each run, then summarizes the completed session and appends it back. Summary uses `_summary_model()` (overridable; defaults to the wrapped model) so a cheaper model can summarize. Config file: [config/mini_memory.yaml](src/minisweagent/config/mini_memory.yaml).
- **Background tasks** — [environments/background.py](src/minisweagent/environments/background.py): `BackgroundEnvironment` (environment_class `background`) detects a `<background>` suffix and starts a detached process (drains its output into a bounded buffer), `<bg:query>` to check status/output, `bg_kill` to kill a task. Env var `background_tasks` exposes task state to prompts. Config file: [config/mini_background.yaml](src/minisweagent/config/mini_background.yaml).
- **Context auto-compression** — [models/compressing_model.py](src/minisweagent/models/compressing_model.py): `CompressingModel` (model_class `compressing`) wraps any model; when the token count (via `litellm.token_counter`) exceeds `compression_threshold_tokens`, old messages are replaced by a summary while the last `keep_recent_messages` stay verbatim (split keeps an assistant boundary so tool results stay paired). Config file: [config/mini_compressing.yaml](src/minisweagent/config/mini_compressing.yaml).
- **MCP** — [models/mcp_model.py](src/minisweagent/models/mcp_model.py) + [agents/mcp_agent.py](src/minisweagent/agents/mcp_agent.py): `MCPModel` (model_class `mcp`, extends `LitellmModel`) attaches MCP servers (stdio or SSE, via `mcp_servers` config) and exposes their tools alongside `bash`; `MCPAgent` (agent_class `mcp`) routes `mcp_server` actions to the tool. Requires the optional `[mcp]` extra (`pip install -e '.[mcp]'`); imports are guarded so the core works without it. Config file: [config/mini_mcp.yaml](src/minisweagent/config/mini_mcp.yaml).
- **Bash environment (Windows)** — [environments/extra/bash.py](src/minisweagent/environments/extra/bash.py): `BashEnvironment` (environment_class `bash`) runs every command via `bash -c` instead of `cmd.exe`. On Windows the local env shells out to `cmd.exe`, which can't run the bash syntax the agent is taught (`cat <<'EOF'` heredocs, `VAR=x cmd`, `sed -i`); this env resolves Git Bash so the agent templates work unchanged. `mini-task` defaults to it on Windows; on POSIX it's equivalent to `local`.

## Conventions

- Write **minimal, concise** code; the project explicitly rewards minimalism. Don't add features via flags/config if a small standalone run script or a new class variant is simpler.
- New, more specific variants of a component go in the component's `extra/` subfolder (e.g. `models/extra/`, `environments/extra/`).
- Python >= 3.10, full type annotations (`list` not `List`), `pathlib` over `os.path`, `typer` for CLIs, jinja2 for templates, pydantic configs, `dataclass` where pydantic isn't needed.
- Comments are discouraged — only for genuinely tricky logic. Code should be self-documenting.
- Tests: pytest only, **do not mock/patch unless asked**, no trivial tests, no `a = func(); assert a == b` (inline it), `pytest.mark.parametrize` first arg is a tuple, second is a list. `print()` in tests is fine.
- **Never** add "Co-authored-by: Cursor" lines to commits. Follow the commit message prefix scheme in AGENTS.md (`feat(component):`, `fix(component):`, `dev:`, `ci:`, `chore:`, ...) with component names `models|agents|env|config|run|benchmarks|cli|deps`.
