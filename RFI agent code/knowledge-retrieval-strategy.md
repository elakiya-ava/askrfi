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

**Recommended approach for v1:** Hybrid Vector RAG with a dual-index strategy (ChromaDB or MongoDB), paired with an LLM zero-shot category classifier.

**v2+ opportunity:** Azure AI Search + Microsoft Graph API could replace the custom vector store + SharePoint sync with a single managed service (see §7).

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

## 5. Vector RAG vs Graph RAG — Head-to-Head for RFI Processing

### What Each Actually Does

| | Vector RAG (Live Processing) | Graph RAG |
|---|---|---|
| **Core operation** | Embed text → search by cosine similarity | Extract entities → build graph → traverse relationships |
| **Index time** | Fast (~seconds per doc). Parse → embed → store. | **Slow** (~minutes per doc). Every entity extraction = LLM call. |
| **Query time** | Fast (~100–500ms per question) | Varies: simple queries are slower (graph overhead); complex multi-hop queries can be faster because relationships are pre-computed |
| **What it's great at** | "Find a past question similar to this one" | "What certifications does Avalere hold that apply to EU pharma data handling?" (traverses cert → regulation → region) |
| **What it struggles with** | Cross-referencing across multiple documents; consistency checking | Simple lookup questions (overkill; adds latency for no benefit) |

### When Each Wins

| Question Type | Vector RAG | Graph RAG | Winner |
|---|---|---|---|
| "What is your company address?" | ✅ Instant match | ⚠️ Unnecessary traversal | Vector RAG |
| "Describe your GDPR compliance measures" | ✅ Good match from past answers | ✅ Can link policy → certification → audit | Tie |
| "How do your data handling practices differ for EU vs US clients?" | ⚠️ May return partial results from separate docs | ✅ Traverses region → policy → certification relationships | Graph RAG |
| "List all certifications relevant to this client's industry" | ⚠️ Returns individual cert mentions | ✅ Pre-built entity graph of all certifications | Graph RAG |
| Boilerplate (60-70% of RFI questions) | ✅ Perfect — high-similarity match | ⚠️ Massive overkill | Vector RAG |

### The Volume Question

```
RFI Count:    20          50          100         200+
              │           │           │           │
Vector RAG:   ████████    ████████    ███████     ██████
              excellent   excellent   good        noise increases

Graph RAG:    ██          ████        ███████     █████████
              overkill    starting    good ROI    excellent
                          to add
                          value
```

**At 20 RFIs:** Vector RAG handles 90%+ of questions well. Graph RAG adds cost and complexity without meaningful quality improvement.

**At 100+ RFIs:** Vector search starts returning noisier results (more similar-but-wrong matches). Graph RAG's entity disambiguation and relationship traversal starts paying off.

**At 200+ RFIs:** Graph RAG significantly outperforms on cross-referencing, consistency, and complex queries. Vector RAG still handles boilerplate well.

### Recommendation: Sequential, Not Competitive

These are **not competing approaches** — they're layers:

```
v1: Vector RAG alone (handles 85% of questions)
v2: Vector RAG + entity tagging (handles 90%)
v3: Vector RAG + Graph RAG hybrid (handles 95%+)
    └── Vector RAG for simple matching
    └── Graph RAG for cross-referencing and consistency
```

---

## 6. Database Comparison — MongoDB vs ChromaDB vs Others

> **Context:** The team has MongoDB available. ChromaDB was the original recommendation. This section compares options.

### Comparison Matrix

