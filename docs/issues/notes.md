# 低优先级备忘（Notes）

已接受/已知妥协/触发式，短期不动。当前 13 条。

---

### [N1] WD tagger 是「占位死代码」，只为路径跑通
- 状态：备忘
- 位置：pipeline/vindex.py:90-91；config/project.json:63
- 描述：wd-swinv2-tagger-v3 覆盖春物 3/12 角色、连主角八幡都不在词表里，「拿它做角色过滤
  等于没做」，但故意保留以在 camie 拿不到时跑通代码路径。
- 线索：ADR-0003 实测「词表大的认不准，认得准的词表小」；换真人影视素材时必须整块摘掉
  （ADR-0003 硬要求）。
- 推进：换真人影视素材时触发摘除。

### [N2] tagger 的 general 标签落盘但无任何检索路径读它
- 状态：备忘
- 位置：pipeline/vindex.py:321-324
- 描述：presence 索引顺带存了 booru general 标签（rain/night/indoors），注释明写「当前没有
  任何检索路径读它」「它不是一条通道：没有路由指向它，也不许有」。
- 线索：为「第 2 层探针万一不过时的备用料」存的，推理钱已花、将来要用不必重跑。
- 推进：第 2 层方向重新讨论时（D5）才用得上。

### [N3] shots calibrate `--sheet` 与 `--long` 不能一次给全
- 状态：备忘
- 位置：config/project.json:55；pipeline/shots.py（calibrate 命令）
- 描述：`--sheet` 分支提前 return，两个参数都传只跑 `--sheet`，`--long` 要单独再跑一次。
   config 注释自称「现有 CLI 的已知小瑕疵」。
- 推进：不阻塞，只是命令要跑两次。

### [N4] CoreML 推理路径不可用，退回 CPU
- 状态：备忘
- 位置：pyproject.toml:34-36；pipeline/vindex.py:213
- 描述：onnxruntime CoreML EP 在动漫人脸/CCIP 图上跑不通（节点切碎成几百个分区、运行时报错），
  注释「只用 CPUExecutionProvider」「CPU 0.24s/张已经够用」。
- 推进：CoreML 提速是悬着的未尝试方向，短期不追。

### [N5] ingest phase0 重建索引时跳过 verify 的潜在静默风险
- 状态：备忘
- 位置：pipeline/ingest.py:465-482
- 描述：已登记过的集重建索引时不重跑对轴校验，只在日志标注「重建，未重跑 verify」。
- 线索：有意的（登记过=验证过），但片源被替换后存在静默失效可能。
- 推进：记录时确认此设计是否仍成立。

### [N6] ASR 兜底的同音字错误是已知限制
- 状态：备忘
- 位置：pipeline/asr.py:16-17
- 描述：「已知限制：会出同音字错误（实测"只剩下"→"之剩下"）。语义检索对此鲁棒，但精确文本
  匹配不要依赖 ASR 结果。」
- 推进：已量化、已接受，无修复计划。

### [N7] IndexTTS-2 4.4G 遗留模型 + 自写解码循环删除条件
- 状态：备忘（触发式）
- 位置：docs/adr/0002:60-61, 158-160；pyproject.toml:15-16
- 描述：`data/models/hub/models--mlx-community--IndexTTS-2-fp16` 4.4G 当前无法使用
  （mlx-audio 0.4.6 不支持 IndexTTS-2），等上游支持或腾空间时删。绕开上游 `model.generate()`
  两处 bug 的自写解码循环，上游修好后可删（删前先跑 `tts probe` 对比 CER）。
- 推进：上游支持 / 上游修复时触发。

### [N8] 簇纯度阈值待定 + Phase 0 人工时长待回填
- 状态：备忘
- 位置：docs/adr/0003:270, 274
- 描述：「每簇抽 20 张人看，错一张就拆簇（阈值待定）」；Phase 0「一部番多一次人工（时长
  待实测）」。20 张抽检实际已执行（东京喰种贴错事故后），但正式阈值和总时长没回填。
