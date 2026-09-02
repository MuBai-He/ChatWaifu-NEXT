export const NATIVE_INTERACTION_GUARD_NOTIFICATION =
  "chatwaifu:native-interaction-guard";

export type NativeInteractionGuardSource =
  "skill-confirmation" | "avatar-gesture" | "push-to-talk";

export type NativeInteractionGuardNotification = {
  active: boolean;
  sources: NativeInteractionGuardSource[];
};

const activeGuards = new Map<symbol, NativeInteractionGuardSource>();

export function acquireNativeInteractionGuard(
  source: NativeInteractionGuardSource,
): () => void {
  const token = Symbol(source);
  activeGuards.set(token, source);
  notifyGuardState();
  let released = false;
  return () => {
    if (released) return;
    released = true;
    activeGuards.delete(token);
    notifyGuardState();
  };
}

export function isNativeInteractionGuardActive(): boolean {
  return activeGuards.size > 0;
}

function notifyGuardState(): void {
  window.dispatchEvent(
    new CustomEvent<NativeInteractionGuardNotification>(
      NATIVE_INTERACTION_GUARD_NOTIFICATION,
      {
        detail: {
          active: isNativeInteractionGuardActive(),
          sources: [...new Set(activeGuards.values())],
        },
      },
    ),
  );
}
