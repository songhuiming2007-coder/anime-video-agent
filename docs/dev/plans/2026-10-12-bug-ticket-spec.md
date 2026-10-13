# D82：生产中撞 bug → ava 开工单 → coding agent 修 施工 spec

> 对应 issue：**D82**（`docs/dev/issues/README.md`）。上位需求：[`2026-10-12-bug-ticket-direction.md`](2026-10-12-bug-ticket-direction.md)（A–D 四节）。本 spec 以代码现状为准，与上位需求不符之处见 §三。
> 相关：ADR-0025（工具表封顶 15）、ADR-0029 / ADR-0030（bash；审批只拦硬危险）、D51 / D60（`command_preview` 卡面先例）、D52（工具结果脱敏）、D53（argv 预检）、D80（UI 先例）、D81（姊妹 spec [`2026-10-12-project-layer-spec.md`](2026-10-12-project-layer-spec.md)）。
> **PR-B / PR-C 含 UI，plans/README 的 UI 五条规则对它们生效**：场景见 §一，「复制给 coding agent」按钮的样子由人从截图里选（PR-B），验收第 1 条是人在打包版里做一件真实工作（§九）。
> **仓库是公开的**（GitHub `songhuiming2007-coder/anime-video-agent`）：工单原文含输出、本机路径、期名，**永远不进 commit**。
> **红队已审（2026-10-12）**：发现与处置见 §十二；P1 全部吸收或经人裁决（新增裁决 Q3、Q4；`run_pipeline` 方案经审接受）。

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
| Q2（上位 §3-2） | 「复制给 coding agent」按钮 | 「C 工具行+提示条 (Recommended)」（2026-10-13，PR-B 三套方案真实窗口截图，AskUserQuestion 选项原文） | 工具行行尾文字按钮「复制给 coding agent」+ 输入框上方一条提示条「本会话开了工单 T-…（待修）· 复制给 coding agent」；工具行主文本「已开工单 T-…」。提示条在回合折叠之外（D80 回合跑完默认折叠，工单那条工具行随之收起），不改折叠规则。截图脚本 [`2026-10-12-bug-ticket-spec/options.shots.spec.ts`](2026-10-12-bug-ticket-spec/options.shots.spec.ts) |
| Q5（PR-B 实测引出，2026-10-13） | 开单卡的卡面：PR-A 的卡面是整份工单（约 53 行、卡高 1167 px），1280×800 与 1440×900 下都看不到「批准」按钮，不满足 §一 第 2 步 | 「K1 摘要在前、全文收起 (Recommended)」 | 卡面先列 新开 / 追加、命令、退出码、报错末行、「为什么不是用法错误」，工单全文（含通用的执行审批头与完整 argv）收进「工单全文」折叠。实现见 §8.3 ④–⑥（施工时补） |
| — | 开单走新工具还是 `run_pipeline` 模块 | **本 spec 的设计选择**（不是人裁决）；红队 2026-10-12 审过，**接受**，不立 ADR | 走 `run_pipeline tickets`，理由见 §三 #1。红队结论见 §十二 R-1：安全性质（弹卡、不开放「本会话都批准」、bash 绕不过、现场取自日志）在模块方案里都能做到且有测试；新工具多出的只有参数结构化，换来的是一份 ADR 与工具表上调 |
| Q3（§十「行为变化」，2026-10-12 审 spec 时） | bash 直接跑 `corrections global` / `calibration set` / `bgm register` 今天不弹卡，PR-A 后弹卡；另：bash 能直接写 `tickets/`（伪造「已修」工单或绕卡落单） | 「堵口 + 写 tickets/ 也弹卡 (推荐)」 | 行为变化**人已确认**；bash 对 `tickets/` 的写形态另加一条硬危险 `[写工单目录]`（§4.4） |
| Q4（红队 R-7 引出，2026-10-12） | bash 能免卡直接跑 coding agent CLI（本机已装 `claude`、`pi`），`claude -p "处理工单 …"` 就能改 `pipeline/`，绕过「第一阶段不自动拉起」与 Code Freeze | 「加一条硬危险 (推荐)」 | bash 命令里以命令词出现 `claude` / `codex` / `opencode` / `pi` / `aider` / `gemini` / `cursor-agent` → 标 `[拉起 coding agent]` 并弹卡（§4.4）；`python -c` 一类写法照旧绕得过，ADR-0030 已接受的同类 |

## 三、现状核对（2026-10-12）

| # | 上位需求的说法 / 前提 | 代码现状（引原文） | 结论与对设计的影响 |
|---|---|---|---|
| 1 | 「开单工具（新工具，一律弹卡）」 | ADR-0025 修订注：「第 15 个工具 `bash` … 封顶上调至 15；第 16 个须再立 ADR」；`config/agent/tools.json` 现 15 个 | 新工具要先立 ADR。本 spec 改走 `run_pipeline` 的新模块 `pipeline.tickets`：卡面用已有的 `command_preview`（D51 `corrections.preview`、D60 `calibration.preview` 先例）显示工单全文；「一律弹卡」用 `CONFIG_WRITE_COMMANDS` 同款的逐条人审集合实现。代价：模型填的长文本走 argv（`shlex` 能处理引号与换行，`run_pipeline` 已 `shlex.split`） |
| 2 | 「输出尾部（沿用现有密钥脱敏）」 | `tools.py`：`scrub_secrets` 只用在 bash——`"stdout_tail": scrub_secrets(done.stdout_tail, ctx.root),`；`run_pipeline` 返回 `"stdout_tail": executed_job.stdout_tail,`，**没过 `scrub_secrets`**；工具结果进会话前统一过 `scrub_restricted`（`_tool_message`） | 工单的系统采集段对**所有**来源的文本再过一遍 `scrub_secrets` + `scrub_restricted`（两者对干净文本幂等）；模型填写的字段同样过 |
| 3 | 现场怎么拿 | 会话日志每条记录带 `origin`（`_record`：`record = {**record, "origin": origin}`）；真实执行的工具结果是 `control.commit(_tool_message(call, outcome), "tool")`，被拒 / 预检拒 / 判重是 `"synthetic_tool"`；结果内容是 `json.dumps(outcome)` 经 `scrub_restricted` | 系统按模型给的 `tool_call_id` 从**会话日志**取现场（不从模型参数取）：只认 `origin == "tool"` 的结果，即真跑过的命令。日志里的形状（红队 R-11 读真实 `session.jsonl` 核实）：`{"k": "msg", "message": {"role": "tool", "tool_call_id": …, "content": "<JSON 串>"}, "origin": "tool", …}`，`content` 解开是 `execute_tool` 的外壳 `{"ok": true, "result": {…}}`，`returncode` / `stdout_tail` / `stderr_tail` / `argv` 在 `result` 里；实现层抛异常时是 `{"ok": false, "error": …}`，没有 `returncode`。压缩事件（`k: "compaction"`）的 `tail` 里有同一批消息的副本。预检拒、人拒的调用不能开单——那是用法问题，不是代码 bug（D53 即一例）。从日志取而不是从内存消息取：压缩、D65 工具结果老化都不影响日志原文 |
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

- `<期目录>` 由 `run_pipeline` 自动补位（会话绑定的期 / 卷目录；D81 落地后企划会话补企划目录，D81 §4.2 已把 `tickets` 列进企划会话的放行集合）。补位照 `corrections` 的写法插在子命令词之后，只对 `open` 与不带位置参数的 `list` 补；`show <工单号>` 不补（补了会多出一个位置参数被 D53 预检拒）。
- **模型不能指定别的期**——这一条现有补位机制给不了（红队 R-3）：`validate_pipeline_command` 只在没有位置参数时补位，模型写 `tickets open data/episodes/<别的期> --call …` 会原样放行，工单的「期」就成了模型填的值。所以 `tickets.preview` 加一条规则：`open` 的位置参数 `resolve()` 后必须等于宿主绑定的会话目录（审查层传入，不取模型参数），否则不成立、弹卡前拒。
- `--blocked` 取值固定：`01 02 02.5 02.8 03 03.5 04 05 06 07 08 09 总装`（WORKFLOW 九步加人工环节编号，再加 D81 的总装；`总装` 只在企划会话里有效，卷 / 期会话给 `总装` → 不成立）。
- 模型**只能**给上面五个文本字段与 `--call`、`--blocked`。系统采集字段没有对应参数：给 `--exit` 之类 → argparse 报错，审查层在弹卡前拒（`preview` 不成立）。
- argparse 用 `allow_abbrev=False`。

