# anime-video-agent

把一个选题变成一条 2–4 分钟动漫二创成片的流水线。人只在两处出现：**审时间码、选封面并发布**。

不是「AI 一键出片」。它是一串各自能独立跑、产物落盘、中断可续的命令，加上一套**机器可判定的
质检门禁**——真正的设计重点在于让失败**显式**发生，而不是让成片看起来正常。

```
选题 → 写稿+机检 → 本地TTS配音 → 语义检索排片 → [人审时间码]
     → 渲染(切片+字幕+BGM) → 11 项质检 → 封面候选+标题 → [人选并发布]
```

**顺序不能换。** 必须先配音拿到每段真实时长，再排画面轨；反过来做全程对不齐，
而且不会报错——只是画面和口播差之毫厘、越到后面差得越多。

---

## 前置条件

### 硬性

| | 要求 | 缺了会怎样 |
|---|---|---|
| 系统 | macOS | 只在 macOS 上跑过；ffmpeg 部分与平台无关，语音部分见下 |
| **Apple Silicon** | 配音 / ASR 必需 | Intel Mac、Linux、Windows 装不上 `mlx`，**第 03 步和 ASR 兜底不可用**，其余步骤照常 |
| Python | 3.12（`>=3.12,<3.13`） | 部分 ML 包在 3.13+ 尚无 wheel |
| ffmpeg | **必须带 libass** | 烧不了字幕，渲染在最后一步才炸。`brew install ffmpeg` 的默认版本带 |
| 存储 | 一部番的片源几十 G | 见下方「data/ 放哪」 |
| 素材 | **自备片源 + 与片源同一发布的字幕** | 仓库不含也不分发任何素材 |
| 参考干声 | 5–8 条、每条 8–10 秒 | 没有就没法克隆音色 |

**语音部分只跑 Apple Silicon，这是硬限制，不是配置问题。** `config/voice.json` 留了
`engine` 字段作为接缝，换云 API 或别的本地引擎只需实现一个函数，但仓库里目前只有 mlx 一种实现。

### 模型（约 3.2G，落 `data/`，不落系统盘）

| 模型 | 用途 | 怎么来 |
|---|---|---|
| `BAAI/bge-base-zh-v1.5` | 字幕语义索引（约 400M） | 首次运行自动下 |
| `mlx-community/whisper-large-v3-turbo` | 回读质检、对轴校验、无字幕兜底（约 1.5G） | 首次运行自动下 |
| IndexTTS-1.5（mlx 转换版，约 1.3G） | 配音 | **手动放** `data/models/local/IndexTTS-1.5/`：`config.json`、`model.safetensors`、`model.safetensors.index.json`、`tokenizer.model`。或把 `config/voice.json` 的 `model` 改成 HF repo id 自动下 |

### `data/` 放哪

仓库本身只有纯文本。片源、字幕、索引、模型、每期产物全在 `data/` 下，**不进 git**。
两种放法都支持——系统盘装得下就用实体目录，装不下就软链到外置盘：

```bash
./pipeline/preflight.sh --init                              # 系统盘实体目录
./pipeline/preflight.sh --init /Volumes/<你的盘>/anime-video-data   # 外置盘 + 软链
```

**脚本绝不自动创建 `data/`，不可达就立刻报错退出。** 这条是刻意的：符号链接指向未挂载的卷时，
`mkdir -p data/library` 会在 `/Volumes/` 里建出实体目录，几十 G 静默写进系统盘，
**而且盘插上之后还看不见它**（挂载点被占，系统改挂到「卷名 1」）。

---

## 装

```bash
brew install ffmpeg
uv venv --python 3.12
uv pip install -e ".[apple,dev]"     # 非 Apple Silicon 去掉 apple
pytest                               # 165 条，2.5 秒，不碰片源/模型/外置盘
./pipeline/preflight.sh              # 自检：data 可达、ffmpeg 带 libass
cp CLAUDE.local.md.example CLAUDE.local.md   # 填本机情况，不进 git
```

`CLAUDE.md` 是给 coding agent 读的工程约定；跑这个项目的人也该读一遍，
它解释了大量「为什么不那样做」。

---

## 跑一期

```bash
EP=data/episodes/2026-07-30-<番>-<主题>

# 02 写稿（调 skills/write-script，写完自跑机检）
python -m pipeline.check_script $EP/02-script.md

# 03 配音（先于排片，产出每段真实时长）
python -m pipeline.tts     $EP

# 04 素材检索排片
python -m pipeline.clips   $EP

# 05 人审时间码 —— 唯一的人工关卡
python -m pipeline.review  $EP              # 出 04-review.html
open $EP/04-review.html
python -m pipeline.review  $EP --approve    # 必须显式批准，agent 不许代按

# 06→08
python -m pipeline.render  $EP
python -m pipeline.qc      $EP              # 不过非零退出
python -m pipeline.cover   $EP              # 封面候选 + 标题原料
```

