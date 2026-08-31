# Product icon system

ChatWaifu NEXT uses one icon policy across the Web conversation surface, the
desktop pet, the desktop control center, and native desktop packaging.

## Functional icons

- React surfaces render functional icons through
  `apps/web/src/components/ProductIcon.tsx`.
- `ProductIcon` uses the open-source `lucide-react` package and keeps every
  decorative SVG outside the accessibility tree. The owning button or link
  supplies the accessible name.
- Feature components must not add Unicode symbols, emoji, copied SVG paths, or
  a second icon library. Add a semantic name to `ProductIcon` instead.
- Settings registry entries continue to use `SettingsIconName`; the adapter in
  `SettingsIcon.tsx` maps those stable product names onto `ProductIcon`.

## Brand mark

The ChatWaifu mark is the AI-drawn crescent-and-ribbon image already used by
the Web documentation. It is deliberately separate from the functional icon
library.

- Canonical source: `docs-site/public/brand/chatwaifu-mark.png`
- Compact source: `docs-site/public/brand/chatwaifu-mark-small.png`
- Web copy: `apps/web/public/brand/chatwaifu-mark-small.png`
- Generated native assets: `apps/desktop/src-tauri/icons/`

Regenerate native platform icons after changing the canonical image:

```bash
pnpm --filter @chatwaifu/desktop exec tauri icon \
  "$PWD/docs-site/public/brand/chatwaifu-mark.png" \
  --output "$PWD/apps/desktop/src-tauri/icons"
```

The tray image is a 44-by-44 transparent copy of the compact mark. macOS treats
its alpha channel as a template image; Windows and Linux display the same
source in color. Do not add text to either application or tray artwork because
it becomes unreadable at system-icon sizes.

## Destructive actions

An icon never substitutes for a destructive-action explanation. Data removal
must name its actual Runtime scope, distinguish preserved configuration from
deleted character/session data, and require the two-step confirmation owned by
`DataClearConfirmationDialog.tsx`.
