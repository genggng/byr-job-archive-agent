from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from byr_job_store import (
    archive_stats,
    build_database,
    export_jsonl,
    get_post,
    require_database,
    search_posts,
    split_values,
)


class StoreTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.archive = Path(self.temporary.name)
        board_dir = self.archive / "JobInfo-招聘信息专版"
        board_dir.mkdir()
        posts = {}
        samples = [
            (
                101,
                "字节跳动算法实习内推",
                "北京团队招聘大模型算法实习生，每周至少四天。",
                "北京",
                "算法",
                True,
            ),
            (
                102,
                "求助：两个 offer 如何选择",
                "想讨论北京和上海两个岗位。",
                "北京 / 上海",
                "",
                False,
            ),
        ]
        for number, title, body, locations, role, is_recruitment in samples:
            relative = f"JobInfo-招聘信息专版/{number}.md"
            (self.archive / relative).write_text(
                f"# {title}\n\n## 原帖正文\n\n{body}\n",
                encoding="utf-8",
            )
            key = f"JobInfo:{number}"
            posts[key] = {
                "key": key,
                "board": "JobInfo",
                "board_name": "招聘信息专版",
                "article_number": number,
                "title": title,
                "author": "tester",
                "nickname": "",
                "published_at": f"2026-07-{number - 90:02d}T12:00:00+08:00",
                "capture_complete": True,
                "content_hash": str(number),
                "is_recruitment": is_recruitment,
                "recruitment_type": "实习 / 内推" if is_recruitment else "",
                "role_category": role,
                "organization": "字节跳动" if is_recruitment else "",
                "locations": locations,
                "cohorts": "27届" if is_recruitment else "",
                "education": "本科及以上" if is_recruitment else "",
                "internship_requirement": "",
                "experience_requirement": "",
                "emails": "",
                "contacts": "",
                "application_urls": "",
                "summary": body,
                "character_count": len(body),
                "markdown_path": relative,
                "first_archived_at": "2026-07-20T12:00:00+08:00",
                "last_checked_at": "2026-07-20T12:00:00+08:00",
            }
        self.state = self.archive / "state.json"
        self.state.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "updated_at": "2026-07-20T12:00:00+08:00",
                    "boards": {},
                    "posts": posts,
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        self.database = self.archive / "byr_jobs.sqlite3"
        build_database(self.state, self.database)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_search_defaults_to_recruitment_and_reads_body(self) -> None:
        result = search_posts(self.database, query="大模型", include_body=True)
        self.assertEqual(result["total"], 1)
        self.assertEqual(result["items"][0]["key"], "JobInfo:101")
        self.assertIn("每周至少四天", result["items"][0]["body"])

    def test_filters_use_normalized_facets(self) -> None:
        result = search_posts(
            self.database,
            locations=["北京"],
            roles=["算法"],
            recruitment_types=["内推"],
        )
        self.assertEqual([item["key"] for item in result["items"]], ["JobInfo:101"])

    def test_all_posts_and_get_stable_key(self) -> None:
        result = search_posts(self.database, recruitment_only=None)
        self.assertEqual(result["total"], 2)
        post = get_post(self.database, "JobInfo:102")
        self.assertFalse(post["is_recruitment"])
        self.assertEqual(post["source_id"], "JobInfo:102")

    def test_total_is_preserved_past_last_page(self) -> None:
        result = search_posts(
            self.database,
            recruitment_only=None,
            offset=20,
        )
        self.assertEqual(result["total"], 2)
        self.assertEqual(result["items"], [])

    def test_stats_and_jsonl_export(self) -> None:
        stats = archive_stats(self.database)
        self.assertEqual(stats["totals"]["posts"], 2)
        self.assertEqual(stats["totals"]["recruitment_posts"], 1)
        output = self.archive / "posts.jsonl"
        self.assertEqual(export_jsonl(self.database, output), 2)
        lines = output.read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(lines), 2)
        self.assertIn("body", json.loads(lines[0]))

    def test_intrinsic_slash_in_role_label_is_preserved(self) -> None:
        self.assertEqual(
            split_values("研发 / 销售/市场 / 研究/职能"),
            ["研发", "销售/市场", "研究/职能"],
        )

    def test_query_requires_database_to_be_built_first(self) -> None:
        self.database.unlink()
        with self.assertRaisesRegex(RuntimeError, "先运行"):
            require_database(self.archive)

    def test_query_rejects_stale_database(self) -> None:
        state = json.loads(self.state.read_text(encoding="utf-8"))
        state["updated_at"] = "2026-07-21T12:00:00+08:00"
        self.state.write_text(
            json.dumps(state, ensure_ascii=False),
            encoding="utf-8",
        )
        with self.assertRaisesRegex(RuntimeError, "已经过期"):
            require_database(self.archive)


if __name__ == "__main__":
    unittest.main()
