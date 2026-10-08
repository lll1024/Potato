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
  return <section className="trace-timeline" aria-label="实际时间线">
    <h4>实际时间线</h4><p className="hint">只展示已采集的模型请求、工具 SDK 与应用边界；未结束调用没有结束时间或进度百分比。</p>
    <ol>{boundaries.map(item => <li key={item.identity} data-reading-id={item.identity}>
      <button type="button" aria-pressed={selected === item.objectId} onClick={() => onLocate(item.objectType, item.objectId, item.turnId)}>
        <time dateTime={item.time}>{new Date(item.time).toLocaleTimeString('zh-CN', {hour12:false, fractionalSecondDigits:3})}</time>
        <span>第 {item.turnOrdinal} 轮 · {item.label}</span>
      </button>
    </li>)}</ol>
    {!boundaries.length && <p className="hint">等待真实请求与工具事件。</p>}
  </section>;
}
