"""Ordered SQLite migrations for the local Runtime."""

from chatwaifu_runtime.persistence.agent_delivery_migration import AGENT_DELIVERY_MIGRATION_SQL

_BASE_MIGRATIONS: tuple[tuple[int, str], ...] = (
    (
        1,
        """
        CREATE TABLE sessions (
            session_id TEXT PRIMARY KEY,
            character_id TEXT NOT NULL,
            state TEXT NOT NULL,
            conversation_state TEXT NOT NULL,
            revision INTEGER NOT NULL DEFAULT 0,
            next_sequence INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE turns (
            turn_id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL REFERENCES sessions(session_id) ON DELETE CASCADE,
            role TEXT NOT NULL,
            committed_text TEXT,
            committed_at TEXT,
            created_at TEXT NOT NULL
        );

        CREATE TABLE generations (
            generation_id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL REFERENCES sessions(session_id) ON DELETE CASCADE,
            turn_id TEXT NOT NULL REFERENCES turns(turn_id) ON DELETE CASCADE,
            state TEXT NOT NULL,
            backend_kind TEXT NOT NULL,
            started_at TEXT,
            completed_at TEXT,
            invalidated_at TEXT
        );

        CREATE TABLE events (
            event_id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL REFERENCES sessions(session_id) ON DELETE CASCADE,
            sequence INTEGER NOT NULL,
            event_type TEXT NOT NULL,
            schema_version TEXT NOT NULL,
            occurred_at TEXT NOT NULL,
            source TEXT NOT NULL,
            correlation_id TEXT,
            causation_id TEXT,
            payload_json TEXT NOT NULL,
            envelope_json TEXT NOT NULL,
            UNIQUE(session_id, sequence)
        );

        CREATE TABLE outbox (
            event_id TEXT PRIMARY KEY REFERENCES events(event_id) ON DELETE CASCADE,
            envelope_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            published_at TEXT
        );

        CREATE INDEX events_session_sequence_idx ON events(session_id, sequence);
        CREATE INDEX outbox_pending_idx ON outbox(published_at, created_at);
        CREATE INDEX turns_session_created_idx ON turns(session_id, created_at);
        """,
    ),
    (
        2,
        """
        ALTER TABLE generations ADD COLUMN output_text TEXT;
        ALTER TABLE generations ADD COLUMN error_code TEXT;
        CREATE INDEX generations_session_started_idx
            ON generations(session_id, started_at);
        """,
    ),
    (
        3,
        """
        CREATE TABLE memory_items (
            memory_id TEXT PRIMARY KEY,
            content TEXT NOT NULL,
            normalized_content TEXT NOT NULL,
            state TEXT NOT NULL,
            source_session_id TEXT NOT NULL,
            source_turn_id TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            tombstoned_at TEXT
        );

        CREATE INDEX memory_items_state_created_idx
            ON memory_items(state, created_at DESC);

        CREATE VIRTUAL TABLE memory_fts USING fts5(
            memory_id UNINDEXED,
            content,
            tokenize = 'unicode61'
        );

        CREATE TRIGGER memory_items_after_insert AFTER INSERT ON memory_items
        WHEN new.state = 'active'
        BEGIN
            INSERT INTO memory_fts(memory_id, content) VALUES (new.memory_id, new.content);
        END;

        CREATE TRIGGER memory_items_after_update AFTER UPDATE ON memory_items
        BEGIN
            DELETE FROM memory_fts WHERE memory_id = old.memory_id;
            INSERT INTO memory_fts(memory_id, content)
                SELECT new.memory_id, new.content WHERE new.state = 'active';
        END;

        CREATE TRIGGER memory_items_after_delete AFTER DELETE ON memory_items
        BEGIN
            DELETE FROM memory_fts WHERE memory_id = old.memory_id;
        END;
        """,
    ),
    (
        4,
        """
        CREATE TABLE skill_plugins (
            plugin_id TEXT PRIMARY KEY,
            version TEXT NOT NULL,
            name TEXT NOT NULL,
            description TEXT NOT NULL,
            install_path TEXT NOT NULL UNIQUE,
            manifest_json TEXT NOT NULL,
            enabled INTEGER NOT NULL DEFAULT 1 CHECK(enabled IN (0, 1)),
            installed_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE skill_runs (
            skill_run_id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL REFERENCES sessions(session_id) ON DELETE CASCADE,
            skill_id TEXT NOT NULL,
            skill_version TEXT NOT NULL,
            capability TEXT NOT NULL,
            plugin_id TEXT REFERENCES skill_plugins(plugin_id) ON DELETE SET NULL,
            state TEXT NOT NULL,
            arguments_json TEXT NOT NULL,
            result_json TEXT,
            error_json TEXT,
            progress REAL,
            confirmation_request_id TEXT,
            cancel_requested INTEGER NOT NULL DEFAULT 0 CHECK(cancel_requested IN (0, 1)),
            created_at TEXT NOT NULL,
            started_at TEXT,
            updated_at TEXT NOT NULL,
            completed_at TEXT
        );

        CREATE TABLE permission_requests (
            request_id TEXT PRIMARY KEY,
            skill_run_id TEXT NOT NULL UNIQUE
                REFERENCES skill_runs(skill_run_id) ON DELETE CASCADE,
            principal TEXT NOT NULL,
            skill_id TEXT NOT NULL,
            capability TEXT NOT NULL,
            permissions_json TEXT NOT NULL,
            side_effect TEXT NOT NULL,
            reason TEXT NOT NULL,
            state TEXT NOT NULL,
            decision TEXT,
            decided_by TEXT,
            requested_at TEXT NOT NULL,
            decided_at TEXT
        );

        CREATE TABLE permission_grants (
            grant_id TEXT PRIMARY KEY,
            principal TEXT NOT NULL,
            skill_id TEXT NOT NULL,
            capability TEXT NOT NULL,
            permission TEXT NOT NULL,
            scope TEXT NOT NULL CHECK(scope IN ('once', 'session', 'always')),
            session_id TEXT REFERENCES sessions(session_id) ON DELETE CASCADE,
            created_at TEXT NOT NULL,
            expires_at TEXT,
            revoked_at TEXT
        );

        CREATE TABLE skill_tool_calls (
            tool_call_id TEXT PRIMARY KEY,
            skill_run_id TEXT NOT NULL
                REFERENCES skill_runs(skill_run_id) ON DELETE CASCADE,
            adapter TEXT NOT NULL,
            method TEXT NOT NULL,
            request_json TEXT NOT NULL,
            response_json TEXT,
            error_json TEXT,
            status TEXT NOT NULL,
            started_at TEXT NOT NULL,
            completed_at TEXT
        );

        CREATE INDEX skill_runs_session_created_idx
            ON skill_runs(session_id, created_at DESC);
        CREATE INDEX skill_runs_state_updated_idx
            ON skill_runs(state, updated_at DESC);
        CREATE INDEX permission_grants_lookup_idx
            ON permission_grants(principal, skill_id, capability, permission, revoked_at);
        CREATE INDEX skill_tool_calls_run_started_idx
            ON skill_tool_calls(skill_run_id, started_at);
        """,
    ),
    (
        5,
        """
        CREATE TABLE memory_records (
            memory_id TEXT PRIMARY KEY,
            namespace TEXT NOT NULL,
            kind TEXT NOT NULL,
            subject_id TEXT,
            predicate TEXT,
            value_json TEXT,
            text TEXT NOT NULL,
            normalized_text TEXT NOT NULL,
            search_terms TEXT NOT NULL,
            observed_at TEXT NOT NULL,
            valid_from TEXT,
            valid_to TEXT,
            confidence REAL NOT NULL CHECK(confidence >= 0 AND confidence <= 1),
            importance REAL NOT NULL CHECK(importance >= 0 AND importance <= 1),
            sensitivity TEXT NOT NULL,
            state TEXT NOT NULL,
            supersedes TEXT REFERENCES memory_records(memory_id) ON DELETE SET NULL,
            pinned INTEGER NOT NULL DEFAULT 0 CHECK(pinned IN (0, 1)),
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            tombstoned_at TEXT
        );

        CREATE TABLE memory_sources (
            source_id TEXT PRIMARY KEY,
            memory_id TEXT NOT NULL REFERENCES memory_records(memory_id) ON DELETE CASCADE,
            source_event_id TEXT NOT NULL REFERENCES events(event_id) ON DELETE RESTRICT,
            session_id TEXT NOT NULL REFERENCES sessions(session_id) ON DELETE CASCADE,
            turn_id TEXT REFERENCES turns(turn_id) ON DELETE SET NULL,
            source_kind TEXT NOT NULL,
            created_at TEXT NOT NULL,
            UNIQUE(memory_id, source_event_id)
        );

        CREATE TABLE memory_proposals (
            proposal_id TEXT PRIMARY KEY,
            operation TEXT NOT NULL,
            candidate_json TEXT,
            target_memory_id TEXT REFERENCES memory_records(memory_id) ON DELETE SET NULL,
            evidence_event_ids_json TEXT NOT NULL,
            confidence REAL NOT NULL CHECK(confidence >= 0 AND confidence <= 1),
            rationale TEXT NOT NULL,
            status TEXT NOT NULL,
            created_at TEXT NOT NULL,
            decided_at TEXT
        );

        CREATE VIRTUAL TABLE memory_records_fts USING fts5(
            memory_id UNINDEXED,
            text,
            search_terms,
            tokenize = 'unicode61'
        );

        CREATE TRIGGER memory_records_after_insert AFTER INSERT ON memory_records
        WHEN new.state = 'active'
        BEGIN
            INSERT INTO memory_records_fts(memory_id, text, search_terms)
            VALUES (new.memory_id, new.text, new.search_terms);
        END;

        CREATE TRIGGER memory_records_after_update AFTER UPDATE ON memory_records
        BEGIN
            DELETE FROM memory_records_fts WHERE memory_id = old.memory_id;
            INSERT INTO memory_records_fts(memory_id, text, search_terms)
                SELECT new.memory_id, new.text, new.search_terms WHERE new.state = 'active';
        END;

        CREATE TRIGGER memory_records_after_delete AFTER DELETE ON memory_records
        BEGIN
            DELETE FROM memory_records_fts WHERE memory_id = old.memory_id;
        END;

        CREATE INDEX memory_records_active_created_idx
            ON memory_records(state, pinned DESC, created_at DESC);
        CREATE INDEX memory_records_identity_idx
            ON memory_records(namespace, subject_id, predicate, state);
        CREATE UNIQUE INDEX memory_records_active_text_unique_idx
            ON memory_records(namespace, normalized_text) WHERE state = 'active';
        CREATE INDEX memory_sources_memory_idx ON memory_sources(memory_id, created_at);
        CREATE INDEX memory_proposals_status_created_idx
            ON memory_proposals(status, created_at DESC);

        INSERT INTO memory_records(
            memory_id, namespace, kind, subject_id, predicate, value_json,
            text, normalized_text, search_terms, observed_at, confidence,
            importance, sensitivity, state, pinned, created_at, updated_at,
            tombstoned_at
        )
        SELECT
            memory_id, 'user/local/global', 'semantic.fact', 'user', NULL,
            json_quote(content), content, normalized_content, normalized_content,
            created_at, 1.0, 0.7, 'private', state, 0, created_at, updated_at,
            tombstoned_at
        FROM memory_items;

        INSERT INTO memory_sources(
            source_id, memory_id, source_event_id, session_id, turn_id,
            source_kind, created_at
        )
        SELECT
            lower(hex(randomblob(4))) || '-' || lower(hex(randomblob(2))) || '-4' ||
            substr(lower(hex(randomblob(2))), 2) || '-' ||
            substr('89ab', abs(random()) % 4 + 1, 1) ||
            substr(lower(hex(randomblob(2))), 2) || '-' || lower(hex(randomblob(6))),
            item.memory_id, event.event_id, item.source_session_id,
            item.source_turn_id, 'migration', item.created_at
        FROM memory_items AS item
        JOIN events AS event
          ON json_extract(event.envelope_json, '$.turn_id') = item.source_turn_id
        WHERE event.event_type = 'user.turn_committed';
        """,
    ),
    (
        6,
        """
        ALTER TABLE generations ADD COLUMN audio_stream_id TEXT;
        ALTER TABLE generations ADD COLUMN spoken_text TEXT NOT NULL DEFAULT '';

        CREATE TABLE playback_segments (
            segment_id TEXT PRIMARY KEY,
            stream_id TEXT NOT NULL,
            session_id TEXT NOT NULL REFERENCES sessions(session_id) ON DELETE CASCADE,
            generation_id TEXT NOT NULL
                REFERENCES generations(generation_id) ON DELETE CASCADE,
            segment_index INTEGER NOT NULL CHECK(segment_index >= 0),
            text TEXT NOT NULL,
            duration_ms INTEGER NOT NULL CHECK(duration_ms >= 0),
            state TEXT NOT NULL CHECK(state IN ('queued', 'playing', 'completed', 'stopped')),
            played_pts_ms INTEGER NOT NULL DEFAULT 0 CHECK(played_pts_ms >= 0),
            buffered_ms INTEGER NOT NULL DEFAULT 0 CHECK(buffered_ms >= 0),
            client_clock_ms INTEGER NOT NULL DEFAULT 0 CHECK(client_clock_ms >= 0),
            transport TEXT CHECK(transport IN ('audio_element', 'webrtc')),
            stop_reason TEXT,
            queued_at TEXT NOT NULL,
            started_at TEXT,
            stopped_at TEXT,
            UNIQUE(generation_id, segment_index),
            UNIQUE(stream_id, segment_id)
        );

        CREATE TABLE playback_ack_commands (
            command_id TEXT PRIMARY KEY,
            segment_id TEXT NOT NULL
                REFERENCES playback_segments(segment_id) ON DELETE CASCADE,
            phase TEXT NOT NULL,
            received_at TEXT NOT NULL
        );

        CREATE INDEX playback_segments_generation_idx
            ON playback_segments(generation_id, segment_index);
        CREATE INDEX playback_segments_session_state_idx
            ON playback_segments(session_id, state);
        """,
    ),
    (
        7,
        """
        CREATE TABLE model_role_configs (
            role TEXT PRIMARY KEY CHECK(role IN (
                'chat', 'memory_extraction', 'memory_summary', 'embedding'
            )),
            provider TEXT NOT NULL,
            model TEXT NOT NULL,
            base_url TEXT NOT NULL,
            timeout_seconds REAL NOT NULL CHECK(timeout_seconds > 0),
            context_window INTEGER NOT NULL CHECK(context_window >= 1024),
            enabled INTEGER NOT NULL DEFAULT 1 CHECK(enabled IN (0, 1)),
            updated_at TEXT NOT NULL
        );

        CREATE TABLE character_states (
            character_id TEXT NOT NULL,
            user_scope TEXT NOT NULL,
            valence REAL NOT NULL CHECK(valence >= -1 AND valence <= 1),
            arousal REAL NOT NULL CHECK(arousal >= 0 AND arousal <= 1),
            energy REAL NOT NULL CHECK(energy >= 0 AND energy <= 1),
            attention REAL NOT NULL CHECK(attention >= 0 AND attention <= 1),
            embarrassment REAL NOT NULL CHECK(embarrassment >= 0 AND embarrassment <= 1),
            tension REAL NOT NULL CHECK(tension >= 0 AND tension <= 1),
            revision INTEGER NOT NULL DEFAULT 0,
            updated_at TEXT NOT NULL,
            PRIMARY KEY(character_id, user_scope)
        );

        CREATE TABLE relationship_states (
            character_id TEXT NOT NULL,
            user_scope TEXT NOT NULL,
            familiarity REAL NOT NULL CHECK(familiarity >= 0 AND familiarity <= 1),
            trust REAL NOT NULL CHECK(trust >= 0 AND trust <= 1),
            affinity REAL NOT NULL CHECK(affinity >= 0 AND affinity <= 1),
            comfort REAL NOT NULL CHECK(comfort >= 0 AND comfort <= 1),
            recent_tension REAL NOT NULL CHECK(recent_tension >= 0 AND recent_tension <= 1),
            interaction_count INTEGER NOT NULL DEFAULT 0 CHECK(interaction_count >= 0),
            stage TEXT NOT NULL CHECK(stage IN ('acquaintance', 'familiar', 'trusted', 'close')),
            preferred_address TEXT,
            revision INTEGER NOT NULL DEFAULT 0,
            updated_at TEXT NOT NULL,
            PRIMARY KEY(character_id, user_scope)
        );

        CREATE TABLE memory_embeddings (
            memory_id TEXT NOT NULL REFERENCES memory_records(memory_id) ON DELETE CASCADE,
            model_fingerprint TEXT NOT NULL,
            vector_json TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY(memory_id, model_fingerprint)
        );

        CREATE INDEX memory_embeddings_model_idx
            ON memory_embeddings(model_fingerprint, memory_id);
        """,
    ),
    (
        8,
        """
        CREATE TABLE companion_settings (
            singleton_id INTEGER PRIMARY KEY CHECK(singleton_id = 1),
            wake_phrase_enabled INTEGER NOT NULL CHECK(wake_phrase_enabled IN (0, 1)),
            wake_phrases_json TEXT NOT NULL,
            quiet_hours_enabled INTEGER NOT NULL CHECK(quiet_hours_enabled IN (0, 1)),
            quiet_start TEXT NOT NULL,
            quiet_end TEXT NOT NULL,
            proactive_enabled INTEGER NOT NULL CHECK(proactive_enabled IN (0, 1)),
            proactive_idle_minutes INTEGER NOT NULL
                CHECK(proactive_idle_minutes BETWEEN 1 AND 1440),
            proactive_cooldown_minutes INTEGER NOT NULL
                CHECK(proactive_cooldown_minutes BETWEEN 1 AND 10080),
            proactive_daily_budget INTEGER NOT NULL CHECK(proactive_daily_budget BETWEEN 0 AND 24),
            resource_sleep_enabled INTEGER NOT NULL CHECK(resource_sleep_enabled IN (0, 1)),
            resource_idle_minutes INTEGER NOT NULL CHECK(resource_idle_minutes BETWEEN 1 AND 1440),
            updated_at TEXT NOT NULL
        );

        CREATE TABLE ambient_actions (
            action_id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL REFERENCES sessions(session_id) ON DELETE CASCADE,
            kind TEXT NOT NULL,
            decision TEXT NOT NULL CHECK(decision IN ('triggered', 'deferred', 'ignored')),
            reason TEXT NOT NULL,
            scheduled_at TEXT NOT NULL,
            emitted_at TEXT
        );

        CREATE INDEX ambient_actions_session_scheduled_idx
            ON ambient_actions(session_id, scheduled_at DESC);
        CREATE INDEX ambient_actions_decision_scheduled_idx
            ON ambient_actions(decision, scheduled_at DESC);

        INSERT INTO companion_settings(
            singleton_id, wake_phrase_enabled, wake_phrases_json,
            quiet_hours_enabled, quiet_start, quiet_end,
            proactive_enabled, proactive_idle_minutes,
            proactive_cooldown_minutes, proactive_daily_budget,
            resource_sleep_enabled, resource_idle_minutes, updated_at
        ) VALUES (
            1, 1, '["宁宁","绫地宁宁"]',
            1, '23:00', '08:00',
            0, 45, 60, 3,
            1, 10, CURRENT_TIMESTAMP
        );
        """,
    ),
    (
        9,
        """
        CREATE TABLE tts_cloud_configs (
            provider_id TEXT PRIMARY KEY,
            enabled INTEGER NOT NULL CHECK(enabled IN (0, 1)),
            model TEXT NOT NULL,
            voice_id TEXT NOT NULL,
            region TEXT NOT NULL CHECK(region IN ('beijing', 'singapore')),
            workspace_id TEXT NOT NULL DEFAULT '',
            language_type TEXT NOT NULL,
            sample_rate INTEGER NOT NULL CHECK(sample_rate IN (8000, 16000, 24000, 48000)),
            speech_rate REAL NOT NULL CHECK(speech_rate BETWEEN 0.5 AND 2.0),
            volume INTEGER NOT NULL CHECK(volume BETWEEN 0 AND 100),
            pitch_rate REAL NOT NULL CHECK(pitch_rate BETWEEN 0.5 AND 2.0),
            timeout_seconds REAL NOT NULL CHECK(timeout_seconds > 0),
            max_audio_bytes INTEGER NOT NULL CHECK(max_audio_bytes >= 1000000),
            updated_at TEXT NOT NULL
        );

        ALTER TABLE playback_segments
            ADD COLUMN duration_finalized INTEGER NOT NULL DEFAULT 1
            CHECK(duration_finalized IN (0, 1));
        """,
    ),
    (
        10,
        """
        ALTER TABLE tts_cloud_configs
            ADD COLUMN instruction TEXT NOT NULL DEFAULT '';
        """,
    ),
    (
        11,
        """
        CREATE TABLE mcp_connections (
            connection_id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            transport TEXT NOT NULL
                CHECK(transport IN ('stdio', 'streamable_http', 'sse')),
            command_json TEXT NOT NULL DEFAULT '[]',
            url TEXT,
            allow_remote INTEGER NOT NULL DEFAULT 0 CHECK(allow_remote IN (0, 1)),
            enabled INTEGER NOT NULL DEFAULT 1 CHECK(enabled IN (0, 1)),
            timeout_seconds REAL NOT NULL DEFAULT 30 CHECK(timeout_seconds > 0),
            trust_level TEXT NOT NULL DEFAULT 'untrusted'
                CHECK(trust_level IN ('trusted', 'untrusted')),
            sandbox_mode TEXT NOT NULL DEFAULT 'required'
                CHECK(sandbox_mode IN ('required', 'preferred', 'disabled')),
            network_policy TEXT NOT NULL DEFAULT 'deny'
                CHECK(network_policy IN ('deny', 'loopback', 'allow')),
            bearer_token_configured INTEGER NOT NULL DEFAULT 0
                CHECK(bearer_token_configured IN (0, 1)),
            status TEXT NOT NULL DEFAULT 'untested'
                CHECK(status IN ('untested', 'ready', 'error', 'disabled')),
            capabilities_json TEXT NOT NULL DEFAULT '{}',
            sandbox_backend TEXT,
            last_error TEXT,
            last_tested_at TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        ALTER TABLE skill_runs ADD COLUMN mcp_connection_id TEXT
            REFERENCES mcp_connections(connection_id) ON DELETE SET NULL;

        CREATE INDEX mcp_connections_enabled_name_idx
            ON mcp_connections(enabled, name COLLATE NOCASE);
        CREATE INDEX skill_runs_mcp_connection_idx
            ON skill_runs(mcp_connection_id, created_at DESC);

        ALTER TABLE skill_plugins ADD COLUMN trust_level TEXT NOT NULL DEFAULT 'untrusted'
            CHECK(trust_level IN ('trusted', 'untrusted'));
        ALTER TABLE skill_plugins ADD COLUMN sandbox_mode TEXT NOT NULL DEFAULT 'required'
            CHECK(sandbox_mode IN ('required', 'preferred', 'disabled'));
        ALTER TABLE skill_plugins ADD COLUMN network_policy TEXT NOT NULL DEFAULT 'deny'
            CHECK(network_policy IN ('deny', 'loopback', 'allow'));
        ALTER TABLE skill_plugins ADD COLUMN sandbox_backend TEXT;
        """,
    ),
    (
        12,
        """
        ALTER TABLE skill_runs ADD COLUMN execution_plan_json TEXT;
        ALTER TABLE skill_runs ADD COLUMN execution_plan_fingerprint TEXT;

        ALTER TABLE permission_requests ADD COLUMN expires_at TEXT;
        UPDATE permission_requests
        SET expires_at = datetime(requested_at, '+5 minutes')
        WHERE expires_at IS NULL;
        CREATE INDEX permission_requests_pending_expiry_idx
            ON permission_requests(state, expires_at);

        ALTER TABLE mcp_connections ADD COLUMN revision INTEGER NOT NULL DEFAULT 1
            CHECK(revision >= 1);
        """,
    ),
    (
        13,
        """
        ALTER TABLE mcp_connections ADD COLUMN sandbox_limits_json TEXT NOT NULL DEFAULT '[]';
        ALTER TABLE skill_plugins ADD COLUMN sandbox_limits_json TEXT NOT NULL DEFAULT '[]';
        """,
    ),
    (
        14,
        """
        ALTER TABLE permission_requests ADD COLUMN skill_version TEXT NOT NULL DEFAULT '';
        ALTER TABLE permission_requests ADD COLUMN subject_fingerprint TEXT NOT NULL
            DEFAULT 'legacy-invalid';
        ALTER TABLE permission_requests ADD COLUMN plugin_id TEXT;
        ALTER TABLE permission_requests ADD COLUMN plugin_fingerprint TEXT;
        ALTER TABLE permission_requests ADD COLUMN mcp_connection_id TEXT;
        ALTER TABLE permission_requests ADD COLUMN mcp_connection_revision INTEGER;

        ALTER TABLE permission_grants ADD COLUMN skill_version TEXT NOT NULL DEFAULT '';
        ALTER TABLE permission_grants ADD COLUMN subject_fingerprint TEXT NOT NULL
            DEFAULT 'legacy-invalid';
        ALTER TABLE permission_grants ADD COLUMN plugin_id TEXT;
        ALTER TABLE permission_grants ADD COLUMN plugin_fingerprint TEXT;
        ALTER TABLE permission_grants ADD COLUMN mcp_connection_id TEXT;
        ALTER TABLE permission_grants ADD COLUMN mcp_connection_revision INTEGER;

        UPDATE permission_grants
        SET revoked_at = COALESCE(revoked_at, created_at)
        WHERE subject_fingerprint = 'legacy-invalid';

        DROP INDEX permission_grants_lookup_idx;
        CREATE INDEX permission_grants_lookup_idx
            ON permission_grants(
                principal, skill_id, capability, permission,
                subject_fingerprint, revoked_at
            );
        CREATE INDEX permission_grants_plugin_idx
            ON permission_grants(plugin_id, revoked_at);
        CREATE INDEX permission_grants_mcp_connection_idx
            ON permission_grants(mcp_connection_id, revoked_at);
        """,
    ),
    (
        15,
        """
        CREATE TABLE tts_provider_configs (
            provider_id TEXT PRIMARY KEY,
            schema_version TEXT NOT NULL,
            configuration_json TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        """,
    ),
    (
        16,
        """
        ALTER TABLE skill_runs ADD COLUMN turn_id TEXT;
        ALTER TABLE skill_runs ADD COLUMN generation_id TEXT;
        ALTER TABLE skill_runs ADD COLUMN origin TEXT NOT NULL DEFAULT 'manual'
            CHECK(origin IN ('manual', 'agent', 'external_mcp'));
        ALTER TABLE skill_runs ADD COLUMN provider_tool_call_id TEXT;

        CREATE INDEX skill_runs_generation_idx
            ON skill_runs(generation_id, created_at DESC);
        """,
    ),
    (
        17,
        """
        ALTER TABLE turns ADD COLUMN source_context_json TEXT;

        CREATE TABLE channel_connections (
            connection_id TEXT PRIMARY KEY,
            provider_id TEXT NOT NULL,
            name TEXT NOT NULL,
            character_id TEXT NOT NULL,
            principal_scope TEXT NOT NULL,
            account_key TEXT,
            allowed_sender_keys_json TEXT NOT NULL DEFAULT '[]',
            enabled INTEGER NOT NULL DEFAULT 1 CHECK(enabled IN (0, 1)),
            timeout_seconds REAL NOT NULL DEFAULT 120 CHECK(timeout_seconds > 0),
            access_token_hash TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'untested'
                CHECK(status IN ('untested', 'ready', 'degraded', 'error', 'disabled')),
            last_error_json TEXT,
            last_seen_at TEXT,
            revision INTEGER NOT NULL DEFAULT 1 CHECK(revision >= 1),
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            deleted_at TEXT
        );

        CREATE TABLE channel_bindings (
            binding_id TEXT PRIMARY KEY,
            connection_id TEXT NOT NULL
                REFERENCES channel_connections(connection_id) ON DELETE CASCADE,
            conversation_key TEXT NOT NULL,
            sender_key TEXT NOT NULL,
            session_id TEXT NOT NULL REFERENCES sessions(session_id) ON DELETE RESTRICT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(connection_id, conversation_key),
            UNIQUE(connection_id, session_id)
        );

        CREATE TABLE channel_turns (
            channel_turn_id TEXT PRIMARY KEY,
            connection_id TEXT NOT NULL
                REFERENCES channel_connections(connection_id) ON DELETE CASCADE,
            binding_id TEXT NOT NULL REFERENCES channel_bindings(binding_id) ON DELETE CASCADE,
            external_message_id TEXT NOT NULL,
            content_sha256 TEXT NOT NULL,
            account_key TEXT,
            conversation_key TEXT NOT NULL,
            chat_type TEXT NOT NULL DEFAULT 'direct'
                CHECK(chat_type IN ('direct', 'group')),
            conversation_label TEXT,
            sender_key TEXT NOT NULL,
            sender_display_name TEXT,
            principal_scope TEXT NOT NULL,
            session_id TEXT NOT NULL REFERENCES sessions(session_id) ON DELETE RESTRICT,
            turn_id TEXT NOT NULL,
            generation_id TEXT NOT NULL,
            status TEXT NOT NULL
                CHECK(status IN (
                    'accepted', 'processing', 'completed', 'cancelling',
                    'cancelled', 'failed', 'timed_out'
                )),
            reply_text TEXT,
            error_json TEXT,
            delivery_id TEXT UNIQUE,
            revision INTEGER NOT NULL DEFAULT 0 CHECK(revision >= 0),
            accepted_at TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            completed_at TEXT,
            UNIQUE(connection_id, external_message_id)
        );

        CREATE TABLE channel_deliveries (
            delivery_id TEXT PRIMARY KEY,
            channel_turn_id TEXT NOT NULL UNIQUE
                REFERENCES channel_turns(channel_turn_id) ON DELETE CASCADE,
            connection_id TEXT NOT NULL
                REFERENCES channel_connections(connection_id) ON DELETE CASCADE,
            status TEXT NOT NULL
                CHECK(status IN ('pending', 'sending', 'delivered', 'failed', 'cancelled')),
            attempt INTEGER NOT NULL DEFAULT 1 CHECK(attempt >= 1),
            provider_message_id TEXT,
            last_error_json TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            delivered_at TEXT
        );

        CREATE INDEX channel_connections_provider_status_idx
            ON channel_connections(provider_id, status, updated_at DESC);
        CREATE INDEX channel_bindings_connection_idx
            ON channel_bindings(connection_id, updated_at DESC);
        CREATE INDEX channel_turns_connection_status_idx
            ON channel_turns(connection_id, status, updated_at DESC);
        CREATE INDEX channel_turns_generation_idx
            ON channel_turns(generation_id);
        CREATE INDEX channel_deliveries_connection_status_idx
            ON channel_deliveries(connection_id, status, updated_at DESC);
        """,
    ),
    (
        18,
        """
        ALTER TABLE memory_sources ADD COLUMN channel_attribution_json TEXT;

        UPDATE memory_sources AS source
        SET channel_attribution_json = (
            SELECT json_object(
                'schema_version', '1.0',
                'provider_id', json_extract(turn.source_context_json, '$.provider_id'),
                'connection_id', json_extract(turn.source_context_json, '$.connection_id'),
                'account_key', json_extract(turn.source_context_json, '$.account_key'),
                'principal_scope', COALESCE(
                    json_extract(turn.source_context_json, '$.principal_scope'), 'local'
                ),
                'chat_type', json_extract(turn.source_context_json, '$.chat_type'),
                'conversation_key',
                    json_extract(turn.source_context_json, '$.conversation_key'),
                'sender_key', json_extract(turn.source_context_json, '$.sender_key'),
                'received_at', COALESCE(
                    json_extract(turn.source_context_json, '$.received_at'),
                    source.created_at
                ),
                'conversation_label',
                    json_extract(turn.source_context_json, '$.conversation_label'),
                'sender_display_name',
                    json_extract(turn.source_context_json, '$.sender_display_name')
            )
            FROM turns AS turn
            WHERE turn.turn_id = source.turn_id
              AND turn.source_context_json IS NOT NULL
              AND json_valid(turn.source_context_json)
              AND json_extract(turn.source_context_json, '$.provider_id') IS NOT NULL
        )
        WHERE source.turn_id IS NOT NULL
          AND EXISTS (
              SELECT 1 FROM turns AS turn
              WHERE turn.turn_id = source.turn_id
                AND turn.source_context_json IS NOT NULL
                AND json_valid(turn.source_context_json)
                AND json_extract(turn.source_context_json, '$.provider_id') IS NOT NULL
          );

        CREATE INDEX memory_sources_channel_provider_idx
            ON memory_sources(
                json_extract(channel_attribution_json, '$.provider_id'),
                created_at
            )
            WHERE channel_attribution_json IS NOT NULL;
        """,
    ),
    (
        19,
        """
        ALTER TABLE channel_deliveries ADD COLUMN lease_id TEXT;
        ALTER TABLE channel_deliveries ADD COLUMN lease_expires_at TEXT;

        CREATE INDEX channel_deliveries_lease_idx
            ON channel_deliveries(status, lease_expires_at)
            WHERE status = 'sending';
        """,
    ),
    (
        20,
        """
        CREATE TABLE channel_adapter_checkpoints (
            connection_id TEXT PRIMARY KEY
                REFERENCES channel_connections(connection_id) ON DELETE CASCADE,
            cursor TEXT NOT NULL DEFAULT '',
            updated_at TEXT NOT NULL
        );
        """,
    ),
    (
        21,
        """
        ALTER TABLE memory_records ADD COLUMN origin_proposal_id TEXT;

        CREATE UNIQUE INDEX memory_records_origin_proposal_idx
            ON memory_records(origin_proposal_id)
            WHERE origin_proposal_id IS NOT NULL;
        """,
    ),
    (
        22,
        """
        ALTER TABLE channel_deliveries
            ADD COLUMN plan_version INTEGER NOT NULL DEFAULT 1 CHECK(plan_version >= 1);
        ALTER TABLE channel_deliveries ADD COLUMN cancel_requested_at TEXT;

        CREATE TABLE channel_delivery_parts (
            part_id TEXT PRIMARY KEY,
            delivery_id TEXT NOT NULL
                REFERENCES channel_deliveries(delivery_id) ON DELETE CASCADE,
            ordinal INTEGER NOT NULL CHECK(ordinal >= 0),
            kind TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            required INTEGER NOT NULL DEFAULT 1 CHECK(required IN (0, 1)),
            status TEXT NOT NULL
                CHECK(
                    status IN ('pending', 'sending', 'delivered', 'failed', 'cancelled', 'skipped')
                ),
            delay_after_ms INTEGER NOT NULL DEFAULT 0 CHECK(delay_after_ms >= 0),
            not_before_at TEXT,
            attempt INTEGER NOT NULL DEFAULT 0 CHECK(attempt >= 0),
            lease_id TEXT,
            lease_expires_at TEXT,
            provider_client_id TEXT NOT NULL UNIQUE,
            provider_message_id TEXT,
            last_error_json TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            delivered_at TEXT,
            CHECK(json_valid(payload_json)),
            CHECK(status != 'sending' OR (lease_id IS NOT NULL AND lease_expires_at IS NOT NULL)),
            CHECK(status != 'delivered' OR delivered_at IS NOT NULL),
            UNIQUE(delivery_id, ordinal)
        );

        CREATE INDEX channel_delivery_parts_delivery_idx
            ON channel_delivery_parts(delivery_id, ordinal ASC);
        CREATE INDEX channel_delivery_parts_claim_idx
            ON channel_delivery_parts(delivery_id, status, ordinal ASC, not_before_at ASC);
        CREATE INDEX channel_delivery_parts_lease_idx
            ON channel_delivery_parts(status, lease_expires_at)
            WHERE status = 'sending';

        -- Reconcile legacy parents before backfill
        UPDATE channel_deliveries
        SET status = 'pending', lease_id = NULL, lease_expires_at = NULL
        WHERE status = 'sending' AND (lease_id IS NULL OR lease_expires_at IS NULL);

        UPDATE channel_deliveries
        SET delivered_at = updated_at
        WHERE status = 'delivered' AND delivered_at IS NULL;

        UPDATE channel_deliveries
        SET lease_id = NULL, lease_expires_at = NULL
        WHERE status IN ('pending', 'delivered', 'failed', 'cancelled');

        WITH delivery_source AS (
            SELECT
                d.delivery_id,
                d.status,
                d.attempt,
                d.lease_id,
                d.lease_expires_at,
                d.provider_message_id,
                d.last_error_json,
                d.created_at,
                d.updated_at,
                COALESCE(d.delivered_at, d.updated_at) AS delivered_at,
                COALESCE(NULLIF(t.reply_text, ''), '(empty reply)') AS text,
                lower(hex(randomblob(16))) AS raw_hex
            FROM channel_deliveries AS d
            LEFT JOIN channel_turns AS t ON t.channel_turn_id = d.channel_turn_id
        )
        INSERT INTO channel_delivery_parts (
            part_id,
            delivery_id,
            ordinal,
            kind,
            payload_json,
            required,
            status,
            delay_after_ms,
            not_before_at,
            attempt,
            lease_id,
            lease_expires_at,
            provider_client_id,
            provider_message_id,
            last_error_json,
            created_at,
            updated_at,
            delivered_at
        )
        SELECT
            substr(raw_hex, 1, 8) || '-' ||
            substr(raw_hex, 9, 4) || '-' ||
            substr(raw_hex, 13, 4) || '-' ||
            substr(raw_hex, 17, 4) || '-' ||
            substr(raw_hex, 21, 12),
            delivery_id,
            0,
            'text',
            json_object('schema_version', '1.0', 'kind', 'text', 'text', text),
            1,
            status,
            0,
            NULL,
            attempt,
            CASE WHEN status = 'sending' THEN lease_id ELSE NULL END,
            CASE WHEN status = 'sending' THEN lease_expires_at ELSE NULL END,
            'chatwaifu-' || replace(delivery_id, '-', '') || '-000',
            provider_message_id,
            last_error_json,
            created_at,
            updated_at,
            CASE WHEN status = 'delivered' THEN delivered_at ELSE NULL END
        FROM delivery_source;
        """,
    ),
    (
        23,
        """
        ALTER TABLE channel_connections ADD COLUMN presentation_policy_json TEXT;
        """,
    ),
    (
        24,
        """
        CREATE TABLE sticker_library_settings (
            principal_scope TEXT NOT NULL,
            character_id TEXT NOT NULL,
            learning_enabled INTEGER NOT NULL DEFAULT 0 CHECK(learning_enabled IN (0, 1)),
            revision INTEGER NOT NULL DEFAULT 0 CHECK(revision >= 0),
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY(principal_scope, character_id)
        );

        CREATE TABLE learned_stickers (
            sticker_id TEXT PRIMARY KEY,
            principal_scope TEXT NOT NULL,
            character_id TEXT NOT NULL,
            sha256 TEXT NOT NULL,
            mime_type TEXT NOT NULL DEFAULT 'image/png' CHECK(mime_type = 'image/png'),
            label TEXT NOT NULL,
            description TEXT NOT NULL,
            expression TEXT NOT NULL CHECK(
                expression IN ('neutral', 'happy', 'sad', 'angry', 'surprised', 'shy', 'curious')
            ),
            byte_size INTEGER NOT NULL CHECK(byte_size > 0 AND byte_size <= 5242880),
            data BLOB NOT NULL,
            source_connection_id TEXT NOT NULL,
            generation_id TEXT NOT NULL,
            learned_at TEXT NOT NULL,
            FOREIGN KEY(principal_scope, character_id)
                REFERENCES sticker_library_settings(principal_scope, character_id)
                ON DELETE CASCADE,
            UNIQUE(principal_scope, character_id, sha256)
        );

        CREATE INDEX learned_stickers_scope_char_idx
            ON learned_stickers(principal_scope, character_id, learned_at ASC);
        """,
    ),
    (
        25,
        """
        CREATE TABLE photo_memory_settings (
            principal_scope TEXT NOT NULL,
            character_id TEXT NOT NULL,
            retention_enabled INTEGER NOT NULL CHECK(retention_enabled IN (0, 1)),
            revision INTEGER NOT NULL CHECK(revision >= 0),
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY(principal_scope, character_id)
        );

        CREATE TABLE photo_assets (
            photo_id TEXT PRIMARY KEY,
            principal_scope TEXT NOT NULL,
            character_id TEXT NOT NULL,
            sha256 TEXT NOT NULL,
            mime_type TEXT NOT NULL CHECK(mime_type IN ('image/png', 'image/jpeg')),
            byte_size INTEGER NOT NULL CHECK(byte_size > 0 AND byte_size <= 5242880),
            width INTEGER NOT NULL CHECK(width > 0 AND width <= 2048),
            height INTEGER NOT NULL CHECK(height > 0 AND height <= 2048),
            title TEXT NOT NULL,
            description TEXT NOT NULL,
            confidence REAL NOT NULL CHECK(confidence >= 0 AND confidence <= 1),
            keywords TEXT NOT NULL,
            caption TEXT NOT NULL,
            received_at TEXT NOT NULL,
            saved_at TEXT NOT NULL,
            source_connection_id TEXT NOT NULL,
            source_session_id TEXT NOT NULL,
            source_turn_id TEXT NOT NULL,
            source_generation_id TEXT NOT NULL,
            data BLOB NOT NULL,
            UNIQUE(principal_scope, character_id, sha256),
            FOREIGN KEY(principal_scope, character_id)
                REFERENCES photo_memory_settings(principal_scope, character_id)
                ON DELETE CASCADE
        );

        CREATE TABLE photo_references (
            photo_id TEXT NOT NULL REFERENCES photo_assets(photo_id) ON DELETE CASCADE,
            generation_id TEXT NOT NULL,
            session_id TEXT NOT NULL,
            reference_type TEXT NOT NULL CHECK(reference_type IN ('source', 'recall')),
            created_at TEXT NOT NULL,
            PRIMARY KEY(photo_id, generation_id)
        );

        CREATE TABLE photo_context_redactions (
            generation_id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL,
            principal_scope TEXT NOT NULL,
            character_id TEXT NOT NULL,
            created_at TEXT NOT NULL
        );

        CREATE VIRTUAL TABLE photo_assets_fts USING fts5(
            photo_id UNINDEXED,
            content,
            tokenize='unicode61'
        );

        CREATE TRIGGER photo_assets_after_delete AFTER DELETE ON photo_assets
        BEGIN
            DELETE FROM photo_assets_fts WHERE photo_id = old.photo_id;
        END;

        CREATE TABLE conversation_history_dependencies (
            source_generation_id TEXT NOT NULL,
            derived_generation_id TEXT NOT NULL,
            PRIMARY KEY (source_generation_id, derived_generation_id)
        );
        CREATE INDEX conversation_history_derived_idx
            ON conversation_history_dependencies(derived_generation_id);

        ALTER TABLE turns ADD COLUMN generation_id TEXT;
        CREATE INDEX turns_generation_id_idx
            ON turns(generation_id) WHERE generation_id IS NOT NULL;
        """,
    ),
    (
        26,
        """
        CREATE TABLE photo_embeddings (
            photo_id TEXT NOT NULL REFERENCES photo_assets(photo_id) ON DELETE CASCADE,
            representation TEXT NOT NULL,
            principal_scope TEXT NOT NULL,
            character_id TEXT NOT NULL,
            vector_space_id TEXT NOT NULL,
            model_fingerprint TEXT NOT NULL,
            route_generation INTEGER NOT NULL DEFAULT 0,
            vector_json TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY(photo_id, representation)
        );

        CREATE INDEX photo_embeddings_lookup_idx
            ON photo_embeddings(principal_scope, character_id, representation, vector_space_id);

        CREATE TRIGGER photo_assets_after_delete_embeddings AFTER DELETE ON photo_assets
        BEGIN
            DELETE FROM photo_embeddings WHERE photo_id = old.photo_id;
        END;
        """,
    ),
    (
        27,
        """
        ALTER TABLE photo_assets ADD COLUMN user_annotations_json TEXT NOT NULL DEFAULT '[]';
        ALTER TABLE photo_assets ADD COLUMN captured_at TEXT;
        ALTER TABLE photo_assets ADD COLUMN captured_at_offset TEXT;
        ALTER TABLE photo_assets ADD COLUMN original_width INTEGER;
        ALTER TABLE photo_assets ADD COLUMN original_height INTEGER;
        ALTER TABLE photo_assets ADD COLUMN original_mime_type TEXT;
        """,
    ),
    (
        28,
        """
        CREATE TABLE channel_turn_burst_members (
            burst_id TEXT NOT NULL,
            leader_channel_turn_id TEXT NOT NULL
                REFERENCES channel_turns(channel_turn_id) ON DELETE CASCADE,
            member_channel_turn_id TEXT NOT NULL PRIMARY KEY
                REFERENCES channel_turns(channel_turn_id) ON DELETE CASCADE,
            ordinal INTEGER NOT NULL CHECK(ordinal >= 0 AND ordinal < 4),
            received_at TEXT NOT NULL,
            created_at TEXT NOT NULL
        );

        CREATE INDEX channel_turn_burst_members_leader_idx
            ON channel_turn_burst_members(leader_channel_turn_id);
        CREATE INDEX channel_turn_burst_members_burst_idx
            ON channel_turn_burst_members(burst_id);
        """,
    ),
    (
        29,
        """
        ALTER TABLE playback_segments
            ADD COLUMN transcript_finalized INTEGER NOT NULL DEFAULT 1
            CHECK(transcript_finalized IN (0, 1));
        ALTER TABLE playback_segments
            ADD COLUMN spoken_committed INTEGER NOT NULL DEFAULT 0
            CHECK(spoken_committed IN (0, 1));
        UPDATE playback_segments SET spoken_committed = 1 WHERE state = 'completed';
        """,
    ),
    (
        30,
        """
        CREATE TABLE spoken_memory_facts (
            source_event_id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL,
            turn_id TEXT NOT NULL,
            spoken_text TEXT NOT NULL,
            state TEXT NOT NULL CHECK(state IN ('pending', 'completed', 'failed', 'paused')),
            staged_candidates_json TEXT,
            checkpoint_index INTEGER NOT NULL DEFAULT 0,
            retry_count INTEGER NOT NULL DEFAULT 0,
            next_retry_at TEXT,
            last_error TEXT,
            created_at TEXT NOT NULL,
            completed_at TEXT
        );
        CREATE INDEX spoken_memory_facts_pending_idx
            ON spoken_memory_facts(state, next_retry_at, created_at);
        CREATE INDEX spoken_memory_facts_session_idx
            ON spoken_memory_facts(session_id);

        CREATE TABLE memory_scope_resets (
            character_id TEXT PRIMARY KEY,
            reset_at TEXT NOT NULL
        );
        """,
    ),
    (
        31,
        """
        CREATE TABLE realtime_configurations (
            id TEXT PRIMARY KEY,
            schema_version TEXT NOT NULL,
            revision INTEGER NOT NULL,
            connection_mode TEXT NOT NULL CHECK(connection_mode IN ('cascade', 'cloud_realtime')),
            cloud_backend TEXT NOT NULL,
            model TEXT NOT NULL,
            voice TEXT NOT NULL,
            transcription_model TEXT NOT NULL,
            cloud_tools_enabled INTEGER NOT NULL CHECK(cloud_tools_enabled IN (0, 1)),
            cloud_egress_consent INTEGER NOT NULL CHECK(cloud_egress_consent IN (0, 1)),
            secret_key_ref TEXT,
            updated_at TEXT NOT NULL
        );
        """,
    ),
    (
        32,
        """
        CREATE TABLE participants (
            participant_id TEXT PRIMARY KEY,
            display_name TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        INSERT INTO participants VALUES
            ('local', '主人', strftime('%Y-%m-%dT%H:%M:%f+00:00','now'));
        CREATE TABLE conversation_scenes (
            scene_id TEXT PRIMARY KEY,
            display_name TEXT NOT NULL,
            participant_ids_json TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        ALTER TABLE sessions ADD COLUMN participant_id TEXT NOT NULL DEFAULT 'local';
        ALTER TABLE sessions ADD COLUMN scene_id TEXT REFERENCES conversation_scenes(scene_id);
        ALTER TABLE sessions ADD COLUMN scene_kind TEXT NOT NULL DEFAULT 'private';
        ALTER TABLE sessions ADD COLUMN audience_json TEXT NOT NULL DEFAULT '["local"]';
        ALTER TABLE sessions ADD COLUMN user_scope TEXT NOT NULL DEFAULT 'local';
        CREATE INDEX sessions_scope_idx ON sessions(character_id, user_scope);
        CREATE TRIGGER sessions_scope_immutable
        BEFORE UPDATE OF participant_id, scene_id, scene_kind, audience_json, user_scope ON sessions
        WHEN NEW.participant_id IS NOT OLD.participant_id OR NEW.scene_id IS NOT OLD.scene_id
          OR NEW.scene_kind IS NOT OLD.scene_kind OR NEW.audience_json IS NOT OLD.audience_json
          OR NEW.user_scope IS NOT OLD.user_scope
        BEGIN SELECT RAISE(ABORT, 'session conversation scope is immutable'); END;
        ALTER TABLE memory_scope_resets RENAME TO legacy_memory_scope_resets;
        CREATE TABLE memory_scope_resets (
            character_id TEXT NOT NULL,
            user_scope TEXT NOT NULL DEFAULT 'local',
            reset_at TEXT NOT NULL,
            PRIMARY KEY(character_id, user_scope)
        );
        INSERT INTO memory_scope_resets
            SELECT character_id, 'local', reset_at FROM legacy_memory_scope_resets;
        DROP TABLE legacy_memory_scope_resets;
        """,
    ),
    (
        33,
        """
        CREATE TABLE assistant_accounts (
            account_id TEXT PRIMARY KEY,
            owner_scope TEXT NOT NULL CHECK(owner_scope = 'local'),
            status TEXT NOT NULL CHECK(status IN ('connected', 'revoked')),
            secret_ref TEXT NOT NULL UNIQUE,
            revision INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE assistant_calendars (
            account_id TEXT NOT NULL REFERENCES assistant_accounts(account_id),
            calendar_id TEXT NOT NULL,
            title TEXT NOT NULL,
            timezone TEXT,
            access_role TEXT NOT NULL,
            selected INTEGER NOT NULL DEFAULT 0 CHECK(selected IN (0, 1)),
            revision INTEGER NOT NULL DEFAULT 0,
            sync_token TEXT,
            synced_at TEXT,
            PRIMARY KEY(account_id, calendar_id)
        );
        CREATE TABLE assistant_events (
            account_id TEXT NOT NULL,
            calendar_id TEXT NOT NULL,
            event_id TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            PRIMARY KEY(account_id, calendar_id, event_id),
            FOREIGN KEY(account_id, calendar_id)
                REFERENCES assistant_calendars(account_id, calendar_id) ON DELETE CASCADE
        );
        """,
    ),
    (
        34,
        """
        CREATE TABLE assistant_devices (
            device_id TEXT PRIMARY KEY, name TEXT NOT NULL, secret_hash TEXT NOT NULL,
            revoked INTEGER NOT NULL DEFAULT 0, sources_json TEXT NOT NULL DEFAULT '[]',
            source_revision INTEGER NOT NULL DEFAULT 0,
            last_seen REAL NOT NULL DEFAULT 0
        );
        CREATE TABLE assistant_tasks (
            task_id TEXT PRIMARY KEY,
            device_id TEXT NOT NULL REFERENCES assistant_devices(device_id),
            payload_json TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'active', due REAL NOT NULL,
            revision INTEGER NOT NULL DEFAULT 0
        );
        CREATE INDEX assistant_tasks_due ON assistant_tasks(state,due);
        CREATE TABLE assistant_deliveries (
            delivery_id TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES assistant_tasks(task_id),
            device_id TEXT NOT NULL REFERENCES assistant_devices(device_id), due REAL NOT NULL,
            expires REAL NOT NULL, state TEXT NOT NULL DEFAULT 'pending', ack_action TEXT,
            UNIQUE(task_id,due)
        );
        CREATE TABLE assistant_operations (
            operation_id TEXT PRIMARY KEY,
            device_id TEXT NOT NULL REFERENCES assistant_devices(device_id),
            payload_json TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'queued',
            expires REAL NOT NULL, result_json TEXT NOT NULL DEFAULT '{}'
        );
        """,
    ),
    (
        35,
        """
        ALTER TABLE assistant_accounts ADD COLUMN display_label TEXT;
        ALTER TABLE assistant_calendars ADD COLUMN is_primary INTEGER NOT NULL DEFAULT 0
            CHECK(is_primary IN (0, 1));
        CREATE TABLE assistant_tasklists (
            account_id TEXT NOT NULL REFERENCES assistant_accounts(account_id),
            list_id TEXT NOT NULL,
            title TEXT NOT NULL,
            selected INTEGER NOT NULL DEFAULT 0 CHECK(selected IN (0, 1)),
            PRIMARY KEY(account_id, list_id)
        );
        CREATE TABLE assistant_write_destinations (
            kind TEXT PRIMARY KEY CHECK(kind IN ('calendar', 'reminder')),
            provider TEXT NOT NULL CHECK(provider IN ('google', 'apple')),
            account_id TEXT,
            collection_id TEXT NOT NULL,
            device_id TEXT,
            CHECK((provider='google' AND account_id IS NOT NULL AND device_id IS NULL)
               OR (provider='apple' AND account_id IS NULL AND device_id IS NOT NULL))
        );
        """,
    ),
    (
        36,
        """
        ALTER TABLE model_role_configs ADD COLUMN budget_json TEXT NOT NULL DEFAULT '{}';
        """,
    ),
    (
        37,
        """
        ALTER TABLE channel_turns ADD COLUMN input_kind TEXT NOT NULL DEFAULT 'text'
            CHECK(input_kind IN ('text', 'image', 'audio'));
        """,
    ),
    (
        38,
        """
        CREATE UNIQUE INDEX channel_bindings_identity_idx
            ON channel_bindings(binding_id, connection_id);
        CREATE UNIQUE INDEX channel_turns_identity_idx
            ON channel_turns(channel_turn_id, binding_id, connection_id);
        CREATE INDEX channel_turns_owner_idle_idx
            ON channel_turns(binding_id, accepted_at DESC, channel_turn_id DESC);
        CREATE INDEX channel_turns_binding_status_idx ON channel_turns(binding_id,status);
        CREATE INDEX generations_channel_active_idx ON generations(session_id,generation_id)
            WHERE invalidated_at IS NULL
              AND state NOT IN ('completed','cancelled','failed','timed_out');
        CREATE TABLE channel_proactive_policies (
            connection_id TEXT PRIMARY KEY REFERENCES channel_connections(connection_id),
            policy_json TEXT NOT NULL CHECK(json_valid(policy_json)),
            revision INTEGER NOT NULL CHECK(revision >= 1),
            binding_id TEXT REFERENCES channel_bindings(binding_id),
            authorized_route_revision INTEGER,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE channel_proactive_episodes (
            binding_id TEXT NOT NULL REFERENCES channel_bindings(binding_id),
            source TEXT NOT NULL CHECK(source = 'idle_check_in'),
            anchor_channel_turn_id TEXT NOT NULL REFERENCES channel_turns(channel_turn_id),
            not_before_at TEXT NOT NULL, expires_at TEXT NOT NULL,
            revoked_at TEXT,
            PRIMARY KEY(binding_id, source, anchor_channel_turn_id)
        );
        CREATE TABLE channel_outbound_intents (
            request_id TEXT PRIMARY KEY,
            connection_id TEXT NOT NULL REFERENCES channel_connections(connection_id),
            binding_id TEXT NOT NULL,
            source TEXT NOT NULL CHECK(source = 'idle_check_in'),
            source_event_key TEXT NOT NULL UNIQUE,
            anchor_channel_turn_id TEXT NOT NULL,
            account_key TEXT NOT NULL, sender_key TEXT NOT NULL, conversation_key TEXT NOT NULL,
            character_id TEXT NOT NULL, principal_scope TEXT NOT NULL,
            session_id TEXT NOT NULL REFERENCES sessions(session_id) ON DELETE RESTRICT,
            turn_id TEXT NOT NULL UNIQUE, generation_id TEXT NOT NULL UNIQUE,
            audio_stream_id TEXT NOT NULL UNIQUE,
            policy_revision INTEGER NOT NULL CHECK(policy_revision >= 1),
            route_revision INTEGER NOT NULL CHECK(route_revision >= 1),
            revision INTEGER NOT NULL DEFAULT 0 CHECK(revision >= 0),
            status TEXT NOT NULL CHECK(status IN ('pending','generating','planned','settled')),
            budget_day TEXT NOT NULL,
            not_before_at TEXT NOT NULL, expires_at TEXT NOT NULL,
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
            settled_at TEXT, settled_reason TEXT, cancel_requested_at TEXT, cancel_reason TEXT,
            reply_text TEXT, reply_sha256 TEXT, error_json TEXT,
            UNIQUE(binding_id, source, anchor_channel_turn_id),
            UNIQUE(request_id, binding_id, connection_id),
            FOREIGN KEY(binding_id, connection_id)
                REFERENCES channel_bindings(binding_id, connection_id),
            FOREIGN KEY(anchor_channel_turn_id, binding_id, connection_id)
                REFERENCES channel_turns(channel_turn_id, binding_id, connection_id),
            CHECK(expires_at > not_before_at),
            CHECK((status = 'settled') = (settled_at IS NOT NULL)),
            CHECK(reply_text IS NULL OR length(reply_text) BETWEEN 1 AND 2000)
        );
        CREATE UNIQUE INDEX channel_outbound_active_binding_idx
            ON channel_outbound_intents(binding_id) WHERE status != 'settled';
        CREATE INDEX channel_outbound_active_idx
            ON channel_outbound_intents(status, created_at, request_id);
        CREATE INDEX channel_outbound_budget_idx
            ON channel_outbound_intents(connection_id, budget_day, created_at);
        CREATE INDEX channel_outbound_history_idx
            ON channel_outbound_intents(connection_id, created_at DESC, request_id DESC);
        CREATE TRIGGER channel_outbound_capacity
        BEFORE INSERT ON channel_outbound_intents WHEN NEW.status != 'settled'
          AND (SELECT count(*) FROM channel_outbound_intents WHERE status != 'settled') >= 32
        BEGIN SELECT RAISE(ABORT, 'proactive global capacity exceeded'); END;

        -- Copy both sides before dropping the old child; foreign keys remain ON throughout.
        CREATE TABLE channel_deliveries_v38 (
            delivery_id TEXT PRIMARY KEY,
            channel_turn_id TEXT UNIQUE,
            outbound_intent_id TEXT UNIQUE,
            connection_id TEXT NOT NULL REFERENCES channel_connections(connection_id)
                ON DELETE CASCADE,
            binding_id TEXT NOT NULL,
            status TEXT NOT NULL
                CHECK(status IN ('pending','sending','delivered','failed','cancelled')),
            attempt INTEGER NOT NULL DEFAULT 1 CHECK(attempt >= 1),
            provider_message_id TEXT, last_error_json TEXT,
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL, delivered_at TEXT,
            lease_id TEXT, lease_expires_at TEXT,
            plan_version INTEGER NOT NULL DEFAULT 1 CHECK(plan_version >= 1),
            cancel_requested_at TEXT,
            CHECK((channel_turn_id IS NULL) != (outbound_intent_id IS NULL)),
            FOREIGN KEY(channel_turn_id, binding_id, connection_id)
                REFERENCES channel_turns(channel_turn_id, binding_id, connection_id)
                ON DELETE CASCADE,
            FOREIGN KEY(outbound_intent_id, binding_id, connection_id)
                REFERENCES channel_outbound_intents(request_id, binding_id, connection_id)
        );
        INSERT INTO channel_deliveries_v38
            SELECT d.delivery_id, d.channel_turn_id, NULL, d.connection_id, t.binding_id,
                   d.status,d.attempt,d.provider_message_id,d.last_error_json,
                   d.created_at,d.updated_at,d.delivered_at,d.lease_id,d.lease_expires_at,
                   d.plan_version,d.cancel_requested_at
            FROM channel_deliveries d JOIN channel_turns t
                ON t.channel_turn_id=d.channel_turn_id;
        CREATE TABLE channel_delivery_parts_v38 (
            part_id TEXT PRIMARY KEY,
            delivery_id TEXT NOT NULL REFERENCES channel_deliveries_v38(delivery_id)
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
        INSERT INTO channel_delivery_parts_v38 SELECT * FROM channel_delivery_parts;
        DROP TABLE channel_delivery_parts;
        DROP TABLE channel_deliveries;
        ALTER TABLE channel_deliveries_v38 RENAME TO channel_deliveries;
        ALTER TABLE channel_delivery_parts_v38 RENAME TO channel_delivery_parts;
        CREATE INDEX channel_deliveries_connection_status_idx
            ON channel_deliveries(connection_id,status,updated_at DESC);
        CREATE INDEX channel_deliveries_binding_status_idx
            ON channel_deliveries(binding_id,status,created_at);
        CREATE INDEX channel_deliveries_lease_idx ON channel_deliveries(status,lease_expires_at)
            WHERE status = 'sending';
        CREATE INDEX channel_delivery_parts_delivery_idx
            ON channel_delivery_parts(delivery_id,ordinal ASC);
        CREATE INDEX channel_delivery_parts_claim_idx
            ON channel_delivery_parts(delivery_id,status,ordinal ASC,not_before_at ASC);
        CREATE INDEX channel_delivery_parts_lease_idx
            ON channel_delivery_parts(status,lease_expires_at) WHERE status = 'sending';
        """,
    ),
    (
        39,
        """
        ALTER TABLE sessions ADD COLUMN state_scope TEXT NOT NULL DEFAULT 'local';
        UPDATE sessions SET state_scope=user_scope;
        DROP TRIGGER sessions_scope_immutable;
        CREATE TRIGGER sessions_scope_immutable
        BEFORE UPDATE OF participant_id, scene_id, scene_kind, audience_json,
            user_scope, state_scope ON sessions
        WHEN NEW.participant_id IS NOT OLD.participant_id OR NEW.scene_id IS NOT OLD.scene_id
          OR NEW.scene_kind IS NOT OLD.scene_kind OR NEW.audience_json IS NOT OLD.audience_json
          OR NEW.user_scope IS NOT OLD.user_scope OR NEW.state_scope IS NOT OLD.state_scope
        BEGIN SELECT RAISE(ABORT, 'session conversation scope is immutable'); END;
        DROP INDEX memory_records_active_text_unique_idx;
        CREATE UNIQUE INDEX memory_records_active_text_unique_idx
            ON memory_records(namespace, COALESCE(subject_id,''), normalized_text)
            WHERE state='active';
        """,
    ),
)


