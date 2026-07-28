#!/usr/bin/env python3
"""Run the repository query CLI from the bundled Skill."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def find_repository() -> Path:
    configured = os.environ.get("BYR_JOB_REPO")
    candidates = [Path(configured).expanduser()] if configured else []
    candidates.extend(Path(__file__).resolve().parents)
    for candidate in candidates:
        if (candidate / "byr_job_query.py").is_file():
            return candidate
    raise SystemExit(
        "未找到 byr_job_query.py；请将 BYR_JOB_REPO 指向 byr-job-archive 仓库。"
    )


def main() -> None:
    repository = find_repository()
    command = [sys.executable, str(repository / "byr_job_query.py"), *sys.argv[1:]]
    raise SystemExit(subprocess.run(command, cwd=repository).returncode)


if __name__ == "__main__":
    main()
