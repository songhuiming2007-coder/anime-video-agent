# Plan: v2 系统架构设计 —— 端云协同的工业化纪录片流水线

- **立项日期**: 2026-09-10
- **对应 Issues**: B1 (排片错配), B2 (人时超预算), B3 (10 分钟未验证), D1 (复核子题), D2 (无台词排片死角), D4 (VLM 启动), D5 (画面语义复活), D6 (错配率判据), D22 (多模态漏斗), D23 (配音顺听摩擦力), D24 (素材库扩张与缓存), D25 (CJK 音读泄漏)
- **关联 ADR**: ADR-0014 (端云解耦), ADR-0003 (视觉索引), ADR-0004 (集号锁定与 presence), ADR-0005 (相似度≠语义), ADR-0006 (Qwen3-TTS), ADR-0008 (锚点直通), ADR-0010/0011/0012/0013 (异构素材与双模态协议)
- **上位施工图**: `2026-09-08-v2-cloud-gpu-reconstruction.md`（本文件是它的架构落地层；动了其中任何既定决策的地方都显式标注）
- **执行时机**: 经人类总监确认本方案后动工

---

## 〇、 顶层判断（结论先行）

七个子系统展开之前，先记五条贯穿全局的架构判断。后面每一节都是这五条的展开。

**判断 0：v2 不是重写，是「换引擎、开通道、加闭环」。**
`clips.py` 的分配器（指针轮转）、`size()` 水填、锚点直通、presence 带内次级排序、段级不变量、截取守卫、回读拼音比对——这些是十几期踩出来的资产，**一行不动**。v2 改的是四个点：检索空间的语义质量（VLM 意象文本化）、配音引擎（IndexTTS2）、音读泄漏治理（拼音直注）、算力位置（云端 4090）。凡是这份文档没点名的模块，默认原样保留。

**判断 1：VLM 打标的真正价值不是「看懂了画面」，是把画面检索从图像-文本空间搬进文本-文本空间。**
ADR-0003 否掉第 2 层的根因不是命中率，是 **CLIP 余弦没有绝对含义，门槛测不出来**（三种定法全败，噪声地板无上界）。而文本-文本检索的门槛标定方法在本项目是被证明过的（subindex 零假设组 → NO_MATCH=0.45）。Qwen3-VL 把镜头变成 30 字中文意象描述后，画面通道复用整套已验证的文本检索纪律——**这是 D5「第 2 层复活两条路」中「交给 VLM 判断」那条的正确接法**，且顺带让索引变得人可读（caption 是纯文本，打标质量人可以抽查，CLIP 向量做不到）。

**判断 2：数据引力决定渲染位置——云端是纯推理节点，视频片源默认不出本地。**
v2 重构施工图把 04/05/渲染画在云端。按实测算账：家用宽带上行约 30~50 Mbps，一季片源 30~40GB 首传需 2~4 小时；而 03 配音 + 回读 + 检索的全部云端产物回传量 < 100MB。ffmpeg 渲染是 CPU 活，Mac 本地 x264 渲 3 分钟 1080p 约 5~10 分钟，零成本零传输。**修正：推理上云（TTS/ASR/VLM/Embedding），渲染留本地，NVENC 云端渲染不进 v2 范围**（闸门见第八节）。施工图拓扑图相应修正，动工前同步改那份文档。

**判断 3：评估不是一道新门禁，是一套「冻结基线 + 可比较增量」的测量仪。**
v1 两期成片的人审改动痕迹（`recheck diff`）与 03.5/05 结构化人评是唯一可信的真值源。评估体系复用既有判据纪律：指标只用于**同通道纵向对比**（v1 vs v2），绝不跨通道融合成一个总分（S2/S10 直接适用）。

**判断 4：成本闸是设计输入，不是事后报表。**
单期 ≤0.3 元 ≈ 4090 开机 8 分钟。这决定了：云端单期只跑「配音 + 回读 + 当期新增索引」，Phase 0 级重活（全番 VLM 打标、超分）按番一次性摊销、单独记账、单独批准。

---

## 一、 任务调度与端云协同协议（Client-Server Headless Orchestration）

### 1.1 决策：CLI 封装 SSH + rsync，不上任何常驻服务

三选一明确排除两个：

| 候选 | 结论 | 理由 |
|---|---|---|
| 轻量 FastAPI / RPC 守护进程 | **否决** | 违反「不引入 web 框架/编排」的明令不做事项；引入常驻进程 = 引入鉴权、端口暴露、进程保活三个新失效面；而我们的负载是**批处理**不是交互，RPC 的亚秒延迟优势用不上 |
| Git + OSS/网盘声明式任务池 | **否决** | 任务池本质是数据库的穷人版，违反「不引入数据库」；Git 传二进制产物（wav/mp4）是滥用；状态真值因此劈成「Git 里的声明」与「云端的实际」两处，违反产物即状态（E1） |
| **CLI 封装 SSH + rsync（采纳）** | ✅ | SSH 自带鉴权与加密；rsync 增量续传天然幂等；「产物即状态」直接映射为「同步产物目录」；零新增依赖 |

### 1.2 模块：`pipeline/cloud.py`（新建，E8）

云端不装任何本项目守护进程——云端环境 = 同一仓库的 git checkout + 云端 `config/cloud.json` 指到本机实例。**任务的概念不存在于系统里，存在的只有「本地发起的一次远端命令调用」**，调用记录落盘即账簿。

