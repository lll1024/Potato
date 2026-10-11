"""按实际 Exa 文本契约验证公开资料工具边界。"""
import json
import unittest

from mcp import Client
from mcp.server import MCPServer
from mcp.types import CallToolResult, TextContent

from exa_mcp import ExaTools


class ExaSourceTests(unittest.IsolatedAsyncioTestCase):
    async def test_search_highlights_are_clues_with_real_source_and_query_time(self):
        server = MCPServer("Exa 契约")

        @server.tool(name="web_search_exa")
        def search(query: str, objective: str) -> str:
            return "Title: 参观须知\nURL: https://museum.example.test/visit\nPublished: N/A\nAuthor: N/A\nHighlights:\n周一闭馆，预约条件须读正文。"

        @server.tool(name="web_fetch_exa")
        def fetch(urls: list[str], maxCharacters: int = 3000) -> str:
            return "未调用"

        @server.tool(name="agent_run")
        def paid(query: str) -> str:
            raise AssertionError("不得调用付费研究")

        async with Client(server) as client:
            tools = ExaTools(client, redact=lambda value: value, call_interval=0)
            await tools.discover()
            result = await tools.call("web_search_exa", {"query": "博物馆参观须知", "objective": "优先官方页面"})
        self.assertEqual({tool["name"] for tool in tools.declarations}, {"web_search_exa", "web_fetch_exa"})
        self.assertFalse(result["is_error"])
        envelope = json.loads(result["content"])
        self.assertEqual(envelope["provider"], "exa")
        self.assertEqual(envelope["document_status"], "search_clues")
        self.assertEqual(envelope["sources"][0]["url"], "https://museum.example.test/visit")
        self.assertEqual(envelope["sources"][0]["content_type"], "highlights")
        self.assertNotIn("published_date", envelope["sources"][0])
        self.assertTrue(envelope["retrieved_at"].endswith("+00:00"))

    async def test_fetch_summary_is_not_presented_as_body(self):
        server = MCPServer("内容类型边界")

        @server.tool(name="web_search_exa")
        def search(query: str, objective: str) -> str:
            return "No results found"

        @server.tool(name="web_fetch_exa")
        def fetch(urls: list[str]) -> str:
            return "# 参观须知\nURL: https://museum.example.test/visit\n\nSummary:\n模型生成的参观规则摘要。"

        async with Client(server) as client:
            tools = ExaTools(client, redact=lambda value: value)
            await tools.discover()
            result = await tools.call("web_fetch_exa", {"urls": ["https://museum.example.test/visit"]})
        self.assertEqual(json.loads(result["content"])["sources"][0]["content_type"], "generated_summary")

    async def test_fetch_marks_requested_page_without_return_as_incomplete(self):
        server = MCPServer("部分页面边界")

        @server.tool(name="web_search_exa")
        def search(query: str, objective: str) -> str:
            return "No results found"

        @server.tool(name="web_fetch_exa")
        def fetch(urls: list[str], maxCharacters: int = 3000) -> str:
            return "# 参观须知\nURL: https://museum.example.test/visit\n\n常规开放时间9:00至17:00。"

        async with Client(server) as client:
            tools = ExaTools(client, redact=lambda value: value)
            await tools.discover()
            result = await tools.call("web_fetch_exa", {"urls": ["https://museum.example.test/visit", "https://museum.example.test/missing"], "maxCharacters": 100})
        envelope = json.loads(result["content"])
        self.assertEqual(envelope["failed_sources"][0]["url"], "https://museum.example.test/missing")
        self.assertEqual(envelope["max_characters"], 100)
        self.assertIn("截断", envelope["content_boundary"])

    async def test_text_rate_limit_error_pauses_sources(self):
        server = MCPServer("Exa 文本限流")

        @server.tool(name="web_search_exa")
        def search(query: str, objective: str) -> CallToolResult:
            return CallToolResult(content=[TextContent(text="Rate limit exceeded (429). Add an API key to upgrade.")], is_error=True)

        @server.tool(name="web_fetch_exa")
        def fetch(urls: list[str]) -> str:
            raise AssertionError("限流后不能继续调用资料服务")

        async with Client(server) as client:
            tools = ExaTools(client, redact=lambda value: value)
            await tools.discover()
            first = await tools.call("web_search_exa", {"query": "官方须知", "objective": "官方资料"})
            second = await tools.call("web_fetch_exa", {"urls": ["https://museum.example.test/visit"]})
        self.assertTrue(first["is_error"])
        self.assertEqual(tools.failure, "rate_limit")
        self.assertTrue(second.get("not_executed"))
        self.assertNotIn("upgrade", first["content"])

    async def test_first_failed_page_does_not_discard_later_successful_body(self):
        server = MCPServer("混合页面边界")

        @server.tool(name="web_search_exa")
        def search(query: str, objective: str) -> str:
            return "No results found"

        @server.tool(name="web_fetch_exa")
        def fetch(urls: list[str]) -> str:
            return "Error fetching https://museum.example.test/missing: CRAWL_UNKNOWN_ERROR\n\n# 参观须知\nURL: https://museum.example.test/visit\n\n专项体验仍须预约。"

        async with Client(server) as client:
            tools = ExaTools(client, redact=lambda value: value)
            await tools.discover()
            result = await tools.call("web_fetch_exa", {"urls": ["https://museum.example.test/missing", "https://museum.example.test/visit"]})
        self.assertFalse(result["is_error"])
        envelope = json.loads(result["content"])
        self.assertEqual(envelope["sources"][0]["content"], "专项体验仍须预约。")
        self.assertEqual(envelope["failed_sources"][0]["url"], "https://museum.example.test/missing")
