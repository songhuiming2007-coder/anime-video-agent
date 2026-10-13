"""pipeline.tickets：生产中撞到流水线真 bug → 经人卡开一张带现场的工单（D82）。

    python -m pipeline.tickets open  <期目录> --call <tool_call_id> --blocked <工序>
            --symptom <现象> --expected <预期> --repro <最小复现> --why <为什么是代码 bug 而不是用法错误>
    python -m pipeline.tickets list  [<期目录>] [--json]      # 只读：只打系统解析出的字段
    python -m pipeline.tickets show  <工单号>                 # 只读：同上

纪律（D82 spec §4）：
1. 现场由系统按 `tool_call_id` 从**会话日志**取（只认 `k == "msg"`、`origin == "tool"`、退出码非 0 的真跑结果），
   模型只能填现象 / 预期 / 复现 / 为何不是用法错误五个文本字段；
2. 全部文本写入前过 scrub_secrets + scrub_restricted，去重键在脱敏之后算；
3. 工单落在仓库根的 `tickets/`（`.gitignore` 的 `/tickets/`，写盘前运行时 `git check-ignore` 再查一次）；
4. 「查重 → 新建或追加」在 `tickets/.lock`（fcntl）内完成；新单目标已存在不覆盖、加 `-2`、`-3`；
5. stdout 只打工单号、路径、一行摘要与末行 `TICKET_JSON {…}`；list / show 也不出正文（工单原文不进模型上下文）；
6. 唯一的外部调用是 `git`（静态测试守住）；不拉起任何 coding agent。
"""

from __future__ import annotations

import argparse
import contextlib
import datetime
import fcntl
import hashlib
import json
import os
import re
import shlex
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

from pipeline import paths

# 取值理由见 D82 spec §6.4
TICKETS_DIR = "tickets"                 # 人裁决 Q1：仓库内、gitignore
TICKET_DIRTY_LIST_MAX = 20              # 现场有哪些未提交改动；超过 20 个本身就说明现场不干净，写「另有 N 个」
TICKET_FIELD_MAX_CHARS = 4000           # 单个模型字段上限：已超过一个作业尾部（4096 字节）的量级，更长多半是又贴了一遍输出
TICKET_WHY_MIN_CHARS = 20               # 只拦「因为是 bug」这类空话；判断真伪的是人
TICKET_RESUME_MAX_CHARS = 200           # 提醒是一行 notice，续跑命令通常是一条 run_pipeline 命令
# WORKFLOW 九步加人工环节编号，再加 D81 企划会话的总装；先后按此表比（§4.8）
BLOCKED_STEPS: tuple[str, ...] = ("01", "02", "02.5", "02.8", "03", "03.5", "04", "05", "06", "07", "08", "09", "总装")
STATUSES = ("待修", "已修", "不是bug", "不修")
_CALL_ID = re.compile(r"[A-Za-z0-9_.:-]{1,128}")
_COMMIT = re.compile(r"[0-9a-f]{7,40}")
_TICKET_ID = re.compile(r"T-\d{8}T\d{6}Z-[0-9a-f]{6}(-\d+)?")
TOOL_NAMES = ("bash", "run_pipeline")
JSON_MARK = "TICKET_JSON "
FIELDS = (("symptom", "现象"), ("expected", "预期"), ("repro", "最小复现"), ("why", "为什么是代码 bug 而不是用法错误"))


class TicketError(Exception):
    """开单不成立（现场取不到、参数不合规、目录没被 gitignore）。消息直接给人看。"""


# ---------------------------------------------------------------------------
# 脱敏（与 bash / run_pipeline 结果同一套：密钥值 + 受限字样）
# ---------------------------------------------------------------------------


def scrub(text: str, root: Path | None = None) -> str:
    from pipeline.agent.tools import scrub_restricted, scrub_secrets

    return scrub_restricted(scrub_secrets(str(text), root))


# ---------------------------------------------------------------------------
# 现场（§4.2）
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Scene:
    tool: str
    command: str
    returncode: int
    stdout_tail: str
    stderr_tail: str
    message: str
    sid: str
    argv: tuple[str, ...]


