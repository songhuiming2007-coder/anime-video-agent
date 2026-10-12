# D80：对话区第二轮打磨（会话标题 · 工具行 · 运行动感 · 回合折叠）施工 spec

> 对应 issue：**D80**（`docs/dev/issues/README.md`）。来源：2026-10-12 需求草案《AVA-UI优化需求》（人桌面，未入仓；本 spec 已吸收其全部有效内容，草案不再是依据）。
> 相关：Spec 14（视觉设计系统，第一轮学 ZCode 的静态骨架）、D39（布局，预览默认收起）、D45 / D66（会话管理、回收站）、D46（对话可读性、运行指示行）、D71（作业条）、D76（复制命令）、D78 / ADR-0030（审批只拦硬危险、回车即批）、D79（运行中排队）、S9-R1（失败原文逐字）、TG-4′。
> 参照物：`github.com/zai-org/ZCode` 浅克隆 `aac4755`（2026-10-10），**读源码，未运行**。
> **本 spec 含 UI，plans/README 的 UI 五条规则生效**：场景见 §一；R2 / R3 / R4 的视觉由人从截图方案里选（§四 PR-B，规则 4）；验收第 1 条是真实任务、人勾（§九）。

---

## 一、用户场景与目标（UI 规则 1）

- **谁**：人本人，一期做下来在桌面端和 ava 连续对话 1–3 小时。
- **窗口**：1440×900 与 1280×800。侧栏、对话区常开，预览默认收起（D39 方案 B）。
- **连续做什么**：发一句需求 → ava 连跑 5–20 个工具（多为 bash、run_pipeline、read_artifact）→ 中途可能弹 1 张卡 → 答复 → 再发下一句。同一期 3–8 个会话。
- **每步要看到什么**：
  - 发完消息：看得出它**在动**、在干哪一步（不只是「模型思考中 · 3 秒」）。
  - 工具跑完：扫一眼知道「跑了几个命令、有没有失败」；细节点开才看，不看就不占地方。
  - 弹卡时：不点开任何东西，就看到**要批哪条命令、为什么拦**。
  - 回头找旧会话：靠名字认出「修稿那次」「查素材那次」。

**目标**：第一轮（Spec 14）骨架已到位，这一轮补对话区的「活」——会话有名字、工具行说人话且按状态而非整行染色、运行中有克制的动感、做完一轮过程自动收起、开发者遥测不铺在聊天里。**业务语义（停机点、审批边界、Code Freeze、失败原文逐字）一条不改。**

## 二、人裁决（2026-10-12）

| # | 问题 | 裁决 | 施工注记 |
|---|---|---|---|
| Q1 | 标题用哪个模型 | 不新增配置项，一次性短请求 | **实查修正**：草案说「ava 没配轻量模型」不对——`llm.py` 已有 `PURPOSES = ("reasoning", "light")`，`LLMConfig.model_for("light")` 未配时回落到 `model`。所以用 `purpose="light"`：配了轻档走轻档，没配自然就是同一模型，仍不新增配置项。请求体本来就不带 thinking 字段，无需「关掉」 |
| Q2 | 标题存哪 | 期目录 `_agent/session-titles.json`，不往 `session.jsonl` 塞元数据 | 选题会话对应 `data/_idea/_agent/session-titles.json`。读改写持文件锁，写法照 `session_log.py` 回收站的 `fcntl` 锁先例（§五 R1 ②） |
| Q3 | 动效时长 token | 同意动 `tokens.css`：`--dur-slow: 300ms`、`--dur-enter: 240ms` | 取值理由见 §六 |
| Q4 | 回合折叠默认值 | 默认折叠（照 ZCode），有失败 / 被拒 / 被中断的回合不自动收起 | — |
| Q5 | 视觉方案 | R2 / R3 / R4 施工前先出 2–3 套真实窗口截图让人选 | 见 §四 PR-B |
| PR-B | R2 / R3 / R4 方案 | **人选 A 平铺（照 ZCode）**（2026-10-12，看过 [`options.shots.spec.ts`](2026-10-12-conv-liveliness-shots/options.shots.spec.ts) 的对照页：现状 / A 平铺 / B 卡片收纳 / C 时间线竖轨，含完成态浅深色、1280 宽、展开细节、运行中动图） | A 的样子：图标 + 类别词 + 主文本（bash 用途）+ 次文本（命令，等宽灰）+ 状态词 + ›；终端组子行不重复类别词；折叠行是一行灰字「已工作 … · N 个工具 · M 个失败 ›」；展开后过程挂 1px 细竖线；扫光用强调色、2s 一周。PR-C 照脚本里 A 的 CSS 落进 `style.css`（`[data-pb="A"]` 段与公共段） |

## 三、现状实测（2026-10-12，真实 app + 真实 core + 假模型）

