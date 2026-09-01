# tts：readings 复用比对改段级 speakable（2026-09-02 已修）

## 现象

03 顺听改一个读音，`python -m pipeline.tts` 把全部 9 段都重跑。WORKFLOW 承诺
「改读音只改 config/voice.json 的 readings 再单段重跑，自动重做受影响的段」，
实际从未实现——须贺期修段 2/8/9 的读音时，指纹一变九段全废，只能靠手动改
manifest 指纹保住其余段复用。

## 成因

`_reusable` 把 readings **全表哈希**当全局门：`any(old.get(k) != v)` 为真则所有段
`continue`。readings 影响的是每段实际喂给模型的文本，改一个词只让「念出来不一样」
的段失效，全表指纹把粒度放大到了全局。

全表指纹还有一个漏检面：`json.dumps(sort_keys=True)` 对**键序**不敏感，而
`str.replace` 按插入序生效——只调键序不改词条（如长键顶到短键前面）时，指纹不变、
旧音频被静默复用，念出来的却是旧文本。

## 修法

- `Take` 加 `speakable` 字段：合成时把剥引号+读音替换后的文本落进 manifest。
- `_reusable` 两层门：`engine/model/ref_audio` 仍是全局（变了=真全量重做）；
  readings 移出全局门，段级比对 `take.speakable != speakable(take.text)`。
- 旧 manifest（无 speakable 字段）退回全表指纹，行为与从前一致，不更糟。
- 日志细化：「读音表变了，重做受影响段：2、8、9」/「各段合成文本不变，全部复用」。

## 验证

- `tests/test_tts.py::TestReusable`：受影响段单重做 / 无关读音全复用 /
  键序变异（指纹检不出的）/ 旧 manifest 回退 / 音色变仍全量 / Take 带 speakable。
