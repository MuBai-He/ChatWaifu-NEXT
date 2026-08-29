# ADR 0025: ChatWaifu-owned Windows AppContainer launcher for stdio Runtime Skills

- Status: Accepted
- Date: 2026-08-29
- Validation state: Developer acceptance passed on real Windows; signed release packaging pending

## Context

ADR 0018 requires untrusted local stdio MCP servers to fail closed unless an operating-system
sandbox can enforce their filesystem and network policy. ADR 0021 additionally binds approval to an
immutable permission subject and separates read-only plugin packages from writable plugin data.
Seatbelt and bubblewrap provide this boundary on macOS and Linux, but a cleaned environment or a
Windows process group is not an AppContainer and must not be reported as one.

Windows process creation needs one trusted component to coordinate the AppContainer profile and SID,
filesystem ACLs, `STARTUPINFOEX`, inherited stdio handles, and Job Object membership. Keeping this in
Python `ctypes` would duplicate subtle Win32 ownership and x64 ABI rules inside Runtime. Reusing a
generic command-line sandbox unchanged would instead make ChatWaifu depend on another program's
profile naming, cleanup timing, policy surface, and security claims.

The MCP SDK still owns the stdio protocol and request lifecycle. The Windows component is an
infrastructure adapter that enforces an already-approved immutable `ExecutionPlan`; it is not a new
permission broker or a place for provider, memory, or character policy.

## Decision

ChatWaifu ships a trusted, offline, x64 Windows executable named
`chatwaifu-appcontainer-host.exe`. Runtime selects it only for Windows stdio profiles whose sandbox
plan requires or prefers an enforcing native backend. The executable receives an absolute child
executable, argument vector, working/data directory, explicit read-only roots, network policy,
resource limits, and an opaque sandbox subject. It launches the child directly with `CreateProcessW`;
it never invokes a shell or interprets MCP bytes.

The implementation pins `rappct` for AppContainer profile/SID and ACL primitives and follows the
reviewed `sandboxrs-windows` launch pattern for process containment. One `STARTUPINFOEX` creation
atomically supplies the AppContainer security capabilities, the declared stdin/stdout/stderr handle
allowlist, and `PROC_THREAD_ATTRIBUTE_JOB_LIST`, so the child cannot execute before Job membership is
established. ChatWaifu owns the final policy, validation, lifecycle, error model, and tests rather
than treating either upstream crate as a complete sandbox product.

### Profile and filesystem lifecycle

The profile name is a deterministic, non-secret digest of a namespaced local execution owner
(`plugin:<id>` or `mcp-connection:<uuid>`). One owner therefore reuses one stable AppContainer SID
across executions. The immutable `ExecutionPlan` still binds package fingerprints, capability
policy, adapter targets, and MCP-connection revisions before launch; changing a connection command
or disabling, updating, or removing an owner revokes its profile under the same operation lease
before the new identity can execute. A profile is created lazily on first execution and startup
reconciliation revokes owners that are disabled, removed, remote-only, or no longer sandboxed.

Before adding an AppContainer SID ACE, the host writes and flushes a versioned manifest beneath a
trusted state root. The manifest records the profile, derived SID, and normalized roots granted to
that SID. Startup reconciliation compares these manifests with active permission subjects and
repairs or revokes abandoned entries. Revocation removes only ACEs owned by that exact SID before
deleting its profile and manifest; it does not restore a historical whole DACL or depend on the
launcher exiting normally. The package and approved runtime/interpreter roots receive read/execute
access; the per-plugin data/working root receives write access. Trusted state, secret stores, and
unrelated plugin roots are never granted.

Directory inheritance is not assumed to cover every plugin file. On first grant or access upgrade,
the host walks ordinary descendants without following reparse points and explicitly grants protected
DACL objects such as native Python extensions. Completion is journaled only after the walk succeeds,
so a crash retries it; later launches of the same stable profile grant the roots without rescanning
the runtime. Revocation removes the exact SID from both roots and descendants, restores security-
relevant root inheritance control, and preserves unrelated ACEs added before or during the profile
lifecycle.

All paths are absolute, canonicalized, covered by an explicit root, and rejected when they overlap
trusted state or cross a reparse point. A child executable outside the approved roots, a corrupt or
SID-mismatched manifest, a missing helper, or a failed ACL/profile operation fails closed.

### Process, stdio, cancellation, and limits

The host is byte-transparent on stdin and stdout so the official MCP stdio transport remains the
protocol owner. Diagnostics use stderr only. The child inherits only its three declared pipe handles;
parent-only and unrelated inheritable handles are excluded with `PROC_THREAD_ATTRIBUTE_HANDLE_LIST`
and closed promptly after launch.

Every child enters a non-breakaway Job Object before it can execute. The Job enables
`JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`, a bounded active-process count, and a per-job memory limit.
CPU limits may be added only when the selected Windows API actually enforces them and the Runtime
snapshot names that exact limit. Runtime reports `windows_appcontainer`, the effective network
policy, and only limits confirmed by the host; it never infers enforcement from requested values.

Cancellation first closes MCP input and allows the bounded protocol shutdown path. When the deadline
expires or the host is terminated, closing or explicitly terminating the Job ends the entire child
tree. Late output cannot escape the existing Skill Run and generation guards. Profile and ACL
cleanup remains lifecycle/reconciliation work, not a destructor side effect of each child process.

### Network policy