# Prepared separately until migration 39 is integrated.
GROUP_MIGRATION40_SQL = r"""

CREATE TABLE channel_participant_links (
 link_id TEXT PRIMARY KEY, provider_id TEXT NOT NULL CHECK(provider_id='qq_napcat'),
 account_key TEXT NOT NULL, sender_key TEXT NOT NULL,
 participant_id TEXT NOT NULL REFERENCES participants(participant_id),
 enabled INTEGER NOT NULL CHECK(enabled IN (0,1)), revision INTEGER NOT NULL CHECK(revision>=1),
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
 UNIQUE(provider_id,account_key,sender_key), UNIQUE(link_id,participant_id,sender_key),
 UNIQUE(link_id,account_key,sender_key,participant_id)
);
CREATE TRIGGER channel_participant_identity_immutable
BEFORE UPDATE OF provider_id,account_key,sender_key,participant_id ON channel_participant_links
BEGIN SELECT RAISE(ABORT,'participant link identity is immutable'); END;
CREATE TABLE channel_group_audience_observations (
 observation_id TEXT PRIMARY KEY,
 connection_id TEXT NOT NULL REFERENCES channel_connections(connection_id),
 connection_revision INTEGER NOT NULL CHECK(connection_revision>=1),
 account_key TEXT NOT NULL, group_id TEXT NOT NULL,
 member_ids_json TEXT NOT NULL CHECK(json_valid(member_ids_json)),
 observed_at TEXT NOT NULL, expires_at TEXT NOT NULL CHECK(expires_at>observed_at)
);
CREATE INDEX channel_group_observation_idx
 ON channel_group_audience_observations(connection_id,observed_at DESC,observation_id DESC);
CREATE TABLE channel_group_routes (
 route_id TEXT PRIMARY KEY,
 connection_id TEXT NOT NULL REFERENCES channel_connections(connection_id),
 account_key TEXT NOT NULL, group_id TEXT NOT NULL, character_id TEXT NOT NULL,
 scene_id TEXT NOT NULL REFERENCES conversation_scenes(scene_id),
 display_name TEXT NOT NULL, revision INTEGER NOT NULL CHECK(revision>=1),
 enabled INTEGER NOT NULL DEFAULT 0 CHECK(enabled IN (0,1)), pause_reason TEXT,
 observation_id TEXT NOT NULL REFERENCES channel_group_audience_observations(observation_id),
 audience_fingerprint TEXT NOT NULL CHECK(length(audience_fingerprint)=64),
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL, deleted_at TEXT,
 UNIQUE(connection_id,group_id), UNIQUE(route_id,connection_id),
 CHECK(enabled=0 OR (deleted_at IS NULL AND pause_reason IS NULL))
);
CREATE TRIGGER channel_group_route_identity_immutable
BEFORE UPDATE OF connection_id,account_key,group_id,character_id ON channel_group_routes
BEGIN SELECT RAISE(ABORT,'group route identity is immutable'); END;
CREATE TABLE channel_group_route_versions (
 route_id TEXT NOT NULL, revision INTEGER NOT NULL CHECK(revision>=1),
 connection_id TEXT NOT NULL, account_key TEXT NOT NULL, group_id TEXT NOT NULL,
 character_id TEXT NOT NULL, scene_id TEXT NOT NULL REFERENCES conversation_scenes(scene_id),
 observation_id TEXT NOT NULL REFERENCES channel_group_audience_observations(observation_id),
 audience_fingerprint TEXT NOT NULL, enabled INTEGER NOT NULL CHECK(enabled IN (0,1)),
 pause_reason TEXT, created_at TEXT NOT NULL,
 PRIMARY KEY(route_id,revision), UNIQUE(route_id,revision,account_key),
 UNIQUE(route_id,revision,connection_id),
 FOREIGN KEY(route_id,connection_id) REFERENCES channel_group_routes(route_id,connection_id)
);
CREATE TRIGGER channel_group_version_no_update BEFORE UPDATE ON channel_group_route_versions
BEGIN SELECT RAISE(ABORT,'group route versions are immutable'); END;
CREATE TRIGGER channel_group_version_no_delete BEFORE DELETE ON channel_group_route_versions
BEGIN SELECT RAISE(ABORT,'group route versions are immutable'); END;
CREATE TABLE channel_group_route_members (
 route_id TEXT NOT NULL, route_revision INTEGER NOT NULL, account_key TEXT NOT NULL,
 link_id TEXT NOT NULL, sender_key TEXT NOT NULL, participant_id TEXT NOT NULL,
 can_speak INTEGER NOT NULL CHECK(can_speak IN (0,1)),
 PRIMARY KEY(route_id,route_revision,sender_key), UNIQUE(route_id,route_revision,participant_id),
 FOREIGN KEY(route_id,route_revision,account_key)
   REFERENCES channel_group_route_versions(route_id,revision,account_key),
 FOREIGN KEY(link_id,account_key,sender_key,participant_id)
   REFERENCES channel_participant_links(link_id,account_key,sender_key,participant_id)
);
CREATE TRIGGER channel_group_member_no_update BEFORE UPDATE ON channel_group_route_members
BEGIN SELECT RAISE(ABORT,'group route members are immutable'); END;
CREATE TRIGGER channel_group_member_no_delete BEFORE DELETE ON channel_group_route_members
BEGIN SELECT RAISE(ABORT,'group route members are immutable'); END;

CREATE TABLE channel_bindings_v40 (
            binding_id TEXT PRIMARY KEY,
            connection_id TEXT NOT NULL
                REFERENCES channel_connections(connection_id) ON DELETE CASCADE,
            conversation_key TEXT NOT NULL,
            sender_key TEXT NOT NULL,
            session_id TEXT NOT NULL REFERENCES sessions(session_id) ON DELETE RESTRICT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            chat_type TEXT NOT NULL DEFAULT 'direct' CHECK(chat_type IN ('direct','group')),
 legacy_group_provenance INTEGER NOT NULL DEFAULT 0 CHECK(legacy_group_provenance IN (0,1)),
 group_route_id TEXT REFERENCES channel_group_routes(route_id),
 scene_id TEXT REFERENCES conversation_scenes(scene_id),
 participant_link_id TEXT, participant_id TEXT,
 FOREIGN KEY(group_route_id,connection_id)
  REFERENCES channel_group_routes(route_id,connection_id),
 FOREIGN KEY(participant_link_id,participant_id,sender_key)
  REFERENCES channel_participant_links(link_id,participant_id,sender_key),
 CHECK((chat_type='direct' AND group_route_id IS NULL AND scene_id IS NULL
   AND participant_link_id IS NULL AND participant_id IS NULL) OR
  (chat_type='group' AND group_route_id IS NOT NULL AND scene_id IS NOT NULL
   AND participant_link_id IS NOT NULL AND participant_id IS NOT NULL
   AND legacy_group_provenance=0)),
            UNIQUE(connection_id, session_id)
        );
CREATE UNIQUE INDEX channel_bindings_identity_idx_v40
            ON channel_bindings_v40(binding_id, connection_id);
CREATE UNIQUE INDEX channel_bindings_group_identity_idx_v40
 ON channel_bindings_v40(binding_id,connection_id,group_route_id);
INSERT INTO channel_bindings_v40(binding_id,connection_id,conversation_key,sender_key,
 session_id,created_at,updated_at,legacy_group_provenance)
 SELECT b.binding_id,b.connection_id,b.conversation_key,b.sender_key,
 b.session_id,b.created_at,b.updated_at,EXISTS(
  SELECT 1 FROM channel_turns t WHERE t.binding_id=b.binding_id AND t.chat_type='group'
 ) FROM channel_bindings b;

CREATE TABLE channel_turns_v40 (
            channel_turn_id TEXT PRIMARY KEY,
            connection_id TEXT NOT NULL
                REFERENCES channel_connections(connection_id) ON DELETE CASCADE,
            binding_id TEXT NOT NULL REFERENCES channel_bindings_v40(binding_id) ON DELETE CASCADE,
            external_message_id TEXT NOT NULL,
            content_sha256 TEXT NOT NULL,
            account_key TEXT,
            conversation_key TEXT NOT NULL,
            chat_type TEXT NOT NULL DEFAULT 'direct'
                CHECK(chat_type IN ('direct', 'group')),
            conversation_label TEXT,
            sender_key TEXT NOT NULL,
            sender_display_name TEXT,
            principal_scope TEXT NOT NULL,
            session_id TEXT NOT NULL REFERENCES sessions(session_id) ON DELETE RESTRICT,
            turn_id TEXT NOT NULL,
            generation_id TEXT NOT NULL,
            status TEXT NOT NULL
                CHECK(status IN (
                    'accepted', 'processing', 'completed', 'cancelling',
                    'cancelled', 'failed', 'timed_out'
                )),
            reply_text TEXT,
            error_json TEXT,
            delivery_id TEXT UNIQUE,
            revision INTEGER NOT NULL DEFAULT 0 CHECK(revision >= 0),
            accepted_at TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            completed_at TEXT, input_kind TEXT NOT NULL DEFAULT 'text'
            CHECK(input_kind IN ('text', 'image', 'audio')),
            group_lineage_version INTEGER NOT NULL DEFAULT 0 CHECK(group_lineage_version IN (0,1)),
 group_route_id TEXT, group_route_revision INTEGER,
 FOREIGN KEY(group_route_id,group_route_revision,connection_id)
  REFERENCES channel_group_route_versions(route_id,revision,connection_id),
 FOREIGN KEY(binding_id,connection_id,group_route_id)
  REFERENCES channel_bindings_v40(binding_id,connection_id,group_route_id),
 CHECK((group_lineage_version=0 AND group_route_id IS NULL AND group_route_revision IS NULL)
 OR (group_lineage_version=1 AND chat_type='group' AND group_route_id IS NOT NULL
 AND group_route_revision>=1)),
 UNIQUE(channel_turn_id,connection_id,group_route_id,group_route_revision),
 UNIQUE(connection_id,chat_type,conversation_key,external_message_id)
        );
CREATE UNIQUE INDEX channel_turns_identity_idx_v40
            ON channel_turns_v40(channel_turn_id, binding_id, connection_id);
INSERT INTO channel_turns_v40(channel_turn_id,connection_id,binding_id,external_message_id,
 content_sha256,account_key,conversation_key,chat_type,conversation_label,sender_key,
 sender_display_name,principal_scope,session_id,turn_id,generation_id,status,reply_text,
 error_json,delivery_id,revision,accepted_at,created_at,updated_at,completed_at,input_kind)
 SELECT channel_turn_id,connection_id,binding_id,external_message_id,content_sha256,account_key,
 conversation_key,chat_type,conversation_label,sender_key,sender_display_name,principal_scope,
 session_id,turn_id,generation_id,status,reply_text,error_json,delivery_id,revision,accepted_at,
 created_at,updated_at,completed_at,input_kind FROM channel_turns;

CREATE TABLE channel_turn_burst_members_v40 (
            burst_id TEXT NOT NULL,
            leader_channel_turn_id TEXT NOT NULL
                REFERENCES channel_turns_v40(channel_turn_id) ON DELETE CASCADE,
            member_channel_turn_id TEXT NOT NULL PRIMARY KEY
                REFERENCES channel_turns_v40(channel_turn_id) ON DELETE CASCADE,
            ordinal INTEGER NOT NULL CHECK(ordinal >= 0 AND ordinal < 4),
            received_at TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
INSERT INTO channel_turn_burst_members_v40(burst_id,leader_channel_turn_id,
 member_channel_turn_id,ordinal,received_at,created_at) SELECT burst_id,leader_channel_turn_id,
 member_channel_turn_id,ordinal,received_at,created_at FROM channel_turn_burst_members;

CREATE TABLE channel_proactive_policies_v40 (
            connection_id TEXT PRIMARY KEY REFERENCES channel_connections(connection_id),
            policy_json TEXT NOT NULL CHECK(json_valid(policy_json)),
            revision INTEGER NOT NULL CHECK(revision >= 1),
            binding_id TEXT REFERENCES channel_bindings_v40(binding_id),
            authorized_route_revision INTEGER,
            updated_at TEXT NOT NULL
        );
INSERT INTO channel_proactive_policies_v40(connection_id,policy_json,revision,binding_id,
 authorized_route_revision,updated_at) SELECT connection_id,policy_json,revision,binding_id,
 authorized_route_revision,updated_at FROM channel_proactive_policies;

CREATE TABLE channel_proactive_episodes_v40 (
            binding_id TEXT NOT NULL REFERENCES channel_bindings_v40(binding_id),
            source TEXT NOT NULL CHECK(source = 'idle_check_in'),
            anchor_channel_turn_id TEXT NOT NULL REFERENCES channel_turns_v40(channel_turn_id),
            not_before_at TEXT NOT NULL, expires_at TEXT NOT NULL,
            revoked_at TEXT,
            PRIMARY KEY(binding_id, source, anchor_channel_turn_id)
        );
INSERT INTO channel_proactive_episodes_v40(binding_id,source,anchor_channel_turn_id,
 not_before_at,expires_at,revoked_at) SELECT binding_id,source,anchor_channel_turn_id,
 not_before_at,expires_at,revoked_at FROM channel_proactive_episodes;

CREATE TABLE channel_outbound_intents_v40 (
            request_id TEXT PRIMARY KEY,
            connection_id TEXT NOT NULL REFERENCES channel_connections(connection_id),
            binding_id TEXT NOT NULL,
            source TEXT NOT NULL CHECK(source = 'idle_check_in'),
            source_event_key TEXT NOT NULL UNIQUE,
            anchor_channel_turn_id TEXT NOT NULL,
            account_key TEXT NOT NULL, sender_key TEXT NOT NULL, conversation_key TEXT NOT NULL,
            character_id TEXT NOT NULL, principal_scope TEXT NOT NULL,
            session_id TEXT NOT NULL REFERENCES sessions(session_id) ON DELETE RESTRICT,
            turn_id TEXT NOT NULL UNIQUE, generation_id TEXT NOT NULL UNIQUE,
            audio_stream_id TEXT NOT NULL UNIQUE,
            policy_revision INTEGER NOT NULL CHECK(policy_revision >= 1),
            route_revision INTEGER NOT NULL CHECK(route_revision >= 1),
            revision INTEGER NOT NULL DEFAULT 0 CHECK(revision >= 0),
            status TEXT NOT NULL CHECK(status IN ('pending','generating','planned','settled')),
            budget_day TEXT NOT NULL,
            not_before_at TEXT NOT NULL, expires_at TEXT NOT NULL,
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
            settled_at TEXT, settled_reason TEXT, cancel_requested_at TEXT, cancel_reason TEXT,
            reply_text TEXT, reply_sha256 TEXT, error_json TEXT,
            UNIQUE(binding_id, source, anchor_channel_turn_id),
            UNIQUE(request_id, binding_id, connection_id),
            FOREIGN KEY(binding_id, connection_id)
                REFERENCES channel_bindings_v40(binding_id, connection_id),
            FOREIGN KEY(anchor_channel_turn_id, binding_id, connection_id)
                REFERENCES channel_turns_v40(channel_turn_id, binding_id, connection_id),
            CHECK(expires_at > not_before_at),
            CHECK((status = 'settled') = (settled_at IS NOT NULL)),
            CHECK(reply_text IS NULL OR length(reply_text) BETWEEN 1 AND 2000)
        );
CREATE UNIQUE INDEX channel_outbound_active_binding_idx_v40
            ON channel_outbound_intents_v40(binding_id) WHERE status != 'settled';
INSERT INTO channel_outbound_intents_v40(request_id,connection_id,binding_id,source,
 source_event_key,anchor_channel_turn_id,account_key,sender_key,conversation_key,character_id,
 principal_scope,session_id,turn_id,generation_id,audio_stream_id,policy_revision,
 route_revision,revision,status,budget_day,not_before_at,expires_at,created_at,updated_at,
 settled_at,settled_reason,cancel_requested_at,cancel_reason,reply_text,reply_sha256,
 error_json) SELECT request_id,connection_id,binding_id,source,source_event_key,
 anchor_channel_turn_id,account_key,sender_key,conversation_key,character_id,principal_scope,
 session_id,turn_id,generation_id,audio_stream_id,policy_revision,route_revision,revision,
 status,budget_day,not_before_at,expires_at,created_at,updated_at,settled_at,settled_reason,
 cancel_requested_at,cancel_reason,reply_text,reply_sha256,error_json FROM channel_outbound_intents;

CREATE TABLE "channel_deliveries_v40" (
            delivery_id TEXT PRIMARY KEY,
            channel_turn_id TEXT UNIQUE,
            outbound_intent_id TEXT UNIQUE,
            connection_id TEXT NOT NULL REFERENCES channel_connections(connection_id)
                ON DELETE CASCADE,
            binding_id TEXT NOT NULL,
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
            CHECK((channel_turn_id IS NULL) != (outbound_intent_id IS NULL)),
            FOREIGN KEY(channel_turn_id, binding_id, connection_id)
                REFERENCES channel_turns_v40(channel_turn_id, binding_id, connection_id)
                ON DELETE CASCADE,
            FOREIGN KEY(outbound_intent_id, binding_id, connection_id)
                REFERENCES channel_outbound_intents_v40(request_id, binding_id, connection_id)
        , CHECK((group_target_json IS NULL AND group_route_id IS NULL AND group_route_revision
 IS NULL)
 OR (group_target_json IS NOT NULL AND group_route_id IS NOT NULL AND group_route_revision>=1
 AND channel_turn_id IS NOT NULL AND outbound_intent_id IS NULL)),
 FOREIGN KEY(channel_turn_id,connection_id,group_route_id,group_route_revision)
 REFERENCES channel_turns_v40(channel_turn_id,connection_id,group_route_id,group_route_revision)
);
INSERT INTO channel_deliveries_v40(delivery_id,channel_turn_id,outbound_intent_id,connection_id,
 binding_id,status,attempt,provider_message_id,last_error_json,created_at,updated_at,
 delivered_at,lease_id,lease_expires_at,plan_version,cancel_requested_at) SELECT delivery_id,
 channel_turn_id,outbound_intent_id,connection_id,binding_id,status,attempt,provider_message_id,
 last_error_json,created_at,updated_at,delivered_at,lease_id,lease_expires_at,plan_version,
 cancel_requested_at FROM channel_deliveries;

CREATE TABLE "channel_delivery_parts_v40" (
            part_id TEXT PRIMARY KEY,
            delivery_id TEXT NOT NULL REFERENCES "channel_deliveries_v40"(delivery_id)
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
INSERT INTO channel_delivery_parts_v40(part_id,delivery_id,ordinal,kind,payload_json,required,
 status,delay_after_ms,not_before_at,attempt,lease_id,lease_expires_at,provider_client_id,
 provider_message_id,last_error_json,created_at,updated_at,delivered_at) SELECT part_id,
 delivery_id,ordinal,kind,payload_json,required,status,delay_after_ms,not_before_at,attempt,
 lease_id,lease_expires_at,provider_client_id,provider_message_id,last_error_json,created_at,
 updated_at,delivered_at FROM channel_delivery_parts;
DROP TABLE channel_delivery_parts;
DROP TABLE channel_deliveries;
DROP TABLE channel_outbound_intents;
DROP TABLE channel_proactive_episodes;
DROP TABLE channel_proactive_policies;
DROP TABLE channel_turn_burst_members;
DROP TABLE channel_turns;
DROP TABLE channel_bindings;
ALTER TABLE channel_bindings_v40 RENAME TO channel_bindings;
ALTER TABLE channel_turns_v40 RENAME TO channel_turns;
ALTER TABLE channel_turn_burst_members_v40 RENAME TO channel_turn_burst_members;
ALTER TABLE channel_proactive_policies_v40 RENAME TO channel_proactive_policies;
ALTER TABLE channel_proactive_episodes_v40 RENAME TO channel_proactive_episodes;
ALTER TABLE channel_outbound_intents_v40 RENAME TO channel_outbound_intents;
ALTER TABLE channel_deliveries_v40 RENAME TO channel_deliveries;
ALTER TABLE channel_delivery_parts_v40 RENAME TO channel_delivery_parts;
CREATE INDEX channel_bindings_connection_idx
            ON channel_bindings(connection_id, updated_at DESC);
CREATE INDEX channel_turns_connection_status_idx
            ON channel_turns(connection_id, status, updated_at DESC);
CREATE INDEX channel_turns_generation_idx
            ON channel_turns(generation_id);
CREATE INDEX channel_turns_owner_idle_idx
            ON channel_turns(binding_id, accepted_at DESC, channel_turn_id DESC);
CREATE INDEX channel_turns_binding_status_idx ON channel_turns(binding_id,status);
CREATE INDEX channel_turn_burst_members_leader_idx
            ON channel_turn_burst_members(leader_channel_turn_id);
CREATE INDEX channel_turn_burst_members_burst_idx
            ON channel_turn_burst_members(burst_id);
CREATE INDEX channel_outbound_active_idx
            ON channel_outbound_intents(status, created_at, request_id);
CREATE INDEX channel_outbound_budget_idx
            ON channel_outbound_intents(connection_id, budget_day, created_at);
CREATE INDEX channel_outbound_history_idx
            ON channel_outbound_intents(connection_id, created_at DESC, request_id DESC);
CREATE TRIGGER channel_outbound_capacity
        BEFORE INSERT ON channel_outbound_intents WHEN NEW.status != 'settled'
          AND (SELECT count(*) FROM channel_outbound_intents WHERE status != 'settled') >= 32
        BEGIN SELECT RAISE(ABORT, 'proactive global capacity exceeded'); END;
CREATE INDEX channel_deliveries_connection_status_idx
            ON channel_deliveries(connection_id,status,updated_at DESC);
CREATE INDEX channel_deliveries_binding_status_idx
            ON channel_deliveries(binding_id,status,created_at);
CREATE INDEX channel_deliveries_lease_idx ON channel_deliveries(status,lease_expires_at)
            WHERE status = 'sending';
CREATE INDEX channel_delivery_parts_delivery_idx
            ON channel_delivery_parts(delivery_id,ordinal ASC);
CREATE INDEX channel_delivery_parts_claim_idx
            ON channel_delivery_parts(delivery_id,status,ordinal ASC,not_before_at ASC);
CREATE INDEX channel_delivery_parts_lease_idx
            ON channel_delivery_parts(status,lease_expires_at) WHERE status = 'sending';

DROP INDEX channel_bindings_identity_idx_v40;
CREATE UNIQUE INDEX channel_bindings_identity_idx ON channel_bindings(binding_id,connection_id);
DROP INDEX channel_bindings_group_identity_idx_v40;
CREATE UNIQUE INDEX channel_bindings_group_identity_idx
 ON channel_bindings(binding_id,connection_id,group_route_id);
DROP INDEX channel_turns_identity_idx_v40;
CREATE UNIQUE INDEX channel_turns_identity_idx ON channel_turns(channel_turn_id,binding_id,
 connection_id);
DROP INDEX channel_outbound_active_binding_idx_v40;
CREATE UNIQUE INDEX channel_outbound_active_binding_idx ON channel_outbound_intents(binding_id)
 WHERE status != 'settled';
CREATE UNIQUE INDEX channel_bindings_direct_route_idx
 ON channel_bindings(connection_id,conversation_key)
 WHERE chat_type='direct' AND legacy_group_provenance=0;
CREATE TRIGGER channel_bindings_legacy_provenance_immutable
 BEFORE UPDATE OF legacy_group_provenance ON channel_bindings
BEGIN SELECT RAISE(ABORT,'legacy group provenance is immutable'); END;
CREATE UNIQUE INDEX channel_bindings_group_member_idx
 ON channel_bindings(connection_id,group_route_id,scene_id,sender_key) WHERE chat_type='group';
CREATE INDEX channel_group_turns_idx ON channel_turns(group_route_id,status,accepted_at);
CREATE UNIQUE INDEX channel_group_turn_route_identity_idx
 ON channel_turns(channel_turn_id,group_route_id);
CREATE TABLE channel_group_route_heads (
 route_id TEXT PRIMARY KEY REFERENCES channel_group_routes(route_id),
 latest_channel_turn_id TEXT,
 active_channel_turn_id TEXT,
 pending_channel_turn_id TEXT,
 updated_at TEXT NOT NULL,
 CHECK(active_channel_turn_id IS NULL OR active_channel_turn_id!=pending_channel_turn_id),
 FOREIGN KEY(latest_channel_turn_id,route_id)
  REFERENCES channel_turns(channel_turn_id,group_route_id),
 FOREIGN KEY(active_channel_turn_id,route_id)
  REFERENCES channel_turns(channel_turn_id,group_route_id),
 FOREIGN KEY(pending_channel_turn_id,route_id)
  REFERENCES channel_turns(channel_turn_id,group_route_id)
);
CREATE TRIGGER channel_group_heads_capacity BEFORE UPDATE OF active_channel_turn_id
 ON channel_group_route_heads WHEN NEW.active_channel_turn_id IS NOT NULL
 AND OLD.active_channel_turn_id IS NULL
 AND (SELECT count(*) FROM channel_group_route_heads WHERE active_channel_turn_id IS NOT NULL)>=32
BEGIN SELECT RAISE(ABORT,'group work capacity exceeded'); END;
CREATE TRIGGER channel_group_heads_insert_capacity BEFORE INSERT ON channel_group_route_heads
 WHEN NEW.active_channel_turn_id IS NOT NULL
 AND (SELECT count(*) FROM channel_group_route_heads WHERE active_channel_turn_id IS NOT NULL)>=32
BEGIN SELECT RAISE(ABORT,'group work capacity exceeded'); END;
CREATE TRIGGER channel_group_target_immutable BEFORE UPDATE OF group_target_json,group_route_id,
 group_route_revision ON channel_deliveries
BEGIN SELECT RAISE(ABORT,'group delivery target is immutable'); END;
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
CREATE TRIGGER channel_group_text_only BEFORE INSERT ON channel_delivery_parts
 WHEN (SELECT group_target_json FROM channel_deliveries WHERE delivery_id=NEW.delivery_id) IS
 NOT NULL
 AND (NEW.kind!='text' OR NEW.ordinal!=0 OR NEW.required!=1
  OR NEW.delay_after_ms!=0 OR NEW.not_before_at IS NOT NULL)
BEGIN SELECT RAISE(ABORT,'group delivery requires one immediate text part'); END;
CREATE TRIGGER channel_group_part_content_immutable BEFORE UPDATE OF kind,ordinal,
 required,delay_after_ms,not_before_at,payload_json,part_id,delivery_id,provider_client_id
 ON channel_delivery_parts
 WHEN (SELECT group_target_json FROM channel_deliveries WHERE delivery_id=OLD.delivery_id) IS
 NOT NULL
BEGIN SELECT RAISE(ABORT,'group delivery content is immutable'); END;
"""

