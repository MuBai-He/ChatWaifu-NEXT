use crate::runtime_health::{
    RuntimeBootstrap, RuntimeLifecycleState, RuntimeStatus, parse_bootstrap_line,
};
use std::{
    io::{BufRead, BufReader},
    path::PathBuf,
    process::{Child, Command, Stdio},
    sync::{
        Arc, Mutex, MutexGuard,
        mpsc::{self, Receiver, RecvTimeoutError, Sender},
    },
    thread::{self, JoinHandle},
    time::{Duration, Instant},
};
use tauri::{AppHandle, Emitter};

pub const RUNTIME_STATUS_CHANGED_EVENT: &str = "desktop-runtime-status-changed";
const MAX_AUTOMATIC_RESTARTS: u32 = 5;
const STARTUP_TIMEOUT: Duration = Duration::from_secs(120);

#[derive(Clone, Copy, Debug)]
enum SupervisorCommand {
    Start,
    Stop,
    Restart,
    Shutdown,
}

#[derive(Default)]
pub struct RuntimeHost {
    control: Mutex<Option<Sender<SupervisorCommand>>>,
    join: Mutex<Option<JoinHandle<()>>>,
    status: Arc<Mutex<RuntimeStatus>>,
}

impl RuntimeHost {
    pub fn ensure_started(&self, app: AppHandle) -> Result<RuntimeStatus, String> {
        let mut control = lock(&self.control, "Runtime supervisor control")?;
        let needs_thread = lock(&self.join, "Runtime supervisor thread")?
            .as_ref()
            .is_none_or(JoinHandle::is_finished);
        if needs_thread {
            update_status(
                &app,
                &self.status,
                RuntimeStatus {
                    state: RuntimeLifecycleState::Starting,
                    detail: Some("正在启动本地 Runtime 与语音服务".to_owned()),
                    ..RuntimeStatus::default()
                },
            );
            let (sender, receiver) = mpsc::channel();
            let status = Arc::clone(&self.status);
            let supervisor_app = app.clone();
            let join = thread::Builder::new()
                .name("chatwaifu-runtime-supervisor".to_owned())
                .spawn(move || supervise(supervisor_app, receiver, status))
                .map_err(|error| {
                    update_status(&app, &self.status, RuntimeStatus::default());
                    format!("failed to start Runtime supervisor: {error}")
                })?;
            *lock(&self.join, "Runtime supervisor thread")? = Some(join);
            *control = Some(sender);
        } else if let Some(sender) = control.as_ref() {
            let current = self.status()?;
            if should_request_start(&current.state) {
                sender
                    .send(SupervisorCommand::Start)
                    .map_err(|_| "Runtime supervisor is unavailable".to_owned())?;
            }
        }
        self.status()
    }

    pub fn stop(&self) -> Result<RuntimeStatus, String> {
        self.send(SupervisorCommand::Stop)?;
        self.status()
    }

    pub fn restart(&self) -> Result<RuntimeStatus, String> {
        self.send(SupervisorCommand::Restart)?;
        self.status()
    }

    pub fn status(&self) -> Result<RuntimeStatus, String> {
        Ok(lock(&self.status, "Runtime status")?.clone())
    }

    pub fn shutdown_and_wait(&self) {
        let _ = self.send(SupervisorCommand::Shutdown);
        let join = self.join.lock().ok().and_then(|mut value| value.take());
        if let Some(join) = join {
            let _ = join.join();
        }
        if let Ok(mut control) = self.control.lock() {
            *control = None;
        }
    }

    fn send(&self, command: SupervisorCommand) -> Result<(), String> {
        let control = lock(&self.control, "Runtime supervisor control")?;
        let sender = control
            .as_ref()
            .ok_or_else(|| "Runtime supervisor has not started".to_owned())?;
        sender
            .send(command)
            .map_err(|_| "Runtime supervisor is unavailable".to_owned())
    }
}

fn should_request_start(state: &RuntimeLifecycleState) -> bool {
    matches!(
        state,
        RuntimeLifecycleState::Stopped | RuntimeLifecycleState::CircuitOpen
    )
}

