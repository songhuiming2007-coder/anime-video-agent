# Spec 16：出网断言对「可信仓库文档」做命中位置级豁免（D30）

> **状态：v0.1 草案（2026-10-06，D30-A 立文；同日人裁决 §10：Q1 = ②a，Q2–Q4 同意建议）**。待红队（D30-R）🟢 → 施工（D30-B）→ 独立评审（D30-C）。本文件不改代码。
> 对应 issues：**D30**（主）；顺带登记的新问题见 §10 Q3/Q4。
> 相关：Spec 4（`archive/2026-09-23-network-tools-spec.md`）§2.4 出网断言生效点、§7.1 T5a/T5b/T17、MUT-2/MUT-13；impl spec（`2026-09-18-ava-agent-impl-spec.md`）§2.5 **Y2-r19** 出网边界；ADR-0021（网络工具内化）、ADR-0022（工序层上下文装配）；新提 **ADR-0026**（`docs/dev/adr/0026-egress-assert-trusted-repo-docs.md`，提议中）。

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

## 4. 候选方案（每个都写清放过了什么）

| 编号 | 方案 | 放过了什么 | 放过的面里有没有真凭据风险 | 治本？ | 冻结面 |
|---|---|---|---|---|---|
| ① | 改写两份 runbook，避开字面路径（如写成「期目录下 03-audio 里的 manifest」） | 无（断言不变） | 无 | **否**：任何规程再写一次字面路径就复发；还逼文档作者绕着护栏措辞 | 不动 |
| **②a（推荐）** | **命中位置级豁免**：断言时额外给一组「可信文本」（本回合装配器从仓库读入的常驻层与工序层文档正文）；一个模式命中**只有整体落在某段可信文本的逐字副本区间内**才放过，其余命中照旧拦 | 仓库规程文档里的字面路径（只限逐字副本那几段字节） | **无**：可信文本 = 磁盘上 git 跟踪的 `.md` 规程，模型无写入口；记忆（模型可写）明确不在可信集内；凭据文件不可能被选为可信文档（§5.2 路径过滤） | **是**：任何规程写任何字面量都不再炸；其他来源的命中一条不放 | 改 Spec 4 §2.4 生效点措辞（加修订记录），T5a/T5b/T17 不变 |
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
3. 非空时，对每段可信文本 `t`：取它在**同一序列化口径**下的形态 `j = json.dumps(t, ensure_ascii=False)[1:-1].casefold()`（与请求体里那段字节的转义形态一致：换行、引号、反斜杠都已转义），在 `folded` 中找出 `j` 的**全部**出现区间（`str.find` 循环，允许重叠起点前移 1）。
4. 对每条模式找出它在 `folded` 中的**全部**命中区间；某个命中 `[s, s+len(p))` 若**整体包含于**某个可信区间之内 → 这次命中豁免；**任何一个命中不被包含 → 照旧 `PermissionError`**（报错文案不变）。
5. 不做「先删掉可信文本再匹配」：删除会把横跨可信文本边界的命中拆碎（可信文本末尾 `cloud.lo` + 后接外来文本 `cal.json`），区间包含判定不受此影响。
6. 长度：`casefold` 可能改变长度（如 `ß`→`ss`），区间全部在 folded 串上计算，可信文本也先 fold 再找，两边同一坐标系。

### 5.2 可信文本集从哪来

只取**本回合装配器从仓库读入的规程正文**，按 `strip()` 后的形态（与 `render_step_injection` / `assemble_resident_prompt` 实际拼进消息的字节一致）：

- 常驻层三件：`assembly.json` `resident` 指名的 director / scope / agents 文档；
- 工序层：`resolve_step_docs(scope, step_key)` 解析出的全部文档（含 `_reinject_changed` 追加的修订版）。

**硬排除**：
- 记忆文档（`resolve_memory_injection`，`memory.MEMORY_REL_PATH`）——模型经 `write_memory` 可写，永不进可信集；
- 路径不以 `.md` 结尾的文档；路径本身命中 `RESTRICTED_EGRESS_PATTERNS`（casefold）或 resolve 后不在仓库根之内的文档——防 `assembly.json` 被人误配指向 `config/cloud.local.json` 之类时，凭据被当成「可信规程」放出去（此时不进可信集，断言照常拦下）；
- `extra_prompt`、状态卡、`render_step_injection` 的页眉页脚——它们不是仓库文档，照常断言。

