# Implementation Spec：pi 侦察派工单（ava ↔ pi 交接协议）

日期：2026-09-21（**v0.4**，三轮红队收口；状态：**可动工**）
上位文档：`~/Desktop/ava-pi-双Agent协同与交接缺口-需求交接-2026-09-21.md`（需求交接）

> **v0.4 修订来源**：第三轮红队（0🔴 + 2🟡 + 2🔵，全收；总裁决「可动工」）。
> 🟡-1：probe() 单源化没写模块级依赖边界——照触点表直译必踩两雷：clips↔scout
> 循环 import（MIN_CLIP 在 clips 初始化完成前不存在）、status 热路径经 scout 拖入
> numpy/ML 栈。修法：scout 模块级依赖白名单 stdlib + paths + bgm，MIN_CLIP 与
> resolve_patch_floor 一律函数内延迟 import，外加一行 numpy 回归断言。
> 🟡-2：判据 4 标「强烈建议」与「全部成立才算过」自相矛盾——它正是为接零向量池
> 而生，可跳过等于没加（S9）。修法：判据 4 转必选，豁免通道（贴输出交人裁决）保留。
> 🔵 两条一并收：patch 拒发后 fall through 到 notes 检测；主动补料工单判据 4 不适用。
> 三轮收敛曲线：5🔴+9🟡 → 1🔴+7🟡 → 0🔴+2🟡 → 收口。

> **v0.3 修订来源**：第二轮红队（1🔴 + 7🟡 + 4🔵，全收）。🔴-1：洁净度条款的引用
> 悬空——runbook 04.5 节根本没有台标/水印条文，真条文在 `skills/acquire-assets/SKILL.md`，
> 规范衰减从引用断链处原样回归。🟡 层全部是协议文本的边界收口：可救谓词单源化
> （detect / render / advisory 三处共用，消灭「空任务工单」与「死路指路」）、
> 工单钉死解释器与 cwd（推导态该喂的没喂）、titles 候选槽位冲突、企划期 floor 无义指令。
> 复合判据专项枚举：11 条可达退出路径堵住 9 条，剩 2 条（零向量池、洁净度）分别由
> 新增判据 4 与 §6 诚实声明收口。

> **v0.2 修订来源**：首轮红队（5🔴 + 9🟡 + 6🔵，全收）。🔴 四条同源于「声称复用
> 现有机制却没有逐行核对机制的真实行为」：load_pool 半建守卫（写 requests.md 会锁死
> clips）、ingest_patch 两段式退出码（0 ≠ 完成）、rescue 的 anchor 排除（锚点段补丁池
> 结构上救不了）、MIN_CLIP 早退（<2.5s 缺口不值得救）；🔴-4 是对 park span 停机点
> 记账相邻交互的失察（双记污染止损线）。🟡 层纠正两处事实错误（01-topic.md 无
> frontmatter、07-titles.md 无「5 条机器候选」）与一处架构错误（工单内嵌标准原文 =
> 第二事实源，改为按绝对路径引用 SSOT）。

---

## 0. 一句话设计

**派工单即协议。** `ava` 新增一个纯函数渲染器 `pipeline/scout.py`，把「缺什么、
落到哪、怎么算验收」渲染成一份**自包含 Markdown 派工单**（落盘
`scout-ticket-<type>.md`）；人复制粘贴给 `pi`；`pi` 不需要任何预装知识即可按单作战，
且必须跑绿单内**复合验收判据**才许报完成。无守护进程、无 IPC、无新依赖；
硬约束不抄原文，工单按绝对路径指 STANDARD / runbook / skills 的现行条文（SSOT，
`ava` 与 `pi` 同机，读盘零成本）。

## 1. 关键设计决策（对应交接文档 §7.1–7.4）

### 7.1 生成机制与落点：手动触发 + 状态指路，可救谓词单源

- **唯一生成入口**：REPL 快捷键 `/scout`（进程内直调，同 `/status` 的实现方式，
  **不进** `run_pipeline` 白名单、**不进** LLM 工具表——6 工具冻结不动）；
  CLI 形态 `python -m pipeline.scout <期目录> [--type patch|notes|titles] [--floor <值>]`
  等价但不记时（见 7.4）。`/patch` 占位接通为 `/scout --type patch`，REPL 与
  `ava <期> /patch` 裸形态（cli.py:1084-1086）两处一起接。
