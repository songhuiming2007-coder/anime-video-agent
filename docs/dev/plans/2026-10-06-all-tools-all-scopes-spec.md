# Spec 17：工具不再按模式（scope）分配，所有模式开放全部工具（D43）

> **状态：v0.2，定向复审 🟢 可动工，待人确认后施工 D43-B**（2026-10-07 作者修订，回应 D43-R 的 5🟡 + 8🔵，见「作者修订回应」；🟡-1 人 2026-10-07 裁决 (A)；同日 D43-R2 定向复审 🟢，13 条全部核销，另 6🔵 由施工吸收，见「定向复审」）。沿革：v0.1 草案 2026-10-06 立文；§8 Q1–Q3 人同日裁决全部按建议；同日红队一轮 D43-R 🟡 修订后复审。本文件不改代码；作者修订并经红队复审 🟢、人确认后才施工（D43-B），施工后另开 session 独立评审（D43-C）。
> 对应 issues：**D43**（主）；与 **D42** 交叉（D42 方案 (a) 的「idea 补联网工具」被本 spec 覆盖，见 §6）。
> 相关：ADR-0021（网络工具内化，「网络工具只对 asset / creative 可见」）、ADR-0025（工具表封顶 14，`cover_edit` 只对 creative 可见）、ADR-0023（跨期记忆，`write_memory` 只挂 creative）、Spec 10（`archive/2026-09-25-desktop-conversation-panel-spec.md`）§2.5（idea 会话零写权限）、impl spec（`2026-09-18-ava-agent-impl-spec.md`）§2.4 / §2.5 B3-r6（scope 白名单）；新提 **ADR-0027**（`docs/dev/adr/0027-tools-not-gated-by-scope.md`，提议中）。

## 红队一轮裁决（2026-10-06，D43-R，独立 session 未参与立文；只审文稿，未改仓库代码）

**裁决：🟡 修订后复审**（5🟡 + 8🔵，其中 🟡-1 需人裁决）。方向与机制主干站得住：全仓 grep 确认 §2 表列的三层模式闸之外没有第四处（桌面端无按模式过滤工具的代码）；§3.3 右列护栏逐条核实与模式无关（`--force` 前缀禁令与 `cloud exec` 禁令本就在 scope 分派之前全局生效）；`PIPELINE_MODULES` 与 `ASSET_COMMANDS` 不相交；R1 出网探针 14 例全拦；R2/R3/R5 的机制结论经读码与实测确认成立。阻塞项在：§3.4 的 idea 放行表与抓取卡链自相矛盾（🟡-1）、两处指定杀手杀不死对应回退（🟡-2/🟡-3）、§7 文档修订面漏五处（🟡-4）、shipped 变异矩阵三条锚点被本 spec 拆除而未提（🟡-5）。