fn supervise(
    app: AppHandle,
    commands: Receiver<SupervisorCommand>,
    status: Arc<Mutex<RuntimeStatus>>,
) {
    let mut desired_running = true;
    let mut restart_count = 0;
    loop {
        if !desired_running {
            match commands.recv() {
                Ok(SupervisorCommand::Start | SupervisorCommand::Restart) => {
                    desired_running = true;
                    restart_count = 0;
                }
                Ok(SupervisorCommand::Stop) => continue,
                Ok(SupervisorCommand::Shutdown) | Err(_) => return,
            }
        }

        update_status(
            &app,
            &status,
            RuntimeStatus {
                state: RuntimeLifecycleState::Starting,
                restart_count,
                detail: Some("正在启动本地 Runtime 与语音服务".to_owned()),
                ..RuntimeStatus::default()
            },
        );
        let mut child = match spawn_service_stack() {
            Ok(child) => child,
            Err(error) => {
                if !recover_or_wait(
                    &app,
                    &commands,
                    &status,
                    &mut desired_running,
                    &mut restart_count,
                    error,
                ) {
                    return;
                }
                continue;
            }
        };
        let line_receiver = child.stdout.take().map(spawn_stdout_reader);
        let started_at = Instant::now();
        let mut ready = false;
        let mut requested_restart = false;
        let mut shutdown = false;
        let mut failure: Option<String> = None;

        loop {
            match commands.try_recv() {
                Ok(SupervisorCommand::Stop) => {
                    desired_running = false;
                    terminate_child(&mut child);
                    break;
                }
                Ok(SupervisorCommand::Restart | SupervisorCommand::Start) => {
                    desired_running = true;
                    requested_restart = true;
                    restart_count = 0;
                    terminate_child(&mut child);
                    break;
                }
                Ok(SupervisorCommand::Shutdown) | Err(mpsc::TryRecvError::Disconnected) => {
                    shutdown = true;
                    terminate_child(&mut child);
                    break;
                }
                Err(mpsc::TryRecvError::Empty) => {}
            }

            if let Some(receiver) = line_receiver.as_ref() {
                while let Ok(line) = receiver.try_recv() {
                    match parse_bootstrap_line(&line) {
                        Ok(Some(bootstrap)) => {
                            ready = true;
                            publish_ready(&app, &status, restart_count, bootstrap);
                        }
                        Ok(None) => eprintln!("[desktop-services] {line}"),
                        Err(error) => {
                            failure = Some(error);
                            terminate_child(&mut child);
                            break;
                        }
                    }
                }
            }
            if failure.is_some() {
                break;
            }
            match child.try_wait() {
                Ok(Some(exit)) => {
                    failure = Some(format!("本地服务意外退出：{exit}"));
                    break;
                }
                Ok(None) => {}
                Err(error) => {
                    failure = Some(format!("无法读取本地服务状态：{error}"));
                    terminate_child(&mut child);
                    break;
                }
            }
            if !ready && started_at.elapsed() >= STARTUP_TIMEOUT {
                failure = Some("本地服务启动超时".to_owned());
                terminate_child(&mut child);
                break;
            }
            match commands.recv_timeout(Duration::from_millis(100)) {
                Ok(command) => match command {
                    SupervisorCommand::Stop => {
                        desired_running = false;
                        terminate_child(&mut child);
                        break;
                    }
                    SupervisorCommand::Start | SupervisorCommand::Restart => {
                        requested_restart = true;
                        restart_count = 0;
                        terminate_child(&mut child);
                        break;
                    }
                    SupervisorCommand::Shutdown => {
                        shutdown = true;
                        terminate_child(&mut child);
                        break;
                    }
                },
                Err(RecvTimeoutError::Timeout) => {}
                Err(RecvTimeoutError::Disconnected) => {
                    shutdown = true;
                    terminate_child(&mut child);
                    break;
                }
            }
        }

        if shutdown {
            update_status(&app, &status, RuntimeStatus::default());
            return;
        }
        if !desired_running {
            update_status(&app, &status, RuntimeStatus::default());
            continue;
        }
        if requested_restart {
            continue;
        }
        if let Some(error) = failure
            && !recover_or_wait(
                &app,
                &commands,
                &status,
                &mut desired_running,
                &mut restart_count,
                error,
            )
        {
            return;
        }
    }
}

fn recover_or_wait(
    app: &AppHandle,
    commands: &Receiver<SupervisorCommand>,
    status: &Arc<Mutex<RuntimeStatus>>,
    desired_running: &mut bool,
    restart_count: &mut u32,
    error: String,
) -> bool {
    *restart_count += 1;
    let Some(backoff) = restart_backoff(*restart_count) else {
        update_status(
            app,
            status,
            RuntimeStatus {
                state: RuntimeLifecycleState::CircuitOpen,
                restart_count: *restart_count,
                detail: Some(format!("{error}；自动恢复已暂停，请手动重启")),
                ..RuntimeStatus::default()
            },
        );
        *desired_running = false;
        return true;
    };
    update_status(
        app,
        status,
        RuntimeStatus {
            state: RuntimeLifecycleState::Backoff,
            restart_count: *restart_count,
            detail: Some(format!("{error}；将在 {} 秒后自动恢复", backoff.as_secs())),
            ..RuntimeStatus::default()
        },
    );
    match commands.recv_timeout(backoff) {
        Ok(SupervisorCommand::Shutdown) | Err(RecvTimeoutError::Disconnected) => false,
        Ok(SupervisorCommand::Stop) => {
            *desired_running = false;
            true
        }
        Ok(SupervisorCommand::Start | SupervisorCommand::Restart) => {
            *restart_count = 0;
            *desired_running = true;
            true
        }
        Err(RecvTimeoutError::Timeout) => true,
    }
}

