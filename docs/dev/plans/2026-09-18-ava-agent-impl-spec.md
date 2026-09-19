# Implementation Spec：ava 统一 CLI Agent Harness

日期：2026-09-19（**v1.20**，第二十轮人类预算口径更正；版本号由
`tests/test_docs_invariants.py` 断言与状态行一致——B1-r19）
上位文档：`docs/dev/plans/2026-09-18-ava-agent-harness.md`（施工图）
状态：**v1.20 已评审，开工**

> **后继扩展注记（2026-09-20）**：本 Spec 的默认交互层（原将 AI 约束于 `/chat`、`/script` 子循环）
> 已由后继 Spec [`2026-09-20-ava-ai-native-director-spec.md`](2026-09-20-ava-ai-native-director-spec.md)（v1.4）
> 扩展为默认 AI 制片总监连续对话。该扩展是**后继扩展而非修订**：本 Spec 定义的 scope 模型、
> 护栏矩阵与 PR0–PR4 交付物全部继续有效。
>
> **例外清单补登（🔵 终审）**：除 §6 铁律 2 已点名的 `status.py:62` 措辞修正外，
> 后续 PR 另动过一处 `pipeline/`：`review.py:331` 的补丁段二次确认提示由
> `print(prompt, end="")` 改为 `print(prompt, end="", flush=True)`（PR6 Popen 化后
> 走管子，不 flush 则提示符在人等待输入前不可见）。语义零变化、无行为分叉，
> 但严格说属例外清单外，在此补登以免铁律 2 的账目失准。

> **v1.20 修订来源**：第二十轮修订（人类预算口径更正，2026-09-19 用户以约 20 期
> 实践否证原「每期人类 ≤ 10 分钟」假设——人类时间 ∝ 成片时长，改按预算 = k × 片长核算；
> §2.6 止损判据同步调整，CLAUDE.md:50/AGENTS.md:50 与 issue B3 同步更正）。

> **v1.19 修订来源**：第十九轮红队（0🔴 + 2🟡 + 3🔵，全收，第九层：**产品验收**
> ——不再产出正确性缺陷，只产出「你无法证明自己成功」）。
> Y1：唯一活着的止损线「每期人类 ≤ 10 分钟」没有读数（项目 issue B3 就挂着
> 「从未端到端验证过」）——新增 §2.6 人类耗时记账。Y2：只写了「密钥不出网」，
> 而 creative scope 会把期目录与 `data/library/` 笔记整段发往第三方 LLM 端点——
> **出网边界没写**。B1：头部版本号落后状态行 15 版（已接进不变量测试）。

> **v1.18 修订来源**：第十八轮红队（1🔴 + 1🟡 + 4🔵，全收，第八层：**施工序与文件归属**
> ——前七层问「spec 自洽吗/符合宪章吗」，这层问「这份 spec 明天能照着往下走吗」）。
> R1：PR1 的验收项（`write_episode_file` 越界、`/run` 与 asset 白名单、`extra_args`
> 值域）全部依赖 `tools.py` 与 `config/agent/**`，而它们被排在 PR4——PR1 交不出自己
> 写下的验收项，两条架构护栏在真正会写产物的 PR2/PR3 期间**没有代码载体**。
> Y1：PR1–PR4 零工时估计，而总闸把每个 PR 都钉死——「PR4 能不能砍」文档答不上。

> **v1.17 修订来源**：第十七轮红队（1🔴 + 1🟡 + 4🔵，全收，第七层：**项目自己的成文
> 纪律**——前六层问「spec 自洽吗」，这层问「spec 符合这个仓库的宪章吗」）。
> R1：改合成链却全文零提 `SYNTH_LOGIC_VERSION`，而 tts.py:1451 写着「**任何改变
> 合成输出的代码改动都必须 bump 它**」（被 2026-09-11 的 27 段混血音频事故换来的）。
> Y1：STANDARD.md 有 ADR 标准（「难以回头的决策」「必写推翻条件」），而 ava 引进了
> 至少两条这样的决策，交付清单里没有 ADR 项。

> **v1.16 修订来源**：第十六轮红队（0🔴 + 2🟡 + 4🔵，全收，第六层：写者与读者的并发）。
> `atomic_write` 是 tmp + `os.replace`，所以**读者安全；写者之间零防护**（全库无锁）。
> Y1：`--apply-patch` 是分钟级长任务而每次进度落盘是**整份重写**，期间任何第二个写者
> （另一窗口的 `/voice`、另一 checkout 的 ava——而 §1.4 的双 checkout 共享 data
> 正是本设计邀请来的）的新条目会被**静默抹掉**；id 分配同样会撞。
> Y2：attic 修剪是**静默删除**——剪的可能是某段唯一的回滚退路（与 r14 刚立的
> 「隐藏要可见」同族，只执行了一半）。

> **v1.15 修订来源**：第十五轮红队（1🔴 + 2🟡 + 4🔵，全收，第五层：验收矩阵的成本
> 与失败态 + 前门常量与分支代码的连通性）。R1：captions 的「专用分支」是**死代码**
> ——`ALLOWED_TASKS = {"tts", "probe"}` 而 `cmd_run:1173` **先过白名单再分发**，
> 永远走不到分支；spec 那句「不是往集合里加字符串」把正确补法劝退了一半。
> Y1：PR3 的云端验收约 ¥0.4–0.85，是单期预算（¥0.35）的 1.1–2.4 倍，而预算闸只数
> 生成秒数（¥0.063）。Y2：`cmd_pull` 无条件打 `[OK] pull 完成` + 退出码 0，
> 实例关机/任务未完/就绪三种状态输出同形。

> **v1.14 修订来源**：第十四轮红队（0🔴 + 2🟡 + 4🔵，全收，矛头对准 §5 验证矩阵
> 的可执行性）。Y1：PR3 的验证命令按 spec 写**必然撞墙**——cloud 的
> `resolve_episode_rel_path` 硬拒仓库外路径（既有测试 `test_cloud.py:380` 钉着），
> `/tmp` 副本推不上云。Y2：新的不变量测试只有反向断言（删掉文档即全绿），
> 且变体组里的 `--redo` 会误报（它是现行合法机制）。

> **v1.13 修订来源**：第十三轮红队（1🔴 + 2🟡 + 4🔵，全收，矛头对准 v1.12 刚加的
> 验收判据自身）。R1：`grep "rm seg"` 是**字面判据**，漏掉第三本手册
> `docs/runbook/03-tts.md:21`（它写的是中文「删除对应 `seg-XX.wav`」）——PR0 全做对
> 之后判据亮绿灯而一本活的旧手册原样留着；同一文件还有第二处不存在的 `ava-cloud`
> 别名。Y1：判据**自指**（spec 自身 :9/:1036 含该字面串），实现者只能删落痕或放宽
> 排除项。Y2：镜像检查是**一次性动作**，而 STANDARD.md 自己记着 2026-08-21 那类漂移。

> **v1.12 修订来源**：第十二轮红队（1🔴 + 2🟡 + 3🔵，全收，换个角度：文档 SSOT 与
> 人类时间预算）。R1：`CLAUDE.md`（项目级 SSOT，逐字镜像到 `AGENTS.md`）第 235 行
> 教的是与 §3 平行的第二条纠错路（`rm seg-XX.wav` + 改全局注音表），而期级 overlay
> 对同名键优先——照 SSOT 手册做会静默失效；同处的 `ava-cloud push/run/pull` 在
> pyproject 里根本没有对应别名。Y1：止损线是「每期人类 ≤ 10 分钟」，而「听」全量
> 顺听把 03.5 从 2 分钟变成 ~7 分钟。Y2：五处导航仍写旧 03.5 语义。

> **v1.11 修订来源**：第十一轮红队（1🔴 + 1🟡 + 4🔵，全收，逐条对着 pyproject.toml /
> uv.lock 核过）。R1：`uv sync` 不装 extras（pytest 与 mlx 都在 optional-dependencies），
> 搭出来的 worktree 跑不了总闸也跑不了 PR2 真跑验证。Y1：仓库已有 14 个 `ava-*` 脚本，
> 新入口与 /run 的实现基准（模块 vs 脚本）没选。

> **v1.10 修订来源**：第十轮红队（1🔴 + 2🟡 + 3🔵，全收，且逐条对着 cloud.py /
> faces.py 源码核过）。R1：asset 白名单里的 `cloud exec` 是不接受任何值域的任意
> 远端 shell 逃逸口，与 spec 自己「远端 shell 是真注入面所以要严校验」的声明矛盾。
> Y1：Y2-r9 指的三个「既有能力」两个不能用（status 不印 tmux、心跳被 watchdog 代为
> 刷新——是个会答「活着」的假判据）。Y2：faces 只列了 review 半环，compute 半环缺失。

> **v1.9 修订来源**：第九轮红队（0🔴 + 2🟡 + 3🔵，全收）。Y1：asset scope 的值域
> 不能复用窄正则（路径类值会被误杀，asset 又变空壳）；Y2：两段式打标缺「远端死任务」
> 分支，就绪检测要看生产者不只产物。

> **v1.8 修订来源**：第八轮红队（1🔴 + 2🟡 + 3🔵，全收）。R1 是 Spec 文档自身的
> 回归：v1.0 的「手改检测交人定」在 v1.2 重写挂起流程时丢失——自动重跑 clips 前
> 必须段级 diff，rescue-only 保证非失败段逐字节不变，差异段只剩三种成因
> （补丁填充/rescue 触及/手改被冲），第三种列出段号停下来交人定。

> **v1.7 修订来源**：第七轮红队（0🔴 + 3🟡 + 6🔵，全收）。Y1 锚点 short 段排除出
> 补位；Y2 /voice 指令表认小数段号；Y3 tools.json 的 pipeline 表消费者与 asset
> scope 显式落位。

> **v1.6 修订来源**：第六轮红队（0🔴 + 2🟡 + 5🔵，全收）。结构性问题五轮清零后，
> 本轮剩下报账一致性（Y1 pin 轴）与测试清单口径（Y2）两处文本级修复。

> **v1.5 修订来源**：第五轮红队（1🔴 R1 redo 语义两读 + 2🟡 + 8🔵，全收）。
> 结论复述：结构性问题清零，本轮全是接线层与文档层定点修。

> **v1.4 修订来源**：第四轮红队（2🔴 + 2🟡 + 4🔵，全部验伤属实）。
> R1：short 补尾不可调 `size()`——它 pop 掉 limit/span/floor 且水填重排老片段，
> 与「逐字节保留」断言数学互斥；改为 post-size 手工追加。R2：global 条目
> 「首段即标 applied」制造假完成状态；改为 affected/done_segments 进度模型。

> **v1.3 修订来源**：第三轮红队（3🔴 + 6🟡 + 7🔵，全部验伤属实，其中 R3 边界一
> 在 tests/test_render.py:349/374 实证：既有 fixture 只写 approved 不写 04-clips.json）。
> R1 rescue-B「重置 hits」会丢掉 short 段已分配的主池片段；R2 锚点第五堵墙
> （pools 组装把补丁池拖进 load_sources_multi）；R3 mtime 硬闸三边界。
> 另有 v1.2 修法自身的两处回归（Y1 段号、Y3 stale 误杀）一并修复。

> **v1.2 修订来源**：第二轮红队（4🔴 N1–N4 + 6🟡 + 7🔵，全部验伤属实）。
> N1/N4 是 Spec 自家文本自相矛盾（正则杀自家示例、锚点语法写错）；
> N2/N3 是 design sketch 在真实控制流里走不通（rescue 插入点/allow 集合/live 时机、
> 多句段 label 归一）。修复落在 §1.2/§2.2/§2.4/§3.2/§3.4/§3.5/§4.2/§4.4/§5。

> **用户拍板落痕（2026-09-18）**：① R2 砍语速调整——/voice 对「太快/太慢」明确报错，
> 真机制（atempo + refit 联动）记账为 issue D26，以后再做；② R5 PR3 视频-only，
> 图片走 ffmpeg 转换兜底，真图片形态记账为 N11；③ 补丁池门槛判决采纳——pool.json
> 自带 `no_match` 字段 + 三闸（零向量报账 / score+floor 落盘 / 补丁段 approve 二次确认）。

> **v1.1 修订来源**：2026-09-18 红队 Review 报告（9🔴/11🟡/9🔵）。验伤结论：
> 🔴 全部属实（R1–R9），🟡/🔵 除 B1 前提一半不成立外全部接受。
> 本版每条结构性修复在文中以 `(R1)`…`(B8)` 标注出处。
>
> **与施工图的偏差**：施工图 §3 的顺听纠错是 review.html 网页方案，本次红线已改为
> 纯 CLI（QuickTime/afplay 顺听 + 终端纠错）。本 Spec §3 取代施工图 §3；
> **施工图 §3/§7 的修订已前置为 PR0**（原排 PR4 是自相矛盾：先改文档再改实践）(B8)。

---

## 0. 架构立场（不可推翻）

ava 不是在通用 Coding Agent 上套 prompt 的玩具，而是**借 Coding Agent 的手脚、长自己的大脑**：

1. **借手脚**：原生 subprocess 执行、原子落盘（`paths.atomic_write`）、机检自愈闭环。
2. **三条领域护栏，全部落在 harness 层、可测试**：
   - **Code Freeze**：制片会话内 `pipeline/` 绝对只读。ava 的 LLM 工具表里不存在
     写源码的工具；启动与每次执行管线命令前跑 `git status --porcelain -- pipeline/`，
     脏则 WARN。引擎要改 = 退出制片会话，那是开发会话（worktree，见 §1.4）。
   - **配音纪律**：ava 永不生成 `--force`/`--force-all`；重配只走 `--redo`/`--apply-patch`。
     这道闸同时在两层执行：ava /run 白名单拒收，cloud `extra_args` 参数白名单拒收 (R9c)。
   - **封面标题**：只出候选，工具表里没有「定稿」动作。
3. **分阶段注意力特化**：每 Scope 一份 system prompt + 工具白名单
   （`config/agent/scopes/*.md` + `config/agent/tools.json`）。
4. **制片语义层**：`pipeline/corrections.py` 是「制片语言 → 机械动作」的唯一翻译器。

---

## 1. 模块边界与目录设计

### 1.1 新增文件（零新依赖）

```
pipeline/
  agent/                      # ava 宿主（只许依赖 pipeline.*，反向依赖禁止）
    __init__.py
    cli.py                    # ava 入口、REPL、斜杠路由、根目录看板
    resolver.py               # StateResolver：status.inspect_episode 的 scope 化封装
    scopes.py                 # Scope 加载：system prompt + 工具白名单
    llm.py                    # 纯 stdlib（urllib）OpenAI-compatible 客户端（§2.5）
    tools.py                  # LLM 工具注册表 → pipeline 子命令（白名单制）
  corrections.py              # 【顶层 pipeline 模块】顺听纠错：解析 + 落盘 + overlay 推导
  ingest_patch.py             # 【顶层 pipeline 模块】单期补料编排（视频 only，§4.1）
config/
  agent.json                  # {"base_url", "model", "api_key_env"} —— 密钥只走环境变量
  agent/scopes/{creative,pipeline}.md
  agent/tools.json
tests/
  test_agent_resolver.py / test_agent_cli.py / test_corrections.py / test_ingest_patch.py
docs/runbook/03.5-voice-check.md   # 改写：顺听 + ava /voice 流程（含 corrections.json 资产纪律 B9）
                                  #        + **≤10 分钟预算与三项抽检定位**（Y1-r12）
docs/runbook/04-clips.md           # 增补：04.5 补料挂起与恢复
CLAUDE.md + AGENTS.md              # **SSOT 纠错手册改写**（R1-r12，两份必须逐字同步）
docs/runbook/03-tts.md             # **第三本手册：核心规程与铁律 1/2 要与 §3 两层关系对齐**
                                  #  + 修第二处不存在的 `ava-cloud` 别名（R1-r13）
README.md / docs/INDEX.md / docs/WORKFLOW.md   # 03.5 行与导航同步（Y2-r12）
docs/dev/STANDARD.md               # §九文档标准表：补 docs/runbook/ 与导航类文档两行 (B2-r12)
tests/test_docs_invariants.py      # **文档不变量固化成测试**（Y2-r13，进总闸）
```

