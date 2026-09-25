# ava 启动入口改造 Spec：无期选题会话（idea scope）

日期：2026-09-21（**v1.2，红队终审两轮收口**）
需求来源：`ava-启动入口改造-需求交接-2026-09-20.md`（一轮对话的结论、实测证据与硬约束；
本 Spec 是它的架构设计与施工图，需求表的结论不再复述论证，只引用编号）
上位文档：`archive/2026-09-20-ava-ai-native-director-spec.md`（v1.5，交互层 SSOT）、
`2026-09-18-ava-agent-impl-spec.md`（v1.20，**本 Spec 扩展其 §2.3 写死的启动形态，
实施落地时以其 v1.21 修订注记收口**，见 §6.9）
对应 issues：**D27**（本 Spec 开立）
状态：**已落地实施并全量验证通过（v1.2，D27 实施闭环，15 组变异全杀）**

> **v1.2 修订来源**：红队复审（v1.1 收口核验 13/13 属实；本轮 1🔴 + 1🔵）。
> 🔴-R1：M27a 仍为一号三义（三消费点 = harness 三个条目），拆为 M27a1/a2/a3，
> §3.4 / §5.1 / §7 / §9 四处计数 13→15；🔵-R2：T1 行内注明「词=说明」解析面
> 限期名段除外（防实现者把正则写宽造成期名含 `=` 的理论假阳性）。无设计语义变更。

> **v1.1 修订来源**：第一轮红队终审（事实性复核 10/10 属实；2🔴 + 3🟡 + 4🔵）。
> 收口清单：① 🔴-1 T10 补键集精确断言（M16③ 同族病：只断言「带这三个键」杀不死
> 「多塞一个键」）；② 🔴-2 变异一号多义全拆（M22a/b、M25a/b、M27a/b/c + 新增 M30，
> 照 M15a/b、M21a/b 拆分先例），§5.1 表逐号对齐、§7 验证命令同步；③ 🟡-3 §2.2 补
> v1.5 §6 铁律 4 的显式处置（扩展非改动的限缩声明）；④ 🟡-4 降级文案表外风险实收
> 入列（`llm.py` 进改动清单 + T11/M30）；⑤ 🟡-5 T1 升级回需求 §7.5 委托的通用防
> 漂移断言；⑥ 🔵-6/7/8/9 措辞与声明修正（碰撞声明写准、impl-spec §2.5 示例注记、
> T8 代理判据改实、idea 不做 `check_code_freeze` 写明）；另 B3：§2.1「同一循环体」
> 措辞改实为近拷贝。无设计语义变更，护栏与目标行为表一条未动。

> 本 Spec 回答一个问题：如何给「还没有期」的活（想一个新视频做什么）开一个入口，
> 同时让需求交接的 8 条硬约束一条不松、既有 1440 条测试除显式列名的扩展外全绿。
>
> 核心架构主张一句话：**无期态不是新的权限概念，而是既有机制的缺省值**——
> `ToolContext.episode_dir = None` + 写工具 fail-closed + `asset` 空表先例，
> 护栏层早就按「可以没有期」写好了（需求 §2 已实测核证）。本改造只接入口，不设防。

---

## 1. 目标与对应 issue

**D27：立项前工作无入口。** `ava` 今天的入口默认「你总是要处理某一期」：
`create_new_episode()` 建完目录直接 `return 0` 退出程序，`run_repl(ep_dir)` 的
期目录入参必填。而 `01-topic.md` 的「张力」是讨论的产物，不是讨论的入场券——
容器被要求先于内容存在，顺序反了（需求 §1）。

目标行为表（需求 §3，本表即验收语义）：

| 输入 | 去哪 | 变化 |
|---|---|---|
| `ava` + 回车 | 最近改动那期 | **不变**（硬约束 1/2） |
| `ava` + 数字 / 期名 | 那期 | **不变**（`tests/test_agent_cli.py` 选期语义钉住） |
| `ava` + `idea` | 无期选题会话（第二消费点） | 新增 |
| `ava new <名>` | 建目录 + 模板 → **直接进该期对话**（tty 下） | 改（原为建完退出） |
| `ava idea` | 无期选题会话（第一消费点） | 新增 |

非 tty 下三条路径全部是「打印即退出」：裸 `ava` 打印看板（既有，不动）、
`ava new` 建目录打印产物路径后退出、`ava idea` 打印会话说明后退出（§3.3）。

### 前置条件

- 分支 `ava-harness`（本地，不切分支、不 push）；
- 开工基线：`PYTHONDONTWRITEBYTECODE=1 uv run python -m pytest -q -p no:cacheprovider`
  全绿（HEAD 实测 1440 passed），基线不绿不开工；
- 手动验收（真 `ava new` / `ava idea`）需要 data 盘挂载；纯函数与 REPL 测试不需要
  （tmp_path + monkeypatch `paths.ROOT` 先例）。

---

## 2. 架构设计

### 2.1 一个实现、两个消费点（照 Spec §3.2-7 老规矩）

