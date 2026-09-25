# 需求交接：ava Harness 演进方向（2026-09-22 晚讨论收口）

日期：2026-09-22（2026-09-23 补：§0.2 按用户裁决修订第 2、4 条；新增 §6 二期需求、§7 施工模块划分）
性质：**上位需求文档**，不是 spec。下游产物 = 各特性的 Implementation Spec（格式先例：`2026-09-21-pi-scout-handoff-spec.md`，红队三轮收口文化）。
阅读方式：先读本文件确定要写的 spec，再读对应 ADR 全文，再动工。

---

## 0. 一句话方向

**ava 从「白名单执行器」演进为「有事件层、有审批对象、有网络工具、有上下文装配、有记忆的桌面 agent」**，终态是用户打开 ava 不用太多思考就能高效做视频。骨架、手、记忆、上下文四条线已各立一条 ADR，本文件把它们翻译成施工顺序与 spec 边界。

## 0.1 终态产品画像与端到端用户旅程（User Journey）

- **形态定调**：macOS 原生桌面应用（双击打开），非 WebUI、非命令行。本质是**「任务流看板 + 产物审阅器 + 伴随式 Agent（对标 ZCode、Hermes 桌面端）」**。
- **端到端生产旅程（A Day in Production）**：
  1. **启动与进入**：用户双击 macOS Application 图标唤起工作台。界面三栏布局：左侧为期列表与产物文件树；中央为伴随式对话与任务执行线；右侧为全格式 `PreviewPane` 预览中心。
  2. **意图自推断与发起**：用户在中央输入框以自然语言发起需求（如「做一期关于《罪恶王冠》集与祈命运的剧情回顾」），Agent 自主推断题材模式、建立期目录，解构出任务线。
  3. **任务进度全可见**：流水线推进过程中，界面呈现结构化步骤条与任务轨迹（如「正在检索候选素材 3/5」、「正在生成音频 seg-04」），非刷屏黑底终端日志，任务耗时与状态实时刷新。
  4. **停机点主动触达与审计回显**：Agent 自动推进至停机点（02.5 / 03.5 / 05 / 09）时，**右侧 PreviewPane 自动呼出对应产物的全保真预览**（02.5 排版稿件、03.5 音频播放器、05 审片网页/视频、08 封面九宫格），中央弹出 Approval 审计卡，清晰输出让用户决策的关键指标（时长合规带、LUFS 响度、门禁绿灯、CER 告警点）。
  5. **极简裁判决策**：用户审阅预览产物后，在卡片上直接点击「批准」或「打回（附带自由文本反馈）」。Agent 接纳反馈后自纠或接续推至下一工序，直至成片导出。

## 0.2 终态验收判据（Acceptance Criteria）

1. **终端零依赖**：正常制片全流程（从 01 选题到 07 封面），人类**无需打开系统终端、无需敲入任何 bash 命令**；
2. **操作次数极简（裁判模型）**：全期制片过程，人类的判定集中在 4 个停机点（审稿批准、顺听打点、审片批准、封面与标题定稿），无任何多余确认。2026-09-23 修订两处：① 09 不是「单选」，而是多轮协作（换图、改字、讨论标题），定稿权在人（§6 Spec 11）；② §4 红线 5 规定的人审闸门（素材 fetch 批准、记忆首次写入确认、browser 逐调用审批）是停机点之外的必要确认，不算「多余确认」；
3. **全要素应用内原位审计**：所有产物（`.html`、`.mp4`、`.wav`、`.png`、`.md`、`.json`）必须能在桌面端右侧面板直接点击查看/播放，严禁依赖外部播放器或跳转外部浏览器。本条约束的是「审阅 ava 的产物」；人自己上网找图、用 Google Flow 扩图属于 ava 之外的创作行为，不受本条约束（2026-09-23 澄清）；
4. ~~**人时预算硬核达标**：全期制片人类总投入时间（以应用内统计为准）严格落在 $k \times \text{片长}$ 预算内。~~ **2026-09-23 用户裁决撤销，降为观测量**：墙钟计时混入注意力因素（人走神也照算），拿它当验收门槛或停产判据都太硬。人类耗时继续记录，按停机点分布呈现，用来找「哪个环节最费人」，不作任何门禁（`docs/dev/STANDARD.md` 三节同步修订；代码侧改动见 §7 M0；桌面端的采集口径在 §6 Spec 10 定义）。

