"""Ordered SQLite migrations for the local Runtime."""

MIGRATIONS: tuple[tuple[int, str], ...] = (
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
)
