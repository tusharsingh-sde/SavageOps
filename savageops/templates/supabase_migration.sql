
-- Run this in your Supabase Dashboard SQL Editor

ALTER TABLE incidents ADD COLUMN IF NOT EXISTS group_key text;
ALTER TABLE incidents ADD COLUMN IF NOT EXISTS occurrences int DEFAULT 1;
ALTER TABLE incidents ADD COLUMN IF NOT EXISTS source text;          -- webhook | poller
ALTER TABLE incidents ADD COLUMN IF NOT EXISTS last_seen_at timestamptz DEFAULT now();
ALTER TABLE incidents ADD COLUMN IF NOT EXISTS alerted_at timestamptz;

-- Keep a single active incident per service/alert group, including incidents waiting
-- for either human gate. Resolve any existing duplicate groups before applying this index.
CREATE UNIQUE INDEX IF NOT EXISTS incidents_one_open_group_idx
  ON incidents (group_key)
  WHERE group_key IS NOT NULL
    AND status IN ('received', 'triaged', 'fixing', 'awaiting_user', 'awaiting_push', 'pushing', 'needs_human', 'failed', 'pr_open');

-- LLM Telemetry columns
ALTER TABLE agent_runs ADD COLUMN IF NOT EXISTS provider text;
ALTER TABLE agent_runs ADD COLUMN IF NOT EXISTS model text;
ALTER TABLE agent_runs ADD COLUMN IF NOT EXISTS role text;
ALTER TABLE agent_runs ADD COLUMN IF NOT EXISTS input_tokens int DEFAULT 0;
ALTER TABLE agent_runs ADD COLUMN IF NOT EXISTS output_tokens int DEFAULT 0;
ALTER TABLE agent_runs ADD COLUMN IF NOT EXISTS latency float DEFAULT 0.0;
ALTER TABLE agent_runs ADD COLUMN IF NOT EXISTS fallback_depth int DEFAULT 0;

-- Model usage statistics view
CREATE OR REPLACE VIEW model_usage_stats AS
SELECT
    role,
    provider,
    model,
    COUNT(*) AS calls,
    AVG(latency) AS avg_latency,
    SUM(input_tokens) AS total_input_tokens,
    SUM(output_tokens) AS total_output_tokens,
    AVG(fallback_depth) AS avg_fallback_depth,
    COUNT(*) FILTER (WHERE error IS NOT NULL OR kind = 'error') AS failures
FROM agent_runs
WHERE provider IS NOT NULL
GROUP BY role, provider, model;
