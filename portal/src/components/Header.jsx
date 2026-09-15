import React from 'react';
import { Activity, Shield, RefreshCw, Hospital, Radio, Command, Search } from 'lucide-react';
import { toast } from './Toast';

export function Header({
  title,
  subtitle,
  sseConnected,
  onRefresh,
  isRefreshing,
  tenantName = 'Apollo Metro Hospital',
  onOpenCommandPalette,
}) {
  return (
    <header className="h-16 bg-white/80 backdrop-blur-md border-b border-slate-200/80 px-6 flex items-center justify-between sticky top-0 z-20 shadow-xs">
      <div>
        <div className="flex items-center gap-2.5">
          <h2 className="text-base font-bold text-slate-900">{title}</h2>
          <span className="hidden md:inline-block px-2.5 py-0.5 rounded-full text-[10px] font-bold bg-slate-100 text-slate-700 border border-slate-200">
            {tenantName}
          </span>
        </div>
        {subtitle && <p className="text-xs text-slate-500 truncate max-w-xl">{subtitle}</p>}
      </div>

      <div className="flex items-center gap-3">
        {/* Global Command Palette Trigger Button */}
        <button
          onClick={onOpenCommandPalette}
          className="hidden sm:flex items-center gap-2 px-3 py-1.5 rounded-xl bg-slate-50 hover:bg-slate-100 border border-slate-200 text-xs text-slate-600 hover:text-slate-900 transition-all shadow-xs group"
        >
          <Search className="w-3.5 h-3.5 text-sky-600" />
          <span className="font-medium">Search & Actions</span>
          <kbd className="font-mono text-[10px] text-slate-500 bg-white px-1.5 py-0.5 rounded border border-slate-200 group-hover:border-slate-300 shadow-xs">
            ⌘K
          </kbd>
        </button>

        {/* SSE Live Connection Status */}
        <div className="flex items-center gap-2 px-3 py-1 rounded-full bg-slate-50 border border-slate-200 text-xs">
          <span className="relative flex h-2 w-2">
            {sseConnected && <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-emerald-500 opacity-75" />}
            <span className={`relative inline-flex rounded-full h-2 w-2 ${sseConnected ? 'bg-emerald-500' : 'bg-amber-500'}`} />
          </span>
          <span className="text-slate-700 font-semibold text-[11px]">
            {sseConnected ? 'Live Telephony Sync' : 'Connecting Stream...'}
          </span>
        </div>

        {/* Security Indicator */}
        <div className="hidden lg:flex items-center gap-1.5 px-2.5 py-1 rounded-xl bg-sky-50 border border-sky-200 text-sky-700 text-xs font-semibold">
          <Shield className="w-3.5 h-3.5" />
          <span>Zero-Trust RBAC</span>
        </div>

        {/* Refresh Action */}
        {onRefresh && (
          <button
            onClick={() => {
              onRefresh();
              toast.info('Central telemetry refreshed from DynamoDB');
            }}
            disabled={isRefreshing}
            className="p-2 text-slate-500 hover:text-slate-900 hover:bg-slate-100 rounded-xl transition-colors border border-slate-200 disabled:opacity-50"
            title="Refresh Central Data"
          >
            <RefreshCw className={`w-4 h-4 ${isRefreshing ? 'animate-spin text-sky-600' : ''}`} />
          </button>
        )}
      </div>
    </header>
  );
}
