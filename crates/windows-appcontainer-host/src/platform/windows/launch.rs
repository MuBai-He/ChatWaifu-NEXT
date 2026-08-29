//! Atomic AppContainer process creation for a long-lived stdio MCP child.
//!
//! The launch shape follows the MIT-licensed `sandboxrs-windows` AppContainer
//! backend, with an additional `PROC_THREAD_ATTRIBUTE_JOB_LIST` so the process
//! enters the resource-limited Job Object before its first instruction runs.

use std::ffi::{OsStr, OsString};
use std::fs::File;
use std::io::{self, Write};
use std::os::windows::ffi::OsStrExt;
use std::os::windows::io::{FromRawHandle, RawHandle};
use std::path::{Path, PathBuf};
use std::sync::Arc;
use std::sync::atomic::{AtomicU64, Ordering};
use std::thread;

use windows_sys::Win32::Foundation::{
    CloseHandle, ERROR_PIPE_CONNECTED, GENERIC_READ, GENERIC_WRITE, GetLastError, HANDLE,
    HANDLE_FLAG_INHERIT, HLOCAL, INVALID_HANDLE_VALUE, LocalFree, SetHandleInformation,
    WAIT_FAILED, WAIT_OBJECT_0,
};
use windows_sys::Win32::Security::Authorization::{
    ConvertSidToStringSidW, ConvertStringSecurityDescriptorToSecurityDescriptorW,
    ConvertStringSidToSidW,
};
use windows_sys::Win32::Security::{
    GetTokenInformation, PSECURITY_DESCRIPTOR, PSID, SECURITY_ATTRIBUTES, SECURITY_CAPABILITIES,
    SID_AND_ATTRIBUTES, TOKEN_QUERY, TOKEN_USER, TokenUser,
};
use windows_sys::Win32::Storage::FileSystem::{
    CreateFileW, FILE_FLAG_FIRST_PIPE_INSTANCE, OPEN_EXISTING, PIPE_ACCESS_INBOUND,
    PIPE_ACCESS_OUTBOUND,
};
use windows_sys::Win32::System::JobObjects::{
    CreateJobObjectW, JOB_OBJECT_LIMIT_ACTIVE_PROCESS, JOB_OBJECT_LIMIT_JOB_MEMORY,
    JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE, JOBOBJECT_EXTENDED_LIMIT_INFORMATION,
    JobObjectExtendedLimitInformation, SetInformationJobObject, TerminateJobObject,
};
use windows_sys::Win32::System::Pipes::{
    ConnectNamedPipe, CreateNamedPipeW, PIPE_REJECT_REMOTE_CLIENTS, PIPE_TYPE_BYTE, PIPE_WAIT,
};
use windows_sys::Win32::System::Threading::{
    CREATE_NO_WINDOW, CREATE_UNICODE_ENVIRONMENT, CreateEventW, CreateProcessW,
    DeleteProcThreadAttributeList, EXTENDED_STARTUPINFO_PRESENT, GetCurrentProcess,
    GetExitCodeProcess, InitializeProcThreadAttributeList, LPPROC_THREAD_ATTRIBUTE_LIST,
    OpenProcessToken, PROC_THREAD_ATTRIBUTE_HANDLE_LIST, PROC_THREAD_ATTRIBUTE_JOB_LIST,
    PROC_THREAD_ATTRIBUTE_SECURITY_CAPABILITIES, PROCESS_INFORMATION, STARTF_USESTDHANDLES,
    STARTUPINFOEXW, SetEvent, UpdateProcThreadAttribute, WaitForMultipleObjects,
    WaitForSingleObject,
};

use crate::cli::NetworkPolicy;
use crate::error::{HostError, Result};

pub(super) struct LaunchRequest {
    pub(super) profile_sid: String,
    pub(super) network: NetworkPolicy,
    pub(super) executable: PathBuf,
    pub(super) arguments: Vec<OsString>,
    pub(super) cwd: PathBuf,
    pub(super) memory_bytes: u64,
    pub(super) max_processes: u32,
}

pub(super) struct Child {
    process: HANDLE,
    thread: HANDLE,
    job: Job,
    stdin: Option<File>,
    stdout: Option<File>,
    stderr: Option<File>,
}

