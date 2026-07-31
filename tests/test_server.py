"""Behavior contract for the Soleaux composition root."""

from fastmcp import Client

from soleaux import surface
from soleaux.server import mcp


async def test_composition_root_serves_the_fixed_catalog() -> None:
    async with Client(mcp) as client:
        tools = await client.list_tools()
        assert {tool.name for tool in tools} == set(surface.tool_names())


async def test_search_tool_has_bounded_fastmcp_deadline() -> None:
    tool = await mcp.get_tool("search")

    assert tool is not None
    assert tool.timeout == 60.0
