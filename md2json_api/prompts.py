from __future__ import annotations

import json
import re
from typing import Any

from .models import ALLOWED_ENVS, MarkdownSection


PROMPT_PROFILES = ("auto", "textbook", "paper", "chinese_math")


COMMON_INTRO = """You extract mathematical Markdown from textbooks, lecture notes, and papers into structured JSON.

Follow the exact schema. The surrounding program handles section splitting, label normalization, and validation. Your job is semantic extraction of the supplied Markdown section.

Allowed env values:
{allowed_envs}

Output shape:
Return one JSON object with exactly one key, "items", whose value is an array of item objects. If there is no extractable mathematical item, return {{"items": []}}.
"""


FINAL_ITEM_FIELD_SPEC = """Field specification for every final item:

- index:
  1-based order of the item inside the current supplied section. Preserve source order.

- label:
  Use source-aware labels.
  If the source item has an explicit printed number or label, the label must preserve that source number in canonical English form: "<Type> <source_number>". Examples: "Theorem 6.1", "Definition 1.2.4", "Corollary 6.1.1", "Algorithm 1". This label must match number_components joined by "." and must not use the local extraction index.
  If the item is inferred from unnumbered prose or has no explicit printed source number, use a synthetic label that cannot be confused with source numbering: "<Type> <section_number>-extra-<local_synthetic_index>", for example "Definition 6-extra-1" or "Remark 4.2-extra-1". These synthetic items must not consume or shift explicit source numbers.

- env:
  Use only one allowed value. Map source labels conservatively:
  Definition/定义 -> def; Theorem/定理 -> thm; Proposition/命题 -> prop; Lemma/引理 -> lemma; Corollary/推论 -> cor; Example/例 -> example; Exercise/练习 -> exercise; Remark/注/注记 -> remark; Algorithm/算法 -> algorithm; Assumption/假设 -> assumption; Claim/断言 -> claim; Conjecture/猜想 -> conjecture; Problem/问题 -> problem; Question -> question; Notation/记号 -> notation.

- number_components:
  Preserve explicit source numbering when it exists. Examples: "Theorem 3." -> ["3"]; "Definition 1.2.4" -> ["1", "2", "4"]; "Theorem A.1" -> ["A", "1"]; unnumbered items -> [].

- context:
  Repeat exactly the supplied chapter, section, chapter_number, and section_number on every item.

- content:
  Preserve source text closely, including explicit source labels, Markdown tables, displayed formulas, punctuation, and LaTeX. Do not summarize, translate, or silently rewrite mathematical content. Keep "Theorem 2.1.", "定义 1.2.4", "Algorithm 1" and similar source labels in content.

- dependencies:
  Default to []. Fill only when the source explicitly names a dependency and the name is recoverable without guessing, such as "by Theorem 2.1" or "using Lemma 3.4". Use the same canonical source-aware labels as label, so an explicit dependency on "Theorem 6.1" stays "Theorem 6.1" rather than a synthetic local-index label. Do not infer hidden dependencies.
  Treat dependencies as source-internal mathematical item labels only. Do not put bibliography citations, bracketed references like "[12]", author-year references, book/paper titles, page references, or theorem numbers from other works into dependencies. Preserve external citations verbatim in content/proof.

- proof:
  Use null unless a proof block is explicitly marked. When a proof boundary is explicit, put the proof body in proof and keep the statement in content. Do not create standalone proof items.
"""


