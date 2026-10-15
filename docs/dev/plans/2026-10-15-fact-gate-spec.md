# D99 事实关卡：抓取留档 + 结构化事实条目 + 写入时机器校验

日期：2026-10-15　状态：**PR-1 / PR-2 已施工·机检通过；PR-3 未动**（§九 裁决：② 人定跨会话也算；① ③ 人未表态，按推荐执行，可改）
附件：[`2026-10-15-fact-gate-spec/`](2026-10-15-fact-gate-spec/)（实测脚本，路径全取参数）

## 一、目标与对应 issue

- **D99**：事实文件没有关卡。AVA 把没打开过的出处标「一手」、凭记忆补事实、改写动机，还汇报「核心事实均已落实一手文献支撑」。错误四类：① 没读原文标一手 ② 凭记忆补 ③ 名单 / 数字与原文不符 ④ 动机因果被改写。
- **人定方向**（2026-10-14）：靠系统设计在源头避免——事实条目结构化（说法 / URL / 原文摘录 / 级别），写入时由系统校验「URL 本会话确实抓过」「摘录确实出现在抓到的原文里」，过不了的写不进或强制标「未核实」；「一手」不由模型自填；抓取结果留可引用的记录；第 ④ 类交独立核对（判据 11）。
- **人追加**（2026-10-15）：合适的话可以加新工具，怎样合理高效就怎样做。据此本图选**新工具 `fact_add`**（理由见 §四.1）。同日人又裁决工具表取消硬上限、改软约束（ADR-0032），所以不必为占位另立 ADR，`fact_add` 的出处就是本施工图。
- 相关：ADR-0021（网络工具内化）、ADR-0032（工具表软约束，取代 ADR-0025 / 0031 的封顶）、ADR-0029 / ADR-0030（bash 与硬危险清单）、D54（剧情事实出处规程）、判据 5 / 11。

## 二、实测（2026-10-15，不凭想象定规则）

材料：`data/episodes/supercell-传奇企划志/01-席卷平成的同人雷暴/01-facts-2007-2009.md`（第三版，人工核对过）。用 **AVA 自己的** `fetch_web` 与 `crawl_page` 重新抓其中全部 16 个出处 URL（fetch 15/16 成功，natalie.mu 静态 405，crawl 16/16），存下不截断的全文，再对 65 条「原文摘录」逐级归一化做子串比对。脚本：附件 `fetch_sources.py` → `match_levels.py` → `negative_cases.py`。

### 2.1 分级命中（每条取能命中的最低一级）

| 级 | 归一化（两边同做） | fetch 文本 | crawl markdown |
|---|---|---|---|
| L0 | 原样 | 54 | 55 |
| L1 | 空白折成一个 | — | 1 |
| L2 | 去掉全部空白 | 1 | — |
| L4 | + NFKC + 去 markdown 标记（`**`、`[文](链接)`、反斜杠） | 2 | — |
| L4q | + 引号括号归一（`『』“”"'‘’` → `「」`） | 6 | 6 |
| L5 | 只留字母、数字、组合附标（标点全丢） | 2 | 3 |
| 未命中 | | 0 | 0 |

每一级为什么需要（全部来自真实样本）：

- **空白**：`_TextExtractor` 把每个文本节点用空格拼起来，句中有 `<a>` / `<b>` 就多出空格；crawl 在块之间是换行。
- **markdown**：摘录作者自己加了粗体（`**5曲連続でミリオン再生達成**`、`**そこに、デPは…**`）；crawl 侧原文里有 `[1000万再生](…)` 链接。
- **引号括号**：6 条摘录把原文的 `「」` 写成了 `『』`（三轮采访 4 条、note #17 1 条、ORICON 2008 1 条）。这是抄写时的改动，但不改变说法。
- **只到 L5 才命中的**：`huke：いや…`（摘录加了说话人冒号，原文说话人是单独的粗体节点）、`知り合いの中で…`（原文句中有脚注号 `【※】`，摘录删了）、atwiki 的 `1000万再生*2`（脚注号）。这三条都是**抄写时动过原文**，内容没错，但已经不是逐字。

