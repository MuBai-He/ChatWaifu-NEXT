import { createContext, useContext } from "react";

export const ConnectionControlsContext = createContext<{
  mode: "local" | "remote" | null;
  address?: string;
  open: () => void;
} | null>(null);
export const ScopeControlsContext = createContext<{
  label: string;
  open: () => void;
} | null>(null);
export const useConnectionControls = () =>
  useContext(ConnectionControlsContext);
export const useScopeControls = () => useContext(ScopeControlsContext);
