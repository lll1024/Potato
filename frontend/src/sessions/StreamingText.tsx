import { useLayoutEffect, useRef, useState, type ReactNode } from 'react';

type ReadingUpdate = {text: string; identity: string; live: boolean; stopped?: boolean; recovery?: number};
const graphemes = new Intl.Segmenter('zh', {granularity: 'grapheme'});

// 已有内容直接恢复；只有正在生成的大块追加使用最多 200ms 的同一截止时间。
export function useStreamingText({text, identity, live, stopped = false, recovery = 0}: ReadingUpdate) {
  const [visible, setVisible] = useState(text);
  const reading = useRef({identity, recovery, visible: text, base: text, started: null as number | null});
  useLayoutEffect(() => {
    const current = reading.current;
    let frame = 0;
    const show = (value: string) => {current.visible = value; setVisible(value);};
    const direct = () => {current.started = null; current.base = text; show(text);};
    const newIdentity = current.identity !== identity;
    const replaced = newIdentity || current.recovery !== recovery || !text.startsWith(current.visible);
    current.identity = identity; current.recovery = recovery;
    const reduced = window.matchMedia('(prefers-reduced-motion: reduce)');
    if (stopped && !newIdentity) current.started = null;
    else if (replaced || !live || reduced.matches || document.hidden) direct();
    else {
      const remainder = Array.from(graphemes.segment(text.slice(current.visible.length)), part => part.segment);
      if (current.started === null && remainder.length <= 64) direct();
      else {
        if (current.started === null) {current.started = performance.now(); current.base = current.visible;}
        const parts = Array.from(graphemes.segment(text.slice(current.base.length)), part => part.segment);
        const append = () => {
          const elapsed = performance.now() - current.started!;
          if (elapsed >= 200) {direct(); return;}
          const count = Math.min(parts.length, Math.max(16, Math.ceil(parts.length * elapsed / 200)));
          show(current.base + parts.slice(0, count).join(''));
          frame = requestAnimationFrame(append);
        };
        append();
      }
    }
    const settle = () => {cancelAnimationFrame(frame); if (!stopped) direct();};
    reduced.addEventListener('change', settle);
    document.addEventListener('visibilitychange', settle);
    return () => {cancelAnimationFrame(frame); reduced.removeEventListener('change', settle); document.removeEventListener('visibilitychange', settle);};
  }, [text, identity, live, stopped, recovery]);
  return visible;
}

export function StreamingText({children, ...update}: ReadingUpdate & {children: (text: string) => ReactNode}) {
  return children(useStreamingText(update));
}
