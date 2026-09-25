# Implementation Spec：上下文装配器与 AGENTS.md 瘦身（Spec 1 / ADR-0022）

日期：2026-09-22（**v0.4**，红队三轮收口 + B1/B2 定向复审通过；状态：**可动工**）  
上位文档：`docs/dev/plans/2026-09-22-harness-evolution-direction.md`（§2 Spec 1），`docs/dev/adr/0022-context-assembly-and-agents-md-slimming.md`  

---

## 0. 一句话设计

**常驻层字节级恒定，工序层精准增量注入。**  
通过纯函数装配器 `pipeline/agent/assembly.py` 读取配置化路由表 `config/agent/assembly.json`，依据 `pipeline.status` 推导的当前工序动态拼装系统提示（常驻层 + 当前工序层）。对 `AGENTS.md` 执行零语义变更的模块化瘦身（搬迁细则至对应 runbook 与 STANDARD.md，保留十三条判据与核心硬约束），将常驻规则压降至 **≤140 行**。  
**v0.3 修正**：工序层注入从 assistant 消息改为 **user 消息**（红队 B1 证伪 assistant 连续注入的 API 兼容性）；首轮 `messages[0]` 仅含常驻层 + 动态层，工序层首轮也以独立 user 消息注入（红队 M1 修复「首轮固化」矛盾）；`04-clips.md` 增加 ≤80 行硬门禁测试（红队 M2 修复「临时放宽无兜底」）。

---

## 1. 红队裁决与修订纪要

### 1.1 第三轮红队裁决与修订纪要（v0.3 → v0.4，2🔴 + 4🟡 + 6🔵 全收；原裁决「🔴 驳回重大修订」，修订后待 B1/B2 定向复审）

| 编号 | 红队指控 | 裁决 | 修订动作 |
|---|---|---|---|
| 🔴 B1 | 常驻层配置只接线 director + AGENTS.md 两源，scope 边界段按配置施工即从系统提示静默消失；且与现有 `assemble_system_prompt`（cli.py:513-539）无替换声明 | **采纳** | §3.4 resident 块增加 `"scope": "config/agent/scopes/{scope}.md"` 模板键；§7 PR2 写死「删除/收编 `assemble_system_prompt`，messages[0] 刷新复用 cli.py:667-673 现有整段重算机制」，禁止双轨并存 |
| 🔴 B2 | 04-clips.md 自估 ~84 行突破自家 ≤80 硬门禁，「预估超限 + 状态接受」组合使 PR0 交付即红，M2 兜底被算术否证 | **采纳** | 采用方案 (a)：SP 迁入增量硬上限改为 **≤11 行**（69+11=80），§4.2/§4.4 清册标注压缩要求；≤80 定为永久硬门禁（非临时放宽），§6.4 测试措辞同步 |
| 🟡 M1 | §2.1 三层定义表仍写「assistant 消息」注入——v0.3 头号修订未同步到规范表 | **采纳** | 表内改为 user 消息并加「（v0.3 改）」标注 |
| 🟡 M2 | 十三条判据断言机理部分空置——非锚定子串 `"1."`/`"2."`/`"3."` 命中硬约束节（AGENTS.md:46-48）编号列表，删判据 1/2/3 永远假绿 | **采纳** | §6.4 断言改为行首锚定正则 + 判据节区限定；§6.5 变异 3 补「删判据第 1 条」用例 |
| 🟡 M3 | 依赖隔离测试进程内快照法对预载重依赖假阴性（numpy ∈ before → 差集为空），与同仓库 Spec 2 §5.2 独立子进程先例纪律不一致 | **采纳** | §6.1 改为 `subprocess.run([sys.executable, "-c", probe])` 独立解释器探针，照搬 Spec 2 §5.2 先例 |
| 🟡 M4 | tracker 生命周期未写死 + 后续轮 status_card 刷新伪码含糊（「否则追加」分支可致卡片无限堆积），轮内懒创建可致去重静默失效 | **采纳** | §5.2/§5.3 写死「tracker 由 run_agent_loop 创建一次，会话级单例，与 messages 同寿命，严禁轮内懒创建」；后续轮刷新改为复用 cli.py:667-673 整段重算替换 messages[0]，删除「否则追加」分支 |
| 🔵 m1 | 循环软链异常类型事实错误（实测抛 OSError 族，非 RuntimeError） | **采纳** | §3.5 三层防御清单改为 OSError 族（附实测备注） |
| 🔵 m2 | `_base` 中 `"04"` 路由为死配置（STEP_KEY_MAP 无键映射到 "04"，status.py 无独立 04 工序） | **采纳** | §3.4 删除 `"04"` 路由并注明理由（03.5 复合工序已覆盖 04-clips.md 注入） |
| 🔵 m3 | §5.2 类型断裂：`resolve_step_docs` 返回 list[Path]，伪码却取 `.rel_path` | **采纳** | §5.2 伪码补 `load_injected_doc` 转换环节 |
| 🔵 m4 | §4.3 数字自相矛盾两处（14→20 名为压缩实为膨胀；「预留 14 行缓冲」与「留 4 行余量」对不上） | **采纳** | 环境与验证块压缩目标改为 14 行（保留全文），总计改 130 行，缓冲/余量统一为 10 行 |
| 🔵 m5 | Anthropic 兼容性声称失实（原生 API 要求 user/assistant 严格交替，连续 user 会 400） | **采纳** | §2.2 论据收紧为「OpenAI 兼容系」，附 Anthropic 接入时的客户端层适配注记 |
| 🔵 m6 | 杂项：`token_estimate` 无估算口径；§6.4 片段 REPO_ROOT 未定义；PR2 端到端门禁过重 | **采纳** | §3.2 补估算口径注释（`len(content) // 4` 经验值）；§6.4 片段自包含化；PR2 端到端验证降级为可选冒烟 |

