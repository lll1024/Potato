import { useEffect, useRef, useState } from 'react';
import { Highlight, type TraceLocation } from './PayloadViewer';
type Match = Omit<TraceLocation,'query'> & {request_ordinal?: number; tool_ordinal?: number; tool_name?: string; highlight?: string};
const fields: Record<string,string> = {turn_input:'用户输入',turn_answer:'对话回答',input:'模型完整输入',response:'SDK 完整返回',error:'模型错误',tool_arguments:'工具参数',tool_service:'SDK 服务结果',tool_result:'实际应用回填',tool_error:'工具错误'};
export function TraceSearch({sessionId,onLocate,onInteraction}: {sessionId: string; onLocate: (location: TraceLocation) => void; onInteraction?: () => void}) {
  const requestGeneration=useRef(0);
  const [query,setQuery]=useState('');
  const [searched,setSearched]=useState('');
  const [scope,setScope]=useState('full');
  const [matches,setMatches]=useState<Match[]>([]);
  const [status,setStatus]=useState('');
  const [error,setError]=useState('');
  const [busy,setBusy]=useState(false);
  useEffect(() => {requestGeneration.current += 1; setQuery(''); setSearched(''); setMatches([]); setStatus(''); setError(''); setBusy(false); return () => {requestGeneration.current += 1;};},[sessionId]);
  async function search() {
    onInteraction?.();
    if (!query.trim()) {setError('请输入搜索关键词。'); return;}
    const generation=requestGeneration.current;
    setBusy(true); setError('');
    try {
      const response=await fetch(`/api/sessions/${sessionId}/search?${new URLSearchParams({q:query,scope})}`);
      const result=await response.json();
      if (generation !== requestGeneration.current) return;
      if (!response.ok) throw new Error('搜索失败，请重试。');
      if (result.schema_version!==1) throw new Error('数据版本不兼容，请更新页面。');
      setMatches(result.matches); setSearched(result.query); setQuery(result.query); setStatus(`找到 ${result.matches.length} 个匹配字段`);
    } catch (cause) {if (generation===requestGeneration.current) setError(cause instanceof Error ? cause.message : '搜索失败。');}
    finally {if (generation===requestGeneration.current) setBusy(false);}
  }
  return <section className="trace-search" aria-label="当前会话搜索">
    <form onSubmit={event => {event.preventDefault(); void search();}}>
      <label>搜索当前会话<input name="trace-query" value={query} onFocus={onInteraction} onChange={event => {onInteraction?.(); setQuery(event.target.value);}} /></label>
      <label>搜索范围<select value={scope} onChange={event => {onInteraction?.(); setScope(event.target.value);}}><option value="full">完整已保存内容（含未加载）</option><option value="summary">仅摘要</option></select></label>
      <button disabled={busy}>{busy ? '搜索中…' : '搜索'}</button>
    </form>
    <p role="status">{status}</p><p role="alert">{error}</p>
    {!!matches.length && <ul>{matches.map((match,index) => <li key={`${match.payload_id ?? match.object_id}-${match.field}-${index}`}><button onClick={() => {onInteraction?.(); onLocate({...match,query:match.highlight ?? searched});}}>
      <strong>第 {match.turn_ordinal} 轮 · {match.object_type==='tool' ? `工具 ${match.tool_ordinal ?? ''} ${match.tool_name ?? ''}` : match.object_type==='request' ? `模型请求 ${match.request_ordinal ?? ''}` : '对话轮次'} · {fields[match.field] ?? match.field}</strong>
      <span>节选：<Highlight text={match.excerpt ?? ''} query={match.highlight ?? searched} /></span>
      <small>对象 {match.object_id}{match.payload_id ? ` · 载荷 ${match.payload_id}` : ''}</small>
    </button></li>)}</ul>}
  </section>;
}