| # | 现象 | 代码位置 |
|---|---|---|
| P1 | 会话名 = 首条用户消息截断；不能改名；空会话显示「（没有用户消息）」 | `SessionList.tsx`：`const title = r.firstUser \|\| "（没有用户消息）";` |
| P2 | 工具行显示内部工具名（`read_status`、`bash`），没有人话动词 | `ConversationPane.tsx` `case "tool":` 的 `<code>{row.name}</code>` |
| P3 | 成功整行变绿、失败整行变红，语义色铺满正文（违背 Spec 14「语义色只给真实状态」） | `ui.css`：`.conv-tool-ok { color: var(--ok); }`、`.conv-tool-fail { color: var(--danger); }` |
| P4 | 每条工具下挂「模型看到的原文」「作业输出（末 N 行）」两个原生 `<details>`，展开瞬跳、像调试器 | 同 `case "tool":` |
| P5 | 「复制命令」文字按钮每行常显 | 同上，`<CopyButton … label="复制命令" testId="tool-copy" />` |
| P6 | 短命令耗时总显示 `0s`，是噪声 | 同上，`{Math.round(row.durationMs / 100) / 10}s` |
| P7 | 连续 bash 平铺，无「终端 · 3 个命令 · 1 个失败」分组 | — |
| P8 | 一轮结束后过程不收起 | — |
| P9 | 运行中只有末尾「⟳ 模型思考中 · N 秒」与工具行转圈 | `ConversationPane.tsx::Activity` |
| P10 | 新行出现是硬切，无入场过渡 | 全局 |
| P11 | 每轮末尾铺整行遥测（`模型调用 4 · 工具 4（执行 4、重复拒绝 0）· 检查点 0 · 用时 0.041 s · done · none · 查证 …`） | `case "footer":` |
| P12 | 「停机点 05 待审（在待答区处理）」每轮重复 | `case "stop":` |
| P13 | **工具卡的命令与危险标记被待答区滚出视野**（草案原判「卡片不显示命令」，实测推翻，见下） | `ui.css` `.dock { … max-height: 45%; overflow: auto; … }`、`style.css` `.conv-shell .dock { max-height: 40%; }`、`HumanCards.tsx::RequestCard` 的自动聚焦 |
| P14 | 会话行只有标题：无相对时间、看不出在跑、看不出有待答卡 | `SessionList.tsx` |

**P13 实测**（附件 [`2026-10-12-conv-liveliness-shots/shots.spec.ts`](2026-10-12-conv-liveliness-shots/shots.spec.ts)，场景：停机点 05 待审 + bash `rm stale.tmp` 弹卡）：

| 窗口 | 待答区可见高 / 内容高 | 聚焦「批准」后 `scrollTop` | `card_text` 含命令 / 含「危险标记: [删除]」 | 聚焦后 `card_text` 在可见区内 |
|---|---|---|---|---|
| 1440×900 | 276 / 538 px | 196 | 是 / 是 | **否**（正文底 468 < 待答区顶 474，整段在上方） |
| 1280×800 | 236 / 538 px | 216 | 是 / 是 | **否** |

结论：core 的 `card_text` 第一行就是「危险标记」（`status_card.py` bash 分支「危险标记行必须排在一切模型可控字段之前」），内容早已齐全；问题是**布局**——待答区限高 40%，同时塞着工具卡与停机点卡，`approveRef.current?.focus()` 把待答区滚到按钮处，卡头与正文整段被推出视野。另见卡面噪声：标题重复（「工具卡 · bash · 工具审查: bash」）、`目录:` 一行是整条绝对路径。

## 四、分期与 PR 切分

| PR | 内容 | 动到 | 前置 |
|---|---|---|---|
| **PR-A** | R1 会话标题 | core（`pipeline/agent/`）+ host + renderer | 无，可与 PR-B 并行 |
| **PR-B** | R2 / R3 / R4 截图选型：附件目录加方案脚本，在真实窗口上改 DOM 出 2–3 套（差别在扫光强弱、分组样式、折叠行样式），**人选完才进 PR-C** | 只加附件，不改产品代码 | 无 |
| **PR-C** | 按人选方案施工 R2 / R3 / R4 | renderer + `tokens.css`；core 只加一个条件键（R2 ①） | PR-B 人已选 |
| **PR-D** | R5 / R6 / R7 | renderer；R7 依赖 PR-A 的标题 | PR-A；R6 的视觉调整同样先出截图（规则 4） |

R1 单独成 PR 的理由：它是本 spec 唯一动 core 会话层、写盘、出网的部分，出问题时要能单独回退；R2–R4 只改「怎么显示」。

## 五、改动清单

### R1 会话标题（P0，对应 P1）——PR-A

