"""文档不变量测试（PR0 固化，进总闸）

验证：
1. AGENTS.md 是唯一常驻规则真源，CLAUDE.md 已废除（由 Claude Code 2.1.277+ 原生支持）；
2. 操作类文档白名单内文件存在、超 N 字、且受 git 追踪；
3. 操作类文档绝不含旧手册变体串（rm seg, 删除.*seg, ava-cloud）；
4. 配音操作与导航文档具备正向新路锚点（--apply-patch 或 corrections.json）；
5. Implementation Spec 头部版本号与状态行版本号严格一致；
6. ADR 编号连续，且 ADR-0018 / ADR-0019 具备推翻条件小节。
"""

from pathlib import Path
import re
import subprocess
import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent

# 操作类规程文档白名单（记录类天然豁免，见 spec §5 PR0）
OPERATIONAL_DOCS = [
    "AGENTS.md",
    "README.md",
    "docs/INDEX.md",
    "docs/WORKFLOW.md",
    "docs/dev/STANDARD.md",
    "docs/dev/HELP.md",
    "docs/runbook/01-topic.md",
    "docs/runbook/02-script.md",
    "docs/runbook/02.5-human-review.md",
    "docs/runbook/03-tts.md",
    "docs/runbook/03.5-voice-check.md",
    "docs/runbook/04-clips.md",
    "docs/runbook/05-timecode.md",
    "docs/runbook/06-render.md",
    "docs/runbook/07-qc.md",
    "docs/runbook/08-cover-title.md",
    "docs/runbook/09-publish.md",
]

# 涉及配音规程与工序导航的核心操作文档（必须包含新路锚点）
VOICE_DOCS = [
    "AGENTS.md",
    "README.md",
    "docs/INDEX.md",
    "docs/WORKFLOW.md",
    "docs/dev/STANDARD.md",
    "docs/dev/HELP.md",
    "docs/runbook/03-tts.md",
    "docs/runbook/03.5-voice-check.md",
    "docs/runbook/04-clips.md",
]

FORBIDDEN_VARIANTS = [
    (re.compile(r"rm\s+seg", re.IGNORECASE), "rm seg"),
    (re.compile(r"删除.*seg", re.IGNORECASE), "删除.*seg"),
    (re.compile(r"ava-cloud", re.IGNORECASE), "ava-cloud"),
]


def test_agents_md_is_unified_ssot_and_no_claude_md():
    """AGENTS.md 是唯一常驻规则真源，CLAUDE.md 已废除（由 Claude Code 2.1.277+ 原生支持）"""
    claude_path = REPO_ROOT / "CLAUDE.md"
    agents_path = REPO_ROOT / "AGENTS.md"

    assert not claude_path.exists(), (
        "CLAUDE.md 已于 2026-09-19 正式废除（Claude Code ≥2.1.277 已原生支持 AGENTS.md），"
        "严禁重新引入双写维护！"
    )
    assert agents_path.exists(), "AGENTS.md 必须存在且作为唯一真源"

    agents_lines = agents_path.read_text(encoding="utf-8").splitlines()
    assert len(agents_lines) > 50, "AGENTS.md 正文过短"


def test_operational_docs_exist_and_tracked_in_git():
    """操作类文档必须存在、正文非空、且被 git 追踪"""
    git_files = set(
        subprocess.check_output(
            ["git", "ls-files"], cwd=REPO_ROOT, text=True
        ).splitlines()
    )

    for rel_path in OPERATIONAL_DOCS:
        doc_path = REPO_ROOT / rel_path
        assert doc_path.exists(), f"操作文档缺失: {rel_path}"
        content = doc_path.read_text(encoding="utf-8")
        assert len(content.strip()) > 100, f"操作文档正文过短（疑似空壳）: {rel_path}"
        assert rel_path in git_files, f"操作文档未被 git 追踪: {rel_path}"


def test_operational_docs_no_forbidden_variants():
    """操作类文档绝不含已废除旧手册变体串（打印命中清单）"""
    violations = []

    for rel_path in OPERATIONAL_DOCS:
        doc_path = REPO_ROOT / rel_path
        if not doc_path.exists():
            continue
        lines = doc_path.read_text(encoding="utf-8").splitlines()
        for line_no, line in enumerate(lines, 1):
            for pattern, name in FORBIDDEN_VARIANTS:
                if pattern.search(line):
                    violations.append(
                        f"{rel_path}:{line_no}: 命中废弃串 '{name}' -> {line.strip()}"
                    )

    if violations:
        msg = "发现操作文档包含废弃变体串：\n" + "\n".join(violations)
        pytest.fail(msg)


def test_voice_docs_contain_positive_anchors():
    """核心配音与导航操作类文档必须出现新路锚点（--apply-patch 或 corrections.json）"""
    anchor_pattern = re.compile(r"(--apply-patch|corrections\.json)")

    for rel_path in VOICE_DOCS:
        doc_path = REPO_ROOT / rel_path
        assert doc_path.exists(), f"文档缺失: {rel_path}"
        content = doc_path.read_text(encoding="utf-8")
        assert anchor_pattern.search(content), (
            f"{rel_path} 缺少新路锚点（--apply-patch 或 corrections.json）"
        )


def test_spec_header_and_status_version_consistent():
    """Impl Spec 头部日期行版本号与状态行版本号必须一致"""
    spec_path = REPO_ROOT / "docs/dev/plans/2026-09-18-ava-agent-impl-spec.md"
    assert spec_path.exists(), "Impl spec 不存在"

    content = spec_path.read_text(encoding="utf-8")
    lines = content.splitlines()[:20]

    header_version = None
    status_version = None

    for line in lines:
        if "日期：" in line and not header_version:
            m = re.search(r"\*\*(v\d+\.\d+)\*\*", line)
            if m:
                header_version = m.group(1)
        if "状态：" in line and not status_version:
            m = re.search(r"\*\*(v\d+\.\d+)", line)
            if m:
                status_version = m.group(1)

    assert header_version, "未在 spec 头部日期行解析出版本号"
    assert status_version, "未在 spec 状态行解析出版本号"
    assert header_version == status_version, (
        f"Spec 头部版本号 ({header_version}) 与状态行版本号 ({status_version}) 不一致"
    )


def test_adrs_0018_0019_exist_with_reversal_conditions():
    """ADR-0018 与 ADR-0019 必须存在且包含推翻条件"""
    adr_dir = REPO_ROOT / "docs/dev/adr"
    adr18 = list(adr_dir.glob("0018-*.md"))
    adr19 = list(adr_dir.glob("0019-*.md"))

    assert len(adr18) == 1, "缺少 ADR-0018"
    assert len(adr19) == 1, "缺少 ADR-0019"

    for adr_path in [adr18[0], adr19[0]]:
        content = adr_path.read_text(encoding="utf-8")
        assert "推翻" in content, f"{adr_path.name} 缺少推翻条件小节"

    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    assert "ADR-0018" in readme, "README.md 缺少 ADR-0018 引用"
    assert "ADR-0019" in readme, "README.md 缺少 ADR-0019 引用"
