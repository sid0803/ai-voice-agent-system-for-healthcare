import React, { useState } from 'react';
import { X, PhoneOutgoing, User, ShieldAlert, CheckCircle, Clock } from 'lucide-react';
import { toast } from './Toast';

export function OutboundDispatchModal({ isOpen, onClose, initialPhone = '' }) {
  const [phone, setPhone] = useState(initialPhone || '+91 9876543210');
  const [patientName, setPatientName] = useState('Ramesh Verma');
  const [purpose, setPurpose] = useState('OPD_APPOINTMENT_REMINDER');
  const [confirmed, setConfirmed] = useState(false);
  const [dispatching, setDispatching] = useState(false);

  if (!isOpen) return null;

  const handleDispatch = (e) => {
    e.preventDefault();
    if (!confirmed) {
      toast.warning('Please confirm the mandatory authorization checkbox');
      return;
    }

    setDispatching(true);
    setTimeout(() => {
      setDispatching(false);
      toast.success(
        `Outbound call initiated to ${phone.substring(0, 5)}****** for ${patientName}`,
        'Call Dispatched'
      );
      onClose();
    }, 800);
  };

  const scriptPreviews = {
    OPD_APPOINTMENT_REMINDER:
      'Namaste, this is ASHA calling from Apollo Metro Hospital regarding your upcoming consultation with Dr. Amit Sharma tomorrow at 11:00 AM. Please confirm if you will be attending.',
    DOCTOR_FOLLOWUP:
      'Namaste, this is ASHA calling from Apollo Metro Hospital following up on your consultation. How are your symptoms currently?',
    TRIAGE_CALLBACK:
      'Emergency clinical callback: ASHA voice assistant connecting with on-duty physician for immediate triage consultation.',
  };

  return (
    <div className="fixed inset-0 z-50 bg-slate-900/40 backdrop-blur-sm flex items-center justify-center p-4 animate-in fade-in duration-150">
      <div className="bg-white border border-slate-200 rounded-3xl p-6 max-w-lg w-full space-y-5 shadow-2xl animate-in zoom-in-95">
        <div className="flex items-center justify-between border-b border-slate-100 pb-4">
          <div className="flex items-center gap-3">
            <div className="w-10 h-10 rounded-2xl bg-indigo-50 border border-indigo-200 flex items-center justify-center text-indigo-600">
              <PhoneOutgoing className="w-5 h-5" />
            </div>
            <div>
              <h3 className="text-base font-bold text-slate-900">Dispatch Single Outbound Call</h3>
              <p className="text-xs text-slate-500">Telephony bridge via Exotel with conversational Bedrock AI</p>
            </div>
          </div>
          <button
            onClick={onClose}
            className="p-1.5 text-slate-400 hover:text-slate-700 hover:bg-slate-100 rounded-xl transition-colors"
          >
            <X className="w-5 h-5" />
          </button>
        </div>

        <form onSubmit={handleDispatch} className="space-y-4">
          <div className="grid grid-cols-2 gap-4">
            <div className="space-y-1.5">
              <label className="text-xs font-bold text-slate-700">Patient Phone (E.164)</label>
              <input
                type="text"
                required
                value={phone}
                onChange={(e) => setPhone(e.target.value)}
                placeholder="+91 9876543210"
                className="w-full bg-slate-50 border border-slate-200 rounded-xl p-2.5 text-xs text-slate-900 focus:outline-none focus:border-indigo-500 focus:bg-white font-mono font-medium"
              />
            </div>

            <div className="space-y-1.5">
              <label className="text-xs font-bold text-slate-700">Patient Name</label>
              <input
                type="text"
                required
                value={patientName}
                onChange={(e) => setPatientName(e.target.value)}
                className="w-full bg-slate-50 border border-slate-200 rounded-xl p-2.5 text-xs text-slate-900 focus:outline-none focus:border-indigo-500 focus:bg-white font-medium"
              />
            </div>
          </div>

          <div className="space-y-1.5">
            <label className="text-xs font-bold text-slate-700">Call Purpose & Script Template</label>
            <select
              value={purpose}
              onChange={(e) => setPurpose(e.target.value)}
              className="w-full bg-slate-50 border border-slate-200 rounded-xl p-2.5 text-xs text-slate-900 focus:outline-none focus:border-indigo-500 focus:bg-white font-medium"
            >
              <option value="OPD_APPOINTMENT_REMINDER">OPD Appointment Reminder & Confirmation</option>
              <option value="DOCTOR_FOLLOWUP">Post-Consultation Health Follow-up</option>
              <option value="TRIAGE_CALLBACK">Clinical Emergency Handoff Callback</option>
            </select>
          </div>

          {/* Script Preview Box */}
          <div className="p-3.5 bg-indigo-50/50 rounded-2xl border border-indigo-100 space-y-1">
            <span className="text-[11px] font-bold text-indigo-900">AI Introductory Greeting Preview:</span>
            <p className="text-xs text-indigo-800 italic leading-relaxed">
              "{scriptPreviews[purpose]}"
            </p>
          </div>

          {/* Mandatory Double Confirmation Guard */}
          <div className="p-3.5 rounded-2xl bg-amber-50 border border-amber-200 flex items-start gap-3">
            <input
              type="checkbox"
              id="confirmOutbound"
              checked={confirmed}
              onChange={(e) => setConfirmed(e.target.checked)}
              className="mt-0.5 w-4 h-4 rounded text-indigo-600 border-slate-300 focus:ring-0 cursor-pointer"
            />
            <label htmlFor="confirmOutbound" className="text-xs text-amber-900 font-medium leading-snug cursor-pointer select-none">
              I certify this is an authorized clinical outbound call conforming to hospital policy and TRAI/DND guidelines.
            </label>
          </div>

          <div className="pt-3 border-t border-slate-100 flex items-center justify-end gap-3">
            <button
              type="button"
              onClick={onClose}
              className="px-4 py-2 rounded-xl text-xs font-semibold text-slate-600 hover:bg-slate-100 transition-colors"
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={dispatching || !confirmed}
              className="px-5 py-2.5 rounded-xl text-xs font-bold bg-indigo-600 hover:bg-indigo-500 text-white shadow-md shadow-indigo-600/20 transition-all disabled:opacity-50 flex items-center gap-2"
            >
              {dispatching ? (
                <>
                  <Clock className="w-4 h-4 animate-spin" />
                  <span>Connecting Bridge...</span>
                </>
              ) : (
                <>
                  <PhoneOutgoing className="w-4 h-4" />
                  <span>Trigger Outbound Call</span>
                </>
              )}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
