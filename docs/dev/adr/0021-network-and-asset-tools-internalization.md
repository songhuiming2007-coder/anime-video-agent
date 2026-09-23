---
related-issues: B2, D23
related-plans: 2026-09-21-pi-scout-handoff-spec, 2026-09-18-ava-agent-harness
status: accepted
---

# ADR-0021：网络与素材工具内化，scout 派工降级为升级通道

日期：2026-09-22
状态：**已通过**
前置：ADR-0018, ADR-0020
依据：Hermes 工具系统调研（2026-09-22）、STANDARD.md 五节反爬升级链（2026-09-03 立）、scout-handoff 八会话施工复盘

## 背景

ADR-0018 冻结的 6 工具表 + scout-handoff 派工单模式，在实践中暴露结构性矛盾：

1. **升级链的规矩在 ava，工具在 pi**。STANDARD.md 五节立的反爬升级链（静态获取 → `agent_crawl` 无头绕盾 → `agent_browser` 登录态）是 ava 的生产纪律，但两个工具本体都是 pi 环境的设施。ava 立了自己执行不了的规矩，只能把整条链以工单形式外包——每次缺料都要人工搬运工单、开外部会话、等回接，直接吃人时预算。
2. **上网检索、爬取、素材搜集是 ava 的核心业务能力，不是边缘需求**。02 写稿考据、Phase 0 素材扩充、04 排片落空补料，三条主干工序全部依赖它。核心能力外包意味着 ava 的能力闭环断裂。
3. **ADR-0020 的 Electron 路线使矛盾尖锐化**：外包给 pi 的动作不产生 ava 的事件流，桌面端 UI 无从呈现「agent 正在检索/正在绕盾/正在下载」。

Hermes 调研给出可抄的现成范式：toolsets 分组授权（webhook 面只暴露 4 个只读工具）、危险工具独立 approval 门、浏览器工具独立持久化 profile。

## 决策

### 1. 网络工具三级链内化进 ava 仓库，与 STANDARD.md 已立升级链一一对应

| 工具 | 对应升级链 | 职责（单一，禁止瑞士军刀） |
|---|---|---|
| `web_search` | —（探测） | 关键词探测，返回标题/URL/摘要，只读 |
| `web_fetch` | 第一级：静态获取 | 单 URL 静态抓取净文，只读 |
| `crawl` | 第二级：`agent_crawl` | 无头抓取，可开 stealth 绕盾，只读 |
| `browser` | 第三级：`agent_browser` | 登录态/交互式浏览器，**approval 门**，独立持久化 profile |

纪律全部沿用 STANDARD.md 既有条款：逐级升级不跳级、严禁失败静默降级为水百科、browser 严禁直连主力 Chrome Default 配置（独立 profile，防 SingletonLock 与 Keychain 阻断）。

### 2. Scope 分组授权（借 Hermes toolsets 模式）

- **网络工具只对 asset scope 与 creative scope 可见**；pipeline scope（渲染/质检等确定性工序）维持只读本地产物，永不见网络工具。
- `browser` 是全表最重工具：execution 前必须过 approval 门（借 Hermes 审批分层：确定性规则，不引入 guardian LLM），且 profile 路径写死在 config、不接受运行时参数覆盖。
- 出网边界沿用 `assert_egress_boundary`（ADR-0018 §2.5），四个新工具全部纳入同一边界检查；密钥/登录态 cookie 属出网敏感物，沿用 RESTRICTED_EGRESS_PATTERNS 机制。

### 3. 素材获取走既有三层分工，人审闸门原样保留

不新造素材工具，只把 agent 侧接口补齐：

- agent 新增 `acquire_propose`（受控写入 `data/library/incoming/candidates.json`，白名单文件，同 `write_episode_file` 的纪律：双端 resolve 防穿透、atomic_write）；
- **人审闸门不变**：candidates.json 必须人逐条批准后才允许 fetch（版权与带宽风险由人的显式动作承担，acquire-assets skill 已立）；
- fetch/gate/register 走现有 `pipeline.acquire` 白名单命令，不新增 LLM 工具。

### 4. scout 派工单降级为升级通道，不废弃

- 常规检索/抓取/补料由 ava 内部工具闭环完成，不再派工；
- 工单保留用于真正需要外部 agent 的重活（批量采掘、需要 pi 侧重资产环境的任务）；
- 判据：**ava 内部工具链三级全部失败或明确超出能力边界时才允许生成工单**——工单从「唯一通道」变为「诚实失败后的显式升级」，与 STANDARD.md 五节「诚实失败优于凑合交付」一致。

### 5. 依赖与实施顺序

- `web_search`/`web_fetch` 保持零重依赖（stdlib + 既有出网路径）；
- `crawl` 的重依赖（Crawl4AI/Camoufox 级）做成 **uv 可选 extras**，未安装时工具在 schema 层隐藏（不注册进工具表），而不是运行时 import 报错；
- 顺序：`web_search` + `web_fetch` → `crawl` → `browser` →（随 ADR-0020 桌面端）BrowserView 嵌入呈现 browser 工具的可见窗口。

## 不做的事

- 不引入通用插件/MCP 体系；工具表保持静态注册、scope 过滤，数量封顶在 ~12 个（现有 6 + 网络 4 + acquire_propose + 预留 1）。
- 不做自动批量 fetch：下载动作永远先过人审闸门，agent 无批量下载工具。
- 不把 pi 的重资产环境（模型、片源库）复制进 ava——那部分仍是工单存在的理由。
- 不修改 6 个既有工具的语义；本条是纯增量。

## 推翻条件

- 若 `crawl`/`browser` 的依赖使 ava 安装体积或维护成本失控（如 Camoufox 二进制更新频繁断裂），允许将第二、三级重新外置为独立 sidecar 进程，ava 经白名单命令调用——但工具 schema 与 scope 授权层保留在 ava 内，UI 事件流不断裂。
- 若人审闸门在真实生产中证明是瓶颈（积压超过人时预算），允许对**白名单站点 + 已有 `why` 充分性机检**的候选开放限额的自动 fetch，但每次开放必须单独立 ADR。