```
ava-cloud up                          # 开机（AutoDL 控制台 API），等到 SSH 可达
ava-cloud push <本期目录>              # rsync 上行：02-script.md + config/ + 必要索引
ava-cloud run  <本期目录> tts          # 远端执行 python -m pipeline.tts（tmux 包裹）
ava-cloud pull <本期目录>              # rsync 下行：03-audio/ → 本地对应期目录
ava-cloud down                        # 关机并记账
ava-cloud status                      # 实例状态 + 本次会话已计费时长 + 账簿累计
ava-cloud doctor                      # 链路自检：SSH 可达/显卡可见/模型在位/磁盘余量
```

`config/cloud.json`（进 git 的只有键结构，实例 ID/密钥在 `config/cloud.local.json`，不进 git，对齐 `CLAUDE.local.md` 惯例）：

```json
{
  "_note": "端云协同配置。机制在此，实例凭据在 cloud.local.json（不进 git）。",
  "provider": "autodl",
  "image": "anime-video-agent-v2:py312-torch2.8-cu128",
  "gpu": "RTX 4090 24GB",
  "remote_root": "/root/anime-video-agent",
  "remote_data": "/root/autodl-tmp/data",
  "budget_per_episode_cny": 0.3,
  "budget_per_phase0_cny": 25.0,
  "idle_shutdown_seconds": 300,
  "_idle_note": "300s：云端 TTS+回读单期实测 5~8 分钟量级（ADR-0014），任务结束后 5 分钟无心跳即关机；留 5 分钟是为了 pull 校验失败时重传，不是为了挂机",
  "sync": {
    "up":   ["02-script.md", "01-topic.md", "config/"],
    "down": ["03-audio/", "04-clips.json", "06-check.log"]
  }
}
```

### 1.3 GPU 生命周期与成本闸

1. **按需开机**：`ava-cloud up` 调 AutoDL API 开机；无 API 凭据时打印控制台链接并轮询等待——**不因缺凭据而假装开机**（E10：三种失败分开报）。
2. **执行**：`run` 在远端 tmux 会话里执行，本地断连不影响远端；`run` 返回前把远端产物与 `manifest` 校验和拉回比对。
3. **自动关机双保险**：
   - 主：每条 `run` 命令末尾挂 `av-cloud heartbeat` 循环；心跳停止 `idle_shutdown_seconds` 后远端 `shutdown`；
   - 兜底：远端 systemd timer 每小时检查 AutoDL 计费余额，异常时强制关机。**双保险的理由**：单期成本红线 0.3 元，而忘关机一晚上 = 40 元，是单期预算的 130 倍。这类「不报错但烧钱」的失败与静默失败同构，按「把静默失败变成当场失败」的程序处理——关机动作本身写账簿。
4. **账簿** `data/cloud/ledger.jsonl`（不进 git，可重生成故可清）：每行一次会话
   ```json
   {"session": "2026-09-12-ep3-tts", "up": "19:02:11", "down": "19:11:40",
    "gpu_seconds": 569, "cny": 0.31, "episode": "EGOIST-终局葬礼",
    "tasks": ["tts", "asr_qc"], "over_budget": true}
   ```
   `over_budget` 超线不阻断（那是诚实记录），但 `status` 命令用红字打出来——连续超线触发与「连续 3 期超人时」同级的停产复盘。

### 1.4 容错与断点恢复

| 失效 | 机制 |
|---|---|
| 网络抖动/断连 | SSH ControlMaster 长连接复用 + 自动重连；rsync `--partial --checksum` 断点续传；远端 tmux 隔离，本地掉线任务不死，`ava-cloud attach` 重新接上日志 |
| 远端任务中断 | 产物即状态在云端同构成立：`tts.py` 的 manifest 增量复用、`vindex.py` 的逐集落盘，重跑即续跑，**不做任何额外的断点框架** |
| 显存 OOM | 云端模型串行加载（TTS 出清再载 ASR），峰值驻留 ≤ 1 个大模型；批量任务（VLM 打标）batch 遇 OOM 自动减半重试一次，再失败整批诚实失败（不降级到 CPU——8B VLM 在 CPU 上的耗时本身就是失败） |
| 拉回的产物损坏 | rsync 校验和复核 + 本地 `preflight` 扩展一项「期目录产物 JSON 可解析」 |

---

## 二、 第 0 步：评估（Evaluation）体系

> 施工图第五章原话：「没有评估，就无法判断一次改动的提升源于设计本身还是随机波动。」本节把它落成机制。

### 2.1 评估对象与真值源

评估的输入只有两类，全部来自既有产物，不创造新的主观打分仪式：

1. **机器侧**：`03-audio/manifest.json`（CER、重试次数、时长比）、`04-clips.json`（状态分布、阶梯级别、scope 降级数）、`06-check.log`（门禁明细）；
2. **人审侧（真值）**：
   - **排片错配率** = `recheck diff` 的人审改动段数 ÷ 总段数。这是 D6 一直缺的完成判据，工具已存在（`pipeline/recheck.py`），缺的只是「每期 05 之后固定跑一次归档」的纪律；
   - **配音/听感**：03.5 顺听关卡从「纯听」升级为**30 秒结构化打点**（见 2.3），人时预算内。

### 2.2 子维度判据定义

全部指标**只在同一通道内做 v1↔v2 纵向对比**（判断 3）。不设总分。

