"""
RFI pipeline functions that process RFI questions.

match_and_fill       — sync retrieval + generation via Claude (one question at a time)
match_and_fill_async — async concurrent fill with rate-limit backoff (production path)
review_answers       — checks consistency across answers via Claude

Retrieval uses Azure AI Search (required).
"""

from __future__ import annotations

import asyncio
import json
import os

from anthropic import Anthropic, AsyncAnthropic
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


# ─── MATCHER + FILLER ───────────────────────────────────────────────────────

def _sanitize_odata_string(value: str) -> str:
    """Escape single quotes in OData filter values to prevent injection."""
    return value.replace("'", "''")


def _find_similar_qas_azure(
    question: str,
    category: str,
    client_name: str = "",
    max_results: int = 10,
) -> list[dict]:
    """
    Azure AI Search hybrid retrieval: keyword + vector + semantic ranker.
    Returns ranked results with real similarity scores.
    """
    aoai_client = _get_azure_openai_client()
    embedding = _embed(question, aoai_client)

    index_name = os.environ.get("AZURE_SEARCH_QUESTION_INDEX", "rfi-questions")
    search_client = _get_search_client(index_name)

    # Build filter — category is always applied (sanitize to prevent OData injection)
    search_filter = f"category eq '{_sanitize_odata_string(category)}'"

    # Hybrid query: keyword + vector + semantic ranker
    vector_query = VectorizedQuery(
        vector=embedding,
        k_nearest_neighbors=50,
        fields="embedding",
    )

    search_kwargs = {
        "search_text": question,
        "vector_queries": [vector_query],
        "filter": search_filter,
        "top": max_results,
    }

    # Enable semantic ranker if a config is set (optional — requires Standard tier)
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
            "match_type": "same_client" if client_name and result_client.lower() == client_name.lower() else "same_category",
            "score": r.get("@search.score", 0.0),
        })

    return output


def _find_similar_qas(
    question: str,
    category: str,
    client_name: str = "",
    max_results: int = 10,
) -> list[dict]:
    """
    Retrieve similar Q&A pairs via Azure AI Search hybrid retrieval.
    Return shape: [{"question", "answer", "client", "source", "match_type", "score"}, ...]
    """
    return _find_similar_qas_azure(question, category, client_name, max_results)


def _find_base_info(category: str, question: str) -> str:
    """
    Retrieve relevant base info chunks from Azure AI Search knowledge index.
    Returns formatted text to inject into the filler prompt.
    """

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
            filter=f"category eq '{_sanitize_odata_string(category)}'",
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
    client_name: str = "",
    client: Anthropic | None = None,
) -> list[dict]:
    """
    For each question, find similar past answers and generate a response.
    Adds 'generated_answer', 'confidence', and 'source_references' to each question.

    Retrieval is via Azure AI Search only (both Q&A and base info).
    """
    client = client or _get_client()

    for q in questions:
        category = q.get("category", q.get("category_hint", "Uncategorized"))

        # Gather context — past Q&A pairs via Azure AI Search
        similar_qas = _find_similar_qas(q["question_text"], category, client_name)

        # Get relevant base info via Azure AI Search
        base_info_text = _find_base_info(category, q["question_text"])

        prompt = _build_filler_prompt(q, similar_qas, base_info_text)

        try:
            response = client.messages.create(
                model="claude-sonnet-4-20250514",
                max_tokens=2048,
                temperature=0.1,
                messages=[{"role": "user", "content": prompt}],
            )

            # Truncation detection
            if response.stop_reason == "max_tokens":
                q["generated_answer"] = response.content[0].text.strip()
                q["confidence"] = 0.3
                q["citation"] = ""
                q["source_references"] = []
                q["fill_status"] = "truncated"
                continue

            text = response.content[0].text.strip()
            if text.startswith("```"):
                text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()

            result = json.loads(text)
            q["generated_answer"] = result.get("answer", "")
            q["confidence"] = min(max(float(result.get("confidence", 0)), 0), 1.0)
            q["citation"] = result.get("citation", "")
            q["source_references"] = result.get("sources", [])
            q["fill_status"] = "filled"

        except json.JSONDecodeError:
            q["generated_answer"] = response.content[0].text.strip() if response else ""
            q["confidence"] = 0.4
            q["citation"] = ""
            q["source_references"] = []
            q["fill_status"] = "parse_error"

        except Exception as e:
            error_str = str(e).lower()
            if "rate" in error_str and "limit" in error_str:
                q["generated_answer"] = "[RATE LIMITED] Could not generate answer."
                q["confidence"] = 0.0
                q["fill_status"] = "rate_limited"
            else:
                q["generated_answer"] = f"[ERROR] Could not generate answer: {e}"
                q["confidence"] = 0.0
                q["fill_status"] = "error"
            q["citation"] = ""
            q["source_references"] = []

        # Progress indicator
        print(f"  Filled {questions.index(q) + 1}/{len(questions)} questions", end="\r")

    print()
    return questions


