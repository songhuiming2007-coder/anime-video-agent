"""test_ingest_patch.py：补料通道与补丁池测试（Spec §4）。"""

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
import pytest

from pipeline import ingest_patch, vindex


class TestSanitizePoolName:
    def test_中文与特殊符号净化(self):
        assert ingest_patch.sanitize_pool_name("EGOIST三期") == "EGOIST-patch"
        assert ingest_patch.sanitize_pool_name("ep_01-test") == "ep_01-test-patch"
        assert ingest_patch.sanitize_pool_name("@@@") == "patch-patch"

    def test_撞名追加序号(self):
        existing = {"ep01-patch", "ep01-patch-2"}
        assert ingest_patch.sanitize_pool_name("ep01", existing) == "ep01-patch-3"


class TestPendingAssets:
    def test_资产超过99个报错(self, tmp_path):
        ep = tmp_path / "ep"
        patch_dir = ep / "04-patch"
        patch_dir.mkdir(parents=True)
        # 预置 99 个已登记资产
        assets = {f"SP{i:02d}": {"path": f"/tmp/{i}.mp4", "duration": 1.0} for i in range(1, 100)}
        (patch_dir / "pool.json").write_text(json.dumps({"pool": "p", "assets": assets}))
        patch_assets = ep / "patch_assets"
        patch_assets.mkdir(parents=True)
        (patch_assets / "overflow.mp4").write_bytes(b"x")

        with pytest.raises(SystemExit, match="补丁池资产数超过上限 99 个"):
            ingest_patch.ingest(ep, floor=0.60, local=True)

    def test_图片资产立即拦截并给出转换命令(self, tmp_path):
        ep = tmp_path / "ep"
        patch_assets = ep / "patch_assets"
        patch_assets.mkdir(parents=True)
        (patch_assets / "photo.jpg").write_bytes(b"fake_jpg")

        with pytest.raises(SystemExit) as exc:
            ingest_patch.pending_assets(ep)
        msg = str(exc.value)
        assert "ffmpeg -loop 1 -t 8 -i photo.jpg -pix_fmt yuv420p photo.mp4" in msg

    def test_无资产返回空列表(self, tmp_path):
        ep = tmp_path / "ep"
        assert ingest_patch.pending_assets(ep) == []

    def test_忽略隐藏文件并识别新视频(self, tmp_path):
        ep = tmp_path / "ep"
        patch_assets = ep / "patch_assets"
        patch_assets.mkdir(parents=True)
        (patch_assets / ".DS_Store").write_bytes(b"hidden")
        vid = patch_assets / "v1.mp4"
        vid.write_bytes(b"video")

        pending = ingest_patch.pending_assets(ep)
        assert pending == [vid]


class TestResolvePatchFloor:
    def test_未标定且无显式floor抛错(self, monkeypatch):
        monkeypatch.setattr(vindex, "scene_conf", lambda anime, path: {})
        with pytest.raises(SystemExit, match="主番未标定画面检索门槛"):
            ingest_patch.resolve_patch_floor("未知番", explicit_floor=None)

    def test_未标定但给显式floor放行(self, monkeypatch):
        monkeypatch.setattr(vindex, "scene_conf", lambda anime, path: {})
        floor, src, cal = ingest_patch.resolve_patch_floor("未知番", explicit_floor=0.62)
        assert floor == 0.62
        assert src == "explicit"
        assert cal is False

    def test_已标定直接沿用(self, monkeypatch):
        monkeypatch.setattr(vindex, "scene_conf", lambda anime, path: {"no_match": 0.584})
        floor, src, cal = ingest_patch.resolve_patch_floor("罪恶王冠", explicit_floor=None)
        assert floor == 0.584
        assert src == "罪恶王冠"
        assert cal is True


