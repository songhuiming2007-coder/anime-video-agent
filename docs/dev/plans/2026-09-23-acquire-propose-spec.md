# Implementation Spec：acquire_propose 受控素材提案（Spec 6 / ADR-0021 §3）

日期：2026-09-23（**v0.3**，红队三轮收口（一轮 6🔴+7🟡+5🔵、二轮 4🔴+2🟡+2🔵 全收，三轮确认通过）；状态：**可动工**）  
上位文档：`docs/dev/plans/2026-09-22-harness-evolution-direction.md`（§2 Spec 6，§4 施工红线八条），`docs/dev/adr/0021-network-and-asset-tools-internalization.md`（§3 素材获取三层分工，§「不做的事」）  
格式与契约范本：`docs/dev/plans/2026-09-22-jobs-and-events-spec.md`（Spec 2 v0.4，红队四轮收口），`docs/dev/plans/2026-09-22-approval-objectification-spec.md`（Spec 3 v0.2，红队两轮收口）  
`why` 判据真源：`skills/acquire-assets/SKILL.md` §一（交接契约，全文已精读）  
红队一轮报告：会话内下达（2026-09-23，未落盘；6🔴 + 7🟡 + 5🔵，总裁决「打回，v0.2 修订后再审」）  
红队二轮报告：会话内下达（2026-09-23，未落盘；4🔴 + 2🟡 + 2🔵，总裁决「打回 v0.3 轻修，预期第三轮为确认轮」）  
红队三轮报告：会话内下达（2026-09-23，未落盘；确认轮，总裁决「通过红队评审，建议收口」）

---

## 0. 一句话设计

**提案只是提案，抓取永远等人批；写盘走锁，判重走 URL，schema 一字不增。**  
新增纯 stdlib 模块 `pipeline/candidates.py`：把 candidates.json 的 schema 纯函数（`load_candidates` 等 6 个）从 `pipeline/acquire.py` **物理下移**至此并 re-export 保持既有 API 零回归——因为 `acquire.py:35-36` 顶层经 `ingest.py:38 → subindex.py:22-24` 链式拉入 `numpy / pysubs2 / sentence_transformers`，LLM 工具侧一旦 import acquire 即污染 agent 进程（现状证据见 §2.1）；同模块新增 `propose_candidates()`——`data/library/incoming/candidates.json` 的**唯一程序化写入点**，兄弟锁文件 flock 排他包裹读-改-写全程 + `paths.atomic_write`（`paths.py:118-128`）+ 双端 resolve 防 symlink（纪律模板 `tools.py:60-123 write_episode_file`）+ URL 双源判重（候选清单 + `fetched.json` 台账）；agent 侧新增 LLM 工具 `acquire_propose`（`TOOL_SCHEMAS` +1，**asset scope 唯一可见**，不标 `side_effect=False` 故每次调用必过人审卡）。**人审闸门与 `pipeline.acquire` 的 fetch/gate/register 行为零改动**：工具零网络 I/O，fetch 永远由人逐条批准后走既有白名单命令。

---

## 1. 红队裁决与修订纪要

> **第三轮确认轮收口记录（总裁决「通过红队评审，建议收口」）**：二轮裁决 8 项 + 3 处自查新增全部落实（🔴-r1~r4 行号、🟡-r5 卡面清洗、🟡-r6 grep 口径、🔵-r7/r8 逐项核实通过）；红队认账其两处括注数据错误（清洗先例 162-163、`if ep_dir:` 635），连同一轮的「15 字」共 3 处均由修订方独立复算后按实测落笔；自查表 30 行现存引用终核无误（含补验 `tools.py:247` asset 分支起点、`paths.py:126-128` 三步分解）；设计层终审无新发现（三层零 fetch 闭合、写纪律四道、人审闸门零回归、MUT-1~13 机理全成立、RF-8/10/11 缺口各有着落）。状态变更（「可动工」）由维护者落笔（2026-09-23），非红队代行。

> **修订方法声明（v0.2）**：采纳前对红队指控逐条独立复核（sed/awk/grep 对照工作树），属实才改。复核要点：`test_acquire.py:19` import 行、`acquire.py:450` 取候选行、`tools.py:78-123` 逐窗口、`tools.py:668` except 行、`network-tools-spec.md:136` 引文落点、`paths.py:144/149/154` 三处 raise、`cli.py:624` target_str、`status_card.py:201/269` 空卡渲染面——全部属实。红队 🟡-13 括注「15 字」复核为 **17**（`len('高质量的 Live 视频，值得收藏')` 含 2 空格与全角逗号），按 17 修（结论不变）。另：修订自查发现红队 🟡-9 的同根上游问题（审批卡对本工具渲染空卡），一并收治，见下表 🟡-9 行。

### 1.1 第二轮红队复审纪要（v0.2 → v0.3，4🔴 + 2🟡 + 2🔵 全收 + 3 处自查同类残留；原裁决「打回 v0.3 轻修」）

> **修订方法声明（v0.3）**：采纳前逐条独立复核（awk/grep 对照工作树）。红队两处括注数据按实测纠正：① 🟡-r5 的清洗先例实为 `status_card.py:162-163`（162 `splitlines` 取首行、163 `re.sub` 剥离控制字符；164 是 `content = args.get(...)`），红队作 163-164 偏一行；② 🔵-r7 的 `if ep_dir:` 实为 `cli.py:635`（634 是 `norm_decision` 赋值），红队作 634-635 偏一行。结论均不变，按实测落笔。另按红队「以 grep -n 为唯一数据源全表再过一遍」的要求自查，发现其未点名的同类口径残留 3 处（candidates_path / ledger_path / cmd_fetch 尾部空行），一并收治。

| 编号 | 红队指控 | 裁决 | 修订动作 |
|---|---|---|---|
| 🔴-r1 | §4.1 `_WHY_MIN_CHARS` 注释仍写「❌ 示例均 ≤12 字」（🟡-13 漏改，且逐字进 candidates.py 源码，与 §2.7 自相矛盾） | **采纳** | 注释改为「❌ 示例实测 17/4 字（len() 口径）」 |
| 🔴-r2 | §2.3 正文 YAGNI 注释仍引 264-266（266 是 docstring 闭合行）——🔵-14「正文同步」未兑现 | **采纳** | 改 264-265 |
| 🔴-r3 | slugify 290-292 按 v0.2 自立口径即错（return 在 293） | **采纳**（复核：290 def / 291 docstring / 292 `re.sub` / 293 return） | §2.1 迁移清单与自查表改 290-293 |
| 🔴-r4 | `render_approval_card` 140-276（`return "\\n".join(lines)` 实在 279） | **采纳**（grep 复核：279 return、282 `log_approval_decision` def） | §4.5/§8 PR2/自查表改 140-279 |
| 🟡-r5 | §4.5② 新分支渲染模型可控 title/url，未规定控制字符清洗与单行化——卡面可被伪造（含 `\n` 的 title 伪造卡面行、ANSI escape 覆盖危险标记行），「人读卡」防线对本工具失效，正是 §4.5 要修的问题的镜像 | **采纳**（攻击形态成立；先例独立复核为 `status_card.py:162-163`） | §4.5② 补清洗纪律（每字段单行化 + `re.sub(r"[\x00-\x1f\x7f-\x9f]", "", ...)` 剥离，复用 162-163 同款）；T15 补④清洗断言；MUT-13 加第三腿 |
| 🟡-r6 | 门禁 4 naive grep 必中 §4.1 docstring 自指（「顶层无 urllib/socket/requests/subprocess」），门禁自我证伪 | **采纳** | 门禁 4 grep 口径写明：限定 `^import `/`^from ` 行（docstring 自述不误伤） |
| 🔵-r7 | §4.5①/T15② 适用边界未声明：记账仅 `ep_dir` 非空时落（`cli.py:635` `if ep_dir:`；无期会话 `resolve_episode_target`（`cli.py:1079`）返回 None）——既有行为非本 spec 引入，但叙事越界 | **采纳** | §4.5 补边界声明半行；T15② 写明 ep_dir 前置 |
| 🔵-r8 | dup_verdicts「URL 查重腿 267-270」：267 是 `return [`，腿本体 268-270 | **采纳** | 自查表改 268-270 |
| 自查 | 红队「全表再过一遍」要求下发现同类口径残留 3 处（未点名）：candidates_path 78-80→78-79、ledger_path 82-84→82-83、cmd_fetch 443-481→443-480（尾部空行计入） | **自纠** | 自查表同步修正 |

### 1.2 第一轮红队裁决与修订纪要（v0.1 → v0.2，6🔴 + 7🟡 + 5🔵 全收 + 1 项自纠新增；原裁决「打回，v0.2 修订后再审」）

