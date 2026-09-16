---
related-issues: —
related-plans: —
status: rejected
---

# ADR-0009：RVC 两段式音色迁移 pilot 失败，停止投入

日期：2026-08-29
状态：**已否决**（pilot 训练完成，A/B 人耳判负；完整 23.9 分钟数据集训练取消）
前置：ADR-0006（Qwen3-TTS 1.7B Base x-vector 克隆路径）；
      `data/voice/README.md` 2026-08-27 长参考实验

## 背景

ADR-0006 选定 Qwen3-TTS 1.7B Base 做 x-vector 克隆，音色靠参考音频驱动。
2026-08-27 的长参考实验把生产参考从 6 秒 `seg6.wav` 换成 5 分钟
`vtuber_reading_dn_sqz75_n.wav`，靠清洗+停顿压缩+响度归一化把发音稳定性提到
CER 0%（六个历史错词），音色追平 `seg6`。

但当时已识别到天花板：**发音/口音由参考音频决定，而 VTuber 母带无法重录**。
一旦参考本身带口音或个别词念法不稳，TTS 会把这些缺陷复制出来。
RVC 两段式被设想为“解耦器”——用一段发音清晰的中文 TTS 做底，再用 RVC
把 VTuber 音色迁移上去。

## 实验

### 训练数据

- `tools/rvc/assets/raw/pilot/`
  - `vtuber_reading_dn_sqz75_n.wav`（约 3.6 分钟，生产参考）
  - `seg6.wav`（约 8.9 秒，历史基准）
- 预处理后 86 个有效切片，训练 50 epoch（约 3 小时，MPS 中后期掉到 30 分钟/epoch）。

### A/B 设计

生成同一句测试文本，统一响度到 -1 dBFS：

| 文件 | 路径 | 说明 |
|---|---|---|
| `direct_prod.wav` | `tools/rvc/pilot_ab/direct_prod.wav` | Qwen3 + 生产参考 `sqz75_n`（当前 baseline） |
| `rvc_prod.wav` | `tools/rvc/pilot_ab/rvc_prod.wav` | `direct_prod` 过 RVC pilot |
| `direct_clean.wav` | `tools/rvc/pilot_ab/direct_clean.wav` | Qwen3 + 干净短参考 `part2_020.wav` |
| `rvc_clean.wav` | `tools/rvc/pilot_ab/rvc_clean.wav` | `direct_clean` 过 RVC pilot（两段式目标） |

### 人耳判决

用户反馈：**四个样本都有念不对字、口音奇怪的问题。**

即：
- RVC 未能修复 VTuber 参考里的发音/口音缺陷（`rvc_prod` 不比 `direct_prod` 干净）。
- 用干净短参考做底的“两段式”也没能产出自然清晰的输出（`rvc_clean` 同样怪）。
- RVC 音色迁移本身没有达到“可接受”的听感门槛。

## 决定

1. **RVC 路线停止。** 不再用 23.9 分钟完整朗读片段训练 RVC，也不再往两段式 TTS→RVC
   架构追加投入。
2. **生产参考保持 `vtuber_reading_dn_sqz75_n.wav`**，继续走 Qwen3-TTS x-vector 直出路线。
3. **朗读片段库 `data/voice/reference/reading_clips/` 及 T7 备份保留**，作为音色素材库
   备忘，但当前不进入任何生产链路。
4. **RVC 训练产物保留在 `tools/rvc/` 但不删除**，作为失败实验归档； pilot 模型权重
   `assets/weights/pilot.pth` 与索引暂不移除，避免误删后无法复盘。

## 理由

1. **问题定位错了层。** RVC 只迁移音色/频谱特征，不纠正发音习惯或音位选择。
   如果底层 TTS 已经把“喰种”念成“餐种”或带 VTuber 口音，RVC 会把错误声音一起染色。
2. **两段式的“清晰底”假设不成立。** 干净短参考（`part2_020.wav`）合成的 Qwen3 输出本身
   仍带原 VTuber 素材的说话习惯，说明 x-vector 克隆对发音风格的影响比预期深；
   同时也说明我们手头没有真正“中性清晰”的参考源。
3. **RVC 本身质量未达标。** 即使不考虑发音，pilot 模型的音色输出也不自然，
   继续扩大数据集训练 200+ epoch 的成本/收益比不可接受（预估 5 小时以上且结果不可控）。
4. **长参考优化已被验证有效。** `sqz75_n` 已经在六个历史错词上做到 CER 0%，
   人耳也认可音色。应把精力收回到“继续优化 x-vector 参考”这条已知有效的路，
   而非另开一条高风险的训练线。

## 影响

- `docs/adr/0006-tts-qwen3.md` 中“RVC 两段式是下一步杠杆”的表述已过期，
  应以本 ADR 为准。
- `data/voice/README.md` 增加 RVC pilot 失败记录，避免未来重复尝试。
- `tools/rvc/` 目录进入“归档/不再维护”状态；若后续清理，需单独评估是否删除
  1.7 GB 预训练权重。

## 推翻条件

未来只有满足以下全部条件才重启 RVC：
1. 手头有质量明显高于 `sqz75_n` 的“清晰中文参考”或能拿到目标 VTuber 新录干声；
2. 小数据集 pilot（≤ 5 分钟）能在人耳 A/B 中明显击败当前 `sqz75_n` 直出；
3. 有明确的指标（而非纯人耳）能区分“音色像”和“发音对”。
