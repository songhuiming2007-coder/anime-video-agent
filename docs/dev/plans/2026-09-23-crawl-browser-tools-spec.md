# Implementation Spec：网络工具内化第二批 crawl + browser（Spec 5 / ADR-0021）

日期：2026-09-23（**v0.5**，红队三轮收口 + 终局微轮全验通过；状态：**🟢 可动工**）  
上位文档：`docs/dev/plans/2026-09-22-harness-evolution-direction.md`（§2 Spec 5，§4 施工红线八条，§5 明确排除），`docs/dev/adr/0021-network-and-asset-tools-internalization.md`（全文，含升级链条款与「不做的事」），`docs/dev/adr/0020-harness-eventization-and-electron-desktop.md`（「不做的事」：不引入 guardian LLM）  
格式与契约范本：`docs/dev/plans/2026-09-22-jobs-and-events-spec.md`（Spec 2 v0.4，红队四轮收口），`docs/dev/plans/2026-09-22-approval-objectification-spec.md`（Spec 3 v0.2，红队两轮收口）  
红队一轮报告：用户粘贴（2026-09-23，未落盘；2🔴 + 7🟡 + 9🔵，总裁决「🔴 修订后复审（diff-only）」）  
红队二轮报告：用户粘贴（2026-09-23，未落盘；diff-only 复审：一轮 18 项中 16 项完整落地 + 🔵-3 不完整 + ToolContext 部分采纳认可（红队主动认错括注偏一行）；修订新引入 3🟡 + 3🔵，总裁决「🟡 修订后复审（限定范围，diff-only）」）  
红队三轮报告：用户粘贴（2026-09-23，未落盘；限定四项全验通过；🔵-R3 新增自愈分流段复审发现 2🟡 + 2🔵，总裁决「🟡 修订后复审（终局微轮，限定范围：§4.2 自愈分流段 + §2.3 补句 + MUT-16/T11 各补半句，diff-only）」）  
红队终局报告：用户粘贴（2026-09-23，未落盘；diff-only 单段落复审：三轮 4 项全验通过、零新增发现，变异矩阵 18 条终态推演成立，总裁决「🟢 可动工」）  
直接前置：`docs/dev/plans/2026-09-23-network-tools-spec.md`（Spec 4 v0.3，可动工）——scope 分组授权机制、egress 双闸、`_scrub` 清洗、`web.json` 配置纪律均以其为准复用  
**施工状态声明（如实，2026-09-23 核实）**：Spec 2 / Spec 3 / Spec 4 均为「可动工未施工」——`pipeline/jobs.py`、`pipeline/approvals.py`、`pipeline/agent/web.py`、`config/agent/web.json` **均不存在**（`pipeline/`、`pipeline/agent/`、`config/agent/` 目录实测）。本 spec 的施工依赖与降级口径见 §6。

---

## 0. 一句话设计

**重依赖进 extras，能力掩码进注册表；浏览器独立 profile，启动即审批事件。**  
新增两个零顶层重依赖模块 `pipeline/agent/web_crawl.py`（crawl：无头渲染抓取，可 stealth，对应升级链第二级）与 `pipeline/agent/web_browser.py`（browser：登录态持久化 profile 浏览器，对应升级链第三级）；重依赖（crawl4ai / playwright）声明为 `pyproject.toml` 的 uv 可选 extras，**顶层只探测不导入**（`importlib.util.find_spec`），未安装时经 tools.py 新增的**能力掩码**（capability mask，继 scope 掩码之后的第二层 schema 过滤）从 LLM 工具表隐藏，`execute_tool` 再设一道显式错误闸（不静默）；browser 的 profile 路径钉死在 `config/agent/web.json`（默认 `data/browser-profile`，守卫函数强制其必须落在 `data/` 子树内——**构造上不可能触碰主力 Chrome Default**），每次浏览器进程启动落一条 `browser_session_started` 事件（走 Spec 2 的 events.jsonl 契约）；approval 门 = 既有 `_default_approve` 逐调用人审卡（browser 不标 `side_effect: False`，fail-closed 默认弹卡），确定性规则、零新机制、无 guardian LLM。工具表 8 → 10，距 ~12 封顶余 2。

---

## 1. 红队裁决与修订纪要

> **终局微轮收口记录（v0.5，总裁决「🟢 可动工」）**：三轮限定的 4 项修订（🟡-R4/R5、🔵-R4/R5）经红队逐条独立复核全部愈合——自愈分流段语义闭合（探活优先分流 + 调用内复用卡），连带回归（MUT-17/MUT-18 杀法不变、门禁 3 兼容、变异矩阵 18 条终态逐条推演必红成立）通过，零新增发现、零夹带。四轮收口轨迹：一轮 2🔴+7🟡+9🔵（两处结构性穿透失实）→ 二轮 3🟡+3🔵（修订质量缺陷）→ 三轮 2🟡+2🔵（新增分流段语义缺陷）→ 终局全验通过。**施工前置复述（红队明示不因 🟢 豁免）**：PR1 动工前 Spec 4 必须完整落地（含其 PR2 协议键白名单化）；PR2 动工前 Spec 2 必须落地（`EventType` 加一行 `BROWSER_SESSION_STARTED`，归属按 §6.2 裁决）；`crawl4ai-setup` / `playwright install chromium` 为人工一次性前置；并行施工不允许。
> **施工提示（红队非裁决项）**：严格按 §8 PR1→PR2→PR3 推进；PR3 的 MUT-1~MUT-18 逐条验讫记录与 §9 门禁打勾是交付物，不许跳票。

### 1.3 第三轮红队裁决与修订纪要（v0.3 → v0.4，2🟡 + 2🔵 全收；原裁决「🟡 修订后复审（终局微轮，diff-only）」）

> 三轮对二轮限定四项（§6.1/T17/T12/MUT-18）与 🔵-R2 的落验结论经我方对照工作树抽查无异议；本轮 4 项指控均在 🔵-R3 引入的自愈分流段及其连带处，逐条推理复核属实后采纳。

| 编号 | 红队指控 | 裁决 | 修订动作 |
|---|---|---|---|
| 🟡-R4 | 自愈分流括注「新卡 + 新事件」的「新卡」在调用内机械不可达（`browser_action` 无审批通道，重启发生在当前调用的卡之后），与 §2.3「新启动 = 新卡」字面叠加会推出两种分歧实现 | **采纳**（签名复核属实：模块确无审批通道；一轮 🔴 同族微缩版） | §4.2 括注改为「复用当前调用的人审卡（同一已批准动作，不重复弹卡）+ 按新启动落新事件」；§2.3 事件粒度段补「调用内自愈重启不新增卡」一句 |
| 🟡-R5 | 「（或首次操作）」把存活会话上的真实超时（browser.timeout_s=60 的设计预期常态）误判为死会话——误杀健康会话 + 假重启事件 + 双倍等待 | **采纳**（采用红队推荐方案①：失败后先探活分流，约三行文本） | §4.2 分流重写：探活存活 → 直接包装「操作失败」不动会话；探活也败 → 判死重启重试；T11 死会话腿补对照断言 |
| 🔵-R4 | MUT-16 机理栏残留「无 extras 环境」旧写法措辞 | 采纳 | 同步为「sys.modules 置 None 强制 ImportError」（杀法不变） |
| 🔵-R5 | T11 死会话腿未注明异常族构造方式（无 playwright 环境无从直接构造）；T18 超时腿在自愈分流下的行为需注记 | 采纳 | T11 补 `_pw_error_types` 缝注入构造句 + 探活存活对照；T18 超时腿注「探活桩钉存活，直接包装不触发重启」 |

### 1.2 第二轮红队裁决与修订纪要（v0.2 → v0.3，3🟡 + 3🔵 全收；原裁决「🟡 修订后复审（限定范围，diff-only）」）

> 二轮对一轮 18 项的落验结论（16 项完整落地、两个 🔴 修复结构性成立、伪证伪已除、假见证已删）经我方对照工作树抽查无异议。红队就一轮 ToolContext 括注偏一行**主动认错**并认可我方澄清，§1.1 🟡-7 维持原判。本轮 6 项指控逐条独立复核属实后采纳；二轮行号差异清单 9 项中 8 项 ✓ 经我方抽核相符。

| 编号 | 红队指控 | 裁决 | 修订动作 |
|---|---|---|---|
| 🟡-R1 | §6.1 第 2 条「若未落地则顺带」死分支残留，与 §4.3②/§8 PR1 前置三处口径互打 | **采纳**（grep 实证，行 567 区域原文复核属实） | §6.1 第 2 条改确认项口径：白名单化由 Spec 4 PR2 交付，Spec 4 完整落地是硬前置，本 spec 零顺带 |
| 🟡-R2 | T17 双腿依赖「测试机未装 extras」前提：装有 extras 的环境腿①会真 import 真渲染（真浏览器 + 真出网，撞隔离铁律且红），腿②同款——成未登记的第二个环境例外 | **采纳**（推理复核属实；`sys.modules` 置 None 强制 ImportError 是标准技巧，Spec 3 T10b 已有先例（其 m2 裁决要求走 monkeypatch fixture 自动 teardown）） | T17 双腿改写为 `monkeypatch.setitem(sys.modules, "crawl4ai"/"playwright", None)` 强制 ImportError，双环境同绿、零真 import、零环境前提 |
| 🟡-R3 | T12 重定向腿 fixture 错位：getaddrinfo 一律打私网则初始 `_guard_url` 先炸，证到的是初始守卫而非落地复跑；且「删落地复跑」无配套变异 | **采纳**（触发路径推理属实——初始 URL 也过同一个被 patch 的 getaddrinfo） | T12 fixture 改**分主机钉死**（初始主机返回公网、私网字面 IP 返回其自身）；新增 MUT-18（删落地复跑 → T12 重定向腿必红）；门禁 6 挂接 MUT-18 |
| 🔵-R1 | T17 腿② execute_tool 路径前置未写明（缺合法 crawl 段会先报「crawl 段」而非「导入失败」） | 采纳 | T17 补 fixture 句（tmp web.json 含合法 crawl 段 + getaddrinfo patch） |
| 🔵-R2 | 「唯一例外是 T6（依赖 CI 未装 extras）」表述失准：T6 双腿对正确实现双环境均绿、对 MUT-3/4 双环境均红，已无环境前提 | 采纳 | §7.1 可用性铁律与 RF-8 同步简化 |
| 🔵-R3 | 死会话探活的 playwright 异常归属未写清：该触发「丢弃 + 新启动（新卡 + 新事件）」还是被包装成「操作失败」错误数据，两种写法都「符合」v0.2 文本但语义相反 | 采纳 | §4.2 流程补分流句（探活/会话异常的归属：丢弃重启，不包装）；T11 补「死会话 → 自动重启 + 新事件」腿 |

### 1.1 第一轮红队裁决与修订纪要（v0.1 → v0.2，2🔴 + 7🟡 + 9🔵；原裁决「🔴 修订后复审（diff-only）」）

> **修订方法声明**：采纳前对红队指控逐条独立复核（awk/sed/grep 对照工作树），属实才改。复核要点：`tools.py:668` 四异常捕获面确无 ImportError；`cli.py:689-699` 确无 Exception 兜底（689-712 全段复核）；`cli.py:624` target_str 推导确无 url 键；`grep -rn session.jsonl pipeline/` 零命中；pyproject.toml 实测 85 行；STANDARD.md 条款实测跨 196-199。

