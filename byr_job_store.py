#!/usr/bin/env python3
"""Derived SQLite search index for the BYR BBS job archive."""

from __future__ import annotations

import json
import os
import re
import sqlite3
import tempfile
from collections.abc import Iterable
from datetime import datetime
from pathlib import Path
from typing import Any


DATABASE_NAME = "byr_jobs.sqlite3"
DATABASE_SCHEMA_VERSION = 1
FACET_FIELDS = {
    "recruitment_type": "recruitment_type",
    "role_category": "role_category",
    "organization": "organization",
    "location": "locations",
    "cohort": "cohorts",
    "education": "education",
}
POST_COLUMNS = (
    "key",
    "board",
    "board_name",
    "article_number",
    "title",
    "author",
    "nickname",
    "published_at",
    "capture_complete",
    "content_hash",
    "is_recruitment",
    "recruitment_type",
    "role_category",
    "organization",
    "locations",
    "cohorts",
    "education",
    "internship_requirement",
    "experience_requirement",
    "emails",
    "contacts",
    "application_urls",
    "summary",
    "character_count",
    "markdown_path",
    "first_archived_at",
    "last_checked_at",
    "body",
)
PUBLIC_COLUMNS = tuple(column for column in POST_COLUMNS if column != "body")


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def load_state(state_path: Path) -> dict[str, Any]:
    with state_path.open("r", encoding="utf-8") as handle:
        state = json.load(handle)
    if not isinstance(state.get("posts"), dict):
        raise ValueError(f"{state_path} 缺少 posts 对象")
    return state


def _safe_markdown_path(archive_dir: Path, relative_path: str) -> Path:
    root = archive_dir.resolve()
    path = (root / relative_path).resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"Markdown 路径越界：{relative_path}") from exc
    return path


def read_post_body(archive_dir: Path, post: dict[str, Any]) -> str:
    relative_path = str(post.get("markdown_path") or "")
    if not relative_path:
        return ""
    path = _safe_markdown_path(archive_dir, relative_path)
    try:
        markdown = path.read_text(encoding="utf-8")
    except OSError:
        return ""
    marker = "\n## 原帖正文\n"
    return markdown.split(marker, 1)[1].strip() if marker in markdown else markdown


def split_values(value: Any) -> list[str]:
    if not value:
        return []
    # The extractor joins values with " / ", while labels such as
    # "销售/市场" and "研究/职能" contain an intentional slash.
    parts = re.split(r"\s+/\s+|[；;]\s*", str(value))
    return list(dict.fromkeys(part.strip() for part in parts if part.strip()))


def _create_schema(connection: sqlite3.Connection) -> str:
    connection.executescript(
        """
        PRAGMA journal_mode = DELETE;
        PRAGMA synchronous = FULL;
        CREATE TABLE metadata (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
        CREATE TABLE posts (
            key TEXT PRIMARY KEY,
            board TEXT NOT NULL,
            board_name TEXT NOT NULL,
            article_number INTEGER NOT NULL,
            title TEXT NOT NULL,
            author TEXT NOT NULL,
            nickname TEXT NOT NULL,
            published_at TEXT NOT NULL,
            capture_complete INTEGER NOT NULL,
            content_hash TEXT NOT NULL,
            is_recruitment INTEGER NOT NULL,
            recruitment_type TEXT NOT NULL,
            role_category TEXT NOT NULL,
            organization TEXT NOT NULL,
            locations TEXT NOT NULL,
            cohorts TEXT NOT NULL,
            education TEXT NOT NULL,
            internship_requirement TEXT NOT NULL,
            experience_requirement TEXT NOT NULL,
            emails TEXT NOT NULL,
            contacts TEXT NOT NULL,
            application_urls TEXT NOT NULL,
            summary TEXT NOT NULL,
            character_count INTEGER NOT NULL,
            markdown_path TEXT NOT NULL,
            first_archived_at TEXT NOT NULL,
            last_checked_at TEXT NOT NULL,
            body TEXT NOT NULL,
            UNIQUE(board, article_number)
        );
        CREATE INDEX posts_published_at ON posts(published_at DESC);
        CREATE INDEX posts_board_number ON posts(board, article_number DESC);
        CREATE INDEX posts_recruitment_date
            ON posts(is_recruitment, published_at DESC);
        CREATE TABLE post_facets (
            post_key TEXT NOT NULL REFERENCES posts(key) ON DELETE CASCADE,
            facet TEXT NOT NULL,
            value TEXT NOT NULL,
            PRIMARY KEY (post_key, facet, value)
        );
        CREATE INDEX post_facets_lookup
            ON post_facets(facet, value, post_key);
        """
    )
    try:
        connection.execute(
            """
            CREATE VIRTUAL TABLE posts_fts USING fts5(
                post_key UNINDEXED,
                title,
                summary,
                body,
                tokenize='trigram'
            )
            """
        )
        return "trigram"
    except sqlite3.OperationalError:
        connection.execute(
            """
            CREATE VIRTUAL TABLE posts_fts USING fts5(
                post_key UNINDEXED,
                title,
                summary,
                body,
                tokenize='unicode61'
            )
            """
        )
        return "unicode61"


