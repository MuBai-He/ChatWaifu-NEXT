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
)
