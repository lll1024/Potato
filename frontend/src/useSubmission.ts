import { useRef, useState } from 'react';

export type AcceptedSubmission = {
  schema_version: number; submission_id: string; session_id: string; turn_id: string;
};
export type PendingSubmission = { submission_id: string; input: string; session_id: string | null };

class SubmissionError extends Error {
  constructor(readonly code: string, message: string) { super(message); }
}

async function request(path: string, options?: RequestInit): Promise<AcceptedSubmission> {
  const response = await fetch(path, options);
  const body = await response.json();
  if (!response.ok) {
    throw new SubmissionError(body.detail?.code ?? 'REJECTED',
      typeof body.detail === 'string' ? body.detail : body.detail?.message ?? '请求未被接受，请检查本机服务。');
  }
  if (body.schema_version !== 1 || !body.session_id || !body.turn_id) {
    throw new Error('无法识别接受结果。');
  }
  return body;
}

export function useSubmission(onAccepted: (accepted: AcceptedSubmission, submitted: PendingSubmission) => void) {
  const [pending, setPending] = useState<PendingSubmission | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState('');
  const inFlight = useRef(false);

  async function perform(submitted: PendingSubmission, query: boolean) {
    if (inFlight.current) return;
    inFlight.current = true;
    setSubmitting(true);
    setError('');
    try {
      const accepted = await request(query ? `/api/submissions/${encodeURIComponent(submitted.submission_id)}` : '/api/turns', query ? undefined : {
        method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify(submitted),
      });
      setPending(null);
      onAccepted(accepted, submitted);
    } catch (cause) {
      if (cause instanceof SubmissionError) {
        if (query && cause.code === 'SUBMISSION_NOT_FOUND') {
          setError('服务尚未接受这次提交，可安全重试；重试会沿用同一提交标识。');
        } else if (!query || cause.code === 'SUBMISSION_DELETED') {
          setPending(null);
          setError(cause.message);
        } else {
          setError(cause.message);
        }
      } else {
        setError('连接中断，尚不能确认是否已接受。请核对提交或安全重试；不会自动重发，也不会把连接问题记作模型失败。');
      }
    } finally { inFlight.current = false; setSubmitting(false); }
  }

  async function send(input: string, sessionId: string | null) {
    if (submitting || pending) return;
    const submitted = {submission_id: crypto.randomUUID(), input, session_id: sessionId};
    setPending(submitted);
    await perform(submitted, false);
  }

  return {pending, submitting, error, send,
    check: () => pending && !submitting ? perform(pending, true) : Promise.resolve(),
    retry: () => pending && !submitting ? perform(pending, false) : Promise.resolve(),
  };
}
