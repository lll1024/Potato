export type Turn = {
  turn_id: string; ordinal: number; input: string; status: 'running' | 'completed' | 'failed' | 'terminated';
  reason: string | null; answer: string | null; answer_source: 'model' | 'application' | null;
  tool_error_count: number; context_excluded?: boolean;
};

export function ConversationRounds({turns, statusText}: {turns: Turn[]; statusText: (turn: Turn) => string}) {
  return <>{turns.map(turn => <section key={turn.turn_id} aria-label={`第 ${turn.ordinal} 轮对话`}>
    <p className="round-status">第 {turn.ordinal} 轮 · {statusText(turn)}</p>
    <article className="message user"><h3>你的输入</h3><p>{turn.input}</p></article>
    {turn.answer !== null && <article className="message answer">
      <div className="answer-heading"><h3>旅行助手</h3><span>{turn.answer_source === 'model' ? '模型回答' : '应用说明'}</span></div>
      <p>{turn.answer}</p>
    </article>}
    {turn.context_excluded && <p className="hint">服务中断，未结束调用的结果未知。本轮输入与轨迹仍可回看，整轮已排除后续上下文；继续提问会创建新轮次。</p>}
    {turn.status === 'running' && <p className="hint">正在查询，最终回答会在本轮结束后显示。关闭页面不会停止后台查询。</p>}
  </section>)}</>;
}