### 4.2 系统采集（模型改不了）

| 字段 | 来源 |
|---|---|
| 命令 | bash：会话日志里 `tool_call_id` 对应的 assistant `tool_calls` 参数 `command`；`run_pipeline`：工具结果里的 `result.argv`（**真正执行的那条**，含当时的自动补位；原稿写「取 `command` 再规范化」，事后重算会因白名单或补位规则变化而与实际执行的不同，红队 R-11） |
| 退出码、输出尾部 | 同一 `tool_call_id` 的工具结果记录（`origin == "tool"`）里 `content` 解开后的 `result.returncode`、`result.stdout_tail`、`result.stderr_tail`、`result.message`（形状见 §三 #3） |
| 代码版本 | `git -C <仓库根> rev-parse HEAD`；`git -C <仓库根> status --porcelain=v1 --untracked-files=no` → 是否有未提交改动 + 前 `TICKET_DIRTY_LIST_MAX` 个路径 |
| 期、会话、时间 | 期键（相对 `data/episodes/`，如 `supercell-编年史/01-xxx`）、会话 sid（日志里该调用所在段）、UTC 时间 |

取现场的规则（不满足任何一条 → `preview` 返回不成立，审查层弹卡前拒，拒因说明是哪条）：

1. `tool_call_id` 形状：`[A-Za-z0-9_.:-]{1,128}`。
2. 在该期 `session.jsonl` 里**恰好**有一个 assistant 记录含这个 id 的调用，且**恰好**有一个对应的工具结果记录。**只数 `k == "msg"` 的记录**（与 `session_log._assistant_index` 同口径）：压缩事件的 `tail` 是副本，算进去的话压缩过的会话里任何调用都「出现两次」，再也开不了单（红队 R-12）。
3. 工具名是 `bash` 或 `run_pipeline`。
4. 结果记录 `origin == "tool"`（真跑过），且 `result.returncode` 是非 0 整数（外壳 `ok: false` 的实现层异常没有 `returncode`，不成立）。
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
（去重命中时追加：时间 / 会话 / 代码版本 / 期 / 命令 / 退出码 / stderr 与 stdout 尾部——与「现场」同一组字段，§4.5）

