"""静态门禁：必然出错的那几类问题，pytest 一跑就拦下。

只选「运行到就崩」的规则，不碰风格：
  E9    语法错误 / 解析失败
  F821  未定义名（2026-10-13：tts 用了没导入的 datetime，有纠错条目时合成一段即崩；
        clips 写错变量名，NameError 被 except 吞掉，scout 提示一次都没打出来过）
  F822  __all__ 里列了不存在的名字
  F823  局部变量在赋值前被引用

两个 bug 都是 ruff 一跑就报的，却在 main 上待了几周——它们藏在测试没走到的分支里。
测试只能覆盖想到的路径，静态检查不挑路径。

ruff 在 `dev` 依赖里（`pyproject.toml`）。没装就失败而不是跳过：跳过不是通过。
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RULES = "E9,F821,F822,F823"
TARGETS = ["pipeline", "scripts", "tools", "tests"]


def test_没有必然出错的静态问题():
    r = subprocess.run(
        [sys.executable, "-m", "ruff", "check", "--select", RULES, "--output-format", "concise",
         "--no-cache", *TARGETS],
        cwd=ROOT, capture_output=True, text=True,
    )
    if "No module named ruff" in r.stderr:
        raise AssertionError("没装 ruff：`uv pip install --python .venv/bin/python ruff`（dev 依赖），"
                             "或 `uv sync --extra dev` 时带上本机在用的其余 extras")
    assert r.returncode == 0, f"ruff {RULES} 报错：\n{r.stdout}{r.stderr}"
