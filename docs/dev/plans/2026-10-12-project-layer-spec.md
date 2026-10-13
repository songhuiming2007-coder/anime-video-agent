# D81：企划层（多卷系列成为一等实体）施工 spec

> 对应 issue：**D81**（`docs/dev/issues/README.md`）。上位需求：[`2026-10-12-project-layer-direction.md`](2026-10-12-project-layer-direction.md)（A–E 五节）。本 spec 以代码现状为准，与上位需求不符之处见 §三，设计按现状调整，不照抄上位需求。
> 相关：ADR-0023（跨期记忆）、ADR-0025（工具表封顶 15）、ADR-0027（工具不按模式授权）、ADR-0029 / ADR-0030（bash、审批只拦硬危险）、D43、D47 / D48（写期文件）、D52（工具结果脱敏）、D53（argv 预检）、D80（UI 先例）、qc 总装口径（2026-09-13）。
> 姊妹 spec：[`2026-10-12-bug-ticket-spec.md`](2026-10-12-bug-ticket-spec.md)（D82）。两者无硬依赖：D82 排在 PR-1 之后是时机（第一卷开写前要有企划层，开始生产时要能开工单）；反方向上，本 spec PR-5 用到 D82 引入的「逐条人审的模块命令」集合与 bash 绕卡规则（§4.8）。
> **PR-2 含 UI，plans/README 的 UI 五条规则对它生效**：场景见 §一，视觉方案由人从截图里选（PR-2a），验收第 1 条是人在打包版里做一件真实工作（§九）。
> **数据纪律**：`data/episodes/EGOIST-传奇企划志-V2/` 是已交付成品，**只读**。本 spec 的任何测试与验收步骤都不写入它；用到它的验收一律走 §4.9 的「只读镜像」，并在步骤前后用 `find … -newer` 证明没写过。
> **红队已审（2026-10-12）**：34 条发现与处置见 §十四；P1 全部吸收或经人裁决（新增裁决 Q6）。

---

## 一、目标与用户场景（UI 规则 1）

**目标**：母目录从「放几份 md 的文件夹」变成 ava 认识的企划。企划有自己的立项文件 `00-project.md`、会话、上下文、记忆、跨卷检查和最后一道工序（总装）。凡是属于整个系列、不属于某一卷的东西，不再靠 bash 和人的记忆兜底。

**第一个使用者**：supercell 编年史，分 4 卷左右，最后总装成不少于 1 小时的合集，全程在桌面端里做。参照物是 `data/episodes/EGOIST-传奇企划志-V2/`（三卷 + `00-全一期-典藏版/`）。

**时机**（以 supercell 为准）：PR-1 + PR-2 在第一卷开写前可用；PR-3（C-3）在第二卷写稿时可用；PR-4（C-1/C-2）在第二卷排片前可用；PR-5（D）在最后一卷完成前可用。

**界面场景**（PR-2 的设计输入，截图方案必须按这个场景出）：

- **谁**：人本人，一卷做下来 3–8 个会话、1–3 小时；整个企划持续数周。
- **窗口**：1280×800 与 1440×900 两档。侧栏、对话区常开，预览默认收起（D39 方案 B）。
- **列表里同时有**：supercell 企划 4 卷（加一个合集期，后期才出现）、EGOIST V2 三卷 + 典藏版、EGOIST V1 三卷（目录名与 V2 完全相同：`01-借躯降生` 等），以及约 20 个普通期（实数：`data/episodes/` 下 22 个顶层目录，19 个普通期 + `2025-老视频` + 两个 EGOIST 企划）。
- **人连续做什么、每一步要看到什么**：
  1. 新建企划「supercell 编年史」：要看到它作为一个企划出现在列表里，标着「未立项」（还没有 `00-project.md`）。
  2. 点进企划，开企划会话，和 ava 定总纲与卷表：要看到当前是**企划会话**（不是某一卷），状态区显示卷表解析结果；ava 改卷表时弹卡，卡上能看到卷表 diff。
  3. 在企划下新建第一卷：要看到新卷归在 supercell 下面，而不是混进按时间排的普通期里。
  4. 进第一卷开会话写稿：要看得出这是 supercell 的第一卷；ava 第一轮就知道分卷边界（不用人复述）。
  5. 来回切换：企划会话 ↔ 第一卷会话 ↔ 某个普通期。要在两步之内回到任意一处，且不会把 V1 和 V2 的 `01-借躯降生` 认错。
  6. 做到第三卷时扫一眼企划：每卷走到哪一步、哪卷停在人审、哪卷有卡待答，不用逐卷点开。
  7. 「按工序」视图下：企划内各卷怎么出现（归组还是散开），由截图方案定，人选。

## 二、裁决记录

| # | 问题 | 人的原话（2026-10-12，AskUserQuestion 选项原文） | 施工注记 |
|---|---|---|---|
| Q1（上位 §5-1） | `00-project.md` 字段与格式；标题 / 封面模板放哪 | 「单文件，机读头+卷表+散文 (推荐)」（看过 EGOIST V2 真实内容写的样例） | 格式见 §4.7。样例里的 `番:` 行保留为说明文字、机器不读——没有消费方的字段不进解析（判据 6） |
| Q2（上位 §5-3） | 企划会话能做什么 | 「只做规划、跨卷审查、总装 (推荐)」 | 企划会话的 `run_pipeline` 只放行企划级模块（`assemble`、`crosscheck`）与只读模块，卷级工序拒绝并指路到卷会话（§4.2） |
| Q3（上位 §5-4） | 企划记忆额度 | 「3000 字」 | `PROJECT_MEMORY_BUDGET_CHARS = 3000`，压力线按原公式 `3000 − (400 + 3) = 2597`（§4.5） |
| Q4（新增，现状核对 §三 #5/#6 引出） | ADR-0030 后写 `01-topic.md` 与写记忆都已免卡，`00-project.md` 与企划记忆要不要弹卡 | 「机读字段变了才弹卡 (推荐)」 | 机读字段（企划名、合集、卷表）变化 → 弹卡带 diff、不开放「本会话都批准」；只改散文 → 免卡、写前留底。企划记忆照全局记忆：免卡，日志记 before / after（§4.4、§4.5） |
| Q5（新增） | 总装什么时候弹卡 | 「每次总装都弹卡 (推荐)」 | 每次 `assemble` 都弹卡；覆盖已有 `05-final.mp4` 另需 `--replace`，同样逐条弹卡、不开放「本会话都批准」（§4.8） |
| Q6（红队 R-6 引出，2026-10-12 审 spec 时） | 卷走到 03 之后 scope 变 `pipeline`，新开的卷会话注不注入企划记忆（全局记忆在 pipeline scope 不注入，ADR-0023 补记） | 「卷/企划会话一律注入 (推荐)」 | 企划记忆的注入**不看 `memory.scopes`**：卷会话与企划会话一律注入；全局记忆的开关不变（§4.6） |
| UI | 侧栏企划分组、企划进度、新建企划 / 卷入口 | 「B 分组卡片」（2026-10-13，PR-2a 三套方案 × 五个场景帧，AskUserQuestion 选项原文） | 企划是一张浅底卡：卡头 = 折叠箭头 + 企划名 + 「未立项」标 + 右侧「企划会话」按钮；卡头下一行聚合徽标（N 张卡待答、停机 N），再下一条每卷一段的进度条（绿 = 完成、橙 = 停在人审、灰蓝 = 进行中）；卷行带「第 N 卷」前缀，合集写「合集 ·」；卡底「＋ 新建卷」；列表顶「＋ 新建企划」。「按工序」视图下各卷散进停机点 / 非停机点分组，行首带企划名。截图脚本 [`2026-10-12-project-layer-spec/options.shots.spec.ts`](2026-10-12-project-layer-spec/options.shots.spec.ts) |

## 三、现状核对（2026-10-12，逐条对照上位需求 §1）

| # | 上位需求的说法 | 代码现状（引原文） | 结论与对设计的影响 |
|---|---|---|---|
| 1 | 子卷被展平进期列表，母目录消失；两个企划各有一个 `01-…` 时列表里分不清 | `cli.py::get_episodes_list`：「`# 若子目录包含子期目录（如 EGOIST-传奇企划志-V2/03-终局葬礼），展平纳入`」；`episodes.ts`：`for (const s of subEps) visible.push({ epKey: \`${name}/${s}\`, abs: \`${d}/${s}\` });` | **部分成立**。桌面端的 `epKey` 带母目录名（`App.tsx`：`<span className="ep-name">{e.epKey}</span>`），列表里**分得清**。分不清的是 Python 侧：`_tool_list_episodes` 只给 `"name": ep.name,`，模型看到的 V1 与 V2 都是 `01-借躯降生`；`resolve_episode_target` 先 `ep_root.glob(f"*/{target}")` 取第一个、再 `ep_root.rglob(target)`，**同名卷静默解析到先遍历到的那个企划**。现状就有此冲突：`EGOIST-传奇企划志/` 与 `EGOIST-传奇企划志-V2/` 下三卷目录名完全相同。→ PR-1 修解析（§4.1） |
| 2 | （上位需求未提）母目录 | EGOIST V2 母目录已有 `session.jsonl`（82 行）、`events.jsonl`、`_agent/approvals.jsonl`（2026-09-27）——**有人已经把母目录当成「期」开过 ava 会话**：`protocol.py` 的 `ep_dir = resolve_episode_target(rest[0])` 接受任何 `data/episodes/` 下的目录。而母目录 `README.md` 写着「严禁在本母目录下直接写入 `01-topic.md`」，`write_episode_file` 却会放行 | → PR-1：企划目录不再能作为期目录打开（期会话、`write_episode_file` 都拒）；母目录已有的 `session.jsonl` 原样作为企划会话的历史（同名同格式，§4.2） |
| 3 | （上位需求未提）典藏版 | `00-全一期-典藏版/` 有 `01-topic.md`，按现规则它是一个「子期」，与三卷并列出现在列表里；qc 的 48–52 分钟门禁就读它的 `时长目标` | 合集期仍是企划下的一个子目录、仍有自己的 `01-topic.md`；`00-project.md` 用 `合集:` 字段标出它（§4.7），总装输出到它 |
| 4 | 读工具只能读当期目录与 `data/library/`，`..` 一律拒；bash 只回传尾部 16 KB | `tools.py::_tool_read_artifact`：`if rel.is_absolute() or ".." in rel.parts:`；`_allowed_read_roots` 只有期目录与 `data/library`；`jobs.py`：`SHELL_TAIL_BYTES = 16384` | 成立。补充：候选还含 `data/library/notes/`；只放行 `READ_ALLOWED_SUFFIXES = frozenset({".md", ".txt", ".json", ".patch", ".log"})` |
| 5 | 写工具只放行 `01-topic.md` / `02-script.draft.md` / `07-titles.md`；母目录文件只能 bash 写 | `EPISODE_WRITABLE_FILES` 现为 **5 个**：另有 `02-script.md`（D47）与 `02-adversarial.md`（D48）。且 `session.py`：`AUTO_APPROVED_TOOLS = frozenset({"write_episode_file", "write_note", "write_memory", …})`——ADR-0030 决策 3 起写期文件**全部免卡**（含 `01-topic.md`），回退靠 `_agent/file-history/` 留底 | **上位需求「照 `01-topic.md` 的纪律弹卡」前提已不成立**。→ 交人裁决（Q4） |
| 6 | （安全相关，上位需求未提） | `_tool_write_episode_file`：`confirmed = bool(args.get("confirmed", False)) or ctx.confirmed`——**`confirmed` 可由模型参数给**；`llm.py`：`replace(context, confirmed=True) if (approve is not None or not bare) else context`——经过审查层的调用一律 `confirmed=True`，免卡与人批**在执行层无法区分** | `00-project.md` 不能沿用这条：Q4 要求「机读字段变了必须人批」，执行层必须能分辨「人在卡上批了」。→ PR-1 引入 `ToolContext.human_approved` 与卡上绑定（§4.4） |
| 7 | 跨期记忆是一份全局 `data/library/memory.md`（4000 字）；scope 只是日志字段 | `memory.py`：`MEMORY_REL_PATH = "data/library/memory.md"`、`BUDGET_CHARS = 4000`；`apply_op` 注释「D43 / Spec 17（ADR-0027）：不再有 scope 闸」 | 成立。补充：ADR-0030 决策 3 后 `write_memory` 也免卡（日志 `"confirm": "auto" if plan.requires_card else "card_free"`）；仍在的护栏是校验规则（R1–R9）、`MemoryBudgetError` 超额即拒、`provenance_ok` 来源确认。三条路径被 `_require_lib` 钉死在 `data/library/` 下，复用要先参数化（§4.5） |
| 8 | 镜头撞车只在一期之内查；对抗审查只读本期稿件 | `clips.py::_overlaps`（键 `(anime, season, episode)`、`OVERLAP_GAP = 0.5`）；`adversarial.py::gather_evidence` 只读 `ep / script_name` 与番剧笔记 | 成立 |
| 9 | 总装靠手写 `assemble.sh`，不在白名单；qc 已认 `04-assembly.json` | `qc.py`：`ASSEMBLY = "04-assembly.json"   # 总装期部件账本（assemble.sh 写，qc 读）`；`PIPELINE_MODULES` 无 assemble | 成立。补充两处：① `assemble.sh` 把参数写死（`PARTS=("01-借躯降生" "02-皮套囚徒" "03-终局葬礼")`、过场卡 `anullsrc=r=48000:cl=mono`、`-ac 1`），换一个系列就要改脚本；② 盘上账本与脚本现版本对不上：脚本对每个部件都写 `src` 键（`src = str(f.parent.resolve()) if kind == "part" and f.parent.name == "work" else None`，截尾件的值是 **`work/` 目录本身**，与它自己的注释「要回产出它的那一期」相反；过场卡与未裁的三卷写 `null`）；盘上则是过场卡**没有 `src` 键**、两个截尾件与未裁的三卷的 `src` 都是**卷目录**——**盘上账本不是现版本脚本产出的**，所以 PR-5 的「逐项一致」只比 `name / kind / offset / duration`，不比 `path / src`（红队 R-28 校正，2026-10-12 读盘核实） |
| 10 | （上位需求未提）工具表封顶 | ADR-0025 修订注：「第 15 个工具 `bash` … 封顶上调至 15；第 16 个须再立 ADR」；`config/agent/tools.json` 现 15 个 | 本 spec **不新增工具**：`00-project.md` 走 `write_episode_file`（按会话类型换白名单），企划记忆走 `write_memory` 加 `target` 参数，企划读域走 `read_artifact` 加 `area` 参数，总装与跨卷检查走 `run_pipeline` 新模块 |
| 11 | （上位需求未提）bash 绕过 | ADR-0030 起 bash 只在命中硬危险时弹卡。实测 `status_card.bash_danger_tags`：`cat ../02-皮套囚徒/02-script.md`、`python -m pipeline.assemble … --replace` 均返回 `[]` | 读域与写域规则**只约束读写工具**，bash 能读兄弟卷、能直接跑模块。这是 ADR-0030「已向人说明、由人接受的后果」。本 spec 只做两件事：总装命令经 bash 也弹卡（§4.8，与 D82 共用一条规则）；企划记忆被 bash 改写时由来源确认拦下注入（§4.5）。其余写进 §八 的残余风险 |

## 四、共用设计（各 PR 引用）

### 4.1 企划与卷的判定（树规则，Python 与 TS 同一条）

对 `data/episodes/` 下每个顶层条目 `d`（跟随符号链接判目录，跳过 `.` 前缀，`_` 前缀计入隐藏数，**与现规则相同**）：

- `d/00-project.md` 是文件，**或** `d` 有至少一个子目录 `s`（不以 `.`/`_` 开头，且 `d/s/01-topic.md` 存在）→ `d` 是**企划**。它的**成员**＝满足后一条件的全部子目录（含合集期）。
- 否则 `d` 是**普通期**（与现状相同）。
- 不往下嵌套第三层（上位需求 §3）。

数据形状：

- Python 新增 `cli.get_episode_tree(root) -> EpisodeTree(plain: list[Path], projects: list[ProjectEntry], hidden_underscore: int)`，`ProjectEntry(dir, members: list[Path], has_project_file: bool, mtime)`。
- `get_episodes_list` 保留，返回值不变（成员仍平铺，供 `print_board`、交互选期等既有调用）。它改成由 `get_episode_tree` 推导，**单一规则源**。
- TS `episodes.ts` 新增 `listEpisodeTree(root, fs)`，与 Python 同形；`listEpisodes` 同样由它推导。
- 排序：普通期与企划混排，按 mtime 降序（企划的 mtime = 企划目录与全部成员 mtime 的最大值）；企划内成员按目录名**码点序**升序（Python `sorted()` 是码点序；TS 必须显式按码点比较，**不能用默认 `sort()`**，后者按 UTF-16 码元，含增补平面字符时与 Python 不一致）。卷表顺序只在宿主拿到 `/project-meta` 后用于展示（§4.7），不进这条规则，TS 侧不解析 `00-project.md`。

期名解析（`resolve_episode_target`）收紧：

- 只接受 `data/episodes/<名>`、`data/episodes/<企划>/<卷>` 两种形状，以及解析后落在这两种位置上的绝对路径；**删掉 `rglob` 回退**（现在给 `"03-audio"` 会解析到某期的 `03-audio/` 当期目录）。
- 裸卷名在多个企划下同时存在 → 返回 `None` 并列出全部候选（`EGOIST-传奇企划志/01-借躯降生`、`EGOIST-传奇企划志-V2/01-借躯降生`），不取第一个。卷会话与企划会话里，裸卷名先在本企划内解析。
- 企划目录本身**不是**期：`resolve_episode_target`、`tools.resolve_episode_dir` 遇到企划目录一律拒（「这是企划目录，用企划会话」）。
- `_` 前缀的顶层目录不进列表，但可以显式指定（CLI、协议、`run_pipeline` 的人工命令），按同一判定认作企划或期——与今天 `_` 前缀验证期的口径相同（`print_board`：「已隐藏 N 个下划线验证/临时目录」）。§4.9 的镜像企划靠这一条。

**判定只有一处**（红队 R-13）：「这个目录是企划 / 卷 / 普通期」全部调 `cli.classify_dir(path) -> "project" | "volume" | "episode" | None`（内部就是上面的树规则），不许各处自己 `exists("01-topic.md")`。调用点：`get_episode_tree`、`resolve_episode_target`、`tools.resolve_episode_dir`、协议 `--project`（目标必须是企划，否则 `E_BAD_REQUEST`）、`ava new --project`（目标必须是企划；对普通期拒，防止把一个期悄悄变成「自己也有 `01-topic.md` 的企划」）、`adversarial` / `crosscheck` / `assemble` 认企划。母目录没有 `01-topic.md` 时（EGOIST V1 / V2、刚 `new-project` 的空企划）所有调用点给出同一个答案，由 §6.5 的「判定一致」测试逐处锁住。

**卷的身份按字面路径，不按 `resolve()`**（红队 R-5）：成员可以是软链卷（TI-5 夹具、§4.9 镜像）。`resolve()` 之后软链卷要么落到 `data/episodes` 之外（被形状检查拒掉），要么落到另一个企划下（镜像卷 → EGOIST V2 的卷，企划绑定随之错成 V2）。所以：
- 形状检查、企划归属（「这一卷属于哪个企划」）一律在**规范化后的字面路径**上判：`data/episodes/<企划>/<卷>`，企划 = 字面父目录。
- `resolve()` 只用于读写域的**包含判断**（§4.3、§4.4）与「部件文件落在成员目录内」（§10.3 ⑦）。
- 软链卷上开卷会话时，租约与 `session.jsonl` 照样经软链落在真实卷目录里——这是软链的本义，不拦；但 §4.9 的镜像因此**禁止开卷会话**（见 §4.9 第 3 步）。