- **可救谓词单源**（detect、渲染、advisory 三处共用 scout.py 的一个只读探针，
  防止三份检测语义分叉——cli.py:925 已有同型事故护栏先例）：
  ```python
  def probe(ep_dir) -> dict:
      """只读探针，返回 {"patchable": [...], "unrescuable": [...], "missing_notes": [...]}"""
  ```
  段可救 ⟺ **channel ≠ anchor 且（status ∈ {no_match, no_source} 或
  residual ≥ 2.5）**（residual = duration − Σ clips[].dur；两道结构闸与 rescue 一致：
  rescue-A/B 跳过 anchor，clips.py:1001/1080/1129；residual < MIN_CLIP 早退，
  clips.py:1133-1137）。
- **类型自动推断**（`--type` 可覆盖）：
  1. `04-clips.json` 存在且有失败段（status ∉ {ok, ok_extended}）：
     - `patchable` 非空 → `patch`；
     - **`patchable` 为空 → 不生成 patch 工单**，逐段输出状况与人工指引
       （锚点系段 → 改锚点/改稿；residual < 2.5s → 走 05 人审或改稿），
       **并继续规则 2 的 notes 检测**（跨番期缺笔记与排片落空同场是常态，
       detect 返回多结果，两类缺口一次报全）。
       S4：拿不到能救的信息不定罪，空任务工单不许签发；
  2. `01-topic.md` 存在：用 **`bgm.animes_of()`**（现成解析器，处理全角冒号/
     括号短名/素材番剧列表三形态，不自写解析——R1）逐素材番检查
     `data/library/notes/<番>.md`，有缺 → `notes`；animes_of 返回空 → 跳过不定罪。
     跨番/企划期（ADR-0010）按素材番表逐部检查，不查企划名；
  3. `07-titles.md` 存在 → `titles`；`--type titles` 而它不存在 → E10 报错
     「先 /run cover」；
  4. 皆不命中 → 报错列出三种 `--type`，不猜。
  5. **`--type patch` 而失败段为零（主动补料）**：不渲染段表，工单只写
     「无缺口可推导——素材直落 `patch_assets/` 后跑验收命令（runbook 04.5）」，
     不签发空任务。**判据 4 不适用**（无「本单目标段」）：验收 = 判据 1–3 +
     人回 ava `/run clips` 确认补丁池可被检索。
- **不自动打印全文**。`status.py` 两条 advisory 各自 try/except **调 `scout.probe`
  取数拼文案**（同一份探测结论，永不指死路：`patchable` 为空时文案自然是「N 段失败
  且补丁池救不了，改锚点/改稿」）；`clips` 失败页脚同样调 probe 决定指路措辞。
  指路统一为「**REPL 内敲 `/scout`**（或命令行 `python -m pipeline.scout <期>`）」，
  REPL 形态在前（CLI 裸跑不记时，见 7.4）。全文只在人敲 `/scout` 时出。
- **复制形态**：stdout 上派工单夹在唯一标记行间，其余诊断一律走 stderr；同内容
  `paths.atomic_write` 落盘 `data/episodes/<期>/scout-ticket-<type>.md`
  （带类型后缀：同期缺笔记 + 缺素材并存时不互相覆盖）。
- **场景 A（新番立项缺笔记）入口**：`ava new <期名>` → 填 `01-topic.md` 的 `番:` →
  `/scout --type notes`。工单落盘在期目录内，不要求期走完后段工序。

```
════════ pi 派工单（连同首尾标记行整段复制） ════════
<markdown 全文>
════════ 派工单结束 ════════
```

### 7.2 pi 侧固化形态：工单自包含是机制，标准按引用不摘录

- **协议约束力在工单正文**，但正文**不嵌标准原文**。S11/S12 是活条款，抄进
  scout.py 模板 = 第二事实源。工单按绝对路径引用现行条文——pi 同机，读盘零成本。
  **引用必须指到真有该条文的文件**（二轮 🔴-1：指错文件 = 约束不存在）：
  - 笔记标准 → `docs/dev/STANDARD.md` S11/S12；
  - 入库规格与流程 → `docs/runbook/04-clips.md` 04.5 节；
  - 素材采掘纪律（渠道、无台标/水印、原图分辨率）→ `skills/acquire-assets/SKILL.md`。