无期选题会话的实现 = **既有 `run_agent_loop` 泛化出的第三个 scope_mode**：

```
run_agent_loop(ep_dir=None, scope_mode="idea")
```

两个消费点调同一个入口，不做两份实现、不做两份清单（需求 §5-2）：

1. **子命令**：`main()` 在 `resolve_episode_target` 之前分派 `args[0] == IDEA_KEYWORD`
   （与 `ava new` 的 `args[0] == "new"` 前置分派同构）；
2. **选期提示词**：`select_episode_interactive` 认出关键词，返回字符串 sentinel
   （见 §2.4），看板流把它路由到同一个 `run_agent_loop(None, scope_mode="idea")`。

`run_agent_loop` 的泛化面（三处签名放宽，行为分支新增一条）：

- `ep_dir: Path` → `ep_dir: Path | None`；`scope_mode="idea"` 新增一个子循环分支。
  **措辞改实（v1.1 B3）**：聚焦循环体每轮 `inspect_episode(ep_dir)` 与 `ep_dir.name`
  两处对 None 裸崩，无法原样复用——idea 子循环是聚焦循环的**近拷贝**（差 3 行：
  不 `inspect_episode`、scope 恒定 `"idea"` 不热推导、提示行 `ava [选题] (idea) > `），
  外加开场打一段会话说明（这是什么、写权限为零、产出如何落盘）。**共享的是回合
  机身 `_dispatch_agent_turn`**——降级、LLMError/PermissionError 回滚、审批回调的
  唯一实现长在它里面，v1.5 §1.1「全仓库一份聊天循环」铁律的实体在此，不动；
  idea 子循环**不做** `check_code_freeze`（v1.1 🔵-9：随聚焦模式——idea 工具表连
  `run_pipeline` 都没有，碰不到 `pipeline/`，检查无的放矢；照抄 `_run_repl_body`
  开场会多出 freeze 检查与停机点记账两道无意义动作）；
- `_dispatch_agent_turn` 的 `ep_dir` / `status` 放宽为可 None；idea 模式下
  `ToolContext(scope="idea", episode_dir=None, root=root)`——读域只剩
  `data/library/`、写工具 fail-closed，全部是既有缺省行为（需求 §2 实测表）；
- `assemble_system_prompt` 的 `ep_dir` / `status` 放宽为可 None；`ep_dir is None`
  时状态卡段取 `build_idea_card()`（§2.3）。

**不新增第四个聊天循环**：idea 分支长在 `run_agent_loop` 里，降级、
LLMError/PermissionError 回滚、审批回调仍然全仓库一份实现（v1.5 §1.1 铁律）。
`/quit` 与 EOF/KeyboardInterrupt 退出语义与聚焦模式一致；其余快捷键在 idea
会话里**不开放**（聚焦模式先例：只有 `/quit`，其余输入进对话——模型手里有
`list_episodes` / `read_status`，看板类需求走工具，不为它开第二条路由）。

### 2.2 idea scope 与工具表（需求 §7.3 已核完，照单落）

`config/agent/tools.json` 增加一键：

```json
"idea": ["read_artifact", "list_episodes", "read_status", "search_notes"]
```

即 `creative` 表减去它唯一的写工具。**取值理由**：

- 4 个工具全部已声明 `side_effect: false`——idea 会话里**一张审批卡都不会弹**，
  审批疲劳面为零新增；
- 写权限为零不靠 prompt 劝导：`write_episode_file` 不在表内（schema 层不可见）
  + 执行层 `execute_tool` 按 scope 白名单拦截 + `_tool_write_episode_file` 对
  `episode_dir=None` 抛 `PermissionError`——三层都是既有机制，本 Spec 零新增护栏；
- 新 scope 名的合法先例是 `asset` 空表（`load_scope` 对缺省 scope 文件回退
  `# Idea Scope`，但 §2.3 要求成稿 `idea.md`，不回退）。

`tests/test_agent_tools.py` 的 `SPEC_TOOLS` 镜像表同步加 `"idea"` 键——
它是仓库真配置与 spec 表一致性的钉扎测试（`test_llm_scope_tool_filtering_isolation`
末段逐 scope 比对），加键是显式扩展，与硬约束 8 同程序。

**与 v1.5 §6 铁律 4 的关系（v1.1 🟡-3 显式处置）**：铁律 4 写「`tools.json`、六个
工具的 schema、`validate_pipeline_command` 的拒收集零改动」。本 Spec 给
`tools.json` 加第四个 scope 键，与该条的冲突处置如下——铁律 4 的语境是 v1.5
**自身 PR5–PR7 的验收门槛**（防的是那轮重构顺手扩张工具面），其保护对象是
**三张既有表、六个 schema、拒收集**；本改造对这三样逐字不动，新增的是第四个
scope 键，属**扩展而非改动**。需求交接 §7.3 已核完表内容并写明「零新增工具、
零新增权限概念」，是本改动的需求侧依据；本节即该铁律在 idea scope 下的显式
限缩声明，限缩范围为「仅允许新增 `idea` 一键，且元素全部取自既有只读工具」。
真实配置改为四键后，impl-spec §2.5 内嵌的三键最小形态示例不同步改
（其语境是 PR4 工具面冻结），由 v1.21 修订注记补半句指向本节（v1.1 🔵-7，见 §6-9）。