### 4.2 三种会话

| 会话 | 绑定 | 会话日志 | 租约 | scope |
|---|---|---|---|---|
| 普通期会话 | 期目录 | `<期>/session.jsonl`（不变） | 期目录 | 按 `resolver.scope_of`（不变） |
| **卷会话** | 卷目录；宿主同时绑定所属企划目录（由树规则得出，**不取模型参数**） | `<卷>/session.jsonl` | 卷目录 | 同上 |
| **企划会话**（新） | 企划目录 | `<企划>/session.jsonl`（EGOIST V2 母目录已有的这份原样当历史） | 企划目录（`EpisodeLease` 照用） | 新 scope `project`（`config/agent/scopes/project.md`） |

- 协议入口：`python -m pipeline.agent.protocol --project <企划目录> [--continue [<前缀>] | --fresh]`；`ready` 帧加键 `session_kind`（`"episode" | "volume" | "project" | "idea"`）与 `project`（企划目录名或 `null`）。
- 企划会话能做的事（Q2）：读整个企划（§4.3）、写 `00-project.md`（§4.4）、写企划记忆与全局记忆（§4.5）、`run_pipeline` 只放行 `PROJECT_PIPELINE_MODULES = {"assemble", "crosscheck"}` 与不绑期目录的只读模块 `subgrep`；其余模块拒绝，拒因写明「这是卷级工序，到卷 `<卷>` 的会话里跑」。`status` / `check_script` 也拒：它们要一个期目录，企划会话的自动补位是企划目录，补上去结果无意义；看某卷进度用 `read_status(episode=<卷>)`。企划会话里 `write_episode_file` 只认 `00-project.md`。
- 卷会话：`run_pipeline` 额外放行只读的 `crosscheck`（自动补位为所属企划目录）；`assemble` 只在企划会话里放行。
- D82 落地后，企划会话的 `run_pipeline` 另放行 `tickets`（总装撞 bug 要能在跑总装的那个会话里开单；D82 spec §4.1）。放行集合写成 `PROJECT_PIPELINE_MODULES ∪ {"subgrep", "tickets"}`，`tickets` 在 D82 PR-A 之前不存在、白名单拒，不影响本 spec 先施工。
- **企划会话的期目录绑定**（红队 R-3）：`SessionHost.ep_dir` 与 `ToolContext.episode_dir` = 企划目录（租约、会话日志、审批流水、自动补位都要一个目录，沿用这一个字段）；另设 `ToolContext.session_kind = "project"` 与 `project_dir` = 同一目录。凡按 `episode_dir` 工作的工具在 `session_kind == "project"` 时逐个定口径，**不许落到默认分支**：
  - `write_episode_file`：只认 `00-project.md`，走 §4.4 的企划分支，不经 `resolve_episode_dir`（后者对企划目录一律拒）；
  - `cover_edit`、`acquire_propose`：拒，拒因「这是卷级操作，到卷 `<卷>` 的会话里做」（不拒的话封面与抓取提案会写进母目录）；
  - `run_pipeline`：见上；`read_artifact`：§4.3；`write_memory`：§4.5；
  - 审查层与实现层各判一次（同 `NEEDS_EPISODE_TOOLS` 的双保险口径）。
- 企划会话不处理停机点：不读 `_agent/approvals_store.json`（停机点状态的来源），每回合照 S9-R4 发**恰好一帧空的** `stop_points{items: []}`（与 idea 会话同口径；宿主 `sessions.ts` 靠这一帧结束回合收尾，不发会卡住排队消息）。`_agent/approvals.jsonl` 是审批流水（`log_approval_decision` 写），企划会话照写，不是停机点状态（红队 R-7 校正）。

### 4.3 读域（`read_artifact`）

新增可选参数 `area`，取值 `"episode" | "project" | "library"`；不给时沿用现有候选顺序，并在末尾追加「企划顶层」候选（只在卷会话、且 `rel` 只有一段时）。

| 会话 | 允许的根（全部先 `resolve()`） | 额外条件 |
|---|---|---|
| 普通期 | 期目录、`data/library/`（不变） | — |
| 卷会话 | 卷目录、`data/library/`，**加上企划顶层文件** | 企划顶层候选：`rel` 只有一段，且 `target.resolve().parent == 企划目录.resolve()`。兄弟卷一律不在读域里 |
| 企划会话 | 企划目录，及每个成员目录各自的 `resolve()`（成员可以是人建的软链卷，TI-5 的夹具里就有），加上 `data/library/`（与其他会话一致；`area=library` 在企划会话里要有可读的根，红队 R-14） | — |

所有会话一律：

1. `rel` 是绝对路径或含 `..` → 拒（不变）。
2. **双端 resolve**：每个根先 `resolve()`；候选 `target = (根 / rel).resolve()`；只有 `target == 根 or 根 in target.parents` 才算在域内。软链（文件或目录）指出域外 → 不在域内。
3. **硬排除**：`deny_dir_hit` 对 `rel` 与 `resolve()` 后的 `target` 各判一次，casefold 后命中 `03-audio` / `04-patch` 任何一段即拒（不变，企划会话的跨卷路径如 `02-皮套囚徒/03-AUDIO/manifest.json` 同样命中）。
4. **记忆文件不可读**：`_is_memory_file` 扩成「`resolve()` 后的路径 casefold 等于全局 `memory.md` 或 `memory.log.jsonl`，**或** basename casefold 为 `00-memory.md` / `00-memory.log.jsonl`」。按 basename 判、不按位置判：经软链（`notes.md -> 00-memory.md`）读到的 `target` basename 仍是 `00-memory.md`，照拒。日志也要列进来：日志的状态行带 `result_text`（记忆全文），读它等于绕过来源确认读记忆。
5. 后缀白名单不变，但**对 `rel` 与 `resolve()` 后的 `target` 各判一次**（红队 R-15）：现状只判 `rel.suffix`，软链 `x.json -> 00-memory.log.jsonl` 能以 `.json` 的名义读到 `.jsonl` 的内容。全局 `memory.log.jsonl` 今天就有这条旁路（要先用 bash 建软链，而 bash 本来就能读，所以是 P2），本 spec 一并补上。

卷会话**不能借读兄弟卷**的全部路径：`../02-x/…`（`..` 拒）、`area=project` 加 `02-x/02-script.md`（企划候选只认一段）、企划顶层放一个指向兄弟卷文件的软链（`resolve().parent` 不是企划目录）、卷内放一个指向兄弟卷的软链（`resolve()` 后不在卷根内）。兄弟卷的稿子只经 C-3 的受控通道进来（PR-3）。

### 4.4 写域、弹卡与卡上绑定

**`write_episode_file` 的白名单按会话类型换**：

| 会话 | 白名单 |
|---|---|
| 普通期 / 卷 | `EPISODE_WRITABLE_FILES`（5 个，不变；**不含** `00-project.md`） |
| 企划 | `PROJECT_WRITABLE_FILES = {"00-project.md"}` |

写 `00-project.md` 的规则（Q4）：

1. 写前解析：`after` 必须能被 §4.7 的解析器解析，不能就在弹卡前拒（与 D48「content / edits 不成立在弹卡前拒」同口径）。
2. 比对 `before` 与 `after` 的**机读字段**（企划名、合集、卷表各行的五列）：
   - 不同（含 `before` 不存在，即首次立项；含 `before` **不可解析**——人手改坏或 bash 写坏后由模型修，红队 R-16）→ **弹卡**，卡面是机读字段的逐行 diff + 全文 diff；不开放「本会话都批准」（`allow_key = None`）。
   - 相同（只改了散文）→ 免卡。
3. 覆盖前留底到 `<企划>/_agent/file-history/`（与 ADR-0030 给 `01-topic.md` 补的留底同一函数 `_keep_history`）。
4. 目标路径：`(企划目录 / "00-project.md").resolve().parent == 企划目录.resolve()`，否则拒（`00-project.md` 本身是指向别处的软链时拒）。企划目录本身另走一个 `resolve_project_dir`（与 `resolve_episode_dir` 同样的三种越界检查，再加 `classify_dir == "project"`），**不经** `resolve_episode_dir`——后者按 §4.1 对企划目录一律拒（红队 R-3）。
5. 确认**只从宿主来**：执行层只看 `ctx.human_approved` 与 `ctx.bound`，**不读 `args` 里任何同名键**（`confirmed`、`human_approved`、`bound`）。模型在参数里塞 `human_approved: true` 与正确的 `base_sha`，结果与不塞相同（§6.5 测试、§6.6 M6）。

**卡上绑定（新机制，PR-1 引入；本期只给 `00-project.md` 用，机制是通用的）**——解决 §三 #6「执行层分不清免卡与人批」：

- `ToolVerdict` 增加 `bind: dict | None`：弹卡时写入卡面所依据的状态（这里是 `before` 的 sha256）。
- `Session._review` 只在人在卡上批准时（现有代码返回 `Decision(ok=True, provenance="human")` 的那一支）把 `bind` 原样带进 `Decision`；`session_allow`、`card_free`、旧 REPL 的 `approve_cb` 一律不带。
- `llm.py` 组装 `exec_ctx` 时，除现有的 `confirmed=True` 外，再设 `human_approved = (decision.provenance == "human" and decision.ok)` 与 `bound = decision.bind`。
- `_tool_write_episode_file` 写 `00-project.md` 时**在执行层重算**：若机读字段有变，要求 `ctx.human_approved` 为真**且** `ctx.bound["base_sha"]` 等于此刻盘上 `before` 的 sha（文件在卡片之后被改过 → 拒，「文件在你看卡之后变了，重新提交」）。审查层与执行层各判一次：绕过审查层的裸调用、审查与执行之间被 bash 改过文件，都在执行层拦下。

### 4.5 企划记忆

`memory.py` 参数化为 `MemoryStore`（目录、正文、日志、锁、预算、注入头、告警前缀、证据引用范围）。全局记忆是一个实例，行为逐字节不变；企划记忆是另一个实例：

| 项 | 全局 | 企划 |
|---|---|---|
| 正文 | `data/library/memory.md` | `<企划>/00-memory.md` |
| 日志 / 锁 | `data/library/memory.log.jsonl` / `memory.lock` | `<企划>/00-memory.log.jsonl` / `<企划>/00-memory.lock` |
| 三路径同目录、双端 resolve、绝不 mkdir | `_require_lib` | 同一检查，目录换成企划目录（企划目录不存在 → 拒，不建） |
| 预算 | `BUDGET_CHARS = 4000` | `PROJECT_MEMORY_BUDGET_CHARS = 3000`（Q3），压力线 2597 |
| 证据引用 | 任意期 | 只认**本企划的成员**（`<企划>/<卷>`），引用别处 → `MemoryRuleError` |
| 注入头 | `INJECTION_HEADER` | 「[企划记忆 · `<企划名>` · 经验参考，非规则] …」，同样声明不是指令 |
| 免卡 / 弹卡 | ADR-0030：免卡，日志 `confirm: auto` | 同全局（Q4） |

- `write_memory` 加参数 `target: "global" | "project"`（默认 `global`，向后兼容）。`project` 只在卷会话与企划会话里有效，企划目录取自 `ToolContext`（宿主绑定），**不取模型参数**；普通期会话给 `project` → 拒。
- 沿用 `apply_op` 的全部纪律：唯一写入口、锁内重读重规划、`requires_card and not confirmed` 拒、参数表外键拒、超额即拒（`MemoryBudgetError`，绝不截断、绝不自动淘汰）。
- **来源确认**：企划记忆注入前跑同一套 `provenance_ok`（正文 sha 对日志状态行）。bash 直接改了 `00-memory.md` → 注入改发告警（`[企划记忆未注入] …`）+ notice `project_memory_unconfirmed`，人用 `/memory --project ack`（或协议 `command{memory_ack, target:"project"}`）看 diff 后对账。**它拦的是「只改正文」**：日志 `00-memory.log.jsonl` 与正文同在企划目录、同样 bash 可写，同时伪造正文与一行状态（自算 sha256）就能过——与全局记忆今天的处境相同，写进 §十二 残余风险，不宣称能拦蓄意伪造（红队 R-17）。
- 教训分两层（上位需求 B）：跨系列通用的进全局记忆或 `config/voice.json`；只属于本系列的进企划记忆。这条写进 `config/agent/scopes/project.md` 与 `creative.md` 的记忆段，不做机器判定（「是不是本系列独有」是判断，不是规则）。

### 4.6 上下文注入（卷会话与企划会话）

- 卷会话与企划会话在首轮（及压缩后未保留时）各注入一条 user 消息「[企划 · `<企划名>`]」：
  - `00-project.md` 全文 ≤ `PROJECT_DOC_INJECT_MAX_CHARS` → 注入全文；
  - 超过 → 注入机读头 + 卷表 + 散文各节的标题列表 + 一行「全文：`read_artifact(path="00-project.md", area="project")`」。**不从中间截断**；
  - 解析失败 → 注入前 `PROJECT_DOC_INJECT_MAX_CHARS` 字原文 + 一行解析错误（人要看到问题，模型也要知道卷表不可信）。
- 企划记忆**不看** `config/agent/assembly.json` 的 `memory.scopes`：卷会话与企划会话一律注入（Q6，人 2026-10-12 裁决）。理由：ADR-0023 补记「pipeline scope 不单独注入」的前提是「整期共用一份历史」，而现在一卷开 3–8 个会话，03 之后新开的会话按旧开关看不到任何记忆；企划记忆里的读音取舍、封面参数恰好在 03 / 08 才用得上。全局记忆的门控不变；`memory.scopes` 加 `project` 只管企划会话里的**全局**记忆。
- 注入文本先过 `scrub_restricted`：`00-project.md` 不是可信仓库文档（Spec 16 豁免只认仓库文档），里面若写了 `03-audio/manifest.json` 这类字样，不脱敏就会让整轮请求被出网断言拦下。
- **新鲜度**：已注入两份文本的 sha 记在 `tracker` 上（新字段；提交注入消息时照现有写法用 `_doc_shas` 把 sha 记进日志，供恢复时重置）。每轮开始重算；`00-project.md` 或企划记忆变了 → 本轮再注入一条「（已更新）」版本。企划会话改了卷表，正开着的卷会话下一轮就能看到。**这是新行为**：全局记忆今天只在会话内注入一次（`_inject_memory` 见 `MEMORY_REL_PATH in tracker.injected_paths` 就返回），变了只在恢复后的第一轮由 `_reinject_changed` 补；本 spec 不改全局记忆的这一行为（红队 R-18 校正「沿用」的说法）。

### 4.7 `00-project.md` 格式与解析（Q1：单文件）

```markdown
# 00 企划

企划: EGOIST 传奇企划志
番: EGOIST
合集: 00-全一期-典藏版

## 卷表

| 卷序 | 目录 | 年份 | 时长预算 | 片尾 |
|---|---|---|---|---|
| 1 | 01-借躯降生 | 2011–2014 | 14-16分钟 | 余韵18秒 |
| 2 | 02-皮套囚徒 | 2015–2019 | 14-16分钟 | 余韵18秒 |
| 3 | 03-终局葬礼 | 2020–2023 | 18-20分钟 | 完整保留 |

## 总纲
（人写的散文，不解析）

## 标题模板
EGOIST编年史：第X期 <四字主题>——<反差金句>（年份跨度）

## 封面模板
主标 EGOIST 编年史 · 副标 借 躯 降 生 · 微标 [ 第一期 ]
横版：宋体 Heavy 136/122/36px，右对齐 X=W-120，Y=360 ……
```

解析器 `pipeline/project.py::parse(text) -> ProjectDoc | ParseError`（唯一一处；宿主经 core 管理命令 `/project-meta --dir=<企划>` 取结果，TS 不另写解析器）：

| 字段 | 规则 |
|---|---|
| 机读头 | 第一个 `## ` 标题之前、形如 `键: 值` 的行。认 `企划`（必填）与 `合集`（选填）；同一键出现两次 → 错。其他行（含 `番:`）是说明文字 |
| `企划` | 单行，1–`PROJECT_NAME_MAX_CHARS` 字，不含控制字符 |
| `合集` | 单段目录名：过 `cli._episode_name_problem`，不等于任何卷目录。存在性不在解析期判（立项时合集期还没建），PR-5 总装时判 |
| 卷表 | `## 卷表` 下第一张表；表头必须恰为 `卷序 / 目录 / 年份 / 时长预算 / 片尾` 五列、按此顺序 |
| `卷序` | 从 1 起的连续整数 |
| `目录` | 单段目录名，过 `_episode_name_problem`；各行不重复——**按 NFC 后 casefold 比较**（APFS 大小写不敏感：`01-A` 与 `01-a` 是同一个目录，按原文比较会让同一卷进两次合集，红队 R-19）；`合集` 与各行目录的「不相等」同样按此口径。**不判存在**（先写卷表再建卷是正常顺序），目录与成员对不上的情况由状态卡报提示（「卷表列了 `04-x` 但目录不存在」「目录 `05-y` 不在卷表」），不拦 |
| `年份` | 单行，≤ `YEAR_SPAN_MAX_CHARS` 字，不解析内容 |
| `时长预算` | 与 `01-topic.md` 的 `时长目标` 同一语法（复用 `check_script.DURATION_FIELD` 的数字段）：`A-B分钟`，`0 < A ≤ B` |
| `片尾` | `完整保留` 或 `余韵N秒`，N 为整数，`1 ≤ N ≤ TAIL_MAX_S`；**最后一行必须是 `完整保留`**（EGOIST 教训：末卷片尾完整保留、不裁） |

机读字段 = `(企划, 合集, 卷表全部行)`；§4.4 的弹卡判定只比这组值（解析后比较，空白与表格对齐方式的差异不算变化）。

### 4.8 总装、跨卷检查与 bash 绕卡

- `assemble` 每次弹卡（Q5）；带 `--replace` 时卡面首行写「⚠ 将覆盖已有 05-final.mp4（不留底）」。两种都不开放「本会话都批准」：`assemble` 进 D82 引入的「逐条人审的模块命令」集合 `tools.HUMAN_GATED_COMMANDS`。
- `assemble` 的 argparse 用 `allow_abbrev=False`：`--rep` 这类前缀缩写不被认作 `--replace`。
- **bash 绕卡**（§三 #11）：`status_card.bash_danger_tags` 加一条规则——命令里出现 `pipeline.<模块>` 或 `pipeline/<模块>.py`，且该模块（或模块 + 子命令）在「逐条人审的模块命令」里 → 标 `[须人审的流水线命令]`，照危险命令弹卡。这条规则的实现与测试在 D82 spec 的 PR 里落地（D82 先施工，`tickets open` 也要它）；本 spec PR-5 只往集合里加 `assemble`。
- `crosscheck` 默认只读、免卡；带 `--write`（把报告写进企划目录）时按普通白名单命令处理（ADR-0030 同样免卡）。只读判定按参数级做（不含 `--write` 才算只读，照 `_readonly_asset_subcommand` 的口径），**不能整模块进 `READONLY_PIPELINE_MODULES`**——该集合的注释要求成员「都不落盘」。