GROUP_BUBBLES_MIGRATION41_SQL = """
DROP TRIGGER channel_group_text_only;
DROP TRIGGER channel_group_part_content_immutable;
CREATE TRIGGER channel_group_text_only BEFORE INSERT ON channel_delivery_parts
 WHEN (SELECT group_target_json FROM channel_deliveries WHERE delivery_id=NEW.delivery_id)
 IS NOT NULL
 AND (NEW.kind!='text' OR NEW.ordinal<0 OR NEW.ordinal>=10 OR NEW.required!=1
  OR NEW.ordinal!=(SELECT count(*) FROM channel_delivery_parts WHERE delivery_id=NEW.delivery_id)
  OR NEW.delay_after_ms<0 OR NEW.delay_after_ms>30000 OR NEW.not_before_at IS NOT NULL
  OR NEW.status!='pending' OR NEW.attempt!=0
  OR NOT json_valid(NEW.payload_json)
  OR json_extract(NEW.payload_json,'$.kind') IS NOT 'text'
  OR json_type(NEW.payload_json,'$.text') IS NOT 'text'
  OR length(trim(json_extract(NEW.payload_json,'$.text')))=0)
BEGIN SELECT RAISE(ABORT,'group delivery requires bounded ordered text parts'); END;
CREATE TRIGGER channel_group_part_content_immutable BEFORE UPDATE OF kind,ordinal,
 required,delay_after_ms,payload_json,part_id,delivery_id,provider_client_id
 ON channel_delivery_parts
 WHEN (SELECT group_target_json FROM channel_deliveries WHERE delivery_id=OLD.delivery_id)
 IS NOT NULL
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
"""

