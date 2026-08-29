#![cfg_attr(not(windows), allow(dead_code))]

use std::path::PathBuf;

#[derive(Debug, thiserror::Error)]
pub(crate) enum HostError {
    #[error("Windows AppContainer isolation is unsupported on this platform")]
    #[cfg(not(windows))]
    UnsupportedPlatform,

    #[error("invalid profile name {0:?}; expected 1-128 ASCII letters, digits, '.', '_' or '-'")]
    InvalidProfileName(String),

    #[error("trusted state directory must be an absolute, non-reparse path: {0}")]
    InvalidStateDirectory(PathBuf),

    #[error("invalid sandbox path {path}: {reason}")]
    InvalidPath { path: PathBuf, reason: String },

    #[error("sandbox root {root} overlaps trusted state directory {state_dir}")]
    StateDirectoryOverlap { root: PathBuf, state_dir: PathBuf },

    #[error("child executable and cwd must be covered by an explicit sandbox root: {0}")]
    UncoveredPath(PathBuf),

    #[error("journal for {profile} is inconsistent: {reason}")]
    JournalInconsistent { profile: String, reason: String },

    #[error("Windows API {api} failed with code {code}")]
    WindowsApi { api: &'static str, code: u32 },

    #[error("AppContainer operation failed: {0}")]
    AppContainer(String),

    #[error(transparent)]
    Io(#[from] std::io::Error),

    #[error(transparent)]
    Json(#[from] serde_json::Error),

    #[error("child I/O bridge failed: {0}")]
    ChildIo(String),
}

impl HostError {
    pub(crate) fn exit_code(&self) -> i32 {
        match self {
            #[cfg(not(windows))]
            Self::UnsupportedPlatform => 78,
            Self::InvalidProfileName(_)
            | Self::InvalidStateDirectory(_)
            | Self::InvalidPath { .. }
            | Self::StateDirectoryOverlap { .. }
            | Self::UncoveredPath(_)
            | Self::JournalInconsistent { .. } => 64,
            _ => 70,
        }
    }
}

pub(crate) type Result<T> = std::result::Result<T, HostError>;
