import { useEffect, useRef, useState, type FormEvent } from 'react';
import { TraceView, type TraceLocation } from './TraceView';
import { useReadingFollow } from './useReadingFollow';
import { ReadingScroll } from './ReadingScroll';
import { HistorySidebar, useConversationDraft } from './HistorySidebar';
import { ComposerInput } from './ComposerInput';
import { ConversationRounds, type Turn } from './ConversationRounds';
import type { RoundActivity } from './RoundProgress';
import { useSubmission } from './useSubmission';
import { useServiceEvents } from './useServiceEvents';
import { useSessionPage } from './useSessionPage';
import { requestJson } from '../api';
import { reasonLabels } from './turnReasons';
import { Icon } from '../Icon';
import type { ComponentType } from 'react';
import { isExceptional } from './ExceptionOutcome';

type Snapshot = RoundActivity & { schema_version: number; session: { title: string; updated_at?: string }; turns: Turn[]; next_before: number | null; total_turns: number };

function api<T>(path: string, options?: RequestInit): Promise<T> {
  return requestJson<T>(path, '请求未成功，请检查输入或本机服务。', options);
}

function statusText(turn: Turn): string {
  if (turn.status === 'running') return '正在执行';
  if (turn.status === 'completed') return turn.tool_error_count ? '完成，含工具错误' : '已完成';
  if (turn.reason === 'user_stop') return '已停止';
  return `${turn.status === 'failed' ? '失败' : '终止'}：${reasonLabels[turn.reason ?? ''] ?? turn.reason ?? '原因未知'}`;
}

