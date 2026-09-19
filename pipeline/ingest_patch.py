"""临时补料与联合检索补丁池编排（Spec §4）。

零新依赖，纯标准库 + 项目既有 shots/vindex/cloud 模块。
视频 only，图片资产直接拦截并提示转码命令（R5）。
两段式执行：本地切镜头/抽帧 → 云端 VLM captions → 本地 embed 向量化。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from pathlib import Path

import numpy as np

from . import bgm, cloud, paths, shots, vindex
from .ingest import intact, probe_video

# 默认补丁池镜头切分阈值（当主番未标定且未指定时使用）
DEFAULT_PATCH_THRESHOLD = 12.0

# 支持的视频扩展名与明确拒绝的图片扩展名
VIDEO_EXTS = {".mp4", ".mkv", ".mov", ".avi", ".webm", ".m4v"}
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}


def sanitize_pool_name(name: str, existing_pools: set[str] | None = None) -> str:
    """期目录名净化为补丁池名（Spec §4.1 Y4）。

    仅保留英文字母、数字、下划线与连字符；撞名追加序号。
    """
    cleaned = re.sub(r"[^A-Za-z0-9_-]+", "-", name).strip("-")
    if not cleaned:
        cleaned = "patch"
    candidate = f"{cleaned}-patch"
    if not existing_pools or candidate not in existing_pools:
        return candidate
    i = 2
    while f"{candidate}-{i}" in existing_pools:
        i += 1
    return f"{candidate}-{i}"


def pending_assets(episode: Path) -> list[Path]:
    """检测 patch_assets/ 下未在 04-patch/pool.json 登记的视频文件。

    严格视频-only：遇到图片资产立即报错并给出 ffmpeg 转换命令（R5）。
    """
    assets_dir = episode / "patch_assets"
    if not assets_dir.is_dir():
        return []

    # 过滤隐藏文件（.DS_Store 等）
    files = sorted([
        f for f in assets_dir.iterdir()
        if f.is_file() and not f.name.startswith(".")
    ])

    # 图片类型守卫 (R5)
    for f in files:
        if f.suffix.lower() in IMAGE_EXTS:
            raise SystemExit(
                f"FAIL 检测到图片资产 {f.name}。当前补料链路仅支持视频资产。\n"
                f"     请使用 ffmpeg 将其转为微动视频后重新放入 patch_assets/：\n"
                f"     ffmpeg -loop 1 -t 8 -i {f.name} -pix_fmt yuv420p {f.stem}.mp4"
            )

    pool_path = episode / "04-patch" / "pool.json"
    registered_paths: set[str] = set()
    if pool_path.exists():
        try:
            data = json.loads(pool_path.read_text(encoding="utf-8"))
            assets = data.get("assets", {})
            for a in assets.values():
                if "path" in a:
                    registered_paths.add(str(Path(a["path"]).resolve()))
                    registered_paths.add(Path(a["path"]).name)
        except Exception:
            pass

    unregistered = [
        f for f in files
        if str(f.resolve()) not in registered_paths and f.name not in registered_paths
    ]
    return unregistered


def get_shot_threshold(anime: str | None) -> tuple[float, str]:
    """获取镜头切分阈值：主番已标定用主番值，未标定用默认值（Spec §4.3）。"""
    if anime:
        try:
            val = shots.threshold(anime)
            return val, anime
        except SystemExit:
            pass
    return DEFAULT_PATCH_THRESHOLD, "patch-default"


def resolve_patch_floor(main_anime: str | None, explicit_floor: float | None = None) -> tuple[float, str, bool]:
    """计算补丁池检索门槛 floor（Spec §4.4 R4）。

    返回: (floor_value, no_match_source, calibrated)
    """
    if explicit_floor is not None:
        return explicit_floor, "explicit", False
    if main_anime:
        try:
            cfg = vindex.scene_conf(main_anime, vindex.SCENES)
            t = cfg.get("no_match")
            if t is not None:
                return float(t), main_anime, True
        except Exception:
            pass
    raise SystemExit(
        "FAIL 主番未标定画面检索门槛 (no_match)，补料入库时必须显式指定 `--floor <值>`：\n"
        "     示例：python -m pipeline.ingest_patch <期目录> --floor 0.60\n"
        "     （注：主番标定可跑 python -m pipeline.vprobe scene <番名> <集号>）"
    )


def load_pool(episode: Path) -> dict | None:
    """读取并自校验期级补丁池（Spec §4.3 & §4.4）。

    若 04-patch/ 目录不存在返回 None。
    若存在但损坏/半建则抛出显式 SystemExit 指路。
    """
    patch_dir = episode / "04-patch"
    if not patch_dir.exists():
        return None

    pool_path = patch_dir / "pool.json"
    if not pool_path.exists():
        raise SystemExit(
            f"FAIL 04-patch/ 目录存在但缺少 pool.json。\n"
            f"     删 04-patch 目录，或重跑 `python -m pipeline.ingest_patch {episode}`"
        )

    try:
        data = json.loads(pool_path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise SystemExit(
            f"FAIL 补丁池登记表 pool.json 损坏无法读取: {exc}\n"
            f"     删 04-patch 目录，或重跑 `python -m pipeline.ingest_patch {episode}`"
        ) from exc

    pool = data.get("pool")
    if not pool:
        raise SystemExit(
            f"FAIL pool.json 缺少 pool 字段。\n"
            f"     删 04-patch 目录，或重跑 `python -m pipeline.ingest_patch {episode}`"
        )

    floor = data.get("floor") or data.get("no_match", 0.60)
    assets = data.get("assets", {})
    if not assets:
        # 空补丁池直接返回空容器
        return {
            "pool": pool, "floor": floor, "no_match": floor,
            "sources": {}, "shots": {}, "vecs": np.zeros((0, vindex.EMBED_DIM), dtype=np.float32),
            "units": [], "pool_meta": data,
        }

    shots_dir = patch_dir / "shots"
    vindex_dir = patch_dir / "vindex"
    if not shots_dir.is_dir() or not vindex_dir.is_dir():
        raise SystemExit(
            f"FAIL 04-patch 目录不完整（缺少 shots/ 或 vindex/ 目录）。\n"
            f"     删 04-patch 目录，或重跑 `python -m pipeline.ingest_patch {episode}`"
        )

    sources: dict = {}
    shots_table: dict = {}
    vec_list: list[np.ndarray] = []
    unit_list: list[vindex.Shot] = []

    for key, asset in sorted(assets.items()):
        # 1. 源文件漂移检测 (B4)
        asset_path = Path(asset["path"])
        if not asset_path.exists():
            print(f"WARN 补丁资产源文件不存在: {asset_path}（入库后被删除，可能导致后续渲染失败）", file=sys.stderr)
        else:
            try:
                st = asset_path.stat()
                if st.st_size != asset.get("size") or abs(st.st_mtime - asset.get("mtime", st.st_mtime)) > 1e-3:
                    print(f"WARN 补丁资产源文件发生漂移: {asset_path}（size/mtime 已变）", file=sys.stderr)
            except Exception:
                pass

        # 2. 索引完整性自校验
        shot_file = shots_dir / f"{pool}_{key}.json"
        scene_npy = vindex_dir / f"{pool}_{key}.scene.npy"
        scene_json = vindex_dir / f"{pool}_{key}.scene.json"

        if not shot_file.exists() or not scene_npy.exists() or not scene_json.exists():
            raise SystemExit(
                f"FAIL 补丁池 {pool} {key} 索引缺失或半建。\n"
                f"     删 04-patch 目录，或重跑 `python -m pipeline.ingest_patch {episode}`"
            )

        try:
            sh_data = json.loads(shot_file.read_text(encoding="utf-8"))
            sc_data = json.loads(scene_json.read_text(encoding="utf-8"))
            vecs = np.load(scene_npy)
        except Exception as exc:
            raise SystemExit(
                f"FAIL 补丁池 {pool} {key} 索引文件损坏: {exc}\n"
                f"     删 04-patch 目录，或重跑 `python -m pipeline.ingest_patch {episode}`"
            ) from exc

        sh_list = sh_data.get("shots", [])
        sc_shots = sc_data.get("shots", [])

        # 行数一致性校验
        if len(sc_shots) != len(sh_list) or vecs.shape[0] != len(sh_list):
            raise SystemExit(
                f"FAIL 补丁池 {pool} {key} scene 行数与镜头表不一致（{len(sc_shots)} vs {len(sh_list)}）。\n"
                f"     删 04-patch 目录，或重跑 `python -m pipeline.ingest_patch {episode}`"
            )

        # 登记 sources 与 shots
        sources[(pool, key)] = {
            "path": str(asset_path),
            "duration": asset["duration"],
            "fps": asset.get("fps", 24.0),
        }
        sources[key] = sources[(pool, key)]

        shots_table[key] = sh_list
        if key.startswith("SP") and key[2:].isdigit():
            shots_table[int(key[2:])] = sh_list

        vec_list.append(vecs)
        ep_no = int(key[2:]) if (key.startswith("SP") and key[2:].isdigit()) else 1
        for s in sc_shots:
            unit_list.append(vindex.Shot(
                anime=pool,
                season=None,
                episode=ep_no,
                start=s["start"],
                end=s["end"],
                text=s.get("label", ""),
            ))

    all_vecs = np.concatenate(vec_list, axis=0) if vec_list else np.zeros((0, vindex.EMBED_DIM), dtype=np.float32)

    # 闸 1：零向量占比检测（>10% WARN）
    if all_vecs.shape[0] > 0:
        zero_count = int(np.sum(np.all(all_vecs == 0, axis=1)))
        total_count = all_vecs.shape[0]
        zero_ratio = zero_count / total_count
        if zero_ratio > 0.10:
            print(
                f"WARN 补丁池 {pool} 零向量占比过高: {zero_ratio:.1%} "
                f"({zero_count}/{total_count} 个镜头打标失败为零向量)",
                file=sys.stderr,
            )

    return {
        "pool": pool,
        "floor": floor,
        "no_match": floor,
        "sources": sources,
        "shots": shots_table,
        "vecs": all_vecs,
        "units": unit_list,
        "pool_meta": data,
    }


def ingest(
    episode: Path,
    floor: float | None = None,
    local: bool = False,
    batch: int = vindex.CAPTION_BATCH,
    confirm_cost: bool = False,
) -> int:
    """单期补料编排流程（Spec §4.3 两段式）。"""
    episode = Path(episode)
    if not episode.is_dir():
        raise SystemExit(f"FAIL 期目录不存在: {episode}")

    pool_name = sanitize_pool_name(episode.name)
    patch_dir = episode / "04-patch"
    patch_shots_dir = patch_dir / "shots"
    patch_frames_dir = patch_dir / "frames"
    patch_vindex_dir = patch_dir / "vindex"
    pool_path = patch_dir / "pool.json"

    for d in (patch_shots_dir, patch_frames_dir, patch_vindex_dir):
        d.mkdir(parents=True, exist_ok=True)

    requests_md = patch_dir / "requests.md"
    if not requests_md.exists():
        requests_md.write_text("# 补料缺口清单\n\n", encoding="utf-8")

    # 记录 pool.json 并发指纹 (B1-r16)
    initial_mtime = pool_path.stat().st_mtime if pool_path.exists() else None
    initial_sha = hashlib.sha256(pool_path.read_bytes()).hexdigest() if pool_path.exists() else None

    pool_data: dict = {}
    if pool_path.exists():
        try:
            pool_data = json.loads(pool_path.read_text(encoding="utf-8"))
        except Exception as e:
            raise SystemExit(f"FAIL pool.json 损坏: {e}") from e

    registered_assets = pool_data.setdefault("assets", {})
    existing_keys = [k for k in registered_assets.keys() if k.startswith("SP") and k[2:].isdigit()]
    used_numbers = [int(k[2:]) for k in existing_keys]
    next_number = (max(used_numbers) + 1) if used_numbers else 1

    main_anime = bgm.anime_of(episode)
    # 门槛计算
    if "floor" in pool_data and floor is None:
        thr_floor = float(pool_data["floor"])
        floor_source = pool_data.get("no_match_source", "pool-existing")
        calibrated = pool_data.get("calibrated", False)
    else:
        thr_floor, floor_source, calibrated = resolve_patch_floor(main_anime, floor)

    # 1. 扫描待处理资产
    unregistered = pending_assets(episode)

    # 2. 本地准备：镜头切分与抽帧
    new_keys: list[str] = []
    for asset in unregistered:
        if next_number > 99:
            raise SystemExit(f"FAIL 补丁池资产数超过上限 99 个（尝试添加 {asset.name}）")
        key = f"SP{next_number:02d}"
        next_number += 1

        print(f"[*] 处理补丁资产: {asset.name} -> {pool_name} {key}")
        intact(asset)
        probe = probe_video(asset)
        duration = probe["duration"]
        fps = probe["fps"]

        # 镜头切分
        shot_thr, thr_src = get_shot_threshold(main_anime)
        min_shot = shots.min_shot()
        cuts = shots.scan(asset)
        cut_list = shots.cut(cuts, shot_thr, duration, min_shot)

        meta = {
            "detector": "ffmpeg-scdet",
            "scene_threshold": shot_thr,
            "threshold_source": thr_src,
            "min_shot": min_shot,
            "duration": duration,
            "fps": fps,
            "source": str(asset.resolve()),
        }
        shot_table = {
            "meta": meta,
            "shots": [
                {
                    "i": i,
                    "start": s["start"],
                    "end": s["end"],
                    "rep": round((s["start"] + s["end"]) / 2, 3),
                }
                for i, s in enumerate(cut_list)
            ],
        }
        paths.atomic_write(
            patch_shots_dir / f"{pool_name}_{key}.json",
            json.dumps(shot_table, ensure_ascii=False, indent=2),
        )

        # 抽帧
        shots.caption_frames(pool_name, key, out_dir=patch_shots_dir, dest_dir=patch_frames_dir, check=False)

        registered_assets[key] = {
            "path": str(asset.resolve()),
            "duration": duration,
            "size": asset.stat().st_size,
            "mtime": asset.stat().st_mtime,
            "fps": fps,
        }
        new_keys.append(key)

    # 收集全部需打标的 key（新资产 + 旧资产中未完成者）
    all_keys = sorted(registered_assets.keys())
    keys_needing_captions: list[str] = []
    for k in all_keys:
        cap_file = patch_vindex_dir / f"{pool_name}_{k}.captions.json"
        if not cap_file.exists():
            keys_needing_captions.append(k)
        else:
            try:
                cdata = json.loads(cap_file.read_text(encoding="utf-8"))
                if vindex.pending_shots(cdata.get("captions", [])) > 0:
                    keys_needing_captions.append(k)
            except Exception:
                keys_needing_captions.append(k)

    # 3. captions 打标步骤
    if keys_needing_captions:
        if local:
            print(f"[*] 本地执行 VLM captions: {pool_name} ({', '.join(keys_needing_captions)})")
            for k in keys_needing_captions:
                vindex.build_captions(
                    pool_name,
                    k,
                    out_dir=patch_vindex_dir,
                    shots_dir=patch_shots_dir,
                    frames_dir=patch_frames_dir,
                    batch=batch,
                    check=False,
                )
        else:
            # 云端两段式
            cfg_global, cfg_local = cloud.load_cloud_config()
            host = cloud._ssh_host(cfg_local)
            if not cloud.is_ssh_reachable(host):
                raise SystemExit("FAIL 实例不可达/已关机，先 `python -m pipeline.cloud up`")

            active = cloud.remote_active_tasks(host=host)
            if "ava-captions" in active:
                print("[*] 远端打标任务正在运行 (ava-captions)，请稍后重跑本命令。")
                return 2

            # 尝试拉回现有产物
            cloud.cmd_pull(argparse.Namespace(target=str(episode)))

            # 重新核验是否仍有未完成的 key
            still_pending = []
            for k in keys_needing_captions:
                cap_file = patch_vindex_dir / f"{pool_name}_{k}.captions.json"
                if not cap_file.exists():
                    still_pending.append(k)
                else:
                    try:
                        cdata = json.loads(cap_file.read_text(encoding="utf-8"))
                        if vindex.pending_shots(cdata.get("captions", [])) > 0:
                            still_pending.append(k)
                    except Exception:
                        still_pending.append(k)

            if still_pending:
                # 远端未在运行且本地尚缺产物：提交远端任务
                print(f"[*] 推送资产并提交远端打标任务: {still_pending}")
                cloud.cmd_push(argparse.Namespace(target=str(episode)))
                cloud.cmd_run(argparse.Namespace(
                    target=str(episode),
                    task="captions",
                    extra=[pool_name] + still_pending,
                ))
                print("[*] 已提交云端打标 (tmux: ava-captions)，完成后请重跑本命令。")
                return 0

    # 4. stage 2 就绪：执行本地 embed 向量化
    print(f"[*] 向量化建库: {pool_name} -> {patch_vindex_dir}")
    vindex.build_embed(
        pool_name,
        out_dir=patch_vindex_dir,
        batch=batch,
        shots_dir=patch_shots_dir,
        check=False,
    )

    # 5. 落盘登记 pool.json
    pool_data["pool"] = pool_name
    pool_data["floor"] = thr_floor
    pool_data["no_match"] = thr_floor
    pool_data["no_match_source"] = floor_source
    pool_data["calibrated"] = calibrated
    pool_data["assets"] = registered_assets

    # 写回前并发保护 (B1-r16)
    if pool_path.exists():
        if initial_mtime is None:
            raise SystemExit("FAIL pool.json 在本次执行期间被其他进程创建，放弃写入以防覆盖：请重跑命令")
        st = pool_path.stat()
        cur_sha = hashlib.sha256(pool_path.read_bytes()).hexdigest()
        if st.st_mtime != initial_mtime or cur_sha != initial_sha:
            raise SystemExit("FAIL pool.json 在本次执行期间被外部修改（mtime/sha 变更），放弃写入以防覆盖：请重跑命令")

    paths.atomic_write(
        pool_path,
        json.dumps(pool_data, ensure_ascii=False, indent=2),
    )
    print(f"[OK] 补料入库完成: {pool_name} (已登记 {len(registered_assets)} 个资产) → {pool_path}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="临时补料处理通道（Spec §4）")
    ap.add_argument("episode", type=Path, help="期目录，如 data/episodes/xxx")
    ap.add_argument("--floor", type=float, default=None, help="检索阈值 floor（主番未标定时必传）")
    ap.add_argument("--local", action="store_true", help="本地执行 VLM captions（不走云端）")
    ap.add_argument("--batch", type=int, default=vindex.CAPTION_BATCH)
    ap.add_argument("--confirm-cost", action="store_true")
    a = ap.parse_args()
    paths.require_data()
    return ingest(a.episode, floor=a.floor, local=a.local, batch=a.batch, confirm_cost=a.confirm_cost)


if __name__ == "__main__":
    raise SystemExit(main())
