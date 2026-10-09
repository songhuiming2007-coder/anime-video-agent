# Runbook: 03 语音合成（TTS）

根据定稿脚本进行增量语音合成，确定各段真实物理时长。

## 执行
agent 经 `run_pipeline` 跑 `tts`（弹卡，期目录自动补上）。云端无头 GPU 实例（ADR-0016）：agent 依次提议 `cloud up` → `cloud push <期目录>` → `cloud run <期目录> tts` → `cloud pull <期目录>`，每张都是计费卡；跑完提议 `cloud down`。

底层排查参考：`python -m pipeline.tts data/episodes/<期号>`；云端 `python -m pipeline.cloud push|run|pull …`。

## 产物与位置
- `data/episodes/<期号>/03-audio/seg-*.wav`（逐段音频）
- `data/episodes/<期号>/03-audio/manifest.json`（各段实际物理时长与回读元数据）

## 开配前先定本地还是云端

`01-topic.md` 写了 `配音: 本地` 或 `配音: 云端` 就照办；没写，agent 先问人，不自己挑。首次配音之后，重配与 apply 一律跟随本期 `manifest.json` 记录的引擎侧（见 03.5「核心纪律」第 3 条），不再问。

## 核心规程与铁律
1. **配音只有首次是全量，后续改动一律增量**：
   - 严禁擅自使用 `--force` 或 `--force-all` 全量重配洗掉已通过音频；
   - 点名重配：录纠错（桌面端顺听面板、终端 `/voice`，或 agent 经人审卡 `corrections add`）→ `tts --apply-patch`；或 agent 跑 `tts --redo 2,4,5`（段号逗号分隔，弹卡）。
2. **多音字错音修正**：改读音表，不改稿、不动音色参考干声。三层表（全局 `config/voice.json` 的 `pinyin_injections` / `readings`，期级 `03-audio/corrections.json`）的分工与写法见 `03.5-voice-check.md`。
3. **换引擎/改全局基准必须先报影响范围**：动全局参数前必须告知用户会有多少段作废，由人类拍板。
