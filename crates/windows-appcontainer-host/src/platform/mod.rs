#[cfg(not(windows))]
mod unsupported;
#[cfg(windows)]
mod windows;

use crate::cli::Command;
use crate::error::Result;

pub(crate) fn execute(command: Command) -> Result<i32> {
    #[cfg(windows)]
    {
        windows::execute(command)
    }
    #[cfg(not(windows))]
    {
        unsupported::execute(command)
    }
}