> **定向复审收口记录（v0.4 补丁，B1/B2 复审通过，总裁决「🟢 可动工」）**：
> - 🔴 N1（修订引入的新缺陷，阻塞放行）：§6.4 节区定位正则 `判据[^\n]*\n(...)` 非标题锚定，抢先命中 AGENTS.md:3 头部段落的「判据」一词，干净文件上 13 条判据全部漏报假红（已实测复现：命中 0/13）。按复审成品机械替换为标题锚定版 `^#+ [^\n]*判据[^\n]*\n(.*?)(?=\n#+ |\Z)`（re.S | re.M），实测 13/13 全命中，删判据 1/7 双变异均按预期变红；docstring 补「行首数字编号子列表」已知上限；
> - B1/B2 修订经逐行复核无残留，M1/M3/M4 与 m1–m6 抽查全部属实。

### 1.2 第一、二轮红队裁决与修订纪要（v0.1 → v0.3）

| 编号 | 红队指控 | 裁决 | 修订动作 |
|---|---|---|---|
| 🔴 B1 | assistant 消息注入与 `run_tool_loop` 不兼容，连续 assistant 消息 API 行为未定义 | **采纳** | 工序层注入改为 **user 消息**，彻底规避连续 assistant 问题 |
| 🔴 B2 | `normalize_step_key` 前缀匹配无法覆盖 12 种真实 `current_step` | **采纳** | 改为显式查表 `STEP_KEY_MAP`，12 种状态全枚举；`03.5` 复合状态同时注入 `03.5` + `04` 两篇文档 |
| 🔴 B3 | AGENTS.md 瘦身 150 行预算不可达，保留区块实测 172 行 | **部分采纳** | 保留区块精确核算后压缩至 **136 行**（详见 §3.3）；`04-clips.md` 超限风险由拆分方案解决（详见 §3.2） |
| 🟡 M1 | 首轮 `messages[0]` 包含工序层文档，与「常驻层恒定」矛盾 | **采纳** | 首轮 `messages[0]` 仅含常驻层 + 动态层，工序层首轮也以独立 user 消息注入 |
| 🟡 M2 | `04-clips.md` 「临时放宽至 ≤80 行」缺乏门禁兜底 | **采纳** | 新增 `test_runbook_04_clips_line_budget` 硬门禁 |
| 🟡 M3 | `step_key_of` 的 `None` 输入与 idea scope 语义不匹配 | **采纳** | `idea` scope 路由改为空清单，不注入工序文档 |
| 🟡 M4 | `SessionContextTracker` 接口与 `_dispatch_agent_turn` 现有调用时序不兼容 | **采纳** | 补充 `_dispatch_agent_turn` 新签名，明确 tracker 注入点 |
| 🔵 m1 | `assembly.json` 中 `creative`/`pipeline` 路由高度重复 | **采纳** | 引入 `_base` 继承机制 |
| 🔵 m2 | `normalize_step_key` 命名误导 | **采纳** | 改名为 `step_key_of` |
| 🔵 m3 | Spec 版本号格式与 `test_docs_invariants.py` 正则可能不兼容 | **驳回** | 当前测试仅检查 `2026-09-18-ava-agent-impl-spec.md`，不覆盖本 Spec；格式保持现状 |
| 🔵 m4 | `warn_once` 路径格式不统一导致去重失效 | **采纳** | 统一为 `Path(path).as_posix()` 规范化 |

