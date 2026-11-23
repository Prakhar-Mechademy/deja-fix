import React, { useMemo, useState } from 'react';
import { Database, RotateCcw, Search } from 'lucide-react';
import type {
  SessionRecord,
  SessionTokenSummary,
  SolutionRecord,
  TokenLogRecord,
} from '../types';

export type InspectorTab = 'hub' | 'tokens' | 'sessions';

interface InspectorPaneProps {
  activeTab: InspectorTab;
  onSelectTab: (tab: InspectorTab) => void;
  solutions: SolutionRecord[];
  includeUnverified: boolean;
  onToggleIncludeUnverified: (next: boolean) => void;
  sessions: SessionRecord[];
  sessionTokenSummaries: SessionTokenSummary[];
  tokenLogs: TokenLogRecord[];
  activeSessionId: number | null;
  onResumeSession: (session: SessionRecord) => void;
  onResetDatabase: () => void;
  isLoading: boolean;
  fullWidth?: boolean;
}

export const InspectorPane: React.FC<InspectorPaneProps> = ({
  activeTab,
  onSelectTab,
  solutions,
  includeUnverified,
  onToggleIncludeUnverified,
  sessions,
  sessionTokenSummaries,
  tokenLogs,
  activeSessionId,
  onResumeSession,
  onResetDatabase,
  isLoading,
  fullWidth = false,
}) => {
  const [searchQuery, setSearchQuery] = useState('');
  const [confirmReset, setConfirmReset] = useState(false);

  const filteredSolutions = useMemo(() => {
    const q = searchQuery.trim().toLowerCase();
    if (!q) return solutions;
    return solutions.filter(
      (s) =>
        s.symptoms.toLowerCase().includes(q) ||
        s.os.toLowerCase().includes(q) ||
        s.device.toLowerCase().includes(q) ||
        s.app_version.toLowerCase().includes(q) ||
        s.fix_steps.some((step) => step.toLowerCase().includes(q)),
    );
  }, [solutions, searchQuery]);

  const escalatedAvgTokens = useMemo(() => {
    const esc = sessionTokenSummaries.filter((s) => s.had_escalation);
    if (esc.length === 0) return null;
    return Math.round(esc.reduce((acc, s) => acc + s.total_tokens, 0) / esc.length);
  }, [sessionTokenSummaries]);

  const hubAvgTokens = useMemo(() => {
    const hub = sessionTokenSummaries.filter((s) => s.had_hub_reuse && !s.had_escalation);
    if (hub.length === 0) return null;
    return Math.round(hub.reduce((acc, s) => acc + s.total_tokens, 0) / hub.length);
  }, [sessionTokenSummaries]);

  return (
    <section
      aria-label="System inspector"
      className={`bg-white border border-slate-200 rounded-xl flex flex-col ${
        fullWidth ? 'min-h-[600px]' : 'h-[calc(100vh-12rem)] min-h-[580px]'
      }`}
    >
      {/* Inspector Header & Segmented Tabs */}
      <div className="px-5 py-3.5 border-b border-slate-200 flex flex-wrap items-center justify-between gap-3">
        <div
          role="tablist"
          aria-label="Inspector views"
          className="inline-flex items-center p-1 bg-slate-100 rounded-lg"
        >
          <button
            type="button"
            role="tab"
            aria-selected={activeTab === 'hub'}
            onClick={() => onSelectTab('hub')}
            className={`px-3 py-1.5 text-xs font-medium rounded-md transition-colors whitespace-nowrap shrink-0 ${
              activeTab === 'hub'
                ? 'bg-white text-slate-900 shadow-sm'
                : 'text-slate-600 hover:text-slate-900'
            }`}
          >
            Knowledge Hub ({solutions.length})
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={activeTab === 'tokens'}
            onClick={() => onSelectTab('tokens')}
            className={`px-3 py-1.5 text-xs font-medium rounded-md transition-colors whitespace-nowrap shrink-0 ${
              activeTab === 'tokens'
                ? 'bg-white text-slate-900 shadow-sm'
                : 'text-slate-600 hover:text-slate-900'
            }`}
          >
            Token Ledger
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={activeTab === 'sessions'}
            onClick={() => onSelectTab('sessions')}
            className={`px-3 py-1.5 text-xs font-medium rounded-md transition-colors whitespace-nowrap shrink-0 ${
              activeTab === 'sessions'
                ? 'bg-white text-slate-900 shadow-sm'
                : 'text-slate-600 hover:text-slate-900'
            }`}
          >
            Sessions ({sessions.length})
          </button>
        </div>

        <div className="flex items-center gap-2">
          {!confirmReset ? (
            <button
              type="button"
              onClick={() => setConfirmReset(true)}
              className="inline-flex items-center gap-1.5 px-2.5 py-1.5 text-xs font-medium text-slate-600 hover:text-red-700 hover:bg-red-50 rounded-lg transition-colors whitespace-nowrap shrink-0"
            >
              <RotateCcw className="w-3.5 h-3.5" aria-hidden="true" />
              <span>Reset DB</span>
            </button>
          ) : (
            <div className="flex items-center gap-1.5">
              <button
                type="button"
                onClick={() => {
                  setConfirmReset(false);
                  onResetDatabase();
                }}
                className="px-2.5 py-1 text-xs font-medium text-white bg-red-600 hover:bg-red-700 rounded-md whitespace-nowrap"
              >
                Confirm Empty Reset
              </button>
              <button
                type="button"
                onClick={() => setConfirmReset(false)}
                className="px-2 py-1 text-xs font-medium text-slate-600 hover:text-slate-900"
              >
                Cancel
              </button>
            </div>
          )}
        </div>
      </div>

      {/* Tab Content */}
      <div className="flex-1 overflow-y-auto p-5">
        {isLoading ? (
          <div className="space-y-3">
            <div className="h-16 bg-slate-100 rounded-lg animate-pulse" />
            <div className="h-16 bg-slate-100 rounded-lg animate-pulse" />
            <div className="h-16 bg-slate-100 rounded-lg animate-pulse" />
          </div>
        ) : activeTab === 'hub' ? (
          <div className="space-y-4">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <div className="relative flex-1 min-w-[200px]">
                <Search
                  className="w-3.5 h-3.5 text-slate-400 absolute left-3 top-1/2 -translate-y-1/2"
                  aria-hidden="true"
                />
                <input
                  type="text"
                  value={searchQuery}
                  onChange={(e) => setSearchQuery(e.target.value)}
                  placeholder="Filter solutions by symptom, OS, or version..."
                  className="w-full h-9 pl-8 pr-3 text-xs bg-slate-50 border border-slate-200 rounded-lg text-slate-900 placeholder:text-slate-400 focus:bg-white focus:outline-none focus:ring-2 focus:ring-blue-600"
                />
              </div>
              <button
                type="button"
                onClick={() => onToggleIncludeUnverified(!includeUnverified)}
                className={`h-9 px-3 text-xs font-medium rounded-lg border transition-colors whitespace-nowrap ${
                  includeUnverified
                    ? 'bg-slate-900 text-white border-slate-900'
                    : 'bg-white text-slate-600 border-slate-200 hover:text-slate-900'
                }`}
              >
                {includeUnverified ? 'Showing All' : 'Verified Only'}
              </button>
            </div>

            {filteredSolutions.length === 0 ? (
              <div className="py-12 px-4 text-center border border-dashed border-slate-200 rounded-xl">
                <Database className="w-6 h-6 text-slate-400 mx-auto mb-2" aria-hidden="true" />
                <h3 className="text-sm font-semibold text-slate-900">
                  Knowledge Hub is empty
                </h3>
                <p className="text-xs text-slate-600 mt-1 max-w-sm mx-auto leading-relaxed">
                  Starts with zero seed data. Escalate a problem and confirm that the fix worked to extract Solution 1 without personal data.
                </p>
              </div>
            ) : (
              <div className="divide-y divide-slate-200 border border-slate-200 rounded-xl overflow-hidden">
                {filteredSolutions.map((sol) => (
                  <article key={sol.id} className="p-4 bg-white">
                    <div className="flex flex-wrap items-baseline justify-between gap-2">
                      <h3 className="text-sm font-semibold text-slate-900">
                        {sol.symptoms}
                      </h3>
                      <span className="font-mono text-xs text-emerald-700 font-medium tabular-nums whitespace-nowrap">
                        {sol.verified ? 'Verified' : 'Unverified'} · Confirmed {sol.confirmation_count}x
                      </span>
                    </div>

                    <div className="mt-1.5 flex flex-wrap items-center gap-2 text-xs text-slate-500">
                      <span className="font-mono tabular-nums">Solution #{sol.id}</span>
                      <span aria-hidden="true">·</span>
                      <span className="font-mono tabular-nums">Source Session #{sol.source_session_id}</span>
                      <span aria-hidden="true">·</span>
                      <span>
                        {sol.device} / {sol.os} / {sol.app_version}
                      </span>
                    </div>

                    <div className="mt-3 space-y-2 text-xs">
                      <div>
                        <div className="font-medium text-slate-700 mb-1">Verified Fix Steps</div>
                        <ol className="list-decimal list-inside space-y-1 text-slate-600">
                          {sol.fix_steps.map((step, idx) => (
                            <li key={idx}>{step}</li>
                          ))}
                        </ol>
                      </div>

                      {sol.what_did_not_work.length > 0 && (
                        <div className="pt-1">
                          <span className="font-medium text-slate-700">What did not work: </span>
                          <span className="text-slate-600">
                            {sol.what_did_not_work.join('; ')}
                          </span>
                        </div>
                      )}
                    </div>
                  </article>
                ))}
              </div>
            )}
          </div>
        ) : activeTab === 'tokens' ? (
          <div className="space-y-5">
            {/* Cost Comparison Strip */}
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
              <div className="p-3.5 bg-slate-50 border border-slate-200 rounded-lg">
                <div className="text-xs text-slate-500">Avg. Escalated Session</div>
                <div className="font-mono text-lg font-semibold text-slate-900 tabular-nums mt-0.5">
                  {escalatedAvgTokens !== null ? `${escalatedAvgTokens.toLocaleString()} tokens` : '—'}
                </div>
              </div>
              <div className="p-3.5 bg-slate-50 border border-slate-200 rounded-lg">
                <div className="text-xs text-slate-500">Avg. Hub-Served Session</div>
                <div className="font-mono text-lg font-semibold text-emerald-700 tabular-nums mt-0.5">
                  {hubAvgTokens !== null ? `${hubAvgTokens.toLocaleString()} tokens` : '—'}
                </div>
              </div>
            </div>

            {sessionTokenSummaries.length === 0 ? (
              <div className="py-10 text-center text-xs text-slate-500 border border-dashed border-slate-200 rounded-xl">
                No LLM token calls logged yet. Send a chat message or run the 3-session demo.
              </div>
            ) : (
              <>
                <div>
                  <h3 className="text-xs font-semibold text-slate-700 mb-2">
                    Session Token Totals
                  </h3>
                  <div className="overflow-x-auto border border-slate-200 rounded-xl">
                    <table className="w-full text-xs text-left">
                      <thead className="bg-slate-50 border-b border-slate-200 text-slate-600">
                        <tr>
                          <th className="py-2.5 px-3 font-medium">Session</th>
                          <th className="py-2.5 px-3 font-medium">Path</th>
                          <th className="py-2.5 px-3 font-medium text-right">In</th>
                          <th className="py-2.5 px-3 font-medium text-right">Out</th>
                          <th className="py-2.5 px-3 font-medium text-right">Total</th>
                          <th className="py-2.5 px-3 font-medium text-right">Est. USD</th>
                        </tr>
                      </thead>
                      <tbody className="divide-y divide-slate-200">
                        {sessionTokenSummaries.map((row) => (
                          <tr key={row.session_id} className="hover:bg-slate-50">
                            <td className="py-2.5 px-3">
                              <div className="font-mono font-medium text-slate-900 tabular-nums">
                                #{row.session_id} · {row.user_display_name}
                              </div>
                            </td>
                            <td className="py-2.5 px-3 whitespace-nowrap">
                              {row.had_escalation ? (
                                <span className="text-red-700 font-medium">Escalated</span>
                              ) : row.had_hub_reuse ? (
                                <span className="text-emerald-700 font-medium">Hub Grounded</span>
                              ) : (
                                <span className="text-slate-500">Greeting / Open</span>
                              )}
                            </td>
                            <td className="py-2.5 px-3 text-right font-mono tabular-nums text-slate-700">
                              {row.tokens_in.toLocaleString()}
                            </td>
                            <td className="py-2.5 px-3 text-right font-mono tabular-nums text-slate-700">
                              {row.tokens_out.toLocaleString()}
                            </td>
                            <td className="py-2.5 px-3 text-right font-mono font-semibold tabular-nums text-slate-900">
                              {row.total_tokens.toLocaleString()}
                            </td>
                            <td className="py-2.5 px-3 text-right font-mono tabular-nums text-slate-600">
                              ${row.estimated_cost_usd.toFixed(6)}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </div>

                <div>
                  <h3 className="text-xs font-semibold text-slate-700 mb-2">
                    Per-Call LLM Log ({tokenLogs.length})
                  </h3>
                  <div className="overflow-x-auto border border-slate-200 rounded-xl">
                    <table className="w-full text-xs text-left">
                      <thead className="bg-slate-50 border-b border-slate-200 text-slate-600">
                        <tr>
                          <th className="py-2 px-3 font-medium">Session</th>
                          <th className="py-2 px-3 font-medium">Call Type</th>
                          <th className="py-2 px-3 font-medium">Model</th>
                          <th className="py-2 px-3 font-medium text-right">In / Out</th>
                        </tr>
                      </thead>
                      <tbody className="divide-y divide-slate-200">
                        {tokenLogs.slice(0, 40).map((log) => (
                          <tr key={log.id} className="hover:bg-slate-50">
                            <td className="py-2 px-3 font-mono tabular-nums text-slate-700">
                              #{log.session_id}
                            </td>
                            <td className="py-2 px-3 font-mono text-slate-800">{log.call_type}</td>
                            <td className="py-2 px-3 font-mono text-slate-500">{log.model}</td>
                            <td className="py-2 px-3 text-right font-mono tabular-nums text-slate-700">
                              {log.tokens_in} / {log.tokens_out}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </div>
              </>
            )}
          </div>
        ) : (
          <div className="space-y-3">
            {sessions.length === 0 ? (
              <div className="py-12 text-center text-xs text-slate-500 border border-dashed border-slate-200 rounded-xl">
                No sessions recorded yet. Start a chat to persist users, sessions, and messages to SQLite.
              </div>
            ) : (
              <div className="divide-y divide-slate-200 border border-slate-200 rounded-xl overflow-hidden">
                {sessions.map((s) => {
                  const isCurrent = s.id === activeSessionId;
                  return (
                    <div
                      key={s.id}
                      className={`p-3.5 flex items-center justify-between gap-3 ${
                        isCurrent ? 'bg-blue-50/40' : 'bg-white'
                      }`}
                    >
                      <div className="min-w-0">
                        <div className="flex items-center gap-2 text-xs">
                          <span className="font-mono font-semibold text-slate-900 tabular-nums">
                            Session #{s.id}
                          </span>
                          <span aria-hidden="true" className="text-slate-300">
                            ·
                          </span>
                          <span className="font-medium text-slate-700 truncate">
                            {s.user_display_name} ({s.user_handle})
                          </span>
                          <span aria-hidden="true" className="text-slate-300">
                            ·
                          </span>
                          <span className="capitalize text-slate-600">{s.status}</span>
                        </div>
                        <div className="text-sm font-medium text-slate-900 truncate mt-0.5">
                          {s.title}
                        </div>
                        <div className="text-xs text-slate-500 font-mono tabular-nums mt-0.5">
                          {s.message_count ?? 0} messages ·{' '}
                          {((s.tokens_in_total ?? 0) + (s.tokens_out_total ?? 0)).toLocaleString()} tokens
                        </div>
                      </div>
                      <button
                        type="button"
                        onClick={() => onResumeSession(s)}
                        className="px-3 py-1.5 text-xs font-medium text-blue-700 bg-blue-50 hover:bg-blue-100 rounded-lg transition-colors whitespace-nowrap shrink-0"
                      >
                        {isCurrent ? 'Active' : 'Resume'}
                      </button>
                    </div>
                  );
                })}
              </div>
            )}
          </div>
        )}
      </div>
    </section>
  );
};
