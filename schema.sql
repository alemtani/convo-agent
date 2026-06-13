-- Convo Agent — durable learning state only.
-- The server persists NO transcripts and NO turns (stateless proxy; the client
-- holds the running conversation). Every domain row is user_id / language scoped
-- so multi-user / multi-language stays additive — "build for one, design for many".

PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS user (
    id          TEXT PRIMARY KEY,
    handle      TEXT,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Pointer to kb/<language>/<id>/, NOT the markdown content.
-- max_band is DERIVED from the topic's vocab (highest HSK band used), for
-- ordering/gating; the fair-game ceiling itself is universal and lives in
-- kb/zh/_hsk/ceiling.json, never per-topic.
CREATE TABLE IF NOT EXISTS topic (
    id            TEXT NOT NULL,
    language      TEXT NOT NULL DEFAULT 'zh',
    kb_path       TEXT NOT NULL,
    content_hash  TEXT NOT NULL,   -- detects when the committed KB markdown changed
    max_band      INTEGER,
    PRIMARY KEY (id, language)
);

-- Accumulating covered-set. Covered is monotonic — every covered topic stays
-- fair game forever; covered_at also feeds the freshness term in selection.
CREATE TABLE IF NOT EXISTS covered_topic (
    user_id     TEXT NOT NULL DEFAULT 'default',
    language    TEXT NOT NULL DEFAULT 'zh',
    topic_id    TEXT NOT NULL,
    covered_at  TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (user_id, language, topic_id),
    FOREIGN KEY (user_id) REFERENCES user(id),
    FOREIGN KEY (topic_id, language) REFERENCES topic(id, language)
);

-- The only writeback path (periodic + end-of-session feedback rounds).
CREATE TABLE IF NOT EXISTS proficiency (
    user_id               TEXT NOT NULL DEFAULT 'default',
    language              TEXT NOT NULL DEFAULT 'zh',
    topic_id              TEXT NOT NULL,
    measured_scores_json  TEXT NOT NULL DEFAULT '{}',
    derived_strength      REAL,            -- 0..1; weakness drives selection weight
    last_practiced        TEXT,
    PRIMARY KEY (user_id, language, topic_id),
    FOREIGN KEY (user_id) REFERENCES user(id),
    FOREIGN KEY (topic_id, language) REFERENCES topic(id, language)
);

-- Lightweight per-session trends — NOT a transcript.
CREATE TABLE IF NOT EXISTS session_summary (
    id                     INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id                TEXT NOT NULL DEFAULT 'default',
    language               TEXT NOT NULL DEFAULT 'zh',
    topics_json            TEXT NOT NULL,   -- topics drilled this session
    started_at             TEXT NOT NULL DEFAULT (datetime('now')),
    turn_count             INTEGER NOT NULL DEFAULT 0,
    aggregate_scores_json  TEXT,
    FOREIGN KEY (user_id) REFERENCES user(id)
);
