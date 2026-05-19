# Project Tracker — RFI Agent v2

> **Sprint:** 2026-05-12 → 2026-05-25 (2 weeks)  
> **Dev:** Solo  
> **Last updated:** 2026-05-19  

---

## 🔴 Action Items (Blocking)

These must be resolved before or during Phase 1. No code dependency — just decisions and inputs.

| # | Action Item | Owner | Deadline | Status | Impact if Delayed |
|---|---|---|---|---|---|
| A1 | **~~Verify MongoDB tier~~** — Resolved: using flat JSON knowledge base (`knowledge_base.json`). No database needed for v1. | You | May 12 | ✅ Resolved | — |
| A2 | **Provide Legal base info document** — Legal is a separate category from Compliance but has no source doc. Needed for Knowledge Index. | You | May 14 | 🔲 | 8/9 categories still work; Legal questions flagged for human |
| A3 | **~~Decide: orchestration framework~~** — Resolved: Plain Python + `anthropic` SDK. Implemented in `agents.py`. | You | May 14 | ✅ Resolved | — |
| A4 | **~~Decide: agent count~~** — Resolved: 3 agents (classify → match_and_fill → review). | You | May 14 | ✅ Resolved | — |
| A5 | **~~Decide: embedding model~~** — Resolved: Azure OpenAI `text-embedding-ada-002` via `AZURE_OPENAI_EMBEDDING_DEPLOYMENT` env var. Dimension auto-detected at indexing time. | You | May 12 | ✅ Resolved | — |
| A6 | **~~Decide: LLM~~** — Resolved: Claude Sonnet (`claude-sonnet-4-20250514`) throughout. | You | May 15 | ✅ Resolved | — |

---

## 🔴 Critical Bugs Found (2026-05-19 Audit)

| # | Issue | Severity | Status |
|---|---|---|---|
| B1 | `match_and_fill_async` doesn't exist — `ui.py` imports it but it's never been implemented | P0 | ✅ Fixed |
| B2 | `cli.py` calls `match_and_fill(questions, client_name=..., max_concurrent=...)` but actual signature requires `knowledge_base` and `base_info` positional args | P0 | ✅ Fixed |
| B3 | `classify_questions()` is never called in the pipeline — dead code removed, category comes from sheet name hint | P0 | ✅ Removed |
| B4 | Filler uses `max_tokens=2048` but PRD said `4096` — PRD corrected to match code (2048) | P1 | ✅ Fixed |
| B5 | Reviewer groups by `category` but PRD says group by `sheet_name` | P1 | ✅ Fixed |
| B6 | No `fill_status` tracking in sync `match_and_fill()` — UI depends on it | P1 | ✅ Fixed |
| B7 | No `stop_reason` / truncation detection (PRD: truncated → confidence 0.3) | P1 | ✅ Fixed |
| B8 | Dead `classify_questions()` + `CATEGORIES` + `_KEYWORD_MAP` removed from agents.py | P2 | ✅ Removed |
| B9 | Frontend is pure mock — `simulateAgentFill()` with hardcoded fake answers, no backend API | P2 | Expected (prototype) |
| B10 | Frontend parser logic diverges from backend (different header regex, no `_is_section_header()`) | P2 | Acceptable for now |
| B11 | OData injection in `_find_similar_qas_azure` and `_find_base_info` — category string unescaped in filter | P1 | ✅ Fixed |

---

## Progress Summary

| Phase | Tasks | Done | Status |
|---|---|---|---|
| Phase 1: Foundation | 6 | 6 | ✅ Complete |
| Phase 2: Core Pipeline | 6 | 4 | 🔴 Broken (async missing, signature mismatch, no classifier call) |
| Phase 3: Output & Polish | 5 | 3 | 🟡 In progress (2 remaining) |
| Phase 4: Testing & Hardening | 4 | 0 | 🔲 Not started |
| **Total** | **21** | **13** | **62%** |

---

## Phase 1: Foundation — Days 1–3 (May 12–14)

