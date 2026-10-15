import { useEffect, useState } from 'react';
import type { Turn } from './ConversationRounds';
import './conversation-controls.css';
import { ModelRetries, retryEntries, type RetryEvent } from './ModelRetries';

type Request = {request_id: string; turn_id: string; status: string; purpose?: string; interrupted?: boolean; started_at?: string};
type Tool = {tool_call_id: string; turn_id: string; name: string; status: string; interrupted?: boolean};
type Event = RetryEvent & {tool_call_id?: string | null};
export type RoundActivity = {requests?: Request[]; tool_calls?: Tool[]; events?: Event[]};
const toolActions: Record<string, string> = {
  maps_text_search: '查询地点', maps_search_detail: '查询地点详情', maps_around_search: '查询周边地点',
  maps_weather: '查询天气', maps_geo: '查询地点坐标', maps_regeocode: '查询地址', maps_distance: '比较地点距离',
  maps_direction_walking: '比较步行路线', maps_direction_bicycling: '比较骑行路线',
  maps_direction_driving: '比较驾车路线', maps_direction_transit_integrated: '比较公共交通路线',
};
const action = (tool?: Tool) => tool ? toolActions[tool.name] ?? '执行查询' : '执行查询';

export function RoundProgress({turn, activity, stopping, disconnected, statusText, onStop}: {
  turn: Turn; activity?: RoundActivity; stopping?: boolean; disconnected?: boolean; statusText: string; onStop?: () => void;
}) {
  const [now, setNow] = useState(Date.now);
  const running = turn.status === 'running';
  useEffect(() => {
    if (!running) return;
    setNow(Date.now());
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [running]);
  const requests = (activity?.requests ?? []).filter(item => item.turn_id === turn.turn_id);
  const tools = (activity?.tool_calls ?? []).filter(item => item.turn_id === turn.turn_id);
  const events = (activity?.events ?? []).filter(item => item.turn_id === turn.turn_id).sort((a, b) => a.sequence - b.sequence);
  const executing = tools.filter(item => item.status === 'running' && !item.interrupted);
  const waiting = tools.filter(item => item.status === 'waiting' && !item.interrupted);
  const pending = tools.filter(item => item.status === 'pending' && !item.interrupted);
  const retries = retryEntries(events, turn);
  const retry = retries.at(-1);
  let phase = statusText;
  if (running) {
    if (disconnected) phase = '连接中断，后台可能仍在处理';
    else if (stopping) phase = '正在停止';
    else if (executing.length) phase = executing.length === 1 ? `正在${action(executing[0])}` : `正在执行 ${executing.length} 项查询`;
    else if (waiting.length) phase = `等待调用额度 · ${waiting.length} 项查询`;
    else if (pending.length) phase = `正在准备 ${pending.length} 项查询`;
    else if (retry?.state === 'waiting') phase = `等待重试（${retry.data?.retry ?? '未知'}/${retry.data?.max_retries ?? '未知'}） · ${retry.data?.delay_ms === undefined ? '等待时长未知' : `${Number((retry.data.delay_ms / 1000).toFixed(2))} 秒`}`;
    else if (requests.some(item => item.status === 'running' && !item.interrupted && item.purpose === 'travel_final')) phase = '已达到调用上限，正在整理已有资料';
    else if (requests.some(item => item.status === 'running' && !item.interrupted)) phase = '正在请求助手';
    else phase = requests.length ? '正在处理查询结果' : '正在准备请求';
    if (executing.length && waiting.length && !stopping && !disconnected) phase += ` · ${waiting.length} 项等待调用额度`;
  }
  const start = Date.parse(turn.created_at ?? events.find(item => item.kind === 'turn.accepted')?.timestamp ?? '');
  const duration = running ? now - start : turn.duration_ms ?? Date.parse(turn.finished_at ?? '') - start;
  const seconds = Number.isFinite(duration) ? Math.max(0, Math.floor(duration / 1000)) : null;
  return <div className="round-progress" role="region" aria-label={`第 ${turn.ordinal} 轮处理进展`} data-reading-anchor={`progress:${turn.turn_id}`}>
    <div className="progress-summary"><p role="status">{running && <span className="activity-spinner" aria-hidden="true" />}{phase}</p>
      {seconds !== null && <span className="progress-duration">{running ? '已等待' : '已用时'} {seconds} 秒</span>}</div>
    {running && retries.length > 0 && onStop && <button type="button" disabled={stopping || disconnected} onClick={onStop}>停止本轮重试</button>}
    <ModelRetries events={events} turn={turn} />
  </div>;
}
