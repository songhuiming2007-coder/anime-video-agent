# Plan：ava-agent 架构设计与闭环控制（施工图）

日期：2026-09-18  
对应 issues：B2 / B3 / D10 / D23  
相关 ADR：ADR-0006, ADR-0014, ADR-0015, ADR-0017  
状态：**施工中，以 2026-09-18-ava-agent-impl-spec.md 为唯一实现真源**

---

## 1. 目标与背景

当前系统本质上是 `anime-video-pipeline`（一组离散的 Python 脚本工具箱）。在写稿端主力切换至 Gemini 后，02.5 改稿耗时已压进 ≤10 分钟（10分钟内视频），写稿瓶颈基本消除。但在实战生产中，人类用户依然充当了各个工序之间的调度器、调试器和状态搬运工，导致以下三大核心痛点：
1. **配音顺听摩擦力极大**：机器无法完全识别语气断层与多音字错读，人耳听出问题后需频繁跨窗口切终端、记段号、手打错误原因汇报给 Agent 重配；
2. **排片中途缺料断流**：纪录片发散性强，排片发现素材不足时，现有流程必须中断并新开 session 重新走重型入库，无法灵活热插拔补充局部素材；
3. **工作流割裂与多 Session 搬运**：资产预处理、创意找灵感、写文案、管线执行对上下文要求截然相反，用户被迫在 3 个以上的独立 Session 间人肉搬运产物。

**本方案目标**：
在不引入任何沉重第三方 Agent 框架（如 LangChain、AutoGen）的前提下，基于现有的“产物即状态”铁律，构建一个轻量、状态机驱动、兼顾人耳极简纠错与动态补料的 **ava-agent Harness**。

---

## 2. 架构设计：三层上下文与状态分层

将原本混杂在人脑中的工序，在 Agent Harness 内部划分为三个显式隔离的会话作用域（Scope）：

```
                     ┌───────────────────────────────────┐
                     │          ava CLI / REPL           │
                     │         (统一的 Agent 宿主)        │
                     └─────────────────┬─────────────────┘
                                       │
                    基于工作目录与产物状态自动路由 / 斜杠指令切换
                                       │
       ┌───────────────────────────────┼──────────────────────────────┐
       ▼                               ▼                              ▼
┌──────────────────┐         ┌──────────────────┐         ┌──────────────────┐
│   Asset Scope    │         │  Creative Scope  │         │  Pipeline Scope  │
│  (资产与预处理)   │         │ (选题与文案撰写) │         │ (视听工程与流水线)│
├──────────────────┤         ├──────────────────┤         ├──────────────────┤
│ • 目标：全局番剧库│         │ • 目标：单期剧本 │         │ • 目标：成品视频 │
│ • 特征：离线重算力│         │ • 特征：发散+约束│         │ • 特征：确定性DAG│
│ • 工具：         │         │ • 工具：         │         │ • 工具：         │
│   ingest_phase0  │         │   query_notes    │         │   ava_tts        │
│   vindex_vlm     │         │   draft_script   │         │   ava_clips      │
│   acquire_crawl  │         │   check_script   │         │   ava_render     │
│                  │         │   fix_lint_auto  │         │   run_qc         │
└──────────────────┘         └──────────────────┘         └──────────────────┘
```

### 2.1 状态判定与上下文感知（Context-Aware Launch）
直接运行 `ava [路径]` 时，不弹出冗长问卷，完全由文件产物自动推导状态：
- **命中期目录**：
  - 若只有 `01-topic.md`：自动进入 **Creative Scope**，提示是否开始写稿；
  - 若 `02-script.md` 存在且全绿：自动进入 **Pipeline Scope**，提示接管下游视听管线；
  - 若处于顺听停机点：提示进行 QuickTime/afplay 顺听与 `/voice` 纠错；若处于审片停机点：提示审阅 `04-review.html`。
- **命中根目录**：
  - 输出看板（最近活跃期号与当前阻塞卡点），支持回车秒选继续或 `ava new <期号>` 新开一期（重名期目录硬拒）。
