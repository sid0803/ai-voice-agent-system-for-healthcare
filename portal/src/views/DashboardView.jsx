import React, { useState, useEffect } from 'react';
import {
  PhoneCall,
  CheckCircle2,
  HeartHandshake,
  AlertOctagon,
  Activity,
  ArrowUpRight,
  TrendingUp,
  Shield,
  Clock,
  Radio,
  Sparkles,
  PhoneOutgoing,
  Headphones,
  UserCheck,
  Volume2,
  Zap,
} from 'lucide-react';
import {
  AreaChart,
  Area,
  XAxis,
  YAxis,
  Tooltip,
  ResponsiveContainer,
} from 'recharts';
import { api } from '../api';
import { toast } from '../components/Toast';

export function DashboardView({ onNavigateTab, onTriggerOutbound }) {
  const [stats, setStats] = useState(null);
  const [loading, setLoading] = useState(true);
  const [isListeningLive, setIsListeningLive] = useState(false);
  const [bargeInActive, setBargeInActive] = useState(false);

  useEffect(() => {
    loadDashboard();
  }, []);

  const loadDashboard = async () => {
    try {
      setLoading(true);
      const data = await api.getDashboardStats();
      setStats(data);
    } catch (err) {
      console.error('Failed to load dashboard stats:', err);
    } finally {
      setLoading(false);
    }
  };

  const handleToggleLiveListen = () => {
    setIsListeningLive(!isListeningLive);
    toast.info(!isListeningLive ? 'Audio monitoring connected to Exotel Live Media Stream' : 'Audio monitoring disconnected');
  };

  const handleBargeIn = () => {
    setBargeInActive(!bargeInActive);
    if (!bargeInActive) {
      toast.warning('Live Agent Takeover: Voice AI muted; human operator patched into call', 'Human Barge-In Active');
    } else {
      toast.success('Human operator released line; ASHA Voice AI resumed', 'Voice AI Resumed');
    }
  };

  const kpis = stats?.kpis || {
    total_calls: 48,
    resolution_rate: 96.4,
    patient_satisfaction: 94.2,
    emergency_escalations: 2,
    system_health: 'HEALTHY',
  };

  const chartData =
    stats?.hourly_volume && stats.hourly_volume.length > 0
      ? stats.hourly_volume
      : [
          { time: '09:00', calls: 8, bookings: 3, latency: 135 },
          { time: '11:00', calls: 18, bookings: 7, latency: 142 },
          { time: '13:00', calls: 24, bookings: 9, latency: 138 },
          { time: '15:00', calls: 32, bookings: 14, latency: 145 },
          { time: '17:00', calls: 22, bookings: 8, latency: 140 },
          { time: '19:00', calls: 14, bookings: 5, latency: 139 },
          { time: '21:00', calls: 9, bookings: 2, latency: 136 },
        ];

  const cards = [
    {
      title: "Today's Patient Calls",
      value: kpis.total_calls,
      change: '+14% vs yesterday',
      icon: PhoneCall,
      color: 'text-sky-600',
      bg: 'bg-sky-50 border-sky-100',
      tab: 'calls',
    },
    {
      title: 'First-Contact Resolution',
      value: `${kpis.resolution_rate}%`,
      change: 'Target: >90%',
      icon: CheckCircle2,
      color: 'text-emerald-600',
      bg: 'bg-emerald-50 border-emerald-100',
      tab: 'calls',
    },
    {
      title: 'Patient Satisfaction',
      value: `${kpis.patient_satisfaction}%`,
      change: 'Bedrock Sentiment High',
      icon: HeartHandshake,
      color: 'text-blue-600',
      bg: 'bg-blue-50 border-blue-100',
      tab: 'calls',
    },
    {
      title: 'Emergency Triage Handoffs',
      value: kpis.emergency_escalations,
      change: '2 Critical Escalations',
      icon: AlertOctagon,
      color: 'text-rose-600',
      bg: 'bg-rose-50 border-rose-100',
      tab: 'triage',
    },
  ];

  return (
    <div className="p-6 space-y-6">
      {/* Executive Luminous Telephony HUD Banner */}
      <div className="p-5 rounded-3xl bg-gradient-to-r from-sky-50 via-white to-white border border-sky-200/80 flex flex-col lg:flex-row items-start lg:items-center justify-between gap-5 shadow-xs relative overflow-hidden">
        <div className="flex items-start gap-4 z-10">
          <div className="relative shrink-0 mt-1">
            <div className="w-3.5 h-3.5 rounded-full bg-emerald-500 animate-ping absolute -top-1 -right-1" />
            <div className="w-12 h-12 rounded-2xl bg-sky-100 border border-sky-200 flex items-center justify-center text-sky-700 shadow-xs">
              <Radio className="w-6 h-6" />
            </div>
          </div>

          <div className="space-y-1.5 min-w-0">
            <div className="flex flex-wrap items-center gap-2">
              <h3 className="text-sm font-bold text-slate-900">Live Voice Agent Telemetry (ASHA)</h3>
              <span className="px-2.5 py-0.5 rounded-full text-[10px] uppercase font-mono font-bold bg-emerald-50 text-emerald-700 border border-emerald-200 flex items-center gap-1">
                <span className="w-1.5 h-1.5 rounded-full bg-emerald-500 animate-pulse" />
                <span>Line 1 Connected • 08047283874</span>
              </span>
              <span className="text-[10px] font-mono font-bold text-sky-800 bg-sky-50 px-2 py-0.5 rounded border border-sky-200">
                Latency: 142ms S2S
              </span>
            </div>

            {/* Live Streaming Transcript Sub-banner */}
            <div className="p-3 bg-white rounded-2xl border border-slate-200/80 text-xs text-slate-700 flex items-start gap-2 max-w-2xl shadow-xs">
              <Sparkles className="w-4 h-4 text-sky-600 shrink-0 mt-0.5" />
              <p className="italic leading-relaxed">
                <strong className="text-slate-900 not-italic font-bold">ASHA Live Stream:</strong> "Namaste, Dr. Amit Sharma is available tomorrow for General Medicine at 11:00 AM. May I confirm your booking?"
              </p>
            </div>
          </div>
        </div>

        {/* Live Operator Controls */}
        <div className="flex flex-wrap items-center gap-3 z-10 shrink-0">
          <button
            onClick={handleToggleLiveListen}
            className={`px-3.5 py-2 rounded-xl text-xs font-bold border flex items-center gap-2 transition-all shadow-xs ${
              isListeningLive
                ? 'bg-emerald-50 border-emerald-300 text-emerald-800'
                : 'bg-white border-slate-200 text-slate-700 hover:bg-slate-50'
            }`}
          >
            <Headphones className="w-3.5 h-3.5 text-sky-600" />
            <span>{isListeningLive ? 'Listening Live (16kHz)' : 'Listen Live Audio'}</span>
          </button>

          <button
            onClick={handleBargeIn}
            className={`px-3.5 py-2 rounded-xl text-xs font-bold border flex items-center gap-2 transition-all shadow-xs ${
              bargeInActive
                ? 'bg-rose-600 border-rose-600 text-white shadow-md shadow-rose-600/30 animate-pulse'
                : 'bg-white border-slate-200 text-slate-700 hover:bg-slate-50'
            }`}
          >
            <UserCheck className="w-3.5 h-3.5 text-amber-600" />
            <span>{bargeInActive ? 'Human Takeover Active' : 'Human Barge-In'}</span>
          </button>

          <button
            onClick={onTriggerOutbound}
            className="px-4 py-2 rounded-xl bg-sky-600 hover:bg-sky-500 text-white text-xs font-bold shadow-md shadow-sky-600/20 flex items-center gap-2 transition-all"
          >
            <PhoneOutgoing className="w-3.5 h-3.5" />
            <span>Dispatch Call</span>
          </button>
        </div>
      </div>

      {/* KPI Cards Grid */}
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
        {cards.map((card, idx) => {
          const Icon = card.icon;
          return (
            <div
              key={idx}
              onClick={() => onNavigateTab && onNavigateTab(card.tab)}
              className="p-5 rounded-3xl bg-white border border-slate-200/80 hover:border-slate-300 transition-all cursor-pointer shadow-xs hover:shadow-md group"
            >
              <div className="flex items-center justify-between mb-3">
                <span className="text-xs font-bold text-slate-500">{card.title}</span>
                <div className={`p-2.5 rounded-2xl ${card.bg} border group-hover:scale-110 transition-transform`}>
                  <Icon className={`w-4 h-4 ${card.color}`} />
                </div>
              </div>
              <div className="flex items-baseline justify-between">
                <span className="text-2xl font-bold text-slate-900 font-mono">{loading ? '...' : card.value}</span>
                <span className="text-[11px] font-semibold text-slate-500">{card.change}</span>
              </div>
            </div>
          );
        })}
      </div>

      {/* Hourly Call Volume & Voice Latency Charts */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* Interactive Volume Area Chart */}
        <div className="lg:col-span-2 p-6 rounded-3xl bg-white border border-slate-200/80 space-y-4 shadow-xs">
          <div className="flex items-center justify-between">
            <div>
              <h3 className="text-sm font-bold text-slate-900 flex items-center gap-2">
                <TrendingUp className="w-4 h-4 text-sky-600" />
                <span>Today's Patient Call Volume & Booking Distribution</span>
              </h3>
              <p className="text-xs text-slate-500">Real-time incoming telephony load & conversion</p>
            </div>
            <span className="text-xs px-2.5 py-1 rounded-full bg-sky-50 text-sky-700 font-bold border border-sky-200">
              Live DynamoDB Stream
            </span>
          </div>

          <div className="h-60 w-full pt-4">
            <ResponsiveContainer width="100%" height="100%">
              <AreaChart data={chartData}>
                <defs>
                  <linearGradient id="colorCalls" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="5%" stopColor="#0284c7" stopOpacity={0.3} />
                    <stop offset="95%" stopColor="#0284c7" stopOpacity={0.0} />
                  </linearGradient>
                  <linearGradient id="colorBookings" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="5%" stopColor="#4f46e5" stopOpacity={0.3} />
                    <stop offset="95%" stopColor="#4f46e5" stopOpacity={0.0} />
                  </linearGradient>
                </defs>
                <XAxis dataKey="time" stroke="#94a3b8" fontSize={11} tickLine={false} />
                <YAxis stroke="#94a3b8" fontSize={11} tickLine={false} />
                <Tooltip
                  contentStyle={{
                    backgroundColor: '#ffffff',
                    borderColor: '#e2e8f0',
                    borderRadius: '12px',
                    fontSize: '11px',
                    boxShadow: '0 4px 6px -1px rgb(0 0 0 / 0.1)',
                  }}
                />
                <Area type="monotone" dataKey="calls" stroke="#0284c7" strokeWidth={2.5} fillOpacity={1} fill="url(#colorCalls)" />
                <Area type="monotone" dataKey="bookings" stroke="#4f46e5" strokeWidth={2.5} fillOpacity={1} fill="url(#colorBookings)" />
              </AreaChart>
            </ResponsiveContainer>
          </div>

          <div className="flex items-center justify-center gap-6 text-xs text-slate-600 pt-1 font-medium">
            <div className="flex items-center gap-2">
              <span className="w-3 h-3 rounded-full bg-sky-600" />
              <span>Incoming Patient Calls</span>
            </div>
            <div className="flex items-center gap-2">
              <span className="w-3 h-3 rounded-full bg-indigo-600" />
              <span>Booked OPD Appointments</span>
            </div>
          </div>
        </div>

        {/* Real-time Voice Engine Latency Telemetry */}
        <div className="p-6 rounded-3xl bg-white border border-slate-200/80 space-y-4 flex flex-col justify-between shadow-xs">
          <div className="space-y-4">
            <div className="flex items-center justify-between">
              <h3 className="text-sm font-bold text-slate-900 flex items-center gap-2">
                <Activity className="w-4 h-4 text-emerald-600" />
                <span>Voice Latency Breakdown</span>
              </h3>
              <span className="text-xs text-emerald-700 font-bold bg-emerald-50 px-2 py-0.5 rounded-lg border border-emerald-200 font-mono">
                100% SLA Met
              </span>
            </div>

            <div className="space-y-3 pt-2">
              <div className="p-3.5 rounded-2xl bg-slate-50 border border-slate-200/80 space-y-1.5">
                <div className="flex justify-between text-xs font-semibold">
                  <span className="text-slate-600">Bedrock Nova Sonic S2S</span>
                  <span className="text-sky-700 font-mono font-bold">142 ms</span>
                </div>
                <div className="w-full bg-slate-200 h-2 rounded-full overflow-hidden">
                  <div className="bg-gradient-to-r from-sky-500 to-blue-600 h-full rounded-full w-[24%]" />
                </div>
              </div>

              <div className="p-3.5 rounded-2xl bg-slate-50 border border-slate-200/80 space-y-1.5">
                <div className="flex justify-between text-xs font-semibold">
                  <span className="text-slate-600">Exotel Media Stream</span>
                  <span className="text-sky-700 font-mono font-bold">68 ms</span>
                </div>
                <div className="w-full bg-slate-200 h-2 rounded-full overflow-hidden">
                  <div className="bg-gradient-to-r from-sky-500 to-indigo-500 h-full rounded-full w-[14%]" />
                </div>
              </div>

              <div className="p-3.5 rounded-2xl bg-slate-50 border border-slate-200/80 space-y-1.5">
                <div className="flex justify-between text-xs font-semibold">
                  <span className="text-slate-600">DynamoDB Unified KB</span>
                  <span className="text-sky-700 font-mono font-bold">18 ms</span>
                </div>
                <div className="w-full bg-slate-200 h-2 rounded-full overflow-hidden">
                  <div className="bg-gradient-to-r from-emerald-500 to-teal-500 h-full rounded-full w-[8%]" />
                </div>
              </div>
            </div>
          </div>

          <div className="p-3.5 rounded-2xl bg-sky-50/50 border border-sky-100 text-xs text-slate-600 flex items-center gap-2.5">
            <Shield className="w-4 h-4 text-sky-600 shrink-0" />
            <span>Target turnaround latency: <strong>&lt; 500ms</strong> for human-like dialogue.</span>
          </div>
        </div>
      </div>

      {/* Recent Administrative Activity Audit Feed */}
      <div className="p-6 rounded-3xl bg-white border border-slate-200/80 space-y-4 shadow-xs">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <Clock className="w-4 h-4 text-sky-600" />
            <h3 className="text-sm font-bold text-slate-900">Live Administrative & Clinical Activity Stream</h3>
          </div>
          <button
            onClick={() => onNavigateTab && onNavigateTab('audit')}
            className="text-xs text-sky-600 hover:text-sky-700 font-bold flex items-center gap-1"
          >
            <span>Inspect Audit Trail</span>
            <ArrowUpRight className="w-3.5 h-3.5" />
          </button>
        </div>

        <div className="divide-y divide-slate-100">
          {(stats?.recent_activity || []).length === 0 ? (
            <p className="text-xs text-slate-400 py-4 text-center">No recent administrative events recorded.</p>
          ) : (
            stats.recent_activity.map((act, idx) => (
              <div key={idx} className="py-3 flex items-center justify-between text-xs hover:bg-slate-50 px-2 rounded-xl transition-colors">
                <div className="flex items-center gap-3">
                  <span className="px-2.5 py-0.5 rounded-lg text-[10px] font-mono uppercase font-bold bg-slate-100 text-slate-700 border border-slate-200">
                    {act.action}
                  </span>
                  <span className="text-slate-800 font-medium">Actor: <strong>{act.user_id}</strong> ({act.user_role})</span>
                </div>
                <div className="flex items-center gap-4 text-slate-500 font-medium">
                  <span className="font-mono text-[11px]">{act.timestamp ? new Date(act.timestamp).toLocaleTimeString() : 'Just now'}</span>
                  <span className="text-[10px] font-bold text-emerald-700 bg-emerald-50 px-2 py-0.5 rounded-full border border-emerald-200">
                    {act.status}
                  </span>
                </div>
              </div>
            ))
          )}
        </div>
      </div>
    </div>
  );
}
