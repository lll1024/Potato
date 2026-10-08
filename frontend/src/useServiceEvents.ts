import { useCallback, useEffect, useRef, useState } from 'react';

export type ServiceState = {
  active_turn_id: string | null; active_session_id: string | null; accepting: boolean;
  stopping: boolean; map_paused: boolean; map_pause_reason: string | null;
  service_status: 'available' | 'unavailable'; storage_error?: string | null;
};
type ServiceSnapshot = ServiceState & { schema_version: number; state?: ServiceState; cursor: number; stream_id: string };

export function useServiceEvents() {
  const [state, setState] = useState<ServiceState>({active_turn_id:null,active_session_id:null,
    accepting:false,stopping:false,map_paused:false,map_pause_reason:null,service_status:'unavailable'});
  const [revision, setRevision] = useState(0);
  const [datasetRevision, setDatasetRevision] = useState(0);
  const [deletedSession, setDeletedSession] = useState<string | null>(null);
  const [connectionError, setConnectionError] = useState('');
  const [recoveryNotice, setRecoveryNotice] = useState('');
  const stateRead = useRef(0);
  const refresh = useCallback(async () => {
    const reading = ++stateRead.current;
    const response = await fetch('/api/snapshot');
    if (!response.ok) throw new Error('服务状态暂时无法核对。');
    const result: ServiceSnapshot = await response.json();
    if (result.schema_version !== 1) throw new Error('数据版本不兼容，请更新页面和服务。');
    if (reading === stateRead.current) setState(result.state ?? result);
    return result;
  }, []);

  useEffect(() => {
    let cancelled = false;
    let stream: EventSource | undefined;
    let reconnectTimer: number | undefined;
    let cursor: number | undefined;
    let streamId: string | undefined;
    const processed = new Set<string>();
    function unavailable() {
      if (cancelled) return;
      setConnectionError('通知连接中断，正在从最后已处理游标补齐；已接受的查询继续在后台执行。');
      setState(current => ({...current,accepting:false,service_status:'unavailable'}));
    }
    function reconnect() {
      stream?.close();
      window.clearTimeout(reconnectTimer);
      unavailable();
      if (!cancelled) reconnectTimer = window.setTimeout(() => void connect(), 2000);
    }
    async function connect() {
      try {
        const snapshot = await refresh();
        if (cancelled) return;
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
        stream = new EventSource(`/api/events?after=${cursor}&stream_id=${encodeURIComponent(streamId!)}`);
        stream.addEventListener('trace', event => {
          if (cancelled) return;
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
        stream.addEventListener('service.state', event => {
          if (cancelled) return;
          try {
            const notice = JSON.parse((event as MessageEvent).data);
            if (notice.schema_version !== 1 || !notice.state) throw new Error('服务状态无法识别。');
            ++stateRead.current;
            setState(notice.state);
            setConnectionError('');
          } catch { reconnect(); }
        });
        stream.onerror = reconnect;
      } catch {
        reconnect();
      }
    }
    void connect();
    return () => {cancelled = true; stream?.close(); window.clearTimeout(reconnectTimer);};
  }, [refresh]);

  return {state,setState,revision,datasetRevision,deletedSession,connectionError,recoveryNotice,refresh};
}