class TestLoadPool:
    def _create_mock_pool(self, ep: Path, pool_name: str, key: str, n_shots: int, zero_ratio: float = 0.0):
        patch_dir = ep / "04-patch"
        shots_dir = patch_dir / "shots"
        vindex_dir = patch_dir / "vindex"
        for d in (shots_dir, vindex_dir):
            d.mkdir(parents=True, exist_ok=True)

        asset_file = ep / "patch_assets" / "asset.mp4"
        asset_file.parent.mkdir(parents=True, exist_ok=True)
        asset_file.write_bytes(b"asset_content")

        pool_json = patch_dir / "pool.json"
        pool_json.write_text(json.dumps({
            "pool": pool_name,
            "floor": 0.60,
            "no_match": 0.60,
            "no_match_source": "explicit",
            "calibrated": False,
            "assets": {
                key: {
                    "path": str(asset_file),
                    "duration": 10.0,
                    "size": asset_file.stat().st_size,
                    "mtime": asset_file.stat().st_mtime,
                    "fps": 24.0,
                }
            }
        }, ensure_ascii=False), encoding="utf-8")

        sh_list = [{"i": i, "start": i * 1.0, "end": (i + 1) * 1.0} for i in range(n_shots)]
        (shots_dir / f"{pool_name}_{key}.json").write_text(
            json.dumps({"meta": {"duration": 10.0}, "shots": sh_list}), encoding="utf-8"
        )

        sc_shots = [{"i": i, "start": i * 1.0, "end": (i + 1) * 1.0, "label": f"shot {i}"} for i in range(n_shots)]
        (vindex_dir / f"{pool_name}_{key}.scene.json").write_text(
            json.dumps({"shots": sc_shots}), encoding="utf-8"
        )

        vecs = np.ones((n_shots, vindex.EMBED_DIM), dtype=np.float32)
        n_zero = int(n_shots * zero_ratio)
        if n_zero > 0:
            vecs[:n_zero] = 0.0
        np.save(vindex_dir / f"{pool_name}_{key}.scene.npy", vecs)

    def test_无04patch目录返回None(self, tmp_path):
        assert ingest_patch.load_pool(tmp_path / "ep") is None

    def test_目录存在但缺少pool_json指路(self, tmp_path):
        ep = tmp_path / "ep"
        (ep / "04-patch").mkdir(parents=True)
        with pytest.raises(SystemExit) as exc:
            ingest_patch.load_pool(ep)
        assert "删 04-patch 目录，或重跑" in str(exc.value)

    def test_索引半建指路(self, tmp_path):
        ep = tmp_path / "ep"
        patch_dir = ep / "04-patch"
        patch_dir.mkdir(parents=True)
        (patch_dir / "pool.json").write_text(json.dumps({
            "pool": "test-patch", "assets": {"SP01": {"path": "/tmp/a.mp4", "duration": 1.0}}
        }))
        (patch_dir / "shots").mkdir()
        (patch_dir / "vindex").mkdir()
        # 缺少具体索引文件
        with pytest.raises(SystemExit) as exc:
            ingest_patch.load_pool(ep)
        assert "删 04-patch 目录，或重跑" in str(exc.value)

    def test_行数不一致报错指路(self, tmp_path):
        ep = tmp_path / "ep"
        self._create_mock_pool(ep, "test-patch", "SP01", n_shots=5)
        # 破坏 scene.json 镜头数
        sc_file = ep / "04-patch" / "vindex" / "test-patch_SP01.scene.json"
        sc_data = json.loads(sc_file.read_text(encoding="utf-8"))
        sc_data["shots"].pop()
        sc_file.write_text(json.dumps(sc_data), encoding="utf-8")

        with pytest.raises(SystemExit) as exc:
            ingest_patch.load_pool(ep)
        assert "scene 行数与镜头表不一致" in str(exc.value)
        assert "删 04-patch 目录，或重跑" in str(exc.value)

    def test_零向量占比过高打印WARN(self, tmp_path, capsys):
        ep = tmp_path / "ep"
        self._create_mock_pool(ep, "test-patch", "SP01", n_shots=10, zero_ratio=0.3)
        res = ingest_patch.load_pool(ep)
        assert res is not None
        assert res["pool"] == "test-patch"
        assert res["vecs"].shape[0] == 10
        assert len(res["units"]) == 10
        err = capsys.readouterr().err
        assert "零向量占比过高" in err and "30.0%" in err

    def test_资产漂移检测WARN(self, tmp_path, capsys):
        ep = tmp_path / "ep"
        self._create_mock_pool(ep, "test-patch", "SP01", n_shots=2)
        # 修改文件内容触发 size/mtime 漂移
        asset_file = ep / "patch_assets" / "asset.mp4"
        asset_file.write_bytes(b"modified_asset_content_longer")
        res = ingest_patch.load_pool(ep)
        assert res is not None
        err = capsys.readouterr().err
        assert "补丁资产源文件发生漂移" in err


