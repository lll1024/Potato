import { useEffect, useState, type FormEvent } from 'react';
import { createRoot } from 'react-dom/client';
import './style.css';
import { TraceView } from './TraceView';
import { HistorySidebar, useConversationDraft } from './HistorySidebar';
import { ConversationRounds, type Turn } from './ConversationRounds';

type Snapshot = { schema_version: number; session: { title: string }; turns: Turn[]; next_before: number | null; total_turns: number };
type ServiceState = { active_turn_id: string | null; accepting: boolean };

async function api<T>(path: string, options?: RequestInit): Promise<T> {
  const response = await fetch(path, options);
  const body = await response.json();
  if (!response.ok) throw new Error(typeof body.detail === 'string' ? body.detail : '请求未成功，请检查输入或本机服务。');
  if (body.schema_version !== 1) throw new Error('数据版本不兼容，请更新页面和服务。');
  return body;
}

const reasonLabels: Record<string, string> = {
  model_error: '模型请求失败', execution_error: '服务执行失败', budget: '查询达到上限',
  output_limit: '输出达到上限', map_paused: '地图查询已暂停', service_shutdown: '服务退出',
};
function statusText(turn: Turn): string {
  if (turn.status === 'running') return '正在执行';
  if (turn.status === 'completed') return turn.tool_error_count ? '完成，含工具错误' : '已完成';
  return `${turn.status === 'failed' ? '失败' : '终止'}：${reasonLabels[turn.reason ?? ''] ?? turn.reason ?? '原因未知'}`;
}

function App() {
  const [view, setView] = useState<'conversation' | 'trace'>('conversation');
  const [sessionId, setSessionId] = useState<string | null>(null);
  const {draft, setDraft, removeDraft} = useConversationDraft(sessionId);
  const [earlierTurns, setEarlierTurns] = useState<Record<string, Turn[]>>({});
  const [loadingEarlier, setLoadingEarlier] = useState(false);
  const [snapshot, setSnapshot] = useState<Snapshot | null>(null);
  const [state, setState] = useState<ServiceState>({ active_turn_id: null, accepting: true });
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    let cancelled = false;
    async function refresh() {
      try {
        const result = await api<ServiceState>('/api/state');
        if (!cancelled) setState(result);
      } catch (cause) {
        if (!cancelled) setError(cause instanceof Error ? cause.message : '无法连接本机服务。');
      }
    }
    void refresh();
    const interval = window.setInterval(refresh, 500);
    return () => { cancelled = true; window.clearInterval(interval); };
  }, []);

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
  }, [sessionId]);

  async function send(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError('');
    setSubmitting(true);
    try {
      const accepted = await api<{session_id: string; turn_id: string}>('/api/turns', {
        method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({input: draft, session_id: sessionId}),
      });
      setState(current => ({...current, active_turn_id: accepted.turn_id}));
      if (accepted.session_id !== sessionId) setSnapshot(null);
      setSessionId(accepted.session_id);
      setDraft('');
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : '发送失败，输入仍保留。');
    } finally { setSubmitting(false); }
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
  const busy = Boolean(state.active_turn_id) || submitting;
  const turns = [...(sessionId ? earlierTurns[sessionId] ?? [] : []), ...(snapshot?.turns ?? [])]
    .filter((turn, index, all) => all.findIndex(item => item.turn_id === turn.turn_id) === index);
  const turn = snapshot?.turns.at(-1);
  const canLoadEarlier = Boolean(snapshot && turns[0]?.ordinal > 1);
  return <div className="app">
    <header>
      <div><p className="eyebrow">本机调试</p><h1>旅行助手</h1></div>
      <button onClick={newConversation}>新建对话</button>
    </header>
    <main>
      <section className="conversation" aria-labelledby="conversation-title">
        <div className="conversation-heading">
          <h2 id="conversation-title">{snapshot?.session.title ?? '空白对话'}</h2>
          <p role="status">{turn ? statusText(turn) : busy ? '其他对话正在执行' : '准备就绪'}</p>
        </div>
        <nav className="view-switch" aria-label="会话视图"><button aria-pressed={view === 'conversation'} onClick={() => setView('conversation')}>对话</button><button aria-pressed={view === 'trace'} onClick={() => setView('trace')}>执行轨迹</button></nav>
        {view === 'trace' ? <TraceView sessionId={sessionId} /> : <div className="messages">
          {turn ? <>
            {canLoadEarlier && <button className="load-earlier" disabled={loadingEarlier} onClick={() => void loadEarlier()}>加载更早对话</button>}
            <ConversationRounds turns={turns} statusText={statusText} />
          </> : <div className="empty"><h3>从一条旅行需求开始</h3><p>可以查询地点、比较交通路线、寻找餐饮，或安排多日旅行行程。</p><p className="example">例如：查询杭州西湖的地址。</p></div>}
        </div>}
        <form onSubmit={send}>
          <label htmlFor="travel-input">旅行需求</label>
          <textarea id="travel-input" name="travel-input" value={draft} onChange={event => setDraft(event.target.value)} required rows={4} placeholder="写下城市、日期和你想查询的内容" />
          <div className="form-bottom"><p className="hint">{sessionId ? '继续提问会使用此会话的完整上下文。切换会话保留当前草稿。' : '发送后保存到本机；空白对话不会产生记录。'}</p><button type="submit" disabled={busy || !state.accepting}>{submitting ? '正在发送…' : '发送'}</button></div>
          <p className="error" role="alert">{error}</p>
          {!state.accepting && <p className="error">服务无法保存或正在退出，请检查后重启。</p>}
        </form>
      </section>
      <HistorySidebar sessionId={sessionId} activeTurnId={state.active_turn_id} onSelect={selectConversation} onDelete={deletedConversation} />
    </main>
  </div>;
}

createRoot(document.getElementById('root')!).render(<App />);