| 编号 | 红队指控 | 裁决 | 修订动作 |
|---|---|---|---|
| 🔴-1 | find_spec 假阳性（部分/损坏安装）→ impl import 抛 ImportError → 穿透 execute_tool/run_tool_loop/cli.py except 面 → REPL 崩溃；§2.1② 选型理由倒置 | **采纳**（机理链逐环复核属实） | §2.1② 重写为「find_spec + invalidate_caches + impl 侧 ImportError→ValueError 包装」组合方案，倒置理由改正；§3.4 新增「第五、六类异常接管」段；§4.1/§4.2 默认实现加包装条款；新增 T17 与 MUT-16 |
| 🔴-2 | playwright 异常族（Exception 直接子类）全在捕获面外；§3.4/RF-4 的「错误数据回喂」对 browser 机械不可达；crawl 侧有包装条款而 browser 侧漏写 | **采纳**（不对称漏写属实） | §4.2 新增 `_pw_error_types()`（可 monkeypatch 测试缝）与 browser_action 全路径包装条款；§3.4 同步；新增 T18（含非 playwright 异常不误包对照腿）与 MUT-17 |
| 🟡-1 | MUT-4 证伪空转：§5.2 探针从不调用 `_extra_available`/`build_tool_schemas` | **采纳**（探针原文复核属实，Spec 4 🟡-2 同族伪证伪） | §5.2 第二腿独立为 `test_capability_probe_itself_is_side_effect_free`，探针显式调用变异点，双环境杀法写清；MUT-4 机理重写 |
| 🟡-2 | `approved_via: "cli_card"` 是模块自证（模块不感知审批），T9③ 固化假见证 | **采纳**（`llm.py:212` approve 钩可选 + §4.2 docstring 自述「不感知审批」，矛盾属实） | §2.3/§3.5 删除该字段（事件只承载「启动发生」，审批凭证归 `approvals.jsonl` 记账链 + Spec 2 M1 双写）；T9③ 改断「必备四键 + 无自证字段」 |
| 🟡-3 | T14 的 browser 腿是 PR2 交付物，PR1 时点必红 | **采纳**（Spec 4 🟡-4 同族判例） | T14 拆 T14a（crawl，PR1）/ T14b（browser，PR2）；§8 两 PR 测试清单同步修订 |
| 🟡-4 | browser/crawl 重定向逐跳守卫缺位且未登记（首跳合法 30x 跳内网，人卡只见初始 URL） | **采纳**（Spec 4 §2.6③ 已点名该绕法；采纳红队「final_url 复跑守卫」低成本建议） | §4.2 流程加 navigate 落地后 `final_url` 复跑 `_guard_url`（extract_text 对当前页 URL 同款）；T12 增重定向腿；RF-9 改写 |
| 🟡-5 | 「会话史可审计」overstated：reason 无持久落点；browser 记账 target 恒空串（`cli.py:624` 无 url 键）；§4.6 预先封死最便宜修复 | **采纳**（两项事实指控均复核属实） | §2.4/RF-12 措辞降级如实；§4.5 新增② target_str 补 `url` 键一行级扩展；§4.6 相应开口；T10 补「target 含所批 URL」断言 |
| 🟡-6 | crawl 的浏览器二进制前置缺失（crawl4ai 底层渲染需二进制，上游安装器自带自动下载，与纪律有未裁决张力） | **采纳** | §2.1①/§8 PR1 前置/RF-2 补 `crawl4ai-setup` 人工步骤；裁决：人工执行安装命令，ava 代码零触发上游自动下载 |
| 🟡-7 | 行号失实组（pyproject 85 行/apple 43-48/STANDARD 196-199/KeyError 448-452/atomic_write 118-127/ToolContext/payload 键数） | **采纳 5 项；部分采纳 1 项** | 前五项复核属实并修正；ToolContext 一项**部分采纳**：awk 实测 `@dataclass` 在 317、`class` 在 318，v0.1「317-331」含装饰器口径本成立（红队括注「316 @dataclass」反而偏一行），但正文未注明 class 行，精确化为「317-331（@dataclass 317，class 318）」；「payload 五键」随 🟡-2 删字段统一为「必备四键 + pid 可缺省」。附表全表重核 |
| 🔵-1 | ADR-0021「不注册进工具表」与 mask-don't-remove 的字面张力未登记 | 采纳 | §2.1③ 补解释裁决：ADR 语境「工具表」= LLM 可见 schema 清单 |
| 🔵-2 | `importlib.invalidate_caches()` 未提 | 采纳 | §2.1② 探测前加一行 invalidate_caches |
| 🔵-3 | 硬依赖下「若 Spec 4 未落地则顺带」是死分支 | 采纳 | §4.3②/T14a 删死分支，Spec 4 完整落地为硬前置 |
| 🔵-4 | §4.1/§4.2 import 代码块漏 `json`（§5.1 白名单已列） | 采纳 | 两个 import 块补 `import json` |
| 🔵-5 | 无显示环境（SSH/云主机）headed=true 不可启动未登记 | 采纳 | 新增 RF-13（headed 可配 false，不发明 Xvfb） |
| 🔵-6 | cookie at-rest 与 profile 目录权限未声明 | 采纳 | §2.2 补 chmod 0700 + 平台继承行为声明；新增 RF-14 |
| 🔵-7 | T10 直调 `_default_approve` 的降级写法使「零调用/零事件」腿恒真空转 | 采纳 | T10 限定 live-loop 路径（patch `llm.urllib.request.urlopen`，Spec 4 T17② 同款接线） |
| 🔵-8 | MUT-5 变异运行时 T2 撞真实 DNS，变异规程自身违反隔离铁律 | 采纳 | §8 PR3 变异规程补「临时 patch getaddrinfo 或断网运行 + 验讫注明」 |
| 🔵-9 | persistent context 无公开 `.pid`，「五键齐全」若含 pid 则恒假 | 采纳 | §3.5/T9③/门禁 3 统一口径：必备四键 + pid 可缺省（约，施工时先实测再定断言形态） |

---

## 2. 关键设计决策（含代码现状证据）

### 2.1 决策 1：crawl extras 的技术形态——optional-dependencies + `find_spec` 探测 + 能力掩码（正面回答预审问题 1）

- **现状与约束**：
  - `pyproject.toml:39` 已有 `[project.optional-dependencies]` 段（`apple` extras 在 43-48 行、`dev` 在 51 行），uv extras 是仓库既有先例（apple 段注释即「不装这组则配音/ASR 不可用，其余正常跑」的降级哲学）；
  - direction §4 红线 7 与 ADR-0021 §5：crawl 的重依赖（Crawl4AI/Camoufox 级）做成 uv 可选 extras，**未安装时工具在 schema 层隐藏（不注册进工具表），而不是运行时 import 报错**；
  - Spec 4 确立的注册链：`config/agent/tools.json`（scope → 工具名白名单）→ `tools.py:431-437 tool_names_for_scope` → `tools.py:440-455 build_tool_schemas`（tools.json 里出现未注册名**当场 KeyError 不静默跳过**，448-452 行）→ `llm.py:193` 每轮构建。
- **决策内容**：
  1. **extras 声明**（`pyproject.toml` 追加两组，版本下界为约值，施工时以实测可用版本钉死并按 E3 写理由）：
     ```toml
     crawl = [
         "crawl4ai>=0.7",   # 约：无头抓取 + fit_markdown；stealth 配置字段以施工时版本文档为准
     ]
     browser = [
         "playwright>=1.40",  # 约：persistent context 承载独立 profile
     ]
     ```
     浏览器二进制不进依赖解析：`uv run playwright install chromium`（browser）与 `uv run crawl4ai-setup`（crawl——crawl4ai 的无头渲染底层自带浏览器二进制，安装步骤约：以施工时所装版本文档为准）都是**人工一次性安装步骤**（写进 §8 前置条件）；二进制缺失时工具启动显式报错（§10 RF-2），**绝不自动下载**（自动下载二进制 = 未审批的副作用；v0.2 据 🟡-6 补 crawl 侧并裁决张力：人工执行安装命令，ava 代码零触发上游自动下载）。
  2. **探测方式：`find_spec` + 缓存失效化 + impl 侧 ImportError 兜底（组合方案，v0.2 据 🔴-1 修订）**。v0.1 否决 try/import 的理由写倒了：把「装了但 import 失败」与「没装」混为一谈的恰恰是 find_spec（spec 可定位 ≠ 可导入——部分安装/损坏安装/依赖缺失时 find_spec 返回非 None 而 import 会炸）。修订后分两层各司其职：
     - **探测层仍用 find_spec**：理由收敛为一条但成立——不执行重包顶层代码（crawl4ai/playwright 顶层初始化重、拖慢每轮工具表构建、违反纯洁性纪律）。探测前调 `importlib.invalidate_caches()`（一行，🔵-2：消除同 REPL 会话内 `uv sync` 之后 FileFinder 目录缓存造成的假阴性/假阳性叠加态）。实现为 tools.py 新增单个可 monkeypatch 的探测缝：
     ```python
     def _extra_available(dist: str) -> bool:
         """可选 extras 探测：find_spec 只定位不执行（防顶层 import 重依赖污染热路径）。"""
         importlib.invalidate_caches()
         return importlib.util.find_spec(dist) is not None
     ```
     - **导入失败的兜底层在 impl**（🔴-1 的结构性修复）：`_default_crawl_fn` / `_default_launch_fn` 的函数级 import 包 `try/except ImportError → ValueError("可选依赖已定位但导入失败（部分/损坏安装），请重装对应 extras")`——ValueError 落入 `execute_tool` 捕获面（`tools.py:668`）成为显式错误数据；不修此层，则 ImportError 穿透 `tools.py:668`（四异常无它）→ `run_tool_loop`（无兜底）→ `cli.py:691-699`（仅 LLMError/PermissionError 两支）→ REPL 带 traceback 崩溃（红队机理链，我方逐环复核属实）。测试锚点：§7.1 T17。
     **不做结果缓存**——缓存是测试敌对状态（装了 extras 的环境无法模拟「未安装」），每轮探测的开销可忽略。
  3. **能力掩码（本 spec 对注册链的唯一语义增量）**：`TOOL_SCHEMAS` 条目新增宿主元数据键 `"requires_extra": "<import 名>"`（crawl → `"crawl4ai"`，browser → `"playwright"`）。**注册表常驻不变**（mask-don't-remove，Spec 4 §2.2 精神），变化在翻译层：
     - `build_tool_schemas` 在 scope 过滤之后追加能力过滤：`requires_extra` 存在且 `_extra_available(...) is False` → **跳过该条目**（schema 层隐藏，ADR-0021 §5 原文语义）；
     - `execute_tool`（`tools.py:653-670`）在 scope 闸（660-665 行）之后追加能力闸：extras 缺失 → 返回显式错误数据 `{"ok": False, "error": "工具 'crawl' 需要可选依赖（uv sync --extra crawl），当前环境未安装"}`——**显式可辨，不是静默跳过，也不是裸 ImportError**（ImportError 不在 `tools.py:668` 的四异常捕获面内，不设此闸会异常穿透）；
     - 未注册名 KeyError 行为**不变**（配置与实现分叉仍当场报错）；能力掩码只作用于「已注册但缺依赖」一种情形。
     - **ADR 字面张力的解释裁决（🔵-1，登记）**：ADR-0021 §5 原文「未安装时工具在 schema 层隐藏（**不注册进工具表**）」与本设计的「注册表常驻、翻译层跳过」存在字面张力。裁决：ADR 语境中的「工具表」指 **LLM 可见的 schema 清单**（`build_tool_schemas` 输出），不是 `TOOL_SCHEMAS` 注册表本体——注册表常驻是 Spec 4 §2.2 mask-don't-remove 的既有口径，两者在「模型看不见」这一实质上一致。
  4. **与既有 drift 测试的相容**：`tests/test_agent_tools.py:311-316 SPEC_TOOLS` 与 `:405`、`:546-578` 的逐字断言在加入 crawl/browser 后会随环境有无 extras 而漂移——本 spec 要求这些断言的期望清单统一改为经 `_extra_available` 过滤计算（或 monkeypatch 探测缝钉死可用性），杜绝「同一代码在两种环境一绿一红」（§7.1 T15）。
- **明确不做**：不做 entry-points/插件发现（ADR-0021「不做的事」：工具表静态注册）；不做运行时 `pip install` 自动补装；不把 Camoufox 单独立 extra（stealth 是 crawl 的参数而非第二个工具，绕盾实现细节归 crawl4ai 配置，约）。

### 2.2 决策 2：browser profile 隔离——data/ 子树不变量，可验证性靠「构造性不可能 + 守卫单测 + argv 断言」（正面回答预审问题 2）

- **上位要求原文**：STANDARD.md:196-199「需登录态升级走 `agent_browser` 带独立持久化配置与可见窗口（`--headed --profile ~/.config/pi-browser-profile`），严禁直连主力 Chrome Default 配置（防 SingletonLock 与 Keychain 阻断）」；ADR-0021 §2「profile 路径写死在 config、不接受运行时参数覆盖」。
- **决策内容**：
  1. **路径钉死**：`config/agent/web.json` 新增 `browser.profile_dir`，默认值 `"data/browser-profile"`（相对仓库根解析；`.gitignore:2` 的 `/data` 保证登录态 cookie 永不进 git）。工具参数 schema **没有** profile 相关字段（§4.3，exact-keys 测试钉死）——模型在运行时无任何覆盖通道。
  2. **守卫不变量（单条规则，完备可测）**：`_assert_profile_isolation(profile_dir, root)`——`profile_dir` resolve 后必须是 `(root/"data").resolve()` 的**严格后代**，否则 `PermissionError`。主力 Chrome 的 profile 根（`~/Library/Application Support/Google/Chrome` 等）不在仓库 `data/` 子树内，故**碰 Default 在构造上不可能**——不需要维护一份「已知浏览器路径黑名单」（黑名单必然不全，白名单子树一条规则覆盖全部情形）。
  3. **验证方法（三层，全部确定性、零真实浏览器）**：
     - **守卫单测**（T7）：`data/browser-profile` 放行；`~/Library/Application Support/Google/Chrome/Default`、`~/Library/Application Support/Google/Chrome`、仓库根、`/tmp/x`、`data/` 自身（非严格后代）逐一断言 PermissionError；
     - **启动参数断言**（T9）：注入 fake `launch_fn`，断言实际收到的 `user_data_dir` 等于 config 钉死的 resolve 路径、`headless` 与 config 一致——证明启动用的就是钉死路径而非别的东西；
     - **schema 排他断言**（T8）：`browser` 工具 parameters 的 properties 键集合恰为 `{"action", "url", "reason"}`——无 profile/headless 入口。
  4. **不做进程枚举**：运行期枚举本机 Chrome 进程来证明「没碰主力」在 CI 上既 flaky 又是负向证明（证明不了「永远不会」），**声明不做**；隔离强度由不变量（2）承担——它不是「检测碰到没」，而是「想碰也传不进去这个路径」。人工验收可选项：`lsof` 核对运行中浏览器进程的 `--user-data-dir`（写进 §9 门禁的人工项，不进 CI）。
- **登录态的人因流程**：首次使用某站点登录态时，人在 headed 窗口里**手动完成登录**（browser 默认 `headed: true`，与 STANDARD.md:198-199 可见窗口先例一致），cookies 持久化在 `data/browser-profile/`；之后 agent 的 navigate/extract 复用该 profile。**ava 代码永不读写 cookie 本体**——cookie 属出网敏感物（ADR-0021 §2），模块只持有 profile 目录路径，不解析其内容。
- **at-rest 口径（🔵-6，登记）**：profile 目录首次创建时 `chmod 0700`（仅本人可读写）；cookie 的 at-rest 加密依赖 Chromium 对 profile 的 Safe Storage/Keychain 集成——**平台继承行为，非 ava 提供的保证**，ava 不另立加密承诺（§10 RF-14）。

### 2.3 决策 3：browser approval 门——逐调用人审卡（既有机制）+ 启动级事件落盘（Spec 2 契约），两种粒度分开定义（正面回答预审问题 3）

- **现状证据**：
  - 审批机制已存在且逐调用生效：`llm.py:212-213` 在**每次工具执行前**调 `approve(name, args)`；`cli.py:542-642 _default_approve` 的 fail-closed 分流（`cli.py:591`：`side_effect = TOOL_SCHEMAS[name].get("side_effect", True)`——**默认 True 必弹卡**）；弹卡段 `render_approval_card`（`cli.py:616`）+ `input()`（`cli.py:629`）+ 记账 `log_approval_decision`（`cli.py:636`）+ 拒绝返回 `(False, "人类拒绝执行该工具调用")`（`cli.py:641-642`）；
  - 事件契约（Spec 2 v0.4 §3.2/§4.1，未施工）：`get_publisher().emit(event_type, payload, *, episode_dir=None)` 非阻塞、fail-silent；`publisher.close()` 毒丸排空是读文件前的同步点铁律（Spec 2 §7.1 红队三轮 B2）。
