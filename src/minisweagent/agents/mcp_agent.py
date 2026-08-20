"""Agent variant that executes MCP tool calls via the model instead of the environment.

Pairs with :class:`~minisweagent.models.mcp_model.MCPModel`: when an action is tagged
with ``mcp_server`` (a tool provided by an MCP server), it is routed to the model's
:meth:`~minisweagent.models.mcp_model.MCPModel.call_mcp_tool`; all other actions go to
``env.execute`` as usual.
"""

from minisweagent.agents.default import DefaultAgent


class MCPAgent(DefaultAgent):
    def execute_actions(self, message: dict) -> list[dict]:
        outputs = []
        for action in message.get("extra", {}).get("actions", []):
            if "mcp_server" in action:
                outputs.append(self.model.call_mcp_tool(action))
            else:
                outputs.append(self.env.execute(action))
        return self.add_messages(*self.model.format_observation_messages(message, outputs, self.get_template_vars()))
