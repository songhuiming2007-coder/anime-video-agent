# Issues：已归档条目

活跃问题见 `README.md` 单表。本文件只存已解决、已作废或历史上有价值的问题。
**规则：解决即归档，不再删除。** 编号断裂比归档文件更害人。

---

## 2026-10-08：N60 复核通过归档

### [N60] 桌面端「建期带入」host 侧一次性标记（`carried`）的不变量零用例守
- 状态：**已解决**（✅ 2026-10-08；施工 `f485764` TH-D42 ⑥～⑨ + C-2(b) + C-3 注记、`dee2fde` TH-D42 ⑩ + ⑥ 拒因收紧；定向复核 🟡 `eb0851c` → 收尾复跑 🟢）
- 关联：`desktop/src/host/service.ts`（`carried`、`convSend`、`convResume`、`createEpisode`、确认切仓处 `carried.clear()`）；Spec 18 §9.6 偏差 1、「独立评审」🔵 C-1 / C-2，D42；Spec 18 §10、「N60 定向复核」与「N60 收尾复跑」
- 原记录（活跃表原文）：问题「桌面端「建期带入」的 host 侧一次性标记（`carried`）有两条不变量零用例守：发送失败不消费、切仓清空」；推进「D42-C 自设变异 V3（发送前就消费）、V4（切仓不清标记）全量 vitest 432 仍绿，e2e 只有两版 TX-7 走建期且都是成功路径。今天代码读下来是对的：失败不消费、切仓清空、resume 成功即消费。补法：TH-D42 ⑥「marker true → 首发失败 → 再发仍 `SESSION_CONTINUE`」、⑦「marker true → 切仓 → 同名期首发 `SESSION_NEW`」，可顺带 ⑧ ep 的 resume 消费；顺手可把 `createEpisode` 在 `migrated=false` 时 `carried.delete(name)`（C-2(b)，只在期目录被 app 外删除后同名重建时可达）。不阻断，碰 desktop 时一起做」
- 活跃表最后状态：**定向复核 🟡 打回（2026-10-08，D42-C 评审 session；见 Spec 18「N60 定向复核」）**：V3～V7 全杀、注记与实现一致，但自设变异 V8（未带入时 `carried.clear()` 清掉所有期的标记）全量 vitest 存活——补一条「A 带入 → B 未带入 → A 首发仍 CONTINUE」用例（R-1），另顺手收紧 ⑥ 拒因断言（🔵 R-2）；补完只需复跑 V8 + V5。**此前**：已修·待定向复核（施工方 D42-B session；记录见 Spec 18 §10）：补 TH-D42 ⑥～⑨，V3 / V4 / V5 / V6 均由对应用例断言杀死；顺手采纳 C-2(b)、补 C-3 注记。复核不能是本 session（D42-C 评审 session 可做）。原级别：备忘·用例缺口（D42-C 评审副产物，2026-10-08）
- 评审：D42-C 评审 session 定向复核——⑥～⑨ 断言的都是 spawn 模板序列、⑥ 拒因实测为 ready 前退出（`E_SESSION`）、C-2(b) 不动带入路径、Spec 10 §2.1 / §3.1 注记与实现一致；V3～V7 全杀，自设 V8（未带入时 `carried.clear()`）存活 → 🟡 R-1。补 ⑩ 后收尾复跑：V8 由 ⑩ L907 杀死、V5（删行）由 ⑨ L893 杀死，md5 一致；vitest 437、tsc 绿。产品代码只有 C-2(b) 一行 `else { carried.delete(name) }`。

---

## 2026-10-08：D42 评审通过归档

### [D42] 选题会话（idea）与建期写 `01-topic.md` 交互断层
- 状态：**已解决**（Spec 18；施工 `d1baacf` PR1 core、`907d280` 杀手断言收紧、`a7501d2` PR2 desktop、`31f380a` PR3 回填；2026-10-08 D42-C 独立评审通过）
- 关联：`pipeline/agent/session_log.py`（`acquire_idea_lease`、`EpisodeLease.clear`、`plan_repairs`）、`pipeline/agent/session.py`（`SessionHost.log_dir`、`resume_idea`）、`pipeline/agent/protocol.py`、`pipeline/agent/cli.py`（`migrate_idea_session`、`new --from-idea`）、`desktop/src/host/service.ts`、`sessions.ts`、`spawner.ts`、`desktop/src/renderer/App.tsx`；Spec 10 §2.5, D27 spec（`archive/2026-09-21-ava-entry-idea-scope.md`；原误写 ADR-0018，D42-R2 余-1 更正）, ADR-0021, Spec 18；N60
- 原记录（活跃表原文）：问题「选题会话（`idea` scope）与建期写 `01-topic.md` 交互断层：左栏「选题」入口缺少联网工具且零写权限，聊定后点「＋ 新建一期」不继承对话上下文（提示「讨论不会带入本期，请在这里重述」），无法在选题会话内一气呵成建期并由模型写入 `01-topic.md`」；位置「`config/agent/tools.json`（`idea` 工具表）、`config/agent/scopes/idea.md`、`desktop/src/renderer/App.tsx:413-426`、`pipeline/agent/tools.py`」；推进「**现状与痛点**：① 桌面端左栏首行「选题」（`idea` scope）是最自然的选题入口，但 `tools.json` 中 `idea` 仅配 `read_artifact`/`list_episodes`/`read_status`/`search_notes`，无联网考据工具（`web_search`/`web_fetch`/`crawl`）且无写权限；② 人在「选题」会话聊透张力与锚点后，点右上角「＋ 新建一期」只会建空期并切入全新的 `creative` 会话（`App.tsx:424` 显示「选题会话的讨论不会带入本期；需要的要点请在这里重述」），迫使人手动复述或复制粘贴才能让 `creative` 模型调 `write_episode_file` 落盘 `01-topic.md`；③ 当前临时绕法：不进左栏「选题」，直接点「＋ 新建一期」进 `creative` 会话聊选题并写 `01-topic.md`。**2026-10-06 加注**：方案 (a) 的「idea 补联网三件」已被 D43（所有模式开放全部工具，Spec 17）覆盖，D42 完整 spec 只剩「建期时把选题会话记录带进新期」，且须在 D43 施工之后施工。**后续重构候选方向（待立 spec 拍板）**：(a) 选题转正继承——给 `idea` 补齐联网只读工具，且在 `idea` 下点「＋ 新建一期」（或新增受控立项工具 `propose_new_episode` 经人审卡一键建期+写 `01-topic.md`）时将当前 `idea` 会话历史迁移/挂载为新期的首场 `creative` 会话，消除上下文断层；(b) 入口合一——评估是否取消独立的无期 `idea` 会话，统一收敛到带期目录的 `01 选题`（`creative` scope）。」
- 活跃表最后状态：**已修·待评审（2026-10-08，D42-B）**：施工 `d1baacf` PR1 core、`907d280` 杀手断言收紧、`a7501d2` PR2 desktop、PR3 回填；pytest 2075 passed（6 红与基线同一集合，数据盘未挂载）、vitest 432、Playwright 115 passed / 2 skipped；MUT-D42-a～h 全杀并逐条复核为断言级失败、md5 还原一致；门禁③ 真会话冒烟过（idea 聊一轮 → 再进历史在 → `--from-idea` 带入 → 新期不重述直接写 01-topic.md，弹卡批准落盘）；人 2026-10-08 两处裁决：首发改 host 侧一次性标记（TG-10 冲突）、截图按现状。偏差 7 条见 Spec 18 §9.6。下一步：D42-C 独立评审（不能是施工 session）；打包版真机并入 ACC。**此前**：Spec 18 定向复审 🟢 可动工（2026-10-08，D42-R2）：8 条全部核销，无需人裁决；2 条非阻断残余（余-1 文首「触及冻结面」行与两处「相关」列的 ADR-0018 误指随 PR1 清理；余-2 plan_repairs 修法为 id 级满足的病态边界加注）。详见 Spec 18 文首「定向复审」。沿革：v0.1 → D42-R 🟡 3🟡+5🔵 → v0.2 作者修订（🟡-2 人裁决 (A) 顺手修 `plan_repairs`）→ D42-R2 🟢。下一步：D42-B 施工（另开 session）。完整 spec 已立：`plans/archive/2026-10-08-idea-session-migration-spec.md`（Spec 18，2026-10-08 立文；只剩「建期迁会话」一半——联网三件已被 D43 覆盖）（2026-10-06 人裁决选型稿：方案 (a)、联网三件含 `crawl`、同一时刻只留一个选题会话）
- 评审：✅ 通过（2026-10-08 D42-C，评审人未参与立文、v0.2 修订、D42-R / R2 与 D42-B 施工；详见 Spec 18 文首「独立评审」）。§3.1–§3.3 逐条读码属实（idea `ep_dir` 恒 None、日志目录解耦；data/ 不可达拒启、`_idea` 缺失自建、租约失败显式报错；先建期后迁移、临时文件 + fsync + `os.replace`、清空失败退 5 + 手动指引、marker 恰一行；`plan_repairs` id 级满足；桌面端先结束选题会话、结束不了不建期、带入后清缓冲、idea-note 两态）；偏差 1（host 侧一次性标记）逐路径攻过，无误接别的会话段；全量复跑 pytest 2075 passed（6 红即数据盘未挂载集合）、vitest 432、tsc 绿、Playwright 115 passed / 2 skipped；变异亲跑 9 条——MUT-D42-a / e / f / g 与自设 V1、V2b 断言级杀死，自设 V2 崩在异常不算杀，desktop 自设 V3 / V4 存活（登记 N60）；冒烟：读 D42-B 帧证据 35 帧逐帧一致，并在 HEAD 上用真 LLM 独立复跑（新期记录与迁移前 `_idea` 逐字节相等、迁移后再进 idea 为全新会话、新期带入后弹卡写 01-topic.md）。
- 发现（无阻断，🔵）：C-1 偏差 1 的「失败不消费 / 切仓清空」零用例守（N60）；C-2 标记两处边界（发送确认超时但已送达 / `migrated=false` 不删旧标记）；C-3 Spec 10 §2.1 第 2 条与 §3.1 方法表未随 D42 注记；C-4 建期前无条件结束选题回合（符合 §3.3 ③，ACC 观察）。打包版真机（Spec 10 门禁 12 更新版）并入 ACC。

---

## 2026-10-08：D43 评审通过归档

### [D43] 工具按模式（scope）授权 → 所有模式开放全部工具
- 状态：**已解决**（Spec 17 / ADR-0027；施工 `45fc26b` PR1 core+测试、`e03e373` PR2 文档；2026-10-08 D43-C 独立评审通过）
- 关联：`config/agent/tools.json`、`pipeline/agent/tools.py`（`tool_names`、`build_tool_schemas`、`execute_tool`、`write_episode_file`、`validate_pipeline_command`、`NEEDS_EPISODE_TOOLS`）、`pipeline/agent/memory.py::apply_op`、`pipeline/agent/session.py::review_tool_call`、`config/agent/scopes/*.md`；Spec 17（`plans/archive/2026-10-06-all-tools-all-scopes-spec.md`）、ADR-0027（已通过）、ADR-0021、ADR-0023、ADR-0025、Spec 10 §2.5、D42
- 原记录（活跃表原文）：问题「工具按模式（scope）授权：各模式只见部分工具，且 `write_episode_file` / `write_memory` / `run_pipeline` 在实现里再按模式拒绝一次。人裁决改为**所有模式开放全部工具**」；位置「`config/agent/tools.json`、`pipeline/agent/tools.py`（`tool_names_for_scope`、`execute_tool`、`write_episode_file`、`validate_pipeline_command`）、`pipeline/agent/memory.py::apply_op`、`pipeline/agent/session.py::review_tool_call`、`config/agent/scopes/*.md`」；备注「**人原话（2026-10-06）**：「我建议不论什么模式，都开放所有 tool，具体原因我先不展开，这是我深思后的考虑」；同日确认 ① 实现里的模式检查一起去掉 ② 人审卡全部照旧。设计见 `plans/archive/2026-10-06-all-tools-all-scopes-spec.md`（Spec 17）：`tools.json` 收单表、删越 scope 拒绝与三处实现内检查、`run_pipeline` 合表、与模式无关的护栏全留、无期会话统一报「先建期」。§8 Q1–Q3 人 2026-10-06 裁决全部按建议（单表 / idea 下 `run_pipeline` 报先建期 / pipeline 不加记忆注入）。**2026-10-06 D43-R 红队一轮 🟡 修订后复审**：方向与机制主干成立（无第四处模式闸、护栏与模式无关、R1 探针 14 例全拦、R2/R3/R5 成立），阻塞项 5🟡——🟡-1 §3.4 放行 idea `acquire_propose` 与抓取卡链矛盾（批准后必报「先建期」，**需人裁决**三选一）、🟡-2 TA-6 杀不死 MUT-A8（弹卡≠落盘）、🟡-3 review 层越 scope 拒绝残留无杀手、🟡-4 §7 修订面漏 CHEATSHEET/D27 spec/Spec 9/Spec 1/impl spec §2.3 五处、🟡-5 shipped 变异矩阵 M20/M24/M29 锚点被拆除且 MUT-A1~A8 未要求登记；另 8🔵（含原型实跑失败清单 ≥14 份文件、合表语义写死、R4 批准记录落点实测等）。详见 Spec 17 文首。**2026-10-07**：人裁决 🟡-1 选 (A)（idea 下 `acquire_propose` 也报「先建期」）；人指定由立文人做作者修订，Spec 17 v0.2：12 条采纳、🟡-5 部分采纳（M24 退役、M29 改锚、M20 改锚不退役——「未注册名字拒绝」仍是现役护栏）。**2026-10-07 D43-R2 定向复审 🟢 可动工**（由 D43-R 红队 session 做）：13 条全部核销，🟡-5 中「M20 改锚不退役」的理由成立；原型实测 MUT-A8～A11 都被指定用例杀死；§3.4 拦截无缺口。另 6🔵 由施工吸收：R2-1 T17 不变量与 Q3 冲突，须按 Q3 退役并换等强断言，不许往 `memory.scopes` 加 pipeline；R2-2 §7 再补三处；R2-3 TA-1/MUT-A1 改在请求层；R2-4 T13② 补期目录；R2-5 §3.4 补两个边界；R2-6 先建期 reject 排在 dry-run 之前。人 2026-10-07 确认 🟢。下一步：D43-B 施工（人定 2026-10-08；提示词见桌面提示词文件「D43-B」块）→ D43-C 评审（红队 session 可做，不能是施工方）。**先于 D42-B 施工**（D42 方案 (a) 的「idea 补联网工具」被本条覆盖）」
- 评审：✅ 通过（2026-10-08 D43-C，评审人未参与立文、D43-R / D43-R2 两轮审查与 D43-B 施工；详见 Spec 17 文首「独立评审」）。① §3.1–§3.5 逐条读码核对：单表恰为注册集、反向分叉检查与 B3-r6 同点同形态、「先建期」reject 排在未注册之后 dry-run 之前（R2-6）、NEEDS_EPISODE_TOOLS 恰 4 个且两层共用同一常量与文案、`run_pipeline` 函数本体不拦、合表两条语义成立、`memory.scopes` 未加 pipeline；6🔵（R2-1～R2-6）全部核销。② 变异亲跑 5 条：MUT-A1 / A8 / A9 / A10 全杀、指定杀手逐条在红单内、无中止轮；自设矩阵外变异（PIPELINE_MODULES × ASSET_COMMANDS 人为重名）被 TK-9 杀死，还原 md5 对拍一致。③ 全量 pytest 复跑 `6 failed, 2060 passed`，6 红与基线同一集合（数据盘未挂载），无新红。④ 改写对照抽 9 行全部属实且有牙（M20 先建期版与 TK-4 均经变异实证）。⑤ 文档修订面抽 ≥5 处（三份 ADR、CHEATSHEET、Spec 7 四处、acquire-propose / web-fetch-links / impl spec / Spec 10），注记在位且措辞与现状一致。⑥ 门禁③冒烟帧证据（scratchpad `d43b/smoke-frames-final.jsonl`）逐帧核对：web_search ok=true、两个需期工具 ok=false 且文案逐字相符、受限串整轮 blocked（llm_calls=0）。⑦ diff 边界：PR1 31 文件全在 §10 PR1 范围、PR2 18 文件全为 docs/。
- 发现（无阻断，🔵）：C-1 §10 PR1 文件清单未列 `llm.py` / `jobs.py`（签名删除的必然调用点，§11 已如实记录；建议今后清单注明「签名变更的调用点随行」），见 Spec 17 文首发现表。

---

## 2026-10-06：桌面端可用性收口（续）

### [N59①] web_search 条数默认 / 上限改为 20（N59 的 ②③ 仍在活跃表：② 不做，③ 搁置）
- 状态：**已解决**（施工 `ae0b142`；D29-B 打回一处，复修 `a1125fa`；2026-10-06 定向复核 🟢 通过；仅 ① 部分）
- 关联：`pipeline/agent/web.py`（`SEARCH_DEFAULT_LIMIT` / `SEARCH_MAX_LIMIT` / `_Provider.max_count`）、`pipeline/agent/tools.py`（`web_search` 的 `limit` 描述）；Spec 15 §13 / §14 / §15 / §16，Spec 4 §3.2 修订注记
- 原记录：D29 门禁 7 人审「5 条远远不够；人物介绍还可以，人物剖析有问题」→ 人 2026-10-06 裁决默认与上限都改为 20，调研策略交给模型、不写进 harness。
- 取证（2026-10-06 终端，真实配置、limit=10、两家各单独查）：「一色彩羽 人物分析 性格 成长」「春物 一色彩羽 角色剖析 长评」两条查询两家全部 `truncated=true`；10 条里约一半仍是百科（维基 / 百度 / 游民 / bangumi 角色页），剖析类来源（豆瓣长评、bgm 日志、B站专栏、巴哈、个人博客、reddit 分析帖、知乎话题）只占另一半；查询词带「长评 / 剖析」时评论类明显上浮；两家结果重叠不到一半（如 B站专栏只出现在 Tavily），而现设计主家答了就不问备家。
- 候选方向与代价（③ 搁置的决策依据）：① 提高 `SEARCH_MAX_LIMIT`（每条 snippet 500 字，20 条一次约 1 万字进上下文且每轮重发）——已做；② 调研策略写进 scope / runbook——人裁决不写（原话见 Spec 15 §13）；③ 「两家都查、合并去重」——推翻 Spec 15「每家至多 1 次、不并发」与省额度取舍，Tavily 用量随之翻倍（见 N58）。
- 评审：D29-B 打回 🟡 B-1（默认 limit=20 时请求 Tavily `max_results = 21`，超出文档范围 `0 <= x <= 20`），另 🔵 B-2（带内错误先截后脱敏）、🔵 B-3（用例补丁打全局 `os.environ.get`，junit 下假绿）。复修后定向复核 ✅：tavily 单次上限 20、截住时以「返回数 == 请求数」判 `truncated`，真网请求体 `max_results = 20`；B-2 改为先脱敏后截断；B-3 只替换 `web` 模块的 `os`。6 条变异在 junit 口径下全杀（2 条自设），全量 pytest 2061 passed。遗留 🔵 C-1：T-P16 `exact` 腿删了一条长度断言，下次顺手补（Spec 15 §16）。

### [D29] web_search 默认端点（DuckDuckGo html）被盾，工具实际不可用
- 状态：**已解决**（Spec 15；施工 `4c7059b` PR1、`0a50d0e` PR2、`36fd369` PR3、`4184335` 门禁记录；2026-10-06 D29-B 独立评审通过）
- 关联：`config/agent/web.json`、`pipeline/agent/web.py::_SearchResultParser`；Spec 4, Spec 13, D28；Spec 15（`plans/archive/2026-09-29-web-search-provider-spec.md`）、N50、N58、N59
- 原记录（活跃表原文）：状态「**选型已拍板（2026-09-29：Exa 主 + Tavily 备）；Spec 15 v0.3（2026-10-06 作者修订，回应 D29-R2 的 4🟡 + 5🔵；人同日裁决 R2-9 / N50 接受、Q5 改为「替换」）；**同日定向复核 D29-R3 🟢 可动工**（2🔵 施工时顺手改）→ 施工 D29-A（PR0 人注册 Tavily key，终端与钥匙串两处放，变量名须以 `TAVILY_` 开头）**；**2026-10-06 D29-A 施工中：PR0 ✅（key 两处已放；`exa_mcp` / `tavily` 真实响应入 fixture）、PR1 ✅ `4c7059b`（core provider 链 + 默认链；本机 `web.local.json` 的 search 段经人当场确认已替换为新链；偏差 8 条见 Spec 15 §12，含人裁决的 `exa_mcp` 固定 `objective` 与 `exa_api` 暂不注册）、PR2 ✅ `0a50d0e`（description；终端门禁 4、5 真机过；N58 用量观察）、PR3 ✅ `36fd369`（桌面端从钥匙串注入 web 检索密钥；Spec 10 §2.9 已加修订记录；打包版上人手跑桌面端门禁 4、5 均过：主家答 `exa_mcp`，主家端点指错后由 `tavily` 答）。**已修·待评审（D29-B）**；门禁 7 人审 2026-10-06：人物介绍类「还行」、人物剖析类不足（5 条远远不够），后者登记 N59；评审通过后跑 S13-G8**」；一句话「web_search 默认端点（DuckDuckGo html）被盾，工具实际不可用」；推进 / 线索：2026-09-26 实测（Spec 13 红队一轮 🟡-1 牵出）：GET `https://html.duckduckgo.com/html/` 返回 **202 + anomaly/challenge 页**、零结果，`search_web` 抛「搜索解析为空」。影响：升级链第一级探测失效，asset/creative 联网研究只能直抓已知 URL 或站点 API（Spec 13 门禁 8 起手已改走公开 API）。注意：解析器钉死 DDG DOM（`result__a`/`result__snippet`），换 provider = 端点 + 解析器一起换，不是改一行配置。**方向（2026-09-26 人原话）「参考 pi 的 web search 实现，不要在意国内网络环境、我有代理」**：即照 pi 的多 provider 路线做（provider 可插拔、配置驱动），不因墙内直连迁就选型（**此解读 2026-09-26 人确认属实**）。⚠️ **本行曾误记为「弃 HTML 爬取改走搜索 API——Brave 主力 + 博查 fallback」，2026-09-26 由人更正：provider 从未拍板**（此处已改正，勿再沿用）。可确定的部分：HTML 端点（DDG）已实测被盾；**360 方案（同为 HTML 爬取）已被人否并 revert**（`d4e8bad` → `aeef32d`），与否决理由（2026-09-26 人原话）——「**用 360 搜索不如不用**」，即**检索质量差到不如不搜**：否的是**质量**不是网络可达性；key 走 `web.json` 已有的 `api_key_env`；`_SearchResultParser` 随之退役。**provider 选型由 D29 施工 session 先出调研对比（含 pi 支持清单、各家 key/配额、检索质量、代理下可用性）再报人拍板；首要判据是检索质量，国内可达性不作判据（有代理）。** **另为 Spec 13 门禁 8 复跑前置**（2026-09-26 验收拍板：先修 D29 再复跑）；施工时点按 2026-09-26 人拍板**顺延到二期 21 个 session 收官之后**（不在二期施工范围） **2026-09-29 调研（只读，未改代码，待人拍板）**：① **pi 实际路线**——`~/.pi/agent/web-search.json` 只有 `provider:"all"` 与 summaryModel，**没配任何 key**，靠 `pi-web-access` 0.33.0 的 auto 链（SearXNG→Exa→OpenAI→Brave→Parallel→Tavily→…共 20+ provider 可插拔，另含 DDG 无 key 兜底），实际落到**无 key 的 Exa MCP**（`POST https://mcp.exa.ai/mcp?tools=web_search_exa`，JSON-RPC over SSE，头 `x-exa-source`，429 时提示加 key）。② **代理下实测**（本机 127.0.0.1:7897，3 条查询各 6 结果）：「一色彩羽 角色介绍」得 Gamersky/anibase/搜狗百科/百度百科/Hikarinagi/博客；英文「Iroha Isshiki」得 MAL/Fandom/LNDB；「楪祈 罪恶王冠」得 中文百科全书/动漫大风堂/番组百科/百度百科/维基/萌娘——**中日英三类都给出角色资料页，质量明显好于 DDG 之前的零结果与 360**，可直接喂 Spec 13 门禁 8。③ 各家额度（WebSearch 2026-09 查，价格以官网为准）：Exa 带 key 注册送 $10、每月 1 日重置为 $10（约 1400 次，$7/千次），无 key MCP 有限流；Tavily 每月 1000 credits 免费、无需信用卡（basic=1 credit）；**Brave 2026-02 已取消免费档**（$5 预付额度约 1000 次，绑卡后转计费）；博查/Serper 等未实测。④ **建议**：主力 = Exa（先用无 key MCP，遇 429 再申请 key 走 `api_key_env`），fallback = Tavily（免费 1000/月）；provider 做成配置驱动（`web.json` 加 `provider` 字段 + 每家一个 ~30 行的适配器，返回统一 `{title,url,snippet}`），`_SearchResultParser` 退役。风险：无 key MCP 是 pi 借用的公共入口，无 SLA，429/下线后必须有第二 provider；结果含正文摘录 ~1–2k 字/条，需截断防占上下文。 **2026-10-06 Spec 15 红队一轮（D29-R）：🟡 修订后复审**——5🟡（桌面端会话拿不到 `TAVILY_API_KEY`；`urllib` 跟随 POST 302 时转发 `Authorization`/`x-api-key`，本机探针已证；PR1/PR2 顺序与 Q5 矛盾会打断 web_fetch；删 `WebConfig.api_key` 打断 fetch 脱敏；Q3 与「免 key 入口被收紧」冲突需人澄清）+ 6🔵，详见 Spec 15 文首裁决表。 **2026-10-06 Spec 15 定向复审（D29-R2）：🟡 再修订**——R2-1 本机 `web.local.json` 整份取代 `web.json`，按 v0.2「删 search 段」迁移后 `load_web_config → None`（scratchpad 探针已证），🟡-3 原指控复发；R2-2 web 配置可指名 LLM 或其他 `*_TOKEN` 密钥外发给 Tavily / Exa；R2-3 TH-W2 的 `ready.llm` 在 host 测试里是脚本写死的，M18 存活；R2-4 T5a/T5b/T17 依赖旧 search 配置，须在 PR1 等强改写（Spec 16 的 MUT-D7 杀手）。详见 Spec 15「定向复审」。
- 评审：✅ 通过（2026-10-06 D29-B，评审人未参与 Spec 15 的写作、任何一轮审查与施工；详见 Spec 15 §14）。① 全量 `uv run pytest` 2059 passed；desktop `tsc` 过、`vitest` 422 条全绿；② 变异 10 条亲跑（M5 / M13 / M10 与自设 V1～V5、desktop D1 / D2），全部由指定用例的断言杀死，md5 一致；③ 契约四键与 Spec 4 §3.2 一致，`tools.json` / scopes / runbook / `llm.py` / `session.py` 的 diff 为空，`_guard_url` / `_scrub` / `assert_egress_boundary` 一行没动；④ 终端真网复跑：主路径 `exa_mcp` 20 条带摘录；主家端点指错后由 `tavily` 答；key 未设 → 「tavily: 需要环境变量 TAVILY_API_KEY（…两处放法）」，主家正常时不受影响；key 错误 → 401 直接失败并列出此前尝试；⑤ 凭据审计：diff、提交信息、fixture、已跟踪文件对真 key 都是 0 次命中。桌面端门禁 4、5 没有重跑，依据是施工方存证与对其 `session.jsonl` 的被动核对。
- 发现：🟡 B-1 记在 N59（Tavily 请求 21 超文档上限 20，属 N59 改动，不影响本条归档）；🔵 B-2 带内错误先截断后脱敏，key 跨 200 字边界时前半段会漏（现无暴露面：带内错误只来自免 key 的 exa_mcp）；🔵 B-3 `test_tp9_web_key_env_names_reads_no_values` 替换了进程全局的 `os.environ.get`，加 `--junitxml` 跑时杀死会变成 pytest 内部崩溃。两条 🔵 见 Spec 15 §14，顺手修即可。

