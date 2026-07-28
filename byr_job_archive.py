#!/usr/bin/env python3
"""Incrementally archive one year of job-related BYR BBS boards via Telnet.

The scraper:
1. walks board index pages backwards from the newest article;
2. opens each selected article through the official guest Telnet interface;
3. stores one Markdown file per article;
4. keeps a JSON state/index for stable deduplication;
5. rebuilds a SQLite search index for agents;
6. optionally rebuilds an Excel index through build_byr_job_index.mjs.
"""

from __future__ import annotations

import argparse
import codecs
import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Iterable

import pexpect

from byr_job_store import DATABASE_NAME, build_database


BOARD_NAMES = {
    "JobInfo": "招聘信息专版",
    "Job": "毕业生找工作",
    "Jump": "跳槽就业",
}

MONTHS = {
    "Jan": 1,
    "Feb": 2,
    "Mar": 3,
    "Apr": 4,
    "May": 5,
    "Jun": 6,
    "Jul": 7,
    "Aug": 8,
    "Sep": 9,
    "Oct": 10,
    "Nov": 11,
    "Dec": 12,
}

ANSI_RE = re.compile(r"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07]*(?:\x07|\x1b\\))")
CATALOG_RE = re.compile(
    r"^\s*>?\s*(?P<number>\d+)\s+"
    r"(?P<author>\S+?)\s+"
    r"(?P<month>Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s*"
    r"(?P<day>\d{1,2})\s+"
    r"(?P<title>.*)$"
)
HEADER_RE = re.compile(
    r"发信人:\s*(?P<author>\S+)(?:\s+\((?P<nickname>.*?)\))?,\s*信区:\s*(?P<board>\S+)"
)
TITLE_RE = re.compile(r"标\s*题:\s*(?P<title>.*)")
POSTED_RE = re.compile(
    r"发信站:\s*北邮人论坛\s*\("
    r"(?P<weekday>[A-Za-z]{3})\s+"
    r"(?P<month>[A-Za-z]{3})\s+"
    r"(?P<day>\d{1,2})\s+"
    r"(?P<clock>\d{2}:\d{2}:\d{2})\s+"
    r"(?P<year>\d{4})"
)
EMAIL_RE = re.compile(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}")
URL_RE = re.compile(r"https?://[^\s<>()\]，。；、]+")
CONTACT_RE = re.compile(
    r"(?:微信|vx|V信|QQ|电话|手机|联系(?:方式)?)[：:\s]*"
    r"([A-Za-z0-9_.+-]{5,})",
    re.IGNORECASE,
)
COHORT_RE = re.compile(r"(?:20)?(?:2[4-9])届|20\d{2}[.-]\d{1,2}")
EXPERIENCE_RE = re.compile(
    r"(?:\d+\s*[-~至]\s*\d+|\d+)\s*年(?:以上)?(?:[^，。；\n]{0,12})?(?:经验|工作)"
)

LOCATION_WORDS = [
    "北京",
    "上海",
    "郑州",
    "成都",
    "深圳",
    "广州",
    "杭州",
    "南京",
    "武汉",
    "西安",
    "苏州",
    "天津",
    "重庆",
    "长沙",
    "合肥",
    "青岛",
    "厦门",
    "海外",
    "远程",
    "线上",
]

ORGANIZATION_WORDS = [
    "阿里",
    "阿里云",
    "字节跳动",
    "抖音",
    "快手",
    "美团",
    "京东",
    "百度",
    "腾讯",
    "华为",
    "中兴",
    "拼多多",
    "PDD",
    "得物",
    "小红书",
    "蚂蚁",
    "米哈游",
    "大疆",
    "网易",
    "小米",
    "国家数据发展研究院",
    "中车研究院",
    "中国电信",
    "中电信人工智能公司",
    "中国光大银行",
]

