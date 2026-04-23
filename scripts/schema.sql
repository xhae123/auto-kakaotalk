-- Single source of truth for state/app.db schema.
-- Loaded by db.py at init. Keep in sync with SKILL.md "State shape" section.

CREATE TABLE IF NOT EXISTS targets (
  chat_id TEXT PRIMARY KEY,
  platform TEXT NOT NULL,
  display_name TEXT NOT NULL,
  registered_at TEXT NOT NULL,
  baseline_count INTEGER DEFAULT 0,
  persona_path TEXT
);

CREATE TABLE IF NOT EXISTS messages (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  chat_id TEXT NOT NULL,
  platform TEXT NOT NULL,
  platform_msg_id TEXT,
  direction TEXT NOT NULL,          -- 'in' | 'out'
  sender TEXT,
  body TEXT NOT NULL,
  sent_at TEXT NOT NULL,            -- UTC ISO8601
  ingested_at TEXT NOT NULL,
  state TEXT NOT NULL,              -- in: queued|responded|skipped|learned
                                    -- out: drafted|sending|sent|failed
  reply_to_id INTEGER,
  draft_text TEXT,
  error TEXT,
  UNIQUE(platform, platform_msg_id)
);

CREATE INDEX IF NOT EXISTS idx_messages_chat_time
  ON messages(chat_id, sent_at);
CREATE INDEX IF NOT EXISTS idx_messages_pending
  ON messages(state) WHERE state = 'queued';

CREATE TABLE IF NOT EXISTS cursors (
  chat_id TEXT PRIMARY KEY,
  last_seen_at TEXT NOT NULL,
  last_seen_msg_id TEXT
);
