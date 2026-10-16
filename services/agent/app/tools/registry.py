from dataclasses import dataclass
from typing import Any

import structlog
from langchain_core.tools import BaseTool, tool
from langchain_mcp_adapters.client import MultiServerMCPClient

from ..core.settings import Settings
from .orders import demo_order, normalize_order

logger = structlog.get_logger()


@tool
def lookup_order(order_id: str, as_of_date: str | None = None) -> dict[str, Any]:
    """Look up the current status of a customer order by its identifier."""

    # This deterministic adapter is intentionally easy to replace with a real order API.
    return demo_order(order_id, as_of_date)


@dataclass
class ToolResult:
    name: str
    output: Any


class ToolRegistry:
    """Executes read tools and idempotent business actions behind one boundary."""

    def __init__(self, tools: list[BaseTool]) -> None:
        self._tools = {candidate.name: candidate for candidate in tools}
        self._completed_actions: dict[str, dict[str, Any]] = {}

    @classmethod
    async def create(cls, settings: Settings) -> "ToolRegistry":
        tools: list[BaseTool] = [lookup_order]
        if settings.mcp_server_url:
            try:
                client = MultiServerMCPClient(
                    {
                        "business": {
                            "transport": "streamable_http",
                            "url": settings.mcp_server_url,
                        }
                    }
                )
                mcp_tools = await client.get_tools()
                # An MCP tool overrides the local demo tool when names match.
                tools = [
                    candidate
                    for candidate in tools
                    if candidate.name not in {t.name for t in mcp_tools}
                ]
                tools.extend(mcp_tools)
                logger.info("mcp_tools_loaded", tools=[candidate.name for candidate in mcp_tools])
            except Exception as exc:  # startup remains available if an optional MCP server is down
                logger.warning("mcp_unavailable_using_local_tools", error=str(exc))
        return cls(tools)

    async def execute_read_tool(self, name: str, arguments: dict[str, Any]) -> ToolResult:
        if name not in self._tools:
            raise ValueError(f"Tool is not allow-listed: {name}")
        output = await self._tools[name].ainvoke(arguments)
        if name == "lookup_order":
            output = normalize_order(output, arguments["order_id"])
        return ToolResult(name=name, output=output)

    async def execute_action(
        self, ticket_id: str, action: str, arguments: dict[str, Any]
    ) -> ToolResult:
        """Execute an approved side effect exactly once per ticket and action.

        Replace this demo implementation with an API/database transaction that accepts
        the same idempotency key. Never place the real side effect before a graph interrupt.
        """

        idempotency_key = f"{ticket_id}:{action}"
        if idempotency_key not in self._completed_actions:
            self._completed_actions[idempotency_key] = {
                "idempotency_key": idempotency_key,
                "action": action,
                "arguments": arguments,
                "result": "accepted",
            }
        return ToolResult(name=action, output=self._completed_actions[idempotency_key])
