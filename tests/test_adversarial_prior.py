"""D81 PR-3（C-3）：企划内的卷跑 02.8 时带上前卷定稿；`--out` 让全部写入落在指定目录。

临时仓库根（paths.ROOT 指过去），夹具企划与软链卷都在 tmp_path 下，不碰真实 data/。模型是替身（不出网）。
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from pipeline import adversarial as adv
from pipeline import paths
from pipeline.agent import llm as llm_mod

TABLE = (
    "企划: 测试编年史\n\n## 卷表\n\n| 卷序 | 目录 | 年份 | 时长预算 | 片尾 |\n|---|---|---|---|---|\n"
    "| 1 | 01-a | 2011 | 14-16分钟 | 余韵18秒 |\n| 2 | 02-b | 2012 | 14-16分钟 | 余韵18秒 |\n"
    "| 3 | 03-c | 2013 | 14-16分钟 | 余韵18秒 |\n| 4 | 04-d | 2014 | 14-16分钟 | 完整保留 |\n"
)


# 夹具卷的 01-topic.md 不写「番:」（本文件只测前卷），既有的这条缺口说明每个用例都会有
NO_ANIME = "01-topic.md 没写「番:」：没有笔记、字幕与在场索引可给审查者，只能审语言与衔接"


def script(*voices: str) -> str:
    return "".join(f"## 段落 {i}\n\n配音：{v}\n\n画面：\n  锚点: 无（氛围段）\n  查询: 示例\n\n" for i, v in enumerate(voices, 1))


def write_vol(proj: Path, name: str, final: str | None = None, draft: str | None = None) -> Path:
    d = proj / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "01-topic.md").write_text("# 选题\n类型：杂谈\n", encoding="utf-8")
    if final is not None:
        (d / "02-script.md").write_text(final, encoding="utf-8")
    if draft is not None:
        (d / "02-script.draft.md").write_text(draft, encoding="utf-8")
    return d


@pytest.fixture
def root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    r = tmp_path / "repo"
    (r / "data" / "episodes").mkdir(parents=True)
    monkeypatch.setattr(paths, "ROOT", r)
    monkeypatch.setattr(paths, "DATA", r / "data")
    return r


@pytest.fixture
def proj(root: Path) -> Path:
    p = root / "data" / "episodes" / "P"
    p.mkdir()
    (p / "00-project.md").write_text(TABLE, encoding="utf-8")
    write_vol(p, "01-a", final=script("第一卷甲句：二〇一一年三人组成乐队。", "第一卷乙句：首场演出在秋叶原。"),
              draft=script("第一卷草稿里的独特句子，不该给审查者。"))
    write_vol(p, "02-b", final=script("第二卷甲句：二〇一二年主唱换人。"))
    write_vol(p, "03-c", final=script("第三卷：二〇一三年乐队解散。"))
    write_vol(p, "04-d", final=script("第四卷的独特句子，在本卷之后。"))
    return p


def prompt_of(ep: Path) -> tuple[adv.Evidence, str]:
    ev = adv.gather_evidence(ep, "02-script.md")
    return ev, adv.build_user_prompt(ev)


def test_第3卷带第1和第2卷旁白_不带之后的卷与草稿(proj: Path) -> None:
    ev, user = prompt_of(proj / "03-c")
    assert [(p.seq, p.name) for p in ev.prior] == [(1, "01-a"), (2, "02-b")]
    assert "### 第 1 卷 · 01-a\n\n段 1：第一卷甲句：二〇一一年三人组成乐队。\n段 2：第一卷乙句：首场演出在秋叶原。" in user
    assert "### 第 2 卷 · 02-b\n\n段 1：第二卷甲句：二〇一二年主唱换人。" in user
    assert "第四卷的独特句子" not in user and "第一卷草稿里的独特句子" not in user
    assert "前卷冲突" in user and ev.gaps == [NO_ANIME]


def test_前卷缺定稿列进缺口_其余照带(proj: Path) -> None:
    (proj / "02-b" / "02-script.md").rename(proj / "02-b" / "02-script.draft.md")
    ev, user = prompt_of(proj / "03-c")
    assert [p.name for p in ev.prior] == ["01-a"]
    assert ev.gaps == [NO_ANIME, "前卷 第 2 卷 02-b 没有 02-script.md（草稿不算定稿）：这一卷没给审查者"]
    assert "第二卷甲句" not in user


def test_本卷不在卷表_不带前卷且说明原因(proj: Path) -> None:
    write_vol(proj, "05-x", final=script("卷表外的一卷。"))
    ev, user = prompt_of(proj / "05-x")
    assert ev.prior == [] and "前卷定稿" not in user
    assert ev.gaps == [NO_ANIME, "本卷 05-x 不在企划 P 的卷表里：不带前卷定稿"]


def test_企划文件不可解析_不带前卷且说明原因(proj: Path) -> None:
    (proj / "00-project.md").write_text("企划: P\n\n## 卷表\n\n| 卷序 | 目录 |\n", encoding="utf-8")
    ev, _user = prompt_of(proj / "03-c")
    assert ev.prior == [] and len(ev.gaps) == 2 and ev.gaps[1].startswith("企划 P 的 00-project.md 解析失败")


def test_第1卷没有前卷(proj: Path) -> None:
    ev, user = prompt_of(proj / "01-a")
    assert ev.prior == [] and ev.gaps == [NO_ANIME] and "前卷定稿" not in user


# ---- verify_item：前卷两类的机械核销 ----


def _prior_item(**over: str) -> dict:
    return {"段": "1", "类": "前卷冲突", "原文": "二〇一三年乐队解散", "问题": "第一卷说三人组成，未交代何时变动",
            "prior_volume": "01-a", "prior_segment": "1", "prior_quote": "三人组成乐队", **over}


@pytest.mark.parametrize("over,problem", [
    ({}, ""),
    ({"prior_volume": "04-d"}, "前卷「04-d」不在本次输入里"),
    ({"prior_segment": "9"}, "前卷 01-a 没有段 9"),
    ({"prior_quote": "五人组成乐队"}, "前卷引文不在 01-a 段 1 的旁白里"),
    ({"类": "前卷重复", "prior_quote": "首场演出在秋叶原", "prior_segment": "2"}, ""),
])
def test_前卷引用核对(proj: Path, over: dict, problem: str) -> None:
    ev, _ = prompt_of(proj / "03-c")
    out = adv.verify_item(_prior_item(**over), ev)
    assert out["核对"] == problem


def test_没有前卷输入时前卷类别不认(proj: Path) -> None:
    ev, _ = prompt_of(proj / "01-a")
    out = adv.verify_item(_prior_item(原文="二〇一一年三人组成乐队"), ev)
    assert out["核对"] == "类别「前卷冲突」不在 事实/衔接/语言 之内（本次没有前卷输入）"


# ---- 端到端（替身模型） ----


def _fake_llm(monkeypatch: pytest.MonkeyPatch, *contents: str) -> list[list[dict]]:
    sent: list[list[dict]] = []
    replies = list(contents)

    def fake_chat(messages, tools=None, **kwargs):
        sent.append(messages)
        return {"role": "assistant", "content": replies.pop(0) if len(replies) > 1 else replies[0]}

    monkeypatch.setattr(llm_mod, "chat_complete", fake_chat)
    monkeypatch.setattr(llm_mod, "load_llm_config",
                        lambda root=None: llm_mod.LLMConfig(base_url="http://x", model="m-test", api_key="k"))
    return sent


def test_报告有与前卷一节_终审表带编号(proj: Path, monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    _fake_llm(monkeypatch, json.dumps({"items": [_prior_item(), _prior_item(类="前卷重复", prior_quote="不存在的话")]}, ensure_ascii=False))
    assert adv.main([str(proj / "03-c"), "--script", "02-script.md"]) == 0
    report = (proj / "03-c" / adv.REPORT).read_text(encoding="utf-8")
    assert "、前卷定稿旁白（第 1 卷 01-a、第 2 卷 02-b）" in report and "与前卷 2 条" in report
    assert "## 与前卷（2 条）" in report
    assert "| V1 | 1 | 前卷冲突 | 二〇一三年乐队解散 | 01-a · 段 1 | 三人组成乐队 | 第一卷说三人组成，未交代何时变动 |" in report
    assert "⚠ 审查者引用不存在（前卷引文不在 01-a 段 1 的旁白里）" in report
    assert "| V1 |  |  |\n| V2 |  |  |\n" in report
    assert "与前卷 2 条" in capsys.readouterr().out


def test_前卷旁白超限报错_不调模型(proj: Path, monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    sent = _fake_llm(monkeypatch, '{"items": []}')
    monkeypatch.setattr(adv, "PRIOR_NARRATION_MAX_CHARS", 20)
    assert adv.main([str(proj / "03-c"), "--script", "02-script.md"]) == 1
    err = capsys.readouterr().err
    assert sent == [] and "前卷旁白合计" in err and "超过上限 20" in err
    with pytest.raises(adv.AdversarialError, match="不截断"):
        adv.gather_evidence(proj / "03-c", "02-script.md")


def _tree(d: Path) -> list[tuple[str, int, int]]:
    out = []
    for dirpath, dirnames, filenames in os.walk(d):
        for n in sorted(dirnames + filenames):
            st = os.lstat(os.path.join(dirpath, n))
            out.append((os.path.relpath(os.path.join(dirpath, n), d), st.st_mtime_ns, st.st_size))
    return sorted(out)


def test_软链卷与out_全部写入落在out_被指向的目录不变(root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    """镜像企划 M：第 2 卷是指向成品目录的软链（§4.9）。企划归属按字面父目录 → 认作 M 的第 2 卷、带 M 第 1 卷旁白；
    解析不了的回复、正常回复两次（第二次触发覆盖前留底）——原文留档、报告、留底全在 --out 下，成品目录一字节不动。"""
    m = root / "data" / "episodes" / "M"
    m.mkdir()
    (m / "00-project.md").write_text(TABLE.replace("| 3 | 03-c", "| 3 | 03-c").replace("测试编年史", "镜像"), encoding="utf-8")
    write_vol(m, "01-a", final=script("镜像第一卷的独特旁白。"))
    real = write_vol(tmp_path / "成品", "02-b", final=script("成品第二卷：二〇一二年主唱换人。"))
    (m / "02-b").symlink_to(real, target_is_directory=True)
    out = tmp_path / "out"
    out.mkdir()
    before = _tree(real)

    sent = _fake_llm(monkeypatch, "解析不了的回复")
    assert adv.main([str(m / "02-b"), "--script", "02-script.md", "--out", str(out)]) == 1
    assert (out / "_agent" / "adversarial-raw.txt").read_text(encoding="utf-8") == "解析不了的回复"
    assert "镜像第一卷的独特旁白" in sent[0][1]["content"]       # 认作 M 的第 2 卷
    assert _tree(real) == before

    _fake_llm(monkeypatch, '{"items": []}')
    assert adv.main([str(m / "02-b"), "--script", "02-script.md", "--out", str(out)]) == 0
    (out / adv.REPORT).write_text("旧报告，终审已填\n", encoding="utf-8")
    assert adv.main([str(m / "02-b"), "--script", "02-script.md", "--out", str(out)]) == 0
    assert [p.read_text(encoding="utf-8") for p in (out / "_agent" / "adversarial-history").glob("*.md")] == ["旧报告，终审已填\n"]
    assert (out / adv.REPORT).read_text(encoding="utf-8").startswith("# 02.8 零上下文对抗审查")
    assert _tree(real) == before


def test_out目录不存在_拒且不建(proj: Path, tmp_path: Path, capsys) -> None:
    assert adv.main([str(proj / "03-c"), "--out", str(tmp_path / "nope")]) == 1
    assert not (tmp_path / "nope").exists() and "--out 目录不存在" in capsys.readouterr().err
