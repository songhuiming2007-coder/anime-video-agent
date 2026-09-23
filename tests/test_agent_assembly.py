"""纯函数测试：上下文装配器与路由引擎（Spec 1 PR1）。
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from pipeline.agent.assembly import (
    AssembledResident,
    InjectedDoc,
    SessionContextTracker,
    assemble_resident_prompt,
    load_injected_doc,
    render_step_injection,
    resolve_step_docs,
    step_key_of,
)


def test_assembly_zero_heavy_deps():
    """装配器热路径严禁拖入重依赖（独立子进程探针，Spec 1 §6.1）。"""
    probe_code = (
        "import pipeline.agent.assembly, sys; "
        "forbidden = ('numpy', 'torch', 'mlx_whisper', 'moviepy', 'transformers'); "
        "leaked = [m for m in forbidden if m in sys.modules]; "
        "assert not leaked, f'装配器顶层违规引入重量级包: {leaked}'"
    )
    res = subprocess.run(
        [sys.executable, "-c", probe_code],
        capture_output=True,
        text=True,
    )
    assert res.returncode == 0, f"依赖纯洁性检验失败:\n{res.stderr}"


@pytest.mark.parametrize("current_step,expected", [
    ("01 选题", "01"),
    ("02 脚本写作", "02"),
    ("02 脚本写作（草稿待定稿）", "02"),
    ("02.5 人审改稿", "02.5"),
    ("03 语音合成", "03"),
    ("03.5 配音顺听 / 04 排片", "03.5"),
    ("05 审时间码", "05"),
    ("06 本地渲染", "06"),
    ("07 自动质检", "07"),
    ("07 自动质检（未通过）", "07"),
    ("08 封面与标题候选", "08"),
    ("09 人工发布", "09"),
    (None, "default"),
    ("未知状态", "default"),
])
def test_step_key_of_full_coverage(current_step, expected):
    """工序键映射全覆盖（Spec 1 §6.2）。"""
    assert step_key_of(current_step) == expected


def test_load_injected_doc_symlink_loop(tmp_path):
    """循环软链安全返回 None，不崩溃（Spec 1 §6.3）。"""
    doc = tmp_path / "doc.md"
    doc.symlink_to(doc)  # 自指循环
    result = load_injected_doc(str(doc.relative_to(tmp_path)), root=tmp_path)
    assert result is None


def test_load_injected_doc_encoding_fallback(tmp_path):
    """非 UTF-8 字符降级替换，不抛出 UnicodeDecodeError（Spec 1 §6.3）。"""
    doc = tmp_path / "doc.md"
    doc.write_bytes(b"\xff\xfe\x00\x01")  # 非法 UTF-8
    result = load_injected_doc(str(doc.relative_to(tmp_path)), root=tmp_path)
    assert result is not None
    assert result.content  # 已替换为可显示字符


def test_resolve_step_docs_corrupted_config(tmp_path):
    """配置 JSON 损坏时回退空清单，不抛出 KeyError（Spec 1 §6.3）。"""
    config = tmp_path / "assembly.json"
    config.write_text("{invalid json", encoding="utf-8")
    result = resolve_step_docs("creative", "02", config_path=config, root=tmp_path)
    assert result == []


def test_resolve_step_docs_routes():
    """解析真实 assembly.json 路由表与继承机制。"""
    # 1. creative 继承 _base 并覆盖 02
    creative_02 = resolve_step_docs("creative", "02")
    assert len(creative_02) == 2
    assert Path("docs/runbook/02-script.md") in creative_02
    assert Path("skills/write-script/SKILL.md") in creative_02

    # 2. pipeline 纯继承 _base，02 只有 1 个文档
    pipeline_02 = resolve_step_docs("pipeline", "02")
    assert len(pipeline_02) == 1
    assert pipeline_02[0] == Path("docs/runbook/02-script.md")

    # 3. 03.5 复合状态注入两篇文档
    pipeline_035 = resolve_step_docs("pipeline", "03.5")
    assert len(pipeline_035) == 2
    assert Path("docs/runbook/03.5-voice-check.md") in pipeline_035
    assert Path("docs/runbook/04-clips.md") in pipeline_035

    # 4. idea scope 回退为空清单
    idea_01 = resolve_step_docs("idea", "01")
    assert idea_01 == []
    idea_default = resolve_step_docs("idea", "default")
    assert idea_default == []


def test_assemble_resident_prompt_structure():
    """常驻层组装三源内容且字节级稳定。"""
    resident = assemble_resident_prompt("creative")
    assert isinstance(resident, AssembledResident)
    assert resident.scope == "creative"
    assert resident.token_estimate > 0
    # 必须包含 director 人格、scope 边界与 AGENTS.md 核心内容
    assert "总监" in resident.content or "Director" in resident.content
    assert "Creative Scope" in resident.content or "创意" in resident.content
    assert "AGENTS.md" in resident.content or "常驻规则" in resident.content

    # 再次组装内容完全一致（字节级恒定）
    resident_2 = assemble_resident_prompt("creative")
    assert resident.content == resident_2.content


def test_session_context_tracker():
    """SessionContextTracker 首轮与警告去重行为。"""
    tracker = SessionContextTracker(resident_prompt="RESIDENT_PROMPT", active_scope="creative")
    init_prompt = tracker.get_initial_system_prompt("STATUS_CARD")
    assert init_prompt == "RESIDENT_PROMPT\n\n---\n\nSTATUS_CARD"
    assert tracker.active_scope == "creative"

    # 警告去重测试
    tracker.warn_once("foo/bar.md", "缺失文件 1")
    assert "foo/bar.md" in tracker._warned_paths
    tracker.warn_once("foo/bar.md", "缺失文件 1（重复）")
    assert len(tracker._warned_paths) == 1


def test_render_step_injection():
    """测试工序层注入文本渲染。"""
    doc1 = InjectedDoc(
        rel_path="docs/runbook/01.md",
        abs_path=Path("/tmp/01.md"),
        content="# Runbook 01\n内容 1",
        token_estimate=10,
    )
    doc2 = InjectedDoc(
        rel_path="docs/runbook/02.md",
        abs_path=Path("/tmp/02.md"),
        content="# Runbook 02\n内容 2",
        token_estimate=10,
    )

    rendered = render_step_injection([doc1, doc2], step_name="01 选题")
    assert "[系统提示更新] 当前工序已进入 01 选题。" in rendered
    assert "# Runbook 01\n内容 1" in rendered
    assert "# Runbook 02\n内容 2" in rendered
    assert "**注意**：以上规程仅适用于当前工序" in rendered

    # 空列表返回空字符串
    assert render_step_injection([]) == ""
