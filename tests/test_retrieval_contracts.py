"""资料获取公开分段入口，供应商使用真实 MCP adapter。"""
import asyncio
import unittest
from typing import cast

from mcp import Client
from mcp.server import MCPServer
from travel_app.retrieval import Query, Retrieval


class RetrievalContractTests(unittest.IsolatedAsyncioTestCase):
    async def test_segment_preserves_identity_order_and_does_not_expose_providers(self):
        server = MCPServer("公开资料获取测试")
        seen = []
        @server.tool(name="maps_text_search")
        async def search(keywords: str, city: str) -> dict:
            seen.append(keywords)
            return {"name": keywords, "address": city}
        async with Client(server) as client:
            retrieval = Retrieval(client)
            await retrieval.discover()
            outputs = await retrieval.execute([
                Query("first", "maps_text_search", {"keywords": "西湖", "city": "杭州"}),
                Query("invalid", "bash", {}),
            ])
            self.assertEqual([item.query_id for item in outputs], ["first", "invalid"])
            self.assertEqual([(item.is_error, item.not_executed) for item in outputs], [(False, False), (True, False)])
            self.assertIn("西湖", outputs[0].content)
            self.assertEqual(seen, ["西湖"])
            self.assertEqual([item["name"] for item in retrieval.capabilities.declarations], ["maps_text_search"])
            self.assertFalse(hasattr(retrieval, "amap"))
            self.assertFalse(hasattr(retrieval, "sources"))

    async def test_start_fact_failure_never_enters_service(self):
        server = MCPServer("开始事实保存失败")
        seen = []
        @server.tool(name="maps_text_search")
        async def search(keywords: str, city: str) -> dict:
            seen.append(keywords)
            return {"name": keywords}
        class SaveFailure(RuntimeError):
            pass
        async def observe(kind, data):
            if kind == "tool.running":
                raise SaveFailure("开始事实未保存")
        async with Client(server) as client:
            retrieval = Retrieval(client)
            await retrieval.discover()
            with self.assertRaises(SaveFailure):
                await retrieval.execute([Query("one", "maps_text_search", {"keywords": "西湖", "city": "杭州"})], observer=observe)
        self.assertEqual(seen, [])

    async def test_result_fact_failure_waits_for_other_real_results_and_save_attempts(self):
        server = MCPServer("在途保存边界")
        gates = {name: asyncio.Event() for name in ("one", "two")}
        entered, saved = [], []
        @server.tool(name="maps_text_search")
        async def search(keywords: str, city: str) -> dict:
            return {"name": keywords}
        @server.tool(name="web_search_exa")
        async def source(query: str, objective: str) -> str:
            entered.append(query)
            await gates[query].wait()
            return f"Title: {query}\nURL: https://guide.example.test/{query}\nHighlights:\n资料 {query}"
        @server.tool(name="web_fetch_exa")
        async def fetch(urls: list[str]) -> str:
            return ""
        class SaveFailure(RuntimeError):
            pass
        async def observe(kind, data):
            if kind == "tool.completed":
                saved.append(data["query_id"])
                if data["query_id"] == "one":
                    raise SaveFailure("结果事实未保存")
        async with Client(server) as client:
            retrieval = Retrieval(client, source_client=client, source_interval=0)
            await retrieval.discover()
            task = asyncio.create_task(retrieval.execute([
                Query(name, "web_search_exa", {"query": name, "objective": "资料"}) for name in gates
            ], observer=observe))
            try:
                async with asyncio.timeout(3):
                    while len(entered) < 2:
                        await asyncio.sleep(.01)
                gates["one"].set()
                async with asyncio.timeout(3):
                    while not saved:
                        await asyncio.sleep(.01)
                self.assertFalse(task.done())
                gates["two"].set()
                with self.assertRaises(SaveFailure):
                    await asyncio.wait_for(task, 3)
                self.assertEqual(saved, ["one", "two"])
            finally:
                for gate in gates.values():
                    gate.set()
                await asyncio.gather(task, return_exceptions=True)

    async def test_stopped_segment_keeps_all_identities_without_service_calls(self):
        server = MCPServer("停止分段")
        seen = []
        @server.tool(name="maps_text_search")
        async def search(keywords: str, city: str) -> dict:
            seen.append(keywords)
            return {"name": keywords}
        async with Client(server) as client:
            retrieval = Retrieval(client)
            await retrieval.discover()
            stop = asyncio.Event()
            stop.set()
            results = await retrieval.execute([Query(str(i), "maps_text_search", {"keywords": "西湖", "city": "杭州"})
                                               for i in range(2)], stop_requested=stop)
            self.assertEqual([item.query_id for item in results], ["0", "1"])
            self.assertTrue(all(item.is_error and item.not_executed for item in results))
            self.assertEqual(seen, [])

    async def test_observation_cannot_rewrite_returned_query_facts(self):
        server = MCPServer("观察隔离")
        @server.tool(name="maps_text_search")
        async def search(keywords: str, city: str) -> dict:
            return {"name": keywords}
        async def observe(kind, data):
            if kind == "tool.completed":
                data["result"].update(content="篡改", is_error=True)
        async with Client(server) as client:
            retrieval = Retrieval(client)
            await retrieval.discover()
            output = (await retrieval.execute([Query("one", "maps_text_search", {"keywords": "西湖", "city": "杭州"})], observer=observe))[0]
            self.assertFalse(output.is_error)
            self.assertIn("西湖", output.content)

    async def test_source_discovery_failure_keeps_map_capability_available(self):
        server = MCPServer("独立地图能力")
        @server.tool(name="maps_text_search")
        async def search(keywords: str, city: str) -> dict:
            return {"name": keywords}
        class BrokenSources:
            async def list_tools(self, **_kwargs):
                raise ConnectionError("网页发现失败")
        async with Client(server) as client:
            retrieval = Retrieval(client, source_client=cast(Client, BrokenSources()))
            await retrieval.discover()
            self.assertIn("暂不可用", retrieval.capabilities.source_status or "")
            self.assertEqual([item["name"] for item in retrieval.capabilities.declarations], ["maps_text_search"])
            output = (await retrieval.execute([Query("map", "maps_text_search", {"keywords": "西湖", "city": "杭州"})]))[0]
            self.assertFalse(output.is_error)
