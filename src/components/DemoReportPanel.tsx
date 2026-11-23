import React from 'react';
import { CheckCircle2, X, XCircle } from 'lucide-react';
import type { DemoReport } from '../types';

interface DemoReportPanelProps {
  report: DemoReport;
  onClose: () => void;
  onInspectSession: (sessionId: number) => void;
}

export const DemoReportPanel: React.FC<DemoReportPanelProps> = ({
  report,
  onClose,
  onInspectSession,
}) => {
  const tokenSavingsPct =
    report.session1.tokens > 0
      ? Math.round(((report.session1.tokens - report.session2.tokens) / report.session1.tokens) * 100)
      : 0;

  return (
    <section
      aria-label="Three-session scenario verification results"
      className="bg-white border border-slate-200 rounded-xl p-5"
    >
      <div className="flex items-start justify-between gap-4 pb-4 border-b border-slate-200">
        <div>
          <h2 className="text-base font-semibold text-slate-900">
            Three-Session Live Verification Complete
          </h2>
          <p className="text-sm text-slate-600 mt-0.5">
            Executed against a clean empty database: Escalation + Extraction, Hub Reuse, and Isolated Returning Memory.
          </p>
        </div>
        <button
          type="button"
          onClick={onClose}
          aria-label="Dismiss scenario report"
          className="p-1.5 text-slate-500 hover:text-slate-900 rounded-lg hover:bg-slate-100 transition-colors"
        >
          <X className="w-4 h-4" />
        </button>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-3 gap-4 py-4 border-b border-slate-200">
        <div className="p-3 bg-slate-50 rounded-lg border border-slate-200">
          <div className="text-xs font-medium text-slate-500">Session 1 · Escalation + Extract</div>
          <div className="mt-1 flex items-baseline justify-between">
            <span className="font-mono text-lg font-semibold text-slate-900 tabular-nums">
              {report.session1.tokens.toLocaleString()} tokens
            </span>
            <button
              type="button"
              onClick={() => onInspectSession(report.session1.id)}
              className="text-xs font-medium text-blue-600 hover:underline whitespace-nowrap"
            >
              Inspect #{report.session1.id}
            </button>
          </div>
          <div className="font-mono text-xs text-slate-500 tabular-nums mt-0.5">
            ${report.session1.costUsd.toFixed(6)} estimated
          </div>
        </div>

        <div className="p-3 bg-slate-50 rounded-lg border border-slate-200">
          <div className="text-xs font-medium text-slate-500">Session 2 · Hub Reuse</div>
          <div className="mt-1 flex items-baseline justify-between">
            <span className="font-mono text-lg font-semibold text-emerald-700 tabular-nums">
              {report.session2.tokens.toLocaleString()} tokens
            </span>
            <button
              type="button"
              onClick={() => onInspectSession(report.session2.id)}
              className="text-xs font-medium text-blue-600 hover:underline whitespace-nowrap"
            >
              Inspect #{report.session2.id}
            </button>
          </div>
          <div className="font-mono text-xs text-emerald-700 tabular-nums mt-0.5">
            {tokenSavingsPct > 0 ? `${tokenSavingsPct}% fewer tokens than Session 1` : 'Lower cost than Session 1'}
          </div>
        </div>

        <div className="p-3 bg-slate-50 rounded-lg border border-slate-200">
          <div className="text-xs font-medium text-slate-500">Session 3 · Isolated Memory</div>
          <div className="mt-1 flex items-baseline justify-between">
            <span className="text-sm font-semibold text-slate-900">Customer A Returned</span>
            <button
              type="button"
              onClick={() => onInspectSession(report.session3.id)}
              className="text-xs font-medium text-blue-600 hover:underline whitespace-nowrap"
            >
              Inspect #{report.session3.id}
            </button>
          </div>
          <div className="text-xs text-slate-600 mt-1 line-clamp-2">{report.session3.greeting}</div>
        </div>
      </div>

      <ul className="pt-4 grid grid-cols-1 lg:grid-cols-2 gap-2.5">
        {report.assertions.map((item) => (
          <li key={item.name} className="flex items-start gap-2.5 text-xs">
            {item.passed ? (
              <CheckCircle2 className="w-4 h-4 text-emerald-600 shrink-0 mt-0.5" aria-hidden="true" />
            ) : (
              <XCircle className="w-4 h-4 text-red-600 shrink-0 mt-0.5" aria-hidden="true" />
            )}
            <div>
              <div className="font-medium text-slate-900">{item.name}</div>
              <div className="text-slate-500 font-mono mt-0.5">{item.detail}</div>
            </div>
          </li>
        ))}
      </ul>
    </section>
  );
};