| 编号 | 指控 | 证据 | 建议 |
|---|---|---|---|
| 🟡-1 | **§3.4 放行 idea 的 `acquire_propose`，与抓取卡链自相矛盾（需人裁决）**。`_post_execute` 对 `acquire_propose` 成功**无条件**自动弹抓取卡（不看 scope、不看 ep_dir）；抓取卡批准后内核执行器 `_fetch_executor` 调 `run_pipeline("acquire fetch N", episode_dir=None, scope="asset", confirmed=True)`。按 §3.4/Q2 的「先建期」拦截（若落在 `run_pipeline` 函数级），人在 idea 里批准抓取卡**必**拿到「先建期」错误——批准被白费，且文案误导（抓素材写库级 incoming 池，根本不需要期）。现行代码该路径合法：探针实跑真仓 `run_pipeline('acquire fetch 1', ep None, scope='asset')` dry-run → ok「待人类确认」 | `session.py:1125 _post_execute`（仅判 `name` 与 `ok`）、`:1260-1265 _fetch_executor` 默认实现（生产无 override，cli/protocol/desktop 均不设 `fetch_executor`）；探针 `d43r/probe` 真仓 vs 原型两端对比；Q2 的「无期路径从未实测」理由同样适用于这条内核路径 | 三选一，**需人裁决**：(A) idea 下 `acquire_propose` 也统一报「先建期」（与 `run_pipeline` 一致，最收）；(B) `acquire_propose` 放行照旧，但 `_post_execute` 在 ep None 时不弹抓取卡（候选照落池、提示建期后抓；「先建期」拦截只放 review/模型路径，内核执行器不受影响）；(C) 抓取链在 idea 全通（`acquire fetch` 无期执行转正、事件落库级——但这正是 Q2 判为「扩面」的事）。选定后 TA-6 同步加对应断言 |
| 🟡-2 | **TA-6 杀不死 MUT-A8（弹卡 ≠ 落盘）**。MUT-A8「`memory.apply_op` 恢复 creative-only」的指定杀手是「TA-6（idea 写记忆弹卡）」；但弹卡走 `review_tool_call` → `memory.plan_op`，`plan_op` 没有 scope 参数、不含 creative-only 检查；该检查只在 `apply_op`（执行段）。TA-6 措辞只到「调 `write_memory` → 弹卡」，人按 y 之后 `apply_op` 才炸——MUT-A8 在「只弹卡不执行」的 TA-6 下存活 | `memory.py:840 plan_op` 签名无 scope；`:862-874 apply_op` 的 scope 闸；`session.py` review 的 write_memory dry-run 走 `plan_op` | TA-6 的 write_memory 腿改为完整回环：弹卡 → 人按 y → **真落盘**（memory.md 出现条目、日志行 episode 为 null、scope 记为 idea）；或新增一条「非 creative scope 直调 `apply_op` 成功」。MUT-A8 杀手指定随之修正 |
| 🟡-3 | **review 层的越 scope 拒绝若漏删，没有任何新增用例杀得死**。「只改 tools.json 不删实现内检查」的最隐蔽残留是 `review_tool_call` 的越 scope 拒绝：TA-3 走 `execute_tool` 直调（打桩），不经过 review；§9 对 pr6 M20 的改写是「越 scope 拦截 → 未注册名字拦截」，改完后 review 层不再有「已注册但旧意义上越 scope 的工具应放行」的断言。施工若删了 `execute_tool` ② 层却在 review 留了同样的拒绝，全量测试照样绿、行为照旧（pipeline 调 `web_search` 在弹卡前被 reject） | `session.py:259-263` review 层越 scope 拒绝；TA-3 措辞「`execute_tool` 调 `web_search`（打桩）」；§9 对 M20 的改写指令 | TA-3 加 review 腿（或新增 TA-3b）：三个非 creative scope 下 `review_tool_call("web_search"/"write_memory"/"run_pipeline")` 的 action 不为 reject；MUT 清单加一条「review_tool_call 保留越 scope 拒绝 → TA-3b 杀」 |
| 🟡-4 | **§7 文档修订面至少漏 5 处「某模式零写/不可见」的句子**。§7 列举与 §3.5 的 grep 指令（只覆盖 `AGENTS.md` 与 `docs/runbook/`）漏了：(a) `docs/CHEATSHEET.md` L33「idea scope，写权限为零」与 L114「未注册/超 scope 的工具在弹卡之前就被拦下」（改后「超 scope」拒收不存在）；(b) `archive/2026-09-21-ava-entry-idea-scope.md`（D27，idea 零写机制的立规文件，§2.2「写权限为零不靠 prompt 劝导：write_episode_file 不在表内 + execute_tool 按 scope 白名单拦截」等多处）；(c) `archive/2026-09-25-agent-session-protocol-spec.md` L226/L782（idea「零写权限」两处）；(d) `archive/2026-09-20-ava-ai-native-director-spec.md` L249（「pipeline scope 看不到 write_episode_file——制片期零写权限」）；(e) impl spec §2.3 L324「ava idea…只读 4 工具，写权限为零」与 §1.2 目录注释（§7 只点名 §2.4/§2.5 B3-r6）。另 ADR-0023 L76 同一行里「idea 零写权限（机制保证）」半句亦被证伪，§7 只说「write_memory 只挂 creative 的表述加注」 | 上述行号逐一核对；grep 口令 `docs/` 全仓「零写\|只对.*可见\|永不见\|写权限为零」 | §7 补列 (a)–(e)；ADR-0023 加注覆盖 L76 整行；§3.5 的 grep 范围从「AGENTS.md 与 docs/runbook/」扩为「docs/ 全仓（含 archive）」 |
| 🟡-5 | **shipped 变异矩阵三条锚点被本 spec 拆除，MUT-A1~A8 未要求登记入矩阵**。`scripts/verify_mutations.py` 的 shipped 矩阵：M20（session.py 越 scope 预校验，old 锚 = 被删代码）、M24（tools.json idea 四键段，old 锚 = 被删配置形态）、M29（status_card idea 卡「写权限: 无（机制保证）」全文，old 锚 = 被改文案）在本 spec 落地后锚点全部失配，元守卫 `test_anchors_in_shipped_matrix_are_unique_in_repo` 当场红。M20/M24 守护的语义本身被本 spec 废除，应显式退役而非改锚；M29 随新文案改锚。且仓库惯例是变异登记进 shipped 矩阵，§9 的 MUT-A1~A8 只写在 spec 里 | 原型副本（scratchpad `d43r/proto`，按 spec 意图最小改动）实跑该元守卫 →「M20 @ session.py 命中 0 次、M24 @ tools.json 命中 0 次、M29 @ status_card.py 命中 0 次」；`scripts/verify_mutations.py:193-201/256-264/354-362` | §9 增一条：PR1 同提交内 M20/M24 从矩阵退役（注明被 D43 废除）、M29 锚点随 §3.5 新文案更新、MUT-A1~A8 登记进 `scripts/verify_mutations.py` 并各配锚点 |
| 🔵-1 | §9 改写清单远小于实跑失败面。原型实跑（排除副本缺 docs/ 等 artifact）失败分布于 ≥14 份文件：test_agent_tools（约 24）、test_agent_protocol（19）、test_agent_pr6（13）、test_agent_loop（12）、test_golden_terminal（12）、test_agent_llm_tiering（7）、test_agent_web（5）、test_agent_crawl_browser（4）、test_agent_director（3）、test_agent_session（4 + 10 夹具 error）、test_agent_assembly_integration（2）、test_agent_memory（2）、test_candidates_propose（2）、test_review（1）、test_run_pipeline_pinning（1）；另 7 份文件 collection 即 ImportError（导入 `tool_names_for_scope`/`CREATIVE_WRITABLE_FILES` 旧名：director/loop/pr6/session/terminal_session/tools/golden_terminal）。§9 只列 6 份。绝大多数同根因：共享夹具（`make_agent_root`/`SPEC_TOOLS`、`_agent_root`、`_make_web_root`、golden/protocol 夹具）写四键 tools.json，或 `test_agent_llm_tiering` monkeypatch 旧签名 | `d43r/proto` 全量 `uv run pytest`（PYTHONDONTWRITEBYTECODE=1）输出；逃生舱「以实跑失败列表为准」程序上兜得住，但清单误导施工量估计 | §9 写明改写轴：先改共享夹具与签名替身，再按实跑列表逐条收尾；本行的文件清单照录进 §9 |
| 🔵-2 | 反向分叉检查的报错形态与「注册表扩张」夹具耦合未写。测试在子进程注册 `test_ping`/`test_slow`/`test_stdin_child`/`test_writer` 等假工具时，夹具 tools.json 必须同步列出它们，否则反向检查在会话第一轮 KeyError（子进程 rc=1）。单表失败模式实测：tools.json 缺失/损坏/旧四键格式 → 单表加载得空表（`load_scope` 仍吞异常回空表）→ 反向检查必炸 → fail-closed，「读取失败不静默扩张」语义在单表下成立且更响亮 | test_agent_protocol 子进程 stderr 的 KeyError 全文（含四个 test_* 工具名）；原型对缺失/损坏/旧格式的三态验证 | 写死：反向检查与 B3-r6 正向检查同点同形态（`build_tool_schemas` 抛 KeyError 当场报错），TA-2 的「加载报错」即此；夹具 tools.json 与注册表同步扩张写进施工注 |
| 🔵-3 | 合表语义两条没写死：(a) 模块 ∈ `ASSET_COMMANDS` 时子命令校验永远生效（今天 9+6 不相交，写死可防未来重名时被「不限子命令」侧吞掉）；(b) 当期目录自动补位只对原 `PIPELINE_MODULES` 侧 8 个模块生效，asset 侧 6 模块不补位（维持现状；原型验证 asset 模块空位置参数即因子命令校验被拒，补位先后顺序不影响结论，但写死防施工合错） | 原型合表实现与对拍：`cloud status`/`tts --redo 3` 过、`faces unknown`/`cloud exec`/`tts --force-a` 拒 | §3.3 run_pipeline 行加这两句 |
| 🔵-4 | 「先建期」拦截的机制位置没写死，且与 🟡-1 联动。TA-6 的「不弹卡」只能由 review 层 reject 实现（write_episode_file/cover_edit 无 dry-run，side_effect 分流在弹卡之前没有拦截点）；`run_pipeline` 函数级是否也拦，直接决定内核抓取执行器在 idea 下的生死 | `session.py:289-305` review 顺序；原型把拦截至 review 层（reject）+ 实现层统一文案双保险 | 写死：review_tool_call 对 ep None 的 {write_episode_file, cover_edit, run_pipeline} 先 reject（统一文案、不弹卡）；实现层 `_tool_write_episode_file`/`_tool_cover_edit` 统一文案双保险；`run_pipeline` 函数级拦不拦随 🟡-1 裁决 |
| 🔵-5 | TA-1/TA-5 措辞瑕疵。TA-1「名字集合 == TOOL_SCHEMAS 键集合」只在 crawl4ai/playwright extras 已装时成立（`build_tool_schemas` 对缺 extras 的工具跳过）；TA-5「creative 模式 validate_pipeline_command(…)」的「creative 模式」在合表后无意义（scope 参数已删） | `tools.py:721-723` extras 跳过逻辑 | TA-1 写明 extras 前提（或断言 == 注册集 ∩ extras 可用集）；TA-5 改「任意调用路径下」 |
| 🔵-6 | R4 实测结论可直接写进 §3.4（代替「施工时须核实」）：ep None 时 `log_approval_decision` 发 approval_resolved 事件落**库级** `data/_events.jsonl`、期级 approvals.jsonl 不记、idea 会话记录本就不落盘（persist=False）——批准不静默丢，但比期级少 latency/channel/request_id 字段。另：`write_memory` 的 op=cite 在 idea 下报「cite 需要会话绑定的期目录」（`episode_ref_of(None)` 抛 PermissionError），语义正确，§3.4 的「放行」严格说不含 cite | 探针 `d43r/probe_r4_idea.py`：13 个工具 ep None 逐一直调 + 批准记录落点实跑（事件 1 行入库级 _events.jsonl、approvals.jsonl 零产生、候选池落库级） | §3.4 写明上述落点与字段缺失是「接受」还是「补齐」；write_memory 行加半句 cite 除外 |
| 🔵-7 | R2 残余未登记：封板（02-diff.patch 存在）后改写 02-script.draft.md，status 不倒退（从不看草稿内容、只看存在性）、02.5 闸不受影响（`_gate_valid` 只比 02-diff.patch 与 02-script.md 的 mtime）、人时不受影响（human_time.json 只增）；唯一损失是审计面——02-diff.patch 左半锚点（草稿原文）被改写后无法从两份文件重现（patch 内文仍在） | `status.py:223-225/311-340`、`approvals.py:332-355 _gate_valid`、`status_card.py:44` | §5 R2 的「本 spec 的判断」列补这一句 |
| 🔵-8 | `desktop/e2e/sessionReal.spec.ts:372` 注释「acquire_propose 只在 asset scope」过期（e2e 切 asset 才调 acquire_propose；改后不必切，但测试仍绿） | 该行注释 | 施工时顺手改注释，不阻塞 |

