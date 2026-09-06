"""dump_cn：从双语 ass 提取 CN 样式对白行（笔记加厚取证用）。

2026-09-05 随 tools/ 清理收编进 pipeline/。风险点是样式过滤与标签剥除——
收错样式会把日文行混进取证材料，标签不剥会污染台词原文。
"""

from pipeline import dump_cn

ASS = """[Script Info]
Title: x

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
Dialogue: 0,0:00:02.00,0:00:04.00,CN,,0,0,0,,第二句
Dialogue: 0,0:00:01.00,0:00:02.00,CN,,0,0,0,,{\\pos(320,240)}第一句\\N换行
Dialogue: 0,0:00:03.00,0:00:05.00,JP,,0,0,0,,日文行は含めない
Comment: 0,0:00:01.00,0:00:02.00,CN,,0,0,0,,注释行不算
Dialogue: 0,0:00:06.00,0:00:07.00,Sign,,0,0,0,,标题卡样式不收
"""


def _mk(tmp_path):
    p = tmp_path / "x.Chs&Jap.ass"
    p.write_text(ASS, encoding="utf-8")
    return str(p)


class TestDumpCn:
    def test_只收CN样式_剥标签_按时间排序(self, tmp_path, capsys):
        dump_cn.main(_mk(tmp_path))
        lines = capsys.readouterr().out.strip().split("\n")
        assert [ln.split("\t")[0] for ln in lines] == ["0:00:01.00", "0:00:02.00"]
        assert all("\tCN\t" in ln for ln in lines)      # JP/Sign/Comment 都不收
        assert "pos" not in lines[0] and "\\N" not in lines[0]  # 标签剥除

    def test_jp开关连JP一起收(self, tmp_path, capsys):
        dump_cn.main(_mk(tmp_path), True)
        out = capsys.readouterr().out
        assert "\tJP\t" in out
