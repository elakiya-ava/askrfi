"""
Past RFI indexer — parses all filled RFIs and indexes them for retrieval.

v1: structured JSON knowledge base (no vector store)
v2: Azure AI Search with hybrid search (keyword + vector + semantic ranker)

When Azure AI Search is configured (env vars set), the indexer:
1. Builds the JSON knowledge base (still used as fallback)
2. Embeds all Q&A pairs via Azure OpenAI
3. Uploads documents to the Azure AI Search question index
4. Chunks and uploads base info to the knowledge index
"""

from __future__ import annotations

import json
import os
from collections import defaultdict
from dataclasses import asdict

from excel_parser import parse_rfi, extract_client_from_filename, RFIQuestion


def _azure_configured() -> bool:
    """Check if Azure AI Search env vars are set."""
    return bool(
        os.environ.get("AZURE_SEARCH_ENDPOINT")
        and os.environ.get("AZURE_SEARCH_API_KEY")
        and os.environ.get("AZURE_OPENAI_ENDPOINT")
        and os.environ.get("AZURE_OPENAI_API_KEY")
    )


def _index_to_azure(knowledge_base: dict, base_info_dir: str) -> None:
    """
    Push Q&A pairs and base info chunks to Azure AI Search indexes.
    Creates indexes if they don't exist.
    """
    from azure.search.documents import SearchClient
    from azure.search.documents.indexes import SearchIndexClient
    from azure.search.documents.indexes.models import (
        SearchIndex,
        SimpleField,
        SearchableField,
        SearchField,
        SearchFieldDataType,
        VectorSearch,
        HnswAlgorithmConfiguration,
        VectorSearchProfile,
        ScoringProfile,
        TextWeights,
        ScoringFunction,
        TagScoringFunction,
        TagScoringParameters,
    )
    from azure.core.credentials import AzureKeyCredential
    from openai import AzureOpenAI

    endpoint = os.environ["AZURE_SEARCH_ENDPOINT"]
    api_key = os.environ["AZURE_SEARCH_API_KEY"]
    credential = AzureKeyCredential(api_key)
    question_index = os.environ.get("AZURE_SEARCH_QUESTION_INDEX", "rfi-questions")
    knowledge_index = os.environ.get("AZURE_SEARCH_KNOWLEDGE_INDEX", "rfi-knowledge")

    aoai = AzureOpenAI(
        azure_endpoint=os.environ["AZURE_OPENAI_ENDPOINT"],
        api_key=os.environ["AZURE_OPENAI_API_KEY"],
        api_version=os.environ.get("AZURE_OPENAI_API_VERSION", "2024-02-01"),
    )
    embedding_model = os.environ.get("AZURE_OPENAI_EMBEDDING_DEPLOYMENT", "text-embedding-ada-002")

    def embed(text: str) -> list[float]:
        response = aoai.embeddings.create(input=[text], model=embedding_model)
        return response.data[0].embedding

    # ── Detect embedding dimensions from a test call ──
    test_embedding = embed("test")
    dims = len(test_embedding)
    print(f"  Embedding dimensions: {dims}")

    index_client = SearchIndexClient(endpoint=endpoint, credential=credential)

    # ── Create question index ──
    vector_search = VectorSearch(
        algorithms=[HnswAlgorithmConfiguration(name="hnsw-config")],
        profiles=[VectorSearchProfile(name="vector-profile", algorithm_configuration_name="hnsw-config")],
    )

    question_fields = [
        SimpleField(name="id", type=SearchFieldDataType.String, key=True),
        SearchableField(name="question_text", type=SearchFieldDataType.String),
        SearchableField(name="answer_text", type=SearchFieldDataType.String),
        SimpleField(name="category", type=SearchFieldDataType.String, filterable=True),
        SimpleField(name="client", type=SearchFieldDataType.String, filterable=True),
        SimpleField(name="source_file", type=SearchFieldDataType.String),
        SimpleField(name="sheet_name", type=SearchFieldDataType.String),
        SearchField(
            name="embedding",
            type=SearchFieldDataType.Collection(SearchFieldDataType.Single),
            searchable=True,
            vector_search_dimensions=dims,
            vector_search_profile_name="vector-profile",
        ),
    ]

    scoring_profile = ScoringProfile(
        name="client-boost",
        text_weights=TextWeights(weights={"question_text": 2, "answer_text": 1}),
        functions=[
            TagScoringFunction(
                field_name="client",
                boost=3,
                parameters=TagScoringParameters(tags_parameter="clientName"),
            ),
        ],
    )

    question_idx = SearchIndex(
        name=question_index,
        fields=question_fields,
        vector_search=vector_search,
        scoring_profiles=[scoring_profile],
    )

    print(f"  Creating/updating index: {question_index}")
    index_client.create_or_update_index(question_idx)

    # ── Create knowledge index ──
    knowledge_fields = [
        SimpleField(name="id", type=SearchFieldDataType.String, key=True),
        SearchableField(name="content", type=SearchFieldDataType.String),
        SimpleField(name="category", type=SearchFieldDataType.String, filterable=True),
        SimpleField(name="source_doc", type=SearchFieldDataType.String),
        SimpleField(name="section_heading", type=SearchFieldDataType.String),
        SearchField(
            name="embedding",
            type=SearchFieldDataType.Collection(SearchFieldDataType.Single),
            searchable=True,
            vector_search_dimensions=dims,
            vector_search_profile_name="vector-profile",
        ),
    ]

    knowledge_idx = SearchIndex(
        name=knowledge_index,
        fields=knowledge_fields,
        vector_search=vector_search,
    )

    print(f"  Creating/updating index: {knowledge_index}")
    index_client.create_or_update_index(knowledge_idx)

    # ── Upload Q&A pairs ──
    question_client = SearchClient(endpoint=endpoint, index_name=question_index, credential=credential)

    docs = []
    for cat, qas in knowledge_base.get("by_category", {}).items():
        for i, qa in enumerate(qas):
            doc_id = f"{cat.replace(' ', '_').lower()}_{i}"
            docs.append({
                "id": doc_id,
                "question_text": qa["question"],
                "answer_text": qa["answer"],
                "category": cat,
                "client": qa.get("client", ""),
                "source_file": qa.get("source", ""),
                "sheet_name": qa.get("sheet", ""),
                "embedding": embed(qa["question"]),
            })

            if len(docs) % 25 == 0:
                print(f"  Embedded {len(docs)} Q&A pairs...", end="\r")

    print(f"  Uploading {len(docs)} Q&A pairs to {question_index}...")
    # Upload in batches of 100 (Azure limit is 1000 per batch)
    for batch_start in range(0, len(docs), 100):
        batch = docs[batch_start:batch_start + 100]
        question_client.upload_documents(batch)
    print(f"  Uploaded {len(docs)} Q&A pairs.")

    # ── Chunk and upload base info ──
    if os.path.isdir(base_info_dir):
        knowledge_client = SearchClient(endpoint=endpoint, index_name=knowledge_index, credential=credential)
        base_docs = []

        # Map filenames to categories
        filename_to_category = {
            "Company Information": "Company Information",
            "Commercial Information (General)": "Commercial Information",
            "Compliance": "Compliance",
            "Data, information security, and client confidentiality": "Data & Information Security",
            "Environmental, social, and governance": "ESG",
            "People Information": "People Information",
            "Suppliers and freelancers": "Suppliers & Freelancers",
            "Technology and AI": "Technology & AI",
        }

        for fname in os.listdir(base_info_dir):
            if not fname.endswith(".txt"):
                continue

            name = os.path.splitext(fname)[0]
            category = filename_to_category.get(name, "Uncategorized")

            with open(os.path.join(base_info_dir, fname), "r", encoding="utf-8") as f:
                full_text = f.read()

            # Chunk by paragraph (double newline) — keep chunks 200-800 tokens
            paragraphs = [p.strip() for p in full_text.split("\n\n") if p.strip()]
            current_chunk = ""
            chunk_idx = 0

            for para in paragraphs:
                # If adding this paragraph would exceed ~800 tokens (~3200 chars), flush
                if current_chunk and len(current_chunk) + len(para) > 3200:
                    doc_id = f"base_{name.replace(' ', '_').lower()}_{chunk_idx}"
                    base_docs.append({
                        "id": doc_id,
                        "content": current_chunk,
                        "category": category,
                        "source_doc": name,
                        "section_heading": "",
                        "embedding": embed(current_chunk),
                    })
                    chunk_idx += 1
                    current_chunk = para
                else:
                    current_chunk = f"{current_chunk}\n\n{para}" if current_chunk else para

            # Flush remaining
            if current_chunk:
                doc_id = f"base_{name.replace(' ', '_').lower()}_{chunk_idx}"
                base_docs.append({
                    "id": doc_id,
                    "content": current_chunk,
                    "category": category,
                    "source_doc": name,
                    "section_heading": "",
                    "embedding": embed(current_chunk),
                })

        print(f"  Uploading {len(base_docs)} base info chunks to {knowledge_index}...")
        for batch_start in range(0, len(base_docs), 100):
            batch = base_docs[batch_start:batch_start + 100]
            knowledge_client.upload_documents(batch)
        print(f"  Uploaded {len(base_docs)} base info chunks.")