### [D30] 期目录处在 03 / 03.5 时会话第一轮即 [BLOCKED]（自家 runbook 字面路径撞出网断言）
- 状态：**已解决**（Spec 16 / ADR-0026；施工 `d5d94bf`；2026-10-06 D30-C 独立评审通过）
- 关联：`docs/runbook/03.5-voice-check.md`、`pipeline/agent/tools.py:54-59`、`pipeline/agent/llm.py:255`；Spec 4, Spec 13；Spec 16、ADR-0026、N52、N53、N55、N56、N57
- 原记录（活跃表原文）：期目录处在 step=03.5 时任何会话第一轮即 [BLOCKED]：runbook 文本含字面 `03-audio/manifest.json` 撞受限出网子串断言——2026-09-26 Spec 13 门禁 8 冒烟复现（绑定 step=03.5 期目录的会话必炸，payload 含该 runbook 文本即触发 `RESTRICTED_EGRESS_PATTERNS` 子串匹配）。候选：① runbook 改写避开字面路径（回避式不治本，别的文档会再撞）；② egress 断言收窄匹配语义（治本，动 Spec 4 冻结面，需独立 spec/ADR）；③ 维持登记人工绕行。验收评审倾向 ② **2026-10-06 D30-A 复现（临时仓库副本、假 LLM 端点、零出网）**：step=03.5 与 **step=03** 两期首轮都 `turn_finished{stopped:"blocked",llm_calls:0}`；逐条断言定位到 origin=`injection` 的工序层注入消息，命中 `docs/runbook/03.5-voice-check.md:7` 与 **`docs/runbook/03-tts.md:16`**（原登记只有 03.5）。注入面全扫：四条模式只有 `03-audio/manifest.json` 出现、只在这两份 runbook。附带发现见 N52（桌面端看不到拦截原因）、N53（作业输出尾巴带路径，未实测）。方案对比与测试/变异清单见 Spec 16。
- 评审：✅ 通过（2026-10-06 D30-C，评审人为 Spec 16 的一轮红队兼定向复审人，未参与修订与施工）。① 退回修复亲跑 TD-1，两期都红在命中串 `'03-audio/manifest.json'`，还原后 md5 一致、转绿；用 `git archive` 取修前 / 修后两份临时仓库副本，协议子进程 + 只监听 127.0.0.1 的假 LLM 真会话复跑：D30-S03 / D30-S035 修前 `blocked`、0 个请求，修后 `done`、恰 1 个请求，请求体含对应 runbook 原文，`prompt_chars` 9192 / 14031 与 D30-A 一致；全量 `2003 passed`。② D30-R 最坏样例在修后代码上逐条实跑：该拦的全拦，读域过滤 13 例全部符合预期。③ 自设变异 4 条：V1（重叠判定）、V3（不按 resolve 后路径判）、V6（首轮注入不进 (a)）分别被 TD-4、TD-5、TD-1e 以断言杀死；V4（(b) 不收常驻三件）存活，登记 N57。④ 文档面齐全；⑤ diff 边界合规。详见 Spec 16 §13。
- 发现（无阻断，🔵）：(a) 生产入口的常驻层只靠 (b)，零用例守（N57）；(b) Spec 16 §5.3「规程原文逐字抄进工具参数会被豁免」与实测不符：工具参数在请求体里转义了两次，实际被拦，方向更严，记在 Spec 16 §13。

### [D37] 原生确认框未经人手点击即记为「批准」
- 状态：**已解决**（定性 `dd3742d`，人裁决方案 (b) `288d1c5`，施工 `b83c692`、`86df0fb`；2026-10-06 D37-C 独立评审通过）
- 关联：`desktop/src/main/index.ts`（`dialog.showMessageBox` + `signal` 的 resolve 语义）、`src/main/confirm.ts` 队列；Spec 10 §2.4 第 5 层、H-1/H-2、门禁 4 与门禁 12
- 原记录（活跃表原文，含定性与施工回填）：2026-09-27 M9 打包版实测**两次**：① `#3` 抓取卡——19:49:36 dump 到 `AXSheet`（正文正确），19:49:37.6 `approvals.jsonl` 已记 `decision: y`，全程我只按过卡片按钮、没碰确认框；② `#7` 抓取卡——确认框正文已读（与卡片逐字一致），我发出的一次真实鼠标点击落在**非 ava 的前台应用**上，随后决策仍记为 `y`。两次的共同点是「确认框消失 + 决策=批准」，但触发事件未确证（候选解释：`signal` 被 abort 时 Electron 以 `response: 0`（=批准按钮）resolve → fail-open）。**必须由人在机器前、无自动化介入地手点一次复现定性**；若成立，则 browser/抓取卡的「第二道闸」形同虚设。**2026-09-27 代码侧排查（只读 + scratchpad 探针，Electron 44.4.5，未改代码）**——实测事实：① 链路只在 `showMessageBox` 返回 `response===0` 时写 `y`（`main/index.ts` show → `confirm.ts` broker → `host/confirm.ts` → `sessions.answer` 过 `needsNativeConfirm` 后才写 `answer`）；② broker 只在 `hostExited()` 时 abort，abort 后 `active!==ac` → **不回任何 confirm-result**；③ 探针（同 options、挂父窗口）：`signal` abort → `response=1`（取消）；父窗口 `close()` → `1`；`app.hide()` 失焦 3 s 内不 resolve、之后 abort → `1`；父窗口 `destroy()` → **永不 resolve**（非 fail-open，但 host 的 `await confirm` 无超时 → 该卡 `answering` 永久占用、再答 E_BUSY，另一个缺陷，待登记）。**候选解释「abort 以 0 resolve」被实测否定**。键盘：Escape/Space 实测 → 取消；Return **结论不定**（1 次 `response=0`、焦点确认在探针窗口的 2 次均不 resolve；另 3 次 System Events 按键落到了其他前台应用，探针作废——键盘探针会误伤前台应用，**不再自动化**）。与 spec 的对照：§2.4 第 5 层要求「默认与取消按钮都是『取消』」，若 Return 能触发「批准」即违约；§2.10 退出确认同款写法，须一并复核。下一步＝人手复现配方（见施工提示词「收尾批进度」D37 行），定性后再修 **2026-10-06 人手定性（D37-A；打包版 HEAD `239f51f`、`desktopDirty=false`，Electron 44.4.5，macOS Darwin 27.0；临时仓库副本 scratchpad `d37-repo`，期 `D37-TEST`；LLM 走 `agent.local.json` 的本地代理；全部点击与按键由人完成，Claude 只读 `session.jsonl` / `_agent/approvals.jsonl` 与 CGWindowList，零键鼠事件）**——**实测事实**（时间为本地 UTC+8）：① 全屏下点抓取卡「批准」→ 确认框弹出（CGWindowList：260×298 独立窗口，几何中心与父窗口重合）；人切到其他应用（终端）回消息、再切回 → 框仍在、无新行；② 人先点框内文字再按 Return → 文字被全选、框仍在、无新行（焦点落文本区，**不计为有效 Return 样本**）；③ 鼠标点「取消」→ 框消失、卡仍在、无新行；④ 再点卡片「批准」、框弹出后不点任何处直接按一次 Return（AVA 在前台）→ **无任何反应**，框仍在、无新行；⑤ 同一框按 Esc → 取消、无新行；⑥ §2.10 退出确认：有 1 张未答卡时 Cmd+Q → 框弹出 → Return → **无反应、未退出**；鼠标点「取消」→ 未退出（PID 存活）；⑦ 对照组：卡片「批准」→ 框内鼠标点「批准」→ 15:56:07.682 `acquire_fetch #1 … y`；⑧ 窗口模式（退出全屏）复测：框形态与全屏相同（人目视为屏幕正中的独立小窗，**未用 AX 取证是否 sheet**——本 session 禁止触碰确认框的自动化），Return 无反应、Esc 取消。`_agent/approvals.jsonl` 全文 5 行：15:43:21 propose y、15:56:07 fetch #1 y（⑦ 对照）、16:03:20 propose y、**16:03:24 fetch #2 y（卡开后 3.2 s；人自述是误以为要一路点批准、用鼠标点了框内「批准」，属人手批准，不计复现）**、16:04:19 propose y；其余所有非人手点「批准」的路径（等待 30 s、切走切回、Return、Esc、鼠标取消）**零写入**。**机理判断（猜测，未证实）**：`defaultId: 1` 在本机没有把 Return 绑到「取消」——Return 不绑任何按钮，所以既不放行也不取消；09-27 的两次 y 都发生在有自动化介入时（AX dump 到 `AXSheet`、System Events 按键/鼠标事件落点不确定），而今天的框人目视为居中窗口、与当时记录的 `AXSheet` 是否同一呈现未确证；无自动化介入的全部样本均未复现。**修复方向（交人裁决，D37-B 据此施工）**：(a) 查清 Return 为何不落到 `defaultId` 并修成 Return = 取消（两处框一致，打包版人手复验）；(b) 接受「Return 无效、Esc = 取消、只有鼠标批准」为安全等价物，改 Spec 10 §2.4 第 5 层 / §2.10 措辞；D40 不受影响照修。09-27 的未解释 y 不再追（复现需重新引入自动化，违反第 5 条纪律），在 D37-C 评审中登记为残余风险。**顺带**：N44 Dock 点图标 → 窗口回前台并可直接输入（人确认）；输入法：拼音选词上屏后再按 Enter 正常发送，「组字中按 Enter」未单独测；发出内容带尾随换行（见 N51）。 **D37-B 施工（2026-10-06，与 D40 同批）**：按钮 / `defaultId` / `cancelId` 不变，抽到 `main/confirm.ts`（`approveBoxOptions` / `quitBoxOptions` / `APPROVE_BUTTON`）；对话框抛异常改回 `ok:false`（此前只撤状态不回复，host 的 `await confirm` 会永久挂起）；Spec 10 §2.4 第 5 层、§2.10 第 4 步改措辞 + §2.10 前加修订记录。新增 TH-22b（两处按钮语义 + 静态核对 `index.ts` 只经构造函数取选项）、TH-22c（非人手路径一律 ok:false）、TH-18b（D40 端到端）。变异 6/6 KILLED（M1 windowGone 不回包、M2 异常分支不回复、M3 抓取框 defaultId=0、M4 退出框 cancelId=0、M5 closed 不调 windowGone、M6 windowGone 不 abort），还原 md5 一致。验证：vitest 407 passed、tsc 通过、playwright 115 passed / 2 skipped；`release-build` @ `b83c692`（`desktopDirty=false`）+ `vitest run e2e-packaged` 11/11。**未实测**：打包版上真实 `destroy()` 窗口的路径（无法人手触发；以 broker 单测 + host 端到端代替）；打包版人手复验（Return / Esc / 鼠标取消 / 鼠标批准 / 退出框 Return）留给 D37-C 当场做。
- 评审：✅ 通过（2026-10-06 D37-C 独立评审，与 D37-B 施工方不共用上下文）。① **打包版人手复验**：`release-build` @ `a2da050`（`desktopDirty=false`，`e2e-packaged` 11/11）；临时仓库副本（scratchpad `d37c-repo`，期 `D37C-TEST`，素材模式）；全部点击与按键由人完成，Claude 只被动读 `_agent/approvals.jsonl` 与 `session.jsonl`，零键鼠事件。逐步（本地 UTC+8）：17:45:18 人批准提案卡（`acquire_propose y`）→ 抓取卡 #1 打开；(1) 卡片「批准」→ 框弹出后不点任何处按 Return → 无反应、框在、卡在、零写入（17:45:49 核）；(2) 同一框按 Esc → 框消失、卡在、零写入；(3) 再批准 → 鼠标点「取消」→ 界面「E_STALE：已在确认框取消」、卡在、零写入（17:46:36 核）；(4) 再批准 → 鼠标点「批准」→ 17:46:52.517 恰一条 `acquire_fetch #1 … y`（latency 93.9 s），卡关闭；(5) 抓取卡 #2 未答时 Cmd+Q → 退出框按 Return → 无反应、AVA 主进程存活；(6) 鼠标点「取消」→ 不退出（17:47:43 进程存活，approvals 全文 2 行）；(7)（D37-A 未测，本次补）Cmd+Q → 退出框按 Esc → 框消失、不退出；(8) Cmd+Q → 鼠标点「退出」→ 17:48:07 卡 #2 `voided(interrupted)`、`turn_end`，进程退出。八步全部与 Spec 10 §2.4 第 5 层 / §2.10 修订语义一致。② **用例逐条读**：TH-22b 钉住两处 `buttons` / `defaultId` / `cancelId` 与 `APPROVE_BUTTON`，并静态核对 `main/index.ts` 两处只经构造函数取选项、两处 `r.response === APPROVE_BUTTON`、`closed` 后执行 `windowGone()`；TH-22c 在 broker 层断言 `ok:false`（show 回 false / 抛异常 / `windowGone` 对已打开与排队中的各回一次且迟到的 true 不回 / 之后能重答 / 空闲不回）；TH-18b 用真实 host 与 main 两端 broker 端到端断言 `E_BUSY` → `windowGone` → `E_STALE`、answer 零行、卡仍打开、重答恰 1 行。断言的都是 ok:false、零写入与卡仍打开，不是「没报错」；抛异常路径的「零写入、卡仍打开」由 host 共享的 `if (!ok) fail("E_STALE")` 分支承担（TH-18「桩返回 false」守，见 V4）。③ **变异 5 条亲跑，全部自行设计**（非施工方 M1–M6 的复跑；`PYTHONDONTWRITEBYTECODE=1 npx vitest run <文件>`）：V1 `windowGone` 只回已打开的、漏回排队中的 → TH-22c windowGone 用例 `expected [['boot1:1',false]] to deeply equal [[…],…(1)]`；V2 对话框异常分支改回 true（fail-open）→ TH-22c 抛异常用例 `expected [['boot1:1',true]] to deeply equal [['boot1:1',false]]`；V3 去掉 resolve 分支的迟到守卫 → TH-22c「迟到的 true 不回」与 TH-22「host 退出零回复」两条断言；V4 host 忽略 `ok:false`（`if (false && !ok)`）→ TH-18 三条 + TH-18b `expected undefined to be 'E_STALE'`；V5 退出框 `defaultId: 0` → TH-22b 退出框用例 `expected +0 to be 1`；均还原 md5 一致（`confirm.ts` 7f5a41db…、`sessions.ts` c5122332…），`git status` 干净。全量 `npx vitest run` 410 passed、`tsc` 通过。④ **diff 边界**：`b83c692` 只动 `main/confirm.ts`、`main/index.ts`、`tests/host/confirm.test.ts`、`tests/host/sessions.test.ts` 与 Spec 10 / README / plans / issues 文档，合规。
- 发现（无阻断）：🔵-1 退出确认框的 `showMessageBox(...).then` 无 reject 分支，且同样挂在主窗口上——抛异常或父窗口 destroy 时 `quitPhase` 永久停在 `confirming`，此后 Cmd+Q 全被拦、只能强退；方向安全（不会误退），但与「非人手路径一律回落取消」口径不一致，登记 **N54**。🔵-2 `windowGone` 的真实 `destroy()` 路径在打包版上无法人手触发，以 broker 单测 + TH-18b 代替（施工已如实申报），接受。**残余风险（登记，不追）**：2026-09-27 两次未解释的 y 都发生在有自动化介入时；无自动化下 D37-A 与本次的全部非人手结束样本（等待、切走切回、Return、Esc、鼠标取消）零写入；复现需重新引入自动化，违反施工纪律第 5 条。

### [D40] 原生确认框所属父窗口被 destroy 时卡片永久卡在 answering
- 状态：**已解决**（随 D37-B 施工 `b83c692`；2026-10-06 与 D37 同场 D37-C 独立评审通过）
- 关联：`desktop/src/main/index.ts`（`dialog.showMessageBox`）、`src/main/confirm.ts` 队列、`host/confirm.ts`；D37, Spec 10 §2.4
- 原记录（活跃表原文，含施工回填）：**已修·待评审**（2026-10-06 随 D37-B：主窗口 `closed` → `mainConfirm.windowGone()` 对已打开与排队中的确认请求各回 ok:false；选 broker 补回包而非 host 超时，理由见 Spec 10 §2.10 前修订记录）——2026-09-27 D37 代码侧探针（Electron 44.4.5，scratchpad，未改代码）实测：父窗口 `destroy()` → 永不 resolve（`close()`/abort/`hide()` 均 resolve 为取消，非本条）。不是 fail-open（不会记批准），但 host 侧无超时，卡片停在 answering、之后所有重答返回 E_BUSY，只能重启。触发面窄（窗口被强制销毁而 host 不退出），优先级低于 D37 定性。**修法待定**：broker 对 `destroy` 事件补一个 ok:false 回包，或 `await confirm` 加超时 fallback 取消。
- 评审：✅ 通过（见上条 [D37] 评审 ②③）：TH-18b 端到端守住「挂起期间重答 E_BUSY → `windowGone` → E_STALE、零写入、卡仍打开 → 重答恰 1 行」；变异 V1（漏回排队）、V3（迟到结果不拦）由 TH-22c 以断言杀死；`win.on("closed")` 调 `windowGone()` 由 TH-22b 静态核对。真实 `destroy()` 打包版无法人手触发，接受以单测代替。同类缺口在退出确认框上仍在，另登记 N54。

### [N39] repoRoot 切换窗口内新起的读取拿到「新代号 + 旧仓库根」
- 状态：**已解决**（施工 4ca9a03；2026-10-06 独立评审通过）
- 关联：`desktop/src/host/service.ts::requestRepoRootChange`（`repoGen += 1` 早于 `resolveRepoRoot()`）；Spec 8 §2.9/§4, D32
- 原记录（活跃表原文，含施工回填）：repoRoot 切换窗口内新起的读取拿到「新代号 + 旧仓库根」。2026-09-28 D32 施工读码发现（未构造出可见现场）：切换开始即递增代号，仓库根要到 `resolveRepoRoot()` 才换；这段窗口里 30 s 定时的 `refreshEpisodeStatuses` 若起跑，会拿新代号去读旧根，结果通过代号比较写进 `summaries` 并推 `episodes.summary`（侧栏摘要，不进期 snapshot）。纵深缺口。候选：`resolveRepoRoot()` 后再递增一次代号 / 切换中不起新读取 / 读取记下发出时的根并在回包时比对；前两者触及 Spec 8 代号语义，须人拍板 **2026-09-29 人拍板「先试候选 2」并已施工**：`refreshEpisodeStatuses` 与 `loadStatus` 入口加 `if (this.switching) return`（切换窗口内不起新读取；`probe` 不加——切换自己在窗口内调它；切换收尾原本就 `void refreshEpisodeStatuses()` 补刷一次）。用例 TH-13b：占住一个在途读取让窗口保持打开，窗口内调 `refreshEpisodeStatuses` 与活跃期 `tickActive`，断言 STATUS spawn 数不变；窗口关闭后恢复。变异 2/2 被该用例以断言杀死（去 refresh 守卫 / 去 loadStatus 守卫，均 `expected 3 to be 2`）。vitest 378 全绿、tsc 通过。**未做**：未构造出用户可见现场（机理成立，后果限于侧栏摘要）；未走 e2e。
- 评审：✅ 通过（2026-10-06 独立评审 N39-B，与施工方不共用上下文）。① 读码：守卫只在 `refreshEpisodeStatuses` 与 `loadStatus` 入口（`if (this.switching) return`）；`probe` 未加且理由成立——切换在 `resolveRepoRoot()` 之后才 `await this.probe()`，此时根已是新根、代号已是新代号，组合一致；全文件带代号的读取只有这三处（`const gen = this.repoGen`），无遗漏；切换窗口唯一的让出点是等在途 spawn 的 `while` 循环，循环结束到 `resolveRepoRoot()` 之间全是同步代码，不存在第二个窗口。② 补刷：`finally` 复位 `switching` 后 `void this.refreshEpisodeStatuses()`，窗口内被跳过的读取不会让侧栏永久陈旧；即使补刷因 `reach` 未就绪提前返回，30 s 定时刷新兜底。③ 变异亲跑（`npx vitest run tests/host/sessions.test.ts -t TH-13b`）：去 refresh 守卫、去 loadStatus 守卫 → 均被 TH-13b 以 `AssertionError: expected 3 to be 2` 杀死，还原 md5 一致、复跑绿。④「未构造用户可见现场、未走 e2e」不必补：后果限于侧栏摘要至多一个刷新周期，TH-13b 用真实 `HostService` 与可控闸门精确卡在窗口内，比 e2e 定时撞窗口更确定。
- 发现（无阻断，🔵）：(a) 删掉切换收尾的 `void this.refreshEpisodeStatuses()`，host 全部 164 个用例仍绿——补刷是 N39 前就有的行为，本修依赖它却无用例守，后果是切仓后侧栏最多空 30 s；(b) 把 refresh 守卫改成永久 `return` 时，TH-13b 是在前置 `waitFor(() => calls > 0)` 超时（8 s）后失败，「窗口关闭后恢复读取」那条断言本身没有独立守住的用例（一般刷新路径只靠 TH-13b 的前置步骤间接覆盖）。两条都不是 N39 引入，记此备查。