export function SessionsApp({PreferencesView}: {PreferencesView: ComponentType<{onBack: () => void}>}) {
  const [sidebarOpen, setSidebarOpen] = useState(() => !window.matchMedia('(max-width: 760px)').matches);
  const navigationToggle = useRef<HTMLButtonElement>(null);
  const sidebarToggle = useRef<HTMLButtonElement>(null);
  const [view, setView] = useState<'conversation' | 'trace' | 'preferences'>('conversation');
  useEffect(() => {
    if (view !== 'preferences') return;
    const mobile = window.matchMedia('(max-width: 760px)');
    const keepPreferencesVisible = () => {if (mobile.matches) setSidebarOpen(false);};
    keepPreferencesVisible();
    mobile.addEventListener('change', keepPreferencesVisible);
    return () => mobile.removeEventListener('change', keepPreferencesVisible);
  }, [view]);
  const [traceLocation,setTraceLocation]=useState<TraceLocation | null>(null);
  const [sessionId, setSessionId] = useState<string | null>(null);
  const {draft, setDraft, removeDraft, clearSubmittedDraft, clearDrafts} = useConversationDraft(sessionId);
  const {state, revision: serviceRevision, datasetRevision, deletedSession, connectionError, recoveryNotice, refresh: refreshService} = useServiceEvents();
  const {snapshot, error, setError, loadingEarlier, loadEarlier: readEarlier, invalidate} = useSessionPage<Snapshot>(sessionId, serviceRevision, 'include_messages=false', datasetRevision, true);

  const submission = useSubmission((accepted, submitted) => {
    if (!submitted.preserve_draft) clearSubmittedDraft(submitted.session_id, submitted.input);
    if (sessionId === submitted.session_id && accepted.session_id !== sessionId) invalidate();
    setSessionId(current => current === submitted.session_id ? accepted.session_id : current);
    void refreshService().catch(() => setError('提交已接受，但暂时无法核对服务状态。'));
  });

  useEffect(() => {
    if (sessionId && error === '会话不存在。') deletedConversation(sessionId);
  }, [sessionId, error]);

  async function send(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (state.active_turn_id || !state.accepting || submission.pending || !draft.trim()) return;
    setError('');
    await submission.send(draft, sessionId);
  }

  async function stopTurn(turn: Turn) {
    try {
      await api(`/api/turns/${turn.turn_id}/stop`, {method: 'POST'});
      await refreshService();
    } catch (cause) {setError(cause instanceof Error ? cause.message : '停止请求未成功，请重试。');}
  }

  function selectConversation(id: string | null) {
    setSessionId(id); setTraceLocation(null); setError('');
    if (view === 'preferences') setView('conversation');
  }
  function closeMobileNavigation() {
    if (window.matchMedia('(max-width: 760px)').matches) {
      setSidebarOpen(false);
      requestAnimationFrame(() => navigationToggle.current?.focus());
    }
  }
  function newConversation() { selectConversation(null); setView('conversation'); closeMobileNavigation(); }
  function deletedConversation(id: string) {
    removeDraft(id);
    invalidate(id);
    if (sessionId === id) selectConversation(null);
  }
  useEffect(() => {if (deletedSession) deletedConversation(deletedSession);}, [deletedSession]);
  useEffect(() => {
    if (datasetRevision) {submission.reset(); clearDrafts(); selectConversation(null);}
  }, [datasetRevision]);
  async function loadEarlier() {
    conversationFollow.pause();
    await readEarlier();
  }
  const busy = Boolean(state.active_turn_id) || submission.submitting;
  const turns = snapshot?.turns ?? [];
  const turn = snapshot?.turns.at(-1);
  const storageTurnId = state.storage_error && state.unsaved_fact?.turn_id ? state.unsaved_fact.turn_id : undefined;
  const retryBlocked = submission.pending ? '这次提交尚待核对，请先核对原提交。'
    : busy ? (state.stopping ? '正在停止，仍占用执行名额。' : '全局忙，请等待当前请求结束。')
    : !state.accepting ? (state.storage_error ?? '本机暂不可接收输入，请等待服务可用后再尝试。')
    : state.map_paused ? '地图查询仍暂停，暂不能重新查询；可编辑需求，讨论已有资料。' : '';
  async function retryTurn(failed: Turn) {
    if (retryBlocked || !sessionId || turn?.turn_id !== failed.turn_id || !isExceptional(failed)) return;
    setError('');
    await submission.send(failed.input, sessionId, true);
  }
  const conversationFollow=useReadingFollow(sessionId, turn ? JSON.stringify([turn.turn_id,turn.status,turn.answer,snapshot?.events?.at(-1)?.event_id]) : '');
  const activity: RoundActivity = snapshot ?? {};
  function showTurnTrace(turn: Turn) {
    conversationFollow.pause();
    setTraceLocation({turn_id:turn.turn_id,turn_ordinal:turn.ordinal,object_type:'turn',object_id:turn.turn_id,payload_id:null,field:'turn_input',query:''});
    setView('trace');
  }
  const canLoadEarlier = Boolean(snapshot?.next_before);
  function toggleNavigation() {
    setSidebarOpen(current => !current);
    requestAnimationFrame(() => sidebarOpen ? navigationToggle.current?.focus() : sidebarToggle.current?.focus());
  }
  return <div className={`app ${sidebarOpen ? 'navigation-open' : 'navigation-closed'}`}>
    <aside className="sidebar" id="session-navigation" aria-label="助手会话导航" onKeyDown={event => {if (event.key === 'Escape') {setSidebarOpen(false); requestAnimationFrame(() => navigationToggle.current?.focus());}}}>
      <div className="sidebar-top"><div className="brand"><Icon name="compass" size={22} /><h1>Potato</h1></div><button ref={sidebarToggle} className="icon-button" type="button" aria-label="收起会话导航" onClick={toggleNavigation}><Icon name="panel" /></button></div>
      <button className="new-conversation" onClick={newConversation}><Icon name="plus" />新会话</button>
      <HistorySidebar key={datasetRevision} sessionId={sessionId} activeTurnId={state.active_turn_id} activeSessionId={state.active_session_id} stopping={state.stopping}
        viewingResults={view === 'conversation' && Boolean(snapshot) && turn?.status !== 'running'} viewedUpdatedAt={snapshot?.session.updated_at} resetKey={datasetRevision}
        refreshKey={serviceRevision} onSelect={id => {selectConversation(id); closeMobileNavigation();}} onDelete={deletedConversation} />
    </aside>
    <main className="workspace">
      <header className="workspace-header">
        <div className="workspace-title"><button ref={navigationToggle} className="icon-button" type="button" aria-label={sidebarOpen ? '收起会话导航' : '打开会话导航'} aria-expanded={sidebarOpen} aria-controls="session-navigation" onClick={toggleNavigation}><Icon name="panel" /></button><h2 id="conversation-title">{snapshot?.session.title ?? (sessionId ? '正在读取会话…' : '新会话')}</h2></div>
        <button className="preferences-entry" onClick={() => setView('preferences')} aria-pressed={view === 'preferences'}>旅行者偏好</button>
      </header>
      {view === 'preferences' ? <PreferencesView onBack={() => setView('conversation')} /> : <section className="conversation" aria-labelledby="conversation-title">
        <nav className="view-switch" aria-label="会话视图"><button aria-pressed={view === 'conversation'} onClick={() => setView('conversation')}>对话</button><button aria-pressed={view === 'trace'} onClick={() => setView('trace')}>轨迹</button><p role="status" className="view-status">{turn ? turn.turn_id === storageTurnId ? '存储故障，结果未完整保存' : statusText(turn) : busy ? '其他对话正在执行' : '旅行规划与查询'}</p></nav>
        {view === 'trace' ? <TraceView sessionId={sessionId} refreshKey={serviceRevision} datasetRevision={datasetRevision} location={traceLocation}
          onStop={turnId => {const turn = turns.find(item => item.turn_id === turnId); if (turn) void stopTurn(turn);}}
          stopping={state.stopping} disconnected={Boolean(connectionError)} /> : <>
          <div className="reading-follow conversation-follow"><p role="status">{conversationFollow.hasNew ? '有新内容' : conversationFollow.paused ? '已暂停跟随，正在回看' : '停留底部时跟随新回答'}</p>{conversationFollow.paused && <button type="button" onClick={conversationFollow.resume}>回到最新</button>}</div>
          <ReadingScroll className="messages" identity={sessionId} paused={conversationFollow.paused} scrollRef={conversationFollow.ref} onScroll={conversationFollow.onScroll}>
          {sessionId && !snapshot ? <div className="empty" role="status"><h3>正在读取会话…</h3><p>正在恢复已保存的对话与执行记录。</p></div> : turn ? <>
            {canLoadEarlier && <button className="load-earlier" disabled={loadingEarlier} onClick={() => void loadEarlier()}>加载更早对话</button>}
            <ConversationRounds turns={turns} statusText={statusText} onTrace={showTurnTrace} activity={activity} onStop={stopTurn}
              onRetry={failed => void retryTurn(failed)} retryTurnId={turn && (isExceptional(turn) || turn.turn_id === storageTurnId) ? turn.turn_id : undefined}
              storageTurnId={storageTurnId}
              retryBlocked={retryBlocked}
              stoppingTurnId={state.stopping ? state.active_turn_id : null} disconnected={Boolean(connectionError)} />
          </> : <div className="empty"><div className="empty-mark"><Icon name="compass" size={36} /></div><h3>下一站，去哪里？</h3></div>}
        </ReadingScroll></>}
        <div className="composer-area"><form className="composer" onSubmit={send}>
          <label className="visually-hidden" htmlFor="travel-input">旅行需求</label>
          <ComposerInput value={draft} onChange={setDraft} canSend={!busy && !submission.pending && state.accepting} />
          <div className="form-bottom"><p className="hint">{sessionId ? '继续此会话 · 切换会话保留草稿' : '发送后保存在本机'}</p><button className="send-button" type="submit" disabled={busy || Boolean(submission.pending) || !state.accepting || !draft.trim()} aria-label={submission.submitting ? '正在发送' : '发送旅行需求'}><Icon name="send" size={20} /><span>{submission.submitting ? '正在发送…' : '发送'}</span></button></div>
          <p role="status" className="hint">{submission.submitting ? '正在发送你的消息…' : submission.pending ? '这次提交尚待核对；编辑草稿不会改变原提交，刷新页面不会自动重发。' : ''}</p>
          {submission.pending && <div className="submission-actions">
            <button type="button" disabled={submission.submitting} onClick={() => void submission.check()}>核对提交</button>
            <button type="button" disabled={submission.submitting} onClick={() => void submission.retry()}>安全重试同一提交</button>
          </div>}
          <p className="error" role="alert">{submission.error || error}</p>
          <p className="hint" role="status">{connectionError}</p>
          {recoveryNotice && <p className="hint" role="status">{recoveryNotice}</p>}
          {!state.accepting && <p className="error" role="alert">{state.storage_error ?? (connectionError ? '请等待连接恢复后再发送。' : '本机服务暂不可用，请检查后重启。')}</p>}
        </form></div>
      </section>}
    </main>
  </div>;
}
