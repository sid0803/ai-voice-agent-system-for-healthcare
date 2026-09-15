import React, { useState, useEffect } from 'react';
import { Sidebar } from './components/Sidebar';
import { Header } from './components/Header';
import { ToastContainer, toast } from './components/Toast';
import { CommandPalette } from './components/CommandPalette';
import { OutboundDispatchModal } from './components/OutboundDispatchModal';
import { StaffManageModal } from './components/StaffManageModal';
import { NewAppointmentModal } from './components/NewAppointmentModal';
import { DiffViewerModal } from './components/DiffViewerModal';
import { DocUploadModal } from './components/DocUploadModal';
import { DashboardView } from './views/DashboardView';
import { VoiceSandboxView } from './views/VoiceSandboxView';
import { AnalyticsView } from './views/AnalyticsView';
import { CallsView } from './views/CallsView';
import { AppointmentsView } from './views/AppointmentsView';
import { TriageView } from './views/TriageView';
import { KnowledgeCMSView } from './views/KnowledgeCMSView';
import { AuditLogsView } from './views/AuditLogsView';
import { LoginView } from './views/LoginView';
import { api } from './api';
import { soundEngine } from './utils/audioAlert';
import { AlertOctagon, ArrowRight, Volume2, VolumeX } from 'lucide-react';


