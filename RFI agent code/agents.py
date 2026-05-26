"""
RFI pipeline functions that process RFI questions.

match_and_fill    — retrieves similar past Q&A + generates answers via Claude
review_answers    — verifies consistency, validates citations, assigns confidence

Retrieval uses Azure AI Search (hybrid: keyword + vector + semantic ranker).
"""

from __future__ import annotations

import json
import os

from anthropic import Anthropic
from openai import AzureOpenAI
from azure.search.documents import SearchClient
from azure.search.documents.models import VectorizedQuery
from azure.core.credentials import AzureKeyCredential


def _get_client() -> Anthropic:
    return Anthropic()


# ─── AZURE AI SEARCH + EMBEDDING HELPERS ────────────────────────────────────

def _get_azure_openai_client() -> AzureOpenAI:
    return AzureOpenAI(
        azure_endpoint=os.environ["AZURE_OPENAI_ENDPOINT"],
        api_key=os.environ["AZURE_OPENAI_API_KEY"],
        api_version=os.environ.get("AZURE_OPENAI_API_VERSION", "2024-02-01"),
    )


def _get_search_client(index_name: str) -> SearchClient:
    return SearchClient(
        endpoint=os.environ["AZURE_SEARCH_ENDPOINT"],
        index_name=index_name,
        credential=AzureKeyCredential(os.environ["AZURE_SEARCH_API_KEY"]),
    )


def _embed(text: str, aoai_client: AzureOpenAI | None = None) -> list[float]:
    """Embed text using Azure OpenAI. Returns a vector."""
    aoai_client = aoai_client or _get_azure_openai_client()
    deployment = os.environ.get("AZURE_OPENAI_EMBEDDING_DEPLOYMENT", "text-embedding-ada-002")
    response = aoai_client.embeddings.create(input=[text], model=deployment)
    return response.data[0].embedding


_AZURE_REQUIRED_VARS = [
    "AZURE_SEARCH_ENDPOINT",
    "AZURE_SEARCH_API_KEY",
    "AZURE_OPENAI_ENDPOINT",
    "AZURE_OPENAI_API_KEY",
]


def _azure_configured() -> bool:
    """Check if Azure AI Search env vars are set. Raises if partially configured."""
    set_vars = [v for v in _AZURE_REQUIRED_VARS if os.environ.get(v)]
    if len(set_vars) == len(_AZURE_REQUIRED_VARS):
        return True
    if set_vars:
        missing = [v for v in _AZURE_REQUIRED_VARS if v not in set_vars]
        raise EnvironmentError(
            f"Azure AI Search is partially configured. "
            f"Set vars: {set_vars}. Missing: {missing}"
        )
    return False


# ─── RETRIEVAL (Azure AI Search) ────────────────────────────────────────────

def _find_similar_qas(
    question: str,
    client_name: str = "",
    max_results: int = 10,
) -> list[dict]:
    """
    Azure AI Search hybrid retrieval: keyword + vector + semantic ranker.
    No category filtering — relevance ranking handles topic matching.
    """
    aoai_client = _get_azure_openai_client()
    embedding = _embed(question, aoai_client)

    index_name = os.environ.get("AZURE_SEARCH_QUESTION_INDEX", "rfi-questions")
    search_client = _get_search_client(index_name)

    vector_query = VectorizedQuery(
        vector=embedding,
        k_nearest_neighbors=50,
        fields="embedding",
    )

    search_kwargs = {
        "search_text": question,
        "vector_queries": [vector_query],
        "top": max_results,
    }

    # Enable semantic ranker if configured (requires Standard tier)
    semantic_config = os.environ.get("AZURE_SEARCH_SEMANTIC_CONFIG")
    if semantic_config:
        search_kwargs["query_type"] = "semantic"
        search_kwargs["semantic_configuration_name"] = semantic_config

    # Enable client boost scoring profile if configured
    scoring_profile = os.environ.get("AZURE_SEARCH_SCORING_PROFILE")
    if scoring_profile and client_name:
        search_kwargs["scoring_profile"] = scoring_profile
        search_kwargs["scoring_parameters"] = [f"clientName-{client_name}"]

    results = search_client.search(**search_kwargs)

    output = []
    for r in results:
        result_client = r.get("client", "")
        output.append({
            "question": r["question_text"],
            "answer": r["answer_text"],
            "client": result_client,
            "source": r.get("source_file", ""),
            "match_type": "same_client" if client_name and result_client.lower() == client_name.lower() else "cross_client",
            "score": r.get("@search.score", 0.0),
        })

    return output