| # | 文件 | 改动 |
|---|---|---|
| ① | `pipeline/agent/session_titles.py`（新） | 纯函数 + 文件锁：`load(log_dir) -> dict[sid, {title, source, at}]`；`write(log_dir, sid, title, source)`，持 `_agent/session-titles.lock` 读改写、`os.replace` 原子落盘。**CAS 规则**：`source="generated"` 遇已有 `custom` → 不写、返回 False；`custom` 无条件写。`validate(text)`：去首尾空白与引号、单行、1–`TITLE_MAX_CHARS` 字，不合格返回 None |
| ② | 同上 | `generate(first_user, first_answer, cfg) -> str \| None`：`chat_complete(messages, purpose="light", timeout=TITLE_TIMEOUT_S, scope=<本会话 scope>)`，不带 tools；提示词要求「中文，概括这次要干什么，不超过 12 字，不带期名，只输出标题」。任何异常（`LLMError`、出网断言、超时）→ None，**不抛、不发 notice** |
| ③ | `pipeline/agent/session.py`（会话首个 `turn_finished` 之后） | 本会话首轮正常结束（`turn_finished.stopped == "done"`）且标题文件里该 sid 无记录时，起**守护线程**跑 ②，成功则 ① 写入 `generated`，再经 `channel` 发 `session_title` 帧 `{sid, title, source}`。线程不进会话日志、不进 `messages`、不计模型调用 / 检查点；新线程的 contextvars 是空上下文，`_LAST_USAGE` 不会串到主回合的上下文读数（测试锁住，§七） |
| ④ | `pipeline/agent/protocol.py`、`desktop/src/shared/convFrames.ts` | 新帧 `session_title`（core → host）；TS 侧登记帧形状。host 收到后更新会话列表行的标题，不进对话流 |
| ⑤ | `pipeline/agent/cli.py::_dispatch_session_admin` | `/list-sessions` 每行加 `title` 与 `title_source`（无记录时 `title = first_user`、`title_source = "first_input"`）；新人令 `/rename-session --sid=<sid> --title=<text>`：过 `validate`，写 `custom`；sid 不存在退 1、用法错退 2。**不取期租约**（标题文件有自己的锁，会话在跑时也能改名） |
| ⑥ | `pipeline/agent/session_log.py`（删会话进回收站处）、`cli.py::_purge_trash` | 删会话时该 sid 的标题条目随之移出（写进回收站同名 `.title.json` 旁注，供 D66 回收站列表显示名字）；彻底删除时一并删。Spec 18 建期迁会话：idea 的标题条目随会话迁到期目录 |
| ⑦ | `desktop/src/renderer/SessionList.tsx` | 标题取 `r.title`；双击行或右键菜单「重命名」进行内编辑，回车保存（调 ⑤）、Esc 取消、空串视为取消；收到 `session_title` 帧时标题淡入切换（`--dur-base`，`prefers-reduced-motion` 下直接换） |

### R2 工具行重做（P0，对应 P2–P7）——PR-C

| # | 文件 | 改动 |
|---|---|---|
| ① | `pipeline/agent/session.py`（tool 帧组装处：`if name == "bash": frame["command"] = …`）、`convFrames.CONDITIONAL` | bash 帧再加一个条件键 `description`（模型给的 `args.description`，剥控制字符，可为空串）。与 D76 的 `command` 同族：是模型发出的参数，不是工具结果 |
| ② | `desktop/src/shared/toolView.ts`（新） | 纯函数 `toolCategory(name)`：`bash`→终端；`read_artifact`/`read_status`/`list_episodes`→读取；`write_episode_file`/`write_note`/`write_memory`/`cover_edit`→写入；`run_pipeline`→流水线；`web_search`/`search_notes`→搜索；`web_fetch`/`crawl`/`browser`→网页；`acquire_propose`→素材；其余→工具名原样。`toolStatus(row, running)` → 等待中 / 执行中 / 已执行 / 执行失败 / 已拒绝 / 已停止。`mainText(row)`：bash 取 `description`、空则取 `summary`；其余取 `summary`。上述 15 个名字与 `config/agent/tools.json` 逐一核对过（2026-10-12）；施工时若注册表已增减，对不上停下报告 |
| ③ | `desktop/src/shared/convFold.ts::ToolRow` | 加 `id: string`（`turn_id` + `index`，历史重建行用位置序号兜底）、`turnId: string \| null`、`description: string \| null`。`ConversationPane` 的 `<Row key={i}>` 改用稳定 key |
| ④ | `desktop/src/renderer/ConversationPane.tsx` `case "tool":` | 摘要行：`图标 类别词 主文本 … 状态词 ›`；内部工具名与完整命令放 `title` 悬停与展开区。只有状态词带语义色（删掉 `.conv-tool-ok/-fail` 的整行染色，新样式写 `style.css`，`ui.css` 冻结不动）。失败状态词悬停显示 `observation` 首行 |
| ⑤ | 同上 | 一个 `›` 控制展开，展开区依次：命令全文（带复制）→ 作业输出 → 模型看到的原文（非成功时，逐字不截断，S9-R1）。高度过渡用 `grid-template-rows: 0fr → 1fr` + `--dur-slow`，收起在 `transitionend` 后才卸载内容。展开状态按 `row.id` 存在 ConversationPane 内的 `Map`（切会话重挂即清空，不落盘） |
| ⑥ | 同上 | 复制改为悬停才出现的图标按钮（`testId="tool-copy"` 保留，e2e 已有断言）；耗时 `< TOOL_SECS_MIN_MS` 不显示，否则 `3.2s` |
| ⑦ | `desktop/src/shared/toolView.ts` | `groupRuns(rows)`：同一 `turnId` 内连续 ≥2 条 bash 折成一组「终端 · N 个命令 · M 个失败」；组内有失败或有 open 的卡 → 默认展开，否则收起 |
| ⑧ | 同上 | `重复调用` 徽标改中性小徽标，与状态词同层级 |

