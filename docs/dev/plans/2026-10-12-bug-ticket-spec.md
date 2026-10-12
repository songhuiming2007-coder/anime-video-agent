# D82：生产中撞 bug → ava 开工单 → coding agent 修 施工 spec

> 对应 issue：**D82**（`docs/dev/issues/README.md`）。上位需求：[`2026-10-12-bug-ticket-direction.md`](2026-10-12-bug-ticket-direction.md)（A–D 四节）。本 spec 以代码现状为准，与上位需求不符之处见 §三。
> 相关：ADR-0025（工具表封顶 15）、ADR-0029 / ADR-0030（bash；审批只拦硬危险）、D51 / D60（`command_preview` 卡面先例）、D52（工具结果脱敏）、D53（argv 预检）、D80（UI 先例）、D81（姊妹 spec [`2026-10-12-project-layer-spec.md`](2026-10-12-project-layer-spec.md)）。
> **PR-B / PR-C 含 UI，plans/README 的 UI 五条规则对它们生效**：场景见 §一，「复制给 coding agent」按钮的样子由人从截图里选（PR-B），验收第 1 条是人在打包版里做一件真实工作（§九）。
> **仓库是公开的**（GitHub `songhuiming2007-coder/anime-video-agent`）：工单原文含输出、本机路径、期名，**永远不进 commit**。

---

## 一、目标与用户场景（UI 规则 1）

**目标**：ava 受 Code Freeze 约束不能改 `pipeline/` 与 `config/`。撞到真 bug 时，今天只能由人切到 coding agent、凭记忆复述现场。改成：ava 经人确认写一份带现场的工单，人把工单交给 coding agent，修完 ava 知道从哪一步续跑。

**时机**：supercell 第一卷开始生产（03 配音起）时可用。依据（上位需求）：EGOIST 制作期 2026-09-11 至 09-13 三天仓库 40 多个提交，多数是 tts / music / clips / qc 的边做边修。

**界面场景**（PR-B 截图方案按这个场景出）：

- **谁**：人本人，正在 supercell 第一卷的卷会话里跑工序。
- **窗口**：1280×800 与 1440×900；侧栏、对话区常开，预览默认收起。侧栏内容量同 D81 §一：supercell 4 卷、EGOIST V2 三卷 + 典藏版、EGOIST V1 三卷、约 20 个普通期。
- **连续做什么、每步要看到什么**：
  1. ava 跑 `run_pipeline tts` 失败（退出码非 0）。要看到失败的工具行（D80 方案 A 的样子，不变）。
  2. ava 判断是代码 bug，提议开单，弹卡。**不点开任何东西**就要看到：工单全文的开头（命令、退出码、报错行）与「为什么不是用法错误」那一段；是否追加到已有工单。卡在对话流末尾（D80 R6 人选方案 C），1280×800 下卡头与批准按钮同时可见。
  3. 人批准。要看到工具行变成「已开工单 T-…」，旁边有「复制给 coding agent」。
  4. 人点复制，切到终端里的 coding agent 粘贴。要确认复制成功（按钮短暂变「已复制」，D76 的 `CopyButton` 口径）。
  5. 几小时后 coding agent 修完。人重新打开第一卷，开一个会话。要在对话区顶部看到一条提示：「工单 T-… 已修（提交 abc1234），从 03 重跑：`tts --redo 5`」。
  6. 人照做；卷走过 03 之后再开会话，这条提示不再出现。

## 二、裁决记录

| # | 问题 | 人的原话（2026-10-12，AskUserQuestion 选项原文） | 施工注记 |
|---|---|---|---|
| Q1（上位 §3-1） | 工单存哪里 | 「仓库内 tickets/，加进 .gitignore (推荐)」 | 目录 `<仓库根>/tickets/`；`.gitignore` 加 `/tickets/`；静态测试 + 运行时 `git check-ignore` 双重把关（§4.6） |
| Q2（上位 §3-2） | 「复制给 coding agent」按钮 | **未定**——照 UI 规则 4，PR-B 出 2–3 套真实窗口截图，人选 | 人选后写回本表 |
| — | 开单走新工具还是 `run_pipeline` 模块 | **本 spec 的设计选择，待红队审**（不是人裁决） | 走 `run_pipeline tickets`，理由见 §三 #1。若审查认为必须是独立工具，需另立 ADR（ADR-0025：第 16 个工具须再立 ADR） |

## 三、现状核对（2026-10-12）