def _find_base_info(question: str) -> str:
    """
    Retrieve relevant base info chunks from Azure AI Search knowledge index.
    Pure semantic search — no category filtering.
    """
    if not _azure_configured():
        return ""

    index_name = os.environ.get("AZURE_SEARCH_KNOWLEDGE_INDEX", "rfi-knowledge")
    try:
        aoai_client = _get_azure_openai_client()
        embedding = _embed(question, aoai_client)
        search_client = _get_search_client(index_name)

        vector_query = VectorizedQuery(
            vector=embedding,
            k_nearest_neighbors=20,
            fields="embedding",
        )

        results = search_client.search(
            search_text=question,
            vector_queries=[vector_query],
            top=5,
        )

        chunks = []
        for r in results:
            heading = r.get("section_heading", "")
            source = r.get("source_doc", "")
            content = r.get("content", "")
            label = f"{source} > {heading}" if heading else source
            chunks.append(f"\n--- {label} ---\n{content}")

        return "\n".join(chunks) if chunks else ""

    except Exception as e:
        print(f"  WARN: Azure knowledge search failed: {e}")
        return ""


def match_and_fill(
    questions: list[dict],
    knowledge_base: dict,
    base_info: dict[str, str],
    client_name: str = "",
    client: Anthropic | None = None,
) -> list[dict]:
    """
    For each question, find similar past answers via Azure AI Search and generate
    a response with Claude. Adds 'generated_answer', 'confidence', 'citation',
    and 'source_references' to each question.
    """
    client = client or _get_client()

    batch_size = 5
    for start in range(0, len(questions), batch_size):
        batch = questions[start:start + batch_size]

        for q in batch:
            # Retrieve similar past Q&A pairs (no category filter)
            similar_qas = _find_similar_qas(q["question_text"], client_name)

            # Retrieve relevant base info
            base_info_text = _find_base_info(q["question_text"])

            # Build context for Claude
            past_qa_text = ""
            if similar_qas:
                past_qa_text = "\n\nPast Q&A pairs from similar RFIs:\n"
                for i, qa in enumerate(similar_qas):
                    past_qa_text += f"\n[Past Q{i+1} | Client: {qa['client']} | Source: {qa['source']}]\n"
                    past_qa_text += f"Q: {qa['question']}\n"
                    past_qa_text += f"A: {qa['answer']}\n"

            prompt = f"""You are filling out an RFI (Request for Information) for Avalere Health, a healthcare consulting firm.

QUESTION:
{q['question_text']}

{f"EXISTING ANSWER (from previous submission): {q.get('existing_answer', '')}" if q.get('existing_answer') else "No existing answer."}

{f"BASE COMPANY INFORMATION:{base_info_text}" if base_info_text else "No base info available."}
{past_qa_text if past_qa_text else "No similar past Q&A pairs found."}

INSTRUCTIONS:
1. If a past Q&A question is nearly identical to this question, reuse that past answer verbatim.
2. If similar (but not identical) past Q&A pairs are available, adapt the most relevant answer to fit this specific question.
3. If an existing answer is provided and is consistent with the base info and past Q&As, you may reuse it.
4. If base company information is available, use it to construct an accurate answer.
5. DO NOT fabricate or hallucinate any facts, statistics, certifications, policy details, or any specifics not present in the provided context. If the context does not contain enough information to answer confidently, respond with "[NEEDS REVIEW]" followed by your best attempt using only what is provided.
6. Keep the tone professional and consistent with a healthcare consulting firm.
7. Be concise but thorough. Match the expected level of detail.
8. Cite ALL sources used — include filename, section heading/sheet name where the information was found.

Respond with ONLY a JSON object:
{{
  "answer": "your answer text",
  "citation": "cite sources used — filename, section heading/sheet name, etc.",
  "sources": ["list of source identifiers used"]
}}
"""
            try:
                response = client.messages.create(
                    model="claude-sonnet-4-20250514",
                    max_tokens=2048,
                    temperature=0.1,
                    messages=[{"role": "user", "content": prompt}],
                )

                text = response.content[0].text.strip()
                if text.startswith("```"):
                    text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()

                result = json.loads(text)
                q["generated_answer"] = result.get("answer", "")
                q["confidence"] = 0.0  # Reviewer assigns final confidence
                q["citation"] = result.get("citation", "")
                q["source_references"] = result.get("sources", [])
                q["fill_status"] = "filled"

            except Exception as e:
                q["generated_answer"] = f"[ERROR] Could not generate answer: {e}"
                q["confidence"] = 0.0
                q["citation"] = ""
                q["source_references"] = []
                q["fill_status"] = "error"

        done = min(start + batch_size, len(questions))
        print(f"  Filled {done}/{len(questions)} questions", end="\r")

    print()
    return questions


