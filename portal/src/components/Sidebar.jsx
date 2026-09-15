import React from 'react';
import {
  LayoutDashboard,
  PhoneCall,
  CalendarCheck,
  AlertTriangle,
  BookOpenCheck,
  ShieldCheck,
  LogOut,
  Hospital,
  User,
  UserPlus,
  PhoneOutgoing,
  Mic,
  TrendingUp,
} from 'lucide-react';

export function Sidebar({ currentTab, setCurrentTab, user, onLogout, onOpenStaffModal, onOpenOutboundModal }) {
  const role = user?.role || 'staff';
  const hospitalName = user?.tenant_name || 'Apollo Metro Hospital';
  const canManageUsers = user?.permissions?.includes('users.manage') || user?.role === 'hospital_admin' || user?.role === 'super_admin';
  const canOutbound = user?.permissions?.includes('telephony.single_outbound') || user?.role === 'hospital_admin';

  const menuItems = [
    { id: 'dashboard', label: 'Command Center', icon: LayoutDashboard, permission: 'calls.read' },
    { id: 'sandbox', label: 'Voice AI Sandbox', icon: Mic, permission: 'sandbox.simulate', badge: 'AI Studio' },
    { id: 'analytics', label: 'Clinical Analytics', icon: TrendingUp, permission: 'analytics.read' },
    { id: 'calls', label: 'Call Transcripts', icon: PhoneCall, permission: 'calls.read' },
    { id: 'appointments', label: 'OPD Appointments', icon: CalendarCheck, permission: 'appointments.read' },
    { id: 'triage', label: 'Emergency Triage', icon: AlertTriangle, permission: 'triage.read', badge: 'Live' },
    { id: 'knowledge', label: 'Hospital Knowledge CMS', icon: BookOpenCheck, permission: 'knowledge.read', badge: 'Draft' },
    { id: 'audit', label: 'Audit Trail', icon: ShieldCheck, permission: 'audit.read' },
  ];


  const userPermissions = new Set(user?.permissions || []);
  const visibleItems = menuItems.filter((item) => !item.permission || userPermissions.has(item.permission));

  return (
    <aside className="w-64 bg-white border-r border-slate-200/80 flex flex-col justify-between shrink-0 h-screen sticky top-0 shadow-sm z-30">
      <div>
        {/* Hospital Branding Header */}
        <div className="p-5 border-b border-slate-100 flex items-center gap-3">
          <div className="w-10 h-10 rounded-2xl bg-gradient-to-br from-sky-500 to-blue-600 flex items-center justify-center shadow-md shadow-sky-500/20 text-white">
            <Hospital className="w-5 h-5" />
          </div>
          <div>
            <h1 className="font-bold text-sm text-slate-900 leading-tight">InDiiServe ASHA</h1>
            <p className="text-xs text-sky-600 font-semibold truncate max-w-[140px]">{hospitalName}</p>
          </div>
        </div>

        {/* Navigation Items */}
        <nav className="p-3 space-y-1">
          {visibleItems.map((item) => {
            const Icon = item.icon;
            const active = currentTab === item.id;
            return (
              <button
                key={item.id}
                onClick={() => setCurrentTab(item.id)}
                className={`w-full flex items-center justify-between px-3.5 py-2.5 rounded-2xl text-xs font-semibold transition-all ${
                  active
                    ? 'bg-sky-50 text-sky-700 font-bold border border-sky-200/80 shadow-sm'
                    : 'text-slate-600 hover:text-slate-900 hover:bg-slate-50'
                }`}
              >
                <div className="flex items-center gap-3">
                  <Icon className={`w-4 h-4 ${active ? 'text-sky-600' : 'text-slate-400'}`} />
                  <span>{item.label}</span>
                </div>
                {item.badge && (
                  <span
                    className={`text-[9px] uppercase font-extrabold px-2 py-0.5 rounded-full ${
                      item.badge === 'Live'
                        ? 'bg-rose-50 text-rose-600 border border-rose-200 animate-pulse'
                        : 'bg-amber-50 text-amber-700 border border-amber-200'
                    }`}
                  >
                    {item.badge}
                  </span>
                )}
              </button>
            );
          })}
        </nav>

        {/* Quick Operations Actions */}
        <div className="px-3 pt-3 space-y-2 border-t border-slate-100 mx-2">
          {canOutbound && (
            <button
              onClick={onOpenOutboundModal}
              className="w-full flex items-center justify-center gap-2 px-3 py-2 rounded-xl bg-indigo-50 hover:bg-indigo-100 text-indigo-700 text-xs font-semibold border border-indigo-200/80 transition-all shadow-sm"
            >
              <PhoneOutgoing className="w-3.5 h-3.5" />
              <span>Outbound Call</span>
            </button>
          )}

          {canManageUsers && (
            <button
              onClick={onOpenStaffModal}
              className="w-full flex items-center justify-center gap-2 px-3 py-2 rounded-xl bg-slate-50 hover:bg-slate-100 text-slate-700 text-xs font-semibold border border-slate-200 transition-all shadow-sm"
            >
              <UserPlus className="w-3.5 h-3.5 text-sky-600" />
              <span>Onboard Staff</span>
            </button>
          )}
        </div>
      </div>

      {/* User Info & Logout Footer */}
      <div className="p-4 border-t border-slate-100 bg-slate-50/50">
        <div className="bg-white rounded-2xl p-3 border border-slate-200/80 mb-3 flex items-center gap-3 shadow-sm">
          <div className="w-8 h-8 rounded-xl bg-sky-50 text-sky-700 font-bold text-xs flex items-center justify-center border border-sky-200">
            {user?.username?.substring(0, 2).toUpperCase() || 'AD'}
          </div>
          <div className="overflow-hidden flex-1">
            <p className="text-xs font-bold text-slate-800 truncate">{user?.username}</p>
            <span className="text-[10px] font-semibold uppercase tracking-wider text-sky-700 px-1.5 py-0.2 bg-sky-50 rounded border border-sky-200">
              {role.replace('_', ' ')}
            </span>
          </div>
        </div>

        <button
          onClick={onLogout}
          className="w-full flex items-center justify-center gap-2 px-3 py-2 text-xs font-semibold text-slate-500 hover:text-rose-600 hover:bg-rose-50 rounded-xl transition-colors border border-transparent hover:border-rose-200"
        >
          <LogOut className="w-3.5 h-3.5" />
          <span>Sign Out Session</span>
        </button>
      </div>
    </aside>
  );
}