### 4.9 真实数据的只读镜像（所有用 EGOIST V2 的验收共用）

`data/episodes/EGOIST-传奇企划志-V2/` 只读。需要它的验收一律按下列步骤做，步骤由人执行或人批卡：

1. `marker=$(mktemp)`（记下开始时刻）。
2. 建顶层镜像企划 `data/episodes/_d81-mirror/`（`_` 前缀：不进期列表，但能显式指定，见 §4.1 末条）：
   - `00-project.md` 写 §4.7 的样例；
   - 三卷各建一个**指向原目录的软链**：`01-借躯降生 -> ../EGOIST-传奇企划志-V2/01-借躯降生` 等。**典藏版不进镜像**：`合集:` 行指向的成员不存在时，任何漏写 `--out` 的总装命令在前置检查就退 2，不会把成品目录当默认输出。
   另建输出目录 `data/episodes/_d81-out/`（普通目录，不是镜像的成员）。
3. 镜像上跑验收命令。一切输出只落在 `_d81-mirror/`（会话日志、租约）与 `_d81-out/`（报告、成片）内；经软链进入的卷目录**不得**成为任何写入的目标——这正是 PR-3 `--out` 与 PR-5 `--out` 存在的理由。由此**镜像里的卷一律不开卷会话**（协议、桌面端、`ava` REPL 都不行）：卷会话的租约与 `session.jsonl` 会经软链写进 EGOIST V2 的卷目录（§4.1 末条）。需要卷视角的验收只用 CLI + `--out`（PR-3），企划视角用企划会话（企划目录 `_d81-mirror/` 本身不是软链）。
4. `find "data/episodes/EGOIST-传奇企划志-V2" -newer "$marker"` **输出必须为空**，贴进施工记录。
5. 验收结束后两个 `_` 目录的去留由人定（删除属红线，agent 不自行删）。

成员是软链卷，这正是 §4.1 允许、§4.3 读域按成员 `resolve()` 放行的形状。

## 五、PR 切分与顺序

| 顺序 | PR | 内容 | 动到 | 前置 |
|---|---|---|---|---|
| 1 | **PR-1** | A + B 的 core：树规则（Py + TS 镜像）、期名解析收紧、企划会话协议、读域、`00-project.md` 写入与卡上绑定、企划记忆、注入、`ava new-project` / `ava new --project` | core、host（树规则镜像、`ready` 键、`ConvKey`、spawner 模板）；**不动 renderer** | 无 |
| 1′ | **PR-2a** | 截图选型：企划分组 / 进度一览 / 新建企划与卷的入口，2–3 套真实窗口方案 | 只加附件，不改产品代码 | 无，可与 PR-1 并行 |
| 2 | **PR-2b** | 按人选方案施工侧栏分组、企划会话入口、**E 进度一览**、新建企划 / 卷 | renderer + host | PR-1、PR-2a 人已选 |
| 3 | D82 | 见姊妹 spec | — | 无硬依赖（时机排序）；它引入的 bash 绕卡规则是 PR-5 的前置 |
| 4 | **PR-3** | C-3：02.8 对抗审查在卷会话里带上前卷定稿 | `pipeline/adversarial.py` | PR-1 |
| 5 | **PR-4** | C-1 / C-2：`pipeline.crosscheck` 镜头与配乐跨卷重复 | 新模块 + 白名单 | PR-1 |
| 6 | **PR-5** | D：`pipeline.assemble` | 新模块 + 白名单 + 卡 | PR-1；D82 PR 的 bash 绕卡规则 |

与交接时建议的顺序（D81-A+B → D82 → C → D → E）相比的三处调整：

1. **E 并进 PR-2b，不单独排最后**。E 只是把各卷已有的 `currentStep` 显示在企划下面，和分组是同一块屏幕。分开做就要出两轮截图、人选两次；而且第 6 步场景（扫一眼各卷进度）在第二卷开工时就要用，排到最后会拖到最后一卷。
2. **A+B 拆成 PR-1（core）与 PR-2（UI）**。core 只动安全语义与状态一致性（读写域、卡、记忆、租约），适合红队与变异；UI 按规则 4 由人选。两者分开，core 出问题能单独回退，截图选型也能和 core 施工并行，不拖第一卷开写的时间。
3. C 拆成 PR-3（C-3）与 PR-4（C-1/C-2），按上位需求 §4 的时机：C-3 在第二卷写稿时要用，C-1/C-2 在第二卷排片前要用。

---

## 六、PR-1：企划成为一等实体 + 上下文多一层企划（A + B 的 core）

### 6.1 目标与对应 issue

D81 A、B。完成后：CLI / 协议层能建企划、开企划会话与卷会话；读写域、记忆、注入按 §4 生效；桌面端此时仍是旧侧栏（成员按 `epKey` 平铺），但宿主数据已带树形，企划会话可经协议打开（e2e 用）。

### 6.2 前置条件

- `uv run pytest` 基线全绿（执行代理守则）。
- 不需要数据盘：全部测试用临时根（`get_episodes_list(root=…)` 已支持注入）。§6.9 的真实数据验收需要 T7。

### 6.3 改动清单

| # | 文件 | 锚点（原文） | 改动 |
|---|---|---|---|
| ① | `pipeline/agent/cli.py` | `def get_episodes_list(root: Path \| None = None) -> tuple[list[Path], int]:` | 新增 `get_episode_tree`（§4.1）；`get_episodes_list` 改为由它推导，返回值不变 |
| ② | 同上 | `def resolve_episode_target(target: str) -> Path \| None:` 内 `for matched in ep_root.rglob(target):` | 删 `rglob` 回退；`glob("*/target")` 多于一个 → `None` + 候选清单（新函数 `resolve_episode_candidates` 返回列表，`resolve_episode_target` 包它）；**规范化后的字面路径**必须落在 §4.1 的两种形状上（不在 `resolve()` 之后判，§4.1 末条）；企划目录 → 拒。现状第一支 `Path(target).exists()` 认当前工作目录下的任何目录（如 `pipeline`），一并收进形状检查。新增 `cli.classify_dir`（§4.1「判定只有一处」） |
| ③ | `desktop/src/host/episodes.ts` | `if (subEps.length > 0) {` | 新增 `listEpisodeTree`，与 ① 同形；成员排序用码点比较函数；`listEpisodes` 由它推导 |
| ④ | `pipeline/project.py`（新） | — | §4.7 解析器、`machine_fields(doc)`、`diff_machine(before, after)`、`members_vs_table(project_dir, doc)`（状态卡提示用）、常量（§6.4） |
| ⑤ | `pipeline/agent/protocol.py` | `idea = "--idea" in args` 那段参数解析；`"episode": ep_dir.name if ep_dir is not None else None,` | 加 `--project <企划目录>`（与 `--idea`、期目录互斥）；取企划目录租约；`ready` 帧加 `session_kind`、`project`；企划会话的常驻层按 `project` scope 装配，状态卡用 ⑨ |
| ⑥ | `pipeline/agent/tools.py` | `class ToolContext:` | 加 `project_dir: Path \| None`、`session_kind: str`、`human_approved: bool = False`、`bound: dict \| None = None`。四项都由宿主填，**不取模型参数** |
| ⑦ | 同上 | `def _allowed_read_roots(ctx: ToolContext) -> list[Path]:`、`def _is_memory_file(target: Path, ctx: ToolContext) -> bool:`、`_tool_read_artifact` 的 `candidates` 段 | §4.3：按会话类型给根；`area` 参数；企划顶层候选；记忆文件按 basename casefold 判。`TOOL_SCHEMAS["read_artifact"]` 加 `area` 枚举与说明 |
| ⑧ | 同上 | `def write_episode_file(`、`_tool_write_episode_file` 的 `confirmed = bool(args.get("confirmed", False)) or ctx.confirmed` | §4.4：白名单按会话类型；`00-project.md` 走解析 → 机读 diff → 执行层重算 + `human_approved` + `bound["base_sha"]`；留底；`resolve_episode_dir` 拒企划目录 |
| ⑨ | `pipeline/agent/status_card.py` | `def build_status_card(` | 新增 `build_project_card(project_dir)`：企划名、卷表（每卷 `inspect_episode` 的 `current_step`）、合集期是否有成片、`00-project.md` 解析错误、`members_vs_table` 提示；卷会话状态卡首行加「企划 `<企划>` · 第 N 卷」（N 取卷表，不在卷表则写「不在卷表」） |
| ⑩ | `pipeline/agent/session.py` | `def review_tool_call(`；`AUTO_APPROVED_TOOLS = frozenset(`；`_review` 里 `return Decision(ok=True, provenance="human")` | `write_episode_file` 的 `00-project.md` 分支：解析失败拒；机读字段有变 → `ask`（`bind={"base_sha": …}`、无 `allow_key`）；否则 `allow`。`ToolVerdict.bind` 只在人批的那一支带进 `Decision` |
| ⑪ | `pipeline/agent/llm.py` | `class Decision:`；`replace(context, confirmed=True) if (approve is not None or not bare) else context` | `Decision` 加 `bind: dict \| None = None`；`exec_ctx` 同时设 `human_approved`、`bound`（§4.4） |
| ⑫ | `pipeline/agent/session.py` | `def _inject_memory(` | 新 `_inject_project`（§4.6）：两条注入（企划文件、企划记忆），sha 进 tracker；每轮比对 sha，变了再注入「（已更新）」；压缩后保留判定同 `_kept_injected` 机制 |
| ⑬ | `pipeline/agent/memory.py` | `MEMORY_REL_PATH = "data/library/memory.md"`、`def _require_lib(`、`def apply_op(`、`def render_injection(`、`def provenance_ok(` | §4.5：`MemoryStore` 参数化；全局实例保持现有常量与行为；企划实例；证据引用范围检查；`/memory --project` 的 show / check / ack |
| ⑭ | `pipeline/agent/tools.py` | `write_memory` 的 schema 与 `_tool_write_memory`（「confirmed 只取 ctx.confirmed，**不读 args["confirmed"]**」） | 加 `target` 参数；`project` 只在卷 / 企划会话有效，企划目录取自 `ctx.project_dir` |
| ⑮ | `pipeline/agent/tools.py` | `def _tool_list_episodes(` 的 `"name": ep.name,` | `name` 改为相对 `data/episodes/` 的键（`EGOIST-传奇企划志-V2/01-借躯降生`）；加 `project`（企划名或 null）与 `projects` 列表（企划键、`has_project_file`、成员键） |
| ⑯ | `pipeline/agent/tools.py` | `_tool_read_status` 的 `ep_dir = resolve_episode_target(target)` | 卷 / 企划会话里裸卷名先在本企划内解析；歧义时返回候选，不猜 |
| ⑰ | `pipeline/agent/tools.py` | `PIPELINE_MODULES: set[str] = {`；`run_pipeline` 的自动补位 | 企划会话：模块不在 `PROJECT_PIPELINE_MODULES ∪ {"subgrep", "tickets"}` → 拒并指路（`tickets` 见 §4.2，D82 落地前它本就不在白名单）；补位目标为企划目录（`assemble` / `crosscheck` 的模块本身在 PR-4 / PR-5 才加，PR-1 只放好分流与拒因，用测试替身模块验证） |
| ⑱ | `pipeline/agent/cli.py` | `def create_new_episode(ep_name: str) -> int:`、`main` 里 `if args and args[0] == "new":` | 新 `ava new-project <企划名>`：建 `data/episodes/<企划>/` + 骨架 `00-project.md`（机读头只有 `企划:`，卷表空表头——空卷表合法，§4.7 解析器对 0 行卷表不报错，但 PR-5 总装拒）；`ava new --project <企划> <卷名>`：在企划下建卷 + `01-topic.md` 模板。两者都沿用 `_episode_name_problem`、`require_data_at`、绝不建 `data/`。新管理命令 `/project-meta --dir=<企划>`（宿主取解析结果） |
| ⑲ | `desktop/src/shared/protocol.ts` | `export type ConvKey = "idea" \| \`ep:${string}\`;`；`export interface EpisodesList {` | `ConvKey` 加 `` `proj:${string}` ``；`EpisodesList` 加 `projects: ProjectSummary[]`（企划键、`hasProjectFile`、成员 `EpisodeSummary[]`、企划会话的 `conv`） |
| ⑳ | `desktop/src/shared/convFrames.ts` | `ready: {` 的必需键表 | 加 `session_kind: ["string"]`、`project: ["string", "null"]`；TX-0（`desktop/e2e/sessionReal.spec.ts` 的 `test("TX-0 契约：…")`，真实 core 实发键 vs 表）扩到期 / 卷 / 企划三种会话 |
| ㉑ | `desktop/src/host/spawner.ts`、`sessions.ts`、`service.ts` | `case "SESSION_IDEA":`；`const { visible, hiddenUnderscore } = listEpisodes(join(this.dataRoot, "episodes"), this.deps.fs);` | 模板 `SESSION_PROJECT`（`--project <abs>`，abs 由宿主映射并刚 stat 过，同 `SESSION_NEW` 纪律）；会话管理认 `proj:` 键；`service` 改用 `listEpisodeTree` 产出 `projects` |
| ㉒ | `config/agent/scopes/project.md`（新）、`config/agent/assembly.json`、`config/agent/scopes/creative.md` | `"scopes": ["creative", "asset", "idea"]` | 新 scope 提示词（企划会话能做与不能做的事、记忆两层分法）；`memory.scopes` 加 `project`（只管企划会话里的全局记忆；企划记忆不受这张表门控，Q6）；`routes` 加 `project: {"default": []}`；`creative.md` 记忆段加两层分法一句 |
| ㉓ | `.gitignore` | — | 不改（企划目录在 `data/` 下，本来不进 git） |
| ㉔ | `pipeline/agent/tools.py`、`session.py` | `def _tool_cover_edit(`、`def _tool_acquire_propose(` 的 `if not ctx.episode_dir:`；`review_tool_call` 的 `if ep_dir is None and name in NEEDS_EPISODE_TOOLS:` | 企划会话（`session_kind == "project"`）里 `cover_edit`、`acquire_propose` 审查层与实现层各拒一次，拒因指向卷会话（§4.2，红队 R-3） |
| ㉕ | `pipeline/agent/protocol.py` | `writer.send({"t": "stop_points", "items": [], "turn_id": turn_id})`（idea 那一支） | 企划会话每回合（含 `/compact` 回合）走同一支，发恰好一帧空 `stop_points`（§4.2，红队 R-7） |

### 6.4 新常量与取值理由

| 常量 | 值 | 理由 |
|---|---|---|
| `PROJECT_FILE` | `"00-project.md"` | 上位需求定名；`00-` 前缀让它在 Finder 与 `ls` 里排在卷目录（`01-`…）之前，与 EGOIST 母目录现有 `00-project-plan.md` 习惯一致 |
| `PROJECT_MEMORY_FILE` / `_LOG` / `_LOCK` | `00-memory.md` / `00-memory.log.jsonl` / `00-memory.lock` | 三路径同目录是 `memory.py` 现有不变量（`_require_lib`：`if path.parent != lib`），企划实例照搬 |
| `PROJECT_MEMORY_BUDGET_CHARS` | 3000 | 人裁决 Q3（2026-10-12） |
| 企划记忆压力线 | 2597 | 原公式 `BUDGET − (ENTRY_MAX_CHARS + len(SEP))`，不另定 |
| `PROJECT_DOC_INJECT_MAX_CHARS` | 8000 | 与 `DIGEST_MAX_CHARS = 8000` 同一依据：「不大于仓库里最重的单篇注入文档」——今天最重的是 `skills/write-script/SKILL.md`（实测 8958 字）。两条路径都有真实样本：EGOIST 总纲原稿 `00-project-plan.md` 实测 **5455 字**（11822 是 UTF-8 字节数，原稿误记为字数，红队 R-20），走全文分支；踩坑全录 `00-production-lessons.md` 实测 10075 字，若并进 `00-project.md` 就走「机读部分 + 章节目录」分支 |
| `PROJECT_NAME_MAX_CHARS` | 40 | 企划名进状态卡首行与侧栏（侧栏 1280 宽一行约 13 字，见 D80 §六），40 字给长名留余量；超了多半是把一句话当成了名字 |
| `YEAR_SPAN_MAX_CHARS` | 20 | `2011–2014`、`2020–2023（终场）` 这类写法都在 15 字内；超了说明写进了别的内容 |
| `TAIL_MAX_S` | 60 | 只做形状检查：余韵超过一分钟就不是「片尾余韵」而是一段音乐。EGOIST 实际取 18（README 红线 15–20），具体值由人在卷表里写，代码不替人定 |
| `PROJECT_PIPELINE_MODULES` | `{"assemble", "crosscheck"}` | 人裁决 Q2：企划会话只做规划、跨卷审查、总装 |

### 6.5 测试要求

期望值先在实现上跑一遍、看懂为什么是这个值再写进断言；每条安全规则配一条变异（§6.6）。

**pytest**（`tests/test_project_layer.py` 新，另扩既有文件）

- 树规则：临时根下造「普通期」「有 `00-project.md` 无成员的新企划」「无 `00-project.md` 有成员的旧企划（V1 形状）」「成员含软链卷」「企划目录自己也有 `01-topic.md`」「`_` 前缀成员」「第三层子期」，断言分组、隐藏数与排序。`get_episodes_list` 的基准（红队 R-9）：**不含 `00-project.md` 的夹具**输出与改动前逐项相同（取改动前的实现跑一遍作基准，写进夹具）；「有 `00-project.md` 无成员的新企划」是**有意的行为差异**——改动前它被当成普通期列出，改动后它是企划、不进平铺列表——单独断言，不进逐项相同的基准。
- 判定一致（红队 R-13）：同一组夹具上，`get_episode_tree`、`resolve_episode_target`、`resolve_episode_dir`、协议 `--project`、`ava new --project` 对每个目录给出同一判定（企划 / 卷 / 普通期）；`ava new --project <普通期>` → 拒；协议 `--project <普通期>` → `E_BAD_REQUEST`。
- 期名解析：V1 / V2 同名卷 → `None` + 两个候选；`"03-audio"` → `None`；`"pipeline"`（工作目录在仓库根）→ `None`；企划目录 → 拒；绝对路径在 `data/episodes` 之外 → `None`。
- 软链卷身份（红队 R-5）：夹具企划 `M/` 的成员 `M/01-a -> ../P/01-a`（`P` 是另一个企划）：`resolve_episode_target("M/01-a")` 的企划归属是 `M`（不是 `P`）；成员 `M/02-x -> <data 之外>` 同样认作 `M` 的卷；卷会话首轮注入的卷表来自 `M/00-project.md`。
- 读域（每条都在**卷会话**与**企划会话**各跑一遍）：
  - 卷会话读 `00-project.md`（无 `area`、`area=project` 两种）→ 可读；
  - 卷会话读兄弟卷：`../02-b/02-script.md`、`area=project` + `02-b/02-script.md`、企划顶层软链 `evil.md -> 02-b/02-script.md`、卷内软链 `x.md -> ../02-b/02-script.md` → 四条全拒；
  - 企划会话读 `02-b/02-script.md` → 可读；读软链成员内的文件 → 可读；企划内软链指向另一个企划的文件 → 拒；
  - `03-AUDIO/manifest.json`、`02-b/04-Patch/x.json`（大小写混写）→ 拒；目录软链 `audio -> 03-audio` 再读 `audio/manifest.json` → 拒（resolve 后命中）；
  - `00-memory.md`、`00-MEMORY.md`、软链 `m.md -> 00-memory.md`、全局 `memory.md` → 全拒；
  - 软链 `x.json -> 00-memory.log.jsonl`、`y.json -> <data/library>/memory.log.jsonl`、`z.md -> session.jsonl` → 全拒（resolve 后的后缀与记忆日志 basename 两条规则，红队 R-15）；
  - 企划会话 `area=library` 读 `notes/<番>.md` → 可读（红队 R-14）。
