# Implementation Spec：停机点深度组件（Spec 11：02.5 app 内编辑与封板、03.5 顺听按钮、人时采集）

日期：2026-09-26（**v0.3**，红队一轮修订（2🔴 + 7🟡 全收、🔵 9 条全收或部分收）+ 二轮定向复审修订（3🟡 + 1🔵 全收），逐条裁决见 §1.1/§1.2；状态：**v0.3 红队三轮 🟢，可动工**（第三轮定向复审 2026-09-26 闭环，唯一残留 ADR 注记版本号失实已随手修正；S8-R17 改写措辞仍待人最终确认））  
上位文档：`docs/dev/plans/2026-09-22-harness-evolution-direction.md`（§0 产品画像与终态判据、§4 施工红线八条、§5 明确排除、§6 Spec 11 范围全文）  
相关 ADR：**ADR-0024（桌面端写产物，`docs/dev/adr/0024-desktop-artifact-writes.md`，状态「已通过」——2026-09-26 用户接受；PR2–PR5 以其为前置，已满足）**、ADR-0018（保留条款）、ADR-0019（corrections 生命周期）、ADR-0020（§3 审批对象、§4 桌面端、§5 Context 纪律）  
契约依赖：**Spec 8**（已施工，`desktop/` 代码即现状，文档 `archive/2026-09-23-electron-desktop-spec.md` v0.5）；Spec 3（已施工，`pipeline/approvals.py`）；Spec 2（已施工，`pipeline/jobs.py`）；Spec 9（v0.7 红队 🟢、未施工——本 spec **不消费其协议进程**，仅落实其 RF-17 的遗留处置）；Spec 10（v0.5 红队 🟢、未施工——本 spec 与其无修订关系，边界见 §6.2）  
对应 issues：D18、D19（archive.md 2026-09-25 拍板「不立判据、走替代处置」，两个落点并入本 spec）  
格式范本：Spec 2、Spec 3、Spec 8（`archive/`）  
撰写基线：HEAD `5baf6a0`；`uv run pytest` 1715 passed、`cd desktop && npx vitest run` 121 passed（均 2026-09-26 实跑）；`uv run pytest tests/test_docs_invariants.py` 11 passed

**用户裁决记录**

| 日期 | 事项 | 裁决 |
|---|---|---|
| 2026-09-26 | 02.5 编辑器选型 | **CodeMirror 6 + 既有 markdown-it 预览**（否决纯 textarea 与 WYSIWYG） |
| 2026-09-26 | 03.5 纠错范围 | **含自由文本纠错输入框**，复用终端同一文法与确认卡语义；划词注音仍后置 |
| 2026-09-26 | 人时口径 | **审阅面可见即计时，墙钟，失焦照算**（与终端 REPL 停留口径一致，两端可比） |
| 2026-09-26 | 零改动封板 | **保持现状：diff 为空不可封板**，按钮禁用并说明；不改 approvals 闸门格式 |
| 2026-09-26 | ADR-0024（桌面端写产物） | **接受**（ADR 状态转「已通过」） |
| 2026-09-26 | §6.1 全部修订请求（S8-R13~R17、RF17-C1、D-R1） | **授权**（「接收；授权」）；S8-R17 在 v0.2 按红队 🟡-5 改写前置条件处置（§1.1 🔴/🟡 表），改写后的措辞随本轮定向复审一并呈人确认 |

**作者自报的未实测假设**（红队复审优先攻击面；正文相应处已标「约」）

| 编号 | 假设 | 状态 / 退路 |
|---|---|---|
| A1 | 钉死的 CodeMirror 6 五包（§5.1）在 Electron 44 renderer 正常装载与运行 | 版本号经 `npm view` 核实存在（2026-09-26）；运行时未测。退路：PR3 首日实测，失败则降级为原生 textarea（依赖白名单随之清空） |
| A2 | `tts --apply-patch` 的实际时长分布（决定 RUN_TTS_APPLY_PATCH 模板无超时是否可接受） | 未测（需真实纠错条目触发重配）。退路：模板加 30 分钟超时；PR4 实测回填 |
| A3 | 打包 app 的 spawn 链路（固定 PATH 白名单 + `/opt/homebrew/bin` 扩展）下 `git diff --no-index` 与 `ffprobe` 可用 | 部分实测：`env -i PATH=/usr/bin:/bin` 下 `git diff --no-index` 正常（2026-09-26，scratchpad）；`/opt/homebrew/bin` 扩展未在打包版端到端测。`/opt/homebrew/bin` 是 arm64 macOS 惯例路径，写死是本机事实（如实声明） |
| A4 | `human_time.json` 被 host 直读与 core `atomic_write` 并发安全 | 未单独实测；与 approvals_store 直读同一模式（整文件 `os.replace`，读端只见旧版或新版，Spec 8 §2.3 既有论证） |
| A5 | `ava-media://` 对 `02-script.md` 的文本读取与编辑器装载的时序（host stat 指纹先于 renderer fetch 内容，之间存在被改窗口） | 未测；后果仅为保存时指纹不符拒存（安全方向），不纠正 |

---

## 0. 一句话设计

**镜子还是镜子，只是上面多了六个按钮和一支笔：笔的每一次落墨都由人点击发起、经 core 的既有纪律落盘；按钮不调模型，语义与终端 `/voice` 一一对应。**

02.5：PreviewPane 的 `.md` 预览旁长出 CodeMirror 编辑器，保存经 `ava <期> /save-script`（stdin 传正文、指纹不符拒存），封板经 `ava <期> /seal-script`（core 内跑既有 `git diff --no-index`、空 diff 拒封），批准仍走 Spec 3/8 既有 ack 链路。03.5：段落列表 + 播放走 renderer 原生 `<audio>`（不用 afplay/QuickTime），`回滚/撤回/纠错/done` 各对应一个确定性 core 子命令；done 的增量重配复用既有 `/run tts --apply-patch` 白名单通道，成为桌面端第一个长任务 spawn（Spec 8「v1 不拉起长任务」的受控开口）。人时：审阅面可见即计时、墙钟、失焦照算，经 `ava <期> /record-time` 落入既有 `human_time.json`（复用 `record_human_time` 同一函数），Spec 8 门禁 14 的「不计人时」横幅随之退役。check_script 增 INFO 级人物提示行（只报不拦），02.5 人审要点清单增两条并随编辑器呈现。全部写能力以 ADR-0024 为依据；host 对 `data/` 直写禁令（I1）不变。

---

## 1. 红队裁决与修订纪要

### 1.2 第二轮红队定向复审裁决与修订纪要（v0.2 → v0.3，3🟡 + 1🔵 全收；原裁决「🟡 修订后定向复审（第二轮）」）

复审确认一轮 2🔴 与大部 🟡/🔵 已闭环（含 🔴-1 期望值逐字吻合与跨种子确定性独立复现、🟡-2 的 `parseLossless` 与 `SELF_CHECK_TEXT` 夹具值同源核实、`cli.py:606/616` 的行号更正被红队反验为作者对）。四处新发现全部属实，根因同一类：**v0.2 修订期两批 edit 调用因单个 oldText 不匹配整批回滚，作者未逐项回验落点**，导致「裁决表声称已修、正文/ADR 未改」两处假闭环（二轮 🟡-1/🟡-2）。教训已记录：此后每批 edit 后必须回读落点再报。

| 编号 | 红队指控 | 独立复核 | 裁决 | 修订动作 |
|---|---|---|---|---|
| 🟡-1 | 一轮 🟡-3 的 I2 闭集补全未落到 §2.1 与 ADR-0024 §3，门禁 1 的新断言建在没补的闭集上 | **属实**（§2.1 line 81 与 ADR line 43 仍为旧枚举；门禁 1 按旧闭集必红） | **采纳** | §2.1 I2 与 ADR-0024 §3 实际写入完整枚举（本版）；§1.1 🟡-3 行的修订动作如实补记漏落经过 |
| 🟡-2 | ADR-0024 状态字段根本没改（frontmatter 仍 `proposed`、正文仍「提议」），与 spec 头部/门禁 0 矛盾 | **属实**（ADR line 4/10 原样；同一批回滚的受害者） | **采纳** | ADR frontmatter 与正文状态行改「已通过（2026-09-26 用户接受）」；§3 I2 枚举随同次编辑补齐（上条） |
| 🟡-3 | 门禁 4 仍要求「MUT-8 被捕获」，MUT-8 已删 | **属实**（§9 门禁 4 原文比对） | **采纳** | 门禁 4 改为「TC-8 的 `/voice-add` 原文重解析契约断言成立（替代 MUT-8）」 |
| 🔵-1 | 行号修订漏三处：§2.4 仍引 `cli.py:83`、§3.2 仍引 `cli.py:379`、补核表 `_gate_valid` def 写 313 | **属实**（实测：85、377、312） | **采纳** | 三处全部改正 |

**下一轮（第三轮）定向复审范围（建议限定）**：本轮四处修订点（§2.1 I2 枚举、ADR-0024 状态与 §3、门禁 4 措辞、三处行号）+ 附表「v0.2 补核」未抽查行。其余内容两轮已闭环，建议不重审。

### 1.1 第一轮红队裁决与修订纪要（v0.1 → v0.2，2🔴 + 7🟡 + 9🔵；原裁决「🟡 修订后定向复审」）

红队报告为会话粘贴、未落盘，本表即唯一存档。每条指控在采纳前均已对照工作树独立复核（复核证据见附表「v0.2 补核」各行；🔴-1 的机制指控不属实但契约缺口属实，属「部分属实」，采纳到哪一步见表）。

