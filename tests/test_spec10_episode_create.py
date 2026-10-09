"""Spec 10 PR0 的 core 侧用例（TY-1~TY-6、TY-8、TY-9）。

C10-R1：`create_new_episode` 的检查与写入同源、名字规则、退出码；
C10-R2：`api_key_env_name` 只读配置；C10-R3：01 的「类型」未填 = 01 未完成；
C10-R4：`TOPIC_FIELDS` 不再跨行。
"""

from __future__ import annotations

from pathlib import Path
import json
import os
import subprocess
import sys

import pytest

from pipeline.agent.cli import create_new_episode, main
from pipeline.agent.llm import api_key_env_name, load_llm_config

REPO_ROOT = Path(__file__).resolve().parent.parent
TOPIC_TEMPLATE = "# {name} 选题配置\n\n番：\n类型：\n模式：\n张力：\n锚点：\n"


def _scaffold(tmp_path: Path, monkeypatch, *, library: bool = True, episodes: bool = True) -> Path:
    """临时仓库骨架 + 把 `paths.ROOT` 指过去（C10-R1 后检查落在它下面）。"""
    from pipeline import paths

    monkeypatch.setattr(paths, "ROOT", tmp_path)
    data = tmp_path / "data"
    if library:
        (data / "library").mkdir(parents=True)
    if episodes:
        (data / "episodes").mkdir(parents=True)
    return tmp_path


def _tree(root: Path) -> list[str]:
    return sorted(str(p.relative_to(root)) for p in root.rglob("*"))


# --- TY-1：data/ 不可达四场景，退出码恰为 2、stderr 原文、零创建 -------------


def test_ty1_data_absent(tmp_path, monkeypatch, capsys):
    _scaffold(tmp_path, monkeypatch, library=False, episodes=False)
    before = _tree(tmp_path)
    assert create_new_episode("01-x") == 2
    err = capsys.readouterr().err
    assert "不存在。先建存储骨架" in err
    assert _tree(tmp_path) == before


def test_ty1_data_dangling_symlink(tmp_path, monkeypatch, capsys):
    _scaffold(tmp_path, monkeypatch, library=False, episodes=False)
    (tmp_path / "data").symlink_to(tmp_path / "nowhere")
    assert create_new_episode("01-x") == 2
    err = capsys.readouterr().err
    assert "是悬空的符号链接" in err
    assert not (tmp_path / "nowhere").exists()


def test_ty1_library_absent(tmp_path, monkeypatch, capsys):
    _scaffold(tmp_path, monkeypatch, library=False)
    before = _tree(tmp_path)
    assert create_new_episode("01-x") == 2
    err = capsys.readouterr().err
    assert "骨架不全" in err
    assert _tree(tmp_path) == before


def test_ty1_episodes_absent(tmp_path, monkeypatch, capsys):
    _scaffold(tmp_path, monkeypatch, episodes=False)
    before = _tree(tmp_path)
    assert create_new_episode("01-x") == 2
    assert "先跑 ./pipeline/preflight.sh --init" in capsys.readouterr().err
    assert _tree(tmp_path) == before
    # 少了的那一层绝不被补出来（AGENTS.md 五）
    assert not (tmp_path / "data" / "episodes").exists()


def test_ty1_real_data_untouched(tmp_path, monkeypatch, capsys):
    """同源后检查落在临时根：真实 data/ 的 mtime 不变。"""
    from pipeline import paths

    _scaffold(tmp_path, monkeypatch, library=False, episodes=False)
    real_data = paths.DATA
    if not real_data.exists():
        pytest.skip("本机没有真实 data/")
    before = real_data.stat().st_mtime_ns
    assert create_new_episode("01-x") == 2
    err = capsys.readouterr().err
    # 报的是临时根下的 data/（同源），且真实盘的 mtime 不动
    assert str(tmp_path / "data") in err and "不存在。先建存储骨架" in err
    assert real_data.stat().st_mtime_ns == before


