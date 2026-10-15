"""会话内核（Spec 9 §2、§4.3）：人机 I/O 与回合编排的分界线。

内核**不做 I/O**——打印走 `HumanChannel.show`，问人走 `HumanChannel.ask`。终端接
`TtyChannel`（cli.py），桌面端接 `ProtocolChannel`（protocol.py，PR3）。人的答复只从这两条线
进来，模型碰不到（§2.4.2）。

## 与 spec 措辞的一处实现选择（如实记账）

§2.2 把中断写成「回合内安装信号处理器」。这里**不装处理器**：Python 默认的 SIGINT →
主线程 `KeyboardInterrupt` 已经是「0.5 s 内浮出」的那条路径（E2 实测），而延迟区靠
`TurnInterrupt.defer()` 捕获 `KeyboardInterrupt` 并在退出时重抛实现。语义与 §2.2 的状态表
逐条对应（执行中 / 停止中 / 收尾中 / 收尾后），代码更少，行为对终端 Ctrl-C 与
`pthread_kill` 两种入口一致。

## 子会话

`AgentSession` 由 `SessionHost` 持有：主会话 `persist=True`（落 `session.jsonl`），
子会话 `persist=False`（独立 messages、退出即丢），但共享进程级的期租约、中断控制与检查点配置。
idea 会话也是主会话（Spec 18 §3.1）：落库级 `data/_idea/session.jsonl`——`SessionHost` 的
「日志目录」与「期目录」解耦，idea 的 `ep_dir` 恒为 `None`（工具语义照旧无期），日志目录是 `_idea`。
"""

from __future__ import annotations

import contextlib
import copy
import hashlib
import json
import os
import re
import secrets
import signal
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterator, Literal, Protocol

from pipeline import paths
from pipeline.agent import llm as llm_module  # 晚绑定：测试打桩 pipeline.agent.llm.run_tool_loop 要生效
from pipeline.agent import model_switch
from pipeline.agent.llm import Decision, LoopControl, dedup_key
from pipeline.agent.session_log import (
    SCHEMA as SCHEMA_NAME,
    EpisodeLease,
    SessionLocked,
    SessionLogBroken,
    list_sessions,
    last_context_reading,
    load_session,
    plan_repairs,
    rebuild_messages,
    resume_target,
)

REQUEST_ID_BYTES = 16

# 临界区工具（§2.2 第 3 条）：显式字面量，TG-5 比对它与「side_effect 为真的工具 − run_pipeline」。
# Spec 12：cover_edit 不标 side_effect（fail-closed 弹卡）→ 必须同步进临界区。ADR-0028：write_note 同理。
CRITICAL_TOOLS = frozenset(
    {"write_episode_file", "acquire_propose", "write_memory", "browser", "cover_edit", "write_note"}
)

# ADR-0030 决策 5：卡片答复「本会话都批准」——批准本次，并让本会话进程内同类（同 allow_key）此后免卡
SESSION_ALLOW_DECISION = "approve_session"

# 终端卡片的末行提示（§3.2 的两种文案 + 工具卡的既有文案）
_PROMPTS = {
    "tool_call": "└─ 执行? [y/N]: ",
    "memory_ack": "└─ 执行? [y/N]: ",
    "fetch": "└─ 抓取? [y/N]: ",
    "checkpoint": "└─ 继续? [y/N]: ",
}


def prompt_for(kind: str) -> str:
    return _PROMPTS.get(kind, _PROMPTS["tool_call"])


@dataclass(frozen=True)
class HumanRequest:
    """人审请求（§3.2）。`request_id` 是答复的唯一凭据，`secrets.token_hex(16)`，不进 messages。"""

    request_id: str
    kind: Literal["tool_call", "fetch", "checkpoint", "memory_ack"]
    turn_id: str | None
    title: str
    card_text: str
    fields: dict[str, Any]
    options: tuple[str, ...]
    feedback_allowed: bool
    # D82 Q5（人选 K1）：卡面摘要。给了的话渲染端卡面显示它、card_text 收进「工单全文」折叠；终端通道照旧显示 card_text
    card_summary: str | None = None


@dataclass(frozen=True)
class HumanAnswer:
    request_id: str
    decision: str
    feedback: str | None
    channel: Literal["tty", "protocol"]
    latency_s: float


class HumanChannel(Protocol):
    name: Literal["tty", "protocol"]

    def show(self, kind: str, payload: dict[str, Any]) -> None: ...

    def ask(self, request: HumanRequest) -> HumanAnswer: ...


def new_request_id() -> str:
    return secrets.token_hex(REQUEST_ID_BYTES)


_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f-\x9f]")


def _clean(text: Any) -> str:
    return _CONTROL_CHARS.sub("", str(text if text is not None else ""))


class TurnInterrupt:
    """回合级中断控制（§2.2）。深度与待处理中断**只属于主线程**（🟡-C）。"""

    def __init__(self) -> None:
        self.wrapup_started = False
        # SIGTERM 语义（§2.2 第 5 条）：中断 + **跳过收尾** + turn_end{wrapup:"skipped"}
        self.skip_wrapup = False
        self._main = threading.get_ident()
        self._depth = 0
        self._pending = 0
        self._absorb = 0
        self._dropped = 0
        self._handling = False

    # ---- 回合状态 ----

    @contextlib.contextmanager
    def installed(self) -> Iterator[None]:
        """回合边界：复位状态。空闲态不装任何东西，Ctrl-C 行为与现状一致。"""
        self.wrapup_started = False
        self.skip_wrapup = False
        self._depth = self._pending = self._absorb = self._dropped = 0
        self._handling = False
        try:
            yield
        finally:
            self._depth = self._pending = self._absorb = 0

    @contextlib.contextmanager
    def absorb(self) -> Iterator[None]:
        """「停止中」/「收尾后」：此后到达的中断只置标志，不抛（回合结束时丢弃）。"""
        self._absorb += 1
        try:
            yield
        finally:
            self._absorb -= 1

    @property
    def dropped(self) -> int:
        return self._dropped

    def clear_dropped(self) -> int:
        count, self._dropped = self._dropped, 0
        return count

    # ---- 延迟区 ----

    def _is_main(self) -> bool:
        return threading.get_ident() == self._main

    @contextlib.contextmanager
    def defer(self) -> Iterator[None]:
        """延迟区（§2.2 第 3 条）：区内到达的中断等退出后再抛；非主线程调用是空操作。"""
        if not self._is_main():
            yield
            return
        self._depth += 1
        try:
            yield
        except KeyboardInterrupt:
            self._pending += 1
        finally:
            self._depth -= 1
            if self._depth == 0 and self._pending:
                pending, self._pending = self._pending, 0
                if self._absorb:
                    self._dropped += pending
                else:
                    raise KeyboardInterrupt

    @contextlib.contextmanager
    def absorbed(self) -> Iterator[None]:
        """「收尾后」区（§2.2 状态表）：区内到达的中断只置标志、不抛，回合结束时丢弃并发 notice。

        顺序不能颠倒：`defer()` 退出时才查 `_absorb`，外层的 `absorb()` 必须先于它退出。
        """
        with self.absorb():
            with self.defer():
                yield

    def request(self) -> None:
        """协议读者线程调用：把中断打到主线程（`pthread_kill`，E2 实测 0.5 s 内浮出）。"""
        signal.pthread_kill(self._main, signal.SIGINT)


class FetchHookInterrupted(KeyboardInterrupt):
    """抓取钩子里浮出的中断（§2.2 落点表「等人答复」「抓取 job 中」两行）：整轮停，
    但 `acquire_propose` 的结果照常，带上已答/作废的抓取记录（`outcome`）。

    是 KeyboardInterrupt 的子类：不认识它的上层照旧按中断处理。
    """

    def __init__(self, outcome: dict[str, Any]) -> None:
        super().__init__()
        self.outcome = outcome


def _proposed_urls(args: dict[str, Any]) -> list[str]:
    """`acquire_propose` 入参里的 URL：按输入顺序去重，与 `propose_candidates` 同样 strip。"""
    urls: list[str] = []
    for item in args.get("candidates") or []:
        url = item.get("url") if isinstance(item, dict) else None
        if isinstance(url, str) and url.strip() and url.strip() not in urls:
            urls.append(url.strip())
    return urls


class _FetchStopped(Exception):
    """`_ask_fetch` → `_fetch_cards` 的内部信号：本卡已按中断记账，余卡不再出。"""

    def __init__(self, record: dict[str, Any]) -> None:
        super().__init__()
        self.record = record


@dataclass(frozen=True)
class ToolVerdict:
    """确定性工具审查结果（§4.3）：不打印、不提问，只给判断。"""

    action: Literal["reject", "allow", "ask"]
    reason: str = ""
    echo: str | None = None
    request: HumanRequest | None = None
    # ADR-0030 决策 5：「本会话都批准」的同类键（工具 + 危险标记组合）；None = 该卡不开放此选项
    allow_key: str | None = None
    # D81 §4.4 卡上绑定：弹卡时写入卡面所依据的状态（00-project.md 的 base_sha）；只在人批那一支带进 Decision
    bind: dict | None = None


# ADR-0030 决策 3/4（D78）：可回退的写入与抓取类工具免卡。回退靠写前留底（草稿 / 定稿 / 笔记 /
# 其余期文件）与记忆日志的 before/after；browser 用独立 profile。未登记的新工具照旧按 side_effect fail-closed
AUTO_APPROVED_TOOLS = frozenset(
    {"write_episode_file", "write_note", "write_memory", "cover_edit", "acquire_propose", "browser"}
)
# ADR-0030 决策 2：run_pipeline 只有这些 cloud 子命令弹卡——开机 / 跑任务花钱，down / 清理销毁云端东西
CLOUD_CARD_SUBCOMMANDS = frozenset({"up", "run", "down", "clean-frames", "relocate-data"})
# AGENTS.md Code Freeze 的三条例外（读音表 / 标定值 / 曲库登记）写 config/，规定「人审卡批准后」才写：
# 与 bash「写 config/ 算硬危险」同一口径，逐条弹卡。D82：它是 tools.HUMAN_GATED_COMMANDS 的子集（审查走后者）
CONFIG_WRITE_COMMANDS = frozenset({("corrections", "global"), ("calibration", "set"), ("bgm", "register")})