class TestTwoStageExecution:
    def test_三态可辨_关机_运行中_就绪(self, tmp_path, monkeypatch, capsys):
        ep = tmp_path / "ep"
        patch_assets = ep / "patch_assets"
        patch_assets.mkdir(parents=True)
        vid = patch_assets / "sample.mp4"
        vid.write_bytes(b"dummy")

        monkeypatch.setattr(ingest_patch.bgm, "anime_of", lambda p: "test_anime")
        monkeypatch.setattr(ingest_patch, "intact", lambda p: None)
        monkeypatch.setattr(ingest_patch, "probe_video", lambda p: {"duration": 5.0, "fps": 24.0})
        monkeypatch.setattr(ingest_patch.shots, "scan", lambda p: [])
        monkeypatch.setattr(ingest_patch.shots, "cut", lambda c, t, d, m: [{"start": 0.0, "end": 5.0}])
        monkeypatch.setattr(ingest_patch.shots, "caption_frames", lambda *a, **kw: None)

        # 态 1：实例关机 -> FAIL
        monkeypatch.setattr(ingest_patch.cloud, "load_cloud_config", lambda: ({}, {}))
        monkeypatch.setattr(ingest_patch.cloud, "_ssh_host", lambda c: "autodl")
        monkeypatch.setattr(ingest_patch.cloud, "is_ssh_reachable", lambda h: False)

        with pytest.raises(SystemExit, match="实例不可达/已关机"):
            ingest_patch.ingest(ep, floor=0.60)

        # 态 2：远端任务进行中 -> return 2, 提示等待中
        monkeypatch.setattr(ingest_patch.cloud, "is_ssh_reachable", lambda h="autodl": True)
        monkeypatch.setattr(ingest_patch.cloud, "remote_active_tasks", lambda *a, **kw: ["ava-captions"])

        rc = ingest_patch.ingest(ep, floor=0.60)
        assert rc == 2
        out = capsys.readouterr().out
        assert "远端打标任务正在运行" in out and "ava-captions" in out

        # 态 3：就绪 -> pull 产物就绪并完成 embed 与登记
        monkeypatch.setattr(ingest_patch.cloud, "remote_active_tasks", lambda *a, **kw: [])
        def fake_pull(args):
            # 模拟 pull 成功写入 captions.json
            cap_dir = ep / "04-patch" / "vindex"
            cap_dir.mkdir(parents=True, exist_ok=True)
            pool_name = ingest_patch.sanitize_pool_name(ep.name)
            (cap_dir / f"{pool_name}_SP01.captions.json").write_text(json.dumps({
                "captions": [{"shot": 0, "status": "ok", "caption": "test caption"}]
            }))
            return 0

        monkeypatch.setattr(ingest_patch.cloud, "cmd_pull", fake_pull)

        def fake_embed(pool, out_dir, **kw):
            np.save(out_dir / f"{pool}_SP01.scene.npy", np.ones((1, vindex.EMBED_DIM), dtype=np.float32))
            (out_dir / f"{pool}_SP01.scene.json").write_text(json.dumps({
                "shots": [{"start": 0.0, "end": 5.0, "label": "test"}]
            }))

        monkeypatch.setattr(ingest_patch.vindex, "build_embed", fake_embed)

        rc = ingest_patch.ingest(ep, floor=0.60)
        assert rc == 0
        pool_res = ingest_patch.load_pool(ep)
        assert pool_res is not None
        assert "SP01" in pool_res["pool_meta"]["assets"]


class TestConcurrencyGuard:
    def test_pool_json写入前并发修改拦截(self, tmp_path, monkeypatch):
        ep = tmp_path / "ep"
        patch_assets = ep / "patch_assets"
        patch_assets.mkdir(parents=True)
        (patch_assets / "sample.mp4").write_bytes(b"dummy")

        patch_dir = ep / "04-patch"
        patch_dir.mkdir(parents=True)
        pool_json = patch_dir / "pool.json"
        pool_json.write_text(json.dumps({"pool": "p", "assets": {}}))

        monkeypatch.setattr(ingest_patch.bgm, "anime_of", lambda p: "test_anime")
        monkeypatch.setattr(ingest_patch, "intact", lambda p: None)
        monkeypatch.setattr(ingest_patch, "probe_video", lambda p: {"duration": 5.0, "fps": 24.0})
        monkeypatch.setattr(ingest_patch.shots, "scan", lambda p: [])
        monkeypatch.setattr(ingest_patch.shots, "cut", lambda c, t, d, m: [{"start": 0.0, "end": 5.0}])
        monkeypatch.setattr(ingest_patch.shots, "caption_frames", lambda *a, **kw: None)
        monkeypatch.setattr(ingest_patch.vindex, "build_captions", lambda *a, **kw: None)

        # 模拟在 build_embed 执行期间外部修改 pool.json
        def fake_embed(*a, **kw):
            pool_json.write_text(json.dumps({"pool": "p", "assets": {}, "modified_by_other": True}))

        monkeypatch.setattr(ingest_patch.vindex, "build_embed", fake_embed)

        with pytest.raises(SystemExit, match="pool.json 在本次执行期间被外部修改"):
            ingest_patch.ingest(ep, floor=0.60, local=True)
