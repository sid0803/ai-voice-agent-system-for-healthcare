import React from 'react';
import { X, Printer, Send, MessageCircle, Calendar, User, Stethoscope, MapPin, CheckCircle2, ShieldCheck, QrCode } from 'lucide-react';
import { printAppointmentSlip } from '../utils/pdfGenerator';
import { toast } from './Toast';

export function BookingSlipModal({ isOpen, onClose, appointment }) {
  if (!isOpen || !appointment) return null;

  const handlePrint = () => {
    printAppointmentSlip(appointment);
    toast.success('Printing OPD Appointment Pass (PDF preview opened)');
  };

  const handleSendWhatsApp = () => {
    const phone = (appointment.caller_phone || '919876543210').replace(/[^0-9]/g, '');
    const message = encodeURIComponent(
      `🏥 *Apollo Metro Hospital — OPD Appointment Pass*\n\n` +
      `👤 *Patient:* ${appointment.patient_name}\n` +
      `🩺 *Doctor:* ${appointment.doctor_name} (${appointment.department})\n` +
      `📅 *Date & Time:* ${appointment.visit_datetime}\n` +
      `📍 *Location:* OPD Room 104, Apollo Metro Hospital\n` +
      `🎟️ *Reference ID:* ${appointment.appointment_id}\n` +
      `💰 *Consultation Fee:* ₹500\n\n` +
      `_Please arrive 15 minutes before your scheduled slot. Show this reference ID at reception._\n` +
      `Hospital Helpline: 09513886363`
    );

    window.open(`https://wa.me/${phone}?text=${message}`, '_blank');
    toast.success(`WhatsApp confirmation dispatched for ${appointment.patient_name}`);
  };

  return (
    <div className="fixed inset-0 z-50 bg-slate-900/40 backdrop-blur-sm flex items-center justify-center p-4 animate-in fade-in duration-150">
      <div className="bg-white border border-slate-200 rounded-3xl p-6 max-w-lg w-full space-y-5 shadow-2xl animate-in zoom-in-95">
        <div className="flex items-center justify-between border-b border-slate-100 pb-4">
          <div className="flex items-center gap-3">
            <div className="w-10 h-10 rounded-2xl bg-sky-50 border border-sky-200 flex items-center justify-center text-sky-600">
              <Calendar className="w-5 h-5" />
            </div>
            <div>
              <h3 className="text-base font-bold text-slate-900">Digital OPD Appointment Pass</h3>
              <p className="text-xs text-slate-500 font-mono">Reference: {appointment.appointment_id}</p>
            </div>
          </div>
          <button
            onClick={onClose}
            className="p-1.5 text-slate-400 hover:text-slate-700 hover:bg-slate-100 rounded-xl transition-colors"
          >
            <X className="w-5 h-5" />
          </button>
        </div>

        {/* Digital Pass Card */}
        <div className="rounded-3xl border-2 border-sky-500/30 bg-gradient-to-br from-sky-50/60 via-white to-white p-5 space-y-4 shadow-sm relative overflow-hidden">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              <span className="w-2 h-2 rounded-full bg-emerald-500 animate-pulse" />
              <span className="text-xs font-extrabold text-sky-900 uppercase tracking-wider">
                Apollo Metro Hospital • OPD Pass
              </span>
            </div>
            <span className="px-2.5 py-0.5 rounded-full text-[10px] font-mono font-bold bg-emerald-50 text-emerald-700 border border-emerald-200">
              {appointment.status || 'CONFIRMED'}
            </span>
          </div>

          <div className="grid grid-cols-2 gap-4 text-xs">
            <div className="space-y-1">
              <span className="text-[10px] uppercase font-bold text-slate-500">Patient</span>
              <p className="font-bold text-slate-900 text-sm">{appointment.patient_name}</p>
              <p className="text-[11px] text-slate-500 font-mono">{appointment.caller_phone}</p>
            </div>

            <div className="space-y-1">
              <span className="text-[10px] uppercase font-bold text-slate-500">Doctor & OPD</span>
              <p className="font-bold text-sky-700 text-sm">{appointment.doctor_name}</p>
              <p className="text-[11px] text-slate-600">{appointment.department} • Room 104</p>
            </div>
          </div>

          <div className="p-3.5 bg-slate-50 rounded-2xl border border-slate-200/80 flex items-center justify-between text-xs">
            <div className="space-y-0.5">
              <span className="text-[10px] uppercase font-bold text-slate-500">Appointment Schedule</span>
              <p className="font-bold text-slate-900">{appointment.visit_datetime}</p>
            </div>
            <div className="text-right space-y-0.5">
              <span className="text-[10px] uppercase font-bold text-slate-500">Consultation Fee</span>
              <p className="font-mono font-bold text-sky-700">₹500</p>
            </div>
          </div>

          <div className="flex items-center justify-between text-[11px] text-slate-500 pt-1">
            <div className="flex items-center gap-1.5">
              <MapPin className="w-3.5 h-3.5 text-sky-600" />
              <span>Apollo Metro Hospital, Central Block</span>
            </div>
            <div className="flex items-center gap-1 font-mono text-[10px] text-slate-400">
              <QrCode className="w-3.5 h-3.5 text-sky-600" />
              <span>{appointment.appointment_id}</span>
            </div>
          </div>
        </div>

        {/* Action Buttons */}
        <div className="flex items-center justify-end gap-3 pt-2">
          <button
            type="button"
            onClick={handlePrint}
            className="px-4 py-2.5 rounded-2xl bg-slate-100 hover:bg-slate-200 text-slate-800 font-bold text-xs flex items-center gap-2 transition-all shadow-xs"
          >
            <Printer className="w-4 h-4 text-slate-600" />
            <span>Print PDF Slip</span>
          </button>

          <button
            type="button"
            onClick={handleSendWhatsApp}
            className="px-4 py-2.5 rounded-2xl bg-emerald-600 hover:bg-emerald-500 text-white font-bold text-xs flex items-center gap-2 transition-all shadow-md shadow-emerald-600/20"
          >
            <MessageCircle className="w-4 h-4 fill-current" />
            <span>Send WhatsApp Slip</span>
          </button>
        </div>
      </div>
    </div>
  );
}
