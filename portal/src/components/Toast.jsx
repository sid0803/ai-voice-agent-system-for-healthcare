import React, { useState, useEffect } from 'react';
import { CheckCircle2, AlertTriangle, XCircle, Info, X } from 'lucide-react';

export function ToastContainer() {
  const [toasts, setToasts] = useState([]);

  useEffect(() => {
    const handleToast = (e) => {
      const { type = 'info', title, message, duration = 4000 } = e.detail || {};
      const id = Date.now() + Math.random().toString(36).substring(2, 6);

      setToasts((prev) => [...prev, { id, type, title, message, duration }]);

      if (duration > 0) {
        setTimeout(() => {
          setToasts((prev) => prev.filter((t) => t.id !== id));
        }, duration);
      }
    };

    window.addEventListener('toast', handleToast);
    return () => window.removeEventListener('toast', handleToast);
  }, []);

  const removeToast = (id) => {
    setToasts((prev) => prev.filter((t) => t.id !== id));
  };

  if (toasts.length === 0) return null;

  return (
    <div className="fixed bottom-5 right-5 z-[9999] flex flex-col gap-2.5 max-w-sm w-full pointer-events-none">
      {toasts.map((toast) => {
        const isSuccess = toast.type === 'success';
        const isError = toast.type === 'error';
        const isWarning = toast.type === 'warning';

        return (
          <div
            key={toast.id}
            className={`pointer-events-auto p-4 rounded-2xl border shadow-xl backdrop-blur-md flex items-start gap-3 transition-all animate-in slide-in-from-bottom-5 duration-300 ${
              isSuccess
                ? 'bg-emerald-50/95 border-emerald-200 text-emerald-950 shadow-emerald-900/5'
                : isError
                ? 'bg-rose-50/95 border-rose-200 text-rose-950 shadow-rose-900/5'
                : isWarning
                ? 'bg-amber-50/95 border-amber-200 text-amber-950 shadow-amber-900/5'
                : 'bg-white/95 border-slate-200 text-slate-900 shadow-slate-900/5'
            }`}
          >
            <div className="shrink-0 mt-0.5">
              {isSuccess && <CheckCircle2 className="w-5 h-5 text-emerald-600" />}
              {isError && <XCircle className="w-5 h-5 text-rose-600" />}
              {isWarning && <AlertTriangle className="w-5 h-5 text-amber-600" />}
              {!isSuccess && !isError && !isWarning && <Info className="w-5 h-5 text-sky-600" />}
            </div>

            <div className="flex-1 min-w-0">
              {toast.title && <h5 className="font-bold text-xs leading-tight mb-0.5">{toast.title}</h5>}
              <p className="text-xs opacity-90 leading-relaxed break-words">{toast.message}</p>
            </div>

            <button
              onClick={() => removeToast(toast.id)}
              className="shrink-0 p-1 rounded-lg opacity-60 hover:opacity-100 hover:bg-slate-100 transition-colors"
            >
              <X className="w-3.5 h-3.5" />
            </button>
          </div>
        );
      })}
    </div>
  );
}

// Global toast helper
export const toast = {
  success: (message, title = 'Success') => {
    window.dispatchEvent(new CustomEvent('toast', { detail: { type: 'success', title, message } }));
  },
  error: (message, title = 'Error') => {
    window.dispatchEvent(new CustomEvent('toast', { detail: { type: 'error', title, message } }));
  },
  warning: (message, title = 'Attention') => {
    window.dispatchEvent(new CustomEvent('toast', { detail: { type: 'warning', title, message } }));
  },
  info: (message, title = 'Information') => {
    window.dispatchEvent(new CustomEvent('toast', { detail: { type: 'info', title, message } }));
  },
};
