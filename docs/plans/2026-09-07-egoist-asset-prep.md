# EGOIST 企划非常规素材预处理 · 落地实施方案（v2，用户修订版）

> 对应：ADR-0010（决策二 SP 收编）、ADR-0011（SP 集号落地）、issues N27（阈值标定纪律）。
> 状态：v2 按用户意见修订（M1 改前置 symlink、M2 改扁平键），待批准后动工。
> 完成判定见末节，全部满足后移入 `archive/`。

## 目标

两条战线入库 30 个资产，全程零人肉重命名、phase0 零改动：
- 《PSYCHO-PASS》S1：22 话正片（VCB MKV）+ 22 个诸神双语 ASS（跨目录、异前缀）；
- EGOIST SP01–SP08：3 MV + 2 Live + 3 微动（零字幕，ADR-0010 决策二口径）。

## 用户修订记录（v1 → v2）

1. **M1 否决**：`--subs-dir/--sub-pattern` 进 phase0 属过度设计（字幕组命名千差万别，
   正则死磕进核心引擎只增维护负担）。改为**前置 symlink 对齐**，核心引擎零改动、
   零测试负担，磁盘零损耗，契合「前置清洗数据，流水线只吃标准格式」。
2. **M2 改扁平键**：`"EGOIST/SP04": 25.0`，回退 `"EGOIST": 10.0`；拒绝嵌套 dict，
   不碰 `visual.scene_threshold` 的既有结构假定，老番零影响。
3. **SP 阈值分工**：SP01–03 / SP06–08 用 EGOIST 默认 10.0（用户拍板，微动零切点
   与阈值无关）；SP04/SP05 先 `calibrate` 人看表拍板，再写扁平键、再 build。

## 改动清单（机制部分）

### M1 · `tools/link_subs.py`（新增脚本，pipeline/ 零改动）

把「跨目录、异前缀」的字幕按集号对齐成视频同目录的规范名软链：

```
<V 目录>/<视频主名>.Chs&Jap.ass  →  <S 目录>/实际字幕.ass
```

- 双侧各一个正则取集号：视频侧默认 `\[(?P<episode>\d{2}|OVA)\]`（VCB 通例直接中），
  字幕侧默认 `#(?P<episode>\d{2})`（诸神通例），都可 CLI 覆盖；
- **默认 dry-run** 打印「集号 → 视频 ↔ 字幕」配对表，人眼核对了再 `--apply`；
  幂等（已存在且指向同一目标的链接跳过），配对任何一侧集号缺失/重复就拒做该集
  并列入报告——软链错了 phase0 不会报错，错的代价在这里拦；
- 链接名带 `.Chs&Jap.ass` 后缀，直接命中现有 `SUB_GLOB`；`_find_sub` 的
  同目录+同主名规则原样生效。诸神 ASS 的 JP/CN 双轨无需解析改动（verify 按假名
  取日文行、subindex 按 CJK 取中文行，均与 Style 标记无关，已读源码确认）；
- 自检：dry-run 输出即人工核对面；配对逻辑（双正则 → 对齐表）抽成纯函数
  `_pair(videos, subs, v_pat, s_pat)`，附 `python tools/link_subs.py --selftest`
  的内联断言自检（临时目录造假文件跑通配对/缺号/重号三分支）。

### M2 · `shots.threshold` 支持扁平集键覆盖

`pipeline/shots.py`：

```python
def threshold(anime: str, key: str | None = None) -> float:
    table = paths.conf("visual.scene_threshold")
    t = table.get(f"{anime}/{key}") if (isinstance(table, dict) and key) else None
    if t is None:
        t = table.get(anime) if isinstance(table, dict) else None
    ...（缺配置照旧 FAIL，文案补一句「SP 等单素材覆盖写 "<番>/<集键>"」）
```

- 调用点四处传集键：`build`（season/episode → `_key`，含 `--sp` 的 SP 键）、
  `meta`、`load`、`rebuild`；标量值的老番配置逐字节不受影响；