**攻击面逐项结论（含通过项）**：

- **① 漏网模式闸**：**没有第四处**。`desktop/src` 全量 grep 无按模式过滤工具/渲染分叉（scope 仅用于素材模式切换 `conv.command{scope}` 与头部标签）；`llm.py` 模型档位（ADR-0023，spec 明确不改）、`resolver.scope_of`（模式推导，不改）、`memory.scopes`（注入范围，不改）、`protocol.py command{scope}`（模式切换）均非工具放行判断；tools.json 无 `*.local.json` 覆盖机制，全仓唯一读取点是 `scopes.py::load_scope`；tests 夹具里的模式分叉即 🔵-1 清单。
- **② §3.3 右列逐条核**：`--force`/`--force-all` 前缀禁令与 `cloud exec` 禁令位于 `validate_pipeline_command` 的 scope 分派**之前**，本就全局生效，合表后不丢；`cloud run` 的 `validate_extra_args`（`args[3:]`，与 argparse 的 `run <target> <task> <extra…>` 对齐）在 asset 分支内，合表须随模块保留（🔵-3）；`PIPELINE_MODULES`（9）与 `ASSET_COMMANDS`（6）不相交，无重名绕过；当期目录自动补位模块集（tts/clips/review/render/qc/cover/status/check_script）不变；`_model_argv_has_options`（模型 review 只能生成审片页）与 scope 无关，合表后照常；`create_job`/`run_pipeline` 的 scope 降为记录字段与现状一致（`Job.scope` 照写 job_created 事件）。
- **③ R1**：探针 `d43r/probe_r1_egress.py` 14 例全符合预期——web 四个出方向（`web.py:864` search、`:976` fetch、`web_crawl.py:232`、`web_browser.py:420`）均不传 trusted_texts；把 `03-audio/manifest.json`、`03-audio/voice.json`、`cloud.local.json`、`agent.local.json` 平写/一次编码/双重编码/大小写变体藏进 query、URL query 参数、URL 路径段、crawl reason、browser navigate url+reason **一律拦**；全角同形放过 = RF-11 已登记上限，修前修后不变；正常 query/URL 对照组零误伤。受限文件**内容**无工具可读（读域硬排除 03-audio/04-patch，config/ 不在读域），泄露面止于路径名字符串本身。R1 结论成立。R2 机制结论成立（残余 🔵-7）。R3 成立（锁内重读重规划，不读而写最坏是语义重复，人审卡兜底；Q3 人已裁决）。R4 实测见 🔵-6，新矛盾见 🟡-1。R5 成立——`cloud down` 在 `cloud.py:1210-1215` 有运行时守卫（远端有活跃任务且无 `--force` → 结算账簿但不关机并提示），`--force` 禁令在 validate 层对人/模型同效；提示足够。R6 列出即可。
- **④ 单表失败模式**：缺失/损坏/旧四键格式 → 空表 → 反向检查必炸，fail-closed 成立、应当场报错（形态 🔵-2）；测试夹具大面积崩属实（🔵-1），spec 的「以施工前全量实跑失败列表为准」程序上兜得住，但应吸收本裁决的清单与夹具轴。
- **⑤ 测试与变异**：TA-1~TA-7 对「只改 tools.json 不删 `execute_tool` ②」（TA-3）、「合表漏子命令限制」（TA-5 `faces unknown`）、「idea 下静默写入」（TA-6 零写入）、「pipeline 下跳过出网断言」（TA-7）及 MUT-A1~A7 的指定杀手均对得上；两处对不上：🟡-2（MUT-A8）、🟡-3（review 层残留既无变异也无杀手）。
- **⑥ 文档修订面**：§7 对 ADR-0021（L39/L65）、ADR-0025（§2 两处 + 推翻条件句）、Spec 10 §2.5（L283）、Spec 4（掩码表整节）/Spec 12（L97-98）的处理方向正确；ADR-0027 的「取代与修订」与三份 ADR 原文逐条对得上，推翻条件自洽；与 D42 的先后与范围划分自洽（issues D42 行已加注「只剩建期迁会话一半、须在 D43 之后施工」）。漏项见 🟡-4。
- **数字核验**：§2 与 §3.6 的 schema 字数属实（实测：全集 13 件 7057 字符；现四模式 creative 5696 / pipeline 1279 / asset 2748 / idea 1201；增量 creative 1361 / asset 4309 / pipeline 5778 / idea 5856——与「≈7.1k、多 ≈1.4k/4.4k/5.9k、现 1.2k–5.7k」全部一致）。

**探针与原型**（全部在 scratchpad，未进仓库）：`d43r/probe_r1_egress.py`（R1，14 例，纯字符串判定层、零网络）、`d43r/probe_r4_idea.py`（R4，ep None 逐工具 + 批准落点，假 root）、`d43r/proto/`（按 spec 意图的最小原型副本：单表 tools.json、删三处实现内检查、合表、review 层「先建期」reject、§3.5 文案；全量 `uv run pytest` 实跑拿真实失败清单，即 🔵-1 来源；原型不进仓库）。

## 作者修订回应（v0.2，2026-10-07；修订人 = v0.1 立文人，人指定，未参与 D43-R）

逐条核过证据再改：🟡-2（`memory.py::plan_op` 签名无 scope，creative-only 只在 `apply_op`）、🟡-3（`session.py::review_tool_call` 的越 scope 拒绝在 run_pipeline / write_memory dry-run 之前）、🟡-4（五处原文逐行 grep 命中）、🟡-5（`scripts/verify_mutations.py` M20 / M24 / M29 的 `old` 锚点）均属实。

| 编号 | 处置 | 改了哪里 |
|---|---|---|
| 🟡-1 | **采纳，人 2026-10-07 裁决 (A)**：idea 下 `acquire_propose` 也统一报「先建期」。抓取卡链因此在 idea 下不会出现，内核 `_fetch_executor` 与 `run_pipeline` 函数本体不改 | §3.4 表与拦截机制段、§8 Q4、§9 TA-6 / MUT-A11 |
| 🟡-2 | 采纳：TA-6 的 `write_memory` 腿改为完整回环（弹卡 → 人按 y → `memory.md` 真落盘、日志行 `episode` 为 null）；另加 TA-6b「非 creative 直调 `apply_op` 成功」；MUT-A8 杀手改为 TA-6b | §9 |
| 🟡-3 | 采纳：新增 TA-3b（review 层）与 MUT-A9（`review_tool_call` 保留越 scope 拒绝 → TA-3b 杀） | §9 |
| 🟡-4 | 采纳：§7 补列 (a)–(e) 与 ADR-0023 L76 整行；§3.5 的 grep 范围扩为 `docs/` 全仓（含 archive） | §3.5、§7 |
| 🟡-5 | **部分采纳**：M24（idea 四键工具表）守的语义被本 spec 废除 → 退役；M29 随 §3.5 新文案改锚；**M20 不退役、改锚**——它守的是「未注册 / 超 scope」两件事，本 spec 只废掉「超 scope」一半，「未注册名字在弹卡前拒绝」仍是现役护栏，锚点缩到剩下的那段；MUT-A1～A11 全部登记进 `scripts/verify_mutations.py` | §9「shipped 矩阵」段、§10 PR1 |
| 🔵-1 | 采纳：§9 写明改写轴（先共享夹具与签名替身，再按实跑列表收尾），照录红队实跑的文件清单 | §9 |
| 🔵-2 | 采纳：反向检查与 B3-r6 正向检查同点同形态（`build_tool_schemas` 当场抛错）；夹具 `tools.json` 须与测试注册的假工具同步扩张 | §3.1、§9 |
| 🔵-3 | 采纳：合表两条语义写死 | §3.3 run_pipeline 行 |
| 🔵-4 | 采纳：「先建期」拦截写死在 review 层 reject（不弹卡），实现层同文案双保险；`run_pipeline` 函数本体不拦（终端 `/run` 无期调用的既有语义与用例不动） | §3.4 |
| 🔵-5 | 采纳：TA-1 写明 extras 前提；TA-5 改「任意调用路径下」 | §9 |
| 🔵-6 | 采纳：R4 实测落点写进 §3.4，库级事件少字段一项**接受**（idea 本就不落盘，库级 `approval_resolved` 事件已够追溯）；`write_memory` 的 `cite` 在 idea 下报错属正确语义 | §3.4 |
| 🔵-7 | 采纳：R2 判断列补审计面残余 | §5 R2 |
| 🔵-8 | 采纳：施工顺手改 `desktop/e2e/sessionReal.spec.ts` 过期注释 | §10 PR1 |

