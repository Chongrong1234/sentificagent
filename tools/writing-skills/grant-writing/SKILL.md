# Grant Writing Skill

Use this skill when drafting a funding proposal, project application, or grant
summary from local RAG evidence.

**IMPORTANT — Template First**: The proposal structure (sections, their titles
and order) is defined by the project's LaTeX template or the NSFC/agency format,
NOT by this skill. This skill only governs language quality, argumentation style,
and evidence usage within the template-defined framework. Never override, reorder,
or rename template sections.

## Section-level writing protocol

- Project title: concise, domain-specific, and technically precise.
- Executive summary: 1-2 paragraphs covering problem, objective, technical route,
  innovation, feasibility, and expected outputs.
- Scientific problem and significance: 4-6 paragraphs. Start from national or
  disciplinary need, narrow to the concrete scientific problem, explain why
  existing methods are insufficient, then state the opportunity enabled by new
  evidence.
- Research objectives: list 2-4 objectives. Each objective must be measurable and
  linked to a scientific question.
- Technical route and work packages: 4-8 paragraphs plus enumerated work
  packages. Each work package should include input data, method, expected result,
  validation, and dependency on other packages.
- Innovation points: 3-5 numbered points. Each point must explain what is new
  relative to current evidence and why it is non-trivial.
- Feasibility and preliminary basis: 3-6 paragraphs. Use local RAG evidence to
  establish literature basis, data basis, method basis, and team/system basis.
- Timeline and milestones: clear staged plan with deliverables.
- Expected outputs: papers, datasets, software, models, evaluation benchmarks, or
  decision-support tools.
- Risks and mitigation: identify data, model, compute, field validation, and LLM
  hallucination risks with countermeasures.

## Writing rules

- Align objectives with evidence retrieved from the local library.
- Convert literature gaps into specific work packages.
- Make innovation claims concrete and bounded.
- Keep deliverables measurable.
- Include risk mitigation for data availability, model reliability, compute, and
  field validation when relevant.
- Cite local evidence with the template's citation commands. Never fabricate
  citation keys or use fake `[P1]`-style markers.
- The planner must infer RAG retrieval queries from the user's conversation and
  project goal. Do not require the user to manually provide search keywords.
- Use all relevant local evidence returned by the RAG stage. Do not cap evidence
  by default.
- Every proposal section must be present and substantive. Never leave TODO,
  placeholder, or "to be expanded" sections.
- If evidence is insufficient, keep the section and explicitly describe the
  missing preliminary basis or validation evidence.
- LaTeX output must include all proposal sections and a references/evidence
  section.
- The output should read like a serious NSFC-style draft, not a short memo or
  bullet-only outline.

## De-AI Style Rules for Chinese Grant Writing (CRITICAL)

Apply these rules aggressively to remove LLM-generated writing style:

### Banned phrases — never use:
- "值得注意的是..." / "需要指出的是..." / "值得一提的是..."
- "综上所述..." / "总而言之..." — at the end of every section
- "不仅...而且..." / "既...又..." — used excessively (once per section max)
- "具有重要的理论意义和应用价值" — this is pure filler, state the specific value
- "为进一步研究奠定了基础" — state what specifically it enables
- "在...领域具有广泛的应用前景" — be specific about what applications
- "国内外学者开展了大量研究" — cite who did what specifically
- "取得了丰硕的成果" — this says nothing, give concrete achievements
- "引起了广泛关注" / "成为了研究热点" — state who is working on it

### Structural rules:
- 每句话都要有信息量：问题、证据、方法细节、技术指标、风险、对策
- 不要写"路线图"句（"本章将介绍..."），直接进入内容
- 段首不要用"随着...的发展"、"近年来..."等套话开头
- 不要堆砌形容词："系统深入的研究" → 写清楚研究了什么
- 不要用夸张修辞："重大突破"、"革命性"、"颠覆性" → 用具体描述
- 结论性套话禁止："本项目的研究成果将为...提供重要的理论和实践指导"

### 正式语体要求:
- 使用正式、具体、可执行的语气
- 关键科学问题要写具体的技术瓶颈，不要写泛泛的宏观问题
- 技术路线要落到具体方法、模型、数据、验证步骤
- 创新点要与现有方法做具体对比，说明"新在哪里、为什么非平凡"
- 已有结果必须写成研究基础、预实验观察、技术储备或可行性证据
- 严禁把预期成果写成已完成的最终产出