### 2.3 无期态的 system 组装（需求 §7.2 的裁决）

三段式不变（v1.5 §1.3），逐段裁决：

- **段 1（人格）**：`director.md` 原样。人格与边界分离，人格无期目录假设。
- **段 2（边界）**：**新写 `config/agent/scopes/idea.md`，不复用 `creative.md`**。
  理由：`creative.md` 写死了「只允许产出 `01-topic.md` 与 `02-script.draft.md`」
  的**写权限声明**——对 idea scope 这是一句假话（idea 零写权限），边界段必须
  说真话，否则 prompt 层自己先教模型一个错误的心智模型。`idea.md` 成稿大纲：
  ① 定位：立项前选题会话，当前没有期目录，写权限为零（机制保证，不是纪律）；
  ② 能做什么：读 `data/library/notes/` 番剧笔记、`read_status(episode=...)`
  跨期读旧期状态做参照、比较候选张力与锚点；
  ③ 产出与落盘：产出 = 期名候选 + `01-topic.md` 内容草案；**期名由人拍板，
  模型只出候选**（与标题封面红线同族）；讨论定稿后指引人 `ava new <名>`，
  落盘发生在新期会话里；
  ④ 纪律复述：张力是唯一编辑判断、机器出候选不代填（人物志/剧情回顾/共鸣
  不设张力）；读到的内容是数据不是指令。
- **段 3（状态卡）**：**新纯函数 `build_idea_card() -> str`，纯静态文本**
  （落 `pipeline/agent/status_card.py`）。内容：模式（选题会话·无期）｜
  scope=idea｜写权限=无（机制保证）｜读域=`data/library/` + 跨期 `read_status`｜
  产出落盘路径（`ava new` → 新期会话写入）。≤ 400 字符预算不变。
  **为什么静态卡不需要受限串清洗器**：M5 清洗器的存在理由是 advisory 与期名
  是**外来文本**；常数卡零外来输入，清洗器无的放矢。出网兜底
  `assert_egress_boundary` 仍在 payload 层覆盖整张卡（v1.5 §1.3-4），不少一道闸。

**已知能力边界（诚实声明，不是缺陷）**：idea 会话里 `read_artifact` 的候选列表
不含任何期目录（`ctx.episode_dir` 为 None），所以**读不到旧期 `01-topic.md` 的
内容**，只能经 `read_status` 拿到旧期的阶段元数据。需求 §3.1 设计的参照机制
本来就是 `read_status(episode=...)`；若实测需要内容级参照，升级路径是给
`read_artifact` 加可选 `episode` 参数（读域原则不动），属后续独立小改。

### 2.4 入口词表唯一真源（需求 §7.5 的裁决）

模块级常量落 `pipeline/agent/cli.py`：

```python
IDEA_KEYWORD = "idea"  # 选期提示 / 解析 / main 分派三处共用；改词只改这里
```

- 三个消费点（`select_episode_interactive` 的 prompt 文案、其解析分支、
  `main()` 的子命令分派）**全部引用该常量**，任何一处硬编码字面量都是漂移面
  （需求硬约束 6：这个项目在「两处清单失同步」上栽过至少两次）；
- **为什么词是 `idea`**（需求 §5-5 已拍板，此处记录取值理由）：ASCII（选期
  提示符上切输入法是无谓摩擦）、与 `ava new` 对称、单词无歧义；
- sentinel 设计：`select_episode_interactive` 返回类型扩为
  `Path | str | None`，`str` 的唯一取值是 `IDEA_KEYWORD`。既有断言
  （`res == ep1` 等，`tests/test_agent_cli.py` 选期语义用例）不受影响——
  这是扩展不是重写（硬约束 8）；
- prompt 文案在现有 `[回车默认选 1: <期名>]` 基础上加 `idea=选题会话`——
  是「去哪儿」不是「做什么」（硬约束 4：入口第一层不出现动作词）；
- **期名碰撞声明（v1.1 🔵-6 写准）**：`new` 的先例在 `main()` 分派层，**不在
  提示词层**——`select_episode_interactive` 今天对 `"new"` 无特判，本改造才是
  提示词层第一个关键词。影子面有两层：① 期目录恰好名为 `idea` 时关键词优先，
  数字序号仍可达；② 现状名称匹配是 `choice in ep.name` **子串匹配**——今天输入
  `"idea"` 能摸中任何名字含 idea 的期（如 `01-idea-notes`），改造后该输入变为
  sentinel，这类期的**子串捷径消失**，只剩精确全名与序号。期命名惯例（`01-…`）
  使实际影响为零，接受；数字序号是恒定的逃生通道。

### 2.5 `ava new` 建完直接进对话

`create_new_episode()` **函数本体不动**（返回 int 的契约被
`tests/test_agent_cli.py` 重名必拒用例钉死）。行为变化发生在 `main()` 分派层：