def _post_row(
    key: str,
    post: dict[str, Any],
    archive_dir: Path,
) -> tuple[Any, ...]:
    normalized = {
        "key": key,
        "board": str(post.get("board") or key.partition(":")[0]),
        "board_name": str(post.get("board_name") or ""),
        "article_number": int(post.get("article_number") or key.partition(":")[2]),
        "title": str(post.get("title") or ""),
        "author": str(post.get("author") or ""),
        "nickname": str(post.get("nickname") or ""),
        "published_at": str(post.get("published_at") or ""),
        "capture_complete": int(bool(post.get("capture_complete"))),
        "content_hash": str(post.get("content_hash") or ""),
        "is_recruitment": int(bool(post.get("is_recruitment"))),
        "recruitment_type": str(post.get("recruitment_type") or ""),
        "role_category": str(post.get("role_category") or ""),
        "organization": str(post.get("organization") or ""),
        "locations": str(post.get("locations") or ""),
        "cohorts": str(post.get("cohorts") or ""),
        "education": str(post.get("education") or ""),
        "internship_requirement": str(post.get("internship_requirement") or ""),
        "experience_requirement": str(post.get("experience_requirement") or ""),
        "emails": str(post.get("emails") or ""),
        "contacts": str(post.get("contacts") or ""),
        "application_urls": str(post.get("application_urls") or ""),
        "summary": str(post.get("summary") or ""),
        "character_count": int(post.get("character_count") or 0),
        "markdown_path": str(post.get("markdown_path") or ""),
        "first_archived_at": str(post.get("first_archived_at") or ""),
        "last_checked_at": str(post.get("last_checked_at") or ""),
        "body": read_post_body(archive_dir, post),
    }
    return tuple(normalized[column] for column in POST_COLUMNS)


def build_database(
    state_path: Path,
    database_path: Path | None = None,
) -> dict[str, Any]:
    """Atomically rebuild the derived database from state.json and Markdown."""
    state_path = state_path.expanduser().resolve()
    archive_dir = state_path.parent
    database_path = (
        database_path.expanduser().resolve()
        if database_path
        else archive_dir / DATABASE_NAME
    )
    database_path.parent.mkdir(parents=True, exist_ok=True)
    state = load_state(state_path)

    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{database_path.name}.",
        suffix=".tmp",
        dir=database_path.parent,
    )
    os.close(descriptor)
    temporary_path = Path(temporary_name)
    post_count = 0
    missing_markdown = 0
    try:
        connection = sqlite3.connect(temporary_path)
        try:
            connection.execute("PRAGMA foreign_keys = ON")
            tokenizer = _create_schema(connection)
            insert_sql = (
                f"INSERT INTO posts ({', '.join(POST_COLUMNS)}) "
                f"VALUES ({', '.join('?' for _ in POST_COLUMNS)})"
            )
            for key, post in sorted(state["posts"].items()):
                row = _post_row(key, post, archive_dir)
                connection.execute(insert_sql, row)
                body = row[-1]
                if post.get("markdown_path") and not body:
                    missing_markdown += 1
                connection.execute(
                    """
                    INSERT INTO posts_fts(post_key, title, summary, body)
                    VALUES (?, ?, ?, ?)
                    """,
                    (key, row[4], row[22], body),
                )
                facets: list[tuple[str, str, str]] = []
                for facet, field in FACET_FIELDS.items():
                    facets.extend(
                        (key, facet, value)
                        for value in split_values(post.get(field))
                    )
                connection.executemany(
                    """
                    INSERT OR IGNORE INTO post_facets(post_key, facet, value)
                    VALUES (?, ?, ?)
                    """,
                    facets,
                )
                post_count += 1

            metadata = {
                "schema_version": str(DATABASE_SCHEMA_VERSION),
                "built_at": now_iso(),
                "source_state": str(state_path),
                "source_schema_version": str(state.get("schema_version", "")),
                "source_updated_at": str(state.get("updated_at", "")),
                "post_count": str(post_count),
                "missing_markdown_count": str(missing_markdown),
                "fts_tokenizer": tokenizer,
            }
            connection.executemany(
                "INSERT INTO metadata(key, value) VALUES (?, ?)",
                metadata.items(),
            )
            connection.commit()
            integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
            if integrity != "ok":
                raise RuntimeError(f"SQLite 完整性检查失败：{integrity}")
        finally:
            connection.close()
        temporary_path.replace(database_path)
    except BaseException:
        temporary_path.unlink(missing_ok=True)
        raise

    return {
        "database": str(database_path),
        "posts": post_count,
        "missing_markdown": missing_markdown,
        "source_updated_at": state.get("updated_at"),
    }