- **「每次启动」的粒度定义（本 spec 的核心澄清，两个粒度不矛盾）**：
  - **审批卡粒度 = 每次工具调用**。browser 是全表最重工具（ADR-0021 §2 原文），登录态下模型每一个动作（导航到哪个 URL）都必须过人眼——逐调用弹卡是**零新机制的保守默认**（不标 `side_effect: False` 即自动获得），且每次卡的 y/n 已由 `log_approval_decision`（`status_card.py:282-315`）记账、由 Spec 2 的 M1 接线双写 `APPROVAL_RESOLVED` 事件。**不发明会话级/项目级授权缓存**（ZCode options 机制属 Spec 8 桌面端议题，本期不预留）；若逐调用卡在真实生产中证明疲劳（`decision_latency_s` 信号，`status_card.py:310`），另立 ADR 再加，不在本 spec 预埋。
  - **事件粒度 = 进程级（每次浏览器进程启动一条）**。定义：一次 `launch_persistent_context` 成功 = 一次启动。REPL 进程内 browser 会话为模块级单例，续用不重复发射；会话关闭（atexit/显式 close）或崩溃后再次使用 = 新启动 = 新卡 + 新事件。**调用内死会话自愈重启不新增卡**（复用当前调用已批准的动作授权，模块无审批通道，「调用内新卡」机械不可达；只新增启动事件——「每次启动落审批事件」的粒度承诺不变，🟡-R4）。
- **事件契约（payload schema）**：新增事件类型 `browser_session_started`（对 Spec 2 `EventType` 的 append-only 扩展，新增一行枚举值，不改既有值语义；Spec 3 §3.3「复用已冻结枚举不新造」针对的是 approval 语义复用，本条是新增观测维度，接口申请与归属见 §6.2）：
  ```json
  {
    "profile_dir": "<resolve 后的绝对路径>",
    "headed": true,
    "pid": 48123,
    "scope": "asset",
    "trigger": {"action": "navigate", "url": "https://example.com", "reason": "web_fetch 403，crawl 遇盾"}
  }
  ```
   payload **严禁**含 cookie/凭据/页面内容；url 只记导航目标。**v0.2 删除 v0.1 的 `approved_via: "cli_card"` 字段（🟡-2）**：事件由 web_browser 模块发射，而模块不感知审批（`llm.py:212` 的 approve 钩可选、宿主可整体替换）——模块自证「已经审批」是假见证，会把事件流作为审批轨迹的举证价值归零（v0.1 的 T9③ 直调零审批路径却断言该字段，亲自演示了这个问题）。审批凭证的持久载体是 `_default_approve` 的 `approvals.jsonl` 记账链（`cli.py:636`，v0.2 起含所批 URL，§4.5②）与 Spec 2 M1 的 `APPROVAL_RESOLVED` 双写。发射失败静默（Spec 2 sidecar 纪律），browser 启动永不因事件通道阻塞。
- **无 guardian LLM**：approval 门 = `_default_approve` 确定性分流 + 人，方向 §5 与 ADR-0020「不做的事」明确排除 guardian LLM，本 spec 一行相关代码不写。
- **顺序铁律**：先过卡（`llm.py:212-213` 在 execute 之前），卡拒绝则 `_TOOL_IMPLS` 根本不执行——无启动、无事件（事件语义 = 「启动发生了」，被拒的尝试已由卡记账覆盖，不双写）。

### 2.4 决策 4：crawl 与 web_fetch 的职责切分与升级链顺序（正面回答预审问题 4）

- **上位原文（STANDARD.md:196-199，已逐字核实）**：「严禁因 fetch 失败就偷懒退回水百科交差。静态获取失败必须执行工具升级链：优先升级走 `agent_crawl`（无头静默抓取，开 stealth 绕盾）；需登录态升级走 `agent_browser` 带独立持久化配置与可见窗口」。
- **升级链顺序（四级，与 ADR-0021 §1 表一一对应）**：

| 级 | 工具 | 何时用 | 何时不用 |
|---|---|---|---|
| 0 探测 | `web_search`（Spec 4） | 只有关键词、没有 URL | 已知确切 URL |
| 1 静态 | `web_fetch`（Spec 4） | 单 URL 抓净文，**默认首选**（最便宜、无浏览器开销） | 403/Cloudflare/JS 渲染页/解析为空 |
| 2 无头 | `crawl`（本 spec） | web_fetch 明确失败后的渲染抓取；`stealth=True` 仅在普通无头被盾时 | web_fetch 能成的页面（不许跳级省一步） |
| 3 登录态 | `browser`（本 spec） | crawl 也失败，或页面必须登录态/交互 | 任何 crawl 能成的页面（最重工具，启动即审批事件） |

- **不跳级的机械化落法（两级，都不发明新框架）**：
  1. **声明式**：`crawl` 与 `browser` 的参数 schema 均含**必填** `reason: string`（「为什么下一级不够用」——web_fetch 的报错原文、遇到的盾、需要的登录态）。**审计面如实声明（v0.2 据 🟡-5 降级措辞）**：会话史是 REPL 内存态（`pipeline/` 全仓无 session.jsonl 实现，grep 零命中，退出即灭），reason 的**内存可审**成立；**持久**审计面 = browser 侧卡记账（v0.2 起 target 含所批 URL，§4.5②）+ 每次启动事件（§3.5）；crawl 侧免弹卡、无记账，**无持久落点**——跳级纪律对 crawl 只有事前声明，登记 §10 RF-12。browser 的 `reason` 进 `render_approval_card` 的 args 渲染，人卡即跳级审查闸；
  2. **提示式**：工具 description 写死链位（「仅在 web_fetch 失败后使用」），web_fetch 的 403 错误文案已含升级提示（Spec 4 §3.4，「应升级 crawl」），crawl 的失败文案同样含「应升级 browser」提示（§3.4）。
- **明确不做**：不做「必须先观察到 web_fetch 失败记录才放行 crawl」的状态机校验——跨调用的证据链追踪是未经上位要求的机制发明（YAGNI），跳级纪律由 reason 声明 + 人卡 + 事件审计承担；若红队认为不够，请指出具体攻击面再议。
- **失败不静默降级**：crawl/browser 的错误一律结构化错误数据回喂（断网/超时/渲染失败/配置缺失），**严禁**降级为「凭印象回答」（水百科禁令，STANDARD.md:196-199）；egress 命中同 Spec 4 §3.4 定性——请求零发出 + 本轮 `[BLOCKED]` 交人（`cli.py:695-699` 路径，机制相同不重复展开）。

### 2.5 决策 5：browser 的 action 面——导航 + 读文，写操作一律不进本期（正面回答预审问题 5）

- **方向文档与 ADR-0021 只说了**：登录态、独立持久化 profile、approval 门、profile 路径 config 钉死。**没有授权任何写操作**。按「方向文档没说的不要扩张」，本期 action 面收敛为两个：
  - `navigate`（`url` 必填）：page.goto，返回 `{action, url, final_url, title}`；
  - `extract_text`：当前页净文提取（去 script/style），经 `_scrub` 清洗与字符封顶后返回 `{action, url, text, truncated}`。
- **明确排除（本期不做，每条写理由）**：
  - `click` / `type` / 表单提交：点击在语义上不可判定读写（一个 click 可以是翻页也可以是「确认订单」），确定性规则切不干净——写操作要进须另立 ADR 并配独立审批语义；
  - 任意 JS `evaluate`：等价于把页面控制权交给模型生成的代码，无确定性护栏可守；
  - 截图：ava 当前 LLM 通道为文本（`llm.py:125-139` 的 payload 契约无图像字段），截图无人消费（YAGNI）；桌面端 BrowserView 可见窗口呈现属 Spec 8（ADR-0021 §5 尾条）；
  - 文件下载：素材下载走 `pipeline.acquire` 人审闸门（ADR-0021 §3），browser 不提供任何内容写盘出口（同 Spec 4 §6.4 物理分离纪律；profile 目录由浏览器进程自建、ava 仅 chmod，不算内容出口）。
- **crawl 的参数面**：`url`（必填）、`reason`（必填，§2.4）、`stealth`（可选，默认 false——绕盾增加风控暴露面，默认关，显式开）。返回 `{url, final_url, markdown, truncated, stealth}`。

### 2.6 决策 6：配置扩展——web.json 加段不炸基座，各段独立校验

- **现状**：`config/agent/web.json` 尚不存在（Spec 4 未施工）；Spec 4 §3.1 规定其基座段 `search`/`fetch` 与「校验失败一律 `load_web_config → None`」语义。
- **决策内容**：
  1. `web.json` 扩展为四段（基座两段归 Spec 4，本 spec 加后两段）：
     ```json
     {
       "search": { "...": "Spec 4 §3.1" },
       "fetch": { "...": "Spec 4 §3.1" },
       "crawl": { "timeout_s": 60, "max_chars": 30000 },
       "browser": { "profile_dir": "data/browser-profile", "headed": true, "timeout_s": 60 }
     }
     ```
     取值理由：`crawl.timeout_s=60`——无头渲染整页比静态抓取慢一个量级（对照 Spec 4 的 fetch 30s 与 llm.py 的 LLM 60s，取齐 LLM 上限）；`max_chars=30000` 与 Spec 4 fetch 同值（同一上下文预算约束，不发明第二口径）；`browser.timeout_s=60` 同理（登录页/重站渲染慢）；`headed=true` 钉死 STANDARD.md:198 可见窗口先例。
  2. **独立校验、互不拖垮**：`web_crawl.py` / `web_browser.py` 各自实现 `load_crawl_section(root)` / `load_browser_section(root)`——只读 web.local.json（优先）/ web.json 中**自己那一段**，缺段/损坏/约束违例 → 返回 `None` → 该工具显式错误（「缺少 config/agent/web.json 的 crawl 段」）；基座段损坏只影响 Spec 4 两工具，crawl 段损坏不影响 browser，反之亦然。**对 Spec 4 的接口要求**（登记 §6.1）：`load_web_config` 须忽略未知顶层键（不因多出 crawl/browser 段而返回 None）——「校验必填、宽容多余」本就是配置加载的正态。
  3. `web.local.json` 覆盖语义沿用 Spec 4：整文件覆盖、不进 git（`.gitignore:8-9`）。

### 2.7 决策 7：测试接缝——fake 注入，零真实浏览器、零真实出网

- 沿用 Spec 4 §7.1 网络隔离铁律并加码：**本 spec 全部测试零真实浏览器进程、零真实出网、零真实 profile 目录**。
- `web_crawl.crawl_page(..., crawl_fn=None)`：`crawl_fn` 为 None 时调用点解析默认实现（函数内延迟 import crawl4ai + `asyncio.run`；B1 纪律——绝不做成 def 默认参数）；测试注入 fake 返回 fixture markdown 或抛指定异常。
- `web_browser`：`launch_fn` 注入缝（默认实现函数内延迟 import playwright，`sync_playwright()` + `launch_persistent_context(user_data_dir=..., headless=...)`——playwright API 形态为约，施工时以所装版本钉死）；测试注入 fake 记录 `user_data_dir`/`headless` 实参并返回桩 context。模块级 `_reset_session_for_testing()` 消灭跨用例单例污染（Spec 2 `_reset_global_publisher_for_testing` 同款先例）。
- 凡过 `_guard_url` 的用例 monkeypatch `socket.getaddrinfo` 钉死公网地址（Spec 4 红队一轮 🟡-10 铁律原文沿用：DNS 本身就是出网）。
- 事件断言一律先 `publisher.close()` 再读文件（Spec 2 同步点铁律），publisher 经 `data_root`/`AVA_EVENTS_ROOT` 定向 `tmp_path`（Spec 2 红队三轮 B3 sink 隔离铁律）。

### 2.8 决策 8：工具位次台账与 ADR 登记

- 工具表 8（Spec 4 后）→ **10**（本 spec +2），距 ~12 封顶余 2（Spec 6 acquire_propose +1 = 11，机动位 1）——与 Spec 4 §2.7 台账一致，与 ADR-0021「不做的事」的数量账吻合。
- 两工具登记 `"adr": "ADR-0021"`，自动落入 Spec 4 T12 的 ADR 存在性门禁（数字段 glob 恰中 1）。
- `crawl` 标 `"side_effect": False`（只读抓取，与 web_fetch 同级，免弹卡只回显一行）；`browser` **不标**（默认 True，fail-closed 必弹卡，§2.3）。
- `llm.py:4` docstring 工具计数随 Spec 4/5 连续修订（8 → 10；若 Spec 4 未先落地则施工时一次到位并注明）。

---

## 3. 数据契约（Data Contracts）

### 3.1 `config/agent/web.json` 新增段 Schema

| 字段 | 类型 | 必填 | 约束 | 说明 |
|---|---|---|---|---|
| `crawl.timeout_s` | number | ✓ | >0，≤120 | 无头渲染超时，默认 60 |
| `crawl.max_chars` | integer | ✓ | ≥1000 | 返回 markdown 上限，默认 30000 |
| `browser.profile_dir` | string | ✓ | 非空；resolve 后必须落在 `<root>/data/` 严格后代（§2.2 守卫） | 独立持久化 profile 路径，默认 `data/browser-profile` |
| `browser.headed` | boolean | ✓ | — | 可见窗口开关，默认 true（登录靠人在窗口里手动完成） |
| `browser.timeout_s` | number | ✓ | >0，≤120 | 导航/提取超时，默认 60 |

校验失败（缺键/类型错/约束违例）→ 各自 `load_*_section → None` → 工具显式错误；**不静默回落默认值**（tools.json「读取失败不静默扩张」同款纪律）。`profile_dir` 的守卫拒绝（§2.2）抛 `PermissionError` 而非 None——配置指向危险位置是对抗性事件，必须响。

