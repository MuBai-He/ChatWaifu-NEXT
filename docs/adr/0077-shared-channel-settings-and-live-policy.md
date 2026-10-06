# ADR 0077: Shared channel settings and persisted operator policy

- Status: Accepted
- Date: 2026-10-06
- Scope: Existing WeChat and QQ features, local/remote operator clients.
- Supersedes: Host-file-only editing of QQ owner public reads and group discussion budgets; existing admission and audience rules remain authoritative.

## Decision

Web and Desktop use `features/channels-settings` and common settings primitives.
Web has `/settings/channels`; Desktop retains its Channels category. Neither
settings entry owns realtime playback, event/audio sockets or model generation.
Product dependency gates continue to prohibit Desktop surfaces in the Web bundle.

Keep connection identity, credentials, owner pairing and group audience authority
separate from presentation and capability settings. Existing versioned connection,
group, proactive and media APIs remain responsible for their own settings. Expose
the missing connection presentation controls and group requested-voice field in
UI; never derive permission from editable labels, quoted messages or model prose.

Add typed `ChannelRuntimePolicy` and operator-only GET/PUT `/v1/channels/settings`.
SQLite migration 44 stores one non-secret policy and monotonically increasing CAS
revision. Before the first UI save, public-web/group defaults come from TOML/env;
afterward the saved policy takes precedence on restart. UI receives selected
service names, not provider keys or endpoints. Channel ingress tokens do not
authorize this operator API.

Runtime domain consumers use live permission probes, independent of HTTP or DB
implementations. QQ private tool exposure and execution both check current policy;
private voice is checked again before media publication and the final provider
send. Closing voice input cancels existing QQ preprocessing and blocks new audio
before loading/admission. Group voice still needs its own opted-in route and a
current authorized speaker request; the private voice switch cannot grant or
revoke group authority. Native favorite adds check policy before upload/add and
do not disable classification or scoped library persistence.

Changing group discussion budgets replaces volatile intake caches. Already
admitted generations retain frozen materials/budgets. Turning unrelated private
capabilities off does not clear discussion caches. Raw discussion never becomes a
policy, identity map or wholesale long-term memory write.

Publish a committed settings update under the writer lock, including when the
HTTP caller cancels. Revocations become visible before awaiting cancellation.
Conflicts refresh local probes; clients read back uncertain writes and require a
new operator action rather than replaying a mutation. A PUT returns its own
committed revision. In-process caching assumes one Runtime process per database,
as with the existing container lifecycle; multi-worker policy propagation would
require a separate design.

UI mutations and library reads/writes retain the originating Runtime identity.
Abort/reject late results after endpoint, credential or restart epoch changes.
Group editors preserve fresh audience observation, CAS and explicit confirmation.
Connection option edits retain the existing group pause/revalidation mechanism.

## Consequences and evidence

Existing server releases need this backend migration/API to edit the new global
settings. The UI reports missing APIs instead of inventing editable defaults.
Provider credentials, search/crawler deployment and native QQ login remain host
setup; these controls do not provision those services.

See [the coverage audit](../channel-settings-audit.md) for source checks, isolated
Runtime/browser verification and the separate server/phone acceptance boundary.