## 定向复审（2026-10-07，D43-R2；复审人 = D43-R 红队 session，未参与 v0.1 立文与 v0.2 修订；只审文稿，未改仓库代码）

**裁决：🟢 可动工**（13 条全部核销，另有 6🔵 由 D43-B 施工时吸收，在施工回填里逐条写清处置；不需要人重新拍板）。复审对象为 v0.2（`726f5cb`）与措辞收口 `81598a8`。

**核销**（逐条去回应表指向的正文核对，并在 scratchpad 原型上实跑）：

| 编号 | 结论 | 依据 |
|---|---|---|
| 🟡-1 | ✅ | §3.4 表、拦截机制段，§8 Q4，§9 TA-6 与 MUT-A11 都已落地；原型上 MUT-A11 被 TA-6[acquire_propose] 杀死 |
| 🟡-2 | ✅ | TA-6 的 write_memory 腿改为完整回环，另加 TA-6b；原型上 MUT-A8 被 TA-6b（两个 scope）杀死，TA-6 回环腿也一起变红 |
| 🟡-3 | ✅ | 已加 TA-3b 与 MUT-A9；原型上 MUT-A9 被 TA-3b 在三个 scope 下全部杀死 |
| 🟡-4 | ✅（另有新漏项，见 R2-2） | §7 (a)–(e) 与 ADR-0023 L76 的原文逐行 grep 都命中；§3.5 的 grep 范围已扩到整个 `docs/` |
| 🟡-5 | ✅，部分采纳的理由成立 | M24 退役、M29 改锚已写进 §9。M20 改锚、不退役：`review_tool_call` 去掉「未注册」两行后，未注册名字会在 `TOOL_SCHEMAS[name].get("side_effect")` 处抛 KeyError，`run_tool_loop` 兜底走 `_stop("error")`，整个回合中止，不再是「拒收并回喂拒因」，所以这两行是现役护栏。改锚后的杀手是 `test_m20_unregistered_tool_rejected_no_card`（断言回喂拒因）；`test_m20_out_of_scope_tool_rejected_no_card` 按 §9 改写 |
| 🔵-1 | ✅ | 改写轴与清单已写进 §9；`81598a8` 剔除 test_run_pipeline_pinning 的理由由原型实测确认（函数本体不拦时，该文件全绿） |
| 🔵-2 / 🔵-3 / 🔵-4 / 🔵-5 / 🔵-6 / 🔵-7 / 🔵-8 | ✅ | 分别落在 §3.1、§3.3、§3.4、§9、§3.4、§5 R2、§10 PR1，正文措辞与回应表一致 |
| 预检残余 ①（`81598a8` §9） | ✅ 核销 | 见 🔵-1 行 |
| 预检残余 ②（`81598a8` §3.4） | ✅ 核销 | 实测 13 个工具的 `side_effect`：有副作用的 6 个是 write_episode_file / run_pipeline / acquire_propose / browser / write_memory / cover_edit；减去 4 个需期工具，剩 write_memory 与 browser，措辞准确 |

**v0.2 新增面的攻击结论**：

- **① §3.4 拦截无缺口**。逐条核过 idea 下以 `episode_dir=None` 触发抓取卡或执行 `run_pipeline` 的路径：终端 idea REPL 没有 `/run`（`cli.py` idea 分支每一行都进 agent 回合）；`/run` 只在期 REPL 里（`cli.py` 主 REPL）和裸形态 `ava <期> /run`（需要先解析期目录）；协议层 idea 会话忽略 scope override（`protocol.py::_run_turn`：`idea = ep_dir is None` 时 override 置空）；idea 会话 `persist=False`，没有 `--continue` 可恢复；抓取卡唯一来源是 `llm.py` 在 `acquire_propose` 成功后调 `post_execute`，review 层与实现层都拦住了这一步；裸循环（`control is None` 且 `approve is None`）绕过 review，但实现层双保险覆盖全部 4 个工具（原型 TA-6 的 `execute_tool` 腿）。`run_pipeline` 函数本体不拦以后，剩下的无期调用只有 review 的 dry-run（`confirmed=False`，零副作用）和内核 `_fetch_executor`（已不可达）。「scope 切换中途建期」属于 D42 的范围，建期后 `ep_dir` 不再为 None，需期工具本来就该放行。
- **② 批准落点「接受」对追溯够用**，有两个边界要写明（R2-5）。
- **③ TA-3b / TA-6 / TA-6b 都能写成断言**：原型 10 例在 v0.2 实现上全绿，MUT-A8～A11 各自被指定用例杀死，还原后 md5 对拍一致。MUT-A9 是插入型变异、A10 / A11 是删除型，代码写出来后都能取到逐字唯一的锚点。§9 只要求登记、没钉锚点原文，这是对的，施工前写不出来。TA-1 / MUT-A1 有问题，见 R2-3。
- **④ 重新 grep 整个 `docs/`**：(a)–(e) 与 ADR-0023 都在，另有新漏项（R2-1、R2-2）。

