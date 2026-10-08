import { useEffect, useState, type FormEvent } from 'react';
import { createRoot } from 'react-dom/client';
import './style.css';

type Turn = {
  turn_id: string; input: string; status: 'running' | 'completed' | 'failed' | 'terminated';
  reason: string | null; answer: string | null; answer_source: 'model' | 'application' | null;
  tool_error_count: number;
};
type Snapshot = { schema_version: number; session: { title: string }; turns: Turn[] };
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
  const [draft, setDraft] = useState('');
  const [sessionId, setSessionId] = useState<string | null>(null);
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
        method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({input: draft}),
      });
      setState(current => ({...current, active_turn_id: accepted.turn_id}));
      setSessionId(accepted.session_id);
      setDraft('');
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : '发送失败，输入仍保留。');
    } finally { setSubmitting(false); }
  }

  function newConversation() {
    setSessionId(null); setSnapshot(null); setDraft(''); setError('');
  }
  const busy = Boolean(state.active_turn_id) || submitting;
  const turn = snapshot?.turns[0];
  return <div className="app">
    <header>
      <div><p className="eyebrow">本机调试</p><h1>旅行助手</h1></div>
      <button onClick={newConversation} disabled={busy}>新建对话</button>
    </header>
    <main>
      <section className="conversation" aria-labelledby="conversation-title">
        <div className="conversation-heading">
          <h2 id="conversation-title">{snapshot?.session.title ?? '空白对话'}</h2>
          <p role="status">{turn ? statusText(turn) : busy ? '其他对话正在执行' : '准备就绪'}</p>
        </div>
        <div className="messages">
          {turn ? <>
            <article className="message user"><h3>你的输入</h3><p>{turn.input}</p></article>
            {turn.answer !== null && <article className="message answer">
              <div className="answer-heading"><h3>旅行助手</h3><span>{turn.answer_source === 'model' ? '模型回答' : '应用说明'}</span></div>
              <p>{turn.answer}</p>
            </article>}
            {turn.status === 'running' && <p className="hint">正在查询，最终回答会在本轮结束后显示。关闭页面不会停止后台查询。</p>}
          </> : <div className="empty"><h3>从一条旅行需求开始</h3><p>可以查询地点、比较交通路线、寻找餐饮，或安排多日旅行行程。</p><p className="example">例如：查询杭州西湖的地址。</p></div>}
        </div>
        <form onSubmit={send}>
          <label htmlFor="travel-input">旅行需求</label>
          <textarea id="travel-input" name="travel-input" value={draft} onChange={event => setDraft(event.target.value)} required rows={4} placeholder="写下城市、日期和你想查询的内容" />
          <div className="form-bottom"><p className="hint">{sessionId ? '点击新建对话，开始下一次查询。' : '发送后保存到本机；空白对话不会产生记录。'}</p><button type="submit" disabled={busy || Boolean(sessionId) || !state.accepting}>{submitting ? '正在发送…' : '发送'}</button></div>
          <p className="error" role="alert">{error}</p>
          {!state.accepting && <p className="error">服务无法保存或正在退出，请检查后重启。</p>}
        </form>
      </section>
      <aside><h2>真实查询，保留依据</h2><p>根据你的旅行条件查询地点、交通、餐饮与天气。</p><p>最终文字会注明来自模型还是应用说明，并显示实际完成、失败或终止状态。</p><p className="hint">请提供城市或明确地点，日期和偏好可以帮助安排旅行行程。未查询或缺失的信息仍待核实。</p></aside>
    </main>
  </div>;
}

createRoot(document.getElementById('root')!).render(<App />);
