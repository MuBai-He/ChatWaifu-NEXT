mod acl;
mod launch;
mod mutex;
mod paths;

use std::collections::HashSet;
use std::fs;
use std::path::{Path, PathBuf};

use rappct::{AppContainerProfile, derive_sid_from_name};

use crate::cli::{Command, ProfileArgs, ReconcileArgs, RunArgs};
use crate::error::{HostError, Result};
use crate::journal::{self, ProfileJournal};

pub(super) fn execute(command: Command) -> Result<i32> {
    match command {
        Command::Run(args) => run(args),
        Command::Revoke(args) => {
            revoke(args)?;
            Ok(0)
        }
        Command::Reconcile(args) => {
            reconcile(args)?;
            Ok(0)
        }
    }
}

fn run(args: RunArgs) -> Result<i32> {
    paths::validate_profile_name(&args.profile_name)?;
    let state_dir = paths::prepare_state_dir(&args.state_dir)?;
    let state_mutex_name = format!("state-{}", journal::state_key(&state_dir));
    let state_lock = mutex::NamedMutex::acquire(&state_mutex_name)?;
    let profile_lock = mutex::NamedMutex::acquire(&format!(
        "profile-{}",
        journal::profile_key(&args.profile_name)
    ))?;

    let mut prepared = paths::prepare_run(args, state_dir.clone())?;
    for root in &mut prepared.roots {
        root.original_dacl_control = Some(acl::dacl_control(&root.path)?);
    }
    let sid = derive_sid_from_name(&prepared.profile_name)
        .map_err(|error| HostError::AppContainer(error.to_string()))?
        .as_string()
        .to_owned();
    let manifest_path = journal::journal_path(&state_dir, &prepared.profile_name);
    let mut manifest = match journal::load(&manifest_path)? {
        Some(existing) => {
            existing.validate(&prepared.profile_name, &sid)?;
            existing
        }
        None => ProfileJournal::new(prepared.profile_name.clone(), sid.clone(), Vec::new()),
    };
    manifest.merge_roots(prepared.roots.clone());

    // This fsynced manifest deliberately precedes profile creation and every
    // grant. A crash can leave an unnecessary journal, but never an untracked
    // AppContainer profile or filesystem ACE.
    journal::write_durable(&state_dir, &manifest)?;
    let profile = AppContainerProfile::ensure(
        &prepared.profile_name,
        &prepared.profile_name,
        Some("ChatWaifu Runtime Skill"),
    )
    .map_err(|error| HostError::AppContainer(error.to_string()))?;
    if profile.sid.as_string() != sid {
        return Err(HostError::JournalInconsistent {
            profile: prepared.profile_name,
            reason: "created profile SID differs from its derived SID".to_owned(),
        });
    }
    for root in &manifest.roots {
        paths::validate_journal_root(&root.path)?;
        acl::grant_tree(&root.path, &sid, root.access)?;
    }

    // Reconcile/revoke must not overlap the policy mutation above. The
    // profile mutex stays held through child lifetime; the global state mutex
    // can be released now so unrelated profiles may launch concurrently.
    drop(state_lock);
    let child = launch::spawn(launch::LaunchRequest {
        profile_sid: sid,
        network: prepared.network,
        executable: prepared.executable,
        arguments: prepared.arguments,
        cwd: prepared.cwd,
        memory_bytes: prepared.memory_bytes,
        max_processes: prepared.max_processes,
    })?;
    let exit_code = child.bridge_and_wait()?;
    drop(profile_lock);
    Ok(exit_code)
}

fn revoke(args: ProfileArgs) -> Result<()> {
    paths::validate_profile_name(&args.profile_name)?;
    let state_dir = paths::prepare_state_dir(&args.state_dir)?;
    let _state_lock =
        mutex::NamedMutex::acquire(&format!("state-{}", journal::state_key(&state_dir)))?;
    let _profile_lock = mutex::NamedMutex::acquire(&format!(
        "profile-{}",
        journal::profile_key(&args.profile_name)
    ))?;
    revoke_profile(&state_dir, &args.profile_name, None)
}

