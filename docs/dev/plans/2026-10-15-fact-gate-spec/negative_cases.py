import json, re, sys, unicodedata, pathlib
pages = pathlib.Path(sys.argv[1]); index = json.loads((pages/"index.json").read_text(encoding="utf-8"))
h_of = {v["url"]: k for k, v in index.items()}
def md(s): return re.sub(r"(\*\*|__|\*|`|~~)", "", re.sub(r"!?\[([^\]]*)\]\([^)]*\)", r"\1", s)).replace("\\", "")
def L5(s): return "".join(c for c in unicodedata.normalize("NFKC", md(s)) if unicodedata.category(c)[0] in "LNM")
cases = [
 ("note19 编造", "https://note.com/ryodayospcl/n/ne0daf286b0bf", "放送部だった自分はドラクエの曲を流した"),
 ("note19 编造", "https://note.com/ryodayospcl/n/ne0daf286b0bf", "山田先生が血相を変えて"),
 ("note19 编造", "https://note.com/ryodayospcl/n/ne0daf286b0bf", "誰だ！ こんなふざけた曲を流したのは！"),
 ("ASCII 问答拼接", "https://ascii.jp/elem/000/000/449/449394/4/", "元々あの絵には設定はないんです"),
 ("ORICON 未核实", "https://web.archive.org/web/20120224151117/http://rn.oricon.co.jp/special/20080805_02.html", "機械に歌わせるからこそ、ともすれば恥ずかしい歌詞を躊躇なしに書けるのだと思います"),
 ("改写动机(示意)", "https://originalnews.nico/20667/2", "集英社の編集部に止められたので、別名義で描き始めたんですよ。"),
 ("截短真句", "https://note.com/ryodayospcl/n/ne0daf286b0bf", "全校生徒の前で"),
]
for tag, url, ex in cases:
    for k in ("fetch", "crawl"):
        p = pages / f"{h_of[url]}.{k}.txt"
        t = p.read_text(encoding="utf-8") if p.exists() else ""
        print(f"{tag:12} {k:5} {'命中' if L5(ex) in L5(t) else '未命中'}  {ex[:30]}")