- 写 `00-project.md`：
  - 卷会话写 `00-project.md` → 拒；企划会话写 `01-topic.md` → 拒；
  - 首次写（无 `before`）→ `ask`；`before` 不可解析（缺 `企划:`）→ `ask`；只改散文 → `allow` 且留底；改卷表一格 → `ask`，卡面含该格 diff；只改表格对齐空白 → `allow`；卷表两行目录 `01-A` / `01-a` → 弹卡前 `reject`；
  - 解析失败（缺 `企划:`、卷序不连续、末行余韵、目录含 `/`）→ 弹卡前 `reject`；
  - **执行层**：不经审查层直接调 `_tool_write_episode_file`，`ctx.confirmed=True` 但 `human_approved=False`，机读字段有变 → `PermissionError`；`human_approved=True` 但 `bound["base_sha"]` 与盘上不符（模拟卡后被改）→ `PermissionError`；**`ctx.human_approved=False`、`args` 里带 `human_approved: true`、`bound: {"base_sha": <盘上正确 sha>}`、`confirmed: true`，机读字段有变 → `PermissionError`**（模型自造参数伪造「已确认」，红队 R-10）；`00-project.md` 是指向别处的软链 → 拒。
  - 卡上绑定：`session_allow`、`card_free` 两条路径进执行层时 `human_approved` 为假**且 `bound is None`**；人批那一支 `bound == {"base_sha": …}`（用协议进程集成测试走真实 `_review`）。只断言 `human_approved` 的话 M9 杀不死：带上 `bind` 不改变执行层结论（红队 R-10）。
- 企划会话的其余工具（红队 R-3、R-7）：`cover_edit` / `acquire_propose` → 审查层 `reject`、绕过审查层直调实现层 → `PermissionError`，母目录前后 `find -newer` 为空；协议进程里企划会话跑一回合与一次 `/compact` 回合 → 各恰好一帧 `stop_points{items: []}`。
- 企划记忆：
  - 卷会话 `write_memory(target="project", op="add")` 写进 `<企划>/00-memory.md` 与其日志，全局文件字节不变；普通期会话 `target="project"` → 拒；
  - 写后超 3000 → `MemoryBudgetError`，文件字节不变；
  - 证据引用另一个企划的卷 → `MemoryRuleError`；
  - 全局记忆的既有测试全部不改且仍绿（`MemoryStore` 参数化不改全局行为）；
  - 来源确认：测试里直接改 `00-memory.md`（模拟 bash）→ 注入变告警、发 `project_memory_unconfirmed`；`ack` 后恢复注入；
  - 企划目录不可达 / 不存在 → 拒，且不建目录。
- 注入：卷会话首轮请求（假模型端点记录）含卷表各行与一条企划记忆正文；**卷停在 03（scope `pipeline`）时新开的卷会话**首轮请求同样含企划记忆正文、不含全局记忆正文（Q6）；`00-project.md` 超 8000 字 → 只含机读部分与章节标题、不含正文中段；两轮之间改卷表 → 第二轮请求含「（已更新）」版本；`00-project.md` 写入 `03-audio/manifest.json` 字样 → 注入文本里是 `[已脱敏]`，请求不被出网断言拦下。
- 企划会话 `run_pipeline`：卷级模块（`tts`）→ 拒，拒因含「到卷 … 的会话里跑」；`status` → 拒，拒因指向 `read_status`；`subgrep` → 放行。
- 期目录守卫：`protocol <企划目录>`（期模式）→ `E_BAD_REQUEST`；`write_episode_file` 以企划目录为 `episode_dir` → 拒。
- `list_episodes`：V1 与 V2 同名卷给出两个不同的 `name`。
- `ava new-project` / `ava new --project`：名字不合规退 2；`data/` 不可达退 2 且不建；已存在退 1。

**vitest**

- TI-5 扩：`listEpisodeTree` 与 `cli.get_episode_tree(root)` 跨语言对拍，夹具同上，**另加一对成员名，第一个不同的字符一个在 U+E000–U+FFFF、一个在增补平面**，如 `（终）`（U+FF08）与 `😀-x`（U+1F600）：码点序是 `（终）` 在前，UTF-16 码元序是 `😀-x` 在前（高代理 0xD83D < 0xFF08）。只加一个增补平面名、其余都是 ASCII 时两种序一致，M15 杀不死（红队 R-11）。全角括号在真实期名里就有（`…函数的终途（void）`）。`listEpisodes` 与改动前对拍：同上，只对不含 `00-project.md` 的夹具要求不变（R-9）。
- `convFrames`：`ready` 缺 `session_kind` → malformed；`project` 为 null 合法。
- spawner：`SESSION_PROJECT` 的 argv；`ConvKey` `proj:` 的路由。

**e2e**（真实 core + 假模型）

- 夹具企划（两卷 + `00-project.md`）：经协议开企划会话，`ready.session_kind === "project"`；假模型调 `read_artifact` 读两卷的 `02-script.md` 均成功（上位需求 A 验收口径的机检版）；开卷会话，首轮请求含卷表。

### 6.6 变异清单（每条都必须让某个测试变红）

| # | 改坏什么 | 由哪条测试抓 |
|---|---|---|
| M1 | 读域去掉 `resolve()`，用未解析路径做包含判断 | 卷内软链 / 企划顶层软链读兄弟卷 |
| M2 | `deny_dir_hit` 去掉 casefold | `03-AUDIO` / `04-Patch` 两条 |
| M3 | 只对 `rel` 判 deny，不对 `resolve()` 后的 target 判 | 目录软链 `audio -> 03-audio` |
| M4 | 企划顶层候选不检查「只有一段」 | `area=project` + `02-b/02-script.md` |
| M5 | `_is_memory_file` 改回只比全局路径 | `00-memory.md` 与软链两条 |
| M6 | 执行层从 `args` 读 `human_approved` / `bound`（或 `confirmed`）代替 `ctx` | 执行层「模型自造参数伪造已确认」那条（原稿写「改用 `args["confirmed"]`」：`00-project.md` 分支根本不看 `confirmed`，这条变异没有可观察的行为，红队 R-10 改写） |
| M7 | 执行层不查 `human_approved`（只查 `confirmed`） | 裸调用改卷表 |
| M8 | 执行层不比 `base_sha` | 卡后被改那条 |
| M9 | `session_allow` 路径也带上 `bind` | 卡上绑定集成测试 |
| M10 | 机读比对改成比原文 | 「只改对齐空白 → allow」 |
| M11 | 企划记忆预算误用 4000 | 超 3000 拒那条 |
| M12 | 企划记忆证据不限本企划 | 引用另一企划那条 |
| M13 | 注入不过 `scrub_restricted` | 出网断言那条 |
| M14 | 注入不做 sha 比对（只注一次） | 两轮之间改卷表 |
| M15 | TS 成员排序用默认 `sort()` | TI-5 增补平面字符成员 |
| M16 | `resolve_episode_target` 恢复取第一个 glob 命中 | V1 / V2 同名卷 |
| M17 | 恢复 `rglob` 回退 | `"03-audio"` |
| M18 | 企划会话放行卷级模块 | `tts` 拒那条 |
| M19 | 企划会话不拦 `cover_edit`（落到默认分支） | 企划会话 `cover_edit` 拒、母目录 `find -newer` 为空 |
| M20 | 企划会话不发 `stop_points` | 一回合恰好一帧空 `stop_points` |
| M21 | 企划归属改用 `resolve()` 后的父目录 | 软链卷身份（`M/01-a -> ../P/01-a` 归 `M`） |
| M22 | 读域后缀只判 `rel` | 软链 `x.json -> 00-memory.log.jsonl` |
| M23 | 企划记忆按 `memory.scopes` 门控 | 停在 03 的卷新开会话含企划记忆 |
| M24 | `classify_dir` 某一调用点改回自判 `01-topic.md` | 判定一致那组（`ava new --project <普通期>` 拒） |

### 6.7 验证命令

```bash
uv run pytest
cd desktop && npm run typecheck && npx vitest run
cd desktop && npx electron-vite build && npx playwright test
```

### 6.8 明确不做

- 不改 renderer（侧栏分组、企划会话入口都在 PR-2b）。
- 不改 ADR-0030 的任何既有免卡项（`01-topic.md`、全局记忆照旧免卡）；卡上绑定只给 `00-project.md` 用。
- 不让卷会话写 `00-project.md`：卷会话的写域保持本卷目录，企划级改动回企划会话做（Q2 的同一取向）。
- 不解析 `00-project.md` 的散文章节（总纲、标题模板、封面模板）；不把标题 / 封面模板接进 08 工序（只作上下文）。
- 不做三层嵌套、跨卷自动写稿、自动拆卷（上位需求 §3）。
- 不迁移、不改写 EGOIST V1 / V2 母目录的任何文件；不替 EGOIST 写 `00-project.md`。

### 6.9 完成判定

- [ ] §6.3 ①–㉕ 落地
- [ ] §6.5 测试全部通过，§6.6 M1–M24 每条实测变红（贴「改坏 → 红的测试名 → 恢复 → 绿」记录）
- [ ] §6.7 三条命令全绿，贴输出
- [ ] 真实数据只读验收（§4.9 镜像，需 T7）：`get_episode_tree` 在真实 `data/episodes` 上的分组结果贴出（22 个顶层条目 → 普通期 + 两个企划）；在镜像企划上开企划会话（假模型即可），`read_artifact` 读到三卷 `02-script.md`；`find … -newer` 为空

---

## 七、PR-2：侧栏企划分组、企划会话入口、进度一览（A / E 的 UI）

### 7.1 目标与对应 issue

D81 A（期列表分组、企划会话入口）、E（企划进度一览）。场景见 §一。

### 7.2 前置条件

- PR-2a：无（只出附件）。PR-2b：PR-1 已合、人已在 PR-2a 选定方案。

### 7.3 PR-2a：截图选型（施工第一步，UI 规则 4）

附件目录 `2026-10-12-project-layer-spec/`，脚本 `options.shots.spec.ts` + `shots.config.ts`（照 D80 `2026-10-12-conv-liveliness-shots/` 先例：未打包构建 × 临时夹具仓、绝不指向真实 `data/`，真实 core + 假模型，在 DOM 上改出方案；在 `desktop/` 下 `PYTHONDONTWRITEBYTECODE=1 npx playwright test -c ../docs/dev/plans/2026-10-12-project-layer-spec/shots.config.ts`，产物进 `desktop/out/d81-shots/`）。

- 夹具内容量**按 §一 场景**：supercell 4 卷 + 合集期、EGOIST V2 三卷 + 典藏版、EGOIST V1 三卷（同名）、20 个普通期，期名用真实长度的中文名。
- 每套方案出：1280×800 与 1440×900 各一张；「按时间」与「按工序」两种视图；企划折叠 / 展开；正在企划会话里 / 正在某卷会话里；一卷有待答卡、一卷停在人审；新建企划与在企划下新建卷的入口。
- 方案之间的差别在：企划行长什么样（标题 + 进度条 / 各卷工序缩写 / 只显示最慢的一卷）、成员缩进还是分组卡片、企划会话列表挂在企划行下面还是单独一个入口、「按工序」视图下企划成员是归组还是散开。
- 截图交人，人选完写回 §二，才进 PR-2b。

### 7.4 PR-2b 改动清单（按人选方案细化后施工；以下是不随方案变化的部分）

| # | 文件 | 锚点 | 改动 |
|---|---|---|---|
| ① | `desktop/src/renderer/App.tsx` | `function EpisodeList({ list, active, showIdea, …` 与 `const row = (e: EpisodeSummary) => (` | 渲染 `projects`；企划行、成员行、企划会话入口；当前选中项的高亮在「企划会话 / 某卷 / 普通期 / 选题」四者之间互斥 |
| ② | 同上 | `<span className="ep-name">{e.epKey}</span>` | 企划成员行显示卷目录名，不再重复企划前缀；悬停显示完整键 |
| ③ | `App.tsx`（原稿误写 `SessionList.tsx`；`SessionList` 只收 `convKey` 属性，键在 `App.tsx` 的 `row` 里拼） | `convKey={\`ep:${e.epKey}\` as ConvKey}` | 企划会话的会话列表用 `proj:` 键 |
| ④ | `NewEpisodeForm.tsx` | — | 新建企划、在企划下新建卷（调 PR-1 的 `ava new-project` / `ava new --project` 模板）。新的 RPC 方法名进 TG-16 的方法闭集，调用点只在点击处理器里（TG-10），企划名 / 卷名初值为空串、不从对话预填（TG-15） |
| ⑤ | `desktop/src/host/service.ts` | — | 企划行进度：成员的 `currentStep` / `isBlocked` / `conv` 已在 `EpisodeSummary` 里，直接用；卷表顺序取 `/project-meta`（失败时按名字码点序，不报错） |
| ⑥ | `style.css` | — | 人选方案的样式；`ui.css` 冻结不动（Spec 14 门禁 1） |

### 7.5 新常量

由人选方案决定（缩进量、进度显示长度等），每个写取值理由；施工时补进本节。

PR-2b 落地（全部是 `style.css` 里的样式值，不加 token、不进 TS）：

| 值 | 取值 | 理由 |
|---|---|---|
| 进度条一段的高 | `6px`，段间 `2px` | 照抄人选的 PR-2a 方案 B 原型；1280 宽、5 段时每段约 40 px，颜色可辨 |
| 徽标行 / 进度条左缩进 | `var(--space-6)`（24 px） | 原型是 28 px（对齐折叠箭头之后的企划名）；token 阶梯里没有 28，取最近的 24，仍落在箭头右侧 |
| 进度条颜色 | `--ok` 走到 09、`--warn` 停在人审、`--wait` 进行中、`--border-strong` 未取到 | 同原型；全是既有 token |


### 7.6 测试要求

- vitest：企划行进度文本的纯函数（各卷工序 → 显示串）；搜索框过滤命中卷名时企划行连带显示。
- e2e：夹具按 §一 场景；点企划 → 企划会话打开、`ready.session_kind === "project"`；点 V1 的 `01-借躯降生` 与 V2 的 `01-借躯降生` 打开的是两个不同目录（看 `ready` 帧与状态区）；新建企划 → 列表出现「未立项」；在企划下新建卷 → 卷出现在该企划下。
- 底线断言（UI 规则 5）：1280×800 与 1440×900 下企划行、成员行、会话列表两两 boundingBox 不相交；侧栏 `scrollWidth <= clientWidth`；20 个普通期 + 3 个企划全展开时侧栏可滚动、不压破对话区最小宽度。
- 变异：选中高亮不互斥（企划会话与某卷同时高亮）；V1 / V2 同名卷点击打开同一目录；企划会话误用 `ep:` 键。

### 7.7 验证命令

同 §6.7。

### 7.8 明确不做

- 不在侧栏里解析 `00-project.md`（卷表顺序经 `/project-meta` 由 core 给）。
- 不做拖拽排序卷、不做在侧栏里改卷表。
- 不改 Spec 14 冻结的 `ui.css` 与既有 token（新 token 需人批）。
- **会撞上的静态门禁，事先写明**（`desktop/tests/static/`，红队 R-21）：门禁 1（`tokens.css` / `ui.css` sha256）——样式只进 `style.css`；VS-1「浅色恰 81 个 token」——加 token 就要人批并同步改这个数；VS-3 / VS-7——颜色字面量不进 TSX，成员行缩进若用内联样式只许 `paddingLeft`；VS-8——企划行、展开 / 折叠、会话入口都必须是原生 `<button>`；TG-10 / TG-15 / TG-16——见 §7.4 ④。

### 7.9 完成判定

- [ ] PR-2a：截图交付，人已选，写回 §二
- [x] PR-2b：§7.4 落地，§7.6 测试与变异通过，§7.7 全绿（2026-10-13，见 §十五）
- [x] 1280×800 与 1440×900 各一张实现截图给人看（`desktop/out/d81-shots/pr-2b/`）
- [ ] §九 第 1 条（人勾）

---

## 八、PR-3：C-3 对抗审查带上前卷定稿

### 8.1 目标与对应 issue

D81 C-3。卷会话里跑 02.8 时，把本企划卷表中卷序更小、已有 `02-script.md` 的各卷的**旁白文本**一并给审查者，专查与前卷的日期、成员变动、事件说法冲突，以及同一件事讲了两遍。只改审查的输入，不新增环节。

### 8.2 前置条件

PR-1（卷 ↔ 企划绑定、`00-project.md` 解析）。

### 8.3 改动清单

| # | 文件 | 锚点 | 改动 |
|---|---|---|---|
| ① | `pipeline/adversarial.py` | `def gather_evidence(ep: Path, script_name: str) -> Evidence:` | 期目录是某企划的卷（按传入路径的**字面父目录**判，不先 `resolve()`，§4.1 末条；否则镜像卷会被认成 EGOIST V2 的卷、找不到镜像的 `00-project.md`）、且该企划 `00-project.md` 可解析时：按卷表找出卷序更小的卷；每卷只取 `02-script.md` 的旁白（`tts.parse_script` 的段落文本，带段号），**不取草稿**；缺 `02-script.md` 的前卷列进 `gaps`（判据 9：跳过要显式列出）。本卷不在卷表 → 不带前卷，`gaps` 写明原因 |
| ② | 同上 | `def build_user_prompt(ev: Evidence) -> str:` | 新增「前卷定稿」区块与审查要求（冲突 / 重复两类，必须给出前卷名与段号） |
| ③ | 同上 | `def verify_item(item: dict, ev: Evidence) -> dict:` | 新类别 `前卷冲突`、`前卷重复`：必须带 `prior_volume`、`prior_segment`；机械核销——该前卷存在于本次输入、段号存在、引文（`_squash` 后）是该段旁白的子串，否则标「引用核不上」 |
| ④ | 同上 | `def render_report(` | 报告新增「与前卷」一节，终审表（D48 ②）照旧由写稿会话填 |
| ⑤ | `config/agent/scopes/creative.md`、`docs/runbook/02-script.md` | — | 一句话：企划内的卷跑 02.8 会自动带前卷，不用手动贴 |
| ⑥ | `pipeline/adversarial.py` | `def write_report(ep: Path, text: str, now: datetime) -> Path:`；`def main(argv: list[str] \| None = None) -> int:` | 加 `--out <目录>`：**本模块的全部写入**都改落该目录（目录必须已存在，不建）——报告 `REPORT`、覆盖前留底 `HISTORY_DIR`、以及 `review()` 里回复解析失败时写的 `RAW_REPLY`（`raw = ep.joinpath(*RAW_REPLY)`，原稿漏了这一处与留底，红队 R-4）。`review()` 因此要接收写入目录参数，不再自己拼 `ep`。默认行为不变。用途只有一个：§4.9 镜像里的卷是指向成品目录的软链，任何一处漏改都会经软链写进 EGOIST V2 |

### 8.4 新常量

