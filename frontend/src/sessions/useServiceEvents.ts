import { useCallback, useEffect, useRef, useState } from 'react';

export type AnswerPreview = {
  instance_id: string; stream_id: string; session_id: string; turn_id: string;
  attempt_id: string; revision: number; text: string; status: 'generating' | 'generated' | 'invalidated' | 'retrying';
  delivery?: 'stream' | 'complete';
};
export type ProgressEntry = {id: string; attempt_id: string; kind: 'model' | 'tool'; text: string; status: string; delivery?: 'stream' | 'complete'};
export type ProgressPreview = {instance_id: string; stream_id: string; session_id: string; turn_id: string;
  revision: number; entries: ProgressEntry[]};
export type ServiceState = {
  progress_revision?: number; progress_preview?: ProgressPreview | null;
  instance_id?: string; answer_revision?: number; answer_preview?: AnswerPreview | null;
  active_turn_id: string | null; active_session_id: string | null; accepting: boolean;
  stopping: boolean; map_paused: boolean; map_pause_reason: string | null;
  service_status: 'available' | 'unavailable'; storage_error?: string | null;
  unsaved_fact?: {turn_id: string; kind: string; message: string} | null;
};
type ServiceSnapshot = ServiceState & { schema_version: number; state?: ServiceState; cursor: number; stream_id: string };

