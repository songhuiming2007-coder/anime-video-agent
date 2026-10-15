"""D99 实测③：正确归属后的分级统计 + 截断位置。用法同 match.py。"""
import json, re, sys, unicodedata, pathlib
facts, pages = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2])
index = json.loads((pages / "index.json").read_text(encoding="utf-8"))
CAP = 30000
QUOTES = str.maketrans({c: "「" for c in "『“\"‘'"} | {c: "」" for c in "』”’"})
def md(s):
    s = re.sub(r"!?\[([^\]]*)\]\([^)]*\)", r"\1", s)
    return re.sub(r"(\*\*|__|\*|`|~~)", "", s).replace("\\", "")
def letters(s): return "".join(c for c in s if unicodedata.category(c)[0] in "LNM")
def ws0(s): return re.sub(r"\s+", "", s)
nf = lambda s: unicodedata.normalize("NFKC", s)
LEVELS = [("L0", lambda s: s), ("L1", lambda s: re.sub(r"\s+", " ", s).strip()),
          ("L2", ws0), ("L3", lambda s: ws0(nf(s))), ("L4", lambda s: ws0(md(nf(s)))),
          ("L4q", lambda s: ws0(md(nf(s))).translate(QUOTES)), ("L5", lambda s: letters(nf(md(s))))]
text = facts.read_text(encoding="utf-8")
exs = [m.group(1).strip() for m in re.finditer(r"^\s*> (.+)$", text, re.M)][6:]   # 前 6 条是文件头说明
def load(h, k):
    p = pages / f"{h}.{k}.txt"; return p.read_text(encoding="utf-8") if p.exists() else None
stats = {k: {} for k in ("fetch", "crawl")}; over = []
for ex in exs:
    for kind in ("fetch", "crawl"):
        hit = None
        for name, f in LEVELS:
            for h, rec in index.items():
                t = load(h, kind)
                if t is None: continue
                i = f(t).find(f(ex))
                if i >= 0:
                    # 原文位置近似：归一化视图里的比例 × 原文长度
                    pos = int(i / max(1, len(f(t))) * len(t))
                    hit = (name, rec["url"], pos, len(t)); break
            if hit: break
        lv = hit[0] if hit else "✗"
        stats[kind][lv] = stats[kind].get(lv, 0) + 1
        if hit and hit[2] + len(ex) > CAP:
            over.append((kind, hit[1][-45:], hit[2], hit[3], ex[:30]))
print("摘录条数", len(exs)); print(json.dumps(stats, ensure_ascii=False))
print("超出 30000 字截断上限才出现的摘录：")
for o in over: print("  ", o)
