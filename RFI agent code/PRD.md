# PRD: RFI Auto-Fill Multi-Agent System

> **Product:** RFI Agent v2  
> **Owner:** Avalere Health — ASK! RFI Team  
> **Date:** 2026-05-19  
> **Status:** Implemented — all architectural decisions resolved

---

## 1. Problem Statement

Avalere Health consultants manually fill RFIs (Requests for Information) from pharma/biotech clients. Each RFI is an Excel workbook with 50–200+ questions covering company info, compliance, data security, ESG, staffing, and more.

**Pain points:**
- 60–70% of questions are recurring across clients (same question, same answer)
- Consultants spend 4–8 hours per RFI on boilerplate answers they've written before
- No central system connects past answers to new questions
- Base info exists on SharePoint but isn't structured for quick retrieval
- Risk of inconsistent answers across RFIs for the same client

---

## 2. Goals

### v2 Goals (current)
1. **Auto-fill boilerplate questions** — static company facts that never change ✅
2. **Match and adapt past answers** — hybrid retrieval (keyword + vector + semantic ranker) from Azure AI Search ✅
3. **Semantic relevance ranking** — no category gating; Azure AI Search handles relevance ✅
4. **Flag gaps** — clearly mark questions the system couldn't confidently answer ✅
5. **Preserve Excel formatting** — output is a new copy of the original Excel, macros preserved ✅
6. **Async concurrency** — parallel Claude calls with rate-limit handling ✅
7. **Live progress UI** — Rich TUI with real-time status per question ✅

### Non-Goals
- Web UI / dashboard
- SharePoint API integration (blocked by InfoSec)
- Real-time collaboration
- Auto-submission to clients
- Fine-tuning any model

### Planned (v2.1 — branch `word_ppt_input`)
- **.docx / .pptx format support** — parse RFI questions from Word documents and PowerPoint slides, extract to same question structure as Excel. In progress on feature branch, not yet tested or merged.

---

## 3. System Architecture

### High-Level Data Flow

```
┌────────────┐     ┌────────────┐     ┌──────────────────┐     ┌────────────────┐
│   INPUT    │     │  MODULE    │     │     AGENT 1      │     │ AGENT 2 +      │
│            │     │            │     │                  │     │  MODULE        │
│  New RFI   │────▶│   Parser   │────▶│ Matcher + Filler │────▶│ Reviewer +     │
│  (.xlsx)   │     │  (Python)  │     │                  │     │ Writer (Python)│
└────────────┘     └────────────┘     └──────────────────┘     └────────────────┘
                                              │                        │
                                     ┌────────▼────────┐      ┌───────▼────────┐
                                     │  Azure AI Search │      │   Claude API   │
                                     │  (Dual Index)    │      │   (Anthropic)  │
                                     └─────────────────┘      └────────────────┘
```

> **Note:** The pipeline has **2 LLM agents** (Matcher+Filler, Reviewer) and **2 Python modules** (Parser, Writer). There is no separate Classifier agent — category inference is handled within the Matcher+Filler agent using sheet name hints and Azure AI Search's built-in semantic matching. The Parser and Writer are deterministic Python code — no LLM calls.

### Component Descriptions

#### Module: Parser (Python — no LLM)
- **Input:** Path to new RFI Excel file (.xlsx or .xlsm)
- **Output:** Structured list of questions with cell references (sheet_name, row, question_col, answer_col)
- **LLM:** None — pure Python heuristics (see `excel_parser.py`)
- **Logic:**
  - Open workbook with openpyxl (read-only mode for .xlsm to preserve macros)
  - **For each sheet:** detect question/answer column structure via regex header matching (`QUESTION_HEADERS`, `ANSWER_HEADERS`) with fallback to longest-text-column heuristic
  - **For each row after header:** extract question text, existing answer, question number; skip section headers and non-question rows
  - Infer category hint from sheet name (e.g., "Compliance" tab → Compliance category)
  - Output: `List[RFIQuestion(sheet_name, row, question_col, answer_col, question_text, existing_answer, category_hint)]`

