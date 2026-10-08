import { useEffect, useState } from 'react';
import './trace.css';

type Usage = Record<string, unknown>;
type UsageSummary = Record<string, {value: number | null; known_count: number; request_count: number}>;
type TraceRequest = {
  request_id: string; turn_id: string; ordinal: number; status: string; interrupted?: boolean;
  started_at: string; finished_at: string | null; duration_ms: number | null;
  input_payload_id: string; response_payload_id: string | null; error_payload_id: string | null;
  usage: Usage | null; usage_state: string;
};
type TraceTool = {
  tool_call_id: string; request_id: string; turn_id: string; ordinal: number; tool_use_id: string; name: string;
  interrupted?: boolean; status: string; proposed_at: string; started_at?: string; call_started_at?: string; call_finished_at?: string; finished_at?: string;
  call_started: boolean; wait_duration_ms: number | null; call_duration_ms: number | null; total_duration_ms: number | null;
  sdk_is_error?: boolean; failure_category?: string | null; reason?: string | null;
  arguments_payload_id: string; service_payload_id?: string; result_payload_id?: string; error_payload_id?: string;
};
type TraceTurn = {turn_id: string; ordinal: number; input: string; status: string; reason?: string | null; tool_error_count: number; usage_summary?: UsageSummary};
type TraceSnapshot = {session: {title: string}; turns: TraceTurn[]; requests: TraceRequest[]; tool_calls: TraceTool[]; usage_summary: UsageSummary; cursor: number; stream_id: string};
type Props = {sessionId: string | null; refreshKey?: number};
const toolStatuses: Record<string,string> = {pending:'已提出',waiting:'限速等待',running:'执行中',completed:'成功',failed:'失败',not_executed:'未执行'};
const reasonLabels: Record<string,string> = {budget:'查询达到上限',model_error:'模型请求失败',output_limit:'输出达到上限',map_paused:'地图查询已暂停',user_stop:'用户停止',service_shutdown:'服务退出',service_interrupted:'服务中断',storage_failure:'存储故障'};
const failureLabels: Record<string,string> = {auth:'鉴权失败',quota:'额度或限流',connection:'连接不可恢复',service:'地图服务暂停',arguments:'参数错误',timeout:'查询超时',business:'地图业务失败',sdk_error:'SDK 错误标志',error:'普通工具失败'};
function turnStatus(turn: TraceTurn) { return turn.status === 'completed' ? (turn.tool_error_count ? `完成，含工具错误（${turn.tool_error_count}）` : '已完成') : turn.status === 'running' ? '正在执行' : `${turn.status === 'failed' ? '失败' : '终止'}：${reasonLabels[turn.reason ?? ''] ?? turn.reason ?? '原因未知'}`; }
const statuses: Record<string,string> = {running:'正在请求',completed:'已收到响应',failed:'请求失败'};
const usageLabels: Record<string,string> = {input_tokens:'输入',output_tokens:'输出',cache_creation_input_tokens:'缓存写入',cache_read_input_tokens:'缓存读取',
  'cache_creation.ephemeral_5m_input_tokens':'缓存写入（5 分钟）', 'cache_creation.ephemeral_1h_input_tokens':'缓存写入（1 小时）'};

async function read<T>(path: string): Promise<T> {
  const response = await fetch(path);
  const result = await response.json();
  if (!response.ok) throw new Error(typeof result.detail === 'string' ? result.detail : result.detail?.message ?? '无法读取执行轨迹。');
  if (result.schema_version !== 1) throw new Error('数据版本不兼容，请更新页面。');
  return result;
}

function UsageTotals({summary}: {summary?: UsageSummary}) {
  if (!summary) return null;
  return <div className="usage-totals">{Object.entries(summary).map(([field,value]) => <span key={field}>
    {usageLabels[field] ?? field}：{value.value ?? '未知'} <small>（已知 {value.known_count}/{value.request_count} 请求）</small>
  </span>)}</div>;
}

