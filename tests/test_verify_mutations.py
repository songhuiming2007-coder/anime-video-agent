"""变异验证 harness（`scripts/verify_mutations.py`）自身的安全护栏测试。

为什么单独测一个脚本：它的恢复动作是 `git checkout -- <file>`，**会连未提交改动
一起丢掉**。2026-09-20 这个形状的事故发生过两次（毁掉 `cli.py` 的审批耗时读数、
毁掉 `tools.py` 的 Ctrl-C kill 修复），两次都靠运气发现。所以这套 harness 的三条
安全行为必须有测试钉住：

1. 工作树不干净 → ABORT（exit 2），且**在动手之前**就停；
2. 锚点命中数 ≠ 1 → 报错而不是静默跳过（静默跳过 = 这条护栏事实上没在验）；
3. 跑测试过程中炸了 → 仍要恢复文件（try/finally）。
"""

from __future__ import annotations

import importlib.util
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "verify_mutations.py"

MUT = {"id": "X", "guard": "探针护栏", "file": "target.py",
       "old": "VALUE = 1", "new": "VALUE = 9"}


def _load_module():
    spec = importlib.util.spec_from_file_location("verify_mutations", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def _make_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    for cmd in (["init", "-q"], ["config", "user.email", "t@e.st"],
                ["config", "user.name", "t"]):
        subprocess.run(["git", *cmd], cwd=repo, check=True, capture_output=True)
    (repo / "target.py").write_text("VALUE = 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=repo, check=True, capture_output=True)
    return repo


def test_dirty_tree_aborts_before_touching_anything(tmp_path, capsys):
    mod = _load_module()
    repo = _make_repo(tmp_path)
    (repo / "target.py").write_text("VALUE = 2\n", encoding="utf-8")  # 未提交改动

    assert mod.main(["--repo", str(repo), "--only", "M1"]) == 2

    assert "ABORT" in capsys.readouterr().err
    assert (repo / "target.py").read_text(encoding="utf-8") == "VALUE = 2\n", \
        "硬闸必须在动手之前触发，不能先改后拦"


def test_dirty_ok_runs_but_labels_results_unreproducible(tmp_path, capsys):
    mod = _load_module()
    repo = _make_repo(tmp_path)
    (repo / "target.py").write_text("VALUE = 2\n", encoding="utf-8")

    assert mod.main(["--repo", str(repo), "--only", "M1", "--dirty-ok"]) == 0

    out = capsys.readouterr().out
    assert "--dirty-ok" in out and "不对应任何 commit" in out


def test_anchor_miss_is_reported_not_silently_skipped(tmp_path):
    mod = _load_module()
    repo = _make_repo(tmp_path)
    h = mod.Harness(repo)

    row = h.check_one({**MUT, "old": "NOT_IN_FILE"})

    assert row["failed"] is None
    assert "anchor matched 0 times" in row["error"]
    assert (repo / "target.py").read_text(encoding="utf-8") == "VALUE = 1\n"


def test_revert_still_happens_when_suite_explodes(tmp_path):
    """两次真实事故的形状：跑测试这一步炸了，未提交改动不能被留在变异态。"""
    mod = _load_module()
    repo = _make_repo(tmp_path)
    h = mod.Harness(repo)

    def boom():
        raise RuntimeError("套件自己炸了")

    h.run_suite = boom
    with pytest.raises(RuntimeError):
        h.check_one(MUT)

    assert (repo / "target.py").read_text(encoding="utf-8") == "VALUE = 1\n", \
        "恢复必须走 finally，不能在正常路径上"


def test_anchors_in_shipped_matrix_are_unique_in_repo():
    """矩阵里的锚点必须当场命中且唯一——锚点过期=这条护栏事实上没在验。"""
    mod = _load_module()
    misses = []
    for mut in mod.MUTATIONS:
        text = (REPO_ROOT / mut["file"]).read_text(encoding="utf-8")
        n = text.count(mut["old"])
        if n != 1:
            misses.append(f"{mut['id']} @ {mut['file']}: 命中 {n} 次")
    assert not misses, "过期/歧义锚点：\n" + "\n".join(misses)