def review_tool_call(
    name: str,
    args: dict[str, Any],
    *,
    ep_dir: Path | None,
    scope: str,
    status: Any = None,
    root: Path | None = None,
    origin: str = "model",
    turn_id: str | None = None,
    draft_writes_this_turn: int = 0,
    session_kind: str = "episode",
    project_dir: Path | None = None,
) -> ToolVerdict:
    """工具审查（原 `cli._default_approve` 的确定性部分，Spec 9 §2.4.2/§2.6）。

    顺序（D43 / Spec 17 §3.2/§3.4）：未注册 → 无期会话的需期工具「先建期」→
    run_pipeline dry-run → write_memory dry-run → 参数级免卡（只读 pipeline 模块、
    限次的草稿写入，2026-10-08 spec）→ fail-closed `side_effect` 分流 → 弹卡。
    「越 scope」拒绝已随 D43 删除（所有模式开放全部工具）。

    `draft_writes_this_turn`：本轮已放行的 02-script.draft.md 写入次数，由 Session 计数传入。
    `session_kind` / `project_dir`（D81）：宿主绑定的会话类型与所属企划；企划会话的需期工具逐个定口径（§4.2）。
    """
    from pipeline.agent.tools import (
        DRAFT_FILENAME,
        DRAFT_FREE_WRITES_PER_TURN,
        NEEDS_EPISODE_TOOLS,
        NO_EPISODE_MESSAGE,
        PROJECT_FILE,
        PROJECT_VOLUME_ONLY_MESSAGE,
        PROJECT_VOLUME_ONLY_TOOLS,
        READONLY_PIPELINE_MODULES,
        TOOL_SCHEMAS,
        crosscheck_writes,
        memory_store_for,
        plan_project_write,
        project_module_refusal,
        project_write_preview,
        resolve_write_content,
        run_pipeline,
        script_write_refusal,
    )

    if name not in TOOL_SCHEMAS:
        return ToolVerdict("reject", reason=f"未注册的工具 '{name}'（工具清单不现场发明）")

    # D43 / Spec 17 §3.4：无期会话（idea）调需期工具，在弹卡之前统一报「先建期」。
    # 位置写死（D43-R2 🔵 R2-6）：排在未注册检查之后、一切 dry-run 之前——否则一条本身
    # 非法的命令（如 faces unknown）拿到的是白名单拒因，不是统一文案。
    if ep_dir is None and name in NEEDS_EPISODE_TOOLS:
        return ToolVerdict("reject", reason=NO_EPISODE_MESSAGE)
    # D81 §4.2（红队 R-3）：企划会话的期目录绑定是企划目录，卷级操作不落到默认分支（封面 / 抓取提案不写进母目录）
    if session_kind == "project" and name in PROJECT_VOLUME_ONLY_TOOLS:
        return ToolVerdict("reject", reason=PROJECT_VOLUME_ONLY_MESSAGE.format(tool=name))

    argv: list[str] | None = None
    if name == "run_pipeline":
        cmd_str = str(args.get("command", ""))
        outcome = run_pipeline(cmd_str, episode_dir=ep_dir, scope=scope, confirmed=False, project_dir=project_dir)
        if not outcome["ok"]:
            # 先走既有的白名单/dry-run 拒因（--force 全量覆盖等），文案不许被下面的模型规则截走
            return ToolVerdict("reject", reason=outcome["message"])
        argv = list(outcome["argv"])
        # D81 §4.2（Q2）：企划会话只跑企划级模块；企划级专属模块只在企划会话里跑
        refusal = project_module_refusal(argv, session_kind, project_dir)
        if refusal:
            return ToolVerdict("reject", reason=refusal)
        if origin == "model" and _model_argv_has_options(argv, ep_dir):
            # §2.4.3（🔴-1）：模型发起的 review 只能生成审片页，任何以 `-` 开头的 token 一律拒。
            # 前缀缩写（--a/--ap/--appr）与 `pipeline.review` 写法一并在规范化 argv 上拦。
            return ToolVerdict(
                "reject",
                reason=(
                    f"模型只能生成审片页（review <期>），不得携带任何选项：{cmd_str}。"
                    "停机点 05 的批准只能由人经 /approve 或桌面端决策条完成（ADR-0020 §3）"
                ),
            )

    memory_plan = None
    if name == "write_memory":
        from pipeline.agent import memory

        try:
            # D81 §4.5：target=project 写企划记忆（企划目录取自宿主绑定）；去掉 target 再交给记忆层
            store, mem_args = memory_store_for(args, session_kind, project_dir, root)
            memory_plan = memory.plan_op(
                str(mem_args.get("op", "")), mem_args, root=root, episode_dir=ep_dir, store=store
            )
        except (ValueError, PermissionError, OSError) as exc:
            return ToolVerdict("reject", reason=f"{type(exc).__name__}: {exc}")
        if not memory_plan.requires_card:
            return ToolVerdict("allow", echo=f"[memory] {memory_plan.summary}")

    # 参数级免卡（2026-10-08 spec §A）。只认规范化 argv 里的模块名，不认模型写的原始串。
    if argv is not None and _pipeline_module_of(argv) in READONLY_PIPELINE_MODULES:
        return ToolVerdict("allow", echo=_echo_line(name, args))
    if argv is not None and _readonly_asset_subcommand(argv):
        return ToolVerdict("allow", echo=_echo_line(name, args))
    # D81 §4.8：crosscheck 不带 --write 才算只读（整模块不进只读集合：--write 会落盘）
    if argv is not None and _pipeline_module_of(argv) == "crosscheck" and not crosscheck_writes(argv):
        return ToolVerdict("allow", echo=_echo_line(name, args))

    # D51：读音纠错录入。check 只读、免卡；add / global 在弹卡前跑同一组校验，不成立不弹卡，
    # 成立则把「写什么、读音核对、本期受影响段」放上卡面
    command_preview: list[str] | None = None
    card_summary: list[str] | None = None
    if argv is not None and _pipeline_module_of(argv) == "corrections":
        from pipeline import corrections

        rest = argv[argv.index("-m") + 2:]
        if rest[:1] == ["check"]:
            return ToolVerdict("allow", echo=_echo_line(name, args))
        ok, command_preview = corrections.preview(rest)
        if not ok:
            return ToolVerdict("reject", reason="\n".join(command_preview))

    # D60：标定值写入。键不在白名单 / 越界 / 实录太短在弹卡前拒；成立则把旧值 → 新值、实录与影响面放上卡面
    if argv is not None and _pipeline_module_of(argv) == "calibration":
        from pipeline import calibration

        ok, calib_preview = calibration.preview(argv[argv.index("-m") + 2:])
        if not ok:
            return ToolVerdict("reject", reason="\n".join(calib_preview))
        command_preview = calib_preview

    # D82：开工单。现场取不到 / 参数不合规 / 期目录不是本会话绑定的目录 → 弹卡前拒；成立则卡面是工单全文
    if argv is not None and _pipeline_module_of(argv) == "tickets":
        from pipeline import tickets

        rest = argv[argv.index("-m") + 2:]
        if rest[:1] == ["open"]:
            ok, ticket_preview = tickets.preview(rest, bound_dir=ep_dir, root=root)
            if not ok:
                return ToolVerdict("reject", reason="\n".join(ticket_preview))
            command_preview = ticket_preview
            card_summary = tickets.preview_summary(rest, bound_dir=ep_dir, root=root)

    # D81 §10.3 ⑭：总装。卷表错、缺部件、已有成片未带 --replace、参数不成立 → 弹卡前拒；成立则卡面是部件与截断点
    if argv is not None and _pipeline_module_of(argv) == "assemble":
        from pipeline import assemble

        ok, assemble_preview = assemble.preview(argv[argv.index("-m") + 2:])
        if not ok:
            return ToolVerdict("reject", reason="\n".join(assemble_preview))
        command_preview = assemble_preview

    # D61 / ADR-0028：write_note 的番名 / 目标 / 新建前提与 edits 成立性，在弹卡前拒
    if name == "write_note":
        from pipeline.agent.tools import note_write_refusal, resolve_note_content

        refusal = note_write_refusal(args, root)
        if refusal:
            return ToolVerdict("reject", reason=refusal)
        try:
            resolve_note_content(args, root)
        except (ValueError, PermissionError) as exc:
            return ToolVerdict("reject", reason=f"{type(exc).__name__}: {exc}")

    loop_label = None
    if name == "write_episode_file" and session_kind == "project":
        # D81 §4.4（Q4）：企划会话只写 00-project.md。写后解析不了 → 弹卡前拒；机读字段（企划名 / 合集 / 卷表）
        # 有变 → 弹卡带 diff、不开放「本会话都批准」，卡上绑定盘上 base_sha；只改散文 → 免卡（写前留底）
        try:
            plan = plan_project_write(project_dir or ep_dir, args, root)  # type: ignore[arg-type]
        except (ValueError, PermissionError, OSError) as exc:
            return ToolVerdict("reject", reason=f"{type(exc).__name__}: {exc}")
        if not plan.machine_diff:
            return ToolVerdict("allow", echo=_echo_line(name, args))
        return ToolVerdict(
            "ask",
            request=_tool_request(
                name, args, None, ep_dir, status, None, turn_id, command_preview=project_write_preview(plan),
            ),
            bind={"base_sha": plan.base_sha},
        )
    if name == "write_episode_file" and str(args.get("filename", "")).strip() == PROJECT_FILE:
        return ToolVerdict(
            "reject", reason=f"{PROJECT_FILE} 只在企划会话里写（卷会话的写域是本卷目录，企划级改动回企划会话做）",
        )
    if name == "write_episode_file":
        # D47：定稿只能改不能新建；定稿存在后草稿冻结。在弹卡之前拒，人不必为必然失败的写入点卡
        refusal = script_write_refusal(ep_dir, str(args.get("filename", "")))
        if refusal:
            return ToolVerdict("reject", reason=refusal)
        # D48：content / edits 不成立（edits 找不到或不唯一）在弹卡前拒，人不必为必然失败的写入点卡
        try:
            resolve_write_content(ep_dir, args)
        except (ValueError, PermissionError) as exc:
            return ToolVerdict("reject", reason=f"{type(exc).__name__}: {exc}")
    if name == "write_episode_file" and str(args.get("filename", "")) == DRAFT_FILENAME:
        if draft_writes_this_turn < DRAFT_FREE_WRITES_PER_TURN:
            return ToolVerdict("allow", echo=_echo_line(name, args))
        # 超限恢复弹卡：这是防打转的刹车，卡上写明第几次，人一眼看出它在原地转（ADR-0030 保留）
        loop_label = f"本轮第 {draft_writes_this_turn + 1} 次重写草稿"
        return ToolVerdict(
            "ask",
            request=_tool_request(
                name, args, argv, ep_dir, status, memory_plan, turn_id, loop_label=loop_label,
                command_preview=command_preview,
            ),
        )

    # ADR-0030 决策 1（取代 ADR-0029 决策 4）：bash 只在命中硬危险时弹卡，其余免卡
    allow_key: str | None = None
    if name == "bash":
        from pipeline.agent.status_card import bash_danger_tags

        tags = bash_danger_tags(str(args.get("command", "")))
        if not tags:
            return ToolVerdict("allow", echo=_echo_line(name, args))
        allow_key = "bash:" + "".join(sorted(set(tags)))
    elif name == "run_pipeline":
        # ADR-0030 决策 2：白名单内全部免卡，只有 cloud 花钱 / 销毁子命令弹卡
        rest = argv[argv.index("-m") + 2:] if argv and "-m" in argv else []
        module = _pipeline_module_of(argv or [])
        if module == "cloud" and rest[:1] and rest[0] in CLOUD_CARD_SUBCOMMANDS:
            allow_key = f"run_pipeline:cloud {rest[0]}"
        elif _human_gated(module, rest):
            # D82 §4.4：逐条人审的模块命令（Code Freeze 三条例外写 config/ + tickets open）：每次弹卡，不开放「本会话都批准」
            pass
        elif _skips_human_gate(rest):
            # 越过人工关卡（ADR-0030 决策 7 同一口径）：--confirm-patch 跳过 03.5 补丁段二次确认、
            # --waive 豁免素材门禁。逐条弹卡，不开放「本会话都批准」
            pass
        else:
            return ToolVerdict("allow", echo=_echo_line(name, args))
    elif name in AUTO_APPROVED_TOOLS:
        if memory_plan is not None:  # 与 cite 免卡同一回显：写了什么一眼可见，不是截断的参数 JSON
            return ToolVerdict("allow", echo=f"[memory] {memory_plan.summary}")
        return ToolVerdict("allow", echo=_echo_line(name, args))
    elif not TOOL_SCHEMAS[name].get("side_effect", True):
        return ToolVerdict("allow", echo=_echo_line(name, args))

    return ToolVerdict(
        "ask",
        request=_tool_request(
            name, args, argv, ep_dir, status, memory_plan, turn_id, loop_label=loop_label,
            command_preview=command_preview, session_allow=allow_key is not None, card_summary=card_summary,
        ),
        allow_key=allow_key,
    )


def _human_gated(module: str | None, rest: list[str]) -> bool:
    from pipeline.agent.tools import HUMAN_GATED_COMMANDS

    sub = rest[0] if rest else None
    return (module, sub) in HUMAN_GATED_COMMANDS or (module, None) in HUMAN_GATED_COMMANDS


def _skips_human_gate(tokens: list[str]) -> bool:
    """规范化 argv 里是否带越过人工关卡的旗标（含 argparse 前缀缩写与 `=` 写法）。"""
    for tok in tokens:
        flag = tok.split("=", 1)[0]
        if len(flag) > 2 and flag.startswith("--") and (
            "--confirm-patch".startswith(flag) or "--waive".startswith(flag)
        ):
            return True
    return False


def _pipeline_module_of(argv: list[str]) -> str | None:
    """规范化 argv（`python -m pipeline.<模块> ...`）里的模块短名；形状不符返回 None。"""
    if "-m" not in argv:
        return None
    module_at = argv.index("-m") + 1
    if module_at >= len(argv) or not argv[module_at].startswith("pipeline."):
        return None
    return argv[module_at].removeprefix("pipeline.")


def _readonly_asset_subcommand(argv: list[str]) -> bool:
    """规范化 argv 是否 asset 侧的只读子命令（D54 ③：`vindex who`）。子命令取模块名后第一个参数。"""
    from pipeline.agent.tools import READONLY_ASSET_SUBCOMMANDS

    module = _pipeline_module_of(argv)
    if module not in READONLY_ASSET_SUBCOMMANDS:
        return False
    rest = argv[argv.index("-m") + 2:]
    return bool(rest) and rest[0] in READONLY_ASSET_SUBCOMMANDS[module]


def _model_argv_has_options(argv: list[str], ep_dir: Path | None) -> bool:
    """模型发起的 `run_pipeline`：模块是 pipeline.review 时，除注入的期目录外不许有任何选项。"""
    if "-m" not in argv:
        return False
    module_at = argv.index("-m") + 1
    if argv[module_at] != "pipeline.review":
        return False
    ep_str = str(Path(ep_dir).resolve()) if ep_dir else None
    for token in argv[module_at + 1:]:
        if ep_str is not None and str(Path(token).resolve()) == ep_str:
            continue
        if token.startswith("-"):
            return True
    return False


