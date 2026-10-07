# Spec 16：出网断言对「可信仓库文档」做命中位置级豁免（D30）

> **状态：已施工并通过独立评审（D30-B 施工 `d5d94bf`；D30-C 评审 ✅ 2026-10-06，见文末 §13）**。v0.2 红队定向复审（D30-R2，2026-10-06）🟢 可动工（附 2 条施工约束 🔵，见「红队定向复审裁决」，均已照做）。v0.2 = 2026-10-06 作者修订，回应 D30-R 的 3🟡 + 6🔵（见「作者修订回应」）。沿革：v0.1 草案 2026-10-06 D30-A 立文；同日人裁决 §10（Q1 = ②a，Q2–Q4 同意建议）；同日红队一轮 D30-R 🟡 修订后复审。下一步：施工（D30-B，按定向复审的 🔵-R2-1/R2-2 补齐 TD-2f 构造与 MUT-D9c）→ 独立评审（D30-C）。本文件不改代码。
> 对应 issues：**D30**（主）；顺带登记的新问题见 §10 Q3/Q4；v0.2 另登记 **N55**（装配器读域缺口，§3）。
> 相关：Spec 4（`2026-09-23-network-tools-spec.md`）§2.4 出网断言生效点、§7.1 T5a/T5b/T17、MUT-2/MUT-13；impl spec（`../2026-09-18-ava-agent-impl-spec.md`）§2.5 **Y2-r19** 出网边界；ADR-0021（网络工具内化）、ADR-0022（工序层上下文装配）；新提 **ADR-0026**（`docs/dev/adr/0026-egress-assert-trusted-repo-docs.md`，提议中）。

## 红队一轮裁决（2026-10-06，D30-R，独立 session 未参与起草；只审文稿，未改代码）

**裁决：🟡 修订后复审**（3🟡 + 6🔵）。方案 ②a 的核心判定是对的：豁免只放行「逐字等于可信文本」的那几段字节，而断言本来只比对文件名、不比对内容，所以**豁免不扩大任何内容泄露面**；区间包含（非整条消息、非先删后匹配）、`casefold` 坐标一致、web 四个出方向不动，这几条经探针验证成立。阻塞项在**可信集取哪些文本**：按现稿施工，D30 会在同一会话推进工序后原样复发（🟡-1），且有三处容易把不可信内容顺手带进可信集（🟡-2）。

| 编号 | 指控 | 证据 | 建议 |
|---|---|---|---|
| 🟡-1 | **可信集只取「当前工序」，同会话推进或恢复后 D30 复发**。§5.2 写「工序层：`resolve_step_docs(scope, step_key)` 解析出的全部文档」，只认当前 step；但历史里早先工序的注入消息一直留着。`assembly.json` 的 `_base`：`03` → 只有 `03-tts.md`，`03.5` → `03.5-voice-check.md` + `04-clips.md`。正常流程是同一会话里跑完配音、期目录从 03 进入 03.5：此时 03-tts.md（含字面 `03-audio/manifest.json`）在历史里、不在当前集 → 再次 `[BLOCKED]`；进入 04 及以后，两份 runbook 都不在当前集，本会话此后每一轮都被拦。`--continue` 恢复到后续工序同理。且恢复时无法从记录里重建「注入过哪些文档」：`session.py::_commit` 只有 `_reinject_changed` 传 `docs=`，首次注入（`_assemble` 两处）不记 `docs` | 探针（scratchpad `d30r/probe.py`，§5.1 算法忠实复刻）P3：历史含 03 注入、可信集 = 03.5 规程 → `BLOCK 03-audio/manifest.json`；P4：可信集 = 本会话全部已注入 → PASS。`config/agent/assembly.json` routes；`session.py` `_assemble` / `_commit` / `prepare_resume` 的 `docs` 汇总 | 可信集改为两部分的并集：(a) **本进程装配器提交过的全部文档正文**，在读入处捕获、只增不减（不随 `_rollback` 收缩——可信集是超集无害，豁免只认逐字字节）；(b) **`assembly.json` 的 resident 三件 + 全部 routes（所有 scope、所有 step）所指文档的当前磁盘正文**（过滤见 🟡-3），它同时覆盖 `--continue` 历史里未改版的旧注入。§5.3 的「恢复后规程已改」残余照旧成立。补 **TD-1b**（同一会话三轮：step 03 → 03.5 → 04，每轮首个请求照常发出）、**TD-1c**（`--continue` 恢复到 step 04、历史含 03 与 03.5 注入）；补变异 **MUT-D9**「可信集只认当前工序」→ 由 TD-1b 杀 |
| 🟡-2 | **可信正文的采集口径没写死，三处会把不可信内容顺手带进可信集**。(a) `tracker.resident_prompt` 不是三份文件的原文，而是 director + **scope + `extra_prompt`** + AGENTS 的拼接体（`assembly.py::assemble_resident_prompt`：`scope_text = f"{scope_text}\n\n{extra_prompt}"`）；§5.2 恰好提示「`SessionContextTracker` 已有 `resident_prompt`」，整体入集就把 `extra_prompt` 变成可信。(b) `_reinject_changed` 的候选**含记忆**：`candidates.append(memory_doc[0])`，以 origin=`injection`、页眉「规程已修订」提交；照 §5.2「含 `_reinject_changed` 追加的修订版」施工，记忆就进了可信集，与「记忆永不可信」直接冲突。(c) 若可信正文另从磁盘重读：常驻层用 `read_text`（做换行翻译），工序层 `load_injected_doc` 用 `read_bytes().decode`（不翻译），两种读法对 CRLF 文档的结果不一样，可信集与消息里的字节会对不上（方向是拦，但 D30 会重新炸） | 探针 P13：记忆以 reinject 形态含 `cloud.local.json`、实现若收全部 reinject 候选 → PASS（该拦未拦）；P11：可信集按 `\r\n` 另读、消息为 `\n` → BLOCK。代码：`assembly.py` L276-277、`session.py::_reinject_changed` 的 `candidates` | §5.2 写死：**逐文件**、在装配器读入处捕获**它实际拼进消息的那份字符串**（工序层取 `InjectedDoc.content`；常驻层取三份文件各自读入后的原文，**不取 `resident_prompt`**）；记忆按 `MEMORY_REL_PATH` 与「来自 `resolve_memory_injection`」两道排除，`_reinject_changed` 收集可信正文时显式跳过记忆。补 **TD-2e**（`extra_prompt` 含受限串 → 拦）、**TD-2f**（`--continue` 后记忆被改、经 `_reinject_changed` 重注入且含受限串 → 拦）；补变异 **MUT-D10**「`resident_prompt` 整体入集」→ TD-2e 杀、**MUT-D11**「reinject 候选不滤记忆」→ TD-2f 杀 |
| 🟡-3 | **§5.2 路径过滤的保障是虚的，而且过滤口径不够**。断言只比对四个文件名，不比对内容：`assembly.json` 误配指向 `config/cloud.local.json`（或 `docs/` 下一个软链到它的 `.md`）时，注入的是密钥**内容**，修前修后断言都不会响——「此时不进可信集，断言照常拦下」不成立，TD-5「内容含受限串 → 拦」测的也不是凭据安全。另：(a) 现稿只说「路径本身命中模式」，没要求对 resolve 后的真实路径再判（软链）；(b) `data/` 未软链外置盘时就在仓库根内，`data/episodes/*/01-topic.md`、`02-script.md` 是模型经 `write_episode_file` 可写的 `.md`，「`.md` + 根内 + 不命中模式」三条都满足，一旦被路由就成了模型可写的可信文本 | `assembly.py::load_injected_doc` 接受绝对路径与任意相对路径，不设读域；`assert_egress_boundary` 只做文件名子串匹配；`memory.MEMORY_REL_PATH = "data/library/memory.md"`（同样是根内 `.md`，现稿靠来源排除而非路径排除） | ① 如实改写 §3 / §5.2：豁免不扩大内容泄露面（断言本来就不看内容）；「装配器会照路由读任意文件、包括凭据」是**既有缺口**，登记新 issue，在 `load_injected_doc` 层拒载另修，不挂在本断言上。② 过滤改为**白名单根**（`docs/`、`skills/`、`config/agent/scopes/`、仓库根的 `AGENTS.md`），对 **resolve 后**的真实路径判定，并显式排除 `data/`。③ TD-5 改为直接断言「这些文档不进可信集」（检查可信集本身），不再借「内容含受限串」间接测 |
| 🔵-1 | 豁免与命中位置无关：可信区间在**整个请求体**里搜，不限于装配器放置的那几条消息。可信文本若很短且含模式（例如某份规程全文只有「见 03-audio/manifest.json。」），模型在工具参数里写出同一句就会被豁免，「读域拒了 + 发送闸再掐」的双保险对它失效 | 探针 P7、P8 均 PASS。现存含模式的两份 runbook 都是 KB 级，没有现实风险 | 二选一写进 §5.1：含模式的可信文本须 ≥ 某个长度（如 200 字符）才入集；或把可信区间限定在 `messages[0]` 与 origin=`injection` 的消息内。并在 §5.3 登记这一条 |
| 🔵-2 | 空可信文本（文档 strip 后为空）没排除：`find("")` 会产生 O(n) 个零长区间，不会错判，但白白耗时 | 探针 P14 | 入集时过滤空串 |
| 🔵-3 | MUT-D8（包含判定差一，`<=` 写成 `<`）在 TD-1 上杀不死：runbook 里的命中在正文中段，离区间边界很远 | 读 §8 | 新增 **TD-7b**：可信文本恰好以模式**开头**、恰好以模式**结尾**各一例，作为 MUT-D8 的指定杀手，不要留到施工时再指认 |
| 🔵-4 | `assert_egress_boundary` 的 `content` 是 `str` 时不做 `json.dumps`，可信文本却一律按 JSON 转义形态去找，两种口径不一致 | `tools.py` L314 | 写明可信文本只在 `content` 为 dict/list 时按转义形态找、为 str 时按原文找；或规定只有 `chat_complete` 这一处（payload 恒为 dict）可以带可信集 |
| 🔵-5 | 文档修订面有缺漏：README「已知限制」的「03.5 期的会话」一行（应改为 03 / 03.5，D30-C 通过后删除）没列进 §7；impl spec 里有两处 Y2-r19（L433 与 L1215），§7 只写「§2.5」 | `README.md` 已知限制表；`grep -n Y2-r19` | §7 补 README 一行；两处 Y2-r19 都加修订注记（或指名改哪一处） |
| 🔵-6 | 工具 schema（payload 的 `tools[].description`）同样是仓库静态文本，但不在可信集里：哪天描述里写了受限文件名，所有请求都会被拦 | `chat_complete` 的 payload 组装 | 今天的描述不含模式。在 §5.3 登记一句即可，不必为它扩展可信集 |