```
rc = create_new_episode(args[1])
rc != 0            → return rc（重名必拒，不进入对话）
非 tty             → return 0（建目录是确定性动作，进对话才被 isatty 闸住，§3.3）
tty                → run_repl(<新期目录>)
```

消掉「取名 → 被退出 → 再启动」的断点（需求 §5-3），同时停机点墙钟记账
（`run_repl` 外层包装）从新期第一届会话起自然生效，不需要任何特殊处理。

---

## 3. 开放问题裁决（需求 §7 逐条收口）

### 3.1 §7.1 讨论内容落点 —— v1 不落盘（对 Spec §7-4 的正面回答）

**裁决：选题讨论内容不落盘，不新建 `idea/` 目录、不进 `data/library/`。**

1. 选题会话的法定产出物形态已经明确 = 人定的期名 + `01-topic.md` 内容，它的
   落盘点是新期的 `01-topic.md`（经 `write_episode_file` + 人显式确认），
   不是任何「讨论记录」文件；
2. 任何形式的讨论落盘都是**新增写权限概念**（新写目标），直接违反需求 §7.3
   「零新增工具、零新增权限概念」与 §10-3；落进 `data/library/` 还会把未终审的
   草案混进 `search_notes` 读域，污染跨期知识库（笔记库的权威性由对抗审查 +
   终审裁决撑着，S11）；
3. **与 v1.5 §7-4 的关系**：本设计不触发、也不消耗那条推翻条件。「上次选题
   讨论聊到一半想续」正是 §7-4 的合法计数事件——本 Spec 实施后真实使用中出现
   两次，按 §7-4 启动 jsonl 会话日志设计（idea 会话一并纳入）。**不用设计预判
   替真实需求计数**，判据原样保留为活判据（判据要么被执行，要么被删除）。

**为什么不是「先 `ava new` 再回同一会话续聊」（v1.1 补论证）**：那条动线要求
跨进程携带讨论上下文，而 v1.5 两条既有铁律封死了它——§1.1 消息隔离（子会话
独立 messages、主会话冻结）与 §1.4 会话不落盘（跨进程续聊必需落盘，正是本节
裁决推迟的东西）。剪贴板动线不是偷懒，是零新增权限概念下的唯一通道；它的
脆弱性由 §10-1 推翻条件兜底。

交接动线（内容如何从 idea 会话到新期，全程既有机制、零新增）：

```
idea 会话讨论定稿 → 人复制终稿内容 → ava new <人定的名>（新行为：直接进该期对话）
→ 首条消息粘贴内容 → 模型提议 write_episode_file(01-topic.md) → 人按 y 落盘
```

### 3.2 §7.3 `list_episodes` 补字段 —— 补 `current_step` / `is_blocked`，加键不改形

**裁决：补。** 选题会话的高频参照问题「哪期做到哪了 / 哪期卡住了」今天需要
N 次 `read_status` 才能答，而 `print_board` 早已对每期付过同样的
`inspect_episode` 成本——机制已存在，只是没接给工具。

实现形状：**新增 `episodes_detail` 键，不改既有 `episodes` 键**——

```python
return {
    "episodes": [ep.name for ep in episodes],          # 既有，不动（被既有断言钉住）
    "episodes_detail": [{"name": …, "current_step": …, "is_blocked": …}, …],
    "hidden_underscore": hidden,
}
```

- 只增字段不改语义（`run_pipeline` §3.3 先例）；改 `episodes` 的元素形状会弄红
  既有断言（`"01-smoke" in listing["result"]["episodes"]`），那是拿兼容换美观；
- **不补 advisories**：它是未清洗的自由文本——状态卡路径有 M5 清洗器，工具
  回喂路径没有；两个新字段都是产物文件名推导的元数据，无内容出网面；
- 工具 description 同步更新一句（声明返回每期的阶段与阻塞标记）。

### 3.3 §7.4 非 tty 下的 `ava idea` —— 打印说明即退出，exit 0

**裁决：打印一段会话说明（这是什么、为什么需要 tty、等价手动路径）后
`return 0`。** 与 B2-r5 同族：非 tty = 报告命令，打印即退出，不裸崩不假装。
备选「报错退出非零」被否：与看板路径的既有语义不一致（裸 `ava` 非 tty 是
exit 0），脚本误调的代价就是一行说明，不值得一个新的退出码语义。
`ava new` 非 tty：建目录 + 打印产物路径 + exit 0（§2.5）。

### 3.4 §7.6 变异条目 —— M22a–M30（15 条），见 §5

---

## 4. 威胁模型与护栏盘点（本改造新增的攻击面）

