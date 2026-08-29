use std::ffi::OsString;
use std::fs;
use std::os::windows::fs::MetadataExt;
use std::path::{Path, PathBuf};

use windows_sys::Win32::Storage::FileSystem::FILE_ATTRIBUTE_REPARSE_POINT;

use crate::cli::{NetworkPolicy, RunArgs};
use crate::error::{HostError, Result};
use crate::journal::{RootAccess, RootGrant};

pub(super) struct PreparedRun {
    pub(super) profile_name: String,
    pub(super) cwd: PathBuf,
    pub(super) roots: Vec<RootGrant>,
    pub(super) network: NetworkPolicy,
    pub(super) memory_bytes: u64,
    pub(super) max_processes: u32,
    pub(super) executable: PathBuf,
    pub(super) arguments: Vec<OsString>,
}

pub(super) fn validate_profile_name(name: &str) -> Result<()> {
    if name.is_empty()
        || name.len() > 128
        || !name
            .bytes()
            .all(|value| value.is_ascii_alphanumeric() || matches!(value, b'.' | b'_' | b'-'))
    {
        return Err(HostError::InvalidProfileName(name.to_owned()));
    }
    Ok(())
}

pub(super) fn prepare_state_dir(path: &Path) -> Result<PathBuf> {
    if !path.is_absolute() {
        return Err(HostError::InvalidStateDirectory(path.to_path_buf()));
    }
    reject_existing_reparse_components(path)
        .map_err(|_| HostError::InvalidStateDirectory(path.to_path_buf()))?;
    fs::create_dir_all(path)?;
    let canonical = fs::canonicalize(path)?;
    reject_existing_reparse_components(&canonical)
        .map_err(|_| HostError::InvalidStateDirectory(path.to_path_buf()))?;
    Ok(canonical)
}

pub(super) fn prepare_run(args: RunArgs, state_dir: PathBuf) -> Result<PreparedRun> {
    let cwd = canonical_sandbox_path(&args.cwd, true)?;
    let executable_value = args.child.first().ok_or_else(|| HostError::InvalidPath {
        path: PathBuf::new(),
        reason: "missing child executable".to_owned(),
    })?;
    let executable_path = PathBuf::from(executable_value);
    if !executable_path.is_absolute() {
        return Err(HostError::InvalidPath {
            path: executable_path,
            reason: "child executable must be absolute".to_owned(),
        });
    }
    let executable = canonical_sandbox_path(&executable_path, false)?;

    let mut roots = Vec::with_capacity(args.read_only.len() + args.writable.len());
    for path in args.read_only {
        roots.push(RootGrant {
            path: canonical_sandbox_path(&path, path.is_dir())?,
            access: RootAccess::ReadOnly,
            original_dacl_control: None,
            recursive_grant_complete: false,
        });
    }
    for path in args.writable {
        roots.push(RootGrant {
            path: canonical_sandbox_path(&path, path.is_dir())?,
            access: RootAccess::Writable,
            original_dacl_control: None,
            recursive_grant_complete: false,
        });
    }
    if roots.is_empty() {
        return Err(HostError::InvalidPath {
            path: cwd,
            reason: "at least one explicit read-only or writable root is required".to_owned(),
        });
    }

    for root in &roots {
        if paths_overlap(&root.path, &state_dir) {
            return Err(HostError::StateDirectoryOverlap {
                root: root.path.clone(),
                state_dir,
            });
        }
    }
    if !roots.iter().any(|root| is_within(&cwd, &root.path)) {
        return Err(HostError::UncoveredPath(cwd));
    }
    if !roots.iter().any(|root| is_within(&executable, &root.path)) {
        return Err(HostError::UncoveredPath(executable));
    }

    Ok(PreparedRun {
        profile_name: args.profile_name,
        cwd,
        roots,
        network: args.network,
        memory_bytes: args.memory_bytes,
        max_processes: args.max_processes,
        executable,
        arguments: args.child.into_iter().skip(1).collect(),
    })
}

pub(super) fn validate_journal_root(path: &Path) -> Result<()> {
    let canonical = canonical_sandbox_path(path, path.is_dir())?;
    if normalize(&canonical) != normalize(path) {
        return Err(HostError::InvalidPath {
            path: path.to_path_buf(),
            reason: "journal root no longer resolves to the recorded object".to_owned(),
        });
    }
    Ok(())
}

fn canonical_sandbox_path(path: &Path, must_be_directory: bool) -> Result<PathBuf> {
    if !path.is_absolute() {
        return Err(HostError::InvalidPath {
            path: path.to_path_buf(),
            reason: "path must be absolute".to_owned(),
        });
    }
    reject_existing_reparse_components(path)?;
    let canonical = fs::canonicalize(path).map_err(|error| HostError::InvalidPath {
        path: path.to_path_buf(),
        reason: error.to_string(),
    })?;
    reject_existing_reparse_components(&canonical)?;
    let metadata = fs::metadata(&canonical)?;
    if must_be_directory && !metadata.is_dir() {
        return Err(HostError::InvalidPath {
            path: canonical,
            reason: "expected a directory".to_owned(),
        });
    }
    if !must_be_directory && !metadata.is_file() {
        return Err(HostError::InvalidPath {
            path: canonical,
            reason: "expected a file".to_owned(),
        });
    }
    Ok(canonical)
}

fn reject_existing_reparse_components(path: &Path) -> Result<()> {
    for ancestor in path.ancestors() {
        let metadata = match fs::symlink_metadata(ancestor) {
            Ok(metadata) => metadata,
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => continue,
            Err(error) => return Err(error.into()),
        };
        if metadata.file_attributes() & FILE_ATTRIBUTE_REPARSE_POINT != 0 {
            return Err(HostError::InvalidPath {
                path: ancestor.to_path_buf(),
                reason: "reparse points are not trusted sandbox roots".to_owned(),
            });
        }
    }
    Ok(())
}

fn normalize(path: &Path) -> String {
    let mut value = path.as_os_str().to_string_lossy().replace('/', "\\");
    while value.ends_with('\\') {
        value.pop();
    }
    value.to_lowercase()
}

fn is_within(path: &Path, root: &Path) -> bool {
    let path = normalize(path);
    let root = normalize(root);
    path == root
        || path
            .strip_prefix(&root)
            .is_some_and(|suffix| suffix.starts_with('\\'))
}

fn paths_overlap(left: &Path, right: &Path) -> bool {
    is_within(left, right) || is_within(right, left)
}
