#![cfg_attr(not(windows), allow(dead_code))]

use std::collections::BTreeMap;
use std::fs::{self, File, OpenOptions};
use std::io::{BufReader, BufWriter, Write};
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicU64, Ordering};

use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};

use crate::error::{HostError, Result};

pub(crate) const JOURNAL_VERSION: u32 = 1;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub(crate) enum RootAccess {
    ReadOnly,
    Writable,
}

impl RootAccess {
    fn merge(self, other: Self) -> Self {
        if self == Self::Writable || other == Self::Writable {
            Self::Writable
        } else {
            Self::ReadOnly
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub(crate) struct RootGrant {
    pub(crate) path: PathBuf,
    pub(crate) access: RootAccess,
    /// DACL control bits observed before this profile's first grant.
    #[serde(default)]
    pub(crate) original_dacl_control: Option<u16>,
    /// Set only after protected descendants have received explicit ACEs.
    /// A false value is persisted before mutation so a crash retries safely.
    #[serde(default)]
    pub(crate) recursive_grant_complete: bool,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub(crate) struct ProfileJournal {
    pub(crate) version: u32,
    pub(crate) profile_name: String,
    pub(crate) sid: String,
    pub(crate) roots: Vec<RootGrant>,
}

impl ProfileJournal {
    pub(crate) fn new(profile_name: String, sid: String, roots: Vec<RootGrant>) -> Self {
        let mut journal = Self {
            version: JOURNAL_VERSION,
            profile_name,
            sid,
            roots: Vec::new(),
        };
        journal.merge_roots(roots);
        journal
    }

    pub(crate) fn merge_roots(&mut self, roots: impl IntoIterator<Item = RootGrant>) {
        let mut merged = BTreeMap::<String, RootGrant>::new();
        for root in self.roots.drain(..).chain(roots) {
            let key = path_key(&root.path);
            merged
                .entry(key)
                .and_modify(|existing| {
                    let merged_access = existing.access.merge(root.access);
                    if merged_access != existing.access {
                        existing.recursive_grant_complete = false;
                    }
                    existing.access = merged_access;
                    if existing.original_dacl_control.is_none() {
                        existing.original_dacl_control = root.original_dacl_control;
                    }
                })
                .or_insert(root);
        }
        self.roots = merged.into_values().collect();
    }

    pub(crate) fn validate(&self, expected_profile: &str, expected_sid: &str) -> Result<()> {
        if self.version != JOURNAL_VERSION {
            return Err(HostError::JournalInconsistent {
                profile: expected_profile.to_owned(),
                reason: format!("unsupported version {}", self.version),
            });
        }
        if self.profile_name != expected_profile || self.sid != expected_sid {
            return Err(HostError::JournalInconsistent {
                profile: expected_profile.to_owned(),
                reason: "profile name or derived SID does not match".to_owned(),
            });
        }
        Ok(())
    }
}

pub(crate) fn journal_dir(state_dir: &Path) -> PathBuf {
    state_dir.to_path_buf()
}

pub(crate) fn journal_path(state_dir: &Path, profile_name: &str) -> PathBuf {
    // Profile names are validated to a path-safe ASCII subset at the CLI
    // boundary. Keeping the moniker in the filename lets the Runtime detect
    // orphaned policy even when the helper binary is temporarily unavailable.
    journal_dir(state_dir).join(format!("{profile_name}.json"))
}

pub(crate) fn profile_key(profile_name: &str) -> String {
    hex_digest(profile_name.as_bytes())
}

pub(crate) fn state_key(state_dir: &Path) -> String {
    let normalized = state_dir
        .as_os_str()
        .to_string_lossy()
        .replace('/', "\\")
        .trim_end_matches('\\')
        .to_lowercase();
    hex_digest(normalized.as_bytes())
}

fn hex_digest(bytes: &[u8]) -> String {
    let digest = Sha256::digest(bytes);
    digest.iter().map(|byte| format!("{byte:02x}")).collect()
}

fn path_key(path: &Path) -> String {
    let value = path.as_os_str().to_string_lossy().replace('/', "\\");
    if cfg!(windows) {
        value.to_lowercase()
    } else {
        value
    }
}

pub(crate) fn load(path: &Path) -> Result<Option<ProfileJournal>> {
    match fs::symlink_metadata(path) {
        Ok(metadata) if metadata.file_type().is_symlink() || !metadata.is_file() => {
            return Err(HostError::JournalInconsistent {
                profile: path
                    .file_stem()
                    .unwrap_or_default()
                    .to_string_lossy()
                    .into_owned(),
                reason: "journal must be a regular, non-symlink file".to_owned(),
            });
        }
        Ok(_) => {}
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => return Ok(None),
        Err(error) => return Err(error.into()),
    }
    let file = match File::open(path) {
        Ok(file) => file,
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => return Ok(None),
        Err(error) => return Err(error.into()),
    };
    Ok(Some(serde_json::from_reader(BufReader::new(file))?))
}

pub(crate) fn load_all(state_dir: &Path) -> Result<Vec<(PathBuf, ProfileJournal)>> {
    let directory = journal_dir(state_dir);
    let entries = match fs::read_dir(&directory) {
        Ok(entries) => entries,
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => return Ok(Vec::new()),
        Err(error) => return Err(error.into()),
    };
    let mut journals = Vec::new();
    for entry in entries {
        let entry = entry?;
        let path = entry.path();
        let file_type = entry.file_type()?;
        if file_type.is_symlink()
            || !file_type.is_file()
            || path.extension().is_none_or(|ext| ext != "json")
        {
            continue;
        }
        let journal: ProfileJournal = serde_json::from_reader(BufReader::new(File::open(&path)?))?;
        journals.push((path, journal));
    }
    journals.sort_by(|left, right| left.0.cmp(&right.0));
    Ok(journals)
}

/// Persist the complete root union before any ACL mutation.
///
/// The temporary file is fsynced and atomically replaces the prior manifest.
/// On Windows `replace_file` adds write-through semantics.
pub(crate) fn write_durable(state_dir: &Path, journal: &ProfileJournal) -> Result<PathBuf> {
    let directory = journal_dir(state_dir);
    fs::create_dir_all(&directory)?;
    let destination = journal_path(state_dir, &journal.profile_name);
    static NEXT_TEMP: AtomicU64 = AtomicU64::new(0);
    let temp = directory.join(format!(
        ".{}.{}.{}.tmp",
        journal.profile_name,
        std::process::id(),
        NEXT_TEMP.fetch_add(1, Ordering::Relaxed)
    ));

    let file = OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(&temp)?;
    let mut writer = BufWriter::new(file);
    serde_json::to_writer_pretty(&mut writer, journal)?;
    writer.write_all(b"\n")?;
    writer.flush()?;
    writer.get_ref().sync_all()?;
    drop(writer);

    if let Err(error) = replace_file(&temp, &destination) {
        let _ = fs::remove_file(&temp);
        return Err(error);
    }
    Ok(destination)
}

#[cfg(windows)]
fn replace_file(source: &Path, destination: &Path) -> Result<()> {
    use std::os::windows::ffi::OsStrExt;
    use windows_sys::Win32::Storage::FileSystem::{
        MOVEFILE_REPLACE_EXISTING, MOVEFILE_WRITE_THROUGH, MoveFileExW,
    };

    let source: Vec<u16> = source.as_os_str().encode_wide().chain(Some(0)).collect();
    let destination: Vec<u16> = destination
        .as_os_str()
        .encode_wide()
        .chain(Some(0))
        .collect();
    // SAFETY: both arguments are live, NUL-terminated UTF-16 paths.
    let moved = unsafe {
        MoveFileExW(
            source.as_ptr(),
            destination.as_ptr(),
            MOVEFILE_REPLACE_EXISTING | MOVEFILE_WRITE_THROUGH,
        )
    };
    if moved == 0 {
        return Err(std::io::Error::last_os_error().into());
    }
    Ok(())
}

#[cfg(not(windows))]
fn replace_file(source: &Path, destination: &Path) -> Result<()> {
    fs::rename(source, destination)?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use std::path::PathBuf;

    use super::{ProfileJournal, RootAccess, RootGrant, journal_path, profile_key, state_key};

    #[test]
    fn profile_key_is_stable_and_path_safe() {
        let first = profile_key("ChatWaifu.Plugin.宁宁");
        let second = profile_key("ChatWaifu.Plugin.宁宁");
        assert_eq!(first, second);
        assert_eq!(first.len(), 64);
        assert!(first.chars().all(|value| value.is_ascii_hexdigit()));
        assert_eq!(
            journal_path(PathBuf::from("state").as_path(), "ChatWaifu.Plugin.safe"),
            PathBuf::from("state/ChatWaifu.Plugin.safe.json")
        );
    }

    #[test]
    fn root_union_keeps_the_strongest_access() {
        let path = PathBuf::from("plugin-data");
        let mut journal = ProfileJournal::new(
            "profile".to_owned(),
            "S-1-15-2-1".to_owned(),
            vec![RootGrant {
                path: path.clone(),
                access: RootAccess::ReadOnly,
                original_dacl_control: Some(0),
                recursive_grant_complete: true,
            }],
        );
        journal.merge_roots([RootGrant {
            path: path.clone(),
            access: RootAccess::Writable,
            original_dacl_control: Some(0x0400),
            recursive_grant_complete: false,
        }]);
        assert_eq!(journal.roots.len(), 1);
        assert_eq!(journal.roots[0].path, path);
        assert_eq!(journal.roots[0].access, RootAccess::Writable);
        assert_eq!(journal.roots[0].original_dacl_control, Some(0));
        assert!(!journal.roots[0].recursive_grant_complete);
    }

    #[test]
    fn state_mutex_key_uses_windows_path_equivalence() {
        assert_eq!(
            state_key(PathBuf::from(r"C:\ChatWaifu\State\").as_path()),
            state_key(PathBuf::from("c:/chatwaifu/state").as_path())
        );
    }
}