### 3.2 `crawl` 工具契约

- **参数 schema（LLM 可见）**：`url: string`（必填，http/https）、`reason: string`（必填，非空——为什么 web_fetch 不够）、`stealth: boolean`（可选，默认 false）。
- **返回值**：
  ```json
  {
    "url": "模型请求的原始 URL",
    "final_url": "渲染落地后的最终 URL",
    "markdown": "fit markdown 净文（已经 _scrub 清洗）",
    "truncated": false,
    "stealth": false
  }
  ```
- **拒绝面**：非 http/https、私网/保留地址（复用 Spec 4 `_guard_url`）、egress 模式串（含 URL 编码形态，复用 `_normalized_for_assert` + `assert_egress_boundary`）→ PermissionError；渲染失败/超时/缺 extras/缺配置段 → 显式错误数据，文案含升级提示「按 STANDARD.md 五节升级链应升级 browser（登录态，需人审卡）」。

### 3.3 `browser` 工具契约

- **参数 schema（LLM 可见）**：`action: string`（必填，枚举 `["navigate", "extract_text"]`）、`url: string`（navigate 时必填）、`reason: string`（必填，非空——为什么 crawl 不够/为什么需要登录态）。**properties 键集合恰为这三件，无 profile/headless 入口**（§2.2）。
- **navigate 返回**：`{"action": "navigate", "url": "<原始>", "final_url": "<落地>", "title": "<页标题>"}`；
- **extract_text 返回**：`{"action": "extract_text", "url": "<当前页>", "text": "<净文，已 _scrub>", "truncated": false}`；
- **拒绝面**：navigate 的 URL 同 §3.2 守卫集；未知 action → ValueError；extract_text 在无存活会话时 → ValueError（「browser 会话未启动，请先 navigate」——不隐式启动，首次启动必须是人审过的 navigate）。
- **副作用声明**：`side_effect` 不标（默认 True）——逐调用弹卡（§2.3）。

### 3.4 错误降级契约（沿用 Spec 4 §3.4 两类两命运；v0.2 增量：第五/六类异常接管）

**第五、六类异常的接管（v0.2 新增，🔴-1/🔴-2）**：crawl4ai/playwright 的异常族（含 `playwright.sync_api.Error`/`TimeoutError`——与 builtin TimeoutError 同名不同类，均为 Exception 直接子类）与部分安装下的 ImportError 均**不在** `tools.py:668` 四异常捕获面内；不接管则穿透 `execute_tool` → `run_tool_loop` → `cli.py:689-699`（仅 LLMError/PermissionError 两支，无 Exception 兜底）→ REPL 带 traceback 崩溃。因此本 spec 强制：**impl 边界的 import 失败与 crawl4ai/playwright 异常一律在模块内包装为 ValueError**（含诚实失败文案），落入捕获面成为错误数据——「错误数据回喂模型自纠」承诺对 crawl 与 browser 同样机械可达。测试锚点 T17/T18，变异 MUT-16/MUT-17。

普通网络/渲染错误 → 错误数据回喂模型自纠；egress 命中 → 请求零发出 + 会话史污染 + 下一轮 payload 断言炸 → `PermissionError` 穿透 `run_tool_loop` → `cli.py:695-699` `[BLOCKED]` 交人（机制与 Spec 4 完全相同，不重复论证；T 用例只钉 crawl/browser 两条新入口的拦截点，不重演 live-loop 全链）。crawl 失败文案示例：  
`无头渲染失败（<原因>）：按 STANDARD.md 五节升级链应升级 browser（登录态，approval 门）；严禁静默降级为水百科（STANDARD.md:196-199）。`

### 3.5 `browser_session_started` 事件契约

字段表（落盘形态 = Spec 2 §3.3 行级契约：event_id/timestamp/episode/type/payload 五键，`sort_keys=True` 确定性序列化）：

| payload 字段 | 类型 | 约束 | 说明 |
|---|---|---|---|
| `profile_dir` | string | 绝对路径 | resolve 后的钉死 profile 路径 |
| `headed` | boolean | — | 与 config 一致 |
| `pid` | integer | >0 | 浏览器驱动进程 pid（取不到时省略该键，不填假值——playwright persistent context 无公开 `.pid` 属性，可得性为约，🔵-9；施工时先实测再定断言形态） |
| `scope` | string | asset / creative | 发起会话的 scope（pipeline/idea 到不了这里，§4.4） |
| `trigger` | object | `{action, url?, reason}` | 触发启动的首次调用参数（不含凭据） |

**口径统一（v0.2，🟡-2/🟡-7/🔵-9）**：必备四键 = `profile_dir` / `headed` / `scope` / `trigger`；`pid` 可缺省；payload 不含任何审批自证字段（原 `approved_via` 已删，§2.3）。envelope 五键（event_id/timestamp/episode/type/payload）是 Spec 2 §3.3 契约，与 payload 键数不要混写。

---

## 4. 模块接口与签名设计

### 4.1 新模块 `pipeline/agent/web_crawl.py`

```python
"""pipeline.agent.web_crawl: crawl 无头渲染抓取工具（Spec 5 / ADR-0021，升级链第二级）。

纪律：
1. 顶层零重依赖：crawl4ai 只在 _default_crawl_fn 内函数级延迟 import；
   顶层只 import stdlib + pipeline.agent.web（Spec 4 的守卫/清洗/配置复用）；
2. 出网双闸沿用 Spec 4：发送前 assert_egress_boundary（迭代 unquote 归一后）
   + _guard_url（scheme 白名单 + 私网拒连）；
3. 只读：不写任何文件；抓回内容经 _scrub 后作数据回喂；
4. 诚实失败：渲染失败如实报错并提示升级 browser，严禁静默降级水百科
   （STANDARD.md:196-199）。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from pipeline.agent.web import (  # Spec 4 模块，§6.1 硬依赖
    _guard_url,
    _normalized_for_assert,
    _scrub,
)
from pipeline.agent.tools import assert_egress_boundary


@dataclass(frozen=True)
class CrawlSection:
    timeout_s: float
    max_chars: int


def load_crawl_section(root: Path | None = None) -> CrawlSection | None:
    """读 web.local.json（优先）/ web.json 的 crawl 段，逐字段按 §3.1 校验。

    缺失/损坏/违例 → None（显式降级，不拖垮基座段，§2.6）。
    """


def _default_crawl_fn(url: str, *, stealth: bool, timeout_s: float) -> dict[str, str]:
    """crawl4ai 封装（函数级延迟 import + asyncio.run）。

    import 失败（部分/损坏安装——find_spec 假阳性路径，🔴-1）与 crawl4ai 渲染
    异常一律包装为 ValueError（含升级 browser 提示），绝不放任穿透（§3.4）。
    返回 {"final_url": ..., "markdown": ...}。crawl4ai API 形态为约（以施工时
    所装版本文档为准）；stealth → crawl4ai 浏览器配置的映射同上。
    前置：人工 `uv run crawl4ai-setup` 装浏览器二进制（🟡-6，§10 RF-2）。
    """


def crawl_page(
    url: str,
    reason: str,
    *,
    stealth: bool = False,
    section: CrawlSection | None = None,
    crawl_fn: Callable[..., dict[str, str]] | None = None,
    root: Path | None = None,
) -> dict[str, Any]:
    """流程：reason 非空校验 → load_crawl_section（None → ValueError 显式降级）→
    assert_egress_boundary(url, {"url": _normalized_for_assert(url)}) →
    _guard_url(url) → crawl_fn（None 时调用点解析 _default_crawl_fn，B1 纪律）
    → _scrub(markdown) → max_chars 截断（truncated=True）→ 返回 §3.2 契约。
    渲染异常包装为含升级 browser 提示的 ValueError（§3.4）。
    """
```

### 4.2 新模块 `pipeline/agent/web_browser.py`

```python
"""pipeline.agent.web_browser: browser 登录态浏览器工具（Spec 5 / ADR-0021，第三级）。

纪律：
1. 顶层零重依赖：playwright 只在 _default_launch_fn 内函数级延迟 import；
   顶层 stdlib + pipeline.agent.web（守卫/清洗复用）；
2. profile 路径只来自 config（web.json browser.profile_dir），运行时参数
   无覆盖通道；_assert_profile_isolation 强制 data/ 严格后代（§2.2）——
   碰主力 Chrome Default 在构造上不可能；
3. approval 门 = 既有逐调用人审卡（本模块不感知审批，llm.py:212-213 在
   execute 前问人）；每次进程启动落 browser_session_started 事件（§3.5）；
4. action 面只有 navigate / extract_text（§2.5）；模块无内容写盘出口
   （profile 目录由浏览器进程自建，ava 仅对其 chmod 0700，§2.2）。
"""

from __future__ import annotations

import atexit
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from pipeline.agent.web import _guard_url, _normalized_for_assert, _scrub
from pipeline.agent.tools import assert_egress_boundary

_ACTIONS = ("navigate", "extract_text")


@dataclass(frozen=True)
class BrowserSection:
    profile_dir: str   # 原始配置值（相对 root 解析在守卫内完成）
    headed: bool
    timeout_s: float


def load_browser_section(root: Path | None = None) -> BrowserSection | None:
    """读 web.local.json（优先）/ web.json 的 browser 段（同 §2.6 独立校验）。"""


def _assert_profile_isolation(profile_dir: str, root: Path) -> Path:
    """profile 隔离守卫：resolve 后必须是 <root>/data 的严格后代，否则
    PermissionError（配置指向危险位置必须响，§3.1）。返回 resolve 后路径。"""


def _default_launch_fn(*, user_data_dir: Path, headed: bool) -> Any:
    """playwright 封装（函数级延迟 import）：sync_playwright() +
    launch_persistent_context(user_data_dir=..., headless=not headed)。
    import 失败（部分/损坏安装，🔴-1）→ ValueError 包装，绝不放任穿透。
    API 形态为约；浏览器二进制缺失 → 显式 ValueError 提示人工
    `uv run playwright install chromium`（§10 RF-2），绝不自动下载。
    返回的会话对象需暴露 .pid（取不到则 None）/ .goto()/.extract_text()/.close()。
    """


def _pw_error_types() -> tuple[type[BaseException], ...]:
    """playwright 异常族（函数级延迟 import；未安装 → 空元组）。

    playwright.sync_api.Error/TimeoutError 是 Exception 直接子类、与 builtin
    TimeoutError 同名不同类，不在 tools.py:668 捕获面内（🔴-2）——browser_action
    对启动/导航/提取全路径用本族做 isinstance 包装为 ValueError（§3.4）。
    本函数是可 monkeypatch 的测试缝（T18：注入 FakePWError）。
    """


def browser_action(
    action: str,
    reason: str,
    *,
    url: str | None = None,
    section: BrowserSection | None = None,
    launch_fn: Callable[..., Any] | None = None,
    episode_dir: Path | None = None,
    scope: str = "creative",
    root: Path | None = None,
) -> dict[str, Any]:
    """流程：action ∈ _ACTIONS 校验 → reason 非空 → load_browser_section →
    navigate：egress 断言 + _guard_url(url)；extract_text：要求会话存活
    （不隐式启动，§3.3）→ 会话未存活则 _assert_profile_isolation（通过后
    首建目录 chmod 0700，§2.2）+ launch_fn 启动（B1 纪律解析默认实现）→
    启动成功即发 browser_session_started 事件（函数级延迟 import
    pipeline.jobs，emit 永向调用方不抛异常，Spec 2 sidecar 纪律）→
    navigate 落地后对 final_url 复跑 _guard_url（🟡-4：首跳合法、30x 跳进
    内网是 Spec 4 §2.6③ 已点名的标准绕法，浏览器内核自行跟跳，ava 侧初始
    校验覆盖不到；extract_text 对当前页 URL 同款复跑）→
    死会话自愈分流（v0.4 收口，🟡-R4/🟡-R5；S16 🟡-1 修订）：会话调用遇 playwright 族异常
    时**先探活**（订阅 `context.on("close")` 事件置 dead 标记，`probe()` 返回 `not _dead`——
    playwright 中 `context.pages` 仅返回本地 `_pages.copy()` 永不抛错，不可用于探活；严禁
    调用 `cookies()` 触碰 cookie 本体，S16 🟡-1）——探活存活 = 操作本身失败
    （真实超时是 browser.timeout_s=60 设计预期的常态，不是死会话），直接
    包装 ValueError「操作失败」，会话不动；探活也败 = 死会话：丢弃旧会话、
    按新启动重启后重试该操作一次——重启**复用当前调用的人审卡**（同一
    已批准动作，不重复弹卡；模块无审批通道，「调用内新卡」机械不可达，
    🟡-R4）+ 按新启动落新事件；重启后操作仍失败 → 包装 ValueError
    （🔴-2/🔵-R3，§3.4：操作失败才是错误数据，自愈重启不是）→
    extract_text 结果 _scrub + 封顶 → 返回 §3.3 契约。
    会话为模块级单例：续用不重启不重复发射；close/崩溃后再用 = 新启动。
    """


def _reset_session_for_testing() -> None:
    """测试夹具专用：关闭并清空模块级会话单例（消灭跨用例污染）。"""
```

（模块级单例 `_SESSION` + `atexit` 注册关闭一次；并发不防——REPL 单线程先例，§10 RF-5 登记。）

### 4.3 `tools.py` 注册面变更（增量）