| # | 上位需求的说法 / 前提 | 代码现状（引原文） | 结论与对设计的影响 |
|---|---|---|---|
| 1 | 「开单工具（新工具，一律弹卡）」 | ADR-0025 修订注：「第 15 个工具 `bash` … 封顶上调至 15；第 16 个须再立 ADR」；`config/agent/tools.json` 现 15 个 | 新工具要先立 ADR。本 spec 改走 `run_pipeline` 的新模块 `pipeline.tickets`：卡面用已有的 `command_preview`（D51 `corrections.preview`、D60 `calibration.preview` 先例）显示工单全文；「一律弹卡」用 `CONFIG_WRITE_COMMANDS` 同款的逐条人审集合实现。代价：模型填的长文本走 argv（`shlex` 能处理引号与换行，`run_pipeline` 已 `shlex.split`） |
| 2 | 「输出尾部（沿用现有密钥脱敏）」 | `tools.py`：`scrub_secrets` 只用在 bash——`"stdout_tail": scrub_secrets(done.stdout_tail, ctx.root),`；`run_pipeline` 返回 `"stdout_tail": executed_job.stdout_tail,`，**没过 `scrub_secrets`**；工具结果进会话前统一过 `scrub_restricted`（`_tool_message`） | 工单的系统采集段对**所有**来源的文本再过一遍 `scrub_secrets` + `scrub_restricted`（两者对干净文本幂等）；模型填写的字段同样过 |
| 3 | 现场怎么拿 | 会话日志每条记录带 `origin`（`_record`：`record = {**record, "origin": origin}`）；真实执行的工具结果是 `control.commit(_tool_message(call, outcome), "tool")`，被拒 / 预检拒 / 判重是 `"synthetic_tool"`；结果内容是 `json.dumps(outcome)` 经 `scrub_restricted` | 系统按模型给的 `tool_call_id` 从**会话日志**取现场（不从模型参数取）：只认 `origin == "tool"` 的结果，即真跑过的命令。预检拒、人拒的调用不能开单——那是用法问题，不是代码 bug（D53 即一例）。从日志取而不是从内存消息取：压缩、D65 工具结果老化都不影响日志原文 |
| 4 | 「同一命令、同一报错首行」去重 | bash 输出只回传尾部（`SHELL_TAIL_BYTES = 16384`），`run_pipeline` 作业尾部 4 KB（`jobs.py`：`tail_bytes: int = 4096`） | 输出是**尾部**，第一行是被截断处的任意一行，不稳定。改用**最后一个非空行**：Python 的 traceback 末行是异常本身，本仓库 `raise SystemExit("FAIL …")` 的失败原因也在末行。与上位需求字面不同，理由如上 |
| 5 | （上位需求未提）bash 绕卡 | 实测 `status_card.bash_danger_tags(".venv/bin/python -m pipeline.corrections global …")` 返回 `[]`；`python -m pipeline.calibration set x` 返回 `[]` | **现有缺口**：ADR-0030 决策 2 规定写 `config/` 的三条命令逐条弹卡，但 bash 直接 `python -m` 跑它们不弹卡（`_FREEZE_PATH` 只认字面路径 `pipeline/`、`config/`）。`tickets open` 若不补这条，同样能经 bash 绕过人卡。本 spec PR-A 加一条通用规则（§4.4），**顺带堵上这三条命令的现有缺口**——这是行为变化，单列在 §十 交红队与人看 |
| 6 | 「ava 开会话时看到本期有已修的工单，提醒人」 | `notice` 帧形状 `notice: { level: ["string"], code: ["string"], text: ["string"] }`；notice 是给人看的帧，不进模型消息 | 提醒走 `notice`（新 `code: "ticket_fixed"`，键不变），不进模型上下文：工单「处理结果」段是 coding agent 写的，不该原样注入生产 agent |
| 7 | 工单目录要不进 git | `.gitignore` 现无 tickets；仓库里也没有任何守 `.gitignore` 的测试 | PR-A 加静态测试；写入前运行时再查一次（§4.6） |

## 四、设计

### 4.1 命令形状

```
python -m pipeline.tickets open  <期目录> --call <tool_call_id> --blocked <工序>
        --symptom <现象> --expected <预期> --repro <最小复现> --why <为什么是代码 bug 而不是用法错误>
python -m pipeline.tickets list  [<期目录>] [--json]      # 只读
python -m pipeline.tickets show  <工单号>                 # 只读
```

- `<期目录>` 由 `run_pipeline` 自动补位（会话绑定的期 / 卷目录；D81 落地后企划会话补企划目录）。模型不能指定别的期。
- `--blocked` 取值固定：`01 02 02.5 02.8 03 03.5 04 05 06 07 08 09`（WORKFLOW 九步加人工环节编号）。
- 模型**只能**给上面五个文本字段与 `--call`、`--blocked`。系统采集字段没有对应参数：给 `--exit` 之类 → argparse 报错，审查层在弹卡前拒（`preview` 不成立）。
- argparse 用 `allow_abbrev=False`。

### 4.2 系统采集（模型改不了）