| Feature | MongoDB Atlas Vector Search | ChromaDB | FAISS | MongoDB Community (local) |
|---|---|---|---|---|
| **Vector search** | ✅ `$vectorSearch` aggregation (Atlas M10+ clusters) | ✅ Built-in | ✅ Built-in | ❌ Not available |
| **Metadata filtering** | ✅ Full MongoDB query language | ✅ Basic `where` filters | ❌ Manual filtering | ✅ Full query language |
| **Graph traversal** | ✅ `$graphLookup` (for Graph RAG later) | ❌ | ❌ | ✅ `$graphLookup` |
| **Persistence** | ✅ Cloud-managed | ✅ Local SQLite | ✅ Local file | ✅ Local files |
| **Setup complexity** | Medium (need Atlas cluster) | Low (pip install) | Low (pip install) | Low (local install) |
| **Scalability** | Excellent (cloud) | Good (local, up to ~1M vectors) | Excellent (optimized for scale) | Good |
| **Cost** | $57+/month (M10 cluster) | Free | Free | Free |
| **One DB for everything** | ✅ Vectors + metadata + graph + raw data | ❌ Vectors only | ❌ Vectors only | ⚠️ No vectors, but metadata + graph |
| **Future Graph RAG** | ✅ Native `$graphLookup` | ❌ Need separate Neo4j | ❌ Need separate Neo4j | ✅ Native `$graphLookup` |
| **Python library** | `pymongo` | `chromadb` | `faiss-cpu` | `pymongo` |

### Decision Tree

```
Do you have MongoDB Atlas (cloud, M10+ tier)?
├── YES → Use MongoDB Atlas Vector Search for everything
│         ✅ One database: vectors + metadata + future graph
│         ✅ No additional services to manage
│         ✅ `$graphLookup` ready for Graph RAG in v3
│         ⚠️ Cost: $57+/month for M10 cluster
│
├── NO (Community/local MongoDB) → Hybrid approach
│   │
│   ├── ChromaDB for vectors (free, local, simple)
│   │   + MongoDB for metadata, raw Q&A storage, future graph
│   │   ✅ Both free and local
│   │   ⚠️ Two data stores to manage
│   │
│   └── OR: FAISS for vectors (free, local, fastest)
│       + MongoDB for metadata
│       ✅ Fastest vector search
│       ⚠️ No built-in persistence (must manage index files)
│       ⚠️ No metadata filtering (must implement manually)
│
└── NOT SURE → Start with ChromaDB (zero config)
              → Migrate to MongoDB Atlas later when confirmed
              → ChromaDB → MongoDB migration is straightforward
```

### MongoDB Atlas Vector Search — How It Works

If you have Atlas, here's how the dual-index maps:

```javascript
// Question Index — a MongoDB collection
{
  _id: ObjectId,
  question_text: "What is your company's annual revenue?",
  answer_text: "Avalere Health's annual revenue is...",
  category: "commercial_information",
  client: "Pfizer",
  source_file: "Pfizer_RFI_2025.xlsx",
  sheet: "Company Info",
  row: 12,
  embedding: [0.023, -0.145, ...],  // 384-dim or 1536-dim vector
}

// Create vector search index
db.questions.createSearchIndex({
  name: "question_vector_index",
  type: "vectorSearch",
  definition: {
    fields: [{
      type: "vector",
      path: "embedding",
      numDimensions: 384,
      similarity: "cosine"
    }]
  }
})

// Query: find similar questions, filtered by category
db.questions.aggregate([
  {
    $vectorSearch: {
      index: "question_vector_index",
      path: "embedding",
      queryVector: [0.031, -0.128, ...],
      numCandidates: 50,
      limit: 5,
      filter: { category: "compliance" }
    }
  }
])
```

### MongoDB `$graphLookup` — Future Graph RAG Foundation

When you're ready for Graph RAG (v3), MongoDB can serve as both vector store AND graph database:

```javascript
// Entity collection (nodes in the graph)
{ _id: "cert_soc2", type: "certification", name: "SOC 2 Type II", ... }
{ _id: "reg_gdpr", type: "regulation", name: "GDPR", region: "EU", ... }

// Relationship collection (edges)
{ from: "cert_soc2", to: "reg_gdpr", relationship: "satisfies", ... }

// Graph traversal: find all certifications related to EU regulations
db.entities.aggregate([
  { $match: { type: "regulation", region: "EU" } },
  { $graphLookup: {
      from: "relationships",
      startWith: "$_id",
      connectFromField: "to",
      connectToField: "from",
      as: "related_certs",
      maxDepth: 2
  }}
])
```

