export type SettingsIconName =
  | "brand"
  | "pet"
  | "companion"
  | "voice"
  | "models"
  | "channels"
  | "data"
  | "memory"
  | "skills";

export function SettingsIcon({ name }: { name: SettingsIconName }) {
  return (
    <svg
      aria-hidden="true"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.7"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      {iconPaths[name]}
    </svg>
  );
}

const iconPaths: Record<SettingsIconName, React.ReactNode> = {
  brand: (
    <>
      <path d="M15.4 3.3a8.6 8.6 0 1 0 5.3 11.4 7.2 7.2 0 0 1-9.3-9.3 8.6 8.6 0 0 0 4-2.1Z" />
      <path d="m18.2 4.2.45 1.2 1.15.45-1.15.45-.45 1.2-.45-1.2-1.15-.45 1.15-.45.45-1.2Z" />
    </>
  ),
  pet: (
    <>
      <rect x="3" y="4" width="18" height="13" rx="2.5" />
      <path d="M8 21h8M12 17v4" />
      <path d="M9.2 10.2c.9-1.7 4.7-1.7 5.6 0M9.8 8.1h.01M14.2 8.1h.01" />
    </>
  ),
  companion: (
    <>
      <path d="M15.5 3.2a8.4 8.4 0 1 0 5.2 11.2 7 7 0 0 1-9.1-9.1 8.4 8.4 0 0 0 3.9-2.1Z" />
      <path d="M17.2 4.2h3M18.7 2.7v3M18 17.5c-1.3 1.1-3.2 1.8-5.2 1.8" />
    </>
  ),
  voice: (
    <>
      <path d="M4 14h3l4 3V7L7 10H4v4Z" />
      <path d="M15 9.2a4 4 0 0 1 0 5.6M17.8 6.5a7.8 7.8 0 0 1 0 11" />
    </>
  ),
  models: (
    <>
      <circle cx="12" cy="12" r="3" />
      <circle cx="5" cy="6" r="2" />
      <circle cx="19" cy="6" r="2" />
      <circle cx="5" cy="18" r="2" />
      <circle cx="19" cy="18" r="2" />
      <path d="m7 7.4 2.6 2.5M17 7.4l-2.6 2.5M7 16.6l2.6-2.5M17 16.6l-2.6-2.5" />
    </>
  ),
  channels: (
    <>
      <path d="M4.2 5.5h10.4a3.2 3.2 0 0 1 3.2 3.2v2.6a3.2 3.2 0 0 1-3.2 3.2H9.2l-3.8 2.8.8-2.8h-2a3.2 3.2 0 0 1-3.2-3.2V8.7a3.2 3.2 0 0 1 3.2-3.2Z" />
      <path d="M11.3 14.5v.8a3.2 3.2 0 0 0 3.2 3.2h2l3.8 2.8-.8-2.8h.3a3.2 3.2 0 0 0 3.2-3.2v-2.6a3.2 3.2 0 0 0-3.2-3.2h-2" />
      <path d="M6.2 9.8h.01M10.5 9.8h.01M14.8 9.8h.01" />
    </>
  ),
  data: (
    <>
      <ellipse cx="12" cy="5" rx="7.5" ry="3" />
      <path d="M4.5 5v6c0 1.7 3.4 3 7.5 3s7.5-1.3 7.5-3V5" />
      <path d="M4.5 11v6c0 1.7 3.4 3 7.5 3s7.5-1.3 7.5-3v-6" />
    </>
  ),
  memory: (
    <>
      <path d="M6 4.5A2.5 2.5 0 0 1 8.5 2H19v17H8.5A2.5 2.5 0 0 0 6 21.5v-17Z" />
      <path d="M6 19a2 2 0 0 1 2-2h11M10 6h5M10 10h5" />
    </>
  ),
  skills: (
    <>
      <path d="M8.5 3.5h-3a2 2 0 0 0-2 2v3a2.5 2.5 0 1 1 0 5v3a2 2 0 0 0 2 2h3a2.5 2.5 0 1 0 5 0h3a2 2 0 0 0 2-2v-3a2.5 2.5 0 1 0 0-5v-3a2 2 0 0 0-2-2h-3a2.5 2.5 0 1 1-5 0Z" />
    </>
  ),
};
