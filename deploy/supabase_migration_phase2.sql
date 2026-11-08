-- SavageOps Phase 2 Migration: LLM Telemetry and Usage Stats View
-- Run this in your Supabase Dashboard SQL Editor

-- 1. Add LLM call telemetry columns to agent_runs
ALTER TABLE agent_runs ADD COLUMN IF NOT EXISTS provider text;
ALTER TABLE agent_runs ADD COLUMN IF NOT EXISTS model text;
ALTER TABLE agent_runs ADD COLUMN IF NOT EXISTS role text;
ALTER TABLE agent_runs ADD COLUMN IF NOT EXISTS input_tokens int DEFAULT 0;
ALTER TABLE agent_runs ADD COLUMN IF NOT EXISTS output_tokens int DEFAULT 0;
ALTER TABLE agent_runs ADD COLUMN IF NOT EXISTS latency float DEFAULT 0.0;
ALTER TABLE agent_runs ADD COLUMN IF NOT EXISTS fallback_depth int DEFAULT 0;

-- 2. Create model_usage_stats view over agent_runs grouped by role, provider, model
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
