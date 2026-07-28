#!/usr/bin/env python3
"""Machine-readable CLI and read-only HTTP API for the BYR job archive."""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

from byr_job_store import (
    DATABASE_NAME,
    archive_stats,
    build_database,
    ensure_database,
    export_jsonl,
    get_post,
    search_posts,
)


DEFAULT_ARCHIVE = Path(__file__).with_name("北邮人论坛近一年归档")


def emit(payload: Any, *, pretty: bool = True) -> None:
    json.dump(
        payload,
        sys.stdout,
        ensure_ascii=False,
        indent=2 if pretty else None,
    )
    sys.stdout.write("\n")


def add_search_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("-q", "--query", help="标题、摘要和原帖正文关键词")
    parser.add_argument("--board", action="append", choices=["JobInfo", "Job", "Jump"])
    parser.add_argument("--location", action="append", help="地点，可重复或逗号分隔")
    parser.add_argument("--type", dest="recruitment_types", action="append")
    parser.add_argument("--role", action="append", help="岗位类别，可重复")
    parser.add_argument("--cohort", action="append", help="毕业届别，可重复")
    parser.add_argument("--organization", help="公司或机构名称片段")
    parser.add_argument("--since", help="发布时间下限，如 2026-07-01")
    parser.add_argument("--until", help="发布时间上限（不含），如 2026-08-01")
    parser.add_argument(
        "--all-posts",
        action="store_true",
        help="包含未识别为招聘信息的讨论帖",
    )
    parser.add_argument("--complete-only", action="store_true")
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--include-body", action="store_true")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="检索北邮人论坛就业版面本地归档，默认输出 JSON。"
    )
    parser.add_argument("--archive-dir", type=Path, default=DEFAULT_ARCHIVE)
    parser.add_argument(
        "--database",
        type=Path,
        help=f"SQLite 路径；默认 ARCHIVE_DIR/{DATABASE_NAME}",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    index_parser = subparsers.add_parser("index", help="从 state.json 和 Markdown 重建 SQLite")
    index_parser.add_argument("--state", type=Path)

    search_parser = subparsers.add_parser("search", help="筛选并全文检索帖子")
    add_search_arguments(search_parser)

    get_parser = subparsers.add_parser("get", help="按稳定唯一键读取帖子")
    get_parser.add_argument("key", help="例如 JobInfo:39728")
    get_parser.add_argument("--no-body", action="store_true")

    subparsers.add_parser("stats", help="返回归档统计和可用筛选值")

    export_parser = subparsers.add_parser("export-jsonl", help="导出 Agent 友好的 JSONL")
    export_parser.add_argument("--output", type=Path, required=True)
    export_parser.add_argument("--no-body", action="store_true")

    serve_parser = subparsers.add_parser("serve", help="启动只读 HTTP JSON API")
    serve_parser.add_argument("--host", default="127.0.0.1")
    serve_parser.add_argument("--port", type=int, default=8765)
    return parser.parse_args(argv)


def _database_for(args: argparse.Namespace) -> Path:
    return ensure_database(args.archive_dir, args.database)


def _search_kwargs(args: argparse.Namespace) -> dict[str, Any]:
    return {
        "query": args.query,
        "boards": args.board,
        "locations": args.location,
        "recruitment_types": args.recruitment_types,
        "roles": args.role,
        "cohorts": args.cohort,
        "organization": args.organization,
        "since": args.since,
        "until": args.until,
        "recruitment_only": None if args.all_posts else True,
        "complete_only": args.complete_only,
        "limit": args.limit,
        "offset": args.offset,
        "include_body": args.include_body,
    }


def _single(query: dict[str, list[str]], key: str) -> str | None:
    values = query.get(key)
    return values[-1] if values else None


def _many(query: dict[str, list[str]], key: str) -> list[str] | None:
    values = query.get(key)
    return values or None


def make_handler(database_path: Path) -> type[BaseHTTPRequestHandler]:
    class ApiHandler(BaseHTTPRequestHandler):
        server_version = "ByrJobArchive/1"

        def _write(self, status: HTTPStatus, payload: Any) -> None:
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status.value)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self) -> None:  # noqa: N802 - stdlib handler API
            parsed = urlparse(self.path)
            query = parse_qs(parsed.query)
            try:
                if parsed.path == "/health":
                    self._write(HTTPStatus.OK, {"status": "ok"})
                    return
                if parsed.path == "/v1/stats":
                    self._write(HTTPStatus.OK, archive_stats(database_path))
                    return
                if parsed.path == "/v1/posts":
                    all_posts = _single(query, "all_posts") in {"1", "true", "yes"}
                    payload = search_posts(
                        database_path,
                        query=_single(query, "q"),
                        boards=_many(query, "board"),
                        locations=_many(query, "location"),
                        recruitment_types=_many(query, "type"),
                        roles=_many(query, "role"),
                        cohorts=_many(query, "cohort"),
                        organization=_single(query, "organization"),
                        since=_single(query, "since"),
                        until=_single(query, "until"),
                        recruitment_only=None if all_posts else True,
                        complete_only=_single(query, "complete_only")
                        in {"1", "true", "yes"},
                        limit=int(_single(query, "limit") or 20),
                        offset=int(_single(query, "offset") or 0),
                        include_body=_single(query, "include_body")
                        in {"1", "true", "yes"},
                    )
                    self._write(HTTPStatus.OK, payload)
                    return
                prefix = "/v1/posts/"
                if parsed.path.startswith(prefix):
                    key = unquote(parsed.path[len(prefix) :])
                    post = get_post(database_path, key, include_body=True)
                    if post is None:
                        self._write(
                            HTTPStatus.NOT_FOUND,
                            {"error": "post_not_found", "key": key},
                        )
                    else:
                        self._write(HTTPStatus.OK, post)
                    return
                self._write(
                    HTTPStatus.NOT_FOUND,
                    {
                        "error": "not_found",
                        "endpoints": ["/health", "/v1/stats", "/v1/posts"],
                    },
                )
            except (ValueError, sqlite3.Error) as exc:
                self._write(HTTPStatus.BAD_REQUEST, {"error": str(exc)})

        def log_message(self, format: str, *args: Any) -> None:
            print(f"[http] {self.address_string()} {format % args}", file=sys.stderr)

    return ApiHandler


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    archive_dir = args.archive_dir.expanduser().resolve()
    database_path = (
        args.database.expanduser().resolve()
        if args.database
        else archive_dir / DATABASE_NAME
    )

    if args.command == "index":
        state_path = (
            args.state.expanduser().resolve()
            if args.state
            else archive_dir / "state.json"
        )
        emit(build_database(state_path, database_path))
        return

    database_path = _database_for(args)
    if args.command == "search":
        emit(search_posts(database_path, **_search_kwargs(args)))
    elif args.command == "get":
        post = get_post(database_path, args.key, include_body=not args.no_body)
        if post is None:
            emit({"error": "post_not_found", "key": args.key})
            raise SystemExit(2)
        emit(post)
    elif args.command == "stats":
        emit(archive_stats(database_path))
    elif args.command == "export-jsonl":
        count = export_jsonl(
            database_path,
            args.output.expanduser().resolve(),
            include_body=not args.no_body,
        )
        emit({"output": str(args.output.expanduser().resolve()), "posts": count})
    elif args.command == "serve":
        server = ThreadingHTTPServer(
            (args.host, args.port),
            make_handler(database_path),
        )
        print(
            f"只读 API：http://{args.host}:{server.server_port}/v1/posts",
            file=sys.stderr,
        )
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            server.server_close()


if __name__ == "__main__":
    main()