| 编号 | 红队指控 | 独立复核 | 裁决 | 修订动作 |
|---|---|---|---|---|
| 🔴-1 | TC-1 的 9 行期望值不可复现：行内别名顺序未冻结；作者期望值与 set 迭代序吻合 | **部分属实**。契约缺口属实：§2.5 确实未冻结行内顺序，TC-1 逐字节断言与规则互相打架。机制指控不属实：撰写实验的收集是 `sorted(...)`（码位序，确定性），不是 set 迭代序——红队复现的翻转是它自己 set 实现的产物；但码位序未被 spec 冻结，责任不变。另自查发现第二个未冻结点：同一别名多次出现是否重复报告也未冻结（位置序初跑段 20 出现「大老师、大老师」） | **采纳** | §2.5 规则冻结：行内别名按**首现位置升序**、**去重**（同别名只报一次）、命中收集禁用 set；期望值重跑回填（段 1 → `雪乃、团子`，段 20 → `雪之下雪乃、大老师`）；4 个 PYTHONHASHSEED（0/1/42/random）输出 md5 全同（`02bd00b6…`），确定性实测入附表；TC-1/MUT-1 同步（MUT-1 证伪基线改为别名总数 12 → 22） |
| 🔴-2 | 封板无脏缓冲防护：dirty 时点封板 → patch 对旧保存版本生成 → 时序闸与非空闸全部合法放行，人封的不是屏幕上那版 | **属实**（纯文档推演：§2.2 工具条无先后约束；闸门只看磁盘，`approvals.py:327`/`330-331`） | **采纳（推荐项：dirty 禁用封板）** | §2.2 增 dirty-seal 规则：编辑器 dirty 时「封板」按钮禁用并提示「有未保存改动」——与「零改动不可封板」同一哲学（封板对象必须是磁盘上人已确认的版本）；「从草稿新建」同理 dirty 禁用；TD-3 增子用例、新增 MUT-16；门禁 3 增条目 |
| 🟡-1 | `/voice-parse` schema 与 Patch dataclass 对不上：`seed_pin` 不在 Patch（`corrections.py:484` 落盘时才生成）、缺 `kind`/`raw`；「字段原样」为假 | **属实**（`corrections.py:38-47` Patch 九字段逐一比对；`484` `entry["seed_pin"] = random.SystemRandom()…`） | **采纳** | §3.3 重写：`/voice-parse` 输出 = Patch 八字段（`raw` 显式剔除并说明——原文已在输入框，回传只增加帧面）；`seed_pin` 只出现在 `/voice-add` 落盘条目与 `pending_corrections`；`pending_corrections` 改「条目 dict 原样透传，消费方忽略未知键」（与 §3.1 容错规则 1 同精神），不枚举全量 |
| 🟡-2 | SAVE_SCRIPT stdout 指纹 JSON 的无损解析没接线；普通 `JSON.parse` 丢精度后「刚存完就冲突」 | **属实**（Spec 8 R3-B1 同机理；§2.2 只规定了 argv 方向） | **采纳** | §3.1 SAVE_SCRIPT 行明写 stdout 指纹 JSON 必须经 `losslessJson` 的 `parseLossless` 解析（`mtime_ns` → bigint）；§4.3 host 接口同步；§7.2 新增 TD-5（`os.utime(ns=1790171112636927676)` 夹具对拍，沿用 Spec 8 TA-2 夹具值）；§7.3 新增 MUT-17 |
| 🟡-3 | I2 闭集遗漏 done 的直接产物（seg wav 本体、新 attic 快照、`.apply_patch.lock` 创建与删除、applied 回写、job 事件），TI-3b 扩展后跑一次 done 必红 | **属实**（`tts.py:1830-1833` apply_patch 分支持锁；`corrections.py:436-450` 锁创建/删除；`679-704` `backup_segments` 新建快照含 copy2 manifest+wav；applied 回写经 `save_corrections_raw:410`；job 事件经 `run_pipeline` 包装） | **采纳** | §2.1 I2 闭集按实际写清单补全为五项 + events；门禁 1 增「done 全程目录树清单差异 ⊆ 闭集」断言。（**v0.2 实际未落入**：当时一批三处 edit 中另一处 oldText 不匹配导致整批回滚，作者未逐项回验，正文与 ADR-0024 §3 仍是旧枚举——红队二轮 🟡-1 抓出，v0.3 才实际落入 §2.1 与 ADR-0024 §3） |
| 🟡-4 | MUT-8 纸面推演不成立：stdin 只收原文，「篡改 JSON 再 add」无通道，TC-9 两断言照常绿 | **属实**（纯推演：接口冻结本身已是防护，用例无处着力） | **采纳** | MUT-8 删除，防护改由契约断言承担：TC-8 增「`/voice-add` 的 stdin 按文法原文处理」对拍（同一原文经 `/voice-add` 落盘 == `append_correction(parse_correction(原文))` 直调），§7.3 注明理由 |
| 🟡-5 | S8-R17 无视 Spec 8 自写前置（`archive` line 729「将来拉起长任务必须先落 Spec 2 RF-7 的 SIGTERM 优雅关闭」）；锁残留无恢复路径 | **属实**（line 729 原文比对一致；`apply_patch_lock`（`corrections.py:436-450`）无存活检测，SIGKILL/断电残留后 `append_correction:457` 与后续 apply-patch 双双 SystemExit） | **采纳，选 (b) 并改写前置** | 选 (b) 的理由：本设计宿主**永不**向该任务发信号（无超时、退出不杀、detached 孤儿跑完时 finally 正常清锁），SIGTERM 优雅关闭在本通道无用武之地；残余风险只有 SIGKILL/断电。S8-R17 措辞改写为「前置条件改写 + 例外」并配套：`/voice-info` 增 `apply_patch_lock: {exists, pid, pid_alive}` 确定性事实字段；面板与 runbook 写明手工清除路径（删 `03-audio/.apply_patch.lock`）；新增 TI 用例 TI-11；改写后的 S8-R17 措辞随复审呈人确认 |
| 🟡-6 | S8-R15 自称「沿用/保留」Spec 8 门禁 14 的半句，实际把「终端 `/voice`」改成了「`--review`」——是修订不是保留 | **属实**（archive line 256/1002 原文为「须在终端 `/voice` 完成」；真实打点入口是 `tts.py:1686 write_review`，`/voice` 指令表无打点命令，grep 核实一致） | **采纳** | S8-R15 如实改写：门禁 14 的「不计人时」半句随人时落地退役；「结构化打点」半句**修订**为「须在终端 `python -m pipeline.tts <期> --review` 完成」（同时修正 Spec 8 line 256 的表述错误）；§2.3 末、§6.1 同步 |
| 🟡-7 | 行号自查表 9 处累积偏差（含 ADR-0024 的 `approvals.py:329-330` 同错） | **属实**（逐条复核全中：85/540/1687/330-331/163-167/148-149/shared 12/六个正则/377） | **采纳** | 附表全量重核回填（v0.2 起逐行附复核方式）；ADR-0024 §4 的行号同步修正；§2.3「五个」改「六个」；§2.4 看板行号改 377；<6s 过滤的引用依据改写（见 🔵-2） |
| 🔵-1 | 映射表漏伴随行为：终端回滚/撤回先 `player.stop()`；done 的「退出纠错」在桌面无对应 | **属实**（`cli.py:605/614` 两行 `player.stop()` 复核一致） | **采纳** | §2.3 映射表增「差异说明」列：回滚/撤回前桌面同样先停播放；done 无「退出纠错」对应（面板常驻，无模式可退）；「一一对应」措辞收窄为「六指令语义一一对应，伴随行为差异逐行列出」 |
| 🔵-2 | <6s 丢弃的引用依据不成立：`cli.py:66` 的 0.1 分钟过滤仅适用 scout；桌面多一层过滤 = 两端口径新差异 | **属实**（`cli.py:66` `if stop == "scout"` 复核；终端停机点停留无下限） | **采纳（取消过滤）** | §2.4 取消 6 s 过滤：与终端停机点口径完全一致（全量记录）；噪音事后可按 `source` 键与时长过滤，不在采集端丢数据；常量表删 `HUMAN_TIME_MIN_INTERVAL_MS`；TD-1/MUT-11 同步改写 |
| 🔵-3 | 别名含通用词（「老师」「一色」），真实期 43% 段命中，INFO 有 wallpaper 化风险 | **属实**（期望值 21 段命中 9 段） | **采纳（备案）** | RF-6 增「施工后首个真期回访命中密度，若 >50% 段命中且人反馈无视，按 D18 精神收敛别名集（通用词别名降级）或调整提示粒度，另行修订」 |
| 🔵-4 | MUT-6 论证文字错误：闸门是三条件（存在、新于、非空），空 patch 会被非空闸拦下 | **属实**（`approvals.py:311-338` `_gate_valid` 复核） | **采纳** | MUT-6 机理改写：闸门第三条件（非空）能拦空 patch，TC-6 的独特证伪点是「既有 patch 不被清空」与「拒封退出码」，结论不变 |
| 🔵-5 | TC-5 同 size 异 mtime 子用例建议 `os.utime` 显式构造，防低粒度文件系统假绿 | **属实**（合理测试构造建议） | **采纳** | TC-5 子用例改为「写入同长内容后 `os.utime(ns=…)` 显式设不同 mtime」 |
| 🔵-6 | TE-* 夹具位置未写明；TE-4 若误指真实期即污染真实 `data/` | **属实**（v0.1 未写夹具位置；Spec 8 有临时 repo 副本先例） | **采纳** | §7.2 增夹具规则：e2e 一律临时 repo 副本（Spec 8 §7.1 先例），严禁指向真实 `data/`；门禁 9 增对应条目 |
| 🔵-7 | `voice-info` 的 heteronyms 未提 readings 截断（终端 `[:3]`，`cli.py:544`） | **属实**（544 行 `h.get('readings', [])[:3]` 复核） | **采纳** | §3.3 heteronyms 条目补 `readings` 截断前 3（终端同款） |
| 🔵-8 | §3.5 可先回填参考值：`from pipeline import corrections, g2p, tts` 暖进程 0.18 s | **属实**（红队实测值，作者未独立重跑——作为参考值回填并标注来源） | **采纳** | §3.5 回填：VOICE_* 启动开销约 0.18 s（暖进程，红队实测），非 status 的 0.03–0.04 s 量级但可接受 |
| 🔵-9 | `/save-script` 等号形式引 REVIEW_APPROVE 的 valued_flags 理由不适用（新子命令走裸形态分派，不过 `validate_pipeline_command`） | **属实**（裸形态分派按 argv 位置自解析，`cli.py:1612-1687`） | **采纳** | §3.1 等号形式理由改写：与既有 spawn 模板风格一致 + 值含特殊字符时无分词歧义；不引 valued_flags |

**下一轮定向复审范围（建议限定）**：本轮 2🔴 + 7🟡 的九处修订点（§2.5 顺序冻结与期望值、§2.2 dirty-seal、§3.3 schema、§3.1/§4.3 lossless 接线、§2.1 I2 补全、MUT-8 删除与 TC-8 增补、S8-R17 改写与 `apply_patch_lock` 字段、S8-R15 措辞、附表重核）+ 受影响测试矩阵行（TC-1/5/8、TD-1/3/5、MUT-1/6/11/16/17）。未变更的骨架（裸子命令写路径、指纹拒存、renderer 播放、人时口径、INFO 结构分离、PR 划分）本轮红队已确认成立，建议不重审。

---

## 2. 关键设计决策（含代码现状证据）

### 2.1 决策 1：写路径——core 裸形态子命令，不进 LLM 工具表（正面回答设计问题 1）

- **现状证据**：core 唯一的受控写入口是 `write_episode_file`（`pipeline/agent/tools.py:62-125`）：creative scope 限定、文件名白名单 `{01-topic.md, 02-script.draft.md}`（`tools.py:26-28`）、双端 resolve、`paths.atomic_write`（`paths.py:118-128`，`126` 写 `.tmp`、`128` `os.replace`）。它是 **LLM 工具表**里的工具（`tools.py:358` 起的 schema），`02-script.md` 刻意不在白名单——落定为正稿永远是人的动作。Spec 8 的桌面端写不变量：**I1** host 对 `data/` 零写入零 mkdir；**I2** 桌面引发的 core 写入只有「自愈簿记」与「显式点击（ack）」两类（Spec 8 §2.3）；spawn 闭集 `stdio: ["ignore", "pipe", "pipe"]`（Spec 8 §3.4）。
- **决策**（ADR-0024 决策 1 的落地）：UI 的写入全部经 **core 新增的非交互裸形态子命令**完成（`ava <期> /save-script` 等，全集见 §3.1），分派位置与既有 `/approve`、`/reject`、`/approvals` 同级（`cli.py:1612-1693` 的裸形态分派块，S3-R5 已保证非 TTY 下未分派子命令退 2 不落入 REPL，`cli.py:1716-1721`）。**不扩 `write_episode_file` 白名单、不进 LLM 工具表**：模型写通道（草稿）与人写通道（正稿）分开；工具表总数不变，红线 6 不受影响。
- **I1/I2 重新表述**（替代 Spec 8 §2.3 I2 清单，修订请求 S8-R14）：
  - **I1（不变）**：host 进程自身对 `data/` 零写入、零 mkdir；renderer 无任何 Node/文件 API。本 spec 新增的全部写入都发生在 host spawn 的 core 子进程里。
  - **I2（扩展后闭集）**：桌面端引发的 core 写入仍只有两类。**自愈簿记**：不变（Spec 8 §2.12）。**显式点击**：既有 ack 写入 + 本 spec 新增——`02-script.md`（保存/从草稿新建）；`02-diff.patch`（封板）；`03-audio/corrections.json`（纠错落盘、撤回、done 的 applied 回写，经 `save_corrections_raw`，`corrections.py:410`）；`03-audio/attic/**`（回滚的恢复读来源；done 经 `backup_segments`（`corrections.py:679-704`）**新建**快照：mkdir + `copy2` manifest 与受影响段 wav，`_prune_attic` 超限删旧快照）；`03-audio/seg-*.wav` 与 `03-audio/manifest.json`（done 的增量重配重生成本体与清单）；`03-audio/.apply_patch.lock` 的创建与删除（`corrections.py:436-450`）；`human_time.json`（人时条目）；对应 `events.jsonl` 事件行（`job_*` 经 `run_pipeline` 包装、激活预留枚举 `human_time_recorded`，`jobs.py:56`——撰写时 grep 核实该枚举无任何调用点）。闭集之外一律禁止；Spec 8 TI-3b 清单相应扩展（S8-R14），门禁 1 增「done 全程目录树清单差异 ⊆ 本闭集」断言。

### 2.2 决策 2：02.5 编辑器——形态、保存冲突规则、封板映射（正面回答设计问题 2）

