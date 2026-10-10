# D66：回收站查看与选择性彻底清空 施工 spec

> 对应 issue：**D66**（`docs/dev/issues/README.md`，三项裁决已拍板：恢复不做 / 逐段确认 / 仅人手动）。
> 相关：D45（期会话删除=移回收站）、D58（选题会话同套）、Spec 10（桌面端不读会话记录的静态守卫 TG-14）、N54 / D40（确认框异常回落语义，复用即继承）、N55（读域校验先例）、D57（host 直读被 TG-14 拦下的先例）。
> **本 spec 含 UI，plans/README 的 UI 五条规则生效**：用户场景见 §一，视觉取舍出真实窗口截图给人选（§四），验收第 1 条是真实任务手验、人勾（§九）。
> **2026-10-10 红队复审已过两轮**：一轮 10 条发现全部采纳（发现 1 竞态为阻断级，修复见 §五）；「未能构造出反例」清单见附录 A。二轮 R1（partial 判据漏中间坏行）已修进 §三/§七，R2（反例清单落点）以附录 A 了结。本版为修订稿 v2。

---

## 一、用户场景与目标

**场景**：人在 1280×800 与 1440×900 窗口下做完一期（或聊完几段选题），侧栏展开。他想确认「之前删掉的会话还在不在、占多大地方」，挑几段没用的彻底清掉，释放数据盘空间并确认隐私内容不残留。

**取舍写明（红队发现 7）**：侧栏收起时回收站入口消失——接受。与 D45 会话列表同一限制（`App.tsx` 的 `<nav hidden={!layout.leftOpen}>`），回收站是低频管理动作，不配 D49-A S4 那种收起态入口；若真实使用中这条限制被撞到，再另立项补入口。

**目标**：桌面端能看到当前期（或选题）回收站里每段的会话号、最后时间、条数、文件大小，并逐段彻底删除（不可恢复、逐段确认）。

**机制基础**（2026-10-10 读码核实，红队复核一致）：

- 回收站一段一文件：`<日志目录>/_agent/session-trash/<sid>.jsonl`，同名冲突加 `-<%Y%m%dT%H%M%S%f>` 后缀（`session_log.py::move_session_to_trash`）；sid 字符集 `^[0-9a-f]{16}$` 双端一致（`shared/constants.ts` 与 `cli.py`），时间戳后缀只含数字与 T，正常路径产不出奇异文件名；
- `session_log.list_sessions(raw)` 对任意 JSONL 字节出摘要，不写新解析器（但「解析失败」的判据见 §三——解析器本身永不抛错）；
- 彻底删除 = `unlink` 回收站文件本身；**主日志零接触**——不 open、不创建、不替换主日志（锁选型见 §五.3）。

## 二、人裁决（已定，施工中不得偏离）

| 项 | 裁决 |
|---|---|
| 恢复对话 | **不做**。真误删，人手动把文件从 `session-trash/` 搬回上级目录即可（与 D45 回收站语义一致） |
| 确认形态 | **逐段确认**：复用宿主 `deps.confirm` → 主进程 `dialog.showMessageBox` 原生模态框——与删除会话（`service.ts::convDelete`）、退出确认框（N54）同一落位，文案写明「彻底删除，不可恢复」。**渲染层不新造确认组件**（红队发现 2：渲染层不存在可复用的删除确认框，D45 的确认在宿主层；`ScriptEditor.tsx` 的 `window.confirm` 是另一物，不复用） |
| agent 通路 | **无**。`/list-trash` 与 `/purge-trash` 不进 LLM 工具表、不进白名单，与 `/delete-session` 同级（`cli.py` 原注「不进 LLM 工具表」） |

## 三、core 改动（`pipeline/agent/`）

