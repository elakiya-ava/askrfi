# Knowledge Retrieval Strategy — RFI Auto-Fill System

> Research comparing Vector RAG, Graph RAG, and Fine-Tuning for an RFI auto-fill system at Avalere Health.  
> Date: 2026-05-11

---

## Executive Summary

| Approach | Dev Effort | Quality (20 docs) | Risk | Verdict |
|---|---|---|---|---|
| **Vector Store RAG** | 9–15 days | 7/10 | Low | Foundation of v1 |
| **Graph RAG** | 14–23 days | 7/10 (overkill) | Medium | Defer to v3 (200+ RFIs) |
| **Fine-Tuning** | 15–24 days | 5/10 | **HIGH** | **Do not pursue** |
| **Hybrid (RAG + Classifier + Dual-Index)** | 12–18 days | **8/10** | Low | **✅ Recommended** |

**Recommended approach for v1:** Hybrid Vector RAG with a dual-index strategy in ChromaDB, paired with a Claude zero-shot category classifier.

---

## 1. Vector Store RAG (ChromaDB / FAISS)

### How It Works for This Use Case
1. Extract Q&A pairs from all 20 filled RFIs using `openpyxl`
2. Embed questions and answers separately using an embedding model
3. Store in ChromaDB (local, no server needed) with metadata (client, category, source file)
4. At query time: embed new question → retrieve top-k similar past questions → use matched answers as context for Claude to generate/adapt an answer

### Chunking Strategy
- **Q&A pairs:** keep as atomic units (question + answer = 1 chunk). Do NOT split Q&A across chunks.
- **Base info docs:** chunk by section/paragraph (~200–500 tokens each)
- **Metadata per chunk:** category tag, client name, source filename, sheet name, row number

### Embedding Model Options
| Model | Cost | Quality | Latency | Notes |
|---|---|---|---|---|
| Voyage AI `voyage-3-large` | $0.00013/1k tokens | Best for retrieval | API call | Claude-optimized |
| OpenAI `text-embedding-3-large` | $0.00013/1k tokens | Strong | API call | Good alternative |
| `all-MiniLM-L6-v2` (local) | Free | Good enough | Instant | No API dependency |

### Pros
- Simple to implement and debug
- Low cost (~$5–25/month for 10 RFIs processed)
- Works well for short question matching (RFI questions are 1–2 sentences)
- Easy to add new RFIs incrementally (just embed and append)
- Grounded answers — always citing a source

### Cons
- Pure semantic similarity can miss structurally similar but differently worded questions
- No relationship reasoning between entities (e.g., linking certifications to policies)
- Category filtering needs a separate classifier layer
- Retrieval quality degrades without good metadata filtering

### Implementation Effort: 9–15 dev-days

---

## 2. Graph RAG (Microsoft GraphRAG / Neo4j + LLM)

### How It Works
1. Extract entities from RFI Q&A pairs (company names, policies, certifications, processes, regions)
2. Build a knowledge graph: entities as nodes, relationships as edges
3. Run community detection to find clusters of related Q&A
4. At query time: identify relevant entities in the new question → traverse the graph → retrieve connected answers and context

### When Graph RAG Outperforms Vector RAG
- **Multi-hop reasoning:** "What certifications does Avalere hold that are relevant to EU data handling for pharma clients?"
- **Cross-document synthesis:** combining partial answers from 3 different past RFIs
- **Entity disambiguation:** distinguishing "SOC 2" from "SOC 2 Type II" or "GDPR compliance" from "GDPR certification"
- **Consistency enforcement:** ensuring all answers about the same entity are aligned

### Why Defer to v3
- **20 RFIs ≈ 500–1000 Q&A pairs** — far below the threshold where graph structure adds value
- **Microsoft GraphRAG** is tightly coupled to OpenAI (entity extraction, summarization prompts); adapting to Claude adds friction
- **10–50x more expensive indexing** than vector RAG (every entity extraction = LLM call)
- The real value emerges at **100+ documents** where multi-hop reasoning across many sources matters
- **Neo4j adds operational complexity** (database server, schema management)

### Pros
- Superior for complex, cross-referencing queries
- Better at maintaining consistency across answers
- Rich entity-relationship model enables "explain why" capabilities

### Cons
- Massive overkill for 20 documents
- High setup cost (14–23 dev-days)
- OpenAI-centric tooling requires adaptation for Claude
- Maintenance burden: graph schema evolves as new question types appear