| 字段 | 来源 |
|---|---|
| 命令 | 会话日志里 `tool_call_id` 对应的 assistant `tool_calls` 参数：bash 取 `command`；`run_pipeline` 取 `command` 并按 `validate_pipeline_command` 规范化成 argv |
| 退出码、输出尾部 | 同一 `tool_call_id` 的工具结果记录（`origin == "tool"`）里的 `returncode`、`stdout_tail`、`stderr_tail`、`message` / `error` |
| 代码版本 | `git -C <仓库根> rev-parse HEAD`；`git -C <仓库根> status --porcelain=v1 --untracked-files=no` → 是否有未提交改动 + 前 `TICKET_DIRTY_LIST_MAX` 个路径 |
| 期、会话、时间 | 期键（相对 `data/episodes/`，如 `supercell-编年史/01-xxx`）、会话 sid（日志里该调用所在段）、UTC 时间 |

取现场的规则（不满足任何一条 → `preview` 返回不成立，审查层弹卡前拒，拒因说明是哪条）：

1. `tool_call_id` 形状：`[A-Za-z0-9_.:-]{1,128}`。
2. 在该期 `session.jsonl` 里**恰好**有一个 assistant 记录含这个 id 的调用，且**恰好**有一个对应的工具结果记录。
3. 工具名是 `bash` 或 `run_pipeline`。
4. 结果记录 `origin == "tool"`（真跑过），且 `returncode` 是非 0 整数。
5. 结果内容能解析为 JSON 对象。

卡面附加的机械提示（不拦）：退出码为 2 且输出含 `usage:` → 首行加「⚠ 退出码 2 且输出含 usage：多半是参数用法错误，请看清『为什么不是用法错误』再批」。

### 4.3 工单文件

- 位置：`<仓库根>/tickets/<工单号>.md`；工单号 `T-<UTC yyyymmddTHHMMSSZ>-<去重键 sha256 前 6 位>`。文件名不含期名（期名可能带任意字符）。
- 格式：

```markdown
# 工单 T-20261020T061502Z-a1b2c3

状态: 待修
期: supercell-编年史/01-xxx
阻塞: 03
去重键: tts --redo 5 ⟂ RuntimeError: …

## 现场（系统采集，勿改）
- 时间: 2026-10-20T06:15:02Z
- 会话: <sid>
- 代码版本: <HEAD>（有未提交改动：pipeline/tts.py …）
- 命令:
  ```
  …
  ```
- 退出码: 1
- stderr 尾部:
  ```
  …
  ```
- stdout 尾部: …

## 模型填写
### 现象
### 预期
### 最小复现
### 为什么是代码 bug 而不是用法错误

## 复现记录
（去重命中时追加：时间 / 会话 / 代码版本 / 期）

## 处理结果（coding agent 填）
状态: 已修 | 不是bug | 不修
提交:
续跑:
```

- 状态取值：`待修`（开单时）、`已修`、`不是bug`、`不修`。只有 coding agent（或人）改状态；ava 侧没有任何写工单状态的入口。
- 所有文本（系统采集与模型填写）写入前过 `scrub_secrets` + `scrub_restricted`。
- 写入：持 `tickets/.lock`（`fcntl`，照 `session_titles.py` 先例）完成「查重 → 写新单或追加」；落盘用 `paths.atomic_write`。

### 4.4 一律弹卡，且 bash 绕不过

- 新增集合 `HUMAN_GATED_COMMANDS`（定义在 `tools.py`：`status_card` 已从 `tools` 导入 `scrub_restricted`，放这里两边都能引用而不破叶子性；`session.py` 的 `CONFIG_WRITE_COMMANDS` 改为它的子集）：`CONFIG_WRITE_COMMANDS` 的三条 + `("tickets", "open")`（D81 PR-5 再加 `("assemble", None)`）。`run_pipeline` 命中 → 逐条弹卡、不开放「本会话都批准」，卡面 `command_preview = tickets.preview(argv)`（工单全文、是否追加到已有单）。`tickets list` / `show` 进 `READONLY_PIPELINE_MODULES` 的同口径免卡（只读子命令，入选前核实无写调用）。
- `status_card.bash_danger_tags` 加一条规则：命令里出现 `pipeline.<模块>` 或 `pipeline/<模块>.py`，且后面跟着 `HUMAN_GATED_COMMANDS` 里该模块的子命令（`None` 表示任何调用）→ 标 `[须人审的流水线命令]`。这条规则从 `HUMAN_GATED_COMMANDS` 生成，不另写一份清单。
- 这条规则是 ADR-0030 决策 1 的危险清单的扩充：PR-A 同时给 ADR-0030 加修订注。

### 4.5 去重

