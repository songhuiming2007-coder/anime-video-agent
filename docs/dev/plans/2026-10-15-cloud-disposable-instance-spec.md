# D102 云端实例改为用完即弃：模型放文件存储，哪台有卡开哪台（施工图）

> 状态：**待人确认**（2026-10-15）。第五节有要人定的点。
> 关联：ADR-0014（云端无头模型栈）、ADR-0016（渲染在本地、推理在云端）、`config/cloud.json` 的 `_instance_lifecycle_note`（9 月 11 日定的「停机保号」，本图推翻它）。

## 一、目标与对应 issue

- 对应 issues **D102**。
- **现象**：10 月以来每次要用卡，AutoDL 都报「该主机空闲 GPU 不足：主机 GPU 空闲数量 0 卡」。按量计费的实例绑定在一台物理机上，关机后卡会被别人租走；我们的 33GB 模型放在这台机器的数据盘上，所以只能等这一台的卡。2026-10-15 傍晚的算力市场快照：西北B区全区还有 vGPU-32GB 3 张、RTX 6000D 4 张、H800 2 张空闲，但我们那台（F29 机）是 0 张。
- **根因**：9 月 11 日定「停机保号，不释放实例」时，没考虑到实例和物理机是绑死的。
- **目标**：模型放进西北B区的**文件存储**（`/root/autodl-fs`，全区任何实例开机自动挂载，不随实例释放）；系统环境存成**自定义镜像**。要用卡时，在西北B区任挑一台有空卡、驱动够新的机器，用镜像开新实例，把 SSH 登录指令交给 AVA，然后照常 push、run、pull；用完关机，实例当成一次性的。

## 二、已完成的准备（2026-10-15，人授权后经 claude-in-chrome 操作）

1. 西北B区文件存储已初始化（200GB 容量，20GB 以内免费，超出部分 ¥0.01/GB/日；连续 3 个月不登录或欠费 50 元以上会被清空；文件数上限 20 万）。
2. F29 机已用无卡模式开机，`/root/autodl-fs` 已挂上。
3. 用到的 5 个模型已用 rsync 拷进 `/root/autodl-fs/models/`，目录结构和原来一样（`Qwen/Qwen3-TTS-12Hz-1.7B-Base`、`Qwen/Qwen3-VL-8B-Instruct`、`openai/whisper-large-v3`、`FunAudioLLM/SenseVoiceSmall`、`BAAI/bge-m3`），共 30.1GB（rsync 退出码 0，用时 371 秒，写入 60–95MB/s；拷完逐文件比对文件数与字节数，5 个目录全部一致），每天约 ¥0.10。**没拷**的：`IndexTeam/IndexTTS-2`（11G，已出局）、`IndexTeam/IndexTTS-2.5`（5.2G）、`iic/CosyVoice2-0.5B`（5.3G，从没评估过）；它们还留在 F29 的数据盘上，F29 释放时会一起没掉。
4. F29 关机后已保存镜像 `ava-py312-torch28-cu128-20261015`（`image-e9940b7867`，11.39GB，PyTorch 2.8.0 / Python 3.12 / CUDA 12.8，缓存在西北区；30GB 以内免费）。
5. **发现**：保存之前控制台「我的镜像」是**空的**。`cloud.json` 里的 `"image": "anime-video-agent-v2:py312-torch2.8-cu128"` 和 `_instance_lifecycle_note` 里「镜像的角色是保险」说的那份镜像并不存在。第 4 步补上了这份镜像。

## 三、改动清单（锚点引原文，不给行号）

### A. 连接信息不再写死在 `~/.ssh/config`

现状：所有 ssh / rsync 都连别名 `autodl`（`_ssh_host` 返回 `cfg_local.get("ssh_host", "autodl")`），别名在 `~/.ssh/config` 里写死成 `connect.weste.seetacloud.com:34557`。每换一台实例都要手改用户的 ssh 配置。