ROLE_RULES = [
    ("产品", ["产品经理", "产品运营", "产品实习", "产品策划", "产品助理"]),
    ("运营", ["运营", "增长", "内容生态", "用户增长", "商业化"]),
    ("算法", ["算法", "机器学习", "深度学习", "大模型", "LLM", "NLP", "CV"]),
    ("研发", ["研发", "开发", "后端", "前端", "客户端", "测试", "工程师"]),
    ("数据", ["数据分析", "数据产品", "数据科学", "数据治理", "数仓"]),
    ("研究/职能", ["研究员", "政策研究", "项目管理", "综合支撑", "行政", "人力"]),
    ("销售/市场", ["销售", "市场", "营销", "商务", "渠道"]),
]

RECRUITMENT_TERMS = [
    "招聘",
    "校招",
    "社招",
    "实习",
    "内推",
    "岗位",
    "职位",
    "简历",
    "投递",
    "任职要求",
]
NON_RECRUITMENT_TITLE_TERMS = [
    "求助",
    "offer比较",
    "offer求比较",
    "请教",
    "讨论",
    "咨询",
    "怎么选",
    "怎么办",
]

PAGE_UP = "\x1b[5~"


@dataclass(frozen=True)
class CatalogItem:
    board: str
    number: int
    listed_author: str
    listed_title: str
    inferred_date: str


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def clean_screen(text: str) -> str:
    text = ANSI_RE.sub("", text).replace("\r", "")
    return "\n".join(line.rstrip() for line in text.splitlines())


def read_until_quiet(
    child: pexpect.spawn,
    *,
    quiet_seconds: float = 0.25,
    max_seconds: float = 4.0,
) -> str:
    deadline = time.monotonic() + max_seconds
    chunks: list[str] = []
    while time.monotonic() < deadline:
        try:
            chunks.append(
                child.read_nonblocking(size=65536, timeout=quiet_seconds)
            )
        except pexpect.TIMEOUT:
            if chunks:
                break
        except pexpect.EOF:
            break
    return "".join(chunks)


def send_line(child: pexpect.spawn, value: str = "") -> None:
    child.send(value)
    child.send("\r")


class ByrTelnetSession:
    def __init__(
        self,
        host: str,
        port: int,
        login_id: str = "guest",
    ) -> None:
        self.host = host
        self.port = port
        self.login_id = login_id
        self.child: pexpect.spawn | None = None
        self.board: str | None = None

    def connect(self, board: str) -> None:
        self.close()
        nc_args = ["/usr/bin/nc", "-t", self.host, str(self.port)]

        command = shlex.join(nc_args)
        child = pexpect.spawn(
            "/bin/sh",
            ["-c", f"stty raw -echo; exec {command}"],
            encoding="gbk",
            codec_errors="replace",
            timeout=10,
            env={"TERM": "xterm-256color"},
            dimensions=(24, 100),
        )
        try:
            child.expect("请输入代号", timeout=12)
            send_line(child, self.login_id)
            result = child.expect(["按任何键继续", "最大登录用户数"], timeout=12)
            if result == 1:
                raise RuntimeError("访客席位已满，请稍后重试")

            send_line(child)
            for _ in range(8):
                menu_result = child.expect(
                    ["目前选择", "按任何键继续", "上次连线时间"], timeout=12
                )
                if menu_result == 0:
                    break
                send_line(child)
            else:
                raise RuntimeError("未能进入论坛主菜单")

            send_line(child, "S")
            child.expect("请输入讨论区名称", timeout=10)
            send_line(child, board)
            board_result = child.expect(
                ["按任何键继续", f"\\[{re.escape(board)}\\]"], timeout=10
            )
            if board_result == 0:
                send_line(child)
                child.expect(f"\\[{re.escape(board)}\\]", timeout=10)
            read_until_quiet(child)
        except pexpect.TIMEOUT as exc:
            child.close(force=True)
            raise RuntimeError(
                f"连接 {self.host}:{self.port} 超时，论坛没有返回登录界面。"
                "请检查当前网络或稍后重试。"
            ) from exc
        except pexpect.EOF as exc:
            child.close(force=True)
            raise RuntimeError("Telnet 连接被论坛或网络提前关闭") from exc
        except Exception:
            child.close(force=True)
            raise

        self.child = child
        self.board = board

    def ensure(self, board: str) -> pexpect.spawn:
        if self.child is None or not self.child.isalive() or self.board != board:
            self.connect(board)
        assert self.child is not None
        return self.child

    def latest_screen(self, board: str) -> str:
        child = self.ensure(board)
        child.send("$")
        read_until_quiet(child)
        child.send("\x0c")
        return clean_screen(read_until_quiet(child, max_seconds=5))

    def previous_catalog_screen(self, board: str) -> str:
        child = self.ensure(board)
        child.send(PAGE_UP)
        read_until_quiet(child)
        child.send("\x0c")
        return clean_screen(read_until_quiet(child, max_seconds=5))

    def read_article(
        self, board: str, number: int, max_pages: int = 80
    ) -> tuple[str, bool]:
        child = self.ensure(board)

        send_line(child, str(number))
        read_until_quiet(child)
        send_line(child)
        first = clean_screen(read_until_quiet(child, max_seconds=6))
        screens = [first]
        complete = "[阅读文章]" in first

        for _ in range(max_pages - 1):
            if complete:
                break
            if "主题不存在" in screens[-1] or "没有这篇文章" in screens[-1]:
                break
            child.send(" ")
            screen = clean_screen(read_until_quiet(child, max_seconds=5))
            screens.append(screen)
            complete = "[阅读文章]" in screen

        child.send("q")
        read_until_quiet(child)
        return merge_article_screens(screens), complete

    def close(self) -> None:
        if self.child is not None:
            self.child.close(force=True)
        self.child = None
        self.board = None

    def __enter__(self) -> "ByrTelnetSession":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