1. **新增 `_extra_available`**（§2.1②，顶部 import 块加 `importlib`——stdlib，纯洁性不受影响）；
2. **`build_tool_schemas`**（`tools.py:440-455`）能力过滤（§2.1③）；协议键白名单化（`name`/`description`/`parameters`）由 Spec 4 PR2 交付——§6.1 硬依赖指 Spec 4 **完整**落地，`requires_extra`/`adr`/`side_effect` 均不泄入 LLM payload（v0.2 据 🔵-3 删除「未落地则顺带」死分支）；
3. **`execute_tool`**（`tools.py:653-670`）能力闸（§2.1③，插在 660-665 scope 闸之后、666 try 之前）；
4. **`TOOL_SCHEMAS`**（`tools.py:334-428`）追加两条（Spec 4 的 8 条一字不改）：

   ```python
   "crawl": {
       "name": "crawl",
       "side_effect": False,
       "adr": "ADR-0021",
       "requires_extra": "crawl4ai",
       "description": (
           "无头渲染抓取单个 URL（只读，升级链第二级）。仅在 web_fetch 失败后使用，"
           "reason 必填说明下级为何不够；stealth 仅在普通无头被盾时显式开启。"
           "失败如实报错并提示升级 browser，严禁静默降级为水百科。"
       ),
       "parameters": {
           "type": "object",
           "properties": {
               "url": {"type": "string", "description": "http/https URL"},
               "reason": {"type": "string", "description": "为什么 web_fetch 不够（其报错原文/遇到的盾）"},
               "stealth": {"type": "boolean", "description": "绕盾模式，默认 false"},
           },
           "required": ["url", "reason"],
           "additionalProperties": False,
       },
   },
   "browser": {
       "name": "browser",
       "adr": "ADR-0021",
       "requires_extra": "playwright",
       "description": (
           "登录态浏览器（升级链第三级，每次调用过人审卡，每次启动落审批事件）。"
           "action 仅支持 navigate / extract_text；profile 独立持久化、路径由配置钉死。"
           "仅在 crawl 也不够或必须登录态时使用，reason 必填。"
       ),
       "parameters": {
           "type": "object",
           "properties": {
               "action": {"type": "string", "enum": ["navigate", "extract_text"]},
               "url": {"type": "string", "description": "navigate 的目标 URL"},
               "reason": {"type": "string", "description": "为什么 crawl 不够/为何需要登录态"},
           },
           "required": ["action", "reason"],
           "additionalProperties": False,
       },
   },
   ```
   （browser **不标** `side_effect`——默认 True 必弹卡，fail-closed，§2.3。）
5. **`_TOOL_IMPLS`**（`tools.py:643-650`）追加两条延迟 import 包装（风格照抄 Spec 4 §4.2；`browser_action` 调用透传 `ctx.episode_dir` 与 `ctx.scope` 供事件 payload）。

### 4.4 `config/agent/tools.json` 变更（护栏变更；**以 Spec 4 落地后形态为基准**，§6.1）

```diff
   "creative": [
     "read_artifact", "write_episode_file", "list_episodes", "read_status",
-    "search_notes", "web_search", "web_fetch"
+    "search_notes", "web_search", "web_fetch", "crawl", "browser"
   ],
-  "asset": ["web_search", "web_fetch"],
+  "asset": ["web_search", "web_fetch", "crawl", "browser"],
```
`pipeline` 与 `idea` 两键**一字不动**（scope 分组沿用 Spec 4 机制：asset/creative 可见，pipeline 永不可见，ADR-0021 §2）。

### 4.5 `cli.py` 变更（两处增量）

1. 在 Spec 4 §4.4 的 `web_fetch` 分支后追加只读回显：
```python
        elif name == "crawl":
            summary = str(args.get("url", "")).strip()
```
   browser 走弹卡路径（`render_approval_card` 通用渲染 args，`status_card.py:140`），**无需**新卡片代码——action/url/reason 全部入卡，人即跳级审查闸（§2.4）。
2. **审批记账的 target_str 推导补 `url` 键（v0.2 新增，🟡-5）**：`cli.py:624` 现状为 `target_str = " ".join(argv) if argv else str(args.get("filename", "") or args.get("command", ""))`——browser 参数无 filename/command 键，记账 target 恒为空串（人批的是什么 URL 无持久记录）。一行级扩展为 `... or args.get("url", "")`，browser 卡记账即可定位所批 URL（crawl 免弹卡不进记账，不受影响）。此为对 Spec 4 收口文本之外的一处 cli.py 增量，登记于此待复审确认。

### 4.6 明确不改的既有行为（范围闸门）

- Spec 4 全部既有面（web.py 八字段基座契约、egress/守卫/清洗实现语义）：复用不改动；唯一例外是 `load_web_config` 须宽容未知顶层键（§2.6②，对 Spec 4 的接口要求）；
- `resolver.py:17-23 scope_of`、四份 scope 提示词：不动（mask-don't-remove 不靠 prompt 自律）；
- `acquire.py`、candidates.json 人审闸门、四个停机点：不动；browser 不提供任何写盘/下载出口（§2.5）；
- `_default_approve` 的卡片渲染与记账**逻辑**：不动（通用机制直接承载 browser）；唯一例外是 §4.5② 的 target_str 一行级推导扩展（🟡-5）；
- 不新增 scope、不新增写操作 action、不发明会话级授权缓存（§2.3）。

---

## 5. 依赖白名单与纯洁性保障

### 5.1 模块级依赖白名单

- **`web_crawl.py` 顶层**：stdlib（`dataclasses`, `json`, `pathlib`, `typing`）+ `pipeline.agent.web`（`_guard_url` / `_normalized_for_assert` / `_scrub`）+ `pipeline.agent.tools`（`assert_egress_boundary`）；`crawl4ai` 仅允许出现在 `_default_crawl_fn` 函数体内（连同 `asyncio` 也在函数级——顶层 import asyncio 虽 stdlib 无害，但保持「重路径全部函数级」一条规则好守）。
- **`web_browser.py` 顶层**：stdlib（`atexit`, `dataclasses`, `json`, `pathlib`, `typing`）+ 同上的 web/tools 符号；`playwright` 仅允许在 `_default_launch_fn` 函数体内；`pipeline.jobs` 仅允许在事件发射点函数级延迟 import（Spec 2 未施工期间该 import 的降解语义见 §6.2）。
- **tools.py 增量**：`importlib`（stdlib）。
- **严禁顶层导入**：`crawl4ai` / `playwright` / `camoufox` / `numpy` / `torch` / `mlx_whisper` / `moviepy` / `cv2` / `transformers` / `requests` / `httpx` / `bs4` / `lxml`。

### 5.2 独立子进程纯洁性测试（Spec 2 §5.2 / Spec 4 §5.2 同款机制）

两条腿，均在**未安装 extras 的环境**（CI 默认形态，`dev` extras 不含 crawl/browser）：

```python
def test_crawl_browser_modules_import_without_extras():
    """未装 extras 时两模块顶层可 import 且零重包泄漏（schema 隐藏≠import 报错）。"""
    import subprocess, sys
    probe = (
        "import pipeline.agent.web_crawl, pipeline.agent.web_browser, sys; "
        "forbidden = ('crawl4ai', 'playwright', 'camoufox', 'numpy', 'torch', "
        "             'moviepy', 'transformers', 'requests', 'httpx', 'bs4', 'lxml'); "
        "leaked = [m for m in forbidden if m in sys.modules]; "
        "assert not leaked, f'顶层违规引入: {leaked}'"
    )
    res = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True)
    assert res.returncode == 0, f"纯洁性检验失败:\n{res.stderr}"
```

第二条腿（v0.2 据 🟡-1 重写为独立测试并**显式调用变异点**——v0.1 的「第二腿」只是一句 prose、探针本体从不调用 `_extra_available`/`build_tool_schemas`，MUT-4 在该写法下结构性空转，红队指控属实）：

```python
def test_capability_probe_itself_is_side_effect_free():
    """探测路径自身零重包副作用（MUT-4 的证伪测试）。

    子进程内真实调用 tools._extra_available('crawl4ai'/'playwright') 与
    build_tool_schemas('creative')，断言调用后重包不在 sys.modules。
    两种环境杀 MUT-4（import_module 实现）的路径：已装 extras——crawl4ai
    真实进入 sys.modules，泄漏断言红；未装——import_module 在调用点抛
    ModuleNotFoundError，子进程非零退出红。正确实现（find_spec）双环境均绿。
    """
    import subprocess, sys
    probe = (
        "from pipeline.agent import tools; "
        "tools._extra_available('crawl4ai'); tools._extra_available('playwright'); "
        "tools.build_tool_schemas('creative'); "
        "import sys; "
        "leaked = [m for m in ('crawl4ai', 'playwright') if m in sys.modules]; "
        "assert not leaked, f'探测路径泄漏重包: {leaked}'"
    )
    res = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True)
    assert res.returncode == 0, f"探测侧效应检验失败:\n{res.stderr}"
```

---

## 6. 跨 Spec 接口与系统边界

### 6.1 与 Spec 4（web_search + web_fetch）的接口边界——**硬施工依赖**

- **施工状态**：Spec 4 v0.3「可动工未施工」（2026-09-23 核实 `pipeline/agent/web.py` 与 `config/agent/web.json` 不存在）。**本 spec PR1 动工前 Spec 4 必须已落地**；并行施工不允许（两 spec 同改 tools.py 注册面与 tools.json，合并冲突无谓消耗）。
- **复用清单（以 Spec 4 §4.1 签名为准，本 spec 不复制第二份）**：`_guard_url` / `_normalized_for_assert` / `_scrub`（入方向清洗）/ `RESTRICTED_EGRESS_PATTERNS`（经 tools import）/ `assert_egress_boundary`；egress 命中的 `[BLOCKED]` 行为定性（Spec 4 §3.4）原样继承。
- **对 Spec 4 的两处接口要求**（登记待红队裁决，不构成对 Spec 4 已收口文本的回改授权）：
  1. `load_web_config` 须**宽容未知顶层键**（web.json 加 crawl/browser 段不炸基座，§2.6②）——若 Spec 4 施工时实现为「严格键集合校验」，此处需要一行级放宽；
  2. `build_tool_schemas` 协议键白名单化（`name`/`description`/`parameters`）由 **Spec 4 PR2 交付**（Spec 4 §4.2③ 已含）；Spec 4 **完整**落地是本 spec PR1 的硬前置（§8），白名单化必在场、`requires_extra`/`adr`/`side_effect` 不泄入 LLM payload——本 spec **零顺带**（v0.3 据 🟡-R1 删除「若未落地则顺带」残留，与 §4.3② 口径统一）。
- **工具位次**：不留占位条目纪律沿用（Spec 4 §2.7）；本 spec 后 10/12（§2.8）。
- **升级链接口**：web_fetch 403 文案已指向 crawl（Spec 4 §3.4），Spec 4 施工时该文案不因 crawl 不存在而改写（「文案不含工具存在性断言」，Spec 4 §6.5 原文承诺）。

### 6.2 与 Spec 2（jobs 层与 events.jsonl）的接口边界——**browser 事件为硬施工依赖**

- **施工状态**：Spec 2 v0.4「可动工未施工」（`pipeline/jobs.py` 不存在，Spec 4 §6.2 已登记、本 spec 复核同结论）。
- **本 spec 消费的接口**：`get_publisher().emit(event_type, payload, *, episode_dir=None)`、`publisher.close()` 同步点、`data_root`/`AVA_EVENTS_ROOT` sink 覆写（Spec 2 §4.1）。
- **EventType 扩展申请**：`EventType` 新增一行 `BROWSER_SESSION_STARTED = "browser_session_started"`（append-only，不改既有值语义）。归属建议：Spec 2 先落地则本 spec PR2 顺手加一行（在 PR 描述注明）；Spec 2 评审认为应归 Spec 2 文本则回改 Spec 2 v0.5——**留待红队裁决**，本 spec 不单方面拍板归属。
- **施工依赖钉死**：**PR2（browser）动工前 Spec 2 必须已落地**。「jobs 缺失则静默跳过事件发射」是**禁项**（审批事件落盘是验收方向，静默跳过 = 观测层造假）；若评审要求 browser 先于 Spec 2 交付，唯一可接受替代是 PR2 拆分（browser 工具本体先交、事件发射留 TODO 并降级验收门禁 3 为「暂免」）——本 spec **不推荐**该拆法，仅如实登记选项。
- **零感知铁律不变**：browser 事件是观测层，不参与 `pipeline.status` 推导（direction §4 红线 3）；profile 目录不是期产物，status 不读它。

### 6.3 与 Spec 3（approval 对象化）的接口边界

- browser 的审批**不产生停机点 approval 对象**：四个停机点（02.5/03.5/05/09）不增不减（direction §4 红线 4）；browser 卡是工具调用级审批，记账走既有 `log_approval_decision` → `approvals.jsonl`（`cli.py:636`）+ Spec 2 M1 的 `APPROVAL_RESOLVED` 双写，与 Spec 3 的 `approvals_store.json` 对象队列零交集。
- Spec 3 的 rejected 结构化反馈（哪段、什么问题）不扩展到工具卡——`cli.py:641-642` 的拒绝只回固定文案，本期不改。

### 6.4 与 Spec 6（acquire_propose）/ Spec 8（桌面端）的接口边界

- crawl/browser 抓回内容只进 LLM 上下文；变成素材候选的正确路径是 Spec 6 `acquire_propose`（未施工）或人工登记 candidates.json——本 spec 不提供捷径（ADR-0021 §3）。
- `browser_session_started` 事件是 Spec 8 桌面端呈现「agent 正在浏览」轨迹的数据源；BrowserView 嵌入 browser 工具的可见窗口（ADR-0021 §5 尾条）属 Spec 8，本 spec 只保证 `headed=true` 时窗口真实存在。

### 6.5 与 pi 侧 `agent_crawl` / `agent_browser` 的边界

- 本 spec 是**内化**：ava 仓库内自有实现，不调用、不依赖 pi 环境的 `agent_crawl`/`agent_browser` 工具本体；STANDARD.md:196-199 提到的两个名字是升级链的**职责定义**，实现归 ava 后，scout 派工单按 ADR-0021 §4 降级为「内部三级全失败后的显式升级通道」。
- profile 隔离的对照面包括 pi 侧：`~/.config/pi-browser-profile` 同样不在 ava `data/` 子树内，守卫天然拒绝（§2.2 白名单子树规则的完备性）。

---

## 7. 测试规格与变异检验方案

### 7.1 单元测试规格（`tests/test_agent_crawl_browser.py`；T15/T16 为既有文件内更新）

