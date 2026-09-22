//! Temporary native OAuth callback; no provider or persistent account credentials.

use serde::Serialize;
use std::sync::{
    Arc, Mutex,
    atomic::{AtomicU64, Ordering},
};
use std::time::Duration;
use tauri::{State, WebviewWindow};
use tokio::{
    io::{AsyncReadExt, AsyncWriteExt},
    net::TcpListener,
    sync::watch,
};

static NEXT_FLOW: AtomicU64 = AtomicU64::new(1);

struct Flow {
    id: String,
    redirect: String,
    listener: Option<TcpListener>,
    cancel: watch::Sender<u8>,
}

#[derive(Default)]
pub struct OAuthState(Arc<Mutex<Option<Flow>>>);

impl OAuthState {
    pub fn cancel_all(&self) {
        if let Ok(mut slot) = self.0.lock() {
            if let Some(flow) = slot.take() {
                let _ = flow.cancel.send(1);
            }
        }
    }
}

#[derive(Serialize)]
pub struct CallbackReservation {
    flow_id: String,
    redirect_uri: String,
}

#[derive(Serialize)]
pub struct CallbackResult {
    state: String,
    code: String,
}

fn check_window(window: &WebviewWindow) -> Result<(), String> {
    if window.label() != crate::CONTROL_CENTER_LABEL || !window.is_visible().unwrap_or(false) {
        return Err("请在桌宠设置中授权日历".into());
    }
    Ok(())
}

#[tauri::command]
pub async fn prepare_google_oauth(
    window: WebviewWindow,
    state: State<'_, OAuthState>,
) -> Result<CallbackReservation, String> {
    check_window(&window)?;
    let listener = TcpListener::bind("127.0.0.1:0")
        .await
        .map_err(|_| "无法创建本机授权回调")?;
    let port = listener
        .local_addr()
        .map_err(|_| "无法获取回调端口")?
        .port();
    let redirect = format!("http://127.0.0.1:{port}/oauth/google");
    let id = NEXT_FLOW.fetch_add(1, Ordering::Relaxed).to_string();
    let (cancel, _) = watch::channel(0u8);
    let mut slot = state.0.lock().map_err(|_| "授权状态不可用")?;
    if let Some(previous) = slot.take() {
        let _ = previous.cancel.send(1);
    }
    *slot = Some(Flow {
        id: id.clone(),
        redirect: redirect.clone(),
        listener: Some(listener),
        cancel,
    });
    let mut cancelled = slot.as_ref().ok_or("授权已取消")?.cancel.subscribe();
    drop(slot);
    let shared = state.0.clone();
    let expires_id = id.clone();
    tauri::async_runtime::spawn(async move {
        tokio::select! {
            _ = cancelled.changed() => {},
            _ = tokio::time::sleep(Duration::from_secs(300)) => {
                if let Ok(mut slot) = shared.lock() {
                    if slot.as_ref().is_some_and(|f| f.id == expires_id) {
                        if let Some(flow) = slot.take() { let _ = flow.cancel.send(2); }
                    }
                }
            }
        }
    });
    Ok(CallbackReservation {
        flow_id: id,
        redirect_uri: redirect,
    })
}

#[tauri::command]
pub fn cancel_google_oauth(
    window: WebviewWindow,
    state: State<'_, OAuthState>,
    flow_id: String,
) -> Result<(), String> {
    if window.label() != crate::CONTROL_CENTER_LABEL {
        return Err("请在桌宠设置中操作".into());
    }
    let mut slot = state.0.lock().map_err(|_| "授权状态不可用")?;
    if slot.as_ref().is_some_and(|flow| flow.id == flow_id) {
        if let Some(flow) = slot.take() {
            let _ = flow.cancel.send(1);
        }
    }
    Ok(())
}