def index_all_rfis(rfi_dir: str, output_path: str) -> dict:
    """
    Parse all RFI Excel files in rfi_dir and build a structured knowledge base.
    Saves to output_path as JSON.

    Structure:
    {
        "rfis": [
            {
                "filename": "...",
                "client": "Pfizer",
                "questions": [ {sheet, row, question_text, existing_answer, category_hint, ...} ]
            }
        ],
        "by_category": {
            "Company Information": [ {question_text, answer, client, source} ],
            ...
        },
        "stats": { "total_rfis": N, "total_questions": N, "by_category": {...} }
    }
    """
    rfis = []
    by_category = defaultdict(list)
    total_questions = 0

    files = [f for f in os.listdir(rfi_dir) if f.endswith((".xlsx", ".xlsm"))]
    print(f"Indexing {len(files)} RFI files...")

    for fname in sorted(files):
        filepath = os.path.join(rfi_dir, fname)
        client = extract_client_from_filename(fname)

        try:
            questions = parse_rfi(filepath)
        except Exception as e:
            print(f"  ERROR parsing {fname}: {e}")
            continue

        if not questions:
            print(f"  WARN: {fname} — no questions extracted")
            continue

        rfi_entry = {
            "filename": fname,
            "client": client,
            "question_count": len(questions),
            "questions": [asdict(q) for q in questions],
        }
        rfis.append(rfi_entry)
        total_questions += len(questions)

        # Index by category for fast lookup
        for q in questions:
            cat = q.category_hint or "Uncategorized"
            if q.existing_answer:  # Only index Q&A pairs that have answers
                by_category[cat].append({
                    "question": q.question_text,
                    "answer": q.existing_answer,
                    "client": client,
                    "source": fname,
                    "sheet": q.sheet_name,
                })

        print(f"  {fname[:55]:55s} | {client or '?':15s} | {len(questions):3d} Qs")

    # Build stats
    cat_stats = {cat: len(items) for cat, items in by_category.items()}

    knowledge_base = {
        "rfis": rfis,
        "by_category": dict(by_category),
        "stats": {
            "total_rfis": len(rfis),
            "total_questions": total_questions,
            "total_answered": sum(cat_stats.values()),
            "by_category": cat_stats,
        },
    }

    # Save
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(knowledge_base, f, indent=2, ensure_ascii=False)

    print(f"\nDone. Indexed {len(rfis)} RFIs, {total_questions} questions total.")
    print(f"  Answered Q&As by category:")
    for cat, count in sorted(cat_stats.items(), key=lambda x: -x[1]):
        print(f"    {cat}: {count}")
    print(f"  Saved to {output_path}")

    # ── Azure AI Search: embed and upload ──
    if _azure_configured():
        print()
        print("=" * 60)
        print("Step 3: Uploading to Azure AI Search...")
        print("=" * 60)
        base_info_dir = os.path.join(os.path.dirname(output_path), "base_info")
        _index_to_azure(knowledge_base, base_info_dir)
        print("Azure AI Search indexing complete.")
    else:
        print("\n  Azure AI Search not configured — using JSON fallback only.")
        print("  To enable: fill in Azure vars in .env (see .env.example)")

    return knowledge_base


if __name__ == "__main__":
    script_dir = os.path.dirname(os.path.abspath(__file__))
    rfi_dir = os.path.join(script_dir, "..")
    output_path = os.path.join(script_dir, "data", "knowledge_base.json")
    index_all_rfis(rfi_dir, output_path)
