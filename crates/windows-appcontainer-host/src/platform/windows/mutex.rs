use std::os::windows::ffi::OsStrExt;

use windows_sys::Win32::Foundation::{
    CloseHandle, HANDLE, WAIT_ABANDONED_0, WAIT_FAILED, WAIT_OBJECT_0,
};
use windows_sys::Win32::System::Threading::{
    CreateMutexW, INFINITE, ReleaseMutex, WaitForSingleObject,
};

use crate::error::Result;

pub(super) struct NamedMutex {
    handle: HANDLE,
}

impl NamedMutex {
    pub(super) fn acquire(key: &str) -> Result<Self> {
        let name = std::ffi::OsStr::new(&format!("Local\\ChatWaifu.AppContainer.{key}"))
            .encode_wide()
            .chain(Some(0))
            .collect::<Vec<_>>();
        // SAFETY: the mutex name is a live, NUL-terminated UTF-16 string.
        let handle = unsafe { CreateMutexW(std::ptr::null(), 0, name.as_ptr()) };
        if handle.is_null() {
            return Err(std::io::Error::last_os_error().into());
        }
        // A named kernel mutex blocks without polling and is automatically
        // released if a prior launcher crashes.
        let wait = unsafe { WaitForSingleObject(handle, INFINITE) };
        if wait != WAIT_OBJECT_0 && wait != WAIT_ABANDONED_0 {
            let error = if wait == WAIT_FAILED {
                std::io::Error::last_os_error()
            } else {
                std::io::Error::other(format!("unexpected mutex wait result {wait}"))
            };
            unsafe {
                let _ = CloseHandle(handle);
            }
            return Err(error.into());
        }
        Ok(Self { handle })
    }
}

impl Drop for NamedMutex {
    fn drop(&mut self) {
        // SAFETY: this guard owns the acquired mutex and its handle.
        unsafe {
            let _ = ReleaseMutex(self.handle);
            let _ = CloseHandle(self.handle);
        }
    }
}

unsafe impl Send for NamedMutex {}