| 文件 | 锚点 | 改动 |
|---|---|---|
| `pipeline/cloud.py` | 新子命令 `connect` | `cloud connect "<控制台复制的登录指令>"`：解析 `ssh -p <端口> <用户>@<主机>`，只把 `server` / `port` / `user` 写进 `config/cloud.local.json`（其余键原样保留，token 不碰），再生成 `data/cloud/ssh_config`（见下一行）并跑一次 `true` 验证连通。解析不出就报错，把原文里认出来的部分列出来 |
| `pipeline/cloud.py` | `ssh_args = ["ssh", "-o", "BatchMode=yes", host, cmd]` 以及 `run_rsync` 的 `"-e", "ssh"`、`cmd_exec`、`cmd_logs`、`cmd_attach` 里直接调 `ssh` 的地方 | 统一加 `-F data/cloud/ssh_config`。该文件里写 `Host autodl`（HostName / Port / User 取自 `cloud.local.json`）、`StrictHostKeyChecking accept-new`、`UserKnownHostsFile data/cloud/known_hosts`，末尾 `Include ~/.ssh/config`，沿用用户原有的密钥设置。文件不存在时退回旧行为（直接用 `~/.ssh/config` 的别名），所以老配置不受影响 |
| `pipeline/agent/tools.py` | `"cloud": {"status", "logs", "doctor", "up", "down", ...}` | 白名单加 `connect`。它写的是 `config/cloud.local.json` 里的连接字段，算 Code Freeze 的新例外（同读音表、标定值、曲库登记一样，人审卡批准后由 CLI 写入）；AGENTS.md 第二节例外清单同步加第 ④ 条 |

**为什么不让 AVA 直接读写 `cloud.local.json`**：这个文件里有 `api_token`，在 `RESTRICTED_EGRESS_PATTERNS` 里拦着；由 CLI 写，AVA 只看到「已连接 xxx:端口」。

### B. 模型目录指向文件存储

现状：模型路径在三处写成 `/root/autodl-tmp/models/...`（`cloud.json` 的 `remote_models`、`voice.cloud.json` 的 `model`、`asr.py` 的 `CLOUD_WHISPER_DIR` / `CLOUD_SENSEVOICE_DIR`）。

做法：**路径一处都不改**，由 `cloud up` / `cloud connect` 在远端做一次幂等的 `ln -sfn /root/autodl-fs/models /root/autodl-tmp/models`（新实例的数据盘是空的；F29 那种已有实体目录的，不动它，报告一句）。好处是改动面最小，旧实例照常能用。

**读取速度已实测**（10-15，无卡模式的 F29 上 `dd` 读 Qwen3-VL 一个 5GB 分片）：82MB/s。按这个速度，Qwen3-VL 17.5GB 加载约 3.5 分钟，TTS 4.5GB 约 1 分钟。先拷到本地盘再加载，读文件存储这一步省不掉，所以拷贝没有好处，**直接软链**。带卡的机器 CPU 更多，可能更快，在第一台新实例上复测一次冷启动，结果写进 `cloud.json` 的 note。

### C. `cloud up` 改成告诉人怎么租

| 文件 | 锚点 | 改动 |
|---|---|---|
| `pipeline/cloud.py` | `print(f"    请前往 AutoDL 控制台手动开机实例（模式选择：{mode}）：")` | 不可达时改为打印租机条件：西北B区；GPU 显存 ≥ 本次任务需要（TTS 约 12GB、VLM 打标约 24GB，见第五节 ③）；CUDA ≥ 12.8（镜像是 cu128）；镜像选「我的镜像」里的 `<cloud.json 的 image>`。然后提示把登录指令交给 AVA 走 `cloud connect` |
| `pipeline/cloud.py` | `detect_runtime_mode_from_gpu_probe` 之后 | 加一个探针：`nvidia-smi --query-gpu=name,memory.total`，把 GPU 型号和显存写进会话文件（`session_data`），账本从此记录实际用的是什么卡 |

### D. `cloud doctor` 多查三项

- `/root/autodl-fs` 挂上了没有（`mountpoint -q` 或 `df` 里有 `AutoFS`）；没挂上就给修法（控制台账号设置里看文件存储挂载状态、重启实例）。
- `/root/autodl-tmp/models` 是不是指向文件存储的软链，或者是有完整模型的实体目录。
- 显存够不够：低于第五节 ③ 定的值时报 WARN，说明哪类任务会 OOM。

### E. 计费账本

`hourly_rate_cny: 2.4` 是按 6000D 写的。以后卡型不固定，改为 `cloud.json` 加一张 `gpu_rates_cny`（型号 → 元/时，取值注明哪天从算力市场抄的），按 C 探到的型号查；查不到就按 `hourly_rate_cny` 记，并在账本里标「估算」。今天抄到的价格：西北B区 vGPU-32GB ¥1.58/时（会员 9.5 折后）。