#### Agent 1: Matcher + Filler (async)
- **Input:** Parsed questions + client name (auto-detected from filename)
- **Output:** Each question annotated with `generated_answer`, `confidence`, `citation`, `source_references`, `fill_status`
- **LLM:** Claude Sonnet (`claude-sonnet-4-20250514`) via `AsyncAnthropic`
- **LLM config:** `temperature=0.1`, `max_tokens=2048`
- **Concurrency:** `asyncio.Semaphore(5)` — max 5 parallel Claude calls with exponential backoff on rate limits (base 2s, max 3 retries)
- **Logic:**
  - **Retrieval (Azure AI Search):** Hybrid search (keyword + vector + optional semantic ranker) against `rfi-questions` index. No category filter — relevance ranking handles topic matching. Client name passed as a scoring profile parameter for soft boost (not a hard filter).
  - **Base info retrieval:** Separate hybrid query against `rfi-knowledge` index. Returns top 5 paragraph-level chunks by relevance. No truncation.
  - **Generate:** One async Claude API call per question with full context (all past Q&As + all base info chunks + existing answer if any).
  - **Priority order (in prompt):**
    1. Identical past Q&A → reuse answer verbatim
    2. Similar past Q&As → adapt the most relevant
    3. Existing answer (if consistent with other sources)
    4. Base company info → synthesize
    5. Insufficient context → respond with `[NEEDS REVIEW]`
  - **Anti-hallucination:** Explicit guardrail in prompt: "DO NOT fabricate facts, statistics, certifications, policy details, or any specifics not present in the provided context."
  - **Confidence scoring:** Generated by Claude (0.0–1.0) based on source quality and answer completeness. NOT hardcoded. Clamped to `[0.0, 1.0]`. Override only on error conditions: truncation → 0.3, rate limit/error → 0.0.
  - **Citations:** Claude returns structured `citation` string + `sources` array referencing past RFI filenames and base info documents.
  - **Truncation detection:** If `stop_reason == "max_tokens"`, answer marked as truncated with confidence 0.3.
  - Output: each question dict gains `generated_answer`, `confidence`, `citation`, `source_references`, `fill_status` (filled / truncated / rate_limited / parse_error / error)

#### Agent 2: Reviewer
- **Input:** Filled questions (grouped by `sheet_name`)
- **Output:** Questions with adjusted confidence scores, `review_status`, and `review_flag`
- **LLM:** Claude Sonnet (`claude-sonnet-4-20250514`) via sync `Anthropic`
- **LLM config:** `temperature=0`, `max_tokens=2048`
- **Logic:**
  - Group answers by sheet (not category — sheets are the natural grouping from the source Excel)
  - For each sheet with 2+ answered questions, send full Q&A text (no truncation) to Claude to check for contradictions and factual concerns
  - Cap at 20 answers per review call to stay within context budget
  - Every answer gets a `review_status`: `"reviewed"` (clean), `"flagged"` (issue found), or `"unreviewed"` (review failed/skipped)
  - Confidence adjustments: flagged issues get per-flag adjustment, contradictions capped at 0.5
  - **Error handling:**
    - Parse errors (malformed Claude JSON) → all answers in that sheet marked `"unreviewed"`, confidence capped at 0.6
    - Auth and rate-limit errors → re-raised (must not be silently swallowed)
    - Other API errors → non-fatal, answers marked `"unreviewed"`, confidence capped at 0.6

#### Module: Writer (Python — no LLM)
- **Input:** Reviewed questions with answers + original Excel file path
- **Output:** Filled Excel file + summary report
- **LLM:** None — pure Python (see `writer.py`)
- **Logic:**
  - Copy original file to `{filename}_FILLED.xlsx` (preserves formatting, macros)
  - **For each sheet:** group questions by `sheet_name`, write `generated_answer` into `answer_col` at `row`
  - Add "AI Confidence" column next to answer column with scores formatted as percentages
  - Color-code: green (≥0.80), yellow (0.50–0.79), red (<0.50)
  - Only overwrites empty cells or placeholder values (N/A, TBD, Pending); preserves existing answers
  - **Summary report:** Generate `{filename}_SUMMARY.md` listing fill rate, flagged questions, sources used (not yet implemented)