### R3 运行中的动感（P0，对应 P9、P10）——PR-C

| # | 文件 | 改动 |
|---|---|---|
| ① | `style.css` | `.conv-shimmer`：`background-clip: text` 渐变 + `@keyframes`，周期 `SHIMMER_MS`；执行中工具行的类别词挂它，图标静态，**删掉每行一个的 `ui-spinner`**（`e2e/session.spec.ts` 里 `conv-tool-spinner` 的两条断言同步改为查扫光标记） |
| ② | `ConversationPane.tsx::Activity` | 文案改「正在思考 · 12 秒」/「正在执行 · 12 秒」，挂扫光，去掉独立转圈；秒数保留（D46「还在动」的证据） |
| ③ | `ConversationPane.tsx` `case "tool":` | run_pipeline 执行中：摘要行内一根 64px 细条来回扫光（**core 不发进度**——D71 已查明 `job_heartbeat` 从未发出，不编造百分比）；最新一行作业输出作为次文本滚动替换，积压直接跳到最新 |
| ④ | 同上 + `style.css` | 入场：新用户气泡、新工具行、新答复淡入 + 4px 上移，`--dur-enter`。**只给 ConversationPane 挂载之后新出现的 key**——挂载那一刻已有的行（历史恢复、切会话重挂）一律不播 |
| ⑤ | `style.css` | `@media (prefers-reduced-motion: reduce)` 下显式 `animation: none` 停扫光（`ui.css` 全局规则只把时长压到近 0，扫光是无限循环，要显式停） |

### R4 回合折叠（P0，对应 P8）——PR-C

| # | 文件 | 改动 |
|---|---|---|
| ① | `desktop/src/shared/toolView.ts` | `turnSegments(rows)`：以 `user` 行开头、以该回合的 `footer` 结尾切段，段内「过程」= 工具行 / 组、`log`、`injection`、`card`、`notice`、中间答复（D79 的 interim）；**终稿答复与用户消息留在外面**。段的 `outcome`：done / 有失败 / 有被拒卡 / 已停止（`exited` 或中断） |
| ② | `ConversationPane.tsx` | 已结束段折成一行「已工作 1分12秒 · 7 个工具 ›」；运行中段显示「工作中 23 秒」且展开。运行 → 结束那一刻自动收起一次；`outcome` 非 done 的段不自动收起；之后人手动开合，状态按段首 user 行 key 记住 |
| ③ | 同上 | 开合用 R2 ⑤ 同一套高度过渡。折叠只改显示，`ConvRow` 数据与帧一条不删（e2e 的 `convSnapshot` 不受影响） |

### R5 噪声收纳（P1，对应 P11、P12）——PR-D

- `case "footer":` 默认只显示「N 次调用 · 上下文 X 字」，`ⓘ` 悬停 / 点击看完整遥测原文（字段不删，仍可复制）。
- `convFold.ts` 折 `stop` 行：同一会话里同一停机点文本只在**与上一条不同**时出一行。

### R6 工具卡一眼可批（P1，对应 P13）——PR-D

- **硬要求**：聚焦「批准」后，卡头与 `card_text` 中「危险标记」到「命令」各行必须在待答区可见区内（P13 实测的反面，机检锁住，§七）。怎么做（待答区高度、卡片顺序、聚焦用 `preventScroll`、停机点卡收成一行）出 2–3 套截图人选。
- 「告诉它怎么改」输入框默认收起，点「拒绝…」才展开；回车批准（ADR-0030 决策 6）与 400 ms 防连按不变。
- 时间线里的「[工具卡] bash · 待答」纯文本行改成 R2 同结构的摘要行，状态词「等待批准」。
- 卡面噪声（标题重复、`目录:` 整条绝对路径）属于 core 的 `card_text`：**本 spec 不改**（卡面文本有 H-3「按原样、不解释标记」与危险标记行位置的安全约束），另记备忘。

### R7 会话列表行（P1，对应 P14）——PR-D

- 行内：标题（R1）+ 右侧相对时间（复用 `shared/relTime.ts`，与期行同口径）。
- 正在跑的会话：标题挂 R3 同一套扫光；有待答卡的会话用期行已有的「待答」徽标口径。
- 「删除」改为悬停图标按钮；删除逻辑（回收站、宿主确认框）不动。