| 编号 | 红队指控 | 裁决 | 修订动作 |
|---|---|---|---|
| 🔴-1 | `test_acquire.py` import 行实为 `:19`（`:23` 是 `class TestCheckVideo` 行），3 处引用失实；自查表「行数核了、行号没核」 | **采纳**（复核属实） | §2.1/§4.4/自查表同步修正为 `:19` |
| 🔴-2 | `cands[no - 1]` 实为 `acquire.py:450`，3 处引用 445-447 失实（append-only 第一硬理由的证据指错行） | **采纳**（复核属实：444-446 存在性检查、447 `load_candidates`、448-449 范围检查、**450 取候选**） | §2.4/§3.1/自查表同步修正 |
| 🔴-3 | `write_episode_file` 内部五行引用四行错（实测：scope 闸 78-79 / 白名单检查 83-86 / resolve 语句 91-92 / 父级比对 114-115 / atomic_write 122 / return 123） | **采纳**（逐窗口复核属实） | §2.8 对齐表与自查表全部重核改写 |
| 🔴-4 | `execute_tool` 函数体 653-670、四异常 except 在 668（v0.1 作 653-668/667——把 Spec 4 自查表的正确引用抄错） | **采纳**（复核属实） | §2.8 两处与自查表修正 |
| 🔴-5 | `dup_verdicts` 同名查重腿实为 271-273 | **采纳**（复核属实：URL 查重腿 268-270、同名查重腿 271-273） | §2.3/自查表修正 |
| 🔴-6 | 「asset scope 无写工具」引文在 Spec 4 §2.5（注入安全，`network-tools-spec.md:136`），非 §2.6（SSRF 节）——跨 spec 锚点错节号 | **采纳**（复核属实） | §2.5/§6.1 锚点改为 §2.5 + 行号 |
| 🟡-7 | MUT-5 证伪机理技术性错误：`os.replace` 换 symlink 本体不穿透（`paths.py:126-128`），「外部文件零改动」变异后仍绿，「双双失败」失实 | **采纳**（复核属实） | §7.2 MUT-5 机理重写：单点变红（PermissionError 缺席）+ 防线真实定位（防越界读合并与 symlink 语义破坏，非防穿透写入） |
| 🟡-8 | 台账损坏 → `SystemExit` 穿透路径未封（`ledger_load` raise 在 `acquire.py:378`；捕获面只挂 step 7 `load_candidates`；`execute_tool` 白名单 `tools.py:668` 不含 SystemExit，穿透即杀 REPL） | **采纳**（机理链逐环复核属实） | §4.1 step 4 补捕获面（与 step 7 同挂 catch SystemExit → ValueError）；T2 补台账损坏子场景；新增 MUT-12 |
| 🟡-9 | 审批记账 target 恒空（`cli.py:624` 推导只有 filename/command 键，`candidates` 键无落点；Spec 5 🟡-5 同款坑踩第二次） | **采纳**（复核属实；且修订自查发现同根上游：`render_approval_card` 通用分支对本工具渲染**空卡**——`status_card.py:201` cmd_str 为空、`:269` 兜底「本地只读产物生成」，卡上无标题无 URL，人对空卡按 y = 盲批） | 新增 §4.5：① `cli.py:624` target_str 补 candidates title 摘要（与 Spec 5 v0.2 §4.5② 的 `url` 键扩展幂等重叠）；② `status_card.py` 新增 acquire_propose 专用卡分支（URL 逐条上卡，ledger target 只记 title）；T15/MUT-13 钉死 |
| 🟡-10 | §0「等 5 个」与正文 6 个自相矛盾 | **采纳** | §0 改「等 6 个」 |
| 🟡-11 | 「字节级不动」overclaim：JSON round-trip 整卷重写，人手改过的排版会被 canonical 归一；T4 在真实手改文件上假绿 | **采纳** | §2.4 降级为「内容与顺序不变、序列化归一为 canonical（如实声明）」；T4 前置条件写明「预置文件以同参数 dumps 生成」，断言改 dict/顺序级 + canonical 前置下字节级 |
| 🟡-12 | 敏感 URL（token 直链/内网保留地址）可入档持久化，威胁模型全文缺失（Spec 4 §2.6 已为 web_fetch 建 SSRF 守卫，说明本项目认真对待过） | **采纳** | §10 新增 RF-10：缓冲链（卡上逐条 URL 过目 + fetch 人批 + fetch 侧 `dup_verdicts` 终裁）+ 不建机器判据的正面论证（威胁模型不同：SSRF 守卫防的是模型自主出网，此处执行面是人批 CLI）；§4.5 联动（URL 上卡、不进 ledger 持久记账） |
| 🟡-13 | why 阈值证据失实：v0.1 称「❌ 示例均 ≤12 字」，实测 17 字与 4 字 | **采纳**（自行 `len()` 复核：17/4；红队括注「15」为另一计数口径，不影响结论） | §2.7 证据改写为实测值与口径；20 字下界结论不变（17 < 20 ≪ 40+） |
| 🔵-14 | 尾部空行计入区间的系统性口径偏差 6 处（ledger_load 368-379→368-378、ledger_save 381-384→381-383、ledger_seen_urls 386-388→386-387、ASSET_COMMANDS 43-50→43-49、write_episode_file 60-124→60-123、YAGNI 注释 264-266→264-265） | **采纳**（逐条复核属实） | 自查表立统一口径「def 行至最后一条语句，不含尾部空行」并全表执行；正文同步 |
| 🔵-15 | `paths.py`「SystemExit 140-157」不精确（实 raise 在 144/149/154） | **采纳** | §2.8/自查表精确化 |
| 🔵-16 | candidates.json 损坏恢复路径只有行为没有文字 | **采纳** | §10 新增 RF-11 半行（fail-closed + 人按报错行号手修，`_line_of` 点名行号的设计本意） |
| 🔵-17 | 台账判重与并发 fetch 的 TOCTOU 未登记（后果为零：fetch 侧 `dup_verdicts` 终裁） | **采纳** | §2.3 补 TOCTOU 登记段 |
| 🔵-18 | §2.1 红线 7 引文「重依赖不进热路径」是改写非原文 | **采纳** | §2.1 改引原文：「新模块顶层零重依赖（numpy/ML 栈不进 status/scout/装配器热路径），重依赖函数内延迟 import 或 uv extras 隔离」 |

---

## 2. 关键设计决策（含代码现状证据）

### 2.1 决策 1：schema 纯函数物理下移 `pipeline/candidates.py`——import acquire 即拉 ML 栈，工具侧必须绕开

- **现状证据（逐行核实）**：
  - `pipeline/acquire.py:35-36` 顶层 `from .ingest import register as ingest_register` / `from .ingest import intact`；
  - `pipeline/ingest.py:38` 顶层 `from .subindex import INDEX_DIR, build, _clean, has_index, sniff_encoding`；
  - `pipeline/subindex.py:22-24` 顶层 `import numpy as np` / `import pysubs2` / `from sentence_transformers import SentenceTransformer`。
  - 结论：**`import pipeline.acquire` 必然链式载入 numpy + sentence_transformers**。即便在 tools.py 里做函数级延迟 import，asset scope 会话第一次调工具就把数百 MB 的 ML 栈拉进 agent 进程——为写一个 JSON 文件，正是 direction §4 红线 7 防的形态（红线 7 原文：「新模块顶层零重依赖（numpy/ML 栈不进 status/scout/装配器热路径），重依赖函数内延迟 import 或 uv extras 隔离」，🔵-18 改引原文）。
- **决策**：新建 `pipeline/candidates.py`（顶层仅 stdlib + `from pipeline import paths`），把 candidates.json 的 **schema 纯函数层**从 acquire.py 搬入：
  - `_line_of`（现 `acquire.py:215-226`）、`load_candidates`（现 `acquire.py:229-257`）、`slugify`（现 `acquire.py:290-293`）、`ledger_load`（现 `acquire.py:368-378`）、`ledger_save`（现 `acquire.py:381-383`）、`ledger_seen_urls`（现 `acquire.py:386-387`）；
  - acquire.py 改为 `from .candidates import ...` **re-export**，对外 API 与行为一字不变——`tests/test_acquire.py:19` 走 `from pipeline import acquire as A` 属性访问，re-export 后零改动全绿；
  - acquire.py docstring（行 16-18）本就自认「纯函数层与执行层分开」，搬迁是顺势收口，不是新发明；`dup_verdicts`（`acquire.py:260-273`）、`fetch_argv`、三个子命令与全部执行层**留在 acquire.py 原地不动**（它们消费 ingest/ffprobe，本就属于重侧）。
- **为什么不是「propose 直接放 acquire.py」**：见上，import 链污染。为什么不是「propose 模块里复制一份校验」：schema 双源分叉（红队必打项），T12 的 identity 断言（同一函数对象）专杀此捷径（§7.2 MUT-11）。

### 2.2 决策 2：并发写——单写入点 + 兄弟锁文件 flock 包裹读-改-写全程（正面回答问题 1）

- **现状**：candidates.json 今天没有任何程序化写入者（acquire.py 只读它，`cmd_fetch` `acquire.py:443-480` 读；人手工编辑）。`acquire_propose` 是第一个程序写入者，多会话并发提案是真场景（两个 REPL 同时跑 asset scope）。
- **决策**：
  1. **单写入点**：`pipeline/candidates.py:propose_candidates()` 是 candidates.json 的**唯一程序化写入路径**；本 spec 之外不存在第二条代码写路径（门禁 3 grep 断言）；
  2. **跨进程**：兄弟锁文件 `incoming/candidates.lock`（append 模式打开、内容为空），`fcntl.flock(LOCK_EX)` **包裹读-改-写全程**（读现有清单 → 判重合并 → 整卷校验 → `paths.atomic_write` 覆写 → 释锁）。锁加在兄弟文件而非目标文件上，因为 atomic_write 的 `os.replace` 换 inode，对目标文件 flock 锁不住后来者（Spec 3 §2.1 同款论证）；
  3. **进程内**：模块级 `threading.Lock` 先串行化同进程线程，再进 flock 区块；
  4. **读侧无锁**：`cmd_fetch` 只读 candidates.json，atomic_write 保证它要么读到旧整份要么读到新整份，无半份（`paths.py:118-128` docstring 的设计意图）；
  5. **人工手改不走锁（已知上限登记）**：人直接编辑 candidates.json 不经锁——竞态窗口是工具持锁的毫秒级，且 `os.replace` 保证整份替换无交织；最坏情形是与工具写入同时手改导致后写覆盖先写（last-writer-wins）。登记为 RF-4，不做自动化（给人侧加锁是过度工程）。