- 工单只承载两类自有内容：
  1. **推导态**：缺口段表、资产基数 N、落盘绝对路径、**钉死解释器与 cwd 的命令**
     （`sys.executable` 与仓库根是 ava 知道而 pi 算不出的推导态，见 §3.A）、
     复合验收判据；
  2. **稳定操作增量**：跟随代码报错文案走的命令（如 ingest_patch 图片守卫里写死的
     ffmpeg 转码命令），代码改了报错文案就改，不独立腐化。
- 可选加速器 `skills/ava-scout/SKILL.md` 维持 P2 再评；没有它协议照样成立。
- 防「自由发挥偷懒」靠复合验收判据，不靠叮嘱；报完成时必须贴验收输出。

### 7.3 回流闭环：复用既有缝，不写 04-patch/requests.md

| 缝 | 处置 |
|---|---|
| `04-patch/requests.md` | **不写**。`ingest_patch.load_pool` 半建守卫「04-patch/ 存在但缺 pool.json → SystemExit」（ingest_patch.py:130-136），而 `clips.run()` 无条件调 load_pool（clips.py:825）——写 requests.md 创建目录那一刻起整期 clips 被锁死到 pi 交付。该占位头从未被消费，不值得对接（YAGNI）。段表只在 `scout-ticket-patch.md` |
| `status.py` advisory「补料挂起」 | 不动 |
| `ingest_patch` + clips rescue | 不动；工单把它写为验收命令（复合判据见 §3.A），pi 与人同机可跑 |

准入卡口不变：pi 落盘 → ingest_patch 复合判据绿 → 人回 ava `/run clips` →
05 人审 `--approve`。`render.py:1068` approved-diff 硬闸保证补丁改动不重走 05
就渲不出来，N5 卡口完好。pi 执行 ingest_patch 不是权限开口（人本机通用 agent 本握
shell，工单不授予新能力，且它只写 04-patch/）。

### 7.4 人时记账：计入，与 park span 互斥不双记，零值条目不落盘

- REPL `/scout` 复用 `record_human_time`：发单记 `entered_at`，人回 ava 敲下一条
  命令记 `left_at`，写 `human_time.json`，`stop: "scout"`。**minutes == 0 的条目
  不落盘**（连敲两次 `/scout` 的噪音）。
- **与停机点 park span 互斥**（照抄 03.5 先例，cli.py:103-106）：scout span 开启时
  先 `close_stop()` 挂起当前 park span，关闭时若 step 仍在停机点则重开——否则
  同一段墙钟被 status.py:113 无脑求和两次，恰好污染本要保护的止损线。
- **口径诚实标注**：该墙钟含 pi 执行时间，是「本次缺料造成的流水线停顿」上限。
  k 值回填时将 `scout` 条目单列观察。
- CLI 裸跑不记时，spec 不为此加状态机。

## 2. 派工单通用格式

无 YAML frontmatter（没有消费者的机器接口是死重；将来真要做「未消化派工单」检测，
靠 `scout-ticket-*.md` 文件名即可）。开头一行元信息 + 一段环境钉死：

```
# pi 侦察派工单 · <类型> ｜ 期: <期目录名> ｜ 番: <番> ｜ 生成: <ISO8601>

> 执行环境：所有命令在仓库根执行，解释器用钉死值：
>   cd <仓库根绝对路径>
>   <sys.executable 写实> -m pipeline.<模块> ...
> （不要用系统 python——依赖在项目 .venv 内，E6）

## 任务
<一段话说清要什么、为什么（缺口上下文）>

## 遵守的标准（先读原文，逐条遵守）
<绝对路径 + 节号清单>

## 硬约束（违反即返工）
<类型特定的稳定操作增量>

## 产物与落盘路径
<绝对路径表>

## 验收（全部成立才算过，报告时贴输出）
<命令 + 复合判据>

## 禁止事项
<类型特定清单，每条对应一个已知返工事故>

## 完成后
<人回 ava 要敲什么>
```