- 推进：下次跑 Phase 0 时回填实测值。

### [N9] PRESENCE_BAND 换番/换 embedding 模型后要重测；ADR-0004 两条推翻观察项
- 状态：备忘（触发式）
- 位置：docs/adr/0004:106-107, 127-138
- 描述：PRESENCE_BAND=0.06 是 bge-base-zh-v1.5 在这份语料上的噪声性质，换番/换模型要重测。
  ADR-0004 推翻条件：① 集号约束让 no_match 明显变多 → 考虑「该集±相邻集」放宽；
  ② presence 带内排序在 05 人审反复排错 → BAND 收窄或回布尔过滤+漏检垫底。
- 推进：换番/换模型、或出现上述症状时触发。

### [N10] 周复盘没脚本化 + 选题没脚本化
- 状态：备忘
- 位置：docs/WORKFLOW.md:225, 695-697
- 描述：「拉平台数据回喂选题库这一步目前是手动的」；「选题这一步还没脚本化，手写 01-topic.md」。
- 推进：ROADMAP 阶段 2/5 相关。

### [N11] Python 3.15 未验 + 语音 engine 只有 mlx 一种实现
- 状态：备忘
- 位置：README.md:76, 80-81, 161；pyproject.toml:7
- 描述：3.15 还在 beta「没验过」；config/voice.json 留了 engine 字段作接缝「但目前只有 mlx
  一种实现」。Windows 侧（D9）的已知缺口。
- 推进：环境升级或 Windows 化时触发。

### [N12] BGM 素材缺口 + voice readings 换番要清
- 状态：备忘（触发式）
- 位置：config/bgm.json:61, 170；config/voice.json:9
- 描述：东京喰种 unravel/asphyxia、罪恶王冠 CD1 人声单曲的正式 OP/ED instrumental 轨都不在
  素材里，「要用的话得另外找伴奏单曲碟」。voice.json readings 表每番积累，换番要清掉上部词。
- 推进：选曲时 / 换番时触发。

### [N13] review.py「手工」标记改动未提交
- 状态：备忘
- 位置：pipeline/review.py:189-200（工作区，未提交）
- 描述：给 score=None（05 人审手工指定/覆盖）的片段在审图 HTML 里标「手工」、不做分数横比。
  修「人改了画面但界面显示不准确」的补丁，改了一半/未自测。
- 推进：自测后提交（找用户确认）。

### [N14] FACE_EXPAND=1.6 待逐番标定
- 状态：备忘
- 位置：pipeline/faces.py:120-122
- 描述：「1.6 是起点，抽检（`vprobe presence`）发现认混了就回来调，调完要重跑嵌入。」
- 推进：换番 / 抽检出认混时触发。

### [N15] tts.py 的 2–4 分钟成片时长检查硬编码，不走 config 时长目标
- 状态：备忘
- 位置：pipeline/tts.py:726-727
- 描述：`if not 120 <= total <= 240` 硬编码，不读 `01-topic.md` 的 `时长目标` 覆盖——人物志类
  7–8 分钟选题会在此误报 WARN，而 qc.py 已支持 per-episode 覆盖。
- 推进：对齐 qc.py 的做法读 per-episode 时长目标。

