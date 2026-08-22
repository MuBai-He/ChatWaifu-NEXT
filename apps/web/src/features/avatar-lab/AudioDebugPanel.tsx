import {
  AnalyserLipSyncSource,
  SyntheticLipSyncSource,
  type LipSyncSource,
} from "@chatwaifu/avatar-sdk";
import { useRef, useState } from "react";

interface AudioDebugPanelProps {
  onStart: (source: LipSyncSource) => void;
  onStop: () => void;
}

export function AudioDebugPanel({ onStart, onStop }: AudioDebugPanelProps) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [sourceName, setSourceName] = useState("silent");
  const [error, setError] = useState<string | null>(null);

  const startSynthetic = (mode: "sine" | "random") => {
    setError(null);
    setSourceName(`${mode} envelope`);
    onStart(new SyntheticLipSyncSource(mode));
  };

  const startWav = async (file: File) => {
    try {
      setError(null);
      const context = new AudioContext();
      const buffer = await context.decodeAudioData(await file.arrayBuffer());
      const source = context.createBufferSource();
      const analyser = context.createAnalyser();
      analyser.fftSize = 512;
      source.buffer = buffer;
      source.connect(analyser);
      analyser.connect(context.destination);
      const lipSync = new AnalyserLipSyncSource(analyser, {
        nodesToDisconnect: [source, analyser],
        onDispose: () => {
          try {
            source.stop();
          } catch {
            // A naturally ended source is already stopped.
          }
          void context.close();
        },
      });
      source.onended = () => {
        setSourceName("silent");
        onStop();
      };
      source.start();
      setSourceName(file.name);
      onStart(lipSync);
    } catch (audioError: unknown) {
      setError(
        audioError instanceof Error
          ? audioError.message
          : "Unable to decode WAV file.",
      );
    }
  };

  const startMicrophone = async () => {
    try {
      setError(null);
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const context = new AudioContext();
      const source = context.createMediaStreamSource(stream);
      const analyser = context.createAnalyser();
      analyser.fftSize = 512;
      source.connect(analyser);
      setSourceName("microphone analyser");
      onStart(
        new AnalyserLipSyncSource(analyser, {
          nodesToDisconnect: [source, analyser],
          onDispose: () => {
            for (const track of stream.getTracks()) track.stop();
            void context.close();
          },
        }),
      );
    } catch (microphoneError: unknown) {
      setError(
        microphoneError instanceof Error
          ? microphoneError.message
          : "Microphone permission was denied.",
      );
    }
  };

  return (
    <section className="lab-panel audio-panel">
      <div className="panel-heading">
        <div>
          <p className="panel-kicker">Audio clock</p>
          <h2>Lip-sync source</h2>
        </div>
        <span className="source-chip">{sourceName}</span>
      </div>
      <div className="audio-actions">
        <button type="button" onClick={() => startSynthetic("sine")}>
          Sine envelope
        </button>
        <button type="button" onClick={() => startSynthetic("random")}>
          Random envelope
        </button>
        <button type="button" onClick={() => inputRef.current?.click()}>
          Local WAV
        </button>
        <button type="button" onClick={() => void startMicrophone()}>
          Microphone analyser
        </button>
        <button
          type="button"
          className="stop-button"
          onClick={() => {
            setSourceName("silent");
            onStop();
          }}
        >
          Stop / neutral
        </button>
      </div>
      <input
        ref={inputRef}
        type="file"
        accept="audio/wav,audio/wave,audio/x-wav"
        hidden
        onChange={(event) => {
          const file = event.target.files?.[0];
          if (file) void startWav(file);
          event.target.value = "";
        }}
      />
      <p className="panel-note">
        WAV uses the AudioContext clock. Microphone mode analyses locally and is
        not connected to speakers, avoiding feedback.
      </p>
      {error ? <p className="inline-error">{error}</p> : null}
    </section>
  );
}
