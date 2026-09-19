PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS collection_runs (
  id TEXT PRIMARY KEY, source_id TEXT NOT NULL, started_at TEXT NOT NULL,
  finished_at TEXT, status TEXT NOT NULL CHECK(status IN ('running','ok','partial','failed','skipped')),
  request_count INTEGER NOT NULL DEFAULT 0, accepted_count INTEGER NOT NULL DEFAULT 0,
  rejected_count INTEGER NOT NULL DEFAULT 0, reason TEXT, raw_prefix TEXT
);
CREATE INDEX IF NOT EXISTS runs_source_time ON collection_runs(source_id, started_at);
CREATE TABLE IF NOT EXISTS daily_quota (
  source_id TEXT NOT NULL, day TEXT NOT NULL, used INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY(source_id, day)
);
CREATE TABLE IF NOT EXISTS observations (
  id TEXT PRIMARY KEY, source_id TEXT NOT NULL, entity_id TEXT NOT NULL,
  route_id TEXT NOT NULL, label TEXT NOT NULL, lon REAL NOT NULL, lat REAL NOT NULL,
  observed_at TEXT, retrieved_at TEXT NOT NULL, input_hash TEXT NOT NULL,
  run_id TEXT NOT NULL REFERENCES collection_runs(id),
  CHECK(lon BETWEEN 124 AND 132), CHECK(lat BETWEEN 33 AND 39)
);
CREATE INDEX IF NOT EXISTS observations_window ON observations(retrieved_at, entity_id);
CREATE TABLE IF NOT EXISTS publication_history (
  release_id TEXT PRIMARY KEY, published_at TEXT NOT NULL, previous_release_id TEXT,
  manifest_hash TEXT NOT NULL, kind TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS operation_locks (
  name TEXT PRIMARY KEY, owner TEXT NOT NULL, expires_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS pending_publications (
  hour TEXT PRIMARY KEY,
  updated_at TEXT NOT NULL
);
