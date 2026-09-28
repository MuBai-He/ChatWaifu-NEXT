//! Native-only device binding and EventKit boundary. No local Runtime or shell helper.
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use std::{collections::BTreeMap, fs, io::Write, sync::Mutex};
use tauri::{AppHandle, WebviewWindow};

#[derive(Default)]
pub struct AssistantDeviceState(pub Mutex<()>);

#[derive(Clone, Deserialize, Serialize, Default)]
struct Binding {
    device_id: String,
    secret: String,
    #[serde(default)]
    sources: Vec<Value>,
    #[serde(default)]
    source_revision: u64,
    #[serde(default)]
    journal: BTreeMap<String, Value>,
    #[serde(default)]
    presented: Vec<String>,
    #[serde(default)]
    presentation_receipts: Vec<String>,
    #[serde(default)]
    actions: BTreeMap<String, DeliveryActionRecord>,
}

#[derive(Clone, Copy, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
enum DeliveryDecision {
    Stop,
    Snooze,
}

#[derive(Clone, Deserialize, Serialize)]
struct DeliveryActionRecord {
    action: DeliveryDecision,
    #[serde(default)]
    rejected: bool,
}

fn record_presentation(binding: &mut Binding, id: &str) -> Result<bool, &'static str> {
    if id.len() != 36 {
        return Err("invalid_delivery");
    }
    if binding.presented.iter().any(|presented| presented == id) {
        return Ok(false);
    }
    if binding.presentation_receipts.len() >= 10_000 {
        return Err("presentation_receipt_queue_full");
    }
    binding.presented.push(id.into());
    binding.presentation_receipts.push(id.into());
    if binding.presented.len() > 1000 {
        binding.presented.remove(0);
    }
    Ok(true)
}

fn queue_action(
    binding: &mut Binding,
    id: &str,
    action: DeliveryDecision,
) -> Result<(), &'static str> {
    if id.len() != 36 || !binding.presented.iter().any(|presented| presented == id) {
        return Err("delivery_not_presented");
    }
    if let Some(old) = binding.actions.get(id) {
        return if old.action == action {
            Ok(())
        } else {
            Err("delivery_action_conflict")
        };
    }
    if binding.actions.len() >= 1000 {
        return Err("delivery_action_queue_full");
    }
    binding.actions.insert(
        id.into(),
        DeliveryActionRecord {
            action,
            rejected: false,
        },
    );
    Ok(())
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub struct DeviceCall {
    server: String,
    action: String,
    #[serde(default)]
    payload: Value,
}

fn save(app: &AppHandle, bindings: &BTreeMap<String, Binding>) -> Result<(), String> {
    let directory = super::sidecar::desktop_config_dir(app)?;
    fs::create_dir_all(&directory).map_err(|_| "device_storage_unavailable")?;
    let temporary = directory.join("assistant-devices.tmp");
    let mut options = fs::OpenOptions::new();
    options.write(true).create(true).truncate(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        options.mode(0o600);
    }
    let mut file = options
        .open(&temporary)
        .map_err(|_| "device_storage_unavailable")?;
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        file.set_permissions(fs::Permissions::from_mode(0o600))
            .map_err(|_| "device_storage_unavailable")?;
    }
    let bytes = serde_json::to_vec(bindings).map_err(|_| "device_encode_failed")?;
    file.write_all(&bytes)
        .and_then(|_| file.sync_all())
        .map_err(|_| "device_save_failed")?;
    fs::rename(temporary, directory.join("assistant-devices.json"))
        .map_err(|_| "device_save_failed".into())
}

#[cfg(target_os = "macos")]
fn eventkit(payload: &Value) -> Result<Value, String> {
    use std::ffi::{CStr, CString};
    unsafe extern "C" {
        fn cw_apple(input: *const libc::c_char) -> *mut libc::c_char;
    }
    let input = CString::new(payload.to_string()).map_err(|_| "invalid_native_request")?;
    // cw_apple owns a strdup buffer; always free it after copying, including parse errors.
    unsafe {
        let output = cw_apple(input.as_ptr());
        if output.is_null() {
            return Err("apple_native_error".into());
        }
        let bytes = CStr::from_ptr(output).to_bytes().to_vec();
        libc::free(output.cast());
        serde_json::from_slice(&bytes).map_err(|_| "apple_native_error".into())
    }
}
#[cfg(not(target_os = "macos"))]
fn eventkit(_payload: &Value) -> Result<Value, String> {
    Ok(json!({"error": "apple_not_supported"}))
}

