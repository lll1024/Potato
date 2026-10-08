import { useEffect, useRef, useState } from 'react';
import './trace.css';
import { Payload, Highlight, type TraceLocation } from './PayloadViewer';
import { TraceSearch } from './TraceSearch';
import { TraceTimeline } from './TraceTimeline';
import { useReadingFollow } from './useReadingFollow';
import { ReadingScroll } from './ReadingScroll';
export type { TraceLocation } from './PayloadViewer';

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
  interrupted?: boolean; status: string; proposed_at: string; waiting_at?: string; started_at?: string; call_started_at?: string; call_finished_at?: string; finished_at?: string;
  call_started: boolean; wait_duration_ms: number | null; call_duration_ms: number | null; total_duration_ms: number | null;
  sdk_is_error?: boolean; failure_category?: string | null; reason?: string | null;
  arguments_payload_id: string; service_payload_id?: string; result_payload_id?: string; error_payload_id?: string;
};
type TraceTurn = {turn_id: string; ordinal: number; input: string; input_is_summary?: boolean; input_payload_id?: string; answer_payload_id?: string; answer_source?: string | null; status: string; reason?: string | null; tool_error_count: number; usage_summary?: UsageSummary};
type TraceEvent = {event_id: string; sequence: number; kind: string; request_id?: string | null; tool_call_id?: string | null; turn_id: string};
type TraceSnapshot = {events: TraceEvent[]; session: {title: string}; turns: TraceTurn[]; requests: TraceRequest[]; tool_calls: TraceTool[]; usage_summary: UsageSummary; next_before: number | null; cursor: number; stream_id: string};
type Props = {sessionId: string | null; refreshKey?: number; location?: TraceLocation | null; onInteraction?: () => void};
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

