"""The MCP server module imports and registers its tools (a broken docstring once kept it from starting)."""
import asyncio


def test_server_imports_and_lists_tools():
    from claudio import server
    names = {t.name for t in asyncio.run(server.mcp.list_tools())}
    assert {'index_folder', 'caption_overlay', 'caption_mogrt', 'export_edit'} <= names


def test_what_goes_out_with_every_message_stays_lean():
    """Tool descriptions + schemas + instructions are re-sent on every message: no workflow, no client, no personal
    paths in them (those live in the recipes and the profile), and the shared rules said once, in the instructions."""
    import json, re
    from claudio import server
    tools = asyncio.run(server.mcp.list_tools())
    desc = ' '.join(t.description or '' for t in tools)
    schemas = [json.dumps(t.inputSchema) for t in tools]
    assert len(desc) < 25000 and sum(map(len, schemas)) < 17000, (len(desc), sum(map(len, schemas)))
    assert not re.search(r'\bINC\b|Enki|bernardo|claudio-ae|D:\\\\Documentos', desc)
    assert desc.count('crashes Premiere') == 0 and 'crashes Premiere' in server.mcp.instructions
    assert not any('"title": "' in s.replace('"title": {', '') for s in schemas)   # pydantic titles dropped
    fetch = next(t for t in tools if t.name == 'fetch_music').inputSchema
    assert 'title' in fetch['properties']                                            # the argument named title stays


def test_arguments_are_still_validated():
    import pytest
    from claudio import server
    with pytest.raises(Exception, match='Field required'):                 # pydantic still checks without titles
        asyncio.run(server.mcp.call_tool('fetch_music', {'url': 'x'}))