---

## 2. 系统提示三层结构与注入协议（对应 ADR-0022 §1–3）

### 2.1 三层定义

| 层级 | 内容 | 注入位置 | 生命周期 |
|---|---|---|---|
| **常驻层 (Resident)** | Director 人格 + Scope 边界 + 瘦身版 AGENTS.md（定位、硬约束、十三条判据、失败处理、工程约定要点、九步表、环境验证） | `messages[0]` 的 system prompt | **会话初始化一次写入，字节级恒定，绝不修改** |
| **工序层 (Step)** | 当前工序的 runbook 文档（如 02 写稿时注入 `02-script.md` + `write-script/SKILL.md`） | 以 **user 消息** 追加到 `messages` 历史（v0.3 改，红队 B1） | 工序切换时增量追加，已注入文档去重 |
| **动态层 (Dynamic)** | `status_card`（当前期号、产物清单、停机点、推荐命令） | `messages[0]` 尾部 | 每轮刷新，**不触碰常驻层前缀** |

### 2.2 工序层注入消息格式

当 `inspect_episode()` 检测到工序切换（如从 02 进入 03），装配器生成以下 **user 消息** 追加到历史：

```markdown
[系统提示更新] 当前工序已进入 03 语音合成。

请遵循以下规程：

---

# Runbook: 03 语音合成

...（docs/runbook/03-tts.md 全文）...

---

**注意**：以上规程仅适用于当前工序。若后续工序切换，将追加新的上下文更新。
```

**设计依据**：
- **user 消息是 OpenAI 兼容系 API 兼容性最好的注入方式**（ava 客户端 `llm.py` 走 chat/completions 纯 OpenAI 线格式，user 消息可出现在历史任意位置，无连续 assistant 的未定义行为）。注：Anthropic 原生 API 要求 user/assistant 严格交替，连续 user 序列会 400——ava 当前无 Anthropic 直连接入，若未来接入须由客户端层做消息合并适配（红队三轮 m5）；
- 追加消息不改变 `messages[0]` 的哈希值，Prompt Cache 前缀保持有效；
- 已注入文档路径记录到 `SessionContextTracker.injected_paths`，重复切换不重复注入；
- 首轮工序层也以独立 user 消息注入，**不拼入 `messages[0]`**（修复红队 M1 的「首轮固化」矛盾）。

---

## 3. 装配器模块：`pipeline/agent/assembly.py`

### 3.1 依赖白名单（模块级）

```python
from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

from pipeline import paths
```

严禁顶层 import `numpy`、`torch`、`mlx_whisper`、`moviepy`、`transformers`。

### 3.2 数据模型

```python
@dataclass(frozen=True)
class InjectedDoc:
    rel_path: str
    abs_path: Path
    content: str
    token_estimate: int  # 估算口径：len(content) // 4（经验值，仅作预算粗估，不作精确计费）


@dataclass(frozen=True)
class AssembledResident:
    scope: str
    content: str  # director.md + scope.md + AGENTS.md 瘦身版
    token_estimate: int
```

### 3.3 工序键映射（12 种全枚举）

`pipeline/status.py` 的 `current_step` 真实输出空间（红队 B2 已枚举验证）：

```python
STEP_KEY_MAP: dict[str, str] = {
    "01 选题": "01",
    "02 脚本写作": "02",
    "02 脚本写作（草稿待定稿）": "02",
    "02.5 人审改稿": "02.5",
    "03 语音合成": "03",
    "03.5 配音顺听 / 04 排片": "03.5",
    "05 审时间码": "05",
    "06 本地渲染": "06",
    "07 自动质检": "07",
    "07 自动质检（未通过）": "07",
    "08 封面与标题候选": "08",
    "09 人工发布": "09",
}


def step_key_of(current_step: str | None) -> str:
    """current_step 字符串 → 路由键。查表优先，未命中回退 'default'。"""
    if current_step is None:
        return "default"
    for prefix, key in sorted(STEP_KEY_MAP.items(), key=lambda x: -len(x[0])):
        if current_step.startswith(prefix):
            return key
    return "default"
```

### 3.4 路由配置：`config/agent/assembly.json`