> **隔离铁律（沿用 Spec 4 §7.1 并加码）**：全部测试**零真实出网、零真实浏览器进程、零真实 profile 目录**。`crawl_fn`/`launch_fn` 注入 fake（返回 fixture 或抛指定异常），fake 内记录调用实参与次数；凡过 `_guard_url` 的用例 monkeypatch `socket.getaddrinfo` 返回钉死公网地址（DNS 即出网，Spec 4 🟡-10）；fixture HTML/markdown 为测试文件内常量。事件断言**先 `publisher.close()` 后读文件**（Spec 2 同步点铁律），publisher 经 `AVA_EVENTS_ROOT` 定向 `tmp_path`；每个 browser 用例 teardown 调 `_reset_session_for_testing()`。
>
> **可用性铁律**：凡断言「extras 可用/不可用」语义的用例（T4/T5/T17 等），monkeypatch `tools._extra_available` 或 `sys.modules` 探测缝钉死行为，**不依赖测试机真实装没装**（同一套件在有/无 extras 两种环境必须同样绿）。T6 双腿同样是环境无关设计（v0.3 据 🔵-R2 简化）：正确实现（find_spec 不执行重包顶层）双环境均绿，MUT-3（顶层 import）/MUT-4（import_module 探测）双环境均红——无环境前提。
>
> **PR 归属**：PR1 用例只依赖 web_crawl.py 本体与 tools.py 掩码层（直调 `crawl_page` / `build_tool_schemas` / `execute_tool`）；PR2 用例依赖 web_browser.py 与 jobs.py。

| 编号 | 测试用例名 | PR | 覆盖场景 | 验证断言 |
|---|---|---|---|---|
| **T1** | `test_crawl_returns_scrubbed_capped_markdown` | PR1 | crawl 解析/清洗/封顶 | fake crawl_fn 返回 fixture markdown（含 `cloud.local.json` 字串与超长正文）；断言 `_scrub` 替换 `[已脱敏]` 生效、`len(markdown) <= max_chars` 且 `truncated is True`、`final_url` 透传；`reason=""` → ValueError |
| **T2** | `test_crawl_egress_blocked_before_render` | PR1 | crawl 出网前拦截 | 直调 `crawl_page("http://x/?q=cloud%2Elocal%2Ejson", "r")`（编码形态，迭代 unquote 归一）→ PermissionError，fake crawl_fn **零调用**；`Cloud.Local.JSON` 大小写变体同款 |
| **T3** | `test_crawl_rejects_bad_scheme_and_private_ip` | PR1 | crawl 出网目标收敛 | `ftp://`、`file:///etc/passwd` → PermissionError；monkeypatch getaddrinfo 返回 `169.254.169.254`/`127.0.0.1` → PermissionError；fake 均零调用 |
| **T4** | `test_extras_missing_hides_and_blocks_tools` | PR1 | **核心验收 1：未装 extras 三层证据** | monkeypatch `_extra_available → False`：① `crawl`/`browser` ∈ `TOOL_SCHEMAS`（注册表常驻）；② `build_tool_schemas("creative")` 与 `("asset")` 输出**不含**两工具（schema 层隐藏）；③ `execute_tool("crawl", {...}, creative ctx)` 返回 `{"ok": False, "error": 含 "可选依赖"}` 且**不抛异常**（能力闸在 try 之前，ImportError 无从穿透）；fake crawl_fn 零调用 |
| **T5** | `test_extras_present_exposes_tools` | PR1 | 能力掩码正向腿 + schema 钉死 | monkeypatch `_extra_available → True`：`build_tool_schemas("creative")` 含 crawl/browser，每个 function 键集合恰为 `{"name","description","parameters"}`（`requires_extra`/`side_effect`/`adr` 零泄漏）；crawl schema `required == ["url","reason"]`；browser schema properties 键恰为 `{"action","url","reason"}`（§2.2 schema 排他断言）、`action` 枚举恰为 `["navigate","extract_text"]` |
| **T6** | `test_crawl_browser_modules_pure` | PR1 | 依赖纯洁性 | §5.2 双腿子进程探针全绿 |
| **T7** | `test_profile_isolation_guard` | PR2 | **核心验收 2：profile 隔离守卫** | `_assert_profile_isolation("data/browser-profile", root)` 放行且返回 resolve 路径；`~/Library/Application Support/Google/Chrome/Default`、其上级 `.../Google/Chrome`、`~/.config/pi-browser-profile`、仓库根、`/tmp/x`、`data`（自身非严格后代）逐一 PermissionError |
| **T8** | `test_browser_schema_has_no_profile_override` | PR2 | profile 运行时零覆盖通道 | 与 T5 的 properties 断言同源但更严：直接断言 `TOOL_SCHEMAS["browser"]["parameters"]` 全文字符串不含 `profile`/`headless`/`user_data_dir`（防施工者换名夹带） |
| **T9** | `test_browser_launch_uses_pinned_profile_and_emits_event` | PR2 | **核心验收 3：启动钉死路径 + 事件落盘** | tmp root 造 web.json（browser 段 profile_dir 默认）；fake launch_fn 记录实参；直调 `browser_action("navigate", "crawl 遇盾", url="https://example.com", ...)`：① fake 收到 `user_data_dir == (tmp_root/"data/browser-profile").resolve()`、`headed == True`；② 返回值含 final_url/title；③ **`publisher.close()` 后**读 `tmp_path` 下 events.jsonl：恰 1 条 `browser_session_started`，payload **必备四键**（`profile_dir`/`headed`/`scope`/`trigger`）齐全、`profile_dir` 为同一路径、`trigger.reason` 吻合；`pid` 若在场则 >0（可得性为约，🔵-9，施工时先实测再定断言形态）；**无 `approved_via` 类审批自证字段**（🟡-2：本用例直调零审批，事件只承载「启动发生」）；④ payload 全文不含 cookie 字样字段 |
| **T10** | `test_browser_card_per_call_and_reject_blocks_launch` | PR2 | **核心验收 4：approval 门逐调用 + 拒绝零启动** | **live-loop 路径（v0.2 据 🔵-7 删除直调降级写法——直调 approve 不经过 impl，「零调用/零事件」腿恒真空转）**：patch `pipeline.agent.llm.urllib.request.urlopen` 首轮返回「带 browser tool_call 的 reply」（Spec 4 T17② 同款接线），approve 钩接 `_default_approve`，patch `builtins.input` 先 `"n"` 后 `"y"` 两轮——拒绝轮：`_TOOL_IMPLS` 未执行（fake launch_fn 零调用、`publisher.close()` 后事件零条）、outcome 为拒绝错误数据；批准轮：放行后启动一次、事件一条。`approvals.jsonl` 含两条记账且 **target 字段含所批 URL**（§4.5② target_str 扩展，🟡-5） |
| **T11** | `test_browser_session_reuse_no_duplicate_event` | PR2 | 启动粒度 = 进程级（§2.3） | 同进程内连续 `browser_action` ×3（navigate/extract_text/navigate，fake launch_fn）：launch_fn 调用**恰 1 次**；`publisher.close()` 后事件**恰 1 条**；`_reset_session_for_testing()` 后再调用 → launch 计数 +1、事件 +1（新启动 = 新事件）；**死会话腿（v0.4 收口，🔵-R3/🟡-R4/🟡-R5）**：monkeypatch `web_browser._pw_error_types` 返回 `(FakePWError,)`（异常族构造缝，T18 同款——无 playwright 环境下无从直接构造，🔵-R5）；fake 会话首次操作与探活**均**抛 FakePWError → 判死：旧会话丢弃、自动重启（launch 计数 +1、事件 +1）后重试成功，**不**包装为「操作失败」、**不**新增人审卡；对照：探活桩为存活时，首次操作失败直接包装 ValueError、零重启（🟡-R5 条件收窄） |
| **T12** | `test_browser_navigate_egress_and_guard` | PR2 | browser 出网双闸 + 重定向复跑（🟡-4） | navigate 到含模式串 URL（编码形态）→ PermissionError 且 launch_fn 零调用（断言先于启动）；getaddrinfo 打私网地址同款；`extract_text` 在无会话时 → ValueError 含「未启动」（不隐式启动）；**重定向腿（v0.3 据 🟡-R3 修 fixture）**：fake getaddrinfo **分主机钉死**——初始主机（如 `example.com`）返回公网地址、私网字面 IP 主机返回其自身；fake 会话 navigate 落地后 `final_url` 为 `http://169.254.169.254/...` → 落地复跑 `_guard_url` 抛 PermissionError，内网内容不进返回值（若 getaddrinfo 一律打私网，初始守卫先炸，证到的就不是复跑——v0.2 的 fixture 错位在此） |
| **T13** | `test_browser_extract_text_scrubbed_and_capped` | PR2 | 读文清洗 | fake 会话返回含模式串的超长页面文本 → `text` 已 `[已脱敏]`、超长截断 `truncated is True` |
| **T14a** | `test_crawl_section_independent_degradation` | PR1 | crawl 配置独立校验（§2.6；v0.2 拆腿，🟡-3） | tmp root：缺 crawl 段 → `load_crawl_section → None` 且 crawl 工具错误文案含「crawl 段」；crawl 段损坏（类型错）同款；Spec 4 基座段照常可用（直调 `load_web_config` 验证——Spec 4 **完整**落地是 PR1 硬前置，🔵-3 已删「否则」死分支） |
| **T14b** | `test_browser_section_independent_degradation` | PR2 | browser 配置独立校验 | 缺/坏 browser 段 → `load_browser_section → None`，工具显式错误含「browser 段」且不影响 crawl 段加载；`browser.profile_dir` 指向 `../outside` → 守卫 PermissionError（非 None——危险配置必须响，§3.1） |
| **T15** | `SPEC_TOOLS` 与 drift 断言同步更新（`tests/test_agent_tools.py:311-316`、`:405`、`:546-578`） | PR1/PR2 | 真配置 drift 与新掩码语义相容 | 期望清单计算统一经 `_extra_available` 过滤（或 monkeypatch 钉 True 取全集）；`pipeline`/`idea` 两表逐字不变。**先改代码与真配置跑一遍再写断言**（AGENTS.md:217 纪律①） |
| **T16** | ADR 门禁含新条目（Spec 4 T12 扩展回归） | PR2 | 工具表新口径 | `crawl`/`browser` 的 `adr` 均为 `ADR-0021`，数字段 glob 恰中 `0021-*.md` 一个文件；工具表总数 10 ≤ 12 |
| **T17** | `test_partial_install_import_failure_is_explicit_error` | PR1/PR2 | **🔴-1：find_spec 假阳性 → ImportError 不穿透** | 双腿（v0.3 据 🟡-R2 改写为 import 机制层 mock，**双环境同绿、零真 import**）：前置 `monkeypatch.setitem(sys.modules, "crawl4ai", None)` / `("playwright", None)`——sys.modules 置 None 使对应 import 必抛 ImportError（标准技巧，Spec 3 T10b 先例，monkeypatch 自动 teardown）——① 直调 `_default_crawl_fn`/`_default_launch_fn` → 抛 **ValueError**（含「导入失败/重装」文案），绝非 ImportError；② monkeypatch `tools._extra_available → True`（模拟部分安装：能力闸放行、import 必败），fixture：tmp web.json 含**合法 crawl 段** + getaddrinfo patch（🔵-R1：缺段会先报「crawl 段」而非「导入失败」，guard 需 DNS patch），`execute_tool("crawl", {"url": "https://example.com", "reason": "r"}, creative ctx)` → `{"ok": False, "error": 含 "导入失败"}` 且**不抛异常**（包装后落入 `tools.py:668` 捕获面） |
| **T18** | `test_playwright_errors_wrapped_as_value_error` | PR2 | **🔴-2：playwright 异常族包装** | monkeypatch `web_browser._pw_error_types` 返回 `(FakePWError,)`：fake launch_fn 抛 FakePWError（启动失败/双开抢 profile 场景）→ `browser_action` 抛 ValueError 含「browser 操作失败」；fake 会话 navigate 超时抛 FakePWError 同款（探活桩钉**存活**——v0.4 分流下直接包装、不触发自愈重启，🔵-R5）；**对照腿**：非 playwright 异常（守卫的 PermissionError、参数 ValueError）原样穿透不被误包 |

> **S16 修复轮新增（本表止于 T18，不逐行展开）**：T19 `test_adapter_probe_marks_dead_on_context_close`、T20 `test_default_crawl_fn_runs_coroutine_in_worker_thread`、T21 `test_default_crawl_fn_forces_base_dir_env_before_import`（含 X-6 腿：已导入但绑定目录不可知 → ValueError）、T22 `test_missing_data_dir_is_explicit_error`，外加 T9⑤（profile 首建 chmod 0700）、T12⑤（extract_text 落地复跑）、T18④（非 playwright 异常穿透）；逐条断言见各用例 docstring 与 S15/S16 报告。本节两条铁律不变，且**不依赖真实 `data/`**：外置盘拔掉时 `tests/test_agent_crawl_browser.py` 必须全绿（T17/T20/T21/T22 自钉 `paths.DATA`）。

### 7.2 变异检验矩阵（Mutation Testing Matrix）