## 0.3 借鉴边界：抄 ZCode 的「形态架构」，立 ava 的「视频生产专属交互逻辑」

> **最高警示**：ZCode 是纯粹的 **Coding Agent**，而 ava 是极其专业的 **动漫二创视频生产 Agent**。两者业务心智模型完全不同。**抄的是前端工程架构与 UI 形态质感，绝不是其通用编程交互逻辑！**

| 维度 | 抄 ZCode（形态与工程底座） | 立 ava 专属（绝不照搬，针对视频重构） |
|---|---|---|
| **进程模型** | 三进程隔离（main / host utilityProcess / renderer），长任务不卡窗口。 | 保持不变。 |
| **主面板核心** | ZCode 核心是 **代码 Diff Viewer 与 Monaco 编辑器**（针对代码变更审查）。 | **彻底剔除代码编辑器/Git面板！** ava 核心是 **时序音画对照审验**：HTML5 视频（带毫秒/帧级时间码 overlay）、音频分段顺听列表、全保真内嵌 `04-review.html` 审片页。 |
| **任务流转** | ZCode 围绕代码分支、终端命令、持续编辑循环运转。 | ava 严格围绕 **四阶段物理工序（A/B/C/D）与 4 大人工停机点（02.5/03.5/05/09）** 推进，是阶段性闸门（Stage Gate），不是代码迭代。 |
| **人机交互心智** | 程序员与 Agent 协同编码、修改文件、调试报错。 | **总监与执行导演（裁判模型）**：用户不跟 Agent 漫无边际聊天，只在停机点对着右侧原生预览看数据/音画，只做「批准 / 驳回附带结构化意见 / 划词注音打点」。 |
| **界面像素纪律** | 界面每一处为了提高 Coding 效率服务。 | **界面的每一像素必须只服务于「如何让人最快审阅视听素材、最省力判定合格」**。严禁塞入任何 Git 提交树、终端繁琐输出或无用的通用开发工具。 |

## 1. ADR 总表（施工前必读全文）

| ADR | 标题 | 一句话 |
|---|---|---|
| `docs/dev/adr/0020-harness-eventization-and-electron-desktop.md` | Harness 事件化与 Electron 桌面端 | jobs 状态机 + events.jsonl + approval 对象 + Electron（解除 ADR-0018 的 GUI 禁令） |
| `docs/dev/adr/0021-network-and-asset-tools-internalization.md` | 网络与素材工具内化 | web_search/web_fetch/crawl/browser 四级链进仓库，scout 派工降级为升级通道 |
| `docs/dev/adr/0022-context-assembly-and-agents-md-slimming.md` | 上下文按需装配 | 三层系统提示（常驻/工序/按需），AGENTS.md 瘦身 ≤150 行 |
| `docs/dev/adr/0023-cross-episode-memory-and-model-tiering.md` | 跨期记忆与模型分层 | memory.md（4000 字符预算、首次人确认）+ models 按用途分档 |

依赖关系：**0022 先行**（纯 harness 层、无外部依赖、当期受益）→ **0020 P1 jobs 层**（一切 UI 与记忆数据源的地基）→ **0021**（web_search/web_fetch 先行，crawl/browser 随后）→ **0023**（等 0020 的 approval feedback 落地后再做记忆聚合）。桌面端（0020 §4）最后。

## 2. 需要写的 Spec 清单（按施工顺序）

### Spec 1：上下文装配器（ADR-0022）

- **范围**：`pipeline/agent/` 新增装配器模块（纯函数：输入 scope + `inspect_episode()` 工序 → 输出注入文档清单）；路由表进配置不进代码；AGENTS.md 瘦身搬迁（内容一字不改只搬家）；工序层只增不改。
- **关键约束**：判据十三条不进按需层；装配器顶层零重依赖（同 scout.py 纪律）；系统提示会话内稳定（追加不修改）。
- **验收方向**：瘦身后 AGENTS.md ≤150 行；搬迁内容 diff 为零；装配器纯函数测试（构造各工序状态断言注入清单）。

### Spec 2：jobs 层与 events.jsonl（ADR-0020 §2）

