import React from 'react';
import { X, GitCompare, CheckCircle2, ArrowRight } from 'lucide-react';

export function DiffViewerModal({ isOpen, onClose, publishedVersion = 'v1.0', draftVersion = 'v1.1-DRAFT', onPublish }) {
  if (!isOpen) return null;

  const changes = [
    {
      category: 'Doctor Roster',
      item: 'Dr. Amit Sharma (General Medicine)',
      published: 'OPD Timings: Mon-Sat 10:00 AM - 1:00 PM | Fee: ₹500',
      draft: 'OPD Timings: Mon-Sat 10:00 AM - 2:00 PM (Extended) | Fee: ₹500',
      type: 'MODIFIED',
    },
    {
      category: 'Radiology Catalog',
      item: 'MRI Brain with Contrast (3.0 Tesla)',
      published: 'Base Tariff: ₹7,500 | Fasting: 4 Hours',
      draft: 'Base Tariff: ₹7,500 | Fasting: 4 Hours | Fast-Track Slot Available',
      type: 'MODIFIED',
    },
    {
      category: 'Emergency Triage Rules',
      item: 'Acute Chest Pain Protocol',
      published: 'Route to Emergency Dept Extension 108',
      draft: 'Immediate Handoff + Priority ECG Alert',
      type: 'NEW',
    },
  ];

  return (
    <div className="fixed inset-0 z-50 bg-slate-900/40 backdrop-blur-sm flex items-center justify-center p-4 animate-in fade-in duration-150">
      <div className="bg-white border border-slate-200 rounded-3xl p-6 max-w-2xl w-full space-y-5 shadow-2xl animate-in zoom-in-95 max-h-[85vh] flex flex-col">
        <div className="flex items-center justify-between border-b border-slate-100 pb-4 shrink-0">
          <div className="flex items-center gap-3">
            <div className="w-10 h-10 rounded-2xl bg-sky-50 border border-sky-200 flex items-center justify-center text-sky-600">
              <GitCompare className="w-5 h-5" />
            </div>
            <div>
              <h3 className="text-base font-bold text-slate-900">Knowledge Base Version Diff Viewer</h3>
              <p className="text-xs text-slate-500">
                Comparing <span className="text-slate-800 font-mono font-bold">{publishedVersion}</span> vs.{' '}
                <span className="text-sky-700 font-mono font-bold">{draftVersion}</span>
              </p>
            </div>
          </div>
          <button
            onClick={onClose}
            className="p-1.5 text-slate-400 hover:text-slate-700 hover:bg-slate-100 rounded-xl transition-colors"
          >
            <X className="w-5 h-5" />
          </button>
        </div>

        {/* Diff Changes Table */}
        <div className="flex-1 overflow-y-auto space-y-3 pr-1">
          {changes.map((ch, idx) => (
            <div key={idx} className="p-4 rounded-2xl bg-slate-50 border border-slate-200 space-y-2 text-xs">
              <div className="flex items-center justify-between">
                <span className="font-bold text-slate-900">{ch.item}</span>
                <span className="text-[10px] uppercase font-bold px-2 py-0.5 rounded bg-sky-100 text-sky-800 border border-sky-200">
                  {ch.category}
                </span>
              </div>

              <div className="grid grid-cols-2 gap-3 pt-1">
                <div className="p-3 rounded-xl bg-rose-50 border border-rose-200 space-y-1">
                  <span className="text-[10px] uppercase font-bold text-rose-700">Published ({publishedVersion})</span>
                  <p className="text-slate-600 font-mono text-[11px] leading-relaxed line-through opacity-80">
                    {ch.published}
                  </p>
                </div>

                <div className="p-3 rounded-xl bg-emerald-50 border border-emerald-200 space-y-1">
                  <span className="text-[10px] uppercase font-bold text-emerald-700">Draft ({draftVersion})</span>
                  <p className="text-emerald-900 font-mono text-[11px] leading-relaxed font-semibold">
                    {ch.draft}
                  </p>
                </div>
              </div>
            </div>
          ))}
        </div>

        <div className="pt-4 border-t border-slate-100 flex items-center justify-between shrink-0">
          <span className="text-xs text-slate-500">
            Publishing will hot-reload the Bedrock voice agent cache in real-time.
          </span>
          <div className="flex items-center gap-3">
            <button
              onClick={onClose}
              className="px-4 py-2 rounded-xl text-xs font-semibold text-slate-600 hover:bg-slate-100 transition-colors"
            >
              Close Diff
            </button>
            <button
              onClick={() => {
                if (onPublish) onPublish();
                onClose();
              }}
              className="px-5 py-2.5 rounded-xl text-xs font-bold bg-sky-600 hover:bg-sky-500 text-white shadow-md shadow-sky-600/20 flex items-center gap-2 transition-all"
            >
              <CheckCircle2 className="w-4 h-4" />
              <span>Publish Draft to Production</span>
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