| 常量 | 值 | 理由 |
|---|---|---|
| `PRIOR_NARRATION_MAX_CHARS` | 40000 | 前卷只给旁白。EGOIST V2 三卷 `tts.parse_script` 的段落文本实测 3465 / 3196 / 2503 字（2026-10-12 红队只读复测；原稿按「每分钟约 280 字」估 4000–5500，现行 `script.cpm` 是 257）。supercell 按每卷 ≤ 5500 字留余量、4 卷最多带 3 卷约 1.7 万字；取 40000 是两倍余量，且远低于 `MAX_INPUT_CHARS = 150_000` 的整次输入上限。**超了就报错停（`AdversarialError`），不截断**——截掉的那部分恰好可能是冲突所在（判据 5 的同一类问题） |

### 8.5 测试要求

- 夹具企划三卷：第 3 卷审查的输入含第 1、2 卷旁白，不含第 3 卷之后的卷、不含任何卷的草稿；第 2 卷缺 `02-script.md` → `gaps` 列出、第 1 卷照带；本卷不在卷表 → 不带前卷、`gaps` 有原因；普通期 → 输入与改动前逐字节相同（用改动前实现跑出基准）。
- `verify_item`：前卷名不在输入里 / 段号不存在 / 引文不是子串 → 「引用核不上」。
- 超 40000 字 → `AdversarialError`，不发请求（假端点请求数为 0）。
- 软链卷（红队 R-4、R-5）：夹具企划 `M/` 的第 2 卷是指向另一目录的软链，`--out` 给定；假端点回一段**解析不了**的回复 → 退 1，`RAW_REPLY` 落在 `--out` 下，被软链指向的目录 `find -newer` 为空；再以正常回复跑两次（第二次触发覆盖前留底）→ 留底同样在 `--out` 下；该卷认作 `M` 的第 2 卷（输入含 `M` 第 1 卷旁白）。
- 变异：带上草稿；带上卷序更大的卷；超限时截断而非报错；`verify_item` 不核前卷引文；普通期输入被改动；`RAW_REPLY` 仍写 `ep`（不随 `--out`）；企划归属用 `resolve()` 后的父目录。

### 8.6 验证命令

同 §6.7（本 PR 不动桌面端，后两条用来确认没碰坏）。

### 8.7 明确不做

- 不新增环节，不改 02.8 的提示 / 不拦口径（D48 ②：只提示、不拦）。
- 不给审查者兄弟卷的笔记、排片或音频；不让卷会话的读工具因此放开兄弟卷（§4.3 不变）。
- 不做跨企划的检查。

### 8.8 完成判定

- [x] §8.3 ①–⑥ 落地；§8.5 测试与变异通过（另加：`--out` 给定时期目录前后 `find -newer` 为空）；§8.6 全绿（2026-10-13，见 §十五）
- [ ] 真实数据验收（§4.9 镜像，需 T7，**会出网调用模型一次**，由人批）：在镜像企划第 3 卷上跑 `python -m pipeline.adversarial data/episodes/_d81-mirror/03-终局葬礼 --script 02-script.md --out data/episodes/_d81-out/`；「与前卷」一节交人逐条判断真冲突 / 误报，结论写进施工记录；`find … -newer` 为空

---

## 九、PR-4：C-1 / C-2 镜头与配乐跨卷重复（`pipeline.crosscheck`）

### 9.1 目标与对应 issue

D81 C-1、C-2。纯规则，**报警告不拦截**——有意的首尾呼应是合法的，由人判断。

### 9.2 前置条件

PR-1。

### 9.3 改动清单

| # | 文件 | 锚点 | 改动 |
|---|---|---|---|
| ① | `pipeline/crosscheck.py`（新） | — | `python -m pipeline.crosscheck <企划目录> [--json] [--write]`。卷取自卷表（合集期不参与）。默认**只读**：只打印；`--write` 才写 `<企划>/00-crosscheck.md`（覆盖前留底到 `_agent/file-history/`） |
| ② | `pipeline/clips.py` | `def _overlaps(cand, chosen) -> bool:` 里的三元组键 | 把「两段素材是不是同一集」抽成**谓词** `same_footage(a, b) -> bool`，`_overlaps` 与 crosscheck 共用（一条规则，不复制）。必须保住 `_overlaps` 现有语义（红队 R-12）：`anime` 为 `None` 是「未知番」通配，跟谁都算同番（docstring：「单番期语义上跟谁都同番」）——所以不能做成元组键直接比相等；`(season, episode)` 相同才算同集（SP 的 `season` 为 `None`）。≤ 8 s 微动物证的豁免**不进**谓词，留在 `_overlaps` 里原样不动 |
| ③ | `pipeline/crosscheck.py` | — | **C-1 镜头**：每卷读 `04-clips.approved.json`，没有则读 `04-clips.json`（报告注明用的是哪份）；都没有 → 该卷列入「未检查」。片段在 `segments[].clips[]` 里。两卷间 `same_footage` 为真且时间区间交集 > 0 → 命中，报两卷名、段号、各自区间、交集长度。区间与 `_overlaps` 同口径：`[start, start + span)`，没有 `span` 用 `dur`（`extend` 不计，`_overlaps` 也不计）。两段都满足 `_overlaps` 的微动物证条件（`sp` 且 `limit ≤ 8`）的命中**照报**，单列一节「微动物证（单卷内豁免的那一类）」——跨卷是警告不拦，由人判断（上位需求 C） |
| ④ | 同上 | — | **C-2 配乐**：每卷用 `music.parse_script_music(02-script.md)`；非铺底块（`bgm_only=False`，即前景或片尾）的曲名（NFKC + 去首尾空白后逐字比较）在两卷以上出现 → 命中，报各卷块号、`after`（fade / bgm / end）与曲内区间。`parse_script_music` 抛 `SystemExit` → 该卷列入「未检查」并带原因，不中断其他卷 |
| ⑤ | 同上 | — | 退出码：0 = 跑完（有命中也是 0，警告不拦）；2 = 企划不可解析 / 卷表为空；「未检查」的卷在输出末尾显式列出（判据 9：跳过不是通过） |
| ⑥ | `pipeline/agent/tools.py`、`pipeline/agent/session.py` | `PIPELINE_MODULES`；`review_tool_call` 的「参数级免卡」段 | 加 `crosscheck`；参数级只读判定：不含 `--write` 才算只读（§4.8），入选前核实默认路径无写调用。自动补位：企划会话补企划目录；卷会话补**所属企划目录**（不是卷目录）——`validate_pipeline_command` 现只收 `ep_dir`，要多收一个 `project_dir`（取自 `ToolContext`，不取模型参数）；卷会话带 `--write` → 拒（卷会话只放行只读的 `crosscheck`，§4.2） |
| ⑦ | `pipeline/status.py` 或企划状态卡 | — | 企划状态卡显示「上次跨卷检查：N 条命中 / 未检查 M 卷」（读 `00-crosscheck.md`，没有则不显示） |

### 9.4 新常量

无。交集阈值取 0（任何正交集都报）：这是警告，不是门禁；误报的代价是人看一眼，漏报的代价是合集里同一镜头出现两次。报告带交集长度，0.04 s 级的帧量化抖动人一眼能认出。

### 9.5 测试要求

- 夹具三卷：同集同区间 → 命中；同集不相交 → 不命中；不同番同 `S01E01` → 不命中（复用 `same_footage`，2026-09-06 跨番改造的同一教训）；一边 `anime` 为 `None`、同 `S01E01` 同区间 → 命中（通配语义）；两段都是 ≤ 8 s 微动 SP 且重叠 → 命中、落在「微动物证」节；片段有 `extend` → 区间不含它；某卷只有 `04-clips.json` → 报告注明来源；某卷两份都没有 → 「未检查」。
- `_overlaps` 回归：`tests/` 里现有 `_overlaps` / 排片撞车用例全部不改且仍绿；另加一条「`anime` 为 `None` 与 `anime='X'` 同集重叠 → 撞车」（抽谓词时最容易丢的就是这条）。
- 配乐：同曲前景两卷 → 命中；同曲一卷前景一卷铺底 → 不命中；曲名全角 / 半角差异经 NFKC 后相同 → 命中；某卷 `02-script.md` 音乐段格式错 → 该卷「未检查」，其余照查。
- 默认运行前后企划目录 `find -newer` 为空（只读）；`--write` 只写 `00-crosscheck.md` 与留底。
- 变异：区间判定改成 `≥ 0`（相邻也报）；把铺底块算进 C-2；格式错的卷让整个命令失败；默认路径写了文件；`same_footage` 退回只比 `(season, episode)`；`same_footage` 改成元组相等（丢掉 `None` 通配）；区间把 `extend` 算进去；卷会话补位成卷目录。

### 9.6 验证命令

同 §6.7。

### 9.7 明确不做

- 不拦截、不改任何卷的产物；不自动换片、不自动换曲。
- 不查画面相似（只查同一素材的时间重叠）。
- 不查合集期自身。

### 9.8 完成判定

- [x] §9.3 ①–⑦ 落地；§9.5 测试与变异通过；§9.6 全绿（2026-10-13，见 §十五）
- [ ] 真实数据验收（§4.9 镜像，需 T7）：在镜像企划上跑 `python -m pipeline.crosscheck data/episodes/_d81-mirror`（不带 `--write`），全部命中项贴进施工记录，**交人逐条判断「真重复 / 有意呼应 / 误报」**，人的判断原话写回本节；`find … -newer` 为空

---

## 十、PR-5：总装工序 `pipeline.assemble`

### 10.1 目标与对应 issue

D81 D。把 `assemble.sh` 泛化成 `pipeline.assemble`，进 `run_pipeline` 白名单，每次弹卡（Q5）。带上 EGOIST 三条教训：截断点不写预测值；过场卡与主体流参数逐项比对、不一致当场停；末卷片尾完整保留。

### 10.2 前置条件

PR-1；D82 PR 已落地「bash 跑逐条人审模块也弹卡」的规则（§4.8）。基线 `uv run pytest` 绿。验收需要 T7 与约 4 GB 空闲（输出 1.6 GB + 两段截尾件约 1.2 GB + 过场卡）。

### 10.3 改动清单

| # | 文件 | 锚点 | 改动 |
|---|---|---|---|
| ① | `pipeline/assemble.py`（新） | `assemble.sh` 全文 | `python -m pipeline.assemble <企划目录> [--out <目录>] [--replace]`，`argparse(allow_abbrev=False)`。步骤与 `assemble.sh` 一一对应：前置检查 → 截断点 → 过场卡 → 截尾 → 参数自检 → concat → 自证 → 账本 → qc |
| ② | 同上 | `PARTS=("01-借躯降生" "02-皮套囚徒" "03-终局葬礼")` | **部件清单从卷表读**：每行 `目录` 必须是企划目录下 §4.1 认出的成员（单段名、过 `_episode_name_problem`、有 `01-topic.md`），且有 `05-final.mp4`、`08-cover.jpg`；`余韵N秒` 的卷还要有 `02-script.md` 与 `03-audio/manifest.json`。任一不满足 → 退 2，**在任何 ffmpeg 之前** |
| ③ | 同上 | `vo_end = max(s["start"] + s["dur"] for s in tl["segments"])` | 截断点 = `music.build_timeline` 的旁白终点 + 卷表 `余韵N秒`；`cut >= total_duration` → 退 2。`完整保留` 的卷原样 `-c copy` 进拼接 |
| ④ | 同上 | `-f lavfi -t "$CARD_DUR" -i "anullsrc=r=48000:cl=mono"`、`-c:a aac -b:a 192k -ac 1 -ar 48000` | **不写死流参数**：以第一卷 `05-final.mp4` 的 ffprobe 签名为基准（像素格式、分辨率、帧率、声道数、采样率），过场卡与截尾件按基准生成。EGOIST 的基准探出来就是 `yuv420p / 1920×1080 / 24000/1001 / mono / 48000`，生成的 argv 与 `assemble.sh` 逐 token 相同（§10.5 黄金测试锁住） |
| ⑤ | 同上 | `stream_sig()` 与「编码参数不一致，禁止流拷贝拼接」 | 全部部件逐项比对签名（同 `assemble.sh` 的字段集），任一不等 → 打印 diff、退 1、不拼接 |
| ⑥ | 同上 | `printf "file '%s'\n" "$f" >> "$WORK/concat.txt"` | concat 清单按 ffmpeg concat 语法转义（`'` → `'\''`）；路径含换行 / 控制字符 → 退 2。原脚本这里不转义，目录名带 `'` 时拼接清单会被截断甚至多出一条指令 |
| ⑦ | 同上 | — | **一律 argv**：`subprocess.run([...])`，不用 `shell=True`、`os.system`、字符串拼命令（静态测试守住）。所有输入路径先 `resolve()`，部件文件必须落在对应成员目录的 `resolve()` 之内（成员可以是人建的软链卷，§4.1）；输出路径必须落在输出目录内 |
| ⑧ | 同上 | — | **输出与覆盖**：默认输出目录 = 卷表 `合集:` 指向的成员（必须存在且有 `01-topic.md`——qc 读它的 `时长目标`）；`--out` 指定别处时，该目录必须已存在、不在任何卷目录内、不是企划目录本身，且同样要有 `01-topic.md`。目标 `05-final.mp4` 已存在且没带 `--replace` → 退 2。以上全部前置检查都在**创建任何文件或目录（`cards/`、`work/`）与任何 ffmpeg 之前**完成。带 `--replace` 时新成片先写到同目录临时名，自证通过后才 `os.replace`；自证失败时旧成片原样保留 |
| ⑨ | 同上 | — | **输入指纹**：开工时记录每个部件源文件的 `(size, mtime_ns)`，`os.replace` 前复核；任何一个变了 → 退 1、不替换、不写账本（防总装期间某卷被重渲） |
| ⑩ | 同上 | `"$PY" - "$DIR" "$OUT" "$GOT" "${ASSEMBLE[@]}" <<'PY'` 那段账本代码 | 写 `04-assembly.json`，形状与现账本相同（`total_duration`、`parts[{name, path, kind, offset, duration, src}]`、`_note`）；`kind=part` 的部件一律写 `src` = 成员目录（qc 字幕四项要回产出它的那一卷读 plan 与 manifest；修掉 §三 #9 的不一致）。**命名照现账本钉死**（验收要比 `name`，红队 R-27）：第 i 卷（从 1 起）的过场卡文件 `cards/card_part{i}.mp4`、`name = "card_part{i}"`；被截的卷 `work/part{i}_trimmed.mp4`、`name = "part{i}_trimmed"`；`完整保留` 的卷直接用它的 `05-final.mp4`、`name` = 卷目录名 |
| ⑩′ | 同上 | `DETAIL=$(ffmpeg -v error -i "$OUT" -f null - 2>&1 \|\| true)` | **自证两项都做**（原稿 ① 只写了「自证」二字）：总长与部件之和差 < `DURATION_TOLERANCE_S`；`ffmpeg -v error -i <临时成片> -f null -` 的输出为空（流拷贝拼错不报错、只在解码时炸，`assemble.sh` 原则 4）。任一不过 → 退 1、不 `os.replace`、不写账本 |
| ⑩″ | 同上 | — | ⑧ 的前置检查全部通过之后、建 `cards/` 之前，输出目录持锁 `<输出目录>/_agent/assemble.lock`（`fcntl`，同 `session_titles.py` 先例；前置检查失败的路径仍然不建任何文件），拿不到退 3：两个总装同时跑会互相覆盖 `cards/`、`work/` 与临时成片（企划会话租约挡不住 CLI 与 bash） |
| ⑪ | 同上 | `cd "$REPO" && "$PY" -m pipeline.qc "$DIR"` | 收尾跑 `python -m pipeline.qc <输出目录>`（argv），退出码透传 |
| ⑫ | 同上 | — | 空间预检：输出目录所在卷的空闲 < `ASSEMBLE_FREE_FACTOR` × 部件源文件总大小 → 退 2（在任何 ffmpeg 之前） |
| ⑬ | `pipeline/agent/tools.py` | `PIPELINE_MODULES: set[str] = {` | 加 `assemble`；只在企划会话放行（PR-1 ⑰ 的分流） |
| ⑭ | `pipeline/agent/tools.py`、`session.py` | `HUMAN_GATED_COMMANDS`（D82 PR-A 引入） | 加 `("assemble", None)`（任何子命令 / 选项都算）；卡面用 `command_preview`：`assemble.preview(argv)` 列出部件、截断点、输出位置、是否覆盖。`preview` 不成立（卷表错、缺文件、已有成片未带 `--replace`）→ 弹卡前拒 |
| ⑮ | `docs/runbook/09-publish.md` 或新 `docs/runbook/10-assemble.md`、`config/agent/scopes/project.md` | — | 企划会话怎么总装、`--replace` 的含义 |

### 10.4 新常量与取值理由

| 常量 | 值 | 理由 |
|---|---|---|
| `CARD_SECONDS` | 3 | `assemble.sh` 的 `CARD_DUR=3`，EGOIST 已交付值；验收要求与典藏版逐项一致，改它就对不上 |
| `CARD_FADE_S` | 0.5 | 同上：`fade=t=in:st=0:d=0.5` / `fade=t=out:st=${fade_out}:d=0.5` |
| `TRIM_FADE_S` | 1.0 | 同上：`FADE=1.0`（截断点前音画淡出） |
| `TRIM_VIDEO_ARGS` / `TRIM_AUDIO_BITRATE` | `-c:v libx264 -preset medium -crf 18`、`192k` | 同上，`assemble.sh` 截尾与过场卡的编码参数；是 EGOIST 交付时的取值，不是新判断 |
| `DURATION_TOLERANCE_S` | 0.1 | `assemble.sh` 自证「总长 = 部件之和」的容差（`$GOT-$SUM<0.1`）；也是本 PR 验收「与典藏版时长差 < 0.1 s」的同一个数 |
| `ASSEMBLE_FREE_FACTOR` | 2.0 | 输出成片 ≈ 部件之和，截尾件 ≈ 被截卷之和（≤ 部件之和），两者相加 ≤ 2 倍；EGOIST 实测输出 1.66 GB、三卷源合计 1.82 GB。不够就在开工前停，比拼到一半写满盘好 |

余韵秒数不是常量：每卷写在卷表里（§4.7），EGOIST 是 18（2026-09-13 人拍板）。

### 10.5 测试要求

