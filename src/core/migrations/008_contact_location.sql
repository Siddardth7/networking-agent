-- 008_contact_location.sql
-- Per-contact location (#18, A7 — timing intelligence). The finder/providers
-- already extract a location for each ContactCandidate (the contact's own
-- LinkedIn location via Apify, the campaign/site context via Serper) but it was
-- dropped before the contact row was written. Persist it so the timing
-- recommender can map location → local timezone and suggest a Tue-Thu morning
-- send window. NULL reads as "unknown" → the recommender falls back to UTC.

ALTER TABLE contacts ADD COLUMN location TEXT;