```json
{
  "version": "1.0",
  "_note": "工序层上下文装配路由表（ADR-0022）。机制进代码，路由进配置。",
  "resident": {
    "director": "config/agent/scopes/director.md",
    "scope": "config/agent/scopes/{scope}.md",
    "agents": "AGENTS.md"
  },
  "routes": {
    "_base": {
      "default": ["docs/WORKFLOW.md"],
      "01": ["docs/runbook/01-topic.md"],
      "02": ["docs/runbook/02-script.md"],
      "02.5": ["docs/runbook/02.5-human-review.md"],
      "03": ["docs/runbook/03-tts.md"],
      "03.5": ["docs/runbook/03.5-voice-check.md", "docs/runbook/04-clips.md"],
      "05": ["docs/runbook/05-timecode.md"],
      "06": ["docs/runbook/06-render.md"],
      "07": ["docs/runbook/07-qc.md"],
      "08": ["docs/runbook/08-cover-title.md"],
      "09": ["docs/runbook/09-publish.md"]
    },
    "creative": {
      "_extends": "_base",
      "02": ["docs/runbook/02-script.md", "skills/write-script/SKILL.md"]
    },
    "pipeline": {
      "_extends": "_base"
    },
    "idea": {
      "default": []
    }
  }
}
```

**resident 块说明**：`scope` 键为模板路径，`{scope}` 由装配器以当前 scope 名替换（与 `scopes.py:load_scope` 的 `scopes/<scope>.md` 读取约定同源），确保常驻层三源（director + scope 边界 + AGENTS.md 瘦身版）全部进配置，scope 边界段不再静默消失（红队三轮 B1）。

**复合工序处理**：`03.5` 同时注入 `03.5-voice-check.md`（顺听规程）和 `04-clips.md`（排片预备），因该状态是「顺听完成即启动排片」的过渡点。`status.py` 无独立 "04" 工序（12 种 `current_step` 已枚举验证），`_base` 不配置 `"04"` 死路由（红队三轮 m2）。

**idea scope 特判**：`idea` scope 的 `default` 路由为空清单，不注入任何工序文档。选题讨论是开放式对话，无需 SOP 约束；agent 应自主引导用户完成选题，而非机械执行 `01-topic.md` 的填写流程。

### 3.5 核心函数

```python
def resolve_step_docs(
    scope: str,
    step_key: str,
    config_path: Path | None = None,
    root: Path | None = None,
) -> list[Path]:
    """解析 scope + step_key 到文档路径清单。

    支持 `_extends` 继承：先取 _base，再叠加 scope 特有键。
    配置损坏（JSON 解析失败 / routes 键缺失）时打印警告并返回空清单。
    """


def load_injected_doc(
    rel_path: str,
    root: Path | None = None,
) -> InjectedDoc | None:
    """读取文档。三层防御：
    1. 循环软链 → OSError 族捕获（实测 macOS/Python 3.12 自指软链 read_text 抛 OSError 子类，非 RuntimeError），返回 None；
    2. 文件不存在 → FileNotFoundError 捕获，返回 None；
    3. 编码异常 → errors='replace' 降级，打印警告。
    """


def assemble_resident_prompt(
    scope: str,
    config_path: Path | None = None,
    root: Path | None = None,
) -> AssembledResident:
    """组装常驻层：director.md + scope.md + AGENTS.md 瘦身版。

    会话内字节级恒定，任何修改都会破坏 Prompt Cache。
    """


def render_step_injection(docs: list[InjectedDoc]) -> str:
    """将工序文档清单渲染为 user 消息文本。"""
```

---

## 4. AGENTS.md 瘦身与迁移清册

### 4.1 瘦身原则

- **保留白名单**（必须常驻）：定位、硬约束（Code Freeze、人时预算、禁止无限打磨）、十三条判据、失败处理原则、工程约定核心（产物即状态、纯文本进 git、路径相对、禁止自动建 data/）、九步表与停机点定义、环境验证核心命令（uv、numpy 上界、写测试纪律）；
- **迁移黑名单**（必须搬出）：清理约定细则、内容参数表、BGM 约定细则、版权与发布细则、SP 特典协议细则、四阶段工序卡操作细节、截取守卫参数表。

### 4.2 迁移明细与去向 SSOT 映射表

