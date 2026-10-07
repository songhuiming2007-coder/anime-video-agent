---
related-issues: D43, D42
related-plans: 2026-10-06-all-tools-all-scopes-spec
status: accepted
---

# ADR-0027：工具不按模式（scope）授权，所有模式开放全部工具

日期：2026-10-06
状态：**已通过**（2026-10-06 人裁决方向并确认两点：模式检查一起去掉、人审卡照旧；同日 Spec 17 §8 Q1–Q3 人裁决全部按建议；同日红队一轮 D43-R 🟡；2026-10-07 人裁决 🟡-1 选 (A)，Spec 17 v0.2 作者修订，D43-R2 定向复审 🟢；D43-B 2026-10-08 施工（`45fc26b` / `e03e373`）；2026-10-08 D43-C 独立评审通过，本 ADR 转「已通过」）
前置：ADR-0021（网络工具内化，scope 过滤）、ADR-0023（跨期记忆，`write_memory` 只挂 creative）、ADR-0025（工具表封顶 14，`cover_edit` 只对 creative 可见）、Spec 10 §2.5（idea 会话零写权限）

## 背景

到 2026-10-06，LLM 工具表共 13 个，按模式分四份清单授权（creative 11、pipeline 4、asset 5、idea 4），并在三处工具实现里再按模式拒绝一次（`write_episode_file`、`write_memory` 只许 creative；`run_pipeline` 按模式分两份命令白名单）。

## 决策

人于 2026-10-06 裁决：**不论什么模式，都开放所有工具**（人原话：「我建议不论什么模式，都开放所有 tool，具体原因我先不展开，这是我深思后的考虑」）。同日确认：工具实现里的模式检查一起去掉；人审卡全部照旧。

1. 模式只决定注入哪份提示与工序手册，不参与任何工具放行判断；`tools.json` 收为单表（Spec 17 §3.1，§8 Q1 人已裁决）。
2. 删除：调用前的「越 scope」拒绝；`write_episode_file` 与 `write_memory` 的 creative-only 检查；`run_pipeline` 的按模式分表（合为一表）。
3. 保留（与模式无关，一条不动）：写入文件白名单与路径防穿透、写 `01-topic.md` 必须人确认、`CRITICAL_TOOLS` 人审卡、`browser` 原生确认框、出网断言与 URL 守卫、清洗与脱敏、`cloud exec` 与 `--force` 禁令、`run_pipeline` 执行须人在宿主确认、Code Freeze。
4. 需要期目录的工具（`write_episode_file`、`cover_edit`、`run_pipeline`、`acquire_propose`）在无期会话（idea）里统一报「先建期」，不藏工具；拦截在弹卡之前（Spec 17 §3.4）。

## 理由

动机由人保留，本 ADR 不代写。机制上的依据只有一条：上述「保留」的护栏全部不依赖模式，删掉模式授权后，凭据出网、写源码、越界写、未经人批准执行这几类后果仍各有一道与模式无关的防线（逐条对照见 Spec 17 §3.3、§5）。

## 取代与修订

- 取代 ADR-0021「网络工具只对 asset / creative 可见；pipeline 永不见网络工具」与「scope 过滤」；
- 取代 ADR-0025 中 `cover_edit` 的 scope 归属与「不改动任何既有工具的 scope 归属」；封顶数字 14 不变；
- 修订 ADR-0023 中「idea 零写权限（机制保证），写入工具仍只挂在 creative」整行（记忆注入范围不变）；
- 修订 Spec 10 §2.5 idea「零写权限」为「无期目录，写期文件前须先建期」。
具体修订面见 Spec 17 §7。

## 放弃的方案

- **只改 `tools.json`、保留实现内检查**：模型会拿到 `PermissionError` 白白浪费轮次；人已明确否决。
- **四个模式各留一份相同清单**：改动小，但四份配置会再次各自漂移；Spec 17 §8 Q1 人已选单表。

## 推翻条件

- 施工后观察到模型因工具全量可见而系统性选错工具、且影响产出（Spec 17 §5 R6），可重新引入按模式分组，但须另立 ADR，并说明为何不能只靠提示解决。
