# D81：企划层（多卷系列成为一等实体）施工 spec

> 对应 issue：**D81**（`docs/dev/issues/README.md`）。上位需求：[`2026-10-12-project-layer-direction.md`](2026-10-12-project-layer-direction.md)（A–E 五节）。本 spec 以代码现状为准，与上位需求不符之处见 §三，设计按现状调整，不照抄上位需求。
> 相关：ADR-0023（跨期记忆）、ADR-0025（工具表封顶 15）、ADR-0027（工具不按模式授权）、ADR-0029 / ADR-0030（bash、审批只拦硬危险）、D43、D47 / D48（写期文件）、D52（工具结果脱敏）、D53（argv 预检）、D80（UI 先例）、qc 总装口径（2026-09-13）。
> 姊妹 spec：[`2026-10-12-bug-ticket-spec.md`](2026-10-12-bug-ticket-spec.md)（D82）。两者无硬依赖：D82 排在 PR-1 之后是时机（第一卷开写前要有企划层，开始生产时要能开工单）；反方向上，本 spec PR-5 用到 D82 引入的「逐条人审的模块命令」集合与 bash 绕卡规则（§4.8）。
> **PR-2 含 UI，plans/README 的 UI 五条规则对它生效**：场景见 §一，视觉方案由人从截图里选（PR-2a），验收第 1 条是人在打包版里做一件真实工作（§九）。
> **数据纪律**：`data/episodes/EGOIST-传奇企划志-V2/` 是已交付成品，**只读**。本 spec 的任何测试与验收步骤都不写入它；用到它的验收一律走 §4.9 的「只读镜像」，并在步骤前后用 `find … -newer` 证明没写过。

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
| UI | 侧栏企划分组、企划进度、新建企划 / 卷入口 | **未定**——照 UI 规则 4，PR-2a 出 2–3 套真实窗口截图，人选 | 人选后写回本表 |

## 三、现状核对（2026-10-12，逐条对照上位需求 §1）

| # | 上位需求的说法 | 代码现状（引原文） | 结论与对设计的影响 |
|---|---|---|---|
| 1 | 子卷被展平进期列表，母目录消失；两个企划各有一个 `01-…` 时列表里分不清 | `cli.py::get_episodes_list`：「`# 若子目录包含子期目录（如 EGOIST-传奇企划志-V2/03-终局葬礼），展平纳入`」；`episodes.ts`：`for (const s of subEps) visible.push({ epKey: \`${name}/${s}\`, abs: \`${d}/${s}\` });` | **部分成立**。桌面端的 `epKey` 带母目录名（`App.tsx`：`<span className="ep-name">{e.epKey}</span>`），列表里**分得清**。分不清的是 Python 侧：`_tool_list_episodes` 只给 `"name": ep.name,`，模型看到的 V1 与 V2 都是 `01-借躯降生`；`resolve_episode_target` 先 `ep_root.glob(f"*/{target}")` 取第一个、再 `ep_root.rglob(target)`，**同名卷静默解析到先遍历到的那个企划**。现状就有此冲突：`EGOIST-传奇企划志/` 与 `EGOIST-传奇企划志-V2/` 下三卷目录名完全相同。→ PR-1 修解析（§4.1） |
| 2 | （上位需求未提）母目录 | EGOIST V2 母目录已有 `session.jsonl`（82 行）、`events.jsonl`、`_agent/approvals.jsonl`（2026-09-27）——**有人已经把母目录当成「期」开过 ava 会话**：`protocol.py` 的 `ep_dir = resolve_episode_target(rest[0])` 接受任何 `data/episodes/` 下的目录。而母目录 `README.md` 写着「严禁在本母目录下直接写入 `01-topic.md`」，`write_episode_file` 却会放行 | → PR-1：企划目录不再能作为期目录打开（期会话、`write_episode_file` 都拒）；母目录已有的 `session.jsonl` 原样作为企划会话的历史（同名同格式，§4.2） |
| 3 | （上位需求未提）典藏版 | `00-全一期-典藏版/` 有 `01-topic.md`，按现规则它是一个「子期」，与三卷并列出现在列表里；qc 的 48–52 分钟门禁就读它的 `时长目标` | 合集期仍是企划下的一个子目录、仍有自己的 `01-topic.md`；`00-project.md` 用 `合集:` 字段标出它（§4.7），总装输出到它 |
| 4 | 读工具只能读当期目录与 `data/library/`，`..` 一律拒；bash 只回传尾部 16 KB | `tools.py::_tool_read_artifact`：`if rel.is_absolute() or ".." in rel.parts:`；`_allowed_read_roots` 只有期目录与 `data/library`；`jobs.py`：`SHELL_TAIL_BYTES = 16384` | 成立。补充：候选还含 `data/library/notes/`；只放行 `READ_ALLOWED_SUFFIXES = frozenset({".md", ".txt", ".json", ".patch", ".log"})` |
| 5 | 写工具只放行 `01-topic.md` / `02-script.draft.md` / `07-titles.md`；母目录文件只能 bash 写 | `EPISODE_WRITABLE_FILES` 现为 **5 个**：另有 `02-script.md`（D47）与 `02-adversarial.md`（D48）。且 `session.py`：`AUTO_APPROVED_TOOLS = frozenset({"write_episode_file", "write_note", "write_memory", …})`——ADR-0030 决策 3 起写期文件**全部免卡**（含 `01-topic.md`），回退靠 `_agent/file-history/` 留底 | **上位需求「照 `01-topic.md` 的纪律弹卡」前提已不成立**。→ 交人裁决（Q4） |
| 6 | （安全相关，上位需求未提） | `_tool_write_episode_file`：`confirmed = bool(args.get("confirmed", False)) or ctx.confirmed`——**`confirmed` 可由模型参数给**；`llm.py`：`replace(context, confirmed=True) if (approve is not None or not bare) else context`——经过审查层的调用一律 `confirmed=True`，免卡与人批**在执行层无法区分** | `00-project.md` 不能沿用这条：Q4 要求「机读字段变了必须人批」，执行层必须能分辨「人在卡上批了」。→ PR-1 引入 `ToolContext.human_approved` 与卡上绑定（§4.4） |
| 7 | 跨期记忆是一份全局 `data/library/memory.md`（4000 字）；scope 只是日志字段 | `memory.py`：`MEMORY_REL_PATH = "data/library/memory.md"`、`BUDGET_CHARS = 4000`；`apply_op` 注释「D43 / Spec 17：不再有 scope 闸」 | 成立。补充：ADR-0030 决策 3 后 `write_memory` 也免卡（日志 `"confirm": "auto" if plan.requires_card else "card_free"`）；仍在的护栏是校验规则（R1–R9）、`MemoryBudgetError` 超额即拒、`provenance_ok` 来源确认。三条路径被 `_require_lib` 钉死在 `data/library/` 下，复用要先参数化（§4.5） |
| 8 | 镜头撞车只在一期之内查；对抗审查只读本期稿件 | `clips.py::_overlaps`（键 `(anime, season, episode)`、`OVERLAP_GAP = 0.5`）；`adversarial.py::gather_evidence` 只读 `ep / script_name` 与番剧笔记 | 成立 |
| 9 | 总装靠手写 `assemble.sh`，不在白名单；qc 已认 `04-assembly.json` | `qc.py`：`ASSEMBLY = "04-assembly.json"   # 总装期部件账本（assemble.sh 写，qc 读）`；`PIPELINE_MODULES` 无 assemble | 成立。补充两处：① `assemble.sh` 把参数写死（`PARTS=("01-借躯降生" "02-皮套囚徒" "03-终局葬礼")`、过场卡 `anullsrc=r=48000:cl=mono`、`-ac 1`），换一个系列就要改脚本；② 盘上的 `04-assembly.json` 给未裁的三卷也写了 `src`，而脚本现版本只给 `work/` 下的截尾件写 `src`——**盘上账本不是现版本脚本产出的**，所以 PR-5 的「逐项一致」只比 `name / kind / offset / duration`，不比 `path / src` |
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