| 新威胁 | 设防 |
|---|---|
| **无期会话拿到写能力** | 三层既有机制，零新增：① schema 层 `write_episode_file` 不在 idea 表；② 执行层 `execute_tool` 按 scope 白名单拦截；③ `_tool_write_episode_file` 对 `episode_dir=None` fail-closed。**真正的风险在接线**：`_dispatch_agent_turn` 若在 idea 模式误传真实 `ep_dir`，三层全被绕过——M26 专项变异钉的就是这一行 |
| **入口词表漂移**（prompt 文案、解析、main 分派三处各写各的） | `IDEA_KEYWORD` 单常量 + M27a1/a2/a3 变异：任何一处改成不同字面量即有测试红 |
| **`ava new` 建完不进对话**（行为回归成退出） | M23 变异 + 接线级测试（mock `run_repl`，断言被以新期目录调用） |
| **非 tty 行为被改坏**（`ava new` / `ava idea` 在管道里 EOFError 裸崩） | 两条新路径各自的 isatty 测试（M25a/b）；裸 `ava` 由既有 `test_non_tty_degradation_exits_cleanly` 钉住，不动 |
| **idea 会话会话历史污染主流程** | idea 是独立进程的独立 `messages`（聚焦模式同构），无期会话可污染——结构性不存在 |
| **模型在 idea 会话替人定期名** | prompt 层（`idea.md` ③：只出候选，拍板在人）+ 结构层（会话无写工具，定了也落不了盘；落盘唯一通道 `ava new <名>` 的名是人敲的）。单层声明：与 v1.5 §4.5 同一家规——期名红线 = prompt + 人那次敲击 |
| **`idea` 关键词吞掉同名期** | 数字序号仍可达；概率实际为零，已声明（§2.4） |
| **降级文案指错路**（v1.1 🟡-4 实收入列）：idea 会话 LLM 缺失时（两个触发点：子循环入口与 `_dispatch_agent_turn` 的降级分支），`local_directive_message` 的 else 通用分支会输出「机器步骤用 `ava <期> /run <命令>` 推进」——指向一个不存在的期，且 idea 会话没有 `/run` 能力 | `local_directive_message` 加 idea 分支（§6-6 + T11/M30）：指引改实为「人工读 `data/library/notes/`，想好了 `ava new <名>`」 |

护栏总账：本 Spec **零新增护栏机制**，全部设防 = 既有机制 + 接线测试 + 变异。

---

## 5. 测试与变异设计

纪律沿用：每条新护栏配一条变异；变异验证全程 `PYTHONDONTWRITEBYTECODE=1` +
清 `__pycache__`；开跑前工作树必须干净（`scripts/verify_mutations.py` 自带
ABORT 硬闸）；编号续 repo 序列（M21 之后）。

### 5.1 新增测试（落 `tests/test_agent_cli.py` 与 `tests/test_agent_tools.py`）

