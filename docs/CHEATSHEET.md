# ava 终端交互与作业极简速查 (CHEATSHEET)

本文档是操作员（Operator）使用 `ava` 宿主进行全流程视频制作的日常速查手册。
最后按实际行为校对：2026-11-21（ADR-0030 仅硬危险弹卡、多槽位云端、多协议 `/login` 与推理控件同步）。

---

## 零、一句话心智模型

> **`ava <期>` 进来就是与 AI 制片总监的连续对话**：直接说白话，不用背命令。
> **斜杠命令仍然全在**：`/` 开头的输入零 token、毫秒级返回，键盘流的路径一条没删。
> **能回退的免卡、硬危险才弹卡**（ADR-0030）：普通期文件写入与本地工序跑批免弹卡（有副作用的先在对话里问人）；只有云端计费/维护、写 `config/`、越过人工闸、企划总装/开单与 `bash`/`chrome` 硬危险操作弹审批卡。
> **底层命令永远存在**：任何步骤都可以用 `python -m pipeline.X` 独立排查或批处理；两者消费相同的磁盘文件，状态完全等价。

---

## 一、终端入口命令（怎么进）

在仓库根目录下，最常用的终端入口只有以下几条：

```bash
# 1. 唤起全局期看板（不带参数）：各期阶段、停机卡点与人时；回车默认选 1，输入 idea 进选题会话
ava

# 2. 直达指定期工作台（进入后即与制片总监对话）
ava <期名或期号>
# 示例：
ava 2026-07-30-春物-雪乃适合大老师

# 3. 立项新一期 / 新建企划与卷（原子创建目录与模板，重名必拒；支持带入选题对话）
ava new 2026-xx-xx-番名-主题标题 [--from-idea[=<会话号>]]
ava new-project <企划名> [--from-idea[=<会话号>]]
ava new --project <企划名> <卷名>     # 新建卷；若目录下已有封面等预设文件但缺 01-topic.md 会直接接管并保留原文件

# 4. 恢复会话
ava <期号> --continue [<会话号前缀>]   # 接着本期上一段会话（ava <期号> --sessions 列出会话）
ava --continue                        # 直达最近写入的期并继续会话

# 5. 无期选题会话（idea scope，用于发散选题、读番剧笔记与比较张力）
ava idea

# 6. 一次性子命令（跑完即退，适合脚本化）
ava <期号> /status    # 打印当期诊断卡后退出
ava <期号> /voice     # 直达顺听配音纠错台
ava <期号> /approvals # 打印当期挂起的停机点审批对象
ava <期号> /approve <stop> --id <id> [--cover <07-cover相对路径> --title <标题>]
ava <期号> /reject <stop> --id <id> <哪段> <问题...>
ava <期号> /scout [--type ...]  /  ava <期号> /patch
ava <期号> /run tts   # ⚠️ 人手敲的这一行 = 已确认，不弹卡，直接执行
```

> `ava <期> /run <cmd>` 与工作台里敲 `/run <cmd>` **不是同一件事**：前者是你亲手敲的命令行，视为已确认；
> 后者会先弹审批卡片、默认 N。其余未被一次性分支接管的子命令在交互 TTY 中会落进工作台；非交互 stdin 会直接报错退出（exit 2）。

---

## 二、工作台交互（进去了怎么敲）

### 1. 两种输入，一条规则

| 你敲的 | 去哪 | 代价 |
|---|---|---|
| `/` 开头的（斜杠命令） | 快捷键路由，现有代码路径原样 | 零 token、毫秒级 |
| 其它一切（含 `run tts` 这种裸命令） | 交给制片总监，它去调工具 | 费一轮 token（裸命令不做特判） |

进入后是 `ava [<期名>] (<scope>) > ` 提示符，scope 按当前阶段自动推导（`creative` 创意写作 / `pipeline` 流水线制片 / `asset` 资产云端）。

直接说白话的例子：