def parse_catalog_screen(screen: str, board: str) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for line in screen.splitlines():
        match = CATALOG_RE.match(line)
        if not match:
            continue
        title = re.sub(r"^[\s@●○◆◇★☆?]+", "", match.group("title")).strip()
        items.append(
            {
                "board": board,
                "number": int(match.group("number")),
                "listed_author": match.group("author"),
                "listed_title": title,
                "month": MONTHS[match.group("month")],
                "day": int(match.group("day")),
            }
        )
    return items


def infer_catalog_dates(
    rows_descending: Iterable[dict[str, Any]],
    *,
    starting_year: int,
    previous_month_day: tuple[int, int],
) -> tuple[list[CatalogItem], int, tuple[int, int]]:
    year = starting_year
    previous = previous_month_day
    output: list[CatalogItem] = []

    for row in rows_descending:
        current = (row["month"], row["day"])
        if current > previous:
            year -= 1
        inferred = date(year, current[0], current[1])
        output.append(
            CatalogItem(
                board=row["board"],
                number=row["number"],
                listed_author=row["listed_author"],
                listed_title=row["listed_title"],
                inferred_date=inferred.isoformat(),
            )
        )
        previous = current
    return output, year, previous


def collect_catalog(
    session: ByrTelnetSession,
    board: str,
    cutoff: date,
    *,
    stop_number: int | None,
    max_posts: int | None,
) -> list[CatalogItem]:
    today = date.today()
    year = today.year
    previous_month_day = (today.month, today.day)
    seen_numbers: set[int] = set()
    seen_pages: set[tuple[int, ...]] = set()
    collected: list[CatalogItem] = []
    screen = session.latest_screen(board)
    page_count = 0

    while True:
        page_count += 1
        parsed = parse_catalog_screen(screen, board)
        fresh = [row for row in parsed if row["number"] not in seen_numbers]
        fresh.sort(key=lambda row: row["number"], reverse=True)

        page_signature = tuple(sorted(row["number"] for row in parsed))
        if not page_signature or page_signature in seen_pages:
            break
        seen_pages.add(page_signature)

        dated, year, previous_month_day = infer_catalog_dates(
            fresh,
            starting_year=year,
            previous_month_day=previous_month_day,
        )

        if page_count == 1 or page_count % 5 == 0:
            oldest = dated[-1].inferred_date if dated else "未识别"
            print(
                f"[{board}] 目录进度：已扫描 {page_count} 页，"
                f"发现 {len(seen_numbers) + len(dated)} 个编号，"
                f"当前最早日期 {oldest}",
                flush=True,
            )

        should_stop = False
        for item in dated:
            seen_numbers.add(item.number)
            item_date = date.fromisoformat(item.inferred_date)
            if item_date < cutoff:
                should_stop = True
                continue
            if stop_number is not None and item.number < stop_number:
                should_stop = True
                continue
            collected.append(item)
            if max_posts is not None and len(collected) >= max_posts:
                should_stop = True
                break

        if should_stop:
            break
        screen = session.previous_catalog_screen(board)

    print(
        f"[{board}] 目录扫描完成：{page_count} 页，"
        f"本次进入正文检查 {len(collected)} 篇",
        flush=True,
    )
    return collected