可信集由会话持有（`SessionContextTracker` 已有 `resident_prompt` 与 `injected_paths`，施工时加一个只读的可信正文列表），经 `run_tool_loop` → `chat_complete(..., egress_trusted=...)` 显式传到断言，不用模块级全局状态。终端 `ava`（`cli.py`）与协议会话都经 `session.py::AgentSession` 的同一处 `llm_module.run_tool_loop(...)` 调用（读码确认只有这一处），在这里传一次即覆盖两个入口；收尾调用（wrapup）若另走 `chat_complete`，同样要传。

### 5.3 已知残余（如实接受）

- **恢复会话后规程已改**：`--continue` 重建的历史里是旧版规程正文，可信集只认磁盘上的现版本；若旧版含字面量而现版改了，旧消息里的命中不被豁免 → 仍会 `[BLOCKED]`。触发要求「规程恰好改了这几行」且恢复旧会话，概率低；绕法是开新会话。不为此把历史版本纳入可信集（那要信任 `session.jsonl`，它在期目录里）。
- **模型逐字复述整段规程**：复述出来的字节与仓库文档相同，豁免它不放出任何新内容，可接受。
- **人亲手在对话里打出受限路径**、**作业输出尾巴里带路径**：本方案不放过，仍会拦（见 §10 Q2/Q3）。

## 6. 不做的事（施工纪律）

- 不放宽 `casefold`、不去掉 `_normalized_for_assert` 的 unquote 归一；Spec 4 §2.4 登记的 Unicode 同形上限（RF-11）不是扩面的口子。
- 不改 `_scrub`、状态卡清洗、记忆 R3；不改模式表四条内容。
- 不删、不弱化任何现有用例（T5a/T5b/T17、`test_assert_egress_boundary`、大小写用例、`test_egress_payload_blocks_case_variant_audio_read`、`test_m5_status_card_passes_assert_egress_boundary`）。
- 不改两份 runbook 的字面路径（那是方案 ①，会把复现用例变成假绿）。

## 7. 冻结面影响与文档修订面

| 文件 | 改动 |
|---|---|
| Spec 4 `archive/2026-09-23-network-tools-spec.md` | §2.4 第 1 条（生效点）加修订记录：LLM 请求体断言允许带可信文本集，命中位置级豁免；web 四个出方向不变；引 D30、本 spec、ADR-0026 |
| impl spec `2026-09-18-ava-agent-impl-spec.md` §2.5 Y2-r19 | 「出网边界」段尾加一句修订注记（日期、D30）：仓库规程逐字副本不视为越界内容 |
| ADR-0026（新，提议中 → 人接受后改「已通过」） | 决策、理由、放过面、残余 |
| ADR-0021 / ADR-0022 | frontmatter `related-issues` 加 D30（不改正文） |
| `docs/dev/plans/README.md`、issues D30 行 | 登记与状态 |

## 8. 测试与变异清单

用例（`tests/test_agent_tools.py` 断言层 + `tests/test_agent_session.py` 或 `tests/test_agent_loop.py` 会话层；期望值先在实现上跑一遍再写进断言）：

| 编号 | 断言 |
|---|---|
| **TD-1（复现转绿）** | 真实仓库规程 + step=03.5 与 step=03 两期：会话首轮 `chat_complete` 照常发出（fake urlopen 恰 1 次），请求体里**仍含** runbook 原文（含 `03-audio/manifest.json` 字面量）。修前同一用例红、失败原文是该命中串 |
| TD-2a | 可信文本 = 规程；人发的消息里含 `cloud.local.json` → 拦 |
| TD-2b | 模型工具调用参数含 `03-AUDIO/MANIFEST.JSON` → 拦（与现有双保险用例同口径） |
| TD-2c | 工具返回值（role=tool）含 `agent.local.json` → 拦 |
| TD-2d | 记忆文档含受限串（人手改坏 `memory.md`、绕过 R3）→ 拦（记忆不在可信集） |
| TD-3 | 同一条注入消息里，规程正文之后拼接一段非规程文字含受限串 → 拦（豁免是区间级，不是整条消息级） |
| TD-4 | 跨边界：可信文本以 `…cloud.lo` 结尾、后接外来文本 `cal.json` → 拦 |
| TD-5 | `assembly.json` 路由指向非 `.md` 文件 / 路径含受限模式 / 仓库根之外 → 不进可信集，内容含受限串 → 拦 |
| TD-6 | `trusted_texts=()` 时与修前逐字节同判（对现有 `test_assert_egress_boundary` 全部输入再跑一遍，结果相同） |
| TD-7 | 可信文本含 `ß` 等 casefold 变长字符、受限串在其后：区间计算不错位（该放的放、该拦的拦各一例） |

