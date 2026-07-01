-- 009_applications.sql
-- Application mode (Phase B, #58): a per-job-posting referral front-door
-- alongside the existing company-targeted Campaign mode. `applications` is the
-- posting entity (job_id = the linkage key the consumer polls "referral yet?").
-- `application_contacts` links postings ↔ contacts many-to-many — decision #2 in
-- docs/APPLICATION_FEED_INPUT_DESIGN_2026-06-30.md §6/§11: a contact can serve
-- >1 req at one company, so a join table, NOT a contacts.job_id FK (the FK
-- breaks the first time that happens). Ships DARK in P1 — tables only, no writes
-- yet (P2/#59 wires /network-jobs). Campaign-mode rows leave both tables empty,
-- so this is fully backward compatible.

CREATE TABLE IF NOT EXISTS applications (
    job_id TEXT PRIMARY KEY,
    company TEXT NOT NULL,
    company_slug TEXT,
    role_title TEXT NOT NULL,
    function TEXT,
    job_url TEXT,
    score INTEGER,
    deadline TEXT,
    status TEXT DEFAULT 'NEW',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_applications_company ON applications(company_slug);

-- Many-to-many posting ↔ contact linkage. The (job_id, contact_id) pair is the
-- PK so re-linking a contact to a posting is idempotent (INSERT OR IGNORE in
-- P2). FK targets are named via REFERENCES (matching the existing schema
-- convention — no PRAGMA foreign_keys enforcement is declared repo-wide).
CREATE TABLE IF NOT EXISTS application_contacts (
    job_id TEXT REFERENCES applications(job_id),
    contact_id INTEGER REFERENCES contacts(id),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (job_id, contact_id)
);
CREATE INDEX IF NOT EXISTS idx_appcontacts_contact ON application_contacts(contact_id);