def strip_screen_noise(lines: Iterable[str]) -> list[str]:
    output: list[str] = []
    for line in lines:
        stripped = line.strip()
        if not stripped:
            output.append("")
            continue
        if any(
            token in line
            for token in (
                "下面还有喔",
                "[阅读文章]",
                "离开[←",
                "在线/最高:",
                "总人数/好友",
                "转到∶[",
            )
        ):
            continue
        if re.search(r"第\(\d+-\d+\)行", line):
            continue
        if re.match(r"^\s*编号\s+刊", line):
            continue
        if re.match(r"^\s*>?\s*\d+\s+\S+\s+[A-Z][a-z]{2}\s*\d+", line):
            continue
        output.append(line.rstrip())
    return output


def merge_with_overlap(existing: list[str], incoming: list[str]) -> list[str]:
    max_overlap = min(len(existing), len(incoming), 40)
    for overlap in range(max_overlap, 0, -1):
        if existing[-overlap:] == incoming[:overlap]:
            return existing + incoming[overlap:]
    return existing + incoming


def merge_article_screens(screens: list[str]) -> str:
    merged: list[str] = []
    found_header = False

    for screen in screens:
        lines = strip_screen_noise(screen.splitlines())
        if not found_header:
            for index, line in enumerate(lines):
                if line.startswith("发信人:"):
                    lines = lines[index:]
                    found_header = True
                    break
            else:
                continue
        else:
            for index, line in enumerate(lines):
                if line.startswith("发信人:"):
                    lines = lines[index:]
                    break
        merged = merge_with_overlap(merged, lines)

    cleaned: list[str] = []
    for line in merged:
        if "※ 来源:" in line or "※ 修改:" in line:
            continue
        if line.strip().startswith("[FROM:"):
            continue
        if "回信 R │" in line:
            continue
        cleaned.append(line)

    while cleaned and cleaned[-1].strip() in {"", "--"}:
        cleaned.pop()
    return "\n".join(cleaned)


def parse_article(
    catalog: CatalogItem,
    raw_text: str,
    *,
    complete: bool,
) -> dict[str, Any]:
    lines = raw_text.splitlines()
    author = catalog.listed_author
    nickname = ""
    title = catalog.listed_title or f"{catalog.board} {catalog.number}"
    published_at = f"{catalog.inferred_date}T00:00:00+08:00"
    body_start = 0

    for index, line in enumerate(lines[:10]):
        header_match = HEADER_RE.search(line)
        if header_match:
            author = header_match.group("author")
            nickname = header_match.group("nickname") or ""
        title_match = TITLE_RE.search(line)
        if title_match:
            title = title_match.group("title").strip()
        posted_match = POSTED_RE.search(line)
        if posted_match:
            parsed = datetime.strptime(
                (
                    f"{posted_match.group('month')} "
                    f"{posted_match.group('day')} "
                    f"{posted_match.group('clock')} "
                    f"{posted_match.group('year')}"
                ),
                "%b %d %H:%M:%S %Y",
            )
            published_at = parsed.replace(
                tzinfo=datetime.now().astimezone().tzinfo
            ).isoformat()
            body_start = index + 1

    body_lines = lines[body_start:]
    while body_lines and body_lines[0].strip() in {"", "--"}:
        body_lines.pop(0)
    while body_lines and body_lines[-1].strip() in {"", "--"}:
        body_lines.pop()
    body = "\n".join(body_lines).strip()

    combined = f"{title}\n{body}"
    metadata = extract_key_information(title, body)
    content_hash = hashlib.sha256(
        f"{author}\n{title}\n{published_at}\n{body}".encode("utf-8")
    ).hexdigest()

    return {
        "key": f"{catalog.board}:{catalog.number}",
        "board": catalog.board,
        "board_name": BOARD_NAMES[catalog.board],
        "article_number": catalog.number,
        "author": author,
        "nickname": nickname,
        "title": title,
        "published_at": published_at,
        "body": body,
        "capture_complete": complete,
        "content_hash": content_hash,
        **metadata,
        "character_count": len(combined),
    }