def tool_summary(name: str, args: dict[str, Any]) -> str:
    """`tool{phase:"start"}.summary` 与终端 `[tool]` 回显共用同一规则（§3.1，剥控制字符）。"""
    listed = {
        "read_artifact": "path",
        "view_image": "path",
        "read_status": "episode",
        "search_notes": "query",
        "web_search": "query",
        "web_fetch": "url",
        "crawl": "url",
        "list_episodes": None,
    }
    if name == "write_episode_file":
        # D48：不再显示截断的 JSON（`{"confirmed": false, "content": "# …`）
        fn = str(args.get("filename", "")).strip()
        edits, content = args.get("edits"), args.get("content")
        if isinstance(edits, list):
            how = f"局部 {len(edits)} 处"
        elif isinstance(content, str):
            how = f"整篇 {len(content.encode('utf-8')) / 1024:.1f} KB"
        else:
            how = ""
        summary = " · ".join(p for p in (fn, how) if p)
    elif name == "run_pipeline":
        summary = str(args.get("command", "")).strip()
    elif name == "bash":
        lines = str(args.get("command", "")).strip().splitlines()
        first = lines[0] if lines else ""
        summary = (first[:80] + "…") if len(first) > 80 or len(lines) > 1 else first
    elif name == "search_notes" and args.get("source") == "subs":
        parts = ["[字幕]", str(args.get("episode", "")).strip(), str(args.get("query", "")).strip()]
        summary = " ".join(p for p in parts if p)
    elif name in listed:
        key = listed[name]
        summary = "" if key is None else str(args.get(key, "")).strip()
    else:
        summary = json.dumps(args, ensure_ascii=False)[:60] if args else ""
    return _clean(summary)


def tool_flags(content: Any) -> tuple[bool | None, str | None]:
    """§3.1 的 ok/observation 判定（只在 core 做一次，host/TS 不解析工具内容）。

    明确成功 = tool 消息 content 能解析为 JSON 对象、顶层 `ok is True`，且 `result`
    不是 `ok is False` 的对象（run_pipeline 失败时顶层仍为 `ok: True`，失败在 result 里）。
    """
    text = content if isinstance(content, str) else json.dumps(content, ensure_ascii=False)
    try:
        parsed = json.loads(text)
    except (TypeError, json.JSONDecodeError):
        return False, text
    if not isinstance(parsed, dict) or "ok" not in parsed:
        return False, text
    ok = parsed.get("ok") is True
    result = parsed.get("result")
    if ok and isinstance(result, dict) and result.get("ok") is False:
        ok = False
    return ok, (None if ok else text)


def _echo_line(name: str, args: dict[str, Any]) -> str:
    """终端 `[tool]` 回显（与 Spec 1 cli.py:803-819 同一规则，扩展到全部工具）。"""
    return _clean(f"[tool] {name} {tool_summary(name, args)}".strip())


def _target_of(memory_plan: Any, argv: list[str] | None, args: dict[str, Any]) -> str:
    if memory_plan is not None:
        return str(memory_plan.summary)
    if argv:
        return " ".join(argv)
    from pipeline.agent.cli import _candidates_brief

    return str(
        args.get("filename", "")
        or args.get("command", "")
        or args.get("url", "")
        or _candidates_brief(args.get("candidates"))
    )


def _tool_request(
    name: str,
    args: dict[str, Any],
    argv: list[str] | None,
    ep_dir: Path | None,
    status: Any,
    memory_plan: Any,
    turn_id: str | None = None,
    *,
    loop_label: str | None = None,
    command_preview: list[str] | None = None,
    session_allow: bool = False,
    card_summary: list[str] | None = None,
) -> HumanRequest:
    from pipeline.agent.memory import render_plan_preview
    from pipeline.agent.status_card import render_approval_card
    from pipeline.approvals import human_stop_of

    stop_label = None
    if status is not None:
        stop = human_stop_of(status.current_step)
        if stop is not None and argv and any(
            m in argv or f"pipeline.{m}" in argv for m in ("tts", "clips", "render")
        ):
            stop_label = f"[{stop}] 当前处于停机点 {status.current_step}"
    if loop_label:
        stop_label = loop_label if stop_label is None else f"{stop_label}；{loop_label}"

    card = render_approval_card(
        name,
        args,
        argv=argv,
        stop_label=stop_label,
        episode_dir=ep_dir,
        memory_preview=render_plan_preview(memory_plan) if memory_plan else None,
        command_preview=command_preview,
    )
    body = card.split("\n")
    if body and body[-1].strip().startswith("└─"):
        body.pop()  # 终端提示符行：协议下由 options 承担
    # §3.2 的 danger = 卡片「危险标记」那一行的值（M9 实测此前取的是末行的终端提示符）。
    # 取第一行：真标记行总在正文之前，其后的记忆全文、写稿 diff（D47）可能含模型写的「危险标记:」字样
    marks = [ln.split("危险标记:", 1)[1].strip() for ln in body if "危险标记:" in ln]
    fields = {
        "tool": name,
        "args": _jsonable(args),
        "argv": argv or [],
        "target": _target_of(memory_plan, argv, args),
        "stop_label": stop_label,
        "danger": _clean(marks[0] if marks else "无"),
        "memory_preview": list(render_plan_preview(memory_plan)) if memory_plan else [],
    }
    return HumanRequest(
        request_id=new_request_id(),
        kind="tool_call",
        # §3.1：回合内的请求带本回合 turn_id（M9 实测此前写死 None，review_tool_call 收到的 turn_id 白传）
        turn_id=turn_id,
        title=f"工具审查: {name}",
        card_text="\n".join(body),
        fields=fields,
        # ADR-0030 决策 5：bash 硬危险 / cloud 卡多一个「本会话都批准」（排在中间：终端 y 取首项、其余取末项）
        options=("approve", SESSION_ALLOW_DECISION, "reject") if session_allow else ("approve", "reject"),
        feedback_allowed=True,
        card_summary="\n".join(_clean(ln) for ln in card_summary) if card_summary else None,
    )


def _jsonable(value: Any) -> Any:
    try:
        json.dumps(value, ensure_ascii=False)
        return value
    except (TypeError, ValueError):
        return str(value)


class SessionHost:
    """进程内每期一个：持有租约、中断控制、主会话对象与其 messages 列表对象（§2.6）。

    `log_dir`（Spec 18 §3.1）：会话记录所在目录，缺省即期目录；idea 会话传 `data/_idea`，
    `ep_dir` 保持 `None`——日志的家不是期目录，需期工具照报「先建期」。
    """

    def __init__(
        self,
        ep_dir: Path | str | None,
        *,
        root: Path | None = None,
        channel: HumanChannel,
        ephemeral: bool = False,
        log_dir: Path | str | None = None,
    ) -> None:
        self.ep_dir = Path(ep_dir).resolve() if ep_dir is not None else None
        self.log_dir = Path(log_dir).resolve() if log_dir is not None else self.ep_dir
        # D81 §4.2：会话类型由树规则在**字面路径**上得出（软链卷的企划归属按字面，红队 R-5）；
        # 企划目录本身作期目录绑定时是企划会话。project_dir 保持字面路径（注入、读域、记忆都从它取）
        from pipeline import project as project_mod

        self.session_kind = "idea"
        self.project_dir: Path | None = None
        if ep_dir is not None:
            literal = project_mod.literal_episode_path(ep_dir, root)
            kind = project_mod.classify_dir(literal, root) if literal is not None else None
            if kind == "project":
                self.session_kind, self.project_dir = "project", literal
            elif kind == "volume":
                self.session_kind, self.project_dir = "volume", project_mod.project_of(literal, root)
            else:
                self.session_kind = "episode"
        self.root = root
        self.channel = channel
        self.ephemeral = ephemeral
        self.interrupt = TurnInterrupt()
        self.lease: EpisodeLease | None = None
        self.lease_error: str | None = None
        self.main_messages: list[dict[str, Any]] | None = None
        # D91：本会话的模型 / 推理强度设置（按 sid 落盘，见 sid 的 setter）。先于 sid 赋值初始化
        self.model_override = model_switch.Override()
        self.model_override_warning: str | None = None
        self._sid: str | None = None
        self.sid = None
        # 抓取执行器注入点（§2.4.4 第 4 条）：生产默认走 run_pipeline，测试注入替身
        self.fetch_executor: Callable[[int], dict[str, Any]] | None = None
        self._main: AgentSession | None = None
        self._scope_override: str | None = None
        self.resume_state: dict[str, Any] | None = None
        # ADR-0030 决策 5：「本会话都批准」过的同类键。只在进程内存，换会话 / 重启即失效
        self.session_allow: set[str] = set()
        # D79 排队消息（照 Pi 的 steering）：协议进程注入取队列函数，返回 [{id, text}]；
        # 终端没有排队，保持 None。送达通知同理（协议下发 steer_delivered 帧）
        self.steer_source: Callable[[], list[dict[str, str]]] | None = None
        self.on_steer_delivered: Callable[[list[str]], None] | None = None

    # ---- D91：会话级模型设置 ----

    @property
    def sid(self) -> str | None:
        return self._sid

    @sid.setter
    def sid(self, value: str | None) -> None:
        """sid 一确定就对齐本会话的模型设置：盘上有 → 读回（重开旧会话）；盘上没有而内存里有
        （新会话发第一条消息之前就 /model 过）→ 写下去。sid 只在会话开头赋一次。"""
        self._sid = value
        if value is None or self._persist_dir() is None:
            return
        stored, warning = model_switch.load(self._persist_dir(), value)
        self.model_override_warning = warning
        if not stored.empty:
            self.model_override = stored
        elif not self.model_override.empty and warning is None:
            self._save_override()

    def _persist_dir(self) -> Path | None:
        return None if self.ephemeral else self.log_dir

    def _save_override(self) -> None:
        d = self._persist_dir()
        if d is None or self._sid is None:
            return
        try:
            model_switch.save(d, self._sid, self.model_override)
        except (OSError, model_switch.ModelSwitchError) as exc:
            self.model_override_warning = f"本会话模型设置没能落盘（{exc}），重开会话后会回到默认"

    def set_model_override(self, override: "model_switch.Override") -> None:
        """改本会话设置：内存立即生效（下一轮起），有 sid 就落盘；没有 sid 等 sid 确定时再写。"""
        self.model_override = override
        self._save_override()

    def effective_llm_config(self, root: Path | None = None) -> Any:
        """默认配置叠本会话设置——回合、压缩、起标题、读数分母都取这一份（D91）。"""
        return model_switch.effective_config(
            llm_module.load_llm_config(root or self.root), self.model_override  # 晚绑定：测试可打桩
        )

    # ---- 登记 ----

    def matches(self, ep_dir: Path | str | None) -> bool:
        """期目录一致性（resolve 后比较）：不一致即按「没有登记」处理（§2.6）。"""
        if ep_dir is None or self.ep_dir is None:
            return ep_dir is None and self.ep_dir is None
        try:
            return Path(ep_dir).resolve() == self.ep_dir
        except OSError:
            return False

    def bind_main(self, messages: list[dict[str, Any]]) -> None:
        self.main_messages = messages

    def ensure_lease(self) -> EpisodeLease | None:
        """懒取租约（§2.5）：首个 agent 回合取，进程生命期持有。失败只记原因，不抛。

        只服务期会话。idea 会话的租约在启动时由 `acquire_idea_lease` 当场取、取不到就退出
        （Spec 18 §3.1：不许走这里的静默路径退回非持久）。
        """
        if self.ephemeral or self.log_dir is None:
            return None
        if self.lease is not None or self.lease_error is not None:
            return self.lease
        try:
            self.lease = EpisodeLease.acquire(self.log_dir)
        except (SessionLocked, SessionLogBroken) as exc:
            self.lease_error = str(exc)
        return self.lease

    def session(self, *, persist: bool, scope_mode: str, extra_prompt: str = "") -> "AgentSession":
        if persist and self._main is not None:
            self._main.extra_prompt = extra_prompt
            return self._main
        session = AgentSession(
            self, scope_mode=scope_mode, persist=persist, extra_prompt=extra_prompt
        )
        if persist:
            self._main = session
        return session

    def set_scope_override(self, value: str | None) -> None:
        self._scope_override = value
        if self._main is not None:
            self._main.set_scope_override(value)

    # ---- 分派 ----

    def dispatch(
        self,
        line: str,
        messages: list[dict[str, Any]],
        ep_dir: Path | str | None,
        scope: str,
        status: Any = None,
        extra_prompt: str = "",
        root: Path | None = None,
        approve_cb: Callable[[str, dict], Any] | None = None,
        tracker: Any = None,
    ) -> dict[str, Any]:
        """一轮对话（`cli._dispatch_agent_turn` 的全部语义）。"""
        persist = messages is self.main_messages
        self.ensure_lease()
        session = self.session(
            persist=persist, scope_mode=scope, extra_prompt=extra_prompt
        )
        return session.run_turn(
            line,
            messages=messages,
            scope=scope,
            status=status,
            tracker=tracker,
            root=root if root is not None else self.root,
            approve_cb=approve_cb,
            ep_dir=ep_dir,
        )


