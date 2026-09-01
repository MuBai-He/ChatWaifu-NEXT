export const desktopOnboardingCompletionKey =
  "chatwaifu.desktop.onboarding.completed.v1";
const desktopOnboardingAttemptKey =
  "chatwaifu.desktop.onboarding.auto-opened.v1";

export function isDesktopOnboardingCompleted(): boolean {
  try {
    return (
      window.localStorage.getItem(desktopOnboardingCompletionKey) === "true"
    );
  } catch {
    return false;
  }
}

export function completeDesktopOnboarding(): void {
  try {
    window.localStorage.setItem(desktopOnboardingCompletionKey, "true");
  } catch {
    // Completion persistence is optional in locked-down or test WebViews.
  }
}

export function shouldAutoOpenDesktopOnboarding(desktopHost: boolean): boolean {
  if (!desktopHost || isDesktopOnboardingCompleted()) return false;
  try {
    if (window.sessionStorage.getItem(desktopOnboardingAttemptKey) === "true") {
      return false;
    }
    window.sessionStorage.setItem(desktopOnboardingAttemptKey, "true");
  } catch {
    // A WebView without session storage can still make one best-effort attempt.
  }
  return true;
}

export function releaseDesktopOnboardingAutoOpen(): void {
  try {
    window.sessionStorage.removeItem(desktopOnboardingAttemptKey);
  } catch {
    // Nothing to release when session storage is unavailable.
  }
}