## 六、新常量与取值理由

| 常量 | 值 | 理由 |
|---|---|---|
| `TITLE_MAX_CHARS`（校验上限） | 20 | 提示词要求 ≤12 字；校验放宽到 20 是给模型留余量，超 20 视为没听指令、走兜底。侧栏 1280 宽下一行约容 13 个汉字（P14 截图实量：13px/字、文本区约 180px），R7 加相对时间后约 11 字，超出部分省略号截断，悬停看全名 |
| 提示词目标 | ≤12 字 | 同上：让常见标题在 R7 布局下不被截断 |
| `TITLE_TIMEOUT_S` | 30 | 线程异步、不阻塞任何东西，超时只决定线程多挂多久；取 `REQUEST_TIMEOUT = 60` 的一半，因为标题请求上下文只有首问首答（几百字），远小于主回合 |
| `--dur-slow` | 300ms | 展开收起；ZCode `ToolLayout.tsx` Collapsible 实值 |
| `--dur-enter` | 240ms | 入场；ZCode 用 900ms 是配合逐字流式，ava 答复整段到达（D46），900ms 会让整段文字「慢慢浮现」显得迟钝；取 `--dur-base`（180ms）与 `--dur-slow` 之间 |
| `SHIMMER_MS` | 2000 | 人选方案 A（强扫光，2s 一周；B 的 4s 灰阶扫光落选）。扫光强弱是审美取舍，规则 4 |
| `TOOL_SECS_MIN_MS` | 1000 | 草案 P6：亚秒级耗时对人没有信息量；≥1s 才可能是「这一步慢」的线索 |

## 七、测试

- **pytest**（`tests/test_session_titles.py` 新）：`validate` 边界（空、纯空白、带引号、多行、21 字）；CAS——先写 `custom` 再写 `generated` 不覆盖、反序则覆盖；`generate` 遇 `LLMError` / 超时返回 None 且不抛；首轮 done 后起线程写 `generated` 并发 `session_title` 帧，非 done 不起；标题请求**不进** `session.jsonl`、不改模型调用计数与检查点；主回合 `turn_finished` 的上下文读数不受标题请求的 usage 影响；`/list-sessions` 带 `title` / `title_source`；`/rename-session` 会话在跑时也成功；删会话后标题条目随之移出。
- **vitest**：`toolCategory` 全映射与兜底；`toolStatus` 六态；`groupRuns`（跨回合不合组、单条不成组、失败计数）；`turnSegments`（终稿答复留在外面、interim 进过程、四种 outcome）；`stop` 去重。
- **e2e**（`desktop/e2e/session*.spec.ts` 扩）：工具行摘要含类别词与状态词、展开后命令全文可见；失败行展开后原文与 `session.jsonl` 对应 tool 消息逐字一致（沿用 TX-1）；回合结束自动折叠、有失败的回合不折；改名后侧栏显示新名、重启后仍在；标题请求失败时主回合照常、侧栏保留兜底名。
- **底线断言（UI 规则 5）**：1280×800 与 1440×900 下工具行、分组、折叠行两两 boundingBox 不相交，`scrollWidth <= clientWidth`；`prefers-reduced-motion` 下扫光与入场的 `animation-name` 为 `none`；切会话后新挂载的行不带入场标记；**R6：聚焦批准后 `card_text` 在待答区可见范围内**（直接复用附件脚本里的 `textFullyVisible` 量法）。
- **变异**（安全语义与状态一致性，规则 4 允许的范围）：CAS 放行 `generated` 覆盖 `custom`；标题请求写进会话日志；失败原文被截断；有失败的回合被自动折叠；R6 聚焦恢复默认滚动——各自应被抓。

## 八、明确不做

- **R8 流式逐字输出**：ava 答复整段到达（D46），逐字出现要新增 core ↔ host 增量帧，是协议改动不是 UI 打磨，另立项。
- **排队面板的编辑 / 拖动排序**（ZCode `ConversationQueuePanel`）：D79 刚落地，等真实使用反馈。
- 不引 Tailwind、Radix、lucide、`motion`（Spec 14 §2.4「不学」表；新依赖要人批）。图标继续用仓库内手绘 SVG，新增「终端 / 读取 / 写入 / 网页 / 流水线 / 搜索 / 素材」照 Spec 14 §2.5 规格补画。
- 不改 core 的 `card_text`（R6 末条）。
- 不改停机点、审批边界（ADR-0030）、失败原文逐字（S9-R1）、TG-4′、Spec 10 §2.3 的冻结文案（恢复历史分隔线等）。
- 进度百分比：core 不发，不编造。

## 九、验收