| 现有节次 | 现有标题 | 原文行号 | 原文行数 | 搬迁目标 | 目标章节 | 处理要求 |
|---|---|---|---|---|---|---|
| 七（细则） | 截取守卫与质检门禁参数表 | 136–146 | 11 行 | `docs/runbook/06-render.md` + `07-qc.md` | 「截取守卫与渲染门禁参数」 | 原文完整搬迁，两文档各保留相关部分 |
| 八 | 清理约定 | 147–168 | 22 行 | `docs/dev/STANDARD.md` | 第七节「数据与存储标准」追加 | 逐字迁移，表格原样 |
| 九 | 内容参数表 | 169–185 | 17 行 | `docs/runbook/01-topic.md` | 「01.2 平台与内容规格基准表」 | 逐字迁移 |
| 十 | BGM 约定 | 186–193 | 8 行 | `docs/runbook/01-topic.md` | 「01.3 BGM 选曲填入与响度约束」 | 逐字迁移，与选题 BGM 字段对接 |
| 十一 | 版权与发布 | 194–204 | 11 行 | `docs/runbook/09-publish.md` | 「发布合规与版权守则」 | 逐字迁移 |
| 十三 | 跨番混剪与 SP 特典 | 219–228 | 10 行 | 拆分：基础规则 → `04-clips.md`；双模态协议 → `06-render.md` | 「04.6 SP 素材基础规则」/「06.2 双模态片段协议」 | 拆分搬迁，04-clips.md 新增 **≤11 行**（硬上限，红队三轮 B2） |
| 十四（细则） | 四阶段工序卡操作细节 | 230–239 | 10 行 | `docs/WORKFLOW.md` | 「核心工序卡」 | 细节汇入，WORKFLOW.md 保持 ≤100 行 |

**`04-clips.md` 超限防护（v0.4 重算，红队三轮 B2）**：当前 69 行，若按原 ≤15 行增量迁入 SP 基础规则将预估 ~84 行，突破自家 §6.4 的 ≤80 硬门禁——「预估超限 + 状态接受」的组合禁止入档。处理方案：
- 将 SP 素材的「双模态片段协议」（ADR-0013，形态 1/形态 2 定义）拆出，迁入 `06-render.md`（渲染器需要该协议）；
- `04-clips.md` 仅保留「SP 素材基础规则」（锚点直通、无字幕索引、-an 剔除音轨），**迁入增量硬上限 ≤11 行**（69+11=80），执行清册时必须压缩表述至该上限内；压不进 11 行则回退评审，不得强行迁入。

### 4.3 瘦身后行数精确核算

基于 `AGENTS.md` 当前 241 行，各保留区块实测行数与压缩目标：

| 保留区块 | 原始行号 | 原始行数 | 压缩目标 | 压缩手段 |
|---|---|---|---|---|
| 头部 + 文档地图 | 1–29 | 29 行 | 22 行 | 删除冗余修饰，保留索引表 |
| 一、定位 | 30–39 | 10 行 | 10 行 | 保留全文 |
| 二、硬约束 | 40–53 | 14 行 | 14 行 | 保留全文 |
| 三、判据 | 54–84 | 31 行 | 31 行 | **一字不减，13 条全文保留** |
| 四、失败处理 | 85–93 | 9 行 | 9 行 | 保留全文 |
| 五、工程约定（核心） | 94–119 | 26 行 | 15 行 | 删除「素材硬规则」细节（已迁 04-clips.md），保留原则性条款 |
| 六、每期九步 | 120–135 | 16 行 | 15 行 | 保留表格与停机点，删除冗余说明 |
| 十二、环境与验证 | 205–218 | 14 行 | 14 行 | 保留全文（核心命令与写测试纪律） |
| **总计** | | **149 行** | **130 行** | 预留 10 行缓冲 |

**结论**：≤140 行目标可达，130 行方案留 10 行安全余量。

### 4.4 搬迁后目标文档行数预估

| 目标文档 | 当前行数 | 搬入行数 | 搬迁后预估 | 预算 | 状态 |
|---|---|---|---|---|---|
| `docs/runbook/01-topic.md` | 24 行 | +17（九）+8（十）=25 行 | ~49 行 | ≤60 行 | 安全 |
| `docs/runbook/04-clips.md` | 69 行 | +≤11（SP 基础规则，压缩表述） | ≤80 行 | ≤80 行（永久硬门禁） | 安全 |
| `docs/runbook/06-render.md` | 22 行 | +6（七节一半）+8（双模态）=14 行 | ~36 行 | ≤60 行 | 安全 |
| `docs/runbook/07-qc.md` | 24 行 | +5（七节一半）| ~29 行 | ≤60 行 | 安全 |
| `docs/runbook/09-publish.md` | 17 行 | +11（十一）| ~28 行 | ≤60 行 | 安全 |
| `docs/WORKFLOW.md` | 70 行 | +10（十四细则）| ~80 行 | ≤100 行 | 安全 |
| `docs/dev/STANDARD.md` | 500 行 | +22（八）| ~522 行 | 无明确预算 | 接受 |