```text
帮我看下当前排片缺口
跑一下排片看看
根据 01 选题出一版草稿
第 5 段发飘，帮我分析一下标点
```

### 2. 斜杠命令全集（与 `/help` 同步）

| 命令 | 适用阶段 | 功能说明 | 典型场景 |
|---|---|---|---|
| `/status` | 全阶段 | 打印 64 宽结构化诊断卡 | 不知道下一步干啥时敲，看【下一步动作】与【推荐命令】 |
| `/approvals` | 全阶段 | 查看当期挂起的停机点审批对象 | 停机点 ack 前看对象清单与 id |
| `/approve` `/reject` | 全阶段 | 批准/驳回停机点：`/approve <stop> [--id <id>]`；`/reject <stop> [--id <id>] <哪段> <问题>` | 02.5/03.5/05/09 的显式 ack，拒因回喂 agent |
| `/board` | 全阶段 | 唤起全局期看板（含各期人时） | 制作中途想查看其他期进度 |
| `/run <cmd>` | pipeline | 手动在工作台执行白名单工序命令 | **先确认 → 敲 y 才真跑**，带耗时统计与尾部日志（agent 经 `run_pipeline` 调本地工序免卡） |
| `/voice` | pipeline | 进入配音顺听纠错台 | 03 配音完成后，戴耳机极简挑错字、改读音、钉种子 |
| `/save-script` 等 8 个停机点子命令 | 内部/脚本 | `/save-script` `/seal-script` `/voice-info` `/voice-parse` `/voice-add` `/voice-revert` `/voice-retract` `/record-time` | 桌面端/无头协议使用的停机点结构化读写入口 |
| `/import-cover <路径>` | pipeline | 导入自备封面图进 `07-cover/import/` | 终端 REPL 导入横/竖底图（桌面端直接拖入预览区） |
| `/chat` | creative | 聚焦选题发散（独立子会话） | 01 阶段梳理核心张力、人物弧光与矛盾 |
| `/script` | creative | 聚焦写稿（独立子会话） | 02 阶段自动调阅选题与资料库笔记，产出 `02-script.draft.md` |
| `/asset` | 特殊 | 切入 Phase 0 资产与云端调度模式 | 注入素材规程（D43 起各模式工具相同；asset 侧命令见下文放行表） |
| `/pipeline` | 特殊 | 退出 asset，切回自动工序模式 | 完成底层资产维护后切回常规制片 |
| `/scout` | pipeline | 生成 pi 侦察派工单 | 缺料/缺笔记/标题候选时生成自包含工单交给 pi 采掘 |
| `/patch` | pipeline | 生成临时补料派工单 | `/scout --type patch` 别名（缺料段派工） |
| `/memory` | 全阶段 | 跨期记忆：`show`（默认）/ `check` / `ack` / `digest` | 查看记忆全文；外部改动后 ack 对齐；聚合驳回反馈提议新条目 |
| `/compact` | 全阶段 | 手动压缩当前会话上下文 | 长会话泄压，保留尾部预算与关键决策，读数带分母 |
| `/model` | 全阶段 | 看 / 切换**本会话**模型：`/model`、`/model <名>`、`/model default` | 按来源分组显示（含搜索与来源筛选）；只影响当前会话。桌面端发裸 `/model` 直接展开模型下拉，点一行切换 |
| `/thinking` | 全阶段 | 看 / 切换**本会话**推理强度：`/thinking <none\|minimal\|low\|medium\|high\|xhigh\|auto\|max>`、`/thinking default` | 请求按服务商协议映射思考参数；`default` = 不发，由模型自定。桌面端工具栏对应 5 格信号强度条 + 分段控件与 `Shift+Tab` 循环（D168） |
| `/login` `/logout` | 全阶段 | 查看 / 登录 / 登出模型服务商：`/login [<provider> [<api_key>]]`、`/logout <provider>`（44 家，D170） | 凭据落 `config/auth.local.json`（0600）；桌面端工具栏「登录」按钮、模型下拉入口或发裸 `/login` 均直开登录弹窗 |
| `/help` | 全阶段 | 查看命令列表 | 随时查看帮助提示 |
| `/quit` `/exit` | 全阶段 | 退出 ava 交互终端 | 进度 100% 依赖落盘文件，随时安全退出 |