def collect(ep_dir: Path, call_id: str) -> Scene:
    """按 tool_call_id 从 `<期>/session.jsonl` 取现场；五条规则任一不满足 → TicketError（拒因说明是哪条）。"""
    from pipeline.agent.session_log import find_tool_exchange, read_log, _parse_lines

    if not _CALL_ID.fullmatch(call_id or ""):
        raise TicketError(f"--call 形状不对（[A-Za-z0-9_.:-]，1–128 位）：{call_id!r}")
    records = [r for _line, r in _parse_lines(read_log(ep_dir))[0] if r is not None]
    found = find_tool_exchange(records, call_id)
    if isinstance(found, str):
        raise TicketError(found)
    name, args, result_record = found
    if name not in TOOL_NAMES:
        raise TicketError(f"只能对 bash / run_pipeline 的失败开单，这条是 {name}")
    if result_record.get("origin") != "tool":
        raise TicketError("这条调用没有真正执行（被白名单 / 预检 / 人拒了）：那是用法问题，不是代码 bug")
    try:
        content = json.loads(str((result_record.get("message") or {}).get("content") or ""))
    except (TypeError, ValueError):
        raise TicketError("工具结果不是 JSON 对象，取不到现场") from None
    if not isinstance(content, dict):
        raise TicketError("工具结果不是 JSON 对象，取不到现场")
    result = content.get("result")
    if not isinstance(result, dict) or not isinstance(result.get("returncode"), int) or isinstance(result.get("returncode"), bool):
        raise TicketError("这条调用没有退出码（实现层异常或只校验未执行），不是可开单的失败")
    if result["returncode"] == 0:
        raise TicketError("这条调用退出码为 0，没有失败")
    if name == "run_pipeline":
        argv = tuple(str(a) for a in (result.get("argv") or []))
        command = shlex.join(argv)
    else:
        argv = ()
        command = str((args or {}).get("command", ""))
    return Scene(
        tool=name, command=command, returncode=int(result["returncode"]),
        stdout_tail=str(result.get("stdout_tail") or ""), stderr_tail=str(result.get("stderr_tail") or ""),
        message=str(result.get("message") or result.get("error") or ""),
        sid=str(result_record.get("sid") or ""), argv=argv,
    )


def normalized_command(scene: Scene, ep_dir: Path) -> str:
    """去重用的规范化命令：run_pipeline 取模块名之后的部分、期目录换成 `<期>`；bash 折叠空白。"""
    if scene.tool == "run_pipeline" and "-m" in scene.argv:
        at = scene.argv.index("-m") + 1
        module = scene.argv[at].removeprefix("pipeline.") if at < len(scene.argv) else ""
        eps = {str(ep_dir), str(Path(ep_dir).resolve())}
        rest = ["<期>" if tok in eps else tok for tok in scene.argv[at + 1:]]
        return " ".join([module, *rest])
    return " ".join(scene.command.split())


def error_line(scene: Scene) -> str:
    """报错行 = stderr 尾部最后一个非空行；空则 stdout 的；再空取 message（输出只有尾部，首行不稳定，§三 #4）。"""
    for text in (scene.stderr_tail, scene.stdout_tail, scene.message):
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        if lines:
            return lines[-1]
    return ""


# ---------------------------------------------------------------------------
# 仓库状态与工单文件
# ---------------------------------------------------------------------------


def _git(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, check=False)


def code_version(root: Path) -> str:
    try:
        head = _git(root, "rev-parse", "HEAD").stdout.strip() or "未知"
        dirty = [ln[3:] for ln in _git(root, "status", "--porcelain=v1", "--untracked-files=no").stdout.splitlines() if ln.strip()]
    except OSError:
        return "未知（git 不可用）"
    if not dirty:
        return head
    shown = dirty[:TICKET_DIRTY_LIST_MAX]
    more = f" 另有 {len(dirty) - len(shown)} 个" if len(dirty) > len(shown) else ""
    return f"{head}（有未提交改动：{' '.join(shown)}{more}）"


def require_ignored(root: Path, target: Path) -> None:
    """运行时第二道闸：目标路径必须被 .gitignore 覆盖（公开仓库，工单含输出原文与本机路径）。"""
    try:
        rc = _git(root, "check-ignore", "-q", str(target)).returncode
    except OSError:
        rc = -1
    if rc != 0:
        raise TicketError("tickets/ 没被 gitignore 覆盖（或 git 不可用），拒绝写入（公开仓库）")


def tickets_dir(root: Path | None = None) -> Path:
    return Path(root or paths.ROOT) / TICKETS_DIR