## 2026-09-29：桌面端可用性收口

### [N49] 桌面端会话里 agent 起的作业找不到 ffmpeg / ffprobe
- 状态：**已解决**（施工 882a142；2026-09-29 独立评审通过）
- 关联：`desktop/src/host/spawner.ts`（会话 spawn 用 `childEnv(process.env)`，未开 `homebrewPath`）、`pipeline/jobs.py`（作业 `env = dict(os.environ)` 原样继承）、pipeline 各处按名字调 `ffmpeg`/`ffprobe`；Spec 8 §3.4 / RF-3、Spec 10 §3.4、D39
- 原记录（活跃表原文，含施工回填）：桌面端会话里 agent 起的作业找不到 ffmpeg / ffprobe：配音时长复核、排片探测、渲染、质检、封面、补料探测在桌面端全会失败（终端 `ava` 不受影响）。Spec 8 为 Finder 启动的 GUI app 把子进程 PATH 固定为 `/usr/bin:/bin:/usr/sbin:/sbin`，并在 RF-3 写明「加模板时必须复核」；只有 `SEAL_SCRIPT`、`RUN_TTS_APPLY_PATCH` 两个模板开了 `/opt/homebrew/bin`（A3）。Spec 10 新增的 `SESSION_*` 模板没开，而会话里 `run_pipeline` 起的作业经 `jobs.py` 原样继承这个 PATH。**实测（2026-09-29）**：`env -i PATH=/usr/bin:/bin:/usr/sbin:/sbin` 下 `.venv/bin/python` 调 `ffmpeg`、`ffprobe` 均 `FileNotFoundError`，`git` 正常（`/usr/bin/git` 在）；本机 ffmpeg 只在 `/opt/homebrew/bin`。既有 e2e 的真实 core 用例用的是桩作业，所以一直没暴露。**未做**：没有用真实会话跑一次需要 ffmpeg 的作业来端到端证实。**候选修法**：`SESSION_*` 与 `SEAL`/`TTS_APPLY` 一样开 homebrew PATH（agent 工具表是闭集、无 shell 工具，暴露面小，但属 Spec 8 冻结的环境白名单，须人确认）。**施工（2026-09-29，人拍板候选 (a)）**：① 端到端复现——`e2e/sessionReal.spec.ts`「N49 真实 core」在真实会话里批准 `run_pipeline qc`（现场 ffmpeg 生成 5 s mp4），修前作业 stderr 为 `FileNotFoundError: [Errno 2] No such file or directory: 'ffprobe'`（`qc.py` `_probe`）；② `spawnSession` 改 `childEnv(process.env, true)`，PATH 末尾追加 `/opt/homebrew/bin`，系统目录同名程序仍优先；同一 e2e 转绿（`06-check.log` 有 ffprobe 实测的音画时长与 ffmpeg 实测的响度）；③ TH-6 从会话子进程内部取 `os.environ` 断言 PATH 恰为选定值、其余白名单项与 `childEnv` 逐项相同、仍只多一个密钥键；④ 变异：去会话 homebrewPath → N49 e2e 红（TH-6 亦红）；`childEnv` 的 homebrew 段拼错 → TH-6 与 spawner 既有 PATH 断言红；只在会话 env 上拼错 PATH → 仅 TH-6 红；会话 env 改 `PYTHONUNBUFFERED` → TH-6 的逐项循环红（先试的 `LANG=C` 是被「多余键」断言借 PEP 538 的 `LC_CTYPE` 杀的，不算，已换）；均还原 md5 对拍一致；⑤ Spec 8 §3.4、Spec 10 §3.4 archive 原文加修订记录。**顺带盘点（只登记不扩修）**：`acquire` 的 `yt-dlp` 经 `shutil.which` 查找，只在 `/opt/homebrew/bin`、不在 `.venv`——修前桌面端抓取卡批准后同样会 `FAIL 找不到 yt-dlp`，本修随之可找到（未端到端测：真实抓取要出网）；`git`/`curl`/`ssh`/`open` 在 `/usr/bin` 不受影响；Python playwright 浏览器依赖 HOME，已在白名单。**未覆盖**：Intel Mac 的 `/usr/local/bin`（与 SEAL/TTS_APPLY 同一限制，A3 已如实声明）；打包版端到端未跑（留 ACC）。
- 评审：✅ 通过（2026-09-29 独立评审，与施工方不共用上下文）。① 退回修复（会话 env 改回 `childEnv(process.env)`）→ N49 e2e 红在 `sessionReal.spec.ts:474`，stderr 为 `qc.py` `_probe` 的 `FileNotFoundError: [Errno 2] No such file or directory: 'ffprobe'`，无其他失败因素；② 恢复（md5 对拍一致）→ 同一用例绿；会话进程实际 PATH 用 `ps -Eww` 独立取证（不经 TH-6、不经 spawn 日志）：会话 `pipeline.agent.protocol` → `pipeline.qc` → `ffprobe`/`ffmpeg` 每一级 PATH 均为 `/usr/bin:/bin:/usr/sbin:/sbin:/opt/homebrew/bin`，会话环境仍只有白名单 + 一个密钥键（另有 macOS/venv 自动加的 `__CF_USER_TEXT_ENCODING`、`__PYVENV_LAUNCHER__`）；③ 扩面红队：AST 扫 `pipeline/` 全部 subprocess 调用并核 venv 内 mlx_audio / playwright / crawl4ai，修前修后解析结果只变了 `ffmpeg`、`ffprobe`、`yt-dlp` 三个；`git`、`python3`、`ssh`、`rsync`、`curl`、`open` 仍命中 `/usr/bin`（homebrew 同名版被遮住）；agent 无 shell 工具、argv 恒为 `[sys.executable, -m, pipeline.<白名单模块>]`，`KEY_ENV_NAME_RE` 挡住名为 PATH 的密钥变量；`~/.ssh/config`、git 全局配置无引到 homebrew 程序的钩子——**无新越权面**；`yt-dlp` 从找不到变为真会跑，属恢复 Spec 9 S6-R1 本意，未端到端测（要出网）已如实标注；④ 变异 4 条亲跑，全部断言失败、无伪杀：会话 env 退回修前 → e2e + TH-6；homebrew 挪到 PATH 最前 → 仅 TH-6（顺序只由它守）；会话 env 去掉 TMPDIR → 仅 TH-6 逐项循环；`childEnv` homebrew 段拼成 `/opt/homebrew/sbin` → TH-6 + spawner 既有 PATH 断言 + e2e；均还原 md5 一致；⑤ diff 边界合规，`pipeline/`、`config/`、`package.json` 未动；全程临时副本，真实数据盘零新文件。发现（无阻断）：密钥变量随作业继承到外部程序（既有行为，登记 N50）；N49 e2e 在无 `/opt/homebrew/bin/ffmpeg` 的机器上直接红而非跳过（有意为之，A3 已声明）；`BuiltCore.homebrewPath` 的 JSDoc 写「git 在 /opt/homebrew/bin」不准（本机 `/usr/bin/git` 优先），只是注释，不在本条范围。

## 2026-09-28：收尾批复核收口（只读复核 / 决策准备 session）

### [N43] Playwright 硬编码 `workers: 1`，全量 e2e 只能串行
- 状态：**已解决**（施工 9f07166；2026-09-29 独立评审通过）
- 关联：`desktop/playwright.config.ts`（`workers: 1`）；Spec 8 §7, Spec 10 §7
- 原记录（活跃表原文，含施工回填）：2026-09-28 人报：各用例已有独立临时 `--ava-repo-root` / `--ava-user-data`，理论上可并行。**但并行等于自加负载**：D31/D32/N37 都是「负载下才红」的时序问题，且真实数据零污染的 globalSetup/teardown 在多 worker 下是否仍成立未核。推进：等 D31/D32/N37 收口、N41/N42 落地后再试 `workers: 2–3`，负载下复跑全量 ×3 全绿才可改 **2026-09-28 人拍板：开施工试点 workers=2**。验收：负载下未打包全量 ×3 全绿、真实数据零污染的 globalSetup/teardown 在多 worker 下仍成立（逐条核）；不达标则登记原因退回 workers=1。  **2026-09-28 施工（已修·待评审）**：`playwright.config.ts` workers 1→2（config 注释写明前置与多 worker 安全性论证：用例 mkdtemp 独立 repo/userData、无共享端口——媒体走自定义 scheme、globalSetup/Teardown 是进程级钩子仍只跑一次）。验收实测：空载全量绿（5.6 min，串行 9.3 min，约 -40%）；负载（6×yes）下——N48 修复前 5 轮 3 红（VE-1 ×2 + TX-15 ×1，TX-15 查实是 N48 的 NameError 与 workers 无关）；**N48 修复后负载 ×3 全绿**（111 passed / 2 skipped ×3），真实数据零污染校验逐轮通过。**评审注意（诚实申报）**：N48 修复前 VE-1 在 workers=2+负载下红过 2 次，当时未存日志、直接证据缺失；修复后 3/3 绿。VE-1 那两次红疑似 2 worker + 6×yes 超订下 10 s 等待预算见底，若复发应登记新条目（不许调大超时消音）。TX-15 负载红（N48）已修，workers=2 的验收轮次是在它修复后跑的
- 评审：✅ 通过。① workers=2 独立验证（实测）：空载全量 e2e 1 次 111 passed / 2 skipped（5.7 min）；6×yes 满载全量 2 次均 111 passed / 2 skipped（5.9 / 6.1 min），零失败零重试，每轮 globalTeardown 输出「真实数据零污染」。② 未复测 workers=1 的串行基线耗时，施工方『空载 -40%』未独立证实（本次只证 workers=2 稳定）。③ 边界：config diff 被卷进 N48 的 commit a532725（施工时工作树未提交）属提交卫生问题，内容本身正确；施工方记录的『N48 修复前 VE-1 在 workers=2+负载下红 2 次、直接证据未抓到』——评审满载 ×2 未复现，若复发须登记新条目。

### [N44] TI-10「第二实例启动 → 已有窗口获得焦点」在人正操作别的 app 时偶发红
- 状态：**已解决**（施工 c7036ac；2026-09-29 独立评审通过）
- 关联：`desktop/e2e/ack.spec.ts`（TI-10 的 `isFocused()` 轮询 3 s）；Spec 8 §7（TI-10）
- 原记录（活跃表原文，含施工回填）：2026-09-28 N37 负载验收时发现：负载全量 ×3 中 2 次红；随后单跑——后台模式负载 1/3、前台模式（不带 `--ava-test-background`）负载 2/3、前台空载 3/4 通过，均在人正用终端打字时。判断：断言要的是「macOS 把系统焦点交给本窗口」，macOS 14 起协作式激活会在用户正操作别的 app 时拒绝抢前台，属用例对环境的依赖，**未证实与 N41 有关**（样本小，两种模式分不出差别，故未给 TI-10 开前台特例）。候选：① 断言降为「main 调用了 focusWindow 且窗口 restore/show」+ 真机手验焦点；② 保留焦点断言但仅在无人值守时跑（需可检测的开关）；③ 维持现状并在评审口径里注明「人在机器前时 TI-10 红不算回归」。推荐 ①（焦点是否授予本就不归 app 控制），待人拍板 **2026-09-28 人拍板：采纳候选 ①**——TI-10 的焦点断言改为「main 确实调用了 focusWindow，且窗口已 restore/show」（可观测、不依赖 macOS 是否授予焦点），系统焦点是否真的给到改为打包版真机手验一项；不改 N41 的后台模式。  **2026-09-28 修复（已修·待评审）**：TI-10 的 `isFocused()` 轮询删除，改为断言 ① main 侧 `__avaTestFocusCalls` 计数 +1（仅未打包构建的 dev 计数器，focusWindow 内自增）② 窗口 `isMinimized:false` + `isVisible:true`。Spec 8（archive）TI-10 行已加 N44 修订注记；系统焦点授予列入打包版真机手验。变异 2/2（还原 md5 对拍）：删计数自增→TI-10 红；second-instance 不调 focusWindow→TI-10 红。tsc 通过、vitest 377 passed、未打包全量 e2e 111 passed/2 skipped（真实数据零污染）。**评审注意**：① 「窗口 blur 后 focusWindow 被调」由 dev 计数器观测，focus() 本身的系统效果不再有机检（已转述给人的手验项）；② 手验可与 D37 配方同场做
- 评审：✅ 通过（附一处加固）。① 复现与基线（实测）：TI-10 单跑 3/3 绿；未打包全量 e2e 空载 111 passed / 2 skipped。② 变异 4 条（每条改 main 后 electron-vite build、还原后重建，md5 对拍）：删计数自增→TI-10 红；second-instance 不调 focusWindow→TI-10 红；**删 focusWindow 里的 restore→SURVIVED**（窗口本来就没最小化，『restore/show』断言不可观测）——评审补：先 minimize 并轮询到 isMinimized 再起第二实例，重跑 KILLED，加固版单跑 3/3 绿；删 win.focus()→仍绿，属人拍板候选①的既定边界（系统焦点授予不归 app 控制，改打包版真机手验，未机检）。③ 遗留：打包版真机焦点手验仍是待人项，可与 D37 配方同场。

### [D7] 次回预告仍在索引里未滤除
- 状态：**已收口（style 侧已落盘；混入主对白轨属 N25 同构局限）**（2026-09-29 S21 收尾：状态早已是终态，按维护规则「解决即归档」仅迁移，不改结论）
- 关联：`pipeline/subindex.py:66-105`；N25
- 原记录（活跃表原文）：`NON_DIALOGUE_STYLE` 已补齐独立预告样式（前/后缀 `yokoku`/`preview`/`次回予告`/`下集预告`/`予告`/`预告`）并由单测+变异检验锁死。三番 ASS 实测核明：预告标题卡（春物 S2 `Title-Yokoku`、春物 S1/S3 与喰种 `Title`/`TITLE`）早已被 `^title` 滤除；真正占 ~2% 的预告角色对白在春物（`Sub-CN`/`Text-cn`，420/15009=2.80%）、东京喰种（`CN`/`Default`/`DefaultUP`，338/13810=2.45%）中直接混入正片主对白 style（与 N25 同构，纯 style 过滤若触碰会误伤全片正片台词），罪恶王冠 BD 则不含预告段（0%）。在库 151 集旧索引零差异、免重建

### [D8] 纯中文字幕集数过不了对轴校验
- 状态：**已决策（放弃该集，不入库）**（2026-09-29 S21 收尾：状态早已是终态，按维护规则「解决即归档」仅迁移，不改结论）
- 关联：`pipeline/ingest.py:278-345`、`docs/dev/postmortems/workflow-history.md:1080`；ADR-0007
- 原记录（活跃表原文）：2026-09-25 拍板：**放弃，不走 ASR 兜底**。verify 靠字幕含假名行对日语 ASR（对照窗判据），纯中文/假名稀疏轨无窗可用。全库实测唯一活实例 = 春物 S3 OVA（`.SC.ass` 假名行仅 4%，未入库，笔记已登记素材边界）；夏隧 zh.ass 同为 3% 但已入库。理由三条：① 1 集番外 OVA 不值「ASR(ja)+机翻+质检判据」一整条边链；② ASR+机翻双重无标定误差会混进按字幕组文本标定的检索池，质检体系不覆盖；③ 与 ADR-0007 同逻辑同判。未来若某番大量集数纯中文轨（=整部拒收）再重估 ASR 兜底

### [N5] ingest phase0 重建索引时跳过 verify 的静默风险
- 状态：**已解决（已落盘指纹校验）**（2026-09-29 S21 收尾：状态早已是终态，按维护规则「解决即归档」仅迁移，不改结论）
- 关联：`pipeline/ingest.py:375-418,529-562`；—
- 原记录（活跃表原文）：`register()` 落盘视频字节大小 `size` 与外挂字幕 `sub_sha256`；`phase0 --reindex` 仅在 `path + size + sub_sha256` 三项全匹配时才免跑 `verify`，同名替换片源/字幕、换路径或旧表缺指纹均自动重跑 `verify`（`tests/test_ingest.py::TestPhase0ReindexFingerprint` + M1/M2/M3 变异检验锁死）

### [N28] 非终端信号（`kill -INT <ava pid>`）下 ffmpeg 孙进程存活并继续写输出
- 状态：**已解决（真渲染复验通过）**（2026-09-29 S21 收尾：状态早已是终态，按维护规则「解决即归档」仅迁移，不改结论）
- 关联：`pipeline/jobs.py::execute_job`；ava-impl-spec §3.3, 变异 M21
- 原记录（活跃表原文）：`execute_job` 已开启 `Popen(start_new_session=True)` 并将 `Popen` 返回后的全周期纳入 `try ... except BaseException:`，中断时由 `_kill_process_group` 调用 `os.killpg(proc.pid, signal.SIGKILL)` + `proc.wait(timeout=2.0)` 连根拔起子进程及全部 `ffmpeg` 孙进程并收尸（`tests/test_agent_pr6.py::test_n28_*` 双腿单测 + M1/M2/M3 变异检验锁死）。2026-09-25 真渲染复验（`2026-09-06-你的名字-遗忘的代价`）：6 并发 `ffmpeg` 孙进程（PIDs 6709–6714）下发单点 `kill -INT`，响应耗时 `0.258s`，残留 `ffmpeg` 进程 `[]`（0 存活）

### [D24③] 跨挂载点迁移：shots 镜头表 meta.source 存绝对路径（D24 的 ①② 仍在活跃表挂触发条件）
- 状态：**已解决**（施工 08f5c5a；2026-09-29 独立评审通过；仅 ③ 部分）
- 关联：`pipeline/ingest.py`；ADR-0012, Local-First
- 原记录（活跃表原文，含施工回填）：素材库向 TB 级扩张，缺少全量 vs 增量 hash 变更检测与智能缓存淘汰，跨设备挂载需保证便携性。**2026-09-28 决策准备（只读）**：量化现状——T7 数据盘共 256G（盘 931G 用 301G、余 631G），其中 `raw` 219G、`bgm` 12G、`shots` 1.4G（268 个代表帧目录）、`vindex` 218M、`index` 196M；「TB 级」离现实还远（约 1/4）。重建成本见 ADR-0003「6」：一季建视觉索引约 2.4 h。三子项：**① 增量变更检测——不做**：N5 已落 `path + size + sub_sha256` 三项指纹，`phase0 --reindex` 只重建不匹配的集；触发条件：单次 `--reindex` 实测超过 1 h 或出现「指纹匹配但内容已变」的实例。**② 代表帧缓存淘汰——不做**：1.4G 仅占数据盘 0.5%，无清理压力；触发条件：`shots/` 超过数据盘剩余空间的 10% 或 > 50G。**③ 跨挂载点迁移——最小做（报人）**：代码与 config 无 `/Volumes`/`/Users` 硬编码（仅注释），`data` 走仓库内符号链接；**但 `data/library/shots/*.json` 的 `meta.source` 在 231 个里有 15 个存绝对路径，其中 1 个已失效**（`夏隧_S01E01.json` 指向改名前的旧仓库 `.../anime-video-agent/data/library/...`），`shots.py` 重抽帧（约 336、430 行）直接读它，会当场失败——这就是换盘/改名/换机时的实际断点。最小方案：写入侧改存「相对 data 根」路径、读取侧对绝对路径做一次按 data 根重定位并在失效时给出可操作报错；存量 15 个文件一次性迁移（需人批准改数据）。另开施工 session **2026-09-28 人拍板**：①② 不做（按触发条件挂着）；③ 另开施工——`shots.py` 写入 `meta.source` 改存相对 data 根的路径、读取侧对绝对路径按 data 根重定位并在失效时给可操作报错；存量 15 个绝对路径文件一次性迁移（含已失效的 `夏隧_S01E01.json`）。动 `pipeline/` 与真实 data，施工需配评审。  **2026-09-28 ③施工（已修·待评审）**：写入侧 `shots.meta()` 改走 `_store_source`（绝对路径在 data 根内 → 存 data 根相对；存量仓库根相对 `data/...` 统一改存 data 根相对；data 根之外保持绝对）；读取侧两处抽帧（`frames`/`caption_frames`）改走 `_resolve_source`——三种落盘形态统一解析，绝对路径失效时按 `library/` 尾部拼当前 data 根重定位，仍找不到给可操作报错（指向 sources.json 修法）。存量迁移：15 个绝对路径文件以 `sources.json`（登记表是唯一事实源）为准重写 meta.source 并逐个验证可解析——含已失效的 `夏隧_S01E01`（旧仓库路径 + 目录结构已变，`library/` 尾部规则救不回，靠 sources.json 找回）；迁移后全库 231 个 shots json 零绝对路径。用例 6 条（TestSourcePathPortability）。变异 3/3（还原 md5 对拍）：去重定位分支→重定位用例红；写入侧退回原样→store/meta 用例红；失效不报错→可操作报错用例红。全量 1976 passed。**评审注意**：① 存量 216 个「仓库根相对」文件未动（读取侧兼容）；② `clips.json`/`recheck` 等其他产物的 source 字段不在本次范围；③ 迁移直接改的是 T7 真实数据（拍板已批），未留 .bak——原值可从 git 历史+本 issue 行还原
- 评审：✅ 通过（③ 部分；①② 按触发条件挂着的结论维持，D24 行整体收口）。① 存量核对（实测）：231 张镜头表 meta.source 形态 = 216 个 data/… + 15 个 data 根相对，零绝对路径，且 _resolve_source 全部解析到真实文件（含原已失效的夏隧 S01E01）。② 变异 5 条实跑：重定位去掉→重定位用例红；meta 写入不走 store→写入侧用例红；失效不报错→报错用例红；**frames()/caption_frames() 读取侧改回直接 Path(meta.source)→SURVIVED**（只测 _resolve_source 本身守不到这两处接线）——评审补『假 _extract 记录器 + 相对 source』接线用例，重跑两条均 KILLED（首版夹具 fps 写成浮点、收集期报错造成假杀，已修并确认基线绿）。③ 其余消费方核对：镜头画廊只取 basename，load() 不对账 source，无其他直接 Path(meta.source) 的读者。

