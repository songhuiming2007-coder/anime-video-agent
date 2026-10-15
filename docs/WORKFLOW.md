# 每期作业流程 (WORKFLOW)

产物即状态，落盘于 `data/episodes/<期号>/`。无数据库与外部队列，任何步骤中断，解决后从断点继续跑。

> **开工第一指令（自然语言优先）**：主入口是桌面端 AVA（侧栏选期 / 选题，直接说白话）；终端等价入口如下。
> **命令由 agent 发，先问再发**（D59、D92）：流水线命令都由 agent 经 `run_pipeline` 发，人不在终端手敲。有副作用的命令 agent 先在对话里问、人同意再发；人的话已经说清要做什么（「开始渲染吧」）就直接发。真正弹卡的只剩云端计费、写 `config/` 的四条例外、越过人工关卡的选项与开工单。停机点的批准仍只能由人做。下文的 `python -m …` 只作底层排查参考。
> ```bash
> ava <期号>                                      # 宿主入口：与 AI 制片总监连续对话（ava idea 选题发散）
> ava <期号> --continue [<会话号前缀>]             # 接着本期上一段会话继续（ava <期号> --sessions 列出全部会话）
> python -m pipeline.status data/episodes/<期号>  # 底层排查参考：同一份状态卡，无宿主包装
> ```
> 1. **直接说白话**：`ava <期>` 进当期对话；`ava idea` 进无期选题会话；`ava new <名>` 建期并进对话。
> 2. **快捷键零 token**：以 `/` 开头的快捷键（`/status` `/voice` `/run` 等）全部保留可用，毫秒级响应且零 token。
> 3. **副作用先问**：改文件、跑流水线前 agent 先在对话里说清要做什么、影响哪些段，人同意再做（ADR-0030 起大多不弹卡；仍弹卡的见上一条，默认 N，须显式批准）。
> 4. **四大停机点不变**：02.5 人审改稿、03.5 配音顺听、05 审时间码、09 标题封面拍板仍须人类闭环，严禁越权串联跳过。
> 一次性 `ava <期> /run <命令>` 视为人手敲确认直接执行；`--force`/`--force-all` 在 ava 层直接拒收并指引 `--redo` / `--apply-patch`。
> 5. **撞到引擎缺陷开工单**（D82）：确信不是当期输入写错时，agent 经 `run_pipeline tickets open` 开单（一律弹卡），人把卡上「复制给 coding agent」的交接文本交给 coding agent；修完重开会话会提示从哪一步续跑。

---

## 一、四阶段工序卡与人工停机点（严禁一键盲目串联）

- **单一事实来源（SSOT）**：解说词、微单元分镜、音乐试听段（`## 音乐段`）、音画同源开关，**只写在 `02-script.md` 里**。不另维护外置文件（两份会分叉）；中间草稿隔离，生产目录只留唯一生效文件。
- **离散工序**：流水线分四个阶段，到停机点停下交人：
  - **工序 A【脚本与分镜】**：agent 维护 `02-script.md` ──→ 跑 `check_script` 全绿 ──→ 🛑 **【停机点 1】**：人类阅读审稿确认（02.5），批准即封板！
  - **工序 B【配音与顺听】**：agent 提议 `tts`（本地）或云端：人租机贴登录指令 → `cloud connect` → `cloud up` → `cloud push` → `cloud run <期> tts` → `cloud pull` → `cloud down`（connect、up、run、down 弹卡）──→ 🛑 **【停机点 2】**：顺听（03.5）打点，错字处置走顺听面板 / `/voice` → `corrections.json` → `--apply-patch`，定死时长！
  - **工序 C【排片与审片】**：agent 跑 `clips`、`review` 产出 `04-review.html` ──→ 🛑 **【停机点 3】**：桌面端预览区审看（05），人在决策条批准，产出 `04-clips.approved.json`！
  - **工序 D【确定性渲染与质检】**：读取 `approved.json` ──→ agent 跑 `render` 与 `qc`，一趟出片！