| 维度 | 指标 | 定义与取数 | 门禁 or 仅观测 |
|---|---|---|---|
| **配音** | 错字率 | manifest 各段 CER 的分布（中位/P90），拼音维度比对（沿用 `tts.syllables`） | 门禁沿用既有 MAX_CER，评估只看分布移动 |
| | 重试摩擦 | 平均每段 attempts；qc_skip（ASR 盲区豁免）段数 | 观测 |
| | 韵律起伏 | **客观代理**：f0 标准差 + 句间停顿时长分布（`librosa`/`parselmouth` 提取）；**主观**：03.5 打点 1~5 分 | 仅观测——韵律好坏没有机器门槛，这正是首期反馈的盲区，先量化再谈门禁 |
| | 漏字/幻觉 | 时长比落出 DUR_BAND 的次数 + 回读编辑距离中的删/插占比 | 门禁沿用既有逻辑 |
| **排片** | 错配率 | `recheck diff`：05 人审改动段 ÷ 总段数（v1 基线见下） | 观测（排序信号） |
| | 意象命中率 | `场景` 通道段中，05 人审未改动的比例 | 观测 |
| | 无对白镜头覆盖 | 稿件中「讲无台词事件」的段落里，锚点/场景通道成功排上的比例（v1 这些段全灭） | 观测 |
| | 节奏冲突 | `anchor_overlap` / `short` / `no_match` 段数合计 | 观测 |
| **素材** | 丰富度 | 期级素材构成比：动画/Live/MV/扫图/物证 各占时长百分比，对照 `01-topic.md` 的体裁预期 | 观测 |
| | 搜集自动化率 | 当期素材中经 `acquire.py` 自动检索入库的件数占比 | 观测 |

### 2.3 03.5/05 结构化打点（30 秒，不破人时预算）

03.5 顺听结束时，终端就地弹三个 1~5 分选择（音色稳定 / 韵律起伏 / 错字），05 审片结束弹两个（意象贴合 / 节奏）。结果追加进 `03-audio/manifest.json` 的 `human_review` 与 `04-clips.approved.json` 的 `human_review` 字段——**产物即状态，人评也是产物**。五个按键合计 < 30 秒，且把原来「听完就算了」的隐性判断变成可对比数据。

### 2.4 基线与 Harness 原型

**v1 基线冻结**（先于一切 v2 改动执行，这就是施工图说的「第一交付物极小且可验证」）：

```bash
python -m pipeline.eval freeze data/episodes/<第一期> --tag v1-baseline
python -m pipeline.eval freeze data/episodes/<第二期> --tag v1-baseline
python -m pipeline.eval report --tag v1-baseline        # 人读报告
python -m pipeline.eval diff v1-baseline <新tag>         # v2 每次改动后对比
```

`pipeline/eval.py`（新建）只做三件事：`freeze`（把 2.1 的两类输入抽成一份不可变快照 `data/eval/<tag>/<期>.json`）、`report`（人读表）、`diff`（两 tag 逐指标对比表）。**不实现自动排名**——diff 表交人看，判断提升的是人（判断 3）。

快照 Schema：

```json
{
  "episode": "EGOIST-借躯降生", "tag": "v1-baseline", "frozen_at": "2026-09-10",
  "tts":  {"cer_median": 0.03, "cer_p90": 0.11, "attempts_mean": 1.4,
           "qc_skip_segments": [5], "dur_ratio_out_of_band": 0},
  "clips":{"segments": 32, "status": {"ok": 27, "ok_extended": 2, "no_match": 3},
           "human_changed_segments": 11, "mismatch_rate": 0.344,
           "rung_hist": {"1": 24, "2": 5, "3": 3}, "ep_fell_back": 4},
  "qc":   {"pass": true, "violations": []},
  "human_review": {"voice_stability": 4, "prosody": 2, "misread": 4,
                   "imagery_fit": 3, "rhythm": 3},
  "materials": {"anime": 0.71, "live": 0.12, "mv": 0.10, "scan": 0.07}
}
```

**v1 基线已知值**（回填自施工图第六章与 ADR-0014）：前两期人评「配音韵律」维度 ≈ 2/5（无起伏、无情感）；「现实场景动漫画面过多」——这两条就是 v2 必须推动的指标，其余维度 v1 已及格，v2 不许回退。**回退判据写在 diff 里：任何 v1 已及格指标变差 = 该改动不许合并**，与 C3 合并判据并列。

### 2.5 评估纪律

- 评估跑在**冻结基线**上，不换基线自欺；基线只能追加新期，不许改旧快照（追加式，同 issues 表「编号不回填」）。
- 每个 v2 改动点（换 TTS 引擎、上 VLM 打标、拼音直注）动工前先写「预期推动哪个指标」，完工后 `eval diff` 验证。**推不动指标的改动回滚**——这是「没有评估就动工只会浪费 GPU 算力」的执行形式。

---

## 三、 v2 配音与音频工业化流水线（`pipeline/tts.py` 重构）

### 3.1 引擎落地：IndexTTS2 主选，CosyVoice 2.0 备选，Engine 接缝不动

`Engine` 类与 `LOADERS` 表是既有接缝（ADR-0006 换引擎时验证过这个接缝够宽）。v2 新增 `indextts2` / `cosyvoice2` 两个 loader（PyTorch/CUDA 路径），**本地 mlx 的 `qwen3_tts` loader 保留不删**——它是云端不可用时的诚实降级通道，也是 A/B 对照组。引擎切换仍只改 `config/voice.json` 的 `engine` 字段，别处不动（ADR-0006 决定一的机制原样继承）。

`config/voice.json` v2 扩展：