export function App() {
  const [user, setUser] = useState(null);
  const [authChecking, setAuthChecking] = useState(true);
  const [currentTab, setCurrentTab] = useState('dashboard');
  const [sseConnected, setSseConnected] = useState(false);
  const [isRefreshing, setIsRefreshing] = useState(false);

  // Modals state
  const [isCommandPaletteOpen, setIsCommandPaletteOpen] = useState(false);
  const [isOutboundModalOpen, setIsOutboundModalOpen] = useState(false);
  const [outboundInitialPhone, setOutboundInitialPhone] = useState('');
  const [isStaffModalOpen, setIsStaffModalOpen] = useState(false);
  const [isNewBookingOpen, setIsNewBookingOpen] = useState(false);
  const [isDiffModalOpen, setIsDiffModalOpen] = useState(false);
  const [isDocUploadOpen, setIsDocUploadOpen] = useState(false);

  // Global Emergency Alert State
  const [criticalTriageAlert, setCriticalTriageAlert] = useState(null);
  const [sirenSilenced, setSirenSilenced] = useState(false);

  // Keyboard shortcut listener (Cmd+K / Ctrl+K)
  useEffect(() => {
    const handleKeyDown = (e) => {
      if ((e.metaKey || e.ctrlKey) && e.key === 'k') {
        e.preventDefault();
        setIsCommandPaletteOpen((prev) => !prev);
      }
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, []);

  useEffect(() => {
    checkAuth();

    const handleUnauthorized = () => {
      setUser(null);
      toast.warning('Your session has expired. Please sign in again.');
    };
    window.addEventListener('auth:unauthorized', handleUnauthorized);
    return () => window.removeEventListener('auth:unauthorized', handleUnauthorized);
  }, []);

  // Server-Sent Events (SSE) Stream Listener
  useEffect(() => {
    if (!user) return;

    let eventSource = null;
    try {
      eventSource = new EventSource('/api/v1/events/stream');

      eventSource.onopen = () => {
        setSseConnected(true);
      };

      eventSource.onmessage = (event) => {
        // SSE message received
      };

      eventSource.onerror = () => {
        setSseConnected(false);
      };
    } catch (e) {
      console.warn('SSE connection failed:', e);
    }

    return () => {
      if (eventSource) {
        eventSource.close();
      }
    };
  }, [user]);

  // Global Code Red Emergency Alert Monitor & Siren
  useEffect(() => {
    if (!user) return;

    const checkEmergencyTriage = async () => {
      try {
        const data = await api.getTriageEvents('status=OPEN');
        const criticalOpen = (data.events || []).find(
          (e) =>
            (e.priority || '').toUpperCase() === 'CRITICAL' &&
            (e.status || '').toUpperCase() === 'OPEN'
        );
        if (criticalOpen) {
          setCriticalTriageAlert((prev) => {
            if (!prev || prev.event_id !== criticalOpen.event_id) {
              if (!sirenSilenced) {
                soundEngine.playEmergencyChime();
              }
            }
            return criticalOpen;
          });
        } else {
          setCriticalTriageAlert(null);
          setSirenSilenced(false);
        }
      } catch (err) {
        // Silently catch background poll error
      }
    };

    checkEmergencyTriage();
    const interval = setInterval(checkEmergencyTriage, 15000);
    return () => clearInterval(interval);
  }, [user, sirenSilenced]);

  const checkAuth = async () => {
    try {
      setAuthChecking(true);
      const res = await api.getMe();
      if (res.user) {
        setUser(res.user);
      }
    } catch (err) {
      setUser(null);
    } finally {
      setAuthChecking(false);
    }
  };

  const handleLogout = async () => {
    try {
      await api.logout();
      toast.info('Signed out successfully');
    } catch (e) {
      // Ignore
    } finally {
      setUser(null);
    }
  };

  const handleManualRefresh = () => {
    setIsRefreshing(true);
    setTimeout(() => setIsRefreshing(false), 600);
  };

  const handleTriggerOutbound = (phone = '') => {
    setOutboundInitialPhone(phone);
    setIsOutboundModalOpen(true);
  };

  if (authChecking) {
    return (
      <div className="min-h-screen bg-slate-50 flex items-center justify-center text-slate-500 text-xs font-mono">
        <div className="flex items-center gap-3 bg-white p-5 rounded-2xl border border-slate-200 shadow-sm">
          <div className="w-4 h-4 border-2 border-sky-600 border-t-transparent rounded-full animate-spin" />
          <span className="font-medium text-slate-700">Verifying secure hospital session...</span>
        </div>
      </div>
    );
  }

  if (!user) {
    return (
      <>
        <ToastContainer />
        <LoginView onLoginSuccess={(u) => setUser(u)} />
      </>
    );
  }

  const titles = {
    dashboard: { title: 'Operational Command Center', subtitle: 'Live voice AI analytics, patient volume & triage telemetry' },
    sandbox: { title: 'Clinician Voice AI Testing Studio', subtitle: 'Interactive simulator to test Bedrock Nova Sonic conversational grounding and tool traces' },
    analytics: { title: 'Clinical Analytics & Revenue Intelligence', subtitle: 'OPD revenue tracking from voice bookings, slot fill heatmaps & conversion funnels' },
    calls: { title: 'Patient Call Transcripts & Speech Logs', subtitle: 'Turn-by-turn conversational grounding audited under HIPAA/NABH guidelines' },
    appointments: { title: 'OPD Appointments & Doctor Roster', subtitle: 'Central doctor availability matrix & walk-in booking management' },
    triage: { title: 'Emergency Severity Index (ESI) Triage Feed', subtitle: 'Live clinical escalations, response SLA timers & duty physician assignments' },
    knowledge: { title: 'Hospital Knowledge Base CMS', subtitle: 'Doctor directory, OPD tariffs, and self-learning distiller review pipeline' },
    audit: { title: 'Application Put-Only Audit Trail', subtitle: 'Immutable record of clinical mutations, cancellations, and access events' },
  };

  const headerInfo = titles[currentTab] || { title: 'Hospital Management', subtitle: '' };

  return (
    <div className="flex min-h-screen bg-slate-50 text-slate-900 selection:bg-sky-500 selection:text-white">
      {/* Toast Notification Container */}
      <ToastContainer />

      {/* Sidebar */}
      <Sidebar
        currentTab={currentTab}
        setCurrentTab={setCurrentTab}
        user={user}
        onLogout={handleLogout}
        onOpenStaffModal={() => setIsStaffModalOpen(true)}
        onOpenOutboundModal={() => handleTriggerOutbound('')}
      />

      {/* Main Content Viewport */}
      <div className="flex-1 flex flex-col min-w-0">
        {/* Global Emergency Escalation Top Banner (Code Red) */}
        {criticalTriageAlert && (
          <div className="bg-gradient-to-r from-rose-700 via-rose-600 to-red-600 text-white px-6 py-2.5 flex items-center justify-between shadow-lg z-30 animate-in slide-in-from-top duration-200">
            <div className="flex items-center gap-3 min-w-0">
              <span className="relative flex h-3 w-3 shrink-0">
                <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-white opacity-75"></span>
                <span className="relative inline-flex rounded-full h-3 w-3 bg-white"></span>
              </span>
              <AlertOctagon className="w-5 h-5 fill-white text-rose-600 shrink-0" />
              <div className="min-w-0 truncate">
                <span className="font-extrabold text-xs uppercase tracking-wider bg-white/20 px-2 py-0.5 rounded mr-2">
                  CODE RED TRIAGE
                </span>
                <span className="text-xs font-bold">
                  Critical Escalation ({criticalTriageAlert.category || 'Emergency Symptoms'} - ESI Level 1/2)
                </span>
                <span className="text-xs text-rose-100 hidden md:inline ml-2">
                  • Patient {criticalTriageAlert.caller_phone || 'Caller'} requires immediate physician assignment!
                </span>
              </div>
            </div>

            <div className="flex items-center gap-3 shrink-0">
              <button
                onClick={() => {
                  setSirenSilenced(true);
                  toast.info('Emergency audio siren silenced');
                }}
                className="text-xs text-rose-100 hover:text-white flex items-center gap-1 font-semibold underline px-2 py-1"
                title="Mute Audio Chime"
              >
                {sirenSilenced ? <VolumeX className="w-3.5 h-3.5" /> : <Volume2 className="w-3.5 h-3.5" />}
                <span className="hidden sm:inline">{sirenSilenced ? 'Muted' : 'Silence'}</span>
              </button>
              <button
                onClick={() => {
                  setCurrentTab('triage');
                }}
                className="px-3.5 py-1 bg-white text-rose-700 hover:bg-rose-50 font-bold text-xs rounded-xl shadow-xs flex items-center gap-1.5 transition-all"
              >
                <span>Respond in ER Console</span>
                <ArrowRight className="w-3.5 h-3.5" />
              </button>
            </div>
          </div>
        )}

        <Header
          title={headerInfo.title}
          subtitle={headerInfo.subtitle}
          sseConnected={sseConnected}
          onRefresh={handleManualRefresh}
          isRefreshing={isRefreshing}
          tenantName={user.tenant_name || 'Apollo Metro Hospital'}
          onOpenCommandPalette={() => setIsCommandPaletteOpen(true)}
        />

        <main className="flex-1 overflow-y-auto">
          {currentTab === 'dashboard' && (
            <DashboardView
              onNavigateTab={(t) => setCurrentTab(t)}
              onTriggerOutbound={() => handleTriggerOutbound('')}
            />
          )}
          {currentTab === 'sandbox' && <VoiceSandboxView />}
          {currentTab === 'analytics' && <AnalyticsView />}
          {currentTab === 'calls' && (
            <CallsView
              user={user}
              onBookAppointment={() => setIsNewBookingOpen(true)}
              onTriggerOutbound={(ph) => handleTriggerOutbound(ph)}
            />
          )}
          {currentTab === 'appointments' && <AppointmentsView user={user} />}
          {currentTab === 'triage' && (
            <TriageView
              user={user}
              onTriggerOutbound={(ph) => handleTriggerOutbound(ph)}
            />
          )}
          {currentTab === 'knowledge' && <KnowledgeCMSView user={user} />}
          {currentTab === 'audit' && <AuditLogsView user={user} />}
        </main>
      </div>

      {/* Command Palette (Cmd+K) */}
      <CommandPalette
        isOpen={isCommandPaletteOpen}
        onClose={() => setIsCommandPaletteOpen(false)}
        onNavigate={(tab) => setCurrentTab(tab)}
        onOpenBooking={() => setIsNewBookingOpen(true)}
        onOpenOutbound={() => handleTriggerOutbound('')}
        onOpenStaff={() => setIsStaffModalOpen(true)}
        onOpenDiff={() => setIsDiffModalOpen(true)}
        onOpenDocUpload={() => setIsDocUploadOpen(true)}
      />

      {/* Outbound Dispatch Modal */}
      <OutboundDispatchModal
        isOpen={isOutboundModalOpen}
        onClose={() => setIsOutboundModalOpen(false)}
        initialPhone={outboundInitialPhone}
      />

      {/* Staff Onboarding Modal */}
      <StaffManageModal
        isOpen={isStaffModalOpen}
        onClose={() => setIsStaffModalOpen(false)}
        currentHospital={user.tenant_id}
      />

      {/* Walk-in Booking Modal */}
      <NewAppointmentModal
        isOpen={isNewBookingOpen}
        onClose={() => setIsNewBookingOpen(false)}
      />

      {/* Knowledge Diff Modal */}
      <DiffViewerModal
        isOpen={isDiffModalOpen}
        onClose={() => setIsDiffModalOpen(false)}
        publishedVersion="v1.0"
        draftVersion="v1.1-DRAFT"
        onPublish={() => toast.success('Knowledge Base v1.1 published & hot-reloaded!')}
      />

      {/* Document Ingestion Uploader Modal */}
      <DocUploadModal
        isOpen={isDocUploadOpen}
        onClose={() => setIsDocUploadOpen(false)}
        onStagedSuccess={() => {
          setCurrentTab('knowledge');
          setIsDiffModalOpen(true);
        }}
      />
    </div>
  );
}

