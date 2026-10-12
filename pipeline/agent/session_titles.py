"""会话标题（D80 R1，spec `docs/dev/plans/2026-10-12-conv-liveliness-spec.md`）。

标题落在会话记录所在目录的 `_agent/session-titles.json`（期会话 = 期目录；选题会话 = `data/_idea`），
形如 `{sid: {"title", "source", "at"}}`。**不往 `session.jsonl` 塞元数据**：会话记录只记消息，
`rebuild_messages` 与金样本不受影响（人裁决 Q2）。

三级来源（照 ZCode `first_input / generated / custom`）：
- 没有记录 = `first_input`：列表显示首条用户消息截断（`session_log.FIRST_USER_CHARS`），不落盘；
- `generated`：首轮正常结束后后台线程请模型起的名；
- `custom`：人手动改的名，**优先级最高**——迟到的 `generated` 不得覆盖（读改写在文件锁内比对来源，CAS）。

写者有两个进程：协议进程的标题线程（generated）与 `/rename-session` 人令（custom，不取期租约，
会话在跑时也能改名）。两者都持 `_agent/session-titles.lock` 的 fcntl 锁读改写、`atomic_write` 落盘。
"""

from __future__ import annotations

import contextlib
import fcntl
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

TITLES_NAME = "session-titles.json"
LOCK_NAME = "session-titles.lock"
SOURCES = ("generated", "custom")

# 校验上限 20 字：提示词要求 ≤12 字（侧栏 1280 宽一行约容 13 个汉字，R7 加相对时间后约 11 字）；
# 放宽到 20 是给模型留余量，超出视为没听指令、走兜底（spec §六）
TITLE_MAX_CHARS = 20
# 标题请求在后台线程里跑、不阻塞任何东西，超时只决定线程多挂多久；取 REQUEST_TIMEOUT（60 s）的一半——
# 请求只带首问首答的摘录，远小于主回合（spec §六）
TITLE_TIMEOUT_S = 30
# 喂给模型的首问 / 首答各截这么多字：起名只要大意，上下文越小首字延迟越短（mimo 大上下文 TTFT 实测十几秒起）
EXCERPT_CHARS = 500

# 开头那句也是 e2e 假模型（desktop/e2e/fakeLlm.ts 的 TITLE_PROMPT_HEAD）与 pytest 假端点认起名请求的依据：改这句两处一起改
PROMPT = (
    "给下面这段对话起一个会话标题：中文，不超过 12 个字，概括用户这次要做什么"
    "（如「检查稿件字数」「清理临时文件」）。不要带期名、番名或引号，不要句号，只输出标题本身。"
)

# 模型常给标题包一层引号或书名号；去掉后再校验（不是改写内容，只剥外壳）
_WRAPPERS = ("「」", "『』", "“”", '""', "''", "《》", "【】")
_CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f]")


class TitlesBroken(RuntimeError):
    """标题文件存在但解析不了：写者一律拒写，不拿空表覆盖掉人改过的名。"""


def titles_path(log_dir: Path) -> Path:
    return Path(log_dir) / "_agent" / TITLES_NAME


def validate(text: Any) -> str | None:
    """模型或人给的标题 → 合格标题或 None。去首尾空白、剥一层引号 / 书名号、去句末句号；
    含换行或控制字符、为空、超 `TITLE_MAX_CHARS` 字一律不合格（不截断：截出来的半句名比兜底更糟）。"""
    if not isinstance(text, str):
        return None
    t = text.strip()
    for pair in _WRAPPERS:
        if len(t) >= 2 and t[0] == pair[0] and t[-1] == pair[1]:
            t = t[1:-1].strip()
            break
    t = t.rstrip("。.").strip()
    if not t or _CONTROL.search(t) or len(t) > TITLE_MAX_CHARS:
        return None
    return t


def _parse(raw: str) -> dict[str, dict[str, Any]]:
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("顶层不是对象")
    out: dict[str, dict[str, Any]] = {}
    for sid, entry in data.items():
        if isinstance(entry, dict) and isinstance(entry.get("title"), str) and entry.get("source") in SOURCES:
            out[str(sid)] = entry
    return out


def load(log_dir: Path) -> dict[str, dict[str, Any]]:
    """只读：没有文件或解析不了都当没有标题（列表照常用兜底名显示，不因标题坏了读不出会话）。"""
    try:
        return _parse(titles_path(log_dir).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


@contextlib.contextmanager
def _lock(log_dir: Path) -> Iterator[None]:
    """`_agent/session-titles.lock` 的阻塞 fcntl 锁：两个写者的临界区只有一次读改写，等一下即可。"""
    agent_dir = Path(log_dir) / "_agent"
    agent_dir.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(agent_dir / LOCK_NAME), os.O_RDWR | os.O_CREAT, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        os.close(fd)


def write(log_dir: Path, sid: str, title: str, source: str) -> bool:
    """持锁读改写。返回是否写入：`generated` 遇已有 `custom` → 不写（CAS，人改的名永远不被覆盖）。
    文件存在但解析不了 → `TitlesBroken`（不拿空表覆盖）；写盘失败 → OSError 原样上抛。"""
    from pipeline.paths import atomic_write

    if source not in SOURCES:
        raise ValueError(f"未知的标题来源：{source!r}")
    path = titles_path(log_dir)
    with _lock(log_dir):
        try:
            raw = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            data: dict[str, dict[str, Any]] = {}
        else:
            try:
                data = _parse(raw)
            except ValueError as exc:
                raise TitlesBroken(f"{path} 无法解析，拒绝覆盖：{exc}") from None
        if source == "generated" and data.get(sid, {}).get("source") == "custom":
            return False
        data[sid] = {"title": title, "source": source, "at": datetime.now(timezone.utc).isoformat()}
        atomic_write(path, json.dumps(data, ensure_ascii=False, indent=1) + "\n")
    return True


def copy_entry(src_dir: Path, dst_dir: Path, sid: str) -> None:
    """建期迁会话（Spec 18）时把这段的标题带进新期；没有就什么都不做。源条目不删（sid 不复用，残留无害）。"""
    entry = load(src_dir).get(sid)
    if entry is not None:
        write(dst_dir, sid, str(entry["title"]), str(entry["source"]))


def generate(first_user: str, answer: str, root: Path | None, scope: str) -> str | None:
    """请模型起名。任何失败（没配模型、网络 / HTTP、出网断言、超时、返回不合格）一律 None——
    调用方静默保留兜底名，不发 notice、不影响主回合（R1.2）。走 `light` 档：配了轻档用轻档，没配回落主模型（Q1）。"""
    from pipeline.agent import llm as llm_mod

    cfg = llm_mod.load_llm_config(root)
    if cfg is None:
        return None
    messages = [
        {"role": "system", "content": PROMPT},
        {"role": "user", "content": f"用户：{first_user[:EXCERPT_CHARS]}\n\n助手：{answer[:EXCERPT_CHARS]}"},
    ]
    try:
        reply = llm_mod.chat_complete(messages, config=cfg, timeout=TITLE_TIMEOUT_S, scope=scope, purpose="light")
    except Exception:  # noqa: BLE001 —— 后台起名是锦上添花：出网断言、网络、协议，什么错都只意味着「没有新名」
        return None
    return validate(reply.get("content") if isinstance(reply, dict) else None)
