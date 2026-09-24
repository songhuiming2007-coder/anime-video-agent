"""pipeline.approvals: 停机点 pending approval 对象化（Spec 3 / ADR-0020 §3）。

纪律：
1. ack 永远是显式动作；本模块没有任何自动批准路径（解封物对齐是事后登记，
   且必须过 §2.3 失效判据双闸，见红队一轮 B1）；
2. 对象状态永不替代物理产物闸门（02-diff.patch / 04-clips.approved.json）；
3. 绝不自动 mkdir 期目录；期目录不可达时全部 fail-silent（paths.py:131-158 铁律）；
4. 顶层仅 stdlib + pipeline.paths + pipeline.align（纯 stdlib 叶子模块，行 14-19 核实）；
   jobs / status / status_card 一律函数级延迟 import。
"""

from __future__ import annotations

import contextlib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
import fcntl
import json
from pathlib import Path
import shutil
import threading
import time
from typing import TYPE_CHECKING, Any, Iterator, Literal
import uuid

from pipeline import align, paths

if TYPE_CHECKING:
    from pipeline.status import EpisodeStatus

ApprovalType = Literal["02.5", "03.5", "05", "09"]

# 人类停机点唯一真源（由 cli.py 下沉至此，cli.py 反向 import，不另造第二份枚举）
HUMAN_STOPS: set[str] = {"02.5", "03.5", "05", "09"}


def human_stop_of(current_step: str) -> ApprovalType | None:
    """current_step 字符串 → 停机点标签（HUMAN_STOPS 的唯一消费者）。

    「02.5 人审改稿」「03.5 配音顺听 / 04 排片」「05 审时间码」「09 人工发布」
    都是标签前缀形态；非停机点返回 None。
    """
    for stop in sorted(HUMAN_STOPS, key=len, reverse=True):
        if current_step.startswith(stop):
            return stop  # type: ignore[return-value]
    return None


# 停机点类型 → 关联产物（指纹对象）与解封物
_STOP_ARTIFACTS: dict[str, tuple[str, ...]] = {
    "02.5": ("02-script.md",),
    "03.5": ("03-audio/manifest.json",),
    "05": ("04-clips.json",),
    "09": ("05-final.mp4", "07-titles.md"),
}
_STOP_GATE: dict[str, str | None] = {
    "02.5": "02-diff.patch",
    "03.5": None,
    "05": "04-clips.approved.json",
    "09": None,
}
_STOP_NOTES: dict[str, str] = {
    "05": "批准须先显式执行 python -m pipeline.review <期> --approve",
}
_STOP_NAMES: dict[str, str] = {
    "02.5": "人审改稿",
    "03.5": "配音顺听",
    "05": "审时间码",
    "09": "人工发布",
}
_MAX_APPROVALS_PER_EPISODE = 64

_THREAD_LOCK = threading.Lock()
_EPISODES_ROOT_CACHE: Path | None = None
_EPISODES_ROOT_FOR_BASE: Path | None = None


class ApprovalStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    SUPERSEDED = "superseded"


class ApprovalError(RuntimeError):
    """approval 领域受控异常（红队一轮 m3）：交互层捕获后打印受控提示，
    严禁原生 ValueError 裸抛穿透 REPL。"""


def _utc_now_iso() -> str:
    """统一 UTC 时间戳：ISO 8601 带微秒与 Z 标记（与 Spec 2 _utc_now_iso 口径一致）。"""
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_iso_seconds(ts: str) -> float | None:
    if not ts:
        return None
    try:
        norm = ts[:-1] + "+00:00" if ts.endswith("Z") else ts
        return datetime.fromisoformat(norm).timestamp()
    except Exception:
        return None


def _validate_feedback(feedback: dict[str, str] | None) -> dict[str, str]:
    """校验 rejected 结构化反馈 schema（§2.4）：target 与 problem 必填非空字符串。"""
    if not isinstance(feedback, dict):
        raise ApprovalError("rejected 反馈必须为包含 target 与 problem 的字典")
    target = feedback.get("target")
    problem = feedback.get("problem")
    if not isinstance(target, str) or not target.strip():
        raise ApprovalError("rejected 反馈缺少非空的 target 字段（哪段）")
    if not isinstance(problem, str) or not problem.strip():
        raise ApprovalError("rejected 反馈缺少非空的 problem 字段（什么问题）")
    return {"target": target, "problem": problem}


@dataclass
class ArtifactFingerprint:
    path: str            # 期目录相对路径，如 "04-clips.json"
    size: int
    mtime_ns: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "size": int(self.size),
            "mtime_ns": int(self.mtime_ns),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ArtifactFingerprint:
        return cls(
            path=str(data["path"]),
            size=int(data["size"]),
            mtime_ns=int(data["mtime_ns"]),
        )