| 变异编号 | 注入变异（故意写坏代码） | 预期变红的测试 | 证伪机理（为什么必须红） |
|---|---|---|---|
| **MUT-1** | `build_tool_schemas` 删除能力过滤（requires_extra 存在也不跳过） | T4② | extras 缺失下 crawl/browser 出现在 creative/asset 的 schema 清单，「不含」断言失败变红——钉死「schema 层隐藏」 |
| **MUT-2** | `execute_tool` 删除能力闸 | T4③ | extras 缺失下 execute 穿透到 `_TOOL_IMPLS["crawl"]`，其函数体 `import crawl4ai` 抛 ImportError——不在 `tools.py:668` 四异常捕获面内 → 异常穿透，「不抛异常 + 显式错误数据」断言失败变红 |
| **MUT-3** | `web_crawl.py` 顶层加 `import crawl4ai` | T6 | 未装 extras 的子进程探针 import 即 ImportError（非零退出码）变红；装了的环境则 `crawl4ai in sys.modules` 断言变红——双环境均红。**S16 实测注记**：无 extras 环境下红在**收集测试**——顶层 import 使 `tests/test_agent_crawl_browser.py` 自身 import 即失败，探针根本没跑到；探针断言变红只发生在装了 extras 的环境（两环境都红，但机理只在有 extras 那侧成立） |
| **MUT-4** | `_extra_available` 改为 `importlib.import_module(dist)` 实现 | T6（第二腿，v0.2 据 🟡-1 修复空转；S16 🔵-4 补注） | 第二腿探针**显式调用** `_extra_available` 与 `build_tool_schemas`：已装 extras 环境下 import_module 使 crawl4ai 真实进入 sys.modules，泄漏断言红；未装环境下不带 try 的 import_module 在调用点抛 ModuleNotFoundError，子进程非零退出红（「双环境均红」仅适用于不带 try 的 `import_module` 变体；若写为 `try: import_module(...) except ImportError: return False` 变体，在无 extras 环境下会因抛错早退而未留 sys.modules 记录从而存活，只在有 extras 的环境里变红，S16 🔵-4） |
| **MUT-5** | `crawl_page` 删除 `assert_egress_boundary` 调用 | T2 | 编码形态模式串不再抛 PermissionError，fake crawl_fn 被真实调用（计数 ≥1），「零调用 + raises」双断言失败变红 |
| **MUT-6** | `crawl_page` 删除 `_guard_url` 调用 | T3 | 私网地址用例 fake 被调用，「零调用 + PermissionError」失败变红 |
| **MUT-7** | `_assert_profile_isolation` 删除 data/ 子树校验（只返回 resolve） | T7 | Chrome Default 等六个危险路径全部放行，PermissionError 断言逐一失败变红——钉死 profile 隔离唯一不变量 |
| **MUT-8** | `TOOL_SCHEMAS["browser"]["parameters"]["properties"]` 增加 `profile_dir` 键（模型可覆盖路径） | T8 | 全文字符串含 `profile`，断言失败变红——钉死「路径只来自 config」 |
| **MUT-9** | `browser_action` 启动时把 `user_data_dir` 硬编码为 Chrome Default 路径（绕过 section） | T9① | fake launch_fn 收到的 `user_data_dir` ≠ config 钉死路径，相等断言失败变红——证明启动实参被钉死而非名义钉死 |
| **MUT-10** | 删除启动后的 `emit(browser_session_started, ...)` | T9③ | `publisher.close()` 后 events.jsonl 无该事件，「恰 1 条」断言失败变红——钉死审批事件落盘 |
| **MUT-11** | `TOOL_SCHEMAS["browser"]` 标上 `"side_effect": False` | T10 | 拒绝腿走只读分流免弹卡直接放行（`cli.py:606`），input 未被调、launch_fn 被调用、返回 `(True, "")`——「拒绝阻断启动」断言失败变红 |
| **MUT-12** | `browser_action` 每次调用都新建会话（删单例复用） | T11 | 三次调用 launch_fn 计数为 3、事件 3 条，「恰 1」断言失败变红——钉死「每次启动」的进程级粒度定义 |
| **MUT-13** | `extract_text` 返回路径删除 `_scrub` | T13 | 模式串原样出现在返回 text，`[已脱敏]` 断言失败变红 |
| **MUT-14** | `load_crawl_section` 缺段时静默回落默认配置（不返回 None） | T14a | 缺段场景工具不再报「crawl 段」显式错误而是正常返回，断言失败变红——钉死「不静默回落默认值」 |
| **MUT-15** | `extract_text` 无会话时隐式启动（删「未启动」ValueError 分支） | T12 | 无会话 extract_text 触发 launch_fn（计数 ≥1）且不抛错，断言失败变红——钉死「首次启动必须是人审过的 navigate」 |
| **MUT-16** | 删除 `_default_crawl_fn`/`_default_launch_fn` 的 ImportError → ValueError 包装（🔴-1 配套） | T17 | 腿①：`sys.modules` 置 None 强制 ImportError 后直调默认实现，抛出的不再是 ValueError 而是 ImportError，「ValueError + 文案」断言失败红（双环境同款，v0.4 据 🔵-R4 同步措辞；杀法不变）；腿②：ImportError 穿透 `execute_tool` 捕获面（`tools.py:668` 四类不含它）成为未捕获异常，「不抛异常」断言失败红——钉死部分安装路径的封闭 |
| **MUT-17** | 删除 `browser_action` 的 playwright 异常包装（🔴-2 配套） | T18 | FakePWError 不再被包装、原样穿透（isinstance 判定落空），「ValueError + 文案」断言失败红——启动失败/超时/死会话的 REPL 崩溃路径重新敞开（即 🔴-2 机理链复现） |
| **MUT-18** | 删除 navigate/extract_text 对落地 URL 的 `_guard_url` 复跑（🟡-R3 配套） | T12（重定向腿） | 删复跑后内网 `final_url` 不抛错、页面内容进返回值，「PermissionError + 内网内容不进返回值」断言失败红——钉死「首跳合法、30x 跳进内网」旁路的封闭；v0.2 漏配此变异（关键断言缺变异，红队指控属实） |

（每条变异的反向验证：正确实现下对应测试确绿——T4/T9 的双腿设计保证变异杀的是真断言而非环境巧合；期望值先跑再写断言，AGENTS.md:217。）

> **S16 修复轮新增（本表止于 MUT-18，不逐行展开）**：MUT-19（`probe()` 恒 True）、MUT-20（协程改回主线程 `asyncio.run`）、MUT-21（删 `chmod 0700`）、MUT-22/MUT-23（删 `data/` 可达检查，browser/crawl 各一）、MUT-24/MUT-25（删/改 `CRAWL4_AI_BASE_DIRECTORY` 赋值）、MUT-26（只删 extract_text 落地复跑）、MUT-27（操作层 `isinstance` 改恒真）、MUT-28（绑定目录不可知时不再拒绝）；另有两条定向变异 MUT-29（删 T17 的 `paths.DATA` 钉死，须在「无盘插件」下变红）/ MUT-30（删会话级 env 归还，须在「ambient 插件」下变红）。逐条目标用例与变红机理见 S15/S16 报告。

---

## 8. 施工与 PR 划分

### PR1：extras 声明、能力掩码层与 crawl 工具（schema 隐藏机制落地）

- **前置**：Spec 4 **完整**落地（含其 PR2 的协议键白名单化，§6.1）；开工前 `uv run pytest` 基线全绿；真跑 crawl 的端到端验证前需人工 `uv sync --extra crawl && uv run crawl4ai-setup`（浏览器二进制，🟡-6/§2.1①——单测全 fake 不依赖此前置）。
- **范围**：`pyproject.toml` 加 `crawl` extras（§2.1①）；`tools.py` 三处增量（`_extra_available`、build_tool_schemas 能力过滤、execute_tool 能力闸，§4.3）+ TOOL_SCHEMAS/`_TOOL_IMPLS` 加 crawl 条目；`config/agent/tools.json` 加 crawl（§4.4 的 crawl 部分）；`config/agent/web.json` 加 crawl 段；新建 `pipeline/agent/web_crawl.py`（§4.1）；`cli.py` 只读回显分支与 target_str 扩展（§4.5）；`llm.py:4` 计数修订；测试 T1/T2/T3/T4/T5/T6/T14a/T17（crawl 腿）+ T15（crawl 部分）。
- **验证命令**：`uv run pytest tests/test_agent_crawl_browser.py tests/test_agent_tools.py tests/test_agent_pr6.py`（默认环境无 extras：验证隐藏腿）；`uv sync --extra crawl && uv run pytest tests/test_agent_crawl_browser.py -k "T5 or T6"`（有 extras 环境验正向腿，T6 口径见 §7.1 铁律）。

### PR2：browser 工具与启动审批事件

- **前置**：PR1 合入；**Spec 2 已落地**（§6.2 硬依赖）；本机已 `uv sync --extra browser && uv run playwright install chromium`（人工一次性步骤）。
- **范围**：`pyproject.toml` 加 `browser` extras；`pipeline/jobs.py` 的 `EventType` 加一行 `BROWSER_SESSION_STARTED`（归属裁决见 §6.2）；新建 `pipeline/agent/web_browser.py`（§4.2）；TOOL_SCHEMAS/`_TOOL_IMPLS`/tools.json/web.json 加 browser；测试 T7~T13、T14b、T15（browser 部分）、T16、T17（browser 腿）、T18。
- **验证命令**：`uv run pytest tests/test_agent_crawl_browser.py tests/test_jobs.py tests/test_agent_cli.py`

### PR3：变异检验、门禁固化与文档收口

- **范围**：MUT-1~MUT-18 逐条注入复验（每条留验讫记录）；§9 门禁逐项打勾；`docs/dev/plans/README.md` 索引登记；文档门禁。
- **变异规程补充（🔵-8）**：验讫 MUT-5（删 egress 断言）时，T2 会漏到 `_guard_url` 的真实 DNS 解析——该次运行前给 T2 临时补 `getaddrinfo` patch（或在断网环境运行），并在验讫记录中注明；严禁以「变异运行撞了真实出网」为由跳过该变异。
- **验证命令**：`uv run pytest tests/test_agent_crawl_browser.py tests/test_agent_tools.py tests/test_agent_cli.py tests/test_docs_invariants.py`

---

## 9. 验收门禁清单（Accept Gates）

- [x] **门禁 1（未装 extras 三层证据 + 部分安装封闭）**：无 extras 环境下 `build_tool_schemas("creative")`/`("asset")` 无 crawl/browser（schema 隐藏）；两工具常驻 `TOOL_SCHEMAS`（注册表不删）；`execute_tool` 返回显式「可选依赖未安装」错误数据、零异常穿透；**部分/损坏安装**（find_spec 假阳性）路径同样显式错误数据、ImportError 零穿透——T4/T17 全绿，MUT-1/MUT-2/MUT-16 必红；
- [x] **门禁 2（依赖纯洁性）**：独立子进程断言两新模块顶层零重依赖、未装 extras 时可 import——T6 全绿，MUT-3/MUT-4 必红；
- [x] **门禁 3（browser 启动审批事件落盘）**：每次进程启动恰一条 `browser_session_started`（payload **必备四键**齐全、pid 可缺省、无凭据字段、无审批自证字段），**前置条件：读取前 `publisher.close()` 完成有界排空**（Spec 2 同步点铁律）；会话续用不重复发射，重启才再发射——T9/T11 全绿，MUT-10/MUT-12 必红；
- [x] **门禁 4（approval 门逐调用、确定性、零 guardian）**：browser 不标 side_effect，每次调用过 `_default_approve` 人审卡；拒绝则零启动零事件；全 diff 无 LLM 审批判断代码——T10 全绿，MUT-11 必红；
- [x] **门禁 5（profile 隔离可验证）**：守卫放行 `data/browser-profile`、拒绝 Chrome Default 等六个危险路径；fake launch_fn 实参 `user_data_dir` 与 config 钉死路径逐字节相等；工具 schema 无 profile/headless 入口——T7/T8/T9① 全绿，MUT-7/MUT-8/MUT-9 必红。**人工附加项（不进 CI，待人装好二进制后手验）**：真机启动一次后 `lsof -p <pid> | grep user-data-dir` 或 `ps aux | grep user-data-dir` 核对实际进程路径；
- [x] **门禁 6（升级链职责切分 + 异常接管）**：crawl/browser 的 `reason` 必填钉死在 schema；crawl 失败文案含升级 browser 提示；egress/私网守卫在两工具同 Spec 4 口径生效（含 navigate/extract 对落地 URL 的重定向复跑，🟡-4）；crawl4ai/playwright 异常族全包装为 ValueError，REPL 零 traceback——T2/T3/T5/T12/T18 全绿，MUT-5/MUT-6/MUT-17/MUT-18 必红；
- [x] **门禁 7（scope 分组不变 + 位次台账）**：`pipeline`/`idea` 两表逐字不变；asset/creative 加两工具；`adr` 均指 ADR-0021 真实文件；工具表总数 11 ≤ 12（Spec 6 acquire_propose 已于 M7 先行落地，故 9 → 11）——T15/T16 全绿；
- [x] **门禁 8（红线零触碰）**：`git diff` 中 `acquire.py`、四个停机点、`resolver.py`、四份 scope 提示词、`_default_approve` 卡片渲染逻辑零改动；无数据库、无 web server、无 guardian LLM、无会话级授权缓存；
- [x] **门禁 9（既有测试零回归 + 文档门禁）**：`tests/test_agent_tools.py`、`test_agent_pr6.py`、`test_agent_director.py`、`test_agent_cli.py`、`test_jobs.py`、`test_agent_web.py` 全绿；`uv run pytest tests/test_docs_invariants.py` 全绿。

---

## 10. 潜在红旗与自纠预案（Red Flags & Remediation）

