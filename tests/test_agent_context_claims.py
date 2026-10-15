"""D92：会进模型 context 的文字不许谎报审批卡，常驻层只放做片规则。

ADR-0030（D78）把大多数操作改成免卡之后，工具描述里还写着「每次调用弹人审卡」——模型以为有卡兜底，
就不先问人。行为口径是「先问清楚再做」（director.md 第 2 条），描述必须和审批事实一致。
"""

from __future__ import annotations

import json
import re

import pytest

from pipeline.agent.assembly import assemble_resident_prompt
from pipeline.agent.session import AUTO_APPROVED_TOOLS
from pipeline.agent.tools import TOOL_SCHEMAS

# 「这个工具每次都会弹卡」一类的正面断言；「不弹卡」「只在…时弹卡」「会弹审批卡（列出的少数命令）」不算
CARD_CLAIM = re.compile(r"弹人审卡|过人审卡|校验通过后弹审批卡|每次(?:调用|执行|写)?都?会?弹卡|一张审批卡")

# run_pipeline / bash 不在免卡集合里（按参数分流），但它们的大多数调用同样免卡
CHECKED = sorted(AUTO_APPROVED_TOOLS | {"run_pipeline", "bash"})


@pytest.mark.parametrize("name", CHECKED)
def test_tool_text_does_not_claim_cards(name: str) -> None:
    text = json.dumps({k: v for k, v in TOOL_SCHEMAS[name].items() if k in ("description", "parameters")},
                      ensure_ascii=False)
    hit = CARD_CLAIM.search(text)
    assert hit is None, f"{name} 的描述仍写着「{hit.group(0)}」，与审批事实不符（ADR-0030）"


def test_acquire_propose_says_it_checks_not_downloads() -> None:
    """D100 起它只登记并核对、不下载：描述要说清，并指明下载另走 acquire fetch、表以机器表为准。"""
    text = TOOL_SCHEMAS["acquire_propose"]["description"]
    assert "不下载" in text and "立即抓取" not in text
    assert "acquire fetch" in text and "review.table" in text


@pytest.mark.parametrize("scope", ["creative", "pipeline", "idea", "project", "asset"])
def test_resident_is_production_rules_not_dev_agents_md(scope: str) -> None:
    resident = assemble_resident_prompt(scope)
    assert "# 做片常驻规则" in resident.content
    assert "config/agent/scopes/production.md" in [path for path, _ in resident.doc_texts]
    # AGENTS.md 的开发段落（判据、工程约定、环境与验证）不进 AVA 的 context
    for dev_marker in ("## 七、环境与验证", "## 五、工程约定", "numpy"):
        assert dev_marker not in resident.content, f"{scope} 常驻层混进了开发段落：{dev_marker}"