function Payload({id,title}: {id: string | null; title: string}) {
  const [content,setContent] = useState<unknown>(undefined);
  const [error,setError] = useState('');
  const [wrap,setWrap] = useState(true);
  useEffect(() => { setContent(undefined); setError(''); }, [id]);
  async function load() {
    if (!id || content !== undefined) return;
    try { setContent((await read<{content: unknown}>(`/api/payloads/${id}`)).content); }
    catch (cause) { setError(cause instanceof Error ? cause.message : '详情加载失败。'); }
  }
  if (!id) return null;
  return <details className="trace-payload" onToggle={event => {if (event.currentTarget.open) void load();}}>
    <summary>{title}</summary>
    {error && <p role="alert">{error}</p>}
    {content === undefined ? <p className="hint">正在读取完整载荷…</p> : <>
      <label className="wrap-toggle"><input type="checkbox" checked={wrap} onChange={event => setWrap(event.target.checked)} />自动换行</label>
      <pre className={wrap ? 'wrap' : ''}>{JSON.stringify(content,null,2)}</pre>
    </>}
  </details>;
}

export function TraceView({sessionId,refreshKey}: Props) {
  const [snapshot,setSnapshot] = useState<TraceSnapshot | null>(null);
  const [selected,setSelected] = useState<string | null>(null);
  const [expanded,setExpanded] = useState<Record<string,boolean>>({});
  const [error,setError] = useState('');
  useEffect(() => {
    setSnapshot(null); setSelected(null); setExpanded({}); setError('');
  }, [sessionId]);
  useEffect(() => {
    if (!sessionId) return;
    let cancelled = false;
    let source: EventSource | null = null;
    async function refresh() {
      try {
        const result = await read<TraceSnapshot>(`/api/sessions/${sessionId}`);
        if (cancelled) return;
        setSnapshot(result);
        setExpanded(current => {
          const next = {...current};
          const latest = result.turns.at(-1)?.turn_id;
          for (const turn of result.turns) if (!(turn.turn_id in next)) next[turn.turn_id] = turn.turn_id === latest;
          for (const request of result.requests) if (!(request.request_id in next)) next[request.request_id] = request.turn_id === latest;
          return next;
        });
        if (!source && refreshKey === undefined) {
          source = new EventSource(`/api/events?after=${result.cursor}&stream_id=${result.stream_id}`);
          source.addEventListener('trace', event => {
            const data = JSON.parse((event as MessageEvent).data);
            if (data.session_id === sessionId) void refresh();
          });
        }
      } catch (cause) {
        if (!cancelled) setError(cause instanceof Error ? cause.message : '无法读取执行轨迹。');
      }
    }
    void refresh();
    return () => {cancelled = true; source?.close();};
  }, [sessionId,refreshKey]);
  const request = snapshot?.requests.find(item => item.request_id === selected);
  const tool = snapshot?.tool_calls.find(item => item.tool_call_id === selected);
  function expandAll(open: boolean) {
    const next: Record<string,boolean> = {session: open};
    for (const turn of snapshot?.turns ?? []) next[turn.turn_id] = open;
    for (const item of snapshot?.requests ?? []) next[item.request_id] = open;
    setExpanded(next);
  }
  if (!sessionId) return <div className="trace-empty"><p>发送旅行需求后，这里显示每次模型请求的真实开始与结束。</p></div>;
  return <div className="trace-view">
    <div className="trace-toolbar"><h3>执行轨迹</h3><div><button onClick={() => expandAll(true)}>全部展开</button><button onClick={() => expandAll(false)}>全部收起</button></div></div>
    <p className="hint">请求“已收到响应”仅表示模型返回，本轮仍可能继续查询或终止。</p>
    <p role="alert" className="error">{error}</p>
    <UsageTotals summary={snapshot?.usage_summary} />
    <div className="trace-layout">
      <div className="trace-tree">
        <details open={expanded.session ?? true} onToggle={event => {const open=event.currentTarget.open; setExpanded(current => ({...current,session:open}));}}>
          <summary>助手会话：{snapshot?.session.title ?? '正在读取'}</summary>
          {snapshot?.turns.map(turn => <details className="trace-turn" key={turn.turn_id} open={expanded[turn.turn_id] ?? false} onToggle={event => {const open=event.currentTarget.open; setExpanded(current => ({...current,[turn.turn_id]:open}));}}>
            <summary>第 {turn.ordinal} 轮 · {turnStatus(turn)} · {turn.input}</summary>
            {turn.reason === 'service_interrupted' && <p className="hint">本轮已排除后续上下文；保留最后保存事实，没有自动重试或重放。</p>}
            <UsageTotals summary={turn.usage_summary} />
            {snapshot.requests.filter(item => item.turn_id === turn.turn_id).map(item => <details key={item.request_id} className={`trace-request ${selected === item.request_id ? 'selected' : ''}`} open={expanded[item.request_id] ?? false} onToggle={event => {const open=event.currentTarget.open; setExpanded(current => ({...current,[item.request_id]:open}));}}>
              <summary>模型请求 {item.ordinal} · {item.interrupted ? '中断，结果未知' : statuses[item.status] ?? item.status}</summary>
              <div className="trace-request-row"><time dateTime={item.started_at}>{new Date(item.started_at).toLocaleTimeString('zh-CN')}</time><span>{item.duration_ms === null ? (item.interrupted ? '—（中断，结束未知）' : '—（尚未结束）') : `${item.duration_ms.toFixed(1)} ms`}</span><button aria-pressed={selected === item.request_id} onClick={() => setSelected(item.request_id)}>查看详情</button></div>
              {snapshot.tool_calls.filter(call => call.request_id === item.request_id).map(call => <div key={call.tool_call_id} className={`trace-tool ${selected === call.tool_call_id ? 'selected' : ''}`}>
                <div className="trace-request-row"><span>工具 {call.ordinal} · {call.name} · {call.interrupted ? '中断，结果未知' : toolStatuses[call.status] ?? call.status}</span><button aria-pressed={selected === call.tool_call_id} onClick={() => setSelected(call.tool_call_id)}>查看工具详情</button></div>
                {selected === call.tool_call_id && <div className="trace-mobile-detail"><ToolDetail tool={call} /></div>}
              </div>)}
              {selected === item.request_id && <div className="trace-mobile-detail"><RequestDetail request={item} /></div>}
            </details>)}
          </details>)}
        </details>
      </div>
      <section className="trace-detail" aria-label="选中执行详情">{tool ? <ToolDetail key={tool.tool_call_id} tool={tool} /> : request ? <RequestDetail key={request.request_id} request={request} /> : <p className="hint">选择模型请求或工具调用，查看真实时间与完整来源。</p>}</section>
    </div>
  </div>;
}

