import React, { useState, useEffect } from 'react';
import { ShieldCheck, Lock, Search, Filter, AlertCircle, FileText, ChevronRight, X, Copy } from 'lucide-react';
import { api } from '../api';
import { toast } from '../components/Toast';

export function AuditLogsView({ user }) {
  const [logs, setLogs] = useState([]);
  const [loading, setLoading] = useState(true);
  const [selectedAudit, setSelectedAudit] = useState(null);
  const [search, setSearch] = useState('');

  useEffect(() => {
    loadAuditLogs();
  }, []);

  const loadAuditLogs = async () => {
    try {
      setLoading(true);
      const data = await api.getDashboardStats();
      setLogs(data.recent_activity || []);
    } catch (err) {
      console.error('Failed to load audit logs:', err);
    } finally {
      setLoading(false);
    }
  };

  const filteredLogs = logs.filter((l) => {
    const q = search.toLowerCase();
    return (
      (l.action && l.action.toLowerCase().includes(q)) ||
      (l.user_id && l.user_id.toLowerCase().includes(q)) ||
      (l.resource_type && l.resource_type.toLowerCase().includes(q))
    );
  });

  return (
    <div className="p-6 space-y-6">
      {/* Security Compliance Banner */}
      <div className="p-5 rounded-3xl bg-gradient-to-r from-sky-50 via-white to-white border border-sky-200/80 flex flex-col sm:flex-row items-start sm:items-center justify-between gap-4 shadow-xs">
        <div className="flex items-center gap-3.5">
          <div className="w-10 h-10 rounded-2xl bg-sky-100 border border-sky-200 flex items-center justify-center text-sky-700 font-bold">
            <ShieldCheck className="w-5 h-5" />
          </div>
          <div>
            <h3 className="text-sm font-bold text-slate-900">Application Put-Only Audit Trail</h3>
            <p className="text-xs text-slate-600">
              All clinical mutations, phone unmasking, appointment cancellations, and 403 denied events are permanently recorded.
            </p>
          </div>
        </div>

        <div className="flex items-center gap-2 px-3.5 py-1.5 rounded-2xl bg-slate-100 border border-slate-200 text-xs text-slate-700 font-mono font-semibold">
          <Lock className="w-3.5 h-3.5 text-sky-600" />
          <span>PII & Secrets Auto-Redacted</span>
        </div>
      </div>

      {/* Search Bar */}
      <div className="flex items-center justify-between gap-4 bg-white p-4 rounded-3xl border border-slate-200 shadow-xs">
        <div className="relative w-full sm:w-96">
          <Search className="w-4 h-4 text-slate-400 absolute left-3.5 top-1/2 -translate-y-1/2" />
          <input
            type="text"
            placeholder="Search by action, user ID, resource..."
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            className="w-full bg-slate-50 border border-slate-200 rounded-2xl pl-10 pr-4 py-2.5 text-xs text-slate-900 placeholder-slate-400 focus:outline-none focus:border-sky-500 focus:bg-white font-medium"
          />
        </div>
        <span className="text-xs text-slate-500 font-mono font-semibold">{filteredLogs.length} Events Logged</span>
      </div>

      {/* Audit Logs Table */}
      <div className="bg-white rounded-3xl border border-slate-200 overflow-hidden shadow-xs">
        <div className="overflow-x-auto">
          <table className="w-full text-left text-xs">
            <thead className="bg-slate-50 border-b border-slate-200 text-slate-500 font-bold uppercase text-[10px] tracking-wider">
              <tr>
                <th className="p-4">Action Key</th>
                <th className="p-4">Actor (User ID)</th>
                <th className="p-4">Assigned Role</th>
                <th className="p-4">Resource Target</th>
                <th className="p-4">Event Timestamp</th>
                <th className="p-4">Status</th>
                <th className="p-4 text-right">Details</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100 text-slate-700 font-medium">
              {loading ? (
                <tr>
                  <td colSpan={7} className="text-center py-12 text-slate-400">
                    Loading immutable audit trail...
                  </td>
                </tr>
              ) : filteredLogs.length === 0 ? (
                <tr>
                  <td colSpan={7} className="text-center py-12 text-slate-400">
                    No recent audit events match your search.
                  </td>
                </tr>
              ) : (
                filteredLogs.map((log, idx) => {
                  const isSuccess = log.status === 'SUCCESS';
                  const isDenied = log.status === 'DENIED';

                  return (
                    <tr
                      key={idx}
                      onClick={() => setSelectedAudit(log)}
                      className="hover:bg-slate-50 cursor-pointer transition-colors group"
                    >
                      <td className="p-4 font-mono font-bold text-sky-700">{log.action}</td>
                      <td className="p-4 font-bold text-slate-900">{log.user_id}</td>
                      <td className="p-4">
                        <span className="text-[10px] font-bold uppercase px-2 py-0.5 rounded-md bg-slate-100 text-slate-700">
                          {log.user_role}
                        </span>
                      </td>
                      <td className="p-4 text-slate-600 font-mono">{log.resource_type}</td>
                      <td className="p-4 text-slate-500 font-mono text-[11px]">
                        {log.timestamp ? new Date(log.timestamp).toLocaleString() : 'Just now'}
                      </td>
                      <td className="p-4">
                        <span
                          className={`px-2.5 py-0.5 rounded-full text-[10px] uppercase font-bold border ${
                            isSuccess
                              ? 'bg-emerald-50 text-emerald-700 border-emerald-200'
                              : isDenied
                              ? 'bg-amber-50 text-amber-700 border-amber-200'
                              : 'bg-rose-50 text-rose-700 border-rose-200'
                          }`}
                        >
                          {log.status}
                        </span>
                      </td>
                      <td className="p-4 text-right">
                        <span className="text-sky-600 group-hover:translate-x-1 inline-flex transition-transform">
                          <ChevronRight className="w-4 h-4" />
                        </span>
                      </td>
                    </tr>
                  );
                })
              )}
            </tbody>
          </table>
        </div>
      </div>

      {/* Detailed JSON Inspector Drawer */}
      {selectedAudit && (
        <div className="fixed inset-0 z-50 bg-slate-900/40 backdrop-blur-sm flex justify-end animate-in fade-in duration-150">
          <div className="w-full max-w-lg bg-white border-l border-slate-200 h-full flex flex-col shadow-2xl animate-in slide-in-from-right duration-300">
            <div className="p-5 border-b border-slate-100 flex items-center justify-between bg-slate-50/70">
              <div className="flex items-center gap-3">
                <div className="w-9 h-9 rounded-xl bg-sky-50 border border-sky-200 flex items-center justify-center text-sky-700">
                  <FileText className="w-4 h-4" />
                </div>
                <div>
                  <h4 className="font-bold text-sm text-slate-900">{selectedAudit.action}</h4>
                  <p className="text-xs text-slate-500 font-mono">ID: {selectedAudit.audit_id || 'aud_live_rec'}</p>
                </div>
              </div>
              <button
                onClick={() => setSelectedAudit(null)}
                className="p-1.5 text-slate-400 hover:text-slate-700 hover:bg-slate-100 rounded-xl transition-colors"
              >
                <X className="w-5 h-5" />
              </button>
            </div>

            <div className="flex-1 overflow-y-auto p-5 space-y-4">
              <div className="p-4 bg-slate-50 rounded-2xl border border-slate-200 space-y-2 text-xs">
                <div className="flex justify-between">
                  <span className="text-slate-500 font-semibold">Actor User:</span>
                  <strong className="text-slate-900">{selectedAudit.user_id}</strong>
                </div>
                <div className="flex justify-between">
                  <span className="text-slate-500 font-semibold">Actor Role:</span>
                  <strong className="text-sky-700 uppercase">{selectedAudit.user_role}</strong>
                </div>
                <div className="flex justify-between">
                  <span className="text-slate-500 font-semibold">Resource:</span>
                  <strong className="text-slate-800 font-mono">{selectedAudit.resource_type}</strong>
                </div>
                <div className="flex justify-between">
                  <span className="text-slate-500 font-semibold">Timestamp:</span>
                  <strong className="text-slate-800">{selectedAudit.timestamp || 'N/A'}</strong>
                </div>
              </div>

              <div className="space-y-2">
                <div className="flex items-center justify-between">
                  <span className="text-xs font-bold text-slate-800">Sanitized Event Payload:</span>
                  <button
                    onClick={() => {
                      navigator.clipboard.writeText(JSON.stringify(selectedAudit, null, 2));
                      toast.success('Audit payload copied to clipboard');
                    }}
                    className="text-[11px] text-sky-700 hover:text-sky-800 flex items-center gap-1 font-bold"
                  >
                    <Copy className="w-3 h-3" />
                    <span>Copy JSON</span>
                  </button>
                </div>
                <pre className="bg-slate-900 text-sky-300 p-4 rounded-2xl border border-slate-800 text-[11px] font-mono overflow-x-auto leading-relaxed shadow-xs">
                  {JSON.stringify(selectedAudit, null, 2)}
                </pre>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