### 4.2 三种会话

| 会话 | 绑定 | 会话日志 | 租约 | scope |
|---|---|---|---|---|
| 普通期会话 | 期目录 | `<期>/session.jsonl`（不变） | 期目录 | 按 `resolver.scope_of`（不变） |
| **卷会话** | 卷目录；宿主同时绑定所属企划目录（由树规则得出，**不取模型参数**） | `<卷>/session.jsonl` | 卷目录 | 同上 |
| **企划会话**（新） | 企划目录 | `<企划>/session.jsonl`（EGOIST V2 母目录已有的这份原样当历史） | 企划目录（`EpisodeLease` 照用） | 新 scope `project`（`config/agent/scopes/project.md`） |

- 协议入口：`python -m pipeline.agent.protocol --project <企划目录> [--continue [<前缀>] | --fresh]`；`ready` 帧加键 `session_kind`（`"episode" | "volume" | "project" | "idea"`）与 `project`（企划目录名或 `null`）。
- 企划会话能做的事（Q2）：读整个企划（§4.3）、写 `00-project.md`（§4.4）、写企划记忆与全局记忆（§4.5）、`run_pipeline` 只放行 `PROJECT_PIPELINE_MODULES = {"assemble", "crosscheck"}` 与不绑期目录的只读模块 `subgrep`；其余模块拒绝，拒因写明「这是卷级工序，到卷 `<卷>` 的会话里跑」。`status` / `check_script` 也拒：它们要一个期目录，企划会话的自动补位是企划目录，补上去结果无意义；看某卷进度用 `read_status(episode=<卷>)`。企划会话里 `write_episode_file` 只认 `00-project.md`。
- 卷会话：`run_pipeline` 额外放行只读的 `crosscheck`（自动补位为所属企划目录）；`assemble` 只在企划会话里放行。
- 企划会话不处理停机点：母目录 `_agent/approvals.jsonl` 里的旧记录不读、不发 `stop_points`。

### 4.3 读域（`read_artifact`）

新增可选参数 `area`，取值 `"episode" | "project" | "library"`；不给时沿用现有候选顺序，并在末尾追加「企划顶层」候选（只在卷会话、且 `rel` 只有一段时）。

