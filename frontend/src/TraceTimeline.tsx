type Request = {request_id: string; turn_id: string; ordinal: number; status: string; started_at: string; finished_at: string | null; interrupted?: boolean};
type Tool = {tool_call_id: string; turn_id: string; request_id: string; ordinal: number; name: string; proposed_at: string; waiting_at?: string; call_started_at?: string; call_finished_at?: string; finished_at?: string; status: string; interrupted?: boolean};
type Event = {request_id?: string | null; tool_call_id?: string | null; sequence: number; kind: string};
type Boundary = {identity: string; objectId: string; objectType: 'request' | 'tool'; turnId: string; turnOrdinal: number; time: string; label: string; order: number};

export function TraceTimeline({turns, requests, tools, events, selected, onLocate}: {
  turns: {turn_id: string; ordinal: number}[]; requests: Request[]; tools: Tool[]; events: Event[];
  selected: string | null; onLocate: (objectType: 'request' | 'tool', objectId: string, turnId: string) => void;
}) {
  const boundaries: Boundary[] = [];
  function add(objectType: 'request' | 'tool', objectId: string, turnId: string, time: string | null | undefined, kind: string, label: string, fallback: number) {
    if (!time) return;
    const turnOrdinal = turns.find(turn => turn.turn_id === turnId)?.ordinal ?? 0;
    const related=events.filter(item => objectType === 'request' ? item.request_id === objectId && !item.tool_call_id : item.tool_call_id === objectId);
    const event=related.find(item => item.kind === kind) ?? (kind.endsWith('.result') ? related.at(-1) : undefined);
    boundaries.push({identity: `${objectId}:${kind}`, objectId, objectType, turnId, turnOrdinal, time, label, order: (event?.sequence ?? fallback) * 10 + (kind.endsWith('.result') ? 1 : 0)});
  }
  for (const request of requests) {
    const title = `模型请求 ${request.ordinal}`;
    add('request', request.request_id, request.turn_id, request.started_at, 'request.started', `${title} · 请求开始${request.interrupted ? '（中断，结果未知）' : request.finished_at ? '' : '（尚未结束）'}`, request.ordinal * 10);
    add('request', request.request_id, request.turn_id, request.finished_at, request.status === 'failed' ? 'request.failed' : 'request.completed', `${title} · 请求结束${request.status === 'failed' ? '／失败' : '／已返回'}`, request.ordinal * 10 + 9);
  }
  for (const tool of tools) {
    const title = `工具 ${tool.ordinal} · ${tool.name}`;
    add('tool', tool.tool_call_id, tool.turn_id, tool.proposed_at, 'tool.pending', `${title} · 模型提出`, tool.ordinal * 10);
    add('tool', tool.tool_call_id, tool.turn_id, tool.waiting_at, 'tool.waiting', `${title} · 限速等待开始`, tool.ordinal * 10 + 1);
    add('tool', tool.tool_call_id, tool.turn_id, tool.call_started_at, 'tool.running', `${title} · SDK 开始${tool.interrupted ? '（中断，结果未知）' : tool.call_finished_at ? '' : '（尚未结束）'}`, tool.ordinal * 10 + 2);
    add('tool', tool.tool_call_id, tool.turn_id, tool.call_finished_at, `tool.${tool.status}`, `${title} · SDK 结束`, tool.ordinal * 10 + 3);
    add('tool', tool.tool_call_id, tool.turn_id, tool.finished_at, `tool.${tool.status}.result`, `${title} · 应用回填${tool.status === 'not_executed' ? '（未调用）' : ''}`, tool.ordinal * 10 + 4);
  }
  boundaries.sort((a, b) => a.time.localeCompare(b.time) || a.order - b.order || a.identity.localeCompare(b.identity));
  const times = boundaries.map(item => Date.parse(item.time));
  const start = times.length ? Math.min(...times) : 0;
  const range = times.length ? Math.max(...times) - start : 0;
  function position(from: string, to?: string | null) {
    return {left: `${range ? (Date.parse(from) - start) / range * 100 : 0}%`, width: to && range ? `${(Date.parse(to) - Date.parse(from)) / range * 100}%` : '0%'};
  }
  return <section className="trace-timeline" aria-label="实际时间线">
    <div className="timeline-caption"><h4>实际时间线</h4><span>未结束调用仅标记开始</span></div>
    <div className="timeline-lane"><span>模型</span><div>{requests.map(item => <button type="button" key={item.request_id} className={`timeline-bar model ${selected === item.request_id ? 'selected' : ''}`} style={position(item.started_at, item.finished_at)} aria-label={`第 ${turns.find(turn => turn.turn_id === item.turn_id)?.ordinal} 轮，模型请求 ${item.ordinal}，${item.finished_at ? '已结束' : '尚未结束'}`} title={`模型请求 ${item.ordinal} · ${item.started_at}${item.finished_at ? ` → ${item.finished_at}` : ' · 结束未知'}`} onClick={() => onLocate('request', item.request_id, item.turn_id)} />)}</div></div>
    <div className="timeline-lane"><span>工具</span><div>{tools.map(item => <button type="button" key={item.tool_call_id} className={`timeline-bar tool ${selected === item.tool_call_id ? 'selected' : ''}`} style={position(item.proposed_at, item.finished_at)} aria-label={`第 ${turns.find(turn => turn.turn_id === item.turn_id)?.ordinal} 轮，工具 ${item.ordinal}，${item.name}，提出至应用回填${item.finished_at ? '' : '，尚未结束'}`} title={`${item.name} · 提出 ${item.proposed_at}${item.finished_at ? ` → 应用回填 ${item.finished_at}` : ' · 回填未知'}`} onClick={() => onLocate('tool', item.tool_call_id, item.turn_id)} />)}</div></div>
    <details className="timeline-boundaries"><summary>查看 {boundaries.length} 个已采集时间边界</summary>
    <p className="hint">模型条显示请求至结束；工具条显示提出至应用回填。SDK 与限速等待的独立边界见下方记录。</p>
    <ol>{boundaries.map(item => <li key={item.identity}>
      <button type="button" data-reading-anchor={`boundary:${item.identity}`} aria-pressed={selected === item.objectId} onClick={() => onLocate(item.objectType, item.objectId, item.turnId)}>
        <time dateTime={item.time}>{new Date(item.time).toLocaleTimeString('zh-CN', {hour12:false, hour:'2-digit', minute:'2-digit', second:'2-digit', fractionalSecondDigits:3})}</time>
        <span>第 {item.turnOrdinal} 轮 · {item.label}</span>
      </button>
    </li>)}</ol></details>
    {!boundaries.length && <p className="hint">等待真实请求与工具事件。</p>}
  </section>;
}
