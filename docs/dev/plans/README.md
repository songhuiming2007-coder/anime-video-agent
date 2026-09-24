# Plans：可执行改进方案（施工图）

给执行代理（或未来会话）的**施工图**：目标、改动清单、验证命令、完成判定写死在文件里，
执行者只做清单内的事。不是 ADR（难以回头的决策，在 `docs/adr/`）、不是 issues（未解决
问题账本，在 `docs/issues/README.md`）。

> **查当前该做什么，先看下表。** 已归档方案见 [`archive/`](archive/)。

---

## 活跃方案

| 方案 | 对应 issues | 相关 ADR | 状态 |
|---|---|---|---|
| [P1：候选复核探针](2026-08-14-p1-recheck-probe.md) | B1 / D1 / D6 | ADR-0005, ADR-0003 | 工具就绪，待人工标注 + 复核会话 |
| [P2：段级时长不变量](2026-08-14-p2-artifact-invariant.md) | B4（段级不变量） | — | 已立项，未归档 |
| [P4：写稿链重构](2026-08-14-p4-script-pipeline.md) | B2 / D12 | — | E1–E6 已落地；B2 / D12 仍开 |
| [检索降级红旗](2026-08-21-检索降级红旗.md) | D2 / D7 | ADR-0005 | 未动工 |
| [v2 云端 GPU 重构](2026-09-08-v2-cloud-gpu-reconstruction.md) | B1, B2, D1, D2, D4, D5, D6, D22, D23, D25 | ADR-0014, ADR-0003, ADR-0005, ADR-0006 | **全局收束施工图**，M1/M2a/M2b/M2.5 已交付，进至 M3 |
| [v2 系统架构设计](2026-09-10-v2-architecture-design.md) | B1, B2, B3, D1, D2, D4, D5, D6, D22, D23, D24, D25 | ADR-0014, ADR-0003, ADR-0004, ADR-0005, ADR-0006, ADR-0008, ADR-0010~0013 | v2 架构落地层（7 大子系统设计），核心能力已在 M1~M2.5 交付落地 |
| [M2b 服务对象修正：番剧域 → 素材池](2026-09-11-m2b-pool-scope.md) | D4 / D5 / D6 / B1 | ADR-0015, ADR-0003, ADR-0008, ADR-0016 | **已执行并验收**（commit `b257e6d`，2026-09-11） |
| [ava-agent 架构设计与闭环控制](2026-09-18-ava-agent-harness.md) | B2 / B3 / D10 / D23 | ADR-0006, ADR-0014, ADR-0015, ADR-0017 | **已立项，待开工** |
| [ava 启动入口改造：idea scope](2026-09-21-ava-entry-idea-scope.md) | D27 | — | **已落地实施并全量验证通过（v1.2，D27 实施闭环，15 组变异全杀）** |
| [pi 侦察派工单（ava ↔ pi 交接协议）](2026-09-21-pi-scout-handoff-spec.md) | — | — | **已落地并验收（v0.4）** |
| [ava Harness 演进方向（上位需求）](2026-09-22-harness-evolution-direction.md) | B2 / D23 | ADR-0020~0023 | 上位需求文档，定义 8 份 Spec 施工序列；2026-09-23 补 §6 二期需求（Spec 9 无终端 agent 会话与 Session 恢复、Spec 10 桌面对话面板、Spec 11 停机点深度组件、Spec 12 封面标题协作；ADR-0024/0025 待立；一期完工后再写）与 **§7 一期施工 25 个 session（M1–M12 + 全局纠偏）及通用守则**；人时预算降为观测 |
| [上下文装配器与 AGENTS.md 瘦身（Spec 1）](2026-09-22-context-assembly-spec.md) | B2 | ADR-0022 | **M1 施工完成，S2 review 🟢**（PR0–PR2；AGENTS.md 126 行，十三判据完整；scope 热切换常驻层重组装；变异矩阵 4 项复核变红） |
| [jobs 层与 events.jsonl（Spec 2）](2026-09-22-jobs-and-events-spec.md) | B2 / D23 | ADR-0020 | **M3 施工完成（PR3–PR4），S6 review 🟢**（执行器置换、CLI 贯通、零回归、MUT-1~6 复核全杀） |
| [approval 对象化（Spec 3）](2026-09-22-approval-objectification-spec.md) | B2 / D23 | ADR-0020 | **M4、M5 施工完成（PR1–PR4），S8 / S10 review 🟢**（ack 表面、确认路径复核、一次进锁、`review.py` 期望指纹；MUT-1~28 全杀；门禁 11 真实期 REPL 手验待人执行） |
| [网络工具第一批 web_search + web_fetch（Spec 4）](2026-09-23-network-tools-spec.md) | B2 / D23 | ADR-0021 | **M6 施工完成（PR1–PR3），S12 review 🟢**（v0.4：工具表 6→8、scope 掩码、SSRF + fake-ip 段放行；MUT-1~23 全杀；`web_search` 默认 DDG 端点被机器人挑战页拦截，当前不可用，provider 留待后续 spec） |
| [网络工具第二批 crawl + browser（Spec 5）](2026-09-23-crawl-browser-tools-spec.md) | B2 / D23 | ADR-0021 | **M8 施工完成（PR1–PR3），S16 review 🟢**（工具表 9→11、能力掩码、独立 profile 守卫、启动事件落盘；S16 修复轮：死会话自愈、browser 后 crawl 事件循环冲突、不自建 data/、crawl4ai 基目录重定向 `data/crawl4ai/`（RF-15）；MUT-1~30 全杀；浏览器二进制与门禁 5 lsof 手验待人执行） |
| [acquire_propose 受控素材提案（Spec 6）](2026-09-23-acquire-propose-spec.md) | B2 / D23 | ADR-0021 | **M7 施工完成（PR1–PR2），S14 review 🟢**（工具表 8→9、asset scope 唯一可见；schema 纯函数与 incoming/ledger 路径单源下移 `pipeline/candidates.py`，acquire 零回归；MUT-1~13 全杀） |
| [跨期记忆 memory.md 与模型分层（Spec 7）](2026-09-23-memory-and-model-tiering-spec.md) | B2 | ADR-0023 | **M9 + M10 施工完成（PR1–PR6），S20 review 🟢**（装配器注入与开放写权限同 PR 落地，注入 scope = creative/asset/idea；`/memory` show/check/ack/digest，ack 仅交互终端；工具表 12/12，`write_memory` 仅 creative；MUT-1~53 全量实跑，S20 独立复跑 32 条；S20 修复轮：R7 断言收紧到冲突 id；门禁 8 跨档冒烟 2026-09-24 人执行通过，本机 models 段启用） |
| [Electron 桌面端架构冻结稿（Spec 8）](2026-09-23-electron-desktop-spec.md) | B2 / D23 | ADR-0020 | **v0.5 红队三轮收口 + 定向核对 🟢（4🔵 全收），PR0–PR3 可动工**（PR2 首日须实测 Electron 运行时的 JSON.parse `context.source`（假设 10）；PR4 阻塞于 Spec 3 v0.6 PR3 施工，而 Spec 3 §1.3–§1.6 的定向复核尚未进行，见 spec §1.4、§6.2） |

