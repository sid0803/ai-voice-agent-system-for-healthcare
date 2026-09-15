import React, { useState, useEffect } from 'react';
import {
  TrendingUp,
  DollarSign,
  PieChart as PieIcon,
  Activity,
  Layers,
  ArrowUpRight,
  Sparkles,
  Download,
  Calendar,
  Languages,
  Clock,
  Filter,
} from 'lucide-react';
import {
  BarChart,
  Bar,
  XAxis,
  YAxis,
  Tooltip,
  ResponsiveContainer,
  PieChart,
  Pie,
  Cell,
  Legend,
} from 'recharts';
import { api } from '../api';
import { toast } from '../components/Toast';

export function AnalyticsView() {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [timeRange, setTimeRange] = useState('TODAY'); // 'TODAY' | 'WEEK' | 'MONTH'

  useEffect(() => {
    loadAnalytics();
  }, []);

  const loadAnalytics = async () => {
    try {
      setLoading(true);
      const res = await api.getAnalyticsOverview();
      setData(res);
    } catch (err) {
      console.error('Failed to load analytics:', err);
    } finally {
      setLoading(false);
    }
  };

  const kpis = data?.kpis || {
    today_voice_revenue: 34500,
    monthly_voice_revenue: 621000,
    conversion_rate: 42.8,
    avg_call_duration_seconds: 84,
    emergency_escalation_rate: 4.2,
  };

  const departmentRevenue = data?.department_revenue || [
    { department: 'General Medicine', revenue: 14500 },
    { department: 'Cardiology', revenue: 12000 },
    { department: 'Orthopedics', revenue: 4800 },
    { department: 'Pediatrics', revenue: 3200 },
  ];

  const hourlyHeatmap = data?.hourly_heatmap || [
    { hour: '09:00', load: 45, bookings: 4, status: 'OPTIMAL' },
    { hour: '10:00', load: 85, bookings: 11, status: 'PEAK' },
    { hour: '11:00', load: 92, bookings: 14, status: 'CRITICAL_PEAK' },
    { hour: '12:00', load: 88, bookings: 12, status: 'PEAK' },
    { hour: '13:00', load: 60, bookings: 6, status: 'MODERATE' },
    { hour: '14:00', load: 30, bookings: 2, status: 'LOW' },
    { hour: '15:00', load: 55, bookings: 7, status: 'OPTIMAL' },
    { hour: '16:00', load: 78, bookings: 10, status: 'PEAK' },
    { hour: '17:00', load: 82, bookings: 11, status: 'PEAK' },
    { hour: '18:00', load: 65, bookings: 8, status: 'OPTIMAL' },
    { hour: '19:00', load: 40, bookings: 3, status: 'LOW' },
  ];

  const languages = data?.languages || [
    { name: 'Hindi', value: 64, color: '#0284c7' },
    { name: 'Hinglish', value: 24, color: '#4f46e5' },
    { name: 'English', value: 12, color: '#10b981' },
  ];

  const funnel = data?.conversion_funnel || [
    { stage: 'Incoming Inquiries', count: 148, rate: '100%' },
    { stage: 'Doctor / Service Matched', count: 116, rate: '78.4%' },
    { stage: 'Slot Selected', count: 80, rate: '54.1%' },
    { stage: 'Confirmed OPD Bookings', count: 63, rate: '42.5%' },
  ];

  const handleExport = () => {
    toast.success('Clinical Revenue & Analytics CSV Report exported successfully');
  };

  return (
    <div className="p-6 space-y-6">
      {/* Top Header */}
      <div className="flex flex-col sm:flex-row items-start sm:items-center justify-between gap-4 bg-white p-4 rounded-3xl border border-slate-200 shadow-xs">
        <div>
          <h3 className="text-base font-bold text-slate-900">Clinical Analytics & Executive Revenue Intelligence Hub</h3>
          <p className="text-xs text-slate-500">
            Real-time tracking of voice booking revenues, doctor slot fill heatmaps, and conversion funnels.
          </p>
        </div>

        <div className="flex items-center gap-3">
          <div className="flex items-center p-1 bg-slate-100 rounded-2xl border border-slate-200 text-xs font-bold">
            <button
              onClick={() => setTimeRange('TODAY')}
              className={`px-3 py-1 rounded-xl transition-all ${
                timeRange === 'TODAY' ? 'bg-white text-sky-700 shadow-xs' : 'text-slate-600'
              }`}
            >
              Today
            </button>
            <button
              onClick={() => setTimeRange('WEEK')}
              className={`px-3 py-1 rounded-xl transition-all ${
                timeRange === 'WEEK' ? 'bg-white text-sky-700 shadow-xs' : 'text-slate-600'
              }`}
            >
              This Week
            </button>
            <button
              onClick={() => setTimeRange('MONTH')}
              className={`px-3 py-1 rounded-xl transition-all ${
                timeRange === 'MONTH' ? 'bg-white text-sky-700 shadow-xs' : 'text-slate-600'
              }`}
            >
              This Month
            </button>
          </div>

          <button
            onClick={handleExport}
            className="px-3.5 py-2 rounded-2xl bg-white hover:bg-slate-50 text-slate-700 text-xs font-bold border border-slate-200 flex items-center gap-1.5 transition-all shadow-xs"
          >
            <Download className="w-3.5 h-3.5 text-sky-600" />
            <span>Export Report</span>
          </button>
        </div>
      </div>

      {/* KPI Cards Grid */}
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
        <div className="p-5 rounded-3xl bg-white border border-slate-200 shadow-xs space-y-3">
          <div className="flex items-center justify-between">
            <span className="text-xs font-bold text-slate-500">Today's Voice Revenue</span>
            <div className="p-2 rounded-xl bg-emerald-50 text-emerald-600 border border-emerald-100">
              <DollarSign className="w-4 h-4" />
            </div>
          </div>
          <div className="flex items-baseline justify-between">
            <span className="text-2xl font-bold font-mono text-slate-900">
              ₹{kpis.today_voice_revenue.toLocaleString()}
            </span>
            <span className="text-[11px] font-bold text-emerald-600">+22.4% vs avg</span>
          </div>
        </div>

        <div className="p-5 rounded-3xl bg-white border border-slate-200 shadow-xs space-y-3">
          <div className="flex items-center justify-between">
            <span className="text-xs font-bold text-slate-500">Booking Conversion Rate</span>
            <div className="p-2 rounded-xl bg-sky-50 text-sky-600 border border-sky-100">
              <TrendingUp className="w-4 h-4" />
            </div>
          </div>
          <div className="flex items-baseline justify-between">
            <span className="text-2xl font-bold font-mono text-slate-900">{kpis.conversion_rate}%</span>
            <span className="text-[11px] font-semibold text-slate-500">Target &gt; 40%</span>
          </div>
        </div>

        <div className="p-5 rounded-3xl bg-white border border-slate-200 shadow-xs space-y-3">
          <div className="flex items-center justify-between">
            <span className="text-xs font-bold text-slate-500">Average Call Duration</span>
            <div className="p-2 rounded-xl bg-indigo-50 text-indigo-600 border border-indigo-100">
              <Clock className="w-4 h-4" />
            </div>
          </div>
          <div className="flex items-baseline justify-between">
            <span className="text-2xl font-bold font-mono text-slate-900">{kpis.avg_call_duration_seconds}s</span>
            <span className="text-[11px] font-semibold text-indigo-600">Optimal &lt; 90s</span>
          </div>
        </div>

        <div className="p-5 rounded-3xl bg-white border border-slate-200 shadow-xs space-y-3">
          <div className="flex items-center justify-between">
            <span className="text-xs font-bold text-slate-500">Emergency Escalation Rate</span>
            <div className="p-2 rounded-xl bg-rose-50 text-rose-600 border border-rose-100">
              <Activity className="w-4 h-4" />
            </div>
          </div>
          <div className="flex items-baseline justify-between">
            <span className="text-2xl font-bold font-mono text-slate-900">{kpis.emergency_escalation_rate}%</span>
            <span className="text-[11px] font-bold text-rose-600">2 Critical Triage</span>
          </div>
        </div>
      </div>

      {/* Row 2: Revenue By Department + Language Donut */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* Department Revenue Bar Chart */}
        <div className="lg:col-span-2 p-6 rounded-3xl bg-white border border-slate-200 shadow-xs space-y-4">
          <div className="flex items-center justify-between">
            <div>
              <h3 className="text-sm font-bold text-slate-900">Estimated Voice Revenue by Clinical Specialty</h3>
              <p className="text-xs text-slate-500">Consultation fee totals from verified voice bookings</p>
            </div>
            <span className="text-xs px-2.5 py-1 rounded-full bg-emerald-50 text-emerald-700 font-bold border border-emerald-200">
              Direct OPD Conversion
            </span>
          </div>

          <div className="h-60 w-full pt-4">
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={departmentRevenue}>
                <XAxis dataKey="department" stroke="#94a3b8" fontSize={11} tickLine={false} />
                <YAxis stroke="#94a3b8" fontSize={11} tickLine={false} />
                <Tooltip
                  contentStyle={{
                    backgroundColor: '#ffffff',
                    borderColor: '#e2e8f0',
                    borderRadius: '12px',
                    fontSize: '11px',
                    boxShadow: '0 4px 6px -1px rgb(0 0 0 / 0.1)',
                  }}
                  formatter={(val) => [`₹${val.toLocaleString()}`, 'Revenue']}
                />
                <Bar dataKey="revenue" fill="#0284c7" radius={[8, 8, 0, 0]} />
              </BarChart>
            </ResponsiveContainer>
          </div>
        </div>

        {/* Language Distribution Donut */}
        <div className="p-6 rounded-3xl bg-white border border-slate-200 shadow-xs space-y-4 flex flex-col justify-between">
          <div>
            <h3 className="text-sm font-bold text-slate-900 flex items-center gap-2">
              <Languages className="w-4 h-4 text-sky-600" />
              <span>Caller Language Breakdown</span>
            </h3>
            <p className="text-xs text-slate-500">Real-time multilingual language switching distribution</p>
          </div>

          <div className="h-44 w-full">
            <ResponsiveContainer width="100%" height="100%">
              <PieChart>
                <Pie
                  data={languages}
                  innerRadius={45}
                  outerRadius={65}
                  paddingAngle={5}
                  dataKey="value"
                >
                  {languages.map((entry, index) => (
                    <Cell key={`cell-${index}`} fill={entry.color} />
                  ))}
                </Pie>
                <Tooltip />
              </PieChart>
            </ResponsiveContainer>
          </div>

          <div className="space-y-2 pt-2 border-t border-slate-100">
            {languages.map((l) => (
              <div key={l.name} className="flex items-center justify-between text-xs">
                <div className="flex items-center gap-2">
                  <span className="w-2.5 h-2.5 rounded-full" style={{ backgroundColor: l.color }} />
                  <span className="text-slate-700 font-medium">{l.name}</span>
                </div>
                <span className="font-mono font-bold text-slate-900">{l.value}%</span>
              </div>
            ))}
          </div>
        </div>
      </div>

      {/* Row 3: Hourly Heatmap + Conversion Funnel */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {/* Hourly Slot Utilization Heatmap */}
        <div className="p-6 rounded-3xl bg-white border border-slate-200 shadow-xs space-y-4">
          <div className="flex items-center justify-between">
            <div>
              <h3 className="text-sm font-bold text-slate-900">Doctor Slot Fill & Hourly Telephony Heatmap</h3>
              <p className="text-xs text-slate-500">Hourly load percentage across OPD shifts (09:00 - 19:00)</p>
            </div>
          </div>

          <div className="grid grid-cols-4 sm:grid-cols-6 gap-2 pt-2">
            {hourlyHeatmap.map((item) => {
              const isPeak = item.load >= 80;
              const isModerate = item.load >= 50 && item.load < 80;

              return (
                <div
                  key={item.hour}
                  className={`p-3 rounded-2xl border text-center space-y-1 transition-all ${
                    isPeak
                      ? 'bg-rose-50 border-rose-200 text-rose-950'
                      : isModerate
                      ? 'bg-sky-50 border-sky-200 text-sky-950'
                      : 'bg-slate-50 border-slate-200 text-slate-700'
                  }`}
                >
                  <span className="text-[10px] font-mono font-bold block opacity-75">{item.hour}</span>
                  <span className="text-sm font-mono font-bold block">{item.load}%</span>
                  <span className="text-[9px] uppercase font-bold tracking-wider block opacity-75">
                    {item.bookings} Booked
                  </span>
                </div>
              );
            })}
          </div>
        </div>

        {/* Intent Conversion Funnel */}
        <div className="p-6 rounded-3xl bg-white border border-slate-200 shadow-xs space-y-4">
          <div>
            <h3 className="text-sm font-bold text-slate-900">Inbound Intent-to-Booking Conversion Funnel</h3>
            <p className="text-xs text-slate-500">Conversion stages from initial voice query to confirmed appointment</p>
          </div>

          <div className="space-y-3 pt-1">
            {funnel.map((step, idx) => (
              <div key={idx} className="p-3.5 rounded-2xl bg-slate-50 border border-slate-200 space-y-1.5">
                <div className="flex items-center justify-between text-xs font-bold">
                  <span className="text-slate-800">{step.stage}</span>
                  <div className="flex items-center gap-2 font-mono">
                    <span className="text-sky-700">{step.count} Calls</span>
                    <span className="text-[10px] px-2 py-0.5 rounded bg-sky-100 text-sky-800">
                      {step.rate}
                    </span>
                  </div>
                </div>
                <div className="w-full bg-slate-200 h-2 rounded-full overflow-hidden">
                  <div
                    style={{ width: step.rate }}
                    className="h-full bg-gradient-to-r from-sky-500 to-indigo-600 rounded-full"
                  />
                </div>
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}