- **会话中随时切换**：
  支持斜杠指令自由调整模式：`/chat`（选题发散）、`/script`（聚焦写文案）、`/run`（驱动管线）、`/patch`（局部补料）。

---

## 3. 配音极简纠错闭环架构（Human-in-the-Loop Voice Refinement）

> **架构决策更新**：原方案设计基于 `03-audio/review.html` 单页 Webview 点选交互，经红队评审与用户拍板，彻底废弃 GUI/Webview 方案，改为**纯 CLI、零 GUI、QuickTime/afplay 顺听 + 终端 corrections.json 交互**模式。
> 详细设计与规范参见唯一实现真源：`docs/dev/plans/2026-09-18-ava-agent-impl-spec.md` §3。

```
┌──────────────────────────────┐
│  QuickTime Player / afplay   │ ───> 人类顺听（三项抽检：生僻字/长句/段首）
└──────────────────────────────┘
               │ 终端即时随手纠错
               ▼
┌──────────────────────────────┐
│     ava <期> /voice 指令     │ ───> 交互式录入（改成 / 换种子 / 撤销）
└──────────────┬───────────────┘
               │ 结构化解析与持久化
               ▼
┌──────────────────────────────┐
│  03-audio/corrections.json   │ ───> append-only 永久资产（期级 overlay 同名键优先）
└──────────────┬───────────────┘
               │ --apply-patch 单段靶向重配（本地 / 云端）
               ▼
┌──────────────────────────────┐
│ python -m pipeline.tts <期>  │ ───> 只重配受影响段，旧音频进 03-audio/attic/
└──────────────────────────────┘
```

### 3.1 CLI 交互纠错的两大分支设计
在 `ava <期> /voice` 指令交互中，提供明确互斥的两种纠错形式：

1. **分支 A：字词级错读（多音字 / 错读）**
   - 语法：`05 重 改成 zhòng` 或交互式输入；
   - 规则：写入当期读音覆盖表（期级 overlay），注入 `g2p` 拼音规则，**仅靶向重配受影响 segment**。

2. **分支 B：整句级听感断层（语气、音色发飘、杂音）**
   - 语法：`12 换种子`；
   - 规则：不改读音规则，严格按照换种子公式 `attempt * 1000 + 段号 + seed_offset` 重掷种子生成，保留历史版本至 `attic/`。
   - 注意：语速调整不进入本次合成链（issue D26），对快慢诉求明确提示不支持。

### 3.2 补丁契约格式 (`03-audio/corrections.json`)
采用 append-only 永久资产结构，支持本地/云端一致性处理：
```json
[
  {
    "id": "c1",
    "target": "seg-05",
    "action": "replace_reading",
    "word": "重叠",
    "pinyin": "zhòng dié",
    "status": "applied",
    "applied_at": "2026-09-18T20:00:00+08:00"
  },
  {
    "id": "c2",
    "target": "seg-12",
    "action": "reseed",
    "seed_used": 1019,
    "status": "applied",
    "applied_at": "2026-09-18T20:02:00+08:00"
  }
]
```

---

## 4. 纪录片临时素材热插拔补料通道（Ad-hoc Patch Ingest）

解决排片过程中临时发现素材匮乏时的断流问题，避免重走全番 Phase 0。

1. **排片审片标记缺口**：
   在 `04-review.html` 中允许对素材不贴合的镜头标记 `[缺料: 描述需求]`。
2. **快速喂料通道**：
   - 外部手动放入：直接放入 `data/episodes/<期号>/patch_assets/`（仅限视频片段，图片需转为视频，详见 Spec §4.1）；
   - 或对话喂入：向 Agent 提供指定 URL 或直接上传。
3. **轻量增量入库指令**：
   - 运行 `python -m pipeline.ingest_patch <期目录>`；
   - 仅对 `patch_assets/` 内的新增物料进行秒级场景切分、抽关键帧、VLM 密集打标与 BGE-M3 向量化；
   - 产物写入当期局部检索池 `data/episodes/<期号>/04-patch_index.json`；
   - 排片引擎将主素材池与当期 patch 池做动态联合检索，立刻填充空缺镜头并刷新 Review 页面。

