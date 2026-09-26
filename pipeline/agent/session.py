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
子会话与 idea 会话 `persist=False`（独立 messages、退出即丢，与现状一致），但共享
进程级的期租约、中断控制与检查点配置。
"""

from __future__ import annotations

import contextlib
import copy
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
from pipeline.agent.llm import Decision, LoopControl, dedup_key
from pipeline.agent.session_log import (
    SCHEMA as SCHEMA_NAME,
    EpisodeLease,
    SessionLocked,
    SessionLogBroken,
    load_session,
    plan_repairs,
    rebuild_messages,
)

REQUEST_ID_BYTES = 16

# 临界区工具（§2.2 第 3 条）：显式字面量，TG-5 比对它与「side_effect 为真的工具 − run_pipeline」。
CRITICAL_TOOLS = frozenset({"write_episode_file", "acquire_propose", "write_memory", "browser"})

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

    def request(self) -> None:
        """协议读者线程调用：把中断打到主线程（`pthread_kill`，E2 实测 0.5 s 内浮出）。"""
        signal.pthread_kill(self._main, signal.SIGINT)


@dataclass(frozen=True)
class ToolVerdict:
    """确定性工具审查结果（§4.3）：不打印、不提问，只给判断。"""

    action: Literal["reject", "allow", "ask"]
    reason: str = ""
    echo: str | None = None
    request: HumanRequest | None = None


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
) -> ToolVerdict:
    """工具审查（原 `cli._default_approve` 的确定性部分，Spec 9 §2.4.2/§2.6）。

    顺序与现状一致：未注册/越 scope → run_pipeline dry-run → write_memory dry-run →
    fail-closed `side_effect` 分流 → 弹卡。
    """
    from pipeline.agent.tools import TOOL_SCHEMAS, run_pipeline, tool_names_for_scope

    if name not in TOOL_SCHEMAS:
        return ToolVerdict("reject", reason=f"未注册的工具 '{name}'（工具清单不现场发明）")

    allowed = tool_names_for_scope(scope, root)
    if name not in allowed:
        return ToolVerdict(
            "reject", reason=f"工具 '{name}' 不在 {scope} scope 白名单内（当前放行: {allowed}）"
        )

    argv: list[str] | None = None
    if name == "run_pipeline":
        cmd_str = str(args.get("command", ""))
        outcome = run_pipeline(cmd_str, episode_dir=ep_dir, scope=scope, confirmed=False)
        if not outcome["ok"]:
            # 先走既有的白名单/dry-run 拒因（--force 全量覆盖等），文案不许被下面的模型规则截走
            return ToolVerdict("reject", reason=outcome["message"])
        argv = list(outcome["argv"])
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
            memory_plan = memory.plan_op(
                str(args.get("op", "")), args, root=root, episode_dir=ep_dir
            )
        except (ValueError, PermissionError, OSError) as exc:
            return ToolVerdict("reject", reason=f"{type(exc).__name__}: {exc}")
        if not memory_plan.requires_card:
            return ToolVerdict("allow", echo=f"[memory] {memory_plan.summary}")

    side_effect = TOOL_SCHEMAS[name].get("side_effect", True)
    if not side_effect:
        return ToolVerdict("allow", echo=_echo_line(name, args))

    return ToolVerdict("ask", request=_tool_request(name, args, argv, ep_dir, status, memory_plan))


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


def _echo_line(name: str, args: dict[str, Any]) -> str:
    """终端 `[tool]` 回显（与 Spec 1 cli.py:803-819 同一规则，扩展到全部工具）。"""
    listed = {
        "read_artifact": "path",
        "read_status": "episode",
        "search_notes": "query",
        "web_search": "query",
        "web_fetch": "url",
        "crawl": "url",
        "list_episodes": None,
    }
    if name in listed:
        key = listed[name]
        summary = "" if key is None else str(args.get(key, "")).strip()
    else:
        summary = json.dumps(args, ensure_ascii=False)[:60] if args else ""
    return _clean(f"[tool] {name} {summary}".strip())


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

    card = render_approval_card(
        name,
        args,
        argv=argv,
        stop_label=stop_label,
        episode_dir=ep_dir,
        memory_preview=render_plan_preview(memory_plan) if memory_plan else None,
    )
    body = card.split("\n")
    prompt = body.pop() if body and body[-1].strip().startswith("└─") else ""
    fields = {
        "tool": name,
        "args": _jsonable(args),
        "argv": argv or [],
        "target": _target_of(memory_plan, argv, args),
        "stop_label": stop_label,
        "danger": _clean(prompt or "无"),
        "memory_preview": list(render_plan_preview(memory_plan)) if memory_plan else [],
    }
    return HumanRequest(
        request_id=new_request_id(),
        kind="tool_call",
        turn_id=None,
        title=f"工具审查: {name}",
        card_text="\n".join(body),
        fields=fields,
        options=("approve", "reject"),
        feedback_allowed=True,
    )


def _jsonable(value: Any) -> Any:
    try:
        json.dumps(value, ensure_ascii=False)
        return value
    except (TypeError, ValueError):
        return str(value)


class SessionHost:
    """进程内每期一个：持有租约、中断控制、主会话对象与其 messages 列表对象（§2.6）。"""

    def __init__(
        self,
        ep_dir: Path | str | None,
        *,
        root: Path | None = None,
        channel: HumanChannel,
        ephemeral: bool = False,
    ) -> None:
        self.ep_dir = Path(ep_dir).resolve() if ep_dir is not None else None
        self.root = root
        self.channel = channel
        self.ephemeral = ephemeral
        self.interrupt = TurnInterrupt()
        self.lease: EpisodeLease | None = None
        self.lease_error: str | None = None
        self.main_messages: list[dict[str, Any]] | None = None
        self.sid: str | None = None
        # 抓取执行器注入点（§2.4.4 第 4 条）：生产默认走 run_pipeline，测试注入替身
        self.fetch_executor: Callable[[int], dict[str, Any]] | None = None
        self._main: AgentSession | None = None
        self._scope_override: str | None = None
        self.resume_state: dict[str, Any] | None = None

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
        """懒取租约（§2.5）：首个 agent 回合取，进程生命期持有。失败只记原因，不抛。"""
        if self.ephemeral or self.ep_dir is None:
            return None
        if self.lease is not None or self.lease_error is not None:
            return self.lease
        try:
            self.lease = EpisodeLease.acquire(self.ep_dir)
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
        )


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
        self._scope = scope_mode
        self._log_broken = False
        self._last_records: list[dict[str, Any]] = []
        self._injected_docs: dict[str, str] = {}

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
        self._record(record, origin)
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
    ) -> dict[str, Any]:
        """跑一轮（§4.7 的处理顺序）。"""
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
        self._turn_id = secrets.token_hex(8)
        effective_scope = self._scope_override or scope
        # 本轮的工具审查一律用调用方给的 scope（终端每轮热推导，M11 锚点不动）；
        # 绝不在这里从 status 重新推一份——那会把 pipeline scope 的回合按产物阶段错配成 creative。
        self._scope = effective_scope
        with self.interrupt.installed():
            snapshot = (len(messages), copy.deepcopy(tracker))
            outcome: dict[str, Any] | None = None
            with self.interrupt.absorb():
                self._open_session(effective_scope, tracker, root)
                self._record({
                    "k": "turn_start",
                    "scope": effective_scope,
                    "step_key": step_key_of(status.current_step if status else None),
                })
            try:
                degraded = self._assemble(
                    messages, tracker, status, effective_scope, line, root=root
                )
                if degraded is not None:
                    self.channel.show("stdout", {"text": f"\n{degraded['content']}"})
                    self._finish_turn(snapshot, tracker, {"stopped": "degraded"}, messages)
                    return {"stopped": "degraded", "messages": messages, "final": degraded}

                outcome = llm_module.run_tool_loop(
                    messages,
                    ctx=self._tool_context(effective_scope, root),
                    # approve 显式传 None（裸循环适配器不用），control 才是生产路径。
                    # 现有多处打桩的签名带位置参数 approve，不传就当场 TypeError。
                    approve=None,
                    control=self._control(
                        turn_id=self._turn_id, status=status, root=root, approve_cb=approve_cb
                    ),
                )
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
        with self.interrupt.absorb():
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
                "recovered": False,
            })
            if self._log_broken:
                self.channel.show("notice", {
                    "level": "error", "code": "session_log_broken",
                    "text": "会话记录已损坏，本轮不再落盘。请开新会话（不加 --continue）。",
                })

    def _rollback(self, messages: list[dict[str, Any]], snapshot: tuple, tracker: Any) -> None:
        """回滚 = 恢复到回合开始时的快照（§2.3 第 3 条）。"""
        length, saved_tracker = snapshot
        del messages[length:]
        for name in (
            "injected_paths", "active_step_key", "resident_prompt", "active_scope",
            "memory_warn_injected", "memory_warn_printed",
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
        )
        from pipeline.agent.llm import load_llm_config, local_directive_message
        from pipeline.agent.status_card import build_idea_card

        if tracker.active_scope != scope or not tracker.resident_prompt:
            tracker.resident_prompt = assemble_resident_prompt(
                scope, root=root, extra_prompt=self.extra_prompt
            ).content
            tracker.active_scope = scope

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
                self._commit({"role": "user", "content": injection}, "injection")
                tracker.injected_paths.update(d.rel_path for d in step_docs)
            tracker.active_step_key = step_key
        else:
            if step_key != tracker.active_step_key:
                new_docs = [d for d in step_docs if d.rel_path not in tracker.injected_paths]
                if new_docs:
                    injection = render_step_injection(
                        new_docs, step_name=status.current_step if status else None
                    )
                    self._commit({"role": "user", "content": injection}, "injection")
                    tracker.injected_paths.update(d.rel_path for d in new_docs)
                tracker.active_step_key = step_key
            # 刷新状态卡：整段替换 messages[0]（一轮之内不再变）
            messages[0] = {
                "role": "system",
                "content": tracker.get_initial_system_prompt(status_card),
            }

        self._inject_memory(messages, tracker, scope, root=root)
        self._reinject_changed(messages, tracker, scope, status, root=root)

        if load_llm_config(root) is None:
            return local_directive_message(scope, "缺少 config/agent.json 或环境变量密钥")
        self._commit({"role": "user", "content": line}, "user")
        return None

    def _status_card(self, scope: str, status: Any) -> str:
        from pipeline.agent.status_card import build_status_card

        return build_status_card(self.host.ep_dir, status, scope=scope)

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
            self._commit({"role": "user", "content": doc.content}, "memory")
            tracker.injected_paths.add(doc.rel_path)
        elif not tracker.memory_warn_injected:
            self._commit({"role": "user", "content": doc.content}, "memory")
            tracker.memory_warn_injected = True
            if not tracker.memory_warn_printed:
                self.channel.show("stderr", {"text": f"[WARN] {doc.content}"})
                tracker.memory_warn_printed = True

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
        )

        candidates = []
        step_key = step_key_of(status.current_step if status else None)
        for path in resolve_step_docs(scope, step_key, root=root):
            doc = load_injected_doc(path.as_posix(), root=root)
            if doc is not None:
                candidates.append(doc)
        memory_doc = resolve_memory_injection(scope, root=root)
        if memory_doc is not None:
            candidates.append(memory_doc[0])
        for doc in candidates:
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
        self._injected_docs = {}

    def _tool_context(self, scope: str, root: Path | None):
        from pipeline.agent.tools import ToolContext

        # 期目录一律取自**包装收到的参数**，从不取自 SessionHost（五轮自查 / MUT-60）
        return ToolContext(scope=scope, episode_dir=self.host.ep_dir, root=root)

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
            on_exec_started=lambda call_id, name: self._record({
                "k": "tool_exec_started", "tool_call_id": call_id, "name": name, "turn_id": turn_id,
            }),
            critical_tools=CRITICAL_TOOLS,
        )

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

        verdict = review_tool_call(
            name, args, ep_dir=self.host.ep_dir, scope=self._scope,
            status=status, root=root, origin="model", turn_id=turn_id,
        )
        if verdict.action == "reject":
            self.channel.show("stdout", {"text": f"[REJECT] {verdict.reason}"})
            return Decision(ok=False, reason=verdict.reason, provenance="precheck")
        if verdict.action == "allow":
            if verdict.echo:
                self.channel.show("tool", {"echo": verdict.echo})
            return Decision(ok=True, provenance="card_free")

        request = verdict.request
        assert request is not None
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
        approved = answer.decision == "approve"
        log_approval_decision(
            self.host.ep_dir, name, str(request.fields.get("target", "")),
            "y" if approved else "n", latency_s=answer.latency_s, channel=answer.channel,
        )
        self._close_request(request, reason="answered", decision=answer.decision,
                            latency_s=answer.latency_s, cause=None)
        if approved:
            return Decision(ok=True, provenance="human")
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
            fetches = self._fetch_cards(outcome)
        except SystemExit as exc:  # 纵深防御：钩子里的任何 SystemExit 都转成 notice
            self.channel.show("notice", {"level": "error", "code": "fetch_hook_error",
                                         "text": str(exc)})
            return outcome
        if fetches:
            outcome = {**outcome, "result": {**(outcome.get("result") or {}), "fetch": fetches}}
        return outcome

    def _fetch_cards(self, outcome: dict[str, Any]) -> list[dict[str, Any]]:
        data_root = Path(paths.DATA)
        base_data = Path(self.host.root or paths.ROOT) / "data"
        if not data_root.exists():
            self.channel.show("notice", {
                "level": "warn", "code": "fetch_disabled_data_unreachable",
                "text": f"素材目录不可达（{data_root}），本轮不出抓取卡；提案结果照常。",
            })
            return []
        if os.path.realpath(data_root) != os.path.realpath(base_data):
            self.channel.show("notice", {
                "level": "warn", "code": "fetch_disabled_root_mismatch",
                "text": "会话根目录与素材目录不同源，本轮不出抓取卡（避免写错台帐）。",
            })
            return []

        incoming = data_root / "library" / "incoming"
        candidates = _read_json_list(incoming / "candidates.json")
        ledger = _read_json_list(incoming / "fetched.json")
        fetched_urls = {str(entry.get("url")) for entry in ledger if entry.get("url")}

        fetches: list[dict[str, Any]] = []
        seen: set[str] = set()
        for index, candidate in enumerate(candidates, 1):
            url = str(candidate.get("url") or "")
            if not url or url in seen or url in fetched_urls:
                continue
            seen.add(url)
            request = HumanRequest(
                request_id=new_request_id(),
                kind="fetch",
                turn_id=self._turn_id,
                title=f"素材抓取候选 #{index}",
                card_text=_fetch_card(index, candidate),
                fields={
                    "no": index,
                    "title": _clean(candidate.get("title")),
                    "url": url,
                    "type": _clean(candidate.get("type")),
                    "source": _clean(candidate.get("source")),
                    "why": _clean(candidate.get("why")),
                    "expected_dur": candidate.get("expected_dur"),
                },
                options=("approve", "reject"),
                feedback_allowed=False,
            )
            fetches.append(self._ask_fetch(request, index, url))
        return fetches

    def _ask_fetch(self, request: HumanRequest, no: int, url: str) -> dict[str, Any]:
        from pipeline.agent.status_card import log_approval_decision

        record: dict[str, Any] = {"no": no, "url": url}
        self._record({
            "k": "request_opened", "request_id": request.request_id, "kind": "fetch",
            "title": request.title, "turn_id": self._turn_id,
        })
        try:
            answer = self.channel.ask(request)
        except KeyboardInterrupt:
            self._close_request(request, reason="voided", decision=None, latency_s=None,
                                cause="interrupted")
            record.update({"decision": "voided", "ok": False, "returncode": None,
                           "message": "人类中断，未抓取", "stdout_tail": ""})
            return record
        self._close_request(request, reason="answered", decision=answer.decision,
                            latency_s=answer.latency_s, cause=None)
        if not answer.decision == "approve":
            log_approval_decision(self.host.ep_dir, "acquire_fetch", f"#{no} {url}", "n",
                                  latency_s=answer.latency_s, channel=answer.channel)
            record.update({"decision": "rejected", "ok": False, "returncode": None,
                           "message": "人类拒绝抓取", "stdout_tail": ""})
            return record

        current = _read_json_list(Path(paths.DATA) / "library" / "incoming" / "candidates.json")
        aligned = 0 < no <= len(current) and str(current[no - 1].get("url") or "") == url
        log_approval_decision(self.host.ep_dir, "acquire_fetch", f"#{no} {url}", "y",
                              latency_s=answer.latency_s, channel=answer.channel)
        if not aligned:
            record.update({"decision": "misaligned", "ok": False, "returncode": None,
                           "message": "清单已变，序号不再指向该 URL，未抓取", "stdout_tail": ""})
            return record
        result = self._fetch_executor(no)
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

    def memory_ack(self) -> tuple[bool, str]:
        """`/memory ack` 与协议 `command{memory_ack}` 共用（Spec 7 §6.6 / S7-R1）。"""
        from pipeline.agent import memory

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
            confirm=confirm,
            # 终端如实报 isatty；协议通道是 S7-R1 明确承认的部分反转（只能由人点击触发）
            is_tty=lambda: bool(getattr(self.channel, "is_tty", lambda: True)()),
        )
        if ok:
            from pipeline.agent.status_card import log_approval_decision

            log_approval_decision(
                self.host.ep_dir, "memory_ack", "memory.md", "y",
                latency_s=state["latency"], emit_event=False, channel=self.channel.name,
            )
        return ok, (captured[0] if captured else "")


def _read_json_list(path: Path) -> list[dict[str, Any]]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    return [item for item in data if isinstance(item, dict)] if isinstance(data, list) else []


def _fetch_card(no: int, candidate: dict[str, Any]) -> str:
    """§3.2 的抓取卡文案（每字段一行，无「全部批准」）。"""
    dur = candidate.get("expected_dur")
    dur_text = f"{dur}s" if isinstance(dur, (int, float)) else "未知"
    return (
        f"┌─ [素材抓取] 候选 #{no}（逐条人审，ADR-0021 / Spec 6）\n"
        f"│ 标题: {_clean(candidate.get('title'))} │ URL: {_clean(candidate.get('url'))}\n"
        f"│ 类型: {_clean(candidate.get('type'))} | 来源: {_clean(candidate.get('source'))}\n"
        f"│ why: {_clean(candidate.get('why'))}\n"
        f"│ 预计时长: {dur_text}"
    )


def prepare_resume(host: "SessionHost", sid: str) -> dict[str, Any]:
    """`--continue` 启动时（**持锁之后**）读日志、跑修复、重建历史（§2.8）。

    返回 `{status, sid, messages, docs, step_key, resident_sha256, last_seq}`；状态非
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
        elif record.get("k") == "msg":
            for item in record.get("docs") or []:
                if item.get("path"):
                    docs[str(item["path"])] = str(item.get("sha256", ""))
    state = {
        "status": "resumed",
        "sid": sid,
        "messages": rebuild_messages(loaded),
        "docs": docs,
        "step_key": step_key,
        "resident_sha256": resident_sha,
        "last_seq": loaded.last_seq,
    }
    host.resume_state = state
    return state
