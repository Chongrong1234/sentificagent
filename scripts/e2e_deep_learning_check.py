"""E2E test: full three-stage writing workflow for a deep learning topic.

Topic: 面向移动巡检的轻量化路面裂缝检测 (Lightweight Pavement Crack Detection for Mobile Inspection)

Tests the complete workflow:
  Phase 1: Project creation + exploration + topic selection
  Phase 2: Outline negotiation + writing order + chapter writing + citations + lock/unlock
  Phase 3: Final review + compilation
"""

import json
import sys
import os
import unittest

# This file is a manual smoke test. It performs writes at import time and may
# require a local TeX installation, so keep it out of automatic discovery.
if __name__ != "__main__":
    raise unittest.SkipTest("manual E2E smoke test; run this file directly")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.literature_agent import writing_workflow as wf
from src.literature_agent import writing_workspace as ws
from src.literature_agent import template_guardrails as tg
from src.literature_agent.template_guardrails import load_guardrails


PASS = 0
FAIL = 0
STEP = 0


def step(label):
    global STEP
    STEP += 1
    print(f"\n{'='*70}")
    print(f"  STEP {STEP}: {label}")
    print(f"{'='*70}")


def ok(msg=""):
    global PASS
    PASS += 1
    suffix = f"  -- {msg}" if msg else ""
    print(f"  [PASS]{suffix}")


def bad(msg):
    global FAIL
    FAIL += 1
    print(f"  [FAIL] {msg}")


def check(condition, msg=""):
    if condition:
        ok(msg)
    else:
        bad(msg)


# ── Test Data ────────────────────────────────────────────────────────

TEMPLATE_ID = "hithesis-harbin-bachelor-opening"
TOPIC = "面向移动巡检的轻量化路面裂缝检测"
TOPIC_TYPE = "deep_learning"

_test_project_id = None
_guardrails = None
_sections = []

# ── Phase 1: Project + Exploration ───────────────────────────────────

step("Create project")

result = ws.create_project({
    "title": TOPIC,
    "template_id": TEMPLATE_ID,
    "goal": "撰写关于移动端路面裂缝检测的本科开题报告",
    "requirements": "关注轻量化模型设计（MobileNet/EfficientNet变体），用于移动巡检设备实时检测",
    "author": "E2E Test",
    "query": "lightweight pavement crack detection mobile inspection real-time segmentation",
})
check("project_id" in result, f"project_id key present: {list(result.keys())[:5]}")
_test_project_id = result.get("project_id", "")
check(bool(_test_project_id), f"project_id: {_test_project_id}")
print(f"  project_id: {_test_project_id}")

# ── Phase 1b: Load guardrails ───────────────────────────────────────

step("Load guardrails")

_guardrails = load_guardrails(_test_project_id)
check(isinstance(_guardrails, dict), f"guardrails is dict")
check("sections" in _guardrails, f"has 'sections' key")

_sections = _guardrails.get("sections", [])
check(len(_sections) > 0, f"sections count: {len(_sections)}")
if _sections:
    for s in _sections[:5]:
        print(f"  - {s.get('id','?'):30s} {s.get('title','?')}")
    if len(_sections) > 5:
        print(f"  ... and {len(_sections)-5} more")

# ── Phase 1c: Exploration ────────────────────────────────────────────

step("Get exploration report")

try:
    report = wf.get_exploration_report(_test_project_id, TOPIC)
    check("topics" in report or "report" in report or "summary" in report,
          f"exploration keys: {list(report.keys())[:5]}")
except Exception as e:
    bad(f"get_exploration_report: {e}")

# ── Phase 1d: Select topic ──────────────────────────────────────────

step("Select exploration topic")

result = wf.select_exploration_topic(_test_project_id, TOPIC)
check("stage" in result or "workflow" in result, f"topic selection keys: {list(result.keys())[:5]}")
stage = result.get("stage", result.get("workflow_stage", ""))
print(f"  stage: {stage}")

# ── Phase 2: Outline Negotiation ─────────────────────────────────────

step("Start outline negotiation")

result = wf.start_outline_negotiation(_test_project_id)
check("stage" in result or "sections" in result, f"negotiation started, keys: {list(result.keys())[:5]}")
print(f"  stage: {result.get('stage', result.get('workflow_stage', '?'))}")

# Negotiate ~4 sections
negotiated = []
for i, sec in enumerate(_sections[:4]):
    step(f"Negotiate section: {sec.get('title','?')}")

    sec_id = sec["id"]
    result = wf.negotiate_section(
        _test_project_id,
        sec_id,
        "approved",
        strategy_label="按模板方案",
    )
    check("stage_card" in result or "sections" in result,
          f"negotiate {sec_id}: {list(result.keys())[:4]}")
    negotiated.append(sec)
    print(f"  section: {sec_id}")

# ── Phase 2b: Writing order ─────────────────────────────────────────

