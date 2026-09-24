"""Spec 3 (ADR-0020 §3) approval 对象化单元测试（PR1 & PR2 + S8 review 补强）。"""

from __future__ import annotations

import fcntl
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
from typing import Any

import pytest

from pipeline import approvals
from pipeline.agent.status_card import build_status_card
from pipeline.approvals import (
    Approval,
    ApprovalError,
    ApprovalStatus,
    approve,
    ensure_pending,
    get,
    list_pending,
    reject,
)
from pipeline.status import inspect_episode


def _make_episode_at_02_5(base: Path, name: str = "ep-02-5") -> Path:
    """构造处于 02.5 人审改稿停机点的期目录。"""
    ep = base / name
    ep.mkdir(parents=True, exist_ok=True)
    (ep / "01-topic.md").write_text("# 选题\n类型：杂谈\n", encoding="utf-8")
    (ep / "02-script.draft.md").write_text("# 草稿\n", encoding="utf-8")
    (ep / "02-script.md").write_text("## 段落 1\n配音：测试台词。\n", encoding="utf-8")
    return ep


def _make_episode_at_03_5(base: Path, name: str = "ep-03-5") -> Path:
    """构造处于 03.5 配音顺听停机点的期目录。"""
    ep = _make_episode_at_02_5(base, name=name)
    (ep / "02-diff.patch").write_text("--- a\n+++ b\n@@ -1 +1 @@\n-old\n+new\n", encoding="utf-8")
    audio_dir = ep / "03-audio"
    audio_dir.mkdir(exist_ok=True)
    (audio_dir / "manifest.json").write_text(
        json.dumps({"segments": [{"index": 1, "duration": 4.0}]}),
        encoding="utf-8",
    )
    return ep