> **阶段 0 前置（每部番一次，不在 01–09 里）**：素材入库与全季打标。
> 全季 `vindex captions` 要上行几百 MB 帧，而远端 `data/` 在**系统盘**
> （`config/cloud.json` 的 `remote_data` 有意未接线）——开工前先改道数据盘：
> agent 提议 `cloud relocate-data`（弹卡，一次性、幂等：已是软链就直接报 OK；远端有任务在跑就拒；
> 数据盘上已有 `data/` 就拒、交人看），它把远端 `data/` 挪到 `/root/autodl-tmp/data` 并软链回来，打印系统盘余量。
> `cloud push` 自带余量闸：系统盘不够会当场拒上行并给出修法（`cloud clean-frames` 清上一轮打标帧，
> 或 `cloud relocate-data`），不会传到一半才爆。单集补料切片（~14MB/集）量级可忽略，但改道仍建议先做好。
> 详见 [`dev/postmortems/workflow-history.md`](dev/postmortems/workflow-history.md)「云端中间物」。

| 工序阶段 | 包含步骤 | agent 发的命令（有副作用的先问人；终端 REPL 里人亲手敲 `/run` 等价） | 底层排查参考 | 🛑 人工停机点与硬门禁 |
|---|---|---|---|---|
| **A. 脚本与分镜** | 01 选题 → 02 写稿 | 写稿（选题会话 / 期会话里说白话）<br>`check_script`<br>`adversarial`（02.8，每期必跑） | `python -m pipeline.check_script <期>/02-script.md` | **02.5 人审改稿**：人精修事实与张力，批准时自动封板（`02-diff.patch`），严禁跳过。 |
| **B. 配音与顺听** | 03 语音合成 | `tts`<br>`corrections add` / `corrections global`（全局表弹卡）→ `tts --apply-patch` | `python -m pipeline.tts <期>` | **03.5 配音顺听**：顺听面板（终端备用 /voice）纠错（corrections.json 永久资产，--apply-patch 靶向重配）。 |
| **C. 排片与审片** | 04 画面排片 → 05 审时间码 | `clips`、`review` | `python -m pipeline.clips <期>`<br>`python -m pipeline.review <期>` | **05 审时间码**：预览区看 `04-review.html` 确认无画外音错配，人在决策条批准（agent 不能 approve）。无 approved 文件渲染器拒绝启动。 |
| **D. 渲染与发布** | 06 渲染 → 07 质检 → 08 封面标题 → 09 发布 | `render`、`qc`、`cover` | `python -m pipeline.render <期>`<br>`python -m pipeline.qc <期>`<br>`python -m pipeline.cover <期>` | **09 标题与封面拍板**：Agent 仅出 5 条标题候选与封面池，**严禁自行定稿**，必须由人类挑选并手动上传。 |
| **补料通道**（不固定在哪一步，所有题材；企划期常要补好几轮） | 缺画面 → 补料入池 → 写锚点 / 重排 | 检索（`images` 直链）→ 候选列给人、人同意 → `acquire_propose`（直接下载）→ `acquire register --to-patch` → `ingest_patch`（写稿期只为锚点加 `--anchor-only`）→ `clips`（见 runbook 02 第 2.5 步、04.5） | `python -m pipeline.ingest_patch <期>` | 缺口段带「必审」标进 05，不得绕过审片。 |

---

## 二、标准执行速查（01 – 09 步）

1. **[01 选题](runbook/01-topic.md)**（人）：填 `01-topic.md`（番、类型、锚点、张力）。张力是整条流水线唯一编辑判断，定死后不许 agent 篡改。
2. **[02 写稿](runbook/02-script.md)**（Agent）：先给人设计稿（核心立意、大概写什么、多长），人点头再写；先读番剧笔记，按 `skills/write-script` 写 `02-script.draft.md`，自己跑 `check_script` 到全绿，再跑 02.8 `adversarial`（每期必跑）。
3. **[02.5 人审](runbook/02.5-human-review.md)**（人）：逐段挑刺、事实核查、人自己改稿、结构改动（拆段、补素材、多锚点、试看段）；让 agent 改时写前留底。批准 02.5 时自动封板生成 `02-diff.patch`（也可在 app 内 02.5 编辑器点「封板」）。
4. **[03 配音](runbook/03-tts.md)**（Agent/机器）：agent 先问本地还是云端、再跑 `tts`（云端开机 / 跑任务 / 关机是计费卡）。
   - **红线**：此后一律只补点名段，**严禁擅自 `--force` 全量重配**（ava 层直接拒收该旗标并指引 `--redo`）；错字走 `g2p.py` 注入，换引擎前必须报备影响段数。