- 去重键 = `规范化命令 ⟂ 报错行`：
  - 规范化命令：`run_pipeline` 取规范化 argv 中模块名之后的部分，自动补位的期目录替换为 `<期>`（同一个 bug 在不同卷上复现能合并）；bash 取 `command` 折叠空白。
  - 报错行：`stderr_tail` 的最后一个非空行；为空则取 `stdout_tail` 的；再为空取 `error` / `message`。逐字比较，不做模糊匹配。
- 命中**状态为待修**的工单 → 追加一条「复现记录」，不新开；卡面写明「将追加到 T-…」。
- 命中**已修**的工单 → 新开，并在新单首行写「与已修工单 T-… 同签名，可能是回归」。
- 不同报错行 → 新开。

### 4.6 不进 git

- `.gitignore` 加 `/tickets/`。
- **静态测试**（pytest，跑在仓库里）：`git check-ignore -q tickets/T-x.md` 退出码 0；`git ls-files tickets` 输出为空。
- **运行时**：`tickets open` 写盘前对目标路径跑 `git -C <仓库根> check-ignore -q <路径>`（argv），非 0 或 git 不可用 → 拒写（「tickets/ 没被 gitignore 覆盖，拒绝写入（公开仓库）」）。
- 工单内容不出网：`tickets open` 只向 stdout 打印工单号、路径与一行摘要，以及最后一行机读 `TICKET_JSON {…}`（§4.7），不打印工单正文，所以工具结果里不会出现工单原文。

### 4.7 交接（第一阶段：一键复制）

- `tickets open` 成功时 stdout 末行打印 `TICKET_JSON {"id": "...", "path": "...", "appended": false}`。
- core 组装 `run_pipeline` 的 tool 帧时（`session.py` / `protocol.py` 现有 `frame["command"]`、`frame["description"]` 那段），若模块是 `tickets open` 且成功：严格解析末行；`path` 的 `resolve()` 必须在 `<仓库根>/tickets/` 内；通过后加条件键 `handoff`（字符串），**文本由 core 的模板生成**，不取模块输出里的任意文字：

  > 请处理工单 `<绝对路径>`：先读工单，按仓库 AGENTS.md 出修复方案等我批准，不要直接改；修完在工单「处理结果」写状态、提交号与续跑命令，并把它整理成 docs/dev/issues/README.md 的正式条目（工单原文不进 commit）。

- 历史重建的 tool 帧（`history`）同样带 `handoff`（core 从日志里的结果重算），重启后按钮还在。
- `convFrames.ts` 的 `CONDITIONAL.tool` 登记：有 `handoff` 键时必须是字符串，否则 malformed。
- 渲染端按人选方案放「复制给 coding agent」按钮，复用 `copy.tsx` 的 `CopyButton`。
- **第一阶段不自动拉起 coding agent**：宿主与 core 都不 spawn 任何外部 agent；交接只经剪贴板，由人发出。

### 4.8 闭环提醒

- 期会话（及 D81 后的卷会话、企划会话）启动、`ready` 之后：扫描 `tickets/*.md`，挑出 `期:` 等于本期键、`状态: 已修` 的工单。
- 只在本期**还没走过**被阻塞的工序时提醒：`inspect_episode(期).current_step` 的工序键（`assembly.STEP_KEY_MAP` 同一套）在 `阻塞:` 之前或相同 → 提醒；已经过了 → 不提醒。不另存「已提醒」状态（状态即产物）。
- 提醒是一条 `notice{level:"info", code:"ticket_fixed"}`，文本只由系统解析出的字段组成：工单号、提交号（必须匹配 `[0-9a-f]{7,40}`，否则写「未填」）、阻塞工序、`续跑:` 行（单行、过脱敏、超 `TICKET_RESUME_MAX_CHARS` 截断并标「…」）。**不进模型消息**。
- 解析失败的工单（缺状态行等）跳过并在 notice 里计数「N 份工单解析失败」，不静默。

## 五、PR 切分与顺序

| 顺序 | PR | 内容 | 动到 | 前置 |
|---|---|---|---|---|
| 1 | **PR-A** | core：`pipeline.tickets`、审查与卡面、`HUMAN_GATED_COMMANDS` 与 bash 规则、`handoff` 帧键、闭环提醒、`.gitignore` 与静态测试、ADR-0030 修订注 | core + `convFrames.ts` 契约 | 无（与 D81 PR-1 无硬依赖） |
| 1′ | **PR-B** | 截图选型：「复制给 coding agent」按钮与已开工单的工具行样子，2–3 套 | 只加附件 | 无，可与 PR-A 并行 |
| 2 | **PR-C** | 按人选方案施工按钮；`ticket_fixed` notice 用现有 notice 样式，不另做 | renderer | PR-A、PR-B 人已选 |

在 D81 中的位置：D81 PR-1 → PR-2 → **本 spec** → D81 PR-3 / PR-4 / PR-5。排在 D81 PR-1 之后是时机（第一卷开写前要企划层，开始生产时要工单）；D81 PR-5 的「总装经 bash 也弹卡」依赖本 spec PR-A 的规则。

