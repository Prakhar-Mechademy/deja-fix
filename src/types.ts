export type MessageRole = 'user' | 'assistant';

export type MessageRoute =
  | 'user'
  | 'hub'
  | 'clarify'
  | 'escalation'
  | 'greeting'
  | 'declined'
  | 'error';

export type SessionStatus = 'active' | 'resolved' | 'escalated' | 'unconfirmed';

export interface UserRecord {
  id: number;
  handle: string;
  display_name: string;
  created_at: string;
}

export interface SessionRecord {
  id: number;
  user_id: number;
  title: string;
  status: SessionStatus;
  memory_summary: string | null;
  created_at: string;
  updated_at: string;
  user_handle?: string;
  user_display_name?: string;
  message_count?: number;
  tokens_in_total?: number;
  tokens_out_total?: number;
  cost_total_usd?: number;
}

export interface MessageRecord {
  id: number;
  session_id: number;
  user_id: number;
  role: MessageRole;
  route: MessageRoute;
  content: string;
  created_at: string;
}

export interface SolutionRecord {
  id: number;
  source_session_id: number;
  verified: number;
  symptoms: string;
  device: string;
  os: string;
  app_version: string;
  fix_steps: string[];
  what_did_not_work: string[];
  confirmation_count: number;
  created_at: string;
  updated_at: string;
}

export interface TokenLogRecord {
  id: number;
  session_id: number;
  user_id: number;
  call_type: string;
  model: string;
  tokens_in: number;
  tokens_out: number;
  estimated_cost_usd: number;
  created_at: string;
}

export interface SessionTokenSummary {
  session_id: number;
  session_title: string;
  user_handle: string;
  user_display_name: string;
  status: SessionStatus;
  had_escalation: boolean;
  had_hub_reuse: boolean;
  calls_count: number;
  tokens_in: number;
  tokens_out: number;
  total_tokens: number;
  estimated_cost_usd: number;
}

export interface RateLimitStatus {
  messages: { used: number; limit: number; remaining: number };
  escalations: { used: number; limit: number; remaining: number };
}

export interface AppStateResponse {
  users: UserRecord[];
  sessions: SessionRecord[];
  solutions: SolutionRecord[];
  tokenLogs: TokenLogRecord[];
  sessionTokenSummaries: SessionTokenSummary[];
  rateLimit: RateLimitStatus | null;
}

export interface DemoReport {
  session1: { id: number; tokens: number; costUsd: number };
  session2: { id: number; tokens: number; costUsd: number };
  session3: { id: number; greeting: string };
  solutionsCount: number;
  assertions: Array<{ name: string; passed: boolean; detail: string }>;
}
