"""跨模块「逐字一致」纪律的机制化（2026-09-16 审计 G1）。

仓库里有几处**刻意双份维护**的判据（注释里写着「两处各自维护，改一处必须同步
另一处」）——双份的理由都成立（check_script/qc 不能 import clips 的 ML 栈；
align 是不能 import clips 的零依赖叶子），但「靠人肉记得同步」不是机制。

不一致的失败形态很毒：同一篇稿子在 02 机检通过、04 排片才报错（或反过来），
报错离病根隔一道工序，排查方向整个指错。这几条断言把纪律变成红绿机制：
任何一处改了、另一处没跟上，测试当场红。

- `clips._ANCHOR` ↔ `check_script.ANCHOR_TC`（锚点时间码语法）
- `clips._ANCHOR_FIELD` ↔ `check_script._ANCHOR_FIELD`（锚点字段续行规则）
- `qc.DURATION_FIELD(+_HINT)` ↔ `check_script.DURATION_FIELD(+_HINT)`（时长目标字段）
- `clips.MIN_CLIP` ↔ `align.REFIT_MIN_CLIP`（「比这更短会闪」在生成/重排两段的落实）

pattern 与 flags 一起比：`re.I`/`re.M` 是语法的一部分，只比字符串等于漏掉一半。
"""

from pipeline import align, check_script, clips, qc


def _same(a, b) -> bool:
    return (a.pattern, a.flags) == (b.pattern, b.flags)


def test_锚点时间码正则两处逐字一致():
    assert _same(clips._ANCHOR, check_script.ANCHOR_TC), \
        "clips._ANCHOR 与 check_script.ANCHOR_TC 必须逐字一致（改一处必须同步另一处）"


def test_锚点字段续行正则两处逐字一致():
    assert _same(clips._ANCHOR_FIELD, check_script._ANCHOR_FIELD), \
        "clips._ANCHOR_FIELD 与 check_script._ANCHOR_FIELD 必须逐字一致"


def test_时长目标字段正则两处逐字一致():
    assert _same(qc.DURATION_FIELD, check_script.DURATION_FIELD), \
        "qc 与 check_script 的 DURATION_FIELD 必须逐字一致（两处口径必须一致）"
    assert _same(qc.DURATION_FIELD_HINT, check_script.DURATION_FIELD_HINT)


def test_片段时长下限两处同源():
    """clips.MIN_CLIP（生成下限）与 align.REFIT_MIN_CLIP（重排下限）是同一条
    「比这更短会闪」纪律在两个阶段的落实——数值必须相等，改一个必须改另一个。"""
    assert clips.MIN_CLIP == align.REFIT_MIN_CLIP
