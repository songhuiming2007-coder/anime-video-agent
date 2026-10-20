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
        # 预置 SP_MAX 个已登记资产（D127：上限 99 → 999）
        from pipeline.keys import SP_MAX, sp_key
        assets = {sp_key(i): {"path": f"/tmp/{i}.mp4", "duration": 1.0} for i in range(1, SP_MAX + 1)}
        (patch_dir / "pool.json").write_text(json.dumps({"pool": "p", "assets": assets}))
        patch_assets = ep / "patch_assets"
        patch_assets.mkdir(parents=True)
        (patch_assets / "overflow.mp4").write_bytes(b"x")

        with pytest.raises(SystemExit, match="补丁池资产数超过上限 999 个"):
            ingest_patch.ingest(ep, floor=0.60, local=True)

    def test_未转码图片拦截_指向ingest_patch自转(self, tmp_path):
        """D59：不再给人 ffmpeg 命令；指向 ingest_patch 自己（它先 convert_stills）。"""
        ep = tmp_path / "ep"
        patch_assets = ep / "patch_assets"
        patch_assets.mkdir(parents=True)
        (patch_assets / "photo.jpg").write_bytes(b"fake_jpg")

        with pytest.raises(SystemExit) as exc:
            ingest_patch.pending_assets(ep)
        msg = str(exc.value)
        assert "photo-still.mp4" in msg and "ffmpeg" not in msg

    def test_已转码图片不进候选_转出的视频进(self, tmp_path):
        ep = tmp_path / "ep"
        patch_assets = ep / "patch_assets"
        patch_assets.mkdir(parents=True)
        (patch_assets / "photo.jpg").write_bytes(b"fake_jpg")
        (patch_assets / "photo-still.mp4").write_bytes(b"x")
        assert [f.name for f in ingest_patch.pending_assets(ep)] == ["photo-still.mp4"]

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
        assert "ingest_patch --reset" in str(exc.value)

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
        assert "ingest_patch --reset" in str(exc.value)

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
        assert "ingest_patch --reset" in str(exc.value)

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


