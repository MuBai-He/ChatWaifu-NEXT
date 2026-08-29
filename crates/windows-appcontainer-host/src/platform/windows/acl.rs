use std::path::Path;

use rappct::acl::{AccessMask, ResourcePath, grant_to_package};
use rappct::sid::AppContainerSid;

use crate::error::{HostError, Result};
use crate::journal::RootAccess;

const FILE_GENERIC_READ_EXECUTE: u32 = 0x0012_00A9;
const FILE_GENERIC_WRITE: u32 = 0x0012_0116;
const DELETE: u32 = 0x0001_0000;
const FILE_DELETE_CHILD: u32 = 0x0000_0040;
const WRITABLE_DIRECTORY_ACCESS: u32 =
    FILE_GENERIC_READ_EXECUTE | FILE_GENERIC_WRITE | DELETE | FILE_DELETE_CHILD;
const WRITABLE_FILE_ACCESS: u32 = FILE_GENERIC_READ_EXECUTE | FILE_GENERIC_WRITE | DELETE;

pub(super) fn grant_root(path: &Path, sid: &str, access: RootAccess) -> Result<()> {
    let metadata = std::fs::metadata(path)?;
    let target = if metadata.is_dir() {
        ResourcePath::Directory(path.to_path_buf())
    } else if metadata.is_file() {
        ResourcePath::File(path.to_path_buf())
    } else {
        return Err(HostError::InvalidPath {
            path: path.to_path_buf(),
            reason: "only files and directories can be granted".to_owned(),
        });
    };
    let mask = match access {
        RootAccess::ReadOnly => FILE_GENERIC_READ_EXECUTE,
        RootAccess::Writable if metadata.is_dir() => WRITABLE_DIRECTORY_ACCESS,
        RootAccess::Writable => WRITABLE_FILE_ACCESS,
    };
    grant_to_package(target, &AppContainerSid::from_sddl(sid), AccessMask(mask))
        .map_err(|error| HostError::AppContainer(format!("grant {}: {error}", path.display())))
}

