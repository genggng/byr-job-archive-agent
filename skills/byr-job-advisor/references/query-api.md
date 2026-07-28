# Query interface

## CLI

All commands return UTF-8 JSON.

```bash
python3 byr_job_query.py stats
python3 byr_job_query.py search --query "产品经理" --location 上海 --limit 10
python3 byr_job_query.py get JobInfo:39728
python3 byr_job_query.py export-jsonl --output /tmp/byr-jobs.jsonl
python3 byr_job_query.py index
```

`search` returns `total`, page `count`, normalized query parameters, and `items`. Each item contains `source_id`, `source_uri`, `markdown_path`, `capture_complete`, and `content_hash`. Add `--include-body` only for a small result page.

## HTTP

Start the localhost-only default:

```bash
python3 byr_job_query.py serve --host 127.0.0.1 --port 8765
```

Read-only endpoints:

- `GET /health`
- `GET /v1/stats`
- `GET /v1/posts?q=算法&location=北京&limit=20`
- `GET /v1/posts/JobInfo%3A39728`

Repeat query parameters for multi-value filters. Supported post-list parameters are `q`, `board`, `location`, `type`, `role`, `cohort`, `organization`, `since`, `until`, `all_posts`, `complete_only`, `include_body`, `limit`, and `offset`.

The server has no authentication. Bind to loopback unless the user explicitly supplies a protected deployment design.

## SQLite

`byr_jobs.sqlite3` is a disposable derived database.

- `posts`: structured fields plus original body.
- `post_facets`: normalized multi-value filters.
- `posts_fts`: FTS5 index, using the trigram tokenizer when available.
- `metadata`: source version, build time, coverage, and tokenizer.

Do not update SQLite directly. Rebuild it from `state.json` and Markdown.
