---
related-issues: B2, B3, D10, D23
related-plans: 2026-09-18-ava-agent-harness, 2026-09-18-ava-agent-impl-spec
status: accepted
---

# ADR-0018：ava 统一 CLI 入口与三条 Harness 护栏

日期：2026-09-18
状态：**已通过**
前置：ADR-0006, ADR-0014, ADR-0015, ADR-0017

## 背景

当前系统由一组离散的 pipeline 脚本组成。在写稿端切换至 Gemini 之后，02.5 改稿瓶颈已消除，但人类用户在调度各个工序时存在大量摩擦。若引入外部 Agent 框架（如 LangChain、CrewAI）会带来沉重黑盒与过度抽象；若完全由 LLM 自由调用 shell，则存在擅自改代码、全量重配废弃人工审听、擅自定稿标题封面等严重合规风险。

因此需要构建纯 stdlib/原生 Python 实现的 `ava` 统一 CLI Agent Harness，并在调度层设立硬性护栏。

## 决策

1. **统一 CLI 宿主与 Scope 隔离**：
   - 统一入口 `ava [期号]`，通过文件产物状态自动推导并路由至对应 Scope（Asset / Creative / Pipeline），根目录提供交互看板与 `ava new <期号>`；
   - 绝不引入任何第三方 Agent 框架，不搞 review.html/Webview GUI，保持纯 CLI 与 QuickTime/afplay 系统原生工具顺听。

2. **三条核心 Harness 护栏**：
   - **生产期代码写保护（Code Freeze）**：进入制片会话时检测 Git 状态，工作区或引擎有改动则 WARN；严禁在制片会话中修改通用 pipeline 代码（引擎修改必须走物理隔离的独立 worktree 会话）；
   - **配音纪律双层闸**：ava 永不生成 `--force` 或 `--force-all`；重配只允许走 `--redo` 或 `--apply-patch`。该规则在两层严格执行：① ava /run 白名单硬拒 `--force`；② cloud `extra_args` 白名单硬拒 `--force`；
   - **封面与标题只出候选**：标题与封面文案必须由人类用户拍板，LLM 工具表与 REPL 中绝不设立「定稿」动作，严禁 Agent 自行定稿。

## 推翻条件

若 Code Freeze 的 git 判据在真实工作流中误报率高到被无视（如经常因非关键文件触碰导致误拦截或提示疲劳），则推翻当前 git 状态判据，改为基于 Git worktree 的物理环境绝对隔离 + CI 独立检查。
