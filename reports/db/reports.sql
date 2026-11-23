CREATE TABLE IF NOT EXISTS report_jobs (
    job_id UUID PRIMARY KEY,
    report_type VARCHAR(50) NOT NULL,
    format VARCHAR(10) NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'PENDING',
    s3_key TEXT,
    error_message TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS report_outbox (
    job_id UUID PRIMARY KEY
        REFERENCES report_jobs(job_id),
    published_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_report_outbox_pending
ON report_outbox(created_at)
WHERE published_at IS NULL;