产物带序号落在同一目录，**产物文件即状态**：看目录里有哪些文件就知道进行到哪，
任何一步失败都能从中间接上重跑。逐步细节、每步该盯什么、以及各步踩过的坑见
[docs/WORKFLOW.md](docs/WORKFLOW.md)。

### Phase 0（一部番做一次）

```bash
python -m pipeline.ingest intact data/library/raw/<番>/*/*.mkv                       # 片源完整性
python -m pipeline.ingest phase0 data/library/raw/<番>/<该季目录>/*.mkv --anime <番> --season 1
python -m pipeline.bgm    scan   <CD 目录>                                         # 解 cue 找 instrumental
```

`phase0` 的两个默认值是按某个压制组的命名写的，**换发布组要给**：
`--pattern`（从文件名取集号的正则，需含 `(?P<episode>…)`）与
`--sub-glob`（同名外挂字幕的通配符，默认 `*.Chs&Jap.ass`）。

**一次做完全集，做不完不许出片。** 判据是三条数字相等：完整片源集数 = 索引集数 = 笔记集数。
理由在 WORKFLOW.md——素材边界缩水是唯一一种「越用越不觉得有问题」的失败。

字幕对轴校验（`verify`）拿视频自己的声音去比，**要求字幕带日文轨**：只有纯中文字幕的集
过不了这一关，得走 ASR 兜底或者放弃那几集。

---

## 技术栈与技术选择

| 环节 | 用什么 | 为什么不用别的 |
|---|---|---|
| 检索 | `sentence-transformers` + **bge-base-zh-v1.5**，向量归一化后点积即余弦 | bge-small 实测 Top-1 只有 75%，base 是 100%（8 条查询）。**不为省 300M 退回 small** |
| 索引单元 | ASS 字幕**滑窗 2 行**，向量存 `.npy`，一集一个文件 | 单行太碎（一句话常跨两行轴），3 行开始混进无关对话 |
| 字幕解析 | `pysubs2` | ASS 的 style 与 `\p1` 绘图标签是关键信号，见下 |
| 配音 | **IndexTTS-1.5** on `mlx-audio`，自己写自回归解码循环 | 云 API 把「必须联网 + 有账号 + 有配额」塞进主循环，且要把参考干声传给第三方。上游 `generate()` 有两处 bug，见 ADR-0002 |
| ASR | `mlx-whisper` large-v3-turbo | 三处都要它：回读质检、字幕对轴校验、无字幕时兜底转录 |
| 视频/音频 | **全程 ffmpeg**，无 NLE | MoviePy 比直接调 ffmpeg 慢一个量级且不增加能力；Remotion 要等表现力真撞天花板 |
| 字幕烧录 | `ass` 滤镜（libass），**折行自己算** | libass 靠空格找断点，中文整句没有空格就**根本不折**，91 字渲成一整行切出画面 |
| BGM | `sidechaincompress` 侧链闪避，每首按**实测响度**归一到 -26 LUFS | 固定音量系数是错的控制量：劇伴与单曲伴奏实测极差 11 dB |
| 响度 | `loudnorm` 归一到 -16 LUFS / TP -1 dBTP | 质检门禁按同一组数判 |
| 封面 | `Pillow`：拉普拉斯方差 + 平均亮度 + dHash + 时间邻近去重 | 打分排序试过两版都把正确答案压掉了，见下 |
| 状态 | **文件系统**，无数据库、无队列 | 产物即状态。人机交接一律走文件，不走对话上下文 |
| 测试 | `pytest`，165 条纯函数 | 不 mock ffmpeg 和模型——mock 检查的是 mock |

**没有的东西也是选择：** 没有云 API、没有数据库、没有编排框架、没有 web 服务、
没有一键脚本。每一步都是能单独跑、能单独失败、能单独重跑的命令。

---

## 这个仓库真正值钱的部分

不是代码结构，是**注释和 ADR 里那些实测数据**。几乎每个常量后面都跟着「为什么是这个数」，
每个 workaround 后面都跟着「不这么做会怎样」。举几个已经付过学费的：

- **别用「实占块 ÷ 逻辑大小」判视频下完没有。** 拿 piece 级 SHA-1 当真值比对 89 个文件，
  这个判据漏放 14/72 个残缺文件，最大高估 83 个百分点——写过又作废的数据不会退还磁盘块。
  改用 `ffmpeg -c copy -f null -` 走一遍全片，0/72 漏放