function RequestDetail({request}: {request: TraceRequest}) {
  function usageValue(field: string): unknown {
    return field.split('.').reduce<unknown>((value,key) => value !== null && typeof value === 'object' ? (value as Usage)[key] : undefined, request.usage);
  }
  const missing = request.interrupted ? '未采集／结果未知' : request.usage_state === 'not_completed' ? '未采集／尚未完成' : '未返回';
  return <div>
    <h3>模型请求 {request.ordinal} · {request.interrupted ? '中断，结果未知' : statuses[request.status] ?? request.status}</h3>
    {request.interrupted && <p className="hint">保留上次保存的开始事实；结束、耗时与用量未知，没有重试或重放。</p>}
    <dl className="request-facts"><dt>应用请求身份</dt><dd>{request.request_id}</dd><dt>开始时间</dt><dd>{new Date(request.started_at).toLocaleString('zh-CN')}</dd><dt>结束时间</dt><dd>{request.finished_at ? new Date(request.finished_at).toLocaleString('zh-CN') : request.interrupted ? '—（中断，结束未知）' : '—（尚未结束）'}</dd><dt>SDK 调用耗时</dt><dd>{request.duration_ms === null ? (request.interrupted ? '—（中断，耗时未知）' : '—（尚未完成）') : `${request.duration_ms.toFixed(1)} ms`}</dd></dl>
    <h4>实际 token 用量</h4>
    <dl className="request-facts">{Object.entries(usageLabels).map(([field,label]) => <div key={field}><dt>{label}</dt><dd>{typeof usageValue(field) === 'number' ? String(usageValue(field)) : missing}</dd></div>)}</dl>
    {request.usage && <details><summary>全部实际 usage 字段</summary><pre className="wrap">{JSON.stringify(request.usage,null,2)}</pre></details>}
    <Payload key={request.input_payload_id} id={request.input_payload_id} title="完整逻辑输入" />
    <Payload key={request.response_payload_id} id={request.response_payload_id} title="完整已解析返回（含实际存在的元数据）" />
    <Payload key={request.error_payload_id} id={request.error_payload_id} title="完整错误详情" />
  </div>;
}

