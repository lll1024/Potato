import { useRef, useState } from 'react';

export type AcceptedSubmission = {
  schema_version: number; submission_id: string; session_id: string; turn_id: string;
};
export type PendingSubmission = { submission_id: string; input: string; session_id: string | null; preserve_draft?: boolean };

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
  const generation = useRef(0);
  const inFlight = useRef(false);
  const acceptedCallback = useRef(onAccepted);
  acceptedCallback.current = onAccepted;

  async function perform(submitted: PendingSubmission, query: boolean) {
    if (inFlight.current) return;
    const epoch = generation.current;
    inFlight.current = true;
    setSubmitting(true);
    setError('');
    try {
      const accepted = await request(query ? `/api/submissions/${encodeURIComponent(submitted.submission_id)}` : '/api/turns', query ? undefined : {
        method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify({submission_id: submitted.submission_id, input: submitted.input, session_id: submitted.session_id}),
      });
      if (epoch !== generation.current) return;
      setPending(null);
      acceptedCallback.current(accepted, submitted);
    } catch (cause) {
      if (epoch !== generation.current) return;
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
    } finally { if (epoch === generation.current) {inFlight.current = false; setSubmitting(false);} }
  }

  async function send(input: string, sessionId: string | null, preserveDraft = false) {
    if (inFlight.current || submitting || pending || !input.trim()) return;
    const submitted = {submission_id: crypto.randomUUID(), input, session_id: sessionId, preserve_draft: preserveDraft};
    setPending(submitted);
    await perform(submitted, false);
  }

  function reset() {
    ++generation.current; inFlight.current = false;
    setPending(null); setSubmitting(false); setError('');
  }

  return {pending, submitting, error, send, reset,
    check: () => pending && !submitting ? perform(pending, true) : Promise.resolve(),
    retry: () => pending && !submitting ? perform(pending, false) : Promise.resolve(),
  };
}
