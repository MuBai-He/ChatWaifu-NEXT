"""Additive owner harness storage; task journals remain authoritative."""

HARNESS_MIGRATION = (
    "\nALTER TABLE knowledge_devices ADD COLUMN label TEXT NOT NULL DEF"
    "AULT '手机';\nALTER TABLE memory_proposals ADD COLUMN decision_basis"
    " TEXT;\nALTER TABLE memory_proposals ADD COLUMN policy_version TEX"
    "T;\nCREATE TABLE harness_reminders(occurrence_id TEXT PRIMARY KEY,"
    "owner TEXT NOT NULL,work_item_id\nTEXT NOT NULL,scheduled_at INTEG"
    "ER NOT NULL,state TEXT NOT NULL,payload TEXT NOT NULL);\nCREATE IN"
    "DEX harness_reminders_work ON harness_reminders(owner,work_item_i"
    "d);\nCREATE TABLE harness_reminder_receipts(occurrence_id TEXT NOT"
    " NULL,device_id TEXT NOT\nNULL,state TEXT NOT NULL,observed_at TEX"
    "T NOT NULL,received_at TEXT NOT NULL,PRIMARY\nKEY(occurrence_id,de"
    "vice_id,state,observed_at));\nCREATE TABLE harness_projections(own"
    "er TEXT NOT NULL,work_item_id TEXT NOT NULL,record_id TEXT\nNOT NU"
    "LL,record_revision INTEGER NOT NULL,work_revision INTEGER NOT NUL"
    "L,PRIMARY\nKEY(owner,work_item_id));\nCREATE TABLE harness_action_c"
    "laims(action_id TEXT PRIMARY KEY,task_id TEXT NOT NULL,step_key\nT"
    "EXT NOT NULL);\nCREATE TABLE harness_commands(operation_id TEXT PR"
    "IMARY KEY,owner TEXT NOT NULL,request_hash\nTEXT NOT NULL,referenc"
    "e TEXT NOT NULL);\nCREATE TABLE harness_suppressions(owner TEXT NO"
    "T NULL,digest TEXT NOT NULL,cause TEXT NOT\nNULL,PRIMARY KEY(owner"
    ",digest));\nCREATE TABLE harness_intent_wakes(intent_id TEXT NOT N"
    "ULL,scheduled_at TEXT NOT NULL,task_id\nTEXT NOT NULL,PRIMARY KEY("
    "intent_id,scheduled_at));\nCREATE TABLE harness_provider_sources(o"
    "wner TEXT NOT NULL,scope TEXT NOT NULL,initialized_at INTEGER NOT"
    " NULL,PRIMARY KEY(owner,scope));\nCREATE TABLE harness_intents(int"
    "ent_id TEXT PRIMARY KEY, owner TEXT NOT NULL,\n revision INTEGER N"
    "OT NULL, payload TEXT NOT NULL);\nCREATE INDEX harness_intents_own"
    "er ON harness_intents(owner);\nCREATE TABLE harness_processing(own"
    "er TEXT NOT NULL, room_id TEXT NOT NULL,\n device_id TEXT NOT NULL"
    ", epoch INTEGER NOT NULL, state TEXT NOT NULL,\n watermark INTEGER"
    " NOT NULL DEFAULT 0, PRIMARY KEY(owner,room_id));\nCREATE TABLE ha"
    "rness_observations(observation_id TEXT PRIMARY KEY, owner TEXT NO"
    "T NULL,\n room_id TEXT NOT NULL, reference TEXT NOT NULL, revision"
    " INTEGER NOT NULL,\n state TEXT NOT NULL, available_at INTEGER NOT"
    " NULL, lease_until INTEGER NOT NULL DEFAULT 0,\n payload TEXT NOT "
    "NULL, UNIQUE(owner,reference,revision));\nCREATE INDEX harness_obs"
    "ervations_due ON harness_observations(state,available_at);\nCREATE"
    " TABLE harness_work_items(work_item_id TEXT PRIMARY KEY,owner TEX"
    "T NOT NULL,\n revision INTEGER NOT NULL, payload TEXT NOT NULL);\nC"
    "REATE INDEX harness_work_owner ON harness_work_items(owner);\nCREA"
    "TE INDEX harness_work_task ON harness_work_items(json_extract(pay"
    "load,'$.task_id'));\nCREATE TABLE harness_processed_sources(owner "
    "TEXT NOT NULL,reference TEXT NOT NULL,\n revision INTEGER NOT NULL"
    ",PRIMARY KEY(owner,reference,revision));\nCREATE TABLE harness_dis"
    "patch(intent_id TEXT NOT NULL,work_item_id TEXT NOT NULL,\n work_r"
    "evision INTEGER NOT NULL,task_id TEXT NOT NULL,PRIMARY\nKEY(intent"
    "_id,work_item_id,work_revision));\nCREATE TABLE harness_processing"
    "_receipts(sequence INTEGER PRIMARY KEY AUTOINCREMENT,owner TEXT\nN"
    "OT NULL,reference TEXT NOT NULL,revision INTEGER NOT NULL,UNIQUE("
    "owner,reference,revision));\nCREATE TABLE harness_activity(sequenc"
    "e INTEGER PRIMARY KEY AUTOINCREMENT,owner TEXT NOT NULL,\n kind TE"
    "XT NOT NULL,reference TEXT NOT NULL,state TEXT NOT NULL,summary T"
    "EXT NOT NULL,\n created_at TEXT NOT NULL,evidence TEXT NOT NULL,de"
    "dup_key TEXT,details TEXT NOT NULL DEFAULT '{}');\nCREATE UNIQUE I"
    "NDEX harness_activity_dedup ON harness_activity(owner,dedup_key);"
    "\nCREATE TABLE harness_activity_receipts(sequence INTEGER NOT NULL"
    ",device_id TEXT NOT NULL,state\nTEXT NOT NULL,updated_at TEXT NOT "
    "NULL,PRIMARY KEY(sequence,device_id,state));\nCREATE INDEX harness"
    "_activity_owner ON harness_activity(owner,sequence);\n\nCREATE TABL"
    "E harness_work_claims(\n work_item_id TEXT PRIMARY KEY,\n task_id T"
    "EXT NOT NULL,\n step_key TEXT NOT NULL);\n\nCREATE TABLE harness_int"
    "ent_rechecks(intent_id TEXT PRIMARY KEY,revision INTEGER NOT NULL"
    ",after_row INTEGER NOT NULL,through_row INTEGER NOT NULL);\n"
)