| 会话 | 允许的根（全部先 `resolve()`） | 额外条件 |
|---|---|---|
| 普通期 | 期目录、`data/library/`（不变） | — |
| 卷会话 | 卷目录、`data/library/`，**加上企划顶层文件** | 企划顶层候选：`rel` 只有一段，且 `target.resolve().parent == 企划目录.resolve()`。兄弟卷一律不在读域里 |
| 企划会话 | 企划目录，及每个成员目录各自的 `resolve()`（成员可以是人建的软链卷，TI-5 的夹具里就有） | — |

所有会话一律：

1. `rel` 是绝对路径或含 `..` → 拒（不变）。
2. **双端 resolve**：每个根先 `resolve()`；候选 `target = (根 / rel).resolve()`；只有 `target == 根 or 根 in target.parents` 才算在域内。软链（文件或目录）指出域外 → 不在域内。
3. **硬排除**：`deny_dir_hit` 对 `rel` 与 `resolve()` 后的 `target` 各判一次，casefold 后命中 `03-audio` / `04-patch` 任何一段即拒（不变，企划会话的跨卷路径如 `02-皮套囚徒/03-AUDIO/manifest.json` 同样命中）。
4. **记忆文件不可读**：`_is_memory_file` 扩成「`resolve()` 后的路径 casefold 等于全局 `memory.md`，**或** basename casefold 为 `00-memory.md`」。按 basename 判、不按位置判：经软链（`notes.md -> 00-memory.md`）读到的 `target` basename 仍是 `00-memory.md`，照拒。
5. 后缀白名单不变。

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
   - 不同（含 `before` 不存在，即首次立项）→ **弹卡**，卡面是机读字段的逐行 diff + 全文 diff；不开放「本会话都批准」（`allow_key = None`）。
   - 相同（只改了散文）→ 免卡。
3. 覆盖前留底到 `<企划>/_agent/file-history/`（与 ADR-0030 给 `01-topic.md` 补的留底同一函数 `_keep_history`）。
4. 目标路径：`(企划目录 / "00-project.md").resolve().parent == 企划目录.resolve()`，否则拒（`00-project.md` 本身是指向别处的软链时拒）。
5. `confirmed` **只从宿主来**：写 `00-project.md` 时不读 `args["confirmed"]`。

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
- **来源确认**：企划记忆注入前跑同一套 `provenance_ok`（正文 sha 对日志状态行）。bash 直接改了 `00-memory.md` → 注入改发告警（`[企划记忆未注入] …`）+ notice `project_memory_unconfirmed`，人用 `/memory --project ack`（或协议 `command{memory_ack, target:"project"}`）看 diff 后对账。
- 教训分两层（上位需求 B）：跨系列通用的进全局记忆或 `config/voice.json`；只属于本系列的进企划记忆。这条写进 `config/agent/scopes/project.md` 与 `creative.md` 的记忆段，不做机器判定（「是不是本系列独有」是判断，不是规则）。

### 4.6 上下文注入（卷会话与企划会话）

- 卷会话与企划会话在首轮（及压缩后未保留时）各注入一条 user 消息「[企划 · `<企划名>`]」：
  - `00-project.md` 全文 ≤ `PROJECT_DOC_INJECT_MAX_CHARS` → 注入全文；
  - 超过 → 注入机读头 + 卷表 + 散文各节的标题列表 + 一行「全文：`read_artifact(path="00-project.md", area="project")`」。**不从中间截断**；
  - 解析失败 → 注入前 `PROJECT_DOC_INJECT_MAX_CHARS` 字原文 + 一行解析错误（人要看到问题，模型也要知道卷表不可信）。
