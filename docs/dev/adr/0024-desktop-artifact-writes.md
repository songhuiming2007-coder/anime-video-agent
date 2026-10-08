---
related-issues: D18, D19
related-plans: 2026-09-22-harness-evolution-direction, 2026-09-26-stop-point-deep-components-spec
status: accepted
---

# ADR-0024：桌面端写产物（app 内编辑与确定性人令的写路径）

日期：2026-09-26
状态：**已通过**（2026-09-26 用户接受）
前置：ADR-0018, ADR-0019, ADR-0020
依据：direction §6 Spec 11（2026-09-23 用户裁决「app 内置 Markdown 编辑器」，并明示「这是 UI 第一次拥有写产物的能力，须先立 ADR-0024」）；Spec 8 v0.5 的写不变量 I1/I2（`archive/2026-09-23-electron-desktop-spec.md` §2.3）；Spec 10 §6.1 S8-R1① 先例（I2 增「会话写入」类）

## 背景

Spec 8 把桌面端冻结为「只读的镜子加一组显式的按钮」：**I1** host 对 `data/` 零写入；**I2** 桌面端引发的 core 写入只有「自愈簿记」与「显式点击（ack）」两类。Spec 10 经 S8-R1①/R4 为会话与建期开了两个受控口子。

Spec 11 需要三类新写入，全部是人（不是模型）在停机点上的显式动作：

1. **02.5 改稿与封板**：编辑并保存 `02-script.md`、生成 `02-diff.patch`；
2. **03.5 顺听纠错**：回滚（恢复 attic 旧音频）、撤回（删除 corrections 条目）、done（触发 `tts --apply-patch` 增量重配）——即终端 `/voice` 六指令的按钮化；
3. **人时记录**：向 `human_time.json` 追加审阅耗时条目。

这些写入的共同性质：**触发源是人的显式点击，语义确定、无 LLM 参与**。它们与 Spec 8 既有的 ack 写入同类，只是对象从 approval 簿记扩展到期产物本体。

## 决策

### 1. 写路径只有一条：人令 → host spawn → core 裸形态子命令 → 白名单 + atomic_write

- UI（renderer/host）对 `data/` 的直写禁令 **I1 不变**；新增写入全部经 host spawn core 的**非交互裸形态子命令**完成（`ava <期> /save-script`、`/seal-script`、`/voice-revert` 等，全集见 Spec 11 §3.4）。
- core 侧落盘纪律与 `write_episode_file`（`pipeline/agent/tools.py:62-125`）同款：期目录必须在 `data/episodes` 之下（双端 resolve 防 symlink 穿透）、目标文件名闭集、落盘必须 `paths.atomic_write`（`paths.py:118-128`）。
- **不进 LLM 工具表**：这些子命令是 UI 的人令通道，不是模型工具。`write_episode_file` 的白名单（`{01-topic.md, 02-script.draft.md}`，tools.py:26-28）**不扩入** `02-script.md`——模型的写通道与人的写通道分开，前者保持现状（creative scope 只写草稿，落定为 `02-script.md` 永远是人的动作），工具表总数不变（红线 6 不受影响，无需 ADR-0025 口径调整）。
  - **2026-10-08 修订（D47，人裁决）**：白名单扩入 `02-script.md`，模型可在人审批下改定稿（只能改、不能新建；02-script.md 存在后 draft 冻结；每次写都弹卡并附 diff；写前留底 `_agent/script-history/`）。人审闸门由 02.5 批准与封板新鲜度承担，不靠「谁写的文件」；标注数据的出处由留底保住。详见 [D47 spec](../plans/2026-10-08-agent-edits-final-script-spec.md)。本 ADR 其余条目中「agent 永远不写 `02-script.md`」的表述按此修订理解；人的写通道（裸形态子命令）不变。

### 2. 正文经 stdin 传递，指纹不符即拒存