- `/chat` 与 `/script` 各自持独立会话历史，**退出后主会话历史不被污染**（进去前聊到哪，出来还是哪）。
- 不认识的斜杠命令（如 `/statsu`）会零 token 报「未知命令」，不会被送去问模型。

### 3. 审批卡片（ADR-0030 起仅硬危险、计费与特例写 config 弹卡）

```text
┌─ 执行审批 ──────────────────────────────────────────
│ 工具: run_pipeline
│ 命令: /…/.venv/bin/python3 -m pipeline.cloud up --provider colab
│ 性质: ☁ Colab (colab) 云端动作（CU 审批）
│ 危险标记: [计费] ☁Colab 运行时将消耗 Compute Units（CU），down 或空闲超时自动释放
└─ 执行? [y=批准 / a=本会话同类都批准 / N]:
```

| 危险标记 | 什么时候出现 |
|---|---|
| `[计费] ☁计费` / `☁Colab…CU` | `cloud up / run / down / clean-frames / relocate-data / sync-code / lib-captions`（7 个卡点子命令，按 `--provider` 与 `--slot` 隔离；`push`/`pull` 不弹卡） |
| `[全局]` / `[全局配置]` | `corrections global`、`calibration set`、`bgm register`、`cloud connect`、`cloud use`（写 `config/` 白名单） |
| `[门禁豁免]` / `[跳过人工闸]` | `acquire register --waive <理由>`、`review --confirm-patch`（含 argparse 前缀缩写） |
| `[JS 执行]` / `[用户既有标签页]` / `[高敏站点交互]` | `chrome` 危险动作：`evaluate`、读/操作非本会话 `open` 的既有标签页、高敏域名上的 `click`/`fill`/`evaluate` |
| `[删除]` / `[写源码/配置]` / `[停机点解封物]` 等 10 类 | `bash` 命中硬危险：删除、`git` 改历史/推远端、提权、全局装依赖、写 `pipeline/`/`config/`、写停机点解封物、直接跑须人审命令、写 `tickets/`、拉起 coding agent、写 `facts.jsonl`/`fetch-log/` |
| `tickets open` / `assemble` / `facts promote` | 人工闸门命令（逐条弹卡，不开放「本会话都批准」） |
| `00-project.md` 机读字段 / 草稿单轮写超 3 次 | `write_episode_file` 正常写盘免卡（留底在 `_agent/`），仅改 `00-project.md` 企划/合集/卷表或单轮重写草稿 >3 次弹卡 |

**按键纪律**：

- **默认 N**：回车、EOF、任何非 `y`/`a` 输入一律不执行——只有单个 `y` 算本次批准，或单个 `a` 算「本会话同类（同工具 + 同危险标记/槽位）都批准」（仅对 `bash`、除 `up --recreate`/`--slot auto` 外的 `cloud` 卡、以及仅含 `[用户既有标签页]` 的 `chrome` 卡开放）；敲 `yes` 不算。
- **日常免卡工具**（ADR-0030）：除上表硬危险与人工闸外，全部只读工具（`read_artifact` / `read_status` / `search_notes` / `list_episodes` / `web_search` / `web_fetch` / `crawl` / `browser` / `view_image`）、常规写入工具（`write_episode_file` / `write_note` / `write_memory` / `cover_edit` / `acquire_propose` / `fact_add`）、普通 `bash`、`chrome` 自建后台页常规浏览，以及本地 `run_pipeline` 工序命令（`tts`、`clips`、`review`、`render`、`qc`、`cover`、`acquire fetch/register` 等）**均不弹卡**——有副作用的由 agent 先在对话里问人确认后再调。
- **拒收不弹卡**：`--force`、白名单外、未注册的工具、无期会话里需要期目录的工具在弹卡**之前**就被拦下，直接 `[REJECT] <具体拒因>` 回喂模型（它分得清「护栏拒的」和「你拒的」）。
- **留痕**：每次弹卡按键都追加一行到 `data/episodes/<期>/_agent/approvals.jsonl`（时间、工具、规范化命令、y/n、决策耗时秒数）——这是审批疲劳的唯一读数来源。