`corrections.py`/`ingest_patch.py` 在顶层的原因：`tts.py`/`clips.py` 要 import 它们，
agent 依赖 pipeline，pipeline 绝不反向依赖 agent。

### 1.2 修改的现有文件（Review 后重估的真实改动量）

| 文件 | 改动 | 行数量级 |
|---|---|---|
| `pyproject.toml` | ① `ava = "pipeline.agent.cli:main"` 加进既有 `[project.scripts]`（那里已有 14 个 `ava-*`）；② 可选补 `ava-cloud`/`ava-status` 两个别名（为 Tab 补全一致性，见 §2.4 入口基准）。**不改打包配置**：`[tool.hatch.build.targets.wheel] packages = ["pipeline"]` 自动带 `pipeline/agent/` 子包，B1-r11 已核 | 2 行 |
| `pipeline/status.py` | `advisories` 字段 + 三条产物检测（§2.2），**不 import 任何重模块**(Y9) | ~40 行 |
| `pipeline/tts.py` | speakable 链接管 overlay（§3.4，**含 `_reusable` 比对侧**）+ `--apply-patch` + **版本常量 `SYNTH_LOGIC_VERSION` 5→6**（R1-r17，含注释里一行版本历史） | ~80 行 + 1 行常量 |
| `pipeline/clips.py` | `candidate()` 集键走 `shots._key`；rescue 两段补位（§4.4，N2）；锚点直通补丁池（`_parse_anchor` extra_pools + `_anchor_candidate` 加 `shots_table` 参数，N4） | ~85 行 |
| `pipeline/vindex.py` | `build_captions`/`load_captions`/`check_caption_meta`/`_check_meta_shots` 全链加 `shots_dir`+`check` 参数；CLI 透传 (R8) | ~35 行 |
| `pipeline/shots.py` | `load()` 加 `check_config=True` 参数；`caption_frames` 透传 (R8) | ~8 行 |
| `pipeline/cloud.py` | captions 专用远端命令分支（**不是**往白名单集合加字符串，R9b）；`extra_args` 参数白名单正则 (R9c)；上行默认清单加 `03-audio/corrections.json` (R9a)；**抽 `remote_active_tasks(host)` + `cmd_status` 增印活跃后台任务**（Y1-r10） | ~40 行 |
| `pipeline/review.py` | 补丁标（**从段级 `via="patch-rescue"` 推导**，B4 二选一选定）+ approved 过期提示 (R6) | ~15 行 |
| `pipeline/render.py` | approved 过期硬闸：04-clips.json 存在 ∧ 段级内容 diff 非空 → SystemExit（**diff 是闸本体，mtime 只当快路径**，R3） | ~10 行 |

### 1.3 复用与**不复用**清单（Review 教训：声称「复用」必须指出接缝真的存在）

| 能力 | 复用点 | 接缝状态 |
|---|---|---|
| 状态诊断 | `status.inspect_episode` 原样调用 | ✅ 现成 |
| 拼音注入 | `g2p.to_tone3/format_for_engine/inject` | ✅ 现成 |
| 单段重配 | `tts.run(redo=)` + `_reusable` | ⚠️ 需接管（§3.4，R1） |
| 场景切分 | `shots.scan`/`shots.cut`（显式传阈值，**绕过 `shots.load`**） | ⚠️ 见 §4.3（R8） |
| 抽帧 | `shots.caption_frames`（加 `check` 透传后可用） | ⚠️ 8 行改动 |
| VLM 打标/向量 | `vindex.build_captions/build_embed` | ⚠️ ~35 行参数化（R8） |
| 云端调度 | `cloud exec/up/down` + 新增 captions 专用分支 | ⚠️ ~30 行（R9） |
| 渲染 | **零改动**（clip 自带 source 绝对路径；`_source_path` 只服务 BGM 段） | ✅ 已核实 |

### 1.4 开发环境约定：worktree 隔离，main 永远是可生产副本

```bash
git worktree add ../anime-video-agent-ava -b ava-harness
cd ../anime-video-agent-ava && uv python pin 3.12 && uv sync --extra apple --extra dev
ln -s "/Volumes/Samsung T7/anime-video-data" data   # 重建外置盘软链
cp ../anime-video-agent/config/cloud.local.json config/   # SSH/实例配置（gitignore 不随分支，B5）
```

**为什么必须带 `--extra`（R1-r11）**：`pytest` 在 `dev` extra、`mlx*` 在 `apple` extra，
而 pyproject **没有** `[tool.uv] default-extras`——extras 是 opt-in 的，裸 `uv sync` 只装
基座六项。后果不是“少装了东西”而是两条最关键的验收都跑不了：
① `python -m pytest -W error` → `No module named pytest`，总闸直接无法执行；
② PR2 的 `/voice` 真跑验证 → 选 loader 时 `No module named mlx_audio`。
而最坏的绕法是回 main checkout 验证——那正是本节要物理隔离的生产环境；另一条是
“PR2 先跳 mlx 路径”，用跳过验证换绿灯。① 备选：给 pyproject 加一行
`[tool.uv] default-extras = ["apple", "dev"]`（那是改配置，按项目纪律**先问人**）；
② `uv python pin 3.12`：`requires-python = ">=3.12,<3.15"`，本机 uv 挑到 3.15/3.11
时 `uv sync` 直接失败且报错指向版本（B3-r11，PR0 顺手）。

**分工写清（B4-r11）**：`pythonpath = ["."]` 让「不装包也能跑 pytest」，所以 venv 不是
跑单测的必需品——它只服务 PR2/PR3 的**真跑**验证（mlx 合成、ffmpeg、模型加载）。
把这条写出来，比笼统的「先 uv sync」更能防「验证借生产 venv 跑」那条歪路。

- ava 的四个 PR 都要动 `tts.py`/`clips.py` 接缝，而 main checkout 同时是服役中的
  制片环境——分支切换让制片代码在两版间横跳，Code Freeze 必须是物理隔离。
- data 软链两端指向**同一份**真实数据：只读共享。**验证矩阵分两栏（Y1-r14）**：
  · **本地栏 → `/tmp` 副本**：PR1、PR2（走本地 mlx 引擎的期）。
  · **云端栏 → 仓库内一次性验证期目录**：`data/episodes/_ava-verify-<标签>/`
    （PR3 的 captions 与 §3.6 的云端 apply-patch 都要上云，而 cloud 的
    `resolve_episode_rel_path` **硬拒仓库外路径**——既有测试
    `test_cloud.py:380` 钉着这条，不是猜测）。它不是交付期：**验收后 `rm -rf` 掉**，
    不删就会变成第 N 个神秘期目录。
  · 任何验证命令不许以**真实期目录**为写目标（这一条不变）。
- 测试不依赖外置盘（既有约定），worktree 不挂盘也能跑全量 pytest。
- **worktree 的 venv 是独立的**：PR2 的 mlx 真跑验证必须在**worktree 自己的 venv
  里跑**，不许借 main 的 venv（借生产 venv 跑新代码 = 绕过 Code Freeze 的物理隔离）。
- **本次改造的授权边界 (B3-r12)**：`CLAUDE.md` 第 44-49 行要求「严禁 Agent 私自修改
  `pipeline/` 源码，必须停机向人类出示三项汇报」——本次 ava 重构**已获人类授权**，
  **授权范围 = 本 Spec §1.2 的文件清单**；超出清单的 `pipeline/` 改动仍需走那条汇报流程。
- **不要在 worktree 里升级依赖**（B2-r11）：`uv.lock` 与 main 共享同一份，
  `uv lock --upgrade` 会让两个环境跑的不是同一套版本，而总闸的「1163 条全绿」
  在两处就不是同一个含义了。
- 合并纪律：每 PR `python -m pytest -W error` 全绿 + 既有 1163 条零修改，fast-forward。

---

## 2. StateResolver 与 CLI 交互设计

### 2.1 Scope 推导（纯函数）

```python
def scope_of(status: EpisodeStatus) -> str:
    """产物阶段 → Scope。status.py 实际产出 12 种 current_step 字符串（含
    「02 脚本写作（草稿待定稿）」「07 自动质检（未通过）」），测试按 12 种全枚举钉死 (B6)。"""
    return "creative" if status.current_step.startswith(("01", "02")) else "pipeline"
```

`asset` scope 不进自动推导，只由显式 `/asset` 进入。

### 2.2 status.py 的 advisory 机制（Review 后修正语义）

`EpisodeStatus` 新增 `advisories: list[str]`。**三条纪律**：

1. **常驻性** (Y8)：advisory 与阶段判定**解耦**——不是插在哪个分支里，而是无论
   当前走到哪一步（含 05/06），只要事实成立就显示。未应用纠错在渲染阶段依然
   是返工源，隐身不可接受。
2. **坏文件免疫** (Y7)：三条探测各自 try/except——`corrections.json` 被手编坏时
   advisory 显示「corrections.json 不可读：{e}」，绝不让状态诊断整体挂掉。
3. **零重依赖** (Y9)：pending 探测就是「glob `patch_assets/` vs 读 `pool.json`」
   十行逻辑，**内联进 status.py**，不 import `ingest_patch`（它会拖入 vindex/numpy
   链；status 被看板对每一期调用）。

三条检测（v1.20 起为四条，第四条见 §2.6）：
- `03-audio/corrections.json` 有未完成条目（`applied=false` 或 `done_segments
  未覆盖 affected`，R2）→ 「N 条纠错待应用/待收尾」。
  **指引分本地/云端期**（读 manifest 的 engine 字段，B6）：云端期显示
  「这期是云端配音，apply 走 `cloud run`」——照抄错误侧的命令会撞
  `run()` 的引擎变更 SystemExit（§3.6）；**manifest 缺失/不可读时 engine 读不到，
  指引降级为「先看 03-audio/manifest.json 的 engine 字段决定本地/云端」**，
  不许裸 KeyError 也不许静默当本地期 (Y2)；
- `patch_assets/` 有未入库文件 → 「补料挂起：N 个文件待入库」；
- `04-clips.approved.json` 与 `04-clips.json` 内容不一致（段级 diff）→
  「approved 已过期，必须重走 05」——**这条同时补上既有系统的盲区**：
  今天任何 clips 重跑后 approved 都会静默过期 (R6)。

### 2.3 `ava` 启动形态

```bash
ava                       # 根目录看板：各期 current_step + advisories，回车秒选最近期
                          #（data/episodes 未挂载时走 status.main 同款
                          # 「可能外置硬盘未挂载」分支，不显示空看板，B5-r7）
                          # 选择输入语义写死 (B2-r8)：回车 = 第 1 行；数字 = 序号；
                          # 字符串 = 当期号直跳；非法输入重提示不猜测
ava <期目录|期号>
ava <期> /voice           # 直达指定模式
```

**非 tty 降级** (B2-r5)：看板/REPL 的 `input()` 在非 tty（tmux detached、脚本调用、
`ssh host ava`）下会 EOFError 裸崩。启动时 `sys.stdin.isatty()` 判定：非 tty 只打印
看板/状态卡就退出，要交互必须显式给期参数。

**看板排除规则写死 (B4-r14)**：`status.py:307` 今天只过滤 `.` 前缀（隐藏文件），
`_ava-verify-*` 这类验证期目录会出现在看板第一行（刚动过、mtime 最新）。
规则改成**排除 `.` 与 `_` 两种前缀**（`_` = 非交付目录的约定），并在看板末尾
印一行「已隐藏 N 个下划线目录」——隐藏要可见，不静默。

### 2.4 REPL 与斜杠路由

```
[/chat] 选题发散（creative，LLM 在场）  [/script] 聚焦写稿（产出只许 02-script.draft.md）
[/run]  管线执行（命令先回显、按 y 才 subprocess）  [/voice] 顺听纠错（§3，裸行不过 LLM）
[/patch] 补料（§4）   [/status] [/board] [/quit]
```

硬纪律（代码强制，非 prompt 约定）：
1. **Code Freeze**：唯一书写工具 `write_episode_file` 校验路径必须 resolve 到当期
   目录内且文件名在 scope 白名单（creative: `{01-topic.md, 02-script.draft.md}`；
   pipeline: `{}` 零写权限），**且落盘必须走 `paths.atomic_write`**（B3-r8——
   §0 的「原子落盘」不能只是口号，唯一书写工具的契约里要有它）。**resolve 判定两端同做** (B1-r5)：data/ 是指向外置盘的
   symlink，只 resolve 一边，合法写入被误杀或越界写被放行，二者必居其一。
   Code Freeze 的判据工具自身失败时（git 缺失/非零退出）必须 WARN 横幅，
   绝不静默放行（B4-r6）——与「fallback 必须显式可辨」同一条家规。
   写 `01-topic.md` 额外要人在 REPL 显式确认——
   01 选题按 WORKFLOW 是人的活，harness 代笔前必须问过 (B7)。
