# Personal assistant development status

## Unified agenda candidate (2026-09-28, deployed for acceptance)

The `mubai/unified-agenda` branch extends the merged read-only Google and Apple
features. It adds a combined today/next 7 days/next 30 days panel, Google Calendar
event and Google Tasks list/item read/write routes, a persisted default calendar
and task-list destination, and confirmation-gated agenda management. Runtime
migration 35 adds task-list selections, write destinations and a display label for
multiple Google accounts. Existing accounts and selections remain in place.

To test with another real Google account, enable the **Google Tasks API** in the
same Google Cloud project. In the desktop Personal Assistant panel, use **Upgrade
this account** on the existing read-only account, sign into the _same_ Google
account and grant Calendar event write and Tasks scopes. The server compares the
primary calendar identity before replacing its stored refresh token. If consent
fails or the account differs, the old account stays connected with its prior
capabilities. Select a calendar and a task list explicitly, then mark writable
targets as defaults. Test in a disposable calendar/list and verify each create,
edit, complete and delete in the corresponding Google application. Do not retry
an uncertain write before checking the source. Google Tasks due dates have no
time-of-day; timed ringing still uses the ChatWaifu task/alarm scheduler.

Apple sources continue to require the paired Mac, EventKit permissions and online
device receipts. Revoking the device or deselecting its source clears the matching
default write destination. `queued` means the device has not confirmed the write.
Live provider write evidence is recorded below. Visual use of the combined panel,
chat-originated writes and offline notification inbox behavior still need separate
acceptance.

The Linux source service was backed up at
`/home/mubai/chatwaifu-server/backups/unified-agenda-20260927T172613Z`
with a consistent SQLite backup before migration. Four older server source files
for MCP settings/transport were synchronized to the already merged `main` baseline;
the first restart exposed their mismatch, so the old service was restored before
reapplying this branch. The final Runtime is active with migration 35, direct TLS
and the 18443 TCP forwarding socket active. Unauthenticated assistant status gives
401 with certificate verification passing; authenticated status gives 200/`ready`
and both new Skills are listed. The single previously connected Google account
remained connected; there were zero default write destinations at deployment before
the user's later selection. Deployment checks alone did not establish real provider
read/write or a visual desktop result.

On 2026-09-28, the owner selected Google Calendar and Google Tasks destinations and
completed expanded OAuth consent. Authenticated HTTPS status reported `ready`; the
account reported both calendar and Tasks write capability. Unique temporary items
were created in the selected sources through the live Runtime. Google Calendar and
Tasks provider queries read back the created and modified items; Tasks completion
read back as `completed`. Provider deletes succeeded and subsequent queries found
zero matching test items. A separate create via the unified `/agenda/items` route
used the default Google Tasks destination, was read back and was deleted.

The paired Mac was online and exposed the user-selected writable Apple “个人”
calendar and “Reminders” list. Actual EventKit device receipts and list operations
confirmed create, update and delete in both sources and completion of the test
reminder. Both sources returned zero matching test items after cleanup. This is
provider/device readback, not a visual check inside Google Calendar, Google Tasks,
Apple Calendar or Apple Reminders. The Google default destinations were not changed
during Apple acceptance.

The live device exposed 11 selected Apple sources, with its Reminders list after
the old overview's first-eight cutoff. The desktop overview now reserves room for
both calendar and reminder sources and prioritizes any default Apple destinations
within its eight-source request bound. It also excludes completed Apple reminders
and reminders due beyond the active day range, matching Google Tasks behavior.
Frontend typecheck, lint and desktop bundle passed. The user refreshed the
running desktop's next-seven-days overview and confirmed that unfinished Apple
Reminders appear without flooding the view with completed items.

Google Stage A has a user-confirmed Google connection and a selected calendar on the HTTPS deployment. The settings UI, authenticated event API, and direct `calendar.read` invocation returned the existing event `测试`. After the 2026-09-27 repair, the user confirmed the desktop-pet conversation could read the schedule.

The Runtime owns the Google adapter, account service, OAuth coordinator and startup secret
cleanup. The feature defaults off. The following server configuration enables the subsystem;
without a client ID it reports `unconfigured` and makes no Google requests:

```toml
[personal_assistant]
enabled = true
```

The Desktop OAuth client is configured server-side with `google_client_id` and optional
`google_client_secret` (a protected configuration value). Corresponding environment overrides
are `CHATWAIFU_PERSONAL_ASSISTANT__GOOGLE_CLIENT_ID` and
`CHATWAIFU_PERSONAL_ASSISTANT__GOOGLE_CLIENT_SECRET`. Never put secrets in Vite variables.
The desktop provides authorization, calendar selection and read-only query controls.

Authenticated `GET /v1/personal-assistant/status` reports the state and whether this
connection admits authorization. `ready` means configured, not connected or accepted.
`GET /v1/personal-assistant/accounts?session_id=<uuid>` returns only account ID/status after
validating the persisted owner session.

OAuth begin/complete/cancel endpoints now exist, but require a configured exact
`google_oauth_https_origin` and **direct HTTPS to the Runtime**. All `Forwarded` and
`X-Forwarded-*` headers are rejected even if the ASGI server interpreted them. TLS-terminating
reverse proxies and bare LAN HTTP are not admitted by this initial implementation. Do not
change the existing deployment or bypass certificate validation merely to enable OAuth.
The deployment recorded below now has verified HTTPS and a user-provided Desktop OAuth client.

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

Calendar selection/query UI, bounded recurring-instance queries and Runtime Skill registration are implemented. Google connection and an existing event have been observed; dialog lookup is tracked separately below.