| 编号 | 指控 | 证据 | 建议 |
|---|---|---|---|
| 🔵 R2-1 | **Spec 7 的 T17 不变量与 Q3 直接冲突，§9 没点名，施工时容易被反向「修绿」**。`test_agent_tools.py::test_write_memory_registered_creative_only_with_adr` 末段的不变量是「挂了 write_memory 的 scope 必须都在 `memory.scopes` 里（看得见才写得动）」（Spec 7 §2.8、二轮 🟡-A、Spec 7 内部编号 MUT-45）。单表之后每个 scope 都挂 write_memory，而 Q3 已裁决 pipeline 不注入，这条必然变红。让它变绿最顺手的两种改法都错：往 `memory.scopes` 里加 pipeline 会推翻 Q3，并违反 §4「不改记忆注入范围」；直接删掉又违反「不许只删」 | `tests/test_agent_tools.py:685-691`；`archive/2026-09-23-memory-and-model-tiering-spec.md` L62、L320、L391-397、T17 行；ADR-0023 补记 L80「pipeline scope 仍不单独注入」 | §9 改写要点补一句：T17 不变量按 Q3 退役，换成等强断言「`memory.scopes` 恰为 {creative, asset, idea}，不含 pipeline」，Q3 往哪边漂都会被拦；§7 补列 Spec 7 上述各处，加修订注记 |
| 🔵 R2-2 | §7 还漏三处「某模式不可见/只挂 creative」的原文 | `archive/2026-09-23-acquire-propose-spec.md` L119「idea scope 不加……扩可见性须另立 ADR」、L149（scope 闸表）；`archive/2026-09-26-web-fetch-links-and-research-strategy-spec.md` L145「write_memory 只挂 creative scope」；D42 选型稿 `2026-10-06-idea-to-episode-options.md` L24 说 `browser`「要期目录挂登录态 profile」，与代码不符（profile 来自 `web.json` 的 `browser.profile_dir`，`episode_dir` 只用于事件落点，见 `web_browser.py`） | 补进 §7；D42 选型稿按 §6 加注时把这句一起更正 |
| 🔵 R2-3 | **TA-1 / MUT-A1 在新签名下是空转的**。§3.1 把签名改成 `build_tool_schemas(root)`，不再收 scope，所以「四个模式 `build_tool_schemas` 输出逐字节相等」恒真；MUT-A1「对 idea 只返回旧 4 件」在函数体里没有 scope 可判断，按原文植不进去。全仓唯一调用点是 `llm.py::run_tool_loop` 的 `build_tool_schemas(context.scope, effective_root)` | `grep -rn "build_tool_schemas(" pipeline/` 只有 1 处 | TA-1 改在请求层断言：四个 scope 的 `ToolContext` 各跑一次 `run_tool_loop`（假 LLM），请求体里的 `tools` 逐字节相等；MUT-A1 植在 `llm.py` 调用点（如 `context.scope == "idea"` 时过滤），TA-1 杀 |
| 🔵 R2-4 | 🟡-1 (A) 带来一条估量清单外的改写：T13② 的子进程 `execute_tool('acquire_propose', ToolContext(scope='asset', root=root))` 没传期目录，实现层双保险会让它报「先建期」 | 原型实跑 `test_candidates_propose.py::test_tools_import_does_not_pull_candidates` 红，报错正文就是统一文案；同一份原型上 `test_run_pipeline_pinning`、`test_review` 全绿 | 改写时给这条用例补 `episode_dir`，保留「实调后按需加载 candidates、不拉 acquire」这条原断言；照「以施工前实跑为准」处理即可 |
| 🔵 R2-5 | §3.4「批准不静默丢」有两个边界没写：① idea 下卡被中断作废时，只有 `_record`，而它在 `persist=False` 时什么也不写，所以作废没有任何记录；② `log_approval_decision` 的事件写入包在 `except Exception: pass` 里，`ep_dir` 为 None 时又直接 return，库级事件一旦写失败（如外置盘掉线），y/n 都不留痕。write_memory 的 y 另有 `memory.log.jsonl` 一行（含 scope，episode 为 null），这一条可靠 | `status_card.py::log_approval_decision`（emit 吞异常；`if not ep_dir: return`）；`session.py::_record`（`if not self.persist: return`） | 作废的卡没执行任何操作，不丢证据；事件写失败与期级共用同一个吞异常点，属既有行为。§3.4 的「接受」后面补半句写明这两种情况，不改代码 |
| 🔵 R2-6 | 拦截顺序没写死：`review_tool_call` 里的「先建期」reject 必须排在 `run_pipeline` dry-run 之前，否则 idea 下一条本身非法的命令（如 `faces unknown`）拿到的是白名单拒因，不是统一文案。TA-6 用合法命令测不出这个差别 | `session.py::review_tool_call` 现在的顺序：未注册 → 越 scope → run_pipeline dry-run → write_memory dry-run | §3.4 拦截机制段补「排在未注册检查之后、一切 dry-run 之前」；TA-6 的 run_pipeline 腿加一个非法命令样例 |

**原型与探针**（都在 scratchpad 的 `d43r2/`，未进仓库）：`base/` 是 HEAD 导出的副本，相关的 22 份测试文件基线为 4 红（`test_agent_assembly_integration` 2 条、`test_golden_terminal` 2 条，副本缺 `data/` 等本机文件，与本 spec 无关）；`proto/` 按 v0.2 意图做最小改动：单表、反向检查、删越 scope 与三处实现内检查、合表、`NEEDS_EPISODE_TOOLS` 两层拦截。为了把语义失败和改名引起的 collection 错误分开，原型保留了旧名 `tool_names_for_scope` 作为别名。`test_d43r2_proto.py` 是 TA-3b / TA-6 / TA-6b 的原型，`d43r2_mut.py` 负责植入并还原 MUT-A8～A11、做 md5 对拍。逐文件实跑时 `test_agent_protocol` 的子进程因夹具 KeyError 逐条等超时（🔵-1 已知面），跑到该文件即中止，没有跑完全量。

## 0. 一句话

模式（`creative` / `pipeline` / `asset` / `idea`）只决定注入哪份提示与工序手册，**不再决定模型能用哪些工具**。全部 13 个工具在任何模式下都可见、可调用；工具实现里按模式拒绝的检查一并删除；与模式无关的护栏（写入文件白名单、路径防穿透、人审卡、出网断言、`cloud exec` 禁令、`--force` 禁令、Code Freeze）一条不动。

## 1. 人的裁决（2026-10-06）

- 人原话：「我建议不论什么模式，都开放所有 tool，具体原因我先不展开，这是我深思后的考虑」。
- 同日确认两点：① 工具实现里的模式检查**也一起去掉**（不是只改 `tools.json` 的可见清单）；② **人审卡全部照旧**。
- 动机由人保留，本 spec 不替人补写理由；§3.6 只记录这项改动在机制上的客观效果，供红队核对，不作为立项依据。

## 2. 现状：工具被三层东西按模式卡住

| 层 | 位置（以符号为锚，行号会漂） | 现状 |
|---|---|---|
| ① 可见清单 | `config/agent/tools.json`；`tools.py::tool_names_for_scope` / `build_tool_schemas`；`scopes.py::load_scope` | 四个模式各一份清单：creative 11 个、pipeline 4 个、asset 5 个、idea 4 个（全部 13 个工具 schema ≈ 7.1k 字；现各模式 1.2k–5.7k 字） |
| ② 调用前的白名单闸 | `tools.py::execute_tool`（「四层闸」第 ②层）；`session.py::review_tool_call`（「未注册/越 scope」先拒，不弹卡） | 模型调了本模式清单外的工具 → 直接返回「不在 X scope 白名单内」 |
| ③ 实现内的模式检查 | `tools.py::write_episode_file`（`scope != "creative"` → `PermissionError` 零写权限）；`memory.py::apply_op`（`scope != "creative"` → 没有记忆写权限）；`tools.py::validate_pipeline_command`（creative/pipeline 用 `PIPELINE_MODULES`，asset 用 `ASSET_COMMANDS`，其他 → 「未知的 Scope」） | 只改 ① 而不动 ③，模型调用会拿到 `PermissionError`，白白浪费轮次（人 2026-10-06 确认一起去掉） |
| 文案 | `config/agent/scopes/idea.md`（「写权限为零（机制保证，不是纪律）」「指引人退出并运行 `ava new`」）、`asset.md`（「只出提案不抓取」）、`pipeline.md`（「零直接文件写权限」）；`status_card.py`（idea 卡「写权限: 无（机制保证）」）；`cli.py` idea 横幅与帮助三处；`protocol.py` 注释 | 与新能力对不上，须同步改 |

不受模式影响、本 spec 不碰的：`assembly.json` 的常驻层 / 工序层路由与 `memory.scopes`（记忆注入范围）；`resolver.scope_of`（工序 → 模式推导）；桌面端「素材模式」切换（仍切提示）；模型分级（ADR-0023）。

## 3. 设计

### 3.1 可见清单：`tools.json` 收为一张表

- `config/agent/tools.json` 从「四模式各一份」改为**单一清单** `{"tools": [13 个工具名]}`；`tool_names_for_scope(scope, root)` 改名为 `tool_names(root)`（不再收 scope），`build_tool_schemas(root)` 同理；`scopes.load_scope` 不再读工具表（只管提示文件）。
- 理由：四份完全相同的清单会各自漂移，「哪个模式漏了一个」正是本次要消除的那类问题；单表让「工具全开」由结构保证，而不是靠四份配置碰巧一致。（备选：保留四键、内容相同——改动更小，但留着漂移口子。§8 Q1 人已选单表。）
- 保留现有「`tools.json` 写了未注册的名字 → 当场报错」的分叉检查（B3-r6），新增反向检查：`TOOL_SCHEMAS` 里注册了但 `tools.json` 没列的 → 同样报错（全开之后两边应恰好相等）。反向检查与正向检查同点同形态：都在 `build_tool_schemas` 里当场抛错（v0.2，🔵-2）。`tools.json` 缺失、损坏或残留旧四键格式时加载得空表，反向检查随即报错，fail-closed，「读取失败不静默扩张」语义保持。
- ADR-0025 的封顶（14，现役 13）按全局数量计，本 spec 不新增工具，不触发。