# run_turn 的 ep_dir 缺省哨兵：None 是合法的期目录（idea 无期），不能拿来表示「未传」
_HOST_EP = object()


@contextlib.contextmanager
def _sigint_blocked() -> Iterator[None]:
    """临界区内把 SIGINT 推迟到区段结束（D62 压缩落盘 + 原地改写）。

    不用 `pthread_sigmask`：它只屏蔽主线程，终端 Ctrl-C 发给整个进程，内核会交给任何没屏蔽的
    线程（REPL 里常驻着事件发布守护线程），CPython 照样在主线程下一条字节码抛出（复核 A 实测）。
    Python 层处理器**总在主线程**执行、与哪个线程收到信号无关——区内换成「只记一笔」，
    退出时恢复原处理器，记过就交给原处理器补一次（缺省处理器 = 抛 KeyboardInterrupt）。
    """
    if threading.current_thread() is not threading.main_thread():
        yield
        return
    hits: list[int] = []
    previous = signal.signal(signal.SIGINT, lambda signum, frame: hits.append(signum))
    try:
        yield
    finally:
        signal.signal(signal.SIGINT, previous)
        if hits and callable(previous):
            previous(signal.SIGINT, None)


def _k(tokens: int | None) -> str:
    """notice 用的 token 读数：12345 → 12.3k；None → ?。"""
    return "?" if tokens is None else f"{tokens / 1000:.1f}k"