@contextlib.contextmanager
def _locked(directory: Path) -> Iterator[None]:
    fd = os.open(str(directory / ".lock"), os.O_RDWR | os.O_CREAT, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


@dataclass(frozen=True)
class TicketInfo:
    id: str
    path: Path
    status: str
    episode: str
    blocked: str
    key: str
    commit: str
    resume: str
    repros: int


def _section(text: str, title: str) -> str:
    m = re.search(rf"^## {re.escape(title)}[^\n]*\n(.*?)(?=^## |\Z)", text, re.M | re.S)
    return m.group(1) if m else ""


def parse_ticket(path: Path) -> TicketInfo | None:
    """只解析系统字段：头部 `状态 / 期 / 阻塞 / 去重键`、处理结果段的 `状态 / 提交 / 续跑`。解析不了返回 None。"""
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    head = text.split("\n## ", 1)[0]

    def field(block: str, key: str) -> str | None:
        m = re.search(rf"^{key}:[ \t]*(.*)$", block, re.M)
        return m.group(1).strip() if m else None

    status = field(head, "状态")
    episode = field(head, "期")
    blocked = field(head, "阻塞")
    key = field(head, "去重键")
    if status not in STATUSES or not episode or blocked not in BLOCKED_STEPS or key is None:
        return None
    result = _section(text, "处理结果")
    done = field(result, "状态")
    if done in STATUSES[1:]:
        status = done   # coding agent 在「处理结果」写的结论优先于头部
    commit = field(result, "提交") or ""
    resume = (field(result, "续跑") or "").splitlines()[0] if field(result, "续跑") else ""
    return TicketInfo(
        id=path.stem, path=path, status=status, episode=episode, blocked=blocked, key=key,
        commit=commit if _COMMIT.fullmatch(commit) else "", resume=resume,
        repros=len(re.findall(r"^### 复现 ", _section(text, "复现记录"), re.M)),
    )


def _ticket_order(path: Path) -> tuple[str, int, str]:
    """开单先后：工单号里的 UTC 时间，同秒再按 `-2`、`-3` 后缀。文件名字典序不行：`…-2.md` 排在 `….md` 前（'-' < '.'）。"""
    m = _TICKET_ID.fullmatch(path.stem)
    return (path.stem[2:18], int(m.group(1)[1:]) if m and m.group(1) else 1, path.stem)


def all_tickets(root: Path | None = None) -> tuple[list[TicketInfo], int]:
    """(可解析的工单, 解析失败份数)，按开单先后排（同签名取「上一次」的结论要用最后一张）。"""
    d = tickets_dir(root)
    if not d.is_dir():
        return [], 0
    out: list[TicketInfo] = []
    bad = 0
    for path in sorted(d.glob("T-*.md"), key=_ticket_order):
        info = parse_ticket(path)
        if info is None:
            bad += 1
        else:
            out.append(info)
    return out, bad


def _scene_block(scene: Scene, *, when: str, sid: str, version: str, episode: str, root: Path) -> list[str]:
    s = lambda t: scrub(t, root)  # noqa: E731
    fence = lambda body: ["  ```", *[f"  {ln}" for ln in (s(body).splitlines() or [""])], "  ```"]  # noqa: E731
    return [
        f"- 时间: {when}", f"- 会话: {sid}", f"- 代码版本: {s(version)}", f"- 期: {episode}",
        "- 命令:", *fence(scene.command), f"- 退出码: {scene.returncode}",
        "- stderr 尾部:", *fence(scene.stderr_tail), "- stdout 尾部:", *fence(scene.stdout_tail),
        *([f"- 消息: {s(scene.message)}"] if scene.message else []),
    ]


def render(ticket_id: str, *, episode: str, blocked: str, key: str, scene: Scene, fields: dict[str, str],
           when: str, version: str, first_line: str | None, root: Path) -> str:
    lines = [f"# 工单 {ticket_id}", ""]
    if first_line:
        lines += [f"> {first_line}", ""]
    lines += [f"状态: 待修", f"期: {episode}", f"阻塞: {blocked}", f"去重键: {key}", "",
              "## 现场（系统采集，勿改）",
              *_scene_block(scene, when=when, sid=scene.sid, version=version, episode=episode, root=root), "",
              "## 模型填写"]
    for name, title in FIELDS:
        lines += [f"### {title}", scrub(fields[name], root), ""]
    lines += ["## 复现记录", "", "## 处理结果（coding agent 填）", "状态: 已修 | 不是bug | 不修", "提交:", "续跑:", ""]
    return "\n".join(lines)


def _repro_block(scene: Scene, *, when: str, version: str, episode: str, root: Path, n: int) -> str:
    return "\n".join([f"### 复现 {n}", *_scene_block(scene, when=when, sid=scene.sid, version=version, episode=episode, root=root), ""])


# ---------------------------------------------------------------------------
# 开单规划（审查层的卡面与执行层共用）
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class OpenPlan:
    episode: str
    blocked: str
    key: str
    scene: Scene
    fields: dict[str, str]
    append_to: TicketInfo | None
    first_line: str | None
    warn: str | None


def episode_key_of(ep_dir: Path, root: Path | None = None) -> str:
    from pipeline.project import episode_key

    parts = episode_key(ep_dir, root)
    if parts is None:
        raise TicketError(f"不在 data/episodes 下：{ep_dir}")
    return "/".join(parts)


def _parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="python -m pipeline.tickets", allow_abbrev=False)
    sub = ap.add_subparsers(dest="cmd", required=True)
    op = sub.add_parser("open", allow_abbrev=False)
    op.add_argument("ep")
    op.add_argument("--call", required=True)
    op.add_argument("--blocked", required=True)
    for name, _title in FIELDS:
        op.add_argument(f"--{name}", required=True)
    ls = sub.add_parser("list", allow_abbrev=False)
    ls.add_argument("ep", nargs="?")
    ls.add_argument("--json", action="store_true")
    sh = sub.add_parser("show", allow_abbrev=False)
    sh.add_argument("ticket")
    return ap


