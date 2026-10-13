"""看图（D90 / ADR-0031）：`view_image` 的读域闸、请求时附图投影、工具循环里的接线。

LLM 全用脚本替身，不打真网；图片在 tmp 里现画，不碰真 data/。
"""

from __future__ import annotations

import base64
import copy
import io
import json
from pathlib import Path

import pytest
from PIL import Image

from pipeline.agent import llm as llm_mod
from pipeline.agent import vision
from pipeline.agent.llm import _WRAPUP_KEY, STEER_KEY, run_tool_loop
from pipeline.agent.tools import ToolContext, execute_tool
from tests.test_agent_loop import CFG as LOOP_CFG
from tests.test_agent_loop import Script as LoopScript
from tests.test_agent_loop import build_control, make_agent_root, ROOT_URL


@pytest.fixture(autouse=True)
def _fresh_cache():
    vision._CACHE.clear()
    yield
    vision._CACHE.clear()


def _png(path: Path, size=(320, 180), color="#ff0000") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size, color).save(path, "PNG")
    return path


@pytest.fixture
def ep(tmp_path: Path) -> Path:
    d = tmp_path / "ep"
    _png(d / "07-cover" / "edit-1.png")
    return d


def _ctx(ep: Path | None, **kw) -> ToolContext:
    return ToolContext(scope="creative", episode_dir=ep, **kw)


def _view(ep: Path | None, path: str, **kw) -> dict:
    return execute_tool("view_image", {"path": path}, _ctx(ep, **kw))


# ---------------------------------------------------------------------------
# 工具本身
# ---------------------------------------------------------------------------


def test_view_image_returns_size_and_private_ref(ep: Path) -> None:
    out = _view(ep, "07-cover/edit-1.png")
    assert out["ok"], out
    r = out["result"]
    assert (r["width"], r["height"], r["shown"]) == (320, 180, "320×180")
    ref = r[vision.OUTCOME_IMAGE_KEY]
    assert Path(ref["abs_path"]) == (ep / "07-cover/edit-1.png").resolve()
    assert len(ref["sha256"]) == 64


def test_tool_message_moves_ref_off_the_wire_text(ep: Path) -> None:
    out = _view(ep, "07-cover/edit-1.png")
    msg = llm_mod._tool_message({"id": "c1"}, out)
    assert vision.OUTCOME_IMAGE_KEY not in msg["content"] and "abs_path" not in msg["content"]
    assert json.loads(msg["content"])["result"]["path"] == "07-cover/edit-1.png"
    assert msg[vision.IMAGE_KEY]["path"] == "07-cover/edit-1.png"
    assert vision.OUTCOME_IMAGE_KEY in out["result"], "不 mutate 入参"


def test_shown_size_caps_long_side(ep: Path) -> None:
    _png(ep / "07-cover/big.png", size=(3136, 1000))
    r = _view(ep, "07-cover/big.png")["result"]
    assert r["shown"] == "1568×500"


@pytest.mark.parametrize("path, needle", [
    ("../x.png", "不含 .."),
    ("/etc/x.png", "不含 .."),
    ("02-script.md", "只看图片"),
    ("04-patch/a.png", "04-patch"),
    ("03-AUDIO/a.png", "03-audio"),
    ("07-cover/nope.png", "不存在"),
    ("", "不能为空"),
])
def test_view_image_rejects(ep: Path, path: str, needle: str) -> None:
    for sub in ("04-patch", "03-AUDIO"):
        _png(ep / sub / "a.png")
    out = _view(ep, path)
    assert not out["ok"] and needle in out["error"], out


def test_symlink_escape_rejected(ep: Path, tmp_path: Path) -> None:
    outside = _png(tmp_path / "outside.png")
    (ep / "07-cover" / "link.png").symlink_to(outside)
    out = _view(ep, "07-cover/link.png")
    assert not out["ok"] and "越界" in out["error"], out


def test_symlink_to_non_image_rejected(ep: Path) -> None:
    (ep / "02-script.md").write_text("稿", encoding="utf-8")
    (ep / "07-cover" / "fake.png").symlink_to(ep / "02-script.md")
    out = _view(ep, "07-cover/fake.png")
    assert not out["ok"] and "只看图片" in out["error"], out