- **范围**：`pipeline/jobs.py`（job 状态机 pending→running→succeeded|failed|blocked，id/命令/起止/退出码）；事件追加写 `data/episodes/<期>/events.jsonl`（不进 git）；事件 sidecar 失败静默纪律；CLI 执行入口全部改走 jobs。
- **关键约束**：**状态真相源不变**——`pipeline.status` 仍从产物文件推导，events.jsonl 是观测层不参与状态推导（防双头）；append-only、确定性序列化；sidecar 满则丢、死则短路，主流水线永不阻塞。
- **验收方向**：杀掉一个跑着的 job，从 events.jsonl 能完整还原执行轨迹；事件写入故障（如盘满）时流水线照常跑完。

### Spec 3：approval 对象化（ADR-0020 §3）

- **范围**：停机点 02.5/03.5/05/09 升格为 pending approval 对象（类型/期号/关联产物/可选项），状态机 pending→approved|rejected(feedback)|superseded；ack 落盘为事件；rejected 携带结构化反馈（哪段、什么问题）回写期产物。
- **关键约束**：ack 必须是显式动作（AGENTS.md 六节精神不变）；写产物状态保持单线程；`--approve` 语义不回归（ADR 历史教训：曾因自动写 approved 形同虚设）。
- **验收方向**：REPL 退出重开后 pending approval 仍在，可 ack；rejected 的 feedback 能被后续 agent 会话读到。

### Spec 4：网络工具 web_search + web_fetch（ADR-0021，第一批）

- **范围**：两个只读工具进 LLM 工具表；scope 分组授权机制（asset/creative 可见，pipeline 永不可见）落地——这是 toolsets 分组的第一块砖，后续工具都走它；纳入 `assert_egress_boundary`。
- **关键约束**：零重依赖（stdlib + 既有出网路径）；工具表封顶 ~12 个（ADR-0021 已立）；scout spec 里「6 工具冻结」的验收门禁已失效（ADR-0021 推翻），spec 里要写明新口径「工具表变更必须有对应 ADR 编号」。
- **验收方向**：pipeline scope 会话的工具 schema 里看不到网络工具（mask-don't-remove）；egress boundary 对两工具生效。

### Spec 5：crawl + browser（ADR-0021，第二批）

- **范围**：crawl（无头、可 stealth）做成 uv 可选 extras，未安装时工具从 schema 层隐藏；browser（登录态、独立持久化 profile、approval 门、profile 路径 config 钉死）。
- **关键约束**：升级链逐级不跳级、失败不静默降级（STANDARD.md 五节既有条款）；browser 严禁直连主力 Chrome Default 配置；approval 门是确定性规则，不引入 guardian LLM。
- **验收方向**：未装 extras 时工具表无 crawl；browser 每次启动落审批事件；profile 隔离可验证。

### Spec 6：acquire_propose（ADR-0021 §3）

- **范围**：受控写 `data/library/incoming/candidates.json`（白名单、双端 resolve、atomic_write，同 write_episode_file 纪律）；人审闸门与 `pipeline.acquire` 不动。
- **关键约束**：不许自动 fetch；candidates.json 的 `why` 字段充分性沿用 acquire-assets skill 判据。
- 小 spec，可与 Spec 4/5 合并写。

### Spec 7：memory.md 与模型分层（ADR-0023）

- **范围**：`data/library/memory.md`（不进 git、条目制、4000 字符预算、atomic_write、受控写入工具）；首次写入人确认；装配器在 asset/creative scope 注入全文（依赖 Spec 1）；`config/agent.json` 增加 `models` 段（reasoning/light 两档，缺省回落单模型）。
- **关键约束**：分层表不越位（红线进 AGENTS.md、参数进 config、经验进 memory.md，memory.md 不是绕过 ADR 的后门）；「可能」不许进记忆、每条带证据期号；模型分层调用点写死用途，不做自动路由。
- **验收方向**：未配置 models 段时行为与现状完全一致（回归测试）；memory.md 超限必须合并腾位。

### Spec 8：Electron 桌面端（ADR-0020 §4，最后做，可先只写架构 spec）