**攻击面逐项结论（含通过项，判据 11 通过率抽样）**：
- **能出网的最坏样例**，逐条判定（探针结果；「不变」指修前修后行为相同）：
  - URL 编码：LLM 请求体断言本来就不做 unquote，修前修后不变（web 出方向的 unquote 归一不动）。
  - Unicode 同形 / 全角：不变，仍是 RF-11 已登记的上限。
  - `casefold` 变长字符：P9（ß 在前，该放的放）与 P10（ß 在前 + 规程外的受限串，该拦的拦）均符合预期；另对全部 0x110000 个码位验证 `casefold` 与上下文无关（`fold(x+c+y) == x+fold(c)+y`，例外数 0），所以可信文本与请求体折叠到同一坐标系。
  - 拆分拼接：跨消息拆开的名字修前就不拦，不变。
  - 横跨可信文本边界：P6 拦。
  - 藏进工具参数、工具返回值、状态卡的受限串：照旧拦（P5 同口径）。藏进记忆和 `extra_prompt` 的，取决于 🟡-2 的采集口径。
  - 路由指向凭据文件：豁免不扩大泄露面，但这是既有缺口（🟡-3）。
  - 模型逐字复述规程再夹带：夹带的字节落在可信区间外，拦（P5）；夹带写进复述内部，复述就不再逐字一致，同样拦。
- **可信文本集的边界**：记忆必须排除（现稿已写，但 reinject 路径有漏口，见 🟡-2）；`--continue` 恢复后的旧版规程不豁免，方向是拦，可以接受（§5.3）；未改版的旧注入在现稿下也不豁免（🟡-1）。
- **治本与否**：豁免机制本身是治本的，换一份含同类字面量的规程不会再炸。但前提是 🟡-1 修掉，否则「同会话换工序」正是同类文档再次炸开的路径。
- **用例与变异能否杀死回退**：「断言整个关掉」→ MUT-D7（T5a / T17 / TD-2a）✔；「豁免退化为整条消息级」→ MUT-D3（TD-3）✔，P5 实测拦；「先删后匹配」→ MUT-D4（TD-4）✔，P6 实测拦；「只认当前工序」「`resident_prompt` 整体入集」「reinject 不滤记忆」**三条回退现稿杀不死**，需补 MUT-D9～D11；MUT-D8 需要指定杀手（🔵-3）。
- **文档修订面**：Spec 4 §2.4、ADR-0026、ADR-0021/0022 的 frontmatter、plans/README、issues D30 都已列出；缺 README 已知限制与第二处 Y2-r19（🔵-5）。
- **核对过的现状事实**：`chat_complete` 全仓只有 `llm.py::run_tool_loop._chat` 一个调用方，收尾调用也经它，所以 §5.2「收尾若另走 `chat_complete`」的条件不成立，传一处即覆盖。web 四个调用点只传 query、url 或 reason，确实不需要可信集。现有用例 `test_assert_egress_boundary`、`test_egress_payload_blocks_case_variant_audio_read`、`test_m5_status_card_passes_assert_egress_boundary`、T5a / T5b / T17 都在。性能方面，24.5 万字符的请求体加 3 段可信文本，判定耗时约 1 ms。

### 作者修订回应（v0.2，2026-10-06；修订人未参与 D30-R）

先核证据再改。红队探针 `d30r/probe.py` 仍在，复跑 P1–P13（跳过全码位 casefold 扫描），结果与裁决表一致：P3 拦、P4 放、P11 拦、P13 放。另写了 scratchpad `d30v2/probe_v2.py`，调用真实 `assembly.py` 的读法与渲染，只复刻 §5.1 判定，不改仓库，用来验证修订方案：
- V1：同会话 03 → 03.5 → 05，可信 = (a)∪(b) → 放；V2：只认当前工序 → 拦（MUT-D9 的信号）；V3：`--continue` 恢复后只有 (b) → 放。
- V4：短可信文本被模型复述进工具参数，加门槛后 → 拦。
- V5：scope 文件的 strip 形态是带 `extra_prompt` 的常驻层的子串。V6 / V7：`extra_prompt` 含受限串，可信 = 三件原文 → 拦；可信 = `resident_prompt` 整体 → 放（MUT-D10 的信号）。
- V8 / V9：CRLF 路由文档，同一读法 → 放；改用 `read_text` → 拦。V10：str 口径。V11：门槛边界上模式恰在开头、恰在结尾 → 放。

另核：现有 17 份常驻 / 被路由文档 resolve 后全部落在白名单根内，没有软链，最短的是 390 字（`config/agent/scopes/pipeline.md`）。白名单和 200 字门槛都不会挡掉现有文档，**没有需要人裁决的新取舍**。

| 编号 | 处置 | 改了哪里 |
|---|---|---|
| 🟡-1 | 采纳 | §5.2.1 (a)：本进程装配器提交过的全部正文，存 `tracker.trusted_doc_texts`，不进 `_rollback` 的恢复清单。§5.2.2 (b)：resident 三件 + 全部 routes 的当前磁盘正文，每回合装配时重读。§8 补 TD-1b（同会话 03 → 03.5 → 05；仓库没有单独的 04 工序键，03.5 已含 04 排片）、TD-1c（`--continue` 恢复到 05，历史含 03 与 03.5 注入）、TD-1e（同进程中途改版），以及 MUT-D9 / D9b |
| 🟡-2 | 采纳 | §5.2.1 逐文件在读入处捕获：工序层取 `doc.content.strip()`；常驻层改由 `assemble_resident_prompt` 返回三份文件各自的 strip 原文，scope 取拼 `extra_prompt` **之前**的那份，不取 `resident_prompt`。§5.2.3 记忆两道排除，`_reinject_changed` 只收 `resolve_step_docs` 的候选。§5.2.2 写明 CRLF 口径：同一层用同一读法（路由文档走 `load_injected_doc`，不翻译换行；常驻三件走 `read_text`，翻译换行），两种读法各抽成一个函数，装配和可信集共用。§8 补 TD-2e、TD-2f、TD-1d（CRLF）与 MUT-D10、D11、D12。另删去 v0.1「收尾若另走 `chat_complete`」的条件句（红队已核实不成立） |
| 🟡-3 | 采纳（四项） | ① §3 新增「这道断言管不到的东西」，§4 ②a 行与 §5.2.3 如实改写：豁免不扩大内容泄露面，凭据内容被路由进消息是装配器读域的既有缺口。② §5.2.3 改为白名单根 + resolve 后判定，显式排除 `data/` 与 `MEMORY_REL_PATH`，规则写成一个函数供 N55 复用。③ §8 TD-5 改为直接断言可信集本身。④ issues 新登记 **N55**（`load_injected_doc` 不设读域），§7 文档面加一行 |
| 🔵-1 | 采纳，选「长度门槛」 | §5.1 第 3 步：`trusted_texts` 里短于 `TRUSTED_TEXT_MIN_CHARS = 200` 字符的元素一律忽略。理由有三条。① 位置限定要知道哪几条是注入消息，但 `origin` 只记在 `session.jsonl` 里，内存中和发出去的消息都不带它。要做位置限定，得把消息下标一路传进断言，再在序列化后的请求体里换算区间，改动面和出错面都大。② **对 🟡-1 的历史注入仍然成立**：恢复出来的历史里，旧注入是整份规程正文（现有最短 390 字），门槛不影响它们；判定与位置无关，这些正文在请求体里的任何位置都能认出来。反过来，如果改成位置限定，旧注入的 origin 只能从期目录里的 `session.jsonl` 取，而那正是 §5.3 拒绝信任的东西。③ 残余：模型如果把 ≥ 200 字的规程原文逐字抄进工具参数，这段会被豁免。但被豁免的字节就是仓库文档本身，没有新内容出去。「读域拒了 + 发送闸再掐」防的是模型在参数里写路径，只写一个路径远不到 200 字。已在 §5.3 登记 |
| 🔵-2 | 采纳（并入 🔵-1） | 门槛顺带滤掉空串 |
| 🔵-3 | 采纳 | §8 新增 TD-7b（≥ 200 字的可信文本，模式恰在开头、恰在结尾各一例），定为 MUT-D8 的指定杀手 |
| 🔵-4 | 采纳 | §5.1 第 3 步：可信文本的形态与 content 的序列化口径一致（dict / list → JSON 转义形态；str → 原文），并写明只有 `chat_complete` 传可信集；§8 补 TD-6b |
| 🔵-5 | 采纳 | §7 补 README「已知限制」的「03.5 期的会话」一行（施工时改为 03 / 03.5，D30-C 通过后删除）；impl spec 两处 Y2-r19（L433 设计段、L1215 验收段）都加修订注记 |
| 🔵-6 | 采纳 | §5.3 登记：工具 schema 不在可信集里，今天的 description 不含受限模式 |

