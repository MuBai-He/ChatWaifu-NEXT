//! Thin Tauri host for ChatWaifu's desktop-pet and control-center surfaces.

mod runtime_health;
mod sidecar;

use serde::{Deserialize, Serialize};
use std::{
    fs,
    path::PathBuf,
    sync::{Mutex, MutexGuard},
};
use tauri::{
    AppHandle, Emitter, Manager, PhysicalPosition, PhysicalSize, Position, RunEvent, Size, State,
    WebviewUrl, WebviewWindow, WebviewWindowBuilder, Window, WindowEvent,
    image::Image,
    menu::{Menu, MenuItem},
    tray::TrayIconBuilder,
};

use runtime_health::RuntimeStatus;
use sidecar::RuntimeHost;

pub use sidecar::runtime_supervisor_exit_code;
#[cfg(target_os = "windows")]
#[doc(hidden)]
pub use sidecar::{windows_current_process_image_path, windows_physical_user_root_paths};

pub const HOST_ROLE: &str = "os-capabilities-and-sidecar-management";
pub const AVATAR_OVERLAY_LABEL: &str = "avatar-overlay";
pub const CONTROL_CENTER_LABEL: &str = "control-center";
pub const APP_ENTRY: &str = "index.html";
pub const PREFERENCES_CHANGED_EVENT: &str = "desktop-preferences-changed";
pub const CONTROL_CENTER_SURFACE: &str = "desktop-settings";
pub const NATIVE_SURFACE_QUERY: &str = "chatwaifu_surface";
pub const CONTROL_CENTER_INIT_SCRIPT: &str = r#"
Object.defineProperty(window, "__CHATWAIFU_NATIVE_SURFACE__", {
  value: "desktop-settings",
  writable: false,
  configurable: false
});
"#;

#[derive(Clone, Debug, Deserialize, PartialEq, Serialize)]
#[serde(default)]
pub struct DesktopPreferences {
    pub always_on_top: bool,
    pub click_through: bool,
    pub overlay_visible: bool,
    pub show_subtitles: bool,
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
            show_subtitles: true,
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
    interaction_region_active: Mutex<bool>,
    runtime: RuntimeHost,
}

#[tauri::command]
async fn show_control_center(app: AppHandle) -> Result<(), String> {
    show_control_center_window(&app)
}

fn show_control_center_window(app: &AppHandle) -> Result<(), String> {
    let window = match app.get_webview_window(CONTROL_CENTER_LABEL) {
        Some(window) => {
            refresh_development_window(app, &window)?;
            window
        }
        None => WebviewWindowBuilder::new(app, CONTROL_CENTER_LABEL, control_center_entry(app))
            .initialization_script(CONTROL_CENTER_INIT_SCRIPT)
            .title("ChatWaifu NEXT · 桌宠设置")
            .always_on_top(true)
            .inner_size(960.0, 700.0)
            .min_inner_size(720.0, 540.0)
            .center()
            .build()
            .map_err(window_error)?,
    };
    set_avatar_overlay_topmost(app, false)?;
    window.set_always_on_top(true).map_err(window_error)?;
    if let Err(error) = window.show().map_err(window_error) {
        restore_avatar_overlay_topmost(app);
        return Err(error);
    }
    if let Err(error) = window.set_focus().map_err(window_error) {
        restore_avatar_overlay_topmost(app);
        return Err(error);
    }
    Ok(())
}

#[cfg(debug_assertions)]
fn refresh_development_window(app: &AppHandle, window: &WebviewWindow) -> Result<(), String> {
    if let Some(dev_url) = development_control_center_url(app) {
        window.navigate(dev_url).map_err(window_error)?;
    }
    Ok(())
}

#[cfg(not(debug_assertions))]
fn refresh_development_window(_app: &AppHandle, _window: &WebviewWindow) -> Result<(), String> {
    Ok(())
}

fn control_center_entry(_app: &AppHandle) -> WebviewUrl {
    #[cfg(debug_assertions)]
    if let Some(dev_url) = development_control_center_url(_app) {
        return WebviewUrl::External(dev_url);
    }
    WebviewUrl::App(APP_ENTRY.into())
}

