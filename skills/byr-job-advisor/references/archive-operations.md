# Archive operations

Refresh only when the user asks for newer forum data. The collector is read-only and uses the official `guest` Telnet entry.

```bash
python3 byr_job_archive.py --skip-excel --proxy 127.0.0.1:7890
```

Use direct access only when TCP port 23 is reachable. For a low-risk smoke test:

```bash
python3 byr_job_archive.py \
  --boards JobInfo \
  --max-posts 3 \
  --skip-excel \
  --proxy 127.0.0.1:7890
```

Do not request or reuse forum passwords, browser cookies, or personal credentials. Do not reset `state.json`, delete existing Markdown after a remote failure, or mark incomplete reads as complete.

Rebuild derived outputs without network access:

```bash
python3 byr_job_query.py index
python3 byr_job_archive.py --rebuild-excel
```

Use Excel rebuild only when the Codex workspace `@oai/artifact-tool` runtime is available. SQLite/JSON access does not depend on Node.js.

The archive uniquely identifies a post as `board:Telnet article number`. Refresh failures do not invalidate already archived posts. Report the latest successfully archived timestamp rather than claiming live coverage.