EXTRACTION_FIELD_SPEC = """Field specification for every extraction item:

- label:
  Use source-aware labels.
  If the source item has an explicit printed number or label, the label must preserve that source number in canonical English form: "<Type> <source_number>". Examples: "Theorem 6.1", "Definition 1.2.4", "Corollary 6.1.1", "Algorithm 1". This label must match number_components joined by "." and must not use the local extraction index.
  If the item is inferred from unnumbered prose or has no explicit printed source number, use a synthetic label that cannot be confused with source numbering: "<Type> <section_number>-extra-<local_synthetic_index>", for example "Definition 6-extra-1" or "Remark 4.2-extra-1". These synthetic items must not consume or shift explicit source numbers.
  The converter will validate labels later, but fill this field with the same source-aware rule so raw API traces are useful.

- env:
  Use only one allowed value. Map source labels conservatively:
  Definition/定义 -> def; Theorem/定理 -> thm; Proposition/命题 -> prop; Lemma/引理 -> lemma; Corollary/推论 -> cor; Example/例 -> example; Exercise/练习 -> exercise; Remark/注/注记 -> remark; Algorithm/算法 -> algorithm; Assumption/假设 -> assumption; Claim/断言 -> claim; Conjecture/猜想 -> conjecture; Problem/问题 -> problem; Question -> question; Notation/记号 -> notation.

- number_components:
  Preserve explicit source numbering when it exists. Examples: "Theorem 3." -> ["3"]; "Definition 1.2.4" -> ["1", "2", "4"]; "Theorem A.1" -> ["A", "1"]; unnumbered items -> [].

- dependencies:
  Default to []. Fill only when the source explicitly names a dependency and the name is recoverable without guessing, such as "by Theorem 2.1" or "using Lemma 3.4". Use the same canonical source-aware labels as label, so an explicit dependency on "Theorem 6.1" stays "Theorem 6.1" rather than a synthetic local-index label. Do not infer hidden dependencies.
  Treat dependencies as source-internal mathematical item labels only. Do not put bibliography citations, bracketed references like "[12]", author-year references, book/paper titles, page references, or theorem numbers from other works into dependencies.

- source_order_anchor:
  A short exact substring at or very near the item start. Use it to identify the item in source order.

- content_span:
  A literal source span object for the item statement/content. The local converter copies content from this span; do not output content text yourself.
  Use fields: start_anchor, end_anchor, start_occurrence, end_occurrence, include_start, include_end.
  The content span must contain the full statement/content and stop before the explicit proof marker or next item.

- proof_span:
  Null unless a proof block is explicitly marked. When a proof boundary is explicit, provide a literal source span object for the proof body only.
  The local converter resolves proof_span after content_span and copies proof from the Markdown; do not output proof text yourself.
"""


FIELD_SPEC = EXTRACTION_FIELD_SPEC


SPAN_INCLUSION_RULES = """Span inclusion rules:

- start_occurrence and end_occurrence are 1-based counts from the beginning of the supplied Markdown section.
- include_start/include_end control whether the full anchor string itself is copied into the field.
- If include_start is false, the copied text starts after the entire start_anchor. Therefore, do not put proof-body words or formulas inside a start_anchor that will be excluded.
- If include_end is false, the copied text stops before the entire end_anchor. Therefore, do not put statement/proof text that must be preserved inside an excluded end_anchor.
- For content_span, normally use include_start=true so the item label/opening statement is preserved. Use include_end=false when the end_anchor is a proof marker or the next item boundary.
- For proof_span, use include_start=false only when start_anchor is just the explicit proof marker to remove, such as "Proof.", "Proof of Lemma 2.", or "证明". If start_anchor includes the first actual proof-body words, set include_start=true.
- For proof_span, normally use include_end=false when the end_anchor is the next item boundary or following non-proof prose.
"""


COMMON_RULES = """Extraction rules:

- Stay strictly inside the supplied section/chunk. Do not pull material from neighboring sections.
- Extract only structurally meaningful mathematical items: definitions, theorems, propositions, lemmas, corollaries, algorithms, assumptions, claims, conjectures, examples, exercises, problems, questions, notation blocks, and remarks.
- Prefer omission over noisy extraction for ordinary exposition, motivation, paper introductions, abstracts, experimental discussion, table captions, and purely transitional prose.
- Include a section heading inside content only when the item is the central section-opening definition, notation block, or remark-like conceptual block.
- If a section has no explicit theorem-like marker but introduces one central mathematical notion, represent that opening block as one def, notation, or remark. Otherwise return no items.
- Split proof into proof only when the boundary is explicit, for example "Proof.", "Proof of Theorem 3.", "证明", "Construction.", or "Proof; uniqueness.".
- If a proof appears later and names an earlier result in the same supplied section, attach it to that earlier result rather than making a new item.
- If a standalone proof names a result outside this supplied section, preserve it as a remark item with the proof text in content and put the named result in dependencies when explicit.
- If the proof boundary is ambiguous, keep the text in content and set proof to null.
- Do not invent theorem numbers, dependencies, missing statements, proof text, or hidden structure.
- Keep one item per logical extracted block. Do not split one theorem into several items just because it has cases, equations, or paragraphs.
- In an Exercises/Problems subsection, split explicitly numbered exercises/problems into separate items. Do not merge a list such as "1. ... 2. ... 3. ..." into one exercise block; use labels like "Exercise 1", "Exercise 2", etc. when those numbers are printed in the source.
- Return valid JSON only. The response schema is enforced.
"""


