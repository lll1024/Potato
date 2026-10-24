"""仅通过公开入口运行真实规划、查询、上下文及 SQLite 保存。"""
import asyncio, json, tempfile
from pathlib import Path
from types import SimpleNamespace
from datetime import datetime, timezone
from anthropic.types import Message, Usage, TextBlock, ToolUseBlock
from mcp.types import Tool, ListToolsResult, CallToolResult, TextContent
from travel_app.planning import plan
from travel_app.context import ContextPolicy
from travel_app.retrieval import Retrieval
from travel_app.local_records import LocalRecords

OUT = Path("/tmp/potato86-independent-use")
class ScriptedMCP:
    def __init__(self): self.calls=[]; self.session=SimpleNamespace(call_tool=self.perform_call)
    async def list_tools(self, *, cursor=None):
        return ListToolsResult(tools=[Tool(name="maps_text_search", description="本机脚本地点查询", inputSchema={"type":"object","properties":{"keywords":{"type":"string"},"city":{"type":"string"}},"required":["keywords"]})])
    async def perform_call(self, name, arguments):
        self.calls.append({"name":name,"arguments":arguments})
        return CallToolResult(content=[TextContent(type="text",text=json.dumps({"pois":[{"name":"西湖","location":"120.15,30.24"}]},ensure_ascii=False))])
class ScriptedModel:
    def __init__(self):
        self.messages=self
        self.calls=[]
    async def create(self, **kwargs):
        self.calls.append(kwargs)
        if len(self.calls)==1:
            content=[ToolUseBlock(type="tool_use",id="local-query-1",name="maps_text_search",input={"keywords":"西湖","city":"杭州"})]
            reason="tool_use"
        else:
            content=[TextBlock(type="text",text="建议在西湖步行游览；开放和预约信息仍待核实。")]
            reason="end_turn"
        return Message(id=f"local-model-{len(self.calls)}", type="message",role="assistant", model="scripted",content=content,stop_reason=reason,stop_sequence=None,usage=Usage(input_tokens=100,output_tokens=40))
async def main():
    mcp=ScriptedMCP(); model=ScriptedModel()
    tools=Retrieval(mcp,map_interval=0,source_interval=0)
    await tools.discover()
    facts=[]
    with tempfile.TemporaryDirectory(prefix="potato86-records-",dir="/tmp") as directory:
        records=LocalRecords(Path(directory))
        database=Path(directory)/"travel.sqlite3"
        assert database.is_file()
        try:
            records.recover()
            # 外层提交身份、全局名额属于会话；此处只演示记录的公开原子保存。
            accepted=records.accept("杭州西湖一天步行游览",preference_now=1000)
            (OUT/"accepted.json").write_text(json.dumps(accepted,ensure_ascii=False,default=str,indent=2))
            # 接受结果字段属于原协议；此次只通过公开返回值取身份。
            session_id=accepted["session_id"]; turn_id=accepted["turn_id"]
            async def observer(kind,data):
                facts.append({"kind":kind,"data":data})
                if kind.startswith("request."): records.observe_request(session_id,turn_id,kind,data)
                elif kind.startswith("tool."): records.observe_tool(session_id,turn_id,kind,data)
                elif kind.startswith("context."): records.observe_context(session_id,turn_id,kind,data)
            result=await plan("杭州西湖一天步行游览",model,tools,"scripted",observer=observer,context_policy=ContextPolicy(window=100000),clock=lambda:datetime(2026,10,24,tzinfo=timezone.utc))
            assert result.status=="completed" and result.reason is None,result.fields()
            (OUT/"public-observations.json").write_text(json.dumps(facts,ensure_ascii=False,default=str,indent=2))
            print("PUBLIC CAPABILITIES",tools.capabilities)
            assert len(mcp.calls)==1,mcp.calls
            records.finish(session_id,turn_id,result.answer,result.material,result.fields(),0,preference_now=1010)
            snapshot=records.snapshot(session_id)
            (OUT/"snapshot.json").write_text(json.dumps(snapshot,ensure_ascii=False,default=str,indent=2))
            continued=await plan("继续，安排一处休息",model,tools,"scripted",continuation=result.continuation,context_policy=ContextPolicy(window=100000),clock=lambda:datetime(2026,10,25,tzinfo=timezone.utc))
            assert continued.status=="completed" and continued.reason is None,continued.fields()
            report={"first":result.fields(),"continuation":continued.fields(),"model_calls":len(model.calls),"mcp_calls":mcp.calls,"saved":True,"sqlite_real":database.is_file(),"sqlite_size":database.stat().st_size,"observations":len(facts)}
            (OUT/"public-result.json").write_text(json.dumps(report,ensure_ascii=False,indent=2))
            (OUT/"external-requests.json").write_text(json.dumps(model.calls,ensure_ascii=False,default=str,indent=2))
            print(json.dumps(report,ensure_ascii=False,indent=2))
        finally: records.close()
asyncio.run(main())
