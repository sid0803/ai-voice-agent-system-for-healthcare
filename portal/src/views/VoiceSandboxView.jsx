import React, { useState, useEffect, useRef } from 'react';
import {
  Mic,
  MicOff,
  Send,
  Sparkles,
  Zap,
  Activity,
  Shield,
  Volume2,
  AlertTriangle,
  Stethoscope,
  Clock,
  Play,
  RotateCcw,
  CheckCircle2,
  Bot,
  User,
  HelpCircle,
} from 'lucide-react';
import { api } from '../api';
import { toast } from '../components/Toast';

export function VoiceSandboxView() {
  const [query, setQuery] = useState('');
  const [selectedPersona, setSelectedPersona] = useState('STANDARD');
  const [isListening, setIsListening] = useState(false);
  const [isSimulating, setIsSimulating] = useState(false);
  const [chatHistory, setChatHistory] = useState([
    {
      id: 1,
      role: 'ASSISTANT',
      text: 'Namaste! Main ASHA hoon, Apollo Metro Hospital ki AI voice assistant. Aap mujhse doctor appointments, OPD timings, diagnostic test tariffs, ya emergency assistance ke bare mein pooch sakte hain.',
      toolCall: null,
      telemetry: { latency: 138, sentiment: 'REASSURED', confidence: 0.99 },
    },
  ]);
  const [selectedToolTrace, setSelectedToolTrace] = useState(null);
  const chatEndRef = useRef(null);

  const personas = [
    {
      id: 'STANDARD',
      label: 'Standard Patient',
      desc: 'Normal tone inquiry for routine consultations and fees.',
      preset: 'Dr. Amit Sharma ki OPD timing aur consultation fee kitni hai?',
    },
    {
      id: 'CHEST_PAIN',
      label: '🚨 ESI-1 Cardiac Emergency',
      desc: 'Severe chest pain, cold sweats, and left arm numbness.',
      preset: 'Mujhe bahut tez chhati mein dard ho raha hai aur bayen hath mein dard ja raha hai, emergency hai!',
    },
    {
      id: 'MRI_TARIFF',
      label: '🔬 Diagnostic Tariff Inquiry',
      desc: 'Fast-track Brain MRI with contrast price and prep.',
      preset: 'Brain MRI with contrast ka kitna kharcha aayega aur kya khali pet aana hai?',
    },
    {
      id: 'PEDIATRICS',
      label: '👶 Child Specialist Booking',
      desc: 'Booking consultation with Dr. Sunita Rao.',
      preset: 'Mere bache ko fever hai, Dr. Sunita Rao pediatrician se appointment chahiye.',
    },
    {
      id: 'INSURANCE',
      label: '💳 Cashless Mediclaim TPA',
      desc: 'Star Health / HDFC Ergo cashless admission policy.',
      preset: 'Kya Apollo Metro mein Star Health cashless mediclaim policy accepted hai?',
    },
  ];

  useEffect(() => {
    chatEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [chatHistory]);

  const handleSend = async (overrideQuery = null) => {
    const textToSend = overrideQuery || query;
    if (!textToSend.trim()) return;

    const userTurn = {
      id: Date.now(),
      role: 'USER',
      text: textToSend,
    };

    setChatHistory((prev) => [...prev, userTurn]);
    setQuery('');
    setIsSimulating(true);

    try {
      const res = await api.simulateVoiceTurn({
        query: textToSend,
        persona: selectedPersona,
        language: 'hi-IN',
      });

      const assistantTurn = {
        id: Date.now() + 1,
        role: 'ASSISTANT',
        text: res.response_text,
        toolCall: res.tool_call,
        telemetry: {
          latency: res.telemetry?.speech_to_speech_latency_ms || 142,
          sentiment: res.telemetry?.sentiment_label || 'REASSURED',
          confidence: res.telemetry?.grounding_confidence || 0.98,
          isEmergency: res.is_emergency,
        },
      };

      setChatHistory((prev) => [...prev, assistantTurn]);
      setSelectedToolTrace(res.tool_call);

      // Browser Speech Synthesis for speech auditory playback
      if ('speechSynthesis' in window) {
        window.speechSynthesis.cancel();
        const utterance = new SpeechSynthesisUtterance(res.response_text);
        utterance.rate = 1.05;
        utterance.pitch = 1.1; // Warm, friendly tone
        window.speechSynthesis.speak(utterance);
      }
    } catch (err) {
      toast.error(err.message || 'Simulation error');
    } finally {
      setIsSimulating(false);
    }
  };

  const handleToggleMic = () => {
    const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
    if (!SpeechRecognition) {
      toast.warning('Web Speech API is not supported in this browser. Please use text input.');
      return;
    }

    if (isListening) {
      setIsListening(false);
    } else {
      try {
        const recognition = new SpeechRecognition();
        recognition.lang = 'hi-IN';
        recognition.continuous = false;
        recognition.interimResults = false;

        recognition.onstart = () => {
          setIsListening(true);
          toast.info('Listening to your microphone (Hindi / English)...');
        };

        recognition.onresult = (event) => {
          const transcript = event.results[0][0].transcript;
          setQuery(transcript);
          setIsListening(false);
          handleSend(transcript);
        };

        recognition.onerror = (e) => {
          console.warn('Speech recognition error:', e);
          setIsListening(false);
          toast.error('Could not capture audio. Please type query.');
        };

        recognition.onend = () => {
          setIsListening(false);
        };

        recognition.start();
      } catch (e) {
        setIsListening(false);
      }
    }
  };

  const handlePersonaSelect = (persona) => {
    setSelectedPersona(persona.id);
    setQuery(persona.preset);
  };

  return (
    <div className="p-6 space-y-6">
      {/* Studio Header */}
      <div className="p-5 rounded-3xl bg-gradient-to-r from-sky-50 via-white to-white border border-sky-200 flex flex-col sm:flex-row items-start sm:items-center justify-between gap-4 shadow-xs">
        <div className="flex items-center gap-3.5">
          <div className="w-12 h-12 rounded-2xl bg-sky-100 border border-sky-200 flex items-center justify-center text-sky-700 shadow-xs">
            <Mic className="w-6 h-6" />
          </div>
          <div>
            <div className="flex items-center gap-2">
              <h3 className="text-sm font-bold text-slate-900">Clinician Voice AI Testing Studio (ASHA Simulator)</h3>
              <span className="px-2.5 py-0.5 rounded-full text-[10px] uppercase font-mono font-bold bg-sky-50 text-sky-700 border border-sky-200">
                Bedrock Nova Sonic v1.0
              </span>
            </div>
            <p className="text-xs text-slate-500">
              Interactive test bench to simulate real-time patient conversations, tool execution traces, and grounding in Hindi & English.
            </p>
          </div>
        </div>

        <div className="flex items-center gap-3">
          <button
            onClick={() => {
              setChatHistory([]);
              setSelectedToolTrace(null);
              toast.info('Simulator conversation cleared');
            }}
            className="px-3.5 py-1.5 rounded-xl bg-white border border-slate-200 text-xs text-slate-700 font-semibold flex items-center gap-1.5 hover:bg-slate-50 transition-colors shadow-xs"
          >
            <RotateCcw className="w-3.5 h-3.5" />
            <span>Reset Studio</span>
          </button>
        </div>
      </div>

      {/* Preset Personas Selector */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-5 gap-3">
        {personas.map((p) => {
          const isSelected = selectedPersona === p.id;
          return (
            <div
              key={p.id}
              onClick={() => handlePersonaSelect(p)}
              className={`p-3.5 rounded-2xl border cursor-pointer transition-all shadow-xs ${
                isSelected
                  ? 'bg-sky-50/90 border-sky-300 text-sky-950 font-bold shadow-sky-100'
                  : 'bg-white border-slate-200 hover:border-slate-300 text-slate-700'
              }`}
            >
              <h5 className="text-xs font-bold truncate">{p.label}</h5>
              <p className="text-[11px] text-slate-500 mt-1 line-clamp-2 leading-relaxed font-normal">{p.desc}</p>
            </div>
          );
        })}
      </div>

      {/* Main Studio Dual Pane */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* Left: Chat & Voice Interface (2 Cols) */}
        <div className="lg:col-span-2 bg-white rounded-3xl border border-slate-200 shadow-xs flex flex-col h-[520px] overflow-hidden">
          {/* Messages Stream */}
          <div className="flex-1 overflow-y-auto p-5 space-y-4">
            {chatHistory.map((msg) => {
              const isAssistant = msg.role === 'ASSISTANT';
              const isEmergency = msg.telemetry?.isEmergency;

              return (
                <div
                  key={msg.id}
                  className={`flex gap-3 text-xs ${isAssistant ? 'flex-row' : 'flex-row-reverse'}`}
                >
                  <div
                    className={`w-8 h-8 rounded-xl flex items-center justify-center shrink-0 font-bold ${
                      isAssistant
                        ? isEmergency
                          ? 'bg-rose-100 text-rose-700 border border-rose-200'
                          : 'bg-sky-100 text-sky-700 border border-sky-200'
                        : 'bg-slate-200 text-slate-800'
                    }`}
                  >
                    {isAssistant ? <Bot className="w-4 h-4" /> : <User className="w-4 h-4" />}
                  </div>

                  <div
                    onClick={() => msg.toolCall && setSelectedToolTrace(msg.toolCall)}
                    className={`max-w-[80%] rounded-2xl p-4 shadow-xs transition-all ${
                      isAssistant
                        ? isEmergency
                          ? 'bg-rose-50 border border-rose-200 text-rose-950 cursor-pointer hover:border-rose-300'
                          : 'bg-slate-50 border border-slate-200 text-slate-900 cursor-pointer hover:border-sky-300'
                        : 'bg-sky-600 text-white shadow-sky-600/10 font-medium'
                    }`}
                  >
                    <div className="flex items-center justify-between gap-4 mb-1.5 text-[10px] opacity-75 font-semibold">
                      <span className="uppercase tracking-wider">
                        {isAssistant ? (isEmergency ? 'ASHA (Emergency Mode)' : 'ASHA Voice AI') : 'You (Tester)'}
                      </span>
                      {isAssistant && msg.telemetry && (
                        <span className="font-mono text-sky-800 font-bold">
                          {msg.telemetry.latency}ms • {msg.telemetry.sentiment}
                        </span>
                      )}
                    </div>
                    <p className="leading-relaxed text-[12px] whitespace-pre-wrap">{msg.text}</p>

                    {msg.toolCall && (
                      <div className="mt-2.5 pt-2 border-t border-slate-200/80 flex items-center justify-between text-[10px] text-sky-700 font-mono font-bold">
                        <span>🛠️ Tool: {msg.toolCall.name}</span>
                        <span className="underline">Inspect Trace ➔</span>
                      </div>
                    )}
                  </div>
                </div>
              );
            })}
            {isSimulating && (
              <div className="flex gap-3 text-xs">
                <div className="w-8 h-8 rounded-xl bg-sky-100 text-sky-700 flex items-center justify-center">
                  <Bot className="w-4 h-4 animate-spin" />
                </div>
                <div className="bg-slate-50 border border-slate-200 rounded-2xl p-4 text-xs text-slate-500 flex items-center gap-2">
                  <span className="w-2 h-2 rounded-full bg-sky-500 animate-pulse" />
                  <span>Synthesizing Bedrock Nova Sonic speech response...</span>
                </div>
              </div>
            )}
            <div ref={chatEndRef} />
          </div>

          {/* Input Controls Bar */}
          <div className="p-4 border-t border-slate-100 bg-slate-50/50 flex items-center gap-3">
            <button
              onClick={handleToggleMic}
              className={`p-3 rounded-2xl transition-all shadow-xs ${
                isListening
                  ? 'bg-rose-600 text-white animate-pulse shadow-rose-600/30'
                  : 'bg-white border border-slate-200 text-sky-600 hover:bg-slate-100'
              }`}
              title={isListening ? 'Stop Listening' : 'Speak into Microphone'}
            >
              {isListening ? <MicOff className="w-5 h-5" /> : <Mic className="w-5 h-5" />}
            </button>

            <input
              type="text"
              placeholder="Ask ASHA in Hindi or English (e.g. Dr Amit Sharma timing, MRI brain fee...)"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && handleSend()}
              className="flex-1 bg-white border border-slate-200 rounded-2xl px-4 py-2.5 text-xs text-slate-900 placeholder-slate-400 focus:outline-none focus:border-sky-500 font-medium"
            />

            <button
              onClick={() => handleSend()}
              disabled={isSimulating || !query.trim()}
              className="px-5 py-2.5 rounded-2xl bg-sky-600 hover:bg-sky-500 text-white font-bold text-xs shadow-md shadow-sky-600/20 flex items-center gap-1.5 transition-all disabled:opacity-50"
            >
              <span>Send</span>
              <Send className="w-3.5 h-3.5" />
            </button>
          </div>
        </div>

        {/* Right: Live Telemetry & Tool Inspector (1 Col) */}
        <div className="bg-white rounded-3xl border border-slate-200 p-5 space-y-4 shadow-xs flex flex-col justify-between">
          <div className="space-y-4">
            <div className="flex items-center justify-between border-b border-slate-100 pb-3">
              <h4 className="text-xs font-bold text-slate-900 flex items-center gap-2">
                <Zap className="w-4 h-4 text-sky-600" />
                <span>Tool Execution & Grounding Trace</span>
              </h4>
              <span className="text-[10px] font-mono font-bold text-emerald-700 bg-emerald-50 px-2 py-0.5 rounded border border-emerald-200">
                100% Deterministic
              </span>
            </div>

            {selectedToolTrace ? (
              <div className="space-y-3">
                <div className="p-3.5 rounded-2xl bg-slate-50 border border-slate-200 space-y-1.5 text-xs">
                  <div className="flex justify-between">
                    <span className="text-slate-500 font-bold uppercase text-[10px]">Invoked Tool:</span>
                    <span className="font-mono font-bold text-sky-700">{selectedToolTrace.name}</span>
                  </div>
                  <div className="flex justify-between">
                    <span className="text-slate-500 font-bold uppercase text-[10px]">Lookup Latency:</span>
                    <span className="font-mono text-emerald-700 font-bold">{selectedToolTrace.latency_ms} ms</span>
                  </div>
                </div>

                <div className="space-y-1.5">
                  <span className="text-[10px] uppercase font-bold text-slate-500">Query Arguments:</span>
                  <pre className="bg-slate-900 text-sky-300 p-3 rounded-xl text-[11px] font-mono overflow-x-auto">
                    {JSON.stringify(selectedToolTrace.arguments, null, 2)}
                  </pre>
                </div>

                <div className="space-y-1.5">
                  <span className="text-[10px] uppercase font-bold text-slate-500">Grounded Output Context:</span>
                  <div className="p-3 bg-slate-50 rounded-xl border border-slate-200 text-xs text-slate-800 leading-relaxed font-mono text-[11px]">
                    {selectedToolTrace.result_summary}
                  </div>
                </div>
              </div>
            ) : (
              <div className="p-8 text-center text-xs text-slate-400 space-y-2">
                <Activity className="w-6 h-6 mx-auto text-slate-300" />
                <p>Send a query or select an assistant message to inspect real-time tool execution.</p>
              </div>
            )}
          </div>

          <div className="p-3.5 rounded-2xl bg-sky-50 border border-sky-100 text-xs text-slate-600 space-y-1">
            <div className="flex items-center gap-1.5 font-bold text-sky-900">
              <Shield className="w-3.5 h-3.5 text-sky-600" />
              <span>Safety & Clinical Grounding</span>
            </div>
            <p className="text-[11px] text-slate-500 leading-relaxed">
              Every response is grounded deterministically against DynamoDB hospital catalog without LLM hallucinations.
            </p>
          </div>
        </div>
      </div>
    </div>
  );
}
