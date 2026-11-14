# 云端算力双后端改造施工图（AutoDL + Google Colab SSH / Drive）

- **状态**：已施工·机检通过（2026-11-14：全仓 `pytest -q` 3799 passed、Notebook JSON/AST 校验通过；真实 Google Colab Pro GPU 运行时实机冒烟待用户在 Colab 端执行确认）
- **关联 ADR**：[ADR-0014](../adr/0014-cloud-gpu-headless-model-stack.md)、[ADR-0016](../adr/0016-render-local-inference-cloud.md)、[ADR-0030](../adr/0030-approval-danger-only.md)
- **目标**：把 AVA 全部云端 GPU 能力（`tts` 配音与回读、`captions` / `lib-captions` VLM 打标、`ingest_patch` 补料打标、`probe` 探针）解耦为 **AutoDL** 与 **Google Colab** 两条平行通道；其中 Colab 同时支持 **SSH 隧道（`colab`，优先 `cloudflared`）** 与 **Google Drive 共享队列（`colab-drive`）** 两种接入，且全程对 `ava` agent 透明友好。

---

## 一、需求与裁决汇总

| 维度 | 选定方案 | 理由 |
|---|---|---|
| **Colab 接入方式** | **双通道并存**：`colab`（固定 Notebook + `cloudflared` / TCP SSH 隧道）与 `colab-drive`（Google Drive 共享任务队列） | 隧道可用时走 `colab` 复用 `rsync` + `tmux` 全套能力；隧道受限或无 `cloudflared` 时走 `colab-drive` 零端口直跑 |
| **Colab 冷启动与缓存** | **混合策略**：Python 依赖/venv 缓存存 Google Drive，模型权重由 Colab GCP 内网直连 HuggingFace 拉取到本地 `/content/models`（可选 Drive 备份） | 避免每次开机重复编译安装依赖，同时利用 GCP 内网高速下载，避免大权重直接走 Drive FUSE 拖慢推理 |
| **双后端切换方式** | **独立双配置 + 显式切换**：`cloud.local.json` 各自保存 `profiles.{autodl,colab,colab-drive}` 与 `active_provider`，支持 `cloud use <provider>` 与单命令 `--provider <name>` 覆盖 | 切换后端不互相覆盖已有连接信息；agent 可按当前任务/卡型随时指定后端 |
| **计费与停机释放** | **原生双口径记账 + 自动释放**：AutoDL 记人民币 `cny`，Colab 记 Compute Units `cu`；无 `hold` 且空闲超时或收到 `down`/释放信号时，Colab 内核调用 `google.colab.runtime.unassign()` | 不把人民币单期预算误套到 Colab CU；避免 `down` 依赖不存在的 `/usr/bin/shutdown` |
| **SSH 隧道工具** | **优先 `cloudflared`（兼容标准 TCP `ssh -p`）**：`cloud connect` 识别隧道地址并生成带 `ProxyCommand` 的 `ssh_config` | 免费、免账号、不限流量；HostName 严格校验防命令注入 |

---

## 二、架构与文件改动清单

### 1. 配置层：`config/cloud.json` 与 `config/cloud.local.json`

- `config/cloud.json` 保留现有顶层键（保证旧代码与现有测试兼容），新增 `"providers"` 字典：
  - `autodl`：`transport: "ssh"`、`remote_root`、`remote_models`、`fs_models`、`remote_python`、`remote_data_disk`、`billing_unit: "cny"`。
  - `colab`：`transport: "ssh"`、`remote_root: "/content/anime-video-agent"`、`remote_models: "/content/models"`、`remote_python: "/content/venv/bin/python"`、`remote_data_disk: "/content"`、`remote_log_dir: "/content/logs"`、`drive_cache_dir: "/content/drive/MyDrive/ava_cache"`、`billing_unit: "cu"`、`cu_rates`（T4/L4/A100 参考 CU/h）、`budget_per_episode_cu`、`budget_per_phase0_cu`。
  - `colab-drive`：`transport: "drive"`、同上远端与 CU 预算配置，任务队列通过本地挂载的 `drive_root` 交互。
- `config/cloud.local.json`（不进 git，受出网敏感词保护）：
  - 新增 `active_provider`（默认 `"autodl"`）与 `profiles.{autodl, colab, colab-drive}`。
  - 保留顶层 `server` / `port` / `user` / `ssh_host` 向下兼容：读写 `autodl` profile 时同步顶层字段。

