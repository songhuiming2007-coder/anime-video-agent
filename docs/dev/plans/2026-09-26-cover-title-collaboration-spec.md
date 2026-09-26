# Implementation Spec：09 封面与标题协作（Spec 12）

日期：2026-09-26（**v0.3**，红队二轮修订（1🟡 + 4🔵 全收，逐条裁决见 §1.2）；状态：**待红队定向复审（第三轮，仅限 §1.2 五处文本手术）**；ADR-0025 已被用户接受、§6.1 全部修订请求已获授权（2026-09-26）——授权不等于可动工，动工仍须红队 🟢）  
上位文档：`docs/dev/plans/2026-09-22-harness-evolution-direction.md` §6 Spec 12（范围以该节为准；§0 产品画像、§4 施工红线八条、§5 明确排除同样生效）  
相关 ADR：**ADR-0025（工具表封顶上调至 14，`docs/dev/adr/0025-tool-table-cap-raise.md`，已通过——2026-09-26 用户接受，ADR-0021 口径与 direction §4 红线 6 已同步修订）**、ADR-0021（封顶口径的立法者，随 ADR-0025 接受同步修订）、ADR-0024（桌面端写产物：人令 → host spawn → core 裸形态子命令）、ADR-0018（封面标题只出候选）、ADR-0020（§3 approval 对象、§4 桌面端）  
契约依赖：Spec 3（已施工，`pipeline/approvals.py`）；Spec 8（已施工，`desktop/` 代码即现状，文档 `archive/2026-09-23-electron-desktop-spec.md`）；Spec 9 / Spec 10 / Spec 11（红队 🟢、未施工——标题讨论承载依赖 Spec 10 的对话面板，桌面导入 UI 沿用 Spec 11 的 spawn 先例）  
格式范本：Spec 2、Spec 3、Spec 8（`archive/`）  
撰写基线：HEAD `8bf711d`；`uv run pytest` 1715 passed、`cd desktop && npx vitest run` 121 passed（均 2026-09-26 实跑）

**用户裁决记录**（2026-09-26，撰写前逐条问人）

| 事项 | 裁决 |
|---|---|
| 09 的 ack 是否记录人最终选定的封面与标题 | **记录**：ack 携带封面文件（期目录相对路径）与标题原文，落 approval 对象与事件（direction 明示须问人的作者默认项，已裁决） |
| cover_edit 每次渲染是否弹人审卡 | **每次弹卡**（沿用 fail-closed；多轮迭代就多张卡，每张一次点击） |
| 封面叠字默认字体 | **引入一份开源字体放 `data/fonts/`**（用户原话：「引入一份开源免费相对够用也不那么多的字体库」）；config 钉文件路径，字体文件由人一次性放置 |
| 2026-09-26 ADR-0025（工具表封顶上调至 14） | **接受**（ADR 状态转「已通过」；ADR-0021 与 direction §4 红线 6 口径同步为 14） |
| 2026-09-26 §6.1 全部修订请求（S3-R12、S8-R18/R19、S10-R1、C12-R1/R2、D-R2） | **授权**（授权不等于可动工：动工仍须本 spec 红队 🟢；S10-R1 随 Spec 10 施工并入） |

**作者自报的未实测假设**（红队优先攻击面；正文相应处已标「约」）

| 编号 | 假设 | 状态 / 退路 |
|---|---|---|
| A1 | 正式字体文件的形态（文件名、字重、是否 ttc） | 机制已用 `/System/Library/Fonts/Hiragino Sans GB.ttc` 实测（2026-09-26 scratchpad）；正式字体待人放置后回填 config 默认值与渲染基线。**注意实测发现**：同一 ttc 不同 index 是不同家族（index=0 `Hiragino Sans GB`、index=1 `.Hiragino Sans GB Interface`），故 config 必须钉 `font_index` |
| A2 | 渲染基线 sha256 在正式字体 + Pillow 12.3.0 下可稳定复现 | 机制已实测：同参数跨两个独立进程渲染 sha256 全同（`3e606ec3…`，scratchpad）；正式基线值 PR1 首日先跑回填 |
| A3 | Electron 44 sandbox renderer 里拖拽 `DataTransferItem.getAsFile().arrayBuffer()` 可用 | Chromium 标准 API，未实测；退路：`<input type="file">` 同样给出 `File` 对象，拖拽只是便利层 |
| A4 | 32 MiB 图上界经 renderer→host（MessagePort）→core（stdin）的性能 | 未测；封面图实际 1–5 MiB 量级（约），上限是防御性的；PR3 实测回填 |
| A5 | `/import-cover` 经 spawn stdin 传 MB 级字节无截断 | Spec 11 的 stdin 先例（SAVE_SCRIPT）只传 KB 级文本；PR1 实测回填 |

---

## 0. 一句话设计

**人找图、人扩图、人定稿；agent 只做两件事——把参数变成像素（确定性、可复现），把讨论变成候选（落盘、可指认）。**

图片导入走 ADR-0024 既定写路径：人把图拖进 app，renderer 读字节，经 host spawn 的 core 裸形态子命令 `/import-cover`（stdin 传字节）落 `07-cover/import/`，Pillow 完整解码验证、原名保留、冲突不覆盖。确定性编辑是第 13 个 LLM 工具 `cover_edit`（ADR-0025，已通过）：agent 只出参数（文字、九宫锚点、字号、颜色、描边），`pipeline/cover_edit.py` 用 Pillow 渲染，字体文件钉死在 config、Pillow 由 uv.lock 钉版本，同参数同输出做字节级断言。标题讨论就是 Spec 10 面板里的普通对话，候选经 `write_episode_file`（白名单扩 `07-titles.md`）落盘，逐次弹人审卡。定稿权在人：09 的 ack 必须携带人最终选定的封面路径与标题原文（2026-09-26 用户裁决），记入 approval 对象与事件；LLM 工具表里永远没有「定稿」动作（ADR-0018 不变）。AI 生图/扩图与自动上传发布明确不做。

---

## 1. 红队裁决与修订纪要

### 1.2 第二轮红队定向复审裁决与修订纪要（v0.2 → v0.3，1🟡 + 4🔵 全收；原裁决「🟡 修订后定向复审（第三轮，范围极小）」）

红队报告为会话粘贴、未落盘，本表即唯一存档。五处残留逐条独立复核**全部属实**，且根因明确：v0.2 修订期一批 7 处的 edit 调用因单个 oldText 不匹配**整批回滚**，作者未逐项回验落点（§3.1 失败行、§3.3 注释、§4.1 签名三处因此未落）——与 Spec 11 红队二轮 🟡-1 记录的事故同类重犯。教训重申：每批 edit 后必须回读落点再报。

| 编号 | 红队指控 | 独立复核 | 裁决 | 修订动作 |
|---|---|---|---|---|
| 🟡（🟡-4 残留） | §3.1 失败行「字节超限、」未删，裁决声明与落地不符 | **属实**（原文比对；同批回滚的 §3.3/§4.1 两处亦未落） | **采纳** | 删「字节超限、」四字；本节如实记录回滚根因 |
| 🔵（🔴-1 残留） | §3.3 代码块注释 `cover_mtime_ns: 1790…` 无引号，与字符串决策相悖 | **属实** | **采纳** | 加引号 |
| 🔵（🔵-9 残留） | v0.2 附表混留「Spec 9 §3.1 请求帧四类」旧错行，与紧邻的更正确行矛盾 | **属实**（主表旧行与重写后的更正确行并存） | **采纳** | 删旧行（更正确行已在） |
| 🔵（🔵-10 残留） | §4.1 `_cmd_import_cover` 签名旁无限流注释 | **属实** | **采纳** | 签名改为 `data: bytes` 并补「分派处先 read(N+1) 限流」注释 |
| 🔵（🟡-3 残留） | §0 仍写「（前置 ADR-0025）」 | **属实** | **采纳** | 改「（ADR-0025，已通过）」 |

红队二轮另确认：🟡-5 的作者反转实测独立复现成立（五组构造结果与 v0.2 附表一致）；🟡-3 的「部分属实」抗辩成立，红队收回一轮「全文停留在提议」的过重表述。汇报措辞更正：此前汇报的「门禁 11 passed」指 `test_docs_invariants.py` 的 11 条文档不变量用例，**不是** §9 验收门禁——§9 门禁 1~10 全是施工后验收项，当前可勾的只有门禁 0 的前两条。

**第三轮复审范围**：仅限本节 5 处文本手术的 diff。其余全部闭环，不再重审。

### 1.1 第一轮红队裁决与修订纪要（v0.1 → v0.2，2🔴 全收、🟡-3/4/6/7 全收、🟡-5 部分收、🔵-8~13 全收；原裁决「🟡 修订后定向复审」）