- `config/project.json`：`visual.scene_threshold` 新增 `"PSYCHO-PASS"`（标定后填）
  与 `"EGOIST": 10.0`（用户拍板；SP04/SP05 的 `"EGOIST/SP04"` `"EGOIST/SP05"`
  扁平键在 calibrate 拍板后补写），`_scene_threshold_note` 追加实录；
- `min_shot` 维持全局不动。

### M3 · 测试

- `test_shots.py`：扁平键覆盖命中、无覆盖回退番键、标量旧配置零漂移、
  `load` 对账按覆盖值放行/拦截；
- 全量回归 772+N 全绿才收工；
- 数据操作不进 pytest（V1–V5 命令块验证）。

## 执行步骤（数据部分，按序执行）

```bash
T7="/Volumes/Samsung T7/anime-video-data"
ls "$T7"   # 探活：挂不上就停

# ---- 战场 A · PSYCHO-PASS S1 ----
# A0. 同卷 rename 迁移正式库（零拷贝），字幕收 subs/ 子目录
mkdir -p "$T7/sources/PSYCHO-PASS/subs"
mv "$T7/待入库/[VCB-Studio] PSYCHO-PASS/"*.mkv "$T7/sources/PSYCHO-PASS/"
mv "$T7/待入库/PSYCHO-PASS_ASS/"*.ass "$T7/sources/PSYCHO-PASS/subs/"

# A1. 前置对齐：先 dry-run 核对配对表，再落地（人工检查点①）
uv run python tools/link_subs.py "$T7/sources/PSYCHO-PASS" "$T7/sources/PSYCHO-PASS/subs"
uv run python tools/link_subs.py "$T7/sources/PSYCHO-PASS" "$T7/sources/PSYCHO-PASS/subs" --apply

# A2. 逐番阈值标定（人工检查点②：看密度表拍板，写入 config "PSYCHO-PASS" 键）
uv run python -m pipeline.shots calibrate \
  "$T7/sources/PSYCHO-PASS/[VCB-Studio] PSYCHO-PASS [01][Hi10p_1080p][x264_flac].mkv"

# A3. phase0 整季入库（verify 验轴 → 建索引 → 登记；已索引自动 SKIP，可断点续跑）
uv run python -m pipeline.ingest phase0 "$T7/sources/PSYCHO-PASS/"*.mkv \
  --anime PSYCHO-PASS --season 1

# A4. 22 集镜头切分（集号从文件名取，零手工编号）
for v in "$T7/sources/PSYCHO-PASS/"*.mkv; do
  ep=$(sed -E 's/.*\[([0-9]{2})\].*/\1/' <<< "$(basename "$v")")
  uv run python -m pipeline.shots build "$v" --anime PSYCHO-PASS --season 1 --episode "$((10#$ep))"
done

# ---- 战场 B · SP01–SP08 ----
E="$T7/sources/EGOIST"
# B1. 登记（集号从文件名取；intact 逐文件过）
for v in "$E/"SP0*.mp4; do
  n=$(sed -E 's/SP0*([0-9]+)\.mp4/\1/' <<< "$(basename "$v")")
  uv run python -m pipeline.ingest sources "$v" --anime EGOIST --sp "$n"
done

# B2. config 写入 "EGOIST": 10.0（用户拍板值），先 build MV/微动六件
for n in 1 2 3 6 7 8; do
  uv run python -m pipeline.shots build "$E/SP0$n.mp4" --anime EGOIST --sp "$n"
done

# B3. Live 标定（人工检查点③：密度表+抽检联系表，判据=碎镜头趋零、
#     中位时长落到舞台镜头常态 3–8s；过切无害、漏切有害，拿不准往低取）
uv run python -m pipeline.shots calibrate "$E/SP04.mp4" --sheet <候选值>
uv run python -m pipeline.shots calibrate "$E/SP05.mp4" --sheet <候选值>
#     拍板后写 "EGOIST/SP04" / "EGOIST/SP05" 扁平键，再：
uv run python -m pipeline.shots build "$E/SP04.mp4" --anime EGOIST --sp 4
uv run python -m pipeline.shots build "$E/SP05.mp4" --anime EGOIST --sp 5
```

