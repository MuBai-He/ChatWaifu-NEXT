//! Persist the explicit client choice independently from local Runtime supervision.
use serde::{Deserialize, Serialize};
use std::{fs, io::Write};
use tauri::AppHandle;

// Deliberately no Debug: this structure contains the Runtime access token.
#[derive(Clone, Deserialize, Serialize)]
#[serde(tag = "mode", rename_all = "snake_case", deny_unknown_fields)]
pub enum ClientConnection {
    Local,
    Remote { base_url: String, token: String },
}

impl ClientConnection {
    pub fn validate(&self) -> Result<(), String> {
        if let Self::Remote { base_url, token } = self {
            let url = tauri::Url::parse(base_url).map_err(|_| "服务器地址无效")?;
            let loopback = url.host_str() == Some("127.0.0.1");
            if !(url.scheme() == "https" || (url.scheme() == "http" && loopback))
                || !url.username().is_empty()
                || url.password().is_some()
                || url.query().is_some()
                || url.fragment().is_some()
                || url.path() != "/"
                || token.trim().len() < 32
            {
                return Err(
                    "请使用 HTTPS 服务器根地址和至少 32 字符的访问令牌；本机隧道可使用 HTTP。"
                        .into(),
                );
            }
        }
        Ok(())
    }
}

pub fn load(app: &AppHandle) -> Result<Option<ClientConnection>, String> {
    let path = super::sidecar::desktop_config_dir(app)?.join("client-connection.json");
    let raw = match fs::read(path) {
        Ok(raw) => raw,
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => return Ok(None),
        Err(_) => return Err("无法读取客户端连接设置".into()),
    };
    let config: ClientConnection =
        serde_json::from_slice(&raw).map_err(|_| "客户端连接设置损坏，请重新配置")?;
    config.validate()?;
    Ok(Some(config))
}

pub fn save(app: &AppHandle, config: &ClientConnection) -> Result<(), String> {
    let directory = super::sidecar::desktop_config_dir(app)?;
    fs::create_dir_all(&directory).map_err(|_| "无法创建客户端配置目录")?;
    let path = directory.join("client-connection.json");
    let temporary = directory.join("client-connection.json.tmp");
    let mut options = fs::OpenOptions::new();
    options.write(true).create(true).truncate(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        options.mode(0o600);
    }
    let mut file = options
        .open(&temporary)
        .map_err(|_| "无法保存客户端连接设置")?;
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        file.set_permissions(fs::Permissions::from_mode(0o600))
            .map_err(|_| "无法保护客户端连接设置")?;
    }
    let bytes = serde_json::to_vec(config).map_err(|_| "无法编码客户端连接设置")?;
    file.write_all(&bytes)
        .and_then(|_| file.sync_all())
        .map_err(|_| "无法写入客户端连接设置")?;
    fs::rename(temporary, path).map_err(|_| "无法提交客户端连接设置".to_owned())
}