### Implementation Effort: 14–23 dev-days

---

## 3. Fine-Tuning

### Critical Blockers
| Issue | Impact |
|---|---|
| **Claude cannot be fine-tuned** | No public fine-tuning API from Anthropic. Full stop. |
| **20 documents ≈ 500–1000 Q&A pairs** | Borderline insufficient for meaningful fine-tuning |
| **Compliance-sensitive content** | Hallucination risk is unacceptable for healthcare RFIs |

### If We Tried Anyway (with a different model)
- **OpenAI GPT-4o-mini fine-tuning:** available but requires reformatting all data as chat completions; $25+ per training run
- **Open-source (Llama 3, Mistral):** requires GPU infrastructure ($100+/month), LoRA/QLoRA setup, evaluation pipeline
- **Re-training needed** every time new RFIs are added (fragile pipeline)
- **No grounding:** fine-tuned models generate from learned patterns, not from cited sources — dangerous for compliance answers

### Why Not
1. Claude can't be fine-tuned — and we're committed to Claude
2. RAG provides grounding that fine-tuning fundamentally cannot
3. 20 docs is insufficient training data — likely to overfit or hallucinate
4. Maintenance nightmare: re-train on every new RFI batch
5. For compliance content, you MUST be able to trace answers to sources

### Implementation Effort: 15–24 dev-days (and the result is worse)

---

## 4. Recommended Hybrid: Vector RAG + Category Classifier + Dual-Index

### Architecture

```
                    ┌─────────────────────────┐
                    │   New RFI Question       │
                    └──────────┬──────────────┘
                               │
                    ┌──────────▼──────────────┐
                    │  Claude Category         │
                    │  Classifier (zero-shot)  │
                    └──────────┬──────────────┘
                               │ category tag
                    ┌──────────▼──────────────┐
              ┌─────┤  ChromaDB Dual Index     ├─────┐
              │     └─────────────────────────┘     │
    ┌─────────▼─────────┐              ┌────────────▼────────┐
    │  Question Index    │              │  Knowledge Index    │
    │  (past RFI Q&As)  │              │  (base info chunks) │
    │  filtered by       │              │  filtered by        │
    │  category + client │              │  category           │
    └─────────┬─────────┘              └────────────┬────────┘
              │ matched answers                     │ relevant facts
              └──────────┬──────────────────────────┘
                         │
              ┌──────────▼──────────────┐
              │  Decision Logic          │
              │                          │
              │  similarity > 0.85 →     │
              │    use past answer       │
              │                          │
              │  similarity 0.5–0.85 →   │
              │    Claude adapts answer  │
              │                          │
              │  similarity < 0.5 →      │
              │    Claude synthesizes    │
              │    from knowledge base   │
              │                          │
              │  no relevant context →   │
              │    flag for human        │
              └──────────┬──────────────┘
                         │
              ┌──────────▼──────────────┐
              │  Answer + Confidence     │
              │  Score (0.0–1.0)         │
              └─────────────────────────┘
```

### Dual-Index Strategy

**Question Index:**
- Contains: every question extracted from the 20 past RFIs
- Embedding: the question text only
- Metadata: `{client, category, source_file, sheet, row, answer_text}`
- Purpose: find past questions similar to the new question, then retrieve the stored answer

**Knowledge Index:**
- Contains: chunks from the 8 base info documents (Company Info, Compliance, Legal, Data/InfoSec, ESG, People, Suppliers, Tech/AI)
- Embedding: section/paragraph text
- Metadata: `{category, source_doc, section_heading}`
- Purpose: ground answers in canonical Avalere information when no past RFI match exists

### Why This Combination Works
1. **Category classifier filters** reduce search space → better retrieval precision
2. **Dual index** means high-confidence matches come from real past answers (trustworthy), while knowledge base fills gaps
3. **Threshold-based routing** keeps Claude out of the loop for exact matches → lower cost, higher speed
4. **Confidence scores** are derived from similarity scores + Claude's self-assessment → meaningful flagging

### Cost Estimate
| Component | Monthly Cost (10 RFIs) |
|---|---|
| ChromaDB | Free (local) |
| Embeddings (local) | Free |
| Embeddings (Voyage AI) | ~$0.50 |
| Claude API calls | ~$5–20 |
| **Total** | **~$5–25/month** |

