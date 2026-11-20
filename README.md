# anime-video-agent

制作动漫与 ACG 纪实长篇（人物志 / 企划编年史 / 杂谈 / 盘点，支持多卷系列与合集）的流水线与桌面端应用 **AVA**。输入选题，产出 1080p MP4 成片、封面候选与 5 条标题候选；人定稿后生成横版（16:9）、竖版（9:14）与 4:3 三版封面。

## 架构组成

- **桌面端 AVA**（`desktop/`）：日常唯一交互入口（macOS Electron），提供期看板、对话区、全局作业条、产物预览、02.5 改稿与 03.5 顺听专用面板，支持会话内切换模型、推理强度与多服务商登录；窗口在后台等人处理时 Dock 图标持续弹跳提醒。
- **agent 宿主**（`pipeline/agent/`）：由桌面端自动拉起并驱动流水线，负责读取期状态、起草文本与调用工具。有副作用的操作先在对话确认，仅高风险动作（云端计费、特例改配置、越过停机点、开工单、`bash`/`chrome` 危险操作）弹审批卡。
- **流水线**（`pipeline/`）：独立的 Python 工序模块（配音、排片、渲染、质检、封面），通过磁盘产物衔接，支持原声试听与动态配乐编排；仅调用 TTS / ASR / VLM / 向量检索等专用模型。

## 单期流程

在 AVA 中可开启**选题会话**（尚未建期）、**期会话**（制作单期）或**企划会话**（管理多卷系列）。单期按以下 9 步推进：

| 步 | 做什么 | 谁做 | 产物 |
|---|---|---|---|
| 01 | 选题 | 人（agent 可协助起草） | `01-topic.md` |
| 02 | 写稿，机检通过后跑 02.8 对抗审查 | agent | `02-script.draft.md` |
| **02.5** | **审改稿件并定稿** | **人** | `02-script.md`，批准时自动封板 `02-diff.patch` |
| 03 | 配音 | 模块（云端或本地） | `03-audio/` |
| **03.5** | **顺听配音并纠错** | **人** | 在顺听面板标出错字，靶向重配受影响段 |
| 04 | 检索画面并排片 | 模块 | `04-clips.json` |
| **05** | **核对画面与口播** | **人** | 审看 `04-review.html`，批准产出 `04-clips.approved.json` |
| 06 | 渲染 | 模块 | `05-final.mp4` |
| 07 | 质检 | 模块 | `06-check.log` |
| 08 | 生成封面与标题候选 | 模块出封面，agent 写标题与叠字 | `07-cover/`、`07-titles.md` |
| **09** | **选定封面标题并上传** | **人** | `08-cover.jpg` / `08-cover-vertical.jpg` / `08-cover-4x3.jpg` |

加粗的 4 步为**人工停机点**，须由人在桌面端确认。缺画面时可在任意阶段随时补素材。

## 核心约定

- **产物即状态**：无数据库与队列，产物落盘于 `data/episodes/<期号>/`，中断可续跑。
- **失败显式暴露**：不达标即报错停机，不以空镜或截断强行交付。
- **人时只记、不设上限**：停机点耗时记入 `human_time.json`，用于定位最费人的环节。
- **端云分工**：Apple Silicon Mac 负责交互与 ffmpeg 渲染；重模型（TTS、VLM 打标、ASR 仲裁）按需上云用完即弃，本地保留 mlx 轻量引擎兜底配音。

## 安装与启动