## 处理结果（coding agent 填）
状态: 已修 | 不是bug | 不修
提交:
续跑:
```

- 状态取值：`待修`（开单时）、`已修`、`不是bug`、`不修`。只有 coding agent（或人）改状态；ava 侧没有任何写工单状态的入口。
- 所有文本（系统采集与模型填写）写入前过 `scrub_secrets` + `scrub_restricted`。**去重键在脱敏之后算**（红队 R-13）：它原样写进头部 `去重键:` 行与卡面，用脱敏前的命令与报错行算，密钥就从这一行漏出去；脱敏对同一份输入是确定的，不影响去重。`tickets open` 打到 stdout 的「一行摘要」同样只用脱敏后的文本。
- 写入：持 `tickets/.lock`（`fcntl`，照 `session_titles.py` 先例）完成「查重 → 写新单或追加」；落盘用 `paths.atomic_write`。`tickets/` 不存在时由 `open` 建（只建这一层，且在 `git check-ignore` 通过之后）。新单的目标文件已存在（同一秒同签名，例如已修单回归后立刻又开）→ 不覆盖，工单号末尾加 `-2`、`-3` 直到不冲突（红队 R-14）。

### 4.4 一律弹卡，且 bash 绕不过

- 新增集合 `HUMAN_GATED_COMMANDS`（定义在 `tools.py`：`status_card` 已从 `tools` 导入 `scrub_restricted`，放这里两边都能引用而不破叶子性；`session.py` 的 `CONFIG_WRITE_COMMANDS` 改为它的子集）：`CONFIG_WRITE_COMMANDS` 的三条 + `("tickets", "open")`（D81 PR-5 再加 `("assemble", None)`）。`run_pipeline` 命中 → 逐条弹卡、不开放「本会话都批准」，卡面 `command_preview = tickets.preview(argv)`（工单全文、是否追加到已有单）。`tickets list` / `show` 进 `READONLY_PIPELINE_MODULES` 的同口径免卡（只读子命令，入选前核实无写调用）。
- `status_card.bash_danger_tags` 加一条规则：命令里出现 `pipeline.<模块>` 或 `pipeline/<模块>.py`，且**在它之后的任意位置**（同一条命令里，不要求紧挨着）出现 `HUMAN_GATED_COMMANDS` 里该模块的子命令词（`None` 表示任何调用）→ 标 `[须人审的流水线命令]`。不要求紧挨着（红队 R-6）：argparse 接受 `python -m pipeline.corrections -- global …`，模块名带引号（`-m 'pipeline.corrections'`）也照样能跑，「紧跟子命令」的正则两种写法都抓不到；多弹一张卡的代价远小于漏一张。这条规则从 `HUMAN_GATED_COMMANDS` 生成，不另写一份清单。
- 再加两条（人裁决 Q3、Q4，2026-10-12）：
  - `[写工单目录]`：命令里出现 `tickets/` 路径（`_FREEZE_PATH` 同款写法：`(^|[\s'"/=])tickets/`）且命中 `_WRITE_FORM` 的写形态（重定向、`tee`、`sed -i`、`cp` / `mv` / `ln` / `touch` 等）→ 弹卡。只读（`cat`、`ls`、`grep`）不拦。堵的是「bash 伪造一张『已修』工单让 ava 推送续跑提醒」与「bash 绕过人卡直接落一张单」。
  - `[拉起 coding agent]`：命令词（行首或 `;` `&` `|` `(` 之后，允许前面有 `env X=1`、`nohup`、`exec`）是 `claude`、`codex`、`opencode`、`pi`、`aider`、`gemini`、`cursor-agent` 之一 → 弹卡。名单是静态的，写在 `status_card.py` 常量里并注明来历；`claudette`、`pip`、`./pi-tools` 这类只是前缀或路径相同的不算。
- 这三条都是 ADR-0030 决策 1 的危险清单的扩充：PR-A 同时给 ADR-0030 加修订注，三条一起写。

### 4.5 去重

- 去重键 = `规范化命令 ⟂ 报错行`：
  - 规范化命令：`run_pipeline` 取规范化 argv 中模块名之后的部分，自动补位的期目录替换为 `<期>`（同一个 bug 在不同卷上复现能合并）；bash 取 `command` 折叠空白。
  - 报错行：`stderr_tail` 的最后一个非空行；为空则取 `stdout_tail` 的；再为空取 `error` / `message`。逐字比较，不做模糊匹配。
- 命中**状态为待修**的工单 → 追加一条「复现记录」，不新开；卡面写明「将追加到 T-…」，并把**两次的报错尾部并排**放上卡面，人一眼看得出是不是同一个 bug。
- **每条复现记录带完整现场**（命令、退出码、stderr / stdout 尾部，同样脱敏），不只是「时间 / 会话 / 代码版本 / 期」（红队 R-2）：末行相同的两个不同 bug（例如末行都是一句汇总式的 `FAIL …`）会被合进一张单，原稿的复现记录不带输出，第二个 bug 的现场就丢了。带上之后误合并只多一段文字，不丢证据。
- 命中**已修**的工单 → 新开，并在新单首行写「与已修工单 T-… 同签名，可能是回归」。
- 命中**不是bug** / **不修**的工单 → 新开，首行写「与 T-…（结论：不是bug / 不修）同签名」，让人与 coding agent 先看上次的结论（原稿没定义这两种状态，红队 R-14）。
- 不同报错行 → 新开。

### 4.6 不进 git

- `.gitignore` 加 `/tickets/`。
- **静态测试**（pytest，跑在仓库里）：`git check-ignore -q tickets/T-x.md` 退出码 0；`git ls-files tickets` 输出为空。
- **运行时**：`tickets open` 写盘前对目标路径跑 `git -C <仓库根> check-ignore -q <路径>`（argv），非 0 或 git 不可用 → 拒写（「tickets/ 没被 gitignore 覆盖，拒绝写入（公开仓库）」）。
- 工单内容不出网：`tickets open` 只向 stdout 打印工单号、路径与一行摘要，以及最后一行机读 `TICKET_JSON {…}`（§4.7），不打印工单正文，所以工具结果里不会出现工单原文。
- **`list` / `show` 同样不打印正文**（红队 R-4）：原稿把它们当只读免卡，却没说打印什么——打印全文就把「处理结果」段（coding agent 写的）送进模型上下文，与 §三 #6「不该原样注入生产 agent」、与上一条「工单内容不出网」相反。两者只打印系统解析出的字段：工单号、`状态`、`期`、`阻塞`、`去重键`、提交号（过 `[0-9a-f]{7,40}`，否则「未填」）、`续跑:` 行（规则同 §4.8）、复现次数。要看全文的是人与 coding agent，直接打开文件。

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
- 只在本期**还没走过**被阻塞的工序时提醒：`inspect_episode(期).current_step` 的工序键（`assembly.STEP_KEY_MAP` 同一套）在 `阻塞:` 之前或相同 → 提醒；已经过了 → 不提醒。先后按 §4.1 `--blocked` 取值表的顺序比。注意 `inspect_episode` 把 03.5 与 04 合成一步（键 `03.5`），也从不停在 `02.8`：`阻塞: 04` 在当前键 `03.5` 时提醒、`阻塞: 02.8` 在当前键已到 `03` 时不提醒——这两条写进测试（红队 R-15）。不另存「已提醒」状态（状态即产物）。
- `阻塞: 总装`（企划会话里开的单，`期:` 是企划键）：当前步骤不适用。改判产物——卷表 `合集:` 指向的成员里 `04-assembly.json` 不存在、或它的 mtime 早于工单文件的 mtime（处理结果写回后工单才变新）→ 提醒；之后重新总装过 → 不提醒（红队 R-5，与 D81 §4.2 放行 `tickets` 同步）。
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
| ③ | `pipeline/agent/tools.py` | `PIPELINE_MODULES: set[str] = {`；`PIPELINE_VALUED_FLAGS: frozenset[str] = frozenset({`；`_AUTOFILL_MODULES`；`_SUBCOMMAND_WORDS` | 加模块 `tickets`；带值旗标加 `--call`、`--blocked`、`--symptom`、`--expected`、`--repro`、`--why`（D53 的 ast 守卫测试会扫到）；`tickets` 进 `_AUTOFILL_MODULES` 与子命令词（`open`、`list`、`show`）；补位要在 `validate_pipeline_command` 里照 `corrections` 那一支另写一支（那里是逐模块的 `if/elif`，进元组不会自动生效），只给 `open` 与无位置参数的 `list` 补（§4.1）；`tickets list` / `show` 的只读放行照 `_readonly_asset_subcommand` 的口径（`READONLY_ASSET_SUBCOMMANDS` 加 `"tickets": {"list", "show"}`），入选前核实两者无写调用 |
| ④ | `pipeline/agent/session.py` | `CONFIG_WRITE_COMMANDS = frozenset({("corrections", "global"), ("calibration", "set"), ("bgm", "register")})` 与 `review_tool_call` 里 `elif (module, rest[0] if rest else None) in CONFIG_WRITE_COMMANDS:` | `review_tool_call` 改用 `tools.HUMAN_GATED_COMMANDS`（§4.4）；`tickets open` 先跑 `tickets.preview`，不成立 → `reject`（拒因即 preview 的原因），成立 → `ask`、`command_preview` = 工单全文、无 `allow_key` |
| ⑤ | `pipeline/agent/status_card.py` | `def bash_danger_tags(command: str) -> list[str]:`、`_BASH_DANGER_RULES` | 加由 `tools.HUMAN_GATED_COMMANDS` 生成的规则，标 `[须人审的流水线命令]`；另加 `[写工单目录]`、`[拉起 coding agent]` 两条（§4.4，人裁决 Q3、Q4）。`status_card` 不 import `session`（叶子性），只引 `tools` |
| ⑥ | `pipeline/agent/session.py`、`protocol.py` | `frame["command"] = self._tool_commands.get(index, "")`；`frame["command"] = command if isinstance(command, str) else ""` | `tickets open` 成功时加 `handoff`（§4.7）；实时帧与 history 帧两处 |
| ⑦ | `desktop/src/shared/convFrames.ts` | `export const CONDITIONAL: Partial<Record<OutType, …>> = {` | `tool`（与 `history` 的工具行）登记 `handoff: ["string"]`（有键才查）；TX-0 用真实 core 的 `tickets open` 帧比对 |
| ⑧ | `pipeline/agent/protocol.py` | 发 `ready` 之后、首个回合之前 | 调 `tickets.find_fixed` → 发 `notice{code:"ticket_fixed"}`（§4.8）。不进 `messages`、不落进模型请求 |
| ⑨ | `.gitignore` | `# 桌面端（Spec 8）：依赖与构建产物不进库` 那段之后 | 加 `/tickets/` 与一行注释（公开仓库，工单含输出原文与本机路径） |
| ⑩ | `tests/test_tickets_static.py`（新） | — | §4.6 静态两条 |
| ⑪ | `config/agent/scopes/creative.md`、`docs/runbook/`（WORKFLOW 停机规程旁） | — | 什么时候开单：确信是引擎缺陷（AGENTS.md Code Freeze 的「三项汇报」）时，用 `tickets open` 代替在对话里复述；先排查当期输入；`--why` 必填且要具体 |
| ⑫ | `docs/dev/adr/0030-approval-danger-only.md` | 「## 取代与修订」 | 加修订注：危险清单扩入三条——「须人审的流水线命令」（含 `corrections global` / `calibration set` / `bgm register` 经 bash 的现有缺口，人 2026-10-12 确认）、「写工单目录」、「拉起 coding agent」——日期与本 spec 链接 |

### 6.4 新常量与取值理由

| 常量 | 值 | 理由 |
|---|---|---|
| `TICKETS_DIR` | `tickets`（仓库根下） | 人裁决 Q1 |
| `TICKET_DIRTY_LIST_MAX` | 20 | 只用来让 coding agent 知道「现场有哪些未提交改动」；一次生产里本不该有未提交的引擎改动（Code Freeze），超过 20 个本身就说明现场不干净，写「另有 N 个」即可 |
| `TICKET_FIELD_MAX_CHARS` | 4000 | 单个模型字段的上限。`--why` 要求具体，但 4000 字已超过一个 `run_pipeline` 作业尾部（4096 字节）的量级；更长多半是把输出原文又贴了一遍，那部分系统已采集 |
| `TICKET_WHY_MIN_CHARS` | 20 | 只拦「因为是 bug」这类空话（D53 那次模型把参数写错当成 bug，就是没说清依据）；20 字够写一句「同一参数在 X 期能跑、这里报 Y，且输入格式已核对」。判断真伪的是人，不是这个数 |
| `CODING_AGENT_COMMANDS` | `claude`、`codex`、`opencode`、`pi`、`aider`、`gemini`、`cursor-agent` | 人裁决 Q4。名单 = 本机已装（`claude`、`pi`，2026-10-12 `command -v` 实测）+ 常见 coding agent CLI 的命令名；静态名单绕得过，ADR-0030 已接受这一类 |
| `TICKET_RESUME_MAX_CHARS` | 200 | 提醒是一行 notice；续跑命令通常是一条 `run_pipeline` 命令，200 字足够 |
| `--blocked` 取值表 | 12 个工序号 + `总装` | 与 WORKFLOW 九步和人工环节编号一一对应，`assembly.STEP_KEY_MAP` 的值域加 `02.8`、`04`；`总装` 是 D81 企划会话的工序（红队 R-5） |

### 6.5 测试要求

期望值先在实现上跑一遍再写断言；每条安全规则配一条变异（§6.6）。

**pytest**（`tests/test_tickets.py` 新）

- 取现场：伪造 `session.jsonl`——
  - bash 真跑失败（`origin: tool`、`returncode: 1`）→ 成立，命令、退出码、尾部逐字来自日志；
  - `run_pipeline` 被白名单拒（`synthetic_tool`）→ 不成立，拒因含「用法」；人拒的调用 → 不成立；`returncode: 0` → 不成立；`returncode: null`（只校验未执行）→ 不成立；
  - 同一 id 出现两次 → 不成立；id 不存在 → 不成立；id 含 `/` → 不成立；
  - `write_episode_file` 的失败 → 不成立（工具名不在 bash / run_pipeline）；
  - 会话做过一次压缩、失败调用同时出现在 `msg` 记录与压缩事件的 `tail` 里 → **成立**（只数 `msg`，红队 R-12）；
  - `run_pipeline` 的命令取自 `result.argv`：伪造日志里 assistant 参数 `command` 与 `result.argv` 不同 → 工单写的是 `result.argv`。
- 模型改不了系统字段：模型在 `--symptom` 里写「退出码 0、命令是 X」→ 工单「现场」段仍是日志里的值；argv 带 `--exit 0` → argparse 报错、`preview` 不成立；`tickets open data/episodes/<别的期> --call …`（显式给另一个期目录）→ `preview` 不成立（红队 R-3）。
- 脱敏：在临时 `config/agent.local.json` 里放一个密钥值，让它出现在 `run_pipeline` 结果的 `stderr_tail`（该路径今天不经 `scrub_secrets`）与模型的 `--repro` 里 → 工单两处都是 `[已脱敏]`；密钥出现在报错**末行** → 头部 `去重键:` 行、卡面与 stdout 摘要里也是 `[已脱敏]`（红队 R-13）；`03-audio/manifest.json` 字样 → `[已脱敏]`。
- 去重：同命令同末行 + 待修 → 追加「复现记录」、不新建文件；同签名但已修 → 新建且首行写「可能是回归」；末行不同 → 新建；不同卷同模块同报错（期目录替换为 `<期>`）→ 追加。追加的复现记录含第二次的 stderr 尾部原文（红队 R-2）；同签名、原单状态 `不是bug` → 新建且首行写明上次结论；同一秒内同签名再开（模拟已修单回归）→ 第二个工单号带 `-2`、第一个文件字节不变（R-14）。并发：两个进程同时 `open` 同签名 → 只有一个新文件（持锁）。
- 不进 git：静态两条（⑩）；运行时——在临时 git 仓库里不写 `.gitignore` 跑 `open` → 拒写、无文件；`git` 不在 PATH → 拒写。
- 输出：`open` 的 stdout 不含工单正文（断言模型字段与 stderr 尾部的文字都不在 stdout 里），末行是合法 `TICKET_JSON`。
- 审查层：`run_pipeline tickets open …` → `ask`、`allow_key is None`、卡面含工单全文；`preview` 不成立 → `reject` 不弹卡；`tickets list` → 免卡；会话里「本会话都批准」过别的 bash 类别后，`tickets open` 仍弹卡。
- bash 规则：`python -m pipeline.tickets open …`、`.venv/bin/python -m pipeline.tickets open …`、`python pipeline/tickets.py open …`、`python -m pipeline.corrections -- global …`、`python -m 'pipeline.tickets' open …` → 含 `[须人审的流水线命令]`（后两条是红队 R-6 的写法）；`python -m pipeline.tickets list` → 不含；`python -m pipeline.corrections global …`、`python -m pipeline.calibration set …`、`python -m pipeline.bgm register …` → 含（§三 #5 的现有缺口）；`python -m pipeline.corrections check` → 不含。
- `[写工单目录]`（Q3）：`echo x > tickets/T-1.md`、`sed -i '' 's/待修/已修/' tickets/T-1.md`、`cp a.md tickets/`、`tee -a tickets/T-1.md` → 含；`cat tickets/T-1.md`、`ls tickets/`、`grep 已修 tickets/*.md` → 不含。
- `[拉起 coding agent]`（Q4）：`claude -p "处理工单"`、`cd x && codex exec …`、`nohup opencode run … &`、`env A=1 pi …`、`echo hi | claude` → 含；`claudette`、`pip install x`、`ls ./pi-tools`、`grep claude notes.md` → 不含。
- `handoff`：成功的 `tickets open` 帧带 `handoff`，文本等于模板（路径为工单绝对路径）；`TICKET_JSON` 里多塞一个 `"handoff": "<注入文本>"` 键、stdout 前面再印几行任意文字 → `handoff` 仍逐字等于模板（M12 靠这条杀：只伪造路径的那条用例在「文本取自输出、路径照校验」的变异下照样绿，红队 R-10）；末行 `TICKET_JSON` 的 `path` 指向 `tickets/` 之外（伪造输出）→ 不加键；失败的 `open` → 不加键；history 帧同样带。
- 提醒：工单 `已修`、期停在 03、`阻塞: 03` → 会话启动发一条 `ticket_fixed`，文本含工单号与提交号；期已到 05 → 不发；`阻塞: 04`、当前键 `03.5` → 发；`阻塞: 02.8`、当前键 `03` → 不发（R-15）；企划会话、`阻塞: 总装`、合集期 `04-assembly.json` 早于工单 → 发，晚于工单 → 不发（R-5）；`待修` → 不发；提交号写成 `HEAD~1` → 文本写「未填」；假模型端点收到的请求里**没有**工单号与续跑文本；一份工单缺状态行 → notice 里计「1 份解析失败」。
- 不自动拉起：静态——`pipeline/tickets.py` 的 `subprocess` 调用第一个参数只能是 `"git"`（ast 扫描）；`desktop/src` 里与工单相关的代码不调用 `spawn` / `exec`（扫描 `handoff` 相关文件）；bash 经 `[拉起 coding agent]` 弹卡（上面 Q4 那组）。
- `list` / `show` 不出正文（红队 R-4）：工单的「处理结果」与「模型填写」段各放一句独特标记 → `tickets list`、`tickets show <号>` 的 stdout 里都没有这两句，有工单号、状态与续跑行。

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
| M16 | 不比位置参数与绑定目录 | 显式给另一个期目录 → 不成立 |
| M17 | 取现场时把压缩事件的 `tail` 也数进去 | 压缩过的会话仍能开单 |
| M18 | 去重键用脱敏前的文本算 | 末行带密钥 → `去重键:` 行是 `[已脱敏]` |
| M19 | 复现记录不带输出 | 追加的复现记录含第二次 stderr 原文 |
| M20 | 新单目标已存在时覆盖 | 同秒同签名 → `-2`、原文件字节不变 |
| M21 | bash 规则要求子命令紧跟模块名 | `-- global`、带引号的模块名 |
| M22 | 去掉 `[写工单目录]` | `sed -i … tickets/T-1.md` |
| M23 | 去掉 `[拉起 coding agent]`，或按子串匹配 | `claude -p …` 含；`claudette` / `pip` 不含 |
| M24 | `show` 打印全文 | `list` / `show` 不出正文 |
| M25 | 提醒的先后比较不认 `03.5` 合并步 | `阻塞: 04`、当前 `03.5` → 发 |

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

- [x] §6.3 ①–⑫ 落地（2026-10-13，见 §十三）
- [x] §6.5 测试全部通过，§6.6 M1–M25 每条实测变红（贴「改坏 → 红的测试名 → 恢复 → 绿」）
- [x] §6.7 三条命令全绿，贴输出
- [x] `git check-ignore -v tickets/x.md` 输出贴进施工记录（指向 `.gitignore` 的 `/tickets/` 行）

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

- [x] 截图交付，人已选，写回 §二（2026-10-13：按钮 C、卡面 K1）

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
| ② | `desktop/src/renderer/ConversationPane.tsx` | `function ToolLine({ row, turnRunning, fresh, opens, inGroup }`（原稿写 `case "tool":`，那一处在 `shared/convFold.ts` 的帧折叠里，渲染端是 `if (row.k === "tool") return <ToolLine …`，红队 R-16） | 有 `handoff` 的工具行按人选方案显示「复制给 coding agent」（复用 `CopyButton`，复制的是 `handoff` 原文）。按钮是原生 `<button>`（静态门禁 VS-8）；样式只进 `style.css`，不加 token（门禁 1、VS-1） |
| ③ | `style.css` | — | 人选方案的样式；`ui.css` 冻结不动 |
| ④ | `pipeline/tickets.py`、`pipeline/agent/session.py`、`protocol.py` | `HumanRequest`、`_tool_request`、请求帧 `"card_text": request.card_text,` | **人选 K1 后补（Q5）**：`tickets.preview_summary` 给开单卡卡面摘要（新开 / 追加、命令、退出码、报错末行、为什么不是用法错误，同一套脱敏）；`HumanRequest.card_summary`（默认 None）；请求帧条件键 `card_summary`（没有摘要的卡不带）。`card_text` 不变（终端通道照旧显示全文） |
| ⑤ | `desktop/src/shared/convFrames.ts`、`HumanCards.tsx` | `CONDITIONAL`、`<pre className="request-card-text"` | `request` 登记 `card_summary: ["string"]`（有键才查）；有摘要的卡卡面显示摘要，`card_text` 收进「工单全文（N 行）」折叠 |
| ⑥ | `ConversationPane.tsx`、`App.tsx`、`shared/toolView.ts` | `<AnswerDock …/>` 与 `<Composer` 之间 | **人选 C**：`HandoffStrip`（输入框上方，本会话每张工单一条「本会话开了工单 T-…（待修）· 复制给 coding agent」）；`toolView.ticketIdOf` / `handoffTickets`；带 `handoff` 的工具行主文本「已开工单 T-…」 |

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

- [x] §8.3 落地，§8.5 测试与变异通过，§8.6 全绿（2026-10-13，见 §十三）
- [x] 1280×800 与 1440×900 各一张实现截图给人看（`desktop/out/d82-shots/pr-c/`）
- [ ] §九 第 1 条（人勾）

---

## 九、验收（UI 规则 2）

1. **【人勾】真实任务**：打包版、真实窗口尺寸，在 supercell 某卷生产中真撞到一次流水线 bug（或人指定一条已知会失败的命令复现），走完「开单 → 复制 → coding agent 修 → 重开会话看到提醒 → 续跑」全程，记下别扭之处。agent 不得代勾，不得用 mock 或截图替代。
2. **【人勾】截图选型**：PR-B 已选，写回 §二。
3. 机检：各 PR 验证命令全绿、变异全杀。

## 十、给红队的重点

| 规则 | 写在 | 由什么证明 |
|---|---|---|
| 系统采集字段模型经 `tickets` 参数改不了（含「期」） | §4.1、§4.2 | 「模型改不了系统字段」、显式给别的期、压缩后取现场；M1、M2、M16、M17 |
| 沿用现有密钥脱敏，且补上 `run_pipeline` 结果没过 `scrub_secrets` 的缺口 | §三 #2、§4.3 | 脱敏三条；M3、M4 |
| 工单目录不进 git（静态 + 运行时） | §4.6 | 静态两条、运行时两条；M8、M9 |
| 一律弹卡，不开放「本会话都批准」，bash 也绕不过 | §4.4 | 审查层、bash 规则（含 `--` 与带引号写法）；M10、M11、M21 |
| bash 写 `tickets/` 弹卡（人裁决 Q3） | §4.4 | `[写工单目录]` 一组；M22 |
| bash 拉起 coding agent 弹卡（人裁决 Q4） | §4.4 | `[拉起 coding agent]` 一组；M23 |
| 工单内容不经 `list` / `show` 进模型上下文 | §4.6 | `list` / `show` 不出正文；M24 |
| 误合并不丢现场；去重键不漏密钥 | §4.3、§4.5 | 复现记录含输出、去重键脱敏；M18、M19 |
| 交接文本不能被模块输出或模型注入 | §4.7 | 伪造 `TICKET_JSON`；M12 |
| 提醒不进模型上下文 | §4.8 | 假端点请求；M13 |
| 第一阶段不自动拉起 coding agent | §4.7、§6.8 | 两条静态扫描 |
| **行为变化**：`corrections global` / `calibration set` / `bgm register` 经 bash 跑，今天不弹卡，PR-A 后弹卡 | §三 #5、§4.4 | bash 规则三条。这是在堵现有缺口（ADR-0030 决策 2 的本意），会让过去能「顺手 bash 跑一下」的写 `config/` 操作多一张卡。**人已确认（2026-10-12，§二 Q3）** |
| 残余：静态规则可被 `python -c` 之类绕过（含经 `python -c` 写 `tickets/`、拉起 coding agent）；bash 能往会话日志 `session.jsonl` 追加一条伪造的 `origin: "tool"` 失败记录，再拿它的 id 开单——「现场」就成了 bash 写的内容（日志的 `flock` 是建议锁，挡不住 `>>`），所以「系统采集」的准确含义是「经 `tickets` 参数改不了」，不是「无论如何改不了」；人在卡上看到的仍是全文 | §6.8 | 不测，ADR-0030 已接受的同类风险（红队 R-8 补写） |

## 十一、总完成判定

- [x] PR-A 完成（§6.9）
- [x] PR-B 完成（§7.9）
- [x] PR-C 完成（§8.8），**待人验收**（机检通过；§九 第 1 条待人勾）
- [ ] §九 第 1、2 条人已勾
- [x] §十「行为变化」一行人已确认（2026-10-12，§二 Q3）

---

## 十二、红队记录（2026-10-12，零上下文审查）

审查对象：提交 `73fd6a2` 的本 spec。逐条回到代码核对；会话日志的形状用真实 `session.jsonl` 只读核实（211 个 `tool_call_id` 无重复，形如 `acquire_propose-1790472635275161000-53`，本地生成）。严重度口径同 D81 spec §十四。**收口：P0 无；P1 全部吸收或经人裁决；P2 全部吸收，其中一条只上报不在本 spec 修。**

| # | 严重度 | 发现（证据） | 失败场景 | 处置 |
|---|---|---|---|---|
| R-1 | — | 开单走 `run_pipeline tickets` 还是新工具（§二 待审项）。逐条比：弹卡且无 `allow_key` → `review_tool_call` 的 `CONFIG_WRITE_COMMANDS` 同款分支现成；卡面全文 → `command_preview`（D51 / D60 先例）现成；bash 绕卡 → 两种方案都得加 bash 规则；现场取自日志 → 与工具形态无关；长文本走 argv → `shlex.split` 能处理引号与换行，ARG_MAX 远大于 5 × 4000 字 | — | **接受**，不立 ADR。新工具多出的只有参数结构化，代价是一份 ADR 与工具表上调 |
| R-2 | P1 | 去重签名 = 规范化命令 ⟂ 报错末行。本仓库很多模块以一句汇总收尾（如 `FAIL …`、`退出码 1`），原稿的复现记录只记「时间 / 会话 / 代码版本 / 期」 | 同一命令下末行相同的两个不同 bug 被合进一张单，第二个 bug 的输出没有落盘，coding agent 只看得到第一个 | 已吸收：§4.5 每条复现记录带完整现场、卡面并排两次尾部；§6.5、M19 |
| R-3 | P1 | §4.1「模型不能指定别的期」。`validate_pipeline_command` 只在没有位置参数时补位（`corrections` 那一支：`if len(pos_args) == 1 and pos_args[0] in _SUBCOMMAND_WORDS["corrections"]:`），显式给的位置参数原样放行 | `tickets open data/episodes/<别的期> --call …`：工单的「期」由模型决定，去别的期日志里取现场 | 已吸收：§4.1 位置参数必须等于宿主绑定目录；§6.5、M16 |
| R-4 | P1 | `tickets list` / `show` 原稿只说「只读免卡」，没说打印什么 | `show` 打全文 → coding agent 写的「处理结果」与模型字段原样进模型上下文并出网，与 §三 #6、§4.6 自己的理由相反 | 已吸收：§4.6 两者只打系统解析字段；§6.5、M24 |
| R-5 | P1 | 与 D81 的衔接：原稿写「企划会话补企划目录」，但 D81 企划会话的放行集合不含 `tickets`；`--blocked` 没有总装；§4.8 的提醒按 `inspect_episode(期)` 判，对企划目录无意义 | 总装撞 bug，在跑总装的会话里开不了单；开了也没有提醒 | 已吸收：D81 §4.2 放行 `tickets`；本 spec §4.1 `总装`、§4.8 按合集期 `04-assembly.json` 判；§6.5 |
| R-6 | P2 | bash 规则「后面跟着子命令」。argparse 接受 `python -m pipeline.corrections -- global …`；`-m 'pipeline.corrections'` 带引号也能跑 | 紧跟式正则漏掉这两种写法，`config/` 照样免卡写 | 已吸收：§4.4 改为「之后任意位置」；§6.5、M21 |
| R-7 | P1 | 「有没有任何路径能自动拉起 coding agent」：bash 免卡（ADR-0030），本机已装 `claude`（`~/.local/bin`）与 `pi`（`/opt/homebrew/bin`）。原稿的两条静态扫描只看 `tickets.py` 与 `desktop/src` | `claude -p "处理工单 tickets/T-…"` 经 bash 免卡执行，coding agent 直接改 `pipeline/`——「第一阶段不自动拉起」与 Code Freeze 同时失守，命令里没有 `pipeline/` 字样，`_FREEZE_PATH` 抓不到 | **人裁决 Q4（2026-10-12）**：加 `[拉起 coding agent]` 硬危险；§4.4、§6.4、§6.5、M23 |
| R-8 | P2 | 「系统采集字段模型改不了」：会话日志是 `os.write` 追加、`flock` 是建议锁，bash 的 `>>` 能写进去 | bash 追加一条伪造的 `origin: "tool"` 失败记录，再用它的 id 开单，「现场」由模型决定 | 已吸收：§十 残余一行写明准确含义（经 `tickets` 参数改不了）；人在卡上仍看全文。不另加机制：与 ADR-0030「静态规则可绕过」同类 |
| R-9 | P2（上报） | §三 #2 指出 `run_pipeline` 的结果没过 `scrub_secrets`。后果不止于工单：`_tool_message` 只过 `scrub_restricted`，所以**今天** `run_pipeline` 输出里若有密钥值，会原样进会话日志并出网（bash 结果是脱过的） | 某模块把密钥打进 stdout / stderr（例如诊断类子命令），它就进了模型请求 | **不在本 spec 修**（D82 只保证工单里脱敏）。已登记为 **D83**（2026-10-12，人裁决 supercell 开始生产前修，随施工一并修）：`_tool_run_pipeline` 返回前对 `stdout_tail` / `stderr_tail` / `message` 过 `scrub_secrets`，与 bash 对齐 |
| R-10 | P2 | M12「`handoff` 文本取自模块输出」由「伪造 `TICKET_JSON` 指向 `tickets/` 外」来抓。若变异只改文本来源、路径照校验，这条用例照样绿 | M12 永远绿 | 已吸收：§6.5 加「`TICKET_JSON` 多塞 `handoff` 键」用例 |
| R-11 | P2 | §4.2 写「结果记录里的 `returncode`」。真实形状是 `message.content` → JSON → `result.returncode`；`run_pipeline` 的命令原稿要事后重新规范化 | 照字面实现取不到 `returncode`；事后重算的 argv 可能与实际执行的不同 | 已吸收：§三 #3 写明形状；§4.2 命令取 `result.argv` |
| R-12 | P2 | 压缩事件 `{"k": "compaction", …, "tail": tail}` 带同一批消息的副本；§4.2 第 2 条要求「恰好一个」却没说数哪些记录 | 压缩过的会话里任何调用都「出现两次」，开不了单 | 已吸收：§4.2 只数 `k == "msg"`；§6.5、M17 |
| R-13 | P2 | 去重键原样写进头部 `去重键:` 行与卡面，原稿没说在脱敏前还是后算 | 报错末行带密钥 → 头部明文 | 已吸收：§4.3；§6.5、M18 |
| R-14 | P2 | 工单号 = 秒级时间 + 签名前 6 位；状态 `不是bug` / `不修` 的去重行为没定义 | 已修单回归后同一秒内再开 → 同名覆盖旧单；撞上「不是bug」单不知道新开还是追加 | 已吸收：§4.3 冲突加 `-2`；§4.5 两种状态新开并写明上次结论；M20 |
| R-15 | P2 | §4.8「当前工序在 `阻塞:` 之前或相同」。`inspect_episode` 的步骤键把 03.5 与 04 合成 `03.5`，也从不停在 `02.8` | 不写明的话，`阻塞: 04` 与 `阻塞: 02.8` 的提醒行为取决于实现者怎么排序 | 已吸收：§4.8 写明顺序与两例；§6.5、M25 |
| R-16 | P2 | §8.3 ② 锚点 `ConversationPane.tsx` 的 `case "tool":` 不存在：`case "tool":` 在 `shared/convFold.ts`，渲染端是 `function ToolLine(` | 施工者找不到锚点 | 已吸收：§8.3 ②，并写明 VS-8 / 门禁 1 |
| R-17 | P1 | bash 写 `tickets/` 不弹卡 | 伪造「已修」工单 → ava 推送一条带续跑命令的提醒，人照做；或绕过人卡直接落单 | **人裁决 Q3（2026-10-12）**：加 `[写工单目录]` 硬危险；§4.4、§6.5、M22 |
| R-18 | — | 锚点普查：`scrub_secrets(done.stdout_tail, …)`、`executed_job.stdout_tail`、`record = {**record, "origin": origin}`、`control.commit(_tool_message(call, outcome), "tool")`、`synthetic_tool`、`tail_bytes: int = 4096`、`SHELL_TAIL_BYTES = 16384`、`CONFIG_WRITE_COMMANDS` 与其分支、`frame["command"]` 两处、`CONDITIONAL`、`notice` 契约、`.gitignore` 锚点注释、ADR-0030「## 取代与修订」、`session_titles.py` 的 `fcntl`、`paths.atomic_write`、`ToolRow`、`CopyButton`、`STEP_KEY_MAP`；「status_card 已从 tools 导入 scrub_restricted」「仓库里没有守 .gitignore 的测试」两条现状判断 | — | 通过。错引 1 处见 R-16 |
| R-19 | — | 覆盖：上位需求 A（开单、系统 / 模型字段分工、卡上全文）、B（去重，末行代替首行有理由）、C（一键复制、第一阶段不自动拉起）、D（处理结果写回、开会话提醒）、§2 不做、§3 两项待裁，全部有落点 | — | 通过。C 的「不自动拉起」在 bash 侧原有缺口，见 R-7 |
| R-20 | — | UI 规则 1：§一 写了两档窗口、与 D81 同一份侧栏内容量、六步连续操作与每步要看到什么；按钮位置留给 PR-B 截图 | — | 通过 |

**上位需求本身**：「开单工具（新工具）」与 ADR-0025 封顶冲突，原稿已在 §三 #1 处理，红队接受（R-1）；「同一报错首行」在只有尾部的输出上不稳定，原稿已改末行，红队接受并补上误合并时不丢证据（R-2）。没有发现其他错误。

**改完复核**：本节新增与改写的锚点（`function ToolLine(`、`if (row.k === "tool") return <ToolLine`、`_assistant_index`、`"k": "compaction"` 与 `"tail": tail`、`_AUTOFILL_MODULES`、`READONLY_ASSET_SUBCOMMANDS`、`_FREEZE_PATH`、`_WRITE_FORM`）已重新 grep，全部命中。

## 十三、施工记录

### PR-A（2026-10-13）

**已施工·机检通过。** 分支 `d81-d82-project-layer`，排在 D81 PR-2b 之后（§五）。

- §6.3 ①–⑫ 全部落地：`pipeline/tickets.py`（新）；`session_log.find_tool_exchange`；`tools.py` 加模块 `tickets`、六个带值旗标、补位分支（只给 `open` 与无位置参数的 `list`）、`READONLY_ASSET_SUBCOMMANDS["tickets"] = {list, show}`、`HUMAN_GATED_COMMANDS`、交接模板 `TICKET_HANDOFF_TEMPLATE` 与 `ticket_handoff()`；`session.review_tool_call` 改走 `HUMAN_GATED_COMMANDS`，`tickets open` 先跑 `tickets.preview(rest, bound_dir=ep_dir)`（不成立 → `reject`，成立 → 卡面是工单全文、无 `allow_key`）；`status_card` 三条新规则（`[须人审的流水线命令]` 由 `HUMAN_GATED_COMMANDS` 生成、`[写工单目录]`、`[拉起 coding agent]`）；实时 tool 帧与 history 帧的 `handoff`；`ready` 之后的 `ticket_fixed` notice；`.gitignore` 的 `/tickets/`；`tests/test_tickets_static.py`；`creative.md` 新小节与 `WORKFLOW.md` 第 5 条（⑪ 写的「`docs/runbook/`（WORKFLOW 停机规程旁）」落在 WORKFLOW 的停机规程条目下）；ADR-0030 修订注。
- `CONFIG_WRITE_COMMANDS` 仍在 `session.py` 定义，审查不再直接用它；它是 `HUMAN_GATED_COMMANDS` 子集这件事由 `test_写config的三条是逐条人审集合的子集` 钉住。
- `list` / `show` 的 `续跑` 行与提醒同一规则（单行、脱敏、超 200 字截断），不出正文。
- **施工中发现并修的缺陷**：同签名取「上一次」的结论时，工单按文件名字典序排，`T-…-2.md` 排在 `T-….md` 前（'-' < '.'），取到的是更早那张。改为按工单号里的 UTC 时间、再按 `-2`、`-3` 后缀排（M26）。
- **变异检验暴露的测试缺陷**：初版协议用例（提醒不进模型请求）的夹具把 `config/agent.local.json` 写成只有密钥（给脱敏用例用），它整文件取代 `agent.json`，模型根本没被调用，「请求里没有工单号」恒真。改为写完整的假端点配置，并加断言「端点至少收到一次请求」。另外，M13 的第一版写法（往 `_startup_tail` 的 `messages` 追加）是等价变异——新会话首轮由系统提示 + 状态卡重建消息，那份列表不会被发出；改为经状态卡进 system prompt 这条真实可能的泄漏路径后被抓。
- 既有变异矩阵：`scripts/verify_mutations.py` 的 D78-3 锚点随 ④ 搬到 `elif _human_gated(module, rest):`，`--only D78-3 --dirty-ok` 全量重跑 KILLED（红 4 条：`test_d51_tv9_good_reading_card_shows_plan`、`test_show免卡_set弹卡且卡上有旧值新值`、两条 `TestReview`）。
- 测试：`tests/test_tickets.py` 69 条 + `tests/test_tickets_static.py` 2 条；vitest `convFrames` +1（`handoff` 有键才查、必须是字符串，tool 与 history 两种帧）；e2e `sessionReal.spec.ts`「D82 开工单」（真实 core + 假模型：bash 真失败 → `run_pipeline tickets open` 弹卡、卡面含命令与退出码 → 批准 → tool 帧带 `handoff` 且过 TX-0 契约比对、临时仓库 `tickets/` 落一份工单）。并发用例在子进程里给 `all_tickets` 包 0.5 s 延迟，让「查重 → 写」窗口必然重叠，M7（不持锁）才稳定可杀。
- 变异 26/26 被抓（M1–M25 照 §6.6，另加 M26；scratchpad `d82a_mut.json`，跑完 `git diff` 只剩真实改动）：

| # | 抓到它的测试 |
|---|---|
| M1 | `test_多给系统字段参数不成立` |
| M2 | `test_不成立[synthetic_tool …用法]` |
| M3 / M4 | `test_run_pipeline尾部与模型字段里的密钥` |
| M5 / M19 | `test_同命令同末行待修_追加且带第二次输出` |
| M6 / M20 / M26 | `test_已修同签名新开写可能是回归_不是bug写上次结论` |
| M7 | `test_两个进程同时开同签名_只有一个新文件` |
| M8 | `test_运行时_仓库没有gitignore拒写` |
| M9 | `test_gitignore覆盖tickets目录` |
| M10 | `test_open弹卡_无本会话都批准_卡面是全文` |
| M11 / M21 | `test_bash规则_须人审的流水线命令`（`pipeline/tickets.py` 写法 / `-- global`） |
| M12 | `test_handoff文本只来自模板` |
| M13 | `test_协议_提醒只走notice不进模型请求` |
| M14 / M25 | `test_提醒看工序进度`（05 / `阻塞: 04`、当前 `03.5`） |
| M15 | `test_stdout不含正文_末行是TICKET_JSON` |
| M16 | `test_显式给别的期不成立` |
| M17 | `test_压缩事件的tail副本不算重复` |
| M18 | `test_密钥在报错末行_去重键卡面与stdout都脱敏` |
| M22 | `test_bash规则_写工单目录` |
| M23 | `test_bash规则_拉起coding_agent[claudette-False]` |
| M24 | `test_list与show不出正文` |

- `git check-ignore -v tickets/x.md`：`.gitignore:34:/tickets/	tickets/x.md`；`git ls-files tickets` 为空；施工前后仓库根下没有 `tickets/` 目录（测试一律在临时仓库）。
- **验证**：pytest 2859 / typecheck 通过 / vitest 562 / playwright 142（2 跳过）。

### PR-B（2026-10-13）

- 附件 `2026-10-12-bug-ticket-spec/`（`shots.config.ts` + `options.shots.spec.ts`），照 D80 先例：未打包构建 × 临时夹具仓（侧栏内容量同 D81 §一）× 真实 core × 假模型；开单是 PR-A 的真实链路（bash 真失败 → `run_pipeline tickets open` 弹卡 → 批准 → 真实 `handoff` 帧 → 临时仓库 `tickets/` 落单），第 5 步改夹具工单的「处理结果」后重开会话，拍到真实 `ticket_fixed` notice。只有工单那条工具行上的按钮 / 下一行 / 提示条与卡面 K1 / K2 是原型 DOM（`adoptedStyleSheets` 注入）。产物 `desktop/out/d82-shots/pr-b/`（对照页 `index.html`、`measures.json`）。
- **实测发现**：① 回合跑完默认折叠（D80 R4，`turnDefaultOpen`），工单那条工具行随之收起——A / B 的按钮在默认状态下看不见，若选它们 PR-C 要另改折叠规则；② PR-A 的开单卡卡面是整份工单，卡高 1167 px，对话流可见区 444 px（1280×800）/ 544 px（1440×900），两档都只见卡头、不见「批准」，不满足 §一 第 2 步。为此加了卡面两种原型：K1 摘要在前（卡高 249 px，两档卡头与批准同时可见，不点开就看到命令 / 退出码 / 报错末行 / why）、K2 全文限高 200 px 内部滚动（两档同时可见，但前 200 px 被 `run_pipeline` 的完整 argv 回显占掉，why 要滚动才见）。
- 夹具注意：`realCoreFixture` 默认给每期配 `03-audio` 与 `04-clips.json`（停在 05）；场景要「02.5 已批、停在 03」，所以删这两项并放 `02-diff.patch`（02.5 的封板产物）。工单阻塞 03 的提醒只在还没走过 03 时出现，停在 05 时不出提醒是正确行为。
- **人选**：按钮 **C 工具行 + 提示条**、卡面 **K1 摘要在前、全文收起**（2026-10-13），已写回 §二 Q2 / Q5。

### PR-C（2026-10-13）

按人选（按钮 C、卡面 K1）施工。**机检通过，待人验收。**

- §8.3 ①–③ 照原稿，④–⑥ 为人选 K1 / C 后补（已写进 §8.3 表）：`ToolRow.handoff`（实时帧 end 阶段与历史帧都取）；工具行行尾 `CopyButton`「复制给 coding agent」（复制 `handoff` 原文，常驻可见）；输入框上方 `HandoffStrip`；带 `handoff` 的行主文本「已开工单 T-…」；开单卡卡面 K1：core 新增 `tickets.preview_summary`、`HumanRequest.card_summary`、请求帧条件键 `card_summary`，渲染端有摘要就把 `card_text` 收进「工单全文」折叠（其余卡与终端通道不变）。样式只进 `style.css`，`tokens.css` / `ui.css` 零改动。
- 实测（`impl.shots.spec.ts`，产物 `desktop/out/d82-shots/pr-c/`）：开单卡卡高 249 px，1280×800（可见区 444 px）与 1440×900（544 px）下卡头与「批准」同时可见；回合跑完折叠后提示条仍在；展开回合后工具行行尾按钮可见；第 5 步 `ticket_fixed` 提醒照旧。
- 测试：vitest `convFold`（实时 / 孤立 end / 历史取 `handoff`，空串视同没有）、`toolView`（`ticketIdOf` 含 `-2`、主文本、提示条去重取最后一次）、`convFrames`（`card_summary` 有键才查）；pytest `test_卡面摘要_K1_全文仍在card_text`（摘要逐行、末行密钥脱敏、其余卡无摘要）；e2e「D82 PR-C」（真实 core + 假模型：1280×800 下卡面是摘要、全文在折叠里、卡头与批准同时在对话流可见区；批准后提示条复制、展开回合后工具行复制，系统剪贴板读回都等于帧里的 `handoff`；失败的 bash 行没有按钮；1280 / 1440 下按钮与工具行其他元素、提示条各元素两两不相交，按钮不越出所在工具行、提示条不越出对话流；退出并重启 app → 进上次的会话 → 历史帧带 `handoff`、提示条与工具行按钮都在、复制得原文）。
- `test_桌面端交接相关代码不spawn` 的静态扫描原用子串 `exec(`，会误中正则的 `.exec(`（`convFold.ts` 因出现 `handoff` 字样新进扫描范围）；改为只认引 `child_process` 或裸调 `spawn` / `exec` / `execFile`（含 Sync），并带正反例自测。
- 变异 12/12 被抓（scratchpad `d82c_mut.json`）：P1 工具行按钮复制摘要（e2e）、P2 / P2b 历史行丢 `handoff`（vitest / e2e 重启后）、P3 提示条取第一次的交接文本（vitest）、P4 渲染端无视 `card_summary`（e2e）、P5 摘要漏 why、P6 摘要不脱敏（pytest）、P7 协议不发 `card_summary`（e2e）、P8 不渲染提示条（e2e）、P9 每条工具行都给按钮（e2e）、P10 主文本不改（vitest）、P11「工单全文」默认展开挤掉批准按钮（e2e）。
- **遗留（既有问题，未修）**：预览展开时（1280×800 中栏约 605 px），回合展开区里的工具行比对话流宽 2 px，对话流出现 2 px 横向滚动——带不带本按钮都一样（失败的 bash 行同样），是 D80 工具行版面的既有问题；预览收起时（§一 场景）不出现。
- **验证**：pytest 2860 / typecheck 通过 / vitest 568 / playwright 143（2 跳过）。

### 收尾核对（2026-10-13）

EGOIST V2 只读核对与 D81 共用一次：见 [D81 spec §十五「收尾核对」](2026-10-12-project-layer-spec.md)——自分支开工（2026-10-12 20:40）起的写入与 PR-1 记录的相同（20:58:56–20:59:04 桌面端停机点对象、22:39 `.DS_Store`），D82 各 PR 无新增写入。

---

## 十四、施工审查记录（2026-10-13，零上下文审查）

> 审查人不是施工会话。复跑、变异、红队的方法与总表见 [D81 spec §十六](2026-10-12-project-layer-spec.md)；本节只记 D82 的部分。

### 复跑与范围闸门

- 三条验证命令在分支 HEAD（5e18c96）全绿：pytest 2928（含审查补的 1 条）、typecheck 通过、vitest 568、playwright 143（2 跳过）——与施工记录所贴逐项对上。
- D82 的 diff（PR-A / PR-B / PR-C）逐处对得上 §6.3 / §7.3 / §8.3 清单与施工记录的偏差申报（卡面 K1 与按钮 C 是人选后补进 §8.3 ④–⑥ 的；`scan.ts` 的方法闭集加 `project.create` 是 TG-16 的要求）。无超范围改动。

### 发现与处置

1. **[已修 `5e18c96`] 测试缺口**：`tickets show <工单号>` 的 `_TICKET_ID` 形状闸此前无测试（变异删闸全绿）。已补回归测试并做变异验证。详见 D81 spec §十六 发现 2。
2. 其余无 D82 独有的缺陷。两条观察记录在 D81 spec §十六 发现 6–7（`bash -c` 间接拉起与 `_is_memory_file` 冗余）。

### 红队实测（工单各项，审查者自造夹具亲跑）

- **系统采集字段改不了**：模型 `--symptom` 里谎称「退出码 0」→ 工单现场段仍是日志里的真实值；显式给别的期目录 → `preview` 不成立（弹卡前拒）。
- **脱敏覆盖**：假密钥（`config/agent.local.json` 放 `sk-PROBE-…`）出现在 `run_pipeline` 的 stderr 末行与模型字段里 → 工单两处、头部 `去重键:` 行、卡面、stdout 摘要全部是脱敏后的。
- **进 git 拦截（真实仓库实测）**：`git add tickets/T-….md` 被 `.gitignore:34:/tickets/` 拒（exit 1）；`git ls-files tickets` 为空；试完撤掉，`git status` 干净。
- **去重不误合并**：同命令同末行（待修）→ 追加且复现记录带第二次输出原文；末行不同 → 新开；同签名已修 → 新开且首行「可能是回归」；`不是bug` 结论写进新单首行。
- **自动拉起**：`tickets.py` 的 subprocess 调用第一个参数只有 `git`（ast 扫描复核）；`[拉起 coding agent]` 规则的正反例（`claude -p` 拦 / `claudette`、`pip`、`./pi-tools` 不拦）经变异 D82-M23 复核被抓。
- **提醒不进模型**：`find_fixed` 只在「已修 + 没走过阻塞工序」时出 notice；提醒文本只含系统解析字段（提交号不合式写「未填」）。
- **闭环提醒实测**：E1 已修单（阻塞 03、期未过 03）→ `notice_text` 含工单号 / 提交 / 续跑命令；别的期不提醒。
- 变异抽查：D82-M8 / M12 / M23 / M26 被抓 + 施工自报 M1–M26 的清单与测试名抽查吻合。

### 界面（PR-B / PR-C，UI 规则 4/5）

- 人选已写回 §二：按钮 **C 工具行+提示条**、卡面 **K1 摘要在前**（2026-10-13）。实现与方案一致（代码级核对：工具行 `CopyButton` + `HandoffStrip`；`card_summary` 进 `convFrames.ts` 契约并有 malformed 测试）。
- 底线机检随 playwright 通过（1280×800 卡头与批准同见、两档不相交不溢出、剪贴板读回原文、重启仍在）。**好不好用由人验收**（§九第 1 条未勾，本 spec 状态保持「机检通过，待人验收」）。
