# D127 施工图：SP 集键放宽到 2–3 位

日期：2026-10-20（v0.1）；**人 10-20 裁决：放宽到 2–3 位，先出施工图**；待人看过施工图说开工
关联：issues D127、D126、D129、ADR-0010 决策二

## 一、要解决什么

企划池的集键写死两位（`SP01`–`SP99`）。supercell 池登记到 SP99 就满了，卷二补丁池还有 6 条迁不进来（D129 定了企划卷补料一律进企划池）。复用撤下的 13 个空号不采用：老稿、日志、台账里的旧编号会悄悄指向新素材。

一个企划池 99 件不是极端情况：supercell 两卷就用满了，后面还有卷。

## 二、现状（2026-10-20 读码）

**生成侧不用改**：`f"SP{n:02d}"` 遇到 100 原样出 `SP100`（`shots._key`、`clips`、`align`、`review`、`crosscheck`、`ingest`、`acquire`、`ingest_patch` 共 12 处）。

**解析侧 17 处写死 1–2 位，分三类：**

| 类 | 位置 | 现写法 | 不改的后果 |
|---|---|---|---|
| 登记入口 | `acquire.register_key`（`--as`） | `SP(\d{2})` | 本次撞上的报错 |
| | `acquire._SP_PREFIX`、`acquire.next_key`（含「到 99 就报错」） | `^SP(\d{2})(?!\d)`、`SP(\d{2})` | 自动取号停在 99 |
| | `ingest_patch` 补丁池上限 | `next_number > 99` | 补丁池同样封顶 |
| 索引与云端 | `vindex._parse_key` | `SP(\d{2})` | SP100 的镜头表 / 打标加载不了 |
| | `cloud._LIB_KEY` | `SP\d{2}` | `lib-push / lib-captions / lib-pull` 拒收 |
| | `calibration._NAME_SP` | `/SP\d{2}` | 不能给单集标阈值 |
| 稿件与排片 | `clips._ANCHOR`（约 70 行）、`clips` 第 221 行 | `SP(?P<sp>\d{1,2})`、`SP(\d{1,2})` | clips 当场报「锚点写的是…机器认不了」（2026-10-20 实测，响亮失败，不是静默） |
| | `check_script` 第 354、366 行、`_REUSE_ITEM` | 同上 | 机检认不出 SP100 锚点、复用声明 |
| | `render` 第 1002、1012 行 | `SP\d{1,2}`、`SP(\d{1,2})` | avsync 试看段取不到片源 |

已经能吃三位的：`clips` 第 612 行（`SP\d+`）、`check_script` 第 1070 行（`SP\d`）、`ingest_patch` 的 `key[2:].isdigit()` 几处。

**排序**：`vindex.caption_keys`、`ingest_patch` 第 494 行按字符串排序，`SP100` 会落在 `SP10` 与 `SP11` 之间。查过用途：都是列清单与迭代，不决定索引行号以外的东西；向量索引重建时行序跟着变，本来就整体重建，不影响检索结果。仍改成按数字排，免得人看列表时以为漏号。

## 三、改法

1. **一处定义**：`pipeline/paths.py`（或新建无依赖的 `pipeline/keys.py`）放
   - `SP_DIGITS = r"\d{2,3}"`；解析锚点、复用声明这类人写的地方保留现在对 1 位的容忍：`SP_DIGITS_LOOSE = r"\d{1,3}"`
   - `sp_key(n: int) -> str`（两位补零、三位原样、>999 抛错）
   - `key_sort(key: str)`：`SxxEyy` 与 `SPn` 按数字排
   
   17 处正则全部引用这两个常量，不再各写一份——`vindex.key_of` 的注释早就预言过「某天 SP 改成三位数，另一处静默按旧格式找文件」。
2. **上限**：`next_key` 与补丁池上限改到 999，报错文案同步。
3. **前缀判定**：`_SP_PREFIX` 用 `(?!\d)` 防 `SP10` 吃掉 `SP100` 的前缀；文件名 `SP100-xxx.mp4` 与 `SP10-xxx.mp4` 必须各认各的。
4. **ADR-0010 决策二**补一段修订：两位起、满 99 进三位，理由与日期；不改两位补零。
5. **文档**：`skills/acquire-assets/SKILL.md`、`skills/write-script/SHOTLIST.md` 里写「两位」的地方同步（按 D101 交人过目）。

## 四、不做

- 不把已有的 `SP01`–`SP99` 改写成三位：老稿、台账、镜头表文件名全都要动，没有收益。
- 不改番剧集键 `SxxEyy`。
- 不加四位：999 件的池离现实很远，到时再议。

## 五、测试

- `sp_key` / `key_sort` 纯函数：`1→SP01`、`99→SP99`、`100→SP100`、`1000` 抛错；`[SP100, SP11, SP10, SP9]` 排成 `SP9, SP10, SP11, SP100`。
- 每一处解析点一条用例，覆盖 `SP100`：登记 `--as SP100`、`next_key` 从 99 取到 100、`_parse_key("SP100")`、`lib-push 池 SP100`、`calibration set 池/SP100`、锚点行 `画面: supercell SP100 00:12-00:18` 能排、`复用: supercell SP100（…）` 能认、avsync 能取片源。
- 前缀：`raw/` 里同时有 `SP10-a.mp4`、`SP100-b.mp4`，`next_key` 得 101。
- 端到端：临时池登记到 SP100，切镜头 → 锚点稿 → `check_script` PASS → `clips` 排出该段。
- 静态守卫：`tests/` 里加一条 grep，`pipeline/` 下不许再出现字面量 `SP(\d{2})` / `SP\d{1,2}` 一类写法（全部走常量）。
- 变异：把常量改回 `\d{2}`，上面的用例必须变红。

## 六、完成判定

- 上述用例全绿、全量 pytest 绿、变异被杀。
- 用真实 supercell 池副本把卷二剩下 6 条登记成 SP100–SP105，切镜头、打标（云端）、锚点排片各走一遍。
- ADR-0010 修订段落人过目。

## 七、施工记录

（未施工）