### F. 镜像

已保存（第二节第 4 步）。`cloud.json` 的 `image` 改成 `ava-py312-torch28-cu128-20261015`。注意 SSH 免密靠的是镜像里系统盘上的 `/root/.ssh/authorized_keys`（控制台账号级「SSH 免密登录」里没有登记公钥）；要更稳，可以把本机公钥登记到账号级，人定。之后每改一次远端环境（装包、升级 torch），都要重新保存一次镜像，写进 `docs/runbook/03-tts.md` 的云端一节。

### G. 文档与 AVA 提示

- `config/cloud.json`：改写 `_instance_lifecycle_note`（推翻「停机保号」，写清本图的流程和理由），删掉 `_retention_defense_note`（不再需要每 10 天开一次机），`image` 字段在镜像真正存好之后再写回。
- `docs/runbook/03-tts.md`、`docs/WORKFLOW.md` 云端那几行：流程改为「租机（人，约 1 分钟）→ `cloud connect`（卡）→ push / run / pull → down」。
- `config/agent/scopes/production.md` 或 `pipeline.md`：一句话说明人贴来 `ssh -p … root@…` 这种指令时，提议 `cloud connect`，不要尝试自己 ssh。

## 四、新常量 / 新判据的取值理由

- 显存门槛：TTS 模型 1.7B，bf16 权重约 4GB，加上推理开销，12GB 有余量；Qwen3-VL-8B 的 bf16 权重 17.5GB，24GB 大概率够，32GB（vGPU-32GB）稳妥。**都要在第一台新实例上实测峰值再写进配置**（AGENTS.md 判据第 7 条：带「可能」的判断不进配置）。
- CUDA ≥ 12.8：镜像里是 torch 2.8 + cu128，驱动支持不到 12.8 的机器跑不起来。

## 五、要人定的点

1. **F29 什么时候释放**：新流程跑通一次之后，F29 上没拷走的 21.5GB（IndexTTS-2 / 2.5、CosyVoice2）会随之消失。要不要留，留的话也拷进文件存储（每天多 ¥0.2 左右）？推荐不留：IndexTTS 已出局（ADR-0017），CosyVoice2 从没评估过，以后要用时可以重新下载。
2. **`cloud connect` 算不算新的 Code Freeze 例外**：推荐算（第三节 A），否则每次换实例都得人手改 `cloud.local.json`。
3. **显存门槛**：等实测，见第四节。
4. 只开西北B区的文件存储，还是再开一个区做后备（多一份约 ¥0.09/天）：推荐先只开西北B区（10-15 快照里它空卡最多，而且横幅公告说该区要陆续上线 1000 张 vGPU-32G，10 月下旬再上线 400 张 PRO 6000）。

## 六、测试要求

- `connect` 的解析是纯函数：用控制台真实格式 `ssh -p 34557 root@connect.weste.seetacloud.com` 及其变体（多空格、带 `-o` 选项、缺端口）做用例；写入 `cloud.local.json` 后其他键（含 `api_token`）原样保留——用临时目录里的假文件测。
- ssh / rsync 参数里带上 `-F data/cloud/ssh_config`，文件不存在时不带：断言命令行。
- `up` 不可达时的打印里有地区、显存、CUDA、镜像名四项。
- doctor 的三项新检查各有通过 / 失败用例（远端调用用现有 `_ssh` 打桩方式）。
- 期望值先在实现上跑通，再做变异检验。

## 七、验证命令

```bash
uv run pytest
uv run python -m pipeline.cloud connect "ssh -p <端口> root@<主机>"   # 在一台新实例上
uv run python -m pipeline.cloud doctor
uv run python -m pipeline.cloud run <期目录> probe                   # 测模型加载时间与显存峰值
```

## 八、明确不做

- 不用 AutoDL 弹性部署 API 自动找卡开机：该 API 要企业认证（官方文档 `docs/esd_api_doc/`），个人账号用不了。
- 不改模型路径常量（第三节 B 用软链解决）。
- 不做跨区自动切换。

## 九、完成判定

在一台**不是 F29** 的西北B区实例上，用镜像开机 → `cloud connect` → `cloud doctor` 全绿 → 跑通一期 TTS（或一次 VLM 打标）→ pull 回本地 → `cloud down`，账本记录了实际 GPU 型号。