---

## 六、PR-A：开单、去重、闭环（core）

### 6.1 目标与对应 issue

D82 A、B、C（core 部分）、D。

### 6.2 前置条件

- `uv run pytest` 基线全绿。
- 不需要数据盘：测试用临时期目录与伪造的 `session.jsonl`。仓库是 git 仓库（静态测试依赖）。

### 6.3 改动清单

| # | 文件 | 锚点（原文） | 改动 |
|---|---|---|---|
| ① | `pipeline/tickets.py`（新） | — | `open` / `list` / `show`（§4.1）；`preview(argv) -> (ok, lines)`（审查层复用：取现场、渲染全文、查重结论）；`collect(ep_dir, call_id)`（§4.2 五条规则）；`dedupe_key`、`render`、`append_repro`；`find_fixed(ep_key, current_step)`（§4.8）。只用 `subprocess.run(["git", …])` 这一种外部调用 |
| ② | `pipeline/agent/session_log.py` | `def append(self, record: dict[str, Any]) -> None:` 附近的读日志函数 | 新纯函数 `find_tool_exchange(records, call_id) -> (call_args, result_record) \| 原因`：按 §4.2 第 2、3 条找配对；不改写日志 |
| ③ | `pipeline/agent/tools.py` | `PIPELINE_MODULES: set[str] = {`；`PIPELINE_VALUED_FLAGS: frozenset[str] = frozenset({`；`_AUTOFILL_MODULES`；`_SUBCOMMAND_WORDS` | 加模块 `tickets`；带值旗标加 `--call`、`--blocked`、`--symptom`、`--expected`、`--repro`、`--why`（D53 的 ast 守卫测试会扫到）；`tickets` 进自动补位与子命令词（`open`、`list`、`show`）；`tickets list` / `show` 的只读放行照 `_readonly_asset_subcommand` 的口径 |
| ④ | `pipeline/agent/session.py` | `CONFIG_WRITE_COMMANDS = frozenset({("corrections", "global"), ("calibration", "set"), ("bgm", "register")})` 与 `review_tool_call` 里 `elif (module, rest[0] if rest else None) in CONFIG_WRITE_COMMANDS:` | `review_tool_call` 改用 `tools.HUMAN_GATED_COMMANDS`（§4.4）；`tickets open` 先跑 `tickets.preview`，不成立 → `reject`（拒因即 preview 的原因），成立 → `ask`、`command_preview` = 工单全文、无 `allow_key` |
| ⑤ | `pipeline/agent/status_card.py` | `def bash_danger_tags(command: str) -> list[str]:`、`_BASH_DANGER_RULES` | 加由 `tools.HUMAN_GATED_COMMANDS` 生成的规则，标 `[须人审的流水线命令]`（§4.4）。`status_card` 不 import `session`（叶子性），只引 `tools` |
| ⑥ | `pipeline/agent/session.py`、`protocol.py` | `frame["command"] = self._tool_commands.get(index, "")`；`frame["command"] = command if isinstance(command, str) else ""` | `tickets open` 成功时加 `handoff`（§4.7）；实时帧与 history 帧两处 |
| ⑦ | `desktop/src/shared/convFrames.ts` | `export const CONDITIONAL: Partial<Record<OutType, …>> = {` | `tool`（与 `history` 的工具行）登记 `handoff: ["string"]`（有键才查）；TX-0 用真实 core 的 `tickets open` 帧比对 |
| ⑧ | `pipeline/agent/protocol.py` | 发 `ready` 之后、首个回合之前 | 调 `tickets.find_fixed` → 发 `notice{code:"ticket_fixed"}`（§4.8）。不进 `messages`、不落进模型请求 |
| ⑨ | `.gitignore` | `# 桌面端（Spec 8）：依赖与构建产物不进库` 那段之后 | 加 `/tickets/` 与一行注释（公开仓库，工单含输出原文与本机路径） |
| ⑩ | `tests/test_tickets_static.py`（新） | — | §4.6 静态两条 |
| ⑪ | `config/agent/scopes/creative.md`、`docs/runbook/`（WORKFLOW 停机规程旁） | — | 什么时候开单：确信是引擎缺陷（AGENTS.md Code Freeze 的「三项汇报」）时，用 `tickets open` 代替在对话里复述；先排查当期输入；`--why` 必填且要具体 |
| ⑫ | `docs/dev/adr/0030-approval-danger-only.md` | 「## 取代与修订」 | 加修订注：危险清单扩入「须人审的流水线命令」（含 `corrections global` / `calibration set` / `bgm register` 经 bash 的现有缺口），日期与本 spec 链接 |

### 6.4 新常量与取值理由

