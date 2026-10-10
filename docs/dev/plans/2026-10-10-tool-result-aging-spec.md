# D65：工具结果老化（tool result aging）施工 spec

> 对应 issue：**D65**（`docs/dev/issues/README.md`）。
> 相关：D62（/compact，本 spec 修订其三处口径）、D41/D56/D64（读数链路）、Spec 18（建期迁移）、ADR-0022（装配路由）、Spec 16（出网断言）、D52（工具结果脱敏）。
> 本 spec 只覆盖 D65。无 UI 改动，plans/README 的 UI 五条规则不适用。
> **2026-10-10 红队复审已过一轮**：F1（wrapup 边界）、F2（compact 口径）、F3（脱敏前提）、F4（占位文案）四条成立已吸收；数据修正（统计口径、伪恋三条大头、web 零样本）已回填。本版为修订稿。

---

## 一、目标

消灭「上下文 3–5 轮涨到 100k」的**工具结果这一根**：大体积工具结果逐字永久驻留会话历史、每轮全量重发。（红队范围提示：assistant 消息里 tool_calls 的**参数**——写稿工具的参数是整篇稿子——不在本 spec 范围；写稿重的会话仍有第二根，见 §七「残留病根」。）

机制：**请求时纯函数老化投影**。发给模型前，把「上一用户回合及更早」且「超过阈值」的 tool 消息正文替换为占位摘要；`session.jsonl` 与内存中的 `convo` **一字不动**，日志保留全文、可恢复、可重读。

为什么不改迁移（Spec 18）：迁移代码零改动即被覆盖——选题段带进来的旧工具结果，在期会话第一轮请求时自然落在「上一回合及更早」区间，直接被老化。

与 D62 的关系（红队 F2 修订）：老化先做、读数（服务商 `prompt_tokens`）自动反映老化后大小；但 compact 的**切口预算**、**摘要器输入**、**膨胀护栏尺子**三处必须同步换成老化后口径（人拍板 2026-10-10，含 is_bloated 补漏），否则「原始体量 ≫ 发送体量」成为常态后会出真故障（§四 compact 改动）。

### 已排除的备选（留痕，防重提）

| 备选 | 排除理由 |
|---|---|
| 改小 `MAX_READ_BYTES`（200KB → 几十 KB） | 治标。当轮一次性读入的大结果仍全量进历史。老化后 200KB 只在当前回合可见，上限值不再关键。**不动**（范围闸门，见 §七）。 |
| LLM 摘要替代占位 | 引入额外模型调用与「摘要丢判断依据」的风险（D62 spec 已论证同类问题）；占位 + 重读足够（§五 可重放性表）。 |
| 直接改写 `session.jsonl`（落盘即老化） | 破坏日志即事实的约定；全文丢失后无法审计「模型当时看到了什么」。老化必须是投影。 |

## 二、前置条件

- 数据盘挂载（`/Volumes/Samsung T7`），`data/` 软链可达；不挂载也能施工（单测不依赖数据盘），但 §八 的真实日志回放验证需要盘。
- 基线：`uv run pytest` 全绿才开工（执行代理守则）。

## 三、设计

### 3.1 规则

对要发送的消息列表 `convo`（`llm.py::run_tool_loop` 内的工作副本）做投影：

1. 找边界：从后往前找最后一条 `role == "user"` 且**不带 wrapup 标记**（§3.4）的消息，记为当前回合起点；
2. 起点**之后**的所有消息原样保留（当前回合内的工具结果模型正在用）；
3. 起点**之前**的每条 `role == "tool"` 消息：若 `len(content) > AGED_TOOL_MAX_CHARS`，替换 `content` 为占位串（§3.2）；其余消息原样；
4. 纯函数：返回新列表，只复制被替换的消息，**不 mutate 入参**；同一输入恒得同一输出；
5. 函数签名带 `exempt_last_turn: bool = True`；发送路径（`_chat`）与摘要器路径共用同一次投影（compact() 开头一次投影三处共用，§四），均用默认 `True`——正常形态下被压缩的 region 全在最后一条 user 之前，等效全老化；差异只在「当前回合被 max_messages 劈开」的边角，此时当回合大结果留尾部按原列表全文保留，与发送口径一致（施工实录有记录）。