- **范围**：
  - **通用多格式文件预览中心（PreviewPane，桌面端第一基座）**：
    1. **`.html` 预览**（`04-review.html`、`shots gallery`、静态网页）：经沙箱 `<iframe sandbox="allow-scripts">` 或 `BrowserView` 原样嵌入。**MVP 阶段 04 审片直接复用成熟的 `04-review.html`，无需手写复杂的 React NLE 审片组件**；
    2. **`.mp4` / `.mov` 预览**（`05-final.mp4`、切片素材）：HTML5 原生 `<video controls>` + 毫秒/帧级时间码 overlay，对照脚本核验；
    3. **`.wav` / `.flac` 预览**（`03-audio/*.wav`、BGM 曲库）：HTML5 原生 `<audio controls>` + 连续播放队列，服务 03.5 顺听；
    4. **`.png` / `.jpg` 预览**（封面候选、单帧）：图片缩放平移（Pan-Zoom）查看；
    5. **`.md` / `.json` 预览**：带排版渲染与语法高亮折叠。
  - **产物文件树一等公民**：左侧/侧边直接展开当期目录结构，点击任何中间产物右侧瞬间响应对应预览，无需切出至 Finder/QuickTime/Chrome。
  - **交互渐进增强**：v1 核心为「左侧产物树 + 通用全类型预览 + 底部/浮动 Approval 决策条（Approve / Reject 携带 freeText 反馈）」；复杂划词注音打点等深度业务组件后置演进，不阻塞首版交付。
  - **进程与通信模型**：main + host utilityProcess + renderer 三进程；host spawn `ava`/pipeline 子进程并消费 events.jsonl（tail 订阅 + snapshot 首载）；core 保持纯 Python 无 server；asar 完整性校验。
- **关键约束**：UI 只投影 ava 自有事件与 approval 对象，不做通用 agent 协议适配（ZCode 放弃多 CLI 聚合的教训）；媒体用原生 `<video>/<audio>` + 本地路径；写路径仍走既有白名单工具，UI 是「读取 + 审批」。
- **注意**：UI 不做 LLM 推荐/预判断（2026-09-22 用户明确否决建议 F：LLM 的推荐不可信）。只呈现确定性事实（人时读数、门禁告警段定位等纯数据回显）。

## 3. 可引用的调研证据（2026-09-22 三份调研，写 spec 时直接引用，不必重新调研）

**Hermes**（源码级，github.com/NousResearch/hermes-agent）：
- 事件 sidecar：`tui_gateway/event_publisher.py`——daemon 线程、queue 满即丢、死连接短路，主循环永不阻塞。
- toolsets 分组：`toolsets.py`——webhook 面只暴露 4 个只读工具；按 surface 加载不同工具集。
- 审批分层：`approval_detection.py → approval_floors.py → approval_prompt.py`，确定性规则优先；`HERMES_YOLO_MODE` 在 import 时冻结防进程内提权（`approval.py:47`）。
- 记忆：`MEMORY.md`/`USER.md` 平面文件，`\n§\n` 分隔、字符预算、原子写、注入扫描。
- 反面教材：SQLite state.db 有 SCHEMA_VERSION=30 的迁移链 + 十余个 repair/wal 模块——**ava 不引入数据库的实证**。

**Manus**（官方博客 manus.im/blog/Context-Engineering-for-AI-Agents-Lessons-from-Building-Manus + 泄露 prompt 2025-03 版，注明时效）：
- KV-cache：agent 输入输出 token 比 ~100:1；系统提示稳定 + append-only 上下文直接决定成本。
- mask-don't-remove：工具常驻上下文，按状态机过滤可用动作，不靠 prompt 求模型守规矩。
- 保留错误 observation 让模型自纠，不静默清理。
- 文件系统即上下文：产物只留路径引用，压缩可恢复。

**ZCode**（开源仓库 github.com/zai-org/ZCode v3.14.0，2026-09-20 开源）：
- 三进程：main + host（utilityProcess）+ renderer，业务不跑主进程。
- 权限确认协议化：options（一次/本会话/本项目总是允许）+ **freeText 拒绝回喂 agent**——「拒绝并告诉它怎么改」是一等交互。
- 事件流 snapshot+delta+coalesce 抗卡顿。
- 教训：v2.x「套壳聚合多家 CLI」在 v3.0 被自研单一协议彻底替换；未开 asar 完整性校验被字节级 patch。

## 4. 施工红线汇总（所有 spec 通用）

