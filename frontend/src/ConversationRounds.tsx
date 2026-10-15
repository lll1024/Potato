import { AnswerBody } from './AnswerBody';
import { RoundProgress, type RoundActivity } from './RoundProgress';
import { ExceptionOutcome, isExceptional, type QueryMaterial } from './ExceptionOutcome';

export type Turn = {
  turn_id: string; ordinal: number; input: string; status: 'running' | 'completed' | 'failed' | 'terminated';
  reason: string | null; answer: string | null; answer_source: 'model' | 'application' | null;
  model_error_code?: string | null;
  tool_error_count: number; context_excluded?: boolean; created_at?: string; finished_at?: string | null; duration_ms?: number | null;
  query_materials?: QueryMaterial[]; unreadable_query_count?: number;
};

export function ConversationRounds({turns, statusText, onTrace, activity, stoppingTurnId, disconnected, onRetry, retryTurnId, retryBlocked, storageTurnId, onStop}: {
  turns: Turn[]; statusText: (turn: Turn) => string; onTrace?: (turn: Turn) => void; activity?: RoundActivity;
  stoppingTurnId?: string | null; disconnected?: boolean;
  onRetry?: (turn: Turn) => void; retryTurnId?: string; retryBlocked?: string;
  storageTurnId?: string;
  onStop?: (turn: Turn) => void;
}) {
  return <>{turns.map(turn => <section key={turn.turn_id} aria-label={`第 ${turn.ordinal} 轮对话`}>
    <div className="round-heading"><p className="round-status">第 {turn.ordinal} 轮 · {turn.turn_id === storageTurnId ? '存储故障，结果未完整保存' : statusText(turn)}</p>{onTrace && <button type="button" onClick={() => onTrace(turn)}>查看本轮轨迹</button>}</div>
    <article className="message user" data-reading-anchor={`input:${turn.turn_id}`}><h3>你的输入</h3><p>{turn.input}</p></article>
    {(isExceptional(turn) || turn.turn_id === storageTurnId) && <ExceptionOutcome turn={turn} storageFault={turn.turn_id === storageTurnId}
      onRetry={turn.turn_id === retryTurnId ? onRetry : undefined} retryBlocked={retryBlocked} />}
    {!isExceptional(turn) && turn.turn_id !== storageTurnId && <RoundProgress turn={turn} activity={activity} stopping={stoppingTurnId === turn.turn_id} disconnected={disconnected} statusText={statusText(turn)} onStop={onStop ? () => onStop(turn) : undefined} />}
    {turn.answer?.trim() && (!isExceptional(turn) || (['output_limit', 'budget'].includes(turn.reason ?? '') && turn.answer_source === 'model')) && <article className="message answer" data-reading-anchor={`answer:${turn.turn_id}`}>
      <div className="answer-heading"><h3>旅行助手</h3><span>{turn.answer_source === 'model' ? '模型回答' : '应用说明'}</span></div>
      {isExceptional(turn) && <p className="hint">回答不完整</p>}
      <AnswerBody text={turn.answer} />
    </article>}
  </section>)}</>;
}
