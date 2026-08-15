#!/bin/bash
# 录制参考干声：BlackHole 2ch → 每段一个 wav（48kHz / 16bit / 双声道）。
#
# 用法：./pipeline/record_refs.sh [输出目录] [段数] [每段秒数]
#   默认：~/sysaudio/，7 段，每段 10 秒
#
# 前置：
#   - 已装 BlackHole 虚拟声卡（ffmpeg avfoundation 里叫 "BlackHole 2ch"）
#   - 播放音频前，把系统输出（或播放器输出）路由到 BlackHole 2ch
#
# 录音要求（实测踩过，见 ADR-0006）：
#   - 参考音峰值 ≥ -20 dBFS，否则模型提取不到说话人特征，克隆输出全是杂音
#   - 每段 8-10 秒连续说话，不要留大段静音
#   - 要「平静清晰」的声音，别选情绪起伏大的（seg7 起伏 CV 1.40，
#     克隆出带兴奋感、吞字；CV < 1.0 的段明显稳）
set -euo pipefail

OUT_DIR="${1:-$HOME/sysaudio}"
N="${2:-7}"
SECS="${3:-10}"
DEV="BlackHole 2ch"

mkdir -p "$OUT_DIR"

# `-list_devices` 以非零退出（`-i ""` 故意报错），pipefail 会把它当失败，
# 所以先抓输出再 grep，不能 `ffmpeg ... | grep` 直接当 if 条件。
dev_list=$(ffmpeg -hide_banner -f avfoundation -list_devices true -i "" 2>&1 || true)
if ! grep -q "$DEV" <<<"$dev_list"; then
    echo "FAIL 没找到音频设备 '$DEV'。先装 BlackHole，并把播放器输出路由到它。" >&2
    exit 1
fi

echo "设备：$DEV  →  $OUT_DIR/"
echo "共 $N 段，每段 $SECS 秒。每段开始前按回车（好切换播放内容）。"
echo

for i in $(seq 1 "$N"); do
    out="$OUT_DIR/seg$i.wav"
    echo "── 第 $i/${N} 段：准备好播放内容后，按回车开始录 ${SECS}s"
    read -r _ || true
    if ! ffmpeg -hide_banner -loglevel error -y -f avfoundation -i "none:$DEV" \
            -t "$SECS" -c:a pcm_s16le -ar 48000 -ac 2 "$out"; then
        echo "FAIL 第 $i 段录制失败" >&2
        exit 1
    fi
    vol=$(ffmpeg -hide_banner -i "$out" -af volumedetect -f null - 2>&1 \
          | sed -n 's/.*max_volume: \([0-9.-]*\) dB.*/\1/p')
    if awk -v v="$vol" 'BEGIN { exit !(v < -20) }'; then
        echo "  ✗ 已存 ${out}，峰值 ${vol} dBFS——太轻，要求 ≥ -20 dBFS，建议重录本段" >&2
    else
        echo "  ✓ 已存 ${out}（峰值 ${vol} dBFS）"
    fi
    sleep 1
done
echo "完成：$OUT_DIR/seg1.wav … seg$N.wav"