环境要求：macOS、[`uv`](https://github.com/astral-sh/uv)、带 libass 的 `ffmpeg`（`brew install ffmpeg`）、Python 3.12–3.14、Node.js。

```bash
# 1. 初始化 Python 环境与数据目录
git clone <repo-url> && cd anime-video-agent-ava
uv venv && uv pip install -e ".[apple,dev]"
./pipeline/preflight.sh --init   # 创建 data 目录（外置盘可在参数后指定路径）
./pipeline/preflight.sh          # 检查环境与 ffmpeg

# 2. 启动或打包桌面端 AVA
cd desktop && npm ci
npm run dev                      # 开发模式启动
npm run release-build            # 打包 macOS 裸产物（dist/mac-arm64/AVA.app）
npm run init-cert                # 首次运行：生成本机代码签名证书「AVA Local Dev」（保 TCC 授权不漂移）
npm run release-install          # 打包并安装稳定签名版到 /Applications/AVA.app
```

> 以后桌面端有改动需要重新打包安装时，只需完全退出 AVA（`Cmd + Q`）后跑一条命令：`cd desktop && npm run release-install`（会自动重签替换 `/Applications/AVA.app` 并清理 `dist/mac-arm64/AVA.app`，macOS TCC 权限持久保留）。

运行纯函数测试可在仓库根目录执行 `pytest`（约 3 分钟）。网页抓取所需的 `crawl` / `browser` 可选依赖见 [`pyproject.toml`](pyproject.toml)；环境变量密钥从系统钥匙串读取，服务商登录凭据保存于 `config/auth.local.json`（权限 0600，不进 git）。

## 常见工作流

- **单期制作**：在 AVA 侧栏新建期（或从选题会话带入），全程直接用中文与制片 agent 对话推进。03.5 阶段在顺听面板标出错字后靶向重配，无需整期重来；云端配音支持 AutoDL 与 Google Colab 双后端，开机、任务与关机均需计费审批。
- **补素材**：缺少库内画面（官方图、PV、访谈等）时，agent 搜索并列出候选，人点名后下载登记（图片自动转为微动视频）。期内补料进本期补丁池并切镜打标，企划卷进企划池统一打标；网页抓取按 `web_fetch` → `crawl` → `browser` → 主力 `chrome` 升级并留档原文，细节见 [`skills/acquire-assets/SKILL.md`](skills/acquire-assets/SKILL.md)。
- **多卷企划**：在 AVA 侧栏创建企划与子卷。企划会话负责规划 `00-project.md`、预收集素材、跨卷查重与成片合集总装（见 [`docs/runbook/10-assemble.md`](docs/runbook/10-assemble.md)）；事实条目需经逐字核对留档出处与独立复核后再入库。
- **新番入库（每部番一次）**：将片源放入 `data/library/raw/<番>/` 后，在 AVA 会话内由 agent 依次发起验片、对轴建字幕索引、视觉标定与人脸贴名、索引核对、参考音色试音与 BGM 登记；番剧本体不做 VLM 打标。参数细节见 [`docs/dev/postmortems/workflow-history.md`](docs/dev/postmortems/workflow-history.md) 的 Phase 0 章节。

> 注：终端 `ava` CLI 与 `python -m pipeline.<模块>` 仍保留为底层实现，仅供无头调试与故障排查参考，日常无需在终端手动操作。

## 已知限制

| 问题 | 影响 | 编号 |
|---|---|---|
| 回读质检同音字盲区 | TTS 读错但 Whisper 回读识别为同一错字时无法机检发现，需由 03.5 人工顺听兜底 | N6 / N22 |

完整问题清单见 [`docs/dev/issues/README.md`](docs/dev/issues/README.md)。

## 文档导航

- [`docs/WORKFLOW.md`](docs/WORKFLOW.md)：单期九步速查
- [`docs/runbook/`](docs/runbook/)：各工序操作手册（含 10 企划总装）
- [`skills/`](skills/)：写稿、补素材与动效素材技能说明
- [`docs/INDEX.md`](docs/INDEX.md)：生产与开发文档总索引
- [`docs/dev/adr/`](docs/dev/adr/)：架构决策记录
- [`AGENTS.md`](AGENTS.md)：coding agent 开发约束与红线

## 许可证

MIT，见 [LICENSE](LICENSE)。片源、字幕、音源等素材版权归原作者，本仓库仅提供处理工具。