def test_no_episode_and_project_session_rejected(ep: Path) -> None:
    out = _view(None, "07-cover/edit-1.png")
    assert not out["ok"] and "没有期目录" in out["error"]
    out = _view(ep, "07-cover/edit-1.png", session_kind="project")
    assert not out["ok"] and "企划会话里不做" in out["error"]


def test_size_limits(ep: Path, monkeypatch) -> None:
    monkeypatch.setattr(vision, "MAX_PIXELS", 1000)
    out = _view(ep, "07-cover/edit-1.png")
    assert not out["ok"] and "像素过大" in out["error"]
    monkeypatch.setattr(vision, "MAX_FILE_BYTES", 10)
    out = _view(ep, "07-cover/edit-1.png")
    assert not out["ok"] and "超过上限" in out["error"]


def test_undecodable_image_rejected(ep: Path) -> None:
    (ep / "07-cover" / "bad.png").write_bytes(b"not a png")
    out = _view(ep, "07-cover/bad.png")
    assert not out["ok"] and "打不开" in out["error"]


# ---------------------------------------------------------------------------
# 附图投影
# ---------------------------------------------------------------------------


def _call(*ids: str) -> dict:
    return {
        "role": "assistant", "content": None,
        "tool_calls": [
            {"id": i, "type": "function", "function": {"name": "view_image", "arguments": "{}"}}
            for i in ids
        ],
    }


def _seen(ep: Path, call_id: str, rel: str) -> dict:
    return llm_mod._tool_message({"id": call_id}, _view(ep, rel))


def _images(message: dict) -> list[str]:
    return [p["image_url"]["url"] for p in message["content"] if p.get("type") == "image_url"]


def _texts(message: dict) -> str:
    return "".join(p.get("text", "") for p in message["content"] if p.get("type") == "text")


def test_no_images_returns_same_object() -> None:
    msgs = [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "yo"}]
    assert vision.attach_images(msgs) is msgs


def test_attached_after_whole_batch_not_between(ep: Path) -> None:
    _png(ep / "07-cover/edit-2.png", color="#00ff00")
    msgs = [
        {"role": "system", "content": "s"}, {"role": "user", "content": "看两张"},
        _call("a", "b"), _seen(ep, "a", "07-cover/edit-1.png"), _seen(ep, "b", "07-cover/edit-2.png"),
    ]
    before = copy.deepcopy(msgs)
    out = vision.attach_images(msgs)
    assert msgs == before, "不 mutate 入参"
    assert [m["role"] for m in out] == ["system", "user", "assistant", "tool", "tool", "user"]
    extra = out[-1]
    assert len(_images(extra)) == 2
    assert "宿主附图，不是人发的消息" in _texts(extra)
    assert "07-cover/edit-1.png" in _texts(extra) and "07-cover/edit-2.png" in _texts(extra)