### 2.2 截断

现行 `max_chars = 30000`。ORICON 2009 存档的 crawl 全文 50,817 字、ORICON 2008 32,395 字，超过上限；但本样本 65 条摘录的命中位置都在前 30,000 字内。**结论**：留档必须存截断前的全文（否则「摘录在原文第 3 万字之后」会被误判为编造），LLM 看到的仍是截断版。

### 2.3 错页归属（关卡第一次实跑就抓到的真错）

`知り合いの中でこういう話に乗ってくれそうな人を考えて、hukeさんとredjuiceさんを…` 在事实文件里挂在 `https://originalnews.nico/20667`（采访第 1 页）下，实际只出现在 `/20667/2`（第 2 页）。第三版是人工逐条核对过的，这个错仍然漏了——**按「摘录必须出现在它声称的那个 URL 的抓取记录里」比对，能机械地抓出来**。

### 2.4 反向检验：编造与拼接会不会放过（L5 最宽松那一级）

| 样本 | 来源 | 结果 |
|---|---|---|
| 「放送部だった自分はドラクエの曲を流した」 | 第二版编造（note #19 勘误） | 拦下 |
| 「山田先生が血相を変えて」 | 同上 | 拦下 |
| 「誰だ！ こんなふざけた曲を流したのは！」 | 同上 | 拦下 |
| 「元々あの絵には設定はないんです」 | 第二版把记者提问与回答拼成一句（ASCII 勘误） | 拦下 |
| 「集英社の編集部に止められたので、別名義で描き始めたんですよ。」 | 仿第一版「躲编辑」的动机改写 | 拦下 |
| 「機械に歌わせるからこそ…書けるのだと思います」 | 事实文件列在「未核实」 | **命中**：这句其实就在 ORICON 2008 原文里，可以从未核实清单移出（属卷一产物，本图不改，报人） |

### 2.5 由此定的比对规则

- **逐字通过** = L4q 命中：NFKC → 去 markdown 标记 → 去全部空白 → 引号括号归一。被归一化掉的只有空白、排版标记和引号形状——判据 5 问「被归一化掉的部分是不是缺陷所在」：D99 的缺陷是编造、拼接、错页、改数字，这四样都不在被抹掉的字符里。
- **近似** = 只有 L5 命中：条目照写，但级别强制为「待核」、卡面列出与原文的逐字差异。不判通过，因为 L5 丢掉了全部标点，`？` 与 `。` 的差别（问句被写成陈述）就在被丢掉的部分里。
- **未命中** = 写不进为「一手 / 二手」，只能以「未核实」写入（保留说法，供人追查）。

## 三、改动清单

> 以下锚点引原文片段，不给行号。分三个 PR，各自一个 commit。

### PR-1 抓取留档（不改任何行为，只多落盘）

1. **新文件 `pipeline/agent/fetch_log.py`**：
   - `record(*, url, final_url, kind, text, sid, root) -> str`：全文写入 `data/library/fetch-log/<sha256(final_url)[:16]>/<UTC 时间戳>-<kind>.txt`，再往 `data/library/fetch-log/index.jsonl` 追加一行 `{"id", "url", "final_url", "kind", "sid", "ts", "chars", "sha256", "file"}`；返回记录 id。`kind ∈ fetch | crawl | browser`。写用 `paths.atomic_write`；index 追加用 `fcntl` 锁（照 `candidates.py` 的 `candidates.lock` 写法）。
   - `lookup(url, *, sid=None, root) -> list[Record]`：按 `url` 或 `final_url` 精确匹配（去掉 fragment、末尾 `/` 归一），`sid` 给了就只回本会话的。
   - `data/` 不可达立刻报错，不自动建（AGENTS.md 五节）。