1. **不引入数据库**（含 SQLite）：JSONL + 文件锁覆盖 ava 规模。唯一豁免通道：只读索引且可删了重建（ADR-0020 推翻条件）。
2. **不引入第三方 Agent 框架、编排框架、web server 层**（ADR-0018 保留条款；core 无 server，host 进程 spawn 子进程）。
3. **产物即状态不变**：`pipeline.status` 的产物推导是唯一状态真相源，events.jsonl/session.jsonl 都是观测层。
4. **人工停机点不减少**：02.5/03.5/05/09 全部保留；ack 永远显式；封面标题只出候选（ADR-0018）。
5. **人审闸门不自动化**：素材 fetch 必先过人批准（ADR-0021）；记忆首次写入人确认（ADR-0023）。
6. **工具表封顶 ~12 个**；新增工具必须有 ADR 编号（替代已失效的「6 工具冻结」门禁）。
7. **依赖纯洁性**：新模块顶层零重依赖（numpy/ML 栈不进 status/scout/装配器热路径），重依赖函数内延迟 import 或 uv extras 隔离；每 spec 附 numpy 回归断言（scout spec v0.4 先例）。
8. **写测试两条纪律**（AGENTS.md 十二节）：期望值先跑一遍再写断言；写完做变异检验。

## 5. 明确排除（今晚讨论中否决的，spec 不要复活）

- ~~LLM 推荐/预判断~~（建议 F，用户否决：LLM 的推荐一般很可笑；且与判据「不许把审美判断包装成机器判据」同源）。UI 只呈现确定性事实。
- ~~多 agent 框架聚合 / 通用 agent 协议适配~~（ZCode 教训）。
- ~~guardian LLM 智能审批~~（产线用确定性规则）。
- ~~向量记忆 / embedding 检索~~（规模不配）。
- ~~Web UI 终态~~（用户明确要 Electron）。

## 6. 二期需求（2026-09-23 补充定义；一期 25 个 session 完工后，按一期的老路写 spec、过红队）

来源：2026-09-23 对照 §0.2 验收逐条查空白，用户逐项裁决。一期 8 份 spec 覆盖地基与桌面端 v1（只读 + 审批）；以下是 §0.2 终态的剩余主体，拆成 4 份 spec、2 条新 ADR。写作顺序 Spec 9 → 10 → 11 → 12（后者依赖前者）。设计与评审的会话提示词另存于用户桌面，不进仓库。

### Spec 9：无终端 agent 会话协议与 Session 恢复（core）

- **定位**：§0.2 第 1 条「终端零依赖」的 core 侧地基。桌面端没有 TTY，需要一个 host 能 spawn、经 stdio 驱动的 agent 会话进程。
- **范围**：非 TTY 会话协议（用户消息进；agent 回复、工具轨迹、人审请求出；ava 自有协议，不做通用 agent 协议适配，ADR-0020 §4）；**人审请求统一协议化**——工具调用审批（`cli.py` 的 `_default_approve`，含 Spec 5 browser 逐调用卡）、Spec 6 素材 fetch 批准、Spec 7 `/memory ack`，答复只能来自人的操作，agent 不能自答；**Session 恢复**：ADR-0020 已决定（`data/episodes/<期>/session.jsonl` append-only + `ava --continue`，停机点挂起期间退出不丢上下文），一期没有任何 spec 覆盖，并入本 spec。
- **约束**：core 不起 server、不开端口（§4 红线 2）；终端 REPL 零回归；Spec 1 的系统提示会话内稳定纪律在恢复时仍成立；审批是确定性规则，无 guardian LLM（§5）。

### Spec 10：桌面端对话面板与人审卡片（desktop）

- **范围**：消费 Spec 9；中央输入框用自然语言发起新期（§0.1 第 2 步，建期目录由 core 完成，UI 不 mkdir）；任务轨迹实时可见；Spec 9 的全部人审请求在 app 内以卡片呈现，只能由人点击答复；09 标题的多轮讨论在这里进行（Spec 12）。
- **约束**：新增协议方法、spawn 模板 = 修订 Spec 8 的闭集；停机点自动呼出与对话流争夺焦点的问题（Spec 8 RF-24）在此一并定规则。

### Spec 11：停机点深度组件（02.5 app 内改稿与封板、03.5 顺听纠错、人时采集）

