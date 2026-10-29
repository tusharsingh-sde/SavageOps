
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
