//! Phase 0 compile target. Tauri host behavior begins in Phase 3.

/// Architectural role of the future desktop crate.
pub const HOST_ROLE: &str = "os-capabilities-and-sidecar-management";

#[cfg(test)]
mod tests {
    use super::HOST_ROLE;

    #[test]
    fn host_role_does_not_claim_character_logic() {
        assert_eq!(HOST_ROLE, "os-capabilities-and-sidecar-management");
    }
}