# --- TY-2 / TY-3：名字规则与合法名 ------------------------------------------


BAD_NAMES = [
    "../x",
    "a/b",
    "a\\b",
    "",
    "  ",
    " x",
    "x ",
    "_x",
    ".x",
    ".",
    "..",
    "-x",
    "--continue",
    "含\x07控制符",
    "含\x00NUL",
    "长" * 86,  # 3 字节 × 86 = 258 > 255
    # 末尾句读：从 agent 回复整句复制带进来的（2026-10-10 伪恋一期，D63）
    "2026-10-10-伪恋-橘万里花的进攻哲学。",
    "x，",
    "x、",
    "x；",
    "x：",
    "x．",
    "x.",
    "x,",
    "x;",
    "x:",
]

#: 末尾标点规则不许误伤的期名：括号结尾是真实存量期名，问句/感叹/省略号是正当标题。
OK_TRAILING_NAMES = [
    "2026-08-13-罪恶王冠-函数的终途（void）",
    "x「终」",
    "她为什么要逃？",
    "x！",
    "x?",
    "x…",
    "中间的。句号不算",
]


@pytest.mark.parametrize("name", BAD_NAMES)
def test_ty2_bad_names(tmp_path, monkeypatch, capsys, name):
    _scaffold(tmp_path, monkeypatch)
    ep_root = tmp_path / "data" / "episodes"
    before = _tree(tmp_path)
    assert create_new_episode(name) == 2, name
    assert "期名不合规" in capsys.readouterr().err
    assert _tree(tmp_path) == before
    assert not (tmp_path / "x").exists() and not (tmp_path / "escape").exists()
    assert list(ep_root.iterdir()) == []


def test_ty2_trailing_punct_reason(tmp_path, monkeypatch, capsys):
    _scaffold(tmp_path, monkeypatch)
    assert create_new_episode("2026-10-10-伪恋-橘万里花的进攻哲学。") == 2
    assert "不许以 '。' 结尾" in capsys.readouterr().err


@pytest.mark.parametrize("name", OK_TRAILING_NAMES)
def test_ty3_trailing_chars_not_overblocked(tmp_path, monkeypatch, name):
    _scaffold(tmp_path, monkeypatch)
    assert create_new_episode(name) == 0, name
    assert (tmp_path / "data" / "episodes" / name / "01-topic.md").is_file()


def test_ty2_absolute_path_rejected(tmp_path, monkeypatch, capsys):
    _scaffold(tmp_path, monkeypatch)
    target = tmp_path / "abs-escape"
    assert create_new_episode(str(target)) == 2
    assert "期名不合规" in capsys.readouterr().err
    assert not target.exists()


def test_ty3_valid_name_and_duplicate(tmp_path, monkeypatch, capsys):
    _scaffold(tmp_path, monkeypatch)
    name = "2026-09-26-罪恶王冠-测试"
    assert create_new_episode(name) == 0
    topic = tmp_path / "data" / "episodes" / name / "01-topic.md"
    assert topic.read_text(encoding="utf-8") == TOPIC_TEMPLATE.format(name=name)
    assert create_new_episode(name) == 1
    assert "拒绝覆盖" in capsys.readouterr().err


def test_ty4_main_requires_exactly_one_name(tmp_path, monkeypatch, capsys):
    _scaffold(tmp_path, monkeypatch)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
    assert main(["new"]) == 2
    assert main(["new", "a", "b"]) == 2
    err = capsys.readouterr().err
    assert err.count("用法: ava new") == 2
    assert list((tmp_path / "data" / "episodes").iterdir()) == []


# --- TY-5：api_key_env_name 只读配置 ---------------------------------------


class _BoomEnv(dict):
    """任何访问即抛：证明这条路径**不读** os.environ（MUT-21 的杀手）。"""

    def __getitem__(self, key):  # noqa: D105
        raise AssertionError(f"不该读环境变量：{key}")

    def get(self, key, default=None):
        raise AssertionError(f"不该读环境变量：{key}")

    def __contains__(self, key):  # noqa: D105
        raise AssertionError(f"不该读环境变量：{key}")