---

## 5. 改动清单与实现步骤

> 具体实现顺序与验收矩阵以 `docs/dev/plans/2026-09-18-ava-agent-impl-spec.md` §5 为准。

### 第零阶段：文档收口与基准归一（PR0）
- [ ] 消除施工图、Impl Spec 与各 runbook 间的描述分歧；
- [ ] 补齐 ADR-0018（harness 护栏）与 ADR-0019（overlay 生命周期）；
- [ ] 更正 CLAUDE.md/AGENTS.md 人时预算口径与纠错规程；建立 `tests/test_docs_invariants.py` 自动化门禁。

### 第一阶段：CLI 宿主、StateResolver 与护栏层（PR1）
- [ ] `pipeline/agent/{__init__,cli,resolver,scopes,tools}.py`：CLI 入口、StateResolver、REPL 路由、根目录看板与子命令白名单；
- [ ] `config/agent/{tools.json,scopes/*.md}`：护栏配置与 prompt 模板；
- [ ] 人类耗时记账 `human_time.json` 与看板读数。

### 第二阶段：顺听纠错闭环与 `--apply-patch`（PR2）
- [ ] `pipeline/corrections.py`：结构化解析、append-only 落盘、期级 overlay 推导；
- [ ] `pipeline/tts.py`：新增 `--apply-patch` 靶向重配与 `03-audio/attic/` 快照备份，bump `SYNTH_LOGIC_VERSION` 5→6。

### 第三阶段：单期视频补料热插拔（PR3）
- [ ] `pipeline/ingest_patch.py`：单期视频补料（视频-only），支持秒级打标、向量化与 patch 索引；
- [ ] `pipeline/clips.py`：排片引擎接入局部 patch 池，支持自动补位。

### 第四阶段：Creative Scope 纯 LLM 客户端与写稿闭环（PR4，纯 LLM 层且唯一可砍）
- [ ] `pipeline/agent/llm.py`：纯 stdlib urllib 实现 OpenAI-compatible 客户端；
- [ ] Creative 阶段写稿与机检自愈。

---

## 6. 明确不做的事（边界与防过度设计）

1. **严禁引入沉重外部 Agent 框架**（LangChain / AutoGen / CrewAI / Semantic Kernel 等），代码保持纯原生 Python。
2. **严禁将机器指标当作人耳替代品**：不做全自动“听音辨错”，坚持人耳点选裁决。
3. **严禁自动修改全局番剧资产库**：单期补料仅留在当期 `patch_assets/` 与局部索引中，保证底库的纯洁与不可变性。
4. **严禁绕过人审停机点**：观点张力（01）、文案终审（02.5）、配音顺听（03.5）、排片审阅（05）四大人类停机点严格保留，Agent 仅负责消除工序之间的机械摩擦。
5. **严禁在 03-audio 阶段引入 Webview/GUI**：坚持纯 CLI 与系统原生播放器（QuickTime / afplay）顺听。

---

## 7. 完成判定

> 对齐 Impl Spec §5 的最终交付验收项：

- [ ] `ava <期>` 命中根目录与期目录均可正确识别状态并进入对应 Scope；
- [ ] QuickTime / afplay 顺听 + `ava <期> /voice` 指令能正确追加 `03-audio/corrections.json`；
- [ ] `pipeline.tts <期> --apply-patch` 能按补丁精准重配受影响段落，旧音频安全备份至 `03-audio/attic/`；
- [ ] 向 `patch_assets/` 放入短视频后，`python -m pipeline.ingest_patch` 完成打标并在排片中成功被检索消费；
- [ ] 三条 harness 护栏与白名单机制生效：禁止非白名单命令执行、拦截 `--force`、限制单期文件写入范围；
- [ ] 全套自动化测试（含 `tests/test_docs_invariants.py` 与业务测试）`-W error` 100% 绿灯。
