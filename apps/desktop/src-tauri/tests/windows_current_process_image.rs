#![cfg(target_os = "windows")]

use chatwaifu_desktop_host::windows_current_process_image_path;
use std::ffi::OsString;
use std::fs;
use std::os::windows::ffi::{OsStrExt, OsStringExt};
use std::path::{Path, PathBuf};
use std::process::Command;
use std::time::{SystemTime, UNIX_EPOCH};

const CHILD_PROBE_ENV: &str = "CHATWAIFU_WINDOWS_IMAGE_PATH_CHILD_PROBE";
const CHILD_PROBE_MARKER: &str = "CHATWAIFU_PHYSICAL_IMAGE=";
const CHILD_PROBE_ERROR_MARKER: &str = "CHATWAIFU_PHYSICAL_IMAGE_ERROR=";
const CURRENT_IMAGE_TEST_NAME: &str =
    "query_full_process_image_name_returns_the_real_test_process_image";

#[test]
fn query_full_process_image_name_returns_the_real_test_process_image() {
    let standard = std::env::current_exe().expect("std::env::current_exe should locate the test");

    if std::env::var_os(CHILD_PROBE_ENV).is_some() {
        println!("CHATWAIFU_STD_CURRENT_IMAGE={}", standard.display());
        match windows_current_process_image_path() {
            Ok(queried) => println!("{CHILD_PROBE_MARKER}{}", queried.display()),
            Err(error) => println!("{CHILD_PROBE_ERROR_MARKER}{error}"),
        }
        return;
    }

    let queried = windows_current_process_image_path()
        .expect("QueryFullProcessImageNameW should locate the current process image");

    assert!(queried.is_absolute(), "queried path: {}", queried.display());
    assert!(queried.is_file(), "queried path: {}", queried.display());
    assert_eq!(
        queried
            .canonicalize()
            .expect("canonicalize queried image path"),
        standard
            .canonicalize()
            .expect("canonicalize standard image path")
    );
}

#[test]
fn copied_local_app_data_child_resolves_back_to_its_physical_image() {
    let local_app_data = PathBuf::from(
        std::env::var_os("LOCALAPPDATA").expect("LOCALAPPDATA is required on Windows"),
    );
    assert!(local_app_data.is_absolute());
    let nonce = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .expect("system clock should be after Unix epoch")
        .as_nanos();
    let probe_directory =
        local_app_data.join(format!("ChatWaifuPathProbe-{}-{nonce}", std::process::id()));
    let _cleanup = PhysicalDirectoryCleanup::new(&local_app_data, &probe_directory);
    let physical_directory = verbatim_path(&probe_directory);
    fs::create_dir_all(&physical_directory).expect("create physical LocalAppData probe directory");

    let source = std::env::current_exe().expect("locate integration test executable");
    let destination = probe_directory.join("probe.exe");
    fs::copy(&source, verbatim_path(&destination))
        .expect("copy integration test to physical LocalAppData");

    let output = Command::new(&destination)
        .env(CHILD_PROBE_ENV, "1")
        .args(["--exact", CURRENT_IMAGE_TEST_NAME, "--nocapture"])
        .output()
        .expect("launch LocalAppData path probe child");
    assert!(
        output.status.success(),
        "probe failed with {}\nstdout:\n{}\nstderr:\n{}",
        output.status,
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr)
    );
    let stdout = String::from_utf8(output.stdout).expect("probe output should be UTF-8");
    println!("LocalAppData child probe:\n{stdout}");
    let resolved = stdout
        .lines()
        .find_map(|line| line.strip_prefix(CHILD_PROBE_MARKER))
        .map(PathBuf::from);
    let error = stdout
        .lines()
        .find_map(|line| line.strip_prefix(CHILD_PROBE_ERROR_MARKER));
    match (resolved, error) {
        (Some(resolved), None) => {
            assert_eq!(resolved, destination, "probe output:\n{stdout}");
            assert!(
                !resolved
                    .to_string_lossy()
                    .contains("\\Packages\\OpenAI.Codex_"),
                "probe output:\n{stdout}"
            );
        }
        (None, Some(error)) => {
            // A packaged parent may redirect this test's attempted physical
            // staging write itself. In that case there is no real candidate,
            // and the resolver must fail closed instead of blessing LocalCache.
            assert!(
                error.contains("无法解析为物理文件"),
                "probe output:\n{stdout}"
            );
            assert!(
                error.contains(
                    "\\Packages\\OpenAI.Codex_2p2nqsd0c76g0\\LocalCache\\Local\\ChatWaifuPathProbe-"
                ),
                "probe output:\n{stdout}"
            );
        }
        _ => panic!("ambiguous probe result:\n{stdout}"),
    }
}

struct PhysicalDirectoryCleanup {
    local_app_data: PathBuf,
    directory: PathBuf,
}

impl PhysicalDirectoryCleanup {
    fn new(local_app_data: &Path, directory: &Path) -> Self {
        let relative = directory
            .strip_prefix(local_app_data)
            .expect("probe cleanup must stay under LocalAppData");
        assert_eq!(relative.components().count(), 1);
        assert!(
            relative
                .to_string_lossy()
                .starts_with("ChatWaifuPathProbe-")
        );
        Self {
            local_app_data: local_app_data.to_path_buf(),
            directory: directory.to_path_buf(),
        }
    }
}

impl Drop for PhysicalDirectoryCleanup {
    fn drop(&mut self) {
        if self.directory.strip_prefix(&self.local_app_data).is_ok()
            && self
                .directory
                .file_name()
                .is_some_and(|name| name.to_string_lossy().starts_with("ChatWaifuPathProbe-"))
        {
            let _ = fs::remove_dir_all(verbatim_path(&self.directory));
        }
    }
}

fn verbatim_path(path: &Path) -> PathBuf {
    let units = path.as_os_str().encode_wide().collect::<Vec<_>>();
    assert!(
        units.len() >= 3
            && units[0] <= u16::from(u8::MAX)
            && units[1] == u16::from(b':')
            && (units[2] == u16::from(b'\\') || units[2] == u16::from(b'/')),
        "expected an absolute drive path: {}",
        path.display()
    );
    let mut result = "\\\\?\\".encode_utf16().collect::<Vec<_>>();
    result.extend(units);
    PathBuf::from(OsString::from_wide(&result))
}