红队报告为会话粘贴、未落盘，本表即唯一存档。每条指控在采纳前均已对照工作树独立复核（复核列），其中 🟡-5 的夹具构造经 scratchpad 实测后出现**方向反转**（详见该行）。无驳回。

| 编号 | 红队指控 | 独立复核 | 裁决 | 修订动作 |
|---|---|---|---|---|
| 🔴-1 | `cover_mtime_ns`（纳秒 ≈1.79e18 > 2^53）经 `losslessJson.ts` 的 reviver 只认 `mtime_ns` 键，会静默落成有损 double；Spec 8 RF-23 冻结预案「新增超 2^53 整数字段必须同步扩展规则 8 与 TE-13」未被触发 | **属实**：`losslessJson.ts` reviver 原文 `k !== "mtime_ns" ? v : …`；RF-23 原文（archive line 1033）逐字一致 | **采纳，选方案 ①**（`cover_mtime_ns` 一律十进制字符串，core 侧断言纯数字字面量）——不选 ②（扩规则 8 键集合 + TE-13 + MUT）的理由：RF-5 的消费只是「事后相等性审计比对」，字符串相等即够，无需 bigint 比较语义；不动 Spec 8 已冻结的规则 8/TE-13，修订面最小；Spec 8 m3 警告的「形状变化静默失配」只针对既有 `mtime_ns` 键，新键以字符串入场无此问题 | §3.3 finalize 形状改为 `cover_mtime_ns: string`（十进制，`^\d+$` 断言）；§2.3 同步；§4.1 签名口径写明；S8-R19 增「contracts.ts `ApprovalRecord`/`ApprovalJson` 增 finalize 类型（无 bigint，全字符串/安全整数）」；TC-8 增「finalize 经 losslessJson 通路读出与 core 写入逐字节相等」断言；新增 MUT-15（core 把 `cover_mtime_ns` 写成 int → TC-8 红）；门禁 4 同步 |
| 🔴-2 | §2.3 裸形态规定空格固定位置取参，§2.3 桌面端段与 §3.5 却规定等号形式 `--cover=<p>`；同一子命令两套语法，照写集成即断；等号形式的理由（valued_flags 闭集）对手解析的 `/approve` 不成立 | **属实**：`cli.py:1624` `len(args) != 5` 手解析确认；spec 三处原文并排确为矛盾；那句「无损纪律不适用于此」把 argv 方向与 store/事件方向混为一谈，是作者的错 | **采纳** | §2.3 桌面端段与 §3.5 统一为空格固定位置（`APPROVE` 模板 argv 追加 `--cover`、`<path>`、`--title`、`<标题>` 四个独立 argv 元素，`shell:false` 下天然逐字节无损）；删去那句不成立的括号注释；TD-2 断言改为空格形态；§4.2 同步 |
| 🟡-3 | ADR-0025 已翻篇为 accepted，spec 头/§6.1/PR0/附表停留在「提议」 | **部分属实**：头部、§6.1、PR0、门禁 0 在接受当天（红队评审到达前）已同步（当前工作树 line 3/5/231/313/322 可查）；**附表三行确实未回核**（ADR-0021 与 direction 红线 6 的「~12」原文已改、direction §6 的「ADR-0025 前置」措辞已改），属实；另自查发现 §2.2 与 §6.3 各残留一处「前置：ADR-0025 被接受」 | **采纳**（残留部分） | 附表三行按接受后原文重写并注明重核日期；§2.2、§6.3 残留「前置」措辞清理；§6.1 的 ADR-0025 行已于接受当日标「已闭环」 |
| 🟡-4 | §3.1 退出码自相矛盾（stdin 行「超限退 2」、失败行「字节超限…退 1」、TC-2 含糊「各退 1/2」） | **属实**（三处原文比对） | **采纳**：超限 = 资源/用法拒绝，退 2；校验失败（解码、格式、像素超限、期目录）退 1 | §3.1 失败行删「字节超限」；TC-2 逐条定死退出码 |
| 🟡-5 | MUT-1 的「截断像素数据」夹具会被 verify() 拦下，杀不掉「去掉 load()」；建议改写为「合法容器 + 短 IDAT」（verify OK / load FAIL） | **部分属实且方向反转**（scratchpad 实测 `mut1b/mut1c/mut1d.py`，Pillow 12.3.0）：文件级截断确被 open/verify 拦（`UnidentifiedImageError`）；但红队建议的「合法容器 + 短 IDAT」**只在非行对齐时成立**——zlib 合法但缺整行的流 verify OK、**load 也 OK**（缺行静默补黑像素，实测下半部 `(0,0,0)`）；能稳定区分 verify/load 的构造是「IDAT 为非 zlib 乱字节」（verify OK / load FAIL `broken data stream`）或「解压字节缺 1（非行对齐）」（load FAIL `image file is truncated`） | **采纳**（按实测修正夹具构造，不照抄红队建议） | MUT-1 夹具写死为「合法容器 + IDAT 乱字节」；§2.1 校验段补实测分工表；新增 RF-11（行对齐短缺静默补黑的已知空洞，人预览兜底）；附表补实测记录 |
| 🟡-6 | 模板名不是「ACK」是 `APPROVE`（Spec 8 §3.4、`spawner.ts:22`） | **属实**（`spawner.ts:22` `| "APPROVE"`、88-90 case 分支） | **采纳** | 全文 ACK→APPROVE（§2.3、§3.5、§4.2、S8-R19） |
| 🟡-7 | `tests/test_candidates_propose.py:441` 与 `tests/test_agent_web.py:864-865` 现有 `len(TOOL_SCHEMAS) == 12` / `<= 12`，PR1 必撞；门禁 6 只断言「恰 13」，第 14 个工具进来无静态护栏 | **属实**（两处原文比对；864-865 在同一个 `test_every_tool_has_existing_adr` 里，该用例还逐工具 glob ADR 文件——ADR-0025 已存在，`adr: "ADR-0025"` 天然过） | **采纳** | PR1 改动清单补两处断言更新（`== 12`→`== 13`、`<= 12`→`<= 14`）；门禁 6 增「`<= 14` 上限断言接替既有 `<= 12`」 |
| 🔵-8 | §4.1 签名 `dict[str, str]` 与 §3.3 `dict[str, Any]` 不一致 | 属实 | **采纳** | §4.1 写明：入参 `dict[str, str]`（人只给 cover/title 两个字符串键），落库字段 `dict[str, Any]`（core 自填 `cover_size: int` 与 `cover_mtime_ns: str`） |
| 🔵-9 | §2.1 引 RF-9 应为 RF-8；Spec 9 引用实为 §3.2（342 行在「3.2 人审请求」内）；`titles()` 实为 511–555；`MAX_IMAGE_PIXELS` 实测 89,478,485 | 全部属实（RF 编号比对；Spec 9 §3.2 起于 336；`cover.py` 558 已是 `main`；`uv run python -c` 实测 89478485） | **采纳** | 四处全改 |
| 🔵-10 | §3.1「读端带限流」与 §4.1 签名 `stdin_bytes: bytes`（整读后传入）矛盾 | 属实 | **采纳** | 限流冻结在分派层：分派处 `sys.stdin.buffer.read(IMPORT_MAX_BYTES + 1)`，超即退 2；`_cmd_import_cover` 收到的是已限流的 bytes，签名注释写明 |
| 🔵-11 | 变异矩阵漏列 32 MiB 上限与 edit-* 64 上限的观察者 | 属实 | **采纳** | 新增 MUT-13（去掉 32 MiB 闸 → TC-2 红）、MUT-14（去掉 64 上限 → TC-4 红） |
| 🔵-12 | 像素数检查应先于完整解码（头部即知尺寸，先判后 load 更稳） | 属实（合理实现顺序） | **采纳** | §2.1 冻结校验顺序：`open`（头部）→ 格式与像素闸 → `load()` 完整解码 |
| 🔵-13 | REPL 的 `--cover <path>` 对含空格文件名（cp 旁路进来的合法文件）不友好 | 属实 | **采纳** | §2.3 REPL 段写明：`--cover` 后取 shlex 单 token，含空格用引号；core 校验兜底（不存在的路径必然 ApprovalError） |

**下一轮定向复审应限定的范围**：① 🔴-1 的字符串形态（§2.3/§3.3/§4.1/S8-R19/TC-8/MUT-15/门禁 4）；② 🔴-2 的空格固定位置统一（§2.3/§3.5/§4.2/TD-2）；③ MUT-1 新夹具与 RF-11（§2.1/§7.3，含作者的反转实测记录）；④ §3.1 退出码与 🔵-10 限流分层；⑤ PR1 清单的两处既有断言更新与门禁 6 的 `<= 14`；⑥ 附表重写行。其余章节（设计决策骨架、数据契约其余部分、测试矩阵主体、红线对照）一轮已确认成立，建议不重审。

---