```json
{
  "engine": "indextts2",
  "model": "IndexTeam/IndexTTS-2",
  "ref_audio": "data/voice/seg6.wav",
  "lang_code": "zh",
  "readings": {},
  "_readings_note": "v2 起默认空表：音读泄漏由拼音直注层治理（三.2）。此表降级为『逐案 override』——自动注音念错的个例才进表，且每条必须带注入日期与失败样本。",
  "emotions": {
    "_note": "02-script.md 情绪字段的受控词表 → IndexTTS2 情感参数。机制：词表在此映射；内容：每期写哪些词由写稿 skill 定。",
    "平静叙述": {"emo_vector": null, "emo_alpha": 1.0},
    "低沉克制": {"emo_text": "低沉、克制、带叹息感", "emo_alpha": 0.8},
    "激越陈词": {"emo_text": "激昂、语速加快、张力外放", "emo_alpha": 0.9}
  },
  "speed": {"慢": 0.92, "中": 1.0, "快": 1.08},
  "titles": {}
}
```

### 3.2 拼音直注脱敏层（D25 终局治理，替代 readings 手工表）

ADR-0006 补记已定性：CJK 同形字在 Tokenizer 共享 Token ID，多语种自回归模型在上下文漂移时激活日语音读（世界→秀界、监督→坚督）。已验证的物理阻断手段是**拼音直注**（`shí六首` 先例 100% 咬死）。v2 把它从手工表升级为自动化机制，新建 `pipeline/g2p.py`（E8：新增能力开新文件）：

```
speakable_v2(text):
    text = _MUTE.sub("", text)                     # 剥不发音符号（既有，不动）
    for token in heteronym_scan(text):             # pypinyin heteronym=True 扫多音字
        reading = context_reading(token, text)     # pypinyin 词组消歧取上下文读音
        if reading in RISKY:                       # RISKY = 历史音读泄漏音素集
            text = inject_pinyin(text, token, reading)   # 「世界」→「shì界」式局部注入
    for src, rep in readings_override().items():   # 逐案 override（极少数残留）
        text = text.replace(src, rep)
    return text
```

三个设计要点：

1. **注入是局部的，不是全文转拼音。** 全文拼音会摧毁模型对分词与韵律的既有先验（文本侧信息全丢），只注多音字中的**风险音素**。`RISKY` 集合不是拍的：取自既有 readings 表全部历史条目的错误读音侧（日语音读/训读音），这是本项目自己踩出来的泄漏清单，进 `config/voice.json` 的 `_risky_on_yomi` 并带来源注释（E3）。
2. **注入格式按引擎声明，不写死。** IndexTTS2 与 CosyVoice 的拼音/音素注入语法不同，格式归各 loader 的 `inject_pinyin` 实现；`g2p.py` 只负责「哪个字该读什么」。**此条待实测**：IndexTTS2 的注入语法以其官方文档与 M1 探针实测为准，验不通则该引擎降级方案 = 逐字同音替换（v1 readings 机制的自动化版），不许带「应该支持」进代码（S7）。
3. **闭环验证不收门票。** 拼音比对回读门禁原样保留（它本就工作在拼音维度，注入不改变比对口径）；注入动作逐段记进 manifest 的 `g2p_injections` 字段，审账可查——**机器自己做的替换和 readings 表一样需要可审计**。

readings 表存量 40+ 条在 M2 全部转录为 `RISKY` 音素集输入，验证新层后**连表删除**（删除动作按红线先问人）。

### 3.3 情感与韵律控制协议（SSOT 扩展）

直击 v1 基线反馈第一硬伤「无起伏、无情感」。`02-script.md` 段落块新增两个**可选**字段（不写 = 平静叙述，行为与 v1 一致，旧稿零迁移）：

```markdown
## 段落 12

配音: 他们烧掉日记的那个夜晚，chelly 还不知道自己已经被判了死刑。
情绪: 低沉克制
语速: 慢

画面:
  锚点: 罪恶王冠 S01E19 18:22
```

- **受控词表，不是自由文本**：`情绪` 取值必须在 `config/voice.json` 的 `emotions` 键里，`check_script.py` 新增一项机检拦表外词（自由文本情绪描述 = 每期发挥不稳定，且无法进 manifest 审计）；
- **执行映射**：`tts.py` 合成时按词表查得 IndexTTS2 情感参数（情感文本描述或 8 维情感向量，二选一以 M1 探针实测为准——**待实测**）；
- **韵律客观回流**：情感参数与合成结果一起进 manifest（`emotion`/`speed` 字段），eval 的 f0/停顿分布（二.2）因此能按情绪分组统计，回答「情感声明是否真的改变了声学输出」——**防止情绪字段沦为写了不生效的安慰剂**。

### 3.4 时长可控与排片解摩

N1 顺序不可交换的理由不变（先配音再排片）。IndexTTS2 的时长可控用在**收口**而非**硬贴**：

- 合成目标时长 = `expected_duration(text)` × `speed` 字段系数；
- 生成后仍以 ffprobe 实测为准写 manifest（不采信模型自报，铁律不动）；
- 时长比落出 DUR_BAND (0.5, 2.0) 的既有门禁不动；新增**软带** [0.9, 1.12]：落进软带外的段落 manifest 标 `dur_drift: true` 并 WARN——**不拦截**，因为它不再是错误，只是给 04 排片的水填压力的预报。软带的数来自 v1 经验：水填双向余量通常能吸收 ±10%，超出后 `short` 率显著上升。**待 M2 实测回填，不许先当门禁用**（S2：先观测，够格再谈卡门）。

### 3.5 回读质检升级：SenseVoice-Small 主读 + Whisper Large-v3 仲裁

现有逻辑（3 次重试 → ASR 盲区豁免 → 整体失败）逐字保留，只换 ASR 通道：

```
回读仲裁(segment audio):
    r1 = sensevoice_small.transcribe(audio)        # 主读：快（≈15× 实时），情感标签白送
    if pinyin_cer(r1) pass: return pass
    r2 = whisper_large_v3.transcribe(audio)        # 仲裁：仅主读失败时启用
    if pinyin_cer(r2) pass: return pass_with_note("sensevoice 误报，whisper 仲裁通过")
    return fail                                    # 进入既有重试/豁免链
```

