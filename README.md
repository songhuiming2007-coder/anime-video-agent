# anime-video-agent

自动化动漫二创短视频（解说 / 杂谈 / 盘点）命令行流水线。

输入选题，自动完成写稿、配音、素材检索、排片、音视频渲染与质检，输出可直接上传的 1080p MP4、封面图与标题候选。主流程由脚本与 Coding Agent 驱动，人类仅在 4 处关键质检节点介入确认。

---

## 架构与人机分工

### 1. 端云解耦架构
- **本地 Mac（Apple Silicon）**：负责交互、代码执行、视频解复用与 ffmpeg 最终渲染，解除本地显存与散热瓶颈；
- **云端 Headless GPU（AutoDL）**：承载重型大模型推理（Qwen3-TTS 1.7B、Qwen3-VL、SenseVoice、bge-m3）；本地亦支持 mlx 轻量引擎离线兜底。

### 2. 流水线与耗时（每期人类投入 ≤ 10 分钟）

```
[01 选题] ──> [02 写稿] ──> [02.5 人审改稿] ──> [03 配音] ──> [03.5 配音顺听]
  (人 1.5m)    (Agent 3m)     (人 3-5m)       (云端/本地 5m)     (人 2-3m)
                                                     │
[09 发布] <── [08 封面标题] <── [07 质检] <── [06 渲染] <── [05 审时间码] <── [04 排片]
  (人 3m)      (Agent 1m)     (机器 40s)    (机器 1.5m)     (人 5m)        (Agent 1m)
```

- **产物即状态**：无数据库、无外部队列。所有中间件与产物依序落盘至 `data/episodes/<期号>/`（从 `01-topic.md` 到 `07-cover/`），随断随续。
- **让失败显式发生**：杜绝静默降级（空镜兜底、截断容错等），不达标当场报错中断。

---

## 快速上手