2. **/run 白名单按 scope 分，基准是「模块 + 子命令」（Y1-r8/Y1-r11）**：
   **一律以 `python -m pipeline.X [子命令]` 为白名单键，不走 console script**。
   理由：仓库已有 14 个 `ava-*` 脚本（`ava-tts`/`ava-clips`/…），而 `cloud`、`status`、
   `agent` 三个没有脚本别名——靠脚本表会让白名单与脚本表两处失同步（漏一个就是
   「进得去跑不了」第三次）。可选补 `ava-cloud`/`ava-status` 两个别名只为 Tab 补全
   一致性，**不是执行基准**。用户可见的三种写法（`ava` / `python -m pipeline.X` /
   `ava-X`）在 README 与 runbook 里统一教 `ava`（宿主入口）与 `python -m pipeline.X`
   （底层排查与复现），`ava-*` 保留为历史兼容不再新增。
   制片期（creative/pipeline）放
   `{check_script, tts, clips, review, render, qc, cover, bgm, status}`；
   **asset scope 放 Phase 0 命令** `{ingest phase0, shots build/frames/caption-frames,
   vindex captions/embed, faces detect/cluster/sheet/name/presence,
   cloud status/logs/doctor/up/down/run/push/pull}`——
   asset scope 进得去就得跑得了，不然它是空壳。两条粒度纪律：
   · **faces 必须全五个子命令 (Y2-r10)**：detect（① 检测+嵌入）/cluster（② 聚类 /
     GPU 小时级）/sheet/name（③ 贴名）/presence（④ 落索引）是四步流水线，
     只放 sheet/presence 等于只留终点、砍掉最该有进度与断点封装的重活；
   · **`cloud exec` 不进白名单 (R1-r10)**：它是位置参数、不接受任何值域、且
     `cmd_exec` 不经过 `validate_task_whitelist` 也不经过 `build_remote_run_command`
     ——`cloud exec "rm -rf /root/autodl-tmp/models/…"` 能通过 spec 写的全部三道
     校验。与「远端 shell 是真注入面所以要严校验」直接矛盾。要保留这条逃生口，
     它必须与其余通道**同级处理**（回显 + 额外二次确认 + 文案写明「绕过白名单、
     直接执行任意远端命令」，且 `--fg` 的 60s 超时只是本地掐断 ssh、远端命令仍在跑，
     这一点也要写在文案里，B2-r10），而不是混在 Phase 0 命令组里当普通子命令；
     当前决定：**不放**。
   **值校验按命令逐个定义 (Y1-r9)，
   不是复用一套窄正则**（`^[\d.,\s]+$` 那种用在 `ingest phase0 /Volumes/T7/[VCB]
   Oregairu [01].mkv --anime 春物` 上会全军覆没，asset scope 刚补的命令一条都跑不出去）：
   · 路径类值 → 「resolve 后存在」（本地走 argv 列表，无 shell 解析即无注入面，
     不做字符正则——本地 subprocess 的真正风险是误拒，不是注入）；
   · 番名值 → ∈ `sources.json` / `characters.json` 的键；
   · 集号值 → `S\d+E\d+|SP\d+`；
   · **簇号值（`faces name`）→ ∈ 当前聚类产物 (B3-r10)**：它写的是
     `characters.json`（Phase 0 全局资产、跳期共享），不能让写全局资产的动作无约束；
   · cloud 系的值仍走 R9c 的严校验（远端 shell 是真注入面）。
   「同一套机制」指的是 flag 白名单 + 按 flag 定义的值域，不是同一套正则。
   两组都过这套机制。
   **拒收名单含 `cloud down --force` (B1-r10)**：「--force 在 ava 层拒收」不能只读字
   面挂 tts——`cloud down --force` 会掉正在跑的后台任务（GPU 计费中），是销毁性动作，
   同一份名单，指引写着「先 `cloud status` 确认无活跃任务」。
   `--force`/`--force-all` 在 ava 层直接拒收。**第二层闸**：
   `cloud.build_remote_run_command` 的 `extra_args` 按 **flag+值对**白名单校验 (Y3)：
   旗标在清单内**且**值过该旗标的正则才放行——`--redo` 的值限
   `^(?:[\d.,\s]+|stale)$`（**stale 是 tts.py 自己 help 里写着的关键字**，
   纯数字正则会误杀它，Y3；`--redo "3,7 && curl x|sh"` 这类值注入同样被拒）；
   `--floor` 限数字；不带值的旗标不许带值。测试必须含正例（`--redo 3,7` 放行）——
   只测拒收会实现成「全拒」，把合法流程一起堵死。
3. **会话状态不落盘**：现场恢复 100% 靠产物。
4. `/voice`、`/patch` 裸行走确定性解析器，不过 LLM——**解析可复现**（同一输入永远
   同一张补丁）；但 `pin_seed` 的种子值是首次落盘时随机生成、此后恒定，
   不说「同一张补丁」的满话 (Y10)。

### 2.5 llm.py 边界

stdlib `urllib` POST `{base_url}/chat/completions`（OpenAI 兼容端点）。
**为什么不用官方 SDK**（面试题，先写在这）：零新依赖是红线，而我们只用
chat/completions + tools 两个端点；代价是自己追协议变化——接受，因为工具表
只有 5-8 个、字段用量是协议的最小公约数 (Y11)。**工具清单不现场发明** (B3-r6)，
tools.json 的最小形态：
```json
{
  "creative": ["read_artifact", "write_episode_file", "list_episodes", "read_status", "search_notes"],
  "pipeline": ["read_artifact", "read_status", "list_episodes", "run_pipeline"],
  "asset": []
}
```
（Y3-r7 两处落位：**pipeline 表的消费者是 /run 的确定性执行器**，不是 LLM——
pipeline scope 不加载 llm.py，这张表是给「人敲命令 → 白名单校验」用的能力表；
**asset scope 必须有显式空表**，缺键时降级行为未定义，`/asset` 进得了但工具域
不该静默猜。另外 `read_artifact` 的读域要写死：期目录内 + `data/library/` 只读，
越界读不敏感但会养成「工具什么都能读」的错觉，稀释写校验的严肃性，B1-r7。
`run_pipeline` 只放 §2.4 白名单内的子命令；每个工具在代码里带一句话契约与参数
schema，PR4 不许超出这张表发明新工具。）行数预算 ~150（含多轮 tool_calls
状态机与超时），不是 100。**tool_calls 循环必须有 max_iterations 上限** (B3-r5)——
模型死循环调工具 = 无限烧 token，两行代码的事。密钥只走 `api_key_env` 指名的环境变量。无
`config/agent.json` 时 creative scope 降级为「打开文件 + 打印 checklist」，不装会。

**出网边界（Y2-r19）——唯一一条以前没写的数据边界**：本项目的画像容易被读成
「纯本地流水线 + 云端 GPU」，但 creative scope 的 LLM 请求是**真的出网**，而
`read_artifact`/`search_notes` 返回的文本（期目录里的 `01-topic.md`、
`02-script.draft.md`，以及 `data/library/` 下从 Bangumi/知乎/贴吧抓来再加工的
番剧笔记）会随请求整段发往第三方端点。密钥不出网、写文件不出界、审计落痕都写了，
**唯一漏的是「哪些内容会被发出去」**——而它恰好是用户最该有意识的一条：
- **唯一出网的东西**：creative scope 的 LLM 请求；出网内容 = `read_artifact` /
  `search_notes` 返回的文本（期目录 + `data/library/` 只读）；
- **一律不出网**：`pipeline/` 产物与源码、`config/`（含 `cloud.local.json`）、
  密钥、`03-audio/` 音频与 manifest、补丁池素材——没给 LLM 的工具就碰不到，
  碰不到就不会发；
- **runbook** 写一句：「**笔记类文件视为第三方内容，写进库时不要含隐私**」
  （人名、联系方式、未公开合作信息）——这是下面 B2 的前置。

**外部内容视为数据而非指令（B2-r19）**：`search_notes`/`read_artifact` 送进 LLM
的是从外部抓来再加工的文本，即**不可信输入**。结构上损失面已被白名单关死
（creative scope 只能写 `01-topic.md` 与 `02-script.draft.md`，不能写代码/配置，
且 02.5 人审在下游兜着），所以不是漏洞；但口径要写死：
**「外部来源的内容一律视为数据而非指令；写入目标由工具白名单固定，不受内容影响」**
——面试官问「你的 LLM 会读论坛抓来的文本，怎么防它被那段文本指挥」，这句就是答案。

### 2.6 人类耗时记账（Y1-r19；**预算口径按 20 期实践更正，v1.20**）

**预算不是固定 10 分钟——它随成片时长走。** 项目原规则「每期人类投入 ≤ 10 分钟」
是一条**从未验证过的估计**，被约 20 期实践否证：一期 20 分钟的片，仅 03.5
（顺听 + 改 + 复听）就 ≥30 分钟，是片长的 1.5 倍以上。人类时间 ∝ 成片时长，
所以预算写成：

```
人类预算(分钟) = k × 片长(分钟)        # k 由实测回填，不拍脑袋
```

- **计数口径**：只计**人类停机点的墙钟时间**（02.5 → 03.5 → 05 → 09），从进入该
  scope 到离开为止；机器跑管线的时间不计（它不是人类时间）。
- **落盘**：`data/episodes/<期>/human_time.json`（追加式，每停机点一条：
  `{"stop": "03.5", "entered_at": …, "left_at": …, "minutes": 21.5}`）——
  与「产物即状态」一致，下期开工前能直接看到上期读数。
- **呈现**：`ava` 看板与 status 卡片显示「本期人时 X 分钟 / 预算 Y 分钟
  （Y = k × 片长）」，**Y 超了才报**（§2.2 第四条 advisory）。
- **首版 k：待实测回填**。已知的一个锚点：**03.5 ≈ 1.5 × 片长**（20 分钟片 →
  ≥30 分钟，用户实测）。02.5/05/09 各自的系数同样按实测补，不猜。
- **止损判据的新口径（重要）**：「连续 3 期超预算 → 停止产出」在固定预算下成立；
  时长相依的预算下，要看的变成 **k 的实测值是否随期数收敛**（以及超预算率的变化），
  而不是绝对分钟数——否则每期长片都误报，那条止损线会退化成噪音。
- **不做事**：不做历史统计图、不做自动优化建议（那是人脑的活）。这条只回答
  「这一期花了多少分钟、相对片长是否异常」。

---

## 3. 顺听极简纠错与增量重配链路（v1.1 重写，R1/R2/R3/Y1/Y2/Y4/Y5）

### 3.1 交互闭环（/voice 模式）

```
/voice
  ava 打印：段号清单（label → seg-NN.wav 映射表，Y4）+ g2p.scan_heteronyms 预检清单
  用户顺听 → 切回终端随手敲：
    5段 重叠 念成 chóng dié 改成 zhòng dié      # 分支 A：字词级错读 → 段级拼音注入
    12段 语气发飘 换种子                          # 分支 B：整句听感 → 钉新种子
  每条 → 解析 → 回显结构化补丁卡片 → y 确认落盘 corrections.json
  （卡片显式打印条目 id，如「#2 段12 pin_seed」——回滚按**段号**、撤回按**条目 id**，
  两个数字命名空间并存，指令文案必须写明各按哪个号，B2-r6）
/done → python -m pipeline.tts <期> --apply-patch
  → 备份 → 只重配受影响段 → 提示复听 → 不满意「回滚 5」
```

**/voice 裸行路由顺序写死** (Y2)：**先全串匹配内建指令表，不中才进纠错解析器**。
指令表（全部 `fullmatch`，不是前缀；**数字组与文法表同形、认小数段号**，Y2-r7）：
`^听\s*(\d+(?:\.\d+)?)$`、`^停$`、`^回滚\s*(\d+(?:\.\d+)?)$`、
`^撤回\s*\d+$`（撤回按条目 id，保持整数）、`^done$`。两个方向的误吞都防：「听 5」
不会掉进纠错解析器（「听」不是该段文本子串会 PatchError）；「听起来第5段发飘」
不会被当播放指令（不是 fullmatch）；「听 12.3」「回滚 12.3」正常生效。
指令表进 PR2 测试。

**砍掉的第三分支** (R2)：`语速: 慢/中/快` 的系数在合成链上**从未被消费**
（`_render_one` 把它赋给 `_spd_coef` 下划线变量即弃）——它只进 manifest 供 eval
分组。所以「9段 太快」**不映射到任何合成动作**：解析器对「太快/太慢/偏快/偏慢」
明确报错「语速调整暂不支持（改语速会改变段时长、连锁排片 refit），
可换种子重试或改稿」，不假装能修。哪天要真做，那是一条「引擎参数或 atempo
后处理 + `_stale_downstream` + refit」的独立提案，先进施工图再说。

**顺听定位 (Y1-r12；预算口径 v1.20 更正)**：默认仍是 03.5 的「三项抽检」，
不是全量。人类预算**按成片时长核算**（§2.6）：一期 20 分钟的片，光 03.5
（顺听 + 改 + 复听）就 ≥30 分钟——所以「全量顺听」在长片上不是 7 分钟，
而是与片长同量级的深挖动作。规则不变：默认抽检（开头段 / 最长段 / 夹杂
英日专名段）；`听` 全量是**怀疑系统性发飘时的深挖动作**，执行前提示预计时长
（= 音频总时长）；`听 5` 是常规单段复查。runbook 03.5 重写时把「预算 = k×片长」
与三项抽检一并写进去。

**顺听输出** (Y3+Y6+B1-r6)：`听` = afplay **后台**顺序播放（Popen 不阻塞 REPL）；
裸行「停」= **停整个播放序列**（置停止标志 + kill 当前 afplay）——逐文件循环下
只 kill 当前进程会自动接播下一段，「停」名不副实；
`听 5` = `open -a "QuickTime Player" <该 label 对应的 seg-NN.wav>`。
段号是 label、文件名是 index（`seg-{index:02d}`），两者在小数段号插入后必然
错位——所有按段号找文件的动作必须过 `parse_script` 的 label→index 映射 (Y4)。
~~QT 歌单模式假设已删除（未实证）~~。

### 3.2 解析器（`pipeline/corrections.py`，纯函数）

**预处理**：输入先过 `unicodedata.normalize("NFKC", line)`——全角数字、全角
小数点、全角逗号一次解决（`５段`→`5段`、`zhòng，dié`→`zhòng,dié`）(Y5)。

```python
@dataclass
class Patch:
    segment: str                 # 稿件段号 label
    kind: str                    # "pronunciation" | "timbre"
    word: str | None
    heard: str | None
    target_tone3: str | None     # 如 "zhong4die2"
    issue: str | None
    action: str                  # "inject" | "pin_seed"
    scope: str                   # "segment"（默认）| "global"（用户显式说「全局」才允许，Y1）
    raw: str
```

文法（按优先级；**任何一步不确定都抛 PatchError，带「解析出了什么 + 该段原文 +
正确写法示例」，绝不猜**）：

| 规则 | 判据 | 落点 |
|---|---|---|
| 段号 | **三条独立正则**（N1+Y1，共三种写法，数字必须在此数清）：尾置形
  `(?:第\s*)?(\d+(?:\.\d+)?)\s*段`（`5段`/`第5段`/`第 5 段`）；前置形
  `段落\s*(\d+(?:\.\d+)?)`（`段落 5.1`）；前置简式 `段\s*(\d+(?:\.\d+)?)`
  （`段5`/`段 5`——v1.2 拆条时弄丢的形态，补回）。**同一句输入出现多个段号
  （finditer 全量匹配 >1）→ 报错「一次只纠一段，请分两条打」**，绝不静默取第一个 (Y2)。
  文法表每条正则必须拿 PR2 测试清单逐条跑过才可定稿 | 必须 ∈ labels，否则报错并列可用段号 |
| 目标拼音 | `改成\|应读\|读作` 之后的拉丁簇 | 走 §3.2.1 音节切分 + 逐音节 to_tone3 |
| 听成 | `念成\|读成\|听成` 之后的拉丁簇 | 存 heard，不进合成。**heard 只做
  「拉丁簇存在」的宽校验、原样落盘，不过音节切分器**（Y1：它是 audit-only 字段，
  「念成 xī'ān」是信息完整的合法报错，被隔音符规则误杀 = 把人的报错拒之门外） |
| 裸拼音簇 | 无关键词且全文唯一簇 | 视为 target；**≥2 个无关键词簇 → 报错**；有 heard 无 target → 报错「缺目标读音」 |
| 词 | 剔除关键词后的汉字串 | 必须是该段配音文本子串；不是 → 报错并打印该段原文 |
| 听感词 | `{发飘, 断层, 不稳, 闷, 机械, 吞字, 杂音, 赶, 变了}` 命中且无拼音簇 | `pin_seed` |
| 语速词 | `{太快, 太慢, 偏快, 偏慢, 有点快, 有点慢}` | **明确报错**（R2，文案见 §3.1） |
| 全局 | 含「全局」/「所有段」 | scope=global，回显时**二次确认**（影响面是全期） |

