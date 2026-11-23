import { useCallback, useEffect, useState } from 'react';
import {
  ApiError,
  createSession,
  fetchSessionDetail,
  fetchState,
  resetDatabase,
  runThreeSessionDemo,
  sendChatMessage,
  submitSessionFeedback,
} from './api';
import { ChatWorkspace } from './components/ChatWorkspace';
import { DemoReportPanel } from './components/DemoReportPanel';
import { InspectorPane, type InspectorTab } from './components/InspectorPane';
import { TopBar, type NavView } from './components/TopBar';
import type {
  DemoReport,
  MessageRecord,
  RateLimitStatus,
  SessionRecord,
  SessionTokenSummary,
  SolutionRecord,
  TokenLogRecord,
  UserRecord,
} from './types';

export default function App() {
  const [activeView, setActiveView] = useState<NavView>('console');
  const [inspectorTab, setInspectorTab] = useState<InspectorTab>('hub');

  const [activeUserHandle, setActiveUserHandle] = useState('customer_a');
  const [activeDisplayName, setActiveDisplayName] = useState('Priya Sharma');

  const [users, setUsers] = useState<UserRecord[]>([]);
  const [allSessions, setAllSessions] = useState<SessionRecord[]>([]);
  const [solutions, setSolutions] = useState<SolutionRecord[]>([]);
  const [includeUnverified, setIncludeUnverified] = useState(false);
  const [tokenLogs, setTokenLogs] = useState<TokenLogRecord[]>([]);
  const [sessionTokenSummaries, setSessionTokenSummaries] = useState<SessionTokenSummary[]>([]);
  const [rateLimit, setRateLimit] = useState<RateLimitStatus | null>(null);

  const [activeSession, setActiveSession] = useState<SessionRecord | null>(null);
  const [messages, setMessages] = useState<MessageRecord[]>([]);
  const [pendingUserMessage, setPendingUserMessage] = useState<string | null>(null);

  const [isLoadingState, setIsLoadingState] = useState(true);
  const [isSending, setIsSending] = useState(false);
  const [isRunningDemo, setIsRunningDemo] = useState(false);
  const [demoReport, setDemoReport] = useState<DemoReport | null>(null);
  const [errorBanner, setErrorBanner] = useState<{
    message: string;
    isRateLimit?: boolean;
    retryAfterSeconds?: number;
  } | null>(null);

  const refreshGlobalState = useCallback(
    async (userHandle = activeUserHandle, showUnverified = includeUnverified) => {
      try {
        const data = await fetchState(undefined, showUnverified);
        setUsers(data.users);
        setAllSessions(data.sessions);
        setSolutions(data.solutions);
        setTokenLogs(data.tokenLogs);
        setSessionTokenSummaries(data.sessionTokenSummaries);

        // Fetch rate limit status specifically for active user if persisted
        const activeU = data.users.find((u) => u.handle === userHandle);
        if (activeU) {
          const userScoped = await fetchState(userHandle, showUnverified);
          setRateLimit(userScoped.rateLimit);
        } else {
          setRateLimit(null);
        }
        return data;
      } catch (err) {
        setErrorBanner({
          message: err instanceof Error ? err.message : 'Unable to load workspace state.',
        });
        return null;
      } finally {
        setIsLoadingState(false);
      }
    },
    [activeUserHandle, includeUnverified],
  );

  useEffect(() => {
    void refreshGlobalState();
  }, [refreshGlobalState]);

  const loadSession = useCallback(async (sessionId: number) => {
    const detail = await fetchSessionDetail(sessionId);
    setActiveSession(detail.session);
    setMessages(detail.messages);
    if (detail.session.user_handle) {
      setActiveUserHandle(detail.session.user_handle);
      setActiveDisplayName(detail.session.user_display_name || detail.session.user_handle);
    }
  }, []);

  const handleSelectNavView = (view: NavView) => {
    setActiveView(view);
    if (view === 'hub') setInspectorTab('hub');
    if (view === 'tokens') setInspectorTab('tokens');
    if (view === 'sessions') setInspectorTab('sessions');
  };

  const handleSelectCustomer = async (handle: string, displayName: string) => {
    setErrorBanner(null);
    setActiveUserHandle(handle);
    setActiveDisplayName(displayName);

    const existingForUser = allSessions.filter((s) => s.user_handle === handle);
    if (existingForUser.length > 0) {
      await loadSession(existingForUser[0].id);
    } else {
      setActiveSession(null);
      setMessages([]);
    }
    await refreshGlobalState(handle, includeUnverified);
  };

  const handleNewSession = async () => {
    setErrorBanner(null);
    try {
      const created = await createSession({
        userHandle: activeUserHandle,
        displayName: activeDisplayName,
      });
      setActiveSession(created.session);
      setMessages(created.messages);
      await refreshGlobalState(activeUserHandle, includeUnverified);
    } catch (err) {
      setErrorBanner({
        message: err instanceof Error ? err.message : 'Failed to create a new session.',
      });
    }
  };

  const handleSendMessage = async (text: string) => {
    setErrorBanner(null);
    setPendingUserMessage(text);
    setIsSending(true);
    try {
      const result = await sendChatMessage({
        userHandle: activeUserHandle,
        displayName: activeDisplayName,
        sessionId: activeSession?.id,
        message: text,
      });
      setPendingUserMessage(null);
      setActiveSession(result.session);
      setMessages(result.messages);
      setRateLimit(result.rateLimit);
      await refreshGlobalState(activeUserHandle, includeUnverified);
    } catch (err) {
      setPendingUserMessage(null);
      if (err instanceof ApiError && err.status === 429) {
        setErrorBanner({
          message: err.message,
          isRateLimit: true,
          retryAfterSeconds: err.retryAfterSeconds,
        });
      } else {
        setErrorBanner({
          message:
            err instanceof Error
              ? err.message
              : 'Unable to reach the support assistant right now.',
        });
      }
      await refreshGlobalState(activeUserHandle, includeUnverified);
    } finally {
      setIsSending(false);
    }
  };

  const handleFeedback = async (outcome: 'confirmed' | 'failed') => {
    if (!activeSession) return;
    setErrorBanner(null);
    setIsSending(true);
    try {
      const res = await submitSessionFeedback(activeSession.id, outcome);
      setActiveSession(res.session);
      setMessages(res.messages);
      await refreshGlobalState(activeUserHandle, includeUnverified);
    } catch (err) {
      if (err instanceof ApiError && err.status === 429) {
        setErrorBanner({
          message: err.message,
          isRateLimit: true,
          retryAfterSeconds: err.retryAfterSeconds,
        });
      } else {
        setErrorBanner({
          message: err instanceof Error ? err.message : 'Unable to save session feedback.',
        });
      }
    } finally {
      setIsSending(false);
    }
  };

  const handleRunDemo = async () => {
    setErrorBanner(null);
    setIsRunningDemo(true);
    try {
      const report = await runThreeSessionDemo(true);
      setDemoReport(report);
      setActiveView('console');
      setInspectorTab('tokens');
      await refreshGlobalState('customer_a', includeUnverified);
      await loadSession(report.session3.id);
    } catch (err) {
      setErrorBanner({
        message: err instanceof Error ? err.message : 'Failed to run 3-session demo.',
      });
    } finally {
      setIsRunningDemo(false);
    }
  };

  const handleResetDatabase = async () => {
    setErrorBanner(null);
    setDemoReport(null);
    await resetDatabase();
    setActiveSession(null);
    setMessages([]);
    setRateLimit(null);
    await refreshGlobalState(activeUserHandle, includeUnverified);
  };

  const handleToggleIncludeUnverified = async (next: boolean) => {
    setIncludeUnverified(next);
    await refreshGlobalState(activeUserHandle, next);
  };

  const userSessions = allSessions.filter((s) => s.user_handle === activeUserHandle);

  return (
    <div className="min-h-screen bg-slate-50 text-slate-900 flex flex-col">
      <TopBar
        activeView={activeView}
        onSelectView={handleSelectNavView}
        onRunDemo={handleRunDemo}
        isRunningDemo={isRunningDemo}
      />

      <main className="flex-1 max-w-[1440px] w-full mx-auto px-6 py-6 space-y-6">
        {demoReport && (
          <DemoReportPanel
            report={demoReport}
            onClose={() => setDemoReport(null)}
            onInspectSession={async (id) => {
              setActiveView('console');
              await loadSession(id);
            }}
          />
        )}

        {activeView === 'console' ? (
          <div className="grid grid-cols-1 lg:grid-cols-12 gap-6 items-stretch">
            <div className="lg:col-span-7">
              <ChatWorkspace
                activeUserHandle={activeUserHandle}
                activeDisplayName={activeDisplayName}
                users={users}
                userSessions={userSessions}
                activeSession={activeSession}
                messages={messages}
                rateLimit={rateLimit}
                isSending={isSending}
                pendingUserMessage={pendingUserMessage}
                errorBanner={errorBanner}
                onSelectCustomer={handleSelectCustomer}
                onNewSession={handleNewSession}
                onSelectSession={(id) => void loadSession(id)}
                onSendMessage={(text) => void handleSendMessage(text)}
                onFeedback={(outcome) => void handleFeedback(outcome)}
                onDismissError={() => setErrorBanner(null)}
              />
            </div>
            <div className="lg:col-span-5">
              <InspectorPane
                activeTab={inspectorTab}
                onSelectTab={setInspectorTab}
                solutions={solutions}
                includeUnverified={includeUnverified}
                onToggleIncludeUnverified={(next) => void handleToggleIncludeUnverified(next)}
                sessions={allSessions}
                sessionTokenSummaries={sessionTokenSummaries}
                tokenLogs={tokenLogs}
                activeSessionId={activeSession?.id ?? null}
                onResumeSession={async (s) => {
                  await loadSession(s.id);
                }}
                onResetDatabase={() => void handleResetDatabase()}
                isLoading={isLoadingState}
              />
            </div>
          </div>
        ) : (
          <InspectorPane
            activeTab={inspectorTab}
            onSelectTab={(tab) => {
              setInspectorTab(tab);
              setActiveView(tab);
            }}
            solutions={solutions}
            includeUnverified={includeUnverified}
            onToggleIncludeUnverified={(next) => void handleToggleIncludeUnverified(next)}
            sessions={allSessions}
            sessionTokenSummaries={sessionTokenSummaries}
            tokenLogs={tokenLogs}
            activeSessionId={activeSession?.id ?? null}
            onResumeSession={async (s) => {
              await loadSession(s.id);
              setActiveView('console');
            }}
            onResetDatabase={() => void handleResetDatabase()}
            isLoading={isLoadingState}
            fullWidth
          />
        )}
      </main>
    </div>
  );
}