- **不选「单写入进程/队列」**：ava 无常驻进程（core 无 server，ADR-0018 保留条款），flock 是文件系统级现成原语。

### 2.3 决策 3：判重键 = URL 精确串，双源判重（正面回答问题 2）

- **决策**：判重键为 **URL 字符串**（`strip()` 空白后即归一，不做语义归一化），双源：
  1. 既有 `candidates.json` 全部条目的 `url`；
  2. `incoming/fetched.json` 抓取台账的 URL 集（复用 `ledger_seen_urls`，现 `acquire.py:386-387`）——skill 明文「别把已经入库的当候选」「URL 抓过 fetch 会拒」，提案层提前挡掉比让人审读垃圾条目更省人时。
- **命中即跳过**：重复条目**不追加**，结果对象逐条报告 `{"url", "reason"}`（`已在候选清单` / `已在抓取台账`），同批其余条目照常入库——与 `load_candidates`「批量里一个坏不打断整批」（E10）同哲学。
- **为什么精确串而非语义归一**（去 utm、http→https、尾斜杠折叠）：fetch 侧的既有闸门 `dup_verdicts`（`acquire.py:260-273`）就是精确串匹配（`url in seen_urls`），提案层与它同口径，避免「提案层放行的 fetch 层拦」或反过来的双口径分裂；语义归一化的漏网（同内容不同 URL）由人审闸门兜住，且 acquire.py:264-265 注释已对「跨源内容级去重」明示 YAGNI。
- **slug 撞名不查**：`slugify` 折名撞车由 fetch 时 `dup_verdicts` 的「同名查重」腿（`acquire.py:271-273`）拦截，提案层不重复建设。
- **台账 TOCTOU 登记（🔵-17）**：提案读台账与 `cmd_fetch` 之间不持同一把锁（fetch 不持 `candidates.lock`），提案落盘后台账可被新的 fetch 改变——**后果为零**：fetch 侧 `dup_verdicts`（`acquire.py:260-273`）是终裁，重复 URL 照样拒抓；提案层判重只是省人时的前置筛，不承担正确性。

### 2.4 决策 4：append-only——不删、不改、不重排；清理是人的显式动作（正面回答问题 3）

- **硬约束来源**：`cmd_fetch`（`acquire.py:443-480`）按 **1-based 数组序号**取候选（`c = cands[no - 1]` 在 `acquire.py:450`），人审卡上念的「fetch 3」就是这个序号。**工具若重排/删除中间条目，人手里的序号即错位**，会把 B 素材当成 A 抓回来——这是 append-only 的第一理由，比「审计」更硬。
- **决策**：
  1. `propose_candidates` 只在数组**尾部追加**新条目，既有条目的**内容与顺序不变**——如实声明（🟡-11）：写入是 JSON round-trip 整卷重写，序列化归一为 canonical 格式（同参数 `json.dumps(indent=2, ensure_ascii=False)`）；**归一不等于不动**——人手改过的排版（缩进、键序、尾随空行）会被规整，此行为无害且可辩护（格式单源化），但 spec 不把「归一」写成「不动」；
  2. **过期与清理走人工**：已 fetch 的条目留在清单里（fetch 时被台账拦截，`dup_verdicts` URL 查重腿），人认为清单太长就手动删行——那是人的显式动作，与「重抓先清台账」同款纪律（skill §二「不要重复跑 fetch」）；
  3. 本 spec **只定契约不定自动化**：不写清理命令、不写过期字段、不写 TTL（direction §4 红线 1 精神：JSON 文件 + 人工足够覆盖 ava 规模）。
- **契约后果**：candidates.json 只增不减（程序侧），文件体积以百条计，无任何性能问题；体积失控时人手动清理，RF-2 登记。

### 2.5 决策 5：scope 归属——asset scope 唯一可见（正面回答 scope 问题）

- **决策**：`acquire_propose` 只进 `config/agent/tools.json` 的 `asset` 键；`creative` / `pipeline` / `idea` 三键一字不动。
- **论证（四点）**：
  1. **写域归属**：candidates.json 在 `data/library/incoming/`（素材池交接区），不在任何期目录。creative scope 的写权限被 `CREATIVE_WRITABLE_FILES`（`tools.py:24-27`）写死为**期产物两个文件**（`01-topic.md` / `02-script.draft.md`），把 library 级写塞进 creative 会模糊「期写域 vs 库写域」的既有边界；
  2. **工序归属**：`ASSET_COMMANDS`（`tools.py:43-49`）就是 Phase 0 素材工序白名单（`ingest.phase0` / `shots` / `vindex` / `faces`），素材提案是它的天然上游；ADR-0021 §2 把网络工具给 asset scope 的理由（「Phase 0 素材扩充」）原样适用于提案工具——检索（web_search/web_fetch，Spec 4）与提案（acquire_propose）在同一 scope 闭环，模型不用跨 scope 搬 URL；
  3. **pipeline scope 永不可见**：ADR-0021 §2 明文网络工具对 pipeline 不可见（渲染/质检是确定性工序）；提案工具同理——pipeline scope 会话没有素材判断业务；
  4. **idea scope 不加**：idea 是写权限为零的选题会话（`cli.py:725`，Spec 4 §2.2 同款论证），扩可见性须另立 ADR。
- **asset scope 现状（核实）**：`config/agent/tools.json` 中 `"asset": []`（显式空表语义，`tools.py:431-437 tool_names_for_scope` docstring）；`config/agent/scopes/` 下**无 asset.md**（仅 creative/director/idea/pipeline 四个，`load_scope` `scopes.py:31-49` 回落默认提示）。本 spec 不补 asset.md（提示词工程超出范围，RF-6 登记）。
- **与 Spec 4 RF-2 表述的冲突处理**：Spec 4 §2.5（注入安全决策，`2026-09-23-network-tools-spec.md:136`）论证防线时写过「asset scope 会话里模型连写工具都看不到」。本 spec 落地后该句过时——但 Spec 4 同段已写明**真实防线是「全部副作用工具都在人审卡之后」**：`acquire_propose` 不标 `side_effect=False`，`cli.py:591-592` fail-closed 默认 True，**每次调用必弹人审卡**，防线不削弱。Spec 4 已冻结文本不回改，此处正式登记该表述的演进（§6.1）。
- **每次一卡是否扰民**：工具参数是**数组**（一批候选一次调用），一张卡审一整批；且这张卡是写盘护栏，**不是**人审闸门本身——闸门是 fetch 的逐条批准（§2.6），两层不混淆。

### 2.6 决策 6：人审闸门零改动 + 不许自动 fetch 的机械化落法

- **闸门现状（不动清单）**：人逐条批准 → `python -m pipeline.acquire fetch <N>`（`acquire.py:443-480`，含 `--dry-run`、台账查重拒抓）→ `gate`（`acquire.py:393` 起）→ `register`（`acquire.py:483` 起，复用 `ingest.register` 强制 intact）。本 spec 对这三个子命令的函数体**零 diff**（§8 PR1 范围闸门；`tests/test_acquire.py` 全绿 + T12 identity 断言做回归）。
- **「不许自动 fetch」的机械化**：
  1. `pipeline/candidates.py` 顶层 import 白名单**不含任何网络模块**（`urllib` / `socket` / `requests` / `httpx` / `subprocess`），独立子进程纯洁性探针静态断言（T13①）——模块里物理上没有发起网络调用的能力；
  2. `acquire_propose` 的参数 schema 只有 `candidates` 一个字段（候选数组），**没有 URL 抓取语义参数**；工具实现全文无一处网络调用（门禁 4 grep 断言）；
  3. ADR-0021 §3 的分工原样成立：fetch/gate/register 走既有 `pipeline.acquire` 白名单命令，**不新增任何 LLM 抓取工具**。
- **「证据」从哪来（正面回答问题 4）**：提案的证据 = **模型上下文中已有的观察**——Spec 4 的 `web_search`/`web_fetch`、Spec 5 的 `crawl`/`browser` 抓回的页面内容，经模型的判断浓缩进 `source` 字段（「从哪儿来的：站点名 + 大致检索路径」）与 `why` 字段（「补哪个缺口 + 凭什么认为是它」）。工具本身不替你抓一个字节；模型没有先检索就写不出合格的 `why`——skill 的判据天然强制「先证据后提案」。**不新增 evidence 字段**：schema 冻结（§2.7），`source` + `why` 已承载证据链。

### 2.7 决策 7：schema 冻结，`why` 充分性沿用 skill 判据——工具层只做软 WARN