#[cfg(debug_assertions)]
fn development_control_center_url(app: &AppHandle) -> Option<tauri::Url> {
    let mut dev_url = app.config().build.dev_url.clone()?;
    dev_url
        .query_pairs_mut()
        .append_pair(NATIVE_SURFACE_QUERY, CONTROL_CENTER_SURFACE);
    Some(dev_url)
}

#[tauri::command]
fn get_desktop_preferences(state: State<'_, DesktopState>) -> Result<DesktopPreferences, String> {
    Ok(lock_preferences(&state)?.clone())
}

#[tauri::command]
async fn set_avatar_overlay_always_on_top(
    app: AppHandle,
    enabled: bool,
) -> Result<DesktopPreferences, String> {
    let state = app.state::<DesktopState>();
    set_always_on_top(&app, &state, enabled)
}

#[tauri::command]
async fn set_avatar_overlay_click_through(
    app: AppHandle,
    enabled: bool,
) -> Result<DesktopPreferences, String> {
    let state = app.state::<DesktopState>();
    set_click_through(&app, &state, enabled)
}

#[tauri::command]
async fn set_avatar_overlay_visible(
    app: AppHandle,
    enabled: bool,
) -> Result<DesktopPreferences, String> {
    let state = app.state::<DesktopState>();
    set_overlay_visible(&app, &state, enabled)
}

#[tauri::command]
fn set_avatar_overlay_display(
    app: AppHandle,
    state: State<'_, DesktopState>,
    show_subtitles: bool,
) -> Result<DesktopPreferences, String> {
    let preferences = update_preferences(&state, |preferences| {
        preferences.show_subtitles = show_subtitles;
    })?;
    commit_preferences(&app, &preferences)?;
    Ok(preferences)
}

#[tauri::command]
async fn set_avatar_overlay_interaction_region_active(
    app: AppHandle,
    active: bool,
) -> Result<(), String> {
    let state = app.state::<DesktopState>();
    let mut interaction_active = lock_interaction_region_active(&state)?;
    let click_through = lock_preferences(&state)?.click_through;
    required_window(&app, AVATAR_OVERLAY_LABEL)?
        .set_ignore_cursor_events(should_ignore_cursor_events(click_through, active))
        .map_err(window_error)?;
    *interaction_active = active;
    Ok(())
}

#[tauri::command]
fn start_runtime(app: AppHandle, state: State<'_, DesktopState>) -> Result<RuntimeStatus, String> {
    state.runtime.ensure_started(app)
}

#[tauri::command]
fn stop_runtime(state: State<'_, DesktopState>) -> Result<RuntimeStatus, String> {
    state.runtime.stop()
}

#[tauri::command]
fn restart_runtime(state: State<'_, DesktopState>) -> Result<RuntimeStatus, String> {
    state.runtime.restart()
}

#[tauri::command]
fn get_runtime_status(state: State<'_, DesktopState>) -> Result<RuntimeStatus, String> {
    state.runtime.status()
}

pub fn run() {
    let app = tauri::Builder::default()
        .manage(DesktopState::default())
        .setup(|app| {
            restore_preferences(app.handle())?;
            build_tray(app)?;
            let state = app.state::<DesktopState>();
            state
                .runtime
                .ensure_started(app.handle().clone())
                .map_err(std::io::Error::other)?;
            Ok(())
        })
        .on_window_event(handle_window_event)
        .invoke_handler(tauri::generate_handler![
            show_control_center,
            get_desktop_preferences,
            set_avatar_overlay_always_on_top,
            set_avatar_overlay_click_through,
            set_avatar_overlay_visible,
            set_avatar_overlay_display,
            set_avatar_overlay_interaction_region_active,
            start_runtime,
            stop_runtime,
            restart_runtime,
            get_runtime_status,
        ])
        .build(tauri::generate_context!())
        .expect("failed to build ChatWaifu desktop host");
    app.run(|handle, event| {
        if matches!(event, RunEvent::ExitRequested { .. } | RunEvent::Exit) {
            handle.state::<DesktopState>().runtime.shutdown_and_wait();
        }
    });
}