### Implementation Effort: 12–18 dev-days

---

## Evolution Path

```
v1 (Now, 20 RFIs)              v2 (50+ RFIs)               v3 (200+ RFIs)
────────────────────            ─────────────────            ─────────────────
Vector RAG (ChromaDB)           + Answer caching             + Entity Graph (Neo4j)
+ Category Classifier           + Feedback loop              + GraphRAG layer
+ Dual Index                    + Entity tagging             + Consistency checker
+ Local embeddings              + Reranking (Cohere/Voyage)  + Cross-doc synthesis
                                + Voyage AI embeddings       + Auto-updating base info
```

---

## Libraries Required (v1)

| Library | Purpose |
|---|---|
| `chromadb` | Local vector store |
| `sentence-transformers` | Local embeddings (all-MiniLM-L6-v2) |
| `anthropic` | Claude API |
| `openpyxl` | Excel read/write |
| `pymupdf` | PDF parsing (base info) |
| `beautifulsoup4` | HTML parsing (base info) |
| `pandas` | Data manipulation |
| `click` | CLI framework |
| `python-dotenv` | Environment variables |

---

## Docs to Send Your Engineers — Azure AI Search

If the team decides to move to Azure AI Search (v2+), start here:

| # | Resource | What It Covers | Link |
|---|---|---|---|
| 1 | **Azure AI Search docs hub** | Main landing page — links to quickstarts, concepts, REST/SDK docs | [learn.microsoft.com/en-us/azure/search/](https://learn.microsoft.com/en-us/azure/search/) |
| 2 | **What is Azure AI Search?** | Service overview, classic search vs agentic retrieval (RAG), architecture diagrams | [learn.microsoft.com/en-us/azure/search/search-what-is-azure-search](https://learn.microsoft.com/en-us/azure/search/search-what-is-azure-search) |
| 3 | **Feature list (vectors, hybrid, semantic)** | Concrete capabilities: HNSW, hybrid queries, semantic ranker, agentic retrieval, indexing, security | [learn.microsoft.com/en-us/azure/search/search-features-list](https://learn.microsoft.com/en-us/azure/search/search-features-list) |
| 4 | **Azure AI Search product page** | Concise "what it is" and where it fits (vector DB, RAG, Foundry IQ, pricing) | [azure.microsoft.com/en-us/products/ai-services/ai-search](https://azure.microsoft.com/en-us/products/ai-services/ai-search) |
| 5 | **SharePoint knowledge source (remote, for agentic RAG)** | Wiring SharePoint into agentic retrieval — no index needed, queries SharePoint directly via Copilot Retrieval API | [learn.microsoft.com/en-us/azure/search/agentic-knowledge-source-how-to-sharepoint-remote](https://learn.microsoft.com/en-us/azure/search/agentic-knowledge-source-how-to-sharepoint-remote) |
| 6 | **RAG and indexes in Azure** | How RAG is modeled around indexes/knowledge bases, choosing between agentic retrieval and classic RAG | [learn.microsoft.com/en-us/azure/search/retrieval-augmented-generation-overview](https://learn.microsoft.com/en-us/azure/search/retrieval-augmented-generation-overview) |

### Key Takeaways for This Project

- **Agentic retrieval (preview)** is the new model — it uses an LLM for query planning, multi-source access, and parallel subqueries. Designed for agent-to-agent workflows (exactly our use case).
- **Remote SharePoint knowledge source** can query SharePoint directly without indexing — relevant if Avalere's base info lives in SharePoint and needs to stay fresh.
- **Hybrid search** (keyword + vector) with **semantic ranker** is the recommended combination for maximum recall — maps to our dual-index concept.
- **Python SDK:** `azure-search-documents` (preview package needed for agentic retrieval features).
- **Pricing:** Free tier available for dev/test; Basic tier ($70/mo) for production. Embeddings can be handled by Azure OpenAI or brought from any model.

---

## Open Questions for v2+
1. Should we add a **feedback loop** where consultants correct answers and those corrections update the vector store?
2. At what RFI count do we invest in **Voyage AI** embeddings over local?
3. When do we add a **reranking** step (Cohere Rerank or Voyage Rerank)?
4. Should the Knowledge Index be **auto-refreshed** when SharePoint base info pages are updated?
5. Should we migrate to **Azure AI Search agentic retrieval** instead of building our own retrieval logic? (See docs above.)
