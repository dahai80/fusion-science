from __future__ import annotations

import pytest

from fusion_science.core.tools import ToolDefinition, ToolRegistry, register_builtin_tools


class TestToolDefinition:
    def test_definition_init(self):
        async def handler(**kwargs):
            return "ok"

        td = ToolDefinition(
            name="test_tool",
            description="A test tool",
            parameters={"type": "object", "properties": {}},
            handler=handler,
        )
        assert td.name == "test_tool"
        assert td.mcp_exposed is True

    def test_definition_mcp_disabled(self):
        td = ToolDefinition(
            name="internal",
            description="Internal",
            parameters={},
            handler=None,
            mcp_exposed=False,
        )
        assert td.mcp_exposed is False


class TestToolRegistry:
    def test_empty_registry(self):
        reg = ToolRegistry()
        assert reg.list_tools() == []
        assert reg.get_openai_tools() == []
        assert not reg.has_tool("anything")

    def test_register_tool(self):
        reg = ToolRegistry()

        async def add(**kwargs):
            return kwargs.get("a", 0) + kwargs.get("b", 0)

        reg.register(
            name="add",
            description="Add two numbers",
            parameters={
                "type": "object",
                "properties": {
                    "a": {"type": "integer"},
                    "b": {"type": "integer"},
                },
            },
            handler=add,
        )
        assert reg.has_tool("add")
        assert len(reg.list_tools()) == 1

    @pytest.mark.asyncio
    async def test_execute_tool(self):
        reg = ToolRegistry()

        async def greet(**kwargs):
            return f"Hello, {kwargs.get('name', 'world')}!"

        reg.register(
            name="greet",
            description="Greet someone",
            parameters={"type": "object", "properties": {"name": {"type": "string"}}},
            handler=greet,
        )
        result = await reg.execute("greet", {"name": "Science"})
        assert "Science" in str(result)

    @pytest.mark.asyncio
    async def test_execute_missing_tool(self):
        reg = ToolRegistry()
        result = await reg.execute("nonexistent", {})
        assert "error" in result

    def test_unregister(self):
        reg = ToolRegistry()
        reg.register(name="tmp", description="temp", parameters={}, handler=None)
        assert reg.has_tool("tmp")
        reg.unregister("tmp")
        assert not reg.has_tool("tmp")

    def test_get_openai_tools(self):
        reg = ToolRegistry()
        reg.register(
            name="test",
            description="Test tool",
            parameters={"type": "object", "properties": {"q": {"type": "string"}}},
            handler=None,
        )
        tools = reg.get_openai_tools()
        assert len(tools) == 1
        assert tools[0]["type"] == "function"
        assert tools[0]["function"]["name"] == "test"

    def test_get_mcp_tools(self):
        reg = ToolRegistry()
        reg.register(name="mcp_tool", description="MCP exposed", parameters={}, handler=None, mcp_exposed=True)
        reg.register(name="internal_tool", description="Internal only", parameters={}, handler=None, mcp_exposed=False)
        mcp = reg.get_mcp_tools()
        assert len(mcp) == 1
        assert mcp[0]["name"] == "mcp_tool"

    def test_get_tool(self):
        reg = ToolRegistry()
        reg.register(name="fetch", description="Fetch data", parameters={}, handler=None)
        tool = reg.get_tool("fetch")
        assert tool.name == "fetch"
        assert reg.get_tool("missing") is None


class TestBuiltinTools:
    def test_register_builtin_tools(self):
        reg = ToolRegistry()
        register_builtin_tools(reg)
        tools = reg.list_tools()
        # 5 original + 7 Phase 7 tools = 12
        assert len(tools) == 12
        assert reg.has_tool("search_literature")
        assert reg.has_tool("search_database")
        assert reg.has_tool("execute_python")
        assert reg.has_tool("generate_chart")
        assert reg.has_tool("fetch_paper")
        assert reg.has_tool("extract_findings")
        assert reg.has_tool("analyze_consensus")
        assert reg.has_tool("execute_r")
        assert reg.has_tool("visualize_molecule")
        assert reg.has_tool("visualize_protein")
        assert reg.has_tool("write_section")
        assert reg.has_tool("manage_citations")

    def test_builtin_openai_format(self):
        reg = ToolRegistry()
        register_builtin_tools(reg)
        openai_tools = reg.get_openai_tools()
        assert len(openai_tools) == 12
        for t in openai_tools:
            assert t["type"] == "function"
            assert "name" in t["function"]
            assert "parameters" in t["function"]
