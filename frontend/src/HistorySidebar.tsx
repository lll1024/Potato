import { useEffect, useRef, useState, type FormEvent } from 'react';
import { requestJson } from './api';
import './conversation-controls.css';

type Session = {
  session_id: string; title: string; updated_at: string; status: string;
  reason: string | null; tool_error_count: number;
};
type Page = { sessions: Session[]; next_cursor: string | null };
type Indicators = Record<string, {status: 'running' | 'unread'; updated_at: string | null}>;
const indicatorKey = 'travel.conversation-activity.v2';

function savedIndicators(): Indicators {
  try {
    const saved: unknown = JSON.parse(localStorage.getItem(indicatorKey) ?? '{}');
    if (!saved || typeof saved !== 'object' || Array.isArray(saved)) return {};
    return Object.fromEntries(Object.entries(saved).filter(([, value]) => value && typeof value === 'object'
      && (value.status === 'running' || value.status === 'unread') && (value.updated_at === null || typeof value.updated_at === 'string'))) as Indicators;
  } catch { return {}; }
}

function historyApi<T>(path: string, options?: RequestInit): Promise<T> {
  return requestJson<T>(path, '历史操作未成功，请重试。', options);
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

export function HistorySidebar({sessionId, activeTurnId, activeSessionId, stopping, viewingResults = true, viewedUpdatedAt, resetKey, refreshKey, onSelect, onDelete}: {
  sessionId: string | null; activeTurnId: string | null;
  activeSessionId?: string | null; stopping?: boolean;
  viewingResults?: boolean; viewedUpdatedAt?: string; resetKey?: number;
  refreshKey?: number;
  onSelect: (id: string) => void; onDelete: (id: string) => void;
}) {
  const [sessions, setSessions] = useState<Session[]>([]);
  const [indicators, setIndicators] = useState(savedIndicators);
  const [visible, setVisible] = useState(() => document.visibilityState === 'visible');
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [editing, setEditing] = useState<string | null>(null);
  const [title, setTitle] = useState('');
  const [error, setError] = useState('');
  const [working, setWorking] = useState(false);
  const [revision, setRevision] = useState(0);
  const [pendingDelete, setPendingDelete] = useState<Session | null>(null);
  const [menuId, setMenuId] = useState<string | null>(null);
  const [deleteError, setDeleteError] = useState('');
  const menuPanel = useRef<HTMLDivElement>(null);
  const menuTrigger = useRef<HTMLButtonElement | null>(null);
  const deleteDialog = useRef<HTMLDialogElement>(null);
  const deleteTrigger = useRef<HTMLButtonElement | null>(null);
  const historyRegion = useRef<HTMLElement>(null);
  const visibleCount = useRef(20);
  const menuSession = sessions.find(session => session.session_id === menuId);

  useEffect(() => {
    const change = () => setVisible(document.visibilityState === 'visible');
    document.addEventListener('visibilitychange', change);
    return () => document.removeEventListener('visibilitychange', change);
  }, []);
  useEffect(() => {
    if (resetKey) setIndicators({});
  }, [resetKey]);
  useEffect(() => {
    setIndicators(current => {
      const next = {...current};
      if (activeSessionId && next[activeSessionId]?.status !== 'running') {
        next[activeSessionId] = {status: 'running', updated_at: sessions.find(item => item.session_id === activeSessionId)?.updated_at ?? null};
      }
      for (const session of sessions) {
        const id = session.session_id;
        if (session.status === 'running') next[id] = {status: 'running', updated_at: session.updated_at};
        else if (id === activeSessionId) continue;
        else if (session.status === 'completed' && next[id]?.status === 'running' && next[id].updated_at !== session.updated_at) {
          next[id] = {status: 'unread', updated_at: session.updated_at};
        }
        else if (session.status !== 'completed') delete next[id];
        if (id === sessionId && id !== activeSessionId && viewingResults && visible && session.status === 'completed'
          && next[id]?.status === 'unread' && next[id].updated_at === viewedUpdatedAt) delete next[id];
      }
      return JSON.stringify(next) === JSON.stringify(current) ? current : next;
    });
  }, [sessions, sessionId, activeSessionId, viewingResults, viewedUpdatedAt, visible]);
  useEffect(() => {
    try {localStorage.setItem(indicatorKey, JSON.stringify(indicators));} catch { /* 本机存储不可用时仍保留当前页面的提示。 */ }
  }, [indicators]);

  function closeMenu(restoreFocus = false) {
    menuPanel.current?.hidePopover();
    setMenuId(null);
    if (restoreFocus) menuTrigger.current?.focus();
  }

  function finishEditing() {
    setEditing(null);
    window.requestAnimationFrame(() => menuTrigger.current?.focus());
  }

  useEffect(() => {
    const panel = menuPanel.current;
    const trigger = menuTrigger.current;
    if (!menuSession || !panel || !trigger) {panel?.hidePopover(); return;}
    panel.showPopover();
    function position() {
      const rect = trigger!.getBoundingClientRect();
      panel!.style.left = `${Math.max(8, Math.min(rect.right - panel!.offsetWidth, window.innerWidth - panel!.offsetWidth - 8))}px`;
      panel!.style.top = `${Math.max(8, Math.min(rect.bottom + 4, window.innerHeight - panel!.offsetHeight - 8))}px`;
    }
    position();
    panel.querySelector<HTMLButtonElement>('button:not(:disabled)')?.focus();
    window.addEventListener('resize', position);
    window.addEventListener('scroll', position, true);
    return () => {
      window.removeEventListener('resize', position);
      window.removeEventListener('scroll', position, true);
    };
  }, [menuId, Boolean(menuSession)]);

  useEffect(() => {
    if (pendingDelete && !deleteDialog.current?.open) deleteDialog.current?.showModal();
  }, [pendingDelete]);

  function closedDeleteDialog() {
    setPendingDelete(null);
    setDeleteError('');
    window.requestAnimationFrame(() => {
      if (menuPanel.current?.matches(':popover-open')) return;
      const trigger = deleteTrigger.current;
      if (trigger?.isConnected && !trigger.disabled) trigger.focus();
      else historyRegion.current?.focus();
    });
  }

  useEffect(() => {
    let cancelled = false;
    async function refresh() {
      let page=await historyApi<Page>('/api/sessions?limit=20');
      const rows=[...page.sessions];
      while (page.next_cursor && rows.length<visibleCount.current) {
        page=await historyApi<Page>(`/api/sessions?limit=20&cursor=${encodeURIComponent(page.next_cursor)}`);
        rows.push(...page.sessions);
      }
      if (!cancelled) {setSessions(rows); setNextCursor(page.next_cursor);}
    }
    void refresh().catch(cause => { if (!cancelled) setError(cause instanceof Error ? cause.message : '无法读取历史。'); });
    return () => { cancelled = true; };
  }, [activeTurnId, revision, refreshKey]);

  async function more() {
    setWorking(true); setError('');
    try {
      const page = await historyApi<Page>(`/api/sessions?limit=20&cursor=${encodeURIComponent(nextCursor ?? '')}`);
      visibleCount.current+=page.sessions.length;
      setSessions(current => [...current, ...page.sessions.filter(item => !current.some(old => old.session_id === item.session_id))]);
      setNextCursor(page.next_cursor);
    } catch (cause) { setError(cause instanceof Error ? cause.message : '无法读取更多历史。'); }
    finally { setWorking(false); }
  }

  async function rename(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); setWorking(true); setError('');
    try {
      await historyApi(`/api/sessions/${editing}`, {method: 'PATCH', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({title})});
      finishEditing(); setRevision(current => current + 1);
    } catch (cause) { setError(cause instanceof Error ? cause.message : '重命名失败。'); }
    finally { setWorking(false); }
  }

  async function remove() {
    if (!pendingDelete) return;
    const session = pendingDelete;
    setWorking(true); setDeleteError('');
    try {
      await historyApi(`/api/sessions/${session.session_id}`, {method: 'DELETE'});
      if (editing === session.session_id) setEditing(null);
      onDelete(session.session_id); setRevision(current => current + 1);
      setIndicators(current => {const next = {...current}; delete next[session.session_id]; return next;});
      deleteTrigger.current = null;
      deleteDialog.current?.close();
    } catch (cause) { setDeleteError(cause instanceof Error ? cause.message : '删除失败。'); }
    finally { setWorking(false); }
  }

  return <aside className="history" aria-label="历史会话" ref={historyRegion} tabIndex={-1}>
    <ul className="history-list">{sessions.map(session => <li className="history-item" key={session.session_id}>
      {editing !== session.session_id && <button className="history-select" aria-current={sessionId === session.session_id ? 'true' : undefined} onClick={() => onSelect(session.session_id)}>
        <strong>{(session.status === 'running' || session.session_id === activeSessionId)
          ? <span className="activity-spinner" role="img" aria-label="正在处理" />
          : indicators[session.session_id]?.status === 'unread' && <span className="unread-result-dot" role="img" aria-label="有未查看的结果" />}
          <span className="history-title">{session.title}</span></strong>
        <time dateTime={session.updated_at}>{new Date(session.updated_at).toLocaleString('zh-CN')}</time>
        <span>{statusLabel(session.session_id === activeSessionId ? {...session, status: stopping ? 'stopping' : 'running'} : session)}</span>
      </button>}
      <button className="history-menu-trigger" type="button" disabled={working} aria-label={`会话操作“${session.title}”`} aria-haspopup="menu" aria-expanded={menuId === session.session_id} aria-controls="history-menu"
        onClick={event => {menuTrigger.current = event.currentTarget; setMenuId(current => current === session.session_id ? null : session.session_id);}}
        onKeyDown={event => {if (['ArrowDown', 'ArrowUp'].includes(event.key)) {event.preventDefault(); menuTrigger.current = event.currentTarget; setMenuId(session.session_id);}}}>
        <svg width="18" height="18" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><circle cx="5" cy="12" r="1.6" /><circle cx="12" cy="12" r="1.6" /><circle cx="19" cy="12" r="1.6" /></svg>
      </button>
      {editing === session.session_id && <form className="rename-form" onSubmit={rename} onKeyDown={event => {if (event.key === 'Escape') {event.preventDefault(); event.stopPropagation(); if (!working) finishEditing();}}}>
        <label htmlFor="session-title">会话标题</label>
        <input id="session-title" name="session-title" value={title} onChange={event => setTitle(event.target.value)} required maxLength={120} disabled={working} autoFocus />
        <div className="history-actions"><button type="submit" disabled={working}>保存标题</button><button type="button" disabled={working} onClick={finishEditing}>取消</button></div>
      </form>}
    </li>)}</ul>
    <div id="history-menu" className="history-menu" ref={menuPanel} popover="auto" role="menu" aria-label="会话操作"
      onToggle={event => {if (event.newState === 'closed' && !event.currentTarget.matches(':popover-open')) setMenuId(null);}}
      onBlur={event => {if (!event.currentTarget.contains(event.relatedTarget)) closeMenu();}}
      onKeyDown={event => {
        if (event.key === 'Escape') {event.preventDefault(); event.stopPropagation(); closeMenu(true); return;}
        if (!['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key)) return;
        event.preventDefault();
        const items = [...event.currentTarget.querySelectorAll<HTMLButtonElement>('button:not(:disabled)')];
        const index = items.indexOf(document.activeElement as HTMLButtonElement);
        const next = event.key === 'Home' ? 0 : event.key === 'End' ? items.length - 1 : (index + (event.key === 'ArrowDown' ? 1 : -1) + items.length) % items.length;
        items[next]?.focus();
      }}>
      <button role="menuitem" type="button" disabled={working} onClick={() => {if (menuSession) {setEditing(menuSession.session_id); setTitle(menuSession.title);} closeMenu();}}>重命名</button>
      <button role="menuitem" type="button" disabled={working || !menuSession || menuSession.session_id === activeSessionId || ['running', 'stopping'].includes(menuSession.status)} onClick={() => {if (menuSession) {deleteTrigger.current = menuTrigger.current; setDeleteError(''); setPendingDelete(menuSession);} closeMenu();}}>删除会话</button>
    </div>
    {!sessions.length && <p className="hint">暂无已发送的会话</p>}
    {nextCursor !== null && <button disabled={working} onClick={() => void more()}>加载更多会话</button>}
    <p className="error" role="alert">{error}</p>
    <dialog ref={deleteDialog} className="delete-dialog" aria-labelledby="delete-dialog-title" aria-describedby="delete-dialog-description" onClose={closedDeleteDialog} onCancel={event => {if (working) event.preventDefault();}} onKeyDown={event => {if (event.key === 'Escape') event.stopPropagation();}}>
      <h2 id="delete-dialog-title">删除会话？</h2>
      <p id="delete-dialog-description">删除“{pendingDelete?.title}”后，此会话的对话、上下文和执行轨迹将一并删除。</p>
      <p className="error" role="alert">{deleteError}</p>
      <div className="dialog-actions">
        <button type="button" autoFocus disabled={working} onClick={() => deleteDialog.current?.close()}>取消</button>
        <button type="button" disabled={working} onClick={() => void remove()}>{working ? '正在删除…' : '确认删除'}</button>
      </div>
    </dialog>
  </aside>;
}