fn run(app: AppHandle, call: DeviceCall) -> Result<Value, String> {
    use tauri::Manager;
    let state = app.state::<AssistantDeviceState>();
    let _guard = state.0.lock().map_err(|_| "device_busy")?;
    let url = tauri::Url::parse(&call.server).map_err(|_| "invalid_server")?;
    if url.path() != "/"
        || url.query().is_some()
        || url.fragment().is_some()
        || !url.username().is_empty()
        || url.password().is_some()
        || !(url.scheme() == "https"
            || (url.scheme() == "http"
                && matches!(url.host_str(), Some("127.0.0.1" | "localhost" | "[::1]"))))
    {
        return Err("device_requires_https_or_localhost".into());
    }
    let server = url.origin().ascii_serialization();
    let path = super::sidecar::desktop_config_dir(&app)?.join("assistant-devices.json");
    let mut bindings: BTreeMap<String, Binding> = match fs::read(path) {
        Ok(bytes) => serde_json::from_slice(&bytes).map_err(|_| "device_storage_corrupt")?,
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => BTreeMap::new(),
        Err(_) => return Err("device_storage_unavailable".into()),
    };
    if call.action == "notification_permission" {
        use tauri_plugin_notification::NotificationExt;
        return app
            .notification()
            .request_permission()
            .map(|p| json!({"permission": format!("{p:?}")}))
            .map_err(|_| "notification_permission_failed".into());
    }
    if call.action == "load" {
        return Ok(bindings.get(&server).map_or(Value::Null, |b| json!({"device_id":b.device_id,"secret":b.secret,"sources":b.sources,"source_revision":b.source_revision,"results":b.journal,"presentation_receipts":b.presentation_receipts.iter().take(20).collect::<Vec<_>>(),"actions":b.actions})));
    }
    if call.action == "pair" {
        let device_id = call.payload["device_id"]
            .as_str()
            .filter(|v| v.len() == 36)
            .ok_or("invalid_device")?;
        let secret = call.payload["secret"]
            .as_str()
            .filter(|v| (32..=128).contains(&v.len()))
            .ok_or("invalid_device")?;
        if bindings.contains_key(&server) {
            return Err("unpair_current_device_first".into());
        }
        bindings.insert(
            server,
            Binding {
                device_id: device_id.into(),
                secret: secret.into(),
                ..Default::default()
            },
        );
        save(&app, &bindings)?;
        return Ok(json!({"ok":true}));
    }
    if call.action == "forget" {
        bindings.remove(&server);
        save(&app, &bindings)?;
        return Ok(json!({"ok":true}));
    }
    if call.action == "permission" || call.action == "sources" {
        let resource = call.payload["resource"]
            .as_str()
            .filter(|r| matches!(*r, "calendar" | "reminder"))
            .ok_or("invalid_resource")?;
        return eventkit(&json!({"action":call.action,"resource":resource}));
    }
    let binding = bindings.get_mut(&server).ok_or("device_not_paired")?;
    match call.action.as_str() {
        "select" => {
            let selected = call
                .payload
                .as_array()
                .filter(|s| s.len() <= 50)
                .ok_or("invalid_sources")?;
            // Re-discover from EventKit: never accept frontend-supplied writability or source titles.
            let mut available = Vec::new();
            for resource in ["calendar", "reminder"] {
                let result = eventkit(&json!({"action":"sources","resource":resource}))?;
                if let Some(items) = result["items"].as_array() {
                    available.extend(items.clone());
                }
            }
            binding.source_revision += 1;
            binding.sources = available
                .into_iter()
                .filter(|source| {
                    selected
                        .iter()
                        .any(|s| s["id"] == source["id"] && s["resource"] == source["resource"])
                })
                .collect();
        }
        "present" => {
            let id = call.payload["id"].as_str().ok_or("invalid_delivery")?;
            let first = record_presentation(binding, id)?;
            if first {
                save(&app, &bindings)?;
            }
            return Ok(json!({"first": first}));
        }
        "queue_action" => {
            let id = call.payload["id"].as_str().ok_or("invalid_delivery")?;
            let action: DeliveryDecision = serde_json::from_value(call.payload["action"].clone())
                .map_err(|_| "invalid_delivery_action")?;
            queue_action(binding, id, action)?;
        }
        "reject_action" => {
            let id = call.payload.as_str().ok_or("invalid_delivery")?;
            let record = binding
                .actions
                .get_mut(id)
                .ok_or("delivery_action_missing")?;
            record.rejected = true;
        }
        "forget_action" => {
            let id = call.payload.as_str().ok_or("invalid_delivery")?;
            binding.actions.remove(id);
        }
        "notify" => {
            use tauri_plugin_notification::NotificationExt;
            let title = call.payload["title"]
                .as_str()
                .filter(|s| s.chars().count() <= 200)
                .ok_or("invalid_notification")?;
            app.notification()
                .builder()
                .title("ChatWaifu 提醒")
                .body(title)
                .show()
                .map_err(|_| "notification_failed")?;
        }
        "sound" => {
            return eventkit(&json!({"action":"sound"}));
        }
        "forget_result" => {
            if let Some(id) = call.payload.as_str() {
                binding.journal.remove(id);
            }
        }
        "forget_presentation" => {
            if let Some(id) = call.payload.as_str() {
                binding
                    .presentation_receipts
                    .retain(|receipt| receipt != id);
            }
        }
        "execute" => {
            let p = &call.payload;
            let id = p["request_id"]
                .as_str()
                .filter(|v| v.len() == 36)
                .ok_or("invalid_operation")?;
            if let Some(old) = binding.journal.get(id) {
                return Ok(old.clone());
            }
            if binding.journal.len() >= 200 {
                return Err("device_result_queue_full".into());
            }
            let now = std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .map_err(|_| "clock_unavailable")?
                .as_secs_f64();
            if p["expires_at"]
                .as_f64()
                .is_none_or(|expires| expires <= now || expires > now + 301.0)
            {
                return Err("operation_expired".into());
            }
            let action = p["action"].as_str().ok_or("invalid_action")?;
            if !matches!(action, "list" | "create" | "update" | "complete" | "delete")
                || p["device_id"] != binding.device_id
            {
                return Err("invalid_operation".into());
            }
            if !binding.sources.iter().any(|s| {
                s["id"] == p["calendar_id"]
                    && s["resource"] == p["resource"]
                    && (action == "list" || s["writable"] == true)
            }) {
                return Err("source_not_selected".into());
            }
            if p.to_string().len() > 8192 {
                return Err("operation_too_large".into());
            }
            if action != "list" {
                // A crash during a write leaves an explicit uncertain result; never repeat it.
                binding
                    .journal
                    .insert(id.into(), json!({"error":"apple_write_outcome_uncertain"}));
                save(&app, &bindings)?;
            }
            let result = eventkit(p).unwrap_or_else(|e| json!({"error":e}));
            bindings
                .get_mut(&server)
                .ok_or("device_not_paired")?
                .journal
                .insert(id.into(), result.clone());
            save(&app, &bindings)?;
            return Ok(result);
        }
        _ => return Err("invalid_device_action".into()),
    }
    save(&app, &bindings)?;
    Ok(json!({"ok":true}))
}