## 2. 关键设计决策（含代码现状证据）

### 2.1 决策 1：图片导入——拖入 app，经 core 裸形态子命令落 `07-cover/import/`（正面回答设计问题 1）

- **工作流前提**（direction §6 Spec 12 用户描述）：agent 出的封面常不理想，人自己上网找图；图大多不是标准 16:9 / 9:16，人先在 ava 之外用 Google Flow 扩成横竖两版再交给 agent；少数比例合适的原图直接交给 agent。**ava 收的是「已经合格的图」，比例是否合适由人判断，机器不设判据**（判据 1：机器没有「比例适合当封面」的可靠判据，only 尺寸断言属于呈现层事实，不做拦截）。
- **写路径**（沿用 Spec 11 / ADR-0024 决策 1）：renderer 读拖拽文件的字节（`File.arrayBuffer()`，Chromium 标准 API，A3）→ 经既有 MessagePort 通道传 host → host spawn `ava <期> /import-cover --name=<原始文件名>`，字节走 **stdin**（同 ADR-0024「正文经 stdin 传递，不进 argv」的纪律；这样 preload 保持「零 `exposeInMainWorld`」现状不改动，spawn 闭集只增一个模板，见 §6.1 S8-R18）。host 对 `data/` 零写入（I1）不变。
- **落盘位置与命名**（冻结）：`data/episodes/<期>/07-cover/import/`。文件名 = 原始文件名的 stem 经净化（保留字母/数字/CJK/`-`/`_`，其余折叠为单个 `-`，截 40 字符，全空则 `img`）+ 规范化后缀（小写，`.jpeg`→`.jpg`）；**冲突不覆盖**，追加 `-2`、`-3`…。理由：人按自己起的文件名认图（「这张是我在 Flow 里扩的竖版」）；不覆盖 = 导入历史不可变，09 定稿记录的路径永远指向同一份字节。
- **core 侧校验（诚实失败，不信后缀；顺序冻结，🔵-12）**：① 分派层 `sys.stdin.buffer.read(IMPORT_MAX_BYTES + 1)` 限流，超 32 MiB 退 2（🔵-10）；② Pillow `Image.open` 读头部（`format ∈ {PNG, JPEG}`，不符退 1）；③ 像素数闸（宽×高 ≤ 40_000_000，超限退 1——防解压炸弹，Pillow 的 `MAX_IMAGE_PIXELS` 默认 89,478,485（实测）太宽，此处显式收紧为 4 千万——8K 图约 3300 万像素，余量够且能拦恶意构造）；④ `verify()` + `img.load()` 完整解码。**verify 与 load 的分工实测**（2026-09-26 scratchpad `mut1b/c/d.py`，Pillow 12.3.0）：文件级截断与 CRC 错由 open/verify 拦；非 zlib 乱字节 IDAT 与非行对齐短缺由 load 拦；**行对齐短缺两者都放，缺行静默补黑**（实测下半部像素 `(0,0,0)`）——此空洞记入 RF-11，由人预览兜底。全部通过才把**原始字节**（不重编码，保真）写临时文件再 `os.replace`（`paths.atomic_write` 扩 bytes 形态，§4.1）。期目录不存在 → 退 1，绝不 mkdir 期目录；`07-cover/import/` 子目录在期目录已确认存在的前提下按需创建（`status_card.py` 建 `_agent/` 的既有先例）。
- **stdout**：单行 JSON `{"path": "07-cover/import/<名>", "width": W, "height": H, "format": "PNG"}`——宽/高是**确定性事实**，供人与 agent 知道这是横版还是竖版（呈现，不是判据）。
- **终端平价路径**：`cat ~/Downloads/x.png | ava <期> /import-cover --name=x.png`；人直接 `cp` 进 `07-cover/import/` 也合法（产物即状态，文件出现即有效），代价是跳过解码校验——坏文件会在 `cover_edit` 渲染或 09 ack 校验时被拦（RF-8）。

### 2.2 决策 2：`cover_edit`——第 13 个 LLM 工具，agent 出参数、Pillow 渲染、字节级可复现（正面回答设计问题 2）

- **为什么是 LLM 工具**（ADR-0025 决策 2 的论证全文见该 ADR）：发起者是 agent（人提「把标题压在左上、白字黑边」，agent 出参数）；裸形态子命令是人令通道 agent 够不到；`run_pipeline` 白名单模块会把工具暴露给 pipeline scope 且参数校验力弱。ADR-0025 已被接受（2026-09-26，封顶 12 → 14）。
- **scope 归属**：仅 creative（`config/agent/tools.json` 的 creative 清单 10 → 11）；asset/pipeline/idea 不见（mask-don't-remove，ADR-0021 §2）。
- **每次渲染弹人审卡**（2026-09-26 用户裁决）：schema 不标 `side_effect`，沿用 `_default_approve` 的 fail-closed 默认 True（`cli.py:800-802`，write_episode_file/acquire_propose 同款）。卡片展示全部参数（源图、输出名、每行文字/锚点/字号/描边）——人审的是「agent 打算把什么字叠到哪张图上」。
- **渲染纪律（可复现的四根桩）**：
  1. **字体文件钉死**：`config/project.json` 新增 `cover.font_file`（仓库相对路径，默认 `data/fonts/SourceHanSansSC-Bold.otf`，待人放置后回填，A1）与 `cover.font_index`（ttc 取第几个字模，默认 0——实测同 ttc 不同 index 是不同家族，不钉会静默换字）；字体文件缺席 → 工具如实报错（含 config 键与期望路径），**不 fallback 系统字体**（fallback 会让同参数在不同机器产出不同字节，且「渲染成功了」掩盖字体缺席）。
  2. **Pillow 版本钉死**：`pyproject.toml:31` 既有 `pillow>=11.0` 不变，实际版本由 uv.lock 钉在 12.3.0（实测 `PIL.__version__`）；渲染基线测试（§7 TC-3）在 Pillow 或字体文件变更时**必须**重跑回填，这是特性不是脆弱——字节基线就是用来在依赖变动时报警的。
  3. **渲染管线确定性**：`truetype(font_file, size, index)` + `ImageDraw.text((x, y), text, font=, fill=, stroke_width=, stroke_fill=, anchor=)`，无随机、无时间、无环境读入；输出 PNG（无损、编码确定性）。已实测：同参数跨两个独立进程渲染，输出字节 sha256 全同（2026-09-26 scratchpad，Hiragino ttc 96 号字 + stroke_width=6，`3e606ec392c33b80…`，35919 字节）。
  4. **坐标系确定性**：九宫锚点（`tl/tc/tr/cl/cc/cr/bl/bc/br`）映射到 Pillow `anchor` 参数（`la/ma/ra/lm/mm/rm/ld/md/rd`），角/边锚点带固定边距 `MARGIN_PX=48`（1280×720 画布上 48px 是常见安全边距的量级；初值，PR1 按真实封面效果可经修订调整），`dx/dy` 为锚点基础上的像素偏移（dy 向下为正）。不给「自由 x/y」——锚点 + 偏移已覆盖全部排版意图，且天然随画布尺寸缩放语义稳定。
- **产出命名与不可覆盖**：`output` 必须匹配 `^07-cover/edit-[a-z0-9][a-z0-9-]{0,38}\.png$` 且**目标不得已存在**（同 import 的不覆盖纪律：迭代产生 edit-1、edit-2…，历史不可变，定稿路径永远指向同一份字节）。单期 `edit-*` 上限 64 个（防工具失控增生；真实迭代一期 <10 版（约），64 是两个数量级余量，RF-4）。
- **源图域**：`source` 必须是期目录内 `07-cover/` 下的 `.png/.jpg`（含 `import/` 子目录与 08 机筛的 `cover-N.jpg`），双端 resolve 防穿透（同 `write_episode_file` 纪律）。**只读源、不覆盖源**。
- **明确排除**：不缩放、不裁剪、不滤镜、不多图合成（扩图已由人在 Google Flow 完成；YAGNI，需要时另行修订）。不做 AI 生图/扩图（§2.6）。

### 2.3 决策 3：09 ack 携带定稿记录（2026-09-26 用户裁决；正面回答设计问题 3）

