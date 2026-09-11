#!/bin/bash
# M2a 收尾：全量重跑 → 拉回 → 本地组装 m2-probe 目录
#
# 为什么必须 --force：tts.py 的增量复用判据看不出引擎代码变了
# （2026-09-11 事故：修完 seed 与 prompt 缓存后，27 段被静默复用为修复前产物）。
# 现在指纹里有了 SYNTH_LOGIC_VERSION，但这一步不赌它，显式 --force。
#
# 前置：本地 git 工作区干净（云端是 rsync 过去的，不含 .git）
set -euo pipefail
cd "$(dirname "$0")/.."

EP_REMOTE="data/episodes/EGOIST-传奇企划志/01-借躯降生"
DST="data/episodes/m2-probe"

echo "== 1/5 同步代码到云端 =="
rsync -az --exclude='.git' --exclude='data/' --exclude='__pycache__' \
      --exclude='.venv' --exclude='*.pyc' ./ autodl:/root/anime-video-agent/

echo "== 2/5 开机（带卡，会真实计费）=="
uv run python -m pipeline.cloud up --mode gpu

echo "== 3/5 全量重跑 34 段（约 10 分钟）=="
uv run python -m pipeline.cloud exec \
  "cd /root/anime-video-agent && /root/it-venv/bin/python -m pipeline.tts run \
   '$EP_REMOTE' --config config/voice.cloud.json --force"

echo "== 4/5 拉回 =="
uv run python -m pipeline.cloud pull "$EP_REMOTE/03-audio"

echo "== 5/5 关机 + 本地组装 =="
uv run python -m pipeline.cloud down

rm -rf "$DST"
mkdir -p "$DST/03-audio"
cp "$EP_REMOTE/01-topic.md" "$EP_REMOTE/02-script.md" "$DST/"
cp "$EP_REMOTE"/03-audio/*.wav "$DST/03-audio/"
cp "$EP_REMOTE"/03-audio/manifest.json "$DST/03-audio/manifest.json"

echo
echo "完成。下一步（需你人耳）："
echo "  1. 顺听 $DST/03-audio/（34 段）"
echo "  2. python -m pipeline.tts $DST --review voice=?,prosody=?,misread=?"
echo "  3. python -m pipeline.eval freeze $DST --tag m2-probe"
echo "  4. python -m pipeline.eval diff v1-baseline m2-probe"