class AgentSession:
    """一条会话线（主会话或子会话）。"""

    def __init__(
        self,
        host: SessionHost,
        *,
        scope_mode: str,
        persist: bool,
        extra_prompt: str = "",
    ) -> None:
        self.host = host
        self.scope_mode = scope_mode
        self.persist = persist
        self.extra_prompt = extra_prompt
        self.interrupt = host.interrupt
        self.channel = host.channel
        self._scope_override = host._scope_override
        self.messages: list[dict[str, Any]] = []
        self._turn_id = ""
        self._draft_writes = 0  # 本轮已放行的草稿写入次数（2026-10-08 spec §A：免卡限次）
        self._scope = scope_mode
        self._log_broken = False
        self._last_records: list[dict[str, Any]] = []
        self._injected_docs: dict[str, str] = {}
        self._tool_summaries: dict[int, str] = {}  # 本条回复内 index → start 帧的 summary（end 帧复用）
        self._tool_commands: dict[int, str] = {}   # D76：bash 调用 index → 完整命令（摘要只有 80 字，复制要原文）
        self._tool_descriptions: dict[int, str] = {}  # D80：bash 调用 index → 模型给的用途（工具行主文本）
        # D62 上下文压缩：摘要器失败 / 膨胀 / 压不了之后本会话不再自动压缩（spec §4.5）
        self.auto_compact_disabled = False
        self._compact_warned: set[str] = set()   # 「自动压缩不可用」只提示一次
        # 本回合最近一次压缩点：(压缩后长度, tracker 快照)。回合要回滚时只回到这里（spec §九 偏离 10）
        self._rollback_mark: tuple[int, Any] | None = None
        self._turn_compaction: dict[str, Any] | None = None  # 本回合压缩记账：首次压缩前、末次压缩后
        self._tracker: Any = None
        self._egress_trusted: list[str] = []
        self._egress_skip_turn = ""   # 本回合已提示过「预检不过、这次不压」
        self._root: Path | None = None

    # ---- 会话记录 ----

    def set_scope_override(self, value: str | None) -> None:
        self._scope_override = value

    def _record(self, record: dict[str, Any], origin: str | None = None) -> None:
        if not self.persist or self._log_broken:
            return
        lease = self.host.lease
        if lease is None:
            return
        try:
            if origin is not None:
                record = {**record, "origin": origin}
            lease.append({**record, "turn_id": record.get("turn_id") or self._turn_id})
        except SessionLogBroken as exc:
            # 会话记录丢了就无法恢复：标记损坏，此后不再写盘，绝不静默继续（§2.5）
            self._log_broken = True
            self.channel.show("stderr", {"text": f"[会话] 记录写盘失败，本会话此后不再落盘：{exc}"})

    def _commit(self, message: dict[str, Any], origin: str, docs: list[dict[str, str]] | None = None) -> None:
        """唯一提交入口：写盘 → 追加进内存（§2.5）。中断不可能落在两步之间。"""
        record: dict[str, Any] = {"k": "msg", "message": message}
        if docs:
            record["docs"] = docs
        # 延迟区闭集（§2.2 第 3 条）：区内到达的中断在退出时才抛，所以下面这两步
        # 不会被**撕开**（MUT-37）。注意 `defer()` 只是在 yield 处接住异常——落在
        # `_record` 里的信号照旧会丢掉后面的 `append`（盘上有、内存里没有 → 配对照
        # 不上 → 下次请求 400），所以这里显式把两步做完再抛。
        with self.interrupt.defer():
            try:
                self._record(record, origin)
            except KeyboardInterrupt:
                self.messages.append(message)
                raise
            self.messages.append(message)

    def _open_session(self, scope: str, tracker: Any, root: Path | None) -> None:
        """首个落盘回合写 `session_start`；带 `--continue` 时写 `segment_start`（§2.5）。"""
        if not self.persist or self.host.lease is None or self.host.sid is not None:
            return
        import hashlib

        resident = tracker.resident_prompt or ""
        resident_sha = hashlib.sha256(resident.encode("utf-8")).hexdigest()
        resume = self.host.resume_state
        if resume is None:
            sid = secrets.token_hex(8)
            self.host.lease.begin(sid, resumed_from_seq=0)
            self.host.sid = sid
            self._record({
                "k": "session_start", "schema": SCHEMA_NAME, "episode": self._episode_name(),
                "scope_mode": self.scope_mode, "resident_sha256": resident_sha,
                "pid": os.getpid(),
            })
            return
        sid = str(resume["sid"])
        self.host.lease.begin(sid, resumed_from_seq=int(resume.get("last_seq", 0)))
        self.host.sid = sid
        self._record({
            "k": "segment_start", "resident_sha256": resident_sha, "pid": os.getpid(),
            "resumed_from_seq": int(resume.get("last_seq", 0)),
        })
        self._injected_docs = dict(resume.get("docs") or {})
        if resume.get("resident_sha256") and resume["resident_sha256"] != resident_sha:
            self.channel.show("notice", {
                "level": "info", "code": "resident_changed",
                "text": "系统提示常驻层已按当前文件重建（规程/spec 有改动）。",
            })

    def _episode_name(self) -> str:
        return self.host.ep_dir.name if self.host.ep_dir is not None else ""

    # ---- 回合 ----

    def run_turn(
        self,
        line: str,
        *,
        messages: list[dict[str, Any]],
        scope: str,
        status: Any = None,
        tracker: Any = None,
        root: Path | None = None,
        approve_cb: Callable[[str, dict], Any] | None = None,
        turn_id: str | None = None,
        ep_dir: Path | str | None | object = _HOST_EP,
    ) -> dict[str, Any]:
        """跑一轮（§4.7 的处理顺序）。

        `turn_id` 可由调用方给定：协议要先发 `turn_started{turn_id}` 出去，host 才可能
        发回 `interrupt{turn_id}`（§3.1）；终端路径不传，自行生成。
        `ep_dir` 由包装传入（§4.3）：工具上下文的期目录取它；不传（协议进程、测试直调）即登记本身的期。
        """
        from pipeline.agent.assembly import step_key_of
        from pipeline.agent.llm import LLMError, local_directive_message  # noqa: F401

        if tracker is None:
            raise ValueError(
                "tracker 必须由 run_agent_loop 创建并作为会话级单例传入，"
                "严禁在 _dispatch_agent_turn 轮内懒创建（Spec 1 M4 铁律）"
            )
        if self.channel is None:
            # 内核不变量（§2.4.2 第 5 条 / TK-5）：没有可问人的通道，工具循环一步都不许跑。
            # 模型自答的通道不存在，这条断言就是「答复只能来自人」的最后一道机制保证。
            raise RuntimeError("没有可用的通道：拒绝运行工具循环（Spec 9 §2.4.2）")

        self.messages = messages
        self._turn_id = turn_id or secrets.token_hex(8)
        self._draft_writes = 0
        self._tracker = tracker
        self._root = root
        self._rollback_mark = None
        self._turn_compaction = None
        self._ep_dir = self.host.ep_dir if ep_dir is _HOST_EP else (
            Path(ep_dir).resolve() if ep_dir is not None else None  # type: ignore[arg-type]
        )
        effective_scope = self._scope_override or scope
        # 本轮的工具审查一律用调用方给的 scope（终端每轮热推导，M11 锚点不动）；
        # 绝不在这里从 status 重新推一份——那会把 pipeline scope 的回合按产物阶段错配成 creative。
        self._scope = effective_scope
        with self.interrupt.installed():
            snapshot = (len(messages), copy.deepcopy(tracker))
            outcome: dict[str, Any] | None = None
            try:
                # 回合起点的写盘临界区（§2.2 第 3 条）：区内到达的中断等退出后再抛，
                # 写盘与进内存不会被撕开。浮出后由本函数的 except 接住 → interrupted + 回滚。
                with self.interrupt.defer():
                    self._open_session(effective_scope, tracker, root)
                    self._record({
                        "k": "turn_start",
                        "scope": effective_scope,
                        "step_key": step_key_of(status.current_step if status else None),
                    })
                degraded = self._assemble(
                    messages, tracker, status, effective_scope, line, root=root
                )
                if degraded is not None:
                    self.channel.show("stdout", {"text": f"\n{degraded['content']}"})
                    self._finish_turn(snapshot, tracker, {"stopped": "degraded"}, messages)
                    return {"stopped": "degraded", "messages": messages, "final": degraded}

                from pipeline.agent.assembly import route_trusted_texts

                # 出网断言可信集 = (a) 本进程已提交的规程正文 ∪ (b) 路由表当前正文（Spec 16 §5.2.4）
                egress_trusted = [
                    *tracker.trusted_doc_texts, *route_trusted_texts(effective_scope, root=root)
                ]
                self._egress_trusted = egress_trusted  # D62：自动压缩改写前用同一可信集预检
                outcome = llm_module.run_tool_loop(
                    messages,
                    ctx=self._tool_context(effective_scope, root),
                    # D91：本会话的模型 / 推理强度（没设过就是默认配置；缺配置时为 None → 循环自己降级）
                    config=self.host.effective_llm_config(root),
                    egress_trusted=egress_trusted,
                    # approve 显式传 None（裸循环适配器不用），control 才是生产路径。
                    # 现有多处打桩的签名带位置参数 approve，不传就当场 TypeError。
                    approve=None,
                    control=self._control(
                        turn_id=self._turn_id, status=status, root=root, approve_cb=approve_cb
                    ),
                )
            except KeyboardInterrupt:
                # 首个模型请求之前浮出（回合起点/装配期）：本轮 0 条回复 → 回滚（§2.3 第 3 条）
                outcome = {
                    "stopped": "interrupted", "rollback": True,
                    "llm_calls": 0, "tool_calls_made": 0, "tool_executions": 0,
                    "duplicates_rejected": 0, "checkpoints": 0,
                    "wrapup": "skipped" if self.interrupt.skip_wrapup else "none",
                }
            except LLMError as exc:
                self.channel.show("stdout", {"text": f"[FAIL] {exc}"})
                self._rollback(messages, snapshot, tracker)
                return {"stopped": "error", "messages": messages, "final": {}}
            except PermissionError as exc:
                self.channel.show("stdout", {"text": f"[BLOCKED] 出网被拦截：{exc}"})
                self.channel.show("stdout", {
                    "text": "          本次请求未发出。请改问不含受限内容（密钥/音频清单/补片素材）的问题。"
                })
                self._rollback(messages, snapshot, tracker)
                return {"stopped": "error", "messages": messages, "final": {}}
            self._finish_turn(snapshot, tracker, outcome, messages)
            if outcome.get("stopped") == "interrupted":
                # I-8：非正常停止要看得见（“不知为何停了”比多一行更难查）
                self.channel.show("stdout", {
                    "text": "[中断] 本轮已停止，已执行的工具结果保留在会话中。"
                })
            if self.interrupt.clear_dropped():
                self.channel.show("notice", {
                    "level": "info", "code": "interrupt_dropped",
                    "text": "回合并已结束，这次中断被丢弃（未带进空闲态）。",
                })
            return outcome

    def _finish_turn(
        self, snapshot: tuple, tracker: Any, outcome: dict[str, Any], messages: list[dict[str, Any]]
    ) -> None:
        if outcome.get("stopped") == "degraded":
            return
        if outcome.get("rollback"):
            self._rollback(messages, snapshot, tracker)
        # 压缩记账以会话层为准：压缩落完后才浮出的中断，工具循环那边记不上（审查 #3）
        compaction = self._turn_compaction or {}
        outcome["compacted"] = bool(compaction)
        outcome["tokens_before"] = compaction.get("tokens_before")
        outcome["tokens_after"] = compaction.get("tokens_after")
        from pipeline.agent.tools import turn_error_text  # 延迟 import：与本文件其余 tools 引用同口径（避循环）

        with self.interrupt.absorbed():
            self._record({
                "k": "turn_end",
                "stopped": outcome.get("stopped", "error"),
                "llm_calls": outcome.get("llm_calls", 0),
                "tool_calls": outcome.get("tool_calls_made", 0),
                "tool_executions": outcome.get("tool_executions", 0),
                "duplicates_rejected": outcome.get("duplicates_rejected", 0),
                "checkpoints": outcome.get("checkpoints", 0),
                "wrapup": outcome.get("wrapup", "none"),
                "duration_s": round(float(outcome.get("elapsed_s", 0.0)), 3),
                "prompt_chars": outcome.get("prompt_chars", 0),
                "prompt_tokens": outcome.get("prompt_tokens"),  # D56：服务商给的输入 token；拿不到为 None
                "lookups": outcome.get("lookups"),  # D48 ①：本回合查证调用按类计数；本地指令等无模型回合为 None
                # 报错原文（脱敏、截断）：0 次模型调用就失败的回合，事后只能靠这一行知道是哪一类错（2026-10-13）
                "error": turn_error_text(outcome.get("error"), self._root),
                "recovered": False,
                # D62 人裁决②：压缩改写了工作前提，turn_end 如实带出（desktop 读数「压缩后（压缩前峰值）」用）
                "compacted": outcome["compacted"],
                "tokens_before": outcome["tokens_before"],
                "tokens_after": outcome["tokens_after"],
                "llm_retries": outcome.get("llm_retries", 0),  # D93：本回合模型请求自动重试次数
            })
            if self._log_broken:
                self.channel.show("notice", {
                    "level": "error", "code": "session_log_broken",
                    "text": "会话记录已损坏，本轮不再落盘。请开新会话（不加 --continue）。",
                })

    def _rollback(self, messages: list[dict[str, Any]], snapshot: tuple, tracker: Any) -> None:
        """回滚 = 恢复到回合开始时的快照（§2.3 第 3 条）。

        D62：本回合压缩过 → 回合起点的快照水位已失效（压缩把历史缩到它之下，`del` 成空操作 = 假回滚），
        改为回到**最近一次压缩点**。这与日志投影一致：压缩事件自带保留尾部，`turn_rollback` 只滤掉
        本回合在压缩点之后提交的消息。压缩本身不撤销（spec §九 偏离 10）。
        """
        mark = self._rollback_mark
        length, saved_tracker = mark if mark is not None else snapshot
        if mark is not None:
            self.channel.show("notice", {
                "level": "warn", "code": "rollback_to_compaction",
                "text": (
                    "本回合压缩过上下文：只回滚到压缩点，压缩本身保留（较早的对话已是摘要）；"
                    "压缩点之前本轮已提交的内容（含本轮的提问）仍在上下文里。"
                ),
            })
        del messages[length:]
        # §2.3 第 3 条 / 🔵-1 / 🟡-6：**只有**显示锁存不回退（`memory_warn_printed`，
        # 免得终端重复打印同一条告警）；告警消息本身随回滚出历史，下一轮重新注入。
        for name in (
            "injected_paths", "active_step_key", "resident_prompt", "active_scope",
            "memory_warn_injected",
        ):
            value = getattr(saved_tracker, name)
            setattr(tracker, name, set(value) if isinstance(value, set) else value)
        self._record({"k": "turn_rollback"})

    # ---- 装配（原 cli.py:887-976 整段搬入，逻辑不改） ----

    def _assemble(
        self,
        messages: list[dict[str, Any]],
        tracker: Any,
        status: Any,
        scope: str,
        line: str,
        *,
        root: Path | None,
    ) -> dict[str, Any] | None:
        from pipeline.agent.assembly import (
            assemble_resident_prompt,
            load_injected_doc,
            render_step_injection,
            resolve_memory_injection,
            resolve_step_docs,
            step_key_of,
            trusted_texts_of_docs,
            trusted_texts_of_resident,
        )
        from pipeline.agent.llm import load_llm_config, local_directive_message
        from pipeline.agent.status_card import build_idea_card

        if tracker.active_scope != scope or not tracker.resident_prompt:
            resident = assemble_resident_prompt(scope, root=root, extra_prompt=self.extra_prompt)
            tracker.resident_prompt = resident.content
            tracker.active_scope = scope
            # 可信集 (a) 常驻层：三份文件各自的原文，**不取** resident_prompt（它含 extra_prompt）
            tracker.trust(trusted_texts_of_resident(resident, root))

        step_key = step_key_of(status.current_step if status else None)
        step_docs = [
            doc
            for doc in (
                load_injected_doc(p.as_posix(), root=root)
                for p in resolve_step_docs(scope, step_key, root=root)
            )
            if doc is not None
        ]
        status_card = (
            build_idea_card()
            if self.host.ep_dir is None
            else self._status_card(scope, status)
        )

        if not messages:
            sys_content = tracker.get_initial_system_prompt(status_card)
            self._commit({"role": "system", "content": sys_content}, "injection")
            if step_docs:
                injection = render_step_injection(
                    step_docs, step_name=status.current_step if status else None
                )
                self._commit({"role": "user", "content": injection}, "injection", docs=_doc_shas(step_docs))
                tracker.injected_paths.update(d.rel_path for d in step_docs)
                tracker.trust(trusted_texts_of_docs(step_docs, root))  # 可信集 (a) 工序层
            tracker.active_step_key = step_key
        else:
            if step_key != tracker.active_step_key:
                new_docs = [d for d in step_docs if d.rel_path not in tracker.injected_paths]
                if new_docs:
                    injection = render_step_injection(
                        new_docs, step_name=status.current_step if status else None
                    )
                    self._commit({"role": "user", "content": injection}, "injection", docs=_doc_shas(new_docs))
                    tracker.injected_paths.update(d.rel_path for d in new_docs)
                    tracker.trust(trusted_texts_of_docs(new_docs, root))  # 可信集 (a) 工序层
                tracker.active_step_key = step_key
            # 刷新状态卡：整段替换 messages[0]（一轮之内不再变）
            messages[0] = {
                "role": "system",
                "content": tracker.get_initial_system_prompt(status_card),
            }

        self._inject_memory(messages, tracker, scope, root=root)
        self._reinject_changed(messages, tracker, scope, status, root=root)
        self._inject_project(tracker, root=root)

        if load_llm_config(root) is None:
            return local_directive_message(scope, "缺少 config/agent.json 或环境变量密钥")
        self._commit({"role": "user", "content": line}, "user")
        return None

    def _status_card(self, scope: str, status: Any) -> str:
        from pipeline.agent.status_card import build_project_card, build_status_card

        if self.host.session_kind == "project" and self.host.project_dir is not None:
            return build_project_card(self.host.project_dir)
        return build_status_card(
            self.host.ep_dir, status, scope=scope,
            project_dir=self.host.project_dir if self.host.session_kind == "volume" else None,
        )

    def _inject_memory(
        self, messages: list[dict[str, Any]], tracker: Any, scope: str, *, root: Path | None
    ) -> None:
        from pipeline.agent.assembly import resolve_memory_injection
        from pipeline.agent.memory import MEMORY_REL_PATH

        if MEMORY_REL_PATH in tracker.injected_paths:
            return
        resolved = resolve_memory_injection(scope, root=root)
        if resolved is None:
            return
        doc, is_warning = resolved
        if not is_warning:
            # 告警正文不记 sha：记了会让恢复后把真正的记忆当成「已注入」而不再注入（N56）
            self._commit({"role": "user", "content": doc.content}, "memory", docs=_doc_shas([doc]))
            tracker.injected_paths.add(doc.rel_path)
        elif not tracker.memory_warn_injected:
            self._commit({"role": "user", "content": doc.content}, "memory")
            tracker.memory_warn_injected = True
            if not tracker.memory_warn_printed:
                self.channel.show("stderr", {"text": f"[WARN] {doc.content}"})
                tracker.memory_warn_printed = True

    def _inject_project(self, tracker: Any, *, root: Path | None) -> None:
        """D81 §4.6：卷 / 企划会话注入「企划文件」与「企划记忆」各一条，每轮比 sha、变了再注「（已更新）」。

        企划记忆不看 `memory.scopes`（人裁决 Q6）：卷 / 企划会话一律注入。注入文本过 scrub_restricted。
        """
        if self.host.session_kind not in ("volume", "project") or self.host.project_dir is None:
            return
        for path, text, warning in project_injections(self.host.project_dir, root):
            sha = hashlib.sha256(text.encode("utf-8")).hexdigest()
            if tracker.project_injected.get(path) == sha:
                continue
            updated = path in tracker.project_injected
            content = text.replace("]", "]（已更新）", 1) if updated else text
            self._commit({"role": "user", "content": content}, "injection",
                         docs=[{"path": path, "sha256": sha}])
            tracker.project_injected[path] = sha
            tracker.project_texts[path] = content
            if warning == "unconfirmed" and not tracker.project_memory_notice_sent:
                tracker.project_memory_notice_sent = True
                self.channel.show("notice", {
                    "level": "warn", "code": "project_memory_unconfirmed",
                    "text": "企划记忆 00-memory.md 有 ava 之外的改动，本会话未注入；请人运行 /memory --project ack 看 diff 后对账。",
                })

    def _reinject_changed(
        self, messages: list[dict[str, Any]], tracker: Any, scope: str, status: Any, *, root: Path | None
    ) -> None:
        """恢复后的第一轮：已注入文档的当前 sha 变了就追加新正文（§2.7 第 3 条）。"""
        if not self._injected_docs:
            return
        import hashlib

        from pipeline.agent.assembly import (
            load_injected_doc,
            resolve_memory_injection,
            resolve_step_docs,
            step_key_of,
            trusted_texts_of_docs,
        )

        # (文档, 能否进出网可信集)：只有 resolve_step_docs 的候选可以；记忆永不可信（Spec 16 §5.2.3）
        candidates: list[tuple[Any, bool]] = []
        step_key = step_key_of(status.current_step if status else None)
        for path in resolve_step_docs(scope, step_key, root=root):
            doc = load_injected_doc(path.as_posix(), root=root)
            if doc is not None:
                candidates.append((doc, True))
        memory_doc = resolve_memory_injection(scope, root=root)
        if memory_doc is not None and not memory_doc[1]:  # 记忆此刻只剩告警：不当作「已修订」重注入
            candidates.append((memory_doc[0], False))
        for doc, trustable in candidates:
            recorded = self._injected_docs.get(doc.rel_path)
            if not recorded:
                continue
            current = hashlib.sha256(doc.content.encode("utf-8")).hexdigest()
            if current == recorded:
                continue
            self._commit(
                {
                    "role": "user",
                    "content": f"[系统提示更新] 规程已修订：{doc.rel_path}\n\n{doc.content}",
                },
                "injection",
                docs=[{"path": doc.rel_path, "sha256": current}],
            )
            tracker.injected_paths.add(doc.rel_path)
            if trustable:
                tracker.trust(trusted_texts_of_docs([doc], root))  # 可信集 (a) 修订重注入
        self._injected_docs = {}

    # ---- 上下文压缩（D62） ----

    def compact(
        self,
        messages: list[dict[str, Any]],
        *,
        tracker: Any,
        root: Path | None = None,
        scope: str | None = None,
        trigger: str = "manual",
        status_card: str | None = None,
        reinject_card: str | None = None,
        reading: Any = None,
    ) -> dict[str, Any]:
        """把 `messages[1:cut]` 换成一条摘要（spec §4.2–4.8）。手动 `/compact` 与自动触发共用。

        失败一律历史不动、如实返回原因；摘要器失败 / 膨胀 / 配置坏 → 本会话禁用自动压缩。
        成功时**先写盘再原地改写**（与 `_commit` 同一纪律），并清掉被压掉的已注入文档登记，
        让下一轮装配器重注当前工序的规程（压缩发现 #1，spec §九）。
        中断：手动时吞掉并报「历史未改动」；自动（回合内）时照抛，交给工具循环的落点表。
        """
        import hashlib

        from pipeline.agent import compact as cp

        manual = trigger == "manual"

        def fail(reason: str, text: str, *, disable: bool = False) -> dict[str, Any]:
            if disable:
                self.auto_compact_disabled = True
            return {"ok": False, "reason": reason, "text": text}

        if not self.persist or messages is not self.host.main_messages:
            return fail("not_main", "只有主会话可以压缩（子会话短且单次，不压）")
        if len(messages) < 2:
            return fail("nothing", "没有可压缩的内容：会话还没有对话")
        self.host.ensure_lease()
        effective_scope = scope or self._scope
        cfg = self.host.effective_llm_config(root)   # D91：摘要器与本会话用同一个模型
        if cfg is None:
            return fail("no_llm", "LLM 不可用（缺配置或密钥），无法生成摘要")
        try:
            ccfg = cp.load_compact_config(root)
        except cp.CompactConfigError as exc:
            return fail("bad_config", f"config/agent/compact.json 不合法：{exc}", disable=True)
        purpose = llm_module.purpose_for_scope(effective_scope)
        model = cfg.model_for(purpose)
        budget = ccfg.tail_budget_tokens(model)
        if budget is None:
            return fail(
                "no_window",
                f"窗口表里没有当前模型 {model}：先在 config/agent/compact.json 登记它的窗口（不猜）",
            )
        if not cp.is_closed(messages):
            return fail("open_calls", "历史里有还没配上结果的工具调用，现在压缩会切坏配对")
        # D65（红队 F2，人拍板 2026-10-10）：压缩三处口径全按老化后（= 实际发送口径）——
        # 投影保序保长，切口下标回原列表取 tail / 写史；落盘与出网断言对象不变。
        # 放这里一次投影三处共用，不进 compact.py（它的生产调用方只有本函数）。
        from pipeline.agent import aging

        aged = aging.age_tool_results(messages)
        cut = cp.choose_cut(aged, token_budget=budget, max_messages=ccfg.tail_max_messages)
        region = aged[1:cut] if cut > 1 else []
        if not cp.strip_marked(region):
            return fail("nothing", "没有可压缩的内容：较早的历史已经全在保留的近期原文里")

        # 读数连同口径一起传进来（审查 #5）：自动触发的读数可能是估算，不许记成实测
        before = reading if reading is not None else cp.context_reading(None, messages)
        card = cp.card_message(reinject_card) if reinject_card else None
        tail = cp.strip_marked(messages[cut:])
        if not manual:
            # 自动压缩后回合还要接着发请求：先用本回合的可信集把「压缩后会留下的原文」过一遍出网断言，
            # 过不了就不压——否则压缩后撞断言只能回到压缩点，脏串仍在尾部里，一轮轮卡死（审查 #2）。
            # 摘要不用查：它是输出侧脱敏后的文本。查在调摘要器之前，省一次注定白费的长调用
            from pipeline.agent.tools import assert_egress_boundary

            kept_wire = llm_module._wire_messages([messages[0], *([card] if card else []), *tail], purpose)
            try:
                assert_egress_boundary(cfg.endpoint, {"messages": kept_wire}, trusted_texts=self._egress_trusted)
            except PermissionError as exc:
                return fail("egress", f"压缩后留下的原文过不了出网断言（{exc}），不压")
        request, input_hits = cp.build_summary_request(region, status_card=status_card)
        try:
            reply = llm_module.chat_complete(
                request, None, config=cfg, root=root, timeout=cp.COMPACT_TIMEOUT_S,
                scope=effective_scope, purpose=purpose,
            )
        except KeyboardInterrupt:
            if not manual:
                raise
            return fail("interrupted", "压缩被中断，历史未改动")
        except (llm_module.LLMError, PermissionError) as exc:
            return fail("error", f"摘要器调用失败，历史未改动：{exc}", disable=True)
        text = str(reply.get("content") or "").strip()
        if reply.get("degraded") or not text:
            return fail("empty", "摘要器没有返回可用摘要，历史未改动", disable=True)
        if cp.is_bloated(text, region):
            return fail(
                "bloated",
                f"按实际发送口径（大体积工具结果已老化占位），摘要省不出更多"
                f"（≥ 发送口径原文的 {cp.BLOAT_RATIO:.0%}），历史未改动",
                disable=True,
            )

        summary = cp.summary_message(cp.annotate_redactions(text, input_hits))
        after = cp.compacted_history(messages, cut, summary, card)
        chars_before = sum(cp.message_chars(m) for m in messages)
        chars_after = sum(cp.message_chars(m) for m in after)
        if before.source == "usage" and before.tokens is not None and chars_before:
            # 同一把尺：压缩前是服务商实测，压缩后按字数比例折算，不拿实测去比估算
            tokens_after, after_source = round(before.tokens * chars_after / chars_before), "scaled"
        else:
            tokens_after, after_source = cp.estimate_tokens(after), "estimate"
        kept_docs = self._kept_injected(tracker, tail, effective_scope, root)
        # D81：企划注入的正文没留在尾部 → 下一轮重注（与规程同一判据）
        kept_project = cp.kept_doc_paths(tracker.project_texts, tail)
        info = {
            "ok": True,
            "trigger": trigger,
            "compacted": len(region),
            "kept": len(tail),
            "tokens_before": before.tokens,
            "tokens_before_source": before.source,
            "tokens_after": tokens_after,
            "tokens_after_source": after_source,
        }
        record = {
            "k": "compaction",
            **{k: v for k, v in info.items() if k != "ok"},
            "summary": summary,
            "card": card,
            "tail": tail,
            # 压缩后仍在上下文里的已注入文档：--continue 据此重置「已注入」登记（审查 #1）
            "docs_kept": sorted(kept_docs | kept_project),
            "summary_sha256": hashlib.sha256(summary["content"].encode("utf-8")).hexdigest(),
            # 手动压缩不属于任何回合：给自己的 id，免得挂在上一回合名下、跟着它的回滚记录走
            "turn_id": f"compact-{secrets.token_hex(4)}" if manual or not self._turn_id else self._turn_id,
        }
        try:
            # 写盘、原地改写、登记与记账必须一起成立（审查 #3）。`defer()` 只在 yield 处接住异常，
            # 挡不住信号打断区内语句（见 _commit 注释）；所以这段在主线程上**屏蔽 SIGINT**，
            # 信号挂起到区段做完才浮出，再由 defer 接住、退出时抛出
            with self.interrupt.defer(), _sigint_blocked():
                self._open_session(effective_scope, tracker, root)  # 恢复后还没跑过回合：先写 segment_start
                self._record(record)
                cp.apply_compaction(messages, cut, summary, card)
                tracker.injected_paths = kept_docs
                tracker.project_injected = {k: v for k, v in tracker.project_injected.items() if k in kept_project}
                tracker.project_texts = {k: v for k, v in tracker.project_texts.items() if k in kept_project}
                tracker.active_step_key = None  # 下一轮装配按当前工序重注被压掉的规程
                tracker.memory_warn_injected = False
                if not manual:
                    self._rollback_mark = (len(messages), copy.deepcopy(tracker))
                    first = self._turn_compaction or {"tokens_before": before.tokens}
                    self._turn_compaction = {**first, "tokens_after": tokens_after}
                self.channel.show("notice", {
                    "level": "info", "code": "compacted",
                    "text": (
                        f"上下文已压缩（{'手动' if manual else '自动'}）：{len(region)} 条较早消息并成一条摘要，"
                        f"保留近期 {len(tail)} 条；约 {_k(before.tokens)} → 约 {_k(tokens_after)} token。"
                    ),
                })
        except KeyboardInterrupt:
            if not manual:
                raise
        return info

    def _kept_injected(self, tracker: Any, tail: list[dict[str, Any]], scope: str, root: Path | None) -> set[str]:
        """已注入文档里正文仍逐字留在保留尾部的那些；其余的压缩后要让装配器重注。"""
        from pipeline.agent import compact as cp
        from pipeline.agent.assembly import load_injected_doc, resolve_memory_injection
        from pipeline.agent.memory import MEMORY_REL_PATH

        contents: dict[str, str] = {}
        for path in tracker.injected_paths:
            if path == MEMORY_REL_PATH:
                resolved = resolve_memory_injection(scope, root=root)
                doc = resolved[0] if resolved is not None and not resolved[1] else None
            else:
                doc = load_injected_doc(path, root=root)
            if doc is not None:
                contents[path] = doc.content
        return cp.kept_doc_paths(contents, tail)

    def _auto_compact(self, convo: list[dict[str, Any]], tokens_hint: int | None) -> dict[str, Any] | None:
        """工具循环的自动触发点（PR3，spec §4.1 双触发）。返回 None = 没压；`{"ok": False, "abort": True}` = 中止回合交人。"""
        from pipeline.agent import compact as cp

        if not self.persist or self.auto_compact_disabled or convo is not self.host.main_messages:
            return None
        try:
            ccfg = cp.load_compact_config(self._root)
        except cp.CompactConfigError as exc:
            self.auto_compact_disabled = True
            self.channel.show("notice", {"level": "error", "code": "compact_config",
                                         "text": f"config/agent/compact.json 不合法，本会话不自动压缩：{exc}"})
            return None
        if not ccfg.auto_trigger:
            return None
        cfg = self.host.effective_llm_config(self._root)   # D91：触发判据按本会话实际模型的窗口
        if cfg is None:
            return None
        model = cfg.model_for(llm_module.purpose_for_scope(self._scope))
        decision = cp.decide_trigger(ccfg, model, tokens_hint, convo)
        if decision.reason in ("no_window", "no_reading"):
            if decision.reason not in self._compact_warned:
                self._compact_warned.add(decision.reason)
                why = (f"窗口表里没有当前模型 {model}" if decision.reason == "no_window"
                       else "服务商没给 token 读数、字数也估不出")
                self.channel.show("notice", {"level": "warn", "code": "compact_unavailable",
                                             "text": f"自动压缩不可用：{why}。上下文会一直涨，必要时手动 /compact。"})
            return None
        if not decision.fire:
            return None
        card = None
        if self.host.ep_dir is not None:  # idea 会话：静态卡本就在 messages[0]，跳过重注入（人裁决③）
            from pipeline.status import inspect_episode

            card = self._status_card(self._scope, inspect_episode(self.host.ep_dir))
        result = self.compact(
            convo, tracker=self._tracker, root=self._root, scope=self._scope, trigger="auto",
            status_card=card, reinject_card=card, reading=decision.reading,
        )
        if result["ok"]:
            return result
        if result["reason"] == "egress":
            # 只跳过这一次（复核 B）：脏消息会随本回合撞断言后的整回合回滚出历史，没理由连带关掉自动压缩。
            # 同一回合只提示一次——预检在摘要器之前、代价小，循环每轮重试也不白花长调用
            if self._egress_skip_turn != self._turn_id:
                self._egress_skip_turn = self._turn_id
                self.channel.show("notice", {"level": "warn", "code": "compact_skipped",
                                             "text": f"已到压缩线，这次不压：{result['text']}。"})
            return None
        if result["reason"] in ("nothing", "no_window", "open_calls", "not_main", "no_llm"):
            # 不是摘要器失败：压不了就照常跑（撞窗口时服务商报错如实交人），本会话不再反复尝试
            self.auto_compact_disabled = True
            self.channel.show("notice", {"level": "warn", "code": "compact_skipped",
                                         "text": f"已到压缩线但压不了：{result['text']}。本会话不再自动压缩。"})
            return None
        self.channel.show("notice", {"level": "error", "code": "compact_failed",
                                     "text": f"自动压缩失败：{result['text']}。本回合停止，本会话不再自动压缩。"})
        return {"ok": False, "abort": True, "text": result["text"]}

    def _tool_context(self, scope: str, root: Path | None):
        from pipeline.agent.tools import ToolContext

        # 期目录一律取自**包装收到的参数**，从不取自 SessionHost（五轮自查 / MUT-60）
        # D81：会话类型与所属企划取自宿主绑定（树规则得出），不取模型参数
        return ToolContext(
            scope=scope, episode_dir=self._ep_dir, root=root,
            session_kind=self.host.session_kind, project_dir=self.host.project_dir,
            session_id=self.host.sid,
        )

    # ---- 控制面 ----

    def _control(
        self,
        *,
        turn_id: str,
        status: Any,
        root: Path | None,
        approve_cb: Callable[[str, dict], Any] | None,
    ) -> LoopControl:
        return LoopControl(
            interrupt=self.interrupt,
            commit=self._commit,
            review=lambda name, args: self._review(name, args, turn_id, status, root, approve_cb),
            ask_checkpoint=lambda snapshot: self._ask_checkpoint(snapshot, turn_id),
            post_execute=self._post_execute,
            on_trace=self._on_trace,
            on_exec_started=lambda call_id, name: self._record({
                "k": "tool_exec_started", "tool_call_id": call_id, "name": name, "turn_id": turn_id,
            }),
            critical_tools=CRITICAL_TOOLS,
            # D62：子会话（persist=False）不自动压缩（spec §4.8，审查 #15）
            compact=self._auto_compact if self.persist else None,
            # D79：排队消息只属于主会话（子会话没有人在对面打字）
            drain_steer=self.host.steer_source if self.persist else None,
            on_steer=self._on_steer,
            on_llm_retry=self._on_llm_retry,
        )

    def _on_llm_retry(self, attempt: int, limit: int, delay: float, error: str) -> None:
        """D93：模型请求遇可重试错误、即将自动重发——上屏一行，人看得到「在重试」而不是干等。"""
        from pipeline.agent.tools import turn_error_text  # 延迟 import：与 turn_end 同口径（脱敏、截断）

        self.channel.show("notice", {
            "level": "warn", "code": "llm_retry",
            "text": f"模型请求失败（{turn_error_text(error, self._root)}），{delay:g} 秒后自动重试"
                    f"（{attempt}/{limit}），点停止可放弃",
        })

    def _on_steer(self, ids: list[str], answered: str | None) -> None:
        """D79：排队消息已送达。被续上的答复先上屏，再报送达（协议下发 steer_delivered）。"""
        if answered:
            self.channel.show("interim_answer", {"turn_id": self._turn_id, "text": answered})
        if self.host.on_steer_delivered is not None:
            self.host.on_steer_delivered(ids)

    def _on_trace(self, payload: dict[str, Any]) -> None:
        """工具帧（§3.1 的 `tool`）：判定在这里做一次，terminal 忽略，协议通道转成帧。

        帧的键**恰为** §3.1 的 8 个（S9-R1）：原始 `args` 与工具结果 `content` 只是判定材料，
        不出进程（host 不解析工具内容；整份稿件也不该塞进帧里）。未在该阶段定义的键取中性值：
        start 的 ok/observation 为 null；end 的 summary 复用同一调用 start 时算出的值。
        M9 真实联调实测：此前只「添加」判定结果，start 缺 ok/observation、end 缺 summary，
        host 按 §3.2 整帧判 malformed 丢弃——界面上一条工具行都没有。
        唯一的条件键（D76）：`name == "bash"` 的两个阶段都多一个 `command`（完整命令原文，工具行「复制命令」用；
        摘要截在 80 字）。它是模型发出的参数、不是工具结果，TS 侧 `convFrames.CONDITIONAL` 同步登记。
        D80（R2 ①）：同一条件下再多一个 `description`（模型给的用途，剥控制字符，可为空串），工具行拿它当主文本。
        """
        name = str(payload.get("name", ""))
        index = int(payload.get("index", 0))
        if payload.get("phase") == "start":
            summary = tool_summary(name, payload.get("args") or {})
            self._tool_summaries[index] = summary
            if name == "bash":
                self._tool_commands[index] = str((payload.get("args") or {}).get("command", ""))
                self._tool_descriptions[index] = _clean((payload.get("args") or {}).get("description", "")).strip()
            ok, observation = None, None
        else:
            summary = self._tool_summaries.get(index, "")
            ok, observation = tool_flags(payload.get("content"))
        frame = {
            "turn_id": self._turn_id,
            "phase": payload.get("phase"),
            "index": index,
            "name": name,
            "summary": summary,
            "ok": ok,
            "observation": observation,
            "duplicate": bool(payload.get("duplicate", False)),
        }
        if name == "bash":
            frame["command"] = self._tool_commands.get(index, "")
            frame["description"] = self._tool_descriptions.get(index, "")
        if name == "run_pipeline" and payload.get("phase") != "start":
            # D82 §4.7：成功的 tickets open 带交接文本（core 模板生成；条件键，TS 侧 CONDITIONAL 登记）
            from pipeline.agent.tools import ticket_handoff

            handoff = ticket_handoff(payload.get("content"), self.host.root)
            if handoff is not None:
                frame["handoff"] = handoff
        self.channel.show("tool", frame)

    def _review(
        self,
        name: str,
        args: dict[str, Any],
        turn_id: str,
        status: Any,
        root: Path | None,
        approve_cb: Callable[[str, dict], Any] | None,
    ) -> Decision:
        from pipeline.agent.status_card import log_approval_decision

        if approve_cb is not None:
            raw = approve_cb(name, args)
            if isinstance(raw, bool):
                return Decision(ok=raw, provenance="auto")
            ok, reason = raw
            return Decision(
                ok=bool(ok), reason="" if ok else str(reason), provenance="auto" if ok else "human"
            )

        from pipeline.agent.tools import DRAFT_FILENAME

        verdict = review_tool_call(
            name, args, ep_dir=self.host.ep_dir, scope=self._scope,
            status=status, root=root, origin="model", turn_id=turn_id,
            draft_writes_this_turn=self._draft_writes,
            session_kind=self.host.session_kind, project_dir=self.host.project_dir,
        )
        is_draft_write = (
            name == "write_episode_file" and str(args.get("filename", "")) == DRAFT_FILENAME
        )
        if verdict.action == "reject":
            self.channel.show("stdout", {"text": f"[REJECT] {verdict.reason}"})
            return Decision(ok=False, reason=verdict.reason, provenance="precheck")
        if verdict.action == "allow":
            if is_draft_write:
                self._draft_writes += 1
            if verdict.echo:
                # 终端回显走 kind="echo"（协议下由 tool 帧的 summary 承担，不再重复一份）
                self.channel.show("echo", {"text": verdict.echo})
            return Decision(ok=True, provenance="card_free")

        request = verdict.request
        assert request is not None
        if verdict.allow_key is not None and verdict.allow_key in self.host.session_allow:
            # ADR-0030 决策 5：本会话已「都批准」过同类：不出卡，审批日志照记一行（可追溯谁放的行）
            log_approval_decision(
                self.host.ep_dir, name, str(request.fields.get("target", "")), "y",
                latency_s=None, channel="session_allow",
            )
            self.channel.show("echo", {"text": f"[本会话已批准同类] {verdict.allow_key}"})
            return Decision(ok=True, provenance="session_allow")
        self._record({
            "k": "request_opened", "request_id": request.request_id,
            "kind": request.kind, "title": request.title, "turn_id": turn_id,
        })
        try:
            answer = self.channel.ask(request)
        except KeyboardInterrupt:
            self._close_request(request, reason="voided", decision=None, latency_s=None,
                                cause="interrupted")
            raise
        approved = answer.decision in ("approve", SESSION_ALLOW_DECISION)
        if answer.decision == SESSION_ALLOW_DECISION and verdict.allow_key is not None:
            self.host.session_allow.add(verdict.allow_key)
        log_approval_decision(
            self.host.ep_dir, name, str(request.fields.get("target", "")),
            "y" if approved else "n", latency_s=answer.latency_s, channel=answer.channel,
        )
        self._close_request(request, reason="answered", decision=answer.decision,
                            latency_s=answer.latency_s, cause=None)
        if approved:
            if is_draft_write:
                self._draft_writes = 0  # 人看过超限卡并放行：再给一个免卡额度
            # D81 §4.4：卡上绑定只走人在卡上批准的这一支（session_allow / card_free / approve_cb 一律不带）
            return Decision(ok=True, provenance="human", bind=verdict.bind)
        self.channel.show("stdout", {"text": "[CANCEL] 已取消执行"})
        return Decision(
            ok=False, provenance="human",
            reason="人类拒绝执行该工具调用",
            feedback=answer.feedback,
        )

    def _close_request(
        self, request: HumanRequest, *, reason: str, decision: str | None,
        latency_s: float | None, cause: str | None,
    ) -> None:
        self._record({
            "k": "request_closed", "request_id": request.request_id, "reason": reason,
            "decision": decision, "channel": getattr(self.channel, "name", None),
            "latency_s": latency_s, "cause": cause,
        })

    def _ask_checkpoint(self, snapshot: dict[str, Any], turn_id: str) -> bool:
        """检查点卡（§2.3 第 6 条）：继续 → True；停止 → False。"""
        trigger = "模型调用" if snapshot.get("trigger") == "replies" else "工具执行"
        count = snapshot.get("llm_calls") if snapshot.get("trigger") == "replies" else snapshot.get("tool_executions")
        minutes = float(snapshot.get("elapsed_s", 0.0)) / 60.0
        card_text = (
            "┌─ [检查点] 本轮已连续 "
            f"{int(count or 0)} 次{trigger}：模型调用 {snapshot.get('llm_calls', 0)} 次，"
            f"工具执行 {snapshot.get('tool_executions', 0)} 次，用时 {minutes:.1f} 分钟\n"
            "│ 这不是任务预算：继续则计数归零；停止则先收尾总结。"
        )
        request = HumanRequest(
            request_id=new_request_id(), kind="checkpoint", turn_id=turn_id,
            title="检查点", card_text=card_text,
            fields={
                "llm_calls": snapshot.get("llm_calls", 0),
                "tool_executions": snapshot.get("tool_executions", 0),
                "elapsed_s": snapshot.get("elapsed_s", 0.0),
                "trigger": snapshot.get("trigger"),
            },
            options=("continue", "stop"), feedback_allowed=False,
        )
        self._record({
            "k": "request_opened", "request_id": request.request_id, "kind": "checkpoint",
            "title": request.title, "turn_id": turn_id,
        })
        try:
            answer = self.channel.ask(request)
        except KeyboardInterrupt:
            self._close_request(request, reason="voided", decision=None, latency_s=None,
                                cause="interrupted")
            raise
        self._close_request(request, reason="answered", decision=answer.decision,
                            latency_s=answer.latency_s, cause=None)
        return answer.decision == "continue"

    # ---- 抓取钩子（S6-R1 / §2.4.4） ----

    def _post_execute(self, name: str, args: dict[str, Any], outcome: dict[str, Any]) -> dict[str, Any]:
        if name != "acquire_propose" or not outcome.get("ok"):
            return outcome
        try:
            fetches = self._fetch_cards(outcome, _proposed_urls(args))
        except FetchHookInterrupted as stop:
            # D35：中断即整轮停——结果照常交回（带抓取记录），中断继续往上浮给工具循环
            stop.outcome = {**outcome, "result": {**(outcome.get("result") or {}),
                                                  "fetch": stop.outcome["fetch"]}}
            raise
        except SystemExit as exc:  # 纵深防御：钩子里的任何 SystemExit 都转成 notice
            self.channel.show("notice", {"level": "error", "code": "fetch_hook_error",
                                         "text": str(exc)})
            return outcome
        if fetches:
            outcome = {**outcome, "result": {**(outcome.get("result") or {}), "fetch": fetches}}
        return outcome

    def _fetch_cards(self, outcome: dict[str, Any], urls: list[str]) -> list[dict[str, Any]]:
        """§2.4.4 第 2 条：只抓**本次输入**的 URL（在清单中、不在台账；序号取清单里的 1-based N）。ADR-0030 起不出卡。"""
        data_root = Path(paths.DATA)
        base_data = Path(self.host.root or paths.ROOT) / "data"
        if not data_root.exists():
            self.channel.show("notice", {
                "level": "warn", "code": "fetch_disabled_data_unreachable",
                "text": f"素材目录不可达（{data_root}），本轮不抓取；提案结果照常。",
            })
            return []
        if os.path.realpath(data_root) != os.path.realpath(base_data):
            self.channel.show("notice", {
                "level": "warn", "code": "fetch_disabled_root_mismatch",
                "text": "会话根目录与素材目录不同源，本轮不抓取（避免写错台帐）。",
            })
            return []

        incoming = data_root / "library" / "incoming"
        candidates = _read_json_list(incoming / "candidates.json")
        ledger = _read_json_list(incoming / "fetched.json")
        fetched_urls = {str(entry.get("url")) for entry in ledger if entry.get("url")}

        # D34（M9 实测）：此前遍历**整份**清单，一次提案会把全部历史未抓候选逐张推给人
        position: dict[str, int] = {}
        for index, candidate in enumerate(candidates, 1):
            position.setdefault(str(candidate.get("url") or ""), index)
        fetches: list[dict[str, Any]] = []
        stopped = False
        for url in urls:  # 已按输入顺序去重
            index = position.get(url)
            if not url or index is None or url in fetched_urls:
                continue
            if stopped:
                # §2.2：中断之后其余候选一律 voided（不再抓，只记账）
                fetches.append({"no": index, "url": url, "decision": "voided", "ok": False,
                                "returncode": None, "message": "人类中断，未抓取",
                                "stdout_tail": ""})
                continue
            try:
                # ADR-0030 决策 4（D78）：抓取不再逐条出卡，本次提案里未抓的候选直接抓（审批日志记 channel=auto）
                fetches.append(self._auto_fetch(index, url))
            except _FetchStopped as stop:
                fetches.append(stop.record)
                stopped = True
        if stopped:
            raise FetchHookInterrupted({"fetch": fetches})
        return fetches

    def _auto_fetch(self, no: int, url: str) -> dict[str, Any]:
        from pipeline.agent.status_card import log_approval_decision

        record: dict[str, Any] = {"no": no, "url": url}
        current = _read_json_list(Path(paths.DATA) / "library" / "incoming" / "candidates.json")
        aligned = 0 < no <= len(current) and str(current[no - 1].get("url") or "") == url
        log_approval_decision(self.host.ep_dir, "acquire_fetch", f"#{no} {url}", "y",
                              latency_s=None, channel="auto")
        if not aligned:
            record.update({"decision": "misaligned", "ok": False, "returncode": None,
                           "message": "清单已变，序号不再指向该 URL，未抓取", "stdout_tail": ""})
            return record
        try:
            result = self._fetch_executor(no)
        except KeyboardInterrupt:
            # §2.2「抓取 job 中」：job 进程组已被杀（jobs.py），该候选 interrupted、余卡 voided
            record.update({"decision": "interrupted", "ok": False, "returncode": None,
                           "message": "人类中断，抓取 job 已被终止，产物可能不完整",
                           "stdout_tail": ""})
            raise _FetchStopped(record) from None
        record.update({
            "decision": "approved",
            "ok": bool(result.get("ok")),
            "returncode": result.get("returncode"),
            "message": str(result.get("message", "")),
            "stdout_tail": str(result.get("stdout_tail", ""))[-2000:],
        })
        return record

    def _fetch_executor(self, no: int) -> dict[str, Any]:
        if self.host.fetch_executor is not None:
            return self.host.fetch_executor(no)
        from pipeline.agent.tools import run_pipeline

        return run_pipeline(
            f"acquire fetch {no}", episode_dir=self.host.ep_dir, scope="asset", confirmed=True
        )

    # ---- 恢复 ----

    def memory_ack(self, target: str = "global") -> tuple[bool, str]:
        """`/memory ack` 与协议 `command{memory_ack}` 共用（Spec 7 §6.6 / S7-R1）。

        D81：`target="project"` 对账本企划的企划记忆（企划目录取自宿主绑定）。
        """
        from pipeline.agent import memory

        store = None
        if target == "project":
            if self.host.session_kind not in ("volume", "project") or self.host.project_dir is None:
                return False, "当前会话不属于任何企划，没有企划记忆可对账"
            store = memory.project_store(self.host.project_dir, self.host.root)

        captured: list[str] = []
        state: dict[str, float] = {"latency": 0.0}

        def confirm(text: str) -> tuple[bool, float]:
            captured.append(text)
            request = HumanRequest(
                request_id=new_request_id(), kind="memory_ack", turn_id=self._turn_id,
                title="记忆对账", card_text=text,
                fields={"text": text}, options=("approve", "reject"), feedback_allowed=False,
            )
            started = time.time()
            answer = self.channel.ask(request)
            state["latency"] = answer.latency_s or (time.time() - started)
            return answer.decision == "approve", state["latency"]

        ok = memory.ack_external(
            root=self.host.root,
            store=store,
            confirm=confirm,
            # 终端如实报 isatty；协议通道是 S7-R1 明确承认的部分反转（只能由人点击触发）
            is_tty=lambda: bool(getattr(self.channel, "is_tty", lambda: True)()),
        )
        if ok:
            from pipeline.agent.status_card import log_approval_decision

            log_approval_decision(
                self.host.ep_dir, "memory_ack", store.body if store is not None else "memory.md", "y",
                latency_s=state["latency"], emit_event=False, channel=self.channel.name,
            )
        return ok, (captured[0] if captured else "")


