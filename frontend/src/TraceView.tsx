import { useEffect, useState } from 'react';
import './trace.css';

type Usage = Record<string, unknown>;
type UsageSummary = Record<string, {value: number | null; known_count: number; request_count: number}>;
type TraceRequest = {
  request_id: string; turn_id: string; ordinal: number; status: string;
  started_at: string; finished_at: string | null; duration_ms: number | null;
  input_payload_id: string; response_payload_id: string | null; error_payload_id: string | null;
  usage: Usage | null; usage_state: string;
};
type TraceTurn = {turn_id: string; ordinal: number; input: string; status: string; usage_summary?: UsageSummary};
type TraceSnapshot = {session: {title: string}; turns: TraceTurn[]; requests: TraceRequest[]; usage_summary: UsageSummary; cursor: number; stream_id: string};
type Props = {sessionId: string | null; refreshKey?: number};
const statuses: Record<string,string> = {running:'正在请求',completed:'已收到响应',failed:'请求失败'};
const usageLabels: Record<string,string> = {input_tokens:'输入',output_tokens:'输出',cache_creation_input_tokens:'缓存写入',cache_read_input_tokens:'缓存读取',
  'cache_creation.ephemeral_5m_input_tokens':'缓存写入（5 分钟）', 'cache_creation.ephemeral_1h_input_tokens':'缓存写入（1 小时）'};

async function read<T>(path: string): Promise<T> {
  const response = await fetch(path);
  const result = await response.json();
  if (!response.ok) throw new Error(result.detail ?? '无法读取执行轨迹。');
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
            <summary>第 {turn.ordinal} 轮 · {turn.input}</summary>
            <UsageTotals summary={turn.usage_summary} />
            {snapshot.requests.filter(item => item.turn_id === turn.turn_id).map(item => <details key={item.request_id} className={`trace-request ${selected === item.request_id ? 'selected' : ''}`} open={expanded[item.request_id] ?? false} onToggle={event => {const open=event.currentTarget.open; setExpanded(current => ({...current,[item.request_id]:open}));}}>
              <summary>模型请求 {item.ordinal} · {statuses[item.status] ?? item.status}</summary>
              <div className="trace-request-row"><time dateTime={item.started_at}>{new Date(item.started_at).toLocaleTimeString('zh-CN')}</time><span>{item.duration_ms === null ? '—（尚未结束）' : `${item.duration_ms.toFixed(1)} ms`}</span><button aria-pressed={selected === item.request_id} onClick={() => setSelected(item.request_id)}>查看详情</button></div>
              {selected === item.request_id && <div className="trace-mobile-detail"><RequestDetail request={item} /></div>}
            </details>)}
          </details>)}
        </details>
      </div>
      <section className="trace-detail" aria-label="选中模型请求详情">{request ? <RequestDetail key={request.request_id} request={request} /> : <p className="hint">选择一个模型请求，查看时间、真实用量与完整载荷。</p>}</section>
    </div>
  </div>;
}

function RequestDetail({request}: {request: TraceRequest}) {
  function usageValue(field: string): unknown {
    return field.split('.').reduce<unknown>((value,key) => value !== null && typeof value === 'object' ? (value as Usage)[key] : undefined, request.usage);
  }
  const missing = request.usage_state === 'not_completed' ? '未采集／尚未完成' : '未返回';
  return <div>
    <h3>模型请求 {request.ordinal} · {statuses[request.status] ?? request.status}</h3>
    <dl className="request-facts"><dt>应用请求身份</dt><dd>{request.request_id}</dd><dt>开始时间</dt><dd>{new Date(request.started_at).toLocaleString('zh-CN')}</dd><dt>结束时间</dt><dd>{request.finished_at ? new Date(request.finished_at).toLocaleString('zh-CN') : '—（尚未结束）'}</dd><dt>SDK 调用耗时</dt><dd>{request.duration_ms === null ? '—（尚未完成）' : `${request.duration_ms.toFixed(1)} ms`}</dd></dl>
    <h4>实际 token 用量</h4>
    <dl className="request-facts">{Object.entries(usageLabels).map(([field,label]) => <div key={field}><dt>{label}</dt><dd>{typeof usageValue(field) === 'number' ? String(usageValue(field)) : missing}</dd></div>)}</dl>
    {request.usage && <details><summary>全部实际 usage 字段</summary><pre className="wrap">{JSON.stringify(request.usage,null,2)}</pre></details>}
    <Payload key={request.input_payload_id} id={request.input_payload_id} title="完整逻辑输入" />
    <Payload key={request.response_payload_id} id={request.response_payload_id} title="完整已解析返回（含实际存在的元数据）" />
    <Payload key={request.error_payload_id} id={request.error_payload_id} title="完整错误详情" />
  </div>;
}
