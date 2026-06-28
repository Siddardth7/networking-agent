-- 007_contact_outcome.sql
-- Per-contact outreach outcomes (#15, A6): the feedback signal — who replied,
-- yielded a point of contact, or gave a sponsorship answer. Stored on the
-- contact (latest outcome reached in the funnel) so it's directly queryable;
-- the values are the src.core.schemas.Outcome enum. outcome defaults 'NONE' so
-- pre-#15 rows read as "nothing recorded yet".

ALTER TABLE contacts ADD COLUMN outcome TEXT DEFAULT 'NONE';
ALTER TABLE contacts ADD COLUMN outcome_notes TEXT;
ALTER TABLE contacts ADD COLUMN outcome_at TIMESTAMP;
