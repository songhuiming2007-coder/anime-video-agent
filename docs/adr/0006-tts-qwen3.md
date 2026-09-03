---
related-issues: D21, N7, N11, N12, N19, N22
related-plans: —
status: accepted
---

# ADR-0006：配音引擎换成 Qwen3-TTS 1.7B Base（多语种）

日期：2026-08-15
状态：已采纳
前置：ADR-0002（决定一「本地不用云」不变；决定二/三已被本 ADR 取代）

## 背景

ADR-0002 定的 IndexTTS-1.5 是**纯中文模型**。罪恶王冠音乐乐评期（2026-08-15）的
旁白里有英日歌名（My Dearest、エウテルペ、The Everlasting Guilty Crown…），
IndexTTS 把 `My Dearest` 念成 `Femi Dearest Faye`（实测），CER 28% 且人耳直接出戏。

两条路：
1. 稿子里的歌名全部改成中文指代（「第一首片头曲」），歌名靠画面字幕呈现——试过，效果大打折扣；
2. 换多语种 TTS 引擎，歌名直接念原文——本文档说的就是这条路。

## 决定一：引擎 = Qwen3-TTS 1.7B Base（mlx-community 转换版）

| 候选 | 结论 |
|---|---|
| Qwen3-TTS 0.6B Base | 多语种（中英日韩德法俄葡西意），音色最像参考（用户原话「完全是可爱的音色」），但**口齿不清**——「第一」「响起」这类词念糊，是模型太小，不是克隆问题 |
| Qwen3-TTS 1.7B Base | 口齿清晰度「改善可以说很大」，音色略逊于 0.6B，用户拍板要清晰度，**采纳** |

0.6B（4.4G）与 1.7B（4.3G）均已下载到 SSD。0.6B 暂留作音色对照，验证 1.7B 整篇
语速/音色稳定后再删。

## 决定二：克隆走 x-vector 路径，不传 ref_text

新版 mlx-audio（0.4.8 重写版，`qwen3_tts` 模块）有两条克隆路径：

| 路径 | 触发条件 | 实测结果 |
|---|---|---|
| ICL（In-Context Learning） | `ref_audio` + `ref_text` 齐传 | **有 bug：模型把 ref_text 当正文复述**。用户听输出，听到的是参考转录原文（たよりになる仲間が増えるなんて…）而不是目标文本 |
| x-vector 说话人嵌入 | 只传 `ref_audio`，不传 `ref_text` | 念的是目标文本，音色照样来自参考，实测正常 |

ICL 复述的根因在 mlx-audio 的 `_prepare_icl_generation_inputs`：
`combined_text_ids = ref_text_ids + text_ids` 拼接后整体当文本输入，模型学到的行为是
「把整段（含参考转录）念出来」。上游没修好之前**不要启用 ICL 路径**。

所以 `config/voice.json` 的 `ref_text` 保持 `null`，`pipeline/tts.py` 的 qwen3 分支
只传 `ref_audio` + `lang_code`。这也顺带消除了对 `transcripts.json` 的依赖。

## 决定三：语言用 lang_code 控制，写进 voice.json

Qwen3-TTS 的 `lang_code` 参数控制发音语言（auto / chinese / english / japanese…）。
本项目的旁白是中文、夹英日歌名，实测 `lang_code="zh"` 时中文正常、歌名按原文念。

`config/voice.json` 新增 `lang_code` 字段（默认 `"auto"`，本项目 `"zh"`）。
这是**内容相关**配置：换番时若旁白语言变化，改这一个字段即可，不动代码。

## 音色：seg7（2026-08-15 用户自录）

用户录了 7 段日文参考干声（`~/sysaudio/seg1-7.wav`，48kHz 双声道，峰值 -5 dBFS）。

**试听踩过一个坑：参考干声音量太低会把整个克隆搞坏。** 第一批参考音峰值只有
0.043–0.048（约 -26 dBFS），模型提取不到说话人特征，输出是「像嚎叫的杂音」。
重新录制（峰值约 0.5 / -5 dBFS）后正常。

候选 7 段各生成同一句，再取前 4 段念完整旁白句复选。**seg7 胜出**（用户原话「最后一个最好」）。
完整句子试听存 `data/voice/probe/p12-seg7.wav`。

选音色判据沿用 ADR-0002：语速优先、音色最后。本项目的语速估算是另一条线
（`script.cpm`），换引擎后 cpm **必须重测**——1.7B 与 IndexTTS 的语速特性不同
（IndexTTS 语速与文本长度正相关 r=+0.613，Qwen3 待实测）。

## 回读质检：拼音比对保留，英日部分按字符保留

ADR-0002 的「回读比对在带声调拼音上做」（`tts.syllables()`）继续有效。
`normalize` 后，汉字走 pypinyin、其余字符（英文/日文假名）按原样逐字保留——
歌名虽然 Whisper 回读可能听岔（エウテルペ→Eutelope），但漏读/重复/跑飞
改变的是音节数量与顺序，拼音/字符照样抓得住。

第一次整篇配音时留意：含歌名的段落 CER 会偏高（英日假名 vs 拼音比对），
如误伤严重，给歌名区段单独做豁免，不给整段放松阈值。

## 遗留

### 补记（2026-08-27）：x-vector 参考优化实验，三路全否，seg6 维持现状

为治「1.7B + 日文参考配中文，常用词声调跑偏（少年/警察/体制/世界，readings 表持续在补）」的问题，跑了三组对照实验，同句同种子盲听，**seg6 全胜**：

| 参考 | 结果 |
|---|---|
| 母带长参考（2h17m 读书视频抽轨，4:01-9:00 纯朗读段，归一 -5dBFS） | 底噪大、音量低 4dB——成片音轨的制作染色（压限/混响尾）被 x-vector 编码进嗓音表征 |
| 母带清洗参考（afftdn 降噪 + loudnorm 链） | 听感「远远不如 seg6」——稳态降噪只去加性噪声，救不回动态损伤 |
| seg6（现状） | **胜** |

结论：**参考质量 >> 参考时长**。ECAPA 长参考的表征增益是真的（结构上无截断已核实），但前提是干净录音；脏母带做不出好参考。合成速度/内存与参考时长无关（0.22× 实时，两者一致）。

另：4bit 量化版（`mlx-community/Qwen3-TTS-12Hz-1.7B-Base-4bit`）引擎切换初期已实测音色完全丢失，否决——量化对音色伤害远大于对口齿，x-vector 说话人表征对数值精度极敏感。**音色资产寄生在合成模型内部表征上，换量化/版本/框架即陪葬**，这是 RVC 两段式的核心动机。

发音稳定性问题的下一步若要再攻，只剩 RVC 两段式（发音交给念得稳的中文引擎 + 母带训 RVC 转换音色），未立项。素材已备：`data/voice/raw/vtuber_part1-4.wav`（2h17m，需剔除变声线段）。

- `data/models/hub/models--mlx-community--IndexTTS-2-fp16` 4.4G：ADR-0002 遗留的废模型，
  现在引擎已换 Qwen3，**可以删**。
- `_indextts_audio`（自写解码循环）与 `_tail_artifact`（句尾机械声裁剪）仍留在
  `pipeline/tts.py`：IndexTTS 可能回归复用，暂不删；Qwen3 分支不需要它们。
- `readings` 读音替换表（楪祈→夜祈 等）是 IndexTTS 的补偿机制。Qwen3-TTS 多语种、
  多音字处理更强，换引擎后**逐条实测再决定去留**，暂时保留（换了无害、删了要重测）。
- 0.6B 模型待 1.7B 整篇验证后删除。