## 验证（全部可复现）

```bash
# V1. 机制回归：全量测试全绿
uv run pytest -q

# V2. 登记面：8 键齐全、时长与实测一致
uv run python - <<'EOF'
from pipeline.ingest import load_sources
src = load_sources("EGOIST")
assert sorted(k for k in src if k.startswith("SP")) == \
    [f"SP0{i}" for i in range(1, 9)], src.keys()
print("OK EGOIST SP01–08：", {k: round(v["duration"], 1) for k, v in src.items()})
EOF

# V3. 切分面：八张表全过对账（load 自带阈值一致性校验），微动必须唯一全局镜头
uv run python - <<'EOF'
from pipeline.shots import load
for i in range(1, 9):
    d = load("EGOIST", f"SP0{i}")
    n = len(d["shots"])
    if i >= 6:
        assert n == 1 and d["shots"][0]["start"] == 0.0, d["shots"][:3]
    print(f"OK SP0{i}: {n} 镜头（阈值 {d['meta']['scene_threshold']}）")
EOF

# V4. 排片端到端吸附：临时稿件走真 run()，8 段锚点各归各的片源
uv run python - <<'EOF'
import json, tempfile
from pathlib import Path
from pipeline import clips
ep = Path(tempfile.mkdtemp()) / "ep"
(ep / "03-audio").mkdir(parents=True)
(ep / "01-topic.md").write_text("番: EGOIST\n", encoding="utf-8")
(ep / "02-script.md").write_text("\n".join(
    f"## 段落 {i}\n\n配音：第{i}段。\n\n画面：\n  锚点: EGOIST SP0{i} 00:02\n"
    for i in range(1, 9)), encoding="utf-8")
(ep / "03-audio/manifest.json").write_text(json.dumps({"segments": [
    {"index": i, "label": str(i), "text": "x", "file": f"s{i}.wav",
     "duration": 3.0, "cer": 0.0, "attempts": 1} for i in range(1, 9)]}),
    encoding="utf-8")
data = json.loads(clips.run(ep).read_text(encoding="utf-8"))
for s in data["segments"]:
    assert s["status"] == "ok", (s["index"], s["status"])
    print(f"OK 段{s['index']}: {s['clips'][0]['source'].split('/')[-1]} "
          f"@{s['clips'][0]['start']}s 共{s['clips'][0]['dur']}s")
EOF

# V5. PSYCHO-PASS 冒烟：检索命中 + 锚点吸附
uv run python -m pipeline.check_script <任一引用 PP S01 集号与锚点的草稿>
```

## 完成判定

- [ ] M2 进代码，`tools/link_subs.py` 落地，全量测试 772+N 全绿；
- [ ] sources.json 出现 `PSYCHO-PASS` S01E01–E22 与 `EGOIST` SP01–SP08；
- [ ] `data/library/index` 出现 PSYCHO-PASS 22 集检索单元（verify 全过）；
- [ ] `data/library/shots/` 出现 PSYCHO-PASS_S01E01–22 与 EGOIST_SP01–08 镜头表，
      SP06–08 各为唯一全局镜头；
- [ ] config 新增键带实录注释（PSYCHO-PASS 与 SP04/SP05 为 calibrate 拍板值，
      EGOIST=10.0 标注用户拍板）；
- [ ] V1–V5 全部输出 OK。

## 风险与对策

| 风险 | 对策 |
|---|---|
| 诸神 ASS 与 VCB 版轴有固定偏移（片头卡差异） | verify 的对照组判据专为轴偏移设计；真偏移逐集 FAIL 并带采样明细，不静默 |
| 软链配对对错集 | link_subs 默认 dry-run 人工核对；任一侧集号缺失/重复拒做该集 |
| SP04/SP05 爆闪在 scdet 上降不动 | 候选切点已存，`rebuild` 免重解码试多档；再不行人工圈时段 |
| phase0 跑到一半中断 | 已索引的集自动 SKIP，重跑同一条命令断点续跑 |
| T7 未挂载 | A0 之前 `ls "$T7"` 探活，挂不上就停 |
