# 每期作业流程 (WORKFLOW)

产物即状态，落盘于 `data/episodes/<期号>/`。无数据库与外部队列，任何步骤中断，解决后从断点继续跑。

> **开工第一指令**：任何 Agent 开始做视频任务，第一步必须先运行状态诊断：
> ```bash
> python -m pipeline.status data/episodes/<期号>
> ```
> 严格按诊断输出的当前阶段与停机卡点执行，严禁越过未批准节点盲目向下游串联。

---

## 一、四阶段工序卡与人工停机点（严禁一键盲目串联）

| 工序阶段 | 包含步骤 | 核心命令 | 🛑 人工停机点与硬门禁 |
|---|---|---|---|
| **A. 脚本与分镜** | 01 选题 → 02 写稿 | `python -m pipeline.check_script <期>/02-script.md` | **02.5 人审改稿**：人工精修事实与张力，产出 `02-diff.patch` 后封板，严禁跳过。 |
| **B. 配音与顺听** | 03 语音合成 | `python -m pipeline.tts <期>` | **03.5 配音顺听**：人耳抽检开头与最长段音频，确认无漏读发飘。 |
| **C. 排片与审片** | 04 画面排片 → 05 审时间码 | `python -m pipeline.clips <期>`<br>`python -m pipeline.review <期>` | **05 审时间码**：浏览器打开 `04-review.html` 确认无画外音错配，显式执行 `--approve`。无此文件渲染器拒绝启动。 |
| **D. 渲染与发布** | 06 渲染 → 07 质检 → 08 封面标题 → 09 发布 | `python -m pipeline.render <期>`<br>`python -m pipeline.qc <期>`<br>`python -m pipeline.cover <期>` | **09 标题与封面拍板**：Agent 仅出 5 条标题候选与封面池，**严禁自行定稿**，必须由人类挑选并手动上传。 |

---

## 二、标准执行速查（01 – 09 步）

1. **[01 选题](runbook/01-topic.md)**（人）：填 `01-topic.md`（番、类型、锚点、张力）。张力是整条流水线唯一编辑判断，定死后不许 agent 篡改。
2. **[02 写稿](runbook/02-script.md)**（Agent）：调 `skills/write-script` 写 `02-script.md`。跑 `check_script` 机检 23 项全绿。
3. **[02.5 人审](runbook/02.5-human-review.md)**（人）：改稿并在当期目录生成 `02-diff.patch`（`git diff --no-index 02-script.draft.md 02-script.md > 02-diff.patch`）。
4. **[03 配音](runbook/03-tts.md)**（Agent/机器）：跑 `python -m pipeline.tts <期>`。
   - **红线**：此后一律只补点名段，**严禁擅自 `--force` 全量重配**；错字走 `g2p.py` 注入，换引擎前必须报备影响段数。
5. **[03.5 顺听](runbook/03.5-voice-check.md)**（人）：人耳抽检确认语速与音色。
6. **[04 排片](runbook/04-clips.md)**（Agent/机器）：跑 `python -m pipeline.clips <期>`，全局贪心分派。通道互斥（锚点直通 / 台词 / 画面 VLM），不跨通道比分。
7. **[05 审片](runbook/05-timecode.md)**（人）：浏览器看 `04-review.html`，通过后执行 `python -m pipeline.review <期> --approve` 产出 `04-clips.approved.json`。
8. **[06 渲染](runbook/06-render.md)**（Agent/机器）：跑 `python -m pipeline.render <期>`，产出 `05-final.mp4`（强制双重切片校验、字幕折行、BGM侧链闪避）。
9. **[07 质检](runbook/07-qc.md)**（机器）：跑 `python -m pipeline.qc <期>`，11 项机器硬门禁全绿（音画同步、黑帧、静音等），产出 `06-check.log`。
10. **[08 封面与标题](runbook/08-cover-title.md)**（Agent/机器）：跑 `python -m pipeline.cover <期>`。产出候选池与 `07-titles.md`（5 条候选）。
11. **[09 发布](runbook/09-publish.md)**（人）：人选定稿封面图与标题，手动上传各平台。

---

## 三、四大物理红线（触犯即故障）

1. **配音只有首次是全量**：后续改动只删改对应 `seg-XX.wav`，严禁 `--force` 或擅自换引擎洗掉全量音频。
2. **时间码未经 `--approve` 绝不渲染**：渲染脚本强制校验 `04-clips.approved.json`，缺失立即终止。
3. **标题与封面文案由人拍板**：Agent 只能出候选并标明取舍代价，不得自行将候选填为定稿。
4. **素材边界严格以集数对齐为准**：七条索引数字不相等时，绝不启动排片与渲染。

> 历史踩坑论证、开发演进与全量详注见开发态归档 [`docs/dev/postmortems/workflow-history.md`](dev/postmortems/workflow-history.md)。
