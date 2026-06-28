-- 005_contacts_dedup.sql
-- FINDER_AUDIT D5 (#27): re-running the Finder/importer inserted duplicate
-- contact rows. The idempotency DELETE only clears state='NEW', so a contact
-- already SELECTED/DRAFTED survived and was inserted again on the next run.
-- A partial unique index makes (company_id, linkedin_url) unique for real URLs,
-- so the INSERT OR IGNORE in ingest_contacts skips the re-insert. Contacts with
-- a NULL linkedin_url are unconstrained (no key to dedup on).

CREATE UNIQUE INDEX IF NOT EXISTS idx_contacts_company_linkedin
    ON contacts (company_id, linkedin_url)
    WHERE linkedin_url IS NOT NULL;