pub(super) fn spawn(request: LaunchRequest) -> Result<Child> {
    let capabilities = SecurityCaps::new(&request.profile_sid, request.network)?;
    let mut stdio = StdioSetup::new(&request.profile_sid)?;
    let job = Job::new(request.memory_bytes, request.max_processes)?;
    let command_line = build_command_line(&request.executable, &request.arguments);
    let executable = wide_null(request.executable.as_os_str());
    let cwd = wide_null(request.cwd.as_os_str());

    let mut attribute_size = 0usize;
    let attribute_count = 3u32;
    // SAFETY: the null call is the documented buffer size query.
    unsafe {
        let _ = InitializeProcThreadAttributeList(
            std::ptr::null_mut(),
            attribute_count,
            0,
            &mut attribute_size,
        );
    }
    if attribute_size == 0 {
        return Err(last_api_error("InitializeProcThreadAttributeList(size)"));
    }
    let mut attribute_storage = vec![0u8; attribute_size];
    let attribute_list = attribute_storage.as_mut_ptr().cast();
    // SAFETY: the buffer has the exact size returned above and remains live
    // through CreateProcessW.
    if unsafe {
        InitializeProcThreadAttributeList(attribute_list, attribute_count, 0, &mut attribute_size)
    } == 0
    {
        return Err(last_api_error("InitializeProcThreadAttributeList"));
    }
    let attribute_guard = AttributeList(attribute_list);

    update_attribute(
        attribute_list,
        PROC_THREAD_ATTRIBUTE_SECURITY_CAPABILITIES as usize,
        (&raw const capabilities.raw).cast(),
        std::mem::size_of::<SECURITY_CAPABILITIES>(),
        "UpdateProcThreadAttribute(SECURITY_CAPABILITIES)",
    )?;
    update_attribute(
        attribute_list,
        PROC_THREAD_ATTRIBUTE_HANDLE_LIST as usize,
        stdio.child_handles.as_ptr().cast(),
        std::mem::size_of::<HANDLE>() * stdio.child_handles.len(),
        "UpdateProcThreadAttribute(HANDLE_LIST)",
    )?;
    let job_handle = job.handle;
    update_attribute(
        attribute_list,
        PROC_THREAD_ATTRIBUTE_JOB_LIST as usize,
        (&raw const job_handle).cast(),
        std::mem::size_of::<HANDLE>(),
        "UpdateProcThreadAttribute(JOB_LIST)",
    )?;

    stdio.startup.lpAttributeList = attribute_list;
    let mut process_information: PROCESS_INFORMATION = unsafe { std::mem::zeroed() };
    let mut mutable_command_line = command_line;
    // SECURITY_CAPABILITIES + JOB_LIST makes isolation/tree containment
    // atomic; unlike suspended-then-assign, no child code can race the Job.
    let created = unsafe {
        CreateProcessW(
            executable.as_ptr(),
            mutable_command_line.as_mut_ptr(),
            std::ptr::null(),
            std::ptr::null(),
            1,
            EXTENDED_STARTUPINFO_PRESENT | CREATE_UNICODE_ENVIRONMENT | CREATE_NO_WINDOW,
            std::ptr::null(),
            cwd.as_ptr(),
            (&raw mut stdio.startup).cast(),
            &mut process_information,
        )
    };
    drop(attribute_guard);
    if created == 0 {
        return Err(last_api_error("CreateProcessW(AppContainer)"));
    }

    // The launcher must close every parent copy of a child endpoint or EOF
    // will never propagate through MCP stdio.
    stdio.close_child_handles();
    Ok(Child {
        process: process_information.hProcess,
        thread: process_information.hThread,
        job,
        stdin: stdio.stdin.take(),
        stdout: stdio.stdout.take(),
        stderr: stdio.stderr.take(),
    })
}