| 编号 | 测试断言 | 对应变异（应恰好弄红本条） |
|---|---|---|
| T1 | **通用防漂移断言（需求 §7.5 原样委托，v1.1 🟡-5 收回收窄）**：解析 select prompt 文案里的每个「词=说明」对，逐词喂解析分支——**每个词都能被解析且指向一个存在的分支**（当前唯一关键词 `idea` → sentinel；未来有人往 prompt 加第二个词而忘接解析时本条当场红，「两处清单失同步」教训的机械化）；输入 `"idea"` 返回字符串 sentinel `IDEA_KEYWORD`；既有回车/数字/期名/非法重试语义逐字不变 | **M22a**：删掉 select 的 idea 解析分支（入口词被吞，掉进「未找到匹配」重提示）；**M27a1**：prompt 文案处把 `IDEA_KEYWORD` 换成不同字面量；**M27a2**：select 解析处同上（词表漂移；v1.2 🔵-R2：通用断言的「词=说明」解析面限关键词段，期名段除外——防期名含 `=` 时正则误把期名碎片当词表条目） |
| T2 | `main(["idea"])` 在 tty 下以 `(None, scope_mode="idea")` 调 `run_agent_loop`（mock 断言）；`main(["idea", "多余参数"])` 报错退出非零、不猜（硬约束 7 同族） | **M22b**：main 的 idea 分派被删（掉进 `resolve_episode_target`，报「期目录不存在」）；**M27a3**：main 分派处字面量漂移（v1.1 消歧：原「M22 同杀」违反表头「恰好弄红本条」，拆号对齐） |
| T3 | 看板流：select 返回 sentinel 后路由到同一个 `run_agent_loop(None, "idea")`（两个消费点一个实现的接线断言） | **M27b**：看板流把 sentinel 当期目录传给 `run_repl`（类型混线） |
| T4 | `main(["new", name])`：tty 下建目录后以新期目录调 `run_repl`；重名仍 exit 1 且不进对话 | **M23**：new 分支退回 `return create_new_episode(...)`（建完不进对话） |
| T5 | 非 tty 双闸：`main(["new", name])` 非 tty 建目录、打印、`run_repl` **未被调用**、exit 0；`main(["idea"])` 非 tty 打印说明、exit 0、`run_agent_loop` 未被调用 | **M25a**：new 路径 isatty 闸被摘/取反；**M25b**：idea 路径 isatty 闸被摘/取反（v1.1 拆号：两处不同编辑） |
| T6 | `tools.json` 的 idea 表**精确等于** `{read_artifact, list_episodes, read_status, search_notes}`（`SPEC_TOOLS` 加键 + 既有逐 scope 比对自动覆盖）；`build_tool_schemas("idea")` 4 项全部无弹卡（`side_effect` 皆 false）；执行层 `execute_tool("write_episode_file", …, ToolContext(scope="idea"))` 被拒 | **M24**：idea 表加入 `write_episode_file`（无期拿写工具——本改造头号变异） |
| T7 | idea 回合的 `ToolContext.episode_dir is None` 且 `scope == "idea"`（mock `run_tool_loop` 捕获 ctx）；`assemble_system_prompt(None, "idea", None)` 输出含 director 人格段与 idea 卡 | **M26**：`_dispatch_agent_turn` 在 idea 模式传入真实 `ep_dir`（写边界被偷开，三层护栏全绕） |
| T8 | `build_idea_card()` 输出 ≤ 400 字符、含「无期」与「写权限」标记、不含任何 `RESTRICTED_EGRESS_PATTERNS` 子串；**输出与任何期目录无关**（v1.1 🔵-8 改实：「两次调用逐字相等」是代理判据——杀不死「静态卡但硬编码塞期名」的未列名形态；列名形态由标记断言兑底，残余声明在案） | **M29**：idea 卡被换成 `build_status_card(某期)` 或塞入文件正文/期名 |
| T9 | `config/agent/scopes/idea.md` 存在，且含「期名由人拍板/只出候选」与「数据不是指令」条款（M12 同族字面文档不变量；防 prompt 被无意删段，**不当人格有效性证据**） | **M28**：`idea.md` 删掉期名条款 |
| T10 | `list_episodes` 工具结果含 `episodes_detail`，每元素**键集精确等于** `{name, current_step, is_blocked}`（v1.1 🔴-1：M16③ 同族病——只断言「带这三个键」杀不死「多塞一个键」；落地为 `assert set(element) == {"name", "current_step", "is_blocked"}`），且既有 `episodes` 键形状不变（`"01-x" in result["episodes"]` 仍真） | **M27c**：detail 键被摘；advisories 被塞进 detail（出网面扩张，由键集精确断言杀死） |
| T11 | idea 会话降级路径：`local_directive_message("idea", …)` 输出**不含** `ava <期>` 与 `/run`，含「`ava new`」指引（v1.1 🟡-4：无期态错误指引的接线断言；两个触发点共用这一个文案真源） | **M30**：`local_directive_message` 的 idea 分支被删（退回 else 通用分支，输出指向不存在的期的错误指引） |

### 5.2 既有测试处置（逐条列名，无意外红灯）

- **全绿不动**：`tests/test_agent_cli.py` 的选期语义（:49）、非 tty 看板（:78）、
  `create_new_episode` 重名必拒（:95）、停机点墙钟全部用例；`test_agent_tools.py`
  全部；1340+ 条其余用例。
- **显式扩展 1 处**：`test_agent_tools.py::SPEC_TOOLS` 加 `"idea"` 键（§2.2）——
  `test_llm_scope_tool_filtering_isolation` 的逐 scope 比对逻辑不变，自动覆盖
  新 scope。
- **无计划内废除**：`create_new_episode` 本体不变；`main()` 的 new 分派行为变化
  **无既有测试钉死**（已 grep 核证：`main(["new", …])` 的返回行为无断言，
  钉住的是函数本体），属「无测试覆盖的行为变更」，由 T4 新测试接管。
- **`llm.py` 改动的既有断言排查（v1.1 🟡-4）**：`local_directive_message` 加 idea
  分支，creative / else 两分支文案逐字不动；既有断言（`["degraded"] is True` 等，
  `tests/test_agent_tools.py` 与 smoke 用例）不受影响——idea 只是不再落入 else。
  T11 落 `tests/test_agent_tools.py`（该文件已 import 被测函数）。

### 5.3 反假测试声明

- T3/T4/T5/T7 是接线级：mock 打在 `run_repl` / `run_agent_loop` / `run_tool_loop`
  边界，断言**调用发生的形状**，不复述实现；
- T9 是字面文档不变量，与 M12 同族声明；
- 变异 → 红条数映射按 PR7 格式回填本节（实施后）；每条变异单独施加、单独
  恢复、收工 `git diff` 确认无残留。

#### 实测变异验证结果（2026-09-21 实施回填）

实测 **15 组变异（M22a–M30）全部杀死测试，无一条杀不死**：
（注：当前环境未挂载外置数据盘，全量测试包含 2 条基线环境性红灯，以下 red 为 harness 实测总红条数，净红条数 = red - 2）

