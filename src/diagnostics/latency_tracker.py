"""High-Precision Latency Telemetry Tracker (Pillar 0).
Tracks high-resolution monotonic timestamps (time.perf_counter()) across the full speech-to-speech lifecycle.
"""

import time
import logging
from dataclasses import dataclass, field
from typing import Optional, Dict, Any, List

logger = logging.getLogger(__name__)

@dataclass
class TurnTimings:
    turn_index: int = 1
    session_id: str = ""
    
    # Monotonic timestamps via time.perf_counter()
    t0_audio_arrival: Optional[float] = None       # T0 = Caller speech start detected
    t1_speech_end: Optional[float] = None          # T1 = VAD detects end of speech (silence onset)
    t2_turn_committed: Optional[float] = None      # T2 = User turn committed (silence gate reached)
    t3_bedrock_sent: Optional[float] = None        # T3 = Local Bedrock request dispatch initiated (contentEnd)
    t4_first_model_output: Optional[float] = None  # T4 = First model output event received from Bedrock stream
    t5_first_audio_gen: Optional[float] = None     # T5 = First audio output chunk received from Bedrock stream
    t6_first_audio_sent: Optional[float] = None    # T6 = First audio packet dispatched to Exotel WebSocket
    t7_estimated_playout: Optional[float] = None   # T7 = Estimated earliest handset playout (T6 + 20ms PBX buffer)
    
    # Tool-specific timestamps
    t_tool_start: Optional[float] = None           # T_tool_start = toolUse event received from Bedrock
    t_tool_result: Optional[float] = None          # T_tool_result = Local tool execution completed & sent to Bedrock
    t_first_filler: Optional[float] = None         # T_first_filler = Acoustic filler audio dispatched to Exotel
    t_first_authoritative: Optional[float] = None  # T_first_authoritative_audio = Authoritative audio packet sent to Exotel
    
    # Metadata
    tool_name: Optional[str] = None
    is_tool_turn: bool = False
    turn_reported: bool = False

    def mark_t0(self):
        """Mark when caller speech begins in current turn."""
        if self.t0_audio_arrival is None:
            self.t0_audio_arrival = time.perf_counter()

    def mark_t1(self):
        """Mark when VAD detects silence onset after speech."""
        self.t1_speech_end = time.perf_counter()

    def mark_t2(self):
        """Mark when user turn is committed."""
        self.t2_turn_committed = time.perf_counter()
        if self.t1_speech_end is None:
            self.t1_speech_end = self.t2_turn_committed

    def mark_t3(self):
        """Mark when local Bedrock request dispatch (contentEnd) is initiated."""
        self.t3_bedrock_sent = time.perf_counter()

    def mark_t4(self, event_type: str = "model_output"):
        """Mark when first model event (text, toolUse, audio) is received."""
        if self.t4_first_model_output is None:
            self.t4_first_model_output = time.perf_counter()

    def mark_t5(self):
        """Mark when first audio chunk is received from Bedrock stream."""
        if self.t5_first_audio_gen is None:
            self.t5_first_audio_gen = time.perf_counter()
            if self.t4_first_model_output is None:
                self.t4_first_model_output = self.t5_first_audio_gen

    def mark_t6(self):
        """Mark when first audio packet is formatted and dispatched over WebSocket to Exotel."""
        if self.t6_first_audio_sent is None:
            self.t6_first_audio_sent = time.perf_counter()
            # T7 is an estimate of downstream PBX transit + jitter buffer playout (+20ms)
            self.t7_estimated_playout = self.t6_first_audio_sent + 0.020
            if self.is_tool_turn and self.t_first_authoritative is None:
                self.t_first_authoritative = self.t6_first_audio_sent
            self.report_telemetry()

    def mark_tool_start(self, tool_name: str):
        """Mark when toolUse is received from Bedrock."""
        self.is_tool_turn = True
        self.tool_name = tool_name
        self.t_tool_start = time.perf_counter()
        if self.t4_first_model_output is None:
            self.t4_first_model_output = self.t_tool_start

    def mark_tool_result(self):
        """Mark when local tool execution completes and toolResult is sent to Bedrock."""
        self.t_tool_result = time.perf_counter()

    def mark_first_filler(self):
        """Mark when acoustic filler audio is dispatched to Exotel."""
        self.t_first_filler = time.perf_counter()

    def calculate_metrics(self) -> Dict[str, Any]:
        """Calculate exact turn latencies in milliseconds."""
        t0 = self.t0_audio_arrival or (self.t1_speech_end - 1.0 if self.t1_speech_end else time.perf_counter() - 1.0)
        t1 = self.t1_speech_end or t0
        t2 = self.t2_turn_committed or t1
        t3 = self.t3_bedrock_sent or t2
        t4 = self.t4_first_model_output or t3
        t5 = self.t5_first_audio_gen or t4
        t6 = self.t6_first_audio_sent or t5
        t7 = self.t7_estimated_playout or (t6 + 0.020)

        # Core metric calculations
        endpointing_ms = max(0.0, (t2 - t1) * 1000.0)
        speech_duration_ms = max(0.0, (t1 - t0) * 1000.0)
        bedrock_roundtrip_first_event_ms = max(0.0, (t4 - t3) * 1000.0)
        tts_audio_start_ms = max(0.0, (t5 - t4) * 1000.0)
        outbound_send_ms = max(0.0, (t6 - t5) * 1000.0)
        
        # Primary responsiveness metrics
        server_s2s_first_audio_ms = max(0.0, (t6 - t2) * 1000.0)
        estimated_ttfat_ms = max(0.0, (t7 - t2) * 1000.0)
        e2e_turn_duration_ms = max(0.0, (t7 - t0) * 1000.0)

        tool_metrics = {}
        if self.is_tool_turn:
            t_ts = self.t_tool_start or t4
            t_tr = self.t_tool_result or t_ts
            t_ff = self.t_first_filler
            t_fa = self.t_first_authoritative or t6

            tool_exec_ms = max(0.0, (t_tr - t_ts) * 1000.0)
            filler_dispatch_delay_ms = max(0.0, (t_ff - t_ts) * 1000.0) if t_ff else None
            authoritative_delay_ms = max(0.0, (t_fa - t_tr) * 1000.0)

            tool_metrics = {
                "tool_name": self.tool_name,
                "tool_exec_duration_ms": round(tool_exec_ms, 2),
                "filler_dispatch_delay_ms": round(filler_dispatch_delay_ms, 2) if filler_dispatch_delay_ms is not None else None,
                "authoritative_audio_delay_ms": round(authoritative_delay_ms, 2),
            }

        return {
            "turn_index": self.turn_index,
            "session_id": self.session_id[:8] if self.session_id else "unknown",
            "is_tool_turn": self.is_tool_turn,
            "speech_duration_ms": round(speech_duration_ms, 2),
            "endpointing_ms": round(endpointing_ms, 2),
            "bedrock_roundtrip_first_event_ms": round(bedrock_roundtrip_first_event_ms, 2),
            "tts_audio_start_ms": round(tts_audio_start_ms, 2),
            "outbound_processing_ms": round(outbound_send_ms, 2),
            "server_s2s_first_audio_ms": round(server_s2s_first_audio_ms, 2),
            "estimated_ttfat_ms": round(estimated_ttfat_ms, 2),
            "e2e_turn_duration_ms": round(e2e_turn_duration_ms, 2),
            **tool_metrics,
        }

    def report_telemetry(self):
        """Format and log telemetry output to console and logger."""
        if self.turn_reported:
            return
        self.turn_reported = True

        m = self.calculate_metrics()
        banner = "=" * 80
        lines = [
            f"\n{banner}",
            f"⏱️  TURN LATENCY TELEMETRY [Turn #{self.turn_index}] (Session: {self.session_id[:8]})",
            f"{'-'*80}",
            f"  • T0 (Caller Speech Start):               {self.t0_audio_arrival:.4f}s" if self.t0_audio_arrival else "  • T0: N/A",
            f"  • T1 (Speech End / Silence Onset):        {self.t1_speech_end:.4f}s" if self.t1_speech_end else "  • T1: N/A",
            f"  • T2 (User Turn Committed):               {self.t2_turn_committed:.4f}s" if self.t2_turn_committed else "  • T2: N/A",
            f"  • T3 (Local Bedrock Dispatch Initiated):  {self.t3_bedrock_sent:.4f}s" if self.t3_bedrock_sent else "  • T3: N/A",
            f"  • T4 (First Model Event Received):        {self.t4_first_model_output:.4f}s" if self.t4_first_model_output else "  • T4: N/A",
            f"  • T5 (First Audio Chunk Generated):       {self.t5_first_audio_gen:.4f}s" if self.t5_first_audio_gen else "  • T5: N/A",
            f"  • T6 (First Audio Dispatched to Exotel):  {self.t6_first_audio_sent:.4f}s" if self.t6_first_audio_sent else "  • T6: N/A",
            f"  • T7 (Estimated Earliest Playout at Ear): {self.t7_estimated_playout:.4f}s [Estimate: T6 + 20ms]" if self.t7_estimated_playout else "  • T7: N/A",
            f"{'-'*80}",
            f"📊 MEASURED LATENCY BREAKDOWN:",
            f"  1. Endpointing (T2 - T1):                    {m['endpointing_ms']:>6.1f} ms  (VAD silence gap)",
            f"  2. Bedrock Round-Trip to First Event (T4-T3): {m['bedrock_roundtrip_first_event_ms']:>6.1f} ms  (Local dispatch + network + prefill)",
            f"  3. TTS / Audio Generation (T5 - T4):         {m['tts_audio_start_ms']:>6.1f} ms  (Model audio token synthesis)",
            f"  4. Outbound & Transcode (T6 - T5):           {m['outbound_processing_ms']:>6.1f} ms  (AudioPolisher + WS send)",
            f"  ------------------------------------------------------------------------------",
            f"  ⚡ Server-Side First Audio (T6 - T2):         {m['server_s2s_first_audio_ms']:>6.1f} ms  [PRIMARY MEASURED S2S METRIC]",
            f"  🎯 Estimated TTFAT at Handset (T7 - T2):     {m['estimated_ttfat_ms']:>6.1f} ms  [ESTIMATED EARLIEST PLAYOUT]",
            f"  ⌛ End-to-End Turn Duration (T7 - T0):       {m['e2e_turn_duration_ms']:>6.1f} ms  (Includes {m['speech_duration_ms']:.0f}ms caller speech)",
        ]

        if self.is_tool_turn:
            lines.extend([
                f"{'-'*80}",
                f"🔧 TOOL EXECUTION BREAKDOWN ({self.tool_name}):",
                f"  • T_tool_start:                   {self.t_tool_start:.4f}s" if self.t_tool_start else "  • T_tool_start: N/A",
                f"  • T_tool_result:                  {self.t_tool_result:.4f}s" if self.t_tool_result else "  • T_tool_result: N/A",
                f"  • Tool Execution Duration:          {m.get('tool_exec_duration_ms', 0):>6.1f} ms  (Local Python lookup)",
                f"  • T_first_filler:                 {self.t_first_filler:.4f}s" if self.t_first_filler else "  • T_first_filler: N/A (None)",
                f"  • T_first_authoritative_audio:    {self.t_first_authoritative:.4f}s" if self.t_first_authoritative else "  • T_first_authoritative: N/A",
                f"  • Authoritative Synthesis Delay:  {m.get('authoritative_audio_delay_ms', 0):>6.1f} ms",
            ])

        lines.append(f"{banner}\n")
        logger.info("\n".join(lines))


