import { useLayoutEffect, useRef } from 'react';
import './conversation-controls.css';

export function ComposerInput({value, onChange}: {value: string; onChange: (value: string) => void}) {
  const input = useRef<HTMLTextAreaElement>(null);

  useLayoutEffect(() => {
    const element = input.current;
    if (!element) return;
    const resize = () => {
      element.style.height = 'auto';
      element.style.height = `${element.scrollHeight + element.offsetHeight - element.clientHeight}px`;
    };
    resize();
    let width = element.getBoundingClientRect().width;
    let frame = 0;
    const observer = new ResizeObserver(() => {
      const nextWidth = element.getBoundingClientRect().width;
      if (width !== nextWidth) {
        width = nextWidth;
        window.cancelAnimationFrame(frame);
        frame = window.requestAnimationFrame(resize);
      }
    });
    observer.observe(element);
    return () => {observer.disconnect(); window.cancelAnimationFrame(frame);};
  }, [value]);

  return <textarea ref={input} className="conversation-input" id="travel-input" name="travel-input" value={value} onChange={event => onChange(event.target.value)} required rows={1} placeholder="写下城市、日期和你想查询的内容…" />;
}
