# D67：恢复时对历史消息做全 role 脱敏投影 施工 spec

> 对应 issue：**D67**（`docs/dev/issues/README.md`，D65 红队报告副产物）。
> 相关：D52（工具结果脱敏，只覆盖其提交之后写入的日志）、Spec 16（出网断言与可信集）、D65（同为「不动盘、读取时投影」思路）。
> 无 UI 改动。**人裁决（2026-10-10）：方向 ① 采纳，带实测修正（见 §一）；② 一次性迁移脚本（破坏 append-only「日志即事实」）与 ③ 不动（弃疗一个会话）均被否。**

---

## 一、目标与实测依据

**病灶**：D52（2026-10-08 12:24Z，提交 `15d8d61`）之前写入的存量会话日志里，受限字面量未脱敏。这类会话今天**恢复即死**：恢复后每轮请求撞出网断言 → 整轮回滚，客户端没有任何修复入口。

**实证**（D65 红队 + 人 2026-10-10 独立实验，董香二期 `data/episodes/2026-09-21-东京喰种-雾岛董香人物志-二/session.jsonl`）：

- 受限字面量不只在一处：tool 正文（seq=288）**和两条 user 角色的历史规程注入（seq=282、292）**都有；
- 实验结果：不 scrub → 被拦；只 scrub tool 消息 → **仍被拦**；全 role scrub → 通过；
- 机理：可信集只含规程的**当前磁盘正文**（16 份），seq=282/292 那两份规程的当前版本已变，豁免盖不住历史副本的命中。所以「只 scrub tool」是假修复，**必须全 role**。

**机制**：恢复重建消息时，对每条消息的字符串 `content` **全 role** 过 `tools.scrub_restricted`（把受限字面量替换为 `[已脱敏]`）。**不动盘**——append-only「日志即事实」不破，投影只发生在读路径，与 D65 老化同一哲学。

## 二、前置条件

- 数据盘挂载：终验（§八）要恢复真实董香二期会话，需要盘；单测不需要。
- 基线：`uv run pytest` 全绿才开工。

## 三、改动清单

| 文件 | 锚点（引原文，不给行号） | 改动 |
|---|---|---|
| `pipeline/agent/session.py` | `prepare_resume` 里 `"messages": rebuild_messages(loaded),` | 对重建输出逐条消息：若 `content` 是字符串，替换为 `scrub_restricted(content)`（`pipeline/agent/tools.py`，D52 现役函数，不新写规则）；非字符串 content 与其余字段不动。**放这里而非 `session_log.rebuild_messages` 内部**：`rebuild_messages` 的全部生产调用方只有这一处（已 grep 复核），session.py 本来就同时依赖 session_log 与 tools（出网断言 `assert_egress_boundary` 就是函数内 import 先例），而 session_log 是纯日志层、不反向依赖 tools——放 session.py 无循环 import 风险，rebuild_messages 保持纯日志语义。且 `prepare_resume` 被桌面协议（protocol.py）与终端（cli.py）共用，终端 `--continue` 与桌面客户端走同一个投影点，终验 CLI 即等效（人已复核） |

**施工细节（核验轮已定点，照做）**：

1. **对象同一性**：scrub 必须发生在 `prepare_resume` 构造 `state["messages"]` 的那一步，下游不许再复制一道——`_auto_compact` 有 `convo is not self.host.main_messages` 的对象同一性判断，再复制会让同一性断掉、自动压缩静默失效；
2. **只动字符串 content**：重建列表里 compaction 的摘要/卡也是普通 content 字符串，`scrub_restricted` 对干净文本幂等，照过即可；assistant 的 `tool_calls.arguments` 不动（ADR-0026 的双保险设计就是参数不脱敏）。
| `tests/`（按邻近原则选文件） | — | §四 用例 |

**影响面核对**：

- **可信集**：每回合装配时从磁盘当前正文重建（`assembly.py::route_trusted_texts`），与本投影无关——当前版规程里的受限字面量豁免照旧；
- **resume 后 compact**：`compact()` 作用于内存消息（已是投影后的），摘要器不会再吃到脏字面量；
- **D65 老化**：同为读路径投影，正交可组合（老化在发送前、本投影在恢复时），互不依赖；
- **cache**：恢复后首轮的前缀本来就因状态卡重算而变，本投影不引入新的失效；
- **在运行会话**：不受影响——D52 之后写入的消息进历史前已脱敏，本投影对干净文本幂等（§四 用例 3 锁这条）。