| 文件 | 锚点（引原文，不给行号） | 改动 |
|---|---|---|
| `pipeline/agent/session_log.py` | `move_session_to_trash`（docstring「回收站文件落盘成功 → 再原子替换日志」） | **共用锁（红队发现 1）**：`move_session_to_trash` 改为持 `_agent/session-trash.lock` 的 fcntl 文件锁，锁覆盖「回收站落盘 + 主日志原子替换」两次写；新建 `purge_trash_file(trash_dir, name)` 持同一把锁跨 `unlink`。**不许用 `EpisodeLease.acquire` 当这把锁**——它 `O_RDWR\|O_CREAT` 会在没有主日志时创建 `session.jsonl`，直接打破「主日志零接触」 |
| `pipeline/agent/cli.py` | `# 不进 LLM 工具表、不进 Spec 11 的八命令表。删除 = 移进 _agent/session-trash/（人选的语义）。` | 同区块加两个桌面端专用命令，插在「多余参数报错」之前（`ava idea /list-sessions` 的精确匹配分支是现成先例，不动 argv 解析层）：<br>① `/list-trash`——只读。枚举 `<日志目录>/_agent/session-trash/*.jsonl`，逐文件出 `{file, sid, last_ts, message_count, bytes}`（`bytes` = `st_size`）。目录不存在或为空输出 `[]`。逐文件状态判据（红队发现 4 + 二轮 R1，解析器对任何字节都不抛错，判据必须写明）：`st_size == 0` → `sid: null, empty: true`；`st_size > 0` 且 `list_sessions` 为空 → `sid: null, parse_error: true`；**`_parse_lines` 的残行计数 > 0 或存在无法解析的完整行（条目里的 None）** → 附加 `partial: true`（R1：中间坏行与末尾残行两种半残形态都要盖住，缺一只盖一半，条数照样静默失真）。实现直接调同模块的 `_parse_lines`，不算新解析器。输出一行 JSON（`/list-sessions` 同形，退出码 0）。<br>② `/purge-trash --file=<回收站文件名>`——持 §五.3 的锁 `unlink`。校验（§五.2）：basename 主校验 + resolve 纵深。退出码：0 成功 / 1 文件不存在（如实报错，不幂等——重复删是 bug 信号）/ 2 参数非法 / 3 锁被占（与 `/delete-session` 的「进行中」同码，宿主复用现有 `E_SESSION_LOCKED` 映射） |

## 四、桌面端改动（`desktop/src/`）

| 文件 | 锚点 | 改动 |
|---|---|---|
| `desktop/src/host/spawner.ts` | `case "LIST_SESSIONS"`（带 `stdoutMax`） | 加 `LIST_TRASH` / `PURGE_TRASH` 模板（期带期目录、选题带 `--idea`）；**`stdoutMax: SESSIONS_STDOUT_MAX_BYTES` 沿用 LIST_SESSIONS 先例（红队发现 3）**——host 解析走 `stdoutFull ?? stdoutTail`，无 stdoutMax 只有 8 KB 尾部（`SPAWN_TAIL_BYTES`），清单超 8 KB 时 JSON 开头被截、`JSON.parse` 炸成 E_CORE「输出不是 JSON」；回收站「只进不出」恰是 D66 立案理由，超 8 KB 是真实形态不是边角 |
| `desktop/src/host/service.ts` | `convDelete`（`this.deps.confirm("把这个会话移到回收站？"…)` 先例） | 加 `conv.trashList` / `conv.purgeTrash`：exact-keys 校验（C10-R1 惯例）；`purgeTrash` 在 **spawn core 之前**调 `deps.confirm`（同 convDelete 落位，红队发现 2）；core 退出码 3 映射 `E_SESSION_LOCKED`（复用 convDelete 现有映射） |
| `desktop/src/shared/protocol.ts` | `conv.*` 消息类型 | 两个新消息类型 |
| `desktop/src/renderer/SessionList.tsx` | 会话列表组件（`key={r.sid}`） | 列表底部加可折叠分组「回收站（N）」：**按文件聚合、React 键用 `file` 不用 `sid`**（红队发现 6：同名冲突时同一 sid 可对应多个回收站文件——手动搬回再删即再造）；同 sid 多文件时行内显示时间戳后缀区分。每行显示 sid 前缀、最后时间、条数（`partial: true` 的加「（记录不全）」、`parse_error`/`empty` 的如实标「无法解析」/「空文件」）、大小（KB/MB 一位小数）、「彻底删除」按钮——只调 `conv.purgeTrash`，确认框在宿主层；删除成功后刷新该分组；空回收站不渲染分组 |

**TG-14 边界提醒（红队发现 10）**：`desktop/src` 任何文件出现 `session.jsonl` 字面量即被静态守卫扫红（纯文本 includes，`desktop/tests/static/scan.ts`）。desktop 侧文案与注释写「主日志」即可，不得出现该字面量；`bytes` 经 core 出数不违规。