**`04-clips.md` 行数门禁定为 ≤80 行（永久硬门禁，非临时放宽，红队三轮 B2 改写）**：SP 素材规则是排片工序的核心知识，强行拆分会破坏内聚性；迁入增量硬上限 ≤11 行（69+11=80），压不进则回退评审。待后续 runbook 重构时再评估是否独立成篇。

**门禁兜底**：`tests/test_docs_invariants.py` 新增 `test_runbook_04_clips_line_budget` 断言，超限时必须触发 PR 评审，不得直接合并。

---

## 5. 宿主与 REPL 接入规格

### 5.1 `_dispatch_agent_turn` 新签名

```python
def _dispatch_agent_turn(
    line: str,
    messages: list[dict[str, Any]],
    ep_dir: Path | None,
    scope: str,
    status: EpisodeStatus | None = None,
    extra_prompt: str = "",
    root: Path | None = None,
    approve_cb: Callable[[str, dict], bool] | None = None,
    tracker: SessionContextTracker | None = None,  # 新增；由 run_agent_loop 创建一次，会话级单例（红队三轮 M4）
) -> dict[str, Any]:
```

### 5.2 会话流程

**tracker 生命周期铁律（红队三轮 M4）**：`SessionContextTracker` 由 `run_agent_loop` 在会话初始化时创建一次，**会话级单例，与 `messages` 同寿命**；严禁在 `_dispatch_agent_turn` 轮内懒创建——否则 `injected_paths` 每轮清空、去重静默失效、工序文档每轮重复注入，与「精准增量注入」直接相反。

```python
# 0. 会话初始化（run_agent_loop 首轮，仅执行一次）
tracker = SessionContextTracker()
tracker.resident_prompt = assemble_resident_prompt(scope).content

# 每轮入口（_dispatch_agent_turn）
step_key = step_key_of(status.current_step if status else None)
# resolve_step_docs 返回 list[Path]，须经 load_injected_doc 转为 InjectedDoc（红队三轮 m3）
step_docs = [
    d
    for d in (load_injected_doc(p.as_posix()) for p in resolve_step_docs(scope, step_key))
    if d is not None
]

if not messages:
    # 首轮：messages[0] 仅含常驻层 + 动态层，工序层以独立 user 消息注入
    status_card = build_status_card(ep_dir, status)
    sys_content = tracker.get_initial_system_prompt(status_card)
    messages.append({"role": "system", "content": sys_content})

    # 工序层首轮注入（独立 user 消息，不拼入 messages[0]）
    if step_docs:
        injection = render_step_injection(step_docs)
        messages.append({"role": "user", "content": injection})
        tracker.injected_paths.update(d.rel_path for d in step_docs)
    tracker.active_step_key = step_key
else:
    # 后续轮：检测工序切换
    if step_key != tracker.active_step_key:
        new_docs = [d for d in step_docs if d.rel_path not in tracker.injected_paths]
        if new_docs:
            injection = render_step_injection(new_docs)
            messages.append({"role": "user", "content": injection})
            tracker.injected_paths.update(d.rel_path for d in new_docs)
        tracker.active_step_key = step_key

    # 刷新 status_card：复用 cli.py:667-673 现有整段重算机制，直接替换 messages[0]
    #（红队三轮 M4/B1：删除「就地追加卡片」分支，防止卡片无限堆积；常驻层前缀由
    #  assemble_resident_prompt 字节级恒定保证，整段重算不破坏 Prompt Cache 前缀）
    sys_content = tracker.get_initial_system_prompt(build_status_card(ep_dir, status))
    messages[0] = {"role": "system", "content": sys_content}

messages.append({"role": "user", "content": line})
outcome = run_tool_loop(messages, ...)
```

### 5.3 `SessionContextTracker` 完整定义

```python
@dataclass
class SessionContextTracker:
    resident_prompt: str = ""
    injected_paths: set[str] = field(default_factory=set)
    active_step_key: str | None = None
    _warned_paths: set[str] = field(default_factory=set)  # 红队 M2：警告去重

    def get_initial_system_prompt(
        self,
        status_card: str,
    ) -> str:
        """首轮 messages[0]：仅常驻层 + 动态层，工序层不拼入。"""
        return f"{self.resident_prompt}\n\n---\n\n{status_card}"

    def warn_once(self, path: str, message: str) -> None:
        """同一缺失文件只警告一次，防止 REPL 刷屏（红队 M2）。"""
        norm_path = str(Path(path).as_posix())  # 统一路径格式
        if norm_path not in self._warned_paths:
            print(f"[WARN] {message}", file=sys.stderr)
            self._warned_paths.add(norm_path)
```