def unique_in_order(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    output: list[str] = []
    for value in values:
        value = value.strip().rstrip(".,;")
        if value and value not in seen:
            seen.add(value)
            output.append(value)
    return output


def extract_key_information(title: str, body: str) -> dict[str, Any]:
    text = f"{title}\n{body}"
    compact = re.sub(r"\s+", " ", body).strip()
    title_lower = title.lower()

    is_recruitment = any(term.lower() in text.lower() for term in RECRUITMENT_TERMS)
    if any(term.lower() in title_lower for term in NON_RECRUITMENT_TITLE_TERMS):
        is_recruitment = False

    recruitment_types = [
        label
        for label in ("校招", "社招", "实习", "内推", "兼职", "全职")
        if label in text
    ]
    role_categories = [
        label
        for label, keywords in ROLE_RULES
        if any(keyword.lower() in text.lower() for keyword in keywords)
    ]
    organizations = [
        organization for organization in ORGANIZATION_WORDS if organization in text
    ]
    locations = [location for location in LOCATION_WORDS if location in text]
    emails = unique_in_order(EMAIL_RE.findall(text))
    urls = unique_in_order(URL_RE.findall(text))
    contacts = unique_in_order(match.group(1) for match in CONTACT_RE.finditer(text))
    cohorts = unique_in_order(COHORT_RE.findall(text))
    experience = unique_in_order(EXPERIENCE_RE.findall(text))

    internship_terms: list[str] = []
    for pattern in (
        r"每周[^，。；\n]{0,20}(?:天|日)",
        r"实习(?:期|时长|周期)[^，。；\n]{0,30}",
        r"(?:至少|不少于)[^，。；\n]{0,20}(?:个月|月)",
        r"(?:立即|尽快)[^，。；\n]{0,10}到岗",
    ):
        internship_terms.extend(re.findall(pattern, text))

    education = unique_in_order(
        re.findall(
            r"(?:本科|硕士|博士|大专|专科)(?:及以上|以上|研究生|学历)?",
            text,
        )
    )

    return {
        "is_recruitment": is_recruitment,
        "recruitment_type": " / ".join(unique_in_order(recruitment_types)),
        "role_category": " / ".join(unique_in_order(role_categories)),
        "organization": " / ".join(unique_in_order(organizations)),
        "locations": " / ".join(unique_in_order(locations)),
        "cohorts": " / ".join(cohorts),
        "education": " / ".join(education),
        "internship_requirement": "；".join(unique_in_order(internship_terms)),
        "experience_requirement": "；".join(experience),
        "emails": "；".join(emails),
        "contacts": "；".join(contacts),
        "application_urls": "；".join(urls),
        "summary": compact[:500],
    }


def markdown_for(post: dict[str, Any]) -> str:
    body = post["body"] or "（正文为空或未能读取）"
    yaml_title = json.dumps(post["title"], ensure_ascii=False)
    return f"""---
board: {post["board"]}
board_name: {post["board_name"]}
article_number: {post["article_number"]}
title: {yaml_title}
author: {post["author"]}
published_at: {post["published_at"]}
archived_at: {post["last_checked_at"]}
capture_complete: {str(post["capture_complete"]).lower()}
content_hash: {post["content_hash"]}
source: telnet://bbs.byr.cn/{post["board"]}/{post["article_number"]}
---

# {post["title"]}

| 字段 | 内容 |
|---|---|
| 版面 | {post["board_name"]}（`{post["board"]}`） |
| Telnet 文章编号 | `{post["article_number"]}` |
| 作者 | `{post["author"]}` |
| 发布时间 | {post["published_at"]} |
| 是否招聘帖 | {"是" if post["is_recruitment"] else "否/讨论帖"} |
| 招聘类型 | {post["recruitment_type"] or "未识别"} |
| 岗位类别 | {post["role_category"] or "未识别"} |
| 地点 | {post["locations"] or "未识别"} |
| 邮箱 | {post["emails"] or "未识别"} |
| 联系方式 | {post["contacts"] or "未识别"} |

## 原帖正文

{body}
"""


def load_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {
            "schema_version": 1,
            "created_at": now_iso(),
            "updated_at": now_iso(),
            "boards": {},
            "posts": {},
        }
    with path.open("r", encoding="utf-8") as handle:
        state = json.load(handle)
    state.setdefault("boards", {})
    state.setdefault("posts", {})
    return state


def save_json_atomic(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    temporary.replace(path)


def write_post(
    output_dir: Path,
    post: dict[str, Any],
    existing: dict[str, Any] | None,
) -> tuple[dict[str, Any], str]:
    board_dir = output_dir / f"{post['board']}-{post['board_name']}"
    board_dir.mkdir(parents=True, exist_ok=True)
    markdown_path = board_dir / f"{post['article_number']}.md"
    checked_at = now_iso()

    post["markdown_path"] = str(markdown_path.relative_to(output_dir))
    post["first_archived_at"] = (
        existing.get("first_archived_at", checked_at) if existing else checked_at
    )
    post["last_checked_at"] = checked_at

    if (
        existing
        and existing.get("content_hash") == post["content_hash"]
        and markdown_path.exists()
    ):
        status = "unchanged"
    else:
        markdown_path.write_text(markdown_for(post), encoding="utf-8")
        status = "updated" if existing else "new"

    stored = {key: value for key, value in post.items() if key != "body"}
    return stored, status


def has_archived_markdown(
    output_dir: Path,
    existing: dict[str, Any] | None,
) -> bool:
    """Return whether a state entry still has its expected local Markdown file."""
    if not existing:
        return False
    relative_path = existing.get("markdown_path")
    if not relative_path:
        return False
    try:
        markdown_path = (output_dir / relative_path).resolve()
        markdown_path.relative_to(output_dir.resolve())
    except (OSError, ValueError):
        return False
    return markdown_path.is_file()


def rebuild_excel(output_dir: Path, state_path: Path, skip_excel: bool) -> None:
    if skip_excel:
        return
    node = shutil.which("node")
    if not node:
        raise RuntimeError(
            "未找到 Node.js，无法生成 Excel。可先用 --skip-excel 抓取，"
            "安装 Node.js 后再执行 --rebuild-excel。"
        )
    builder = Path(__file__).with_name("build_byr_job_index.mjs")
    output = output_dir / "北邮人论坛招聘索引.xlsx"
    subprocess.run(
        [
            node,
            str(builder),
            "--input",
            str(state_path),
            "--output",
            str(output),
        ],
        check=True,
    )


def rebuild_agent_index(
    output_dir: Path,
    state_path: Path,
    skip_agent_index: bool,
) -> None:
    if skip_agent_index:
        return
    result = build_database(state_path, output_dir / DATABASE_NAME)
    print(
        f"Agent 查询库已更新：{result['database']}（{result['posts']} 篇）"
    )


def process_board(
    session: ByrTelnetSession,
    state: dict[str, Any],
    output_dir: Path,
    board: str,
    cutoff: date,
    *,
    rescan_last: int,
    full_rescan: bool,
    max_posts: int | None,
    max_pages: int,
    state_path: Path,
) -> dict[str, int]:
    board_state = state["boards"].setdefault(board, {})
    previous_highest = int(board_state.get("last_seen_number", 0))
    stop_number = None
    initial_backfill_complete = bool(
        board_state.get("initial_backfill_complete", False)
    )
    resume_backfill = not initial_backfill_complete and not full_rescan
    if previous_highest and initial_backfill_complete and not full_rescan:
        stop_number = max(1, previous_highest - rescan_last)

    print(
        f"\n[{board} / {BOARD_NAMES[board]}] 扫描目录"
        f"（截止 {cutoff.isoformat()}，增量下限 {stop_number or '所选时间范围'}）"
    )
    catalog = collect_catalog(
        session,
        board,
        cutoff,
        stop_number=stop_number,
        max_posts=max_posts,
    )
    print(f"[{board}] 本次需检查 {len(catalog)} 篇")

    counters = {
        "new": 0,
        "updated": 0,
        "unchanged": 0,
        "skipped": 0,
        "failed": 0,
    }
    highest_seen = previous_highest
    for index, item in enumerate(catalog, start=1):
        key = f"{board}:{item.number}"
        existing = state["posts"].get(key)
        if resume_backfill and has_archived_markdown(output_dir, existing):
            counters["skipped"] += 1
            highest_seen = max(highest_seen, item.number)
            if index == 1 or index % 250 == 0:
                print(
                    f"[{board}] {index}/{len(catalog)} "
                    f"已从本地快速跳过 {counters['skipped']} 篇，"
                    "继续寻找未下载帖子",
                    flush=True,
                )
            continue
        try:
            raw_text, complete = session.read_article(
                board, item.number, max_pages=max_pages
            )
            if not raw_text.startswith("发信人:"):
                raise RuntimeError("未读取到文章正文页")
            post = parse_article(item, raw_text, complete=complete)
            stored, status = write_post(output_dir, post, existing)
            state["posts"][key] = stored
            counters[status] += 1
            highest_seen = max(highest_seen, item.number)
            marker = {"new": "新增", "updated": "更新", "unchanged": "重复"}[status]
            print(
                f"[{board}] {index}/{len(catalog)} {item.number} "
                f"{marker}：{stored['title'][:60]}"
            )
        except Exception as exc:
            counters["failed"] += 1
            print(f"[{board}] {item.number} 失败：{exc}", file=sys.stderr)
            session.connect(board)

        board_state["last_seen_number"] = highest_seen
        board_state["last_scan_at"] = now_iso()
        board_state["cutoff"] = cutoff.isoformat()
        state["updated_at"] = now_iso()
        save_json_atomic(state_path, state)

    if max_posts is None and counters["failed"] == 0:
        board_state["initial_backfill_complete"] = True
        board_state["initial_backfill_completed_at"] = board_state.get(
            "initial_backfill_completed_at", now_iso()
        )
    state["updated_at"] = now_iso()
    save_json_atomic(state_path, state)
    return counters


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="增量归档北邮人论坛三个就业版面的招聘信息。"
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(__file__).with_name("北邮人论坛招聘信息归档"),
        help="归档目录",
    )
    parser.add_argument(
        "--boards",
        nargs="+",
        choices=sorted(BOARD_NAMES),
        default=list(BOARD_NAMES),
    )
    parser.add_argument("--days", type=int, default=90, help="首次抓取天数（默认 90）")
    parser.add_argument(
        "--rescan-last",
        type=int,
        default=30,
        help="增量运行时复查每个版面最后 N 个编号，用于发现编辑",
    )
    parser.add_argument(
        "--full-rescan",
        action="store_true",
        help="忽略增量编号，重新检查所选时间范围（内容相同不会重写）",
    )
    parser.add_argument(
        "--max-posts",
        type=int,
        help="每个版面最多检查多少篇；主要用于测试",
    )
    parser.add_argument(
        "--max-pages",
        type=int,
        default=80,
        help="单篇文章最多读取多少屏",
    )
    parser.add_argument("--host", default="bbs.byr.cn")
    parser.add_argument("--port", type=int, default=23)
    parser.add_argument(
        "--skip-excel",
        action="store_true",
        help="本次不重建 Excel",
    )
    parser.add_argument(
        "--skip-agent-index",
        action="store_true",
        help="本次不重建 SQLite Agent 查询库",
    )
    parser.add_argument(
        "--rebuild-excel",
        action="store_true",
        help="不连接论坛，只根据现有 state.json 重建 Excel",
    )
    parser.add_argument(
        "--rebuild-agent-index",
        action="store_true",
        help="不连接论坛，只根据 state.json 和 Markdown 重建 SQLite 查询库",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    output_dir = args.output.expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    state_path = output_dir / "state.json"
    state = load_state(state_path)

    if args.rebuild_excel or args.rebuild_agent_index:
        if args.rebuild_excel:
            rebuild_excel(output_dir, state_path, args.skip_excel)
            print(f"已重建：{output_dir / '北邮人论坛招聘索引.xlsx'}")
        if args.rebuild_agent_index:
            rebuild_agent_index(output_dir, state_path, args.skip_agent_index)
        return

    cutoff = date.today() - timedelta(days=args.days)
    totals = {
        "new": 0,
        "updated": 0,
        "unchanged": 0,
        "skipped": 0,
        "failed": 0,
    }
    scrape_error: BaseException | None = None
    try:
        with ByrTelnetSession(args.host, args.port) as session:
            for board in args.boards:
                counters = process_board(
                    session,
                    state,
                    output_dir,
                    board,
                    cutoff,
                    rescan_last=args.rescan_last,
                    full_rescan=args.full_rescan,
                    max_posts=args.max_posts,
                    max_pages=args.max_pages,
                    state_path=state_path,
                )
                for key, value in counters.items():
                    totals[key] += value
                rebuild_agent_index(
                    output_dir,
                    state_path,
                    args.skip_agent_index,
                )
                if not args.skip_excel:
                    rebuild_excel(output_dir, state_path, skip_excel=False)
                    print(
                        f"[{board}] Excel 检查点已更新："
                        f"{output_dir / '北邮人论坛招聘索引.xlsx'}"
                    )
    except BaseException as exc:
        scrape_error = exc
        raise
    finally:
        state["updated_at"] = now_iso()
        save_json_atomic(state_path, state)
        if scrape_error is not None and not args.skip_agent_index:
            try:
                rebuild_agent_index(output_dir, state_path, False)
            except Exception as index_exc:
                print(
                    f"\n抓取失败后尝试生成 Agent 查询库也失败：{index_exc}",
                    file=sys.stderr,
                )
        if scrape_error is not None and not args.skip_excel:
            try:
                rebuild_excel(output_dir, state_path, skip_excel=False)
                print(
                    "\n抓取虽未完成，但已根据现有归档更新 Excel："
                    f"{output_dir / '北邮人论坛招聘索引.xlsx'}",
                    file=sys.stderr,
                )
            except Exception as excel_exc:
                print(
                    f"\n抓取失败后尝试生成 Excel 也失败：{excel_exc}",
                    file=sys.stderr,
                )

    print(
        "\n完成："
        f"新增 {totals['new']}，更新 {totals['updated']}，"
        f"本地快速跳过 {totals['skipped']}，"
        f"联网确认重复 {totals['unchanged']}，失败 {totals['failed']}。"
    )
    print(f"归档目录：{output_dir}")
    if not args.skip_excel:
        print(f"Excel 索引：{output_dir / '北邮人论坛招聘索引.xlsx'}")
    if not args.skip_agent_index:
        print(f"Agent 查询库：{output_dir / DATABASE_NAME}")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print(
            "\n已停止。已写入 state.json 的文章会保留，"
            "Excel 已尝试按当前进度更新；重新执行相同命令即可继续。",
            file=sys.stderr,
        )
        raise SystemExit(130)
    except RuntimeError as exc:
        print(f"\n错误：{exc}", file=sys.stderr)
        raise SystemExit(1)
