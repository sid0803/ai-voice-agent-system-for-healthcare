import React, { useState } from 'react';
import { X, Calendar, User, Phone, Stethoscope, Clock, ShieldCheck } from 'lucide-react';
import { toast } from './Toast';

export function NewAppointmentModal({ isOpen, onClose, onAppointmentCreated, existingApt = null, initialPrefill = null }) {
  const isReschedule = Boolean(existingApt);

  const [patientName, setPatientName] = useState(existingApt?.patient_name || '');
  const [callerPhone, setCallerPhone] = useState(existingApt?.caller_phone || '');
  const [department, setDepartment] = useState(existingApt?.department || 'General Medicine');
  const [doctorName, setDoctorName] = useState(initialPrefill?.doctor || existingApt?.doctor_name || 'Dr. Amit Sharma');
  const [visitDate, setVisitDate] = useState('Tomorrow');
  const [timeSlot, setTimeSlot] = useState(initialPrefill?.time || '11:00 AM');
  const [intent, setIntent] = useState(existingApt?.intent || 'Routine Health Checkup');
  const [loading, setLoading] = useState(false);

  if (!isOpen) return null;

  const doctorOptions = {
    'General Medicine': [
      { name: 'Dr. Amit Sharma', fee: '₹500', timings: 'Mon-Sat 10:00 AM - 2:00 PM', room: 'OPD 104' },
      { name: 'Dr. Priya Patel', fee: '₹600', timings: 'Mon-Sat 4:00 PM - 8:00 PM', room: 'OPD 105' },
    ],
    'Cardiology': [
      { name: 'Dr. Rajesh Gupta', fee: '₹1,000', timings: 'Tue, Thu, Sat 11:00 AM - 3:00 PM', room: 'Cardio Suite A' },
    ],
    'Orthopedics': [
      { name: 'Dr. Vikram Mehta', fee: '₹800', timings: 'Mon, Wed, Fri 2:00 PM - 6:00 PM', room: 'Ortho OPD 202' },
    ],
    'Pediatrics': [
      { name: 'Dr. Sunita Rao', fee: '₹700', timings: 'Mon-Fri 9:00 AM - 1:00 PM', room: 'Child Care OPD' },
    ],
  };

  const selectedDoctorInfo =
    (doctorOptions[department] || []).find((d) => d.name === doctorName) || { fee: '₹500', room: 'OPD 104' };

  const handleSubmit = (e) => {
    e.preventDefault();
    if (!patientName.trim()) {
      toast.error('Please enter patient full name');
      return;
    }

    setLoading(true);
    setTimeout(() => {
      const refId = existingApt?.appointment_id || `REF-APP-${Math.floor(1000 + Math.random() * 9000)}`;
      const newApt = {
        appointment_id: refId,
        patient_name: patientName,
        caller_phone: callerPhone || '+91 9876543210',
        doctor_name: doctorName,
        department: department,
        visit_datetime: `${visitDate}, ${timeSlot}`,
        intent: intent,
        status: 'CONFIRMED',
        source: 'portal_manual_intake',
        created_at: new Date().toISOString(),
      };

      if (onAppointmentCreated) onAppointmentCreated(newApt);
      toast.success(
        isReschedule
          ? `Appointment ${refId} rescheduled to ${visitDate} at ${timeSlot}`
          : `Appointment ${refId} booked for ${patientName} with ${doctorName}`
      );
      setLoading(false);
      onClose();
    }, 400);
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
              <h3 className="text-base font-bold text-slate-900">
                {isReschedule ? 'Reschedule OPD Appointment' : 'Book New OPD Appointment'}
              </h3>
              <p className="text-xs text-slate-500">Walk-in intake synchronized with DynamoDB & ASHA Voice Engine</p>
            </div>
          </div>
          <button
            onClick={onClose}
            className="p-1.5 text-slate-400 hover:text-slate-700 hover:bg-slate-100 rounded-xl transition-colors"
          >
            <X className="w-5 h-5" />
          </button>
        </div>

        <form onSubmit={handleSubmit} className="space-y-4">
          <div className="grid grid-cols-2 gap-4">
            <div className="space-y-1.5">
              <label className="text-xs font-bold text-slate-700">Patient Full Name</label>
              <div className="relative">
                <User className="w-4 h-4 text-slate-400 absolute left-3 top-1/2 -translate-y-1/2" />
                <input
                  type="text"
                  required
                  placeholder="e.g. Ramesh Verma"
                  value={patientName}
                  onChange={(e) => setPatientName(e.target.value)}
                  className="w-full bg-slate-50 border border-slate-200 rounded-xl pl-9 pr-3 py-2 text-xs text-slate-900 focus:outline-none focus:border-sky-500 focus:bg-white transition-all font-medium"
                />
              </div>
            </div>

            <div className="space-y-1.5">
              <label className="text-xs font-bold text-slate-700">Contact Number</label>
              <div className="relative">
                <Phone className="w-4 h-4 text-slate-400 absolute left-3 top-1/2 -translate-y-1/2" />
                <input
                  type="text"
                  placeholder="+91 9876543210"
                  value={callerPhone}
                  onChange={(e) => setCallerPhone(e.target.value)}
                  className="w-full bg-slate-50 border border-slate-200 rounded-xl pl-9 pr-3 py-2 text-xs text-slate-900 focus:outline-none focus:border-sky-500 focus:bg-white transition-all font-mono font-medium"
                />
              </div>
            </div>
          </div>

          <div className="grid grid-cols-2 gap-4">
            <div className="space-y-1.5">
              <label className="text-xs font-bold text-slate-700">Department</label>
              <select
                value={department}
                onChange={(e) => {
                  setDepartment(e.target.value);
                  const firstDoc = doctorOptions[e.target.value]?.[0]?.name;
                  if (firstDoc) setDoctorName(firstDoc);
                }}
                className="w-full bg-slate-50 border border-slate-200 rounded-xl p-2 text-xs text-slate-900 focus:outline-none focus:border-sky-500 focus:bg-white font-medium"
              >
                {Object.keys(doctorOptions).map((dept) => (
                  <option key={dept} value={dept}>
                    {dept}
                  </option>
                ))}
              </select>
            </div>

            <div className="space-y-1.5">
              <label className="text-xs font-bold text-slate-700">Doctor</label>
              <select
                value={doctorName}
                onChange={(e) => setDoctorName(e.target.value)}
                className="w-full bg-slate-50 border border-slate-200 rounded-xl p-2 text-xs text-slate-900 focus:outline-none focus:border-sky-500 focus:bg-white font-medium"
              >
                {(doctorOptions[department] || []).map((doc) => (
                  <option key={doc.name} value={doc.name}>
                    {doc.name} ({doc.fee})
                  </option>
                ))}
              </select>
            </div>
          </div>

          {/* Doctor Info Card */}
          <div className="p-3 bg-slate-50 rounded-2xl border border-slate-200/80 flex items-center justify-between text-xs text-slate-600">
            <div className="flex items-center gap-2">
              <Stethoscope className="w-4 h-4 text-sky-600" />
              <span>Location: <strong className="text-slate-900">{selectedDoctorInfo.room}</strong></span>
            </div>
            <span>Consultation Fee: <strong className="text-sky-700 font-bold">{selectedDoctorInfo.fee}</strong></span>
          </div>

          <div className="grid grid-cols-2 gap-4">
            <div className="space-y-1.5">
              <label className="text-xs font-bold text-slate-700">Appointment Date</label>
              <select
                value={visitDate}
                onChange={(e) => setVisitDate(e.target.value)}
                className="w-full bg-slate-50 border border-slate-200 rounded-xl p-2 text-xs text-slate-900 focus:outline-none focus:border-sky-500 focus:bg-white font-medium"
              >
                <option value="Today">Today (Urgent Slot)</option>
                <option value="Tomorrow">Tomorrow</option>
                <option value="Wednesday">Wednesday</option>
                <option value="Thursday">Thursday</option>
                <option value="Friday">Friday</option>
                <option value="Saturday">Saturday</option>
              </select>
            </div>

            <div className="space-y-1.5">
              <label className="text-xs font-bold text-slate-700">Time Slot</label>
              <select
                value={timeSlot}
                onChange={(e) => setTimeSlot(e.target.value)}
                className="w-full bg-slate-50 border border-slate-200 rounded-xl p-2 text-xs text-slate-900 focus:outline-none focus:border-sky-500 focus:bg-white font-medium"
              >
                <option value="10:00 AM">10:00 AM</option>
                <option value="10:30 AM">10:30 AM</option>
                <option value="11:00 AM">11:00 AM</option>
                <option value="11:30 AM">11:30 AM</option>
                <option value="12:00 PM">12:00 PM</option>
                <option value="04:00 PM">04:00 PM</option>
                <option value="04:30 PM">04:30 PM</option>
                <option value="05:00 PM">05:00 PM</option>
                <option value="05:30 PM">05:30 PM</option>
              </select>
            </div>
          </div>

          <div className="space-y-1.5">
            <label className="text-xs font-bold text-slate-700">Chief Complaint / Notes</label>
            <input
              type="text"
              placeholder="e.g. Mild headache and seasonal fever since 2 days"
              value={intent}
              onChange={(e) => setIntent(e.target.value)}
              className="w-full bg-slate-50 border border-slate-200 rounded-xl p-2.5 text-xs text-slate-900 focus:outline-none focus:border-sky-500 focus:bg-white font-medium"
            />
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
              disabled={loading}
              className="px-5 py-2.5 rounded-xl text-xs font-bold bg-sky-600 hover:bg-sky-500 text-white shadow-md shadow-sky-600/20 transition-all disabled:opacity-50"
            >
              {loading ? 'Confirming...' : isReschedule ? 'Update Appointment' : 'Confirm & Generate REF-ID'}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
