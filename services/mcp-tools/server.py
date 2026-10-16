import os
from typing import Any

import structlog
from app.tools.orders import demo_order
from mcp.server.fastmcp import FastMCP

logger = structlog.get_logger(__name__)

mcp = FastMCP(
    "support-business-tools",
    host="0.0.0.0",
    port=int(os.getenv("MCP_PORT", "8001")),
)


@mcp.tool()
def lookup_order(order_id: str, as_of_date: str | None = None) -> dict[str, Any]:
    """Return order status from the demo order system."""

    result = demo_order(order_id, as_of_date)
    logger.info(
        "lookup_order",
        component="mcp_tool",
        status=result["status"],
        delivered_at=result["delivered_at"],
    )
    return result


@mcp.tool()
def get_shipping_region(postal_code: str) -> dict[str, str]:
    """Return a coarse demo shipping region for a postal code."""

    logger.info("get_shipping_region", postal_code=postal_code)

    return {"postal_code": postal_code, "region": "standard"}


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
