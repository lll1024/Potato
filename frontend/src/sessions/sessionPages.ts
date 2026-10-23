type PageRows = {
  turns: {turn_id: string; ordinal: number}[];
  requests?: {request_id: string; turn_id: string}[];
  tool_calls?: {tool_call_id: string; turn_id: string}[];
  events?: {event_id: string; turn_id: string; sequence: number}[];
  next_before: number | null;
  cursor?: number;
};

function unique<T>(older: T[], newer: T[], identity: (row: T) => string): T[] {
  const rows = new Map(older.map(row => [identity(row), row]));
  for (const row of newer) rows.set(identity(row), row);
  return [...rows.values()];
}

// 对话与轨迹保留各自投影；整轮替换刷新事实，分页保留较新的已读事实。
export function mergeSessionPage<T extends PageRows>(current: T | null, page: T, mode: 'refresh' | 'earlier' | 'locate', requestedBefore?: number): T {
  if (!current) return page;
  const stale = page.cursor !== undefined && current.cursor !== undefined && page.cursor < current.cursor;
  const keepCurrent = mode === 'earlier' || stale;
  const replacing = new Set(keepCurrent ? [] : page.turns.map(turn => turn.turn_id));
  const rows = <R extends {turn_id: string}>(old: R[], incoming: R[], identity: (row: R) => string) =>
    keepCurrent ? unique(incoming, old, identity) : unique(old.filter(row => !replacing.has(row.turn_id)), incoming, identity);
  const historyGap = !stale && mode === 'refresh' && page.turns.length > 0 && current.turns.length > 0
    && page.turns[0].ordinal > current.turns.at(-1)!.ordinal + 1;
  const advanceEarlier = mode === 'earlier' && requestedBefore === current.next_before;
  const metadata = stale ? current : page;
  return {...current, ...metadata,
    next_before: historyGap || advanceEarlier ? page.next_before : current.next_before,
    turns: rows(current.turns, page.turns, row => row.turn_id).sort((a, b) => a.ordinal - b.ordinal),
    requests: rows(current.requests ?? [], page.requests ?? [], row => row.request_id),
    tool_calls: rows(current.tool_calls ?? [], page.tool_calls ?? [], row => row.tool_call_id),
    events: rows(current.events ?? [], page.events ?? [], row => row.event_id).sort((a, b) => a.sequence - b.sequence),
  };
}
