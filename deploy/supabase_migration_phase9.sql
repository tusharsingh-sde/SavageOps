-- SavageOps Phase 9 Migration
-- Run this in your Supabase Dashboard SQL Editor

ALTER TABLE incidents ADD COLUMN IF NOT EXISTS group_key text;
ALTER TABLE incidents ADD COLUMN IF NOT EXISTS occurrences int DEFAULT 1;
ALTER TABLE incidents ADD COLUMN IF NOT EXISTS source text;          -- webhook | poller
ALTER TABLE incidents ADD COLUMN IF NOT EXISTS last_seen_at timestamptz DEFAULT now();