5. **[03.5 顺听](runbook/03.5-voice-check.md)**（人）：桌面端在预览区 `03-audio` 的顺听面板（终端备用 `ava <期> /voice`）顺听 + 纠错（三项抽检为主，corrections.json / --apply-patch）。听出文案错就改稿、只重配点名的段，重配哪几段由人定。
6. **[04 排片](runbook/04-clips.md)**（Agent/机器）：agent 跑 `clips`，全局贪心分派。通道互斥（锚点直通 / 台词 / 画面 VLM），不跨通道比分。审看页发现画面问题改锚点重排，不动配音。**普通期的 BGM 在这时由人点名**（配音出来之后才听得出该配什么），agent 写进 `01-topic.md` 与稿件的音乐段（`skills/write-script/SKILL.md` 8.3）。
7. **[05 审片](runbook/05-timecode.md)**（人）：预览区看 `04-review.html`，通过后在决策条批准 05（终端 `/approve 05`），产出 `04-clips.approved.json`。
8. **[06 渲染](runbook/06-render.md)**（Agent/机器）：agent 跑 `render`，产出 `05-final.mp4`（强制双重切片校验、字幕折行、BGM侧链闪避）。
9. **[07 质检](runbook/07-qc.md)**（机器）：agent 跑 `qc`，11 项机器硬门禁全绿（音画同步、黑帧、静音等），产出 `06-check.log`。
10. **[08 封面与标题](runbook/08-cover-title.md)**（Agent/机器）：agent 跑 `cover`。产出候选池与 `07-titles.md`（5 条候选）。人自备图可在 app 内导入 `07-cover/import/`（或 `cat <图> | ava <期> /import-cover --name=<文件名>`）。叠字：先和人商量写什么字，再由 agent 调 `cover_edit` 直接出图、自己先看过再交人。横版 16:9、竖版 9:14 两版都要（竖版由裁剪或 AI 扩图得到）；简介统一一份。
11. **[09 发布](runbook/09-publish.md)**（人）：人选定稿封面图与标题，手动上传各平台。第 09 停机点的批准须携带封面路径与标题原文（app 内两个输入；终端 `/approve 09 --id <id> --cover <路径> --title <标题>`），core 落定稿记录供事后审计；发布动作仍在 ava 之外。

> **不固定的两件事**：补素材随时可能发生（写稿、改稿、排片、审片）；会话也不固定，一期可以一个会话从头到尾，也可以分几个会话。企划（多卷系列）多出来的做法见 `config/agent/scopes/project.md`「企划实际怎么做」。

> agent 发的命令、终端 `ava <期> /run X` 与 `python -m pipeline.X <期>` 三者等价：宿主只多做白名单校验与审批卡。
> 日常不需要人敲任何一种；要复现问题、排查时才用 `python -m` 形式。

---

## 三、四大物理红线（触犯即故障）

1. **配音只有首次是全量**：后续改动一律走点名重配（`/voice` 纠错 → `--apply-patch` 或 `--redo`），严禁 `--force` 或擅自换引擎洗掉全量音频。ava 宿主层已直接拒收 `--force`/`--force-all` 并给出增量指引。
2. **时间码未经 `--approve` 绝不渲染**：渲染脚本强制校验 `04-clips.approved.json`，缺失立即终止。
3. **标题与封面文案由人拍板**：Agent 只能出候选并标明取舍代价，不得自行将候选填为定稿。
4. **素材边界严格以集数对齐为准**：七条索引数字不相等时，绝不启动排片与渲染。

> 历史踩坑论证、开发演进与全量详注见开发态归档 [`docs/dev/postmortems/workflow-history.md`](dev/postmortems/workflow-history.md)。
