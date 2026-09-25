# Issues：anime-video-agent 未解决问题单表

> **查问题先读这张表。** 已解决的条目归档到 [`archive.md`](archive.md)，不再删除。
> 所有 `B*` / `D*` / `N*` 编号以本表为活跃索引；ADR 与 plans 通过「关联」列回指。

---

## 活跃问题总表

| 编号 | 状态 | 一句话 | 位置 | 关联 | 推进 / 线索 |
|---|---|---|---|---|---|
| B1 | 已解决（M2b 交付） | 排片错配修复方案选定与落地 | `docs/adr/0005:4-6` | ADR-0005, ADR-0015, P1, v2架构方案 | Qwen3-VL 镜头意象打标 + bge-m3 文-文检索已交付（ADR-0015，commit `b257e6d`），EGOIST 池解封 |
| B2 | 瓶颈转移 | ≤10 分钟止损线：写稿突破，瓶颈转移至配音顺听 | `WORKFLOW.md` | P4, ADR-0014, v2重构方案 | 2026-09-18 实测更新：写稿主力切至 Gemini 后，02.5 人工改稿耗时已降至 ≤10 min（10分钟内视频），写稿侧瓶颈解除；耗时阻碍转移至 03.5 配音顺听人耳挑错重配（见 ava-agent 施工图） |
| B3 | 已被实践否证 | 该口径已被实践否证（2026-09-19）：人类时间 ∝ 成片时长，20 分钟片的光 03.5 就 ≥30 分钟；读数已接线（§2.6），待积累 k 实测值 | `ROADMAP.md:30,146` | ROADMAP 闸门 A | 预算模型修正为 k × 片长（v1.20）；human_time.json 记账就绪，待积累数据估算 k |
| D1 | 待决策 | ADR-0005 候选复核四子题未定 | `docs/adr/0005:111-121` | ADR-0005, B1 | 接在哪 / 不通过算什么 / 判据 10 是否适用 / 探针成本；等探针结论 |
| D2 | 待决策 | 无台词画面的排片死角——02 强制确认机制未落实 | `docs/adr/0005:91-98` | ADR-0005, ADR-0008, v2重构方案 | ADR-0008 锚点强制字段已垫底；v2 云端 Qwen2-VL 逐镜头意象打标彻底消灭无台词死角 |
| D4 | 已验收（M2b 交付） | 视觉索引第 3 层 VLM 落地 | `docs/adr/0003:38-39,95-141` | ADR-0003, ADR-0015, ADR-0014, v2架构方案 | ADR-0015 的 VLM 意象打标 + 文-文检索已验收合入（M2b） |
| D5 | 已验收（M2b 交付） | 第 2 层（画面语义）复活落地 | `docs/adr/0003:431-433` | ADR-0003, ADR-0015, v2架构方案 | 以 Qwen3-VL captions + bge-m3 文-文检索实现，CLIP 余弦正式废弃，M2b 已对已标定池解封 |
| D6 | 待决策 | 排片错配率完成判据还没测 | `docs/adr/0003:478` | ADR-0003, ADR-0008, P1, v2重构方案 | 量化通道已建；v2 引入 VLM 意象检索后重测错配率 |
| D7 | 待决策（低风险） | 次回预告仍在索引里未滤除 | `pipeline/subindex.py:66-96` | — | 约占 2%，按 style 过滤即可，未做 |
| D8 | 待决策 | 纯中文字幕集数过不了对轴校验 | `WORKFLOW.md:685-688` | ADR-0007 | ASR 兜底转录 或 放弃那几集，未决定 |
| D9 | 待决策 | Windows 跑通九步 + API 接入 + 共享 fixture 未做 | `ROADMAP.md:48-113` | ROADMAP 阶段 1/2 | 依赖闸门 A（B3）；fixture 未建 |
| D10 | 待决策 | ROADMAP 核心假设未验证 + GUI 边界未答 | `ROADMAP.md:211-219,176-195` | ROADMAP 闸门 D | 骨架通用性、两人不分叉、GUI 给谁用 / 审阅还是编辑 |
| D11 | 待决策 | 角色贴名未完成 + 「佑」显示名待核对 | `config/characters.json:23,51,65,97` | — | 东京喰种 / 罪恶王冠贴名进度未标完成；用 subindex 核「佑」显示名 |
| D12 | 待决策 | BASELINE 语气词密度判据挂起 | `skills/write-script/BASELINE.md:80` | P4 | 口语体样本仅 1 篇；P4 E2 已建 diff 对样本积累机制，≥4 期后决定 |
| D13 | 待验证 | 对抗性审查三步流水线已实战一轮，待新番验证 | `WORKFLOW.md`「番剧笔记」 | — | 罪恶王冠大修实测有效；下一部新番从零建笔记时复盘 |
| D18 | 待决策 | 集号 / 人物字段自觉性缺口导致错配静默发生 | `pipeline/check_script.py` | ADR-0005, ADR-0008, B1 | **2026-08-27 更新**：集号侧已由 `锚点:` 强制字段 + 集/锚点一致性机检覆盖（check_script 与 clips.parse_shots 双卡口）；`人物:` 该写没写仍无判据，保留 |
| D19 | 待决策 | 说话人确认是全流程最不可靠环节 | `skills/write-script/SKILL.md:54` | — | 字幕 Name 字段基本不填，02 写稿侧仍无机器判据 |
| D22 | 已定案（ADR-0015），v2 推进中 | 泛素材多模态漏斗检索（Top-K 候选轻量视觉意象过滤）待探针设计 | `docs/adr/0005` | B1, D1, ADR-0015, v2架构方案 | 全镜头 VLM 打标取代漏斗粗滤设计；漏斗复核子题（D1）范围收窄到台词检索段 |
| D23 | 已定案（M2a 落地） | 配音顺听人机摩擦力大 | `pipeline/tts.py` | 四阶段工序卡, ADR-0006, ADR-0016, ADR-0017 | 云端 Qwen3-TTS + SenseVoice/Whisper 仲裁已入库（ADR-0017）；结构化打点机制就绪 |
| D24 | 待决策 | 数百 GB 海量素材增量索引构建、代表帧缓存管理与跨挂载点迁移机制缺失 | `pipeline/ingest.py` | ADR-0012, Local-First | 素材库向 TB 级扩张，缺少全量 vs 增量 hash 变更检测与智能缓存淘汰，跨设备挂载需保证便携性 |
| D25 | 已落地（M2a 交付） | readings 替换表边际成本极高，需将 CJK 音读泄漏收敛为拼音直注脱敏机制 | `config/voice.json`, `pipeline/tts.py` | ADR-0006, ADR-0014, v2架构方案 | `pipeline/g2p.py` 拼音直注脱敏层已交付；readings 表已降级为个例 override |
| N1 | 备忘 | WD tagger 是占位死代码 | `pipeline/vindex.py:90-91` | ADR-0003 | 词表覆盖率低但故意保留以跑通路径；换真人影视时整块摘除 |
| N2 | 备忘 | tagger general 标签落盘但无检索路径读它 | `pipeline/vindex.py:321-324` | ADR-0003 | 为第 2 层万一复活留的备用料 |
| N4 | 备忘 | CoreML 推理路径不可用，退回 CPU | `pyproject.toml:34-36` | — | onnxruntime CoreML EP 在动漫图上报错，CPU 够用 |
| N5 | 备忘 | ingest phase0 重建索引时跳过 verify 的静默风险 | `pipeline/ingest.py:465-482` | — | 已登记过的集不重跑 verify；片源被替换后可能静默失效 |
| N6 | 备忘 | ASR 兜底同音字错误是已知限制 | `pipeline/asr.py:16-17` | — | 语义检索鲁棒，精确文本匹配不要依赖 ASR |
| N8 | 备忘 | 簇纯度阈值待定 + Phase 0 人工时长待回填 | `docs/adr/0003:270,274` | ADR-0003 | 20 张抽检已执行，正式阈值和总时长未回填 |
| N9 | 备忘 | PRESENCE_BAND 换番/换模型要重测 | `docs/adr/0004:106-107,127-138` | ADR-0004 | 0.06 是本番语料噪声性质；推翻条件待观察 |
| D26 | 待决策（已拍板暂缓） | 语速调整是安慰剂：speed 系数从未进合成链 | `pipeline/tts.py:1351`（`_spd_coef` 未消费） | ava-impl-spec §3.1 (R2) | 2026-09-18 用户拍板：/voice 纠错对「太快/太慢」明确报错不假装能修；真做 = 引擎参数或 atempo 后处理 + 段时长变 → `_stale_downstream` → clips --refit 联动，独立提案 |
| N11 | 备忘 | 图片形态补料三堵硬墙（_parse_key / candidate duration=0 / render ffprobe jpg） | `pipeline/vindex.py:674`, `pipeline/clips.py:340`, `pipeline/render.py` | ava-impl-spec §4.1 (R5) | 2026-09-18 拍板 PR3 视频-only；图片走 `ffmpeg -loop 1` 转视频兜底；真图片形态（虚构 duration + render -loop 分支）是独立提案 |
| N10 | 备忘 | 周复盘 + 选题没脚本化 | `WORKFLOW.md:225,695-697` | ROADMAP 阶段 2/5 | 手写 `01-topic.md`、拉平台数据手动 |
| N11 | 备忘 | Python 3.15 未验 + 语音 engine 接缝只 mlx 实现 | `README.md:76,80-81` | ADR-0006 | Qwen3-TTS 已用接缝；Windows 侧未补 |
| N12 | 备忘 | BGM 素材缺口 + voice readings 换番要清 | `config/bgm.json:61,170` | — | 伴奏单曲缺；readings 表每番积累需清理 |
| N14 | 备忘 | FACE_EXPAND=1.6 待逐番标定 | `pipeline/faces.py:120-122` | ADR-0003 | 起点值，抽检认混时调 |
| N16 | 备忘 | 春物角色样本少 + _note 口径与实际通道不符 | `config/characters.json:6` | ADR-0003 | 川崎/叶山/三浦样本少；`_note` 仍按 camie 口径写，实际已是 ccip |
| N17 | 备忘 | 三番合计 239 个角色簇未贴名 | `vindex/*.clusters.json` | D11 | 春物 40 / 东京喰种 173 / 罪恶王冠 26 簇无名字 |
| N19 | 备忘 | cpm 换音色/换题材/换引擎要重测 | `config/project.json:49` | ADR-0006 | 同一音色不同文风可差 24%；Qwen3 语速待实测 |
| N20 | 备忘 | 触发式推翻条件未单独立档 | `ADR-0002/0003/0004/0005` | — | 各 ADR 的 what-if 分散在库里；是否抽成备忘是结构性取舍 |
| N21 | 备忘 | 文档维护琐碎项集合（部分已修） | `BASELINE.md:46-48` | — | ④ 知乎 3 篇超 45 字占比缺测；⑥ VOICE.local 移植表缺项；①②③⑤ 已修 |
| N22 | 备忘 | 回读质检盲区：TTS 念错但 Whisper 回读也认成同一个错字 | `config/voice.json:9` | ADR-0006 | 推测性盲区；英日歌名区段单独豁免是处理方向 |
| N23 | 备忘/纪律 | 严禁在仓库根目录落地临时脚本 | `AGENTS.md` | — | 2026-08-26 用户严厉指出；单期测试必须在 `data/episodes/<本期>/` 或通过 CLI 完成 |
| N24 | 备忘 | 剧场版/长篇剧情场景实体核验 | `data/library/notes/天气之子.md:386` | — | 2026-08-26 实测：「审讯室」实为「警车后座」；02 与 02.8 加强核验 |
| N25 | 备忘 | 歌词混入 JPN 对白轨且无独立 style（君名本版片源），style 过滤对其无效 | `pipeline/subindex.py:67-82` | — | 2026-09-01 Phase 0 绕过；下部电影/下版片源会再踩 |
| N28 | 备忘 | 非终端信号（`kill -INT <ava pid>`）下 ffmpeg 孙进程存活并继续写输出 | `pipeline/agent/tools.py::run_pipeline` | ava-impl-spec §3.3, 变异 M21 | 2026-09-20 终审登记：**终端 Ctrl-C 不成立**（SIGINT 发给整个前台进程组，且 render.py 的 `subprocess.run` 中断时自 kill）。堵法 `Popen(start_new_session=True)` + 中断时 `os.killpg(SIGKILL)`，代价是子进程从此不再收终端 Ctrl-C、中断唯一入口变成我们的处理器，须拿真渲染复验（实测忠实版中断耗时 0.26s，`4.27s` 那个数字是造了持管道的孙进程所致，已撤回） |
| N27 | 备忘 | 夏隧的 scene_threshold=10.0 是沿用值，标定实录缺失 | `config/project.json` visual | ADR-0003 | 2026-09-05 审计发现；伪恋已于当日补标定销号（维持 10.0，实录见 config 注记）；夏隧仅 1 集镜头表，补跑 calibrate 即可 |
| D27 | 已解决（实施与变异全量通过） | 立项前工作无入口：ava 启动形态缺无期选题会话 | `pipeline/agent/cli.py`（`create_new_episode` / `select_episode_interactive` / `main`） | ava-entry-idea-scope 方案, impl-spec §2.3 | 需求交接 2026-09-20（容器先于内容的顺序倒置）；2026-09-21 实施落地，15 组变异全杀闭环 |
| D28 | 待决策 | 工具循环 10 轮上限对网络调研偏少：被墙或绕路时 10 轮耗尽也拿不到有效信息 | `pipeline/agent/llm.py:33`（`DEFAULT_MAX_ITERATIONS`）、`pipeline/agent/tools.py::_tool_web_fetch` | Spec 4, impl-spec B3-r5 | 2026-09-24 S20 门禁 8 冒烟实测：asset 抓 bgm 一色彩羽，搜索页撞游客登录墙（200 但只有导航栏），随后 9 次 fetch 在作品页与 API 间绕路，没去能直接抓到简介的 `/character/26090` 就耗尽 10 轮。10 这个数本身也没写依据。2026-09-24 用户裁决：**不设固定轮数上限**，停止前必须先做无工具收尾总结，防失控改用「人随时中断 + 完全相同的调用拒绝执行」（归二期 Spec 9）；不靠人指路，改为让模型拿到更好的信息：web_fetch 返回页内链接清单，scope 提示写通用研究策略，站点经验进 memory.md（归二期 Spec 13）；crawl/browser 的 extras 由人安装。到顶回显工具原始 JSON 是另一个 bug（`llm.py:365` 的 `final=convo[-1]`），已交 S25 |
| N29 | 待修（已排插单 session） | 镜头画廊「复制锚点」按钮点不动：`json.dumps` 的双引号直接塞进双引号 `onclick` 属性，浏览器解析出的属性值只剩 `cp(this, ` | `pipeline/shots.py` `gallery()` 的 `onclick="cp(this, {json.dumps(anchor, …)})"` 一行 | Spec 8 §2.7 RF-7 | 2026-09-24 S21 施工发现（桌面端 e2e 夹具已按修好后的形态绕开）；修法为该处加 `html.escape`，经用户同意排为 M11 提交后、S23 前的独立插单 session（不并入 M11：TC-4 要求 `pipeline/` 零改动）；data/ 下已有画廊需重生成，批量还是按需由人定 |

---

## 维护规则

1. **活跃区只留本表。** 新增问题直接追加一行；状态变化只改本表对应列。
2. **解决即归档。** 条目解决后：迁移到 `archive.md`，本表删除，编号不回填。
3. **编号不回填。** 删掉 B1 后 B2 不变号，避免改一堆引用。
4. **ADR / plans 与本表互指：** ADR 用 frontmatter `related-issues` 回指；plans 正文第一节写明对应编号。