- **形态**：`approvals.approve()` 增可选参数 `finalize: dict | None`；**stop=="09" 时必填**，其余停机点给了一律 `ApprovalError`（参数语义严格分停机点，防误挂）。`finalize = {"cover": "<07-cover/ 内相对路径>", "title": "<标题原文>", "cover_size": int, "cover_mtime_ns": str}`——后两个字段由 core 在 ack 时刻自 stat 填入（人只给路径与标题），用于事后审计「定稿那一刻这份文件长什么样」（cover.py 重跑会重写 `cover-N.jpg`，指纹让「定稿后文件被覆盖」可检测，RF-5）。**`cover_mtime_ns` 是十进制字符串、core 落盘前断言 `^\d+$`**（🔴-1 方案 ①：纳秒值超 2^53，桌面端 `losslessJson.ts` 的 reviver 只认 `mtime_ns` 键，存 int 会静默落成有损 double；RF-5 的消费只是相等性审计比对，字符串相等即够，不动 Spec 8 已冻结的规则 8/TE-13）。
- **校验（ack 时刻，确定性）**：`cover` 相对路径必须解析在期目录 `07-cover/` 之下（resolve 防穿透）、文件存在、后缀 `.png/.jpg/.jpeg`；`title` 去空白后非空、不含换行、≤ 100 字符（B 站标题上限 80 字符，100 留余量；超限不是机器判内容好坏，是防把简介粘进来）。校验失败 → `ApprovalError`，对象不转移。
- **落点**：Approval dataclass 增 `finalize` 字段（随 `approvals_store.json` 持久）；`approval_resolved` 事件载荷增 `"finalize"` 键；`approvals.jsonl` 形状**不变**（它是审批疲劳判据的 y/n 流水账，定稿内容不属于它）。
- **CLI 表面**（沿用 S3-R1/R2/R6 的 argv 位置取参与退出码契约，修订请求 S3-R12）：
  - 裸形态：`ava <期> /approve 09 --id <id> --cover <path> --title <标题>`——固定位置（`args[5]=="--cover"`、`args[6]`=路径、`args[7]=="--title"`、`args[8]`=标题整元素，标题含空格无损）；其余停机点 `len(args)==5` 不变。02.5/03.5/05 带 `--cover` → 退 2。
  - REPL：`/approve 09 --id <id> --cover <path> --title <标题原文剩余>`——`--cover` 后取 shlex 单 token（含空格的路径用引号包住；core 的路径校验兜底，不存在/越界必然 `ApprovalError`，🔵-13）；`--title` 之后取整行原文（同 C.3 的 problem 口径）。
- **桌面端**：09 决策卡增两个输入——封面文件选择器（选项 = 产物树 `07-cover/` 下的现存文件清单，host 直读目录，确定性事实）+ 标题文本框；两者非空才可点批准；`APPROVE` 模板（🟡-6：不是「ACK」，`spawner.ts:22`）argv 对 09 追加 `--cover`、`<path>`、`--title`、`<标题>` **四个独立 argv 元素（空格固定位置，与 §2.3 裸形态同一套语法；`shell:false` 下 argv 元素天然逐字节无损——等号形式是无理由的第二套语法，已删，🔴-2）**。修订请求 S8-R19（对当前 `DecisionBar.tsx`；Spec 10 施工后迁移义务转入其 `HumanCards`，S10-R1）。
- **与解封物的关系**：09 维持无解封物（`_STOP_GATE["09"] = None`，`approvals.py:61`），定稿记录**不是**物理闸门——它只是把人的最终选择变成可审计数据。发布动作仍在 ava 之外（§2.6）。

### 2.4 决策 4：标题讨论的承载——Spec 10 面板里的普通对话 + 候选落盘走 `write_episode_file`（正面回答设计问题 4）

- **讨论不需要新协议**：标题的多轮讨论就是 Spec 10 对话面板里的人机对话（user_message ↔ assistant 帧），Spec 9 §3.1 的协议帧零改动。对话中间态允许只在会话里（session.jsonl 已是 append-only 落盘）；**候选要指认时必须落盘**——落盘点是既有产物 `07-titles.md`（`cover.py:511-555 titles()` 已生成带空候选表的模板，且注释明写「标题由 agent 写，写完覆盖本节」）。
- **写入通道**（修订请求 C12-R1）：`CREATIVE_WRITABLE_FILES`（`tools.py:26-29`）扩入 `07-titles.md`。这是**既有工具的语义扩展，工具表总数不变**（红线 6 只约束数量与 ADR 编号，不约束白名单内容；ADR-0024 决策 1 冻的是 `02-script.md` 不入白名单，与 `07-titles.md` 无关——后者从来就是「agent 写候选、人定稿」的文件）。写仍弹人审卡（fail-closed 默认），卡片显示全文 diff 前的完整新内容（write_episode_file 既有语义）。
- **现状证据与缺口**：当前 ava agent **没有任何**写 `07-titles.md` 的通道——真实期 `2026-07-30-春物-雪乃适合大老师/07-titles.md` 的候选表五行全空（撰写时实读）；现实中候选或由 pi 工单追加 6–10 行（`scout.py:385-393`），或由人手写。C12-R1 补上的正是这条断掉的通道。
- **指纹漂移的正确性**：09 的关联产物含 `07-titles.md`（`approvals.py:55` `_STOP_ARTIFACTS`）。agent 重写候选 → 指纹漂移 → 既有自愈把旧 09 pending 转 SUPERSEDED、新建 pending（Spec 3 §2.2/§2.3）——「候选变了要人重看」由既有机制天然保证，本 spec 零新增逻辑。
- **scout 工单通道不变**：pi 追加 6–10 槽的既有纪律原样保留，两组并存。

### 2.5 决策 5：ADR-0018「封面标题只出候选、agent 不定稿」如何保持（正面回答设计问题 5）

四条结构性保证，全部可静态断言（门禁 5）：

1. **工具表无定稿动作**：`cover_edit` 只产 `07-cover/edit-*.png` 候选（输出名校验强制该前缀）；`write_episode_file` 写 `07-titles.md` 弹人审卡且语义是候选（定稿不进该文件）；LLM 工具表中不存在任何「标记最终封面/标题」的工具。
2. **定稿数据只来自人的 ack**：`finalize` 的唯一写入点是 `approvals.approve()` 的显式 ack 路径（人敲命令/点按钮），agent 会话进程拿不到 `approvals` 的写通道（与现状一致——agent 只经状态卡看到 pending）。
3. **候选不排名**：`cover_edit` 输出按 agent 命名自然排列，无评分、无推荐字段；09 卡的封面选择器按文件名排序平铺（判据 2/10：机器不给封面排序）。
4. **事件与对象里的 finalize 由 core 校验存在性**：agent 无法伪造「人已选定」——伪造需要构造一次带合法 `--id` 与现有文件路径的人令 ack，而 ack 入口的显式性由 Spec 3 既有纪律保证（含 `--id` 绑定对象、指纹漂移拒批）。

### 2.6 决策 6：范围闸门（明确不做，正面回答设计问题 6）

- **AI 生图 / AI 扩图不进 ava**（direction §6 明令）：扩图由人在 Google Flow 完成；ava 不新增任何生图工具、不接任何生图 API。
- **自动上传发布不做**（触发「公开发布」红线；runbook 09「坚决不做自动上传」原样保留）：09 ack 记录定稿后，发布动作仍在 ava 之外由人完成，ava 不提供「复制标题到剪贴板」之外的任何便利（剪贴板便利本 spec 也不做，YAGNI）。
- 不改 08 机筛（`cover.py` 的候选帧流水线原样；本 spec 的导入与编辑是平行入口，不替代它）。
- 不做封面 A/B 数据回收、不做「封面效果预测」等 LLM 推荐（direction §5 排除项）。
- 不动 `runbook 08` 的三层分工与「机器不假装能判张力」的立场；本 spec 的所有机器校验（尺寸、解码、路径）都是确定性事实断言，不是审美判据。

---

## 3. 数据契约

### 3.1 `/import-cover` 裸形态子命令（`cli.py:1612` 起的分派块内新增；退出码 0 成 / 1 败 / 2 用法错）

| 项 | 契约 |
|---|---|
| argv | `ava <期> /import-cover --name=<原始文件名>`（等号形式；`--name` 缺省时用 `img` 为 stem） |
| stdin | 图片字节，上限 `IMPORT_MAX_BYTES = 32 MiB`（超限退 2；读端带限流，不整读后再判） |
| 成功 | §2.1 校验通过 → 原子落 `07-cover/import/<净化名>.<后缀>`（冲突追加 `-2`…）→ stdout 单行 JSON `{"path","width","height","format"}`，退 0 |
| 失败 | 期目录不存在/越界 → 退 1；解码失败、非 PNG/JPEG、像素超限 → stderr 一行原因，退 1，**零落盘** |

- REPL 同名命令可用（stdin 在 REPL 内不可用，REPL 形态改为 `/import-cover <源路径>` 由 core 读文件——仅此形态差异，校验与落盘同一函数）。
- 写纪律：期目录双端 resolve；目标文件名闭集由净化规则生成（人给的 `--name` 只提供 stem 建议，不直接成为路径）；落盘 tmp + `os.replace`。

### 3.2 `cover_edit` 工具 schema（v1，冻结；`additionalProperties: false` 全层）

