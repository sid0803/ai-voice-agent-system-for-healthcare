import React, { useState, useEffect } from 'react';
import {
  Search,
  Calendar,
  User,
  Stethoscope,
  Clock,
  Plus,
  RefreshCw,
  XCircle,
  AlertCircle,
  Edit3,
  LayoutGrid,
  List,
  CheckCircle2,
  Lock,
  Ticket,
} from 'lucide-react';
import { api } from '../api';
import { toast } from '../components/Toast';
import { NewAppointmentModal } from '../components/NewAppointmentModal';
import { BookingSlipModal } from '../components/BookingSlipModal';

export function AppointmentsView({ user }) {
  const [appointments, setAppointments] = useState([]);
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState('');
  const [viewMode, setViewMode] = useState('matrix'); // 'matrix' | 'list'
  const [cancellingId, setCancellingId] = useState(null);
  const [cancelModalItem, setCancelModalItem] = useState(null);
  const [isNewModalOpen, setIsNewModalOpen] = useState(false);
  const [rescheduleItem, setRescheduleItem] = useState(null);
  const [prefilledSlot, setPrefilledSlot] = useState(null);
  const [selectedSlipApt, setSelectedSlipApt] = useState(null);


  const canCancel = user?.permissions?.includes('appointments.cancel') || user?.role === 'hospital_admin' || user?.role === 'staff';
  const canBook = user?.permissions?.includes('appointments.write') || user?.role === 'hospital_admin' || user?.role === 'staff';

  useEffect(() => {
    loadAppointments();
  }, []);

  const loadAppointments = async () => {
    try {
      setLoading(true);
      const data = await api.getAppointments();
      setAppointments(data.appointments || []);
    } catch (err) {
      console.error('Failed to load appointments:', err);
    } finally {
      setLoading(false);
    }
  };

  const handleConfirmCancel = async () => {
    if (!cancelModalItem) return;
    try {
      setCancellingId(cancelModalItem.appointment_id);
      const idempotencyKey = `cancel_${cancelModalItem.appointment_id}_${Date.now()}`;
      await api.cancelAppointment(cancelModalItem.appointment_id, idempotencyKey, 'Cancelled via Hospital Portal');

      setAppointments((prev) =>
        prev.map((a) =>
          a.appointment_id === cancelModalItem.appointment_id ? { ...a, status: 'CANCELLED' } : a
        )
      );
      toast.success(`Appointment ${cancelModalItem.appointment_id} for ${cancelModalItem.patient_name} cancelled`);
      setCancelModalItem(null);
    } catch (err) {
      toast.error(err.message || 'Cancellation failed');
    } finally {
      setCancellingId(null);
    }
  };

  const handleAppointmentCreated = (newApt) => {
    setAppointments((prev) => [newApt, ...prev]);
  };

  const handleSlotClick = (docName, slotTime, existingBooking) => {
    if (existingBooking) {
      setRescheduleItem(existingBooking);
    } else if (canBook) {
      setPrefilledSlot({ doctor: docName, time: slotTime });
      setIsNewModalOpen(true);
    }
  };

  // Doctors & Slot Definitions
  const doctorsList = [
    { name: 'Dr. Amit Sharma', department: 'General Medicine', fee: '₹500', room: 'OPD 104', activeSlots: ['10:00 AM', '10:30 AM', '11:00 AM', '11:30 AM', '12:00 PM', '12:30 PM', '01:00 PM', '01:30 PM'] },
    { name: 'Dr. Priya Patel', department: 'General Medicine', fee: '₹600', room: 'OPD 105', activeSlots: ['04:00 PM', '04:30 PM', '05:00 PM', '05:30 PM', '06:00 PM', '06:30 PM', '07:00 PM', '07:30 PM'] },
    { name: 'Dr. Rajesh Gupta', department: 'Cardiology', fee: '₹1,000', room: 'Suite A', activeSlots: ['11:00 AM', '11:30 AM', '12:00 PM', '12:30 PM', '01:00 PM', '01:30 PM', '02:00 PM', '02:30 PM'] },
    { name: 'Dr. Vikram Mehta', department: 'Orthopedics', fee: '₹800', room: 'OPD 202', activeSlots: ['02:00 PM', '02:30 PM', '03:00 PM', '03:30 PM', '04:00 PM', '04:30 PM', '05:00 PM', '05:30 PM'] },
    { name: 'Dr. Sunita Rao', department: 'Pediatrics', fee: '₹700', room: 'Child OPD', activeSlots: ['09:00 AM', '09:30 AM', '10:00 AM', '10:30 AM', '11:00 AM', '11:30 AM', '12:00 PM', '12:30 PM'] },
  ];

  const timeColumns = ['09:30 AM', '10:00 AM', '10:30 AM', '11:00 AM', '11:30 AM', '12:00 PM', '12:30 PM', '02:00 PM', '03:00 PM', '04:00 PM', '05:00 PM', '06:00 PM'];

  const filtered = appointments.filter((a) => {
    const q = search.toLowerCase();
    return (
      (a.patient_name && a.patient_name.toLowerCase().includes(q)) ||
      (a.doctor_name && a.doctor_name.toLowerCase().includes(q)) ||
      (a.department && a.department.toLowerCase().includes(q)) ||
      (a.appointment_id && a.appointment_id.toLowerCase().includes(q))
    );
  });

  return (
    <div className="p-6 space-y-6">
      {/* Top Header & Actions Bar */}
      <div className="flex flex-col sm:flex-row items-center justify-between gap-4 bg-white p-4 rounded-3xl border border-slate-200 shadow-xs">
        <div className="flex items-center gap-3 w-full sm:w-auto">
          {/* View Toggle */}
          <div className="flex items-center p-1 bg-slate-100 rounded-2xl border border-slate-200">
            <button
              onClick={() => setViewMode('matrix')}
              className={`flex items-center gap-1.5 px-3 py-1.5 rounded-xl text-xs font-bold transition-all ${
                viewMode === 'matrix' ? 'bg-white text-sky-700 shadow-xs' : 'text-slate-600 hover:text-slate-900'
              }`}
            >
              <LayoutGrid className="w-3.5 h-3.5" />
              <span>Doctor Slot Matrix</span>
            </button>
            <button
              onClick={() => setViewMode('list')}
              className={`flex items-center gap-1.5 px-3 py-1.5 rounded-xl text-xs font-bold transition-all ${
                viewMode === 'list' ? 'bg-white text-sky-700 shadow-xs' : 'text-slate-600 hover:text-slate-900'
              }`}
            >
              <List className="w-3.5 h-3.5" />
              <span>Table List ({filtered.length})</span>
            </button>
          </div>

          <div className="relative flex-1 sm:w-72">
            <Search className="w-4 h-4 text-slate-400 absolute left-3.5 top-1/2 -translate-y-1/2" />
            <input
              type="text"
              placeholder="Search patient, doctor, ID..."
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              className="w-full bg-slate-50 border border-slate-200 rounded-xl pl-10 pr-3 py-2 text-xs text-slate-900 placeholder-slate-400 focus:outline-none focus:border-sky-500 focus:bg-white font-medium"
            />
          </div>
        </div>

        <div className="flex items-center gap-3 w-full sm:w-auto justify-end">
          {canBook && (
            <button
              onClick={() => {
                setPrefilledSlot(null);
                setIsNewModalOpen(true);
              }}
              className="px-4 py-2.5 rounded-2xl bg-sky-600 hover:bg-sky-500 text-white font-bold text-xs shadow-md shadow-sky-600/20 flex items-center gap-2 transition-all shrink-0"
            >
              <Plus className="w-4 h-4" />
              <span>Book Walk-In OPD</span>
            </button>
          )}
        </div>
      </div>

      {/* VIEW 1: Doctor Availability Slot Matrix Grid */}
      {viewMode === 'matrix' && (
        <div className="space-y-4">
          {/* Legend */}
          <div className="flex items-center gap-5 text-xs text-slate-600 px-1 font-medium">
            <div className="flex items-center gap-2">
              <span className="w-3 h-3 rounded bg-emerald-100 border border-emerald-300" />
              <span>🟢 Available (1-Click Book)</span>
            </div>
            <div className="flex items-center gap-2">
              <span className="w-3 h-3 rounded bg-amber-100 border border-amber-300" />
              <span>🟡 Voice-Reserved (ASHA)</span>
            </div>
            <div className="flex items-center gap-2">
              <span className="w-3 h-3 rounded bg-rose-100 border border-rose-300" />
              <span>🔴 Booked Patient</span>
            </div>
            <div className="flex items-center gap-2">
              <span className="w-3 h-3 rounded bg-slate-100 border border-slate-200" />
              <span>⚪ Off-Duty</span>
            </div>
          </div>

          {/* Matrix Board */}
          <div className="bg-white rounded-3xl border border-slate-200 overflow-x-auto shadow-xs">
            <div className="min-w-[900px]">
              <div className="grid grid-cols-13 bg-slate-50/80 border-b border-slate-200 text-[10px] uppercase font-bold text-slate-500 tracking-wider">
                <div className="p-3.5 col-span-3">Doctor & Specialty</div>
                {timeColumns.map((col) => (
                  <div key={col} className="p-3.5 text-center font-mono">{col}</div>
                ))}
              </div>

              <div className="divide-y divide-slate-100">
                {doctorsList.map((doc) => (
                  <div key={doc.name} className="grid grid-cols-13 items-center hover:bg-slate-50/60 transition-colors">
                    {/* Doctor Info Box */}
                    <div className="p-4 col-span-3 border-r border-slate-200/80 flex items-center gap-3">
                      <div className="w-9 h-9 rounded-xl bg-sky-50 border border-sky-200 flex items-center justify-center text-sky-600 font-bold shrink-0">
                        <Stethoscope className="w-4 h-4" />
                      </div>
                      <div className="min-w-0">
                        <h4 className="font-bold text-xs text-slate-900 truncate">{doc.name}</h4>
                        <div className="flex items-center gap-2 text-[10px] text-slate-500 mt-0.5">
                          <span className="text-sky-700 font-bold">{doc.fee}</span>
                          <span>•</span>
                          <span>{doc.room}</span>
                        </div>
                      </div>
                    </div>

                    {/* Time Slot Cells */}
                    {timeColumns.map((time) => {
                      const isWorking = doc.activeSlots.some((s) => s.includes(time.substring(0, 5)));
                      const isBooked = appointments.find(
                        (a) =>
                          a.doctor_name === doc.name &&
                          a.visit_datetime &&
                          a.visit_datetime.includes(time.substring(0, 5)) &&
                          a.status !== 'CANCELLED'
                      );

                      return (
                        <div
                          key={time}
                          className="p-2 border-r border-slate-100 text-center flex items-center justify-center"
                        >
                          {!isWorking ? (
                            <span className="w-8 h-8 rounded-lg bg-slate-50 border border-slate-100 flex items-center justify-center text-[10px] text-slate-300 select-none">
                              -
                            </span>
                          ) : isBooked ? (
                            <button
                              onClick={() => handleSlotClick(doc.name, time, isBooked)}
                              className="w-full py-1.5 px-1 rounded-lg bg-rose-50 border border-rose-200 text-rose-700 hover:bg-rose-100 transition-all text-[10px] font-mono font-bold truncate shadow-xs"
                              title={`Booked: ${isBooked.patient_name} (${isBooked.appointment_id})`}
                            >
                              {isBooked.patient_name.split(' ')[0]}
                            </button>
                          ) : (
                            <button
                              onClick={() => handleSlotClick(doc.name, time, null)}
                              className="w-full py-1.5 px-1 rounded-lg bg-emerald-50 border border-emerald-200 text-emerald-700 hover:bg-emerald-100 transition-all text-[10px] font-mono font-bold shadow-xs"
                              title="Click to Book Slot"
                            >
                              Open
                            </button>
                          )}
                        </div>
                      );
                    })}
                  </div>
                ))}
              </div>
            </div>
          </div>
        </div>
      )}

      {/* VIEW 2: Standard Table List */}
      {viewMode === 'list' && (
        <div className="bg-white rounded-3xl border border-slate-200 overflow-hidden shadow-xs">
          <div className="overflow-x-auto">
            <table className="w-full text-left text-xs">
              <thead className="bg-slate-50 border-b border-slate-200 text-slate-500 font-bold uppercase text-[10px] tracking-wider">
                <tr>
                  <th className="p-4">Reference ID</th>
                  <th className="p-4">Patient Name</th>
                  <th className="p-4">Assigned Doctor</th>
                  <th className="p-4">Department</th>
                  <th className="p-4">Visit Date/Time</th>
                  <th className="p-4">Status</th>
                  <th className="p-4 text-right">Actions</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100 text-slate-700 font-medium">
                {loading ? (
                  <tr>
                    <td colSpan={7} className="text-center py-12 text-slate-400">
                      Loading appointments from DynamoDB...
                    </td>
                  </tr>
                ) : filtered.length === 0 ? (
                  <tr>
                    <td colSpan={7} className="text-center py-12 text-slate-400">
                      No appointments match search.
                    </td>
                  </tr>
                ) : (
                  filtered.map((apt) => {
                    const isCancelled = apt.status === 'CANCELLED';
                    return (
                      <tr key={apt.appointment_id} className="hover:bg-slate-50 transition-colors">
                        <td className="p-4 font-mono font-bold text-sky-700">{apt.appointment_id}</td>
                        <td className="p-4 font-bold text-slate-900 flex items-center gap-2">
                          <User className="w-3.5 h-3.5 text-slate-400" />
                          <span>{apt.patient_name}</span>
                        </td>
                        <td className="p-4 text-slate-800">
                          <div className="flex items-center gap-1.5 font-semibold">
                            <Stethoscope className="w-3.5 h-3.5 text-sky-600" />
                            <span>{apt.doctor_name}</span>
                          </div>
                        </td>
                        <td className="p-4 text-slate-500">{apt.department}</td>
                        <td className="p-4 font-mono text-slate-700">{apt.visit_datetime}</td>
                        <td className="p-4">
                          <span
                            className={`px-2.5 py-0.5 rounded-full text-[10px] uppercase font-bold border ${
                              isCancelled
                                ? 'bg-rose-50 text-rose-700 border-rose-200'
                                : 'bg-emerald-50 text-emerald-700 border-emerald-200'
                            }`}
                          >
                            {apt.status || 'CONFIRMED'}
                          </span>
                        </td>
                        <td className="p-4 text-right">
                          <div className="flex items-center justify-end gap-2">
                            {!isCancelled && (
                              <button
                                onClick={() => setSelectedSlipApt(apt)}
                                className="px-2.5 py-1 rounded-xl bg-sky-50 hover:bg-sky-100 text-sky-700 text-xs font-bold border border-sky-200 flex items-center gap-1 transition-colors shadow-xs"
                                title="Generate Digital Pass / PDF Slip"
                              >
                                <Ticket className="w-3.5 h-3.5" />
                                <span>Pass</span>
                              </button>
                            )}
                            {!isCancelled && canBook && (
                              <button
                                onClick={() => setRescheduleItem(apt)}
                                className="p-1.5 rounded-lg text-slate-500 hover:text-sky-600 hover:bg-slate-100 transition-colors"
                                title="Reschedule Appointment"
                              >
                                <Edit3 className="w-3.5 h-3.5" />
                              </button>
                            )}
                            {canCancel && !isCancelled && (
                              <button
                                onClick={() => setCancelModalItem(apt)}
                                disabled={cancellingId === apt.appointment_id}
                                className="px-3 py-1 rounded-xl text-xs font-bold text-rose-600 hover:bg-rose-50 border border-transparent hover:border-rose-200 transition-colors"
                              >
                                Cancel
                              </button>
                            )}
                          </div>
                        </td>
                      </tr>
                    );
                  })
                )}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* Digital Appointment Pass & WhatsApp Slip Modal */}
      <BookingSlipModal
        isOpen={Boolean(selectedSlipApt)}
        onClose={() => setSelectedSlipApt(null)}
        appointment={selectedSlipApt}
      />

      {/* Cancellation Confirmation Modal */}
      {cancelModalItem && (
        <div className="fixed inset-0 z-50 bg-slate-900/40 backdrop-blur-sm flex items-center justify-center p-4">
          <div className="bg-white border border-slate-200 rounded-3xl p-6 max-w-md w-full space-y-4 shadow-2xl animate-in zoom-in-95">
            <div className="flex items-center gap-3 text-rose-600">
              <AlertCircle className="w-6 h-6" />
              <h3 className="text-base font-bold text-slate-900">Cancel OPD Appointment</h3>
            </div>
            <p className="text-xs text-slate-600 leading-relaxed">
              Are you sure you want to cancel the appointment for{' '}
              <strong className="text-slate-900">{cancelModalItem.patient_name}</strong> with{' '}
              <strong className="text-slate-900">{cancelModalItem.doctor_name}</strong> on{' '}
              <strong className="text-slate-900">{cancelModalItem.visit_datetime}</strong>?
            </p>
            <p className="text-[11px] text-slate-500 font-mono bg-slate-50 p-2.5 rounded-xl border border-slate-200">
              Idempotency Key: cancel_{cancelModalItem.appointment_id}
            </p>
            <div className="flex items-center justify-end gap-3 pt-2">
              <button
                onClick={() => setCancelModalItem(null)}
                className="px-4 py-2 rounded-xl text-xs font-semibold text-slate-600 hover:bg-slate-100 transition-colors"
              >
                Keep Appointment
              </button>
              <button
                onClick={handleConfirmCancel}
                disabled={cancellingId !== null}
                className="px-4 py-2 rounded-xl text-xs font-bold bg-rose-600 hover:bg-rose-500 text-white transition-colors shadow-sm"
              >
                {cancellingId ? 'Cancelling...' : 'Confirm Cancellation'}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* New Appointment Modal */}
      <NewAppointmentModal
        isOpen={isNewModalOpen}
        onClose={() => setIsNewModalOpen(false)}
        initialPrefill={prefilledSlot}
        onAppointmentCreated={(newApt) => {
          handleAppointmentCreated(newApt);
          setSelectedSlipApt(newApt);
        }}
      />

      {/* Reschedule Modal */}
      <NewAppointmentModal
        isOpen={Boolean(rescheduleItem)}
        onClose={() => setRescheduleItem(null)}
        existingApt={rescheduleItem}
        onAppointmentCreated={(updated) => {
          setAppointments((prev) =>
            prev.map((a) => (a.appointment_id === updated.appointment_id ? updated : a))
          );
          setSelectedSlipApt(updated);
        }}
      />
    </div>
  );
}

