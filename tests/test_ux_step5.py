"""Step 5 UX 升级回归测试：HTML 审片页（A1）、读音纠错裸换种子与残留锁、status 可复制命令、cloud 单位/ANSI/活跃任务保护、model_switch 过滤与上调文案。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pytest

from pipeline import cloud, corrections, review, shots, status
from pipeline.agent import auth, cli, model_switch, status_card


def test_review_html_a1_zero_js_filter_audio_and_light_contrast(tmp_path: Path) -> None:
    ep = tmp_path / "01-demo"
    (ep / "03-audio").mkdir(parents=True)
    (ep / "03-audio" / "seg-01.wav").write_bytes(b"RIFF")
    (ep / "03-audio" / "manifest.json").write_text(
        json.dumps({
            "segments": [
                {"index": 1, "label": "1", "file": "seg-01.wav", "duration": 4.0},
                {"index": 2, "label": "2", "file": "seg-02.wav", "duration": 3.0},
            ]
        }),
        encoding="utf-8",
    )
    (ep / "04-clips.json").write_text(
        json.dumps({
            "total_duration": 7.0,
            "segments": [
                {"index": 1, "text": "正常段落", "duration": 4.0, "status": "ok", "used_query": "台词一", "clips": []},
                {"index": 2, "text": "告警段落", "duration": 3.0, "status": "no_match", "used_query": "台词二", "clips": []},
            ],
        }),
        encoding="utf-8",
    )
    dest = review.build(ep)
    html_text = dest.read_text(encoding="utf-8")
    assert "<script" not in html_text.lower()
    assert 'id="flt-all"' in html_text and 'id="flt-attn"' in html_text
    assert 'href="#seg-2"' in html_text
    assert '<audio class="aud" controls preload="none" src="03-audio/seg-01.wav">' in html_text
    assert "上方这句口播" in html_text and "左边这句口播" not in html_text
    assert "prefers-reduced-motion" in html_text


def test_corrections_bare_reseed_and_dead_pid_lock_hint(tmp_path: Path) -> None:
    segs = {"1": "第一段文本", "2": "第二段文本", "10": "第十段文本"}
    patch = corrections.parse_correction("2段 换种子", segs)
    assert patch.kind == "timbre" and patch.action == "pin_seed" and patch.issue == "换种子"

    # 表外听感词仍须拒绝（test_corrections.py:241 契约）
    with pytest.raises(corrections.PatchError):
        corrections.parse_correction("2段 音色变了 换种子", segs)

    # 自然排序：'2' 排在 '10' 之前
    with pytest.raises(corrections.PatchError) as exc:
        corrections.parse_correction("99段 换种子", segs)
    msg = str(exc.value)
    assert msg.index("'2'") < msg.index("'10'")

    # 残留死 PID 锁给出清理命令提示，且保留原有子串
    (tmp_path / "03-audio").mkdir(parents=True)
    (tmp_path / "03-audio" / ".apply_patch.lock").write_text("99999999", encoding="utf-8")
    with pytest.raises(SystemExit) as lock_exc:
        corrections.append_correction(tmp_path, patch)
    assert "应用纠错进行中，稍后再落盘" in str(lock_exc.value)
    assert "--clear-stale-lock" in str(lock_exc.value)


def test_status_next_command_clean_and_quotes_spaces(tmp_path: Path) -> None:
    ep = tmp_path / "01 测试期"
    ep.mkdir()
    (ep / "01-topic.md").write_text("# 选题\n类型：杂谈\n", encoding="utf-8")
    (ep / "02-script.md").write_text("# 稿件\n", encoding="utf-8")
    st = status.inspect_episode(ep)
    assert st.next_command is not None
    assert "(人审确认后" not in st.next_command
    assert f"'{ep.resolve()}'" in st.next_command


def test_cloud_amount_format_ansi_guard_and_active_session_preserved(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    assert cloud._fmt_amount(0.1234, "cu") == "0.1234 CU"
    assert cloud._fmt_amount(0.1234, "cny") == "¥0.1234"
    assert "CU " not in cloud._fmt_amount(1.0, "cu")
    assert "元" not in cloud._fmt_amount(1.0, "cny")
    monkeypatch.setenv("NO_COLOR", "1")
    assert "\033[" not in cloud._ansi("91", "warn")

    # 活跃会话存在 + 远端仍有活跃任务 + 未传 --force：保留 active_session.json 且不提前写账本
    sfile = tmp_path / "active_session.json"
    sfile.write_text(json.dumps({"session": "s1", "mode": "gpu", "up": "10:00:00", "up_epoch": 1000.0, "tasks": ["tts"]}), encoding="utf-8")
    ledger = tmp_path / "ledger.jsonl"
    monkeypatch.setattr(cloud, "load_cloud_config", lambda *a, **k: ({"hourly_rate_cny": 2.4}, {}))
    monkeypatch.setattr(cloud, "session_file_for", lambda *a, **k: sfile)
    monkeypatch.setattr(cloud, "get_ledger_file", lambda: ledger)
    monkeypatch.setattr(cloud, "is_ssh_reachable", lambda host: True)
    monkeypatch.setattr(cloud, "_ssh", lambda *a, **k: type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})())
    monkeypatch.setattr(cloud, "remote_active_tasks", lambda host=None: ["ava-tts"])
    assert cloud.cmd_down(argparse.Namespace(force=False, rate=None)) == 0
    assert sfile.exists()
    assert not ledger.exists()


def test_model_switch_filter_and_upward_clamp_and_card_danger_join(tmp_path: Path) -> None:
    # 向上 clamp 不再误写「已降为」
    model_switch.check_effort("minimal", model="deepseek-r1")
    assert model_switch.last_warn and "已调为 low" in model_switch.last_warn

    # write_episode_file 新建定稿但已有封板时，绝不拼出「无；改后封板失效」
    (tmp_path / "02-diff.patch").write_text("diff\n", encoding="utf-8")
    card = status_card.render_approval_card(
        "write_episode_file",
        {"filename": "02-script.md", "content": "# 稿"},
        target_exists=False,
        episode_dir=tmp_path,
    )
    assert "无；" not in card
    assert "改后封板失效" in card

    # _require_provider 大小写归一化与相近建议
    assert auth._require_provider("OpenAI").id == "openai"
    with pytest.raises(auth.AuthError, match="openai"):
        auth._require_provider("opena")


def test_auth_https_url_validation_and_device_uri_rejection(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # A5 P0 #2：严格只放行无 userinfo、无空白控制符的 https:// URL
    assert auth._validate_https_url("https://github.com/login/device") == "https://github.com/login/device"
    for bad in [
        "",
        "http://example.com/login",
        "file:///etc/passwd",
        "javascript:alert(1)",
        "-a Calculator",
        "https://user:pass@example.com/auth",
        "https://example.com/a\nb",
        "https://example.com/a b",
        "https:///no-host",
    ]:
        assert auth._validate_https_url(bad) is None, bad

    # 即使不在 PYTEST 环境下，非法 URL 也绝不调用 subprocess / webbrowser
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    called: list[object] = []
    monkeypatch.setattr(auth.subprocess, "run", lambda *a, **k: called.append(a))
    assert auth._try_open_browser("file:///etc/passwd") is False
    assert auth._try_open_browser("-a Calculator") is False
    assert called == []

    # start_auth_flow(oauth_device) 拒绝远端返回的非 https verification_uri
    monkeypatch.setenv("AVA_AUTH_PATH", str(tmp_path / "auth.local.json"))
    monkeypatch.setattr(
        auth,
        "_http_json",
        lambda *a, **k: {
            "device_code": "dc_1",
            "user_code": "ABCD-1234",
            "verification_uri_complete": "file:///tmp/evil",
            "interval": 5,
            "expires_in": 600,
        },
    )
    with pytest.raises(auth.AuthError, match="非 https"):
        auth.start_auth_flow("github-copilot", mode="oauth_device", root=tmp_path)


def test_shots_gallery_escapes_rel_src(tmp_path: Path) -> None:
    # P1 #11：番剧名或集键含 " / # / ? 时 src 必须先 percent-encode 再 html.escape
    anime = '番剧#1?x="y'
    key = "S01E01"
    out_dir = tmp_path / "shots"
    dest_dir = out_dir / "frames"
    fr = dest_dir / f"{anime}_{key}"
    fr.mkdir(parents=True)
    (fr / "00001.jpg").write_bytes(b"\xff\xd8\xff\xd9")
    (out_dir / f"{anime}_{key}.json").write_text(
        json.dumps({
            "meta": {"source": "/tmp/v.mkv", "scene_threshold": 0.3},
            "shots": [{"i": 0, "start": 0.0, "end": 2.5}],
        }),
        encoding="utf-8",
    )
    dest = shots.gallery(anime, key, out_dir, dest_dir, check=False)
    html_text = dest.read_text(encoding="utf-8")
    assert 'src="frames/%E7%95%AA%E5%89%A7%231%3Fx%3D%22y_S01E01/00001.jpg"' in html_text
    assert '="y_S01E01' not in html_text


def test_corrections_global_reseed_zero_pid_and_atomic_lock(tmp_path: Path) -> None:
    segs = {"1": "第一段", "7": "第七段"}
    with pytest.raises(corrections.PatchError, match="不支持「全局/所有段」"):
        corrections.parse_correction("7段 全局 换种子", segs)
    assert corrections._is_pid_running(0) is False
    assert corrections._is_pid_running(-1) is False

    lock_dir = tmp_path / "03-audio"
    lock_dir.mkdir(parents=True)
    (lock_dir / ".apply_patch.lock").write_text("0", encoding="utf-8")
    with pytest.raises(SystemExit, match="不是 pid"):
        corrections.clear_stale_apply_lock(tmp_path)
    assert (lock_dir / ".apply_patch.lock").exists()

    # 原子排他锁：重入直接报忙
    (lock_dir / ".apply_patch.lock").unlink()
    with corrections.apply_patch_lock(tmp_path):
        with pytest.raises(SystemExit, match="应用纠错进行中"):
            with corrections.apply_patch_lock(tmp_path):
                pass
    assert not (lock_dir / ".apply_patch.lock").exists()


def test_cloud_down_offline_colab_drive_settles_and_hold_notice(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from pipeline import cloud_drive

    # colab-drive 离线但 Drive 队列残留任务时：正常结算并清理 active_session.json
    sfile = tmp_path / "active_session.json"
    sfile.write_text(
        json.dumps({"session": "s_drive", "mode": "gpu", "up": "10:00:00", "up_epoch": 1000.0, "tasks": ["tts"]}),
        encoding="utf-8",
    )
    ledger = tmp_path / "ledger.jsonl"
    drive_root = tmp_path / "drive"
    drive_root.mkdir()
    monkeypatch.setattr(
        cloud,
        "load_cloud_config",
        lambda *a, **k: (
            {"hourly_rate_cny": 2.4},
            {"active_provider": "colab-drive", "profiles": {"colab-drive": {"drive_root": str(drive_root)}}},
        ),
    )
    monkeypatch.setattr(cloud, "session_file_for", lambda *a, **k: sfile)
    monkeypatch.setattr(cloud, "get_ledger_file", lambda: ledger)
    monkeypatch.setattr(cloud_drive, "is_drive_reachable", lambda *a, **k: True)
    monkeypatch.setattr(cloud_drive, "parse_worker_heartbeat", lambda *a, **k: {"online": False})
    monkeypatch.setattr(cloud_drive, "clear_drive_hold", lambda *a, **k: None)
    monkeypatch.setattr(cloud_drive, "active_drive_jobs", lambda *a, **k: ["stale-job"])
    monkeypatch.setattr(cloud_drive, "request_drive_shutdown", lambda *a, **k: drive_root / "shutdown")
    assert cloud.cmd_down(argparse.Namespace(force=False, rate=None, provider="colab-drive")) == 0
    assert not sfile.exists()
    assert ledger.exists()

    # 在线且有活跃任务拒关机时，输出明确告知已解除保持
    sfile.write_text(
        json.dumps({"session": "s_drive2", "mode": "gpu", "up": "10:00:00", "up_epoch": 1000.0, "tasks": ["tts"]}),
        encoding="utf-8",
    )
    monkeypatch.setattr(cloud_drive, "parse_worker_heartbeat", lambda *a, **k: {"online": True, "gpu": "T4"})
    capsys.readouterr()
    assert cloud.cmd_down(argparse.Namespace(force=False, rate=None, provider="colab-drive")) == 0
    out = capsys.readouterr().out
    assert "已解除会话保持" in out
    assert sfile.exists()


def test_repl_approve_09_symmetric_title_cover_order() -> None:
    a = cli._parse_repl_approve('/approve 09 --id appr_1 --cover "07-cover/a b.png" --title 我的 标题  "引号"')
    b = cli._parse_repl_approve('/approve 09 --id appr_1 --title 我的 标题  "引号" --cover "07-cover/a b.png"')
    assert a == b == ("09", "appr_1", {"cover": "07-cover/a b.png", "title": '我的 标题  "引号"'})