2. **`pipeline/agent/web.py::fetch_web`**：增关键字参数 `keep_full: bool = False`；为真时返回值多一个 `full_text`（`_scrub` + `_redact_all` 之后、`max_fetch_chars` 截断之前的文本）。锚点：`text = _redact_all(_scrub(extractor.get_text()), cfg.secret_values)` 之后、`if len(text) > cfg.max_fetch_chars:` 之前。
3. **`pipeline/agent/web_crawl.py::crawl_page`**：同上，锚点 `scrubbed_md = _scrub(raw_md)` 之后。
4. **`pipeline/agent/web_browser.py`** 的 `extract_text`：同上（先读码确认它的截断点，对不上就停下报告）。
5. **`pipeline/agent/tools.py`**：`_tool_web_fetch` / `_tool_crawl` / `_tool_browser`（仅 `extract_text`）以 `keep_full=True` 调用，`full_text` 交 `fetch_log.record` 后 **pop 掉**，返回值加 `"fetch_record": <id>`。`ToolContext` 增字段 `session_id: str | None = None`，由 `session.py` 构造处（`return ToolContext(scope=scope, episode_dir=self._ep_dir, root=root,`）填 `self.sid`。
6. **出网**：留档只写本地盘，不经 LLM，不改 egress 断言。全文已过 `_scrub` / `_redact_all`，与 LLM 已见内容同口径脱敏。

### PR-2 `fact_add` 工具与事实存储

1. **为什么不复用现有工具**（ADR-0032 软约束要求写在出处里；schema 的 `adr` 字段填 `plans/2026-10-15-fact-gate-spec.md`）：
   - `run_pipeline` 只能传 argv：一条摘录动辄 100–300 字日文，带引号、括号、换行，经 shell 风格参数传递要转义，模型写错一个引号整条报错重试；`write_episode_file` 是整文件 / 片段替换，管不到「每条一校验」；`bash` 正是 D99 事件里绕过一切的那条路。
   - 单独一个工具，参数是结构化 JSON（说法、URL、摘录数组），校验结果逐条回给模型，模型能当场改正。
   - 读写边界：只写本会话所属卷 / 期 / 企划目录下的 `facts.jsonl` 与生成的阅读版；只读 `data/library/fetch-log/`；不出网。
2. **事实存储**：每个卷 / 期目录一份 `facts.jsonl`（机器读写的权威），一行一条：
   ```json
   {"id": "F012", "claim": "说法（中文）", "url": "https://…", "excerpts": ["原文逐字…"],
    "level": "一手|二手|待核|未核实", "level_by": "human|system", "check": {"record": "<fetch_record id>", "match": "L0|L4q|L5|none", "diff": […]},
    "topic": "节名", "ts": "…", "sid": "…"}
   ```
   `01-facts-*.md` 改为由 `python -m pipeline.facts render <目录>` 从 `facts.jsonl` 生成的阅读版（顶部写「本文件由 facts.jsonl 生成，手改无效」）。
3. **新文件 `pipeline/facts.py`**（纯函数 + CLI）：
   - `normalize(s, level)`：§二.5 的 L4q 与 L5 两档；`check_excerpt(excerpt, text) -> (match, diff)`。
   - `add(entry, *, sid, root) -> dict`：查 `fetch_log.lookup(url, sid=sid)`；没有记录 → 只能 `未核实`（返回值写明「本会话没抓过这个 URL，先 web_fetch / crawl」）；每条摘录在该 URL 任一条本会话记录里 L4q 命中 → 可写；任一条只有 L5 命中 → 级别强制 `待核` 并附 diff；未命中 → 拒写为一手 / 二手，回原文里最接近的片段供模型对照。
   - **级别**：模型只能提 `proposed_level`，落盘的 `level` 由系统定：机检通过的条目先落 `待核`，`level_by: "system"`；升为 `一手 / 二手` 只能经人（见 §九 裁决 ①）。
   - CLI：`check <目录>`（对 `facts.jsonl` 每条按留档**重做**校验——bash 改坏的条目在这里现形）、`render <目录>`、`promote <目录> <F号> <级别>`（人用；进 `HUMAN_GATED_COMMANDS`）。
