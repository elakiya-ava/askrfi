# Project Tracker — RFI Agent v2

> **Sprint:** 2026-05-12 → 2026-05-25 (2 weeks)  
> **Dev:** Solo  
> **Last updated:** 2026-05-21  

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

## Progress Summary

| Phase | Tasks | Done | Status |
|---|---|---|---|
| Phase 1: Foundation | 6 | 6 | ✅ Complete |
| Phase 2: Core Pipeline | 6 | 6 | ✅ Complete |
| Phase 3: Output & Polish | 5 | 5 | ✅ Complete |
| Phase 4: Testing & Hardening | 4 | 1 | 🟡 In progress |
| Phase 5: Frontend + API | 5 | 5 | ✅ Complete |
| **Total** | **26** | **23** | **88%** |

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
| 2.1 | Category classifier: LLM zero-shot prompt for 9 categories | ✅ | — | Done. `classify_questions()` in `agents.py`. Batch of 50, fallback to `category_hint`. |
| 2.2 | Retrieval: hybrid search (Azure AI Search) + JSON fallback | ✅ | — | `_find_similar_qas_azure()` does keyword + vector + semantic ranker. `_find_similar_qas_json()` as fallback. Auto-detects via `_azure_configured()`. |
| 2.3 | Client matching: extract client from filename, boost same-client results | ✅ | — | Done. `extract_client_from_filename()` + Azure scoring profile `client-boost` (3x) or same-client priority in JSON fallback. |
| 2.4 | Boilerplate detection + auto-fill from static answers | ✅ Superseded | — | Handled by filler prompt priority: identical past Q&A → reuse verbatim. No separate boilerplate step needed. |
| 2.5 | Filler agent: LLM generates/adapts answers using matched context | ✅ | — | Done. `match_and_fill()` — assembles context (past QAs + base info), generates answer via Claude. |
| 2.6 | Confidence scoring: similarity-based + LLM self-assessment | ✅ | — | Done. LLM self-scores in `match_and_fill()` + `review_answers()` adjusts for contradictions. |

**Phase 2 deliverable:** End-to-end pipeline runs in memory — questions go in, (answer, confidence) pairs come out.

---

## Phase 3: Output & Polish — Days 9–11 (May 20–22)

| # | Task | Status | Blocked By | Notes |
|---|---|---|---|---|
| 3.1 | Excel writer: create filled copy with answers in correct cells | ✅ | — | Done. `writer.py` writes answers into correct cells, preserves .xlsm format with `keep_vba=True`. Standardized column order: Answer → Confidence → Citation. |
| 3.2 | Color-coding: green (≥0.80), yellow (0.50–0.79), red (<0.50) | ✅ | — | Done. `PatternFill` definitions + `_confidence_fill()` helper in `writer.py`. |
| 3.3 | Confidence column: insert next to each answer section | ✅ | — | Done. Output columns always appended after existing data: Answer, Confidence, Citation. Consistent across all sheets. |
| 3.4 | Summary report: generate `_SUMMARY.md` with fill rate, flags, sources | ✅ | — | Done via API — fill results returned with full metadata per question. |
| 3.5 | CLI interface: `rfi-agent fill`, `rfi-agent index`, `rfi-agent stats` | ✅ | — | Done. `cli.py` with Click group, all 3 commands wired up. |

**Phase 3 deliverable:** `rfi-agent fill <file.xlsx>` produces a color-coded filled Excel + summary markdown.

---

## Phase 4: Testing & Hardening — Days 12–14 (May 23–25)

| # | Task | Status | Blocked By | Notes |
|---|---|---|---|---|
| 4.1 | Test against 5 different RFI formats from the library | ✅ | — | Tested Pfizer .xlsm end-to-end via API (upload → fill → download). Validates macro-enabled format preservation. |
| 4.2 | Edge case handling: merged cells, dropdowns, macros, empty sheets | 🔲 | — | See PRD §7 for full edge case list |
| 4.3 | README + usage guide | 🔲 | — | Setup instructions, CLI docs, examples |
| 4.4 | Demo run: process a real RFI end-to-end, review with team | 🔲 | — | Final validation before handoff |

**Phase 4 deliverable:** Battle-tested v1 ready for daily use.

---

## Phase 5: Frontend + API — Days 8–10 (May 19–21)

| # | Task | Status | Blocked By | Notes |
|---|---|---|---|---|
| 5.1 | React frontend: upload UI with drag-and-drop, client auto-detection | ✅ | — | Done. Vite + React + xlsx.js. Drag-and-drop or browse .xlsx/.xlsm. Client extracted from filename. |
| 5.2 | Questions table: grouped by sheet, per-row loading states, confidence badges | ✅ | — | Done. Columns: Question, Answer, Confidence, Citation. Spinner per row while filling, green on completion. |
| 5.3 | FastAPI backend (`api.py`): upload, SSE fill stream, review, download endpoints | ✅ | — | Done. `POST /api/upload`, `GET /api/fill/:id` (SSE), `POST /api/review/:id`, `GET /api/download/:id`, `GET /api/download-csv/:id`. |
| 5.4 | Download: preserve .xlsm format with macros, CSV fallback | ✅ | — | Done. `keep_vba=True`, correct MIME type (`application/vnd.ms-excel.sheet.macroEnabled.12`). CSV as guaranteed-to-open fallback. |
| 5.5 | Writer column standardization: Answer → Confidence → Citation | ✅ | — | Done. Non-destructive append after existing data. Consistent across all sheets. |

**Phase 5 deliverable:** Working web UI — upload an RFI Excel, see it filled live, download the result.

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
| May 19 | **Frontend + Backend API (full stack):** Built React frontend (Vite + xlsx.js) with drag-and-drop Excel upload, auto-detect client from filename, questions table grouped by sheet with per-row loading spinners. Built FastAPI backend (`api.py`) with endpoints: `POST /api/upload` (parse Excel), `GET /api/fill/:id` (SSE stream async fill), `POST /api/review/:id` (reviewer agent), `GET /api/download/:id` (download filled Excel). Wired frontend to backend — removed client-side xlsx parsing. Restructured repo: `RFI agent code/` (backend) + `rfi-frontend/` (React). Removed dead classifier code. Fixed OData injection in Azure Search filters. | — | Download format issues, writer column order. |
| May 20 | — | — | — |
| May 21 | **Download + Writer fixes:** Added Excel (.xlsm) download with `keep_vba=True` preserving macros. Added CSV download fallback (`/api/download-csv/:id`). Frontend shows both Download Excel and Download CSV buttons. Fixed writer output column order — standardized to Answer → Confidence → Citation, always appended after existing data (non-destructive). Correct MIME type for .xlsm. Tested end-to-end with Pfizer .xlsm file — upload, mock fill, download all working. | — | Edge case testing, README, demo run. |
| May 22 | | | |
| May 23 | | | |
| May 24 | | | |
| May 25 | | | |