- **schema 双真源**：`skills/acquire-assets/SKILL.md` §一 交接契约表（`title`/`url`/`type`/`source`/`why` 必填，`expected_dur` 可空）与 `load_candidates`（`acquire.py:229-257`）的机检（必填非空、`type ∈ ("live","mv","scan","interview")`（`acquire.py:58`）、`url` 可解析 http/https、`expected_dur` 为数或 null）。两者当前一致。
- **历史数据现状（如实声明）**：本机 `data` 是指向 `/Volumes/Samsung T7/anime-video-data` 的符号链接，撰写时**外置盘未挂载**（悬空），`data/library/incoming/candidates.json` 历史实例无法逐字节核对。但 schema 的权威真源是**代码校验器 + skill 文档**而非数据实例（E1 产物即状态的反向：校验器定义何为合法产物），故本 spec 按**冻结**处理：不加字段、不改字段语义、不改校验规则。红队若挂载 T7 后发现历史实例含额外字段，不影响本决策——`load_candidates` 对额外字段既不拒也不消费，冻结语义不变。
- **`why` 充分性的工具层落法**：skill 的判据（「补哪个缺口 + 凭什么认为是它」；「可能」不许进判断 S7）本质是**内容判断**，skill 把它交给 agent 自查 + 人审闸门（「人扫一眼把垃圾挑掉」）。工具层不发明新判据、不做硬拒（硬拒 = 把审美判断包装成机器判据，direction §5 排除项同源），只做两件事：
  1. **全卷校验复用**：合并后的整份清单过 `load_candidates`（catch `SystemExit` → `ValueError` 回喂模型），保证工具写出的文件 fetch 一定读得进；
  2. **软 WARN 回喂**：逐条 lint——`why` 去空白后 < 20 字、或含「可能」二字 → 结果对象 `warnings` 里点名该条（**不阻断写入**），让模型当轮自查改写。阈值依据：skill 的两个 ✅ 示例均 ≥ 40 字；两个 ❌ 示例实测 17 字（「高质量的 Live 视频，值得收藏」）与 4 字（「可能有用」）（`len()` 口径，含空格与全角标点，🟡-13 修正）——17 < 20 ≪ 40+，20 字是区分带内的保守下界；「可能」命中 skill 明文的 ❌ 示例（S7）。WARN 不拒 = S4「拿不到能证伪的信息就不定罪」——引用原文含「可能」二字（如访谈标题）不该被机器拒。
- **充分性终审在人**：与 ADR-0021 §3「candidates.json 必须人逐条批准」的闸门语义一致。

### 2.8 决策 8：写入纪律对齐 `write_episode_file`（`tools.py:60-123`）——白名单、双端 resolve、atomic_write、存储红线

逐条对齐（模板函数逐行精读，行号已核实）：

| 纪律 | `write_episode_file` 先例 | `propose_candidates` 落法 |
|---|---|---|
| 写目标白名单 | `CREATIVE_WRITABLE_FILES` 两文件（常量本体 `tools.py:24-27`，检查 `tools.py:83-86`） | 写目标**更窄**：固定为 `incoming/candidates.json` 一个文件，路径不从模型参数取（模型只给候选数组，不给路径） |
| scope 闸 | 非 creative 零写权限（`tools.py:78-79`） | 经 `execute_tool` 既有三层闸（`tools.py:653-670`）：注册表 → tools.json scope 白名单 → 实现层边界 |
| 双端 resolve 防 symlink | 期目录与目标各自 resolve（`tools.py:91-92`），父级比对（`tools.py:114-115`） | `incoming/` 与 `candidates.json` 各自 resolve；`target.parent != incoming_resolved` 或 target 逃逸 `data/library` 即 `PermissionError`（T5 双场景） |
| 原子落盘 | `paths.atomic_write`（`tools.py:122`） | 同款，锁内调用 |
| 存储红线 | `data/episodes` 不可达即拒（`tools.py:106-107`） | `data/` 悬空（符号链接不可达）或 `data/library` 不在 → `PermissionError`，**零 mkdir**；`incoming/` 子目录在 data 可达前提下 `mkdir(parents=True, exist_ok=True)`（沿用 `acquire.incoming()` `acquire.py:66-76` 先例，**不**走 `require_data()`——它 raise `SystemExit`（`paths.py:144/149/154`，函数体 131-158），工具层必须回 `PermissionError` 喂模型） |

- **不复用 `acquire.incoming()` 的原因**：它内部 `require_data()` 失败走 `SystemExit`（`paths.py:144/149/154`），`execute_tool` 的异常白名单（`tools.py:668`，仅 `PermissionError/FileNotFoundError/ValueError/OSError`）兜不住，SystemExit 会穿透工具回路。propose 自行做等价检查并抛 `PermissionError`。

---

## 3. 数据契约（Data Contracts）

### 3.1 `candidates.json` Schema（冻结，与 skill §一 及 `load_candidates` 一致）

```json
[
  {
    "title": "EGOIST LIVE 2023 パシフィコ横浜 全场",
    "url": "https://example.com/watch?v=xxxx",
    "type": "live",
    "source": "YouTube 搜「EGOIST 横滨 1009 全场」，官方频道投稿",
    "why": "终场 Live 只入库了 19 分钟精华版（SP05），这条是 2 小时全程，补「消散段落」的完整过程；标题卡与 SP05 帧内实证一致",
    "expected_dur": 7200
  }
]
```

| 字段 | 必填 | 机检（`load_candidates` 现状，冻结） |
|---|---|---|
| `title` | ✅ | 非空 |
| `url` | ✅ | 非空且 `urlparse(...).scheme ∈ {"http","https"}` |
| `type` | ✅ | `∈ ("live","mv","scan","interview")`（`acquire.py:58 TYPES`） |
| `source` | ✅ | 非空 |
| `why` | ✅ | 非空（充分性判据见 §2.7，软 WARN 不进机检） |
| `expected_dur` | 可空 | 缺省/null/int/float；其他类型拒 |

- **顶层必须是数组**；元素必须是对象；坏条目**一次报全**并点名行号（`load_candidates` 既有语义，`tests/test_acquire.py:156-180` 已钉死）。
- **条目顺序语义**：数组下标即 `fetch <N>` 的 N（1-based，`c = cands[no - 1]` 在 `acquire.py:450`）——顺序是契约的一部分，§2.4 append-only 的依据。

### 3.2 `propose_candidates` 返回对象（工具回喂模型的形状）

```json
{
  "path": "/Volumes/.../data/library/incoming/candidates.json",
  "added": ["EGOIST LIVE 2023 パシフィコ横浜 全场"],
  "skipped": [{"title": "...", "url": "...", "reason": "url 已在抓取台账（fetched.json）"}],
  "warnings": [{"title": "...", "warning": "why 仅 8 字（<20），说不清补哪个缺口——按 skill §一 改写或删除"}]
}
```

- 全部条目判重命中（零 added）不算错误：正常返回 `added: []`，由模型决定收手或换源；
- 校验失败（schema 不过）**整批拒写**（文件字节不动），`ValueError` 携带 `load_candidates` 的原文报错（含行号）。

### 3.3 锁文件与物理布局

- `data/library/incoming/candidates.lock`：flock 载体，append 打开、内容为空、不进 git（`data/` 整树不进 git，与 events.jsonl 同层纪律）；
- `candidates.json` 整文件覆写走 `paths.atomic_write`，`json.dumps(..., ensure_ascii=False, indent=2)`（与 `ledger_save` `acquire.py:381-383` 同风格，便于人读与 diff）；
- 无容量封顶：条目以百计、人手动清理（§2.4）；封顶是数据库思维，direction 红线 1 不批。

---

## 4. 模块接口与签名设计

### 4.1 `pipeline/candidates.py`（新模块，纯 stdlib + paths）

```python
"""pipeline.candidates: 素材候选清单（candidates.json）的 schema 纯函数层与受控写入。

纪律：
1. 本模块是 candidates.json 的唯一程序化写入点（propose_candidates）；
2. 提案只是提案：本模块零网络能力（顶层无 urllib/socket/requests/subprocess），
   fetch 永远由人批准后走 pipeline.acquire；
3. append-only：不删、不改、不重排既有条目（fetch <N> 的 N 是数组序号）；
4. data/ 不可达即 PermissionError，绝不自动创建 data 根（paths.py:131-158 铁律）；
5. load_candidates 等纯函数自 acquire.py 物理下移（2026-09-23，Spec 6）：
   acquire.py 顶层经 ingest → subindex 拉 numpy/sentence_transformers，
   LLM 工具侧必须绕开该 import 链。
"""

from __future__ import annotations

import fcntl
import json
from pathlib import Path
import re
import threading
from typing import Any
from urllib.parse import urlparse

from pipeline import paths

# ---- 自 acquire.py 原样下移（re-export 保持 acquire API 零回归）----
# _line_of / load_candidates / slugify / ledger_load / ledger_save / ledger_seen_urls
# （函数体逐字搬迁，不改一个字符；T12 identity 断言钉死）

_PROPOSE_LOCK = threading.Lock()
_WHY_MIN_CHARS = 20  # skill §一 ✅ 示例均 ≥40 字、❌ 示例实测 17/4 字（len() 口径），20 是保守下界（软 WARN，不硬拒）


def propose_candidates(
    entries: list[dict[str, Any]],
    *,
    data_root: Path | str,
) -> dict[str, Any]:
    """受控追加素材候选到 incoming/candidates.json（唯一程序化写入点）。

    流程（全程在 candidates.lock 的 flock LOCK_EX 内）：
    1. data_root 可达性检查（悬空/骨架不全 → PermissionError，零 mkdir data 根）；
    2. incoming/ 不存在则 mkdir（data 可达前提下，acquire.incoming() 先例）；
    3. 双端 resolve：candidates.json 的 resolve 父目录必须等于 incoming 的 resolve
       （symlink 穿透 → PermissionError）；
    4. 读既有清单（缺失视为 []）与 fetched.json 台账（缺失视为 []）；
       台账损坏时 ledger_load raise SystemExit（acquire.py:378）——与 step 7 同挂
       catch SystemExit → ValueError 捕获面（🟡-8：捕获面覆盖 step 4/7 两处，
       漏包即穿透 execute_tool 白名单 tools.py:668 杀掉 REPL）；
    5. URL 精确串双源判重，命中进 skipped；
    6. 新条目尾部追加（既有条目不删不改不重排）；
    7. 合并整卷过 load_candidates（catch SystemExit → ValueError，文件字节不动）；
    8. why 软 lint（<20 字 / 含「可能」→ warnings，不阻断）；
    9. paths.atomic_write 整卷覆写（indent=2, ensure_ascii=False）。

    返回 {"path", "added", "skipped", "warnings"}。
    只抛 PermissionError / ValueError / OSError（execute_tool 异常白名单 tools.py:668
    四件套内），绝不 SystemExit、绝不发起任何网络调用。
    """
```