1. **【人勾】真实任务**：打包版、1440×900，完整跑一期，记下别扭之处（UI 规则 2，agent 不得代勾）。
2. **【人勾】截图选型**：R2 / R3 / R4（PR-B）与 R6（PR-D）各从 2–3 套方案里选一套，选定写回本 spec §二。
3. 机检：`uv run pytest`、`cd desktop && npm run typecheck && npm test`、会话 e2e、打包版冒烟全绿；§七变异全杀。
4. 每个 PR 实现后在 1280×800 与 1440×900 各出一张真实窗口截图给人看。

## 十、完成判定

- [x] PR-A：R1 ①–⑦ 落地，§七 R1 相关测试与变异通过（2026-10-12，机检通过，**待人验收**，见 §十二）
- [x] PR-B：方案截图交付，人已选 A（2026-10-12），已写回 §二
- [x] PR-C：R2 / R3 / R4 按人选施工，底线断言通过（2026-10-12，机检通过，**待人验收**，见 §十一）
- [ ] PR-D：R5 / R6 / R7 落地，R6 可见性断言通过（2026-10-12：R5、R6.3、R7 已施工·机检通过；R6.1 / R6.2 **人选方案 C**，施工中，见 §十三）
- [ ] §九 第 1、2 条人已勾

## 十一、PR-C 施工记录（2026-10-12）

**落地**：R2 ①–⑧、R3 ①–⑤、R4 ①–③，照人选方案 A。实现截图（真实组件、不注入原型）：附件 [`impl.shots.spec.ts`](2026-10-12-conv-liveliness-shots/impl.shots.spec.ts)，产物 `desktop/out/d80-shots/pr-c/`（完成态浅 / 深 / 1280、展开细节、运行中静图 + 动图）。

| 文件 | 改了什么 |
|---|---|
| `pipeline/agent/session.py`、`protocol.py`；`desktop/src/shared/convFrames.ts` | bash 的 tool 帧与 history 帧加条件键 `description`（剥控制字符，可为空串）；TS 契约表同步，缺键判 malformed（同 D76 先例） |
| `desktop/src/shared/convFold.ts` | `ToolRow` 加 `id`（实时 `turn_id:index`，历史 `h:index@行号`）与 `description`；历史重建的 user / assistant / tool 行带 `hist`；页脚行带 `stopped` 原值 |
| `desktop/src/shared/toolView.ts`（新） | 类别词、状态词、主 / 次文本、耗时、终端组、回合切段与默认开合、折叠行标签——纯函数 |
| `desktop/src/renderer/ConversationPane.tsx` | 回合折叠块、终端组、新工具行、`Collapse` 高度过渡、`Activity` 改扫光、入场标记 |
| `desktop/src/renderer/style.css`、`tokens.css`、`icons.tsx`、`copy.tsx` | 方案 A 的样式；`--dur-slow` / `--dur-enter`；补 5 个图标（终端 / 写入 / 网页 / 流水线 / 复制）；`CopyButton` 可用图标作标签 |

**与 §五 的偏差（施工时按仓库门禁或实情调整，逐条写明）**

1. **状态词四个，不是六个**：执行中 / 已执行 / 执行失败 / 已停止。「已拒绝」是卡片行的状态，工具行要判它就得解析工具原文，违反 S9-R1「host 不解析」；「等待中」没有对应的帧。
2. **状态词与次文本用 `--text-sm`**，不是原型里的 `--text-xs`：VS-4 只许徽标类用 xs。比原型略大一号。
3. **摘要行拆成「按钮 + 行尾元素」**：VS-8 要求可点击元素是原生按钮，而复制按钮不能嵌在按钮里。所以图标 / 类别词 / 主次文本在一个 `<button aria-expanded>` 里；复制、耗时、状态词在它外面；行尾箭头是 `tabIndex=-1`、`aria-hidden` 的按钮（鼠标点它同效，键盘与读屏走前一个按钮）。
4. **回合切段规则**（spec 未写细，按实情定）：页脚是一轮的结束；页脚后、下一条 user 前的行（停机点等）归入该轮 tail；回合中途送达的插话（D79：user 紧跟工具行、回合未结束）留在过程里，不另起一轮——收起后看不见，展开可见；历史重建没有页脚，user 跟在答复之后即视为新一轮，折叠行写「工作过程」（不知用时）。提示行（notice）一律放在折叠块之外，不藏错误。
5. **入场判据用时刻**：条目时刻（宿主 `now()`）晚于本次挂载的行才挂 `conv-enter` / `data-enter`；历史行另由 `hist` 排除。比「下标 ≥ 挂载时行数」稳：切会话后快照晚到时也不会整屏播入场。
6. **TX-1 的「对话流里没有按钮」收窄到答复行**：折叠 / 展开按钮是宿主自己的，断言本意是「模型输出渲染不出可点控件」；链接、图片仍查整条对话流。
7. **ui.css 未动**（门禁 1 冻结）：`.conv-tool-ok/-fail` 两条规则仍在但不再被引用；`tokens.css` 的冻结哈希按 Q3 裁决更新、token 计数 79 → 81，理由写在 `tests/static/visual.test.ts` 的基准注释里。

