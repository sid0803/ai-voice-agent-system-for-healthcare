import React, { useState } from 'react';
import { X, UserPlus, Shield, Lock, Hospital } from 'lucide-react';
import { api } from '../api';
import { toast } from './Toast';

export function StaffManageModal({ isOpen, onClose, currentHospital = 'apollo_metro' }) {
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [role, setRole] = useState('staff');
  const [loading, setLoading] = useState(false);

  if (!isOpen) return null;

  const handleInvite = async (e) => {
    e.preventDefault();
    if (!username.trim() || !password.trim()) {
      toast.error('Please provide username and password');
      return;
    }

    try {
      setLoading(true);
      await api.inviteUser({
        username: username.trim().toLowerCase(),
        password: password.trim(),
        role: role,
        hospital_id: currentHospital,
      });

      toast.success(`Account created for ${username} with role ${role.replace('_', ' ')}`);
      onClose();
      setUsername('');
      setPassword('');
    } catch (err) {
      toast.error(err.message || 'Failed to create user account');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 bg-slate-900/40 backdrop-blur-sm flex items-center justify-center p-4 animate-in fade-in duration-150">
      <div className="bg-white border border-slate-200 rounded-3xl p-6 max-w-md w-full space-y-5 shadow-2xl animate-in zoom-in-95">
        <div className="flex items-center justify-between border-b border-slate-100 pb-4">
          <div className="flex items-center gap-3">
            <div className="w-10 h-10 rounded-2xl bg-sky-50 border border-sky-200 flex items-center justify-center text-sky-600">
              <UserPlus className="w-5 h-5" />
            </div>
            <div>
              <h3 className="text-base font-bold text-slate-900">Onboard Hospital Staff</h3>
              <p className="text-xs text-slate-500">Create staff, doctor, or receptionist accounts</p>
            </div>
          </div>
          <button
            onClick={onClose}
            className="p-1.5 text-slate-400 hover:text-slate-700 hover:bg-slate-100 rounded-xl transition-colors"
          >
            <X className="w-5 h-5" />
          </button>
        </div>

        <form onSubmit={handleInvite} className="space-y-4">
          <div className="space-y-1.5">
            <label className="text-xs font-bold text-slate-700">Staff Username</label>
            <input
              type="text"
              required
              placeholder="e.g. dr_sharma or reception_2"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              className="w-full bg-slate-50 border border-slate-200 rounded-xl p-2.5 text-xs text-slate-900 focus:outline-none focus:border-sky-500 focus:bg-white font-medium"
            />
          </div>

          <div className="space-y-1.5">
            <label className="text-xs font-bold text-slate-700">Initial Password</label>
            <input
              type="password"
              required
              placeholder="••••••••"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              className="w-full bg-slate-50 border border-slate-200 rounded-xl p-2.5 text-xs text-slate-900 focus:outline-none focus:border-sky-500 focus:bg-white font-medium"
            />
          </div>

          <div className="space-y-1.5">
            <label className="text-xs font-bold text-slate-700">Role & Access Level</label>
            <select
              value={role}
              onChange={(e) => setRole(e.target.value)}
              className="w-full bg-slate-50 border border-slate-200 rounded-xl p-2.5 text-xs text-slate-900 focus:outline-none focus:border-sky-500 focus:bg-white font-medium"
            >
              <option value="receptionist">OPD Receptionist (Bookings & Triage Ack)</option>
              <option value="doctor">Duty Doctor (Clinical Triage & Appointments)</option>
              <option value="staff">Hospital Staff (General Operations)</option>
              <option value="hospital_admin">Hospital Administrator (Full Privileges)</option>
            </select>
          </div>

          <div className="p-3 bg-slate-50 rounded-2xl border border-slate-200 flex items-center justify-between text-xs text-slate-600">
            <div className="flex items-center gap-2">
              <Hospital className="w-4 h-4 text-sky-600" />
              <span>Assigned Tenant: <strong className="text-slate-900">{currentHospital}</strong></span>
            </div>
            <Shield className="w-3.5 h-3.5 text-sky-600" />
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
              {loading ? 'Creating Account...' : 'Create Account'}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