## 红队定向复审裁决（2026-10-06，D30-R2；由 D30-R 一轮红队复审，未参与 v0.2 修订；只审文稿，未改代码）

**裁决：🟢 可动工**。一轮 3🟡 + 6🔵 逐条核对，全部忠实落地；另有 2 条🔵，属于施工时必须照做的约束，不阻塞动工。

逐条核对（含通过项）：
- **🟡-1 ✔**：可信集 = (a) 本进程提交过的正文（只增不减、不进 `_rollback`）∪ (b) 路由表全部 scope、全部工序键的当前磁盘正文。我核对了 `assembly.json` 的 `_base` 键：`01 / 02 / 02.5 / 03 / 03.5 / 05 / 06 / 07 / 08 / 09 / default`，确实没有 `04`，TD-1b 用 03 → 03.5 → 05 是对的。(b) 每回合重读，覆盖 `--continue` 历史里未改版的旧注入；(a) 覆盖同进程中途改版。scope 热切换不留旧常驻层：`_assemble` 每回合整段替换 `messages[0]`，(b) 只按当前 scope 展开 resident，也没有问题。
- **🟡-2 ✔**：三个捕获点写死。工序层取 `doc.content.strip()`；reinject 只收 `resolve_step_docs` 的候选；常驻层取三份文件各自的原文（scope 取拼 `extra_prompt` 之前的那份），明确不取 `resident_prompt`。记忆按「来源 + 路径」双重排除；CRLF 按「同层同读法、抽成函数共用」处理。MUT-D10 / D11 / D12 都已补上。
- **🟡-3 ✔**：§3 如实改写为「断言不看内容，豁免不扩大泄露面」。读域过滤改为白名单根，并按 `resolve(strict=True)` 之后的真实路径判定；`data/` 和 `MEMORY_REL_PATH` 显式排除；规则只写一个函数，N55 复用。TD-5 改为直接断言可信集本身，七种来源各一例并带对照组。N55 已登记。
- **🔵-1 ✔（选长度门槛）**：理由成立。`origin` 只传给 `_commit`、写进 `session.jsonl`，内存里的消息和 `_wire_messages` 发出去的消息都不带它。按位置限定要把下标一路传进断言，改动面和出错面都比门槛大。残余（≥ 200 字规程原文被抄进参数）已在 §5.3 登记，被放过的字节就是仓库文档本身，可以接受。TD-8 含 199 / 200 字的边界例。
- **🔵-2～6 ✔**：空串随门槛一起滤掉；TD-7b 定为 MUT-D8 的指定杀手；str 口径与 TD-6b 已写；README 已知限制与两处 Y2-r19 已进 §7；工具 schema 已登记进 §5.3。
- **门禁 §11**：已同步到 TD-1b～1e、TD-2～8、MUT-D1～D13。

| 编号 | 指控 | 证据 | 建议（施工约束） |
|---|---|---|---|
| 🔵-R2-1 | **TD-2f 按现稿的「自然路径」构造不出来，MUT-D11 会成为空变异**。`_reinject_changed` 只在恢复记录里**带 `docs` 的消息**上触发，而生产代码里唯一写 `docs=` 的就是 `_reinject_changed` 自己（`session.py::_commit` 的调用点中只有它传 `docs`；`_assemble` 的首轮与换工序两处注入、`_inject_memory` 都不传）。`prepare_resume` 只从带 `docs` 的 msg 记录汇总 `docs`，所以真实会话恢复后 `_injected_docs` 恒为空，`_reinject_changed` 直接 return。照「`--continue` 后改记忆 → 经 `_reinject_changed` 重注入」去写 TD-2f，重注入根本不会发生：记忆改走 `_inject_memory`（origin=`memory`），请求照样被拦，用例绿，但 MUT-D11（reinject 收集不滤记忆）不会被执行到，也就杀不死 | `grep -n "docs=" pipeline/agent/session.py` → 只有 `_reinject_changed` 一处；现有 `test_ts6_ts7_resume_rebuilds_resident_and_reinjects_changed_docs` 之所以能测到重注入，是因为**手写**了一条带 `docs` 的注入记录 | TD-2f 照 TS-6 的写法构造：手写一条 `origin=injection`、带 `docs: [{"path": "data/library/memory.md", "sha256": <与当前不同>}]` 的记录，记忆正文 ≥ 200 字（否则门槛先把它滤掉，MUT-D11 同样不会生效）；用例里先断言「规程已修订：data/library/memory.md」那条消息确实出现，再断言被拦、可信集不含记忆正文。顺带把「生产路径从不记 `docs`、恢复后重注入永不触发」登记为 **N56**（Spec 9 §2.7 第 3 条的既有缺口，不在本 spec 修） |
| 🔵-R2-2 | 「(b) 只取当前工序的路由、(a) 照旧」这种回退，没有指定的杀手：MUT-D9 是 (a)(b) 同时坏，TD-1b 在 (b) 完好时也会绿；TD-1c 正好能抓这种回退，但现表没给它挂变异 | §8 变异表 | 补 **MUT-D9c**「(b) 只取当前 step 的路由」→ 应被 TD-1c 杀（恢复到 05 时，03 / 03.5 的旧注入只能靠 (b) 认出） |

## 0. 一句话

LLM 请求出网前的子串断言，对「逐字等于仓库里装配器注入的规程文档」的那几段文字不再报警；其余一切（人打的字、模型的工具调用参数、工具返回值、记忆、状态卡）照旧整串断言，判定规则（casefold、四条模式）一字不改。

## 1. 用户场景与复现

**场景**：一期做到 03（语音合成）或 03.5（配音顺听 / 04 排片），人在桌面端或终端对这一期开会话，第一句话还没说完，本轮就 `[BLOCKED]`。03.5 是人时最重的停机点，agent 在这里完全用不上。

**复现（2026-10-06，D30-A 亲自跑，临时仓库副本 scratchpad `d30-repo`，HEAD `86df0fb`）**：

- 造两期：`D30-S035`（有 `02-diff.patch` + `03-audio/manifest.json`，`inspect_episode` → `03.5 配音顺听 / 04 排片`）、`D30-S03`（有 `02-diff.patch`、无 manifest → `03 语音合成`）。LLM 配置指向死端口 `127.0.0.1:9` + 假密钥变量（断言在发请求之前，零出网）。
- `python -m pipeline.agent.protocol data/episodes/<期>`，等 `ready` 后发一条 `user_message{"text":"你好"}`：**两期都是** `turn_finished{"stopped":"blocked","llm_calls":0,...}`，`prompt_chars` 分别 14 031 / 9 192。
- 用 `session.jsonl` 里落盘的消息逐条调 `assert_egress_boundary`：整份 payload → `拦截出网请求：内容包含受限敏感标记 '03-audio/manifest.json'`；逐条看，**只有 origin=`injection`、role=`user` 的那条工序层注入消息被拦**，常驻层 system 消息与人打的「你好」都过。
- 命中出处：`docs/runbook/03.5-voice-check.md:7` 与 **`docs/runbook/03-tts.md:16`**（D30 行原先只登记了 03.5；03 同样中招）。两份都由 `config/agent/assembly.json` 的 `routes._base` 路由进对应工序。
- 全仓注入面扫描（resident 三件 + 全部 runbook + `skills/write-script/SKILL.md`）：四条模式里只有 `03-audio/manifest.json` 出现，且只在这两份 runbook。