- **02.5**：**app 内置 Markdown 编辑器**（用户裁决，否决「外部编辑器改、app 只封板」）。改稿、预览、封板都在 app 内完成。这是 UI 第一次拥有写产物的能力，偏离 ADR-0020 / 本文件 Spec 8「UI 是读取 + 审批」的定位，**须先立 ADR-0024（桌面端写产物）**，并重新定义 Spec 8 的写不变量 I1/I2：写入走 core（atomic_write + 期目录白名单，同 `write_episode_file` 纪律），不由 host 直写；人与 agent 同时写 `02-script.md` 的冲突要有确定性规则（如保存时指纹不符即拒存）。
- **03.5**：把终端 `/voice` 已有的指令（`听 N`、`停`、`听`、`回滚 N`、`撤回 N`、`done`，`cli.py` `run_voice_session` 的指令文法）做成段落列表上的按钮，语义一对一，不新增纠错能力；划词注音后置。
- **人时采集**：人时已降为观测量（§0.2 第 4 条），本 spec 定义桌面端审阅耗时怎么记；在此之前 Spec 8 门禁 14 的「本次审阅不计人时」横幅照旧。

### Spec 12：09 封面与标题协作

- **实际工作流**（用户 2026-09-23 描述）：agent 出的封面常不理想，人自己上网找图；找到的图**大多不是**标准的 16:9 / 9:16，人先在 ava 之外用 Google Flow 扩成横竖两版，再交给 agent 做**确定性编辑**（叠字、描边、排版等）；少数本身比例合适的图直接交给 agent。标题常需要多轮讨论，或由人给出建议。
- **范围**：① 人把图片交给 ava（拖入 app、导入期目录；写路径沿用 Spec 11 / ADR-0024）；② 确定性图片编辑工具：agent 只给参数（文字、位置、字号、描边），用 Pillow 渲染，结果可复现、零新依赖；③ 标题讨论在 Spec 10 的会话里进行；④ 定稿权在人（ADR-0018「封面标题只出候选」不变）。09 的 ack 记录人最终选定的封面文件与标题——**这一条是作者默认，用户未裁决，写 spec 时须问人**。
- **工具表封顶（用户 2026-09-23 裁决：提高上限）**：一期完工后工具表恰为 12 个（现有 6 + Spec 4 两个 + Spec 5 两个 + Spec 6 一个 + Spec 7 `write_memory`，ADR-0021 的「~12」口径即 6 + 网络 4 + acquire_propose + 预留 1）。图片编辑工具是第 13 个，**须先立 ADR-0025 上调封顶**，同步修订 ADR-0021 的口径与本文件 §4 红线 6；新上限的数值与理由由 ADR 论证，不拍脑袋。
- **明确不做**：AI 生图或扩图进 ava（扩图由人在 Google Flow 完成）；自动上传发布（触发「公开发布」红线）。

## 7. 一期施工：25 个 session（2026-09-23）

**节奏**：12 个模块，每个模块 2 个 session（一个施工、一个 review 验收），review 🟢 才推进下一个模块；第 25 个 session 在全部施工完成后，系统性纠正文档与代码之间的不一致和偏移。各 session 的提示词另存于用户桌面，不进仓库；它们共同遵守下面的「通用守则」。

| 模块 | session | 内容 | 前置 | 备注 |
|---|---|---|---|---|
| **M1** | S1 / S2 | Spec 1 上下文装配器 PR0–PR2 | 无 | PR0 搬迁 AGENTS.md，「内容一字不改」以开工时的 AGENTS.md 为准 |
| **M2** | S3 / S4 | Spec 2 PR1–PR2（数据模型、状态机、事件 sidecar） | M1 | 只加新模块，不动执行路径 |
| **M3** | S5 / S6 | Spec 2 PR3–PR4（执行器置换、CLI 贯通、门禁固化） | M2 | 核心置换，风险最高 |
| **M4** | S7 / S8 | Spec 3 PR1–PR2（对象模型、持久化、自愈、状态卡引导行） | M3 | **S8 同时对 Spec 3 §1.3–§1.6 做红队定向复核**（Spec 8 红队已声明未做） |
| **M5** | S9 / S10 | Spec 3 PR3–PR4（ack 表面、`--id`、确认路径、一次进锁、`review.py` 期望指纹） | M4 + S8 的文档复核 🟢 | 唯一改 `pipeline/review.py` 的模块；`tests/test_review.py` 零回归 |
| **M6** | S11 / S12 | Spec 4 web_search + web_fetch PR1–PR3 | M1 | 工具表 6 → 8 |
| **M7** | S13 / S14 | Spec 6 acquire_propose PR1–PR2 | M6 | 工具表 → 9 |
| **M8** | S15 / S16 | Spec 5 crawl + browser PR1–PR3 | M6、M3 | 工具表 → 11；浏览器二进制由人执行一次性安装 |
| **M9** | S17 / S18 | Spec 7 PR1–PR3（模型分层、记忆核心、工具实现） | 无硬前置 | PR3 之后模型仍看不到该工具 |
| **M10** | S19 / S20 | Spec 7 PR4–PR6（注入与开放写权限、`/memory`、门禁收口） | M1、M5、M9；M7、M8 已登记 | `write_memory` 占第 12 位 |
| **M11** | S21 / S22 | Spec 8 PR0–PR3（隔离守卫、骨架与打包配置、host 读路径、renderer 与预览） | 无硬前置 | 只动 `desktop/`；PR2 首日实测假设 10 |
| **M12** | S23 / S24 | Spec 8 PR4–PR5（决策条、ack 链路、打包加固、MUT 全量实跑） | M5、M11、M3 | 含门禁 11 回看、门禁 6/15 真机手验 |
| — | S25 | 全局纠偏：文档与代码一致性；人时降为观测的代码侧改动；已完工方案归档 | S1–S24 | 文档只许瘦身不许膨胀 |

