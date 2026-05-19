# Project Tracker — RFI Agent v1

> **Sprint:** 2026-05-12 → 2026-05-25 (2 weeks)  
> **Dev:** Solo  
> **Last updated:** 2026-05-11  

---

## Progress Summary

| Phase | Tasks | Done | Status |
|---|---|---|---|
| Phase 1: Foundation | 6 | 0 | 🔲 Not started |
| Phase 2: Core Pipeline | 6 | 0 | 🔲 Not started |
| Phase 3: Output & Polish | 5 | 0 | 🔲 Not started |
| Phase 4: Testing & Hardening | 4 | 0 | 🔲 Not started |
| **Total** | **21** | **0** | **0%** |

---

## Phase 1: Foundation — Days 1–3 (May 12–14)

| # | Task | Status | Notes |
|---|---|---|---|
| 1.1 | Project setup: venv, `requirements.txt`, folder structure, `.env` template | 🔲 | Python 3.11+, create `src/`, `data/`, `tests/` |
| 1.2 | Base info parsing: convert PDFs + HTMLs to clean text/markdown | 🔲 | pymupdf for PDFs, bs4 for HTMLs; store in `data/base_info/` |
| 1.3 | Excel parser: read .xlsx/.xlsm, extract Q&A pairs, handle structure variation | 🔲 | Biggest risk item — test against 3+ RFI formats from library |
| 1.4 | ChromaDB setup: dual-index schema (Question Index + Knowledge Index) | 🔲 | Local persistent storage in `data/chromadb/` |
| 1.5 | Indexing script: parse all 20 RFIs → extract Q&As → embed → store in Question Index | 🔲 | Extract client name from filename; detect category from sheet name |
| 1.6 | Indexing script: parse base info docs → chunk → embed → store in Knowledge Index | 🔲 | Chunk by section (~200-500 tokens) |

**Phase 1 deliverable:** `rfi-agent index` command works — all 20 RFIs and base info are searchable in ChromaDB.

**Blockers:**
- [ ] User to provide Legal base info document
- [ ] Decision: embedding model (local sentence-transformers vs Voyage AI)

---

## Phase 2: Core Pipeline — Days 4–8 (May 15–19)

| # | Task | Status | Notes |
|---|---|---|---|
| 2.1 | Category classifier: Claude zero-shot prompt for 9 categories | 🔲 | Batch questions to minimize API calls; test on 50 questions from past RFIs |
| 2.2 | Vector store retrieval: semantic search with category + client filtering | 🔲 | Top-3 results; tune similarity thresholds (0.50 / 0.70 / 0.90) |
| 2.3 | Client matching: extract client from filename, boost same-client results | 🔲 | Regex on filename patterns like "Gilead", "Pfizer", "AZ" |
| 2.4 | Boilerplate detection + auto-fill from static answers | 🔲 | Build boilerplate lookup table from base info |
| 2.5 | Filler agent: Claude generates/adapts answers using matched context | 🔲 | Prompt engineering: "adapt this past answer to the new question" |
| 2.6 | Confidence scoring: similarity-based + Claude self-assessment | 🔲 | Calibrate against manual review of 20 test questions |

**Phase 2 deliverable:** End-to-end pipeline runs in memory — questions go in, (answer, confidence) pairs come out.

**Blockers:**
- [ ] Decision: orchestration framework (Plain Python vs LangGraph vs Pydantic AI)
- [ ] Decision: agent count (4 vs 7)

---

## Phase 3: Output & Polish — Days 9–11 (May 20–22)

| # | Task | Status | Notes |
|---|---|---|---|
| 3.1 | Excel writer: create filled copy with answers in correct cells | 🔲 | Preserve original formatting; copy file then modify |
| 3.2 | Color-coding: green (≥0.80), yellow (0.50–0.79), red (<0.50) | 🔲 | openpyxl PatternFill on answer cells |
| 3.3 | Confidence column: insert next to each answer section | 🔲 | Handle varying layouts per sheet |
| 3.4 | Summary report: generate `_SUMMARY.md` with fill rate, flags, sources | 🔲 | Template: filled/total, by category, flagged items list |
| 3.5 | CLI interface: `rfi-agent fill`, `rfi-agent index`, `rfi-agent stats` | 🔲 | click framework; argument parsing; progress bars |

**Phase 3 deliverable:** `rfi-agent fill <file.xlsx>` produces a color-coded filled Excel + summary markdown.

---

## Phase 4: Testing & Hardening — Days 12–14 (May 23–25)

| # | Task | Status | Notes |
|---|---|---|---|
| 4.1 | Test against 5 different RFI formats from the library | 🔲 | Pick diverse formats: single-sheet, multi-sheet, .xlsm, etc. |
| 4.2 | Edge case handling: merged cells, dropdowns, macros, empty sheets | 🔲 | See PRD §7 for full edge case list |
| 4.3 | README + usage guide | 🔲 | Setup instructions, CLI docs, examples |
| 4.4 | Demo run: process a real RFI end-to-end, review with team | 🔲 | Final validation before handoff |

**Phase 4 deliverable:** Battle-tested v1 ready for daily use.

---

## Open Decisions Tracker

| Decision | Options | Recommendation | Deadline | Impact if Delayed |
|---|---|---|---|---|
| Orchestration framework | Plain Python / LangGraph / Pydantic AI | Plain Python (fastest for 2-week sprint) | May 14 (end of Phase 1) | Blocks Phase 2 architecture |
| Agent count | 4 consolidated / 7 granular | 4 agents | May 14 | Blocks Phase 2 task breakdown |
| Embedding model | Local sentence-transformers / Voyage AI | Local (zero cost) | May 12 (Day 1) | Blocks indexing script |
| Legal base info doc | User provides | — | May 14 | 8/9 categories indexable without it |

---

## Risk Register

| Risk | Likelihood | Impact | Mitigation | Owner |
|---|---|---|---|---|
| Excel format variety breaks parser | High | Medium | Build heuristic fallbacks; test early with 3+ formats | Dev |
| 2-week timeline too tight | Medium | High | Prioritize: parser + indexing + filler are must-haves; CLI polish is nice-to-have | Dev |
| Low retrieval quality (20 RFIs) | Medium | High | Supplement with base info; tune thresholds; manual boilerplate table | Dev |
| Claude hallucination on compliance Qs | Low | High | Always ground in context; conservative flagging | Dev |
| Missing Legal base info | Medium | Low | Other 8 categories work fine; Legal questions flagged for human | User |

---

## Daily Log

*Update this section daily with progress notes.*

| Date | Work Done | Blockers | Next |
|---|---|---|---|
| May 11 | PRD, research, tracker created | 3 open decisions | Lock decisions, start Phase 1 |
| May 12 | | | |
| May 13 | | | |
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