**验证**：`uv run pytest` 2651 passed；`npm run typecheck` 通过；`vitest` 527 passed（新增 `toolView.test.ts` 16 条）；e2e 全量 131 passed + 新增 `e2e/convTools.spec.ts`（真实 core：用途 / 命令显示、失败不折与成功折、终端组、1440 与 1280 两档两两不相交与不横向溢出、减弱动效下扫光与入场 `animation-name: none`、切会话重挂后无入场标记、运行 → 结束边沿自动收起）。改动的既有 e2e：`session.spec.ts` TX-1 / TX-D46a / TX-D46b、`sessionReal.spec.ts` TX-1 / D76 两条（展开路径与新文案）。

**变异 7/7 被抓**：失败回合也自动折叠、展开区原文被截断、减弱动效下扫光没停、重挂后仍播入场、core 不发 description、终端组阈值改 3、history 帧丢 description。

**待人**：§九 第 1 条真实一期手验（打包版）；看 `pr-c/` 截图确认与选型时的 A 一致。

## 十二、PR-A 施工记录（2026-10-12）

**落地**：R1 ①–⑦。侧栏截图（真实组件）：附件 [`impl.shots.spec.ts`](2026-10-12-conv-liveliness-shots/impl.shots.spec.ts) 的「PR-A」用例，产物 `desktop/out/d80-shots/pr-a/`（兜底名 → 生成名 → 悬停 → 编辑中 → 改名后）。

| 文件 | 改了什么 |
|---|---|
| `pipeline/agent/session_titles.py`（新） | 标题文件读写（fcntl 锁 + `atomic_write`，CAS：`generated` 不覆盖 `custom`；文件坏了拒写不覆盖）、`validate`、`generate`（`purpose="light"`、无 tools、首问首答各截 500 字、任何失败 None）、`copy_entry` |
| `pipeline/agent/protocol.py` | `_maybe_title`：首个 `stopped == "done"` 的回合之后起守护线程；本进程按 sid 只起一次；标题文件已有该 sid 不起；成功写盘后发 `session_title` 帧 |
| `pipeline/agent/cli.py` | `/list-sessions` 行带 `title` / `title_source`；`/rename-session --sid= --title=`（期 / 选题两叉，不取期租约）；`/list-trash` 行带 `title`；建期迁会话带走该段标题 |
| `desktop/src/shared/{convFrames,protocol,sessionTitle}.ts` | 新帧 `session_title`；`SessionRow.title / titleSource`、`TrashRow.title`；`conv.rename` 方法；标题形状规则（宿主、spawner、侧栏共用） |
| `desktop/src/host/{spawner,service}.ts` | `RENAME_SESSION` / `RENAME_IDEA_SESSION` 模板；`conv.rename`（spawn 前校验；退出码 2 → E_BAD_REQUEST、1 → E_CORE；不结束活会话）；会话行缺标题键时回落首条消息（旧 core 兼容）；删除 / 彻底删除确认框显示标题 |
| `desktop/src/renderer/{SessionList,App}.tsx`、`style.css` | 行标题取 `title`；「改名」按钮 + 行内编辑（回车存、Esc / 失焦 / 空串 / 没改 = 取消）；收到 `session_title` 帧重取列表；标题换了淡入（`--dur-base`，减弱动效下不播）；回收站行显示标题 |
| `desktop/e2e/fakeLlm.ts`、`tests/test_agent_protocol.py` 的假端点 | 认出起名请求（首条系统消息 = `session_titles.PROMPT`），另记 `titleRequests`、另行回复，不占剧本序号、不进 `requests`。TS 版默认回 500（既有用例按首条消息认会话行），可设 `titleReply` / `titleDelayMs` |

**与 §五 的偏差**

1. **Q1 用 `light` 档**（§二已注）：仓库本来就有 `models.light`，没配时回落主模型，仍不新增配置项。
2. **删除 / 彻底删除不动标题文件**（原 R1 ⑥ 要「随之移出、写旁注」）：sid 是 16 位随机串不复用，残留条目无害；手工从回收站找回时名字跟着回来；也不给 D66 的「彻底删除主日志零接触」添风险面。回收站列表按 sid 查回标题显示。
3. **改名入口是行尾「改名」按钮**（原 R1.3 写「双击行或右键」）：单击会话行就是 `conv.enter`（结束空闲活会话再接上），双击必然先触发两次单击。按钮与「删除」同为悬停才出现，R7 再一起改成图标。
4. **改名在回合运行中也能用**：只受列表自身 busy 约束（core 侧不取期租约）。
5. **既有测试的三处调整**：`test_tp4c` 关停段过滤异步的 `session_title` 帧；`test_tp16` 的「选题会话只写 `session.jsonl`」放宽到允许 `_agent/` 下的标题文件与锁（守护线程写没写完时刻不定，断言为子集）；`test_agent_steer` 的 `_Host` 替身补 `sid` / `log_dir`。

