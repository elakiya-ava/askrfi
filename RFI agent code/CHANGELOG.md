# Changelog — RFI Agent v1 → v2

All notable changes to the project, with rationale.

---

## 2026-05-19 — Code audit: critical bugs identified + frontend scaffold

### Added: Frontend prototype (`rfi-frontend/`)
- **What:** React + Vite frontend with Excel upload (client-side parsing via SheetJS), drag-and-drop, question table grouped by sheet, status badges, confidence color-coding. Currently uses mock data (`simulateAgentFill()` with hardcoded answers) — no backend API connected yet.
- **Why:** Visual prototype to validate UX before wiring up the real backend. Lets stakeholders see progress and give feedback on the interface.
- **Status:** UI shell only. No backend integration. Parser logic is simplified vs backend (different header regex, no section-header filtering, length-based filtering only).

### Found: `match_and_fill_async` does not exist (P0)
- **What:** `ui.py` line 138 imports `match_and_fill_async` from `agents.py`, but no async version of the filler was ever implemented. The TUI (`run_fill_ui`) is completely broken.
- **Why it matters:** The interactive mode (`--interactive`, default ON in `cli.py`) crashes immediately on import. PRD promises `asyncio.Semaphore(5)` with parallel Claude calls — never built.
- **Fix needed:** Implement `match_and_fill_async()` with proper async concurrency, rate-limit backoff, and `fill_status` tracking.

### Found: `cli.py` passes wrong arguments to `match_and_fill()` (P0)
- **What:** CLI calls `match_and_fill(questions, client_name=client, max_concurrent=concurrency)` but the actual function signature is `match_and_fill(questions, knowledge_base, base_info, client_name="", client=None)`. Missing required positional args `knowledge_base` and `base_info`. Extra kwarg `max_concurrent` doesn't exist.
- **Why it matters:** Non-interactive mode (`--no-interactive`) crashes with `TypeError`. Nobody has run this end-to-end.
- **Fix needed:** CLI must load `knowledge_base.json` and base info text files before calling `match_and_fill()`.

### Found: `classify_questions()` never called in pipeline (P0)
- **What:** The classifier function exists but the CLI never invokes it. Questions go into `match_and_fill()` with no `category` field — retrieval always defaults to "Uncategorized".
- **Why it matters:** Category-filtered retrieval (the whole point of the dual-index) never fires. Every question searches the same unfiltered pool.
- **PRD context:** PRD decided "no separate Classifier agent" — Azure AI Search handles relevance without category gating. So the fix is either: (a) remove category filtering from `_find_similar_qas_azure()` and let semantic ranking handle it, OR (b) call `classify_questions()` in the pipeline. PRD says (a).

### Found: Filler `max_tokens=2048` — should be 4096 per PRD
- **What:** `agents.py` line ~427 uses `max_tokens=2048`. PRD §3 specifies `max_tokens=4096` for the Matcher+Filler agent.
- **Why it matters:** Longer answers get truncated. No truncation detection implemented either (PRD says: if `stop_reason == "max_tokens"` → confidence 0.3).

### Found: Reviewer groups by category, not sheet_name
- **What:** `review_answers()` groups by `q.get("category")` but PRD says "Group answers by sheet (not category — sheets are the natural grouping from the source Excel)".

---

## 2025-05-12 — Azure AI Search migration (v2)
- **What:** Full migration from flat JSON retrieval to Azure AI Search hybrid retrieval.
  - `agents.py`: Added `_embed()`, `_get_azure_openai_client()`, `_get_search_client()`, `_azure_configured()`, `_find_base_info_azure()`. Split `_find_similar_qas` into `_find_similar_qas_azure` (keyword + vector + semantic ranker) and `_find_similar_qas_json` (fallback). Router function auto-detects Azure and falls back to JSON gracefully.
  - `indexer.py`: Added `_index_to_azure()` — creates both indexes (`rfi-questions`, `rfi-knowledge`) with proper schemas, scoring profiles (client boost 3x), vector search config. Embeds all Q&A pairs and chunks base info (paragraph-level, ~200-800 tokens). Runs automatically after JSON indexing when Azure is configured.
  - `.env.example`: Added `AZURE_SEARCH_ENDPOINT`, `AZURE_SEARCH_API_KEY`, `AZURE_SEARCH_QUESTION_INDEX`, `AZURE_SEARCH_KNOWLEDGE_INDEX`, `AZURE_OPENAI_ENDPOINT`, `AZURE_OPENAI_API_KEY`, `AZURE_OPENAI_EMBEDDING_DEPLOYMENT`, `AZURE_OPENAI_API_VERSION`.
  - `.gitignore`: Created — `.env` is gitignored so credentials never get committed.
  - `requirements.txt`: Added `azure-search-documents`, `azure-identity`, `openai`.
