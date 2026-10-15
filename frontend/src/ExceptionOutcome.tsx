import './exception.css';
import type { Turn } from './ConversationRounds';
import { Icon } from './Icon';

export type QueryMaterial = {title: string; entries: string[][]; notes: string[]; tool_call_id: string; payload_id: string};

export function isExceptional(turn: Turn) {
  return turn.status !== 'running' && (turn.status !== 'completed' || turn.answer_source === 'application');
}

function explanation(turn: Turn) {
  const modelFailures: Record<string, string> = {
    AUTH: '模型认证失败', QUOTA: '额度不足', RATE_LIMIT: '请求过于频繁', TIMEOUT: '连接超时',
    TRANSPORT: '连接失败', SERVER: '服务暂时不可用', EMPTY_RESPONSE: '模型返回空响应',
    CONTEXT_WINDOW_EXCEEDED: '上下文超过模型上限', INVALID_REQUEST: '请求参数无效',
  };
  switch (turn.reason) {
    case 'model_error': return modelFailures[turn.model_error_code ?? ''] ?? '请求失败';
    case 'user_stop': return '已停止';
    case 'budget': return '查询达到上限';
    case 'map_paused': return '地图查询已暂停';
    case 'service_shutdown': return '本机服务已退出';
    case 'service_interrupted': return '服务中断';
    case 'output_limit': return '回答达到输出上限';
    case 'storage_failure': return '结果未完整保存';
    case 'context_budget': return '上下文超过安全预算';
    case 'context_configuration': return '上下文容量配置无效';
    case 'context_window_exceeded': return '上下文超过模型上限';
    case 'context_recovery_unavailable': return '上下文无法恢复';
    case 'execution_error': return '服务执行失败';
    default: return '请求失败';
  }
}

export function ExceptionOutcome({turn, onRetry, retryBlocked, storageFault}: {
  turn: Turn; onRetry?: (turn: Turn) => void; retryBlocked?: string; storageFault?: boolean;
}) {
  const help = retryBlocked || '使用原需求创建新对话轮次，可能再次查询；保留输入框草稿。';
  const symbol = <span className="retry-symbol"><Icon name="retry" size={16} /></span>;
  return <div className="exception-outcome" role="region" aria-label={`第 ${turn.ordinal} 轮异常提示`}>
    {onRetry ? <button className="retry-request" type="button" disabled={Boolean(retryBlocked)} aria-label="重新尝试"
      title={retryBlocked || '重新尝试'} aria-describedby={`retry-help-${turn.turn_id}`} onClick={() => onRetry(turn)}>{symbol}</button> : <span className="retry-unavailable" aria-hidden="true">{symbol}</span>}
    <p role="status">{storageFault ? '结果未完整保存' : explanation(turn)}</p>
    {onRetry && <span className="visually-hidden" id={`retry-help-${turn.turn_id}`}>{help}</span>}
  </div>;
}