- 企划记忆按 `config/agent/assembly.json` 的 `memory.scopes` 门控（与全局记忆同一开关；`project` scope 加入该列表）。
- 注入文本先过 `scrub_restricted`：`00-project.md` 不是可信仓库文档（Spec 16 豁免只认仓库文档），里面若写了 `03-audio/manifest.json` 这类字样，不脱敏就会让整轮请求被出网断言拦下。
- **新鲜度**：已注入的 sha 记在 `tracker` 里（沿用 `_doc_shas`）。每轮开始重算；`00-project.md` 或企划记忆变了 → 本轮再注入一条「（已更新）」版本。企划会话改了卷表，正开着的卷会话下一轮就能看到。

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
| `目录` | 单段目录名，过 `_episode_name_problem`；各行不重复。**不判存在**（先写卷表再建卷是正常顺序），目录与成员对不上的情况由状态卡报提示（「卷表列了 `04-x` 但目录不存在」「目录 `05-y` 不在卷表」），不拦 |
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
3. 镜像上跑验收命令。一切输出只落在 `_d81-mirror/`（会话日志、租约）与 `_d81-out/`（报告、成片）内；经软链进入的卷目录**不得**成为任何写入的目标——这正是 PR-3 `--out` 与 PR-5 `--out` 存在的理由。
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
| ② | 同上 | `def resolve_episode_target(target: str) -> Path \| None:` 内 `for matched in ep_root.rglob(target):` | 删 `rglob` 回退；`glob("*/target")` 多于一个 → `None` + 候选清单（新函数 `resolve_episode_candidates` 返回列表，`resolve_episode_target` 包它）；结果必须落在 §4.1 的两种形状上；企划目录 → 拒 |
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
| ⑰ | `pipeline/agent/tools.py` | `PIPELINE_MODULES: set[str] = {`；`run_pipeline` 的自动补位 | 企划会话：模块不在 `PROJECT_PIPELINE_MODULES ∪ {"subgrep"}` → 拒并指路；补位目标为企划目录（`assemble` / `crosscheck` 的模块本身在 PR-4 / PR-5 才加，PR-1 只放好分流与拒因，用测试替身模块验证） |
| ⑱ | `pipeline/agent/cli.py` | `def create_new_episode(ep_name: str) -> int:`、`main` 里 `if args and args[0] == "new":` | 新 `ava new-project <企划名>`：建 `data/episodes/<企划>/` + 骨架 `00-project.md`（机读头只有 `企划:`，卷表空表头——空卷表合法，§4.7 解析器对 0 行卷表不报错，但 PR-5 总装拒）；`ava new --project <企划> <卷名>`：在企划下建卷 + `01-topic.md` 模板。两者都沿用 `_episode_name_problem`、`require_data_at`、绝不建 `data/`。新管理命令 `/project-meta --dir=<企划>`（宿主取解析结果） |
| ⑲ | `desktop/src/shared/protocol.ts` | `export type ConvKey = "idea" \| \`ep:${string}\`;`；`export interface EpisodesList {` | `ConvKey` 加 `` `proj:${string}` ``；`EpisodesList` 加 `projects: ProjectSummary[]`（企划键、`hasProjectFile`、成员 `EpisodeSummary[]`、企划会话的 `conv`） |
| ⑳ | `desktop/src/shared/convFrames.ts` | `ready: {` 的必需键表 | 加 `session_kind: ["string"]`、`project: ["string", "null"]`；TX-0（真实 core 实发键 vs 表）覆盖三种会话 |
| ㉑ | `desktop/src/host/spawner.ts`、`sessions.ts`、`service.ts` | `case "SESSION_IDEA":`；`const { visible, hiddenUnderscore } = listEpisodes(join(this.dataRoot, "episodes"), this.deps.fs);` | 模板 `SESSION_PROJECT`（`--project <abs>`，abs 由宿主映射并刚 stat 过，同 `SESSION_NEW` 纪律）；会话管理认 `proj:` 键；`service` 改用 `listEpisodeTree` 产出 `projects` |
| ㉒ | `config/agent/scopes/project.md`（新）、`config/agent/assembly.json`、`config/agent/scopes/creative.md` | `"scopes": ["creative", "asset", "idea"]` | 新 scope 提示词（企划会话能做与不能做的事、记忆两层分法）；`memory.scopes` 加 `project`；`routes` 加 `project: {"default": []}`；`creative.md` 记忆段加两层分法一句 |
| ㉓ | `.gitignore` | — | 不改（企划目录在 `data/` 下，本来不进 git） |

### 6.4 新常量与取值理由

| 常量 | 值 | 理由 |
|---|---|---|
| `PROJECT_FILE` | `"00-project.md"` | 上位需求定名；`00-` 前缀让它在 Finder 与 `ls` 里排在卷目录（`01-`…）之前，与 EGOIST 母目录现有 `00-project-plan.md` 习惯一致 |
| `PROJECT_MEMORY_FILE` / `_LOG` / `_LOCK` | `00-memory.md` / `00-memory.log.jsonl` / `00-memory.lock` | 三路径同目录是 `memory.py` 现有不变量（`_require_lib`：`if path.parent != lib`），企划实例照搬 |
| `PROJECT_MEMORY_BUDGET_CHARS` | 3000 | 人裁决 Q3（2026-10-12） |
| 企划记忆压力线 | 2597 | 原公式 `BUDGET − (ENTRY_MAX_CHARS + len(SEP))`，不另定 |
| `PROJECT_DOC_INJECT_MAX_CHARS` | 8000 | 与 `DIGEST_MAX_CHARS = 8000` 同一依据：「不大于仓库里最重的单篇注入文档」——今天最重的是 `skills/write-script/SKILL.md`（实测 8958 字）。EGOIST 的总纲原稿 `00-project-plan.md` 实测 11822 字，会走「机读部分 + 章节目录」分支，两条路径都有真实样本 |
| `PROJECT_NAME_MAX_CHARS` | 40 | 企划名进状态卡首行与侧栏（侧栏 1280 宽一行约 13 字，见 D80 §六），40 字给长名留余量；超了多半是把一句话当成了名字 |
| `YEAR_SPAN_MAX_CHARS` | 20 | `2011–2014`、`2020–2023（终场）` 这类写法都在 15 字内；超了说明写进了别的内容 |
| `TAIL_MAX_S` | 60 | 只做形状检查：余韵超过一分钟就不是「片尾余韵」而是一段音乐。EGOIST 实际取 18（README 红线 15–20），具体值由人在卷表里写，代码不替人定 |
| `PROJECT_PIPELINE_MODULES` | `{"assemble", "crosscheck"}` | 人裁决 Q2：企划会话只做规划、跨卷审查、总装 |