---

## 4. Category Taxonomy

| # | Category | Base Info Source | Example Questions |
|---|---|---|---|
| 1 | Company Information | Company Information.pdf | Year founded, HQ location, ownership |
| 2 | Commercial Information | Commercial Information (General).pdf | Revenue, client list, case studies |
| 3 | Compliance | Compliance.html (to be converted) | Anti-bribery policy, audit history |
| 4 | Legal | *User to provide* | Contractual terms, liability, insurance |
| 5 | Data & Information Security | Data, information security....html | GDPR, SOC 2, data handling, encryption |
| 6 | ESG | Environmental, social, and governance.html | Sustainability, DEI, carbon reporting |
| 7 | People Information | People Information.html | Headcount, attrition, training |
| 8 | Suppliers & Freelancers | Suppliers and freelancers.html/.pdf | Subcontracting policy, vendor management |
| 9 | Technology & AI | Technology and AI.pdf | AI governance, tech stack, innovation |

---

## 5. Knowledge Base Strategy

See [knowledge-retrieval-strategy.md](./knowledge-retrieval-strategy.md) for full research.

**Implementation:** Azure AI Search dual-index with hybrid retrieval (keyword + vector + semantic ranker).

### Indexes
- **`rfi-questions`** — Past Q&A pairs from 20+ filled RFIs. Fields: `question_text`, `answer_text`, `client`, `source_file`, `category`, `embedding` (vector). Scoring profile boosts same-client results 3x.
- **`rfi-knowledge`** — Base company info chunked at paragraph level (~200–800 tokens). Fields: `content`, `section_heading`, `source_doc`, `embedding` (vector).

### Indexing Pipeline (`python cli.py index`)
1. Parse all filled RFIs → extract Q&A pairs → embed questions via Azure OpenAI (`text-embedding-ada-002`) → upload to `rfi-questions`
2. Parse base info docs (HTML → text) → chunk by paragraph → embed → upload to `rfi-knowledge`
3. Extract metadata: category, source file, question ID for citation traceability
4. Client name stored as metadata — used for scoring profile boost (soft signal), NOT as a hard filter

---

## 6. Input / Output Specifications

### Input
- **New RFI file:** `.xlsx` or `.xlsm` file path
- **Client name:** Auto-detected from filename (regex match against known client list). Override with `--client` flag.
- **CLI command:** `python cli.py fill <path-to-rfi.xlsx> [--client <name>] [--output-dir <path>] [--interactive/--no-interactive] [--concurrency N]`

### Output
- **Filled Excel:** `{original_name}_FILLED.xlsx` — new copy with answers + confidence column + color-coding
- **Summary report:** `{original_name}_SUMMARY.md` — fill rate, flagged items, sources
- **Categorized workbook (optional):** If the input RFI isn't organized by category, produce a `{original_name}_CATEGORIZED.xlsx` with questions grouped into category sheets

### CLI Interface
```bash
# Fill a new RFI (client auto-detected from filename, live TUI enabled by default)
python cli.py fill "path/to/new_rfi.xlsx"

# Fill with explicit client name + higher concurrency
python cli.py fill "path/to/new_rfi.xlsx" --client "Pfizer" --concurrency 8

# Fill without interactive TUI (plain output)
python cli.py fill "path/to/new_rfi.xlsx" --no-interactive

# Re-index the knowledge base (JSON + Azure AI Search)
python cli.py index

# Show stats about the knowledge base
python cli.py stats
```

---

## 7. Edge Cases & Mitigations