GROUP_STICKERS_MIGRATION42_SQL = """
DROP TRIGGER channel_group_text_only;
CREATE TRIGGER channel_group_text_only BEFORE INSERT ON channel_delivery_parts
 WHEN (SELECT group_target_json FROM channel_deliveries WHERE delivery_id=NEW.delivery_id)
 IS NOT NULL
BEGIN
 SELECT CASE WHEN (
  NEW.ordinal!=(SELECT count(*) FROM channel_delivery_parts WHERE delivery_id=NEW.delivery_id)
  OR NEW.not_before_at IS NOT NULL OR NEW.status!='pending' OR NEW.attempt!=0
  OR NOT json_valid(NEW.payload_json)
  OR EXISTS (SELECT 1 FROM channel_delivery_parts
             WHERE delivery_id=NEW.delivery_id AND kind!='text')
  OR NOT (
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
     JOIN channel_group_routes r ON r.route_id=json_extract(d.group_target_json,'$.route_id')
     JOIN learned_stickers s ON s.principal_scope='scene:'||r.scene_id
       AND s.character_id=r.character_id
     WHERE d.delivery_id=NEW.delivery_id
       AND r.scene_id=json_extract(d.group_target_json,'$.scene_id')
       AND r.revision=json_extract(d.group_target_json,'$.route_revision')
       AND r.enabled=1 AND r.deleted_at IS NULL
       AND s.sticker_id=json_extract(NEW.payload_json,'$.sticker_id')
       AND s.sha256=json_extract(NEW.payload_json,'$.sha256')
       AND s.mime_type=json_extract(NEW.payload_json,'$.mime_type')
    ))
  )
 ) THEN RAISE(ABORT,'group delivery requires bounded ordered text and scoped optional image') END;
END;
"""