class _ParseFail(Exception):
    pass


def _parse(argv: list[str]) -> argparse.Namespace:
    ap = _parser()
    ap.exit = lambda status=0, message=None: (_ for _ in ()).throw(_ParseFail(message or "参数不合规"))  # type: ignore[method-assign]
    ap.error = lambda message: (_ for _ in ()).throw(_ParseFail(message))  # type: ignore[method-assign]
    for sp in ap._subparsers._group_actions[0].choices.values():  # type: ignore[union-attr]
        sp.exit = ap.exit  # type: ignore[method-assign]
        sp.error = ap.error  # type: ignore[method-assign]
    return ap.parse_args(argv)


def plan_open(ns: argparse.Namespace, root: Path, *, bound_dir: Path | None = None) -> OpenPlan:
    from pipeline.project import classify_dir

    ep_dir = Path(ns.ep)
    if bound_dir is not None and Path(ns.ep).resolve() != Path(bound_dir).resolve():
        raise TicketError("开单的期目录必须是本会话绑定的目录（模型不能指定别的期）")
    kind = classify_dir(ep_dir, root)
    if kind is None:
        raise TicketError(f"不是期 / 卷 / 企划目录：{ep_dir}")
    if ns.blocked not in BLOCKED_STEPS:
        raise TicketError(f"--blocked 只能是 {' '.join(BLOCKED_STEPS)}")
    if (ns.blocked == "总装") != (kind == "project"):
        raise TicketError("--blocked 总装 只在企划会话里有效；企划会话只能报总装")
    fields = {name: str(getattr(ns, name)) for name, _t in FIELDS}
    for name, title in FIELDS:
        if not fields[name].strip():
            raise TicketError(f"--{name}（{title}）不能为空")
        if len(fields[name]) > TICKET_FIELD_MAX_CHARS:
            raise TicketError(f"--{name}（{title}）超过 {TICKET_FIELD_MAX_CHARS} 字：输出原文系统已采集，不用再贴")
    if len(fields["why"].strip()) < TICKET_WHY_MIN_CHARS:
        raise TicketError(f"--why 至少 {TICKET_WHY_MIN_CHARS} 字：写清依据（同一参数在别处能跑、这里报什么、输入格式已核对）")
    scene = collect(ep_dir, ns.call)
    # 去重键在脱敏之后算（红队 R-13）：它原样写进头部与卡面
    key = scrub(f"{normalized_command(scene, ep_dir)} ⟂ {error_line(scene)}", root)
    tickets, _bad = all_tickets(root)
    same = [t for t in tickets if t.key == key]
    pending = next((t for t in same if t.status == "待修"), None)
    first = None
    if pending is None and same:
        last = same[-1]
        first = (f"与已修工单 {last.id} 同签名，可能是回归" if last.status == "已修"
                 else f"与 {last.id}（结论：{last.status}）同签名，先看上次的结论")
    warn = None
    if scene.returncode == 2 and "usage:" in (scene.stderr_tail + scene.stdout_tail):
        warn = "⚠ 退出码 2 且输出含 usage：多半是参数用法错误，请看清『为什么不是用法错误』再批"
    return OpenPlan(episode=episode_key_of(ep_dir, root), blocked=ns.blocked, key=key, scene=scene, fields=fields,
                    append_to=pending, first_line=first, warn=warn)


