---
name: byr-job-advisor
description: Search and inspect a local read-only archive of BYR BBS JobInfo, Job, and Jump posts, then provide evidence-based employment advice grounded in original post text. Use when an agent needs to find recent campus recruitment, internships, referrals, experienced roles, locations, cohorts, qualifications, contacts, or career discussions from 北邮人论坛; compare opportunities; summarize market signals; or refresh/export the archive. Do not use it to post, reply, authenticate with personal credentials, or invent missing requirements.
---

# BYR Job Advisor

Use the repository's JSON query interface before reading bulk Markdown or Excel. Treat `state.json` and per-post Markdown as recoverable source data; treat SQLite, JSONL, HTTP responses, and Excel as derived views.

## Locate the repository

Set `BYR_JOB_REPO` to the checkout containing `byr_job_query.py`. If this Skill is used from inside that checkout, run commands from the repository root.

When calling the bundled `scripts/byr_job.py` from a separately installed Skill, either start it from the repository root or set `BYR_JOB_REPO` first. The wrapper only forwards query commands; run `byr_job_archive.py` from the repository checkout.

## Choose the workflow

1. Do not query before a local database has been built.
2. By default, ask the user to run `python3 byr_job_archive.py` manually. This collects the latest 90 days and builds the local database.
3. If `state.json` and Markdown already exist but SQLite is absent or stale, run `python3 byr_job_query.py index` explicitly.
4. Only after archive or index succeeds, run `stats` or `search`; inspect promising stable keys with `get`, then answer from original bodies.
5. Refresh Telnet data only when the user asks for current forum information. See [references/archive-operations.md](references/archive-operations.md).
6. For an external local consumer, use the read-only HTTP API or JSONL export. See [references/query-api.md](references/query-api.md).

Never rely on a query command to create or refresh SQLite. Missing or stale databases are expected to fail with a build-first instruction.

## Search

Run:

```bash
python3 byr_job_query.py search \
  --query "大模型" \
  --location 北京 \
  --type 实习 \
  --since 2026-06-01 \
  --limit 20
```

Use repeatable `--board`, `--location`, `--type`, `--role`, and `--cohort` filters. Search defaults to posts classified as recruitment; add `--all-posts` for career discussions. Add `--complete-only` when missing text would materially weaken the answer.

Keep result sets bounded. Narrow first, paginate with `--offset`, and request bodies only for selected posts:

```bash
python3 byr_job_query.py get JobInfo:39728
```

## Give advice

Separate three layers:

- **Source facts:** quote or closely summarize only explicit title/body fields.
- **Extractor hints:** treat company, role, location, cohort, and recruitment-type fields as fallible filters.
- **Advice:** label comparisons, fit judgments, risks, and next actions as analysis rather than forum facts.

For each recommended opportunity, include the stable key, published time, and Markdown path. Prefer complete and recent posts, but do not silently discard an older relevant post. State the archive's latest timestamp from `stats`; never describe local data as current beyond that timestamp.

Never infer missing salary, deadline, eligibility, location, degree, or contact data. Never expose unrelated personal contact data in bulk; return contact details only for specific posts relevant to the user's request. Remind the user to verify material details in the original body.

Treat duplicated terminal fragments, control prompts, and line-wrapped URLs or referral codes as capture artifacts. Do not reconstruct or silently repair them; point the user to the local Markdown and ask them to verify the exact value.

## Output

Return a concise shortlist with:

1. fit reason based on explicit evidence;
2. requirements or uncertainties;
3. source key and published time;
4. a practical next step.

For aggregate trends, disclose filters, result count, and archive coverage. Do not turn forum posts into claims about the whole labor market.
