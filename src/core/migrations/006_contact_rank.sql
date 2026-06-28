-- 006_contact_rank.sql
-- Referral-likelihood ranking (#11). The Finder/importer score each contact at
-- ingest (src/agents/ranker.py) from deterministic signals on the
-- ContactCandidate, then persist the score + a human-readable reason string so
-- the selection gate can order by likelihood and show WHY without recomputing.
-- rank_score defaults 0 (pre-#11 rows rank last, stable by id).

ALTER TABLE contacts ADD COLUMN rank_score INTEGER DEFAULT 0;
ALTER TABLE contacts ADD COLUMN rank_reasons TEXT;
