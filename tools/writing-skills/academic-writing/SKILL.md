# Academic Writing Skill

Use this skill when drafting an English research paper, survey, or journal/conference
submission from local RAG evidence. The target style is CCF top conference /
top journal quality.

**IMPORTANT — Template First**: The paper structure (chapters, sections, their titles
and order) is defined by the project's LaTeX template, NOT by this skill. This skill
only governs language quality, academic style, and evidence usage within the
template-defined framework. Never override, reorder, or rename template sections.

## Principles

- Write claims only when supported by retrieved evidence.
- Prefer precise scope, concrete method names, datasets, and limitations.
- Separate background, gap, method, evidence, and implications.
- Cite local evidence with the template's citation commands (e.g., `\citep{key}`,
  `\citet{key}`, `\parencite{key}`). Never fabricate citation keys.
- Keep LaTeX compilable and self-contained.
- Use all relevant local evidence returned by the RAG stage. Do not arbitrarily
  restrict the evidence count unless the caller explicitly asks for a short draft.
- Every required section must contain substantive prose grounded in evidence.
  Never leave placeholder text such as "to be expanded", "TODO", or "this
  section should be expanded".
- If a section cannot be supported by available evidence, keep the section and
  explicitly state the evidence gap and what evidence is missing.

## Section-level writing protocol

- Abstract: 1 paragraph. State the research problem, evidence base, method of
  synthesis, main findings, and limitations.
- Introduction: 3-5 paragraphs. Paragraph 1 defines the scientific or engineering
  problem. Paragraph 2 explains why the problem matters in the target domain.
  Paragraph 3 summarizes the state of evidence. Paragraph 4 states the gap.
  Paragraph 5 states the paper contribution and structure.
- Related Work: 4-8 paragraphs. Organize by research streams rather than listing
  papers one by one. For each stream, summarize representative evidence, compare
  methods, and identify unresolved gaps.
- Methods/System Design: 3-6 paragraphs. Describe data sources, retrieval
  strategy, model/workflow design, evaluation logic, and reproducibility
  considerations.
- Evidence Synthesis/Results: 4-8 paragraphs. Each paragraph should make one
  evidence-grounded claim, cite local evidence keys, compare at least two pieces
  of evidence when possible, and end with an implication.
- Discussion: 3-6 paragraphs. Discuss theoretical implications, engineering
  tradeoffs, domain implications, and future research.
- Limitations: 2-4 paragraphs. Distinguish evidence limitations, method
  limitations, data limitations, and generation risks.
- Conclusion: 1-2 paragraphs. Summarize the answer, contribution, and next steps.

## De-AI Style Rules (CRITICAL)

Remove all traces of "LLM-generated" writing. Apply these rules aggressively:

### Banned phrases — never use:
- "It is worth noting that..." / "It should be noted that..."
- "Furthermore," / "Moreover," / "In addition," — used more than once per section
- "In this paper, we..." — used more than once in the entire draft
- "This paper proposes/suggests/argues..." — prefer direct statements
- "It is widely acknowledged that..." / "As is well known..."
- "Due to the fact that..." → use "because"
- "In order to..." → use "to"
- "A large number of..." → use "many" or give a number
- "Has the ability to..." → use "can"
- "In the context of..." → delete or be specific
- "Plays a crucial/important/key role..." → state what it does concretely
- "Has garnered significant attention..." → state who studied it and what they found
- "Shedding light on..." → "showing" or "revealing"
- "Paves the way for..." → "enables"

### Structural rules:
- Every sentence must carry specific information: a claim, evidence, comparison,
  method detail, result, limitation, or implication
- No "roadmap" sentences ("Section 3 discusses X, Section 4 presents Y...")
- No concluding-sentence clichés ("These results demonstrate the effectiveness...")
- Vary sentence length: mix short direct statements with longer explanatory ones
- Paragraphs must have a clear topic sentence and logical progression
- Transitions must be earned: only use "However," "Therefore," "Specifically," when
  the logical relationship is real and non-obvious
- Avoid adjective stacking: "comprehensive systematic thorough analysis" → pick one

### Citation style:
- Always use the template's actual citation commands. If the template uses
  `\citep{}`, use `\citep{}`; if `\parencite{}`, use `\parencite{}`; never
  default to `\cite{}`
- Citations should anchor specific claims: "Smith et al. (2024) found X" not
  "Previous work has studied X [1,2,3]"

## Style constraints

- Use a formal academic tone. Write like a submission intended for peer review:
  specific claims, explicit gaps, concrete comparisons, no motivational filler.
- Prefer the compact, evidence-driven style used in strong CS conference and
  interdisciplinary journal papers.
- Avoid unsupported superlatives ("state-of-the-art", "superior performance")
  unless backed by specific evidence.
- In LaTeX, use standard packages already present in the template. Do not add
  new packages unless necessary for the content.
- The output should be a real draft, not an outline. Avoid overly short sections.