### 4.2 `tools.py` 注册面变更（增量，既有条目一字不改）

1. `TOOL_SCHEMAS`（`tools.py:334-428`）追加一条：
   ```python
   "acquire_propose": {
       "name": "acquire_propose",
       "adr": "ADR-0021",
       # 不标 side_effect：fail-closed 默认 True（cli.py:591-592），每次调用必过人审卡
       "description": (
           "把素材候选追加到 data/library/incoming/candidates.json（人审用提案清单，"
           "追加式、同 URL 自动跳过）。只写提案，绝不抓取——fetch 永远由人逐条批准后 "
           "走 pipeline.acquire。每条必须含 title/url/type/source/why；"
           "why 要写清补哪个缺口、凭什么认为是它（skill: acquire-assets §一）。"
       ),
       "parameters": {
           "type": "object",
           "properties": {
               "candidates": {
                   "type": "array",
                   "description": "一批候选（一次调用一张审批卡审整批）",
                   "items": {
                       "type": "object",
                       "properties": {
                           "title": {"type": "string"},
                           "url": {"type": "string", "description": "http/https 直链或视频页"},
                           "type": {"type": "string", "enum": ["live", "mv", "scan", "interview"]},
                           "source": {"type": "string", "description": "站点名 + 大致检索路径"},
                           "why": {"type": "string", "description": "补哪个缺口 + 凭什么认为是它"},
                           "expected_dur": {"type": ["number", "null"], "description": "秒数，可空"},
                       },
                       "required": ["title", "url", "type", "source", "why"],
                       "additionalProperties": False,
                   },
               },
           },
           "required": ["candidates"],
           "additionalProperties": False,
       },
   },
   ```
2. `_TOOL_IMPLS`（`tools.py:643-650`）追加一条**延迟 import** 包装：
   ```python
   def _tool_acquire_propose(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
       from pipeline.candidates import propose_candidates
       raw = args.get("candidates")
       if not isinstance(raw, list) or not raw or not all(isinstance(c, dict) for c in raw):
           raise ValueError("candidates 必须是非空的对象数组")
       return propose_candidates(raw, data_root=ctx.base / "data")
   ```
3. `build_tool_schemas`（`tools.py:453`）协议键过滤改白名单（**与 Spec 4 §4.2 第 3 条同款变更，幂等重叠**）：
   ```python
   _PROTOCOL_KEYS = ("name", "description", "parameters")
   fn_schema = {k: v for k, v in TOOL_SCHEMAS[name].items() if k in _PROTOCOL_KEYS}
   ```
   Spec 4 已施工则此 diff 为空操作；未施工则本 spec 携带（否则 `"adr"` 键会泄入 LLM payload）。

### 4.3 `config/agent/tools.json` 变更（护栏变更，逐字 diff）

- 基线 A（Spec 4 未施工，当前真实状态）：`"asset": []` → `"asset": ["acquire_propose"]`；
- 基线 B（Spec 4 已施工）：`"asset": ["web_search", "web_fetch"]` → 尾部追加 `"acquire_propose"`；
- `creative` / `pipeline` / `idea` 三键**一字不动**（范围闸门，§2.5）。施工时按仓库实况二选一，另一基线作废。

### 4.4 `acquire.py` 变更（仅 import 面，行为零 diff）

- 删除已下移的 6 个函数体（`_line_of` / `load_candidates` / `slugify` / `ledger_load` / `ledger_save` / `ledger_seen_urls`），替换为 `from .candidates import (...)` re-export；
- `cmd_fetch` / `cmd_gate` / `cmd_register` / `dup_verdicts` / `fetch_argv` / 全部执行层**一行不动**；
- `tests/test_acquire.py:19`（`from pipeline import acquire as A`）零改动全绿。

### 4.5 审批卡与记账接线（🟡-9 + 修订自查新增的空卡问题）

- **问题（两层同根）**：`acquire_propose` 参数键是 `candidates`，既有通用机制对它两头发空：
  1. **记账空**：`cli.py:624` 现状 `target_str = " ".join(argv) if argv else str(args.get("filename", "") or args.get("command", ""))`——无 `candidates` 键推导，人批的是哪批提案在 `approvals.jsonl` 无任何持久记录（🟡-9，Spec 5 🟡-5 同型坑）；
  2. **卡片空（修订自查新增，🟡-9 的同根上游）**：`render_approval_card`（`status_card.py:140-279`）只有 `write_episode_file` 专用分支与 run_pipeline/cloud/render 通用分支；通用分支对本工具渲染 `cmd_str`（`status_card.py:201`，无 `command` 键 → 空串）与兜底性质文案「本地只读产物生成 | 预计分钟级」（`status_card.py:269`）——**卡上无标题、无 URL，人对空卡按 y 等于盲批**，人审卡这道防线对本工具事实失效。
- **决策**：
  1. **`cli.py:624` target_str 补 candidates 摘要**：推导链尾追加 `or _candidates_brief(args.get("candidates"))`（helper 三行：每条 `title` 以「、」拼接、整体截断 120 字符、空名单回落 `""`）。与 Spec 5 v0.2 §4.5② 的 `url` 键扩展**幂等重叠**：Spec 5 未施工时本 spec 落最终形态（filename/command/url/candidates 四段链），已施工则仅加 candidates 段；
  2. **`render_approval_card` 新增 acquire_propose 专用分支**：逐条渲染 `title（type）url`，>10 条时第 10 条后截断为「…共 N 条」；**URL 上卡**（人审的物质内容就是 URL），但 **ledger target 只记 title 摘要、不记 URL**——标题足以定位批次，敏感直链（token 签名 URL）少一个落盘副本（与 §10 RF-10 联动）。**卡面清洗纪律（🟡-r5，防终端注入）**：title/url 是模型全文生成的不可控字符串，上卡前每字段必须复用 `status_card.py:162-163` 同款清洗——`splitlines` 取首行单行化 + `re.sub(r"[\x00-\x1f\x7f-\x9f]", "", ...)` 剥离控制字符（含 ANSI escape）；否则含 `\n` 的 title 可伪造卡面行（假 URL、假「已批准」）、`\x1b[1A\x1b[2K` 可覆盖「危险标记」行——通用分支不清洗是因为 cmd_str 来自白名单命令（内容受控），本分支输入不受控，「人读卡」防线不许对模型输出失效；
- **边界**：不动 `_default_approve` 的审批流与 `log_approval_decision` 记账函数本体（通用机制直接承载）；新增仅 target_str 推导一行链与卡片一个专用分支。T15/MUT-13 钉死。
- **适用边界声明（🔵-r7）**：记账仅在 `ep_dir` 非空时落盘（`cli.py:635` `if ep_dir:`；无期会话 `resolve_episode_target`（`cli.py:1079`）返回 None）——裸 REPL 切 `/asset` 的无期会话里审批决策不落 ledger，这是**既有行为、非本 spec 引入**；§4.5① 的 target 可定位叙事与 §10 RF-10 的「URL 不进 ledger」联动均以有期上下文为前提。卡片渲染（§4.5②）与期无关，无期会话照样弹卡照样清洗。

---

## 5. 依赖白名单与纯洁性保障

### 5.1 `pipeline/candidates.py` 顶层允许导入清单

- 标准库：`fcntl`, `json`, `pathlib`, `re`, `threading`, `typing`, `urllib.parse`
- 项目轻量库：`from pipeline import paths`
- **严禁**：`numpy` / `torch` / `sentence_transformers` / `pysubs2` / `moviepy` 等 ML 栈；`urllib.request` / `socket` / `requests` / `httpx` / `subprocess` 等**网络与进程能力**（「不许自动 fetch」的物理保证，§2.6）。

### 5.2 独立子进程纯洁性测试（防共享 pytest 进程假阳性）

```python
def test_candidates_module_pure_subprocess():
    """独立解释器探针：import pipeline.candidates 后零重依赖、零网络模块。"""
    import subprocess, sys
    probe = (
        "import pipeline.candidates, sys; "
        "forbidden = ('numpy', 'torch', 'sentence_transformers', 'pysubs2', "
        "             'moviepy', 'requests', 'httpx', 'socket', 'subprocess'); "
        "leaked = [m for m in forbidden if m in sys.modules]; "
        "assert not leaked, f'candidates 顶层违规引入: {leaked}'"
    )
    res = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True)
    assert res.returncode == 0, f"纯洁性检验失败:\n{res.stderr}"

def test_tools_import_does_not_pull_candidates():
    """tools.py 顶层不拉 pipeline.candidates（延迟 import 断言）。"""
    import subprocess, sys
    probe = (
        "import pipeline.agent.tools, sys; "
        "assert 'pipeline.candidates' not in sys.modules, 'tools 顶层拉入 candidates'; "
        "assert 'pipeline.acquire' not in sys.modules, 'tools 顶层拉入 acquire（ML 链）'"
    )
    res = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True)
    assert res.returncode == 0, f"延迟 import 检验失败:\n{res.stderr}"
```

---

## 6. 跨 Spec 接口与系统边界

### 6.1 与 Spec 4（web_search + web_fetch）的接口边界