- **为什么是仲裁不是融合**：两个 ASR 的 CER 不可比（S10 同构——不同模型的错误率不是同一个量），所以是「主读不过才请仲裁」的级联，不是分数混合；
- **仲裁通过要落痕**：`manifest` 记 `asr_arbitrated: true`。若某段仲裁率 >30%，说明主读对该音色系统性失真，触发与 ASR 盲区同源的审查（S4 精神：拿不到能证伪的信息不定罪，但必须可见）；
- SenseVoice 的情感识别输出（happy/sad/…）**只进 manifest 观测**，不进任何门禁——它测的是「模型觉得什么情绪」，不是「念得对不对」（S1：先问测的是不是真实产物）；
- `asr.py` 同步抽象后端表 `{mlx_whisper_local, whisper_large_v3_cuda, sensevoice_cuda}`，本地 Mac 无 GPU 时回读自动走 mlx-whisper——**同一份裁决代码，两处算力**，这是 ADR-0014 解耦的代码层落地。

---

## 四、 v2 视觉语义多模态索引引擎（`pipeline/vindex.py` & `clips.py`）

### 4.1 通道拓扑：三通道不变，画面通道换内核

ADR-0003 的三通道纪律（路由不融合、一段一通道、分数不跨通道）**完整继承**。变化只在画面通道的内部实现：

| 通道 | v1 | v2 |
|---|---|---|
| 锚点直通 | 镜头表吸附（ADR-0008） | **不动** |
| 台词检索 | bge-base-zh × 字幕滑窗 | **不动**（换 bge-m3 的论证不成立：字幕检索中日对齐需求由查询改写承担，不换索引——**除非 M2 探针证明 bge-m3 在真实查询集上 Top-1 命中率显著优于 bge-base，那时走独立 ADR**。不为「更新而更新」浪费重建） |
| 画面语义 | Chinese-CLIP 图文余弦（已证死） | **Qwen3-VL 意象打标 → bge-m3 文本向量 → 文-文检索** |

`场景` 字段语法、`scene_no_match` 按番门槛、两级阶梯（`场景`→`备选`）全部沿用；「当前不可用、写了当场报错」的封印在 captions 索引验收后解除（M2 完成判据之一）。

### 4.2 镜头打标流程（Phase 0 级，按番一次）

```
python -m pipeline.vindex captions <番> <集>     # 云端执行，逐镜头落盘
```

1. **代表帧策略**：`shots.py` 的镜头表与 `frames` 抽取不变。长镜头补帧规则——镜头时长 > 8s 时取 3 帧（25%/50%/75% 位点）多图输入，≤ 8s 单帧（50% 位点）。8s 的来路：ADR-0003 标定中位镜头 3.3~3.5s，8s 是 P95 量级，覆盖推拉摇移的主体。**待实测**：M2 抽 30 个长镜头人看 3 帧 caption 是否显著优于单帧，不显著则退回全单帧（YAGNI）。
2. **Prompt 工程**（受控输出，对齐 ADR-0003「输出限死短结构化标签」的成本杠杆）：

   ```
   系统：你是动画分镜分析师。用不超过 30 个汉字描述这个镜头。
   必须覆盖：时间/空间（场景）、人物动作、光影色调、情绪氛围。
   禁止：评价性词汇（"精美""经典"）、剧情推测、超出画面的信息。
   格式：直接输出描述句，不加任何前缀。
   用户：<镜头代表帧>
   ```

   输出校验：超长/含禁词/空输出 → 降温重试一次；仍败 → 该镜 `caption_status: "failed"` 跳过不定罪（S4），计入六条数字对账的第七行（见 4.4）。
3. **逐镜头落盘** `data/library/vindex/<番>_<集键>.captions.json`，断点续跑靠「已有 caption 的镜头跳过」：

   ```json
   {
     "meta": {"model_id": "Qwen/Qwen3-VL-8B-Instruct", "revision": "<sha>",
              "shots_fingerprint": "<shots.json 元信息指纹>",
              "prompt_version": "v1", "created": "2026-09-15"},
     "captions": [{"shot": 12, "start": 183.4, "end": 187.1,
                   "caption": "夜晚教室，少女伏案独坐，冷白灯光，孤独压抑",
                   "status": "ok"}]
   }
   ```

   `meta` 沿用索引自描述标准（ADR-0003/第七节数据标准），`prompt_version` 是新增的自描述维度——**改 prompt 等于换模型**，版本不对加载硬失败。

### 4.3 向量化与检索

```
python -m pipeline.vindex embed <番>             # captions → bge-m3 向量库
```

- 每镜头一条 bge-m3 密集向量（caption 为文本输入），产物 `data/library/vindex/<番>_scene.npy` + 自描述 meta（model_id=bge-m3, revision, dim=1024）；
- **门槛标定复用成熟方法**：`python -m pipeline.vprobe scene <番> <集>` 原命令不动，但语义变为「零假设组查询打文-文索引」——10 条本片绝不可能有的画面描述查询测噪声地板，真实查询测命中带，取中间值按番写进 `config/scenes.json` 的 `no_match`。这正是 ADR-0003 当初想建建不成的东西：**文-文余弦在本项目有可标定的先例**（subindex 0.45），门槛之死由此解开；
- **时序防连撞与锚点直通零改动**：`_overlaps`、`OVERLAP_GAP`、镜头吸附全在时间与镜头维度上，与检索内核无关；
- **检测不对称纪律平移**：caption 打标可能漏细节（「没提到 X」≠「X 不在场」），场景通道维持软过滤传统——`no_match` 硬失败交 05，不自动塞画面。