def _read_json_list(path: Path) -> list[dict[str, Any]]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    return [item for item in data if isinstance(item, dict)] if isinstance(data, list) else []


def resume_idea(host: "SessionHost") -> dict[str, Any] | None:
    """idea 会话启动即恒恢复（Spec 18 §3.1）：持锁后取 `_idea` 里最近的可恢复段。

    无需 `--continue` 与 sid——逻辑上只有一个选题会话。没有可恢复段返回 `None`（全新会话）；
    段损坏等返回非 `resumed` 的状态，调用方照期会话的口径开新会话。`host.sid` 由调用方按
    各自入口的既有口径处理（协议置 sid、终端留空写 `segment_start`），与期会话 `--continue` 一致。
    """
    if host.lease is None:
        return None
    target, _candidates = resume_target(list_sessions(host.lease.read()))
    if target is None:
        return None
    state = prepare_resume(host, target.sid)
    if state["status"] != "resumed":
        host.resume_state = None
    return state


# D81 §6.4：企划文件全文注入上限。依据同 memory.DIGEST_MAX_CHARS = 8000（不大于仓库里最重的单篇注入文档）；
# EGOIST 总纲原稿 5455 字走全文分支，踩坑全录 10075 字若并进来就走「机读部分 + 章节目录」分支
PROJECT_DOC_INJECT_MAX_CHARS = 8000


