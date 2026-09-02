use serde::{Deserialize, Serialize};

pub const BOOTSTRAP_PREFIX: &str = "CHATWAIFU_BOOTSTRAP ";

#[derive(Clone, Deserialize, PartialEq)]
pub struct RuntimeBootstrap {
    pub schema_version: String,
    #[serde(rename = "type")]
    pub event_type: String,
    pub runtime_url: String,
    pub pid: u32,
    pub workers: Vec<String>,
    #[serde(default)]
    pub token: Option<String>,
}

impl std::fmt::Debug for RuntimeBootstrap {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("RuntimeBootstrap")
            .field("schema_version", &self.schema_version)
            .field("type", &self.event_type)
            .field("runtime_url", &self.runtime_url)
            .field("pid", &self.pid)
            .field("workers", &self.workers)
            .field("token", &self.token.as_ref().map(|_| "[REDACTED]"))
            .finish()
    }
}

#[derive(Clone, Debug, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum RuntimeLifecycleState {
    Starting,
    Ready,
    Backoff,
    Stopped,
    CircuitOpen,
}

#[derive(Clone, PartialEq, Serialize)]
pub struct RuntimeStatus {
    pub state: RuntimeLifecycleState,
    pub runtime_url: Option<String>,
    pub pid: Option<u32>,
    pub workers: Vec<String>,
    #[serde(skip)]
    pub token: Option<String>,
    pub restart_count: u32,
    pub detail: Option<String>,
}

impl std::fmt::Debug for RuntimeStatus {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("RuntimeStatus")
            .field("state", &self.state)
            .field("runtime_url", &self.runtime_url)
            .field("pid", &self.pid)
            .field("workers", &self.workers)
            .field("token", &self.token.as_ref().map(|_| "[REDACTED]"))
            .field("restart_count", &self.restart_count)
            .field("detail", &self.detail)
            .finish()
    }
}

impl Default for RuntimeStatus {
    fn default() -> Self {
        Self {
            state: RuntimeLifecycleState::Stopped,
            runtime_url: None,
            pid: None,
            workers: Vec::new(),
            token: None,
            restart_count: 0,
            detail: None,
        }
    }
}

pub fn parse_bootstrap_line(line: &str) -> Result<Option<RuntimeBootstrap>, String> {
    let Some(raw) = line.strip_prefix(BOOTSTRAP_PREFIX) else {
        return Ok(None);
    };
    let bootstrap: RuntimeBootstrap = serde_json::from_str(raw)
        .map_err(|error| format!("invalid Runtime bootstrap payload: {error}"))?;
    if bootstrap.schema_version != "1.0" {
        return Err(format!(
            "unsupported Runtime bootstrap schema {}",
            bootstrap.schema_version
        ));
    }
    if bootstrap.event_type != "runtime.ready" {
        return Err(format!(
            "unexpected Runtime bootstrap type {}",
            bootstrap.event_type
        ));
    }
    if !is_loopback_runtime_url(&bootstrap.runtime_url) {
        return Err("Runtime bootstrap URL must use loopback HTTP".to_owned());
    }
    Ok(Some(bootstrap))
}

fn is_loopback_runtime_url(value: &str) -> bool {
    let Some(rest) = value.strip_prefix("http://") else {
        return false;
    };
    let Some((host, port)) = rest.rsplit_once(':') else {
        return false;
    };
    matches!(host, "127.0.0.1" | "localhost" | "[::1]")
        && port.parse::<u16>().is_ok_and(|port| port > 0)
}

#[cfg(test)]
mod tests {
    use super::{RuntimeLifecycleState, RuntimeStatus, parse_bootstrap_line};

    #[test]
    fn parses_versioned_loopback_bootstrap_without_a_secret() {
        let line = concat!(
            "CHATWAIFU_BOOTSTRAP ",
            r#"{"schema_version":"1.0","type":"runtime.ready","runtime_url":"http://127.0.0.1:43127","pid":12345,"workers":["stt"]}"#
        );
        let parsed = parse_bootstrap_line(line).unwrap().unwrap();
        assert_eq!(parsed.runtime_url, "http://127.0.0.1:43127");
        assert_eq!(parsed.pid, 12345);
        assert_eq!(parsed.workers, vec!["stt"]);
        assert_eq!(parsed.token, None);
    }

    #[test]
    fn parses_versioned_loopback_bootstrap_with_a_secret() {
        let line = concat!(
            "CHATWAIFU_BOOTSTRAP ",
            r#"{"schema_version":"1.0","type":"runtime.ready","runtime_url":"http://127.0.0.1:43127","pid":12345,"workers":["stt"],"token":"secret123"}"#
        );
        let parsed = parse_bootstrap_line(line).unwrap().unwrap();
        assert_eq!(parsed.runtime_url, "http://127.0.0.1:43127");
        assert_eq!(parsed.pid, 12345);
        assert_eq!(parsed.workers, vec!["stt"]);
        assert_eq!(parsed.token, Some("secret123".to_owned()));

        let debug_repr = format!("{parsed:?}");
        assert!(debug_repr.contains("[REDACTED]"));
        assert!(!debug_repr.contains("secret123"));
    }

    #[test]
    fn ignores_normal_sidecar_logs() {
        assert_eq!(
            parse_bootstrap_line("Application startup complete.").unwrap(),
            None
        );
    }

    #[test]
    fn rejects_non_loopback_bootstrap_urls() {
        let line = concat!(
            "CHATWAIFU_BOOTSTRAP ",
            r#"{"schema_version":"1.0","type":"runtime.ready","runtime_url":"http://192.0.2.4:8765","pid":1,"workers":[]}"#
        );
        assert!(parse_bootstrap_line(line).unwrap_err().contains("loopback"));
    }

    #[test]
    fn default_status_is_truthfully_stopped() {
        let status = RuntimeStatus::default();
        assert_eq!(status.state, RuntimeLifecycleState::Stopped);
        assert_eq!(status.runtime_url, None);
        assert_eq!(status.token, None);
    }

    #[test]
    fn status_redacts_debug_token_and_omits_it_from_json_serialization() {
        let status = RuntimeStatus {
            state: RuntimeLifecycleState::Ready,
            runtime_url: Some("http://127.0.0.1:8765".to_owned()),
            pid: Some(1234),
            workers: vec!["stt".to_owned()],
            token: Some("super_secret_token".to_owned()),
            restart_count: 0,
            detail: None,
        };

        let debug_str = format!("{status:?}");
        assert!(debug_str.contains("[REDACTED]"));
        assert!(!debug_str.contains("super_secret_token"));

        let json_str = serde_json::to_string(&status).expect("status should serialize to json");
        assert!(!json_str.contains("token"));
        assert!(!json_str.contains("super_secret_token"));
    }
}