export function TraceView({sessionId,refreshKey,location:externalLocation,onInteraction:onRead}: Props) {
  const sessionRef=useRef(sessionId);
  sessionRef.current=sessionId;
  const [snapshot,setSnapshot] = useState<TraceSnapshot | null>(null);
  const [selected,setSelected] = useState<string | null>(null);
  const [expanded,setExpanded] = useState<Record<string,boolean>>({});
  const [error,setError] = useState('');
  const [location,setLocation] = useState<TraceLocation | null>(null);
  const [loadingEarlier,setLoadingEarlier] = useState(false);
  const follow = useReadingFollow(sessionId, snapshot?.events.at(-1)?.event_id ?? '');
  const latestRef = useRef<string | undefined>(undefined);
  function onInteraction() {follow.pause(); onRead?.();}
  const [narrow,setNarrow] = useState(() => window.matchMedia('(max-width: 1000px)').matches);
  useEffect(() => {
    const media=window.matchMedia('(max-width: 1000px)');
    const change=() => setNarrow(media.matches);
    media.addEventListener('change',change); return () => media.removeEventListener('change',change);
  },[]);
  useEffect(() => {
    setSnapshot(null); setSelected(null); setExpanded({}); setLocation(null); setError(''); latestRef.current=undefined;
  }, [sessionId]);
  useEffect(() => {
  if (!sessionId) return;
    let cancelled = false;
    async function refresh() {
      try {
        const result = await read<TraceSnapshot>(`/api/sessions/${sessionId}?summary=true`);
        if (cancelled) return;
        setSnapshot(current => current ? {...result,
          next_before:current.next_before,
          turns:[...current.turns.filter(item => !result.turns.some(incoming => incoming.turn_id===item.turn_id)),...result.turns].sort((a,b) => a.ordinal-b.ordinal),
          requests:[...current.requests.filter(item => !result.turns.some(turn => turn.turn_id===item.turn_id)),...result.requests],
          tool_calls:[...current.tool_calls.filter(item => !result.turns.some(turn => turn.turn_id===item.turn_id)),...result.tool_calls],
          events:[...current.events.filter(item => !result.turns.some(turn => turn.turn_id===item.turn_id)),...result.events].sort((a,b) => a.sequence-b.sequence)} : result);
        setExpanded(current => {
          const next = {...current};
          const latest = result.turns.at(-1)?.turn_id;
          if (!follow.paused && latestRef.current && latestRef.current !== latest) {
            next[latestRef.current]=false;
            for (const request of result.requests) if (request.turn_id===latestRef.current) next[request.request_id]=false;
          }
          latestRef.current=latest;
          for (const turn of result.turns) if (!(turn.turn_id in next)) next[turn.turn_id] = turn.turn_id === latest;
          for (const request of result.requests) if (!(request.request_id in next)) next[request.request_id] = request.turn_id === latest;
          return next;
        });

      } catch (cause) {
        if (!cancelled) setError(cause instanceof Error ? cause.message : '无法读取执行轨迹。');
      }
    }
    void refresh();
    return () => {cancelled = true;};
  }, [sessionId,refreshKey]);
  async function loadEarlier() {
    if (!sessionId || !snapshot?.next_before) return;
    onInteraction(); setLoadingEarlier(true);
    const id=sessionId;
    try {
      const page=await read<TraceSnapshot>(`/api/sessions/${id}?summary=true&before=${snapshot.next_before}`);
      if (sessionRef.current!==id) return;
      setSnapshot(current => current ? {...current,
        next_before:page.next_before,
        turns:[...page.turns.filter(item => !current.turns.some(old => old.turn_id===item.turn_id)),...current.turns].sort((a,b) => a.ordinal-b.ordinal),
        requests:[...page.requests.filter(item => !current.requests.some(old => old.request_id===item.request_id)),...current.requests],
        tool_calls:[...page.tool_calls.filter(item => !current.tool_calls.some(old => old.tool_call_id===item.tool_call_id)),...current.tool_calls],
        events:[...page.events.filter(item => !current.events.some(old => old.event_id===item.event_id)),...current.events].sort((a,b) => a.sequence-b.sequence)} : page);
    } catch (cause) {setError(cause instanceof Error ? cause.message : '无法读取更早轨迹。');}
    finally {setLoadingEarlier(false);}
  }
  async function locate(target: TraceLocation) {
    if (!sessionId) return;
    onInteraction?.(); setLocation(target); setSelected(target.object_id);
    try {
      const page = await read<TraceSnapshot>(`/api/sessions/${sessionId}?summary=true&limit=1&before=${target.turn_ordinal+1}`);
      if (sessionRef.current !== sessionId) return;
      setSnapshot(current => current ? {...current,
        turns:[...current.turns.filter(item => item.turn_id!==target.turn_id),...page.turns].sort((a,b) => a.ordinal-b.ordinal),
        requests:[...current.requests.filter(item => item.turn_id!==target.turn_id),...page.requests],
        tool_calls:[...current.tool_calls.filter(item => item.turn_id!==target.turn_id),...page.tool_calls],
        events:[...current.events.filter(item => item.turn_id!==target.turn_id),...page.events].sort((a,b) => a.sequence-b.sequence)} : page);
      const requestId=page.tool_calls.find(item => item.tool_call_id===target.object_id)?.request_id ?? target.object_id;
      setExpanded(current => ({...current,session:true,[target.turn_id]:true,[requestId]:true}));
      requestAnimationFrame(() => {
        const object=document.getElementById(`trace-object-${target.object_id}`);
        object?.scrollIntoView({block:'nearest'});
        object?.querySelector<HTMLElement>('summary,button')?.focus({preventScroll:true});
      });
    } catch (cause) {setError(cause instanceof Error ? cause.message : '搜索定位失败。');}
  }
  useEffect(() => {if (externalLocation) void locate(externalLocation);},[externalLocation]);
  function select(id: string) {onInteraction?.(); setSelected(id); setLocation(null);}
  const detailProps={location,onInteraction};
  const selectedTurn=snapshot?.turns.find(turn => turn.turn_id===selected);
  const turnDetail=location?.object_type==='turn' ? <div key={location.turn_id} onFocus={onInteraction} onWheel={onInteraction}>
    <h3>第 {location.turn_ordinal} 轮 · 对话内容</h3>
    {selectedTurn && <p>{turnStatus(selectedTurn)} · {selectedTurn.answer_source === 'model' ? '模型回答' : selectedTurn.answer_source === 'application' ? '应用说明' : '尚无回答'}</p>}
    {!location.payload_id && <p><Highlight text={location.excerpt ?? ''} query={location.query} /></p>}
    <Payload id={selectedTurn?.input_payload_id ?? (location.field==='turn_input' ? location.payload_id : null)} title="完整用户输入" {...detailProps} />
    <Payload id={selectedTurn?.answer_payload_id ?? (location.field==='turn_answer' ? location.payload_id : null)} title="完整对话回答" {...detailProps} />
  </div> : null;
  const request = snapshot?.requests.find(item => item.request_id === selected);
  const tool = snapshot?.tool_calls.find(item => item.tool_call_id === selected);
  function expandAll(open: boolean) {
    onInteraction();
    const next: Record<string,boolean> = {session: open};
    for (const turn of snapshot?.turns ?? []) next[turn.turn_id] = open;
    for (const item of snapshot?.requests ?? []) next[item.request_id] = open;
    setExpanded(next);
  }
  function backToLatest() {
    setSelected(null); setLocation(null);
    const latest=snapshot?.turns.at(-1)?.turn_id;
    const next: Record<string,boolean>={session:true};
    for (const turn of snapshot?.turns ?? []) next[turn.turn_id]=turn.turn_id===latest;
    for (const request of snapshot?.requests ?? []) next[request.request_id]=request.turn_id===latest;
    setExpanded(next);
    follow.resume();
  }
  function timelineLocate(objectType: 'request' | 'tool', objectId: string, turnId: string) {
    const turn=snapshot?.turns.find(item => item.turn_id===turnId);
    if (turn) void locate({turn_id:turnId,turn_ordinal:turn.ordinal,object_type:objectType,object_id:objectId,payload_id:null,field:'',query:''});
  }
  if (!sessionId) return <div className="trace-empty"><p>发送旅行需求后，这里显示每次模型请求的真实开始与结束。</p></div>;
  return <div className="trace-view">
    <div className="trace-toolbar"><h3>执行轨迹</h3><div><button onClick={() => expandAll(true)}>全部展开</button><button onClick={() => expandAll(false)}>全部收起</button></div></div>
    <p className="hint">请求“已收到响应”仅表示模型返回，本轮仍可能继续查询或终止。</p>
    <p role="alert" className="error">{error}</p>
    <TraceSearch sessionId={sessionId} onLocate={target => void locate(target)} onInteraction={onInteraction} />
    <UsageTotals summary={snapshot?.usage_summary} />
    <div className="reading-follow"><p role="status">{follow.hasNew ? '有新事件' : follow.paused ? '已暂停跟随，正在阅读' : '正在跟随最新事件'}</p>{follow.paused && <button type="button" onClick={backToLatest}>回到最新</button>}</div>
    <ReadingScroll className="trace-scroll" identity={sessionId} paused={follow.paused} scrollRef={follow.ref} onScroll={follow.onScroll} onClickCapture={event => {if ((event.target as HTMLElement).closest('summary')) onInteraction();}}>
    {snapshot?.next_before && <button type="button" disabled={loadingEarlier} onClick={() => void loadEarlier()}>加载更早轨迹</button>}
    {snapshot && <TraceTimeline turns={snapshot.turns} requests={snapshot.requests} tools={snapshot.tool_calls} events={snapshot.events} selected={selected} onLocate={timelineLocate} />}
    <div className="trace-layout">
      <div className="trace-tree">
        <details open={expanded.session ?? true} onToggle={event => {const open=event.currentTarget.open; setExpanded(current => ({...current,session:open}));}}>
          <summary>助手会话：{snapshot?.session.title ?? '正在读取'}</summary>
          {snapshot?.turns.map(turn => <details className={`trace-turn ${selected === turn.turn_id ? 'selected' : ''}`} id={`trace-object-${turn.turn_id}`} key={turn.turn_id} open={expanded[turn.turn_id] ?? false} onToggle={event => {const open=event.currentTarget.open; setExpanded(current => ({...current,[turn.turn_id]:open}));}}>
            <summary data-reading-anchor={`turn:${turn.turn_id}`}>第 {turn.ordinal} 轮 · {turnStatus(turn)} · {turn.input_is_summary ? '输入摘要：' : ''}{turn.input}</summary>
            {turn.reason === 'service_interrupted' && <p className="hint">本轮已排除后续上下文；保留最后保存事实，没有自动重试或重放。</p>}
            <UsageTotals summary={turn.usage_summary} />
            {narrow && selected===turn.turn_id && <div className="trace-mobile-detail" data-reading-anchor={`detail:${turn.turn_id}`}>{turnDetail}</div>}
            {snapshot.requests.filter(item => item.turn_id === turn.turn_id).map(item => <details key={item.request_id} id={`trace-object-${item.request_id}`} className={`trace-request ${selected === item.request_id ? 'selected' : ''}`} open={expanded[item.request_id] ?? false} onToggle={event => {const open=event.currentTarget.open; setExpanded(current => ({...current,[item.request_id]:open}));}}>
              <summary data-reading-anchor={`request:${item.request_id}`}>模型请求 {item.ordinal} · {item.interrupted ? '中断，结果未知' : statuses[item.status] ?? item.status}</summary>
              <div className="trace-request-row"><time dateTime={item.started_at}>{new Date(item.started_at).toLocaleTimeString('zh-CN')}</time><span>{item.duration_ms === null ? (item.interrupted ? '—（中断，结束未知）' : '—（尚未结束）') : `${item.duration_ms.toFixed(1)} ms`}</span><button aria-pressed={selected === item.request_id} onClick={() => select(item.request_id)}>查看详情</button></div>
              {snapshot.tool_calls.filter(call => call.request_id === item.request_id).map(call => <div key={call.tool_call_id} id={`trace-object-${call.tool_call_id}`} className={`trace-tool ${selected === call.tool_call_id ? 'selected' : ''}`}>
                <div className="trace-request-row" data-reading-anchor={`tool:${call.tool_call_id}`}><span>工具 {call.ordinal} · {call.name} · {call.interrupted ? '中断，结果未知' : toolStatuses[call.status] ?? call.status}</span><button aria-pressed={selected === call.tool_call_id} onClick={() => select(call.tool_call_id)}>查看工具详情</button></div>
                {narrow && selected === call.tool_call_id && <div className="trace-mobile-detail" data-reading-anchor={`detail:${call.tool_call_id}`}><ToolDetail tool={call} {...detailProps} /></div>}
              </div>)}
              {narrow && selected === item.request_id && <div className="trace-mobile-detail" data-reading-anchor={`detail:${item.request_id}`}><RequestDetail request={item} {...detailProps} /></div>}
            </details>)}
          </details>)}
        </details>
      </div>
      <section className="trace-detail" data-reading-anchor={`detail:${selected ?? 'none'}`} aria-label="选中执行详情">{!narrow && (turnDetail ?? (tool ? <ToolDetail key={tool.tool_call_id} tool={tool} {...detailProps} /> : request ? <RequestDetail key={request.request_id} request={request} {...detailProps} /> : <p className="hint">选择模型请求或工具调用，查看真实时间与完整来源。</p>))}</section>
    </div>
    </ReadingScroll>
  </div>;
}

