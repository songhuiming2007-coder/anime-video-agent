# Plans：可执行改进方案（施工图）

给执行代理（或未来会话）的**施工图**：目标、改动清单、验证命令、完成判定写死在文件里，
执行者只做清单内的事。不是 ADR（难以回头的决策，在 `docs/dev/adr/`）、不是 issues（未解决
问题账本，在 `docs/dev/issues/README.md`）。

> **查当前该做什么，先看下表。** 已归档方案见 [`archive/`](archive/)。

---

## 活跃方案

| 方案 | 对应 issues | 相关 ADR | 状态 |
|---|---|---|---|
| [P1：候选复核探针](2026-08-14-p1-recheck-probe.md) | D1（B1 已由 M2b 收口、D6 已撤销） | ADR-0005, ADR-0003 | 工具就绪，待人工标注 + 复核会话 |
| [P4：写稿链重构](2026-08-14-p4-script-pipeline.md) | B2 / D12 | — | E1–E6 已落地；B2 / D12 仍开 |
| [检索降级红旗](2026-08-21-检索降级红旗.md) | D2（D7 已收口） | ADR-0005 | 未动工 |
| [v2 云端 GPU 重构](2026-09-08-v2-cloud-gpu-reconstruction.md) | B1, B2, D1, D2, D4, D5, D6, D22, D23, D25 | ADR-0014, ADR-0003, ADR-0005, ADR-0006 | **全局收束施工图**，M1/M2a/M2b/M2.5 已交付，进至 M3 |
| [v2 系统架构设计](2026-09-10-v2-architecture-design.md) | B1, B2, B3, D1, D2, D4, D5, D6, D22, D23, D24, D25 | ADR-0014, ADR-0003, ADR-0004, ADR-0005, ADR-0006, ADR-0008, ADR-0010~0013 | v2 架构落地层（7 大子系统设计），核心能力已在 M1~M2.5 交付落地 |
| [M2b 服务对象修正：番剧域 → 素材池](2026-09-11-m2b-pool-scope.md) | D4 / D5 / D6 / B1 | ADR-0015, ADR-0003, ADR-0008, ADR-0016 | **已执行并验收**（commit `b257e6d`，2026-09-11） |
| [ava 启动入口改造：idea scope](2026-09-21-ava-entry-idea-scope.md) | D27 | — | **已落地实施并全量验证通过（v1.2，D27 实施闭环，15 组变异全杀）** |
| [pi 侦察派工单（ava ↔ pi 交接协议）](2026-09-21-pi-scout-handoff-spec.md) | — | — | **已落地并验收（v0.4）** |
| [ava Harness 演进方向（上位需求）](2026-09-22-harness-evolution-direction.md) | B2 / D23 | ADR-0020~0025 | 上位需求文档；一期 8 份 spec（2026-09-25 S25）与**二期 Spec 9–14（2026-09-29 S21）均已完工并归档**，§6 二期需求标注完成；后续独立立项见 issues D29 / D30 |
| [Spec 15：web_search provider 可插拔（Exa 主 + Tavily 备）](2026-09-29-web-search-provider-spec.md) | D29 | ADR-0021 | **v0.1 草案，待人审 → 红队 → 施工**（选型已拍板；§11 五问待确认；PR0 需人注册 Tavily key） |

## 已归档：二期六份 spec（2026-09-29 S21 收尾）

原文（含红队裁决纪要长表、变异矩阵与实跑回填）全部在 [`archive/`](archive/)，活跃文档不复述。「一票否决信息」＝§9 里仍未验的门禁，逐份指针如下：

| 方案 | 状态 | 未验门禁 / 遗留（指针） |
|---|---|---|
| [Spec 9：无终端 agent 会话协议与 Session 恢复（core）](archive/2026-09-25-agent-session-protocol-spec.md) | 已施工 | 无（§9 门禁 1–14 全勾；S9-MUT-1~62 全 KILLED） |
| [Spec 10：桌面端对话面板与人审卡片](archive/2026-09-25-desktop-conversation-panel-spec.md) | 已施工 | 门禁 6 / 9 / 12（打包版·真机手验，待人）；遗留 D37（确认框定性中，安全相关）、N39（待拍板） |
| [Spec 11：停机点深度组件](archive/2026-09-26-stop-point-deep-components-spec.md) | 已施工 | 门禁 9 的打包版手验与 A2 |
| [Spec 12：09 封面与标题协作](archive/2026-09-26-cover-title-collaboration-spec.md) | 已施工 | 门禁 9 的「两图渲染两版」与打包版手验 |
| [Spec 13：web_fetch 链接清单与研究策略](archive/2026-09-26-web-fetch-links-and-research-strategy-spec.md) | 已施工 | **门禁 8（唯一终判）待 D29 收口后复跑** |
| [Spec 14：桌面端视觉设计系统](archive/2026-09-26-desktop-visual-design-spec.md) | 已施工 | 门禁 6 / 7（待人）、门禁 8 的 M31（不可复现） |

> 2026-09-25 文档同步归档 4 份（完成判定已满足，移入 [`archive/`](archive/)）：P2 段级时长不变量（`pipeline/align.py` refit + review/render 双卡口已落地）、M2b 素材池修正（b257e6d 已验收）、ava idea scope 启动入口（D27 闭环）、pi 侦察派工单（v0.4 已验收）。B4 仅为 P2 内部编号，issues 表无对应行，无需同步。

---

## 结构约定

| 项 | 规则 |
|---|---|
| 命名 | `YYYY-MM-DD-<slug>.md`，slug 用 kebab-case 英文 |
| 来源 | 正文第一节写明对应的 B/D/N 编号（`docs/dev/issues/README.md`）与相关 ADR |
| 生命周期 | 完成判定全部满足后**移入 `archive/`，不删除**（历史在 git），对应 issues 条目同步归档 |
| 孤儿 | 超过 60 天没动工的方案：要么重写（现实变了），要么归档。不养尸 |
| 附件 | 放在与方案同名的目录 `<方案文件名去掉 .md>/` 下，只许纯文本，随方案一起归档；附件内脚本的路径一律取参数，不写死 |

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