**视觉取舍（待人看截图，UI 规则 4）**：回收站分组放侧栏会话列表底部（推荐：与「删除」心智同一位置）vs 会话头加入口——施工时各出一张 1280×800 真实窗口截图给人选，人选前不批量铺样式。

## 五、安全边界（逐条对应红线）

1. **不可逆**：彻底删除无回收站之下的回收站，确认框文案必须写死「不可恢复」；
2. **路径校验（红队发现 5：承重墙标对）**：`unlink(2)` 不跟随符号链接，「软链逃逸删外部文件」对 unlink 本不成立。真正的主校验是 **basename 拒分隔符**：`Path(name).name == name` 且 `name != ".."` 且 `.jsonl` 后缀；resolve 校验是纵深：双侧 resolve 后用 `is_relative_to`（或父目录精确等值，现役先例 `cli.py::_episode_file` 的 `target.parent != resolved_ep`）判定落在 `session-trash/` 内——`data/` 可以是指向外置盘的符号链接（`paths.py::require_data_at`），一侧 resolve 一侧不 resolve 会全拒误伤；
3. **并发（红队发现 1，阻断级）**：`move_session_to_trash` 的顺序是「先回收站落盘、再原子替换主日志」，中间有微秒—毫秒窗口；不持锁的 purge 落在窗口内会 unlink 成功、随后主日志移除该 sid——**该段从两个位置同时消失，双向都返回成功，无任何报错**（静默永失，且证伪裁决①的「手动搬回」前提）。修：`session-trash.lock` 共用 fcntl 锁（`session_log` 现役依赖），move 持锁跨两次写、purge 持锁跨 unlink；锁被占 purge 退出码 3。不接受「窗口极窄」的概率论证——这是 spec 明文写出的安全论证被证伪；
4. **agent 隔离**：两个命令不进工具表、不进 `run_pipeline` 白名单、不出现在任何 scope 文档里；
5. **终端直跑**：core 命令本身做全量校验，不依赖桌面端确认框（确认框是 UX，不是安全层）。

## 六、明确不做的事（范围闸门）

1. 恢复功能（人裁决，理由见 §二）；
2. 批量多选清空、「全部清空」按钮（逐段确认是裁决；真要全清，`rm` 一行的事）；
3. 自动过期清理——**但补触发条件**（红队发现 8：无触发条件的拒绝是无限期搁置，不是观察）：回收站总大小 >1 GB 或 >200 段、或 `/list-trash` 输出超 `SESSIONS_STDOUT_MAX_BYTES` 时，另立自动清理项；
4. 回收站内容查看器（看摘要是为了确认身份后删除；要读全文去数据盘点文件，不造第二个对话区）；
5. 期级以外的回收站（目前没有别的回收站）。

## 七、测试要求

期望值先在实现上跑通再写断言；写完做变异检验。

core（pytest）：

1. `/list-trash`：空目录 → `[]`；两段 → 两条摘要且 bytes 与 `st_size` 一致；零字节文件 → `empty: true`；st_size>0 但无可解析段 → `parse_error: true`；**半残文件两种形态各一条**——末尾残行（好行 + 无换行尾巴）与中间坏行（好行中间夹无法解析的完整行）→ 都正常出摘要且带 `partial: true`（发现 4 + R1 用例）；
2. `/purge-trash`：正常删除；`--file=../../x.jsonl`、`--file=/etc/passwd`、`--file=x.txt`、`--file=..` 全拒（退出码 2）；软链指向回收站外的文件被拒；删不存在文件退出码 1；
3. **锁（红队发现 1 用例）**：另一进程持 `session-trash.lock` 时 purge 退出码 3；move 持锁期间 purge 不插入（用锁直接模拟，不拼概率）；purge 后主 `session.jsonl` 逐字节不变（哈希前后一致）——含「该期原本没有主日志」的回归：任何路径不得创建它；
4. 退出码 0/1/2/3 各一条。

桌面端（vitest + 复用现有 e2e 套间）：

5. 宿主：exact-keys 校验、模板 argv 形状（期 / 选题两叉）、**`stdoutMax` 存在且等于 `SESSIONS_STDOUT_MAX_BYTES`（红队发现 3 用例）**、core 非零如实抛、退出码 3 → `E_SESSION_LOCKED`、purgeTrash 在 spawn 之前调 `deps.confirm`（确认取消则不 spawn）；
6. 渲染：分组渲染与折叠、**同 sid 两文件同时列出且键不冲突（红队发现 6 用例）**、大小格式化、`partial`/`parse_error`/`empty` 标记文案、确认取消后不调 purge；
7. e2e：删除一段会话 → 回收站出现该段 → 彻底删除 → 回收站为空、主日志其余段还在、文件系统上该 `.jsonl` 不存在。