### 1. 环境准备
- 操作系统：macOS（开发与渲染环境）
- 基础工具：`ffmpeg`（**必须集成 libass**：`brew install ffmpeg`）、`uv`
- Python：`3.12` / `3.13` / `3.14`
- 推荐驱动 Agent：[Pi](https://github.com/earendil-works/pi-coding-agent) 或 Claude Code

### 2. 安装与初始化

```bash
# 1. 克隆并安装依赖
git clone <repo-url> && cd anime-video-agent-v2
uv venv && uv pip install -e ".[apple,dev]"

# 2. 初始化数据目录（本地或外置存储盘软链接）
./pipeline/preflight.sh --init                           # 使用本地系统盘
# 或：./pipeline/preflight.sh --init /Volumes/SSD/anime-data  # 外置盘挂载

# 3. 运行环境自检与纯函数测试
./pipeline/preflight.sh
pytest
```

---

## 制作全流程速查

### Phase 0：新番素材入库（一部番仅需一次）

在启动具体期数前，需完成片源校验与多模态索引构建：

| 步骤 | 目标 | 核心命令 |
|---|---|---|
| **1. 验片** | 解复用校验片源文件完整性 | `python -m pipeline.ingest intact data/library/raw/<番>/*/*.mkv` |
| **2. 入库** | 对轴校验并提取字幕索引 | `python -m pipeline.ingest phase0 data/library/raw/<番>/S01/*.mkv --anime <番> --season 1` |
| **3. 视觉索引** | 镜头切分、人脸检测聚类与人工标注 | `python -m pipeline.shots build ... && python -m pipeline.faces detect ...` |
| **4. 状态核验** | 确认七维素材与索引数据对齐 | `python -m pipeline.vindex status --anime <番>` |
| **5. 音色试音** | 筛选并标定口播音色参考音频 | `python -m pipeline.tts probe "测试文本" --ref data/voice/reference/sample.wav` |
| **6. BGM 建池** | 解 cue、转码 flac 并测量响度入库 | `python -m pipeline.bgm scan <CD目录> && python -m pipeline.bgm measure ...` |

详细参数与多模态索引规范详见 [`docs/WORKFLOW.md` Phase 0 章节](docs/WORKFLOW.md)。

---

### 每期生产流水线（01 – 09 步）

| 步骤 | 名称 | 执行者 | 命令 / 操作 | 核心产物与检查点 |
|---|---|---|---|---|
| **01** | **选题定稿** | 人类 | 创建 `data/episodes/<期号>/01-topic.md` | 明确番剧、体裁、模式、张力与核心锚点 |
| **02** | **脚本写作** | Agent | 调 `skills/write-script` 查证写稿并运行：<br>`python -m pipeline.check_script data/episodes/<期号>/02-script.md` | 产出 `02-script.md`，机检字数、起伏、节奏、锚点与查询接口 |
| **02.5** | **人审改稿** | **人类** | 人工通读精修 `02-script.md` | 核验台词原文、说话人与事实判断，去除模型套话 |
| **03** | **语音合成** | Agent / 机器 | `python -m pipeline.tts data/episodes/<期号>` | 产出 `03-audio/`，Whisper 自动回读比对质检，复核实际时长 |
| **03.5** | **配音顺听** | **人类** | 人耳抽检开头与最长段音频 | 确认无严重电音、发飘或漏读 |
| **04** | **素材排片** | Agent / 机器 | `python -m pipeline.clips data/episodes/<期号>` | 产出 `04-clips.json`。通过锚点直通 / 台词语义 / 画面 VLM 全局贪心分派 |
| **05** | **审时间码** | **人类** | `python -m pipeline.review data/episodes/<期号>`<br>浏览器打开 `04-review.html` 确认无误后执行：<br>`python -m pipeline.review data/episodes/<期号> --approve` | **核心人工质量闸门**：抽帧比对口播、画面与台词，拦截画外音错配 |
| **06** | **本地渲染** | Agent / 机器 | `python -m pipeline.render data/episodes/<期号>` | 切片、拼装、混 BGM、烧录字幕、响度归一，输出 `05-final.mp4` |
| **07** | **质量门禁** | 机器 | `python -m pipeline.qc data/episodes/<期号>` | 11 项机器硬指标质检（音画同步、黑帧、静音、字幕超宽/空段等），产出 `06-check.log` |
| **08** | **封面与标题** | Agent / 机器 | `python -m pipeline.cover data/episodes/<期号>` | 候选帧自动去重过滤，输出封面联系表与 5 条候选标题（`07-titles.md`） |
| **09** | **人工发布** | **人类** | 人选定稿封面与标题，手动上传平台 | 闭环发布 |

---

## 深入文档索引

项目所有详细规范与历史决策均模块化沉淀在 `docs/` 目录中，按需查阅：

- **全流程操作总纲**：[`docs/WORKFLOW.md`](docs/WORKFLOW.md) —— 48 行极简总览与物理红线；
- **分工序标准操作 Runbook**：[`docs/runbook/`](docs/runbook/) —— 01–09 独立步骤操作手册（每篇 ≤60 行）；
- **判据与质检标准定义**：[`docs/dev/STANDARD.md`](docs/dev/STANDARD.md) —— 所有量化门禁、评分与测试用例准则；
- **文档全景索引表**：[`docs/INDEX.md`](docs/INDEX.md) —— 生产态与开发态双轨索引；
- **架构决策记录**：[`docs/dev/adr/`](docs/dev/adr/) —— 包含端云解耦（ADR-0014/0016）、音色选型（ADR-0017）、VLM 检索（ADR-0015）等核心决策；
- **Coding Agent 协作规范**：[`AGENTS.md`](AGENTS.md) / [`CLAUDE.md`](CLAUDE.md) —— 人机红线、停机点与工程约定。

---

## 许可证

MIT License 详见 [LICENSE](LICENSE)。  
*注意：片源、字幕、音源等媒体素材版权归原作者所有，本仓库仅提供自动化处理工具链。*
