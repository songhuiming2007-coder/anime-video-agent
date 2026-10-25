"""截图上的文字识别（D141）：macOS 自带 Vision 框架，经 `swift` 跑同目录的 ocr_vision.swift。

`expect_text` 原先只核元素的 DOM 文字：Wayback `id_` 页样式丢失、文字被遮挡或落在截图区域外时，
DOM 里有那句话、图上却没画出来，照样通过（2026-10-24 卷三两张 ORICON 截图，靠人看图拦下）。
截图要证的是「图上写着这句话」，所以对 PNG 本身再识别一次。不装依赖：Vision 随系统，
`swift` 随 Xcode 命令行工具；本项目只管 macOS。识别不了就报错，不当作通过。
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

SCRIPT = Path(__file__).with_name("ocr_vision.swift")
# 冷启动（首次编译模块缓存）实测约 8 s，之后约 1.3 s；大截图再留余量
TIMEOUT_S = 120


def recognize(png: bytes) -> str:
    """PNG → 识别出的文字，一行一个结果。swift 不在、Vision 报错、超时都抛 ValueError。"""
    swift = shutil.which("swift")
    if swift is None:
        raise ValueError("核截图文字要用 macOS 的 Vision（经 swift 调用），本机没有 swift：xcode-select --install")
    with tempfile.TemporaryDirectory() as d:
        img = Path(d) / "shot.png"
        img.write_bytes(png)
        try:
            r = subprocess.run([swift, str(SCRIPT), str(img)], capture_output=True, text=True, timeout=TIMEOUT_S)
        except subprocess.TimeoutExpired:
            raise ValueError(f"截图文字识别超时（{TIMEOUT_S} s）") from None
    if r.returncode != 0:
        raise ValueError(f"截图文字识别失败（swift 退出码 {r.returncode}）：{r.stderr.strip()[-300:]}")
    return r.stdout