- **形态**（用户裁决 2026-09-26）：左栏 CodeMirror 6 源码编辑器（Markdown 高亮、折行、行号），右栏复用 Spec 8 既有 markdown-it 渲染做预览（`html: false`，同一渲染参数），随 keystroke 节流刷新。顶部工具条：「保存」（`Cmd+S` 同义）、「从草稿新建」（仅当 `02-script.md` 缺席而 `02-script.draft.md` 在）、「封板」、「机检」（spawn `python -m pipeline.check_script`，原样显示 PASS/FAIL/INFO 文本——UI 不解析判定语义）。侧栏「人审要点」清单（§2.6）。
- **dirty-seal 规则（红队 🔴-2 新增，冻结）**：编辑器有未保存改动（dirty）时，「封板」与「从草稿新建」按钮**禁用**并提示「有未保存改动，封板对象必须是磁盘上的最新版本」。理由：封板的语义是「人确认了这份稿」，而解封物闸门（`approvals.py:311-338` 三条件：存在、新于、非空）只看磁盘版本——dirty 时放行，人封的是上一次保存的版本，屏幕上看到的与被封板、被批准的不一致，全部既有闸门合法放行。禁用与「零改动不可封板」同一哲学：封板对象必须是磁盘上人已确认的版本，任何「先在内存里」的状态都不许进封板链。
- **保存的冲突规则：指纹不符即拒存**（direction §6 Spec 11 的建议，论证如下）。流程：打开编辑器时 host stat `02-script.md` 得 (size, mtime_ns) 作为基线指纹（bigint 无损，沿用 Spec 8 §3.1 规则 8/9 的纪律——**mtime_ns 出 host 一律十进制字符串**，argv 亦同）；保存时 renderer → host → spawn `SAVE_SCRIPT`，argv 带 `--expect-size=<N> --expect-mtime-ns=<M>`，正文经 stdin；core stat 现状，不符则退出 1、一字不写，UI 显示「磁盘版本已变（可能已在别处修改），未保存」并提供「重新载入磁盘版本」（本地未存改动丢弃前二次确认）。**为什么是拒存而不是合并/覆盖**：① `02-script.md` 的字段是机器接口（`查询`/`锚点`/`人物`，`clips.py:206` 起），并发编辑的自动合并超出本系统能力，硬合并产出的是两边都认不出的机器输入；② last-writer-wins 静默吞掉人的修改，违反「诚实失败优于凑合交付」；③ 竞争者集合极小——agent 永不写 `02-script.md`（ADR-0024 决策 1 不扩白名单），唯一竞争是「人在终端/别的编辑器同时改」，指纹拒存恰好把它变成可见事件。已知残余：core 比对指纹与 `os.replace` 之间有微秒级窗口（`02-script.md` 现状无任何锁纪律），正常操作不可触达，如实声明（RF-2）。
- **封板与 core 命令的映射**：终端封板是 `git diff --no-index 02-script.draft.md 02-script.md > 02-diff.patch`（`docs/runbook/02.5-human-review.md` 封板操作节；`status.py:294-295` 的 next_action；`approvals.py:735-736` 的解封物提示）。shell 重定向无法被 `shell: false` 的 spawn 闭集表达，故封板收编为 core 子命令 `/seal-script`：core 以 `subprocess.run(["git", "diff", "--no-index", "02-script.draft.md", "02-script.md"], cwd=ep_dir)`（无 shell）执行**同一条命令**，exit 1（有差异）→ stdout 经 `atomic_write` 落 `02-diff.patch`；exit 0（无差异、输出为空）→ **拒封**，退出 1 并提示「未做任何修改，无封板痕迹可留」（用户裁决 2026-09-26：保持现状，不动 `approvals.py:330-331` 的「非空」闸门）；exit ≥2 或缺 draft → 报错退 1。产物与终端手工封板**字节一致**（同一 git 二进制、同一参数、cwd 相同——已实测该命令在有差异时 exit 1、无差异时 exit 0 且输出空，scratchpad 2026-09-26）。封板成功后 Spec 3 自愈（指纹漂移 + 解封物齐备）把 02.5 对象对齐为 `APPROVED(artifact)`，既有决策条走确认路径——**本 spec 不动 approvals.py**。
- **编辑后未封板的状态**：保存使 `02-script.md` 指纹漂移，既有 H5 触发 heal、旧 pending 转 SUPERSEDED、新建 pending（Spec 3 §2.3）；`02-diff.patch` 若已存在但旧于新保存，`approvals.py:327` 的时序闸判解封物过期——一切沿用既有机制，UI 只显示事实（封板按钮旁显示「封板已过期」推导自 status `block_reason`，不自行推导工序）。

### 2.3 决策 3：03.5 顺听面板——六指令按钮化，语义一对一；裸子命令，不走 Spec 9 协议；播放在 renderer（正面回答设计问题 3）

- **现状证据**：`/voice` 只活在交互式循环里（`run_voice_loop`，`cli.py:506`；REPL 入口 `cli.py:1393`，裸形态 `cli.py:1704-1705` 直接进 `run_voice_session`——后者是墙钟记账包装，`cli.py:301-311`）。指令表（`cli.py:455-460` 六个 `fullmatch` 正则 + 自由文法纠错）与行为：`听 N` → `open -a "QuickTime Player"`（macOS 分支，`cli.py:592`）；`听` → `VoicePlayer.play_all`（afplay 后台线程，`cli.py:463-503`，afplay 调用在 481）；`停` → `player.stop()`；`回滚 N` → 先 `player.stop()` 再 `corrections.revert_segment`（`corrections.py:729`）；`撤回 N` → 先 `player.stop()` 再 `corrections.retract_correction`（`corrections.py:813`）；`done` → 无 pending 提示返回，有 pending 则云端引擎打印提示（`cli.py:634-641`）后 `input("[Y/n]")` 确认、`tts.run(apply_patch=True)`（`cli.py:624-651`）。纠错自由文法：未命中指令表的行送 `corrections.parse_correction`（`corrections.py:162`），出结构化卡片，`y` 确认后 `append_correction`（`corrections.py:453`，落盘走 `atomic_write`，`corrections.py:429`）。
- **映射表（六指令语义一一对应，冻结；伴随行为差异逐行列出，红队 🔵-1）**：

| 终端指令 | 终端行为 | 桌面端控件 | 桌面端行为 | 差异说明 |
|---|---|---|---|---|
| `听 N` | QuickTime 打开该段 | 段落行内「▶」 | renderer `<audio>` 经 `ava-media://` 播该段（Spec 8 PreviewPane 既有通道） | 播放器从外部改为原位（§0.2.3 原位审计） |
| `听` | afplay 顺序播放全量 | 「顺序播放」 | 段落队列连续播放（`ended` 接下一条，同 Spec 8 §2.7 音频队列） | 同上 |
| `停` | 终止播放序列 | 「停」 | 停止当前播放并清空队列 | 无 |
| `回滚 N` | 先 `player.stop()`，再 `corrections.revert_segment`（无确认，直接执行） | 段落行内「回滚」（仅当 attic 有该段快照时可用） | **先停播放**，再 spawn `VOICE_REVERT`；无二次确认（与终端一致），结果显示 core 输出原文 | 无 |
| `撤回 N` | 先 `player.stop()`，再 `corrections.retract_correction`（无确认） | 待应用纠错条目行内「撤回」 | **先停播放**，再 spawn `VOICE_RETRACT`；无二次确认，同上 | 无 |
| `done` | 无 pending → 提示返回；云端引擎提示；`[Y/n]` 确认后 `tts.run(apply_patch=True)`；退出纠错循环 | 「完成并应用补丁」 | 无 pending → 按钮禁用并显示「当前没有待应用的纠错条目」；manifest `engine` 含 `cuda` → 禁用并显示「本期配音引擎为云端引擎，请去云端执行 apply-patch」（读 `voice-info` 返回的 engine 字段，确定性事实）；否则 main 原生确认框「是否立即执行增量重配 (--apply-patch)?」→ spawn `RUN_TTS_APPLY_PATCH`（长任务，§3.4） | 无「退出纠错」对应——面板常驻、无模式可退；确认从 `input()` 改为原生确认框（Spec 9 RF-17 处置，§2.7） |

  - 自由文法纠错（用户裁决：含输入框）：面板顶部输入框接受终端同一文法原文 → spawn `VOICE_PARSE`（stdin 传原文，core 用**同一个** `parse_correction` 解析，stdout 回结构化 JSON 或错误）→ UI 按 JSON 渲染与终端同款的确认卡（含「⚠️ 全局生效」警示，字段一一对应）→ 人点「确认落盘」→ spawn `VOICE_ADD`（stdin 传**原文**而非解析结果，core 重新解析并落盘——不信任跨进程往返的解析结果，parse/add 都对同一文法函数求值，等价且防篡改）→ 显示条目 id 与提示（回滚按段号、撤回按条目 id，同终端文案）。
- **为什么是裸子命令而不是 Spec 9 协议**：① 这六个指令全是**确定性人令**，无 LLM 参与；Spec 9 的协议进程是 LLM 会话（入站 `user_message`/`answer`/`command` 五帧，其 §3.1），把按钮塞进会话进程会要求会话常驻、引入回合/租约语义，而顺听恰恰常发生在「只打开面板听一遍、根本没起会话」的场景；② Spec 8 的 spawn 闭集模型（短命令、argv 闭集、环境白名单）已覆盖此类调用，扩闭集是既有先例（Spec 10 S8-R3）；③ Spec 9 §2.x 的会话锁一期一进程，若顺听依赖会话进程，「会话被占用/未启动」会误阻塞纯本地操作。裸子命令的代价是每个动作一次 Python 启动（status spawn 实测 0.03–0.04 s，Spec 8 §2.3；含 corrections/tts 模块导入的子命令实测回填，§3.5 常量表）。
- **为什么在 renderer 播放而不是 afplay/QuickTime**：direction §0.2 第 3 条「全要素应用内原位审计——严禁依赖外部播放器」；终端的 QuickTime/afplay 是 CLI 时代没有界面的妥协，桌面端打开外部播放器恰好违反终态判据。且 `ava-media://` + 原生 `<audio>` 已在 Spec 8 施工并通过验收，零新增通道。多音字异读预检清单（终端 `/voice` 启动时打印 `g2p.scan_heteronyms` 结果，`cli.py:540-548`、`g2p.py:159`）以只读文本呈现在面板顶部（经 `voice-info` 返回，条数与 readings 截断与终端同款：前 10 条、每条 readings 前 3，`cli.py:543-544`），属「终端既有输出的原位呈现」，不是划词注音。
- **结构化打点（manifest `human_review` 五项评分）不在本 spec**：它是 `python -m pipeline.tts <期> --review`（`tts.py:1813-1821` 独立动作、`1686` `write_review`），不是 `/voice` 指令表的一部分；继续在终端完成，03.5 面板注明（Spec 8 门禁 14 的该半句原文是「须在终端 `/voice` 完成」，与真实入口不符——S8-R15 一并修订其措辞，红队 🟡-6）。

### 2.4 决策 4：人时采集——审阅面可见即计时，经 core 落 `human_time.json`（正面回答设计问题 4）

- **现状证据**：`record_human_time(ep_dir, stop, entered_at, left_at)`（`cli.py:62-85`）向 `human_time.json` 追加 `{stop, entered_at, left_at, minutes}` 条目（`atomic_write`，`cli.py:85`）；调用点：REPL 停机点停留结算（`cli.py:1148`）、`/voice` 包装（`cli.py:310`）、`/scout`（`cli.py:1291`，<0.1 分钟噪音过滤，`cli.py:66`）。`jobs.py:56` 预留 `HUMAN_TIME_RECORDED` 枚举，撰写时无调用点（grep 核实）。Spec 8 门禁 14：人时记账命令落地前，03.5/05 决策条常驻「本次审阅不计人时」、人时类 advisory 旁标「数据源不完整」。direction §0.2 第 4 条（2026-09-23 用户裁决）：人时降为观测量，不设门禁。
- **计时口径**（用户裁决 2026-09-26）：**审阅面可见即计时，墙钟，失焦照算**——与终端 REPL 停留口径一致（人走神照算），两端数据可比。「审阅面」按停机点定义：02.5 = 编辑器打开；03.5 = 顺听面板打开；05/09 = 该停机点决策卡呈现（Spec 8 决策条 / Spec 10 施工后的待答区卡片，两者实现其一即挂钩，本 spec 不依赖 Spec 10）。**同一停机点同一时刻至多一个在计区间**（编辑器与决策卡同开不双计，RF-9）；区间结束于：ack 点击、审阅面关闭、切换活跃期、app 退出（退出时 best-effort 结算在途区间，失败则该段丢失——观测层允许有损，如实声明）。**不设时长下限、不做噪音过滤**（红队 🔵-2）：终端停机点停留记账本来就没有下限（`cli.py:66` 的 0.1 分钟过滤仅对 `scout` 生效），桌面端加过滤会造出新的两端口径差异；误点的短区间带着 `source` 键落盘，分析时可事后过滤，采集端不丢数据。
- **落盘**：新增裸形态子命令 `ava <期> /record-time <stop> --entered <epoch_s> --left <epoch_s>`：校验 `stop ∈ HUMAN_STOPS`、`entered < left`、`left` 不晚于当前（容差 60 s 钟差）→ 复用 `record_human_time` 追加（条目新增可选键 `"source": "desktop"`，终端条目无此键；下游消费只读 `minutes`——`cli.py:377` 的看板汇总只 sum `minutes`，形状兼容已核实）→ 成功后 emit `human_time_recorded` 事件（payload `{stop, minutes}`；sidecar 纪律：失败静默）。**形状与终端记录同一函数产出**，这是「两端可比」的落点。
- **呈现**：host 直读 `human_time.json`（纯读 JSON、整文件 `os.replace` 替换，与 approvals_store 直读同一安全论证；A4）。期视图头部显示按停机点分布：「人时（墙钟·观测量）：02.5 X m · 03.5 Y m · 05 Z m · 09 W m · 其他 V m · 合计 T m」，注「含终端与桌面记录，走神照算，不作门禁」。
- **与 Spec 8 门禁 14 的关系**（S8-R15）：本 spec 落地即门禁 14 所说的「人时记账命令落地」，「本次审阅不计人时」横幅与「数据源不完整」旁标**退役**；「结构化打点须在终端完成」半句**保留但措辞修订**为 `python -m pipeline.tts <期> --review`（原文写 `/voice`，与真实入口不符，红队 🟡-6）。RF-12 相应标记关闭（决策延迟语义不变——它由 Spec 3 的 ack 路径记录，与本 spec 的墙钟采集是两份数据：延迟 = 对象创建到确认，墙钟 = 审阅面可见时长；二者并存，不互相替代）。