impl Child {
    pub(super) fn bridge_and_wait(mut self) -> Result<i32> {
        let child_stdin = self.stdin.take().expect("stdio setup always creates stdin");
        let child_stdout = self
            .stdout
            .take()
            .expect("stdio setup always creates stdout");
        let child_stderr = self
            .stderr
            .take()
            .expect("stdio setup always creates stderr");

        // The input bridge intentionally remains detached. Its kernel event
        // turns parent EOF/error into cancellation without polling.
        let stdin_closed = Arc::new(ParentStdinClosed::new()?);
        let stdin_closed_for_bridge = Arc::clone(&stdin_closed);
        let _stdin_bridge =
            thread::Builder::new()
                .name("mcp-stdin".to_owned())
                .spawn(move || {
                    let mut source = io::stdin().lock();
                    let mut destination = child_stdin;
                    let _ = io::copy(&mut source, &mut destination);
                    let _ = destination.flush();
                    drop(destination);
                    stdin_closed_for_bridge.signal();
                })?;
        let stdout_bridge = thread::Builder::new().name("mcp-stdout".to_owned()).spawn(
            move || -> io::Result<()> {
                let mut source = child_stdout;
                let mut destination = io::stdout().lock();
                io::copy(&mut source, &mut destination)?;
                destination.flush()
            },
        )?;
        let stderr_bridge = thread::Builder::new().name("mcp-stderr".to_owned()).spawn(
            move || -> io::Result<()> {
                let mut source = child_stderr;
                let mut destination = io::stderr().lock();
                io::copy(&mut source, &mut destination)?;
                destination.flush()
            },
        )?;

        let wait_handles = [self.process, stdin_closed.handle];
        // SAFETY: both handles remain live for the duration of the wait.
        let wait = unsafe {
            WaitForMultipleObjects(
                wait_handles.len() as u32,
                wait_handles.as_ptr(),
                0,
                u32::MAX,
            )
        };
        match wait {
            WAIT_OBJECT_0 => {}
            value if value == WAIT_OBJECT_0 + 1 => {
                let _ = self.job.terminate();
                if unsafe { WaitForSingleObject(self.process, u32::MAX) } != WAIT_OBJECT_0 {
                    return Err(last_api_error("WaitForSingleObject(cancelled child)"));
                }
            }
            WAIT_FAILED => return Err(last_api_error("WaitForMultipleObjects")),
            value => {
                return Err(HostError::AppContainer(format!(
                    "unexpected WaitForMultipleObjects result {value}"
                )));
            }
        }
        let mut exit_code = 0u32;
        // SAFETY: the process has exited and the out pointer is valid.
        if unsafe { GetExitCodeProcess(self.process, &mut exit_code) } == 0 {
            return Err(last_api_error("GetExitCodeProcess"));
        }
        // Root exit ends the plugin invocation. Kill any remaining descendants
        // before draining pipes so an inherited stdout handle cannot hang EOF.
        let _ = self.job.terminate();
        stdout_bridge
            .join()
            .map_err(|_| HostError::ChildIo("stdout bridge panicked".to_owned()))?
            .map_err(|error| HostError::ChildIo(format!("stdout: {error}")))?;
        stderr_bridge
            .join()
            .map_err(|_| HostError::ChildIo("stderr bridge panicked".to_owned()))?
            .map_err(|error| HostError::ChildIo(format!("stderr: {error}")))?;

        Ok(i32::try_from(exit_code).unwrap_or(1))
    }
}

struct ParentStdinClosed {
    handle: HANDLE,
}

impl ParentStdinClosed {
    fn new() -> Result<Self> {
        // SAFETY: unnamed manual-reset event with default security.
        let handle = unsafe { CreateEventW(std::ptr::null(), 1, 0, std::ptr::null()) };
        if handle.is_null() {
            return Err(last_api_error("CreateEventW(parent stdin)"));
        }
        Ok(Self { handle })
    }

    fn signal(&self) {
        // SAFETY: the Arc keeps this event live through the signal operation.
        unsafe {
            let _ = SetEvent(self.handle);
        }
    }
}

impl Drop for ParentStdinClosed {
    fn drop(&mut self) {
        unsafe {
            let _ = CloseHandle(self.handle);
        }
    }
}

unsafe impl Send for ParentStdinClosed {}
unsafe impl Sync for ParentStdinClosed {}

impl Drop for Child {
    fn drop(&mut self) {
        let _ = self.job.terminate();
        // SAFETY: both handles came from CreateProcessW and are closed once.
        unsafe {
            let _ = CloseHandle(self.process);
            let _ = CloseHandle(self.thread);
        }
    }
}

struct Job {
    handle: HANDLE,
}

impl Job {
    fn new(memory_bytes: u64, max_processes: u32) -> Result<Self> {
        // SAFETY: unnamed Job with default security.
        let handle = unsafe { CreateJobObjectW(std::ptr::null(), std::ptr::null()) };
        if handle.is_null() {
            return Err(last_api_error("CreateJobObjectW"));
        }
        let job = Self { handle };
        let mut limits: JOBOBJECT_EXTENDED_LIMIT_INFORMATION = unsafe { std::mem::zeroed() };
        limits.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            | JOB_OBJECT_LIMIT_ACTIVE_PROCESS
            | JOB_OBJECT_LIMIT_JOB_MEMORY;
        limits.BasicLimitInformation.ActiveProcessLimit = max_processes;
        limits.JobMemoryLimit = usize::try_from(memory_bytes).map_err(|_| {
            HostError::AppContainer("memory limit does not fit this x64 process".to_owned())
        })?;
        // SAFETY: limits is fully initialized for the documented info class.
        let updated = unsafe {
            SetInformationJobObject(
                handle,
                JobObjectExtendedLimitInformation,
                (&raw const limits).cast(),
                std::mem::size_of::<JOBOBJECT_EXTENDED_LIMIT_INFORMATION>() as u32,
            )
        };
        if updated == 0 {
            return Err(last_api_error("SetInformationJobObject"));
        }
        Ok(job)
    }

