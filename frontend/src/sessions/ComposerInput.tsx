import { useLayoutEffect, useRef } from 'react';
import './conversation-controls.css';

export function ComposerInput({value, onChange, canSend}: {value: string; onChange: (value: string) => void; canSend: boolean}) {
  const input = useRef<HTMLTextAreaElement>(null);
  const composing = useRef(false);

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

  return <textarea ref={input} className="conversation-input" id="travel-input" name="travel-input" value={value} onChange={event => onChange(event.target.value)}
    onCompositionStart={() => {composing.current = true;}} onCompositionEnd={() => {composing.current = false;}}
    onKeyDown={event => {
      if (event.key !== 'Enter' || event.shiftKey || event.ctrlKey || event.metaKey || event.altKey) return;
      if (composing.current || event.nativeEvent.isComposing || event.nativeEvent.keyCode === 229) return;
      event.preventDefault();
      if (canSend && value.trim() && !event.repeat) event.currentTarget.form?.requestSubmit();
    }} required rows={1} placeholder="写下城市、日期和你想查询的内容…" />;
}
