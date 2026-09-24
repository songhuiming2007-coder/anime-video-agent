"""tests/test_candidates_propose.py: Spec 6 (acquire_propose) 单元测试（T1-T15 全集）。"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import fcntl
import json
from pathlib import Path
import subprocess
import sys
from typing import Any

import pytest

from pipeline import acquire as A
from pipeline import candidates as C
from pipeline.agent.cli import _default_approve
from pipeline.agent.status_card import render_approval_card
from pipeline.agent.tools import (
    TOOL_SCHEMAS,
    ToolContext,
    build_tool_schemas,
    execute_tool,
)
from pipeline.candidates import load_candidates, propose_candidates


def _make_skeleton(tmp_path: Path, *, with_incoming: bool = True) -> Path:
    data_root = tmp_path / "data"
    lib_dir = data_root / "library"
    lib_dir.mkdir(parents=True, exist_ok=True)
    if with_incoming:
        (lib_dir / "incoming").mkdir(parents=True, exist_ok=True)
    return data_root


def _valid_entry(idx: int = 1, **overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "title": f"EGOIST LIVE 2023 横滨终场 候选 {idx}",
        "url": f"https://example.com/watch?v=cand{idx}",
        "type": "live",
        "source": f"YouTube 搜索候选 {idx} 官方投稿",
        "why": f"终场 Live 精华版缺第 {idx} 段消散演出全程，此条时长与标题卡均吻合帧内实证",
        "expected_dur": 7200,
    }
    base.update(overrides)
    return base


# ---- T1: 空 incoming 首批提案 ----


def test_propose_appends_to_fresh_incoming(tmp_path: Path) -> None:
    data_root = _make_skeleton(tmp_path)
    batch = [_valid_entry(1), _valid_entry(2, type="interview", expected_dur=None)]

    res = propose_candidates(batch, data_root=data_root)

    cand_file = data_root / "library" / "incoming" / "candidates.json"
    assert cand_file.exists()
    assert res == {
        "path": str(cand_file),
        "added": [batch[0]["title"], batch[1]["title"]],
        "skipped": [],
        "warnings": [],
    }
    loaded = load_candidates(cand_file.read_text(encoding="utf-8"))
    assert loaded == batch


# ---- T2: schema 拒写 + 损坏 fail-closed ----


def test_propose_invalid_batch_refused_file_untouched(tmp_path: Path) -> None:
    data_root = _make_skeleton(tmp_path)
    inc_dir = data_root / "library" / "incoming"
    cand_file = inc_dir / "candidates.json"

    initial = [_valid_entry(1)]
    cand_file.write_text(json.dumps(initial, ensure_ascii=False, indent=2), encoding="utf-8")
    snapshot_bytes = cand_file.read_bytes()

    # 1) 缺 why
    bad_no_why = _valid_entry(2)
    del bad_no_why["why"]
    with pytest.raises(ValueError, match="缺字段 why"):
        propose_candidates([bad_no_why], data_root=data_root)
    assert cand_file.read_bytes() == snapshot_bytes

    # 2) type="bd"
    with pytest.raises(ValueError, match="type='bd' 不在"):
        propose_candidates([_valid_entry(3, type="bd")], data_root=data_root)
    assert cand_file.read_bytes() == snapshot_bytes

    # 3) 非 http url
    with pytest.raises(ValueError, match="url 不可解析"):
        propose_candidates([_valid_entry(4, url="magnet:?xt=urn:btih:deadbeef")], data_root=data_root)
    assert cand_file.read_bytes() == snapshot_bytes

    # 4) 顶层非数组或空数组
    with pytest.raises(ValueError, match="candidates 必须是非空的对象数组"):
        propose_candidates({"title": "not-a-list"}, data_root=data_root)  # type: ignore[arg-type]
    assert cand_file.read_bytes() == snapshot_bytes

    # 子场景①：既有文件本身含坏条目（人手改坏）→ 拒写且文件字节不动
    corrupted_existing = [{"title": "人手改坏缺why", "url": "https://example.com/bad", "type": "mv", "source": "s"}]
    cand_file.write_text(json.dumps(corrupted_existing, ensure_ascii=False, indent=2), encoding="utf-8")
    corrupted_bytes = cand_file.read_bytes()
    with pytest.raises(ValueError, match="缺字段 why"):
        propose_candidates([_valid_entry(5)], data_root=data_root)
    assert cand_file.read_bytes() == corrupted_bytes

    # 恢复合法 candidates.json，测子场景②：fetched.json 写成非法 JSON（台账损坏）→ ValueError 回喂，不穿透 SystemExit
    cand_file.write_bytes(snapshot_bytes)
    ledger_file = inc_dir / "fetched.json"
    ledger_file.write_text("{broken json", encoding="utf-8")
    with pytest.raises(ValueError, match="台账不是合法 JSON"):
        propose_candidates([_valid_entry(6)], data_root=data_root)
    assert cand_file.read_bytes() == snapshot_bytes


# ---- T3: URL 双源判重 ----


def test_propose_dedup_candidates_and_ledger(tmp_path: Path) -> None:
    data_root = _make_skeleton(tmp_path)
    inc_dir = data_root / "library" / "incoming"

    existing_item = _valid_entry(1, url="https://example.com/in-candidates")
    propose_candidates([existing_item], data_root=data_root)

    ledger_file = inc_dir / "fetched.json"
    ledger_file.write_text(
        json.dumps([{"url": "https://example.com/in-ledger", "file": "SP01.mp4"}], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    dup_cand = _valid_entry(2, title="撞候选清单", url="https://example.com/in-candidates")
    dup_cand_ws = _valid_entry(6, title="撞候选清单带空白", url="  https://example.com/in-candidates \n")
    dup_ledger = _valid_entry(3, title="撞抓取台账", url="https://example.com/in-ledger")
    fresh_first = _valid_entry(4, title="批内首条", url="https://example.com/fresh-batch")
    fresh_second = _valid_entry(5, title="批内重复第二条", url="https://example.com/fresh-batch")

    res = propose_candidates(
        [dup_cand, dup_cand_ws, dup_ledger, fresh_first, fresh_second],
        data_root=data_root,
    )

    assert res["added"] == ["批内首条"]
    assert len(res["skipped"]) == 4
    assert res["skipped"][0]["url"] == "https://example.com/in-candidates"
    assert "候选清单" in res["skipped"][0]["reason"]
    assert res["skipped"][1]["url"] == "https://example.com/in-candidates"
    assert "候选清单" in res["skipped"][1]["reason"]
    assert res["skipped"][2]["url"] == "https://example.com/in-ledger"
    assert "台账" in res["skipped"][2]["reason"]
    assert res["skipped"][3]["url"] == "https://example.com/fresh-batch"
    assert "候选清单" in res["skipped"][3]["reason"]

    loaded = load_candidates((inc_dir / "candidates.json").read_text(encoding="utf-8"))
    assert [c["url"] for c in loaded] == [
        "https://example.com/in-candidates",
        "https://example.com/fresh-batch",
    ]


# ---- T4: append-only 序号契约 ----


def test_propose_append_only_order_stable(tmp_path: Path) -> None:
    data_root = _make_skeleton(tmp_path)
    inc_dir = data_root / "library" / "incoming"
    cand_file = inc_dir / "candidates.json"

    # 前置条件（🟡-11）：预置 3 条清单以同参数 json.dumps(indent=2, ensure_ascii=False) 生成
    initial_3 = [
        _valid_entry(1, title="Z-第一条（故意逆字母序测试防重排）"),
        _valid_entry(2, title="M-第二条（fetch 2 的锚点）"),
        _valid_entry(3, title="A-第三条"),
    ]
    initial_raw = json.dumps(initial_3, ensure_ascii=False, indent=2)
    cand_file.write_text(initial_raw, encoding="utf-8")
    initial_prefix_bytes = initial_raw[:-2].encode("utf-8")

    appended_2 = [
        _valid_entry(4, title="B-追加第四条"),
        _valid_entry(5, title="C-追加第五条"),
    ]
    propose_candidates(appended_2, data_root=data_root)

    after_bytes = cand_file.read_bytes()
    loaded = load_candidates(cand_file.read_text(encoding="utf-8"))

    # ① 读回后前 3 条 dict 逐项相等且顺序不变
    assert loaded[:3] == initial_3
    # ② 新条目在尾部
    assert loaded[3:] == appended_2
    # ③ canonical 前置下既有区段字节级不变
    assert after_bytes[: len(initial_prefix_bytes)] == initial_prefix_bytes
    # ④ load_candidates 读回第 2 条仍是原第 2 条（fetch 2 序号语义不变）
    assert loaded[2 - 1] == initial_3[1]


# ---- T5: 双端 resolve 防穿透 ----


def test_propose_symlink_escape_refused(tmp_path: Path) -> None:
    # 场景①：candidates.json 是指向 tmp 外文件的 symlink → PermissionError 且外部文件零改动
    data_root_1 = _make_skeleton(tmp_path / "case1")
    outside_dir = tmp_path / "outside"
    outside_dir.mkdir(parents=True, exist_ok=True)
    outside_file = outside_dir / "secret_candidates.json"
    outside_initial = json.dumps([_valid_entry(99, title="外域条目")], ensure_ascii=False, indent=2)
    outside_file.write_text(outside_initial, encoding="utf-8")
    outside_snapshot = outside_file.read_bytes()

    cand_symlink = data_root_1 / "library" / "incoming" / "candidates.json"
    cand_symlink.symlink_to(outside_file)

    with pytest.raises(PermissionError, match="路径越界"):
        propose_candidates([_valid_entry(1)], data_root=data_root_1)
    assert outside_file.read_bytes() == outside_snapshot
    assert cand_symlink.is_symlink()

    # 场景②：incoming/ 本身是指向 tmp 外目录的 symlink（逃逸 data/library）→ PermissionError
    data_root_2 = _make_skeleton(tmp_path / "case2", with_incoming=False)
    outside_inc = outside_dir / "escaped_incoming"
    outside_inc.mkdir(parents=True, exist_ok=True)
    inc_symlink = data_root_2 / "library" / "incoming"
    inc_symlink.symlink_to(outside_inc)

    with pytest.raises(PermissionError, match="路径越界"):
        propose_candidates([_valid_entry(2)], data_root=data_root_2)
    assert not (outside_inc / "candidates.json").exists()


# ---- T6: 并发写纪律（同进程多线程 + 跨进程 flock 挡写）----


def test_propose_concurrent_writers_serialized(tmp_path: Path) -> None:
    data_root = _make_skeleton(tmp_path)
    inc_dir = data_root / "library" / "incoming"
    cand_file = inc_dir / "candidates.json"

    # 线程腿：同进程 4 线程 × 5 条并发提案 → 20 条全在、JSON 合法、无交织
    def _worker(tid: int) -> None:
        batch = [_valid_entry(tid * 100 + j) for j in range(5)]
        propose_candidates(batch, data_root=data_root)

    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = [pool.submit(_worker, t) for t in range(1, 5)]
        for fut in futures:
            fut.result()

    loaded = load_candidates(cand_file.read_text(encoding="utf-8"))
    assert len(loaded) == 20

    # 跨进程腿：主进程对 candidates.lock 持 LOCK_EX|LOCK_NB，子进程提案在 2s 内无法完成；释锁后子进程完成
    lock_file = inc_dir / "candidates.lock"
    with lock_file.open("a", encoding="utf-8") as lf:
        fcntl.flock(lf.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        child_code = (
            "from pathlib import Path; "
            "from pipeline.candidates import propose_candidates; "
            f"propose_candidates([{{'title': '跨进程条目', 'url': 'https://example.com/subproc', "
            f"'type': 'live', 'source': 'subproc', "
            f"'why': '跨进程锁验证条目，确保主进程释放 LOCK_EX 后子进程完成原子写入', "
            f"'expected_dur': 120}}], data_root=Path({str(data_root)!r}))"
        )
        proc = subprocess.Popen(
            [sys.executable, "-c", child_code],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            with pytest.raises(subprocess.TimeoutExpired, match="timed out"):
                proc.wait(timeout=0.6)
        finally:
            fcntl.flock(lf.fileno(), fcntl.LOCK_UN)

        out, err = proc.communicate(timeout=5.0)
        assert proc.returncode == 0, f"子进程失败: {err}\n{out}"

    loaded_after = load_candidates(cand_file.read_text(encoding="utf-8"))
    assert len(loaded_after) == 21
    assert loaded_after[-1]["url"] == "https://example.com/subproc"


# ---- T7: 存储红线（data 不可达 fail-closed）----


def test_propose_data_unreachable_fail_closed(tmp_path: Path) -> None:
    # ① data/ 不存在 → PermissionError，且 data/ 未被创建
    missing_data = tmp_path / "nonexistent_data"
    with pytest.raises(PermissionError, match="data 根目录不可达"):
        propose_candidates([_valid_entry(1)], data_root=missing_data)
    assert not missing_data.exists()

    # 悬空符号链接同样不创建
    dangling_data = tmp_path / "dangling_data"
    dangling_data.symlink_to(tmp_path / "unmounted_volume")
    with pytest.raises(PermissionError, match="data 根目录不可达"):
        propose_candidates([_valid_entry(1)], data_root=dangling_data)
    assert not (tmp_path / "unmounted_volume").exists()

    # ② data/ 在但无 library/ → PermissionError
    bare_data = tmp_path / "bare_data"
    bare_data.mkdir(parents=True)
    with pytest.raises(PermissionError, match="data/library 骨架不全"):
        propose_candidates([_valid_entry(1)], data_root=bare_data)
    assert not (bare_data / "library").exists()

    # ③ 合法骨架下 incoming/ 缺失 → 自动创建且提案成功
    valid_data = _make_skeleton(tmp_path / "ok_case", with_incoming=False)
    assert not (valid_data / "library" / "incoming").exists()
    res = propose_candidates([_valid_entry(1)], data_root=valid_data)
    assert (valid_data / "library" / "incoming" / "candidates.json").exists()
    assert len(res["added"]) == 1


# ---- T8: why 软 WARN 不阻断 ----


def test_propose_why_lint_warns_not_blocks(tmp_path: Path) -> None:
    data_root = _make_skeleton(tmp_path)

    e_maybe = _valid_entry(
        1,
        title="含可能条目",
        why="这条视频可能是横滨终场的全场录像，时长与帧内标题卡看着比较接近",
    )
    e_short = _valid_entry(
        2,
        title="过短条目",
        why="补终场消散段落缺口",  # 9 字 (<20)
    )
    e_good = _valid_entry(
        3,
        title="充分条目",
        why="终场 Live 只入库了 19 分钟精华版（SP05），这条是 2 小时全程，补「消散段落」的完整过程",
    )

    res = propose_candidates([e_maybe, e_short, e_good], data_root=data_root)

    assert res["added"] == ["含可能条目", "过短条目", "充分条目"]
    warn_by_title = {w["title"]: w["warning"] for w in res["warnings"]}
    assert set(warn_by_title.keys()) == {"含可能条目", "过短条目"}
    assert "可能" in warn_by_title["含可能条目"]
    assert "<20" in warn_by_title["过短条目"]

    cand_file = data_root / "library" / "incoming" / "candidates.json"
    loaded = load_candidates(cand_file.read_text(encoding="utf-8"))
    assert len(loaded) == 3


# ---- T12: acquire re-export identity 与路径语义断言 ----


def test_acquire_reexport_identity(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    names = (
        "load_candidates",
        "slugify",
        "ledger_load",
        "ledger_save",
        "ledger_seen_urls",
        "_line_of",
        "incoming",
        "candidates_path",
        "ledger_path",
        "TYPES",
    )
    for name in names:
        assert getattr(A, name) is getattr(C, name), f"{name} 非同一对象"

    # 验证下移后的 incoming()/candidates_path()/ledger_path() 与默认参数 ledger_load/ledger_save 路径语义
    data_root = _make_skeleton(tmp_path, with_incoming=False)
    from pipeline import paths
    monkeypatch.setattr(paths, "require_data", lambda: data_root)

    expected_inc = data_root / "library" / "incoming"
    assert A.incoming() == expected_inc
    assert expected_inc.is_dir()
    assert A.candidates_path() == expected_inc / "candidates.json"
    assert A.ledger_path() == expected_inc / "fetched.json"

    sample_ledger = [{"url": "https://example.com/sp01", "file": "SP01.mp4"}]
    A.ledger_save(sample_ledger)
    assert (expected_inc / "fetched.json").exists()
    assert A.ledger_load() == sample_ledger


# ---- T13①: 独立子进程纯洁性测试（candidates 顶层零重依赖零网络模块）----


def test_candidates_module_pure_subprocess() -> None:
    probe = (
        "import pipeline.candidates, sys; "
        "forbidden = ('numpy', 'torch', 'sentence_transformers', 'pysubs2', "
        "             'moviepy', 'requests', 'httpx', 'socket', 'subprocess'); "
        "leaked = [m for m in forbidden if m in sys.modules]; "
        "assert not leaked, f'candidates 顶层违规引入: {leaked}'"
    )
    res = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True)
    assert res.returncode == 0, f"纯洁性检验失败:\n{res.stderr}"


# ---- T13②: tools.py 顶层不拉 pipeline.candidates（延迟 import + 实调 execute_tool 断言）----


def test_tools_import_does_not_pull_candidates() -> None:
    probe = (
        "import pipeline.agent.tools as T, sys, tempfile; "
        "from pathlib import Path; "
        "assert 'pipeline.candidates' not in sys.modules, 'tools 顶层拉入 candidates'; "
        "assert 'pipeline.acquire' not in sys.modules, 'tools 顶层拉入 acquire（ML 链）'; "
        "td = tempfile.TemporaryDirectory(); "
        "root = Path(td.name); "
        "(root / 'data' / 'library').mkdir(parents=True); "
        "(root / 'config' / 'agent').mkdir(parents=True); "
        "(root / 'config' / 'agent' / 'tools.json').write_text('{\"asset\": [\"acquire_propose\"]}', encoding='utf-8'); "
        "out = T.execute_tool('acquire_propose', {'candidates': [{"
        "'title': '子进程实调', 'url': 'https://example.com/sub', 'type': 'live', "
        "'source': 's', 'why': '验证延迟 import 下实调 execute_tool 成功落盘且不拉入 acquire'}]}, "
        "T.ToolContext(scope='asset', root=root)); "
        "assert out.get('ok') is True, f'execute_tool 失败: {out}'; "
        "assert 'pipeline.candidates' in sys.modules, '实调后应按需加载 candidates'; "
        "assert 'pipeline.acquire' not in sys.modules, '实调后仍绝不拉入 acquire（ML 链）'; "
        "td.cleanup()"
    )
    res = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True)
    assert res.returncode == 0, f"延迟 import 检验失败:\n{res.stderr}"


# ---- T9: scope 掩码三层断言 ----


def test_acquire_propose_scope_mask(tmp_path: Path) -> None:
    # ① 注册表常驻
    assert "acquire_propose" in TOOL_SCHEMAS
    assert len(TOOL_SCHEMAS) == 9

    # ② build_tool_schemas 仅 asset 含 acquire_propose
    def _schema_names(scope: str) -> list[str]:
        return [s["function"]["name"] for s in build_tool_schemas(scope)]

    assert "acquire_propose" in _schema_names("asset")
    assert "acquire_propose" not in _schema_names("creative")
    assert "acquire_propose" not in _schema_names("pipeline")
    assert "acquire_propose" not in _schema_names("idea")

    # ③ execute_tool 执行层第二道闸：非 asset scope 一律拒
    for denied_scope in ("pipeline", "creative", "idea"):
        res = execute_tool(
            "acquire_propose",
            {"candidates": [_valid_entry(1)]},
            ToolContext(scope=denied_scope, root=tmp_path),
        )
        assert res["ok"] is False
        assert "白名单" in res["error"]


# ---- T10: 人审卡纪律（side_effect fail-closed + ADR-0021）----


def test_acquire_propose_pops_approval_card() -> None:
    schema = TOOL_SCHEMAS["acquire_propose"]
    assert schema.get("side_effect", True) is True
    assert schema.get("adr") == "ADR-0021"


# ---- T11: 宿主元数据零泄漏 ----


def test_protocol_keys_no_leak() -> None:
    for item in build_tool_schemas("asset"):
        fn_keys = set(item["function"].keys())
        assert fn_keys == {"name", "description", "parameters"}
        assert "adr" not in fn_keys
        assert "side_effect" not in fn_keys


# ---- T15: 审批卡非空 + 记账可定位 + 卡面清洗 ----


def test_approval_card_and_ledger_target(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # ① render_approval_card("acquire_propose", args) 输出逐条含 title 与 URL，>10 条截断为「…共 11 条」
    items_11 = [_valid_entry(i, title=f"候选标题-{i}", url=f"https://example.com/v/{i}") for i in range(1, 12)]
    card_11 = render_approval_card("acquire_propose", {"candidates": items_11})
    for i in range(1, 11):
        assert f"候选标题-{i}" in card_11
        assert f"https://example.com/v/{i}" in card_11
    assert "候选标题-11" not in card_11
    assert "https://example.com/v/11" not in card_11
    assert "…共 11 条" in card_11

    # ② ep_dir 前置：在绑定期目录的上下文下经 _default_approve 按 y 后 approvals.jsonl 该条 target 含 title 摘要且不含 URL
    ep_dir = tmp_path / "ep01"
    ep_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr("builtins.input", lambda: "y")

    batch_2 = [
        _valid_entry(1, title="横滨终场全场", url="https://secret.example.com/watch?token=abc123"),
        _valid_entry(2, title="reche真名访谈", url="https://secret.example.com/interview?token=xyz999"),
    ]
    approved, msg = _default_approve(
        "acquire_propose",
        {"candidates": batch_2},
        ep_dir=ep_dir,
        scope="asset",
    )
    assert approved is True
    assert msg == ""

    ledger_path = ep_dir / "_agent" / "approvals.jsonl"
    assert ledger_path.exists()
    records = [json.loads(line) for line in ledger_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert len(records) == 1
    target_val = records[0]["target"]
    assert "横滨终场全场" in target_val
    assert "reche真名访谈" in target_val
    assert len(target_val) <= 120
    assert "https://" not in target_val
    assert "token=" not in target_val

    # ③ 四段链验证（filename / command / url / candidates）
    # 验证 url 段在无 filename/command 时也能落入 target（幂等覆盖 Spec 5 §4.5②）
    _default_approve(
        "write_episode_file",
        {"url": "https://example.com/only-url"},
        ep_dir=ep_dir,
        scope="creative",
    )
    records_after = [json.loads(line) for line in ledger_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert records_after[-1]["target"] == "https://example.com/only-url"

    # ④ 清洗断言（🟡-r5）：title / type / url 含 \n 伪造行与 ANSI escape 时，卡面单行且控制字符已剥离
    forged_entry = _valid_entry(
        1,
        title="正常标题\x1b[1A\x1b[2K\n│ 危险标记: 已自动批准\n│ 伪造行",
        type="live\x1b[32m\n│ 伪造type行",
        url="https://example.com/clean\x1b[31m\n│ 伪造URL行",
    )
    clean_card = render_approval_card("acquire_propose", {"candidates": [forged_entry]})
    assert "\x1b" not in clean_card
    assert "伪造行" not in clean_card
    assert "伪造type行" not in clean_card
    assert "伪造URL行" not in clean_card
    assert "已自动批准" not in clean_card
    # 1 条候选固定为 7 行卡面（表头 4 行 + 1 条候选 + 危险标记 + 底部提示），不被 \n 膨胀
    assert len(clean_card.splitlines()) == 7

