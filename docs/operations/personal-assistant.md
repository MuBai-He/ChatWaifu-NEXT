# Personal assistant development status

Google Stage A is not yet ready for end-user authorization. No real account has been connected.

The Runtime owns the Google adapter, account service, OAuth coordinator and startup secret
cleanup. The feature defaults off. The following server configuration enables the subsystem;
without a client ID it reports `unconfigured` and makes no Google requests:

```toml
[personal_assistant]
enabled = true
```

The future Desktop OAuth client is configured server-side with `google_client_id` and optional
`google_client_secret` (a protected configuration value). Corresponding environment overrides
are `CHATWAIFU_PERSONAL_ASSISTANT__GOOGLE_CLIENT_ID` and
`CHATWAIFU_PERSONAL_ASSISTANT__GOOGLE_CLIENT_SECRET`. Never put secrets in Vite variables.
Do not configure credentials just to test the currently incomplete UI.

Authenticated `GET /v1/personal-assistant/status` reports the state and whether this
connection admits authorization. `ready` means configured, not connected or accepted.
`GET /v1/personal-assistant/accounts?session_id=<uuid>` returns only account ID/status after
validating the persisted owner session.

OAuth begin/complete/cancel endpoints now exist, but require a configured exact
`google_oauth_https_origin` and **direct HTTPS to the Runtime**. All `Forwarded` and
`X-Forwarded-*` headers are rejected even if the ASGI server interpreted them. TLS-terminating
reverse proxies and bare LAN HTTP are not admitted by this initial implementation. Do not
change the existing deployment or bypass certificate validation merely to enable OAuth.
A verified HTTPS deployment and user-owned Google client setup remain integration work.

The desktop control center now has a personal assistant section with missing-configuration
and transport explanations, an account connection action, and cancellation. The Mac host
opens only Google's system-browser authorization URL, binds an ephemeral IPv4 loopback
listener, validates callback state, caps request size and wait time, and closes the listener
on cancellation, window close, or app exit. Windows/Linux system-browser launch is currently
unsupported and returns a clear error. No local Python server is added to the thin client.

Internal OAuth coordination uses server-generated state and PKCE S256, a five-minute lifetime,
a bound session and an exact `http://127.0.0.1:<port>/oauth/google` native callback. Pending and
in-flight flows together are limited to 16. Wrong-session attempts cannot consume a flow;
completion is single-use, cancellation stops in-flight exchange, and Runtime shutdown cancels
active flows. An already committed account is not implicitly revoked by shutdown/cancellation.

The dedicated `config_dir/personal-assistant-secrets.json` is never shared with other provider
secrets. Startup reconciliation is asynchronous; shutdown waits for its cancellation and closes
the HTTP adapter. A cleanup exception is reported as `cleanup_failed` without echoing secrets.

Next: control-center calendar selection/query UI, bounded recurring-instance queries, and Runtime Skill
registration. OAuth consent, native interaction and real Google data remain unverified.

Validation: Rust compilation and two native URL/socket checks passed; desktop build and TypeScript
checks passed; three status/transport/validation HTTP checks passed. These do not establish actual
Mac browser consent, native visual acceptance, direct TLS deployment, or real Google acceptance.

## 2026-09-22 LAN status deployment

The existing LAN server now includes the assistant lifecycle, migration 33 and
status/OAuth routes. Source integration and a consistent SQLite backup were saved
under `chatwaifu-server/backups/assistant-status-20260922` before the update.
The feature is enabled without Google client credentials. Live authenticated
`/v1/personal-assistant/status` returns HTTP 200 with `state=unconfigured` and
`authorization_available=false`; runtime health and channel listing remain 200.
This verifies deployment/status only, not Google consent, synchronization or queries.
The desktop distinguishes an old backend's 404 from missing configuration and
network/auth failures, provides a bounded status request and manual refresh.

## Calendar selection and live query

After OAuth, refresh accounts in desktop settings, discover an account's calendars,
explicitly select permitted calendars, then query the next seven days. The server
accepts timezone-aware windows up to 31 days and 1000 results. Google expands
recurring instances; these reads do not advance the incremental sync cursor.
The service rejects non-owner sessions and unselected calendars and discards late
results after selection/account revision changes. Responses identify live Google
results; failed queries do not render cached events as current.

The LAN server includes `/calendars`, `/calendars/selection` and `/events` routes.
Without OAuth configuration they return `personal_assistant_not_configured`;
Google consent, actual event rendering and dialog Skill integration remain pending.