### 3.2 调用前闸：② 层变成「是否注册」

- `execute_tool` 第 ② 层与 `review_tool_call` 的「越 scope」拒绝删除；第 ① 层「名字已注册」保留（等价于在单表内）。
- `ToolContext.scope` 字段保留，只作**记录用途**（事件 payload、`approvals.jsonl`、browser 事件里的 `scope` 字段照写），不再参与任何放行判断。施工时 grep `ctx.scope` / `scope=` 全部调用点，逐个标注「记录」或删除。

### 3.3 实现内检查

| 工具 | 删掉的 | 保留的（与模式无关） |
|---|---|---|
| `write_episode_file` | `scope != "creative"` 的零写权限 | 文件名白名单 `CREATIVE_WRITABLE_FILES`（`01-topic.md`、`02-script.draft.md`、`07-titles.md`；常量改名 `EPISODE_WRITABLE_FILES`）、双端 resolve、禁写 `pipeline/`、禁越出期根、写 `01-topic.md` 必须人确认、`atomic_write`；`CRITICAL_TOOLS` 人审卡 |
| `write_memory` | `memory.apply_op` 的 `scope != "creative"` | 持锁 → 锁内重读重规划 → 卡闸 → 日志 → 原子写；人审卡 |
| `run_pipeline` | 按 scope 分派的两份白名单与「未知的 Scope」分支 | 合成**一份**放行表：`PIPELINE_MODULES` 的 9 个模块（不限子命令）∪ `ASSET_COMMANDS` 的 6 个模块与各自子命令清单；`--force` / `--force-all` 前缀禁令、`cloud exec` 永久禁令、`cloud run` 的 `validate_extra_args`、当期目录自动补位；人在宿主确认后才执行（`confirmed`）的既有流程。合表语义写死（v0.2，🔵-3）：(a) 模块属于 `ASSET_COMMANDS` 时子命令校验永远生效，将来两表若出现重名模块，以子命令限制为准；(b) 当期目录自动补位只对原 `PIPELINE_MODULES` 侧的 8 个模块生效，asset 侧 6 个模块不补位（维持现状） |
| 其余 10 个 | 无实现内模式检查（`browser` / 记录类 `scope` 参数保留为记录） | 全部照旧：`assert_egress_boundary`、`_guard_url`、`_scrub` / `_redact_secret`、读域、`browser` 人审卡 + 原生确认框、`acquire_propose` 只写候选池 |

`validate_pipeline_command` 的 `scope` 参数删除；`create_job` / `run_pipeline` 的 `scope` 参数降为记录字段（`jobs.py` 的 `Job.scope` 仍写入，值取当时会话模式）。

### 3.4 没有期目录时（idea 会话）

idea 会话没有期目录。工具全部可见，但凡需要期目录的调用，统一返回同一条错误：「当前没有期目录：这一步要先建期（桌面端「＋ 新建一期」/ 终端 `ava new <名>`）」，不藏工具、不弹卡。逐个：

| 工具 | idea 下的行为 |
|---|---|
| `write_episode_file`、`cover_edit` | 返回上述错误（`cover_edit` 现已有 `if not ctx.episode_dir` 分支，改用同一文案） |
| `run_pipeline` | 返回上述错误（§8 Q2 人已裁决） |
| `acquire_propose` | 返回上述错误（v0.2，🟡-1，人 2026-10-07 裁决 (A)）。原因：`acquire_propose` 成功后内核无条件弹抓取卡，批准后的执行器以 `episode_dir=None` 调 `run_pipeline("acquire fetch N")`，与 Q2 冲突；放行提案、不弹卡又会留下无人认领的孤儿候选。抓素材为某一期服务，建期后再提 |
| `read_status` 不带期名 | 现状已报「未指定期，且当前会话未绑定期目录」，照旧 |
| `write_memory` | 放行（记忆是库级，`apply_op` 已接受 `episode_dir=None`）；过人审卡。例外：`op=cite` 需要期目录，`episode_ref_of(None)` 报错，属正确语义（v0.2，🔵-6） |
| `web_search` / `web_fetch` / `crawl` / `browser` | 放行（`browser` 过人审卡 + 原生确认框） |

**拦截机制写死（v0.2，🔵-4）**：
- 主闸在 `review_tool_call`：`ep_dir is None` 且工具 ∈ {`write_episode_file`, `cover_edit`, `run_pipeline`, `acquire_propose`} → 在弹卡**之前** `reject`，理由即上述统一文案。四个工具的集合写成一个常量（如 `NEEDS_EPISODE_TOOLS`），review 层与实现层共用。
- 双保险在实现层：`_tool_write_episode_file`、`_tool_cover_edit`、`_tool_run_pipeline`、`_tool_acquire_propose` 在 `ctx.episode_dir is None` 时抛同一文案。
- `run_pipeline` **函数本体不拦**：终端 `/run` 与既有用例（如 `run_pipeline("check_script", scope="pipeline", confirmed=True)` 无期调用）的语义不动；内核 `_fetch_executor` 也不改。idea 下抓取卡不会出现，因为它的唯一来源 `acquire_propose` 已被拦。

**批准记录落点（v0.2，🔵-6，红队探针实测，接受）**：idea 下仍会弹的**工具批准卡**只有 `write_memory` 与 `browser` 两种（检查点卡等非工具卡照旧，不在本表范围）。`ep_dir is None` 时 `log_approval_decision` 把 `approval_resolved` 事件写到库级 `data/_events.jsonl`，期级 `approvals.jsonl` 不产生；idea 会话本身不落盘（`persist=False`）。库级事件比期级记录少 latency / channel / request_id 字段，**接受**：idea 的批准不静默丢，追溯靠库级事件已够；补齐另立文。

### 3.5 文案同步

- `idea.md`：删「写权限为零（机制保证）」；产出与落盘一节改为「需要写 `01-topic.md` 时先请人建期」（D42 落地后再改为「建期后在同一会话里写」）。
- `asset.md`：「只出提案不抓取」改为「素材下载只出提案（`acquire_propose`），实际下载由人逐条批准后走 `pipeline.acquire`」——把「不抓取」限定在下载素材上，避免被理解成不抓网页（S13-G8 附带观察，未做 A/B，只是消除歧义）。
- `pipeline.md`：「零直接文件写权限」改为「写期文件只走 `write_episode_file`（白名单三份文件、过人审卡）」。
- `status_card.py` idea 卡：「写权限: 无（机制保证）」改为「期目录: 无（写期文件前须先建期）」。
- `cli.py` idea 横幅 / 帮助 / docstring 三处与 `protocol.py` 注释同步。
- **不改** `AGENTS.md`（常驻规则不涉及按模式分工具；140 行预算不动）。施工时 grep `AGENTS.md` 与 `docs/` 全仓（含 `archive/`），口令「零写\|写权限为零\|只对.*可见\|永不见\|超 scope」，有则列入修订面（v0.2，🟡-4：v0.1 只 grep 了 `docs/runbook/`，漏了五处，见 §7）。

### 3.6 客观效果（供红队核对，不是立项理由）

- 每次请求的工具 schema 恒为 ≈ 7.1k 字：creative 多 ≈ 1.4k，asset 多 ≈ 4.4k，pipeline / idea 多 ≈ 5.9k。S13-G8 实测 asset 会话一轮 `prompt_chars` ≈ 2.1 万字，增量占比可见但不大。
- 自动模式下同一会话随工序推进会换模式（03→04 进 pipeline），此前工具表随之变化；全开后工具表在会话内恒定。
- 单会话可见工具数从 4–11 变为 13，ADR-0025 推翻条件里「各 scope 可见工具都 ≤ 8 → 可按 scope 设封顶」那条论证路径随之消失（不影响现行全局封顶 14）。