# ─── ASYNC CONCURRENT FILLER ────────────────────────────────────────────────

def _build_filler_prompt(q: dict, similar_qas: list[dict], base_info_text: str) -> str:
    """Build the filler prompt for a single question. Shared by sync and async paths."""
    category = q.get("category", q.get("category_hint", "Uncategorized"))

    past_qa_text = ""
    if similar_qas:
        past_qa_text = "\n\nPast Q&A pairs from similar RFIs:\n"
        for i, qa in enumerate(similar_qas):
            past_qa_text += f"\n[Past Q{i+1} | Client: {qa['client']} | Source: {qa['source']}]\n"
            past_qa_text += f"Q: {qa['question']}\n"
            past_qa_text += f"A: {qa['answer']}\n"

    return f"""You are filling out an RFI (Request for Information) for Avalere Health, a healthcare consulting firm.

QUESTION (Category: {category}):
{q['question_text']}

{f"EXISTING ANSWER (from previous submission): {q.get('existing_answer', '')}" if q.get('existing_answer') else "No existing answer."}

{f"BASE COMPANY INFORMATION:{base_info_text}" if base_info_text else "No base info available for this category."}
{past_qa_text if past_qa_text else "No similar past Q&A pairs found."}

INSTRUCTIONS:
1. If a past Q&A question is nearly identical to this question, reuse that past answer verbatim.
2. If similar (but not identical) past Q&A pairs are available, adapt the most relevant answer to fit this specific question.
3. If an existing answer is provided and is consistent with the base info and past Q&As, you may reuse it.
4. If base company information is available, use it to construct an accurate answer.
5. DO NOT fabricate, or hallucinate any facts, statistics, certifications, policy details, or any specifics not present in the provided context. If the context does not contain enough information to answer confidently, respond with "[NEEDS REVIEW]" followed by your best attempt using only what is provided.
6. Keep the tone professional and consistent with a healthcare consulting firm.
7. Be concise but thorough. Match the expected level of detail.
8. Add a citation for all the sources you used from the provided context 

Respond with ONLY a JSON object:
{{
  "answer": "your answer text",
  "confidence": 0.0-1.0,
  "citation":"cite sources used in this answer for ex: filename, section heading/sheet name, cell name etc",
  "sources": ["list of sources used"]
}}
"""