```json
{
  "source": "期目录相对路径，^07-cover/(import/)?[^/]+\\.(png|jpg)$，必须存在（只读）",
  "output": "^07-cover/edit-[a-z0-9][a-z0-9-]{0,38}\\.png$，目标必须不存在（不覆盖）",
  "lines": [
    {
      "text": "1–40 字符",
      "size": "int，8–400",
      "anchor": "tl|tc|tr|cl|cc|cr|bl|bc|br",
      "dx": "int，-4000–4000，默认 0",
      "dy": "int，-4000–4000，默认 0",
      "color": "^#[0-9a-fA-F]{6}$，默认 #FFFFFF",
      "stroke_width": "int，0–40，默认 0",
      "stroke_color": "^#[0-9a-fA-F]{6}$，默认 #000000"
    }
  ]
}
```

`lines` 1–6 条，按数组顺序依次绘制（后画的盖先画的，叠放次序即数组序——确定性）。`required: ["source", "output", "lines"]`；每条 line `required: ["text", "size", "anchor"]`。description 写明「只产候选、定稿权在人」（门禁 5 的文本锚点）。

### 3.3 Approval `finalize` 扩展（S3-R12；Spec 3 §3.1 schema 的增量）

```python
finalize: dict[str, Any] | None = None
# {"cover": "07-cover/import/xxx.png", "title": "…", "cover_size": 123456, "cover_mtime_ns": "1790…"}
```

不变量增一条：`finalize` 非空 ⟹ `type == "09"` 且 `status == APPROVED`（写入点只在 approve 转移内）。`approval_resolved` 事件载荷增可选键 `"finalize"`（值同对象字段）；既有消费者按未知键忽略（Spec 2 载荷扩展先例）。`approvals_store.json` 历史对象无此字段，读入默认 None——零迁移。

### 3.4 `config/project.json` 增量

```json
"cover": {
  "_note": "封面叠字字体。Pillow 要字体文件路径（与 subtitle.font 的 fontconfig 字体名是两个机制，config/project.json:21-22）。文件由人放置，不进 git。ttc 须钉 index：实测同 ttc 不同 index 是不同字体家族。",
  "font_file": "data/fonts/SourceHanSansSC-Bold.otf",
  "font_index": 0
}
```

默认值在字体实际放置后回填（A1）；缺席时 `cover_edit` 报错含此键名与期望路径。

### 3.5 spawn 模板新增（S8-R18/R19；并入 Spec 8 §3.4 闭集）

| 模板 | argv | stdin | 超时 | 用途 |
|---|---|---|---|---|
| `IMPORT_COVER` | `[py, "-m", "pipeline.agent.cli", ep, "/import-cover", "--name=<原始文件名>"]` | **pipe**（图片字节，§3.1 上限） | 30 s | §2.1 |
| `APPROVE`（09 变体） | 09 时 argv 追加 `--cover`、`<path>`、`--title`、`<标题>` 四个独立元素（**空格固定位置，与 core 裸形态同一套语法，🔴-2**） | ignore | 同既有 | §2.3 |

stdin pipe 例外属第三类（Spec 10 的 SESSION_*、Spec 11 的 SAVE_SCRIPT/VOICE_* 之后）。环境白名单无新增（Pillow 在 venv 内）。

---

## 4. 模块接口与签名

### 4.1 core（新代码零顶层重依赖）

```python
# pipeline/cover_edit.py —— 新模块。顶层仅 stdlib + pipeline.paths；
# PIL 函数级延迟 import（红线 7；tools.py 热路径不背 Pillow）。
def load_cover_font(size: int) -> "ImageFont.FreeTypeFont":
    """读 config cover.font_file/font_index；文件缺席 → CoverEditError（含 config 键与期望路径）。"""

def render_cover(ep_dir: Path, params: dict) -> dict:
    """按 §3.2 schema（调用前已校验）渲染并原子落盘。
    返回 {"path", "width", "height", "sha256"}。源只读；目标不覆盖。"""

class CoverEditError(RuntimeError): ...  # 受控领域异常，工具层转结构化错误回喂

# pipeline/agent/cli.py —— 裸形态分派块内新增（分派处先
# sys.stdin.buffer.read(IMPORT_MAX_BYTES + 1) 限流，超限退 2；🔵-10）
def _cmd_import_cover(ep_dir: Path, name: str | None, data: bytes) -> int

# pipeline/paths.py —— atomic_write 扩 bytes（修订请求 C12-R2，一处签名放宽）
def atomic_write(dest: Path, data: str | bytes) -> None

# pipeline/approvals.py —— approve 增参（S3-R12）
def approve(ep_dir, stop, *, approval_id=None, source="repl",
            finalize: dict[str, str] | None = None) -> Approval

# pipeline/agent/tools.py —— TOOL_SCHEMAS 增 "cover_edit"（adr: "ADR-0025"，
# 不标 side_effect → fail-closed 弹卡）；_tool_cover_edit 委托 cover_edit.render_cover
```

### 4.2 desktop

- `host/spawner.ts`：`IMPORT_COVER` 模板入闭集；`APPROVE` 模板类型增 09 变体参数（cover/title 两个字符串）；`contracts.ts` 的 `ApprovalRecord`/`ApprovalJson` 增 `finalize` 类型（全字符串/安全整数，无 bigint——🔴-1 选方案 ① 后无需动规则 8 键集合）（S8-R18/R19）。
- `renderer`：产物树 / 期视图的「导入封面图」按钮与拖拽目标（`dragover`/`drop`，`File.arrayBuffer()`）；09 决策卡的封面选择器 + 标题输入（当前落在 `DecisionBar.tsx`；Spec 10 施工后随 `HumanCards` 迁移）。
- 显式点击纪律沿用：发送 `IMPORT_COVER` 与 09 `APPROVE` 的代码只允许出现在对应按钮的点击处理器里（TG-4′ 五条规则覆盖，S8-R7 已泛化）。

---

## 5. 依赖白名单与纯洁性保障

- **Python 侧**：`pyproject.toml` 零改动（Pillow 12.3.0 已是主依赖，`pyproject.toml:31`）。`pipeline/cover_edit.py` 顶层仅 `hashlib/json/re/pathlib/typing` + `pipeline.paths`；`PIL` 函数级延迟 import。纯洁性断言（红线 7）：独立子进程 `import pipeline.cover_edit`、`import pipeline.agent.tools`、`import pipeline.agent.cli` 后 `sys.modules` 无 `PIL`/`numpy`/`torch`/`mlx`。
- **TS 侧**：零新 npm 依赖（拖拽/读字节/选择器全是 Web 标准 API 与既有组件）。
- **字体文件**：`data/fonts/` 下的二进制资产**不进 git**（`data/` 整树纪律），由人一次性放置；不存在时诚实报错（RF-1）。
- **子进程纯洁性断言**：`/import-cover` 在 `env -i PATH=<白名单>` 下行为不变（不依赖 git/ffprobe——与 SEAL_SCRIPT 不同，本 spec 不需要 PATH 开口）。

---

## 6. 跨 spec 接口与系统边界

### 6.1 修订请求（编号明确；**2026-09-26 全部获用户授权**——授权不等于可动工，动工仍须本 spec 红队 🟢）

| 编号 | 对象 | 请求 | 级别 |
|---|---|---|---|
| **ADR-0025** | 新 ADR | 工具表封顶 12 → 14（**已于 2026-09-26 被接受**，封顶口径同步完成） | 已闭环 |
| **S3-R12** | Spec 3 / `pipeline/approvals.py` + `cli.py` | `approve` 增 `finalize`（仅 09 必填，其余停机点给了报错）；Approval schema 与 `approval_resolved` 载荷扩展；裸形态/REPL 的 09 ack 语法（§2.3）；Spec 3 §3.1/§4.1/§4.2 文档同步 | 阻塞 |
| **S8-R18** | Spec 8 §2.3 I2 / §3.4 | spawn 闭集 +1（`IMPORT_COVER`，stdin pipe）；I2「显式点击」类增 `07-cover/import/**`；TI-3b 清单同步 | 阻塞（桌面导入） |
| **S8-R19** | Spec 8 §3.4 / `DecisionBar.tsx` | `APPROVE` 模板 09 变体（`--cover <path> --title <标题>` 空格固定位置，🔴-2）；09 决策卡增封面选择器 + 标题输入，两者非空才可批准；`contracts.ts` 的 `ApprovalRecord`/`ApprovalJson` 增 `finalize` 类型（无 bigint，🔴-1 ①）；Spec 10 施工后迁移义务转入 `HumanCards` | 阻塞（桌面 09） |
| **S10-R1** | Spec 10 | ① 标题讨论 = 面板普通对话，协议零改动（如实声明）；② `HumanCards` 的 09 卡承接 S8-R19 两输入 | 阻塞（文字；Spec 10 未施工，并入其施工单） |
| **C12-R1** | `pipeline/agent/tools.py:26-29` | `CREATIVE_WRITABLE_FILES` 扩入 `07-titles.md`（工具表总数不变；写仍弹卡）；相关测试期望值按「期望值先跑」纪律更新 | 阻塞（core 改动） |
| **C12-R2** | `pipeline/paths.py:118-128` | `atomic_write` 的 `data` 形参放宽为 `str \| bytes`（既有调用点行为逐字节不变） | 阻塞（core 改动） |
| **D-R2** | `docs/runbook/08-cover-title.md`、`09-publish.md`、`docs/WORKFLOW.md` | 08 增「人自备图导入与 cover_edit」一节、09 增「ack 携带定稿」一句；WORKFLOW 08/09 行同步一句（只许瘦身不许膨胀，合计净增 ≤10 行） | 阻塞（文档） |
| **S9 / S11** | Spec 9 / Spec 11 | **无修订**——标题讨论复用 Spec 9 既有帧；导入/编辑不经 Spec 11 的子命令集（那是 02.5/03.5 专属） | — |

