import { useEffect, useState, type FormEvent } from 'react';
import { createRoot } from 'react-dom/client';
import './style.css';
import { TraceView } from './TraceView';
import { HistorySidebar, useConversationDraft } from './HistorySidebar';
import { ConversationRounds, type Turn } from './ConversationRounds';
import { useSubmission } from './useSubmission';
import { useServiceEvents } from './useServiceEvents';

type Snapshot = { schema_version: number; session: { title: string }; turns: Turn[]; next_before: number | null; total_turns: number };

async function api<T>(path: string, options?: RequestInit): Promise<T> {
  const response = await fetch(path, options);
  const body = await response.json();
  if (!response.ok) throw new Error(typeof body.detail === 'string' ? body.detail : body.detail?.message ?? '请求未成功，请检查输入或本机服务。');
  if (body.schema_version !== 1) throw new Error('数据版本不兼容，请更新页面和服务。');
  return body;
}

const reasonLabels: Record<string, string> = {
  model_error: '模型请求失败', execution_error: '服务执行失败', budget: '查询达到上限',
  output_limit: '输出达到上限', map_paused: '地图查询已暂停', service_shutdown: '服务退出', service_interrupted: '服务中断',
};
function statusText(turn: Turn): string {
  if (turn.status === 'running') return '正在执行';
  if (turn.status === 'completed') return turn.tool_error_count ? '完成，含工具错误' : '已完成';
  return `${turn.status === 'failed' ? '失败' : '终止'}：${reasonLabels[turn.reason ?? ''] ?? turn.reason ?? '原因未知'}`;
}

const mapPauseLabels: Record<string, string> = {
  auth: '地图鉴权失败，请检查配置权限后重启。', quota: '地图额度耗尽或访问受限，请检查额度与限流后重启。',
  connection: '地图连接不可用，请检查网络及服务后重启。', service: '地图服务暂不可用，请等待服务恢复后重启。',
};