async def _fill_one_question_async(
    q: dict,
    client_name: str,
    async_client: AsyncAnthropic,
    semaphore: asyncio.Semaphore,
    on_progress=None,
) -> None:
    """Fill a single question with rate-limit retry and backoff."""
    category = q.get("category", q.get("category_hint", "Uncategorized"))
    q["fill_status"] = "filling"
    if on_progress:
        await on_progress(q)

    # Retrieval via Azure AI Search
    similar_qas = _find_similar_qas(q["question_text"], category, client_name)

    # Base info via Azure AI Search
    base_info_text = _find_base_info(category, q["question_text"])

    prompt = _build_filler_prompt(q, similar_qas, base_info_text)

    # Retry with exponential backoff (max 3 retries, base 2s)
    max_retries = 3
    base_delay = 2.0

    async with semaphore:
        for attempt in range(max_retries + 1):
            try:
                response = await async_client.messages.create(
                    model="claude-sonnet-4-20250514",
                    max_tokens=2048,
                    temperature=0.1,
                    messages=[{"role": "user", "content": prompt}],
                )

                # Truncation detection
                if response.stop_reason == "max_tokens":
                    q["generated_answer"] = response.content[0].text.strip()
                    q["confidence"] = 0.3
                    q["citation"] = ""
                    q["source_references"] = []
                    q["fill_status"] = "truncated"
                    break

                text = response.content[0].text.strip()
                if text.startswith("```"):
                    text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()

                result = json.loads(text)
                q["generated_answer"] = result.get("answer", "")
                q["confidence"] = min(max(float(result.get("confidence", 0)), 0), 1.0)
                q["citation"] = result.get("citation", "")
                q["source_references"] = result.get("sources", [])
                q["fill_status"] = "filled"
                break

            except json.JSONDecodeError:
                q["generated_answer"] = response.content[0].text.strip() if response else ""
                q["confidence"] = 0.4
                q["citation"] = ""
                q["source_references"] = []
                q["fill_status"] = "parse_error"
                break  # Don't retry parse errors

            except Exception as e:
                error_str = str(e).lower()
                is_rate_limit = "rate" in error_str and "limit" in error_str
                is_overloaded = "overloaded" in error_str or "529" in error_str

                if (is_rate_limit or is_overloaded) and attempt < max_retries:
                    delay = base_delay * (2 ** attempt)
                    await asyncio.sleep(delay)
                    continue

                if is_rate_limit:
                    q["generated_answer"] = "[RATE LIMITED] Could not generate answer."
                    q["confidence"] = 0.0
                    q["fill_status"] = "rate_limited"
                else:
                    q["generated_answer"] = f"[ERROR] Could not generate answer: {e}"
                    q["confidence"] = 0.0
                    q["fill_status"] = "error"
                q["citation"] = ""
                q["source_references"] = []
                break

    if on_progress:
        await on_progress(q)


async def match_and_fill_async(
    questions: list[dict],
    client_name: str = "",
    max_concurrent: int = 5,
    on_progress=None,
) -> list[dict]:
    """
    Async concurrent fill — production path.

    Processes all questions in parallel (up to max_concurrent) with:
    - asyncio.Semaphore for concurrency control
    - Exponential backoff on rate limits (base 2s, max 3 retries)
    - fill_status tracking per question
    - Truncation detection (stop_reason == max_tokens → confidence 0.3)

    Args:
        questions: parsed question dicts (must have question_text, category_hint at minimum)
        client_name: for same-client boosting
        max_concurrent: max parallel Claude calls (default 5)
        on_progress: async callback(q) called when each question status changes
    """
    async_client = AsyncAnthropic()
    semaphore = asyncio.Semaphore(max_concurrent)

    # Mark all as pending
    for q in questions:
        q["fill_status"] = "pending"

    tasks = [
        _fill_one_question_async(
            q, client_name, async_client, semaphore, on_progress,
        )
        for q in questions
    ]

    await asyncio.gather(*tasks)
    return questions


# ─── AGENT 3: REVIEWER ──────────────────────────────────────────────────────

