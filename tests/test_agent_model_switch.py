"""会话内切换模型与推理强度（D91）：纯函数、按 sid 持久化、会话接线、协议命令。

LLM 全用替身 / 假端点，不打真网；期目录在 tmp 里。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from pipeline.agent import llm as llm_mod
from pipeline.agent import model_switch as ms
from pipeline.agent.llm import LLMConfig
from pipeline.agent.session import SessionHost
from tests.test_agent_compact_session import Script, _cfg, _main, _say, _turns
from tests.test_agent_protocol import FakeEndpoint, Protocol  # noqa: F401 - endpoint 夹具按名注入
from tests.test_agent_protocol import endpoint, world  # noqa: F401
from tests.test_agent_session import FakeChannel, _clean_leases, episode, root  # noqa: F401 - 不引 _no_real_popen：协议用例要起真子进程

BASE = LLMConfig(base_url="http://x/v1", model="flash", api_key="k", models={"reasoning": "flash", "light": "lite"})
MODELS = ["flash", "kimi-k3", "kimi-k2.8", "lite"]


def _fetch(cfg):
    return list(MODELS)


# ---------------------------------------------------------------------------
# 纯函数
# ---------------------------------------------------------------------------


def test_effective_config_combinations() -> None:
    assert ms.effective_config(BASE, ms.Override()) is BASE
    assert ms.effective_config(None, ms.Override(model="kimi-k3")) is None
    only_model = ms.effective_config(BASE, ms.Override(model="kimi-k3"))
    assert only_model.model_for("reasoning") == only_model.model_for("light") == "kimi-k3", "两档都换"
    assert only_model.reasoning_effort is None
    only_effort = ms.effective_config(BASE, ms.Override(reasoning_effort="low"))
    assert only_effort.model_for("light") == "lite" and only_effort.reasoning_effort == "low", "只换档不动分档"
    both = ms.effective_config(BASE, ms.Override(model="kimi-k3", reasoning_effort="high"))
    assert (both.model, both.reasoning_effort) == ("kimi-k3", "high")
    assert BASE.model == "flash" and BASE.reasoning_effort is None, "不 mutate 默认配置"


def test_run_command_model() -> None:
    o = ms.Override()
    same, text = ms.run_command("/model", BASE, o, "reasoning", models_fetcher=_fetch)
    assert same is o and "● flash" in text and "○ kimi-k3" in text and "默认配置" in text
    new, text = ms.run_command("/model kimi-k3", BASE, o, "reasoning", models_fetcher=_fetch)
    assert new.model == "kimi-k3" and "本会话" in text
    _, text = ms.run_command("/model", BASE, new, "reasoning", models_fetcher=_fetch)
    assert "● kimi-k3" in text and "本会话设置" in text
    back, text = ms.run_command("/model default", BASE, new, "reasoning", models_fetcher=_fetch)
    assert back.model is None and "flash" in text
    # D148：不在代理列表 → 不拒，照常切、回执与 last_warn 带提醒
    new2, text = ms.run_command("/model kimi-k33", BASE, o, "reasoning", models_fetcher=_fetch)
    assert new2.model == "kimi-k33", "未知模型也切换"
    assert ms.last_warn is not None and "kimi-k33" in ms.last_warn and "kimi-k3" in ms.last_warn, "warn 含名字与相近项"
    assert "不在代理列表" in text
    # 名字在列表里 → 无 warn
    ms.run_command("/model kimi-k3", BASE, o, "reasoning", models_fetcher=_fetch)
    assert ms.last_warn is None
    with pytest.raises(ms.ModelSwitchError, match="LLM 不可用"):
        ms.run_command("/model kimi-k3", None, o, "reasoning", models_fetcher=_fetch)


def test_check_model_rejects_malformed() -> None:
    """D148：空 / 空白 / 控制字符照旧拒；其余不在列表也放行。"""
    for bad in ("", " ", "a b", "x\ty", "x\x01y"):
        with pytest.raises(ms.ModelSwitchError):
            ms.check_model(bad, MODELS)
        assert ms.last_warn is None
    assert ms.check_model("gpt-6", MODELS) == "gpt-6" and "gpt-6" in (ms.last_warn or "")


def test_run_command_thinking() -> None:
    o = ms.Override(model="kimi-k3")
    _, text = ms.run_command("/thinking", BASE, o, "reasoning")
    assert "模型默认" in text and "xhigh" in text
    new, text = ms.run_command("/thinking LOW", BASE, o, "reasoning")
    assert new == ms.Override(model="kimi-k3", reasoning_effort="low") and "低" in text
    back, _ = ms.run_command("/thinking default", BASE, new, "reasoning")
    assert back == ms.Override(model="kimi-k3"), "清档位不动模型"
    with pytest.raises(ms.ModelSwitchError, match="推理强度只能是"):
        ms.run_command("/thinking turbo", BASE, o, "reasoning")


def test_is_command() -> None:
    assert ms.is_command("/model") and ms.is_command("  /thinking high ")
    assert not ms.is_command("/models") and not ms.is_command("用 /model 切换") and not ms.is_command("")


def test_list_models_reads_proxy(endpoint) -> None:  # noqa: F811
    endpoint.models = ["b", "a", "a"]
    cfg = LLMConfig(base_url=endpoint.url, model="mock", api_key="k")
    assert ms.list_models(cfg) == ["a", "b"]
    with pytest.raises(ms.ModelSwitchError, match="取模型列表失败"):
        ms.list_models(LLMConfig(base_url="http://127.0.0.1:9/v1", model="m", api_key="k"))


def _write_agent_cfg(root: Path, name: str, payload: dict) -> None:
    d = root / "config"
    d.mkdir(parents=True, exist_ok=True)
    (d / name).write_text(json.dumps(payload), encoding="utf-8")


def test_list_models_unauthenticated_reads_configured_base_url(tmp_path: Path, endpoint, monkeypatch) -> None:  # noqa: F811
    # D153：无会话进程时走这条路——不带 auth 也能拿代理列表（D150 实测）。base_url 只从配置读（local 优先）。
    _write_agent_cfg(tmp_path, "agent.json", {"base_url": "http://unused/v1", "model": "m", "api_key_env": "X"})
    _write_agent_cfg(tmp_path, "agent.local.json", {"base_url": endpoint.url, "model": "m", "api_key_env": "X"})
    endpoint.models = ["devin/swe-2", "claude-sonnet-4-6", "devin/swe-2"]
    monkeypatch.delenv("X", raising=False)  # 不读密钥也能取列表
    assert ms.list_models_unauthenticated(tmp_path) == ["claude-sonnet-4-6", "devin/swe-2"]


def test_list_models_unauthenticated_failures(tmp_path: Path, endpoint) -> None:  # noqa: F811
    # 无配置 → 不猜一份列表
    with pytest.raises(ms.ModelSwitchError, match="base_url"):
        ms.list_models_unauthenticated(tmp_path)
    # 代理拒连 → 如实失败
    _write_agent_cfg(tmp_path, "agent.local.json", {"base_url": "http://127.0.0.1:9/v1", "model": "m", "api_key_env": "X"})
    with pytest.raises(ms.ModelSwitchError, match="取模型列表失败"):
        ms.list_models_unauthenticated(tmp_path)


# ---------------------------------------------------------------------------
# 请求体与默认配置
# ---------------------------------------------------------------------------


def _capture_payload(monkeypatch) -> list[dict]:
    sent: list[dict] = []

    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return json.dumps({"choices": [{"message": {"role": "assistant", "content": "好"}}]}).encode()

    def fake_urlopen(request, timeout=None):
        sent.append(json.loads(request.data.decode("utf-8")))
        return _Resp()

    monkeypatch.setattr(llm_mod.urllib.request, "urlopen", fake_urlopen)
    return sent


def test_payload_has_effort_only_when_set(monkeypatch) -> None:
    sent = _capture_payload(monkeypatch)
    llm_mod.chat_complete([{"role": "user", "content": "hi"}], config=BASE)
    llm_mod.chat_complete([{"role": "user", "content": "hi"}],
                          config=ms.effective_config(BASE, ms.Override(reasoning_effort="none")))
    assert "reasoning_effort" not in sent[0], "不设就不带这个键（请求体与改动前一致）"
    assert sent[1]["reasoning_effort"] == "none"


def test_default_effort_from_config(tmp_path: Path, monkeypatch) -> None:
    llm_mod._reset_warn_latch_for_testing()
    (tmp_path / "config").mkdir()
    monkeypatch.setenv("AVA_TEST_KEY", "k")
    path = tmp_path / "config" / "agent.json"
    path.write_text(json.dumps({"base_url": "http://x/v1", "model": "m", "api_key_env": "AVA_TEST_KEY",
                                "reasoning_effort": "High"}), encoding="utf-8")
    assert llm_mod.load_llm_config(tmp_path).reasoning_effort == "high"
    path.write_text(json.dumps({"base_url": "http://x/v1", "model": "m", "api_key_env": "AVA_TEST_KEY",
                                "reasoning_effort": "turbo"}), encoding="utf-8")
    assert llm_mod.load_llm_config(tmp_path).reasoning_effort is None, "不合法 → 当没配"


# ---------------------------------------------------------------------------
# 按 sid 持久化
# ---------------------------------------------------------------------------


def test_store_per_sid(tmp_path: Path) -> None:
    ms.save(tmp_path, "a", ms.Override(model="kimi-k3"))
    ms.save(tmp_path, "b", ms.Override(reasoning_effort="low"))
    assert ms.load(tmp_path, "a") == (ms.Override(model="kimi-k3"), None)
    assert ms.load(tmp_path, "b") == (ms.Override(reasoning_effort="low"), None)
    assert ms.load(tmp_path, "c") == (ms.Override(), None)
    ms.save(tmp_path, "a", ms.Override())
    assert "a" not in json.loads(ms.store_path(tmp_path).read_text(encoding="utf-8")), "清空即删条目"
    assert ms.load(tmp_path, "b")[0].reasoning_effort == "low", "不碰别的会话"


def test_store_corrupt_file(tmp_path: Path) -> None:
    ms.store_path(tmp_path).parent.mkdir(parents=True)
    ms.store_path(tmp_path).write_text("{坏", encoding="utf-8")
    override, warning = ms.load(tmp_path, "a")
    assert override.empty and "无法解析" in warning
    with pytest.raises(ms.ModelSwitchError, match="拒绝覆盖"):
        ms.save(tmp_path, "a", ms.Override(model="x"))
    assert ms.store_path(tmp_path).read_text(encoding="utf-8") == "{坏"


def test_host_sid_assignment_aligns_override(tmp_path: Path) -> None:
    ep = tmp_path / "ep"
    ep.mkdir()
    # 新会话：第一条消息之前就切过 → sid 确定时写盘
    host = SessionHost(ep, channel=FakeChannel())
    host.set_model_override(ms.Override(model="kimi-k3"))
    assert not ms.store_path(ep).exists(), "还没有 sid，不落盘"
    host.sid = "s1"
    assert ms.load(ep, "s1")[0].model == "kimi-k3"
    # 重开旧会话：sid 一确定就读回
    again = SessionHost(ep, channel=FakeChannel())
    again.sid = "s1"
    assert again.model_override.model == "kimi-k3"
    # 别的会话不受影响
    other = SessionHost(ep, channel=FakeChannel())
    other.sid = "s2"
    assert other.model_override.empty
    # 一次性宿主不落盘
    eph = SessionHost(ep, channel=FakeChannel(), ephemeral=True)
    eph.set_model_override(ms.Override(model="x"))
    eph.sid = "s3"
    assert "s3" not in json.loads(ms.store_path(ep).read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# 会话接线：回合与压缩都用本会话设置
# ---------------------------------------------------------------------------


def test_turn_and_compact_use_session_override(root, episode, monkeypatch) -> None:  # noqa: F811
    _cfg(root)
    script = Script(replies=[_say("答" * 100) for _ in range(5)], summaries=[_say("摘要")])
    host, session, messages, tracker = _main(root, episode, monkeypatch, script)
    _turns(session, messages, tracker, root, 1)
    assert script.main[0]["config"].model == "mock-model" and script.main[0]["config"].reasoning_effort is None
    host.set_model_override(ms.Override(model="kimi-k3", reasoning_effort="low"))
    _turns(session, messages, tracker, root, 4)
    cfg = script.main[-1]["config"]
    assert (cfg.model, cfg.reasoning_effort) == ("kimi-k3", "low")
    # 压缩：窗口表按本会话模型查（kimi-k3 不在表里 → 拒，不拿默认模型的窗口冒充）
    result = session.compact(messages, tracker=tracker, root=root, scope="creative")
    assert not result["ok"] and "kimi-k3" in result["text"]
    host.set_model_override(ms.Override(reasoning_effort="low"))
    result = session.compact(messages, tracker=tracker, root=root, scope="creative")
    assert result["ok"], result
    summ = script.summarizer[-1]["config"]
    assert (summ.model, summ.reasoning_effort) == ("mock-model", "low")
    agent_json = json.loads((root / "config" / "agent.json").read_text(encoding="utf-8"))
    assert "reasoning_effort" not in agent_json and agent_json["model"] == "mock-model", "默认配置一字不动"


# ---------------------------------------------------------------------------
# 协议：对话框命令、按钮命令、帧
# ---------------------------------------------------------------------------


def _turn(proc, text: str) -> dict:
    """发一条消息并等这一回合完全收尾（turn_finished 之后还有 stop_points；之前发 command 会 E_BUSY）。"""
    proc.send({"t": "user_message", "text": text})
    done = proc.wait_for("turn_finished")
    proc.wait_for("stop_points")
    return done


def _ready(proc) -> dict:
    return proc.wait_for_ready()   # wait_for_ready 已顺带吃掉 ready 后那帧初始停机点


def _notices(proc, code: str) -> list[dict]:
    return [f for f in proc.frames if f.get("t") == "notice" and f.get("code") == code]


def test_protocol_model_and_thinking_commands(world, endpoint, tmp_path) -> None:  # noqa: F811
    root, episode = world
    (root / "config" / "agent" / "compact.json").write_text(json.dumps({
        "models": {"mock": {"window": 1000, "reliable_ratio": 1.0}, "kimi-k3": {"window": 2000, "reliable_ratio": 1.0}},
    }), encoding="utf-8")
    endpoint.replies = [{"role": "assistant", "content": "好"}]
    proc = Protocol(root, endpoint, episode=str(episode), tmp=tmp_path)
    try:
        ready = _ready(proc)
        assert (ready["model"], ready["reasoning_effort"], ready["model_source"]) == ("mock", None, "default")
        assert ready["context_window"] == 1000
        first_cfg = proc.wait_for("model_config")
        assert first_cfg["model"] == "mock"

        done = _turn(proc, "/model kimi-k3")
        assert done["stopped"] == "command" and done["llm_calls"] == 0
        assert done["context_window"] == 2000, "读数分母按本会话模型查"
        assert _notices(proc, "model")
        cfgs = [f for f in proc.frames if f.get("t") == "model_config"]
        assert cfgs[-1]["model"] == "kimi-k3" and cfgs[-1]["model_source"] == "session"

        _turn(proc, "/thinking low")
        _turn(proc, "/model 带 空格")   # D148：不在列表不报错（warn 放行）；空白这类非法名才报
        assert _notices(proc, "model_err")

        # D148：不在代理列表 → 切换照成、command_result 带 warn 透传
        proc.send({"t": "command", "name": "model", "arg": "brand-new-7", "rid": "rw"})
        res = proc.wait_for("command_result")
        assert res["ok"] and "brand-new-7" in res.get("warn", ""), "warn 字段透传到回执"
        proc.send({"t": "command", "name": "model", "arg": "kimi-k3", "rid": "rd"})
        res = proc.wait_for("command_result")
        assert res["ok"] and "warn" not in res, "在列表里的模型不带 warn"

        _turn(proc, "你好")
        proc.wait_for("session_title", timeout=20)

        proc.send({"t": "command", "name": "models", "arg": None, "rid": "r1"})
        res = proc.wait_for("command_result")
        assert res["ok"] and res["items"] == ["gemini-x", "kimi-k3", "mock"] and res["model"] == "kimi-k3"
        assert "xhigh" in res["efforts"]
        proc.send({"t": "command", "name": "thinking", "arg": "default", "rid": "r2"})
        res = proc.wait_for("command_result")
        assert res["ok"] and res["name"] == "thinking"
        proc.shutdown()
    finally:
        proc.proc.kill()

    assert len(endpoint.requests) == 1, "命令不进模型"
    assert [r["model"] for r in endpoint.title_requests] == ["kimi-k3"], "起标题也用本会话模型"
    req = endpoint.requests[0]
    assert req["model"] == "kimi-k3" and req["reasoning_effort"] == "low"
    sent_users = [m["content"] for m in req["messages"] if m.get("role") == "user"]
    assert not any(str(c).startswith("/") for c in sent_users), "命令不落成 user 消息"
    # 本会话设置按 sid 落盘；默认配置不动
    stored = json.loads(ms.store_path(episode).read_text(encoding="utf-8"))
    assert list(stored.values()) == [{"model": "kimi-k3", "reasoning_effort": None, "at": stored[next(iter(stored))]["at"]}]
    assert json.loads((root / "config" / "agent.json").read_text(encoding="utf-8"))["model"] == "mock"


def test_protocol_new_session_uses_default_and_continue_restores(world, endpoint, tmp_path) -> None:  # noqa: F811
    root, episode = world
    endpoint.replies = [{"role": "assistant", "content": "好"}]
    first = Protocol(root, endpoint, episode=str(episode), tmp=tmp_path / "a")
    try:
        first.wait_for_ready()
        first.send({"t": "user_message", "text": "/model kimi-k3"})
        first.wait_for("turn_finished")
        first.send({"t": "user_message", "text": "你好"})
        first.wait_for("turn_finished")
        first.shutdown()
    finally:
        first.proc.kill()
    resumed = Protocol(root, endpoint, episode=str(episode), extra=["--continue"], tmp=tmp_path / "b")
    try:
        ready = resumed.wait_for_ready()
        assert ready["continue_status"] == "resumed"
        assert ready["model"] == "kimi-k3" and ready["model_source"] == "session", "重开同一个会话：设置还在"
        resumed.shutdown()
    finally:
        resumed.proc.kill()
    fresh = Protocol(root, endpoint, episode=str(episode), tmp=tmp_path / "c")
    try:
        ready = fresh.wait_for_ready()
        assert ready["model"] == "mock" and ready["model_source"] == "default", "新会话用默认"
        fresh.shutdown()
    finally:
        fresh.proc.kill()


def test_protocol_command_frame_validation(world, endpoint, tmp_path) -> None:  # noqa: F811
    root, episode = world
    proc = Protocol(root, endpoint, episode=str(episode), tmp=tmp_path)
    try:
        proc.wait_for_ready()
        proc.send({"t": "command", "name": "model", "arg": None, "rid": "r1"})
        err = proc.wait_for("error")
        assert err["code"] == "E_BAD_REQUEST"
        proc.shutdown()
    finally:
        proc.proc.kill()


def test_protocol_model_commands_busy_mid_turn(world, endpoint, tmp_path) -> None:  # noqa: F811
    """D149：回合在途时按钮 command{model|thinking|models} 不再 E_BUSY——排队到回合收尾执行，
    下个回合生效（回合内 cfg 已绑定，本回合仍用原模型）。文字 /model 照旧 E_BUSY（不走队列，
    与 /compact 同纪律：命令不当 steer 排队消息）。"""
    from tests.test_agent_protocol import tool_call

    root, episode = world
    endpoint.replies = [tool_call("test_slow", {"seconds": 0}), {"role": "assistant", "content": "好"}]
    proc = Protocol(root, endpoint, episode=str(episode), tmp=tmp_path)
    try:
        proc.wait_for_ready()
        proc.send({"t": "user_message", "text": "甲"})
        request = proc.wait_for("request")   # 停在卡上：回合必在进行中
        proc.send({"t": "user_message", "text": "/model kimi-k3"})
        assert proc.wait_for("error")["code"] == "E_BUSY"
        proc.send({"t": "command", "name": "thinking", "arg": "low", "rid": "rc1"})
        proc.send({"t": "command", "name": "model", "arg": "kimi-k3", "rid": "rc2"})
        # 回合在途时不回执、不 E_BUSY；答卡放行回合，回合收尾后主循环执行排队的命令
        proc.send({"t": "answer", "request_id": request["request_id"], "decision": "approve", "feedback": None})
        proc.wait_for("turn_finished")
        r1 = proc.wait_for("command_result")
        r2 = proc.wait_for("command_result")
        assert {r1["rid"], r2["rid"]} == {"rc1", "rc2"} and r1["ok"] and r2["ok"]
        # 排队消息（steer）不吃命令：command 走的是 out 队列不是 steer
        proc.send({"t": "user_message", "text": "乙"})
        proc.wait_for("turn_finished")
        proc.shutdown()
    finally:
        proc.proc.kill()
    assert endpoint.requests[0]["model"] == "mock", "在途回合仍用原模型（cfg 回合起点绑定）"
    assert endpoint.requests[-1]["model"] == "kimi-k3", "排队切换在下个回合生效"
    assert endpoint.requests[-1].get("reasoning_effort") == "low"
    assert not any(f.get("t") == "queued" and "model" in str(f.get("text", "")) for f in proc.frames), "命令不当排队消息"


def test_repl_model_command(tmp_path: Path, monkeypatch, capsys) -> None:
    from pipeline.agent import cli

    ep = tmp_path / "ep"
    ep.mkdir()
    host = SessionHost(ep, channel=FakeChannel())
    monkeypatch.setattr(cli, "_SESSION_HOST", host)
    monkeypatch.setattr(llm_mod, "load_llm_config", lambda root=None: BASE)
    monkeypatch.setattr(ms, "list_models", _fetch)
    cli.run_model_command("/model kimi-k3", "creative")
    assert host.model_override.model == "kimi-k3" and "kimi-k3" in capsys.readouterr().out
    cli.run_model_command("/thinking turbo", "creative")
    assert "[ERROR]" in capsys.readouterr().out and host.model_override.reasoning_effort is None
