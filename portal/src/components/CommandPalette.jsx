import React, { useState, useEffect, useRef } from 'react';
import {
  Search,
  LayoutDashboard,
  PhoneCall,
  Calendar,
  AlertTriangle,
  BookOpenCheck,
  ShieldCheck,
  UserPlus,
  PhoneOutgoing,
  Stethoscope,
  ArrowRight,
  Sparkles,
  X,
  Mic,
  TrendingUp,
  UploadCloud,
} from 'lucide-react';

export function CommandPalette({
  isOpen,
  onClose,
  onNavigate,
  onOpenBooking,
  onOpenOutbound,
  onOpenStaff,
  onOpenDiff,
  onOpenDocUpload,
}) {
  const [query, setQuery] = useState('');
  const [selectedIndex, setSelectedIndex] = useState(0);
  const inputRef = useRef(null);

  useEffect(() => {
    if (isOpen) {
      setTimeout(() => inputRef.current?.focus(), 50);
      setQuery('');
      setSelectedIndex(0);
    }
  }, [isOpen]);

  if (!isOpen) return null;

  const actions = [
    {
      id: 'nav_dashboard',
      category: 'Navigation',
      label: 'Command Center & Live KPIs',
      icon: LayoutDashboard,
      shortcut: 'G D',
      perform: () => onNavigate('dashboard'),
    },
    {
      id: 'nav_sandbox',
      category: 'Navigation',
      label: 'Voice AI Sandbox (Nova Sonic Simulator)',
      icon: Mic,
      shortcut: 'G S',
      perform: () => onNavigate('sandbox'),
    },
    {
      id: 'nav_analytics',
      category: 'Navigation',
      label: 'Clinical Analytics & Revenue Intelligence',
      icon: TrendingUp,
      shortcut: 'G R',
      perform: () => onNavigate('analytics'),
    },
    {
      id: 'nav_calls',
      category: 'Navigation',
      label: 'Patient Call Transcripts & Speech Waveforms',
      icon: PhoneCall,
      shortcut: 'G C',
      perform: () => onNavigate('calls'),
    },
    {
      id: 'nav_appointments',
      category: 'Navigation',
      label: 'Doctor OPD Availability & Bookings',
      icon: Calendar,
      shortcut: 'G A',
      perform: () => onNavigate('appointments'),
    },
    {
      id: 'nav_triage',
      category: 'Navigation',
      label: 'Emergency Clinical Triage Feed (ESI 1–5)',
      icon: AlertTriangle,
      shortcut: 'G T',
      perform: () => onNavigate('triage'),
    },
    {
      id: 'act_upload_doc',
      category: 'Quick Actions',
      label: 'Upload Doctor Roster / Tariff Sheet (AI Ingestion)',
      icon: UploadCloud,
      shortcut: 'Alt U',
      perform: () => onOpenDocUpload && onOpenDocUpload(),
    },

    {
      id: 'nav_knowledge',
      category: 'Navigation',
      label: 'Hospital Knowledge CMS & Roster',
      icon: BookOpenCheck,
      shortcut: 'G K',
      perform: () => onNavigate('knowledge'),
    },
    {
      id: 'nav_audit',
      category: 'Navigation',
      label: 'Immutable Audit Trail & Forensics',
      icon: ShieldCheck,
      shortcut: 'G L',
      perform: () => onNavigate('audit'),
    },
    {
      id: 'act_book',
      category: 'Quick Actions',
      label: 'Book New OPD Walk-In Appointment',
      icon: Calendar,
      shortcut: 'Alt N',
      perform: () => onOpenBooking && onOpenBooking(),
    },
    {
      id: 'act_outbound',
      category: 'Quick Actions',
      label: 'Dispatch Single Outbound Phone Call',
      icon: PhoneOutgoing,
      shortcut: 'Alt O',
      perform: () => onOpenOutbound && onOpenOutbound(),
    },
    {
      id: 'act_staff',
      category: 'Quick Actions',
      label: 'Onboard New Hospital Staff / Doctor',
      icon: UserPlus,
      shortcut: 'Alt S',
      perform: () => onOpenStaff && onOpenStaff(),
    },
    {
      id: 'act_diff',
      category: 'Quick Actions',
      label: 'Inspect Knowledge Base Draft vs. Published Diff',
      icon: Sparkles,
      shortcut: 'Alt V',
      perform: () => onOpenDiff && onOpenDiff(),
    },
    {
      id: 'doc_amit',
      category: 'Doctors & OPD',
      label: 'Dr. Amit Sharma — General Medicine (Room 104 • ₹500)',
      icon: Stethoscope,
      perform: () => onNavigate('appointments'),
    },
    {
      id: 'doc_priya',
      category: 'Doctors & OPD',
      label: 'Dr. Priya Patel — General Medicine (Room 105 • ₹600)',
      icon: Stethoscope,
      perform: () => onNavigate('appointments'),
    },
    {
      id: 'doc_rajesh',
      category: 'Doctors & OPD',
      label: 'Dr. Rajesh Gupta — Cardiology Specialist (Suite A • ₹1,000)',
      icon: Stethoscope,
      perform: () => onNavigate('appointments'),
    },
  ];

  const filtered = actions.filter(
    (a) =>
      a.label.toLowerCase().includes(query.toLowerCase()) ||
      a.category.toLowerCase().includes(query.toLowerCase())
  );

  const handleKeyDown = (e) => {
    if (e.key === 'ArrowDown') {
      e.preventDefault();
      setSelectedIndex((prev) => (prev + 1) % Math.max(1, filtered.length));
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      setSelectedIndex((prev) => (prev - 1 + filtered.length) % Math.max(1, filtered.length));
    } else if (e.key === 'Enter' && filtered[selectedIndex]) {
      e.preventDefault();
      filtered[selectedIndex].perform();
      onClose();
    } else if (e.key === 'Escape') {
      onClose();
    }
  };

  return (
    <div
      className="fixed inset-0 z-[9999] bg-slate-900/40 backdrop-blur-sm flex items-start justify-center pt-24 p-4 animate-in fade-in duration-150"
      onClick={onClose}
    >
      <div
        className="bg-white border border-slate-200 rounded-3xl max-w-xl w-full shadow-2xl overflow-hidden flex flex-col animate-in zoom-in-95 duration-150"
        onClick={(e) => e.stopPropagation()}
        onKeyDown={handleKeyDown}
      >
        {/* Search Input Bar */}
        <div className="p-4 border-b border-slate-100 flex items-center gap-3 bg-slate-50/50">
          <Search className="w-5 h-5 text-sky-600 shrink-0" />
          <input
            ref={inputRef}
            type="text"
            placeholder="Type a command, patient name, doctor, or action..."
            value={query}
            onChange={(e) => {
              setQuery(e.target.value);
              setSelectedIndex(0);
            }}
            className="flex-1 bg-transparent text-sm text-slate-900 placeholder-slate-400 focus:outline-none font-medium"
          />
          <span className="text-[10px] font-mono text-slate-500 bg-white px-2 py-0.5 rounded border border-slate-200 shadow-xs">
            ESC to close
          </span>
        </div>

        {/* Results List */}
        <div className="max-h-80 overflow-y-auto p-2 divide-y divide-slate-100">
          {filtered.length === 0 ? (
            <div className="p-8 text-center text-xs text-slate-400">No matching commands found.</div>
          ) : (
            filtered.map((item, idx) => {
              const Icon = item.icon;
              const isSelected = idx === selectedIndex;
              return (
                <div
                  key={item.id}
                  onClick={() => {
                    item.perform();
                    onClose();
                  }}
                  onMouseEnter={() => setSelectedIndex(idx)}
                  className={`flex items-center justify-between p-3 rounded-2xl cursor-pointer text-xs transition-all ${
                    isSelected
                      ? 'bg-sky-50 text-sky-900 border border-sky-200/80 font-bold'
                      : 'text-slate-700 hover:bg-slate-50 border border-transparent'
                  }`}
                >
                  <div className="flex items-center gap-3 min-w-0">
                    <div
                      className={`p-2 rounded-xl shrink-0 ${
                        isSelected ? 'bg-sky-100 text-sky-700' : 'bg-slate-100 text-slate-500'
                      }`}
                    >
                      <Icon className="w-4 h-4" />
                    </div>
                    <div className="truncate">
                      <p className="truncate">{item.label}</p>
                      <span className="text-[10px] text-slate-400 uppercase tracking-wider font-semibold">{item.category}</span>
                    </div>
                  </div>

                  {item.shortcut && (
                    <span className="text-[10px] font-mono text-slate-500 bg-slate-100 px-2 py-0.5 rounded border border-slate-200 shrink-0">
                      {item.shortcut}
                    </span>
                  )}
                </div>
              );
            })
          )}
        </div>

        {/* Footer */}
        <div className="p-3 bg-slate-50 border-t border-slate-100 flex items-center justify-between text-[11px] text-slate-500">
          <div className="flex items-center gap-2">
            <span>Navigate <strong className="font-mono text-slate-700">↑↓</strong></span>
            <span>•</span>
            <span>Select <strong className="font-mono text-slate-700">↵</strong></span>
          </div>
          <span className="text-sky-600 font-semibold">InDiiServe ASHA Command System</span>
        </div>
      </div>
    </div>
  );
}