fn reconcile(args: ReconcileArgs) -> Result<()> {
    let state_dir = paths::prepare_state_dir(&args.state_dir)?;
    let _state_lock =
        mutex::NamedMutex::acquire(&format!("state-{}", journal::state_key(&state_dir)))?;
    let active = args
        .active_profile_name
        .into_iter()
        .map(|name| {
            paths::validate_profile_name(&name)?;
            Ok(name)
        })
        .collect::<Result<HashSet<_>>>()?;

    for (path, manifest) in journal::load_all(&state_dir)? {
        let profile_name = manifest.profile_name.clone();
        paths::validate_profile_name(&profile_name)?;
        if active.contains(&manifest.profile_name) {
            let _profile_lock = mutex::NamedMutex::acquire(&format!(
                "profile-{}",
                journal::profile_key(&profile_name)
            ))?;
            repair_profile(&state_dir, path, manifest)?;
            continue;
        }
        let _profile_lock = mutex::NamedMutex::acquire(&format!(
            "profile-{}",
            journal::profile_key(&profile_name)
        ))?;
        revoke_profile(&state_dir, &profile_name, Some((path, manifest)))?;
    }
    Ok(())
}

fn repair_profile(state_dir: &Path, path: PathBuf, manifest: ProfileJournal) -> Result<()> {
    let profile_name = manifest.profile_name.clone();
    let sid = derive_sid_from_name(&profile_name)
        .map_err(|error| HostError::AppContainer(error.to_string()))?
        .as_string()
        .to_owned();
    manifest.validate(&profile_name, &sid)?;
    if path != journal::journal_path(state_dir, &profile_name) {
        return Err(HostError::JournalInconsistent {
            profile: profile_name,
            reason: "journal filename does not match profile name".to_owned(),
        });
    }
    let profile = AppContainerProfile::ensure(
        &manifest.profile_name,
        &manifest.profile_name,
        Some("ChatWaifu Runtime Skill"),
    )
    .map_err(|error| HostError::AppContainer(error.to_string()))?;
    if profile.sid.as_string() != sid {
        return Err(HostError::JournalInconsistent {
            profile: manifest.profile_name,
            reason: "reconciled profile SID differs from its derived SID".to_owned(),
        });
    }
    for root in &manifest.roots {
        paths::validate_journal_root(&root.path)?;
        acl::grant_tree(&root.path, &sid, root.access)?;
    }
    Ok(())
}

fn revoke_profile(
    state_dir: &Path,
    profile_name: &str,
    loaded: Option<(PathBuf, ProfileJournal)>,
) -> Result<()> {
    let derived_sid = derive_sid_from_name(profile_name)
        .map_err(|error| HostError::AppContainer(error.to_string()))?;
    let sid = derived_sid.as_string().to_owned();
    let manifest_path = journal::journal_path(state_dir, profile_name);
    let loaded = match loaded {
        Some(value) => Some(value),
        None => journal::load(&manifest_path)?.map(|manifest| (manifest_path.clone(), manifest)),
    };

    if let Some((path, manifest)) = loaded {
        manifest.validate(profile_name, &sid)?;
        if path != journal::journal_path(state_dir, profile_name) {
            return Err(HostError::JournalInconsistent {
                profile: profile_name.to_owned(),
                reason: "journal filename does not match profile name".to_owned(),
            });
        }
        for root in &manifest.roots {
            if root.path.exists() {
                paths::validate_journal_root(&root.path)?;
                acl::revoke_tree(&root.path, &sid, root.original_dacl_control)?;
            }
        }
        delete_profile_idempotent(profile_name)?;
        fs::remove_file(path)?;
    } else {
        // A no-journal revoke remains useful for old launcher versions and
        // manual cleanup. Current `run` always fsyncs its journal first.
        delete_profile_idempotent(profile_name)?;
    }
    Ok(())
}

fn delete_profile_idempotent(profile_name: &str) -> Result<()> {
    use std::ffi::OsStr;
    use std::os::windows::ffi::OsStrExt;

    #[link(name = "Userenv")]
    unsafe extern "system" {
        fn DeleteAppContainerProfile(name: *const u16) -> i32;
    }

    let name: Vec<u16> = OsStr::new(profile_name)
        .encode_wide()
        .chain(Some(0))
        .collect();
    // SAFETY: `name` is a live, NUL-terminated UTF-16 profile moniker.
    let result = unsafe { DeleteAppContainerProfile(name.as_ptr()) };
    // S_OK, HRESULT_FROM_WIN32(ERROR_FILE_NOT_FOUND), and
    // HRESULT_FROM_WIN32(ERROR_NOT_FOUND) are all idempotent success.
    if result == 0 || result as u32 == 0x8007_0002 || result as u32 == 0x8007_0490 {
        return Ok(());
    }
    Err(HostError::AppContainer(format!(
        "DeleteAppContainerProfile failed: 0x{:08X}",
        result as u32
    )))
}
