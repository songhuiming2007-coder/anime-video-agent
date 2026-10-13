# Runbook: 10 企划总装（多卷 → 合集）

企划（多卷系列）各卷都出完成片后，把它们拼成一期合集。**只在企划会话里跑，每次都弹卡由人批**（D81，人裁决 Q5）。

## 输入物
- 企划目录下的 `00-project.md`：卷表决定部件顺序与每卷片尾（`余韵N秒` 截尾、`完整保留` 原样），`合集:` 指向输出期。
- 每卷：`05-final.mp4`、`08-cover.jpg`（过场卡素材）；`余韵N秒` 的卷另需 `02-script.md` 与 `03-audio/manifest.json`（算截断点）。
- 合集期目录：要先建好且有 `01-topic.md`（qc 读它的 `时长目标`）。

## 命令
```bash
python -m pipeline.assemble data/episodes/<企划>                # 输出到合集期
python -m pipeline.assemble data/episodes/<企划> --out <目录>   # 输出到别处（目录须已存在、有 01-topic.md）
python -m pipeline.assemble data/episodes/<企划> --replace      # 覆盖已有 05-final.mp4（不留底）
```
企划会话里由 ava 经 `run_pipeline assemble` 发起，卡面列出部件、每卷截断点与输出位置；带 `--replace` 时卡面首行是「⚠ 将覆盖已有 05-final.mp4（不留底）」。卡不开放「本会话都批准」；bash 直接跑同样弹卡。

## 它做什么（顺序固定）
前置检查（卷表、部件、输出目录、空闲空间；不过退 2，什么都不建）→ 截断点（`build_timeline` 的旁白终点 + 余韵，不写预测值）→ 每卷 3 秒封面过场卡 → 截尾 + 1 秒淡出 → 流签名逐项比对（以第一卷为基准，不一致退 1）→ concat 无损拼接 → 自证（总长 = 部件之和、全片解码零报错）→ `04-assembly.json` 部件账本 → `pipeline.qc`。

## 红线
- **不重配 TTS、不重渲分期主体**：只截尾或原样拼接。
- 自证没过、或总装途中某卷成片被改动 → 退 1，旧成片原样保留、不写账本。
- 退 3 = 另一个总装正在用同一个输出目录，等它跑完。
- 成片交人前至少听一遍各章节交界。
