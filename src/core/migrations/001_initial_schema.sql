-- companies: target list, one row per company
CREATE TABLE IF NOT EXISTS companies (
    id INTEGER PRIMARY KEY,
    slug TEXT UNIQUE NOT NULL,
    name TEXT NOT NULL,
    domain TEXT,
    target_role TEXT,
    rationale TEXT,
    state TEXT DEFAULT 'NEW',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_companies_slug ON companies(slug);

-- contacts: discovered contacts per company
CREATE TABLE IF NOT EXISTS contacts (
    id INTEGER PRIMARY KEY,
    company_id INTEGER REFERENCES companies(id),
    full_name TEXT NOT NULL,
    title TEXT,
    persona TEXT,
    focus_area TEXT,
    linkedin_url TEXT,
    email TEXT,
    email_verified BOOLEAN DEFAULT 0,
    source_provider TEXT,
    selected BOOLEAN DEFAULT 0,
    hook TEXT,
    shared_signals TEXT,
    state TEXT DEFAULT 'NEW',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_contacts_company ON contacts(company_id, state);

-- drafts: generated drafts per contact per channel
CREATE TABLE IF NOT EXISTS drafts (
    id INTEGER PRIMARY KEY,
    contact_id INTEGER REFERENCES contacts(id),
    channel TEXT NOT NULL,
    body TEXT,
    subject TEXT,
    version INTEGER DEFAULT 1,
    quality_flag BOOLEAN DEFAULT 0,
    approved BOOLEAN DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_drafts_contact ON drafts(contact_id, channel);

-- outreach_log: sent outreach entries
CREATE TABLE IF NOT EXISTS outreach_log (
    id INTEGER PRIMARY KEY,
    contact_id INTEGER REFERENCES contacts(id),
    draft_id INTEGER REFERENCES drafts(id),
    channel TEXT NOT NULL,
    sent_at TIMESTAMP,
    response TEXT DEFAULT 'PENDING',
    notes TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- quota: provider usage tracking
CREATE TABLE IF NOT EXISTS quota (
    id INTEGER PRIMARY KEY,
    provider TEXT NOT NULL,
    month_key TEXT NOT NULL,
    used INTEGER DEFAULT 0,
    limit_val INTEGER NOT NULL,
    UNIQUE(provider, month_key)
);

-- followups: v0.2 only -- table created but not used in v0.1.0
CREATE TABLE IF NOT EXISTS followups (
    id INTEGER PRIMARY KEY,
    outreach_log_id INTEGER REFERENCES outreach_log(id),
    scheduled_at TIMESTAMP,
    sent_at TIMESTAMP,
    body TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
