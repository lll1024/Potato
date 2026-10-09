import {useEffect, useState} from 'react';
import {requestJson} from './api';
import './preferences.css';

export type Preference = {id: string; category: string; content: string; source_turn_id: string | null; source_input: string; updated_at: string; input_order: number; version: number};
export type PreferenceState = {schema_version: number; preferences: Preference[]; processing: {turn_id: string; session_id: string; status: string; attempts: number; error: string | null}[]};
const categories = [['diet', '饮食'], ['activity', '活动兴趣'], ['transport', '交通'], ['lodging', '住宿喜好']];

export function PreferencesView({onBack}: {onBack: () => void}) {
  const [snapshot, setSnapshot] = useState<PreferenceState | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  async function refresh() {
    setLoading(true); setError('');
    try {setSnapshot(await requestJson<PreferenceState>('/api/preferences', '无法读取偏好，请刷新重试。'));}
    catch (cause) {setError(cause instanceof Error ? cause.message : '无法读取偏好，请刷新重试。');}
    finally {setLoading(false);}
  }
  useEffect(() => {void refresh();}, []);
  const failed = snapshot?.processing.filter(item => item.status === 'failed') ?? [];
  return <section className="preferences-view" aria-labelledby="preferences-title">
    <div className="preferences-heading">
      <h2 id="preferences-title">旅行者偏好</h2>
      <div className="preferences-actions"><button onClick={onBack}>返回对话</button><button disabled={loading} onClick={() => void refresh()}>刷新偏好</button></div>
    </div>
    <p className="hint">饮食、活动兴趣、交通和住宿喜好会在最后一轮结束并闲置一小时后尝试保存。刚刚表达的偏好可能尚未保存。</p>
    <p className="hint">删除聊天会保留已保存的偏好，聊天与偏好需要分别管理。</p>
    {loading && <p role="status">正在读取偏好…</p>}
    {error && <p className="error" role="alert">{error}</p>}
    {failed.length > 0 && <div className="preference-failures"><h3>尚未保存的输入</h3><p>有 {failed.length} 轮偏好提取未成功，已有偏好仍然保留。</p></div>}
    {snapshot && !snapshot.preferences.length && <div className="preferences-empty"><h3>暂无已保存的偏好</h3><p>在对话中表达你的长期喜好即可。临时安排、游玩节奏和同行人的偏好不会保存到这里。</p></div>}
    {snapshot && categories.map(([category, title]) => {
      const items = snapshot.preferences.filter(item => item.category === category);
      return items.length > 0 && <section className="preference-category" key={category} aria-label={title}>
        <h3>{title}</h3><ul>{items.map(item => <li key={item.id}>
          <p className="preference-content">{item.content}</p>
          <dl><dt>来源表达</dt><dd>{item.source_input || '来源已不可用'}</dd><dt>更新时间</dt><dd><time dateTime={item.updated_at}>{new Date(item.updated_at).toLocaleString('zh-CN')}</time></dd></dl>
        </li>)}</ul>
      </section>;
    })}
  </section>;
}