    fn terminate(&self) -> Result<()> {
        // SAFETY: this is a valid live Job handle.
        if unsafe { TerminateJobObject(self.handle, 1) } == 0 {
            return Err(last_api_error("TerminateJobObject"));
        }
        Ok(())
    }
}

impl Drop for Job {
    fn drop(&mut self) {
        // KILL_ON_JOB_CLOSE guarantees descendants cannot outlive launcher
        // cancellation or a launcher crash.
        unsafe {
            let _ = CloseHandle(self.handle);
        }
    }
}

unsafe impl Send for Job {}

struct SecurityCaps {
    raw: SECURITY_CAPABILITIES,
    sid_allocations: Vec<HLOCAL>,
    _raw_capabilities: Vec<SID_AND_ATTRIBUTES>,
}

impl SecurityCaps {
    fn new(profile_sid: &str, network: NetworkPolicy) -> Result<Self> {
        let capability_sids = match network {
            NetworkPolicy::Deny => Vec::new(),
            NetworkPolicy::Allow => rappct::capability::derive_named_capability_sids(&[
                "internetClient",
                "privateNetworkClientServer",
            ])
            .map_err(|error| HostError::AppContainer(error.to_string()))?
            .into_iter()
            .map(|value| value.sid_sddl)
            .collect(),
        };
        let mut sid_allocations = Vec::with_capacity(1 + capability_sids.len());
        sid_allocations.push(convert_sid(profile_sid)?);
        for sid in &capability_sids {
            sid_allocations.push(convert_sid(sid)?);
        }
        let mut raw_capabilities = sid_allocations
            .iter()
            .skip(1)
            .map(|sid| SID_AND_ATTRIBUTES {
                Sid: (*sid).cast(),
                Attributes: 0x0000_0004, // SE_GROUP_ENABLED
            })
            .collect::<Vec<_>>();
        let raw = SECURITY_CAPABILITIES {
            AppContainerSid: sid_allocations[0].cast(),
            Capabilities: if raw_capabilities.is_empty() {
                std::ptr::null_mut()
            } else {
                raw_capabilities.as_mut_ptr()
            },
            CapabilityCount: raw_capabilities.len() as u32,
            Reserved: 0,
        };
        Ok(Self {
            raw,
            sid_allocations,
            _raw_capabilities: raw_capabilities,
        })
    }
}

impl Drop for SecurityCaps {
    fn drop(&mut self) {
        for sid in self.sid_allocations.drain(..) {
            // SAFETY: ConvertStringSidToSidW allocated every entry.
            unsafe {
                let _ = LocalFree(sid);
            }
        }
    }
}

fn convert_sid(value: &str) -> Result<HLOCAL> {
    let wide: Vec<u16> = value.encode_utf16().chain(Some(0)).collect();
    let mut sid: PSID = std::ptr::null_mut();
    // SAFETY: input is NUL-terminated and sid is a valid out pointer.
    if unsafe { ConvertStringSidToSidW(wide.as_ptr(), &mut sid) } == 0 || sid.is_null() {
        return Err(last_api_error("ConvertStringSidToSidW"));
    }
    Ok(sid.cast())
}

struct AttributeList(LPPROC_THREAD_ATTRIBUTE_LIST);

impl Drop for AttributeList {
    fn drop(&mut self) {
        // SAFETY: initialized once and deleted exactly once.
        unsafe { DeleteProcThreadAttributeList(self.0) }
    }
}

fn update_attribute(
    list: LPPROC_THREAD_ATTRIBUTE_LIST,
    attribute: usize,
    value: *const core::ffi::c_void,
    size: usize,
    api: &'static str,
) -> Result<()> {
    // SAFETY: all attribute backing storage remains live through CreateProcessW.
    if unsafe {
        UpdateProcThreadAttribute(
            list,
            0,
            attribute,
            value,
            size,
            std::ptr::null_mut(),
            std::ptr::null(),
        )
    } == 0
    {
        return Err(last_api_error(api));
    }
    Ok(())
}