## 4. 不做的事

- 不新增工具，不改任何工具的 schema、参数取值域与返回契约。
- 不删、不放宽任何与模式无关的护栏（§3.3 右列全部保留）；`CRITICAL_TOOLS` 集合不变。
- 不改记忆注入范围（`assembly.json` 的 `memory.scopes` 仍是 creative / asset / idea）；pipeline 模式下可调用 `write_memory` 但不预先注入记忆全文，见 §5 R3。
- 不改模式推导（`scope_of`）与提示装配；不改桌面端（施工时全量 e2e 验证无回归即可，若发现桌面端有按模式过滤工具的代码再停下报人）。
- 不顺手做 D42 的「建期时迁移选题会话」。

## 5. 风险与攻击面（给红队）

| # | 风险 | 本 spec 的判断 | 请红队核 |
|---|---|---|---|
| R1 | pipeline 模式此前「永不见网络工具」（ADR-0021）。03 / 03.5 等工序的会话上下文里有期内产物路径，开放网络工具后出网面变大 | 出网断言 `assert_egress_boundary` 对 LLM 请求体与 web 四个出方向都照旧生效（Spec 16 豁免只针对可信规程原文），与模式无关 | 构造 pipeline 模式下把 `03-audio/manifest.json` 内容或受限文件名带进 `web_search` query / `web_fetch` URL 的最坏样例，确认仍被拦 |
| R2 | 非 creative 模式能写期文件：例如 05 返工期间改写 `02-script.draft.md` | 白名单只有三份文件，全部过人审卡；`02-script.md` 本就不在白名单；`status.py` 在 `02-script.md` 存在时不看草稿，工序不会倒退。v0.2（🔵-7，红队读码确认）：02.5 闸 `_gate_valid` 只比 `02-diff.patch` 与 `02-script.md` 的 mtime，人时 `human_time.json` 只增，都不受影响；唯一残余是审计面——封板后改写草稿，`02-diff.patch` 的左半原文无法再由两份文件重现（patch 内文仍在），接受 | 核实封板后改写草稿是否影响 02.5 的 diff / 人时统计等任何下游判定 |
| R3 | pipeline 模式没注入记忆全文，模型可能在没读过记忆时调 `write_memory` | 写入走「锁内重读重规划」，且返回写后全文；不读而写最坏是写出重复或冲突条目，人审卡兜底 | 判断是否应把 pipeline 加进 `memory.scopes`（会改 ADR-0023 补记的「pipeline 不单独注入」），或接受现状 |
| R4 | idea 无期路径：人审卡记录落点、`run_pipeline` 作业落点、抓取卡链 | v0.2：§3.4 已写死——四个需期工具 review 层拒、实现层双保险；批准落点按红队实测接受 | 逐个工具实跑 idea 下的调用，确认没有静默丢记录或写到意外位置 |
| R5 | `run_pipeline` 合表后，creative / pipeline 模式也能提议 `cloud up` / `cloud run`（花钱）与 `faces` / `shots` 等库级重活 | 执行一律要人在宿主确认；`cloud exec` 与 `--force` 禁令不变 | 核 `cloud down` 在作业运行中被提议的提示是否足够 |
| R6 | 13 个工具全量可见，模型选错工具的概率上升 | 不在本 spec 内设防（人裁决的取舍）；施工后观察 | 无（列出即可） |

## 6. 与 D42 的关系

D42 人选方案 (a) 含两半：① idea 补联网三件（`web_search` / `web_fetch` / `crawl`）；② 建期时把选题会话记录带进新期。本 spec 落地后 ① 自动成立（且更宽：idea 全部工具可见），D42 的完整 spec 只剩 ②，范围相应缩小；D42 行与选型稿须加注。两者施工都动 `tools.json` 与 `idea.md`，**本 spec 先施工**，D42-B 在其后。

## 7. 冻结面影响与文档修订面

施工同一 PR 内修订（archive 原文加修订注记：日期、D43、人裁决原话）：
- ADR-0021：「网络工具只对 asset scope 与 creative scope 可见；pipeline scope 永不见网络工具」与「工具表保持静态注册、scope 过滤」→ 改为「静态注册、不按 scope 过滤（ADR-0027）」。
- ADR-0025：§2「`cover_edit` 只对 creative scope 可见」「pipeline scope 永不见它是 scope 分组授权的既有纪律」与「不改动任何既有工具的 scope 归属」加注被 ADR-0027 取代；封顶数字不动；推翻条件里依赖 scope 分组的那句加注。
- ADR-0023：`write_memory` 只挂 creative 的表述加注。
- Spec 10 §2.5：idea「零写权限」改为「无期目录，写期文件前须先建期」。
- impl spec §2.4 / §2.5 B3-r6：scope 白名单语义改为单表。
- Spec 4（`archive/2026-09-23-network-tools-spec.md`）与 Spec 12（cover）中「仅 X scope 可见」的句子：施工前 `grep -n "scope" ` 逐份核全，列表回填 §10。
- v0.2 补列（🟡-4，红队逐行核对）：
  - (a) `docs/CHEATSHEET.md`：「idea scope，写权限为零」一行；「未注册/超 scope 的工具在弹卡之前就被拦下」一行改为「未注册的工具、无期会话里需要期目录的工具在弹卡之前就被拦下」；
  - (b) `archive/2026-09-21-ava-entry-idea-scope.md`（D27，idea 零写机制的立规文件）：§2.2「写权限为零不靠 prompt 劝导：`write_episode_file` 不在表内……」等多处，加修订注记；
  - (c) `archive/2026-09-25-agent-session-protocol-spec.md`（Spec 9）：§2「idea 会话不落盘：无期目录可挂，零写权限」与 TP-16 行「零写权限」两处；
  - (d) `archive/2026-09-20-ava-ai-native-director-spec.md`：「pipeline scope 看不到 `write_episode_file`——制片期零写权限」一处；
  - (e) impl spec：文首 ①「idea scope，写权限为零」、§2.3「ava idea……只读 4 工具，写权限为零」、§2.4「pipeline: `{}` 零写权限」与 §1.2 目录注释；
  - ADR-0023：「idea 零写权限（机制保证），写入工具仍只挂在 creative」整行加注（不止 `write_memory` 半句）。
- 新 ADR-0027 转「已通过」。

## 8. 待人裁决（Q1–Q3 人 2026-10-06 裁决全部按建议；Q4 人 2026-10-07 裁决 (A)；均不再争议）

| # | 问题 | 建议 |
|---|---|---|
| Q1 | `tools.json` 收成单表（改函数签名，测试改动面大一些），还是保留四个键、内容全相同（改动小，但留漂移口子） | **单表**（§3.1）——✅ 人裁决采纳 |
| Q2 | idea 会话里 `run_pipeline` 怎么处理：统一报「先建期」，还是放行库级命令（`ingest phase0`、`cloud status` 等）并补无期记录落点 | **统一报「先建期」**（§3.4）；以后真有无期跑库级命令的需求再立文——✅ 人裁决采纳 |
| Q3 | pipeline 模式要不要也注入记忆全文（§5 R3） | **不加**，维持 ADR-0023 补记；人审卡兜底——✅ 人裁决采纳 |
| Q4（v0.2，红队 🟡-1） | idea 下 `acquire_propose` 怎么处理：(A) 也报「先建期」/ (B) 放行但无期不弹抓取卡 / (C) 抓取链无期转正 | **(A)**——✅ 人 2026-10-07 裁决采纳（§3.4） |

## 9. 测试与变异（施工时按实现跑出期望值再写断言）

