export type ContextEvent = {
  event_id: string; turn_id: string; kind: string;
  data?: {valid?: boolean; used_in_turn?: boolean; result_unknown?: boolean; candidate_recorded?: boolean; reason?: string | null; status?: string; version?: number; before_tokens?: number; after_tokens?: number; changed_result_count?: number; pressure_remaining?: boolean};
};

export function contextEventText(event: ContextEvent): string | null {
  const data = event.data;
  switch (event.kind) {
    case 'context.organized': return `工具结果已整理 · ${data?.before_tokens ?? '未知'} → ${data?.after_tokens ?? '未知'} tokens（估算） · 整理 ${data?.changed_result_count ?? '未知'} 项 · ${data?.pressure_remaining ? '仍有上下文压力' : '上下文压力已缓解'}`;
    case 'context.summary_started': return '正在整理较早的旅行记录';
    case 'context.summary_result': return data?.status === 'completed' ? '摘要请求已返回，等待校验' : '摘要请求失败，保留原上下文';
    case 'context.validated': return data?.valid ? '摘要校验通过' : `摘要未采用：${data?.reason ?? '校验未通过'}`;
    case 'context.candidate': return '摘要候选已记录，尚未用于本轮';
    case 'context.discarded':
      if (data?.result_unknown) return '服务中断，摘要请求结果未知；续聊恢复上一有效版本';
      if (data?.used_in_turn) return '摘要曾用于本轮，未保存供续聊；已弃用，续聊恢复上一有效版本';
      return data?.candidate_recorded ? '摘要候选未采用，已弃用；续聊恢复上一有效版本' : '本轮整理已结束，未保存新版本；续聊恢复上一有效版本';
    case 'context.adopted': return `摘要已用于本轮，尚未保存供续聊 · 输入估算 ${data?.before_tokens ?? '未知'} → ${data?.after_tokens ?? '未知'} tokens`;
    case 'context.saved': return '摘要已保存供续聊';
    case 'context.summary_unavailable': return `无法整理：${data?.reason ?? '保留原上下文'}`;
    default: return null;
  }
}

export function ContextMaintenance({events, turnId}: {events: ContextEvent[]; turnId: string}) {
  const entries = events.filter(event => event.turn_id === turnId).map(event => ({event, text: contextEventText(event)})).filter(entry => entry.text !== null);
  if (!entries.length) return null;
  const latest = entries.at(-1)!;
  return <details className="model-retries" open={latest.event.kind === 'context.summary_started'}>
    <summary>旅行记录整理 · {latest.text}</summary>
    <ol aria-live="polite">{entries.map(({event, text}) => <li key={event.event_id}>{text}</li>)}</ol>
  </details>;
}
