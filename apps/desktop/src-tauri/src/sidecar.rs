use crate::runtime_health::{
    RuntimeBootstrap, RuntimeLifecycleState, RuntimeStatus, parse_bootstrap_line,
};
use std::{
    ffi::{OsStr, OsString},
    fs::{self, OpenOptions},
    io::{BufRead, BufReader},
    path::{Path, PathBuf},
    process::{Child, Command, Stdio},
    sync::{
        Arc, Mutex, MutexGuard,
        mpsc::{self, Receiver, RecvTimeoutError, Sender},
    },
    thread::{self, JoinHandle},
    time::{Duration, Instant},
};
use tauri::{AppHandle, Emitter, Manager};

pub const RUNTIME_STATUS_CHANGED_EVENT: &str = "desktop-runtime-status-changed";
const MAX_AUTOMATIC_RESTARTS: u32 = 5;
// The frozen Runtime starts selected STT/TTS packs concurrently within 300s,
// then gives its HTTP server 120s. Keep an explicit supervisor grace period so
// a valid first CUDA load is not killed and counted as a restart storm.
const WORKER_PACK_STARTUP_BUDGET_SECONDS: u64 = 300;
const RUNTIME_SERVER_STARTUP_BUDGET_SECONDS: u64 = 120;
const STARTUP_SUPERVISOR_GRACE_SECONDS: u64 = 30;
const STARTUP_TIMEOUT: Duration = Duration::from_secs(
    WORKER_PACK_STARTUP_BUDGET_SECONDS
        + RUNTIME_SERVER_STARTUP_BUDGET_SECONDS
        + STARTUP_SUPERVISOR_GRACE_SECONDS,
);
const SIDECAR_LOG_MAX_BYTES: u64 = 5 * 1024 * 1024;
const RUNTIME_SUPERVISOR_ARGUMENT: &str = "--chatwaifu-runtime-supervisor";

#[derive(Clone, Debug, PartialEq, Eq)]
struct DesktopUserRoots {
    config: PathBuf,
    data: PathBuf,
    logs: PathBuf,
}

pub fn runtime_supervisor_exit_code() -> Option<i32> {
    runtime_supervisor_exit_code_from(std::env::args_os().skip(1))
}

