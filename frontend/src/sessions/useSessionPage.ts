import { useEffect, useRef, useState } from 'react';
import { requestJson } from '../api';
import { mergeSessionPage } from './sessionPages';

type SessionPage = Parameters<typeof mergeSessionPage>[0] & {session: {title: string}};

export function useSessionPage<T extends NonNullable<SessionPage>>(sessionId: string | null, revision: number | undefined,
  query: string, datasetRevision = 0, poll = false) {
  const [pageState, setPageState] = useState<{id: string | null; page: T | null}>({id: null, page: null});
  const snapshot = pageState.id === sessionId ? pageState.page : null;
  const [error, setError] = useState('');
  const [loadingEarlier, setLoadingEarlier] = useState(false);
  const [invalidation, setInvalidation] = useState(0);
  const cache = useRef(new Map<string, T>());
  const generation = useRef(0);
  const selected = useRef(sessionId);
  selected.current = sessionId;
  const dataset = useRef(datasetRevision);
  if (dataset.current !== datasetRevision) {
    dataset.current = datasetRevision;
    cache.current.clear();
    ++generation.current;
  }
  useEffect(() => {
    ++generation.current;
    setPageState({id: sessionId, page: sessionId ? cache.current.get(sessionId) ?? null : null});
    setError(''); setLoadingEarlier(false);
  }, [sessionId, datasetRevision]);

  async function read(id: string, suffix = '') {
    return requestJson<T>(`/api/sessions/${id}?${query}${suffix}`, '无法读取会话。');
  }
  function apply(id: string, page: T, mode: 'refresh' | 'earlier' | 'locate', requestedBefore?: number) {
    const merged = mergeSessionPage(cache.current.get(id) ?? null, page, mode, requestedBefore);
    cache.current.set(id, merged);
    if (selected.current === id) setPageState({id, page: merged});
    return merged;
  }
  useEffect(() => {
    if (!sessionId) return;
    let cancelled = false;
    let reading = 0, applied = 0;
    const epoch = generation.current;
    async function refresh() {
      const request = ++reading;
      try {
        const page = await read(sessionId!);
        if (!cancelled && request > applied && epoch === generation.current) {
          applied = request;
          apply(sessionId!, page, 'refresh');
        }
      } catch (cause) {
        if (!cancelled && epoch === generation.current) setError(cause instanceof Error ? cause.message : '无法读取会话。');
      }
    }
    void refresh();
    const interval = poll ? window.setInterval(refresh, 500) : undefined;
    return () => {cancelled = true; window.clearInterval(interval);};
  }, [sessionId, revision, datasetRevision, query, poll, invalidation]);

  async function loadEarlier() {
    if (!sessionId || !snapshot?.next_before || loadingEarlier) return;
    const id = sessionId, epoch = generation.current, before = snapshot.next_before;
    setLoadingEarlier(true);
    try {
      const page = await read(id, `&before=${before}`);
      if (epoch === generation.current) apply(id, page, 'earlier', before);
    } catch (cause) {
      if (epoch === generation.current) setError(cause instanceof Error ? cause.message : '无法读取更早会话。');
    } finally {if (epoch === generation.current) setLoadingEarlier(false);}
  }
  async function locate(ordinal: number) {
    if (!sessionId) return;
    const id = sessionId, epoch = generation.current;
    const page = await read(id, `&limit=1&before=${ordinal + 1}`);
    if (epoch !== generation.current) return;
    apply(id, page, 'locate');
    return page;
  }
  function invalidate(id?: string) {
    if (id && id !== selected.current) {cache.current.delete(id); return;}
    ++generation.current;
    setLoadingEarlier(false);
    if (id) cache.current.delete(id); else cache.current.clear();
    if (!id || selected.current === id) setPageState({id: selected.current, page: null});
    setInvalidation(current => current + 1);
  }
  return {snapshot, error, setError, loadingEarlier, loadEarlier, locate, invalidate};
}