### 6.2 与各有主的边界

- **Spec 3**：除 S3-R12 外零改动；09 维持无解封物，finalize 不是物理闸门（§2.3）。
- **Spec 8**：I1 不变（导入字节经 stdin，host 零写 `data/`）；preload「零 exposeInMainWorld」不变（拖拽字节走 renderer 标准 API + 既有 MessagePort，不需要 `webUtils.getPathForFile`）。
- **Spec 9/10**：协议零新增；工具卡（cover_edit、write_episode_file 的审批）走既有 `request{kind:"tool_call"}` 帧与卡片组件。
- **Spec 11**：无交集（本 spec 的 core 子命令是 `/import-cover` 一个，不进 Spec 11 的八命令表）。
- **pipeline.status**：零感知（finalize、import、edit 都不参与工序推导；09 的推进条件不变）。

### 6.3 八条施工红线对照

1. **不引入数据库**：导入/候选/定稿全部文件落盘（`07-cover/import/**`、`07-cover/edit-*.png`、approval 对象既有存储）。✓
2. **不引入第三方框架/web server**：core 零 server；桌面侧零新 npm 包。✓
3. **产物即状态**：`status.py` 零改动；09 推进条件不变；finalize 是审批簿记不是状态源。✓
4. **停机点不减少、ack 永远显式**：09 的 ack 反而加重了人的显式动作（必须给出封面与标题）；封面标题只出候选（§2.5 四条结构性保证）。✓
5. **人审闸门不自动化**：cover_edit 每次渲染弹卡、07-titles.md 每次写入弹卡（2026-09-26 用户裁决）。✓
6. **工具表封顶**：第 13 个工具经 ADR-0025（已通过，2026-09-26）上调封顶至 14 后入表；ADR-0021 与 direction 红线 6 口径已同步为 14。✓
7. **依赖纯洁**：新模块顶层零重依赖（PIL 函数级），子进程纯洁性断言入 §7。✓
8. **期望值先跑 + 变异检验**：渲染确定性已跨进程实测（scratchpad，§2.2）；渲染基线 sha256 PR1 首日先跑回填再写断言；§7.3 变异矩阵逐条实跑。✓

---

## 7. 测试规格与变异检验

### 7.1 core 测试（`tests/test_cover_edit.py`、`tests/test_approvals.py` 增量）

> 夹具纪律：源图用最小合法 PNG/JPEG（测试内用 Pillow 现场生成 64×36 与 36×64 两张）；字体在测试环境读 config 指向的文件，缺席则 `pytest.importorskip` 不适用——改为跳过单个用例并打印原因（判据 9：跳过必须显式列出）；渲染基线用例单独标记，Pillow/字体变更时重跑回填。

| 编号 | 用例 | 关键断言 |
|---|---|---|
| TC-1 | `/import-cover` 正常路径 | 合法 PNG 字节 → 落 `07-cover/import/<名>.png`、字节与输入全同（不重编码）、stdout JSON 的 path/width/height 正确；同名二次导入得 `-2` 后缀，两份都在 |
| TC-2 | `/import-cover` 拒绝面（退出码逐条定死，🟡-4） | 非图字节（`b"not an image"`）、改名换姓的伪 PNG、解码失败（乱字节 IDAT）、像素超限构造 → **各退 1**；stdin 超 32 MiB → **退 2**；期目录不存在 → 退 1 且**未 mkdir**；以上全部零落盘 |
| TC-3 | `cover_edit` 渲染基线（期望值先跑） | 固定夹具图 + 固定参数（两行文字：主标 96 号 tl、副标 40 号 bl，含描边）渲染两次（**两个独立子进程**）→ 字节 sha256 全同且等于基线值（基线 PR1 首日以正式字体跑出回填；A2）；同参数渲染与「无文字」渲染在文字区域有像素差异（证明字真的画上去了） |
| TC-4 | `cover_edit` schema 校验 | source 越界（`../`、非 07-cover/、不存在）、output 不合命名、output 已存在、lines 0 条 / 7 条、size 越界、颜色非法 → 各被拒且零落盘；`edit-*` 达 64 上限 → 拒 |
| TC-5 | 锚点语义 | 同一行文字分别打 tl/cc/br：文字重心区域像素分别落在左上/中心/右下（区域采样断言，不是全图 hash——防假绿） |
| TC-6 | 字体纪律 | config 指向不存在文件 → `CoverEditError` 含 `cover.font_file` 与期望路径；加载的字体家族名断言（防 ttc index 漂移，RF-6） |
| TC-7 | 纯洁性 | 子进程 import 探针（§5） |
| TC-8 | 09 finalize 正常路径（含无损通路，🔴-1） | pending 09 上 `approve(..., finalize={"cover": <现存路径>, "title": "…"})` → 对象 `finalize` 含四键（core 自填 `cover_size: int`、`cover_mtime_ns: str` 且匹配 `^\d+$`）、`approval_resolved` 载荷含 `finalize`、退出 0；**桌面通路断言**：store 行与事件行经 `losslessJson.parseLossless` 读出的 `cover_mtime_ns` 与 core 写入值逐字节相等（字符串形态天然无损，此断言防的是「哪天有人把它改回 int」） |
| TC-9 | 09 finalize 拒绝面 | 缺 finalize / cover 不存在 / cover 越出 07-cover/ / title 空 / title 含换行 / title 101 字符 → 各 `ApprovalError`，对象不转移；02.5/03.5/05 带 finalize → `ApprovalError` |
| TC-10 | 09 CLI 表面 | 裸形态 argv 位置取参（标题含空格与引号逐字节无损，S3-R1 同款断言）；其余停机点带 `--cover` 退 2；REPL 形态 `--title` 后取整行原文 |
| TC-11 | C12-R1 写标题候选 | `write_episode_file` 写 `07-titles.md` 成功且弹卡语义不变；写后 09 pending 指纹漂移 → 旧对象 SUPERSEDED + 新 pending（既有自愈断言复用） |

### 7.2 desktop 测试

| 编号 | 用例 | 关键断言 |
|---|---|---|
| TD-1 | 导入链路 | 模拟 drop（File 构造）→ host 收到字节 → spawn argv 为 `IMPORT_COVER` 形状、stdin 字节全等；成功后产物树出现新文件 |
| TD-2 | 09 卡两输入 | 封面未选或标题为空 → 批准禁用；两者齐备 → spawn argv 为 `--cover`、`<path>`、`--title`、`<标题>` 四个独立元素（空格固定位置，🔴-2）、标题含空格逐字节无损 |
| TE-1 | e2e（临时 repo 副本，Spec 11 🔵-6 纪律） | 导入一张图 → 树预览可见 →（桩 agent 或直接 core）cover_edit 渲染一版 → 09 卡选该图填标题批准 → approval 对象 finalize 与事件对拍 |

### 7.3 变异检验矩阵（逐条实跑：植入 → 变红 → 还原）