### [N19] cpm 换音色/换题材/换引擎要重测
- 状态：**已解决**（施工 06f7ebf；2026-09-29 独立评审通过）
- 关联：`config/project.json` 的 `script.cpm` 与 `_cpm_note`（原行号已漂移）；ADR-0006
- 原记录（活跃表原文，含施工回填）：同一音色不同文风可差 24%；Qwen3 语速待实测。**2026-09-28 复核（只读）**：① 溯源：现值 `cpm = 298`，来自 `df8c702`（2026-09-11，云端 CUDA Qwen3 切换时重测 315→298）——**换引擎的触发条件当时执行过**；但 `_cpm_note` 仍写「2026-08-10 校准 380→315」，**说明文字没跟上数值**（文档漂移，需人确认后改 note）。② 用真实产物复算（全部 22 期 `03-audio/manifest.json`，字数用 `tts.normalize` 与 `expected_duration` 同口径、时长用 manifest 里 ffprobe 复核的段时长）：IndexTTS 8 期中位 289.5；**本地 Qwen3（mlx）12 期中位 261.5（250–282）**；云端 Qwen3 CUDA 2 期（EGOIST V2 01/02）中位 273.5。298 比现行引擎实测**高约 8–14%**：`check_script` 按 cpm 推出的字数带偏宽、`expected_duration` 偏短估约一成；时长门禁 `DUR_BAND 0.5–2.0` 把偏差吸收了，所以从未报错。「Qwen3 语速待实测」已不成立（上面即实测）。③ 待拍板：(a) 按现行引擎近 N 期 manifest 中位数重标（上面的复算法即可复现，脚本不入仓）并同步改 `_cpm_note`；(b) 维持 298 并在 note 写明「有意取偏快值」的理由；另可考虑把 cpm 改为按引擎/音色分键（与 N14 同一「逐条件标定」思路） **2026-09-28 人拍板：按现行引擎近期中位数重标 cpm**（本地 Qwen3 中位 261.5、云端 273.5；取近 N 期现行引擎中位，约 265）并同步改写 `_cpm_note`（写明复算口径：`tts.normalize` 字数 / manifest ffprobe 时长）。动 `config/`，按 rule ④ 另开施工 + 评审；注意 `check_script` 字数带随之收窄，施工时要核对在制期稿件是否因此被新卡。  **2026-09-28 修复（已修·待评审）**：`script.cpm` 298→257，`_cpm_note` 重写（文档漂移已纠：旧 note 还停在 380→315）。重标口径（本 session 复算，不转述复核数字）：本地 mlx qwen3_tts 全部 9 期 03-audio manifest，`tts.normalize` 字数 ÷ ffprobe 段时长，逐期中位 **257.4**、汇总 259.8、范围 250.0–281.7。**与 C 组复核数字的分歧（评审须核）**：复核记「mlx 12 期中位 261.5」，但本地只有 9 期 mlx manifest（17 份 = 9 mlx + 8 IndexTTS，云端 2 期的 manifest 不在本机），12 期口径在本机复算不出——修复取可验证的 9 期中位 257。代码侧 `check_script.CPM` 的 paths.conf 默认值保持 380 不动（纪律：default=原硬编码值）。全量 1970 passed。变异不适用（纯配置改值 + note），等价性由全量绿承担。
- 评审：✅ 通过。① 独立复算（实测，不转述）：全部 17 份 03-audio manifest = 8 IndexTTS + 9 本地 mlx qwen3_tts（云端 CUDA 期的 manifest 不在本机）；mlx 9 期逐期中位的中位数 256.7、逐期汇总的中位数 257.4，取 257 成立；C 组复核「12 期 261.5」在本机数据上确实复算不出，施工方的分歧记录属实。IndexTTS 中位 288–289，说明旧 298 是老引擎口径。② 影响面核对（实测）：用 cpm 298/257 分别跑 17 篇现存稿的 check_script，已完成期的字数/时长带会随之平移（旧稿多数会在新带下判『时长』超上限），但这些期均已成片，仅在重跑机检时可见；唯一在制期（2026-09-21 东京喰种董香人物志-二）尚无 02-script，新带宽只影响其后续稿——符合人拍板「按近期中位数」的意图。③ 全仓 grep 无残留的 298/cpm 引用；代码默认值保持 380 未动（paths.conf 纪律）。④ 备注：云端 CUDA Qwen3 仅 2 期且 manifest 不在本机，若日后以云端为主要引擎应再重标（_cpm_note 已写明「换引擎仍须重测」）。

### [N48] `--continue` 恢复含 tool 记录的会话必崩（NameError: tool_flags）
- 状态：**已解决**（施工 a532725；2026-09-29 独立评审通过）
- 关联：`pipeline/agent/protocol.py::_send_history`；Spec 9 §3.1 history 帧
- 原记录（活跃表原文，含施工回填）：_send_history 是模块级函数，tool_flags 只在 main() 局部 import；TP-8 场景（等卡时被杀）不产生 tool 记录，藏了整场二期；N43 负载验收时 TX-15 抓到。修为函数内局部 import（守 fd 隔离），新增 TP-8b。
- 评审：✅ 通过。① 原始复现（实测）：去掉 _send_history 里的局部 import，TP-8b 红（NameError）而 TP-8 仍绿——证实 TP-8 场景确实打不到 tool 记录路径；还原 md5 一致。② 静态扫描 protocol.py：其余模块级函数没有再依赖 main() 局部 import 的名字。③ diff 边界：main() 顶部移除 tool_flags 导入 + 函数内延迟导入（守 fd 隔离）+ TP-8b。注：N48 施工时未单独登记活跃表行（只在 N43 行内提及），本条按 commit 信息补录。

### [N47] 稿件 `人物:` 写到「0 个已贴名簇」的角色时静默无效
- 状态：**已解决**（施工 0cb32f3；2026-09-29 独立评审通过）
- 关联：`pipeline/check_script.py`（`人物` 字段处理）、`pipeline/vindex.py`（clusters 读取）；D11, N17, ADR-0004
- 原记录（活跃表原文，含施工回填）：2026-09-28 D11/N17 拍板：不批量贴名，改需求驱动。推进：`check_script` 对 `人物:` 经 `alias_map` 映射到的规范键在该番 clusters 里**零个已贴名簇**时出 INFO「该角色在索引里无代表脸，在场过滤对本段无效（贴名：faces sheet <番>）」——只 INFO、永不 FAIL（同 D18 的「写了就走过滤、不写不勉强」口径）；实测唯一现存命中应为春物「阳乃」2 段 **2026-09-28 修复（已修·待评审）**：新增 `faceless_character_hints`（`_HINT_BLOCK` 切块 + `_HINT_WHO` 取值复用 D18 既有口径）：`人物:` 值经 `_character_alias_tables`（`_character_aliases` 拆出的逐番版，合并语义不变）映射到规范键后，在该番 clusters 里零个已贴名簇 → INFO「…无代表脸，在场过滤对本段无效（贴名：python -m pipeline.faces sheet <番>）」。`_named_cluster_tags` 纯 json 读 clusters（不碰 vindex/faces 的 numpy，守 §5.2 纯洁性），文件缺席/坏 json → None 安静跳过（判据 4）。接线在 `main` 的 `character_hints` 之后。用例 4 条（零贴名出 INFO/有贴名不报/缺席安静跳过/未写人物不报）。真实数据复扫全部在库 02-script.md：恰命中春物「阳乃」2 段（2026-08-04-团子不该赢 段3/段5），与预期一致。变异 3/3（`PYTHONDONTWRITEBYTECODE=1`，还原 md5 对拍）：永不报→零贴名用例红；判定取反→有贴名不报 + 缺席跳过红；缺席返回空集而非 None→缺席跳过红。全量 1968 passed。**评审注意**：① `人物:` 多值写法（顿号分隔）在 clips 侧本就整串当一个名字，本提示同口径；② 跨番期同一别名在两番都登记时只按第一番判（现实无此碰撞）||N48|已修·待评审|`--continue` 恢复含 tool 记录的会话时协议进程必崩（NameError）|`pipeline/agent/protocol.py::_send_history`|Spec 9 §2.8/§3.1|2026-09-28 N43 负载验收时抓到（TX-15 红）：`_send_history` 是模块级函数，`tool_flags` 只在 `main()` 里局部 import，重放 tool 角色记录必 NameError 退 1——二期全程没炸是因为 TP-8 的「等卡时被杀」场景不产生 tool 记录、D35 之后「未执行」补记才让 tool 记录变常见。修复：`_send_history` 内局部 import（守 fd 隔离纪律，main ② 先于一切 pipeline import），`main()` 里冗余的 tool_flags import 撤掉。新增 TP-8b（批准执行的工具 → shutdown → --continue → tool 帧重放 ok/text 映射正确、退 0）。修前红（退回即 NameError）、修后绿；全量 1977 passed。**评审注意**：① TX-15 在负载下偶发红的真因就是本条（不是 N43 的 workers=2）；② TP-8 的 role 断言此前是「允许 tool」而非「必有 tool」，覆盖缺口已由 TP-8b 补上
- 评审：✅ 通过（附两处补丁）。① **发现并修复**：施工把 _character_aliases 拆成 per-anime 表 + 合并时，别名冲突从旧版 setdefault「先到先得」悄悄变成「后到覆盖」，注释却写「与旧版同」；真实 17 期上新旧输出零差异（数据暂未触发），但语义已变——恢复先到先得（表内与跨番），补跨番/单番内冲突两条用例，各自变异 KILLED。② **发现并补**：main() 里对 faceless_character_hints 的调用无用例守着（删掉调用 SURVIVED）——补假 DATA 根走 main() 的接线用例，重跑 KILLED。③ 自身逻辑变异 3 条：条件取反→零贴名用例红；缺席当空集→缺席跳过用例红；main 不打印→接线用例红。纯 stdlib 纯洁性（clusters 纯 json 读取）核实。

### [N46] 字幕组 style 命名表写死在代码里，换番/换字幕组要改 `pipeline/`
- 状态：**已解决**（施工 0ecf16d；2026-09-29 独立评审通过）
- 关联：`pipeline/subindex.py::NON_DIALOGUE_STYLE`；D10, ADR-0007
- 原记录（活跃表原文，含施工回填）：2026-09-28 D10 复核拍板：2026-08-09 罪恶王冠加 3 种命名（裸 `JP`、`CN_song`/`JP_song`/`Eng.song` 等）、2026-09-25 D7 加预告样式，都是「换一部番（字幕组）就要改代码」——按总纲属缺陷。推进：把 style 命名的前缀/后缀/精确匹配表迁入 config（机制留代码：前缀匹配、精确匹配、KANA/CJK 兜底），已有单测与 D7 的变异矩阵随之改为读配置；须保持「主对白 style（Sub-CN/Text-cn/CN/Default/JPN）零误伤」这条既有断言 **2026-09-28 修复（已修·待评审）**：表迁 `config/project.json` 的 `subtitle.non_dialogue_styles`（四类：`prefix_bounded`/`prefix`/`exact`/`suffix`，带 `_nd_note` 说明与「主对白 style 绝不进表」警告）；机制留代码——`build_non_dialogue_re(table)` 把表编成单条正则（re.escape 逐项转义），`non_dialogue_re()` 缓存 accessor 走 `paths.conf`（默认值 = 原硬编码表，符合 paths.conf 纪律），`parse()` 改调它。原 `NON_DIALOGUE_STYLE` 常量删除，其上方整段取舍注释保留。用例：既有 13 处断言改走 `non_dialogue_re()`（KEEP/DROP、Inner、裸 JP、NOTE、D7 预告、反序 song 全保留）；新增「机制读表」（自定义表加 pv → PV-CN 滤掉，默认表不滤）与「config 表与代码默认表行为一致」（防配置漂移）。变异 3/3（`PYTHONDONTWRITEBYTECODE=1`，还原 md5 对拍）：prefix_bounded 丢边界→Inner 用例红；exact 退化为前缀→裸JP用例红；suffix 类删除→反序 song + 预告 3 条红。全量 1970 passed。**评审注意**：① 重建索引产物应与现状逐字节一致（机制等价、表同值），未实跑重建（全量重建约 2.4h/番，无触发理由）；② `docs/dev/plans/2026-08-21-检索降级红旗.md` 与 archive 里对 `NON_DIALOGUE_STYLE` 的引用是历史文档，未追改
- 评审：✅ 通过（附一处加固）。① 等价性（实测）：旧硬编码正则与新表编译正则在 131 个真实 ASS style + 边界样例上零差异。② **发现并修复**：config 表为空 / 含空串条目 / 列表写成字符串时，build_non_dialogue_re 编出匹配一切的正则，整部番对白被静默丢光——现 build 时校验形状并用主对白 style 金丝雀（Sub-CN/Text-cn/CN/Default/JPN）自检，违反即 ValueError；新增坏表用例。③ 变异 4 条实跑：suffix 类丢失→反序命名用例红；canary 检查去掉→坏表用例红；形状校验去掉→SURVIVED，补 exact:[""] 用例后 KILLED；accessor 忽略 config→SURVIVED（config 与默认同值造成等价），补 monkeypatch _CONF 的 accessor 用例后 KILLED。全量 1983 passed。

### [N45] `web_fetch` 不处理 `Content-Encoding`：服务器强行 gzip 时把压缩字节当正文返回，正文乱码、链接清单为空，且不报错
- 状态：**已解决**（施工 2e545be；2026-09-29 独立评审通过）
- 关联：`pipeline/agent/web.py::fetch_web`（「不声明 gzip」的假设）；Spec 4, Spec 13
- 原记录（活跃表原文，含施工回填）：2026-09-28 D28 收口时实测：`fetch_web("https://www.python.org/")` 返回 status 200、`text` 为 gzip 二进制乱码、`links` 0 条；curl 确认该站即使请求头 `Accept-Encoding: identity` 仍回 `content-encoding: gzip`（nginx + varnish）。同批 docs.python.org（58 条链接）与 example.com 正常。后果是**静默失败**：模型拿到乱码、以为页面没内容，也不会按「静态被挡→升级 crawl」的提示升级。推进：读响应头 `Content-Encoding`——`gzip`/`deflate` 则在 `max_fetch_bytes` 上限内流式解压（防解压炸弹：解压后字节同受上限约束），其余非 identity 编码明确报错并附升级 crawl 提示；补用例（本地假服务器强制 gzip）与变异 **2026-09-28 修复（已修·待评审）**：新增 `_read_body_capped(resp, content_encoding, limit)`，`fetch_web` 与 `search_web`（同一读取路径、同一 bug 类）统一走它——gzip 走 `gzip.GzipFile.read(limit)`、deflate 走 `zlib.decompressobj` 的 `max_length` 参数，解压后字节同受 `max_fetch_bytes` 封顶；未知编码 ValueError 附升级 crawl 提示；gzip/deflate 流损坏也诚实报错（不静默错解）。`fetched_bytes` 语义=进入解码的字节数（压缩响应报解压后）。真网复测：`fetch_web("https://www.python.org/")` 由乱码净文/0 链接 → 正常净文（52960 字节）/127 链接。用例 7 条（gzip/deflate/解压炸弹封顶/br 报错/坏 gzip 报错/显式 identity/search_web gzip）。变异 3/3（`PYTHONDONTWRITEBYTECODE=1`，还原 md5 对拍）：fetch_web 退回裸读→gzip/deflate/炸弹 5 条红；未知编码静默当 identity→br 用例红；gzip 去封顶→炸弹用例红。Spec 13（archive）按 v0.5 修订记录同步改 §2.5⑤ 与 RF-12。全量 1964 passed。**评审注意**：① 顺手覆盖了 `search_web`（同一 helper，属同一根因，非顺手改别条目）；② 顺带发现并已修：`feab78c` 落档把字面 `03-audio/manifest.json` 写进 `01-topic.md`，触发 egress 断言致 4 条装配集成测试全红（D30 同类第二现场），已改写避开（8502d21）
- 评审：✅ 通过（附一处用例补强）。① 真网复跑（实测）：python.org 200 / 127 链接 / 正文正常。② 变异 4 条实跑（PYTHONDONTWRITEBYTECODE=1，还原 md5 对拍）：gzip 分支失效→gzip 用例红；gz.read(limit)→gz.read()→bomb 用例红；search_web 不走 helper→search 用例红；**deflate 解压去掉 max_length 限流→SURVIVED**（末尾 out[:limit] 兜住输出，仅内存上界不同）——评审补 test_fetch_content_encoding_deflate_bomb_capped（间谍断言每次 decompress 带 max_length ≤ limit+1），重跑 KILLED。③ diff 边界：只动 web.py 读体函数与调用点 + 测试 + Spec 13 archive 修订。

### [N21] 写稿基线与文风文件的两处缺口（原「文档维护琐碎项集合」）
- 状态：**已收口**（2026-09-28 复核）
- 关联：`skills/write-script/BASELINE.md`（知乎 3 行）、`skills/write-script/VOICE.local.md`（本机私有、不进 git）；—
- 原记录（活跃表原文）：④ 知乎 3 篇超 45 字占比缺测；⑥ VOICE.local 移植表缺项；①②③⑤ 已修。**2026-09-28 复核（只读）**：①②③⑤ 抽查确认已修（`scenes.json` 3 处路径引用全部存在；`bgm.json` 241 首逐条存在，见 N12；`SHOTLIST.md` 已无编号步骤可断；`voice.json` 的 `ref_text: null` 理由在 `_note`）。剩两条改写为可执行项：**(1) 实测知乎 3 篇的超 45 字整句数**——这不是补表格：`check_script` 的 `MIN_LONG_SENT = 2` 要求至少 2 句超 45 字，而「外卖骑手」（30400 赞、最长句仅 60 字）若达不到，这道门禁就在误伤合法的短句文风，与 08-04 删掉「气口均长」门禁是同一类错。做法：取 3 篇原文存本机 txt，`python -m pipeline.check_script --raw <文件>` 同口径量（BASELINE 第 12 行），回填表格；判据：外卖骑手超 45 字整句 <2 → 按判据 3 回到成因重议 `MIN_LONG_SENT`，≥2 → 回填即收口。**阻塞**：无头抓取（Camoufox stealth）被知乎登录墙挡住（2026-09-28 三篇均只拿到首页标语），需人允许用已登录 Chrome 读取，或人手粘贴原文。**(2) 补 VOICE.local 移植表「平台专属梗」一行**：模板（`VOICE.md`「移植改动表」）要求至少判断粗口/居高临下/平台专属梗三条，本机表已判前两条、缺第三条；文风属人的判断，由人补一行「保留/去掉 + 一句理由」即收口。（原提示把 ⑥ 与 N12/N16 并提：那两条是 TTS 读音表与角色表，与写稿文风无关，不冲突。）。**2026-09-28 (1) 已完成**：人允许后用登录态 Chrome 取知乎三篇原文（文本只在本机 scratchpad、不入仓），按 `rhythm()` 同规则量——外卖骑手 10.0%（30 句中 3 句超 45 字：60/52/50）、中年悲哀 43.5%（20 句）、雪之下 62.6%（57 句）；已回填 `BASELINE.md` 表并加脚注。**结论：`MIN_LONG_SENT = 2` 在最短句的人类样本上也成立，不误伤短句文风，门禁不动**。剩 (2)：VOICE.local 移植表补「平台专属梗」一行，由人补
- 收口依据：2026-09-28 两条都已完成：(1) 知乎三篇超 45 字占比补测回填 BASELINE（10.0/43.5/62.6%），MIN_LONG_SENT=2 在最短句人类样本上成立，门禁不动（`2551f84`）；(2) 人拍板在 VOICE.local（本机私有、不进 git）移植表补「平台专属梗 → 去掉：同一期视频双平台发；不分平台的通用互联网梗照常可用」。①②③⑤ 早已修（复核确认）。

### [D10] ROADMAP 核心假设未验证 + GUI 边界未答
- 状态：**已收口**（2026-09-28 复核）
- 关联：`docs/dev/ROADMAP.md` 阶段 6（「GUI / 交互壳」）与「未验证的假设」节（原行号已漂移）；ROADMAP 闸门 D
- 原记录（活跃表原文）：骨架通用性、两人不分叉、GUI 给谁用 / 审阅还是编辑。**2026-09-28 决策准备（只读）**：**④ 审阅型还是编辑型——已有答案**：Spec 10–12 已交付并定死边界——审阅 + 结构化决策（待答区卡片、05/09 决策卡）+ 纯人工文本编辑（02.5 CodeMirror 编辑器，Spec 11 §2.9「不做 LLM 辅助/diff 可视化/版本管理」）+ 封面图拖入导入（图像编辑由 agent 的确定性工具 `cover_edit` 执行，Spec 12）；**不做时间轴/画面剪辑**，未越过 ROADMAP「能拖时间轴、调画面 → 正面撞上」那条线。**③ 给谁用——事实已答、文字未跟上**：ADR-0020 已解禁并交付 Electron 桌面端（打包版、仓库根选择器、钥匙串），实际使用者是本人；ROADMAP 阶段 6 仍写「无需从零开发 Electron…套壳 Pi」，**已过时**（ROADMAP 最后改于 2026-09-16）。待拍板：(a) 明确「只给自己用」，协作者走终端/CLI（D9）；(b) 给协作者也用桌面端 → 要补 Windows 打包、报错说人话等，即 ROADMAP 说的「无底洞」。推荐 (a)，并同步改写 ROADMAP 阶段 6。**① 骨架通用性——部分成立**：逐番标定值已按番分键进 config（`visual.*`、`bgm.json`、`characters.json`），9 部番/池已跑通；但**换番仍改过 pipeline 代码**：2026-08-09 罪恶王冠给 `subindex.NON_DIALOGUE_STYLE` 加 3 种字幕组命名、2026-09-25 D7 加预告 style、N26 电影文件名集号正则——按总纲「换一部番就要改代码即缺陷」，字幕组 style 命名表宜迁 config；跨垂类（影视/游戏）的通用性完全未验证，闸门在 D9。**② 两人不分叉**：三平台 CI 已建；共享 fixture 未建（ROADMAP 自述「待建」），与 D9 同一件事，建议并入 D9 裁决
- 收口依据：2026-09-28 人拍板：③ 桌面端只给自己用；④ 边界已由 Spec 10–12 定死（审阅 + 纯文本编辑，不做时间轴/画面剪辑）；② 并入 D9 收口；ROADMAP 阶段 6 开头、闸门 D、假设 5 与阶段 2 已同步改写。① 骨架通用性中「换番仍改代码」的实例（字幕组 style 命名表写死在 subindex）另登记施工 N46；跨垂类通用性仍未验证，闸门随 D9 的协作者条件。

### [N25] 歌词混入 JPN 对白轨且无独立 style（君名本版片源），style 过滤对其无效
- 状态：**已收口**（2026-09-28 复核）
- 关联：`pipeline/subindex.py`（`NON_DIALOGUE_STYLE` 注释段，原行号已漂移）、`data/library/index/你的名字_S01E01.json`；—
- 原记录（活跃表原文）：2026-09-01 Phase 0 绕过；下部电影/下版片源会再踩。**2026-09-28 复核（只读）**：① 现状：「2026-09-01 绕过」**没有落成任何代码或配置**——当前索引（`built_at 2026-09-01`，6273 单元）里歌词仍在：如 31:19–32:20 的《前前前世》「从你的前前前世开始，我就一直在找你 / 循着你怯生生的笑容，我一路飞奔而至」被窗口与对白拼进同一检索单元。原片字幕（Haruhana BDRip `Chs&Jap.ass`）里歌词与对白同在 `CN`/`JPN` style。② 可检测性：歌词块带**成段一致的覆写标签签名**（《前前前世》`{\an7\fad(200,200)\3a&88}`、片尾《なんでもないや》39 行 `{\fad(300,500)\bord0\shad0}`），但签名**不专属歌词**——`\3a&88`、`\pos` 等同样出现在普通对白上（CN/JPN 共 3266 行，签名分布实测），所以**不能做过滤或门禁**（会误伤对白，与 D7 同理）。可做的是 Phase 0 **INFO 提示**：列出「同一 style 内连续 ≥N 行共享同一非默认、含 `\fad` 的覆写签名」的时间块，交人确认是否歌词；判据 1 上它量的是真实产物（原字幕文件）。③ 待拍板：(a) 做上述提示（`ingest`/`subindex` 只读报告，不改过滤逻辑）；(b) 仅在 Phase 0 人工核验清单加一条「电影/无独立歌词 style 的片源：抽看插曲时段是否有歌词行混入对白轨」；另需人判断已入库的君名歌词单元要不要处理——剧场版的插曲段画面多是剧情蒙太奇，危害小于 TV ED（ED 画面不可用），可能无需重建
- 收口依据：2026-09-28 人拍板：只加人工核验一条（已写入 `docs/runbook/01-topic.md`「01.4 换条件重测清单」的「开新番 · 歌词是否混进对白轨」行）；不做自动提示/门禁（覆写标签签名不专属歌词）；已入库的君名歌词单元不重建（剧场版插曲段画面多为剧情蒙太奇，危害小于 TV ED）。