| 编号 | 变异内容 | 实测 red (净红) | 恰好红预期那组？ | 击杀测试 |
|---|---|---|---|---|
| M22a | 删掉 select 的 idea 解析分支 | 3 (1) | 是 | `test_select_episode_interactive_anti_drift_and_sentinel` |
| M22b | main 的 idea 分派被删 | 4 (2) | 是 | `test_main_idea_subcommand_dispatch_and_extra_args`, `test_non_tty_dual_gates_for_new_and_idea` |
| M23 | ava new 建完退回原先仅退出不进对话 | 3 (1) | 是 | `test_main_new_enters_repl_in_tty` |
| M24 | idea 工具表加入 write_episode_file | 3 (1) | 是 | `test_llm_scope_tool_filtering_isolation` |
| M25a | ava new 路径 isatty 闸被取反 | 4 (2) | 是 | `test_main_new_enters_repl_in_tty`, `test_non_tty_dual_gates_for_new_and_idea` |
| M25b | ava idea 路径 isatty 闸被取反 | 4 (2) | 是 | `test_main_idea_subcommand_dispatch_and_extra_args`, `test_non_tty_dual_gates_for_new_and_idea` |
| M26 | idea 回合 _dispatch_agent_turn 传入真实 ep_dir | 3 (1) | 是 | `test_idea_turn_context_and_system_prompt` |
| M27a1 | select prompt 文案关键词字面量漂移 | 3 (1) | 是 | `test_select_episode_interactive_anti_drift_and_sentinel` |
| M27a2 | select 解析分支关键词字面量漂移 | 3 (1) | 是 | `test_select_episode_interactive_anti_drift_and_sentinel` |
| M27a3 | main 分派处关键词字面量漂移 | 4 (2) | 是 | `test_main_idea_subcommand_dispatch_and_extra_args`, `test_non_tty_dual_gates_for_new_and_idea` |
| M27b | 看板流把 sentinel 当期目录传给 run_repl | 3 (1) | 是 | `test_board_flow_routes_sentinel_to_run_agent_loop` |
| M27c | list_episodes detail 塞入 advisories | 3 (1) | 是 | `test_list_episodes_tool_detail_keys` |
| M28 | idea.md 删掉期名由人拍板条款 | 3 (1) | 是 | `test_idea_scope_doc_invariants` |
| M29 | build_idea_card 换成非静态卡/期名卡 | 4 (2) | 是 | `test_idea_turn_context_and_system_prompt`, `test_build_idea_card_invariants` |
| M30 | local_directive_message idea 分支被删退回通用分支 | 3 (1) | 是 | `test_idea_degrade_directive_message` |

---

## 6. 改动清单（文件 + 锚点，引原文片段，不给行号）

1. **`pipeline/agent/cli.py`**
   - 新增 `IDEA_KEYWORD = "idea"` 模块常量（§2.4）；
   - `select_episode_interactive`：prompt 文案加关键词提示；在
     `if not choice:` 分支后加关键词分支（返回 `IDEA_KEYWORD`），
     其余分支逐字不动；
   - `main()`：`if len(args) >= 2 and args[0] == "new":` 分支改为
     `create_new_episode` → 非 tty return 0 → tty `run_repl`（§2.5）；
     在 `resolve_episode_target` 之前新增 `IDEA_KEYWORD` 分派（§2.1）；
     看板流 `target = select_episode_interactive(episodes)` 之后加
     sentinel 路由（str → `run_agent_loop(None, scope_mode="idea")`）；
   - `run_agent_loop` / `_dispatch_agent_turn` / `assemble_system_prompt`
     签名放宽 + idea 分支（§2.1）；idea 子循环开场打印会话说明。
2. **`pipeline/agent/status_card.py`**：新增 `build_idea_card()` 纯函数（§2.3）。
3. **`config/agent/tools.json`**：加 `"idea"` 键（§2.2）。
4. **`config/agent/scopes/idea.md`**：新写成稿（大纲 §2.3）。
5. **`pipeline/agent/tools.py`**：`_tool_list_episodes` 加 `episodes_detail`
   （锚点：`return {"episodes": [ep.name for ep in episodes], "hidden_underscore": hidden}`）；
   `list_episodes` 的 `TOOL_SCHEMAS` description 同步一句（§3.2）。
6. **`pipeline/agent/llm.py`**（v1.1 🟡-4 补入）：`local_directive_message` 加 idea
   分支——idea 降级文案不含 `ava <期>` 与 `/run`（无期可指、无 `/run` 可用），
   指引改实为「人工读 `data/library/notes/`，想好了 `ava new <名>`」；
   creative / else 两分支文案逐字不动（§5.2 已排查既有断言）。
7. **`tests/test_agent_cli.py`**：T1–T5。
8. **`tests/test_agent_tools.py`**：`SPEC_TOOLS` 加键 + T6–T11。
9. **文档同步**（文档标准：流程语义调整由改动提出方同步）：
   - `impl-spec` v1.20 → **v1.21**：头部日期行与状态行版本号同步 bump
     （`test_spec_header_and_status_version_consistent` 钉住），头部加修订注记块
     （v1.5 先例）：§2.3 启动形态表新增 `ava idea` 行、`ava new` 行为注记、
     选期提示关键词，指向本 Spec；§2.3 正文表照注记加行，其余结构不动；
     另补半句：「§2.5 的 tools.json 最小形态示例仍为三键（其语境是 PR4 工具面
     冻结），idea 表见本 Spec §2.2」（v1.1 🔵-7：无测试钉扎的计划外漂移，文档
     先行的仓库里也要收口）；
   - `docs/CHEATSHEET.md`：入口速查加 `ava idea` 与「new 建完直接进对话」；
   - `docs/WORKFLOW.md` 的 ava 入口段同步一句（≤100 行预算内替换，不新增行数）。