- **ASS 绘图指令占了某一季索引单元的 34%。** 信号在 `{\p1}` 标签里，而清洗函数在过滤之前
  就把 `{...}` 剥掉了。**判据必须在剥标签之前跑**
- **OP/ED 歌词是最坏的一类索引噪声**：语义最贴、画面完全不能用。按 style 名滤，
  那是字幕组自己声明的分类，比时间窗和文风都可靠
- **自回归 TTS 的语速和文本长度正相关**（r=+0.613），整段合成会让长段落越念越快。
  改按句合成后标准差 0.57→0.46
- **回读质检把「她」听成「他」，判整期失败——而音频完全正确。** 中文同音，
  ASR 不产出能区分它们的信息。只折读音完全一致的字（的/得/地 不碰）
- **同一个 `normalize` 被两个目的复用，会把缺陷一起归一掉。** TTS 把中文引号念成了字，
  回读判 **PASS**，因为参考文本早被剥掉了引号。**这一例是靠耳朵听出来的**
- **拉普拉斯方差测的是「画面里有多少细节」，不是「画面有多好」**。拿它给封面候选排序，
  选出来的是货架和书架，主角一个都没有。**它是准入门槛，不是排序依据**
- **`set -o pipefail` 下 `ffmpeg -filters | grep -q` 会误报**：grep 提前关管道，
  ffmpeg 吃 SIGPIPE，于是自检在正确的安装上报 FAIL

共同点：**这些失败全是静默的**——照常出片、照常判 PASS、照常返回 0。
所以每加一道检查，先问它测的是不是真实产物；每加一个分数，先问它能不能用来排序。

> 同一条教训在这个项目里出现过四次：**一个量能用来卡门槛，不代表它能用来排序。**

---

## 测试

```bash
pytest        # 165 条，2.5 秒，不碰片源、模型、外置盘
```

覆盖的是纯函数：字幕折行与分卡、排片时长水填、查询阶梯、cue 时间码、合成单元切分、
回读 CER、封面去重与铺开、配置降级、ASS style 与绘图过滤。
**测的不是 API 形状，是上面那些坑**——每条断言对应一个踩过的具体错误，
注释里写着它当初怎么错的。

两条自加的纪律：

- **期望值先在实现上跑一遍再写进断言。** 写排片测试时有一条想错了：以为某个边界必然判
  short，实际它会往前拉起切点——代码是对的，期望是错的。照着「应该怎样」写测试，
  等于把错误期望固化下来
- **写完做变异检验。** 把被测行为故意改坏，看测试红不红。不红的断言是装饰品

涉及媒体的部分不进测试，靠 pipeline 自己的守卫在真跑时兜：**截取守卫**（切前校验
`seek + dur <= 源时长`，切后按**源片实际帧率**复核 ≤ 1 帧）与 **11 项质检门禁**。
ffmpeg 在片段超出源片末尾时静默截断且不报错，这是已复现的产线杀手。

---

## 结构

```
pipeline/     各阶段脚本，每个可独立运行
tests/        纯函数测试，pytest
config/       project.json（规格与阈值）、voice.json（引擎与音色）、bgm.json（曲目表+实测响度）
skills/       给 coding agent 的写稿指令（VOICE.md 是空模板，要自己填）
docs/         WORKFLOW.md 逐步细节 + adr/ 难以回头的决策
data -> ...   片源、字幕、索引、模型、每期产物。不进 git
```

## 换一部番

1. 改 `config/project.json` 的 `anime.default`
2. 改 `anchor_words`——稿件机检判定「可指认剧情锚点」的具名场合词，**逐番不同**，
   不换的话锚点检查会永远判 0 处
3. 在 `config/bgm.json` 里加一组曲目（每首必须带实测 `lufs`，没有会直接报错退出）
4. 重跑 Phase 0。索引目录按番隔离，`clips` 与 `subindex search` 都按番过滤

`skills/write-script/VOICE.md` 是**空模板**，里面没有可用的声音样本——
它教你怎么从自己的既有文稿里提炼一份，填好放 `VOICE.local.md`（不进 git）。
不填也能出稿，但会是通用 AI 腔。

## 不做什么

- **不做纯搬运。** 混剪 + 原创口播是灰区，加口播是加分项；没有口播的不做
- **不自动上传。** 省下的几分钟不值得拿账号冒险
- **不做画质、转场、文案的无限打磨。** 边际收益极低，时间只花在让流水线更自动、更可靠上
- **政治议题不进自动管道。** agent 不掌握中文平台的审核边界

## License

MIT。见 [LICENSE](LICENSE)。

**素材本身（片源、字幕、音乐）不属于本仓库也不随仓库分发。** 二创的版权风险由使用者自负；
`config/bgm.json` 里的曲目表只是路径与实测响度，不含任何音频。