**wrapup 边界（红队 F1）**：`_wrapup` 提交的收尾指令是 `role == "user"` 且紧跟 `_chat(tool_choice="none")`——若不豁免，收尾总结会在「刚拿到的大结果」被老化、且禁止重读工具的双重degradation下生成。修法和标记先例对齐（`_TIER_KEY` / `COMPACT_KEY`）：收尾指令消息带 `_WRAPUP_KEY` 内部字段，进请求体前由既有 `_INTERNAL_KEYS` 剥离机制删除；老化的边界扫描跳过带此标记的消息。

**单调性**：回合边界只向前移，一条消息一旦被老化就永远老化——前缀在「某条消息跨过回合边界」时变化一次，之后稳定。cache 代价（红队 Q9 核实为真：gpt-4o 自动前缀缓存 ≥1024 token、Gemini 隐式缓存）：老化使失效点之前的前缀在该轮作废一次，换取之后每轮少重发几十 k token；伪恋期实测那条 63.7k 字符结果每轮重发 ≈47k token，一次失效换全程净省。

### 3.2 占位串格式（红队 F4 修订：按工具分文案）

只读工具（`read_artifact` / `search_notes` / `web_search` / `web_fetch` / `crawl` / `browser` / `list_episodes` / `read_status`）：

```
[工具结果已老化｜原 {n} 字符｜需要全文请重新调用 {tool}]
{head}
```

`run_pipeline`（执行型，重调 = 重新执行命令，tts run 实测单次可达 1500s，判重表是回合级的挡不住跨回合重放）：

```
[工具结果已老化｜原 {n} 字符｜该结果来自 run_pipeline，需要最新状态请调 read_status]
{head}
```

- 工具名从消息链取：扫描 assistant 消息的 `tool_calls`，建 `tool_call_id → name` 映射；查不到（修复插入的合成结果等）落通用文案「需要全文请重新调用同一工具」。
- `{n}` = 原字符数；`{head}` = 原文前 300 字符。300 的依据（红队复测确认）：现存 7 类工具结果全是 `{"ok":…, "result":{…}}` JSON，`path`/`query`/`argv` 都在前 ~120 字符内，300 盖住身份区；再大就失去瘦身意义。
- **安全性论证（红队 F3 修订，不许再写成「日志已脱敏」）**：D52 的脱敏（`_tool_message` 调 `scrub_restricted`）只覆盖 D52 提交（2026-10-08 12:24Z）之后写入的日志；存量日志里有未脱敏的受限字面量（实证：董香二期 seq=288，tool 正文含 `03-audio/manifest.json`）。本机制的安全性**不依赖**日志干净，靠的是两条腿：① 老化只删不增，不可能给请求体引入新内容；② `assert_egress_boundary` 对最终 payload 全量照拦，方向 fail-closed。注意后果分层：占位 head 若带进受限字面量（pre-D52 日志可能发生），后果是**整回合被拦回滚**（可用性），不是出网（机密性）。

### 3.3 常量

| 常量 | 值 | 取值理由（判据 7：不许拍脑袋） |
|---|---|---|
| `AGED_TOOL_MAX_CHARS` | `4000` | 2026-10-10 全量 `session.jsonl` 实测（**口径：含全部 sid、排除回滚消息**；红队独立复算一致）：91 条 tool 消息，中位数 319 字符、P90 = 2,666；>4000 的恰 5 条（5.5%）占 67.8% 字符体量。4000 落在 P90 与 P99 之间，恰好只切顶部 5%，常规检索/状态结果（≤3k）一律不碰。（初版 spec 写 92 条/376/2,732，是误把 1 条回滚消息计入；结论不变，口径以本行为准。） |
| 占位 head 长度 | `300` | 见 §3.2。 |

### 3.4 内部标记（红队 F1 修法）

- `llm.py` 新增 `_WRAPUP_KEY = "_ava_wrapup"`，加入 `_INTERNAL_KEYS`（剥离机制现成，`chat_complete` 前的 wire 函数已按此集合删除内部字段）；
- `_wrapup` 里 `control.commit` 的收尾指令消息带 `_WRAPUP_KEY: True`；
- `aging.py` 边界扫描跳过带 `_WRAPUP_KEY` 的消息；
- 模型看不见标记、也仿写不出（同 D62 §4.4 对 COMPACT_KEY 的论证）。

## 四、改动清单