| Edge Case | Mitigation |
|---|---|
| Merged cells in Excel | Unmerge, attribute content to top-left cell |
| Questions spanning multiple rows | Detect by: row has text but no answer column content, next row continues |
| Dropdown / validation cells | Preserve data validation in output; if answer must match a dropdown option, constrain generation |
| .xlsm macros | Open in read-only mode; copy file byte-for-byte, only modify answer cells via openpyxl |
| Very long questions (>512 tokens) | Truncate for embedding, but send full text to Claude |
| Ambiguous category | Assign primary + secondary category; search both in vector store |
| Same question worded differently | Handled by semantic similarity (embeddings catch paraphrases) |
| Empty / instruction-only sheets | Skip sheets with <3 question-like rows |
| Non-English content | Flag for human review; don't auto-fill |
| Password-protected Excel | Fail gracefully with clear error message |
| Contradictory past answers | Use most recent answer; flag discrepancy in summary |

---

## 8. Scope Boundaries

### Implemented (v2)
- Excel parsing (.xlsx, .xlsm) with macro preservation
- Azure AI Search hybrid retrieval (keyword + vector + semantic ranker)
- Dual-index: past Q&A pairs + base info chunks
- Async concurrent fill with rate-limit handling
- Live progress TUI (Rich library)
- Client-aware matching (auto-detected from filename)
- LLM-generated confidence scoring + color-coded Excel output
- Review agent with structured status (reviewed/flagged/unreviewed)
- Anti-hallucination guardrails
- CLI interface with interactive mode
- Summary report generation

### Out of Scope (future)
- SharePoint API integration
- Web UI
- .docx / .pptx parsing
- Feedback loop (consultant corrections → update index)
- Graph RAG / entity extraction
- Multi-language support
- Auto-updating base info
- Approval workflows
- Integration with document management systems
- Dynamic client list (currently hardcoded in `extract_client_from_filename`)

---

## 9. Decisions Log

| Decision | Options Considered | Result | Status |
|---|---|---|---|
| Orchestration framework | LangGraph / Pydantic AI / Plain Python | **Plain Python + `anthropic` SDK + asyncio** | ✅ Decided |
| Agent count | 4 agents / 7 agents / 3+2 / 2+2 | **2 LLM agents + 2 Python modules** (no Classifier) | ✅ Decided |
| Embedding model | Voyage AI / local sentence-transformers / Azure OpenAI | **Azure OpenAI `text-embedding-ada-002`** | ✅ Decided |
| LLM | Claude / OpenAI / evaluate both | **Claude Sonnet** (`claude-sonnet-4-20250514`) | ✅ Decided |
| Vector store | ChromaDB / FAISS / Azure AI Search / none | **Azure AI Search** (hybrid: keyword + vector + semantic ranker) | ✅ Decided |
| Concurrency | Sequential / batch / async | **asyncio + Semaphore(5)** with exponential backoff | ✅ Decided |

---

## 10. Success Metrics

| Metric | Target |
|---|---|
| Fill rate (questions auto-answered) | ≥ 60% |
| Boilerplate accuracy | ≥ 95% |
| Adapted answer accuracy | ≥ 75% (consultant needs minor edits) |
| Processing time per 100-question RFI | < 3 minutes (async concurrency) |
| Consultant time saved per RFI | ≥ 4 hours (from ~6h to ~2h) |
| Unreviewed answers clearly flagged | 100% (no silent failures) |

---

## 11. Risks

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Excel format variety breaks parser | High | Medium | Robust heuristics + regex header detection; fall back to "flag all" |
| Claude hallucination on compliance Qs | Low | High | Explicit anti-hallucination guardrail + `[NEEDS REVIEW]` fallback |
| Base info docs are outdated | Medium | Medium | Timestamp sources; flag if base info is >6 months old |
| API rate limits during large RFIs | Medium | Low | Semaphore(5) + exponential backoff (3 retries) |
| Azure AI Search index drift | Low | Medium | Re-index via `python cli.py index`; JSON fallback path available |
| New client not in filename detection list | Medium | Low | Falls back to empty string; `--client` CLI override available |