| 常量 | 值 | 理由 |
|---|---|---|
| `TICKETS_DIR` | `tickets`（仓库根下） | 人裁决 Q1 |
| `TICKET_DIRTY_LIST_MAX` | 20 | 只用来让 coding agent 知道「现场有哪些未提交改动」；一次生产里本不该有未提交的引擎改动（Code Freeze），超过 20 个本身就说明现场不干净，写「另有 N 个」即可 |
| `TICKET_FIELD_MAX_CHARS` | 4000 | 单个模型字段的上限。`--why` 要求具体，但 4000 字已超过一个 `run_pipeline` 作业尾部（4096 字节）的量级；更长多半是把输出原文又贴了一遍，那部分系统已采集 |
| `TICKET_WHY_MIN_CHARS` | 20 | 只拦「因为是 bug」这类空话（D53 那次模型把参数写错当成 bug，就是没说清依据）；20 字够写一句「同一参数在 X 期能跑、这里报 Y，且输入格式已核对」。判断真伪的是人，不是这个数 |
| `TICKET_RESUME_MAX_CHARS` | 200 | 提醒是一行 notice；续跑命令通常是一条 `run_pipeline` 命令，200 字足够 |
| `--blocked` 取值表 | 12 个工序号 | 与 WORKFLOW 九步和人工环节编号一一对应，`assembly.STEP_KEY_MAP` 的值域加 `02.8`、`04` |

### 6.5 测试要求

期望值先在实现上跑一遍再写断言；每条安全规则配一条变异（§6.6）。

**pytest**（`tests/test_tickets.py` 新）

- 取现场：伪造 `session.jsonl`——
  - bash 真跑失败（`origin: tool`、`returncode: 1`）→ 成立，命令、退出码、尾部逐字来自日志；
  - `run_pipeline` 被白名单拒（`synthetic_tool`）→ 不成立，拒因含「用法」；人拒的调用 → 不成立；`returncode: 0` → 不成立；`returncode: null`（只校验未执行）→ 不成立；
  - 同一 id 出现两次 → 不成立；id 不存在 → 不成立；id 含 `/` → 不成立；
  - `write_episode_file` 的失败 → 不成立（工具名不在 bash / run_pipeline）。
- 模型改不了系统字段：模型在 `--symptom` 里写「退出码 0、命令是 X」→ 工单「现场」段仍是日志里的值；argv 带 `--exit 0` → argparse 报错、`preview` 不成立。
- 脱敏：在临时 `config/agent.local.json` 里放一个密钥值，让它出现在 `run_pipeline` 结果的 `stderr_tail`（该路径今天不经 `scrub_secrets`）与模型的 `--repro` 里 → 工单两处都是 `[已脱敏]`；`03-audio/manifest.json` 字样 → `[已脱敏]`。
- 去重：同命令同末行 + 待修 → 追加「复现记录」、不新建文件；同签名但已修 → 新建且首行写「可能是回归」；末行不同 → 新建；不同卷同模块同报错（期目录替换为 `<期>`）→ 追加。并发：两个进程同时 `open` 同签名 → 只有一个新文件（持锁）。
- 不进 git：静态两条（⑩）；运行时——在临时 git 仓库里不写 `.gitignore` 跑 `open` → 拒写、无文件；`git` 不在 PATH → 拒写。
- 输出：`open` 的 stdout 不含工单正文（断言模型字段与 stderr 尾部的文字都不在 stdout 里），末行是合法 `TICKET_JSON`。
- 审查层：`run_pipeline tickets open …` → `ask`、`allow_key is None`、卡面含工单全文；`preview` 不成立 → `reject` 不弹卡；`tickets list` → 免卡；会话里「本会话都批准」过别的 bash 类别后，`tickets open` 仍弹卡。
- bash 规则：`python -m pipeline.tickets open …`、`.venv/bin/python -m pipeline.tickets open …`、`python pipeline/tickets.py open …` → 含 `[须人审的流水线命令]`；`python -m pipeline.tickets list` → 不含；`python -m pipeline.corrections global …`、`python -m pipeline.calibration set …`、`python -m pipeline.bgm register …` → 含（§三 #5 的现有缺口）；`python -m pipeline.corrections check` → 不含。
- `handoff`：成功的 `tickets open` 帧带 `handoff`，文本等于模板（路径为工单绝对路径）；末行 `TICKET_JSON` 的 `path` 指向 `tickets/` 之外（伪造输出）→ 不加键；失败的 `open` → 不加键；history 帧同样带。
- 提醒：工单 `已修`、期停在 03、`阻塞: 03` → 会话启动发一条 `ticket_fixed`，文本含工单号与提交号；期已到 05 → 不发；`待修` → 不发；提交号写成 `HEAD~1` → 文本写「未填」；假模型端点收到的请求里**没有**工单号与续跑文本；一份工单缺状态行 → notice 里计「1 份解析失败」。
- 不自动拉起：静态——`pipeline/tickets.py` 的 `subprocess` 调用第一个参数只能是 `"git"`（ast 扫描）；`desktop/src` 里与工单相关的代码不调用 `spawn` / `exec`（扫描 `handoff` 相关文件）。