### 2.5 决策 5：check_script 增 INFO 级人物提示行（D18 收口落点 ①，正面回答设计问题 6①)

- **裁决约束**（issues archive D18，2026-09-25 拍板）：「该写没写」不可证伪（提到名字 ≠ 画面需求是该角色；强制每段写 = 逼人编字段），**不立判据**；锚点是一等公民，`人物` 保持「写了就走过滤、不写不勉强」的纯可选通道（`clips.py:167-175` 注释同口径）。所以本提示 **只报 INFO、永不进 FAIL、永不影响退出码**。
- **规则（冻结，v0.2 按红队 🔴-1 收紧）**：对 `tts.parse_script` 解析出的每个口播段（`tts.py:146-171`）：段块含 `人物:` 字段（`clips.py:206` 同口径正则）→ 跳过（人已显式选择）；段块含 `锚点:` 字段 → 跳过（`clips.py:248-249` 锚点与人物/场景互斥，锚点段写人物本来就是 FAIL，提示它去写是教犯错）；其余段的 `配音` 文本与角色别名表做**最长优先、位置不重叠**的子串匹配（避免「大老师」嵌套「老师」双报——实测见下）。**行内输出顺序：按首现位置升序、去重（同一别名只报一次）；命中收集用有序结构，禁用 set**（set 迭代序受 PYTHONHASHSEED 随机化，期望值不可复现——🔴-1 的教训）。命中即输出一行 `INFO 段<label> 提到已登记角色但未写 \`人物:\`：<别名、分隔>`。写了 `场景:` 的段照样列出（人选了场景通道，提示只是给人扫一眼）。
- **别名来源**：`bgm.animes_of(期目录)`（`bgm.py:288`，从 `01-topic.md` 读番表）逐番取 `vindex.alias_map(番)`（`vindex.py:137-155`，`config/characters.json` 别名 → 规范键，规范键自身也算别名）。**拿不到就不报**（判据 4：跳过不定罪）——`01-topic.md` 无番表、`characters.json` 缺席、番不在表（`alias_map` 对后两者抛 SystemExit，`vindex.py:143-148`）一律安静返回空，不输出任何行。
- **期望值已先跑**（AGENTS.md 七节纪律；scratchpad，未改仓库）：真实期 `2026-07-30-春物-雪乃适合大老师`（21 段，全部未写 `人物:`）按上述规则输出恰 9 行（v0.2 按冻结后的顺序规则重跑，红队 🔴-1；4 个 PYTHONHASHSEED（0/1/42/random）下输出 md5 全同 `02bd00b6…`，确定性已实测）：

```
INFO 段1 提到已登记角色但未写 `人物:`：雪乃、团子
INFO 段2 提到已登记角色但未写 `人物:`：大老师
INFO 段4 提到已登记角色但未写 `人物:`：雪之下雪乃
INFO 段9 提到已登记角色但未写 `人物:`：由比滨、雪乃
INFO 段13 提到已登记角色但未写 `人物:`：大老师
INFO 段14 提到已登记角色但未写 `人物:`：大老师
INFO 段17 提到已登记角色但未写 `人物:`：大老师
INFO 段18 提到已登记角色但未写 `人物:`：大老师
INFO 段20 提到已登记角色但未写 `人物:`：雪之下雪乃、大老师
```

  （对照实验：朴素子串匹配在段 2/13/14/17/18 会多报「老师」——「大老师」的嵌套别名，命中别名总数从 12 膨胀到 22；最长优先不重叠规则消去。段 4/20 只报「雪之下雪乃」，「雪乃」「雪之下」被重叠消去。）
- **输出位置**：`main()` 的 PASS/FAIL 块之后、总结行之前（`check_script.py:862-875` 的打印循环后）；退出码仍只由 FAIL 数决定（`875` `return 1 if failed else 0`）。INFO 行不进 `run()` 的 `Check` 列表（`Check.ok` 是布尔，装不下第三态；INFO 与判定结构性分离，从构造上不可能被当成 FAIL——RF-6）。

### 2.6 决策 6：02.5 人审要点清单（D18/D19 收口落点 ②，正面回答设计问题 6②)

- **清单五条（冻结）**：既有三条（`docs/runbook/02.5-human-review.md` 审查重点：编辑判断立不立得住、事实核验、去模型味）+ 新增两条：
  4. **`人物:` 该写没写**——对照机检 INFO 行逐段扫一眼；该写没写不可证伪，这一条是提醒不是判据（D18）；
  5. **说话人断言抽帧核对**——「某某说」类断言抽帧确认说话人（`skills/write-script/SKILL.md:54` 既有约束；字幕 `Name` 字段基本不填，机器三条路全堵，D19）。
- **呈现形态**（随编辑器）：编辑器侧栏可折叠只读清单，默认展开；**不做打勾、不做强制确认**（打勾是把形式当实质，与「不立判据」的拍板同源）。文案真源在 runbook，UI 硬编码镜像，由一项 docs 一致性测试断言两处五条齐全（§7.1 TC-13）——「判据要么被执行，要么被删除」。
- runbook 同步增第 4、5 条（D18 拍板的既有决定，本 spec 执行；文档改动列 §6.1 D-R1）。

### 2.7 决策 7：Spec 9 RF-17 处置（`cli.py:644-646` 的 EOF 默认「是」）

Spec 9 RF-17 明确「Spec 11 按钮化 `/voice` 时必须改为显式答复」。处置两半：① 桌面端 done 走 main 原生确认框 + spawn，根本不经过 `input()` 路径；② core 侧把 `cli.py:644-646` 的 EOF 分支由取 `"y"` 改为取 `"n"` 并打印「未获确认，未执行增量重配」（与 `cli.py:674-676` 等其余确认类 EOF 默认「否」的口径一致）。终端交互行为不变（TTY 下 EOF 只在 Ctrl-D 时发生）。列 §6.1 RF17-C1。

### 2.8 决策 8：划词注音后置的边界（正面回答设计问题 5）

**本 spec 不做**（均须另立 spec）：
- 编辑器/顺听面板里**划词**（选中文本自动带出在哪个段、自动填入纠错输入框的段号与目标词）；
- 注音**候选建议**（选中词后由 g2p 给候选读音列表、点选即填）——`g2p.scan_heteronyms` 只以只读预检清单呈现（§2.3），不做成交互；
- 纠错文法的表单化（段号下拉、拼音字段拆格）——输入框就是终端文法原文，保证语义一对一与文法单源。

**边界判据**：凡是「从界面选区推导文法参数」的能力都后置；凡是「呈现 core 既有输出」或「原样传递人文本」的能力在本 spec。

### 2.9 决策 9：范围闸门（其余明确不做）

- 不改动 `/voice` 终端交互路径的任何行为（除 §2.7 的 EOF 默认值）；REPL 全功能保留，桌面面板是**平行表面**不是替代；
- 不做 manifest `human_review` 五项打点的 UI（§2.3 末）；
- 不做 02.5 的 LLM 辅助（「帮我润色这段」按钮等）——对话走 Spec 10 面板，编辑器是纯人工编辑器；不做任何 LLM 推荐/预判断（direction §5）；
- 不做 diff 可视化（patch 由 PreviewPane 既有 `.patch` 纯文本预览承担，Spec 8 §2.7「其余文本后缀按纯文本显示」）；
- 不做编辑历史/版本管理（git 与 02-diff.patch 已是审计链）；
- 桌面端不发起 05 的 `review --approve` 之外的任何新 `/run`（本 spec 只增 `tts --apply-patch` 一个长任务模板）；
- 人时：不做失焦暂停（用户裁决）、不做按日/按周聚合视图、不回填历史期。

---

## 3. 数据契约

### 3.1 core 新增裸形态子命令（`cli.py:1612-1693` 分派块内新增；全部 `stdin` 非 TTY 可用；退出码 0 成 / 1 败 / 2 用法错）

| 子命令 | argv（`<…>` 为值） | stdin | 成功行为 | 失败 |
|---|---|---|---|---|
| `/save-script` | `ava <期> /save-script --expect-size=<N> --expect-mtime-ns=<M>`（等号形式：与既有 spawn 模板风格一致、值无分词歧义；新子命令走裸形态分派、不过 `validate_pipeline_command`，红队 🔵-9 更正理由） | `02-script.md` 新正文（UTF-8，≤ 1 MiB，超限退 2） | stat 校验指纹（`N=M=-1` 表示断言文件**不存在**，用于从草稿新建）；符 → `atomic_write`，stdout 打印新指纹 JSON `{"size":…,"mtime_ns":…}`，退 0。**host 必须以 `losslessJson.parseLossless` 解析该 stdout**（`mtime_ns` 是约 1.8e18 的纳秒值，普通 `JSON.parse` 丢精度——Spec 8 R3-B1 同机理；否则「刚存完就冲突」，红队 🟡-2） | 指纹不符 → stderr「磁盘版本已变」退 1，**一字不写**；期目录越界/缺席 → 退 1 |
| `/seal-script` | `ava <期> /seal-script` | 无（ignore） | §2.2 的 git 封装；stdout 打印 patch 字节数，退 0 | 空 diff / 缺 draft / 缺 script / git 非 0/1 → stderr 原因，退 1 |
| `/voice-info` | `ava <期> /voice-info` | 无 | stdout 单行 JSON（§3.3），退 0 | 无 `02-script.md` → 退 1（同 `run_voice_loop:510-513`） |
| `/voice-parse` | `ava <期> /voice-parse` | 纠错原文（≤ 64 KiB） | `parse_correction` 成功 → stdout 单行 JSON（补丁结构化字段，§3.3），退 0；`PatchError` → stderr 错误原文（终端同款），退 1 | 同左 |
| `/voice-add` | `ava <期> /voice-add` | 纠错原文（≤ 64 KiB） | 重新 `parse_correction` + `append_correction`，stdout 单行 JSON（落盘条目，含 `id`），退 0 | `PatchError`/`SystemExit` → stderr 原文，退 1 |
| `/voice-revert` | `ava <期> /voice-revert <段号>` | 无 | `corrections.revert_segment`（`corrections.py:729`），stdout 其结果摘要，退 0 | `SystemExit` → 其消息进 stderr，退 1 |
| `/voice-retract` | `ava <期> /voice-retract <id>`（十进制整数） | 无 | `corrections.retract_correction`（`corrections.py:813`），退 0 | 同上；id 非整数退 2 |
| `/record-time` | `ava <期> /record-time <02.5\|03.5\|05\|09> --entered=<epoch_s> --left=<epoch_s>`（等号形式） | 无 | §2.4 校验 → `record_human_time` → emit `human_time_recorded`；退 0 | 校验失败退 2；写失败退 1 |

- **写纪律**：以上凡落盘必走 `paths.atomic_write`；目标文件是闭集（§2.1 I2）；期目录校验复用 `write_episode_file` 的双端 resolve 段（`tools.py:90-113`，抽成共享私有函数或直接复用——施工时择一，以不复制第二份解析逻辑为准）。
- **无副作用子命令**：`/voice-info`、`/voice-parse` 纯读/纯算，零写入（测试断言前后目录树哈希不变）。
- 这些子命令**不进** REPL 的 `/help` 指令表？——进。REPL 里同样可用（裸形态与 REPL 共用分派是自然结果），REPL `/help` 文案同步列出（一行）。

### 3.2 `human_time.json` 条目形状

既有形状不变：`{"stop", "entered_at"(ISO), "left_at"(ISO), "minutes"}`；桌面来源条目新增可选键 `"source": "desktop"`。消费者（`cli.py:377` 看板、status advisory）只读 `minutes` 与 `stop`，已核实兼容。