Validation: Rust compilation and two native URL/socket checks passed; desktop build and TypeScript
checks passed; three status/transport/validation HTTP checks passed. Those earlier checks
alone did not establish Mac browser consent, native visual acceptance, direct TLS
deployment, or real Google acceptance; later evidence is recorded below.

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
Without OAuth configuration they return `personal_assistant_not_configured`.

## Operator prerequisites for first Google connection

1. In Google Cloud, select/create a project and enable Google Calendar API.
2. Configure Google Auth Platform branding and audience. A personal Gmail app
   uses External; in Testing add the signing-in account as a test user.
3. Create an OAuth client of type Desktop app and download its JSON. Give the
   operator the local file path rather than pasting secrets into chat. The Runtime
   uses the downloaded installed.client_id/client_secret; never commit the JSON.
4. Provide a client-trusted HTTPS Runtime origin. Current OAuth admission requires
   end-to-end TLS to Runtime, no forwarded headers; ordinary nginx TLS termination
   with forwarded headers is not yet supported. Existing LAN HTTP chatting can
   continue independently. Certificate trust/login steps require user participation.
5. Sign in via the native system-browser flow, select permitted calendars, then
   grant calendar.read when asked by the Runtime Skill permission prompt.

Google testing-mode refresh tokens for external applications with calendar scopes
can expire after seven days; testing is suitable for initial acceptance, not a
promise of unattended permanent authorization. Broader publishing/verification is
separate from creating a desktop OAuth client.

calendar.read 查询工具已注册，10 项工具边界/路由检查通过，Ruff/Pyright 和前端构建通过。agy High 完成只读审查。服务器补丁曾因旧版参数上下文错位导致短暂启动失败，已修正参数位置并重新编译；恢复后健康接口 200，技能列表显示 calendar.read enabled。尚无 Google 实账号调用验收。

## 2026-09-22 HTTPS deployment

The server now uses `https://mubai.website:18443`, with a user-provided Desktop
OAuth client stored in a private server environment file. Runtime terminates TLS;
a systemd socket forwards TCP without decrypting it. See source-server.md for
launcher settings. Authenticated health and assistant status return 200, with
`state=ready` and `authorization_available=true`. Unauthenticated assistant status
returns 401. Certificate verification passed through the domain and direct LAN
routing. The existing LAN HTTP entry still returns healthy status, but correctly
reports `authorization_available=false`.

In the Mac desktop connection settings use the HTTPS origin and the existing
access token. Open Personal Assistant, connect an account in the system browser,
then discover/select calendars and query the next seven days. The user later
completed Google login and selected a calendar. These deployment checks alone
did not verify real calendar access, permission prompts, or voice queries.

### Authorization returned but no account connected

The native loopback page only acknowledges receipt of Google's browser callback;
the server must still reach `oauth2.googleapis.com` to exchange the code and
Google Calendar APIs to read calendars. On 2026-09-22 direct server probes to
both Google endpoints timed out, while the inbound HTTPS Runtime remained usable.
This is an outbound connectivity issue; a successful browser redirect is not an
account acceptance result. The adapter sets `trust_env=False`, so setting shell
`HTTPS_PROXY` alone does not route it. A working server VPN/TUN route or a future
explicit adapter proxy configuration is required. Do not replay an old OAuth code.

The desktop now announces the server exchange phase, explains `transport_error`
and request timeouts, and refreshes accounts after successful completion. Personal
assistant controls and notices use scoped settings styles. Desktop build/typecheck
and targeted ESLint passed; visual acceptance and real authorization after network
repair remain pending.

## 2026-09-27 dialog calendar mismatch

The selected calendar contains `测试` at 06:00–07:00 Asia/Shanghai on September 27.
The authenticated event API returned it for the calendar's local day, but returned
no events for 00:00–24:00 UTC on September 27. The first desktop question did run
`calendar.read` and received an empty result; its exact tool arguments were audit
redacted, so the UTC window is a plausible cause, not a recovered fact. The
follow-up “没有一个叫测试的吗” did not run the calendar tool at all.

The Runtime Skill now accepts a relative period or explicit calendar dates from
the model and calculates each selected Google calendar's timezone-aware window on
the server. An unqualified query defaults to seven local calendar days starting
at today's midnight. The model-facing schema no longer asks the model to invent
UTC start/end timestamps; a missing calendar timezone fails explicitly instead
of silently querying a UTC day. An immediate local desktop correction can offer the
previous turn's read-only tool topic again; write tools are excluded. Selection,
owner scope, permissions, bounded query limits and stale-result fences remain in
force. The scoped server files were backed up in
`chatwaifu-server/backups/calendar-window-20260927`, deployed, syntax checked,
and restarted. Authenticated HTTPS health returned 200; the skill registry
reported `calendar.read` version 1.1.0 enabled. A direct invocation in the
existing authorized session returned one event, including `测试`, from an
`Asia/Shanghai` window. The user subsequently reported that the desktop-pet dialog
succeeded; exact phrasing and long-term behavior were not independently captured.

## Unified agenda delivery outcomes

The server keeps the existing two-minute alarm and one-hour reminder delivery
windows. An occurrence that expires before the paired device confirms display is
recorded as missed; a displayed occurrence that expires without a stop or snooze
is recorded as unhandled. Neither is replayed after the window. The desktop
persists display receipts and retries them on reconnect, including after expiry,
so a receipt lost during an outage can correct a missed label to unhandled. The
unified agenda shows these records in an inbox; dismissing one only clears that
record and does not mutate the source event, task, or recurring reminder rule.
If stop/snooze acknowledgement fails, the desktop stops ringing locally and asks
the user to retry while the delivery is active. This action is not yet durably
queued for replay; expiry leaves an unhandled record that can be dismissed.
