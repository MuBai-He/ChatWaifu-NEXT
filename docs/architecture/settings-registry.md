# Desktop settings registry

## Responsibility

`desktopSettingsRegistry.tsx` is the only navigation and composition entry for the desktop control
center. A section registers its stable ID, label, description, icon, React component, optional
browser/desktop surfaces, optional visibility predicate, and optional availability predicate.

The registry does not own persistence or provider behavior. UI and OS preferences continue through
`useDesktopPreferences` and Tauri. Conversation, model, memory, companion, and TTS settings continue
through typed Runtime APIs. Provider SDK objects and secrets never enter the registry.

## Adding a section

1. Create a section component under `apps/web/src/features/desktop-settings/` that accepts
   `{ context: DesktopSettingsContext }`.
2. Add one definition to `desktopSettingsRegistry.tsx`.
3. Use `surfaces`, `visible`, or `availability` instead of adding platform/capability branches to
   `DesktopSettingsPage.tsx`.
4. Use `SettingsGroup`, `SettingsSectionIntro`, `SettingsToggle`, `SettingsSecretField`, and
   `SettingsStatus` for shared presentation.
5. Use `useSettingsOperation` for save, test, restart, and other mutually exclusive async actions.
6. Add a registry or component test and update `docs/implementation-status.yaml`.

Example:

```tsx
{
  id: "example",
  label: "示例",
  description: "示例设置",
  icon: "data",
  component: ExampleSettingsSection,
  surfaces: ["desktop"],
  availability: ({ runtime }) => ({
    enabled: runtime.connection === "connected",
    reason: runtime.connection === "connected" ? undefined : "Runtime 离线",
  }),
}
```

## Shared behavior

- Registry construction rejects duplicate section IDs and an empty registry.
- The shell owns selection, navigation, Runtime status, scrolling, and top-level errors.
- Section components receive only the typed state slices in `DesktopSettingsContext`; they do not
  create a second chat/media session.
- `SettingsSecretField` is write-only. It renders configured state but never receives a saved key.
- `useSettingsOperation` permits one active action, blocks duplicate clicks, and normalizes pending,
  success, and error notices. Runtime/client timeouts still own network termination.
- Changing the active TTS provider still uses the existing session command, which cancels the active
  generation before switching. The registry does not bypass realtime cancellation rules.

## Test gate

Run:

```text
uv run python tools/run_pnpm.py --filter @chatwaifu/web lint
uv run python tools/run_pnpm.py --filter @chatwaifu/web typecheck
uv run python tools/run_pnpm.py --filter @chatwaifu/web test
uv run python tools/run_pnpm.py --filter @chatwaifu/web build
```