#[tauri::command]
pub async fn assistant_device(
    app: AppHandle,
    window: WebviewWindow,
    call: DeviceCall,
) -> Result<Value, String> {
    let settings = window.label() == "control-center";
    let overlay = window.label() == "avatar-overlay";
    if !settings && !overlay {
        return Err("device_window_not_allowed".into());
    }
    if matches!(
        call.action.as_str(),
        "pair" | "forget" | "select" | "sources" | "permission" | "notification_permission"
    ) && (!settings || !window.is_visible().unwrap_or(false))
    {
        return Err("open_settings_to_authorize_device".into());
    }
    if matches!(
        call.action.as_str(),
        "execute"
            | "present"
            | "sound"
            | "notify"
            | "forget_result"
            | "forget_presentation"
            | "queue_action"
            | "reject_action"
            | "forget_action"
    ) && !overlay
    {
        return Err("device_executor_is_overlay_only".into());
    }
    tauri::async_runtime::spawn_blocking(move || run(app, call))
        .await
        .map_err(|_| "device_worker_failed".to_string())?
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn queued_action_survives_binding_reload_and_cannot_change_choice() {
        let id = "d6eea974-cb98-4b95-81f1-36488178b013";
        let mut binding = Binding::default();
        assert_eq!(
            queue_action(&mut binding, id, DeliveryDecision::Snooze),
            Err("delivery_not_presented")
        );
        assert_eq!(record_presentation(&mut binding, id), Ok(true));
        assert_eq!(record_presentation(&mut binding, id), Ok(false));
        assert_eq!(binding.presentation_receipts, vec![id]);
        queue_action(&mut binding, id, DeliveryDecision::Snooze).unwrap();
        queue_action(&mut binding, id, DeliveryDecision::Snooze).unwrap();
        assert_eq!(
            queue_action(&mut binding, id, DeliveryDecision::Stop),
            Err("delivery_action_conflict")
        );
        let restored: Binding =
            serde_json::from_slice(&serde_json::to_vec(&binding).unwrap()).unwrap();
        assert_eq!(restored.actions.len(), 1);
        assert!(matches!(
            restored.actions.get(id),
            Some(DeliveryActionRecord {
                action: DeliveryDecision::Snooze,
                rejected: false
            })
        ));
    }
}
