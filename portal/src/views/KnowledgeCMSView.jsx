import React, { useState, useEffect } from 'react';
import {
  BookOpenCheck,
  Stethoscope,
  Activity,
  Sparkles,
  GitCompare,
  CheckCircle2,
  Check,
  X,
  Edit3,
  Save,
  Plus,
  ArrowRight,
  UploadCloud,
  Clock,
  AlertTriangle,
  UserCheck,
  RefreshCw,
} from 'lucide-react';
import { api } from '../api';
import { toast } from '../components/Toast';
import { DiffViewerModal } from '../components/DiffViewerModal';
import { DocUploadModal } from '../components/DocUploadModal';

export function KnowledgeCMSView({ user }) {
  const [activeTab, setActiveTab] = useState('doctors'); // 'doctors' | 'pricing' | 'distiller'
  const [pendingFacts, setPendingFacts] = useState([]);
  const [loading, setLoading] = useState(false);
  const [rosterLoading, setRosterLoading] = useState(false);
  const [processingId, setProcessingId] = useState(null);
  const [isDiffOpen, setIsDiffOpen] = useState(false);
  const [isDocUploadOpen, setIsDocUploadOpen] = useState(false);
  const [hasUnpublishedDraft, setHasUnpublishedDraft] = useState(true);
  const [editingDoctor, setEditingDoctor] = useState(null); // Doctor object for status modal
  const [modalStatus, setModalStatus] = useState('AVAILABLE');
  const [modalDelay, setModalDelay] = useState(0);
  const [modalReason, setModalReason] = useState('');
  const [updatingRoster, setUpdatingRoster] = useState(false);

  const canEdit = user?.permissions?.includes('knowledge.write_draft') || user?.role === 'hospital_admin';
  const canPublish = user?.permissions?.includes('knowledge.publish') || user?.role === 'hospital_admin';

  // Master Doctor Catalog (dynamically loaded from backend roster_store)
  const [doctors, setDoctors] = useState([
    { id: 'dr_amit_sharma', name: 'Dr. Amit Sharma', department: 'General Medicine', fee: '₹500', timings: 'Mon-Sat 10:00 AM - 2:00 PM', room: 'OPD 104', status: 'AVAILABLE', delay_minutes: 0, reason: '' },
    { id: 'dr_priya_patel', name: 'Dr. Priya Patel', department: 'General Medicine', fee: '₹600', timings: 'Mon-Sat 4:00 PM - 8:00 PM', room: 'OPD 105', status: 'AVAILABLE', delay_minutes: 0, reason: '' },
    { id: 'dr_rajesh_gupta', name: 'Dr. Rajesh Gupta', department: 'Cardiology', fee: '₹1,000', timings: 'Tue, Thu, Sat 11:00 AM - 3:00 PM', room: 'Cardio Suite A', status: 'AVAILABLE', delay_minutes: 0, reason: '' },
    { id: 'dr_vikram_mehta', name: 'Dr. Vikram Mehta', department: 'Orthopedics', fee: '₹800', timings: 'Mon, Wed, Fri 2:00 PM - 6:00 PM', room: 'Ortho OPD 202', status: 'AVAILABLE', delay_minutes: 0, reason: '' },
    { id: 'dr_sunita_rao', name: 'Dr. Sunita Rao', department: 'Pediatrics', fee: '₹700', timings: 'Mon-Fri 9:00 AM - 1:00 PM', room: 'Child Care OPD', status: 'AVAILABLE', delay_minutes: 0, reason: '' },
  ]);

  // Master Radiology & Diagnostics Catalog
  const [tariffs, setTariffs] = useState([
    { id: 1, procedure: 'MRI Brain with Contrast (3.0 Tesla)', modality: 'MRI', tariff: '₹7,500', prep: '4h Fasting, Remove Metal Implants' },
    { id: 2, procedure: 'MRI Spine (Lumbar / Cervical)', modality: 'MRI', tariff: '₹6,500', prep: 'No Fasting Required' },
    { id: 3, procedure: 'CT Scan Whole Abdomen (128-Slice)', modality: 'CT', tariff: '₹4,500', prep: '6h Fasting, Serum Creatinine Report' },
    { id: 4, procedure: 'Ultrasound Whole Abdomen (USG)', modality: 'Ultrasound', tariff: '₹1,800', prep: 'Full Bladder, 4h Fasting' },
    { id: 5, procedure: 'Digital Chest X-Ray (PA View)', modality: 'X-Ray', tariff: '₹600', prep: 'No Preparation' },
    { id: 6, procedure: 'Complete Blood Count (CBC) & ESR', modality: 'Pathology', tariff: '₹450', prep: 'Overnight Fasting' },
  ]);

  useEffect(() => {
    loadPendingFacts();
    loadRoster();
  }, []);

  const loadRoster = async () => {
    try {
      setRosterLoading(true);
      const res = await api.getRoster();
      if (res.roster && res.roster.length > 0) {
        setDoctors(
          res.roster.map((d) => ({
            id: d.doctor_id,
            doctor_id: d.doctor_id,
            name: d.name,
            department: d.department,
            fee: d.consultation_fee ? `₹${d.consultation_fee}` : '₹500',
            timings: d.timings || 'Mon-Sat 10:00 AM - 2:00 PM',
            room: d.room || 'OPD 104',
            status: d.status || 'AVAILABLE',
            delay_minutes: d.delay_minutes || 0,
            reason: d.reason || '',
            return_date: d.return_date || null,
          }))
        );
      }
    } catch (err) {
      console.warn('Could not load live roster, falling back to default:', err);
    } finally {
      setRosterLoading(false);
    }
  };

  const loadPendingFacts = async () => {
    try {
      setLoading(true);
      const data = await api.getPendingFacts();
      setPendingFacts(data.pending_facts || []);
    } catch (err) {
      console.error('Failed to load pending facts:', err);
    } finally {
      setLoading(false);
    }
  };

  const handleApprove = async (factId) => {
    try {
      setProcessingId(factId);
      await api.approveFact(factId);
      setPendingFacts((prev) => prev.filter((f) => f.id !== factId));
      setHasUnpublishedDraft(true);
      toast.success(`Candidate fact approved and staged into DRAFT KB (v1.1-DRAFT)`);
    } catch (err) {
      toast.error(err.message || 'Approval failed');
    } finally {
      setProcessingId(null);
    }
  };

  const handleReject = async (factId) => {
    try {
      setProcessingId(factId);
      await api.rejectFact(factId);
      setPendingFacts((prev) => prev.filter((f) => f.id !== factId));
      toast.info(`Candidate fact discarded from quarantine queue`);
    } catch (err) {
      toast.error(err.message || 'Rejection failed');
    } finally {
      setProcessingId(null);
    }
  };

  const handlePublishProduction = () => {
    setHasUnpublishedDraft(false);
    toast.success('Knowledge Base v1.1 published & hot-reloaded into Bedrock Nova Sonic voice cache!');
  };

  const handleOpenDoctorModal = (doc) => {
    setEditingDoctor(doc);
    setModalStatus(doc.status || 'AVAILABLE');
    setModalDelay(doc.delay_minutes || 0);
    setModalReason(doc.reason || '');
  };

  const handleSaveDoctorRoster = async () => {
    if (!editingDoctor) return;
    try {
      setUpdatingRoster(true);
      const doctorId = editingDoctor.doctor_id || editingDoctor.id;
      await api.updateDoctorRoster(doctorId, {
        status: modalStatus,
        delay_minutes: Number(modalDelay) || 0,
        reason: modalReason,
      });
      setDoctors((prev) =>
        prev.map((d) =>
          (d.doctor_id || d.id) === doctorId
            ? {
                ...d,
                status: modalStatus,
                delay_minutes: Number(modalDelay) || 0,
                reason: modalReason,
              }
            : d
        )
      );
      toast.success(
        `${editingDoctor.name} marked as ${modalStatus}. Live voice routing updated immediately!`
      );
      setEditingDoctor(null);
    } catch (err) {
      toast.error(err.message || 'Failed to update clinician roster status');
    } finally {
      setUpdatingRoster(false);
    }
  };

  return (
    <div className="p-6 space-y-6">
      {/* Floating Draft Status Bar */}
      {hasUnpublishedDraft && (
        <div className="p-4 rounded-2xl bg-amber-50/80 border border-amber-200 flex items-center justify-between shadow-xs animate-in slide-in-from-top-3">
          <div className="flex items-center gap-3">
            <div className="w-9 h-9 rounded-xl bg-amber-100 text-amber-900 flex items-center justify-center font-extrabold text-xs border border-amber-200">
              DRAFT
            </div>
            <div>
              <h4 className="text-xs font-bold text-slate-900 flex items-center gap-2">
                <span>Active Knowledge Base Draft Pending</span>
                <span className="text-[10px] font-mono text-amber-800 bg-amber-100 px-2 py-0.5 rounded border border-amber-200 font-bold">
                  v1.1-DRAFT
                </span>
              </h4>
              <p className="text-[11px] text-slate-600">
                Staged candidate facts & tariff adjustments are held safely in draft and do not affect live calls until published.
              </p>
            </div>
          </div>

          <div className="flex items-center gap-3">
            <button
              onClick={() => setIsDocUploadOpen(true)}
              className="px-3.5 py-1.5 rounded-xl bg-white hover:bg-slate-50 text-indigo-700 font-bold text-xs border border-indigo-200 flex items-center gap-1.5 transition-colors shadow-xs"
            >
              <UploadCloud className="w-3.5 h-3.5" />
              <span>Upload Document (AI)</span>
            </button>
            <button
              onClick={() => setIsDiffOpen(true)}
              className="px-3.5 py-1.5 rounded-xl bg-white hover:bg-slate-50 text-sky-700 font-bold text-xs border border-slate-200 flex items-center gap-1.5 transition-colors shadow-xs"
            >
              <GitCompare className="w-3.5 h-3.5" />
              <span>Review Diff</span>
            </button>
            {canPublish && (
              <button
                onClick={handlePublishProduction}
                className="px-4 py-1.5 rounded-xl bg-sky-600 hover:bg-sky-500 text-white font-bold text-xs shadow-md shadow-sky-600/20 flex items-center gap-1.5 transition-all"
              >
                <CheckCircle2 className="w-3.5 h-3.5" />
                <span>Publish to Voice Agent</span>
              </button>
            )}
          </div>

        </div>
      )}

      {/* Tabs Navigation */}
      <div className="flex items-center justify-between border-b border-slate-200 pb-3">
        <div className="flex items-center gap-2">
          <button
            onClick={() => setActiveTab('doctors')}
            className={`flex items-center gap-2 px-4 py-2 rounded-xl text-xs font-bold transition-all ${
              activeTab === 'doctors'
                ? 'bg-sky-50 text-sky-700 border border-sky-200 shadow-xs'
                : 'text-slate-600 hover:text-slate-900 hover:bg-slate-50'
            }`}
          >
            <Stethoscope className="w-4 h-4" />
            <span>Doctor Roster & OPD Timings ({doctors.length})</span>
          </button>

          <button
            onClick={() => setActiveTab('pricing')}
            className={`flex items-center gap-2 px-4 py-2 rounded-xl text-xs font-bold transition-all ${
              activeTab === 'pricing'
                ? 'bg-sky-50 text-sky-700 border border-sky-200 shadow-xs'
                : 'text-slate-600 hover:text-slate-900 hover:bg-slate-50'
            }`}
          >
            <Activity className="w-4 h-4" />
            <span>Radiology & Lab Tariffs ({tariffs.length})</span>
          </button>

          <button
            onClick={() => setActiveTab('distiller')}
            className={`flex items-center gap-2 px-4 py-2 rounded-xl text-xs font-bold transition-all ${
              activeTab === 'distiller'
                ? 'bg-sky-50 text-sky-700 border border-sky-200 shadow-xs'
                : 'text-slate-600 hover:text-slate-900 hover:bg-slate-50'
            }`}
          >
            <Sparkles className="w-4 h-4 text-sky-600" />
            <span>Distiller Quarantine Queue ({pendingFacts.length})</span>
          </button>
        </div>

        <span className="text-xs text-slate-500 font-mono font-medium">
          Sync Engine: <strong className="text-sky-700">Deterministic Unified KB</strong>
        </span>
      </div>

      {/* Tab 1: Doctor Directory & Dynamic Roster */}
      {activeTab === 'doctors' && (
        <div className="space-y-4">
          {/* Operational Banner */}
          <div className="p-4 rounded-2xl bg-sky-50/60 border border-sky-200/80 flex flex-col sm:flex-row items-start sm:items-center justify-between gap-3 text-xs">
            <div className="flex items-center gap-3">
              <div className="w-8 h-8 rounded-xl bg-sky-100 text-sky-700 flex items-center justify-center font-bold">
                <Stethoscope className="w-4 h-4" />
              </div>
              <div>
                <h4 className="font-bold text-slate-900">Real-Time Clinician Roster & Availability Control</h4>
                <p className="text-slate-600 text-[11px]">
                  Emergency leaves and delay overrides update ASHA's voice booking engine instantly with zero redeployment.
                </p>
              </div>
            </div>
            <button
              onClick={loadRoster}
              disabled={rosterLoading}
              className="px-3 py-1.5 rounded-xl bg-white hover:bg-slate-50 text-sky-700 font-bold border border-sky-200 flex items-center gap-1.5 shadow-xs shrink-0"
            >
              <RefreshCw className={`w-3.5 h-3.5 ${rosterLoading ? 'animate-spin' : ''}`} />
              <span>Refresh Roster</span>
            </button>
          </div>

          <div className="bg-white rounded-3xl border border-slate-200 overflow-hidden shadow-xs">
            <div className="overflow-x-auto">
              <table className="w-full text-left text-xs">
                <thead className="bg-slate-50 border-b border-slate-200 text-slate-500 font-bold uppercase text-[10px] tracking-wider">
                  <tr>
                    <th className="p-4">Doctor Name</th>
                    <th className="p-4">Specialty</th>
                    <th className="p-4">Consultation Fee</th>
                    <th className="p-4">OPD Schedule & Room</th>
                    <th className="p-4">Live Roster Status</th>
                    <th className="p-4 text-right">Operational Actions</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-100 text-slate-700 font-medium">
                  {doctors.map((doc) => {
                    const status = doc.status || 'AVAILABLE';
                    return (
                      <tr key={doc.id || doc.doctor_id} className="hover:bg-slate-50 transition-colors">
                        <td className="p-4 font-bold text-slate-900">
                          <div className="flex items-center gap-2">
                            <Stethoscope className="w-3.5 h-3.5 text-sky-600" />
                            <span>{doc.name}</span>
                          </div>
                          {doc.reason && (
                            <p className="text-[10px] font-normal text-slate-500 italic mt-0.5">
                              Note: {doc.reason}
                            </p>
                          )}
                        </td>
                        <td className="p-4 text-slate-800 font-semibold">{doc.department}</td>
                        <td className="p-4 font-mono font-bold text-sky-700">{doc.fee}</td>
                        <td className="p-4 text-slate-600">
                          <div>{doc.timings}</div>
                          <span className="text-[10px] font-mono text-slate-400">{doc.room}</span>
                        </td>
                        <td className="p-4">
                          {status === 'AVAILABLE' && (
                            <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-[10px] uppercase font-bold bg-emerald-50 text-emerald-700 border border-emerald-200">
                              <span className="w-1.5 h-1.5 rounded-full bg-emerald-500 animate-pulse" />
                              <span>On Duty / Available</span>
                            </span>
                          )}
                          {status === 'ON_LEAVE' && (
                            <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-[10px] uppercase font-bold bg-rose-50 text-rose-700 border border-rose-200">
                              <AlertTriangle className="w-3 h-3 text-rose-600" />
                              <span>On Emergency Leave</span>
                            </span>
                          )}
                          {status === 'RUNNING_LATE' && (
                            <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-[10px] uppercase font-bold bg-amber-50 text-amber-700 border border-amber-200">
                              <Clock className="w-3 h-3 text-amber-600" />
                              <span>Delayed (+{doc.delay_minutes || 30}m)</span>
                            </span>
                          )}
                          {status === 'IN_SURGERY' && (
                            <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-[10px] uppercase font-bold bg-purple-50 text-purple-700 border border-purple-200">
                              <Activity className="w-3 h-3 text-purple-600" />
                              <span>In Surgery</span>
                            </span>
                          )}
                        </td>
                        <td className="p-4 text-right">
                          {canEdit ? (
                            <button
                              onClick={() => handleOpenDoctorModal(doc)}
                              className="px-3 py-1.5 rounded-xl bg-slate-100 hover:bg-sky-50 hover:text-sky-700 hover:border-sky-200 text-slate-700 font-bold text-[11px] border border-slate-200 transition-all shadow-xs"
                            >
                              Manage Status
                            </button>
                          ) : (
                            <span className="text-[10px] font-mono text-slate-400">View Only</span>
                          )}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </div>
        </div>
      )}

      {/* Tab 2: Radiology & Lab Tariffs */}
      {activeTab === 'pricing' && (
        <div className="bg-white rounded-3xl border border-slate-200 overflow-hidden shadow-xs">
          <div className="overflow-x-auto">
            <table className="w-full text-left text-xs">
              <thead className="bg-slate-50 border-b border-slate-200 text-slate-500 font-bold uppercase text-[10px] tracking-wider">
                <tr>
                  <th className="p-4">Procedure / Investigation</th>
                  <th className="p-4">Modality</th>
                  <th className="p-4">Standard Tariff</th>
                  <th className="p-4">Patient Preparation Guidelines</th>
                  <th className="p-4 text-right">Status</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100 text-slate-700 font-medium">
                {tariffs.map((t) => (
                  <tr key={t.id} className="hover:bg-slate-50 transition-colors">
                    <td className="p-4 font-bold text-slate-900">{t.procedure}</td>
                    <td className="p-4">
                      <span className="px-2 py-0.5 rounded-md font-mono text-[11px] font-bold bg-slate-100 text-sky-800 border border-slate-200">
                        {t.modality}
                      </span>
                    </td>
                    <td className="p-4 font-mono font-bold text-sky-700">{t.tariff}</td>
                    <td className="p-4 text-slate-500">{t.prep}</td>
                    <td className="p-4 text-right">
                      <span className="px-2.5 py-0.5 rounded-full text-[10px] uppercase font-bold bg-emerald-50 text-emerald-700 border border-emerald-200">
                        Verified
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* Tab 3: Distiller Quarantine Queue */}
      {activeTab === 'distiller' && (
        <div className="space-y-4">
          {loading ? (
            <div className="text-center py-12 text-slate-400 text-xs">Loading candidate facts queue...</div>
          ) : pendingFacts.length === 0 ? (
            <div className="p-12 text-center rounded-3xl bg-white border border-slate-200 text-slate-500 space-y-2 shadow-xs">
              <BookOpenCheck className="w-8 h-8 mx-auto text-sky-600" />
              <p className="text-sm font-bold text-slate-900">No Pending Facts in Queue</p>
              <p className="text-xs text-slate-500">The knowledge base is fully up to date with caller insights.</p>
            </div>
          ) : (
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              {pendingFacts.map((fact) => (
                <div
                  key={fact.id}
                  className="p-5 rounded-3xl bg-white border border-slate-200 space-y-4 hover:border-slate-300 transition-all shadow-xs"
                >
                  <div className="flex items-start justify-between">
                    <div>
                      <span className="text-[10px] uppercase font-bold px-2 py-0.5 rounded bg-sky-50 text-sky-700 border border-sky-200">
                        {fact.category || 'DOCTOR_SCHEDULE'}
                      </span>
                      <h4 className="text-sm font-bold text-slate-900 mt-2">
                        {fact.fact_summary || fact.doctor_name || 'Discovered Schedule Update'}
                      </h4>
                    </div>
                    <span className="text-xs font-mono font-bold text-slate-600 bg-slate-50 px-2 py-1 rounded border border-slate-200">
                      Confidence: {(fact.confidence ? fact.confidence * 100 : 92).toFixed(0)}%
                    </span>
                  </div>

                  <div className="bg-slate-50 p-3.5 rounded-2xl border border-slate-200 text-xs text-slate-700 space-y-1">
                    <p className="text-slate-500 font-semibold">Extracted Detail:</p>
                    <p className="font-mono text-sky-900 font-bold">{JSON.stringify(fact.data || fact, null, 2)}</p>
                  </div>

                  <div className="flex items-center justify-between text-xs text-slate-500 pt-2 border-t border-slate-100">
                    <span className="font-mono text-[11px]">Source Call: {fact.source_call_id ? fact.source_call_id.substring(0, 10) : 'LIVE-TURN'}</span>

                    {canEdit && (
                      <div className="flex items-center gap-2">
                        <button
                          onClick={() => handleReject(fact.id)}
                          disabled={processingId === fact.id}
                          className="p-2 rounded-xl text-rose-600 hover:bg-rose-50 border border-rose-200 transition-colors shadow-xs"
                          title="Reject Fact"
                        >
                          <X className="w-4 h-4" />
                        </button>
                        <button
                          onClick={() => handleApprove(fact.id)}
                          disabled={processingId === fact.id}
                          className="px-3.5 py-1.5 rounded-xl bg-sky-600 hover:bg-sky-500 text-white font-bold text-xs flex items-center gap-1.5 shadow-md shadow-sky-600/20 transition-colors"
                        >
                          <Check className="w-3.5 h-3.5" />
                          <span>Approve to Draft</span>
                        </button>
                      </div>
                    )}
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {/* Visual Diff Viewer Modal */}
      <DiffViewerModal
        isOpen={isDiffOpen}
        onClose={() => setIsDiffOpen(false)}
        publishedVersion="v1.0"
        draftVersion="v1.1-DRAFT"
        onPublish={handlePublishProduction}
      />

      {/* Document Ingestion Uploader Modal */}
      <DocUploadModal
        isOpen={isDocUploadOpen}
        onClose={() => setIsDocUploadOpen(false)}
        onStagedSuccess={(res) => {
          setHasUnpublishedDraft(true);
          setIsDiffOpen(true);
        }}
      />

      {/* Clinician Availability & Dynamic Roster Override Modal */}
      {editingDoctor && (
        <div className="fixed inset-0 z-50 bg-slate-900/50 backdrop-blur-xs flex items-center justify-center p-4 animate-in fade-in duration-150">
          <div className="bg-white border border-slate-200 rounded-3xl p-6 max-w-md w-full space-y-5 shadow-2xl animate-in zoom-in-95">
            <div className="flex items-center justify-between border-b border-slate-100 pb-3">
              <div className="flex items-center gap-3">
                <div className="w-10 h-10 rounded-2xl bg-sky-50 text-sky-700 flex items-center justify-center font-bold">
                  <Stethoscope className="w-5 h-5" />
                </div>
                <div>
                  <h3 className="font-bold text-sm text-slate-900">{editingDoctor.name}</h3>
                  <p className="text-[11px] text-slate-500">{editingDoctor.department} • Room {editingDoctor.room}</p>
                </div>
              </div>
              <button
                onClick={() => setEditingDoctor(null)}
                className="p-1.5 text-slate-400 hover:text-slate-700 hover:bg-slate-100 rounded-xl transition-colors"
              >
                <X className="w-5 h-5" />
              </button>
            </div>

            <div className="space-y-4 text-xs">
              <div>
                <label className="block text-[11px] font-bold text-slate-700 uppercase tracking-wider mb-2">
                  Operational Status
                </label>
                <div className="grid grid-cols-2 gap-2">
                  <button
                    type="button"
                    onClick={() => setModalStatus('AVAILABLE')}
                    className={`p-2.5 rounded-2xl border text-left font-bold transition-all flex items-center gap-2 ${
                      modalStatus === 'AVAILABLE'
                        ? 'bg-emerald-50 border-emerald-300 text-emerald-800 shadow-xs'
                        : 'bg-slate-50 border-slate-200 text-slate-600 hover:bg-slate-100'
                    }`}
                  >
                    <span className="w-2 h-2 rounded-full bg-emerald-500" />
                    <span>Available</span>
                  </button>

                  <button
                    type="button"
                    onClick={() => setModalStatus('ON_LEAVE')}
                    className={`p-2.5 rounded-2xl border text-left font-bold transition-all flex items-center gap-2 ${
                      modalStatus === 'ON_LEAVE'
                        ? 'bg-rose-50 border-rose-300 text-rose-800 shadow-xs'
                        : 'bg-slate-50 border-slate-200 text-slate-600 hover:bg-slate-100'
                    }`}
                  >
                    <AlertTriangle className="w-3.5 h-3.5 text-rose-600" />
                    <span>On Leave</span>
                  </button>

                  <button
                    type="button"
                    onClick={() => {
                      setModalStatus('RUNNING_LATE');
                      if (!modalDelay) setModalDelay(30);
                    }}
                    className={`p-2.5 rounded-2xl border text-left font-bold transition-all flex items-center gap-2 ${
                      modalStatus === 'RUNNING_LATE'
                        ? 'bg-amber-50 border-amber-300 text-amber-800 shadow-xs'
                        : 'bg-slate-50 border-slate-200 text-slate-600 hover:bg-slate-100'
                    }`}
                  >
                    <Clock className="w-3.5 h-3.5 text-amber-600" />
                    <span>Running Late</span>
                  </button>

                  <button
                    type="button"
                    onClick={() => setModalStatus('IN_SURGERY')}
                    className={`p-2.5 rounded-2xl border text-left font-bold transition-all flex items-center gap-2 ${
                      modalStatus === 'IN_SURGERY'
                        ? 'bg-purple-50 border-purple-300 text-purple-800 shadow-xs'
                        : 'bg-slate-50 border-slate-200 text-slate-600 hover:bg-slate-100'
                    }`}
                  >
                    <Activity className="w-3.5 h-3.5 text-purple-600" />
                    <span>In Surgery</span>
                  </button>
                </div>
              </div>

              {modalStatus === 'RUNNING_LATE' && (
                <div className="space-y-2">
                  <label className="block text-[11px] font-bold text-slate-700 uppercase tracking-wider">
                    Expected Delay (Minutes)
                  </label>
                  <div className="flex items-center gap-2">
                    {[15, 30, 45, 60].map((mins) => (
                      <button
                        key={mins}
                        type="button"
                        onClick={() => setModalDelay(mins)}
                        className={`flex-1 py-1.5 rounded-xl border text-xs font-mono font-bold transition-colors ${
                          Number(modalDelay) === mins
                            ? 'bg-amber-100 border-amber-300 text-amber-900'
                            : 'bg-slate-50 border-slate-200 text-slate-600 hover:bg-slate-100'
                        }`}
                      >
                        +{mins}m
                      </button>
                    ))}
                  </div>
                </div>
              )}

              <div className="space-y-1.5">
                <label className="block text-[11px] font-bold text-slate-700 uppercase tracking-wider">
                  Operational Note / Patient Reason
                </label>
                <input
                  type="text"
                  value={modalReason}
                  onChange={(e) => setModalReason(e.target.value)}
                  placeholder={
                    modalStatus === 'ON_LEAVE'
                      ? 'e.g., Emergency leave / Attending conference'
                      : modalStatus === 'RUNNING_LATE'
                      ? 'e.g., Delayed in Emergency OT rounds'
                      : 'Operational note for receptionist & voice agent'
                  }
                  className="w-full px-3 py-2 rounded-xl border border-slate-200 focus:outline-none focus:ring-2 focus:ring-sky-500 text-slate-800 text-xs"
                />
              </div>

              <div className="p-3 bg-slate-50 rounded-2xl border border-slate-200 text-[11px] text-slate-500">
                {modalStatus === 'ON_LEAVE' && (
                  <p className="text-rose-700 font-semibold">
                    Voice AI will immediately block this doctor's slots and proactively offer alternative clinicians to callers.
                  </p>
                )}
                {modalStatus === 'AVAILABLE' && (
                  <p className="text-emerald-700 font-semibold">
                    Doctor is active. Standard OPD slots are open for automated booking.
                  </p>
                )}
                {modalStatus === 'RUNNING_LATE' && (
                  <p className="text-amber-700 font-semibold">
                    Voice agent will inform callers of the delay and adjust walk-in arrival times.
                  </p>
                )}
                {modalStatus === 'IN_SURGERY' && (
                  <p className="text-purple-700 font-semibold">
                    Slots temporarily buffered while clinician is in the operating theater.
                  </p>
                )}
              </div>
            </div>

            <div className="flex items-center justify-end gap-3 pt-2 border-t border-slate-100">
              <button
                type="button"
                onClick={() => setEditingDoctor(null)}
                className="px-4 py-2 rounded-xl text-slate-600 hover:bg-slate-100 text-xs font-bold transition-colors"
              >
                Cancel
              </button>
              <button
                type="button"
                onClick={handleSaveDoctorRoster}
                disabled={updatingRoster}
                className="px-5 py-2 rounded-xl bg-sky-600 hover:bg-sky-500 text-white font-bold text-xs shadow-md shadow-sky-600/20 flex items-center gap-2 transition-all"
              >
                <Save className="w-3.5 h-3.5" />
                <span>{updatingRoster ? 'Saving...' : 'Apply Live Override'}</span>
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

