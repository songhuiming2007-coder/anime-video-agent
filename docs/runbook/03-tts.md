# Runbook: 03 语音合成（TTS）

根据定稿脚本进行增量语音合成，确定各段真实物理时长。

## 执行命令
```bash
python -m pipeline.tts data/episodes/<期号>
```
*云端无头 GPU 实例跑法（ADR-0016）：*
```bash
python -m pipeline.cloud push && python -m pipeline.cloud run <期号> tts && python -m pipeline.cloud pull
```

## 产物与位置
- `data/episodes/<期号>/03-audio/seg-*.wav`（逐段音频）
- `data/episodes/<期号>/03-audio/manifest.json`（各段实际物理时长与回读元数据）

## 核心规程与铁律
1. **配音只有首次是全量，后续改动一律增量**：
   - 严禁擅自使用 `--force` 或 `--force-all` 全量重配洗掉已通过音频；
   - 点名重配：走 `/voice` 纠错 → `--apply-patch`，或 `python -m pipeline.tts <期> --redo XX`。
2. **多音字错音修正**：通过 `pipeline/g2p.py` 注入拼音（全局表 = 长期沉淀层；期级 overlay 同名键优先，改全局表不动已有 overlay 的段），严禁乱动音色参考干声。
3. **换引擎/改全局基准必须先报影响范围**：动全局参数前必须告知用户会有多少段作废，由人类拍板。
