#!/usr/bin/env bash
# 环境自检。任何 pipeline 脚本运行前先跑，或 source 本文件复用 require_data。
#
#   ./pipeline/preflight.sh                    自检
#   ./pipeline/preflight.sh --init             在仓库里建 data/ 实体目录 + 骨架
#   ./pipeline/preflight.sh --init /Volumes/X/anime-video-data
#                                              在外置盘上建骨架，data/ 做符号链接指过去
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DATA="$ROOT/data"
fail=0

# 最重要的一条：data/ 不可达时必须报错退出，**绝不自动创建**（CLAUDE.md「存储约定」）。
# 三种失败分开报——修法完全不同，混成一句会让人查错方向。
require_data() {
  if [ ! -e "$DATA" ]; then
    if [ -L "$DATA" ]; then
      echo "FAIL  data 是悬空的符号链接，指向 $(readlink "$DATA")" >&2
      echo "      插上那块盘再跑。别 mkdir——会在挂载点里建实体目录，盘插上反而看不见" >&2
    else
      echo "FAIL  data 不存在。先建骨架：./pipeline/preflight.sh --init [外置盘目标目录]" >&2
    fi
    return 1
  fi
  if [ ! -d "$DATA/library" ]; then
    echo "FAIL  data 在，但 data/library 不在——骨架不全，或者指错了位置" >&2
    echo "      ./pipeline/preflight.sh --init [外置盘目标目录] 会补齐" >&2
    return 1
  fi
  return 0
}

# 被 source 时只提供函数，不执行检查
(return 0 2>/dev/null) && return 0

# --init：建骨架。**显式动作**，而且只在 data/ 还不存在时干活——
# 已经有 data/ 却指错位置时，替人「修好」它比报错更危险。
if [ "${1:-}" = "--init" ]; then
  target="${2:-}"
  if [ -e "$DATA" ] || [ -L "$DATA" ]; then
    where=$(readlink "$DATA" 2>/dev/null || echo "实体目录")
    # 花括号是必须的：全角「）」的字节会被 bash 当成变量名的一部分
    echo "FAIL  data 已存在（${where}）" >&2
    echo "      要换位置请自己处理，脚本不动已有数据" >&2
    exit 1
  fi
  if [ -n "$target" ]; then
    mkdir -p "$target" || exit 1
    ln -s "$target" "$DATA" || exit 1
  else
    mkdir -p "$DATA" || exit 1
  fi
  # 骨架与 CLAUDE.md「目录结构」一致。models/hub 由 HF_HOME 自己建，不预建。
  mkdir -p "$DATA"/library/{raw,subs,index,notes,bgm,shots,vindex} \
           "$DATA"/models/local \
           "$DATA"/voice/{reference,probe} \
           "$DATA"/episodes || exit 1
  echo "OK    骨架已建：$(cd "$DATA" && pwd -P)"
  df -h "$DATA" | awk 'NR==2 {print "      落在 "$1"，剩余 "$4}'
  echo "      片源放 data/library/raw/<番>/，参考干声放 data/voice/reference/"
  exit 0
fi

require_data || fail=1

if require_data 2>/dev/null; then
  avail=$(df -h "$DATA" | awk 'NR==2 {print $4}')
  echo "OK    data 可达，剩余 $avail"
fi

# 只列流水线真正会调的外部命令。ffmpeg 必须带 libass（烧字幕）。
# 不列 yt-dlp 之类「拿素材时可能用到」的工具——自检里列了用不到的东西，
# 就会有人为了让它变绿去装一个流水线根本不碰的包。
for t in ffmpeg ffprobe python3; do
  if command -v "$t" >/dev/null 2>&1; then
    echo "OK    $t"
  else
    echo "FAIL  缺少 $t" >&2
    fail=1
  fi
done

# 有 ffmpeg 不等于能烧字幕：ass 滤镜要编译时链上 libass，缺了整条渲染在最后一步才炸。
# **别用管道判**：`set -o pipefail` 下 `ffmpeg -filters | grep -q` 会因为 grep 提前
# 关掉管道让 ffmpeg 吃 SIGPIPE，整条管道判非零——门禁在正确的安装上报 FAIL。
ass_probe=$(ffmpeg -hide_banner -h filter=ass 2>&1)
case "$ass_probe" in
  "Filter ass"*) echo "OK    ffmpeg 带 libass" ;;
  *) echo "FAIL  ffmpeg 没有 ass 滤镜，烧不了字幕（brew install ffmpeg 的默认版本带）" >&2
     fail=1 ;;
esac

# 系统盘余量偏低时提醒，避免误写
root_avail=$(df -g / | awk 'NR==2 {print $4}')
if [ "$root_avail" -lt 20 ]; then
  echo "WARN  系统盘仅剩 ${root_avail}G，确认所有大件都写在 data/ 下" >&2
fi

exit "$fail"