| 文件 | 锚点（引原文，不给行号） | 改动 |
|---|---|---|
| `pipeline/agent/aging.py`（新建） | — | `AGED_TOOL_MAX_CHARS = 4_000`、`AGE_HEAD_CHARS = 300`（注释带 §3.3 依据）、`age_tool_results(messages, *, exempt_last_turn=True) -> list` 纯函数（§3.1）、`_placeholder(...) -> str`（§3.2 分文案） |
| `pipeline/agent/llm.py` | `_chat` 内 `prompt_chars = sum(...)` 与 `reply = chat_complete(convo,` | 发送与字符统计改用 `age_tool_results(convo)` 的返回值；`prompt_chars` 口径同步切到投影后（影响说明见 §七）；import 新模块 |
| `pipeline/agent/llm.py` | `_INTERNAL_KEYS = frozenset({_TIER_KEY, COMPACT_KEY})`、`_wrapup` 的 `control.commit({"role": "user", ...}, "wrapup_instruction")` | `_WRAPUP_KEY` 入集合；收尾指令消息带标记（§3.4） |
| `pipeline/agent/session.py` | `compact()` 内 `cut = cp.choose_cut(messages, token_budget=budget, …)`、`region = messages[1:cut] if cut > 1 else []`、`cp.build_summary_request(region)`、`if cp.is_bloated(text, region):` | **红队 F2（三处口径，人拍板 2026-10-10）**：`compact()` 开头投影一次 `aged = aging.age_tool_results(messages)`，三个消费者共用——choose_cut(aged)（投影保序保长，切口下标回原列表取 tail/写史）、build_summary_request 的 region 取 `aged[1:cut]`、is_bloated 的 region 实参同换 aged。tail / compacted_history / 出网断言仍用原列表（落盘与断言对象不变）。放置已定：session.py 一次投影，不进 compact.py（choose_cut/is_bloated 的生产调用方只有 session.py 这一处，已 grep 确认；放 compact.py 内部得投影两次或改返回值，都不干净）。**is_bloated 护栏语义修正**：护栏语义是「压缩后发送量没省」，尺必须是老化后的——用未老化尺，「region 是几条 63k 大结果、老化后仅 ~1.75k、摘要 3–4k」的典型形态会误判「通过」，方向反了。连带效应：对已被老化压扁的历史，手动 /compact 可能被护栏拒——该判断在发送口径下正确，但 fail 文案须按老化口径说人话，改「压缩没有意义」为含老化说明的文案（方向：「按实际发送口径（大体积工具结果已老化占位），摘要省不出更多，历史未改动」），否则用户看着大历史被告知「压缩没有意义」会以为是 bug |
| `tests/test_agent_aging.py`（新建） | — | §六 用例 |

F2 的两个故障机理（留档，修后不复现）：(a) `choose_cut` 用未老化字符累加，一条老化后只占 ~350 字符的 63.7k 结果在预算里仍按 63.7k 计，把本该保留的近期小消息挤出尾部；(b) `build_summary_request` 发未老化全文，老化机制让「原始体量 ≫ 发送体量」成为常态后，老会话手动 /compact 的 region 换算 token 可超摘要器窗口 → HTTP 400 →「摘要器调用失败，历史未改动」，用户在最需要压缩时失去逃生口。

## 五、可重放性核对（占位「重新调用」是否成立）

| 工具 | 结果来源 | 可重放 |
|---|---|---|
| `read_artifact` / `search_notes` / `read_status` / `list_episodes` | 磁盘文件 | ✅ 重读即得 |
| `run_pipeline` 只读子命令（status / check_script 等） | 重跑 | ✅ |
| `run_pipeline` 执行型子命令（tts / render / clips 等） | 重跑 = 重执行（实测单次可达 1500s） | ❌ 故占位指向 `read_status`（§3.2），不指引重调 |
| `write_*` / `acquire_propose` | 确认回执 | 结果极小（实测 max 2.7k），**不会被老化** |
| `web_*` / `crawl` / `browser` | 网络 | ⚠️ 页面可能已变。红队复测：**全部现存日志中 web 四件套样本为 0**——「不豁免」与「豁免」都没有实证基础。维持不豁免，理由是机制统一、无可豁免对象，而非有证据支持（待人定 ② 留口子） |

## 六、测试要求

期望值先在实现上跑通再写断言；写完做变异检验。

用例清单：