FEW_SHOT_EXAMPLES = """Few-shot examples:

Example 1: theorem with explicit proof

Input section:
```md
### 1.3 Subrepresentations

Theorem 1. Let $\\rho: G \\to GL(V)$ be a linear representation of $G$ in $V$ and let $W$ be stable under $G$.

Proof. Let $W'$ be an arbitrary complement of $W$ in $V$.
```

Output:
```json
{
  "items": [
    {
      "label": "Theorem 1",
      "env": "thm",
      "number_components": ["1"],
      "dependencies": [],
      "source_order_anchor": "Theorem 1. Let $\\rho: G \\to GL(V)$",
      "content_span": {
        "start_anchor": "Theorem 1. Let $\\rho: G \\to GL(V)$",
        "end_anchor": "Proof.",
        "start_occurrence": 1,
        "end_occurrence": 1,
        "include_start": true,
        "include_end": false
      },
      "proof_span": {
        "start_anchor": "Proof.",
        "end_anchor": null,
        "start_occurrence": 1,
        "end_occurrence": 1,
        "include_start": false,
        "include_end": false
      }
    }
  ]
}
```

Example 2: opening prose plus exercise

Input section:
```md
### 5.7 The alternating group A4

This is the group of even permutations of four letters.

Exercise 5.4. Set $\\theta(1)=...$
```

Output:
```json
{
  "items": [
    {
      "label": "Definition 5.7-extra-1",
      "env": "def",
      "number_components": [],
      "dependencies": [],
      "source_order_anchor": "### 5.7 The alternating group A4",
      "content_span": {
        "start_anchor": "### 5.7 The alternating group A4",
        "end_anchor": "Exercise 5.4.",
        "start_occurrence": 1,
        "end_occurrence": 1,
        "include_start": true,
        "include_end": false
      },
      "proof_span": null
    },
    {
      "label": "Exercise 5.4",
      "env": "exercise",
      "number_components": ["5", "4"],
      "dependencies": [],
      "source_order_anchor": "Exercise 5.4.",
      "content_span": {
        "start_anchor": "Exercise 5.4.",
        "end_anchor": null,
        "start_occurrence": 1,
        "end_occurrence": 1,
        "include_start": true,
        "include_end": false
      },
      "proof_span": null
    }
  ]
}
```
"""


TEXTBOOK_PROFILE = """Active profile: textbook

Textbook-specific rules:
- Textbooks often use section-opening prose as a central concept introduction. Extract it as def, notation, or remark only when it introduces the central object of the section.
- Include examples and exercises when they are explicitly labeled.
- Do not turn every explanatory paragraph between theorems into a remark.
- When a theorem statement continues across paragraphs, keep the full statement together in one content field until a clear proof boundary or next item begins.
"""


PAPER_PROFILE = """Active profile: paper

Paper-specific rules:
- Do not extract abstract, introduction motivation, related work, contribution summaries, experimental discussion, table captions, acknowledgements, or references unless they contain explicitly labeled mathematical items.
- Extract formally labeled results such as Theorem, Lemma, Proposition, Corollary, Claim, Assumption, Algorithm, Problem, Conjecture, and Definition.
- Treat "Problem statement" or "Our contributions" section titles as section titles, not automatically as problem/remark items.
- Preserve algorithms as algorithm items when the source explicitly labels "Algorithm 1", "Algorithm 2", etc.
- For appendix sections named "Proof of Theorem X", preserve the proof text as a remark if the theorem statement is outside the supplied section, and put "Theorem X" in dependencies.
"""