**附带发现**：协议路径（桌面端）下 `[BLOCKED]` 的原因字符串**不下发**——`turn_finished` 只有 `stopped:"blocked"`，没有任何 `log`/`notice` 帧带命中串（终端 `ava` 有 `cli.py:1492` 的 `[BLOCKED] 出网被拦截：…`）。人在桌面端只看到「被拦了」，不知道为什么。见 §10 Q4。

## 2. 机理与断言现状

`pipeline/agent/tools.py`：

```python
RESTRICTED_EGRESS_PATTERNS = ("cloud.local.json", "agent.local.json", "03-audio/manifest.json", "03-audio/voice.json")

def assert_egress_boundary(endpoint, content):
    text = json.dumps(content, ensure_ascii=False) if not isinstance(content, str) else content
    folded = text.casefold()
    for pattern in RESTRICTED_EGRESS_PATTERNS:
        if pattern.casefold() in folded:
            raise PermissionError(...)
```

调用点（以符号为锚）：

| 调用点 | 断言对象 | 本 spec 是否改动 |
|---|---|---|
| `llm.py::chat_complete`（出网前最后一道） | **整个请求体**（model + messages + tools） | **改**：可带可信文本集 |
| `web.py::search_web` | `{"query": _normalized_for_assert(query)}` | 不改 |
| `web.py::fetch_web` | `{"url": _normalized_for_assert(url)}` | 不改 |
| `web_crawl.py::crawl_page` | url + reason（归一后） | 不改 |
| `web_browser.py::browser_action`（navigate） | url + reason（归一后） | 不改 |

同一张模式表的其他消费者（**全部不改**）：`web.py::_scrub`（抓回内容清洗）、`status_card.py`（状态卡清洗）、`memory.py::_field_violation` R3（记忆写入拒收）。

会话装配（`session.py` 的回合准备段）：`messages[0]` = 常驻层（`assemble_resident_prompt`：director.md + scopes/{scope}.md + AGENTS.md，以 `\n\n---\n\n` 拼接）+ 状态卡；工序层文档经 `render_step_injection` 以 role=user、origin=`injection` 追加，正文是各文档 `content.strip()` 逐字拼接；记忆以 origin=`memory` 单独一条。

## 3. 这道断言实际在防什么（威胁模型）

Y2-r19 立的边界是「`config/`（含 `cloud.local.json`）、密钥、`03-audio/` 音频与 manifest 的**内容**不出网」。内容层面已有**结构性**防线：

1. `read_artifact` 读域写死期目录 + `data/library/`，`deny_dir_hit` 硬排除 `03-audio/`、`04-patch/`，且对 resolve 后的路径再判一次（软链绕不进去）；`config/` 不在任何读根里。
2. 模型没有任何写 `docs/`、`skills/`、`config/`、`AGENTS.md` 的工具（`write_episode_file` 只写期内白名单文件，`write_memory` 只写记忆）。
3. 抓回内容经 `_scrub`，状态卡经清洗，记忆写入有 R3。

LLM 请求体上的子串断言是**第二层绊线**：模型一旦在工具调用参数里提到这些路径（比如尝试读 `03-AUDIO/MANIFEST.JSON`），即使读域已拒，下一次请求也会被整轮掐掉——`tests/test_agent_tools.py::test_egress_payload_blocks_case_variant_audio_read` 明确守着这层「读域拒了 + 发送闸再掐」的双保险。

**D30 的本质**：绊线按「名字出现」判，分不清「仓库规程里人写的一句路径说明」和「模型/外部内容里出现的路径」。前者是 git 跟踪、人手写、模型改不了的静态文本，它出网等于把仓库文档发出去，与 Y2-r19 要防的内容无关。

**这道断言管不到的东西（v0.2，🟡-3 ①）**：断言只比对四个**文件名**，不看内容。由此有两点。第一，本 spec 的豁免**不扩大任何内容泄露面**，被放过的只是规程逐字副本里的文件名字面量。第二，反过来说，如果 `assembly.json` 被误配，路由到 `config/cloud.local.json`（或 `docs/` 下一个软链到它的 `.md`），装配器会把密钥**内容**拼进消息。这些内容里一般不含那四个文件名，所以修前修后断言都不会响。这是装配器读域的既有缺口（`load_injected_doc` 接受绝对路径和任意相对路径，不设读域），登记为 **N55**，应在装配器层拒载，另行修复，不由本断言承担。§5.2.3 的读域过滤只管一件事：不让非规程来源的文本获得豁免。

## 4. 候选方案（每个都写清放过了什么）

| 编号 | 方案 | 放过了什么 | 放过的面里有没有真凭据风险 | 治本？ | 冻结面 |
|---|---|---|---|---|---|
| ① | 改写两份 runbook，避开字面路径（如写成「期目录下 03-audio 里的 manifest」） | 无（断言不变） | 无 | **否**：任何规程再写一次字面路径就复发；还逼文档作者绕着护栏措辞 | 不动 |
| **②a（推荐）** | **命中位置级豁免**：断言时额外给一组「可信文本」（本回合装配器从仓库读入的常驻层与工序层文档正文）；一个模式命中**只有整体落在某段可信文本的逐字副本区间内**才放过，其余命中照旧拦 | 仓库规程文档里的字面路径（只限逐字副本那几段字节） | **无新增**：被放过的只是白名单根内 `.md` 规程逐字副本里的文件名字面量，模型没有写入口；记忆（模型可写）有两道排除（§5.2.3）。断言本来就不看内容，凭据**内容**一旦被误路由进消息，修前修后都拦不住，那是装配器读域的既有缺口（N55），不由本断言承担（§3） | **是**：任何规程写任何字面量都不再炸；其他来源的命中一条不放 | 改 Spec 4 §2.4 生效点措辞（加修订记录），T5a/T5b/T17 不变 |
| ②b | 整条消息按来源豁免：会话提交时给 origin=`injection` 的消息打内部标记，断言跳过整条 | 整条 `messages[0]`（含状态卡、`extra_prompt`）与整条工序层注入消息 | 低但非零：状态卡已清洗、`extra_prompt` 来自宿主；但豁免粒度是「整条消息」，以后谁往注入消息里拼了动态内容就一起被放过 | 是 | 同上，且要改 `_wire_messages` 的标记剥离 |
| ②c | 拆模式表：`03-audio/manifest.json`、`03-audio/voice.json` 退出 LLM 请求体断言，只留在 web 出方向与清洗 | 全部来源里出现的这两条期内产物路径（含模型工具调用参数） | 无凭据风险，但**拆掉了现有双保险**：`test_egress_payload_blocks_case_variant_audio_read` 守的「读域拒了 + 发送闸再掐」对 03-audio 失效 | 半治本：哪天规程写了 `cloud.local.json`（比如云端配置说明）照样炸 | 改模式表语义，动 T14/大小写用例 |
| ②d | 改值匹配：请求体断言改查密钥真值（环境变量里的 key、两份 local.json 的敏感字段），不再查文件名 | 一切「提到文件名」 | 凭据真值仍拦；但 manifest/voice 没有「真值」可匹配，等于这两条从请求体断言里消失（同 ②c 的代价） | 对凭据最精确，对期内产物是拆护栏 | 重写 Y2-r19 语义，影响面最大 |
| ③ | 维持现状，登记人工绕行 | — | — | 否：03/03.5 两个工序 agent 不可用 | 不动 |

**推荐 ②a 的理由**：它是唯一一个「豁免面 = 仓库静态文档本身」的方案——放出去的字节逐字等于 git 里人写的规程，不新增任何能被模型或外部内容利用的口子；现有的每一层（读域、双保险、web 出方向、清洗、R3）原样保留；判定规则（casefold、四条模式、Unicode 同形上限）一字不改。②b 粒度太粗，②c/②d 都要拆现有双保险。

## 5. 推荐方案 ②a 详细设计

### 5.1 判定（`tools.py`）

`assert_egress_boundary(endpoint, content, *, trusted_texts: Sequence[str] = ())`：

