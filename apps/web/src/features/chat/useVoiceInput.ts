import { useCallback, useEffect, useRef, useState } from "react";
import {
  BrowserVoiceClient,
  type VoiceActivationMode,
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
  const [activationMode, setActivationModeState] =
    useState<VoiceActivationMode>("push_to_talk");
  const [transmitting, setTransmitting] = useState(false);

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
    clientRef.current.setCaptureEnabled(activationMode === "open_mic");
    await clientRef.current
      .connect(sessionId, deviceId || undefined)
      .then(refreshDevices)
      .catch(() => undefined);
  }, [activationMode, deviceId, refreshDevices, sessionId]);

  const disconnect = useCallback(async () => {
    setTransmitting(false);
    clientRef.current?.setCaptureEnabled(false);
    await clientRef.current?.disconnect(sessionId ?? undefined);
  }, [sessionId]);

  const setActivationMode = useCallback((next: VoiceActivationMode) => {
    setActivationModeState(next);
    setTransmitting(false);
    clientRef.current?.setCaptureEnabled(next === "open_mic");
  }, []);

  const beginPushToTalk = useCallback(() => {
    if (state !== "connected" || activationMode !== "push_to_talk") return;
    clientRef.current?.setCaptureEnabled(true);
    setTransmitting(true);
  }, [activationMode, state]);

  const endPushToTalk = useCallback(() => {
    if (activationMode !== "push_to_talk") return;
    clientRef.current?.setCaptureEnabled(false);
    setTransmitting(false);
  }, [activationMode]);

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
    activationMode,
    transmitting,
    setDeviceId,
    setActivationMode,
    beginPushToTalk,
    endPushToTalk,
    refreshDevices,
    toggle,
    disconnect,
  };
}