### 6.5 测试要求

期望值先在实现上跑一遍、看懂为什么是这个值再写进断言；每条安全规则配一条变异（§6.6）。

**pytest**（`tests/test_project_layer.py` 新，另扩既有文件）

- 树规则：临时根下造「普通期」「有 `00-project.md` 无成员的新企划」「无 `00-project.md` 有成员的旧企划（V1 形状）」「成员含软链卷」「企划目录自己也有 `01-topic.md`」「`_` 前缀成员」「第三层子期」，断言分组、隐藏数与排序；`get_episodes_list` 输出与改动前逐项相同（取改动前的实现跑一遍作基准，写进夹具）。
- 期名解析：V1 / V2 同名卷 → `None` + 两个候选；`"03-audio"` → `None`；企划目录 → 拒；绝对路径在 `data/episodes` 之外 → `None`。
- 读域（每条都在**卷会话**与**企划会话**各跑一遍）：
  - 卷会话读 `00-project.md`（无 `area`、`area=project` 两种）→ 可读；
  - 卷会话读兄弟卷：`../02-b/02-script.md`、`area=project` + `02-b/02-script.md`、企划顶层软链 `evil.md -> 02-b/02-script.md`、卷内软链 `x.md -> ../02-b/02-script.md` → 四条全拒；
  - 企划会话读 `02-b/02-script.md` → 可读；读软链成员内的文件 → 可读；企划内软链指向另一个企划的文件 → 拒；
  - `03-AUDIO/manifest.json`、`02-b/04-Patch/x.json`（大小写混写）→ 拒；目录软链 `audio -> 03-audio` 再读 `audio/manifest.json` → 拒（resolve 后命中）；
  - `00-memory.md`、`00-MEMORY.md`、软链 `m.md -> 00-memory.md`、全局 `memory.md` → 全拒。
- 写 `00-project.md`：
  - 卷会话写 `00-project.md` → 拒；企划会话写 `01-topic.md` → 拒；
  - 首次写（无 `before`）→ `ask`；只改散文 → `allow` 且留底；改卷表一格 → `ask`，卡面含该格 diff；只改表格对齐空白 → `allow`；
  - 解析失败（缺 `企划:`、卷序不连续、末行余韵、目录含 `/`）→ 弹卡前 `reject`；
  - **执行层**：不经审查层直接调 `_tool_write_episode_file`，`ctx.confirmed=True` 但 `human_approved=False`，机读字段有变 → `PermissionError`；`human_approved=True` 但 `bound["base_sha"]` 与盘上不符（模拟卡后被改）→ `PermissionError`；`args["confirmed"]=True` 对 `00-project.md` 不起作用；`00-project.md` 是指向别处的软链 → 拒。
  - 卡上绑定：`session_allow`、`card_free` 两条路径进执行层时 `human_approved` 为假（用协议进程集成测试走真实 `_review`）。
- 企划记忆：
  - 卷会话 `write_memory(target="project", op="add")` 写进 `<企划>/00-memory.md` 与其日志，全局文件字节不变；普通期会话 `target="project"` → 拒；
  - 写后超 3000 → `MemoryBudgetError`，文件字节不变；
  - 证据引用另一个企划的卷 → `MemoryRuleError`；
  - 全局记忆的既有测试全部不改且仍绿（`MemoryStore` 参数化不改全局行为）；
  - 来源确认：测试里直接改 `00-memory.md`（模拟 bash）→ 注入变告警、发 `project_memory_unconfirmed`；`ack` 后恢复注入；
  - 企划目录不可达 / 不存在 → 拒，且不建目录。
- 注入：卷会话首轮请求（假模型端点记录）含卷表各行与一条企划记忆正文；`00-project.md` 超 8000 字 → 只含机读部分与章节标题、不含正文中段；两轮之间改卷表 → 第二轮请求含「（已更新）」版本；`00-project.md` 写入 `03-audio/manifest.json` 字样 → 注入文本里是 `[已脱敏]`，请求不被出网断言拦下。
- 企划会话 `run_pipeline`：卷级模块（`tts`）→ 拒，拒因含「到卷 … 的会话里跑」；`status` → 拒，拒因指向 `read_status`；`subgrep` → 放行。
- 期目录守卫：`protocol <企划目录>`（期模式）→ `E_BAD_REQUEST`；`write_episode_file` 以企划目录为 `episode_dir` → 拒。
- `list_episodes`：V1 与 V2 同名卷给出两个不同的 `name`。
- `ava new-project` / `ava new --project`：名字不合规退 2；`data/` 不可达退 2 且不建；已存在退 1。

**vitest**

- TI-5 扩：`listEpisodeTree` 与 `cli.get_episode_tree(root)` 跨语言对拍，夹具同上，**另加一个含增补平面字符的成员名**（锁住码点序）；`listEpisodes` 与改动前对拍结果不变。
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
| M6 | `00-project.md` 分支改用 `args["confirmed"]` | 执行层 `args["confirmed"]=True` 那条 |
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

