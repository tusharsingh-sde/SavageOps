Generate reports/phase-<N>-report.md for the phase just worked on, using git (git diff --stat <start-sha>..HEAD, git log) and real command output only.
Sections:
1. Goal and exit criteria (copied from the phase spec)
2. Changed: table | Area/file | Before | After | Why |
3. Evidence: each exit criterion -> command -> trimmed raw output -> PASS / FAIL / BLOCKED / UNVERIFIED
4. Failed or not achieved: table | Item | Status | Root cause | Impact | Next step |
5. Deviations from the plan and decisions taken
6. New risks introduced
7. Git: branch, commit SHAs, ruff and pytest summary lines
8. Gate: PROCEED or STOP + one-line reason
Also append one row to reports/SUMMARY.md. Never claim anything done that section 3 does not prove.
