import React, { useState, useEffect } from 'react';
import {
  Search,
  Phone,
  Clock,
  MessageSquare,
  ChevronRight,
  Filter,
  Shield,
  Volume2,
  Calendar,
  Eye,
  Bot,
  User,
  Sparkles,
  PhoneOutgoing,
  Languages,
  HeartHandshake,
  CheckCircle2,
  CalendarPlus,
} from 'lucide-react';
import { api } from '../api';
import { toast } from '../components/Toast';
import { AudioPlayer } from '../components/AudioPlayer';

export function CallsView({ user, onBookAppointment, onTriggerOutbound }) {
  const [calls, setCalls] = useState([]);
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState('');
  const [selectedSessionId, setSelectedSessionId] = useState(null);
  const [transcriptData, setTranscriptData] = useState(null);
  const [loadingTranscript, setLoadingTranscript] = useState(false);
  const [unmasking, setUnmasking] = useState(false);
  const [unmaskedPhones, setUnmaskedPhones] = useState({});

  const canUnmask = user?.permissions?.includes('calls.read_sensitive') || user?.role === 'hospital_admin';

  useEffect(() => {
    loadCalls();
  }, []);

  const loadCalls = async () => {
    try {
      setLoading(true);
      const data = await api.getCalls();
      const list = data.calls || [];
      setCalls(list);
      if (list.length > 0 && !selectedSessionId) {
        handleSelectCall(list[0]);
      }
    } catch (err) {
      console.error('Failed to load call logs:', err);
    } finally {
      setLoading(false);
    }
  };

  const handleSelectCall = async (call) => {
    setSelectedSessionId(call.session_id);
    try {
      setLoadingTranscript(true);
      const data = await api.getCallTranscript(call.session_id);
      setTranscriptData(data);
    } catch (err) {
      console.error('Failed to fetch transcript:', err);
    } finally {
      setLoadingTranscript(false);
    }
  };

  const handleUnmask = async (sessionId) => {
    try {
      setUnmasking(true);
      const res = await api.unmaskPhone(sessionId);
      setUnmaskedPhones((prev) => ({ ...prev, [sessionId]: res.full_phone }));
      toast.success(`Caller phone unmasked: ${res.full_phone} (Audited under HIPAA/NABH)`);
    } catch (err) {
      toast.error(err.message || 'Unmasking failed: unauthorized');
    } finally {
      setUnmasking(false);
    }
  };

  const filteredCalls = calls.filter((c) => {
    const q = search.toLowerCase();
    const phone = unmaskedPhones[c.session_id] || c.caller_phone;
    return (
      phone.toLowerCase().includes(q) ||
      c.session_id.toLowerCase().includes(q) ||
      (c.preview && c.preview.toLowerCase().includes(q))
    );
  });

  const selectedCall = calls.find((c) => c.session_id === selectedSessionId) || calls[0];
  const displayPhone = selectedCall
    ? unmaskedPhones[selectedCall.session_id] || selectedCall.caller_phone
    : '';

  return (
    <div className="flex-1 flex h-[calc(100vh-4rem)] overflow-hidden bg-slate-50">
      {/* Left Master List Pane */}
      <div className="w-80 lg:w-96 border-r border-slate-200 bg-white flex flex-col shrink-0 shadow-xs">
        {/* Search Header */}
        <div className="p-3.5 border-b border-slate-100 space-y-2.5 bg-slate-50/50">
          <div className="relative">
            <Search className="w-4 h-4 text-slate-400 absolute left-3 top-1/2 -translate-y-1/2" />
            <input
              type="text"
              placeholder="Search phone, session, intent..."
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              className="w-full bg-white border border-slate-200 rounded-xl pl-9 pr-3 py-2 text-xs text-slate-900 placeholder-slate-400 focus:outline-none focus:border-sky-500 focus:ring-1 focus:ring-sky-500 font-medium"
            />
          </div>
          <div className="flex items-center justify-between text-[11px] text-slate-500 font-semibold">
            <span className="font-mono">{filteredCalls.length} Telephony Sessions</span>
            <span className="text-sky-700 font-mono flex items-center gap-1">
              <Shield className="w-3 h-3 text-sky-600" />
              <span>PII Masked</span>
            </span>
          </div>
        </div>

        {/* Master Call List */}
        <div className="flex-1 overflow-y-auto divide-y divide-slate-100">
          {loading ? (
            <div className="p-8 text-center text-xs text-slate-400">Loading call recordings...</div>
          ) : filteredCalls.length === 0 ? (
            <div className="p-8 text-center text-xs text-slate-400">No calls found.</div>
          ) : (
            filteredCalls.map((call) => {
              const isSelected = call.session_id === selectedSessionId;
              const phone = unmaskedPhones[call.session_id] || call.caller_phone;

              return (
                <div
                  key={call.session_id}
                  onClick={() => handleSelectCall(call)}
                  className={`p-3.5 cursor-pointer transition-all ${
                    isSelected
                      ? 'bg-sky-50/70 border-l-3 border-sky-600 shadow-xs'
                      : 'hover:bg-slate-50 border-l-3 border-transparent'
                  }`}
                >
                  <div className="flex items-center justify-between mb-1">
                    <div className="flex items-center gap-2">
                      <div className="w-6 h-6 rounded-lg bg-sky-100 text-sky-700 flex items-center justify-center text-xs font-bold">
                        <Phone className="w-3 h-3" />
                      </div>
                      <span className="font-mono font-bold text-xs text-slate-900">{phone}</span>
                    </div>
                    <span className="text-[10px] font-mono text-slate-500 font-semibold">{call.duration}</span>
                  </div>

                  <p className="text-xs text-slate-600 truncate mt-1 font-medium">
                    {call.preview || 'Doctor Consultation & Pricing Inquiry'}
                  </p>

                  <div className="flex items-center justify-between mt-2 text-[10px]">
                    <span className="text-slate-400 font-mono">{call.timestamp}</span>
                    <span className="px-2 py-0.5 rounded bg-sky-50 text-sky-700 border border-sky-200 font-bold uppercase">
                      {call.sentiment || 'Positive'}
                    </span>
                  </div>
                </div>
              );
            })
          )}
        </div>
      </div>

      {/* Right Detail Pane (Patient 360 & Conversation Stream) */}
      {selectedCall ? (
        <div className="flex-1 flex flex-col min-w-0 bg-slate-50/50 overflow-hidden">
          {/* Detail Header / Patient 360 Card */}
          <div className="p-4 border-b border-slate-200 bg-white flex items-center justify-between gap-4 shrink-0 shadow-xs">
            <div className="flex items-center gap-3">
              <div className="w-10 h-10 rounded-2xl bg-sky-50 border border-sky-200 flex items-center justify-center text-sky-600 font-bold">
                <User className="w-5 h-5" />
              </div>
              <div>
                <div className="flex items-center gap-2">
                  <h3 className="font-bold text-sm text-slate-900 font-mono">{displayPhone}</h3>
                  {canUnmask && !unmaskedPhones[selectedCall.session_id] && (
                    <button
                      onClick={() => handleUnmask(selectedCall.session_id)}
                      disabled={unmasking}
                      className="text-[11px] text-sky-700 hover:text-sky-800 font-bold bg-sky-50 px-2.5 py-0.5 rounded-lg border border-sky-200 flex items-center gap-1 transition-colors"
                    >
                      <Eye className="w-3 h-3" />
                      <span>{unmasking ? 'Auditing...' : 'Unmask Phone'}</span>
                    </button>
                  )}
                </div>
                <p className="text-[11px] text-slate-500 font-mono">Session ID: {selectedCall.session_id}</p>
              </div>
            </div>

            {/* Quick Action Buttons */}
            <div className="flex items-center gap-2">
              <button
                onClick={() => onBookAppointment && onBookAppointment(displayPhone)}
                className="px-3.5 py-1.5 rounded-xl bg-sky-50 hover:bg-sky-100 text-sky-700 text-xs font-bold border border-sky-200 flex items-center gap-1.5 transition-all shadow-xs"
              >
                <CalendarPlus className="w-3.5 h-3.5" />
                <span>Book OPD</span>
              </button>
              <button
                onClick={() => onTriggerOutbound && onTriggerOutbound(displayPhone)}
                className="px-3.5 py-1.5 rounded-xl bg-indigo-50 hover:bg-indigo-100 text-indigo-700 text-xs font-bold border border-indigo-200 flex items-center gap-1.5 transition-all shadow-xs"
              >
                <PhoneOutgoing className="w-3.5 h-3.5" />
                <span>Outbound Call</span>
              </button>
            </div>
          </div>

          {/* Audio Waveform Scrubber */}
          <div className="p-4 border-b border-slate-200 bg-white/60 shrink-0">
            <AudioPlayer durationStr={selectedCall.duration} />
          </div>

          {/* Telemetry Chips Bar */}
          <div className="px-5 py-2.5 bg-white border-b border-slate-200 flex items-center justify-between text-xs text-slate-600 shrink-0">
            <div className="flex items-center gap-3">
              <span className="flex items-center gap-1 font-semibold text-slate-800">
                <Sparkles className="w-3.5 h-3.5 text-sky-600" />
                <span>Intent: <strong className="text-sky-700">{transcriptData?.ai_insights?.detected_intent || 'Doctor Consultation & Booking'}</strong></span>
              </span>
              <span>•</span>
              <span className="text-emerald-700 font-semibold">Outcome: {transcriptData?.ai_insights?.resolution || 'Resolved Cleanly'}</span>
            </div>

            <div className="flex items-center gap-3">
              <span className="px-2 py-0.5 rounded bg-sky-50 text-sky-700 border border-sky-200 text-[10px] font-bold uppercase flex items-center gap-1">
                <Languages className="w-3 h-3" />
                <span>Hindi + English</span>
              </span>
              <span className="text-[11px] text-emerald-700 font-bold flex items-center gap-1">
                <HeartHandshake className="w-3.5 h-3.5" />
                <span>94% Positive</span>
              </span>
            </div>
          </div>

          {/* Turn-by-turn Speech Dialogue */}
          <div className="flex-1 overflow-y-auto p-5 space-y-4">
            {loadingTranscript ? (
              <div className="text-center py-16 text-slate-400 text-xs flex items-center justify-center gap-2">
                <Clock className="w-4 h-4 animate-spin text-sky-600" />
                <span>Loading turn-by-turn dialogue...</span>
              </div>
            ) : (transcriptData?.turns || []).length === 0 ? (
              <div className="text-center py-16 text-slate-400 text-xs">No transcript dialogue available for this session.</div>
            ) : (
              (transcriptData?.turns || []).map((turn) => {
                const isAgent = turn.role === 'ASSISTANT' || turn.role === 'MODEL' || turn.role === 'SYSTEM';
                return (
                  <div
                    key={turn.id}
                    className={`flex gap-3 text-xs ${isAgent ? 'flex-row' : 'flex-row-reverse'}`}
                  >
                    <div
                      className={`w-8 h-8 rounded-xl flex items-center justify-center shrink-0 font-bold ${
                        isAgent
                          ? 'bg-sky-100 text-sky-700 border border-sky-200'
                          : 'bg-indigo-100 text-indigo-700 border border-indigo-200'
                      }`}
                    >
                      {isAgent ? <Bot className="w-4 h-4" /> : <User className="w-4 h-4" />}
                    </div>
                    <div
                      className={`max-w-[75%] rounded-2xl p-4 shadow-xs ${
                        isAgent
                          ? 'bg-white border border-slate-200 text-slate-800'
                          : 'bg-sky-600 text-white shadow-sky-600/10'
                      }`}
                    >
                      <div className="flex items-center justify-between gap-4 mb-1.5 text-[10px] opacity-75 font-semibold">
                        <span className="uppercase tracking-wider">{isAgent ? 'ASHA Voice Agent' : 'Patient'}</span>
                        <span className="font-mono">{turn.timestamp || ''}</span>
                      </div>
                      <p className="leading-relaxed whitespace-pre-wrap text-[12px] font-medium">{turn.text}</p>
                    </div>
                  </div>
                );
              })
            )}
          </div>
        </div>
      ) : (
        <div className="flex-1 flex items-center justify-center text-slate-400 text-xs">
          Select a patient call session from the left to inspect conversation.
        </div>
      )}
    </div>
  );
}
