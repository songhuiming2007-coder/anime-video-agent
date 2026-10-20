"""集键（`SxxEyy` / `SPnn`）格式的唯一定义（D127）。

SP 两位起、满 99 进三位（ADR-0010 决策二，2026-10-20 修订）：`SP01`…`SP99`、`SP100`…`SP999`。
生成走 `sp_key`，解析引用 `SP_NUM` / `SP_NUM_LOOSE`，各模块不再各写一份正则——
`vindex.key_of` 的注释早就写过各写一份的下场：某天 SP 改成三位，另一处静默按旧格式找文件。

纪律：只依赖标准库（clips / check_script / render / cloud 都要 import，别把它们的依赖链拖长）。
"""

from __future__ import annotations

import re

#: 一个池最多到 SP999：supercell 两卷就用满了 99，四位离现实还远（到时再议）。
SP_MAX = 999

#: 规范形的数字部分：两位补零或三位，不收 `SP1`、`SP001`、`SP099`——同一个号只有一种写法，
#: 台账、文件名、镜头表才对得上。登记、索引、云端、标定这类机器读写的地方用它。
SP_NUM = r"(?:\d{2}|[1-9]\d{2})"

#: 人写的地方（锚点、`集:`、`复用:`、avsync 画面）容忍 `SP1` / `SP01` / `SP100`，取出数字后经
#: `sp_key` 归一成规范形再用。用的地方要么整串匹配（fullmatch / `$`），要么后面跟分隔符，防 `SP1000` 被截成 `SP100`。
SP_NUM_LOOSE = r"\d{1,3}"


def sp_key(n: int) -> str:
    """SP 号 → 规范集键：两位补零，三位原样。"""
    if not 1 <= n <= SP_MAX:
        raise ValueError(f"SP 号超出范围 1–{SP_MAX}：{n}")
    return f"SP{n:02d}"


_SORT = re.compile(r"S(\d+)E(\d+)|SP(\d+)")


def key_sort(key: str) -> tuple:
    """集键按数字排：番剧集在前（季、集），SP 在后；`SP100` 排在 `SP99` 之后而不是 `SP10` 与 `SP11` 之间。
    认不出的键排最后、按原文。"""
    m = _SORT.fullmatch(key)
    if m is None:
        return (2, 0, 0, key)
    if m.group(3) is not None:
        return (1, int(m.group(3)), 0, key)
    return (0, int(m.group(1)), int(m.group(2)), key)