@dataclass
class Approval:
    approval_id: str                     # 格式：appr_<epoch_ms>_<hex4>
    episode: str                         # 期相对路径（如 "EGOIST/01-Live"），算法见 §4.1 _episode_repr
    type: ApprovalType                   # 停机点类型，四值封闭集合
    status: ApprovalStatus = ApprovalStatus.PENDING
    artifacts: list[ArtifactFingerprint] = field(default_factory=list)
    options: list[str] = field(default_factory=lambda: ["approve", "reject"])
    created_at: str = ""                 # ISO 8601 UTC，Z 标记
    resolved_at: str | None = None
    resolved_by: str | None = None       # "repl" | "cli" | "artifact"（惰性对齐）
    confirmed_by: str | None = None      # v0.4 S3-R7：artifact 对齐后人的显式确认来源（"repl" | "cli"）
    confirmed_at: str | None = None      # v0.4 S3-R7：显式确认时刻（ISO 8601 UTC，Z 标记）
    feedback: dict[str, str] | None = None  # rejected 时必填：{"target": ..., "problem": ...}
    note: str = ""                       # 05 携带 review --approve 提示等

    def __post_init__(self) -> None:
        if not isinstance(self.status, ApprovalStatus):
            try:
                self.status = ApprovalStatus(self.status)
            except ValueError as exc:
                raise ApprovalError(f"非法 Approval 状态: {self.status!r}") from exc
        if self.type not in HUMAN_STOPS:
            raise ApprovalError(f"非法停机点类型: {self.type!r}")
        self.artifacts = [
            a if isinstance(a, ArtifactFingerprint) else ArtifactFingerprint.from_dict(a)
            for a in (self.artifacts or [])
        ]
        if not self.created_at:
            self.created_at = _utc_now_iso()
        self.validate()

    def validate(self) -> None:
        """校验 §3.1 对象级不变量。"""
        if self.status == ApprovalStatus.REJECTED:
            self.feedback = _validate_feedback(self.feedback)
        elif self.feedback is not None:
            raise ApprovalError("非 rejected 状态严禁携带 feedback 字段")

        if self.confirmed_by is not None:
            if self.status != ApprovalStatus.APPROVED or self.resolved_by != "artifact":
                raise ApprovalError(
                    "confirmed_by 非空时必须满足 status == APPROVED 且 resolved_by == 'artifact'"
                )

    def to_dict(self) -> dict[str, Any]:
        return {
            "approval_id": self.approval_id,
            "episode": self.episode,
            "type": self.type,
            "status": self.status.value,
            "artifacts": [a.to_dict() for a in self.artifacts],
            "options": list(self.options),
            "created_at": self.created_at,
            "resolved_at": self.resolved_at,
            "resolved_by": self.resolved_by,
            "confirmed_by": self.confirmed_by,
            "confirmed_at": self.confirmed_at,
            "feedback": dict(self.feedback) if self.feedback is not None else None,
            "note": self.note,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Approval:
        return cls(
            approval_id=str(data["approval_id"]),
            episode=str(data["episode"]),
            type=data["type"],
            status=ApprovalStatus(data["status"]),
            artifacts=[ArtifactFingerprint.from_dict(a) for a in data.get("artifacts", [])],
            options=list(data.get("options") or ["approve", "reject"]),
            created_at=str(data.get("created_at") or ""),
            resolved_at=data.get("resolved_at"),
            resolved_by=data.get("resolved_by"),
            confirmed_by=data.get("confirmed_by"),
            confirmed_at=data.get("confirmed_at"),
            feedback=dict(data["feedback"]) if data.get("feedback") is not None else None,
            note=str(data.get("note") or ""),
        )


@dataclass(frozen=True)
class Transition:
    """单次 _self_heal_locked 内部发生的状态转移记录（v0.5 S3-R10 / S3-R11）。"""

    approval_id: str
    stop: ApprovalType
    from_status: ApprovalStatus
    to_status: ApprovalStatus
    reason: str  # "fingerprint_drift" | "artifact" | "superseded_by_new" | "stage_passed"


@dataclass
class _StoreState:
    items: list[Approval]
    dirty: bool = False
    backup_failed: bool = False


def _raise_if_store_unwritable(state: _StoreState) -> None:
    """坏条目备份失败时 store 处于不可安全写回的状态（§8 M5：备份成功后才置 dirty）。

    写路径（approve / reject / ensure_pending）必须立刻中止：否则会带着「内存里改了、
    盘上没写」的假成功返回（S10 review 🟡-1：CLI 退出 0、记一行 y、发出事件，对象却
    仍是 PENDING）。读路径（list_pending / 状态卡）保持失败静默。
    """
    if state.backup_failed:
        raise ApprovalError(
            "approvals_store.json 存在损坏或违反不变量的条目且备份失败（_agent/ 写权限或磁盘异常），"
            "本次审批未落盘，请先修复存储后重试"
        )


def _get_episodes_root() -> Path | None:
    global _EPISODES_ROOT_CACHE, _EPISODES_ROOT_FOR_BASE
    if _EPISODES_ROOT_FOR_BASE != paths.ROOT:
        _EPISODES_ROOT_FOR_BASE = paths.ROOT
        try:
            _EPISODES_ROOT_CACHE = (paths.ROOT / "data" / "episodes").resolve()
        except Exception:
            _EPISODES_ROOT_CACHE = None
    return _EPISODES_ROOT_CACHE


def _episode_repr(ep_dir: Path) -> str:
    """期目录 → episode 相对路径字符串（§4.1 M6，镜像 Spec 2 emit 真子集判断）。"""
    try:
        ep_raw = Path(ep_dir)
        ep_resolved = ep_raw.resolve()
        root_resolved = _get_episodes_root()
        raw_root = paths.ROOT / "data" / "episodes"
        for ep_cand, root_cand in ((ep_resolved, root_resolved), (ep_raw, raw_root)):
            if (
                root_cand is not None
                and ep_cand != root_cand
                and root_cand in ep_cand.parents
            ):
                return ep_cand.relative_to(root_cand).as_posix()
        return ep_raw.name
    except Exception:
        return Path(ep_dir).name


def _fingerprint(ep_dir: Path, rel_paths: tuple[str, ...]) -> list[ArtifactFingerprint]:
    """采集关联产物指纹（size, mtime_ns）；文件缺失记 (-1, -1)。"""
    out: list[ArtifactFingerprint] = []
    for rel in rel_paths:
        p = ep_dir / rel
        try:
            if p.exists():
                st = p.stat()
                out.append(ArtifactFingerprint(path=rel, size=st.st_size, mtime_ns=st.st_mtime_ns))
            else:
                out.append(ArtifactFingerprint(path=rel, size=-1, mtime_ns=-1))
        except Exception:
            out.append(ArtifactFingerprint(path=rel, size=-1, mtime_ns=-1))
    return out


def _fingerprints_equal(
    fp_a: list[ArtifactFingerprint],
    fp_b: list[ArtifactFingerprint],
) -> bool:
    if len(fp_a) != len(fp_b):
        return False
    return all(
        a.path == b.path and a.size == b.size and a.mtime_ns == b.mtime_ns
        for a, b in zip(fp_a, fp_b)
    )


def _gate_valid(ep_dir: Path, stop: str) -> bool:
    """§2.3 B1 解封物对齐双闸：时序单调性 ∧ 内容一致性。"""
    gate_rel = _STOP_GATE.get(stop)
    if gate_rel is None:
        return True
    gate_path = ep_dir / gate_rel
    try:
        if not gate_path.exists():
            return False
        gate_st = gate_path.stat()
        for art_rel in _STOP_ARTIFACTS.get(stop, ()):
            art_path = ep_dir / art_rel
            if not art_path.exists():
                return False
            art_st = art_path.stat()
            if gate_st.st_mtime_ns < art_st.st_mtime_ns:
                return False

        if stop == "02.5":
            return gate_st.st_size > 0
        if stop == "05":
            clips_path = ep_dir / "04-clips.json"
            json.loads(clips_path.read_text(encoding="utf-8"))
            json.loads(gate_path.read_text(encoding="utf-8"))
            return not align.has_clips_approved_diff(ep_dir)
        return True
    except Exception:
        return False


def _emit(event_name: str, payload: dict[str, Any], episode_dir: Path | None) -> None:
    """函数级延迟 import pipeline.jobs 发射事件；ImportError 或运行时故障静默降级（§4.1/§6.1）。"""
    try:
        from pipeline.jobs import EventType, get_publisher

        evt_type = (
            EventType.APPROVAL_REQUESTED
            if event_name == "approval_requested"
            else EventType.APPROVAL_RESOLVED
        )
        get_publisher().emit(evt_type, payload, episode_dir=episode_dir)
    except Exception:
        pass


def _log_decision(
    ep_dir: Path,
    source: str,
    target: str,
    decision: str,
    latency_s: float | None,
    *,
    emit_event: bool = False,
) -> None:
    """函数级延迟 import status_card.log_approval_decision 完成记账（§2.5/§4.1 R-3）。"""
    try:
        from pipeline.agent.status_card import log_approval_decision

        log_approval_decision(
            ep_dir,
            source,
            target,
            decision,
            latency_s=latency_s,
            emit_event=emit_event,
        )
    except Exception:
        pass


def _evict_for_new_pending_locked(store: list[Approval]) -> bool:
    """§3.2 M7 容量上限与淘汰阶梯：SUPERSEDED → REJECTED → APPROVED（最旧先）。

    PENDING 永不进淘汰；若 64 条全为 PENDING 则打印 WARN 并返回 False（拒绝新建）。
    """
    while len(store) >= _MAX_APPROVALS_PER_EPISODE:
        victim_idx: int | None = None
        for tier in (
            ApprovalStatus.SUPERSEDED,
            ApprovalStatus.REJECTED,
            ApprovalStatus.APPROVED,
        ):
            for idx, item in enumerate(store):
                if item.status == tier:
                    victim_idx = idx
                    break
            if victim_idx is not None:
                break
        if victim_idx is None:
            print(
                f"[WARN] approvals_store 已达 {_MAX_APPROVALS_PER_EPISODE} 条上限且全为 PENDING，拒绝新建"
            )
            return False
        store.pop(victim_idx)
    return True


def _load_store_items_with_recovery(store_path: Path) -> tuple[list[Approval], bool, bool]:
    """加载 approvals_store.json（M4-2 & M5 修复：遇损坏文件或非法条目时先备份至 corrupt-<ts>.json，
    仅在备份成功后才返回 (loaded, True, False) 置 dirty=True 触发写回清理；
    若备份失败则返回 (loaded, False, True) 严禁覆写原文件）。"""
    if not store_path.exists():
        return ([], False, False)
    loaded: list[Approval] = []
    had_corrupt = False
    try:
        raw = json.loads(store_path.read_text(encoding="utf-8"))
        if not isinstance(raw, list):
            had_corrupt = True
        else:
            for entry in raw:
                if not isinstance(entry, dict):
                    had_corrupt = True
                    continue
                try:
                    loaded.append(Approval.from_dict(entry))
                except Exception:
                    had_corrupt = True
    except Exception:
        had_corrupt = True

    should_dirty = False
    backup_failed = False
    if had_corrupt:
        ts_ms = int(time.time() * 1000)
        corrupt_backup = store_path.with_name(f"approvals_store.corrupt-{ts_ms}.json")
        try:
            shutil.copy2(store_path, corrupt_backup)
            should_dirty = True
            print(
                f"[WARN] approvals_store.json 存在损坏或违反不变量的条目，已备份原文件至 {corrupt_backup.name}"
            )
        except Exception as exc:
            backup_failed = True
            print(
                f"[WARN] approvals_store.json 存在损坏或违反不变量的条目，但备份失败（{exc}），跳过写回以保护原文件"
            )
    return (loaded, should_dirty, backup_failed)


@contextlib.contextmanager
def _locked_approvals(
    ep_dir: Path | str,
    *,
    silent_write_error: bool = False,
) -> Iterator[_StoreState]:
    """读-改-写全程排他锁上下文管理器（§2.1 M1 & §4.1 v0.5 S3-R10 一次进锁）。

    期目录不存在时 fail-silent，绝不自动 mkdir 期目录（paths.py:131-158 铁律）。
    """
    ep_path = Path(ep_dir) if ep_dir else None
    if ep_path is None or not ep_path.exists() or not ep_path.is_dir():
        yield _StoreState(items=[], dirty=False)
        return

    with _THREAD_LOCK:
        agent_dir = ep_path / "_agent"
        lock_path = agent_dir / "approvals_store.lock"
        store_path = agent_dir / "approvals_store.json"
        lock_fh = None
        try:
            agent_dir.mkdir(parents=True, exist_ok=True)
            lock_fh = lock_path.open("a", encoding="utf-8")
            fcntl.flock(lock_fh.fileno(), fcntl.LOCK_EX)
        except Exception:
            if lock_fh is not None:
                with contextlib.suppress(Exception):
                    lock_fh.close()
            if silent_write_error:
                yield _StoreState(items=[], dirty=False)
                return
            raise

        loaded, should_dirty, backup_failed = _load_store_items_with_recovery(store_path)
        state = _StoreState(items=loaded, dirty=should_dirty, backup_failed=backup_failed)
        try:
            yield state
        finally:
            if state.dirty and not backup_failed:
                try:
                    payload = (
                        json.dumps(
                            [item.to_dict() for item in state.items],
                            ensure_ascii=False,
                            indent=2,
                        )
                        + "\n"
                    )
                    paths.atomic_write(store_path, payload)
                except Exception:
                    if not silent_write_error:
                        raise
            if lock_fh is not None:
                with contextlib.suppress(Exception):
                    fcntl.flock(lock_fh.fileno(), fcntl.LOCK_UN)
                with contextlib.suppress(Exception):
                    lock_fh.close()


def _ensure_pending_locked(
    state: _StoreState,
    ep_dir: Path,
    stop: ApprovalType,
    transitions: list[Transition] | None = None,
    pending_events: list[tuple[str, dict[str, Any]]] | None = None,
) -> Approval | None:
    """在已持锁的 state 上幂等补建指定停机点的 pending（永不再进锁，v0.5 S3-R10）。"""
    curr_fp = _fingerprint(ep_dir, _STOP_ARTIFACTS[stop])

    # 1. 已存在同 (类型, 指纹) 的 PENDING -> 幂等直接返回
    for item in state.items:
        if (
            item.type == stop
            and item.status == ApprovalStatus.PENDING
            and _fingerprints_equal(item.artifacts, curr_fp)
        ):
            return item

    # 2. §2.2 B3 终态抑制：同 (期, 类型, 指纹) 已存在 APPROVED / REJECTED 终态时严禁二次创建
    for item in state.items:
        if (
            item.type == stop
            and item.status in (ApprovalStatus.APPROVED, ApprovalStatus.REJECTED)
            and _fingerprints_equal(item.artifacts, curr_fp)
        ):
            return None

    # 3. §2.3 条件 1：同型旧 PENDING 在新建时转 SUPERSEDED
    now = _utc_now_iso()
    for item in state.items:
        if item.type == stop and item.status == ApprovalStatus.PENDING:
            item.status = ApprovalStatus.SUPERSEDED
            item.resolved_at = now
            item.resolved_by = "artifact"
            state.dirty = True
            if transitions is not None:
                transitions.append(
                    Transition(
                        approval_id=item.approval_id,
                        stop=item.type,
                        from_status=ApprovalStatus.PENDING,
                        to_status=ApprovalStatus.SUPERSEDED,
                        reason="superseded_by_new",
                    )
                )
            if pending_events is not None:
                pending_events.append(
                    (
                        "approval_resolved",
                        {
                            "approval_id": item.approval_id,
                            "stop": item.type,
                            "decision": "superseded",
                            "source": "artifact",
                        },
                    )
                )

    # 4. 容量上限与淘汰阶梯（§3.2 M7）
    if not _evict_for_new_pending_locked(state.items):
        return None

    new_obj = Approval(
        approval_id=f"appr_{int(time.time() * 1000)}_{uuid.uuid4().hex[:4]}",
        episode=_episode_repr(ep_dir),
        type=stop,
        status=ApprovalStatus.PENDING,
        artifacts=curr_fp,
        options=["approve", "reject"],
        created_at=now,
        note=_STOP_NOTES.get(stop, ""),
    )
    state.items.append(new_obj)
    state.dirty = True

    req_payload = {
        "approval_id": new_obj.approval_id,
        "stop": new_obj.type,
        "artifacts": [a.to_dict() for a in new_obj.artifacts],
        "options": list(new_obj.options),
        "note": new_obj.note,
    }
    if pending_events is not None:
        pending_events.append(("approval_requested", req_payload))
    else:
        _emit("approval_requested", req_payload, episode_dir=ep_dir)

    return new_obj


def _self_heal_locked(
    state: _StoreState,
    ep_dir: Path,
    status: EpisodeStatus | None = None,
) -> list[Transition]:
    """在已持锁的 state 上完成自愈补建与 §2.3 惰性检测（永不再进锁，v0.5 S3-R10）。"""
    transitions: list[Transition] = []
    pending_events: list[tuple[str, dict[str, Any]]] = []
    drifted_stops: list[ApprovalType] = []

    # 1. 现有 PENDING 对象的「工序越过（§2.3 条件 3）」与「指纹漂移（§2.3 条件 2）」检测
    now = _utc_now_iso()
    has_clips = (ep_dir / "04-clips.json").exists()
    for item in state.items:
        if item.status != ApprovalStatus.PENDING:
            continue
        # §2.3 条件 3：03.5 在 04-clips.json 存在时（status.py:313 退出条件）自动转为 SUPERSEDED
        if item.type == "03.5" and has_clips:
            item.status = ApprovalStatus.SUPERSEDED
            item.resolved_at = now
            item.resolved_by = "artifact"
            state.dirty = True
            transitions.append(
                Transition(
                    approval_id=item.approval_id,
                    stop=item.type,
                    from_status=ApprovalStatus.PENDING,
                    to_status=ApprovalStatus.SUPERSEDED,
                    reason="stage_passed",
                )
            )
            pending_events.append(
                (
                    "approval_resolved",
                    {
                        "approval_id": item.approval_id,
                        "stop": item.type,
                        "decision": "superseded",
                        "source": "artifact",
                    },
                )
            )
            continue

        curr_fp = _fingerprint(ep_dir, _STOP_ARTIFACTS[item.type])
        if not _fingerprints_equal(item.artifacts, curr_fp):
            item.status = ApprovalStatus.SUPERSEDED
            item.resolved_at = now
            item.resolved_by = "artifact"
            state.dirty = True
            if item.type not in drifted_stops:
                drifted_stops.append(item.type)
            transitions.append(
                Transition(
                    approval_id=item.approval_id,
                    stop=item.type,
                    from_status=ApprovalStatus.PENDING,
                    to_status=ApprovalStatus.SUPERSEDED,
                    reason="fingerprint_drift",
                )
            )
            pending_events.append(
                (
                    "approval_resolved",
                    {
                        "approval_id": item.approval_id,
                        "stop": item.type,
                        "decision": "superseded",
                        "source": "artifact",
                    },
                )
            )

    # 2. 现算工序状态并按需补建当前停机点（漂移后跨工序补建仅对带物理闸门的 02.5/05 生效）
    if status is None:
        try:
            from pipeline.status import inspect_episode

            status = inspect_episode(ep_dir)
        except Exception:
            status = None

    candidate_stops: list[ApprovalType] = []
    if status is not None:
        curr_stop = human_stop_of(status.current_step)
        if curr_stop is not None:
            candidate_stops.append(curr_stop)
    for d_stop in drifted_stops:
        if _STOP_GATE.get(d_stop) is not None and d_stop not in candidate_stops:
            candidate_stops.append(d_stop)

    for stop in candidate_stops:
        _ensure_pending_locked(state, ep_dir, stop, transitions, pending_events)

    # 3. 解封物双闸惰性对齐（§2.3 B1）：仅当解封物存在且过双闸时转 APPROVED(source="artifact")
    for item in state.items:
        if item.status != ApprovalStatus.PENDING:
            continue
        if _STOP_GATE.get(item.type) is not None and _gate_valid(ep_dir, item.type):
            item.status = ApprovalStatus.APPROVED
            item.resolved_at = _utc_now_iso()
            item.resolved_by = "artifact"
            state.dirty = True
            transitions.append(
                Transition(
                    approval_id=item.approval_id,
                    stop=item.type,
                    from_status=ApprovalStatus.PENDING,
                    to_status=item.status,
                    reason="artifact",
                )
            )
            pending_events.append(
                (
                    "approval_resolved",
                    {
                        "approval_id": item.approval_id,
                        "stop": item.type,
                        "decision": "approved",
                        "source": "artifact",
                    },
                )
            )

    for evt_name, evt_payload in pending_events:
        _emit(evt_name, evt_payload, episode_dir=ep_dir)

    return transitions


def _gate_error_message(ep_dir: Path, stop: ApprovalType) -> str:
    if stop == "02.5":
        return (
            f"02.5 解封物缺失或已过期（需存在非空且新于 02-script.md 的 02-diff.patch）。"
            f"请先执行封板命令：git diff --no-index {ep_dir}/02-script.draft.md {ep_dir}/02-script.md > {ep_dir}/02-diff.patch"
        )
    if stop == "05":
        return (
            f"05 解封物缺失或已过期（04-clips.approved.json 不存在或与 04-clips.json 存在差异）。"
            f"请先执行批准命令：python -m pipeline.review {ep_dir} --approve"
        )
    return f"停机点 {stop} 解封物校验未通过"


def _append_feedback_markdown(ep_dir: Path, obj: Approval) -> None:
    """向 _agent/approval_feedback.md 追加结构化驳回反馈（§2.4）。"""
    if not obj.feedback:
        return
    agent_dir = ep_dir / "_agent"
    agent_dir.mkdir(parents=True, exist_ok=True)
    fb_path = agent_dir / "approval_feedback.md"
    stop_label = f"{obj.type} {_STOP_NAMES.get(obj.type, '')}".strip()
    block = (
        f"## [{stop_label}] {obj.resolved_at or _utc_now_iso()} ({obj.approval_id})\n"
        f"- **定位 (target)**: {obj.feedback['target']}\n"
        f"- **问题 (problem)**: {obj.feedback['problem']}\n\n"
    )
    existing = fb_path.read_text(encoding="utf-8") if fb_path.exists() else ""
    paths.atomic_write(fb_path, existing + block)


def ensure_pending(
    ep_dir: Path | str,
    status: EpisodeStatus | None = None,
) -> Approval | None:
    """幂等补建停机点 pending（§4.1：一次进锁包装 _ensure_pending_locked）。"""
    ep_path = Path(ep_dir) if ep_dir else None
    if ep_path is None or not ep_path.exists() or not ep_path.is_dir():
        return None

    with _locked_approvals(ep_path, silent_write_error=True) as state:
        _raise_if_store_unwritable(state)
        if status is None:
            try:
                from pipeline.status import inspect_episode

                status = inspect_episode(ep_path)
            except Exception:
                return None
        stop = human_stop_of(status.current_step)
        if stop is None:
            return None
        pending_events: list[tuple[str, dict[str, Any]]] = []
        result = _ensure_pending_locked(state, ep_path, stop, None, pending_events)
        for evt_name, evt_payload in pending_events:
            _emit(evt_name, evt_payload, episode_dir=ep_path)
        return result


def list_pending(ep_dir: Path | str) -> list[Approval]:
    """读路径统一入口：一次进锁执行 _self_heal_locked，返回当前 PENDING 对象列表（§4.1）。"""
    ep_path = Path(ep_dir) if ep_dir else None
    if ep_path is None or not ep_path.exists() or not ep_path.is_dir():
        return []

    with _locked_approvals(ep_path, silent_write_error=True) as state:
        _self_heal_locked(state, ep_path)
        return [item for item in state.items if item.status == ApprovalStatus.PENDING]


def get_status_card_guidance(ep_dir: Path | str) -> tuple[list[str], bool]:
    """一次进锁返回 (pending 类型标签列表, 是否存在未被新 pending 覆盖的 REJECTED 反馈)。

    供 status_card.build_status_card 调用，不暴露私有 _STOP_NAMES 或裸读 JSON。
    """
    ep_path = Path(ep_dir) if ep_dir else None
    if ep_path is None or not ep_path.exists() or not ep_path.is_dir():
        return ([], False)

    with _locked_approvals(ep_path, silent_write_error=True) as state:
        _self_heal_locked(state, ep_path)
        pending_labels: list[str] = []
        pending_stops: set[str] = set()
        for item in state.items:
            if item.status == ApprovalStatus.PENDING:
                pending_stops.add(item.type)
                stop_name = _STOP_NAMES.get(item.type, "")
                pending_labels.append(f"{item.type} {stop_name}".strip())

        latest_by_stop: dict[str, Approval] = {}
        for item in state.items:
            latest_by_stop[item.type] = item

        has_uncovered_rejected = any(
            st_type not in pending_stops and obj.status == ApprovalStatus.REJECTED
            for st_type, obj in latest_by_stop.items()
        )
        return (pending_labels, has_uncovered_rejected)


def approve(
    ep_dir: Path | str,
    stop: ApprovalType,
    *,
    approval_id: str | None = None,
    source: str = "repl",
) -> Approval:
    """显式 ack 通过（§4.1 v0.4/v0.5 S3-R6/R7/R10/R11 冻结顺序，全程一次进锁）。"""
    if stop not in HUMAN_STOPS:
        raise ApprovalError(f"非法停机点类型: {stop!r}")
    ep_path = Path(ep_dir) if ep_dir else None
    if ep_path is None or not ep_path.exists() or not ep_path.is_dir():
        raise ApprovalError(f"目标期目录不存在: {ep_dir}")

    with _locked_approvals(ep_path) as state:
        _raise_if_store_unwritable(state)
        # 1. 一次进锁后先执行 _self_heal_locked
        transitions = _self_heal_locked(state, ep_path)

        # 2. 定位目标对象
        target_obj: Approval | None = None
        if approval_id is not None:
            for item in state.items:
                if item.approval_id == approval_id:
                    target_obj = item
                    break
            if target_obj is None:
                raise ApprovalError(f"未找到指定的审批对象: {approval_id}")
            if target_obj.type != stop:
                raise ApprovalError(
                    f"审批对象 {approval_id} 类型为 {target_obj.type}，与请求的 {stop} 不符"
                )
            if target_obj.status == ApprovalStatus.SUPERSEDED:
                if stop == "03.5" and (ep_path / "04-clips.json").exists():
                    raise ApprovalError("工序已越过 03.5，无需确认")
                raise ApprovalError("对象已被替换（关联产物已变更），请重新审阅")
            if target_obj.status == ApprovalStatus.REJECTED:
                raise ApprovalError(f"对象 {approval_id} 已处于 rejected 终态，不可异决策批准")
        else:
            if any(
                t.stop == stop and t.reason == "stage_passed"
                for t in transitions
            ):
                raise ApprovalError("工序已越过 03.5，无需确认")
            # 未给 id（仅 REPL）：若本次自愈刚把同类型对象转为 SUPERSEDED，拒绝操作替身
            if any(
                t.stop == stop and t.to_status == ApprovalStatus.SUPERSEDED
                for t in transitions
            ):
                raise ApprovalError("本次操作前对象已被替换，请先 /approvals 查看")
            for item in reversed(state.items):
                if item.type == stop and item.status == ApprovalStatus.PENDING:
                    target_obj = item
                    break
            if target_obj is None:
                for item in reversed(state.items):
                    if (
                        item.type == stop
                        and item.status == ApprovalStatus.APPROVED
                        and item.resolved_by == "artifact"
                    ):
                        target_obj = item
                        break
            if target_obj is None:
                for item in reversed(state.items):
                    if item.type == stop:
                        target_obj = item
                        break
            if target_obj is None:
                if stop == "03.5" and (ep_path / "04-clips.json").exists():
                    raise ApprovalError("工序已越过 03.5，无需确认")
                raise ApprovalError(f"当期不存在停机点 {stop} 的待审批对象")
            if target_obj.status == ApprovalStatus.SUPERSEDED:
                if stop == "03.5" and (ep_path / "04-clips.json").exists():
                    raise ApprovalError("工序已越过 03.5，无需确认")
                raise ApprovalError("对象已被替换（关联产物已变更），请重新审阅")
            if target_obj.status == ApprovalStatus.REJECTED:
                raise ApprovalError(f"停机点 {stop} 对象已处于 rejected 终态，不可异决策批准")

        # 3. 目标为 PENDING
        if target_obj.status == ApprovalStatus.PENDING:
            curr_fp = _fingerprint(ep_path, _STOP_ARTIFACTS[stop])
            if not _fingerprints_equal(target_obj.artifacts, curr_fp):
                target_obj.status = ApprovalStatus.SUPERSEDED
                target_obj.resolved_at = _utc_now_iso()
                target_obj.resolved_by = "artifact"
                state.dirty = True
                raise ApprovalError("对象已被替换（关联产物已变更），请重新审阅")

            if _STOP_GATE.get(stop) is not None and not _gate_valid(ep_path, stop):
                raise ApprovalError(_gate_error_message(ep_path, stop))

            now_iso = _utc_now_iso()
            target_obj.status = ApprovalStatus.APPROVED
            target_obj.resolved_at = now_iso
            target_obj.resolved_by = source
            state.dirty = True

            now_s = _parse_iso_seconds(now_iso)
            created_s = _parse_iso_seconds(target_obj.created_at)
            latency_s = (
                round(max(0.0, now_s - created_s), 3)
                if (now_s is not None and created_s is not None)
                else None
            )
            _log_decision(ep_path, source, f"approve:{stop}", "y", latency_s=latency_s)
            _emit(
                "approval_resolved",
                {
                    "approval_id": target_obj.approval_id,
                    "stop": stop,
                    "decision": "approved",
                    "source": source,
                    "latency_s": latency_s,
                },
                episode_dir=ep_path,
            )
            return target_obj

        # 4. 确认路径（v0.4 S3-R7 & v0.5 S3-R11 & v0.7 R-2）：APPROVED 且 resolved_by == "artifact" 且未确认
        if (
            target_obj.status == ApprovalStatus.APPROVED
            and target_obj.resolved_by == "artifact"
            and target_obj.confirmed_by is None
        ):
            curr_fp = _fingerprint(ep_path, _STOP_ARTIFACTS[stop])
            if not _fingerprints_equal(target_obj.artifacts, curr_fp) or (
                _STOP_GATE.get(stop) is not None and not _gate_valid(ep_path, stop)
            ):
                raise ApprovalError(_gate_error_message(ep_path, stop))

            now_iso = _utc_now_iso()
            target_obj.confirmed_by = source
            target_obj.confirmed_at = now_iso
            state.dirty = True

            aligned_in_this_call = any(
                t.approval_id == target_obj.approval_id
                and t.to_status == ApprovalStatus.APPROVED
                and t.reason == "artifact"
                for t in transitions
            )
            latency_s = None
            if aligned_in_this_call:
                now_s = _parse_iso_seconds(now_iso)
                created_s = _parse_iso_seconds(target_obj.created_at)
                if now_s is not None and created_s is not None:
                    latency_s = round(max(0.0, now_s - created_s), 3)

            _log_decision(ep_path, source, f"approve:{stop}", "y", latency_s=latency_s)
            _emit(
                "approval_resolved",
                {
                    "approval_id": target_obj.approval_id,
                    "stop": stop,
                    "decision": "approved",
                    "source": source,
                    "confirms": "artifact",
                    "latency_s": latency_s,
                },
                episode_dir=ep_path,
            )
            return target_obj

        # 5. 其余 APPROVED（已显式批准或已确认）：同决策幂等，原样返回，不重复记账
        return target_obj


def reject(
    ep_dir: Path | str,
    stop: ApprovalType,
    feedback: dict[str, str],
    *,
    approval_id: str | None = None,
    source: str = "repl",
) -> Approval:
    """显式 ack 打回（§4.1 v0.4/v0.5 S3-R6/R10，全程一次进锁）。"""
    if stop not in HUMAN_STOPS:
        raise ApprovalError(f"非法停机点类型: {stop!r}")
    clean_fb = _validate_feedback(feedback)
    ep_path = Path(ep_dir) if ep_dir else None
    if ep_path is None or not ep_path.exists() or not ep_path.is_dir():
        raise ApprovalError(f"目标期目录不存在: {ep_dir}")

    with _locked_approvals(ep_path) as state:
        _raise_if_store_unwritable(state)
        # 1. 一次进锁后先执行 _self_heal_locked
        transitions = _self_heal_locked(state, ep_path)

        # 2. 定位目标对象
        target_obj: Approval | None = None
        if approval_id is not None:
            for item in state.items:
                if item.approval_id == approval_id:
                    target_obj = item
                    break
            if target_obj is None:
                raise ApprovalError(f"未找到指定的审批对象: {approval_id}")
            if target_obj.type != stop:
                raise ApprovalError(
                    f"审批对象 {approval_id} 类型为 {target_obj.type}，与请求的 {stop} 不符"
                )
            if target_obj.status == ApprovalStatus.SUPERSEDED:
                if stop == "03.5" and (ep_path / "04-clips.json").exists():
                    raise ApprovalError("工序已越过 03.5，无需确认")
                raise ApprovalError("对象已被替换（关联产物已变更），请重新审阅")
            if target_obj.status == ApprovalStatus.APPROVED:
                raise ApprovalError(f"对象 {approval_id} 已处于 approved 终态，不可异决策打回")
        else:
            if any(
                t.stop == stop and t.reason == "stage_passed"
                for t in transitions
            ):
                raise ApprovalError("工序已越过 03.5，无需确认")
            if any(
                t.stop == stop and t.to_status == ApprovalStatus.SUPERSEDED
                for t in transitions
            ):
                raise ApprovalError("本次操作前对象已被替换，请先 /approvals 查看")
            for item in reversed(state.items):
                if item.type == stop and item.status == ApprovalStatus.PENDING:
                    target_obj = item
                    break
            if target_obj is None:
                for item in reversed(state.items):
                    if item.type == stop:
                        target_obj = item
                        break
            if target_obj is None:
                if stop == "03.5" and (ep_path / "04-clips.json").exists():
                    raise ApprovalError("工序已越过 03.5，无需确认")
                raise ApprovalError(f"当期不存在停机点 {stop} 的待审批对象")
            if target_obj.status == ApprovalStatus.SUPERSEDED:
                if stop == "03.5" and (ep_path / "04-clips.json").exists():
                    raise ApprovalError("工序已越过 03.5，无需确认")
                raise ApprovalError("对象已被替换（关联产物已变更），请重新审阅")
            if target_obj.status == ApprovalStatus.APPROVED:
                raise ApprovalError(f"停机点 {stop} 对象已处于 approved 终态，不可异决策打回")

        # 同决策幂等：已处于 REJECTED 终态直接返回，不重复记账或追加反馈文件
        if target_obj.status == ApprovalStatus.REJECTED:
            return target_obj

        curr_fp = _fingerprint(ep_path, _STOP_ARTIFACTS[stop])
        if not _fingerprints_equal(target_obj.artifacts, curr_fp):
            target_obj.status = ApprovalStatus.SUPERSEDED
            target_obj.resolved_at = _utc_now_iso()
            target_obj.resolved_by = "artifact"
            state.dirty = True
            raise ApprovalError("对象已被替换（关联产物已变更），请重新审阅")

        now_iso = _utc_now_iso()
        target_obj.status = ApprovalStatus.REJECTED
        target_obj.resolved_at = now_iso
        target_obj.resolved_by = source
        target_obj.feedback = clean_fb
        target_obj.validate()
        state.dirty = True

        _append_feedback_markdown(ep_path, target_obj)

        now_s = _parse_iso_seconds(now_iso)
        created_s = _parse_iso_seconds(target_obj.created_at)
        latency_s = (
            round(max(0.0, now_s - created_s), 3)
            if (now_s is not None and created_s is not None)
            else None
        )
        _log_decision(ep_path, source, f"reject:{stop}", "n", latency_s=latency_s)
        _emit(
            "approval_resolved",
            {
                "approval_id": target_obj.approval_id,
                "stop": stop,
                "decision": "rejected",
                "source": source,
                "feedback": clean_fb,
                "latency_s": latency_s,
            },
            episode_dir=ep_path,
        )
        return target_obj


def get(approval_id: str, ep_dir: Path | str) -> Approval | None:
    """按 id 取对象（含终态，供桌面端/测试核对，一次进锁）。"""
    ep_path = Path(ep_dir) if ep_dir else None
    if ep_path is None or not ep_path.exists() or not ep_path.is_dir():
        return None

    with _locked_approvals(ep_path, silent_write_error=True) as state:
        for item in state.items:
            if item.approval_id == approval_id:
                return item
        return None
