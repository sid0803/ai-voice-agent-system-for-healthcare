import React, { useState, useEffect } from 'react';
import {
  AlertOctagon,
  AlertTriangle,
  CheckCircle2,
  Stethoscope,
  Clock,
  ShieldAlert,
  UserCheck,
  PhoneOutgoing,
  Activity,
  Heart,
  Volume2,
  VolumeX,
} from 'lucide-react';
import { api } from '../api';
import { toast } from '../components/Toast';
import { soundEngine } from '../utils/audioAlert';

export function TriageView({ user, onTriggerOutbound }) {
  const [events, setEvents] = useState([]);
  const [loading, setLoading] = useState(true);
  const [activeModal, setActiveModal] = useState(null); // { type: 'ack' | 'resolve', item: ... }
  const [dutyDoctor, setDutyDoctor] = useState('Dr. Amit Sharma');
  const [clinicalNotes, setClinicalNotes] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [audioEnabled, setAudioEnabled] = useState(true);
  const [slaCounters, setSlaCounters] = useState({});

  const canAck = user?.permissions?.includes('triage.acknowledge') || user?.role === 'hospital_admin' || user?.role === 'staff';
  const canResolve = user?.permissions?.includes('triage.resolve') || user?.role === 'hospital_admin' || user?.role === 'doctor';

  useEffect(() => {
    loadTriage();
  }, []);

  // Tick SLA countdown timers
  useEffect(() => {
    const timer = setInterval(() => {
      setSlaCounters((prev) => {
        const next = { ...prev };
        events.forEach((evt) => {
          if (strUpper(evt.status) !== 'RESOLVED') {
            const current = next[evt.event_id] ?? (evt.priority === 'CRITICAL' ? 120 : 600);
            next[evt.event_id] = Math.max(0, current - 1);
          }
        });
        return next;
      });
    }, 1000);

    return () => clearInterval(timer);
  }, [events]);

  const loadTriage = async () => {
    try {
      setLoading(true);
      const data = await api.getTriageEvents();
      const list = data.events || [];
      setEvents(list);

      // Play emergency chime if critical event is open
      const hasCritical = list.some((e) => strUpper(e.priority) === 'CRITICAL' && strUpper(e.status) === 'OPEN');
      if (hasCritical && audioEnabled) {
        soundEngine.playEmergencyChime();
      }
    } catch (err) {
      console.error('Failed to load triage events:', err);
    } finally {
      setLoading(false);
    }
  };

  const handleAcknowledge = async () => {
    if (!activeModal?.item) return;
    try {
      setSubmitting(true);
      await api.acknowledgeTriage(activeModal.item.event_id, dutyDoctor, 'Assigned via Portal');
      setEvents((prev) =>
        prev.map((e) =>
          e.event_id === activeModal.item.event_id
            ? { ...e, status: 'ACKNOWLEDGED', assigned_doctor_id: dutyDoctor }
            : e
        )
      );
      toast.success(`Alert ${activeModal.item.event_id} assigned to ${dutyDoctor}`);
      setActiveModal(null);
    } catch (err) {
      toast.error(err.message || 'Acknowledgement failed');
    } finally {
      setSubmitting(false);
    }
  };

  const handleResolve = async () => {
    if (!activeModal?.item || !clinicalNotes) return;
    try {
      setSubmitting(true);
      await api.resolveTriage(activeModal.item.event_id, clinicalNotes);
      setEvents((prev) =>
        prev.map((e) =>
          e.event_id === activeModal.item.event_id
            ? { ...e, status: 'RESOLVED', clinical_notes: clinicalNotes }
            : e
        )
      );
      soundEngine.playSuccessChime();
      toast.success(`Emergency alert ${activeModal.item.event_id} marked as RESOLVED`);
      setActiveModal(null);
      setClinicalNotes('');
    } catch (err) {
      toast.error(err.message || 'Resolution failed');
    } finally {
      setSubmitting(false);
    }
  };

  const formatSla = (seconds) => {
    const m = Math.floor(seconds / 60);
    const s = seconds % 60;
    return `${m.toString().padStart(2, '0')}:${s.toString().padStart(2, '0')}`;
  };

  return (
    <div className="p-6 space-y-6">
      {/* Triage Live Command Banner */}
      <div className="p-5 rounded-3xl bg-gradient-to-r from-rose-50 via-white to-white border border-rose-200 flex flex-col sm:flex-row items-start sm:items-center justify-between gap-4 shadow-xs">
        <div className="flex items-center gap-3.5">
          <div className="relative">
            <div className="w-3.5 h-3.5 rounded-full bg-rose-500 animate-ping absolute -top-1 -right-1" />
            <div className="w-12 h-12 rounded-2xl bg-rose-100 border border-rose-200 flex items-center justify-center text-rose-600">
              <Heart className="w-6 h-6 animate-pulse" />
            </div>
          </div>
          <div>
            <div className="flex items-center gap-2">
              <h3 className="text-sm font-bold text-slate-900">Emergency Severity Index (ESI 1–5) Triage Board</h3>
              <span className="px-2.5 py-0.5 rounded-full text-[10px] uppercase font-mono font-bold bg-rose-50 text-rose-700 border border-rose-200">
                Live Clinical Queue
              </span>
            </div>
            <p className="text-xs text-rose-800/80 font-medium">
              High-risk cardiac, stroke, and respiratory red-flags flagged in real time with response countdown timers.
            </p>
          </div>
        </div>

        <div className="flex items-center gap-3">
          <button
            onClick={() => {
              setAudioEnabled(!audioEnabled);
              toast.info(`Auditory alert tones ${!audioEnabled ? 'ENABLED' : 'MUTED'}`);
            }}
            className="px-3 py-1.5 rounded-xl bg-white border border-slate-200 text-xs text-slate-700 font-semibold flex items-center gap-1.5 hover:bg-slate-50 transition-colors shadow-xs"
          >
            {audioEnabled ? <Volume2 className="w-4 h-4 text-sky-600" /> : <VolumeX className="w-4 h-4 text-slate-400" />}
            <span>{audioEnabled ? 'Chime Active' : 'Muted'}</span>
          </button>
        </div>
      </div>

      {/* Events Grid */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-5">
        {events.map((evt) => {
          const isCritical = strUpper(evt.priority) === 'CRITICAL';
          const isResolved = strUpper(evt.status) === 'RESOLVED';
          const isAck = strUpper(evt.status) === 'ACKNOWLEDGED';
          const slaSeconds = slaCounters[evt.event_id] ?? (isCritical ? 120 : 600);

          return (
            <div
              key={evt.event_id}
              className={`p-6 rounded-3xl bg-white border transition-all space-y-4 shadow-xs hover:shadow-md ${
                isCritical && !isResolved
                  ? 'border-l-4 border-l-rose-500 border-slate-200'
                  : 'border-slate-200'
              }`}
            >
              <div className="flex items-start justify-between">
                <div className="flex items-center gap-2.5">
                  <span
                    className={`px-3 py-1 rounded-full text-[10px] font-extrabold uppercase tracking-wider ${
                      isCritical ? 'bg-rose-600 text-white shadow-sm' : 'bg-amber-100 text-amber-900 border border-amber-200'
                    }`}
                  >
                    {isCritical ? 'ESI-1 Resuscitation' : 'ESI-2 Emergent'}
                  </span>
                  <span className="text-xs font-mono font-bold text-slate-800">{evt.caller_phone}</span>
                </div>

                <div className="flex items-center gap-2">
                  {/* Live SLA Countdown Badge */}
                  {!isResolved && (
                    <div
                      className={`px-2.5 py-0.5 rounded-lg border font-mono text-[11px] font-bold flex items-center gap-1.5 ${
                        slaSeconds < 30
                          ? 'bg-rose-600 text-white animate-pulse border-rose-600 shadow-sm'
                          : 'bg-amber-50 text-amber-800 border-amber-200'
                      }`}
                    >
                      <Clock className="w-3 h-3" />
                      <span>SLA {formatSla(slaSeconds)}</span>
                    </div>
                  )}

                  <span
                    className={`text-[10px] uppercase font-extrabold px-2.5 py-0.5 rounded-lg border ${
                      isResolved
                        ? 'bg-emerald-50 text-emerald-700 border-emerald-200'
                        : isAck
                        ? 'bg-amber-50 text-amber-700 border-amber-200'
                        : 'bg-rose-50 text-rose-700 border-rose-200 animate-pulse'
                    }`}
                  >
                    {evt.status || 'OPEN'}
                  </span>
                </div>
              </div>

              {/* Symptom Box */}
              <div className="bg-slate-50 p-4 rounded-2xl border border-slate-200/80 space-y-1.5">
                <span className="text-[10px] font-bold text-slate-500 uppercase tracking-wider">Reported Symptoms:</span>
                <p className="text-xs font-semibold text-slate-900 leading-relaxed italic">
                  "{evt.symptoms}"
                </p>
              </div>

              {/* Pain Gauge & Protocol */}
              <div className="grid grid-cols-2 gap-3 pt-1">
                <div className="p-3 bg-slate-50 rounded-2xl border border-slate-200/80 space-y-1">
                  <span className="text-[10px] uppercase font-bold text-slate-500">Pain Score Scale:</span>
                  <div className="flex items-center gap-2">
                    <span className="text-base font-mono font-bold text-rose-600">{evt.pain_score || 8}/10</span>
                    <div className="flex-1 bg-slate-200 h-2 rounded-full overflow-hidden">
                      <div
                        style={{ width: `${(evt.pain_score || 8) * 10}%` }}
                        className="h-full bg-gradient-to-r from-amber-500 to-rose-500 rounded-full"
                      />
                    </div>
                  </div>
                </div>

                <div className="p-3 bg-slate-50 rounded-2xl border border-slate-200/80 space-y-1">
                  <span className="text-[10px] uppercase font-bold text-slate-500">Protocol Action:</span>
                  <p className="text-xs font-bold text-sky-700 truncate">{evt.recommended_action || 'EMERGENCY_HANDOFF'}</p>
                </div>
              </div>

              {/* Physician / Resolution Note (if present) */}
              {evt.clinical_notes && (
                <div className="p-3.5 rounded-2xl bg-emerald-50 border border-emerald-200 text-xs space-y-1">
                  <span className="text-[10px] uppercase font-bold text-emerald-800 flex items-center gap-1">
                    <CheckCircle2 className="w-3.5 h-3.5 text-emerald-600" />
                    <span>Physician Clinical Sign-Off:</span>
                  </span>
                  <p className="text-emerald-950 font-medium text-[11px] leading-relaxed">{evt.clinical_notes}</p>
                </div>
              )}

              {/* Actions Footer */}
              <div className="pt-3 border-t border-slate-100 flex items-center justify-between gap-3">
                <button
                  onClick={() => onTriggerOutbound && onTriggerOutbound(evt.caller_phone)}
                  className="p-2 text-slate-600 hover:text-indigo-600 hover:bg-slate-100 rounded-xl transition-colors flex items-center gap-1.5 text-xs font-semibold"
                >
                  <PhoneOutgoing className="w-3.5 h-3.5" />
                  <span>Callback Patient</span>
                </button>

                <div className="flex items-center gap-2">
                  {!isResolved && !isAck && canAck && (
                    <button
                      onClick={() => setActiveModal({ type: 'ack', item: evt })}
                      className="px-3.5 py-1.5 rounded-xl bg-amber-50 hover:bg-amber-100 text-amber-800 font-bold text-xs border border-amber-200 flex items-center gap-1.5 transition-colors shadow-xs"
                    >
                      <UserCheck className="w-3.5 h-3.5" />
                      <span>Acknowledge & Assign</span>
                    </button>
                  )}

                  {!isResolved && canResolve && (
                    <button
                      onClick={() => setActiveModal({ type: 'resolve', item: evt })}
                      className="px-4 py-1.5 rounded-xl bg-emerald-600 hover:bg-emerald-500 text-white font-bold text-xs shadow-md shadow-emerald-600/20 flex items-center gap-1.5 transition-colors"
                    >
                      <Stethoscope className="w-3.5 h-3.5" />
                      <span>Clinical Resolve</span>
                    </button>
                  )}
                </div>
              </div>
            </div>
          );
        })}
      </div>

      {/* Acknowledge Modal */}
      {activeModal?.type === 'ack' && (
        <div className="fixed inset-0 z-50 bg-slate-900/40 backdrop-blur-sm flex items-center justify-center p-4">
          <div className="bg-white border border-slate-200 rounded-3xl p-6 max-w-md w-full space-y-4 shadow-2xl animate-in zoom-in-95">
            <h3 className="text-base font-bold text-slate-900">Acknowledge Emergency Alert</h3>
            <p className="text-xs text-slate-600">Assign on-duty ER physician for emergency response.</p>
            <div className="space-y-2">
              <label className="text-xs text-slate-700 font-bold">Select Duty Doctor</label>
              <select
                value={dutyDoctor}
                onChange={(e) => setDutyDoctor(e.target.value)}
                className="w-full bg-slate-50 border border-slate-200 rounded-2xl p-2.5 text-xs text-slate-900 focus:outline-none focus:border-sky-500 font-medium"
              >
                <option value="Dr. Amit Sharma">Dr. Amit Sharma (General Medicine)</option>
                <option value="Dr. Priya Patel">Dr. Priya Patel (General Medicine)</option>
                <option value="Dr. Rajesh Gupta">Dr. Rajesh Gupta (Cardiology Specialist)</option>
                <option value="Dr. Emergency Duty">Dr. Emergency Duty (Trauma Care)</option>
              </select>
            </div>
            <div className="flex items-center justify-end gap-3 pt-2">
              <button
                onClick={() => setActiveModal(null)}
                className="px-4 py-2 rounded-xl text-xs font-semibold text-slate-600 hover:bg-slate-100"
              >
                Cancel
              </button>
              <button
                onClick={handleAcknowledge}
                disabled={submitting}
                className="px-4 py-2 rounded-xl text-xs font-bold bg-amber-500 hover:bg-amber-400 text-slate-950 shadow-sm"
              >
                {submitting ? 'Assigning...' : 'Confirm Assignment'}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Clinical Resolve Modal */}
      {activeModal?.type === 'resolve' && (
        <div className="fixed inset-0 z-50 bg-slate-900/40 backdrop-blur-sm flex items-center justify-center p-4">
          <div className="bg-white border border-slate-200 rounded-3xl p-6 max-w-md w-full space-y-4 shadow-2xl animate-in zoom-in-95">
            <h3 className="text-base font-bold text-slate-900 flex items-center gap-2">
              <Stethoscope className="w-5 h-5 text-emerald-600" />
              <span>Resolve Emergency Triage</span>
            </h3>
            <p className="text-xs text-slate-600">Enter physician sign-off notes for permanent medical audit recording.</p>
            <textarea
              rows={4}
              placeholder="e.g. Patient arrived at ER; Initial ECG normal; prescribed nitrates & referred for cardiology follow-up."
              value={clinicalNotes}
              onChange={(e) => setClinicalNotes(e.target.value)}
              className="w-full bg-slate-50 border border-slate-200 rounded-2xl p-3 text-xs text-slate-900 placeholder-slate-400 focus:outline-none focus:border-sky-500 font-medium"
            />
            <div className="flex items-center justify-end gap-3 pt-2">
              <button
                onClick={() => setActiveModal(null)}
                className="px-4 py-2 rounded-xl text-xs font-semibold text-slate-600 hover:bg-slate-100"
              >
                Cancel
              </button>
              <button
                onClick={handleResolve}
                disabled={submitting || !clinicalNotes}
                className="px-4 py-2 rounded-xl text-xs font-bold bg-emerald-600 hover:bg-emerald-500 text-white disabled:opacity-50 shadow-md shadow-emerald-600/20"
              >
                {submitting ? 'Resolving...' : 'Sign Off & Resolve Alert'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

function strUpper(v) {
  return String(v || '').toUpperCase();
}