### 2. 核心调度层：`pipeline/cloud.py`

1. **Provider 解析与切换**：
   - `resolve_provider(cfg_global, cfg_local, cli_provider=None) -> str`
   - `provider_config(cfg_global, cfg_local, provider) -> dict`
   - 新增子命令 `cloud use <provider>`：切换 `active_provider` 写入 `cloud.local.json`（Code Freeze 例外同 `cloud connect`）。
   - 所有子命令增加 `--provider {autodl,colab,colab-drive}` 可选参数，临时指定不改写 `active_provider`。
2. **`colab`（SSH 隧道）支持**：
   - `parse_login_command(text)` 兼容标准 `ssh -p <port> user@host` 与 `cloudflared` 写法（如 `ssh -o "ProxyCommand=cloudflared access ssh --hostname %h" root@xxxx.trycloudflare.com` 或裸域名 `xxxx.trycloudflare.com`），严格校验主机名与用户名字符集。
   - `render_ssh_config(...)` 按连接类型生成直连或带 `ProxyCommand cloudflared access ssh --hostname %h` 的配置，使用独立 Host 别名（`autodl` / `ava-colab`）互不干扰。
   - 远端模型目录、数据盘、日志目录、解释器按当前 provider 读取，并支持 `AVA_REMOTE_MODELS` 环境变量注入远端运行命令。
   - `doctor` / `fix-env` 按 provider 分派：Colab 下检查 `cloudflared`（仅 `cloudflared` 隧道）、不查 `/root/autodl-fs`、不执行 `/etc/network_turbo` 与 `hf-mirror.com`，直接从 HuggingFace 拉取模型；按显存报告 T4（仅配音）与 L4/A100（VLM + 配音）差异。
   - Watchdog 与 `down`：Colab 写入 `/content/.ava_unassign` 标记通知内核释放运行时（`google.colab.runtime.unassign()`），不依赖 `/usr/bin/shutdown`。
3. **`colab-drive`（Google Drive 共享队列）支持**：
   - `cloud connect <drive_path> --provider colab-drive` 记录 `drive_root` 并验证目录可读可写。
   - 共享目录契约：
     - `code/`（`sync-code` 同步 `pipeline/`、`config/`、`pyproject.toml` + `code_manifest.json`）
     - `staging/`（`push` / `lib-push` 暂存输入）
     - `jobs/<job_id>/`（`request.json`、`status.json`、`worker.log`、`output/`）
     - `heartbeat/worker.json`（worker 定时上报 GPU、显存、磁盘、活跃任务）
     - `control/hold.json` & `control/shutdown.flag`（对应 `hold`、`release`、`down`）
   - 完整支持 `up`、`doctor`、`status`、`sync-code`、`push`、`run`、`logs`、`pull`、`lib-push`、`lib-captions`、`lib-pull`、`hold`、`release`、`down`。
4. **双口径账本与预算**：
   - `format_ledger_entry` / `parse_ledger` 兼容原有 `cny` 条目，新增 `provider`、`billing_unit`（`"cny"` | `"cu"`）、`cu` 字段；`status` 按单位汇总展示。

### 3. 推理模块去硬编码：`pipeline/tts.py`、`pipeline/asr.py`、`pipeline/vindex.py`、`pipeline/ingest_patch.py`

- 统一优先读取环境变量 `AVA_REMOTE_MODELS`（由 `cloud.py` 按当前 provider 注入远端执行命令），其次读 `config/cloud.json` 当前 provider 的 `remote_models`，避免写死 `/root/autodl-tmp/models`。
- `pipeline/asr.py`：若本地模型目录不存在且处于 Colab/显式允许 HF 回退时，允许回退到 HF repo id（AutoDL 仍优先走目录校验，不静默往系统盘写模型）。
- `pipeline/tts.py`：`qwen3_tts_cuda` 同口径解析 `AVA_REMOTE_MODELS` 下的 `Qwen/Qwen3-TTS-12Hz-1.7B-Base`。
- `pipeline/vindex.py`：`caption_model_ref()` 优先 `AVA_REMOTE_MODELS`；新增 `cloud_budget_info(provider=None)` 返回带单位的预算信息，保留 `cloud_budget()` 返回 `(rate, budget)` 兼容既有调用与测试。
- `pipeline/ingest_patch.py`：透传 `--provider` 参数给 `cloud.cmd_pull`/`cmd_push`/`cmd_run`，并在 Drive 模式下支持轮询/拉取补丁池打标结果。