### 4.4 六条数字扩展为七条

`vindex status` 的对账表追加一行「意象打标」：captions 文件覆盖的镜头数 == 镜头表镜头数（允许 `failed` 状态存在但必须显式计数，S9 跳过不是通过）。画面通道启用后，Phase 0 完成判据从六条数字相等变为七条。

### 4.5 VLM 成本测算（诚实账本，判断 4）

全番打标是 Phase 0 级一次性成本，不进单期 0.3 元预算：

| 项 | 估算 | 依据 |
|---|---|---|
| 镜头数/季 | ≈ 15,000（41 集 × 370） | ADR-0003 实测 372/集 |
| 单镜头耗时 | ≈ 1~2s（8B FP16，批处理，限长输出） | **待 M1 镜像基准实测**，取自同规格模型公开吞吐区间，动工时以实测替换 |
| 单季打标 | ≈ 4~8 小时 ≈ **9~18 元** | 4090 约 2.2 元/小时 |
| EGOIST 企划池（SP01~08，约 2 小时素材） | ≈ 数百镜头 < 1 元 | 镜头表已建 |

**预算闸**：`config/cloud.json` 的 `budget_per_phase0_cny: 25.0`；单季打标预估超线时 `vindex captions` 要求显式 `--confirm-cost`，不带则拒绝启动——大账单必须是显式动作，对齐 approve 的显式性原则（N5 同源）。

---

## 五、 多层级素材存储、流转与缓存生命周期

### 5.1 存储角色分配

| 层 | 载体 | 角色 | 内容 |
|---|---|---|---|
| L1 工作集 | Samsung T7（`data/` 符号链接现状不动） | 唯一权威副本 | 片源、字幕、索引、镜头表、番剧笔记、BGM、每期产物、模型缓存（本地 mlx 侧） |
| L2 冷备 | Google Drive A（5TB） | 冷备/归档 | 已完结番的片源与 Phase 0 产物打包；`data/library/` 周期快照 |
| L3 冷备 | Google Drive B（5TB） | 异地副本 | L2 的镜像（rclone 双写）；素材搜集的公共中转 |
| L4 算力驻留 | AutoDL 数据盘（随实例持久） | 云端模型仓 + 工作缓存 | 云端模型 hub（约 40GB：IndexTTS2、Qwen3-VL-8B、bge-m3、Whisper-L3、SenseVoice、Real-ESRGAN）；当期上传的稿件/索引；打标帧缓存 |
| L5 易失 | AutoDL 容器系统盘/临时目录 | 会话缓存 | 临时 wav、超分中间帧、解压暂存——**实例关机即弃，任何正式产物不许只落这里** |

### 5.2 数据流转矩阵

| 数据 | 产生 | 去向 | 生命周期判据（重生成要多久） |
|---|---|---|---|
| 片源/SP 素材 | 本地下载/acquire | L1 权威；完结后归档 L2/L3 | 不可重生成 → 永不清 |
| 字幕索引/镜头表/captions/向量库 | Phase 0（云端算） | 回传 L1 权威；L2 快照 | 重生成要几小时 GPU → 不清 |
| 番剧笔记/曲库/音色参考 | 人工+agent | L1 权威，L2 快照 | 不可重生成 → 永不清 |
| `03-audio/` | 云端 TTS | 回传 L1（改稿重渲要用） | 留 |
| `04-clips*.json` / `05-final.mp4` / `06-check.log` / review HTML | 本地 | L1；成片随发布归档 L2 | 产物即状态 → 永久留 |
| 打标代表帧 | 云端从本地 frames 同步 | L4 驻留 | 本地 `shots frames` 几十秒可重抽 → 云端用完可清 |
| 超分中间帧/RIFE 临时 | 云端 enhance | L5 | 秒级~分钟级重生成 → 即清 |
| 云端模型 hub | 镜像首装 | L4 驻留 | 重下 40GB 要数十分钟 → 不清 |

**一致性规则**：L1 是唯一权威，L2/L3/L4 都是它的派生——任何「云端改了而本地没有」的产物状态都不被承认（产物即状态，状态必须可拉回本地查验）。`ava-cloud pull` 是每条云端流水线的**强制收尾**，不是可选项。

### 5.3 传输方案

| 链路 | 工具 | 说明 |
|---|---|---|
| Mac ↔ AutoDL | rsync over SSH（`ava-cloud push/pull` 内部实现） | 增量、校验和、断点续传；走 AutoDL 给的 SSH 端口 |
| Mac ↔ Google Drive | rclone（既有生态，不引新依赖） | `rclone sync` 单向镜像，A/B 两盘串行写；凭据在 rclone 配置（不进 git） |
| AutoDL ↔ Google Drive | **不建** | AutoDL 学术加速不覆盖 GDrive，链路不稳；中转统一经 Mac（判断 2：云端不是数据枢纽） |
| OSS/ossutil | **不引入** | 现有通道够用；新依赖必须有「现有工具做不到的事」才准入 |

---

## 六、 素材自动化搜集与预处理（Phase 0 进化）

### 6.1 `pipeline/acquire.py`（新建）：检索 → 人审 → 抓取 → 门禁 → 入库

自动化的是**检索与初筛**，入库的最后一公里仍是显式动作（对齐「05 显式 approve」「02.5 封板」的人机边界哲学）：