## 3. 三种类型模板要点

### A. patch（动态补料派工单）

- 数据来源：`scout.probe()`（段表 = patchable / unrescuable 两区直出）。
- **段表分两区渲染**：
  ```
  ## 补丁池可救（pi 的任务）
  | 段号 | 状态 | 需补足时长 | 配音原文 | 查询原文 | 通道 |
  ## 补丁池救不了（给人看的，不要采料）
  | 段号 | 状态 | 行动 |
  | 段7  | anchor_overlap | 改 02-script.md 锚点去重，重跑 clips |
  | 段9  | 锚点 short，缺口 1.2s < 2.5s | 补丁通道不接管，走 05 人审或改稿 |
  ```
- 工单开头一句前置声明：「动手前先确认 `patch_assets/` 无遗留文件（status advisory
  『补料挂起』会显示）；遗留会被本次 ingest 一并登记，使判据 3 的资产计数对不上。」
- 硬约束：
  - **仅视频文件**；图片先转微动视频（命令照抄 ingest_patch 图片守卫的报错文案：
    `ffmpeg -loop 1 -t 8 -i in.jpg -pix_fmt yuv420p out.mp4`）；
  - **素材采掘纪律逐条遵守 `<仓库>/skills/acquire-assets/SKILL.md`**（渠道选择、
    无台标/水印、原图分辨率）；**入库规格与流程逐条遵守
    `<仓库>/docs/runbook/04-clips.md` 04.5 节**。边界声明：「补丁通道不走
    acquire-assets 的 candidates.json 人审流程——那是 Phase 0 池扩充的闸门；
    补丁素材直落 patch_assets/，由 ingest 门禁与 05 人审把关」；
  - 按 Mode 1（纯净画面）采集即可，渲染强制 `-an` 剥音轨（ADR-0013），
    素材有无音轨皆可；音画同源（Mode 2）缺口的派工**不在本协议覆盖范围**（见 §7）；
  - 单段补料镜头本身 ≥ 2.5s（MIN_CLIP，短了 rescue-B 连检索都不发）；
  - 落盘到 `<期>/patch_assets/`。
- **验收（复合判据，全部成立才算过）**：
  ```
  cd <仓库根> && <python> -m pipeline.ingest_patch <期目录>
  1. 退出码 0；
  2. stdout 含「补料入库完成」（出现「已提交云端打标」= 两段式第一段，等远端
     tmux 跑完必须重跑本命令；退出码 2 = 远端打标运行中，等待后重跑）；
  3. 已登记资产数从 N 涨到 N+<本单交付数>——N 由 scout 生成工单时读
     04-patch/pool.json 写实数（无 pool.json 则 N=0），计数命令一并钉进工单：
     <python> -c "import json;print(len(json.load(open('<期>/04-patch/pool.json'))['assets']))"
  4. <python> -m pipeline.clips <期>：本单目标段 status 必须转
     ok/ok_extended——**以页脚段级输出为准**（exit code 1 可能只是「救不了区」
     的锚点段仍在，不必然是本单失败）；目标段未转 ok 或命令本身失败，
     贴输出交人裁决，不许报完成。
     （安全：手改检测闸对 was_failed + is_patch_rescue 的段变化显式放行，
     clips.py:1206-1211，且补丁池在场时自动写 .prepatch.bak）
  ```
  判据 4 是必选项，不是可选项（S9：跳过不是通过）——它接住「零向量池」变体：
  captions 全零向量时判据 1–3 全绿但 rescue 检索全部得分 0 < floor，
  只有真跑 clips 能现形。豁免通道是「贴输出交人裁决」，不是「不跑」。
- **floor 预消解**：scout 生成时调 `resolve_patch_floor` 试算——
  主番已标定 → 验收命令不带 `--floor`；素材番未标定 → 工单写
  「先 `<python> -m pipeline.vprobe scene <番> <集>` 标定」；
  **企划期（animes_of 长度 > 1，无单一主番可继承）→ `/scout` 当场 E10 报错**：
  「企划期补丁池 floor 无主番可继承，请人拍板后以
  `/scout --type patch --floor <值>` 重跑」，scout 把 `--floor` 透传进验收命令
  （`vprobe scene <企划名>` 是无义指令——企划名没有镜头表可探针，
  vindex.py:161-166）。