---

## 6. 测试规格

### 6.1 依赖隔离测试（红队 M1 修正）

```python
def test_assembly_zero_heavy_deps():
    """装配器热路径严禁拖入重依赖（独立子进程探针，红队三轮 M3：进程内快照法对预载重依赖假阴性——
    若 numpy 已被先执行的测试载入，before 快照即含 numpy，差集永空）。照搬 Spec 2 §5.2 先例。"""
    import subprocess
    import sys

    probe_code = (
        "import pipeline.agent.assembly, sys; "
        "forbidden = ('numpy', 'torch', 'mlx_whisper', 'moviepy', 'transformers'); "
        "leaked = [m for m in forbidden if m in sys.modules]; "
        "assert not leaked, f'装配器顶层违规引入重量级包: {leaked}'"
    )
    res = subprocess.run(
        [sys.executable, "-c", probe_code],
        capture_output=True,
        text=True,
    )
    assert res.returncode == 0, f"依赖纯洁性检验失败:\n{res.stderr}"
```

### 6.2 工序键映射全覆盖测试

```python
@pytest.mark.parametrize("current_step,expected", [
    ("01 选题", "01"),
    ("02 脚本写作", "02"),
    ("02 脚本写作（草稿待定稿）", "02"),
    ("02.5 人审改稿", "02.5"),
    ("03 语音合成", "03"),
    ("03.5 配音顺听 / 04 排片", "03.5"),
    ("05 审时间码", "05"),
    ("06 本地渲染", "06"),
    ("07 自动质检", "07"),
    ("07 自动质检（未通过）", "07"),
    ("08 封面与标题候选", "08"),
    ("09 人工发布", "09"),
    (None, "default"),
    ("未知状态", "default"),
])
def test_step_key_of_full_coverage(current_step, expected):
    assert step_key_of(current_step) == expected
```

### 6.3 异常输入防御测试（红队 M3）

```python
def test_load_injected_doc_symlink_loop(tmp_path):
    """循环软链安全返回 None，不崩溃。"""
    doc = tmp_path / "doc.md"
    doc.symlink_to(doc)  # 自指循环
    result = load_injected_doc(str(doc.relative_to(tmp_path)), root=tmp_path)
    assert result is None


def test_load_injected_doc_encoding_fallback(tmp_path):
    """非 UTF-8 字符降级替换，不抛出 UnicodeDecodeError。"""
    doc = tmp_path / "doc.md"
    doc.write_bytes(b"\xff\xfe\x00\x01")  # 非法 UTF-8
    result = load_injected_doc(str(doc.relative_to(tmp_path)), root=tmp_path)
    assert result is not None
    assert result.content  # 已替换为可显示字符


def test_resolve_step_docs_corrupted_config(tmp_path):
    """配置 JSON 损坏时回退空清单，不抛出 KeyError。"""
    config = tmp_path / "assembly.json"
    config.write_text("{invalid json")
    result = resolve_step_docs("creative", "02", config_path=config, root=tmp_path)
    assert result == []
```

### 6.4 AGENTS.md 与 runbook 行数门禁