def test_each_batch_gets_its_own_attachment(ep: Path) -> None:
    msgs = [
        {"role": "user", "content": "看"},
        _call("a"), _seen(ep, "a", "07-cover/edit-1.png"),
        {"role": "assistant", "content": None, "tool_calls": [
            {"id": "x", "type": "function", "function": {"name": "read_status", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "x", "content": "{}"},
    ]
    out = vision.attach_images(msgs)
    roles = [m["role"] for m in out]
    assert roles == ["user", "assistant", "tool", "user", "assistant", "tool"]


def test_previous_turn_images_not_attached(ep: Path) -> None:
    msgs = [
        {"role": "user", "content": "回合一"},
        _call("a"), _seen(ep, "a", "07-cover/edit-1.png"),
        {"role": "assistant", "content": "看完了"},
        {"role": "user", "content": "回合二"},
    ]
    assert vision.attach_images(msgs) is msgs


def test_wrapup_and_steer_do_not_end_the_turn(ep: Path) -> None:
    msgs = [
        {"role": "user", "content": "看"},
        _call("a"), _seen(ep, "a", "07-cover/edit-1.png"),
        {"role": "user", "content": "排队消息", STEER_KEY: True},
        {"role": "user", "content": "收尾", _WRAPUP_KEY: True},
    ]
    out = vision.attach_images(msgs)
    assert [m["role"] for m in out] == ["user", "assistant", "tool", "user", "user", "user"]
    assert len(_images(out[3])) == 1


def test_only_last_four_images_attached(ep: Path) -> None:
    msgs: list[dict] = [{"role": "user", "content": "看六张"}]
    for i in range(6):
        _png(ep / f"07-cover/e{i}.png", color=(i * 40, 0, 0))
        msgs += [_call(f"c{i}"), _seen(ep, f"c{i}", f"07-cover/e{i}.png")]
    out = vision.attach_images(msgs)
    extras = [m for m in out if m["role"] == "user" and isinstance(m["content"], list)]
    assert sum(len(_images(m)) for m in extras) == vision.MAX_IMAGES_PER_TURN
    assert "已省略" in _texts(extras[0]) and "已省略" in _texts(extras[1])
    assert not _images(extras[0]) and _images(extras[-1])


def test_changed_or_missing_file_not_sent(ep: Path) -> None:
    msgs = [{"role": "user", "content": "看"}, _call("a"), _seen(ep, "a", "07-cover/edit-1.png")]
    _png(ep / "07-cover/edit-1.png", color="#0000ff")       # 看过之后被改了
    out = vision.attach_images(msgs)
    assert not _images(out[-1]) and "文件变了或不在了" in _texts(out[-1])
    (ep / "07-cover/edit-1.png").unlink()
    vision._CACHE.clear()
    out = vision.attach_images(msgs)
    assert not _images(out[-1]) and "文件变了或不在了" in _texts(out[-1])


def test_encoded_image_is_downscaled_jpeg(ep: Path) -> None:
    _png(ep / "07-cover/big.png", size=(3136, 1764))
    msgs = [{"role": "user", "content": "看"}, _call("a"), _seen(ep, "a", "07-cover/big.png")]
    url = _images(vision.attach_images(msgs)[-1])[0]
    assert url.startswith("data:image/jpeg;base64,")
    with Image.open(io.BytesIO(base64.b64decode(url.split(",", 1)[1]))) as img:
        assert img.size == (1568, 882)


def test_wire_strips_image_ref(ep: Path) -> None:
    msgs = [{"role": "user", "content": "看"}, _call("a"), _seen(ep, "a", "07-cover/edit-1.png")]
    wired = llm_mod._wire_messages(vision.attach_images(msgs), "reasoning")
    assert all(vision.IMAGE_KEY not in m for m in wired)


# ---------------------------------------------------------------------------
# 工具循环接线（真 execute_tool + 脚本化 chat_complete）
# ---------------------------------------------------------------------------


def test_run_tool_loop_sends_image_but_never_stores_it(ep: Path, tmp_path: Path, monkeypatch) -> None:
    root = make_agent_root(tmp_path / "repo", ROOT_URL)
    view_call = {
        "role": "assistant", "content": None,
        "tool_calls": [{"id": "v1", "type": "function", "function": {
            "name": "view_image", "arguments": json.dumps({"path": "07-cover/edit-1.png"})}}],
    }
    script = LoopScript([view_call, {"role": "assistant", "content": "红色的"}])
    monkeypatch.setattr(llm_mod, "chat_complete", script)
    convo: list[dict] = [{"role": "system", "content": "s"}, {"role": "user", "content": "什么颜色"}]
    frames: list[dict] = []
    control = build_control(convo, on_trace=frames.append)
    out = run_tool_loop(convo, ctx=ToolContext(scope="creative", root=root, episode_dir=ep),
                        config=LOOP_CFG, control=control)
    assert out["stopped"] == "done", out.get("error")

    second = script.requests[1]["messages"]
    assert second[-1]["role"] == "user" and len(_images(second[-1])) == 1, "第二次请求带图"
    assert second[-2]["role"] == "tool"
    assert "base64" not in json.dumps(convo, ensure_ascii=False), "会话史（即落盘内容）不含图"
    stored = next(m for m in convo if m.get("role") == "tool")
    assert stored[vision.IMAGE_KEY]["path"] == "07-cover/edit-1.png"
    end = next(f for f in frames if f.get("phase") == "end")
    assert vision.OUTCOME_IMAGE_KEY not in end["content"]["result"], "人看的工具帧不带私有键"