### [N20] 触发式推翻条件未单独立档
- 状态：**已收口**（2026-09-28 复核）
- 关联：`ADR-0002/0003/0004/0005`；—
- 原记录（活跃表原文）：各 ADR 的 what-if 分散在库里；是否抽成备忘是结构性取舍。**2026-09-28 复核（只读）**：① 摘录：ADR-0002 已 superseded（决定二/三由 ADR-0006 取代），无推翻段；ADR-0003「什么情况下推翻本决定」（约 489 行起：第 1 层/第 2 层各自废掉、建索引耗时超量级、跨通道融合须先离线评测）；ADR-0004「什么情况下推翻本决定」（约 133 行起：集号约束致 no_match 变多、融合有离线证据）+ 正文「换番、换 embedding 模型后这个数要重测」；ADR-0005「什么情况下推翻本记录」（约 142 行）与「推翻本补记」（约 237 行）。② 判定：**这些「推翻条件」多由专门的探针/离线评测触发，日常动作碰不到，留在 ADR 里即可，不必抽表**。但同一批 ADR/配置里还散着另一类条件——「换番 / 换引擎 / 换音色 时必须重测」——它们**恰被日常动作触发，且本轮复核实证已被漏掉**：N9（PRESENCE_BAND 换多部番未重测）、N14（face_expand 五番照抄）、N19（cpm 换引擎重测了但 note 没跟上、换题材未测）。③ 方案（报人，同意后再建）：不新开 dev 文档，在生产态加一张「换条件重测清单」——落点候选 `docs/runbook/01-topic.md` 末尾一节（开新番/换音色时人必经此处），或 `docs/WORKFLOW.md` 链出的独立一页；形态 4 列：触发事件（换番/换引擎/换音色/换题材/换 embedding）｜要重测的量（PRESENCE_BAND、ccip_same/ccip_margin/face_expand、scene_threshold、cpm、voice.titles 实测秒数、readings 条目来历、CCIP provider）｜配置位置｜重测方法指针（ADR 节或 issue 行）。推翻条件本身保持原位
- 收口依据：2026-09-28 人拍板：推翻条件留在各 ADR 原位；被日常动作触发且已实证会漏的「换条件要重测」一类收成清单，落在 `docs/runbook/01-topic.md`「01.4 换条件重测清单」（触发事件 | 要重测的量 | 配置位置 | 方法指针）。

### [N8] 簇纯度判据口径未裁定（Phase 0 人工时长已回填）
- 状态：**已收口**（2026-09-28 复核）
- 关联：`docs/adr/0003:270,274`；ADR-0003
- 原记录（活跃表原文）：20 张抽检已执行，正式阈值和总时长未回填。**2026-09-28 复核（只读）**：两个空格子其实在 ADR-0003 正文里都有数，只是没回填到「待定」处，已回填（数值只落 ADR-0003，本行引用）：① Phase 0 人工时长：春物全季贴名约 10 min（64 簇贴 24 簇，ADR-0003「7 / 8」）；机器侧一季约 2.4 h（「6」）；逐角色抽检的人时未单独计时。② 簇纯度：实施中落为逐角色抽检（`vprobe presence`，抽 20 张验精确率），春物 0.05 门槛下八幡/雪乃/一色/平冢静 20/20、结衣约 18/20。**待拍板**：`vprobe.presence_probe` 文档写「错一张就……提阈值或摘名」，而结衣 18/20 当时被放行（理由：错的是大远景小人物）——二者口径冲突。候选：(a) 严格 20/20，结衣按规则提阈值或摘名；(b) 明文允许「大远景/画面小人物」类错误不计，其余错一张即处置；(c) 设精确率下限（如 ≥18/20）。另：抽检记录只见春物一部，后续番（东京喰种、罪恶王冠等）有无逐角色抽检记录未见落档——换番时是否执行过需人确认
- 收口依据：2026-09-28 人拍板：逐角色抽检（vprobe presence，抽 20 张）口径＝大远景/画面中小人物的误认不计，其余错一张即处置（提阈值或摘名）；已写入 ADR-0003「验簇纯度」处。Phase 0 人工时长已回填 ADR-0003（春物全季贴名约 10 min）。遗留小事：`vprobe.presence_probe` 的 docstring 仍写「错一张就…」，下次改 vprobe 时顺带同步到本口径。

### [N10] 周复盘 + 选题没脚本化
- 状态：**已收口**（2026-09-28 复核）
- 关联：`docs/runbook/01-topic.md`、`docs/WORKFLOW.md`（原指针 `WORKFLOW.md:225,695-697` 已失效，该文件现 78 行）；ROADMAP 阶段 2/5
- 原记录（活跃表原文）：手写 `01-topic.md`、拉平台数据手动。**2026-09-28 复核（只读）**：两件事性质不同，分开判。**选题**：已有工具面——`ava idea`（D27 无期选题会话，发散）与 `ava new <名>` 建期；落定 `01-topic.md` 的「张力」按 WORKFLOW 第 53 行是「整条流水线唯一编辑判断，定死后不许 agent 篡改」，属**刻意保留的人的判断**，不该脚本化——这一半不再是缺口，改记为设计。**周复盘**：全仓无任何拉平台数据或出复盘的代码/工具（grep 播放量/analytics/weekly 零命中）；`runbook/01-topic.md` 第 29 行明文「具体播放数字不进 git（账号数据，本机自维护）」，复盘结论以规则形式回写 runbook。能否脚本化取决于数据源是否稳定可得（B 站创作中心/YouTube Studio 都需登录态，按 AGENTS 工具序列属带凭据的浏览器交互）——这是**待人拍板**的一半：(a) 维持手工，只把「复盘→改规则」的回写纪律留在 runbook；(b) 做一个只读导出（人手导出 CSV → 脚本汇总），不碰登录态；(c) 走带登录态的自动拉取（风险与维护成本最高）。推荐 (b) 或 (a)
- 收口依据：2026-09-28 人拍板：**取消周复盘环节**（理由：平台算法差异与运气随机性大，复盘结论不可靠）；选题的「张力」属刻意保留的人的判断，不脚本化。runbook/01-topic.md 里既有的「来自自己账号的实测复盘」规则保留为历史依据，不再定期复盘。

### [D11] 角色贴名未完成 + 「佑」显示名待核对
- 状态：**已收口**（2026-09-28 复核）
- 关联：`config/characters.json:23,51,65,97`；—
- 原记录（活跃表原文）：东京喰种 / 罪恶王冠贴名进度未标完成；用 subindex 核「佑」显示名。**2026-09-28 补**：「贴名未完成」的实测影响已在 N17 行主写（262 簇未贴名中只有春物阳乃造成真实缺口，其余对现有稿件无可见影响），本行只剩「佑」显示名核对这一件待决。**2026-09-28 决策准备（只读）**：① 量化现状与影响面见 N17（主写处）：七番 262 簇未贴名，现有全部稿件的 `人物:` 只有春物「阳乃」落在 0 簇角色上。② 「佑」核定：`yuu_(guilty_crown)`（ユウ）在本地罪恶王冠全部 36 份字幕里**只出现一次**——ep20 07:15「僕はユウ ダアトの使者です」，诸神 GB 轨译作「**我叫Yu** 是Daath的使者」；素材内对他的称呼是拉丁字母「Yu」，「佑」只来自中文维基。他在罪恶王冠 clusters 里 0 个已贴名簇、现有稿件从未写过，**当前影响为零**。候选：(a) 显示名保留「佑」、别名加「Yu」（与字幕组一致，写稿时两种写法都能映射）；(b) 显示名改「Yu」；(c) 不动，等真有稿件要他时再定。推荐 (a)：零成本、不丢维基通行译名。③ 贴名分批：**不建议批量贴名**（262 簇里 261 个对现有稿件无影响，按工作量算不划算）；改为需求驱动两条——(i) 立即：扫春物 40 个未贴名簇找阳乃（~几分钟人工，见 N17）；(ii) 机制（报人）：`check_script` 对 `人物:` 映射到「0 个已贴名簇」的角色出 INFO「该角色在索引里无代表脸，在场过滤对本段无效」，让贴名只在稿件真需要时发生。改名/加别名属内容决策，拍板后另开施工
- 收口依据：2026-09-28 人拍板：「佑」不动，等稿件真用到该角色时再定（依据：本地罪恶王冠 36 份字幕仅 ep20 07:15 出现一次、诸神 GB 轨译作「Yu」，该角色 0 个已贴名簇、稿件从未写过，影响为零）。贴名不批量做，改需求驱动：check_script 对「人物:」映射到零簇角色出 INFO（已登记施工 N47）；影响面实测见 N17。

### [D9] Windows 跑通九步 + API 接入 + 共享 fixture
- 状态：**已收口**（2026-09-28 复核）
- 关联：`docs/dev/ROADMAP.md` 阶段 1/2（原行号已漂移）；ROADMAP 阶段 1/2
- 原记录（活跃表原文）：依赖闸门 A（B3，已归档/降为观测量）；fixture 未建。**2026-09-28 决策准备（只读）**：三子项逐一——**(1) API 接入：主体已达成，换个口径收口**。ROADMAP 阶段 2 要的是「不装 coding agent 也能跑完九步」：ava 宿主（`pipeline/agent/llm.py`，stdlib 直连 `{base_url}/chat/completions`，`CPA_API_KEY`）已承担写稿/查询/标题，`ava <期> /script` 即写稿入口，这条完成判据已满足；**未达成**的是第二条「同一选题跑两次稿件可复现」——LLM 采样不固定，也没人再把它当目标（稿件质量由 02.5 人审 + 02.8 对抗审查兜）。建议：把阶段 2 判为完成，「可复现」改记为已放弃的目标并写明理由。**(2) Windows 跑通九步：建议不做、收口**。现实需求：本项目是个人单机（macOS + T7 外置盘），ROADMAP 里「Windows 协作者」这条动机今天没有实际对象；闸门 A 所依赖的 B3 已被否证降为观测量。前置现状（grep 实数）：mlx 只在 `asr.py`/`tts.py`/`cloud.py` 三处且都已有 CUDA 后端可替（`qwen3_tts_cuda`、`sensevoice_cuda`/`whisper_large_v3_cuda`），`pipeline/*.sh` 两个 bash 脚本（`preflight.sh`、`record_refs.sh`），桌面端只做 macOS 打包；三平台 CI 已在跑纯函数测试（`windows-latest` 在矩阵里）。若将来真有协作者：触发条件 = 出现一个要在 Windows/Linux CUDA 上出片的具体人，届时按 ROADMAP 闸门 A 原判据验收。**(3) 共享 fixture：随 (2) 收口**。它解的是「两人不分叉」（D10 ②）的痛，单人开发不存在；CI 已覆盖依赖能否全新装上这一类最常见的漂移。候选：(a) 三项按上述收口/改写（推荐）；(b) 保留 Windows 与 fixture 为长期目标，但从活跃表移入 ROADMAP 远期节，不占 issue
- 收口依据：2026-09-28 人拍板：ROADMAP 阶段 2（API 接入）判完成——ava 宿主已直连模型 API 承担写稿/查询/标题，「不装 coding agent 跑完九步」已满足；「同一选题稿件可复现」记为已放弃的目标（稿件质量由 02.5 人审 + 02.8 对抗审查兜）。Windows 跑通九步与共享 fixture 收口：个人单机、无现实协作者；重开条件＝出现要在 Windows/Linux CUDA 上出片的具体协作者，届时按闸门 A 原判据验收。

### [N42] e2e 夹具每条用例都现场重编码同一批静态媒体
- 状态：**已收口**（2026-09-28 复核）
- 关联：`desktop/e2e/fixtures.ts::buildFixture`（约 23–75 行）；Spec 8 §7
- 原记录（活跃表原文）：2026-09-28 人报、读码核实：每次 `buildFixture()` 同步调 ffmpeg 7 次（含 libx264 现场编码 20 s 的 `05-final.mp4`、3 个 wav、4 张图）与 python 2 次（04-review.html、shots gallery），产物跨用例完全相同。推进：在 globalSetup 里生成一次缓存，用例内用 APFS 克隆（`cp -c`）拷入各自的临时仓库，夹具仍然逐用例独立；指纹用到 mtime 的用例需确认拷贝后语义不变 **2026-09-28 实测（空载）**：`buildFixture()` 单次 663–678 ms（其中 `makeFixtureRepo` 约 12 ms，余下即 ffmpeg×7 + python×2），整轮只有约 10 个调用点（preview / preview-security 用 `beforeAll` 共享，ack/session 系用另一套轻夹具），合计约 7 s，占一轮未打包全量（约 9.5 min）的 1% 上下；「每条用例重编码、数十次」与实际不符。缓存能省的上限就是这 7 s，却要引入缓存失效与跨用例共享产物的新风险，建议不做、归档，待人拍板
- 收口依据：2026-09-28 人拍板不做。实测 buildFixture 单次约 0.67 s、整轮约 10 个调用点≈7 s，占全量约 1%；缓存收益小于缓存失效与共享产物的新风险。

### [N24] 剧场版/长篇剧情场景实体核验
- 状态：**已收口**（2026-09-28 复核）
- 关联：`data/library/notes/天气之子.md:386`；—
- 原记录（活跃表原文）：2026-08-26 实测：「审讯室」实为「警车后座」；02 与 02.8 加强核验。**2026-09-28 复核（只读）**：① 规程现状：「02.8 加强核验」已落成硬交付——`skills/write-script/SKILL.md` 交付清单要求每期 `02-adversarial.md`（02.8 零上下文对抗审查 + 终审裁决），审查含「锚点时间码回笔记对撞场景描述」一节；2026-08-20 起共 8 期留有该文件（夏隧、校条祭、伪恋、你的名字、天气之子须贺、EGOIST ×3）。② 覆盖判定：出错的两期（`2026-08-24` 电车难题 第 142/148 行、`2026-08-26` 阳菜人物志 第 61 行，均写「审讯室」）**恰都没有 `02-adversarial.md`**；之后做了 02.8 的天气之子须贺期，§3 场景对撞抓出了「60:56–61:15 跨场景重叠风险」，说明这一层在工作。但 02.8 是拿稿件对笔记，**错源在笔记本身时它抓不到**——那一层归判据 11（笔记对抗审查须猎杀场景/因果错）；本例笔记已改正（`notes/天气之子.md` 现已无「审讯室」字样）。③ 结论：场景实体张冠李戴在稿件层已被 02.8 覆盖、在笔记层由判据 11 覆盖，本行无需新机制，建议收口迁 archive（已交付的两期视频属历史，不追改）
- 收口依据：2026-09-28 人拍板收口。出错两期（08-24、08-26）恰都缺 02.8；之后每期 02-adversarial.md 的「锚点时间码回笔记对撞场景描述」在工作；错源在笔记层时归判据 11；笔记已改正。

### [D12] BASELINE 语气词密度判据挂起
- 状态：**已收口**（2026-09-28 复核）
- 关联：`skills/write-script/BASELINE.md:80`；P4
- 原记录（活跃表原文）：口语体样本仅 1 篇；P4 E2 已建 diff 对样本积累机制，≥4 期后决定。**2026-09-28 决策准备（只读）**：① 样本计数：「≥4 期」已满足——P4 E2 的「初稿→人改定稿」配对现有 5 期标准期（夏隧、校条祭、伪恋、你的名字、天气之子须贺）+ EGOIST 7 组（含 archive 版本）。② 可观测量先跑（统计脚本不入仓；只数 `配音:` 行，语气词取 啊吧嘛哇呢呀啦哦嘞呗咯喽，按汉字每百字）：12 组初稿密度 0–0.32、定稿 0–0.31，**人改稿前后几乎零变化**（最大差 0.01），EGOIST 全系列恒 0。③ 结论：这个量在本项目的真实写稿链里**不承载人的修改意图**——人在 02.5 从不增删语气词；BASELINE 的「口语体 1.21」唯一来源是一篇非动漫知乎回答（文风参照而非本项目目标）。按判据 1/2（先问测的是不是真实产物、能卡门槛不代表该卡），**建议不立语气词密度判据，D12 收口**，把 BASELINE「量过但分不开、因而不设门禁」表里该行补一句本次实测理由即可（文案改动待人拍板）。候选：(a) 收口（推荐）；(b) 保留挂起，改等「人明确想要口语化文风」时再议
- 收口依据：2026-09-28 人拍板收口：不立语气词密度判据。依据（实测）：12 组「初稿→人改定稿」配对，语气词密度 0–0.32/百字，人改前后几乎零变化——这个量不承载人在 02.5 的修改意图；BASELINE 的「口语体 1.21」唯一来源是一篇非动漫知乎回答。

### [D2] 无台词画面的排片死角——02 强制确认机制未落实
- 状态：**已收口**（2026-09-28 复核）
- 关联：`docs/dev/adr/0005`（判断 4 附近）、ADR-0008 锚点字段、各期 `04-clips.approved.json` 的 `channel`；ADR-0005, ADR-0008, v2重构方案
- 原记录（活跃表原文）：ADR-0008 锚点强制字段已垫底；v2 云端 Qwen2-VL 逐镜头意象打标彻底消灭无台词死角。**2026-09-28 决策准备（只读）**：用全部 21 期（不含 archive 版本）`04-clips.approved.json` 的 `channel`/`status` 实数核：合计 438 段，**锚点通道 235 段、台词通道 144 段、早期无 channel 字段 59 段；「场景」（ADR-0015 VLM 意象）通道 0 段；全部期 `status` 为 ok/ok_extended，零落空**。2026-08-31 之后的期几乎全走锚点（伪恋、你的名字、EGOIST 五期 100% 锚点）——没有台词可检索的画面由写稿时按笔记直接给时间码落片。**更正**：本行原述「v2 云端 Qwen2-VL 逐镜头意象打标彻底消灭无台词死角」与事实不符——画面语义索引（`vindex/*captions*`）共 59 份——EGOIST 素材池 58 份 + 罪恶王冠 S01E01 1 份（其余番未建），但**生产期稿件从未走过该通道**（EGOIST 五期也全走锚点）。结论：死角**已被 ADR-0008 锚点机制消灭**（「02 强制确认」即锚点字段 + `anchor_none` 显式声明，已落地），D2 建议收口迁 archive；VLM 场景通道的去留与本条无关，属 D22/ADR-0015 的账。**推翻条件**：05 人审里出现「无台词段因锚点缺失/写错而落到台词通道错配」被打回的实例，或出现 `no_match`——出现即重开
- 收口依据：2026-09-28 人拍板收口。依据（实测）：21 期 438 段中锚点通道 235、台词 144、场景(VLM) 0，全部 ok/ok_extended 零落空；死角由 ADR-0008 锚点字段 + anchor_none 显式声明消灭。推翻条件：05 出现无台词段因锚点缺失/写错落到台词通道错配被打回，或出现 no_match。

### [D28] 工具循环 10 轮上限对网络调研偏少
- 状态：**已收口**（2026-09-28 复核）
- 关联：`pipeline/agent/llm.py:33`（`DEFAULT_MAX_ITERATIONS`）、`pipeline/agent/tools.py::_tool_web_fetch`；Spec 4, impl-spec B3-r5
- 原记录（活跃表原文）：2026-09-24 S20 门禁 8 冒烟实测：asset 抓 bgm 一色彩羽，搜索页撞游客登录墙（200 但只有导航栏），随后 9 次 fetch 在作品页与 API 间绕路，没去能直接抓到简介的 `/character/26090` 就耗尽 10 轮。10 这个数本身也没写依据。2026-09-24 用户裁决：**不设固定轮数上限**，停止前必须先做无工具收尾总结，防失控改用「人随时中断 + 完全相同的调用拒绝执行」（归二期 Spec 9）；不靠人指路，改为让模型拿到更好的信息：web_fetch 返回页内链接清单，scope 提示写通用研究策略，站点经验进 memory.md（归二期 Spec 13）；crawl/browser 的 extras 由人安装。到顶回显工具原始 JSON 是另一个 bug（`llm.py` 上限时 `final=convo[-1]` 即最后一条 tool 消息），已交 S25——**S25 已修**：`_dispatch_agent_turn` 在 `max_iterations` 时只打 WARN 交人接管，不回显 `final` 原文（`tests/test_agent_director.py::test_repl_max_iterations_warning` 锁死，变异检验杀死）
- 收口依据：核心裁决（不设固定轮数上限，靠人中断 + 本轮判重 + 检查点）由 Spec 9 落地（`llm.py` 的 CHECKPOINT_EVERY 与 `dedup_key`）；三件配套事逐条核实已落地：① `web_fetch` 返回 `links`/`links_truncated`（`web.py::_extract_links`/`_cap_links`，工具描述已同步），实跑 docs.python.org 取回 58 条；② 「联网研究策略」节已进 `config/agent/scopes/creative.md` 与 `asset.md`；③ 站点经验经 `write_memory` 走 ADR-0023 记忆通道（首次写入人确认）；crawl/browser 依赖为 pyproject 可选组并附人工安装命令（crawl4ai-setup / playwright install）。实跑中另发现 `web_fetch` 不处理 `Content-Encoding` 的静默失败，已单列 N45，不属本条。

## 2026-09-28：收尾批评审通过（独立评审 session）