### 4. Colab 端 Worker 与启动模板：`tools/colab_worker.py` & `tools/ava_colab_bootstrap.ipynb`

- `tools/colab_worker.py`：纯 Python（标准库为主）实现：
  - `drive` 模式队列 worker：写 `heartbeat/worker.json`、校验 `code_manifest.json`、执行白名单任务（`tts`/`captions`/`probe`）、流式写 `worker.log`、归档产物到 `output/`、检测 `control/hold.json` 与 `control/shutdown.flag` 并调用回调/退出释放。
  - `ssh` 模式保活/自动释放哨兵：巡检 `tmux ls | grep '^ava-'`、`.ava_hold`、`.ava_unassign`，满足空闲或主动关机条件时返回退出信号触发 Notebook 内核 `google.colab.runtime.unassign()`。
- `tools/ava_colab_bootstrap.ipynb`：开箱即用 Colab Notebook，包含：
  - Cell 1：挂载 Google Drive、恢复/缓存依赖 venv、克隆/同步 `anime-video-agent`、软链模型目录并按任务（`tts` / `captions` / `all`）从 HF 直拉权重；
  - Cell 2：一键选择 `mode = "ssh"`（启动 `sshd` + `cloudflared` 并打印 `cloud connect` 指令）或 `mode = "drive"`（启动 `tools/colab_worker.py` 队列监听），结束后自动 `runtime.unassign()`。

### 5. Agent 友好性：`pipeline/agent/*`、`config/agent/scopes/pipeline.md`、`docs/`

- `pipeline/agent/tools.py`：
  - `ASSET_COMMANDS["cloud"]` 增加 `"use"`；`HUMAN_GATED_COMMANDS` 增加 `("cloud", "use")`（与 `("cloud", "connect")` 同级，逐条人审，不开放「本会话都批准」）。
  - `cloud exec` 继续永久禁用。
- `pipeline/agent/session.py`：
  - `CONFIG_WRITE_COMMANDS` 增加 `("cloud", "use")`。
- `pipeline/agent/status_card.py`：
  - 云端审批卡提示区分当前/指定 provider 的计费口径（AutoDL 按量计费 vs Colab 消耗 Compute Units）。
- `config/agent/scopes/pipeline.md` 与 `docs/runbook/03-tts.md`：更新双后端操作说明，告知 agent 可询问或按 `--provider colab|colab-drive|autodl` 切换。

---

## 三、验证命令与完成判定

1. **静态门禁与现有测试全绿**：
   - `.venv/bin/pytest tests/test_static.py`
   - `.venv/bin/pytest tests/test_cloud.py tests/test_ingest_patch.py tests/test_agentic_ops.py tests/test_agent_tools.py tests/test_agent_session.py`
   - `.venv/bin/pytest`（全量测试无回归）
2. **新增单元测试覆盖**：
   - `provider` 解析、`--provider` 临时覆盖与 `cloud use` 切换不覆盖已有 profile 连接；
   - `parse_login_command` / `render_ssh_config` 对 `cloudflared` 隧道解析与注入拦截；
   - Colab `doctor` / `fix-env` / `down` / CU 账本核算纯函数；
   - `colab-drive` 完整闭环（`connect` → `up` → `sync-code` → `push` → `run` → `logs` → `pull` → `hold` → `release` → `down`，以及 `lib-push`/`lib-captions`/`lib-pull`）；
   - `tools/colab_worker.py` 任务分派、白名单校验与异常日志落盘；
   - `tts` / `asr` / `vindex` 经 `AVA_REMOTE_MODELS` 解析模型目录；
   - `ava` agent 对 `cloud use` 逐条弹卡与 `--provider` 透传校验。
3. **Notebook 结构校验**：`json.loads` 解析 `tools/ava_colab_bootstrap.ipynb` 为合法 `.ipynb` 格式。
4. **待人手验项（标注，不伪造通过）**：在真实 Google Colab 运行一次 `ava_colab_bootstrap.ipynb`，分别实测 `colab`（`cloudflared` SSH）与 `colab-drive` 跑通 `probe` / `tts` / `captions`。