def render_project_doc(project_dir: Path, text: str) -> str:
    """`00-project.md` 的注入正文（§4.6）：≤ 上限注全文；超了注机读头 + 卷表 + 章节标题 + 读全文的指路，
    **不从中间截断**；解析失败注前 N 字原文 + 一行解析错误（人要看到问题，模型也要知道卷表不可信）。"""
    from pipeline import project as project_mod

    try:
        doc = project_mod.parse(text)
    except project_mod.ParseError as exc:
        return (f"[企划 · {project_dir.name}] 00-project.md 解析失败，卷表不可信：{exc}\n\n"
                f"{text[:PROJECT_DOC_INJECT_MAX_CHARS]}")
    head = f"[企划 · {doc.name}] 本企划的立项文件 00-project.md（卷表即分卷边界）"
    if len(text) <= PROJECT_DOC_INJECT_MAX_CHARS:
        return f"{head}\n\n{text}"
    lines = text.split("\n")
    first_h2 = next((i for i, ln in enumerate(lines) if ln.startswith("## ")), len(lines))
    at = next(i for i, ln in enumerate(lines) if ln.startswith("## ") and ln[3:].strip() == project_mod.VOLUME_TABLE_HEADING)
    end = next((i for i in range(at + 1, len(lines)) if lines[i].startswith("## ")), len(lines))
    parts = [*lines[:first_h2], *lines[at:end], "章节: " + " / ".join(doc.sections),
             '全文：read_artifact(path="00-project.md", area="project")']
    return f"{head}（全文超 {PROJECT_DOC_INJECT_MAX_CHARS} 字，只注机读部分与章节目录）\n\n" + "\n".join(parts)


