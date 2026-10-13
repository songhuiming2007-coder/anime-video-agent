# anime-video-agent

制作动漫二创短视频（解说 / 杂谈 / 盘点）的工具：一条制作流水线，加上驱动它的 agent 宿主 `ava`。给一个选题，产出可直接上传的 1080p MP4、9 张封面候选和标题候选。

## 由什么组成

- **流水线**（`pipeline/`）：一组能单独运行的 Python 模块，配音、排片、渲染、质检、出封面各一个。每个模块读上一步的产物、写自己的产物。模块里不调对话大模型，只用 TTS、ASR、VLM、向量检索这类专用模型。
- **`ava`**（`pipeline/agent/`）：项目自主实现的 agent 宿主，不依赖第三方 agent 框架。里面的制片 agent 是一个对话大模型：它看期目录判断做到哪一步，写需要创作的文本（选题、稿件草稿、标题候选），再通过 16 个受控白名单工具驱动流水线与环境。只读操作免卡，有副作用的操作逐次弹卡等人批准；`/` 开头的快捷命令直接执行；支持请求时上下文老化与手动 `/compact`，会话内可用 `/model`、`/thinking` 切换模型与推理强度。
- **桌面端 AVA**（`desktop/`，可选）：`ava` 的 macOS Electron 界面，包含期看板、对话区、全局常驻作业条、产物预览与 02.5 改稿 / 03.5 顺听专用面板，和终端用同一套会话与状态机。

## 一期怎么走

| 步 | 做什么 | 谁做 | 产物 |
|---|---|---|---|
| 01 | 选题 | 人（agent 可协助起草） | `01-topic.md` |
| 02 | 写稿 | agent | `02-script.draft.md` |
| **02.5** | **审改稿件并定稿** | **人** | 定稿 `02-script.md`，批准时自动封板 `02-diff.patch` |
| 03 | 配音 | 模块（云端或本地） | `03-audio/` |
| **03.5** | **顺听配音、标出错字** | **人** | 顺听面板或 `/voice` 记录纠错，靶向重配出错段 |
| 04 | 从素材库检索画面、排片 | 模块 | `04-clips.json` |
| **05** | **核对画面与口播** | **人** | 审看 `04-review.html` 抽帧，批准产出 `04-clips.approved.json` |
| 06 | 渲染 | 模块 | `05-final.mp4` |
| 07 | 质检 | 模块 | `06-check.log` |
| 08 | 封面候选、标题候选 | 模块出封面，agent 写标题 | `07-cover/`、`07-titles.md` |
| **09** | **选定封面标题，上传** | **人** | — |

加粗的 4 步是停机点，必须人来拍板，agent 不能代做。

## 几条约定

- **产物即状态**：没有数据库和队列。每一步的产物都落在 `data/episodes/<期号>/`，看目录就知道做到哪，中断了从中间接着跑。
- **失败要显式**：不达标就报错停下，不拿空镜、截断之类的东西凑合交付。
- **人的时间只记录、不设上限**：每期在各停机点花的时间记进 `human_time.json`，用来找出最费人的环节。早先的「k × 片长」预算已经取消。
- **端云分工**：本地 Mac（Apple Silicon）负责交互和 ffmpeg 渲染；重模型（TTS、VLM、ASR、向量检索）放在云端 GPU，本地有 mlx 轻量引擎兜底。

## 安装

需要 macOS、[`uv`](https://github.com/astral-sh/uv)、带 libass 的 `ffmpeg`（`brew install ffmpeg`），Python 3.12–3.14。

```bash
git clone <repo-url> && cd anime-video-agent-ava
uv venv && uv pip install -e ".[apple,dev]"

./pipeline/preflight.sh --init          # 建数据目录；放外置盘就在后面加盘上的路径
./pipeline/preflight.sh                 # 环境自检
pytest                                  # 纯函数测试，2500+ 用例约 2 分钟
```

联网抓取的 `crawl` / `browser` 两组可选依赖见 `pyproject.toml` 注释。

## 使用

### 做一期视频

日常以桌面端 AVA 或终端 `ava` 对话为主，直接用中文跟制片 agent 交互。命令由 agent 发起、人只批卡，不在终端手敲流水线命令。

```bash
ava new <期号>          # 建一期并进入对话
ava <期号>              # 进入这一期的对话
ava <期号> --continue   # 接着上一段会话
ava idea                # 不建期，先聊选题
```

配音有念错的字不用整期重配：03.5 阶段在桌面端顺听面板划选错字并记入期级或全局表（或终端 `ava <期号> /voice`），随后靶向只重配受影响段落。

九步各自做什么、停机点要看什么，见 [`docs/WORKFLOW.md`](docs/WORKFLOW.md)（≤100 行）；对话里的 `/` 快捷命令见 [`docs/CHEATSHEET.md`](docs/CHEATSHEET.md)。

### 新番入库（每部番一次）

开第一期之前，这部番的素材要先入库。片源文件由人放进 `data/library/raw/<番>/`；之后的步骤都由 agent 在期会话里经审批卡跑（只读的免卡，写盘的弹卡；人只批卡、看密度表和抽检表拍板），不用在终端敲：

1. 验片：确认片源能完整解复用（`ingest intact`，免卡）
2. 入库：对轴并建字幕索引（`ingest phase0`）
3. 视觉索引：定阈值、镜头切分、人脸聚类、给角色贴名（`shots calibrate` / `build`、`faces`）
4. 核对：`vindex status --anime <番>` 各项数字对得上（免卡）
5. 音色：试音并选定口播参考音（`tts probe`）
6. BGM：建曲库并测响度（`bgm`）

命令与 `python -m pipeline.<模块> …` 等价，后者只作排查参考。

完整参数见 [`docs/dev/postmortems/workflow-history.md`](docs/dev/postmortems/workflow-history.md) 的 Phase 0 章节。

### 桌面端（可选）

`desktop/` 是 Electron 桌面端（打包后的 app 叫 AVA），功能与终端 `ava` 相同：期看板、对话、产物预览、审批卡、改稿编辑器、配音顺听、封面定稿。它只是 core 的一层壳，所有写入都经 core 的命令完成。LLM 密钥从系统钥匙串读取。

```bash
cd desktop && npm ci
npm run dev             # 开发态
npm run release-build   # 打包
```

## 已知限制

| 问题 | 影响 | 编号 |
|---|---|---|
| 桌面端打包版端到端全流程手验 | 交互布局、作业条与各停机点专用面板已完成修复并单项通过，打包版完整一期闭环手验持续推进中 | D39 |
| 回读质检存在同音字盲区 | TTS 读错但 Whisper 回读认成同一错字时机器无法检出，读音正确性必须由 03.5 人耳顺听兜底 | N6 / N22 |

全部问题见 [`docs/dev/issues/README.md`](docs/dev/issues/README.md)。

## 文档

- [`docs/WORKFLOW.md`](docs/WORKFLOW.md)：每期九步速查
- [`docs/CHEATSHEET.md`](docs/CHEATSHEET.md)：快捷命令与工具审批放行表
- [`docs/runbook/`](docs/runbook/)：每一步的详细操作手册
- [`docs/INDEX.md`](docs/INDEX.md)：全部文档的索引（生产用与开发用分开）
- [`docs/dev/adr/`](docs/dev/adr/)：架构决策记录（如 ADR-0025 工具收敛、ADR-0029 bash 工具）
- [`AGENTS.md`](AGENTS.md)：给 coding agent 的规则（红线、停机点、工程约定）

## 许可证

MIT，见 [LICENSE](LICENSE)。片源、字幕、音源等素材的版权归原作者，本仓库只提供处理工具。