| # | Task | Status | Blocked By | Notes |
|---|---|---|---|---|
| 1.1 | Project setup: venv, `requirements.txt`, folder structure, `.env` template | ✅ | — | Done. `requirements.txt`, `.env.example`, `data/`, `data/base_info/` created. |
| 1.2 | Base info parsing: convert PDFs + HTMLs to clean text/markdown | ✅ | — | Done. `base_info_parser.py` written; 8 `.txt` files in `data/base_info/`. |
| 1.3 | Excel parser: read .xlsx/.xlsm, extract Q&A pairs, handle structure variation | ✅ | — | Done. `excel_parser.py` with heuristic column detection, `RFIQuestion` dataclass. Needs testing against more formats. |
| 1.4 | Database setup: dual-index schema (Question Index + Knowledge Index) | ✅ Superseded | — | Replaced by flat JSON (`knowledge_base.json`). No DB needed for v1. |
| 1.5 | Indexing script: parse all 20 RFIs → extract Q&As → store in JSON KB | ✅ | — | Done. `indexer.py` builds structured JSON with `by_category` index. No embeddings needed. |
| 1.6 | Indexing script: parse base info docs → load as text files | ✅ Superseded | — | Base info loaded directly as `.txt` files at runtime. No chunking/embedding. |

**Phase 1 deliverable:** `rfi-agent index` command works — all 20 RFIs and base info are searchable.

---

## Phase 2: Core Pipeline — Days 4–8 (May 15–19)

| # | Task | Status | Blocked By | Notes |
|---|---|---|---|---|
| 2.1 | Category classifier | ✅ Removed | — | Dead code deleted (2025-05-19). No classifier agent per PRD — Azure semantic matching handles relevance. Category comes from sheet name hint only. |
| 2.2 | Retrieval: hybrid search (Azure AI Search only) | ✅ | — | Azure helpers implemented. JSON fallback removed. CLI passes only client_name. |
| 2.3 | Client matching: extract client from filename, boost same-client results | ✅ | — | Done. `extract_client_from_filename()` + Azure scoring profile `client-boost` (3x) or same-client priority in JSON fallback. |
| 2.4 | Boilerplate detection + auto-fill from static answers | ✅ Superseded | — | Handled by filler prompt priority: identical past Q&A → reuse verbatim. No separate boilerplate step needed. |
| 2.5 | Filler agent: LLM generates/adapts answers using matched context | 🔴 Broken | — | Sync `match_and_fill()` works but: (1) wrong `max_tokens` (2048 vs PRD 4096), (2) no `fill_status` tracking, (3) no truncation detection, (4) no async version exists yet. |
| 2.6 | Confidence scoring: similarity-based + LLM self-assessment | ✅ | — | Done. LLM self-scores in `match_and_fill()` + `review_answers()` adjusts for contradictions. |

**Phase 2 deliverable:** End-to-end pipeline runs in memory — questions go in, (answer, confidence) pairs come out.

---

## Phase 3: Output & Polish — Days 9–11 (May 20–22)

| # | Task | Status | Blocked By | Notes |
|---|---|---|---|---|
| 3.1 | Excel writer: create filled copy with answers in correct cells | � | — | `writer.py` scaffolded. `write_filled_rfi()` started — needs completion. |
| 3.2 | Color-coding: green (≥0.80), yellow (0.50–0.79), red (<0.50) | ✅ | — | Done. `PatternFill` definitions + `_confidence_fill()` helper in `writer.py`. |
| 3.3 | Confidence column: insert next to each answer section | 🟡 | — | Partially started in `writer.py`. Needs cell-placement logic. |
| 3.4 | Summary report: generate `_SUMMARY.md` with fill rate, flags, sources | 🔲 | — | `write_summary` referenced in `cli.py` but not yet implemented. |
| 3.5 | CLI interface: `rfi-agent fill`, `rfi-agent index`, `rfi-agent stats` | ✅ | — | Done. `cli.py` with Click group, all 3 commands wired up. |

**Phase 3 deliverable:** `rfi-agent fill <file.xlsx>` produces a color-coded filled Excel + summary markdown.

---

## Phase 4: Testing & Hardening — Days 12–14 (May 23–25)