**错误消息必须把受控词表列全**（「音色变了」这类表外表达 → 报错 + 列全部听感词）。

#### 3.2.1 拼音音节切分器（Review 指出的真正难点，Y5）

`to_tone3` 只对单音节正确（整串喂 `zhòngdié` 实测得 `zhongdie4`，垃圾）。
切分规则（三档，宁可报错不可猜）：
1. **空格分隔**：每 token 一个音节，直接逐个 `to_tone3`；
2. **无空格但每音节都带调号或调号数字**：按「每个音节恰好含一个调号字母（āáǎà…ü 的
   调号形）或恰好一个末尾 [1-5]」贪心切；切不出 → 报错。**紧凑串的回显卡片必须逐音节
   列出切分结果让人确认** (Y4)：`xian1` 按规则是单音节 xiān，而用户想的可能是
   xī'ān——切分结果进确认卡片，零新机制；
3. **其余（全无声调标记、隔音符 `xī'ān`、混写）→ 报错**：
   「请用空格分音节或给每个音节标声调，如 `zhòng dié` 或 `zhong4 die2`」。
   隔音符合隐语义（音节边界信息），识别它等于重做拼音分词——不做，交给报错引导。

### 3.3 落盘契约：`03-audio/corrections.json`（追加为主、撤回为例外显式删除，B4-r7）

```json
[
  {"id": 1, "segment": "5", "kind": "pronunciation", "scope": "segment",
   "word": "重叠", "heard": "chóng dié", "target_tone3": "zhong4die2",
   "action": "inject", "raw": "…", "applied": false, "applied_at": null,
   "affected": ["5"], "done_segments": [], "created_at": "…"},
  {"id": 2, "segment": "12", "kind": "timbre", "issue": "语气发飘",
   "action": "pin_seed", "seed_pin": 483920, "applied": false,
   "affected": ["12"], "done_segments": [], ...}
]
```

**字段全集与写者/读者表（B4-r17）**——这类 schema 一旦产出，回填比新建贵，
所以先把「谁写、谁读、何时可缺」写死：

| 字段 | 谁写 | 谁读 | 何时可缺 |
|---|---|---|---|
| `id` | `/voice` 落盘（写回时被占用则递增重试，Y1-r16③） | `撒回 N`（按 id 定位） | 不可缺 |
| `applied` / `applied_at` | `--apply-patch` 逐段（Y1-r4） | status advisory、`load_overlay`、`plan_apply` | 不可缺（旧条目按 false 处理） |
| `affected` | `plan_apply` 建 plan 时 | `plan_apply` 重跑、advisory | 建 plan 前为 null |
| `done_segments` | `--apply-patch` 每段合成成功（set 语义，Y1-r4/B5-r6） | `applied` 判定、advisory 列剩余段 | 建 plan 前为 [] |
| `seed_pin` | `/voice` 落盘时（`random.SystemRandom`，此后恒定） | `--redo` / 重配 | 仅 `pin_seed` 条目有 |
| `scope` | 解析器（默认 `segment`，显式「全局」才 global） | `effective_injections` 选全局还是段级 | 旧条目缺失按 `segment` |

**字段全集写死的好处**：`applied` / `affected` / `done_segments` 三者是「假完成
状态」那次讨论（R2-r4）的直接产物，分开写着，下一版加字段时至少有张表要动。

- 写入走 `paths.atomic_write` 整份重写；`seed_pin` 落盘时 `random.SystemRandom`
  生成并记录（此后是钉值）。**手编坏的 corrections.json**：status 侧有 Y7 免疫；
  tts 侧 `load_overlay` 遇 JSONDecodeError 必须 SystemExit 指路「修复或移走
  03-audio/corrections.json」——两种病不许一个治一个不治 (B4-r5)。
- **写者纪律（Y1-r16）：这是“永久资产”，而全库无锁、写入是整份重写，所以必须
  自己把写者问题回答了**（`atomic_write` 只保证读者看到完整版，管不了两个写者）：
  ① **写前指纹校验**（项目最熟的形状，与 `verify_script_vo_hash`、approved 内容闸
     同规）：写回前比对文件 mtime + sha 是否等于读入时记的值；不等 → **FAIL
     「期间 corrections.json 被其他进程改过，重跑本命令」**，不许静默覆盖；
  ② **单写者纪律**：`--apply-patch` 运行期间，REPL 的 `/voice` **拒绝 y 确认**
     （提示「应用纠错进行中，稍后再落盘」）——它在分钟级长任务里，窗口以分钟计；
  ③ **id 分配去竞争**：今天的 id = 「读到的 max+1」，两个写者会撞同一个 id，
     而「撒回 N」是按 id 定位的（撞 id = 撒错条目）。改成写回时若 id 已占用则递增
     重试，或直接用「时间戳 + 进程内计数」。
  ④ **y 之后回读一次确认条目在盘**（B2-r16）：一次 read、几毫秒，直接消灭
     「卡片回显了、无报错、但条目不在文件里」这个最坏的观感（它比任何锁都便宜）。
- **corrections.json 是永久资产**：删了它 = 下次普通重跑时 overlay 消失、
  段级 speakable 比对失配、音频**悄悄重配回错读**。写进 runbook 03.5 与备份清单 (B9)。
- **一词多段默认不联动** (Y1)：scope=segment 的注入只作用于该段——段 5 的「重叠」
  念错而段 18 的「重叠」念对了，是真实存在的场景，词级全局注入会废掉段 18 的
  已审音频（配音纪律：不替人废审听）。全局生效必须用户显式说「全局」。
- **段内子串污染** (Y2)：`g2p.inject` 是词长降序 str.replace，段内多处出现会全换
  （`hits` 字段本来就有记录）；解析时若 word 是 overlay/全局表内其他键的子串
  （或反之），WARN 列出受影响词并要求确认。

### 3.4 overlay 与 tts.py 接缝（R1 修复 + R3 语义反转）

**R1 验伤结论**：overlay 合并在 cfg 上**不够**——`speakable_traced` 读的是
`_injections()`/`_readings()` 两个零参 `lru_cache`，数据源是模块常量 `CONFIG`，
根本不吃 cfg。照原 Spec 实现，读音纠错 = 指纹哈希变、合成文本不变、
重配产出逐字节相同的音频、`applied=true`——静默空操作。

**修法（speakable 链接管，~80 行的核心就在这几处）**：

```python
# corrections.py
def load_overlay(episode: Path, include_pending: bool = False) -> dict:
    """corrections.json → {"injections": {"*": {词: tone3}, "<label>": {词: tone3}},
                          "segment_seeds": {label: pin}}
    **常态只收 applied=true** (R3)：pending 条目只在 --apply-patch 流程内生效。
    原 v1.0 写「全部历史」是错的——pending 的种子钉会泄漏进普通重跑，
    无备份重配 + attic 基线被污染。「读音沉淀是永久的」由 applied=true 承担，
    不是由「pending 也生效」承担。"""

def effective_injections(overlay: dict, label: str) -> dict:
    """段级生效表 = 全局("*") ∪ 该段的注入。纯函数，精确匹配，不做字符串手术。"""

# **label 的传法（N3 定案 + B7 升级）**：拆句段在 `_render_one` 里跑的是句级
# pseudo-segment（label 是 "21.1"），overlay 的键是稿件段号（"21"）。
# 修法不是从字符串反推（剥尾置 .数字会在「真段号 12.3 与段 12 的拆句 12.1 共存」
# 时撞键空间，B7），而是 **`render_segment` 把父段 label 显式传给 `_render_one`**
# （可选参数 `overlay_label`，默认 None = 用自身 label，单句路径行为不变）。
# 字符串反推方案彻底删除，键空间碰撞从根上消失。

# tts.py：speakable_traced / speakable 加两个默认参数（旧调用方签名不变）
def speakable_traced(s, engine_kind, overlay: dict | None = None, label: str | None = None):
    ...
    inj = {**_injections(), **corrections.effective_injections(overlay, label)} if overlay else _injections()

# 调用点接管（全部要改，少一处就是 R1 重演）：
#   run():        overlay = corrections.load_overlay(episode)（pending 时另算，见下）
#                 cfg = {**cfg, "pinyin_injections": 展开后的全集}  → 指纹自动覆盖 (g2p.load_injections 读 cfg)
#   _reusable:    speakable(take.text, eng, overlay, take.label)    → 段级比对含 overlay
#   _render_one / render_segment: speakable_traced(seg.text, kind, overlay, seg.label)
#                 （engine.cfg 上挂着 overlay，从那里取，不透传新参数）
#   render_segment 末尾的 seg_injections = speakable_traced(...)[1] 审计行也要接管 (B1)：
#                 漏掉它，manifest 的 g2p_injections 审计字段与实际合成文本脱节——
#                 与 R1 同族的「接线漏了一个调用点」。
```

**`SYNTH_LOGIC_VERSION` 必须 bump：5 → 6（R1-r17，本次必须表态）**。
代码里那条硬规矩是「任何改变合成输出的代码改动都必须 bump 它」，而被改的正是
合成链（新增 overlay 维度、改 `_injections`/`_readings` 读取路径、改合并顺序）。
**两读都通所以必须表态**——盘上确实还没有 overlay 产物（对既有音频输出没变），
但正因为它两读都通、而这条号背后站着一期真实事故（2026-09-11：27 段被静默复用为
修复前的产物，产出一期「新旧混血」音频，manifest 看起来一切正常），不表态的下场
就是下一个人在同一个接缝上再栽一次。具体三点：
1. **语义**：这个号的含义是「**产出这段音频的代码版本**」，不是「最后一次写入者」；
   PR2 之后「带 overlay 的段」其输出取决于 overlay 链——沿用它等于抹掉新特征的分界（B1-r17：
   把这句提到 tts.py 注释最前面，现在它埋在 v5 那段中间，PR2 的人不一定读到）。
2. **此后生效的规则**：任何改动本节这条链的补丁（`effective_injections`、
   `speakable_traced` 的合并顺序、`overlay_label` 传递、`_injections`/`_readings`
   读取路径）**必须 bump**，与 tts.py 注释里那条硬规矩对齐。
3. **报账闸要能看见纠错痕迹（B2-r17）**：`_report_stale` 的文案补一句「本期存在
   overlay 纠错段，其音频由 v6 之后的代码产出」——现在只看得到版本号，看不到
   「哪些段受过纠错」，而后者正是「防护从强制降为可见」时最该可见的东西。

`segment_seeds` 的 overlay 合并在 run() 里做（`{**cfg.get("segment_seeds",{}), **overlay["segment_seeds"]}`），
`_seed_pins` 比对自动生效，无需接管。

**接线级测试（2026-09-13 `_report_stale` 教训的落实）**：PR2 必须有一条测试断言
**假 Engine `synthesize` 实际收到的 text 含有注入渲染形**（如 `zhòngdié`），
而不是只测 `_reusable` 标红——现有测试全在 `_injections` 接缝内侧 monkeypatch，
接线断了照样全绿。**该用例必须覆盖多句段**（N3：>30 字触发拆句的段，断言
每一句收到的 text 都含渲染形——走显式 `overlay_label` 传递，不再依赖 label 字符串）。

### 3.5 `--apply-patch` 流程（含 R3 的正确语义）

与 `--redo`/`--force-all`/`--review` 互斥。

```
plan = corrections.plan_apply(episode, segs, cfg)
  # 1. 读 pending 条目；空 → 「没有待应用的纠错」返回
  # 2. **旧 manifest 报账闸（Y2）**：manifest 段条目无 speakable 字段时，overlay 进
  #    指纹 → inputs_changed → _reusable 的全表指纹 fallback 会让**全期**不可复用
  #    （EGOIST 早期期目录就是这个形态）。plan_apply 必须先算受影响集，遇旧 manifest
  #    时大字报账「本期 N 段旧产物无 speakable，应用纠错将重配 N 段、审听作废」，
  #    人显式确认才继续——配音纪律的红线场景，不许藏在 fallback 里。
  # 3. 计算受影响段：点名段 ∪（pending-inclusive overlay 下 _reusable 判不可复用的段，
  #    含 speakable 与钉种子两个轴；scope=global 的词必须列出全部命中段，逐段报账
  #   「请都复听」）。**受影响段 = redo = backup 集，一个真源**（Y1-r6）
  # 4. backup_segments：受影响段的 seg-NN.wav + manifest.json 拷入
  #    03-audio/attic/<YYYYMMDD-HHMMSS>/
tts.run(episode, redo=plan["redo"], _overlay=corrections.load_overlay(episode, include_pending=True))
  # pending 只在这条路径里生效（R3）；attic 备份已完成，回滚有基线
  # **条目进度模型（Y1 + R2 定案）**：条目落盘即带 plan_apply 算出的
  #  affected: [labels]；每段合成成功即时把 label 追加进 done_segments 并
  #  atomic_write；**applied=true 仅当 done_segments 覆盖 affected 时置（按 set 语义——
  #  回滚再战会重复追加同一 label，B5-r6）**。
  #  中断重跑 --apply-patch 读进度幂等续做。**redo 的算法钉死为判据减法（R1）：
  #  redo = affected 中「pending-inclusive overlay 下被 _reusable 判为不可复用」的段——
  #  判据与 _reusable 逐项对齐：speakable 失配 ∪ 钉种子失配（Y1-r6：pin_seed 不改
  #  speakable 一个字符，漏掉钉种子轴会出现「实际重配了段 5、报账名单却是空的」——
  #  「实际发生的」与「报出来的」不一致就是判据说谎）。
  #  done_segments 只做 applied 判定与报账，绝不参与 redo 计算。**
  #  **三个集合一个真源：affected = redo = backup 集**（同上判据 ∪ 点名段）——
  #  「backup_segments 涉及段」的歧义随之消除，pin 段首 apply 必有 attic 备份。
  #  名单减法（affected − done_segments）被否：「回滚 5 → 改参数再战」时 done_segments
  #  仍含 5 → redo 算成空集 → 用户被卡死。判据读法天然闭环：回滚恢复了旧 manifest
  #  段条目（无注入的旧 speakable）→ 失配 → 自动进 redo；已 done 段一致 → 自动不进。
  #  被否掉的读法：「首段完成即标 applied」会把部分完成记成全部完成——
  #  advisory 显示 0 条待应用、剩余段带着错读音原地不动，而所谓的
  #  「普通重跑向前收敛」既无保证会发生、发生时被 if inputs_changed 门控静默无声。
  #  「全部完成才标」同样被否——它让 Ctrl-C 窗口对已合成段依旧存在。
  # **manifest 缺失 = 拒绝并指路「先跑 03 配音」**（B3）：plan_apply 的报账闸（Y2）
  # 以 manifest 为输入，缺失时不许静默整期全量。
  # **同段同词的重复纠错**：落盘时若该段该词已有条目，WARN「已有读音 X，本条覆盖为 Y」
  # 并要确认（B4）——后者静默赢是拼音表的老坑，不许在纠错链上复活。
```