1. **阈值边界**：content 恰为 4000 不动、4001 老化；
2. **当前回合豁免**：最后一条 user 之后的 tool 结果（哪怕 100k 字符）原样保留；
3. **wrapup 边界（红队 F1 用例）**：收尾指令（带 `_WRAPUP_KEY` 的 user 消息）之后无其他消息时，本回合的大 tool 结果**不**被老化；wrapup 再往后还有真 user 消息时，按真 user 划界；
4. **单调老化**：上一回合的大结果被老化、小结果不动；同一列表两次投影输出逐字节相等；
5. **不变异入参**：投影后原列表每个对象的 `content` 与调用前逐字节相等（`copy.deepcopy` 快照比对）；
6. **占位分文案（红队 F4 用例）**：`run_pipeline` 的占位指向 `read_status`、不含「重新调用」；只读工具含工具名；tool_call_id 查不到名字时落通用文案；
7. **非 tool 消息免疫**：大正文的 user / assistant 消息原样保留（含 assistant 的 tool_calls 参数——本 spec 明确不动它）；
8. **无 user 消息**：整条列表视为「当前回合之前」，全部按规则处理（防御分支）；
9. **pre-D52 内容（红队 F3 用例）**：含受限字面量的大 tool 结果被老化后，占位 head 取自原文前 300 字符（不额外脱敏——脱敏不是老化的职责，断言兜底）；断言层行为不变（断言是 llm 层既有机制，本用例只锁定「老化不引入新内容」）；
10. **组合：压缩投影之后**（D62）：先经 `rebuild_messages`（含 compaction 事件）重建、再老化，不炸；compaction 的摘要与重注入卡（带 `COMPACT_KEY` 的 user 消息）能正确充当边界；
11. **compact 口径（红队 F2 用例）**：构造「一条 63.7k 老化结果 + 多条近期小消息」的历史——choose_cut 按老化后尺寸计的切口保留的近期消息多于按未老化计的切口；build_summary_request 的入参 region 里大结果已是占位形态；
13. **is_bloated 口径（F2 第三处用例）**：「region 全是大体积 tool 结果、老化后仅 ~1/40」的历史，is_bloated 按老化后尺寸判（摘要 3–4k vs 老化后 region ~2k → 判膨胀、拒压缩），且 fail 文案含老化口径说明；按未老化尺判则放行——对应用例须能区分两把尺；
14. **与 llm 集成**：`run_tool_loop` 桩两回合脚本——第一回合塞 64k 假 tool 结果，断言第二次 `chat_complete` 收到的消息里该结果已被占位替换、`session.jsonl` 落盘原文一字未动；再触发一次 `_wrapup`（模拟中断），断言收尾请求里该结果**仍是全文**（F1 回归锁）。

变异检验（至少）：① 阈值 ±1；② 老化范围扩到当前回合；③ 边界扫描不跳 wrapup（杀 F1）；④ 函数 mutate 入参；⑤ 占位丢 head；⑥ 占位不分文案；⑦ choose_cut 用回未老化尺寸（杀 F2a）；⑧ 摘要器 region 不过投影（杀 F2b）；⑨ is_bloated 用回未老化 region（杀 F2 第三处）。每条须被对应用例杀死。

## 七、明确不做的事（范围闸门）

1. 不改 `session.jsonl` 落盘格式与任何落盘内容（老化是请求时投影）；
2. 不改 `migrate_idea_session` / Spec 18 迁移链路（机制自动覆盖，见 §一）；
3. 不动 `MAX_READ_BYTES` 与 web 三件套的 30k 上限；
4. **D62 的改动收窄为**：session.py `compact()` 内三处口径（choose_cut / build_summary_request / is_bloated，§四）+ bloated fail 文案；compact.py 零改动，压缩触发条件、摘要格式、`compact.json` 一律不动；
5. 不改桌面端读数与 UI、不改出网断言规则（Spec 16）；`session.py` 的改动仅限 §四 的 compact() 投影与调用点；
6. 不做 LLM 摘要、不按工具类型设差异阈值；
7. **残留病根（红队范围提示，留档不治）**：assistant 消息的 tool_calls 参数不老化——写稿重的会话里整篇稿子随每轮重发；伪恋期实测 args 仅 1.8k（调研型），不构成当前病灶。触发条件：出现写稿重会话参数占上下文 >20% 的实测，再另立案。

### 读数口径变化说明（红队 Q11，如实披露）

`prompt_chars` 回落读数随本 spec 从「投影前」切成「投影后」。老会话恢复后会出现一次性落差（如「上次 111k → 下轮实测 ~35k」），方向是变小、标签有「（上次）」后缀，不构成假读数；不改桌面端。

### 待人定（2026-10-10 人拍板：三项全采纳；F2 放置定 session.py）