4. **`pipeline/agent/tools.py`**：新 schema `fact_add`（`side_effect: True`，免卡——写的是本卷事实库，ADR-0030 只拦硬危险；拒写由校验本身完成），参数 `entries: [{claim, url, excerpts[], proposed_level, topic}]`，一次可多条、逐条回结果；执行器调 `pipeline.facts.add`，写完自动 `render`。会话类型：卷 / 期 / 企划会话均可（企划会话写企划母目录的 `facts.jsonl`）。
5. **bash 硬危险**（`status_card._BASH_DANGER_RULES`）：加一条——写入形态（复用 `_WRITE_FORM`）命中 `facts\.jsonl|fetch-log/` 即弹卡，标 `[事实库] 绕过 fact_add 写入`。理由：D99 的事件就是 AVA 用 bash heredoc 写事实文件。
6. **提示词**：`config/agent/scopes/project.md` 与 `production.md` 写事实的段落改为「只用 `fact_add`；`01-facts-*.md` 是生成的阅读版」；去掉一切「标一手」的指示。工具描述里写清三种回执（通过 / 待核 / 拒写）各该怎么做。

### PR-3 独立核对（第 ④ 类）接入现有 02.8

1. `pipeline/adversarial.py`（先读码确认现有入口与输入；对不上停下报告）增一种输入：`facts.jsonl` + 对应留档原文，**不给写稿对话**。核对员只看「说法」是否被「摘录」支持（动机、因果、主语、时间是否被改写），输出逐条 支持 / 部分 / 不支持 + 理由。
2. 报告必须含随机抽样通过清单、终审裁决存档（判据 11），照 02.8 现有格式。
3. 「不支持」的条目 `level` 回落 `待核`，并在 `render` 的阅读版里标红。

## 四、新常量 / 新判据的取值理由

1. **新工具而不是复用**：见 PR-2 第 1 条。人 2026-10-15 明确允许加工具，并取消了工具表硬上限（ADR-0032）。
2. **L4q 为逐字通过线**：§二.1 实测 65 条里 L0–L4q 覆盖 fetch 63 / crawl 62；剩下 2–3 条全是抄写时动过原文（加冒号、删脚注号），降为「待核」而不是放过。
3. **L5 只做「近似」不做通过**：判据 5——L5 丢掉的标点里有 `？`/`。`，问句与陈述的差别就在那里。
4. **按 URL 精确归属，不在全部留档里找**：§二.3 错页那条，在全部页面里找会放过。
5. **「本会话」而不是「任何时候」抓过**：人定方向原话。跨会话复用见 §九 ②。
6. **留档存截断前全文**：§二.2，ORICON 2009 crawl 全文 50,817 字 > 30,000 上限。
7. **不设摘录最短长度**：样本里最短的真摘录「全校生徒の前で。」只有 8 字，且必须落在指定 URL 的原文里，巧合命中的代价是「把真话判成真话」，不是放过编造。

## 五、测试要求

期望值先在实现上跑通、想明白再写进断言；写完做变异检验，贴「几处变异、几处被抓」。

- `tests/test_fetch_log.py`：record / lookup 往返；fragment 与末尾斜杠归一；`sid` 过滤；data/ 不可达报错；`full_text` 不进工具返回值（断言 `execute_tool("web_fetch", …)` 的返回里没有 `full_text`、有 `fetch_record`）。
- `tests/test_facts.py`：**用 §二 的真实样本做夹具**（从留档里截取几段原文与对应摘录写进测试，不依赖 data/）——L0 / L2 / L4 / L4q 各一条通过；`huke：`、`【※】` 两条落「待核」且 diff 指出差异；§二.4 的五条编造 / 拼接全部拒写；错页那条挂 `/20667` 拒写、挂 `/20667/2` 通过；没抓过的 URL 只能写未核实；模型传 `proposed_level: "一手"` 落盘仍是 `待核`；`check` 能抓出 bash 手改过的条目。
- `tests/test_agent_web.py::test_every_tool_has_existing_adr`：`fact_add` 的出处指向本施工图，机检通过。
- `status_card`：`cat >> facts.jsonl`、`tee data/library/fetch-log/x` 弹卡，`cat facts.jsonl` 不弹。
- 变异点（至少）：去掉引号归一、L5 当通过、按全部留档找而不按 URL、忽略 sid、`proposed_level` 直接落盘、`full_text` 不 pop、bash 规则漏 `tee`。