function RequestDetail({request,location,onInteraction}: {request: TraceRequest; location?: TraceLocation | null; onInteraction?: () => void}) {
  function usageValue(field: string): unknown {
    return field.split('.').reduce<unknown>((value,key) => value !== null && typeof value === 'object' ? (value as Usage)[key] : undefined, request.usage);
  }
  const missing = request.interrupted ? '未采集／结果未知' : request.usage_state === 'not_completed' ? '未采集／尚未完成' : '未返回';
  return <div onFocus={onInteraction} onWheel={onInteraction}>
    {location && !location.payload_id && <p><Highlight text={location.excerpt ?? ''} query={location.query} /></p>}
    <h3>模型请求 {request.ordinal} · {request.interrupted ? '中断，结果未知' : statuses[request.status] ?? request.status}</h3>
    {request.interrupted && <p className="hint">保留上次保存的开始事实；结束、耗时与用量未知，没有重试或重放。</p>}
    <dl className="request-facts"><dt>应用请求身份</dt><dd>{request.request_id}</dd><dt>开始时间</dt><dd>{new Date(request.started_at).toLocaleString('zh-CN')}</dd><dt>结束时间</dt><dd>{request.finished_at ? new Date(request.finished_at).toLocaleString('zh-CN') : request.interrupted ? '—（中断，结束未知）' : '—（尚未结束）'}</dd><dt>SDK 调用耗时</dt><dd>{request.duration_ms === null ? (request.interrupted ? '—（中断，耗时未知）' : '—（尚未完成）') : `${request.duration_ms.toFixed(1)} ms`}</dd></dl>
    <h4>实际 token 用量</h4>
    <dl className="request-facts">{Object.entries(usageLabels).map(([field,label]) => <div key={field}><dt>{label}</dt><dd>{typeof usageValue(field) === 'number' ? String(usageValue(field)) : missing}</dd></div>)}</dl>
    {request.usage && <details><summary>全部实际 usage 字段</summary><pre className="wrap">{JSON.stringify(request.usage,null,2)}</pre></details>}
    <Payload location={location} onInteraction={onInteraction} key={request.input_payload_id} id={request.input_payload_id} title="完整逻辑输入" />
    <Payload location={location} onInteraction={onInteraction} key={request.response_payload_id} id={request.response_payload_id} title="完整已解析返回（含实际存在的元数据）" />
    <Payload location={location} onInteraction={onInteraction} key={request.error_payload_id} id={request.error_payload_id} title="完整错误详情" />
  </div>;
}