**回滚与撤回的区别写死** (B5)：`回滚 5` = 从 attic 恢复音频 + manifest 段条目，
条目回到 pending（改参数再战）；`撤回 N` = 删除条目（读音沉淀消失，回显必须写明
「沉淀已删，**下次配音时该段自动重配回原读音**」——用户会以为立即生效，B2-r7）。
两个动作的回显文案各自带一句对方的存在。
**快照选择语义** (B5-r5)：`回滚 5` 选的是「**包含段 5 的最近** attic 快照」，
不是「最近快照」——最近一次 apply 没碰段 5 时，最近快照里根本没有 seg-05.wav；
**滚出保留窗（找不到包含该段的快照）时报错指路「快照已滚出保留窗，只能重新纠错」**，
不许静默无操作 (B3-r7)。
**attic 保留策略** (B6 + Y2-r16)：保留最近 10 份快照，老的随新 apply 清掉；
**但修剪是唯一一处自动删除用户数据的地方，必须可见且有下限**：
- 修剪时打印被删清单**并列出该快照含哪些段**（只报目录名等于没报）：
  「清理 2 份旧快照：20260918-081500（含段 5、段 18）/ 20260918-093000（含段 5）
  ——若这些段仍需回滚，请先 approve 或手工留存」；
- 更稳的一档（可选）：修剪不删「被当前未完成条目引用的段」所在的快照，或把保留窗
  改成「最近 10 份 ∪ 每段各自最近 1 份」；
- runbook 03.5 那句「交付后可整目录清 03-audio/attic/」**要加前置**「清之前先确认
  没有 pending 条目」，不然交付前清空 = 所有「回滚」全失效；
- **清理责任人写死（B4-r16）**：`_ava-verify-*` 验证期目录 = **人**（验收后 `rm -rf`）；
  `attic/` = **程序**（按保留窗自动修剪并报账）——两类自增目录各有人管，
  不变成「谁都不管」的垃圾场。
- **每段一次整份重写是刻意的（B3-r16）**：40 段 = 40 次全文件写，看着 wasteful，
  但「窗口归零 > IO 成本」（文件小）；**注释里要写上这一句**，免得将来有人为了省 IO
  改回批量写——那会重新打开 R2(r4) 的「假完成状态」窗口。

### 3.6 云端执行侧 (R9a)

云端产的期（`engine=qwen3_tts_cuda`），`--apply-patch` 必须在**产出该期音频的同
一侧**执行（本地跑会撞 `run()` 的引擎变更 SystemExit）：
- `cloud.py` 上行默认清单加 `03-audio/corrections.json`（改代码默认值，不动 config）；
- runbook 03.5 写明这条侧别纪律；ava 在 /voice 落盘纠错后按 manifest 的 engine
  字段提示「这期是云端配音，去云端 apply」。
- **与 ADR-0016 的分工指针 (B3-r13)**：ADR-0016 定「推理上云」，本节定「apply 与产出
  该期音频的引擎同侧」——两条不矛盾：ADR 管算力位置，本节管**同一期音频的引擎一致性**
  （本地 apply 会撞引擎变更 SystemExit）。指针放在这里，免得将来有人拿 ADR 质疑 spec。

---

## 4. 临时补料与联合检索（v1.1 重写，R4–R8、门槛判决、B1–B4）

### 4.1 布局与资产形态

```
data/episodes/<期>/
  patch_assets/                 # 人放素材（入口即状态）
  04-patch/
    requests.md                 # 缺口清单
    pool.json                   # 补丁池登记表 + 门槛字段（见 4.4）
    shots/<pool>_SP01.json      # 镜头表（期级本地目录）
    frames/  vindex/            # caption 帧 / captions + scene 索引
```

**视频 only，图片不做** (R5)。图片在当前链路有三堵硬墙（`_parse_key` 只认
SxxEyy/SPxx；`candidate()` 对 duration=0 必判 None；render 对 jpg 跑 ffprobe
duration 直接炸），「与 SP 微动同规」不成立——微动是视频文件。用户要放图片，
报错消息里给一行转换命令：`ffmpeg -loop 1 -t 8 -i in.jpg -pix_fmt yuv420p out.mp4`，
转完走视频路径，零新代码。真图片形态（虚构 duration + render `-loop 1` 分支）
是独立提案，不在本期。

- pool 名：期目录名经**净化**生成——`re.sub(r'[^A-Za-z0-9_-]+', '-', episode.name)
  + "-patch"`（Y4：期目录可以叫「EGOIST三期」，校验管的是「生成的池名是否撞上既有
  池名/番名」，不是拒绝用户的命名习惯；撞名追加序号）；资产编 `SP01…SP99`，
  >99 个当场报错 (B3)。clips 终端报数对 via=patch-rescue 段补「[补丁补位]」标，
  与「[首选被占·第N级救回]」同规 (B2)——via 不能只活在 review 页。
- pool.json 每条资产记 `{"path", "duration", "size", "mtime"}`：
  同路径换内容 / 入库后源被删 → 加载时漂移 WARN（不许到渲染才炸）(B4)。

### 4.2 挂起与恢复

```
04 有 no_match/short 段 → /patch 裸行记缺口 → 素材丢进 patch_assets/
→ status advisory「补料挂起」（常驻，§2.2）
→ python -m pipeline.ingest_patch <期>
→ 重跑 clips（先备份 04-clips.json.prepatch.bak）
→ 缺口段被补丁段填充并带「补丁」标 → 回 05
```

**手改时间码保护闸**（R1-r8，v1.0 的检测升级复活）：自动重跑 clips 前，把当前
`04-clips.json` 与即将生成的新产物做**段级 diff**——rescue-only 保证非失败段
逐字节不变，所以每个差异段只有三种成因：(a) 之前失败、现在被补丁填充；(b) rescue
触及；(c) **手改或稿件改动**。(a)(b) 可机器识别，剩下的就是 (c)：列出段号与改动内容，
**停下来交人定**（先 approve 当前版，或把手改片段誉走，或显式确认丢弃）。
**归因文案要并列两种成因 (B1-r9)**：`compute_script_vo_hash` 只提 `配音：`行，
所以改 `查询`/`备选`/`锚点`/`场景`/`人物` 行同样让段产物变且 vo_hash 放行——
报错写成「这些段的产物会变且不是补丁/rescue 引起——**可能来自手改 04-clips.json
或稿件改动**」，行为不变（本来就该停），但别把手改的账算到合法修稿上。
**坏文件免疫 (B2-r9)**：clips.run 今天不读旧 04-clips.json（从头算），本闸在落盘前
新读它——读到坏 JSON 时 WARN「旧文件不可读，无法 diff，手改检测跳过」并明说，
不许裸 JSONDecodeError（Y7 的坏文件免疫对这条新读文件路径同样适用）。
这条闸同时补上既有系统的盲区：裸跑 `python -m pipeline.clips` 重跑也会覆盖 05
手改且连 bak 都没有——与 R6 的 approved 过期保护合成完整覆盖（approve 前 + 后）。

**approved 过期保护** (R6 + Y6 + R3 边界修正)：三层闸，闸本体是**内容**不是时间戳。
① clips 重跑落盘时若 approved 存在且与新产物段级 diff 非空 → 大字 WARN；
② status 的第三条 advisory（§2.2）常驻；③ **render 硬闸**：
`04-clips.json 存在 ∧ approved 存在 ∧ 段级 diff 非空 → SystemExit 指向重走 05`。
diff 谓词与 §2.2 advisory 3 **同一实现两处用**；mtime 只做快路径（不更新就跳过 diff）。
三个边界一次全消 (R3)：04-clips.json 缺失 = 不比（render 本来就只吃 approved，
既有 test_render.py:349/374 的 fixture 就是这个形态，总闸不红）；cp -r 副本 /
rsync 下行导致的 mtime 翻转，内容相同即放行；确定性重跑逐字节相同，不误伤。

### 4.3 ingest_patch.py：不复用全局加载链，走期级本地加载器 (R8)

Review 验伤：`shots.load` 强制对账 `threshold(anime, key)`（config 无补丁池键 →
SystemExit，ADR-0003 明文「没有默认值」）；`_check_meta_shots` 硬绑全局
`shots.load(anime, key)`；`_require_full_index` glob 全局 SHOTS_DIR。
「加目录参数原样复用」不成立，真实改动是**加载链的参数化 + 期级自校验**：

```python
def ingest(episode: Path) -> dict:
    for asset in pending_assets(episode):
        # 1. ingest.intact / probe_video 原样复用（不过拒收）
        # 2. 镜头切分（本地）：cuts = shots.scan(video)；
        #    shots.cut(cuts, thr, duration, min_shot) —— thr 显式传：
        #    主番有标定用主番值并记 "threshold_source": "<主番>"，
        #    没有就用 ingest_patch 常量并记 "threshold_source": "patch-default"。
        #    不走 shots.threshold()（它无默认值、缺配置硬失败）。
        #    meta 落盘全部参数来源——阈值口径不明是静默失败源。
        # 3. 抽帧：shots.caption_frames(pool, key, out_dir, dest_dir, check=False)
        #    （shots.load 加 check_config 参数，补丁表自校验不对账全局配置，R8）
        # 4. VLM 打标（云端，**两段式**，Y2-r8）：vindex captions 加目录参数，
        #    走 cloud.py 的 captions 专用分支（R9b）。cloud 的 tmux 后台化意味着
        #    提交即返回——本步提交后打印「已提交打标，完成后重跑本命令」并退出；
        #    status advisory 显示「补丁打标进行中」。与「产物即状态」合拍：
        #    captions 文件就是断点（build_captions 断点续跑是既有能力）。
        #    重跑 ingest_patch 时检测远端 captions 就绪 → pull → 走步骤 5。
        #    **就绪检测必须看生产者，不只产物（Y2-r9；判据修正 Y1-r10）**：只查 captions
        #    行状态会把「任务死了」归入「等待中」——实例掉线/watchdog 空闲关机/tmux
        #    会话死掉时，captions 永久 pending，用户永远看到「打标进行中」。
        #    **真判据：查 tmux 会话名（`ava-captions`）是否存在。**
        #    两个假判据必须从实现里排除（本轮对源码验过）：
        #      · `cloud status`——cmd_status 只印 SSH/GPU/数据盘/watchdog/账簿，
        #        没有 tmux 会话列表，看完得不到任何任务死活信息（Y1-r10 要顺手补它）；
        #      · 心跳文件——watchdog 每 20s **无条件** touch 一次（只要 watchdog 活着
        #        就刷新），所以「心跳新鲜」≠「有任务在跑」；它是个会答「活着」的假判据，
        #        **比没有判据更坏，从文字里删掉**。
        #    实现：把 cmd_down 里那段内联 `tmux ls | grep '^ava-'`（cloud.py:1020）
        #    抽成可复用函数 `remote_active_tasks(host) -> list[str]`（~8 行），
        #    ① cmd_status 增印「活跃后台任务: [ava-captions] / 无」；
        #    ② 本步就绪检测用会话名精确匹配（与 build_tmux_launch_command 的 ava-
        #       前缀纪律同源）。
        #    任务不在运行且 captions 未完成 → 报「远端打标任务不在运行，captions 未完成：
        #    重提交（pending 行会重打，断点续跑不重复花钱）或先 cloud up 排查实例」。
        #    两段式与「产物即状态」仍合拍，只是把死任务从等待中切出来。
        # 5. 向量化（本地）：vindex.build_embed(pool, out_dir=04-patch/vindex)
        #    （load_captions/check_caption_meta/_check_meta_shots 全链加 shots_dir 参数，R8）
        # 6. pool.json 登记（含 no_match 字段，见 4.4）
```

**stage 2 的三态可辨（Y2-r15）**：两段式的重跑不能坐在一个「不会失败」的 pull 上——
`cmd_pull` 今天无条件打 `[OK] pull 完成。` 且 `return 0`，而「实例已被 watchdog 关机」
「任务还没打完」「就绪」三种状态**输出几乎同形**（都是「○ 未发现远端产物或跳过」+
一行 WARN + 一行 OK），而正确动作完全不同（重开机 / 继续等 / 走 stage 2）。
三处一起改：① `cmd_pull` 在动 rsync 之前先探实例可达（`is_ssh_reachable`，已有）
与活跃任务（r10 抽的 `remote_active_tasks`），不可达 → **直接 FAIL**「实例不可达/已
关机，先 `python -m pipeline.cloud up`」，不降级成「未发现产物」；② `session_tasks`
加 captions 自检分支（查 `04-patch/vindex/*.captions.json` 存在且无 pending 行），
与 probe/tts 同规；③ `[OK] pull 完成。` 改为**按结果分支**（有失败/未验证就不打 OK）。
§4.3 的用户文案同步改成三态可辨版（实例关机 / 任务未完 / 就绪），与 r10 的存活检测
接口对齐。

断点续跑、failed 重试、pending 拒建全部继承 `build_captions`/`build_embed` 既有判据。
**成本口径要分两把尺子（Y1-r15 / B2-r15）**：打标 1.5s/镜头是**生成时间**（对外文案
不许写「秒级」）；而**账单口径是实例 wall-clock**（开机 + 上行 shots/frames +
加载 15GB Qwen3-VL + 生成 + 拉取 + 关机）。实测校准点：2026-09-13 云端 21 分钟 =
¥0.847（≈0.04 元/分，与 `hourly_rate_cny: 2.4` 一致）。
`vindex.estimate_caption_cost` 只估生成秒数（3 分钟切片 ≈63 镜头 → ¥0.063），
**它是生成时间估计，不是账单估计**——两把尺子各管各的，注释里要写明，
别再互相冒充（实例时间归 cloud 的账簿 `calculate_session_cost`）。

### 4.4 联合检索 = **rescue-only 补位**（R7 的设计性修复 + 门槛判决落实）

v1.0 的「补丁向量 vstack 进主场景池」删除。**补丁池不进主检索**：它只在补位 pass
里被失败段查询。这一刀同时消掉 R7 的两个反例——主池任何段的 top-24 成员不变，
确定性承诺从「台词/锚点段不变」恢复为全称真；`np.argsort` 非稳定排序的 ties 只在
补丁池内部存在，而补位结果排序用显式 tiebreak `(-score, key, start)`，与新产物
无历史包袱。