- **云端是计费动作**：工单写明「实例不可达时停下来报告人，由人决定是否
  `cloud up`」——pi 无权自行开机。
- 完成后：人回 ava `/run clips`，补丁池自动 rescue 补位，再走 05 人审。

### B. notes（Phase 0 番剧笔记采掘派工单）

- 触发：`bgm.animes_of()` 解析出的素材番中，`data/library/notes/<番>.md` 缺失的
  逐部列出（advisory 写明缺哪几部）。
- 标准按引用：STANDARD.md S11（零上下文对抗审查 + 双重指标 + 终审裁决）、
  S12（行级时间码指到台词行）；工单只写稳定操作增量：
  三份产物落盘 `data/library/notes/`——笔记 `<番>.md`、审查报告、裁决记录。
- **验收（半机器门 + 显式人工步）**：
  1. `<python> -m pipeline.vindex status --anime <番>` 七条数字中「笔记 == 片源」
     （vindex.py:1300-1317/1394-1396）。**前置分支**：若该命令报「片源登记表里
     没有《番》」（ingest.py:406-407，Phase 0 未开始），判据 1 改为人核：
     分集速查表集数 == 该番实际集数；
  2. 三份产物齐备；
  3. **显式声明**：正确性与厚度密度无机器门禁，人工对照 S11 清单核对是强制步骤，
     跳过不算通过（S9）。
- 完成后：人回 ava 继续 `/script`。

### C. titles（标题网感对齐派工单）

- 数据来源：`01-topic.md` 张力字段（内嵌，一行；**人物志/剧情回顾/共鸣期无张力
  字段时渲染「本期类型=<类型>，不设张力（N2），标题扣人物/故事本身」**，不许自造
  张力——刻意找张力 = 哗众取宠）；`02-script.md` 给**绝对路径**让 pi 自己读
  （不内嵌快照——工单生成后改稿，快照即陈旧）；`07-titles.md` 现状（cover.titles
  产出标题原料 + 候选表，候选可能为空，cover.py:511-555）。
- **落盘写死**：pi 候选**追加为 `07-titles.md` 候选表第 6–10 行，不覆盖、不改写
  1–5 行已有内容**（ava agent 的候选占 1–5 槽，两组并存，09 停机点人从 10 条里
  挑 1 条）。cover 防覆盖守卫（cover.py:521-523）天然兼容。不许只打在对话里让
  人手抄（E2）。
- 硬约束：金句式不论文式（判据：能否脱离视频单独发出去）；不碰政治议题；
  **候选之间没有排名，不许标推荐度**（N6）；pi 只出候选，定稿权在人。
- 验收：无机检。人在 09 步从候选表挑一条定稿。

## 4. 代码触点清单（负行数原则）

| 文件 | 动作 | 量级 |
|---|---|---|
| `pipeline/scout.py` | **新建**（E8）：`probe()`（只读探针，单源可救谓词）/ `detect_type()` / `render_ticket()` / `main()`；读 pool.json 算 N；resolve_patch_floor 预消解；命令渲染钉死 `sys.executable` 与仓库根。**模块级依赖白名单：stdlib + paths + bgm（均轻模块）**；`from .clips import MIN_CLIP` 与 `from .ingest_patch import resolve_patch_floor` 必须**函数内延迟 import**——clips.py 页脚要 import scout，反向顶层引用即成循环（MIN_CLIP 在 clips 初始化完成前不存在）；status.py 每轮调 probe，不许经 scout 把 numpy/ML 栈拖进看板热路径 | ~220 行 |
| `pipeline/status.py` | `_detect_advisories` 增两条，各自 try/except **调 scout.probe 取数**；docstring「常驻检测四条」改六条 | ~25 行 |
| `pipeline/agent/cli.py` | `/scout` 快捷键；scout span 与 park span 互斥；record_human_time 过滤零值条目；`/patch` 两处占位接通 | ~50 行 |
| `pipeline/clips.py` | 失败页脚调 probe 决定指路措辞（可救 → /scout；不可救 → 改锚点/改稿） | ~8 行 |
| `docs/WORKFLOW.md` | 补料通道行第 3 列 →「ava <期> /scout（缺料时生成 pi 派工单）」，净增 0~1 行，守住 ≤100 行 | ~1 行 |
| `docs/runbook/04-clips.md` | 04.5 节加入口行（/scout 生成派工单） | ~3 行 |
| `tests/test_scout.py` | 纯函数测试：probe 谓词四象限（anchor 段、residual<2.5s、可救段、混合）、patchable 为空不签工单且 fall through 到 notes、主动补料无缺口文案与判据 4 豁免、animes_of 缺番跳过、验收判据含 N 与钉死解释器、企划期 floor E10、标记行配对、**import 负载回归**（`import pipeline.status` 后断言 `numpy` 不在 sys.modules，一行杀死整条热路径回归）；tmp_path 夹具 + 变异检验 | ~130 行 |
| `config/agent/tools.json` | **不动** | 0 |
| ADR | 可逆决策，不写；若 P2 让 pi 自动跑 ingest_patch 常态化，届时再评 | 0 |