export function useServiceEvents() {
  const [state, setState] = useState<ServiceState>({active_turn_id:null,active_session_id:null,
    accepting:false,stopping:false,map_paused:false,map_pause_reason:null,service_status:'unavailable'});
  // 仅持久完成通知授权的短暂显示交接；不回填规范 answer_preview。
  const [answerHandoff, setAnswerHandoff] = useState<AnswerPreview | null>(null);
  const [progressHandoff, setProgressHandoff] = useState<ProgressPreview | null>(null);
  const handoffProgressRevision = useRef(-1);
  const handoff = useRef<AnswerPreview | null>(null);
  const clearAnswerHandoff = useCallback(() => {handoff.current = null; setAnswerHandoff(null); setProgressHandoff(null);}, []);
  const [revision, setRevision] = useState(0);
  const [contentRecoveryRevision, setContentRecoveryRevision] = useState(0);
  const [datasetRevision, setDatasetRevision] = useState(0);
  const [deletedSession, setDeletedSession] = useState<string | null>(null);
  const [connectionError, setConnectionError] = useState('');
  const [recoveryNotice, setRecoveryNotice] = useState('');
  const stateRead = useRef(0);
  const currentSnapshot = useRef<ServiceSnapshot | null>(null);
  // 状态可先宣布下一内容次序；此水位只防状态回退，不代表收到正文／进展。
  const announcedState = useRef<{instance_id?: string; stream_id: string; answer: number; progress: number} | null>(null);
  const rememberState = useCallback((value: ServiceSnapshot) => {
    const old = announcedState.current;
    const same = old?.instance_id === value.instance_id && old?.stream_id === value.stream_id;
    announcedState.current = {instance_id: value.instance_id, stream_id: value.stream_id,
      answer: Math.max(value.answer_revision ?? -1, same ? old!.answer : -1),
      progress: Math.max(value.progress_revision ?? -1, same ? old!.progress : -1)};
  }, []);
  const displayed = useRef<ServiceState>(state);
  const frozen = useRef<{instance: string | undefined; stream: string; turn: string; preview: AnswerPreview | null; progress: ProgressPreview | null} | null>(null);
  const publish = useCallback((next: ServiceSnapshot) => {
    const held = handoff.current;
    const incoming = next.answer_preview;
    const incomingProgress = next.progress_preview;
    if (held && (held.instance_id !== next.instance_id || held.stream_id !== next.stream_id
      || (next.active_turn_id && (next.active_turn_id !== held.turn_id || next.active_session_id !== held.session_id))
      || next.storage_error || (incomingProgress && (incomingProgress.turn_id !== held.turn_id || incomingProgress.session_id !== held.session_id
        || (incomingProgress.revision > handoffProgressRevision.current && incomingProgress.entries.some(entry => ['invalidated', 'retrying'].includes(entry.status)))))
      || (incoming && (incoming.attempt_id !== held.attempt_id || incoming.turn_id !== held.turn_id
        || incoming.session_id !== held.session_id || ['invalidated', 'retrying'].includes(incoming.status))))) clearAnswerHandoff();
    else if (held) {
      // 完成通知后仍可能收到同次作答的较新累计内容；交接也必须前进。
      if (incoming && incoming.revision > held.revision) {
        handoff.current = {...incoming, status: 'generated'};
        setAnswerHandoff(handoff.current);
      }
      if (incomingProgress && incomingProgress.revision > handoffProgressRevision.current) {
        handoffProgressRevision.current = incomingProgress.revision;
        setProgressHandoff(incomingProgress);
      }
    }
    const matches = frozen.current?.instance === next.instance_id && frozen.current?.stream === next.stream_id
      && frozen.current?.turn === next.active_turn_id;
    if (!matches) frozen.current = null;
    if (!frozen.current && next.stopping && next.active_turn_id) {
      const belongsToTarget = (value: AnswerPreview | ProgressPreview) => value.instance_id === next.instance_id
        && value.stream_id === next.stream_id && value.session_id === next.active_session_id && value.turn_id === next.active_turn_id;
      const previous = displayed.current.answer_preview;
      const progress = displayed.current.progress_preview;
      frozen.current = {instance: next.instance_id, stream: next.stream_id, turn: next.active_turn_id,
        progress: progress && belongsToTarget(progress) ? progress
          : next.progress_preview && belongsToTarget(next.progress_preview) ? next.progress_preview : null,
        preview: previous && belongsToTarget(previous) ? previous
          : next.answer_preview && belongsToTarget(next.answer_preview) ? next.answer_preview : null};
    }
    const value = frozen.current
      ? {...next, stopping: true, answer_preview: frozen.current.preview, progress_preview: frozen.current.progress} : next;
    displayed.current = value;
    setState(value);
  }, [clearAnswerHandoff]);
  const beginStop = useCallback((turnId: string) => {
    const current = currentSnapshot.current;
    if (current?.active_turn_id === turnId) publish({...current, stopping: true});
  }, [publish]);
  const refresh = useCallback(async () => {
    const reading = ++stateRead.current;
    const response = await fetch('/api/snapshot');
    if (!response.ok) throw new Error('服务状态暂时无法核对。');
    const result: ServiceSnapshot = await response.json();
    if (result.schema_version !== 1) throw new Error('数据版本不兼容，请更新页面和服务。');
    if (reading === stateRead.current) {
      const previous = currentSnapshot.current;
      let next = {...result, ...result.state};
      if (previous && previous.instance_id === next.instance_id && previous.stream_id === next.stream_id) {
        const oldAnswer = (previous.answer_revision ?? -1) > (next.answer_revision ?? -1);
        const oldProgress = (previous.progress_revision ?? -1) > (next.progress_revision ?? -1);
        const announced = announcedState.current;
        const oldState = announced?.instance_id === next.instance_id && announced?.stream_id === next.stream_id
          && (announced.answer > (next.answer_revision ?? -1) || announced.progress > (next.progress_revision ?? -1));
        if (oldAnswer || oldProgress || oldState) {
          // 两类内容独立交接；旧状态不能释放名额，但另一类较新累计内容仍可采用。
          next = {...previous,
            answer_revision: oldAnswer ? previous.answer_revision : next.answer_revision,
            answer_preview: oldAnswer ? previous.answer_preview : next.answer_preview,
            progress_revision: oldProgress ? previous.progress_revision : next.progress_revision,
            progress_preview: oldProgress ? previous.progress_preview : next.progress_preview};
        }
      }
      rememberState(next);
      currentSnapshot.current = next;
      setContentRecoveryRevision(value => value + 1);
      publish(next);
    }
    return result;
  }, [publish, rememberState]);

  useEffect(() => {
    let cancelled = false;
    let stream: EventSource | undefined;
    let reconnectTimer: number | undefined;
    let cursor: number | undefined;
    let streamId: string | undefined;
    let connection = 0;
    const processed = new Set<string>();
    function requireInstance(instance: string | undefined) {
      const current = currentSnapshot.current;
      if (!current || instance !== current.instance_id) {
        ++stateRead.current;
        clearAnswerHandoff();
        if (current) {
          frozen.current = null;
          currentSnapshot.current = {...current, answer_preview: null, progress_preview: null, stopping: false};
          publish(currentSnapshot.current);
        }
        throw new Error('服务已重启，需要重新核对。');
      }
      return current;
    }
    function unavailable() {
      if (cancelled) return;
      setConnectionError('通知连接中断，正在从最后已处理游标补齐；已接受的查询继续在后台执行。');
      setState(current => ({...current,accepting:false,service_status:'unavailable'}));
    }
    function reconnect() {
      ++connection;
      stream?.close();
      window.clearTimeout(reconnectTimer);
      unavailable();
      if (!cancelled) reconnectTimer = window.setTimeout(() => void connect(), 2000);
    }
    async function connect() {
      const receiving = ++connection;
      const isCurrent = () => !cancelled && receiving === connection;
      try {
        const snapshot = await refresh();
        if (!isCurrent()) return;
        if (cursor === undefined || streamId !== snapshot.stream_id || cursor > snapshot.cursor) {
          if (cursor !== undefined) {
            setRecoveryNotice('事件游标无法续接或数据目录已更换，已重新读取一致快照。');
            setDatasetRevision(current => current + 1);
          }
          cursor = snapshot.cursor;
          streamId = snapshot.stream_id;
          processed.clear();
        }
        // 断线时保持最后实际处理的 cursor；核对状态不能跳过尚未收到的事件。
        setRevision(current => current + 1);
        let firstAnswer = true, firstProgress = true;
        stream = new EventSource(`/api/events?after=${cursor}&stream_id=${encodeURIComponent(streamId!)}`);
        stream.addEventListener('trace', event => {
          if (!isCurrent()) return;
          try {
            const notice = JSON.parse((event as MessageEvent).data);
            if (notice.schema_version !== 1 || !Number.isSafeInteger(notice.cursor) || !notice.event_id) throw new Error('事件无法识别。');
            if (notice.cursor <= cursor!) return;
            cursor = notice.cursor;
            if (processed.has(notice.event_id)) return;
            processed.add(notice.event_id);
            const preview = displayed.current.answer_preview;
            if (notice.kind === 'turn.finished' && notice.data?.answer_source === 'model'
              && (notice.data.status === 'completed' || ['output_limit', 'budget'].includes(notice.data.reason))
              && preview?.text.trim() && !['invalidated', 'retrying'].includes(preview.status)
              && preview.session_id === notice.session_id && preview.turn_id === notice.turn_id) {
              handoff.current = {...preview, status: 'generated'};
              setAnswerHandoff(handoff.current);
              const progress = displayed.current.progress_preview;
              handoffProgressRevision.current = progress?.revision ?? -1;
              setProgressHandoff(progress && progress.instance_id === preview.instance_id && progress.stream_id === preview.stream_id
                && progress.session_id === preview.session_id && progress.turn_id === preview.turn_id ? progress : null);
            }
            if (notice.kind === 'session.deleted') setDeletedSession(notice.session_id);
            setRevision(current => current + 1);
          } catch { reconnect(); }
        });
        stream.addEventListener('answer.preview', event => {
          if (!isCurrent()) return;
          try {
            const notice = JSON.parse((event as MessageEvent).data);
            if (notice.schema_version !== 1 || notice.kind !== 'answer.preview' || !Number.isSafeInteger(notice.revision)) throw new Error('正文更新无法识别。');
            const current = requireInstance(notice.instance_id);
            if (firstAnswer) {firstAnswer = false; setContentRecoveryRevision(value => value + 1);}
            if (notice.revision <= (current.answer_revision ?? -1)) return;
            const preview: AnswerPreview | null = notice.preview;
            if (preview && (preview.instance_id !== current.instance_id || preview.stream_id !== current.stream_id
              || preview.revision !== notice.revision)) return;
            currentSnapshot.current = {...current, answer_revision: notice.revision, answer_preview: preview};
            publish(currentSnapshot.current);
          } catch { reconnect(); }
        });
        stream.addEventListener('progress.preview', event => {
          if (!isCurrent()) return;
          try {
            const notice = JSON.parse((event as MessageEvent).data);
            if (notice.schema_version !== 1 || notice.kind !== 'progress.preview' || !Number.isSafeInteger(notice.revision)) throw new Error('进展更新无法识别。');
            const current = requireInstance(notice.instance_id);
            if (firstProgress) {firstProgress = false; setContentRecoveryRevision(value => value + 1);}
            if (notice.revision <= (current.progress_revision ?? -1)) return;
            const preview: ProgressPreview | null = notice.preview;
            if (preview && (preview.instance_id !== current.instance_id || preview.stream_id !== current.stream_id
              || preview.revision !== notice.revision)) return;
            currentSnapshot.current = {...current, progress_revision: notice.revision, progress_preview: preview};
            publish(currentSnapshot.current);
          } catch { reconnect(); }
        });
        stream.addEventListener('service.state', event => {
          if (!isCurrent()) return;
          try {
            const notice = JSON.parse((event as MessageEvent).data);
            if (notice.schema_version !== 1 || !notice.state) throw new Error('服务状态无法识别。');
            const current = requireInstance(notice.state.instance_id);
            const announced = announcedState.current;
            if ((notice.state.answer_revision !== undefined && notice.state.answer_revision < Math.max(current.answer_revision ?? -1, announced?.answer ?? -1))
              || (notice.state.progress_revision !== undefined && notice.state.progress_revision < Math.max(current.progress_revision ?? -1, announced?.progress ?? -1))) return;
            rememberState({...current, ...notice.state});
            // 状态与正文在网络中可以交错，正文按自己的次序保留，由会话及轮次投影决定是否展示。
            const next = {...current, ...notice.state, answer_revision: current.answer_revision, answer_preview: current.answer_preview,
              progress_revision: current.progress_revision, progress_preview: current.progress_preview};
            currentSnapshot.current = next;
            publish(next);
            setConnectionError('');
          } catch { reconnect(); }
        });
        stream.onerror = () => {if (isCurrent()) reconnect();};
      } catch {
        if (isCurrent()) reconnect();
      }
    }
    void connect();
    return () => {cancelled = true; ++stateRead.current; stream?.close(); window.clearTimeout(reconnectTimer);};
  }, [refresh, publish, clearAnswerHandoff, rememberState]);

  return {state,setState,answerHandoff,progressHandoff,clearAnswerHandoff,revision,contentRecoveryRevision,datasetRevision,deletedSession,connectionError,recoveryNotice,refresh,beginStop};
}
