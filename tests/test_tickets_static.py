"""D82 §4.6：工单目录永不进 git（仓库公开，工单含输出原文、本机路径与期名）。

静态两条跑在真实仓库里：`.gitignore` 覆盖 `tickets/`，且 `tickets/` 下没有任何已跟踪文件。
运行时那道闸（`pipeline.tickets` 写盘前 `git check-ignore`）在 tests/test_tickets.py。
"""

from __future__ import annotations

import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def test_gitignore覆盖tickets目录() -> None:
    r = subprocess.run(["git", "-C", str(REPO), "check-ignore", "-q", "tickets/T-x.md"], check=False)
    assert r.returncode == 0, "tickets/ 没被 .gitignore 覆盖（应有 /tickets/ 一行）"


def test_tickets目录下没有已跟踪文件() -> None:
    r = subprocess.run(["git", "-C", str(REPO), "ls-files", "tickets"], capture_output=True, text=True, check=True)
    assert r.stdout == ""