**验证**：`uv run pytest` 2686 passed（新增 `tests/test_session_titles.py` 34 条 + 协议进程集成 1 条）；`vitest` 536 passed（宿主 `conv.rename` / 标题解析、spawner 改名模板）；e2e 全量 134 passed（新增 `e2e/sessionTitle.spec.ts` 2 条：兜底 → 生成名、改名回车存 / Esc 取消、重启仍在、已有名不再起名、起名失败主回合照常且无提示）。

**变异 7/7 被抓**：生成名覆盖人改名、标题请求同步跑在主线程（usage 串到主回合）、非正常结束也起名、列表不读标题文件、改名要取期租约、侧栏收到标题帧不重取、超长标题截断而非拒收。其中两条首轮漏网后补强了测试：改名测试改由**另一个进程**持期租约（同进程重复取租约测不出来）；侧栏用例让假模型起名晚 1.5 s 到达（否则标题碰巧赶上回合结束那次重取）。

**待人**：真实一期里看起名质量（用的是本机 `light` 档或主模型）；看 `pr-a/` 截图。

## 十三、PR-D 施工记录（2026-10-12，R6.1 / R6.2 待人选）

**已落地**

| 项 | 文件 | 做了什么 |
|---|---|---|
| R5 ① | `shared/convFold.ts`、`renderer/ConversationPane.tsx` | 页脚行多一个 `brief`：「N 次模型调用 · 上下文 …」（工具数与用时已在折叠行上）；默认只显示它，ⓘ 展开完整遥测原文（字段一个不删，悬停也能看） |
| R5 ② | `shared/convFold.ts` | `stop_points` 帧内容与上一帧相同就不成行；停机点解决或新出现（内容变化）才成行。不分回合（S9-R4 每回合恰一帧，逐轮重复就是噪声来源） |
| R6.3 | `shared/convFold.ts`（`cardParts` / `cardPhrase`）、`ConversationPane.tsx`、`style.css` | 时间线里的「[工具卡] bash · 待答」改成与工具行同结构：⚠ 卡类 工具名 … 状态短语。待答显示「等待批准」（等待色），被拒原样带反馈「已拒绝（附反馈：…）」（危险色），作废带原因；过长省略、悬停看全文。`row.text` 不变 |
| R7 | `renderer/SessionList.tsx`、`App.tsx`、`icons.tsx`（补 `trash`）、`style.css` | 行右侧相对时间（`formatRelTime`，同期行口径）；正在跑的会话标题扫光（`conv-shimmer`，同对话区）；活会话有待答卡挂「待答」徽标（`ui-badge--wait`，同期行）；改名 / 删除换成悬停或键盘进入本行才出现的图标按钮，与相对时间占同一位置 |

**验证**：`typecheck` 通过；`vitest` 539 passed（`convFold.test.ts` 新增短摘要、停机点去重、卡片短语 3 条）；e2e 134 passed（`convTools.spec.ts` 补：脚注短摘要与 ⓘ 展开、相对时间、运行中侧栏扫光、三轮下来停机点只一行、卡片行「等待批准」→「已批准」、侧栏「待答」徽标出现与消失；`sessionReal` D56 改为展开后看完整遥测）。变异 4/4 被抓：停机点不去重、脚注默认展开、侧栏丢运行中扫光、待答短语变回「待答」。

**R6.1 / R6.2：方案截图（待人选）**——附件 [`r6.shots.spec.ts`](2026-10-12-conv-liveliness-shots/r6.shots.spec.ts)，产物 `desktop/out/d80-shots/r6/index.html`。card_text 由 core 原样渲染（H-3），三方案只动布局：

| 方案 | 做法 | 1440：命令 / 批准按钮 | 1280：命令 / 批准按钮 |
|---|---|---|---|
| 现状 | 待答区 40%，聚焦把卡头滚走 | ✗ / ✓ | ✗ / ✓ |
| A 聚焦不滚 + 停机点收成一行 | 高度不变；聚焦不滚；停机点卡只留标题；反馈藏到「附说明…」后、拒绝仍一键 | ✓ / ✓ | ✓ / **✗**（卡面 8 行 + 按钮放不进 236px） |
| B 待答区让高 | 有工具卡时待答区最高 60%；「拒绝…」第一次点只展开反馈框（spec 原写法） | ✓ / ✓ | ✓ / ✓ |
| C 卡片进对话流 | 工具卡挂在对话流末尾，待答区只剩停机点；反馈同 A | ✓ / ✓ | ✓ / ✓ |

首轮量法只查了卡面文本，A@1280 截图里按钮被挤出却判 ✓——已补「聚焦的批准按钮也在视野内」一项。

**人选 C**（2026-10-12）：工具卡进对话流末尾，待答区只剩停机点；反馈照 A——「拒绝」一键，「告诉它怎么改」藏在「附说明…」后。
