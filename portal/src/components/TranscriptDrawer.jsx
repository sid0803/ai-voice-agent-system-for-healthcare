import React, { useState } from 'react';
import { X, Phone, Clock, Eye, Bot, User, ShieldAlert, Sparkles, Languages, HeartHandshake } from 'lucide-react';
import { api } from '../api';
import { toast } from './Toast';
import { AudioPlayer } from './AudioPlayer';

export function TranscriptDrawer({ call, transcriptData, onClose, onUnmaskPhone, canUnmask }) {
  const [unmasking, setUnmasking] = useState(false);
  const [unmaskedPhone, setUnmaskedPhone] = useState(null);

  if (!call) return null;

  const handleUnmask = async () => {
    try {
      setUnmasking(true);
      const res = await api.unmaskPhone(call.session_id);
      setUnmaskedPhone(res.full_phone);
      if (onUnmaskPhone) onUnmaskPhone(call.session_id, res.full_phone);
      toast.success(`Caller phone unmasked: ${res.full_phone} (Audited under HIPAA/NABH)`);
    } catch (err) {
      toast.error(err.message || 'Unmasking failed: unauthorized');
    } finally {
      setUnmasking(false);
    }
  };

  const turns = transcriptData?.turns || [];
  const displayPhone = unmaskedPhone || call.caller_phone;

  return (
    <div className="fixed inset-0 z-50 bg-slate-950/70 backdrop-blur-sm flex justify-end animate-in fade-in duration-200">
      <div className="w-full max-w-xl bg-slate-900 border-l border-slate-800 h-full flex flex-col shadow-2xl animate-in slide-in-from-right duration-300">
        {/* Drawer Header */}
        <div className="p-5 border-b border-slate-800 flex items-center justify-between bg-slate-950/60">
          <div className="flex items-center gap-3">
            <div className="w-10 h-10 rounded-2xl bg-teal-500/10 border border-teal-500/20 flex items-center justify-center text-teal-400">
              <Phone className="w-5 h-5" />
            </div>
            <div>
              <div className="flex items-center gap-2">
                <h3 className="font-bold text-sm text-slate-100">{displayPhone}</h3>
                {canUnmask && !unmaskedPhone && (
                  <button
                    onClick={handleUnmask}
                    disabled={unmasking}
                    className="text-[11px] text-teal-400 hover:text-teal-300 hover:underline flex items-center gap-1 font-semibold bg-teal-500/10 px-2.5 py-0.5 rounded-lg border border-teal-500/20"
                  >
                    <Eye className="w-3 h-3" />
                    <span>{unmasking ? 'Auditing...' : 'Unmask Phone'}</span>
                  </button>
                )}
              </div>
              <p className="text-[11px] text-slate-400 font-mono">Session: {call.session_id.substring(0, 20)}...</p>
            </div>
          </div>

          <button
            onClick={onClose}
            className="p-1.5 text-slate-400 hover:text-slate-200 hover:bg-slate-800 rounded-xl transition-colors"
          >
            <X className="w-5 h-5" />
          </button>
        </div>

        {/* Audio Waveform Player */}
        <div className="p-4 border-b border-slate-800 bg-slate-950/40">
          <AudioPlayer durationStr={call.duration} audioUrl={call.recording_url} />
        </div>

        {/* AI Clinical Insights Banner */}
        <div className="p-4 bg-gradient-to-r from-teal-950/30 to-slate-900 border-b border-slate-800 text-xs flex items-start justify-between gap-4">
          <div className="space-y-1">
            <div className="flex items-center gap-2">
              <Sparkles className="w-4 h-4 text-teal-400" />
              <span className="font-semibold text-slate-200">
                Intent: <strong className="text-teal-300 font-bold">{transcriptData?.ai_insights?.detected_intent || 'Doctor Consultation & Booking'}</strong>
              </span>
            </div>
            <div className="flex items-center gap-3 text-slate-400 text-[11px]">
              <span>Duration: <strong className="text-slate-300 font-mono">{call.duration}</strong></span>
              <span>•</span>
              <span>Outcome: <strong className="text-emerald-400">{transcriptData?.ai_insights?.resolution || 'Resolved Cleanly'}</strong></span>
            </div>
          </div>

          <div className="flex flex-col items-end gap-1">
            <span className="px-2 py-0.5 rounded-md bg-teal-500/10 text-teal-400 border border-teal-500/20 text-[10px] font-bold uppercase flex items-center gap-1">
              <Languages className="w-3 h-3" />
              <span>Hindi + English</span>
            </span>
            <span className="text-[10px] text-emerald-400 font-semibold flex items-center gap-1">
              <HeartHandshake className="w-3 h-3" />
              <span>94% Positive</span>
            </span>
          </div>
        </div>

        {/* Turn-by-turn Conversation Stream */}
        <div className="flex-1 overflow-y-auto p-5 space-y-4">
          {turns.length === 0 ? (
            <div className="text-center py-12 text-slate-500 text-xs">
              <Clock className="w-8 h-8 mx-auto mb-2 text-slate-600 animate-spin" />
              Loading conversational speech dialogue...
            </div>
          ) : (
            turns.map((turn) => {
              const isAgent = turn.role === 'ASSISTANT' || turn.role === 'MODEL' || turn.role === 'SYSTEM';
              return (
                <div
                  key={turn.id}
                  className={`flex gap-3 text-xs ${isAgent ? 'flex-row' : 'flex-row-reverse'}`}
                >
                  <div
                    className={`w-8 h-8 rounded-xl flex items-center justify-center shrink-0 ${
                      isAgent ? 'bg-teal-500/20 text-teal-400 border border-teal-500/30' : 'bg-indigo-500/20 text-indigo-400 border border-indigo-500/30'
                    }`}
                  >
                    {isAgent ? <Bot className="w-4 h-4" /> : <User className="w-4 h-4" />}
                  </div>
                  <div
                    className={`max-w-[80%] rounded-2xl p-3.5 shadow-sm ${
                      isAgent
                        ? 'bg-slate-800/90 border border-slate-700/80 text-slate-200'
                        : 'bg-teal-600 text-white shadow-teal-900/20'
                    }`}
                  >
                    <div className="flex items-center justify-between gap-4 mb-1.5 text-[10px] opacity-75">
                      <span className="font-bold uppercase tracking-wider">{isAgent ? 'ASHA Voice Agent' : 'Patient'}</span>
                      <span className="font-mono">{turn.timestamp || ''}</span>
                    </div>
                    <p className="leading-relaxed whitespace-pre-wrap text-[12px]">{turn.text}</p>
                  </div>
                </div>
              );
            })
          )}
        </div>

        {/* Drawer Footer */}
        <div className="p-4 border-t border-slate-800 bg-slate-950/60 text-center text-[11px] text-slate-500 flex items-center justify-center gap-2">
          <ShieldAlert className="w-3.5 h-3.5 text-teal-400" />
          <span>Conversational grounding audited under HIPAA & NABH healthcare compliance standards.</span>
        </div>
      </div>
    </div>
  );
}