fn restart_backoff(restart_count: u32) -> Option<Duration> {
    if restart_count > MAX_AUTOMATIC_RESTARTS {
        return None;
    }
    Some(Duration::from_secs(
        1_u64 << restart_count.saturating_sub(1).min(4),
    ))
}

fn publish_ready(
    app: &AppHandle,
    status: &Arc<Mutex<RuntimeStatus>>,
    restart_count: u32,
    bootstrap: RuntimeBootstrap,
) {
    update_status(
        app,
        status,
        RuntimeStatus {
            state: RuntimeLifecycleState::Ready,
            runtime_url: Some(bootstrap.runtime_url),
            pid: Some(bootstrap.pid),
            workers: bootstrap.workers,
            restart_count,
            detail: None,
        },
    );
}

fn update_status(app: &AppHandle, status: &Arc<Mutex<RuntimeStatus>>, next: RuntimeStatus) {
    if let Ok(mut current) = status.lock() {
        *current = next.clone();
    }
    let _ = app.emit(RUNTIME_STATUS_CHANGED_EVENT, next);
}

fn spawn_service_stack() -> Result<Child, String> {
    let mut command = if let Ok(executable) = std::env::var("CHATWAIFU_DESKTOP_SERVICE_EXECUTABLE")
    {
        Command::new(executable)
    } else {
        let root = workspace_root();
        let mut command = Command::new("uv");
        command
            .arg("run")
            .arg("python")
            .arg(root.join("tools/run_desktop_services.py"))
            .current_dir(root);
        command
    };
    command
        .env(
            "CHATWAIFU_DESKTOP_PARENT_PID",
            std::process::id().to_string(),
        )
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::inherit());
    #[cfg(unix)]
    {
        use std::os::unix::process::CommandExt;
        command.process_group(0);
    }
    command
        .spawn()
        .map_err(|error| format!("无法启动本地服务：{error}"))
}

fn workspace_root() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("../../..")
        .canonicalize()
        .unwrap_or_else(|_| PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../.."))
}

fn spawn_stdout_reader(stdout: impl std::io::Read + Send + 'static) -> Receiver<String> {
    let (sender, receiver) = mpsc::channel();
    thread::spawn(move || {
        for line in BufReader::new(stdout).lines() {
            let Ok(line) = line else { break };
            if sender.send(line).is_err() {
                break;
            }
        }
    });
    receiver
}

fn terminate_child(child: &mut Child) {
    if child.try_wait().ok().flatten().is_some() {
        return;
    }
    #[cfg(unix)]
    unsafe {
        libc::kill(-(child.id() as i32), libc::SIGTERM);
    }
    #[cfg(not(unix))]
    {
        let _ = child.kill();
    }
    let deadline = Instant::now() + Duration::from_secs(5);
    while Instant::now() < deadline {
        if child.try_wait().ok().flatten().is_some() {
            return;
        }
        thread::sleep(Duration::from_millis(50));
    }
    let _ = child.kill();
    let _ = child.wait();
}

fn lock<'a, T>(value: &'a Mutex<T>, label: &str) -> Result<MutexGuard<'a, T>, String> {
    value
        .lock()
        .map_err(|_| format!("{label} lock was poisoned"))
}

#[cfg(test)]
mod tests {
    use super::{MAX_AUTOMATIC_RESTARTS, restart_backoff, should_request_start};
    use crate::runtime_health::RuntimeLifecycleState;
    use std::time::Duration;

    #[test]
    fn restart_backoff_is_bounded_and_opens_the_circuit() {
        assert_eq!(restart_backoff(1), Some(Duration::from_secs(1)));
        assert_eq!(restart_backoff(2), Some(Duration::from_secs(2)));
        assert_eq!(restart_backoff(5), Some(Duration::from_secs(16)));
        assert_eq!(restart_backoff(MAX_AUTOMATIC_RESTARTS + 1), None);
    }

    #[test]
    fn ensure_started_only_wakes_an_inactive_supervisor() {
        assert!(should_request_start(&RuntimeLifecycleState::Stopped));
        assert!(should_request_start(&RuntimeLifecycleState::CircuitOpen));
        assert!(!should_request_start(&RuntimeLifecycleState::Starting));
        assert!(!should_request_start(&RuntimeLifecycleState::Ready));
        assert!(!should_request_start(&RuntimeLifecycleState::Backoff));
    }
}
