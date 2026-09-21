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

Authenticated `GET /v1/personal-assistant/status` returns versioned status and
`authorization_available: false`. `ready` means configured, not connected or accepted.
`GET /v1/personal-assistant/accounts?session_id=<uuid>` returns only account ID/status after
validating the persisted owner session. There are deliberately no OAuth HTTP completion routes
yet. Bare LAN HTTP must not become an authorization-code handoff through spoofable proxy headers.

Internal OAuth coordination uses server-generated state and PKCE S256, a five-minute lifetime,
a bound session and an exact `http://127.0.0.1:<port>/oauth/google` native callback. Pending and
in-flight flows together are limited to 16. Wrong-session attempts cannot consume a flow;
completion is single-use, cancellation stops in-flight exchange, and Runtime shutdown cancels
active flows. An already committed account is not implicitly revoked by shutdown/cancellation.

The dedicated `config_dir/personal-assistant-secrets.json` is never shared with other provider
secrets. Startup reconciliation is asynchronous; shutdown waits for its cancellation and closes
the HTTP adapter. A cleanup exception is reported as `cleanup_failed` without echoing secrets.

Next: native temporary callback listener, trusted transport admission for authorization HTTP,
control-center calendar selection/query UI, bounded recurring-instance queries, and Runtime Skill
registration. OAuth consent, native interaction and real Google data remain unverified.
