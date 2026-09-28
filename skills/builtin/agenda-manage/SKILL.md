---
id: agenda.manage
version: 1.0.0
name: Account-owned agenda mutations
---

# Manage agenda items

The Runtime supplies the trusted owner session. Never provide a session ID or
invent an account, calendar, task list, item ID or etag. Creation uses the user's
explicit default calendar or reminder list. If none is set, ask the user to choose
one in Personal Assistant settings. Calendar events require timezone-aware start
and end times. Google Tasks store a date only; for an exact ringing time, use the
separate schedule capability and do not claim the two items are linked yet.

For Google edits, completion and deletion, first read the item and pass its exact
account_id, collection_id, item_id and etag. A conflict means refresh before
trying again. Apple edits use apple.manage and its device receipt. When creation
returns queued, tell the user that the Mac has not yet confirmed the write. On an
uncertain outcome, check the original provider before considering a new create.
All writes require the normal Runtime Skill permission and confirmation flow.