```
python -m pipeline.acquire search "EGOIST 万里行程 演唱会" --type live
    → data/library/incoming/candidates.json   # 候选清单（标题/来源/时长/分辨率/链接）
python -m pipeline.acquire fetch <候选号>      # yt-dlp 抓取 → incoming/
python -m pipeline.acquire gate  <文件>        # 质量门禁 → 报告
python -m pipeline.acquire register <文件> --pool EGOIST --as SP09
    → 走 ingest 既有登记与 intact 校验，落 sources.json
```

**质量门禁判据**（全部机器可判，S1）：

| 检查 | 判据 | 失败处理 |
|---|---|---|
| 完整性 | `ffmpeg -c copy -f null -` 全片解复用无报错（沿用片源判据） | 拒收 |
| 分辨率 | < 1080p 标 `needs_enhance: true`（不拒收——老 Live 只有 720p 是常态，超分通道存在就是为它们） | 标注入库 |
| 音轨 | 无音轨且候选类型为 live/mv → 拒收（音画同源协议的死素材） | 拒收 |
| 去重 | 与既有 SP 池按时长 ±1s + 首帧 dHash 判重 | 拒收并指出撞了谁 |
| 集键 | `--as SPxx` 不与现有登记冲突 | 冲突当场报错 |

**搜集侧纪律**：`acquire search` 只产出清单，不自动下载任何东西——批量抓取的版权与带宽风险由人显式 `fetch` 承担；SerpAPI/种子检索的 agent 驱动流程进 `skills/` 操作手册而非代码硬编码（机制进代码，渠道进手册——渠道三天两头变）。

### 6.2 `pipeline/enhance.py`（新建）：按需超分与补帧

```
python -m pipeline.enhance video <源> --to 1080p     # Real-ESRGAN-Anime6B，云端执行
python -m pipeline.enhance interp <源> --to 60fps    # RIFE，仅扫图微动/低帧率 Live 用
```

- **按需介入，不进热路径**：触发源只有两个——`acquire gate` 的 `needs_enhance` 标记，或 02 写稿时人/agent 显式指定。超分是 Phase 0 级重活（单集数十分钟 GPU），单期预算内绝不跑；
- **产物即新源**：增强产物登记为独立集键（如 `SP09` 原片 → `SP09HD` 增强版），**原片保留不动**——超分是有损再创作，锚点时间码在两份文件间 1:1 成立（时长不变是 enhance 的硬性自检，变了当场 FAIL）；
- **UVR5/Demucs 分轨**：维持施工图 2026-09-10 降级结论——不进核心路径，需要时再单独立项。

---

## 七、 实施路线、分支与模块重构映射

### 7.1 开发隔离

```bash
git worktree add ../anime-video-agent-v2 -b v2-cloud-gpu
```

worktree 内 `data` 符号链接指向**同一外置盘**（产物即状态跨分支连续）；`config/cloud.local.json` 不跨 worktree 复制前先进 `.gitignore` 核对。分支纪律沿用 C4（一个 PR 一件事、超 400 行拆）。

### 7.2 模块映射表

| 文件 | 处置 | 内容 |
|---|---|---|
| `pipeline/cloud.py` | **新建** | 端云协同 CLI（一.2）、生命周期、账簿 |
| `pipeline/eval.py` | **新建** | 评估 harness：freeze/report/diff（二.4） |
| `pipeline/g2p.py` | **新建** | 拼音直注脱敏层（三.2） |
| `pipeline/acquire.py` | **新建** | 素材检索/抓取/门禁/登记（六.1） |
| `pipeline/enhance.py` | **新建** | Real-ESRGAN/RIFE 封装（六.2） |
| `pipeline/tts.py` | **改造** | 新增 indextts2/cosyvoice2 loader；`speakable` 接 `g2p`；情绪/语速字段执行；manifest 增 `emotion/speed/g2p_injections/asr_arbitrated/human_review` 字段；`_MUTE`/回读拼音比对/重试与豁免链**不动** |
| `pipeline/asr.py` | **改造** | 后端表抽象（mlx 本地 / SenseVoice / Whisper-L3 云端），三.5 |
| `pipeline/vindex.py` | **改造** | 新增 `captions`/`embed` 子命令；`status` 六条→七条；CLIP 场景编码路径（`encode_images`/`encode_query`）在 captions 验收后**删除**（红线动作，届时先问人）；tagger 死代码维持 N1 备忘现状 |
| `pipeline/clips.py` | **微改** | 解除 `场景` 封印（场景通道由「不可用报错」改为读 scenes.json 门槛）；分配器/水填/锚点/presence **零改动** |
| `pipeline/check_script.py` | **微改** | 新增 `情绪`/`语速` 受控词表机检一项；其余 21 项不动 |
| `pipeline/shots.py` | **微改** | `frames` 增长镜头 3 帧模式（四.2）；切分/标定不动 |
| `pipeline/render.py` | **不动** | 渲染留本地（判断 2）；ffmpeg 管线、截取守卫、双模态协议（ADR-0013）原样 |
| `pipeline/qc.py` / `review.py` / `align.py` / `timeline.py` / `bgm.py` / `music.py` / `faces.py` / `ingest.py` / `recheck.py` / `cover.py` | **不动** | 门禁、人审关卡、段级不变量、BGM、锚点体系全部是 v2 要继承的资产 |
| `config/voice.json` | **扩展** | emotions/speed/_risky_on_yomi（三.1） |
| `config/cloud.json` + `config/cloud.local.json` | **新建** | 一.2 |
| `config/scenes.json` | **逐番回填** | captions 索引的 `no_match` 门槛（四.3） |
| `docs/adr/0015-*.md`（拟） | **新建** | 「画面通道 = VLM 打标 + 文-文检索」与「渲染留本地」两个难以回头的决策各立一份 ADR（含推翻条件），本 plan 不落 ADR 职责 |