- **tools.json 同键竞争**：两份 spec 都改 `asset` 键。约定**尾部追加、先到先落**：后施工者按 §4.3 基线 A/B 二选一，冲突面为一行 JSON，评审时逐字核对；
- **`_PROTOCOL_KEYS` 白名单过滤**：与 Spec 4 §4.2 第 3 条是**同一处变更**，幂等重叠——先到者落地，后到者该 diff 自动为空，不重复评审；
- **`adr` 门禁**：本 spec 条目登记 `"adr": "ADR-0021"`（工具表新工具必须有 ADR 编号，direction §4 红线 6；Spec 4 T12 全表门禁落地后本条目天然被覆盖，本 spec 只自证本条目不漏登记）；
- **RF-2 表述演进登记**：Spec 4 §2.5（`2026-09-23-network-tools-spec.md:136`）「asset scope 会话里模型连写工具都看不到」一句自本 spec 施工起过时；真实防线（副作用工具全过人审卡，`cli.py:591-592` fail-closed）不受影响（§2.5）。不回改 Spec 4 已冻结文本；
- **证据链分工**：模型经 web_search/web_fetch 获得的页面观察 → 浓缩进 `source`/`why` 字段（§2.6）。Spec 4 §6.4 已声明「变成素材候选的正确路径是 Spec 6 acquire_propose」，本 spec 即该路径的落地。

### 6.2 与 Spec 5（crawl + browser）的接口边界

零代码耦合。crawl/browser 抓回内容只进 LLM 上下文，变成候选走 `acquire_propose`（Spec 5 §6.4 同款声明）。browser 的 approval 门与本工具的人审卡是两层独立闸门，互不复用。

### 6.3 与 Spec 2 / Spec 3（jobs / approvals）的接口边界

- **零依赖声明（如实）**：截至撰写日（2026-09-23），`pipeline/jobs.py` 与 `pipeline/approvals.py` **均未施工**（Spec 2 v0.4 / Spec 3 v0.2 状态「可动工」）。本 spec 不消费其任何接口、不发射任何事件（`acquire_propose` 不是 pipeline 命令，不进 Job 状态机）；
- **审批记账走既有通道（🟡-9 修订）**：工具调用的人审卡决策由既有 `log_approval_decision` 记账（`status_card.py:282` 起，`cli.py:636` 接线），本 spec 不加新记账路径；但 `cli.py:624` 的 target_str 推导对本工具恒空，§4.5 补 candidates title 摘要扩展（与 Spec 5 的 `url` 键扩展幂等重叠）；Spec 2 PR4 的事件双写落地后自动覆盖本工具，无需改码；
- **candidates.json 与 `pipeline.status` 零关系**：素材提案不是期工序，status.py 对本文件零感知（门禁 4 静态断言同款纪律：status.py 源码不得出现 `candidates` 引用）。

### 6.4 与 acquire-assets skill 的接口边界

skill 是 `why` 判据与三层分工的**文档真源**（§2.7）；本 spec 只把「写 candidates.json」这一步从「agent 手工写文件」升级为「受控工具写文件」，skill 文本不需随本 spec 改动；若红队认为 skill §一应补一句「agent 会话内优先用 acquire_propose 工具」，列为施工可选项而非阻塞项。

---

## 7. 测试规格与变异检验方案

### 7.1 单元测试规格（`tests/test_candidates_propose.py`，新增）

> **期望值先跑再写断言**（AGENTS.md 十二节纪律①）：T1-T8 全部先在 PR1 实现上跑出真值再落断言；§4.3 基线以施工时仓库实况为准先跑一遍。

| 编号 | 测试用例名 | 覆盖场景 | 验证断言 |
|---|---|---|---|
| **T1** | `test_propose_appends_to_fresh_incoming` | 空 incoming 首批提案 | tmp_path 造 `data/library` 骨架；调用后 candidates.json 存在、条目齐全、顺序与入参一致；`load_candidates` 读回校验通过；返回 `added` 全中、`skipped`/`warnings` 为空 |
| **T2** | `test_propose_invalid_batch_refused_file_untouched` | schema 拒写 + 损坏 fail-closed | 预置合法文件快照；分别喂缺 `why`、`type="bd"`、非 http url、顶层传非数组（经 `_tool_acquire_propose`）→ `ValueError`；**文件字节级不变**。子场景①：既有文件本身含坏条目（人手改坏）→ 同样拒写不动（宁可人先修清单）；子场景②（🟡-8 新增）：`fetched.json` 写成非法 JSON（台账损坏）→ **`ValueError` 回喂**（SystemExit 被捕获面包住，不穿透）且 candidates.json 字节不动 |
| **T3** | `test_propose_dedup_candidates_and_ledger` | URL 双源判重 | ① URL 已在 candidates → 该条 skipped（reason 含「候选清单」），不重复追加；② URL 已在 fetched.json 台账 → skipped（reason 含「台账」）；③ 同批内两条同 URL → 第二条 skipped；合法条目照常 added |
| **T4** | `test_propose_append_only_order_stable` | **append-only 序号契约（核心验收 1）** | **前置条件（🟡-11）**：预置 3 条清单以同参数 `json.dumps(indent=2, ensure_ascii=False)` 生成（canonical 前置，否则本测试在真实手改文件上假绿）。追加 2 条后断言：① 读回后前 3 条 dict 逐项相等**且顺序不变**；② 新条目在尾部；③ canonical 前置下既有区段**字节级不变**；④ `load_candidates` 读回第 2 条仍是原第 2 条（`fetch 2` 序号语义不变） |
| **T5** | `test_propose_symlink_escape_refused` | 双端 resolve 防穿透 | ① `candidates.json` 是指向 tmp 外文件的 symlink → `PermissionError` 且外部文件零改动；② `incoming/` 本身是指向 tmp 外目录的 symlink（逃逸 `data/library`）→ `PermissionError` |
| **T6** | `test_propose_concurrent_writers_serialized` | 并发写纪律 | 线程腿：同进程 4 线程 × 5 条并发提案 → 20 条全在、JSON 合法、无交织；**跨进程腿**：主进程对 `candidates.lock` 持 `LOCK_EX\|LOCK_NB`，子进程提案在 2s 内无法完成；释锁后子进程完成且文件可解析（Spec 3 T8 同款确定性持锁挡写，不赌时序） |
| **T7** | `test_propose_data_unreachable_fail_closed` | 存储红线 | ① `data/` 不存在 → `PermissionError`，断言 `data/` **未被创建**；② `data/` 在但无 `library/` → `PermissionError`；③ 合法骨架下 `incoming/` 缺失 → 自动创建（先例行为）且提案成功 |
| **T8** | `test_propose_why_lint_warns_not_blocks` | why 软 WARN（§2.7） | why 含「可能」→ added 且 `warnings` 点名该条；why 仅 8 字 → warnings 点名；充分 why（≥20 字无「可能」）→ warnings 为空；三种情形**文件均正常写入**（WARN 不阻断） |
| **T9** | `test_acquire_propose_scope_mask` | **scope 掩码（核心验收 2）** | 三层：① `acquire_propose ∈ TOOL_SCHEMAS`（注册表常驻）；② `build_tool_schemas("asset")` 含、`("creative")`/`("pipeline")`/`("idea")` 均不含；③ `execute_tool("acquire_propose", ..., ToolContext(scope="pipeline"))` 返回 `{"ok": False, "error": 含"白名单"}`（执行层第二道闸，`tools.py:660-665`） |
| **T10** | `test_acquire_propose_pops_approval_card` | 人审卡纪律 | 静态断言 `TOOL_SCHEMAS["acquire_propose"].get("side_effect", True) is True`——不标 False 即 fail-closed 必弹卡（`cli.py:591-592`）；另断言 `"adr": "ADR-0021"` 在条目内 |
| **T11** | `test_protocol_keys_no_leak` | 宿主元数据零泄漏 | `build_tool_schemas("asset")` 每个 function 键集合恰为 `{"name","description","parameters"}`——`adr`/`side_effect` 不泄入 LLM payload（Spec 4 T11 同款，幂等重叠） |
| **T12** | `test_acquire_reexport_identity` | **闸门零改动回归（核心验收 3）** | `from pipeline import acquire as A` + `from pipeline import candidates as C`：六个名字（`load_candidates`/`slugify`/`ledger_load`/`ledger_save`/`ledger_seen_urls`/`_line_of`）逐一断言 `getattr(A, n) is getattr(C, n)`（同一函数对象，re-export 未包壳未改语义）；外加 `tests/test_acquire.py` 全文件全绿（既有机检回归） |
| **T13** | `test_candidates_purity_subprocess` | 依赖纯洁性 | 见 §5.2 双探针：① candidates 顶层零重依赖零网络模块；② import tools 不拉 candidates/acquire |
| **T14** | `SPEC_TOOLS` 同步更新（`tests/test_agent_tools.py:311-316`） | 真配置 drift 断言 | `SPEC_TOOLS["asset"]` 按 §4.3 落地基线更新（先改真配置与代码跑绿，再更新 SPEC_TOOLS——纪律①）；既有 scope 过滤用例随新表转绿 |
| **T15** | `test_approval_card_and_ledger_target` | 审批卡非空 + 记账可定位 + 卡面清洗（🟡-9/🟡-r5/§4.5） | ① `render_approval_card("acquire_propose", args)` 输出**逐条含 title 与 URL**（11 条时第 10 条后截断为「…共 11 条」）；② **ep_dir 前置（🔵-r7）**：在绑定期目录的 ToolContext 下经 `_default_approve` 按 y 后 `approvals.jsonl` 该条 `target` **含 candidates title 摘要**（≤120 字符）**且不含 URL**；③ Spec 5 的 `url` 键扩展未施工时四段链完整（filename/command/url/candidates），已施工时仅 candidates 段为增量；④ **清洗断言（🟡-r5）**：title 含 `\n` 伪造行与 ANSI escape（`\x1b[`）时，卡面该条**单行且控制字符已剥离**（断言输出不含 `\x1b`、条数不被伪造行膨胀） |