GROUP_VOICE_MIGRATION43_SQL = """
ALTER TABLE channel_group_routes ADD COLUMN allow_requested_voice INTEGER NOT NULL
 DEFAULT 0 CHECK(allow_requested_voice IN (0,1));
ALTER TABLE channel_group_route_versions ADD COLUMN allow_requested_voice INTEGER NOT NULL
 DEFAULT 0 CHECK(allow_requested_voice IN (0,1));
DROP TRIGGER channel_group_text_only;
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
DROP TRIGGER channel_group_part_content_immutable;
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
"""

CHANNEL_SETTINGS_MIGRATION44_SQL = """
CREATE TABLE channel_runtime_settings (
 singleton_id INTEGER PRIMARY KEY CHECK(singleton_id=1),
 revision INTEGER NOT NULL CHECK(revision>=1),
 policy_json TEXT NOT NULL CHECK(json_valid(policy_json)),
 updated_at TEXT NOT NULL
);
"""

_CHANNEL_MIGRATIONS = (
    *_BASE_MIGRATIONS,
    (40, GROUP_MIGRATION40_SQL),
    (41, GROUP_BUBBLES_MIGRATION41_SQL),
    (42, GROUP_STICKERS_MIGRATION42_SQL),
    (43, GROUP_VOICE_MIGRATION43_SQL),
    (44, CHANNEL_SETTINGS_MIGRATION44_SQL),
)