1. `text`、`folded` 的算法与现状完全相同（`json.dumps(..., ensure_ascii=False)` → `casefold()`）。
2. `trusted_texts` 为空 → **行为与现状逐字节一致**（web 四个调用点不传，零变化）。
3. 非空时，先**丢弃短于 `TRUSTED_TEXT_MIN_CHARS = 200` 字符的元素**（含空串；v0.2 🔵-1 / 🔵-2，理由见「作者修订回应」）。对余下每段可信文本 `t`，取它在**与 content 相同的序列化口径**下的形态：content 为 dict / list 时 `j = json.dumps(t, ensure_ascii=False)[1:-1].casefold()`（与请求体里那段字节的转义形态一致，换行、引号、反斜杠都已转义）；content 为 str 时 `j = t.casefold()`（v0.2 🔵-4）。然后在 `folded` 中找出 `j` 的**全部**出现区间（`str.find` 循环，允许重叠起点前移 1）。全仓只有 `llm.py::chat_complete` 传可信集（其 payload 恒为 dict），web 四个调用点不传。
4. 对每条模式找出它在 `folded` 中的**全部**命中区间；某个命中 `[s, s+len(p))` 若**整体包含于**某个可信区间之内 → 这次命中豁免；**任何一个命中不被包含 → 照旧 `PermissionError`**（报错文案不变）。
5. 不做「先删掉可信文本再匹配」：删除会把横跨可信文本边界的命中拆碎（可信文本末尾 `cloud.lo` + 后接外来文本 `cal.json`），区间包含判定不受此影响。
6. 长度：`casefold` 可能改变长度（如 `ß`→`ss`），区间全部在 folded 串上计算，可信文本也先 fold 再找，两边同一坐标系。

### 5.2 可信文本集从哪来（v0.2 重写，回应 🟡-1～🟡-3）

可信集 = **(a) ∪ (b)**。每个元素都是一份**通过 §5.2.3 读域过滤**的仓库文档，按装配器对该层的读法读入后取 `.strip()` 的字符串。可信集是超集也无害，因为豁免只认逐字字节（§5.1）；但每一份进入可信集的文档都必须过 §5.2.3。

#### 5.2.1 (a) 本进程装配器提交过的正文（只增不减）

捕获点在装配器的读入处，取它**实际拼进消息的那份字符串**，逐文件取，不从拼接结果反推：
- **工序层**：`session.py::_assemble` 的首轮与换工序两处，对每个 `InjectedDoc` 取 `doc.content.strip()`（`render_step_injection` 拼进去的正是这个）。
- **修订重注入**：`_reinject_changed` **只对来自 `resolve_step_docs` 的候选**取 `doc.content.strip()`。消息里拼的是未 strip 的 `doc.content`，它的 strip 形态是消息的子串（探针 P12）。`resolve_memory_injection` 产出的记忆候选**一律不收**。
- **常驻层**：`assemble_resident_prompt` 改为同时返回三份文件**各自读入后**的 `.strip()`。scope 取拼 `extra_prompt` **之前**的文件原文；文件缺失时的占位标题（`# Director Persona` 等）不收。**不取 `tracker.resident_prompt`**，因为它含 `extra_prompt`（🟡-2(a)）。scope 原文的 strip 形态是常驻层的子串（探针 V5）。

存放：`SessionContextTracker` 新增 `trusted_doc_texts`（有序、去重），**不加入 `_rollback` 的恢复清单**。回滚只删消息，不收缩可信集。

(a) 的作用：同一进程里规程中途被改过时，历史里是旧版、磁盘上是新版，旧版只有 (a) 记得。

#### 5.2.2 (b) 路由表所指文档的当前磁盘正文（每回合装配时重读）

范围：`assembly.json` 的 resident 三件（按当前 scope 展开），加上 `routes` 下**全部 scope、全部工序键（含 `default`）**所指的文档。

读法：**与装配器对该层的读法相同**，再取 `.strip()`：
- 路由文档一律经 `load_injected_doc(...).content`（`read_bytes().decode("utf-8")`，**不翻译换行**）；
- 常驻三件经与 `assemble_resident_prompt` 共用的读函数（`read_text(encoding="utf-8", errors="replace")`，**翻译换行**）。

施工时把这两种读法各抽成一个函数，装配和可信集都调用它，不许另写第三种。CRLF 口径（🟡-2(c)）由此保证：同一层用同一读法，CRLF 文档的可信形态与消息里的字节一致（探针 V8）；把路由文档改用 `read_text` 读就会对不上（V9，MUT-D12）。

(b) 的作用：`--continue` 恢复出来的历史里，上一进程注入的规程只要没改版，就能在这里被认出（探针 V3）。规程改过版的残余见 §5.3。

#### 5.2.3 读域过滤（白名单根；(a)、(b) 一律适用）

一份文档的正文要进可信集，须**同时**满足以下各条：
1. `target.resolve(strict=True)` 成功，且 resolve 后的真实路径在 `root.resolve()` 之内（软链指向仓库外、或配置里写的是仓库外的绝对路径，都过不了这一条）；
2. resolve 后的相对路径落在**白名单根**内：`docs/`、`skills/`、`config/agent/scopes/`，或者恰好是仓库根的 `AGENTS.md`；
3. **不在 `data/` 下**（`data/` 软链到外置盘时第 1 条已经挡住；没软链时由这一条挡。`data/episodes/*/01-topic.md` 一类文件是模型经 `write_episode_file` 可写的），也不等于 `memory.MEMORY_REL_PATH`；
4. 后缀为 `.md`；
5. 配置里写的路径和 resolve 后的相对路径，casefold 后都不含任何 `RESTRICTED_EGRESS_PATTERNS`。

规则写成**一个**函数（如 `assembly.is_trusted_doc_path(root, rel) -> bool`），可信集收集调用它，N55 的装配器拒载修复也复用它，不许出现第二份规则。2026-10-06 核对：现有全部常驻 / 被路由文档都满足 1～5。

**记忆双重排除**：
- 来源上，`resolve_memory_injection` 的产物从不进入收集：`_inject_memory` 不调用收集逻辑，`_reinject_changed` 显式跳过记忆候选；
- 路径上，第 3 条再挡一次，即使有人把 `memory.md` 写进路由也进不来。

**不进可信集**：`extra_prompt`、状态卡、`render_step_injection` 与 `_reinject_changed` 的页眉页脚、工具 schema（🔵-6，§5.3）。

**这道过滤保障什么**：保障的是「模型可写或非规程来源的文本拿不到豁免」，**不保障**凭据内容不出网（断言本来就不看内容，§3、N55）。

#### 5.2.4 传递

可信集 = `tracker.trusted_doc_texts` ∪ 本回合 (b)，由 `session.py::AgentSession.run_turn` 在调用 `llm_module.run_tool_loop(..., egress_trusted=...)` 时传入，经 `run_tool_loop._chat` → `chat_complete(..., egress_trusted=...)` 到达断言。终端 `ava`（`cli.py`）和协议会话都走这同一处调用；`chat_complete` 全仓只有 `_chat` 一个调用方（收尾也经它，红队已核实），所以传一处就覆盖两个入口。不用模块级全局状态。

### 5.3 已知残余（如实接受）

- **恢复会话后规程已改**：`--continue` 重建的历史里是旧版规程正文，可信集只认磁盘上的现版本；若旧版含字面量而现版改了，旧消息里的命中不被豁免 → 仍会 `[BLOCKED]`。触发要求「规程恰好改了这几行」且恢复旧会话，概率低；绕法是开新会话。不为此把历史版本纳入可信集（那要信任 `session.jsonl`，它在期目录里）。
- **模型逐字复述整段规程**：复述出来的字节与仓库文档相同，豁免它不放出任何新内容，可接受。
- **人亲手在对话里打出受限路径**、**作业输出尾巴里带路径**：本方案不放过，仍会拦（见 §10 Q2/Q3）。
- **（v0.2，🔵-1）模型把 ≥ 200 字的规程原文逐字抄进工具参数**：判定与位置无关，这段会被豁免。被豁免的字节就是仓库文档本身，没有新内容出去；只写一个路径（「读域拒了 + 发送闸再掐」防的情形）远不到门槛。可以接受。
- **（v0.2，🔵-6）工具 schema 不在可信集里**：payload 的 `tools[].description` 也是仓库静态文本，哪天写进受限文件名，所有请求都会被拦。今天的 description 不含受限模式；真遇到时改措辞，或另行把 description 纳入可信集，本 spec 不预做。
- **（v0.2）凭据内容被误路由进消息**：不归本断言管，见 §3 与 N55。

## 6. 不做的事（施工纪律）

- 不放宽 `casefold`、不去掉 `_normalized_for_assert` 的 unquote 归一；Spec 4 §2.4 登记的 Unicode 同形上限（RF-11）不是扩面的口子。
- 不改 `_scrub`、状态卡清洗、记忆 R3；不改模式表四条内容。
- 不删、不弱化任何现有用例（T5a/T5b/T17、`test_assert_egress_boundary`、大小写用例、`test_egress_payload_blocks_case_variant_audio_read`、`test_m5_status_card_passes_assert_egress_boundary`）。
  - 与 Spec 15（D29）的交叉（v0.2 补，见 Spec 15 定向复审 R2-4）：Spec 15 PR1 会把 T5a / T5b / T17 的管道换成新 search 配置与 `_provider_opener`，要求断言强度不变。两者谁后落地，谁就在改写后的 T5a / T17 上复跑一次 MUT-D7，确认仍被杀死。
