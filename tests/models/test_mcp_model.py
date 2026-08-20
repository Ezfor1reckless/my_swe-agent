"""Tests for the MCP model.

The ``mcp`` package is an optional dependency. Tests that only need the pure
helpers run everywhere; tests that need a live session use fake session objects
(the real client is an external package we do not mock).
"""

import importlib.util
from types import SimpleNamespace

import pytest

from minisweagent.models.mcp_model import (
    MCPModel,
    MCPModelConfig,
    MCPServerConfig,
    _mcp_result_to_text,
    _tool_to_openai,
)


def test_tool_to_openai_conversion():
    """MCP tool schemas are converted to OpenAI-style tool definitions."""
    tool = SimpleNamespace(name="read_file", description="Read a file", inputSchema={"type": "object"})
    converted = _tool_to_openai(tool)
    assert converted["name"] == "read_file"
    assert converted["description"] == "Read a file"
    assert converted["inputSchema"]["type"] == "object"


def test_mcp_result_to_text():
    """MCP call results render to plain text (text and structured content)."""
    text_item = SimpleNamespace(type="text", text="hello")
    other_item = SimpleNamespace(type="resource", model_dump=lambda: {"uri": "x"})
    result = SimpleNamespace(content=[text_item, other_item])
    text = _mcp_result_to_text(result)
    assert "hello" in text
    assert "resource" in text or '"uri": "x"' in text

    assert _mcp_result_to_text("plain") == "plain"
    assert _mcp_result_to_text({"a": 1}) == '{"a": 1}'


def test_mcp_server_config_parses_both_kinds():
    """Both stdio and remote server configs parse."""
    cfg = MCPModelConfig(
        model_name="x",
        mcp_servers=[
            MCPServerConfig(command="npx", args=["-y", "server"], env={"FOO": "bar"}),
            MCPServerConfig(url="http://localhost:8000/mcp", headers={"Authorization": "Bearer t"}),
        ],
    )
    assert cfg.mcp_servers[0].command == "npx"
    assert cfg.mcp_servers[0].env["FOO"] == "bar"
    assert cfg.mcp_servers[1].url == "http://localhost:8000/mcp"


def test_server_selection():
    """include/exclude filters control which servers are selected."""
    cfg = MCPModelConfig(
        model_name="x",
        mcp_include_servers=["a"],
        mcp_servers=[MCPServerConfig(command="a"), MCPServerConfig(command="b")],
    )
    model = MCPModel.__new__(MCPModel)  # skip __init__ (no connection)
    model.config = cfg
    assert model._server_selected("a") is True
    assert model._server_selected("b") is False

    cfg2 = MCPModelConfig(
        model_name="x",
        mcp_exclude_servers=["b"],
        mcp_servers=[MCPServerConfig(command="a"), MCPServerConfig(command="b")],
    )
    model.config = cfg2
    assert model._server_selected("a") is True
    assert model._server_selected("b") is False


def test_importing_module_is_safe_without_mcp():
    """Importing the module does not require the mcp package (guards are lazy)."""
    import minisweagent.models.mcp_model as mcp_model

    assert hasattr(mcp_model, "MCPModel")


@pytest.mark.skipif(importlib.util.find_spec("mcp") is None, reason="mcp package not installed")
def test_mcp_parse_actions_routing():
    """Tool call parsing routes bash and MCP tools (fake session, no mcp needed for parse)."""
    from minisweagent.exceptions import FormatError

    model = MCPModel.__new__(MCPModel)
    model.config = MCPModelConfig(model_name="x")
    model._tool_map = {"read_file": ("fs", {"name": "read_file"})}

    response = SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(
                    tool_calls=[
                        SimpleNamespace(id="1", function=SimpleNamespace(name="bash", arguments='{"command": "echo hi"}')),
                        SimpleNamespace(
                            id="2",
                            function=SimpleNamespace(name="read_file", arguments='{"path": "/x"}'),
                        ),
                    ]
                )
            )
        ]
    )
    actions = model._parse_actions(response)
    assert actions == [
        {"command": "echo hi", "tool_call_id": "1"},
        {"mcp_server": "fs", "tool": "read_file", "arguments": {"path": "/x"}, "tool_call_id": "2"},
    ]

    with pytest.raises(FormatError):
        model._parse_actions(
            SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(
                            tool_calls=[SimpleNamespace(id="3", function=SimpleNamespace(name="nope", arguments="{}"))]
                        )
                    )
                ]
            )
        )
