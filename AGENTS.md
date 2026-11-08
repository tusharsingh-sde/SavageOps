# SavageOps agent rules
1. Never read or print .env contents. Check variable names only. Mask secrets in all output, logs, reports and commits. Admin JWT variables (SUPABASE_ANON_KEY, ADMIN_EMAIL, ADMIN_PASSWORD) live only in my PowerShell session, never in files.
2. Work on branch antigravity/phase-N. Commit at the end of each task. Never push to main. Never merge PRs.
3. At phase start run: git rev-parse HEAD > reports/.phase-N-start
4. Write a failing test before each bug fix, then show it passing.
5. Never mark anything PASS/DONE/VERIFIED without raw command output. No output = UNVERIFIED.
6. Never estimate tokens, latency or cost. Query agent_runs or mark BLOCKED.
7. If a step needs a missing credential, running service or Docker, mark BLOCKED and name exactly what I must provide. Do not mock live steps. Mocks exist only in tests, never as a runtime fallback.
8. Edit only savageops/, tests/, scripts/, deploy/, docs/, reports/, AGENTS.md and .agents/. Never touch GitHub/Supabase/Grafana settings or the dummy-app repo.
9. Destructive commands (rm, git reset --hard, docker volume prune, force push) need my explicit confirmation.