/// Remove only ACEs belonging to this exact AppContainer SID.
///
/// This deliberately does not restore a previously captured DACL because that
/// would overwrite unrelated permission changes made after installation.
pub(super) fn revoke_root(
    path: &Path,
    sid: &str,
    original_dacl_control: Option<u16>,
) -> Result<()> {
    use windows::Win32::Security::Authorization::{
        ConvertStringSidToSidW, GetNamedSecurityInfoW, SE_FILE_OBJECT,
    };
    use windows::Win32::Security::{
        ACCESS_ALLOWED_ACE, ACE_HEADER, ACE_REVISION, ACL, ACL_SIZE_INFORMATION,
        AclSizeInformation, AddAce, DACL_SECURITY_INFORMATION, EqualSid, GetAce, GetAclInformation,
        GetSecurityDescriptorControl, InitializeAcl, PSECURITY_DESCRIPTOR, PSID,
    };
    use windows::core::PCWSTR;

    let sid_wide: Vec<u16> = sid.encode_utf16().chain(Some(0)).collect();
    let mut psid = windows::Win32::Security::PSID(std::ptr::null_mut());
    // SAFETY: sid_wide is NUL terminated and `psid` is a valid out pointer.
    unsafe { ConvertStringSidToSidW(PCWSTR(sid_wide.as_ptr()), &mut psid) }
        .map_err(|error| HostError::AppContainer(error.to_string()))?;
    let _sid_guard = LocalGuard(psid.0);

    use std::os::windows::ffi::OsStrExt;
    let path_wide: Vec<u16> = path.as_os_str().encode_wide().chain(Some(0)).collect();
    let mut security_descriptor = PSECURITY_DESCRIPTOR(std::ptr::null_mut());
    let mut dacl: *mut ACL = std::ptr::null_mut();
    // SAFETY: all output pointers and the NUL-terminated path are valid.
    let status = unsafe {
        GetNamedSecurityInfoW(
            PCWSTR(path_wide.as_ptr()),
            SE_FILE_OBJECT,
            DACL_SECURITY_INFORMATION,
            None,
            None,
            Some(&mut dacl),
            None,
            &mut security_descriptor,
        )
    };
    if status.0 != 0 {
        return Err(HostError::AppContainer(format!(
            "GetNamedSecurityInfoW({}) failed: {}",
            path.display(),
            status.0
        )));
    }
    let _descriptor_guard = LocalGuard(security_descriptor.0);
    if dacl.is_null() {
        return Ok(());
    }

    let mut size: ACL_SIZE_INFORMATION = unsafe { std::mem::zeroed() };
    unsafe {
        GetAclInformation(
            dacl,
            (&raw mut size).cast(),
            std::mem::size_of::<ACL_SIZE_INFORMATION>() as u32,
            AclSizeInformation,
        )
    }
    .map_err(|error| HostError::AppContainer(format!("GetAclInformation: {error}")))?;

    // A Vec<u32> gives the DWORD alignment required by ACL structures. The
    // updated ACL can only shrink, so the original used byte count is enough.
    let words = (size.AclBytesInUse as usize).div_ceil(std::mem::size_of::<u32>());
    let mut updated_storage = vec![0u32; words.max(2)];
    let updated_dacl = updated_storage.as_mut_ptr().cast::<ACL>();
    let revision = ACE_REVISION(unsafe { (*dacl).AclRevision } as u32);
    unsafe { InitializeAcl(updated_dacl, (updated_storage.len() * 4) as u32, revision) }
        .map_err(|error| HostError::AppContainer(format!("InitializeAcl: {error}")))?;

    let mut removed = 0u32;
    for index in 0..size.AceCount {
        let mut raw_ace = std::ptr::null_mut();
        unsafe { GetAce(dacl, index, &mut raw_ace) }
            .map_err(|error| HostError::AppContainer(format!("GetAce({index}): {error}")))?;
        let header = unsafe { &*raw_ace.cast::<ACE_HEADER>() };
        let belongs_to_profile = if header.AceType == 0 {
            // `rappct::grant_to_package` emits ordinary ACCESS_ALLOWED_ACE
            // entries. Other ACE kinds are copied byte-for-byte, even if they
            // happen to mention the same SID, because this helper did not add them.
            let allowed = unsafe { &*raw_ace.cast::<ACCESS_ALLOWED_ACE>() };
            let ace_sid = PSID((&raw const allowed.SidStart).cast_mut().cast());
            unsafe { EqualSid(psid, ace_sid) }.is_ok()
        } else {
            false
        };
        if belongs_to_profile {
            removed += 1;
            continue;
        }
        unsafe {
            AddAce(
                updated_dacl,
                revision,
                u32::MAX,
                raw_ace,
                header.AceSize as u32,
            )
        }
        .map_err(|error| HostError::AppContainer(format!("AddAce({index}): {error}")))?;
    }
    if removed == 0 {
        return Ok(());
    }

    let mut control = 0u16;
    let mut descriptor_revision = 0u32;
    unsafe {
        GetSecurityDescriptorControl(security_descriptor, &mut control, &mut descriptor_revision)
    }
    .map_err(|error| HostError::AppContainer(format!("GetSecurityDescriptorControl: {error}")))?;
    let desired_control = original_dacl_control.unwrap_or(control);
    apply_dacl_with_control(&path_wide, updated_dacl, desired_control)?;
    Ok(())
}

pub(super) fn dacl_control(path: &Path) -> Result<u16> {
    use windows::Win32::Security::Authorization::{GetNamedSecurityInfoW, SE_FILE_OBJECT};
    use windows::Win32::Security::{
        ACL, DACL_SECURITY_INFORMATION, GetSecurityDescriptorControl, PSECURITY_DESCRIPTOR,
    };
    use windows::core::PCWSTR;

    use std::os::windows::ffi::OsStrExt;
    let path_wide: Vec<u16> = path.as_os_str().encode_wide().chain(Some(0)).collect();
    let mut security_descriptor = PSECURITY_DESCRIPTOR(std::ptr::null_mut());
    let mut dacl: *mut ACL = std::ptr::null_mut();
    let status = unsafe {
        GetNamedSecurityInfoW(
            PCWSTR(path_wide.as_ptr()),
            SE_FILE_OBJECT,
            DACL_SECURITY_INFORMATION,
            None,
            None,
            Some(&mut dacl),
            None,
            &mut security_descriptor,
        )
    };
    if status.0 != 0 {
        return Err(HostError::AppContainer(format!(
            "GetNamedSecurityInfoW({}) failed: {}",
            path.display(),
            status.0
        )));
    }
    let _descriptor_guard = LocalGuard(security_descriptor.0);
    let mut control = 0u16;
    let mut revision = 0u32;
    unsafe { GetSecurityDescriptorControl(security_descriptor, &mut control, &mut revision) }
        .map_err(|error| {
            HostError::AppContainer(format!("GetSecurityDescriptorControl: {error}"))
        })?;
    Ok(control)
}