**改写**（被删语义的旧断言换成等强的新断言，不许只删）。v0.2（🔵-1）：红队在按本 spec 意图做的最小原型上实跑全量 pytest，失败约 90 条、分布于 ≥ 15 份文件，绝大多数同根因。**改写轴**：先改共享夹具与签名替身（写四键 `tools.json` 的 `make_agent_root` / `SPEC_TOOLS`、`_agent_root`、`_make_web_root`、golden / protocol 夹具；`test_agent_llm_tiering` 对旧签名的 monkeypatch；7 份文件导入旧名 `tool_names_for_scope` / `CREATIVE_WRITABLE_FILES` 的 collection 错误），再按实跑失败列表逐条收尾。夹具 `tools.json` 必须与该测试注册的假工具（`test_ping` / `test_slow` / `test_stdin_child` / `test_writer` 等）同步扩张，否则反向检查当场报错（🔵-2）。红队实跑分布（供估量，以施工前实跑为准）：test_agent_tools（约 24）、test_agent_protocol（19）、test_agent_pr6（13）、test_agent_loop（12）、test_golden_terminal（12）、test_agent_llm_tiering（7）、test_agent_web（5）、test_agent_crawl_browser（4）、test_agent_session（4 + 10 夹具 error）、test_agent_director（3）、test_agent_assembly_integration（2）、test_agent_memory（2）、test_candidates_propose（2）、test_review（1）。（红队原型清单里另有 test_run_pipeline_pinning（1），那是原型在 `run_pipeline` 函数本体拦无期调用所致；v0.2 定为函数本体不拦，该条预期不失败。）语义改写要点：`test_agent_tools.py` 零写权限两条 → 「pipeline / asset 模式写白名单文件成功、写白名单外仍拒」；`validate_pipeline_command` 按 scope 的若干条 → 合表后同等覆盖；「tools.json 已漂移」的逐 scope 精确断言 → 单表等于注册全集；T6 idea 表等于 4 个只读工具 → 全集 + 无期报错；`test_agent_pr6.py` M20 → 「未注册名字」拦截；`test_agent_memory.py` idea 写记忆被拒 → 过卡落盘；`test_agent_director.py` 逐回合工具表断言 → 各回合相同。逐条在施工回填里写「改成了什么」。

**新增**：
- TA-1：四个模式 `build_tool_schemas` 输出逐字节相等，且名字集合 == 注册集 ∩ 可选依赖可用集（`build_tool_schemas` 对缺 crawl4ai / playwright 的工具跳过；施工机两者都装，断言写成交集形式，v0.2 🔵-5）。
- TA-2：`tools.json` 少列一个已注册工具 → `build_tool_schemas` 抛错；缺失 / 损坏 / 旧四键格式三态同样抛错。
- TA-3：pipeline / asset / idea 三个模式下 `execute_tool` 调 `web_search`（打桩）都执行，不返回白名单错误。
- TA-3b（v0.2，🟡-3）：pipeline / asset / idea 三个模式下 `review_tool_call` 对 `web_search`、`write_memory`（`op=add`）的裁决都不是 `reject`；pipeline / asset 模式下对 `run_pipeline("cloud status")` 不是 `reject`。
- TA-4：asset 模式 `write_episode_file("02-script.draft.md")` 过卡后落盘；写 `02-script.md` / `../x` / `pipeline/x` 仍拒；`01-topic.md` 未确认仍拒。
- TA-5：任意调用路径下（v0.2，🔵-5）`validate_pipeline_command("cloud status")` 通过；`cloud exec`、`tts --force-a`、`faces unknown` 仍拒。
- TA-6：idea 会话调 `write_episode_file` / `cover_edit` / `run_pipeline` / `acquire_propose`（v0.2，🟡-1）→ `review_tool_call` 返回 `reject`、理由为「先建期」统一文案、不弹卡、零写入（`incoming` 候选池不变）；直调 `execute_tool` 同样得到统一文案（实现层双保险）。`write_memory` 腿（v0.2，🟡-2）为完整回环：弹卡 → 人按 y → `memory.md` 出现该条、`memory.log.jsonl` 新增一行且 `episode` 为 null。
- TA-6b（v0.2，🟡-2）：`memory.apply_op(..., scope="pipeline", confirmed=True)` 与 `scope="idea"` 都成功落盘。
- TA-7：pipeline 模式下 `web_fetch` 的 URL 含 `agent.local.json` → 仍被出网断言拦截（R1 的回归守卫）。

**变异**（每条须由指定用例的断言杀死；`PYTHONDONTWRITEBYTECODE=1`，还原 md5 对拍）：
- MUT-A1：`build_tool_schemas` 对 idea 只返回旧 4 件 → TA-1 杀。
- MUT-A2：删掉反向分叉检查 → TA-2 杀。
- MUT-A3：`write_episode_file` 恢复 `scope != "creative"` 检查 → TA-4 杀。
- MUT-A4：合表时漏掉 `ASSET_COMMANDS` → TA-5 杀。
- MUT-A5：合表时把 `cloud exec` 禁令挪进某个分支后丢失 → TA-5 杀。
- MUT-A6：idea 下 `run_pipeline` 在 review 层不拒 → TA-6 杀。
- MUT-A7：web 工具在 pipeline 模式跳过 `assert_egress_boundary` → TA-7 杀。
- MUT-A8：`memory.apply_op` 恢复 creative-only → **TA-6b 杀**（v0.2，🟡-2：TA-6 原措辞只到弹卡，走的是不含 scope 闸的 `plan_op`，杀不死）。
- MUT-A9（v0.2，🟡-3）：`review_tool_call` 保留越 scope 拒绝 → TA-3b 杀。
- MUT-A10（v0.2）：review 层「先建期」拒绝删掉（只剩实现层）→ TA-6 的「不弹卡」断言杀。
- MUT-A11（v0.2，🟡-1）：`acquire_propose` 从需期工具集合里漏掉 → TA-6 杀。

**shipped 变异矩阵（v0.2，🟡-5）**：`scripts/verify_mutations.py` 在 PR1 同一提交内同步，否则元守卫 `test_anchors_in_shipped_matrix_are_unique_in_repo` 当场红：
- M24（idea 四键工具表零写权限）：守的语义被本 spec 废除 → **退役**，条目删除并在注释里写「D43 / Spec 17 废除」；
- M20（未注册 / 超 scope 预校验）：**改锚，不退役**——「未注册名字在弹卡前拒绝」仍是现役护栏，`old` 缩到剩下的那段，`guard` 改为「未注册工具预校验」；
- M29（idea 卡纯静态）：锚点随 §3.5 新文案更新；
- MUT-A1～A11 全部登记为新条目，各配逐字唯一的 `old` 锚点。

## 10. PR 划分、验证与门禁

- PR1（core 一个提交）：`tools.json`、`tools.py`、`scopes.py`、`memory.py`、`session.py`、`status_card.py`、`cli.py`、`protocol.py` 注释、三份 scope 提示、测试、`scripts/verify_mutations.py`（§9 shipped 矩阵段）、`desktop/e2e/sessionReal.spec.ts` 过期注释「acquire_propose 只在 asset scope」（v0.2，🔵-8；只改注释）；PR2（文档）：§7 全部修订面 + ADR-0027 状态。
- 验证：全量 `uv run pytest` 全绿；`cd desktop && npx vitest run`、全量 `npx playwright test`（`workers: 2`，临时副本）全绿；§9 变异逐条回填。
- 门禁：① §9 新增用例与改写用例全绿，改写逐条可追溯；② 变异 MUT-A1～A11 共 11 条全杀，且已登记进 shipped 矩阵、`verify_mutations.py --only` 逐条可复跑；③ 真会话冒烟（临时仓库副本、真 LLM）：pipeline 模式会话里让模型查一条网页资料，确认 `web_search` 可用且出网断言照常；idea 会话里让模型写 `01-topic.md`，确认返回「先建期」而不是白名单错误；同一会话里让模型提一条素材候选，确认 `acquire_propose` 同样返回「先建期」、`incoming` 池不变；④ §7 文档修订面完整。
- 施工回填（D43-B 写）：测试失败清单与改写对照、变异回填表、偏差。