| 项 | 裁决 | 说明 |
|---|---|---|
| ① `AGED_TOOL_MAX_CHARS` = 4000 | **采纳** | 人独立复算与 §3.3 口径一致（91 条、中位 319、P90=2,666、>4000 恰 5 条占 67.8%），切点成立 |
| ② web 类结果不豁免 | **采纳** | 理由「机制统一、无可豁免对象」是零样本下唯一诚实的写法 |
| ③ F2 修法 | **采纳，补出第三处 is_bloated**（初稿漏） | choose_cut / build_summary_request / is_bloated 三处同换老化后口径；放置定 session.py `compact()` 开头投影一次三处共用（不进 compact.py：生产调用方只有 session.py 一处，内部投影得做两次）；连带效应已写进 §四（护栏拒压缩的 fail 文案按老化口径说人话） |

## 八、验证命令

```bash
uv run pytest tests/test_agent_aging.py -v     # 新用例
uv run pytest                                   # 全量回归（执行代理守则：贴结果）
# 真实日志回放（需数据盘）：
uv run python - <<'EOF'
import json
from pipeline.agent import aging
path = 'data/episodes/2026-10-10-伪恋-橘万里花的进攻哲学/session.jsonl'
# 不走 rebuild_messages：本验证只量「老化能省多少字符」，回滚丢弃不影响量级
msgs = [json.loads(l)['message'] for l in open(path, 'rb')
        if l.strip() and json.loads(l).get('k') == 'msg']
aged = aging.age_tool_results(msgs)
before = sum(len(str(m.get('content') or '')) for m in msgs)
after = sum(len(str(m.get('content') or '')) for m in aged)
print(f'before={before} after={after} saved={before-after}')
# 期望：saved ≥ 90,000（伪恋期 >4000 的大头有三条：63,698 / 22,103 / 13,326，
# 均不在最后一回合；红队按 §3.1 规则模拟回放实测 saved = 98,122）
EOF
```

回放脚本是一次性验证、不进仓（N23 纪律），输出贴进施工汇报。

## 九、完成判定（逐项打勾）

- [x] `age_tool_results` 纯函数按 §3.1–3.3 实现，常量注释带实测依据（口径：排除回滚消息）
- [x] `_WRAPUP_KEY` 入 `_INTERNAL_KEYS`，wrapup 消息带标记，边界扫描跳过（§3.4）
- [x] `_chat` 唯一会话发送点接上（红队 Q12 已核实：`chat_complete` 全仓 4 个调用点，其余 3 个——压缩摘要器、adversarial、notes_review——不走 run_tool_loop，行为不变或恒等），`prompt_chars` 口径同步（投影后）
- [x] compact 三处口径改完（choose_cut 切口、摘要器 region、is_bloated 尺子均按老化后；放置 = session.py `compact()` 开头一次投影），bloated fail 文案含老化口径说明；compact.py、压缩触发与摘要格式未动
- [x] §六 14 条用例全绿；9 条变异全被杀
- [x] `uv run pytest` 全量绿
- [x] 伪恋期真实日志回放 `saved ≥ 90,000`，且日志文件本身零改动（投影不动盘）
- [x] 待人定 ①②③ 三项已拍板回填（2026-10-10 全采纳）
- [x] issues 表 D65 行更新施工状态

**施工实录（2026-10-10）**：`pipeline/agent/aging.py` 新建；`llm.py` 三处（`_WRAPUP_KEY` 入 `_INTERNAL_KEYS`、`_chat` 接投影、`prompt_chars` 投影后口径、wrapup 消息带标记）；`session.py::compact()` 开头一次投影三处共用 + bloated 文案改老化口径。测试 `tests/test_agent_aging.py` 14 条（spec 清单 13 项 + F2b 正对用例 11b）。变异 9 条逐条注坏均被对应用例杀死（M7 由 11 杀、M8 由 11b 杀、M9 由 13 杀）。全量 2519 绿。伪恋期回放 before=142,553 after=44,461 **saved=98,092**（≥90,000 达标；红队预测 98,122，差 30 字符为占位文案字面差）。§3.1 规则 5 与 §四 的张力按 §四 拍板代码落地：compact() 一次投影用默认 `exempt_last_turn=True`（region 即 `aged[1:cut]`，正常形态下 region 全在最后 user 之前，等效全老化；当回合大结果留尾部按原列表全文保留，与 `_chat` 发送口径一致）。