- 不改两份 runbook 的字面路径（那是方案 ①，会把复现用例变成假绿）。

## 7. 冻结面影响与文档修订面

| 文件 | 改动 |
|---|---|
| Spec 4 `2026-09-23-network-tools-spec.md` | §2.4 第 1 条（生效点）加修订记录：LLM 请求体断言允许带可信文本集，命中位置级豁免；web 四个出方向不变；引 D30、本 spec、ADR-0026 |
| impl spec `../2026-09-18-ava-agent-impl-spec.md` §2.5 Y2-r19 | 「出网边界」段尾加一句修订注记（日期、D30）：仓库规程逐字副本不视为越界内容 |
| ADR-0026（新，提议中 → 人接受后改「已通过」） | 决策、理由、放过面、残余 |
| ADR-0021 / ADR-0022 | frontmatter `related-issues` 加 D30（不改正文） |
| impl spec 第二处 Y2-r19（L1215 验收段，v0.2 🔵-5） | 同上加修订注记（L433 设计段即上面那一行），两处都写 |
| `README.md`「已知限制」的「03.5 期的会话」一行（v0.2 🔵-5） | 施工时改为「03 / 03.5 期的会话」并注明修复中；D30-C 通过后删除 |
| issues **N55**（v0.2 🟡-3 ④，已登记） | 本 spec 不修；§5.2.3 的白名单函数供其复用 |
| `docs/dev/plans/README.md`、issues D30 行 | 登记与状态 |

## 8. 测试与变异清单

用例（`tests/test_agent_tools.py` 断言层 + `tests/test_agent_session.py` 或 `tests/test_agent_loop.py` 会话层；期望值先在实现上跑一遍再写进断言）：

| 编号 | 断言 |
|---|---|
| **TD-1（复现转绿）** | 真实仓库规程 + step=03.5 与 step=03 两期：会话首轮 `chat_complete` 照常发出（fake urlopen 恰 1 次），请求体里**仍含** runbook 原文（含 `03-audio/manifest.json` 字面量）。修前同一用例红、失败原文是该命中串 |
| TD-1b（v0.2，🟡-1） | 同一 `AgentSession`、同一 tracker 跑三轮，期目录依次处在 03 → 03.5 → 05（真实仓库规程）：每轮首个 `chat_complete` 都照常发出（fake urlopen 各 1 次），第三轮请求体里仍含 03 与 03.5 两份规程原文 |
| TD-1c（v0.2，🟡-1） | 新进程 `--continue`（`prepare_resume`）恢复一份历史含 03 与 03.5 注入的会话，期目录处在 05：首轮照常发出（此时 tracker 是空的，只靠 (b)） |
| TD-1d（v0.2，🟡-2(c)） | 临时仓库里一份 **CRLF 换行**、含受限字面量的路由文档：首轮照常发出 |
| TD-1e（v0.2，🟡-1） | 临时仓库、同一进程：step 03 注入后，把 `03-tts.md` 的另一行改掉（字面量那行不动），再推进到 03.5：照常发出（历史里的旧版只有 (a) 认得） |
| TD-2a | 可信文本 = 规程；人发的消息里含 `cloud.local.json` → 拦 |
| TD-2b | 模型工具调用参数含 `03-AUDIO/MANIFEST.JSON` → 拦（与现有双保险用例同口径） |
| TD-2c | 工具返回值（role=tool）含 `agent.local.json` → 拦 |
| TD-2d | 记忆文档含受限串（人手改坏 `memory.md`、绕过 R3）→ 拦（记忆不在可信集） |
| TD-2e（v0.2，🟡-2(a)） | `extra_prompt` 含 `cloud.local.json` → 拦（常驻层的可信正文是三份文件各自的原文，不含 `extra_prompt`） |
| TD-2f（v0.2，🟡-2(b)） | `--continue` 后记忆文件被改（含受限串，绕过 R3 直接改盘），经 `_reinject_changed` 以 origin=`injection`、页眉「规程已修订」重注入 → 拦；并直接断言 `tracker.trusted_doc_texts` 和本回合 (b) 都不含记忆正文 |
| TD-3 | 同一条注入消息里，规程正文之后拼接一段非规程文字含受限串 → 拦（豁免是区间级，不是整条消息级） |
| TD-4 | 跨边界：可信文本以 `…cloud.lo` 结尾、后接外来文本 `cal.json` → 拦 |
| TD-5（v0.2 改，🟡-3 ③） | **直接断言可信集本身**（对收集函数 / `is_trusted_doc_path` 的返回值断言，不再借「内容含受限串 → 拦」间接测）：`assembly.json` 路由（及 resident）分别指向 ① 非 `.md` 文件；② 路径含受限模式；③ 仓库根外的绝对路径；④ `docs/` 下软链到 `config/` 的 `.md`；⑤ 未软链的 `data/episodes/<期>/01-topic.md`；⑥ `data/library/memory.md`；⑦ 白名单根外的根内 `.md`（如 `pipeline/x.md`）。各一例，该文档正文都不在可信集里；对照组：真实 `docs/runbook/03-tts.md` 在 |
| TD-6 | `trusted_texts=()` 时与修前逐字节同判（对现有 `test_assert_egress_boundary` 全部输入再跑一遍，结果相同） |
| TD-6b（v0.2，🔵-4） | content 为 str 时按原文找可信文本：含换行、≥ 200 字的可信文本能被认出（放），在它之外拼上受限串（拦） |
| TD-7 | 可信文本含 `ß` 等 casefold 变长字符、受限串在其后：区间计算不错位（该放的放、该拦的拦各一例） |
| TD-7b（v0.2，🔵-3） | ≥ 200 字的可信文本，模式**恰在开头**、**恰在结尾**各一例 → 放（MUT-D8 的指定杀手） |
| TD-8（v0.2，🔵-1） | 可信文本「见 03-audio/manifest.json。」（短于 200 字），模型在工具参数里写出同一句 → 拦；门槛边界：同构文本 199 字 → 拦、200 字 → 放 |

变异（每条须由指定用例以断言杀死；前置失败、超时不算）：

| 编号 | 变异 | 应被杀于 |
|---|---|---|
| MUT-D1 | 去掉豁免（回到整串判定） | TD-1 |
| MUT-D2 | 记忆文档进了可信集 | TD-2d |
| MUT-D3 | 区间判定改为「命中出现在任意含可信文本的消息里就放」（整条消息级） | TD-3 |
| MUT-D4 | 区间判定改为「先删除可信文本再匹配」 | TD-4 |
| MUT-D5 | 去掉可信文档的路径过滤（或只留后缀判断） | TD-5 |
| MUT-D6 | 可信文本不做 JSON 转义形态直接找（换行处对不上） | TD-1 |
| MUT-D7 | 断言整个关掉（`assert_egress_boundary` 直接 return） | T5a、T17、TD-2a |
| MUT-D8 | 包含判定的边界差一（`<=` 写成 `<`） | TD-7b |
| MUT-D9（v0.2） | 可信集只认当前工序（(a) 不累积、(b) 只取当前 step 的路由） | TD-1b |
| MUT-D9b（v0.2） | 去掉 (a)，只用 (b) | TD-1e |
| MUT-D10（v0.2） | `tracker.resident_prompt` 整体入集（代替三份文件原文） | TD-2e |
| MUT-D11（v0.2） | `_reinject_changed` 收集可信正文时不滤记忆候选 | TD-2f |
| MUT-D12（v0.2） | (b) 读路由文档改用 `read_text`（换行翻译，与注入读法不一致） | TD-1d |
| MUT-D13（v0.2） | 去掉长度门槛 | TD-8 |
| MUT-D9c（定向复审 🔵-R2-2） | (b) 只取当前 step 的路由（(a) 照旧） | TD-1c |

## 9. PR 划分与验证

单 PR（core only）：`tools.py`（断言签名、区间判定、长度门槛）、`llm.py`（`run_tool_loop` / `chat_complete` 透传）、`assembly.py`（`assemble_resident_prompt` 返回三份文件原文、两种读法各抽一个函数、`is_trusted_doc_path`、(b) 的收集函数）、`session.py`（`tracker.trusted_doc_texts` 的累积点与 `run_turn` 传参，`_rollback` 恢复清单**不加**这个字段）、用例；文档按 §7。

验证：`PYTHONDONTWRITEBYTECODE=1 uv run pytest tests/test_agent_tools.py tests/test_agent_web.py tests/test_agent_loop.py tests/test_agent_session.py` → 全量 `uv run pytest` 全绿；变异逐条实跑回填并 md5 对拍；在临时仓库副本上用**真会话**（protocol，假 LLM 端点）复跑 §1 的复现，两期首轮不再 `[BLOCKED]` 并存证。

## 10. 待人裁决

