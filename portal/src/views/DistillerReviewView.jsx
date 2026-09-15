import React, { useState, useEffect } from 'react';
import { BookOpenCheck, Check, X, Sparkles, AlertCircle, Eye, GitCompare } from 'lucide-react';
import { api } from '../api';

export function DistillerReviewView({ user }) {
  const [pendingFacts, setPendingFacts] = useState([]);
  const [loading, setLoading] = useState(true);
  const [processingId, setProcessingId] = useState(null);

  const canReview = user?.permissions?.includes('facts.approve') || user?.role === 'hospital_admin';

  useEffect(() => {
    loadPendingFacts();
  }, []);

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
    } catch (err) {
      alert('Approval failed: ' + err.message);
    } finally {
      setProcessingId(null);
    }
  };

  const handleReject = async (factId) => {
    try {
      setProcessingId(factId);
      await api.rejectFact(factId);
      setPendingFacts((prev) => prev.filter((f) => f.id !== factId));
    } catch (err) {
      alert('Rejection failed: ' + err.message);
    } finally {
      setProcessingId(null);
    }
  };

  return (
    <div className="p-6 space-y-6">
      {/* Knowledge Distiller Pipeline Banner */}
      <div className="p-5 rounded-2xl bg-gradient-to-r from-teal-950/40 via-slate-900 to-slate-900 border border-teal-500/20 flex flex-col md:flex-row items-start md:items-center justify-between gap-4">
        <div className="flex items-center gap-3">
          <div className="w-10 h-10 rounded-xl bg-teal-500/20 border border-teal-500/30 flex items-center justify-center text-teal-400">
            <Sparkles className="w-5 h-5" />
          </div>
          <div>
            <h3 className="text-sm font-semibold text-slate-100">Self-Learning Distiller Fact Queue</h3>
            <p className="text-xs text-slate-300">
              New doctor schedules or tariffs extracted by Bedrock post-call analyzer are held in quarantine until human approval.
            </p>
          </div>
        </div>

        <div className="flex items-center gap-2 bg-slate-950/80 px-3 py-1.5 rounded-xl border border-slate-800 text-xs text-slate-400">
          <GitCompare className="w-4 h-4 text-teal-400" />
          <span>Workflow: <strong>Review ➔ Draft ➔ Publish</strong></span>
        </div>
      </div>

      {/* Facts Card Grid */}
      <div className="space-y-4">
        <div className="flex items-center justify-between">
          <h4 className="text-xs font-semibold uppercase text-slate-400 tracking-wider">
            Pending Learned Facts ({pendingFacts.length})
          </h4>
          <span className="text-xs text-slate-500">Live Bedrock Extractor</span>
        </div>

        {loading ? (
          <div className="text-center py-12 text-slate-500 text-xs">
            Loading candidate facts queue...
          </div>
        ) : pendingFacts.length === 0 ? (
          <div className="p-12 text-center rounded-2xl bg-slate-900 border border-slate-800 text-slate-400 space-y-2">
            <BookOpenCheck className="w-8 h-8 mx-auto text-teal-400" />
            <p className="text-sm font-semibold text-slate-200">No Pending Facts in Queue</p>
            <p className="text-xs text-slate-500">The knowledge base is fully up to date with caller insights.</p>
          </div>
        ) : (
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            {pendingFacts.map((fact) => (
              <div
                key={fact.id}
                className="p-5 rounded-2xl bg-slate-900 border border-slate-800 space-y-4 hover:border-slate-700 transition-all shadow-sm"
              >
                <div className="flex items-start justify-between">
                  <div>
                    <span className="text-[10px] uppercase font-bold px-2 py-0.5 rounded bg-teal-500/10 text-teal-400 border border-teal-500/20">
                      {fact.category || 'DOCTOR_SCHEDULE'}
                    </span>
                    <h4 className="text-sm font-semibold text-slate-100 mt-2">
                      {fact.fact_summary || fact.doctor_name || 'Discovered Schedule Update'}
                    </h4>
                  </div>
                  <span className="text-xs font-mono text-slate-400 bg-slate-950 px-2 py-1 rounded border border-slate-800">
                    Confidence: {(fact.confidence ? fact.confidence * 100 : 92).toFixed(0)}%
                  </span>
                </div>

                <div className="bg-slate-950/80 p-3 rounded-xl border border-slate-800/80 text-xs text-slate-300 space-y-1">
                  <p className="text-slate-400">Extracted Detail:</p>
                  <p className="font-mono text-teal-300">{JSON.stringify(fact.data || fact, null, 2)}</p>
                </div>

                <div className="flex items-center justify-between text-xs text-slate-400 pt-2 border-t border-slate-800">
                  <span className="font-mono text-[11px]">Source Call: {fact.source_call_id ? fact.source_call_id.substring(0, 10) : 'LIVE-TURN'}</span>

                  {canReview && (
                    <div className="flex items-center gap-2">
                      <button
                        onClick={() => handleReject(fact.id)}
                        disabled={processingId === fact.id}
                        className="p-2 rounded-xl text-rose-400 hover:bg-rose-500/10 border border-rose-500/20 transition-colors"
                        title="Reject Fact"
                      >
                        <X className="w-4 h-4" />
                      </button>
                      <button
                        onClick={() => handleApprove(fact.id)}
                        disabled={processingId === fact.id}
                        className="px-3 py-1.5 rounded-xl bg-teal-600 hover:bg-teal-500 text-white font-medium text-xs flex items-center gap-1.5 shadow-sm transition-colors"
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
    </div>
  );
}
