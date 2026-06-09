-- Layer 3+5: Add quality_code to drafts.
-- quality_flag (bool) is retained for backward compatibility; quality_code
-- (str) is the canonical status: "OK" | "SOFT_FLAG" | "HARD_FAIL" | "CRITIC_HOLD".
ALTER TABLE drafts ADD COLUMN quality_code TEXT NOT NULL DEFAULT 'OK';
CREATE INDEX IF NOT EXISTS idx_drafts_quality_code ON drafts(quality_code);