- **黄金测试（与 `assemble.sh` 等价）**：给定 EGOIST 的探测签名（写成夹具）与截断点 889.53 / 866.74，生成的三条 ffmpeg 命令（过场卡、截尾、concat）的 argv 与从 `assemble.sh` 推出的 argv 逐 token 相等（期望 argv 先手工从脚本展开、在实现上跑一遍核对后写进断言；路径类 token 按夹具目录替换后再比）。**注意它把截断点当输入**，抓不住「截断点怎么算」。
- **截断点计算**（红队 R-26）：合成小企划（`02-script.md` + `03-audio/manifest.json` 夹具，不需要真实音频），卷表写 `余韵18秒`、`时长预算` 写一个与 `build_timeline` 结果明显不同的数 → 截断点 = `build_timeline` 的旁白终点 + 18（期望值先在 `music.build_timeline` 上跑一遍再写断言）；`cut >= total_duration` → 退 2。
- **流参数跟随基准**（红队 R-26）：主体是**立体声**的合成小企划（`ffmpeg -f lavfi` 现造）→ 过场卡与截尾件按基准生成立体声，签名一致，拼接成功。M11 靠这条杀。
- **部件越界**：某卷 `05-final.mp4` 是指向成员目录之外的软链 → 退 2，ffmpeg 调用数 0。M7 靠这条杀。
- **解码自证**：把一个部件截掉末尾若干字节制造解码错误（真实 ffmpeg，小样片）→ 退 1、不替换、不写账本。
- **并发**：持有输出目录锁时再起一个 `assemble` → 退 3，第一个的产物不受影响。
- 前置检查：卷表目录写成 `../x`、`/abs`、`a/b` → 解析期就拒；目录不是成员（无 `01-topic.md`）→ 退 2；缺 `05-final.mp4` / `08-cover.jpg` / manifest → 退 2；以上全部在任何 ffmpeg 调用之前（用记录调用的 runner 替身断言调用数为 0），且输出目录前后 `find -newer` 为空（没建 `cards/`、`work/`）。
- 覆盖：已有 `05-final.mp4` 不带 `--replace` → 退 2、旧文件字节不变、ffmpeg 调用数 0；带 `--replace` 但自证失败（替身让时长差 0.2 s）→ 旧文件字节不变；带 `--replace` 且通过 → 新文件到位。
- 输入指纹：执行中途替身改动某卷 `05-final.mp4` 的 mtime → 退 1、不替换、不写账本。
- 签名不一致（替身让过场卡探出 `yuvj420p` + 立体声）→ 退 1，输出含 diff，不拼接。
- concat 转义：成员目录名含 `'` 的夹具，生成的 concat 清单经 ffmpeg 真实解析能拼出正确部件数（小样片，`ffmpeg -f lavfi` 现造 1 秒片子，不 mock ffmpeg——AGENTS.md「不 mock ffmpeg」）。
- 静态：`pipeline/assemble.py` 不含 `shell=True`、`os.system`、`os.popen`、`subprocess.getoutput`。
- 审查层：模型发 `run_pipeline assemble` → `ask`、无 `allow_key`；卷会话发 → 拒；`--rep`（缩写）→ argparse 报错（不被当成 `--replace`）；bash `python -m pipeline.assemble …` → 带 `[须人审的流水线命令]` 弹卡（依赖 D82 PR 的规则，本 PR 加一条用例）。
- 端到端小样（真实 ffmpeg、合成片）：两卷 + 合集期的夹具企划，跑完 qc 能读到账本，`04-assembly.json` 的 `offset` 连续、`duration` 之和与成片差 < 0.1 s，`name` 序列为 `card_part1, part1_trimmed, card_part2, <末卷目录名>`（R-27 的命名规则）。

### 10.6 变异清单

| # | 改坏什么 | 由哪条测试抓 |
|---|---|---|
| M1 | 不查已有成片就开拼 | 覆盖：不带 `--replace` |
| M2 | 先删旧成片再写新的 | 覆盖：自证失败旧文件不变 |
| M3 | 去掉签名比对 | `yuvj420p` + 立体声 |
| M4 | 截断点用卷表时长预算代替 `build_timeline` | 截断点计算那条（原稿写「黄金测试」：黄金测试把截断点当输入，杀不死这条，红队 R-26） |
| M5 | 末卷也裁 | 黄金测试（末卷 `-c copy`）与卷表末行规则 |
| M6 | concat 清单不转义 | 含 `'` 的目录名 |
| M7 | 部件路径不做 resolve 包含检查 | 软链指出成员目录之外的 `05-final.mp4` |
| M8 | 不复核输入指纹 | 中途改 mtime |
| M9 | `allow_abbrev` 默认 | `--rep` |
| M10 | 卡片开放「本会话都批准」 | 审查层无 `allow_key` |
| M11 | 流参数写死为 mono | 流参数跟随基准那条：立体声夹具卷生成的过场卡签名与主体不符 → 本该通过的用例变红 |
| M12 | 去掉解码自证（只比时长） | 解码自证那条 |
| M13 | 不取输出目录锁 | 并发那条 |
| M14 | 账本命名不照 `card_part{i}` / `part{i}_trimmed` | 端到端小样断言 `name` 序列 |

### 10.7 验证命令

同 §6.7。另加真实数据验收（§10.9）的命令，由人批卡执行。

### 10.8 明确不做

- 不重配 TTS、不重渲分期主体（`assemble.sh` 原则 1）。
- 不改 qc 的 11 项门禁与 `assembly_ledger` 读法。
- 不给过场卡做新设计；过场卡素材仍是各卷 `08-cover.jpg`。
- 不处理「合集也要有自己的封面 / 标题」（08 工序照旧在合集期里做）。
- 不删除 EGOIST 典藏版下的 `assemble.sh` 与 `01.5-assembly-spec.md`（成品目录只读）。

### 10.9 完成判定

- [ ] §10.3 ①–⑮（含 ⑩′ ⑩″）落地；§10.5 测试与 §10.6 M1–M14 通过；§10.7 全绿
- [ ] **真实数据验收（需 T7，人批卡执行；EGOIST V2 只读）**：
  1. 按 §4.9 建镜像企划；从典藏版**复制**（只读源）`01-topic.md` 进 `data/episodes/_d81-out/`（qc 读 `时长目标`）。
  2. 跑 `python -m pipeline.assemble data/episodes/_d81-mirror --out data/episodes/_d81-out`。
  3. 截断点必须是 889.53 / 866.74；**不是就停下报告**（说明 `music.build_timeline` 或曲库自 2026-09-13 后变了，这是要人看的事，不调参数凑数）。
  4. 与典藏版逐项比对，写进施工记录：
     - 成片 format duration 与典藏版（2915.617542）之差 < 0.1 s；
     - 新旧 `04-assembly.json` 的部件数、顺序、`name`、`kind` 相同，每个部件 `offset`、`duration` 之差 < 0.1 s（**不比 `path` / `src`**，见 §三 #9）；
     - 新 `06-check.log` 为 11/11 通过，逐项与典藏版 `06-check.log` 并排贴出；
     - 6 个部件的流签名逐项相同。
  5. `find "data/episodes/EGOIST-传奇企划志-V2" -newer "$marker"` 为空。
  6. 人听一遍合集的三处章节交界（判据：成片交人前至少听开头与最长一段，AGENTS.md 四）。

---

## 十一、安全语义与状态一致性：规则 → 测试总表（给红队）

| 规则 | 写在 | 由什么证明 |
|---|---|---|
| 读域双端 resolve，软链指出域外即不在域内 | §4.3 第 2 条 | §6.5 读域四条兄弟卷路径；M1 |
| casefold 后仍排除 `03-audio/`、`04-patch/`，`rel` 与 resolve 后各判一次 | §4.3 第 3 条 | 大小写混写、目录软链两条；M2、M3 |
| 卷会话不能借机读兄弟卷 | §4.3 末段 | 四条路径全拒；M1、M4 |
| 记忆文件不能被读工具绕过（全局与企划，含软链） | §4.3 第 4 条 | 四条拒读；M5 |
| bash 不受读写域约束（ADR-0030 已接受） | §三 #11、§十二 | 不测（残余风险，明写） |
| `00-project.md` 机读字段改动必须人在卡上批，模型确认不了 | §4.4 | 执行层三条（裸调用、`args["confirmed"]`、卡后被改）；M6–M9 |
| 企划记忆沿用 `apply_op`、来源确认、超额即拒 | §4.5 | 预算、证据范围、来源确认三条；M11、M12 |
| `00-project.md` 字段流进总装只走 argv、路径解析在企划目录内 | §4.7、§10.3 ②⑥⑦ | 解析期拒 `../x`；成员资格；concat 转义；静态无 `shell=True`；PR-5 M6、M7 |
| 不覆盖已有 `05-final.mp4`，除非人在卡上批 `--replace` | §4.8、§10.3 ⑧⑭ | 覆盖三条、审查层无 `allow_key`、bash 绕卡；PR-5 M1、M2、M9、M10 |
| 总装期间输入变化不出半成品 | §10.3 ⑨ | 中途改 mtime；PR-5 M8 |
| 新帧键进 `convFrames.ts` 契约 | §6.3 ⑳ | TX-0 三种会话；`ready` 缺键 malformed |
| 期列表 Python 规则与 TS 镜像一致 | §4.1、§6.3 ①③ | TI-5 扩（含增补平面字符）；M15 |
| 同名卷不被静默解析到另一个企划 | §4.1 | V1 / V2 同名卷；M16 |
| 企划目录不能当期目录写 `01-topic.md` | §4.1、§三 #2 | 期目录守卫两条 |
| 注入的企划文本不会触发出网断言 | §4.6 | 脱敏一条；M13 |
| 企划会话不跑卷级工序 | §4.2 | `tts` 拒；M18 |
| 企划会话里需期工具不落到默认分支（封面 / 抓取不写进母目录） | §4.2、§6.3 ㉔ | `cover_edit` / `acquire_propose` 拒；M19 |
| 企划会话守 S9-R4（每回合恰好一帧 `stop_points`） | §4.2、§6.3 ㉕ | 一回合与 `/compact` 回合各一帧；M20 |
| 软链卷的企划归属按字面路径 | §4.1 末条 | 软链卷身份；PR-3 软链卷；M21 |
| 读工具不能经软链读记忆日志 | §4.3 第 4、5 条 | 三条软链拒读；M22 |
| 「企划 / 卷 / 普通期」判定只有一处 | §4.1 | 判定一致；M24 |
| 镜像验收不经软链写 EGOIST V2 | §4.9 第 3 步、§8.3 ⑥ | PR-3 软链卷 `find -newer` 为空；`RAW_REPLY` 变异 |

## 十二、明确不做（全局）与残余风险

- 上位需求 §3 三条：结构化史实数据库、跨卷自动写稿 / 自动拆卷、三层以上嵌套。
- 不新增工具（ADR-0025 封顶，§三 #10）。
- 不改 ADR-0030 已免卡的任何项；不给 bash 加读域限制。
- **残余风险（写给红队，不在本 spec 修）**：bash 能 `cat` 兄弟卷与 `data/library/memory.md`、能改 `00-project.md`（绕过卡，但下一次经工具写入时 `base_sha` 对不上会被拒，注入侧会注入被改后的文本）、能用 ffmpeg 直接覆盖 `05-final.mp4`（`05-final.mp4` 不在 ADR-0030 的解封物清单里）。这些都是 ADR-0030「静态规则可绕过」的同一类，推翻条件按 ADR-0030 原文执行。红队补三条同类（2026-10-12）：
  - 企划会话的 bash 能直接 `python -m pipeline.tts data/episodes/<企划>/<卷>`，绕过 Q2「企划会话不跑卷级工序」——Q2 只约束 `run_pipeline`；
  - bash 同时改 `00-memory.md` 与在 `00-memory.log.jsonl` 追加一行自算 sha 的状态行，来源确认就会放行（§4.5；全局记忆同理）；
  - bash 建硬链接（`ln`，非软链）读兄弟卷文件，`resolve()` 识别不了——但 bash 本就能直接 `cat`，硬链接不扩大能力。

## 十三、验收（UI 规则 2）与总完成判定

1. **【人勾】真实任务**：打包版、1440×900 与 1280×800，建 supercell 编年史企划，在企划会话里定总纲与卷表，建第一卷并开写；记下别扭之处（agent 不得代勾，不得用 mock 或截图替代）。其中顺带验上位需求 B 的口径（红队 R-22：原稿只有假模型的机检版）：在第一卷的**新会话**里第一轮不调任何工具，问 ava 本企划的分卷边界与一条企划记忆，记下它答没答对。
2. **【人勾】截图选型**：PR-2a 的方案已选，写回 §二。
3. **【人判】真实数据**：PR-3 的「与前卷」报告、PR-4 的跨卷命中项，人已逐条判断并写回对应节。
4. 机检：每个 PR 的验证命令全绿、变异全杀、只读验收 `find -newer` 为空。

- [ ] PR-1 完成（§6.9）
- [ ] PR-2 完成（§7.9），**待人验收**
- [ ] PR-3 完成（§8.8）
- [ ] PR-4 完成（§9.8）
- [ ] PR-5 完成（§10.9）
- [ ] 本节 1–3 人已勾

---

## 十四、红队记录（2026-10-12，零上下文审查）

审查对象：提交 `73fd6a2` 的本 spec。审查人没有参与写 spec，逐条回到代码与真实数据核对（真实数据全程只读：只做 `ls` / `stat` / 读文本 / `ffprobe`，没有在 `data/episodes/` 下建任何文件）。严重度：P0 = 照写施工会造成数据损坏或安全边界失效；P1 = 规则不可测、互相矛盾、或会让验收碰到只读成品；P2 = 事实性小错或缺口。**收口：P0 无；P1 全部吸收或经人裁决；P2 全部吸收，只有一条不采纳。**

