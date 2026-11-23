
ALTER TABLE report_jobs
ADD COLUMN IF NOT EXISTS attempt_count INTEGER NOT NULL DEFAULT 0,
ADD COLUMN IF NOT EXISTS last_error_at TIMESTAMPTZ,
ADD COLUMN IF NOT EXISTS processing_token UUID,
ADD COLUMN IF NOT EXISTS lease_until TIMESTAMPTZ;

CREATE INDEX IF NOT EXISTS idx_report_jobs_processing
ON report_jobs(status, lease_until);

CREATE INDEX IF NOT EXISTS idx_report_jobs_errors
ON report_jobs(last_error_at);

