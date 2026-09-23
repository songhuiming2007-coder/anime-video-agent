# Runbook: 04 素材检索与排片

依据配音的真实物理时长，进行三通道素材匹配与全局贪心分派。

## 执行命令
```bash
python -m pipeline.clips data/episodes/<期号>
```

## 产物与位置
- `data/episodes/<期号>/04-clips.json`（片段计划）
- `data/episodes/<期号>/04-review.html`（抽帧对照审片页）

## 核心规程与铁律
1. **三通道互斥**：
   - 锚点直通通道（`锚点:`）：绝对优先，直通镜头表时间码；
   - 台词语义通道（`查询:`）：台词向量检索，可选加 `人物:` 开启在场过滤；
   - 画面 VLM 通道（`场景:`）：文-文意象检索。
   - 一段只走一个通道，严禁跨通道比分。
2. **全局贪心分派**：所有候选按分数排序占坑，整期成片同一镜头绝不重复使用。
3. **检索落空走阶梯，不降级塞空镜**：`查询` → `备选` → `配音原文`。三级皆落空则硬报错，绝不自动插入无意义空镜掩盖画文不符。

## 04.5 临时补料挂起与恢复流程

排片审片（`04-review.html`）发现局部镜头缺料、画文不符或短缺（short / no_source）时，无需中断全番或重走全量 Phase 0。排片落空时，优先在交互 REPL 内运行 `/scout`（或命令行 `python -m pipeline.scout <期>`）生成派工单交由 pi 采掘，通过临时补料通道热插拔解决：

1. **准备补料素材**：
   - 将视频素材放入 `data/episodes/<期号>/patch_assets/`；
   - 资产限制：仅支持视频文件（mp4/mkv 等）。若有静态图片/截图，必须先用 ffmpeg 转为带时长的视频：
     ```bash
     ffmpeg -loop 1 -t 8 -i in.jpg -pix_fmt yuv420p out.mp4
     ```
2. **轻量增量入库**：
   ```bash
   python -m pipeline.ingest_patch data/episodes/<期号>
   ```
   - 自动在当期生成补丁池（池名按期名净化：`re.sub(r'[^A-Za-z0-9_-]+', '-', episode.name) + "-patch"`），资产编号编为 `SP01…SP99`；
   - 进行秒级切镜头、关键帧抽取、密集意象打标与向量化，产物写入当期 `04-patch/`，绝不污染全局 `data/library/`。
3. **重新排片与自动救援**：
   ```bash
   python -m pipeline.clips data/episodes/<期号>
   ```
   - clips 自动检测并加载当期补丁池；
   - **rescue-A（检索失败救援）**：主池检索落空的段落，以「场景 > 查询 > 配音原文」自动在补丁池中进行意象检索补位；
   - **rescue-B（分派失败救援）**：对 `no_source` 段落重置并在补丁池中重新分配；对 `short` 段落保留已选主池高质量片段，仅在尾部用补丁镜头安全拼接延长，绝不暴力抹除主池结果。

## 05 人审改稿写补丁锚点规范

当人类审片（`04-review.html`）发现需要手动精准指定补丁池中的特定镜头时，走「改稿 → 重跑 check_script → 重跑 clips」的闭环：

1. **先出补丁画廊再写锚点**（`shots.gallery` 已全部参数化可直接复用，B8-r5）：
   手写锚点前人需要先看镜头联系表，不然锚点直通的工作流不闭环：
   ```bash
   python -c "from pathlib import Path; from pipeline.shots import gallery; gallery('<净化后池名>', 'SP01', Path('data/episodes/<期号>/04-patch/shots'), Path('data/episodes/<期号>/04-patch/frames'))"
   ```
   双击打开生成的 HTML 画廊，看图选镜头，一键复制时间码。

2. **在 02-script.md 中书写补丁锚点**：
   - 格式：`锚点: <净化后池名> SPxx mm:ss`；
   - 示例：`锚点: EGOIST--patch SP03 1:20`（期目录名如「EGOIST-三期」经净化后可能含双横线 `EGOIST--patch`，手写请以 `04-patch/pool.json` 里的 `pool` 字段为准，示例比正则更直观，B2）。

3. **改稿校验与重排闭环**：
   ```bash
   python -m pipeline.check_script data/episodes/<期号>/02-script.md
   python -m pipeline.clips data/episodes/<期号>
   ```
   - `compute_script_vo_hash` 只提取 `配音：`行（B6），修改画面行（`锚点:`/`查询:`/`场景:`）不改变配音哈希，已合成音频 100% 免重跑；
   - 与 `03-audio/corrections.json` 沉淀互不干扰，无需通过 `--apply-patch` 重新配音；
   - 重跑 clips 后，补丁锚点直接生效直通出片。

## 04.6 SP 素材基础规则
- **异构素材与跨番支持**：除标准番剧 `SxxEyy` 外，原生支持特典集 `SPxx`（`season=None`，素材池 MV/Live/物证等）；支持跨番前缀 `锚点: [番名] SxxEyy 12:30` 或 `[企划名] SPxx mm:ss` 及多锚点蒙太奇。
- **确定性直通排片**：SP 素材无字幕索引，**严禁写查询/人物/场景，必须走确定性锚点直通**；素材自然时长不够填满口播时，系统自动尾帧安全定格延展（`ok_extended`，上限 8.0s，超额诚实判 short）。
- **审帧与音轨隔离**：无字幕素材禁止人工拖进度条，跑 `shots frames` 与 `shots gallery` 选锚点；切片默认强制 `-an` 剔除原生音轨；质检对带 `sp: true` 片段放宽至 1.5s 艺术暗场豁免。