| 风险序号 | 潜在红旗 | 根因与危险性 | 预案与防御动作 |
|---|---|---|---|
| **RF-1** | **crawl4ai / playwright API 形态漂移** | 本 spec 对两库的封装细节标「约」（未逐版本核实），上游 minor 版本可能改构造参数；若施工时照抄网传示例，集成即坏 | `_default_crawl_fn`/`_default_launch_fn` 集中封装、fixture 钉死 ava 侧契约（T1/T9）；施工时以 `uv pip show` 实测版本 + 官方文档钉死调用形态并在代码注释写明版本（E3 纪律）；上游漂移的修复面收敛在两个函数内 |
| **RF-2** | **浏览器二进制缺失（browser + crawl 双侧，v0.2 据 🟡-6 补 crawl）** | `playwright install chromium` 与 `crawl4ai-setup` 都是人工步骤，忘装时首次真跑即失败；crawl4ai 上游安装器自带自动下载行为，与「绝不自动下载」纪律存在张力；静默重试或自动下载 = 未审批副作用 | `_default_launch_fn`/`_default_crawl_fn` 捕获二进制缺失错误转显式 ValueError 提示人工安装命令；**绝不自动下载**（裁决：人工执行安装命令，ava 代码零触发上游自动下载）；§8 PR1/PR2 前置条件写明双侧安装步骤 |
| **RF-3** | **逐调用弹卡审批疲劳** | browser 每次 navigate 都弹卡，长检索会话中人可能机械按 y，闸门形同虚设（`decision_latency_s` 趋零是信号） | 本期接受为保守默认（§2.3：最重工具人必须看见每个 URL）；观测 `status_card.py:310` 的 `decision_latency_s`；会话级/项目级授权缓存（ZCode options）是 Spec 8 议题，**届时另立 ADR**，本 spec 不预埋 |
| **RF-4** | **profile 目录损坏 / 多会话并发抢同一 profile** | 两个 ava REPL 同时启动 browser → 同一 user_data_dir 的 SingletonLock 冲突，启动失败；或 profile 损坏导致登录态丢失 | 启动失败 → 显式错误数据（诚实失败，不静默重试）；登录态丢失 → 人重新在 headed 窗口登录（§2.2 人因流程）；**已知上限**：不做 profile 健康检查与跨进程互斥锁（单用户单机工具，YAGNI；真撞上了报错文案足以自解释） |
| **RF-5** | **browser 会话单例泄漏/状态污染** | 模块级 `_SESSION` 在崩溃后持有死 context，后续调用打到死对象上；atexit 未跑（SIGKILL）时浏览器孤儿进程残留 | 调用前探活（dead 则丢弃并视为新启动——新卡 + 新事件，语义自洽）；`_reset_session_for_testing()` 保证测试隔离；**已知上限**：SIGKILL 下孤儿浏览器进程由 OS 回收策略兜底（headed 窗口人可见可关），不发明进程看护 |
| **RF-6** | **extras 体积失控推翻内化决策** | crawl4ai 链（含浏览器二进制）安装体积大、更新频繁断裂，维护税超收益 | ADR-0021 自带推翻条件：允许将第二、三级重新外置为 sidecar 进程，工具 schema 与 scope 授权层保留——本 spec 的能力掩码层（`_extra_available` + requires_extra）恰好就是外置化的切换点（探测对象从「包装了没」换成「sidecar 在不在」），回退成本低是有意设计 |
| **RF-7** | **能力掩码被误读为「功能缺失 bug」** | 用户/施工者在无 extras 环境发现工具表没有 crawl，误判为注册丢失而「修复」掉掩码 | description/错误文案全部自带安装提示（`uv sync --extra crawl`）；T4 三层断言把「隐藏」钉为**规格**而非偶发；§0 一句话设计点明机制 |
| **RF-8** | **~~测试环境装有 extras 导致 T6 口径失效~~（v0.3 关闭，🔵-R2）** | v0.2 前的探针写法依赖「CI 未装 extras」事实前提，本机全装时可能假绿/假红 | **已消解**：T6 双腿与 T17 均为环境无关设计（正确实现双环境绿、对应变异双环境红，§7.1 可用性铁律）；登记此行仅为留痕，无残余风险 |
| **RF-9** | **SSRF 残余面：DNS rebinding 与重定向跳内网** | navigate/crawl 的 URL 守卫复用 Spec 4 `_guard_url`；**重定向跳内网**（首跳合法、30x 跳进 169.254.169.254，人卡上只见初始 URL）是 Spec 4 §2.6③ 已点名的标准绕法——浏览器内核自行跟跳、ava 侧初始校验覆盖不到（v0.1 漏登记，🟡-4） | 缓解（已纳入设计）：navigate 落地后对 `final_url` 复跑 `_guard_url`，extract_text 对当前页 URL 同款（近零成本，T12 重定向腿钉死）；其余已知上限（DNS rebinding TOCTOU、Unicode 同形）不在本 spec 发明防线（Spec 4 §2.6④/RF-11 已登记）；browser 的人审卡额外提供一层人眼 URL 审查 |
| **RF-10** | **「每次启动」粒度被施工者改写** | 把事件改成按调用发射（观测噪音淹没审批轨迹）或按 REPL 会话发射（重启浏览器不落事件，观测黑洞） | §2.3 粒度定义 + T11 双腿断言（续用不重复、重启必再发）+ MUT-12；门禁 3 逐项打勾 |
| **RF-11** | **Spec 2/4 长期不施工导致本 spec 烂尾** | PR1 硬依赖 Spec 4、PR2 硬依赖 Spec 2，前置不落地则本 spec 整体挂起 | 接受为现实（依赖关系是上位文档定的施工顺序，direction §1/§2）；本 spec 不为此预留「无 Spec 4/2 也能跑」的降级实现——那是双份机制；若前置 spec 被推翻，本 spec 随之重评 |
| **RF-12** | **crawl 被当 web_fetch 平替滥用（跳级常态化）** | 模型发现 crawl 成功率高就跳过 web_fetch，成本与风控暴露面上升，升级链名存实亡。**审计面如实声明（🟡-5）**：reason 只在内存会话史可审（退出即灭），crawl 免弹卡无记账——crawl 层无持久追责面 | `reason` 必填（事前声明 + 内存可审）+ description 链位声明（§2.4）；browser 层持久面 = 卡记账（含所批 URL，§4.5②）+ 启动事件；**已知上限**：crawl 层无持久审计、不做跨调用状态机强制（机制发明）；若事后发现常态跳级，收紧手段是 description 强化或 crawl 改弹卡，单独立 ADR |
| **RF-13** | **无显示环境下 browser 不可启动（🔵-5）** | `headed=true` 钉死可见窗口先例，但 SSH/云主机（本仓有 `pipeline/cloud.py` 云端路径）无显示服务器，headed 启动必败 | **已知上限登记**：`headed` 本就是 config 项（§3.1），无显示环境配 `false` 即可；ava 的制片主战场是本地 macOS（direction §0.1 桌面端终态），云端无头浏览非本期场景，不发明 Xvfb 包装 |
| **RF-14** | **cookie at-rest 依赖平台行为（🔵-6）** | profile 内 cookie 的静态加密依赖 Chromium Safe Storage/Keychain 集成，是平台继承行为而非 ava 的保证；profile 目录权限不收紧则同机其他用户可读登录态 | profile 目录首建 `chmod 0700`（§2.2）；**声明**：ava 不另立 at-rest 加密承诺；主机被入侵场景不在威胁模型内（彼时登录态在内存中同样可用） |
| **RF-15** | **crawl4ai 默认写 `~/.crawl4ai` 与 pi 共用及内置盘污染（S16 🔵-5，人裁决方案 B）** | `import crawl4ai` 会建 `.crawl4ai/` 与 5 个空内容目录，实例化 `AsyncWebCrawler` 会建 `robots/robots_cache.db`（stdlib sqlite3 WAL）；其基目录在模块首次 import 时定死（`async_database.py:17-20` 读取 `CRAWL4_AI_BASE_DIRECTORY`），构造传参盖不住，且 `~/.crawl4ai` 同时被 pi 侧 `agent_crawl` 使用 | 在 `_default_crawl_fn` 函数级 `import crawl4ai` 之前：先验 `data/` 可达，再直接赋值 `os.environ["CRAWL4_AI_BASE_DIRECTORY"] = str(paths.DATA / "crawl4ai")`（禁 `setdefault`，防外部为 pi 设的值令两边重回共用；若导入前已在 `sys.modules` 且绑定目录不符则抛 `ValueError` 诚实失败）。`data/crawl4ai/` 下仅含上述空内容目录与 `robots_cache.db`，ava 业务逻辑不读取、可随时删除重建 |

---

## 附：行号核实自查表（v0.4：2026-09-23 对照工作树全表重核；区间口径沿用 Spec 4 v0.3「def 行至下一个 def/文件末的前一非空行」；红队一轮 🟡-7 反证经 awk 独立复核后修正；二轮差异清单 8 项 ✓ 抽核相符、1 项 ✗ 已修；三轮无新增源码行号引用，改动全在 spec 内部设计与测试规格）

| 引用 | 核实结果 |
|---|---|
| `tools.py:52-58 RESTRICTED_EGRESS_PATTERNS` / `278-289 assert_egress_boundary(endpoint, content)` | ✓ def 行 52 / 278 grep 核实（与 Spec 4 v0.3 自查表一致） |
| `tools.py:317-331 ToolContext`（@dataclass 装饰器 317、class 318；scope/episode_dir/root/confirmed 四字段 324-327；base property def 330） | ✓ awk 逐行核实（v0.1 写「317-331」含装饰器口径本成立；红队括注「316 @dataclass」偏一行，实测 315/316 为空行——澄清登记，见 §1.1 🟡-7） |
| `tools.py:334-428 TOOL_SCHEMAS`（6 条目）/ `431-437 tool_names_for_scope` / `440-455 build_tool_schemas`（448-452 未注册名 KeyError，for 在 447；453 剔除 side_effect 单键——**当前工作树未做白名单化**，白名单化属 Spec 4 PR2） | ✓ def 行 334/431/440 grep + sed 440-456 逐行核实（v0.1 误作 447-452，🟡-7 已修） |
| `tools.py:643-650 _TOOL_IMPLS` / `653-670 execute_tool`（658-659 未注册短路；660-665 scope 闸；666-667 try；668-669 except 四异常；670 ok 包装） | ✓ def 行 643/653 grep + sed 653-670 逐行核实 |
| `cli.py:542-642 _default_approve`（591 side_effect 取值；592-606 只读分流；606 return (True, "")；616 render_approval_card；629 input；636 log_approval_decision；639/641-642 通过/CANCEL 返回） | ✓ grep + sed 590-642 逐行核实 |
| `cli.py:695-699` PermissionError → [BLOCKED] + messages.pop；`cli.py:689-712` except 面全段：仅 LLMError（691-694）与 PermissionError（695-699）两支，**无 Exception 兜底**（🔴-1 机理链关键环） | ✓ sed 685-712 全段核实（v0.2 新增引用） |
| `cli.py:624 target_str` 现状 `args.get("filename", "") or args.get("command", "")`——browser 参数无此二键则恒空串（🟡-5） | ✓ sed 622-626 逐行核实（v0.2 新增引用；§4.5② 一行级扩展的依据） |
| `llm.py:125-139 chat_complete payload 与 urllib POST`（129 assert_egress_boundary）/ `193 build_tool_schemas` / `212-213 approve(name, args)` / `227-231 tool outcome 入会话` | ✓ grep + sed 核实（llm.py 全文 234 行） |
| `status_card.py:140 render_approval_card` / `282-315 log_approval_decision`（310 decision_latency_s） | ✓ grep 核实 def 行；282-315 沿用 Spec 4 v0.3 自查表（同日同树） |
| `paths.py:20 ROOT` / `118-127 atomic_write` / `131 require_data` | ✓ grep + sed 核实（v0.1 误作 118-128，128 为空行，按自家口径修，🟡-7） |
| `pyproject.toml:39 [project.optional-dependencies]`（apple 43-48，49 为注释行；dev 51）；依赖清单无 crawl4ai/playwright/camoufox | ✓ 全文 85 行逐行核实（v0.1 误作 80 行与 43-49，🟡-7 已修） |
| `.gitignore:2 /data`、`8-9 *.local.json` | ✓ 全文核实 |
| `config/agent/tools.json`（22 行；creative 5 / pipeline 4 / asset [] / idea 4——**Spec 4 未施工的当前形态**） | ✓ cat 逐键核实；§4.4 diff 以 Spec 4 落地后形态为基准已声明 |
| `config/agent/` 目录内容：仅 `scopes/` + `tools.json`（web.json 不存在） | ✓ ls 核实 |
| `pipeline/jobs.py` / `pipeline/approvals.py` / `pipeline/agent/web.py` | ✗ **均不存在**（ls 两目录核实）；§6.1/§6.2 施工依赖如实声明 |
| `tests/test_agent_tools.py:311-316 SPEC_TOOLS` / `:405` creative tools 断言 / `:546-578` scope 隔离（沿用 Spec 4 自查表） | ✓ grep 311/405 核实；546-578 沿用同日同树自查表 |
| `tests/test_docs_invariants.py`（191 行，8 用例；spec 版本一致性检查只针对 2026-09-18 impl spec） | ✓ 全文通读 + 基线 8/8 绿（2026-09-23 实测） |
| `docs/dev/STANDARD.md:196-199` 升级链条款（196 条首起、199 行收「--headed…严禁直连主力 Chrome Default」；含 `~/.config/pi-browser-profile` 先例） | ✓ sed 194-200 + grep 行号核实（v0.1 误作 196-198，引文尾部落在 199，🟡-7 已修；Spec 4 §4.1 docstring 同病，继承源登记） |
| `docs/dev/adr/0020`「不做的事」（无 guardian LLM）/ `0021` §1 四级表、§2 profile 钉死、§5 extras、推翻条件、「不做的事」封顶 ~12 | ✓ 全文通读核实 |
| `docs/dev/plans/2026-09-22-harness-evolution-direction.md` §2 Spec 5、§4 红线八条、§5 排除项 | ✓ 全文通读核实 |
| Spec 2 spec：`EventType` 枚举（§3.2）、`emit(event_type, payload, *, episode_dir=None)`（§4.1，spec 行 469-475）、`get_publisher`（626-634）、`close(timeout=0.5)`（592）、`_reset_global_publisher_for_testing`（637-645）、同步点铁律（spec 行 1092）、sink 隔离铁律（1094） | ✓ grep + sed 对 spec 文件核实（jobs.py 本体未施工，签名以 spec 文本为准） |
| Spec 4 spec：§3.1 web.json 基座契约、§3.4 两类错误、§4.1 web.py 签名集、§4.2 白名单三键、§4.4 回显分支、§6.2 jobs 不存在声明、§7.1 网络隔离铁律（🟡-10）、§2.7 位次台账 | ✓ 全文通读核实 |
| crawl4ai / playwright 的 API 形态（`launch_persistent_context(user_data_dir=...)`、fit_markdown 等） | **约**（未对所装版本逐一核实；§4.1/§4.2 标注，RF-1 登记，施工时以实测版本钉死） |
