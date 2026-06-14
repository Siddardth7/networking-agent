-- 004_search_cache.sql
-- v0.2.1 free-quota work: cache provider search responses so repeat
-- queries (re-runs, resumed runs, trial iterations) never re-spend
-- paid/limited search credits.

CREATE TABLE IF NOT EXISTS search_cache (
    cache_key  TEXT PRIMARY KEY,
    provider   TEXT NOT NULL,
    query      TEXT NOT NULL,
    response   TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_search_cache_provider
    ON search_cache (provider, created_at);
