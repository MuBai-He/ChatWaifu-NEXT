# Desktop settings development

The single registration entry is `desktopSettingsRegistry.tsx`. Do not add a new sidebar button or
conditional render branch to `DesktopSettingsPage.tsx`.

Create a section component accepting `DesktopSettingsContext`, register it once, and use the shared
primitives and `useSettingsOperation`. The complete contract, persistence boundaries, example, and
test gate are documented in `docs/architecture/settings-registry.md`.