def _write_cfg(root: Path, name: str, payload: dict) -> None:
    cfg = root / "config"
    cfg.mkdir(parents=True, exist_ok=True)
    (cfg / name).write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def test_ty5_api_key_env_name_reads_config_only(tmp_path, monkeypatch):
    _write_cfg(tmp_path, "agent.json", {
        "base_url": "http://127.0.0.1:1/v1", "model": "m", "api_key_env": "CPA_API_KEY",
    })
    monkeypatch.setattr(os, "environ", _BoomEnv())
    assert api_key_env_name(tmp_path) == "CPA_API_KEY"


def test_ty5_local_wins_and_matches_load_llm_config(tmp_path, monkeypatch):
    _write_cfg(tmp_path, "agent.json", {
        "base_url": "http://a/v1", "model": "m", "api_key_env": "OPENAI_API_KEY",
    })
    _write_cfg(tmp_path, "agent.local.json", {
        "base_url": "http://b/v1", "model": "m2", "api_key_env": "CPA_API_KEY",
    })
    assert api_key_env_name(tmp_path) == "CPA_API_KEY"
    monkeypatch.setenv("CPA_API_KEY", "sk-test")
    cfg = load_llm_config(tmp_path)
    assert cfg is not None and cfg.base_url == "http://b/v1" and cfg.model == "m2"


def test_ty5_incomplete_or_broken_config(tmp_path):
    for payload in (
        {"model": "m", "api_key_env": "CPA_API_KEY"},
        {"base_url": "http://a/v1", "api_key_env": "CPA_API_KEY"},
        {"base_url": "http://a/v1", "model": "m"},
    ):
        cfg_dir = tmp_path / "config"
        for f in cfg_dir.glob("agent*.json"):
            f.unlink()
        _write_cfg(tmp_path, "agent.json", payload)
        assert api_key_env_name(tmp_path) is None

    (tmp_path / "config" / "agent.json").write_text("{ 坏", encoding="utf-8")
    assert api_key_env_name(tmp_path) is None
    # 没有配置文件
    (tmp_path / "config" / "agent.json").unlink()
    assert api_key_env_name(tmp_path) is None


def test_ty5_config_read_once(tmp_path, monkeypatch):
    """取名字与取密钥同读一次：两次读取之间配置被改会拿到互相矛盾的组合（验收 🟡-2）。"""
    _write_cfg(tmp_path, "agent.json", {
        "base_url": "http://a/v1", "model": "m", "api_key_env": "CPA_API_KEY",
    })
    from pipeline.agent import llm

    reads: list[str] = []
    orig = Path.read_text

    def counting(self: Path, *a, **k):
        if self.name.startswith("agent"):
            reads.append(str(self))
        return orig(self, *a, **k)

    monkeypatch.setattr(Path, "read_text", counting)
    monkeypatch.setenv("CPA_API_KEY", "sk-test")
    assert llm.load_llm_config(tmp_path) is not None
    assert [r for r in reads if r.endswith("agent.json")] == [str(tmp_path / "config" / "agent.json")]


def test_ty5_broken_local_shadows_good_agent(tmp_path):
    _write_cfg(tmp_path, "agent.json", {
        "base_url": "http://a/v1", "model": "m", "api_key_env": "CPA_API_KEY",
    })
    (tmp_path / "config" / "agent.local.json").write_text("{ 坏", encoding="utf-8")
    assert api_key_env_name(tmp_path) is None
    assert load_llm_config(tmp_path) is None


# --- TY-8：C10-R3 01 完成判定 ---------------------------------------------


def _topic_case(tmp_path: Path) -> Path:
    ep_dir = tmp_path / "data" / "episodes" / "01-case"
    ep_dir.mkdir(parents=True)
    return ep_dir


