# ruff: noqa: E501
# Migration 48 is deployed; preserve the SQL bytes and checksum.
"""Owner-private knowledge source of truth and durable projection outbox."""

KNOWLEDGE_MIGRATION = """
CREATE TABLE knowledge_owners(owner TEXT PRIMARY KEY, policy_epoch INTEGER NOT NULL DEFAULT 0,
 graph_revision INTEGER NOT NULL DEFAULT 0, history_day TEXT NOT NULL DEFAULT '', history_used
INTEGER NOT NULL DEFAULT 0);
CREATE TABLE knowledge_devices(device_id TEXT PRIMARY KEY, owner TEXT NOT NULL,
 acknowledged INTEGER NOT NULL DEFAULT 0, last_seen INTEGER NOT NULL);
CREATE TABLE knowledge_record_acks(owner TEXT NOT NULL,device_id TEXT NOT NULL,record_id TEXT NOT NULL,
 revision INTEGER NOT NULL,applied_at INTEGER NOT NULL,PRIMARY KEY(owner,device_id,record_id));
CREATE TABLE knowledge_records(owner TEXT NOT NULL, record_id TEXT NOT NULL, kind TEXT NOT NULL,
 room_id TEXT NOT NULL, revision INTEGER NOT NULL, deleted INTEGER NOT NULL DEFAULT 0,
 confirmed INTEGER NOT NULL, payload TEXT NOT NULL, updated_at INTEGER NOT NULL,
 PRIMARY KEY(owner,record_id));
CREATE INDEX knowledge_room ON knowledge_records(owner,room_id,kind,deleted);
CREATE TABLE knowledge_versions(owner TEXT NOT NULL, record_id TEXT NOT NULL, revision INTEGER
NOT NULL,
 payload TEXT NOT NULL, created_at INTEGER NOT NULL, PRIMARY KEY(owner,record_id,revision));
CREATE VIRTUAL TABLE knowledge_fts USING fts5(owner UNINDEXED,record_id UNINDEXED,text);
CREATE TABLE knowledge_policies(owner TEXT NOT NULL, room_id TEXT NOT NULL, revision INTEGER
NOT NULL,
 excluded INTEGER NOT NULL, label TEXT NOT NULL, PRIMARY KEY(owner,room_id));
CREATE TABLE knowledge_receipts(owner TEXT NOT NULL, operation_id TEXT NOT NULL,
 request_hash TEXT NOT NULL, receipt TEXT NOT NULL, PRIMARY KEY(owner,operation_id));
CREATE TABLE knowledge_changes(sequence INTEGER PRIMARY KEY AUTOINCREMENT, owner TEXT NOT NULL,
 kind TEXT NOT NULL, object_id TEXT NOT NULL, payload TEXT NOT NULL, created_at INTEGER NOT NULL);
CREATE INDEX knowledge_pull ON knowledge_changes(owner,sequence);
CREATE TABLE knowledge_conflicts(conflict_id TEXT PRIMARY KEY, owner TEXT NOT NULL,
 record_id TEXT NOT NULL, proposed TEXT NOT NULL, current_revision INTEGER NOT NULL,
 state TEXT NOT NULL DEFAULT 'open', created_at INTEGER NOT NULL);
CREATE TABLE knowledge_jobs(job_id TEXT PRIMARY KEY, owner TEXT NOT NULL, record_id TEXT NOT NULL,
 revision INTEGER NOT NULL, policy_epoch INTEGER NOT NULL, action TEXT NOT NULL,
 state TEXT NOT NULL DEFAULT 'pending', attempts INTEGER NOT NULL DEFAULT 0,
 lease TEXT, lease_until INTEGER NOT NULL DEFAULT 0, next_attempt INTEGER NOT NULL DEFAULT 0,
 historical INTEGER NOT NULL, error_code TEXT, created_at INTEGER NOT NULL);
CREATE INDEX knowledge_queue ON knowledge_jobs(state,next_attempt,historical,created_at);
CREATE INDEX knowledge_record_jobs ON knowledge_jobs(owner,record_id,created_at);
CREATE TABLE knowledge_nodes(owner TEXT NOT NULL, node_id TEXT NOT NULL, kind TEXT NOT NULL,
 label TEXT NOT NULL, room_id TEXT NOT NULL, stable_identity TEXT, merged_into TEXT,
 label_locked INTEGER NOT NULL DEFAULT 0,
 PRIMARY KEY(owner,node_id));
CREATE TABLE knowledge_node_sources(owner TEXT NOT NULL,node_id TEXT NOT NULL,
 record_id TEXT NOT NULL,revision INTEGER NOT NULL,
 PRIMARY KEY(owner,node_id,record_id,revision));
CREATE TABLE knowledge_index_edges(owner TEXT NOT NULL,index_id TEXT NOT NULL,
 relation_id TEXT NOT NULL,PRIMARY KEY(owner,index_id,relation_id));
CREATE TABLE knowledge_relations(owner TEXT NOT NULL, relation_id TEXT NOT NULL,
 source TEXT NOT NULL, target TEXT NOT NULL, predicate TEXT NOT NULL, description TEXT NOT NULL,
 status TEXT NOT NULL, revision INTEGER NOT NULL DEFAULT 1, valid_from INTEGER, valid_to INTEGER,
 source_refs TEXT NOT NULL, created_at INTEGER NOT NULL, PRIMARY KEY(owner,relation_id));
CREATE INDEX knowledge_edges ON knowledge_relations(owner,source,target,status);
CREATE TABLE knowledge_reviews(sequence INTEGER PRIMARY KEY AUTOINCREMENT,owner TEXT NOT NULL,
 object_id TEXT NOT NULL, action TEXT NOT NULL, previous TEXT NOT NULL, created_at INTEGER NOT
NULL);
CREATE TABLE knowledge_merges(owner TEXT NOT NULL,operation_id TEXT NOT NULL, snapshot TEXT NOT
NULL,
 state TEXT NOT NULL DEFAULT 'active', PRIMARY KEY(owner,operation_id));
CREATE TABLE knowledge_viewers(token_hash TEXT PRIMARY KEY, owner TEXT NOT NULL,
 session_id TEXT NOT NULL, device_id TEXT NOT NULL, expires_at INTEGER NOT NULL);
"""