---

## 结构约定

| 项 | 规则 |
|---|---|
| 命名 | `YYYY-MM-DD-<slug>.md`，slug 用 kebab-case 英文 |
| 来源 | 正文第一节写明对应的 B/D/N 编号（`docs/issues/README.md`）与相关 ADR |
| 生命周期 | 完成判定全部满足后**移入 `archive/`，不删除**（历史在 git），对应 issues 条目同步归档 |
| 孤儿 | 超过 60 天没动工的方案：要么重写（现实变了），要么归档。不养尸 |

## 每份方案必含

1. 目标与对应 issue
2. 前置条件（环境、盘、基线）
3. 改动清单：文件 + 锚点（引原文片段，不给行号——行号会漂）
4. 新常量/新判据的取值理由（「为什么是这个数」，拍脑袋的数不许进代码）
5. 测试要求（期望值先在实现上跑通再写断言；写完做变异检验）
6. 验证命令
7. 明确不做的事（范围闸门）
8. 完成判定（可逐项打勾）

## 执行代理守则（每份方案默认生效，不重复写）

- 只做清单内的事。现状与方案描述对不上时**停下报告**，不即兴发挥、不改方案。
- 开工前先跑基线验证（`uv run pytest`），基线不绿不开工。
- 改完必须跑方案里的全部验证命令，贴结果。
- 工作区里别人改了一半的文件（`git status` 可见未提交改动）不许回退、不许覆盖，
  只做字符串级精确修改。
- 不主动 commit / push，除非用户明说。
