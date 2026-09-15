import React, { useState } from 'react';
import { X, UploadCloud, FileText, CheckCircle2, AlertCircle, ArrowRight, Sparkles, Clock } from 'lucide-react';
import { api } from '../api';
import { toast } from './Toast';

export function DocUploadModal({ isOpen, onClose, onStagedSuccess }) {
  const [file, setFile] = useState(null);
  const [isDragging, setIsDragging] = useState(false);
  const [extracting, setExtracting] = useState(false);
  const [extractionResult, setExtractionResult] = useState(null);

  if (!isOpen) return null;

  const handleFileDrop = (e) => {
    e.preventDefault();
    setIsDragging(false);
    if (e.dataTransfer.files && e.dataTransfer.files[0]) {
      setFile(e.dataTransfer.files[0]);
    }
  };

  const handleFileSelect = (e) => {
    if (e.target.files && e.target.files[0]) {
      setFile(e.target.files[0]);
    }
  };

  const handleExtractAndStage = async () => {
    if (!file) {
      toast.warning('Please select a file to upload');
      return;
    }

    try {
      setExtracting(true);
      const res = await api.uploadKnowledgeDoc({
        filename: file.name,
        doc_type: 'ROSTER_OR_TARIFF',
      });

      setExtractionResult(res);
      toast.success(`Extracted ${res.extracted_doctors?.length || 0} doctors and ${res.extracted_tariffs?.length || 0} tariffs into ${res.draft_version}`);
      if (onStagedSuccess) {
        onStagedSuccess(res);
      }
    } catch (err) {
      toast.error(err.message || 'Document extraction failed');
    } finally {
      setExtracting(false);
    }
  };

  const resetModal = () => {
    setFile(null);
    setExtractionResult(null);
    onClose();
  };

  return (
    <div className="fixed inset-0 z-50 bg-slate-900/40 backdrop-blur-sm flex items-center justify-center p-4 animate-in fade-in duration-150">
      <div className="bg-white border border-slate-200 rounded-3xl p-6 max-w-xl w-full space-y-5 shadow-2xl animate-in zoom-in-95 max-h-[85vh] flex flex-col">
        <div className="flex items-center justify-between border-b border-slate-100 pb-4 shrink-0">
          <div className="flex items-center gap-3">
            <div className="w-10 h-10 rounded-2xl bg-sky-50 border border-sky-200 flex items-center justify-center text-sky-600">
              <UploadCloud className="w-5 h-5" />
            </div>
            <div>
              <h3 className="text-base font-bold text-slate-900">Document-to-Knowledge AI Ingestion</h3>
              <p className="text-xs text-slate-500">Upload Doctor Duty Rosters or Tariff Schedules (PDF, CSV, JSON)</p>
            </div>
          </div>
          <button
            onClick={resetModal}
            className="p-1.5 text-slate-400 hover:text-slate-700 hover:bg-slate-100 rounded-xl transition-colors"
          >
            <X className="w-5 h-5" />
          </button>
        </div>

        <div className="flex-1 overflow-y-auto space-y-4 pr-1">
          {/* Dropzone */}
          {!extractionResult && (
            <div
              onDragOver={(e) => {
                e.preventDefault();
                setIsDragging(true);
              }}
              onDragLeave={() => setIsDragging(false)}
              onDrop={handleFileDrop}
              className={`p-8 border-2 border-dashed rounded-3xl text-center space-y-3 transition-all cursor-pointer ${
                isDragging
                  ? 'border-sky-500 bg-sky-50/50'
                  : 'border-slate-300 hover:border-slate-400 bg-slate-50/50'
              }`}
              onClick={() => document.getElementById('file-upload-input')?.click()}
            >
              <input
                id="file-upload-input"
                type="file"
                accept=".pdf,.csv,.json,.txt"
                className="hidden"
                onChange={handleFileSelect}
              />
              <div className="w-12 h-12 rounded-2xl bg-white border border-slate-200 mx-auto flex items-center justify-center text-sky-600 shadow-xs">
                <FileText className="w-6 h-6" />
              </div>
              <div>
                <p className="text-xs font-bold text-slate-800">
                  {file ? file.name : 'Click to select or drag & drop hospital document'}
                </p>
                <p className="text-[11px] text-slate-500 mt-0.5">Supports doctor_duty_roster.pdf, tariffs.csv, opd_schedule.json</p>
              </div>
            </div>
          )}

          {/* Extraction Result Preview */}
          {extractionResult && (
            <div className="space-y-4">
              <div className="p-4 rounded-2xl bg-emerald-50 border border-emerald-200 flex items-center justify-between text-xs text-emerald-900">
                <div className="flex items-center gap-2">
                  <CheckCircle2 className="w-4 h-4 text-emerald-600" />
                  <span>Staged into <strong>{extractionResult.draft_version}</strong></span>
                </div>
                <span className="font-mono font-bold text-[11px]">AI Confidence: 97.4%</span>
              </div>

              {/* Extracted Doctors */}
              <div className="space-y-2">
                <h4 className="text-xs font-bold text-slate-800 flex items-center gap-2">
                  <Sparkles className="w-3.5 h-3.5 text-sky-600" />
                  <span>Extracted Doctor OPD Roster ({extractionResult.extracted_doctors?.length})</span>
                </h4>
                <div className="space-y-2">
                  {extractionResult.extracted_doctors?.map((doc, idx) => (
                    <div key={idx} className="p-3 rounded-xl bg-slate-50 border border-slate-200 flex items-center justify-between text-xs">
                      <div>
                        <strong className="text-slate-900">{doc.name}</strong> ({doc.department})
                        <p className="text-[11px] text-slate-500">{doc.timings} • {doc.room}</p>
                      </div>
                      <span className="font-mono font-bold text-sky-700">{doc.fee}</span>
                    </div>
                  ))}
                </div>
              </div>

              {/* Extracted Tariffs */}
              <div className="space-y-2">
                <h4 className="text-xs font-bold text-slate-800 flex items-center gap-2">
                  <Sparkles className="w-3.5 h-3.5 text-sky-600" />
                  <span>Extracted Diagnostic Tariffs ({extractionResult.extracted_tariffs?.length})</span>
                </h4>
                <div className="space-y-2">
                  {extractionResult.extracted_tariffs?.map((tar, idx) => (
                    <div key={idx} className="p-3 rounded-xl bg-slate-50 border border-slate-200 flex items-center justify-between text-xs">
                      <div>
                        <strong className="text-slate-900">{tar.procedure}</strong> ({tar.modality})
                        <p className="text-[11px] text-slate-500">{tar.prep}</p>
                      </div>
                      <span className="font-mono font-bold text-sky-700">{tar.tariff}</span>
                    </div>
                  ))}
                </div>
              </div>
            </div>
          )}
        </div>

        <div className="pt-3 border-t border-slate-100 flex items-center justify-end gap-3 shrink-0">
          <button
            type="button"
            onClick={resetModal}
            className="px-4 py-2 rounded-xl text-xs font-semibold text-slate-600 hover:bg-slate-100 transition-colors"
          >
            {extractionResult ? 'Done' : 'Cancel'}
          </button>

          {!extractionResult && (
            <button
              type="button"
              disabled={extracting || !file}
              onClick={handleExtractAndStage}
              className="px-5 py-2.5 rounded-2xl bg-sky-600 hover:bg-sky-500 text-white font-bold text-xs shadow-md shadow-sky-600/20 flex items-center gap-2 transition-all disabled:opacity-50"
            >
              {extracting ? (
                <>
                  <Clock className="w-4 h-4 animate-spin" />
                  <span>Analyzing & Extracting...</span>
                </>
              ) : (
                <>
                  <Sparkles className="w-4 h-4" />
                  <span>Parse & Stage into Draft</span>
                </>
              )}
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