function ToolDetail({tool}: {tool: TraceTool}) {
  const duration = (value: number | null, missing: string) => typeof value === 'number' ? `${value.toFixed(1)} ms` : missing;
  const time = (value?: string) => value ? new Date(value).toLocaleString('zh-CN') : tool.interrupted ? '—（中断，未采集）' : '—（尚未采集）';
  return <div>
    <h3>工具 {tool.ordinal} · {tool.name} · {tool.interrupted ? '中断，结果未知' : toolStatuses[tool.status] ?? tool.status}</h3>
    {tool.interrupted && <p className="hint">中断，结果未知。以下是上次保存的事实，缺失的结束与耗时不会用恢复时间补齐。</p>}
    <dl className="request-facts">
      <dt>应用调用身份</dt><dd>{tool.tool_call_id}</dd><dt>所属请求</dt><dd>{tool.request_id}</dd><dt>原始 tool_use_id</dt><dd>{tool.tool_use_id}</dd>
      <dt>模型提出时间</dt><dd>{time(tool.proposed_at)}</dd><dt>应用处理开始</dt><dd>{time(tool.started_at)}</dd>
      <dt>进入底层 SDK</dt><dd>{tool.call_started ? '是' : '否（未调用）'}</dd><dt>SDK 开始时间</dt><dd>{tool.call_started ? time(tool.call_started_at) : '—（未调用）'}</dd>
      <dt>SDK 结束时间</dt><dd>{tool.call_started ? time(tool.call_finished_at) : '—（未调用）'}</dd>
      <dt>限速等待耗时</dt><dd>{duration(tool.wait_duration_ms,'—（未采集／尚未完成）')}</dd>
      <dt>实际调用耗时</dt><dd>{duration(tool.call_duration_ms,tool.call_started ? '—（尚未完成）' : '—（未调用）')}</dd>
      <dt>应用总耗时</dt><dd>{duration(tool.total_duration_ms,'—（未采集／尚未完成）')}</dd>
      <dt>SDK is_error</dt><dd>{typeof tool.sdk_is_error === 'boolean' ? String(tool.sdk_is_error) : '未取得服务返回'}</dd>
      <dt>应用失败分类</dt><dd>{tool.failure_category ? failureLabels[tool.failure_category] ?? tool.failure_category : tool.status === 'completed' ? '无' : '未记录'}</dd>
      {tool.reason && <><dt>未执行原因</dt><dd>{reasonLabels[tool.reason] ?? tool.reason}</dd></>}
    </dl>
    <p className="hint">总耗时从应用处理到产生回填，可包含本地检查；SDK 耗时表示底层调用到返回或异常。</p>
    {['auth','quota','connection','service'].includes(tool.failure_category ?? '') && <p>地图暂停属于整个本地服务；换会话不会解除。修复后重启，仍需一次实际成功查询核对恢复。</p>}
    <Payload key={tool.arguments_payload_id} id={tool.arguments_payload_id} title="模型提出的完整参数" />
    <Payload key={tool.service_payload_id} id={tool.service_payload_id ?? null} title="完整已解析 SDK 服务结果" />
    <Payload key={tool.result_payload_id} id={tool.result_payload_id ?? null} title="实际应用回填（tool_result）" />
    <Payload key={tool.error_payload_id} id={tool.error_payload_id ?? null} title="完整工具错误详情" />
  </div>;
}