def preview(argv: list[str], *, bound_dir: Path | None = None, root: Path | None = None) -> tuple[bool, list[str]]:
    """审查层用（D51 / D60 的 command_preview 先例）：`open` 成立 → (True, 卡面行)；不成立 → (False, 拒因)。"""
    base = Path(root or paths.ROOT)
    try:
        ns = _parse(argv)
    except _ParseFail as exc:
        return False, [f"tickets 参数不合规：{exc}"]
    if ns.cmd != "open":
        return True, []
    try:
        plan = plan_open(ns, base, bound_dir=bound_dir)
    except TicketError as exc:
        return False, [f"开不了单：{exc}"]
    now = _now()
    lines: list[str] = []
    if plan.warn:
        lines.append(plan.warn)
    if plan.append_to is not None:
        lines.append(f"将追加到 {plan.append_to.id}（同命令同报错末行、仍待修）——两次的报错尾部并排：")
        old = plan.append_to.path.read_text(encoding="utf-8")
        old_err = _section(old, "现场（系统采集，勿改）")
        lines += ["── 原单现场 ──", *old_err.strip().splitlines(), "── 这一次 ──",
                  *_scene_block(plan.scene, when=now, sid=plan.scene.sid, version=code_version(base),
                                episode=plan.episode, root=base)]
    else:
        lines.append("将新开一张工单：")
        lines += render("T-（批准后生成）", episode=plan.episode, blocked=plan.blocked, key=plan.key, scene=plan.scene,
                        fields=plan.fields, when=now, version=code_version(base), first_line=plan.first_line,
                        root=base).splitlines()
    return True, lines


def _stamp() -> str:
    """工单号里的 UTC 秒级时间（文件名不含期名：期名可能带任意字符）。"""
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def do_open(ns: argparse.Namespace, root: Path) -> dict[str, Any]:
    plan = plan_open(ns, root)
    d = tickets_dir(root)
    require_ignored(root, d / "T-x.md")
    d.mkdir(exist_ok=True)   # 只建这一层，且在 check-ignore 通过之后
    when = _now()
    version = code_version(root)
    with _locked(d):
        # 锁内重新查重（两个进程同时开同签名：只有一个新文件）
        tickets, _bad = all_tickets(root)
        same = [t for t in tickets if t.key == plan.key]
        pending = next((t for t in same if t.status == "待修"), None)
        if pending is not None:
            n = pending.repros + 1
            text = pending.path.read_text(encoding="utf-8")
            block = _repro_block(plan.scene, when=when, version=version, episode=plan.episode, root=root, n=n)
            marker = "## 处理结果"
            at = text.index(marker) if marker in text else len(text)
            paths.atomic_write(pending.path, text[:at] + block + "\n" + text[at:])
            return {"id": pending.id, "path": str(pending.path), "appended": True}
        base_id = f"T-{_stamp()}-{hashlib.sha256(plan.key.encode('utf-8')).hexdigest()[:6]}"
        ticket_id, n = base_id, 1
        while (d / f"{ticket_id}.md").exists():
            n += 1
            ticket_id = f"{base_id}-{n}"
        target = d / f"{ticket_id}.md"
        require_ignored(root, target)
        paths.atomic_write(target, render(ticket_id, episode=plan.episode, blocked=plan.blocked, key=plan.key,
                                          scene=plan.scene, fields=plan.fields, when=when, version=version,
                                          first_line=plan.first_line, root=root))
        return {"id": ticket_id, "path": str(target), "appended": False}


# ---------------------------------------------------------------------------
# 闭环提醒（§4.8）
# ---------------------------------------------------------------------------


def _step_index(step: str) -> int:
    return BLOCKED_STEPS.index(step) if step in BLOCKED_STEPS else -1


def current_step_key(current_step: str) -> str | None:
    """inspect_episode 的 current_step → 工序键（assembly.STEP_KEY_MAP 同一套；03.5 与 04 合成 03.5）。"""
    from pipeline.agent.assembly import step_key_of

    key = step_key_of(current_step)
    return key if key in BLOCKED_STEPS else None