fn validate_url(value: &str, redirect: &str) -> Result<(tauri::Url, String), String> {
    if value.len() > 8192 {
        return Err("授权地址过长".into());
    }
    let url = tauri::Url::parse(value).map_err(|_| "授权地址无效")?;
    if url.scheme() != "https"
        || url.host_str() != Some("accounts.google.com")
        || !url.username().is_empty()
        || url.password().is_some()
        || url.port().is_some()
        || url.path() != "/o/oauth2/v2/auth"
        || url.fragment().is_some()
    {
        return Err("授权地址无效".into());
    }
    let mut params = std::collections::HashMap::new();
    for (key, value) in url.query_pairs() {
        if params
            .insert(key.into_owned(), value.into_owned())
            .is_some()
        {
            return Err("授权参数重复".into());
        }
    }
    let allowed = [
        "client_id",
        "redirect_uri",
        "response_type",
        "scope",
        "access_type",
        "prompt",
        "state",
        "code_challenge",
        "code_challenge_method",
    ];
    if params.keys().any(|key| !allowed.contains(&key.as_str())) {
        return Err("授权参数无效".into());
    }
    for (key, expected) in [
        ("redirect_uri", redirect),
        ("response_type", "code"),
        ("code_challenge_method", "S256"),
        ("scope", "https://www.googleapis.com/auth/calendar.readonly"),
        ("access_type", "offline"),
    ] {
        if params.get(key).map(String::as_str) != Some(expected) {
            return Err("授权参数无效".into());
        }
    }
    let expected_state = params
        .get("state")
        .filter(|s| s.len() >= 32 && s.len() <= 256)
        .ok_or("授权 state 无效")?
        .clone();
    if params.get("code_challenge").is_none_or(|s| {
        s.len() != 43
            || !s
                .bytes()
                .all(|b| b.is_ascii_alphanumeric() || b == b'-' || b == b'_')
    }) || params.get("client_id").is_none_or(String::is_empty)
    {
        return Err("授权参数无效".into());
    }
    Ok((url, expected_state))
}

fn open_browser(url: &str) -> Result<(), String> {
    #[cfg(target_os = "macos")]
    {
        let status = std::process::Command::new("/usr/bin/open")
            .arg("--")
            .arg(url)
            .status()
            .map_err(|_| "无法打开系统浏览器")?;
        if status.success() {
            Ok(())
        } else {
            Err("无法打开系统浏览器".into())
        }
    }
    #[cfg(not(target_os = "macos"))]
    {
        let _ = url;
        Err("此授权入口目前仅支持 macOS 桌宠".into())
    }
}

#[tauri::command]
pub async fn receive_google_oauth(
    window: WebviewWindow,
    state: State<'_, OAuthState>,
    flow_id: String,
    authorization_url: String,
) -> Result<CallbackResult, String> {
    check_window(&window)?;
    let (listener, mut cancelled, expected, url) = {
        let mut slot = state.0.lock().map_err(|_| "授权状态不可用")?;
        let flow = slot
            .as_mut()
            .filter(|f| f.id == flow_id)
            .ok_or("授权已取消")?;
        let (url, expected) = validate_url(&authorization_url, &flow.redirect)?;
        let listener = flow.listener.take().ok_or("授权正在进行")?;
        (listener, flow.cancel.subscribe(), expected, url)
    };
    let outcome = async {
        let browser_url = url.to_string();
        tauri::async_runtime::spawn_blocking(move || open_browser(&browser_url)).await
            .map_err(|_| "无法打开系统浏览器")??;
        tokio::select! {
            biased;
            _ = cancelled.changed() => Err(if *cancelled.borrow() == 2 {
                "授权已超时，请重试".into()
            } else { "授权已取消".into() }),
            result = tokio::time::timeout(Duration::from_secs(300), receive(&listener, &expected)) =>
                result.unwrap_or_else(|_| Err("授权已超时，请重试".into())),
        }
    }.await;
    if let Ok(mut slot) = state.0.lock() {
        if slot.as_ref().is_some_and(|f| f.id == flow_id) {
            slot.take();
        }
    }
    outcome
}