def database_metadata(database_path: Path) -> dict[str, str]:
    with sqlite3.connect(database_path) as connection:
        return dict(connection.execute("SELECT key, value FROM metadata"))


def require_database(
    archive_dir: Path,
    database_path: Path | None = None,
) -> Path:
    archive_dir = archive_dir.expanduser().resolve()
    state_path = archive_dir / "state.json"
    database_path = (
        database_path.expanduser().resolve()
        if database_path
        else archive_dir / DATABASE_NAME
    )
    if not state_path.exists():
        raise RuntimeError(
            "未找到归档状态 state.json。请先运行 `python byr_job_archive.py` 获取归档。"
        )
    if not database_path.exists():
        raise RuntimeError(
            "尚未构建查询数据库。请先运行 `python byr_job_archive.py` 获取归档，"
            "或在已有 state.json 和 Markdown 时运行 `python byr_job_query.py index`。"
        )
    try:
        metadata = database_metadata(database_path)
        state = load_state(state_path)
    except (OSError, sqlite3.Error, ValueError, json.JSONDecodeError) as exc:
        raise RuntimeError(
            "无法验证查询数据库。请先运行 `python byr_job_query.py index` 重新构建。"
        ) from exc
    if (
        metadata.get("schema_version") != str(DATABASE_SCHEMA_VERSION)
        or metadata.get("source_updated_at") != str(state.get("updated_at", ""))
    ):
        raise RuntimeError(
            "查询数据库尚未构建或已经过期。请先运行 "
            "`python byr_job_query.py index`，再执行检索。"
        )
    return database_path


def _jsonable_row(row: sqlite3.Row, include_body: bool) -> dict[str, Any]:
    columns = POST_COLUMNS if include_body else PUBLIC_COLUMNS
    result = {column: row[column] for column in columns}
    result["capture_complete"] = bool(result["capture_complete"])
    result["is_recruitment"] = bool(result["is_recruitment"])
    result["source_id"] = result["key"]
    result["source_uri"] = (
        f"telnet://bbs.byr.cn/{result['board']}/{result['article_number']}"
    )
    return result


def _normalize_many(values: Iterable[str] | None) -> list[str]:
    if not values:
        return []
    output: list[str] = []
    for value in values:
        output.extend(part.strip() for part in value.split(",") if part.strip())
    return list(dict.fromkeys(output))


