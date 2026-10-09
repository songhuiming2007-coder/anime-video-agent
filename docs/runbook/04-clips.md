# Runbook: 04 素材检索与排片

依据配音的真实物理时长，进行三通道素材匹配与全局贪心分派。

## 执行
agent 经 `run_pipeline` 跑 `clips`（弹卡，期目录自动补上）。底层排查参考：`python -m pipeline.clips data/episodes/<期号>`。

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

排片审片（`04-review.html`）发现局部镜头缺料、画文不符或短缺（short / no_source）时，不必中断全番或重走 Phase 0：走期内临时补料通道。**全程 agent 跑，人只批卡、只做取舍**（2026-10-09 D59；以下命令都是 agent 经 `run_pipeline` 发的，期目录自动补上）：

1. **看缺口**：`scout`（弹卡，写 `scout-ticket-patch.md`）列出可救的段和缺什么画面。不可救的段（锚点重叠、缺口太短）按它给的指引改稿。
2. **找素材**：agent 自己检索（`web_search` → `web_fetch` → `crawl` → `browser`），按 `skills/acquire-assets/SKILL.md` 写候选、调 `acquire_propose`。人在提议卡和逐条抓取卡上批。ava 三级工具都查不到时，才把 `scout` 工单交给外部 agent（ADR-0021 §4 的升级通道）。
3. **进补丁池**：抓下来的文件先过 `acquire gate`（免卡），再 `acquire register <文件> --to-patch <期目录>`（弹卡）挪进本期 `patch_assets/`。图片照收，下一步会自动转成 6 s 微动视频（原图保留）。
4. **轻量增量入库**：`ingest_patch`（弹卡）。
   - 自动在当期生成补丁池（池名按期名净化：`re.sub(r'[^A-Za-z0-9_-]+', '-', episode.name) + "-patch"`），资产编号 `SP01…SP99`；
   - 秒级切镜头、抽帧、密集意象打标（云端 VLM，实例没开时 agent 先提议 `cloud up`，计费卡）与向量化，产物写入当期 `04-patch/`，不污染全局 `data/library/`；
   - 补丁池损坏或半建：`ingest_patch --reset`（弹卡），旧 `04-patch/` 挪进 `04-patch.attic/<时间>/` 后重建，不删。
5. **重新排片与自动救援**：`clips`（弹卡）。
   - clips 自动检测并加载当期补丁池；
   - **rescue-A（检索失败救援）**：主池检索落空的段落，以「场景 > 查询 > 配音原文」自动在补丁池中进行意象检索补位；
   - **rescue-B（分派失败救援）**：对 `no_source` 段落重置并在补丁池中重新分配；对 `short` 段落保留已选主池高质量片段，仅在尾部用补丁镜头安全拼接延长，绝不暴力抹除主池结果。

## 05 人审改稿写补丁锚点规范

当人审片（`04-review.html`）想精确指定补丁池里的某个镜头时，走「出画廊 → 人指镜头 → agent 改稿 → 重跑」的闭环：

1. **出补丁画廊**：agent 跑 `shots gallery --patch <期目录> SPxx`（弹卡），产物 `04-patch/shots/<池名>_SPxx_gallery.html`，在桌面端预览区打开。画廊在期目录下，预览区不放行脚本，「复制锚点」按钮点不了——人直接告诉 agent「用 #12」或报时间码即可。
2. **agent 写补丁锚点**：在 `02-script.md` 里写 `锚点: <池名> SPxx mm:ss`（写稿卡带 diff，人批），示例：`锚点: EGOIST--patch SP03 1:20`。池名以 `04-patch/pool.json` 的 `pool` 字段为准（期目录「EGOIST-三期」净化后含双横线 `EGOIST--patch`；不带方括号，B2）。
3. **改稿校验与重排**：agent 跑 `check_script`（免卡）→ `clips`（弹卡）。
   - `compute_script_vo_hash` 只提取 `配音：` 行（B6），改画面行（`锚点:` / `查询:` / `场景:`）不改配音哈希，已合成音频免重跑；
   - 与 `03-audio/corrections.json` 互不干扰，不用 `--apply-patch` 重配；
   - 重跑 clips 后补丁锚点直接生效。

## 04.6 SP 素材基础规则
- **异构素材与跨番支持**：除标准番剧 `SxxEyy` 外，原生支持特典集 `SPxx`（`season=None`，素材池 MV/Live/物证等）；支持跨番前缀 `锚点: [番名] SxxEyy 12:30` 或 `[企划名] SPxx mm:ss` 及多锚点蒙太奇。
- **确定性直通排片**：SP 素材无字幕索引，**严禁写查询/人物/场景，必须走确定性锚点直通**；素材自然时长不够填满口播时，系统自动尾帧安全定格延展（`ok_extended`，上限 8.0s，超额诚实判 short）。
- **审帧与音轨隔离**：无字幕素材禁止人工拖进度条，由 agent 跑 `shots frames` 与 `shots gallery` 出画廊、人看图指镜头；切片默认强制 `-an` 剔除原生音轨；质检对带 `sp: true` 片段放宽至 1.5s 艺术暗场豁免。