fn build_tray(app: &mut tauri::App) -> tauri::Result<()> {
    let tray_icon = Image::from_bytes(include_bytes!("../icons/tray-template.png"))?;
    let toggle_avatar =
        MenuItem::with_id(app, "toggle-avatar", "显示/隐藏角色", true, None::<&str>)?;
    let control_center =
        MenuItem::with_id(app, "control-center", "打开桌宠设置", true, None::<&str>)?;
    let click_through =
        MenuItem::with_id(app, "click-through", "切换透明区域穿透", true, None::<&str>)?;
    let restart_runtime =
        MenuItem::with_id(app, "restart-runtime", "重启本地服务", true, None::<&str>)?;
    let quit = MenuItem::with_id(app, "quit", "退出 ChatWaifu", true, None::<&str>)?;
    let menu = Menu::with_items(
        app,
        &[
            &toggle_avatar,
            &control_center,
            &click_through,
            &restart_runtime,
            &quit,
        ],
    )?;

    TrayIconBuilder::with_id("chatwaifu-desktop-pet")
        .icon(tray_icon)
        .icon_as_template(cfg!(target_os = "macos"))
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
                let app = app.clone();
                tauri::async_runtime::spawn(async move {
                    if let Err(error) = show_control_center(app).await {
                        eprintln!("desktop tray control-center failed: {error}");
                    }
                });
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
            "restart-runtime" => {
                if let Err(error) = app.state::<DesktopState>().runtime.restart() {
                    eprintln!("desktop tray Runtime restart failed: {error}");
                }
            }
            "quit" => {
                app.state::<DesktopState>().runtime.shutdown_and_wait();
                app.exit(0);
            }
            _ => {}
        })
        .build(app)?;
    Ok(())
}

