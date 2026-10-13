# ADR 0082: Single-owner Runtime password login

- Status: Accepted
- Date: 2026-10-13
- Extends: ADR 0053

## Decision

Web and desktop users may configure a custom password after authenticating with
an existing Runtime credential. A subsequent change requires the current password
and the loaded revision. This is one owner's management credential, without user
accounts or changes to channel ingress authorization.

Passwords are exchanged at `POST /v1/runtime/password/login` for random 24-hour
Bearer credentials. The existing HTTP guard, session-scoped WebSocket ticket flow,
native connection persistence and browser session storage consume those credentials.
The client never persists the password. On restoring an expired or restarted
Runtime session, the connection gate asks for login again.

Runtime owns password verification. Its existing atomic mode-0600 secret store
persists only a versioned, salted scrypt hash in `runtime-password.json`, separately
from the database, model credentials and bootstrap tokens. Hashing runs in the
API worker pool, without blocking the media event loop. Parameters use the
[OWASP scrypt profile](https://cheatsheetseries.owasp.org/cheatsheets/Password_Storage_Cheat_Sheet.html):
N=32768, r=8, p=3. New passwords contain 12-128 Unicode characters; spaces are
significant. API validation errors never echo submitted secret values.

Login and current-password checks share a global five-attempt/60-second window,
which also works behind proxies. Sessions are bounded at 128 entries and stored
only as token digests in process memory. A password change revokes prior password
credentials and unconsumed WebSocket tickets. Runtime restart also revokes password
credentials. Already established streams finish through their existing lifecycle;
the next authenticated HTTP operation or reconnect requires a valid credential.

Host/Origin checks still precede password login; only its exact POST path bypasses
Bearer authentication. Password setup requires an authenticated credential even
when the development guard is disabled. `admin_events` retains its separate admin
token requirement. Existing bootstrap/admin tokens remain compatible and are not
replaced or rotated by this feature.

## Consequences

The shared connection settings panel configures and changes passwords. A remote
client replaces its own credential with the one returned by a successful change;
other clients log in again. Hidden panels clear password drafts and abort requests.
Writes are fenced to their original Runtime identity and revision.

The owner must connect once with an existing access token before setting a password.
Forgotten-password recovery is a host operation: stop Runtime, preserve its state,
remove only `config/runtime-password.json`, restart, authenticate with the existing
operator credential and configure a new password. Clients have no anonymous reset
or password-disable API.
