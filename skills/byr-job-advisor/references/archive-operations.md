# Archive operations

The normal first step is for the user to run the collector manually. It is read-only, uses the official `guest` Telnet entry, collects the latest 90 days by default, and builds SQLite after archiving.

```bash
python3 byr_job_archive.py
```

For a low-risk smoke test:

```bash
python3 byr_job_archive.py \
  --boards JobInfo \
  --max-posts 3 \
  --skip-excel
```

Do not request or reuse forum passwords, browser cookies, or personal credentials. Do not reset `state.json`, delete existing Markdown after a remote failure, or mark incomplete reads as complete.

Rebuild derived outputs without network access:

```bash
python3 byr_job_query.py index
python3 byr_job_archive.py --rebuild-excel
```

Run `index` before every query when SQLite is missing or stale. Query commands never build the database automatically.

Use Excel rebuild only when the Codex workspace `@oai/artifact-tool` runtime is available. SQLite/JSON access does not depend on Node.js.

The archive uniquely identifies a post as `board:Telnet article number`. Refresh failures do not invalidate already archived posts. Report the latest successfully archived timestamp rather than claiming live coverage.