function App() {
  const [view, setView] = useState<'conversation' | 'trace'>('conversation');
  const [sessionId, setSessionId] = useState<string | null>(null);
  const {draft, setDraft, removeDraft, clearSubmittedDraft} = useConversationDraft(sessionId);
  const [earlierTurns, setEarlierTurns] = useState<Record<string, Turn[]>>({});
  const [loadingEarlier, setLoadingEarlier] = useState(false);
  const [snapshot, setSnapshot] = useState<Snapshot | null>(null);
  const {state, revision: serviceRevision, connectionError, refresh: refreshService} = useServiceEvents();
  const [error, setError] = useState('');

  const submission = useSubmission((accepted, submitted) => {
    clearSubmittedDraft(submitted.session_id, submitted.input);
    if (sessionId === submitted.session_id && accepted.session_id !== sessionId) setSnapshot(null);
    setSessionId(current => current === submitted.session_id ? accepted.session_id : current);
    void refreshService().catch(() => setError('提交已接受，但暂时无法核对服务状态。'));
  });

  useEffect(() => {
    if (!sessionId) return;
    let cancelled = false;
    async function refresh() {
      try {
        const result = await api<Snapshot>(`/api/sessions/${sessionId}`);
        if (!cancelled) setSnapshot(result);
      } catch (cause) {
        if (!cancelled) setError(cause instanceof Error ? cause.message : '无法读取会话。');
      }
    }
    void refresh();
    const interval = window.setInterval(refresh, 500);
    return () => { cancelled = true; window.clearInterval(interval); };
  }, [sessionId, serviceRevision]);

  async function send(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (state.active_turn_id || !state.accepting) return;
    setError('');
    await submission.send(draft, sessionId);
  }

  function selectConversation(id: string | null) {
    setSessionId(id); setSnapshot(null); setError('');
  }
  function newConversation() { selectConversation(null); }
  function deletedConversation(id: string) {
    removeDraft(id);
    setEarlierTurns(current => { const remaining = {...current}; delete remaining[id]; return remaining; });
    if (sessionId === id) selectConversation(null);
  }
  async function loadEarlier() {
    if (!sessionId || !snapshot) return;
    const id = sessionId;
    const before = earlierTurns[id]?.[0]?.ordinal ?? snapshot.next_before;
    if (!before) return;
    setLoadingEarlier(true);
    try {
      const page = await api<Snapshot>(`/api/sessions/${id}?before=${before}`);
      setEarlierTurns(current => ({...current, [id]: [...page.turns, ...(current[id] ?? [])]}));
    } catch (cause) { setError(cause instanceof Error ? cause.message : '无法读取更早对话。'); }
    finally { setLoadingEarlier(false); }
  }
  const busy = Boolean(state.active_turn_id) || submission.submitting;
  const turns = [...(sessionId ? earlierTurns[sessionId] ?? [] : []), ...(snapshot?.turns ?? [])]
    .filter((turn, index, all) => all.findIndex(item => item.turn_id === turn.turn_id) === index);
  const turn = snapshot?.turns.at(-1);
  const canLoadEarlier = Boolean(snapshot && turns[0]?.ordinal > 1);
  return <div className="app">
    <header>
      <div><p className="eyebrow">本机调试</p><h1>旅行助手</h1></div>
      <div className="global-status">
        <p role="status">{state.active_turn_id ? `${state.stopping ? '正在停止，仍占用执行名额' : '全局忙，正在执行'} · ${state.active_session_id === sessionId ? '当前会话' : '其他会话'}` : state.accepting ? '全局准备就绪' : '本机服务暂不可用'}</p>
        {state.active_session_id && state.active_session_id !== sessionId && <button onClick={() => selectConversation(state.active_session_id)}>查看执行中的会话</button>}
        {state.map_paused && <p>地图查询已暂停：{mapPauseLabels[state.map_pause_reason ?? ''] ?? '请修复配置、网络或服务后重启。'}</p>}
      </div>
      <button onClick={newConversation}>新建对话</button>
    </header>
    <main>
      <section className="conversation" aria-labelledby="conversation-title">
        <div className="conversation-heading">
          <h2 id="conversation-title">{snapshot?.session.title ?? '空白对话'}</h2>
          <p role="status">{turn ? statusText(turn) : busy ? '其他对话正在执行' : '准备就绪'}</p>
        </div>
        <nav className="view-switch" aria-label="会话视图"><button aria-pressed={view === 'conversation'} onClick={() => setView('conversation')}>对话</button><button aria-pressed={view === 'trace'} onClick={() => setView('trace')}>执行轨迹</button></nav>
        {view === 'trace' ? <TraceView sessionId={sessionId} refreshKey={serviceRevision} /> : <div className="messages">
          {turn ? <>
            {canLoadEarlier && <button className="load-earlier" disabled={loadingEarlier} onClick={() => void loadEarlier()}>加载更早对话</button>}
            <ConversationRounds turns={turns} statusText={statusText} />
          </> : <div className="empty"><h3>从一条旅行需求开始</h3><p>可以查询地点、比较交通路线、寻找餐饮，或安排多日旅行行程。</p><p className="example">例如：查询杭州西湖的地址。</p></div>}
        </div>}
        <form onSubmit={send}>
          <label htmlFor="travel-input">旅行需求</label>
          <textarea id="travel-input" name="travel-input" value={draft} onChange={event => setDraft(event.target.value)} required rows={4} placeholder="写下城市、日期和你想查询的内容" />
          <div className="form-bottom"><p className="hint">{sessionId ? '继续提问会使用此会话的完整上下文。切换会话保留当前草稿。' : '发送后保存到本机；空白对话不会产生记录。'}</p><button type="submit" disabled={busy || Boolean(submission.pending) || !state.accepting}>{submission.submitting ? '正在发送…' : '发送'}</button></div>
          <p role="status" className="hint">{submission.pending ? '这次提交尚待核对；编辑草稿不会改变原提交，刷新页面不会自动重发。' : ''}</p>
          {submission.pending && <div className="submission-actions">
            <button type="button" disabled={submission.submitting} onClick={() => void submission.check()}>核对提交</button>
            <button type="button" disabled={submission.submitting} onClick={() => void submission.retry()}>安全重试同一提交</button>
          </div>}
          <p className="error" role="alert">{submission.error || error}</p>
          <p className="hint" role="status">{connectionError}</p>
          {!state.accepting && <p className="error">{state.storage_error ?? (connectionError ? '请等待连接恢复后再发送。' : '本机服务暂不可用，请检查后重启。')}</p>}
        </form>
      </section>
      <HistorySidebar sessionId={sessionId} activeTurnId={state.active_turn_id} onSelect={selectConversation} onDelete={deletedConversation} />
    </main>
  </div>;
}

createRoot(document.getElementById('root')!).render(<App />);
