export type RetryEvent = {
  event_id: string; turn_id: string; sequence: number; kind: string; timestamp?: string;
  request_id?: string | null;
  data?: {retry_id?: string; step?: number; retry?: number; max_retries?: number; delay_ms?: number; code?: string; message?: string};
};

export function retryEntries(events: RetryEvent[], turn: {turn_id: string; status: string}) {
  return events.filter(event => event.turn_id === turn.turn_id && event.kind === 'request.retry_scheduled').map(event => {
    const data = event.data;
    const started = events.some(item => item.turn_id === turn.turn_id && item.kind === 'request.retry_started'
      && item.data?.retry_id === data?.retry_id && item.data?.retry === data?.retry);
    return {event, data, state: started ? 'started' : turn.status === 'running' ? 'waiting' : 'cancelled'};
  });
}

export function ModelRetries({events, turn}: {events: RetryEvent[]; turn: {turn_id: string; status: string}}) {
  const entries = retryEntries(events, turn);
  if (!entries.length) return null;
  return <details className="model-retries">
    <summary>模型重试记录 · {entries.length} 次</summary>
    <ol>{entries.map(({event, data, state}) => <li key={event.event_id}>
      <span>步骤 {data?.step ?? '未知'} · 重试 {data?.retry ?? '未知'}/{data?.max_retries ?? '未知'} · </span>
      <span>{state === 'waiting' ? '等待重试' : state === 'started' ? '退避结束，恢复尝试' : '等待已结束，未发起重试'}</span>
      <p className="hint">计划等待 {data?.delay_ms === undefined ? '未知' : `${(data.delay_ms / 1000).toFixed(2)} 秒`}
        {data?.code && ` · ${data.code}`}{data?.message && ` · ${data.message}`}</p>
    </li>)}</ol>
  </details>;
}