### [N37] VE-1 在负载下的全量 e2e 里稳定红：脱盘后「期列表清空」10 s 内没落定
- 状态：**已解决**（施工 86b09c0；2026-09-28 独立评审通过）
- 关联：`desktop/e2e/visual.spec.ts:201`（VE-1 ⑩ stale 夹具）；嫌疑在 host 脱盘后的期列表推送
- 原记录（活跃表原文，含施工回填）：2026-09-28 D32 施工时发现（与 D32 无关）：6 个 `yes` 占满 CPU 跑未打包全量 e2e，**4/4 次**红在同一处——`await expect.poll(() => episode.count()).toBe(0)` 10 s 超时，实得 **3**；而紧前一行 `reach-banner` 已在 10 s 内出现。VE-1 单跑、负载下单跑、`visual.spec.ts` 整文件、空载全量均绿。所以不是审计判据问题，而是「reach 已翻红、期列表却迟迟不空」：host 的 `refreshEpisodes()` 在 `reach !== ok` 时直接 return，期列表保持旧 Map；清空要靠别的时机（待查是哪条推送、为何在全量套件后段才慢）。推进：先查脱盘后 `episodes.summary`/`episodes.list` 的实际推送序列（打点，不猜），判定是夹具预算问题还是 host 在脱盘后推了陈旧列表的真实缺陷；不许靠调大 10 s 了事（判据 3） **2026-09-28 定位与修复（host 缺陷）**：机理＝chmod 000 后 `listEpisodes` 因 `stat(episodes)` EACCES 返回空；活跃期 tick（1 s）若抢在 reach 轮询（2 s）之前，`dirExists` 失败→当成「期目录被删」→`refreshEpisodes()` 把期列表清空；reach 先翻红则 tick 与 `refreshEpisodes` 都早退、旧列表永留。空载下多半 tick 先到（列表清空），负载下多半 reach 先到（列表保留，VE-1 红）——同一次拔盘界面取决于竞态。Spec 8 §2.8 / §2.4 表明文「保留最后快照并标陈旧」、Spec 14 mock-06（stale 基准）期列表保留 5 行，故判清空为缺陷。修：`tickEpisodeOnce` 发现期目录不在时先 `pollReach()`，数据根不可达即早退（按脱盘处理），可达才当期目录被删。用例：service.test `N37` 两条（tick 抢先的必现序 + 对照组「只删期目录→照常刷新」），另断言脱盘 tick 不推「期目录已不存在」；修前红（列表 `[]`）。变异 2/2（去 pollReach / 去早退）。VE-1 ⑩ 原 `toBe(0)` 正是把该竞态写成了期望，改为「陈旧标记可见 + 期行数与脱盘前一致」，stale 下限 67→83（三主题实测恒 83，下限只提高）。**评审注意**：VE-1 断言与下限是改期望，依据是 Spec 8 §2.8 与 mock-06，请独立核对这一判断；负载下全量 ×3 结果见下。 **负载验收（6×`yes`，未打包全量 ×3）**：VE-1 3/3 绿（修前 4/4 红）；三轮中第 2、3 轮各红 1 条 TI-10（与本条无关，另登记 N44）。
- 评审：✅ 通过。① 修前复现（实测）：手工回退 service.ts 修复 + VE-1 ⑩ 旧期望（`toBe(0)`），6×`yes` 负载下全量 e2e——VE-1 红、其余 110 过 / 2 跳过，与原报「4/4 红在同一处」同签名；还原后两文件逐字节归位。② 改期望依据核对（实测读 spec）：Spec 8 §2.8 原文「脱卸期间……renderer 保留最后快照并置灰标『陈旧』」支持期列表保留，清空才是缺陷；stale 下限 67→83 与「列表保留后 n 增多」自洽。③ 变异 2/2 实跑（还原后 md5 对拍一致）：去 pollReach 重判 → N37 必现序用例红；去早退 → 同用例红。④ 负载验收（实测）：6×`yes` 下未打包全量 ×3 全绿（111 passed / 2 skipped ×3，含 VE-1）。⑤ 边界：diff 只动 service.ts tick 分支、VE-1 ⑩、N37 两条单测，未碰 reach 轮询与 refreshEpisodes 的其它路径。备注：对照组「只删期目录 → 照常刷新」由施工方用例覆盖，评审复跑全绿（含在上面 vitest 抽查）。

### [N38] 桌面端变异 harness 把「e2e 未选中任何用例」记成红，构建坏掉的变异会报**假 KILLED**
- 状态：**已解决**（施工 5dd7825 + 4a13b4e；2026-09-28 独立评审通过）
- 关联：`desktop/scripts/verify-mutations.mjs:84`
- 原记录（活跃表原文，含施工回填）：2026-09-28 N34 施工实测：MUT-62′ 首版的 `//` 注释落在行中间把整行后半截注释掉，`electron-vite build` 失败，e2e 一条用例都没选中，harness 却判 `KILLED (1s) red=1 :: <e2e 未选中任何用例…>`。后果：任何「把构建弄坏」的变异都会被当成已杀死，变异矩阵的 KILLED 数被虚增。推进：构建失败 / 零用例归为 OTHER（或单列 BUILD_FAIL）且不计杀死；补 harness 自测；用新判定复跑 TS 侧全表，确认没有历史 KILLED 实为构建失败 **2026-09-28 修复（`5dd7825` + `4a13b4e`）**：跑器层面的问题从红条名单分离为 `problems`，判定新增 `BUILD_FAIL`（无报告 / 零 spec 时的顶层错误如 global-setup 构建失败 / vitest 文件级失败且零断言）与 `NO_TESTS`（零用例，含 Playwright「No tests found」），二者优先于红条判定、一律不计杀死；用例已跑时的顶层错误（如 Worker teardown timeout）只进 `notes`。旧版假 KILLED 的机理：占位串塞进红条，串里带着 grep（多半就是期望编号）。自测：`tests/harness/verifyMutations.test.ts` 11 条（纯函数，每条关键分支去掉即红）+ `--self-test` 实跑三条人造变异（坏 e2e 构建 / grep 不中 / vitest 导入源码编译失败）——HEAD 旧判定对前两条报 KILLED，新判定分别 BUILD_FAIL / NO_TESTS / BUILD_FAIL。**全表复跑（净树，N41 落地后）**：69 条 → 68 KILLED、1 SURVIVED（MUT-62，矩阵预期）、BUILD_FAIL/NO_TESTS 0——**历史 KILLED 无一实为构建失败**，Spec 10 §7/§8 无需更正。过程中发现首版把 teardown 超时误判为 BUILD_FAIL（MUT-47/48/49，期望用例其实真红），已由 `4a13b4e` 修正并 `--only` 复跑三条均 KILLED。**评审注意**：① 用「先退回旧判定」复现假 KILLED（`--self-test` 的两条 e2e 人造变异即可）；② 挑战「零 spec 才算 build」这条边界：有没有「部分 spec 跑了、构建其实坏了」的形态
- 评审：✅ 通过。① `--self-test` 实跑（实测）：三条人造变异各归其位——e2e 构建失败 → BUILD_FAIL、grep 选不中 → NO_TESTS、vitest 导入编译失败 → BUILD_FAIL；零假 KILLED。② 自测真能红（实测）：把 BUILD_FAIL/NO_TESTS 优先级分支置否后，--self-test 三条全 FAIL（got=SURVIVED，即「没跑就当存活」同样是误判方向）、harness 单测 11 条红 2 条；还原后 md5 对拍一致、自测复绿。③ 抽查复跑（实测）：MUT-47（首版被 teardown 超时误判 BUILD_FAIL 的那条）经 `--only` 重跑得 KILLED (184s)，MUT-62 得 SURVIVED——与矩阵「等价变异」预期一致。④ 「零 spec 才算 build」边界挑战：Playwright 构建在 global-setup，构建坏则零 spec 跑，不存在「部分 spec 跑了但构建坏了」的形态；vitest 侧「一文件编译失败 + 另一文件真红」会判 BUILD_FAIL 掩盖真红——方向是保守侧（少记杀死、不虚增），可接受。全表 68 KILLED / 1 SURVIVED / 0 BUILD_FAIL 的数字为施工方转述（结果文件已不在），评审未全量重跑。

### [N41] 桌面端 e2e 每条用例都弹出并激活一个 1280×820 窗口，全量跑下来本机无法正常使用
- 状态：**已解决**（施工 d28c736；2026-09-28 独立评审通过）
- 关联：`desktop/src/main/index.ts`（`createWindow()`：未隐藏、未隐藏 Dock 图标）、`desktop/e2e/fixtures.ts`（`electron.launch`）
- 原记录（活跃表原文，含施工回填）：2026-09-28 人报、读码核实：8 个 spec 文件共 112 个 `test()`，每条独立 `electron.launch()` → `app.close()`，全量约十余分钟、平均 5–6 s 抢一次键盘与前台焦点；变异实跑按条目数放大（N38 全表跑到 47/69 时因此被人叫停）。D37 已有「键盘探针会把按键打进其他前台应用」的同类教训。推进：只对未打包构建且由 e2e 启动的实例加测试开关（启动即隐藏 Dock 图标、窗口 `showInactive()` 不激活 app），**打包版零改动**；不用 `show:false`（Chromium 会压低隐藏窗口的绘制，VE 视觉审计与截图可能失真）；键盘/焦点类用例（TX-14/14b、VE-3 等）在无系统焦点下是否仍成立必须全量实测 **2026-09-28 修复（人裁插队）**：未打包构建新增测试开关 `--ava-test-background`（`fixtures.ts::launch` 默认带上），三件事：`app.setActivationPolicy("accessory")`（不进 Dock、不激活 app）；窗口 `show:false` 后 `showInactive()`（可见、照常绘制、不抢焦点）；**显示之后**再移到主屏工作区右下角只露 32×32（先移后显示会被 macOS 在上屏时拉回，实测）。完全移出屏幕与透明度方案未采用（前者会被判遮挡、Chromium 停绘制；后者人裁暂不做）。实测：窗口内 `visibilityState=visible`、rAF 62/s；前台采样对照——改前单条 VE-3 即被 Electron 抢走，改后整轮全量见下。守卫 e2e `N41`（可见/未聚焦/不在 Dock/dx=dy=32）；变异 3/3（去 accessory→dock:true；照常 show→focused:true；先移后显示→dx/dy 变整窗）。打包版零改动（未打包才解析、打包版本就拒绝未知 argv）。
- 评审：✅ 通过。① 变异 3/3 实跑（还原后 md5 对拍一致）：m1 去 accessory→N41 用例红（dock:true）；m2 showInactive 退回 show→红（focused:true）；m3 先移后显示→红（dx/dy 变整窗）——杀手均为守卫用例 N41 本身。② diff 边界：只动 `main/index.ts`（dev.background 分支，打包版 `readDevSwitches` 不解析该开关）、`fixtures.ts`（launch 默认带开关）、`visual.spec.ts`（守卫用例）、issues 行——打包版零改动成立。③ 全量佐证（与 N44 同轮）：vitest 377 passed、未打包全量 e2e 111 passed / 2 skipped、真实数据零污染；N44 的 TI-10 在 background 模式下绿。备注（转述未复核）：「整轮前台采样 1067 次零抢占」为施工方实测。

### [N40] 协议进程以 SIGINT=SIG_IGN 启动时，`interrupt` 全部失效
- 状态：**已解决**（施工 40cb104；2026-09-28 独立评审通过）
- 关联：`pipeline/agent/protocol.py`（启动序列）、`session.py::TurnInterrupt`
- 原记录（活跃表原文，含施工回填）：2026-09-28 D31 负载复现时实测：非交互 shell 里 `&` 起的后台进程继承 SIGINT=SIG_IGN，Python 不装默认处理器，`pthread_kill(SIGINT)` 被内核丢弃——TP-4（等卡时中断）24/24 红，同一用例前台跑全绿。桌面端经 libuv spawn 会重置信号，当前不受影响；但 harness/脚本/将来别的宿主在后台起 core 就会踩到（中断按钮无效、回合停不下）。推进：启动时显式 `signal.signal(SIGINT, signal.default_int_handler)`（或等效），并补「以 SIG_IGN 继承启动仍能中断」的用例 **2026-09-28 修复（已修·待评审）**：`protocol.py` 在装 SIGTERM 处理器处紧接着 `signal.signal(SIGINT, signal.default_int_handler)`（中断只在 ready 后带 turn_id 才有效，装在这里够早）。新增 TP-4d：Popen 时父进程临时 SIG_IGN 让子进程继承，断言等卡中断 <5 s 内 `turn_finished{interrupted}`——修前红（收不到 turn_finished）、修后绿。变异 2/2：A 删恢复行→TP-4d 红；B 装成 SIG_DFL→TP-4/4d/4b/4c 四红（SIG_DFL 收 SIGINT 直接杀进程，空闲 notice 与回合内落点都接不住，所以必须是 default_int_handler）。原始场景复跑：非交互 `bash -c '… & wait'` 后台跑 TP-4 族 6/6 绿（原 24/24 红）；全量 1957 passed。终端 `ava`（cli.py）不用 `pthread_kill`，只靠 tty 的 Ctrl-C；以 SIG_IGN 启动的终端进程本就是启动方有意忽略（nohup 等），不改。**评审注意**：修后以 SIG_IGN 启动的协议进程也会接住外部 SIGINT（空闲发 notice、回合内中断）——这是本条要的语义，但与「启动方想忽略 SIGINT」相反，host 只用 SIGTERM/shutdown 关停，不受影响。
- 评审：✅ 通过。① 变异 2/2 实跑（`PYTHONDONTWRITEBYTECODE=1`，还原后 md5 对拍一致）：A 删恢复行→TP-4d 红（中断 10 s 无 `turn_finished`）；B 装成 SIG_DFL→TP-4/4b/4c/4d 四红（SIGINT 直接杀进程，空闲 notice 与回合内落点都接不住）——与施工回填一致，且证实非 `default_int_handler` 不可。② 原始场景复跑（实测）：非交互 `bash -c '… & wait'` 后台跑 TP-4 族 6/6 绿（修复前此场景 24/24 红）；`tests/test_agent_protocol.py` + `test_agent_session.py` 63 passed，TP-11（SIGTERM）单跑绿——关停与空闲 notice 语义未破。③ diff 边界：只动 `protocol.py` 启动序列 1 行 + 测试 + issues 行，未碰帧构造与 `_on_term`。施工方提示的语义变化（以 SIG_IGN 启动的进程修后也会接外部 SIGINT）已核：host 关停只用 SIGTERM/shutdown，不受影响，成立。

### [N34] 桌面端 e2e 的四处断言缺口（变异实跑暴露）
- 状态：**已解决**（施工 a9e2a77；2026-09-28 独立评审通过）
- 关联：`desktop/e2e/session.spec.ts`（假进程版 TX-1 / TX-8 / TX-14）、`desktop/src/renderer/App.tsx`（onConnect 调用点）；Spec 10 §7 变异表
- 原记录（活跃表原文，含施工回填）：2026-09-27 M9 桌面端变异 68 条实跑 66 KILLED。① 假进程版 TX-1 只在待答区内数批准按钮（spec 要求整页），MUT-26 在它下面存活 → 目标改指真实 core 版；② 假进程版 TX-8 不断言确认框桩被调用，MUT-39 存活 → 同上；③ **MUT-46（RequestCard key 改下标）PARTIAL**：静态守卫 TG-17 红，但能造并发两卡的假进程版 TX-14 仍绿，DOM 复用未导致第二次 Enter 答到第二张卡，机理**未证实**；真实 core 的卡串行出现、无从检验；④ **MUT-62 SURVIVED**：变异在 App.tsx 的 onConnect 调用点，单测打不到，e2e 无「host 重启跨越自动呼出等待」路径。③④ 待补用例。**2026-09-28 施工**：①② 核实（读码，未重跑）：真实 core 版 TX-1 以 `[data-testid^=request-answer]` 数**整页**按钮、TX-8 断言 `quitStubCalls == 1`，矩阵已指向真实版，无需补。③ MUT-46 取 (a)：机理＝答复即 `setBusy(true)` → 按钮 `disabled` → 焦点离开，第二次 Enter 与 key 无关（推断，与「变异下 TX-14 仍绿」一致）；key 真正守的是**组件状态归属**，新增假进程版 **TX-14b**（第一张卡写反馈并拒绝后，第二张卡的反馈框为空、其拒绝帧 `feedback:null`）。harness 实跑 **KILLED**（TG-17 + TX-14b；TX-14b 原文 `Expected: "" Received: "只改第一段"`）。④ MUT-62：补 e2e **TX-15b**（回合在跑时 kill -9 host → 重连 → 追加新 pending 必须照常呼出；回合中不写对象库，避开新 host 激活时 H1 自愈 supersede 另建新号的干扰——首版用例正是被它假红）。MUT-62 实跑仍 **SURVIVED**，判为**等价变异**：重连后 `fetchConv(当前键, resetAuto=true)` 以新 snapshot 做同一次 reset，其间无可达的 pendings 事件；两处 reset 一起去掉的 **MUT-62′ → TX-15b 红，KILLED**。矩阵与 Spec 10 §8 如实改写（MUT-46 期望杀手改为 TG-17/TX-14b；MUT-62 标等价、新增 MUT-62′），**没有把 SURVIVED 记成 KILLED**。验证：`vitest` 364 passed、`tsc --noEmit` 0、未打包全量 e2e 110 过 / 2 跳过；三条变异均经 `scripts/verify-mutations.mjs` 实跑、还原后 md5 一致。**顺带发现（未修，报人）**（已单独登记为 N38）：`verify-mutations.mjs:84` 把「e2e 未选中任何用例」记成一条红——变异若把构建弄坏（本次 MUT-62′ 首版就是：`//` 注释落在行中间），harness 会报 **假 KILLED**；判定应把构建失败/零用例归为 OTHER
- 评审：✅ 通过（附一处措辞更正）。① 亲自用 N38 修后的 harness 实跑三条（`--only`，净树）：MUT-46 **KILLED**（TG-17 + TX-14b 双红）、MUT-62 **SURVIVED**、MUT-62′ **KILLED**（TX-15b）——与矩阵写法一致；MUT-46 已由 TX-14b 真杀，矩阵与报告未把 PARTIAL 冒充为 KILLED。② 新用例能真红：TX-14b 由 MUT-46、TX-15b 由 MUT-62′ 实证。③ **更正**：矩阵称 MUT-62 为「等价变异」不严谨——同一行自承重连后 `episodes.list`/`conv.snapshot` 失败时 onConnect 的同步 reset 是唯一一次 reset，即失败路径可观测、只是无用例；已把 Spec 10 §7 该行、§8 回填段与 `mutations.mjs` note 改为「成功路径不可观测、失败路径无用例」，判定仍为预期 SURVIVED。④ 🔵 观察（不阻塞）：TX-15b 用 `waitForTimeout(3000)` 作「计数不变」的观察窗，负载下窗口可能不够；后续 `approval-id === \"after\"` 断言兜住误判为绿的风险。本次负载全量 ×3（N37 验收同批）中 TX-14b/TX-15b 3/3 绿。⑤ diff 只动 `e2e/session.spec.ts`、`scripts/mutations.mjs`、Spec 10 与 issues 行，未越界。

### [D32] TA-12 在全量 e2e 负载下红一次：旧代号的 STATUS 结果进了 snapshot
- 状态：**已解决**（施工 e4243fa；2026-09-28 独立评审通过）
- 关联：`desktop/e2e/ack.spec.ts`（TA-12）、host 侧 repoRoot 切换与在途命令互斥；Spec 8, Spec 10
- 原记录（活跃表原文，含施工回填）：2026-09-27 M9 全量 e2e（100 过 / 1 跳过）中唯一红条；单跑 3/3 绿。红的是**防线断言本身**（「旧代号 STATUS 结果不进任何 snapshot」），不是超时类——更像 host 在切换窗口里的真实竞态被负载放大，而非测试 flake。推进：负载下循环复现（如 `--repeat-each` + 并行 CPU 压力），抓到后查切换时代号递增与在途 STATUS 回包的先后；不许靠加等待消掉。**2026-09-28 定位与修复（测试时序竞态，host 代号机制无误）**：给 TA-12 的 MutationObserver 记录打阶段标签后，负载下 15 次未自然复现；注入「`writeClips` 后 6 s 才 `arm`」即必现（2/2），且红的 `05` 出现在 **observer 阶段——切换尚未开始**：它是写入 05 之后、`arm` 抵达 host 之前完成的一次常规活跃期轮询（当前代号，合法），不是旧代号结果泄漏。负载只是拉长了「`arm` 经 main→host IPC 生效」的窗口。反过来，旧顺序下被扣住的若是写入前开始的那次 STATUS，它读到 03.5，防线断言空转。修法（只改用例，host 零改动）：钩子是一次性的，改为「arm→扣住 H1（写入前）→写 05→再 arm→放 H1→扣住 H2（必在写入后开始、读到 05）→确认页面仍是 03.5→装 observer→切换」，全程按条件排序、零新增等待，断言原样保留。验证：注入 6 s 延迟后仍绿；变异「删 `loadStatus` 的代号比较」→TA-12 第 512 行防线断言红（还原后 md5 一致）；TA-12 单跑 3/3 绿；负载（6 个 `yes`）下未打包全量 e2e ×3 中 TA-12 三次全绿，但另有无关红条（VE-1 三次、剪贴板「假设 3」一次，均单跑与空载全量绿，另行登记）；空载全量 108 过 / 2 跳过。**顺带发现（未修，报人）**（已单独登记为 N39）：`requestRepoRootChange` 在切换开始即 `repoGen += 1`，但 `repoRoot` 要到 `resolveRepoRoot()` 才换——切换窗口内新起的 `refreshEpisodeStatuses`（30 s 定时器）会拿到**新代号 + 旧根**，其结果通过代号比较、写入 `summaries` 并推 `episodes.summary`（侧栏摘要，不进期 snapshot）。未构造出可见现场，属纵深缺口；修法候选：`resolveRepoRoot()` 后再递增一次代号，但这触及 Spec 8 代号语义，须人拍板
- 评审：✅ 通过。① 原始复现（实测）：取修前写法、在 arm 前注入 6 s 等待 → TA-12 红在第 512 行防线断言（05 在切换开始前被合法渲染）；修后写法在写入前注入同样 6 s → 绿。② 不变量「切换开始即作废旧代号在途结果」（实测变异，均由第 512 行 `steps.filter(05) == []` 杀死，还原后 md5 一致）：M1 `loadStatus` 去代号比较；M2 代号递增挪到 `resolveRepoRoot()` 之后；M3 切换不递增代号 → 3/3 KILLED。③ diff 只动 `e2e/ack.spec.ts` 的 TA-12 与 issues 行，host 零改动；未新增任何等待（两段扣放按条件排序），防线断言原样未收窄。读码旁证：`refreshEpisodeStatuses` 不经 `loadStatus`、只写侧栏摘要不进 snapshot，不构成绕过（其「新代号+旧根」窗口已单列 N39）。④ 负载（6×`yes`）下未打包全量 ×3（N37 验收同批）：TA-12 3/3 绿；该批另有 TI-10 两次红，与本条无关（N44）。

### [D31] TP-6 在全量负载下偶发红
- 状态：**已解决**（施工 df88840；2026-09-28 独立评审通过）
- 关联：`tests/test_agent_protocol.py`（用例名见该文件 TP-6）；Spec 9, Spec 10
- 原记录（活跃表原文，含施工回填）：2026-09-26 Spec 10 PR0 验收复跑中实测：整套 pytest 下偶发红一次（施工方报红 4、验收方同条件测得红 5）；该用例单独跑 3/3 绿、干净树 3/3 绿，确认与本轮变异无关。风险点是**变异 harness 的红条数被当门禁数字**时，flake 会污染计数（`scripts/verify_mutations.py` 已用「中止轮标 ABORTED」堵住另一类假红，这条是超时/竞态类，未堵）。推进：先复现并定位（怀疑与 TP-6 的 busy 帧时序有关），再决定是收紧时序断言还是给该用例加隔离；不调阈值。**2026-09-27 M9 复现**：Spec 9 变异全表实跑（4 分片并行、机器满载）期间再次出现同形态偶发红，单跑仍绿；harness 已加单轮 15 分钟超时，但计分仍会被它污染，复跑非 KILLED 条目时需单独确认。**2026-09-28 修复（测试侧竞态，非 core 缺陷）**：负载下自然复现未抓到（TP-6 单条 96 次、协议+会话四文件 15 轮×94 条、全量 3 轮，全绿），改用注入定位——在两次 `send` 之间插 0.3 s（模拟测试进程被抢占）即必现，失败原文 `等不到 error；已收到 [ready, notice, stop_points, turn_started, assistant, turn_finished, stop_points, turn_started, assistant, turn_finished, stop_points]`：假端点秒回，「甲」已跑完、「乙」开了第二轮，E_BUSY 无从产生。core 的忙判定（读者线程置 `in_flight`）无误。修法＝等待条件化：「甲」改为停在 `test_slow` 人审卡上（卡未答复＝回合必在进行中）再发「乙」，断言收紧为恰 `E_BUSY`、两条坏帧恰 `[E_BAD_REQUEST, E_BAD_REQUEST]`、被拒帧不进对话；零 sleep / 零 skip / 零阈值改动。验证：注入 0.5 s 间隔仍绿；变异 2/2（删 `E_BUSY` 分支→TP-6 红；键集合校验放宽为 ⊇→TP-6 红，旧断言 `in codes` 放得过它）；负载（8 个 `yes` 占满 10 核）下全量 `uv run pytest` 1956 passed ×3。harness 无需加「单跑复核」标记（竞态已从用例里消除）。**顺带发现（未修，报人）**（已单独登记为 N40）：协议进程若以 SIGINT=SIG_IGN 启动（非交互 shell 里 `&` 起的后台进程默认如此），Python 不装默认 SIGINT 处理器，`interrupt` 全部失效（TP-4 24/24 红）；桌面端经 libuv spawn 会重置信号，不受影响，但 harness/脚本若在后台起 core 会踩到
- 评审：✅ 通过。① 原始复现（实测）：取修前写法、在两次 send 间注入 0.3 s → 红（等不到 E_BUSY，进程超时被杀 finish()==-9）；同注入下新写法绿。② 「为什么不是 core 竞态」（读码）：`protocol.py::_dispatch` 由读者线程在读到「甲」时同步置 `in_flight`，「乙」拿不到 E_BUSY 只可能是「甲」整轮已结束（主循环 finally 清标志），判测试侧竞态成立。③ diff 只动 `tests/test_agent_protocol.py` 的 TP-6 与 issues 行；无 sleep / flaky / skip，断言收紧（E_BUSY 精确、两条 E_BAD_REQUEST、被拒帧不进对话）。④ 变异 3 条实跑（还原后 md5 一致）：删 E_BUSY 分支 / 忙时回 E_NOT_READY（旧断言 `in (E_BUSY, E_NOT_READY)` 会放过）/ 回合结束不清忙标志 → 均被 TP-6 杀死。⑤ 负载（6×`yes`）下全量 `uv run pytest` ×3：1957 passed ×3（实测；N40 修复后 `&` 后台 SIG_IGN 假象亦不再出现）。