### 7.2 变异检验矩阵（Mutation Testing Matrix）

| 变异编号 | 注入变异（故意写坏代码） | 预期变红的测试 | 证伪机理（为什么必须红） |
|---|---|---|---|
| **MUT-1** | 去掉 `fcntl.flock`（锁文件照开不锁） | T6 跨进程腿 | 主进程持 `LOCK_EX\|LOCK_NB` 不再能挡住子进程：子进程 2s 内直接完成写入，「无法完成」断言失败变红 |
| **MUT-2** | 去掉 candidates 清单判重腿 | T3① | 同 URL 条目被二次追加，`skipped` 为空、`added` 计数超期，断言失败变红 |
| **MUT-3** | 去掉台账（fetched.json）判重腿 | T3② | 已抓取 URL 再次入清单，`skipped` 为空断言失败变红——skill「别把已入库的当候选」的工具层守卫失效 |
| **MUT-4** | 先 `atomic_write` 后跑 `load_candidates` 校验 | T2 | 非法整批落盘成功，「文件字节级不变」断言失败变红（坏文件还会被 T2 后续 `load_candidates` 读回拒，双重显形） |
| **MUT-5** | 去掉双端 resolve 检查（直接拼路径写） | T5 | **单点变红（🟡-7 机理重写）**：变异后函数正常返回而非抛 `PermissionError`，`pytest.raises(PermissionError)` 断言失败变红。「外部文件零改动」腿在变异后**依然为绿**——`paths.atomic_write` 的 `os.replace`（`paths.py:128`）替换的是 symlink 本体，永不穿透写入指向文件；该断言的真实防御对象是「正确实现下拒写发生在读取之前」（resolve 检查步在读取步之前，越界内容从未被读入合并）。防线定位如实：resolve 检查防的是**越界读合并**（经 symlink 读穿 data 域外文件并并入清单）与 **symlink 语义破坏**（本体被静默替换），不是防穿透写入——`os.replace` 天然不穿透 |
| **MUT-6** | 合并时对整卷排序（如按 title 排序「更整洁」） | T4 | 读回 dict 序列比对失败（既有 3 条顺序变化，断言①变红）；canonical 字节比对同步变红——`fetch <N>` 序号契约被击穿的第一防线 |
| **MUT-7** | `TOOL_SCHEMAS` 条目加 `"side_effect": False` | T10 | 静态断言 `get("side_effect", True) is True` 失败变红——人审卡被静默绕过 |
| **MUT-8** | `tools.py` 顶层 `from pipeline.candidates import propose_candidates` | T13② | 独立子进程探针捕获 `pipeline.candidates in sys.modules`，非零退出码变红 |
| **MUT-9** | 去掉 why 软 lint | T8 | 「可能」条目与 8 字条目均无 warnings，点名断言失败变红 |
| **MUT-10** | data 不可达时改为 `mkdir(parents=True)` 兜底 | T7 | `data/` 被创建，「未被创建」断言失败变红——存储红线（幽灵目录）守卫 |
| **MUT-11** | acquire.py 不 re-export，把 `load_candidates` 函数体**复制**留在本地（schema 双源分叉） | T12 | `A.load_candidates is C.load_candidates` 为 False，identity 断言失败变红——schema 单源守卫 |
| **MUT-12** | `propose_candidates` step 4 台账读取不包 SystemExit（只包 step 7） | T2 子场景② | 台账损坏时 SystemExit 穿透而非 ValueError 回喂，「ValueError」断言失败变红——`execute_tool` 白名单（`tools.py:668`）不含 SystemExit，穿透即杀 REPL 会话（🟡-8） |
| **MUT-13** | `cli.py:624` 不补 candidates 键，或 `render_approval_card` 不加专用分支，或新分支去掉清洗（🟡-r5 加腿） | T15 | target 回落为空串（②断言失败）；卡渲染走通用分支缺 URL（①断言失败）；含 `\n`/ANSI 的 title 原样上卡（④断言失败）——三腿各自独立变红 |

---

## 8. 施工 PR 划分

### PR1：`pipeline/candidates.py` 新建与受控写入核心（schema 搬迁 + 提案写盘）
- **范围**：新建 `pipeline/candidates.py`（6 个纯函数自 acquire.py **逐字搬迁** + `propose_candidates` 全纪律实现）；`acquire.py` 改 re-export（仅 import 面，执行层零 diff）；`tests/test_candidates_propose.py` 入库 **T1-T8 / T12 / T13①**；`tests/test_acquire.py` 全绿回归。**本 PR 不动 tools.py / tools.json / cli.py**（T9-T11/T13② 此时点尚不存在，验证命令确绿）。
- **验证命令**：`uv run pytest tests/test_candidates_propose.py tests/test_acquire.py`

### PR2：工具注册、scope 掩码与门禁固化（LLM 可见）
- **范围**：`tools.py` 三处变更（TOOL_SCHEMAS +1、`_TOOL_IMPLS` +1 延迟包装、`_PROTOCOL_KEYS` 白名单过滤——Spec 4 已施工则第三处为空操作）；`config/agent/tools.json` 按 §4.3 实况基线逐字 diff；**`status_card.py:140-279 render_approval_card` 新增 acquire_propose 专用分支（§4.5②，含卡面清洗纪律）**；**`cli.py:624` target_str 补 candidates 摘要（§4.5①，与 Spec 5 `url` 键扩展幂等重叠）**；`tests/test_agent_tools.py:311-316 SPEC_TOOLS` 同步（先跑再写）；入库 **T9-T11 / T13② / T14 / T15** 与全部变异检验 MUT-1~MUT-13；更新 `docs/dev/plans/README.md` 索引状态。
- **验证命令**：`uv run pytest tests/test_candidates_propose.py tests/test_acquire.py tests/test_agent_tools.py tests/test_agent_cli.py tests/test_docs_invariants.py`

---

## 9. 验收门禁清单（Accept Gates）

- [ ] **门禁 1（写入纪律）**：越界（data 不可达）、symlink 穿透（两场景）、非白名单目标（路径不从参数取）全部被拒且零副作用（T5/T7）；整卷校验不过文件字节不动（T2）；
- [ ] **门禁 2（判重与 append-only）**：URL 双源判重命中即跳过（T3）；既有条目内容与顺序不变（序列化归一为 canonical，🟡-11 口径），`fetch <N>` 序号语义稳定（T4）；
- [ ] **门禁 3（闸门零改动）**：`cmd_fetch` / `cmd_gate` / `cmd_register` 函数体相对施工前零 diff（PR 评审逐字核对）；`tests/test_acquire.py` 全绿；T12 identity 断言通过；全库 grep candidates.json 的程序化写点仅 `propose_candidates` 一处（atomic_write 调用点）；
- [ ] **门禁 4（零自动 fetch）**：`pipeline/candidates.py` 源码无 `urllib.request` / `socket` / `requests` / `httpx` / `subprocess` 任何引用——**grep 口径限定 `^import `/`^from ` 行**（🟡-r6：模块 docstring 的自述不禁词，naive 全文 grep 会自指误伤）；T13① 子进程探针兜底；`status.py` 源码无 `candidates` 引用；
- [ ] **门禁 5（scope 掩码与人审卡）**：creative/pipeline/idea 三 scope 的 schema 与执行层均不可见本工具（T9）；`side_effect` 未标 False 必弹卡（T10）；`adr` 不泄入 LLM payload（T11）；**审批卡对本工具非空卡**——URL 逐条上卡、ledger target 含 title 摘要且不含 URL（T15）；
- [ ] **门禁 6（依赖纯洁性）**：独立子进程断言 candidates 顶层零重依赖零网络模块、tools 不拉 acquire 链（T13）；
- [ ] **门禁 7（既有测试零回归）**：`tests/test_acquire.py`、`tests/test_agent_tools.py`、`tests/test_agent_cli.py` 100% 通过；
- [ ] **门禁 8（文档门禁全绿）**：`uv run pytest tests/test_docs_invariants.py` 全绿。

---

## 10. 潜在红旗与自纠预案（Red Flags & Remediation）