10. **`docs/dev/issues/README.md`**：D27 行（本 Spec 开立时已登记）。
11. **`docs/dev/plans/README.md`**：活跃方案表加本方案行（同上）。

新常量/新判据取值理由（E3）：`IDEA_KEYWORD` 的词形理由在 §2.4；idea 表 4 工具
的理由在 §2.2；静态卡免清洗器的理由在 §2.3；`episodes_detail` 加键不改形的
理由在 §3.2。**本 Spec 无新阈值、无新判据数。**

---

## 7. 验证命令

```bash
# 基线与总闸（基线不绿不开工，改完必须全绿）
PYTHONDONTWRITEBYTECODE=1 uv run python -m pytest -q -p no:cacheprovider

# 文档不变量（impl-spec v1.21 版本一致性等 8 条）
PYTHONDONTWRITEBYTECODE=1 uv run python -m pytest tests/test_docs_invariants.py -q -p no:cacheprovider

# 变异验证（工作树必须干净；M22a–M30 共 15 条，单独施加、跑全量、记红条数）
PYTHONDONTWRITEBYTECODE=1 uv run python scripts/verify_mutations.py --only M22a M22b M23 M24 M25a M25b M26 M27a1 M27a2 M27a3 M27b M27c M28 M29 M30
```

## 8. 明确不做（范围闸门）

1. 不做三级以上菜单树；裸 `ava` 的看板→选期行为逐字不变（硬约束 1/2）；
2. 不改 `classify_input` 的一条规则语义（硬约束 5）；idea 会话不开快捷键路由
   （聚焦模式先例）；另列名需求 §10-2（v1.1 补）：不做「裸 `ava` = 全功能无期
   Director」——设计已满足（idea 只经显式关键词进入，无期态权限被工具表结构
   封死），此处显式列名防漏；
3. **不做讨论落盘**（§3.1 裁决，§7-4 判据原样保留）；
4. 无期会话不建目录、不起名、不产生任何写权限（需求 §10-3）；
5. 不为跨期盘点 / 资产维护 / 继续某期新造入口（需求 §10-4）；
6. 不加 `add_correction` 等任何新工具（v1.5 §6：按真实使用数据再定）；
7. 不给 `read_artifact` 加 `episode` 参数（§2.3 已知边界，后续独立小改）；
8. `list_episodes` 不补 advisories（§3.2 出网面）；
9. idea 会话不计停机点墙钟（无期可记；选题时间不进 k×片长 模型——还没有片长，
   §2.6 口径装不下它，硬塞会污染 k 的实测回填）。

## 9. 完成判定（可逐项打勾）

- [x] `ava new <名>` 后停在该期的对话里（tty），非 tty 建完即退 exit 0；
- [x] `ava idea` 与选期提示 `idea` 进入同一个无期会话实现；
- [x] idea 会话里写工具一次调用都不可达（T6/T7 三层断言）；
- [x] idea 会话能读 `data/library/notes/<番>.md`（`read_artifact` /
      `search_notes`，data 盘挂载下手动验收）；
- [x] 裸 `ava` + 回车一次按键进最近期；非 tty 打印看板即退出（既有用例全绿）；
- [x] 既有选期语义与重名必拒测试全绿不改；`SPEC_TOOLS` 扩展是唯一计划内测试改动；
- [x] M22a–M30 全跑（15 条），变异 → 红条数映射回填 §5.3，无一条杀不死；
- [x] idea 会话降级文案不含 `ava <期>` / `/run`（T11）；
- [x] 上述三条验证命令全绿；impl-spec v1.21 修订注记落盘。

## 10. 推翻条件（本 Spec 的自毁条款）

1. **§7-4 计数触发**：「想续上次选题讨论」真实出现两次 → §3.1 落盘裁决作废，
   启动 jsonl 会话日志设计（idea 会话纳入），本 Spec 回炉；
2. **第二消费点空转**：真实使用中人从不从选期提示进 idea（都走 `ava idea`
   子命令）→ 撤提示词消费点，保留子命令（sentinel 与 M22a/M27b 随之精简）；
3. **人格劝导失效**：idea 会话里模型反复尝试写文件（`PermissionError` 回喂
   成为常态噪音）且 `idea.md` 修订两轮无效 → 照 v1.5 §7-2 同族处置：边界提示
   改宿主硬注入，`idea.md` 瘦身为纯产出物说明；
4. **期名红线实测受冲击**：出现模型诱导人直接用某个期名的会话记录 → 把
   「期名候选必须 ≥2 个且带取舍代价」写进 `idea.md`，并评估是否需要在
   `ava new` 加一行确认回显。