CHINESE_MATH_PROFILE = """Active profile: chinese_math

Chinese mathematical text rules:
- Recognize Chinese item labels: 定义 -> def, 定义-定理 -> thm or def conservatively according to the statement role, 定理 -> thm, 命题 -> prop, 引理 -> lemma, 推论 -> cor, 例 -> example, 练习 -> exercise, 注/注记 -> remark, 算法 -> algorithm, 假设 -> assumption, 断言 -> claim, 猜想 -> conjecture, 问题 -> problem, 记号 -> notation.
- Recognize "证明" as an explicit proof boundary. Put the text after "证明" in proof when it proves the immediately preceding result or explicitly named current result.
- Keep Chinese punctuation and OCR text as close to source as possible. Do not translate Chinese prose into English.
- Preserve labels such as "定义 1.2.4", "命题 1.2.9", "定义-定理 1.3.1" in content.
- Do not treat "阅读提示" or connective commentary as a mathematical item unless it is the central opening remark of an inferred section.
"""


PROFILE_PROMPTS = {
    "textbook": TEXTBOOK_PROFILE,
    "paper": PAPER_PROFILE,
    "chinese_math": CHINESE_MATH_PROFILE,
}


AUDIT_RULES = """Section-level audit and repair rules:

You are a mathematical JSON audit and repair assistant. You receive exactly one source Markdown section and the current canonical JSON items generated for that section.

Task:
1. Read the source Markdown carefully.
2. Read the current JSON as the canonical base.
3. Find only clear, defensible problems.
4. Produce a repair pack and a repaired full item array.

Audit targets:
- missing item: an explicit definition/theorem/proposition/lemma/corollary/remark/example/exercise/algorithm/claim/etc. is absent.
- truncated item: content or proof visibly stops early or loses formulas/paragraphs.
- wrong env: source label clearly maps to a different env.
- proof should be split: explicit proof marker is left in content.
- proof should be merged: proof was split when the boundary is not explicit or belongs elsewhere.
- duplicate or spurious item: JSON has a repeated item or an item not supported by the source.
- wrong item boundary: one source item was split into fragments, or multiple source items were merged incorrectly.
- merged exercises/problems: an Exercises/Problems subsection has explicitly numbered entries, but the current JSON combines them into one block instead of one item per numbered entry.
- wrong ordering, numbering, or label style. Explicitly numbered source items must keep their source labels, while unnumbered inferred items must use synthetic "-extra-" labels.
- broken dependency: dependency is invented, malformed, or an explicit dependency is clearly missing.
- external reference dependency: bibliography citations, bracketed references like "[12]", author-year references, book/paper titles, page references, or theorem numbers from other works should not appear in dependencies.

Conservative repair principles:
- Preserve current JSON whenever it is defensible.
- Do not rewrite the whole section merely for style.
- Do not add ordinary explanatory paragraphs as remarks unless they are central definition/notation/opening blocks.
- Split proof only when the boundary is explicit.
- Do not invent dependencies.
- Do not treat external literature references as missing dependencies; if they appear in dependencies, remove them while preserving the citation in content/proof.
- If uncertain, keep repaired_items conservative and record the uncertainty in open_questions.

When source tools are available:
- You decide the source item inventory by reading the Markdown, just as in the first extraction/API-calls stage. Do not assume a fixed theorem-name regex or a fixed textbook style.
- Call list_source_item_labels with your own identified labels/items; that tool only records your decision and checks literal anchors.
- Use search_source and extract_source_span to locate exact source spans for repaired content/proof. The tools copy spans from the Markdown; they do not decide what counts as a theorem, definition, lemma, etc.
- Choose source anchors that are long enough to be unique. Prefer the complete visible item heading plus the opening words/formula for content spans. For proof spans, prefer the explicit proof marker plus the item label and distinctive nearby words/formulas rather than generic phrases.
- Make source_order_anchor the exact visible heading or opening sentence of the item itself, not a nearby note, preliminary remark, or proof marker.
- Proof spans in build_repaired_items are resolved after that item's content span. Content and proof spans must stay within the current item and must not cross the next item's source_order_anchor.
- In build_repaired_items, enumerate the complete final item array. Preserve unchanged current items by label, and use source spans for any new or modified item text.
- Never handwrite repaired content/proof text when a source span can be used.

Output requirements:
- audit_markdown must follow the ref-style report: section identifier/title, short verdict, current JSON summary, findings with issue type / affected labels / explanation / source excerpt / recommended action, then compact action summary.
- patch_candidate must contain only add/update/delete actions. Use no actions for "no change".
- For add actions, include an anchor when possible, provisional_label, env, reason, content_excerpt, and candidate_item when confident.
- For update actions, include target_label, reason, and field_updates_note.
- For delete actions, include target_label and reason.
- repaired_items must be the full final JSON array for this section after applying the high-confidence actions. If no change, repaired_items must equal the current JSON items except for harmless schema normalization.
"""