This means **no Neo4j needed** — MongoDB handles both vectors and graphs.

### SharePoint → MongoDB Live Sync (v2/v3)

> **Note:** This approach is superseded by the Azure AI Search alternative in §7 if Avalere adopts that path. Keeping this section for the MongoDB-only architecture.

When SP API access is unblocked, the live sync architecture:

```
┌─────────────────┐     Webhook / Graph API     ┌──────────────────┐
│   SharePoint     │ ──────────────────────────▶ │  Azure Function  │
│   (base info     │    "page X was edited"      │  or Flask app    │
│    pages)        │                             └────────┬─────────┘
└─────────────────┘                                       │
                                                          │ 1. Fetch updated page
                                                          │ 2. Parse to text
                                                          │ 3. Re-chunk
                                                          │ 4. Re-embed
                                                          │ 5. Upsert to MongoDB
                                                          │
                                               ┌──────────▼──────────┐
                                               │   MongoDB Atlas      │
                                               │   Knowledge Index    │
                                               │   (auto-updated)     │
                                               └──────────────────────┘
```

**Requirements for this:**
- SharePoint Graph API access (blocked by InfoSec — ETA: weeks)
- Azure Function or equivalent serverless compute
- MongoDB Atlas (for vector search)
- A running service (not CLI) — but could be triggered by cron if webhooks aren't feasible

**Alternative (simpler):** Scheduled sync via cron — run every 4 hours, diff SP content against what's indexed, update only changed docs. No webhooks, no Azure Function.

---

## Evolution Path

```
v1 (Now, 20 RFIs)              v2 (50+ RFIs)               v3 (200+ RFIs)
────────────────────            ─────────────────            ─────────────────
Vector RAG                      + Answer caching             + Graph RAG layer
+ Category Classifier           + Feedback loop              + $graphLookup traversal
+ Dual Index                    + Entity tagging             + Consistency checker
+ Local embeddings              + Reranking (Cohere/Voyage)  + Cross-doc synthesis
+ ChromaDB or MongoDB           + SP scheduled sync          + SP webhook live sync
                                + Voyage AI embeddings       + Auto-updating base info

    ── OR (Azure path) ──       ── Azure v2 ──              ── Azure v3 ──
                                Azure AI Search              + Azure OpenAI GraphRAG
                                + Graph API SP connector     + Semantic ranker
                                + Managed embeddings         + Custom skills pipeline
                                + Hybrid search              + Multi-tenant indexes
                                (replaces ChromaDB/MongoDB
                                 + custom SP sync)
```

**Database migration path:**
- **If starting with ChromaDB:** migrate to MongoDB Atlas or Azure AI Search when ready (re-embed and insert)
- **If starting with MongoDB Atlas:** no migration needed — add `$graphLookup` collections for Graph RAG in v3
- **If MongoDB Community (local):** use ChromaDB for vectors now, plan Atlas or Azure AI Search migration for v2
- **Azure path:** migrate from ChromaDB → Azure AI Search when Graph API access is granted (see §7)

---

## 7. Azure AI Search + Microsoft Graph API — Managed Alternative

> **Status:** Avalere has Azure. Graph API access blocked by InfoSec (ETA: weeks). This is a v2 path.

### What Is Azure AI Search?

Azure AI Search (formerly Azure Cognitive Search) is a managed search service that provides:
- **Vector search** — store and query embeddings (replaces ChromaDB / MongoDB Atlas Vector Search)
- **Hybrid search** — keyword + semantic search in one query (better recall than pure vector)
- **Semantic ranker** — LLM-powered reranking of results (replaces Cohere/Voyage reranking)
- **Built-in chunking + embedding** — via integrated vectorizers (Azure OpenAI, Cohere, custom)
- **SharePoint connector** — crawls SharePoint sites automatically via Microsoft Graph API
- **Skillsets** — custom enrichment pipeline (entity extraction, language detection, PII redaction)