### 3.3 `/voice-info` 与 `/voice-parse` JSON schema（v1，冻结）

`/voice-info`：
```json
{
  "v": 1,
  "engine": "<manifest.engine 原文；manifest 缺席为空串>",
  "engine_cloud": false,
  "segments": [{"label": "1", "index": 1, "wav": "seg-01.wav", "wav_exists": true, "text": "<配音原文>", "has_attic": false}],
  "heteronyms": [{"char": "…", "readings": ["…"]}],
  "pending_corrections": [ … ],
  "apply_patch_lock": {"exists": false, "pid": null, "pid_alive": null}
}
```
- `segments` 由 `tts.parse_script` 供给（core 单源，TS 不解析稿件——RF-1）；`wav` 命名 `seg-{index:02d}.wav`（`tts.py:2024` 命名惯例，由 core 直接给出文件名，TS 不拼规则）；`has_attic` 由 core 查 attic 供给（「回滚」按钮可用性的确定性依据）；**不含时长**——时长由 renderer 的 `<audio>` 元素加载后自 metadata 读出（避免在固定 PATH 的 spawn 环境里调 ffprobe，`ffprobe` 在 `/opt/homebrew/bin`，Spec 8 §3.4 环境白名单之外；A3）。
- `engine_cloud = "cuda" in engine`（终端同款判定，`cli.py:638`）。
- `heteronyms` 即 `g2p.scan_heteronyms(全文)` 的前 10 条、每条 `readings` 截前 3（终端同款截断，`cli.py:543-544`）。
- `pending_corrections`：`corrections.load_corrections_raw`（`corrections.py:393`）中 `applied == false` 的**条目 dict 原样透传，消费方忽略未知键**（红队 🟡-1：不枚举全量——真实条目还有 `kind`/`raw`/`applied_at`/`affected`/`done_segments`/`created_at` 等，`seed_pin` 仅 pin_seed 条目有，`corrections.py:484`）。
- `apply_patch_lock`（红队 🟡-5 新增）：`.apply_patch.lock` 的存在性、锁内记录的 pid、该 pid 是否存活——纯确定性事实，供面板区分「应用进行中」与「锁残留（上次进程被杀/断电），可在终端 `rm data/episodes/<期>/03-audio/.apply_patch.lock` 清除后重试」。

`/voice-parse` 成功输出（红队 🟡-1 重写，字段以 `corrections.py:38-47` 的 Patch dataclass 为准）：`{"v":1,"segment","kind","word","heard","target_tone3","issue","action","scope"}`，缺省字段为 null；**不含 `raw`**（原文就在输入框，回传只增帧面）、**不含 `seed_pin`**（它在 `append_correction` 落盘时才生成，`corrections.py:484`）。`/voice-add` 成功输出：`append_correction` 返回的条目 dict 原样（含 `id` 与 pin_seed 条目的 `seed_pin`）。

### 3.4 spawn 模板新增（S8-R13；并入 Spec 8 §3.4 闭集；`py`、`ep` 同既有约定）

| 模板 | argv | stdin | 超时 | 用途 |
|---|---|---|---|---|
| `SAVE_SCRIPT` | `[py, "-m", "pipeline.agent.cli", ep, "/save-script", "--expect-size=<N>", "--expect-mtime-ns=<M>"]`（`<M>` 为 `String(bigint)`，规则 8/9） | **pipe**（正文） | 30 s | §2.2 |
| `SEAL_SCRIPT` | `[py, "-m", "pipeline.agent.cli", ep, "/seal-script"]` | ignore | 30 s | §2.2 |
| `VOICE_INFO` | `[py, "-m", "pipeline.agent.cli", ep, "/voice-info"]` | ignore | 30 s | §2.3 |
| `VOICE_PARSE` | `[py, "-m", "pipeline.agent.cli", ep, "/voice-parse"]` | **pipe** | 30 s | §2.3 |
| `VOICE_ADD` | `[py, "-m", "pipeline.agent.cli", ep, "/voice-add"]` | **pipe** | 30 s | §2.3 |
| `VOICE_REVERT` | `[py, "-m", "pipeline.agent.cli", ep, "/voice-revert", label]` | ignore | 30 s | §2.3 |
| `VOICE_RETRACT` | `[py, "-m", "pipeline.agent.cli", ep, "/voice-retract", id]` | ignore | 30 s | §2.3 |
| `RECORD_TIME` | `[py, "-m", "pipeline.agent.cli", ep, "/record-time", stop, "--entered=<E>", "--left=<L>"]` | ignore | 30 s | §2.4 |
| `RUN_TTS_APPLY_PATCH` | `[py, "-m", "pipeline.agent.cli", ep, "/run", "tts", "--apply-patch"]` | ignore | **无超时**（长任务；S8-R16/S8-R17 开口；进度经既有 events.jsonl tail 观测，job 生命周期事件由 Spec 2 的 `run_pipeline` 包装自动落盘） | §2.3 done |

- **stdin 例外**（S8-R13 一部分）：`SAVE_SCRIPT`/`VOICE_PARSE`/`VOICE_ADD` 三模板 `stdio: ["pipe", "pipe", "pipe"]`（既有闭集全为 `ignore`；Spec 10 S8-R3 已为 `SESSION_*` 开过 stdin 例外，此为第二类）；写入端写完即 `end()`；正文上限在 core 侧强制（§3.1），host 侧同值早退。
- **环境白名单例外**：`RUN_TTS_APPLY_PATCH` 与 `SEAL_SCRIPT` 需要 `git`/`ffprobe`——PATH 追加 `/opt/homebrew/bin`（仅这两个模板；A3；Spec 8 RF-3 的「固定 PATH 让需要 ffmpeg 的模板当场失败」纪律由白名单的显式开口保留其精神）。
- **长任务前置条件的处置（红队 🟡-5，S8-R17 改写）**：Spec 8 §6.1（archive line 729）原文「将来拉起长任务必须先落 Spec 2 RF-7 的 SIGTERM 优雅关闭」。本 spec 申请**将该前置改写为对本模板不适用**，理由与配套：host 对本模板**永不发信号**（无超时、退出不杀；detached 孤儿跑完时 `apply_patch_lock` 的 finally 正常清锁，`corrections.py:436-450`），SIGTERM 优雅关闭在本通道无用武之地。残余风险只剩 SIGKILL/断电/重启造成的**锁残留**（残留后 `append_correction`（`corrections.py:457`）与后续 apply-patch 双双 SystemExit，03.5 纠错链锁死）——配套：① `/voice-info` 暴露 `apply_patch_lock: {exists, pid, pid_alive}`（§3.3），面板区分「应用进行中」与「锁残留」；② 锁残留的清除路径（终端 `rm 03-audio/.apply_patch.lock`）写进面板提示与 `docs/runbook/03.5-voice-check.md`（D-R1 附带一行）；③ TI-11 用例覆盖。桌面端不提供「清锁」按钮（那是绕过单写者纪律的新写通道，不干）。
- 超时与进程组、stdout/stderr 尾部 8 KiB、`detached: true` 等通用纪律沿用 Spec 8 §3.4。`RUN_TTS_APPLY_PATCH` 例外：app 退出不向在途子进程发信号（与 Spec 8 §2.9「退出不发信号」一致），重开后经 events.jsonl 与 manifest 恢复显示。

### 3.5 常量表（全部为**初值**，PR2/PR3/PR4 实测回填——期望值先跑）

| 常量 | 初值 | 为什么是这个数 |
|---|---|---|
| `SAVE_SCRIPT_MAX_BYTES` | 1 MiB | 真实 `02-script.md` 为 KB 级（约 20 段 × 百字）；1 MiB 是两个数量级余量 |
| `VOICE_PARSE_MAX_BYTES` | 64 KiB | 纠错原文是一行文法，64 KiB 已远超任何合法输入 |
| 预览节流 `EDITOR_PREVIEW_DEBOUNCE_MS` | 300 | markdown-it 渲染 KB 级文本为亚毫秒（约，PR3 实测）；300 ms 节流在人感知上即时 |
| `RECORD_TIME` 钟差容差 | 60 s | host 与 core 同机同时钟，容差只为防取整边界 |
| 新子命令启动开销 | 实测回填 | status spawn 0.03–0.04 s（Spec 8 §2.3）；`from pipeline import corrections, g2p, tts` 暖进程约 0.18 s（红队一轮实测值，v0.2 回填）；VOICE_* 在此量级，可接受 |

---

## 4. 模块接口与签名

### 4.1 core（`pipeline/`，纯 stdlib；新代码零重依赖）

```python
# pipeline/agent/cli.py —— 裸形态分派块内新增（每个子命令一个私有函数，分派处只路由）
def _cmd_save_script(ep_dir: Path, expect_size: int, expect_mtime_ns: int, stdin_text: str) -> int
def _cmd_seal_script(ep_dir: Path) -> int
def _cmd_voice_info(ep_dir: Path) -> int          # 纯读
def _cmd_voice_parse(ep_dir: Path, stdin_text: str) -> int   # 纯算
def _cmd_voice_add(ep_dir: Path, stdin_text: str) -> int
def _cmd_voice_revert(ep_dir: Path, label: str) -> int
def _cmd_voice_retract(ep_dir: Path, item_id: int) -> int
def _cmd_record_time(ep_dir: Path, stop: str, entered: float, left: float) -> int

# pipeline/check_script.py
def character_hints(path: Path) -> list[str]
    """D18 INFO 提示（纯函数式：读 01-topic.md 与 config/characters.json，失败安静返回 []）。
    规则见 Spec 11 §2.5：人物段跳过、锚点段跳过、最长优先不重叠匹配。"""

# record_human_time 增可选参数（默认行为字节不变）
def record_human_time(ep_dir, stop, entered_at, left_at, *, source: str | None = None) -> float
```

### 4.2 `desktop/src/shared/`（纯函数，零 Node/DOM，沿用 Spec 8 §4.1 纪律）

```ts
// voiceInfo.ts —— /voice-info JSON 的容错解析（未知字段忽略、缺必需字段 malformed，同 §3.1 规则 1-3 精神）
export function parseVoiceInfo(text: string): { ok: true; info: VoiceInfo } | { ok: false };
// editorState.ts —— 脏标记与指纹携带（纯 reducer）
export type ScriptEditorState = { baselineFp: { size: string; mtimeNs: string } | null; dirty: boolean; conflict: boolean };
// humanTime.ts —— 人时区间机（纯函数，注入 now 便于单测）
export type HumanTimer = { /* per-stop 单区间：start(stop, now) / stop(stop, now) / flushAll(now) */ };
export function reduceHumanTime(records: HumanTimeRecord[]): { perStop: Record<string, number>; totalMin: number };
```

### 4.3 `desktop/src/host/`

```ts
// spawner.ts —— §3.4 九模板加入闭集（S8-R13）；stdin pipe 三模板的写入辅助；
//   SAVE_SCRIPT 的 stdout 指纹 JSON 必须经 shared/losslessJson.ts 的 parseLossless 解析（红队 🟡-2，Spec 8 R3-B1 同机理）
// humanTime.ts —— 计时器宿主：审阅面事件 → 区间 → flush 时 spawn RECORD_TIME（不设时长下限，红队 🔵-2）
export function noteReviewSurface(epKey: string, stop: Stop, visible: boolean, now: number): void;
export function flushHumanTimers(reason: "ack" | "close" | "switch" | "quit"): void;
```

### 4.4 `desktop/src/renderer/`

- `ScriptEditor.tsx`：CodeMirror 6 装载 + 预览并排 + 工具条（保存/从草稿新建/封板/机检）+ 人审要点侧栏 + 冲突拒存提示与「重新载入」；脏状态切期/关闭二次确认（RF-7）。
- `VoicePanel.tsx`：段落列表（行内 ▶/回滚）、全局 顺序播放/停、待应用纠错条目列表（行内 撤回）、纠错输入框与确认卡、「完成并应用补丁」、异读预检只读区、「结构化打点须在终端 `python -m pipeline.tts <期> --review` 完成」注明（红队 🟡-6 修订后措辞）；`apply_patch_lock.pid_alive === false` 时显示锁残留提示与手工清除路径（红队 🟡-5）。
- 人时读数：期视图头部组件（§2.4 呈现）。
- **显式点击纪律沿用**：发送 `SAVE_SCRIPT`/`SEAL_SCRIPT`/`VOICE_*`/`RECORD_TIME`/`RUN_TTS_APPLY_PATCH` 的代码只允许出现在对应按钮/确认框的点击处理器与 humanTimer 的 flush 路径里（TG-4′ 静态断言扩展，S8-R13 附带）。

---

## 5. 依赖白名单与纯洁性保障

