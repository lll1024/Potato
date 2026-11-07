import { useEffect, useState } from 'react';
import type { AnswerPreview } from './useServiceEvents';
import { AnswerBody } from './AnswerBody';
import { RoundProgress, type RoundActivity } from './RoundProgress';
import { ExceptionOutcome, isExceptional, type QueryMaterial } from './ExceptionOutcome';

export type Turn = {
  turn_id: string; ordinal: number; input: string; status: 'running' | 'completed' | 'failed' | 'terminated';
  reason: string | null; answer: string | null; answer_source: 'model' | 'application' | null;
  model_error_code?: string | null;
  answer_delivery?: 'stream' | 'complete' | null;
  tool_error_count: number; context_excluded?: boolean; created_at?: string; finished_at?: string | null; duration_ms?: number | null;
  query_materials?: QueryMaterial[]; unreadable_query_count?: number;
};

function AnswerCopy({text, label}: {text: string; label: string}) {
  const [notice, setNotice] = useState('');
  useEffect(() => setNotice(''), [text, label]);
  async function copy() {
    try { await navigator.clipboard.writeText(text); setNotice('已复制'); }
    catch { setNotice('复制失败，请选择正文手动复制。'); }
  }
  return <><button type="button" onClick={() => void copy()}>{label}</button><span role="status">{notice}</span></>;
}

export function ConversationRounds({turns, statusText, onTrace, activity, stoppingTurnId, disconnected, onRetry, retryTurnId, retryBlocked, storageTurnId, onStop, preview}: {
  preview?: AnswerPreview | null;
  turns: Turn[]; statusText: (turn: Turn) => string; onTrace?: (turn: Turn) => void; activity?: RoundActivity;
  stoppingTurnId?: string | null; disconnected?: boolean;
  onRetry?: (turn: Turn) => void; retryTurnId?: string; retryBlocked?: string;
  storageTurnId?: string;
  onStop?: (turn: Turn) => void;
}) {
  return <>{turns.map(turn => {
    const current = turn.status === 'running' && preview?.turn_id === turn.turn_id ? preview : null;
    return <section key={turn.turn_id} aria-label={`第 ${turn.ordinal} 轮对话`}>
    <div className="round-heading"><p className="round-status">第 {turn.ordinal} 轮 · {turn.turn_id === storageTurnId ? '存储故障，结果未完整保存' : statusText(turn)}</p>{onTrace && <button type="button" onClick={() => onTrace(turn)}>查看本轮轨迹</button>}</div>
    <article className="message user" data-reading-anchor={`input:${turn.turn_id}`}><h3>你的输入</h3><p>{turn.input}</p></article>
    {(isExceptional(turn) || turn.turn_id === storageTurnId) && <ExceptionOutcome turn={turn} storageFault={turn.turn_id === storageTurnId}
      onRetry={turn.turn_id === retryTurnId ? onRetry : undefined} retryBlocked={retryBlocked} />}
    {!isExceptional(turn) && turn.turn_id !== storageTurnId && <RoundProgress turn={turn} activity={activity} stopping={stoppingTurnId === turn.turn_id} disconnected={disconnected} statusText={statusText(turn)} onStop={onStop ? () => onStop(turn) : undefined} />}
    {current && current.status !== 'retrying' && turn.turn_id !== storageTurnId && onStop && <button type="button"
      disabled={stoppingTurnId === turn.turn_id || disconnected} onClick={() => onStop(turn)}>停止生成</button>}
    {current?.status === 'retrying' && <p className="hint" role="status">正在重试，新尝试将从头作答。</p>}
    {(current?.delivery === 'complete' || turn.answer_delivery === 'complete') && <p className="hint">模型服务不支持流式，改为生成完整回答。</p>}
    {current?.text.trim() && <article className="message answer" data-reading-anchor={`answer:${turn.turn_id}`}>
      <div className="answer-heading"><h3>旅行助手</h3><span>{turn.turn_id === storageTurnId ? '未保存 · 请复制保留，刷新后可能丢失'
        : stoppingTurnId === turn.turn_id ? '正在停止 · 正文已暂停追加' : current.status === 'generated' ? '正在保存 · 尚未保存' : '正在生成 · 尚未保存'}</span></div>
      <AnswerBody text={current.text} />
      <AnswerCopy text={current.text} label={turn.turn_id === storageTurnId ? '复制未保存正文' : '复制当前正文'} />
    </article>}
    {turn.answer?.trim() && (!isExceptional(turn) || (['output_limit', 'budget'].includes(turn.reason ?? '') && turn.answer_source === 'model')) && <article className="message answer" data-reading-anchor={`answer:${turn.turn_id}`}>
      <div className="answer-heading"><h3>旅行助手</h3><span>{turn.answer_source === 'model' ? '模型回答' : '应用说明'}</span></div>
      {isExceptional(turn) && <p className="hint">回答不完整</p>}
      <AnswerBody text={turn.answer} />
      <AnswerCopy text={turn.answer} label={isExceptional(turn) ? '复制不完整回答' : '复制完整回答'} />
    </article>}
  </section>;})}</>;
}