### 4. `/run` 的核心纪律与放行表

**日常不用你敲**（2026-10-09 D59）：下表的命令都由总监经 `run_pipeline` 发，本地工序与只读命令免卡，只有云端计费/维护、写 `config/`、`tickets open`、`assemble` 与越过门禁选项弹卡。`/run <cmd>` 是你在工作台手动调用同一张白名单时使用的（手动敲 `/run` 仍先确认）。

- **放行模块**（与 `pipeline/agent/tools.py` 同步）：
  - 整模块（期目录自动补上）：`check_script`、`status`、`subgrep`（这三个免卡）、`tts`、`clips`、`review`、`render`、`qc`、`cover`、`corrections`（`check` 免卡）、`adversarial`、`fact_review`、`scout`、`ingest_patch`、`tickets`（`list`/`show` 免卡，`open` 弹卡）；不补期目录：`bgm`、`timeline`；企划目录自动补上：`crosscheck`（不带 `--write` 免卡）、`assemble`（仅企划会话，弹卡）；
  - 按子命令（不补期目录，路径要写全；**粗体**标注只读）：
    `acquire` **gate** / check / fetch / register / forget / probe；
    `ingest` **probe** / **intact** / **verify** / subs / run / sources / phase0；
    `shots` build / frames / caption-frames / calibrate / rebuild / gallery；
    `vindex` **who** / **status** / **search** / captions / embed / presence；
    `subindex` **search** / build；`vprobe` tagger / presence / scene / captions；
    `faces` detect / cluster / sheet / name / presence；
    `calibration` **show** / set（写 config 标定值，D60，弹卡）；
  - 整模块不补期目录：`notes_review <番>`（笔记对抗审查，每集一次模型调用，D61）；
    `cloud` status / logs / doctor / up / down / run / push / pull / relocate-data / clean-frames / fix-env / connect（弹卡）/ use（弹卡）/ sync-code / lib-push / lib-captions / lib-pull / hold / release；支持 `--provider <autodl\|colab\|colab-drive>`、`--slot <名\|auto>` / `--instance`、`--recreate`、`--browser-profile`、`--notebook-url`、`--browser-login`、`--drive-root`（`exec`/`attach` 不可经此调）；
- **参数自动补齐**：敲 `/run clips` 会自动注入当前期的完整路径；敲 `/run tts --redo 3` 也会自动注入当期路径；
- **坚决拒收**：`--force` 与 `--force-all` 会被当场拦截，并指引你使用增量参数 `--redo <段号>` 或 `--apply-patch`。

---

## 三、配音顺听极简纠错（`/voice` 模式专用）

03 语音合成完成后，在工作台输入 `/voice` 进入顺听台。

### 1. 听音与控制指令

```text
听 5           # 调用系统 QuickTime 单独打开第 5 段音频（seg-05.wav）复听
听             # 从第 1 段开始自动顺序播放全篇音频（按总时长给出预计时间）
停             # 播放中随时输入「停」叫停音频序列
回滚 5         # 第 5 段改坏了，一键恢复为上一版的备份音频
撤回 2         # 删除指定 id 的纠错条目
done          # 顺听录入完毕，退出并询问是否立即执行增量重配（--apply-patch）
/quit         # 退出顺听模式（已落盘的纠错条目依然保留在 corrections.json）
```

> ⚠️ `done` 的重配确认是 **`[Y/n]`——回车即执行**，与审批卡的「默认 N」相反。不想立刻重配就敲 `n`。

