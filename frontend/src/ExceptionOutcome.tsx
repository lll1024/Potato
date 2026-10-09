import './exception.css';
import type { Turn } from './ConversationRounds';

export type QueryMaterial = {title: string; entries: string[][]; notes: string[]; tool_call_id: string; payload_id: string};

export function isExceptional(turn: Turn) {
  return turn.status !== 'running' && (turn.status !== 'completed' || turn.answer_source === 'application');
}

function explanation(turn: Turn) {
  switch (turn.reason) {
    case 'user_stop': return '已停止本次请求，旅行行程尚未完成。';
    case 'budget': return '查询达到本轮上限，结果不完整。可以缩小范围后发送。';
    case 'map_paused': return '地图查询已暂停，这次请求未能完成。仍可编辑需求，讨论已有资料。';
    case 'service_shutdown': return '本机服务已退出，这次请求未能完成。';
    case 'service_interrupted': return '服务中断，这次请求未能完成。未结束调用的结果仍未知。';
    case 'output_limit': return '回答达到输出上限，下面保留已收到的文字，回答尚不完整。';
    case 'storage_failure': return '存储故障，只能确认最后成功保存的事实。';
    default: return '这次请求未能完成，旅行行程尚未完成。';
  }
}

export function ExceptionOutcome({turn, onTrace, onMaterialTrace, onRetry, retryBlocked, onRead, materialsExpanded, onMaterialsExpanded, storageFault}: {
  turn: Turn; onTrace?: (turn: Turn) => void; onMaterialTrace?: (turn: Turn, material: QueryMaterial) => void;
  onRetry?: (turn: Turn) => void; retryBlocked?: string; onRead?: () => void;
  materialsExpanded?: boolean; onMaterialsExpanded?: (turnId: string, expanded: boolean) => void;
  storageFault?: boolean;
}) {
  const materials = turn.query_materials ?? [];
  return <div className="exception-outcome">
    <p>{storageFault ? '存储故障，本次结果未能完整保存。只能确认最后成功保存的事实。' : explanation(turn)}</p>
    <p>{storageFault || turn.reason === 'storage_failure' ? '请核对处理记录；未保存的需求或结果无法确认。' : '你的旅行需求已保留。'}</p>
    {turn.context_excluded && <p className="hint">已保存资料仍可回看；本轮整体不进入后续模型上下文，重新尝试不会恢复本轮全部成果。</p>}
    <div className="recovery-actions">
      {onRetry && <button className="retry-request" type="button" disabled={Boolean(retryBlocked)} aria-describedby={`retry-help-${turn.turn_id}`} onClick={() => onRetry(turn)}>重新尝试</button>}
      {onTrace && <button type="button" onClick={() => onTrace(turn)}>查看处理记录</button>}
    </div>
    {onRetry && <p className="hint" id={`retry-help-${turn.turn_id}`} role="status">{retryBlocked || '使用原需求创建新对话轮次，可能再次查询；也可编辑需求后发送。'}</p>}
    {materials.length > 0 ? <details className="query-materials" open={materialsExpanded} onClickCapture={onRead}
      onToggle={event => {if (event.currentTarget.open !== Boolean(materialsExpanded)) onMaterialsExpanded?.(turn.turn_id, event.currentTarget.open);}}>
      <summary>查看已有资料</summary>
      <p className="hint">这些是查询资料，旅行行程尚未完成。</p>
      {materials.map(material => <section key={material.tool_call_id} aria-label={material.title}>
        <h4>{material.title}</h4>
        {material.entries.map((lines, index) => <ul key={index}>{lines.map((line, lineIndex) => <li key={lineIndex}>{line}</li>)}</ul>)}
        {material.notes.map((note, index) => <p className="hint" key={index}>{note}</p>)}
        {onMaterialTrace && <button type="button" onClick={() => onMaterialTrace(turn, material)}>查看{material.title}原始记录</button>}
      </section>)}
    </details> : <p className="hint">{turn.unreadable_query_count ? '已保存的查询返回无法整理为可读摘要，请查看处理记录。' : '本轮没有可展示的查询资料；可在处理记录中核对查询状态。'}</p>}
    {materials.length > 0 && Boolean(turn.unreadable_query_count) && <p className="hint">另有查询返回无法整理为摘要，可在处理记录中查看。</p>}
  </div>;
}