## 5. 验收判据

1. **（前置检查，非判据）工单自包含性**：全新 pi session（不装 ava-scout skill）拿到
   工单能正确说出产物路径与验收命令。复述正确不算通过——它只是排除「工单本身
   有歧义」这个混淆变量。
2. **patch 真验收**：构造一期含非锚点 no_match 段，全新 pi session 仅凭工单交付素材，
   复合判据（退出码 0 + 「补料入库完成」+ 资产数 N→N+k + clips 重跑目标段转 ok）
   全绿。
3. **notes 真验收**：pi 交付后 `vindex status --anime <番>` 笔记数 == 片源数（或
   Phase 0 未开始分支的人核通过），三份产物齐备，人工 S11 清单核对签字。
4. **架构零污染**：`pytest` 全绿；无新第三方依赖；`scout.py` 唯一写权限是
   `data/episodes/<期>/scout-ticket-<type>.md`；`/scout` 发出后 clips 照常可跑
   （回归：工单落盘绝不触碰 04-patch/）。
5. **文档真实化**：WORKFLOW.md ≤100 行；runbook 04.5、status.py docstring 同步。
6. **人时不双记**：`/scout` 往返一次，`human_time.json` 出现一条 `stop: "scout"`，
   不与停机点条目重叠，无零值条目。

## 6. 风险与推翻条件

- **判据覆盖的诚实边界**：复合判据只管机械入库与可检索性；**素材的对题性与洁净度
  由 05 人审兜底**——水印/台标不会被任何机检拦截（ingest 只查扩展名与完整性，
  render 的 scale/format 归一化也不报错），工单违规的表现形式是 05 打回而非机检
  拦截。这是协议有意保留的人审职责，不是漏洞。
- **pi 贴伪造的验收输出**：判据 4（真跑 clips）与 pool.json 实物让伪造立刻露馅，
  失败成本 = 一次重跑。不做防伪。
- **锚点段分类错误**（把可救段误判进「救不了」区）：判据只看 channel 字段与
  residual 算术，简单可测；宁可漏派（人照原流程补）不可错派（pi 白跑）。
- **scout 人时口径争议**：k 值回填后若证明墙钟严重高估人时，推翻 7.4 口径，
  改一处即可。
- **类型推断误判**：`--type` 显式覆盖永远在，推断只是默认。

## 7. 明确不做（继承交接文档 §10，逐条确认 + 修订轮新增）

1. 无 WebSocket / HTTP 守护 / 自动调用桥——人是物理气闸；
2. ava 内零爬虫、零网络新增（scout.py 只读本地产物）；
3. LLM 工具表维持 6 工具冻结，tools.json 不动；
4. 不做通用多 Agent 协议，只解本流水线三类缺口；
5. P1 不做 `skills/ava-scout`（工单自包含已够），P2 再评；
6. **不覆盖音乐段/音画同源缺口**（v0.2 新增）：音乐段不走 04-clips.json
   （parse_shots 只切 `## 段落 N` 块，clips.py:167-169），detect 永远看不见它，
   「工单显式标注音画同源」分支无从触发——P1 音画同源素材派工走人工，
   render 期 FAIL 即信号。