### 2. 人话纠错文法（随手敲，自动生成补丁卡片）

- **字词级多音字/错读（拼音注入）**：
  ```text
  5段 假装 念成 jiǎ 改成 jià
  段落 8 重叠 念成 zhòng 改成 chóng
  12段 银行 改成 yín háng
  # 简写：只写段号 + 词 + 目标读音
  5段 假装 jià zhuāng
  # 若该词在全片多处出现且都念错，加「全局」
  全局 5段 乃 念成 réng 改成 nǎi
  ```

- **听感整句异常（钉新种子，换音色感觉）**：
  ```text
  12段 语气发飘 换种子
  3段 语气发闷 换种子
  ```

- **回显确认**：敲完后终端会打印结构化补丁卡片，敲 `y` 即可原子追加落盘到 `03-audio/corrections.json`；
  顺听完敲 `done`，系统会自动调用 `--apply-patch` 进行靶向重配，**仅重新合成受影响的段落**。

---

## 四、一期成片标准 9 步走查表

| 步骤 | 阶段 | 你在 ava 里做什么 | 产物 | 🛑 停机卡点规程 |
|---|---|---|---|---|
| **01** | 选题 | `ava new <期名>`（或桌面端新建），人填 `01-topic.md` | `01-topic.md` | 张力由人脑判定，严禁机器代填 |
| **02** | 写稿 | 直接说「根据选题出草稿」，总监自己跑 `check_script` 与 02.8 `adversarial` | `02-script.draft.md`<br>`02-adversarial.md` | 机检全绿方可进入下一阶段（条数随判据迭代，以 `check_script` 实际输出为准，不写死） |
| **02.5** | **改稿** | 精修事实（或让总监直接改，旧版留底 `_agent/script-history/`），`/approve 02.5` 自动封板 `02-diff.patch` | `02-script.md`<br>`02-diff.patch` | **🛑 停机点 1**：必须通读改稿，未过 02.5 封板不启动配音 |
| **03** | 配音 | 说「开配」，本地由总监直接跑 `tts`；云端按卡批准 `cloud up/run` | `03-audio/seg-*.wav` | 首次全量合成；后续一律只补点名段 |
| **03.5** | **顺听** | 顺听面板或 `/voice` 顺听抽检，纠错后应用补丁；在 03.5 停机卡确认进入排片 | `03-audio/corrections.json` | **🛑 停机点 2**：抽检开头与最长段，用 `--apply-patch` 重配 |
| **04** | 排片 | 说「排片」，总监直接跑 `clips`（免卡） | `04-clips.json`<br>`04-review.html` | 三通道自动贪心占坑；有缺口由总监跑 `scout` 看缺口、自己查证补料（runbook 04.5） |
| **05** | **审片** | 看 `04-review.html` 抽帧，在决策条批准（或 `/approve 05`） | `04-clips.approved.json` | **🛑 停机点 3**：确认无声画错位后显式 approve 封板（总监不能 approve），审看页缺失前严禁批准 |
| **06** | 渲染 | 说「开始渲染」，总监直接跑 `render`（免卡） | `05-final.mp4` | 自动执行双重切片校验、字幕折行、BGM 侧链闪避（渲染校验 `04-clips.approved.json`、`manifest.json` 与 `vo_hash`） |
| **07** | 质检 | 总监直接跑 `qc`（免卡） | `06-check.log` | 10 项机器硬门禁自动化检验（音画同步、黑帧等），以日志实际输出为准 |
| **08** | 封面 | 总监直接跑 `cover`（免卡） | `07-cover/`<br>`07-titles.md` | 自动提取候选池与 5 条标题建议（严禁机器定稿） |
| **09** | **发布** | 人类挑选标题与封面，手动上传平台 | 各平台稿件 | **🛑 停机点 4**：人类拍板发布，归档本期 |

---

## 五、模型配置速查

优先级：`config/agent.local.json`（本地隔离，不进 Git）> `config/agent.json`（仓库内通用默认）。