class LatencyTelemetryManager:
    """Manages active turn latency tracking and historical aggregation per session."""
    def __init__(self):
        self._active_turns: Dict[str, TurnTimings] = {}
        self._history: Dict[str, List[Dict[str, Any]]] = {}

    def get_or_create_turn(self, session_id: str, turn_index: int = 1) -> TurnTimings:
        if session_id not in self._active_turns:
            self._active_turns[session_id] = TurnTimings(turn_index=turn_index, session_id=session_id)
        return self._active_turns[session_id]

    def reset_turn(self, session_id: str, next_turn_index: int) -> TurnTimings:
        if session_id in self._active_turns:
            m = self._active_turns[session_id].calculate_metrics()
            if session_id not in self._history:
                self._history[session_id] = []
            self._history[session_id].append(m)
        
        new_turn = TurnTimings(turn_index=next_turn_index, session_id=session_id)
        self._active_turns[session_id] = new_turn
        return new_turn

    def get_session_summary(self, session_id: str) -> Dict[str, Any]:
        """Compute statistical aggregates (mean, p50, p90) for the session."""
        records = self._history.get(session_id, [])
        if not records:
            return {"turn_count": 0}
        
        s2s_vals = [r["server_s2s_first_audio_ms"] for r in records if "server_s2s_first_audio_ms" in r]
        s2s_vals.sort()
        count = len(s2s_vals)
        
        return {
            "turn_count": count,
            "mean_s2s_ms": round(sum(s2s_vals) / count, 1) if count else 0,
            "p50_s2s_ms": s2s_vals[int(count * 0.50)] if count else 0,
            "p90_s2s_ms": s2s_vals[int(count * 0.90)] if count else 0,
            "min_s2s_ms": s2s_vals[0] if count else 0,
            "max_s2s_ms": s2s_vals[-1] if count else 0,
        }

    def remove_session(self, session_id: str):
        self._active_turns.pop(session_id, None)
        self._history.pop(session_id, None)

latency_telemetry = LatencyTelemetryManager()