```python
# clips.py run() 的控制流改造（N2 修复：插入点按真实控制流钉死，不再横跨）：
#
#   现有顺序：parse_shots → pools 组装 → load_sources → 加载索引 → prep 循环
#            （锚点段在此解析 anchor_cands）→ 锚点预占 placed → by_index
#            → live = [hits 及格段] → quota → 12 轮分配循环 → status 赋值 → out
#
patch = ingest_patch.load_pool(episode)      # ⓪ 提到 parse_shots **之前**（N4-b）：
                                             #    锚点解析要吃 extra_pools，prep 要吃 sources
if patch:
    pool = patch["pool"]
    # **pools 组装跳过补丁池**（R2，第五堵墙）：补丁池登记在期级 pool.json，
    # 全局 sources.json 永远没有它——进 pools 就会被 load_sources_multi 拖到
    # SystemExit「先跑 ingest sources」，报错还指错方向（红线：补丁池不写全局库）。
    # pools 只数全局池；补丁 sources 只走下面这一条 update 路。
    sources = {(anime, k): v for k, v in sources.items()} if 单番平面 else sources
    sources.update(patch["sources"])         # 复合键合并，必须在 prep 之前
    allow = set(animes) | {pool}             # 补丁单元 anime=pool；不进 allow 集合，
                                             # candidate() 第一道番名校验就把它退成 None（N2-a）
#   prep 循环（原样；锚点段此时已能解析补丁池锚点，见下「锚点直通」）
#   锚点预占 placed（原样）

if patch:                                    # rescue-A：只救检索失败段
    for p in prep:
        if p.get("channel") == "anchor":
            continue                         # 锚点段缺料交人补锚，不自动救
        if p["hits"] and p["top_score"] >= p["threshold"]:
            continue                         # 检索及格的不碰。**判据用检索侧条件**——
                                             # status 此刻还没赋值，p["status"] 不存在（N2-c）
        q = p.get("scene") or p.get("query") or p["text"]   # 场景 > 查询 > 配音原文
        hits = vindex.search_scene(q, patch["vecs"], patch["units"], TOPK)
        hits = sorted(hits, key=lambda h: (-h[0], h[1].episode, h[1].start))
        hits = [(s, u) for s, u in hits if s >= patch["floor"]]
        if hits:
            p.update(channel="scene", hits=hits, top_score=hits[0][0],
                     threshold=patch["floor"], via="patch-rescue", rung=1,
                     used_query=q)            # B1：终端报数/review 页显示的必须是
                                             # 实际命中的补丁查询，不是旧的主池失败查询

by_index = ...; live = [...]                 # rescue-A 必须在 live 计算**之前**（N2-b）：
quota = {p["index"]: p["duration"] for p in live}   # 被救段在此之后才进 live 就进不了分配
#   12 轮分配循环（_allocate(live, by_index, sources, allow, quota, pre=placed)）
#   status 赋值（原样）

if patch:                                    # rescue-B：救分派失败段（Y5；R1 修复）
    # **no_source 段：重置无损**（原 hits 已被占完，一个片段都没分到）——重置 hits、
    # 转 scene 通道、进 live、重跑一轮 12 轮循环。
    # **short 段：禁止重置**（R1）。short 是「填不满但填了一部分」——size() 判 short 时
    # 保留已选片段，那些是逐台词标定门槛命中的主池好画面；重置 hits 会把它们扔掉，
    # 换成纯补丁画面（70 分补成 0 分重写，还把主池镜头释放给别段——分派格局被改写）。
    # short 走**追加补尾，不调 size()、不动 hits**（R1 定案）。两次验伤：
    # ① size() 在**所有**返回路径上都对 clip dict 执行 pop("limit"/"span"/"floor")，
    #   且 p["clips"] 与 used 共享同一批对象——short 段的 clips 此刻已无 limit，
    #   再进 size() 第一行 room = [c["limit"] - c["start"]] 即 KeyError；
    # ② size() 是全局水填重排（base/extra/权重/forward-pull 改 start），老 clips 的
    #   dur/start 必变——与「原 clips 逐字节保留」的断言数学互斥。
    # 正确做法（~20 行，全部手工）：residual = need − Σ现有dur；逐条对补丁命中调
    # candidate(floor=补丁floor)（新 dict 字段齐全）→ _overlaps(used) 查重（anime
    # 短路，安全）→ append_dur = min(候选 room, residual)，residual 或可给时长
    # < MIN_CLIP 时不追加（闪帧不如保持 short 交人）→ 新 clip pop 掉
    # limit/span/floor（与老 clips 同形）→ 追加后重判：Σdur 达标改 ok，否则仍 short。
    # 老 clips 的 dur/start 一个字节不动，补丁片段只可能接在尾部。
    starved = [p for p in prep if p["status"] == "no_source"]
    shorts  = [p for p in prep if p["status"] == "short"
               and p.get("channel") != "anchor"]      # 锚点 short 段不自动补尾（Y1-r7）：
                                             # 锚点是「不可替代的 Ground Truth」，自动接补丁尾巴
                                             # 等于拿人肉锁定给未标定的补丁镜头引流。
                                             # 与 rescue-A「锚点段缺料交人补针」同一条纪律；
                                             # 锚点 short 照旧交 05（补锚点或接受 short）。
    ...
    # 确定性论证不变：补丁 clip 与主池 clip 永不 _overlaps，ok 段不受影响，
    # 「非失败段逐字节不变」仍全称成立。
    # 与既有 _rescue_starved（台词续爬）无冲突 (B3)：被救段已转 scene 通道会被
    # 续爬跳过，已爬尽的段 step 到顶不再追加——不存在双重追加。
    # rescue-A 救起段的残留字段顺手清理 (B4)：rung 重计为补丁池语义、旧的
    # ep_scope/ep_fell_back（主池降级痕）一并重置，别让 05 看到自相矛盾的字段组合。
```

分配机制 (Y6)：被救段的 `threshold` 就地换成补丁 floor，`candidate()` 的
per-hit 门槛自然正确（每段单一门槛的既有语义不变——段已整体转成 scene 通道）；
`_allocate`/`_overlaps`/`size` 原样消费（Shot 同形契约成立），补丁 clip 的
`anime=pool` 名使跨池撞车误判不可能。

**`load_pool` 的失败路径** (B1)：04-patch 目录存在但索引缺失/半建 → SystemExit 的
文案必须指路「删 04-patch 目录，或重跑 `python -m pipeline.ingest_patch <期>`」——
让 clips 整期跑死可以，报错不许不指路。

**ties 残余** (B3)：显式 tiebreak 只管 search_scene 已返回的 top-24 内部次序；
同分跨 24/25 边界的成员资格仍由非稳定 argsort 决定——补丁池 ≥24 个同分镜头才触发，
记录在案不处理。

**门槛与三闸**（Review 判决：原方案打回，加闸后可接受）：
- pool.json 携带 `no_match`：主番已标定 → 沿用并在 pool.json 记
  `"no_match_source": "<主番>"`；主番未标定 → ingest 时**必须显式 `--floor <值>`**
  并记 `"calibrated": false`（R4：绝不调 `scene_no_match`——它对未标定番
  SystemExit，会把整期排片跑死，比没有补丁通道更糟）。
- 闸 1：`load_pool` 强制打印零向量占比（检索池缩水的同族病），>10% 出 WARN 横幅；
- 闸 2：补丁命中段的 `score` 与 `floor` 同时进 04-clips.json
  （「0.667 过线 0.005」这种擦边球必须在 05 可见）；
- 闸 3：**「补丁段」的判定 = 段有 `via="patch-rescue"` ∪ 段的任一 clip 的
  `anime` == 补丁池名**（Y1-r5：两条进片路径各管一段——自动补位走 via，
  手写补丁锚点的段没有 via，只认 via 会让锚点补丁段整队逃逸出三闸）。
  review 页补丁段自动标「补丁·必审」，`--approve` 时存在补丁段要显式二次确认
  （人眼替代标定从口号变成门禁）。锚点补丁段的闸 2 无 score 可落（score=None），
  落 clip 的 pool 名即可。
- 余量声明：一期补 3–5 段时此设计成立；若某期 40% 的段靠补丁，
  「重点审」退化为「全片重审」——这时正确动作是给该池跑 `vprobe scene` 正规标定，
  runbook 写明这条升级路径。

**锚点直通补丁池**（面试题 9 的落实；N4 修复，~5 行是幻想，真实 ~25 行已计入 §1.2）：
语法是 **`锚点: <pool名> SP03 1:20`**（`_ANCHOR` 是空格分隔，v1.1 写的 `@` 是 doc bug）。
四关都要过：
① `load_pool` 提到 `parse_shots` 之前，池名传入 `extra_pools`——否则 `_parse_anchor`
对照番表校验直接 SystemExit；
② sources 合并在 prep 之前（见上控制流）——否则 anchor_cands 查不到补丁集键，
锚点段落 no_match 终态；
③ `_anchor_candidate` 加 `shots_table` 参数：补丁池走 04-patch 本地镜头表，
**绕开全局 `shots.load`**——它会对池名做 threshold 对账然后 SystemExit（R8 同一堵墙，
ingest 路径修了、锚点路径上不能忘）；
④ 测试一条：锚点指向补丁池的解析 → 命中 → 与主池锚点的撞车检测。

### 4.5 渲染链路（维持已核实结论）

clip 自带 `source` 绝对路径，渲染不查 sources.json；`_source_path` 只服务 BGM 段；
`_overlaps` 键含 anime，补丁池与主池不互撞。
**已知残余风险**（B1 后半，记录在案不拦工）：qc 的 SP 暗场豁免认 `c.get("sp")`，
补丁实拍暗场会按番剧 0.5s 标准判，可能误报 FAIL——出现时再给 qc 加 pool 维度。

### 4.6 云端执行面 (R9)

- (a) 上行默认清单加 `03-audio/corrections.json`（§3.6）与 `04-patch/` 产物目录
  （ captions 云端跑需要 shots/frames 上行、产物下行）。