fn handle_window_event(window: &Window, event: &WindowEvent) {
    if window.label() == CONTROL_CENTER_LABEL {
        match event {
            WindowEvent::Focused(true) => {
                if let Err(error) = window.set_always_on_top(true) {
                    eprintln!("desktop control-center promotion failed: {error}");
                }
                if let Err(error) = set_avatar_overlay_topmost(window.app_handle(), false) {
                    eprintln!("desktop overlay demotion failed: {error}");
                }
            }
            WindowEvent::Focused(false) | WindowEvent::Destroyed => {
                if let Err(error) = window.set_always_on_top(false) {
                    eprintln!("desktop control-center demotion failed: {error}");
                }
                restore_avatar_overlay_topmost(window.app_handle());
            }
            WindowEvent::CloseRequested { api, .. } => {
                api.prevent_close();
                if let Err(error) = window.hide() {
                    eprintln!("desktop control-center hide failed: {error}");
                }
                if let Err(error) = window.set_always_on_top(false) {
                    eprintln!("desktop control-center demotion failed: {error}");
                }
                restore_avatar_overlay_topmost(window.app_handle());
            }
            _ => {}
        }
        return;
    }
    if window.label() != AVATAR_OVERLAY_LABEL {
        return;
    }
    if !avatar_window_event_persists_preferences(event) {
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
        _ => unreachable!("filtered avatar window event changed before persistence"),
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

fn avatar_window_event_persists_preferences(event: &WindowEvent) -> bool {
    matches!(event, WindowEvent::Moved(_) | WindowEvent::Resized(_))
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
    commit_preferences(app, &preferences)?;
    Ok(preferences)
}

fn set_overlay_visible(
    app: &AppHandle,
    state: &State<'_, DesktopState>,
    enabled: bool,
) -> Result<DesktopPreferences, String> {
    let window = required_window(app, AVATAR_OVERLAY_LABEL)?;
    if enabled {
        window.show().map_err(window_error)?;
    } else {
        window.hide().map_err(window_error)?;
    }
    let preferences = update_preferences(state, |preferences| {
        preferences.overlay_visible = enabled;
    })?;
    commit_preferences(app, &preferences)?;
    Ok(preferences)
}

fn set_always_on_top(
    app: &AppHandle,
    state: &State<'_, DesktopState>,
    enabled: bool,
) -> Result<DesktopPreferences, String> {
    set_avatar_overlay_topmost(
        app,
        effective_overlay_topmost(enabled, control_center_is_focused(app)),
    )?;
    let preferences = update_preferences(state, |preferences| {
        preferences.always_on_top = enabled;
    })?;
    commit_preferences(app, &preferences)?;
    Ok(preferences)
}

fn set_click_through(
    app: &AppHandle,
    state: &State<'_, DesktopState>,
    enabled: bool,
) -> Result<DesktopPreferences, String> {
    let interaction_active = lock_interaction_region_active(state)?;
    required_window(app, AVATAR_OVERLAY_LABEL)?
        .set_ignore_cursor_events(should_ignore_cursor_events(enabled, *interaction_active))
        .map_err(window_error)?;
    let preferences = update_preferences(state, |preferences| {
        preferences.click_through = enabled;
    })?;
    drop(interaction_active);
    commit_preferences(app, &preferences)?;
    Ok(preferences)
}

fn should_ignore_cursor_events(click_through: bool, interaction_active: bool) -> bool {
    click_through && !interaction_active
}

fn control_center_is_focused(app: &AppHandle) -> bool {
    app.get_webview_window(CONTROL_CENTER_LABEL)
        .and_then(|window| window.is_focused().ok())
        .unwrap_or(false)
}

fn effective_overlay_topmost(preferred: bool, control_center_focused: bool) -> bool {
    preferred && !control_center_focused
}

fn set_avatar_overlay_topmost(app: &AppHandle, enabled: bool) -> Result<(), String> {
    required_window(app, AVATAR_OVERLAY_LABEL)?
        .set_always_on_top(enabled)
        .map_err(window_error)
}

fn restore_avatar_overlay_topmost(app: &AppHandle) {
    let enabled = app
        .state::<DesktopState>()
        .preferences
        .lock()
        .map(|preferences| preferences.always_on_top)
        .unwrap_or(true);
    if let Err(error) = set_avatar_overlay_topmost(app, enabled) {
        eprintln!("desktop overlay topmost restore failed: {error}");
    }
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

fn lock_interaction_region_active<'a>(
    state: &'a State<'_, DesktopState>,
) -> Result<MutexGuard<'a, bool>, String> {
    state
        .interaction_region_active
        .lock()
        .map_err(|_| "desktop interaction-region lock was poisoned".to_owned())
}

fn preferences_path(app: &AppHandle) -> Result<PathBuf, String> {
    sidecar::desktop_config_dir(app).map(|directory| directory.join("desktop-preferences.json"))
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

fn commit_preferences(app: &AppHandle, preferences: &DesktopPreferences) -> Result<(), String> {
    persist_preferences(app, preferences)?;
    app.emit(PREFERENCES_CHANGED_EVENT, preferences)
        .map_err(window_error)
}

fn window_error(error: tauri::Error) -> String {
    format!("desktop window operation failed: {error}")
}

#[cfg(test)]
mod tests {
    use super::{
        APP_ENTRY, CONTROL_CENTER_INIT_SCRIPT, CONTROL_CENTER_SURFACE, DesktopPreferences,
        HOST_ROLE, NATIVE_SURFACE_QUERY, avatar_window_event_persists_preferences,
        effective_overlay_topmost, set_avatar_overlay_always_on_top,
        set_avatar_overlay_click_through, set_avatar_overlay_interaction_region_active,
        set_avatar_overlay_visible, should_ignore_cursor_events, show_control_center,
    };
    use std::future::Future;
    use tauri::{PhysicalPosition, PhysicalSize, WindowEvent};

    #[test]
    fn host_role_does_not_claim_character_logic() {
        assert_eq!(HOST_ROLE, "os-capabilities-and-sidecar-management");
    }

    #[test]
    fn packaged_control_center_uses_the_stable_application_entry() {
        assert_eq!(APP_ENTRY, "index.html");
    }

    #[test]
    fn control_center_declares_an_explicit_frontend_surface_contract() {
        assert_eq!(CONTROL_CENTER_SURFACE, "desktop-settings");
        assert_eq!(NATIVE_SURFACE_QUERY, "chatwaifu_surface");
        assert!(CONTROL_CENTER_INIT_SCRIPT.contains("__CHATWAIFU_NATIVE_SURFACE__"));
        assert!(CONTROL_CENTER_INIT_SCRIPT.contains(CONTROL_CENTER_SURFACE));
    }

    #[test]
    fn control_center_command_is_async_for_windows_webview_creation() {
        fn assert_async_command<F, Fut>(_command: F)
        where
            F: Fn(tauri::AppHandle) -> Fut,
            Fut: Future<Output = Result<(), String>>,
        {
        }

        assert_async_command(show_control_center);
    }

    #[test]
    fn native_window_mutation_commands_are_async_on_windows() {
        fn assert_async_preference_command<F, Fut>(_command: F)
        where
            F: Fn(tauri::AppHandle, bool) -> Fut,
            Fut: Future<Output = Result<DesktopPreferences, String>>,
        {
        }

        fn assert_async_interaction_command<F, Fut>(_command: F)
        where
            F: Fn(tauri::AppHandle, bool) -> Fut,
            Fut: Future<Output = Result<(), String>>,
        {
        }

        assert_async_preference_command(set_avatar_overlay_always_on_top);
        assert_async_preference_command(set_avatar_overlay_click_through);
        assert_async_preference_command(set_avatar_overlay_visible);
        assert_async_interaction_command(set_avatar_overlay_interaction_region_active);
    }

    #[test]
    fn desktop_preferences_default_to_an_interactive_visible_pet() {
        let preferences = DesktopPreferences::default();
        assert!(preferences.always_on_top);
        assert!(!preferences.click_through);
        assert!(preferences.overlay_visible);
        assert!(preferences.show_subtitles);
        assert_eq!(preferences.overlay_x, None);
    }

    #[test]
    fn older_preference_files_receive_safe_defaults() {
        let preferences: DesktopPreferences = serde_json::from_str("{}").unwrap();
        assert_eq!(preferences, DesktopPreferences::default());
    }

    #[test]
    fn application_exit_does_not_persist_the_avatar_as_hidden() {
        assert!(!avatar_window_event_persists_preferences(
            &WindowEvent::Destroyed
        ));
        assert!(avatar_window_event_persists_preferences(
            &WindowEvent::Moved(PhysicalPosition::new(40, 60))
        ));
        assert!(avatar_window_event_persists_preferences(
            &WindowEvent::Resized(PhysicalSize::new(430, 650))
        ));
    }

    #[test]
    fn only_interactive_regions_override_transparent_click_through() {
        assert!(should_ignore_cursor_events(true, false));
        assert!(!should_ignore_cursor_events(true, true));
        assert!(!should_ignore_cursor_events(false, false));
        assert!(!should_ignore_cursor_events(false, true));
    }

    #[test]
    fn focused_control_center_temporarily_demotes_the_avatar_overlay() {
        assert!(effective_overlay_topmost(true, false));
        assert!(!effective_overlay_topmost(true, true));
        assert!(!effective_overlay_topmost(false, false));
        assert!(!effective_overlay_topmost(false, true));
    }

    #[test]
    fn avatar_overlay_keeps_realtime_tasks_running_while_inactive() {
        let config: serde_json::Value =
            serde_json::from_str(include_str!("../tauri.conf.json")).unwrap();
        let windows = config["app"]["windows"].as_array().unwrap();
        let overlay = windows
            .iter()
            .find(|window| window["label"] == "avatar-overlay")
            .unwrap();

        assert_eq!(overlay["backgroundThrottling"], "disabled");
    }
}