_AGENT_TASK_MIGRATION = """
CREATE TABLE agent_tasks (
 task_id TEXT PRIMARY KEY, scope TEXT NOT NULL, revision INTEGER NOT NULL,
 state TEXT NOT NULL, payload_json TEXT NOT NULL
);
CREATE INDEX agent_tasks_scope_idx ON agent_tasks(scope,task_id);
CREATE TABLE agent_task_steps (
 sequence INTEGER PRIMARY KEY AUTOINCREMENT,
 task_id TEXT NOT NULL REFERENCES agent_tasks(task_id) ON DELETE CASCADE,
 step_key TEXT NOT NULL, payload_json TEXT NOT NULL, UNIQUE(task_id,step_key)
);
CREATE TABLE agent_events (
 event_id TEXT PRIMARY KEY, task_id TEXT REFERENCES agent_tasks(task_id) ON DELETE CASCADE,
 settled INTEGER NOT NULL DEFAULT 0, payload_json TEXT NOT NULL
);
CREATE TABLE agent_artifacts (
 artifact_id TEXT PRIMARY KEY, scope TEXT NOT NULL, payload_json TEXT NOT NULL,
 relative_path TEXT NOT NULL UNIQUE
);
CREATE INDEX agent_artifacts_scope_idx ON agent_artifacts(scope,artifact_id);
CREATE TABLE agent_group_policies (
  route_id TEXT PRIMARY KEY, revision INTEGER NOT NULL, policy_json TEXT NOT NULL
);
CREATE TABLE agent_behavior_reservations (
  route_id TEXT NOT NULL, kind TEXT NOT NULL, occurred_at TEXT NOT NULL
);
CREATE TABLE agent_group_quiet (
  route_id TEXT PRIMARY KEY, until_at TEXT NOT NULL
);
CREATE TABLE agent_autonomous_turns (
  turn_id TEXT PRIMARY KEY REFERENCES channel_turns(channel_turn_id) ON DELETE CASCADE,
  route_id TEXT NOT NULL, policy_revision INTEGER NOT NULL
);
CREATE INDEX agent_behavior_budget_idx
  ON agent_behavior_reservations(route_id,kind,occurred_at);
CREATE TABLE agent_decisions (
  sequence INTEGER PRIMARY KEY AUTOINCREMENT, route_id TEXT NOT NULL,
  policy_revision INTEGER NOT NULL, occurred_at TEXT NOT NULL, decision_json TEXT NOT NULL
);
CREATE TABLE agent_development_policy (
  singleton INTEGER PRIMARY KEY CHECK(singleton=1), revision INTEGER NOT NULL,
  payload_json TEXT NOT NULL
);
CREATE TABLE agent_candidates (
  candidate_id TEXT PRIMARY KEY, day TEXT NOT NULL UNIQUE, payload_json TEXT NOT NULL,
  revision INTEGER NOT NULL
);
"""

MIGRATIONS = (*_CHANNEL_MIGRATIONS, (45, _AGENT_TASK_MIGRATION), (46, AGENT_DELIVERY_MIGRATION_SQL))