function ToolDetail({tool,location,onInteraction}: {tool: TraceTool; location?: TraceLocation | null; onInteraction?: () => void}) {
  const duration = (value: number | null, missing: string) => typeof value === 'number' ? `${value.toFixed(1)} ms` : missing;
  const time = (value?: string) => value ? new Date(value).toLocaleString('zh-CN') : tool.interrupted ? '—（中断，未采集）' : '—（尚未采集）';
  return <div onFocus={onInteraction} onWheel={onInteraction}>
    {location && !location.payload_id && <p><Highlight text={location.excerpt ?? ''} query={location.query} /></p>}
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
    <Payload location={location} onInteraction={onInteraction} key={tool.arguments_payload_id} id={tool.arguments_payload_id} title="模型提出的完整参数" />
    <Payload location={location} onInteraction={onInteraction} key={tool.service_payload_id} id={tool.service_payload_id ?? null} title="完整已解析 SDK 服务结果" />
    <Payload location={location} onInteraction={onInteraction} key={tool.result_payload_id} id={tool.result_payload_id ?? null} title="实际应用回填（tool_result）" />
    <Payload location={location} onInteraction={onInteraction} key={tool.error_payload_id} id={tool.error_payload_id ?? null} title="完整工具错误详情" />
  </div>;
}