| 编号 | 变异 | 应变红 | 为什么是它（不被掩盖论证） |
|---|---|---|---|
| MUT-1 | `/import-cover` 只做 `Image.open().verify()` 不 `load()` | TC-2（**合法容器 + IDAT 为非 zlib 乱字节**的构造，🟡-5 实测修正：verify OK / load FAIL `broken data stream`） | 实测（Pillow 12.3.0）：文件级截断被 open/verify 拦、行对齐短缺 load 也放（补黑，RF-11），只有「乱字节 IDAT / 非行对齐短缺」能稳定区分 verify 与 load——夹具必须取这一类，其他用例输入不经过该分支，不掩盖 |
| MUT-2 | 导入冲突时覆盖同名文件 | TC-1 二次导入断言 | 只此用例观察 `-2` 与两份并存 |
| MUT-3 | `cover_edit` 渲染漏掉 `stroke_width/stroke_fill` 参数 | TC-3 基线 hash + 像素差异 | 基线变、描边区域像素变；TC-5 不打描边、不掩盖 |
| MUT-4 | 锚点映射表 tl 与 br 对调 | TC-5 | 区域采样专杀；基线用例固定 tl/bl 会连带变红（双杀，不妨碍定位） |
| MUT-5 | output 已存在时仍覆写 | TC-4 | 「目标必须不存在」分支唯一观察者 |
| MUT-6 | 字体缺席时 fallback 系统字体 | TC-6 | 专测缺席分支；正常路径有字体不变红 |
| MUT-7 | `approve` 对 09 不校验 finalize 必填 | TC-9 缺 finalize 子用例 | 该分支唯一观察者 |
| MUT-8 | finalize 校验去掉 resolve 防穿透 | TC-9 cover 越界子用例 | 如 `07-cover/../../x.png` 类路径只由该断言覆盖 |
| MUT-9 | 事件载荷漏 `finalize` 键 | TC-8 | 对象落盘照常，唯一变红点是事件断言 |
| MUT-10 | 09 之外停机点收到 finalize 不报错 | TC-9 末子用例 | 唯一观察者 |
| MUT-11 | `CREATIVE_WRITABLE_FILES` 不扩但 `write_episode_file` 对 `07-titles.md` 放行 | TC-11 + 白名单静态断言 | 静态断言（白名单集合对拍）与该用例双杀 |
| MUT-12 | PIL 改回顶层 import | TC-7 | 子进程探针 `PIL in sys.modules` |
| MUT-13 | 去掉 stdin 32 MiB 上限（分派层限流删除，🔵-11） | TC-2 超限子用例 | 超限输入不再退 2，「退 2 且零落盘」断言失败；其他拒绝子用例不经过该分支 |
| MUT-14 | 去掉 `edit-*` 64 上限（🔵-11） | TC-4 上限子用例 | 第 65 个 edit 文件被放行，「上限拒绝」断言失败；其他 schema 校验用例不掩盖 |
| MUT-15 | core 把 `finalize.cover_mtime_ns` 写成 int（非十进制字符串，🔴-1 ①） | TC-8 | `^\d+$` 字符串断言与 lossless 通路逐字节相等断言双杀——int 形态在桌面端会被静默截成有损 double，这正是 🔴-1 要防的 |

---

## 8. 施工 PR 划分

| PR | 范围 | 前置 | 验证 |
|---|---|---|---|
| **PR0**（文档，非代码） | ~~ADR-0025 用户接受 + 同步修订~~（2026-09-26 已完成）+ D-R2 文档 | 用户接受 ADR-0025（已完成）、D-R2 授权（已获） | `uv run pytest tests/test_docs_invariants.py` |
| **PR1**（core：导入 + 编辑） | `paths.atomic_write` bytes（C12-R2）→ `/import-cover` → `pipeline/cover_edit.py` + 工具注册 + `config/agent/tools.json` creative 清单 + `config/project.json` cover 段；**既有断言更新（🟡-7）**：`tests/test_candidates_propose.py:441` 与 `tests/test_agent_web.py:864` 的 `== 12` → `== 13`、865 的 `<= 12` → `<= 14`（`test_every_tool_has_existing_adr` 的逐工具 ADR glob 对 `ADR-0025` 天然通过——文件已存在）；TC-1~TC-7 + MUT-1~6、MUT-12~14 | PR0 + C12-R1/R2 授权；**渲染基线 sha256 首日先跑回填**（A1/A2） | `uv run pytest` 全绿 + 变异实跑回填 |
| **PR2**（core：定稿记录） | S3-R12（approvals + cli 双表面）+ C12-R1 白名单；TC-8~TC-11 + MUT-7~11 | S3-R12、C12-R1 授权 | 同上 |
| **PR3**（desktop） | S8-R18（spawn 模板）+ 导入 UI + S8-R19（09 卡两输入）；TD-1/2、TE-1 | PR1/PR2 + S8-R18/R19 授权；S10-R1 已并入 Spec 10 或其迁移义务已登记 | `npx vitest run` 全绿 + e2e + A3/A4 实测回填 |

---

## 9. 验收门禁清单

- [ ] **门禁 0（前置）**：ADR-0025 状态「已通过」（**已满足**，2026-09-26）；§6.1 全部修订请求获用户授权（**已满足**，2026-09-26）；本 spec 红队 🟢（**待评审**）。
- [ ] **门禁 1（写纪律）**：导入与渲染的全部写入经 core + 原子落盘；I1 不破（Spec 8 TG-2/TI-3a 全绿）；I2 扩展清单与 TI-3b 一致；编辑工具只读源、不覆盖任何已存在文件（TC-1/TC-4/MUT-2/MUT-5）。
- [ ] **门禁 2（渲染可复现）**：TC-3 跨进程字节级一致 + 基线对拍；MUT-3/MUT-4 被捕获；Pillow 版本与字体文件在 config/lock 中钉死。
- [ ] **门禁 3（导入保真与防御）**：TC-1 字节全同（不重编码）；TC-2 全部拒绝面零落盘；MUT-1 被捕获。
- [ ] **门禁 4（定稿记录）**：09 ack 缺封面或标题必拒（TC-9/MUT-7）；finalize 四键落对象与事件、`cover_mtime_ns` 为十进制字符串且经 losslessJson 通路逐字节无损（TC-8/MUT-9/MUT-15，🔴-1）；CLI 双表面标题逐字节无损（TC-10）。
- [ ] **门禁 5（只出候选）**：grep 断言 LLM 工具表无「定稿」语义工具；`cover_edit` 输出名校验强制 `edit-` 前缀；finalize 唯一写入点是显式 ack 路径（代码审读 + TC-9）。
- [ ] **门禁 6（工具表封顶）**：`TOOL_SCHEMAS` 恰 13 个；`cover_edit` 的 `adr` 字段为 `ADR-0025`；**`<= 14` 上限断言接替既有 `<= 12`**（两处既有 `== 12` 断言已更新为 `== 13`，🟡-7）；ADR-0021 与 direction 红线 6 的口径已同步为 14。
- [ ] **门禁 7（依赖纯洁）**：§5 全部子进程探针断言 + MUT-12；`pyproject.toml` 零改动。
- [ ] **门禁 8（零回归）**：全量 `uv run pytest` 与 `npx vitest run` 绿；既有 09 ack（不带 finalize 的旧调用方——终端手工）在迁移期行为按 S3-R12 裁决执行（v0.1 默认：09 一律必填，无豁免）。
- [ ] **门禁 9（真机手验）**：打包版上完成一次「拖入两张图 → agent 渲染两版 → 选定一版与标题批准 09」全流程（A3/A4 实测回填）。
- [ ] **门禁 10（文档门禁）**：`uv run pytest tests/test_docs_invariants.py` 全绿；D-R2 落地；`docs/dev/plans/README.md` 状态行更新。

---

## 10. 潜在红旗与自纠预案

| 风险序号 | 潜在红旗 | 根因与危险性 | 预案与防御动作 |
|---|---|---|---|
| RF-1 | 字体文件缺席（新机、外置盘脱卸、人还没下载） | 渲染直接不可用；若 fallback 系统字体则同参数不同字节且静默 | 不 fallback，`CoverEditError` 含 config 键与期望路径（TC-6/MUT-6）；runbook 08 写明一次性放置动作（D-R2） |
| RF-2 | Pillow 或字体文件变更后渲染字节漂移 | 基线测试变红被当成「测试脆弱」而非信号 | 基线变红 = 依赖变了，必须重跑回填并在 PR 说明；这是设计意图（§2.2 第 2 桩） |
| RF-3 | 解压炸弹 / 畸形图 | 人拖进来的图来源不可控（网页另存） | 32 MiB + 4 千万像素 + 完整解码三闸（TC-2/MUT-1） |
| RF-4 | `edit-*.png` 失控增生 | 多轮迭代 + 不覆盖 = 单调增长 | 单期 64 上限（TC-4）；期目录本就不进 git，磁盘影响可忽略 |
| RF-5 | 定稿后封面文件被覆盖（cover.py 重跑重写 `cover-N.jpg`；import/edit 不覆盖护不住机筛候选） | 09 记录的路径事后指向别的字节 | finalize 记 `cover_size`（int）与 `cover_mtime_ns`（十进制字符串，🔴-1 ①）双指纹（§2.3），审计可比对；不在 ack 后加锁（过度工程，发布后该文件无人消费） |
| RF-6 | ttc index 漂移静默换字 | 实测同 ttc index=0/1 是不同家族（A1） | config 钉 `font_index` + TC-6 家族名断言 |
| RF-7 | 标题讨论结论不落盘 | 讨论在会话里，人以为「说过了」但候选文件没更新 | 写候选必弹卡（可见）；09 ack 必填标题（最终值兜底）；状态卡 09 引导行沿用 Spec 3 既有机制 |
| RF-8 | 伪扩展名/内容不符的图混进 07-cover | 终端 `cp` 通道跳过导入校验（产物即状态的合法旁路） | `cover_edit` 渲染时完整解码（坏图当场报错）+ 09 ack 校验存在性与后缀——坏图在两条消费路径上都被拦 |
| RF-9 | renderer 拖拽 API 在 Electron 44 行为不符预期（A3） | 未实测 | 退路 `<input type="file">`；PR3 首日实测，失败降级 |
| RF-10 | 行号漂移 | 本文行号是 `8bf711d` 快照；Spec 9/10/11 施工会推动 `cli.py`/`approvals.py` 位移 | 附表全部按内容锚点可重核；施工守则 3 已要求动工前重核 |
| RF-11 | 行对齐短缺的 PNG 静默补黑（🟡-5 反转实测的副产品） | Pillow 12.3.0 实测：zlib 合法但缺整行的 IDAT，verify 与 load **都放**，缺行填黑像素——「完整解码」闸对这一类构造无效 | 如实声明为已知上限：该构造不是真实工具会产出的损坏形态（真实截断是文件级，被 open/verify 拦）；封面候选必然经人预览，缺行补黑肉眼立见；不为它自造 IDAT 长度校验（超出现实威胁模型） |