def test_ty8_new_template_is_01_incomplete(tmp_path, monkeypatch):
    from pipeline import paths, status

    _scaffold(tmp_path, monkeypatch)
    assert create_new_episode("2026-09-26-新期") == 0
    st = status.inspect_episode(tmp_path / "data" / "episodes" / "2026-09-26-新期")
    assert st.current_step == "01 选题"
    assert st.completed_steps == []
    assert st.is_blocked is False
    assert "类型" in st.next_action
    assert st.docs_ref == "docs/runbook/01-topic.md"
    assert paths.ROOT == tmp_path


def test_ty8_filled_type_is_02(tmp_path):
    from pipeline import status

    ep_dir = _topic_case(tmp_path)
    (ep_dir / "01-topic.md").write_text(
        "# 01-case 选题配置\n\n番：春物\n类型：人物志\n模式：\n张力：\n锚点：\n", encoding="utf-8"
    )
    st = status.inspect_episode(ep_dir)
    assert st.current_step == "02 脚本写作"
    assert st.completed_steps == ["01 选题"]


NON_STANDARD_TOPICS = [
    "# 01-case 选题配置\n\n番：春物\n锚点：X\n张力：Y\n",                     # 无类型行
    "# 01-case 选题配置\n\n番：伪恋\n类型：算法诗\n锚点：X\n",                  # 类型不在题材表
    "# 01-case 选题配置\n\n番：罪恶王冠\n类型：杂谈（乐评）\n模式：立论\n",       # 带括号备注
    "# 01-case 选题配置\n\n番：春物\n类型：娱乐向（肥宅快乐导向）\n",            # 同上
    "# 01-case 选题配置\n\n番：春物\n类型：杂谈\n模式：立论\n锚点：X\n",         # 杂谈缺张力
]


@pytest.mark.parametrize("content", NON_STANDARD_TOPICS)
def test_ty8_non_standard_shapes_unchanged(tmp_path, content):
    """E7 的 5 种非标准形状：最小规则不覆盖它们，status 与改动前一致。"""
    from pipeline import status

    ep_dir = _topic_case(tmp_path)
    (ep_dir / "01-topic.md").write_text(content, encoding="utf-8")
    st = status.inspect_episode(ep_dir)
    assert (st.current_step, st.completed_steps, st.is_blocked) == (
        "02 脚本写作", ["01 选题"], False,
    )


def test_ty8_any_non_empty_type_line_counts_as_filled(tmp_path):
    from pipeline import status

    ep_dir = _topic_case(tmp_path)
    (ep_dir / "01-topic.md").write_text(
        "类型：\n类型：杂谈\n", encoding="utf-8"
    )
    assert status.inspect_episode(ep_dir).current_step == "02 脚本写作"


def test_ty8_type_line_does_not_span_lines(tmp_path):
    from pipeline import status

    ep_dir = _topic_case(tmp_path)
    (ep_dir / "01-topic.md").write_text("类型：\n模式：立论\n", encoding="utf-8")
    st = status.inspect_episode(ep_dir)
    assert st.current_step == "01 选题" and st.completed_steps == []


def test_ty8_status_does_not_import_check_script():
    probe = (
        "import sys, pipeline.status; "
        "leaked = [m for m in ('pipeline.check_script', 'pipeline.bgm') if m in sys.modules]; "
        "assert not leaked, leaked"
    )
    r = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True, cwd=REPO_ROOT)
    assert r.returncode == 0, r.stderr


# --- TY-9：C10-R4 不跨行 ---------------------------------------------------


def test_ty9_topic_fields_do_not_span_lines(tmp_path):
    from pipeline.check_script import episode_genre

    (tmp_path / "01-topic.md").write_text(TOPIC_TEMPLATE.format(name="01-case"), encoding="utf-8")
    assert episode_genre(tmp_path / "02-script.md") == ("", "")
