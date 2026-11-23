import React from 'react';
import { Play } from 'lucide-react';

export type NavView = 'console' | 'hub' | 'sessions' | 'tokens';

interface TopBarProps {
  activeView: NavView;
  onSelectView: (view: NavView) => void;
  onRunDemo: () => void;
  isRunningDemo: boolean;
}

const NAV_ITEMS: Array<{ id: NavView; label: string }> = [
  { id: 'console', label: 'Support Console' },
  { id: 'hub', label: 'Knowledge Hub' },
  { id: 'sessions', label: 'Sessions' },
  { id: 'tokens', label: 'Token Ledger' },
];

export const TopBar: React.FC<TopBarProps> = ({
  activeView,
  onSelectView,
  onRunDemo,
  isRunningDemo,
}) => {
  return (
    <header className="sticky top-0 z-30 bg-white/95 backdrop-blur border-b border-slate-200">
      <div className="max-w-[1440px] mx-auto px-6 h-16 flex items-center justify-between gap-8">
        <button
          type="button"
          onClick={() => onSelectView('console')}
          className="text-lg font-bold tracking-tight text-slate-900 whitespace-nowrap shrink-0 focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-blue-600"
        >
          Déjà Fix
        </button>

        <nav aria-label="Primary navigation" className="hidden md:flex items-center gap-6">
          {NAV_ITEMS.map((item) => {
            const active = activeView === item.id;
            return (
              <button
                key={item.id}
                type="button"
                onClick={() => onSelectView(item.id)}
                className={`text-sm font-medium whitespace-nowrap shrink-0 py-1 border-b-2 transition-colors focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-blue-600 ${
                  active
                    ? 'text-blue-600 border-blue-600'
                    : 'text-slate-600 border-transparent hover:text-slate-900 hover:border-slate-300'
                }`}
              >
                {item.label}
              </button>
            );
          })}
        </nav>

        <div className="flex items-center shrink-0">
          <button
            type="button"
            onClick={onRunDemo}
            disabled={isRunningDemo}
            className="inline-flex items-center gap-2 px-4 py-2 text-xs font-semibold text-white bg-blue-600 rounded-lg hover:bg-blue-700 disabled:opacity-60 disabled:cursor-not-allowed transition-colors whitespace-nowrap shrink-0 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-blue-600"
          >
            <Play className="w-3.5 h-3.5" aria-hidden="true" />
            <span>{isRunningDemo ? 'Running Demo...' : 'Run 3-Session Demo'}</span>
          </button>
        </div>
      </div>

      <nav
        aria-label="Mobile navigation"
        className="flex md:hidden items-center gap-4 px-6 pb-2.5 overflow-x-auto border-t border-slate-100"
      >
        {NAV_ITEMS.map((item) => {
          const active = activeView === item.id;
          return (
            <button
              key={item.id}
              type="button"
              onClick={() => onSelectView(item.id)}
              className={`text-xs font-medium whitespace-nowrap shrink-0 py-1.5 border-b-2 transition-colors ${
                active ? 'text-blue-600 border-blue-600' : 'text-slate-600 border-transparent'
              }`}
            >
              {item.label}
            </button>
          );
        })}
      </nav>
    </header>
  );
};
