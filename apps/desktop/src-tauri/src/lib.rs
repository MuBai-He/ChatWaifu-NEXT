//! Thin Tauri host for ChatWaifu's desktop-pet and control-center surfaces.

use serde::{Deserialize, Serialize};
use std::{
    fs,
    path::PathBuf,
    sync::{Mutex, MutexGuard},
};
use tauri::{
    AppHandle, Manager, PhysicalPosition, PhysicalSize, Position, Size, State, WebviewUrl,
    WebviewWindow, WebviewWindowBuilder, Window, WindowEvent,
    menu::{Menu, MenuItem},
    tray::TrayIconBuilder,
};

pub const HOST_ROLE: &str = "os-capabilities-and-sidecar-management";
pub const AVATAR_OVERLAY_LABEL: &str = "avatar-overlay";
pub const CONTROL_CENTER_LABEL: &str = "control-center";

#[derive(Clone, Debug, Deserialize, PartialEq, Serialize)]
#[serde(default)]
pub struct DesktopPreferences {
    pub always_on_top: bool,
    pub click_through: bool,
    pub overlay_visible: bool,
    pub overlay_x: Option<i32>,
    pub overlay_y: Option<i32>,
    pub overlay_width: Option<u32>,
    pub overlay_height: Option<u32>,
}

impl Default for DesktopPreferences {
    fn default() -> Self {
        Self {
            always_on_top: true,
            click_through: false,
            overlay_visible: true,
            overlay_x: None,
            overlay_y: None,
            overlay_width: None,
            overlay_height: None,
        }
    }
}

#[derive(Default)]
struct DesktopState {
    preferences: Mutex<DesktopPreferences>,
}

#[tauri::command]
fn show_control_center(app: AppHandle) -> Result<(), String> {
    let window = match app.get_webview_window(CONTROL_CENTER_LABEL) {
        Some(window) => window,
        None => WebviewWindowBuilder::new(
            &app,
            CONTROL_CENTER_LABEL,
            WebviewUrl::App("/control-center".into()),
        )
        .title("ChatWaifu NEXT · 控制中心")
        .inner_size(1280.0, 820.0)
        .min_inner_size(900.0, 620.0)
        .center()
        .build()
        .map_err(window_error)?,
    };
    window.show().map_err(window_error)?;
    window.set_focus().map_err(window_error)
}

#[tauri::command]
fn get_desktop_preferences(state: State<'_, DesktopState>) -> Result<DesktopPreferences, String> {
    Ok(lock_preferences(&state)?.clone())
}

#[tauri::command]
fn set_avatar_overlay_always_on_top(
    app: AppHandle,
    state: State<'_, DesktopState>,
    enabled: bool,
) -> Result<DesktopPreferences, String> {
    set_always_on_top(&app, &state, enabled)
}

#[tauri::command]
fn set_avatar_overlay_click_through(
    app: AppHandle,
    state: State<'_, DesktopState>,
    enabled: bool,
) -> Result<DesktopPreferences, String> {
    set_click_through(&app, &state, enabled)
}

pub fn run() {
    tauri::Builder::default()
        .manage(DesktopState::default())
        .setup(|app| {
            restore_preferences(app.handle())?;
            build_tray(app)?;
            Ok(())
        })
        .on_window_event(handle_window_event)
        .invoke_handler(tauri::generate_handler![
            show_control_center,
            get_desktop_preferences,
            set_avatar_overlay_always_on_top,
            set_avatar_overlay_click_through,
        ])
        .run(tauri::generate_context!())
        .expect("failed to run ChatWaifu desktop host");
}