class TestAnchorOnly:
    """`--anchor-only`（D84 PR-C）：切完镜头就登记，不打标、不碰云端、不要 floor。

    写稿期补一张图表就要能立刻写锚点；原先 pool.json 要等云端打标 + 向量化都完成才写，
    只为锚点一张图也得开计费 GPU。
    """

    def _stub_stage1(self, ep, monkeypatch, n_files=1):
        patch_assets = ep / "patch_assets"
        patch_assets.mkdir(parents=True)
        for i in range(n_files):
            (patch_assets / f"chart{i}.mp4").write_bytes(b"dummy")
        monkeypatch.setattr(ingest_patch.bgm, "anime_of", lambda p: "未标定的番")
        monkeypatch.setattr(ingest_patch, "intact", lambda p: None)
        monkeypatch.setattr(ingest_patch, "probe_video", lambda p: {"duration": 12.0, "fps": 23.976})
        monkeypatch.setattr(ingest_patch.shots, "scan", lambda p: [])
        monkeypatch.setattr(ingest_patch.shots, "cut", lambda c, t, d, m: [{"start": 0.0, "end": 12.0}])
        monkeypatch.setattr(ingest_patch.shots, "caption_frames", lambda *a, **kw: None)

        def no_cloud(*a, **kw):
            raise AssertionError("anchor-only 不许碰云端 / 打标 / 向量化")
        for name in ("load_cloud_config", "cmd_push", "cmd_run", "cmd_pull"):
            monkeypatch.setattr(ingest_patch.cloud, name, no_cloud)
        monkeypatch.setattr(ingest_patch.vindex, "build_captions", no_cloud)
        monkeypatch.setattr(ingest_patch.vindex, "build_embed", no_cloud)

    def test_只登记不打标_锚点可解析(self, tmp_path, monkeypatch):
        ep = tmp_path / "ep"
        self._stub_stage1(ep, monkeypatch)
        # 主番没标定、也没给 --floor：完整 ingest 会 FAIL，anchor-only 不该被拦
        assert ingest_patch.ingest(ep, anchor_only=True) == 0
        meta = json.loads((ep / "04-patch" / "pool.json").read_text(encoding="utf-8"))
        assert meta["assets"]["SP01"]["indexed"] is False
        assert "floor" not in meta and meta["no_match_source"] == "anchor-only"
        pool = ingest_patch.load_pool(ep)
        assert pool["sources"]["SP01"]["duration"] == 12.0
        assert pool["shots"][1] == pool["shots"]["SP01"] and len(pool["shots"]["SP01"]) == 1
        assert pool["vecs"].shape[0] == 0 and pool["units"] == []
        assert pool["unindexed"] == ["SP01"]

    def test_再跑完整ingest_编号不变且翻成已打标(self, tmp_path, monkeypatch):
        ep = tmp_path / "ep"
        self._stub_stage1(ep, monkeypatch)
        ingest_patch.ingest(ep, anchor_only=True)
        pool_name = ingest_patch.sanitize_pool_name(ep.name)

        def fake_captions(pool, key, out_dir, **kw):
            (out_dir / f"{pool}_{key}.captions.json").write_text(json.dumps(
                {"captions": [{"shot": 0, "status": "ok", "caption": "受力图"}]}))

        def fake_embed(pool, out_dir, **kw):
            np.save(out_dir / f"{pool}_SP01.scene.npy", np.ones((1, vindex.EMBED_DIM), dtype=np.float32))
            (out_dir / f"{pool}_SP01.scene.json").write_text(json.dumps(
                {"shots": [{"start": 0.0, "end": 12.0, "label": "受力图"}]}))
        monkeypatch.setattr(ingest_patch.vindex, "build_captions", fake_captions)
        monkeypatch.setattr(ingest_patch.vindex, "build_embed", fake_embed)

        assert ingest_patch.ingest(ep, floor=0.6, local=True) == 0
        meta = json.loads((ep / "04-patch" / "pool.json").read_text(encoding="utf-8"))
        assert list(meta["assets"]) == ["SP01"]            # 没有被当成新资产重编成 SP02
        assert meta["assets"]["SP01"]["indexed"] is True and meta["floor"] == 0.6
        pool = ingest_patch.load_pool(ep)
        assert pool["unindexed"] == [] and pool["vecs"].shape[0] == 1
        assert (ep / "04-patch" / "vindex" / f"{pool_name}_SP01.captions.json").exists()

    def test_外置盘上的资产登记相对仓库根路径(self, tmp_path, monkeypatch, capsys):
        # N59：data 是指向外置盘的符号链接，resolve 后是卷路径；登记表要存 data/…，换盘不废
        repo, ext = tmp_path / "repo", tmp_path / "Volumes" / "T7" / "anime-video-data"
        repo.mkdir()
        ext.mkdir(parents=True)
        (repo / "data").symlink_to(ext)
        monkeypatch.setattr(ingest_patch.paths, "ROOT", repo)
        monkeypatch.chdir(tmp_path)                       # 读取不靠 cwd 恰好是仓库根
        ep = repo / "data" / "episodes" / "ep"
        self._stub_stage1(ep, monkeypatch)
        ingest_patch.ingest(ep, anchor_only=True)
        meta = json.loads((ep / "04-patch" / "pool.json").read_text(encoding="utf-8"))
        assert meta["assets"]["SP01"]["path"] == "data/episodes/ep/patch_assets/chart0.mp4"
        shot = json.loads((ep / "04-patch" / "shots" / f"{meta['pool']}_SP01.json").read_text(encoding="utf-8"))
        assert shot["meta"]["source"] == "data/episodes/ep/patch_assets/chart0.mp4"
        capsys.readouterr()
        assert ingest_patch.load_pool(ep)["sources"]["SP01"]["path"] == "data/episodes/ep/patch_assets/chart0.mp4"
        assert "不存在" not in capsys.readouterr().err, "相对路径要按仓库根找到文件"
        assert ingest_patch.pending_assets(ep) == [], "已登记的资产重跑时不能被当成新资产"

    def test_转图产物登记still标并带进sources(self, tmp_path, monkeypatch):
        ep = tmp_path / "ep"
        self._stub_stage1(ep, monkeypatch)
        (ep / "patch_assets" / "chart0.mp4").rename(ep / "patch_assets" / "chart0-still.mp4")
        ingest_patch.ingest(ep, anchor_only=True)
        meta = json.loads((ep / "04-patch" / "pool.json").read_text(encoding="utf-8"))
        assert meta["assets"]["SP01"]["still"] is True
        assert ingest_patch.load_pool(ep)["sources"]["SP01"]["still"] is True

    def test_旧池没有indexed键按已打标读(self, tmp_path):
        # D84 之前建的池只有打完标才登记；没有 indexed 键必须照旧要求完整索引，缺了照样 FAIL
        ep = tmp_path / "ep"
        (ep / "04-patch" / "shots").mkdir(parents=True)
        (ep / "04-patch" / "vindex").mkdir()
        (ep / "04-patch" / "pool.json").write_text(json.dumps({"pool": "p", "floor": 0.6, "assets": {
            "SP01": {"path": str(ep / "a.mp4"), "duration": 5.0, "size": 1, "mtime": 0.0}}}))
        (ep / "04-patch" / "shots" / "p_SP01.json").write_text(json.dumps({"shots": [{"start": 0, "end": 5}]}))
        with pytest.raises(SystemExit, match="索引缺失或半建"):
            ingest_patch.load_pool(ep)
