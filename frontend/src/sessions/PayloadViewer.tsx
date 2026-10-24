import { useEffect, useRef, useState } from 'react';

export type TraceLocation = {
  turn_id: string; turn_ordinal: number; object_id: string; object_type: string;
  payload_id: string | null; field: string; query: string; offset?: number; excerpt?: string;
};
type Segment = {schema_version: number; payload_id: string; offset: number; total: number; text: string; next_offset: number | null};
export function Highlight({text,query}: {text: string; query?: string}) {
  if (!query) return text;
  const parts = text.split(query);
  return parts.map((part,index) => <span key={index}>{index > 0 && <mark>{query}</mark>}{part}</span>);
}
async function segment(id: string, offset: number): Promise<Segment> {
  const response = await fetch(`/api/payloads/${id}?offset=${offset}&limit=8192`);
  const result = await response.json();
  if (!response.ok) throw new Error('完整载荷读取失败，请重试。');
  if (result.schema_version !== 1) throw new Error('数据版本不兼容，请更新页面。');
  if (result.payload_id !== id || result.offset !== offset) throw new Error('载荷片段位置不一致，请重新读取。');
  return result;
}
export function Payload({id,title,location,onInteraction}: {id: string | null; title: string; location?: TraceLocation | null; onInteraction?: () => void}) {
  const [text,setText] = useState('');
  const [total,setTotal] = useState<number | null>(null);
  const [next,setNext] = useState<number | null>(0);
  const [error,setError] = useState('');
  const [notice,setNotice] = useState('');
  const [busy,setBusy] = useState(false);
  const [wrap,setWrap] = useState(true);
  const [format,setFormat] = useState(false);
  const detail = useRef<HTMLDetailsElement>(null);
  const preview = useRef<HTMLPreElement>(null);
  const content = useRef({text:'',total:null as number | null,next:0 as number | null});
  const generation = useRef(0);
  const loading = useRef<Promise<string | null> | null>(null);
  const targeted = location?.payload_id === id && !!id;
  useEffect(() => {
    generation.current += 1; loading.current=null; content.current = {text:'',total:null,next:0};
    setText(''); setTotal(null); setNext(0); setError(''); setNotice(''); setBusy(false); setFormat(false);
    return () => {generation.current += 1;};
  },[id]);
  async function load(all = false, until = 0): Promise<string | null> {
    if (!id) return null;
    const token = generation.current;
    if (loading.current) {
      await loading.current;
      if (token !== generation.current) return null;
      return load(all,until);
    }
    setBusy(true); setError('');
    const operation = (async () => {
      try {
        let current = content.current;
        do {
          if (current.next === null) break;
          const part = await segment(id,current.next);
          if (token !== generation.current) return null;
          if (current.total !== null && current.total !== part.total) throw new Error('载荷总量发生变化，请重新读取。');
          current = {text:current.text+part.text,total:part.total,next:part.next_offset};
          content.current=current; setText(current.text); setTotal(current.total); setNext(current.next);
        } while (all || (current.next !== null && current.next <= until));
        return current.text;
      } catch (cause) {
        if (token === generation.current) setError(cause instanceof Error ? cause.message : '载荷读取失败。');
        return null;
      }
    })();
    loading.current=operation;
    const result=await operation;
    if (loading.current===operation) {loading.current=null; setBusy(false);}
    return result;
  }
  useEffect(() => {
    if (!targeted) return;
    if (detail.current) detail.current.open=true;
    void load(false,(location?.offset ?? 0)+Array.from(location?.query ?? '').length);
  // 定位身份改变时读取到匹配位置；普通展开仍只读首段。
  },[id,location]);
  useEffect(() => {
    if (targeted && !busy) {
      preview.current?.querySelector('mark')?.scrollIntoView({block:'nearest'});
      preview.current?.focus({preventScroll:true});
    }
  },[text,targeted,busy]);
  async function copy() {
    onInteraction?.();
    const complete = await load(true);
    if (complete === null) return;
    try {await navigator.clipboard.writeText(complete); setNotice(`已复制完整脱敏内容（${Array.from(complete).length} 字符）`);}
    catch {setError('无法写入剪贴板，请检查浏览器权限后重试。');}
  }
  async function toggleFormat(checked: boolean) {
    onInteraction?.();
    if (checked && await load(true) === null) return;
    setFormat(checked);
  }
  if (!id) return null;
  let display = text;
  if (format && next === null) {
    try {display=JSON.stringify(JSON.parse(text),null,2);} catch { /* 保存内容仍可按原文阅读。 */ }
  }
  return <details ref={detail} className="trace-payload" data-payload-id={id} onToggle={event => {if (event.currentTarget.open) {onInteraction?.(); if (content.current.next===0) void load();}}}>
    <summary>{title}</summary>
    <div className="payload-controls">
      <label><input type="checkbox" checked={wrap} onChange={event => {onInteraction?.(); setWrap(event.target.checked);}} />自动换行</label>
      <label><input type="checkbox" checked={format} disabled={busy} onChange={event => void toggleFormat(event.target.checked)} />格式化 JSON</label>
      <button disabled={busy} onClick={() => void copy()}>复制完整脱敏内容</button>
    </div>
    <p className="hint">{total === null ? '按需读取完整载荷' : next === null ? `完整载荷 · ${total} 字符` : `节选 · 已读取 ${next}/${total} 字符（Unicode 字符位置）`}</p>
    <p role="status">{notice}{busy ? '正在读取…' : ''}</p>
    {error && <p role="alert">{error}</p>}
    <pre ref={preview} tabIndex={0} aria-label={title} className={wrap ? 'wrap' : ''}><Highlight text={display} query={targeted ? location?.query : undefined} /></pre>
    {next !== null && <button disabled={busy} onClick={() => {onInteraction?.(); void load();}}>继续读取下一段</button>}
  </details>;
}
