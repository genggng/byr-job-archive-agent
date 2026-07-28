# BYR BBS Job Archive for Agents

一个通过北邮人论坛官方 Telnet `guest` 入口，只读归档就业版面并向人和 Agent 提供检索接口的工具。

它保留每帖 Markdown 和 `state.json` 作为可恢复事实来源，并生成三种派生视图：

- `byr_jobs.sqlite3`：结构化字段、规范化筛选维度和中文全文检索；
- JSON CLI / 只读 HTTP API / JSONL：供 Agent 和其他程序访问；
- Excel：供人工筛选和浏览。

当前支持 `JobInfo`、`Job` 和 `Jump` 三个版面。工具不需要论坛账号、密码或 Cookie，也不会发帖、回帖或修改论坛内容。

## 快速开始

环境要求：Python 3.10+、`pexpect`。Excel 导出保留现有 `@oai/artifact-tool` 构建器；该依赖由 Codex 工作区运行时提供，普通外部环境可以只使用 Markdown、SQLite、JSON/HTTP 和 JSONL。

```bash
python3 -m pip install -r requirements.txt

# 抓取或增量刷新（普通外部环境跳过 Codex 专用 Excel 构建器）
python3 byr_job_archive.py --skip-excel --proxy 127.0.0.1:7890

# 查询库会在缺失或 state.json 更新后自动重建
python3 byr_job_query.py stats
python3 byr_job_query.py search --query "大模型" --location 北京 --type 实习
python3 byr_job_query.py get JobInfo:39728
```

查询命令默认输出 UTF-8 JSON，并默认只返回自动识别为招聘信息的帖子。结构化字段用于初筛；重要条件必须以 `get` 返回的原帖正文为准。

## Agent 接口

### JSON CLI

```bash
# 可用版面、时间覆盖和筛选值
python3 byr_job_query.py stats

# 多条件检索；筛选参数可重复
python3 byr_job_query.py search \
  --board JobInfo \
  --query "算法" \
  --location 北京 \
  --cohort 27届 \
  --since 2026-06-01 \
  --limit 20

# 按稳定键读取完整原帖
python3 byr_job_query.py get JobInfo:39728

# 导出流式友好的全量 JSONL
python3 byr_job_query.py export-jsonl --output byr-jobs.jsonl
```

### 只读 HTTP API

```bash
python3 byr_job_query.py serve --host 127.0.0.1 --port 8765
curl --get 'http://127.0.0.1:8765/v1/posts' \
  --data-urlencode 'q=大模型' \
  --data-urlencode 'location=北京' \
  --data-urlencode 'limit=10'
curl 'http://127.0.0.1:8765/v1/posts/JobInfo%3A39728'
```

端点包括 `/health`、`/v1/stats`、`/v1/posts` 和 `/v1/posts/{稳定键}`。服务没有身份验证，默认只绑定本机回环地址。

### Codex Skill

仓库内置 `skills/byr-job-advisor/`。可以从 GitHub 仓库安装该 Skill，或将该目录放入 Codex Skills 目录。Skill 会指导 Agent 先筛选、再读取少量原帖，并将帖子事实与就业建议分开。

若 Skill 与仓库不在同一目录树，设置：

```bash
export BYR_JOB_REPO=/absolute/path/to/this-repository
```

## 存储与恢复

```text
北邮人论坛近一年归档/
├── JobInfo-招聘信息专版/*.md
├── Job-毕业生找工作/*.md
├── Jump-跳槽就业/*.md
├── state.json                       # 增量与恢复真源
├── byr_jobs.sqlite3                 # 可重建的 Agent 查询库
└── 北邮人论坛招聘索引.xlsx            # 可重建的人工视图
```

SQLite 包含：

- `posts`：全部结构化字段和原帖正文；
- `post_facets`：地点、招聘类型、岗位类别、届别等多值维度；
- `posts_fts`：全文检索索引；
- `metadata`：构建时间、源状态版本和覆盖信息。

不要直接写 SQLite。离线重建：

```bash
python3 byr_job_query.py index
python3 byr_job_archive.py --rebuild-excel
```

## 抓取选项

```bash
# 只抓指定版面
python3 byr_job_archive.py --boards JobInfo Jump --proxy 127.0.0.1:7890

# 小范围联网测试
python3 byr_job_archive.py \
  --boards JobInfo \
  --max-posts 3 \
  --skip-excel \
  --proxy 127.0.0.1:7890

# 完整复查时间范围
python3 byr_job_archive.py --full-rescan --proxy 127.0.0.1:7890
```

首次默认回填最近 365 天；正常增量只复查各版面最新 30 个编号。每成功处理一帖会原子更新 `state.json`，中断后可续跑，远端错误不会删除已有归档。

更多运行细节见 [北邮人论坛归档工具说明.md](北邮人论坛归档工具说明.md)。

## 验证

```bash
python3 -m py_compile byr_job_archive.py byr_job_store.py byr_job_query.py
python3 -m unittest discover -s tests -v
python3 byr_job_archive.py --help
python3 byr_job_query.py --help
```

## 数据与使用边界

- 自动分类允许为空且可能误报或漏报，不得据此猜测原帖未表达的信息。
- 联系方式只应在具体、相关帖子中使用，不应批量传播。
- 本地归档只能代表其最新成功抓取时间，不能冒充论坛实时状态或整体就业市场。
- 个性化推荐属于归档之上的应用层，不应写入通用抓取和索引规则。
