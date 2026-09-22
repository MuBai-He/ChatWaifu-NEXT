# ADR 0056: Explicit headless channel credential vault

Status: Accepted

## Context

Remote channel authorization currently requires a desktop OS keyring. Headless
Linux returns `channel_secure_store_unavailable` before requesting a QR code.
The server must run without a desktop login, while desktop installations retain
Keychain, Credential Manager, or Secret Service/KWallet by default.

## Decision

Add `channel_credential_backend = "encrypted_file"` as an explicit POSIX server
option; default Settings remains `keyring`. The source-server template opts in.
Existing installations require an explicit configuration update; never silently
fall back when a desktop keyring fails. The adapter implements the existing
ChannelCredentialStore port. No frontend token storage or protocol change.

Encrypt the full map using cryptography Fernet authenticated encryption. Store
ciphertext in data_dir/channel-vault and a generated key in
config_dir/channel-vault-key. Directories are 0700; files are 0600, owner checked,
regular and not symlinked/hardlinked. Serialize through flock; replace files
atomically with file and directory fsync. Finish thread I/O before surfacing task
cancellation. Missing keys for existing ciphertext, invalid permissions, and
corruption fail closed, without regeneration or overwriting existing data.

## Threat boundary and operation

This protects ciphertext-only backups and separates keys from application data.
It does not protect against the Runtime account or root reading both files.
Unattended startup necessarily gives the service access to its key. Back up the
key separately, preferably offline; loss requires reauthorization, not automatic
key rotation. Do not copy desktop credentials automatically. Existing desktop
installations may opt in on Linux, but default to an available OS keyring.

Windows continues using Credential Manager. This adapter is POSIX-only; selecting
it on Windows reports unavailable rather than storing plaintext. Full Linux
native UI acceptance is a separate task from Linux server operation.
