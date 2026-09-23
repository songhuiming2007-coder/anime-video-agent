---
related-issues: B2, D23
related-plans: 2026-09-18-ava-agent-harness
status: accepted
---

# ADR-0020：Harness 事件化演进与 Electron 桌面端

日期：2026-09-22
状态：**已通过**
前置：ADR-0018, ADR-0014, ADR-0016
依据：Hermes Agent 源码（github.com/NousResearch/hermes-agent）、Manus 官方工程博客与泄露 prompt（2025-03 版，注明时效）、ZCode 开源仓库（github.com/zai-org/ZCode，v3.14.0，2026-09-20 开源）三份调研（2026-09-22，调研记录见会话存档）

## 背景

ADR-0018 交付的 ava CLI harness 是「白名单执行器」：LLM 在 scope 约束内调用流水线，人工停机点靠用户显式输命令推进。实践后确认两个结构性缺陷：

1. **无事件层**：每个流水线步骤是同步 subprocess，输出进终端即挥发。任何 UI（哪怕是只读看板）都必须重新发明状态采集，与 `pipeline.status` 形成双头。
2. **停机点不是对象**：02.5/03.5/05/09 只是 REPL 里的标签，不是可挂起、可恢复、可被任一界面 ack 的一等状态。

同时用户明确决策：最终形态是 **Electron 桌面应用**（明确排除 Web UI 作为终态）。ADR-0018 的「不搞 review.html/Webview GUI」禁令是为防止当时 scope 蔓延的局部决策，不是对 GUI 的永久否决——当时的问题（GUI 绕开 CLI 状态）恰恰要靠本条的事件层解决。

调研结论（三条最重要的外部证据）：

- **Hermes**：多 surface 共享 core 的机制是「所有进程读写同一状态存储」+ 事件 sidecar（失败静默、主循环永不因 UI 通道阻塞）；其 SQLite state.db 的 30 版 schema 迁移链与十余个 repair 模块，反向证明了数据库维护税——**文件落盘 + JSONL 事件日志在 ava 的规模上是对的，不引入数据库**。
- **Manus**（官方博客，2025-07）：文件系统即上下文、append-only 上下文、保留错误 observation、mask-don't-remove 状态机、ask/notify 消息分级——与 ava 的「产物即状态」「人工停机点」高度同构。
- **ZCode**（开源代码，v3.14.0）：Electron 41 + React 19，main + host（utilityProcess）+ renderer 三进程；自研 RPC（Channel 抽象，本地 MessagePort / 远程 WebSocket 双传输共用协议）；事件流 snapshot+delta+coalesce；权限确认协议化（options + freeText 拒绝回喂 + 项目级/会话级授权规则）；**其 v2.x「套壳聚合多家 CLI」路线在 v3.0 被自研单一协议彻底替换——证明协议必须自有，UI 只能投影自有协议**。

## 决策

### 1. 解除并澄清 ADR-0018 的 GUI 禁令

ADR-0018「不搞 review.html/Webview GUI」解除，替换为本条第 4 节的 Electron 决策。**保留不变的**：不引入第三方 Agent 框架、Code Freeze、配音双层闸、封面标题只出候选。现有 `04-review.html`、`shots gallery` 静态页维持到桌面端 v1 上线后退役。

### 2. Harness 增加 jobs 层与 events.jsonl（先于一切 UI）

- 每个流水线步骤执行包装为 **job**：有 id、命令、开始/结束时间、退出码、状态机（`pending → running → succeeded | failed | blocked`）。
- 执行过程 emit 结构化事件，追加写入 `data/episodes/<期号>/events.jsonl`（不进 git，与期产物同级）。**append-only、确定性序列化**（Manus KV-cache 原则同样适用于事件日志：可重放、可审计）。
- 事件 sidecar 纪律（借 Hermes `event_publisher`）：事件写入/分发失败完全静默，主流水线永不因事件通道阻塞；消费者（CLI 看板、未来桌面端）满则丢、死则短路。
- **状态真相源不变**：`pipeline.status` 仍从产物文件推导，events.jsonl 是历史与观测层，不参与状态推导。不搞双头。

### 3. 停机点升格为 approval 对象

- 推进到人工停机点时，core 挂起并落盘 **pending approval 对象**（类型、期号、关联产物路径、可选项），状态机 `pending → approved | rejected(feedback) | superseded`。
- ack 是显式动作（沿用 ADR 六节「05 的 --approve 必须是显式动作」的精神），任何 surface（CLI、未来桌面端）都可 ack，ack 本身也落盘为事件。
- **拒绝可携带结构化反馈**（借 ZCode 的 freeText deny 回喂）：05 打回时写「哪段、什么问题」，回写进该期产物供 agent 修正——把「驳回」从口头变成数据。
- 写产物状态保持单线程（Cognition/Anthropic 共同结论：并行写冲突是多 agent 主要失败源）。只允许 read-only 子 agent（素材检索、对抗审查——对抗审查本来就该是干净上下文，ADR 判据 11 已独立确立此点）。