```json
{
  "base_url": "http://127.0.0.1:8317/v1",
  "model": "gemini-3.8-flash-high",
  "api_key_env": "CPA_API_KEY"
}
```

- **换模型**：只改 `"model"` 字段（例如切成 `kimi-k2.5`）；
- **请求超时**（D93，可选）：`"request_timeout_s": 300`（默认就是 300，`0` = 不设）。只用来判「连接已死」，别为了「快点失败」调小——慢模型思考期间代理不发数据，调小就是误杀。超时、连不上、429、5xx 会自动重试 3 次（等 2 / 4 / 8 s），对话区出现「自动重试 n/3」，点停止可放弃；
- **环境变量**：在 `~/.zshrc` 里确保 `export CPA_API_KEY="..."`（密钥绝不写进仓库文件）；
- **无模型降级**：未起 CPA 或未配 Key 时，任何自然语言输入都打印 `[降级模式·本地纯指示]` 并给出人工清单，
  **快捷键照常可用**，不抛裸异常；`/chat` `/script` 同样降级退出。
- **网络工具配置同理**：`config/agent/web.local.json` 存在时整份取代 `web.json` 的 `search` / `fetch` / `crawl` / `browser` 段（不做深合并，缺段等于该段没配）；只有 `chrome` 段（`web_chrome.py`）在 `web.local.json` 未写时会回退读取 `web.json`。
- **多服务商登录（D170）**：`/login` 管 44 家服务商凭据（OAuth / API Key / CPA 代理），落盘 `config/auth.local.json`（0600，不进 git）；登录后 `/model` 下拉里直接出现 `<provider>/<model>` 可切。

---

## 六、常见告警与脱困口诀

1. **报 `[REJECT] 禁止在 ava 中使用 --force`**：
   - 永远不要在 ava 中强制全量重配。单段重配用 `/run tts --redo <段号>`；纠错重配用 `/voice` 敲 `done`（走 `--apply-patch`）。
2. **看板出现 `⚠️ approved 已过期，必须重走 05`**：
   - 说明 05 封板后，上游的脚本或配音又发生了改动，时间码已漂移。让总监重跑 `review`，重新审 `04-review.html` 后 `/approve 05`。
3. **报 `🛑 [CODE FREEZE WARN] pipeline/ 源码存在未提交改动`**：
   - 制片期间严禁修改 `pipeline/` 源代码。请通过 `git status` 撤销对流水线代码的偶发修改。
4. **报 `data/episodes 不可达`**：
   - 检查 `data/` 软链指向的外置 SSD 是否已插紧并正确挂载。
5. **长任务（06 渲染）想中途叫停**：
   - 直接按 `Ctrl-C`：宿主会杀掉渲染子进程并退出，不会留一个后台 ffmpeg 继续写。渲染写的是临时文件 `.05-final.partial.mp4`，失败或中断会自动清理、成功才原子替换 `05-final.mp4`，重跑前无需手动删旧成片。
6. **卡片弹出来但你不确定要不要按**：
   - 默认就是 N，直接回车最安全；想让它改方案，回一句白话告诉它哪里不对（拒因会回喂模型，它会换个路子再来）。

---

## 七、跑完一期，该看的三个读数

单测给不了、只有真跑一期才有的东西：

| 读数 | 位置 | 用来判什么 |
|---|---|---|
| 决策耗时分布 + `n` 占比 | `data/episodes/<期>/_agent/approvals.jsonl` 的 `decision_latency_s` | 审批疲劳：决策延迟近似 0（秒按 y）且 `n` 占比≈0 → 卡片没在起作用 |
| `k` 值（人类分钟 ÷ 片长分钟） | `data/episodes/<期>/human_time.json` | 人时观测口径：按停机点分布找最费人的环节（2026-09-23 起不设硬预算，只观测） |
| 单期 LLM 账单 | 端点后台 | 成本锚点（粗线：单期 > ¥5 就该重做上下文管理） |