人时降为观测后代码侧的改动（`status.py:121-156` 的「人类耗时超预算」advisory、`cli.py:171`、`195-201` 的「连续三期超预算」横幅与 `cli.py:823` 的 docstring、`tests/test_agent_cli.py:291`、`tests/test_agent_resolver.py:137`）按用户裁决放在 S25 做（**S25 已完成**：advisory 改为不含「超预算」的观测行，横幅与计数删除）；S1–S24 期间文档与代码在这一点上暂时分叉，属已知状态。

### 7.1 通用守则（全部 session）

**施工 session**
1. 读 `AGENTS.md`、`docs/dev/plans/README.md`「执行代理守则」、本节，以及本模块对应 spec 的全文（含 §1 裁决表与行号自查表）。
2. 开工前：`git status` 中 `pipeline/`、`tests/`、`config/`、`desktop/` 须干净（上一模块已提交），不干净就停下问人；基线 `uv run pytest` 全绿，不绿停下报告。
3. spec 的行号是撰写时的快照，前面的模块会让它漂移：动任何一处之前先对照工作树按内容锚点重核；对不上且影响设计时停下报告，不即兴发挥。
4. 只做本模块 PR 清单内的事。测试纪律：期望值先跑再写断言；变异矩阵逐条实跑（植入变异 → 确认目标用例变红 → 还原），结果回填报告。
5. §4 八条施工红线任何一条都不许碰。
6. **产品问题问人**：遇到明显涉及产品体验、产品功能的不确定（界面文案、交互流程、默认值、功能取舍），停下来问，不替人拍板；纯技术实现细节按 spec 与仓库惯例自行决定。
7. **大胆删除，但先问**：项目此前并非朝当前方向演进，遇到看起来已无用的代码或文档，先核实（grep 引用、`git log` 来历、测试覆盖），把证据列给人（是什么、为什么没用、谁还引用、删了会怎样），得到明确肯定后再删；删除单独列清单，不夹带在本模块的功能改动里。
8. 文档随代码同步，但只许瘦身不许膨胀：能改一行就不加一段。发现 spec 本身有错 → 报告，经人同意后在 spec 里做最小修订并注明。
9. 不 commit、不 push。结束时输出施工报告：PR 完成情况、验证命令的原样输出、变异结果表、与 spec 的偏差、待人决定的问题、删除清单。

**review session**
1. 独立验收，不信施工报告，一切自己重跑。
2. 检查：`git diff` 范围只触及本模块应触及的文件；spec 验收门禁逐项；spec 里的全部验证命令；全量 `uv run pytest`；变异矩阵抽查（与核心断言相关的全查，其余至少三分之一）；行号与锚点；八条红线；文档是否膨胀；每一处删除是否经人同意。
3. 结论三档：🟢 通过 / 🟡 需修 / 🔴 打回。发现按严重度排序，每条附可复现证据（命令与输出）。
4. 🟡/🔴：人会把清单贴给施工 session 修复，修完再回本 session 复核，直到 🟢。
5. 🟢 之后：只改 `docs/dev/plans/README.md` 中该 spec 的那一行状态；向人申请提交（附 commit message 草稿），得到同意后提交，不 push。