## 四、测试要求

期望值先在实现上跑通再写断言；写完做变异检验。

1. **三 role 全覆盖**：构造含受限字面量的 user / assistant / tool 消息各一条的日志，`prepare_resume` 重建后三条的 content 均为 `[已脱敏]`（锁「只 scrub tool 是假修复」的回归）；
2. **日志不动盘**：投影前后 `session.jsonl` 哈希一致；
3. **幂等**：无受限字面量的消息重建后逐字节不变；
4. **真实回放（需数据盘）**：董香二期日志走 `prepare_resume` → 对重建结果过 `assert_egress_boundary`（可信集按生产装配）→ 不抛异常（复现人实验的「全 role scrub → 通过」行）。

变异（至少）：① 只 scrub tool 角色（必须被用例 1 杀）；② scrub 落盘改写（必须被用例 2 杀）；③ 对非字符串 content 报错或乱改（用例 3 杀）。

## 五、明确不做的事（范围闸门）

1. 不改写日志文件（人裁决否了迁移脚本：append-only 不破）；
2. 不动 `assert_egress_boundary` 的规则与受限模式表（Spec 16 不在本 spec 范围）；
3. 不扫描全库找「还有哪些会话中招」——恢复时才投影，没恢复的会话不主动碰（判据 4 同源：不制造不必要的定罪动作；真要盘点，一条只读 grep 就够，属运维动作不进代码）；
4. 不改 D52 的写入侧脱敏（已在岗）。

## 六、验证命令

```bash
uv run pytest                                   # 全量回归（贴结果）
# 终验（需数据盘）：CLI 恢复董香二期会话（与桌面同投影点，CLI 即等效），首轮请求成功发出、无 turn_rollback
```

终验实操点（核验轮已定点）：

- **flock**：恢复会拿该期租约——终验前确认没有桌面会话正开着董香二期，否则先撞上 SessionLocked 而不是断言；
- **真实副作用**：终验会向日志追加真实的 turn 记录并发一次真实 API 请求；不带 D65 的终验首轮是全量重发（该会话上次实测 prompt_tokens ≈ 101k，gpt-4o 128k 窗口内，能过但接近上限）；**若 D65 先落地，同一终验的首轮小一个量级**——建议施工顺序 D65 → D67（D66 无依赖可任意穿插）。

## 七、完成判定（逐项打勾）

- [x] `prepare_resume` 重建输出全 role 过 `scrub_restricted`，落盘零改动
- [x] §四 4 条用例全绿；3 条变异全被杀
- [x] `uv run pytest` 全量绿
- [x] **终验通过（2026-10-10 实测，pi 代跑人在场）**：钥匙串取 `CPA_API_KEY` 注入环境（桌面同款读取路径，密钥不进 argv/日志），CLI 恢复董香二期 `294d0a7ea4b3a71e`（重放 210 条），首轮真实请求成功发出、模型正常作答（正确回忆上次讨论到段落 21 的事实核查）、无 turn_rollback、回合正常结束。前置条件均已满足：无桌面会话持租约；D65 已先落地
- [x] issues 表 D67 行更新施工状态

**施工实录（2026-10-10）**：`session.py::prepare_resume` 在构造 `state["messages"]` 那一步全 role 投影（函数内 import `tools.scrub_restricted`，循 `assert_egress_boundary` 先例；只动字符串 content，干净消息复用原对象保同一性）。测试进 `tests/test_agent_session.py`（4 条，真实回放用例数据盘缺席自动 skip；假租约 append/truncate 挂 AssertionError，对真实日志零写入）。变异 3 条均被对应用例杀死。连带改写：`test_td1c` 两条断言——恢复后历史副本里的受限字面量已被投影为 [已脱敏]（回合不被拦的语义不变，可信集 (b) 仍管装配器当前注入）。全量 2523 绿。董香二期实测：唯一 sid `294d0a7ea4b3a71e`、352 行、plan_repairs=0、无残行；不 scrub 必拦、全 role scrub 过生产口径断言（可信集 16 份）。**终验留给人的前置已具备：D65 已先落地。**