| # | 严重度 | 发现（证据） | 失败场景 | 处置 |
|---|---|---|---|---|
| R-1 | — | 锚点普查：§三、§六–§十 引的 80 余处原文逐条 grep。命中的有 `get_episodes_list`、展平注释、`resolve_episode_target` 的 `glob` / `rglob`、`_allowed_read_roots`、`deny_dir_hit`、`_is_memory_file`、`READ_ALLOWED_SUFFIXES`、`AUTO_APPROVED_TOOLS`、`confirmed = bool(args.get(…)) or ctx.confirmed`、`replace(context, confirmed=True) if …`、`Decision`、`_inject_memory`、`_require_lib` / `apply_op` / `provenance_ok`、`PIPELINE_MODULES`、`ConvKey`、`EpisodesList`、`ready: {`、`case "SESSION_IDEA":`、`assembly.json` 的 `scopes`、`adversarial` 五个函数签名、`_overlaps`、`OVERLAP_GAP`、`ASSEMBLY = …`、`assemble.sh` 各行、ADR-0025 封顶原文 | — | 通过。错引 2 处见 R-23、R-24 |
| R-2 | — | 覆盖：上位需求 A–E、§3 不做、§4 顺序、§5 四项待裁，逐项都有落点（A、B → PR-1/2；C-3 → PR-3；C-1/C-2 → PR-4；D → PR-5；E → PR-2b） | — | 通过。B 的验收口径只有机检版，见 R-22 |
| R-3 | P1 | 企划会话的期目录绑定没定义。§4.1 让 `resolve_episode_dir` 对企划目录一律拒，§4.4 又让企划会话经 `write_episode_file` 写 `00-project.md`，而 `write_episode_file` 第一步就调 `resolve_episode_dir`。`NEEDS_EPISODE_TOOLS = {"write_episode_file", "cover_edit", "run_pipeline", "acquire_propose"}` 在 `ep_dir is None` 时一律拒 | 若 `ep_dir` 为企划目录：`cover_edit.render_cover(ctx.episode_dir, …)` 免卡把封面写进母目录，与 Q2「只做规划、跨卷审查、总装」相反；若为 `None`：企划会话写不了 `00-project.md` | 已吸收：§4.2「企划会话的期目录绑定」、§4.4 第 4 条 `resolve_project_dir`、§6.3 ㉔、§6.5、M19 |
| R-4 | P1 | `adversarial.review()` 回复解析失败时写 `raw = ep.joinpath(*RAW_REPLY)`；`write_report` 覆盖前留底写 `ep.joinpath(*HISTORY_DIR)`。原稿 `--out` 只改报告 | §8.8 的镜像验收会出网调一次模型，回复格式一错，`_agent/adversarial-raw.txt` 就经软链写进 `EGOIST-传奇企划志-V2/03-终局葬礼/`，违反只读纪律（`find -newer` 能事后发现，但写已经发生） | 已吸收：§8.3 ⑥、§8.5 软链卷用例与两条变异 |
| R-5 | P1 | `resolve_episode_target` 返回 `.resolve()`。镜像卷 `_d81-mirror/01-借躯降生 -> ../EGOIST-传奇企划志-V2/01-借躯降生` 解析后落在 V2 下；TI-5 的 `PROJ2/02-linked` 解析后落在 `data/episodes` 之外 | 镜像卷被认作 V2 的卷，C-3 找不到镜像的 `00-project.md`，验收空转；指向盘外的软链卷被新的形状检查拒掉，与 §4.1「成员可以是软链卷」矛盾；在镜像卷上开卷会话，租约与 `session.jsonl` 写进 V2 | 已吸收：§4.1 末条（身份按字面路径）、§4.9 第 3 步（镜像禁开卷会话）、§8.3 ①、§6.5、M21 |
| R-6 | P1 | `assembly.json` 的 `memory.scopes = ["creative", "asset", "idea"]`，`resolver.scope_of` 在 03 及以后给 `pipeline`。ADR-0023 补记「pipeline scope 仍不单独注入」的理由是「整期共用一份历史」 | 原稿让企划记忆与全局记忆共用开关：卷做到 03 后新开的会话看不到企划记忆，与上位需求 B「卷会话自动常驻」不符，而企划记忆里的读音取舍、封面参数恰好是 03 / 08 才用 | **人裁决 Q6（2026-10-12）**：卷 / 企划会话一律注入企划记忆。已写进 §二、§4.6、§6.3 ㉒、§6.5、M23 |
| R-7 | P1 | 原稿「企划会话…不发 `stop_points`」。协议 S9-R4 规定每回合恰好一帧（`protocol.py`：「idea 没有期目录，也就没有停机点」那一支照发空帧）；宿主 `sessions.ts` 在 `case "stop_points"` 里推进回合收尾（`settle`）。另：`_agent/approvals.jsonl` 是 `log_approval_decision` 写的审批流水，停机点状态在 `approvals_store.json` | 企划会话回合结束后宿主一直等不到收尾，D79 排队的消息发不出去 | 已吸收：§4.2、§6.3 ㉕、M20 |
| R-8 | — | UI 规则 1：§一 写了两档窗口、真实内容量（22 个顶层目录已核实：19 个普通期 + `2025-老视频` + 两个 EGOIST）与 7 步连续操作；侧栏分组、进度显示、入口全部留给 PR-2a 截图，spec 没有替人定视觉 | — | 通过 |
| R-9 | P1 | §6.5 要求 `get_episodes_list` 在全部夹具上与改动前逐项相同，夹具里却有「有 `00-project.md` 无成员的新企划」 | 改动前它是普通期、在平铺列表里；改动后它是企划、不在平铺列表里——这条断言不可能同时满足新规则 | 已吸收：§6.5 区分基准夹具与有意差异 |
| R-10 | P1 | M6「`00-project.md` 分支改用 `args["confirmed"]`」：该分支只看 `human_approved` 与 `bound`，不看 `confirmed`，改了也没有可观察的行为。M9「`session_allow` 也带 `bind`」：对应测试只断言 `human_approved` 为假，带上 `bind` 不改变执行层结论 | 两条变异永远绿，「模型自造参数伪造已确认」反而没有测试 | 已吸收：§4.4 第 5 条、§6.5 执行层与卡上绑定两条、M6 改写 |
| R-11 | P1 | M15 要抓「TS 用默认 `sort()`」。默认 `sort()` 按 UTF-16 码元，与码点序只在「一个字符 ≥ U+E000、另一个在增补平面」时才不同 | 只加一个增补平面字符、其余都是 ASCII 的夹具，两种序一致，M15 杀不死 | 已吸收：§6.5 vitest 夹具改为 `（终）` 对 `😀-x` |
| R-12 | P1 | `_overlaps`：`anime` 为 `None` 是通配（「单番期语义上跟谁都同番」），另有 ≤ 8 s 微动物证豁免；片段区间取 `span` 否则 `dur`。真实 `04-clips.approved.json` 的片段在 `segments[].clips[]` 里，还带 `extend` 键 | 原稿把键抽成元组 `footage_key` 两处共用：`_overlaps` 改用元组相等就丢了 `None` 通配，单期排片悄悄少拦撞车；crosscheck 对微动物证与 `extend` 的口径没定，验收命中数不可复现 | 已吸收：§9.3 ②③ 改为谓词 `same_footage` 并写死区间与微动口径（微动照报、单列一节——上位需求 C「警告不拦，由人判断」），§9.5 回归用例与变异 |
| R-13 | P2 | 「是企划 / 卷 / 普通期」要在树规则、`resolve_episode_target`、`resolve_episode_dir`、协议 `--project`、`ava new --project`、三个新模块里判，原稿没说用同一函数；`--project` 指向普通期、`new --project` 指向普通期都没写怎么办 | 母目录没有 `01-topic.md` 时各处判据分叉；`ava new --project <普通期>` 会把一个期变成「自己也有 `01-topic.md` 的企划」 | 已吸收：§4.1「判定只有一处」、§6.5 判定一致、M24 |
| R-14 | P2 | §4.3 企划会话的根不含 `data/library/`，`area` 枚举却有 `library` | 企划会话里 `area=library` 永远读不到 | 已吸收：§4.3 表 |
| R-15 | P2 | `_tool_read_artifact` 只判 `rel.suffix`；`_is_memory_file` 只认 `memory.md`。记忆日志的状态行带 `result_text`（全文） | 先用 bash 建软链 `x.json -> 00-memory.log.jsonl`，`read_artifact("x.json")` 就能读到记忆全文，绕过来源确认（全局日志今天就有这条旁路；因为前提是 bash，而 bash 本来就能读，所以定 P2） | 已吸收：§4.3 第 4、5 条、§6.5、M22 |
| R-16 | P2 | §4.4 没定义 `before` 不可解析时怎么比机读字段 | 人手改坏后由模型修，行为未定义（可能当成「没变」免卡） | 已吸收：§4.4 第 2 条 |
| R-17 | P2 | §三 #11 与 §4.5 说企划记忆被 bash 改写时由来源确认拦下。`provenance_ok` 比的是正文 sha 与日志状态行，日志同样 bash 可写 | 同时伪造正文与状态行即可注入 | 已吸收：§4.5 改为「拦的是只改正文」，§十二 残余风险 |
| R-18 | P2 | §4.6 说新鲜度「沿用 `_doc_shas`」。`_doc_shas` 是给注入消息记 sha 的函数；全局记忆在会话内只注入一次，变了只在恢复后第一轮补 | 施工者以为现成，实际是新行为 | 已吸收：§4.6 新鲜度条 |
| R-19 | P2 | 卷表「各行不重复」按原文比；APFS 大小写不敏感 | `01-A` 与 `01-a` 是同一个目录，同一卷进两次合集 | 已吸收：§4.7、§6.5 |
| R-20 | P2 | §6.4 写 `00-project-plan.md`「实测 11822 字」。实测 5455 字，11822 是 UTF-8 字节数 | 常量取值理由里的样本说法不成立（它走全文分支，不走摘要分支） | 已吸收：§6.4 改用踩坑全录（10075 字）当摘要分支样本 |
| R-21 | P2 | PR-2b 只提了 `ui.css` 冻结；`desktop/tests/static/` 里还有 VS-1（浅色恰 81 个 token）、VS-3 / VS-7、VS-8（原生交互元素）、TG-10 / TG-15 / TG-16（建期类方法、表单预填、RPC 方法闭集） | 施工时撞门禁才发现，倾向于「改门禁让它过」 | 已吸收：§7.4 ④、§7.8 |
| R-22 | P2 | 上位需求 B 的验收「新开一卷的会话，第一轮不调工具就能说出分卷边界与一条企划记忆」在 spec 里只有假模型的请求内容断言 | 注入了但模型不用，机检发现不了 | 已吸收：§十三 第 1 条顺带验 |
| R-23 | P2 | §7.4 ③ 锚点写在 `SessionList.tsx`；`` convKey={`ep:${e.epKey}` as ConvKey} `` 实际在 `App.tsx` 的 `row` 里 | 施工者找不到锚点 | 已吸收 |
| R-24 | P2 | §三 #7 引 `apply_op` 注释「D43 / Spec 17：不再有 scope 闸」，原文是「D43 / Spec 17（ADR-0027）：不再有 scope 闸」 | 引文漂移 | 已吸收 |
| R-25 | P2 | §6.3 ⑳ 提 TX-0 没说在哪，§6.5 把 `convFrames` 断言放在 vitest；TX-0 是 Playwright e2e（`desktop/e2e/sessionReal.spec.ts`） | 施工者在 vitest 里找不到 TX-0 | 已吸收：§6.3 ⑳ |
| R-26 | P1 | 黄金测试把截断点 889.53 / 866.74 当输入，M4「截断点改用时长预算」杀不死；M7（部件软链越界）、M11（立体声基准）在 §10.5 没有对应用例 | 三条变异永远绿 | 已吸收：§10.5 截断点计算、流参数跟随基准、部件越界三条，M4 改指向 |
| R-27 | P2 | 验收要比新旧账本的 `name`，原稿没钉命名规则。盘上是 `card_part1` / `part1_trimmed` / `03-终局葬礼` | 实现换个命名，验收第 4 步必然不过，却不是缺陷 | 已吸收：§10.3 ⑩、端到端小样断言、M14 |
| R-28 | P2 | §三 #9 对 `assemble.sh` 账本代码的描述与源码不符（见该行校正） | 施工者照错的描述「修」账本 | 已吸收：§三 #9 |
| R-29 | P2 | `assemble.sh` 原则 4 的「全片解码零报错」在 §10.3 只写成「自证」二字，§10.5 没测 | 只比时长的实现能过全部测试 | 已吸收：§10.3 ⑩′、§10.5、M12 |
| R-30 | P2 | 总装没有输出目录锁；企划会话租约挡不住 CLI 与 bash | 两次总装互相覆盖 `cards/`、`work/` | 已吸收：§10.3 ⑩″、§10.5、M13 |
| R-31 | P1 | 企划会话 `run_pipeline` 放行集合不含 `tickets`，D82 却写「企划会话补企划目录」 | 总装撞 bug，在跑总装的那个会话里开不了单 | 已吸收：§4.2、§6.3 ⑰（D82 spec 同步改 `--blocked` 与提醒） |
| R-32 | P2 | §4.2「卷会话 `crosscheck` 自动补位为所属企划目录」，但 `validate_pipeline_command` 只收 `ep_dir` | 照现有补位逻辑会补成卷目录 | 已吸收：§9.3 ⑥ |
| R-33 | P2 | 总装卡上列的部件来自卡片那一刻的卷表；卡后、执行前若 bash 改了卷表，执行用新卷表。可以复用 §4.4 的卡上绑定 | 人批的部件清单与实际拼的不一致 | **不采纳**：窗口只有「人点批准 → 子进程启动」这一瞬，且只有 bash 能在这时改；执行输出与 `04-assembly.json` 会列出实际部件，人在第 6 步听交界时能发现。与 §十二 残余风险同类，留待第一次实例再改 |
| R-34 | P2 | 残余风险漏写三条：企划会话 bash 跑卷级模块、伪造记忆日志、硬链接 | 人以为被拦，其实没有 | 已吸收：§十二 |

**上位需求本身**：没有发现新的错误。§三 已列的两处（「照 `01-topic.md` 的纪律弹卡」前提失效、写工具白名单已是 5 个）由原稿处理，人已裁决 Q4。

**改完复核**：本节新增与改写的锚点（`_tool_cover_edit`、`_tool_acquire_propose`、`if ep_dir is None and name in NEEDS_EPISODE_TOOLS:`、协议里 idea 那一支的空 `stop_points`、`raw = ep.joinpath(*RAW_REPLY)`、`DETAIL=$(ffmpeg -v error -i "$OUT" -f null - 2>&1 || true)`、`App.tsx` 的 `convKey`、`apply_op` 注释原文）已重新 grep，全部命中。

---

## 十五、施工记录

> 分支 `d81-d82-project-layer`（从 `main` 的 `c99a6fa` 切出）。开工 2026-10-12 20:40。基线：pytest 2686 / vitest 539 / playwright 135（2 跳过）。
> 同一分支先落了 D83（`e0ca1fb`，见 issues D83 行），之后按本 spec 的 PR 顺序施工。

### PR-1（2026-10-12 / 13）

**落地**：§6.3 ①–㉕ 全部落地。与清单字面不同之处（都不改设计，逐条写明）：

1. **树规则、期名合规、解析器放在新文件 `pipeline/project.py`**（清单 ④ 的那个文件），`cli` 以原名再导出 `classify_dir` / `get_episode_tree` / `_episode_name_problem`。理由：`adversarial` / `crosscheck` / `assemble` 也要认企划，import `cli.py` 会拖进整个 agent 栈（`cli.py` 文件头已写明共享函数要下沉到叶子模块）。
2. **期根以上经软链的路径**：宿主可能给 `/var/folders/…` 而 `paths.ROOT` 是 resolve 后的 `/private/var/…`（外置盘软链同理）。`episode_key` 先比字面，再只 resolve「期根那一段」，期 / 企划 / 卷各段仍按字面取（§4.1 末条不变，`test_期根以上经软链_末段仍按字面` 锁住）。
3. **期根之外的绝对路径 → None**（§4.1 / §6.5 原文）使 13 条既有测试变红（它们在子进程里用真实仓库根跑 `ava <tmp_path/期>`）。停下问人，**人裁决（2026-10-12，选项原文）：「照 spec 拒，改这 13 条测试 (Recommended)」**。做法：测试侧根目录替身 `tests/root_shim.py`（临时根复制 config/docs/skills/AGENTS.md、软链 pipeline/、`data/episodes` 软链到测试原来的期根，子进程经 sitecustomize 改 `paths.ROOT`）；只换夹具、断言一条不动，golden 金样本 G-P1 / G-P2 逐字不变。
4. **`creative.md` 没有「记忆段」**（清单 ㉒ 的锚点不存在）：在文末新开一小节「记忆两层（D81）」写那一句。
5. **协议入站帧**：`command` 加可选键 `target`（只用于 `memory_ack`，取值 global / project）；入站键检查由「恰为必需键」改为「必需键 + 可选键」，`rid` 之外只放行这一个。
6. **卡上绑定多一层防御**：`llm.py` 组装 `exec_ctx` 时只在 `provenance == "human"` 时传 `bound`（spec 原文是 `bound = decision.bind`，由 `_review` 保证只有人批那支带）。因此 M9（`_review` 的 session_allow 分支带 bind）在集成测试里观察不到，另加 `test_review_本会话已批的同类卡不带绑定` 直接测 `_review` 返回的 `Decision`。集成测试用进程内真实 `_review`（`AgentSession` + 假通道）走人批 / card_free / 「本会话都批准」/ session_allow 四条路径，不是协议子进程。
7. **写入卡面**：`render_approval_card` 的写入分支也渲染 `command_preview`（00-project.md 的机读逐行 diff + 全文 diff）。
8. **企划会话 `--fresh`**：接受，等同开新会话（与不带 `--continue` 相同），对齐 spec 的入口写法。
9. **会话管理认企划目录**：`ava <企划目录绝对路径> /list-sessions` 等会话管理命令放行企划目录（宿主的 `proj:` 会话列表要用），其余期级子命令照拒。
10. **既有契约随之更新**（都是 spec 要求的变化）：`ready` 键集合 +`session_kind` / `project`、`memory.scopes` +`project`、`list_episodes` 明细 +`project`；vitest / e2e 里手写的假 `ready` 帧补两键；`scripts/verify_mutations.py` 中因代码搬家失效的 6 条锚点改到新位置（S10-MUT-18/19/20、MUT-D42-d、M27c、S9-MUT-60）。

**测试**：新增 `tests/test_project_layer.py`（树规则、判定、期名解析、解析器，51 条）、`tests/test_project_session.py`（读域、写企划文件、卡上绑定、企划会话工具、判定一致、CLI、企划记忆、注入、协议，46 条）、`tests/test_d83_run_pipeline_scrub.py` 之外的既有文件按上条更新；vitest +4（树形对拍含 `（终）` / `😀-x` 码点序对、`ready` 缺键、`SESSION_PROJECT` argv、`proj:` 路由）；e2e +1（TX-0c：卷 / 企划会话帧过契约、企划会话读两卷定稿、卷会话首轮含卷表）。期望值均先在实现上跑出、核对来由后写入。

**变异 24/24 被抓**（脚本在 scratchpad，跑完 `git diff` 只剩真实改动）：

| # | 红的测试 |
|---|---|
| M1 | `test_卷会话不能借读兄弟卷_四条路径` |
| M2 / M3 | `test_硬排除_大小写与目录软链[volume]` |
| M4 | `test_企划候选只认一段_多段经软链落回企划顶层也拒`（新增：父目录检查会兜住原来那条用例） |
| M5 / M22 | `test_记忆文件与日志不可读[volume]`（M22 由 `z.md -> session.jsonl` 抓） |
| M6 / M7 / M8 | `test_执行层_裸调用与卡后被改与伪造参数` |
| M9 | `test_review_本会话已批的同类卡不带绑定` |
| M10 | `test_只改散文免卡且留底` |
| M11 / M12 | `test_超3000拒且字节不变` / `test_证据引用另一企划拒` |
| M13 / M14 / M23 | `TestInjection` 的脱敏、「（已更新）」、停在 03 三条 |
| M15 | vitest「D81 树形对拍」（`（终）` 与 `😀-x` 次序颠倒） |
| M16 / M17 | `test_同名卷歧义_返回None并列出两个候选` / `test_拒[03-audio]` |
| M18 / M19 | `test_run_pipeline分流` / `test_封面与抓取提案拒_母目录不被写` |
| M20 | `test_协议_企划会话与期目录守卫`（真实 core 子进程，回合与 /compact 回合各恰一帧空 stop_points） |
| M21 | `test_软链卷的企划归属按字面路径` |
| M24 | `test_判定一致`（`ava new --project <普通期>` 拒） |

**既有变异矩阵复核**：锚点搬家的 6 条经 `scripts/verify_mutations.py --only … --dirty-ok`（全量套件）重跑，6/6 被抓：S10-MUT-18（`test_ty1_*` 4 条）、S10-MUT-19（含 `test_ty2_bad_names[a/b]` 与本 PR 的解析器用例）、S10-MUT-20（`test_ty2_bad_names[_x]` 等）、MUT-D42-d（`test_t_d42_*` 2 条）、M27c（`test_list_episodes_tool_detail_keys`）、S9-MUT-60（`test_tk12_registration_lifetime_and_episode_dir`）。

**真实数据只读验收**（§6.9 第 4 条）：
- `get_episode_tree` 在真实 `data/episodes` 上：23 个顶层条目 → 21 个普通期 + 2 个企划（`EGOIST-传奇企划志` 三卷、`EGOIST-传奇企划志-V2` 三卷 + 典藏版，均无 `00-project.md`），隐藏 2。spec 写 22 时只有 19 个普通期，之后又新建过期，不是偏差。
- 镜像企划**放在 scratchpad 的临时仓库根里**（不在 `data/episodes/` 下建 `_d81-mirror`：本次施工不往 `data/` 写任何东西），三卷软链到 EGOIST V2，`00-project.md` 用 §4.7 样例。经真实协议进程开企划会话（假模型）：`ready.session_kind == "project"`，`read_artifact(area="project")` 读到三卷 `02-script.md`（14082 / 12954 / 11535 字）。
- `find data/episodes/EGOIST-传奇企划志-V2 -newer <镜像验收开始>`：**空**。
- 另记一件事：以开工时间 20:40 为界，EGOIST V2 有 20:58:56–20:59:04 的写入（`03-终局葬礼/_agent/approvals_store.*`、`events.jsonl` 一条 09 停机点建档与一条 `human_time_recorded` 0.08 分钟、典藏版与 01 卷的 `_agent/approvals_store.lock`）和 22:39 的 `.DS_Store`。形态是桌面端宿主在人点开这几期时的行为（建停机点对象、记停机点人时），当时本机的打包版 `AVA.app`（真实 userData）在运行；本次施工的测试一律用临时 userData 与夹具仓。**待人确认当时是否在用桌面端。**

**验证**：pytest 2786/ typecheck 通过 / vitest 543 / playwright 136（2 跳过）。

### PR-2a（2026-10-13）

- 附件 `2026-10-12-project-layer-spec/`（`shots.config.ts` + `options.shots.spec.ts`），照 D80 先例：未打包构建 × 临时夹具仓（§一 的内容量：supercell 4 卷 + 合集、EGOIST V2 三卷 + 典藏版、V1 三卷同名、20 个普通期），宿主期列表是 PR-1 的真实扫描、各期工序是真实 core 的 status；侧栏期列表区换成原型 DOM（样式经 `adoptedStyleSheets` 注入），其余真实渲染。
- 三套方案（A 缩进树 / B 分组卡片 / C 单行摘要）× 五个场景帧（1440 企划会话、1280 卷会话、1440 全折叠、1440 / 1280 按工序），每帧断言侧栏不横向溢出、原型行两两不重叠（UI 规则 5）；产物 `desktop/out/d81-shots/pr-2a/`（对照页 `index.html`）。示意项（「1 张卡待答」「运行中」徽标、企划会话高亮）在对照页写明。
- **人选 B 分组卡片**（2026-10-13），已写回 §二。

### PR-2b（2026-10-13）

按人选方案 B「分组卡片」施工。**机检通过，待人验收。**

