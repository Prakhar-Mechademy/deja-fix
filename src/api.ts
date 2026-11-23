import type {
  AppStateResponse,
  DemoReport,
  MessageRecord,
  RateLimitStatus,
  SessionRecord,
  TokenLogRecord,
  UserRecord,
} from './types';

export class ApiError extends Error {
  status: number;
  code?: string;
  retryAfterSeconds?: number;

  constructor(message: string, status: number, code?: string, retryAfterSeconds?: number) {
    super(message);
    this.status = status;
    this.code = code;
    this.retryAfterSeconds = retryAfterSeconds;
  }
}

async function parseResponse<T>(res: Response): Promise<T> {
  const text = await res.text();
  let json: Record<string, unknown> = {};
  if (text) {
    try {
      json = JSON.parse(text) as Record<string, unknown>;
    } catch {
      throw new ApiError('Server returned a malformed response.', res.status);
    }
  }
  if (!res.ok) {
    throw new ApiError(
      typeof json.error === 'string' ? json.error : `Request failed with status ${res.status}`,
      res.status,
      typeof json.code === 'string' ? json.code : undefined,
      typeof json.retryAfterSeconds === 'number' ? json.retryAfterSeconds : undefined,
    );
  }
  return json as T;
}

export async function fetchState(userHandle?: string, includeUnverified = false): Promise<AppStateResponse> {
  const params = new URLSearchParams();
  if (userHandle) params.set('userHandle', userHandle);
  if (includeUnverified) params.set('includeUnverified', 'true');
  const res = await fetch(`/api/state?${params.toString()}`);
  return parseResponse<AppStateResponse>(res);
}

export async function createSession(params: {
  userHandle: string;
  displayName?: string;
  title?: string;
}): Promise<{ user: UserRecord; session: SessionRecord; messages: MessageRecord[] }> {
  const res = await fetch('/api/sessions', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(params),
  });
  return parseResponse(res);
}

export async function fetchSessionDetail(sessionId: number): Promise<{
  session: SessionRecord;
  messages: MessageRecord[];
  tokenLogs: TokenLogRecord[];
}> {
  const res = await fetch(`/api/sessions/${sessionId}`);
  return parseResponse(res);
}

export async function sendChatMessage(params: {
  userHandle: string;
  displayName?: string;
  sessionId?: number;
  message: string;
}): Promise<{
  user: UserRecord;
  session: SessionRecord;
  messages: MessageRecord[];
  outcome: string;
  rateLimit: RateLimitStatus;
}> {
  const res = await fetch('/api/chat', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(params),
  });
  return parseResponse(res);
}

export async function submitSessionFeedback(
  sessionId: number,
  outcome: 'confirmed' | 'failed',
): Promise<{
  session: SessionRecord;
  messages: MessageRecord[];
  extractedSolutionsCount: number;
}> {
  const res = await fetch(`/api/sessions/${sessionId}/feedback`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ outcome }),
  });
  return parseResponse(res);
}

export async function runThreeSessionDemo(resetFirst = true): Promise<DemoReport> {
  const res = await fetch('/api/demo/run', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ resetFirst }),
  });
  return parseResponse<DemoReport>(res);
}

export async function resetDatabase(): Promise<{ ok: boolean; message: string }> {
  const res = await fetch('/api/admin/reset', { method: 'POST' });
  return parseResponse(res);
}