### 5.1 npm 依赖新增（`desktop/package.json`，版本钉死；2026-09-26 `npm view` 核实存在）

| 包 | 版本 | 用途 |
|---|---|---|
| `@codemirror/state` | 6.7.6 | 编辑器文档模型 |
| `@codemirror/view` | 6.43.13 | 编辑器视图 |
| `@codemirror/language` | 6.12.4 | 高亮/语言宿主 |
| `@codemirror/commands` | 6.11.1 | 基础编辑命令与键位 |
| `@codemirror/lang-markdown` | 6.5.2 | Markdown 高亮 |

传递依赖（`@lezer/*` 等）由 `package-lock.json` 钉死，不直接列。其余依赖不变（markdown-it 15.0.2 复用）。**零运行时框架新增**：不引 UI 组件库、不引状态库。

### 5.2 纯洁性

- **Python 侧**：`pyproject.toml` 零改动；新增 core 代码只用 stdlib（`subprocess`/`json`/`re`/`time`）+ 既有 `pipeline.*` 模块。回归断言（红线 7，scout spec 先例）：测试断言 `import pipeline.agent.cli`、`import pipeline.check_script` 后 `sys.modules` 不含 `numpy`/`torch`/`mlx`。
- **TS 侧**：沿用 Spec 8 §5.2 模块级 import 纪律；`@codemirror/*` 只允许出现在 `renderer/ScriptEditor.tsx`（TG 静态守卫扩展，防渗透进 host/shared——host 是纯 Node，不该碰到 DOM 编辑器）。
- **子进程纯洁性断言**：测试断言 `SAVE_SCRIPT`/`SEAL_SCRIPT`/`VOICE_*`/`RECORD_TIME` 子命令在 `env -i PATH=<白名单>` 下行为不变（A3 的回归化）。

---

## 6. 跨 spec 接口与系统边界

### 6.1 修订请求（编号明确；**全部待用户授权**——未授权不动工；授权不等于可动工，动工仍须红队 🟢 且 ADR-0024 被接受）

| 编号 | 对象 | 请求 | 级别 |
|---|---|---|---|
| **S8-R13** | Spec 8 §3.4 | spawn 闭集 +9（§3.4 表）；stdin pipe 例外三类；`SEAL_SCRIPT`/`RUN_TTS_APPLY_PATCH` 的 PATH 白名单追加 `/opt/homebrew/bin`；TG-4′ 显式点击断言扩展 | 阻塞 |
| **S8-R14** | Spec 8 §2.3 I2 | 「显式点击」类清单扩展（§2.1）；TI-3b 清单同步 | 阻塞 |
| **S8-R15** | Spec 8 门禁 14、RF-12、archive line 256 | 人时落地后「本次审阅不计人时」横幅与「数据源不完整」旁标退役；「结构化打点」半句**修订**（红队 🟡-6：原文「须在终端 `/voice` 完成」与真实入口不符，修订不是保留）：改为「须在终端 `python -m pipeline.tts <期> --review` 完成」，同步 archive line 256 与门禁 14 两处；RF-12 标关闭 | 阻塞 |
| **S8-R16** | Spec 8 §2.11 | 「v1 明确不做」删去三条：02.5 封板与产物编辑（RF-13）、划词注音等深度业务组件、人时记账（RF-12）；RF-13 标关闭 | 阻塞（文字） |
| **S8-R17** | Spec 8 §2.9、§6.1（archive line 729） | 「v1 不拉起长任务」为 `RUN_TTS_APPLY_PATCH` 开一个模板级例外：无超时、app 退出不发信号、进度经 events.jsonl 观测。**v0.2 按红队 🟡-5 增写前置条件处置**：§6.1 line 729「将来拉起长任务必须先落 Spec 2 RF-7 的 SIGTERM 优雅关闭」对本模板改写为不适用（宿主永不发信号，论证见 §3.4）；锁残留风险由 `voice-info` 的 `apply_patch_lock` 字段 + 手工清除路径（runbook 与面板文案）承接。**改写后的措辞随 v0.2 定向复审呈人再确认** | 阻塞 |
| **RF17-C1** | `pipeline/agent/cli.py:644-646` | EOF 默认由「是」改「否」（落实 Spec 9 RF-17 的既有处置授权；`tests/test_agent_cli.py` 若有覆盖该分支的期望值同步改写——期望值先跑） | 阻塞（core 改动；Spec 9 已把处置权委托给本 spec） |
| **D-R1** | `docs/runbook/02.5-human-review.md` | 审查重点 +2 条（§2.6）；封板操作节增一句「或在 app 内 02.5 编辑器点『封板』」；`docs/WORKFLOW.md` 02.5 行同步一句 | 阻塞（文档；只许瘦身不许膨胀，合计净增 ≤6 行） |
| **S3** | Spec 3 | **无修订**——02.5 确认路径、指纹、解封物闸门全部沿用（§2.2 已论证） | — |
| **S9 / S10** | Spec 9 / Spec 10 | **无修订**——不消费协议进程；Spec 10 §6.4 已声明不预置 Spec 11 控件，本 spec 的组件挂在 PreviewPane 区与期视图，不动其卡片 | — |

### 6.2 与 Spec 9 / Spec 10 的边界

- **Spec 9**：本 spec 的子命令全部绕开协议进程（§2.3 论证）；协议进程施工与否不阻塞本 spec 任何 PR。RF-17 处置见 §2.7。会话进程中模型经工具调 `tts` 走既有白名单与审批，与本 spec 的人令通道无交集。
- **Spec 10**：对话面板、待答区、焦点规则（其 §2.7）不变。02.5 编辑器打开时若有会话在跑，互不阻塞；「停机点自动呼出」呼出的是编辑器/顺听面板时仍受 Spec 10 焦点规则约束（人手动点开过别的就不替换）。**待定义缺口（如实声明）**：Spec 10 未施工，05/09 人时的「决策卡呈现」挂钩在 Spec 10 落地后改挂其待答区卡片——两处挂钩都由 host 的 `noteReviewSurface` 单点定义，届时只改调用方。

### 6.3 八条施工红线对照

1. **不引入数据库**：人时/纠错/封板全部文件落盘（`human_time.json`、`corrections.json`、`02-diff.patch`），零数据库。✓
2. **不引入第三方 agent 框架/编排/web server**：core 零 server；spawn 闭集扩 9 个模板全是既有 CLI 的裸形态子命令。✓
3. **产物即状态**：`pipeline.status` 推导逻辑零改动；人时与 INFO 提示都是观测层，不参与状态推导。✓
4. **停机点不减少、ack 永远显式**：四个停机点与 ack 链路不动；封板/保存/done 都是人的显式点击（TG-4′）；零改动不可封板保持闸门原义。✓
5. **人审闸门不自动化**：done 的确认框由人点；纠错落盘由人点；无任何自动保存封板。✓
6. **工具表封顶**：LLM 工具表零新增（§2.1），工具表总数不变。✓
7. **依赖纯洁**：core 零新依赖；npm 新增 5 包钉死并限域 renderer 单文件；子进程纯洁性断言入 §7。✓
8. **期望值先跑 + 变异检验**：INFO 提示期望值已在真实期跑出（§2.5）；封板 git 行为已实测；全部关键断言配 §7.3 变异。✓

---

## 7. 测试规格与变异检验

### 7.1 core 测试（`tests/`，pytest）

| 编号 | 用例 | 关键断言 |
|---|---|---|
| TC-1 | `character_hints` 真实期期望 | 对 `2026-07-30-春物-雪乃适合大老师`（若 V 盘不在则用等价夹具期）输出恰为 §2.5 的 9 行（期望值已按冻结规则先跑；v0.2 重跑，4 个 PYTHONHASHSEED 下输出一致） |
| TC-1b | 确定性（红队 🔴-1 回归） | 同一实现跨进程跑 3 次（不同 PYTHONHASHSEED），输出逐字节一致——set 收集之类的非确定实现在此变红 |
| TC-2 | INFO 不进退出码 | 夹具稿只触发 INFO 时 `check_script` 退出码与 FAIL 数不变；INFO 行出现在 PASS/FAIL 块之后 |
| TC-3 | 别名嵌套不双报 | 文本含「大老师」时命中集含「大老师」不含「老师」（变异点，见 MUT-1） |
| TC-4 | 跳过规则 | 写了 `人物:` 的段、写了 `锚点:` 的段不报；无番表 / 无 characters.json / 番不在表 → 安静返回 `[]` |
| TC-5 | `/save-script` | 正常写入（指纹符 → 落盘、内容一致、返回新指纹）；指纹不符退 1 且文件字节不变（同 size 异 mtime 子用例以 `os.utime(ns=…)` 显式构造，防低粒度文件系统假绿，红队 🔵-5）；`(-1,-1)` 在文件已存在时退 1；正文超上限退 2；越界期目录退 1 |
| TC-6 | `/seal-script` | 产出 patch 与手工 `git diff --no-index` **逐字节一致**；空 diff 退 1 且不写/不改 `02-diff.patch`；缺 draft 退 1；既有 patch 在空 diff 时不被清空 |
| TC-7 | `/voice-info` | schema 逐键对拍 `tts.parse_script` + `load_corrections_raw`；云端引擎期 `engine_cloud == true`；无 `02-script.md` 退 1；运行前后目录树哈希不变（纯读） |
| TC-8 | `/voice-parse` + 契约断言 | 与 `parse_correction` 同输入同结构（含 PatchError 文案一致）；目录树哈希不变（纯算）；**`/voice-add` 的 stdin 只按文法原文处理**：同一原文经 `/voice-add` 落盘 == `append_correction(parse_correction(原文))` 直调逐键一致（红队 🟡-4，替代 MUT-8 的契约防线） |
| TC-9 | `/voice-add` | 落盘条目与 `append_correction` 直调一致；同一原文二次调用产生两个不同 id（与终端连续录入两次语义一致） |
| TC-10 | `/voice-revert` / `/voice-retract` | 调用与 `corrections.revert_segment`/`retract_correction` 直调等价；不存在段/条目 → 退 1 且 stderr 为 SystemExit 消息 |
| TC-11 | `/record-time` | 落盘条目形状与 `record_human_time` 直调一致且含 `source:"desktop"`；`stop` 越集退 2；`entered ≥ left` 退 2；未来 `left` 退 2；成功后 events.jsonl 出现 `human_time_recorded` 行（payload `{stop, minutes}`） |
| TC-12 | RF17-C1 | 非 TTY（stdin EOF）进入 `/voice` done 分支 → 不执行 apply-patch、打印「未获确认」（期望值先跑：先跑出改动后的实际输出再写断言） |
| TC-13 | 人审要点双源一致 | runbook 02.5 与 `ScriptEditor.tsx` 均含 §2.6 全部五条的关键串（含「人物」「抽帧」两条新串） |
| TC-14 | 纯洁性回归 | `import pipeline.agent.cli`、`import pipeline.check_script` 后 `sys.modules` 无 `numpy`/`torch`/`mlx` |

### 7.2 desktop 测试（`desktop/tests/`，vitest + Playwright e2e）

- **夹具规则（红队 🔵-6，冻结）**：全部 e2e 一律在临时 repo 副本上跑（Spec 8 §7.1 先例），**严禁指向真实 `data/`**；人时/封板/纠错类 e2e 的写入只落在副本里。

| 编号 | 用例 | 关键断言 |
|---|---|---|
| TD-1 | 人时区间机 | 同 stop 双 visible 不双计；**不设时长下限**——0.5 s 区间也照常 flush（红队 🔵-2，与终端口径一致）；switch/quit 时 flush 恰一次；ack 后该 stop 区间关闭 |
| TD-2 | `parseVoiceInfo` 容错 | 未知字段忽略；缺 `segments` 键 malformed；真实 core 输出对拍（契约用例，同 Spec 10 TX-0 模式） |
| TD-3 | 编辑器状态机 | 保存成功更新基线指纹；冲突（core 退 1）→ `conflict: true` 且本地内容不丢；「重新载入」需二次确认；**dirty 时封板/从草稿新建禁用并提示**（红队 🔴-2） |
| TD-5 | SAVE_SCRIPT stdout 无损解析（红队 🟡-2） | 夹具期 `os.utime(ns=1790171112636927676)` 后保存：host 经 `parseLossless` 读到的新基线指纹与 `statSync(bigint)` 逐位相等（普通 `JSON.parse` 在该值上丢精度，Spec 8 TA-2 夹具值复用） |
| TI-11 | 锁残留可见性（红队 🟡-5） | 手工放置 `.apply_patch.lock`（pid 填已死进程号）→ 面板显示「锁残留 + 手工清除路径」；pid 存活 → 显示「应用进行中」；无锁 → 正常 |
| TD-4 | done 按钮状态机 | 无 pending 禁用；`engine_cloud` 禁用 + 云端提示；正常路径确认框取消不产生 spawn |
| TE-1 | e2e：02.5 闭环 | 夹具期：从草稿新建 → 编辑 → 保存 → 封板 → heal 后 02.5 对象 artifact 对齐 → 批准退 0（全程零终端） |
| TE-2 | e2e：冲突拒存 | 编辑器打开后外部（测试进程）改写 `02-script.md` → 保存被拒、磁盘字节为外部版、UI 提示可见 |
| TE-3 | e2e：03.5 闭环 | 夹具期：播单段（audio 元素 src 为 `ava-media://`）→ 录入纠错 → 确认落盘（corrections.json 出现条目）→ 撤回（条目消失）→ 回滚按钮可用性与 `has_attic` 一致 |
| TE-4 | e2e：人时 | 打开 02.5 编辑器 → 人为停留（fake timers 不可行，用短真实等待 ≥6 s）→ 关闭 → `human_time.json` 恰增一条 `source:"desktop"` |