AUDIT_FEW_SHOT = """Audit/repair examples:

Example A: proof left in content

Source excerpt:
```md
Theorem 1. Every finite subgroup of $K^\\times$ is cyclic.

Proof. Let $G$ be a finite subgroup ...
```

Current JSON item:
```json
{
  "label": "Theorem 1",
  "env": "thm",
  "content": "Theorem 1. Every finite subgroup of $K^\\times$ is cyclic.\n\nProof. Let $G$ be a finite subgroup ...",
  "proof": null
}
```

Expected repair:
- finding: proof should be split
- patch action: update target_label "Theorem 1"
- repaired item content ends before "Proof."
- repaired item proof is "Let $G$ be a finite subgroup ..."

Example B: explicit source theorem missing

Source excerpt:
```md
Lemma 2.4. If $x \\in U_\\gamma$, then $x+y \\in U_\\gamma$ for all $y \\in U_\\gamma$.
```

Current JSON: []

Expected repair:
- finding: missing item
- patch action: add
- repaired_items contains one lemma item with number_components ["2", "4"].

Example C: no change

If the current JSON already preserves all explicit source items and proof boundaries, output verdict "no change", actions [], open_questions [], and repaired_items equal to the current JSON.
"""


def build_system_prompt(prompt_profile: str = "auto", section: MarkdownSection | None = None) -> str:
    resolved = resolve_prompt_profile(prompt_profile, section)
    return "\n\n".join(
        [
            COMMON_INTRO.format(allowed_envs=", ".join(ALLOWED_ENVS)),
            EXTRACTION_FIELD_SPEC,
            SPAN_INCLUSION_RULES,
            COMMON_RULES,
            FEW_SHOT_EXAMPLES,
            PROFILE_PROMPTS[resolved],
        ]
    )


def build_section_prompt(section: MarkdownSection, prompt_profile: str = "auto") -> str:
    ctx = section.context
    resolved = resolve_prompt_profile(prompt_profile, section)
    return f"""Convert this Markdown section to JSON items.

Prompt profile: {resolved}

Context to use when choosing source-aware labels:
- chapter: {ctx.chapter}
- chapter_number: {ctx.chapter_number}
- section: {ctx.section}
- section_number: {ctx.section_number}

Extraction discipline:
- Treat this as one source section/chunk produced by an external splitter.
- Prefer omission over noisy extraction for non-mathematical connective prose.
- Preserve source order.
- The converter will validate context and labels after parsing; still fill label with the source-aware rule because raw API traces should be meaningful.
- Output only item metadata and source spans. Do not output content or proof text.

Markdown section:

```md
{section.text}
```
    """


def build_audit_repair_system_prompt(prompt_profile: str = "auto", section: MarkdownSection | None = None) -> str:
    resolved = resolve_prompt_profile(prompt_profile, section)
    return "\n\n".join(
        [
            "You audit and repair structured JSON extracted from mathematical Markdown.",
            FINAL_ITEM_FIELD_SPEC,
            SPAN_INCLUSION_RULES,
            COMMON_RULES,
            AUDIT_RULES,
            AUDIT_FEW_SHOT,
            PROFILE_PROMPTS[resolved],
            "Return valid JSON only. The response schema is enforced.",
        ]
    )


