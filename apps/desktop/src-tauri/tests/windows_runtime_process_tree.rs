#![cfg(target_os = "windows")]

use std::io::{BufRead, BufReader};
use std::os::windows::process::CommandExt;
use std::process::{Child, Command, Stdio};
use std::sync::mpsc;
use std::time::Duration;

use windows_sys::Win32::Foundation::{CloseHandle, WAIT_OBJECT_0};
use windows_sys::Win32::System::Threading::{OpenProcess, WaitForSingleObject};

const CREATE_NO_WINDOW: u32 = 0x0800_0000;
const SYNCHRONIZE: u32 = 0x0010_0000;
const DESCENDANT_SCRIPT: &str = concat!(
    "$child = Start-Process -FilePath $env:ComSpec ",
    "-ArgumentList '/d /q /c ping.exe -t 127.0.0.1' ",
    "-PassThru -WindowStyle Hidden; ",
    "[Console]::Out.WriteLine($child.Id); ",
    "[Console]::Out.Flush(); ",
    "Wait-Process -Id $child.Id"
);
const ROOT_EXIT_SCRIPT: &str = concat!(
    "$child = Start-Process -FilePath $env:ComSpec ",
    "-ArgumentList '/d /q /c ping.exe -t 127.0.0.1' ",
    "-PassThru -WindowStyle Hidden; ",
    "[Console]::Out.WriteLine($child.Id); ",
    "[Console]::Out.Flush()"
);

struct ChildGuard(Child);

impl Drop for ChildGuard {
    fn drop(&mut self) {
        let _ = self.0.kill();
        let _ = self.0.wait();
    }
}

#[test]
fn killing_runtime_supervisor_ends_descendants_but_not_unrelated_processes() {
    let mut unrelated = long_lived_process();
    let (mut supervisor, descendant_pid) = spawn_supervisor(std::process::id());

    supervisor.0.kill().expect("terminate runtime supervisor");
    let _ = supervisor.0.wait().expect("wait for runtime supervisor");
    wait_for_process_exit(descendant_pid, Duration::from_secs(5));

    assert_eq!(
        unrelated.0.try_wait().expect("query unrelated process"),
        None,
        "terminating the Runtime Job must not kill an unrelated process"
    );
    unrelated.0.kill().expect("clean up unrelated process");
    let _ = unrelated.0.wait().expect("wait for unrelated process");
}

#[test]
fn force_exiting_desktop_parent_ends_supervisor_and_runtime_tree() {
    let mut desktop_parent = long_lived_process();
    let (mut supervisor, descendant_pid) = spawn_supervisor(desktop_parent.0.id());
    let supervisor_pid = supervisor.0.id();

    desktop_parent.0.kill().expect("force-exit desktop parent");
    let _ = desktop_parent.0.wait().expect("wait for desktop parent");
    wait_for_process_exit(supervisor_pid, Duration::from_secs(5));
    let _ = supervisor.0.wait().expect("reap runtime supervisor");
    wait_for_process_exit(descendant_pid, Duration::from_secs(5));
}

#[test]
fn runtime_root_exit_also_ends_lingering_descendants() {
    let (mut supervisor, descendant_pid) =
        spawn_supervisor_with_script(std::process::id(), ROOT_EXIT_SCRIPT);
    let supervisor_pid = supervisor.0.id();

    wait_for_process_exit(supervisor_pid, Duration::from_secs(5));
    let status = supervisor.0.wait().expect("reap runtime supervisor");
    assert!(status.success(), "runtime supervisor returned {status}");
    wait_for_process_exit(descendant_pid, Duration::from_secs(5));
}

fn long_lived_process() -> ChildGuard {
    ChildGuard(
        Command::new("ping.exe")
            .args(["-t", "127.0.0.1"])
            .stdin(Stdio::null())
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .creation_flags(CREATE_NO_WINDOW)
            .spawn()
            .expect("spawn long-lived process"),
    )
}

fn spawn_supervisor(desktop_parent_pid: u32) -> (ChildGuard, u32) {
    spawn_supervisor_with_script(desktop_parent_pid, DESCENDANT_SCRIPT)
}

fn spawn_supervisor_with_script(
    desktop_parent_pid: u32,
    script: &'static str,
) -> (ChildGuard, u32) {
    let mut supervisor = ChildGuard(
        Command::new(env!("CARGO_BIN_EXE_chatwaifu-desktop-host"))
            .arg("--chatwaifu-runtime-supervisor")
            .arg("--")
            .arg("powershell.exe")
            .args(["-NoProfile", "-NonInteractive", "-Command", script])
            .env(
                "CHATWAIFU_DESKTOP_PARENT_PID",
                desktop_parent_pid.to_string(),
            )
            .stdin(Stdio::null())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .creation_flags(CREATE_NO_WINDOW)
            .spawn()
            .expect("spawn runtime supervisor"),
    );
    let stdout = supervisor
        .0
        .stdout
        .take()
        .expect("runtime supervisor stdout is piped");
    let (sender, receiver) = mpsc::channel();
    std::thread::spawn(move || {
        let mut line = String::new();
        let result = BufReader::new(stdout)
            .read_line(&mut line)
            .map(|_| line.trim().to_owned());
        let _ = sender.send(result);
    });
    let descendant_pid = receiver
        .recv_timeout(Duration::from_secs(15))
        .expect("supervised process did not report its descendant")
        .expect("read supervised descendant pid")
        .parse::<u32>()
        .expect("supervised descendant pid is numeric");
    (supervisor, descendant_pid)
}

fn wait_for_process_exit(process_id: u32, timeout: Duration) {
    // SAFETY: OpenProcess returns a process-local handle which is closed below.
    let handle = unsafe { OpenProcess(SYNCHRONIZE, 0, process_id) };
    if handle.is_null() {
        return;
    }
    // This kernel wait is event-driven; the timeout is only a test failure bound.
    let result = unsafe {
        WaitForSingleObject(
            handle,
            u32::try_from(timeout.as_millis()).unwrap_or(u32::MAX),
        )
    };
    unsafe {
        let _ = CloseHandle(handle);
    }
    assert_eq!(
        result, WAIT_OBJECT_0,
        "Runtime descendant PID {process_id} survived supervisor termination"
    );
}
