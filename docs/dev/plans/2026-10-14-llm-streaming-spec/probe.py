"""D94 探针：同一请求分别非流式 / 流式发给本机代理，原样存下 SSE 行与 JSON。用法（仓库根）：CPA_API_KEY=… uv run python -I <本文件> <输出目录>"""
import json, sys, time, pathlib, urllib.request
from pipeline.agent.llm import load_llm_config
out = pathlib.Path(sys.argv[1]); out.mkdir(parents=True, exist_ok=True)
cfg = load_llm_config()
tools = [{"type": "function", "function": {"name": "get_weather", "description": "查城市天气",
          "parameters": {"type": "object", "properties": {"city": {"type": "string"}}, "required": ["city"]}}}]
cases = {
  "tools2": {"messages": [{"role": "user", "content": "同时查一下杭州和太原的天气，两个城市各调用一次工具。"}], "tools": tools},
  "text": {"messages": [{"role": "user", "content": "用三句话介绍一下杭州西湖。"}]},
}
for name, body in cases.items():
    for effort in (None, "high"):
        for stream in (False, True):
            p = {"model": cfg.model_for(None), **body}
            if effort: p["reasoning_effort"] = effort
            if stream: p.update(stream=True, stream_options={"include_usage": True})
            req = urllib.request.Request(cfg.endpoint, data=json.dumps(p, ensure_ascii=False).encode(),
                  headers={"Content-Type": "application/json", "Authorization": f"Bearer {cfg.api_key}"}, method="POST")
            t0 = time.time()
            with urllib.request.urlopen(req, timeout=300) as r:
                ct = r.headers.get("Content-Type"); raw = r.read().decode("utf-8")
            tag = f"{name}-{effort or 'default'}-{'stream' if stream else 'json'}"
            (out / f"{tag}.txt").write_text(raw, encoding="utf-8")
            print(tag, ct, f"{time.time()-t0:.1f}s", len(raw), flush=True)
print("model", cfg.model_for(None), "endpoint host", cfg.endpoint.split('/')[2])