CALENDAR_PUSH_MIGRATION = """
CREATE TABLE calendar_push_channels(
 channel_id TEXT PRIMARY KEY,owner TEXT NOT NULL,account_id TEXT NOT NULL,calendar_id TEXT NOT NULL,
 account_revision INTEGER NOT NULL,calendar_revision INTEGER NOT NULL,token_digest TEXT NOT NULL,
 callback_address TEXT NOT NULL,
 state TEXT NOT NULL CHECK(state IN ('preparing','active','retired')),expires_at INTEGER NOT NULL,
 created_at INTEGER NOT NULL,resource_id TEXT,last_message INTEGER NOT NULL DEFAULT 0,
 dirty_revision INTEGER NOT NULL DEFAULT 0,consumed_revision INTEGER NOT NULL DEFAULT 0,
 retry_at INTEGER NOT NULL DEFAULT 0,error_code TEXT
);
CREATE INDEX calendar_push_scope
 ON calendar_push_channels(owner,account_id,calendar_id,created_at DESC);
CREATE INDEX calendar_push_dirty
 ON calendar_push_channels(owner,state,dirty_revision,consumed_revision);
"""

HARNESS_METRICS_MIGRATION = """
CREATE TABLE harness_model_usage(
 request_id TEXT PRIMARY KEY,owner TEXT NOT NULL,task_id TEXT,work_item_id TEXT,observation_id TEXT,
 role TEXT NOT NULL,model TEXT NOT NULL,duration_ms INTEGER NOT NULL,state TEXT NOT NULL,
 prompt_tokens INTEGER,completion_tokens INTEGER,total_tokens INTEGER,reasoning_tokens INTEGER,
 synthetic INTEGER NOT NULL,observed_at INTEGER NOT NULL
);
CREATE INDEX harness_usage_owner ON harness_model_usage(owner,observed_at);
CREATE INDEX harness_usage_work ON harness_model_usage(owner,work_item_id,observed_at);
CREATE INDEX harness_usage_task ON harness_model_usage(owner,task_id,observed_at);
CREATE INDEX harness_usage_source ON harness_model_usage(owner,observation_id,observed_at);
CREATE TABLE harness_model_usage_sources(
 request_id TEXT NOT NULL REFERENCES harness_model_usage(request_id) ON DELETE CASCADE,
 observation_id TEXT NOT NULL,PRIMARY KEY(request_id,observation_id)
);
CREATE INDEX harness_usage_sources_observation ON harness_model_usage_sources(observation_id);
CREATE TABLE harness_metric_alerts(
 owner TEXT NOT NULL,alert_key TEXT NOT NULL,active INTEGER NOT NULL,updated_at INTEGER NOT NULL,
 notified INTEGER NOT NULL DEFAULT 0,
 PRIMARY KEY(owner,alert_key)
);
"""
