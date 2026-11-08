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
  const [revision, setRevision] = useState(0);
  const [contentRecoveryRevision, setContentRecoveryRevision] = useState(0);
  const [datasetRevision, setDatasetRevision] = useState(0);
  const [deletedSession, setDeletedSession] = useState<string | null>(null);
  const [connectionError, setConnectionError] = useState('');
  const [recoveryNotice, setRecoveryNotice] = useState('');
  const stateRead = useRef(0);
  const currentSnapshot = useRef<ServiceSnapshot | null>(null);
  const displayed = useRef<ServiceState>(state);
  const frozen = useRef<{instance: string | undefined; stream: string; turn: string; preview: AnswerPreview | null; progress: ProgressPreview | null} | null>(null);
  const publish = useCallback((next: ServiceSnapshot) => {
    const matches = frozen.current?.instance === next.instance_id && frozen.current?.stream === next.stream_id
      && frozen.current?.turn === next.active_turn_id;
    if (!matches) frozen.current = null;
    if (!frozen.current && next.stopping && next.active_turn_id) {
      const previous = displayed.current.answer_preview;
      frozen.current = {instance: next.instance_id, stream: next.stream_id, turn: next.active_turn_id,
        progress: displayed.current.progress_preview ?? next.progress_preview ?? null,
        preview: previous && previous.instance_id === next.instance_id && previous.turn_id === next.active_turn_id ? previous : next.answer_preview ?? null};
    }
    const value = frozen.current
      ? {...next, stopping: true, answer_preview: frozen.current.preview, progress_preview: frozen.current.progress} : next;
    displayed.current = value;
    setState(value);
  }, []);
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
      if (previous?.instance_id === next.instance_id && previous?.stream_id === next.stream_id
        && (previous.progress_revision ?? -1) > (next.progress_revision ?? -1)) {
        next = {...next, progress_revision: previous.progress_revision, progress_preview: previous.progress_preview};
      }
      if (!previous || previous.stream_id !== next.stream_id || previous.instance_id !== next.instance_id
        || (next.answer_revision ?? -1) >= (previous.answer_revision ?? -1)) {
        currentSnapshot.current = next;
        setContentRecoveryRevision(value => value + 1);
        publish(next);
      }
    }
    return result;
  }, [publish]);

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
            ++stateRead.current;
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
            ++stateRead.current;
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
            if (notice.state.answer_revision !== undefined && notice.state.answer_revision < (current.answer_revision ?? -1)) return;
            ++stateRead.current;
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
  }, [refresh, publish]);

  return {state,setState,revision,contentRecoveryRevision,datasetRevision,deletedSession,connectionError,recoveryNotice,refresh,beginStop};
}