fn build_tray(app: &mut tauri::App) -> tauri::Result<()> {
    let toggle_avatar =
        MenuItem::with_id(app, "toggle-avatar", "显示/隐藏角色", true, None::<&str>)?;
    let control_center =
        MenuItem::with_id(app, "control-center", "打开控制中心", true, None::<&str>)?;
    let click_through =
        MenuItem::with_id(app, "click-through", "切换鼠标穿透", true, None::<&str>)?;
    let quit = MenuItem::with_id(app, "quit", "退出 ChatWaifu", true, None::<&str>)?;
    let menu = Menu::with_items(
        app,
        &[&toggle_avatar, &control_center, &click_through, &quit],
    )?;

    TrayIconBuilder::with_id("chatwaifu-desktop-pet")
        .title("宁")
        .tooltip("ChatWaifu NEXT · 绫地宁宁")
        .menu(&menu)
        .show_menu_on_left_click(true)
        .on_menu_event(|app, event| match event.id.as_ref() {
            "toggle-avatar" => {
                if let Err(error) = toggle_avatar_visibility(app) {
                    eprintln!("desktop tray toggle-avatar failed: {error}");
                }
            }
            "control-center" => {
                if let Err(error) = show_control_center(app.clone()) {
                    eprintln!("desktop tray control-center failed: {error}");
                }
            }
            "click-through" => {
                let state = app.state::<DesktopState>();
                let enabled = match lock_preferences(&state) {
                    Ok(preferences) => !preferences.click_through,
                    Err(error) => {
                        eprintln!("desktop tray click-through state failed: {error}");
                        return;
                    }
                };
                if let Err(error) = set_click_through(app, &state, enabled) {
                    eprintln!("desktop tray click-through failed: {error}");
                }
            }
            "quit" => app.exit(0),
            _ => {}
        })
        .build(app)?;
    Ok(())
}

fn handle_window_event(window: &Window, event: &WindowEvent) {
    if window.label() == CONTROL_CENTER_LABEL {
        if let WindowEvent::CloseRequested { api, .. } = event {
            api.prevent_close();
            if let Err(error) = window.hide() {
                eprintln!("desktop control-center hide failed: {error}");
            }
        }
        return;
    }
    if window.label() != AVATAR_OVERLAY_LABEL {
        return;
    }

    let state = window.state::<DesktopState>();
    let update = match event {
        WindowEvent::Moved(position) => update_preferences(&state, |preferences| {
            preferences.overlay_x = Some(position.x);
            preferences.overlay_y = Some(position.y);
        }),
        WindowEvent::Resized(size) => update_preferences(&state, |preferences| {
            preferences.overlay_width = Some(size.width);
            preferences.overlay_height = Some(size.height);
        }),
        WindowEvent::Destroyed => update_preferences(&state, |preferences| {
            preferences.overlay_visible = false;
        }),
        _ => return,
    };
    match update {
        Ok(preferences) => {
            if let Err(error) = persist_preferences(window.app_handle(), &preferences) {
                eprintln!("desktop preference persistence failed: {error}");
            }
        }
        Err(error) => eprintln!("desktop preference update failed: {error}"),
    }
}

fn restore_preferences(app: &AppHandle) -> tauri::Result<()> {
    let preferences = load_preferences(app);
    let state = app.state::<DesktopState>();
    if let Ok(mut current) = state.preferences.lock() {
        *current = preferences.clone();
    }
    let Some(window) = app.get_webview_window(AVATAR_OVERLAY_LABEL) else {
        return Ok(());
    };
    window.set_always_on_top(preferences.always_on_top)?;
    window.set_ignore_cursor_events(preferences.click_through)?;
    if let (Some(x), Some(y)) = (preferences.overlay_x, preferences.overlay_y) {
        window.set_position(Position::Physical(PhysicalPosition::new(x, y)))?;
    }
    if let (Some(width), Some(height)) = (preferences.overlay_width, preferences.overlay_height) {
        window.set_size(Size::Physical(PhysicalSize::new(width, height)))?;
    }
    if preferences.overlay_visible {
        window.show()?;
    } else {
        window.hide()?;
    }
    Ok(())
}

fn toggle_avatar_visibility(app: &AppHandle) -> Result<DesktopPreferences, String> {
    let window = required_window(app, AVATAR_OVERLAY_LABEL)?;
    let visible = window.is_visible().map_err(window_error)?;
    if visible {
        window.hide().map_err(window_error)?;
    } else {
        window.show().map_err(window_error)?;
    }
    let state = app.state::<DesktopState>();
    let preferences = update_preferences(&state, |preferences| {
        preferences.overlay_visible = !visible;
    })?;
    persist_preferences(app, &preferences)?;
    Ok(preferences)
}

