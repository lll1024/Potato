import { useCallback, useLayoutEffect, useRef, useState } from 'react';

// 阅读位置只属于当前视图；暂停不触及后台执行。
export function useReadingFollow(identity: string | null, marker: string) {
  const ref = useRef<HTMLDivElement>(null);
  const following = useRef(true);
  const position = useRef(0);
  const attached = useRef(false);
  const lastMarker = useRef('');
  const lastIdentity = useRef(identity);
  const [paused, setPaused] = useState(false);
  const [hasNew, setHasNew] = useState(false);
  const pause = useCallback(() => {
    if (ref.current) position.current = ref.current.scrollTop;
    following.current = false;
    setPaused(true);
  }, []);
  const resume = useCallback(() => {
    following.current = true;
    setPaused(false);
    setHasNew(false);
    const element = ref.current;
    if (element) element.scrollTop = element.scrollHeight;
  }, []);
  useLayoutEffect(() => {
    if (lastIdentity.current !== identity) {
      lastIdentity.current = identity;
      following.current = true;
      lastMarker.current = '';
      setPaused(false); setHasNew(false);
    }
    if (lastMarker.current === marker) return;
    const hadContent = Boolean(lastMarker.current);
    lastMarker.current = marker;
    if (following.current) {
      const element = ref.current;
      if (element) element.scrollTop = element.scrollHeight;
    } else if (hadContent) setHasNew(true);
  }, [identity, marker]);
  useLayoutEffect(() => {
    if (!attached.current || !ref.current) return;
    attached.current = false;
    // 完整视图布局完成后恢复，避免挂载过程中按尚未稳定的高度夹紧位置。
    ref.current.scrollTop = following.current ? ref.current.scrollHeight : position.current;
  });
  const attach = useCallback((element: HTMLDivElement | null) => {
    ref.current=element;
    attached.current = Boolean(element);
  }, []);
  const onScroll = useCallback(() => {
    const element = ref.current;
    if (element) position.current=element.scrollTop;
    if (element && element.scrollHeight - element.scrollTop - element.clientHeight > 24) pause();
  }, [pause]);
  return {ref: attach, paused, hasNew, pause, resume, onScroll};
}