## 六、验证命令

```bash
uv run pytest                                            # 全绿（含 ruff 门禁）
uv run pytest tests/test_fetch_log.py tests/test_facts.py -q
# 实测复现（附件脚本，scratch 目录输出）
uv run python docs/dev/plans/2026-10-15-fact-gate-spec/fetch_sources.py <facts.md> <scratch>/pages
python3 -I docs/dev/plans/2026-10-15-fact-gate-spec/match_levels.py <facts.md> <scratch>/pages
cd desktop && npx tsc --noEmit -p .                      # 若 PR-2 动了卡面渲染
```

## 七、明确不做的事

- 不改卷一现有事实文件（产物；迁移成 `facts.jsonl` 由人决定，见 §九 ③）。
- 不做跨源内容级去重、不做「同一事实多源交叉」打分。
- 不让机器判「说法是否被摘录支持」——那是第 ④ 类，交 PR-3 的独立核对，不包装成机器判据。
- 不改 `max_fetch_chars`（LLM 看到的长度不变）。
- 不拦 bash 读事实库。

## 八、完成判定

- [x] PR-1：三种抓取都留档；工具返回带 `fetch_record`、不带全文（`6364c26`；测试 16 条，变异 13/13）
- [x] PR-2（机检部分）：`fact_add` 免卡、目录取自宿主绑定；§二 真实样本夹具全部按 §二.5 判；bash 写 `facts.jsonl` / `fetch-log/` 弹卡、`facts promote` 进人工门禁；提示词已改（测试 41 条，变异 19/20，存活 1 处为等价变异：企划会话的 `episode_dir` 与 `project_dir` 同为企划目录）。阅读版文件名定为 `facts.md`（不覆盖人工写的 `01-facts-*.md`）
- [ ] PR-2（原清单）：`fact_add` 三种回执实测；§二 样本夹具全部按 §二.5 判；bash 写事实库弹卡；提示词已改
- [ ] PR-3：02.8 能对 `facts.jsonl` 出带抽样通过清单的报告与终审
- [ ] issues D99 行、plans README 状态同步
- [ ] **人**：在一个真实卷会话里让 AVA 用 `fact_add` 写 5 条以上事实，看三种回执是否好懂（人做、人勾）

## 九、裁决（2026-10-15）

> ② 人定「跨会话也算」。① ③ 人没表态，按下面的推荐施工：① 取 (a)，③ 先不迁。人改主意时：① 换 (b) 要加一张一手来源表与自动定级；③ 卷一迁移是做片工作，等 supercell 重启。

1. **「一手」怎么来**：(a) **推荐**：系统只落「待核」，人用 `facts promote`（或桌面端卡片）逐条 / 按 URL 批量升级——人只需判断「这个 URL 算不算一手」，不用重读摘录；(b) 企划目录维护一张「一手来源表」（官网域名、当事人 note 账号），命中自动标一手，其余待核——省人，但表本身要人维护，且「当时媒体的存档」很难按域名判。
2. **跨会话**：(a) **推荐**：只认本会话的抓取记录（人定原话），换会话续写要重新抓一次；(b) 同一企划的任何会话都算——省流量，但「上周抓过」不能证明这次真的读了。
3. **卷一现有事实文件**：(a) 不迁移，留作人工版；(b) 施工后用 `fact_add` 由 AVA 重录一遍（顺带验证工具）。我倾向 (b)，但那是做片工作，等 supercell 重启再说。