- [ ] §6.3 ①–㉒ 落地
- [ ] §6.5 测试全部通过，§6.6 M1–M18 每条实测变红（贴「改坏 → 红的测试名 → 恢复 → 绿」记录）
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
| ③ | `SessionList.tsx` | `convKey={\`ep:${e.epKey}\` as ConvKey}` | 企划会话的会话列表用 `proj:` 键 |
| ④ | `NewEpisodeForm.tsx` | — | 新建企划、在企划下新建卷（调 PR-1 的 `ava new-project` / `ava new --project` 模板） |
| ⑤ | `desktop/src/host/service.ts` | — | 企划行进度：成员的 `currentStep` / `isBlocked` / `conv` 已在 `EpisodeSummary` 里，直接用；卷表顺序取 `/project-meta`（失败时按名字码点序，不报错） |
| ⑥ | `style.css` | — | 人选方案的样式；`ui.css` 冻结不动（Spec 14 门禁 1） |

### 7.5 新常量

由人选方案决定（缩进量、进度显示长度等），每个写取值理由；施工时补进本节。

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

### 7.9 完成判定

- [ ] PR-2a：截图交付，人已选，写回 §二
- [ ] PR-2b：§7.4 落地，§7.6 测试与变异通过，§7.7 全绿
- [ ] 1280×800 与 1440×900 各一张实现截图给人看
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
| ① | `pipeline/adversarial.py` | `def gather_evidence(ep: Path, script_name: str) -> Evidence:` | 期目录是某企划的卷、且该企划 `00-project.md` 可解析时：按卷表找出卷序更小的卷；每卷只取 `02-script.md` 的旁白（`tts.parse_script` 的段落文本，带段号），**不取草稿**；缺 `02-script.md` 的前卷列进 `gaps`（判据 9：跳过要显式列出）。本卷不在卷表 → 不带前卷，`gaps` 写明原因 |
| ② | 同上 | `def build_user_prompt(ev: Evidence) -> str:` | 新增「前卷定稿」区块与审查要求（冲突 / 重复两类，必须给出前卷名与段号） |
| ③ | 同上 | `def verify_item(item: dict, ev: Evidence) -> dict:` | 新类别 `前卷冲突`、`前卷重复`：必须带 `prior_volume`、`prior_segment`；机械核销——该前卷存在于本次输入、段号存在、引文（`_squash` 后）是该段旁白的子串，否则标「引用核不上」 |
| ④ | 同上 | `def render_report(` | 报告新增「与前卷」一节，终审表（D48 ②）照旧由写稿会话填 |
| ⑤ | `config/agent/scopes/creative.md`、`docs/runbook/02-script.md` | — | 一句话：企划内的卷跑 02.8 会自动带前卷，不用手动贴 |
| ⑥ | `pipeline/adversarial.py` | `def write_report(ep: Path, text: str, now: datetime) -> Path:`；`def main(argv: list[str] \| None = None) -> int:` | 加 `--out <目录>`：报告写到该目录而不是期目录（目录必须已存在，不建）。默认行为不变。用途只有一个：§4.9 镜像里的卷是指向成品目录的软链，不加 `--out` 报告会经软链写进 EGOIST V2 |

### 8.4 新常量

| 常量 | 值 | 理由 |
|---|---|---|
| `PRIOR_NARRATION_MAX_CHARS` | 40000 | 前卷只给旁白。按 EGOIST 实测每卷 14–19 分钟，按 `script.cpm` 量级（每分钟约 280 字）一卷约 4000–5500 字，supercell 4 卷最多带 3 卷约 1.7 万字；取 40000 是两倍余量。**超了就报错停（`AdversarialError`），不截断**——截掉的那部分恰好可能是冲突所在（判据 5 的同一类问题） |

### 8.5 测试要求

- 夹具企划三卷：第 3 卷审查的输入含第 1、2 卷旁白，不含第 3 卷之后的卷、不含任何卷的草稿；第 2 卷缺 `02-script.md` → `gaps` 列出、第 1 卷照带；本卷不在卷表 → 不带前卷、`gaps` 有原因；普通期 → 输入与改动前逐字节相同（用改动前实现跑出基准）。
- `verify_item`：前卷名不在输入里 / 段号不存在 / 引文不是子串 → 「引用核不上」。
- 超 40000 字 → `AdversarialError`，不发请求（假端点请求数为 0）。
- 变异：带上草稿；带上卷序更大的卷；超限时截断而非报错；`verify_item` 不核前卷引文；普通期输入被改动。

### 8.6 验证命令

同 §6.7（本 PR 不动桌面端，后两条用来确认没碰坏）。

### 8.7 明确不做

- 不新增环节，不改 02.8 的提示 / 不拦口径（D48 ②：只提示、不拦）。
- 不给审查者兄弟卷的笔记、排片或音频；不让卷会话的读工具因此放开兄弟卷（§4.3 不变）。
- 不做跨企划的检查。

### 8.8 完成判定