---

## 附：行号核实自查表

2026-09-26 对照工作树逐行核实（HEAD `8bf711d`；`uv run pytest` 1715 passed、`npx vitest run` 121 passed 基线实跑）。

| 引用 | 核实结果 |
|---|---|
| `tools.py:26-29` CREATIVE_WRITABLE_FILES | ✓ 26 行 `CREATIVE_WRITABLE_FILES: set[str] = {`，27-28 两个成员，29 收尾 |
| `tools.py:336` TOOL_SCHEMAS 起；全表恰 12 键 | ✓（336 `TOOL_SCHEMAS: dict[str, dict[str, Any]] = {`；键逐一数：read_artifact/write_episode_file/list_episodes/read_status/run_pipeline/search_notes/web_search/web_fetch/acquire_propose/crawl/browser/write_memory） |
| `cli.py:1612` 裸形态分派块起；1622 `/approve`（`len(args) != 5`）；1650 `/reject` | ✓（1612 `if len(args) > 1:`；1622 `if args[1] == "/approve"`；1624-1628 长度与 `--id` 校验） |
| `cli.py:800-802` fail-closed side_effect 分流 | ✓（801 `side_effect = TOOL_SCHEMAS[name].get("side_effect", True)`；802 `if not side_effect:`） |
| `approvals.py:35` HUMAN_STOPS；`51-56` _STOP_ARTIFACTS（55 行 09）；`57-63` _STOP_GATE（61 行 09 None）；`832` def approve | ✓（逐行实读；832 `def approve(`，签名无 finalize） |
| `paths.py:118-128` atomic_write（text 形态） | ✓（118 def；127 `tmp.write_text(data, encoding="utf-8")`；128 `os.replace`） |
| `cover.py:511-556 titles()`（517 dest；518-522 已填表不覆盖守卫） | ✓（511 def；517 `dest = episode / "07-titles.md"`；518-522 正则判已填 + 打印跳过） |
| `status.py:398-409` 09 工序（`next_command=None`） | ✓（402 `current_step="09 人工发布"`；408 docs_ref 09） |
| `config/agent/tools.json` creative 清单 10 项 | ✓（实读 JSON：read_artifact/write_episode_file/list_episodes/read_status/search_notes/web_search/web_fetch/crawl/browser/write_memory） |
| `config/project.json:21-22` subtitle.font 是 fontconfig 名（与 Pillow 文件路径机制不同） | ✓（21 `_note`「font 必须是本机 fontconfig 认得的名字」；22 `"font": "Hiragino Sans GB"`） |
| `pyproject.toml:31` pillow>=11.0；uv.lock 钉 12.3.0 | ✓（31 行原文；uv.lock `pillow-12.3.0` whl 条目；`PIL.__version__` 实测 12.3.0） |
| `scout.py:385-393` pi 工单追加 07-titles.md 第 6–10 行 | ✓（385 严禁覆盖 1–5 行；390 目标文件；393 验收） |
| 真实期 07-titles.md 候选表全空 | ✓ 实读 `data/episodes/2026-07-30-春物-雪乃适合大老师/07-titles.md`（1–5 行全空；模板头「标题由 agent 写」） |
| 渲染跨进程字节级确定性 | ✓ 实测（2026-09-26 `/tmp/ava-spec12-scratch/probe.py`：Hiragino ttc index=0、96 号、stroke 6，1280×720 两独立进程 sha256 全同 `3e606ec392c33b80…`，35919 字节） |
| ttc index=1 是另一家族 | ✓ 实测（同 probe：index=0 `('Hiragino Sans GB','W3')`、index=1 `('.Hiragino Sans GB Interface','W3')`） |
| `/System/Library/Fonts/Hiragino Sans GB.ttc` 存在 | ✓（ls 实测） |
| Pillow `MAX_IMAGE_PIXELS` 默认值 | ✓ 实测（`uv run python -c "from PIL import Image; print(Image.MAX_IMAGE_PIXELS)"` → **89478485**；🔵-9 更正，v0.1 写的「178M」不成立） |
| Spec 9 请求帧四类（`tool_call/fetch/checkpoint/memory_ack`） | ✓（2026-09-25-agent-session-protocol-spec.md:342，**位于 §3.2「人审请求」**（起于 336 行）——🔵-9 更正，v0.1 误写 §3.1） |
| `cover.py` `titles()` 行范围 | ✓（511 def 起、555 `return dest` 止；558 已是 `main`——🔵-9 更正，v0.1 写 511-556） |

### v0.2 补核（红队一轮修订涉及的新引用与实测，2026-09-26 逐行/逐次核实）

| 引用 | 核实结果 |
|---|---|
| `desktop/src/shared/losslessJson.ts` reviver 只认 `mtime_ns` 键（`k !== "mtime_ns" ? v : …`） | ✓ 实读源文件（🔴-1 属实）；`SELF_CHECK_TEXT` 夹具值 `1790171112636927676` 在位 |
| Spec 8 RF-23 冻结预案「新增任何可能超过 2^53 的整数字段必须同步扩展规则 8 与 TE-13」 | ✓ 原文比对（archive line 1033） |
| `spawner.ts:22` 模板名 `APPROVE`（非「ACK」） | ✓（`export type Template` 22 行 `| "APPROVE"`，88-90 case 分支） |
| `cli.py:1624` `/approve` 手解析 `len(args) != 5` 即退 2 | ✓（🔴-2 属实） |
| `tests/test_candidates_propose.py:441`、`tests/test_agent_web.py:864-865` 的 `== 12` / `<= 12` | ✓（441 行 `assert len(TOOL_SCHEMAS) == 12`；864-865 在 `test_every_tool_has_existing_adr`（858 def）内，该用例 866-875 逐工具 glob ADR 文件，ADR-0025 已存在故 `adr: "ADR-0025"` 天然过） |
| Pillow 12.3.0 校验边界（verify/load 分工） | ✓ 实测（scratchpad `mut1b/c/d.py`）：文件级截断 → open/verify FAIL（`UnidentifiedImageError`）；IDAT CRC 错 → verify FAIL / load OK；IDAT 乱字节 → verify OK / load FAIL（`broken data stream`）；zlib 合法缺 1 字节 → verify OK / load FAIL（`image file is truncated`）；**zlib 合法缺整行 → verify OK / load OK（缺行补黑像素 `(0,0,0)`）**——RF-11 的证据，🟡-5 红队建议构造「短 IDAT」只在该行对齐情形下成立，行对齐时不成立 |
| Spec 8 §2.3 I1/I2、§3.4 spawn 闭集、§4.4 preload 零暴露 | ✓（archive 文档 199-200/500-510 行原文比对；preload 源文件注释「不 exposeInMainWorld 任何函数（§4.4）」一致） |
| direction §6 Spec 12 范围全文（导入/编辑工具/标题讨论/定稿权在人/明确不做；工具表封顶条已改写为「ADR-0025 已于 2026-09-26 接受，封顶 12 → 14」） | ✓（direction 原文比对，v0.2 接受后重核） |
| ADR-0021「数量封顶在 14 个（2026-09-26 经 ADR-0025 由 ~12 上调：现役 13 + 预留 1，第 15 个须再立 ADR）」 | ✓（ADR-0021「不做的事」首条原文，v0.2 接受后重核） |
| direction §4 红线 6「工具表封顶 14 个（2026-09-26 经 ADR-0025 由 ~12 上调：现役 13 + 预留 1）；新增工具必须有 ADR 编号」 | ✓（原文比对，v0.2 接受后重核） |