# ─── AGENT 2: REVIEWER ───────────────────────────────────────────────────────

def _verify_citations_exist(question: dict) -> list[str]:
    """
    Cross-reference cited sources against Azure AI Search to verify they exist.
    Returns list of citations that could NOT be found.
    """
    missing = []
    sources = question.get("source_references", [])
    if not sources or not _azure_configured():
        return missing

    index_name = os.environ.get("AZURE_SEARCH_QUESTION_INDEX", "rfi-questions")
    try:
        search_client = _get_search_client(index_name)
        for source in sources:
            if not source or not source.strip():
                continue
            # Search for the cited source file
            results = search_client.search(
                search_text=source,
                top=1,
                select=["source_file"],
            )
            found = False
            for r in results:
                if source.lower() in (r.get("source_file", "") or "").lower():
                    found = True
                    break
            if not found:
                missing.append(source)
    except Exception:
        pass  # If search fails, skip citation verification

    return missing


def review_answers(
    questions: list[dict],
    client: Anthropic | None = None,
) -> list[dict]:
    """
    Review generated answers for:
    1. Consistency across all answers (no contradictions)
    2. Citation verification (sources exist and are correctly cited)
    3. Confidence scoring (reviewer assigns final 0.0–1.0 score)

    If citations are invalid or answer is unreliable, re-engages the filler
    model to regenerate with corrected context.

    Every answer gets:
      - 'confidence': 0.0–1.0 (assigned by reviewer)
      - 'review_status': "reviewed" | "flagged" | "regenerated" | "unreviewed"
    """
    client = client or _get_client()

    # Step 1: Verify citations exist for each answer
    for q in questions:
        if not q.get("generated_answer") or q.get("fill_status") == "error":
            q["review_status"] = "unreviewed"
            q["confidence"] = 0.0
            continue

        missing_citations = _verify_citations_exist(q)
        if missing_citations:
            q["citation_issues"] = missing_citations

    # Step 2: Send all answers to reviewer for consistency check + confidence scoring
    # Process in batches to stay within context limits
    batch_size = 20
    for start in range(0, len(questions), batch_size):
        batch = [q for q in questions[start:start + batch_size]
                 if q.get("generated_answer") and q.get("fill_status") != "error"]

        if not batch:
            continue

        qa_summary = ""
        for i, q in enumerate(batch):
            citation_warning = ""
            if q.get("citation_issues"):
                citation_warning = f"\n  ⚠ UNVERIFIED CITATIONS: {q['citation_issues']}"
            qa_summary += (
                f"\n[Q{i+1}] {q['question_text']}\n"
                f"  Answer: {q['generated_answer']}\n"
                f"  Cited sources: {q.get('citation', 'None')}{citation_warning}\n"
            )

        try:
            response = client.messages.create(
                model="claude-sonnet-4-20250514",
                max_tokens=4096,
                temperature=0,
                messages=[{
                    "role": "user",
                    "content": f"""You are a quality reviewer for RFI (Request for Information) responses prepared for Avalere Health, a healthcare consulting firm.

Review ALL of the following filled answers. For EACH answer, you must:

1. **Check consistency**: Look for contradictions between answers (e.g., different employee counts, inconsistent descriptions of services, conflicting dates or certifications).
2. **Validate citations**: If citations are marked as UNVERIFIED, or if the answer contains specific claims that don't match the cited source, flag it.
3. **Assess answer quality**: Is the answer complete, professional, and responsive to the question?
4. **Assign a confidence score (0.0–1.0)**:
   - 0.9–1.0: Answer is well-sourced, consistent, and complete
   - 0.7–0.8: Answer is good but may have minor gaps or slightly generic language
   - 0.5–0.6: Answer is usable but needs human review (vague, partially unsourced, or adapted from different context)
   - 0.3–0.4: Answer has issues — unverified citations, potential inaccuracies, or contradicts other answers
   - 0.0–0.2: Answer is unreliable — fabricated citations, clear contradictions, or [NEEDS REVIEW]

ANSWERS TO REVIEW:
{qa_summary}

Respond with ONLY a JSON object:
{{
  "reviews": [
    {{
      "question_index": 1,
      "confidence": 0.85,
      "status": "reviewed",
      "issues": null
    }},
    {{
      "question_index": 2,
      "confidence": 0.3,
      "status": "flagged",
      "issues": "Answer claims ISO 27001 certification but citation source does not mention it. Contradicts Q5 which says certification is in progress."
    }},
    {{
      "question_index": 3,
      "confidence": 0.1,
      "status": "regenerate",
      "issues": "Cited source does not exist. Answer appears fabricated."
    }}
  ]
}}

Status must be one of: "reviewed" (pass), "flagged" (usable but has concerns), "regenerate" (answer is unreliable and should be regenerated).
"""
                }],
            )

            text = response.content[0].text.strip()
            if text.startswith("```"):
                text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()

            review = json.loads(text)

            # Apply review results
            for item in review.get("reviews", []):
                q_idx = item.get("question_index", 0) - 1
                if 0 <= q_idx < len(batch):
                    q = batch[q_idx]
                    q["confidence"] = min(max(float(item.get("confidence", 0)), 0), 1.0)
                    status = item.get("status", "reviewed")

                    if status == "regenerate":
                        q["review_status"] = "regenerate"
                        q["review_flag"] = item.get("issues", "Flagged for regeneration")
                    elif status == "flagged":
                        q["review_status"] = "flagged"
                        q["review_flag"] = item.get("issues", "")
                    else:
                        q["review_status"] = "reviewed"

        except (json.JSONDecodeError, IndexError, KeyError, ValueError) as e:
            print(f"  WARN: Review parse error: {e}")
            for q in batch:
                q["review_status"] = "unreviewed"
                q["confidence"] = 0.5  # Neutral — couldn't verify

        except Exception as e:
            error_str = str(e).lower()
            if "auth" in error_str or "api key" in error_str:
                raise
            if "rate" in error_str and "limit" in error_str:
                raise

            print(f"  WARN: Review failed: {e}")
            for q in batch:
                q["review_status"] = "unreviewed"
                q["confidence"] = 0.5

    # Step 3: Re-engage filler for answers marked "regenerate"
    to_regenerate = [q for q in questions if q.get("review_status") == "regenerate"]
    if to_regenerate:
        print(f"  Regenerating {len(to_regenerate)} answers with reviewer feedback...")
        for q in to_regenerate:
            feedback = q.get("review_flag", "")
            similar_qas = _find_similar_qas(q["question_text"])
            base_info_text = _find_base_info(q["question_text"])

            past_qa_text = ""
            if similar_qas:
                past_qa_text = "\n\nPast Q&A pairs from similar RFIs:\n"
                for i, qa in enumerate(similar_qas):
                    past_qa_text += f"\n[Past Q{i+1} | Client: {qa['client']} | Source: {qa['source']}]\n"
                    past_qa_text += f"Q: {qa['question']}\n"
                    past_qa_text += f"A: {qa['answer']}\n"

            prompt = f"""You are filling out an RFI for Avalere Health. A previous answer was rejected by the reviewer.

QUESTION:
{q['question_text']}

REVIEWER FEEDBACK (why the previous answer was rejected):
{feedback}

PREVIOUS ANSWER (DO NOT reuse if citations were invalid):
{q['generated_answer']}

{f"BASE COMPANY INFORMATION:{base_info_text}" if base_info_text else "No base info available."}
{past_qa_text if past_qa_text else "No similar past Q&A pairs found."}

INSTRUCTIONS:
- Address the reviewer's concerns directly.
- Only cite sources that are provided in the context above.
- If you cannot answer reliably, respond with "[NEEDS REVIEW]" and explain what's missing.
- DO NOT fabricate citations or facts.

Respond with ONLY a JSON object:
{{
  "answer": "your corrected answer",
  "citation": "only cite sources from the provided context",
  "sources": ["list of actual sources used"]
}}
"""
            try:
                response = client.messages.create(
                    model="claude-sonnet-4-20250514",
                    max_tokens=2048,
                    temperature=0.1,
                    messages=[{"role": "user", "content": prompt}],
                )

                text = response.content[0].text.strip()
                if text.startswith("```"):
                    text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()

                result = json.loads(text)
                q["generated_answer"] = result.get("answer", q["generated_answer"])
                q["citation"] = result.get("citation", "")
                q["source_references"] = result.get("sources", [])
                q["review_status"] = "regenerated"
                q["confidence"] = 0.4  # Regenerated answers get conservative confidence

            except Exception as e:
                q["review_status"] = "flagged"
                q["review_flag"] = f"Regeneration failed: {e}. Original issue: {feedback}"
                q["confidence"] = 0.2

    # Mark any remaining unreviewed
    for q in questions:
        if "review_status" not in q:
            q["review_status"] = "unreviewed"
        if "confidence" not in q or q["confidence"] is None:
            q["confidence"] = 0.0

    return questions