- **Why:** JSON retrieval had no relevance ranking — just category filter + insertion order. Azure AI Search provides hybrid search (keyword + vector), semantic ranker, real similarity scores, and scoring profiles for client boost. Base info retrieval now returns relevant chunks instead of full files.
- **Impact:** Plug Azure credentials into `.env` once → `python cli.py index` embeds and uploads everything → `python cli.py fill` uses ranked hybrid retrieval. Falls back to JSON if Azure isn't configured.
- **Migration:** Zero breaking changes. All existing JSON-based functionality preserved as fallback.

---

## 2025-05-12 — Filler prompt rewrite: priority order, no hallucination, no cap
- **What:** (1) Removed `[:8]` cap on similar Q&As — all matches now sent. (2) Rewrote instruction priority: identical past Q&A first, then similar past Q&As, then existing answer (demoted from #1 to #3). (3) Added explicit anti-hallucination guardrail: "DO NOT fabricate facts, statistics, certifications, policy details..." (4) Fixed temperature to `0.1` (was toggling between 0 and 0.1 based on context — user will revisit after research).
- **Why:** Old priority told Claude to trust existing answers first, but those could be wrong/outdated. Past Q&As from the knowledge base are more reliable. Hard cap of 8 dropped potentially relevant matches. No hallucination guard is critical for healthcare/compliance RFIs.
- **Impact:** Better answer quality from full context, less hallucination risk, correct priority chain.

---

## 2025-05-12 — Filler prompt truncations removed
- **What:** Removed `[:300]` on past questions, `[:500]` on past answers, and `[:500]` on existing answers in the filler prompt.
- **Why:** Same class of bug as base info and reviewer truncations. 100K token budget per question makes these cuts unnecessary, and they risk losing critical detail from longer answers.
- **Impact:** Filler now sees full context from knowledge base and prior submissions.

---

## 2025-05-12 — Classifier now passes category_hint to Claude
- **What:** Include `category_hint` in the question list sent to the classifier LLM (e.g. `[Hint: Compliance] <question>`). Also removed `[:200]` truncation on question text.
- **Why:** The prompt told Claude to use `category_hint` as a strong signal, but it was never included in the actual input. The hint (derived from Excel sheet names/section headers by `excel_parser.py`) was only used as a fallback on parse failure.
- **Impact:** Classifier accuracy should improve — Claude now sees the structural metadata from the source Excel file.

---

## 2026-05-12

### Fixed: Same-client retrieval now filtered by category (`agents.py`)
- **What:** Rewrote `_find_similar_qas()` so same-client Q&As are filtered by category first, then boosted to the top of results — instead of dumping ALL same-client Q&As regardless of topic.
- **Why:** Bug — a Pfizer ESG question was pulling Pfizer's Data Security, People, and Company Info Q&As (whatever came first in JSON), crowding out relevant ESG answers from other clients. PRD says "boost same-client" (soft preference), not "override category" (hard filter).
- **Design intent:** The return shape of `_find_similar_qas()` is now a stable interface. When Azure AI Search replaces the JSON backend, it returns the same `list[dict]` with `question`, `answer`, `client`, `source`, `match_type`. Callers (`match_and_fill()`) don't change.

### Fixed: Reviewer prompt truncation removed (`agents.py`)
- **What:** Removed `[:150]` truncation on question text and `[:300]` truncation on answer text in the reviewer prompt.
- **Why:** Same issue as base info truncation. Contradictions past character 300 in an answer were invisible to the reviewer. With max 15 answers per category and 200K context window, the review call uses ~4% of available tokens — no reason to clip.
- **Impact:** Reviewer now sees full question and answer text for contradiction/consistency checking.

### Fixed: Reviewer no longer silently swallows errors (`agents.py`)
- **What:** Rewrote `review_answers()` with three changes:
  1. Every answer now gets a `review_status` field: `"reviewed"` (clean), `"flagged"` (issue found), or `"unreviewed"` (review failed/skipped).
  2. Parse errors (malformed Claude JSON) → all answers in that category marked `"unreviewed"` with confidence capped at 0.6.
  3. Auth and rate-limit errors → re-raised (must not be swallowed silently). Other API errors → treated as non-fatal, answers marked `"unreviewed"`.
- **Why:** Previous `except Exception: pass` silently skipped all review failures. If the API key expired mid-run, output looked "fine" but was entirely unreviewed — no way to tell. Unreviewed answers must be visibly flagged so the consultant knows to check them manually.
- **Impact:** Writer can now use `review_status` for color-coding or summary reporting. Boilerplate answers that pass review keep their high scores. Unreviewed answers are capped at 0.6 (yellow zone).

### Fixed: Base info truncation removed (`agents.py`)
- **What:** Removed `[:3000]` character truncation on base info text in `match_and_fill()`.
- **Why:** 7 of 8 base info files exceed 3000 chars. The largest (Data & InfoSec, 10,402 chars) was losing 71% of its content. With 1-question-per-API-call architecture and a 200K context window, we use ~5K tokens per call — truncation was a premature optimization that silently dropped critical content (e.g., SOC 2 details, GDPR specifics).
- **Impact:** All base info categories now send full content to Claude. Adds ~$0.70 total cost per 100-question RFI.

---

## 2026-05-11

### Updated: PROJECT-TRACKER.md — synced with actual progress
- **What:** Updated all task statuses, resolved 5 of 6 action items, updated progress summary from 0% to 67%, and rewrote daily log.
- **Why:** Tracker was created showing 0/21 tasks and 6 open decisions, but the codebase had already been scaffolded with most Phase 1 and Phase 2 work done. Decisions A1 (DB tier), A3 (framework), A4 (agent count), A5 (embeddings), A6 (LLM) were implicitly resolved through implementation.
- **Impact:** Tracker now reflects reality: 14/21 tasks done, 5/6 decisions resolved. Only A2 (Legal base info doc) remains open.

### Updated: PRD.md — corrected architecture to match code
- **What:** 
  1. Relabeled Parser and Writer from "Agents" to "Modules (Python — no LLM)"
  2. Corrected agent count from 4 to "3 LLM agents + 2 Python modules"
  3. Updated architecture diagram: replaced "ChromaDB Dual Index" with "Knowledge Base (JSON / TBD)"
  4. Rewrote Agent 2 (Matcher+Filler) description to match actual implementation (category-filtered JSON, one API call per question, no vector search)
  5. Added Agent 3 (Reviewer) as separate from Writer
  6. Added ⚠️ v1 gap callout: no vector similarity search in current implementation
  7. Converted "Open Decisions" → "Decisions Log" with all 5 decisions marked resolved
- **Why:** PRD described a system (ChromaDB, embeddings, similarity thresholds, 4 LLM agents) that doesn't match what was built (JSON lookup, no embeddings, 3 LLM agents + 2 Python modules). Docs and code must agree.
- **Impact:** PRD now accurately describes the v1 system. Gaps are explicitly called out rather than hidden behind aspirational language.

### Added: Azure AI Search docs section (`knowledge-retrieval-strategy.md`)
- **What:** Added "Docs to Send Your Engineers" section with 6 verified Azure AI Search links + key takeaways for this project. Added a 5th open question about migrating to Azure AI Search for v2.
- **Why:** Team needs reference material for evaluating Azure AI Search as the v2 retrieval backend, especially the remote SharePoint knowledge source (queries SharePoint directly without indexing).
- **Impact:** Engineers have a curated reading list. Links verified live as of 2026-05-11.
