use serde::{Deserialize, Serialize};

pub const BOOTSTRAP_PREFIX: &str = "CHATWAIFU_BOOTSTRAP ";

#[derive(Clone, Debug, Deserialize, PartialEq)]
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

#[derive(Clone, Debug, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum RuntimeLifecycleState {
    Starting,
    Ready,
    Backoff,
    Stopped,
    CircuitOpen,
}

#[derive(Clone, Debug, PartialEq, Serialize)]
pub struct RuntimeStatus {
    pub state: RuntimeLifecycleState,
    pub runtime_url: Option<String>,
    pub pid: Option<u32>,
    pub workers: Vec<String>,
    pub token: Option<String>,
    pub restart_count: u32,
    pub detail: Option<String>,
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
}
