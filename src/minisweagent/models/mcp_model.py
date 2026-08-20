"""Model that exposes Model Context Protocol (MCP) server tools to the agent.

Each configured MCP server is started with the official ``mcp`` Python SDK, its
``tools/list`` is merged with the built-in ``bash`` tool, and tool calls are routed
back to the owning server via ``tools/call``.

Because MCP tools are executed by the model (not by the environment), this model is
paired with the :class:`MCPAgent` variant, which overrides ``execute_actions`` to
send ``mcp``-tagged actions to :meth:`MCPModel.call_mcp_tool` instead of
``env.execute``.

Requires the optional dependency: ``pip install -e '.[mcp]'``. Importing this module
is safe without the ``mcp`` package; only connecting to servers requires it.
"""

import asyncio
import json
import logging
import os
from typing import Any

from pydantic import BaseModel, Field

from minisweagent.models.litellm_model import LitellmModel, LitellmModelConfig

logger = logging.getLogger("mcp_model")

try:  # pragma: no cover - exercised when the optional extra is installed
    import anyio
    from mcp import ClientSession
    from mcp.client.sse import sse_client
    from mcp.client.stdio import stdio_client
except ImportError:  # pragma: no cover
    anyio = None
    ClientSession = None
    stdio_client = None
    sse_client = None

MCP_NOT_INSTALLED_MSG = (
    "The 'mcp' package is required to use MCPModel. Install it with: pip install -e '.[mcp]'"
)


class MCPServerConfig(BaseModel):
    """Configuration for a single MCP server."""

    command: str | None = None
    """Command of a stdio subprocess server (e.g. `npx`). Mutually exclusive with url."""
    args: list[str] = Field(default_factory=list)
    """Arguments for the stdio server command."""
    env: dict[str, str] = Field(default_factory=dict)
    """Extra environment variables for the stdio server process."""
    url: str | None = None
    """Remote server URL (http/sse). Mutually exclusive with command."""
    headers: dict[str, str] = Field(default_factory=dict)
    """Headers for remote servers (e.g. an Authorization token)."""


class MCPModelConfig(LitellmModelConfig):
    mcp_servers: list[MCPServerConfig] = Field(default_factory=list)
    """MCP servers to connect to and expose to the model."""
    mcp_timeout: float = 30.0
    """Timeout (seconds) for MCP tool calls."""
    mcp_include_servers: list[str] | None = None
    """If set, only expose tools from these servers."""
    mcp_exclude_servers: list[str] | None = None
    """Expose tools from all servers except these (unless include is set)."""


class _ServerConnection:
    def __init__(self, name: str, session: Any, tools: list[dict]):
        self.name = name
        self.session = session
        self.tools = tools