### [N16] 春物角色样本少可靠性存疑 + _note 口径与实现不符
- 状态：备忘
- 位置：config/characters.json:6 vs config/project.json:61 + vindex/*.presence.json meta
- 描述：① 春物川崎/叶山/三浦训练样本很少，「可靠性存疑，逐角色抽检见 `vprobe presence`」——
  抽检结论未回填；② `_note` 按 camie tagger 口径写（27021 角色标签），但实际 presence 索引
  producer 已是 ccip（faces 聚类），文档误导贴名决策。
- 推进：回填抽检结果；重写 `_note` 使其与实际通道一致。

### [N17] 三番合计 239 个角色簇未贴名（D11 的量化）
- 状态：备忘
- 位置：vindex/春物.clusters.json / 东京喰种.clusters.json / 罪恶王冠.clusters.json
- 描述：春物 24/64 已贴名、40 簇未贴；东京喰种 114/287、173 簇未贴；罪恶王冠 45/71、26 簇未贴。
  合计 239 个簇没有名字，角色过滤覆盖不到它们。D11 只说「逐步补」，这里量化了规模。
- 推进：与 D11 一起，贴名完成度要有个「何时算做完」的判定。

### [N18] ADR-0003「过切与漏切代价不对称要写进判据」未落地
- 状态：备忘
- 位置：docs/adr/0003:334 vs 仓库根 CLAUDE.md（判据文档全文无 scdet/往低取内容）
- 描述：ADR 明写「过切与漏切的代价不对称，这条要写进判据……拿不准往低取」，但 CLAUDE.md
  判据十条里没有任何对应条目——违反项目「判据要么被执行，要么被删除」纪律。
- 推进：写进 CLAUDE.md 判据，或从 ADR 里删掉这句声明。

### [N19] cpm 换音色/换题材要重测（触发式）
- 状态：备忘
- 位置：config/project.json:49；docs/WORKFLOW.md:218-219
- 描述：`cpm=315` 是「校准到该账号实际语速」，实测同一音色不同文风可差 24%；换音色/换题材
  要重测。cpm 错 → 字数带错 → 时长目标错 → 质检误判，且静默。
- 推进：换音色/换题材时触发。

### [N20] 触发式推翻条件未单独立档（结构性取舍）
- 状态：备忘
- 位置：ADR-0002:123 / 0003:482-493 / 0004:127-138 / 0005:123-130, 218-222
- 描述：各 ADR 的「什么情况下推翻本记录」「句尾机械声裁剪判据是启发式」「过切漏切代价」等
  触发式 what-if 多只在库里挂名或作线索存在，未单独立档。这是库的结构性取舍，不一定是遗漏。
- 推进：若想完整追踪「推翻条件」，把每篇 ADR 的推翻段抽成一条备忘；否则维持现状。

### [N21] 文档维护琐碎项集合
- 状态：备忘
- 位置：scenes.json:4 / bgm.json:2 / SHOTLIST.md:3 / BASELINE.md:46-48 / voice.json:7 / VOICE.local.md
- 描述：① scenes.json 引用已删除的 `_anchor_note` 键（commit 56361a1 后悬空）；② bgm.json 头部
  `_note` 仍写「一部番固定 3–5 首」，与 CLAUDE.md 2026-08-10「任意首数拼接」冲突；③ SHOTLIST 写
  「第 10 步」但 SKILL 是第 8 节标分镜（编号断裂）；④ BASELINE 知乎 3 篇样本「超 45 字占比」列
  缺测；⑤ voice.json `ref_text: null` 无 why 注释；⑥ VOICE.local 移植表缺「平台专属梗」行、
  两条特征缺原话锚。
- 推进：下次动对应文件时顺手修，不值得单独立项。

### [N22] 回读质检盲区：TTS 念错但 Whisper 回读恰好也认成同一个错字
- 状态：备忘
- 位置：config/voice.json:9（readings 机制前提）+ CLAUDE.md「配音回读质检」
- 描述：能发现念错靠的是回读 CER 不认；若回读也认成同一个错字，此机制静默失效（无第二道防线）。
  推测性盲区，尚未实测踩到。
- 推进：踩到时再处理，不提前立防线。

---

> 附：画面语义通道（`_ladder_scene`/`scene_no_match`/vindex 语义 search）是「摆好待启用」的
> 死代码，非 bug，启用条件 = 重新过探针（N2/D5 联动）。qc.py 的「跳过不是通过」SKIP 纪律、
> clips.py 的 `no_match` 硬失败终态，都是已决策设计，不算问题，不建条目。
