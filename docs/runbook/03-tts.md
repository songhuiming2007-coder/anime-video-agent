# Runbook: 03 语音合成（TTS）

根据定稿脚本进行增量语音合成，确定各段真实物理时长。

## 执行
agent 先问人，再经 `run_pipeline` 跑 `tts`（期目录自动补上）。云端无头 GPU 实例（ADR-0016）：agent 依次提议 `cloud up` → `cloud push <期目录>` → `cloud run <期目录> tts` → `cloud pull <期目录>`，开机（up）与跑任务（run）是计费卡；跑完提议 `cloud down`（也弹卡）。

## 云端实例用完即弃（D102）

实例绑定一台物理机，关机后那台的卡会被别人租走，所以不再保留固定实例。模型在西北B区的文件存储（`/root/autodl-fs/models`，全区实例开机自动挂载），环境在自定义镜像里（`config/cloud.json` 的 `image`）。

1. 人租机（约 1 分钟）：算力市场选西北B区、驱动支持 CUDA ≥ 12.8、显存够本次任务的机器（VLM 打标 ≥ 24GB；只配音不需要大卡），镜像选「我的镜像」里 `config/cloud.json` 的 `image`。`cloud up` 连不上时会打印这几项。
2. 人把控制台的 SSH 登录指令（`ssh -p 端口 root@主机`）和这台实例的按量单价（元/小时）贴给 agent，agent 提议 `cloud connect "<原文>"`（弹卡）：写连接信息、生成 `data/cloud/ssh_config`、验连通、把 `/root/autodl-tmp/models` 软链到文件存储。
3. `cloud doctor` 看文件存储挂载、软链、显存、模型，以及远端代码与本地是否一致（`[CODE]`；不一致先 `cloud sync-code`，否则 `run` 不开跑）；之后照常 `up --rate <单价>` → `push` → `run` → `pull` → `down`。卡和单价每次都可能不同，账本按人报的单价记；没报就按 `gpu_rates_cny` 参考价或 `hourly_rate_cny` 估算并标出，结算时可用 `down --rate` 补。
4. 改过远端环境（装包、升级 torch）后要在控制台重新保存一次镜像，并更新 `config/cloud.json` 的 `image`；不保存的话下一台新实例还是旧环境。

底层排查参考：`python -m pipeline.tts data/episodes/<期号>`；云端 `python -m pipeline.cloud connect|push|run|pull …`。

## 产物与位置
- `data/episodes/<期号>/03-audio/seg-*.wav`（逐段音频）
- `data/episodes/<期号>/03-audio/manifest.json`（各段实际物理时长与回读元数据）

## 开配前先定本地还是云端

`01-topic.md` 写了 `配音: 本地` 或 `配音: 云端` 就照办；没写，agent 先问人，不自己挑。首次配音之后，重配与 apply 一律跟随本期 `manifest.json` 记录的引擎侧（见 03.5「核心纪律」第 3 条），不再问。

## 规则
1. **配音只有首次是全量，后续改动一律增量**：
   - 严禁擅自使用 `--force` 或 `--force-all` 全量重配洗掉已通过音频；
   - 点名重配：录纠错（桌面端顺听面板、终端 `/voice`，或 agent 跑 `corrections add`）→ `tts --apply-patch`；或 agent 跑 `tts --redo 2,4,5`（段号逗号分隔）。重配哪几段先列给人定，人同意再跑。
2. **多音字错音修正**：改读音表，不改稿、不动音色参考干声。三层表（全局 `config/voice.json` 的 `pinyin_injections` / `readings`，期级 `03-audio/corrections.json`）的分工与写法见 `03.5-voice-check.md`。
3. **换引擎/改全局基准必须先报影响范围**：动全局参数前必须告知用户会有多少段作废，由人类拍板。
4. **句内死寂判不合格**（D87）：每个合成单元裁掉首尾静音后，句内连续静音（50 ms 窗 RMS < -45 dB）达到 2.0 s 就判不过，换种子重试，而且不能走 ASR 盲区豁免。拆句段如果在基准种子下必出死寂，会整段统一改用第一遍试出来的替代种子（stderr 有 WARN，`seeds_used` 有记录）；替代种子也不过才报错，提示人去钉种子。
5. **同一期同时只能有一个 tts 在合成**：所有会合成的跑法都要拿 `03-audio/.tts.lock`。第二个进程会直接报「已有一个配音进程在跑」，这时等前一个跑完，进度在 03.5 预览栏看。持锁进程已经不在了（比如作业被强停）的残留锁会被自动接管。这把锁和 `.apply_patch.lock` 是分开的，普通重配期间仍然可以边听边标错。