def review_answers(
    questions: list[dict],
    client: Anthropic | None = None,
) -> list[dict]:
    """
    Review generated answers for consistency and quality.
    Adjusts confidence scores and flags issues.

    Every answer gets a 'review_status' field:
      - "reviewed"   — reviewer checked it, no issues
      - "flagged"    — reviewer found a concern
      - "unreviewed" — review failed or was skipped
    """
    client = client or _get_client()

    # Group answers by sheet_name (PRD: sheets are the natural grouping from source Excel)
    by_sheet = {}
    for q in questions:
        sheet = q.get("sheet_name", "Unknown")
        if sheet not in by_sheet:
            by_sheet[sheet] = []
        by_sheet[sheet].append(q)

    # Check for contradictions within each sheet
    for sheet, sheet_qs in by_sheet.items():
        answered = [q for q in sheet_qs if q.get("generated_answer") and q["confidence"] > 0]

        # Categories with 0-1 answers: mark as reviewed (nothing to contradict)
        if len(answered) < 2:
            for q in answered:
                q["review_status"] = "reviewed"
            continue

        # Build a summary for Claude to review
        qa_summary = "\n".join(
            f"Q{i+1}: {q['question_text']}\nA{i+1}: {q['generated_answer']}\n"
            for i, q in enumerate(answered[:15])  # Cap at 15 to stay in context
        )

        try:
            response = client.messages.create(
                model="claude-sonnet-4-20250514",
                max_tokens=2048,
                temperature=0,
                messages=[{
                    "role": "user",
                    "content": f"""Review these RFI answers for Avalere Health from the "{sheet}" sheet.
Check for:
1. Contradictions between answers (e.g., different employee counts, inconsistent company descriptions)
2. Factual errors or implausible claims
3. Answers that are too vague to be useful

Answers:
{qa_summary}

Respond with ONLY a JSON object:
{{
  "contradictions": [
    {{"questions": [1, 3], "issue": "description of contradiction"}}
  ],
  "flags": [
    {{"question": 2, "issue": "description of concern", "confidence_adjustment": -0.2}}
  ]
}}

If no issues found, respond with {{"contradictions": [], "flags": []}}
"""
                }],
            )

            text = response.content[0].text.strip()
            if text.startswith("```"):
                text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()

            review = json.loads(text)

            # Start by marking all as reviewed (clean)
            for q in answered:
                q["review_status"] = "reviewed"

            # Apply confidence adjustments for flagged answers
            for flag in review.get("flags", []):
                q_idx = flag.get("question", 0) - 1
                if 0 <= q_idx < len(answered):
                    adj = float(flag.get("confidence_adjustment", 0))
                    answered[q_idx]["confidence"] = max(0, answered[q_idx]["confidence"] + adj)
                    answered[q_idx]["review_flag"] = flag.get("issue", "")
                    answered[q_idx]["review_status"] = "flagged"

            for contradiction in review.get("contradictions", []):
                for q_idx in contradiction.get("questions", []):
                    idx = q_idx - 1
                    if 0 <= idx < len(answered):
                        answered[idx]["review_flag"] = contradiction.get("issue", "Contradiction detected")
                        answered[idx]["confidence"] = min(answered[idx]["confidence"], 0.5)
                        answered[idx]["review_status"] = "flagged"

        except (json.JSONDecodeError, IndexError, KeyError, ValueError) as e:
            # Parse errors: review failed but not fatal — flag all as unreviewed
            print(f"  WARN: Review parse error for sheet '{sheet}': {e}")
            for q in answered:
                q["review_status"] = "unreviewed"
                q["review_flag"] = f"Review could not parse response: {e}"
                # Penalize confidence — unreviewed answers are less trustworthy
                q["confidence"] = min(q["confidence"], 0.6)

        except Exception as e:
            # API errors (auth, rate limit, network): let auth/rate-limit bubble up,
            # treat other API errors as non-fatal
            error_str = str(e).lower()
            if "auth" in error_str or "api key" in error_str:
                raise  # Auth failures must not be swallowed
            if "rate" in error_str and "limit" in error_str:
                raise  # Rate limits must not be swallowed

            # Other API errors: flag as unreviewed
            print(f"  WARN: Review failed for sheet '{sheet}': {e}")
            for q in answered:
                q["review_status"] = "unreviewed"
                q["review_flag"] = f"Review failed: {e}"
                q["confidence"] = min(q["confidence"], 0.6)

    # Mark any answers that never went through review (e.g., confidence was 0)
    for q in questions:
        if "review_status" not in q:
            q["review_status"] = "unreviewed"

    return questions