> **2026-10-06 人裁决**：Q1 采用 **②a**；Q2 / Q3 / Q4 均同意建议（人打的受限路径照旧拦；N53 与 N52 另立条目，本 spec 不碰）。下一步：红队 D30-R。

- **Q1 方案**：推荐 ②a。②b/②c/②d/① 的代价见 §4。
- **Q2 人亲手打出受限路径**：现状与 ②a 下都会拦（例如人问「`03-audio/manifest.json` 里第 3 段时长多少」）。建议**本 spec 不处理**：人打的字也可能是粘贴进来的凭据内容，按名字拦虽然粗，方向是安全的；另立条目再议。
- **Q3 作业输出尾巴**：读码推断（**未实测**）——`run_pipeline` 返回值带作业 stdout 尾部，若某作业打印了含 `03-audio/manifest.json` 的绝对路径（如 `tts.py` 的「打点已写入 {manifest_path}」），下一次请求会被拦。建议登记为新问题、本 spec 不碰（要么作业侧不打印绝对路径，要么工具返回值进会话前走 `_scrub`，二者都是另一处冻结面）。
- **Q4 桌面端看不到拦截原因**：协议路径 `turn_finished` 只有 `stopped:"blocked"`，命中串不下发。建议登记新问题，修法是 blocked 时发一条 `notice`（含命中模式名，不含请求体），另走 Spec 9 协议修订。

## 11. 门禁

- [x] 1. 人裁决 Q1–Q4；红队 🟢。　证据：§10 人裁决 2026-10-06；定向复审 D30-R2 🟢（`963ba1c`）。
- [x] 2. TD-1 修前红（失败原文为命中串）、修后绿；TD-1b～TD-1e、TD-2～TD-8（含 b～f 子项）全绿。　证据：D30-C 把四个文件退回 `d5d94bf^` 亲跑 TD-1，两期均 `AssertionError: 本轮被出网断言拦下：拦截出网请求：内容包含受限敏感标记 '03-audio/manifest.json'`；还原（md5 一致）后全绿（§13）。
- [x] 3. MUT-D1～D13（含 D9b、D9c）全部由指定用例以断言杀死；TD-2f 按定向复审 🔵-R2-1 构造并先断言重注入确实发生，还原 md5 一致。　证据：§12 施工回填；D30-C 另设计 4 条亲跑，3 条以断言杀死、1 条存活并登记 N57（§13）。
- [x] 4. 全量 `uv run pytest` 全绿。　证据：D30-C 亲跑 `2003 passed in 94.95s`。
- [x] 5. 真会话复跑 §1 复现存证（03 与 03.5 两期）。　证据：D30-C 用 `git archive` 取修前 / 修后两份临时仓库副本（不含任何 `*.local.json`），协议子进程 + 只监听 127.0.0.1 的假 LLM 独立复跑，结果与 §12 一致（§13）。
- [x] 6. §7 文档修订面齐全；ADR-0026 状态与人裁决一致。　证据：Spec 4 §2.4 修订记录、impl spec 两处 Y2-r19 注记、ADR-0021/0022 frontmatter、README 已知限制均在 `d5d94bf`；ADR-0026 已通过。

## 12. 施工回填（D30-B，2026-10-06；施工人 = v0.2 作者，未参与 D30-R / D30-R2；D30-C 须另开 session）

**diff 摘要**（core only，均在 §9 声明的文件内）：
- `tools.py`：`assert_egress_boundary(..., *, trusted_texts=())`，区间包含判定 + `TRUSTED_TEXT_MIN_CHARS = 200`；可信区间惰性计算（没有任何模式命中就不算）；str / dict 两种序列化口径。
- `llm.py`：`chat_complete` 与 `run_tool_loop` 加 `egress_trusted`，`_chat` 透传；默认 `()`，现有调用与桩不受影响。
- `assembly.py`：`AssembledResident.doc_texts`（三份文件各自的 strip 原文，scope 取拼 `extra_prompt` 之前）；常驻层读法抽成 `read_resident_file`、resident 路径解析抽成 `resident_paths`；`is_trusted_doc_path`（§5.2.3 五条，唯一一份规则，供 N55 复用）；`trusted_texts_of_docs` / `trusted_texts_of_resident`（(a) 的捕获）；`route_trusted_texts`（(b)，路由文档一律经 `load_injected_doc`）；`SessionContextTracker.trusted_doc_texts` + `trust()`。
- `session.py`：`_assemble` 在常驻层重算、首轮注入、换工序注入三处累积 (a)；`_reinject_changed` 只对 `resolve_step_docs` 的候选累积，记忆候选标记为不可信；`run_turn` 组装 (a) ∪ (b) 传给 `run_tool_loop`；`_rollback` 恢复清单**未加**该字段。
- 用例：`tests/test_agent_session.py` 会话层 11 条（TD-1 ×2、TD-1b～1e、TD-2a、TD-2d、TD-2e、TD-2f、TD-5），`tests/test_agent_tools.py` 断言层 8 条（TD-2b/2c、TD-3、TD-4、TD-6、TD-6b、TD-7、TD-7b、TD-8）。会话层用例走真 `chat_complete`、只替换 `urlopen`，规程取真实仓库文档。

**门禁对照**：
- 门禁 2：TD-1 修前实跑红，两期失败原文均为 `本轮被出网断言拦下：拦截出网请求：内容包含受限敏感标记 '03-audio/manifest.json'`；修后全绿。
- 门禁 4：全量 `PYTHONDONTWRITEBYTECODE=1 uv run pytest` → `2003 passed in 94.65s`。
- 门禁 5（真会话，临时仓库副本、不含任何 `*.local.json`，假 LLM 只监听 127.0.0.1，`python -m pipeline.agent.protocol data/episodes/<期>` 发一条 `user_message{"text":"你好"}`）：

| 期 | 修前（HEAD `21a2278` 的四个文件） | 修后 |
|---|---|---|
| D30-S03（step 03） | `turn_finished.stopped=blocked`，`llm_calls=0`，`prompt_chars=9192`，假 LLM 收到 0 个请求 | `stopped=done`，`llm_calls=1`，`prompt_chars=9192`，收到 1 个请求，请求体含 `03-tts.md` 原文与字面量 |
| D30-S035（step 03.5） | `blocked`，`llm_calls=0`，`prompt_chars=14031`，0 个请求 | `done`，`llm_calls=1`，`prompt_chars=14031`，1 个请求，请求体含 `03.5-voice-check.md` 原文与字面量 |

`prompt_chars` 与 D30-A 复现时的 14 031 / 9 192 一致。

**变异回填**（`PYTHONDONTWRITEBYTECODE=1`，每条只跑 `test_agent_tools.py` + `test_agent_session.py` + `test_agent_web.py`，还原后 md5 对拍全部一致，零 collection 错误）：

| 变异 | 实际植入 | 指定杀手 | 结果与杀法 |
|---|---|---|---|
| MUT-D1 | 包含判定恒为「不包含」 | TD-1 | KILLED：`AssertionError: 本轮被出网断言拦下…` |
| MUT-D2 | `_inject_memory` 把记忆正文 `trust` 进 (a) | TD-2d | KILLED：`assert 'done' == 'blocked'` |
| MUT-D3 | 只要请求体里出现任一可信文本就整体放行（比整条消息级更粗；TD-3 是单条消息，两者在杀手上等价） | TD-3 | KILLED：`DID NOT RAISE PermissionError` |
| MUT-D4 | 先删可信文本再匹配 | TD-4 | KILLED：`DID NOT RAISE PermissionError` |
| MUT-D5 | `is_trusted_doc_path` 只判 `.md` 后缀 | TD-5 | KILLED：`②路径含受限模式 不该进可信集` |
| MUT-D6 | 可信文本不转 JSON 转义形态 | TD-1 | KILLED：`AssertionError: 本轮被出网断言拦下…` |
| MUT-D7 | 断言直接 `return` | T5a、T17、TD-2a | KILLED（T5a `DID NOT RAISE`、TD-2a `assert 'done' == 'blocked'`）。**T17 不计**：它的失败是 `llm.py` 里的 `TypeError`（请求真的到了返回 `None` 的 urlopen 桩），属逃逸类症状；T17 是 Spec 4 冻结用例，本 PR 不改它，如实登记 |
| MUT-D8a / D8b | `a <= start` → `<`；`end <= b` → `<` | TD-7b | 均 KILLED：TD-7b 期望放行，实际抛 `PermissionError`（被测性质本身翻转，不是前置失败） |
| MUT-D9 | (a)、(b) 都只取当前工序的文档 | TD-1b | KILLED：`AssertionError: 本轮被出网断言拦下…` |
| MUT-D9b | 去掉 (a)，只用 (b) | TD-1e | KILLED：同上 |
| MUT-D9c | (b) 只取当前工序，(a) 照旧 | TD-1c | KILLED：同上 |
| MUT-D10 | 常驻层把 `resident.content`（含 `extra_prompt`）整体入集 | TD-2e | KILLED：`assert 'done' == 'blocked'` |
| MUT-D11 | `_reinject_changed` 对全部候选（含记忆）直接 `trust(doc.content.strip())`，不过来源与路径过滤 | TD-2f | KILLED：`assert 'done' == 'blocked'`（用例先断言「规程已修订：data/library/memory.md」确实写进 session.jsonl） |
| MUT-D11s（补充） | 只把记忆候选的来源标记翻成「可信」 | TD-2f | **SURVIVED，等价变异**：路径过滤（§5.2.3 第 3 条）照样把 `data/library/memory.md` 挡在可信集外。记忆的两道排除互为后备，单拆一道观察不到；路径那道单独由 TD-5 ⑥ 守 |
| MUT-D12 | (b) 读路由文档改用 `read_text` | TD-1d | KILLED：`AssertionError: 本轮被出网断言拦下…` |
| MUT-D13 | 去掉长度门槛 | TD-8 | KILLED：`DID NOT RAISE PermissionError` |

