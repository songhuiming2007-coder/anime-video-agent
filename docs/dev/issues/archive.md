# Issues：已归档条目

活跃问题见 `README.md` 单表。本文件只存已解决、已作废或历史上有价值的问题。
**规则：解决即归档，不再删除。** 编号断裂比归档文件更害人。

---

## 2026-09-28：收尾批复核收口（只读复核 / 决策准备 session）

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
- 关联：`docs/adr/0008-ground-truth-anchor-clips.md`
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
- 关联：`docs/plans/2026-08-14-p4-script-pipeline.md`
- 要点：`01-topic.md` 加 `缩段不注水: 是` 字段，字数下限 × shrink_factor；
  允许承认这段没料而缩短，不许注水凑数。

### [D14] TTS CPM 单源化
- 状态：**已解决**（2026-08-14，P3 解决删除）
- 关联：`docs/plans/2026-08-14-p3-doc-debt.md`
- 要点：CPM 取值口径统一，消除多文件默认值漂移。

### [D15] BGM 例外规则同步
- 状态：**已解决**（2026-08-14，BGM 例外规则同步）
- 关联：`docs/adr/0007-no-japanese-subs-support.md` 背景中提及
- 要点：例外曲目来源与理由在 config 与文档间同步完成。

### [D16] 人类介入点口径统一
- 状态：**已解决**（2026-08-14，P3 解决删除）
- 关联：`docs/plans/2026-08-14-p3-doc-debt.md`
- 要点：02.5 / 03.5 / 05 / 09 四处人类介入点在 CLAUDE.md / WORKFLOW.md / STANDARD.md 口径统一。

### [D17] 测试数清算
- 状态：**已解决**（2026-08-14，P3 解决删除）
- 关联：`docs/plans/2026-08-14-p3-doc-debt.md`
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