```python
from pathlib import Path
import re

REPO_ROOT = Path(__file__).resolve().parent.parent  # tests/ 上一级


def test_agents_md_line_budget():
    """AGENTS.md 瘦身后必须 ≤140 行。"""
    agents_path = REPO_ROOT / "AGENTS.md"
    lines = agents_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) <= 140, f"AGENTS.md 当前 {len(lines)} 行，超出 140 行预算"


def test_agents_md_thirteen_criteria_intact():
    """十三条判据完整保留，防搬迁误删。

    红队三轮 M2：非锚定子串匹配会命中硬约束节的编号列表（AGENTS.md:46-48 的
    「1. 2. 3.」），删判据 1/2/3 照样假绿。必须行首锚定 + 判据节区限定。
    红队四轮 N1：节区定位必须标题锚定——「判据」一词首现于头部段落与文档地图，
    非标题锚定的正则会抢先命中第 3 行，导致干净文件上 13 条判据全部漏报假红
    （已实测复现）。
    已知上限：判据条目正文未来若出现行首数字编号子列表，对应编号仍可能假绿——
    当前文件无此形态，可接受。
    """
    agents_path = REPO_ROOT / "AGENTS.md"
    content = agents_path.read_text(encoding="utf-8")
    # 限定判据节区（标题锚定，至下一节标题），避免命中其他节的编号列表
    m = re.search(r"^#+ [^\n]*判据[^\n]*\n(.*?)(?=\n#+ |\Z)", content, re.S | re.M)
    assert m, "未定位到十三条判据节区"
    section = m.group(1)
    for i in range(1, 14):
        assert re.search(rf"^\s*{i}[.、]", section, re.M), f"判据第 {i} 条缺失"


def test_runbook_04_clips_line_budget():
    """04-clips.md 硬门禁 ≤80 行（Spec v0.4 §4.4，永久门禁非临时放宽）。超限时必须拆分或回退。"""
    clips_path = REPO_ROOT / "docs/runbook/04-clips.md"
    lines = clips_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) <= 80, (
        f"04-clips.md 当前 {len(lines)} 行，超出 80 行硬门禁。"
        "请评估是否将 SP 素材规则拆分为独立 runbook。"
    )
```

### 6.5 变异检验（AGENTS.md 十二节纪律）

1. **行数变异**：人为将 AGENTS.md 增至 141 行，断言 `test_agents_md_line_budget` 爆红；
2. **依赖变异**：在 `assembly.py` 顶层写入 `import numpy`，断言 `test_assembly_zero_heavy_deps` 爆红；
3. **判据变异**：分别删除判据第 7 条与第 1 条各做一次，断言 `test_agents_md_thirteen_criteria_intact` 均爆红——第 1 条用例专验行首锚定不受硬约束节「1. 2. 3.」编号列表（AGENTS.md:46-48）干扰（红队三轮 M2）。

---

## 7. 实施步骤与 PR 划分

### PR0：AGENTS.md 搬迁与瘦身
- 按 §4.2 清册执行搬迁；
- 瘦身 AGENTS.md 至 ≤140 行；
- 更新 `tests/test_docs_invariants.py` 新增行数与判据完整性断言；
- 验证：搬迁前后各 runbook 与 STANDARD.md 的 diff 零语义变更。

### PR1：装配器核心模块
- 新增 `config/agent/assembly.json`；
- 新增 `pipeline/agent/assembly.py`；
- 新增 `tests/test_agent_assembly.py`（含 6.1–6.3 全部测试）；
- 验证：`pytest tests/test_agent_assembly.py` 全绿。

### PR2：CLI/REPL 接入
- 修改 `pipeline/agent/cli.py`：引入 `SessionContextTracker`（由 `run_agent_loop` 创建一次，会话级单例），改造 `_dispatch_agent_turn`；
- **删除/收编 `cli.py:513-539` 的 `assemble_system_prompt`**：messages[0] 刷新复用 `cli.py:667-673` 现有整段重算机制，由装配器单源供给内容，禁止新旧两套装配逻辑双轨并存（红队三轮 B1）；
- 新增集成测试 `tests/test_agent_assembly_integration.py`：模拟工序切换，验证 user 注入与 messages[0] 前缀稳定性；
- 验证：`pytest tests/test_agent_assembly_integration.py` 全绿；端到端跑通一期完整流程（01→09）与 Prompt Cache 命中率日志确认**降级为可选冒烟**，不作 PR 门禁（红队三轮 m6）。

---

## 8. 明确不做的事（范围闸门）

- 不做 skill 市场 / 插件化加载（装配清单是仓库内配置，不开放运行时注册）；
- 不做文档内容摘要化 / LLM 压缩注入（判据类文本禁摘要）；
- 不动 `02-script.md` SSOT 与期产物的加载逻辑；
- 不引入向量检索 / embedding 做文档路由（规模不配，JSON 路由表足够）；
- 不修改 `pipeline.status` 的推导逻辑（状态真相源不变）。

---

## 9. 完成判定（可逐项打勾）

- [ ] `AGENTS.md` 瘦身后 ≤140 行，十三条判据完整保留；
- [ ] 搬迁内容 diff 为零（逐字比对）；
- [ ] `config/agent/assembly.json` 配置生效，12 种工序状态全覆盖；
- [ ] `pipeline/agent/assembly.py` 通过依赖隔离测试；
- [ ] 工序切换时 user 注入消息格式正确，messages[0] 前缀字节级恒定；
- [ ] 集成测试：一期完整流程无路由落空、无重复注入；
- [ ] 变异检验三项全部通过。