**与 spec 的偏差与未实测项（如实）**：
1. **常驻层 (a) 只在 `_assemble` 重算处捕获**。`protocol.py` / `cli.py` 共 6 处在会话外直接 `tracker.resident_prompt = assemble_resident_prompt(...).content`，那里不在 §9 声明的改动文件内，没有改。这些入口的常驻层由 (b)（每回合按当前 scope 重读三件）覆盖。残余：同一进程内常驻文件被改、且旧版含受限字面量时，旧常驻层不被认出、会被拦（今天三件常驻文档都不含受限模式）。D30-C 若认为必须补，改法是让这 6 处也走 `tracker.trust(trusted_texts_of_resident(...))`。
2. **TD-2d / TD-2f 用打桩模拟「R3 失守」**：`memory.render_injection` 读盘时同样执行 R3，含受限串的 `memory.md` 只会渲染成告警、不会出现在消息里，所以「人手改坏 memory.md」在真实读路径上构造不出来。用例给渲染函数打桩，假设记忆层失守，验证的是出网断言这第二层不把记忆当可信文本。
3. TD-1b 的第三步用 05（仓库没有 04 工序键），与 v0.2 回应表一致。
4. 未在桌面端打包版上复跑；门禁 5 用的是协议子进程（与桌面端会话同一入口 `pipeline.agent.protocol`）。


## 13. 独立评审（D30-C，2026-10-06；评审人 = D30-R / D30-R2 红队，未参与 v0.2 修订与 D30-B 施工）

**结论：✅ 通过**。D30 迁 issues archive，README「已知限制」删去 03 / 03.5 一行。无阻断项；2 条 🔵 已登记或记录。

① **亲自复现修前与修后**：
- 单测层：把 `tools.py` / `llm.py` / `session.py` / `assembly.py` 退回 `d5d94bf^` 跑 TD-1，两期都红，失败原文是命中串 `'03-audio/manifest.json'`；还原后 md5 与修后一致，`git status` 干净。
- 真会话层：用 `git archive` 分别取 `d5d94bf^` 与 `d5d94bf` 两份临时仓库副本，不含任何 `*.local.json`，`agent.local.json` 现写、指向只监听 127.0.0.1 的假 LLM，环境里剥掉全部 `*_API_KEY` / `*_TOKEN` 与代理变量；起 `python -m pipeline.agent.protocol data/episodes/<期>`，`ready` 后发「你好」：

| 期 | 修前 | 修后 |
|---|---|---|
| D30-S03（`inspect` → 03 语音合成） | `stopped=blocked`，`llm_calls=0`，`prompt_chars=9192`，假 LLM 收到 0 个请求 | `done`，`llm_calls=1`，`prompt_chars=9192`，收到 1 个请求；请求体 `messages[1]` 含 `03-tts.md` 的 strip 原文，含字面量 |
| D30-S035（03.5 配音顺听 / 04 排片） | `blocked`，0 个请求，`prompt_chars=14031` | `done`，1 个请求，`prompt_chars=14031`；`messages[1]` 含 `03.5-voice-check.md` 原文 |

- 全量 `PYTHONDONTWRITEBYTECODE=1 uv run pytest` → `2003 passed`。

② **D30-R 最坏样例清单逐条在修后代码上实跑**（真实 `assert_egress_boundary` + 真实 `route_trusted_texts("creative")` 取到的可信集，16 段、最短 564 字；scratchpad `d30c/worst.py`）：
- **该拦的全拦**：工具参数大小写变体 `03-AUDIO/MANIFEST.JSON`、工具返回值含 `agent.local.json`、记忆消息含 `cloud.local.json`、规程后紧贴受限串、跨可信边界 `cloud.lo|cal.json`、复述规程再夹带、复述规程并在中间插入受限串、短于 200 字的可信文本被复述、`ß` 可信文本之外拼受限串。
- **该放的放**：03.5 注入基线；`ß` 变长字符在前的可信文本。
- **与修前相同、本 spec 不管的**：URL 编码 `cloud%2Elocal%2Ejson` 与全角同形字，修前修后都放（LLM 请求体断言本来就不做 unquote，同形字属 RF-11 上限）。
- **读域过滤 13 例全部符合预期**：只有白名单内的真实文件为 True；以下都为 False——软链到凭据、软链到期目录、软链出根、仓库外绝对路径、`data/episodes/*/01-topic.md`、`data/library/memory.md`、`pipeline/x.md`、两种 `..` 穿越、大小写变体 `DOCS/…`、不存在的文件、`config/cloud.local.json`。

③ **变异 4 条亲跑，全部自行设计**（非 §12 MUT-D 系列复跑；`PYTHONDONTWRITEBYTECODE=1`，只跑 `test_agent_{tools,session,assembly,loop}.py`，还原后 md5 全部一致）：

| 变异 | 结果 |
|---|---|
| V1 包含判定改为重叠判定（`a < end and start < b`） | KILLED by TD-4：`DID NOT RAISE PermissionError` |
| V3 白名单按配置路径判，不按 resolve 后真实路径判 | KILLED by TD-5：`④软链目标 不该进可信集：docs/link.md` |
| V6 首轮工序注入不进 (a)（只留换工序那处） | KILLED by TD-1e：`本轮被出网断言拦下…'03-audio/manifest.json'` |
| V4 (b) 不收常驻三件 | **SURVIVED**（148 passed），见 🔵-C1 |

④ **文档面**：Spec 4 §2.4 修订记录、impl spec 两处 Y2-r19 注记、ADR-0021/0022 frontmatter 关联 D30、README 已知限制、plans/README、issues D30 都在 `d5d94bf`，与 §7 对得上。
⑤ **diff 边界**：代码只动 §9 声明的 `tools.py` / `llm.py` / `assembly.py` / `session.py`，用例只动 `test_agent_tools.py` / `test_agent_session.py`，其余都是 §7 列出的文档，合规。

**发现（无阻断）**：
- **🔵-C1 生产入口的常驻层只靠 (b)，而 (b) 的常驻部分没有用例守（V4 存活）**。`protocol.py:640` 与 `cli.py:1799` 等处在进入会话前就设好了 `tracker.resident_prompt` 与 `active_scope`，所以 `_assemble` 里 `trust(trusted_texts_of_resident(...))` 只在 scope 热切换时执行。桌面端与终端的常驻层豁免实际全靠 `route_trusted_texts` 的常驻部分，删掉它全部用例仍绿。今天三份常驻文档都不含受限字面量，所以无可见后果；方向是 fail-closed（将来常驻文档写进字面量会被拦，不会漏）。这与 §12 偏差 1 是同一根源，但 §12 只说了「同进程改版」这一个残余，没说「这一路零用例」。登记 **N57**：补一条用例（常驻文档含字面量、tracker 按 `protocol.py` 的方式预置，首轮应照常发出，用来杀 V4），并视情况让那 6 处也走 `trust(...)`。
- **🔵-C2 §5.3 一条残余说明与实测不符，实测方向更严**。§5.3 写「模型把 ≥ 200 字的规程原文逐字抄进工具参数会被豁免」，实测 W8 是**被拦**。原因是 `tool_calls[].function.arguments` 本身是 JSON 字符串，在请求体里转义了两次，与可信文本的单层转义形态对不上。逐字复述写在 assistant 的 `content` 里时，才会按 §5.3 所说被豁免。不影响安全；本条记录在此，代替修改 §5.3 正文。
- **施工方的 MUT-D7 说明成立**：T17 在断言关掉时死于 `TypeError`，属逃逸，不计为杀死；T5a 与 TD-2a 以断言杀死。T17 是 Spec 4 冻结的用例，未改它是对的。Spec 15 PR1 改写 T17 腿 ② 时会补「拦截出网请求」断言（Spec 15 v0.3 R2-4），届时 T17 可重新成为合格杀手。