class MCPModel(LitellmModel):
    """``LitellmModel`` that also exposes tools from configured MCP servers."""

    def __init__(self, *, config_class: type = MCPModelConfig, **kwargs):
        super().__init__(config_class=config_class, **kwargs)
        self._connections: dict[str, _ServerConnection] = {}
        self._tool_map: dict[str, tuple[str, dict]] = {}  # tool_name -> (server_name, definition)
        self._extra_tools: list[dict] = []
        self._init_mcp()

    # -- setup ----------------------------------------------------------

    def _init_mcp(self) -> None:
        if not self.config.mcp_servers:
            return
        if ClientSession is None:
            raise RuntimeError(MCP_NOT_INSTALLED_MSG)
        try:
            anyio.run(self._connect_all)
        except Exception as e:
            logger.error("Failed to connect MCP servers: %s", e)
            raise RuntimeError(f"Failed to connect MCP servers: {e}") from e

    def _server_selected(self, name: str) -> bool:
        if self.config.mcp_include_servers is not None:
            return name in self.config.mcp_include_servers
        if self.config.mcp_exclude_servers:
            return name not in self.config.mcp_exclude_servers
        return True

    async def _connect_all(self) -> None:
        for i, server_config in enumerate(self.config.mcp_servers):
            name = server_config.command or server_config.url or f"mcp_server_{i}"
            if not self._server_selected(name):
                continue
            tools = await self._connect_one(name, server_config)
            self._connections[name].tools = tools
            self._extra_tools.extend(tools)
            for tool in tools:
                self._tool_map[tool["name"]] = (name, tool)

    async def _connect_one(self, name: str, server_config: MCPServerConfig) -> list[dict]:
        if server_config.command:
            ctx = await stdio_client(_stdio_params(server_config)).__aenter__()
        else:
            ctx = await sse_client(server_config.url, headers=server_config.headers).__aenter__()

        session = await ClientSession(ctx).__aenter__()
        self._connections[name] = _ServerConnection(name, session, [])
        await asyncio.wait_for(session.initialize(), timeout=self.config.mcp_timeout)
        result = await session.list_tools()
        return [_tool_to_openai(t) for t in result.tools]

    # -- query / parse --------------------------------------------------

    def _query(self, messages: list[dict[str, str]], **kwargs):
        from minisweagent.models.utils.actions_toolcall import BASH_TOOL

        return super()._query(messages, tools=[BASH_TOOL, *self._extra_tools], **kwargs)

    def _parse_actions(self, response) -> list[dict]:
        from minisweagent.exceptions import FormatError

        tool_calls = response.choices[0].message.tool_calls or []
        actions = []
        for tool_call in tool_calls:
            try:
                args = json.loads(tool_call.function.arguments)
            except Exception as e:
                raise FormatError(
                    {
                        "role": "user",
                        "content": f"Error parsing tool call arguments: {e}.",
                        "extra": {"interrupt_type": "FormatError"},
                    }
                ) from e
            name = tool_call.function.name
            if name == "bash":
                if "command" not in args:
                    raise self._format_error("Missing 'command' argument in bash tool call.")
                actions.append({"command": args["command"], "tool_call_id": tool_call.id})
            elif name in self._tool_map:
                server_name, _ = self._tool_map[name]
                actions.append(
                    {
                        "mcp_server": server_name,
                        "tool": name,
                        "arguments": args,
                        "tool_call_id": tool_call.id,
                    }
                )
            else:
                raise self._format_error(f"Unknown tool '{name}'.")
        if not actions:
            raise self._format_error(
                "No tool calls found in the response. Every response MUST include at least one tool call."
            )
        return actions

    def _format_error(self, message: str):
        from minisweagent.exceptions import FormatError

        return FormatError(
            {
                "role": "user",
                "content": message,
                "extra": {"interrupt_type": "FormatError"},
            }
        )

    # -- MCP tool execution ---------------------------------------------

    def call_mcp_tool(self, action: dict) -> dict[str, Any]:
        """Execute an ``mcp``-tagged action and return a result dict like ``env.execute``."""
        server_name = action.get("mcp_server")
        tool_name = action.get("tool")
        connection = self._connections.get(server_name)
        if connection is None or connection.session is None:
            return {
                "output": f"Unknown or disconnected MCP server '{server_name}'.",
                "returncode": -1,
                "exception_info": "MCP server not available",
            }
        try:
            result = anyio.run(
                lambda: connection.session.call_tool(tool_name, action.get("arguments", {}))
            )
            text = _mcp_result_to_text(result)
            return {
                "output": text,
                "returncode": 0,
                "exception_info": "",
                "extra": {"mcp": True, "server": server_name, "tool": tool_name},
            }
        except Exception as e:
            return {
                "output": "",
                "returncode": -1,
                "exception_info": f"MCP tool '{tool_name}' failed: {e}",
                "extra": {"mcp": True, "server": server_name, "tool": tool_name, "exception": str(e)},
            }

    # -- protocol -------------------------------------------------------

    def serialize(self) -> dict:
        data = super().serialize()
        data["info"]["config"]["mcp_tools"] = [
            {"server": name, "tool": tool["name"]} for name, tool in self._tool_map.values()
        ]
        return data


def _stdio_params(server_config: MCPServerConfig) -> dict:
    params: dict = {"command": server_config.command, "args": server_config.args}
    if server_config.env:
        params["env"] = {**os.environ, **server_config.env}
    return params


def _tool_to_openai(tool: Any) -> dict:
    schema = dict(tool.inputSchema)
    schema.setdefault("type", "object")
    return {
        "name": tool.name,
        "description": tool.description or "",
        "inputSchema": schema,
    }


def _mcp_result_to_text(result: Any) -> str:
    """Render an MCP CallToolResult into a string (handles text and structured content)."""
    if hasattr(result, "content"):
        parts = []
        for item in result.content:
            if item.type == "text":
                parts.append(item.text)
            else:
                parts.append(json.dumps(item.model_dump(), default=str))
        return "\n".join(parts)
    if isinstance(result, dict):
        return json.dumps(result, default=str)
    return str(result)
