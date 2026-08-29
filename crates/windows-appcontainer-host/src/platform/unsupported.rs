use crate::cli::Command;
use crate::error::{HostError, Result};

pub(super) fn execute(_command: Command) -> Result<i32> {
    Err(HostError::UnsupportedPlatform)
}
