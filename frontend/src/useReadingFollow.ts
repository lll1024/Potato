import { useCallback, useLayoutEffect, useRef, useState } from 'react';

// 阅读位置只属于当前视图；暂停不触及后台执行。
export function useReadingFollow(identity: string | null, marker: string) {
  const ref = useRef<HTMLDivElement>(null);
  const following = useRef(true);
  const position = useRef(0);
  const lastMarker = useRef('');
  const lastIdentity = useRef(identity);
  const anchor = useRef<{element: Element; offset: number} | null>(null);
  const [paused, setPaused] = useState(false);
  const [hasNew, setHasNew] = useState(false);
  const pause = useCallback(() => {
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
  const preservePosition = useCallback(() => {
    const container=ref.current;
    if (!container) return;
    const top=container.getBoundingClientRect().top;
    const element=Array.from(container.querySelectorAll('[data-reading-id]')).find(item => item.getBoundingClientRect().bottom > top);
    if (element) anchor.current={element,offset:element.getBoundingClientRect().top-top};
  }, []);
  useLayoutEffect(() => {
    const saved=anchor.current;
    const container=ref.current;
    if (saved && container && saved.element.isConnected) {
      container.scrollTop+=saved.element.getBoundingClientRect().top-container.getBoundingClientRect().top-saved.offset;
      position.current=container.scrollTop;
    }
    anchor.current=null;
  });
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
  const attach = useCallback((element: HTMLDivElement | null) => {
    if (ref.current) position.current=ref.current.scrollTop;
    ref.current=element;
    if (element) element.scrollTop=following.current ? element.scrollHeight : position.current;
  }, []);
  const onScroll = useCallback(() => {
    const element = ref.current;
    if (element) position.current=element.scrollTop;
    if (element && element.scrollHeight - element.scrollTop - element.clientHeight > 24) pause();
  }, [pause]);
  return {ref: attach, paused, hasNew, pause, resume, onScroll, preservePosition};
}