def build_audit_repair_prompt(
    section: MarkdownSection,
    current_items: list[dict[str, Any]],
    prompt_profile: str = "auto",
    extraction_trace: dict[str, Any] | None = None,
) -> str:
    ctx = section.context
    resolved = resolve_prompt_profile(prompt_profile, section)
    current_json = json.dumps(current_items, ensure_ascii=False, indent=2)
    extraction_trace_block = ""
    if extraction_trace is not None:
        extraction_trace_json = json.dumps(extraction_trace, ensure_ascii=False, indent=2)
        extraction_trace_block = f"""
Initial Extraction Span Trace:

```json
{extraction_trace_json}
```

"""
    return f"""Audit and repair this one section.

Prompt profile: {resolved}

Section identifier:
- section_index: section{section.index:02d}
- chapter: {ctx.chapter}
- chapter_number: {ctx.chapter_number}
- section: {ctx.section}
- section_number: {ctx.section_number}
- source line range: {section.start_line}-{section.end_line}

Source Markdown:

```md
{section.text}
```

Current canonical JSON items:

```json
{current_json}
```

{extraction_trace_block}\
Produce:
1. audit_markdown
2. patch_candidate
3. repaired_items: the complete repaired JSON item array for this section
"""


STRUCTURE_SYSTEM_PROMPT = """You plan chapter and section structure for mathematical Markdown converted from PDFs.

Your only task is document structure. Do not extract theorem/example/proof items.

Output a JSON structure plan with chapter metadata, front/back matter ranges, and the canonical sections that should be used as JSON context.

Planning principles:
- Prefer semantic section boundaries over raw Markdown heading levels; PDF-to-Markdown often has unreliable heading levels.
- Identify chapter titles such as "Chapter 1", "第一章", paper titles, or the title of a section excerpt.
- Identify the canonical section level for extraction context. If a file is clearly one section excerpt, keep that top-level section as the section context and treat lower pedagogical headings as internal content unless they are truly independent main sections.
- Recognize bare numbered section titles such as "1.1 滤子" even when they are not Markdown headings.
- Do not classify item headings as sections: Definition/定义, Theorem/定理, Lemma/引理, Proposition/命题, Corollary/推论, Example/例, Exercise/练习, Try it, Investigate, Algorithm, Remark, Proof, and similar headings are item/activity headings inside a section unless they are also the chosen canonical section level.
- Recognize front matter such as objectives, reading guides, abstracts, chapter introductions, and tables of contents.
- Recognize back matter such as references, bibliography, acknowledgements, index, funding, and appendices only when they are not mathematical body sections. A heading like "A Proof of Theorem 2.7" is a mathematical appendix section, not references.
- Use exact 1-based line numbers from the candidate list. Do not invent line numbers.
- Section ranges must be non-overlapping and in source order. A section range should include its heading line and all body text through the line before the next canonical section or back matter.
- If uncertain, choose fewer, broader sections rather than fragmenting a section into many item-level headings.

Chapter and section metadata audit:
- Before producing the final JSON, audit the chapter, chapter_number, section_number, and section_title values for naming contamination.
- Treat tables of contents, reading guides, series title pages, comments/references sections, bibliography entries, and index entries as unreliable sources for canonical JSON naming unless the document itself is only that material.
- Do not inherit a Part heading as chapter_number merely because it appears before a section. Part I/II/VIII headings are often structural dividers inside a complete book; they should not become chapter_number for ordinary numbered book sections unless the source truly uses parts as the JSON chapter level.
- For a complete book organized directly into numbered sections such as "SECTION 1", "SECTION 2", ..., prefer a book-level chapter such as "Complete book" and an empty chapter_number, while keeping section_number as "1", "2", etc.
- If a complete book contains multiple explicit chapter headings, keep document_title as the book title and use top-level chapter/chapter_number only as fallback metadata. Each section entry must carry the true chapter and chapter_number active at that section, not "Complete book".
- For a single chapter excerpt organized into subsections, keep the true chapter number and use subsection numbers as section_number.
- For a paper, keep chapter_number empty unless there is an explicit chapter-like unit in the source.
- Do not prefix section_number with a stale chapter or part number. For example, do not turn section "13" into "VIII.13" or bibliography author "A. Brøndsted" into "VIII.A".
- section_title must name the canonical source section, not a table-of-contents entry, author name from a bibliography, running header, proof label, or theorem label.
- If hard splitter metadata conflicts with candidate headings or looks contaminated, repair it rather than copying it. Record important naming repairs or remaining uncertainty in warnings.
"""