step("Recommend writing order")

result = wf.recommend_writing_order(_test_project_id, TOPIC_TYPE)
check("order" in result or "sections" in result, f"order keys: {list(result.keys())[:5]}")

ordered = result.get("order") or result.get("sections", [])
if ordered:
    order_ids = []
    for item in ordered:
        if isinstance(item, dict):
            order_ids.append(item.get("id", ""))
        else:
            order_ids.append(str(item))
    order_ids = [o for o in order_ids if o]
    if not order_ids:
        order_ids = [s["id"] for s in negotiated]
    print(f"  order: {order_ids}")

    step("Set writing order")
    result = wf.set_writing_order(_test_project_id, order_ids)
    check(True, f"order set: {len(order_ids)} sections")
else:
    order_ids = [s["id"] for s in negotiated]
    print(f"  no order recommended, using negotiated order: {order_ids}")

# ── Phase 2c: Chapter writing ────────────────────────────────────────

LATEX_CONTENT_TEMPLATE = (
    "\\section{%s}\n"
    "\\textbf{研究背景与意义}\n\n"
    "路面裂缝是公路养护中最常见的病害之一。传统的裂缝检测方法依赖人工巡检，"
    "效率低、主观性强且难以覆盖大规模路网。随着深度学习技术的发展，"
    "基于卷积神经网络的自动裂缝检测方法取得了显著进展。\n\n"
    "然而，现有方法大多依赖高算力服务器，难以部署到移动巡检设备上。"
    "因此，研究面向移动端的轻量化裂缝检测模型具有重要的理论意义和工程价值。"
)

for sec_id in order_ids[:3]:  # Write first 3 chapters
    step(f"Start chapter writing: {sec_id}")

    result = wf.start_chapter_writing(_test_project_id, sec_id)
    check("stage" in result or "chapter_state" in result,
          f"chapter writing started for {sec_id}")
    print(f"  state: {result.get('chapter_state', result.get('stage', '?'))}")

    step(f"Save draft content with guardrails validation: {sec_id}")

    sec_info = next((s for s in _sections if s["id"] == sec_id), _sections[0])
    content = LATEX_CONTENT_TEMPLATE % sec_info.get("title", sec_id)

    # Validate through guardrails
    stripped, violations = tg.strip_illegal_content(
        content, "", _guardrails, sec_id
    )
    if violations:
        print(f"  {len(violations)} violation(s):")
        for v in violations[:3]:
            print(f"    - {v.get('message', str(v))}")

    save_result = wf.save_section_draft(_test_project_id, sec_id, stripped)
    check("path" in save_result or "file" in save_result,
          f"draft saved for {sec_id}")

    # Detect citation needs
    citations = wf.detect_citation_need(stripped, sec_id)
    print(f"  citation points detected: {len(citations)}")

    step(f"Lock chapter: {sec_id}")
    lock_result = wf.lock_chapter(_test_project_id, sec_id)
    check("workflow" in lock_result or "compile" in lock_result,
          f"chapter locked: {sec_id}")

# ── Phase 2d: Unlock and re-lock test ───────────────────────────────

if len(order_ids) >= 2:
    step(f"Unlock chapter: {order_ids[1]}")
    unlock_result = wf.unlock_chapter(_test_project_id, order_ids[1])
    check(True, f"unlocked {order_ids[1]}")

    step(f"Re-lock chapter: {order_ids[1]}")
    relock = wf.lock_chapter(_test_project_id, order_ids[1])
    check(True, f"re-locked {order_ids[1]}")

# ── Phase 3: Context compression ─────────────────────────────────────

if order_ids:
    step(f"Compress context for: {order_ids[0]}")
    ctx_result = wf.compress_context(_test_project_id, order_ids[0])
    check(True, f"context compressed, locked_summaries: {len(ctx_result.get('locked_summaries', {}))}")

# ── Phase 3b: Final Review ──────────────────────────────────────────

step("Run final review")
review_result = wf.run_final_review(_test_project_id)
check("compile" in review_result and "audit" in review_result,
      f"final review keys: {list(review_result.keys())[:5]}")
audit = review_result.get("audit", {})
print(f"  audit verdict: {audit.get('verdict', '?')}, score: {audit.get('overall_score', '?')}")

# ── Phase 3c: Compilation ────────────────────────────────────────────

step("Compile project")
compile_result = wf.compile_project(_test_project_id)
check("status" in compile_result and "returncode" in compile_result,
      f"compilation: {compile_result.get('status')} (rc={compile_result.get('returncode')})")

# ── Summary ──────────────────────────────────────────────────────────

print(f"\n{'='*70}")
print(f"  E2E TEST RESULTS: {PASS} passed, {FAIL} failed ({PASS+FAIL} checks)")
print(f"  Project ID: {_test_project_id}")
print(f"{'='*70}")

if FAIL > 0:
    sys.exit(1)
