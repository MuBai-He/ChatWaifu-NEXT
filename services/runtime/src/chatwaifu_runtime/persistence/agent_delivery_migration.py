"""Extend the delivery ledger with a task-owned source, preserving legacy guards."""

AGENT_DELIVERY_MIGRATION_SQL = """
ALTER TABLE companion_settings ADD COLUMN proactive_decision_mode TEXT NOT NULL DEFAULT 'legacy'
CHECK(proactive_decision_mode IN ('legacy','shadow','model'));
CREATE TABLE agent_task_deliveries (
 request_id TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES agent_tasks(task_id),
 connection_id TEXT NOT NULL REFERENCES channel_connections(connection_id),
 target_json TEXT NOT NULL CHECK(json_valid(target_json))
);
CREATE TEMP TABLE backup_agent_deliveries AS SELECT * FROM channel_deliveries;
CREATE TEMP TABLE backup_agent_parts AS SELECT * FROM channel_delivery_parts;
DROP TABLE channel_delivery_parts;
DROP TABLE channel_deliveries;
CREATE TABLE "channel_deliveries" (
            delivery_id TEXT PRIMARY KEY,
            channel_turn_id TEXT UNIQUE,
            outbound_intent_id TEXT UNIQUE,
 task_delivery_id TEXT UNIQUE REFERENCES agent_task_deliveries(request_id),
            connection_id TEXT NOT NULL REFERENCES channel_connections(connection_id)
                ON DELETE CASCADE,
            binding_id TEXT,
            status TEXT NOT NULL
                CHECK(status IN ('pending','sending','delivered','failed','cancelled')),
            attempt INTEGER NOT NULL DEFAULT 1 CHECK(attempt >= 1),
            provider_message_id TEXT, last_error_json TEXT,
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL, delivered_at TEXT,
            lease_id TEXT, lease_expires_at TEXT,
            plan_version INTEGER NOT NULL DEFAULT 1 CHECK(plan_version >= 1),
            cancel_requested_at TEXT,
 group_target_json TEXT CHECK(group_target_json IS NULL OR json_valid(group_target_json)),
 group_route_id TEXT, group_route_revision INTEGER,
            CHECK((channel_turn_id IS NOT NULL)+(outbound_intent_id IS NOT NULL)+(task_delivery_id
IS NOT NULL)=1),
            FOREIGN KEY(channel_turn_id, binding_id, connection_id)
                REFERENCES "channel_turns"(channel_turn_id, binding_id, connection_id)
                ON DELETE CASCADE,
            FOREIGN KEY(outbound_intent_id, binding_id, connection_id)
                REFERENCES "channel_outbound_intents"(request_id, binding_id, connection_id)
        , CHECK((group_target_json IS NULL AND group_route_id IS NULL AND group_route_revision
 IS NULL)
 OR (group_target_json IS NOT NULL AND group_route_id IS NOT NULL AND group_route_revision>=1
 AND channel_turn_id IS NOT NULL AND outbound_intent_id IS NULL)),
 FOREIGN KEY(channel_turn_id,connection_id,group_route_id,group_route_revision)
 REFERENCES "channel_turns"(channel_turn_id,connection_id,group_route_id,group_route_revision)
);
CREATE TABLE "channel_delivery_parts" (
            part_id TEXT PRIMARY KEY,
            delivery_id TEXT NOT NULL REFERENCES "channel_deliveries"(delivery_id)
                ON DELETE CASCADE,
            ordinal INTEGER NOT NULL CHECK(ordinal >= 0), kind TEXT NOT NULL,
            payload_json TEXT NOT NULL CHECK(json_valid(payload_json)),
            required INTEGER NOT NULL DEFAULT 1 CHECK(required IN (0,1)),
            status TEXT NOT NULL
                CHECK(status IN ('pending','sending','delivered','failed','cancelled','skipped')),
            delay_after_ms INTEGER NOT NULL DEFAULT 0 CHECK(delay_after_ms >= 0),
            not_before_at TEXT, attempt INTEGER NOT NULL DEFAULT 0 CHECK(attempt >= 0),
            lease_id TEXT, lease_expires_at TEXT, provider_client_id TEXT NOT NULL UNIQUE,
            provider_message_id TEXT, last_error_json TEXT,
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL, delivered_at TEXT,
            CHECK(status != 'sending' OR (lease_id IS NOT NULL AND lease_expires_at IS NOT NULL)),
            CHECK(status != 'delivered' OR delivered_at IS NOT NULL),
            UNIQUE(delivery_id,ordinal)
        );
INSERT INTO
channel_deliveries(delivery_id,channel_turn_id,outbound_intent_id,connection_id,binding_id,status,attempt,provider_message_id,last_error_json,created_at,updated_at,delivered_at,lease_id,lease_expires_at,plan_version,cancel_requested_at,group_target_json,group_route_id,group_route_revision)
SELECT
delivery_id,channel_turn_id,outbound_intent_id,connection_id,binding_id,status,attempt,provider_message_id,last_error_json,created_at,updated_at,delivered_at,lease_id,lease_expires_at,plan_version,cancel_requested_at,group_target_json,group_route_id,group_route_revision
FROM backup_agent_deliveries;
DROP TABLE backup_agent_deliveries;
INSERT INTO
channel_delivery_parts(part_id,delivery_id,ordinal,kind,payload_json,required,status,delay_after_ms,not_before_at,attempt,lease_id,lease_expires_at,provider_client_id,provider_message_id,last_error_json,created_at,updated_at,delivered_at)
SELECT
part_id,delivery_id,ordinal,kind,payload_json,required,status,delay_after_ms,not_before_at,attempt,lease_id,lease_expires_at,provider_client_id,provider_message_id,last_error_json,created_at,updated_at,delivered_at
FROM backup_agent_parts;
DROP TABLE backup_agent_parts;
CREATE INDEX channel_deliveries_binding_status_idx
            ON channel_deliveries(binding_id,status,created_at);
CREATE INDEX channel_deliveries_connection_status_idx
            ON channel_deliveries(connection_id,status,updated_at DESC);
CREATE INDEX channel_deliveries_lease_idx ON channel_deliveries(status,lease_expires_at)
            WHERE status = 'sending';
CREATE INDEX channel_delivery_parts_claim_idx
            ON channel_delivery_parts(delivery_id,status,ordinal ASC,not_before_at ASC);
CREATE INDEX channel_delivery_parts_delivery_idx
            ON channel_delivery_parts(delivery_id,ordinal ASC);
CREATE INDEX channel_delivery_parts_lease_idx
            ON channel_delivery_parts(status,lease_expires_at) WHERE status = 'sending';
CREATE TRIGGER channel_group_delivery_guard BEFORE INSERT ON channel_deliveries
 WHEN (SELECT group_lineage_version FROM channel_turns WHERE channel_turn_id=NEW.channel_turn_id)=1
BEGIN
 SELECT CASE WHEN NEW.group_target_json IS NULL OR NOT EXISTS (
  SELECT 1 FROM channel_turns t
  JOIN channel_group_routes r ON r.route_id=t.group_route_id
  JOIN channel_group_route_heads h ON h.route_id=r.route_id
  JOIN channel_connections c ON c.connection_id=r.connection_id
  WHERE t.channel_turn_id=NEW.channel_turn_id AND t.connection_id=NEW.connection_id
  AND NEW.group_route_id=t.group_route_id AND NEW.group_route_revision=t.group_route_revision
  AND r.revision=t.group_route_revision AND r.enabled=1 AND r.deleted_at IS NULL
  AND r.pause_reason IS NULL AND c.enabled=1 AND c.deleted_at IS NULL AND c.status='ready'
  AND c.account_key=r.account_key AND c.character_id=r.character_id
  AND h.latest_channel_turn_id=t.channel_turn_id
  AND json_extract(NEW.group_target_json,'$.kind')='group'
  AND json_extract(NEW.group_target_json,'$.connection_id')=t.connection_id
  AND json_extract(NEW.group_target_json,'$.channel_turn_id')=t.channel_turn_id
  AND json_extract(NEW.group_target_json,'$.route_id')=r.route_id
  AND json_extract(NEW.group_target_json,'$.route_revision')=r.revision
  AND json_extract(NEW.group_target_json,'$.account_key')=r.account_key
  AND json_extract(NEW.group_target_json,'$.group_id')=r.group_id
  AND json_extract(NEW.group_target_json,'$.scene_id')=r.scene_id
  AND json_extract(NEW.group_target_json,'$.audience_fingerprint')=r.audience_fingerprint
 ) THEN RAISE(ABORT,'group delivery is no longer authorized') END;
END;
CREATE TRIGGER channel_group_part_content_immutable BEFORE UPDATE OF kind,ordinal,
 required,delay_after_ms,payload_json,part_id,delivery_id,provider_client_id
 ON channel_delivery_parts
 WHEN (SELECT group_target_json FROM channel_deliveries WHERE delivery_id=OLD.delivery_id)
 IS NOT NULL AND NOT (
  OLD.kind='audio' AND OLD.ordinal=0 AND OLD.required=1 AND NEW.required=0
  AND OLD.status IN ('failed','cancelled') AND NEW.status IS OLD.status
  AND NEW.kind IS OLD.kind AND NEW.ordinal IS OLD.ordinal
  AND NEW.delay_after_ms IS OLD.delay_after_ms AND NEW.payload_json IS OLD.payload_json
  AND NEW.part_id IS OLD.part_id AND NEW.delivery_id IS OLD.delivery_id
  AND NEW.provider_client_id IS OLD.provider_client_id
 )
BEGIN SELECT RAISE(ABORT,'group delivery content is immutable'); END;
CREATE TRIGGER channel_group_part_schedule_guard BEFORE UPDATE OF not_before_at
 ON channel_delivery_parts
 WHEN (SELECT group_target_json FROM channel_deliveries WHERE delivery_id=OLD.delivery_id)
 IS NOT NULL AND OLD.not_before_at IS NOT NEW.not_before_at
BEGIN
 SELECT CASE WHEN NOT (
  OLD.status='pending' AND NEW.status='pending' AND OLD.not_before_at IS NULL
  AND NEW.ordinal>0 AND julianday(NEW.not_before_at) IS NOT NULL
  AND EXISTS (
   SELECT 1 FROM channel_delivery_parts previous
   WHERE previous.delivery_id=OLD.delivery_id AND previous.ordinal=OLD.ordinal-1
   AND previous.status='delivered' AND previous.delivered_at IS NOT NULL
   AND previous.delay_after_ms>0
   AND abs((julianday(NEW.not_before_at)-julianday(previous.delivered_at))*86400000
           -previous.delay_after_ms)<1.0
  )
 ) THEN RAISE(ABORT,'group delivery schedule requires previous receipt') END;
END;
CREATE TRIGGER channel_group_target_immutable BEFORE UPDATE OF group_target_json,group_route_id,
 group_route_revision ON channel_deliveries
BEGIN SELECT RAISE(ABORT,'group delivery target is immutable'); END;
CREATE TRIGGER channel_group_text_only BEFORE INSERT ON channel_delivery_parts
 WHEN (SELECT group_target_json FROM channel_deliveries WHERE delivery_id=NEW.delivery_id)
 IS NOT NULL
BEGIN
 SELECT CASE WHEN (
  NEW.ordinal!=(SELECT count(*) FROM channel_delivery_parts WHERE delivery_id=NEW.delivery_id)
  OR NEW.not_before_at IS NOT NULL OR NEW.status!='pending' OR NEW.attempt!=0
  OR NOT json_valid(NEW.payload_json)
  OR NOT COALESCE((
   (NOT EXISTS (SELECT 1 FROM channel_delivery_parts
                WHERE delivery_id=NEW.delivery_id AND kind!='text')
    AND (
     (NEW.kind='text' AND NEW.ordinal>=0 AND NEW.ordinal<10 AND NEW.required=1
      AND NEW.delay_after_ms>=0 AND NEW.delay_after_ms<=30000
      AND json_extract(NEW.payload_json,'$.kind') IS 'text'
      AND json_type(NEW.payload_json,'$.text') IS 'text'
      AND length(trim(json_extract(NEW.payload_json,'$.text')))>0)
     OR
     (NEW.kind='image' AND NEW.ordinal>=1 AND NEW.ordinal<=10
      AND NEW.required=0 AND NEW.delay_after_ms=0
      AND json_extract(NEW.payload_json,'$.kind') IS 'image'
      AND EXISTS (
       SELECT 1 FROM channel_deliveries d
       JOIN channel_group_routes r ON r.route_id=d.group_route_id
       JOIN learned_stickers s ON s.principal_scope='scene:'||r.scene_id
         AND s.character_id=r.character_id
       WHERE d.delivery_id=NEW.delivery_id
         AND r.scene_id=json_extract(d.group_target_json,'$.scene_id')
         AND r.revision=d.group_route_revision AND r.enabled=1 AND r.deleted_at IS NULL
         AND s.sticker_id=json_extract(NEW.payload_json,'$.sticker_id')
         AND s.sha256=json_extract(NEW.payload_json,'$.sha256')
         AND s.mime_type=json_extract(NEW.payload_json,'$.mime_type')
      ))
    ))
   OR
   (NEW.kind='audio' AND NEW.ordinal=0 AND NEW.required=1 AND NEW.delay_after_ms=0
    AND json_extract(NEW.payload_json,'$.kind') IS 'audio'
    AND json_extract(NEW.payload_json,'$.mime_type') IS 'audio/wav'
    AND json_type(NEW.payload_json,'$.text') IS 'text'
    AND length(trim(json_extract(NEW.payload_json,'$.text'))) BETWEEN 1 AND 2000
    AND json_type(NEW.payload_json,'$.duration_ms') IS 'integer'
    AND json_extract(NEW.payload_json,'$.duration_ms') BETWEEN 1 AND 120000
    AND length(json_extract(NEW.payload_json,'$.asset_id'))=36
    AND length(json_extract(NEW.payload_json,'$.sha256'))=64
    AND json_extract(NEW.payload_json,'$.sha256') NOT GLOB '*[^0-9a-f]*'
    AND EXISTS (
     SELECT 1 FROM channel_deliveries d
     JOIN channel_group_routes r ON r.route_id=d.group_route_id
     JOIN channel_turns t ON t.channel_turn_id=d.channel_turn_id
     JOIN generations g ON g.generation_id=t.generation_id
     WHERE d.delivery_id=NEW.delivery_id AND r.allow_requested_voice=1
      AND r.revision=d.group_route_revision AND r.enabled=1 AND r.pause_reason IS NULL
      AND r.deleted_at IS NULL AND t.status='processing' AND g.state='running'
      AND g.invalidated_at IS NULL AND t.reply_text=json_extract(NEW.payload_json,'$.text')
    ))
   OR
   (NEW.kind='text' AND NEW.ordinal=1 AND NEW.required=1 AND NEW.delay_after_ms=0
    AND json_extract(NEW.payload_json,'$.kind') IS 'text'
    AND json_type(NEW.payload_json,'$.text') IS 'text'
    AND length(trim(json_extract(NEW.payload_json,'$.text')))>0
    AND EXISTS (
     SELECT 1 FROM channel_delivery_parts p JOIN channel_deliveries d ON d.delivery_id=p.delivery_id
     JOIN channel_group_routes r ON r.route_id=d.group_route_id
     JOIN channel_turns t ON t.channel_turn_id=d.channel_turn_id
     JOIN generations g ON g.generation_id=t.generation_id
     WHERE p.delivery_id=NEW.delivery_id AND p.ordinal=0 AND p.kind='audio'
      AND p.required=0 AND p.status IN ('failed','cancelled')
      AND r.allow_requested_voice=1 AND r.revision=d.group_route_revision AND r.enabled=1
      AND r.pause_reason IS NULL AND r.deleted_at IS NULL
      AND g.state='completed' AND g.invalidated_at IS NULL
      AND g.output_text=json_extract(NEW.payload_json,'$.text')
    ))
  ),0)
 ) THEN RAISE(ABORT,'group delivery requires authorized bounded reply parts') END;
END;
"""
