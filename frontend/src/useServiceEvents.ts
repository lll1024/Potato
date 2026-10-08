import { useCallback, useEffect, useState } from 'react';

export type ServiceState = {
  active_turn_id: string | null; active_session_id: string | null; accepting: boolean;
  stopping: boolean; map_paused: boolean; map_pause_reason: string | null;
  service_status: 'available' | 'unavailable'; storage_error?: string | null;
};
type ServiceSnapshot = { schema_version: number; state: ServiceState; cursor: number; stream_id: string };

export function useServiceEvents() {
  const [state, setState] = useState<ServiceState>({active_turn_id:null,active_session_id:null,
    accepting:false,stopping:false,map_paused:false,map_pause_reason:null,service_status:'unavailable'});
  const [revision, setRevision] = useState(0);
  const [connectionError, setConnectionError] = useState('');
  const refresh = useCallback(async () => {
    const response = await fetch('/api/snapshot');
    if (!response.ok) throw new Error('服务状态暂时无法核对。');
    const result: ServiceSnapshot = await response.json();
    if (result.schema_version !== 1) throw new Error('数据版本不兼容，请更新页面和服务。');
    setState(result.state);
    return result;
  }, []);

  useEffect(() => {
    let cancelled = false;
    let stream: EventSource | undefined;
    function unavailable() {
      if (cancelled) return;
      setConnectionError('通知连接中断，正在重新连接；已接受的查询继续在后台执行。');
      setState(current => ({...current,accepting:false,service_status:'unavailable'}));
    }
    async function connect() {
      try {
        const snapshot = await refresh();
        if (cancelled) return;
        stream = new EventSource(`/api/events?after=${snapshot.cursor}&stream_id=${encodeURIComponent(snapshot.stream_id)}`);
        stream.addEventListener('trace', () => {
          if (cancelled) return;
          setRevision(current => current + 1);
          void refresh().catch(unavailable);
        });
        stream.onopen = () => {
          if (cancelled) return;
          setConnectionError('');
          void refresh().catch(unavailable);
        };
        stream.onerror = unavailable;
      } catch {
        unavailable();
      }
    }
    void connect();
    return () => {cancelled = true; stream?.close();};
  }, [refresh]);

  return {state,setState,revision,connectionError,refresh};
}