struct StdioSetup {
    startup: STARTUPINFOEXW,
    stdin: Option<File>,
    stdout: Option<File>,
    stderr: Option<File>,
    child_handles: Vec<HANDLE>,
}

impl StdioSetup {
    fn new(profile_sid: &str) -> Result<Self> {
        let security = PipeSecurityDescriptor::new(profile_sid)?;
        let (stdin, child_stdin) = create_piped_handle(IoSlot::Stdin, &security)?;
        let (stdout, child_stdout) = create_piped_handle(IoSlot::Stdout, &security)?;
        let (stderr, child_stderr) = create_piped_handle(IoSlot::Stderr, &security)?;
        let mut startup: STARTUPINFOEXW = unsafe { std::mem::zeroed() };
        startup.StartupInfo.cb = std::mem::size_of::<STARTUPINFOEXW>() as u32;
        startup.StartupInfo.dwFlags = STARTF_USESTDHANDLES;
        startup.StartupInfo.hStdInput = child_stdin;
        startup.StartupInfo.hStdOutput = child_stdout;
        startup.StartupInfo.hStdError = child_stderr;
        Ok(Self {
            startup,
            stdin: Some(stdin),
            stdout: Some(stdout),
            stderr: Some(stderr),
            child_handles: vec![child_stdin, child_stdout, child_stderr],
        })
    }

    fn close_child_handles(&mut self) {
        for handle in self.child_handles.drain(..) {
            unsafe {
                let _ = CloseHandle(handle);
            }
        }
    }
}

impl Drop for StdioSetup {
    fn drop(&mut self) {
        self.close_child_handles();
    }
}

#[derive(Clone, Copy)]
enum IoSlot {
    Stdin,
    Stdout,
    Stderr,
}

struct PipeSecurityDescriptor(HLOCAL);

impl PipeSecurityDescriptor {
    fn new(appcontainer_sid: &str) -> Result<Self> {
        let user_sid = current_user_sid()?;
        let sddl =
            format!("D:P(A;;GA;;;{user_sid})(A;;GA;;;{appcontainer_sid})S:(ML;;NW;;;S-1-16-0)");
        let wide: Vec<u16> = sddl.encode_utf16().chain(Some(0)).collect();
        let mut descriptor: PSECURITY_DESCRIPTOR = std::ptr::null_mut();
        let mut descriptor_len = 0u32;
        // SAFETY: valid SDDL and out pointers; allocation is owned below.
        if unsafe {
            ConvertStringSecurityDescriptorToSecurityDescriptorW(
                wide.as_ptr(),
                1,
                &mut descriptor,
                &mut descriptor_len,
            )
        } == 0
            || descriptor.is_null()
        {
            return Err(last_api_error(
                "ConvertStringSecurityDescriptorToSecurityDescriptorW",
            ));
        }
        Ok(Self(descriptor.cast()))
    }
}

impl Drop for PipeSecurityDescriptor {
    fn drop(&mut self) {
        unsafe {
            let _ = LocalFree(self.0);
        }
    }
}

fn create_piped_handle(slot: IoSlot, security: &PipeSecurityDescriptor) -> Result<(File, HANDLE)> {
    let name = next_pipe_name(slot);
    let wide_name: Vec<u16> = name.encode_utf16().chain(Some(0)).collect();
    let server_access = match slot {
        IoSlot::Stdin => PIPE_ACCESS_OUTBOUND,
        IoSlot::Stdout | IoSlot::Stderr => PIPE_ACCESS_INBOUND,
    };
    let client_access = match slot {
        IoSlot::Stdin => GENERIC_READ,
        IoSlot::Stdout | IoSlot::Stderr => GENERIC_WRITE,
    };
    let attributes = SECURITY_ATTRIBUTES {
        nLength: std::mem::size_of::<SECURITY_ATTRIBUTES>() as u32,
        lpSecurityDescriptor: security.0.cast(),
        bInheritHandle: 0,
    };
    let server = unsafe {
        CreateNamedPipeW(
            wide_name.as_ptr(),
            server_access | FILE_FLAG_FIRST_PIPE_INSTANCE,
            PIPE_TYPE_BYTE | PIPE_WAIT | PIPE_REJECT_REMOTE_CLIENTS,
            1,
            64 * 1024,
            64 * 1024,
            0,
            &attributes,
        )
    };
    if server == INVALID_HANDLE_VALUE {
        return Err(last_api_error("CreateNamedPipeW"));
    }
    let client = unsafe {
        CreateFileW(
            wide_name.as_ptr(),
            client_access,
            0,
            std::ptr::null(),
            OPEN_EXISTING,
            0,
            std::ptr::null_mut(),
        )
    };
    if client == INVALID_HANDLE_VALUE {
        let error = last_api_error("CreateFileW(named pipe)");
        unsafe {
            let _ = CloseHandle(server);
        }
        return Err(error);
    }
    let connected = unsafe { ConnectNamedPipe(server, std::ptr::null_mut()) };
    if connected == 0 && unsafe { GetLastError() } != ERROR_PIPE_CONNECTED {
        let error = last_api_error("ConnectNamedPipe");
        unsafe {
            let _ = CloseHandle(client);
            let _ = CloseHandle(server);
        }
        return Err(error);
    }
    // Only the client endpoint may cross CreateProcessW's explicit handle list.
    if unsafe { SetHandleInformation(client, HANDLE_FLAG_INHERIT, HANDLE_FLAG_INHERIT) } == 0 {
        let error = last_api_error("SetHandleInformation(HANDLE_FLAG_INHERIT)");
        unsafe {
            let _ = CloseHandle(client);
            let _ = CloseHandle(server);
        }
        return Err(error);
    }
    Ok((
        unsafe { File::from_raw_handle(server as RawHandle) },
        client,
    ))
}