### 7.3 三阶段里程碑

**M1：评估基线 + 云端镜像基准（不动任何生产模块）**

- `eval.py` 落地，`v1-baseline` 两期冻结快照产出（含 2.2 全维度，人评分用 v1 既有反馈回填）；
- AutoDL 镜像定型：PyTorch 2.x + CUDA 12.x + 五个模型预置 L4 数据盘；`doctor` 全绿；
- 基准实测回填：IndexTTS2 整段合成耗时、Qwen3-VL 单镜头打标耗时（替换四.5 的估算）、bge-m3 建库耗时；单期 TTS+回读端到端耗时与花费首测；
- **完成判据**：`eval report --tag v1-baseline` 可产出；`ava-cloud up → run probe → pull → down` 全链路一次跑通且账簿有第一条记录。

**M2：TTS 情感配音 + VLM 视觉索引单点验证（不进生产期）**

- IndexTTS2 probe：同一文本 × 三种情绪声明，人耳确认可区分；`shí六首` 类历史泄漏样本集（从 readings 表转录）在拼音直注层下 100% 念对；
- Qwen3-VL 打标抽验：随机 30 镜头 caption 人看，准确 ≥ 27/30 否则回到 prompt 工程；
- bge-m3 场景库建出 + `vprobe scene` 门槛按番标定 + captions 验收后 `场景` 通道解封；
- **完成判据**：`eval diff v1-baseline m2-probe` 中「韵律」「错字」两项有正向移动；场景通道 Top-5 人抽命中率 ≥ 70%（对齐 ADR-0003 探针惯例）。

**M3：端到端《终局葬礼》成片 + 全量对比**

- 九步全程 v2 链路跑通：云端配音（情绪声明覆盖 ≥ 30% 段落）→ 03.5 结构化打点 → 排片（三通道全开）→ 05 → 本地渲染 → qc 11/11；
- 指标验收：单期 GPU 账单 ≤ 0.3 元、人时 ≤ 10 分钟（超出按 B2 纪律复盘）、`eval diff v1-baseline ep3` 无回退项且韵律/意象贴合正向；
- **完成判据**即施工图的总判据：第三期《终局葬礼》以高质量标准产出发布，v2 分支合并回 main（C3 合并判据全过）。

---

## 八、 明确不做的事（范围闸门）

- **不做** FastAPI/RPC/任务队列/数据库/Web 前端（明令不做事项，本方案无任何例外）；
- **不做** 云端渲染与 NVENC 管线（判断 2：数据引力不成立；若将来批量出片成为常态，单独立 ADR 再议）；
- **不做** UVR5/Demucs 分轨（施工图已降级为可选增强，本方案不拉回）；
- **不换** 台词通道的 bge-base-zh（四.1：除非探针证明 bge-m3 显著更优并单独立 ADR）；
- **不做** 任何「自动评分总分」「跨通道融合分」（S2/S10 判据管辖）；
- **不在 v2 范围**引入 LoRA 微调——零样本克隆先验收，不达标再按施工图兜底条款立项。

---

## 九、 验证命令与测试要求

```bash
# 开工前基线（plans 守则：不绿不开工）
uv run pytest

# M1
python -m pipeline.eval freeze data/episodes/<第一期> --tag v1-baseline
python -m pipeline.eval report --tag v1-baseline
ava-cloud doctor

# M2
python -m pipeline.tts probe "十六首配乐里，她唱完了整个世界"    # 拼音直注冒烟
python -m pipeline.vindex captions EGOIST SP04 && python -m pipeline.vindex embed EGOIST
python -m pipeline.vprobe scene EGOIST SP04
python -m pipeline.vindex status --anime EGOIST                   # 七条数字

# M3（全链路）
python -m pipeline.check_script <本期>/02-script.md
ava-cloud push <本期> && ava-cloud run <本期> tts && ava-cloud pull <本期> && ava-cloud down
python -m pipeline.clips <本期>
python -m pipeline.review <本期>            # 05 人工关卡
python -m pipeline.render <本期> && python -m pipeline.qc <本期>
python -m pipeline.eval freeze <本期> --tag ep3 && python -m pipeline.eval diff v1-baseline ep3
```

**测试要求**（对齐 STANDARD 第八节）：
- 新增纯函数（`g2p.py` 注音选择、`eval.py` 指标计算、`acquire.py` 门禁判重）进 `tests/`，期望值先在真实实现上跑通再写断言，写完做变异检验；
- I/O 边界允许 monkeypatch 观测值注入（既有先例），喂的必须是真实观测值（真实回读文本、真实 ffprobe 输出）；
- 不 mock ffmpeg 与模型——云端媒体正确性由截取守卫与质检门禁在真跑时兜（E7）。

---

## 附：本方案相对上位施工图的三处修正（动工前需人确认）

1. **渲染留本地**（判断 2），施工图拓扑图「04/05 排片与渲染在云端」修正为「排片在云端（轻量），渲染在本地」；
2. **台词通道不换 bge-m3**（四.1），施工图模型矩阵中 `subindex.py → bge-m3` 一行降级为「探针后置决策」；
3. **03.5/05 人审关卡升级为结构化打点**（二.3），在既有两个人工停机点上追加 < 30 秒的数据采集，不改变停机点本身。

三处都触及上位文档既定表述，按「先改文档、再改实践」：本 plan 确认后，同步修订 `2026-09-08-v2-cloud-gpu-reconstruction.md` 对应段落，两处不留分叉。