### 4. 桌面端：Electron，不自研 UI 协议

- **形态**：Electron + React。进程模型借 ZCode：main（窗口/系统）+ host utilityProcess（agent 会话、流水线 job 执行）+ renderer（纯 UI）——业务不跑主进程，崩溃隔离。
- **通信**：不引入外部 RPC 框架。renderer 与 host 之间用 Electron MessagePort/IPC，事件流直接镜像 events.jsonl 的增量（tail 式订阅 + snapshot 首载）。**ava core 保持纯 Python、无 server 依赖**：host 进程 spawn `ava`/pipeline 子进程并解析事件，不反过来改造 core 去服务 UI。
- **协议自有**：UI 只投影 ava 自己的事件与 approval 对象，不做通用 agent 协议适配（ZCode 放弃多 CLI 聚合的教训）。
- **通用多格式文件预览中心（PreviewPane，桌面端第一基座）**：
  - 深度契合「产物即状态」哲学：右侧主面板为按文件扩展名分发的内容路由容器，**以原生多媒体与网页全保真渲染为第一公民**：
    1. **`.html` 预览**（`04-review.html`、`shots gallery`、静态网页）：经沙箱 `<iframe sandbox="allow-scripts">` 或 `BrowserView` 原样嵌入。**MVP 阶段 04 审片直接复用成熟的 `04-review.html`，无需在第一天手写复杂的 React NLE 审片组件**；
    2. **`.mp4` / `.mov` 预览**（`05-final.mp4`、切片）：HTML5 原生 `<video controls>` + 毫秒/帧级时间码 overlay，对照脚本核验；
    3. **`.wav` / `.flac` 预览**（`03-audio/*.wav`、BGM 曲库）：HTML5 原生 `<audio controls>` + 连续播放队列，服务 03.5 顺听；
    4. **`.png` / `.jpg` 预览**（封面候选、单帧）：图片缩放平移（Pan-Zoom）查看；
    5. **`.md` / `.json` 预览**：带排版渲染与语法高亮折叠。
  - **产物文件树作为一等公民**：左侧/侧边直接展开当期目录结构，点击任何中间产物右侧瞬间响应对应预览，无需切出至 Finder/QuickTime/Chrome。
  - **交互渐进增强**：v1 核心为「通用全类型预览 + 悬浮 Approval/Reject 操作条」；复杂的划词注音打点等深度业务组件后置演进，不阻塞首版交付。
- 打包开 Electron asar 完整性校验（ZCode 未开被字节级 patch 的教训）。

### 5. Context 纪律（借 Manus，写入 harness 实现约束）

- agent 上下文里产物只留路径引用，内容按需读盘；压缩可恢复。
- 错误 observation（渲染堆栈、门禁打回）完整保留进上下文与 events.jsonl，不静默清理。
- 停机点之间按状态屏蔽非法工具（mask-don't-remove）：工具常驻上下文，当前状态不可用的动作从工具表过滤，而非靠 prompt 请求模型守规矩。
- **系统提示会话内绝对稳定**：状态变化、人审决策一律以追加的 user/tool 消息注入，不改写 system prompt 与历史（Manus KV-cache 原则：agent 输入输出 token 比 ~100:1，稳定前缀直接决定成本与延迟）。
- **Session 恢复（搭本 ADR 便车，不单独立 ADR）**：对话历史以 append-only JSONL 落盘 `data/episodes/<期号>/session.jsonl`（不进 git，与 events.jsonl 同层），`ava --continue` 恢复；停机点挂起期间退出 REPL 不丢上下文。

## 不做的事（本条同样立下）

- 不引入数据库（SQLite/ORM）。证据见 Hermes 调研：schema 迁移与损坏恢复是最大维护税；JSONL + 文件锁在 ava 规模足够。
- 不做 FastAPI/WebSocket server 层（Hermes 的多 surface server 为 21 个消息平台服务，ava 只有 CLI + 一个桌面端）。
- 不做多 agent 框架聚合、不做插件体系、不做 guardian LLM 智能审批（产线用确定性规则）。
- 不做 Web UI 终态；不为了「以后可能要 Web」而提前把 core 改成 server 形态——桌面端 host 进程是唯一 UI 宿主。

## 推翻条件

- 若 events.jsonl 在真实多期生产中证明不足（如需要跨期联查、复杂过滤），允许引入**只读索引**（如 SQLite FTS），但写路径必须保持文件落盘，索引可随时删了重建。
- 若 Electron 维护成本在 v1 后证明不可承受（打包、更新、跨平台），允许降级为「本地静态页 + 系统播放器」，但 events.jsonl 与 approval 对象层保留——它们是 harness 的资产，不是 UI 的。