def _make_episode_at_05(base: Path, name: str = "ep-05") -> Path:
    """构造处于 05 审时间码停机点的期目录。"""
    ep = _make_episode_at_03_5(base, name=name)
    clips_data = {
        "anime": "TestAnime",
        "total_duration": 4.0,
        "segments": [
            {
                "index": 1,
                "status": "ok",
                "duration": 4.0,
                "clips": [{"season": 1, "episode": 1, "start": 10.0, "dur": 4.0}],
            }
        ],
    }
    (ep / "04-clips.json").write_text(
        json.dumps(clips_data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return ep


def _write_valid_approved_clips(ep: Path) -> Path:
    """写出与当前 04-clips.json 段内容一致且 mtime_ns >= clips 的 04-clips.approved.json。"""
    clips_path = ep / "04-clips.json"
    appr_path = ep / "04-clips.approved.json"
    appr_path.write_text(clips_path.read_text(encoding="utf-8"), encoding="utf-8")
    clips_st = clips_path.stat()
    target_ns = max(appr_path.stat().st_mtime_ns, clips_st.st_mtime_ns + 1_000_000)
    os.utime(appr_path, ns=(target_ns, target_ns))
    return appr_path


# ---------------------------------------------------------------------------
# PR1: T1, T2, T8, T9, T11 (+ T21 一次进锁子进程超时断言 + M4-2 坏条目恢复)
# ---------------------------------------------------------------------------


def test_approval_state_machine(tmp_path: Path) -> None:
    """T1 (M4-3 修复): 经服务层 approve / reject 检验四状态流转、同决策幂等、异决策抛 ApprovalError、feedback 校验。"""
    # 1. pending -> approved 合法，同决策重复幂等，异决策转移抛 ApprovalError
    ep_appr = _make_episode_at_03_5(tmp_path, "ep-sm-approve")
    created_a = ensure_pending(ep_appr)
    assert created_a is not None
    assert created_a.status == ApprovalStatus.PENDING

    res1 = approve(ep_appr, "03.5", approval_id=created_a.approval_id, source="repl")
    assert res1.status == ApprovalStatus.APPROVED
    assert res1.resolved_by == "repl"
    assert res1.resolved_at is not None

    # 同决策重复 approve 幂等返回既有终态对象，不抛错
    res2 = approve(ep_appr, "03.5", approval_id=created_a.approval_id, source="cli")
    assert res2.approval_id == res1.approval_id
    assert res2.status == ApprovalStatus.APPROVED
    assert res2.resolved_by == "repl"

    # 异决策（已 approved 再 reject）抛 ApprovalError
    with pytest.raises(ApprovalError, match="不可异决策"):
        reject(
            ep_appr,
            "03.5",
            {"target": "s01", "problem": "late reject"},
            approval_id=created_a.approval_id,
        )
    with pytest.raises(ApprovalError, match="不可异决策"):
        reject(ep_appr, "03.5", {"target": "s01", "problem": "late reject"})

    # 2. pending -> rejected 缺 feedback 字段必抛 ApprovalError，合法则流转，同决策重复幂等，异决策抛错
    ep_rej = _make_episode_at_03_5(tmp_path, "ep-sm-reject")
    created_r = ensure_pending(ep_rej)
    assert created_r is not None
    for bad_fb in (
        None,
        {},
        {"target": "s1"},
        {"problem": "p"},
        {"target": "  ", "problem": "p"},
        {"target": "s1", "problem": ""},
    ):
        with pytest.raises(ApprovalError):
            reject(ep_rej, "03.5", bad_fb, approval_id=created_r.approval_id)  # type: ignore[arg-type]

    rej1 = reject(
        ep_rej,
        "03.5",
        {"target": "s07", "problem": "画面切给路人"},
        approval_id=created_r.approval_id,
    )
    assert rej1.status == ApprovalStatus.REJECTED
    assert rej1.feedback == {"target": "s07", "problem": "画面切给路人"}

    # 同决策重复 reject 幂等
    rej2 = reject(
        ep_rej,
        "03.5",
        {"target": "s07", "problem": "画面切给路人"},
        approval_id=created_r.approval_id,
    )
    assert rej2.approval_id == rej1.approval_id
    assert rej2.status == ApprovalStatus.REJECTED

    # 异决策（已 rejected 再 approve）抛 ApprovalError
    with pytest.raises(ApprovalError, match="不可异决策"):
        approve(ep_rej, "03.5", approval_id=created_r.approval_id)
    with pytest.raises(ApprovalError, match="不可异决策"):
        approve(ep_rej, "03.5")

    # 3. pending -> superseded 合法，终态不可再转 approved / rejected
    ep_sup = _make_episode_at_03_5(tmp_path, "ep-sm-superseded")
    created_s = ensure_pending(ep_sup)
    assert created_s is not None
    (ep_sup / "03-audio" / "manifest.json").write_text(
        json.dumps({"segments": [{"index": 1, "duration": 5.5}]}),
        encoding="utf-8",
    )
    with pytest.raises(ApprovalError):
        approve(ep_sup, "03.5", approval_id=created_s.approval_id)
    assert get(created_s.approval_id, ep_sup).status == ApprovalStatus.SUPERSEDED  # type: ignore[union-attr]
    with pytest.raises(ApprovalError):
        reject(
            ep_sup,
            "03.5",
            {"target": "s1", "problem": "err"},
            approval_id=created_s.approval_id,
        )

    # 4. 容量上限 64 条与淘汰阶梯（§3.2 M7：SUPERSEDED -> REJECTED -> APPROVED，PENDING 永不淘汰）
    store_items: list[Approval] = [
        Approval(approval_id=f"appr_appr_{i}", episode="ep", type="03.5", status=ApprovalStatus.APPROVED)
        for i in range(62)
    ]
    store_items.append(
        Approval(
            approval_id="appr_rej_oldest",
            episode="ep",
            type="03.5",
            status=ApprovalStatus.REJECTED,
            feedback={"target": "s1", "problem": "bad"},
        )
    )
    store_items.append(
        Approval(
            approval_id="appr_sup_oldest",
            episode="ep",
            type="03.5",
            status=ApprovalStatus.SUPERSEDED,
        )
    )
    assert len(store_items) == 64
    assert approvals._evict_for_new_pending_locked(store_items) is True
    assert len(store_items) == 63
    assert all(x.approval_id != "appr_sup_oldest" for x in store_items), "应最先淘汰 SUPERSEDED"

    store_items.append(
        Approval(approval_id="appr_fill", episode="ep", type="03.5", status=ApprovalStatus.APPROVED)
    )
    assert approvals._evict_for_new_pending_locked(store_items) is True
    assert all(x.approval_id != "appr_rej_oldest" for x in store_items), "次优应淘汰 REJECTED"

    all_pending = [
        Approval(approval_id=f"appr_pend_{i}", episode="ep", type="03.5", status=ApprovalStatus.PENDING)
        for i in range(64)
    ]
    assert approvals._evict_for_new_pending_locked(all_pending) is False
    assert len(all_pending) == 64, "全为 PENDING 时严禁淘汰任何条目"

    # 5. _episode_repr 真子集判断与降级（§4.1 M6）
    assert approvals._episode_repr(tmp_path / "my-tmp-ep") == "my-tmp-ep"
    nested = approvals.paths.ROOT / "data" / "episodes" / "EGOIST" / "01-Live"
    assert approvals._episode_repr(nested) == "EGOIST/01-Live"


def test_corrupt_entry_preserves_valid_store(tmp_path: Path) -> None:
    """M4-2: store 中同时存在合法 REJECTED 与坏条目时，首次读备份并清理写回（state.dirty=True），
    连续多次 list_pending/状态卡仅产生恰好 1 个备份文件，store 内不再含坏条目且合法 REJECTED 完整保留。"""
    ep = _make_episode_at_05(tmp_path, "ep-corrupt-store")
    first = ensure_pending(ep)
    assert first is not None
    rej = reject(
        ep,
        "05",
        {"target": "04-clips.json s01", "problem": "镜头错位"},
        approval_id=first.approval_id,
    )
    assert rej.status == ApprovalStatus.REJECTED

    store_path = ep / "_agent" / "approvals_store.json"
    raw_list = json.loads(store_path.read_text(encoding="utf-8"))
    # 追加一条违反不变量的坏条目（status=rejected 但 feedback=null）
    raw_list.append(
        {
            "approval_id": "appr_corrupt_bad",
            "episode": "ep-corrupt-store",
            "type": "05",
            "status": "rejected",
            "artifacts": [],
            "options": ["approve", "reject"],
            "created_at": "2026-09-24T00:00:00Z",
            "feedback": None,
        }
    )
    store_path.write_text(json.dumps(raw_list, ensure_ascii=False, indent=2), encoding="utf-8")

    # 在不改动产物的情况下连续调用 3 次 list_pending 与 build_status_card：
    # 若首次未把 dirty 置为 True 写回清理，则每次调用都会重复备份且坏条目留在 store 中
    for _ in range(3):
        list_pending(ep)
        build_status_card(ep)
        time.sleep(0.002)

    corrupt_backups = list((ep / "_agent").glob("approvals_store.corrupt-*.json"))
    assert len(corrupt_backups) == 1, f"应恰产生 1 个 corrupt 备份文件，实际 {len(corrupt_backups)} 个"

    cleaned_raw = json.loads(store_path.read_text(encoding="utf-8"))
    assert all(item.get("approval_id") != "appr_corrupt_bad" for item in cleaned_raw), (
        "清理写回后 approvals_store.json 中不应再含坏条目"
    )

    # 改动产物后触发新 pending，原来那条 REJECTED 对象依然完整保留
    clips_path = ep / "04-clips.json"
    clips_path.write_text(clips_path.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    pendings = list_pending(ep)
    assert len(pendings) == 1

    preserved_rej = get(rej.approval_id, ep)
    assert preserved_rej is not None
    assert preserved_rej.status == ApprovalStatus.REJECTED
    assert preserved_rej.feedback == {"target": "04-clips.json s01", "problem": "镜头错位"}
    assert len(list((ep / "_agent").glob("approvals_store.corrupt-*.json"))) == 1


def test_pending_survives_process_restart(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """T2: 跨进程持久化恢复 + events 被 drop 场景恢复（M2/MUT-6） + 直接自愈（B2/MUT-13）。"""
    ep = _make_episode_at_05(tmp_path, "ep-restart")

    # 进程 A：创建 pending 落盘
    code_a = (
        "import json; from pathlib import Path; from pipeline import approvals; "
        f"ep = Path({str(ep)!r}); "
        "obj = approvals.ensure_pending(ep); "
        "print(json.dumps(obj.to_dict()))"
    )
    proc_a = subprocess.run([sys.executable, "-c", code_a], capture_output=True, text=True, check=True)
    obj_a = json.loads(proc_a.stdout.strip().splitlines()[-1])
    assert obj_a["type"] == "05"
    assert obj_a["status"] == "pending"

    # 放入有效解封物后，进程 B：全新子进程先读回同一对象（验证指纹与 id），再完成显式 ack
    code_b = (
        "import json; from pathlib import Path; from pipeline import approvals; "
        f"ep = Path({str(ep)!r}); "
        f"stored = approvals.get({obj_a['approval_id']!r}, ep); "
        "assert stored is not None and stored.status.value == 'pending'; "
        f"assert stored.approval_id == {obj_a['approval_id']!r}; "
        f"assert [a.to_dict() for a in stored.artifacts] == {obj_a['artifacts']!r}; "
        "(ep / '04-clips.approved.json').write_text((ep / '04-clips.json').read_text()); "
        f"res = approvals.approve(ep, '05', approval_id={obj_a['approval_id']!r}, source='cli'); "
        "print(json.dumps(res.to_dict()))"
    )
    proc_b = subprocess.run([sys.executable, "-c", code_b], capture_output=True, text=True, check=True)
    obj_b = json.loads(proc_b.stdout.strip().splitlines()[-1])
    assert obj_b["approval_id"] == obj_a["approval_id"]
    assert obj_b["status"] == "approved"

    # 进程 C：再开子进程确认终态已持久化且 list_pending 为空
    code_c = (
        "import json; from pathlib import Path; from pipeline import approvals; "
        f"ep = Path({str(ep)!r}); "
        f"final_obj = approvals.get({obj_a['approval_id']!r}, ep); "
        "pendings = approvals.list_pending(ep); "
        "print(json.dumps({'status': final_obj.status.value, 'pending_count': len(pendings)}))"
    )
    proc_c = subprocess.run([sys.executable, "-c", code_c], capture_output=True, text=True, check=True)
    res_c = json.loads(proc_c.stdout.strip().splitlines()[-1])
    assert res_c == {"status": "approved", "pending_count": 0}

    # 子场景（M2 / MUT-6）：store 有对象、events.jsonl 不存在且阻断 jobs import，恢复依然成立
    ep_no_evt = _make_episode_at_03_5(tmp_path, "ep-events-dropped")
    monkeypatch.setitem(sys.modules, "pipeline.jobs", None)
    p_created = ensure_pending(ep_no_evt)
    assert p_created is not None
    assert not (ep_no_evt / "events.jsonl").exists()
    recovered = list_pending(ep_no_evt)
    assert len(recovered) == 1
    assert recovered[0].approval_id == p_created.approval_id
    approved_no_evt = approve(ep_no_evt, "03.5", approval_id=p_created.approval_id)
    assert approved_no_evt.status == ApprovalStatus.APPROVED

    # 子场景（B2 / MUT-13）：从未调过 ensure_pending 的期直接 approve，自愈补建并完成批准
    ep_direct = _make_episode_at_03_5(tmp_path, "ep-direct-self-heal")
    direct_res = approve(ep_direct, "03.5")
    assert direct_res.status == ApprovalStatus.APPROVED

    # 子场景（Gate 1 B2）：从未进过 REPL 的期上运行裸形态 `ava <期> /approvals` 自愈并列出 pending
    ep_bare = _make_episode_at_05(tmp_path, "ep-bare-approvals")
    proc_cli = subprocess.run(
        [sys.executable, "-m", "pipeline.agent.cli", str(ep_bare), "/approvals"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert "[pending]" in proc_cli.stdout
    assert "停机点: 05" in proc_cli.stdout


def test_concurrent_ack_serialized_by_flock(tmp_path: Path) -> None:
    """T8: 跨进程 flock 排他锁挡写验证（M1/MUT-1） + 同进程多线程串行化。"""
    ep = _make_episode_at_03_5(tmp_path, "ep-flock")
    item = ensure_pending(ep)
    assert item is not None

    lock_path = ep / "_agent" / "approvals_store.lock"
    assert lock_path.exists()

    lock_fh = lock_path.open("a", encoding="utf-8")
    fcntl.flock(lock_fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    try:
        child_code = (
            "from pathlib import Path; from pipeline import approvals; "
            f"approvals.approve(Path({str(ep)!r}), '03.5', approval_id={item.approval_id!r}, source='cli')"
        )
        proc = subprocess.Popen(
            [sys.executable, "-c", child_code],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        # 主进程持锁期间，子进程在 2.0s 内必须被阻塞无法完成
        time.sleep(2.0)
        assert proc.poll() is None, "子进程未被 flock 阻塞（违反 _locked_approvals 排他锁纪律）"
    finally:
        fcntl.flock(lock_fh.fileno(), fcntl.LOCK_UN)
        lock_fh.close()

    stdout, stderr = proc.communicate(timeout=10.0)
    assert proc.returncode == 0, f"释锁后子进程执行失败: {stderr}"

    # 校验 JSON 完整可解析且状态已更新
    store_raw = json.loads((ep / "_agent" / "approvals_store.json").read_text(encoding="utf-8"))
    assert len(store_raw) == 1
    assert store_raw[0]["status"] == "approved"

    # 同进程双线程并发 ack 串行化
    ep_threads = _make_episode_at_03_5(tmp_path, "ep-threads")
    item_t = ensure_pending(ep_threads)
    assert item_t is not None
    results: list[Approval] = []
    errors: list[Exception] = []

    def _worker() -> None:
        try:
            results.append(approve(ep_threads, "03.5", approval_id=item_t.approval_id))
        except Exception as exc:
            errors.append(exc)

    t1 = threading.Thread(target=_worker)
    t2 = threading.Thread(target=_worker)
    t1.start()
    t2.start()
    t1.join(timeout=5.0)
    t2.join(timeout=5.0)
    assert not errors
    assert len(results) == 2
    assert all(r.status == ApprovalStatus.APPROVED for r in results)
    log_lines = (ep_threads / "_agent" / "approvals.jsonl").read_text(encoding="utf-8").strip().splitlines()
    assert len(log_lines) == 1


def test_fail_silent_when_episode_missing(tmp_path: Path) -> None:
    """T9: 存储红线——期目录不存在时 list_pending 返回 []、ensure_pending 返回 None，零 mkdir。"""
    missing_ep = tmp_path / "nonexistent-episode"
    assert not missing_ep.exists()

    assert list_pending(missing_ep) == []
    assert ensure_pending(missing_ep) is None
    assert get("appr_missing_0000", missing_ep) is None

    assert not missing_ep.exists(), "严禁自动创建不存在的期目录"
    assert not (missing_ep / "_agent").exists(), "严禁在不存在的期目录下创建 _agent/"


def test_approvals_module_pure() -> None:
    """T11: 独立子进程断言 pipeline.approvals 顶层零重依赖，且 pipeline.align 已加载且纯净。"""
    probe = (
        "import sys, pipeline.approvals; "
        "heavy = {'numpy', 'torch', 'mlx_whisper', 'moviepy', 'cv2', 'transformers'}; "
        "loaded = sorted(m for m in sys.modules if m.split('.')[0] in heavy); "
        "assert 'pipeline.align' in sys.modules, 'pipeline.align 应在顶层白名单中'; "
        "assert not loaded, f'检测到违禁顶层重依赖: {loaded}'"
    )
    res = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True)
    assert res.returncode == 0, f"纯洁性探针失败:\nstdout={res.stdout}\nstderr={res.stderr}"


def test_public_ops_enter_lock_once(tmp_path: Path) -> None:
    """T21: 公共入口一次进锁（v0.5 S3-R10），在独立子进程带 timeout 运行，死锁时立即变红不挂死套件。"""
    ep = _make_episode_at_05(tmp_path, "ep-single-lock")
    child_script = f"""
import contextlib
from pathlib import Path
from pipeline import approvals
from pipeline.approvals import ApprovalStatus, approve, get, list_pending, reject

ep = Path({str(ep)!r})
enter_count = 0
orig_locked = approvals._locked_approvals

@contextlib.contextmanager
def _counting_locked(*args, **kwargs):
    global enter_count
    enter_count += 1
    with orig_locked(*args, **kwargs) as st:
        yield st

approvals._locked_approvals = _counting_locked

enter_count = 0
pendings = list_pending(ep)
assert len(pendings) == 1 and enter_count == 1, f"list_pending enter_count={{enter_count}}"

enter_count = 0
fetched = get(pendings[0].approval_id, ep)
assert fetched is not None and enter_count == 1, f"get enter_count={{enter_count}}"

enter_count = 0
rej = reject(ep, "05", {{"target": "s01", "problem": "测试一次进锁"}}, approval_id=pendings[0].approval_id)
assert rej.status == ApprovalStatus.REJECTED and enter_count == 1, f"reject enter_count={{enter_count}}"

(ep / "04-clips.json").write_text((ep / "04-clips.json").read_text(encoding="utf-8") + "\\n", encoding="utf-8")
new_pendings = list_pending(ep)
assert len(new_pendings) == 1
(ep / "04-clips.approved.json").write_text((ep / "04-clips.json").read_text(encoding="utf-8"), encoding="utf-8")

enter_count = 0
appr = approve(ep, "05", approval_id=new_pendings[0].approval_id)
assert appr.status == ApprovalStatus.APPROVED and enter_count == 1, f"approve enter_count={{enter_count}}"
"""
    try:
        res = subprocess.run(
            [sys.executable, "-c", child_script],
            capture_output=True,
            text=True,
            timeout=5.0,
        )
    except subprocess.TimeoutExpired as exc:
        pytest.fail(f"T21 子进程超时（>5.0s），触发重入死锁（MUT-22）: {exc}")
    assert res.returncode == 0, f"T21 子进程断言失败:\nstdout={res.stdout}\nstderr={res.stderr}"


# ---------------------------------------------------------------------------
# PR2: T3, T4, T5, T10b, T14 (+ 03.5 工序越过即作废)
# ---------------------------------------------------------------------------


def test_03_5_stage_passed_supersedes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Spec 3 §2.3 条件 3（S8 review / 用户裁决）：03.5 pending 在 04-clips.json 存在时转为 SUPERSEDED。"""
    ep = _make_episode_at_03_5(tmp_path, "ep-035-stage-passed")
    pending_a = ensure_pending(ep)
    assert pending_a is not None
    assert pending_a.type == "03.5"

    superseded_events: list[dict[str, Any]] = []
    orig_emit = approvals._emit

    def _spy_emit(event_name: str, payload: dict[str, Any], episode_dir: Path | None) -> None:
        if (
            event_name == "approval_resolved"
            and payload.get("stop") == "03.5"
            and payload.get("decision") == "superseded"
        ):
            superseded_events.append(payload)
        orig_emit(event_name, payload, episode_dir)

    monkeypatch.setattr(approvals, "_emit", _spy_emit)

    # 1. 正常推进工序：03-audio/manifest.json 不变，仅生成 04-clips.json（杀 MUT-035-STAGE）
    clips_data = {
        "anime": "TestAnime",
        "total_duration": 4.0,
        "segments": [
            {
                "index": 1,
                "status": "ok",
                "duration": 4.0,
                "clips": [{"season": 1, "episode": 1, "start": 10.0, "dur": 4.0}],
            }
        ],
    }
    (ep / "04-clips.json").write_text(json.dumps(clips_data, ensure_ascii=False), encoding="utf-8")

    pendings = list_pending(ep)
    assert all(p.type != "03.5" for p in pendings), "03.5 越过之后不应再出现在 pending 列表中"
    assert len(pendings) == 1 and pendings[0].type == "05"

    stored_a = get(pending_a.approval_id, ep)
    assert stored_a is not None
    assert stored_a.status == ApprovalStatus.SUPERSEDED
    assert stored_a.resolved_by == "artifact"
    assert len(superseded_events) == 1

    # 状态卡中不再出现 待审批: 03.5
    card = build_status_card(ep)
    assert "03.5" not in card
    assert "待审批: 05 审时间码" in card

    # REPL 对已作废的 03.5 执行 approve(ep, "03.5") 报错说明「工序已越过 03.5，无需确认」
    with pytest.raises(ApprovalError, match="工序已越过 03.5，无需确认"):
        approve(ep, "03.5")

    # 2. 越过之后即使 03-audio/manifest.json 发生指纹漂移，也不得重建 03.5 pending
    (ep / "03-audio" / "manifest.json").write_text(
        json.dumps({"segments": [{"index": 1, "duration": 4.2}]}),
        encoding="utf-8",
    )
    pendings_after_drift = list_pending(ep)
    assert all(p.type != "03.5" for p in pendings_after_drift)


def test_ensure_pending_idempotent_upsert(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """T3: 幂等 upsert + 终态抑制（B3 / MUT-2 / MUT-12）。"""
    ep = _make_episode_at_05(tmp_path, "ep-upsert")
    status = inspect_episode(ep)

    emitted_requested: list[dict[str, Any]] = []
    orig_emit = approvals._emit

    def _spy_emit(event_name: str, payload: dict[str, Any], episode_dir: Path | None) -> None:
        if event_name == "approval_requested":
            emitted_requested.append(payload)
        orig_emit(event_name, payload, episode_dir)

    monkeypatch.setattr(approvals, "_emit", _spy_emit)

    # 1. 同 status 重复调 10 次：恰 1 条 pending、REQUESTED 恰 1 次
    first = ensure_pending(ep, status)
    assert first is not None
    for _ in range(9):
        again = ensure_pending(ep, status)
        assert again is not None
        assert again.approval_id == first.approval_id

    assert len(list_pending(ep)) == 1
    assert len(emitted_requested) == 1

    # 2. reject 后不改产物（指纹不变）再刷新 10 次：零新建、零 REQUESTED（B3 终态抑制）
    reject(ep, "05", {"target": "s01", "problem": "画面与台词不符"}, approval_id=first.approval_id)
    assert get(first.approval_id, ep).status == ApprovalStatus.REJECTED  # type: ignore[union-attr]

    for _ in range(10):
        assert ensure_pending(ep, status) is None
    assert list_pending(ep) == []
    assert len(emitted_requested) == 1

    # 3. 改动产物（指纹漂移）后刷新：新建 1 条 pending，旧 REJECTED 保持终态不复活
    clips_path = ep / "04-clips.json"
    clips_path.write_text(clips_path.read_text(encoding="utf-8") + "  \n", encoding="utf-8")
    recreated = ensure_pending(ep, status)
    assert recreated is not None
    assert recreated.approval_id != first.approval_id
    assert recreated.status == ApprovalStatus.PENDING
    assert len(emitted_requested) == 2
    old_obj = get(first.approval_id, ep)
    assert old_obj is not None and old_obj.status == ApprovalStatus.REJECTED


def test_fingerprint_drift_supersedes(tmp_path: Path) -> None:
    """T4: 指纹漂移惰性检测（§2.3 条件 2 & v0.4 S3-R6）。"""
    ep = _make_episode_at_05(tmp_path, "ep-drift")
    pending_a = ensure_pending(ep)
    assert pending_a is not None

    # 改写 04-clips.json（换内容变 size，仍处 05 停机点）
    clips_path = ep / "04-clips.json"
    data = json.loads(clips_path.read_text(encoding="utf-8"))
    data["extra_comment"] = "fingerprint changed v2"
    clips_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    # 指定 id=A 批准必抛 ApprovalError，且 A 转为 SUPERSEDED
    with pytest.raises(ApprovalError, match="已被替换"):
        approve(ep, "05", approval_id=pending_a.approval_id)

    stored_a = get(pending_a.approval_id, ep)
    assert stored_a is not None
    assert stored_a.status == ApprovalStatus.SUPERSEDED

    # list_pending 恰返回一条新 pending B（指纹为新值、id != A），且 B 未被批准
    pendings_after = list_pending(ep)
    assert len(pendings_after) == 1
    pending_b = pendings_after[0]
    assert pending_b.approval_id != pending_a.approval_id
    assert pending_b.status == ApprovalStatus.PENDING
    assert pending_b.artifacts[0].size == clips_path.stat().st_size

    # 再次漂移 B，测试 REPL 形态 approve(05)（不带 id）在「本次自愈刚替换」时同样抛 ApprovalError
    data["extra_comment"] = "fingerprint changed v3 for repl check"
    clips_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    with pytest.raises(ApprovalError, match="已被替换"):
        approve(ep, "05")
    stored_b = get(pending_b.approval_id, ep)
    assert stored_b is not None and stored_b.status == ApprovalStatus.SUPERSEDED
    pendings_final = list_pending(ep)
    assert len(pendings_final) == 1
    assert pendings_final[0].status == ApprovalStatus.PENDING

    # 03.5 无物理闸门场景：若删除指纹校验，approve 将不抛错而直接转 APPROVED（MUT-3 双重证伪）
    ep_35 = _make_episode_at_03_5(tmp_path, "ep-drift-035")
    p35 = ensure_pending(ep_35)
    assert p35 is not None
    mf_path = ep_35 / "03-audio" / "manifest.json"
    mf_path.write_text(
        json.dumps({"segments": [{"index": 1, "duration": 4.0}, {"index": 2, "duration": 3.5}]}),
        encoding="utf-8",
    )
    with pytest.raises(ApprovalError, match="已被替换"):
        approve(ep_35, "03.5", approval_id=p35.approval_id)
    assert get(p35.approval_id, ep_35).status == ApprovalStatus.SUPERSEDED


def test_gate_artifact_lazy_alignment(tmp_path: Path) -> None:
    """T5: 解封物对齐双闸（05 正常/段级 diff/时序违例 + 02.5 空 patch/旧 patch/正常 patch，杀 MUT-11 & X-2）。"""
    # ① 05 正常路径：造 05 pending 后落有效 approved（mtime >= clips 且段级 diff 为空）-> APPROVED(artifact)
    ep_ok = _make_episode_at_05(tmp_path, "ep-gate-ok")
    obj_ok = ensure_pending(ep_ok)
    assert obj_ok is not None
    _write_valid_approved_clips(ep_ok)
    assert list_pending(ep_ok) == []
    aligned = get(obj_ok.approval_id, ep_ok)
    assert aligned is not None
    assert aligned.status == ApprovalStatus.APPROVED
    assert aligned.resolved_by == "artifact"

    # ② 05 过期路径：approved 存在且 mtime 更新，但与 04-clips.json 存在段级 diff -> 保持 PENDING（杀 MUT-11a）
    ep_diff = _make_episode_at_05(tmp_path, "ep-gate-diff")
    obj_diff = ensure_pending(ep_diff)
    assert obj_diff is not None
    stale_appr = json.loads((ep_diff / "04-clips.json").read_text(encoding="utf-8"))
    stale_appr["segments"][0]["clips"][0]["start"] = 99.0
    appr_path = ep_diff / "04-clips.approved.json"
    appr_path.write_text(json.dumps(stale_appr, ensure_ascii=False), encoding="utf-8")
    clips_ns = (ep_diff / "04-clips.json").stat().st_mtime_ns
    os.utime(appr_path, ns=(clips_ns + 5_000_000, clips_ns + 5_000_000))
    pendings_diff = list_pending(ep_diff)
    assert len(pendings_diff) == 1
    assert pendings_diff[0].approval_id == obj_diff.approval_id
    assert pendings_diff[0].status == ApprovalStatus.PENDING

    # ③ 05 时序违例：段级内容一致，但 approved mtime_ns 早于 04-clips.json -> 保持 PENDING（杀 MUT-11b）
    ep_old = _make_episode_at_05(tmp_path, "ep-gate-old-mtime")
    obj_old = ensure_pending(ep_old)
    assert obj_old is not None
    appr_old_path = _write_valid_approved_clips(ep_old)
    clips_old_ns = (ep_old / "04-clips.json").stat().st_mtime_ns
    os.utime(appr_old_path, ns=(clips_old_ns - 10_000_000, clips_old_ns - 10_000_000))
    pendings_old = list_pending(ep_old)
    assert len(pendings_old) == 1
    assert pendings_old[0].approval_id == obj_old.approval_id
    assert pendings_old[0].status == ApprovalStatus.PENDING

    # ④ 02.5 子场景 A：02-diff.patch 为空（0 字节）-> 保持 PENDING（杀 X-2）
    ep_025_empty = _make_episode_at_02_5(tmp_path, "ep-025-empty-patch")
    obj_025_empty = ensure_pending(ep_025_empty)
    assert obj_025_empty is not None and obj_025_empty.type == "02.5"
    patch_empty = ep_025_empty / "02-diff.patch"
    patch_empty.write_text("", encoding="utf-8")
    script_ns = (ep_025_empty / "02-script.md").stat().st_mtime_ns
    os.utime(patch_empty, ns=(script_ns + 5_000_000, script_ns + 5_000_000))
    pendings_025_empty = list_pending(ep_025_empty)
    assert len(pendings_025_empty) == 1
    assert pendings_025_empty[0].approval_id == obj_025_empty.approval_id
    assert pendings_025_empty[0].status == ApprovalStatus.PENDING

    # ⑤ 02.5 子场景 B：02-diff.patch 非空但 mtime 早于 02-script.md -> 保持 PENDING
    ep_025_old = _make_episode_at_02_5(tmp_path, "ep-025-old-patch")
    obj_025_old = ensure_pending(ep_025_old)
    assert obj_025_old is not None
    patch_old = ep_025_old / "02-diff.patch"
    patch_old.write_text("--- a\n+++ b\n@@ -1 +1 @@\n-a\n+b\n", encoding="utf-8")
    script_old_ns = (ep_025_old / "02-script.md").stat().st_mtime_ns
    os.utime(patch_old, ns=(script_old_ns - 10_000_000, script_old_ns - 10_000_000))
    pendings_025_old = list_pending(ep_025_old)
    assert len(pendings_025_old) == 1
    assert pendings_025_old[0].approval_id == obj_025_old.approval_id
    assert pendings_025_old[0].status == ApprovalStatus.PENDING


def test_emit_degrades_when_jobs_absent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """T10b: 经 pytest monkeypatch 阻断 pipeline.jobs import 时，ack 与对象库照常工作（m2 / MUT-9）。"""
    monkeypatch.setitem(sys.modules, "pipeline.jobs", None)

    ep = _make_episode_at_03_5(tmp_path, "ep-no-jobs")
    pending = ensure_pending(ep)
    assert pending is not None
    assert pending.status == ApprovalStatus.PENDING

    res = approve(ep, "03.5", approval_id=pending.approval_id)
    assert res.status == ApprovalStatus.APPROVED
    stored = get(pending.approval_id, ep)
    assert stored is not None and stored.status == ApprovalStatus.APPROVED


def test_status_card_guidance_line(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """T14: §2.8 状态卡确定性引导行（无对象 / pending / rejected / approved 无驳回行杀 X-1 / 异常降级）。"""
    # 1. 无对象时两行均不出现
    ep_clean = tmp_path / "ep-card-clean"
    ep_clean.mkdir()
    (ep_clean / "01-topic.md").write_text("# Topic\n", encoding="utf-8")
    card_clean = build_status_card(ep_clean)
    assert "待审批:" not in card_clean
    assert "驳回反馈:" not in card_clean

    # 2. 造 pending 后状态卡含 待审批: 与停机点类型
    ep_stop = _make_episode_at_05(tmp_path, "ep-card-stop")
    item = ensure_pending(ep_stop)
    assert item is not None
    card_pending = build_status_card(ep_stop)
    assert "待审批: 05 审时间码（/approvals 查看详情）" in card_pending
    assert "驳回反馈:" not in card_pending
    assert len(card_pending) <= 400

    # 3. reject 后状态卡含 驳回反馈: _agent/approval_feedback.md，且无 待审批:
    reject(
        ep_stop,
        "05",
        {"target": "04-clips.json s01", "problem": "镜头不对"},
        approval_id=item.approval_id,
    )
    card_rejected = build_status_card(ep_stop)
    assert "驳回反馈: _agent/approval_feedback.md" in card_rejected
    assert "待审批:" not in card_rejected
    assert len(card_rejected) <= 400

    # 4. 对象为 APPROVED 时状态卡没有驳回反馈行（杀 X-1）
    ep_approved = _make_episode_at_03_5(tmp_path, "ep-card-approved")
    item_appr = ensure_pending(ep_approved)
    assert item_appr is not None
    approve(ep_approved, "03.5", approval_id=item_appr.approval_id)
    card_approved = build_status_card(ep_approved)
    assert "待审批:" not in card_approved
    assert "驳回反馈:" not in card_approved

    # 5. approvals 模块故障时卡片照常返回（静默降级）
    def _boom(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("simulated approvals failure")

    monkeypatch.setattr(approvals, "get_status_card_guidance", _boom)
    card_degraded = build_status_card(ep_stop)
    assert "[状态卡]" in card_degraded
    assert "待审批:" not in card_degraded
