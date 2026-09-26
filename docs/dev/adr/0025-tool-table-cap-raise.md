---
related-issues: —
related-plans: 2026-09-22-harness-evolution-direction, 2026-09-26-cover-title-collaboration-spec
status: accepted
---

# ADR-0025：工具表封顶上调至 14（cover_edit 占用第 13 位）

日期：2026-09-26
状态：**已通过**（2026-09-26 用户接受；同步动作三条已随接受落盘：ADR-0021 口径、direction §4 红线 6、本状态行）
前置：ADR-0021（工具表封顶 ~12 的立法者）
依据：direction §6 Spec 12（2026-09-23 用户裁决「提高上限」；新上限数值与理由由本 ADR 论证）

## 背景

ADR-0021 给 LLM 工具表立了静态注册 + 数量封顶「~12 个（现有 6 + 网络 4 + acquire_propose + 预留 1）」。一期完工后工具表恰为 12 个（`tools.py:336` `TOOL_SCHEMAS` 实测：read_artifact、write_episode_file、list_episodes、read_status、run_pipeline、search_notes、web_search、web_fetch、acquire_propose、crawl、browser、write_memory），预留槽已被 `write_memory`（ADR-0023）占掉。

direction §6 Spec 12 需要一个确定性封面编辑工具（`cover_edit`：agent 只给文字/位置/字号/描边参数，Pillow 渲染），这是第 13 个工具，现行封顶放不下。用户已于 2026-09-23 裁决「提高上限」，数值与理由由本 ADR 论证。

## 决策

### 1. 封顶由 ~12 上调至 **14**

结构沿用 ADR-0021 的「现役 + 预留 1」形态：`cover_edit` 占第 13 位，第 14 位是新的预留槽。**任何第 15 个工具必须再立一份 ADR**——封顶的意义不是数字本身，而是「每个工具的进出都要过一次 ADR 级别的论证」，这条不变。

### 2. 为什么是 14 而不是别的数

- **不是 13（恰好够用）**：去掉预留槽等于默认「下一个工具不需要 ADR 论证就有位」，ADR-0021 的立法意图（预留 1 槽，用完即须复议）会被静默消解。
- **不是 16 或「取消封顶」**：封顶防的是工具表蔓延——schema 全量进系统提示（token 成本与模型选择困难随数量增长），且每个工具都是新的攻击面与维护面。二期四份 spec 的工具需求已可穷尽枚举：Spec 9/10/11 全部走裸形态子命令（人令通道，不进 LLM 工具表，Spec 11 §2.1 已冻结此分工），LLM 工具净增只有 `cover_edit` 一个。留 1 槽后仍无余粮，正好维持「再要就立 ADR」的压力。
- **为什么 `cover_edit` 必须是 LLM 工具而不是别的形态**：
  - **不是裸形态子命令**：裸形态是人令通道（UI 按钮 → host spawn），agent 无法经它发起调用；而封面编辑的发起者恰恰是 agent（人提要求，agent 出参数）。
  - **不是 `PIPELINE_MODULES` 白名单模块**：该白名单的 scope 语义是「制片期可执行的 pipeline 工序」，`cover_edit` 只对 creative scope 可见（pipeline scope 永不见它是 scope 分组授权的既有纪律）；且 `run_pipeline` 的参数是自由命令串，校验力弱于工具 JSON schema（每个字段的取值域可在 schema 层钉死）。
- **scope 归属**：`cover_edit` 仅 creative scope 可见（`config/agent/tools.json` 的 creative 清单增至 11），asset/pipeline/idea 不见。

### 3. 接受时的同步动作（一次性，随接受落盘）

1. ADR-0021「不做的事」第一条「数量封顶在 ~12 个（现有 6 + 网络 4 + acquire_propose + 预留 1）」修订为「数量封顶在 14 个（ADR-0025 上调；现役 13 + 预留 1，第 15 个须再立 ADR）」；
2. direction §4 红线 6「工具表封顶 ~12 个」修订为「工具表封顶 14 个（ADR-0025）；新增工具必须有 ADR 编号」；
3. 本 ADR 状态转「已通过」。

## 不做的事

- 不取消封顶、不设「软上限」；数字的意义就是被撞到时必须停下来论证。
- 不为「以后可能要的工具」预留超过 1 槽。
- 不改动任何既有工具的语义与 scope 归属。

## 推翻条件

- 若第 14 槽被占且仍有新工具需求，推翻本 ADR 时须同时回答「scope 分组（mask-don't-remove）是否已让单会话可见工具数足够小」——若各 scope 可见工具都 ≤ 8，可论证按 scope 分别设封顶替代全局封顶。
- 若实践证明「每工具一份 ADR」的论证成本高于其防蔓延收益（如连续三份 ADR 都是例行上调且无实质讨论），允许改为「工具表变更须过红队评审」的等效门禁，但封顶数字本身不得因此取消。