### [N36] 会话头降级文案溢出并与对话流重叠
- 状态：**已解决**（施工 db44529；2026-09-28 独立评审通过）
- 关联：`desktop/src/renderer/SessionHeader.tsx` + `style.css`；Spec 10 §2.9 第 6 条
- 原记录（活跃表原文，含施工回填）：2026-09-27 M9 门禁 6(b) 实测（钥匙串条目删除后）：`LLM 未就绪：…` 与 `设置密钥：security add-generic-password -s ava -a CPA_API_KEY -w ｜不注入密钥…` 整段在会话头换行后越出自身盒子，与对话流首行、消息气泡互相压字（截图两处独立复现）。文本内容本身正确、命令与 `secrets.ts:15` 逐字一致，属排版缺陷；建议给该块加 `max-height + overflow: auto` 或收成可展开的一行。**2026-09-28 施工**：取「max-height + overflow」（不改 DOM；`<details>` 要动元素种类，越出 Spec 14 重构边界）。根因 = `ui.css` 的 `.session-head` 写死 `height: 40px` + `align-items: center`，多行文案向上下两个方向越出（900 宽实测子元素顶端 299.5、头部顶端 378.5）。`ui.css` 有 sha256 冻结，覆盖写在 `style.css`：`height:auto; min-height:40px; max-height:30vh; overflow-y:auto; flex-wrap:wrap`，单行时仍是 40px；零颜色、零字号、零文案与 `data-testid` 改动，也没改 TSX。用例 e2e `N36`（900/1280 两种宽度，按几何断言）：头部底边不超过对话区顶边、子元素全部在头部盒子内、常规窗口下 `scrollHeight ≤ clientHeight`（挡住「40px + 内部滚动」式假修）、`key-problem` 整段选中后含完整命令。变异 2/2：删规则（原状）→ 子元素越出顶端红；保留 40px 只加滚动 → 子元素越出底端红。深/浅两态截图已目视：无压字、配色不变。代价：800 高窗口里降级态头部约占 200px，对话流变窄（上限 30vh）。验证：vitest 364 passed（含 VS-3/4/5 扫 style.css 与冻结 sha256）、`tsc` 干净、未打包 e2e 108 passed / 2 skipped
- 评审：✅ 通过（打包版真机门禁 6(b) 留人）。① 深/浅两态（实测，未打包构建 + `emulateMedia` 截中栏）：降级说明与 `security add-generic-password -s ava -a AVA_TEST_KEY -w` 整段落在头部盒子内、不压对话流，两态文字均可读；命令仍在同一文本节点可整句选中（用例末段断言）。diff 只动 `style.css` 一条 `.session-head` 覆盖，文案与 `data-testid` 零改动。② 只引用 `--space-1`，不新增颜色/字号；`ui.css`（sha256 冻结）未动，未越 Spec 14 边界。③ 断言是几何的（头部不压 `.conv`、子元素全在盒内、常规窗口 `scrollHeight ≤ clientHeight`），不是只看文案：变异 3 条实跑（还原后 md5 一致）——A 删掉覆盖（原状）→第 453 行红；B「固定 40px + 内部滚动」假修→第 454 行红；C 只换行不长高→第 454 行红。观察（不阻塞、交人判）：窄中栏下降级态头部占去大半高度，对话流只剩一线（施工报告已提）；中栏「人时」行贴左缘无内边距，属既有排版、与本条无关


### [N32] 会话未启动时「素材模式」按钮可点，但点了静默无反应
- 状态：**已解决**（施工 96a3b86；2026-09-28 独立评审通过）
- 关联：`desktop/src/renderer/SessionHeader.tsx`（素材模式按钮）；Spec 10 §2.6
- 原记录（活跃表原文，含施工回填）：2026-09-27 M9 真实 core 联调中发现：无会话时 scope 切换无处可发，界面既不禁用也不提示。应禁用或给出「先开始会话」提示；归 M10 之后的 UI 收尾。**2026-09-27 施工**：取「允许点、给明确提示」（每次点击都有可观察结果；禁用态只能靠 tooltip 说明原因，且不自动起会话，守 H-8）。`SessionHeader.tsx`：`onClick` 首句仍是 `isTrusted` 守卫，其后 `!live` → 置提示并 return，零 RPC；提示 `data-testid=scope-needs-session`（`role=status`、`muted`），换期（`convKey` 变）即清、会话起来后不再渲染；有会话时行为零变化。用例 e2e `N32`（页面侧 `MessagePort.postMessage` 观察者计 RPC）：无会话点击 → `conv.send/conv.command` 为 `[]` 且提示可见；对照组发一条消息后同一按钮发出 `conv.command`、stdin 恰 1 行 `command`、提示消失。变异 3/3：α 删早退（原状）→ 提示断言红；β 早退不出提示（假修）→ 提示断言红；γ 出提示不早退 → 「零 conv.command」断言红。验证：`npx vitest run` 364 passed（含 TG-4′/TG-10）、`tsc` 干净、未打包 e2e 107 passed / 2 skipped
- 评审：✅ 通过。① 原始缺陷（实测）：删掉无会话早退（退回修复前）→ e2e N32 红（提示不出现）；HEAD 绿。② 变异 4 条实跑（还原后 md5 一致）：A 退回原状→N32 红；B 早退但不给提示→N32 红（`Expected: visible`）；C 把早退挪到 `isTrusted` 首句之前→vitest 静态守卫两条红（「conv.* … 全部合规」「approval.decide 与 conv.answer 全部合规」），e2e 照绿——首句守卫由静态层看守；D 给提示但不早退→N32 第 412 行 `expect(sent()).toEqual([])` 红（零 conv.command 断言是真的，不是只看文案）。③ 有会话对照组在 N32 用例后半段、HEAD 绿；vitest 364 全绿（TG-4′/TG-10/isTrusted/只挂原生元素静态守卫均在内）。边界：只动 `SessionHeader.tsx` + e2e；新增 1 个 `data-testid` 与 1 句提示文案、只用既有 `muted` 类


### [D38] 退出确认把**空闲**会话也算 busy，与「全部空闲时直接退」不符
- 状态：**已解决**（施工 68c723b；2026-09-28 独立评审通过）
- 关联：`desktop/src/host/sessions.ts::quitState`（对 `phase !== "exited"` 一律收）；Spec 10 §2.10 第 3/5 条、用户裁决「有活动会话时退出 → 弹一次确认；全部空闲时直接退」
- 原记录（活跃表原文，含施工回填）：2026-09-27 M9 打包版实测：唯一会话状态为「空闲」时按 Cmd+Q，仍弹原生确认框（正文列出「选题会话 · 空闲」）。第 5 步本身对空闲会话的处理正确（写 `shutdown`、会话 1 s 内发 `bye` 退 0），故这只是「该拦的拦了不该拦的」：全空闲时应当直接退。**2026-09-27 施工**：根因=`quitState()` 与第 5 步 `stopAllForQuit()` 各写一套「忙」判定——前者对 `phase ∉ {exited,none}` 一律收，后者才按「回合在跑或有未答卡」分 shutdown/SIGTERM。修在 host：抽出 `isBusyForQuit(s)`（`turnId !== null |  | open.length > 0`，§2.10 第 5 步原文），`quitState` 与 `stopAllForQuit` 共用；main 零改动（`busy.length === 0 → proceedQuit` 本就对），`sessions-down` 与两个兜底定时器语义未动。用例：host `TH-9⑤`（全空闲 → `[]`；回合在跑、回合已完但有未答卡各列一条，逐字段断言）；e2e `TX-8g`（全空闲 + 「取消」桩 → 仍在 8 s 内退出、会话收 `shutdown` 零信号）、`TX-8h`（回合在跑 → 桩 1 次、列表 `["SESS-A · 运行中"]`、取消后 app/host/回合存活、零 shutdown 零信号）。变异 3/3（还原后 md5 对拍）：A 退回原状（quitState 不过滤）→ TH-9⑤ 第一条断言 `toEqual([])` 红 + e2e TX-8g 红；B 判定去掉 `open.length` → TH-9⑤ 红（CARD 漏列）；C 判定去掉 `turnId` → TH-9⑤ 与既有 TH-9④ 红。验证：`npx vitest run` 364 passed、`tsc --noEmit` 干净、未打包 e2e 全量 106 passed / 2 skipped。**未实测**：打包版门禁 6 复跑（「空闲直接退 / 忙弹框」）——打包版 Playwright 驱动不了、确认框又禁止键盘自动化，留给人手（可与 D37 配方第 6 步一起做）
- 评审：✅ 通过（打包版门禁 6 留人）。① 原始缺陷复现（实测）：把 `quitState` 退回修复前（对所有活会话一律收）→ TH-9⑤ 红、e2e TX-8g（全空闲 Cmd+Q 不弹框直接退）红；HEAD 两者皆绿。② 变异共 3 条实跑（还原后 md5 一致）：A 退回原状→TH-9⑤ + TX-8g；B 忙判定去掉「有未答卡」→TH-9⑤；C 忙判定恒真→TH-9④⑤ + TX-8g。忙路径对照：TX-8、TX-8h 在三条变异下都绿（该拦的仍拦），真实 core 版 TX-8「回合在跑→确认框列出该期→SIGTERM、`turn_end.wrapup == skipped`、8 s 内进程消失」HEAD 实跑绿。③ diff 只动 `host/sessions.ts`（抽出 `isBusyForQuit` 供 `quitState` 与 `stopAllForQuit` 共用），`main/index.ts` 零改动，`sessions-down` 与兜底定时器语义未动。**未实测**：打包版门禁 6「空闲直接退 / 忙弹框」——确认框禁止键盘自动化，留人与 D37 配方第 6 步同场做


### [N35] `cmd_fetch` 无条件先查 yt-dlp，直链（本该走 curl）也走不到
- 状态：**已解决**（施工 b73749e；2026-09-28 独立评审通过）
- 关联：`pipeline/acquire.py::cmd_fetch`（`fetch_argv(..., yt_dlp=yt_dlp_argv())`）、`yt_dlp_argv`；Spec 6
- 原记录（活跃表原文，含施工回填）：2026-09-27 M9 门禁 12 实测：URL 后缀 `.mp4`（`DIRECT_EXT` 命中、`pick_fetcher → "curl"`），但 `acquire fetch 7` 仍以「FAIL 找不到 yt-dlp」退 1 —— 因为 `yt_dlp_argv()` 在 `fetch_argv` 之前被求值并 `raise SystemExit`。本机 yt-dlp 既不在依赖也不在 PATH，**故本章所有抓取卡在本机恒失败**（#3、#7 两次实测均为该错）。修法方向：把 yt-dlp 的查找推给 `pick_fetcher == "yt-dlp"` 那一支，或先判 `--dry-run` 的 fetcher 再取 argv。**2026-09-27 施工**：`cmd_fetch` 改为 `yt_dlp_argv() if pick_fetcher(url) == "yt-dlp" else None`，只有页面那一支才找 yt-dlp；`yt_dlp_argv`/`fetch_argv`/报错文案零改动，`--dry-run` 与真跑共用同一个 argv，一并修好。用例 `tests/test_acquire.py::TestCmdFetchWithoutYtDlp`（夹具 monkeypatch `shutil.which`/`find_spec` 并先自检 `yt_dlp_argv()` 确实报错）：直链 dry-run 打印 curl 命令；直链真跑（`subprocess.run` 桩）argv[0]==curl 且台账 +1；页面 URL 报原错且文案逐字。变异 3/3（`PYTHONDONTWRITEBYTECODE=1`，还原 md5 对拍）：a 退回无条件查找→两条直链用例红；b 条件取反→三条全红；c 永不查找（静默退回裸 `yt-dlp`）→页面用例红。全量 `uv run pytest` 1953 passed。真实 data 只读核对（PATH 剥掉全部 yt-dlp）：`fetch 3 --dry-run`（页面）逐字报原错；真实清单两条直链 #1/#2 已在台账、查重先拦，看不到 curl 分支。**未实测**：门禁 12「批准→真抓→台账 +1」在真实 data 上复跑——需往真实清单加探针、跑完从清单/台账撤掉（删改真实数据，留给人手或评审）；另注：本机现已有 yt-dlp（`/opt/homebrew/bin` 与 Python.framework 各一份），原环境已不自然复现
- 评审：✅ 通过（门禁 12 真实数据一项留人）。① 原始复现与修复（实测，真 CLI、零 monkeypatch）：沙箱 `data/`（非 T7）+ 本地 HTTP 服务一个 `.mp4`，`env -i PATH=/usr/bin:/bin`（brew 的 yt-dlp 不可见；venv 里也无 `yt_dlp` 模块）——修复前 `acaae0c`：直链与页面 URL 都 `FAIL 找不到 yt-dlp` 退 1；HEAD：直链走 `curl`、退 0、落盘文件与源逐字节一致、台账 +1；页面 URL 仍报原 `FAIL 找不到 yt-dlp。二选一：…` 退 1。② 变异 3/3 实跑（还原后 md5 一致）：A 退回无条件求值→两条直链用例红；B 判定取反→三条全红；C 永不求 yt-dlp→页面原错文案用例红。③ **门禁 12「批准抓取卡→真抓→台账 +1」未在真实 data 上复跑**：要在 T7 真实清单/台账里加探针再删行，属删改真实数据（红线 1），评审不擅动；上面沙箱真 CLI 已覆盖同一代码路径，真实数据复跑留人（可与 D37 配方同场做）


### [N33] idea 会话收到 `shutdown` 时多发一条「空闲态收到中断，已忽略」notice
- 状态：**已解决**（施工 74d2cd5；2026-09-28 独立评审通过）
- 关联：`pipeline/agent/protocol.py::_idle_notice` 调用路径；Spec 9 §3.1
- 原记录（活跃表原文，含施工回填）：2026-09-27 M9 真实 core 联调观测：idea 进程关停流程里的中断被当成空闲中断提示了一次，随后正常退出。无功能后果（进程照常结束、帧仍逐键合规），但对话流尾部多一行误导性提示。修法待查关停序列里信号与 shutdown 帧的先后。**2026-09-28 定位与施工**：先用真实 core 探针实测了 idea/期会话 × 4 种关停（跑过一轮后）：shutdown 帧、EOF、「shutdown 后 EOF」都只有 `bye`，**只有空闲时收到 SIGTERM** 会先冒一条 `interrupt_ignored` 再 `bye`（idea 与期会话相同）。所以「收到 shutdown 时」这句描述不准，实际触发源是 SIGTERM。根因 = `_on_term` 不管有没有回合都 `host.interrupt.request()`，空闲时这一枪打在主循环的 `out.get()` 上，被 MUT-31 的空闲分支接住。修法：`_on_term` 入队 shutdown 后，`in_flight` 为假就直接 return（入队本身就能唤醒主循环；这与 shutdown 帧分派用的判定一致）。忙时语义不变，MUT-31 分支与帧键零改动，`verify_mutations.py` 的 124 个锚点仍各自唯一命中。用例：`test_tp4c_idle_shutdown_sends_no_interrupt_notice[sigterm | shutdown]`（关停段 == `[bye(shutdown)]`、退 0）；对照组 `test_tp4c_idle_sigint_still_notices`（未关停时的空闲 SIGINT 之后恰一条 `interrupt_ignored`、进程存活）。变异 3/3（`PYTHONDONTWRITEBYTECODE=1`，还原后 md5 对拍）：m1 原状 → TP-4c[sigterm] 红；m2 假修（删掉空闲 notice）→ 对照组红；m3 判定取反 → TP-4c[sigterm] 与既有 TP-11 红。验证：`uv run pytest` 1956 passed；桌面端真实 core e2e `sessionReal.spec.ts` 27 passed
- 评审：✅ 通过。① 原始复现（实测）：把 HEAD 的 TP-4c 放到修复前 `acaae0c` 上跑，`[sigterm]` 红——关停段为 `[('notice', …), ('bye','shutdown')]`，与 issue 所述「多一行空闲中断提示」一致；`[shutdown]` 与对照组在旧代码上本就绿（证实施工方「触发源是空闲 SIGTERM 而非 shutdown 帧」的更正）。② 变异 3/3 实跑（还原后 md5 一致）：A 去掉空闲早退→TP-4c[sigterm] 红；B 判定取反→TP-4c[sigterm] + TP-11 红（回合中 SIGTERM 不再打断）；C 删掉空闲 notice→对照组 TP-4c_idle_sigint 红——未关停时的空闲 SIGINT 仍发 notice，MUT-31 语义未破。③ diff 未触及任何帧的构造，键集零变化。备注：用例里有两处 `time.sleep(0.3)` 用于等残余帧落定，不承担判定，不构成时序依赖


### [D36] 作废/已关闭的抓取卡不从待答区撤下，徽标与真源不一致
- 状态：**已解决**（施工 7b9b705；2026-09-28 独立评审通过）
- 关联：`desktop/src/host/sessions.ts`（`s.open` 维护）、`renderer/convStore.ts`；Spec 9 §3.1 `request_closed`、S9-R4、Spec 10 §2.4
- 原记录（活跃表原文，含施工回填）：2026-09-27 M9 实测：`session.jsonl` 已 `request_closed(voided #6)` 且只 `request_opened(#7)`，界面待答区仍显示 **#6**、`episodes.summary` 徽标报「2 张卡待答」。按「刷新」后 #6 与 #7 两张卡同时出现。另一次（#4 关闭 → #6 打开）是**滞后十余秒后自愈**。host 的 `request_closed` 分支对 `reason` 无过滤（answered/voided 都删），故「帧没到 host」与「host→renderer 推送丢/迟」两条链都要查；建议补「voided 关闭后徽标立即归零」用例。**2026-09-27 定位与施工**：两条嫌疑链都不是——**帧到了 host，但被 host 当坏帧丢了**。core 的作废帧省略 `decision`（只在非 None 时写）、另带 §3.1 表外的 `cause`；host 的 `parseOutFrame` 按 Spec 9 §3.1 要求 `request_closed` 的 `decision` 在场（string/null）→ 整帧 `malformed` → 记 `frames_lost` 不进 `onFrame` → 卡与徽标永不撤下（answered 帧带 decision，所以答复路径一直正常；TX-0 只跑过 answered 路径，没覆盖作废）。打点证据：把修复退回后跑新用例 TX-0b，快照里恰在关闭处出现 `frames_lost{reason:"malformed"}`、`open` 仍含该卡、`framesLost:1`——即 M9 现场。修法在 core（host 严格校验与 spec 一致，不放宽）：`ProtocolChannel._close` 帧键恰为 `request_id/reason/decision(+rid)`，作废 `decision:null`；`cause` 只进盘（session.jsonl 记录照旧带）。「#4→#6 滞后十余秒自愈」同源（作废帧丢失），**自愈机理未实证**（推测为进程退出时 `finishExit` 清空打开集合）。用例：TP-5/TP-5c 断言作废/答复帧键集；host TH-18（作废帧 → `open` 与徽标同步收缩、零丢帧、末条 delta 已不含；另钉根因：缺 decision 的帧判 malformed）；renderer `convStore` delta 收缩用例；e2e TX-0b（真实 core：待答时点停止 → 作废帧过 `parseOutFrame` 且逐类键集对拍、`framesLost==0`、卡与「张卡待答」立即消失）。变异 2/2 杀（作废帧省略 decision → TP-5 + TX-0b；多带 cause → TP-5 + TX-0b）；S9-MUT-19 因 `_close` 签名去掉 `cause` 重锚，实跑仍 KILLED（TP-5）。验证：pytest 1950 passed、vitest 363 passed、tsc 通过、未打包 e2e 104 passed / 2 skipped
- 评审：✅ 通过。① 现场（实测）：在修复前 `acaae0c` 与 HEAD 上各让真实 core 在卡待答时读到 EOF，抓下 `request_closed` 原帧——旧帧 `{reason:"voided", cause:"interrupted"}`（无 `decision`、带表外 `cause`），喂给 host 真实 `parseOutFrame` 得 `{ok:false, reason:"malformed"}`（整帧丢弃，卡与徽标因此不撤）；HEAD 帧 `{reason:"voided", decision:null}` 解析 ok。根因在 core 成立，host 未放宽。② 变异 3 条链路各一（还原后 md5 一致）：A core 作废帧省略 decision→TP-5 红；B host 只对 answered 撤卡→TH-18 红；C renderer 在 delta 的 open 为空时保留旧 open→**vitest 全绿（convStore 用例只测 q6→q7 替换、没测收缩到空）**，但 e2e TX-0b 红（卡计数期望 0 实得 1）——被 e2e 杀死，单测层有缺口但不阻塞。③ diff 无 renderer/host 源码改动，不存在周期性重取 snapshot 之类掩盖式补丁。空载未打包全量 e2e 108 过 / 2 跳过。未复核（转述）：「#4→#6 滞后十余秒自愈」机理


### [D34] 抓取卡按**整份清单**出卡，而不是按本轮 `acquire_propose` 传入的 URL
- 状态：**已解决**（施工 3e8172e；2026-09-28 独立评审通过）
- 关联：`pipeline/agent/session.py::_fetch_cards`（`for index, candidate in enumerate(candidates, 1)`）；Spec 9 §2.4.4 第 2 条、Spec 6、ADR-0021
- 原记录（活跃表原文，含施工回填）：2026-09-27 M9 门禁 12 实测：本轮只提案本地探针（清单第 7 条），弹出来的却是**候选 #3（真人 YouTube 候选）**；批准后 `acquire fetch 3` 真被执行（本次因缺 yt-dlp 失败，零落盘零台账）。spec 原文是「对**本次输入的每个 URL**（按输入顺序去重）在清单中且不在台账 → 一张卡」。后果：一次 `acquire_propose` 会把清单里全部历史未抓候选逐张推给人，批准即真抓——人以为只批了自己这轮提的那条。**2026-09-27 施工**：`_post_execute` 把工具入参经 `_proposed_urls(args)`（按输入顺序去重、与 `propose_candidates` 同样 strip）传给 `_fetch_cards(outcome, urls)`；出卡只遍历本次输入的 URL，须在清单中（序号取清单里首次出现的 1-based N）且不在台账。`tools.py` 工具实现与 schema、`LoopControl` 签名、`cmd_fetch` 均零改动。TK-2/3/3b/3c 夹具改为传入 spec 所述的 4 条入参（期望值不变）；新增 TK-2b（清单有历史未抓候选、本轮只提 1 条 → 只 1 张卡、序号 4）、TK-2c（同 URL 提两次 → 1 张、出卡顺序随输入）。变异 4/4 杀（退回全清单遍历、去掉去重、序号取输入位置、不 strip）。全量 1950 passed
- 评审：✅ 通过。① 评审自写的独立场景（真 `run_turn` + 真 `acquire_propose`，未入库）：清单预置 2 条历史未抓候选，本轮只提案 1 条新的——修复前 `acaae0c` 出 3 张卡（#1 历史甲、#2 历史乙、#3 新），HEAD 只出 `(#3, 新)` 且批准后执行器只收到 3；② 同一调用里同 URL 提两次→HEAD 只 1 张卡。③ TK-3（misaligned）、TK-3b/3c（fetch_disabled_*）在 HEAD 复跑全绿。变异 4 条实跑（还原后 md5 一致）：A 退回全清单→TK-2b/2c 红；B 去掉去重→TK-2c 红；C 不 strip→TK-2b 红；D 序号改取末次出现→**存活**，但属不变量下的等价变异：`propose_candidates` 对清单与台账做 URL 精确串双源判重，清单里不会有重复 URL（只有人手改清单才可能触发），不要求补用例。边界：只动 `session.py` 与测试，`tools.py`/`cmd_fetch` 零改动（已核 diff）


