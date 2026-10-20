"""The MCP server module imports and registers its tools (a broken docstring once kept it from starting)."""
import asyncio


def test_server_imports_and_lists_tools():
    from claudio import server
    names = {t.name for t in asyncio.run(server.mcp.list_tools())}
    assert {'index_folder', 'caption_overlay', 'caption_mogrt', 'export_edit'} <= names