- [ ] §8.3 ①–⑥ 落地；§8.5 测试与变异通过（另加：`--out` 给定时期目录前后 `find -newer` 为空）；§8.6 全绿
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
| ② | `pipeline/clips.py` | `def _overlaps(cand, chosen) -> bool:` 里的三元组键 | 把「这段素材是哪一集」的键抽成 `footage_key(clip)`，`_overlaps` 与 crosscheck 共用（一条规则，不复制） |
| ③ | `pipeline/crosscheck.py` | — | **C-1 镜头**：每卷读 `04-clips.approved.json`，没有则读 `04-clips.json`（报告注明用的是哪份）；都没有 → 该卷列入「未检查」。两卷间 `footage_key` 相同且时间区间交集 > 0 → 命中，报两卷名、段号、各自区间、交集长度 |
| ④ | 同上 | — | **C-2 配乐**：每卷用 `music.parse_script_music(02-script.md)`；非铺底块（`bgm_only=False`，即前景或片尾）的曲名（NFKC + 去首尾空白后逐字比较）在两卷以上出现 → 命中，报各卷块号、`after`（fade / bgm / end）与曲内区间。`parse_script_music` 抛 `SystemExit` → 该卷列入「未检查」并带原因，不中断其他卷 |
| ⑤ | 同上 | — | 退出码：0 = 跑完（有命中也是 0，警告不拦）；2 = 企划不可解析 / 卷表为空；「未检查」的卷在输出末尾显式列出（判据 9：跳过不是通过） |
| ⑥ | `pipeline/agent/tools.py`、`pipeline/agent/session.py` | `PIPELINE_MODULES`；`review_tool_call` 的「参数级免卡」段 | 加 `crosscheck`；参数级只读判定：不含 `--write` 才算只读（§4.8），入选前核实默认路径无写调用 |
| ⑦ | `pipeline/status.py` 或企划状态卡 | — | 企划状态卡显示「上次跨卷检查：N 条命中 / 未检查 M 卷」（读 `00-crosscheck.md`，没有则不显示） |

### 9.4 新常量

无。交集阈值取 0（任何正交集都报）：这是警告，不是门禁；误报的代价是人看一眼，漏报的代价是合集里同一镜头出现两次。报告带交集长度，0.04 s 级的帧量化抖动人一眼能认出。

### 9.5 测试要求

- 夹具三卷：同集同区间 → 命中；同集不相交 → 不命中；不同番同 `S01E01` → 不命中（复用 `footage_key`，2026-09-06 跨番改造的同一教训）；某卷只有 `04-clips.json` → 报告注明来源；某卷两份都没有 → 「未检查」。
- 配乐：同曲前景两卷 → 命中；同曲一卷前景一卷铺底 → 不命中；曲名全角 / 半角差异经 NFKC 后相同 → 命中；某卷 `02-script.md` 音乐段格式错 → 该卷「未检查」，其余照查。
- 默认运行前后企划目录 `find -newer` 为空（只读）；`--write` 只写 `00-crosscheck.md` 与留底。
- 变异：区间判定改成 `≥ 0`（相邻也报）；把铺底块算进 C-2；格式错的卷让整个命令失败；默认路径写了文件；`footage_key` 退回 `(season, episode)` 二元组。

### 9.6 验证命令

同 §6.7。

### 9.7 明确不做

- 不拦截、不改任何卷的产物；不自动换片、不自动换曲。
- 不查画面相似（只查同一素材的时间重叠）。
- 不查合集期自身。

### 9.8 完成判定

- [ ] §9.3 ①–⑦ 落地；§9.5 测试与变异通过；§9.6 全绿
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
| ⑩ | 同上 | `"$PY" - "$DIR" "$OUT" "$GOT" "${ASSEMBLE[@]}" <<'PY'` 那段账本代码 | 写 `04-assembly.json`，形状与现账本相同（`total_duration`、`parts[{name, path, kind, offset, duration, src}]`、`_note`）；`kind=part` 的部件一律写 `src` = 成员目录（qc 字幕四项要回产出它的那一卷读 plan 与 manifest；修掉 §三 #9 的不一致） |
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

