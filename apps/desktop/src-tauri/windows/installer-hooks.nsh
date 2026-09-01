; Tauri retains MANUPRODUCTKEY during an ordinary data-preserving uninstall.
; That key contains installer metadata (the install location and installer
; language), not ChatWaifu user data. Remove it after a real uninstall so it
; cannot point a future install at an already-deleted directory. Updates keep
; the key until the replacement install rewrites it.
!macro NSIS_HOOK_POSTUNINSTALL
  ${If} $UpdateMode <> 1
    DeleteRegKey HKCU "${MANUPRODUCTKEY}"
    DeleteRegKey /ifempty HKCU "${MANUKEY}"
  ${EndIf}
!macroend