| 风险序号 | 潜在红旗 | 根因与危险性 | 预案与防御动作 |
|---|---|---|---|
| **RF-1** | **自动 fetch 偷渡** | 施工者「图省事」在 propose 里加一句「顺便验证 URL 可达性」——一次 HEAD 请求就击穿「不许自动 fetch」红线（版权与带宽风险转嫁机器） | §5.1 白名单物理剥夺网络能力；门禁 4 grep + T13① 子进程探针双层守卫；ADR-0021 §「不做的事」明文禁批量自动 fetch |
| **RF-2** | **清理/重排自动化破坏序号** | 「清单太乱，顺手排序/去重压缩」——`fetch <N>` 序号错位，人把 B 素材当 A 抓回几百 MB | §2.4 append-only 写死为契约；T4/MUT-6（dict/顺序级断言 + canonical 字节比对，🟡-11 后口径）钉死；清理只许人手动删行 |
| **RF-3** | **并发写交织损坏清单** | 两个 REPL 同时提案，JSON 覆写半途交错，整份清单报废且 fetch 才暴露 | flock 包裹读-改-写全程 + atomic_write（§2.2）；T6/MUT-1 确定性持锁挡写（不赌时序） |
| **RF-4** | **人工手改与工具写入丢更新（已知上限）** | 人直接编辑 candidates.json 不经锁，与工具写入毫秒级撞车时后写覆盖先写 | 登记为已知上限：竞态窗口为工具持锁时长（毫秒级），os.replace 保整份无交织；人审工作流下「边看边改边让 agent 提案」极罕见；不做人侧加锁（过度工程） |
| **RF-5** | **why 判据机械化越位** | 把 20 字/「可能」从软 WARN 升级成硬拒，或新增更多阈值——把审美判断包装成机器判据（direction §5 排除项同源），agent 为绕过机器阈值开始注水 | §2.7 写死「WARN 不阻断」；T8 断言三种情形均正常写入；充分性终审永远在人审闸门 |
| **RF-6** | **借本 spec 顺手提权 tools.json** | 顺手把 read_status/search_notes 加进 asset 表、给 creative 也开 acquire_propose、或补写 asset.md 提示词 | §4.3 逐字 diff 即全部变更，其余三键一字不动写进范围闸门；tools.json 变更按护栏变更评审；asset.md 不在本 spec（§2.5 登记） |
| **RF-7** | **data 悬空时 mkdir 出幽灵目录** | 外置盘脱卸时 mkdir 在挂载点建实体目录，插盘后真数据不可见（`paths.py:131-158` 防的正是这个） | data 根不可达一律 `PermissionError` 零创建；`incoming/` 的 mkdir 仅限 data 已确认可达之后（§2.8）；T7/MUT-10 钉死 |
| **RF-8** | **schema 膨胀（evidence 字段、状态字段、TTL）** | 「顺便」给候选加字段承载更多语义——与 `load_candidates` 冻结 schema 分叉，fetch 侧不消费成死字段 | §2.7 schema 冻结写死；`additionalProperties: False`（工具 schema 层）；新增字段需求必须另立 ADR 并同步改 acquire.py 校验 |
| **RF-9** | **import 链回潮** | 后续有人在 candidates.py 顶层 `from pipeline.acquire import ...`（看似顺手的复用）——ML 栈经 acquire → ingest → subindex 链回流进 LLM 热路径 | §2.1 现状证据写明链条（acquire.py:35-36 → ingest.py:38 → subindex.py:22-24）；T13① 子进程探针捕获即红 |
| **RF-10** | **敏感 URL 入档（🟡-12）** | token 携带的签名直链、内网/保留地址（`http://192.168.x`、`http://169.254.169.254`）可被写进 candidates.json 持久入档；spec 全文不处理就是把威胁模型留给运气 | **缓冲链成立但须写明**：① 审批卡逐条 URL 上卡，人按 y 前过目（§4.5② 专用分支，T15 钉死）；② fetch 永远人逐条批准（ADR-0021 §3）；③ fetch 侧 `dup_verdicts`（`acquire.py:260-273`）终裁重复。**不建机器判据的正面论证**：Spec 4 §2.6 的 SSRF 私网拒连防的是**模型自主出网**（web_fetch 由模型驱动直连）；本工具零网络行为，执行面 fetch 是人对自己机器下的显式 CLI 命令且见完整 URL——威胁模型不同，机器判据放错位置就是「把审美/安全判断包装成机器判据」。联动：URL 不进 `approvals.jsonl` ledger（§4.5① target 只记 title），敏感直链少一个落盘副本 |
| **RF-11** | **candidates.json 损坏后的恢复路径不明（🔵-16）** | 工具 fail-closed（T2 子场景①），但「然后怎么办」只有行为没有文字 | 恢复 = 人按 `load_candidates` 报错**点名的行号**手修文件再跑（`_line_of` `acquire.py:215-226` 的报错行号设计本意——「说第 3 条错了等于让人自己数一遍」）；spec 不建自动修复，半行登记于此 |

---

## 附：行号核实自查表（2026-09-23 对照工作树逐行核实；v0.2 全表重核重写，v0.3 按红队二轮结果以 grep -n 为唯一数据源再过一遍）

> **区间口径（🔵-14 统一）**：`def` 行至最后一条语句，**不含尾部空行**；常量/字典自起始行至闭合行。

| 引用 | 核实结果 |
|---|---|
| `tools.py:60-123 write_episode_file` | ✓ v0.2 逐窗口重核（🔴-3 修正）：def 60；scope 闸 78-79；白名单检查 83-86（clean_name 82）；resolve 语句 91-92（解析块 88-92）；父级比对 114-115；atomic_write 122；return 123 |
| `tools.py:24-27 CREATIVE_WRITABLE_FILES` / `43-49 ASSET_COMMANDS` | ✓ v0.2 重核（🔵-14：ASSET_COMMANDS 闭合行 49） |
| `tools.py:154-275 validate_pipeline_command` | ✓ def 在 154（asset 分支 247 起，Spec 4 自查表已验） |
| `tools.py:308-309 READ_DENY_PARTS / READ_ALLOWED_SUFFIXES` | ✓ |
| `tools.py:318 ToolContext`（`@dataclass` 317）/ `334-428 TOOL_SCHEMAS` | ✓（428 为闭合 `}`，431 起为下一 def） |
| `tools.py:431-437 tool_names_for_scope` / `440-455 build_tool_schemas`（过滤在 453） | ✓ v0.2 重核 |
| `tools.py:643-650 _TOOL_IMPLS` / `653-670 execute_tool`（四异常 except 在 **668**） | ✓ v0.2 重核（🔴-4 修正；v0.1 误作 653-668/667） |
| `tools.py:698-821 run_pipeline` | ✓（文件全长 821 行） |
| `acquire.py:35-36` 顶层 ingest import | ✓ |
| `acquire.py:58 TYPES` | ✓ `("live", "mv", "scan", "interview")` |
| `acquire.py:66-76 incoming()` / `78-79 candidates_path()` / `82-83 ledger_path()` | ✓ v0.3 重核（🔵-14 同类残留自纠：v0.2 误作 78-80/82-84，尾部空行） |
| `acquire.py:215-226 _line_of` / `229-257 load_candidates` | ✓ |
| `acquire.py:260-273 dup_verdicts`（YAGNI 注释 264-265；URL 查重腿 268-270，267 是 `return [`；同名查重腿 271-273） | ✓ v0.3 重核（🔵-r8 修正） |
| `acquire.py:290-293 slugify`（290 def / 291 docstring / 292 `re.sub` / 293 return） | ✓ v0.3 重核（🔴-r3 修正；v0.2 误作 290-292） |
| `acquire.py:368-378 ledger_load`（损坏台账 raise SystemExit 在 **378**，🟡-8 新增引用）/ `381-383 ledger_save` / `386-387 ledger_seen_urls` | ✓ v0.2 重核（🔵-14 口径修正） |
| `acquire.py:443-480 cmd_fetch`：**`c = cands[no - 1]` 在 450**（存在性检查 444-446、`load_candidates` 447、范围检查 448-449；`return 0` 在 480）/ `393 cmd_gate` / `483 cmd_register` | ✓ v0.3 重核（🔴-2 修正 + 🔵-14 同类残留自纠：v0.2 区间误作 443-481） |
| `ingest.py:38` 顶层 subindex import | ✓ |
| `subindex.py:22-24` 顶层 numpy / pysubs2 / sentence_transformers | ✓ |
| `paths.py:118-128 atomic_write`（tmp 126、write 127、**os.replace 128**）/ `131-158 require_data`（raise SystemExit 在 **144/149/154**） | ✓ v0.2 重核（🔵-15 精确化） |
| `scopes.py:31-49 load_scope` | ✓ |
| `resolver.py:17-23 scope_of` | ✓ def 17，return 23 |
| `cli.py:591-592 side_effect fail-closed` / `624 target_str`（现状仅 filename/command 两键，🟡-9 新增引用）/ `635 if ep_dir:`（🔵-r7 新增引用；634 是 `norm_decision`）/ `636 记账接线` / `725 idea scope` / `1079 resolve_episode_target`（🔵-r7 新增引用）/ `981-983 /asset 切换` | ✓（624/635/981-983/1079 逐行核实；591-592/636/725 经 Spec 4 自查表复核） |
| `status_card.py:140-279 render_approval_card`（清洗先例 162-163：162 `splitlines` 取首行、163 `re.sub` 剥离 `\x00-\x1f\x7f-\x9f`，🟡-r5 依据；通用分支 cmd_str 201；兜底 nature「本地只读产物生成…」269；`return` 279）/ `282 log_approval_decision` | ✓ v0.3 重核（🔴-r4 修正；v0.2 误作 140-276） |
| `config/agent/tools.json` 四键现状（creative 5 / pipeline 4 / asset [] / idea 4） | ✓ 逐字核实 |
| `config/agent/scopes/` 无 asset.md（creative/director/idea/pipeline 四个） | ✓ `ls` 实测 |
| `tests/test_acquire.py:19` `from pipeline import acquire as A`（251 行全文） | ✓ v0.2 重核（🔴-1 修正；v0.1 误作 :23） |
| `tests/test_agent_tools.py:311-316 SPEC_TOOLS` | ✓ |
| `data` 为悬空符号链接（→ `/Volumes/Samsung T7/anime-video-data`），incoming/ 不可达 | ✓ `ls -la` 实测（§2.7 历史数据声明依据） |
| `skills/acquire-assets/SKILL.md` §一 schema 表与 why 判据；❌ 示例字数实测 17/4（`len()` 口径，🟡-13） | ✓ 全文精读 |
| `network-tools-spec.md:136`「asset scope 会话里模型连写工具都看不到」落点在 Spec 4 **§2.5**（注入安全） | ✓ v0.2 重核（🔴-6 修正；v0.1 误作 §2.6） |