- (b) captions 需**两件事同时做，缺一不可（R1-r15）**：
  ① **`ALLOWED_TASKS` 加 `"captions"`**（`cloud.py:39`，当前只有 `tts`/`probe`）——
     这是**必须**的，因为 `cmd_run:1173` 在分发之前就调 `validate_task_whitelist`，
     不放行则永远走不到分支代码（`build_remote_run_command:361` 还有第二道，
     同样拒）。只加分支不加 token = 死代码；只加 token 不加分支 =
     `python -m pipeline.captions` 模块不存在。
  ② 加**专用任务分支**（与 probe 同构）：`task == "captions"` → 拼
     `python -m pipeline.vindex captions <pool> <key> --shots-dir … --frames-dir
     --out-dir …`，参数逐项白名单化，**所有路径参数过 `shlex.quote`**
     (B7-r5：期目录名含空格/中文时远端 shell 炸，既有 tts 分支同病——
     不是本 spec 引入，但新分支别再踩一遍）。
     **`ALLOWED_TASKS` 加一个 token 不等于「允许任意任务」**：它仍是闭集，
     与前门校验同层——两处一起改才是完整补法（原 spec 那句
     「不是往集合里加字符串」把前半件劝退了，是本文的错误表述）。
  ③ 两段式的 stage 2 还依赖 `cmd_pull` 的三态可辨（见 §4.3）。
- (c) `extra_args` 过参数白名单正则（§2.4 第 2 条）。

- (d) **上行余量 pre-flight（B4-r15 的落地点，PR0 欠的那句话）**：全季打标前必须先把远端
  `data/` 从**系统盘**改道数据盘（`config/cloud.json` 的 `remote_data` **有意未接线**）。
  落点不在本 spec，而在操作类文档 `docs/WORKFLOW.md` §一「阶段 0 前置」——
  工序卡原先从「A. 脚本与分镜 / 01 选题」开始，**阶段 0（素材入库与全季打标）整个缺失**，
  这才是「改道数据盘」一直没被人在开工前读到、将来会「怪到 ava 头上」的根因。
  `cloud push` 同时新增前置余量闸：**判据必须落在目标盘而不是数据盘**——
  仓库里所有 `df` 只查 `autodl-tmp`（`cloud.py` doctor/status 两处），于是它会打
  「数据盘空间充足 ✓」而帧正把系统盘写满；这条闸把这个盲区补在入闸处。

- **`pool.json` 同病（B1-r16）**：ingest_patch 的登记是读-改-写，而 clips 在另一侧读；
  且云端两段式让窗口更长（提交 → 人工重跑）。修法与 corrections.json ①同一句话：
  **写回前比对 mtime + sha，不等则 FAIL 并让重跑**（不静默覆盖）。

---

## 5. 分阶段施工里程碑

### PR0：文档收口（半天，先于一切代码）(B8)

- 施工图 §3 改为指向本 Spec §3 的一句话 + §7 完成判定换成 CLI 链路验收项；
- 施工图 §2.1 的 `ava new` 与本 Spec 对齐（B1-r8）：Spec 侧补上——`ava new <期号>`
  = mkdir + 写 01-topic.md 模板（进 PR1，见下）；**重名期目录必须拒，不许覆盖现有期**
  (B3-r9)；
- **改写 `CLAUDE.md` 第 235 行的纠错手册，并逐字同步到 `AGENTS.md`（R1-r12，两份
  必须一致——STANDARD.md §九明文记过 2026-08-21 的漂移事故）**：
  · 错字处置改为「`/voice` → `corrections.json` → `--apply-patch`」；
  · 写明两层关系：g2p 全局表仍是**长期沉淀层**，但**期级 overlay 对同名键优先**
    ——改全局表不会改动已有 overlay 的段（这正是旧手册会静默失效的地方）；
  · 顺手修正同处的 `ava-cloud push/run/pull`：这两个别名在 pyproject 里不存在，
    写成 `python -m pipeline.cloud push/run/pull`（Y1-r11 定的入口基准）。
- **改写 `docs/runbook/03-tts.md` 的「核心规程与铁律」（R1-r13，第三本手册）**：
  它 27 行、就活在 INDEX 的「03 语音合成」入口上。三处要对齐：
  · 铁律 1 的「某段重录只需**删除对应 `seg-XX.wav`**」→ 改为
    「点名重配：`/voice` 纠错 → `--apply-patch`，或 `python -m pipeline.tts <期> --redo XX`」；
  · 铁律 2 的「多音字错音修正：通过 `pipeline/g2p.py` 注入拼音」→ 补齐两层关系
    （**全局表 = 长期沉淀层；期级 overlay 同名键优先**，改全局表不动已有 overlay 的段）；
  · 执行命令里的 `ava-cloud push/run/pull` → `python -m pipeline.cloud push/run/pull`
    （这是同一条不存在别名的**第二处**，与 CLAUDE.md:235 同错）。
- runbook 03.5 重写（含 corrections.json 永久资产纪律、云端侧别纪律、**总时长预算
  与三项抽检定位**，Y1-r12）；
- **导航一致性子任务（Y2-r12）**：README 流程图/步骤表、`docs/INDEX.md`、
  `docs/WORKFLOW.md` 的 03.5 行同步为「顺听 + /voice 纠错（可选深挖）」；
  `docs/dev/STANDARD.md` §九表里补 `docs/runbook/` 与导航类文档两行（B2-r12：
  它们不在清单里却是实际漂移面）。
- **顺带同步仓库外的两处（B1/B2-r13，判据射程外但人真会读到）**：桌面那份
  `AVA_PR_SPRINT_PROMPTS.md` 加一个指向 §3 的指针（默认三项抽检 + /voice 纠错）；
  `CLAUDE.local.md`（本机事实类，不进 git）若含旧 03.5 描述同样处理。
- **两篇 ADR（Y1-r17，符合 STANDARD.md 的 ADR 标准：「只记难以回头的决策」「必写
  推翻条件」）**：
  · **ADR-0018「ava 统一 CLI 入口与三条 harness 护栏」**——Code Freeze / 配音纪律
    双层闸 / 封面只出候选；**推翻条件**：若 Code Freeze 的 git 判据在真实工作流中
    误报率高到被无视，则改为 worktree 物理隔离 + CI 检查；
  · **ADR-0019「期级 overlay 与 corrections.json 生命周期」**——把 §3.3/§3.4/§3.5 的
    优先级（overlay 同名键压全局）、永久资产、applied/进度模型、attic 保留窗四条纪律
    收进一处；**推翻条件**：若逐段 scope 被证明在实践中总需要全局联动，则改成
    默认 global + 显式 segment。
  · **同步面**：`README.md:101` 手工列着 ADR 编号（0014–0017），新增两篇要点到那里；
    `docs/dev/STANDARD.md` §九的文档标准表顺带确认 ADR 的维护/同步责任人 (B3-r17)。
- 本 Spec 状态改「已评审，开工」。
- **完成判定（v1.13 重写：从字面判据升级为「白名单 + 变体组 + 人核清单」）**：
  ① **按「记录 vs 规程」划线**（这条同时消掉自指与漏检，Y1-r13）：
     规程只活在**操作类文档白名单**里，判据只在白名单内扫——
     `CLAUDE.md`、`AGENTS.md`、`README.md`、`docs/INDEX.md`、`docs/WORKFLOW.md`、
     `docs/runbook/*.md`、`docs/dev/{STANDARD,HELP}.md`、`docs/dev/plans/`（施工图）
     与 `docs/dev/postmortems/`、`docs/adr/` 是**记录**，天然豁免；
  ② **变体组覆盖四种写法**（单串 grep 是字面测字面，项目在 CER、`_report_stale`
     上都栽过同一形状）：`rm seg` / `删除.*seg` / `--redo`（旧路语境） / `ava-cloud`；
  ③ **判据要打印命中清单**，不只给红/绿——「这几处是历史的、那几处是活的」由人核，
     与「门禁只说不过不说为什么等于逼人重跑」同规；
  ④ **固化成测试 `tests/test_docs_invariants.py`，进总闸（Y2-r13；断言形态 Y2-r14）**：
     **必须有正向断言——只写反向（「不含旧串」）的判据，删掉那份文档就全绿**：
     · 正向：`OPERATIONAL` 清单里每份文件 `exists()`、正文超 N 字、**且在 git 里**
       （`git ls-files` 命中，B1-r14——否则 `git mv` 走就同时绕过清单与断言）；
     · 正向：每份操作类文档**必须出现新路锚点**（`--apply-patch` 或
       `corrections.json` 至少其一）——「讲了新路」是事实，「没讲旧路」只是它的影子；
     · 反向：镜像逐字一致（首行标题除外）+ 不含旧手册变体串；
     · **变体组只收「无法在合法语境出现」的串（Y2-r14）**：`rm seg` / `删除.*seg` /
       `ava-cloud`。**`--redo` 从变体组里拿掉**——它是**现行合法机制**
       （`tts.py` 的 help、§2.4 的放行正例、§3.5 的 redo 语义都用它），
       而「旧路语境」不是机器可判的谓词；照字面留着会逼文档绕着判据写，
       那是判据设计失败的典型症状。每条串都要能回答「它在什么合法语境下会出现」
       ——答不出来的串不许进判据。
  ⑤ **PR0 完成 = 判据全绿 + 人工核一遍命中清单 + 清点 9 项收口**（B2-r14：
     把「清单的完成」也变成能跑的判据，不只前半句）；`docs/dev/HELP.md` 在清单里
     但它自称「一条命令行 pipeline…中间九步」的概览——**PR0 顺手一句话写明它的定位
     与要不要提 ava**（B3-r14），否则它是下一个漂移面。
  ⑥ **同步 issue 台账 + 更正 CLAUDE.md 的错规则（用户 2026-09-19 以约 20 期实践
     否证，Y1-r19/v1.20）**：
     · `docs/dev/issues/README.md` 的 B3「每期人时『≤10 分钟』从未端到端验证过」
       → 改为「该口径**已被实践否证**（2026-09-19）：人类时间 ∝ 成片时长，
       20 分钟片的光 03.5 就 ≥30 分钟；读数已接线（§2.6），待积累 k 实测值」；
     · `CLAUDE.md:50` 与逐字镜像 `AGENTS.md:50` 的替换文本（两份同改）：
       「**每期人类投入按成片时长核算**（2026-09-19 更正）：原「≤10 分钟」是未验证的
       估计，被约 20 期实践否证（20 分钟的片光 03.5 就 ≥30 分钟）。预算 = `k × 片长`，
       k 由 ava 的 `human_time.json` 实测回填（暂以 03.5 ≈ 1.5×片长 为锚点）。
       超预算 = 流程有 bug：改流程或砍环节，不是加时间；连续 3 期超预算**率**上升
       → 停止产出，回头修流程。」
     · 同文件第 53 句「现在唯一活着的止损线是上面那条 10 分钟」→「上面那条
       **按片长核算**的人时预算」。
     · 改完跑 `tests/test_docs_invariants.py`（镜像逐字一致那条会当场盯住两份是否同步）。
  可跑形态（在 bash/zsh 下均成立，B4-r13）：
  ```bash
  python -m pytest tests/test_docs_invariants.py -q   # 判据本体在这里，不靠 shell 花活
  # ADR 编号连续 + 两篇新 ADR 带「推翻条件」小节（Y1-r17，与 r13 那两条判据同规）
  ls docs/dev/adr/ | grep -E '^00(18|19)-' && grep -l "推翻" docs/dev/adr/00{18,19}-*.md
  ```
  **「不再有第二本手册」必须是能跑的判据，且判据自己也要经得起自测。**

### PR1：ava 骨架 + StateResolver + REPL

**文件**：`pipeline/agent/{__init__,cli,resolver,scopes,tools}.py`、
`config/agent/{tools.json,scopes/*.md}`（**护栏层提到 PR1，R1-r18**）、`pyproject.toml`、
`pipeline/status.py`（advisories，§2.2 三条纪律）、`tests/test_agent_{resolver,cli,tools}.py`。

> **为什么 tools.py 与 config/agent/** 必须在 PR1（R1-r18）**：PR1 要验收的每一条
> 护栏（子命令白名单粒度、`cloud exec`/`down --force` 拒收、`--force` 拒收、
> `extra_args` 值白名单、`write_episode_file` 越界校验）都是「工具注册表 + 白名单
> 执行器」的行为。护栏排在 PR4 = PR1 交不出自己的测试，且 PR2/PR3（唯一真会调
> tts/clips 写产物的两个 PR）期间护栏没有代码载体。**一个文件两个 PR：白名单执行器
> 与值域属 PR1（护栏可测），LLM 面向的 schema 与业务实现属 PR4**（B2-r18：
> `tools.json` 是执行器与 LLM 的共同输入，**改它 = 改护栏**，不是普通配置）。
> 顺带消解 B1-r18：`scopes.py` 在无 `config/agent/scopes/*.md` 时的行为不再需要
> 单独定义（prompts 也在 PR1）。

**测试**：`scope_of` × **12** 种 current_step 全枚举 (B6)；三条 advisory 各自
触发/常驻/坏文件免疫 (Y7/Y8)；看板 mtime 排序与回车选择、看板选择输入语义
（回车/数字/期号/非法输入重提示，B2-r8）；REPL 路由与未知指令；
`ava new <期号>` 建目录 + 写 01-topic.md 模板 (B1-r8)、重名期目录拒建 (B3-r9)；
**asset scope 的子命令白名单要到子命令级**（B3-r9）且 **faces 必须五个子命令全在**
（Y2-r10：detect/cluster 是 GPU 小时级重活，只留终点等于砍掉最该封装的一半）；
**`cloud exec` 必须在拒收名单里、`cloud down --force` 也在**（R1/B1-r10）；
/run 白名单拒收 `--force` 并给出 `--redo` 指引、`check_script` 在白名单内 (B7)；
**asset scope 白名单含 Phase 0 命令**（Y1-r8：进得去就得跑得了）；
**人时记账（Y1-r19）**：停机点墙钟 → `human_time.json` 追加、看板读数、超预算触
第四条 advisory、连续三期超预算横幅；计数口径只含人类停机点（机器时间不计）；
**出网边界（Y2-r19）**：断言唯一出网的是 creative LLM 请求，且
`pipeline/`、`config/`、`03-audio/` 的内容不在任何交付给 LLM 的工具返回值里；
cloud extra_args flag+值对白名单：`--redo 3,7` 与 `--redo stale` 双双放行
（正例必须有，stale 是 tts.py 自家关键字，Y3）、`--redo "3,7 && curl x|sh"` 拒收、
无值旗标带值拒收；`write_episode_file` 三种越界各抛错 + 写 01-topic.md 要人确认 (B7)。

**验证**：
```bash
python -m pytest tests/test_agent_resolver.py tests/test_agent_cli.py tests/test_agent_tools.py -q
ava && ava data/episodes/<EGOIST三期>     # 卡片 + advisories 正确
# **负例（护栏真的在拦人，B4-r18）**：写路径的拒收必须能现场演示
ava data/episodes/<EGOIST三期> /run tts --force-all   # 必被拒，并指引 --redo
```

### PR2：顺听纠错链路（最大痛点）

**文件**：`pipeline/corrections.py`、`pipeline/tts.py`（speakable 接管 +
`--apply-patch`）、`pipeline/agent/cli.py`（/voice）、`pipeline/cloud.py`（R9a）、
`tests/test_corrections.py`、runbook 03.5。

**测试**（含 Review 新增弹药）：
- 解析器 ≥22 例：三个示例句；段号写法全枚举（`5段`/`第 5 段`/`段落 5.1`/`段 5`
  ——Y1 回归位）；「5段和7段都念错了」→ PatchError「一次只纠一段」(Y2)；
  `５段`（NFKC 归一）；`第五段` → PatchError；有 heard 无 target → PatchError；
  `zhòng，dié`（NFKC 后单簇）；两个无关键词拼音簇 → PatchError；
  「音色变了」→ PatchError 且消息列全词表；「9段 有点快」→ 语速词明确报错 (R2)；
  词不在段内 → PatchError 打印该段原文；小数段号与句级键 `21.2`；「全局」二次确认；
- 音节切分器：`zhòng dié`→`zhong4die2`；`zhong4 die2` 幂等；`zhòngdié`（无空格
  但每音节带调号）可切；`xī'ān` → PatchError；`lǜ`→`lv4`；
- **接线级**：假 Engine 的 `synthesize` 收到的 text 含 `zhòngdié` 渲染形（R1 的
  本质测试）；`_reusable` 在 overlay 下对段级 scope 精确标红（段 5 变、段 18 不变）；
- R3：pending 条目在普通 `run()` 下**不生效**（fingerprint 与 speakable 双断言），
  `--apply-patch` 路径生效且有 attic 备份；
- 幂等：apply 中途失败重跑，已 done 段不重复合成；**global 条目的进度模型**（R2）：
  affected 落盘、done_segments 逐段追加（set 语义）、done<affected 时条目不算 applied 且
  advisory 计入 pending 并列出剩余段；重跑 --apply-patch 续做剩余段；
  **pin_seed 条目的报账名单必须含该段**（Y1-r6：redo 判据含钉种子轴，
  报账与实际重配同一真源）；
  manifest 缺失 → 拒绝并指路 (B3)；同段同词重复纠错 → 覆盖 WARN (B4)；
  heard 宽校验：「念成 xī'ān」原样落盘不报 PatchError (Y1)；
- **回滚再战不死锁** (R1-r5)：apply 完成 → 回滚 5 → 不改参数直接 --apply-patch，
  段 5 必须进 redo 重配（判据减法，done_segments 不参与）；
- **/voice 路由** (Y2-r5)：「听 5」→ 播放指令、「停」→ kill、「听起来第5段发飘」→
  pin_seed 纠错、「回滚 5」→ 回滚、「听 12.3」/「回滚 12.3」小数段号正常生效
  （Y2-r7）——指令表全部 fullmatch 测一遍；
- **报账闸对新特征有效（R1-r17）**：造一条 `synth_logic=5` 的旧 Take + 一条 overlay
  生效的段 → `_report_stale` 必须把旧段列出来——它同时证明「bump 到 6 之后，报账闸
  对 overlay 维度不再失明」（现在这条测试不存在，因为 spec 之前没提这个号）；
- 写者并发（Y1-r16）：写前指纹不等 → FAIL 不静默覆盖；apply 期间 `/voice` 拒 y；
  y 后回读确认条目在盘；
- 回滚/撤回语义各一条 (B5)。

**验证**（/tmp 副本；**抽检式，不是全量**——别让验收路径示范全量，Y1-r12）：
```bash
cp -r data/episodes/<期> /tmp/ava-p2 && ava /tmp/ava-p2 /voice
# 按 03.5 三项抽检：听 <最长段> 单段复查 → 敲一条真实错读 → /done
# → 观察只重配目标段、attic 备份存在、
#   再跑 python -m pipeline.tts /tmp/ava-p2 全 skip（pending 已沉淀、无漂移）
```

### PR3：补料通道 + rescue 补位（视频 only）

**文件**：`pipeline/ingest_patch.py`、`pipeline/clips.py`、`pipeline/vindex.py`、
`pipeline/shots.py`、`pipeline/cloud.py`（captions 分支）、`pipeline/review.py`、
`tests/test_ingest_patch.py`、`tests/test_clips.py`（补位用例）、runbook 04。

**测试**：
- `pending_assets`/pool.json round-trip；size+mtime 漂移 WARN (B4)；
  图片文件 → 报错且消息含 ffmpeg 转换命令 (R5)；pool 名非法字符报错 (B3)；
- `load_pool`：scene 行数 ≠ shots 数 → 报错；零向量占比 >10% → WARN（闸 1）；
- `candidate()` SP 键（season=None 走 `shots._key`）；
- 补位 rescue-A：假向量下检索失败段被补丁命中救起、带 `via=patch-rescue`、
  used_query 写补丁查询 (B1)、score/floor 双落盘（闸 2）、终端带「[补丁补位]」(B2)；
  **无补丁期输出逐字节不变**（快照对比）；**主池场景段在补丁在场时逐字节不变**
  （rescue-only 的确定性断言，R7）；补丁池内同分 ties 的确定性次序（显式 tiebreak）；
- 补位 rescue-B（R1 定案）：**short 段补尾不调 size()**——断言补尾后老 clips
  逐字节保留、补丁片段接尾部、新 clip 的 limit/span/floor 已 pop 成同形；
   residual < MIN_CLIP 时不追加保持 short；no_source 段重置救起；ok 段逐字节不变；
  **锚点 short 段不被补丁补尾**（Y1-r7：shorts 集合排除 channel==anchor）；
- **锚点写补丁池名 → pools 组装全程无 `load_sources_multi(补丁池)` 调用**（R2）；
- 主番未标定时 ingest 无 `--floor` → 报错指引，有 `--floor` → pool.json 记
  `calibrated: false`，clips 全程不 SystemExit (R4)；
- approved 过期三层闸：clips 重跑 WARN；status advisory 常驻；render 硬闸
  **以内容 diff 为本体**：04-clips.json 缺失不比（兼容 test_render.py:349/374 既有
  fixture）、内容相同放行（cp -r 副本不误伤）、diff 非空才 SystemExit (R3)；
- **手改检测闸** (R1-r8)：05 手改过 04-clips.json（非失败段与上游快照 diff 非空）时，
  补丁入库后的自动重跑必须列出段号并停下交人定；未手改时自动重跑直通；
- 锚点指向补丁池：解析（extra_pools 含池名）→ 命中（sources 已提前合并）→
  `_anchor_candidate` 走本地 shots_table 不碰全局 `shots.load` → 撞车检测 (N4)；
- `load_pool` 半建索引 → SystemExit 且文案指路「删 04-patch 或重跑 ingest_patch」(B1)；
- **云端路径连通性（R1-r15，两件事钉在一条测试里）**：`ALLOWED_TASKS` 含 `captions`
  **且** `build_remote_run_command("captions", …)` 产出的是
  `python -m pipeline.vindex captions …` 而非 `python -m pipeline.captions`——
  只改一处就是同族事故；
- **两段式三态可辨（B3-r15）**：同一命令在「实例关机」（FAIL）与「任务进行中」
  （退出码不同、「等待中」文案）两种状态下行为可区分——与 probe/tts 自检同规；
- review：补丁段「补丁·必审」标 + approve 二次确认**仅当存在补丁段
  （via="patch-rescue" ∪ 任一 clip 的 anime == 补丁池名）才触发**（Y2-r6 口径同步；
  B5：无条件 input() 会把既有 approve 测试卡死）——**锚点补丁段（无 via）的确认
  用例必须有**，不然闸 3 的放宽在测试层落空；
- runbook 04 写明「05 回改 02-script.md 加补丁锚点」的流程：改稿 → 重跑
  check_script → 重跑 clips（vo_hash 只盖配音行，不受影响——B6，已核实
  `compute_script_vo_hash` 只提取 `配音：`行）；给一个**净化后池名**的锚点示例
  （如 `锚点: EGOIST--patch SP03 1:20`，B2：双横线手写易错，示例比 regex 好使）；
  **先出补丁画廊再写锚点**：`shots.gallery` 已全部参数化可直接复用，runbook 补
  一行命令（B8-r5）——手写锚点前人需要先看镜头联系表，不然锚点直通的工作流不闭环。

**验证**（**云端栏：仓库内一次性验证期，不能放 /tmp**——cloud 拒仓库外路径，Y1-r14）：
```bash
cp -r data/episodes/<期> data/episodes/_ava-verify-p3
mkdir -p data/episodes/_ava-verify-p3/patch_assets
cp <3分钟切片.mp4> data/episodes/_ava-verify-p3/patch_assets/
# 成本提示（Y1-r15）：本次验收 ≈ ¥0.4–0.85 —— 实例 wall-clock 约 10–20 分钟
#（开机 + 上行帧 + 加载 15GB 模型 + 打标 + 拉取），是单期预算 ¥0.35 的 1.1–2.4 倍。
# 闸（budget_per_phase0 25 元）看不见它，因为它只数生成秒数（¥0.063）。
# 纪律：用最小切片（3 分钟、单资产）；验收完 5 分钟内 pull + down
#（watchdog 300s 空闲即自关）；超时重开一次机 ≈ 再 ¥0.1–0.2。
python -m pipeline.ingest_patch data/episodes/_ava-verify-p3 --floor 0.60
#   阶段 1 提交后应看到三态可辨的文案；实例关机/任务未完/就绪不得同形（Y2-r15）
python -m pipeline.ingest_patch data/episodes/_ava-verify-p3 --floor 0.60   # 阶段 2：就绪 → pull → embed → 登记
python -m pipeline.clips data/episodes/_ava-verify-p3
python -m pipeline.review data/episodes/_ava-verify-p3   # 补丁段带「必审」标 + approve 二次确认
python -m pipeline.cloud down          # 立刻关机止损；pull 完 5 分钟内完成
rm -rf data/episodes/_ava-verify-p3   # 收尾必删（它不是交付期；看板已排除 `_` 前缀）
```
**B4-r15**：captions 要上行 shots/frames（3 分钟切片约 150 张 896px 帧），而
`docs/dev/postmortems/workflow-history.md` 的「云端中间物」一节记着 `remote_data` **未接线**
（帧级素材走系统盘）——本次验收量级可接受，但**全季打标前先改道数据盘**：
落点已从 spec 移到 `docs/WORKFLOW.md` §一「阶段 0 前置」（见 §4.6 (d)），
并配 `cloud push` 的前置余量闸——不再依赖「人恰好读到本 spec」。

### PR4：LLM 层 + 收尾（**唯一可砍的 PR**）

**文件**：`pipeline/agent/llm.py`（~150 行）、`config/agent.json`、creative 工具的
**业务实现**（`read_artifact`/`search_notes`/`list_episodes`/`read_status`）、
`docs/WORKFLOW.md` 加 ava 入口、向 `tests/test_agent_tools.py` **追加** LLM schema 用例
（B3-r18：「既有测试零修改」——追加不是修改）。

**测试**：llm.py 对 mock HTTP server 测请求格式、密钥只走环境变量；无 agent.json 的
降级路径；creative 工具 schema 与 scope 过滤（白名单/值域部分已在 PR1 测过）。
**机检自愈循环必须有轮数上限**（B6-r7：机检项互相打架时永不收敛——写稿机检 3 轮
仍红交人，与 llm.py 的 max_iterations 是两条不同的闸，都要）。
**验证**：新期全链路跑一遍（/chat → /script → 机检自愈 → 02.5 → /run 配音 →
/voice 纠错 → /patch 补料 → 排片渲染），记录每步耗时。

> **可砍理由（Y1-r18）**：PR4 的 LLM/creative 层与三大痛点里的前两个（顺听纠错、
> 补料断流）毫无关系，且 PR1–PR3 已交付的 `/run`、`/voice`、`/patch` 不依赖它
> ——它是天然的第一顺位可砍项。

### 里程碑总闸（**两档，Y1-r18**）

**每 PR 的工作量与止损条件（Y1-r18；写下来才有可定义的中间态）**：

| PR | 估工时 | 止损条件（超时就把这部分摊到下期） | 做完后 ava 能做什么（可演示判据） |
|---|---|---|---|
| PR0 | 半天 | — | 两份文档不再互相矛盾；判据可跑 |
| PR1 | ≤1.5 天 | 超 2 天：`ava new`/看板装饰性功能延后 | `ava <期>` 能起、能路由、**护栏当场拦下 `--force-all`** |
| PR2 | ≤2 天 | 超 2.5 天：先只支持词级 `pronunciation` 纠错，`pin_seed` 延后 | 一条纠错从 `/voice` 走到音频更新（含 attic 备份与回滚） |
| PR3 | ≤2 天 | 超：先只做视频补料 + rescue-A（rescue-B/锚点直通延后） | 缺口段被补丁填充并带「必审」标进 05 |
| PR4 | ≤1 天 | **整包可砍（第一顺位）** | `/chat` `/script` 能对话写稿 |

**可砍顺序（Y1-r18）**：`PR4 → PR3(补料) → PR2(pin_seed 分支)`；
**底线不可砍**：`PR1 的护栏 + PR2 的 pronunciation`（这两块装上，三大痛点里最大的
那一个就已经解决）。

**两档验收**：
- **底线档**（赶不上时的已定义交付态）：`pytest -W error` 全绿 + 既有 1163 条零修改
  + PR1/PR2 核心的 🔴 变红测试齐；
- **完整档**：+ PR3/PR4 的全部清单。

- `python -m pytest -W error` 全绿（1163 + 新增约 80 条）；
- 既有测试**零修改**通过（接缝改动逼改老测试 = 破坏既有语义，打回）；
- 四大停机点逐项人工过：02.5/05 处 ava 必须停下来等人；
- 每条 🔴 finding 在对应 PR 里有一条**会变红的测试**（第一轮 R1→接线测试、
  R2→语速词报错、R3→pending 不泄漏、R4→未标定不崩、R5→图片报错、
  R6+Y6+R3→approved 过期三层闸、R7→确定性快照、R8→补丁池本地加载链、
  R9→云端三分支；第三轮 R1→short 段原片段保留、R2→pools 组装不炸、
  R3→test_render 既有 fixture 兼容；第四轮 R1→补尾不调 size() 且新 clip 同形、
  R2→done<affected 计入 advisory 且续做幂等；第五轮 R1→回滚后直接 apply-patch
  必须重配该段（判据减法）、Y1→闸 3 覆盖 via ∪ 补丁池 clip、Y2→「听 5」进指令 /
  「听起来第5段发飘」进纠错；第六轮 Y1→pin 条目报账名单必须含该段、
  Y2→锚点补丁段（无 via）触发 approve 二次确认；第七轮 Y1→锚点 short 不被补尾、
  Y2→「听 12.3」/「回滚 12.3」生效、Y3→asset 空表与 pipeline 表只被 /run 消费；
  第八轮 R1→手改检测闸（rescue-only 差异段三成因归因，交人定）、Y1→asset scope
  Phase 0 白名单、Y2→云端打标两段式提交-续跑；第九轮 Y1→路径类值 resolve 存在
  而不做字符正则（asset 命令真能跑）、Y2→远端死任务从「等待中」切出、
  B2→手改闸坏文件 WARN 不裸抛；第十轮 R1→`cloud exec` 不进白名单（护栏自相矛盾）、
  Y1→存活判据用 tmux 会话名而非心跳（心跳是假判据）、Y2→faces 全五子命令
  《compute 半环不可砍）；第十一轮 R1→worktree 必须 `uv sync --extra apple --extra dev`
  （extras 是 opt-in，裸 sync 跑不了总闸与 PR2 真跑）、Y1→/run 基准是
  `python -m pipeline.X` 而非 console script；第十二轮 R1→CLAUDE.md/AGENTS.md 的
  平行纠错手册改写 + `grep "rm seg"` 为空的可机检判据、Y1→03.5 默认三项抽检
  （全量是深挖，PR2 验证走抽检式）、Y2→五处导航 03.5 语义同步；第十三轮 R1→判据
  升级为「操作类白名单 + 变体组 + 打印命中清单」且补第三本手册 03-tts.md、
  Y1→排除项按「记录 vs 规程」划线消自指、Y2→不变量固化成
  `tests/test_docs_invariants.py` 进总闸；第十四轮 Y1→验证矩阵分两栏（云端栏走
  仓库内 `_ava-verify-*`，/tmp 推不上云）、Y2→不变量测试补正向断言且变体组剔除
  合法机制 `--redo`；第十五轮 R1→`ALLOWED_TASKS` 加 token **与**专用分支两件事都做
  （只加分支是死代码，只加 token 是模块不存在）+ 一条「token 放行 + 模板正确」的
  测试、Y1→PR3 验收成本入账（≈¥0.4–0.85 vs 单期 ¥0.35，闸看不见）、Y2→`cmd_pull`
  三态可辨且不无条件打 OK；第十六轮 Y1→永久资产的写者纪律（写前指纹校验 + 单写者 +
  id 去竞争 + y 后回读）、Y2→attic 修剪打印含段号的删除清单且清理责任人写死；
  第十七轮 R1→`SYNTH_LOGIC_VERSION` 5→6 表态 + 此后触发规则 + 报账闸看得见纠错痕迹、
  Y1→两篇 ADR（0018 护栏 / 0019 overlay 生命周期）带推翻条件且编号连续可机检；
  第十八轮 R1→护栏层（`tools.py` + `config/agent/**`）提到 PR1（否则 PR1 交不出
  自己的验收项、PR2/PR3 期间护栏无载体），PR4 缩成纯 LLM 层且成为唯一可砍 PR、
  Y1→每 PR 估工时 + 止损条件 + 可砍顺序 + 两档总闸（底线档/完整档）；
  第十九轮 Y1→§2.6 人类耗时记账（停机点墙钟 → `human_time.json` → 看板读数 +
  第四条 advisory，止损线终于有读数）、Y2→出网边界（唯一出网的是 creative LLM 请求，
  内容 = read_artifact/search_notes 返回文本；其余一律不出网）、
  B1→头部版本号与状态行一致进不变量测试）。

### 被否读法索引（B3-r19）

这份文档最像工程文档的部分：**每条被否掉的设计与它被否在哪一节**。将来有人想
「反向改回去」时，这是唯一的刹车。

| 被否的读法 | 否在哪 | 为什么 |
|---|---|---|
| 「首段完成即标 applied」 | §3.5 (R2-r4) | 把部分完成记成全部完成：advisory 显示 0 条、剩余段带错读音上片 |
| 「全部完成才标 applied」 | §3.5 (R2-r4) | Ctrl-C 窗口对已合成段依旧存在（已合成段会回摆） |
| redo 用名单减法（affected − done） | §3.5 (R1-r5) | 「回滚 5 → 改参数再战」时算成空集，用户被卡死 |
| 补丁向量 vstack 进主场景检索池 | §4.4 (R7-r1) | 主池 top-24 成员会变，确定性承诺碎掉 |
| rescue-B 救援时重置 short 段的 hits | §4.4 (R1-r4) | 丢掉已命中的主池好画面（70 分补成 0 分重写） |
| rescue-B 补尾时再跑一次 `size()` | §4.4 (R1-r4) | `size()` 会 pop 掉 limit/span 且水填重排老片段 |
| approved 过期闸用 mtime 比较 | §4.2 (R3-r3) | cp -r 副本误触、确定性重跑误伤；**内容 diff 才是闸本体** |
| 逐条 overlay 词表全局联动 | §3.3 (Y1-r1) | 会废掉已审听的另一段（词级全局注入） |
| 语速调整（太快/太慢） | §3.1 (R2-r1) | `speed` 系数在合成链上从未被消费；真做要 atempo + refit 联动 |
| 图片资产补料 | §4.1 (R5-r1) | 三堵硬墙（`_parse_key`/duration=0/render ffprobe jpg） |
| `cloud exec` 进 asset 白名单 | §2.4 (R1-r10) | 不接受任何值域、绕过两层闸的任意远端 shell |
| 心跳文件当存活判据 | §4.3 (Y1-r10) | watchdog 无条件代刷——是**会答「活着」的假判据** |
| 固定「≤10 分钟」的人类预算 | §2.6 (v1.20 更正) | **被约 20 期实践否证**：人类时间 ∝ 成片时长（20 分钟片的光 03.5 就 ≥30 分钟） |
| 把「删除 seg-XX.wav」当归档而非手册 | §5 PR0 (R1-r13) | 判据是字面而非事实（旧手册靠中文措辞溜过 grep） |
| `--redo` 进变体组 | §5 PR0 (Y2-r14) | 它是**现行合法机制**，留着会逼文档绕着判据写 |
| 只写反向断言的不变量测试 | §5 PR0 (Y2-r14) | 删掉文档即全绿；「永远绿的断言等于没有断言」 |
| 在 `/tmp` 副本上做云端验收 | §1.4 (Y1-r14) | cloud 拒仓库外路径（resolve_episode_rel_path） |
| 只加 captions 分支不加 token | §4.6 (R1-r15) | `cmd_run` 先过 ALLOWED_TASKS，分支成死代码 |
| 「不 bump SYNTH_LOGIC_VERSION 也说得通」 | §3.4 (R1-r17) | 两读都通 → 必须表态；它背后站着一期混血音频事故 |

---

## 6. 明确不做的事

1. 不引入任何 Agent/LLM 框架、不引入新依赖（llm.py 理由见 §2.5）。
2. 不做 review.html/Webview/本地 HTTP Server。
3. 补丁池永不写入 `data/library/`（sources.json、SHOTS_DIR、VINDEX_DIR 零触碰）。
4. ava 永不生成 `--force`/`--force-all`，永不替人定稿标题封面，永不在 02.5/05
   自动放行。
5. 不做机器自动「听音辨错」——人耳是唯一金标准，机器只提供多音字预检。
6. **不做语速调整**（R2：现有 speed 系数不进合成链；真做需要 atempo + refit
   联动，是独立提案）。
7. **不做图片补料**（R5：三堵硬墙，报错消息给 ffmpeg 转换命令兜底）。