STRUCTURE_METADATA_AUDIT_CHECKLIST = """Final metadata self-audit to perform before returning JSON:
1. Decide the intended JSON context level: complete book, one chapter, one section excerpt, paper, or appendix.
2. Check whether chapter/chapter_number came from a true chapter-like source heading, not from a Part divider, table of contents, comments/references, bibliography, index, or hard splitter fallback.
3. Check every section_number and section_title against the candidate heading that starts the section. Keep canonical book/paper numbering and remove stale chapter/part prefixes.
4. Check whether any lettered section is actually a bibliography author entry or index heading. If so, move it to back_matter_ranges instead of sections.
5. Check whether named back matter such as Comments and References, Bibliography, References, or Index is excluded from body sections unless it is the actual requested document body.
6. Put the repaired chapter/chapter_number and repaired section metadata directly in the normal output fields. Do not add extra JSON keys. If you repaired naming contamination or are uncertain, add a concise warning.
"""


def build_structure_prompt(
    *,
    source_name: str,
    source_line_count: int,
    hard_sections: list[MarkdownSection],
    hard_warnings: list[str],
    candidates: list[dict[str, Any]],
    prompt_profile: str = "auto",
) -> str:
    candidate_lines = []
    for candidate in candidates:
        candidate_lines.append(
            "line {line} [{kind}]: {text}\n"
            "  prev: {prev}\n"
            "  next: {next}".format(
                line=candidate.get("line", ""),
                kind=candidate.get("kind", ""),
                text=candidate.get("text", ""),
                prev=candidate.get("prev", ""),
                next=candidate.get("next", ""),
            )
        )
    hard_lines = []
    for section in hard_sections:
        hard_lines.append(
            f"- section{section.index:02d}: {section.context.section_number} | "
            f"{section.context.section} | lines {section.start_line}-{section.end_line} | "
            f"source_heading={section.source_heading!r}"
        )
    return f"""Create the canonical chapter/section structure plan for this Markdown document.

Source file: {source_name}
Total source lines: {source_line_count}
Prompt profile: {prompt_profile}

Hard splitter draft sections:
{chr(10).join(hard_lines) if hard_lines else "(none)"}

Hard splitter warnings:
{chr(10).join("- " + warning for warning in hard_warnings) if hard_warnings else "(none)"}

Candidate headings/items with neighboring context:
{chr(10).join(candidate_lines) if candidate_lines else "(none)"}

{STRUCTURE_METADATA_AUDIT_CHECKLIST}

Return:
1. document_title
2. chapter and chapter_number
3. front_matter_ranges
4. sections with section_number, section_title, chapter, chapter_number, start_line, end_line, heading_source, confidence, reason
5. back_matter_ranges
6. warnings
"""


def resolve_prompt_profile(prompt_profile: str = "auto", section: MarkdownSection | None = None) -> str:
    requested = (prompt_profile or "auto").strip().lower()
    if requested in PROFILE_PROMPTS:
        return requested
    if requested != "auto":
        return "textbook"
    if section is None:
        return "textbook"
    text = "\n".join([section.context.chapter, section.context.section, section.source_heading, section.text[:4000]])
    if _looks_chinese_math(text):
        return "chinese_math"
    if _looks_paper(section, text):
        return "paper"
    return "textbook"


def _looks_chinese_math(text: str) -> bool:
    cjk_count = len(re.findall(r"[\u4e00-\u9fff]", text))
    if cjk_count >= 20:
        return True
    return any(marker in text for marker in ["定义", "定理", "命题", "引理", "推论", "证明", "注记"])


def _looks_paper(section: MarkdownSection, text: str) -> bool:
    lowered = text.lower()
    section_number = section.context.section_number
    section_title = section.context.section.lower()
    if not section.context.chapter_number and re.fullmatch(r"\d+(?:\.\d+)*|[A-Z]", section_number or ""):
        return True
    paper_markers = [
        "abstract",
        "introduction",
        "prior work",
        "our contributions",
        "computational results",
        "conclusion",
        "proof of theorem",
        "algorithm ",
        "proposition ",
        "arxiv",
    ]
    if any(marker in lowered for marker in paper_markers):
        return True
    return any(marker in section_title for marker in ["problem statement", "prior work", "our contributions", "conclusion"])


# Backward-compatible default used by older callers; extractors now call build_system_prompt.
SYSTEM_PROMPT = build_system_prompt("textbook")