fn next_pipe_name(slot: IoSlot) -> String {
    static NEXT_PIPE: AtomicU64 = AtomicU64::new(0);
    let slot = match slot {
        IoSlot::Stdin => "stdin",
        IoSlot::Stdout => "stdout",
        IoSlot::Stderr => "stderr",
    };
    let nonce = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|value| value.as_nanos())
        .unwrap_or_default();
    let sequence = NEXT_PIPE.fetch_add(1, Ordering::Relaxed);
    format!(
        r"\\.\pipe\chatwaifu-appcontainer-{}-{nonce:x}-{sequence:x}-{slot}",
        std::process::id()
    )
}

fn current_user_sid() -> Result<String> {
    let mut token = std::ptr::null_mut();
    if unsafe { OpenProcessToken(GetCurrentProcess(), TOKEN_QUERY, &mut token) } == 0 {
        return Err(last_api_error("OpenProcessToken"));
    }
    let result = (|| {
        let mut required = 0u32;
        unsafe {
            let _ = GetTokenInformation(token, TokenUser, std::ptr::null_mut(), 0, &mut required);
        }
        if required == 0 {
            return Err(last_api_error("GetTokenInformation(size)"));
        }
        let mut buffer = vec![0u8; required as usize];
        if unsafe {
            GetTokenInformation(
                token,
                TokenUser,
                buffer.as_mut_ptr().cast(),
                required,
                &mut required,
            )
        } == 0
        {
            return Err(last_api_error("GetTokenInformation(TokenUser)"));
        }
        let user = unsafe { std::ptr::read_unaligned(buffer.as_ptr().cast::<TOKEN_USER>()) };
        let mut string_sid = std::ptr::null_mut();
        if unsafe { ConvertSidToStringSidW(user.User.Sid, &mut string_sid) } == 0
            || string_sid.is_null()
        {
            return Err(last_api_error("ConvertSidToStringSidW"));
        }
        let mut length = 0usize;
        unsafe {
            while *string_sid.add(length) != 0 {
                length += 1;
            }
        }
        let value =
            unsafe { String::from_utf16_lossy(std::slice::from_raw_parts(string_sid, length)) };
        unsafe {
            let _ = LocalFree(string_sid.cast());
        }
        Ok(value)
    })();
    unsafe {
        let _ = CloseHandle(token);
    }
    result
}

fn build_command_line(executable: &Path, arguments: &[OsString]) -> Vec<u16> {
    let mut result = quote_argument(executable.as_os_str());
    for argument in arguments {
        result.push(b' ' as u16);
        result.extend(quote_argument(argument));
    }
    result.push(0);
    result
}

/// Quote one argv entry according to the CommandLineToArgvW backslash rules.
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

fn wide_null(value: &OsStr) -> Vec<u16> {
    value.encode_wide().chain(Some(0)).collect()
}

fn last_api_error(api: &'static str) -> HostError {
    HostError::WindowsApi {
        api,
        code: unsafe { GetLastError() },
    }
}

// Raw Windows handles are process-local capabilities owned exclusively by
// these wrappers and may move with the launcher state.
unsafe impl Send for Child {}