变异（每条须由指定用例以断言杀死；前置失败、超时不算）：

| 编号 | 变异 | 应被杀于 |
|---|---|---|
| MUT-D1 | 去掉豁免（回到整串判定） | TD-1 |
| MUT-D2 | 记忆文档进了可信集 | TD-2d |
| MUT-D3 | 区间判定改为「命中出现在任意含可信文本的消息里就放」（整条消息级） | TD-3 |
| MUT-D4 | 区间判定改为「先删除可信文本再匹配」 | TD-4 |
| MUT-D5 | 去掉可信文档的路径过滤 | TD-5 |
| MUT-D6 | 可信文本不做 JSON 转义形态直接找（换行处对不上） | TD-1 |
| MUT-D7 | 断言整个关掉（`assert_egress_boundary` 直接 return） | T5a、T17、TD-2a |
| MUT-D8 | 包含判定的边界差一（`<=` 写成 `<`） | TD-1 或 TD-7（施工时指认到具体一条） |

## 9. PR 划分与验证

单 PR（core only）：`tools.py`（断言签名与区间判定）、`llm.py`（`chat_complete` 透传）、会话两入口传可信集、用例；文档按 §7。

验证：`PYTHONDONTWRITEBYTECODE=1 uv run pytest tests/test_agent_tools.py tests/test_agent_web.py tests/test_agent_loop.py tests/test_agent_session.py` → 全量 `uv run pytest` 全绿；变异逐条实跑回填并 md5 对拍；在临时仓库副本上用**真会话**（protocol，假 LLM 端点）复跑 §1 的复现，两期首轮不再 `[BLOCKED]` 并存证。

## 10. 待人裁决

> **2026-10-06 人裁决**：Q1 采用 **②a**；Q2 / Q3 / Q4 均同意建议（人打的受限路径照旧拦；N53 与 N52 另立条目，本 spec 不碰）。下一步：红队 D30-R。

- **Q1 方案**：推荐 ②a。②b/②c/②d/① 的代价见 §4。
- **Q2 人亲手打出受限路径**：现状与 ②a 下都会拦（例如人问「`03-audio/manifest.json` 里第 3 段时长多少」）。建议**本 spec 不处理**：人打的字也可能是粘贴进来的凭据内容，按名字拦虽然粗，方向是安全的；另立条目再议。
- **Q3 作业输出尾巴**：读码推断（**未实测**）——`run_pipeline` 返回值带作业 stdout 尾部，若某作业打印了含 `03-audio/manifest.json` 的绝对路径（如 `tts.py` 的「打点已写入 {manifest_path}」），下一次请求会被拦。建议登记为新问题、本 spec 不碰（要么作业侧不打印绝对路径，要么工具返回值进会话前走 `_scrub`，二者都是另一处冻结面）。
- **Q4 桌面端看不到拦截原因**：协议路径 `turn_finished` 只有 `stopped:"blocked"`，命中串不下发。建议登记新问题，修法是 blocked 时发一条 `notice`（含命中模式名，不含请求体），另走 Spec 9 协议修订。

## 11. 门禁

1. 人裁决 Q1–Q4；红队 🟢。
2. TD-1 修前红（失败原文为命中串）、修后绿；TD-2～TD-7 全绿。
3. MUT-D1～D8 全部由指定用例以断言杀死，还原 md5 一致。
4. 全量 `uv run pytest` 全绿。
5. 真会话复跑 §1 复现存证（03 与 03.5 两期）。
6. §7 文档修订面齐全；ADR-0026 状态与人裁决一致。