**vitest**

- `convFrames`：`tool` 帧 `handoff` 为数字 → malformed；缺键合法。

**e2e**（真实 core + 假模型，PR-A 只验协议，不验按钮样子）

- 假模型剧本：跑一条必然失败的 bash → 调 `run_pipeline tickets open --call <上一条的 id> …` → 卡出现在对话流、卡面含命令与退出码 → 批准 → tool 帧带 `handoff`；临时仓库根下 `tickets/` 有一份工单。

### 6.6 变异清单

| # | 改坏什么 | 由哪条测试抓 |
|---|---|---|
| M1 | 现场取自模型参数（例如接受 `--stderr`） | 「模型改不了系统字段」 |
| M2 | 接受 `synthetic_tool` 结果 | 白名单拒那条 |
| M3 | 只过 `scrub_restricted` 不过 `scrub_secrets` | `run_pipeline` 尾部的密钥 |
| M4 | 模型字段不脱敏 | `--repro` 里的密钥 |
| M5 | 报错行取首行 | 去重（构造首行不同、末行相同的两次输出） |
| M6 | 去重忽略状态（已修的也追加） | 回归那条 |
| M7 | 不持锁 | 并发两进程 |
| M8 | 运行时不查 `git check-ignore` | 临时仓库无 `.gitignore` |
| M9 | `.gitignore` 删掉 `/tickets/` | 静态测试 |
| M10 | `tickets open` 开放「本会话都批准」 | 审查层 `allow_key is None` |
| M11 | bash 规则漏掉 `pipeline/<模块>.py` 写法 | bash 规则第三条 |
| M12 | `handoff` 文本取自模块输出 | 伪造 `TICKET_JSON` 指向 `tickets/` 外 |
| M13 | 提醒注入模型消息 | 假端点请求不含工单号 |
| M14 | 提醒不看工序进度 | 期已到 05 仍提醒 |
| M15 | stdout 打印工单正文 | stdout 不含正文 |

### 6.7 验证命令

```bash
uv run pytest
cd desktop && npm run typecheck && npx vitest run
cd desktop && npx electron-vite build && npx playwright test
```

### 6.8 明确不做

- 不自动拉起 coding agent（上位需求 1.C；第二阶段「隔离分支里自动修、人来合」等工单质量跑出来再立项）。
- 不让 ava 判定「是 bug」后直接落盘：一律经人卡。
- ava 侧没有改工单状态、关闭工单的入口；状态只由 coding agent 或人改。
- 选题会话（无期目录）不能开单：`run_pipeline` 需要期目录。生产中的 bug 发生在期 / 卷 / 企划会话里。
- 不把工单原文整理进 `docs/dev/issues/`：那是 coding agent 修完后的事，进表的是整理过的条目。
- 不新增工具（ADR-0025）。
- 不处理 bash 用 `python -c "import runpy…"` 一类绕过静态规则的写法：ADR-0030「已向人说明、由人接受的后果」。

### 6.9 完成判定

- [ ] §6.3 ①–⑫ 落地
- [ ] §6.5 测试全部通过，§6.6 M1–M15 每条实测变红（贴「改坏 → 红的测试名 → 恢复 → 绿」）
- [ ] §6.7 三条命令全绿，贴输出
- [ ] `git check-ignore -v tickets/x.md` 输出贴进施工记录（指向 `.gitignore` 的 `/tickets/` 行）

---

## 七、PR-B：截图选型（UI 规则 4）

### 7.1 目标

「复制给 coding agent」按钮与「已开工单」工具行的样子，由人从 2–3 套真实窗口方案里选。

### 7.2 前置条件

无（只出附件）。

### 7.3 改动清单

附件目录 `2026-10-12-bug-ticket-spec/`，脚本 `options.shots.spec.ts`（照 D80 先例，真实窗口、真实组件、在 DOM 上改出方案；路径取参数）。场景按 §一 第 1–5 步取帧：失败工具行、开单卡（1280×800 与 1440×900 各一张，量卡头与批准按钮是否同时可见，复用 D80 附件里的 `textFullyVisible` 量法）、已开工单行 + 按钮、复制后的反馈、重开会话的提醒。

方案差别在按钮的位置：工具行行尾图标按钮（与 D80 的复制命令同位）／工具行下方一行「工单 T-… · 复制给 coding agent」／按钮放在提醒与工具行两处。截图交人，人选完写回 §二。

### 7.4–7.8

