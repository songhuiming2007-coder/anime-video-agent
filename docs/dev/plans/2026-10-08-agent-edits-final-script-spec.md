# agent 在人审批下改 02-script.md（D47）

日期：2026-10-08　状态：**已施工，机检与 e2e 通过，待真实一期手验**
关联：D47；ADR-0024 决策 1（本 spec 修订）；D44（草稿限次免卡）；`pipeline/approvals.py`（02.5 闸门）

## 起因

人原话：「应该给 agent tool 去修改 02，因为有的时候改动挺大的」「草稿变成 02 之后，一般可以让人和 agent 直接修改 02，这样效率高很多」。02.5 阶段人让 agent 改段落，agent 只能改 draft，再要人手工同步到 02-script.md；每次回复都附一段「我改不了 02-script.md」。

而且现行流程已经坏了：draft 是 02-diff.patch 的基线（「机器写的」），02-script.md 建好后 agent 还在改 draft，基线被挪动，patch 不再是「机器初稿 → 人改」。

## 原禁令的两个理由与替代

| 理由 | 替代 |
|---|---|
| 02.5 是人审闸门，机器不能代签定稿 | 闸门是 02.5 批准，不是文件作者：`approvals.py` 记 02-script.md 指纹，并要求 02-diff.patch 非空且新于 02-script.md。agent 写一次，patch 即过期，必须人重新封板、重新批准。另外每次写 02-script.md 都弹卡，卡上是与磁盘现版的 diff |
| 02-diff.patch 是「账号声音」标注数据（机器初稿 vs 人定稿） | agent 代笔会把机器措辞混进「人改」。每次写入前把旧版留底到 `_agent/script-history/`、不修剪，日后分析能把 agent 写入的那几步剔出来 |

## 规则

1. `write_episode_file` 白名单加 `02-script.md`。工具数不变（ADR-0025 不受影响）。
2. **只能改、不能新建**：02-script.md 不存在时拒。从草稿新建仍是人的动作（桌面端「从草稿新建」/ 终端 `cp`），进入 02.5 始终由人决定。
3. **02-script.md 存在后 draft 冻结**：再写 `02-script.draft.md` 一律拒，让 patch 基线保持机器初稿。
4. **每次写 02-script.md 都弹卡**，不进草稿的免卡档。写入卡（02-script.md 与 draft）显示与磁盘现版的 unified diff，超过 80 行截断并注明剩余行数。
5. 危险标记：已有 02-diff.patch →「改后封板失效，需重新封板」；已有 `03-audio/manifest.json` →「已配音，改动段落需 tts --redo」。只提示，不拒。
6. 写前留底 `_agent/script-history/<UTC 纳秒>-02-script.md`，全量保留。
7. 封板与 02.5 批准仍只能是人。

拒绝在弹卡之前给出（`review_tool_call`），`write_episode_file` 执行时再校验一遍。

## 桌面端连带修复

截图时发现请求卡在桌面端根本读不了 diff：`card_text` 的换行被吞成一段（Spec 10 §2.4 本就要求 `pre-wrap`，实现漏了），下面的参数表还把整篇 `args.content` 铺开。改为：正文 `<pre>` 按原样换行、等宽；参数原文收进默认收起的「参数原文」。仍是纯文本，不解释任何标记（H-3）。

diff 上下文取 2 行：稿件里段落标题与配音行隔一个空行，1 行上下文看不到改的是哪一段。

另：模型正文可能含「危险标记:」字样，`_tool_request` 取危险标记改为取第一行（真标记行总在正文之前；原先取最后一行，记忆卡全文早有同样的可冒充窗口）。

## 验证（已跑）

- pytest 全量通过；新增 8 条（只能改不能新建、全量留底不修剪、草稿冻结、卡上 diff 与段落标题、截断与剩余行数、封板 / 配音提示、写后 02.5 解封物过期、弹卡前拒、定稿每次弹卡、危险标记不可冒充）。`test_approvals::TC-11a` 白名单精确集合与 `verify_mutations` 的 M4 / MUT-11 锚点按新语义改；M4 护栏改为「只能改不能新建」，新增 D47-MUT-1~3。
- 手工变异 7 个全部被抓：白名单不加、不冻结草稿、定稿走免卡、卡里不带 diff、留底被修剪、危险标记取末行、diff 上下文退回 1 行。
- vitest 469 passed；e2e 全量 118 passed / 2 skipped，新增 TX-D47（卡片正文 `pre-wrap`、参数原文默认收起；把 `pre-wrap` 改回 `normal` 时该用例红）。

## 待人验（真实一期）

- [ ] 董香二期让 agent 改一段：卡上看到 diff 和「## 段落 N」，批准后 02-script.md 变、草稿不变
- [ ] `_agent/script-history/` 有改前版本
- [ ] 让它改草稿：直接被拒，提示改 02-script.md
- [ ] 已封板时卡上提示「改后封板失效」，改完状态显示需重新封板