### How It Simplifies the Architecture

```
CURRENT PLAN (v1):                              AZURE AI SEARCH ALTERNATIVE (v2):
──────────────────                               ─────────────────────────────────

SharePoint pages                                 SharePoint pages
  │                                                │
  ▼                                                ▼
Manual HTML/PDF export                           Graph API connector (automatic)
  │                                                │
  ▼                                                ▼
Parse with pymupdf / bs4                         Azure AI Search indexer
  │                                              (auto-chunks, auto-embeds)
  ▼                                                │
Embed with sentence-transformers                   ▼
  │                                              Azure AI Search index
  ▼                                              (vector + keyword + metadata)
Store in ChromaDB / MongoDB                        │
  │                                                ▼
  ▼                                              Hybrid query (vector + keyword)
Semantic search (vector only)                    + Semantic ranker
  │                                                │
  ▼                                                ▼
LLM generates answer                            LLM generates answer

Components to manage: 4                          Components to manage: 1
(parser + embedder + vector DB + sync)           (Azure AI Search — everything else is managed)
```

### What You Get vs What You Lose

| | Custom (ChromaDB/MongoDB) | Azure AI Search |
|---|---|---|
| **SP auto-sync** | ❌ Must build custom sync | ✅ Built-in Graph API connector |
| **Vector search** | ✅ | ✅ |
| **Hybrid search** | ❌ Vector only | ✅ Vector + keyword + semantic |
| **Embedding management** | You manage model + pipeline | ✅ Integrated vectorizer |
| **Reranking** | ❌ Must add separately | ✅ Semantic ranker included |
| **Change detection** | ❌ Must build diff logic | ✅ Incremental indexing built-in |
| **Cost** | Free (local) / $57+/mo (Atlas) | Free tier (50MB) / Basic ~$75/mo / S1 ~$250/mo |
| **Vendor lock-in** | Low | Medium (Azure ecosystem) |
| **Control** | Full | Less (managed service) |
| **Graph RAG** | Must build separately | ❌ Not built-in (but can feed into custom Graph RAG) |
| **Offline/local use** | ✅ ChromaDB works offline | ❌ Requires internet |

### Azure AI Search Pricing

| Tier | Monthly Cost | Storage | Indexes | Good For |
|---|---|---|---|---|
| **Free** | $0 | 50 MB | 3 | Prototyping, testing |
| **Basic** | ~$75 | 2 GB | 15 | v2 with 50-100 RFIs |
| **Standard S1** | ~$250 | 25 GB | 50 | v3 with 200+ RFIs |

For 20 RFIs (~500-1000 Q&A pairs + base info), the **Free tier** is sufficient for testing. **Basic** covers v2.

### How the SharePoint Connector Works

```
┌─────────────────────┐
│   SharePoint Site    │
│   (ASK! RFI)        │
│                      │
│  ├── Base Info/      │    Microsoft Graph API     ┌──────────────────────┐
│  │   ├── Company.pdf │ ─────────────────────────▶ │  Azure AI Search     │
│  │   ├── Compliance  │    Indexer crawls every     │                      │
│  │   └── ...         │    N hours (configurable)   │  ┌────────────────┐  │
│  │                   │                             │  │ Knowledge Index │  │
│  └── RFI Library/    │    Auto-detects changes:    │  │ (base info)    │  │
│      ├── Pfizer.xlsx │    - New files              │  └────────────────┘  │
│      ├── Gilead.xlsx │    - Modified pages          │                      │
│      └── ...         │    - Deleted content         │  ┌────────────────┐  │
│                      │                             │  │ Question Index  │  │
└─────────────────────┘    Chunks + embeds            │  │ (past RFIs)    │  │
                           automatically              │  └────────────────┘  │
                                                      └──────────────────────┘
```