fn set_always_on_top(
    app: &AppHandle,
    state: &State<'_, DesktopState>,
    enabled: bool,
) -> Result<DesktopPreferences, String> {
    required_window(app, AVATAR_OVERLAY_LABEL)?
        .set_always_on_top(enabled)
        .map_err(window_error)?;
    let preferences = update_preferences(state, |preferences| {
        preferences.always_on_top = enabled;
    })?;
    persist_preferences(app, &preferences)?;
    Ok(preferences)
}

fn set_click_through(
    app: &AppHandle,
    state: &State<'_, DesktopState>,
    enabled: bool,
) -> Result<DesktopPreferences, String> {
    required_window(app, AVATAR_OVERLAY_LABEL)?
        .set_ignore_cursor_events(enabled)
        .map_err(window_error)?;
    let preferences = update_preferences(state, |preferences| {
        preferences.click_through = enabled;
    })?;
    persist_preferences(app, &preferences)?;
    Ok(preferences)
}

fn required_window(app: &AppHandle, label: &str) -> Result<WebviewWindow, String> {
    app.get_webview_window(label)
        .ok_or_else(|| format!("desktop window {label} is unavailable"))
}

fn update_preferences(
    state: &State<'_, DesktopState>,
    update: impl FnOnce(&mut DesktopPreferences),
) -> Result<DesktopPreferences, String> {
    let mut preferences = lock_preferences(state)?;
    update(&mut preferences);
    Ok(preferences.clone())
}

fn lock_preferences<'a>(
    state: &'a State<'_, DesktopState>,
) -> Result<MutexGuard<'a, DesktopPreferences>, String> {
    state
        .preferences
        .lock()
        .map_err(|_| "desktop preference lock was poisoned".to_owned())
}

fn preferences_path(app: &AppHandle) -> Result<PathBuf, String> {
    app.path()
        .app_config_dir()
        .map(|directory| directory.join("desktop-preferences.json"))
        .map_err(|error| format!("desktop config directory unavailable: {error}"))
}

fn load_preferences(app: &AppHandle) -> DesktopPreferences {
    let Ok(path) = preferences_path(app) else {
        return DesktopPreferences::default();
    };
    let Ok(raw) = fs::read_to_string(path) else {
        return DesktopPreferences::default();
    };
    serde_json::from_str(&raw).unwrap_or_else(|error| {
        eprintln!("desktop preference file ignored: {error}");
        DesktopPreferences::default()
    })
}

fn persist_preferences(app: &AppHandle, preferences: &DesktopPreferences) -> Result<(), String> {
    let path = preferences_path(app)?;
    let directory = path
        .parent()
        .ok_or_else(|| "desktop preference path has no parent".to_owned())?;
    fs::create_dir_all(directory)
        .map_err(|error| format!("desktop config directory creation failed: {error}"))?;
    let temporary = path.with_extension("json.tmp");
    let payload = serde_json::to_vec_pretty(preferences)
        .map_err(|error| format!("desktop preference serialization failed: {error}"))?;
    fs::write(&temporary, payload)
        .map_err(|error| format!("desktop preference write failed: {error}"))?;
    fs::rename(&temporary, &path)
        .map_err(|error| format!("desktop preference commit failed: {error}"))
}

fn window_error(error: tauri::Error) -> String {
    format!("desktop window operation failed: {error}")
}

#[cfg(test)]
mod tests {
    use super::{DesktopPreferences, HOST_ROLE};

    #[test]
    fn host_role_does_not_claim_character_logic() {
        assert_eq!(HOST_ROLE, "os-capabilities-and-sidecar-management");
    }

    #[test]
    fn desktop_preferences_default_to_an_interactive_visible_pet() {
        let preferences = DesktopPreferences::default();
        assert!(preferences.always_on_top);
        assert!(!preferences.click_through);
        assert!(preferences.overlay_visible);
        assert_eq!(preferences.overlay_x, None);
    }

    #[test]
    fn older_preference_files_receive_safe_defaults() {
        let preferences: DesktopPreferences = serde_json::from_str("{}").unwrap();
        assert_eq!(preferences, DesktopPreferences::default());
    }
}