新常量：无。测试：截图脚本本身不进 CI 断言（夹具仓、假模型，绝不指向真实 `data/`）。验证命令：照 D80 附件的跑法，同目录放 `shots.config.ts`，在 `desktop/` 下 `npx electron-vite build && PYTHONDONTWRITEBYTECODE=1 npx playwright test -c ../docs/dev/plans/2026-10-12-bug-ticket-spec/shots.config.ts`（只出图，产物进 `desktop/out/d82-shots/`）。不做：不改产品代码。

### 7.9 完成判定

- [ ] 截图交付，人已选，写回 §二

---

## 八、PR-C：按人选方案施工按钮

### 8.1 目标

D82 C 的界面部分。

### 8.2 前置条件

PR-A 已合、PR-B 人已选。

### 8.3 改动清单

| # | 文件 | 锚点 | 改动 |
|---|---|---|---|
| ① | `desktop/src/shared/convFold.ts` | `ToolRow` 的字段（D80 加了 `id`、`description`） | 加 `handoff: string \| null`，实时帧与历史帧都取 |
| ② | `desktop/src/renderer/ConversationPane.tsx` | `case "tool":` | 有 `handoff` 的工具行按人选方案显示「复制给 coding agent」（复用 `CopyButton`，复制的是 `handoff` 原文） |
| ③ | `style.css` | — | 人选方案的样式；`ui.css` 冻结不动 |

### 8.4 新常量

无（按钮反馈时长沿用 `CopyButton` 现值）。

### 8.5 测试要求

- vitest：`convFold` 取到 `handoff`；历史行同样取到。
- e2e：剧本同 PR-A；批准后按钮出现，点击后剪贴板内容等于帧里的 `handoff`；重启 app 后历史里按钮仍在；没有 `handoff` 的工具行没有按钮。
- 底线断言（UI 规则 5）：1280×800 与 1440×900 下按钮与工具行其他元素两两不相交、不横向溢出。
- 变异：按钮复制的是工具行摘要而不是 `handoff`；历史行丢 `handoff`。

### 8.6 验证命令

同 §6.7。

### 8.7 明确不做

- 不做「一键发给 coding agent」（不自动拉起，§6.8）。
- 不做工单列表面板（`tickets list` 在 CLI 与对话里够用，等真实使用反馈）。

### 8.8 完成判定

- [ ] §8.3 落地，§8.5 测试与变异通过，§8.6 全绿
- [ ] 1280×800 与 1440×900 各一张实现截图给人看
- [ ] §九 第 1 条（人勾）

---

## 九、验收（UI 规则 2）

1. **【人勾】真实任务**：打包版、真实窗口尺寸，在 supercell 某卷生产中真撞到一次流水线 bug（或人指定一条已知会失败的命令复现），走完「开单 → 复制 → coding agent 修 → 重开会话看到提醒 → 续跑」全程，记下别扭之处。agent 不得代勾，不得用 mock 或截图替代。
2. **【人勾】截图选型**：PR-B 已选，写回 §二。
3. 机检：各 PR 验证命令全绿、变异全杀。

## 十、给红队的重点

| 规则 | 写在 | 由什么证明 |
|---|---|---|
| 系统采集字段模型改不了 | §4.2 | 「模型改不了系统字段」；M1、M2 |
| 沿用现有密钥脱敏，且补上 `run_pipeline` 结果没过 `scrub_secrets` 的缺口 | §三 #2、§4.3 | 脱敏三条；M3、M4 |
| 工单目录不进 git（静态 + 运行时） | §4.6 | 静态两条、运行时两条；M8、M9 |
| 一律弹卡，不开放「本会话都批准」，bash 也绕不过 | §4.4 | 审查层、bash 规则；M10、M11 |
| 交接文本不能被模块输出或模型注入 | §4.7 | 伪造 `TICKET_JSON`；M12 |
| 提醒不进模型上下文 | §4.8 | 假端点请求；M13 |
| 第一阶段不自动拉起 coding agent | §4.7、§6.8 | 两条静态扫描 |
| **行为变化**：`corrections global` / `calibration set` / `bgm register` 经 bash 跑，今天不弹卡，PR-A 后弹卡 | §三 #5、§4.4 | bash 规则三条。这是在堵现有缺口（ADR-0030 决策 2 的本意），但会让过去能「顺手 bash 跑一下」的写 `config/` 操作多一张卡，**合并前请人确认** |
| 残余：静态规则可被 `python -c` 之类绕过；bash 能直接改 `tickets/` 下的文件 | §6.8 | 不测，ADR-0030 已接受的同类风险 |

## 十一、总完成判定

- [ ] PR-A 完成（§6.9）
- [ ] PR-B 完成（§7.9）
- [ ] PR-C 完成（§8.8），**待人验收**
- [ ] §九 第 1、2 条人已勾
- [ ] §十「行为变化」一行人已确认
