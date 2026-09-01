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
- The Data section owns the explicit full Worker Pack integrity action. Desktop startup performs only
  bounded metadata and entrypoint checks; the action runs exact-tree, payload-hash, reparse, and PE
  verification outside Runtime's media event loop.
- Changing the active TTS provider still uses the existing session command, which cancels the active
  generation before switching. The registry does not bypass realtime cancellation rules.

## First-run guide

The desktop control center owns a platform-neutral first-run guide for the installed macOS and Windows
products. The pet asks the native Host to show the control center once per process session until the
guide is completed. Completion stores only a versioned boolean in WebView local storage; API keys,
provider credentials, model configuration, and microphone state continue through their existing
Runtime or OS boundaries. The guide explains the base-app versus optional `.cwpack` boundary and
links to the registered model, voice, and companion sections instead of creating duplicate settings
forms. “以后再说” defers until a later launch; “完成引导” suppresses future automatic display, while
the sidebar button always reopens it.

## Test gate

Run:

```text
uv run python tools/run_pnpm.py --filter @chatwaifu/web lint
uv run python tools/run_pnpm.py --filter @chatwaifu/web typecheck
uv run python tools/run_pnpm.py --filter @chatwaifu/web test
uv run python tools/run_pnpm.py --filter @chatwaifu/web build
```