fn apply_dacl_with_control(
    path: &[u16],
    dacl: *mut windows::Win32::Security::ACL,
    original: u16,
) -> Result<()> {
    use windows::Win32::Security::{
        DACL_SECURITY_INFORMATION, InitializeSecurityDescriptor,
        PROTECTED_DACL_SECURITY_INFORMATION, PSECURITY_DESCRIPTOR, SE_DACL_AUTO_INHERIT_REQ,
        SE_DACL_AUTO_INHERITED, SE_DACL_PROTECTED, SECURITY_DESCRIPTOR,
        SECURITY_DESCRIPTOR_CONTROL, SetFileSecurityW, SetSecurityDescriptorControl,
        SetSecurityDescriptorDacl, UNPROTECTED_DACL_SECURITY_INFORMATION,
    };
    use windows::core::PCWSTR;

    let mut descriptor = SECURITY_DESCRIPTOR::default();
    let descriptor_pointer = PSECURITY_DESCRIPTOR((&raw mut descriptor).cast());
    unsafe { InitializeSecurityDescriptor(descriptor_pointer, 1) }.map_err(|error| {
        HostError::AppContainer(format!("InitializeSecurityDescriptor: {error}"))
    })?;
    unsafe { SetSecurityDescriptorDacl(descriptor_pointer, true, Some(dacl), false) }
        .map_err(|error| HostError::AppContainer(format!("SetSecurityDescriptorDacl: {error}")))?;

    let control_mask = SECURITY_DESCRIPTOR_CONTROL(
        SE_DACL_PROTECTED.0 | SE_DACL_AUTO_INHERITED.0 | SE_DACL_AUTO_INHERIT_REQ.0,
    );
    let original_bits = SECURITY_DESCRIPTOR_CONTROL(original & control_mask.0);
    unsafe { SetSecurityDescriptorControl(descriptor_pointer, control_mask, original_bits) }
        .map_err(|error| {
            HostError::AppContainer(format!("SetSecurityDescriptorControl: {error}"))
        })?;
    let mut information = DACL_SECURITY_INFORMATION;
    if original & SE_DACL_PROTECTED.0 != 0 {
        information |= PROTECTED_DACL_SECURITY_INFORMATION;
    } else {
        information |= UNPROTECTED_DACL_SECURITY_INFORMATION;
    }
    let applied =
        unsafe { SetFileSecurityW(PCWSTR(path.as_ptr()), information, descriptor_pointer) };
    if !applied.as_bool() {
        return Err(HostError::AppContainer(format!(
            "SetFileSecurityW(DACL revoke) failed: {}",
            std::io::Error::last_os_error()
        )));
    }
    Ok(())
}

struct LocalGuard(*mut core::ffi::c_void);

impl Drop for LocalGuard {
    fn drop(&mut self) {
        if !self.0.is_null() {
            // SAFETY: these buffers are allocated by LocalAlloc-family Win32
            // security APIs and released exactly once.
            unsafe {
                let _ = windows::Win32::Foundation::LocalFree(Some(
                    windows::Win32::Foundation::HLOCAL(self.0),
                ));
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::{WRITABLE_DIRECTORY_ACCESS, WRITABLE_FILE_ACCESS};

    #[test]
    fn writable_grants_cannot_change_acl_or_owner() {
        const WRITE_DAC: u32 = 0x0004_0000;
        const WRITE_OWNER: u32 = 0x0008_0000;
        const SECURITY_MUTATION: u32 = WRITE_DAC | WRITE_OWNER;
        assert_eq!(WRITABLE_DIRECTORY_ACCESS & SECURITY_MUTATION, 0);
        assert_eq!(WRITABLE_FILE_ACCESS & SECURITY_MUTATION, 0);
    }
}
