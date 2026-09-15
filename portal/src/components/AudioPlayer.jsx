import React, { useState, useEffect, useRef } from 'react';
import { Play, Pause, Volume2, RotateCcw, FastForward } from 'lucide-react';

export function AudioPlayer({ durationStr = '1m 24s', audioUrl = null }) {
  const [isPlaying, setIsPlaying] = useState(false);
  const [progress, setProgress] = useState(0);
  const [speed, setSpeed] = useState(1.0);
  const [currentTimeSec, setCurrentTimeSec] = useState(0);
  const audioRef = useRef(null);

  useEffect(() => {
    let interval = null;
    if (isPlaying && !audioUrl) {
      interval = setInterval(() => {
        setProgress((prev) => {
          if (prev >= 100) {
            setIsPlaying(false);
            return 0;
          }
          const next = prev + 1.2 * speed;
          setCurrentTimeSec(Math.floor((next / 100) * 84));
          return next;
        });
      }, 200);
    }
    return () => clearInterval(interval);
  }, [isPlaying, speed, audioUrl]);

  const togglePlay = () => {
    if (audioUrl && audioRef.current) {
      if (isPlaying) {
        audioRef.current.pause();
      } else {
        audioRef.current.play().catch(() => {});
      }
    }
    setIsPlaying(!isPlaying);
  };

  const handleSpeedChange = () => {
    const speeds = [1.0, 1.25, 1.5, 2.0];
    const nextIdx = (speeds.indexOf(speed) + 1) % speeds.length;
    const nextSpeed = speeds[nextIdx];
    setSpeed(nextSpeed);
    if (audioRef.current) {
      audioRef.current.playbackRate = nextSpeed;
    }
  };

  const resetPlay = () => {
    if (audioRef.current) {
      audioRef.current.currentTime = 0;
    }
    setProgress(0);
    setCurrentTimeSec(0);
    setIsPlaying(false);
  };

  const handleAudioTimeUpdate = () => {
    if (audioRef.current && audioRef.current.duration) {
      const cur = audioRef.current.currentTime;
      const dur = audioRef.current.duration;
      setProgress((cur / dur) * 100);
      setCurrentTimeSec(Math.floor(cur));
    }
  };

  const handleAudioEnded = () => {
    setIsPlaying(false);
    setProgress(0);
    setCurrentTimeSec(0);
  };

  // Dynamic waveform bars
  const bars = [
    25, 45, 70, 90, 60, 40, 85, 100, 75, 55, 30, 65, 80, 95, 40, 60, 85, 50, 70, 90,
    60, 45, 80, 100, 75, 40, 60, 85, 50, 65, 80, 40, 25, 50, 75, 90, 60, 35, 20
  ];

  return (
    <div className="bg-white rounded-2xl p-4 border border-slate-200 shadow-xs space-y-3">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <Volume2 className="w-4 h-4 text-sky-600" />
          <span className="text-xs font-bold text-slate-800">Patient Speech Audio Recording</span>
        </div>
        <span className="text-[10px] font-mono font-bold text-sky-700 bg-sky-50 px-2 py-0.5 rounded border border-sky-200">
          PCM 16kHz Verified
        </span>
      </div>

      {/* Animated Waveform Visualization */}
      <div className="h-12 bg-slate-50 rounded-xl p-2 flex items-center justify-between gap-1 border border-slate-200/80 overflow-hidden">
        {bars.map((height, idx) => {
          const barProgress = (idx / bars.length) * 100;
          const isPassed = progress >= barProgress;
          return (
            <div
              key={idx}
              style={{ height: `${height}%` }}
              className={`flex-1 rounded-full transition-all duration-150 ${
                isPassed
                  ? 'bg-gradient-to-t from-sky-600 to-blue-500'
                  : 'bg-slate-200 hover:bg-slate-300'
              } ${isPlaying && isPassed ? 'opacity-100 scale-y-105' : 'opacity-80'}`}
            />
          );
        })}
      </div>

      {/* Playback Controls & Scrubber */}
      <div className="flex items-center justify-between pt-1">
        <div className="flex items-center gap-2">
          <button
            onClick={togglePlay}
            className="w-8 h-8 rounded-xl bg-sky-600 hover:bg-sky-500 text-white flex items-center justify-center font-bold transition-all shadow-md shadow-sky-500/20"
          >
            {isPlaying ? <Pause className="w-4 h-4 fill-current" /> : <Play className="w-4 h-4 fill-current ml-0.5" />}
          </button>
          <button
            onClick={resetPlay}
            className="p-2 text-slate-500 hover:text-slate-800 hover:bg-slate-100 rounded-lg transition-colors"
            title="Restart Audio"
          >
            <RotateCcw className="w-3.5 h-3.5" />
          </button>
          <button
            onClick={handleSpeedChange}
            className="px-2 py-1 text-[11px] font-mono font-bold text-sky-700 bg-sky-50 hover:bg-sky-100 rounded-lg border border-sky-200 transition-colors"
            title="Playback Speed"
          >
            {speed}x
          </button>
        </div>

        <div className="text-[11px] font-mono text-slate-500 flex items-center gap-1.5">
          <span className="text-slate-900 font-bold">{currentTimeSec}s</span>
          <span>/</span>
          <span>{durationStr}</span>
        </div>
      </div>

      {/* Real HTML5 Audio Stream element if recording URL is supplied */}
      {audioUrl && (
        <audio
          ref={audioRef}
          src={audioUrl}
          onTimeUpdate={handleAudioTimeUpdate}
          onEnded={handleAudioEnded}
          preload="metadata"
          className="hidden"
        />
      )}
    </div>
  );
}