- 稿件正文经 **stdin** 传入子命令（`ava <期> /save-script --expect-size=<N> --expect-mtime-ns=<M>`），不进 argv（argv 在进程表可见且有长度上限；Spec 8 RF-19 已记录同类暴露）。
- 保存携带人载入该版本时的 (size, mtime_ns) 指纹；core 比对磁盘现状，**不符即退出 1、一字不写**（同 Spec 3 S3-R9 `--expect-*` 的语义先例）。**拒存而不是合并**：`02-script.md` 的字段是机器接口（`查询`/`锚点`/`人物`），自动合并 concurrent edits 超出本系统能力；last-writer-wins 会静默吞掉人的修改，违反「诚实失败优于凑合交付」。拒存把冲突变成可见、可恢复的事件（重新载入磁盘版本，改动由人重放）。
- 已知残余：指纹比对与 `os.replace` 之间存在微秒级窗口（现状对 `02-script.md` 本无任何锁纪律）。桌面单实例锁 + 人的编辑动作以分钟计，该窗口不可被正常操作触达；**agent 永远不写 `02-script.md`**（决策 1），竞争者只有「人在另一个编辑器里同时改」。

### 3. I1 不变，I2 重述（替代 Spec 8 §2.3 的 I2 清单）

- **I1（host 零写入）**：原文不变。host 进程自身对 `data/` 零写入、零 mkdir；新增子命令全部以 spawn 执行。
- **I2（桌面端引发的 core 写入闭集）**：在原两类上扩展「显式点击」类的清单——新增：`02-script.md`（保存/从草稿新建）；`02-diff.patch`（封板）；`03-audio/corrections.json`（落盘、撤回、done 的 applied 回写）；`03-audio/attic/**`（回滚的恢复读来源；done 经 `corrections.backup_segments` **新建**快照并修剪旧快照）；`03-audio/seg-*.wav` 与 `03-audio/manifest.json`（done 的增量重配重生成本体与清单）；`03-audio/.apply_patch.lock` 的创建与删除；`human_time.json`（人时条目）；及对应 `events.jsonl` 事件行（`job_*`、`human_time_recorded` 等）。「自愈簿记」类不变。闭集之外一律禁止；Spec 8 TI-3b 的清单相应扩展（修订请求见 Spec 11 §6.1）。（v0.3 补全：初版枚举遗漏 done 的直接产物，红队一轮 🟡-3 抓出、二轮 🟡-1 确认 v0.2 漏落后落入）
  - **2026-10-08 增补（D45 会话管理，人在桌面端点「删除会话」并过原生确认框）**：`session.jsonl` 的移出重写（持期租约，被删 sid 的整行搬走、其余行逐字节写回，经 `atomic_write`；`session_log.move_session_to_trash`）与 `_agent/session-trash/<sid>.jsonl`（回收站，新建不覆盖）。走同一条路径：人令 → host spawn core 裸形态 `/delete-session --sid=<sid>`；该期有活会话时 core 拿不到租约、退出 3，一字不写。会话列表 `/list-sessions` 只读。

### 4. 封板是 core 命令，不是 shell 重定向

`git diff --no-index 02-script.draft.md 02-script.md > 02-diff.patch` 的 shell 重定向形态无法被 `shell: false` 的 spawn 闭集表达。封板改为 core 子命令 `/seal-script`：core 以 `subprocess`（无 shell）执行同一条 `git diff --no-index`，捕获 stdout 后经 `atomic_write` 落盘，产物字节格式与终端手工封板一致（已实测：有差异时退出 1 且输出 unified diff；无差异时退出 0、输出为空 → 拒封，与 approvals 闸门「非空」判据一致，`approvals.py:330-331`）。

## 不做的事

- 不给 UI 开任意文件写能力；写入目标文件名是闭集，逐案进本 ADR 与 Spec 11。
- 不引入 CRDT/自动合并/版本历史；冲突就是拒存。
- 不改 `write_episode_file` 的 scope 语义，不为模型开 `02-script.md` 写权限。
- 不让 renderer/host 持有任何写 fd；预览读路径（`ava-media://`）不变。

## 推翻条件

- 若「指纹不符即拒存」在真实多窗口/多机工作流中误报率高到被无视（如人习惯性地在终端与 app 两边同时改稿），允许引入期级编辑锁或合并 UI，但拒存语义必须先经复盘证明不足。
- 若 core 子命令的 stdin 通道被证明不可维护（如需要流式大文件），允许改为「先写 userData 临时文件、core 命令读临时文件路径」，但临时文件必须留在 userData 子树（I1 不变），且同次调用内校验指纹。
