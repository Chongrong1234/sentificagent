# Writing Studio — 决策驱动协作写作系统 开发规格书

**版本**: v1.0  
**日期**: 2026-05-17  
**状态**: 设计完成，待开发

---

## 目录

1. [设计目标](#1-设计目标)
2. [核心原则](#2-核心原则)
3. [系统架构](#3-系统架构)
4. [工作流：三阶段决策驱动](#4-工作流三阶段决策驱动)
5. [护栏系统：大纲契约](#5-护栏系统大纲契约)
6. [引用审查系统：Citation Gate](#6-引用审查系统citation-gate)
7. [前端设计](#7-前端设计)
8. [后端模块设计](#8-后端模块设计)
9. [文件结构](#9-文件结构)
10. [开发路线](#10-开发路线)

---

## 1. 设计目标

当前「RAG 整篇成文」模式的三个致命缺陷：

1. **决策权不在用户手里** — 用户只能接受或拒绝整篇，无法逐节决策
2. **LLM 替用户做了太多假设** — 研究角度、论证策略、重点取舍全凭 LLM 猜测
3. **总是修改模板格式和架构** — 护栏不够硬，生成后格式混乱

新系统的三个核心目标：

1. **每一步都是用户做的决定** — LLM 是执行者不是决策者
2. **只修改内容，不修改模板架构** — 基于 YAML 护栏的硬约束
3. **每个引用都经过用户确认** — 杜绝虚假引用和弱引用

---

## 2. 核心原则

| 原则 | 含义 |
|------|------|
| **用户决策驱动** | 每一步都是用户做的选择，LLM 给出选项让用户拍板 |
| **选项优于指令** | LLM 给出 2-3 个具体选项，而不是等用户打字描述 |
| **大纲即契约** | 写作前逐章协商，生成 guardrails.yaml 作为不可变合同 |
| **逐章锁定** | 确认的章节不可回退修改（除非用户明确解锁） |
| **引用必审查** | 每个 `\cite{}` 必须用户确认，标注支撑强度 |
| **证据透明** | 每步告知文献库中有什么、缺什么，防止生成无源之水 |
| **模板不可变** | documentclass、导言区、章节标题、参考文献尾区绝对不可修改 |
| **自由写作顺序** | 用户可以任意选择先写哪一章，非线性的写作流程 |

---

## 3. 系统架构

```
┌──────────────────────────────────────────────────┐
│                    Frontend                       │
│  ┌──────────┐ ┌──────────┐ ┌──────────────────┐ │
│  │ 章节卡片列 │ │ 写作/协商区│ │ 预览+编译日志     │ │
│  │ (左侧导航) │ │ (中央主体) │ │ (底部可折叠)      │ │
│  └──────────┘ └──────────┘ └──────────────────┘ │
└────────────────────┬─────────────────────────────┘
                     │ HTTP + SSE
┌────────────────────┴─────────────────────────────┐
│                   Backend                         │
│                                                   │
│  ┌─────────────┐  ┌──────────────┐               │
│  │ 工作流引擎    │  │ 护栏系统      │               │
│  │ workflow.py  │  │ guardrails.py │               │
│  │ (阶段状态机)  │  │ (YAML加载/验证)│               │
│  └──────┬───────┘  └──────┬───────┘               │
│         │                  │                      │
│  ┌──────┴──────────────────┴───────┐              │
│  │         Citation Gate           │              │
│  │  citation_gate.py               │              │
│  │  (引用审查/支撑强度/批量审核)      │              │
│  └──────┬──────────────────────────┘              │
│         │                                         │
│  ┌──────┴──────────────────────────┐              │
│  │         现有模块（复用）          │              │
│  │  template_profile.py            │              │
│  │  template_library.py            │              │
│  │  writing_workspace.py           │              │
│  │  writing_audit.py               │              │
│  │  search_pipeline.py             │              │
│  │  library_store.py               │              │
│  └─────────────────────────────────┘              │
└───────────────────────────────────────────────────┘
```

---

## 4. 工作流：三阶段决策驱动

### 状态机总览

```
STAGE_1: 勘探与选题
    │
    ├─ 用户在文献库中探索，LLM 给出选题建议
    ├─ 用户拍板选题方向
    │
    ▼
STAGE_2: 逐章协商与写作
    │
    ├─ 2a: 大纲协商 — LLM 逐章提案，用户逐章确认
    ├─ 2b: 顺序选择 — 用户决定先写哪章
    ├─ 2c: 逐章写作 — 每章独立循环（协商→写作→审核→锁定）
    │     └─ 每遇到引用，触发 Citation Gate
    │
    ▼
STAGE_3: 终审与编译
    │
    ├─ 编译检查 + 护栏验证 + 引用完整性
    ├─ 交叉引用一致性
    ├─ 节间过渡检查
    │
    ▼
  输出最终 PDF
```

---

### STAGE 1: 勘探与选题

**输入：** 用户给一个主题词（可以很模糊，如"路面病害检测 深度学习"）

**LLM 行为：**
1. 检索本地文献库
2. 按研究方向聚类
3. 输出勘探报告（不输出论文草稿）

**勘探报告结构：**

```
## 文献勘探报告

### 覆盖方向
- CNN裂缝检测: 8篇
- 小样本/迁移学习: 5篇
- 轻量化/边缘部署: 4篇
- 多模态融合: 3篇
- 数据集与基准: 3篇

### 跨方向洞察
- 高精度模型 ↔ 轻量化之间有明显空白
- 小样本+多模态交叉点几乎未被探索

### 选题建议

A. [综述型] 轻量化检测方法综述
   证据充分度: ★★★☆（13篇直接相关）
   适合: 开题报告第一章/毕业论文文献综述

B. [问题导向型] 面向移动巡检的病害检测—从模型压缩到边缘部署
   证据充分度: ★★★☆（12篇直接+4篇补充）
   适合: 课题申请书/开题报告

C. [方法改进型] 基于知识蒸馏的路面病害检测模型压缩
   证据充分度: ★★☆☆（仅4篇直接相关）
   适合: 如果你有实验数据
```

**用户有明确 idea 时：**

用户选择 C 或自定义方向 → LLM 不跳过讨论，而是追问澄清：

1. 重点方向（方法创新 vs 工程部署 vs 两者兼顾）
2. 实验数据情况（现有数据 vs 纯文献论证）
3. 目标模板匹配度（选题是否能填满模板要求的所有章节）
4. 证据缺口识别（哪些论点文献不足）

**输出：** 确认的选题方向 + 初步证据评估

---

### STAGE 2: 逐章协商与写作

#### Phase 2a: 大纲协商

对模板的每个 section（按原顺序），LLM 输出协商卡片：

```
Section 2/8: 国内外在该方向的研究现状及分析

模板要求: 整理国内外文献、方法对比、研究空白

可支撑文献: 23篇，覆盖3个技术流派

建议写作策略:
  A. [技术流派型] 按CNN检测 → 轻量化 → 边缘部署三个方向组织
  B. [时间演进型] 传统方法 → 深度学习 → 轻量化部署
  C. [问题驱动型] 检测精度 → 实时性 → 实际部署 三个挑战展开

你的选择？ [A] [B] [C] [我有想法]
```

**协商标记规则（在 guardrails.yaml 中定义）：**

| negotiation 值 | 含义 | 示例 |
|----------------|------|------|
| `full` | 需要完整协商 | 文献综述、研究内容、研究方案 |
| `light` | 快速确认即可 | 进度安排、条件经费 |
| `skip` | 不需要协商 | 参考文献（系统管理） |

**输出：** 逐章确认后的完整大纲契约 → 写入 `memory/guardrails.yaml`

#### Phase 2b: 写作顺序选择

大纲确认后，LLM 根据选题类型推荐写作顺序：

```
选题类型: 方法/系统型
推荐顺序:
  1. 主要研究内容   ← 你最清楚要研究什么
  2. 研究方案       ← 紧随内容
  3. 国内外研究现状  ← 有了内容才好对标文献
  4. 困难与措施     ← 方案写完自然想到困难
  5. 课题来源与意义  ← 对全局有把握后写引言
  6. 条件与经费     ← 次要
  7. 进度安排       ← 最后规划

[接受推荐] [手动拖拽排序]
```

**智能推荐映射（选题类型 → 推荐顺序）：**

| 选题类型 | 推荐首章 | 推荐顺序特征 |
|---------|---------|------------|
| 方法/系统型 | 研究内容 | 内容→方案→文献→困难→背景→条件→进度 |
| 综述型 | 文献综述 | 文献→内容→方案→背景→困难→条件→进度 |
| 问题驱动型 | 课题来源 | 按模板顺序，但文献综述可提前 |
| 实验报告型 | 研究方案 | 方案→内容→结果→讨论→背景→文献 |

用户可选择接受推荐或手动拖拽排序。

#### Phase 2c: 逐章写作

每章的独立循环：

```
协商（确定本章写作策略 + 子结构）
    │
    ▼
生成（LLM 逐段生成，遇到引用暂停）
    │
    ├─ 引用审查（Citation Gate）
    │
    ▼
自检（护栏验证 + 编译检查）
    │
    ▼
用户审阅
    ├─ 通过 → 锁定本章，进入下一章
    ├─ 修改 → 具体修改指示，LLM 修改后重新自检
    └─ 重写 → 回到协商阶段，重新确定策略
```

**上下文压缩策略：**

每开新章时：
- **保留：** 模板护栏 YAML、已锁定章节摘要（每章 2-3 句）、当前章文献证据、当前章协商记录
- **移出：** 其他章节完整文本、其他章节协商细节、无关文献条目
- **摘要格式：** 「Section 1 确立了路面病害检测的工程背景和自动化需求。引用了 Gopalakrishnan (2018) 等 5 篇文献。」

#### 阶段转换规则

**锁定触发：** 用户通过本章审阅 → 章节状态变为 `locked` → 自动编译一次
**解锁触发：** 用户点击已锁定章节的解锁按钮 → 弹出依赖提示

**依赖追踪：**
- 解锁某章 → 所有序号更大的已锁定章标记为 `needs_review`
- 修改某章的引用 → 提醒其他引用了同一文献的章节
- 跨章引用冲突 → 引用编号自动重新计算

---

### STAGE 3: 终审与编译

**自动检查项：**
1. 编译通过（复用现有 compile_project）
2. 护栏验证 — 所有 immutable zone 未被修改
3. 引用完整性 — 所有 `\cite` 键在 `.bib` 中存在
4. 交叉引用检查 — `\ref` 对应的 `\label` 存在
5. 节间过渡检查 — LLM 通读，标记过渡生硬处
6. 终审质量评分（复用现有 Mode A-F 审计）

**用户手动检查：**
- 通读全文
- 图表位置确认
- 参考文献格式确认

**输出：** 编译成功 → 下载 PDF；有问题 → 返回 Stage 2 修正

---

### 回退机制

```
用户点击已锁定的 Section 2 → 弹出:

  ⚠️ 解锁 Section 2 将影响：

  依赖此章的章节：
  - Section 4 引用了 Section 2 中的 3 篇文献
  - Section 5 的论证建立在 Section 2 的空白分析上

  建议：
  [仅解锁此章] — Section 4, 5 标记为"需复核"
  [解锁此章及后续全部] — Section 2-8 全部重新协商
  [取消]
```

---

## 5. 护栏系统：大纲契约

### 概述

护栏系统是一个双层结构：
- **Layer 1（硬护栏）：** 从 guardrails.yaml 加载，写入时硬拦截
- **Layer 2（软护栏）：** 注入 LLM prompt，引导生成行为

### 护栏即大纲

大纲协商完成后，输出是一份 `guardrails.yaml` 文件，同时具备两个功能：
1. **对 LLM 的写作约束** — 每次调用都作为 prompt 的一部分
2. **对系统的写入拦截** — save_project_file 时验证

### guardrails.yaml 规范

#### 完整 YAML 结构

```yaml
schema_version: 1

# ── 模板元信息 ──
template:
  id: "hithesis-harbin-bachelor-opening"
  name: "HIT 哈尔滨本科开题报告"
  source: "built-in"  # built-in | user-upload | llm-generated

# ── 全局不可变区（硬约束） ──
immutable_zones:
  - id: documentclass
    description: "\\documentclass 行及所有选项"
    detection: "regex:\\\\documentclass\\[.*?\\].*"

  - id: preamble
    description: "\\begin{document} 之前的所有导言区"
    detection: "preamble"

  - id: frontmatter
    description: "封面、makecover、目录"
    detection: "frontmatter"

  - id: bibliography_tail
    description: "参考文献区（\\bibliographystyle + \\bibliography）"
    detection: "regex:\\\\bibliographystyle.*|\\\\bibliography.*"

  - id: end_document
    description: "\\end{document}"
    detection: "regex:\\\\end\\{document\\}"

# ── 引用系统约束 ──
citation:
  style: "hithesis"
  command: "\\cite{}"
  bib_files:
    - "reference.bib"
  require_approval: true        # 是否强制引用审查
  min_strength: 2               # 最低支撑强度要求（1-4）

# ── 章节定义 ──
sections:
  - id: background_purpose
    sort_order: 1
    title: "课题来源及研究的目的和意义"
    heading: "\\section"
    file: "sections/课题来源及研究的目的和意义.tex"
    negotiation: full            # full | light | skip
    title_immutable: true
    allow_subsections: true
    allow_subsubsections: false
    min_paragraphs: 3
    citation_required: false
    suggested_order: 5           # 在推荐写作顺序中的位置
    writing_guide: |
      说明课题的工程或科学来源，阐述研究背景与现状差距，
      明确研究目的，论述研究的理论意义和工程应用价值。
    required_elements:
      - "课题来源（具体项目或问题背景）"
      - "研究背景（当前现状与不足）"
      - "研究目的（要解决什么问题）"
      - "研究意义（理论价值+工程应用价值）"

  - id: literature_review
    sort_order: 2
    title: "国内外在该方向的研究现状及分析"
    heading: "\\section"
    file: "sections/国内外在该方向的研究现状及分析.tex"
    negotiation: full
    title_immutable: true
    allow_subsections: true
    allow_subsubsections: false
    min_paragraphs: 4
    citation_required: true
    suggested_order: 3
    writing_guide: |
      按研究方向分子节，每节综述代表性文献，比较方法优劣，
      最后归纳研究空白。每段至少 1 个引用。
    subsection_strategies:       # 协商阶段的子结构选项
      - id: by_tech_stream
        label: "技术流派型"
        description: "按不同技术路线分节"
      - id: by_timeline
        label: "时间演进型"
        description: "按发展历程分节"
      - id: by_problem
        label: "问题驱动型"
        description: "按核心挑战分节"

  - id: main_research
    sort_order: 3
    title: "主要研究内容"
    heading: "\\section"
    file: "sections/主要研究内容.tex"
    negotiation: full
    title_immutable: true
    allow_subsections: true
    allow_subsubsections: false
    min_paragraphs: 3
    citation_required: false
    suggested_order: 1
    writing_guide: |
      分条列出具体研究内容，每条说明研究什么、用什么方法、预期产出什么。
    requires_figures: false
    requires_experimental_data: false

  - id: research_plan
    sort_order: 4
    title: "研究方案"
    heading: "\\section"
    file: "sections/研究方案.tex"
    negotiation: full
    title_immutable: true
    allow_subsections: true
    allow_subsubsections: true
    min_paragraphs: 4
    citation_required: false
    suggested_order: 2
    writing_guide: |
      说明技术路线、实验设计、数据来源、评估指标。
    requires_figures: true       # 引导用户上传技术路线图
    requires_experimental_data: true

  - id: schedule_targets
    sort_order: 5
    title: "进度安排与预期达到的目标"
    heading: "\\section"
    file: "sections/进度安排_预期达到的目标.tex"
    negotiation: light
    title_immutable: true
    allow_subsections: true
    allow_subsubsections: false
    min_paragraphs: 1
    citation_required: false
    suggested_order: 7
    writing_guide: |
      按时间阶段列出里程碑和交付物，建议用 itemize 列表。

  - id: conditions_funding
    sort_order: 6
    title: "课题已具备和所需的条件与经费"
    heading: "\\section"
    file: "sections/课题已具备和所需的条件_经费.tex"
    negotiation: light
    title_immutable: true
    allow_subsections: false
    allow_subsubsections: false
    min_paragraphs: 2
    citation_required: false
    suggested_order: 6
    writing_guide: |
      说明现有实验条件、数据基础、经费来源与预算。

  - id: difficulties_solutions
    sort_order: 7
    title: "研究过程中可能遇到的困难和问题及解决的措施"
    heading: "\\section"
    file: "sections/研究过程中可能遇到的困难和问题_解决的措施.tex"
    negotiation: full
    title_immutable: true
    allow_subsections: false
    allow_subsubsections: false
    min_paragraphs: 2
    citation_required: false
    suggested_order: 4
    writing_guide: |
      预估技术风险、数据风险、进度风险，逐条给出应对措施。

  - id: references
    sort_order: 8
    title: "主要参考文献"
    heading: "\\section"
    file: "sections/主要参考文献.tex"
    negotiation: skip
    title_immutable: true
    content_managed_by: "system"  # 系统自动管理
    writing_guide: "由 .bib 文件自动生成，无需 LLM 填充。"
```

### 护栏验证机制

写入时执行硬拦截（`template_guardrails.py`）：

```python
def validate_against_guardrails(
    new_content: str,
    existing_content: str,
    guardrails: dict,
    section_id: str,
) -> ValidationResult:
    """
    1. 提取 immutable_zones 的原文
    2. 对比 LLM 输出中的对应区域
    3. 检查 section 标题是否被修改
    4. 检查是否有未知的新 \section
    5. 返回通过/违规列表
    """
```

**违规时行为：裁剪违规内容，保留合法部分，返回警告。**

前端收到警告后的展示：

```
⚠️ 生成内容已被部分裁剪

  以下修改因违反护栏被移除：
  ✕ 修改了 section 标题：「Introduction」→「引言与背景」（标题不可变）
  ✕ 新增了未定义的 \\section{实验设置}（不在 guardrails.yaml 中）
  ✕ 修改了 \\bibliographystyle{hithesis} → \\bibliographystyle{ieeetran}

  已保留的合法内容已写入。
  [查看完整 diff] [忽略警告继续]
```

---

### 内置模板覆盖范围

需要预置 guardrails.yaml 的 hithesis 模板：

| 校区 | 学位 | 类型 | 文件 |
|------|------|------|------|
| harbin | bachelor | 开题 | `hithesis-harbin-bachelor-opening.yaml` |
| harbin | bachelor | 中期 | `hithesis-harbin-bachelor-midterm.yaml` |
| harbin | bachelor | 中文论文 | `hithesis-harbin-bachelor-cn.yaml` |
| harbin | bachelor | 英文论文 | `hithesis-harbin-bachelor-en.yaml` |
| harbin | master | 中文论文 | `hithesis-harbin-master-cn.yaml` |
| harbin | doctor | 中文论文 | `hithesis-harbin-doctor-cn.yaml` |
| harbin | doctor | 英文论文 | `hithesis-harbin-doctor-en.yaml` |
| harbin | postdoc | 中文 | `hithesis-harbin-postdoc-cn.yaml` |
| weihai | bachelor | 开题 | `hithesis-weihai-bachelor-opening.yaml` |
| weihai | bachelor | 中期 | `hithesis-weihai-bachelor-midterm.yaml` |
| weihai | bachelor | 中文 | `hithesis-weihai-bachelor-cn.yaml` |
| weihai | bachelor | 英文 | `hithesis-weihai-bachelor-en.yaml` |
| weihai | master | 中文 | `hithesis-weihai-master-cn.yaml` |
| weihai | doctor | 中文 | `hithesis-weihai-doctor-cn.yaml` |
| shenzhen | bachelor | 开题 | `hithesis-shenzhen-bachelor-opening.yaml` |
| shenzhen | bachelor | 中文 | `hithesis-shenzhen-bachelor-cn.yaml` |
| shenzhen | master | 中文 | `hithesis-shenzhen-master-cn.yaml` |
| shenzhen | master | 英文 | `hithesis-shenzhen-master-en.yaml` |
| shenzhen | doctor | 开题 | `hithesis-shenzhen-doctor-opening.yaml` |
| shenzhen | doctor | 中期 | `hithesis-shenzhen-doctor-midterm.yaml` |
| shenzhen | doctor | 中文 | `hithesis-shenzhen-doctor-cn.yaml` |
| shenzhen | doctor | 英文 | `hithesis-shenzhen-doctor-en.yaml` |

约 24 个 YAML 文件。每个文件基于与之对应的 LaTeX 模板提取实际的 `\section` / `\chapter` 标题。

### 用户上传模板（Phase 2）

用户上传 AAAI / IEEE / 自定义模板时：

1. **LLM 分析模板结构** → 输出 draft `guardrails.yaml`
2. **用户审核确认** → 调整 negotiation 策略、writing_guide 等
3. **契约锁定** → 存入 `project_files/memory/guardrails.yaml`
4. **后续流程与内置模板完全相同**

LLM 分析模板的 Prompt 模板：

```
你是一个学术论文模板分析器。分析以下 LaTeX 模板，生成护栏配置 YAML。

规则：
1. 识别所有 \\section/\\chapter/\\subsection 标题 → 每个顶级标题成为一个 section
2. 标题文字原样保留（title_immutable: true）
3. 判断每个 section 的 negotiation 级别：
   - 文献综述/方法/结果类 → full
   - 进度安排/经费/致谢类 → light
   - 参考文献 → skip
4. 为每个 section 写 writing_guide（1-2句中文指导）
5. \\begin{document} 之前 → immutable_zones
6. 参考文献/附录区 → immutable_zones
7. 输出严格合法的 YAML，不要额外文字
8. 如果检测到 hithesis 系列模板，识别校区、学位、阶段
```

---

## 6. 引用审查系统：Citation Gate

### 核心原则

> 每一个 `\cite{}` 都是用户确认过的。LLM 不得私自插入引用。

### 引用支撑强度分级

| 等级 | 符号 | 含义 | 判断标准 |
|------|------|------|---------|
| 4 | ★★★★ | 直接证据 | 文献的实验/结论直接支持该论点 |
| 3 | ★★★☆ | 部分支撑 | 文献涉及该问题但非核心论证 |
| 2 | ★★☆☆ | 背景参考 | 文献提供了相关背景或类似思路 |
| 1 | ★☆☆☆ | 弱相关 | 勉强搭边 |

**硬性规则：低于 ★★☆（等级 2）的文献不可引用。**

### 引用工作流

```
LLM 生成正文，遇到需要引用的论点
        │
        ▼
插入 [待引用:N] 占位符，暂停正文生成
        │
        ▼
弹出引用审查卡片（批量展示，一个论点可能对应多篇文献）
        │
        ├─ 用户批准 → 占位符替换为 \\cite{key1,key2,...}
        ├─ 用户拒绝某篇 → 移除该文献，保留其他
        ├─ 用户要求重搜 → LLM 扩大搜索，返回新候选
        ├─ 用户跳过 → 标记为 [待补充引用]，事后手动添加
        └─ 用户修改论点 → 回到编辑，重新表述
```

### 批量审核

一个章节写作时，LLM 可以一次提交本章所有待引用的论点：

```
┌─────────────────────────────────────────────────┐
│  📎 本章引用审核 — Section 2（共 4 处待引用）  │
│                                                 │
│  论点 1: CNN在裂缝检测中准确率达85-95%              │
│  候选: Gopalakrishnan 2018 ★★★★ 直接实证          │
│        Zhang 2021         ★★★☆ 部分支撑           │
│  [批准全部] [移除 Zhang] [搜索更多] [跳过]         │
│                                                 │
│  论点 2: 知识蒸馏可压缩参数量至30%以下              │
│  候选: Hinton 2015 ★★★★ 经典论文                  │
│        Gou 2021    ★★★☆ 综述佐证                  │
│  [批准全部] [仅用 Hinton] [搜索更多] [跳过]        │
│                                                 │
│  论点 3: 轻量化模型在Jetson Nano上可达实时推理       │
│  候选: Chen 2022  ★★☆☆ 实验中提到但非重点          │
│  ⚠️ 未找到 ★★★ 或 ★★★★ 级证据                    │
│  [勉强引用 Chen] [放宽搜索] [修改论点] [跳过]      │
│                                                 │
│  论点 4: 多尺度特征融合提升小目标检测性能            │
│  候选: Liu 2019  ★★★★ FPN原始论文                 │
│        Lin 2020  ★★★☆ 在路面场景验证              │
│  [批准全部] [仅用 Lin] [搜索更多] [跳过]           │
│                                                 │
│  [全部批准] [逐个审核] [返回修改]                  │
└─────────────────────────────────────────────────┘
```

### 跨章节引用提示

先写 Section 4 时引用了某文献，后来写 Section 2 时：

```
⚠️ 跨章引用提示

  Section 4（研究方案）已引用以下文献：
  - Hinton 2015（知识蒸馏）
  - Chen 2022（边缘部署性能）

  Section 2（文献综述）应自然引出这些文献，
  避免读者在 Section 4 看到"突如其来"的引用。

  建议：在 Section 2 的文献综述中加入这些文献的讨论，
  为 Section 4 做铺垫。

  [自动将文献加入候选] [手动处理] [忽略]
```

### 引用编号管理

由于写作顺序可能与模板顺序不同，系统管理引用编号：

- 编号以模板定义的 sort_order 为准，不以写作顺序为准
- 编译时由 LaTeX/BibTeX 自动处理
- 跨章引用时，系统自动确保 bib key 一致

---

## 7. 前端设计

### 整体布局

```
┌──────────────────────────────────────────────────────────┐
│  Topbar                                                   │
│  [项目选择 ▼] [编译 PDF] [PDF链接] [设置 ⚙]               │
├────────────┬────────────────────────────┬─────────────────┤
│ 章节卡片列  │      写作/协商区            │  右侧面板        │
│ (280px)    │      (自适应)              │  (可折叠 320px) │
│            │                           │                │
│ STAGE 1    │  ┌─────────────────────┐  │  文献证据        │
│ ┌────────┐ │  │ 协商 / 写作 / 锁定   │  │  ├─ 本章相关     │
│ │🔍勘探  │ │  │ (阶段指示器)         │  │  ├─ 已引用       │
│ └────────┘ │  │                     │  │  └─ 待审核       │
│            │  │ [对话/编辑区域]       │  │                │
│ STAGE 2    │  │                     │  │  护栏状态        │
│ ┌─ 1 ✅ ─┐ │  │                     │  │  ├─ 标题检查     │
│ │课题来源 │ │  │                     │  │  ├─ 引用完整性   │
│ └────────┘ │  │                     │  │  └─ 编译状态     │
│ ┌─ 2 🔵 ─┐ │  │                     │  │                │
│ │研究现状 │ │  │                     │  │                │
│ └────────┘ │  │                     │  │                │
│ ┌─ 3 ⬜ ─┐ │  │                     │  │                │
│ │研究内容 │ │  └─────────────────────┘  │                │
│ └────────┘ │                           │                │
│ ┌─ 4 ⬜ ─┐ │  ┌─────────────────────┐  │                │
│ │研究方案 │ │  │ 📄 实时预览/编译日志  │  │                │
│ └────────┘ │  │ (底部可折叠)         │  │                │
│ ┌─ 5 ⬜ ─┐ │  └─────────────────────┘  │                │
│ └─ ...    │                           │                │
│            │                           │                │
├────────────┴────────────────────────────┴─────────────────┤
│  Status Bar                                                │
│  ✅ 已锁定 2/8 | ⚠️ 1 待复核 | 📎 3 引用待审核              │
└──────────────────────────────────────────────────────────┘
```

### 章节卡片状态

```
⬜ 待处理     灰底，仅显示标题
🔵 协商中     蓝边，显示协商焦点
📝 写作中     绿边呼吸动画，显示生成进度
✅ 已锁定     绿底勾，显示 2 行摘要（可点击展开完整内容）
🔓 解锁中     黄边，显示依赖影响
⚠️ 需复核     橙边，前置章节被修改
```

### 卡片交互

- **点击未锁定卡片：** 切换到该章的写作/协商区
- **点击已锁定卡片：** 展开只读预览 + [解锁] 按钮
- **拖拽：** 在 STAGE 2 区域内可拖拽调整写作顺序（sort_order 不变，仅写作顺序变）

### STAGE 1 卡片

特殊卡片，固定在章节列表顶部。完成后折叠为一行摘要：

```
✅ STAGE 1 完成 — 选题：面向移动巡检的病害检测 | 证据充分度 ★★★☆
```

### 写作区模式

写作区根据当前阶段切换模式：

**协商模式：**
```
┌─────────────────────────────────────────┐
│  Section 2: 国内外研究现状    [协商中 🔵] │
│                                         │
│  ┌─ LLM 消息 ──────────────────────────┐│
│  │ 基于你的文献库，我建议按三个技术     ││
│  │ 流派组织这一章...                    ││
│  └────────────────────────────────────┘│
│                                         │
│  ┌─ 选项 ────────────────────────┐      │
│  │ ○ A. 技术流派型              │      │
│  │ ○ B. 时间演进型              │      │
│  │ ○ C. 问题驱动型              │      │
│  │                              │      │
│  │ [确认选择]                   │      │
│  └──────────────────────────────┘      │
│                                         │
│  ┌─ 用户输入 ──────────────────────────┐│
│  │ [输入你的想法...]              [发送]││
│  └────────────────────────────────────┘│
└─────────────────────────────────────────┘
```

**写作模式：**
```
┌─────────────────────────────────────────┐
│  Section 2: 国内外研究现状    [写作中 📝] │
│                                         │
│  ┌─ 源码编辑器 ────────────────────────┐│
│  │ \subsection{基于CNN的裂缝检测方法}    ││
│  │                                     ││
│  │ 卷积神经网络在路面裂缝检测中...       ││
│  │                                     ││
│  │ [待引用:1] 多个研究表明CNN在纹理     ││
│  │ 分类任务中表现优异 [待引用:2]...      ││
│  │                                     ││
│  └─────────────────────────────────────┘│
│                                         │
│  ┌─ 引用审核（内联）────────────────────┐│
│  │ 📎 [待引用:1] CNN裂缝检测准确率      ││
│  │                                         ││
│  │ Gopalakrishnan 2018 ★★★★ [✓]           ││
│  │ Zhang 2021         ★★★☆ [✓]           ││
│  │                                         ││
│  │ [批准选中的引用] [搜索更多]             ││
│  └─────────────────────────────────────┘│
│                                         │
│  [保存草稿] [请求 AI 续写] [编译预览]     │
└─────────────────────────────────────────┘
```

### 实验图表引导

当写作推进到 `requires_figures: true` 的章节时：

```
┌─────────────────────────────────────────┐
│  📊 本章需要实验图表                       │
│                                         │
│  当前检测到 0 张已上传图片                 │
│                                         │
│  ┌──────────┐ ┌──────────┐             │
│  │ 📁 拖拽   │ │ 📁 拖拽   │             │
│  │ 技术路线图 │ │ 实验结果图 │             │
│  │          │ │          │             │
│  │ (空)     │ │ (空)     │             │
│  └──────────┘ └──────────┘             │
│                                         │
│  图片标签: [fig:technical-route]        │
│  图片说明: [系统总体技术路线]             │
│                                         │
│  LLM 将自动生成：                        │
│  \\begin{figure}[htbp]                  │
│    \\includegraphics{figures/...}       │
│    \\caption{系统总体技术路线}            │
│    \\label{fig:technical-route}         │
│  \\end{figure}                          │
│                                         │
│  [上传图片] [先用占位符] [跳过，纯文字]     │
└─────────────────────────────────────────┘
```

### 配色与视觉

- **主色调：** 学术蓝（#2563EB）+ 中性灰
- **卡片状态色：** 灰(#E5E7EB) / 蓝(#DBEAFE) / 绿(#D1FAE5) / 黄(#FEF3C7) / 橙(#FED7AA)
- **引用强度色：** ★★★★ 绿 / ★★★☆ 蓝 / ★★☆☆ 黄 / ★☆☆☆ 红
- **字体：** 系统等宽字体用于代码编辑器，系统 sans-serif 用于 UI
- **动画：** 写作中卡片呼吸灯效果，引用审核滑入动画
- **暗色模式：** 支持（复用现有 styles.css 变量体系）

---

## 8. 后端模块设计

### 新增模块

#### `template_guardrails.py` — 护栏系统

```python
# 核心 API

def load_guardrails(project_id: str) -> dict:
    """加载项目的 guardrails.yaml，优先读 memory/ 中的用户确认版，
       否则从 configs/guardrails/ 加载内置版。"""

def validate_content(
    new_content: str,
    existing_content: str,
    guardrails: dict,
    section_id: str | None,
) -> ValidationResult:
    """验证新内容是否违反护栏。"""

def strip_illegal_content(
    new_content: str,
    existing_content: str,
    guardrails: dict,
) -> tuple[str, list[Violation]]:
    """裁剪违规内容，返回(合法内容, 违规列表)。"""

def build_guardrails_prompt(
    guardrails: dict,
    section_id: str,
    writing_order: list[str],
    locked_section_summaries: dict[str, str],
) -> str:
    """生成 LLM 系统提示中的护栏部分。"""

def generate_guardrails_from_template(
    template_content: str,
    template_id: str,
    api_key: str,
) -> dict:
    """Phase 2: LLM 分析用户上传的模板，生成 guardrails.yaml 草案。"""
```

#### `citation_gate.py` — 引用审查系统

```python
# 核心 API

@dataclass
class CitationCandidate:
    bib_key: str
    title: str
    authors: str
    year: int
    strength: int       # 1-4
    strength_reason: str
    abstract_snippet: str

def detect_citation_need(
    paragraph: str,
    section_id: str,
) -> list[CitationPoint]:
    """LLM 分析段落，识别需要引用的论点。"""

def search_candidates(
    claim: str,
    library_evidence: list[dict],
    min_strength: int = 2,
) -> list[CitationCandidate]:
    """在文献库中搜索支撑该论点的候选文献。"""

def rate_citation_strength(
    claim: str,
    candidate: dict,
) -> tuple[int, str]:
    """LLM 评估文献对该论点的支撑强度。"""

def apply_citations(
    content: str,
    citation_decisions: dict[str, list[str]],
) -> str:
    """将用户批准的引用替换 [待引用:N] 占位符。"""

def check_cross_chapter_citations(
    current_section: str,
    locked_sections: dict[str, str],
) -> list[CrossChapterHint]:
    """检测跨章节引用，提示需要铺垫。"""
```

#### `writing_workflow.py` — 工作流状态机

```python
# 核心 API

class WorkflowStage(enum.Enum):
    EXPLORATION = "exploration"     # STAGE 1
    OUTLINE_NEGOTIATION = "outline" # STAGE 2a
    ORDER_SELECTION = "ordering"    # STAGE 2b
    CHAPTER_WRITING = "writing"     # STAGE 2c
    FINAL_REVIEW = "review"         # STAGE 3
    COMPLETE = "complete"

class ChapterState(enum.Enum):
    PENDING = "pending"
    NEGOTIATING = "negotiating"
    WRITING = "writing"
    LOCKED = "locked"
    UNLOCKED = "unlocked"
    NEEDS_REVIEW = "needs_review"

def get_workflow_state(project_id: str) -> WorkflowState:
    """获取当前工作流状态。"""

def advance_workflow(project_id: str, new_stage: WorkflowStage) -> WorkflowState:
    """推进工作流到下一阶段。"""

def get_exploration_report(
    topic: str,
    project_id: str,
    api_key: str,
) -> dict:
    """STAGE 1: 生成文献勘探报告 + 选题建议。"""

def start_outline_negotiation(project_id: str) -> dict:
    """STAGE 2a: 开始大纲协商，返回第一个待协商的章节。"""

def negotiate_section(
    project_id: str,
    section_id: str,
    user_choice: str,
) -> dict:
    """处理用户对某章协商的选择，返回确认后的章节策略。"""

def recommend_writing_order(
    project_id: str,
    topic_type: str,
) -> list[str]:
    """STAGE 2b: 推荐写作顺序。"""

def set_writing_order(
    project_id: str,
    ordered_section_ids: list[str],
) -> None:
    """设置用户选择的写作顺序。"""

def start_chapter_writing(
    project_id: str,
    section_id: str,
) -> dict:
    """STAGE 2c: 开始某章的写作，返回压缩后的上下文。"""

def lock_chapter(project_id: str, section_id: str) -> dict:
    """锁定某章，触发编译，返回依赖影响。"""

def unlock_chapter(
    project_id: str,
    section_id: str,
    cascade: bool,
) -> dict:
    """解锁某章，返回影响范围。"""

def compress_context(
    project_id: str,
    current_section_id: str,
) -> str:
    """为当前章生成压缩后的上下文。"""

def run_final_review(project_id: str) -> dict:
    """STAGE 3: 终审检查。"""
```

### 修改现有模块

#### `writing_workspace.py`

- `save_project_file()`: 集成 `validate_content()` + `strip_illegal_content()`
- `create_project()`: 如果使用内置模板，从 `configs/guardrails/` 复制对应 YAML 到项目 memory
- 新增 `save_project_file_for_section()`: 按 section_id 写入，自动做护栏验证

#### `writing_audit.py`

- Mode A/B: 集成 guardrails.yaml 验证
- 新增 Mode G: "Guardrail Compliance" — 专门检查护栏遵守情况

#### `server.py`

- 新增 SSE 端点：工作流状态推送、写作进度推送、引用审核推送
- 新增 REST 端点：工作流操作（勘探/协商/锁定/解锁/终审）

---

## 9. 文件结构

```
项目根目录/
│
├── configs/
│   └── guardrails/                    # 内置模板护栏配置
│       ├── hithesis-harbin-bachelor-opening.yaml
│       ├── hithesis-harbin-bachelor-midterm.yaml
│       ├── hithesis-harbin-bachelor-cn.yaml
│       ├── hithesis-harbin-bachelor-en.yaml
│       ├── hithesis-harbin-master-cn.yaml
│       ├── hithesis-harbin-doctor-cn.yaml
│       ├── ...（约24个文件）
│       └── _schema.yaml               # guardrails.yaml 的 JSON Schema
│
├── src/literature_agent/
│   ├── template_guardrails.py         # NEW: 护栏加载、验证、裁剪
│   ├── citation_gate.py               # NEW: 引用审查、支撑强度、批量审核
│   ├── writing_workflow.py            # NEW: 三阶段工作流状态机
│   ├── writing_workspace.py           # MODIFY: 集成护栏验证 + section级写入
│   ├── writing_audit.py               # MODIFY: Mode A/B 集成 guardrails, +Mode G
│   ├── template_profile.py            # MODIFY: 集成 guardrails YAML
│   ├── template_library.py            # 基本不变
│   └── server.py                      # MODIFY: 新增工作流/引用审查端点
│
├── apps/web/
│   ├── writing.html                   # REWRITE: 卡片式三阶段 UI
│   ├── writing.js                     # REWRITE: 工作流状态管理 + SSE
│   ├── writing-section.html           # DEPRECATE: 合并到 writing.html
│   ├── writing-section.js             # DEPRECATE: 合并到 writing.js
│   └── styles.css                     # MODIFY: 新增卡片/引用审核/护栏状态样式
│
├── tests/
│   ├── test_template_guardrails.py    # NEW
│   ├── test_citation_gate.py          # NEW
│   └── test_writing_workflow.py       # NEW
│
└── docs/
    └── writing-studio-spec.md         # 本文档
```

---

## 10. 开发路线

### Phase A: 基础设施（护栏系统）

- [ ] A1: 创建 `template_guardrails.py` — YAML 加载/验证/裁剪核心逻辑
- [ ] A2: 为所有 24 个 hithesis 子模板编写 YAML 护栏文件
- [ ] A3: 编写单元测试（加载、验证、裁剪、违规检测）
- [ ] A4: 集成到 `save_project_file()` — 写入时硬拦截

### Phase B: 引用审查

- [ ] B1: 创建 `citation_gate.py` — 引用检测/候选搜索/强度评估
- [ ] B2: 实现批量审核逻辑 + 跨章节引用提示
- [ ] B3: 实现 [待引用:N] 占位符解析与替换
- [ ] B4: 编写单元测试

### Phase C: 工作流引擎

- [ ] C1: 创建 `writing_workflow.py` — 状态机 + 三阶段控制
- [ ] C2: 实现 STAGE 1 勘探报告生成
- [ ] C3: 实现 STAGE 2a 大纲逐章协商
- [ ] C4: 实现 STAGE 2b 写作顺序推荐
- [ ] C5: 实现 STAGE 2c 逐章写作循环（含上下文压缩）
- [ ] C6: 实现 STAGE 3 终审检查
- [ ] C7: 实现回退/解锁/依赖追踪
- [ ] C8: 实现实验图表引导逻辑

### Phase D: 前端重构

- [ ] D1: 重写 `writing.html` — 三栏布局（卡片列+写作区+面板）
- [ ] D2: 实现章节卡片组件（6种状态 + 拖拽排序）
- [ ] D3: 实现协商模式 UI（LLM 消息 + 选项卡片 + 用户输入）
- [ ] D4: 实现写作模式 UI（源码编辑 + 内联引用审核）
- [ ] D5: 实现引用审核卡片（批量/单条 + 支撑强度可视化）
- [ ] D6: 实现实验图表上传区域
- [ ] D7: 实现编译日志 + PDF 预览面板
- [ ] D8: SSE 连接，实时状态推送

### Phase E: 用户上传模板支持

- [ ] E1: 实现 `generate_guardrails_from_template()` — LLM 分析模板
- [ ] E2: 前端上传流程 + 契约审核 UI
- [ ] E3: 用户编辑 guardrails.yaml（大纲调整）

### Phase F: 集成测试与打磨

- [ ] F1: 端到端测试（从选题到终审的完整流程）
- [ ] F2: 暗色模式适配
- [ ] F3: 移动端响应式
- [ ] F4: 性能优化（大文件编辑、大量文献）

---

## 附录：LLM Prompt 设计原则

### 协商阶段的 Prompt 结构

```
你是学术写作助手。当前处于大纲协商阶段。

## 模板约束
{guardrails.yaml 中当前 section 的定义}

## 已确认的选题
{topic_summary}

## 文献库证据
{relevant_evidence_summary}

## 已确认的章节策略
{locked_section_strategies}

## 任务
为「{section_title}」提出 2-3 个具体的写作策略选项。
每个选项包括：子结构（subsection）、核心论点、预期引用的文献。
引导用户做出选择。不要直接生成正文。
```

### 写作阶段的 Prompt 结构

```
你是学术写作助手。当前处于逐章写作阶段。

## 硬性约束
- 不得修改 \\section{...} 标题文字
- 不得修改导言区、封面、参考文献尾区
- 每个引用使用 [待引用:N] 占位符，等待用户审核
- 仅可修改 {section_title} 的正文内容
- 可在正文内使用 \\subsection 和 \\subsubsection

## 本章策略（用户已确认）
{negotiation_result}

## 前文章节摘要
{locked_section_summaries}

## 本章文献证据
{section_evidence}

## 任务
生成「{section_title}」的正文。
按照用户确认的策略 ({strategy}) 组织内容。
遇到需要引用的论点时，使用 [待引用:N] 占位符。
```

### 引用强度评估 Prompt

```
你是学术审稿人。评估以下文献对论点的支撑强度。

论点: {claim}

文献:
  Title: {title}
  Authors: {authors}
  Abstract: {abstract}

评分标准:
  ★★★★ (4): 文献实验/结论直接支持该论点
  ★★★☆ (3): 涉及该问题但非核心论证
  ★★☆☆ (2): 仅提供相关背景或类似思路
  ★☆☆☆ (1): 勉强搭边

输出 JSON: {"strength": <1-4>, "reason": "<一句话理由>"}
```
