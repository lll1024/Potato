import { RoundProgress, type RoundActivity } from './RoundProgress';

export type Turn = {
  turn_id: string; ordinal: number; input: string; status: 'running' | 'completed' | 'failed' | 'terminated';
  reason: string | null; answer: string | null; answer_source: 'model' | 'application' | null;
  tool_error_count: number; context_excluded?: boolean; created_at?: string; finished_at?: string | null; duration_ms?: number | null;
};

export function ConversationRounds({turns, statusText, onTrace, activity, stoppingTurnId, disconnected, onRead}: {
  turns: Turn[]; statusText: (turn: Turn) => string; onTrace?: (turn: Turn) => void; activity?: RoundActivity;
  stoppingTurnId?: string | null; disconnected?: boolean; onRead?: () => void;
}) {
  return <>{turns.map(turn => <section key={turn.turn_id} aria-label={`第 ${turn.ordinal} 轮对话`}>
    <div className="round-heading"><p className="round-status">第 {turn.ordinal} 轮 · {statusText(turn)}</p>{onTrace && <button type="button" onClick={() => onTrace(turn)}>查看本轮轨迹</button>}</div>
    <article className="message user" data-reading-anchor={`input:${turn.turn_id}`}><h3>你的输入</h3><p>{turn.input}</p></article>
    <RoundProgress turn={turn} activity={activity} stopping={stoppingTurnId === turn.turn_id} disconnected={disconnected} statusText={statusText(turn)} onRead={onRead} />
    {turn.answer !== null && <article className="message answer" data-reading-anchor={`answer:${turn.turn_id}`}>
      <div className="answer-heading"><h3>旅行助手</h3><span>{turn.answer_source === 'model' ? '模型回答' : '应用说明'}</span></div>
      <p>{turn.answer}</p>
    </article>}
    {turn.context_excluded && <p className="hint">服务中断，未结束调用的结果未知。本轮输入与轨迹仍可回看，整轮已排除后续上下文；继续提问会创建新轮次。</p>}
  </section>)}</>;
}
