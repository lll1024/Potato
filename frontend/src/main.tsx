import { useEffect, useState, type FormEvent } from 'react';
import { createRoot } from 'react-dom/client';
import './style.css';
import { TraceView, type TraceLocation } from './TraceView';
import { useReadingFollow } from './useReadingFollow';
import { ReadingScroll } from './ReadingScroll';
import { HistorySidebar, useConversationDraft } from './HistorySidebar';
import { ComposerInput } from './ComposerInput';
import { ConversationRounds, type Turn } from './ConversationRounds';
import type { RoundActivity } from './RoundProgress';
import { useSubmission } from './useSubmission';
import { useServiceEvents } from './useServiceEvents';
import { requestJson } from './api';
import { reasonLabels } from './turnReasons';

type Snapshot = RoundActivity & { schema_version: number; session: { title: string; updated_at?: string }; turns: Turn[]; next_before: number | null; total_turns: number };

function api<T>(path: string, options?: RequestInit): Promise<T> {
  return requestJson<T>(path, '请求未成功，请检查输入或本机服务。', options);
}

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
  const [traceLocation,setTraceLocation]=useState<TraceLocation | null>(null);
  const [sessionId, setSessionId] = useState<string | null>(null);
  const {draft, setDraft, removeDraft, clearSubmittedDraft} = useConversationDraft(sessionId);
  const [earlierTurns, setEarlierTurns] = useState<Record<string, Turn[]>>({});
  const [earlierActivity, setEarlierActivity] = useState<Record<string, RoundActivity>>({});
  const [loadingEarlier, setLoadingEarlier] = useState(false);
  const [snapshot, setSnapshot] = useState<Snapshot | null>(null);
  const {state, revision: serviceRevision, datasetRevision, deletedSession, connectionError, recoveryNotice, refresh: refreshService} = useServiceEvents();
  const [error, setError] = useState('');
  const [stoppingId, setStoppingId] = useState<string | null>(null);

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
        const result = await api<Snapshot>(`/api/sessions/${sessionId}?include_messages=false`);
        if (!cancelled) setSnapshot(result);
      } catch (cause) {
        if (!cancelled && cause instanceof Error && cause.message === '会话不存在。') {deletedConversation(sessionId!); return;}
        if (!cancelled) setError(cause instanceof Error ? cause.message : '无法读取会话。');
      }
    }
    void refresh();
    const interval = window.setInterval(refresh, 500);
    return () => { cancelled = true; window.clearInterval(interval); };
  }, [sessionId, serviceRevision]);

  async function send(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (state.active_turn_id || !state.accepting || submission.pending || !draft.trim()) return;
    setError('');
    await submission.send(draft, sessionId);
  }

  async function stopTurn(turnId: string) {
    setStoppingId(turnId); setError('');
    try {
      await api(`/api/turns/${encodeURIComponent(turnId)}/stop`, {method: 'POST'});
      await refreshService();
    } catch (cause) { setError(cause instanceof Error ? cause.message : '停止请求未能核对，可重试同一轮次。'); }
    finally { setStoppingId(null); }
  }

  function selectConversation(id: string | null) {
    setSessionId(id); setSnapshot(null); setTraceLocation(null); setError('');
  }
  function newConversation() { selectConversation(null); }
  function deletedConversation(id: string) {
    removeDraft(id);
    setEarlierTurns(current => { const remaining = {...current}; delete remaining[id]; return remaining; });
    setEarlierActivity(current => { const remaining = {...current}; delete remaining[id]; return remaining; });
    if (sessionId === id) selectConversation(null);
  }
  useEffect(() => {if (deletedSession) deletedConversation(deletedSession);}, [deletedSession]);
  useEffect(() => {
    if (datasetRevision) {setEarlierTurns({}); setEarlierActivity({}); selectConversation(null);}
  }, [datasetRevision]);
  async function loadEarlier() {
    conversationFollow.pause();
    if (!sessionId || !snapshot) return;
    const id = sessionId;
    const before = earlierTurns[id]?.[0]?.ordinal ?? snapshot.next_before;
    if (!before) return;
    setLoadingEarlier(true);
    try {
      const page = await api<Snapshot>(`/api/sessions/${id}?include_messages=false&before=${before}`);
      setEarlierTurns(current => ({...current, [id]: [...page.turns, ...(current[id] ?? [])]}));
      setEarlierActivity(current => ({...current, [id]: {
        requests: [...(page.requests ?? []), ...(current[id]?.requests ?? [])],
        tool_calls: [...(page.tool_calls ?? []), ...(current[id]?.tool_calls ?? [])],
        events: [...(page.events ?? []), ...(current[id]?.events ?? [])],
      }}));
    } catch (cause) { setError(cause instanceof Error ? cause.message : '无法读取更早对话。'); }
    finally { setLoadingEarlier(false); }
  }
  const busy = Boolean(state.active_turn_id) || submission.submitting;
  const turns = [...(sessionId ? earlierTurns[sessionId] ?? [] : []), ...(snapshot?.turns ?? [])]
    .filter((turn, index, all) => all.findIndex(item => item.turn_id === turn.turn_id) === index);
  const turn = snapshot?.turns.at(-1);
  const conversationFollow=useReadingFollow(sessionId, turn ? JSON.stringify([turn.turn_id,turn.status,turn.answer,snapshot?.events?.at(-1)?.event_id]) : '');
  const activity: RoundActivity = {
    requests: [...(sessionId ? earlierActivity[sessionId]?.requests ?? [] : []), ...(snapshot?.requests ?? [])],
    tool_calls: [...(sessionId ? earlierActivity[sessionId]?.tool_calls ?? [] : []), ...(snapshot?.tool_calls ?? [])],
    events: [...(sessionId ? earlierActivity[sessionId]?.events ?? [] : []), ...(snapshot?.events ?? [])],
  };
  function showTurnTrace(turn: Turn) {
    conversationFollow.pause();
    setTraceLocation({turn_id:turn.turn_id,turn_ordinal:turn.ordinal,object_type:'turn',object_id:turn.turn_id,payload_id:null,field:'turn_input',query:''});
    setView('trace');
  }
  const canLoadEarlier = Boolean(snapshot && turns[0]?.ordinal > 1);
  return <div className="app">
    <header>
      <div><p className="eyebrow">本机调试</p><h1>旅行助手</h1></div>
      <div className="global-status">
        <p role="status">{state.active_turn_id ? `${state.stopping ? '正在停止，仍占用执行名额' : '全局忙，正在执行'} · ${state.active_session_id === sessionId ? '当前会话' : '其他会话'}` : state.accepting ? '全局准备就绪' : '本机服务暂不可用'}</p>
        {state.active_turn_id && <div>
          <button type="button" disabled={state.stopping || stoppingId === state.active_turn_id} onClick={() => void stopTurn(state.active_turn_id!)}>{state.stopping ? '正在停止…' : stoppingId === state.active_turn_id ? '正在请求停止…' : '停止执行中的轮次'}</button>
          <p className="hint">停止会阻止后续查询；已发起的调用会等待真实返回，期间仍占用执行名额。</p>
        </div>}
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
        {view === 'trace' ? <TraceView sessionId={sessionId} refreshKey={serviceRevision} location={traceLocation} /> : <>
          <div className="reading-follow conversation-follow"><p role="status">{conversationFollow.hasNew ? '有新内容' : conversationFollow.paused ? '已暂停跟随，正在回看' : '停留底部时跟随新回答'}</p>{conversationFollow.paused && <button type="button" onClick={conversationFollow.resume}>回到最新</button>}</div>
          <ReadingScroll className="messages" identity={sessionId} paused={conversationFollow.paused} scrollRef={conversationFollow.ref} onScroll={conversationFollow.onScroll}>
          {turn ? <>
            {canLoadEarlier && <button className="load-earlier" disabled={loadingEarlier} onClick={() => void loadEarlier()}>加载更早对话</button>}
            <ConversationRounds turns={turns} statusText={statusText} onTrace={showTurnTrace} activity={activity}
              stoppingTurnId={state.stopping ? state.active_turn_id : stoppingId} disconnected={Boolean(connectionError)} onRead={conversationFollow.pause} />
          </> : <div className="empty"><h3>从一条旅行需求开始</h3><p>可以查询地点、比较交通路线、寻找餐饮，或安排多日旅行行程。</p><p className="example">例如：查询杭州西湖的地址。</p></div>}
        </ReadingScroll></>}
        <form onSubmit={send}>
          <label htmlFor="travel-input">旅行需求</label>
          <ComposerInput value={draft} onChange={setDraft} canSend={!busy && !submission.pending && state.accepting} />
          <div className="form-bottom"><p className="hint">{sessionId ? '继续提问会使用此会话的完整上下文。切换会话保留当前草稿。' : '发送后保存到本机；空白对话不会产生记录。'}</p><button type="submit" disabled={busy || Boolean(submission.pending) || !state.accepting || !draft.trim()}>{submission.submitting ? '正在发送…' : '发送'}</button></div>
          <p role="status" className="hint">{submission.submitting ? '正在发送你的消息…' : submission.pending ? '这次提交尚待核对；编辑草稿不会改变原提交，刷新页面不会自动重发。' : ''}</p>
          {submission.pending && <div className="submission-actions">
            <button type="button" disabled={submission.submitting} onClick={() => void submission.check()}>核对提交</button>
            <button type="button" disabled={submission.submitting} onClick={() => void submission.retry()}>安全重试同一提交</button>
          </div>}
          <p className="error" role="alert">{submission.error || error}</p>
          <p className="hint" role="status">{connectionError}</p>
          {recoveryNotice && <p className="hint" role="status">{recoveryNotice}</p>}
          {!state.accepting && <p className="error" role="alert">{state.storage_error ?? (connectionError ? '请等待连接恢复后再发送。' : '本机服务暂不可用，请检查后重启。')}</p>}
        </form>
      </section>
      <HistorySidebar sessionId={sessionId} activeTurnId={state.active_turn_id} activeSessionId={state.active_session_id} stopping={state.stopping}
        viewingResults={view === 'conversation' && Boolean(snapshot) && turn?.status !== 'running'} viewedUpdatedAt={snapshot?.session.updated_at} resetKey={datasetRevision}
        refreshKey={serviceRevision} onSelect={selectConversation} onDelete={deletedConversation} />
    </main>
  </div>;
}

createRoot(document.getElementById('root')!).render(<App />);
