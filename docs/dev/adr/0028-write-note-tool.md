---
related-issues: D61, D59
related-plans: 2026-10-09-calibration-and-notes-spec
status: accepted
---

# ADR-0028：第 14 个工具 `write_note`——ava 写番剧笔记

日期：2026-10-09
状态：**已通过**（2026-10-09 人裁决「ava 写番剧笔记：必须要」）
前置：ADR-0025（工具封顶 14，第 14 槽预留）、ADR-0021 §4（scout 工单降级为升级通道）、D47 / D48（写稿卡 diff 与 edits）

## 背景

番剧笔记（`data/library/notes/<番>.md`）是全部期共用的剧情事实来源。ava 没有写 `data/library/` 的工具，缺笔记或笔记要改时只能 `scout --type notes` 出工单，由人搬给 pi——D59「人只批卡不敲命令」收口后剩下的最大一处人搬运。

## 决策

占用 ADR-0025 预留的第 14 槽，新增 `write_note`：

- 目标用 schema 钉死：`notes`（`<番>.md`）与 `review`（已存在的 `<番>-对抗审查报告*.md`，只用来填终审表）；番名只许安全字符，新建须已登记。
- 每次写入弹人审卡、卡上带 diff；覆盖前留底到 `notes/_history/<番>/`。
- 不需要期目录：笔记按番不按期，选题会话也要能补笔记。

不扩 `write_episode_file`：它是需期工具、语义是期文件；让它的 `filename` 接受 `notes/` 前缀会把两套边界搅在一起。

## 后果

- 工具表到顶（14）。**第 15 个工具必须再立 ADR。**
- 笔记三步流水线的执行者由 pi 子代理改为 ava：写厚（单独会话）→ `notes_review`（零上下文对抗审查）→ ava 终审回写。