async fn receive(listener: &TcpListener, expected: &str) -> Result<CallbackResult, String> {
    loop {
        let (mut stream, _) = listener.accept().await.map_err(|_| "回调监听失败")?;
        let parsed = tokio::time::timeout(Duration::from_secs(2), async {
            let mut bytes = Vec::new();
            loop {
                let mut chunk = [0; 1024];
                let n = stream.read(&mut chunk).await.map_err(|_| ())?;
                if n == 0 || bytes.len() + n > 8192 {
                    return Err(());
                }
                bytes.extend_from_slice(&chunk[..n]);
                if bytes.windows(4).any(|w| w == b"\r\n\r\n") {
                    break;
                }
            }
            let request = std::str::from_utf8(&bytes).map_err(|_| ())?;
            let parts: Vec<_> = request
                .lines()
                .next()
                .ok_or(())?
                .split_whitespace()
                .collect();
            if parts.len() != 3 || parts[0] != "GET" || !parts[1].starts_with("/oauth/google?") {
                return Err(());
            }
            let url =
                tauri::Url::parse(&format!("http://127.0.0.1{}", parts[1])).map_err(|_| ())?;
            let mut params = std::collections::HashMap::new();
            for (key, value) in url.query_pairs() {
                if params
                    .insert(key.into_owned(), value.into_owned())
                    .is_some()
                {
                    return Err(());
                }
            }
            if params.get("state").map(String::as_str) != Some(expected) {
                return Err(());
            }
            if params.contains_key("error") {
                return Ok(None);
            }
            let code = params
                .get("code")
                .filter(|s| !s.is_empty() && s.len() <= 4096)
                .ok_or(())?;
            Ok(Some(code.clone()))
        })
        .await;
        let valid = matches!(&parsed, Ok(Ok(_)));
        let response = if valid {
            "HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\nCache-Control: no-store\r\nConnection: close\r\n\r\nReturn to ChatWaifu to finish authorization."
        } else {
            "HTTP/1.1 400 Bad Request\r\nCache-Control: no-store\r\nConnection: close\r\n\r\nInvalid callback."
        };
        let _ = tokio::time::timeout(
            Duration::from_secs(1),
            stream.write_all(response.as_bytes()),
        )
        .await;
        match parsed {
            Ok(Ok(Some(code))) => {
                return Ok(CallbackResult {
                    state: expected.to_string(),
                    code,
                });
            }
            Ok(Ok(None)) => return Err("你已取消 Google 授权".into()),
            _ => continue,
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn refuses_wrong_host_and_callback() {
        let redirect = "http://127.0.0.1:55555/oauth/google";
        let mut url = tauri::Url::parse("https://accounts.google.com/o/oauth2/v2/auth").unwrap();
        url.query_pairs_mut().extend_pairs([
            ("redirect_uri", redirect),
            ("response_type", "code"),
            ("code_challenge_method", "S256"),
            ("client_id", "client"),
            ("code_challenge", &"a".repeat(43)),
            ("state", &"s".repeat(43)),
            ("scope", "https://www.googleapis.com/auth/calendar.readonly"),
            ("access_type", "offline"),
        ]);
        assert!(validate_url(url.as_str(), redirect).is_ok());
        assert!(validate_url(url.as_str(), "http://127.0.0.1:44444/oauth/google").is_err());
        url.set_host(Some("example.com")).unwrap();
        assert!(validate_url(url.as_str(), redirect).is_err());
    }

    #[tokio::test]
    async fn wrong_state_does_not_consume_native_listener() {
        let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
        let address = listener.local_addr().unwrap();
        let receiver = tokio::spawn(async move { receive(&listener, "expected-state").await });
        for state in ["wrong-state", "expected-state"] {
            let mut stream = tokio::net::TcpStream::connect(address).await.unwrap();
            let request = format!(
                "GET /oauth/google?state={state}&code=short-code HTTP/1.1\r\nHost: localhost\r\n\r\n"
            );
            stream.write_all(request.as_bytes()).await.unwrap();
            let mut response = Vec::new();
            tokio::time::timeout(Duration::from_secs(2), stream.read_to_end(&mut response))
                .await
                .unwrap()
                .unwrap();
            assert!(
                String::from_utf8(response)
                    .unwrap()
                    .starts_with(if state == "wrong-state" {
                        "HTTP/1.1 400"
                    } else {
                        "HTTP/1.1 200"
                    })
            );
        }
        let result = receiver.await.unwrap().unwrap();
        assert_eq!(result.code, "short-code");
        assert_eq!(result.state, "expected-state");
    }
}
