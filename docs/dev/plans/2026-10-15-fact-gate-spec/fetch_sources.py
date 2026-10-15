"""D99 实测①：解析事实文件里的出处 URL，用 AVA 自己的 fetch_web 与 crawl_page 各抓一次，原样存档。
用法：uv run python fetch_sources.py <facts.md> <out_dir>   （在仓库根运行）"""
import dataclasses, json, re, sys, hashlib, pathlib, traceback
from pipeline.agent import web, web_crawl

facts, out = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2])
out.mkdir(parents=True, exist_ok=True)
urls = sorted(set(re.findall(r"https?://[^\s）)]+", facts.read_text(encoding="utf-8"))))
cfg = web.load_web_config()
big = dataclasses.replace(cfg, max_fetch_chars=10**7)   # 另抓一份不截断的，分清「截断」与「归一化」两种失配
sec = web_crawl.load_crawl_section()
big_sec = dataclasses.replace(sec, max_chars=10**7) if sec else None
index = {}
for u in urls:
    h = hashlib.sha1(u.encode()).hexdigest()[:10]
    rec = {"url": u}
    for kind, fn in (("fetch", lambda: web.fetch_web(u, config=big)),
                     ("crawl", lambda: web_crawl.crawl_page(u, "D99 实测", section=big_sec))):
        try:
            r = fn()
            text = r.get("text") if kind == "fetch" else r.get("markdown") or r.get("text") or ""
            (out / f"{h}.{kind}.txt").write_text(text, encoding="utf-8")
            rec[kind] = {"ok": True, "chars": len(text), "status": r.get("status")}
        except Exception as e:
            rec[kind] = {"ok": False, "error": f"{type(e).__name__}: {str(e)[:300]}"}
        print(u, kind, rec[kind], flush=True)
    index[h] = rec
(out / "index.json").write_text(json.dumps(index, ensure_ascii=False, indent=1), encoding="utf-8")