def search_posts(
    database_path: Path,
    *,
    query: str | None = None,
    boards: Iterable[str] | None = None,
    locations: Iterable[str] | None = None,
    recruitment_types: Iterable[str] | None = None,
    roles: Iterable[str] | None = None,
    cohorts: Iterable[str] | None = None,
    organization: str | None = None,
    since: str | None = None,
    until: str | None = None,
    recruitment_only: bool | None = True,
    complete_only: bool = False,
    limit: int = 20,
    offset: int = 0,
    include_body: bool = False,
) -> dict[str, Any]:
    limit = max(1, min(int(limit), 200))
    offset = max(0, int(offset))
    clauses: list[str] = []
    parameters: list[Any] = []
    joins = ""

    with sqlite3.connect(database_path) as connection:
        connection.row_factory = sqlite3.Row
        metadata = dict(connection.execute("SELECT key, value FROM metadata"))
        clean_query = (query or "").strip()
        if clean_query:
            tokenizer = metadata.get("fts_tokenizer")
            if tokenizer == "trigram" and len(re.sub(r"\s+", "", clean_query)) >= 3:
                joins = "JOIN posts_fts ON posts_fts.post_key = p.key"
                phrase = '"' + clean_query.replace('"', '""') + '"'
                clauses.append("posts_fts MATCH ?")
                parameters.append(phrase)
            else:
                clauses.append("(p.title LIKE ? OR p.summary LIKE ? OR p.body LIKE ?)")
                like = f"%{clean_query}%"
                parameters.extend((like, like, like))

        normalized_boards = _normalize_many(boards)
        if normalized_boards:
            placeholders = ", ".join("?" for _ in normalized_boards)
            clauses.append(f"p.board IN ({placeholders})")
            parameters.extend(normalized_boards)

        facet_filters = {
            "location": _normalize_many(locations),
            "recruitment_type": _normalize_many(recruitment_types),
            "role_category": _normalize_many(roles),
            "cohort": _normalize_many(cohorts),
        }
        for facet, values in facet_filters.items():
            if not values:
                continue
            placeholders = ", ".join("?" for _ in values)
            clauses.append(
                "EXISTS (SELECT 1 FROM post_facets pf "
                "WHERE pf.post_key = p.key AND pf.facet = ? "
                f"AND pf.value IN ({placeholders}))"
            )
            parameters.extend((facet, *values))

        if organization:
            clauses.append("p.organization LIKE ?")
            parameters.append(f"%{organization.strip()}%")
        if since:
            clauses.append("p.published_at >= ?")
            parameters.append(since)
        if until:
            clauses.append("p.published_at < ?")
            parameters.append(until)
        if recruitment_only is not None:
            clauses.append("p.is_recruitment = ?")
            parameters.append(int(recruitment_only))
        if complete_only:
            clauses.append("p.capture_complete = 1")

        where = " AND ".join(clauses) if clauses else "1 = 1"
        total = int(
            connection.execute(
                f"SELECT COUNT(*) FROM posts p {joins} WHERE {where}",
                parameters,
            ).fetchone()[0]
        )
        selected = ", ".join(f"p.{column}" for column in POST_COLUMNS)
        sql = f"""
            SELECT {selected}
            FROM posts p
            {joins}
            WHERE {where}
            ORDER BY p.published_at DESC, p.article_number DESC
            LIMIT ? OFFSET ?
        """
        rows = connection.execute(sql, (*parameters, limit, offset)).fetchall()
        return {
            "query": {
                "text": clean_query or None,
                "boards": normalized_boards,
                "locations": facet_filters["location"],
                "recruitment_types": facet_filters["recruitment_type"],
                "roles": facet_filters["role_category"],
                "cohorts": facet_filters["cohort"],
                "organization": organization,
                "since": since,
                "until": until,
                "recruitment_only": recruitment_only,
                "complete_only": complete_only,
                "limit": limit,
                "offset": offset,
            },
            "total": total,
            "count": len(rows),
            "items": [_jsonable_row(row, include_body) for row in rows],
        }


def get_post(
    database_path: Path,
    key: str,
    *,
    include_body: bool = True,
) -> dict[str, Any] | None:
    with sqlite3.connect(database_path) as connection:
        connection.row_factory = sqlite3.Row
        row = connection.execute(
            "SELECT * FROM posts WHERE key = ?",
            (key,),
        ).fetchone()
        return _jsonable_row(row, include_body) if row else None


def archive_stats(database_path: Path) -> dict[str, Any]:
    with sqlite3.connect(database_path) as connection:
        connection.row_factory = sqlite3.Row
        totals = connection.execute(
            """
            SELECT
                COUNT(*) AS posts,
                SUM(is_recruitment) AS recruitment_posts,
                SUM(capture_complete) AS complete_posts,
                MIN(published_at) AS earliest_published_at,
                MAX(published_at) AS latest_published_at
            FROM posts
            """
        ).fetchone()
        boards = [
            dict(row)
            for row in connection.execute(
                """
                SELECT board, board_name, COUNT(*) AS posts,
                       SUM(is_recruitment) AS recruitment_posts
                FROM posts GROUP BY board, board_name ORDER BY board
                """
            )
        ]
        facets: dict[str, list[dict[str, Any]]] = {}
        for facet in ("location", "recruitment_type", "role_category", "cohort"):
            facets[facet] = [
                dict(row)
                for row in connection.execute(
                    """
                    SELECT value, COUNT(*) AS posts
                    FROM post_facets
                    WHERE facet = ?
                    GROUP BY value
                    ORDER BY posts DESC, value
                    LIMIT 50
                    """,
                    (facet,),
                )
            ]
        metadata = dict(connection.execute("SELECT key, value FROM metadata"))
        return {
            "totals": dict(totals),
            "boards": boards,
            "facets": facets,
            "metadata": metadata,
        }


def export_jsonl(
    database_path: Path,
    output_path: Path,
    *,
    include_body: bool = True,
) -> int:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with sqlite3.connect(database_path) as connection:
        connection.row_factory = sqlite3.Row
        temporary = output_path.with_suffix(output_path.suffix + ".tmp")
        with temporary.open("w", encoding="utf-8") as handle:
            for row in connection.execute(
                "SELECT * FROM posts ORDER BY published_at DESC, article_number DESC"
            ):
                record = _jsonable_row(row, include_body)
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")
                count += 1
        temporary.replace(output_path)
    return count