### 7.3 变异检验矩阵（逐条实跑：植入变异 → 确认目标用例变红 → 还原）

| 编号 | 变异（怎么改坏） | 应变红的用例 | 为什么是它变红（不被掩盖论证） |
|---|---|---|---|
| MUT-1 | `character_hints` 改回朴素子串匹配（去掉最长优先不重叠） | TC-1（命中别名总数 12 → 22）、TC-3 | TC-3 专测嵌套别名；TC-1 期望值含真实嵌套案例（段 2/13/14/17/18 多报「老师」，段 4/20 多报「雪乃/雪之下」），两杀；红队 🔴-1 后基线按冻结规则重跑 |
| MUT-2 | INFO 行计入 `failed` | TC-2 | TC-2 断言退出码与 FAIL 数不变；无其他断言依赖 INFO 计数 |
| MUT-3 | 锚点段不跳过 | TC-4 | TC-4 夹具有带锚点且配音含角色名的段；其他用例段均无锚点，不掩盖 |
| MUT-4 | `/save-script` 指纹比对改为只比 size | TC-5（mtime 漂移用例） | TC-5 专设同 size 异 mtime 子用例（同长覆写）；size 相同的正常用例不受影响 |
| MUT-5 | `/save-script` 指纹不符时仍落盘 | TC-5 | 断言文件字节不变；与 TE-2 双杀 |
| MUT-6 | `/seal-script` 空 diff 时写空文件 | TC-6 | approvals 闸门是三条件（存在、新于、非空，`approvals.py:311-338`），空 patch 在 approve 时会被非空闸拦下——所以 TC-6 的独特证伪点是「拒封退出码」与「既有 patch 不被清空」，两者只由 TC-6 承担（红队 🔵-4 更正论证，结论不变） |
| MUT-7 | `/seal-script` 改用 `difflib` 生成 patch | TC-6 | 字节对拍必红（difflib 无 `index` 行、无 `diff --git` 头） |
| ~~MUT-8~~ | **v0.2 删除**（红队 🟡-4）：`/voice-add` 的 stdin 只收原文，「篡改 parse 往返 JSON 再 add」无此通道，纸面推演不成立 | — | 防护改由接口冻结本身 + TC-8 的契约断言承担（§7.1） |
| MUT-8b | `character_hints` 命中收集改用 set（顺序不冻结） | TC-1b | TC-1b 跨 PYTHONHASHSEED 复跑，set 迭代序在高概率下至少一次翻转（段 1 两别名，理论 50%）；TC-1 单次运行可能侥绿，TC-1b 不侥 |
| MUT-9 | `/record-time` 不写 `source` 键 | TC-11 | TC-11 显式断言该键；看板消费方不读它，不被其他断言掩盖 |
| MUT-10 | `/record-time` 漏 emit 事件 | TC-11 | events.jsonl 断言；记录落盘照常，唯一变红点 |
| MUT-11 | host 人时区间加 <6 s 过滤 | TD-1 | v0.2 已取消过滤（红队 🔵-2）；TD-1 的 0.5 s 区间照常 flush 子用例专杀「顺手加回过滤」 |
| MUT-12 | 人时同 stop 双区间并行计费 | TD-1 | 「编辑器+决策卡同开」子用例断言 minutes 恰为单区间墙钟 |
| MUT-13 | RF17-C1 不做（EOF 仍取「是」） | TC-12 | TC-12 专测 EOF 分支；TTY 路径测试不变绿不变红、不掩盖 |
| MUT-14 | 编辑器冲突后 UI 仍显示「已保存」 | TD-3、TE-2 | 双杀；core 侧 TC-5 独立兜底 |
| MUT-15 | `@codemirror/*` 被 import 进 `host/` 或 `shared/` | TG 静态守卫（§5.2） | 守卫用例扫描 host/shared 全部源文件 import 语句 |
| MUT-16 | 去掉 dirty-seal 禁用（dirty 时封板按钮可点） | TD-3 的 dirty 子用例、门禁 3 手验 | TD-3 专设 dirty 子用例；干净状态用例不受影响（红队 🔴-2） |
| MUT-17 | host 用普通 `JSON.parse` 读 SAVE_SCRIPT stdout 指纹 | TD-5 | TD-5 夹具值 `1790171112636927676` 不可被 double 精确表示（普通 parse 得 `…927700`），与 statSync(bigint) 不等 → 下次保存恒拒存的行为断言变红（红队 🟡-2） |

---

## 8. 施工 PR 划分

| PR | 范围 | 前置 | 验证 |
|---|---|---|---|
| **PR1**（core，纯读提示） | `check_script.character_hints` + 输出接入 + TC-1~TC-4、TC-13 的 core 半 + MUT-1/2/3；D-R1 的 runbook/WORKFLOW 文档改动 | **无**（不依赖 ADR-0024；纯读、零写入） | `uv run pytest` 全绿 + 变异实跑回填 |
| **PR2**（core，人令子命令） | §3.1 八个子命令 + RF17-C1 + TC-5~TC-12、TC-14 + MUT-4~MUT-13 的 core 半 | **ADR-0024 被接受** | 同上；子命令启动开销实测回填 §3.5 |
| **PR3**（desktop，02.5） | CodeMirror 依赖（§5.1）+ `ScriptEditor.tsx` + host 接线（SAVE_SCRIPT/SEAL_SCRIPT，含 stdout 无损解析）+ 人审要点侧栏 + TD-3、TD-5、TE-1、TE-2 + MUT-14、MUT-15、MUT-16、MUT-17 | PR2 + S8-R13/R14/R16 授权 | `npx vitest run` 全绿 + e2e；A1 首日实测 |
| **PR4**（desktop，03.5） | `VoicePanel.tsx` + host 接线（VOICE_*、RUN_TTS_APPLY_PATCH）+ 锁残留显示 + TD-2、TD-4、TI-11、TE-3 | PR2 + S8-R13/R17 授权 | 同上 + A2 实测回填 |
| **PR5**（desktop+core，人时） | humanTimer（host）+ RECORD_TIME 接线 + 期视图人时读数 + 门禁 14 横幅退役（S8-R15）+ TD-1、TE-4 + MUT-11/12 | PR2；横幅退役建议放在 02.5/03.5 至少其一实机可用之后 | 同上 + 门禁 9（真机手验） |

PR 顺序允许 PR1 ∥ 任何；PR3/PR4 可并行（不同组件、共享 PR2 的 spawn 模板）。

---

## 9. 验收门禁清单

- [ ] **门禁 0（前置）**：ADR-0024 状态为「已通过」；§6.1 全部修订请求获用户授权；本 spec 红队 🟢；
- [ ] **门禁 1（写纪律）**：全部新写入经 core 子命令 + `atomic_write`；I1 不破（host 对 `data/` 仍零写入——Spec 8 TG-2/TI-3a 全绿）；I2 扩展清单与 TI-3b 一致；**done 全程目录树清单差异 ⊆ §2.1 I2 闭集**（红队 🟡-3）；
- [ ] **门禁 2（冲突拒存与无损指纹）**：TC-5 全部子用例 + TE-2 + TD-5；MUT-4/5/14/17 被捕获；
- [ ] **门禁 3（封板保真与 dirty-seal）**：TC-6 字节对拍通过；MUT-6/7 被捕获；真实期手验一次「编辑→封板→批准」零终端闭环（TE-1）；**dirty 时封板/从草稿新建禁用**（TD-3 子用例 + MUT-16 被捕获，红队 🔴-2）；
- [ ] **门禁 4（03.5 语义一对一）**：§2.3 映射表逐条与终端行为对照手验（含两个「无确认」、两个禁用态）；TC-7~TC-10、TD-2/4、TE-3 全绿；**TC-8 的 `/voice-add` 原文重解析契约断言成立**（替代已删除的 MUT-8，红队二轮 🟡-3）；
- [ ] **门禁 5（人时口径）**：TC-11、TD-1、TE-4 全绿；两端记录同形状（真实 `human_time.json` 混入终端与桌面条目后看板汇总正确）；MUT-9~MUT-12 被捕获；门禁 14 横幅已退役（S8-R15）；
- [ ] **门禁 6（INFO 只报不拦）**：TC-1~TC-4 全绿；`check_script` 对既有真实期的退出码与 FAIL 行集合**逐字节不变**（INFO 行之外零 diff，用既有期对拍）；MUT-1/2/3 被捕获；
- [ ] **门禁 7（终端零回归）**：`/voice` REPL 路径相关测试全绿；全量 `uv run pytest` 与 `npx vitest run` 绿；RF17-C1 后 EOF 行为按 TC-12；
- [ ] **门禁 8（依赖纯洁）**：§5.2 全部断言 + MUT-15；`pyproject.toml` 零改动；
- [ ] **门禁 9（打包版手验与 e2e 夹具纪律）**：打包版上完成一次 02.5 编辑-保存-封板、一次 03.5 顺听-纠错-撤回、人时读数可见（A1/A2/A3 实测回填）；**全部 e2e 在临时 repo 副本上跑，严禁指向真实 `data/`**（红队 🔵-6）；
- [ ] **门禁 10（文档门禁）**：`uv run pytest tests/test_docs_invariants.py` 全绿；D-R1 落地；`docs/dev/plans/README.md` 状态行更新。

---

## 10. 潜在红旗与自纠预案

| 风险序号 | 潜在红旗 | 根因与危险性 | 预案与防御动作 |
|---|---|---|---|
| **RF-1** | TS 侧长出第二份稿件解析 | 段号/文件映射若由 TS 从 markdown 自己解析，必与 `tts.parse_script` 漂移（「`配音：`可在块内任意位置」这类教训，`tts.py:147-151`） | `voice-info` 由 core 单源供给段表与 wav 文件名（§3.3）；TD-2 契约对拍 |
| **RF-2** | 指纹拒存被「顺手的并发」绕过 | core 比对与 `os.replace` 间微秒窗口；人同时在终端改稿 | 竞争者集合论证（§2.2）；ack 层指纹（Spec 3/8 既有）兜住「批准旧版」；残余如实声明 |
| **RF-3** | 长任务 `apply-patch` 在桌面端失控 | 无超时模板是第一个例外；模型重配可能分钟级（A2）；SIGKILL/断电后 `.apply_patch.lock` 残留锁死 03.5 纠错链（`corrections.py:436-450`、`457`，红队 🟡-5） | 进度经 events.jsonl（Spec 2 既有）；app 退出不杀、重开恢复显示；不自动重试；失败后 UI 原样显示 stderr 尾部；锁残留由 `voice-info` 的 `apply_patch_lock` 字段可见 + 手工清除路径（§3.4，TI-11） |
| **RF-4** | 人时口径污染观测 | 桌面墙钟与终端 REPL 停留混入同一文件 | `source:"desktop"` 键区分（§3.2）；呈现注明「墙钟·观测量·不作门禁」；不回填历史期 |
| **RF-5** | 纠错输入框变成「自由发挥」 | UI 若自作主张加表单/校验，文法出现第二份实现 | 文法单源 `parse_correction`；parse/add 分离且 add 重解析（§2.3）；TC-8 对拍 |
| **RF-6** | INFO 被人当成判据 | 「列出来了」的心理压力等同 FAIL，逼人给每段写 `人物:`（正是 D18 拍板要防的）；别名含通用词（「老师」「一色」），首个真实期 21 段命中 9 段（43%），有 wallpaper 化风险（红队 🔵-3） | INFO 与 `Check` 结构分离（§2.5）；文案与 runbook 注明「提醒不是判据」；TC-2/MUT-2；**备案**：施工后首个真期回访命中密度，若 >50% 段命中且人反馈无视，按 D18 精神收敛别名集（通用词别名降级）或调整提示粒度，另行修订 |
| **RF-7** | 编辑器丢失未存改动 | 切期/关窗/崩溃时脏缓冲区蒸发 | 脏状态切期/关闭二次确认；`localStorage` 暂存草稿供恢复（便利层，不是真相源；userData 内，不碰 I1） |
| **RF-8** | 封板产物与终端格式漂移 | 自己实现 diff 或改参数 | core 内执行同一条 git 命令（§2.2）；TC-6 字节对拍；MUT-7 |
| **RF-9** | 人时双计 | 编辑器与决策卡同开、面板反复开关 | per-stop 单区间（§2.4）；TD-1/MUT-12 |
| **RF-10** | 老期无 draft，封板按钮困惑 | `2025-老视频` 等早期目录可能无 `02-script.draft.md`（约，未逐期核） | `/seal-script` 缺 draft 明确报错；UI 显示 core 原文；不自动补造 draft（诚实失败） |
| **RF-11** | 六指令「语义一对一」被按钮化悄悄改变 | 终端无确认的回滚/撤回若加确认，或反之，都是语义偏移 | §2.3 映射表冻结逐条对照；门禁 4 手验 |
| **RF-12** | spawn 环境里 git/ffprobe 缺席 | 固定 PATH（A3）；他机 `/usr/local/bin`（Intel Mac） | 白名单显式开口 + 子进程纯洁性断言（§5.2）；他机路径问题如实声明为已知边界（个人项目单机） |