| # | Task | Status | Blocked By | Notes |
|---|---|---|---|---|
| 4.1 | Test against 5 different RFI formats from the library | 🔲 | — | Pick diverse formats: single-sheet, multi-sheet, .xlsm, etc. |
| 4.2 | Edge case handling: merged cells, dropdowns, macros, empty sheets | 🔲 | — | See PRD §7 for full edge case list |
| 4.3 | README + usage guide | 🔲 | — | Setup instructions, CLI docs, examples |
| 4.4 | Demo run: process a real RFI end-to-end, review with team | 🔲 | — | Final validation before handoff |

**Phase 4 deliverable:** Battle-tested v1 ready for daily use.

---

## Risk Register

| Risk | Likelihood | Impact | Mitigation | Owner |
|---|---|---|---|---|
| Excel format variety breaks parser | High | Medium | Build heuristic fallbacks; test early with 3+ formats | Dev |
| 2-week timeline too tight | Low | Medium | Ahead of schedule — 67% done on day 1. Main risk is writer + testing. | Dev |
| Low retrieval quality (20 RFIs) | Low | High | Azure AI Search hybrid retrieval (keyword + vector + semantic ranker) replaces unranked JSON filter. Falls back to JSON if Azure not configured. | Dev |
| LLM hallucination on compliance Qs | Low | High | Anti-hallucination guardrail in filler prompt; `temperature=0.1`; always ground in context; conservative flagging | Dev |
| Missing Legal base info | Medium | Low | Other 8 categories work fine; Legal questions flagged for human | You |
| ~~MongoDB tier unknown~~ | — | — | Resolved: using flat JSON. No database. | — |
| ~~Open decisions delay Phase 2~~ | — | — | Resolved: 5/6 decisions made via implementation. Only A2 (Legal doc) remains. | — |

---

## Daily Log

*Update this section daily with progress notes.*

| Date | Work Done | Blockers | Next |
|---|---|---|---|
| May 11 | PRD, tracker, knowledge-retrieval-strategy created. Full codebase scaffolded: `agents.py` (3 agents: classify, fill, review), `excel_parser.py`, `indexer.py`, `base_info_parser.py`, `writer.py`, `cli.py`. Parsed 8/9 base info docs to text. Resolved decisions A1/A3/A4/A5/A6 via implementation (JSON KB, plain Python, 3 agents, no embeddings, Claude Sonnet). | Legal base info doc (A2) still missing. | Finish `writer.py`, implement `write_summary`, test Excel parser against real RFIs, run end-to-end pipeline. |
| May 12 | **Grill session (Q1–Q11) + bug fixes:** Removed base info `[:3000]` truncation (7/8 files lost 38-71%). Rewrote `_find_similar_qas()` — was dumping all same-client Q&As regardless of category. Rewrote `review_answers()` — had `except Exception: pass`. Removed `[:150]`/`[:300]`/`[:500]` truncations from reviewer + filler prompts. Added `category_hint` to classifier prompt (was promised but never sent). Rewrote filler instructions: identical Q&A match first, existing answer demoted to #3, added anti-hallucination guardrail. Fixed temp to 0.1. Set `max_results=10` on retrieval. Created `CHANGELOG.md`. | Legal base info doc (A2) still missing. | Azure AI Search migration. |
| May 13 | **Azure AI Search migration (v2):** Rewrote retrieval layer — `_find_similar_qas` split into Azure (hybrid: keyword + vector + semantic ranker) and JSON fallback with auto-detection. Added `_embed()`, `_find_base_info_azure()`, Azure helper functions. Rewrote `indexer.py` — creates both indexes (`rfi-questions`, `rfi-knowledge`), embeds Q&As, chunks base info by paragraph, uploads. Added scoring profile (client boost 3x). Created `.gitignore`. Updated `.env.example` with 8 Azure vars. Updated `requirements.txt`. Zero breaking changes — JSON fallback preserved. | Need Azure credentials to test. | Plug in Azure creds, test end-to-end, finish `writer.py`, implement `write_summary`. |
| May 14 | | | |
| May 15 | | | |
| May 16 | | | |
| May 17 | | | |
| May 18 | | | |
| May 19 | | | |
| May 20 | | | |
| May 21 | | | |
| May 22 | | | |
| May 23 | | | |
| May 24 | | | |
| May 25 | | | |