fn runtime_supervisor_exit_code_from(mut arguments: impl Iterator<Item = OsString>) -> Option<i32> {
    if arguments.next().as_deref() != Some(OsStr::new(RUNTIME_SUPERVISOR_ARGUMENT)) {
        return None;
    }
    if arguments.next().as_deref() != Some(OsStr::new("--")) {
        eprintln!("runtime supervisor requires `--` before the service command");
        return Some(2);
    }
    let Some(executable) = arguments.next() else {
        eprintln!("runtime supervisor requires a service executable");
        return Some(2);
    };
    let service_arguments = arguments.collect::<Vec<_>>();
    #[cfg(target_os = "windows")]
    {
        match windows_process_tree::run(&executable, &service_arguments) {
            Ok(exit_code) => Some(exit_code),
            Err(error) => {
                eprintln!("runtime supervisor failed: {error}");
                Some(1)
            }
        }
    }
    #[cfg(not(target_os = "windows"))]
    {
        let _ = (executable, service_arguments);
        eprintln!("runtime supervisor mode is only available on Windows");
        Some(1)
    }
}

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
        let mut child = match spawn_service_stack(&app) {
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

fn spawn_service_stack(app: &AppHandle) -> Result<Child, String> {
    let (executable, arguments, current_dir) =
        if let Some(executable) = std::env::var_os("CHATWAIFU_DESKTOP_SERVICE_EXECUTABLE") {
            (executable, Vec::new(), None)
        } else if cfg!(not(debug_assertions)) {
            let resource_dir = app
                .path()
                .resource_dir()
                .map_err(|error| format!("无法定位打包 Runtime：{error}"))?;
            let component_root = packaged_component_root_for_process(&resource_dir)?;
            let executable = packaged_runtime_executable(&component_root);
            if !executable.is_file() {
                return Err(format!("打包 Runtime 不存在：{}", executable.display()));
            }
            (executable.into_os_string(), Vec::new(), None)
        } else {
            let root = workspace_root();
            (
                OsString::from("uv"),
                vec![
                    OsString::from("run"),
                    OsString::from("python"),
                    root.join("tools/run_desktop_services.py").into_os_string(),
                ],
                Some(root),
            )
        };
    #[cfg(target_os = "windows")]
    let mut command = {
        let current_executable = runtime_supervisor_executable()?;
        let mut command = Command::new(current_executable);
        command
            .arg(RUNTIME_SUPERVISOR_ARGUMENT)
            .arg("--")
            .arg(executable)
            .args(arguments);
        command
    };
    #[cfg(not(target_os = "windows"))]
    let mut command = {
        let mut command = Command::new(executable);
        command.args(arguments);
        command
    };
    if let Some(current_dir) = current_dir {
        command.current_dir(current_dir);
    }
    let user_roots = desktop_user_roots(app)?;
    let config_dir = user_roots.config.join("runtime");
    let data_dir = user_roots.data.join("runtime");
    command
        .env("CHATWAIFU_CONFIG_DIR", config_dir)
        .env("CHATWAIFU_DATA_DIR", data_dir);

    #[cfg(target_os = "windows")]
    if std::env::var_os("CHATWAIFU_SECURITY__WINDOWS_APPCONTAINER_LAUNCHER").is_none() {
        let resource_dir = app
            .path()
            .resource_dir()
            .map_err(|error| format!("无法定位 AppContainer helper 资源目录：{error}"))?;
        let component_root = packaged_component_root_for_process(&resource_dir)?;
        let packaged_launcher = packaged_appcontainer_launcher(&component_root);
        let launcher = if packaged_launcher.is_file() {
            Some(packaged_launcher)
        } else if cfg!(debug_assertions) {
            std::env::current_exe()
                .ok()
                .map(|current| adjacent_appcontainer_launcher(&current))
                .filter(|candidate| candidate.is_file())
        } else {
            return Err(format!(
                "打包 AppContainer helper 不存在：{}",
                packaged_launcher.display()
            ));
        };
        if let Some(launcher) = launcher {
            command.env(
                "CHATWAIFU_SECURITY__WINDOWS_APPCONTAINER_LAUNCHER",
                launcher,
            );
        }
    }
    command
        .env(
            "CHATWAIFU_DESKTOP_PARENT_PID",
            std::process::id().to_string(),
        )
        .stdout(Stdio::piped());
    if cfg!(debug_assertions) {
        command.stdin(Stdio::null());
    } else {
        command.stdin(Stdio::piped());
    }
    if cfg!(debug_assertions) {
        command.stderr(Stdio::inherit());
    } else {
        command.stderr(Stdio::from(open_sidecar_log(app)?));
    }
    #[cfg(target_os = "windows")]
    {
        use std::os::windows::process::CommandExt;
        const CREATE_NO_WINDOW: u32 = 0x0800_0000;
        command.creation_flags(CREATE_NO_WINDOW);
    }
    #[cfg(unix)]
    {
        use std::os::unix::process::CommandExt;
        command.process_group(0);
    }
    command
        .spawn()
        .map_err(|error| format!("无法启动本地服务：{error}"))
}

#[cfg(target_os = "windows")]
mod windows_process_tree {
    use std::ffi::{OsStr, OsString};
    use std::os::windows::ffi::OsStrExt;

    use windows_sys::Win32::Foundation::{
        CloseHandle, GetLastError, HANDLE, WAIT_FAILED, WAIT_OBJECT_0,
    };
    use windows_sys::Win32::System::Console::{
        GetStdHandle, STD_ERROR_HANDLE, STD_INPUT_HANDLE, STD_OUTPUT_HANDLE,
    };
    use windows_sys::Win32::System::JobObjects::{
        CreateJobObjectW, JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE, JOBOBJECT_EXTENDED_LIMIT_INFORMATION,
        JobObjectExtendedLimitInformation, SetInformationJobObject, TerminateJobObject,
    };
    use windows_sys::Win32::System::Threading::{
        CREATE_NO_WINDOW, CreateProcessW, DeleteProcThreadAttributeList,
        EXTENDED_STARTUPINFO_PRESENT, GetExitCodeProcess, INFINITE,
        InitializeProcThreadAttributeList, LPPROC_THREAD_ATTRIBUTE_LIST, OpenProcess,
        PROC_THREAD_ATTRIBUTE_JOB_LIST, PROCESS_INFORMATION, STARTF_USESTDHANDLES, STARTUPINFOEXW,
        UpdateProcThreadAttribute, WaitForMultipleObjects, WaitForSingleObject,
    };

    const SYNCHRONIZE: u32 = 0x0010_0000;

    pub(super) fn run(executable: &OsStr, arguments: &[OsString]) -> Result<i32, String> {
        let desktop_parent = DesktopParent::from_environment()?;
        let job = Job::new()?;
        let mut attribute_size = 0usize;
        // SAFETY: the null call is the documented attribute-list size query.
        unsafe {
            let _ =
                InitializeProcThreadAttributeList(std::ptr::null_mut(), 1, 0, &mut attribute_size);
        }
        if attribute_size == 0 {
            return Err(last_api_error("InitializeProcThreadAttributeList(size)"));
        }
        let mut attribute_storage = vec![0u8; attribute_size];
        let attribute_list = attribute_storage.as_mut_ptr().cast();
        // SAFETY: storage has the exact size returned by the query and remains
        // alive until CreateProcessW returns.
        if unsafe { InitializeProcThreadAttributeList(attribute_list, 1, 0, &mut attribute_size) }
            == 0
        {
            return Err(last_api_error("InitializeProcThreadAttributeList"));
        }
        let attribute_guard = AttributeList(attribute_list);
        let job_handle = job.handle;
        // SAFETY: the job handle storage remains alive through CreateProcessW.
        if unsafe {
            UpdateProcThreadAttribute(
                attribute_list,
                0,
                PROC_THREAD_ATTRIBUTE_JOB_LIST as usize,
                (&raw const job_handle).cast(),
                std::mem::size_of::<HANDLE>(),
                std::ptr::null_mut(),
                std::ptr::null(),
            )
        } == 0
        {
            return Err(last_api_error("UpdateProcThreadAttribute(JOB_LIST)"));
        }

        let mut startup: STARTUPINFOEXW = unsafe { std::mem::zeroed() };
        startup.StartupInfo.cb = std::mem::size_of::<STARTUPINFOEXW>() as u32;
        startup.StartupInfo.dwFlags = STARTF_USESTDHANDLES;
        // SAFETY: these pseudo constants query handles owned by this process.
        startup.StartupInfo.hStdInput = unsafe { GetStdHandle(STD_INPUT_HANDLE) };
        startup.StartupInfo.hStdOutput = unsafe { GetStdHandle(STD_OUTPUT_HANDLE) };
        startup.StartupInfo.hStdError = unsafe { GetStdHandle(STD_ERROR_HANDLE) };
        startup.lpAttributeList = attribute_list;

        let mut command_line = build_command_line(executable, arguments);
        let mut process_information: PROCESS_INFORMATION = unsafe { std::mem::zeroed() };
        // PROC_THREAD_ATTRIBUTE_JOB_LIST assigns the root to the Job before
        // its first instruction. Every descendant is therefore contained,
        // including uv/Python launch layers and Runtime workers.
        let created = unsafe {
            CreateProcessW(
                std::ptr::null(),
                command_line.as_mut_ptr(),
                std::ptr::null(),
                std::ptr::null(),
                1,
                EXTENDED_STARTUPINFO_PRESENT | CREATE_NO_WINDOW,
                std::ptr::null(),
                std::ptr::null(),
                (&raw mut startup).cast(),
                &mut process_information,
            )
        };
        drop(attribute_guard);
        if created == 0 {
            return Err(last_api_error("CreateProcessW(Runtime service)"));
        }
        let process = ProcessHandles(process_information);
        if let Some(parent) = desktop_parent.as_ref() {
            let handles = [process.0.hProcess, parent.handle];
            // SAFETY: both handles remain live through the kernel wait.
            match unsafe {
                WaitForMultipleObjects(handles.len() as u32, handles.as_ptr(), 0, INFINITE)
            } {
                WAIT_OBJECT_0 => {}
                value if value == WAIT_OBJECT_0 + 1 => {
                    // The Tauri host was force-terminated. The hidden wrapper
                    // is still alive, so close the full Job immediately rather
                    // than relying on Python polling or process reparenting.
                    job.terminate()?;
                    // SAFETY: Job termination signals the supervised root.
                    if unsafe { WaitForSingleObject(process.0.hProcess, INFINITE) } != WAIT_OBJECT_0
                    {
                        return Err(last_api_error(
                            "WaitForSingleObject(Runtime service after desktop exit)",
                        ));
                    }
                    return Ok(0);
                }
                WAIT_FAILED => return Err(last_api_error("WaitForMultipleObjects(Runtime)")),
                value => return Err(format!("unexpected Runtime wait result {value}")),
            }
        } else {
            // SAFETY: hProcess is live until ProcessHandles is dropped.
            if unsafe { WaitForSingleObject(process.0.hProcess, INFINITE) } != WAIT_OBJECT_0 {
                return Err(last_api_error("WaitForSingleObject(Runtime service)"));
            }
        }
        let mut exit_code = 1u32;
        // SAFETY: the process has signaled and the output pointer is valid.
        if unsafe { GetExitCodeProcess(process.0.hProcess, &mut exit_code) } == 0 {
            return Err(last_api_error("GetExitCodeProcess(Runtime service)"));
        }
        // The root may exit while a worker still owns inherited pipes or a
        // listening port. End all remaining descendants before returning.
        job.terminate()?;
        Ok(i32::try_from(exit_code).unwrap_or(1))
    }

    struct DesktopParent {
        handle: HANDLE,
    }

    impl DesktopParent {
        fn from_environment() -> Result<Option<Self>, String> {
            let Some(raw_parent_pid) = std::env::var_os("CHATWAIFU_DESKTOP_PARENT_PID") else {
                return Ok(None);
            };
            let parent_pid = raw_parent_pid
                .to_string_lossy()
                .parse::<u32>()
                .map_err(|_| "CHATWAIFU_DESKTOP_PARENT_PID must be an integer".to_owned())?;
            if parent_pid <= 1 || parent_pid == std::process::id() {
                return Err("CHATWAIFU_DESKTOP_PARENT_PID is not a valid supervisor".to_owned());
            }
            // SAFETY: SYNCHRONIZE is read-only and the PID came from the
            // desktop host which spawned this wrapper.
            let handle = unsafe { OpenProcess(SYNCHRONIZE, 0, parent_pid) };
            if handle.is_null() {
                return Err(last_api_error("OpenProcess(desktop parent)"));
            }
            Ok(Some(Self { handle }))
        }
    }

    impl Drop for DesktopParent {
        fn drop(&mut self) {
            unsafe {
                let _ = CloseHandle(self.handle);
            }
        }
    }

    struct Job {
        handle: HANDLE,
    }

    impl Job {
        fn new() -> Result<Self, String> {
            // SAFETY: unnamed Job with default security.
            let handle = unsafe { CreateJobObjectW(std::ptr::null(), std::ptr::null()) };
            if handle.is_null() {
                return Err(last_api_error("CreateJobObjectW"));
            }
            let job = Self { handle };
            let mut limits: JOBOBJECT_EXTENDED_LIMIT_INFORMATION = unsafe { std::mem::zeroed() };
            limits.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
            // SAFETY: limits is initialized for the documented information class.
            if unsafe {
                SetInformationJobObject(
                    handle,
                    JobObjectExtendedLimitInformation,
                    (&raw const limits).cast(),
                    std::mem::size_of::<JOBOBJECT_EXTENDED_LIMIT_INFORMATION>() as u32,
                )
            } == 0
            {
                return Err(last_api_error("SetInformationJobObject"));
            }
            Ok(job)
        }

        fn terminate(&self) -> Result<(), String> {
            // SAFETY: this is a live Job handle owned by this wrapper.
            if unsafe { TerminateJobObject(self.handle, 1) } == 0 {
                return Err(last_api_error("TerminateJobObject"));
            }
            Ok(())
        }
    }

    impl Drop for Job {
        fn drop(&mut self) {
            // KILL_ON_JOB_CLOSE covers abrupt wrapper termination. A separate
            // kernel wait above covers abrupt desktop-host termination.
            unsafe {
                let _ = CloseHandle(self.handle);
            }
        }
    }

    struct ProcessHandles(PROCESS_INFORMATION);

    impl Drop for ProcessHandles {
        fn drop(&mut self) {
            // SAFETY: CreateProcessW returned both handles and they close once.
            unsafe {
                let _ = CloseHandle(self.0.hProcess);
                let _ = CloseHandle(self.0.hThread);
            }
        }
    }

    struct AttributeList(LPPROC_THREAD_ATTRIBUTE_LIST);

    impl Drop for AttributeList {
        fn drop(&mut self) {
            // SAFETY: initialized once and deleted exactly once.
            unsafe { DeleteProcThreadAttributeList(self.0) }
        }
    }

    fn build_command_line(executable: &OsStr, arguments: &[OsString]) -> Vec<u16> {
        let mut result = quote_argument(executable);
        for argument in arguments {
            result.push(b' ' as u16);
            result.extend(quote_argument(argument));
        }
        result.push(0);
        result
    }

    fn quote_argument(value: &OsStr) -> Vec<u16> {
        let input = value.encode_wide().collect::<Vec<_>>();
        let mut output = Vec::with_capacity(input.len() + 2);
        output.push(b'"' as u16);
        let mut backslashes = 0usize;
        for unit in input {
            if unit == b'\\' as u16 {
                backslashes += 1;
                continue;
            }
            if unit == b'"' as u16 {
                output.extend(std::iter::repeat_n(b'\\' as u16, backslashes * 2 + 1));
            } else {
                output.extend(std::iter::repeat_n(b'\\' as u16, backslashes));
            }
            backslashes = 0;
            output.push(unit);
        }
        output.extend(std::iter::repeat_n(b'\\' as u16, backslashes * 2));
        output.push(b'"' as u16);
        output
    }

    fn last_api_error(api: &str) -> String {
        format!("{api} failed with Windows error {}", unsafe {
            GetLastError()
        })
    }
}

#[cfg(any(target_os = "windows", test))]
fn adjacent_appcontainer_launcher(current_executable: &std::path::Path) -> PathBuf {
    current_executable.with_file_name("chatwaifu-appcontainer-host.exe")
}

fn packaged_runtime_executable(resource_dir: &Path) -> PathBuf {
    resource_dir
        .join("runtime-sidecar")
        .join("chatwaifu-runtime.exe")
}

#[cfg(any(target_os = "windows", test))]
fn packaged_appcontainer_launcher(resource_dir: &Path) -> PathBuf {
    resource_dir
        .join("bin")
        .join("chatwaifu-appcontainer-host.exe")
}

fn packaged_component_root_for_process(resource_dir: &Path) -> Result<PathBuf, String> {
    #[cfg(all(target_os = "windows", not(debug_assertions)))]
    {
        let current_image = windows_current_process_image_path()?;
        select_packaged_component_root(resource_dir, Some(&current_image))
    }
    #[cfg(any(not(target_os = "windows"), debug_assertions))]
    {
        select_packaged_component_root(resource_dir, None)
    }
}

fn select_packaged_component_root(
    resource_dir: &Path,
    physical_current_executable: Option<&Path>,
) -> Result<PathBuf, String> {
    let Some(current_executable) = physical_current_executable else {
        return Ok(resource_dir.to_path_buf());
    };
    current_executable
        .parent()
        .filter(|parent| !parent.as_os_str().is_empty())
        .map(Path::to_path_buf)
        .ok_or_else(|| {
            format!(
                "Windows 当前进程映像没有可用的安装目录：{}",
                current_executable.display()
            )
        })
}

#[cfg(target_os = "windows")]
fn runtime_supervisor_executable() -> Result<PathBuf, String> {
    #[cfg(not(debug_assertions))]
    {
        windows_current_process_image_path()
    }
    #[cfg(debug_assertions)]
    {
        std::env::current_exe()
            .map_err(|error| format!("无法定位 Windows Runtime supervisor：{error}"))
    }
}

/// Returns the physical Win32 image path rather than a packaged parent's
/// virtualized view of `current_exe`.
#[cfg(target_os = "windows")]
#[doc(hidden)]
pub fn windows_current_process_image_path() -> Result<PathBuf, String> {
    let win32_image = query_current_process_image_path(0)?;
    // A process inherited from a packaged parent can receive a LocalCache view
    // here even though its image was loaded from an ordinary NSIS directory.
    // Ask Shell for the current user's non-redirected root and only rewrite the
    // exact package-redirection shape validated below.
    let physical_local_app_data = windows_physical_local_app_data()?;
    if let Some(physical_image) =
        validated_physical_image_candidate(&win32_image, &physical_local_app_data)
    {
        return Ok(physical_image);
    }

    Err(format!(
        "Windows 当前进程映像无法解析为物理文件：{}",
        win32_image.display()
    ))
}

#[cfg(target_os = "windows")]
fn query_current_process_image_path(format: u32) -> Result<PathBuf, String> {
    use std::os::windows::ffi::OsStringExt;
    use windows_sys::Win32::Foundation::GetLastError;
    use windows_sys::Win32::System::Threading::{GetCurrentProcess, QueryFullProcessImageNameW};

    // Win32 extended-length paths are capped at 32,767 UTF-16 code units.
    const IMAGE_PATH_BUFFER_UNITS: usize = 32_768;
    let mut buffer = vec![0_u16; IMAGE_PATH_BUFFER_UNITS];
    let mut length =
        u32::try_from(buffer.len()).map_err(|_| "Windows 当前进程映像缓冲区长度溢出".to_owned())?;
    let succeeded = unsafe {
        QueryFullProcessImageNameW(
            GetCurrentProcess(),
            format,
            buffer.as_mut_ptr(),
            &mut length,
        )
    };
    if succeeded == 0 {
        return Err(format!(
            "QueryFullProcessImageNameW failed with Windows error {}",
            unsafe { GetLastError() }
        ));
    }
    if length == 0 {
        return Err("QueryFullProcessImageNameW returned an empty image path".to_owned());
    }
    buffer.truncate(length as usize);
    Ok(PathBuf::from(OsString::from_wide(&buffer)))
}

#[cfg(target_os = "windows")]
fn windows_physical_local_app_data() -> Result<PathBuf, String> {
    use windows_sys::Win32::UI::Shell::FOLDERID_LocalAppData;

    windows_physical_known_folder(&FOLDERID_LocalAppData, "LocalAppData")
}

#[cfg(target_os = "windows")]
fn windows_physical_roaming_app_data() -> Result<PathBuf, String> {
    use windows_sys::Win32::UI::Shell::FOLDERID_RoamingAppData;

    windows_physical_known_folder(&FOLDERID_RoamingAppData, "RoamingAppData")
}

#[cfg(target_os = "windows")]
fn windows_physical_known_folder(
    folder_id: &windows_sys::core::GUID,
    label: &str,
) -> Result<PathBuf, String> {
    use windows_sys::Win32::System::Com::CoTaskMemFree;
    use windows_sys::Win32::UI::Shell::{KF_FLAG_NO_PACKAGE_REDIRECTION, SHGetKnownFolderPath};

    let mut pointer = std::ptr::null_mut();
    let result = unsafe {
        SHGetKnownFolderPath(
            folder_id,
            KF_FLAG_NO_PACKAGE_REDIRECTION as u32,
            std::ptr::null_mut(),
            &mut pointer,
        )
    };
    if result < 0 {
        return Err(format!(
            "SHGetKnownFolderPath({label}, NO_PACKAGE_REDIRECTION) failed with HRESULT 0x{:08x}",
            result as u32
        ));
    }
    if pointer.is_null() {
        return Err(format!("SHGetKnownFolderPath returned a null {label} path"));
    }
    let path = unsafe {
        let mut length = 0_usize;
        while length < 32_768 && *pointer.add(length) != 0 {
            length += 1;
        }
        let value = if length == 32_768 {
            Err("SHGetKnownFolderPath returned an unterminated LocalAppData path".to_owned())
        } else {
            use std::os::windows::ffi::OsStringExt;
            Ok(PathBuf::from(OsString::from_wide(
                std::slice::from_raw_parts(pointer, length),
            )))
        };
        CoTaskMemFree(pointer.cast());
        value
    }?;
    if !path.is_absolute() || !windows_physical_path_is_directory(&path) {
        return Err(format!(
            "Windows physical {label} is not an existing absolute directory: {}",
            path.display()
        ));
    }
    Ok(path)
}

fn user_roots_from_known_folders(
    local_app_data: &Path,
    roaming_app_data: &Path,
    identifier: &str,
) -> Result<DesktopUserRoots, String> {
    if identifier.is_empty()
        || !identifier
            .chars()
            .all(|character| character.is_ascii_alphanumeric() || ".-_".contains(character))
    {
        return Err("桌宠应用标识不能安全映射到用户目录".to_owned());
    }
    let local_root = local_app_data.join(identifier);
    Ok(DesktopUserRoots {
        config: roaming_app_data.join(identifier),
        data: local_root.clone(),
        logs: local_root.join("logs"),
    })
}

fn desktop_user_roots(app: &AppHandle) -> Result<DesktopUserRoots, String> {
    #[cfg(target_os = "windows")]
    {
        user_roots_from_known_folders(
            &windows_physical_local_app_data()?,
            &windows_physical_roaming_app_data()?,
            &app.config().identifier,
        )
    }

    #[cfg(not(target_os = "windows"))]
    {
        Ok(DesktopUserRoots {
            config: app
                .path()
                .app_config_dir()
                .map_err(|error| format!("桌宠配置目录不可用：{error}"))?,
            data: app
                .path()
                .app_local_data_dir()
                .map_err(|error| format!("桌宠数据目录不可用：{error}"))?,
            logs: app
                .path()
                .app_log_dir()
                .map_err(|error| format!("桌宠日志目录不可用：{error}"))?,
        })
    }
}

pub(crate) fn desktop_config_dir(app: &AppHandle) -> Result<PathBuf, String> {
    desktop_user_roots(app).map(|roots| roots.config)
}

#[cfg(target_os = "windows")]
#[doc(hidden)]
pub fn windows_physical_user_root_paths(
    identifier: &str,
) -> Result<(PathBuf, PathBuf, PathBuf), String> {
    let roots = user_roots_from_known_folders(
        &windows_physical_local_app_data()?,
        &windows_physical_roaming_app_data()?,
        identifier,
    )?;
    Ok((roots.config, roots.data, roots.logs))
}

#[cfg(target_os = "windows")]
fn validated_physical_image_candidate(
    image: &Path,
    physical_local_app_data: &Path,
) -> Option<PathBuf> {
    if let Some((path_package_family, mapped)) =
        strict_localcache_physical_image_mapping(image, physical_local_app_data)
    {
        // GetCurrentPackageFamilyName may report NO_PACKAGE for these inherited
        // children. Keep this mapping bounded by the OS-provided user root, one
        // package-family component, an existing package directory, and an
        // existing same-relative-path image. Never accept LocalCache itself as
        // the physical fallback when one of these checks fails.
        if !windows_physical_path_is_directory(
            &physical_local_app_data
                .join("Packages")
                .join(&path_package_family),
        ) || !windows_physical_path_is_file(&mapped)
        {
            return None;
        }
        return Some(mapped);
    }
    windows_physical_path_is_file(image).then(|| image.to_path_buf())
}

#[cfg(test)]
fn strict_localcache_physical_image_candidate(
    image: &Path,
    physical_local_app_data: &Path,
    package_family: &OsStr,
) -> Option<PathBuf> {
    let (path_package_family, candidate) =
        strict_localcache_physical_image_mapping(image, physical_local_app_data)?;
    (path_package_family == package_family).then_some(candidate)
}

#[cfg(any(target_os = "windows", test))]
fn strict_localcache_physical_image_mapping(
    image: &Path,
    physical_local_app_data: &Path,
) -> Option<(OsString, PathBuf)> {
    let relative = image.strip_prefix(physical_local_app_data).ok()?;
    let mut components = relative.components();
    match components.next()? {
        std::path::Component::Normal(value) if value == OsStr::new("Packages") => {}
        _ => return None,
    }
    let path_package_family = match components.next()? {
        std::path::Component::Normal(value) if !value.is_empty() => value.to_os_string(),
        _ => return None,
    };
    match components.next()? {
        std::path::Component::Normal(value) if value == OsStr::new("LocalCache") => {}
        _ => return None,
    }
    match components.next()? {
        std::path::Component::Normal(value) if value == OsStr::new("Local") => {}
        _ => return None,
    }

    let mut payload = PathBuf::new();
    for component in components {
        match component {
            std::path::Component::Normal(value) => payload.push(value),
            _ => return None,
        }
    }
    if payload.as_os_str().is_empty() || payload.file_name() != image.file_name() {
        return None;
    }
    Some((path_package_family, physical_local_app_data.join(payload)))
}

#[cfg(target_os = "windows")]
fn windows_physical_path_is_file(path: &Path) -> bool {
    windows_physical_path_attributes(path).is_some_and(|attributes| attributes & 0x10 == 0)
}

#[cfg(target_os = "windows")]
fn windows_physical_path_is_directory(path: &Path) -> bool {
    windows_physical_path_attributes(path).is_some_and(|attributes| attributes & 0x10 != 0)
}

#[cfg(target_os = "windows")]
fn windows_physical_path_attributes(path: &Path) -> Option<u32> {
    use std::os::windows::ffi::OsStrExt;
    use windows_sys::Win32::Storage::FileSystem::{GetFileAttributesW, INVALID_FILE_ATTRIBUTES};

    let mut path_units = path.as_os_str().encode_wide().collect::<Vec<_>>();
    let mut verbatim = if path_units.starts_with(&[b'\\' as u16, b'\\' as u16]) {
        let mut value = "\\\\?\\UNC\\".encode_utf16().collect::<Vec<_>>();
        value.extend_from_slice(&path_units[2..]);
        value
    } else if path_units.len() >= 3
        && ((u16::from(b'A')..=u16::from(b'Z')).contains(&path_units[0])
            || (u16::from(b'a')..=u16::from(b'z')).contains(&path_units[0]))
        && path_units[1] == b':' as u16
        && (path_units[2] == u16::from(b'\\') || path_units[2] == u16::from(b'/'))
    {
        let mut value = "\\\\?\\".encode_utf16().collect::<Vec<_>>();
        value.append(&mut path_units);
        value
    } else {
        return None;
    };
    verbatim.push(0);
    let attributes = unsafe { GetFileAttributesW(verbatim.as_ptr()) };
    (attributes != INVALID_FILE_ATTRIBUTES).then_some(attributes)
}

fn open_sidecar_log(app: &AppHandle) -> Result<std::fs::File, String> {
    let directory = desktop_user_roots(app)?.logs;
    fs::create_dir_all(&directory).map_err(|error| format!("无法创建桌宠日志目录：{error}"))?;
    let current = directory.join("runtime-sidecar.log");
    if current
        .metadata()
        .map(|metadata| metadata.len() >= SIDECAR_LOG_MAX_BYTES)
        .unwrap_or(false)
    {
        let previous = directory.join("runtime-sidecar.previous.log");
        let _ = fs::remove_file(&previous);
        fs::rename(&current, &previous)
            .map_err(|error| format!("无法轮换 Runtime 日志：{error}"))?;
    }
    OpenOptions::new()
        .create(true)
        .append(true)
        .open(current)
        .map_err(|error| format!("无法打开 Runtime 日志：{error}"))
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
    if child.stdin.take().is_some() {
        let graceful_deadline = Instant::now() + Duration::from_secs(5);
        while Instant::now() < graceful_deadline {
            if child.try_wait().ok().flatten().is_some() {
                return;
            }
            thread::sleep(Duration::from_millis(50));
        }
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
    use super::{
        MAX_AUTOMATIC_RESTARTS, RUNTIME_SERVER_STARTUP_BUDGET_SECONDS,
        STARTUP_SUPERVISOR_GRACE_SECONDS, STARTUP_TIMEOUT, WORKER_PACK_STARTUP_BUDGET_SECONDS,
        adjacent_appcontainer_launcher, packaged_appcontainer_launcher,
        packaged_runtime_executable, restart_backoff, runtime_supervisor_exit_code_from,
        select_packaged_component_root, should_request_start,
        strict_localcache_physical_image_candidate, user_roots_from_known_folders,
    };
    use crate::runtime_health::RuntimeLifecycleState;
    use std::ffi::OsString;
    use std::path::Path;
    use std::time::Duration;

    #[test]
    fn restart_backoff_is_bounded_and_opens_the_circuit() {
        assert_eq!(restart_backoff(1), Some(Duration::from_secs(1)));
        assert_eq!(restart_backoff(2), Some(Duration::from_secs(2)));
        assert_eq!(restart_backoff(5), Some(Duration::from_secs(16)));
        assert_eq!(restart_backoff(MAX_AUTOMATIC_RESTARTS + 1), None);
    }

    #[test]
    fn startup_timeout_covers_worker_runtime_and_supervisor_budgets() {
        assert_eq!(WORKER_PACK_STARTUP_BUDGET_SECONDS, 300);
        assert_eq!(RUNTIME_SERVER_STARTUP_BUDGET_SECONDS, 120);
        assert_eq!(STARTUP_SUPERVISOR_GRACE_SECONDS, 30);
        assert_eq!(STARTUP_TIMEOUT, Duration::from_secs(450));
    }

    #[test]
    fn physical_known_folders_define_one_stable_app_namespace() {
        let roots = user_roots_from_known_folders(
            Path::new("C:/Users/test/AppData/Local"),
            Path::new("C:/Users/test/AppData/Roaming"),
            "local.chatwaifu.next",
        )
        .unwrap();

        assert_eq!(
            roots.config,
            Path::new("C:/Users/test/AppData/Roaming/local.chatwaifu.next")
        );
        assert_eq!(
            roots.data,
            Path::new("C:/Users/test/AppData/Local/local.chatwaifu.next")
        );
        assert_eq!(roots.logs, roots.data.join("logs"));
    }

    #[test]
    fn physical_known_folders_reject_an_identifier_that_can_escape_the_root() {
        for identifier in [
            "",
            "../local.chatwaifu.next",
            "local/chatwaifu",
            "local\\chatwaifu",
        ] {
            assert!(
                user_roots_from_known_folders(
                    Path::new("C:/Users/test/AppData/Local"),
                    Path::new("C:/Users/test/AppData/Roaming"),
                    identifier,
                )
                .is_err(),
                "unexpected safe identifier: {identifier}"
            );
        }
    }

    #[test]
    fn ensure_started_only_wakes_an_inactive_supervisor() {
        assert!(should_request_start(&RuntimeLifecycleState::Stopped));
        assert!(should_request_start(&RuntimeLifecycleState::CircuitOpen));
        assert!(!should_request_start(&RuntimeLifecycleState::Starting));
        assert!(!should_request_start(&RuntimeLifecycleState::Ready));
        assert!(!should_request_start(&RuntimeLifecycleState::Backoff));
    }

    #[test]
    fn packaged_appcontainer_launcher_is_discovered_next_to_desktop_host() {
        assert_eq!(
            adjacent_appcontainer_launcher(Path::new("/opt/chatwaifu/chatwaifu-desktop-host.exe")),
            Path::new("/opt/chatwaifu/chatwaifu-appcontainer-host.exe")
        );
    }

    #[test]
    fn unpackaged_components_keep_the_tauri_resource_root() {
        let resources = Path::new("C:/Program Files/ChatWaifu NEXT/resources");
        let component_root = select_packaged_component_root(resources, None).unwrap();
        assert_eq!(
            packaged_runtime_executable(&component_root),
            resources.join("runtime-sidecar/chatwaifu-runtime.exe")
        );
        assert_eq!(
            packaged_appcontainer_launcher(&component_root),
            resources.join("bin/chatwaifu-appcontainer-host.exe")
        );
    }

    #[test]
    fn windows_packaged_components_ignore_a_virtualized_tauri_resource_root() {
        let virtual_resources = Path::new(
            "C:/Users/test/AppData/Local/Packages/OpenAI.Codex/LocalCache/Local/ChatWaifu NEXT",
        );
        let physical_image =
            Path::new("C:/Users/test/AppData/Local/ChatWaifu NEXT/chatwaifu-desktop-host.exe");
        let component_root =
            select_packaged_component_root(virtual_resources, Some(physical_image)).unwrap();

        assert_eq!(
            packaged_runtime_executable(&component_root),
            Path::new(
                "C:/Users/test/AppData/Local/ChatWaifu NEXT/runtime-sidecar/chatwaifu-runtime.exe"
            )
        );
        assert_eq!(
            packaged_appcontainer_launcher(&component_root),
            Path::new(
                "C:/Users/test/AppData/Local/ChatWaifu NEXT/bin/chatwaifu-appcontainer-host.exe"
            )
        );
    }

    #[test]
    fn packaged_component_root_rejects_an_image_without_a_parent() {
        let error = select_packaged_component_root(
            Path::new("C:/virtual/resources"),
            Some(Path::new("chatwaifu-desktop-host.exe")),
        )
        .unwrap_err();
        assert!(error.contains("没有可用的安装目录"));
    }

    #[test]
    fn codex_localcache_image_maps_to_the_physical_current_user_install() {
        let physical_local_app_data = Path::new("C:/Users/alice/AppData/Local");
        let package_family = OsString::from("OpenAI.Codex_2p2nqsd0c76g0");
        let virtual_image = Path::new(
            "C:/Users/alice/AppData/Local/Packages/OpenAI.Codex_2p2nqsd0c76g0/LocalCache/Local/ChatWaifu NEXT/chatwaifu-desktop-host.exe",
        );

        assert_eq!(
            strict_localcache_physical_image_candidate(
                virtual_image,
                physical_local_app_data,
                &package_family,
            ),
            Some(
                Path::new("C:/Users/alice/AppData/Local/ChatWaifu NEXT/chatwaifu-desktop-host.exe")
                    .to_path_buf()
            )
        );
    }

    #[test]
    fn localcache_mapping_rejects_an_unrelated_or_near_match_package_path() {
        let physical_local_app_data = Path::new("C:/Users/alice/AppData/Local");
        let package_family = OsString::from("OpenAI.Codex_2p2nqsd0c76g0");
        for image in [
            "C:/Users/alice/AppData/Local/Packages/Other.Package/LocalCache/Local/ChatWaifu NEXT/chatwaifu-desktop-host.exe",
            "C:/Users/alice/AppData/Local/Packages/OpenAI.Codex_2p2nqsd0c76g0/LocalCache/LocalOther/ChatWaifu NEXT/chatwaifu-desktop-host.exe",
            "C:/Users/other/AppData/Local/Packages/OpenAI.Codex_2p2nqsd0c76g0/LocalCache/Local/ChatWaifu NEXT/chatwaifu-desktop-host.exe",
        ] {
            assert_eq!(
                strict_localcache_physical_image_candidate(
                    Path::new(image),
                    physical_local_app_data,
                    &package_family,
                ),
                None,
                "unexpected mapping for {image}"
            );
        }
    }

    #[test]
    fn ordinary_desktop_arguments_do_not_enter_runtime_supervisor_mode() {
        assert_eq!(
            runtime_supervisor_exit_code_from(
                [OsString::from("--some-tauri-argument")].into_iter()
            ),
            None
        );
    }

    #[test]
    fn malformed_runtime_supervisor_arguments_fail_closed() {
        assert_eq!(
            runtime_supervisor_exit_code_from(
                [OsString::from("--chatwaifu-runtime-supervisor")].into_iter()
            ),
            Some(2)
        );
        assert_eq!(
            runtime_supervisor_exit_code_from(
                [
                    OsString::from("--chatwaifu-runtime-supervisor"),
                    OsString::from("--"),
                ]
                .into_iter()
            ),
            Some(2)
        );
    }
}