**Requirements:**
1. Azure subscription (✅ Avalere has this)
2. Microsoft Graph API access (⏳ InfoSec blocker — ETA: weeks)
3. Azure AI Search resource provisioned
4. SharePoint data source connection configured

### When to Migrate from ChromaDB/MongoDB to Azure AI Search

| Trigger | Action |
|---|---|
| Graph API access granted by InfoSec | Evaluate Azure AI Search Free tier |
| 50+ RFIs in library | Migrate to Azure AI Search Basic (hybrid search improves quality) |
| SharePoint base info changes frequently | Enable SP connector for auto-sync |
| Reranking needed | Enable semantic ranker (built-in) |

### Migration Path

```
v1 (ChromaDB, local)
  │
  │  Graph API access granted
  ▼
v2a: Test Azure AI Search Free tier
  │   - Create search index
  │   - Configure SP connector
  │   - Run side-by-side comparison vs ChromaDB
  │   - If quality ≥ ChromaDB: migrate
  │
  ▼
v2b: Azure AI Search Basic ($75/mo)
  │   - Full SP auto-sync
  │   - Hybrid search (vector + keyword)
  │   - Semantic ranker
  │   - Retire ChromaDB
  │
  ▼
v3: Azure AI Search S1 + Azure OpenAI
    - GraphRAG entity extraction via Azure OpenAI
    - Custom skillset for entity extraction during indexing
    - Multi-index architecture
```

### Python Integration

```python
# Azure AI Search Python SDK
from azure.search.documents import SearchClient
from azure.search.documents.models import VectorizedQuery
from azure.core.credentials import AzureKeyCredential

client = SearchClient(
    endpoint="https://<your-service>.search.windows.net",
    index_name="rfi-questions",
    credential=AzureKeyCredential("<api-key>")
)

# Hybrid search: vector + keyword + semantic ranker
results = client.search(
    search_text="What is your GDPR compliance policy?",     # keyword search
    vector_queries=[VectorizedQuery(
        vector=embedding,                                     # vector search
        k_nearest_neighbors=5,
        fields="embedding"
    )],
    filter="category eq 'data_security'",                    # metadata filter
    query_type="semantic",                                    # enable semantic ranker
    semantic_configuration_name="rfi-semantic-config",
    top=5
)
```

Libraries: `azure-search-documents`, `azure-identity`

## Libraries Required (v1)

| Library | Purpose |
|---|---|
| `chromadb` OR `pymongo` | Vector store (depends on MongoDB tier) |
| `sentence-transformers` | Local embeddings (all-MiniLM-L6-v2) |
| `anthropic` OR `openai` | LLM API (pending evaluation) |
| `openpyxl` | Excel read/write |
| `pymupdf` | PDF parsing (base info) |
| `beautifulsoup4` | HTML parsing (base info) |
| `pandas` | Data manipulation |
| `click` | CLI framework |
| `python-dotenv` | Environment variables |

---

## Open Questions for v2+
1. Should we add a **feedback loop** where consultants correct answers and those corrections update the vector store?
2. At what RFI count do we invest in **Voyage AI** embeddings over local?
3. When do we add a **reranking** step (Cohere Rerank or Voyage Rerank)?
4. Should the Knowledge Index be **auto-refreshed** when SharePoint base info pages are updated?
5. **MongoDB tier confirmation** — Atlas vs Community determines database architecture
6. **LLM evaluation** — Claude vs OpenAI head-to-head on 20 test questions from past RFIs
7. When SP API access is granted, **webhooks vs scheduled sync** for base info updates?
8. **Azure AI Search evaluation** — when Graph API access is granted, test Free tier side-by-side vs ChromaDB/MongoDB. Could replace custom vector store + SP sync + embedding pipeline with one managed service.
9. **Azure OpenAI vs direct OpenAI/Anthropic** — if on Azure, using Azure OpenAI for embeddings + generation keeps everything in-tenant (data residency, compliance).