- **黄金测试（与 `assemble.sh` 等价）**：给定 EGOIST 的探测签名（写成夹具）与截断点 889.53 / 866.74，生成的三条 ffmpeg 命令（过场卡、截尾、concat）的 argv 与从 `assemble.sh` 推出的 argv 逐 token 相等（期望 argv 先手工从脚本展开、在实现上跑一遍核对后写进断言）。
- 前置检查：卷表目录写成 `../x`、`/abs`、`a/b` → 解析期就拒；目录不是成员（无 `01-topic.md`）→ 退 2；缺 `05-final.mp4` / `08-cover.jpg` / manifest → 退 2；以上全部在任何 ffmpeg 调用之前（用记录调用的 runner 替身断言调用数为 0），且输出目录前后 `find -newer` 为空（没建 `cards/`、`work/`）。
- 覆盖：已有 `05-final.mp4` 不带 `--replace` → 退 2、旧文件字节不变、ffmpeg 调用数 0；带 `--replace` 但自证失败（替身让时长差 0.2 s）→ 旧文件字节不变；带 `--replace` 且通过 → 新文件到位。
- 输入指纹：执行中途替身改动某卷 `05-final.mp4` 的 mtime → 退 1、不替换、不写账本。
- 签名不一致（替身让过场卡探出 `yuvj420p` + 立体声）→ 退 1，输出含 diff，不拼接。
- concat 转义：成员目录名含 `'` 的夹具，生成的 concat 清单经 ffmpeg 真实解析能拼出正确部件数（小样片，`ffmpeg -f lavfi` 现造 1 秒片子，不 mock ffmpeg——AGENTS.md「不 mock ffmpeg」）。
- 静态：`pipeline/assemble.py` 不含 `shell=True`、`os.system`、`os.popen`、`subprocess.getoutput`。
- 审查层：模型发 `run_pipeline assemble` → `ask`、无 `allow_key`；卷会话发 → 拒；`--rep`（缩写）→ argparse 报错（不被当成 `--replace`）；bash `python -m pipeline.assemble …` → 带 `[须人审的流水线命令]` 弹卡（依赖 D82 PR 的规则，本 PR 加一条用例）。
- 端到端小样（真实 ffmpeg、合成片）：两卷 + 合集期的夹具企划，跑完 qc 能读到账本，`04-assembly.json` 的 `offset` 连续、`duration` 之和与成片差 < 0.1 s。

### 10.6 变异清单

| # | 改坏什么 | 由哪条测试抓 |
|---|---|---|
| M1 | 不查已有成片就开拼 | 覆盖：不带 `--replace` |
| M2 | 先删旧成片再写新的 | 覆盖：自证失败旧文件不变 |
| M3 | 去掉签名比对 | `yuvj420p` + 立体声 |
| M4 | 截断点用卷表时长预算代替 `build_timeline` | 黄金测试（889.53 / 866.74） |
| M5 | 末卷也裁 | 黄金测试（末卷 `-c copy`）与卷表末行规则 |
| M6 | concat 清单不转义 | 含 `'` 的目录名 |
| M7 | 部件路径不做 resolve 包含检查 | 软链指出成员目录之外的 `05-final.mp4` |
| M8 | 不复核输入指纹 | 中途改 mtime |
| M9 | `allow_abbrev` 默认 | `--rep` |
| M10 | 卡片开放「本会话都批准」 | 审查层无 `allow_key` |
| M11 | 流参数写死为 mono | 立体声夹具卷生成的过场卡签名与主体不符 → 本该通过的用例变红 |

### 10.7 验证命令

同 §6.7。另加真实数据验收（§10.9）的命令，由人批卡执行。

### 10.8 明确不做

- 不重配 TTS、不重渲分期主体（`assemble.sh` 原则 1）。
- 不改 qc 的 11 项门禁与 `assembly_ledger` 读法。
- 不给过场卡做新设计；过场卡素材仍是各卷 `08-cover.jpg`。
- 不处理「合集也要有自己的封面 / 标题」（08 工序照旧在合集期里做）。
- 不删除 EGOIST 典藏版下的 `assemble.sh` 与 `01.5-assembly-spec.md`（成品目录只读）。

### 10.9 完成判定

- [ ] §10.3 ①–⑮ 落地；§10.5 测试与 §10.6 M1–M11 通过；§10.7 全绿
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

## 十二、明确不做（全局）与残余风险

- 上位需求 §3 三条：结构化史实数据库、跨卷自动写稿 / 自动拆卷、三层以上嵌套。
- 不新增工具（ADR-0025 封顶，§三 #10）。
- 不改 ADR-0030 已免卡的任何项；不给 bash 加读域限制。
- **残余风险（写给红队，不在本 spec 修）**：bash 能 `cat` 兄弟卷与 `data/library/memory.md`、能改 `00-project.md`（绕过卡，但下一次经工具写入时 `base_sha` 对不上会被拒，注入侧会注入被改后的文本）、能用 ffmpeg 直接覆盖 `05-final.mp4`（`05-final.mp4` 不在 ADR-0030 的解封物清单里）。这些都是 ADR-0030「静态规则可绕过」的同一类，推翻条件按 ADR-0030 原文执行。

## 十三、验收（UI 规则 2）与总完成判定

1. **【人勾】真实任务**：打包版、1440×900 与 1280×800，建 supercell 编年史企划，在企划会话里定总纲与卷表，建第一卷并开写；记下别扭之处（agent 不得代勾，不得用 mock 或截图替代）。
2. **【人勾】截图选型**：PR-2a 的方案已选，写回 §二。
3. **【人判】真实数据**：PR-3 的「与前卷」报告、PR-4 的跨卷命中项，人已逐条判断并写回对应节。
4. 机检：每个 PR 的验证命令全绿、变异全杀、只读验收 `find -newer` 为空。

- [ ] PR-1 完成（§6.9）
- [ ] PR-2 完成（§7.9），**待人验收**
- [ ] PR-3 完成（§8.8）
- [ ] PR-4 完成（§9.8）
- [ ] PR-5 完成（§10.9）
- [ ] 本节 1–3 人已勾