def find_fixed(ep_dir: Path, root: Path | None = None) -> tuple[list[TicketInfo], int]:
    """本期 / 本企划「已修」且还没走过被阻塞工序的工单 (工单, 解析失败份数)。"""
    from pipeline import project as project_mod

    base = Path(root or paths.ROOT)
    tickets, bad = all_tickets(base)
    try:
        key = episode_key_of(ep_dir, base)
    except TicketError:
        return [], bad
    out: list[TicketInfo] = []
    kind = project_mod.classify_dir(ep_dir, base)
    for t in tickets:
        if t.episode != key or t.status != "已修":
            continue
        if t.blocked == "总装":
            if kind != "project":
                continue
            doc = project_mod.load(Path(ep_dir))
            coll = doc.collection if isinstance(doc, project_mod.ProjectDoc) else None
            ledger = Path(ep_dir) / coll / "04-assembly.json" if coll else None
            if ledger is not None and ledger.exists() and ledger.stat().st_mtime >= t.path.stat().st_mtime:
                continue
            out.append(t)
            continue
        from pipeline.status import inspect_episode

        cur = current_step_key(inspect_episode(Path(ep_dir)).current_step)
        if cur is None or _step_index(cur) <= _step_index(t.blocked):
            out.append(t)
    return out, bad


def resume_line(t: TicketInfo, root: Path | None = None) -> str:
    """`续跑:` 行（coding agent 写的）：单行、过脱敏、超 TICKET_RESUME_MAX_CHARS 截断并标「…」（提醒与 list / show 同一规则）。"""
    resume = scrub(t.resume, root)
    return resume[:TICKET_RESUME_MAX_CHARS] + "…" if len(resume) > TICKET_RESUME_MAX_CHARS else resume


def notice_text(t: TicketInfo, root: Path | None = None) -> str:
    resume = resume_line(t, root)
    return (f"工单 {t.id} 已修（提交 {t.commit or '未填'}），从 {t.blocked} 重跑"
            f"{f'：{resume}' if resume else '（续跑：未填）'}")


# ---------------------------------------------------------------------------
# 命令行
# ---------------------------------------------------------------------------


def _summary(t: TicketInfo, root: Path | None = None) -> dict[str, Any]:
    return {"id": t.id, "status": t.status, "episode": t.episode, "blocked": t.blocked, "key": t.key,
            "commit": t.commit or "未填", "resume": resume_line(t, root) or "未填", "repros": t.repros}


def main(argv: list[str] | None = None, *, root: Path | None = None) -> int:
    base = Path(root or paths.ROOT)
    try:
        ns = _parse(list(sys.argv[1:] if argv is None else argv))
    except _ParseFail as exc:
        print(f"[tickets] 参数不合规：{exc}", file=sys.stderr)
        return 2
    try:
        if ns.cmd == "open":
            res = do_open(ns, base)
            t = parse_ticket(Path(res["path"]))
            what = f"追加到 {res['id']}" if res["appended"] else f"已开工单 {res['id']}"
            print(f"[OK] {what} → {TICKETS_DIR}/{Path(res['path']).name}")
            if t is not None:
                print(f"     去重键: {t.key}")
            print(JSON_MARK + json.dumps(res, ensure_ascii=False))
            return 0
        if ns.cmd == "list":
            tickets, bad = all_tickets(base)
            if ns.ep:
                key = episode_key_of(Path(ns.ep), base)
                tickets = [t for t in tickets if t.episode == key]
            rows = [_summary(t, base) for t in tickets]
            if ns.json:
                print(json.dumps({"tickets": rows, "unparsed": bad}, ensure_ascii=False))
            else:
                for r in rows:
                    print(f"{r['id']} | {r['status']} | {r['episode']} | 阻塞 {r['blocked']} | 复现 {r['repros']} | "
                          f"提交 {r['commit']} | 续跑 {r['resume']}")
                if bad:
                    print(f"另有 {bad} 份工单解析失败")
            return 0
        if not _TICKET_ID.fullmatch(ns.ticket):
            print(f"[tickets] 工单号形状不对：{ns.ticket}", file=sys.stderr)
            return 2
        t = parse_ticket(tickets_dir(base) / f"{ns.ticket}.md")
        if t is None:
            print(f"[tickets] 没有这张工单或解析失败：{ns.ticket}", file=sys.stderr)
            return 1
        print(json.dumps(_summary(t, base), ensure_ascii=False))
        return 0
    except TicketError as exc:
        print(f"[tickets] {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
