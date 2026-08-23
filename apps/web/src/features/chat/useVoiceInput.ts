import { useCallback, useEffect, useRef, useState } from "react";
import {
  BrowserVoiceClient,
  type VoiceConnectionState,
  type VoiceDevice,
} from "./voiceClient";

interface VoiceInputOptions {
  sessionId: string | null;
  onError: (message: string) => void;
  onConnectionChange: (connected: boolean) => void;
}

export function useVoiceInput({
  sessionId,
  onError,
  onConnectionChange,
}: VoiceInputOptions) {
  const clientRef = useRef<BrowserVoiceClient | null>(null);
  const [state, setState] = useState<VoiceConnectionState>(() =>
    BrowserVoiceClient.supported() ? "disconnected" : "unsupported",
  );
  const [devices, setDevices] = useState<VoiceDevice[]>([]);
  const [deviceId, setDeviceId] = useState("");
  const [inputLevel, setInputLevel] = useState(0);

  useEffect(() => {
    const client = new BrowserVoiceClient({
      onStateChange: (next) => {
        setState(next);
        onConnectionChange(next === "connected");
      },
      onInputLevel: setInputLevel,
      onError,
    });
    clientRef.current = client;
    return () => {
      clientRef.current = null;
      void client.dispose(sessionId ?? undefined);
    };
  }, [onConnectionChange, onError, sessionId]);

  const refreshDevices = useCallback(async () => {
    const available = await clientRef.current?.listInputDevices();
    if (!available) return;
    setDevices(available);
    setDeviceId((current) =>
      available.some((device) => device.deviceId === current)
        ? current
        : (available[0]?.deviceId ?? ""),
    );
  }, []);

  const connect = useCallback(async () => {
    if (!sessionId || !clientRef.current) return;
    await clientRef.current
      .connect(sessionId, deviceId || undefined)
      .then(refreshDevices)
      .catch(() => undefined);
  }, [deviceId, refreshDevices, sessionId]);

  const disconnect = useCallback(async () => {
    await clientRef.current?.disconnect(sessionId ?? undefined);
  }, [sessionId]);

  const toggle = useCallback(async () => {
    if (
      state === "connected" ||
      state === "connecting" ||
      state === "requesting"
    ) {
      await disconnect();
    } else {
      await connect();
    }
  }, [connect, disconnect, state]);

  return {
    state,
    connected: state === "connected",
    devices,
    deviceId,
    inputLevel,
    setDeviceId,
    refreshDevices,
    toggle,
    disconnect,
  };
}