def project_injections(project_dir: Path, root: Path | None) -> list[tuple[str, str, str | None]]:
    """[(相对仓库根的路径, 注入正文, 告警类别)]：企划文件、企划记忆（告警类别 unconfirmed / invalid / None）。"""
    from pipeline import project as project_mod
    from pipeline.agent import memory
    from pipeline.agent.tools import scrub_restricted

    key = project_mod.episode_key(project_dir, root)
    if key is None or len(key) != 1:
        return []
    rel = f"data/episodes/{key[0]}"
    out: list[tuple[str, str, str | None]] = []
    try:
        text = (Path(project_dir) / project_mod.PROJECT_FILE).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        text = None
    if text is not None:
        out.append((f"{rel}/{project_mod.PROJECT_FILE}", scrub_restricted(render_project_doc(Path(project_dir), text)), None))
    try:
        store = memory.project_store(project_dir, root)
    except PermissionError:
        return out
    mem = memory.render_injection(root, store=store)
    if mem is not None:
        warning = None
        if mem.startswith(memory.PROJECT_WARNING_PREFIX):
            warning = "unconfirmed" if "之外的改动" in mem else "invalid"
        out.append((f"{rel}/{memory.PROJECT_MEMORY_FILE}", scrub_restricted(mem), warning))
    return out


def _doc_shas(docs: list[Any]) -> list[dict[str, str]]:
    """注入时记下每份文档的路径与正文 sha（N56）：恢复后 `_reinject_changed` 据此比对、重注入改过的。

    sha 的算法必须与 `_reinject_changed` 比对时一致（`doc.content` 的 UTF-8 sha256）。
    """
    import hashlib

    return [{"path": d.rel_path, "sha256": hashlib.sha256(d.content.encode("utf-8")).hexdigest()} for d in docs]


def prepare_resume(host: "SessionHost", sid: str) -> dict[str, Any]:
    """`--continue` 启动时（**持锁之后**）读日志、跑修复、重建历史（§2.8）。

    返回 `{status, sid, messages, docs, step_key, resident_sha256, last_seq, last_reading}`；状态非
    `resumed` 时历史为空，调用方按 `corrupt` / `schema_unknown` 开新会话。
    已提交的整行一字不改：截断只发生在末尾残行，修复全是追加。
    """
    lease = host.lease
    if lease is None:
        return {"status": "no_session", "sid": sid, "messages": [], "docs": {},
                "step_key": None, "resident_sha256": None, "last_seq": 0}
    loaded = load_session(lease.read(), sid)
    if loaded.status != "ok":
        return {"status": loaded.status, "sid": sid, "messages": [], "docs": {},
                "step_key": None, "resident_sha256": None, "last_seq": loaded.last_seq}
    if loaded.torn_tail_bytes:
        lease.truncate_torn_tail()
        loaded = load_session(lease.read(), sid)
    # 修复行也必须是**本会话**的记录：先登记 sid 与续号。否则 append 写成 sid=null、seq 从 1
    # 重来，`load_session` 一按 sid 过滤，这批修复等于没写（TP-8 实测：恢复后仍发配对不全的历史）。
    lease.begin(sid, resumed_from_seq=int(loaded.last_seq))
    repairs = plan_repairs(loaded)
    if repairs:
        for record in repairs:
            lease.append(record)
        loaded = load_session(lease.read(), sid)

    docs: dict[str, str] = {}
    step_key: str | None = None
    resident_sha: str | None = None
    for record in loaded.records:
        if record.get("k") in ("session_start", "segment_start") and record.get("resident_sha256"):
            resident_sha = str(record["resident_sha256"])
        elif record.get("k") == "turn_start" and record.get("step_key"):
            step_key = str(record["step_key"])
        elif record.get("k") == "compaction":
            # 压掉的规程不再算「已注入」（审查 #1）：只留事件记下的、正文仍在保留尾部里的那些
            kept = set(record.get("docs_kept") or [])
            docs = {path: sha for path, sha in docs.items() if path in kept}
            step_key = None
        elif record.get("k") == "msg":
            for item in record.get("docs") or []:
                if item.get("path"):
                    docs[str(item["path"])] = str(item.get("sha256", ""))
    # D67：存量日志（D52 之前写入）里受限字面量未脱敏，恢复即撞出网断言回滚（董香二期实证：
    # tool 正文与两条 user 规程注入都有，只 scrub tool 是假修复）。恢复时全 role 过一道投影，
    # **不动盘**（append-only 不破）；对干净文本幂等。scrub 就在构造 state["messages"] 这一步
    # 发生、下游不再复制——_auto_compact 认 `convo is main_messages` 的对象同一性。
    from pipeline.agent.tools import scrub_restricted

    messages = [
        ({**m, "content": scrubbed} if scrubbed != m["content"] else m)
        if isinstance(m, dict) and isinstance(m.get("content"), str)
        and (scrubbed := scrub_restricted(m["content"])) != m["content"]
        else m
        for m in rebuild_messages(loaded)
    ]
    state = {
        "status": "resumed",
        "sid": sid,
        "records": loaded.records,
        "messages": messages,
        "docs": docs,
        "step_key": step_key,
        "resident_sha256": resident_sha,
        "last_seq": loaded.last_seq,
        "last_reading": last_context_reading(loaded),  # D64：(prompt_tokens, prompt_chars)，随 ready 帧下发
    }
    host.resume_state = state
    return state