### [D35] 抓取卡上的「停止」停不下回合：只作废当前卡，随即弹下一张
- 状态：**已解决**（施工 3b58c9d；2026-09-28 独立评审通过）
- 关联：`pipeline/agent/session.py::_ask_fetch` 的 `except KeyboardInterrupt` 分支；Spec 9 §2.2 状态表（抓取中中断 → 其余候选卡 `voided`）、H-6
- 原记录（活跃表原文，含施工回填）：2026-09-27 M9 实测：`session.jsonl` 序列 `request_opened(#6) → request_closed(voided #6) → request_opened(#7)`，回合仍在跑（无 `turn_end`、UI 仍显示「停止」）。机理：`_ask_fetch` 吃掉 KeyboardInterrupt 后 `return record`，`_fetch_cards` 循环继续问下一张。按 spec 应「中断即整轮停止 + 其余候选卡 voided」。**2026-09-27 施工**：`_ask_fetch` 的中断（等答复时 → `voided`；抓取 job 中 → `interrupted`）改抛内部 `_FetchStopped`，`_fetch_cards` 停止出卡、余卡只记 `voided` 不开卡，再以 `FetchHookInterrupted`（KeyboardInterrupt 子类，携带 outcome）上浮；`llm.py` 为钩子新增 `hook` 阶段，落点处理提交**真实**提案结果（带抓取记录、执行计数不重复加），同回复后续调用补「未执行」，随后照常收尾，`turn_end{interrupted}` 一条。新增 TK-3d/3e/3f（真 `run_turn` + 真 `acquire_propose`）。变异 5/5 杀（退回 `return record`、余卡不 void、`hook` 分支落回合成结果、`_fetch_cards` 无视停止标志、job 中断不接）；S9-MUT-5 锚点因插入 `hook` 分支重锚并实跑仍 KILLED（TL-7）。全量 1948 passed。残余：中断若恰落在 `ask` 之外的几行（如 `_record`），会以普通中断浮出，提案结果照常但丢该次抓取记录（回合仍正确停下）
- 评审：✅ 通过。① 原始复现（实测，会话层真 `run_turn`）：把 HEAD 的 TK-3d/3e/3f 放到修复前的 `acaae0c` 上跑，三条全红——第 1 张抓取卡上中断后仍出了 3 张抓取卡（`['tool_call','fetch','fetch','fetch']`）、回合以 `done` 结束；**未在协议子进程级复现**（与施工报告同一局限）。② 变异 4/4 实跑（评审工作树，还原后 md5 一致）：A `_ask_fetch` 退回 `return record`→TK-3d/3e 红；B 余卡改 `break` 不记 voided→TK-3d/3e/3f 红；C `llm.py` 钩子吞掉中断→TK-3d/3e/3f 红；D 抓取 job 中断不转 `_FetchStopped`→仅 TK-3f 红（该守的那条）；另复跑重锚后的 S9-MUT-5→TL-7 红（仍 KILLED）。③ TL-8/TL-9*/TL-17 在 HEAD 全绿，第二次中断不打断收尾的既有语义未破。边界：动了 `llm.py` 的落点处理（`hook` 阶段）——越出提示词点名的 `session.py`，但属把中断连同真实提案结果上浮的必要改动，已被 TK-3d/3e/3f 与 TL-7 覆盖。残余窗口（中断落在 `ask` 外的 `_record` 等处会丢本次抓取记录）如施工报告所述，未另测


### [D33] 协议进程读到 stdin EOF 不退出：host/app 意外死亡后会话永不自清
- 状态：**已解决**（施工 68390e8；2026-09-28 独立评审通过）
- 关联：`pipeline/agent/protocol.py`（EOF 分支）；Spec 9 §2.8、Spec 10 §2.1 第 7 条、A3
- 原记录（活跃表原文，含施工回填）：2026-09-27 M9 打包版联调实测：`sleep 1` 管道喂 stdin、会话读过 EOF 后 **>40 s 仍存活**（采样栈：主线程卡在 `lock_PyThread_acquire_lock`→`__psynch_cvwait`，无超时；`lsof` 显示 fd3/4 已 `->(none)`）。对照组：`shutdown` 帧 **1 s 内**发 `bye` 退 0，故不是「所有退出路径都坏」。后果：18:11–18:27 的 M9 e2e 留下 **12 个 PPID=1 的孤儿 protocol 进程**（租约指向已删临时目录），杀 app 后会话也不自退。修法方向：读端 EOF → 走 §2.8「中断当前回合 → 收尾 → `turn_end` → 退 0」，并补 EOF 退出用例（现 TP 表无此断言）。**2026-09-27 施工**：根因＝`FrameReader._on_eof` 只 `pthread_kill` 不入队，空闲时主线程阻塞在 `out.get()` 的锁等上，macOS 上 SIGINT 唤不醒它（修前复现 >12 s 存活且连空闲 notice 都没发）。修法＝与 `shutdown` 帧同一出路：回合在跑才打中断，然后 `out.put(("eof",…))` 唤醒主循环 → `bye{eof}` 退 0。新增 TP-5b（空闲 EOF 10 s 内退 0、无 notice、租约释放可立即重开）、TP-5c（工具执行中 EOF → 不等工具跑完、`turn_end{interrupted}`）、TP-5 补盘上 `request_closed(voided)→turn_end` 顺序。变异 4/4 杀：不入队→TP-5b；删主循环 eof 出口→TP-5b；回合中不打中断→TP-5c；空闲也打中断→TP-5b（多出 notice）。全量 1945 passed
- 评审：✅ 通过。① 原始复现（实测）：`acaae0c` 上 `sleep 1 | python -m pipeline.agent.protocol --idea` 45 s 后仍存活；HEAD 同场景约 2 s 发 `bye{eof}` 退出。② 变异 3/3 实跑（评审工作树，`PYTHONDONTWRITEBYTECODE=1`，还原后 md5 对拍一致）：A 删 eof 入队→TP-5b 红；B 空闲也打中断→TP-5b 红（多出 notice）；C 回合中不打中断→TP-5c 红——杀手均为该变异该守的用例。③ 反驳式检查：diff 只动 `FrameReader._on_eof`，`shutdown` 分支与 `FRAME_DRAIN_TIMEOUT_S` 零改动；回合中 EOF 走「中断→收尾→turn_end→bye{eof}」（TP-5c 断言 `turn_end.stopped==interrupted`）。未复核（转述）：施工报告里的 12 个孤儿进程现场


## 2026-09-02：须贺期实战踩出并当天修复

### [D22] tts readings 复用比对全表指纹一刀切
- 状态：**已解决**（2026-09-02，commit 5570613）
- 关联：`pipeline/tts.py::_reusable`
- 要点：改一个读音九段全废，WORKFLOW 承诺的「自动重做受影响的段」从未实现
  （须贺期修段 2/8/9 时靠手动对齐 manifest 指纹才保住单段重跑）。修法：Take 落
  speakable（实际喂模型的文本），复用改段级比对；engine/model/ref_audio 仍全局门；
  旧 manifest 无 speakable 退回全表指纹。顺带修掉指纹 sort_keys 对键序不敏感、
  而 str.replace 按插入序生效的静默复用漏检。

### [D23] clips 检索及格 ≠ 可分派
- 状态：**已解决**（2026-09-02，commit 8b69375）
- 关联：`pipeline/clips.py::_ladder / _rescue_starved`
- 要点：须贺期段 4——查询及格的候选全被段 3 锚点占完，`备选` 从未被搜就
  no_source，05 人工指定 60:23 才救回。修法：_ladder 可续爬（`_ladder_steps`
  步序列 + 下一步下标），分配循环饿死检测续爬一级、新命中追加 hits 尾部保住
  「只救不比」段内序；`rescue` 字段落盘，05 审查页显示「首选被占·第 N 级救回」。
  真实索引回放：段 4 备选救回 ok，其余 8 段与线上版逐字节一致。画面通道同构
  缺口暂不扩（只有两级、无实战案例，YAGNI）。

## 2026-08-27：由 ADR-0008 解决并归档

### [D20] 排片机制重构：笔记 Ground Truth 锚点直通排片
- 状态：**已解决**（2026-08-27，ADR-0008 定案并实现落地）
- 关联：`docs/dev/adr/0008-ground-truth-anchor-clips.md`
- 要点：02 写稿强制 `锚点:` 字段（check_script 机检）；clips.py 锚点通道起点吸附
  镜头切点、一等公民先占位；双塔检索降级为 `锚点: 无` 氛围段的补位。
  注意：08-26 期 clips.json 与 approved 逐字节相同（diff 通道污染），不作验证样本；
  验证义务与推翻条件在 ADR-0008 末节，等下期新番实测。

## 2026-09-05：审计收口销号

### [D21] Qwen3-TTS 长段落音色漂移诊断
- 状态：**已作废**（2026-09-05 用户确认销号）
- 要点：原假设（Qwen3-TTS 在长段落上音色漂移）不成立——根因是仓库根目录
  临时脚本绕过 `render_segment` 直接调引擎，丢了质检与裁静音链；走正规管线的
  产物无此问题。附带纪律沉淀为 N23（严禁仓库根目录临时脚本），仍活跃。

### [N26] phase0 集号正则吃不了电影文件名
- 状态：**已解决**（2026-09-05，phase0 新增 `--episode` 显式集号）
- 要点：剧场版/电影单文件无 [NN] 通例可循；现在 `phase0 <电影.mkv> --anime X
  --season N --episode M` 跳过正则由人给号（一次一部），君名/天气之子当初的
  verify/run/sources 三步拆跑绕过方案不再需要。

### [N7] IndexTTS 遗留模型与自写解码循环
- 状态：**已解决**（2026-09-05 审计批次3）
- 要点：4.4G 模型权重经查早已随 2026-08-15 换引擎删除（仅剩空 .locks 目录，已清）。
  自写自回归解码循环（tts.py `_indextts_audio`）审计确认为引擎接缝的活回退路径
  （config `engine=indextts` 可选），非死代码；删除条件（上游 mlx-audio 修复对齐）
  写在该函数 docstring 里，条件达成再清。

## 2026-08 批：由 P3 / P4 / 施工图解决并删除的条目

### [D3] 缩段不注水拍板落地
- 状态：**已解决**（2026-08-14，P4 E1 落地）
- 关联：`docs/dev/plans/2026-08-14-p4-script-pipeline.md`
- 要点：`01-topic.md` 加 `缩段不注水: 是` 字段，字数下限 × shrink_factor；
  允许承认这段没料而缩短，不许注水凑数。

### [D14] TTS CPM 单源化
- 状态：**已解决**（2026-08-14，P3 解决删除）
- 关联：`docs/dev/plans/archive/2026-08-14-p3-doc-debt.md`
- 要点：CPM 取值口径统一，消除多文件默认值漂移。

### [D15] BGM 例外规则同步
- 状态：**已解决**（2026-08-14，BGM 例外规则同步）
- 关联：`docs/dev/adr/0007-no-japanese-subs-support.md` 背景中提及
- 要点：例外曲目来源与理由在 config 与文档间同步完成。

### [D16] 人类介入点口径统一
- 状态：**已解决**（2026-08-14，P3 解决删除）
- 关联：`docs/dev/plans/archive/2026-08-14-p3-doc-debt.md`
- 要点：02.5 / 03.5 / 05 / 09 四处人类介入点在 CLAUDE.md / WORKFLOW.md / STANDARD.md 口径统一。

### [D17] 测试数清算
- 状态：**已解决**（2026-08-14，P3 解决删除）
- 关联：`docs/dev/plans/archive/2026-08-14-p3-doc-debt.md`
- 要点：测试断言与变异检验覆盖范围厘清并补全。

### [N3] shots calibrate 多参数支持
- 状态：**已解决**（2026-08-14 删除）
- 要点：`shots calibrate` 支持多参数标定。

### [N13] 人审手工片段标记
- 状态：**已解决**（2026-08-14 删除）
- 要点：05 人审改 `04-clips.json` 的标记机制落地。

### [N15] TTS 时长带改读 config
- 状态：**已解决**（2026-08-14 删除）
- 要点：TTS 时长估算带由代码硬编码改为 config 项。

### [N18] 切分标定判据补进 CLAUDE.md
- 状态：**已解决**（2026-08-14 删除）
- 要点：过切/漏切取舍的判据写进常驻规则。

---

## 2026-08-18 Pipeline 运行问题与架构缺陷复盘

- 状态：**已修复并验证**（2026-08-18，未提交 git，由人拍板）
- 来源：原 `docs/issues/2026-08-18-pipeline-inconsistency-issues.md`（孤儿文件，无编号）
- 修复项：
  1. **局部重跑级联校验**：`tts.py` 新增 `_stale_downstream()`，写完 manifest 立即对
     `04-clips.json` / `04-clips.approved.json` 跑 `verify_alignment`，有违例当场 WARN 并指明清算命令。
  2. **配置文件前置校验**：`tts.load_config()` 坏 JSON / 缺键在加载处 SystemExit；
     `render.run()` 把 `_maybe_music_plan` / `_bgm_plan` 提前到切片之前解析。
  3. **cover.py 字段 schema 兜底**：缺 `season`/`episode` 时按 `source` 路径反查片源登记表。
- 验证：`uv run pytest` 565 → 584 全绿，变异检验均红。

---

## 2026-09-25：Spec 8 M12 打包版启动被模态框挡住

### [N30] 打包版崩溃过之后，之后的某次启动永远到不了 ready
- 状态：**已解决**（2026-09-25，S23 修复轮，未提交）
- 关联：`desktop/src/main/index.ts::disableWindowRestoration`、`desktop/e2e-packaged/packaged.test.ts` TS-9、Spec 8 §2.10、TS-9、MUT-59
- 根因：app（签名身份 `local.ava.desktop`）有过生成崩溃报告的崩溃（TS-1 篡改副本的 SIGTRAP、SIGABRT 等；SIGKILL 不算）之后，AppKit 在 `finishLaunching` 里弹模态框「上次意外退出，要重新打开窗口吗？」（`-[NSPersistentUIRestorer promptToIgnorePersistentStateWithCrashHistory:]` → `NSAlert runModal`，`sample` 与截屏坐实），没人点就永远到不了 ready。与 Clash、系统策略检查无关。S23 曾误判为「间歇性卡死、TS-1 干扰已证伪」，S24 查清与 TS-1 的关联并发现连续重跑必红。
- 实测：启动中发 SIGTRAP 模拟崩溃后各启动 3 次——改前 7 次崩溃有 5 次随后弹框；在 app 偏好域设 `ApplePersistenceIgnoreState=YES` 后 6 次崩溃 18 次启动零弹框；由 app 在当次启动自己写入（每次启动前删键）6 次崩溃 18 次启动零弹框。
- 修复：打包版 main 在 `boot()` 之前写 `ApplePersistenceIgnoreState=YES`（ava 的窗口不靠 AppKit 恢复）。TS-1 放回 TS-6 之前，整套连跑 3 轮均 11/11；MUT-59（删掉该调用）被 TS-9 捕获。
- 复现（改前）：启动打包版时对 main 发一次 SIGTRAP，再连续正常启动 3 次，卡住时对 main 做 `sample` 即见上述调用栈。

---

## 2026-09-25：v2 M2a/M2b 与一期 Harness 收口归档

### [B1] 排片错配修复方案选定与落地
- 状态：**已解决**（M2b 交付，commit `b257e6d`）
- 关联：`docs/adr/0005:4-6`、ADR-0005、ADR-0015、P1、v2架构方案
- 要点：Qwen3-VL 镜头意象打标 + bge-m3 文-文检索已交付（ADR-0015，commit `b257e6d`），EGOIST 池解封；衍生子题（D1 候选复核四子题、D6 错配率重测）在各自条目独立跟踪。

### [B3] 每期人类投入「≤10 分钟」止损线口径
- 状态：**已被实践否证**（2026-09-19 否证改 `k × 片长`；2026-09-23 用户裁决进一步降为纯观测量）
- 关联：`ROADMAP.md:30,146`、ROADMAP 闸门 A、`AGENTS.md` 第二节
- 要点：原「≤10 分钟」假设被约 20 期实践否证（人类时间 ∝ 成片时长，20 分钟片光 03.5 就 ≥30 分钟）；2026-09-19 修正预算模型为 `k × 片长`（v1.20）并接通 `human_time.json` 记账；2026-09-23 用户裁决撤销硬预算与「连续 3 期停产」判据，降为纯观测量。

### [D4] 视觉索引第 3 层 VLM 落地
- 状态：**已验收**（M2b 交付，commit `b257e6d`）
- 关联：`docs/adr/0003:38-39,95-141`、ADR-0003、ADR-0015、ADR-0014、v2架构方案
- 要点：ADR-0015 的 VLM 意象打标 + 文-文检索已验收合入（M2b）。

### [D5] 第 2 层（画面语义）复活落地
- 状态：**已验收**（M2b 交付，commit `b257e6d`）
- 关联：`docs/adr/0003:431-433`、ADR-0003、ADR-0015、v2架构方案
- 要点：以 Qwen3-VL captions + bge-m3 文-文检索实现，CLIP 余弦正式废弃，M2b 已对已标定池解封。

### [D25] readings 替换表边际成本极高，需将 CJK 音读泄漏收敛为拼音直注脱敏机制
- 状态：**已落地**（M2a 交付）
- 关联：`config/voice.json`、`pipeline/tts.py`、`pipeline/g2p.py`、ADR-0006、ADR-0014、ADR-0017、v2架构方案
- 要点：`pipeline/g2p.py` 拼音直注脱敏层已交付；`readings` 表已降级为个例 override。

### [D6] 排片错配率完成判据未测
- 状态：**已关闭（无存在必要，2026-09-25 用户裁决）**
- 关联：`docs/dev/adr/0003:478`、ADR-0003、ADR-0005、ADR-0015、P1
- 要点：量化通道（`pipeline/recheck.py` 的 diff/probe/score，P1 交付）已建并留存可用。用户裁决：VLM 场景通道只对真实素材及需要用到真实素材的题材生效，纯动漫题材边际效益不足，没必要急着打标与测算错配率；D6 撤销，编号不回填。ADR-0003「完成判据的实测」节的排片错配率一项随之不再追测（封面候选 9/9 一项已测）。若未来接入真实素材题材，需要错配率数字时用 recheck.py 现成通道重开即可。

### [N27] 夏隧的 scene_threshold=10.0 是沿用值，标定实录缺失
- 状态：**已关闭（无存在必要，2026-09-25 用户裁决）**
- 关联：`config/project.json` visual、ADR-0003、D6 同型裁决
- 要点：用户裁决——夏隧一期视频（2026-08-20《逃避的代价》已交付）已用现值正常出片，无任何故障症状，此条不是 bug 只是 R2 记录缺漏，不值得纠结。闭环前已顺手实测密度表留存：夏隧（82.7min 电影，894 镜 @10.0）thr5–15 中位 3.42–3.71s 全落动画单镜头 2–5s 常态区、thr20 才偏粗（p90 22.7s），与天气之子/君名「thr10 即漏切正片」的形态不同——若未来真要补标定，起点证据在 `data/library/shots/calibrate/long-t8.jpg` / `long-t10.jpg`（已生成未目检），届时重开即可。编号不回填。

### [D18] 集号 / 人物字段自觉性缺口导致错配静默发生
- 状态：**已关闭（不立判据，走替代处置；2026-09-25 用户拍板）**
- 关联：`pipeline/check_script.py`、ADR-0005、ADR-0008、B1
- 要点：集号侧早已由 `锚点:` 强制字段 + 集/锚点一致性机检双卡口覆盖（2026-08-27）。「`人物:` 该写没写」经论证不可证伪（提到名字≠画面需求是该角色；强制每段写=逼人编字段，check_script 对集号已判过同款哲学），不立硬判据。用户原则：**锚点才是一等公民**，文案与锚点严丝合缝时 `人物` 不是刚性必须项，且真实素材（无 ccip 人脸链）下 presence 通道整体失效。替代处置：① check_script 加 **INFO 级提示行**（不报 FAIL）——配音含已登记角色名但未写 `人物` 的段列出给人扫一遍，别名表现成（`characters.json`）；② 02.5 人审要点加「`人物:` 该写没写」一条。两个落点已并入二期 Spec 11 范围（`~/Desktop/ava二期-spec设计与红队评审提示词.md` Spec 11 作者第 6 问 + 红队攻击面），随 02.5 编辑器一并施工。`人物` 保持「写了就走过滤、不写不勉强」的纯可选通道选择器。编号不回填。

### [D19] 说话人确认是全流程最不可靠环节
- 状态：**已关闭（不立判据，走替代处置；2026-09-25 用户拍板）**
- 关联：`skills/write-script/SKILL.md:54`、D18 裁决
- 要点：机器判据三条路全堵（字幕 Name 字段不填无真实标签；presence 答「谁在场」答不了「谁在说话」，画外音/反应镜头是常态，不可证伪；声纹要新建标定链，成本形态同 D6 裁决）。替代处置：① 「某某说」类断言的真实性由锚点机制承重（台词必须锚到真实字幕行）；② 说话人核对保留为 SKILL.md:54 既有抽帧流程约束（人审环节）；③ 可选增效：说话人断言联系表工具（扫 02-script.md 的「X 说」断言批量抽帧拼一张表，人一次过完，复用 `review._frames()`）——纯人审辅助，明确不是判据，需要时再建。编号不回填。

### [D27] 立项前工作无入口：ava 启动形态缺无期选题会话
- 状态：**已解决**（2026-09-21 实施与 15 组变异全量通过，commit `7866ded` / `527ce56`）
- 关联：`pipeline/agent/cli.py`（`create_new_episode` / `select_episode_interactive` / `main`）、`docs/dev/plans/archive/2026-09-21-ava-entry-idea-scope.md`、impl-spec §2.3
- 要点：解决容器先于内容的顺序倒置（2026-09-20 需求交接）；新增 `ava idea` 无期选题会话（只读 4 工具、零写权限）与 `ava new <名>` 建目录后直进对话，15 组变异全杀闭环。

### [N29] 镜头画廊「复制锚点」按钮点不动
- 状态：**已解决**（2026-09-25 插单修复 commit `7f297cc`，55/58 画廊已重生成）
- 关联：`pipeline/shots.py` `gallery()` 的 `onclick="cp(this, {json.dumps(anchor, …)})"` 一行、Spec 8 §2.7 RF-7
- 要点：`json.dumps` 的双引号直接塞进双引号 `onclick` 属性导致浏览器解析截断为 `cp(this, `；2026-09-25 加 `html.escape` 修复并经 S22 复核，55/58 画廊已重生成。尾注：EGOIST SP19/33/47 因代表帧与镜头表张数不一致被 `gallery()` 门禁拦停、按钮仍坏，用户裁决暂不处理。

---

## 归档规则

1. 活跃区只保留 `README.md` 单表中的条目。
2. 问题解决后：状态改为已解决 / 已作废，迁移到本文件，README 中删除。
3. 编号不回填、不补号；删除后留下的空号用本文件记录去向。