---

## 附：行号核实自查表

2026-09-26 对照工作树逐行核实（HEAD `5baf6a0`；`uv run pytest` 1715 passed、`npx vitest run` 121 passed 基线实跑）。v0.1 表经红队一轮抓出 9 处偏差（🟡-7），v0.2 全量重核回填，并在表尾增「v0.2 补核」一节（本轮修订涉及的新引用全部逐行核实）。

| 引用 | 核实结果 |
|---|---|
| `tools.py:26-28` CREATIVE_WRITABLE_FILES | ✓ `{"01-topic.md", "02-script.draft.md"}`（26 行起 set 定义，27-28 两行成员） |
| `tools.py:62-125 write_episode_file` | ✓ 62 def；85-87 白名单检查；124 `paths.atomic_write`；scope≠creative 拒（77-79） |
| `paths.py:118-128 atomic_write` | ✓ 118 def、126 `.tmp`、128 `os.replace`（沿用 Spec 8 附表复核结论，本机抽核一致） |
| `cli.py:62-86 record_human_time` | ✓ 62 def；66 scout<0.1 过滤；85 `paths.atomic_write`（v0.2 按红队 🟡-7 更正：83 是 `minutes` 行） |
| `cli.py:301-311 run_voice_session` | ✓ 301 def；306 无稿直进 loop；310 记账 |
| `cli.py:455-460` 指令正则 | ✓ 455 `听 N`、456 `停`、457 `听`、458 `回滚 N`、459 `撤回 N`、460 `done`（均 `fullmatch`） |
| `cli.py:463-503 VoicePlayer` / `506 run_voice_loop` / `510-513` 无稿退 1 | ✓（463 `class VoicePlayer`；471 `play_all`；481 afplay/aplay 分支；493 `stop`） |
| `cli.py:540-548` 异读预检打印（540 调 `scan_heteronyms`、543 `[:10]`、544 `readings[:3]`） | ✓（v0.2 按红队 🟡-7/🔵-7 更正与补截断位） |
| `cli.py:588-594`「听 N」QuickTime | ✓（592 `open -a "QuickTime Player"`；594 `xdg-open` 分支） |
| `cli.py:624-651` done 分支 / `634-641` 云端提示（638 `cuda` 判定）/ `644-646` EOF→`"y"` | ✓（644 `input("是否立即执行增量重配 (--apply-patch)? [Y/n]")`；645-646 `except EOFError: confirm = "y"`；649 `tts.run(ep_dir, apply_patch=True)`） |
| `cli.py:1393` REPL `/voice` 入口；`1704-1705` 裸形态 `/voice` | ✓ |
| `cli.py:1612-1687` 裸形态分派块；`1716-1721` 非 TTY 未分派退 2（S3-R5） | ✓（1612 `if len(args) > 1`、1615 `/approvals`、1622 `/approve`、1650 `/reject`、1687 `sub_cmd = " ".join`——v0.2 按红队 🟡-7 更正） |
| `cli.py:1148` / `1291` 人时调用点 | ✓ |
| `corrections.py:162/393/429/453/729/813` | ✓（162 `parse_correction`、393 `load_corrections_raw`、429 `atomic_write`、453 `append_correction`、729 `revert_segment`、813 `retract_correction`） |
| `approvals.py:51-56` `_STOP_ARTIFACTS` / `:58-63` `_STOP_GATE` / `327` 时序闸 / `330-331` 02.5 非空闸 / `735-736` 封板提示 | ✓（51 `_STOP_ARTIFACTS` 起；327 `gate_st.st_mtime_ns < art_st.st_mtime_ns`；330-331 `if stop == "02.5": return gate_st.st_size > 0`——v0.2 按红队 🟡-7 更正；735-736 提示文案含 `git diff --no-index`） |
| `status.py:198-200` / `292-295` 02.5 分支与封板命令文本 | ✓ |
| `tts.py:146-171 parse_script` / `1686 write_review` / `1813-1821` apply-patch 互斥与 review 独立 / `2024` seg 命名 / `395-397 probe_duration(ffprobe)` / `1852、1930` 引擎同侧拦截 | ✓（146 def parse_script、163-167 段构造——v0.2 按红队 🟡-7 更正；2024 命名沿用 Spec 8 附表复核结论；395 def probe_duration、397 ffprobe argv） |
| `clips.py:167-175` 通道选择器注释 / `206` 人物正则 / `248-249` 锚点互斥 FAIL | ✓ |
| `vindex.py:52 CHARACTERS` / `137-155 alias_map`（缺席抛 SystemExit 在 143-146 与 148-149） | ✓（137 def；143-146 无文件 FAIL、148-149 无番 FAIL——v0.2 按红队 🟡-7 更正） |
| `bgm.py:288 animes_of` / `g2p.py:159 scan_heteronyms` | ✓ |
| `check_script.py:862-875` main 输出与退出码 | ✓（862 `checks = run(...)`；866 `mark = "PASS" if c.ok else "FAIL"`；875 `return 1 if failed else 0`） |
CLI 侧「确认类 EOF 默认否」先例 | ✓（`cli.py:674-676` 纠错落盘确认 EOF→`"n"`） |
| `jobs.py:56 HUMAN_TIME_RECORDED` 预留且无调用点 | ✓（grep 全仓库除定义行与注释外无引用） |
| `config/characters.json` 别名表结构（`{"<番>": {"<规范键>": [别名…]}}`，`_` 前缀为注释键） | ✓（实读文件头与「春物」段；`_note`/`_missing` 为注释键，alias_map 的 `tag.startswith("_")` 跳过逻辑一致） |
| Spec 8 §2.3/§3.1 规则 8-9/§3.4/§3.5/门禁 14/RF-12/RF-13/§2.11 | ✓（archive 文档原文比对） |
| Spec 9 RF-17（`cli.py:644-646` EOF 取「是」，处置归 Spec 11） | ✓（`2026-09-25-agent-session-protocol-spec.md` 正文与裁决表） |
| Spec 10 §6.4（与 Spec 11 边界：不预置控件；RF-17 归 Spec 11） | ✓（`2026-09-25-desktop-conversation-panel-spec.md` 原文比对） |
| `docs/runbook/02.5-human-review.md` 审查重点 3 条与封板命令 | ✓（实读全文） |
| `skills/write-script/SKILL.md:54` 说话人抽帧约束 | ✓（54 行附近「『某某说』之前必须抽帧确认说话人」段；issues archive D19 同引） |
| issues archive D18/D19（2026-09-25 拍板「不立判据、走替代处置」，两落点归 Spec 11） | ✓（`docs/dev/issues/archive.md:169-177` 原文比对） |
| `git diff --no-index` 行为（有差异 exit 1 输出 unified diff；无差异 exit 0 输出空） | ✓ 实测（/tmp scratchpad，2026-09-26；输出头 `diff --git a/draft.md b/script.md` + `index …` 行） |
| `env -i PATH=/usr/bin:/bin` 下 `git diff --no-index` 可用 | ✓ 实测（同 scratchpad；输出正常） |
| `ffmpeg`/`ffprobe` 位于 `/opt/homebrew/bin` | ✓（`which` 实测；Spec 8 §3.4 PATH 白名单之外，§3.4 开口的依据） |
| INFO 提示期望值 9 行（§2.5） | ✓ 实测（scratchpad 只读脚本跑真实期 `2026-07-30-春物-雪乃适合大老师`；对照实验：朴素子串匹配命中别名总数 12 → 22）**；v0.2 重跑：位置升序 + 去重规则下 4 个 PYTHONHASHSEED（0/1/42/random）输出 md5 全同 `02bd00b6…`，红队 🔴-1 预测的回填值（段 1 `雪乃、团子`、段 20 `雪之下雪乃、大老师`）逐一吻合** |
| npm 版本（2026-09-26 `npm view`） | ✓ `@codemirror/state` 6.7.6、`@codemirror/view` 6.43.13、`@codemirror/language` 6.12.4、`@codemirror/commands` 6.11.1、`@codemirror/lang-markdown` 6.5.2 |
| `desktop/src/` 现状（host 13 文件、renderer 9 文件、shared 12 文件；无 ScriptEditor/VoicePanel） | ✓（ls 核实——v0.2 按红队 🟡-7 更正 shared 计数；新增组件不撞名） |

### v0.2 补核（红队一轮修订涉及的新引用，2026-09-26 逐行核实）

| 引用 | 核实结果 |
|---|---|
| `corrections.py:38-47` Patch 九字段（segment/kind/word/heard/target_tone3/issue/action/scope/raw，无 `seed_pin`） | ✓（38 `class Patch`，字段逐一比对；🟡-1 属实） |
| `corrections.py:484` `seed_pin` 落盘时生成 | ✓（`entry["seed_pin"] = random.SystemRandom().randint(1, 999999)`） |
| `corrections.py:436-450` `apply_patch_lock`（无存活检测）/ `457` append_correction 锁检查 / `410` `save_corrections_raw` | ✓（436 def；440 存在即 SystemExit；446-450 finally unlink；457 同锁检查；🟡-3/🟡-5 属实） |
| `corrections.py:679-704` `backup_segments`（新建快照 mkdir + copy2 manifest 与 wav）/ `tts.py:1830-1833` apply-patch 持锁分支 / `1862` 调 backup_segments | ✓（679 def；681-698 mkdir/copy2；tts 1830 `if apply_patch:`、1833 `with corrections.apply_patch_lock`、1862 `backup_segments`） |
| Spec 8 archive line 729「将来拉起长任务必须先落 Spec 2 RF-7 的 SIGTERM 优雅关闭」 | ✓ 原文比对一致（🟡-5 属实） |
| Spec 8 archive line 256 / 1002「结构化打点须在终端 `/voice` 完成」 | ✓ 原文比对一致（🟡-6 属实）；真实入口 `tts.py:1686 write_review`、1819-1821 独立动作分支 |
| `cli.py:606` / `616` 回滚/撤回前 `player.stop()` | ✓（🔵-1 属实；红队写 605/614，实测 606/616，差 1 行，已在映射表采用实测值） |
| `cli.py:66` 的 0.1 分钟过滤仅对 `scout` 生效 | ✓（`if stop == "scout"`，🔵-2 属实） |
| `approvals.py:311-338` `_gate_valid` 三条件（存在、逐产物新于、02.5 非空） | ✓（🔵-4 属实；312 def、319-327 存在与时序、330-331 非空） |
| `record_human_time` 消费方只读 `minutes`/`stop` | ✓（`cli.py:377` 看板 sum `minutes`；红队复核 `status.py:126` 同结论） |
| `HUMAN_STOPS`（`approvals.py:35`）含四停机点 | ✓（红队复核一致，本机抽核 `{"02.5","03.5","05","09"}`） |
| 位置升序 + 去重的确定性 | ✓ 实测：PYTHONHASHSEED=0/1/42/random 四轮 md5 全同 `02bd00b6685ed2dd6ef1b03595bf91c20`；同进程 3 次复跑一致 |
| RF17-C1 目标分支（`cli.py:644-646`）无既有测试覆盖 | ✓ 红队核实，作者抽核 `tests/test_agent_cli.py` 的 EOFError 用例均不打 done 分支，改动无既有期望值破碎风险 |
