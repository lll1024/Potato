import { useEffect, useState, type FormEvent } from 'react';

type Session = {
  session_id: string; title: string; updated_at: string; status: string;
  reason: string | null; tool_error_count: number;
};
type Page = { sessions: Session[]; next_cursor: string | null };

async function historyApi<T>(path: string, options?: RequestInit): Promise<T> {
  const response = await fetch(path, options);
  const body = await response.json();
  if (!response.ok) throw new Error(typeof body.detail === 'string' ? body.detail : body.detail?.message ?? '历史操作未成功，请重试。');
  if (body.schema_version !== 1) throw new Error('数据版本不兼容，请更新页面和服务。');
  return body;
}

export function useConversationDraft(sessionId: string | null) {
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const key = sessionId ?? 'blank';
  return {
    draft: drafts[key] ?? '',
    setDraft: (text: string) => setDrafts(current => ({...current, [key]: text})),
    clearSubmittedDraft: (id: string | null, input: string) => setDrafts(current => {
      const origin = id ?? 'blank';
      return current[origin] === input ? {...current, [origin]: ''} : current;
    }),
    removeDraft: (id: string) => setDrafts(current => {
      const remaining = {...current}; delete remaining[id]; return remaining;
    }),
  };
}

function statusLabel(session: Session) {
  if (session.status === 'running') return '正在执行';
  if (session.status === 'stopping') return '正在停止';
  if (session.status === 'completed') return session.tool_error_count ? '完成，含工具错误' : '已完成';
  return session.status === 'failed' ? '失败，可继续' : '终止，可继续';
}

export function HistorySidebar({sessionId, activeTurnId, onSelect, onDelete}: {
  sessionId: string | null; activeTurnId: string | null;
  onSelect: (id: string) => void; onDelete: (id: string) => void;
}) {
  const [sessions, setSessions] = useState<Session[]>([]);
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [editing, setEditing] = useState<string | null>(null);
  const [title, setTitle] = useState('');
  const [error, setError] = useState('');
  const [working, setWorking] = useState(false);
  const [revision, setRevision] = useState(0);

  useEffect(() => {
    let cancelled = false;
    historyApi<Page>('/api/sessions?limit=20').then(page => {
      if (!cancelled) { setSessions(page.sessions); setNextCursor(page.next_cursor); }
    }).catch(cause => { if (!cancelled) setError(cause instanceof Error ? cause.message : '无法读取历史。'); });
    return () => { cancelled = true; };
  }, [activeTurnId, revision]);

  async function more() {
    setWorking(true); setError('');
    try {
      const page = await historyApi<Page>(`/api/sessions?limit=20&cursor=${encodeURIComponent(nextCursor ?? '')}`);
      setSessions(current => [...current, ...page.sessions.filter(item => !current.some(old => old.session_id === item.session_id))]);
      setNextCursor(page.next_cursor);
    } catch (cause) { setError(cause instanceof Error ? cause.message : '无法读取更多历史。'); }
    finally { setWorking(false); }
  }

  async function rename(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); setWorking(true); setError('');
    try {
      await historyApi(`/api/sessions/${editing}`, {method: 'PATCH', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({title})});
      setEditing(null); setRevision(current => current + 1);
    } catch (cause) { setError(cause instanceof Error ? cause.message : '重命名失败。'); }
    finally { setWorking(false); }
  }

  async function remove(session: Session) {
    if (!window.confirm(`删除“${session.title}”？此会话的对话、上下文和执行轨迹将一并删除。`)) return;
    setWorking(true); setError('');
    try {
      await historyApi(`/api/sessions/${session.session_id}`, {method: 'DELETE'});
      if (editing === session.session_id) setEditing(null);
      onDelete(session.session_id); setRevision(current => current + 1);
    } catch (cause) { setError(cause instanceof Error ? cause.message : '删除失败。'); }
    finally { setWorking(false); }
  }

  return <aside className="history" aria-labelledby="history-title">
    <h2 id="history-title">历史会话</h2>
    <p className="hint">按最近对话活动排序。回看不会发起查询。</p>
    <ul className="history-list">{sessions.map(session => <li key={session.session_id}>
      <button className="history-select" aria-current={sessionId === session.session_id ? 'true' : undefined} onClick={() => onSelect(session.session_id)}>
        <strong>{session.title}</strong>
        <time dateTime={session.updated_at}>{new Date(session.updated_at).toLocaleString('zh-CN')}</time>
        <span>{statusLabel(session)}</span>
      </button>
      <div className="history-actions">
        <button disabled={working} aria-label={`重命名“${session.title}”`} onClick={() => {setEditing(session.session_id); setTitle(session.title);}}>重命名</button>
        <button disabled={working || ['running', 'stopping'].includes(session.status)} aria-label={`删除“${session.title}”`} onClick={() => void remove(session)}>删除</button>
      </div>
      {editing === session.session_id && <form className="rename-form" onSubmit={rename}>
        <label htmlFor="session-title">会话标题</label>
        <input id="session-title" name="session-title" value={title} onChange={event => setTitle(event.target.value)} required maxLength={120} autoFocus />
        <div className="history-actions"><button type="submit" disabled={working}>保存标题</button><button type="button" onClick={() => setEditing(null)}>取消</button></div>
      </form>}
    </li>)}</ul>
    {!sessions.length && <p className="hint">尚无已发送的会话。</p>}
    {nextCursor !== null && <button disabled={working} onClick={() => void more()}>加载更多会话</button>}
    <p className="error" role="alert">{error}</p>
  </aside>;
}