变异（至少）：① purge 不校验 basename；② resolve 比较改字符串 startswith；③ purge 不取锁；④ list 对半残文件不标 `partial`；⑤ 模板漏 `stdoutMax`；⑥ 宿主把确认放到 spawn 之后。每条须被对应用例杀死。

## 八、验证命令

```bash
uv run pytest                                   # core 全量
cd desktop && npx vitest run                    # 宿主 + 渲染
cd desktop && npx playwright test               # e2e（含新增回收站用例）
```

## 九、完成判定（逐项打勾）

- [ ] **验收第 1 条（UI 规则 2，人勾）**：人在真实打包版、1280×800 与 1440×900 下完整做完「查看回收站 → 认出目标段 → 逐段彻底清空」全流程，别扭之处记回本节
- [ ] 视觉取舍两张真实窗口截图给人选过（UI 规则 4）
- [ ] core 两个命令按 §三 实现；共用锁按 §五.3 实现且未用 `EpisodeLease.acquire`；§五.2 校验有对应用例
- [ ] §七 用例全绿，6 条变异全被杀
- [ ] `uv run pytest` / vitest / e2e 全量绿（贴结果）
- [ ] 两个命令未出现在任何 LLM 工具表与白名单（grep 验证）；`desktop/src` 无 `session.jsonl` 字面量（TG-14 静态守卫绿）
- [ ] issues 表 D66 行更新施工状态

## 十、与 D65 / D67 的关系

无关依赖，可并行施工。唯一交集：D65 老化投影上线后，回收站里的旧日志仍是全文（投影不动盘），彻底清空的语义不变。红队角 14 已核实：D65 回放只读主日志、tests 的回收站断言全部用 tmp 自建夹具， purge 不会污染 D65 的验证来源。

---

## 附录 A：红队一轮「未能构造出反例」清单（留档，2026-10-10）

审查覆盖但未找出反例的角度，留档防重查：

1. **奇异文件名**：sid 源自 `secrets.token_hex(8)`，回收站文件名只有 `[0-9a-f]` + 时间戳后缀（纯数字+T），正常路径产不出 NFD/大写/尾随空白；手工 `--file` 传入这类形态被 basename + 后缀两条校验拒。
2. **sid 字符集双端一致**：`shared/constants.ts` 与 `cli.py` 同为 `^[0-9a-f]{16}$`，无分叉。
3. **回收站「落盘即稳定」无既有依赖**：全仓 grep，`session-trash` 的生产侧引用只有 `move_session_to_trash` 自身，其余全是测试夹具；新读者只有本 spec 的 `/list-trash`。
4. **stdout 形状**：`/list-sessions` 确为一行 JSON（cli.py），spec 描述吻合；purge 退出码体系 0/1/2/3 有同族 docstring 先例。
5. **正常文件不误判 parse_error**：合法回收站文件由 `split_by_sid` 完整行写出且 move 先截残行。
6. **bytes 字段不违 TG-14**：守卫只扫 `session.jsonl` 字面量；`st_size` 经 core 出数、host 不读记录，合规。
7. **argv 解析层不动**：`ava idea /list-sessions`、`/delete-session` 的精确匹配分支是现成先例。
8. **与 D65 无交叉污染**：D65 回放只读主日志；tests 的回收站断言全部用 tmp 自建夹具。
9. **「同名复活」竞态构造不出**：需同一 sid 被删第二次，而 sid 随机生成且删除后该段已不在会话列表。
10. **并发死锁面（二轮补验）**：move 是「先期租约、后 trash 锁」，purge 只取 trash 锁，全仓无反向取锁顺序，无 AB-BA 面；锁文件落在 `_agent/` 而非 `session-trash/` 内，不会被 `*.jsonl` 枚举、也不会被 purge 的后缀校验误纳。

跳过未查（留档）：桌面端 e2e 对 mainConfirm 排队细节（D40/N54 已覆盖，purge 复用即继承）；`resolve_episode_dir` 对 purge 的 PermissionError 分支（只读路径误伤面小）。