- 宿主：`project.create`（`ava new-project`）、`episode.create` 可选 `project`（`ava new --project`；企划只认宿主自己扫到的，未知企划 `E_BAD_REQUEST` 零 spawn）、各企划卷表随期状态刷新经 `/project-meta` 取回（`ProjectSummary.meta`；取不到为 null，侧栏按目录名码点序排卷，不报错）。三个新 core 模板 `NEW_PROJECT` / `NEW_VOLUME` / `PROJECT_META`。
- 渲染层：`shared/projectView.ts`（纯函数：卷行顺序与标签、进度条各段、卡头徽标、搜索连带、「按时间」混排）；`App.tsx` 的 `EpisodeList` 换成卡片，`activeProject` 与 `active`、`showIdea` 三态互斥（打开任一处清另两处），中栏企划会话显示企划头部（企划名、合集、卷表各卷与工序、解析错误、目录对不上的提示）；`NewProjectForm` / `NewVolumeForm` 在 `NewEpisodeForm.tsx`（TG-10 / TG-15 / TG-16 同纪律，`project.create` 进 `scan.ts` 方法闭集）；`Composer` 的发送目标写「企划会话 · 名」；`SessionHeader` 的模式徽标认 `project`（「企划模式」），企划会话不显示「素材模式」开关。样式只进 `style.css`，`tokens.css` / `ui.css` 零改动（门禁 1 哈希与 VS-1 token 计数不动）。
- **施工中发现并修的 PR-1 缺陷**：`ava <企划目录> /list-sessions`（以及改名、删除、回收站）在 `_dispatch_session_admin` 里先过 `resolve_episode_dir`，对企划目录一律拒（「这是企划目录，不是期」），侧栏企划会话列表显示 E_CORE。改为企划走 `resolve_project_dir`，其余期级命令照拒。新增 `test_企划会话的会话管理命令认企划目录`；e2e「企划会话入口」也断言会话列表读到那一段、无报错行。
- **施工中发现并修的本 PR 缺陷**：`pipeline/status.py` 在 09 恒报 `is_blocked=true`，先判 blocked 的话「完成」（绿）永远走不到，已发布的卷全显示成停在人审、卡头恒挂「停机 N」。改为先判 09；卡头停机数用同一判据（走到 09 的不算）。
- **spec 内部对不上（按树规则施工，待人确认措辞）**：§一 第 1 步与 §7.6 写「新建企划 → 列表出现『未立项』（还没有 `00-project.md`）」；但 §6.3 ⑱（PR-1 已施工）规定 `ava new-project` 同时写骨架 `00-project.md`，且按 §4.1 树规则没有 `00-project.md`、也没有卷的空目录判为普通期，不会作为企划出现。所以新建的企划显示为「还没有卷」、不标「未立项」；「未立项」只出现在有卷而无 `00-project.md` 的旧目录（如真实数据里的两个 EGOIST 企划）。测试按此写。
- **照方案原样施工、交人验收时看的两点**：① 「按工序」视图下（方案 B 散开）没有企划卡，也就没有「企划会话」入口——原型如此；要进企划会话切回「按时间」。② 侧栏默认 240 px 下，「按工序」的行文字「企划名 · 第 N 卷 · 目录」会被截断，V1 / V2 同名卷在这个视图里只能靠悬停的完整键区分（「按时间」视图下各在自己的卡里，不混）。
- 搜索：企划名 / 目录名命中整卡显示，只有卷名命中时只列命中的卷；搜索时命中的卡一律展开。卡默认折叠，含当前卷或正在企划会话的卡默认展开。
- 测试：vitest `tests/shared/projectView.test.ts`（14 条，含 09 恒 blocked、增补平面字符码点序）、`tests/host/spawner.test.ts` +1、`tests/host/service.test.ts` +3（真实 core 建企划 / 建卷 / 卷表，含解析失败与 core 输出不合式）；pytest +1；e2e `desktop/e2e/project.spec.ts` 5 条（企划会话入口与 `ready.session_kind == "project"`、四处高亮互斥；V1 / V2 同名卷打开两个目录、`ready.project` 各自对；新建企划与企划下建卷；1280×800 与 1440×900 底线：20 个普通期 + 3 个企划全展开时卡头 / 企划名 / 会话按钮 / 徽标行 / 进度条 / 行 / 会话行两两不相交、侧栏不横向溢出且可滚动、中栏 ≥ `CENTER_MIN`）。顺带记一个坑：共享的 `launch()` 要等到一行期才返回，夹具若只有折叠卡里的卷会白等 30 s（不报错），所以同名卷用例的夹具带了一个普通期。
- 变异 19/19 被抓（scratchpad `pr2b_mut.json`，跑完 `git diff` 只剩真实改动）：

| # | 变异 | 抓到它的测试 |
|---|---|---|
| M1 | 进企划会话不清 `active`（高亮不互斥） | e2e 企划会话入口 |
| M2 | 卷行按目录名找期打开（V1 / V2 打开同一目录） | e2e 同名卷 |
| M3 | 企划会话误用 `ep:` 键 | e2e 企划会话入口 |
| M4 | 卡上会话列表误用 `ep:` 键 | e2e 企划会话入口 |
| M5 / M5b | 回退 core 会话管理命令的修复 | pytest / e2e 企划会话入口 |
| M6 | 卷行不按卷表、只按码点序 | vitest |
| M7 / M7b | blocked 先判 / 卡头停机数按 `is_blocked` | vitest |
| M8 | 卷名命中时整卡显示 | vitest |
| M9 / M9b | 企划成员也进平铺列表 | vitest / e2e 新建企划 |
| M10 | 徽标漏数企划会话的卡 | vitest |
| M11 | 建卷不校验企划 | host vitest |
| M12 | 卷表取回后不入表 | host vitest |
| M13 | `NEW_VOLUME` 丢 `--project` | spawner vitest |
| M14 | 进度条与徽标行重叠 | e2e 底线 ×2 |
| M15 | 企划名不截断（撑进「企划会话」按钮） | e2e 底线 ×2（初版底线没把企划名与按钮放进比较集，存活；补进后被抓） |
| M16 | 中栏企划头部不按卷表排 | e2e 企划会话入口 |

- 实现截图（UI 规则 4 收尾）：附件 `impl.shots.spec.ts`，产物 `desktop/out/d81-shots/pr-2b/`（`index.html` 对照页）：i1 1440 企划会话、i2 1280 卷会话、i3 1440 全折叠、i4 / i5 1440 / 1280 按工序、i6 1280 新建企划 + 建卷表单。侧栏与中栏全是真实渲染，每帧过同一套底线断言。
- EGOIST V2 只读核对：`find … -newermt "2026-10-12 20:40:00"` 的命中与 PR-1 记录的完全相同（20:58:56–20:59:04 与 22:39 `.DS_Store`），本 PR 无新增写入。
- **验证**：pytest 2788 / typecheck 通过 / vitest 561 / playwright 141（2 跳过）。

### PR-3（2026-10-13）

**已施工·机检通过**（纯 core）；真实数据验收的出网那半（§8.8 第 2 条）按本次施工规则没跑，待人批。

- §8.3 ①–⑥ 落地（`pipeline/adversarial.py`）：`prior_volumes(ep, gaps)` 按**字面父目录**（`project.project_of`，不先 resolve）认企划，按卷表取卷序更小、已有 `02-script.md` 的各卷旁白（`tts.parse_script` 的段号与文本，不取草稿）；缺定稿的前卷、本卷不在卷表、企划文件缺失 / 不可解析都写进 `gaps`；前卷旁白合计超 `PRIOR_NARRATION_MAX_CHARS = 40000` → `AdversarialError`、不调模型。`build_user_prompt` 只在有前卷时追加「前卷定稿」区块与审查要求（写在 user 提示里，不改 `SYSTEM_PROMPT`——普通期输入逐字节不变）。新类别 `前卷冲突` / `前卷重复`，条目带 `prior_volume` / `prior_segment` / `prior_quote`，`verify_item` 机械核销（前卷在本次输入里、段号存在、引文 `_squash` 后是该段旁白的子串），没有前卷输入时这两类判「类别不认」。报告新增「与前卷」一节（编号 V），终审表照旧。`--out <目录>`（必须已存在、不建）：报告、覆盖前留底、回复解析失败的原文留档三处写入全落该目录。`creative.md` 与 `docs/runbook/02-script.md` 各加一句。
- 普通期不变的基准：改动前的实现（提交 `fee56c3`）在 `tests/test_adversarial.py` 的夹具上跑出 system + user 提示的 sha256 `6136017376ce…dfec`，写成 `test_plain_episode_prompt_unchanged_by_project_layer`。
- 测试：`tests/test_adversarial_prior.py` 15 条（第 3 卷带第 1、2 卷、不带第 4 卷与草稿；前卷缺定稿列缺口；本卷不在卷表；企划文件不可解析；第 1 卷无前卷；前卷引用核对 5 例；无前卷时前卷类别不认；报告「与前卷」一节与终审表；超限报错不调模型；软链卷 + `--out`：解析不了的回复 → 原文留档在 `--out`、认作镜像企划的第 2 卷（输入含镜像第 1 卷旁白），再正常跑两次 → 报告与留底都在 `--out`，被软链指向的成品目录逐文件 mtime / 大小不变；`--out` 不存在拒且不建）+ 基准 1 条。
- 变异 8/8 被抓（scratchpad `d81pr3_mut.json`）：R1 带上草稿、R2 带上卷序更大的卷（第 3 卷用例）；R3 超限截断（超限用例）；R4 不核前卷引文（引用核对）；R5 普通期输入被改动（基准用例）；R6 原文留档仍写期目录、R7 企划归属用 resolve 后的父目录、R8 报告与留底仍写期目录（软链 + `--out` 用例）。
- 真实数据只读核对（不出网）：在 PR-1 的 scratchpad 镜像（三卷软链到 EGOIST V2）上只调 `prior_volumes`：第 3 卷带第 1 卷 49 段 3465 字、第 2 卷 43 段 3196 字，缺口为空，前卷区块 7526 字——与 §8.4 的实测值一致。前后 `find data/episodes/EGOIST-传奇企划志-V2 -newer <标记>` 为空。
- **未做（待人）**：§8.8 第 2 条的真实审查要调一次模型（出网、计费），本次施工规则是不跑真实 LLM、不出网；人批后照 §8.8 的命令跑，把「与前卷」一节逐条判真冲突 / 误报写进这里。命令里 `--out` 必须给，且镜像放 `data/episodes/_d81-mirror/`（§4.9）或照本次做法放 scratchpad 临时根。
- **验证**：pytest 2876 / typecheck 通过 / vitest 568 / playwright 143（2 跳过）。

### PR-4（2026-10-13）

**已施工·机检通过**（纯 core）；真实数据命中清单已出，**待人逐条判断**（§9.8 第 2 条）。

- §9.3 ①–⑦ 落地：
  - ① ③ ④ ⑤ 新文件 `pipeline/crosscheck.py`：`python -m pipeline.crosscheck <企划目录> [--json] [--write]`，`argparse(allow_abbrev=False)`（`--wr` 不被认作 `--write`）。卷按卷表顺序取，合集期不参与；企划目录按字面路径判（`project.classify_dir`），镜像企划的软链卷照读。`--write` 写 `00-crosscheck.md`，已存在的先留底到 `_agent/file-history/<UTC 纳秒>-00-crosscheck.md`（与 `tools._keep_history` 同目录同命名；没 import agent 栈）。退出码 0 / 2 照 ⑤。
  - ② `clips.same_footage(a, b)`：`_overlaps` 改为调它，`anime=None` 通配原样保住，≤ 8 s 微动豁免与 `OVERLAP_GAP` 仍留在 `_overlaps`。crosscheck 的微动物证条件照抄 `_overlaps` 那一行（spec 要求豁免不进谓词）。
  - ⑥ `PIPELINE_MODULES` +`crosscheck`、进 `_AUTOFILL_MODULES`（位置参数至多 1 个）。`validate_pipeline_command` / `run_pipeline` / `jobs.create_job` 多收 `project_dir`（工具层取 `ctx.project_dir`，审查层取 `review_tool_call` 的 `project_dir`，都是宿主绑定）；crosscheck 只用它补位，企划会话补企划目录、卷会话补所属企划目录，普通期会话不补。立项时（`create_job` 再校一次）同传，否则执行的 argv 会丢补位。只读判定按参数级：`crosscheck_writes(argv)`（`--write` 及其前缀、`=` 写法都算写），不带写的在审查层走只读免卡；卷会话带写 → `project_module_refusal` 拒（审查层与实现层共用）。企划会话带 `--write` 照 ADR-0030 白名单免卡。
  - ⑦ `status_card.build_project_card` 加一行「上次跨卷检查: N 条命中（镜头 a · 微动物证 b · 配乐 c）/ 未检查 M 卷（生成时间）」，读 `00-crosscheck.md` 的汇总行；没有报告不显示，汇总行被手改掉时显示「找不到汇总行」而不是静默不显示。「未检查 M 卷」按卷计（一卷只要有一项没查到就算）。
- `scripts/verify_mutations.py` 的 M15a 锚点随审查层那一行（多了 `project_dir=project_dir`）重锚；用 `mut.py` 单条复核仍被 `test_tk1_model_initiated_review_with_options_is_rejected` 抓到。
- 测试：`tests/test_crosscheck.py` 17 条（`same_footage` 通配 / 跨番 / SP；`_overlaps` 的 `None` 与具名番同集重叠算撞车；三卷夹具里同集同区间命中、相邻不命中、跨番同 `S01E03` 不命中、`None` 通配命中、两段微动 SP 进「微动物证」、`extend` 不计、只有 `04-clips.json` 的卷注明来源、两份都没有列「未检查」；配乐前景两卷命中、一前景一铺底不命中、全角半角经 NFKC 相同、音乐段格式错的卷「未检查」其余照查；默认运行与 `--json` 前后企划目录逐文件 size / mtime 不变；`--write` 只多出报告，第二次只多出一份留底；`--wr` 退 2；企划文件缺失 / 不可解析 / 卷表为空 / 给的是卷 → 退 2；软链卷照读且被指向的目录不变；状态卡一行）+ `tests/test_project_session.py` 2 条（补位：企划会话与卷会话的 dry-run argv 都补企划目录，卷会话执行时 `create_job` 立项的 argv 同样补企划目录；只读免卡：企划 / 卷会话不带写都放行，卷会话 `--write` / `--wr` / `--write=1` 审查层与实现层都拒，企划会话带写放行）。`tests/test_clips.py`、`tests/test_cross_anime.py` 既有 `_overlaps` 用例一条未改、全绿。
- 变异 11/11 被抓（scratchpad `d81pr4_mut.json`，跑完 `git diff` 只剩真实改动）：

| # | 变异 | 抓到它的测试 |
|---|---|---|
| M1 | 区间判定改成 `≥ 0`（相邻也报） | `test_镜头命中与不命中` |
| M2 | 铺底块算进 C-2 | `test_报告分节_注明来源`（配乐计数） |
| M3 | 格式错的卷让整个命令失败 | `test_配乐_格式错的卷未检查_其余照查` |
| M4 | 默认路径也写报告 | `test_默认只读_企划目录无写入` |
| M5 | `same_footage` 只比 `(season, episode)` | `test_same_footage_None通配_跨番不同集` |
| M6 | `same_footage` 改成元组相等（丢 `None` 通配） | 同上 |
| M7 | 区间把 `extend` 算进去 | `test_镜头命中与不命中` |
| M8 | 卷会话补位成卷目录 | `test_crosscheck补位_企划会话补企划_卷会话补所属企划` |
| X1 | 卷会话带 `--write` 不拒（补充） | `test_crosscheck只读免卡_卷会话带write拒` |
| X2 | 立项（`create_job`）不传 `project_dir`（补充） | `test_crosscheck补位_…`（执行 argv） |
| X3 | 状态卡不显示上次检查（补充） | `test_状态卡显示上次检查` |

- **真实数据只读验收**（§9.8 第 2 条的机器那一半）：PR-1 的 scratchpad 镜像（`mirror-root/data/episodes/_d81-mirror`，三卷软链到 EGOIST V2，`paths.ROOT` 指 mirror-root，进程内调 `crosscheck.main`，不带 `--write`）。跑前 touch 标记，跑后 `find data/episodes/EGOIST-传奇企划志-V2 -newer <标记>`：**空**。三卷都用 `04-clips.approved.json`，未检查 0 卷。汇总：**镜头 19 条、微动物证 0、配乐 0**。命中清单（区间是源片内秒数）：

| # | 素材 | 卷 · 段 [区间) | ↔ 卷 · 段 [区间) | 交集 s | 人的判断 |
|---|---|---|---|---|---|
| 1 | EGOIST SP35 | 01 · 3 [0.00, 5.34) | 02 · 2 [0.00, 5.34) | 5.34 | 待判 |
| 2 | EGOIST SP56 | 01 · 18 [0.00, 5.74) | 02 · 9 [0.00, 6.01) | 5.74 | 待判 |
| 3 | EGOIST SP35 | 01 · 29 [0.00, 5.34) | 02 · 2 [0.00, 5.34) | 5.34 | 待判 |
| 4 | EGOIST SP04 | 01 · 40 [0.00, 29.52) | 02 · 18 [21.56, 29.26) | 7.70 | 待判 |
| 5 | EGOIST SP04 | 01 · 43 [145.81, 157.31) | 02 · 38 [145.81, 157.80) | 11.50 | 待判 |
| 6 | EGOIST SP04 | 01 · 47 [219.76, 228.54) | 02 · 17 [224.64, 235.07) | 3.90 | 待判 |
| 7 | EGOIST SP06 | 01 · 2 [0.00, 6.01) | 03 · 12 [0.00, 5.87) | 5.87 | 待判 |
| 8 | EGOIST SP06 | 01 · 3 [0.00, 6.01) | 03 · 12 [0.00, 5.87) | 5.87 | 待判 |
| 9 | EGOIST SP06 | 01 · 5 [0.00, 6.01) | 03 · 12 [0.00, 5.87) | 5.87 | 待判 |
| 10 | EGOIST SP19 | 01 · 9 [0.00, 19.20) | 03 · 32 [0.00, 7.56) | 7.56 | 待判 |
| 11 | EGOIST SP56 | 01 · 18 [0.00, 5.74) | 03 · 19 [0.00, 6.01) | 5.74 | 待判 |
| 12 | PSYCHO-PASS S01E01 | 01 · 34 [1269.89, 1285.12) | 03 · 16 [1269.89, 1282.66) | 12.77 | 待判 |
| 13 | EGOIST SP04 | 01 · 42 [180.81, 192.42) | 03 · 11 [180.81, 206.94) | 11.61 | 待判 |
| 14 | EGOIST SP02 | 02 · 3 [23.16, 36.20) | 03 · 13 [26.53, 43.61) | 9.68 | 待判 |
| 15 | EGOIST SP56 | 02 · 9 [0.00, 6.01) | 03 · 19 [0.00, 6.01) | 6.01 | 待判 |
| 16 | EGOIST SP02 | 02 · 9 [36.70, 42.70) | 03 · 13 [26.53, 43.61) | 6.00 | 待判 |
| 17 | EGOIST SP40 | 02 · 23 [0.00, 9.40) | 03 · 14 [0.00, 8.33) | 8.33 | 待判 |
| 18 | EGOIST SP04 | 02 · 25 [330.12, 355.02) | 03 · 29 [334.79, 343.62) | 8.83 | 待判 |
| 19 | EGOIST SP47 | 02 · 32 [0.00, 16.71) | 03 · 4 [0.00, 13.47) | 13.47 | 待判 |

  两处与 spec 预期不同，照规则施工、交人看：
  1. **「微动物证」一节在真实数据上恒为空**：条件照 `_overlaps` 读 `limit`，但 `04-clips.approved.json` 的片段不带 `limit`（三卷 155 个片段键集只有 `anime / season / episode / source / start / dur / score / line / presence / sp / extend`；`limit` 只在排片时的候选上有）。所以 SP06（`…_微动.mp4`，6.006 s）这类微动物证落在「镜头」一节（#7–#9）。没有改判据（换成读 `dur` 是设计变化）；要不要改由人定。
  2. **配乐 0 条是真结果，不是没查**：三卷 `02-script.md` 都解析成功（7 / 10 / 11 个音乐段）。唯一跨卷同名的 `Departures ～あなたにおくるアイの歌～` 第 1 卷是前景（M0、M3），第 3 卷是铺底（M4.5），按 §9.3 ④ 一前景一铺底不算命中。
- **验证**：pytest 2895 / typecheck 通过 / vitest 568 / playwright 143（2 跳过）。
- 施工中另记：全量 pytest 第一次跑了 11 分钟、红 11 条，查明是 `test_n28_non_terminal_sigint_kills_grandchild_process_group` 偶发挂住（SIGINT 发给自己的竞态），其后的中断类用例连带假红；另有一个 2026-10-12 14:08 起的旧 pytest 同样挂在这一条上 20 小时，孙进程持续往 pytest 临时目录写，`pytest-147/test_n28_…/05-final.mp4` 已 4.97 GB。两组挂住的测试进程已杀（只杀进程，没删文件）；静默环境重跑全量 2895 全绿、中断类 219 条单独跑也全绿。这条用例的偶发挂起是既有问题，本次未修。