The default `deny` plan creates the AppContainer with no network capability. `allow` is an explicit
permission-plan choice and grants only the capabilities required for the declared policy. There is
no global loopback exemption and no claim that AppContainer can enforce a host-loopback-only mode;
that policy remains unavailable unless a separately reviewed broker supplies it. Where a plugin
needs narrow network access, a ChatWaifu broker/proxy is preferred to giving the child general
internet or private-network capabilities.

The first release uses a regular AppContainer rather than Low Privilege AppContainer because common
Python and native runtimes need deliberate read/execute grants. LPAC is a compatible hardening step
only after runtime compatibility and access checks have real Windows coverage.

### Packaging and validation state

The artifact is x64 (`PE` machine `0x8664`) and is produced beside the x64 desktop/runtime host,
which discovers that sibling executable without a user-configured path. It may execute through
Windows x64 emulation on an ARM64 machine, but that does not make it an ARM64 build. Unsupported
architectures, a missing/wrong-architecture binary, or an unavailable AppContainer API fail closed.

On 2026-08-29 the implementation passed its developer acceptance on an unelevated Windows 11 Pro
10.0.26200 ARM64 VM while both the helper and Python child ran as x64 under Windows emulation. The
helper PE header was independently checked as `0x8664`. Ten real-system cases, split across one
eight-case full run and two added limit/handle cases, covered:

- verify the built helper is `0x8664`, needs no administrator rights, and the child token carries
  the expected AppContainer SID;
- prove package/runtime roots (including a protected native Python extension) are readable but
  immutable, the data root is writable, and trusted state plus an uncovered sibling path are denied;
- prove default network denial covers an external private/LAN peer and loopback, while explicit
  private/LAN allow is reported and reaches the external peer without adding a loopback exemption;
- prove an unrelated inheritable parent event does not cross the handle allowlist and normal MCP
  initialize, tool call, stderr, and EOF preserve protocol framing;
- prove process-count and Job-memory limits, kill-on-host-exit, parent-stdin EOF cancellation, and
  descendant cleanup;
- damage an active root grant, reconcile it, then revoke the durable journal and verify owned
  root/descendant ACE and profile cleanup without changing unrelated DACL entries.

This validates the enforcing development backend and allows Runtime to select it whenever the
sibling helper is present. It is not a claim that a signed installer or frozen Windows Runtime
sidecar has been produced. Release acceptance still requires building both sibling executables in
the packaging pipeline, installing them into their final layout, repeating the smoke suite from the
installed app, and adding real junction/reparse probes, broader mutation-point fault injection, and
long-running multi-subject concurrency tests.

## Consequences

Windows gains an enforceable design compatible with the existing Runtime Skill permission and MCP
transport boundaries, while the platform-specific Win32 surface stays out of Python and the Web UI.
Stable profiles avoid repeated create/delete races, and the durable manifest makes forced termination
recoverable. The additional trusted binary and ACL state require supply-chain notices, Windows-only
tests, startup reconciliation, explicit uninstall cleanup, and signed release packaging.

This decision does not approve arbitrary third-party launchers or convert trusted
`sandbox_mode=disabled` profiles into sandboxed ones. A backend becomes user-visible as enforced only
after its own preparation succeeds. Developer acceptance and signed installed-release acceptance are
reported separately.

## Alternatives

Use Arapuca unchanged. Its compact launcher demonstrates AppContainer, Job, handle-list, and DACL
techniques, but its cleanup is tied to Rust destructor/normal process teardown. Terminating the outer
launcher can skip DACL/profile rollback, its Windows timeout is not an independent lifecycle guard,
and per-run profile behavior conflicts with durable MCP daemons.

Use Microsoft MXC/WXC unchanged. MXC provides convenient ProcessContainer, stdio, DACL, and network
plumbing plus x64/ARM64 executables, but its own release documentation describes the project as an
early preview and warns that MXC profiles are not security boundaries. That is incompatible with
labeling untrusted plugin execution approved today.

Use `rappct` alone. It supplies useful profile, capability, ACL, stdio, and Job primitives, but its
high-level launch path does not by itself provide ChatWaifu's creation-time Job-list containment,
explicit handle allowlist, process-count policy, durable profile journal, or plugin lifecycle reconciliation.

Implement all Win32 calls through Python `ctypes`; require OCI on every Windows installation; or
silently downgrade to environment cleanup. The first increases ABI and ownership risk, the second
adds an unsuitable deployment dependency to the desktop demo, and the third violates fail-closed
and truthful-capability requirements.

## References

- [Microsoft: Implementing an AppContainer](https://learn.microsoft.com/en-us/windows/win32/secauthz/implementing-an-appcontainer)
- [Microsoft: UpdateProcThreadAttribute](https://learn.microsoft.com/en-us/windows/win32/api/processthreadsapi/nf-processthreadsapi-updateprocthreadattribute)
- [Microsoft: Job Objects](https://learn.microsoft.com/en-us/windows/win32/procthread/job-objects)
- [Microsoft: Pipe handle inheritance](https://learn.microsoft.com/en-us/windows/win32/ipc/pipe-handle-inheritance)
- [rappct](https://github.com/cpjet64/rappct)
- [sandboxrs-windows](https://github.com/TarunKurella/sandboxrs-windows)
- [Arapuca](https://github.com/LeGambiArt/arapuca)
- [Microsoft MXC](https://github.com/microsoft/mxc)
